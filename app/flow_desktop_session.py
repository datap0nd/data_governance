"""Run "Just run" Python scripts in the signed-in Windows session, as that user.

The background Flow workers are Windows services. They run in session 0,
which has no desktop, so a program that a script drives through COM (Excel,
for example) cannot start there. A script typed into PowerShell runs in the
signed-in desktop session with the account's standard (non-elevated) rights,
its mapped drives and its own environment. setup.ps1 registers the
interactive, least-privilege scheduled task ``Metronome_Python_Desktop`` for
exactly that context: the worker writes a request into a private spool
folder and asks Task Scheduler to run the task, whose launcher
(``app/flow_desktop_host.py``) checks or starts the script there. The worker
keeps everything else: live output, the process tree, the time limit and
Stop.

Worker-only; ``psutil`` is optional. The portable ``run_flow.py`` never uses
this module: an operator starts that file in their own session already.
"""
from __future__ import annotations

import locale
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

try:
    import psutil
except ImportError:  # the root process and output files still work without it
    psutil = None

from app import flow_desktop_host as host
from app import flow_python

TASK_NAME = "Metronome_Python_Desktop"
SPOOL_ENVIRONMENT = "METRONOME_PYTHON_DESKTOP_SPOOL"
SPOOL_FOLDER = ".metronome-python-desktop"
# Task Scheduler starts an interactive task within seconds while the account
# is signed in; it never starts one while nobody is.
START_TIMEOUT_SECONDS = 60
# A request folder can outlive a crashed worker; no run lasts a day.
STALE_SECONDS = 2 * 24 * 3600
# How often the worker renews a running step's heartbeat; the launcher ends
# the step when it stays silent for host.HEARTBEAT_TIMEOUT_SECONDS.
HEARTBEAT_SECONDS = 5
READ_CHUNK = 1 << 20
LINE_BYTES = 8000
LINE_CHARS = 2000
_SEPARATORS = re.compile(rb"[\r\n]+")

NOT_STARTED = (
    "Windows did not start the script in the signed-in desktop session within {seconds} seconds. "
    "Python scripts run as the signed-in BI desktop user, exactly as they would from PowerShell: "
    "make sure that account is signed in (a locked or disconnected session is fine), then run the Flow again."
)
LOST = (
    "The script stopped without reporting an exit code: its Windows session ended "
    "(for example, the account signed out) or the launcher was closed."
)


class DesktopUnavailable(RuntimeError):
    """Windows could not start the launcher in the signed-in session."""


def spool_root() -> Path:
    return Path(os.environ.get(SPOOL_ENVIRONMENT) or Path.home() / SPOOL_FOLDER)


def start_task() -> None:
    """Ask Task Scheduler to run the interactive launcher task once."""
    schtasks = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "schtasks.exe")
    try:
        completed = subprocess.run([schtasks, "/Run", "/TN", "\\" + TASK_NAME],
                                   capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopUnavailable(f"Task Scheduler could not start {TASK_NAME}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or f"exit code {completed.returncode}"
        raise DesktopUnavailable(
            f"Task Scheduler could not start {TASK_NAME} ({detail}). Update Metronome or run setup.ps1 "
            "to install the task that starts Python scripts in the signed-in Windows session."
        )


def _cleanup(spool: Path) -> None:
    cutoff = time.time() - STALE_SECONDS
    try:
        entries = list(spool.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def _alive(pid) -> bool | None:
    """Whether ``pid`` still runs; ``None`` when this worker cannot tell."""
    if not pid or psutil is None:
        return None
    try:
        process = psutil.Process(int(pid))
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode(locale.getpreferredencoding(False), errors="replace")
    return text[:LINE_CHARS]


class _Tail:
    """New lines of one output file, split the way the live pipe reader splits them."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None
        self.pending = bytearray()

    def _keep(self, segment: bytes) -> None:
        room = LINE_BYTES - len(self.pending)
        if room > 0:
            self.pending += segment[:room]

    def read(self, *, final: bool = False) -> list[str]:
        if self.handle is None:
            try:
                self.handle = open(self.path, "rb")
            except OSError:
                return []
        lines = []
        while True:
            chunk = self.handle.read(READ_CHUNK)
            if not chunk:
                break
            start = 0
            for match in _SEPARATORS.finditer(chunk):
                self._keep(chunk[start:match.start()])
                if self.pending:
                    lines.append(_decode(bytes(self.pending)))
                    self.pending.clear()
                start = match.end()
            self._keep(chunk[start:])
            if not final:
                break
        if final and self.pending:
            lines.append(_decode(bytes(self.pending)))
            self.pending.clear()
        return lines

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class DesktopProcess:
    """The Popen-like view of a script step the launcher started in the session."""

    def __init__(self, directory: Path, command, started: dict):
        self.directory = directory
        self.args = list(command)
        self.pid = int(started["pid"])
        self.host_pid = started.get("host_pid")
        self.returncode = None

    def poll(self):
        if self.returncode is None:
            exited = host.read_json(self.directory / host.EXITED)
            if isinstance(exited, dict):
                self.returncode = int(exited.get("returncode", -1))
        return self.returncode

    def output_closed(self) -> bool:
        return (self.directory / host.DONE).exists()

    def kill(self) -> None:
        if psutil is not None:
            try:
                psutil.Process(self.pid).kill()
            except psutil.Error:
                pass
        elif os.name != "nt":
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass

    def wait(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if end is not None and time.monotonic() >= end:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.05)
        return self.returncode


class DesktopSession:
    """Check and run Python-script steps in the signed-in Windows session."""

    def __init__(self, spool: Path | None = None, *, start=None, env_file=None,
                 start_timeout: float = START_TIMEOUT_SECONDS, poll_seconds: float = .1):
        self.spool = Path(spool) if spool is not None else spool_root()
        self.start = start or start_task
        self.env_file = str(env_file) if env_file else None
        self.start_timeout = start_timeout
        self.poll_seconds = poll_seconds

    # --- request lifecycle -------------------------------------------------

    def _submit(self, kind: str, payload: dict) -> Path:
        self.spool.mkdir(parents=True, exist_ok=True)
        _cleanup(self.spool)
        now = time.time()
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}"
        staging = self.spool / f".tmp-{name}"
        staging.mkdir()
        (staging / host.HEARTBEAT).touch()
        host.write_json(staging / host.REQUEST, {
            "schema": host.SCHEMA, "version": host.VERSION, "kind": kind,
            "created_at": now, "expires_at": now + self.start_timeout,
            "env_file": self.env_file, **payload,
        })
        directory = self.spool / name
        os.replace(staging, directory)  # a launcher never sees a half-written request
        return directory

    @staticmethod
    def _cancel(directory: Path) -> bool:
        """Cancel an unclaimed request; ``False`` when a launcher already owns it."""
        try:
            descriptor = os.open(directory / host.CLAIM, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError:
            return True  # the folder is gone, so no launcher can claim it
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("cancelled")
        return True

    @staticmethod
    def _withdraw(directory: Path) -> None:
        """Tell the launcher that owns the request not to start its script."""
        try:
            (directory / host.WITHDRAWN).touch()
        except OSError:
            pass

    @staticmethod
    def _discard(directory: Path) -> None:
        # Windows can hold a just-closed file for a moment; a folder that still
        # cannot go is removed by the cleanup of later requests.
        for _attempt in range(5):
            shutil.rmtree(directory, ignore_errors=True)
            if not directory.exists():
                return
            time.sleep(.1)

    def _launch(self, kind: str, payload: dict, *, deadline: float | None = None,
                timeout_seconds: float | None = None, stop_requested=None) -> Path:
        """Submit a request, start the task and wait until a launcher owns the request."""
        directory = self._submit(kind, payload)
        try:
            self.start()
        except BaseException:
            self._cancel(directory)
            self._discard(directory)
            raise
        start_deadline = time.monotonic() + self.start_timeout
        while not (directory / host.CLAIM).exists():
            now = time.monotonic()
            failure = None
            if stop_requested is not None and stop_requested():
                failure = RuntimeError("Python script run was stopped.")
            elif deadline is not None and now >= deadline:
                failure = TimeoutError(f"Python script exceeded its {round(timeout_seconds or 0)} second time limit.")
            elif now >= start_deadline:
                failure = DesktopUnavailable(NOT_STARTED.format(seconds=round(self.start_timeout)))
            if failure is not None:
                if self._cancel(directory):
                    self._discard(directory)
                    raise failure
                # A launcher claimed it at this very moment. Follow it, unless
                # Stop or the time limit means it must not start at all.
                if not isinstance(failure, DesktopUnavailable):
                    self._withdraw(directory)
                break
            time.sleep(self.poll_seconds)
        return directory

    def _wait_for(self, directory: Path, names: tuple[str, ...], *, deadline: float, stop_requested=None):
        """The first of ``names`` a launcher writes, or ``None`` when it never answers."""
        while True:
            for name in names:
                data = host.read_json(directory / name)
                if isinstance(data, dict):
                    return name, data
            if stop_requested is not None and stop_requested():
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_seconds)

    # --- public API ----------------------------------------------------------

    def verify(self, scripts, interpreter=None, *, stop_requested=None) -> dict:
        """Confirm every script and choose Python as the signed-in session sees them.

        Nothing runs. Mapped drives exist only in that session, so the
        worker service cannot answer this itself.
        """
        directory = self._launch("verify", {
            "scripts": [str(item) for item in scripts], "interpreter": str(interpreter or ""),
        }, stop_requested=stop_requested)
        try:
            answer = self._wait_for(directory, (host.RESULT,),
                                    deadline=time.monotonic() + self.start_timeout,
                                    stop_requested=stop_requested)
        finally:
            self._discard(directory)
        if answer is None:
            if stop_requested is not None and stop_requested():
                raise RuntimeError("Python script run was stopped.")
            raise DesktopUnavailable("The launcher in the signed-in Windows session did not report back.")
        result = answer[1]
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return {"interpreter": str(result["interpreter"]),
                "interpreter_reason": str(result.get("interpreter_reason") or ""),
                "checksums": {str(key): str(value) for key, value in (result.get("checksums") or {}).items()}}

    def run(self, command, *, cwd, variables: dict, timeout_seconds: float, observer=None,
            on_line=None, on_tick=None, stop_requested=None, script=None,
            on_start=None) -> subprocess.CompletedProcess:
        """Run one step there and follow it like ``flow_python.run_process``.

        ``variables`` are only what Metronome sets for the step: the script
        otherwise keeps the signed-in session's own environment and the
        project .env, as it would in a PowerShell window. The run waits for
        the script and every tracked process it started; a time limit or
        Stop ends the whole tree. The launcher hashes ``script`` just before
        starting it and passes that, with the process IDs, to ``on_start``.
        A heartbeat thread tells the launcher this worker is still in charge;
        without it the launcher ends the step itself.
        """
        deadline = time.monotonic() + timeout_seconds
        directory = self._launch("run", {
            "command": [str(item) for item in command], "cwd": str(cwd),
            "variables": {str(key): str(value) for key, value in (variables or {}).items()},
            "script": str(script) if script is not None else None,
            "deadline": time.time() + timeout_seconds,
        }, deadline=deadline, timeout_seconds=timeout_seconds, stop_requested=stop_requested)
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(HEARTBEAT_SECONDS):
                try:
                    os.utime(directory / host.HEARTBEAT)
                except OSError:
                    pass

        beating = threading.Thread(target=heartbeat, daemon=True)
        beating.start()
        try:
            return self._follow(directory, command, deadline, timeout_seconds, observer,
                                on_line, on_tick, stop_requested, on_start)
        finally:
            stopped.set()
            beating.join(timeout=1)
            self._discard(directory)

    def _follow(self, directory, command, deadline, timeout_seconds, observer, on_line, on_tick,
                stop_requested, on_start=None):
        answer = self._wait_for(directory, (host.STARTED, host.RESULT),
                                deadline=min(deadline, time.monotonic() + self.start_timeout),
                                stop_requested=stop_requested)
        if answer is None or answer[0] == host.RESULT:
            if answer is not None:
                raise OSError((answer[1] or {}).get("error") or "The launcher could not start the script.")
            # Gave up before the start: withdraw, then end a start that raced it.
            self._withdraw(directory)
            late = self._wait_for(directory, (host.STARTED,), deadline=time.monotonic() + 5)
            if late is not None:
                flow_python._kill_process_tree(DesktopProcess(directory, command, late[1]), None)
            if stop_requested is not None and stop_requested():
                raise RuntimeError("Python script run was stopped.")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Python script exceeded its {round(timeout_seconds)} second time limit.")
            raise DesktopUnavailable("The launcher in the signed-in Windows session did not start the script.")
        process = DesktopProcess(directory, command, answer[1])
        if on_start is not None:
            on_start(answer[1])
        if observer is not None and hasattr(observer, "start"):
            observer.start(process)
        tails = {"stdout": _Tail(directory / host.STDOUT), "stderr": _Tail(directory / host.STDERR)}
        texts = {"stdout": "", "stderr": ""}

        def deliver(stream, lines):
            for line in lines:
                if on_line is not None:
                    sanitized = on_line(stream, line, process.pid)
                    if isinstance(sanitized, str):
                        line = sanitized
                texts[stream] = (texts[stream] + line + "\n")[-flow_python.OUTPUT_TAIL_CHARS:]

        last_tick = 0.0
        lost_since = None
        try:
            while True:
                for stream, tail in tails.items():
                    deliver(stream, tail.read())
                now = time.monotonic()
                if now - last_tick >= 1:
                    if observer is not None:
                        observer.sample()
                    if on_tick is not None:
                        on_tick(process, observer)
                    last_tick = now
                if stop_requested is not None and stop_requested():
                    raise RuntimeError("Python script run was stopped.")
                if now >= deadline:
                    raise TimeoutError(f"Python script exceeded its {round(timeout_seconds)} second time limit.")
                exited = process.poll() is not None
                closed = process.output_closed() or (exited and _alive(process.host_pid) is False)
                if not exited and _alive(process.pid) is False:
                    lost_since = lost_since if lost_since is not None else now
                    if now - lost_since > 3 and process.poll() is None:
                        raise RuntimeError(LOST)
                else:
                    lost_since = None
                descendants = (observer.running_descendants() if observer is not None and
                               hasattr(observer, "running_descendants") else [])
                if exited and closed and not descendants:
                    for stream, tail in tails.items():
                        deliver(stream, tail.read(final=True))
                    break
                time.sleep(self.poll_seconds)
            return subprocess.CompletedProcess(list(command), process.returncode, texts["stdout"], texts["stderr"])
        except (TimeoutError, RuntimeError):
            flow_python._kill_process_tree(process, observer)
            raise
        finally:
            if observer is not None and hasattr(observer, "sample"):
                observer.sample()
            for tail in tails.values():
                tail.close()
