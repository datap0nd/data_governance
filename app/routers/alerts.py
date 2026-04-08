from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import AlertOut, AlertResolveRequest

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(active_only: bool = True):
    """List alerts, optionally only active (unresolved) ones."""
    with get_db() as db:
        query = """
            SELECT a.*, s.name AS source_name
            FROM alerts a
            LEFT JOIN sources s ON s.id = a.source_id
        """
        if active_only:
            query += " WHERE a.resolution_status IS NULL AND a.acknowledged = 0"
        query += " ORDER BY a.created_at DESC LIMIT 100"

        rows = db.execute(query).fetchall()

    return [
        AlertOut(
            id=r["id"],
            source_id=r["source_id"],
            source_name=r["source_name"],
            severity=r["severity"],
            message=r["message"],
            acknowledged=bool(r["acknowledged"]),
            acknowledged_by=r["acknowledged_by"],
            assigned_to=r["assigned_to"],
            resolution_status=r["resolution_status"],
            resolution_reason=r["resolution_reason"],
            resolved_at=r["resolved_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: AlertResolveRequest, request: Request):
    """Acknowledge or resolve an alert with an optional reason."""
    if body.status not in ("acknowledged", "resolved"):
        raise HTTPException(status_code=400, detail="status must be 'acknowledged' or 'resolved'")
    actor = get_actor(request)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute(
            """UPDATE alerts
               SET resolution_status = ?, resolution_reason = ?, resolved_at = ?,
                   acknowledged = 1, acknowledged_by = ?
               WHERE id = ?""",
            (body.status, body.reason, now, actor or "user", alert_id),
        )
        log_event(db, "alert", alert_id, None, body.status, body.reason, actor)
    return {"status": body.status}


@router.post("/{alert_id}/reopen")
def reopen_alert(alert_id: int, request: Request):
    """Reopen a previously acknowledged/resolved alert."""
    actor = get_actor(request)
    with get_db() as db:
        db.execute(
            """UPDATE alerts
               SET resolution_status = NULL, resolution_reason = NULL, resolved_at = NULL,
                   acknowledged = 0, acknowledged_by = NULL
               WHERE id = ?""",
            (alert_id,),
        )
        log_event(db, "alert", alert_id, None, "reopened", None, actor)
    return {"status": "active"}


@router.patch("/{alert_id}/assign")
def assign_alert(alert_id: int, request: Request, owner: str | None = None):
    """Assign an owner to an alert."""
    with get_db() as db:
        db.execute("UPDATE alerts SET assigned_to = ? WHERE id = ?", (owner, alert_id))
        log_event(db, "alert", alert_id, None, "assigned", f"to {owner}", get_actor(request))
    return {"status": "assigned", "assigned_to": owner}


@router.get("/owners/list")
def list_alert_owners():
    """Return distinct owners available for alert assignment."""
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT owner FROM (
                SELECT DISTINCT owner FROM reports WHERE owner IS NOT NULL AND owner != ''
                UNION
                SELECT DISTINCT business_owner AS owner FROM reports WHERE business_owner IS NOT NULL AND business_owner != ''
            ) ORDER BY owner
        """).fetchall()
    return [r["owner"] for r in rows]


