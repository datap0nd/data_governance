from fastapi import APIRouter
from app.database import get_db
from app.models import DashboardStats, ScanRunOut

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardStats)
def get_dashboard():
    with get_db() as db:
        # Total source count
        sources_total = db.execute("SELECT COUNT(*) AS c FROM sources WHERE archived = 0").fetchone()["c"]

        # Source status counts from latest probes
        probe_statuses = db.execute("""
            SELECT COALESCE(sp.status, 'unknown') AS eff_status, COUNT(*) AS c
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE s.archived = 0
            GROUP BY eff_status
        """).fetchall()

        status_counts = {r["eff_status"]: r["c"] for r in probe_statuses}
        sources_fresh = status_counts.get("fresh", 0)
        sources_stale = 0  # no longer used, always 0
        sources_outdated = status_counts.get("outdated", 0) + status_counts.get("stale", 0) + status_counts.get("error", 0)
        sources_unknown = status_counts.get("unknown", 0) + status_counts.get("no_connection", 0) + status_counts.get("no_rule", 0)

        # Report counts
        reports_total = db.execute("SELECT COUNT(*) AS c FROM reports WHERE archived = 0").fetchone()["c"]

        # Alert count - match what's visible in the dashboard Alerts table.
        # Use the actions table (same source of truth as the UI), filtered to
        # unresolved entries whose source is still outdated (or unknown).
        alerts_active = db.execute(
            """SELECT COUNT(*) AS c FROM actions a
               LEFT JOIN sources s ON s.id = a.source_id
               LEFT JOIN (
                   SELECT source_id, status,
                          ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                   FROM source_probes
               ) sp ON sp.source_id = a.source_id AND sp.rn = 1
               WHERE a.status NOT IN ('resolved', 'expected')
                 AND (s.archived IS NULL OR s.archived = 0)
                 AND (
                     a.source_id IS NULL
                     OR sp.status IS NULL
                     OR sp.status IN ('outdated', 'stale', 'error')
                 )"""
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


@router.get("/impact")
def get_impact_hierarchy():
    """Return stale/outdated sources ranked by how many reports they affect."""
    with get_db() as db:
        rows = db.execute("""
            SELECT s.id AS source_id, s.name AS source_name,
                   sp.status,
                   CAST(sp.last_data_at AS TEXT) AS last_data_at,
                   COUNT(DISTINCT rt.report_id) AS affected_reports
            FROM sources s
            JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            JOIN report_tables rt ON rt.source_id = s.id
            WHERE sp.status IN ('outdated', 'error')
            GROUP BY s.id
            ORDER BY affected_reports DESC, CASE sp.status WHEN 'outdated' THEN 0 WHEN 'error' THEN 0 ELSE 1 END
        """).fetchall()

        # Gather report names per source
        source_ids = [r["source_id"] for r in rows]
        report_map: dict[int, list[str]] = {}
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            rnames = db.execute(f"""
                SELECT rt.source_id, r.name
                FROM report_tables rt
                JOIN reports r ON r.id = rt.report_id
                WHERE rt.source_id IN ({placeholders})
                ORDER BY r.name
            """, source_ids).fetchall()
            for rn in rnames:
                report_map.setdefault(rn["source_id"], []).append(rn["name"])

    return [
        {
            "source_id": r["source_id"],
            "source_name": r["source_name"],
            "status": r["status"],
            "last_data_at": r["last_data_at"],
            "affected_reports": r["affected_reports"],
            "report_names": report_map.get(r["source_id"], []),
        }
        for r in rows
    ]
