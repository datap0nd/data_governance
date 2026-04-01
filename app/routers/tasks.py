from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import TaskOut, TaskCreate, TaskUpdate, TaskMove
from app.routers.eventlog import log_event

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _row_to_task(r) -> TaskOut:
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
    return [_row_to_task(r) for r in rows]


@router.post("", response_model=TaskOut)
def create_task(task: TaskCreate):
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
        log_event(db, "task", cursor.lastrowid, task.title, "created")
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_task(row)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, update: TaskUpdate):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        existing = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        fields = ["updated_at = ?"]
        values = [now]
        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        values.append(task_id)
        db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values)
        changed = ", ".join(k for k in update.model_dump(exclude_unset=True))
        log_event(db, "task", task_id, None, "updated", changed)
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row)


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
    return _row_to_task(row)


@router.delete("/{task_id}")
def delete_task(task_id: int):
    with get_db() as db:
        existing = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        log_event(db, "task", task_id, None, "deleted")
    return {"status": "deleted"}


@router.get("/owners")
def list_task_owners():
    """Return BI people available for task assignment."""
    with get_db() as db:
        rows = db.execute(
            "SELECT name FROM people WHERE role = 'BI' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
