"""Event log — audit trail for entity changes."""

from fastapi import APIRouter
from app.database import get_db
from app.models import EventLogOut

router = APIRouter(prefix="/api/eventlog", tags=["eventlog"])


def log_event(db, entity_type: str, entity_id: int | None, entity_name: str | None,
              action: str, detail: str | None = None):
    """Insert an event log row. Call within an existing get_db() context."""
    db.execute(
        """INSERT INTO event_log (entity_type, entity_id, entity_name, action, detail)
           VALUES (?, ?, ?, ?, ?)""",
        (entity_type, entity_id, entity_name, action, detail),
    )


@router.get("", response_model=list[EventLogOut])
def list_events(entity_type: str | None = None, action: str | None = None, limit: int = 200):
    """List event log entries, newest first."""
    with get_db() as db:
        query = "SELECT * FROM event_log"
        conditions = []
        params: list = []
        if entity_type:
            conditions.append("entity_type = ?")
            params.append(entity_type)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(query, params).fetchall()

    return [
        EventLogOut(
            id=r["id"],
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            entity_name=r["entity_name"],
            action=r["action"],
            detail=r["detail"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
