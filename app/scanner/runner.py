"""
Scan runner — orchestrates a full scan.

1. Walk the reports folder (finds .pbix files or TMDL exports)
2. Parse all tables and extract sources
3. Deduplicate sources
4. Store everything in SQLite
5. Record the scan run
"""

import csv
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR, TMDL_ROOT
from app.database import get_db
from app.scanner.walker import walk_reports_root
from app.scanner.source_matcher import deduplicate_sources

logger = logging.getLogger(__name__)


def _load_owners_csv() -> tuple[list[str], list[str]]:
    """Load report_owner and business_owner names from owners.csv.

    Returns two lists: (report_owners, business_owners).
    Falls back to empty lists if file not found.
    """
    # Same directory as latest_upload_date.csv (one level above the project)
    csv_path = BASE_DIR.parent / "owners.csv"
    if not csv_path.exists():
        return [], []

    from app.scanner import read_csv_rows
    report_owners = []
    business_owners = []
    for row in read_csv_rows(csv_path):
        if not row:
            continue
        ro = row[0].strip() if len(row) > 0 else ""
        bo = row[1].strip() if len(row) > 1 else ""
        if ro:
            report_owners.append(ro)
        if bo:
            business_owners.append(bo)
    return report_owners, business_owners


def run_scan(reports_path: str | None = None) -> dict:
    """Run a full scan and store results.

    Returns a summary dict with scan statistics.
    """
    root = reports_path or TMDL_ROOT
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO scan_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        scan_id = cursor.lastrowid

    try:
        reports = walk_reports_root(root)
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

            # Load owner names from CSV for random assignment
            csv_report_owners, csv_business_owners = _load_owners_csv()

            # Upsert reports and their tables
            for report in reports:
                # Assign owners from CSV if available, otherwise keep report metadata
                report_owner = random.choice(csv_report_owners) if csv_report_owners else report.report_owner
                business_owner = random.choice(csv_business_owners) if csv_business_owners else report.business_owner

                existing_report = db.execute(
                    "SELECT id FROM reports WHERE name = ?",
                    (report.name,),
                ).fetchone()

                if existing_report:
                    report_id = existing_report["id"]
                    db.execute(
                        "UPDATE reports SET tmdl_path = ?, owner = ?, business_owner = ?, updated_at = ? WHERE id = ?",
                        (report.tmdl_path, report_owner, business_owner, now, report_id),
                    )
                else:
                    cursor = db.execute(
                        "INSERT INTO reports (name, tmdl_path, owner, business_owner, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (report.name, report.tmdl_path, report_owner, business_owner, now, now),
                    )
                    report_id = cursor.lastrowid

                # Upsert report tables
                for table in report.tables:
                    source_id = None
                    source = getattr(table, "source", None)
                    m_expression = getattr(table, "m_expression", None)
                    is_metadata = getattr(table, "is_metadata", False)

                    if source and not is_metadata:
                        # Find matching source in DB
                        source_row = db.execute(
                            "SELECT id FROM sources WHERE name = ?",
                            (source.display_name,),
                        ).fetchone()
                        if source_row:
                            source_id = source_row["id"]
                        elif source.source_type != "unknown":
                            broken_refs += 1
                            log_lines.append(
                                f"BROKEN: {report.name}/{table.table_name} "
                                f"references unknown source: {source.display_name}"
                            )

                    if not is_metadata:
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

            # Set initial "unknown" status for any source without a probe
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

            # Propagate ownership: if a source is used by exactly one report, inherit its owner
            db.execute("""
                UPDATE sources SET owner = (
                    SELECT r.owner FROM report_tables rt
                    JOIN reports r ON r.id = rt.report_id
                    WHERE rt.source_id = sources.id AND r.owner IS NOT NULL
                    GROUP BY rt.source_id
                    HAVING COUNT(DISTINCT r.id) = 1
                    LIMIT 1
                )
                WHERE owner IS NULL
            """)
            # For sources used by multiple reports, mark as "Multiple"
            db.execute("""
                UPDATE sources SET owner = 'Multiple' WHERE owner IS NULL AND (
                    SELECT COUNT(DISTINCT r.id) FROM report_tables rt
                    JOIN reports r ON r.id = rt.report_id
                    WHERE rt.source_id = sources.id AND r.owner IS NOT NULL
                ) > 1
            """)

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

        # Run simulated freshness if enabled
        from app.config import SIMULATE_FRESHNESS
        if SIMULATE_FRESHNESS:
            from app.scanner.prober import simulate_probe
            try:
                simulate_probe()
                logger.info("Simulated freshness probe completed after scan")
            except Exception as e:
                logger.exception("Simulated probe failed after scan: %s", e)

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
