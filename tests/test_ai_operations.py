import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, database
from app.ai import operations_agent, openai_provider, run_store
from app.ai.fake_provider import ScriptedProvider
from app.ai.openai_provider import OpenAIChatProvider
from app.ai.operations_tools import execute_tool
from app.ai.protocol import AIProtocolError, AITransportTimeout, AssistantTurn, ToolCall
from app.ai import router as ai_router
from app.routers import flows


@pytest.fixture()
def ai_db(monkeypatch):
    operations_agent.shutdown_executor()
    with TemporaryDirectory(
        prefix="metronome-ai-operations-", ignore_cleanup_errors=True
    ) as folder:
        db_path = str(Path(folder) / "operations.db")
        monkeypatch.setattr(database, "DB_PATH", db_path)
        database.init_db()
        yield Path(folder)
    operations_agent.shutdown_executor()


def _seed_flow(folder: Path, *, source_type: str = "portal", sql_enabled: bool = True):
    artifact = folder / f"saved artifact {source_type}.csv"
    artifact.write_text("Market,Units\nGlobal,10\n", encoding="utf-8")
    with database.get_db() as db:
        suffix = int(db.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM flows").fetchone()["id"])
        site = db.execute(
            """INSERT INTO flow_sites(name, adapter, base_url)
               VALUES (?, 'web_export', 'https://portal.example.test')""",
            (f"AI Portal {suffix}",),
        ).lastrowid
        report = db.execute(
            """INSERT INTO flow_reports(site_id, name, report_url, source_kind)
               VALUES (?, ?, 'https://portal.example.test/report', 'discovered')""",
            (site, f"AI Report {suffix}"),
        ).lastrowid
        flow = db.execute(
            """INSERT INTO flows
               (name, source_type, site_id, report_id, enabled, selections_json,
                export_views_json, download_mode, period_strategy, window_weeks,
                file_format, browser_mode, start_week, end_week, target_folder,
                filename_template, schedule_type, sql_handoff_enabled, sql_mode,
                sql_database, sql_schema, sql_table)
               VALUES (?, ?, ?, ?, 1, ?, ?, 'one_per_period', ?, 1, 'csv',
                       'headless', ?, ?, ?, 'weekly_{week}.csv', 'manual', ?,
                       ?, ?, ?, ?)""",
            (
                f"AI Flow {suffix}",
                source_type,
                site,
                report,
                json.dumps({"region": "Global"}),
                json.dumps(["Detail export"]),
                "none" if source_type == "outlook" else "fixed",
                None if source_type == "outlook" else "2026-W30",
                None if source_type == "outlook" else "2026-W31",
                str(folder / "downloads"),
                int(sql_enabled),
                "append" if sql_enabled else None,
                "analytics" if sql_enabled else None,
                "reporting" if sql_enabled else None,
                f"ai_target_{suffix}" if sql_enabled else None,
            ),
        ).lastrowid
        job = {
            "execution": {
                "browser_mode": "headless",
                "worker_id": "bi-desktop-headless",
            },
            "flow": {"id": flow, "source_type": source_type},
            "report": {"export_views": ["Detail export"]},
            "selections": {"region": "Global"},
            "downloads": {"mode": "one_per_period", "periods": [["2026-W30"]]},
            "transformation": {"enabled": False},
            "sql_handoff": {
                "enabled": sql_enabled,
                "server": "warehouse.example.test",
                "mode": "append" if sql_enabled else None,
                "database": "analytics" if sql_enabled else None,
                "schema": "reporting" if sql_enabled else None,
                "table": f"ai_target_{suffix}" if sql_enabled else None,
            },
        }
        successful = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, job_json, created_at, started_at, finished_at)
               VALUES (?, 'manual', 'succeeded', ?, '2026-08-26T08:00:00+00:00',
                       '2026-08-26T08:00:01+00:00', '2026-08-26T08:01:00+00:00')""",
            (flow, json.dumps(job)),
        ).lastrowid
        failed_artifact = {
            "status": "saved",
            "file_path": str(artifact),
            "filename": artifact.name,
            "period_key": "2026-W30",
            "row_count": 1,
        }
        failed = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, requested_by, job_json, progress_json,
                artifact_json, error, created_at, started_at, finished_at)
               VALUES (?, 'manual', 'failed', 'Analyst', ?, ?, ?, ?,
                       '2026-08-27T08:00:00+00:00', '2026-08-27T08:00:01+00:00',
                       '2026-08-27T08:00:20+00:00')""",
            (
                flow,
                json.dumps(job),
                json.dumps({"stage": "sql_handoff", "message": "Writing target"}),
                json.dumps([failed_artifact]),
                "password=do-not-leak failed at C:\\Users\\Analyst Name\\saved artifact.csv",
            ),
        ).lastrowid
        db.execute(
            """INSERT INTO flow_run_files
               (run_id, period_key, file_path, filename, file_size, checksum, row_count, status)
               VALUES (?, '2026-W30', ?, ?, ?, 'abc123', 1, 'saved')""",
            (failed, str(artifact), artifact.name, artifact.stat().st_size),
        )
        db.execute(
            """INSERT INTO flow_run_events
               (run_id, status, stage, message, error, traceback)
               VALUES (?, 'failed', 'sql_handoff', 'SQL write failed', ?, ?)""",
            (
                failed,
                "token=do-not-leak at C:\\Users\\Analyst Name\\artifact.csv",
                'File "C:\\Users\\Analyst Name\\worker.py", line 7, in run',
            ),
        )
        db.execute(
            """INSERT INTO flow_operation_timings
               (operation_type, phase, run_id, duration_ms, item_count, status)
               VALUES ('download', 'sql_handoff', ?, 900, 1, 'failed')""",
            (failed,),
        )
    return {
        "flow_id": int(flow),
        "successful_run_id": int(successful),
        "failed_run_id": int(failed),
        "artifact": artifact,
    }


def _seed_pipeline(flow_run_id: int) -> int:
    with database.get_db() as db:
        report_id = db.execute(
            "INSERT INTO reports(name) VALUES (?)", (f"AI Pipeline Report {flow_run_id}",)
        ).lastrowid
        run_id = db.execute(
            """INSERT INTO pipeline_runs
               (report_id, status, stage, trigger_type, plan_hash, plan_json,
                error, requires_inspection, notification_status)
               VALUES (?, 'failed', 'flow', 'manual', 'hash', ?,
                       'A linked Flow failed.', 1, 'pending')""",
            (
                report_id,
                json.dumps({
                    "flows": [{"browser_mode": "headless"}],
                    "materialized_views": [],
                    "warnings": [],
                    "blockers": [],
                }),
            ),
        ).lastrowid
        db.execute(
            """INSERT INTO pipeline_run_steps
               (run_id, step_type, sequence_no, entity_type, entity_id,
                entity_name, status, flow_run_id, error)
               VALUES (?, 'flow', 1, 'flow', '1', 'Linked Flow', 'failed', ?,
                       'The Flow failed.')""",
            (run_id, flow_run_id),
        )
    return int(run_id)


def _seed_action_occurrence(
    focus_type: str,
    focus_id: int,
    *,
    status: str = "open",
    revision: int = 1,
) -> dict[str, int]:
    with database.get_db() as db:
        suffix = int(
            db.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM actions").fetchone()["id"]
        )
        action_id = db.execute(
            """INSERT INTO actions
                   (type, status, fingerprint, notes, evidence_revision,
                    evidence_hash, created_at, updated_at)
               VALUES (?, ?, ?, 'AI test alert', ?, ?,
                       '2026-08-27T09:00:00+00:00', '2026-08-27T09:00:00+00:00')""",
            (
                "flow_failed" if focus_type == "flow_run" else "pipeline_failed",
                status,
                f"ai-test-{focus_type}-{focus_id}-{suffix}",
                revision,
                f"evidence-{revision}",
            ),
        ).lastrowid
        occurrence_id = db.execute(
            """INSERT INTO action_occurrences
                   (action_id, evidence_revision, focus_type, focus_id,
                    evidence_hash, summary, evidence_json, observed_at, created_at)
               VALUES (?, ?, ?, ?, ?, 'Recorded run failed', '{}',
                       '2026-08-27T09:00:00+00:00', '2026-08-27T09:00:00+00:00')""",
            (
                action_id,
                revision,
                focus_type,
                str(focus_id),
                f"evidence-{revision}",
            ),
        ).lastrowid
    return {"action_id": int(action_id), "occurrence_id": int(occurrence_id)}


def _result(ref: str, *, action: str = "inspect") -> dict:
    return {
        "conclusion": "The recorded run failed and needs review.",
        "conclusion_evidence_refs": [ref],
        "confidence": "high",
        "observed_facts": [{
            "statement": "The durable run status is failed.",
            "evidence_refs": [ref],
        }],
        "inferences": [],
        "recommendations": [{
            "action_type": action,
            "title": "Review the recorded failure",
            "rationale": "Use the existing Metronome controls only after review.",
            "evidence_refs": [ref],
        }],
        "unknowns": [],
    }


def test_operations_migration_and_read_tools_are_bounded_and_focus_locked(ai_db):
    seeded = _seed_flow(ai_db)
    with database.get_db() as db:
        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"agent_runs", "agent_steps", "agent_evidence"} <= tables

    run_id = seeded["failed_run_id"]
    summary = execute_tool(
        "get_flow_run", {"run_id": run_id},
        focus_type="flow_run", focus_id=run_id,
    ).to_dict()
    assert summary["data"]["recovery_preflight"]["resume"]["status"] == "eligible"
    assert summary["data"]["recovery_preflight"]["retry_sql"]["status"] == "eligible"
    assert summary["data"]["required_worker"] == {
        "worker_id": "bi-desktop-headless",
        "display_name": None,
        "registered": False,
        "status": "not_registered",
        "current_run_id": None,
        "last_error": None,
        "last_seen_at": None,
    }
    serialized = json.dumps(summary).casefold()
    assert "do-not-leak" not in serialized
    assert "c:\\users" not in serialized
    assert "file_path" not in serialized

    events = execute_tool(
        "get_flow_run_events", {"run_id": run_id},
        focus_type="flow_run", focus_id=run_id,
    ).to_dict()
    event_text = json.dumps(events).casefold()
    assert "do-not-leak" not in event_text
    assert "c:\\users" not in event_text
    assert "[redacted]" in event_text
    assert "[local path]" in event_text

    artifacts = execute_tool(
        "get_flow_run_artifacts", {"run_id": run_id},
        focus_type="flow_run", focus_id=run_id,
    ).to_dict()
    assert "file_path" not in json.dumps(artifacts)
    assert artifacts["data"]["recorded_files"][0]["file_still_exists"] is True

    with pytest.raises(ValueError, match="exact run"):
        execute_tool(
            "get_flow_run", {"run_id": seeded["successful_run_id"]},
            focus_type="flow_run", focus_id=run_id,
        )


def test_pipeline_can_read_only_its_explicitly_linked_flow_run(ai_db):
    seeded = _seed_flow(ai_db)
    pipeline_id = _seed_pipeline(seeded["failed_run_id"])
    linked = execute_tool(
        "get_pipeline_flow_run",
        {"pipeline_run_id": pipeline_id, "flow_run_id": seeded["failed_run_id"]},
        focus_type="pipeline_run", focus_id=pipeline_id,
    )
    assert linked.data["linked_flow_run"]["run"]["id"] == seeded["failed_run_id"]
    linked_events = execute_tool(
        "get_pipeline_flow_run_events",
        {"pipeline_run_id": pipeline_id, "flow_run_id": seeded["failed_run_id"]},
        focus_type="pipeline_run", focus_id=pipeline_id,
    )
    assert linked_events.data["linked_flow_events"]["events"][0]["stage"] == "sql_handoff"
    linked_artifacts = execute_tool(
        "get_pipeline_flow_run_artifacts",
        {"pipeline_run_id": pipeline_id, "flow_run_id": seeded["failed_run_id"]},
        focus_type="pipeline_run", focus_id=pipeline_id,
    )
    assert linked_artifacts.data["linked_flow_artifacts"]["recorded_files"][0]["filename"]
    with pytest.raises(ValueError, match="not linked"):
        execute_tool(
            "get_pipeline_flow_run",
            {"pipeline_run_id": pipeline_id, "flow_run_id": seeded["successful_run_id"]},
            focus_type="pipeline_run", focus_id=pipeline_id,
        )
    with pytest.raises(ValueError, match="exact run"):
        execute_tool(
            "get_pipeline_flow_run",
            {"pipeline_run_id": pipeline_id + 1, "flow_run_id": seeded["failed_run_id"]},
            focus_type="pipeline_run", focus_id=pipeline_id,
        )


def test_scripted_agent_persists_evidence_but_not_hidden_reasoning(ai_db):
    seeded = _seed_flow(ai_db)
    focus_id = seeded["failed_run_id"]
    run_id = run_store.create_run(
        question="What failed?", focus_type="flow_run", focus_id=focus_id, actor="Analyst"
    )
    provider = ScriptedProvider([
        AssistantTurn(
            reasoning_content="private chain of thought",
            tool_calls=(ToolCall("final-1", "submit_agent_result", _result(f"flow_run:{focus_id}")),),
            finish_reason="tool_calls",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
    ])
    operations_agent.execute_run(run_id, provider=provider)
    provider.assert_exhausted()
    result = run_store.get_run(run_id)
    assert result["status"] == "completed"
    assert result["result"]["recommendations"][0]["action_type"] == "inspect"
    assert result["usage"]["total_tokens"] == 15
    assert "private chain of thought" not in json.dumps(result)
    assert [step["tool_name"] for step in result["steps"]] == [
        "get_flow_run", "submit_agent_result"
    ]


def test_mock_preview_bounds_long_recorded_errors(ai_db, monkeypatch):
    seeded = _seed_flow(ai_db)
    focus_id = seeded["failed_run_id"]
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_runs SET error = ? WHERE id = ?",
            ("warehouse failure " * 120, focus_id),
        )
    monkeypatch.setattr(config, "AI_MOCK", True)
    run_id = run_store.create_run(
        question="What failed?", focus_type="flow_run", focus_id=focus_id, actor="Analyst"
    )

    operations_agent.execute_run(run_id)

    result = run_store.get_run(run_id)
    assert result["status"] == "completed"
    error_fact = next(
        item
        for item in result["result"]["observed_facts"]
        if item["statement"].startswith("The final recorded error is:")
    )
    assert len(error_fact["statement"]) <= 800
    assert error_fact["statement"].endswith("… [truncated]")


def test_agent_rejects_disallowed_recovery_then_accepts_repair(ai_db):
    seeded = _seed_flow(ai_db, source_type="outlook", sql_enabled=False)
    focus_id = seeded["failed_run_id"]
    run_id = run_store.create_run(
        question="Can this resume?", focus_type="flow_run", focus_id=focus_id, actor="Analyst"
    )
    invalid = _result(f"flow_run:{focus_id}", action="resume")
    repaired = _result(f"flow_run:{focus_id}", action="inspect")
    provider = ScriptedProvider([
        AssistantTurn(tool_calls=(ToolCall("bad-final", "submit_agent_result", invalid),)),
        AssistantTurn(tool_calls=(ToolCall("good-final", "submit_agent_result", repaired),)),
    ])
    operations_agent.execute_run(run_id, provider=provider)
    provider.assert_exhausted()
    result = run_store.get_run(run_id)
    assert result["status"] == "completed"
    assert result["result"]["recommendations"][0]["action_type"] == "inspect"
    assert [step["status"] for step in result["steps"]] == [
        "completed", "failed", "completed"
    ]


def test_cancel_wins_atomic_race_and_startup_recovers_steps(ai_db):
    seeded = _seed_flow(ai_db)
    focus_id = seeded["failed_run_id"]
    cancelled = run_store.create_run(
        question="Cancel me", focus_type="flow_run", focus_id=focus_id, actor="Analyst"
    )
    assert run_store.claim_run(cancelled)
    assert run_store.request_cancel(cancelled)
    assert run_store.complete_run(cancelled, _result(f"flow_run:{focus_id}"), {}) is False
    assert run_store.get_run(cancelled)["status"] == "cancelled"

    interrupted = run_store.create_run(
        question="Restart me", focus_type="flow_run", focus_id=focus_id, actor="Analyst"
    )
    assert run_store.claim_run(interrupted)
    run_store.start_step(
        interrupted, tool_call_id="stuck", tool_name="get_flow_run",
        arguments={"run_id": focus_id},
    )
    queued = run_store.create_run(
        question="Keep queued", focus_type="flow_run", focus_id=focus_id, actor="Other"
    )
    recovered = run_store.recover_interrupted_runs()
    assert queued in recovered
    assert run_store.get_run(interrupted)["status"] == "failed"
    assert run_store.get_run(interrupted)["steps"][0]["status"] == "failed"

    cancel_on_restart = run_store.create_run(
        question="Cancel across restart", focus_type="flow_run",
        focus_id=focus_id, actor="Analyst"
    )
    assert run_store.claim_run(cancel_on_restart)
    assert run_store.request_cancel(cancel_on_restart)
    run_store.recover_interrupted_runs()
    assert run_store.get_run(cancel_on_restart)["status"] == "cancelled"


def test_retry_sql_probes_files_before_taking_sqlite_write_lock(ai_db, monkeypatch):
    seeded = _seed_flow(ai_db)
    original_is_file = Path.is_file
    observed_unlocked_probe = []

    def checked_is_file(path):
        with database.get_db() as other:
            other.execute("BEGIN IMMEDIATE")
            observed_unlocked_probe.append(True)
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", checked_is_file)
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda _mode: {"status": "already_running", "mode": "headless"},
    )
    result = flows.retry_run_sql(
        seeded["failed_run_id"],
        SimpleNamespace(state=SimpleNamespace(actor="Analyst")),
    )
    assert result["status"] == "queued"
    assert observed_unlocked_probe


def test_operations_api_deduplicates_only_the_exact_same_request(ai_db, monkeypatch):
    seeded = _seed_flow(ai_db)
    submitted = []
    monkeypatch.setattr(operations_agent, "submit_run", submitted.append)
    app = FastAPI()
    app.include_router(ai_router.router)
    with TestClient(app) as client:
        body = {
            "question": "What failed?",
            "focus": {"type": "flow_run", "id": seeded["failed_run_id"]},
        }
        first = client.post("/api/ai/operations/runs", json=body)
        second = client.post("/api/ai/operations/runs", json=body)
        different = client.post(
            "/api/ai/operations/runs",
            json={**body, "question": "What completed?"},
        )
        missing = client.post(
            "/api/ai/operations/runs",
            json={"question": "Inspect", "focus": {"type": "flow_run", "id": 999999}},
        )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert different.status_code == 202
    assert different.json()["id"] != first.json()["id"]
    assert missing.status_code == 404
    assert submitted == [first.json()["id"], different.json()["id"]]


def test_alert_binding_is_server_derived_validated_and_deduplicated(ai_db, monkeypatch):
    seeded = _seed_flow(ai_db)
    linked = _seed_action_occurrence("flow_run", seeded["failed_run_id"])
    submitted = []
    monkeypatch.setattr(operations_agent, "submit_run", submitted.append)
    app = FastAPI()
    app.include_router(ai_router.router)
    body = {
        "question": "What failed?",
        "action_id": linked["action_id"],
        "occurrence_id": linked["occurrence_id"],
    }
    with TestClient(app) as client:
        first = client.post("/api/ai/operations/runs", json=body)
        repeated = client.post("/api/ai/operations/runs", json=body)
        mismatched_focus = client.post(
            "/api/ai/operations/runs",
            json={
                **body,
                "focus": {
                    "type": "flow_run",
                    "id": seeded["successful_run_id"],
                },
            },
        )
        missing_occurrence = client.post(
            "/api/ai/operations/runs",
            json={"question": "Inspect", "action_id": linked["action_id"]},
        )
        unknown_occurrence = client.post(
            "/api/ai/operations/runs",
            json={**body, "occurrence_id": 999999},
        )
        standalone = client.post(
            "/api/ai/operations/runs",
            json={
                "question": "Inspect the successful run",
                "focus": {
                    "type": "flow_run",
                    "id": seeded["successful_run_id"],
                },
            },
        )

    assert first.status_code == 202
    payload = first.json()
    assert payload["id"] == repeated.json()["id"]
    assert payload["focus_type"] == "flow_run"
    assert payload["focus_id"] == seeded["failed_run_id"]
    assert payload["action_id"] == linked["action_id"]
    assert payload["occurrence_id"] == linked["occurrence_id"]
    assert payload["alert_evidence_revision"] == 1
    assert payload["current_alert_evidence_revision"] == 1
    assert payload["is_current"] is True
    assert mismatched_focus.status_code == 409
    assert missing_occurrence.status_code == 422
    assert unknown_occurrence.status_code == 404
    assert standalone.status_code == 202
    assert standalone.json()["action_id"] is None
    assert standalone.json()["alert_binding"] is None
    assert submitted == [payload["id"], standalone.json()["id"]]


def test_run_history_auto_links_only_one_unambiguous_current_occurrence(ai_db):
    seeded = _seed_flow(ai_db)
    linked = _seed_action_occurrence("flow_run", seeded["failed_run_id"])
    run_id = run_store.create_run(
        question="Inspect this run",
        focus_type="flow_run",
        focus_id=seeded["failed_run_id"],
        actor="Analyst",
    )
    run = run_store.get_run(run_id)
    assert run["action_id"] == linked["action_id"]
    assert run["occurrence_id"] == linked["occurrence_id"]

    _seed_action_occurrence("flow_run", seeded["failed_run_id"])
    with pytest.raises(run_store.RunBindingConflict, match="More than one active alert"):
        run_store.create_run(
            question="Do not guess which alert",
            focus_type="flow_run",
            focus_id=seeded["failed_run_id"],
            actor="Analyst",
        )


def test_alert_revision_change_before_execution_fails_without_calling_model(ai_db):
    seeded = _seed_flow(ai_db)
    linked = _seed_action_occurrence("flow_run", seeded["failed_run_id"])
    run_id = run_store.create_run(
        question="What failed?",
        action_id=linked["action_id"],
        occurrence_id=linked["occurrence_id"],
        actor="Analyst",
    )
    with database.get_db() as db:
        db.execute(
            """UPDATE actions SET evidence_revision=2, evidence_hash='evidence-2'
               WHERE id=?""",
            (linked["action_id"],),
        )
    provider = ScriptedProvider([
        AssistantTurn(tool_calls=(ToolCall(
            "unused", "submit_agent_result", _result(f"flow_run:{seeded['failed_run_id']}")
        ),)),
    ])

    operations_agent.execute_run(run_id, provider=provider)

    run = run_store.get_run(run_id)
    assert provider.requests == []
    assert run["status"] == "failed"
    assert run["error_code"] == "agent_evidence_superseded"
    assert run["superseded_reason"] == "alert_evidence_changed"
    assert run["is_current"] is False
    assert run["recommendations_current"] is False
    with pytest.raises(run_store.RunBindingConflict, match="newer evidence"):
        run_store.create_run(
            question="Do not bind stale evidence",
            action_id=linked["action_id"],
            occurrence_id=linked["occurrence_id"],
            actor="Analyst",
        )


def test_alert_revision_change_during_execution_aborts_result(ai_db):
    seeded = _seed_flow(ai_db)
    focus_id = seeded["failed_run_id"]
    linked = _seed_action_occurrence("flow_run", focus_id)
    run_id = run_store.create_run(
        question="What failed?",
        action_id=linked["action_id"],
        occurrence_id=linked["occurrence_id"],
        actor="Analyst",
    )

    class SupersedingProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, _messages, _tools, *, deadline_monotonic=None):
            assert deadline_monotonic is not None
            self.calls += 1
            with database.get_db() as db:
                db.execute(
                    """UPDATE actions
                          SET evidence_revision=2, evidence_hash='evidence-2'
                        WHERE id=?""",
                    (linked["action_id"],),
                )
            return AssistantTurn(tool_calls=(ToolCall(
                "stale-final", "submit_agent_result", _result(f"flow_run:{focus_id}")
            ),))

    provider = SupersedingProvider()
    operations_agent.execute_run(run_id, provider=provider)

    run = run_store.get_run(run_id)
    assert provider.calls == 1
    assert run["status"] == "failed"
    assert run["error_code"] == "agent_evidence_superseded"
    assert run["result"] is None
    assert run["superseded_reason"] == "alert_evidence_changed"
    assert [step["tool_name"] for step in run["steps"]] == ["get_flow_run"]


def test_terminal_audit_step_closes_if_alert_changes_during_final_validation(
    ai_db, monkeypatch
):
    seeded = _seed_flow(ai_db)
    focus_id = seeded["failed_run_id"]
    linked = _seed_action_occurrence("flow_run", focus_id)
    run_id = run_store.create_run(
        question="What failed?",
        action_id=linked["action_id"],
        occurrence_id=linked["occurrence_id"],
        actor="Analyst",
    )
    provider = ScriptedProvider([
        AssistantTurn(tool_calls=(ToolCall(
            "final", "submit_agent_result", _result(f"flow_run:{focus_id}")
        ),)),
    ])
    original_execute_tool = operations_agent.execute_tool
    calls = 0

    def supersede_after_fresh_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        envelope = original_execute_tool(*args, **kwargs)
        if calls == 2:
            with database.get_db() as db:
                db.execute(
                    """UPDATE actions
                          SET evidence_revision=2, evidence_hash='evidence-2'
                        WHERE id=?""",
                    (linked["action_id"],),
                )
        return envelope

    monkeypatch.setattr(operations_agent, "execute_tool", supersede_after_fresh_read)
    operations_agent.execute_run(run_id, provider=provider)

    run = run_store.get_run(run_id)
    assert run["status"] == "failed"
    assert run["error_code"] == "agent_evidence_superseded"
    assert [(step["tool_name"], step["status"]) for step in run["steps"]] == [
        ("get_flow_run", "completed"),
        ("submit_agent_result", "failed"),
    ]


@pytest.mark.parametrize(
    ("closed_status", "expected_reason"),
    [("resolved", "alert_resolved"), ("expected", "alert_expected")],
)
def test_completed_alert_result_becomes_historical_when_alert_closes(
    ai_db, closed_status, expected_reason
):
    seeded = _seed_flow(ai_db)
    focus_id = seeded["failed_run_id"]
    linked = _seed_action_occurrence("flow_run", focus_id)
    run_id = run_store.create_run(
        question="What failed?",
        action_id=linked["action_id"],
        occurrence_id=linked["occurrence_id"],
        actor="Analyst",
    )
    provider = ScriptedProvider([
        AssistantTurn(tool_calls=(ToolCall(
            "final", "submit_agent_result", _result(f"flow_run:{focus_id}")
        ),)),
    ])
    operations_agent.execute_run(run_id, provider=provider)
    provider.assert_exhausted()
    current = run_store.get_run(run_id)
    assert current["status"] == "completed"
    assert current["result"]["recommendations"][0]["action_type"] == "inspect"

    with database.get_db() as db:
        db.execute(
            """UPDATE actions SET status=?, resolved_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (closed_status, linked["action_id"]),
        )

    historical = run_store.get_run(run_id)
    assert historical["status"] == "completed"
    assert historical["superseded"] is True
    assert historical["superseded_reason"] == expected_reason
    assert historical["is_current"] is False
    assert historical["recommendations_current"] is False
    assert historical["result"]["recommendations"] == []
    assert historical["result"]["historical_recommendations"][0]["action_type"] == "inspect"


def test_openai_provider_parses_native_qwen_tools_and_bounds_arguments(monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={
            "id": "response-1",
            "model": "Qwen/Qwen3.8-27B",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": None,
                    "reasoning": "opaque reasoning",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "get_flow_run", "arguments": {"run_id": 7}},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        })

    monkeypatch.setattr(config, "AI_MOCK", False)
    monkeypatch.setattr(config, "AI_API_URL", "https://qwen.example.test/v1/chat/completions")
    monkeypatch.setattr(config, "AI_PROVIDER_PROFILE", "qwen_vllm")
    monkeypatch.setattr(openai_provider.time, "sleep", lambda _seconds: None)
    provider = OpenAIChatProvider(transport=httpx.MockTransport(handler))
    turn = provider.complete(
        [{"role": "user", "content": "inspect"}],
        [{"type": "function", "function": {"name": "get_flow_run", "parameters": {}}}],
    )
    assert len(requests) == 2
    assert requests[-1]["chat_template_kwargs"]["preserve_thinking"] is True
    assert requests[-1]["temperature"] == 1.0
    assert turn.reasoning_content == "opaque reasoning"
    assert turn.tool_calls[0].arguments == {"run_id": 7}
    assert turn.usage == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}

    with pytest.raises(AIProtocolError, match="oversized"):
        openai_provider._json_object({"payload": "x" * (33 * 1024)})
    with pytest.raises(AITransportTimeout, match="already expired"):
        provider.complete(
            [{"role": "user", "content": "too late"}], [],
            deadline_monotonic=time.monotonic() - 1,
        )


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "content": "<TOOL_CALL>{}</TOOL_CALL>"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "type": "function",
                "function": {"name": "get_flow_run", "arguments": "{\"run_id\": 1}"},
            }],
        },
    ],
)
def test_openai_provider_rejects_raw_markup_and_missing_tool_ids(monkeypatch, message):
    monkeypatch.setattr(config, "AI_MOCK", False)
    monkeypatch.setattr(config, "AI_API_URL", "https://qwen.example.test/v1/chat/completions")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "choices": [{"finish_reason": "tool_calls", "message": message}],
    }))
    with pytest.raises(AIProtocolError):
        OpenAIChatProvider(transport=transport).complete(
            [{"role": "user", "content": "inspect"}], []
        )
