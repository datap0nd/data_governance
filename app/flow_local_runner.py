"""Launch the Flows browser worker in the BI desktop user's session."""

from __future__ import annotations

import os
import platform
import subprocess

from app import flow_capacity


SERVICE_NAME = "MXFlowsWorker"
HEADED_TASK_NAME = "Metronome_Flows_Headed"
HEADED_TASK_PATH = rf"\{HEADED_TASK_NAME}"
WORKER_ID = "bi-desktop-headless"
HEADED_WORKER_ID = "bi-desktop-headed"
WORKER_NAME = "BI desktop - headless"


def launch_local_worker(browser_mode: str = "headless", *, slot: int = 1) -> dict:
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
    worker_id = HEADED_WORKER_ID if browser_mode == "headed" else flow_capacity.worker_id(slot)
    display_name = "BI desktop - headed" if browser_mode == "headed" else WORKER_NAME
    schtasks = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "schtasks.exe")
    command = [schtasks, "/Run", "/TN", HEADED_TASK_PATH] if browser_mode == "headed" else ["sc.exe", "start", flow_capacity.service_name(slot)]
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


def stop_local_worker(browser_mode: str, process_id: int | None, *, worker_id: str | None = None) -> dict:
    """Terminate the exact worker assigned to a running flow."""
    if platform.system() != "Windows":
        return {"status": "skipped", "message": "Flow worker termination is Windows-only."}
    if browser_mode not in {"headless", "headed"}:
        return {"status": "error", "message": f"Unsupported browser mode: {browser_mode}."}
    try:
        if isinstance(process_id, int) and process_id > 0:
            completed = subprocess.run(
                ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
                capture_output=True, text=True, timeout=15,
            )
            detail = (completed.stdout or completed.stderr or "").strip()
            if completed.returncode != 0:
                return {"status": "error", "message": detail or "Windows could not stop the flow worker."}
            return {"status": "stopped", "process_id": process_id, "message": detail}
        if browser_mode == "headed":
            if worker_id and worker_id != HEADED_WORKER_ID:
                return {"status": "error", "message": "An exact process ID is required for this headed worker."}
            schtasks = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "schtasks.exe")
            completed = subprocess.run(
                [schtasks, "/End", "/TN", HEADED_TASK_PATH],
                capture_output=True, text=True, timeout=15,
            )
            detail = (completed.stdout or completed.stderr or "").strip()
            if completed.returncode != 0:
                return {"status": "error", "message": detail or "Windows could not end the headed worker task."}
            return {"status": "stopped", "process_id": None, "message": detail}
        slot = flow_capacity.slot_number(worker_id or WORKER_ID)
        if slot is None:
            return {"status": "error", "message": "The assigned worker has no known local service; an exact process ID is required."}
        completed = subprocess.run(
            ["sc.exe", "stop", flow_capacity.service_name(slot)],
            capture_output=True, text=True, timeout=15,
        )
        detail = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return {"status": "error", "message": detail or "Windows could not stop the headless worker service."}
        return {"status": "stopped", "process_id": None, "message": detail}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
