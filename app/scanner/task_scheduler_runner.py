"""
Task Scheduler runner - orchestrates scanning and database storage.

1. Calls scan_scheduled_tasks() from task_scheduler_scanner
2. Upserts into scheduled_tasks table
3. Matches action command/args against scripts table paths
4. Returns summary dict
"""

import logging
from datetime import datetime, timezone
from pathlib import PureWindowsPath

from app.database import get_db
from app.scanner.task_scheduler_scanner import scan_scheduled_tasks

logger = logging.getLogger(__name__)


def _match_script(db, action_command: str | None, action_args: str | None) -> int | None:
    """Try to match the task's action against scripts in the DB.

    Strategy:
    1. Check action_args for .py file paths
    2. Check action_command if it ends in .py
    3. Normalize paths and compare against scripts.path
    """
    candidates = []
    if action_args:
        for token in action_args.split():
            token = token.strip('"').strip("'")
            if token.lower().endswith(".py"):
                candidates.append(token)
    if action_command and action_command.lower().endswith(".py"):
        candidates.append(action_command)

    if not candidates:
        return None

    rows = db.execute("SELECT id, path FROM scripts").fetchall()
    for candidate in candidates:
        cand_name = PureWindowsPath(candidate).name.lower()
        cand_full = candidate.replace("\\", "/").lower()

        for row in rows:
            script_path = (row["path"] or "").replace("\\", "/").lower()
            script_name = PureWindowsPath(row["path"]).name.lower() if row["path"] else ""

            # Exact path match (normalized)
            if script_path and script_path == cand_full:
                return row["id"]
            # Filename match
            if script_name and script_name == cand_name:
                return row["id"]

    return None


def run_task_scheduler_scan() -> dict:
    """Run a full Task Scheduler scan and store results."""
    now = datetime.now(timezone.utc).isoformat()

    try:
        results = scan_scheduled_tasks()

        if not results:
            return {
                "status": "completed",
                "message": "No tasks found (not on Windows or schtasks returned empty)",
                "tasks_found": 0,
                "tasks_updated": 0,
                "scripts_linked": 0,
            }

        tasks_new = 0
        tasks_updated = 0
        scripts_linked = 0

        with get_db() as db:
            for task in results:
                existing = db.execute(
                    "SELECT id FROM scheduled_tasks WHERE task_name = ?",
                    (task.task_name,),
                ).fetchone()

                script_id = _match_script(db, task.action_command, task.action_args)
                if script_id:
                    scripts_linked += 1

                if existing:
                    db.execute(
                        """UPDATE scheduled_tasks
                           SET task_path = ?, status = ?, last_run_time = ?,
                               last_result = ?, next_run_time = ?, author = ?,
                               run_as_user = ?, action_command = ?, action_args = ?,
                               schedule_type = ?, enabled = ?, script_id = ?,
                               last_scanned = ?, updated_at = ?
                           WHERE id = ?""",
                        (task.task_path, task.status, task.last_run_time,
                         task.last_result, task.next_run_time, task.author,
                         task.run_as_user, task.action_command, task.action_args,
                         task.schedule_type, int(task.enabled), script_id,
                         now, now, existing["id"]),
                    )
                    tasks_updated += 1
                else:
                    db.execute(
                        """INSERT INTO scheduled_tasks
                           (task_name, task_path, status, last_run_time,
                            last_result, next_run_time, author, run_as_user,
                            action_command, action_args, schedule_type, enabled,
                            script_id, last_scanned, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (task.task_name, task.task_path, task.status,
                         task.last_run_time, task.last_result, task.next_run_time,
                         task.author, task.run_as_user, task.action_command,
                         task.action_args, task.schedule_type, int(task.enabled),
                         script_id, now, now, now),
                    )
                    tasks_new += 1

        summary = {
            "status": "completed",
            "tasks_new": tasks_new,
            "tasks_updated": tasks_updated,
            "tasks_total": len(results),
            "scripts_linked": scripts_linked,
        }
        logger.info("Task Scheduler scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("Task Scheduler scan failed")
        return {"status": "failed", "error": str(e)}
