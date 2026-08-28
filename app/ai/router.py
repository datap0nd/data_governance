"""FastAPI router for AI-powered insights endpoints."""

import json
import logging
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from app.ai.mock_provider import mock_chat, mock_briefing, mock_report_risk
from app.ai.model_catalog import list_available_models
from app.ai.openai_provider import OpenAIChatProvider
from app.ai.protocol import (
    AIConfigurationError,
    AIProtocolError,
    AITransportError,
    AITransportTimeout,
    AIUpstreamError,
)
from app.ai.runtime_config import (
    AIModelCatalogRequest,
    AISettingsUpdate,
    candidate_model_catalog_settings,
    candidate_runtime_settings,
    load_runtime_settings,
    sanitize_ai_error,
    save_runtime_settings,
)
from app.database import get_db
from app.routers.eventlog import get_actor, log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ChatRequest(BaseModel):
    message: str
    context: str | None = None


class ChatResponse(BaseModel):
    response: str
    sources_referenced: list = []
    reports_referenced: list = []


class BriefingResponse(BaseModel):
    summary: str
    generated_at: str
    risk_level: str


class ReportRiskResponse(BaseModel):
    risk_level: str
    assessment: str
    at_risk_sources: list = []  # kept for API compat, contains degraded sources


class AgentFocus(BaseModel):
    type: Literal["flow_run", "pipeline_run"]
    id: int = Field(ge=1)


class OperationsRunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    focus: AgentFocus | None = None
    action_id: int | None = Field(default=None, ge=1)
    occurrence_id: int | None = Field(default=None, ge=1)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Ask a question about the selected run.")
        return cleaned

    @model_validator(mode="after")
    def validate_focus_or_alert_binding(self):
        if (self.action_id is None) != (self.occurrence_id is None):
            raise ValueError("action_id and occurrence_id must be supplied together.")
        if self.focus is None and self.action_id is None:
            raise ValueError("Select an exact run or alert occurrence to investigate.")
        return self


@router.get("/settings")
def get_ai_settings():
    """Return live AI settings through an API-key-free public projection."""
    return load_runtime_settings().public_dict()


@router.put("/settings")
def update_ai_settings(body: AISettingsUpdate, request: Request):
    """Atomically save live settings. A blank/omitted key keeps the current key."""
    try:
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = load_runtime_settings(db)
            updated = save_runtime_settings(db, body)
            changed = sorted(
                field
                for field in body.model_fields_set
                if field not in {"api_key", "clear_api_key"}
            )
            if body.submitted_api_key():
                key_action = "configured"
            elif body.clear_api_key:
                key_action = "cleared"
            elif previous.api_key and not updated.api_key:
                key_action = "cleared_for_endpoint_change"
            else:
                key_action = "unchanged"
            log_event(
                db,
                "system",
                None,
                "AI settings",
                "settings_updated",
                json.dumps(
                    {"changed_fields": changed, "api_key": key_action},
                    separators=(",", ":"),
                ),
                get_actor(request),
            )
    except ValueError as exc:
        raise HTTPException(422, sanitize_ai_error(exc)) from exc
    return updated.public_dict()


@router.post("/settings/models")
def get_available_ai_models(body: AIModelCatalogRequest):
    """List model IDs from an unsaved or saved OpenAI-compatible endpoint."""
    try:
        candidate = candidate_model_catalog_settings(body)
        models = list_available_models(candidate)
    except ValueError as exc:
        raise HTTPException(422, sanitize_ai_error(exc)) from exc
    except AIConfigurationError as exc:
        raise HTTPException(
            422, sanitize_ai_error(exc, body.submitted_api_key())
        ) from exc
    except AITransportTimeout as exc:
        raise HTTPException(
            504, sanitize_ai_error(exc, candidate.api_key)
        ) from exc
    except (AITransportError, AIUpstreamError, AIProtocolError) as exc:
        raise HTTPException(
            502, sanitize_ai_error(exc, candidate.api_key)
        ) from exc
    return {"models": models}


@router.post("/settings/test")
def test_ai_settings(body: AISettingsUpdate):
    """Test native model tool-call compatibility without sending app data."""
    try:
        candidate = candidate_runtime_settings(body)
    except ValueError as exc:
        raise HTTPException(422, sanitize_ai_error(exc)) from exc
    if not candidate.qwen_enabled:
        return {
            "ok": False,
            "status": "not_configured",
            "message": "Choose Local AI mode to test a model endpoint.",
            "latency_ms": None,
            "model": candidate.model,
            "tool_calls_compatible": False,
        }

    nonce = uuid.uuid4().hex
    tool = {
        "type": "function",
        "function": {
            "name": "metronome_connection_check",
            "description": "Return the supplied nonce to verify native tool-call support.",
            "parameters": {
                "type": "object",
                "properties": {"nonce": {"type": "string"}},
                "required": ["nonce"],
                "additionalProperties": False,
            },
        },
    }
    started = time.monotonic()
    failure: Exception | None = None
    try:
        turn = OpenAIChatProvider(settings=candidate).complete(
            [
                {
                    "role": "system",
                    "content": (
                        "This is a connection test. Call metronome_connection_check "
                        "exactly once with the nonce. Do not answer in prose."
                    ),
                },
                {"role": "user", "content": f"Nonce: {nonce}"},
            ],
            [tool],
            deadline_monotonic=time.monotonic()
            + min(30.0, candidate.http_timeout_seconds),
        )
        compatible = bool(
            len(turn.tool_calls) == 1
            and turn.tool_calls[0].name == "metronome_connection_check"
            and turn.tool_calls[0].arguments == {"nonce": nonce}
        )
        if not compatible:
            return {
                "ok": False,
                "status": "tool_calls_incompatible",
                "message": (
                    "The endpoint responded, but it did not return the required native tool call. "
                    "Enable native reasoning and tool-call parsers on the model server; "
                    "Qwen on vLLM requires the Qwen parsers."
                ),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "model": candidate.model,
                "tool_calls_compatible": False,
            }
        return {
            "ok": True,
            "status": "connected",
            "message": "Endpoint, model, and native tool calls are ready.",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "model": candidate.model,
            "tool_calls_compatible": True,
        }
    except AIConfigurationError as exc:
        failure = exc
        status_key = (
            "credentials_rejected"
            if "credential" in str(exc).casefold()
            else "not_configured"
        )
    except AITransportTimeout as exc:
        failure = exc
        status_key = "timeout"
    except AITransportError as exc:
        failure = exc
        status_key = "unreachable"
    except AIUpstreamError as exc:
        failure = exc
        status_key = "upstream_error"
    except AIProtocolError as exc:
        failure = exc
        status_key = "tool_calls_incompatible"
    except Exception as exc:
        failure = exc
        logger.error(
            "Unexpected AI connection-test failure: %s",
            sanitize_ai_error(exc, candidate.api_key),
        )
        status_key = "test_failed"
    return {
        "ok": False,
        "status": status_key,
        "message": sanitize_ai_error(failure, candidate.api_key),
        "latency_ms": round((time.monotonic() - started) * 1000),
        "model": candidate.model,
        "tool_calls_compatible": False,
    }


@router.post("/chat", response_model=ChatResponse)
def ai_chat(req: ChatRequest):
    """Chat with the AI assistant about your data ecosystem."""
    snapshot = None
    try:
        snapshot = load_runtime_settings()
        if not snapshot.enabled:
            raise HTTPException(503, "AI is disabled in System > AI.")
        from app.ai.context_builder import get_full_context
        ctx = get_full_context()
        if snapshot.mock_mode:
            result = mock_chat(req.message, ctx)
            return ChatResponse(**result)
        else:
            from app.ai.llm_provider import call_llm

            system_prompt = (
                "You are the MX Analytics assistant. You help BI managers understand "
                "the health of their data sources, reports, and alerts. Answer concisely "
                "based on the data context provided. If you don't know, say so.\n\n"
                "DATA CONTEXT:\n" + json.dumps({
                    "sources": [{"name": s["name"], "type": s["type"], "status": s.get("probe_status", "unknown")} for s in ctx["sources"]],
                    "reports": [{"name": r["name"], "owner": r.get("owner"), "source_count": r.get("source_count", 0)} for r in ctx["reports"]],
                    "alerts_active": len(ctx["alerts"]),
                    "last_scan": ctx["last_scan"]["started_at"] if ctx.get("last_scan") else None,
                }, indent=None)
            )

            response_text = call_llm(system_prompt, req.message, settings=snapshot)
            return ChatResponse(response=response_text)
    except HTTPException:
        raise
    except Exception as e:
        safe_message = sanitize_ai_error(
            e,
            snapshot.api_key if snapshot is not None else "",
        )
        logger.error("AI chat error: %s", safe_message)
        raise HTTPException(status_code=502, detail=safe_message)


@router.get("/briefing", response_model=BriefingResponse)
def ai_briefing():
    """Get an AI-generated dashboard briefing."""
    try:
        if not load_runtime_settings().enabled:
            raise HTTPException(503, "AI is disabled in System > AI.")
        from app.ai.context_builder import get_dashboard_summary
        summary = get_dashboard_summary()
        result = mock_briefing(summary)
        return BriefingResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI briefing error")
        raise HTTPException(status_code=500, detail=sanitize_ai_error(e))


@router.get("/report-risk/{report_id}", response_model=ReportRiskResponse)
def ai_report_risk(report_id: int):
    """Get AI risk assessment for a specific report."""
    try:
        if not load_runtime_settings().enabled:
            raise HTTPException(503, "AI is disabled in System > AI.")
        from app.ai.context_builder import get_report_context
        ctx = get_report_context(report_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Report not found")
        result = mock_report_risk(ctx)
        return ReportRiskResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI report risk error")
        raise HTTPException(status_code=500, detail=sanitize_ai_error(e))


@router.post("/operations/runs", status_code=status.HTTP_202_ACCEPTED)
def create_operations_run(body: OperationsRunCreate, request: Request):
    """Queue one exact, read-only Flow or Pipeline investigation."""
    from app.ai import operations_agent, run_store

    snapshot = load_runtime_settings()
    if not snapshot.feature_enabled("operations_investigator"):
        raise HTTPException(503, "Operations Investigator is disabled in System > AI.")
    actor = get_actor(request)
    try:
        run_id, created = run_store.create_or_reuse_run(
            question=body.question,
            focus_type=body.focus.type if body.focus else None,
            focus_id=body.focus.id if body.focus else None,
            action_id=body.action_id,
            occurrence_id=body.occurrence_id,
            actor=actor,
            settings=snapshot,
        )
    except run_store.RunBindingNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except run_store.RunBindingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except run_store.RunBindingUnsupported as exc:
        raise HTTPException(422, str(exc)) from exc
    except run_store.RunBindingError as exc:
        raise HTTPException(422, str(exc)) from exc
    if run_id is None:
        raise HTTPException(429, "Too many investigations are already queued. Try again shortly.")
    if created:
        operations_agent.submit_run(run_id)
    return run_store.get_run(run_id)


@router.get("/operations/runs/{run_id}")
def get_operations_run(run_id: int):
    from app.ai import run_store

    run = run_store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Investigation not found.")
    return run


@router.post("/operations/runs/{run_id}/cancel")
def cancel_operations_run(run_id: int):
    from app.ai import run_store

    if not run_store.request_cancel(run_id):
        raise HTTPException(404, "Investigation not found.")
    return run_store.get_run(run_id)
