"""Launch the Flows browser worker in the BI desktop user's session."""

from __future__ import annotations

import platform
import subprocess
import os
import sys
from datetime import datetime
from pathlib import Path


TASK_NAME = "DG_Flow_Worker"
WORKER_ID = "bi-desktop"
WORKER_NAME = "BI desktop"
_worker_process: subprocess.Popen | None = None
_worker_log_handle = None


def launch_local_worker() -> dict:
    """Ensure a resident worker child process is running on this machine."""
    global _worker_process, _worker_log_handle
    if platform.system() != "Windows":
        return {"status": "skipped", "mode": "local", "message": "Local flow execution is Windows-only."}

    code_dir = Path(__file__).resolve().parent.parent
    headed = os.environ.get("DG_FLOW_HEADED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    if _worker_process is not None and _worker_process.poll() is None:
        return {
            "status": "starting", "mode": "local", "worker_id": WORKER_ID,
            "display_name": WORKER_NAME, "headed": headed, "pid": _worker_process.pid,
        }

    if _worker_log_handle is not None:
        _worker_log_handle.close()
        _worker_log_handle = None

    log_dir = code_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "flow_worker.log"
    _worker_log_handle = log_path.open("a", encoding="utf-8", buffering=1)
    _worker_log_handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Starting Metronome flow worker.\n")

    command = [
        sys.executable, "-u", "-m", "app.flow_worker",
        "--server", "http://127.0.0.1:8000",
        "--worker-id", WORKER_ID,
        "--name", WORKER_NAME,
    ]
    if headed:
        command.append("--headed")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(code_dir)
    creationflags = 0 if headed else getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        _worker_process = subprocess.Popen(
            command, cwd=code_dir, env=environment, stdin=subprocess.DEVNULL,
            stdout=_worker_log_handle, stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except Exception as exc:
        _worker_log_handle.write(f"Worker launch failed: {exc}\n")
        return {"status": "error", "mode": "local", "message": str(exc), "log_path": str(log_path)}
    return {
        "status": "starting", "mode": "local", "worker_id": WORKER_ID,
        "display_name": WORKER_NAME, "headed": headed, "pid": _worker_process.pid,
        "log_path": str(log_path),
    }
