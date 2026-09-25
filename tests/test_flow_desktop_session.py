"""Synthetic tests for starting "Just run" Python scripts in the signed-in session.

Task Scheduler is replaced by starting the real launcher
(``app/flow_desktop_host.py``) as a separate process with its own "session"
environment, which is what the interactive task does on the work PC. No live
system, scheduled task or desktop session is used.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from app import flow_desktop_host as host
from app import flow_desktop_session, flow_python, flow_worker
from app.flow_desktop_session import DesktopSession, DesktopUnavailable
from app.flow_process_tree import ProcessTreeObserver

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "app" / "flow_desktop_host.py"


class _TaskScheduler:
    """Start the launcher the way the interactive scheduled task would."""

    def __init__(self, spool: Path, **session_variables):
        self.spool = spool
        self.environment = {key: value for key, value in os.environ.items() if key != "WORKER_ONLY"}
        self.environment.update(session_variables)
        self.processes = []

    def __call__(self):
        self.processes.append(subprocess.Popen([sys.executable, str(HOST), str(self.spool)], env=self.environment))

    def wait(self):
        for process in self.processes:
            process.wait(timeout=30)


class _InProcessScheduler:
    """Run the launcher on a thread of this process, so a test can shorten its limits."""

    def __init__(self, spool: Path):
        self.spool = spool
        self.threads = []

    def __call__(self):
        thread = threading.Thread(target=host.main, args=([str(self.spool)],), daemon=True)
        thread.start()
        self.threads.append(thread)

    def wait(self):
        for thread in self.threads:
            thread.join(timeout=30)


def _run_request(spool: Path, script: Path, deadline: float) -> Path:
    """A run request that no worker follows, as after the worker crashed."""
    return DesktopSession(spool, start=lambda: None)._submit("run", {
        "command": [sys.executable, str(script)], "cwd": str(script.parent), "variables": {},
        "script": str(script), "deadline": deadline,
    })


@pytest.fixture
def scheduler(tmp_path):
    started = _TaskScheduler(tmp_path / "spool")
    yield started
    started.wait()


def _gone(pid: int) -> bool:
    for _ in range(50):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            return True
        time.sleep(.1)
    return False


def test_run_job_uses_the_session_environment_project_env_and_exact_arguments(tmp_path, monkeypatch, scheduler):
    monkeypatch.setenv("WORKER_ONLY", "service variable")
    scheduler.environment.update(SESSION_ONLY="signed-in session", METRONOME_FLOW_OUTPUT="stale")
    env_file = tmp_path / ".env"
    env_file.write_text("DG_DESKTOP_SETTING=from-dotenv\nSESSION_ONLY=\n", encoding="utf-8")
    folder = tmp_path / "scripts"
    folder.mkdir()
    (folder / "sibling.py").write_text("VALUE = 'sibling imported'\n", encoding="utf-8")
    script = folder / "report.py"
    script.write_text(
        "import json, os, sys, sibling\n"
        "try:\n    input()\n    stdin = 'open'\nexcept EOFError:\n    stdin = 'closed'\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'sibling': sibling.VALUE, 'stdin': stdin,\n"
        "    'session': os.environ.get('SESSION_ONLY'), 'worker': os.environ.get('WORKER_ONLY'),\n"
        "    'dotenv': os.environ.get('DG_DESKTOP_SETTING'), 'stale': os.environ.get('METRONOME_FLOW_OUTPUT'),\n"
        "    'mode': os.environ.get('METRONOME_FLOW_MODE'), 'step': os.environ.get('METRONOME_FLOW_STEP'),\n"
        "    'value': os.environ.get('METRONOME_FLOW_VALUE'), 'unbuffered': os.environ.get('PYTHONUNBUFFERED')}))\n",
        encoding="utf-8",
    )
    job = {"flow": {"name": "Excel report"}, "python_source": {
        "enabled": True, "mode": "run", "scripts": [str(script)],
        "arguments": ['--sheet "two words"'], "values": [["A"]],
        "interpreter": sys.executable, "timeout_seconds": 60,
    }}
    events = []
    artifacts, timings, result = flow_worker.execute_python_run_job(
        job, lambda status, detail: events.append(detail), run_id=41,
        script_observer_factory=ProcessTreeObserver,
        desktop_session_factory=lambda: DesktopSession(scheduler.spool, start=scheduler, env_file=env_file),
    )
    assert artifacts == [] and result["no_op"] is False and timings[0]["status"] == "succeeded"
    assert [detail["stage"] for detail in events] == [
        "python_session", "python_scripts", "python_step", "python_step_complete", "python_complete",
    ]
    scripts_event = events[1]
    assert scripts_event["session"] == "desktop"
    assert scripts_event["interpreter"] == sys.executable
    assert scripts_event["interpreter_reason"] == "Flow setting"
    assert "in the signed-in Windows session" in scripts_event["message"]
    step = events[3]
    assert step["exit_code"] == 0
    assert step["checksum"] == hashlib.sha256(script.read_bytes()).hexdigest()
    seen = json.loads(step["stdout"])
    assert seen["argv"] == ["--sheet", "two words", "A"]
    assert Path(seen["cwd"]).resolve() == folder.resolve()
    assert seen["sibling"] == "sibling imported"
    assert seen["stdin"] == "closed"
    # The script sees the session's environment and the project .env, never
    # the worker service's own variables or stale file-producing ones.
    assert seen["session"] == "signed-in session"
    assert seen["worker"] is None
    assert seen["dotenv"] == "from-dotenv"
    assert seen["stale"] is None
    assert (seen["mode"], seen["step"], seen["value"], seen["unbuffered"]) == ("run", "1", "A", "1")
    assert list(scheduler.spool.iterdir()) == []


def test_missing_later_script_fails_in_the_session_before_the_first_one_runs(tmp_path, scheduler):
    marker = tmp_path / "ran.txt"
    first = tmp_path / "first.py"
    first.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    missing = tmp_path / "missing.py"
    job = {"flow": {"name": "Two"}, "python_source": {
        "enabled": True, "mode": "run", "scripts": [str(first), str(missing)],
        "arguments": ["", ""], "values": [[], []], "interpreter": sys.executable, "timeout_seconds": 60,
    }}
    events = []
    with pytest.raises(RuntimeError, match="does not exist or is not readable"):
        flow_worker.execute_python_run_job(
            job, lambda status, detail: events.append(detail), run_id=42,
            desktop_session_factory=lambda: DesktopSession(scheduler.spool, start=scheduler),
        )
    assert [detail["stage"] for detail in events] == ["python_session"]
    assert not marker.exists()


def test_session_waits_for_processes_the_script_started_and_keeps_their_output(tmp_path, scheduler):
    (tmp_path / "child.py").write_text("import time\ntime.sleep(.6)\nprint('child finished', flush=True)\n",
                                       encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "print('parent finished', flush=True)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = flow_python.run_scripts_only(
        [launcher], environment=os.environ.copy(), flow_name="Wait", run_id=43,
        interpreter=sys.executable, timeout_seconds=30, observer_factory=ProcessTreeObserver,
        session=DesktopSession(scheduler.spool, start=scheduler), checksums={},
    )
    assert time.monotonic() - started >= .5
    assert "parent finished" in result[0]["stdout"]
    assert "child finished" in result[0]["stdout"]


def test_session_time_limit_ends_the_whole_script_tree(tmp_path, scheduler):
    (tmp_path / "child.py").write_text(
        "import os, pathlib, time\n"
        "pathlib.Path('child.pid').write_text(str(os.getpid()))\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    with pytest.raises(TimeoutError, match="time limit"):
        flow_python.run_scripts_only(
            [launcher], environment=os.environ.copy(), flow_name="Timeout", run_id=44,
            interpreter=sys.executable, timeout_seconds=4, observer_factory=ProcessTreeObserver,
            session=DesktopSession(scheduler.spool, start=scheduler), checksums={},
        )
    assert _gone(int((tmp_path / "child.pid").read_text()))


def test_stop_ends_the_script_tree_started_in_the_session(tmp_path, scheduler):
    script = tmp_path / "long.py"
    script.write_text(
        "import os, pathlib, time\n"
        "pathlib.Path('root.pid').write_text(str(os.getpid()))\n"
        "print('working', flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    lines = []
    with pytest.raises(RuntimeError, match="stopped"):
        flow_python.run_scripts_only(
            [script], environment=os.environ.copy(), flow_name="Stop", run_id=45,
            interpreter=sys.executable, timeout_seconds=60, observer_factory=ProcessTreeObserver,
            on_line=lambda stream, line, pid: lines.append(line) or line,
            stop_requested=lambda: "working" in lines,
            session=DesktopSession(scheduler.spool, start=scheduler), checksums={},
        )
    assert _gone(int((tmp_path / "root.pid").read_text()))


def test_nonzero_exit_and_console_splitting_match_the_worker_pipe_reader(tmp_path, scheduler):
    script = tmp_path / "console.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'first\\rsecond\\xff\\n\\n\\nthird')\n"
        "sys.stdout.buffer.flush()\n"
        "print('broken', file=sys.stderr)\n"
        "raise SystemExit(7)\n", encoding="utf-8",
    )
    lines = []
    records = []
    with pytest.raises(RuntimeError, match="exit code 7: broken"):
        flow_python.run_scripts_only(
            [script], environment=os.environ.copy(), flow_name="Console", run_id=46,
            interpreter=sys.executable, step_result=records.append,
            on_line=lambda stream, line, pid: lines.append((stream, line)) or line,
            session=DesktopSession(scheduler.spool, start=scheduler), checksums={},
        )
    stdout = [line for stream, line in lines if stream == "stdout"]
    assert stdout[0] == "first"
    assert stdout[1].startswith("second")
    assert stdout[2:] == ["third"]
    assert ("stderr", "broken") in lines
    assert records[0]["exit_code"] == 7


def test_a_command_the_session_cannot_start_is_reported(tmp_path, scheduler):
    session = DesktopSession(scheduler.spool, start=scheduler)
    with pytest.raises(OSError):
        session.run([str(tmp_path / "no-such-python"), "script.py"], cwd=tmp_path,
                    variables={}, timeout_seconds=30)
    assert list(scheduler.spool.iterdir()) == []


def test_fails_closed_when_windows_never_starts_the_launcher(tmp_path):
    spool = tmp_path / "spool"
    marker = tmp_path / "ran.txt"
    script = tmp_path / "script.py"
    script.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    session = DesktopSession(spool, start=lambda: None, start_timeout=.5)
    with pytest.raises(DesktopUnavailable, match="make sure that account is signed in"):
        session.run([sys.executable, str(script)], cwd=tmp_path, variables={}, timeout_seconds=30)
    assert list(spool.iterdir()) == []
    # A launcher that Windows starts late finds nothing left to run.
    assert host.main([str(spool)]) == 0
    assert not marker.exists()


def test_task_scheduler_refusal_is_reported_and_leaves_no_request(tmp_path):
    def refuse():
        raise DesktopUnavailable("Task Scheduler could not start Metronome_Python_Desktop. Update Metronome or run setup.ps1.")

    spool = tmp_path / "spool"
    with pytest.raises(DesktopUnavailable, match="run setup.ps1"):
        DesktopSession(spool, start=refuse).verify([tmp_path / "script.py"])
    assert list(spool.iterdir()) == []


def test_start_task_runs_the_interactive_task_and_explains_a_missing_one(monkeypatch):
    calls = []
    monkeypatch.setattr(flow_desktop_session.subprocess, "run", lambda command, **kwargs: calls.append(command)
                        or SimpleNamespace(returncode=1, stdout="", stderr="ERROR: The system cannot find the file specified."))
    with pytest.raises(DesktopUnavailable, match="run setup.ps1"):
        flow_desktop_session.start_task()
    assert calls[0][1:] == ["/Run", "/TN", "\\Metronome_Python_Desktop"]
    assert calls[0][0].endswith(os.path.join("System32", "schtasks.exe"))
    monkeypatch.setattr(flow_desktop_session.subprocess, "run",
                        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="SUCCESS", stderr=""))
    flow_desktop_session.start_task()


def test_launcher_claims_each_pending_request_once_in_order_and_skips_expired_or_cancelled(tmp_path):
    session = DesktopSession(tmp_path, start=lambda: None)
    first = session._submit("verify", {"scripts": []})
    second = session._submit("verify", {"scripts": []})
    expired = session._submit("verify", {"scripts": []})
    request = json.loads((expired / host.REQUEST).read_text(encoding="utf-8"))
    host.write_json(expired / host.REQUEST, {**request, "expires_at": 0})
    cancelled = session._submit("verify", {"scripts": []})
    assert session._cancel(cancelled) is True
    assert host.claim(tmp_path)[0] == first
    assert host.claim(tmp_path)[0] == second
    assert host.claim(tmp_path) is None
    assert session._cancel(first) is False


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX signals to end the launcher and the script")
def test_session_that_ends_mid_run_is_reported_without_waiting_for_the_time_limit(tmp_path, scheduler):
    script = tmp_path / "logoff.py"
    script.write_text(
        "import os, signal, time\n"
        "print('started', flush=True)\n"
        "time.sleep(.5)\n"
        "os.kill(os.getppid(), signal.SIGKILL)\n"   # the launcher, as a sign-out would
        "os.kill(os.getpid(), signal.SIGKILL)\n", encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="session ended"):
        flow_python.run_scripts_only(
            [script], environment=os.environ.copy(), flow_name="Logoff", run_id=47,
            interpreter=sys.executable, timeout_seconds=60, observer_factory=ProcessTreeObserver,
            session=DesktopSession(scheduler.spool, start=scheduler), checksums={},
        )
    assert time.monotonic() - started < 30


def test_launcher_ends_a_step_whose_worker_stopped_renewing_its_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "HEARTBEAT_TIMEOUT_SECONDS", 1)
    script = tmp_path / "long.py"
    script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path('pids.txt').write_text(f'{os.getpid()} {child.pid}')\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    spool = tmp_path / "spool"
    directory = _run_request(spool, script, time.time() + 600)

    def worker_until_started():
        # The worker renews the heartbeat until the script runs, then "crashes".
        while not (tmp_path / "pids.txt").exists():
            os.utime(directory / host.HEARTBEAT)
            time.sleep(.1)

    threading.Thread(target=worker_until_started, daemon=True).start()
    started = time.monotonic()
    assert host.main([str(spool)]) == 0
    assert time.monotonic() - started < 30
    assert host.read_json(directory / host.EXITED)["ended_by"] == "worker lost"
    assert host.read_json(directory / host.DONE)["ended_by"] == "worker lost"
    root, child = (int(value) for value in (tmp_path / "pids.txt").read_text().split())
    assert _gone(root) and _gone(child)


def test_launcher_enforces_the_time_limit_when_no_worker_does(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "DEADLINE_GRACE_SECONDS", 0)
    script = tmp_path / "leaves_child.py"
    script.write_text(
        "import pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n", encoding="utf-8",
    )
    spool = tmp_path / "spool"
    directory = _run_request(spool, script, time.time() + 2)
    alive = threading.Event()

    def live_worker():
        # A worker that renews the heartbeat but never ends the step itself.
        while not alive.is_set():
            os.utime(directory / host.HEARTBEAT)
            time.sleep(.2)

    threading.Thread(target=live_worker, daemon=True).start()
    try:
        assert host.main([str(spool)]) == 0
    finally:
        alive.set()
    exited = host.read_json(directory / host.EXITED)
    assert exited["returncode"] == 0 and exited["ended_by"] is None
    # The child it left behind held the output until the limit ended it too.
    assert host.read_json(directory / host.DONE)["ended_by"] == "time limit"
    assert _gone(int((tmp_path / "child.pid").read_text()))


def test_worker_heartbeat_keeps_a_long_step_running(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "HEARTBEAT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(flow_desktop_session, "HEARTBEAT_SECONDS", .2)
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(2.5)\nprint('done', flush=True)\n", encoding="utf-8")
    launcher = _InProcessScheduler(tmp_path / "spool")
    completed = DesktopSession(launcher.spool, start=launcher).run(
        [sys.executable, str(script)], cwd=tmp_path, variables={}, timeout_seconds=30, script=script,
    )
    launcher.wait()
    assert completed.returncode == 0
    assert "done" in completed.stdout


def test_each_run_records_the_hash_of_the_script_as_it_started(tmp_path, scheduler):
    script = tmp_path / "changed.py"
    script.write_text("print('edited after the check')\n", encoding="utf-8")
    records = []
    flow_python.run_scripts_only(
        [script], environment=os.environ.copy(), flow_name="Hash", run_id=48,
        interpreter=sys.executable, step_result=records.append,
        session=DesktopSession(scheduler.spool, start=scheduler), checksums={str(script): "0" * 64},
    )
    assert records[0]["script_checksum"] == hashlib.sha256(script.read_bytes()).hexdigest()
    # A script removed after the check fails that run instead of starting it.
    with pytest.raises(RuntimeError, match="does not exist or is not readable"):
        flow_python.run_scripts_only(
            [tmp_path / "removed.py"], environment=os.environ.copy(), flow_name="Hash", run_id=49,
            interpreter=sys.executable,
            session=DesktopSession(scheduler.spool, start=scheduler), checksums={},
        )


def test_output_lines_are_bounded_like_the_pipe_reader(tmp_path):
    path = tmp_path / "stdout.log"
    path.write_bytes(b"x" * 9000 + b"\n" + "é".encode() * 3000 + b"\r\n\r\n" + b"tail")
    tail = flow_desktop_session._Tail(path)
    lines = tail.read(final=True)
    tail.close()
    assert [len(line) for line in lines[:1]] == [2000]
    assert lines[1] == "é" * 2000
    assert lines[2:] == ["tail"]


def test_setup_registers_the_standard_rights_desktop_launcher_task():
    source = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    assert "$PythonDesktopTaskName = 'Metronome_Python_Desktop'" in source
    assert "-LogonType Interactive -RunLevel Limited" in source
    assert "-MultipleInstances Parallel" in source
    assert "-Priority 4" in source
    assert "Join-Path $PyDir 'pythonw.exe'" in source
    assert "app\\flow_desktop_host.py" in source
    assert "'.metronome-python-desktop'" in source
    assert "Register-ScheduledTask -TaskName $PythonDesktopTaskName" in source
    stop = "Stop-ScheduledTask -TaskName $PythonDesktopTaskName"
    assert stop in source
    assert source.index(stop) < source.index("Expand-Archive -Path $ZipPath")


def test_worker_advertises_desktop_runs_and_uses_the_session_only_on_windows():
    source = (ROOT / "app" / "flow_worker.py").read_text(encoding="utf-8")
    assert "registration['capabilities'][flow_python.DESKTOP_CAPABILITY] = True" in source
    loop = source[source.index("def run_worker("):]
    assert 'if os.name == "nt":' in loop
    assert "DesktopSession(env_file=config.ENV_FILE)" in loop
    # The portable run_flow.py copies execute_flow and what it references;
    # it must never pull in the Task Scheduler launcher.
    shared = source[source.index("def execute_flow("):source.index("def run_worker(")]
    assert "flow_desktop_session" not in shared
    assert flow_desktop_session.TASK_NAME == "Metronome_Python_Desktop"


def test_spool_is_private_to_the_account_profile_unless_overridden(tmp_path, monkeypatch):
    monkeypatch.delenv(flow_desktop_session.SPOOL_ENVIRONMENT, raising=False)
    assert flow_desktop_session.spool_root() == Path.home() / ".metronome-python-desktop"
    monkeypatch.setenv(flow_desktop_session.SPOOL_ENVIRONMENT, str(tmp_path / "spool"))
    assert flow_desktop_session.spool_root() == tmp_path / "spool"
