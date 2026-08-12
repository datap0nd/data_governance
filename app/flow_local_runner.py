"""Launch the Flows browser worker in the BI desktop user's session."""

from __future__ import annotations

import platform
import subprocess


TASK_NAME = "DG_Flow_Worker"
WORKER_ID = "bi-desktop"
WORKER_NAME = "BI desktop"


def launch_local_worker() -> dict:
    """Start the preinstalled interactive worker task on Windows.

    Metronome itself runs in service session 0. A child process launched by the
    service cannot use browser credentials protected for the signed-in desktop
    session, even when both processes name the same Windows account. The setup
    script therefore installs ``DG_Flow_Worker`` as an interactive task, and
    the service only asks Task Scheduler to run it.
    """
    if platform.system() != "Windows":
        return {"status": "skipped", "mode": "local", "message": "Local flow execution is Windows-only."}
    try:
        completed = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", TASK_NAME],
            capture_output=True, text=True, timeout=15,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Task Scheduler rejected the request.").strip()
            return {
                "status": "error", "mode": "interactive_task", "worker_id": WORKER_ID,
                "message": detail,
            }
    except Exception as exc:
        return {"status": "error", "mode": "interactive_task", "worker_id": WORKER_ID, "message": str(exc)}
    return {
        "status": "starting", "mode": "interactive_task", "worker_id": WORKER_ID,
        "display_name": WORKER_NAME,
    }
