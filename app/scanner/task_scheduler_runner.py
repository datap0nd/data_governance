"""
Task Scheduler runner - orchestrates scanning and database storage.

1. Calls scan_scheduled_tasks() from task_scheduler_scanner
2. Upserts into scheduled_tasks table
3. Matches action command/args against scripts table paths
4. Generates task_failed / script_failed actions for surfaced alerts
5. Returns summary dict
"""

import logging
from datetime import datetime, timezone
from pathlib import PureWindowsPath

from app.database import get_db
from app.scanner.task_scheduler_scanner import scan_scheduled_tasks

logger = logging.getLogger(__name__)


# Windows Task Scheduler last_result codes that are not failures. Anything
# outside this set that isn't "success" gets treated as a failure.
_TASK_SUCCESS_CODES = {"0", "0x0", "0x00000000"}
_TASK_BENIGN_CODES = {
    "267009", "0x41301",   # currently running
    "267011", "0x41303",   # not yet run / ready
    "267045", "0x41325",   # scheduled
    "1057",                # service cannot be started
    "",                    # never populated
}


def _is_task_failed(last_result: str | None) -> bool:
    """Return True if the last_result indicates the task errored out."""
    if last_result is None:
        return False
    code = last_result.strip()
    if not code:
        return False
    if code in _TASK_SUCCESS_CODES:
        return False
    if code in _TASK_BENIGN_CODES:
        return False
    # Normalize 0x-prefix hex - if it reduces to 0, it's success
    try:
        val = int(code, 0) if code.startswith(("0x", "0X")) else int(code)
        if val == 0:
            return False
    except (ValueError, TypeError):
        pass
    return True


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


def run_task_scheduler_scan(new_only: bool = False) -> dict:
    """Run a Task Scheduler scan and store results.

    If *new_only* is True, skip tasks already in the DB.
    """
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
                # Use task_name + hostname as unique key (same name can exist on different machines)
                existing = db.execute(
                    "SELECT id FROM scheduled_tasks WHERE task_name = ? AND COALESCE(hostname, '') = ?",
                    (task.task_name, task.hostname or ""),
                ).fetchone()

                if new_only and existing:
                    continue  # Skip existing in new-only mode

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
                               hostname = ?, machine_alias = ?,
                               last_scanned = ?, updated_at = ?
                           WHERE id = ?""",
                        (task.task_path, task.status, task.last_run_time,
                         task.last_result, task.next_run_time, task.author,
                         task.run_as_user, task.action_command, task.action_args,
                         task.schedule_type, int(task.enabled), script_id,
                         task.hostname, task.machine_alias,
                         now, now, existing["id"]),
                    )
                    tasks_updated += 1
                else:
                    db.execute(
                        """INSERT INTO scheduled_tasks
                           (task_name, task_path, status, last_run_time,
                            last_result, next_run_time, author, run_as_user,
                            action_command, action_args, schedule_type, enabled,
                            script_id, hostname, machine_alias,
                            last_scanned, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (task.task_name, task.task_path, task.status,
                         task.last_run_time, task.last_result, task.next_run_time,
                         task.author, task.run_as_user, task.action_command,
                         task.action_args, task.schedule_type, int(task.enabled),
                         script_id, task.hostname, task.machine_alias,
                         now, now, now),
                    )
                    tasks_new += 1

            # Generate task_failed / script_failed actions and auto-resolve
            # any that have recovered since the last scan
            alerts_created, alerts_resolved = _sync_task_alerts(db, now)

        summary = {
            "status": "completed",
            "tasks_new": tasks_new,
            "tasks_updated": tasks_updated,
            "tasks_total": len(results),
            "scripts_linked": scripts_linked,
            "alerts_created": alerts_created,
            "alerts_resolved": alerts_resolved,
        }
        logger.info("Task Scheduler scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("Task Scheduler scan failed")
        return {"status": "failed", "error": str(e)}


def _sync_task_alerts(db, now: str) -> tuple[int, int]:
    """Create task_failed actions for failing tasks; auto-resolve recovered ones.

    For each active, non-archived task:
      - If last_result indicates failure: open task_failed (asset_type
        scheduled_task). If the task is linked to a script, mirror a
        script_failed action so the Scripts view also shows the problem.
      - If last_result indicates success: auto-resolve any open
        task_failed / script_failed actions for this task.

    Returns (created, resolved).
    """
    rows = db.execute(
        """SELECT id, task_name, last_result, script_id
           FROM scheduled_tasks
           WHERE COALESCE(archived, 0) = 0"""
    ).fetchall()

    created = 0
    resolved = 0
    for t in rows:
        task_id = t["id"]
        task_name = t["task_name"]
        script_id = t["script_id"]
        failing = _is_task_failed(t["last_result"])

        if failing:
            msg = f"Scheduled task last run returned {t['last_result']!r}: {task_name}"
            # Task-level action
            existing = db.execute(
                """SELECT id FROM actions
                   WHERE scheduled_task_id = ? AND type = 'task_failed'
                     AND status NOT IN ('resolved', 'expected')""",
                (task_id,),
            ).fetchone()
            if not existing:
                db.execute(
                    """INSERT INTO actions (scheduled_task_id, type, status, notes, created_at, updated_at)
                       VALUES (?, 'task_failed', 'open', ?, ?, ?)""",
                    (task_id, msg, now, now),
                )
                created += 1
            # Script-level mirror (only if a script is linked)
            if script_id:
                script_msg = f"Script failing via task {task_name!r} (exit {t['last_result']})"
                existing_script = db.execute(
                    """SELECT id FROM actions
                       WHERE script_id = ? AND type = 'script_failed'
                         AND status NOT IN ('resolved', 'expected')""",
                    (script_id,),
                ).fetchone()
                if not existing_script:
                    db.execute(
                        """INSERT INTO actions (script_id, type, status, notes, created_at, updated_at)
                           VALUES (?, 'script_failed', 'open', ?, ?, ?)""",
                        (script_id, script_msg, now, now),
                    )
                    created += 1
        else:
            # Recovered: close any open alerts tied to this task
            r1 = db.execute(
                """UPDATE actions
                   SET status = 'resolved', resolved_at = ?, updated_at = ?,
                       notes = COALESCE(notes, '') || ' [auto-resolved: task recovered]'
                   WHERE scheduled_task_id = ? AND type = 'task_failed'
                     AND status NOT IN ('resolved', 'expected')""",
                (now, now, task_id),
            )
            resolved += r1.rowcount or 0
            if script_id:
                # Only close the script-level action if no OTHER task linked
                # to this script is still failing
                any_other_failing = False
                other_tasks = db.execute(
                    """SELECT last_result FROM scheduled_tasks
                       WHERE script_id = ? AND id != ?
                         AND COALESCE(archived, 0) = 0""",
                    (script_id, task_id),
                ).fetchall()
                for ot in other_tasks:
                    if _is_task_failed(ot["last_result"]):
                        any_other_failing = True
                        break
                if not any_other_failing:
                    r2 = db.execute(
                        """UPDATE actions
                           SET status = 'resolved', resolved_at = ?, updated_at = ?,
                               notes = COALESCE(notes, '') || ' [auto-resolved: all tasks for this script recovered]'
                           WHERE script_id = ? AND type = 'script_failed'
                             AND status NOT IN ('resolved', 'expected')""",
                        (now, now, script_id),
                    )
                    resolved += r2.rowcount or 0

    return created, resolved
