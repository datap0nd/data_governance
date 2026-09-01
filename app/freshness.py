"""Shared recurrence and freshness-health evaluation.

Flow schedule configuration is a wall-clock contract in Metronome's configured
host timezone.  Probe evidence is an instant.  Keeping those two concepts
separate here prevents the Flow scheduler and Source prober from quietly using
different timezone conventions.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from app.config import FLOW_TIMEZONE

UTC = timezone.utc
WEEKDAY_NUMBERS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _row_value(row, key: str, default=None):
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return default


def host_timezone() -> ZoneInfo:
    """Return the one named timezone used for Flow wall-clock schedules."""
    try:
        return ZoneInfo(FLOW_TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return None


def parse_source_timestamp(value: str | datetime | None) -> datetime | None:
    """Parse Source/probe evidence; legacy naive values mean UTC."""
    parsed = _parse(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_flow_timestamp(value: str | datetime | None) -> datetime | None:
    """Parse Flow history; legacy naive values mean host-local wall time."""
    parsed = _parse(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = localize_wall_time(parsed, host_timezone())
    return parsed.astimezone(UTC)


def parse_monitoring_timestamp(value: str | datetime | None) -> datetime | None:
    """Parse new monitoring fields, which are always stored as aware UTC."""
    return parse_source_timestamp(value)


def localize_wall_time(value: datetime, zone: ZoneInfo | None = None) -> datetime:
    """Attach a zone deterministically across DST folds and gaps.

    The first fold is selected for repeated wall times.  A nonexistent time is
    advanced minute-by-minute to the first valid local instant, matching the
    single-dispatch behavior expected from the scheduler.
    """
    zone = zone or host_timezone()
    naive = value.replace(tzinfo=None)
    for minute_offset in range(181):
        candidate = naive + timedelta(minutes=minute_offset)
        for fold in (0, 1):
            aware = candidate.replace(tzinfo=zone, fold=fold)
            round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
            if round_trip == candidate:
                return aware
    raise ValueError(f"Could not resolve local wall time {value!s} in {zone.key}")


def normalize_weekdays(values: Iterable[str] | None) -> list[str]:
    selected = {
        str(value).strip().casefold()
        for value in (values or [])
        if str(value).strip().casefold() in WEEKDAY_NUMBERS
    }
    return [name for name in WEEKDAY_NUMBERS if name in selected]


def schedule_rule(
    schedule_type: str | None,
    schedule_time: str | None,
    schedule_days: Iterable[str] | None = None,
    schedule_day: int | None = None,
    *,
    timezone_name: str | None = None,
) -> dict | None:
    kind = str(schedule_type or "").strip().casefold()
    if kind == "manual" or not kind:
        return None
    if kind not in {"daily", "weekly", "fixed", "monthly"}:
        raise ValueError(f"Unsupported schedule type: {schedule_type}")
    if not schedule_time or len(schedule_time) != 5:
        raise ValueError("Scheduled rules need a valid HH:MM time.")
    try:
        hour, minute = (int(part) for part in schedule_time.split(":"))
        time(hour, minute)
    except (TypeError, ValueError):
        raise ValueError("Scheduled rules need a valid HH:MM time.") from None
    days = normalize_weekdays(schedule_days)
    if kind in {"weekly", "fixed"} and not days:
        raise ValueError("Weekly rules need at least one weekday.")
    if kind == "monthly" and (not isinstance(schedule_day, int) or not 1 <= schedule_day <= 31):
        raise ValueError("Monthly rules need a day of month from 1 to 31.")
    return {
        "type": "weekly" if kind == "fixed" else kind,
        "time": schedule_time,
        "days": days,
        "day": schedule_day if kind == "monthly" else None,
        "timezone": timezone_name or FLOW_TIMEZONE,
    }


def rule_key(rule: dict | None) -> tuple | None:
    if not rule:
        return None
    return (
        rule.get("type"),
        tuple(normalize_weekdays(rule.get("days"))),
        rule.get("day"),
        rule.get("time"),
        rule.get("timezone") or FLOW_TIMEZONE,
    )


def _zone_for_rule(rule: dict) -> ZoneInfo:
    try:
        return ZoneInfo(str(rule.get("timezone") or FLOW_TIMEZONE))
    except Exception:
        return host_timezone()


def _occurs_on(rule: dict, candidate_date: date) -> bool:
    kind = rule.get("type")
    if kind == "daily":
        return True
    if kind in {"weekly", "fixed"}:
        selected = {WEEKDAY_NUMBERS[item] for item in normalize_weekdays(rule.get("days"))}
        return candidate_date.weekday() in selected
    if kind == "monthly":
        day = int(rule.get("day") or 0)
        return day > 0 and day <= calendar.monthrange(candidate_date.year, candidate_date.month)[1] and candidate_date.day == day
    return False


def occurrence_at(rule: dict, candidate_date: date) -> datetime:
    hour, minute = (int(part) for part in str(rule["time"]).split(":"))
    wall = datetime.combine(candidate_date, time(hour, minute))
    return localize_wall_time(wall, _zone_for_rule(rule)).astimezone(UTC)


def next_occurrence(rule: dict, *, after: datetime | None = None) -> datetime:
    after = after or utc_now()
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    zone = _zone_for_rule(rule)
    local_date = after.astimezone(zone).date()
    for offset in range(3700):
        candidate_date = local_date + timedelta(days=offset)
        if not _occurs_on(rule, candidate_date):
            continue
        candidate = occurrence_at(rule, candidate_date)
        if candidate > after:
            return candidate
    raise ValueError("Could not calculate the next scheduled occurrence.")


def latest_enforceable_occurrence(
    rule: dict,
    *,
    as_of: datetime | None = None,
    baseline: datetime | None = None,
    grace: timedelta = timedelta(hours=24),
) -> tuple[datetime | None, datetime | None]:
    as_of = as_of or utc_now()
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    zone = _zone_for_rule(rule)
    local_date = as_of.astimezone(zone).date()
    for offset in range(3700):
        candidate_date = local_date - timedelta(days=offset)
        if not _occurs_on(rule, candidate_date):
            continue
        occurrence = occurrence_at(rule, candidate_date)
        deadline = occurrence + grace
        if deadline > as_of:
            continue
        if baseline is not None:
            if baseline.tzinfo is None:
                baseline = baseline.replace(tzinfo=UTC)
            if occurrence < baseline.astimezone(UTC):
                return None, None
        return occurrence, deadline
    return None, None


def evaluate_timed_rule(
    rule: dict,
    *,
    evidence_at: datetime | None,
    baseline_at: datetime | None,
    as_of: datetime | None = None,
) -> dict:
    occurrence, deadline = latest_enforceable_occurrence(
        rule, as_of=as_of, baseline=baseline_at,
    )
    if occurrence is None:
        status = "pending"
    elif evidence_at is not None and evidence_at.astimezone(UTC) >= occurrence:
        status = "healthy"
    else:
        status = "overdue"
    return {
        "status": status,
        "latest_due_at": iso_utc(occurrence),
        "grace_deadline_at": iso_utc(deadline),
        "evidence_at": iso_utc(evidence_at),
        "baseline_at": iso_utc(baseline_at),
        "timezone": rule.get("timezone") or FLOW_TIMEZONE,
    }


def flow_freshness(flow, *, as_of: datetime | None = None) -> tuple[dict | None, dict]:
    schedule_type = str(_row_value(flow, "schedule_type", "manual") or "manual")
    if schedule_type == "manual":
        return None, {
            "status": "not_monitored", "latest_due_at": None,
            "grace_deadline_at": None, "evidence_at": None,
            "baseline_at": None, "timezone": FLOW_TIMEZONE,
        }
    days_raw = _row_value(flow, "schedule_days") or []
    if isinstance(days_raw, str):
        import json
        try:
            days_raw = json.loads(days_raw)
        except (TypeError, ValueError):
            days_raw = [item.strip() for item in days_raw.split(",") if item.strip()]
    rule = schedule_rule(
        schedule_type,
        _row_value(flow, "schedule_time"),
        days_raw,
        _row_value(flow, "schedule_day"),
    )
    baseline = parse_monitoring_timestamp(_row_value(flow, "freshness_effective_from_at"))
    evidence = parse_monitoring_timestamp(_row_value(flow, "last_execution_success_at"))
    if not bool(_row_value(flow, "enabled")):
        return rule, {
            "status": "paused", "latest_due_at": None,
            "grace_deadline_at": None, "evidence_at": iso_utc(evidence),
            "baseline_at": iso_utc(baseline), "timezone": FLOW_TIMEZONE,
        }
    return rule, evaluate_timed_rule(
        rule, evidence_at=evidence, baseline_at=baseline, as_of=as_of,
    )
