"""
Task Scheduler scanner - reads Windows Task Scheduler via schtasks.exe.

Supports scanning the local machine and remote machines via WinRM.
On non-Windows (Linux dev), returns an empty list with a warning log.
"""

import csv
import io
import logging
import os
import platform
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SCHTASKS_CMD = ["schtasks.exe", "/query", "/fo", "CSV", "/v"]

# Machine aliases: hostname (lowercase) -> display name
# Also configurable via DG_MACHINE_ALIASES env var: "hostname1=Alias1,hostname2=Alias2"
_builtin_aliases = {
    "mx-share": "Admin",
}
_env_aliases = os.environ.get("DG_MACHINE_ALIASES", "")
MACHINE_ALIASES = dict(_builtin_aliases)
if _env_aliases:
    for pair in _env_aliases.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            MACHINE_ALIASES[k.strip().lower()] = v.strip()

def _get_local_hostname() -> str:
    """Get the local machine's hostname."""
    import socket
    return socket.gethostname()

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
    hostname: str = ""
    machine_alias: str = ""


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


def _parse_schtasks_csv(raw_output: str, hostname: str = "", alias: str = "") -> list[ScheduledTaskResult]:
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
            hostname=hostname,
            machine_alias=alias,
        ))

    return results


def _resolve_alias(hostname: str) -> str:
    """Look up a display alias for a hostname."""
    return MACHINE_ALIASES.get(hostname.lower(), hostname)


def _scan_single_machine(hostname: str | None = None) -> list[ScheduledTaskResult]:
    """Scan a single machine's Task Scheduler (local if hostname is None)."""
    cmd = list(SCHTASKS_CMD)
    label = "local"
    if hostname:
        cmd.extend(["/s", hostname])
        label = hostname

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("schtasks failed for %s (rc=%d): %s",
                           label, result.returncode, result.stderr[:500])
            return []

        actual_hostname = hostname or _get_local_hostname()
        alias = _resolve_alias(actual_hostname)
        return _parse_schtasks_csv(result.stdout, actual_hostname, alias)

    except FileNotFoundError:
        logger.warning("schtasks.exe not found")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("schtasks timed out for %s after 60s", label)
        return []
    except Exception:
        logger.exception("Task Scheduler scan failed for %s", label)
        return []


# Remote machines to scan (set via DG_SCHTASK_REMOTES env var, comma-separated)
_remotes_raw = os.environ.get("DG_SCHTASK_REMOTES", "MX-Share")
REMOTE_MACHINES = [h.strip() for h in _remotes_raw.split(",") if h.strip()]


def scan_scheduled_tasks() -> list[ScheduledTaskResult]:
    """Query Windows Task Scheduler on local and remote machines.

    Returns empty list on non-Windows or if schtasks fails.
    """
    if platform.system() != "Windows":
        logger.info("Not on Windows - skipping Task Scheduler scan")
        return []

    all_results = []

    # Scan local machine
    local = _scan_single_machine(None)
    # Set alias for local machine
    local_hostname = _get_local_hostname()
    local_alias = _resolve_alias(local_hostname)
    # If the local machine IS one of the remotes, skip it in remote scan
    local_lower = local_hostname.lower()
    for t in local:
        if not t.machine_alias:
            t.machine_alias = local_alias
    all_results.extend(local)
    logger.info("Local scan: %d tasks from %s (%s)", len(local), local_hostname, local_alias)

    # Scan remote machines
    for remote in REMOTE_MACHINES:
        if remote.lower() == local_lower:
            continue  # Already scanned locally
        logger.info("Scanning remote machine: %s", remote)
        remote_results = _scan_single_machine(remote)
        logger.info("Remote scan %s: %d tasks", remote, len(remote_results))
        all_results.extend(remote_results)

    return all_results
