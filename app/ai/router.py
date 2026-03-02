"""FastAPI router for AI-powered insights endpoints."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import AI_MOCK
from app.ai.context_builder import get_full_context, get_report_context, get_dashboard_summary
from app.ai.mock_provider import mock_chat, mock_briefing, mock_report_risk, mock_suggestions
from app.ai.query_auditor import mock_audit_report, mock_audit_all

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
    at_risk_sources: list = []


class SuggestionItem(BaseModel):
    title: str
    description: str
    priority: str
    related_entity: str | None = None
    entity_id: int | None = None


class SuggestionsResponse(BaseModel):
    suggestions: list[SuggestionItem]


class AISettingsResponse(BaseModel):
    mock_mode: bool
    api_url: str
    model: str
    has_api_key: bool


def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Call the real LLM via LiteLLM. Returns response text or fallback."""
    from app.ai.client import call_llm
    return call_llm(system_prompt, user_prompt)


@router.post("/chat", response_model=ChatResponse)
def ai_chat(req: ChatRequest):
    """Chat with the AI assistant about your data ecosystem."""
    try:
        if AI_MOCK:
            ctx = get_full_context()
            result = mock_chat(req.message, ctx)
            return ChatResponse(**result)
        else:
            from app.ai.prompts import CHAT_SYSTEM, build_chat_prompt
            prompt = build_chat_prompt(req.message)
            response = _llm_call(CHAT_SYSTEM, prompt)
            return ChatResponse(response=response)
    except Exception as e:
        logger.exception("AI chat error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/briefing", response_model=BriefingResponse)
def ai_briefing():
    """Get an AI-generated dashboard briefing."""
    try:
        summary = get_dashboard_summary()
        if AI_MOCK:
            result = mock_briefing(summary)
            return BriefingResponse(**result)
        else:
            from app.ai.prompts import BRIEFING_SYSTEM, build_briefing_prompt
            prompt = build_briefing_prompt()
            response = _llm_call(BRIEFING_SYSTEM, prompt)
            # Compute risk level from actual data
            sc = summary["status_counts"]
            if sc.get("stale", 0) or sc.get("outdated", 0):
                risk = "high"
            elif sc.get("no_connection", 0):
                risk = "medium"
            else:
                risk = "low"
            return BriefingResponse(
                summary=response,
                generated_at=datetime.now(timezone.utc).isoformat(),
                risk_level=risk,
            )
    except Exception as e:
        logger.exception("AI briefing error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report-risk/{report_id}", response_model=ReportRiskResponse)
def ai_report_risk(report_id: int):
    """Get AI risk assessment for a specific report."""
    try:
        ctx = get_report_context(report_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Report not found")
        if AI_MOCK:
            result = mock_report_risk(ctx)
            return ReportRiskResponse(**result)
        else:
            from app.ai.prompts import RISK_SYSTEM, build_risk_prompt
            prompt = build_risk_prompt(report_id)
            response = _llm_call(RISK_SYSTEM, prompt)
            # Compute risk from data
            tables = ctx.get("tables", [])
            no_conn = [t for t in tables if t.get("probe_status") == "no_connection"]
            stale = [t for t in tables if t.get("probe_status") in ("stale", "outdated")]
            if no_conn or stale:
                risk = "high"
            elif any(t.get("probe_status") in (None, "unknown") for t in tables):
                risk = "medium"
            else:
                risk = "low"
            return ReportRiskResponse(risk_level=risk, assessment=response)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI report risk error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggestions", response_model=SuggestionsResponse)
def ai_suggestions():
    """Get AI-powered action suggestions."""
    try:
        summary = get_dashboard_summary()
        # Suggestions always use mock logic (structured data, not free-text)
        result = mock_suggestions(summary)
        return SuggestionsResponse(**result)
    except Exception as e:
        logger.exception("AI suggestions error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit/{report_id}")
def ai_audit_report(report_id: int):
    """Audit M expressions for a specific report."""
    try:
        ctx = get_report_context(report_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Report not found")
        result = mock_audit_report(ctx["report"], ctx["tables"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI audit error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit")
def ai_audit_all():
    """Audit M expressions for all reports."""
    try:
        from app.database import get_db
        with get_db() as db:
            reports = [dict(r) for r in db.execute("SELECT * FROM reports").fetchall()]
        all_data = []
        for r in reports:
            ctx = get_report_context(r["id"])
            if ctx:
                all_data.append(ctx)
        result = mock_audit_all(all_data)
        return result
    except Exception as e:
        logger.exception("AI audit all error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings", response_model=AISettingsResponse)
def ai_settings():
    """Get current AI configuration."""
    from app.config import AI_BASE_URL, AI_API_KEY, AI_MODEL
    return AISettingsResponse(
        mock_mode=AI_MOCK,
        api_url=AI_BASE_URL,
        model=AI_MODEL,
        has_api_key=bool(AI_API_KEY),
    )
