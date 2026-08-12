"""Launch the Flows browser worker in the BI desktop user's session."""

from __future__ import annotations

import platform
import subprocess


SERVICE_NAME = "MXFlowsWorker"
WORKER_ID = "bi-desktop"
WORKER_NAME = "BI desktop"


def launch_local_worker() -> dict:
    """Ask Windows to start the preinstalled headless worker service.

    Metronome itself runs in service session 0. A child process launched by the
    service cannot use browser credentials protected for the signed-in desktop
    session, even when both processes name the same Windows account. The setup
    setup installs ``MXFlowsWorker`` under that same Windows account.
    """
    if platform.system() != "Windows":
        return {"status": "skipped", "mode": "local", "message": "Local flow execution is Windows-only."}
    try:
        completed = subprocess.run(
            ["sc.exe", "start", SERVICE_NAME],
            capture_output=True, text=True, timeout=15,
        )
        detail = (completed.stderr or completed.stdout or "").strip()
        # Windows returns 1056 when the service is already running. That is a
        # successful outcome for this idempotent ensure operation.
        if completed.returncode != 0 and "1056" not in detail:
            detail = detail or "Windows rejected the worker service start request."
            return {
                "status": "error", "mode": "windows_service", "worker_id": WORKER_ID,
                "message": detail,
            }
    except Exception as exc:
        return {"status": "error", "mode": "windows_service", "worker_id": WORKER_ID, "message": str(exc)}
    return {
        "status": "starting", "mode": "windows_service", "worker_id": WORKER_ID,
        "display_name": WORKER_NAME,
    }
