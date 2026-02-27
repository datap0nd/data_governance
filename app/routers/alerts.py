from fastapi import APIRouter
from app.database import get_db
from app.models import AlertOut

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(active_only: bool = True):
    """List alerts, optionally only unacknowledged ones."""
    with get_db() as db:
        query = """
            SELECT a.*, s.name AS source_name
            FROM alerts a
            LEFT JOIN sources s ON s.id = a.source_id
        """
        if active_only:
            query += " WHERE a.acknowledged = 0"
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
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, by: str = "user"):
    with get_db() as db:
        db.execute(
            "UPDATE alerts SET acknowledged = 1, acknowledged_by = ? WHERE id = ?",
            (by, alert_id),
        )
    return {"status": "acknowledged"}
