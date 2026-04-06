"""Power BI refresh schedule sync - runs PowerShell to fetch data from PBI Service."""

import json
import logging
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR, PBI_WORKSPACE
from app.database import get_db

logger = logging.getLogger(__name__)

PS1_SCRIPT = BASE_DIR / "tools" / "pbi_refresh_sync.ps1"


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


def run_pbi_sync(workspace: str | None = None, on_progress=None) -> dict:
    """Run the PBI refresh sync.

    Shells out to PowerShell, reads the JSON output, and updates the reports table.
    Returns a summary dict.
    """
    if platform.system() != "Windows":
        msg = "PBI sync only available on Windows (requires PowerShell + MicrosoftPowerBIMgmt)"
        logger.warning(msg)
        return {"status": "skipped", "message": msg}

    ws_name = workspace or PBI_WORKSPACE
    if not ws_name:
        return {"status": "error", "message": "No PBI workspace configured (set DG_PBI_WORKSPACE)"}

    if not PS1_SCRIPT.exists():
        return {"status": "error", "message": f"PowerShell script not found: {PS1_SCRIPT}"}

    if on_progress:
        on_progress(f"Starting PBI sync for workspace: {ws_name}")

    # Run PowerShell script
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            "powershell", "-ExecutionPolicy", "Bypass", "-File",
            str(PS1_SCRIPT),
            "-WorkspaceName", ws_name,
            "-OutputPath", tmp_path,
        ]
        if on_progress:
            on_progress("Running PowerShell script...")

        # No capture_output - let PowerShell run in a visible console
        # so the browser login popup can appear. Output goes to the JSON file.
        creation = subprocess.CREATE_NEW_CONSOLE if platform.system() == "Windows" else 0
        result = subprocess.run(cmd, timeout=120, creationflags=creation)

        if result.returncode != 0:
            if on_progress:
                on_progress("PowerShell script failed")
            return {
                "status": "error",
                "message": "PowerShell script failed - login may have been cancelled or timed out",
            }

        if on_progress:
            on_progress("Reading PBI data...")

        # Parse JSON output
        with open(tmp_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "PowerShell script timed out (120s)"}
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"Invalid JSON from PowerShell: {e}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Update reports in DB
    reports_data = data.get("reports") or []
    matched = 0
    unmatched = []
    log_lines = []

    with get_db() as db:
        for entry in reports_data:
            report_name = entry.get("report_name")
            if not report_name:
                continue

            # Match by report name
            row = db.execute(
                "SELECT id FROM reports WHERE name = ?", (report_name,)
            ).fetchone()

            if not row:
                unmatched.append(report_name)
                log_lines.append(f"SKIP: {report_name} (not in DB)")
                continue

            report_id = row["id"]
            schedule = entry.get("schedule") or {}
            last_refresh = entry.get("last_refresh") or {}

            schedule_str = _build_schedule_string(schedule)
            last_refresh_at = last_refresh.get("end_time") or last_refresh.get("start_time")
            refresh_status = last_refresh.get("status")
            refresh_error = last_refresh.get("error")

            # Truncate long error messages
            if refresh_error and len(refresh_error) > 500:
                refresh_error = refresh_error[:500] + "..."

            db.execute(
                """UPDATE reports SET
                    pbi_dataset_id = ?,
                    pbi_refresh_schedule = ?,
                    pbi_last_refresh_at = ?,
                    pbi_refresh_status = ?,
                    pbi_refresh_error = ?,
                    updated_at = ?
                WHERE id = ?""",
                (
                    entry.get("dataset_id"),
                    schedule_str,
                    last_refresh_at,
                    refresh_status,
                    refresh_error,
                    datetime.now(timezone.utc).isoformat(),
                    report_id,
                ),
            )
            matched += 1
            status_str = refresh_status or "no history"
            log_lines.append(f"OK: {report_name} - {status_str} ({schedule_str or 'no schedule'})")

    if on_progress:
        on_progress(f"Done: {matched} matched, {len(unmatched)} unmatched")

    summary = {
        "status": "completed",
        "workspace": ws_name,
        "synced_at": data.get("synced_at"),
        "total_pbi_reports": len(reports_data),
        "matched": matched,
        "unmatched": unmatched,
        "log": "\n".join(log_lines),
    }
    logger.info("PBI sync completed: %s matched, %s unmatched", matched, len(unmatched))
    return summary
