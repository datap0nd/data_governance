"""HTTP contracts for the lightweight list snapshot and constrained edits."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.routers import flows
from test_flows import flow_db, _flow, _seed_catalog, _mark_discovered, _person, _request


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
        assert data["events"][0]["status"] == state
        assert data["events"][0]["key"] == f"state:{run_id}:{state}"
        assert "payload" not in response.text
    with database.get_db() as db:
        assert db.execute("SELECT count(*) FROM flow_run_events").fetchone()[0] == 0


def test_activity_includes_old_active_runs_and_only_50_lightweight_events(activity_client):
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
    assert len(data["events"]) == 50
    assert data["events"][0]["run_id"] == run_id
    assert all(len(event["message"]) == 1000 for event in data["events"])
    assert all(event["key"].startswith("event:") for event in data["events"])
    assert not {"job_json", "artifacts", "traceback", "details_json", "error"} & set(data["events"][0])
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
    assert any(e["key"] == f"state:{queued['id']}:cancelled" for e in data["events"])
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
