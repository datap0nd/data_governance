"""Launch the Flows browser worker in the BI desktop user's session."""

from __future__ import annotations

import platform
import subprocess
import os
from pathlib import Path


TASK_NAME = "DG_Flow_Worker"
WORKER_ID = "bi-desktop"
WORKER_NAME = "BI desktop"


def launch_local_worker() -> dict:
    """Ensure the resident worker task is installed and started locally."""
    if platform.system() != "Windows":
        return {"status": "skipped", "mode": "local", "message": "Local flow execution is Windows-only."}

    code_dir = Path(__file__).resolve().parent.parent
    launcher = code_dir / "tools" / "run_flow_worker.ps1"
    if not launcher.exists():
        return {"status": "error", "mode": "local", "message": f"Worker launcher not found: {launcher}"}
    headed = os.environ.get("DG_FLOW_HEADED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{launcher}"'
    if headed:
        command += " -Headed"

    created = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/tr", command, "/sc", "onlogon", "/it", "/f"],
        cwd=code_dir,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if created.returncode != 0:
        return {
            "status": "error", "mode": "local",
            "message": (created.stderr or created.stdout or "Could not create the BI desktop worker task.").strip(),
        }

    started = subprocess.run(
        ["schtasks", "/run", "/tn", TASK_NAME],
        cwd=code_dir,
        capture_output=True,
        text=True,
        timeout=20,
    )
    message = (started.stderr or started.stdout or "").strip()
    if started.returncode != 0 and "already running" not in message.casefold():
        return {"status": "error", "mode": "local", "message": message or "Could not start the BI desktop worker task."}
    return {
        "status": "launched", "mode": "local", "worker_id": WORKER_ID,
        "display_name": WORKER_NAME, "headed": headed,
    }
