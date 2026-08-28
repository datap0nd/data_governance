"""FastAPI router for AI-powered insights endpoints."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from app.config import AI_MOCK
from app.ai.mock_provider import mock_chat, mock_briefing, mock_report_risk
from app.routers.eventlog import get_actor

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


@router.post("/chat", response_model=ChatResponse)
def ai_chat(req: ChatRequest):
    """Chat with the AI assistant about your data ecosystem."""
    try:
        from app.ai.context_builder import get_full_context
        ctx = get_full_context()
        if AI_MOCK:
            result = mock_chat(req.message, ctx)
            return ChatResponse(**result)
        else:
            from app.ai.llm_provider import call_llm
            import json

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

            response_text = call_llm(system_prompt, req.message)
            return ChatResponse(response=response_text)
    except Exception as e:
        logger.exception("AI chat error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/briefing", response_model=BriefingResponse)
def ai_briefing():
    """Get an AI-generated dashboard briefing."""
    try:
        from app.ai.context_builder import get_dashboard_summary
        summary = get_dashboard_summary()
        result = mock_briefing(summary)
        return BriefingResponse(**result)
    except Exception as e:
        logger.exception("AI briefing error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report-risk/{report_id}", response_model=ReportRiskResponse)
def ai_report_risk(report_id: int):
    """Get AI risk assessment for a specific report."""
    try:
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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/operations/runs", status_code=status.HTTP_202_ACCEPTED)
def create_operations_run(body: OperationsRunCreate, request: Request):
    """Queue one exact, read-only Flow or Pipeline investigation."""
    from app.ai import operations_agent, run_store

    actor = get_actor(request)
    try:
        run_id, created = run_store.create_or_reuse_run(
            question=body.question,
            focus_type=body.focus.type if body.focus else None,
            focus_id=body.focus.id if body.focus else None,
            action_id=body.action_id,
            occurrence_id=body.occurrence_id,
            actor=actor,
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
