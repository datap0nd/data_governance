"""Schedule discrepancy detection and alert trend endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from app.database import get_db

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

DAY_ORDER = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


@router.get("/upstream-systems")
def list_upstream_systems():
    """List all upstream systems with source counts."""
    with get_db() as db:
        rows = db.execute("""
            SELECT us.*,
                   (SELECT COUNT(*) FROM sources s WHERE s.upstream_id = us.id) AS source_count
            FROM upstream_systems us
            ORDER BY us.name
        """).fetchall()
    return [dict(r) for r in rows]


@router.get("/discrepancies")
def get_schedule_discrepancies():
    """Detect refresh schedule discrepancies in upstream → source → report chains.

    A discrepancy occurs when:
    - upstream refreshes on same day or after its dependent source (source pulls stale data)
    - source refreshes on Sunday (same day as report, no time gap)
    """
    with get_db() as db:
        rows = db.execute("""
            SELECT
                us.id AS upstream_id,
                us.name AS upstream_name,
                us.code AS upstream_code,
                us.refresh_day AS upstream_refresh_day,
                s.id AS source_id,
                s.name AS source_name,
                s.refresh_schedule AS source_refresh_day,
                r.id AS report_id,
                r.name AS report_name,
                r.frequency AS report_frequency
            FROM sources s
            JOIN upstream_systems us ON us.id = s.upstream_id
            JOIN report_tables rt ON rt.source_id = s.id
            JOIN reports r ON r.id = rt.report_id
            WHERE s.refresh_schedule IS NOT NULL
              AND us.refresh_day IS NOT NULL
            GROUP BY us.id, s.id, r.id
            ORDER BY s.name, r.name
        """).fetchall()

    discrepancies = []
    for row in rows:
        upstream_day = DAY_ORDER.get(row["upstream_refresh_day"], -1)
        source_day = DAY_ORDER.get(row["source_refresh_day"], -1)

        issues = []

        # Upstream must refresh BEFORE source
        if upstream_day >= source_day:
            issues.append({
                "type": "upstream_after_source",
                "severity": "warning",
                "message": (
                    f"Upstream refreshes {row['upstream_refresh_day']} but source "
                    f"refreshes {row['source_refresh_day']} — source may pull stale upstream data"
                ),
            })

        # Source must refresh BEFORE report (Sunday = 6)
        if source_day >= 6:
            issues.append({
                "type": "source_after_report",
                "severity": "critical",
                "message": (
                    f"Source refreshes {row['source_refresh_day']} (same day as report) "
                    f"— report may use previous week's data"
                ),
            })

        if issues:
            discrepancies.append({
                "upstream_id": row["upstream_id"],
                "upstream_name": row["upstream_name"],
                "upstream_code": row["upstream_code"],
                "upstream_refresh_day": row["upstream_refresh_day"],
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "source_refresh_day": row["source_refresh_day"],
                "report_id": row["report_id"],
                "report_name": row["report_name"],
                "issues": issues,
            })

    return {
        "summary": {
            "total_chains": len(rows),
            "discrepancy_count": len(discrepancies),
            "critical_count": sum(1 for d in discrepancies if any(i["severity"] == "critical" for i in d["issues"])),
            "warning_count": sum(1 for d in discrepancies if all(i["severity"] == "warning" for i in d["issues"])),
        },
        "discrepancies": discrepancies,
    }


@router.get("/alert-trend")
def get_alert_trend():
    """Return daily alert counts for the past 30 days."""
    with get_db() as db:
        rows = db.execute("""
            SELECT DATE(created_at) AS day, COUNT(*) AS count
            FROM alerts
            WHERE created_at >= date('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY day
        """).fetchall()

    counts = {r["day"]: r["count"] for r in rows}

    # Fill in zero-count days for a continuous 30-day series
    today = datetime.now(timezone.utc).date()
    trend = []
    for i in range(29, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        trend.append({"day": day, "count": counts.get(day, 0)})

    return trend
