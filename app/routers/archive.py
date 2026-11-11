"""Archive / unarchive entities (sources, reports, upstream systems, documentation)."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from app.archive_ops import archive_source
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.source_identity import active_identity_holders

router = APIRouter(prefix="/api/archive", tags=["archive"])

_TABLES = {
    "source": "sources",
    "report": "reports",
    "upstream": "upstream_systems",
    "documentation": "documentation",
}

_NAME_COL = {
    "source": "name",
    "report": "name",
    "upstream": "name",
    "documentation": "title",
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
        if entity_type == "source" and not new_val:
            # One physical PostgreSQL table maps to one active source. Making
            # a second one active would leave every scan unable to tell them
            # apart, so the operator has to pick which row stays active.
            holders = active_identity_holders(db, entity_id)
            if holders:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f'Cannot restore "{row["ename"]}": the active source '
                        f'"{holders[0]["source_name"]}" (id {holders[0]["source_id"]}) '
                        "already tracks the same PostgreSQL table. "
                        "Archive that source first, then restore this one."
                    ),
                )
        if entity_type == "source" and new_val:
            # Archived sources are no longer probed, so nothing would ever
            # close their open actions/alerts again - resolve them now.
            now = datetime.now(timezone.utc).isoformat()
            archive_source(db, entity_id, now, reason="Source archived")
        else:
            db.execute(f"UPDATE {table} SET archived = ? WHERE id = ?", (new_val, entity_id))
        action = "archived" if new_val else "unarchived"
        log_event(db, entity_type, entity_id, row["ename"], action, actor=get_actor(request))

    return {"status": action, "id": entity_id, "archived": bool(new_val)}
