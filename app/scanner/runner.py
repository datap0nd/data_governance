"""
Scan runner — orchestrates a full scan.

1. Walk the reports folder (finds .pbix files or TMDL exports)
2. Parse all tables and extract sources
3. Deduplicate sources
4. Store everything in SQLite
5. Record the scan run
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR, TMDL_ROOT
from app.database import get_db
from app.scanner.walker import walk_reports_root
from app.scanner.source_matcher import deduplicate_sources

logger = logging.getLogger(__name__)



def _load_powerbi_links() -> dict[str, str]:
    """Load report name → Power BI URL mapping from powerbi_links.csv.

    CSV format: report_name,powerbi_url (no headers).
    Returns a dict mapping report name to URL.
    """
    csv_path = BASE_DIR.parent / "powerbi_links.csv"
    if not csv_path.exists():
        return {}

    from app.scanner import read_csv_rows
    links = {}
    for row in read_csv_rows(csv_path):
        if not row or len(row) < 2:
            continue
        name = row[0].strip()
        url = row[1].strip()
        if name and url:
            links[name] = url
    return links



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

            # Load Power BI links from CSV
            powerbi_links = _load_powerbi_links()

            # Upsert reports and their tables
            for report in reports:
                report_owner = report.report_owner
                business_owner = report.business_owner
                powerbi_url = powerbi_links.get(report.name, None)

                existing_report = db.execute(
                    "SELECT id FROM reports WHERE name = ?",
                    (report.name,),
                ).fetchone()

                if existing_report:
                    report_id = existing_report["id"]
                    db.execute(
                        "UPDATE reports SET tmdl_path = ?, owner = ?, business_owner = ?, powerbi_url = ?, updated_at = ? WHERE id = ?",
                        (report.tmdl_path, report_owner, business_owner, powerbi_url, now, report_id),
                    )
                else:
                    cursor = db.execute(
                        "INSERT INTO reports (name, tmdl_path, owner, business_owner, powerbi_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (report.name, report.tmdl_path, report_owner, business_owner, powerbi_url, now, now),
                    )
                    report_id = cursor.lastrowid

                # Upsert report tables
                from app.scanner.tmdl_parser import is_auto_table
                for table in report.tables:
                    # Skip Power BI auto-generated internal tables
                    if is_auto_table(table.table_name):
                        continue
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
