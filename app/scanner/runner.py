"""
Scan runner — orchestrates a full scan.

1. Walk the reports folder (finds .pbix files or TMDL exports)
2. Parse all tables and extract sources
3. Deduplicate sources
4. Store everything in SQLite
5. Record the scan run
"""

import hashlib
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
    rollup_requested_component_status,
    terminal_status_for_components,
)
from app.scanner.tmdl_parser import (
    LOCAL_USER_PATH,
    is_folder_like_file_source,
    path_has_file_extension,
)
from app.scanner.walker import walk_reports_root, diagnose_reports_root
from app.scanner.source_matcher import deduplicate_sources
from app.scanner.findings import sync_managed_actions
from app.scanner import jobs as scanner_jobs
from app.asset_visibility import get_active_source_ids
from app.query_history import (
    REPORT_M_KIND,
    link_versions_to_action,
    observe_query,
    report_artifact_key,
)
from app.source_identity import (
    postgres_server_identity,
    exact_identity_rows,
    reconcile_all_flow_targets,
    split_relation,
    upsert_postgres_identity,
)

logger = logging.getLogger(__name__)
_DEFAULT_WALK_REPORTS_ROOT = walk_reports_root


class ReportDiscoveryError(RuntimeError):
    """Raised before reconciliation when the configured report snapshot is invalid."""


def _validate_report_discovery(root: str | Path, reports: list) -> None:
    path = Path(root)
    if not path.exists():
        raise ReportDiscoveryError(f"Configured report root does not exist: {path}")
    if not path.is_dir():
        raise ReportDiscoveryError(f"Configured report root is not a directory: {path}")
    if reports:
        return
    diagnostic = diagnose_reports_root(path)
    errors = diagnostic.get("errors") or []
    if errors:
        reason = "; ".join(str(item) for item in errors[:3])
    else:
        pbix_count = len(diagnostic.get("pbix_files") or [])
        tmdl_count = sum(
            1 for item in diagnostic.get("tmdl_folders") or []
            if not item.get("skip_reason")
        )
        reason = (
            "no valid report definitions were parsed "
            f"({pbix_count} PBIX candidate(s), {tmdl_count} valid TMDL folder(s))"
        )
    raise ReportDiscoveryError(f"Report discovery produced no usable reports: {reason}")

_FILE_SOURCE_DB_TYPES = {"csv", "excel", "folder", "file"}


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
    run_followups: bool = True,
    operation_id: int | None = None,
    initial_components: dict | None = None,
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
    from app.scanner import modules as scanner_modules

    report_module_run_id = scanner_modules.create_module_run(
        "report_catalog",
        scanner_job_id=operation_id,
        details={"phase": "backup"},
    )
    scanner_jobs.mark_running(
        operation_id,
        current_step="Preparing full scan",
        message="Creating a database backup before discovery.",
    )

    try:
        _backup_db()
    except Exception as exc:
        scanner_modules.finish_module_run(
            report_module_run_id,
            status="failed",
            summary="The pre-scan database backup failed.",
            details={"status": "failed", "error": str(exc)},
            log="Database backup failed before report discovery.",
        )
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
    scanner_modules.attach_scan_run(report_module_run_id, int(scan_id))

    active_report_count = 0
    reports_scanned = 0
    reports_discovered = 0
    reports_preserved_incomplete = 0
    ambiguous_provider_count = 0
    catalog_warning_count = 0
    active_source_count = 0
    new_sources = 0
    changed_queries = 0
    broken_refs = 0
    retired_report_tables = 0
    log_text = "Scan did not complete core discovery."
    postgres_required = False
    components = dict(initial_components or {})
    components["core"] = {
        "status": "running",
        "requested": True,
        "required": True,
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
            message="Reading PBIX/TMDL report definitions and source expressions.",
        )
        reports = walk_reports_root(root)
        # Filesystem validation belongs to the built-in discovery provider.
        # Tests and embedding callers may replace it with an in-memory source.
        if walk_reports_root is _DEFAULT_WALK_REPORTS_ROOT:
            _validate_report_discovery(root, reports)
        assert_not_cancelled(generation, "Report scan")
        reports_discovered = sum(
            int((getattr(report, "discovery", {}) or {}).get("candidate_count") or 1)
            for report in reports
        )
        reconcilable_reports = [
            report for report in reports
            if (getattr(report, "discovery", {}) or {}).get("snapshot_complete", True)
        ]
        reports_scanned = len(reconcilable_reports)
        reports_preserved_incomplete = len(reports) - reports_scanned
        ambiguous_provider_count = sum(
            1 for report in reports
            if (getattr(report, "discovery", {}) or {}).get("ambiguous_provider")
        )
        catalog_warning_count = sum(
            int(not (getattr(report, "discovery", {}) or {}).get("snapshot_complete", True))
            + len((getattr(report, "discovery", {}) or {}).get("provider_warnings") or [])
            + int(bool((getattr(report, "discovery", {}) or {}).get("table_sets_disagree")))
            for report in reports
        )
        all_sources = deduplicate_sources(reconcilable_reports)
        assert_not_cancelled(generation, "Report scan")

        broken_by_report: dict[int, dict] = {}
        log_lines = []

        provider_counts: dict[str, int] = {}
        for report in reports:
            discovery = getattr(report, "discovery", {}) or {}
            provider = str(discovery.get("model_provider") or "legacy")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if not discovery.get("snapshot_complete", True):
                issues = "; ".join(str(item) for item in (discovery.get("issues") or [])[:5])
                log_lines.append(
                    f"DISCOVERY WARNING: {report.name} snapshot was incomplete; "
                    f"kept the last trusted catalog links{f' ({issues})' if issues else ''}"
                )
                continue
            for warning in discovery.get("provider_warnings") or []:
                log_lines.append(
                    f"DISCOVERY WARNING: {report.name} {warning.get('provider', 'alternate')} "
                    "snapshot was incomplete; used the complete provider"
                )
            if discovery.get("table_sets_disagree"):
                log_lines.append(
                    f"DISCOVERY WARNING: {report.name} PBIX/TMDL table sets differ; "
                    f"used {provider.upper()} model snapshot"
                )
        if provider_counts:
            log_lines.append(
                "DISCOVERY: " + ", ".join(
                    f"{name}={count}" for name, count in sorted(provider_counts.items())
                )
            )

        # Per-report parsing summary (visible in scan log)
        for report in reconcilable_reports:
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
            message=f"Writing {reports_scanned} complete report snapshot(s) and {len(all_sources)} source identity record(s).",
            progress_current=0,
            progress_total=reports_scanned,
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
            for report in reconcilable_reports:
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
                           powerbi_url = COALESCE(NULLIF(powerbi_url, ''), ?),
                           updated_at = ? WHERE id = ?""",
                        (report.tmdl_path, report.report_owner, report.business_owner, None, now, report_id),
                    )
                else:
                    cursor = db.execute(
                        "INSERT INTO reports (name, tmdl_path, owner, business_owner, powerbi_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (report.name, report.tmdl_path, report.report_owner, report.business_owner, None, now, now),
                    )
                    report_id = cursor.lastrowid

                # Upsert report tables
                from app.scanner.tmdl_parser import is_auto_table
                report_query_changes: list[dict] = []
                for table in report.tables:
                    assert_not_cancelled(generation, "Report scan")
                    # Skip Power BI auto-generated internal tables
                    if is_auto_table(table.table_name):
                        continue
                    source_id = None
                    source = getattr(table, "source", None)
                    m_expression = getattr(table, "m_expression", None)
                    is_metadata = getattr(table, "is_metadata", False)
                    existing_table = db.execute(
                        """SELECT id, source_id, source_expression, last_scanned
                           FROM report_tables WHERE report_id = ? AND table_name = ?""",
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

                    if not is_metadata:
                        # Version M at report-table grain. A brand-new table is
                        # a baseline; an existing table moving to/from an empty
                        # expression is a real addition/removal.
                        if m_expression is not None or (
                            existing_table is not None and existing_table["source_expression"] is not None
                        ):
                            observation = observe_query(
                                db,
                                artifact_kind=REPORT_M_KIND,
                                artifact_key=report_artifact_key(report_id, table.table_name),
                                report_id=report_id,
                                source_id=source_id,
                                artifact_name=table.table_name,
                                language="m",
                                query_text=m_expression,
                                scan_run_id=scan_id,
                                detected_at=now,
                                has_saved_baseline=existing_table is not None,
                                saved_baseline_text=existing_table["source_expression"] if existing_table else None,
                                saved_baseline_source_id=existing_table["source_id"] if existing_table else None,
                                saved_baseline_at=existing_table["last_scanned"] if existing_table else None,
                            )
                            if observation.changed:
                                report_query_changes.append({
                                    "version_id": observation.version_id,
                                    "table_name": table.table_name,
                                    "query_hash": observation.query_hash,
                                    "change_kind": "updated",
                                })
                        db.execute(
                            """INSERT INTO report_tables (report_id, table_name, source_id, source_expression, last_scanned)
                               VALUES (?, ?, ?, ?, ?)
                               ON CONFLICT(report_id, table_name)
                               DO UPDATE SET source_id = ?, source_expression = ?, last_scanned = ?""",
                            (
                                report_id,
                                table.table_name,
                                source_id,
                                m_expression,
                                now,
                                source_id,
                                m_expression,
                                now,
                            ),
                        )

                # A complete provider snapshot is authoritative. Preserve the
                # catalog row and its history, but unlink tables no longer in
                # the model so pipelines cannot retain stale transitive edges.
                current_table_names = {
                    table.table_name for table in report.tables
                    if not is_auto_table(table.table_name)
                }
                missing_tables = db.execute(
                    """SELECT id, table_name, source_id, source_expression, last_scanned
                       FROM report_tables WHERE report_id = ?""",
                    (report_id,),
                ).fetchall()
                for missing in missing_tables:
                    if missing["table_name"] in current_table_names:
                        continue
                    was_active = (
                        missing["source_id"] is not None
                        or missing["source_expression"] is not None
                    )
                    if was_active:
                        observation = observe_query(
                            db,
                            artifact_kind=REPORT_M_KIND,
                            artifact_key=report_artifact_key(report_id, missing["table_name"]),
                            report_id=report_id,
                            source_id=None,
                            artifact_name=missing["table_name"],
                            language="m",
                            query_text=None,
                            scan_run_id=scan_id,
                            detected_at=now,
                            has_saved_baseline=True,
                            saved_baseline_text=missing["source_expression"],
                            saved_baseline_source_id=missing["source_id"],
                            saved_baseline_at=missing["last_scanned"],
                        )
                        if observation.changed:
                            report_query_changes.append({
                                "version_id": observation.version_id,
                                "table_name": missing["table_name"],
                                "query_hash": observation.query_hash,
                                "change_kind": "removed",
                            })
                        retired_report_tables += 1
                        log_lines.append(
                            f"RETIRED: {report.name}/{missing['table_name']} was removed from the authoritative model"
                        )
                    db.execute(
                        """UPDATE report_tables
                           SET source_id=NULL, source_expression=NULL, last_scanned=?
                           WHERE id=?""",
                        (now, missing["id"]),
                    )

                if report_query_changes:
                    changed_queries += len(report_query_changes)
                    signature = "|".join(
                        f"{item['table_name']}:{item['query_hash']}"
                        for item in sorted(report_query_changes, key=lambda item: item["table_name"].casefold())
                    )
                    fingerprint = (
                        f"changed_query:report:{report_id}:"
                        f"{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]}"
                    )
                    owner_row = db.execute("SELECT owner FROM reports WHERE id = ?", (report_id,)).fetchone()
                    owner = owner_row["owner"] if owner_row else report.report_owner
                    names = [item["table_name"] for item in report_query_changes]
                    removed_names = [
                        item["table_name"] for item in report_query_changes
                        if item.get("change_kind") == "removed"
                    ]
                    notes = (
                        f"{len(names)} model query/table change{'s' if len(names) != 1 else ''} "
                        f"detected in {report.name}: {', '.join(names)}."
                    )
                    if removed_names:
                        notes += (
                            " Removed from the authoritative model: "
                            + ", ".join(removed_names)
                            + "."
                        )
                    db.execute(
                        """UPDATE actions
                           SET status='resolved', resolved_at=?, updated_at=?,
                               notes=COALESCE(notes, '') || ' [auto-resolved: superseded query change]'
                           WHERE report_id=? AND type='changed_query'
                             AND fingerprint!=?
                             AND status IN ('open','acknowledged','investigating')""",
                        (now, now, report_id, fingerprint),
                    )
                    prior = db.execute(
                        """SELECT id FROM actions
                           WHERE fingerprint = ? AND status != 'resolved'
                           ORDER BY id DESC LIMIT 1""",
                        (fingerprint,),
                    ).fetchone()
                    if prior:
                        action_id = prior["id"]
                        db.execute(
                            "UPDATE actions SET notes=?, assigned_to=?, updated_at=? WHERE id=?",
                            (notes, owner, now, action_id),
                        )
                    else:
                        cursor = db.execute(
                            """INSERT INTO actions
                               (report_id, type, status, assigned_to, notes, fingerprint, created_at, updated_at)
                               VALUES (?, 'changed_query', 'open', ?, ?, ?, ?, ?)""",
                            (report_id, owner, notes, fingerprint, now, now),
                        )
                        action_id = int(cursor.lastrowid)
                    link_versions_to_action(
                        db,
                        [item["version_id"] for item in report_query_changes],
                        action_id,
                    )
                    log_lines.append(f"CHANGED: {report.name} model in {', '.join(names)}")

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
                if measures:
                    db.execute("DELETE FROM report_measures WHERE report_id = ?", (report_id,))
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

        core_status = (
            "completed_with_warnings"
            if catalog_warning_count
            else "completed"
        )
        components["core"] = component_result(
            {
                "status": core_status,
                "reports_scanned": reports_scanned,
                "reports_discovered": reports_discovered,
                "catalog_reports_active": active_report_count,
                "reports_preserved_incomplete": reports_preserved_incomplete,
                "ambiguous_provider_count": ambiguous_provider_count,
                "catalog_warning_count": catalog_warning_count,
                "sources_found": active_source_count,
                "new_sources": new_sources,
                "changed_queries": changed_queries,
                "broken_refs": broken_refs,
                "retired_report_tables": retired_report_tables,
            },
            required=True,
        )
        scanner_modules.finish_module_run(
            report_module_run_id,
            status=core_status,
            summary=(
                f"Reconciled {reports_scanned} of {reports_discovered} discovered report snapshot(s); "
                f"the catalog contains {active_report_count} active report(s)."
            ),
            details=components["core"],
            log=log_text,
        )
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Refreshing PostgreSQL lineage",
            message="Reading materialized-view dependencies for required databases.",
            progress_current=None,
            progress_total=None,
        )

        # Scan PostgreSQL MV dependencies
        dep_module_run_id = None
        assert_not_cancelled(generation, "Report scan")
        if run_followups:
            dep_module_run_id = scanner_modules.create_module_run(
                "postgres_lineage", scanner_job_id=operation_id, scan_run_id=scan_id
            )
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
        else:
            dep_result = {"status": "not_requested"}
        dep_status = normalize_scan_status(dep_result.get("status"))
        # Deferred report relinks can make the core-stage snapshot stale, while
        # an unidentified active PostgreSQL source can still require attention
        # even when pg_deps has no catalog target. Recompute actual final work
        # instead of trusting either snapshot or status in isolation.
        with get_db() as db:
            postgres_required = _postgres_work_is_required(db)
        if run_followups:
            dep_requested = not (
                not postgres_required and dep_status in {"skipped", "not_requested"}
            )
        else:
            postgres_required = False
            dep_requested = False
        dep_component = component_result(
            dep_result,
            requested=dep_requested,
            required=postgres_required,
        )
        components["postgres_dependencies"] = dep_component
        if dep_module_run_id is not None:
            scanner_modules.finish_module_run(
                dep_module_run_id,
                status=dep_component.get("status") or "completed",
                summary=(
                    f"Found {int(dep_component.get('mvs_found') or 0)} materialized view(s) "
                    f"and {int(dep_component.get('deps_created') or 0)} dependency edge(s)."
                ),
                details=dep_component,
                log=dep_component.get("log") or dep_component.get("query_change_log"),
            )

        # Scan pg_cron for MV refresh schedules
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Reading PostgreSQL schedules",
            message="Checking pg_cron materialized-view refresh schedules.",
        )
        cron_module_run_id = None
        assert_not_cancelled(generation, "Report scan")
        if run_followups:
            cron_module_run_id = scanner_modules.create_module_run(
                "postgres_schedules", scanner_job_id=operation_id, scan_run_id=scan_id
            )
            try:
                from app.scanner.pg_cron import scan_pg_cron

                cron_result = scan_pg_cron()
                cron_result = _mapping_result(cron_result)
                logger.info("pg_cron scan completed: %s", cron_result.get("status"))
            except Exception as e:
                cron_result = {"status": "failed", "error": str(e)}
                logger.exception("pg_cron scan failed: %s", e)
        else:
            cron_result = {"status": "not_requested"}
        cron_status = normalize_scan_status(cron_result.get("status"))
        cron_requested = run_followups and not (
            not postgres_required and cron_status in {"skipped", "not_requested"}
        )
        cron_component = component_result(
            cron_result,
            requested=cron_requested,
            required=postgres_required,
        )
        components["postgres_schedules"] = cron_component
        if cron_module_run_id is not None:
            scanner_modules.finish_module_run(
                cron_module_run_id,
                status=cron_component.get("status") or "completed",
                summary=(cron_component.get("message") or "PostgreSQL schedules checked."),
                details=cron_component,
                log=cron_component.get("log"),
            )

        # Import configured usage CSVs as part of the scan instead of waiting
        # for a user to open a report or action page.
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Syncing usage metadata",
            message="Reading configured Power BI usage exports.",
        )
        usage_module_run_id = None
        if run_followups:
            usage_module_run_id = scanner_modules.create_module_run(
                "usage_metadata", scanner_job_id=operation_id, scan_run_id=scan_id
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
            try:
                from app.scanner.pbi_sync import (
                    cached_account_available,
                    service_principal_configured,
                    trigger_pbi_usage_sync_and_wait,
                )

                if service_principal_configured() or cached_account_available():
                    pbi_usage_result = trigger_pbi_usage_sync_and_wait(
                        cancel_existing=False,
                        cancel_generation=generation,
                        operation_id=operation_id,
                    )
                    pbi_usage_result = _mapping_result(pbi_usage_result)
                else:
                    pbi_usage_result = {
                        "status": "skipped",
                        "reason": "Power BI headless authentication is not configured.",
                    }
            except ScannerWorkCancelled:
                raise
            except Exception as e:
                pbi_usage_result = {"status": "failed", "error": str(e)}
                logger.exception("Power BI usage sync failed: %s", e)
        else:
            usage_result = {"status": "not_requested"}
            pbi_usage_result = {"status": "not_requested"}
        csv_component = component_result(
            usage_result,
            requested=run_followups and normalize_scan_status(usage_result.get("status")) != "skipped",
        )
        pbi_usage_component = component_result(
            pbi_usage_result,
            requested=run_followups and normalize_scan_status(pbi_usage_result.get("status")) != "skipped",
        )
        usage_failures = [
            name for name, item in (
                ("csv_import", csv_component),
                ("power_bi_usage", pbi_usage_component),
            )
            if normalize_scan_status(item.get("status")) == "failed"
        ]
        usage_warnings = [
            name for name, item in (
                ("csv_import", csv_component),
                ("power_bi_usage", pbi_usage_component),
            )
            if normalize_scan_status(item.get("status")) == "completed_with_warnings"
        ]
        usage_status = rollup_requested_component_status(
            {"csv_import": csv_component, "power_bi_usage": pbi_usage_component},
            empty_status="not_requested",
        )
        usage_issue_names = usage_failures or usage_warnings
        usage_parts = {"csv_import": csv_component, "power_bi_usage": pbi_usage_component}
        usage_diagnostic = (
            usage_parts[usage_issue_names[0]].get("diagnostic")
            if len(usage_issue_names) == 1
            and isinstance(usage_parts[usage_issue_names[0]].get("diagnostic"), dict)
            else None
        )
        usage_summary = (
            str(usage_diagnostic.get("operator_summary")) if usage_diagnostic else
            "Failed sub-steps: " + ", ".join(usage_failures) if usage_failures else
            "Completed with warnings: " + ", ".join(usage_warnings) if usage_warnings else
            "Usage metadata was not requested." if usage_status == "not_requested" else
            "Usage metadata synchronized."
        )
        usage_component = component_result(
            {
                "status": usage_status,
                "reason_code": (
                    "usage_metadata_substep_failed" if usage_failures else
                    "usage_metadata_partial" if usage_warnings else
                    "usage_metadata_not_requested" if usage_status == "not_requested" else
                    "usage_metadata_completed"
                ),
                "operator_summary": usage_summary,
                "csv_import": csv_component,
                "power_bi_usage": pbi_usage_component,
                "failed_subscans": usage_failures,
                "warning_subscans": usage_warnings,
                **({"diagnostic": usage_diagnostic} if usage_diagnostic else {}),
            },
            requested=run_followups,
        )
        components["usage"] = usage_component
        if usage_module_run_id is not None:
            scanner_modules.finish_module_run(
                usage_module_run_id,
                status=usage_status,
                summary=usage_summary,
                details=usage_component,
                log="\n".join(
                    (
                        f"Configured CSV import: {csv_component.get('status', 'unknown')}",
                        f"Power BI usage sync: {pbi_usage_component.get('status', 'unknown')}",
                    )
                ),
            )

        # Probe after dependency and cron discovery so all freshness and
        # data-quality decisions use the current graph.
        probe_result = None
        probe_module_run_id = None
        if run_followups and run_followup_probe:
            probe_module_run_id = scanner_modules.create_module_run(
                "source_freshness", scanner_job_id=operation_id, scan_run_id=scan_id
            )
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
        if probe_module_run_id is not None:
            scanner_modules.finish_module_run(
                probe_module_run_id,
                status=probe_component.get("status") or "completed",
                summary=(probe_component.get("message") or "Source freshness checked."),
                details=probe_component,
                log=probe_component.get("log"),
            )

        # Persist governance findings as owned actions with automatic closure
        # when the next scan proves the condition has cleared.
        scanner_jobs.heartbeat(
            operation_id,
            current_step="Evaluating governance checks",
            message="Checking best practices, schedules, and documentation coverage.",
        )
        governance_results = {}
        governance_module_run_id = None
        if run_followups:
            governance_module_run_id = scanner_modules.create_module_run(
                "governance", scanner_job_id=operation_id, scan_run_id=scan_id
            )
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
        failed_governance = [
            name for name, item in governance_components.items()
            if normalize_scan_status(item.get("status")) == "failed"
        ]
        governance_status = (
            "failed" if failed_governance else
            "completed_with_warnings"
            if any(component_has_warning(item) for item in governance_components.values())
            else "completed"
        ) if run_followups else "not_requested"
        governance_component = component_result(
            {
                "status": governance_status,
                **governance_components,
            },
            requested=run_followups,
        )
        components["governance"] = governance_component
        if governance_module_run_id is not None:
            scanner_modules.finish_module_run(
                governance_module_run_id,
                status=governance_status,
                summary=(
                    "Failed sub-checks: " + ", ".join(failed_governance)
                    if failed_governance else "Governance checks completed."
                ),
                details=governance_component,
                log="\n".join(
                    f"{name}: {item.get('status', 'unknown')}"
                    for name, item in governance_components.items()
                ),
            )

        mv_changed_queries = int(dep_component.get("changed_queries") or 0)
        changed_queries += mv_changed_queries
        auxiliary_log = [
            f"PostgreSQL dependencies: {dep_component.get('status', 'unknown')}",
            f"PostgreSQL schedules: {cron_component.get('status', 'unknown')}",
            f"Configured usage import: {usage_component.get('status', 'unknown')}",
        ]
        if dep_component.get("definition_status") == "skipped":
            auxiliary_log.append(
                "PostgreSQL MV query history: skipped; dependency discovery continued"
            )
        auxiliary_log.append(f"Source probe: {probe_component.get('status', 'unknown')}")
        if dep_component.get("query_change_log"):
            auxiliary_log.append(dep_component["query_change_log"])
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
                reports_scanned=reports_scanned,
                sources_found=active_source_count,
                new_sources=new_sources,
                changed_queries=changed_queries,
                broken_refs=broken_refs,
                components=components,
                log=final_log,
            )

        summary = {
            "scan_id": scan_id,
            "reports_scanned": reports_scanned,
            "reports_discovered": reports_discovered,
            "catalog_reports_active": active_report_count,
            "reports_preserved_incomplete": reports_preserved_incomplete,
            "ambiguous_provider_count": ambiguous_provider_count,
            "catalog_warning_count": catalog_warning_count,
            "sources_found": active_source_count,
            "new_sources": new_sources,
            "changed_queries": changed_queries,
            "broken_refs": broken_refs,
            "retired_report_tables": retired_report_tables,
            "components": components,
            "probe": probe_component,
            "postgres_dependencies": dep_component,
            "postgres_schedules": cron_component,
            "usage": usage_component,
            "governance": governance_components,
            "status": stored_status,
            "log": final_log,
            "scanned_path": str(Path(root).resolve()),
        }
        scanner_jobs.finish_job(
            operation_id,
            status=stored_status,
            result=summary,
            message=(
                f"Reconciled {reports_scanned} report snapshot(s) and found "
                f"{active_source_count} active source(s)."
            ),
        )
        logger.info("Scan completed: %s", summary)
        return summary

    except ScannerWorkCancelled as e:
        logger.info("Scan stopped: %s", e)
        scanner_modules.finish_active_runs_for_scan(
            scan_id,
            status="stopped",
            summary=str(e),
            details={"status": "stopped", "message": str(e)},
        )
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
                reports_scanned=reports_scanned,
                sources_found=active_source_count,
                new_sources=new_sources,
                changed_queries=changed_queries,
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
        current_report_run = scanner_modules.get_module_run(report_module_run_id)
        if current_report_run and current_report_run.get("active"):
            discovery_failure = isinstance(e, ReportDiscoveryError)
            scanner_modules.finish_module_run(
                report_module_run_id,
                status="failed",
                summary=(
                    "Configured report root does not exist or produced no usable report definitions."
                    if discovery_failure else "PBIX / TMDL catalog discovery failed."
                ),
                details={
                    "status": "failed",
                    "reason_code": (
                        "report_discovery_failed" if discovery_failure else "module_execution_failed"
                    ),
                    "error": str(e),
                },
                log="Report catalog discovery failed before reconciliation.",
            )
        scanner_modules.finish_active_runs_for_scan(
            scan_id,
            status="failed",
            summary="Module interrupted by scanner orchestration failure.",
            details={"status": "failed", "error": str(e)},
        )
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
                reports_scanned=reports_scanned,
                sources_found=active_source_count,
                new_sources=new_sources,
                changed_queries=changed_queries,
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
