"""Schedule discrepancy detection, alert trend, and health trend endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from app.database import get_db
from app.scanner.findings import sync_managed_actions

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

DAY_ORDER = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def _next_weekday_date(day_name: str, with_iso: bool = False) -> str | dict:
    """Compute the next occurrence of the given weekday.

    If *with_iso* is True, return a dict with both the short label and full ISO
    datetime (midnight UTC) for use in the discrepancies API.
    """
    day_num = DAY_ORDER.get(day_name, -1)
    if day_num < 0:
        return {"label": day_name, "iso": None} if with_iso else day_name
    today = datetime.now(timezone.utc).date()
    today_dow = today.weekday()  # Monday=0, Sunday=6
    diff = day_num - today_dow
    if diff < 0:
        diff += 7
    next_date = today + timedelta(days=diff)
    label = next_date.strftime("%d/%m")
    if with_iso:
        iso = datetime(next_date.year, next_date.month, next_date.day, tzinfo=timezone.utc).isoformat()
        return {"label": label, "iso": iso}
    return label


@router.get("/upstream-systems")
def list_upstream_systems(include_archived: bool = Query(False)):
    """List all upstream systems with source counts."""
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE us.archived = 0"
        rows = db.execute(f"""
            SELECT us.*,
                   (SELECT COUNT(*) FROM sources s WHERE s.upstream_id = us.id) AS source_count
            FROM upstream_systems us
            {archive_filter}
            ORDER BY us.name
        """).fetchall()
    return [dict(r) for r in rows]


def run_schedule_discrepancy_scan(*, persist: bool = True):
    """Detect refresh schedule discrepancies in upstream -> source -> report chains.

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
        if upstream_day < 0 or source_day < 0:
            continue

        upstream_info = _next_weekday_date(row["upstream_refresh_day"], with_iso=True)
        source_info = _next_weekday_date(row["source_refresh_day"], with_iso=True)

        # Parse report frequency (e.g. "Weekly - Tuesday" or "Monthly - 15")
        freq = row["report_frequency"] or ""
        is_monthly = freq.startswith("Monthly - ")

        report_day_name = "Sunday"  # default for weekly
        if freq.startswith("Weekly - "):
            parsed_day = freq.replace("Weekly - ", "").strip()
            if parsed_day in DAY_ORDER:
                report_day_name = parsed_day
        report_day = DAY_ORDER.get(report_day_name, 6)

        issues = []

        # Upstream must refresh BEFORE source
        if upstream_day >= source_day:
            issues.append({
                "type": "upstream_after_source",
                "severity": "warning",
                "message": f"Upstream >= source ({row['upstream_refresh_day']} >= {row['source_refresh_day']})",
            })

        # Source must refresh BEFORE report (only applicable for weekly reports)
        if not is_monthly and source_day >= report_day:
            issues.append({
                "type": "source_after_report",
                "severity": "critical",
                "message": f"Source on report day ({row['source_refresh_day']} >= {report_day_name})",
            })

        if issues:
            discrepancies.append({
                "upstream_id": row["upstream_id"],
                "upstream_name": row["upstream_name"],
                "upstream_code": row["upstream_code"],
                "upstream_refresh_day": row["upstream_refresh_day"],
                "upstream_refresh_date": upstream_info["label"],
                "upstream_refresh_iso": upstream_info["iso"],
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "source_refresh_day": row["source_refresh_day"],
                "source_refresh_date": source_info["label"],
                "source_refresh_iso": source_info["iso"],
                "report_id": row["report_id"],
                "report_name": row["report_name"],
                "report_refresh_date": _next_weekday_date(report_day_name, with_iso=True)["label"],
                "report_refresh_iso": _next_weekday_date(report_day_name, with_iso=True)["iso"],
                "issues": issues,
            })

    lifecycle = None
    if persist:
        now = datetime.now(timezone.utc).isoformat()
        grouped: dict[int, list[dict]] = {}
        for discrepancy in discrepancies:
            grouped.setdefault(discrepancy["report_id"], []).append(discrepancy)
        with get_db() as db:
            reports = {
                row["id"]: row
                for row in db.execute("SELECT id, owner FROM reports WHERE COALESCE(archived, 0) = 0").fetchall()
            }
            findings = []
            for report_id, items in grouped.items():
                report = reports.get(report_id)
                if not report:
                    continue
                messages = []
                for item in items:
                    for issue in item["issues"]:
                        messages.append(f"{item['upstream_name']} -> {item['source_name']}: {issue['message']}")
                findings.append({
                    "fingerprint": f"schedule_discrepancy:{report_id}",
                    "report_id": report_id,
                    "assigned_to": report["owner"],
                    "notes": f"{len(messages)} schedule issue(s). " + "; ".join(messages[:10]),
                })
            lifecycle = sync_managed_actions(db, "schedule_discrepancy", findings, now)

    return {
        "summary": {
            "total_chains": len(rows),
            "discrepancy_count": len(discrepancies),
            "critical_count": sum(1 for d in discrepancies if any(i["severity"] == "critical" for i in d["issues"])),
            "warning_count": sum(1 for d in discrepancies if all(i["severity"] == "warning" for i in d["issues"])),
        },
        "discrepancies": discrepancies,
        "actions": lifecycle,
    }


@router.get("/discrepancies")
def get_schedule_discrepancies():
    """Detect and persist refresh schedule discrepancies."""
    return run_schedule_discrepancy_scan(persist=True)


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


@router.get("/health-trend")
def get_health_trend():
    """Return daily source health distribution for the past 30 days.

    Uses probe_runs aggregate counts, carrying forward the last known values
    for days without probes.
    """
    with get_db() as db:
        rows = db.execute("""
            SELECT DATE(started_at) AS day, sources_probed, fresh, stale, outdated,
                   unknown, COALESCE(no_rule, 0) AS no_rule
            FROM probe_runs
            WHERE started_at >= date('now', '-30 days') AND status = 'completed'
            ORDER BY started_at
        """).fetchall()

    # Group by day, take latest per day
    daily = {}
    for r in rows:
        accounted = sum((r[key] or 0) for key in ("fresh", "stale", "outdated", "unknown", "no_rule"))
        if accounted != (r["sources_probed"] or 0):
            continue
        daily[r["day"]] = {
            "healthy": r["fresh"] or 0,
            "degraded": (r["stale"] or 0) + (r["outdated"] or 0),
            "unknown": (r["unknown"] or 0) + (r["no_rule"] or 0),
        }

    today = datetime.now(timezone.utc).date()
    trend = []
    last_known = None
    for i in range(29, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        if day in daily:
            last_known = daily[day]
        if last_known is not None:
            trend.append({"day": day, **last_known})

    return trend
