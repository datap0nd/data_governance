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

from app.config import TMDL_ROOT, DB_PATH, PGHOST, PGDATABASE
from app.database import get_db
from app.scanner.control import ScannerWorkCancelled, assert_not_cancelled, current_cancel_generation
from app.scanner.tmdl_parser import is_folder_like_file_source, path_has_file_extension
from app.scanner.walker import walk_reports_root
from app.scanner.source_matcher import deduplicate_sources
from app.scanner.findings import sync_managed_actions
from app.asset_visibility import get_active_source_ids
from app.query_history import (
    REPORT_M_KIND,
    link_versions_to_action,
    observe_query,
    report_artifact_key,
)

logger = logging.getLogger(__name__)

_FILE_SOURCE_DB_TYPES = {"csv", "excel", "folder", "file"}


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
        db.execute(
            "UPDATE sources SET archived = 1, updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        db.execute(
            """UPDATE actions SET status = 'resolved', resolved_at = ?,
                                  updated_at = ?,
                                  notes = COALESCE(notes, '') || ' [auto-resolved: folder source archived]'
               WHERE source_id = ? AND status NOT IN ('resolved', 'expected')""",
            (now, now, row["id"]),
        )
        db.execute(
            """UPDATE alerts SET resolution_status = 'resolved', resolved_at = ?,
                                 acknowledged = 1, acknowledged_by = 'auto',
                                 resolution_reason = 'Folder source archived'
               WHERE source_id = ? AND resolution_status IS NULL""",
            (now, row["id"]),
        )
        archived_count += 1
        log_lines.append(f"ARCHIVED: {row['name']} (folder path, not a file source)")
    if archived_count:
        log_lines.append(f"TOTAL ARCHIVED: {archived_count} folder-like file sources")


def run_scan(
    reports_path: str | None = None,
    *,
    cancel_generation: int | None = None,
    run_followup_probe: bool = True,
) -> dict:
    """Run a full scan and store results.

    Returns a summary dict with scan statistics.
    """
    generation = current_cancel_generation() if cancel_generation is None else cancel_generation
    assert_not_cancelled(generation, "Report scan")

    _backup_db()

    root = reports_path or TMDL_ROOT
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO scan_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        scan_id = cursor.lastrowid

    try:
        assert_not_cancelled(generation, "Report scan")
        reports = walk_reports_root(root)
        assert_not_cancelled(generation, "Report scan")
        all_sources = deduplicate_sources(reports)
        assert_not_cancelled(generation, "Report scan")

        new_sources = 0
        changed_queries = 0
        broken_refs = 0
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

        with get_db() as db:
            assert_not_cancelled(generation, "Report scan")
            # Normalize PostgreSQL source names BEFORE upsert so matches work.
            # Handles two legacy naming patterns:
            #   1. "SERVER_NAME/database/schema.table" -> "schema.table"
            #   2. "database.schema.table" -> "schema.table"
            # Canonical form is just schema.table.
            def _merge_source(db, old_id, new_id, old_name, new_name, log_lines):
                """Migrate all FK references from old_id to new_id, then delete old."""
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
                db.execute("DELETE FROM source_probes WHERE source_id = ?", (old_id,))
                db.execute("DELETE FROM sources WHERE id = ?", (old_id,))
                log_lines.append(f"MERGED: {old_name} -> {new_name} (source {old_id} into {new_id})")

            # Pass 1: Strip "PGHOST/PGDATABASE/" prefix
            if PGHOST:
                prefix = f"{PGHOST}/"
                if PGDATABASE:
                    prefix += f"{PGDATABASE}/"
                old_rows = db.execute(
                    "SELECT id, name FROM sources WHERE name LIKE ? AND type = 'postgresql'",
                    (f"{PGHOST}/%",),
                ).fetchall()
                for row in old_rows:
                    new_name = row["name"].replace(prefix, "", 1)
                    dup = db.execute("SELECT id FROM sources WHERE name = ? AND id != ?",
                                    (new_name, row["id"])).fetchone()
                    if dup:
                        _merge_source(db, row["id"], dup["id"], row["name"], new_name, log_lines)
                    else:
                        db.execute("UPDATE sources SET name = ? WHERE id = ?",
                                   (new_name, row["id"]))

            # Pass 2: Strip "PGDATABASE." prefix.
            if PGDATABASE:
                db_prefix = f"{PGDATABASE}."
                old_rows = db.execute(
                    "SELECT id, name FROM sources WHERE name LIKE ? AND type = 'postgresql'",
                    (f"{PGDATABASE}.%",),
                ).fetchall()
                for row in old_rows:
                    new_name = row["name"].replace(db_prefix, "", 1)
                    if new_name == row["name"]:
                        continue
                    dup = db.execute("SELECT id FROM sources WHERE name = ? AND id != ?",
                                    (new_name, row["id"])).fetchone()
                    if dup:
                        _merge_source(db, row["id"], dup["id"], row["name"], new_name, log_lines)
                    else:
                        db.execute("UPDATE sources SET name = ? WHERE id = ?",
                                   (new_name, row["id"]))

            # Pass 3: Strip parenthetical artifacts from source names
            # e.g. "schema.table_name (view)" -> "schema.table_name"
            # Also strips internal parens like "foo(bar)baz" -> "foobaz"
            import re as _re
            paren_rows = db.execute(
                "SELECT id, name FROM sources WHERE name LIKE '%(%' OR name LIKE '%)%'"
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
                        _merge_source(db, row["id"], dup["id"], row["name"], new_name, log_lines)
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
                "SELECT id, name FROM sources WHERE type = 'postgresql' AND archived = 0"
            ).fetchall()
            archived_count = 0
            for row in pg_rows:
                # A clean PG source name is either "schema.table" or "table"
                if _validate_table_name(row["name"]) is None:
                    db.execute(
                        "UPDATE sources SET archived = 1, updated_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    # Close any open actions tied to this source so the Alerts
                    # table doesn't keep showing noise
                    db.execute(
                        """UPDATE actions SET status = 'resolved', resolved_at = ?,
                                              updated_at = ?,
                                              notes = COALESCE(notes, '') || ' [auto-resolved: source archived]'
                           WHERE source_id = ? AND status NOT IN ('resolved', 'expected')""",
                        (now, now, row["id"]),
                    )
                    # Acknowledge any open alerts too
                    db.execute(
                        """UPDATE alerts SET resolution_status = 'resolved', resolved_at = ?,
                                             acknowledged = 1, acknowledged_by = 'auto',
                                             resolution_reason = 'Source archived (invalid name)'
                           WHERE source_id = ? AND resolution_status IS NULL""",
                        (now, row["id"]),
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

            # Upsert sources
            for key, source_info in all_sources.items():
                existing = db.execute(
                    "SELECT id, source_query, owner FROM sources WHERE name = ?",
                    (source_info.display_name,),
                ).fetchone()

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
                    db.execute(
                        """INSERT INTO sources (name, type, connection_info, source_query, discovered_by, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'scan', ?, ?)""",
                        (
                            source_info.display_name,
                            source_info.source_type,
                            source_info.connection_info,
                            source_info.raw_expression,
                            now,
                            now,
                        ),
                    )
                    new_sources += 1
                    table_info = f" -> {source_info.sql_table}" if source_info.sql_table else ""
                    log_lines.append(f"NEW: {source_info.display_name} ({source_info.source_type}){table_info}")

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
                        source_row = db.execute(
                            "SELECT id FROM sources WHERE name = ?",
                            (source.display_name,),
                        ).fetchone()
                        if source_row:
                            source_id = source_row["id"]
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
                    notes = (
                        f"{len(names)} Power Query change{'s' if len(names) != 1 else ''} "
                        f"detected in {report.name}: {', '.join(names)}."
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
                    log_lines.append(
                        f"CHANGED: {report.name} Power Query in {', '.join(names)}"
                    )

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

            # Update scan run record
            assert_not_cancelled(generation, "Report scan")
            active_report_count = db.execute(
                "SELECT COUNT(*) AS count FROM reports WHERE COALESCE(archived, 0) = 0"
            ).fetchone()["count"]
            active_source_count = len(get_active_source_ids(db))
            log_text = "\n".join(log_lines) if log_lines else "No changes detected."
            finished = datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE scan_runs
                   SET finished_at = ?, reports_scanned = ?, sources_found = ?,
                       new_sources = ?, changed_queries = ?, broken_refs = ?,
                       status = 'completed', log = ?
                   WHERE id = ?""",
                (
                    finished,
                    active_report_count,
                    active_source_count,
                    new_sources,
                    changed_queries,
                    broken_refs,
                    log_text,
                    scan_id,
                ),
            )

        # Scan PostgreSQL MV dependencies
        assert_not_cancelled(generation, "Report scan")
        from app.scanner.pg_deps import scan_pg_dependencies
        try:
            dep_result = scan_pg_dependencies(scan_run_id=scan_id)
            logger.info("PG dependency scan completed: %s", dep_result.get("status"))
        except Exception as e:
            dep_result = {"status": "failed", "error": str(e)}
            logger.exception("PG dependency scan failed: %s", e)

        # Scan pg_cron for MV refresh schedules
        assert_not_cancelled(generation, "Report scan")
        from app.scanner.pg_cron import scan_pg_cron
        try:
            cron_result = scan_pg_cron()
            logger.info("pg_cron scan completed: %s", cron_result.get("status"))
        except Exception as e:
            cron_result = {"status": "failed", "error": str(e)}
            logger.exception("pg_cron scan failed: %s", e)

        # Scan Python scripts for table references
        assert_not_cancelled(generation, "Report scan")
        from app.scanner.script_runner import run_script_scan
        try:
            script_result = run_script_scan()
            logger.info("Script scan completed: %s", script_result.get("status"))
        except Exception as e:
            script_result = {"status": "failed", "error": str(e)}
            logger.exception("Script scan failed: %s", e)

        # Scan Windows Task Scheduler after scripts so task-to-script links
        # and task failure actions are refreshed in the same overall run.
        assert_not_cancelled(generation, "Report scan")
        from app.scanner.task_scheduler_runner import run_task_scheduler_scan
        try:
            task_result = run_task_scheduler_scan()
            logger.info("Task Scheduler scan completed: %s", task_result.get("status"))
        except Exception as e:
            task_result = {"status": "failed", "error": str(e)}
            logger.exception("Task Scheduler scan failed: %s", e)

        # Import configured usage CSVs as part of the scan instead of waiting
        # for a user to open a report or action page.
        from app.usage import sync_usage_from_csv_if_configured
        try:
            with get_db() as db:
                usage_result = sync_usage_from_csv_if_configured(db)
            logger.info("Usage sync completed: %s", usage_result.get("status"))
        except Exception as e:
            usage_result = {"status": "failed", "error": str(e)}
            logger.exception("Usage sync failed: %s", e)

        # Probe after dependency, cron, script, and task discovery so all
        # freshness and data-quality decisions use the current graph.
        probe_result = None
        if run_followup_probe:
            from app.scanner.prober import run_probe
            try:
                probe_result = run_probe(cancel_generation=generation)
                logger.info("Freshness and data-quality checks completed after scan")
            except ScannerWorkCancelled:
                raise
            except Exception as e:
                probe_result = {"status": "failed", "error": str(e)}
                logger.exception("Probe failed after scan: %s", e)

        # Persist governance findings as owned actions with automatic closure
        # when the next scan proves the condition has cleared.
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

        mv_changed_queries = int(dep_result.get("changed_queries") or 0)
        changed_queries += mv_changed_queries
        auxiliary_log = [
            f"PostgreSQL dependencies: {dep_result.get('status', 'unknown')}",
            f"PostgreSQL schedules: {cron_result.get('status', 'unknown')}",
            f"Scripts: {script_result.get('status', 'unknown')}",
            f"Windows scheduled tasks: {task_result.get('status', 'unknown')}",
            f"Configured usage import: {usage_result.get('status', 'unknown')}",
        ]
        if dep_result.get("definition_status") == "skipped":
            auxiliary_log.append(
                "PostgreSQL MV query history: skipped; dependency discovery continued"
            )
        if probe_result is not None:
            auxiliary_log.append(f"Source probe: {probe_result.get('status', 'unknown')}")
        if dep_result.get("query_change_log"):
            auxiliary_log.append(dep_result["query_change_log"])
        for name, result in governance_results.items():
            status = result.get("status", "completed") if isinstance(result, dict) else "unknown"
            auxiliary_log.append(f"Governance {name}: {status}")
        final_log = "\n".join([log_text, *auxiliary_log])
        with get_db() as db:
            db.execute(
                "UPDATE scan_runs SET finished_at = ?, changed_queries = ?, log = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), changed_queries, final_log, scan_id),
            )

        summary = {
            "scan_id": scan_id,
            "reports_scanned": active_report_count,
            "sources_found": active_source_count,
            "new_sources": new_sources,
            "changed_queries": changed_queries,
            "broken_refs": broken_refs,
            "probe": probe_result,
            "postgres_dependencies": dep_result,
            "postgres_schedules": cron_result,
            "scripts": script_result,
            "task_scheduler": task_result,
            "usage": usage_result,
            "governance": governance_results,
            "status": "completed",
            "log": final_log,
            "scanned_path": str(Path(root).resolve()),
        }
        logger.info("Scan completed: %s", summary)
        return summary

    except ScannerWorkCancelled as e:
        logger.info("Scan stopped: %s", e)
        with get_db() as db:
            db.execute(
                "UPDATE scan_runs SET finished_at = ?, status = 'stopped', log = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), str(e), scan_id),
            )
        return {
            "scan_id": scan_id,
            "status": "stopped",
            "message": str(e),
        }

    except Exception as e:
        logger.exception("Scan failed")
        with get_db() as db:
            db.execute(
                "UPDATE scan_runs SET finished_at = ?, status = 'failed', log = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), str(e), scan_id),
            )
        return {
            "scan_id": scan_id,
            "status": "failed",
            "error": str(e),
        }
