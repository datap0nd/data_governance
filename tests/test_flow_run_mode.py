"""Synthetic tests for Python scheduler execution; no live systems are used."""
import json
import os
import sys
import time
from pathlib import Path

import pytest
import psutil

from app import database, flow_python, flow_worker
from app.flow_process_tree import ProcessTreeObserver
from app.routers import flows
from test_flow_python import _python_flow
from test_flows import flow_db, _request


def test_run_mode_forces_output_features_off_and_validates_interpreter():
    body = flows.FlowWrite(
        name="Launcher", source_type="python", python_run_mode="run",
        python_scripts=[str(Path.cwd() / "launcher.py")], file_format="xlsx",
        filename_template="old.xlsx", sql_handoff_enabled=True,
        sql_database="warehouse", sql_schema="public", sql_table="old",
        email_delivery={"enabled": True, "recipients": ["a@example.test"]},
    )
    assert body.python_run_mode == "run"
    assert body.file_format == "csv" and body.filename_template == "run-only.csv"
    assert body.sql_handoff_enabled is False and body.email_delivery is None
    assert body.post_sql_refresh is None
    with pytest.raises(ValueError, match="absolute python.exe"):
        flows.FlowWrite(name="Bad", source_type="python", python_run_mode="run",
                        python_scripts=[str(Path.cwd() / "a.py")],
                        python_interpreter="relative/python.exe")
    with pytest.raises(ValueError):
        flows.FlowWrite(name="Bad", source_type="python", python_run_mode="run",
                        python_scripts=[str(Path.cwd() / "a.py")],
                        python_timeout_minutes=1441)


def test_run_scripts_only_uses_exact_arguments_working_folder_and_environment(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    (folder / "sibling.py").write_text("VALUE = 'sibling imported'\n", encoding="utf-8")
    script = folder / "launcher.py"
    script.write_text(
        "import json, os, sys, sibling\n"
        "assert os.getcwd() == os.path.dirname(__file__)\n"
        "assert 'METRONOME_FLOW_OUTPUT' not in os.environ\n"
        "assert os.environ['METRONOME_FLOW_MODE'] == 'run'\n"
        "print(json.dumps({'argv': sys.argv[1:], 'sibling': sibling.VALUE, "
        "'step': os.environ['METRONOME_FLOW_STEP'], 'value': os.environ['METRONOME_FLOW_VALUE']}))\n",
        encoding="utf-8",
    )
    result = flow_python.run_scripts_only(
        [script], environment={**os.environ, "METRONOME_FLOW_OUTPUT": "stale"},
        flow_name="Launcher", run_id=31, interpreter=sys.executable,
        arguments=['--name "two words"'], values=[["A"]],
        observer_factory=ProcessTreeObserver,
    )
    assert result[0]["exit_code"] == 0
    assert json.loads(result[0]["stdout"])["argv"] == ["--name", "two words", "A"]
    assert json.loads(result[0]["stdout"])["sibling"] == "sibling imported"
    assert result[0]["output_path"] is None


def test_run_mode_waits_for_fire_and_forget_child_and_collects_output(tmp_path):
    child = tmp_path / "child.py"
    child.write_text("import time\ntime.sleep(.6)\nprint('child finished', flush=True)\n", encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "print('parent finished', flush=True)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = flow_python.run_scripts_only(
        [launcher], environment=os.environ.copy(), flow_name="Wait", run_id=32,
        interpreter=sys.executable, timeout_seconds=10,
        observer_factory=ProcessTreeObserver,
    )
    assert time.monotonic() - started >= .5
    assert "parent finished" in result[0]["stdout"]
    assert "child finished" in result[0]["stdout"]


def test_run_mode_nonzero_and_closed_stdin_are_reported(tmp_path):
    script = tmp_path / "fails.py"
    script.write_text(
        "import sys\n"
        "try: input('prompt')\n"
        "except EOFError: print('stdin closed', file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    events = []
    with pytest.raises(RuntimeError, match="exit code 7: stdin closed"):
        flow_python.run_scripts_only(
            [script], environment=os.environ.copy(), flow_name="Fail", run_id=33,
            interpreter=sys.executable, step_result=events.append,
        )
    assert events[0]["exit_code"] == 7
    assert events[0]["duration_ms"] > 0


def test_execute_run_job_skips_artifacts_and_reports_completion(tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("print('hello from run mode')\n", encoding="utf-8")
    job = {"flow": {"name": "Hello"}, "python_source": {
        "enabled": True, "mode": "run", "scripts": [str(script)],
        "arguments": [""], "values": [[]], "interpreter": sys.executable,
        "timeout_seconds": 10,
    }}
    events = []
    artifacts, timings, result = flow_worker.execute_python_run_job(
        job, lambda status, detail: events.append((status, detail)), run_id=34,
        script_observer_factory=ProcessTreeObserver,
    )
    assert artifacts == [] and result["no_op"] is False
    assert result["sql_artifacts"] == []
    assert timings[0]["status"] == "succeeded"
    assert [detail["stage"] for _, detail in events] == [
        "python_scripts", "python_step", "python_step_complete", "python_complete",
    ]


def test_run_mode_claim_requires_versioned_worker_capability(flow_db, tmp_path):
    script = tmp_path / "script.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    saved = flows.create_flow(_python_flow([script], python_run_mode="run"), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        assert job["python_source"]["mode"] == "run"
        db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at)
               VALUES (?, 'scheduled', 'queued', ?, ?)""",
            (saved["id"], json.dumps(job), flows._iso(flows._now())),
        )
    base = {"adapters": ["python_script"], "headed": False,
            "shared_flow_artifacts": True}
    flows.register_worker(flows.WorkerRegister(
        worker_id="old-python-worker", display_name="Old", capabilities=base,
    ))
    assert flows.claim_run("old-python-worker")["run"] is None
    flows.register_worker(flows.WorkerRegister(
        worker_id="new-python-worker", display_name="New",
        capabilities={**base, flow_python.RUN_CAPABILITY: True},
    ))
    assert flows.claim_run("new-python-worker")["run"]["flow_id"] == saved["id"]


def test_run_timeout_ends_child_process_tree(tmp_path):
    child = tmp_path / "child.py"
    child.write_text(
        "import os, pathlib, time\n"
        "pathlib.Path('child.pid').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n", encoding="utf-8",
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "time.sleep(30)\n", encoding="utf-8",
    )
    with pytest.raises(TimeoutError, match="time limit"):
        flow_python.run_scripts_only(
            [launcher], environment=os.environ.copy(), flow_name="Timeout", run_id=35,
            interpreter=sys.executable, timeout_seconds=3,
            observer_factory=ProcessTreeObserver,
        )
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(20):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(.1)
    else:
        pytest.fail("The child process remained alive after the run timed out.")


def test_console_splits_carriage_returns_and_replaces_invalid_bytes(tmp_path):
    script = tmp_path / "console.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'first\\rsecond\\xff\\n')\n"
        "sys.stdout.buffer.flush()\n", encoding="utf-8",
    )
    lines = []
    flow_python.run_scripts_only(
        [script], environment=os.environ.copy(), flow_name="Console", run_id=36,
        interpreter=sys.executable,
        on_line=lambda stream, line, pid: lines.append((stream, line)) or line,
    )
    assert lines[0] == ("stdout", "first")
    assert lines[1][0] == "stdout" and lines[1][1].startswith("second")
