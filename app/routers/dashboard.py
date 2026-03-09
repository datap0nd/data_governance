from fastapi import APIRouter
from app.database import get_db
from app.models import DashboardStats, ScanRunOut

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardStats)
def get_dashboard():
    with get_db() as db:
        # Total source count
        sources_total = db.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]

        # Source status counts from latest probes
        probe_statuses = db.execute("""
            SELECT COALESCE(sp.status, 'unknown') AS eff_status, COUNT(*) AS c
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            GROUP BY eff_status
        """).fetchall()

        status_counts = {r["eff_status"]: r["c"] for r in probe_statuses}
        sources_fresh = status_counts.get("fresh", 0)
        sources_stale = status_counts.get("stale", 0)
        sources_outdated = status_counts.get("outdated", 0) + status_counts.get("error", 0)
        sources_unknown = status_counts.get("unknown", 0) + status_counts.get("no_connection", 0)

        # Report counts
        reports_total = db.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"]

        # Alert count
        alerts_active = db.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE acknowledged = 0"
        ).fetchone()["c"]

        # Last scan
        last_scan_row = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        last_scan = ScanRunOut(**dict(last_scan_row)) if last_scan_row else None

    return DashboardStats(
        sources_total=sources_total,
        sources_fresh=sources_fresh,
        sources_stale=sources_stale,
        sources_outdated=sources_outdated,
        sources_unknown=sources_unknown,
        reports_total=reports_total,
        alerts_active=alerts_active,
        last_scan=last_scan,
    )
