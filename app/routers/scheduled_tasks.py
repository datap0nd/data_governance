"""Scheduled Tasks API - scan and list Windows Task Scheduler entries."""

from fastapi import APIRouter, HTTPException, Query, Request
from app.database import get_db
from app.models import ScheduledTaskOut
from app.scanner.task_scheduler_runner import run_task_scheduler_scan

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])


def _build_task_out(row) -> ScheduledTaskOut:
    keys = row.keys()
    return ScheduledTaskOut(
        id=row["id"],
        task_name=row["task_name"],
        task_path=row["task_path"],
        status=row["status"],
        last_run_time=row["last_run_time"],
        last_result=row["last_result"],
        next_run_time=row["next_run_time"],
        author=row["author"],
        run_as_user=row["run_as_user"],
        action_command=row["action_command"],
        action_args=row["action_args"],
        schedule_type=row["schedule_type"],
        enabled=bool(row["enabled"]),
        script_id=row["script_id"],
        script_name=row["script_name"] if "script_name" in keys else None,
        hostname=row["hostname"] if "hostname" in keys else None,
        machine_alias=row["machine_alias"] if "machine_alias" in keys else None,
        archived=bool(row["archived"]),
        last_scanned=row["last_scanned"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[ScheduledTaskOut])
def list_scheduled_tasks(include_archived: bool = Query(False)):
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE st.archived = 0"
        rows = db.execute(f"""
            SELECT st.*, s.display_name AS script_name
            FROM scheduled_tasks st
            LEFT JOIN scripts s ON s.id = st.script_id
            {archive_filter}
            ORDER BY st.task_name
        """).fetchall()
    return [_build_task_out(r) for r in rows]


@router.get("/{task_id}", response_model=ScheduledTaskOut)
def get_scheduled_task(task_id: int):
    with get_db() as db:
        row = db.execute("""
            SELECT st.*, s.display_name AS script_name
            FROM scheduled_tasks st
            LEFT JOIN scripts s ON s.id = st.script_id
            WHERE st.id = ?
        """, (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return _build_task_out(row)


@router.post("/scan")
def trigger_task_scheduler_scan(request: Request, new_only: bool = Query(False)):
    """Trigger a scan of Windows Task Scheduler."""
    from app.routers.scanner import _require_local
    _require_local(request)
    return run_task_scheduler_scan(new_only=new_only)
