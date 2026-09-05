"""HTTP contracts for the lightweight list snapshot and constrained edits."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.routers import flows
from test_flows import flow_db, _flow, _seed_catalog, _mark_discovered, _person, _request
from test_flow_parallel import bundle, claim, complete


@pytest.fixture
def activity_client(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "launched"})
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    app = FastAPI()
    app.include_router(flows.router)
    with TestClient(app) as client:
        yield client, saved


def add_run(flow_id, status="queued", created="2026-09-01T10:00:00"):
    with database.get_db() as db:
        return db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at)
               VALUES (?, 'manual', ?, '{"secret":"payload"}', ?)""",
            (flow_id, status, created),
        ).lastrowid


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_activity_tracks_all_states_without_progress(activity_client, terminal):
    client, flow = activity_client
    empty = client.get("/api/flows/activity").json()
    assert empty["latest_runs"][0]["id"] is None
    run_id = add_run(flow["id"])
    for state in ["queued", "claimed", "running", terminal]:
        with database.get_db() as db:
            db.execute("UPDATE flow_runs SET status=? WHERE id=?", (state, run_id))
        response = client.get("/api/flows/activity")
        assert response.status_code == 200
        data = response.json()
        assert data["latest_runs"][0]["status"] == state
        assert bool(data["active_runs"]) == (state in {"queued", "claimed", "running"})
        assert data["latest_runs"][0]["progress"]["stage"] == state
        assert data["latest_runs"][0]["progress"]["total"] is None
        assert "payload" not in response.text
    with database.get_db() as db:
        assert db.execute("SELECT count(*) FROM flow_run_events").fetchone()[0] == 0


def test_activity_includes_old_active_runs_without_returning_history(activity_client):
    client, flow = activity_client
    active_id = add_run(flow["id"], "running", "2025-01-01T00:00:00")
    for _ in range(105):
        run_id = add_run(flow["id"], "succeeded")
        with database.get_db() as db:
            db.execute(
                """INSERT INTO flow_run_events(run_id,status,stage,message,details_json,traceback,created_at)
                   VALUES (?, 'succeeded', 'download', ?, '{"private":"details"}', 'trace-secret',
                           '2026-09-01T10:00:01')""", (run_id, "m" * 2000),
            )
    data = client.get("/api/flows/activity").json()
    assert data["active_runs"][0]["id"] == active_id
    assert data["latest_runs"][0]["id"] == run_id
    assert "events" not in data
    assert not {"job_json", "artifacts", "traceback", "details_json", "error"} & set(data["active_runs"][0])
    assert "trace-secret" not in json.dumps(data)
    assert data["workers"] == {"total": 0, "online": 0}


def test_activity_stop_without_event_and_worker_heartbeat_counts(activity_client, monkeypatch):
    client, flow = activity_client
    monkeypatch.setattr(flows, "stop_local_worker", lambda **kwargs: {"status": "stopped"})
    queued = flows.queue_run(flow["id"], _request())
    with database.get_db() as db:
        db.execute("INSERT INTO flow_run_events(run_id,status,stage,message) VALUES (?,'queued','queue','Waiting')", (queued["id"],))
        for worker_id, status, seen in [
            ("fresh", "online", flows._iso(flows._now())),
            ("old", "online", "2000-01-01T00:00:00"),
            ("offline", "offline", flows._iso(flows._now())),
        ]:
            db.execute("INSERT INTO flow_workers(worker_id,display_name,status,last_seen_at) VALUES (?,?,?,?)", (worker_id, worker_id, status, seen))
    assert client.post(f"/api/flows/{flow['id']}/stop").status_code == 200
    data = client.get("/api/flows/activity").json()
    assert not data["active_runs"]
    assert data["latest_runs"][0]["status"] == "cancelled"
    assert data["latest_runs"][0]["progress"]["message"] == "Cancelled"
    assert data["workers"] == {"total": 3, "online": 1}


def test_inline_edits_preserve_config_and_queued_snapshot(activity_client):
    client, flow = activity_client
    person = _person()
    queued = flows.queue_run(flow["id"], _request())
    with database.get_db() as db:
        before = dict(db.execute("SELECT * FROM flows WHERE id=?", (flow["id"],)).fetchone())
        job = db.execute("SELECT job_json FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()[0]
    url = f"/api/flows/{flow['id']}"
    assert client.patch(url, json={"owner_person_id": person["id"], "browser_mode": "headed"}).status_code == 200
    with database.get_db() as db:
        after = dict(db.execute("SELECT * FROM flows WHERE id=?", (flow["id"],)).fetchone())
        assert db.execute("SELECT job_json FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()[0] == job
        assert db.execute("SELECT detail FROM event_log WHERE entity_type='flow' AND action='updated'").fetchone()
        db.execute("UPDATE flow_runs SET status='cancelled' WHERE id=?", (queued["id"],))
    for key in before.keys() - {"owner_person_id", "browser_mode", "updated_at"}:
        assert after[key] == before[key], key
    assert after["owner_person_id"] == person["id"]
    assert after["browser_mode"] == "headed"
    assert flows.queue_run(flow["id"], _request())["job"]["execution"]["browser_mode"] == "headed"
    assert client.patch(url, json={"owner_person_id": None}).json()["owner_person_id"] is None


@pytest.mark.parametrize("body,code", [
    ({"owner_person_id": 999999}, 400), ({"owner_person_id": "1"}, 422),
    ({"browser_mode": "visible"}, 422), ({"browser_mode": None}, 422),
    ({"schedule_type": "manual"}, 422), ({}, 422),
])
def test_inline_rejects_invalid_changes(activity_client, body, code):
    client, flow = activity_client
    assert client.patch(f"/api/flows/{flow['id']}", json=body).status_code == code


@pytest.mark.parametrize("source", ["outlook", "file"])
def test_inline_rejects_unsupported_browser_and_honors_locks(activity_client, source):
    client, flow = activity_client
    url = f"/api/flows/{flow['id']}"
    with database.get_db() as db:
        db.execute("UPDATE flows SET source_type=? WHERE id=?", (source, flow["id"]))
    assert client.patch(url, json={"browser_mode": "headed"}).status_code == 400
    assert client.patch(url, json={"owner_person_id": None}).status_code == 200
    # Exercise the real lock lookup; this read-only contract does not need a pipeline plan.
    with database.get_db() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("INSERT INTO pipeline_resource_locks(resource_type,resource_key,run_id) VALUES ('flow',?,1)", (str(flow["id"]),))
    assert client.patch(url, json={"owner_person_id": None}).status_code == 409
    assert client.patch("/api/flows/999999", json={"owner_person_id": None}).status_code == 404


def progress_run(flow_id, count=1, **extra):
    job = {"flow": {"source_type": "portal"}, "downloads": {"periods": list(range(count))},
           "report": {}, "sql_handoff": {"enabled": True}, **extra}
    run_id = add_run(flow_id)
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET job_json=? WHERE id=?", (json.dumps(job), run_id))
    return run_id


def report_progress(run_id, stage, status="running", artifacts=None):
    with database.get_db() as db:
        detail = {"stage": stage, "message": f"Now {stage}"}
        db.execute("UPDATE flow_runs SET status=?,progress_json=? WHERE id=?", (status, json.dumps(detail), run_id))
        db.execute("INSERT INTO flow_run_events(run_id,status,stage) VALUES (?,?,?)", (run_id, status, stage))
        if artifacts is not None:
            db.execute("UPDATE flow_runs SET artifact_json=? WHERE id=?", (json.dumps(artifacts), run_id))


def test_simple_sql_flow_has_five_evidence_based_steps(activity_client):
    client, flow = activity_client
    run_id = progress_run(flow["id"])
    def read():
        return client.get("/api/flows/activity").json()["latest_runs"][0]["progress"]
    assert (read()["completed"], read()["total"]) == (0, 5)
    report_progress(run_id, "opening_report")
    assert read()["completed"] == 0
    report_progress(run_id, "file_export")
    assert read()["completed"] == 1
    report_progress(run_id, "sql_insertion", artifacts=[{"status": "saved", "bundle_index": 1}])
    assert read()["completed"] == 3
    assert read()["message"] == "Now sql_insertion"
    # COPY progress does not mean a SQL transaction has committed.
    report_progress(run_id, "sql_copy")
    assert read()["completed"] == 3
    report_progress(run_id, "sql_insertion_complete")
    assert read()["completed"] == 4
    report_progress(run_id, "complete", "succeeded")
    assert read()["completed"] == 5
    assert read()["runners"] == []


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_long_flow_keeps_three_of_fifty_steps_on_failure(activity_client, terminal):
    client, flow = activity_client
    run_id = progress_run(flow["id"], count=46)
    artifacts = [{"status": "saved", "bundle_index": index} for index in [1, 2, 2]]
    report_progress(run_id, "file_export", artifacts=artifacts)
    report_progress(run_id, terminal, terminal)
    progress = client.get("/api/flows/activity").json()["latest_runs"][0]["progress"]
    assert (progress["completed"], progress["total"]) == (3, 50)
    assert progress["runners"] == []


def test_progress_uses_frozen_configuration_and_noop_skips_unused_work(activity_client):
    client, flow = activity_client
    run_id = progress_run(flow["id"], transformation={"enabled": True},
                          downloads={"output_mode": "direct_replace"})
    assert client.get("/api/flows/activity").json()["latest_runs"][0]["progress"]["total"] == 7
    report_progress(run_id, "local_file_no_op")
    progress = client.get("/api/flows/activity").json()["latest_runs"][0]["progress"]
    assert (progress["completed"], progress["total"]) == (1, 2)
    assert [item["label"] for item in progress["phases"]] == ["Check source", "Finish"]


def test_parallel_row_counts_real_workers_and_heartbeats_preserve_actions(bundle):
    from app.routers import flow_tasks as routes
    tasks = [claim("worker-1", bundle["run"]["id"]), claim("worker-2"), claim("worker-3")]
    for index, task in enumerate(tasks):
        routes.task_progress(task["worker_id"], task["id"], routes.TaskReport(
            lease_token=task["lease_token"], status="running",
            progress={"stage": "file_export", "message": f"Downloading export {index + 1}"},
        ))
    task = tasks[0]
    routes.task_progress(task["worker_id"], task["id"], routes.TaskReport(
        lease_token=task["lease_token"], status="running", progress={"stage": "task_heartbeat"},
    ))
    progress = flows.flow_activity()["active_runs"][0]["progress"]
    assert len(progress["runners"]) == 3  # Coordinator's own task counts once.
    assert progress["runners"][0]["message"] == "Downloading export 1"
    assert progress["completed"] == 1
    complete(tasks[1])
    progress = flows.flow_activity()["active_runs"][0]["progress"]
    assert len(progress["runners"]) == 2
    assert progress["completed"] == 2
    assert "1 of 3" in progress["message"]


@pytest.mark.parametrize("bundle", [{"flow": {"download_parallelism": 5,
    "start_week": "2026-W01", "end_week": "2026-W15", "window_weeks": 3}}], indirect=True)
def test_five_parallel_exports_have_independent_worker_progress(bundle):
    from app.routers import flow_tasks as routes
    from app import flow_paths
    with database.get_db() as db:
        flow_paths.save_setting(db, f"flows_portal_capacity:{bundle['site']['id']}", 5)
    tasks = [claim('worker-1', bundle['run']['id'])] + [claim(f'worker-{i}') for i in range(2, 6)]
    assert all(tasks)
    for task, stage in zip(tasks, ['authentication', 'file_export', 'file_normalization', 'file_transfer', 'configuring']):
        routes.task_progress(task['worker_id'], task['id'], routes.TaskReport(
            lease_token=task['lease_token'], status='running', progress={'stage':stage, 'message':stage}))
    progress = flows.flow_activity()['active_runs'][0]['progress']
    runners = progress['runners']
    assert len(runners) == 5  # Coordinator's download is not a sixth worker.
    assert [r['completed'] for r in runners] == [0, 1, 2, 1, 0]
    assert [r['total'] for r in runners] == [3] * 5
    assert [r['task_id'] for r in runners] == [t['id'] for t in tasks]
    assert [r['label'] for r in runners] == [f'Export {i} of 5' for i in range(1, 6)]
    task = tasks[2]
    routes.task_progress(task['worker_id'], task['id'], routes.TaskReport(
        lease_token=task['lease_token'], status='running', progress={'stage':'task_heartbeat'}))
    assert flows.flow_activity()['active_runs'][0]['progress']['runners'][2]['completed'] == 2
    routes.task_progress(task['worker_id'], task['id'], routes.TaskReport(
        lease_token=task['lease_token'], status='running', progress={'stage':'file_export', 'message':'Recorded export recipe'}))
    assert flows.flow_activity()['active_runs'][0]['progress']['runners'][2]['completed'] == 2
    complete(tasks[1])
    remaining = flows.flow_activity()['active_runs'][0]['progress']['runners']
    assert len(remaining) == 4
    assert 'worker-2' not in [r['id'] for r in remaining]
