"""Cached Pipeline Insights API; hover requests never touch PostgreSQL or Qwen."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.local_access import require_app_access
from app.pipeline_insights import cached_sample_for_source
from app.pipeline_insights_settings import (
    get_pipeline_insights_settings,
    save_pipeline_insights_settings,
)


router = APIRouter(prefix="/api/pipeline-insights", tags=["pipeline-insights"])


class PipelineInsightsSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples_scheduled: bool = True
    explanations_scheduled: bool = True
    weekday: str = "sunday"
    time: str = "10:00"
    exclusions: list[str] = Field(default_factory=list, max_length=500)


@router.get("/settings")
def get_settings(request: Request):
    require_app_access(request)
    result = get_pipeline_insights_settings().public_dict()
    try:
        from app.main import pipeline_insights_schedule_payload
        result.update(pipeline_insights_schedule_payload())
    except Exception:
        result.update({"next_run_at": None, "scheduler_running": False})
    return result


@router.put("/settings")
def put_settings(body: PipelineInsightsSettingsRequest, request: Request):
    require_app_access(request)
    try:
        saved = save_pipeline_insights_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reschedule_error = None
    try:
        from app.main import configure_pipeline_insights_job
        configure_pipeline_insights_job()
    except Exception as exc:
        reschedule_error = str(exc)
    result = saved.public_dict()
    try:
        from app.main import pipeline_insights_schedule_payload
        result.update(pipeline_insights_schedule_payload())
    except Exception:
        result.update({"next_run_at": None, "scheduler_running": False})
    if reschedule_error:
        result["reschedule_error"] = reschedule_error
    return result


@router.get("/sources/{source_id}/sample")
def get_source_sample(source_id: int, request: Request):
    require_app_access(request)
    result = cached_sample_for_source(source_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No cached relation sample is available.")
    return result
