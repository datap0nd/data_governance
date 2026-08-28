from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.routers import actions
from app.scanner.findings import sync_managed_actions


def test_occurrence_api_exposes_current_exact_focus_and_analysis_state(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "alert-occurrence-api.db"))
    database.init_db()
    with database.get_db() as db:
        lifecycle = sync_managed_actions(
            db,
            "flow_failed",
            [{
                "fingerprint": "flow_failed:7",
                "notes": "Download failed",
                "occurrence": {
                    "focus_type": "flow_run",
                    "focus_id": 71,
                    "summary": "Flow run #71 failed.",
                    "observed_at": "2026-08-27T12:00:00+00:00",
                    "evidence": {"status": "failed", "stage": "download"},
                },
            }],
            "2026-08-27T12:00:00+00:00",
        )
        action_id = lifecycle["action_ids"]["flow_failed:7"]
        agent_run_id = db.execute(
            """INSERT INTO agent_runs
                   (question, focus_type, focus_id, status, model, provider_mode,
                    prompt_version, action_id, action_evidence_revision)
               VALUES ('Why?', 'flow_run', '71', 'completed', 'test', 'mock',
                       'test-v1', ?, 1)""",
            (action_id,),
        ).lastrowid

    payload = actions.list_action_occurrences(action_id)
    assert payload["action_status"] == "open"
    assert payload["evidence_revision"] == 1
    occurrence = payload["occurrences"][0]
    assert occurrence["focus_type"] == "flow_run"
    assert occurrence["focus_id"] == "71"
    assert occurrence["status"] == "failed"
    assert occurrence["is_current"] is True
    assert occurrence["latest_analysis_run_id"] == agent_run_id
    assert occurrence["analysis_is_current"] is True

    with database.get_db() as db:
        db.execute("UPDATE actions SET status='resolved' WHERE id=?", (action_id,))

    resolved = actions.list_action_occurrences(action_id)["occurrences"][0]
    assert resolved["is_current"] is False
    assert resolved["analysis_is_current"] is False
    assert resolved["analysis_superseded_reason"] == "alert_resolved"


def test_reopening_old_duplicate_alert_returns_conflict_instead_of_constraint_error(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "alert-reopen.db"))
    database.init_db()
    with database.get_db() as db:
        old_id = db.execute(
            """INSERT INTO actions(type, status, fingerprint)
               VALUES ('flow_failed', 'resolved', 'flow_failed:9')"""
        ).lastrowid
        current_id = db.execute(
            """INSERT INTO actions(type, status, fingerprint)
               VALUES ('flow_failed', 'open', 'flow_failed:9')"""
        ).lastrowid

    app = FastAPI()
    app.include_router(actions.router)
    with TestClient(app) as client:
        response = client.patch(f"/api/actions/{old_id}", json={"status": "open"})

    assert response.status_code == 409
    assert response.json()["detail"] == "A newer active Alert already represents this issue."
    with database.get_db() as db:
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (old_id,)
        ).fetchone()[0] == "resolved"
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (current_id,)
        ).fetchone()[0] == "open"
