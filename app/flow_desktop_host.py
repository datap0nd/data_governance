"""Launcher that Task Scheduler runs in the signed-in Windows session.

setup.ps1 registers it as the interactive, least-privilege scheduled task
``Metronome_Python_Desktop`` (``pythonw.exe flow_desktop_host.py <spool>``).
Each start claims the oldest pending request a Flow worker wrote into the
spool folder (see ``app/flow_desktop_session.py``) and either checks a Flow's
scripts or starts one script step the way PowerShell would: in this session,
with this account's standard rights and environment plus the project .env,
in the script's folder, with stdin closed. The step's output is relayed into
files that the worker follows. The launcher records when the script exits
and when every process holding its output has finished. It also ends the
step itself once the Flow's time limit has passed or the worker has stopped
renewing the request's heartbeat, so a script never outlives a crashed or
restarted worker.

It runs under the embedded pythonw.exe, without a console, and never prints.
Beyond the standard library it uses only the worker's optional process-tree
observer (psutil) to end processes the script started.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_CODE_DIR = Path(__file__).resolve().parent.parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from app import env_file, flow_python  # noqa: E402

try:
    import psutil
    from app.flow_process_tree import ProcessTreeObserver
except ImportError:  # the root process can still be ended without them
    psutil = None
    ProcessTreeObserver = None

SCHEMA = "metronome-desktop-request"
VERSION = 1
# One folder per request; the worker creates it with request.json already
# written, then each side adds only its own markers.
REQUEST = "request.json"
CLAIM = "claim"            # O_EXCL: "host <pid>" from a launcher or "cancelled" from the worker
WITHDRAWN = "withdrawn"    # the worker gave up after the claim; do not start the script
RESULT = "result.json"     # a check's result, or why the script could not start
STARTED = "started.json"   # {"pid", "host_pid"} once the script runs
EXITED = "exit.json"       # {"returncode"} once the script itself exits
DONE = "done.json"         # {"returncode"} once no process holds its output any more
STDOUT = "stdout.log"
STDERR = "stderr.log"
HEARTBEAT = "heartbeat"    # the worker renews its modification time while it follows the step
# The worker renews the heartbeat every few seconds from its own thread; this
# long a silence means it is gone (crashed, stopped or restarted).
HEARTBEAT_TIMEOUT_SECONDS = 120
# The worker ends a step at the Flow's time limit itself; the launcher is the
# backstop for a worker that can no longer do it.
DEADLINE_GRACE_SECONDS = 30
# After ending a step, how long to wait for output still held by processes
# outside the script's tree before recording that the step is done.
FORCED_OUTPUT_WAIT_SECONDS = 30


def write_json(path: Path, data: dict) -> None:
    """Replace ``path`` atomically so a reader never sees half a marker."""
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _names(values: dict) -> dict:
    # Windows variable names ignore case; os.environ.copy() upper-cases them there.
    return {(str(key).upper() if os.name == "nt" else str(key)): str(value)
            for key, value in (values or {}).items()}


def session_environment(request: dict) -> dict:
    """This session's own environment, the project .env, then the step's variables."""
    environment = os.environ.copy()
    if request.get("env_file"):
        values, _status = env_file.read(Path(str(request["env_file"])))
        environment.update(_names(values))
    return flow_python.run_environment(environment, _names(request.get("variables") or {}))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def claim(spool: Path):
    """The oldest pending, unexpired request, now owned by this launcher, or ``None``."""
    try:
        entries = sorted(entry for entry in spool.iterdir()
                         if entry.is_dir() and not entry.name.startswith("."))
    except OSError:
        return None
    now = time.time()
    for entry in entries:
        request = read_json(entry / REQUEST)
        if not isinstance(request, dict) or request.get("schema") != SCHEMA or request.get("version") != VERSION:
            continue
        try:
            if float(request.get("expires_at") or 0) < now:
                continue
        except (TypeError, ValueError):
            continue
        try:
            descriptor = os.open(entry / CLAIM, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            continue  # another launcher owns it, or the worker withdrew it
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"host {os.getpid()}")
        return entry, request
    return None


def verify(request: dict) -> dict:
    """Check the scripts and choose Python as this session sees them; nothing runs."""
    environment = session_environment(request)
    names = [str(item) for item in request.get("scripts") or []]
    flow_python.check_scripts([Path(name) for name in names])
    interpreter, reason = flow_python.resolve_interpreter(request.get("interpreter") or None, environment)
    return {"interpreter": interpreter, "interpreter_reason": reason,
            "checksums": {name: _sha256(Path(name)) for name in names}}


def _relay(pipe, target) -> None:
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                view = view[target.write(view):]
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _heartbeat_age(directory: Path) -> float:
    try:
        return max(0.0, time.time() - (directory / HEARTBEAT).stat().st_mtime)
    except OSError:
        return float("inf")


def _overdue(directory: Path, request: dict) -> str | None:
    """Why the launcher must end the step itself, or ``None`` while its worker is in charge."""
    try:
        deadline = float(request["deadline"])
    except (KeyError, TypeError, ValueError):
        deadline = None
    if deadline is not None and time.time() > deadline + DEADLINE_GRACE_SECONDS:
        return "time limit"
    if _heartbeat_age(directory) > HEARTBEAT_TIMEOUT_SECONDS:
        return "worker lost"
    return None


def _orphans(pid: int, started_at: float) -> list:
    """Processes the script started that outlived it (Windows keeps their parent ID)."""
    found = []
    try:
        for candidate in psutil.process_iter(["ppid", "create_time"]):
            if candidate.info["ppid"] == pid and (candidate.info["create_time"] or 0) >= started_at - 1:
                found.append(candidate)
                try:
                    found.extend(candidate.children(recursive=True))
                except psutil.Error:
                    pass
    except psutil.Error:
        pass
    return found


def _end_tree(process, observer, started_at: float) -> None:
    """End the script, what it started, and every process the observer saw it start."""
    if observer is not None:
        observer.kill_all()
    if psutil is not None:
        for orphan in _orphans(process.pid, started_at):
            try:
                orphan.kill()
            except psutil.Error:
                pass
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                           timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def run(directory: Path, request: dict) -> None:
    """Start one step, relay its output, and record its exit and the end of its output."""
    command = [str(item) for item in request.get("command") or []]
    if not command:
        raise RuntimeError("The launcher request names no command.")
    environment = session_environment(request)
    if (directory / WITHDRAWN).exists():
        return
    checksum = None
    if request.get("script"):
        # The run history records the exact script this step runs, read here
        # at the start, as the worker does for a script it starts itself.
        script = Path(str(request["script"]))
        try:
            checksum = _sha256(script)
        except OSError:
            write_json(directory / RESULT, {"error": f"Python script does not exist or is not readable: {script}"})
            return
    outputs = [open(directory / STDOUT, "wb", buffering=0), open(directory / STDERR, "wb", buffering=0)]
    ended_by = None
    try:
        try:
            process = subprocess.Popen(
                command, cwd=str(request.get("cwd") or directory), env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                # A console program started by pythonw would otherwise open a
                # visible console window; this gives it a hidden one instead.
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                               getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            write_json(directory / RESULT, {"error": str(exc) or type(exc).__name__})
            return
        started_at = time.time()
        observer = ProcessTreeObserver() if ProcessTreeObserver is not None else None
        if observer is not None:
            observer.start(process)
        write_json(directory / STARTED, {"pid": process.pid, "host_pid": os.getpid(),
                                         "started_at": started_at, "checksum": checksum})
        relays = [threading.Thread(target=_relay, args=(pipe, target), daemon=True)
                  for pipe, target in ((process.stdout, outputs[0]), (process.stderr, outputs[1]))]
        for relay in relays:
            relay.start()
        while True:
            try:
                returncode = process.wait(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                pass
            if observer is not None:
                observer.sample()
            ended_by = _overdue(directory, request)
            if ended_by:
                _end_tree(process, observer, started_at)
                returncode = process.wait()
                break
        write_json(directory / EXITED, {"returncode": returncode, "ended_by": ended_by})
        # Descendants that inherited the output keep it open; the worker
        # waits for them exactly as it waits for a pipe it reads itself. The
        # same time limit and heartbeat still apply while it waits.
        forced_at = time.monotonic() if ended_by else None
        while any(relay.is_alive() for relay in relays):
            for relay in relays:
                relay.join(timeout=.25)
            if observer is not None:
                observer.sample()
            if forced_at is None:
                ended_by = _overdue(directory, request)
                if ended_by:
                    _end_tree(process, observer, started_at)
                    forced_at = time.monotonic()
            elif time.monotonic() - forced_at > FORCED_OUTPUT_WAIT_SECONDS:
                break  # a process outside the tree still holds the output
    finally:
        for handle in outputs:
            try:
                handle.close()
            except OSError:
                pass
    write_json(directory / DONE, {"returncode": returncode, "ended_by": ended_by})


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        return 2
    claimed = claim(Path(arguments[0]))
    if claimed is None:
        return 0
    directory, request = claimed
    try:
        if request.get("kind") == "verify":
            write_json(directory / RESULT, verify(request))
        elif request.get("kind") == "run":
            run(directory, request)
        else:
            write_json(directory / RESULT, {"error": "The launcher does not support this request."})
    except Exception as exc:  # the worker reports it; pythonw has no console
        try:
            write_json(directory / RESULT, {"error": str(exc) or type(exc).__name__})
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
