"""Archive / unarchive entities (sources, reports, scripts, upstream systems, scheduled tasks)."""

from fastapi import APIRouter, HTTPException, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor

router = APIRouter(prefix="/api/archive", tags=["archive"])

_TABLES = {
    "source": "sources",
    "report": "reports",
    "script": "scripts",
    "upstream": "upstream_systems",
    "scheduled_task": "scheduled_tasks",
}

_NAME_COL = {
    "source": "name",
    "report": "name",
    "script": "display_name",
    "upstream": "name",
    "scheduled_task": "task_name",
}


@router.post("/{entity_type}/{entity_id}")
def toggle_archive(entity_type: str, entity_id: int, request: Request):
    """Toggle the archived flag for an entity."""
    table = _TABLES.get(entity_type)
    if not table:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")

    with get_db() as db:
        row = db.execute(f"SELECT id, archived, {_NAME_COL[entity_type]} AS ename FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entity not found")

        new_val = 0 if row["archived"] else 1
        db.execute(f"UPDATE {table} SET archived = ? WHERE id = ?", (new_val, entity_id))
        action = "archived" if new_val else "unarchived"
        log_event(db, entity_type, entity_id, row["ename"], action, actor=get_actor(request))

    return {"status": action, "id": entity_id, "archived": bool(new_val)}
