from fastapi import APIRouter
from app.database import get_db
from app.scanner.runner import run_scan
from app.scanner.prober import run_probe
from app.models import ScanRunOut

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.post("/run")
def trigger_scan():
    """Trigger a full scan (reads .pbix files or TMDL exports)."""
    result = run_scan()
    # After scan, probe PostgreSQL sources for last-updated timestamps
    try:
        run_probe()
    except Exception:
        pass  # probe is best-effort; don't fail the scan
    return result


@router.post("/probe")
def trigger_probe():
    """Probe PostgreSQL sources for last-updated timestamps."""
    result = run_probe()
    return result


@router.get("/runs", response_model=list[ScanRunOut])
def list_scan_runs():
    """List all scan runs, most recent first."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    return [ScanRunOut(**dict(r)) for r in rows]


@router.get("/runs/{run_id}", response_model=ScanRunOut)
def get_scan_run(run_id: int):
    with get_db() as db:
        r = db.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return {"error": "Scan run not found"}
    return ScanRunOut(**dict(r))
