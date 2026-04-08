from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from app.database import get_db
from app.models import TaskOut, TaskCreate, TaskUpdate, TaskMove, TaskLinkInfo
from app.routers.eventlog import log_event, get_actor

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Maps entity_type to (table, name_column)
ENTITY_TABLES = {
    "report": ("reports", "name"),
    "source": ("sources", "name"),
    "script": ("scripts", "display_name"),
    "upstream_system": ("upstream_systems", "name"),
    "scheduled_task": ("scheduled_tasks", "task_name"),
}


def _get_links(db, task_id: int) -> list[TaskLinkInfo]:
    rows = db.execute(
        "SELECT entity_type, entity_id FROM task_links WHERE task_id = ? ORDER BY created_at",
        (task_id,),
    ).fetchall()
    links = []
    for r in rows:
        etype = r["entity_type"]
        eid = r["entity_id"]
        name = None
        tbl = ENTITY_TABLES.get(etype)
        if tbl:
            nr = db.execute(f"SELECT {tbl[1]} FROM {tbl[0]} WHERE id = ?", (eid,)).fetchone()
            if nr:
                name = nr[0]
        links.append(TaskLinkInfo(entity_type=etype, entity_id=eid, entity_name=name))
    return links


def _sync_links(db, task_id: int, links):
    db.execute("DELETE FROM task_links WHERE task_id = ?", (task_id,))
    for link in links:
        db.execute(
            "INSERT INTO task_links (task_id, entity_type, entity_id) VALUES (?, ?, ?)",
            (task_id, link.entity_type, link.entity_id),
        )


def _row_to_task(r, db) -> TaskOut:
    return TaskOut(
        id=r["id"],
        title=r["title"],
        description=r["description"],
        status=r["status"],
        priority=r["priority"],
        assigned_to=r["assigned_to"],
        due_date=r["due_date"],
        position=r["position"],
        email_owner=bool(r["email_owner"]) if r["email_owner"] else False,
        linked_entities=_get_links(db, r["id"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.get("", response_model=list[TaskOut])
def list_tasks(status: str | None = None, assigned_to: str | None = None):
    with get_db() as db:
        query = "SELECT * FROM tasks"
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if assigned_to:
            conditions.append("assigned_to = ?")
            params.append(assigned_to)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY position, created_at DESC"
        rows = db.execute(query, params).fetchall()
        return [_row_to_task(r, db) for r in rows]


@router.get("/for-entity")
def tasks_for_entity(entity_type: str, entity_id: int):
    """Get all tasks linked to a specific entity."""
    with get_db() as db:
        rows = db.execute(
            """SELECT t.* FROM tasks t
               JOIN task_links tl ON tl.task_id = t.id
               WHERE tl.entity_type = ? AND tl.entity_id = ?
               ORDER BY CASE t.status
                   WHEN 'in_progress' THEN 0 WHEN 'backlog' THEN 1
                   WHEN 'review' THEN 2 WHEN 'done' THEN 3 ELSE 4 END,
               t.created_at DESC""",
            (entity_type, entity_id),
        ).fetchall()
        return [_row_to_task(r, db) for r in rows]


@router.post("", response_model=TaskOut)
def create_task(task: TaskCreate, request: Request):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        # Set position to end of target column
        max_pos = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM tasks WHERE status = ?",
            (task.status,),
        ).fetchone()[0]

        cursor = db.execute(
            """INSERT INTO tasks (title, description, status, priority, assigned_to, due_date, position, email_owner, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.title, task.description, task.status, task.priority,
             task.assigned_to, task.due_date, max_pos, int(task.email_owner), now, now),
        )
        task_id = cursor.lastrowid
        if task.linked_entities:
            _sync_links(db, task_id, task.linked_entities)
        log_event(db, "task", task_id, task.title, "created", actor=get_actor(request))
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row, db)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, update: TaskUpdate, request: Request):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        existing = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        fields = ["updated_at = ?"]
        values = [now]
        data = update.model_dump(exclude_unset=True)
        links = data.pop("linked_entities", None)
        for field_name, value in data.items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        values.append(task_id)
        db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values)

        if links is not None:
            from app.models import TaskLinkRequest
            _sync_links(db, task_id, [TaskLinkRequest(**l) for l in links])

        changed = ", ".join(k for k in data)
        if links is not None:
            changed = (changed + ", linked_entities") if changed else "linked_entities"
        log_event(db, "task", task_id, None, "updated", changed, get_actor(request))
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row, db)


@router.patch("/{task_id}/move", response_model=TaskOut)
def move_task(task_id: int, move: TaskMove):
    """Move a task to a different column and/or position (for drag-and-drop)."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        existing = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        old_status = existing["status"]

        # Shift positions in target column to make room
        db.execute(
            "UPDATE tasks SET position = position + 1 WHERE status = ? AND position >= ? AND id != ?",
            (move.status, move.position, task_id),
        )
        db.execute(
            "UPDATE tasks SET status = ?, position = ?, updated_at = ? WHERE id = ?",
            (move.status, move.position, now, task_id),
        )

        # Close gaps in the old column if the task moved to a different column
        if old_status != move.status:
            old_tasks = db.execute(
                "SELECT id FROM tasks WHERE status = ? ORDER BY position",
                (old_status,),
            ).fetchall()
            for i, t in enumerate(old_tasks):
                db.execute("UPDATE tasks SET position = ? WHERE id = ?", (i, t["id"]))

        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row, db)


@router.delete("/{task_id}")
def delete_task(task_id: int, request: Request):
    with get_db() as db:
        existing = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        log_event(db, "task", task_id, None, "deleted", actor=get_actor(request))
    return {"status": "deleted"}


@router.get("/owners")
def list_task_owners():
    """Return BI people available for task assignment."""
    with get_db() as db:
        rows = db.execute(
            "SELECT name FROM people WHERE role = 'BI' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]


@router.get("/linkable-entities")
def list_linkable_entities():
    """Return all entities available for task linking, grouped by type."""
    with get_db() as db:
        result = {}
        for etype, (table, name_col) in ENTITY_TABLES.items():
            archived_filter = " WHERE archived = 0" if etype != "upstream_system" else ""
            try:
                rows = db.execute(
                    f"SELECT id, {name_col} as name FROM {table}{archived_filter} ORDER BY {name_col}"
                ).fetchall()
            except Exception:
                rows = db.execute(
                    f"SELECT id, {name_col} as name FROM {table} ORDER BY {name_col}"
                ).fetchall()
            result[etype] = [{"id": r["id"], "name": r["name"]} for r in rows]
    return result
