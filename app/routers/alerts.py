from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from app.database import get_db
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
            resolution_status=r["resolution_status"],
            resolution_reason=r["resolution_reason"],
            resolved_at=r["resolved_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: AlertResolveRequest):
    """Acknowledge or resolve an alert with an optional reason."""
    if body.status not in ("acknowledged", "resolved"):
        raise HTTPException(status_code=400, detail="status must be 'acknowledged' or 'resolved'")
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        db.execute(
            """UPDATE alerts
               SET resolution_status = ?, resolution_reason = ?, resolved_at = ?,
                   acknowledged = 1, acknowledged_by = 'user'
               WHERE id = ?""",
            (body.status, body.reason, now, alert_id),
        )
    return {"status": body.status}


@router.post("/{alert_id}/reopen")
def reopen_alert(alert_id: int):
    """Reopen a previously acknowledged/resolved alert."""
    with get_db() as db:
        db.execute(
            """UPDATE alerts
               SET resolution_status = NULL, resolution_reason = NULL, resolved_at = NULL,
                   acknowledged = 0, acknowledged_by = NULL
               WHERE id = ?""",
            (alert_id,),
        )
    return {"status": "active"}


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, by: str = "user"):
    """Legacy acknowledge endpoint — kept for backward compatibility."""
    with get_db() as db:
        db.execute(
            """UPDATE alerts
               SET acknowledged = 1, acknowledged_by = ?,
                   resolution_status = 'acknowledged', resolved_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (by, alert_id),
        )
    return {"status": "acknowledged"}
