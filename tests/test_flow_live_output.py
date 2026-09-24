"""Synthetic live console, marker and API retention tests."""
import json

from app import database
from app.flow_script_live import LiveObserver
from app.routers import flows
from test_flow_python import _python_flow
from test_flows import flow_db, _request


def _running_python_flow(flow_db, tmp_path):
    script = tmp_path / "script.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    saved = flows.create_flow(_python_flow([script], python_run_mode="run"), _request())
    now = flows._iso(flows._now())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        run_id = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, worker_id, job_json, created_at)
               VALUES (?, 'manual', 'running', 'test-worker', ?, ?)""",
            (saved["id"], json.dumps(job), now),
        ).lastrowid
    flows.register_worker(flows.WorkerRegister(
        worker_id="test-worker", display_name="Test", capabilities={"adapters": ["python_script"]},
    ))
    return saved, run_id


def test_live_endpoint_is_assigned_bounded_idempotent_and_incremental(flow_db, tmp_path):
    saved, run_id = _running_python_flow(flow_db, tmp_path)
    now = flows._iso(flows._now())
    payload = flows.WorkerLive(
        lines=[flows.LiveLine(line_no=1, stream="stdout", text="hello", pid=12, at=now)],
        processes=[{"pid": 12, "label": "script.py", "state": "running"}],
        stage="Loading", progress="1/2", script="script.py", last_output_at=now,
    )
    assert flows.update_run_live("test-worker", run_id, payload)["accepted"] is True
    assert flows.update_run_live("test-worker", run_id, payload)["accepted"] is True
    output = flows.get_run_output(run_id, after_line=0, limit=10)
    assert [(line["line_no"], line["text"]) for line in output["lines"]] == [(1, "hello")]
    assert flows.get_run_output(run_id, after_line=1, limit=10)["lines"] == []
    run = flows.get_run(run_id)
    assert run["live"]["stage"] == "Loading"
    assert run["live"]["processes"][0]["label"] == "script.py"
    assert "live_json" not in run
    assert flows.list_runs(flow_id=saved["id"], status="running", before_id=None, limit=10)[0]["live"]["progress"] == "1/2"
    assert flows.list_runs(flow_id=saved["id"], status="failed", before_id=None, limit=10) == []
    try:
        flows.update_run_live("other-worker", run_id, payload)
        assert False, "wrong worker was accepted"
    except flows.HTTPException as error:
        assert error.status_code == 404


def test_live_output_keeps_first_500_and_last_4500_lines(flow_db, tmp_path):
    _, run_id = _running_python_flow(flow_db, tmp_path)
    now = flows._iso(flows._now())
    for first in range(1, 6001, 1000):
        lines = [flows.LiveLine(line_no=n, stream="stdout", text=str(n), at=now)
                 for n in range(first, first + 1000)]
        flows.update_run_live("test-worker", run_id, flows.WorkerLive(lines=lines))
    output = flows.get_run_output(run_id, after_line=0, limit=1000)
    assert output["omitted"] == 1000
    assert output["lines"][0]["line_no"] == 1
    assert output["lines"][499]["line_no"] == 500
    assert output["lines"][500]["line_no"] == 1501


def test_live_observer_redacts_and_interprets_markers():
    posts, events = [], []
    observer = LiveObserver(lambda body: posts.append(body) or {"terminal": False},
                            lambda status, detail: events.append(detail),
                            {"DG_UPLOAD_PGPASSWORD": "hidden-value"})
    observer.line("stdout", "::stage::Loading hidden-value", 21)
    observer.line("stderr", "::progress::3/10", 21)
    observer.line("stdout", "password hidden-value", 21)
    observer.flush(force=True)
    assert posts[0]["stage"] == "Loading [redacted]"
    assert posts[0]["progress"] == "3/10"
    assert posts[0]["lines"][2]["text"] == "password [redacted]"
    assert events[0]["stage"] == "python_stage"


def test_live_observer_counts_drops_without_double_counting_line_gaps():
    posts = []
    observer = LiveObserver(lambda body: posts.append(body) or {"terminal": False},
                            lambda *_: None, {})
    for index in range(5001):
        observer.line("stdout", str(index), 21)
    assert observer.dropped == 1
    assert observer.next_line == 5001
    observer.flush(force=True)
    observer.line("stdout", "later", 21)
    assert observer.lines[-1]["line_no"] == 5001
    assert posts[0]["dropped"] == 1


def test_cancelled_run_accepts_one_final_snapshot_and_keeps_worker(flow_db, tmp_path, monkeypatch):
    saved, run_id = _running_python_flow(flow_db, tmp_path)
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET current_run_id=?, status='busy' WHERE worker_id='test-worker'",
                   (run_id,))
        db.execute("UPDATE flow_workers SET capabilities_json=? WHERE worker_id='test-worker'",
                   (json.dumps({"adapters": ["python_script"], "python_script_run_v1": True}),))
    stopped = []
    monkeypatch.setattr(flows, "stop_local_worker", lambda *a, **k: stopped.append((a, k)))
    result = flows.stop_run(saved["id"], _request())
    assert result["status"] == "cancelled"
    assert result["worker"]["status"] == "cooperative"
    assert stopped == []
    with database.get_db() as db:
        assert db.execute("SELECT current_run_id FROM flow_workers WHERE worker_id='test-worker'").fetchone()[0] == run_id
    now = flows._iso(flows._now())
    final = flows.WorkerLive(
        lines=[flows.LiveLine(line_no=1, stream="stderr", text="stopping", at=now)],
        final=True,
    )
    assert flows.update_run_live("test-worker", run_id, final)["accepted"] is True
    assert flows.update_run_live("test-worker", run_id, final)["accepted"] is False
    assert flows.get_run_output(run_id, after_line=0, limit=10)["lines"][0]["text"] == "stopping"
    assert any(event["stage"] == "python_stopped" for event in flows.get_run(run_id)["events"])


def test_terminal_run_prunes_old_console_without_erasing_run_history(flow_db, tmp_path):
    saved, oldest_id = _running_python_flow(flow_db, tmp_path)
    now = flows._iso(flows._now())
    with database.get_db() as db:
        job_json = db.execute("SELECT job_json FROM flow_runs WHERE id=?", (oldest_id,)).fetchone()[0]
        db.execute("UPDATE flow_runs SET status='succeeded', finished_at=? WHERE id=?", (now, oldest_id))
        db.execute("INSERT INTO flow_run_output(run_id, line_no, stream, text, emitted_at) VALUES (?, 1, 'stdout', 'old', ?)", (oldest_id, now))
        for index in range(49):
            db.execute(
                """INSERT INTO flow_runs(flow_id, trigger_type, status, worker_id, job_json, created_at, finished_at)
                   VALUES (?, 'manual', 'succeeded', 'test-worker', ?, ?, ?)""",
                (saved["id"], job_json, now, now),
            )
        newest_id = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, worker_id, job_json, created_at)
               VALUES (?, 'manual', 'running', 'test-worker', ?, ?)""",
            (saved["id"], job_json, now),
        ).lastrowid
    flows.update_run("test-worker", newest_id, flows.WorkerProgress(
        status="succeeded", progress={"stage": "complete", "message": "done"},
    ))
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM flow_run_output WHERE run_id=?", (oldest_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM flow_runs WHERE flow_id=?", (saved["id"],)).fetchone()[0] == 51
