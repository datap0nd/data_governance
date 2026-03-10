"""
Scan runner — orchestrates a full scan.

1. Walk the reports folder (finds .pbix files or TMDL exports)
2. Parse all tables and extract sources
3. Deduplicate sources
4. Store everything in SQLite
5. Record the scan run
"""

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


_UPSTREAM_SYSTEMS = [
    ("GSCM - Global Supply Chain Master", "GSCM"),
    ("GSCM - Global Sourcing & Contract Mgmt", "GSCM"),
    ("GSCM - Goods & Services Catalog Manager", "GSCM"),
    ("GSCM - Group Supply Configuration Module", "GSCM"),
    ("ASAP - Automated Sales Analytics Platform", "ASAP"),
    ("ASAP - Advanced Strategic Account Portal", "ASAP"),
    ("ASAP - Aggregated Sales & Allocation Pipeline", "ASAP"),
    ("ASAP - Analytics Suite for Account Performance", "ASAP"),
]

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _seed_upstream_and_schedules(db, now: str):
    """Seed upstream systems, assign upstream linkage, and randomize refresh schedules."""
    # 1. Insert upstream systems if table is empty
    count = db.execute("SELECT COUNT(*) AS c FROM upstream_systems").fetchone()["c"]
    if count == 0:
        for name, code in _UPSTREAM_SYSTEMS:
            day = random.choice(_WEEKDAYS)
            db.execute(
                "INSERT INTO upstream_systems (name, code, refresh_day, created_at) VALUES (?, ?, ?, ?)",
                (name, code, day, now),
            )

    # 2. For each source without an upstream_id, randomly assign one
    upstream_ids = [r["id"] for r in db.execute("SELECT id FROM upstream_systems").fetchall()]
    if upstream_ids:
        unlinked = db.execute("SELECT id FROM sources WHERE upstream_id IS NULL").fetchall()
        for row in unlinked:
            db.execute(
                "UPDATE sources SET upstream_id = ? WHERE id = ?",
                (random.choice(upstream_ids), row["id"]),
            )

    # 3. For each source without a refresh_schedule, assign random weekday
    no_sched = db.execute("SELECT id FROM sources WHERE refresh_schedule IS NULL OR refresh_schedule = ''").fetchall()
    for row in no_sched:
        db.execute(
            "UPDATE sources SET refresh_schedule = ? WHERE id = ?",
            (random.choice(_WEEKDAYS), row["id"]),
        )

    # 4. Set all reports to frequency = 'Weekly - Sunday' if not set
    db.execute(
        "UPDATE reports SET frequency = 'Weekly - Sunday' WHERE frequency IS NULL OR frequency = ''"
    )


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

            # Load owner names and Power BI links from CSVs
            csv_report_owners, csv_business_owners = _load_owners_csv()
            powerbi_links = _load_powerbi_links()

            # Upsert reports and their tables
            for report in reports:
                # Assign owners from CSV if available, otherwise keep report metadata
                report_owner = random.choice(csv_report_owners) if csv_report_owners else report.report_owner
                business_owner = random.choice(csv_business_owners) if csv_business_owners else report.business_owner
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

            # Assign source owners randomly from owners.csv
            csv_report_owners_for_sources, _ = _load_owners_csv()
            if csv_report_owners_for_sources:
                no_owner = db.execute("SELECT id FROM sources WHERE owner IS NULL OR owner = ''").fetchall()
                for row in no_owner:
                    db.execute(
                        "UPDATE sources SET owner = ? WHERE id = ?",
                        (random.choice(csv_report_owners_for_sources), row["id"]),
                    )

            # Seed upstream systems and refresh schedules
            _seed_upstream_and_schedules(db, now)

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
