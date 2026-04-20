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
    """
    rows = db.execute("""
        SELECT rt.source_id, rt.report_id, r.name AS report_name
        FROM report_tables rt
        JOIN reports r ON r.id = rt.report_id
        WHERE COALESCE(r.archived, 0) = 0 AND rt.source_id IS NOT NULL
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


@router.get("", response_model=list[ActionOut])
def list_actions(status: str | None = None):
    with get_db() as db:
        # Hide actions tied to archived sources - the source has been retired
        # so the alert isn't actionable any more. Also hide actions where the
        # source's latest probe is no longer outdated (these are stale from
        # before a rule change / data refresh; the prober will auto-close
        # them on its next run, but don't clutter the UI in the meantime).
        query = """
            SELECT a.*, s.name AS source_name, r.name AS report_name,
                   sp.status AS latest_source_status
            FROM actions a
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN reports r ON r.id = a.report_id
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = a.source_id AND sp.rn = 1
            WHERE (s.archived IS NULL OR s.archived = 0)
        """
        params = []
        if status:
            query += " AND a.status = ?"
            params.append(status)
        query += " ORDER BY a.created_at DESC"
        rows = db.execute(query, params).fetchall()

        source_days = _compute_source_days_outdated(db)
        source_reports, report_degradation, _ = _compute_report_context(db, source_days)

    # Only include actions that are either:
    #   a) not tied to a source (broken_ref, changed_query etc.), or
    #   b) tied to a source whose latest probe is still outdated/stale/error,
    #      OR has never been probed (status NULL) so we don't know yet
    # This eliminates the 0d noise for sources that have flipped back to fresh
    # or no_rule but whose old alerts haven't been auto-closed yet.
    ACTIONABLE_STATUSES = {"outdated", "stale", "error"}

    results: list[ActionOut] = []
    for r in rows:
        sid = r["source_id"]
        latest = r["latest_source_status"]
        # Skip source-tied actions whose source isn't currently outdated.
        # Keep actions with no source_id (source-independent issues).
        # Keep actions whose source has never been probed (latest is None) -
        # we can't tell yet if it's degraded.
        if sid is not None and latest is not None and latest not in ACTIONABLE_STATUSES:
            continue
        linked = source_reports.get(sid, []) if sid else []
        names = [rn for _, rn in linked]
        # Pick the report with the highest degradation_days as the "top" report
        top_rid, top_rname, top_days = None, None, 0
        for rid, rname in linked:
            d = report_degradation.get(rid, 0)
            if d >= top_days:
                top_rid, top_rname, top_days = rid, rname, d

        results.append(ActionOut(
            id=r["id"],
            source_id=r["source_id"],
            source_name=r["source_name"],
            report_id=r["report_id"],
            report_name=r["report_name"],
            report_names=names,
            top_report_id=top_rid,
            top_report_name=top_rname,
            top_report_degradation_days=top_days,
            source_days_outdated=source_days.get(sid, 0) if sid else 0,
            type=r["type"],
            status=r["status"],
            assigned_to=r["assigned_to"],
            notes=r["notes"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            resolved_at=r["resolved_at"],
        ))

    # Sort: open/unresolved first; within that by top_report_degradation_days DESC,
    # then source_days_outdated DESC, then created_at DESC
    def sort_key(a: ActionOut):
        is_closed = a.status in ("resolved", "expected")
        return (
            1 if is_closed else 0,
            -a.top_report_degradation_days,
            -a.source_days_outdated,
            -(datetime.fromisoformat(a.created_at).timestamp() if a.created_at else 0),
        )
    results.sort(key=sort_key)

    # Dedupe by source_id so each source shows one row even if the DB
    # accidentally holds multiple open actions for it (happens when old
    # action rows get merged via UPDATE ... SET source_id during source
    # renames). We keep the first one in sort order (highest priority).
    seen_source_ids: set[int] = set()
    deduped: list[ActionOut] = []
    for a in results:
        if a.source_id is None:
            deduped.append(a)
            continue
        if a.source_id in seen_source_ids:
            continue
        seen_source_ids.add(a.source_id)
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
