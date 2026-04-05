"""
Task Scheduler scanner - reads Windows Task Scheduler via schtasks.exe.

On non-Windows (Linux dev), returns an empty list with a warning log.
"""

import csv
import io
import logging
import platform
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SCHTASKS_CMD = ["schtasks.exe", "/query", "/fo", "CSV", "/v"]

# schtasks CSV column headers (English locale)
COL_TASKNAME = "TaskName"
COL_STATUS = "Status"
COL_LAST_RUN = "Last Run Time"
COL_LAST_RESULT = "Last Result"
COL_NEXT_RUN = "Next Run Time"
COL_AUTHOR = "Author"
COL_TASK_TO_RUN = "Task To Run"
COL_SCHEDULE_TYPE = "Schedule Type"
COL_RUN_AS_USER = "Run As User"
COL_STATE = "Scheduled Task State"


@dataclass
class ScheduledTaskResult:
    task_name: str
    task_path: str
    status: str
    last_run_time: str | None
    last_result: str | None
    next_run_time: str | None
    author: str | None
    run_as_user: str | None
    action_command: str | None
    action_args: str | None
    schedule_type: str | None
    enabled: bool


def _split_command(task_to_run: str) -> tuple[str | None, str | None]:
    """Split 'Task To Run' into (executable, arguments).

    schtasks puts the full command line in one field.
    e.g. 'C:\\Python39\\python.exe C:\\scripts\\my_script.py --flag'
    """
    if not task_to_run:
        return None, None
    # Handle quoted executable
    if task_to_run.startswith('"'):
        end = task_to_run.find('"', 1)
        if end > 0:
            cmd = task_to_run[1:end]
            args = task_to_run[end + 1:].strip() or None
            return cmd, args
    # Space-separated
    parts = task_to_run.split(None, 1)
    cmd = parts[0] if parts else None
    args = parts[1] if len(parts) > 1 else None
    return cmd, args


def _parse_schtasks_csv(raw_output: str) -> list[ScheduledTaskResult]:
    """Parse CSV output from schtasks.exe /query /fo CSV /v."""
    results = []
    reader = csv.DictReader(io.StringIO(raw_output))

    for row in reader:
        task_name_full = row.get(COL_TASKNAME, "").strip()
        if not task_name_full:
            continue

        # Skip Microsoft built-in tasks
        if task_name_full.startswith("\\Microsoft\\"):
            continue

        # task_path is the full path, task_name is just the leaf
        task_path = task_name_full
        task_name = task_name_full.rsplit("\\", 1)[-1]

        # Parse "Task To Run" into command + args
        task_to_run = row.get(COL_TASK_TO_RUN, "").strip()
        action_command, action_args = _split_command(task_to_run)

        last_run = row.get(COL_LAST_RUN, "").strip()
        next_run = row.get(COL_NEXT_RUN, "").strip()

        # Normalize "N/A" and "Never" to None
        if last_run in ("N/A", "Never", ""):
            last_run = None
        if next_run in ("N/A", "Never", ""):
            next_run = None

        state = row.get(COL_STATE, "").strip()
        enabled = state.lower() != "disabled"

        results.append(ScheduledTaskResult(
            task_name=task_name,
            task_path=task_path,
            status=row.get(COL_STATUS, "").strip(),
            last_run_time=last_run,
            last_result=row.get(COL_LAST_RESULT, "").strip() or None,
            next_run_time=next_run,
            author=row.get(COL_AUTHOR, "").strip() or None,
            run_as_user=row.get(COL_RUN_AS_USER, "").strip() or None,
            action_command=action_command,
            action_args=action_args,
            schedule_type=row.get(COL_SCHEDULE_TYPE, "").strip() or None,
            enabled=enabled,
        ))

    return results


def scan_scheduled_tasks() -> list[ScheduledTaskResult]:
    """Query Windows Task Scheduler and return parsed results.

    Returns empty list on non-Windows or if schtasks fails.
    """
    if platform.system() != "Windows":
        logger.info("Not on Windows - skipping Task Scheduler scan")
        return []

    try:
        result = subprocess.run(
            SCHTASKS_CMD,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("schtasks failed (rc=%d): %s",
                           result.returncode, result.stderr[:500])
            return []

        return _parse_schtasks_csv(result.stdout)

    except FileNotFoundError:
        logger.warning("schtasks.exe not found")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("schtasks timed out after 30s")
        return []
    except Exception:
        logger.exception("Task Scheduler scan failed")
        return []
