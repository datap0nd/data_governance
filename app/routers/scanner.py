import logging

from fastapi import APIRouter, HTTPException, Request
from app.config import TMDL_ROOT
from app.database import get_db
from app.scanner.runner import run_scan
from app.scanner.prober import run_probe
from app.scanner.pbi_sync import trigger_pbi_sync, import_pbi_data
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
    # After probe, launch PBI sync in user's session
    try:
        pbi_result = trigger_pbi_sync()
        result["pbi_sync"] = pbi_result
    except Exception as e:
        logger.exception("PBI sync failed after scan")
        result["pbi_sync"] = {"status": "failed", "error": str(e)}
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
