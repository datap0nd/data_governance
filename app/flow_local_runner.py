"""Launch the Flows browser worker in the BI desktop user's session."""

from __future__ import annotations

import os
import platform
import subprocess


SERVICE_NAME = "MXFlowsWorker"
HEADED_TASK_NAME = "Metronome_Flows_Headed"
HEADED_TASK_PATH = rf"\{HEADED_TASK_NAME}"
WORKER_ID = "bi-desktop-headless"
HEADED_WORKER_ID = "bi-desktop-headed"
WORKER_NAME = "BI desktop - headless"


def launch_local_worker(browser_mode: str = "headless") -> dict:
    """Start the installed worker that matches a flow's browser mode.

    Metronome itself runs in service session 0. A child process launched by the
    API cannot show a browser in the signed-in desktop. Setup therefore installs
    a background service for headless work and an on-demand interactive task
    for headed work.
    """
    if platform.system() != "Windows":
        return {"status": "skipped", "mode": "local", "message": "Local flow execution is Windows-only."}
    if browser_mode not in {"headless", "headed"}:
        return {"status": "error", "mode": browser_mode, "message": "Unsupported browser mode."}
    worker_id = HEADED_WORKER_ID if browser_mode == "headed" else WORKER_ID
    display_name = "BI desktop - headed" if browser_mode == "headed" else WORKER_NAME
    schtasks = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "schtasks.exe")
    command = [schtasks, "/Run", "/TN", HEADED_TASK_PATH] if browser_mode == "headed" else ["sc.exe", "start", SERVICE_NAME]
    try:
        completed = subprocess.run(
            command,
            capture_output=True, text=True, timeout=15,
        )
        detail = (completed.stderr or completed.stdout or "").strip()
        # Windows returns 1056 when the service is already running. That is a
        # successful outcome for this idempotent ensure operation.
        if completed.returncode != 0 and not (browser_mode == "headless" and "1056" in detail):
            detail = detail or "Windows rejected the worker service start request."
            return {
                "status": "error", "mode": browser_mode, "worker_id": worker_id,
                "message": detail,
            }
    except Exception as exc:
        return {"status": "error", "mode": browser_mode, "worker_id": worker_id, "message": str(exc)}
    return {
        "status": "starting", "mode": browser_mode, "worker_id": worker_id,
        "display_name": display_name,
    }


def stop_headed_worker(process_id: int | None) -> dict:
    """Terminate only the exact interactive worker process tree for a run."""
    if platform.system() != "Windows":
        return {"status": "skipped", "message": "Headed worker termination is Windows-only."}
    try:
        if not isinstance(process_id, int) or process_id <= 0:
            schtasks = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "schtasks.exe")
            completed = subprocess.run(
                [schtasks, "/End", "/TN", HEADED_TASK_PATH],
                capture_output=True, text=True, timeout=15,
            )
            detail = (completed.stdout or completed.stderr or "").strip()
            if completed.returncode != 0:
                return {"status": "error", "message": detail or "Windows could not end the headed worker task."}
            return {"status": "stopped", "process_id": None, "message": detail}
        completed = subprocess.run(
            ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
        )
        detail = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return {"status": "error", "message": detail or "Windows could not stop the headed worker."}
        return {"status": "stopped", "process_id": process_id, "message": detail}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
