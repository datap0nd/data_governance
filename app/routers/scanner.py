import logging

from fastapi import APIRouter
from app.config import TMDL_ROOT
from app.database import get_db
from app.scanner.runner import run_scan
from app.scanner.prober import run_probe, probe_debug
from app.scanner.pbi_sync import run_pbi_sync
from app.scanner.walker import diagnose_reports_root
from app.models import ScanRunOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.post("/run")
def trigger_scan():
    """Trigger a full scan (reads .pbix files or TMDL exports)."""
    result = run_scan()
    # After scan, probe sources for freshness
    try:
        probe_result = run_probe()
        result["probe"] = probe_result
    except Exception as e:
        logger.exception("Probe failed after scan")
        result["probe"] = {"status": "failed", "error": str(e)}
    # After probe, sync PBI refresh schedules
    try:
        pbi_result = run_pbi_sync()
        result["pbi_sync"] = pbi_result
    except Exception as e:
        logger.exception("PBI sync failed after scan")
        result["pbi_sync"] = {"status": "failed", "error": str(e)}
    return result


@router.post("/probe")
def trigger_probe():
    """Probe all sources for freshness (file mod times, PostgreSQL CSV, etc.)."""
    return run_probe()


@router.get("/probe/debug")
def probe_diagnostics():
    """Show CSV samples and PostgreSQL source names side-by-side for debugging."""
    return probe_debug()


@router.get("/probe/runs")
def list_probe_runs():
    """List all probe runs, most recent first."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM probe_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/pbi-sync")
def trigger_pbi_sync():
    """Sync Power BI refresh schedules and status from PBI Service."""
    return run_pbi_sync()


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
    """Step-by-step diagnostics of the scanner discovery logic.

    Shows the resolved path, directory listing, what .pbix/.tmdl files
    were found, and why each subfolder was accepted or skipped.
    """
    return diagnose_reports_root(TMDL_ROOT)


@router.get("/runs/{run_id}", response_model=ScanRunOut)
def get_scan_run(run_id: int):
    with get_db() as db:
        r = db.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return {"error": "Scan run not found"}
    return ScanRunOut(**dict(r))
