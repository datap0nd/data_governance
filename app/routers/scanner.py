import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import fastapi
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.config import TMDL_ROOT, DB_PATH, PGHOST, PGDATABASE, PGUSER
from app.database import get_db
from app.scanner.runner import run_scan
from app.scanner.prober import run_probe
from app.scanner.pbi_sync import trigger_pbi_sync, import_pbi_data, trigger_pbi_usage_sync
from app.scanner.walker import diagnose_reports_root
from app.models import ScanRunOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


def _require_local(request: Request):
    """Raise 403 if request is not from localhost."""
    ip = request.client.host if request.client else ""
    if ip not in ("127.0.0.1", "::1") and not ip.startswith("::ffff:127.0.0.1"):
        raise HTTPException(status_code=403, detail="Scanner restricted to server machine")


@router.post("/run")
def do_scan(request: Request):
    """Trigger a full scan (reads .pbix files or TMDL exports)."""
    _require_local(request)
    result = run_scan()
    # After scan, probe sources for freshness
    try:
        probe_result = run_probe()
        result["probe"] = probe_result
    except Exception as e:
        logger.exception("Probe failed after scan")
        result["probe"] = {"status": "failed", "error": str(e)}
    return result


@router.post("/probe")
def do_probe(request: Request):
    """Probe all sources for freshness (file mod times)."""
    _require_local(request)
    return run_probe()


@router.get("/probe/runs")
def list_probe_runs():
    """List all probe runs, most recent first."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM probe_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/pbi-sync")
def do_pbi_sync(request: Request):
    """Launch PBI sync in the user's interactive session."""
    _require_local(request)
    return trigger_pbi_sync()


@router.post("/pbi-import")
async def do_pbi_import(request: Request):
    """Receive PBI data from the PS1 script and update the DB."""
    _require_local(request)
    data = await request.json()
    return import_pbi_data(data)


@router.get("/runs", response_model=list[ScanRunOut])
def list_scan_runs():
    """List all scan runs, most recent first."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    return [ScanRunOut(**dict(r)) for r in rows]


@router.get("/diagnose")
def diagnose_scan():
    """Step-by-step diagnostics of the scanner discovery logic."""
    return diagnose_reports_root(TMDL_ROOT)


@router.get("/runs/{run_id}", response_model=ScanRunOut)
def get_scan_run(run_id: int):
    with get_db() as db:
        r = db.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return {"error": "Scan run not found"}
    return ScanRunOut(**dict(r))


@router.post("/pg-deps")
def do_pg_deps(request: Request):
    """Scan PostgreSQL for materialized view dependencies."""
    _require_local(request)
    from app.scanner.pg_deps import scan_pg_dependencies
    return scan_pg_dependencies()


@router.post("/pg-cron")
def do_pg_cron(request: Request):
    """Scan pg_cron for MV refresh schedules."""
    _require_local(request)
    from app.scanner.pg_cron import scan_pg_cron
    return scan_pg_cron()


class OpenPathRequest(BaseModel):
    path: str


@router.post("/open-path")
def open_path(body: OpenPathRequest, request: Request):
    """Open the containing folder of a file path in the OS file explorer."""
    _require_local(request)
    target = Path(body.path)

    # If it's a file, open its parent folder; if directory, open it directly
    folder = target.parent if target.is_file() else target
    if not folder.exists():
        # Try the path as-is even if we can't verify (network paths)
        folder = target.parent if not target.suffix == "" else target

    try:
        if sys.platform == "win32":
            if target.is_file():
                # Select the file in Explorer
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(folder)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return {"status": "ok", "opened": str(folder)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open path: {e}")


@router.get("/diagnostic")
def diagnostic_report():
    """Generate a comprehensive diagnostic report for debugging."""
    import os
    import platform
    from pathlib import Path
    from app.config import SCRIPTS_PATHS, REPORTS_PATH

    report = {}

    # ── Environment ──
    report["environment"] = {
        "python_version": sys.version,
        "platform": platform.platform(),
        "db_path": DB_PATH,
        "db_exists": Path(DB_PATH).exists(),
        "db_size_kb": round(Path(DB_PATH).stat().st_size / 1024) if Path(DB_PATH).exists() else 0,
        "tmdl_root": TMDL_ROOT,
        "tmdl_root_exists": Path(TMDL_ROOT).is_dir() if TMDL_ROOT else False,
        "reports_path": REPORTS_PATH,
        "scripts_paths": SCRIPTS_PATHS,
        "pghost": PGHOST or "(not set)",
        "pgdatabase": PGDATABASE or "(not set)",
        "pguser": PGUSER or "(not set)",
    }

    with get_db() as db:
        # ── Table Row Counts ──
        tables = [
            "sources", "reports", "report_tables", "report_pages",
            "report_visuals", "visual_fields", "report_measures", "report_columns",
            "source_probes", "probe_runs", "scan_runs",
            "source_dependencies", "scripts", "script_tables",
            "scheduled_tasks", "alerts", "actions", "checks",
            "upstream_systems", "tasks", "event_log", "people",
        ]
        counts = {}
        for t in tables:
            try:
                row = db.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()
                counts[t] = row["c"]
            except Exception:
                counts[t] = "TABLE_NOT_FOUND"
        report["row_counts"] = counts

        # ── Sources Summary ──
        src_rows = db.execute("""
            SELECT s.id, s.name, s.type, s.discovered_by,
                   sp.status AS probe_status,
                   (SELECT COUNT(*) FROM report_tables rt WHERE rt.source_id = s.id) AS report_count,
                   (SELECT COUNT(*) FROM script_tables st WHERE st.source_id = s.id) AS script_ref_count,
                   (SELECT COUNT(*) FROM source_dependencies sd WHERE sd.source_id = s.id) AS dep_from_count,
                   (SELECT COUNT(*) FROM source_dependencies sd WHERE sd.depends_on_id = s.id) AS dep_to_count
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE s.archived = 0
            ORDER BY s.name
        """).fetchall()
        report["sources"] = [
            {
                "id": r["id"], "name": r["name"], "type": r["type"],
                "discovered_by": r["discovered_by"], "probe_status": r["probe_status"] or "unknown",
                "report_count": r["report_count"], "script_ref_count": r["script_ref_count"],
                "dep_from_count": r["dep_from_count"], "dep_to_count": r["dep_to_count"],
            }
            for r in src_rows
        ]

        # ── Source Name Issues ──
        # Sources with IP prefix still in name
        ip_sources = db.execute(
            "SELECT id, name FROM sources WHERE name LIKE '%.%.%.%/%' AND archived = 0"
        ).fetchall()
        report["sources_with_ip_prefix"] = [{"id": r["id"], "name": r["name"]} for r in ip_sources]

        # Potential duplicate source names (same table, different prefix)
        dup_check = db.execute("""
            SELECT s1.id AS id1, s1.name AS name1, s2.id AS id2, s2.name AS name2
            FROM sources s1
            JOIN sources s2 ON s1.id < s2.id
                AND s1.type = s2.type
                AND s1.archived = 0 AND s2.archived = 0
            WHERE (
                s1.name LIKE '%.' || SUBSTR(s2.name, INSTR(s2.name, '.') + 1)
                OR s2.name LIKE '%.' || SUBSTR(s1.name, INSTR(s1.name, '.') + 1)
            )
            LIMIT 50
        """).fetchall()
        report["potential_duplicate_sources"] = [
            {"id1": r["id1"], "name1": r["name1"], "id2": r["id2"], "name2": r["name2"]}
            for r in dup_check
        ]

        # ── Broken FK References ──
        broken_fks = {}

        # script_tables pointing to non-existent sources
        broken = db.execute("""
            SELECT st.id, st.script_id, st.table_name, st.source_id, st.direction
            FROM script_tables st
            WHERE st.source_id IS NOT NULL
              AND st.source_id NOT IN (SELECT id FROM sources)
        """).fetchall()
        broken_fks["script_tables_missing_source"] = [dict(r) for r in broken]

        # report_tables pointing to non-existent sources
        broken = db.execute("""
            SELECT rt.id, rt.report_id, rt.table_name, rt.source_id
            FROM report_tables rt
            WHERE rt.source_id IS NOT NULL
              AND rt.source_id NOT IN (SELECT id FROM sources)
        """).fetchall()
        broken_fks["report_tables_missing_source"] = [dict(r) for r in broken]

        # source_dependencies pointing to non-existent sources
        broken = db.execute("""
            SELECT sd.id, sd.source_id, sd.depends_on_id
            FROM source_dependencies sd
            WHERE sd.source_id NOT IN (SELECT id FROM sources)
               OR sd.depends_on_id NOT IN (SELECT id FROM sources)
        """).fetchall()
        broken_fks["source_deps_missing_source"] = [dict(r) for r in broken]

        report["broken_fk_references"] = broken_fks

        # ── Script Tables Detail ──
        st_rows = db.execute("""
            SELECT st.table_name, st.direction, st.source_id,
                   sc.display_name AS script_name, sc.path AS script_path,
                   s.name AS matched_source_name
            FROM script_tables st
            JOIN scripts sc ON sc.id = st.script_id
            LEFT JOIN sources s ON s.id = st.source_id
            WHERE COALESCE(sc.archived, 0) = 0
            ORDER BY sc.display_name, st.direction, st.table_name
        """).fetchall()
        report["script_tables"] = [
            {
                "script": r["script_name"], "table": r["table_name"],
                "direction": r["direction"], "source_id": r["source_id"],
                "matched_source": r["matched_source_name"],
            }
            for r in st_rows
        ]

        # ── Unlinked Script Tables (no source_id match) ──
        unlinked = [r for r in report["script_tables"] if r["source_id"] is None]
        report["unlinked_script_tables"] = unlinked

        # ── Source Dependencies ──
        dep_rows = db.execute("""
            SELECT sd.source_id, s1.name AS source_name,
                   sd.depends_on_id, s2.name AS depends_on_name,
                   sd.discovered_by
            FROM source_dependencies sd
            LEFT JOIN sources s1 ON s1.id = sd.source_id
            LEFT JOIN sources s2 ON s2.id = sd.depends_on_id
            ORDER BY s1.name
        """).fetchall()
        report["source_dependencies"] = [
            {
                "source_id": r["source_id"], "source_name": r["source_name"],
                "depends_on_id": r["depends_on_id"], "depends_on_name": r["depends_on_name"],
                "discovered_by": r["discovered_by"],
            }
            for r in dep_rows
        ]

        # ── Reports with No Sources ──
        no_src = db.execute("""
            SELECT r.id, r.name
            FROM reports r
            WHERE NOT EXISTS (
                SELECT 1 FROM report_tables rt
                WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
            )
        """).fetchall()
        report["reports_with_no_sources"] = [{"id": r["id"], "name": r["name"]} for r in no_src]

        # ── Recent Scan Runs ──
        scans = db.execute(
            "SELECT id, started_at, finished_at, status, reports_scanned, sources_found, new_sources, changed_queries, broken_refs, log FROM scan_runs ORDER BY id DESC LIMIT 5"
        ).fetchall()
        report["recent_scans"] = [dict(r) for r in scans]

        # ── Source Type Distribution ──
        type_dist = db.execute(
            "SELECT type, COUNT(*) AS count FROM sources WHERE archived = 0 GROUP BY type ORDER BY count DESC"
        ).fetchall()
        report["source_type_distribution"] = {r["type"]: r["count"] for r in type_dist}

        # ── Probe Status Distribution ──
        probe_dist = db.execute("""
            SELECT sp.status, COUNT(*) AS count
            FROM sources s
            JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE s.archived = 0
            GROUP BY sp.status
        """).fetchall()
        report["probe_status_distribution"] = {r["status"]: r["count"] for r in probe_dist}

    return report


@router.get("/pbi-usage-days")
def get_usage_days():
    """Return list of days already synced for PBI usage."""
    with get_db() as db:
        rows = db.execute("SELECT date FROM pbi_usage_days ORDER BY date").fetchall()
    return [r["date"] for r in rows]


@router.post("/pbi-usage-import")
def import_pbi_usage(request: Request, data: dict = fastapi.Body(...)):
    """Import PBI usage data from PS1 script."""
    _require_local(request)
    entries = data.get("entries") or []
    days_synced = data.get("days_synced") or []

    matched = 0
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        # Build name -> id lookup
        all_reports = db.execute("SELECT id, name FROM reports").fetchall()
        name_map = {r["name"].strip().lower(): r["id"] for r in all_reports}

        # Record synced days
        for day in days_synced:
            db.execute(
                "INSERT OR IGNORE INTO pbi_usage_days (date, synced_at) VALUES (?, ?)",
                (day, now),
            )

        # Insert view counts
        for entry in entries:
            report_name = entry.get("report_name", "").strip()
            if not report_name:
                continue
            report_id = name_map.get(report_name.lower())
            db.execute(
                """INSERT INTO pbi_report_views (report_name, report_id, view_date, view_count, unique_users)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(report_name, view_date) DO UPDATE SET
                       view_count = excluded.view_count,
                       unique_users = excluded.unique_users,
                       report_id = COALESCE(excluded.report_id, report_id)""",
                (report_name, report_id, entry.get("date"), entry.get("view_count", 0), entry.get("unique_users", 0)),
            )
            if report_id:
                matched += 1

    return {"status": "completed", "matched": matched, "total_entries": len(entries), "days_synced": len(days_synced)}


@router.post("/pbi-usage-sync")
def do_pbi_usage_sync(request: Request):
    """Launch PBI usage sync in the user's interactive session."""
    _require_local(request)
    return trigger_pbi_usage_sync()
