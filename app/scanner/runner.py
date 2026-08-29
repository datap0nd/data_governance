"""
Scan runner — orchestrates a full scan.

1. Walk the reports folder (finds .pbix files or TMDL exports)
2. Parse all tables and extract sources
3. Deduplicate sources
4. Store everything in SQLite
5. Record the scan run
"""

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.archive_ops import archive_source
from app.config import (
    TMDL_ROOT,
    DB_PATH,
    PGHOST,
    PGDATABASE,
    UPLOAD_PGHOST,
    UPLOAD_PGPORT,
)
from app.database import get_db
from app.scanner.control import ScannerWorkCancelled, assert_not_cancelled, current_cancel_generation
from app.scanner.lifecycle import (
    component_has_warning,
    component_result,
    finish_scan_run,
    normalize_scan_status,
    terminal_status_for_components,
)
from app.scanner.tmdl_parser import (
    LOCAL_USER_PATH,
    is_folder_like_file_source,
    path_has_file_extension,
)
from app.scanner.walker import walk_reports_root
from app.scanner.source_matcher import deduplicate_sources
from app.scanner.findings import sync_managed_actions
from app.scanner import jobs as scanner_jobs
from app.asset_visibility import get_active_source_ids
from app.source_identity import (
    postgres_server_identity,
    exact_identity_rows,
    reconcile_all_flow_targets,
    split_relation,
    upsert_postgres_identity,
)

logger = logging.getLogger(__name__)

_FILE_SOURCE_DB_TYPES = {"csv", "excel", "folder", "file"}


def _source_resolution(source, *, source_id: int | None, is_metadata: bool, expression: str | None) -> tuple[str, str | None]:
    if is_metadata or not expression:
        return "not_applicable", None
    if source_id is not None:
        return "resolved", None
    if source is None:
        return "unresolved", "unrecognized_source_expression"
    if source.source_type == "calculated":
        return "not_external", None
    if source.source_type == "postgresql":
        if not source.postgres_single_connector:
            return "unresolved", "multiple_postgres_connectors"
        if not source.postgres_single_native_query:
            return "unresolved", "multiple_native_postgres_queries"
        if not source.postgres_native_query_exact:
            return "unresolved", "nonliteral_native_postgres_query"
        if not source.postgres_conditional_output_exact:
            return "unresolved", "conditional_postgres_output"
        if not source.sql_table:
            return "unresolved", "unresolved_postgres_relation"
        return "unresolved", "postgres_relation_not_verified"
    if source.source_type == "unknown":
        return "unresolved", "unsupported_or_multiple_sources"
    return "unresolved", "source_not_linked"


def _postgres_work_is_required(db) -> bool:
    """Return the current, not start-snapshot, PostgreSQL obligation."""
    active_source_ids = get_active_source_ids(db)
    postgres_source_ids = {
        int(row["id"])
        for row in db.execute(
            """SELECT id FROM sources
               WHERE LOWER(COALESCE(type, '')) = 'postgresql'
                 AND COALESCE(archived, 0) = 0"""
        ).fetchall()
    }
    active_postgres_sources = bool(active_source_ids & postgres_source_ids)
    sql_flow_targets = bool(
        db.execute(
            """SELECT EXISTS(
                   SELECT 1 FROM flows
                   WHERE COALESCE(sql_handoff_enabled, 0) = 1
               ) AS required"""
        ).fetchone()["required"]
    )
    return bool(active_postgres_sources or sql_flow_targets)


def _backup_db() -> None:
    """Backup governance.db before scanning."""
    db_file = Path(DB_PATH)
    if not db_file.exists():
        return

    backup_dir = db_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"governance_{timestamp}.db"

    try:
        shutil.copy2(str(db_file), str(backup_path))
        logger.info("Database backed up to %s", backup_path)
    except Exception as e:
        logger.warning("Failed to backup database: %s", e)


def _source_row_is_folder_like(row) -> bool:
    """Return True for scan-discovered file sources that are actually folders."""
    source_query = row["source_query"] or ""
    if re.search(r'Folder\.Files\s*\(', source_query):
        return True
    source_type = (row["type"] or "").lower()
    if source_type not in _FILE_SOURCE_DB_TYPES:
        return False
    path = (row["connection_info"] or row["name"] or "").strip()
    return bool(path) and not path_has_file_extension(path)


def _archive_folder_like_scan_sources(db, now: str, log_lines: list[str]) -> None:
    """Hide old folder entries produced by Folder.Files scans."""
    rows = db.execute(
        """SELECT id, name, type, connection_info, source_query
           FROM sources
           WHERE archived = 0
             AND discovered_by = 'scan'
             AND type IN ('csv', 'excel', 'folder', 'file')"""
    ).fetchall()
    archived_count = 0
    for row in rows:
        if not _source_row_is_folder_like(row):
            continue
        db.execute("UPDATE report_tables SET source_id = NULL WHERE source_id = ?", (row["id"],))
        archive_source(
            db, row["id"], now,
            reason="Folder source archived",
            action_note=" [auto-resolved: folder source archived]",
        )
        archived_count += 1
        log_lines.append(f"ARCHIVED: {row['name']} (folder path, not a file source)")
    if archived_count:
        log_lines.append(f"TOTAL ARCHIVED: {archived_count} folder-like file sources")


def _retire_legacy_unknown_sources(db, now: str, log_lines: list[str]) -> int:
    """Unlink and archive the old shared ``Unknown Source`` placeholder."""
    rows = db.execute(
        """SELECT id, name FROM sources
             WHERE COALESCE(archived, 0)=0
               AND lower(trim(name))='unknown source'
             ORDER BY id"""
    ).fetchall()
    for row in rows:
        source_id = int(row["id"])
        db.execute("UPDATE report_tables SET source_id=NULL WHERE source_id=?", (source_id,))
        archive_source(
            db,
            source_id,
            now,
            reason="Legacy unresolved source placeholder retired",
        )
        replacement_name = f"Archived unresolved source {source_id}"
        suffix = 2
        candidate = replacement_name
        while db.execute(
            "SELECT 1 FROM sources WHERE name=? AND id!=?", (candidate, source_id)
        ).fetchone():
            candidate = f"{replacement_name} #{suffix}"
            suffix += 1
        db.execute("UPDATE sources SET name=? WHERE id=?", (candidate, source_id))
        log_lines.append(
            f"ARCHIVED: legacy unresolved placeholder source {source_id}; report tables are unlinked"
        )
    return len(rows)


def _archive_local_user_path_sources(db, all_sources, now: str, log_lines: list[str]) -> None:
    """Archive scan-discovered file sources on an analyst's local profile.

    The server's service account can never reach C:\\Users\\<analyst> paths,
    so probing them only produces 'unknown' noise. Archiving (rather than
    dropping) keeps the "report reads from a personal folder" signal visible
    under Show Archived and preserves report lineage.
    """
    # File-source rows are keyed by basename (sources.name is UNIQUE), so one
    # row can serve both a C:\Users\... and a \\share\... lineage, with
    # connection_info last-writer-wins. Only archive a basename when no
    # non-local path was observed for it anywhere in this scan.
    def _basename(path: str) -> str:
        return path.replace("\\", "/").rstrip("/").split("/")[-1]

    contested_names = set()
    for info in all_sources.values():
        path = (getattr(info, "file_path", None) or "").strip()
        if path and not LOCAL_USER_PATH.match(path):
            contested_names.add(_basename(path).casefold())

    # A targeted scan may not contain every report already in the registry.
    # Preserve a basename that an existing lineage expression still points at
    # through a non-local path, even if that report was outside this scan.
    contested_source_ids = set()
    file_call = re.compile(
        r'(?:File\.Contents|Folder\.Files)\s*\(\s*"((?:[^"]|"")*)"',
        re.IGNORECASE,
    )
    linked_rows = db.execute(
        """SELECT source_id, source_expression FROM report_tables
           WHERE source_id IS NOT NULL AND source_expression IS NOT NULL"""
    ).fetchall()
    for linked in linked_rows:
        for match in file_call.finditer(linked["source_expression"] or ""):
            path = match.group(1).replace('""', '"').strip()
            if path and not LOCAL_USER_PATH.match(path):
                contested_source_ids.add(linked["source_id"])
                break

    rows = db.execute(
        """SELECT id, name, connection_info FROM sources
           WHERE COALESCE(archived, 0) = 0
             AND discovered_by = 'scan'
             AND type IN ('csv', 'excel', 'folder', 'file')"""
    ).fetchall()
    archived_count = 0
    for row in rows:
        path = (row["connection_info"] or row["name"] or "").strip()
        if not LOCAL_USER_PATH.match(path):
            continue
        if row["name"].casefold() in contested_names or row["id"] in contested_source_ids:
            log_lines.append(
                f"SKIPPED (shared basename): {row['name']} also has a non-local path"
            )
            continue
        archive_source(
            db, row["id"], now,
            reason="Source archived (local user path, not probeable from server)",
        )
        archived_count += 1
        log_lines.append(
            f"ARCHIVED: {row['name']} (local user profile path, not probeable from server)"
        )
    if archived_count:
        log_lines.append(f"TOTAL ARCHIVED: {archived_count} local-user-path sources")


def run_scan(
    reports_path: str | None = None,
    *,
    cancel_generation: int | None = None,
    run_followup_probe: bool = True,
    operation_id: int | None = None,
) -> dict:
    """Run a full scan and store results.

    Returns a summary dict with scan statistics.
    """
    generation = current_cancel_generation() if cancel_generation is None else cancel_generation
    assert_not_cancelled(generation, "Report scan")

    if operation_id is None:
        operation_id = scanner_jobs.create_job(
            "full_scan",
            trigger_source="system",
            current_step="Preparing full scan",
            message="Creating a database backup before discovery.",
        )
    scanner_jobs.mark_running(
        operation_id,
        current_step="Preparing full scan",
        message="Creating a database backup before discovery.",
    )

    try:
        _backup_db()
    except Exception as exc:
        scanner_jobs.finish_job(
            operation_id,
            status="failed",
            result={"status": "failed", "error": str(exc)},
            message="The pre-scan database backup failed; review server logs.",
        )
        raise

    root = reports_path or TMDL_ROOT
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO scan_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        scan_id = cursor.lastrowid
    scanner_jobs.attach_scan_run(operation_id, int(scan_id))

    active_report_count = 0
    active_source_count = 0
    new_sources = 0
    broken_refs = 0
    log_text = "Scan did not complete core discovery."
    postgres_required = False
    components = {
        "core": {
            "status": "running",
            "requested": True,
            "required": True,
        }
    }

    def _mapping_result(value):
        if isinstance(value, dict):
            return value
        return {"status": "completed", "result": value}

    try:
        assert_not_cancelled(generation, "Report scan")
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Discovering Power BI reports",
            message=(
                "Reading the explicitly supplied local report path."
                if reports_path is not None
                else "Reading live semantic models through XMLA/TOM with Fabric fallback."
            ),
        )
        if reports_path is not None:
            reports = walk_reports_root(root)
            scan_origin = str(Path(root).resolve())
        else:
            from app.scanner.pbi_metadata import read_live_reports

            reports = read_live_reports()
            scan_origin = "powerbi://configured-workspace"
        assert_not_cancelled(generation, "Report scan")
        all_sources = deduplicate_sources(reports)
        assert_not_cancelled(generation, "Report scan")

        broken_by_report: dict[int, dict] = {}
        log_lines = []

        # Per-report parsing summary (visible in scan log)
        for report in reports:
            assert_not_cancelled(generation, "Report scan")
            tables_count = len(report.tables)
            measures_count = len(getattr(report, "measures", []))
            layout = getattr(report, "layout", None)
            if layout and hasattr(layout, "pages"):
                vis_count = sum(len(p.visuals) for p in layout.pages)
                field_count = sum(len(v.field_refs) for p in layout.pages for v in p.visuals)
                log_lines.append(
                    f"REPORT: {report.name} - {tables_count} tables, {measures_count} measures, "
                    f"{len(layout.pages)} pages, {vis_count} visuals, {field_count} field refs"
                )
            else:
                diag = getattr(report, "layout_diagnostic", None)
                diag_suffix = f" [{diag}]" if diag else ""
                log_lines.append(
                    f"REPORT: {report.name} - {tables_count} tables, {measures_count} measures, "
                    f"NO LAYOUT (visuals not detected){diag_suffix}"
                )

        scanner_jobs.heartbeat(
            operation_id,
            current_step="Reconciling report catalog",
            message=f"Writing {len(reports)} report(s) and {len(all_sources)} source identity record(s).",
            progress_current=0,
            progress_total=len(reports),
        )
        with get_db() as db:
            assert_not_cancelled(generation, "Report scan")
            # Normalize PostgreSQL source names BEFORE upsert so matches work.
            # Handles two legacy naming patterns:
            #   1. "SERVER_NAME/database/schema.table" -> "schema.table"
            #   2. "database.schema.table" -> "schema.table"
            # Canonical form is just schema.table.
            def _merge_source(db, old_id, new_id, old_name, new_name, log_lines):
                """Merge only sources with the same structured identity state.

                Legacy display-name cleanup must never move references between
                two different physical PostgreSQL relations, or from an
                unidentified legacy source onto an identified source.
                """
                identity_columns = (
                    "server_name, database_name, schema_name, relation_name"
                )
                old_identity = db.execute(
                    f"SELECT {identity_columns} FROM source_postgres_identities WHERE source_id=?",
                    (old_id,),
                ).fetchone()
                new_identity = db.execute(
                    f"SELECT {identity_columns} FROM source_postgres_identities WHERE source_id=?",
                    (new_id,),
                ).fetchone()
                if old_identity is not None or new_identity is not None:
                    log_lines.append(
                        f"SKIPPED MERGE: {old_name} -> {new_name} "
                        "(structured PostgreSQL identity is authoritative)"
                    )
                    return False
                db.execute("UPDATE report_tables SET source_id = ? WHERE source_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE script_tables SET source_id = ? WHERE source_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE source_dependencies SET source_id = ? WHERE source_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE source_dependencies SET depends_on_id = ? WHERE depends_on_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE checks SET source_id = ? WHERE source_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE alerts SET source_id = ? WHERE source_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE actions SET source_id = ? WHERE source_id = ?",
                           (new_id, old_id))
                db.execute("UPDATE flows SET sql_target_source_id = ? WHERE sql_target_source_id = ?",
                           (new_id, old_id))
                db.execute("DELETE FROM source_postgres_identities WHERE source_id = ?", (old_id,))
                db.execute("DELETE FROM source_probes WHERE source_id = ?", (old_id,))
                db.execute("DELETE FROM sources WHERE id = ?", (old_id,))
                log_lines.append(f"MERGED: {old_name} -> {new_name} (source {old_id} into {new_id})")
                return True

            # Pass 1: Strip "PGHOST/PGDATABASE/" prefix
            if PGHOST:
                prefix = f"{PGHOST}/"
                if PGDATABASE:
                    prefix += f"{PGDATABASE}/"
                old_rows = db.execute(
                    """SELECT s.id, s.name FROM sources s
                       WHERE s.name LIKE ? AND s.type = 'postgresql'
                         AND NOT EXISTS (
                             SELECT 1 FROM source_postgres_identities spi
                             WHERE spi.source_id=s.id
                         )""",
                    (f"{PGHOST}/%",),
                ).fetchall()
                for row in old_rows:
                    new_name = row["name"].replace(prefix, "", 1)
                    dup = db.execute("SELECT id FROM sources WHERE name = ? AND id != ?",
                                    (new_name, row["id"])).fetchone()
                    if dup:
                        _merge_source(
                            db, row["id"], dup["id"], row["name"], new_name, log_lines
                        )
                    else:
                        db.execute("UPDATE sources SET name = ? WHERE id = ?",
                                   (new_name, row["id"]))

            # Pass 2: Strip "PGDATABASE." prefix.
            if PGDATABASE:
                db_prefix = f"{PGDATABASE}."
                old_rows = db.execute(
                    """SELECT s.id, s.name FROM sources s
                       WHERE s.name LIKE ? AND s.type = 'postgresql'
                         AND NOT EXISTS (
                             SELECT 1 FROM source_postgres_identities spi
                             WHERE spi.source_id=s.id
                         )""",
                    (f"{PGDATABASE}.%",),
                ).fetchall()
                for row in old_rows:
                    new_name = row["name"].replace(db_prefix, "", 1)
                    if new_name == row["name"]:
                        continue
                    dup = db.execute("SELECT id FROM sources WHERE name = ? AND id != ?",
                                    (new_name, row["id"])).fetchone()
                    if dup:
                        _merge_source(
                            db, row["id"], dup["id"], row["name"], new_name, log_lines
                        )
                    else:
                        db.execute("UPDATE sources SET name = ? WHERE id = ?",
                                   (new_name, row["id"]))

            # Pass 3: Strip parenthetical artifacts from source names
            # e.g. "schema.table_name (view)" -> "schema.table_name"
            # Also strips internal parens like "foo(bar)baz" -> "foobaz"
            import re as _re
            paren_rows = db.execute(
                """SELECT s.id, s.name FROM sources s
                   WHERE (s.name LIKE '%(%' OR s.name LIKE '%)%')
                     AND NOT EXISTS (
                         SELECT 1 FROM source_postgres_identities spi
                         WHERE spi.source_id=s.id
                     )"""
            ).fetchall()
            for row in paren_rows:
                # Strip all parenthesised groups, including internal ones
                new_name = _re.sub(r'\s*\([^)]*\)\s*', '', row["name"]).strip()
                # Also collapse accidental double dots / trailing dots
                new_name = _re.sub(r'\.+', '.', new_name).strip('.')
                if new_name and new_name != row["name"]:
                    dup = db.execute("SELECT id FROM sources WHERE name = ? AND id != ?",
                                    (new_name, row["id"])).fetchone()
                    if dup:
                        _merge_source(
                            db, row["id"], dup["id"], row["name"], new_name, log_lines
                        )
                    else:
                        db.execute("UPDATE sources SET name = ? WHERE id = ?",
                                   (new_name, row["id"]))
                        log_lines.append(f"CLEANED: {row['name']} -> {new_name}")

            # Pass 4: Archive bogus postgresql entries whose names aren't clean
            # identifiers. These are typically parser artifacts where a
            # Name="..." step matched a column or filter instead of a table.
            # Archive rather than delete so nothing is lost.
            from app.scanner.tmdl_parser import _validate_table_name
            pg_rows = db.execute(
                """SELECT s.id, s.name FROM sources s
                   WHERE s.type = 'postgresql' AND s.archived = 0
                     AND NOT EXISTS (
                         SELECT 1 FROM source_postgres_identities spi
                         WHERE spi.source_id=s.id
                     )"""
            ).fetchall()
            archived_count = 0
            for row in pg_rows:
                # A clean PG source name is either "schema.table" or "table"
                if _validate_table_name(row["name"]) is None:
                    archive_source(
                        db, row["id"], now,
                        reason="Source archived (invalid name)",
                    )
                    archived_count += 1
                    log_lines.append(
                        f"ARCHIVED: {row['name']} (not a clean postgresql identifier)"
                    )
            if archived_count:
                log_lines.append(
                    f"TOTAL ARCHIVED: {archived_count} postgresql sources with invalid names"
                )

            _archive_folder_like_scan_sources(db, now, log_lines)

            # Upsert sources. PostgreSQL relations are resolved through their
            # structured identity first so two databases may legitimately have
            # the same display relation name.
            source_ids_by_key: dict[str, int] = {}
            for key, source_info in all_sources.items():
                pg_parts = (
                    split_relation(source_info.sql_table)
                    if source_info.source_type == "postgresql"
                    and source_info.postgres_identity_is_exact
                    else None
                )
                existing = None
                if pg_parts and source_info.database:
                    identity_matches = exact_identity_rows(
                        db,
                        server=source_info.server,
                        database=source_info.database,
                        schema=pg_parts[0],
                        relation=pg_parts[1],
                    )
                    if len(identity_matches) > 1:
                        match_ids = ", ".join(
                            str(int(row["source_id"])) for row in identity_matches
                        )
                        raise RuntimeError(
                            "Ambiguous PostgreSQL identity for "
                            f"{source_info.database}.{pg_parts[0]}.{pg_parts[1]}: "
                            f"sources {match_ids}"
                        )
                    if len(identity_matches) == 1:
                        existing = db.execute(
                            "SELECT id, source_query, owner FROM sources WHERE id=?",
                            (identity_matches[0]["source_id"],),
                        ).fetchone()
                if not existing and not (pg_parts and source_info.database):
                    candidate = db.execute(
                        "SELECT id, source_query, owner FROM sources WHERE name = ?",
                        (source_info.display_name,),
                    ).fetchone()
                    existing = candidate

                if existing:
                    source_id = existing["id"]
                    new_query = source_info.raw_expression or ""
                    # Keep the legacy representative expression for backwards
                    # compatibility, but never use it for change detection. One
                    # source may be shared by many reports with different M.
                    db.execute(
                        "UPDATE sources SET source_query = ?, connection_info = ?, updated_at = ? WHERE id = ?",
                        (new_query, source_info.connection_info, now, source_id),
                    )
                else:
                    source_name = source_info.display_name
                    name_taken = db.execute(
                        "SELECT 1 FROM sources WHERE name=?", (source_name,)
                    ).fetchone()
                    if name_taken and pg_parts:
                        base_name = (
                            f"{source_info.display_name} "
                            f"[{source_info.database}@{source_info.server}]"
                        )
                        source_name = base_name
                        suffix = 2
                        while db.execute(
                            "SELECT 1 FROM sources WHERE name=?", (source_name,)
                        ).fetchone():
                            source_name = f"{base_name} #{suffix}"
                            suffix += 1
                    cursor = db.execute(
                        """INSERT INTO sources (name, type, connection_info, source_query, discovered_by, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'scan', ?, ?)""",
                        (
                            source_name,
                            source_info.source_type,
                            source_info.connection_info,
                            source_info.raw_expression,
                            now,
                            now,
                        ),
                    )
                    source_id = int(cursor.lastrowid)
                    new_sources += 1
                    table_info = f" -> {source_info.sql_table}" if source_info.sql_table else ""
                    log_lines.append(f"NEW: {source_info.display_name} ({source_info.source_type}){table_info}")
                if pg_parts and source_info.database:
                    identity_claim = upsert_postgres_identity(
                        db,
                        source_id=int(source_id),
                        server=source_info.server,
                        database=source_info.database,
                        schema=pg_parts[0],
                        relation=pg_parts[1],
                        relation_kind="table",
                        verified_at=now,
                        preserve_existing_relation_kind=True,
                    )
                    if identity_claim["status"] == "conflict":
                        # A source ID is one immutable physical relation. If a
                        # legacy/display-name match points elsewhere, create a
                        # database-qualified source instead of overwriting its
                        # identity and silently moving existing lineage.
                        base_name = (
                            f"{source_info.display_name} "
                            f"[{source_info.database}@{source_info.server}]"
                        )
                        source_name = base_name
                        suffix = 2
                        while db.execute(
                            "SELECT 1 FROM sources WHERE name=?", (source_name,)
                        ).fetchone():
                            source_name = f"{base_name} #{suffix}"
                            suffix += 1
                        cursor = db.execute(
                            """INSERT INTO sources
                                   (name, type, connection_info, source_query,
                                    discovered_by, created_at, updated_at)
                               VALUES (?, ?, ?, ?, 'scan', ?, ?)""",
                            (
                                source_name,
                                source_info.source_type,
                                source_info.connection_info,
                                source_info.raw_expression,
                                now,
                                now,
                            ),
                        )
                        source_id = int(cursor.lastrowid)
                        replacement_claim = upsert_postgres_identity(
                            db,
                            source_id=source_id,
                            server=source_info.server,
                            database=source_info.database,
                            schema=pg_parts[0],
                            relation=pg_parts[1],
                            relation_kind="table",
                            verified_at=now,
                            preserve_existing_relation_kind=True,
                        )
                        if replacement_claim["status"] == "conflict":
                            raise RuntimeError(
                                f"Could not claim PostgreSQL identity for {source_name}."
                            )
                        new_sources += 1
                        log_lines.append(
                            f"NEW: {source_name} (postgresql) -> {source_info.sql_table}"
                        )
                source_ids_by_key[key] = int(source_id)

            # Exact links are safe to backfill only after every source identity
            # from this scan has landed. Ambiguous targets remain null.
            reconcile_all_flow_targets(
                db,
                server=postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT),
            )

            # After the upsert so sources first seen this scan are archived
            # before the follow-up probe runs.
            _archive_local_user_path_sources(db, all_sources, now, log_lines)

            # Upsert reports and their tables
            for report in reports:
                assert_not_cancelled(generation, "Report scan")
                existing_report = db.execute(
                    "SELECT id FROM reports WHERE name = ?",
                    (report.name,),
                ).fetchone()

                if existing_report:
                    report_id = existing_report["id"]
                    # Only update owner/business_owner if not already set in DB
                    db.execute(
                        """UPDATE reports SET tmdl_path = ?,
                           owner = COALESCE(NULLIF(owner, ''), ?),
                           business_owner = COALESCE(NULLIF(business_owner, ''), ?),
                           powerbi_url = COALESCE(?, powerbi_url),
                           pbi_workspace_id = COALESCE(?, pbi_workspace_id),
                           pbi_report_id = COALESCE(?, pbi_report_id),
                           pbi_dataset_id = COALESCE(?, pbi_dataset_id),
                           metadata_provider = COALESCE(?, metadata_provider),
                           updated_at = ? WHERE id = ?""",
                        (
                            report.tmdl_path,
                            report.report_owner,
                            report.business_owner,
                            getattr(report, "powerbi_url", None),
                            getattr(report, "workspace_id", None),
                            getattr(report, "pbi_report_id", None),
                            getattr(report, "dataset_id", None),
                            getattr(report, "metadata_provider", None),
                            now,
                            report_id,
                        ),
                    )
                else:
                    cursor = db.execute(
                        """INSERT INTO reports
                               (name, tmdl_path, owner, business_owner, powerbi_url,
                                pbi_workspace_id, pbi_report_id, pbi_dataset_id,
                                metadata_provider, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            report.name,
                            report.tmdl_path,
                            report.report_owner,
                            report.business_owner,
                            getattr(report, "powerbi_url", None),
                            getattr(report, "workspace_id", None),
                            getattr(report, "pbi_report_id", None),
                            getattr(report, "dataset_id", None),
                            getattr(report, "metadata_provider", None),
                            now,
                            now,
                        ),
                    )
                    report_id = cursor.lastrowid

                # Upsert report tables
                from app.scanner.tmdl_parser import is_auto_table
                seen_table_names: list[str] = []
                for table in report.tables:
                    assert_not_cancelled(generation, "Report scan")
                    # Skip Power BI auto-generated internal tables
                    if is_auto_table(table.table_name):
                        continue
                    if not getattr(table, "is_metadata", False):
                        seen_table_names.append(table.table_name)
                    source_id = None
                    source_candidate_id = None
                    source = getattr(table, "source", None)
                    m_expression = getattr(table, "m_expression", None)
                    is_metadata = getattr(table, "is_metadata", False)
                    existing_table = db.execute(
                        """SELECT source_id, source_candidate_id FROM report_tables
                             WHERE report_id=? AND table_name=?""",
                        (report_id, table.table_name),
                    ).fetchone()
                    if source and not is_metadata and is_folder_like_file_source(source):
                        log_lines.append(
                            f"SKIPPED: {report.name}/{table.table_name} folder source "
                            f"{source.file_path or source.display_name}"
                        )
                    elif source and not is_metadata:
                        # Find matching source in DB
                        mapped_source_id = source_ids_by_key.get(source.connection_key)
                        if mapped_source_id:
                            if source.source_type == "postgresql":
                                # PostgreSQL catalog verification owns relinks.
                                # Keep the prior anchor (or NULL for a new table)
                                # until the complete endpoint snapshot commits.
                                prior_source_id = (
                                    int(existing_table["source_id"])
                                    if existing_table is not None
                                    and existing_table["source_id"] is not None
                                    else None
                                )
                                source_id = prior_source_id
                                if source.postgres_identity_is_exact:
                                    if prior_source_id == mapped_source_id:
                                        source_id = mapped_source_id
                                    else:
                                        source_candidate_id = mapped_source_id
                            else:
                                source_id = mapped_source_id
                        elif source.source_type != "unknown":
                            broken_refs += 1
                            bucket = broken_by_report.setdefault(
                                report_id,
                                {"report_id": report_id, "owner": report.report_owner, "items": []},
                            )
                            bucket["items"].append(
                                f"{table.table_name} -> {source.display_name}"
                            )
                            log_lines.append(
                                f"BROKEN: {report.name}/{table.table_name} "
                                f"references unknown source: {source.display_name}"
                            )

                    resolution_status, resolution_reason = _source_resolution(
                        source,
                        source_id=source_id,
                        is_metadata=is_metadata,
                        expression=m_expression,
                    )
                    if (
                        source
                        and source.source_type == "postgresql"
                        and source.postgres_identity_is_exact
                        and source_candidate_id is not None
                    ):
                        resolution_status = "pending_verification"
                        resolution_reason = "awaiting_postgres_catalog"
                    if not is_metadata:
                        db.execute(
                            """INSERT INTO report_tables
                                   (report_id, table_name, source_id, source_candidate_id,
                                    source_expression,
                                    source_resolution_status, source_resolution_reason,
                                    last_scanned)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                               ON CONFLICT(report_id, table_name)
                               DO UPDATE SET source_id = ?, source_candidate_id = ?,
                                             source_expression = ?,
                                             source_resolution_status = ?,
                                             source_resolution_reason = ?, last_scanned = ?""",
                            (
                                report_id,
                                table.table_name,
                                source_id,
                                source_candidate_id,
                                m_expression,
                                resolution_status,
                                resolution_reason,
                                now,
                                source_id,
                                source_candidate_id,
                                m_expression,
                                resolution_status,
                                resolution_reason,
                                now,
                            ),
                        )

                if seen_table_names:
                    placeholders = ",".join("?" for _ in seen_table_names)
                    db.execute(
                        f"""DELETE FROM report_tables
                              WHERE report_id=? AND table_name NOT IN ({placeholders})""",
                        (report_id, *seen_table_names),
                    )
                else:
                    db.execute("DELETE FROM report_tables WHERE report_id=?", (report_id,))

                # Store visual layout (PBIX mode only)
                layout = getattr(report, "layout", None)
                if layout and hasattr(layout, "pages"):
                    # Clean stale layout data for this report
                    db.execute("""
                        DELETE FROM visual_fields WHERE visual_id IN (
                            SELECT rv.id FROM report_visuals rv
                            JOIN report_pages rp ON rp.id = rv.page_id
                            WHERE rp.report_id = ?)""", (report_id,))
                    db.execute("""
                        DELETE FROM report_visuals WHERE page_id IN (
                            SELECT id FROM report_pages WHERE report_id = ?)""", (report_id,))
                    db.execute("DELETE FROM report_pages WHERE report_id = ?", (report_id,))

                    seen_pages = {}
                    for page in layout.pages:
                        # Deduplicate page names (Power BI allows duplicate page names)
                        pname = page.page_name
                        if pname in seen_pages:
                            seen_pages[pname] += 1
                            pname = f"{pname} ({seen_pages[pname]})"
                        else:
                            seen_pages[pname] = 1

                        db.execute(
                            """INSERT INTO report_pages (report_id, page_name, page_ordinal, last_scanned)
                               VALUES (?, ?, ?, ?)
                               ON CONFLICT(report_id, page_name)
                               DO UPDATE SET page_ordinal = ?, last_scanned = ?""",
                            (report_id, pname, page.page_ordinal, now,
                             page.page_ordinal, now),
                        )
                        page_row = db.execute(
                            "SELECT id FROM report_pages WHERE report_id = ? AND page_name = ?",
                            (report_id, pname),
                        ).fetchone()
                        page_id = page_row["id"]

                        for visual in page.visuals:
                            db.execute(
                                """INSERT INTO report_visuals (page_id, visual_id, visual_type, title, last_scanned)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (page_id, visual.visual_id, visual.visual_type, visual.title, now),
                            )
                            vis_row = db.execute(
                                "SELECT id FROM report_visuals WHERE page_id = ? AND visual_id = ?",
                                (page_id, visual.visual_id),
                            ).fetchone()
                            vis_id = vis_row["id"]

                            for ref in visual.field_refs:
                                db.execute(
                                    """INSERT INTO visual_fields (visual_id, table_name, field_name)
                                       VALUES (?, ?, ?)
                                       ON CONFLICT(visual_id, table_name, field_name) DO NOTHING""",
                                    (vis_id, ref.table_name, ref.field_name),
                                )

                # Store measures
                measures = getattr(report, "measures", [])
                db.execute("DELETE FROM report_measures WHERE report_id = ?", (report_id,))
                if measures:
                    for m in measures:
                        db.execute(
                            """INSERT INTO report_measures (report_id, table_name, measure_name, measure_dax)
                               VALUES (?, ?, ?, ?)
                               ON CONFLICT(report_id, table_name, measure_name) DO UPDATE SET measure_dax = ?""",
                            (report_id, m.table_name, m.measure_name, m.dax_expression, m.dax_expression),
                        )

                # Store columns
                db.execute("DELETE FROM report_columns WHERE report_id = ?", (report_id,))
                for table in report.tables:
                    if getattr(table, "is_metadata", False) or is_auto_table(table.table_name):
                        continue
                    for col in getattr(table, "columns", []):
                        db.execute(
                            """INSERT INTO report_columns (report_id, table_name, column_name)
                               VALUES (?, ?, ?)
                               ON CONFLICT(report_id, table_name, column_name) DO NOTHING""",
                            (report_id, table.table_name, col),
                        )

            broken_findings = []
            for report_id, bucket in broken_by_report.items():
                report_owner = bucket["owner"]
                if not report_owner:
                    owner_row = db.execute("SELECT owner FROM reports WHERE id = ?", (report_id,)).fetchone()
                    report_owner = owner_row["owner"] if owner_row else None
                broken_findings.append({
                    "fingerprint": f"broken_ref:{report_id}",
                    "report_id": report_id,
                    "assigned_to": report_owner,
                    "notes": f"{len(bucket['items'])} broken report reference(s): " + "; ".join(bucket["items"][:20]),
                })
            broken_lifecycle = sync_managed_actions(db, "broken_ref", broken_findings, now)
            if any(broken_lifecycle[key] for key in ("created", "resolved")):
                log_lines.append(
                    f"Broken-reference actions: {broken_lifecycle['created']} created, "
                    f"{broken_lifecycle['resolved']} resolved"
                )

            _retire_legacy_unknown_sources(db, now, log_lines)

            # Set initial "unknown" status for any source without a probe
            assert_not_cancelled(generation, "Report scan")
            sourceless = db.execute("""
                SELECT s.id FROM sources s
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_probes sp WHERE sp.source_id = s.id
                )
            """).fetchall()
            for row in sourceless:
                db.execute(
                    "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', 'Initial scan — no probe data yet')",
                    (row["id"], now),
                )

            # Capture core counters, but leave the scan row running until every
            # requested component has completed.
            assert_not_cancelled(generation, "Report scan")
            active_report_count = db.execute(
                "SELECT COUNT(*) AS count FROM reports WHERE COALESCE(archived, 0) = 0"
            ).fetchone()["count"]
            active_source_ids = get_active_source_ids(db)
            active_source_count = len(active_source_ids)
            postgres_required = _postgres_work_is_required(db)
            log_text = "\n".join(log_lines) if log_lines else "No changes detected."

        components["core"] = component_result(
            {
                "status": "completed",
                "reports_scanned": active_report_count,
                "sources_found": active_source_count,
                "new_sources": new_sources,
                "broken_refs": broken_refs,
            },
            required=True,
        )
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Refreshing PostgreSQL lineage",
            message="Reading materialized-view dependencies for required databases.",
            progress_current=None,
            progress_total=None,
        )

        # Scan PostgreSQL MV dependencies
        assert_not_cancelled(generation, "Report scan")
        try:
            from app.scanner.pg_deps import scan_pg_dependencies

            dep_result = scan_pg_dependencies(
                scan_run_id=scan_id,
                operation_id=operation_id,
                cancel_generation=generation,
            )
            dep_result = _mapping_result(dep_result)
            logger.info("PG dependency scan completed: %s", dep_result.get("status"))
        except Exception as e:
            dep_result = {"status": "failed", "error": str(e)}
            logger.exception("PG dependency scan failed: %s", e)
        dep_status = normalize_scan_status(dep_result.get("status"))
        # Deferred report relinks can make the core-stage snapshot stale, while
        # an unidentified active PostgreSQL source can still require attention
        # even when pg_deps has no catalog target. Recompute actual final work
        # instead of trusting either snapshot or status in isolation.
        with get_db() as db:
            postgres_required = _postgres_work_is_required(db)
        dep_requested = not (
            not postgres_required and dep_status in {"skipped", "not_requested"}
        )
        dep_component = component_result(
            dep_result,
            requested=dep_requested,
            required=postgres_required,
        )
        components["postgres_dependencies"] = dep_component

        # Scan pg_cron for MV refresh schedules
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Reading PostgreSQL schedules",
            message="Checking pg_cron materialized-view refresh schedules.",
        )
        assert_not_cancelled(generation, "Report scan")
        try:
            from app.scanner.pg_cron import scan_pg_cron

            cron_result = scan_pg_cron()
            cron_result = _mapping_result(cron_result)
            logger.info("pg_cron scan completed: %s", cron_result.get("status"))
        except Exception as e:
            cron_result = {"status": "failed", "error": str(e)}
            logger.exception("pg_cron scan failed: %s", e)
        cron_status = normalize_scan_status(cron_result.get("status"))
        cron_requested = not (
            not postgres_required and cron_status in {"skipped", "not_requested"}
        )
        cron_component = component_result(
            cron_result,
            requested=cron_requested,
            required=postgres_required,
        )
        components["postgres_schedules"] = cron_component

        # Import configured usage CSVs as part of the scan instead of waiting
        # for a user to open a report or action page.
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Syncing usage metadata",
            message="Reading configured Power BI usage exports.",
        )
        try:
            from app.usage import sync_usage_from_csv_if_configured

            with get_db() as db:
                usage_result = sync_usage_from_csv_if_configured(db)
            usage_result = _mapping_result(usage_result)
            logger.info("Usage sync completed: %s", usage_result.get("status"))
        except Exception as e:
            usage_result = {"status": "failed", "error": str(e)}
            logger.exception("Usage sync failed: %s", e)
        usage_component = component_result(usage_result)
        components["usage"] = usage_component

        # Probe after dependency and cron discovery so all freshness and
        # data-quality decisions use the current graph.
        probe_result = None
        if run_followup_probe:
            scanner_jobs.heartbeat(
                operation_id,
                current_step="Probing source freshness",
                message="Checking file and PostgreSQL source freshness.",
            )
            try:
                from app.scanner.prober import run_probe

                probe_result = run_probe(
                    cancel_generation=generation,
                    operation_id=operation_id,
                )
                probe_result = _mapping_result(probe_result)
                logger.info("Freshness and data-quality checks completed after scan")
            except ScannerWorkCancelled:
                raise
            except Exception as e:
                probe_result = {"status": "failed", "error": str(e)}
                logger.exception("Probe failed after scan: %s", e)
            probe_component = component_result(probe_result)
        else:
            probe_component = component_result(requested=False)
        components["probe"] = probe_component

        # Persist governance findings as owned actions with automatic closure
        # when the next scan proves the condition has cleared.
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Evaluating governance checks",
            message="Checking best practices, schedules, and documentation coverage.",
        )
        governance_results = {}
        try:
            from app.routers.best_practices import run_best_practice_scan

            governance_results["best_practices"] = run_best_practice_scan(persist=False)
        except Exception as e:
            governance_results["best_practices"] = {"status": "failed", "error": str(e)}
            logger.exception("Best-practice scan failed: %s", e)
        try:
            from app.routers.schedules import run_schedule_discrepancy_scan

            governance_results["schedule_discrepancies"] = run_schedule_discrepancy_scan(persist=True)
        except Exception as e:
            governance_results["schedule_discrepancies"] = {"status": "failed", "error": str(e)}
            logger.exception("Schedule discrepancy scan failed: %s", e)
        try:
            from app.routers.documentation import sync_documentation_completeness_actions

            governance_results["documentation"] = sync_documentation_completeness_actions()
        except Exception as e:
            governance_results["documentation"] = {"status": "failed", "error": str(e)}
            logger.exception("Documentation completeness scan failed: %s", e)

        governance_components = {}
        for name, result in governance_results.items():
            governance_components[name] = component_result(_mapping_result(result))
        governance_status = (
            "completed_with_warnings"
            if any(component_has_warning(item) for item in governance_components.values())
            else "completed"
        )
        governance_component = component_result(
            {
                "status": governance_status,
                **governance_components,
            }
        )
        components["governance"] = governance_component

        auxiliary_log = [
            f"PostgreSQL dependencies: {dep_component.get('status', 'unknown')}",
            f"PostgreSQL schedules: {cron_component.get('status', 'unknown')}",
            f"Configured usage import: {usage_component.get('status', 'unknown')}",
        ]
        auxiliary_log.append(f"Source probe: {probe_component.get('status', 'unknown')}")
        for name, result in governance_components.items():
            auxiliary_log.append(f"Governance {name}: {result.get('status', 'unknown')}")
        final_log = "\n".join([log_text, *auxiliary_log])
        overall_status = terminal_status_for_components(components)
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Finalizing full scan",
            message="Saving the component summary and final counters.",
        )
        with get_db() as db:
            stored_status = finish_scan_run(
                db,
                scan_id,
                status=overall_status,
                reports_scanned=active_report_count,
                sources_found=active_source_count,
                new_sources=new_sources,
                broken_refs=broken_refs,
                components=components,
                log=final_log,
            )

        summary = {
            "scan_id": scan_id,
            "reports_scanned": active_report_count,
            "sources_found": active_source_count,
            "new_sources": new_sources,
            "broken_refs": broken_refs,
            "components": components,
            "probe": probe_component,
            "postgres_dependencies": dep_component,
            "postgres_schedules": cron_component,
            "usage": usage_component,
            "governance": governance_components,
            "status": stored_status,
            "log": final_log,
            "scanned_path": scan_origin,
        }
        scanner_jobs.finish_job(
            operation_id,
            status=stored_status,
            result=summary,
            message=(
                f"Scanned {active_report_count} report(s) and found "
                f"{active_source_count} active source(s)."
            ),
        )
        logger.info("Scan completed: %s", summary)
        return summary

    except ScannerWorkCancelled as e:
        logger.info("Scan stopped: %s", e)
        if normalize_scan_status(components.get("core", {}).get("status")) == "running":
            components["core"] = component_result(
                {"status": "stopped", "message": str(e)}, required=True
            )
        else:
            components["cancellation"] = component_result(
                {"status": "stopped", "message": str(e)}, required=True
            )
        stopped_log = f"STOPPED: {e}"
        with get_db() as db:
            stored_status = finish_scan_run(
                db,
                scan_id,
                status="stopped",
                reports_scanned=active_report_count,
                sources_found=active_source_count,
                new_sources=new_sources,
                broken_refs=broken_refs,
                components=components,
                log=stopped_log,
            )
        scanner_jobs.finish_job(
            operation_id,
            status="stopped",
            result={"status": stored_status, "message": str(e), "components": components},
            message=str(e),
        )
        return {
            "scan_id": scan_id,
            "status": stored_status,
            "message": str(e),
            "components": components,
        }

    except Exception as e:
        logger.exception("Scan failed")
        core_status = normalize_scan_status(components.get("core", {}).get("status"))
        if core_status == "running":
            components["core"] = component_result(
                {"status": "failed", "error": str(e)}, required=True
            )
            terminal_status = "failed"
        else:
            components["runner"] = component_result(
                {"status": "failed", "error": str(e)}, required=True
            )
            terminal_status = "completed_with_warnings"
        failure_log = (
            "Core discovery failed; review server logs."
            if terminal_status == "failed"
            else "An auxiliary scan component failed; review server logs."
        )
        with get_db() as db:
            stored_status = finish_scan_run(
                db,
                scan_id,
                status=terminal_status,
                reports_scanned=active_report_count,
                sources_found=active_source_count,
                new_sources=new_sources,
                broken_refs=broken_refs,
                components=components,
                log=failure_log,
            )
        scanner_jobs.finish_job(
            operation_id,
            status=stored_status,
            result={
                "status": stored_status,
                "error": "Redacted; review server logs.",
                "components": components,
            },
            message=failure_log,
        )
        return {
            "scan_id": scan_id,
            "status": stored_status,
            "error": "Redacted; review server logs.",
            "components": components,
        }
