from fastapi import APIRouter
from app.database import get_db
from app.models import DashboardStats, ScanRunOut
from app.routers.actions import list_actions
from app.routers.sources import list_sources

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardStats)
def get_dashboard():
    # Stats are derived from the same endpoints that power the Sources and
    # Alerts pages so the numbers on the cards always match what the user
    # sees when they click through. A tiny bit of redundant work, but zero
    # drift risk.
    visible_sources = list_sources(include_archived=False)
    visible_actions = list_actions(status=None)

    sources_total = len(visible_sources)
    sources_fresh = sum(1 for s in visible_sources if s.status == "fresh")
    sources_outdated = sum(
        1 for s in visible_sources if s.status in ("outdated", "stale", "error")
    )
    sources_unknown = sources_total - sources_fresh - sources_outdated

    # Active = anything that isn't resolved or expected (same filter as
    # the Alerts table; list_actions already applied the visibility rules)
    alerts_active = sum(
        1 for a in visible_actions if a.status not in ("resolved", "expected")
    )

    with get_db() as db:
        reports_total = db.execute(
            "SELECT COUNT(*) AS c FROM reports WHERE archived = 0"
        ).fetchone()["c"]
        last_scan_row = db.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        last_scan = ScanRunOut(**dict(last_scan_row)) if last_scan_row else None

    return DashboardStats(
        sources_total=sources_total,
        sources_fresh=sources_fresh,
        sources_stale=0,  # legacy field, always 0
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
