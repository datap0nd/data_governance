"""
Scan runner — orchestrates a full TMDL scan.

1. Walk the folder structure
2. Parse all tables
3. Deduplicate sources
4. Store everything in SQLite
5. Record the scan run
"""

import logging
from datetime import datetime, timezone

from app.config import TMDL_ROOT
from app.database import get_db
from app.scanner.walker import walk_tmdl_root
from app.scanner.source_matcher import deduplicate_sources
from app.scanner.tmdl_parser import resolve_parameters

logger = logging.getLogger(__name__)


def run_scan(tmdl_root: str | None = None) -> dict:
    """Run a full TMDL scan and store results.

    Returns a summary dict with scan statistics.
    """
    root = tmdl_root or TMDL_ROOT
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        # Record scan start
        cursor = db.execute(
            "INSERT INTO scan_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        scan_id = cursor.lastrowid

    try:
        # Walk and parse
        reports = walk_tmdl_root(root)
        all_sources = deduplicate_sources(reports)

        new_sources = 0
        changed_queries = 0
        broken_refs = 0
        log_lines = []

        with get_db() as db:
            # Upsert sources
            for key, source_info in all_sources.items():
                existing = db.execute(
                    "SELECT id, source_query FROM sources WHERE name = ?",
                    (source_info.display_name,),
                ).fetchone()

                if existing:
                    source_id = existing["id"]
                    old_query = existing["source_query"] or ""
                    new_query = source_info.raw_expression or ""
                    if old_query != new_query:
                        changed_queries += 1
                        db.execute(
                            "UPDATE sources SET source_query = ?, connection_info = ?, updated_at = ? WHERE id = ?",
                            (new_query, source_info.connection_info, now, source_id),
                        )
                        log_lines.append(f"CHANGED: {source_info.display_name} query updated")
                else:
                    cursor = db.execute(
                        """INSERT INTO sources (name, type, connection_info, source_query, discovered_by, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'tmdl_scan', ?, ?)""",
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
                    log_lines.append(f"NEW: {source_info.display_name} ({source_info.source_type})")

            # Upsert reports and their tables
            for report in reports:
                existing_report = db.execute(
                    "SELECT id FROM reports WHERE name = ?",
                    (report.name,),
                ).fetchone()

                if existing_report:
                    report_id = existing_report["id"]
                    db.execute(
                        "UPDATE reports SET tmdl_path = ?, owner = ?, business_owner = ?, updated_at = ? WHERE id = ?",
                        (report.tmdl_path, report.report_owner, report.business_owner, now, report_id),
                    )
                else:
                    cursor = db.execute(
                        "INSERT INTO reports (name, tmdl_path, owner, business_owner, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (report.name, report.tmdl_path, report.report_owner, report.business_owner, now, now),
                    )
                    report_id = cursor.lastrowid

                # Upsert report tables
                for table in report.tables:
                    source_id = None
                    if table.source:
                        resolved = resolve_parameters(table.source, report.expressions)
                        # Find matching source in DB
                        source_row = db.execute(
                            "SELECT id FROM sources WHERE name = ?",
                            (resolved.display_name,),
                        ).fetchone()
                        if source_row:
                            source_id = source_row["id"]
                        elif table.source.source_type != "unknown":
                            broken_refs += 1
                            log_lines.append(
                                f"BROKEN: {report.name}/{table.table_name} "
                                f"references unknown source: {resolved.display_name}"
                            )

                    db.execute(
                        """INSERT INTO report_tables (report_id, table_name, source_id, source_expression, last_scanned)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(report_id, table_name)
                           DO UPDATE SET source_id = ?, source_expression = ?, last_scanned = ?""",
                        (
                            report_id,
                            table.table_name,
                            source_id,
                            table.m_expression,
                            now,
                            source_id,
                            table.m_expression,
                            now,
                        ),
                    )

            # Update scan run record
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
                    len(reports),
                    len(all_sources),
                    new_sources,
                    changed_queries,
                    broken_refs,
                    log_text,
                    scan_id,
                ),
            )

        summary = {
            "scan_id": scan_id,
            "reports_scanned": len(reports),
            "sources_found": len(all_sources),
            "new_sources": new_sources,
            "changed_queries": changed_queries,
            "broken_refs": broken_refs,
            "status": "completed",
            "log": log_text,
        }
        logger.info("Scan completed: %s", summary)
        return summary

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
