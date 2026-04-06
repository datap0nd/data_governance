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
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR, PBI_WORKSPACE
from app.database import get_db

logger = logging.getLogger(__name__)

PS1_SCRIPT = BASE_DIR / "tools" / "pbi_refresh_sync.ps1"
TASK_NAME = "DG_PBI_Sync"


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
        return {"status": "skipped", "message": "PBI sync only available on Windows"}

    ws_name = workspace or PBI_WORKSPACE
    if not ws_name:
        return {"status": "error", "message": "No PBI workspace configured (set DG_PBI_WORKSPACE)"}

    if not PS1_SCRIPT.exists():
        return {"status": "error", "message": f"PowerShell script not found: {PS1_SCRIPT}"}

    # Build the command the scheduled task will run
    ps_cmd = (
        f'powershell -ExecutionPolicy Bypass -File "{PS1_SCRIPT}" '
        f'-WorkspaceName "{ws_name}" -ApiBase "http://localhost:{port}"'
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
        return {"status": "error", "message": f"Failed to launch PBI sync task: {stderr}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {
        "status": "launched",
        "message": "PBI sync started - a PowerShell window should appear on your desktop. Log in if prompted.",
    }


def import_pbi_data(data: dict) -> dict:
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

    summary = {
        "status": "completed",
        "workspace": data.get("workspace"),
        "synced_at": data.get("synced_at"),
        "total_pbi_reports": len(reports_data),
        "matched": matched,
        "unmatched": unmatched,
        "archived": archived_count,
        "log": "\n".join(log_lines),
    }
    logger.info("PBI sync completed: %s matched, %s unmatched, %s archived", matched, len(unmatched), archived_count)
    return summary
