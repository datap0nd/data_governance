import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app import config, database
from app.ai import operations_agent, run_store
from app.ai.fake_provider import ScriptedProvider
from app.ai.operations_tools import Evidence, MAX_TOOL_RESULT_BYTES, ToolEnvelope, execute_tool
from app.ai.protocol import AgentResult, AssistantTurn, ToolCall
from app.routers import actions, email


@pytest.fixture()
def alert_ai_db(monkeypatch):
    operations_agent.shutdown_executor()
    with TemporaryDirectory(
        prefix="metronome-alert-ai-", ignore_cleanup_errors=True
    ) as folder:
        db_path = str(Path(folder) / "alert-ai.db")
        monkeypatch.setattr(database, "DB_PATH", db_path)
        monkeypatch.setattr(config, "AI_MOCK", True)
        database.init_db()
        yield
    operations_agent.shutdown_executor()


def _seed_source_alert(*, revision: int = 1) -> int:
    with database.get_db() as db:
        source_id = db.execute(
            """INSERT INTO sources(name, type, connection_info, source_query, owner)
               VALUES ('ASAP import', 'postgres',
                       'postgresql://admin:secret@warehouse/private',
                       'SELECT * FROM salary_private', 'Data Owner')"""
        ).lastrowid
        db.execute(
            """INSERT INTO source_probes
                   (source_id, probed_at, last_data_at, row_count, status, message)
               VALUES (?, '2026-08-28T08:00:00+00:00',
                       '2026-08-20T08:00:00+00:00', 42, 'outdated',
                       'token=private-token at C:\\Users\\Analyst\\raw.csv')""",
            (source_id,),
        )
        action_id = db.execute(
            """INSERT INTO actions
                   (source_id, type, status, notes, fingerprint,
                    evidence_revision, evidence_hash, created_at, updated_at)
               VALUES (?, 'stale_source', 'open',
                       'password=private-password at C:\\Users\\Analyst\\raw.csv',
                       'stale-source-test', ?, 'hash',
                       '2026-08-28T08:00:00+00:00',
                       '2026-08-28T08:00:00+00:00')""",
            (source_id, revision),
        ).lastrowid
    return int(action_id)


def test_generic_alert_context_is_broad_bounded_and_redacted(alert_ai_db):
    action_id = _seed_source_alert()
    with database.get_db() as db:
        db.execute(
            """UPDATE actions
                  SET notes=notes || ' JSON {"api_key":"json-secret-123"}'
                           || ' Authorization: Bearer bearer-secret-456'
                WHERE id=?""",
            (action_id,),
        )
        db.execute(
            """UPDATE source_probes
                  SET message=message || ' password="quoted secret value"'
                WHERE source_id=(SELECT source_id FROM actions WHERE id=?)""",
            (action_id,),
        )

    envelope = execute_tool(
        "get_alert_context",
        {"action_id": action_id},
        focus_type="alert",
        focus_id=action_id,
    ).to_dict()
    rendered = json.dumps(envelope)

    assert envelope["data"]["alert"]["type"] == "stale_source"
    assert envelope["data"]["source_context"]["latest_probes"][0]["status"] == "outdated"
    assert envelope["data"]["scope_note"].startswith("Read-only")
    assert "private-password" not in rendered
    assert "private-token" not in rendered
    assert "json-secret-123" not in rendered
    assert "bearer-secret-456" not in rendered
    assert "quoted secret value" not in rendered
    assert "admin:secret" not in rendered
    assert "salary_private" not in rendered
    assert "C:\\Users\\Analyst" not in rendered
    assert "[redacted]" in rendered
    with pytest.raises(ValueError, match="locked"):
        execute_tool(
            "get_alert_context",
            {"action_id": action_id + 1},
            focus_type="alert",
            focus_id=action_id,
        )


def test_large_alert_context_compacts_instead_of_failing(alert_ai_db):
    long_text = "diagnostic-" + ("x" * 1200)
    with database.get_db() as db:
        report_id = db.execute(
            "INSERT INTO reports(name, owner) VALUES (?, 'Owner')",
            ("Large report " + ("r" * 250),),
        ).lastrowid
        for index in range(30):
            source_id = db.execute(
                "INSERT INTO sources(name, type) VALUES (?, 'postgres')",
                (f"Large source {index} " + ("s" * 220),),
            ).lastrowid
            db.execute(
                "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (?, ?, ?)",
                (report_id, f"table_{index}_" + ("t" * 300), source_id),
            )
            db.execute(
                """INSERT INTO source_probes(source_id, status, message)
                   VALUES (?, 'error', ?)""",
                (source_id, long_text),
            )
        plan = json.dumps({
            "flows": [],
            "materialized_views": [],
            "warnings": [long_text] * 20,
            "blockers": [long_text] * 20,
        })
        pipeline_id = db.execute(
            """INSERT INTO pipeline_runs
                   (report_id, status, stage, trigger_type, plan_hash, plan_json,
                    error, requires_inspection)
               VALUES (?, 'failed', 'flow', 'manual', 'large', ?, ?, 1)""",
            (report_id, plan, long_text),
        ).lastrowid
        for index in range(50):
            db.execute(
                """INSERT INTO pipeline_run_steps
                       (run_id, step_type, sequence_no, entity_type, entity_id,
                        entity_name, status, error)
                   VALUES (?, 'flow', ?, 'flow', ?, ?, 'failed', ?)""",
                (pipeline_id, index, str(index), f"Flow {index}", long_text),
            )
            db.execute(
                """INSERT INTO pipeline_resource_locks(resource_type, resource_key, run_id)
                   VALUES ('flow_target', ?, ?)""",
                (f"file|C:\\Users\\Analyst\\{index}\\" + ("p" * 300), pipeline_id),
            )
        action_id = db.execute(
            """INSERT INTO actions
                   (report_id, type, status, notes, fingerprint,
                    evidence_revision, evidence_hash)
               VALUES (?, 'pipeline_failed', 'open', ?, 'large-alert', 1, 'large')""",
            (report_id, long_text),
        ).lastrowid
        db.execute(
            """INSERT INTO action_occurrences
                   (action_id, evidence_revision, focus_type, focus_id,
                    evidence_hash, summary, evidence_json, observed_at)
               VALUES (?, 1, 'pipeline_run', ?, 'large', ?, '{}', CURRENT_TIMESTAMP)""",
            (action_id, pipeline_id, long_text),
        )

    envelope = execute_tool(
        "get_alert_context", {"action_id": action_id},
        focus_type="alert", focus_id=action_id,
    ).to_dict()
    encoded = json.dumps(envelope).encode("utf-8")
    assert len(encoded) <= MAX_TOOL_RESULT_BYTES
    assert envelope["truncated"] is True
    assert envelope["data"]["truncation"]["truncated"] is True
    assert "C:\\Users\\Analyst" not in encoded.decode("utf-8")


def test_context_fingerprint_ignores_heartbeats_and_unrelated_scan_noise():
    first = {
        "alert": {"type": "flow_failed", "notes": "SQL failed"},
        "source_context": {
            "latest_probes": [{
                "id": 10,
                "probed_at": "2026-08-28T10:00:00Z",
                "status": "outdated",
                "last_data_at": "2026-08-20T00:00:00Z",
                "row_count": 42,
                "message": "Old data",
            }],
        },
        "focused_run": {
            "run": {"id": 7, "status": "failed"},
            "required_worker": {
                "worker_id": "desktop-1",
                "last_seen_at": "2026-08-28T10:00:00Z",
            },
        },
        "platform_observation": {"latest_scan": {"id": 100, "status": "completed"}},
    }
    second = json.loads(json.dumps(first))
    second["source_context"]["latest_probes"][0]["id"] = 11
    second["source_context"]["latest_probes"][0]["probed_at"] = "2026-08-28T10:05:00Z"
    second["focused_run"]["required_worker"]["last_seen_at"] = "2026-08-28T10:05:00Z"
    second["platform_observation"]["latest_scan"]["id"] = 101

    assert run_store._stable_alert_context(first) == run_store._stable_alert_context(second)

    second["focused_run"]["run"]["status"] = "succeeded"
    assert run_store._stable_alert_context(first) != run_store._stable_alert_context(second)


def test_active_alert_is_automatically_analyzed_once_per_revision(
    alert_ai_db, monkeypatch
):
    action_id = _seed_source_alert()
    submitted = []
    monkeypatch.setattr(operations_agent, "submit_run", submitted.append)

    first = operations_agent.enrich_active_alerts()
    assert first["queued"] == 1
    assert len(submitted) == 1
    run_id = submitted[0]
    queued = run_store.get_run(run_id)
    assert queued["mode"] == "alert_auto"
    assert queued["focus_type"] == "alert"
    assert queued["focus_id"] == action_id
    assert queued["action_evidence_revision"] == 1

    operations_agent.execute_run(run_id)
    completed = run_store.get_run(run_id)
    assert completed["status"] == "completed"
    assert completed["result"]["alert_assessment"] == "uncertain"
    assert completed["result"]["confidence"] == "low"
    assert "no model judgment was made" in completed["result"]["conclusion"]
    assert completed["read_only"] is True

    submitted.clear()
    second = operations_agent.enrich_active_alerts()
    assert second["queued"] == 0
    assert submitted == []

    with database.get_db() as db:
        db.execute(
            """UPDATE actions SET evidence_revision=2, evidence_hash='new-hash',
                      updated_at='2026-08-28T09:00:00+00:00'
               WHERE id=?""",
            (action_id,),
        )
    submitted.clear()
    third = operations_agent.enrich_active_alerts()
    assert third["queued"] == 1
    assert run_store.get_run(run_id)["superseded_reason"] == "alert_evidence_changed"


def test_occurrence_analysis_does_not_suppress_overall_alert_analysis(alert_ai_db):
    action_id = _seed_source_alert()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO agent_runs
                   (mode, question, focus_type, focus_id, status, actor,
                    model, reasoning_effort, provider_mode, prompt_version,
                    action_id, action_evidence_revision, final_json)
               VALUES ('incident', 'Review this occurrence', 'flow_run', ?,
                       'completed', 'Analyst', 'test-model', 'medium', 'mock',
                       'test-v1', ?, 1, '{}')""",
            ("999", action_id),
        )

    run_id, created = run_store.create_or_reuse_auto_alert_run(action_id)

    assert created is True
    assert run_id is not None
    run = run_store.get_run(run_id)
    assert run["focus_type"] == "alert"
    assert run["mode"] == "alert_auto"


def test_qwen_alert_review_gets_redacted_snapshot_and_structured_assessment(alert_ai_db):
    action_id = _seed_source_alert()
    run_id, created = run_store.create_or_reuse_auto_alert_run(action_id)
    assert created and run_id
    result = {
        "conclusion": "The latest probe supports the stale-source Alert.",
        "conclusion_evidence_refs": [f"alert:{action_id}"],
        "alert_assessment": "confirmed",
        "confidence": "high",
        "observed_facts": [{
            "statement": "The current probe is outdated.",
            "evidence_refs": [f"alert:{action_id}"],
        }],
        "inferences": [],
        "recommendations": [{
            "action_type": "contact_owner",
            "title": "Confirm the upstream refresh",
            "rationale": "The source owner should confirm its next expected load.",
            "evidence_refs": [f"alert:{action_id}"],
        }],
        "unknowns": [],
    }
    provider = ScriptedProvider([
        AssistantTurn(tool_calls=(
            ToolCall("final-alert", "submit_agent_result", result),
        )),
    ])

    operations_agent.execute_run(run_id, provider=provider)
    provider.assert_exhausted()
    completed = run_store.get_run(run_id)
    assert completed["status"] == "completed"
    assert completed["result"]["alert_assessment"] == "confirmed"
    request = provider.requests[0]
    request_text = json.dumps(request)
    assert "private-password" not in request_text
    assert "private-token" not in request_text
    assert "salary_private" not in request_text
    assert {tool["function"]["name"] for tool in request["tools"]} == {
        "get_alert_context", "submit_agent_result"
    }


def test_flow_alert_recovery_suggestion_requires_nested_preflight(monkeypatch):
    ref = "alert:7"
    result = AgentResult.model_validate({
        "conclusion": "The Flow can safely resume.",
        "conclusion_evidence_refs": [ref],
        "alert_assessment": "confirmed",
        "confidence": "high",
        "observed_facts": [{"statement": "Resume is eligible.", "evidence_refs": [ref]}],
        "recommendations": [{
            "action_type": "resume",
            "title": "Resume the Flow",
            "rationale": "The server preflight is eligible.",
            "evidence_refs": [ref],
        }],
    })
    seed = ToolEnvelope(
        data={
            "current_occurrence": {"focus_type": "flow_run"},
            "focused_run": {"recovery_preflight": {
                "resume": {"status": "eligible"},
            }},
        },
        evidence=(Evidence(ref, "alert", "7", "Alert #7", "/#alerts", "now"),),
        observed_at="now",
    )
    monkeypatch.setattr(run_store, "evidence_keys", lambda run_id: {ref})

    operations_agent._validate_terminal_result(
        1, result, focus_type="alert", seed=seed
    )
    seed.data["focused_run"]["recovery_preflight"]["resume"]["status"] = "blocked"
    with pytest.raises(ValueError, match="preflight is blocked"):
        operations_agent._validate_terminal_result(
            1, result, focus_type="alert", seed=seed
        )
    seed.data["current_occurrence"]["focus_type"] = "pipeline_run"
    with pytest.raises(ValueError, match="not valid for this investigation focus"):
        operations_agent._validate_terminal_result(
            1, result, focus_type="alert", seed=seed
        )


def test_alert_api_and_email_use_only_current_completed_assessment(alert_ai_db):
    action_id = _seed_source_alert()
    run_id, created = run_store.create_or_reuse_auto_alert_run(action_id)
    assert created and run_id
    operations_agent.execute_run(run_id)

    payload = actions.list_action_occurrences(action_id)
    assert payload["occurrences"] == []
    assert payload["current_analysis_run_id"] == run_id
    assert payload["current_analysis_status"] == "completed"
    assert payload["current_analysis_is_current"] is True

    current = email._current_alert_ai_assessments([action_id])
    assert current[action_id]["run_id"] == run_id
    assert current[action_id]["conclusion"]

    alert = {
        "id": action_id,
        "type": "stale_source",
        "status": "open",
        "asset_name": "ASAP import",
        "asset_type": "source",
        "degraded_since": "2026-08-28T08:00:00+00:00",
        "ai_assessment": current[action_id],
    }
    summary = email._build_alert_summary(
        {"name": "Data Owner", "email": "owner@example.test"}, [alert]
    )
    assert "Deterministic preview:" in summary["body_text"]
    assert current[action_id]["conclusion"] in summary["body_text"]
    assert "Next action:" in summary["body_text"]

    with database.get_db() as db:
        db.execute(
            "UPDATE actions SET status='resolved', resolved_at=CURRENT_TIMESTAMP WHERE id=?",
            (action_id,),
        )
    assert email._current_alert_ai_assessments([action_id]) == {}


def test_live_context_change_supersedes_analysis_without_revision_change(alert_ai_db):
    action_id = _seed_source_alert()
    first_run_id, created = run_store.create_or_reuse_auto_alert_run(action_id)
    assert created is True
    operations_agent.execute_run(first_run_id)
    assert action_id in email._current_alert_ai_assessments([action_id])

    with database.get_db() as db:
        source_id = db.execute(
            "SELECT source_id FROM actions WHERE id=?", (action_id,)
        ).fetchone()[0]
        revision_before = db.execute(
            "SELECT evidence_revision FROM actions WHERE id=?", (action_id,)
        ).fetchone()[0]
        db.execute(
            """INSERT INTO source_probes
                   (source_id, probed_at, last_data_at, row_count, status, message)
               VALUES (?, '2026-08-28T10:00:00+00:00',
                       '2026-08-28T09:55:00+00:00', 99, 'fresh', 'Recovered')""",
            (source_id,),
        )

    assert email._current_alert_ai_assessments([action_id]) == {}
    stale = run_store.get_run(first_run_id)
    assert stale["superseded_reason"] == "alert_context_changed"
    assert stale["recommendations_current"] is False

    second_run_id, created = run_store.create_or_reuse_auto_alert_run(action_id)
    assert created is True
    assert second_run_id != first_run_id
    with database.get_db() as db:
        assert db.execute(
            "SELECT evidence_revision FROM actions WHERE id=?", (action_id,)
        ).fetchone()[0] == revision_before


def test_email_explicitly_reports_pending_ai_without_blocking():
    alert = {
        "id": 9,
        "type": "pipeline_failed",
        "status": "open",
        "asset_name": "Finance",
        "asset_type": "report",
        "degraded_since": "2026-08-28T08:00:00+00:00",
        "ai_assessment": None,
        "recommendation": "Review the failed run.",
    }
    summary = email._build_alert_summary(
        {"name": "Owner", "email": "owner@example.test"}, [alert]
    )
    assert "Automated assessment: Pending or unavailable" in summary["body_text"]
    assert "deterministic Alert remains active" in summary["body_text"]
    assert "Next action: Review the failed run." in summary["body_text"]
