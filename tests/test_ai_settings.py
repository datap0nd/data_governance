import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import config, database
from app.ai import operations_agent, router as ai_router, run_store, runtime_config
from app.ai.protocol import AssistantTurn, ToolCall
from app.routers import email


@pytest.fixture()
def ai_settings_db(monkeypatch):
    operations_agent.shutdown_executor()
    with TemporaryDirectory(
        prefix="metronome-ai-settings-", ignore_cleanup_errors=True
    ) as folder:
        path = str(Path(folder) / "settings.db")
        monkeypatch.setattr(database, "DB_PATH", path)
        monkeypatch.setattr(config, "AI_MOCK", True)
        monkeypatch.setattr(config, "AI_API_URL", "http://localhost:11434/v1/chat/completions")
        monkeypatch.setattr(config, "AI_API_KEY", "")
        monkeypatch.setattr(config, "AI_MODEL", "Qwen/Qwen3.8-27B")
        database.init_db()
        app = FastAPI()
        app.include_router(ai_router.router)
        yield TestClient(app)
    operations_agent.shutdown_executor()


def _put_qwen(client: TestClient, **overrides):
    body = {
        "mode": "qwen",
        "endpoint": "http://qwen.example.test:8000/v1",
        "model": "Qwen/test-27B",
        "provider_profile": "qwen_vllm",
        "reasoning_effort": "medium",
        "max_tool_calls": 7,
        "max_model_turns": 5,
        "max_seconds": 120,
        "http_timeout_seconds": 45,
        "max_output_tokens": 2048,
        "temperature": 0.7,
        "top_p": 0.9,
        "operations_investigator_enabled": True,
        "automatic_alert_review_enabled": True,
        "alert_email_analysis_enabled": True,
        "documentation_suggestions_enabled": True,
    }
    body.update(overrides)
    return client.put("/api/ai/settings", json=body)


def _seed_alert() -> int:
    with database.get_db() as db:
        source_id = db.execute(
            "INSERT INTO sources(name, type, owner) VALUES ('AI settings source', 'postgres', 'Owner')"
        ).lastrowid
        db.execute(
            """INSERT INTO source_probes(source_id, status, message)
               VALUES (?, 'outdated', 'Recorded source is stale')""",
            (source_id,),
        )
        action_id = db.execute(
            """INSERT INTO actions
                   (source_id, type, status, fingerprint, evidence_revision,
                    evidence_hash, created_at, updated_at)
               VALUES (?, 'stale_source', 'open', 'ai-settings-alert', 1,
                       'v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (source_id,),
        ).lastrowid
    return int(action_id)


def test_invalid_environment_endpoint_does_not_prevent_metronome_startup(
    ai_settings_db, monkeypatch, caplog
):
    monkeypatch.setattr(config, "AI_MOCK", False)
    monkeypatch.setattr(config, "AI_API_URL", "not-a-valid-endpoint")

    resolved = runtime_config.environment_settings()

    assert resolved.mode == "disabled"
    assert resolved.endpoint == ""
    assert "AI endpoint is invalid" in caplog.text
    assert "not-a-valid-endpoint" not in caplog.text


def test_settings_round_trip_normalizes_endpoint_and_never_returns_or_logs_key(
    ai_settings_db, monkeypatch,
):
    client = ai_settings_db
    initial = client.get("/api/ai/settings")
    assert initial.status_code == 200
    assert initial.json()["mode"] == "preview"
    assert initial.json()["configuration_source"] == "environment"

    secret = "local-qwen-key-do-not-return"
    saved = _put_qwen(client, api_key=secret)
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["endpoint"] == "http://qwen.example.test:8000/v1/chat/completions"
    assert payload["api_key_configured"] is True
    assert payload["api_key_source"] == "system"
    assert secret not in saved.text
    assert "api_key" not in payload

    reread = client.get("/api/ai/settings")
    assert secret not in reread.text
    with database.get_db() as db:
        key_row = db.execute(
            "SELECT value FROM app_settings WHERE key=?", (runtime_config.API_KEY_KEY,)
        ).fetchone()
        event = db.execute(
            "SELECT detail FROM event_log WHERE entity_name='AI settings' ORDER BY id DESC"
        ).fetchone()
    assert key_row["value"] == secret
    assert secret not in event["detail"]
    assert json.loads(event["detail"])["api_key"] == "configured"

    # Blank means keep; removal is an explicit, separate action.
    kept = _put_qwen(client, api_key="", model="Qwen/updated-27B")
    assert kept.status_code == 200
    assert kept.json()["api_key_configured"] is True
    monkeypatch.setattr(config, "AI_API_KEY", "environment-key-must-not-resurrect")
    cleared = client.put("/api/ai/settings", json={"clear_api_key": True})
    assert cleared.status_code == 200
    assert cleared.json()["api_key_configured"] is False
    assert cleared.json()["api_key_source"] == "none"
    assert client.get("/api/ai/settings").json()["api_key_configured"] is False
    with database.get_db() as db:
        tombstone = db.execute(
            "SELECT value FROM app_settings WHERE key=?", (runtime_config.API_KEY_KEY,)
        ).fetchone()
    assert tombstone["value"] == ""


def test_secret_is_redacted_from_validation_and_connection_failures(
    ai_settings_db, monkeypatch, caplog
):
    client = ai_settings_db
    secret = "never-echo-this-key"
    conflict = client.put(
        "/api/ai/settings", json={"api_key": secret, "clear_api_key": True}
    )
    assert conflict.status_code == 422
    assert secret not in conflict.text

    class FailingProvider:
        def __init__(self, *args, settings=None, **kwargs):
            self.settings = settings

        def complete(self, *_args, **_kwargs):
            raise RuntimeError(f"Authorization: Bearer {self.settings.api_key}")

    monkeypatch.setattr(ai_router, "OpenAIChatProvider", FailingProvider)
    result = client.post(
        "/api/ai/settings/test",
        json={
            "mode": "qwen",
            "endpoint": "http://qwen.example.test/v1",
            "model": "Qwen/test",
            "api_key": secret,
        },
    )
    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert secret not in result.text
    assert "[redacted]" in result.json()["message"]
    assert secret not in caplog.text

    malformed_secret = "malformed-secret-that-must-not-be-reflected"
    malformed = client.put(
        "/api/ai/settings",
        json={"api_key": [malformed_secret]},
    )
    assert malformed.status_code == 422
    assert malformed_secret not in malformed.text


def test_connection_test_sends_only_nonce_and_requires_native_tool_call(
    ai_settings_db, monkeypatch
):
    client = ai_settings_db
    observed = {}

    class CompatibleProvider:
        def __init__(self, *args, settings=None, **kwargs):
            observed["settings"] = settings

        def complete(self, messages, tools, **_kwargs):
            rendered = json.dumps({"messages": messages, "tools": tools})
            observed["request"] = rendered
            assert "source" not in rendered.casefold()
            assert "report" not in rendered.casefold()
            nonce = messages[-1]["content"].split("Nonce: ", 1)[1]
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        "connection-check",
                        "metronome_connection_check",
                        {"nonce": nonce},
                    ),
                )
            )

    monkeypatch.setattr(ai_router, "OpenAIChatProvider", CompatibleProvider)
    result = client.post(
        "/api/ai/settings/test",
        json={
            "mode": "qwen",
            "endpoint": "http://qwen.example.test/v1",
            "model": "Qwen/test",
            "api_key": "connection-secret",
        },
    )
    assert result.status_code == 200
    assert result.json() | {"latency_ms": None} == {
        "ok": True,
        "status": "connected",
        "message": "Endpoint, model, and native tool calls are ready.",
        "latency_ms": None,
        "model": "Qwen/test",
        "tool_calls_compatible": True,
    }
    assert "connection-secret" not in result.text


def test_chat_and_operations_never_log_or_persist_configured_key(
    ai_settings_db, monkeypatch, caplog
):
    client = ai_settings_db
    secret = "configured-provider-key-must-stay-private"
    saved = _put_qwen(client, api_key=secret)
    assert saved.status_code == 200
    snapshot = runtime_config.load_runtime_settings()

    from app.ai import llm_provider

    monkeypatch.setattr(
        llm_provider,
        "call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"provider failed with {secret}")
        ),
    )
    chat = client.post("/api/ai/chat", json={"message": "Hello"})
    assert chat.status_code == 502
    assert secret not in chat.text

    action_id = _seed_alert()
    run_id, created = run_store.create_or_reuse_auto_alert_run(
        action_id, settings=snapshot
    )
    assert created and run_id

    class FailingProvider:
        def complete(self, *_args, **_kwargs):
            raise RuntimeError(f"unexpected provider failure {secret}")

    operations_agent.execute_run(
        run_id,
        provider=FailingProvider(),
        settings=snapshot,
    )
    failed = run_store.get_run(run_id)
    assert failed["status"] == "failed"
    assert secret not in str(failed.get("error"))
    assert secret not in caplog.text


def test_disabled_mode_gates_operations_docs_and_email_content(ai_settings_db):
    client = ai_settings_db
    disabled = client.put("/api/ai/settings", json={"mode": "disabled"})
    assert disabled.status_code == 200

    manual = client.post(
        "/api/ai/operations/runs",
        json={"question": "Inspect", "focus": {"type": "flow_run", "id": 1}},
    )
    assert manual.status_code == 503
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0

    with pytest.raises(HTTPException) as docs_error:
        from app.routers.documentation import ai_suggest_doc

        ai_suggest_doc(1)
    assert docs_error.value.status_code == 503

    email_payload = email.get_alert_summaries()
    assert email_payload["ai_analysis"] == {
        "enabled": False,
        "state": "disabled",
        "reason": "AI analysis is disabled in System > AI.",
        "mode": "disabled",
    }
    summary = email._build_alert_summary(
        {"name": "Owner", "email": "owner@example.test"},
        [{
            "type": "stale_source",
            "asset_name": "Warehouse",
            "recommendation": "Use deterministic recovery",
            "ai_assessment": {
                "provider_mode": "qwen",
                "conclusion": "SHOULD NOT APPEAR",
                "recommendation_title": "Use model advice",
            },
        }],
        include_ai_analysis=False,
    )
    assert "SHOULD NOT APPEAR" not in summary["body_text"]
    assert "Use model advice" not in summary["body_text"]
    assert "Use deterministic recovery" in summary["body_text"]


def test_model_config_change_supersedes_auto_alert_and_queues_fresh_review(
    ai_settings_db,
):
    client = ai_settings_db
    action_id = _seed_alert()
    original_settings = runtime_config.load_runtime_settings()
    run_id, created = run_store.create_or_reuse_auto_alert_run(
        action_id, settings=original_settings
    )
    assert created and run_id
    operations_agent.execute_run(run_id, settings=original_settings)
    assert run_store.get_run(run_id)["status"] == "completed"

    changed = client.put("/api/ai/settings", json={"model": "Qwen/new-preview-model"})
    assert changed.status_code == 200
    latest_settings = runtime_config.load_runtime_settings()
    assert latest_settings.fingerprint != original_settings.fingerprint
    historical = run_store.get_run(run_id)
    assert historical["is_current"] is False
    assert historical["superseded_reason"] == "ai_configuration_changed"

    replacement, replacement_created = run_store.create_or_reuse_auto_alert_run(
        action_id, settings=latest_settings
    )
    assert replacement_created is True
    assert replacement != run_id
    assert run_store.get_run(replacement)["config_fingerprint"] == latest_settings.fingerprint


def test_running_alert_keeps_its_starting_snapshot_but_result_becomes_historical(
    ai_settings_db,
):
    client = ai_settings_db
    action_id = _seed_alert()
    starting = runtime_config.load_runtime_settings()
    run_id, created = run_store.create_or_reuse_auto_alert_run(
        action_id, settings=starting
    )
    assert created and run_id
    claimed = run_store.claim_run(run_id, settings=starting)
    assert claimed["status"] == "running"
    assert claimed["model"] == starting.model

    changed = client.put("/api/ai/settings", json={"model": "Qwen/next-model"})
    assert changed.status_code == 200
    # Polling a live run does not rewrite/cancel the immutable configuration it
    # started with.
    still_running = run_store.get_run(run_id)
    assert still_running["status"] == "running"
    assert still_running["model"] == starting.model
    assert still_running["superseded_at"] is None

    assert run_store.complete_run(
        run_id,
        {
            "conclusion": "Completed with the starting snapshot.",
            "recommendations": [],
        },
        {},
    ) is True
    historical = run_store.get_run(run_id)
    assert historical["status"] == "completed"
    assert historical["is_current"] is False
    assert historical["superseded_reason"] == "ai_configuration_changed"
