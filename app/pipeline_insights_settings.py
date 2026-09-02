"""Persisted schedule and exclusion settings for Pipeline Insights."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.settings import get_setting, set_setting


SETTINGS_KEY = "pipeline_insights_settings_v1"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class PipelineInsightsSettings:
    samples_scheduled: bool = True
    explanations_scheduled: bool = True
    weekday: str = "sunday"
    time: str = "10:00"
    exclusions: tuple[str, ...] = ()

    def public_dict(self) -> dict:
        return {
            "samples_scheduled": self.samples_scheduled,
            "explanations_scheduled": self.explanations_scheduled,
            "weekday": self.weekday,
            "time": self.time,
            "exclusions": list(self.exclusions),
        }


def _clean_exclusions(values) -> tuple[str, ...]:
    result = []
    for value in values or ():
        clean = str(value or "").strip()
        if not clean:
            continue
        if len(clean) > 500 or any(ord(char) < 32 for char in clean):
            raise ValueError("A Pipeline Insights exclusion is invalid.")
        if clean not in result:
            result.append(clean)
    if len(result) > 500:
        raise ValueError("At most 500 Pipeline Insights exclusions are allowed.")
    return tuple(result)


def validate_settings(value: dict) -> PipelineInsightsSettings:
    weekday = str(value.get("weekday", "sunday")).strip().casefold()
    if weekday not in WEEKDAYS:
        raise ValueError("weekday must be Monday through Sunday")
    time_value = str(value.get("time", "10:00")).strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
        raise ValueError("time must use HH:MM")
    return PipelineInsightsSettings(
        samples_scheduled=bool(value.get("samples_scheduled", True)),
        explanations_scheduled=bool(value.get("explanations_scheduled", True)),
        weekday=weekday,
        time=time_value,
        exclusions=_clean_exclusions(value.get("exclusions")),
    )


def get_pipeline_insights_settings() -> PipelineInsightsSettings:
    raw = get_setting(SETTINGS_KEY)
    if not raw:
        return PipelineInsightsSettings()
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return validate_settings(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return PipelineInsightsSettings()


def save_pipeline_insights_settings(value: dict) -> PipelineInsightsSettings:
    settings = validate_settings(value)
    set_setting(
        SETTINGS_KEY,
        json.dumps(settings.public_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    return settings
