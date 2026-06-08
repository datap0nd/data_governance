"""Power BI refresh schedule sync.

Two modes:
1. trigger_pbi_sync() - called from the API button. Creates a Windows scheduled task
   that runs the PS1 script in the user's interactive session (so login popup works).
   The PS1 script POSTs data back to /api/scanner/pbi-import.
2. import_pbi_data() - receives the JSON from the PS1 script and updates the DB.
"""

import json
import logging
import os
import platform
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import (
    BASE_DIR,
    PBI_CLIENT_ID,
    PBI_CLIENT_SECRET,
    PBI_SYNC_WINDOWS_USER,
    PBI_TENANT_ID,
    PBI_WORKSPACE,
)
from app.database import get_db

logger = logging.getLogger(__name__)

PS1_SCRIPT = BASE_DIR / "tools" / "pbi_refresh_sync.ps1"
TASK_NAME = "DG_PBI_Sync"
PS1_USAGE_SCRIPT = BASE_DIR / "tools" / "pbi_usage_sync.ps1"
USAGE_TASK_NAME = "DG_PBI_Usage_Sync"
RDP_GUARD_SCRIPT = BASE_DIR / "tools" / "rdp_console_guard.ps1"
RDP_GUARD_TASK_NAME = "DG_RDP_Console_Guard"
PENDING_REFRESH_SETTING = "pbi_sync_pending_refresh"
PBI_IMPORT_LOCK_RETRY_SECONDS = 900


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def service_principal_configured() -> bool:
    """Return True when unattended Power BI auth has enough config to run."""
    return bool(PBI_TENANT_ID and PBI_CLIENT_ID and PBI_CLIENT_SECRET)


def _record_sync_run(
    sync_type: str,
    status: str,
    message: str | None = None,
    details: dict | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    """Persist sync status so scheduled emails can require fresh PBI data."""
    now = _now_iso()
    started = started_at or now
    finished = finished_at if finished_at is not None else (now if status in {"completed", "failed", "skipped", "stopped"} else None)
    details_json = json.dumps(details or {}, default=str) if details else None
    try:
        with get_db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS pbi_sync_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_type   TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME,
                    message     TEXT,
                    details     TEXT
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_pbi_sync_runs_type_status ON pbi_sync_runs(sync_type, status, finished_at)"
            )
            db.execute(
                """INSERT INTO pbi_sync_runs (sync_type, status, started_at, finished_at, message, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sync_type, status, started, finished, message, details_json),
            )
    except Exception:
        logger.exception("Could not record PBI sync run")


def _read_app_setting(key: str) -> str | None:
    with get_db() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def _write_app_setting(key: str, value: str) -> None:
    with get_db() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        db.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = CURRENT_TIMESTAMP""",
            (key, value),
        )


def _delete_app_setting(key: str) -> None:
    with get_db() as db:
        db.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def get_pending_pbi_sync() -> dict | None:
    raw = _read_app_setting(PENDING_REFRESH_SETTING)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "pending", "message": raw}


def _save_pending_pbi_sync(payload: dict) -> None:
    _write_app_setting(PENDING_REFRESH_SETTING, json.dumps(payload, default=str))


def clear_pending_pbi_sync() -> None:
    try:
        _delete_app_setting(PENDING_REFRESH_SETTING)
    except Exception:
        logger.exception("Could not clear pending PBI sync state")


def _mark_pbi_sync_pending(message: str, source: str, details: dict | None = None) -> dict:
    now = _now_iso()
    existing = get_pending_pbi_sync() or {}
    first_seen = existing.get("created_at") or now
    attempts = int(existing.get("attempts") or 0)
    pending = {
        "status": "pending",
        "sync_type": "refresh",
        "source": source,
        "message": message,
        "created_at": first_seen,
        "updated_at": now,
        "attempts": attempts,
        "details": details or existing.get("details") or {},
    }
    _save_pending_pbi_sync(pending)
    if not existing:
        _record_sync_run("refresh", "pending", message, pending, started_at=now, finished_at=None)
    return pending


def _update_pending_retry(message: str, details: dict | None = None) -> dict:
    now = _now_iso()
    pending = get_pending_pbi_sync() or {
        "status": "pending",
        "sync_type": "refresh",
        "source": "retry",
        "created_at": now,
        "attempts": 0,
    }
    pending["message"] = message
    pending["updated_at"] = now
    pending["last_retry_at"] = now
    pending["attempts"] = int(pending.get("attempts") or 0) + 1
    if details:
        pending["details"] = details
    _save_pending_pbi_sync(pending)
    return pending


def latest_pbi_sync(sync_type: str = "refresh") -> dict | None:
    """Return the newest sync run for a sync type."""
    try:
        with get_db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS pbi_sync_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_type   TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME,
                    message     TEXT,
                    details     TEXT
                )"""
            )
            row = db.execute(
                """SELECT * FROM pbi_sync_runs
                   WHERE sync_type = ?
                   ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
                   LIMIT 1""",
                (sync_type,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("Could not read latest PBI sync run")
        return None


def latest_successful_pbi_sync(sync_type: str = "refresh") -> dict | None:
    """Return the newest completed sync run for a sync type."""
    try:
        with get_db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS pbi_sync_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_type   TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME,
                    message     TEXT,
                    details     TEXT
                )"""
            )
            row = db.execute(
                """SELECT * FROM pbi_sync_runs
                   WHERE sync_type = ? AND status = 'completed'
                   ORDER BY finished_at DESC, id DESC
                   LIMIT 1""",
                (sync_type,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("Could not read latest successful PBI sync run")
        return None


def pbi_sync_freshness(max_age_hours: float = 24.0, sync_type: str = "refresh") -> dict:
    """Return freshness information for the latest successful PBI sync."""
    latest_success = latest_successful_pbi_sync(sync_type)
    latest_attempt = latest_pbi_sync(sync_type)
    if not latest_success or not latest_success.get("finished_at"):
        return {
            "fresh": False,
            "reason": "No completed Power BI sync has been recorded.",
            "latest_success": latest_success,
            "latest_attempt": latest_attempt,
        }
    try:
        finished = latest_success["finished_at"].replace("Z", "+00:00")
        finished_dt = datetime.fromisoformat(finished)
        if finished_dt.tzinfo is None:
            finished_dt = finished_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return {
            "fresh": False,
            "reason": "The latest Power BI sync timestamp is invalid.",
            "latest_success": latest_success,
            "latest_attempt": latest_attempt,
        }
    age_hours = (datetime.now(timezone.utc) - finished_dt).total_seconds() / 3600
    fresh = age_hours <= max_age_hours
    return {
        "fresh": fresh,
        "age_hours": age_hours,
        "max_age_hours": max_age_hours,
        "reason": None if fresh else f"Latest Power BI sync is {age_hours:.1f}h old.",
        "latest_success": latest_success,
        "latest_attempt": latest_attempt,
    }


def _launch_powershell_background(script: Path, args: list[str], sync_type: str) -> dict:
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        _record_sync_run(sync_type, "failed", str(exc))
        return {"status": "error", "message": str(exc)}
    if sync_type == "refresh":
        clear_pending_pbi_sync()
    _record_sync_run(sync_type, "launched", "Power BI sync launched with service principal authentication.")
    return {
        "status": "launched",
        "message": "Power BI sync launched in unattended service-principal mode.",
        "auth_mode": "service_principal",
    }


def _run_schtasks_action(task_name: str, action: str) -> dict:
    command = ["schtasks", f"/{action}", "/tn", task_name]
    if action == "delete":
        command.append("/f")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "task": task_name,
            "action": action,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "message": (result.stdout or result.stderr or "").strip()[-500:],
        }
    except Exception as exc:
        return {
            "task": task_name,
            "action": action,
            "ok": False,
            "message": str(exc),
        }


def _stop_matching_powershell_processes() -> dict:
    script = r'''
$ErrorActionPreference = "SilentlyContinue"
$patterns = @(
    "pbi_refresh_sync.ps1",
    "pbi_usage_sync.ps1",
    "pbi_auto_click_picker.ps1",
    "pbi-sync/run-status",
    "Interactive Power BI sign-in did not complete"
)

function Get-DgCommandText($processInfo) {
    $line = [string]$processInfo.CommandLine
    if ($line -match "(?i)-encodedcommand\s+([A-Za-z0-9+/=]+)") {
        try {
            $decoded = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Matches[1]))
            return "$line`n$decoded"
        } catch {
            return $line
        }
    }
    return $line
}

function Test-DgSyncProcess([string]$text) {
    foreach ($pattern in $patterns) {
        if ($text -like "*$pattern*") {
            return $true
        }
    }
    return $false
}

function Get-DgSyncType([string]$text) {
    if ($text -like "*pbi_refresh_sync.ps1*") { return "refresh" }
    if ($text -like "*pbi_usage_sync.ps1*") { return "usage" }
    return ""
}

$stopped = @()
$errors = @()
$currentPid = $PID
$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.ProcessId -ne $currentPid -and
        $_.Name -match "^(powershell|pwsh)(\.exe)?$" -and
        (Test-DgSyncProcess (Get-DgCommandText $_))
    }

foreach ($processInfo in $processes) {
    $text = Get-DgCommandText $processInfo
    $preview = if ($text.Length -gt 700) { $text.Substring(0, 700) } else { $text }
    try {
        Stop-Process -Id $processInfo.ProcessId -Force -ErrorAction Stop
        $stopped += [pscustomobject]@{
            pid = $processInfo.ProcessId
            name = $processInfo.Name
            sync_type = Get-DgSyncType $text
            command_line = $preview
        }
    } catch {
        $errors += [pscustomobject]@{
            pid = $processInfo.ProcessId
            name = $processInfo.Name
            message = $_.Exception.Message
            command_line = $preview
        }
    }
}

[pscustomobject]@{
    stopped = @($stopped)
    errors = @($errors)
} | ConvertTo-Json -Depth 5
'''
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    stdout = (result.stdout or "").strip()
    parsed: dict = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"raw_output": stdout[-1000:]}
    parsed.setdefault("stopped", [])
    parsed.setdefault("errors", [])
    parsed["returncode"] = result.returncode
    if result.stderr:
        parsed["stderr"] = result.stderr.strip()[-1000:]
    return parsed


def stop_pbi_sync_processes() -> dict:
    """Stop running Power BI refresh/usage sync tasks and helper processes."""
    if platform.system() != "Windows":
        return {"status": "skipped", "message": "PBI sync stop is only available on Windows"}

    sync_tasks = ((TASK_NAME, "refresh"), (USAGE_TASK_NAME, "usage"))
    task_results = []
    recorded_types: set[str] = set()

    for task_name, sync_type in sync_tasks:
        for action in ("end", "delete"):
            result = _run_schtasks_action(task_name, action)
            task_results.append(result)
            if action == "end" and result.get("ok"):
                recorded_types.add(sync_type)

    process_result = _stop_matching_powershell_processes()
    stopped_processes = process_result.get("stopped") or []
    if isinstance(stopped_processes, dict):
        stopped_processes = [stopped_processes]
    process_errors = process_result.get("errors") or []
    if isinstance(process_errors, dict):
        process_errors = [process_errors]

    for proc in stopped_processes:
        sync_type = (proc or {}).get("sync_type")
        if sync_type in {"refresh", "usage"}:
            recorded_types.add(sync_type)

    for sync_type in ("refresh", "usage"):
        latest = latest_pbi_sync(sync_type)
        if latest and latest.get("status") == "launched":
            recorded_types.add(sync_type)

    details = {
        "tasks": task_results,
        "processes": process_result,
    }
    for sync_type in sorted(recorded_types):
        _record_sync_run(
            sync_type,
            "stopped",
            "Power BI sync stopped by user.",
            details,
        )

    stopped_task_actions = sum(1 for result in task_results if result.get("action") == "end" and result.get("ok"))
    stopped_process_count = len(stopped_processes)
    status = "stopped" if recorded_types or stopped_task_actions or stopped_process_count else "idle"
    if status == "idle":
        message = "No running Power BI sync tasks or processes were found."
    else:
        message = (
            f"Stop requested: {stopped_task_actions} task action(s), "
            f"{stopped_process_count} process(es) stopped."
        )

    return {
        "status": status,
        "message": message,
        "tasks": task_results,
        "processes_stopped": stopped_process_count,
        "process_errors": process_errors,
        "recorded_sync_types": sorted(recorded_types),
    }


def _short_windows_user(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "\\" in raw:
        return raw.rsplit("\\", 1)[-1]
    if "@" in raw:
        return raw.split("@", 1)[0]
    return raw


def _parse_quser_sessions(output: str) -> list[dict]:
    sessions = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("username"):
            continue
        line = line.lstrip(">").strip()
        parts = line.split()
        if len(parts) < 3:
            continue
        user = parts[0]
        if parts[1].isdigit():
            session_name = ""
            session_id = parts[1]
            state = parts[2]
        elif len(parts) >= 4 and parts[2].isdigit():
            session_name = parts[1]
            session_id = parts[2]
            state = parts[3]
        else:
            continue
        try:
            parsed_id = int(session_id)
        except ValueError:
            continue
        sessions.append(
            {
                "user": user,
                "short_user": _short_windows_user(user),
                "session_name": session_name,
                "session_id": parsed_id,
                "state": state,
                "raw": line,
            }
        )
    return sessions


def _target_session_status() -> dict:
    target_user = PBI_SYNC_WINDOWS_USER or os.environ.get("USERNAME", "")
    target_short = _short_windows_user(target_user)
    if not target_short:
        return {
            "ready": False,
            "repairable": False,
            "message": "No sync Windows user configured. Set DG_PBI_SYNC_WINDOWS_USER.",
            "target_user": target_user,
            "sessions": [],
        }
    try:
        result = subprocess.run(
            ["quser"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return {
            "ready": False,
            "repairable": False,
            "message": f"Could not query Windows sessions with quser: {exc}",
            "target_user": target_user,
            "sessions": [],
        }
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        return {
            "ready": False,
            "repairable": False,
            "message": f"quser failed: {msg}",
            "target_user": target_user,
            "sessions": [],
        }

    sessions = _parse_quser_sessions(result.stdout)
    target_sessions = [s for s in sessions if s["short_user"] == target_short]
    if not target_sessions:
        return {
            "ready": False,
            "repairable": False,
            "message": f"No Windows session found for sync user '{target_user}'. Log into that account once before running PBI sync.",
            "target_user": target_user,
            "sessions": sessions,
        }

    interactive = next(
        (
            s
            for s in target_sessions
            if s["state"].lower() == "active"
            and (
                s["session_name"].lower() == "console"
                or s["session_name"].lower().startswith("rdp-tcp")
            )
        ),
        None,
    )
    if interactive:
        surface = "console" if interactive["session_name"].lower() == "console" else "active RDP"
        return {
            "ready": True,
            "repairable": False,
            "message": f"Sync user '{target_user}' is active on {surface} session {interactive['session_id']}.",
            "target_user": target_user,
            "sessions": target_sessions,
            "interactive_session": interactive,
        }

    repairable = any(s["state"].lower().startswith("disc") for s in target_sessions)
    states = ", ".join(f"{s['session_name'] or '-'}:{s['session_id']}:{s['state']}" for s in target_sessions)
    return {
        "ready": False,
        "repairable": repairable,
        "message": f"Sync user '{target_user}' is not active in an interactive session. Sessions: {states}",
        "target_user": target_user,
        "sessions": target_sessions,
    }


def _wait_for_console_ready(seconds: int = 15) -> dict:
    deadline = time.monotonic() + seconds
    latest = _target_session_status()
    while time.monotonic() < deadline:
        if latest.get("ready"):
            return latest
        time.sleep(1)
        latest = _target_session_status()
    return latest


def _run_guard_script_direct(target_user: str) -> dict:
    if not RDP_GUARD_SCRIPT.exists():
        return {"status": "skipped", "message": f"RDP console guard script not found: {RDP_GUARD_SCRIPT}"}
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(RDP_GUARD_SCRIPT),
    ]
    if target_user:
        command.extend(["-TargetUser", target_user])
    try:
        result = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "status": "completed" if result.returncode == 0 else "failed",
            "message": (result.stdout or result.stderr or "").strip()[-500:],
            "returncode": result.returncode,
        }
    except Exception as exc:
        return {"status": "failed", "message": str(exc)}


def _run_rdp_console_guard() -> dict:
    """Repair and verify the sync user's interactive session before PBI sync."""
    if platform.system() != "Windows":
        return {"status": "skipped", "ready": True, "message": "RDP console guard only runs on Windows."}

    target_user = PBI_SYNC_WINDOWS_USER or os.environ.get("USERNAME", "")
    before = _target_session_status()
    if before.get("ready"):
        return {"status": "ready", "ready": True, "message": before.get("message"), "before": before, "after": before}

    guard_attempts = []
    try:
        task_result = subprocess.run(
            ["schtasks", "/run", "/tn", RDP_GUARD_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        guard_attempts.append(
            {
                "method": "scheduled_task",
                "returncode": task_result.returncode,
                "message": (task_result.stdout or task_result.stderr or "").strip()[-500:],
            }
        )
        if task_result.returncode == 0:
            after_task = _wait_for_console_ready(15)
            if after_task.get("ready"):
                return {
                    "status": "repaired",
                    "ready": True,
                    "message": after_task.get("message"),
                    "before": before,
                    "after": after_task,
                    "guard_attempts": guard_attempts,
                }
    except Exception as exc:
        guard_attempts.append({"method": "scheduled_task", "status": "failed", "message": str(exc)})

    direct_result = _run_guard_script_direct(target_user)
    guard_attempts.append({"method": "direct_script", **direct_result})
    after_direct = _wait_for_console_ready(10)
    if after_direct.get("ready"):
        return {
            "status": "repaired",
            "ready": True,
            "message": after_direct.get("message"),
            "before": before,
            "after": after_direct,
            "guard_attempts": guard_attempts,
        }

    return {
        "status": "not_ready",
        "ready": False,
        "message": after_direct.get("message") or before.get("message"),
        "before": before,
        "after": after_direct,
        "guard_attempts": guard_attempts,
    }


def rdp_console_guard_status() -> dict:
    """Return current sync-user session status for operator diagnostics."""
    if platform.system() != "Windows":
        return {"status": "skipped", "ready": True, "message": "RDP console guard only runs on Windows."}
    status = _target_session_status()
    return {"status": "ready" if status.get("ready") else "not_ready", **status}


def _build_schedule_string(schedule: dict) -> str | None:
    """Convert schedule dict to human-readable string like 'Monday, Wednesday @ 08:00, 16:00'."""
    if not schedule or not schedule.get("enabled"):
        return None
    days = schedule.get("days") or []
    times = schedule.get("times") or []
    if not days and not times:
        return None
    parts = []
    if days:
        parts.append(", ".join(days))
    if times:
        parts.append(", ".join(times))
    return " @ ".join(parts) if parts else None


def trigger_pbi_sync(workspace: str | None = None, port: int = 8000) -> dict:
    """Launch PBI sync in the user's interactive session via scheduled task.

    The PS1 script runs where the user can see the login popup, fetches PBI data,
    and POSTs it back to /api/scanner/pbi-import.
    """
    if platform.system() != "Windows":
        message = "PBI sync only available on Windows"
        _record_sync_run("refresh", "skipped", message)
        return {"status": "skipped", "message": message}

    ws_name = workspace or PBI_WORKSPACE
    if not ws_name:
        message = "No PBI workspace configured (set DG_PBI_WORKSPACE)"
        _record_sync_run("refresh", "failed", message)
        return {"status": "error", "message": message}

    if not PS1_SCRIPT.exists():
        message = f"PowerShell script not found: {PS1_SCRIPT}"
        _record_sync_run("refresh", "failed", message)
        return {"status": "error", "message": message}

    if service_principal_configured():
        return _launch_powershell_background(
            PS1_SCRIPT,
            ["-WorkspaceName", ws_name, "-ApiBase", f"http://localhost:{port}", "-NoPause"],
            "refresh",
        )

    guard_result = _run_rdp_console_guard()
    logger.info("RDP console guard before refresh sync: %s", guard_result.get("status"))
    if not guard_result.get("ready"):
        message = guard_result.get("message") or "Sync Windows session is not ready for interactive Power BI sign-in."
        _record_sync_run("refresh", "failed", message, guard_result)
        return {
            "status": "error",
            "message": message,
            "auth_mode": "interactive",
            "guard": guard_result,
        }

    # Build the command the scheduled task will run
    ps_cmd = (
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{PS1_SCRIPT}" '
        f'-WorkspaceName "{ws_name}" -ApiBase "http://localhost:{port}" -NoPause'
    )

    # Create a scheduled task that runs in the interactive session
    try:
        # Delete old task if it exists
        subprocess.run(
            ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
            capture_output=True, timeout=10,
        )
        # Create new task - /it = interactive only, /rl highest = run elevated
        subprocess.run(
            [
                "schtasks", "/create",
                "/tn", TASK_NAME,
                "/tr", ps_cmd,
                "/sc", "once",
                "/st", "00:00",
                "/it",
                "/f",
            ],
            capture_output=True, text=True, timeout=10, check=True,
        )
        # Run it now
        subprocess.run(
            ["schtasks", "/run", "/tn", TASK_NAME],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        _record_sync_run("refresh", "failed", f"Failed to launch PBI sync task: {stderr}")
        return {"status": "error", "message": f"Failed to launch PBI sync task: {stderr}"}
    except Exception as e:
        _record_sync_run("refresh", "failed", str(e))
        return {"status": "error", "message": str(e)}

    clear_pending_pbi_sync()
    _record_sync_run("refresh", "launched", "Power BI sync launched with interactive scheduled task.")
    return {
        "status": "launched",
        "message": "PBI sync started - a PowerShell window should appear on your desktop. Log in if prompted.",
        "auth_mode": "interactive",
    }


def _should_defer_interactive_sync(result: dict) -> bool:
    if service_principal_configured():
        return False
    if (result.get("status") or "").lower() != "error":
        return False
    if result.get("guard"):
        return True
    message = (result.get("message") or "").lower()
    return any(
        token in message
        for token in (
            "interactive",
            "session",
            "desktop",
            "lock screen",
            "not active",
            "not logged on",
            "user is not logged",
        )
    )


def trigger_pbi_sync_or_defer(source: str = "scheduled") -> dict:
    """Launch PBI sync, or mark it pending until an interactive session is usable."""
    result = trigger_pbi_sync()
    status = (result.get("status") or "").lower()
    if status in {"launched", "completed"}:
        clear_pending_pbi_sync()
        return result
    if _should_defer_interactive_sync(result):
        pending = _mark_pbi_sync_pending(
            result.get("message") or "Power BI sync is waiting for an interactive desktop.",
            source,
            result,
        )
        return {**result, "pending": pending}
    return result


def retry_pending_pbi_sync() -> dict:
    """Retry a pending scheduled PBI sync when the interactive desktop is ready."""
    pending = get_pending_pbi_sync()
    if not pending:
        return {"status": "idle", "message": "No pending Power BI sync."}

    guard_result = _run_rdp_console_guard()
    if not guard_result.get("ready"):
        message = guard_result.get("message") or "Power BI sync is waiting for an interactive desktop."
        updated = _update_pending_retry(message, guard_result)
        return {"status": "waiting", "message": message, "pending": updated, "guard": guard_result}

    result = trigger_pbi_sync()
    status = (result.get("status") or "").lower()
    if status in {"launched", "completed"}:
        clear_pending_pbi_sync()
        return result
    if _should_defer_interactive_sync(result):
        updated = _update_pending_retry(
            result.get("message") or "Power BI sync is still waiting for an interactive desktop.",
            result,
        )
        return {**result, "pending": updated}

    clear_pending_pbi_sync()
    return result


def _is_sqlite_lock_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and (
        "locked" in str(exc).lower() or "busy" in str(exc).lower()
    )


def _run_with_sqlite_lock_retry(operation: str, fn):
    deadline = time.monotonic() + PBI_IMPORT_LOCK_RETRY_SECONDS
    attempt = 0
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _is_sqlite_lock_error(exc) or time.monotonic() >= deadline:
                raise
            attempt += 1
            sleep_seconds = min(10.0, 0.5 * attempt)
            logger.warning(
                "%s waiting for SQLite lock, retry %s in %.1fs: %s",
                operation,
                attempt,
                sleep_seconds,
                exc,
            )
            time.sleep(sleep_seconds)


def import_pbi_data(data: dict) -> dict:
    """Import PBI data, waiting through refresh-time SQLite write contention."""
    return _run_with_sqlite_lock_retry("Power BI refresh import", lambda: _import_pbi_data_once(data))


def _import_pbi_data_once(data: dict) -> dict:
    """Import PBI data received from the PS1 script and update the reports table.

    Also auto-archives reports not found in PBI and sets powerbi_url.
    """
    reports_data = data.get("reports") or []
    matched = 0
    unmatched = []
    archived_count = 0
    log_lines = []

    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        # Build a lookup map: lowercase name -> (id, original name)
        all_reports = db.execute("SELECT id, name FROM reports").fetchall()
        name_map = {r["name"].strip().lower(): (r["id"], r["name"]) for r in all_reports}

        matched_ids = set()

        for entry in reports_data:
            report_name = entry.get("report_name")
            if not report_name:
                continue

            # Case-insensitive match with trimmed whitespace
            match = name_map.get(report_name.strip().lower())

            if not match:
                unmatched.append(report_name)
                log_lines.append(f"SKIP: {report_name} (not in DB)")
                continue

            report_id = match[0]
            matched_ids.add(report_id)
            schedule = entry.get("schedule") or {}
            last_refresh = entry.get("last_refresh") or {}

            schedule_str = _build_schedule_string(schedule)
            last_refresh_at = last_refresh.get("end_time") or last_refresh.get("start_time")
            refresh_status = last_refresh.get("status")
            refresh_error = last_refresh.get("error")

            if refresh_error and len(refresh_error) > 500:
                refresh_error = refresh_error[:500] + "..."

            db.execute(
                """UPDATE reports SET
                    pbi_dataset_id = ?,
                    pbi_refresh_schedule = ?,
                    pbi_last_refresh_at = ?,
                    pbi_refresh_status = ?,
                    pbi_refresh_error = ?,
                    powerbi_url = ?,
                    archived = 0,
                    updated_at = ?
                WHERE id = ?""",
                (
                    entry.get("dataset_id"),
                    schedule_str,
                    last_refresh_at,
                    refresh_status,
                    refresh_error,
                    entry.get("web_url"),
                    now,
                    report_id,
                ),
            )
            matched += 1
            status_str = refresh_status or "no history"
            log_lines.append(f"OK: {report_name} - {status_str} ({schedule_str or 'no schedule'})")

        # Auto-archive reports NOT found in PBI workspace
        if matched_ids:
            for r in all_reports:
                if r["id"] not in matched_ids:
                    db.execute(
                        "UPDATE reports SET archived = 1, updated_at = ? WHERE id = ? AND archived = 0",
                        (now, r["id"]),
                    )
                    if db.total_changes:
                        archived_count += 1
                        log_lines.append(f"ARCHIVE: {r['name']} (not in PBI workspace)")

        # Check for overdue refreshes and create alerts
        overdue_count = _check_refresh_alerts(db, now)

    summary = {
        "status": "completed",
        "workspace": data.get("workspace"),
        "synced_at": data.get("synced_at"),
        "total_pbi_reports": len(reports_data),
        "matched": matched,
        "unmatched": unmatched,
        "archived": archived_count,
        "overdue_alerts": overdue_count,
        "log": "\n".join(log_lines),
    }
    _record_sync_run(
        "refresh",
        "completed",
        f"Power BI refresh sync completed: {matched} matched, {len(unmatched)} unmatched.",
        {
            "workspace": data.get("workspace"),
            "synced_at": data.get("synced_at"),
            "total_pbi_reports": len(reports_data),
            "matched": matched,
            "unmatched_count": len(unmatched),
            "archived": archived_count,
            "overdue_alerts": overdue_count,
        },
        finished_at=now,
    )
    clear_pending_pbi_sync()
    logger.info("PBI sync completed: %s matched, %s unmatched, %s archived, %s overdue", matched, len(unmatched), archived_count, overdue_count)
    return summary


WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def _schedule_days_per_week(schedule_str: str | None) -> int:
    """Parse number of refresh days per week from schedule string."""
    if not schedule_str:
        return 0
    day_part = schedule_str.split(" @ ")[0] if " @ " in schedule_str else schedule_str
    days = [d.strip().lower() for d in day_part.split(",")]
    return sum(1 for d in days if d in WEEKDAYS)


def _max_gap_hours(days_per_week: int) -> float:
    """Max expected hours between refreshes, with buffer.

    Formula: (7 / days_per_week + 1) * 24
    - Daily (7): 48h (2 days)
    - Business days (5): ~58h (~2.4 days)
    - 3x/week: ~80h (~3.3 days)
    - 2x/week: ~108h (~4.5 days)
    - Weekly (1): 192h (8 days)
    """
    if days_per_week <= 0:
        return 0
    return (7 / days_per_week + 1) * 24


def is_refresh_overdue(schedule_str: str | None, last_refresh_at: str | None) -> bool:
    """Check if a report's refresh is overdue based on its schedule."""
    dpw = _schedule_days_per_week(schedule_str)
    if dpw == 0:
        return False
    if not last_refresh_at:
        return True  # has a schedule but never refreshed

    try:
        # Handle various timestamp formats
        ts = last_refresh_at.replace("Z", "+00:00")
        last_dt = datetime.fromisoformat(ts)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False

    now = datetime.now(timezone.utc)
    hours_since = (now - last_dt).total_seconds() / 3600
    return hours_since > _max_gap_hours(dpw)


def _check_refresh_alerts(db, now: str) -> int:
    """Create actions + alerts for reports with refresh problems.

    Two kinds of issues, both surface in the dashboard Alerts table:
      - refresh_failed: pbi_refresh_status indicates the last refresh errored
      - refresh_overdue: scheduled to refresh but no recent successful run

    Duplicates are avoided by the standard "open action already exists for
    this report and type" check. When a refresh recovers (status becomes
    Completed), any open action for that report auto-resolves on the next
    sync via _auto_resolve_recovered_refreshes.
    """
    reports = db.execute(
        """SELECT id, name, pbi_refresh_schedule, pbi_last_refresh_at,
                  pbi_refresh_status, pbi_refresh_error, owner
           FROM reports WHERE archived = 0"""
    ).fetchall()

    created = 0
    for r in reports:
        status_val = (r["pbi_refresh_status"] or "").lower()
        is_failed = status_val in ("failed", "disabled", "cancelled")
        is_overdue = (
            r["pbi_refresh_schedule"]
            and is_refresh_overdue(r["pbi_refresh_schedule"], r["pbi_last_refresh_at"])
        )

        if not is_failed and not is_overdue:
            continue

        # Failed takes precedence over overdue (a failed run is the root cause)
        action_type = "refresh_failed" if is_failed else "refresh_overdue"
        last_str = r["pbi_last_refresh_at"] or "never"
        if is_failed:
            msg = f"PBI refresh {status_val}: {r['name']}"
            if r["pbi_refresh_error"]:
                msg += f" - {r['pbi_refresh_error'][:200]}"
            severity = "critical"
        else:
            msg = (
                f"PBI refresh overdue: {r['name']} "
                f"(schedule: {r['pbi_refresh_schedule']}, last refresh: {last_str})"
            )
            severity = "warning"

        # Action (drives the Alerts table on the dashboard)
        existing_action = db.execute(
            """SELECT id FROM actions
               WHERE report_id = ? AND source_id IS NULL AND type IN ('refresh_failed', 'refresh_overdue')
                 AND status NOT IN ('resolved', 'expected')""",
            (r["id"],),
        ).fetchone()
        if not existing_action:
            db.execute(
                """INSERT INTO actions (report_id, type, status, assigned_to, notes, created_at, updated_at)
                   VALUES (?, ?, 'open', ?, ?, ?, ?)""",
                (r["id"], action_type, r["owner"], msg, now, now),
            )
            created += 1

        # Alert (legacy view - keep for back-compat, but tied to report_id now)
        existing_alert = db.execute(
            """SELECT id FROM alerts
               WHERE message LIKE ? AND resolution_status IS NULL""",
            (f"PBI refresh % {r['name']}%",),
        ).fetchone()
        if not existing_alert:
            db.execute(
                """INSERT INTO alerts (severity, message, assigned_to, created_at)
                   VALUES (?, ?, ?, ?)""",
                (severity, msg, r["owner"], now),
            )

    # Auto-resolve actions for reports whose refresh is back to healthy
    resolved = _auto_resolve_recovered_refreshes(db, now)
    if resolved:
        logger.info("Auto-resolved %s recovered refresh actions", resolved)
    return created


def _auto_resolve_recovered_refreshes(db, now: str) -> int:
    """Close refresh_* actions whose report is no longer failing/overdue."""
    rows = db.execute(
        """SELECT a.id, r.pbi_refresh_status, r.pbi_refresh_schedule,
                  r.pbi_last_refresh_at
           FROM actions a
           JOIN reports r ON r.id = a.report_id
           WHERE a.source_id IS NULL
             AND a.type IN ('refresh_failed', 'refresh_overdue')
             AND a.status NOT IN ('resolved', 'expected')"""
    ).fetchall()
    closed = 0
    for row in rows:
        status_val = (row["pbi_refresh_status"] or "").lower()
        is_failed = status_val in ("failed", "disabled", "cancelled")
        is_overdue = (
            row["pbi_refresh_schedule"]
            and is_refresh_overdue(row["pbi_refresh_schedule"], row["pbi_last_refresh_at"])
        )
        if not is_failed and not is_overdue:
            db.execute(
                """UPDATE actions SET status = 'resolved', resolved_at = ?, updated_at = ?,
                      notes = COALESCE(notes, '') || ' [auto-resolved: refresh recovered]'
                   WHERE id = ?""",
                (now, now, row["id"]),
            )
            closed += 1
    return closed


def trigger_pbi_usage_sync(port: int = 8000) -> dict:
    """Launch PBI usage sync in the user's interactive session."""
    if platform.system() != "Windows":
        message = "PBI usage sync only available on Windows"
        _record_sync_run("usage", "skipped", message)
        return {"status": "skipped", "message": message}

    if not PS1_USAGE_SCRIPT.exists():
        message = f"PowerShell script not found: {PS1_USAGE_SCRIPT}"
        _record_sync_run("usage", "failed", message)
        return {"status": "error", "message": message}

    if service_principal_configured():
        return _launch_powershell_background(
            PS1_USAGE_SCRIPT,
            ["-ApiBase", f"http://localhost:{port}", "-NoPause"],
            "usage",
        )

    guard_result = _run_rdp_console_guard()
    logger.info("RDP console guard before usage sync: %s", guard_result.get("status"))
    if not guard_result.get("ready"):
        message = guard_result.get("message") or "Sync Windows session is not ready for interactive Power BI sign-in."
        _record_sync_run("usage", "failed", message, guard_result)
        return {
            "status": "error",
            "message": message,
            "auth_mode": "interactive",
            "guard": guard_result,
        }

    ps_cmd = (
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{PS1_USAGE_SCRIPT}" '
        f'-ApiBase "http://localhost:{port}" -NoPause'
    )

    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", USAGE_TASK_NAME, "/f"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["schtasks", "/create", "/tn", USAGE_TASK_NAME,
             "/tr", ps_cmd, "/sc", "once", "/st", "00:00", "/it", "/f"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        subprocess.run(
            ["schtasks", "/run", "/tn", USAGE_TASK_NAME],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        _record_sync_run("usage", "failed", f"Failed to launch usage sync: {stderr}")
        return {"status": "error", "message": f"Failed to launch usage sync: {stderr}"}
    except Exception as e:
        _record_sync_run("usage", "failed", str(e))
        return {"status": "error", "message": str(e)}

    _record_sync_run("usage", "launched", "Power BI usage sync launched with interactive scheduled task.")
    return {"status": "launched", "message": "Usage sync started - check the PowerShell window.", "auth_mode": "interactive"}
