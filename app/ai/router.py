"""FastAPI router for AI-powered insights endpoints."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import AI_MOCK
from app.ai.mock_provider import mock_chat, mock_briefing, mock_report_risk

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
