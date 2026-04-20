from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import ActionOut, ActionUpdate

router = APIRouter(prefix="/api/actions", tags=["actions"])


def _compute_source_days_outdated(db) -> dict[int, int]:
    """For each source with outdated latest probe, days between now and last_data_at."""
    rows = db.execute("""
        SELECT sp.source_id, sp.status, sp.last_data_at
        FROM source_probes sp
        WHERE sp.id = (
            SELECT sp2.id FROM source_probes sp2
            WHERE sp2.source_id = sp.source_id
            ORDER BY sp2.probed_at DESC LIMIT 1
        )
    """).fetchall()
    result: dict[int, int] = {}
    now = datetime.now(timezone.utc)
    for r in rows:
        status = r["status"]
        last_data = r["last_data_at"]
        if status not in ("outdated", "stale", "error") or not last_data:
            continue
        try:
            dt = datetime.fromisoformat(last_data)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = max(0, (now - dt).days)
        except (ValueError, TypeError):
            days = 0
        result[r["source_id"]] = days
    return result


def _compute_report_context(db, source_days: dict[int, int]):
    """Return (source_reports_map, report_degradation_map).

    source_reports_map: {source_id: [(report_id, report_name), ...]}
    report_degradation_map: {report_id: total_degradation_days}

    Active (non-archived) reports are preferred, but archived reports are
    still included so the Alerts table matches what Sources page shows.
    """
    rows = db.execute("""
        SELECT rt.source_id, rt.report_id, r.name AS report_name,
               COALESCE(r.archived, 0) AS archived
        FROM report_tables rt
        JOIN reports r ON r.id = rt.report_id
        WHERE rt.source_id IS NOT NULL
        ORDER BY archived ASC, r.name ASC
    """).fetchall()

    source_reports: dict[int, list[tuple[int, str]]] = {}
    report_sources: dict[int, list[int]] = {}
    report_names: dict[int, str] = {}
    for r in rows:
        sid = r["source_id"]
        rid = r["report_id"]
        source_reports.setdefault(sid, []).append((rid, r["report_name"]))
        report_sources.setdefault(rid, []).append(sid)
        report_names[rid] = r["report_name"]

    report_degradation: dict[int, int] = {}
    for rid, sids in report_sources.items():
        total = sum(source_days.get(sid, 0) for sid in sids)
        report_degradation[rid] = total

    return source_reports, report_degradation, report_names


def _compute_report_action_days(db) -> dict[int, int]:
    """Days since last successful refresh, keyed by report_id.

    For report-level actions (refresh_failed / refresh_overdue) we want a
    "days since problem started" metric analogous to source_days_outdated.
    Uses pbi_last_refresh_at as the reference point.
    """
    rows = db.execute(
        "SELECT id, pbi_last_refresh_at FROM reports WHERE archived = 0"
    ).fetchall()
    result: dict[int, int] = {}
    now = datetime.now(timezone.utc)
    for r in rows:
        last = r["pbi_last_refresh_at"]
        if not last:
            result[r["id"]] = 0
            continue
        try:
            ts = last.replace("Z", "+00:00") if isinstance(last, str) else last
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            result[r["id"]] = max(0, (now - dt).days)
        except (ValueError, TypeError, AttributeError):
            result[r["id"]] = 0
    return result


@router.get("", response_model=list[ActionOut])
def list_actions(status: str | None = None):
    with get_db() as db:
        # Each action is about one asset - a source, report, scheduled task,
        # or script. For source-tied actions we also look up the latest probe
        # so we can skip actions on sources that are no longer outdated
        # (stale rows from before a freshness rule change will get auto-
        # resolved on next probe but we don't want them cluttering the UI
        # in the meantime).
        query = """
            SELECT a.*, s.name AS source_name, s.archived AS source_archived,
                   r.name AS report_name, r.archived AS report_archived,
                   st.task_name AS task_name, st.archived AS task_archived,
                   sc.display_name AS script_name, sc.archived AS script_archived,
                   sp.status AS latest_source_status
            FROM actions a
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN reports r ON r.id = a.report_id
            LEFT JOIN scheduled_tasks st ON st.id = a.scheduled_task_id
            LEFT JOIN scripts sc ON sc.id = a.script_id
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = a.source_id AND sp.rn = 1
        """
        params = []
        if status:
            query += " WHERE a.status = ?"
            params.append(status)
        query += " ORDER BY a.created_at DESC"
        rows = db.execute(query, params).fetchall()

        source_days = _compute_source_days_outdated(db)
        source_reports, report_degradation, _ = _compute_report_context(db, source_days)
        report_days = _compute_report_action_days(db)

    ACTIONABLE_STATUSES = {"outdated", "stale", "error"}

    results: list[ActionOut] = []
    for r in rows:
        sid = r["source_id"]
        rid = r["report_id"]
        tid = r["scheduled_task_id"] if "scheduled_task_id" in r.keys() else None
        scid = r["script_id"] if "script_id" in r.keys() else None
        latest = r["latest_source_status"]

        # Source-tied: hide if the source is archived or no longer outdated
        if sid is not None:
            if r["source_archived"]:
                continue
            if latest is not None and latest not in ACTIONABLE_STATUSES:
                continue
        # Report-tied: hide if the report is archived
        if rid is not None and sid is None and r["report_archived"]:
            continue
        # Task-tied: hide if task archived
        if tid is not None and r["task_archived"]:
            continue
        # Script-tied: hide if script archived
        if scid is not None and r["script_archived"]:
            continue

        # Determine asset identity for this action - priority order mirrors
        # how the action was created: source > report > task > script
        if sid is not None:
            asset_type = "source"
            asset_id = sid
            asset_name = r["source_name"]
            asset_days = source_days.get(sid, 0)
        elif rid is not None:
            asset_type = "report"
            asset_id = rid
            asset_name = r["report_name"]
            asset_days = report_days.get(rid, 0)
        elif tid is not None:
            asset_type = "scheduled_task"
            asset_id = tid
            asset_name = r["task_name"]
            asset_days = 0  # no meaningful day count for task failures yet
        elif scid is not None:
            asset_type = "script"
            asset_id = scid
            asset_name = r["script_name"]
            asset_days = 0
        else:
            asset_type = None
            asset_id = None
            asset_name = None
            asset_days = 0

        # For source-tied alerts, surface the top affected report
        linked = source_reports.get(sid, []) if sid else []
        names = [rn for _, rn in linked]
        top_rid, top_rname, top_days = None, None, 0
        for lrid, lrname in linked:
            d = report_degradation.get(lrid, 0)
            if d >= top_days:
                top_rid, top_rname, top_days = lrid, lrname, d

        results.append(ActionOut(
            id=r["id"],
            source_id=sid,
            source_name=r["source_name"],
            report_id=rid,
            report_name=r["report_name"],
            report_names=names,
            top_report_id=top_rid,
            top_report_name=top_rname,
            top_report_degradation_days=top_days,
            source_days_outdated=source_days.get(sid, 0) if sid else 0,
            asset_type=asset_type,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_days=asset_days,
            type=r["type"],
            status=r["status"],
            assigned_to=r["assigned_to"],
            notes=r["notes"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            resolved_at=r["resolved_at"],
        ))

    # Sort: open first; by asset_days DESC (most urgent first); then created_at DESC
    def sort_key(a: ActionOut):
        is_closed = a.status in ("resolved", "expected")
        return (
            1 if is_closed else 0,
            -max(a.asset_days, a.top_report_degradation_days),
            -(datetime.fromisoformat(a.created_at).timestamp() if a.created_at else 0),
        )
    results.sort(key=sort_key)

    # Dedupe: one row per asset (source or report)
    seen: set[tuple[str, int]] = set()
    deduped: list[ActionOut] = []
    for a in results:
        if a.asset_type is None or a.asset_id is None:
            deduped.append(a)
            continue
        key = (a.asset_type, a.asset_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    return deduped


@router.patch("/{action_id}", response_model=ActionOut)
def update_action(action_id: int, update: ActionUpdate, request: Request):
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        existing = db.execute("SELECT id FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Action not found")

        fields = ["updated_at = ?"]
        values = [now]

        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        # Auto-set resolved_at when status becomes resolved or expected
        if update.status in ("resolved", "expected"):
            fields.append("resolved_at = ?")
            values.append(now)

        values.append(action_id)
        db.execute(
            f"UPDATE actions SET {', '.join(fields)} WHERE id = ?",
            values,
        )

        changed = ", ".join(k for k in update.model_dump(exclude_unset=True))
        log_event(db, "action", action_id, None, "updated", changed, get_actor(request))

        r = db.execute("""
            SELECT a.*, s.name AS source_name, r.name AS report_name
            FROM actions a
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN reports r ON r.id = a.report_id
            WHERE a.id = ?
        """, (action_id,)).fetchone()

    return ActionOut(
        id=r["id"],
        source_id=r["source_id"],
        source_name=r["source_name"],
        report_id=r["report_id"],
        report_name=r["report_name"],
        type=r["type"],
        status=r["status"],
        assigned_to=r["assigned_to"],
        notes=r["notes"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        resolved_at=r["resolved_at"],
    )
