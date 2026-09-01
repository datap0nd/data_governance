"""Managed Source freshness inheritance and exact direct-output bindings."""

from __future__ import annotations

import json
import ntpath
import re
from typing import Iterable
from zoneinfo import ZoneInfo

from app.config import FLOW_TIMEZONE
from app.freshness import (
    evaluate_timed_rule,
    flow_freshness,
    iso_utc,
    normalize_weekdays,
    parse_monitoring_timestamp,
    parse_source_timestamp,
    rule_key,
    schedule_rule,
    utc_now,
)
from app.source_identity import file_flow_target, normalize_file_path, static_flow_filename


def _value(row, key: str, default=None):
    return row[key] if row is not None and key in row.keys() else default


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads(value, default):
    try:
        return json.loads(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _cron_int(value: str, minimum: int, maximum: int) -> int | None:
    if not re.fullmatch(r"\d+", value or ""):
        return None
    number = int(value)
    return number if minimum <= number <= maximum else None


_CRON_DAY_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}
_FLOW_DAY_FOR_CRON = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


def _cron_day(value: str) -> int | None:
    raw = value.strip().casefold()
    if raw in _CRON_DAY_NAMES:
        return _CRON_DAY_NAMES[raw]
    number = _cron_int(raw, 0, 7)
    return 0 if number == 7 else number


def _cron_days(field: str) -> list[str] | None:
    if any(marker in field.casefold() for marker in ("/", "#", "l", "?")):
        return None
    selected: set[int] = set()
    for token in field.split(","):
        token = token.strip()
        if not token:
            return None
        if "-" in token:
            parts = token.split("-", 1)
            start, end = _cron_day(parts[0]), _cron_day(parts[1])
            if start is None or end is None or start > end:
                return None
            selected.update(range(start, end + 1))
        else:
            day = _cron_day(token)
            if day is None:
                return None
            selected.add(day)
    return normalize_weekdays(_FLOW_DAY_FOR_CRON[day] for day in selected)


def normalize_cron_schedule(expression: str | None, timezone_name: str | None) -> dict | None:
    """Return only cron schedules that the freshness evaluator represents exactly."""
    raw = str(expression or "").strip()
    zone = str(timezone_name or "").strip()
    if not raw or not zone:
        return None
    try:
        ZoneInfo(zone)
    except Exception:
        return None
    aliases = {
        "@daily": "0 0 * * *",
        "@weekly": "0 0 * * 0",
        "@monthly": "0 0 1 * *",
    }
    raw = aliases.get(raw.casefold(), raw)
    parts = raw.split()
    if len(parts) != 5:
        return None
    minute = _cron_int(parts[0], 0, 59)
    hour = _cron_int(parts[1], 0, 23)
    if minute is None or hour is None or parts[3] != "*":
        return None
    schedule_time = f"{hour:02d}:{minute:02d}"
    dom, dow = parts[2], parts[4]
    if dom == "*" and dow == "*":
        return schedule_rule("daily", schedule_time, timezone_name=zone)
    if dom == "*" and dow != "*":
        days = _cron_days(dow)
        return schedule_rule("weekly", schedule_time, days, timezone_name=zone) if days else None
    if dow == "*":
        month_day = _cron_int(dom, 1, 31)
        return schedule_rule("monthly", schedule_time, schedule_day=month_day, timezone_name=zone) if month_day else None
    # PostgreSQL cron's DOM/DOW combination has OR semantics and cannot be
    # represented by one Metronome rule without changing meaning.
    return None


def advisory_schedule_rule(expression: str | None) -> dict | None:
    """Parse legacy display text conservatively; it never becomes authoritative."""
    raw = str(expression or "").strip()
    if not raw or "disabled" in raw.casefold():
        return None
    exact = normalize_cron_schedule(raw, FLOW_TIMEZONE)
    if exact:
        return exact
    lower = raw.casefold()
    if lower in {"daily", "every day"}:
        return {"type": "daily", "time": None, "days": [], "day": None, "timezone": FLOW_TIMEZONE}
    days = [day for day in normalize_weekdays(
        name for name in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        if re.search(rf"\b(?:{name}|{name[:3]})\b", lower)
    )]
    if days:
        return {"type": "weekly", "time": None, "days": days, "day": None, "timezone": FLOW_TIMEZONE}
    return None


def _legacy_auto_set_matches(source, event_detail: str | None) -> bool:
    inferred = advisory_schedule_rule(_value(source, "refresh_schedule"))
    stored = str(_value(source, "freshness_rule_type") or "").casefold()
    refresh_text = str(_value(source, "refresh_schedule") or "").strip()
    detail = str(event_detail or "")
    if not inferred or not refresh_text or f"source refresh schedule: {refresh_text};" not in detail:
        return False
    if inferred["type"] == "daily":
        return stored == "daily" and detail.startswith("set daily from source refresh schedule:")
    if inferred["type"] == "weekly":
        stored_days = normalize_weekdays(
            str(_value(source, "freshness_schedule_days") or "").split(",")
        )
        rule_label = ",".join(day.capitalize() for day in stored_days)
        return (
            stored == "fixed"
            and stored_days == normalize_weekdays(inferred.get("days"))
            and detail.startswith(f"set {rule_label} from source refresh schedule:")
        )
    if inferred["type"] == "monthly":
        return stored == "monthly" and int(_value(source, "freshness_schedule_day") or 0) == int(inferred.get("day") or 0)
    return False


def upsert_schedule_evidence(
    db,
    *,
    source_id: int,
    origin: str,
    external_id: str,
    expression: str,
    timezone_name: str | None,
    active: bool,
    authoritative: bool,
    generation: str | None = None,
    observed_at: str | None = None,
) -> dict | None:
    rule = normalize_cron_schedule(expression, timezone_name) if authoritative else advisory_schedule_rule(expression)
    now = observed_at or iso_utc(utc_now())
    db.execute(
        """INSERT INTO source_schedule_evidence
           (source_id, origin, external_id, raw_expression, rule_type,
            schedule_time, schedule_days, schedule_day, timezone, supported,
            authoritative, active, scan_generation, observed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id, origin, external_id) DO UPDATE SET
             raw_expression=excluded.raw_expression,
             rule_type=excluded.rule_type,
             schedule_time=excluded.schedule_time,
             schedule_days=excluded.schedule_days,
             schedule_day=excluded.schedule_day,
             timezone=excluded.timezone,
             supported=excluded.supported,
             authoritative=excluded.authoritative,
             active=excluded.active,
             scan_generation=excluded.scan_generation,
             observed_at=excluded.observed_at""",
        (
            int(source_id), origin, str(external_id), str(expression),
            rule.get("type") if rule else None,
            rule.get("time") if rule else None,
            _json(rule.get("days", [])) if rule else None,
            rule.get("day") if rule else None,
            rule.get("timezone") if rule else timezone_name,
            int(rule is not None), int(authoritative), int(active), generation, now,
        ),
    )
    return rule


def expire_schedule_evidence_generation(db, *, origin: str, generation: str) -> int:
    cursor = db.execute(
        """UPDATE source_schedule_evidence SET active=0
           WHERE origin=? AND active=1 AND COALESCE(scan_generation, '')<>?""",
        (origin, generation),
    )
    return int(cursor.rowcount)


def _evidence_rule(row) -> dict | None:
    if not _value(row, "supported"):
        return None
    return {
        "type": row["rule_type"],
        "time": row["schedule_time"],
        "days": _loads(row["schedule_days"], []),
        "day": row["schedule_day"],
        "timezone": row["timezone"] or FLOW_TIMEZONE,
    }


def _flow_rule(row) -> dict | None:
    days = _loads(_value(row, "schedule_days"), [])
    try:
        return schedule_rule(
            _value(row, "schedule_type"), _value(row, "schedule_time"),
            days, _value(row, "schedule_day"),
        )
    except ValueError:
        return None


def _source_rule(row) -> dict | None:
    rule_type = str(_value(row, "freshness_rule_type") or "").casefold()
    if rule_type == "custom" and int(_value(row, "custom_fresh_days") or 0) > 0:
        return {
            "type": "custom", "fresh_days": int(_value(row, "custom_fresh_days")),
            "time": None, "days": [], "day": None,
            "timezone": _value(row, "freshness_timezone") or FLOW_TIMEZONE,
        }
    if rule_type == "fixed":
        rule_type = "weekly"
    if rule_type not in {"daily", "weekly", "monthly"}:
        return None
    return {
        "type": rule_type,
        "time": _value(row, "freshness_schedule_time"),
        "days": normalize_weekdays(str(_value(row, "freshness_schedule_days") or "").split(",")),
        "day": _value(row, "freshness_schedule_day"),
        "timezone": _value(row, "freshness_timezone") or FLOW_TIMEZONE,
    }


def _manual_rule_valid(row) -> bool:
    kind = str(_value(row, "freshness_rule_type") or "").casefold()
    days = int(_value(row, "custom_fresh_days") or 0)
    if not kind and days > 0:
        return True
    if kind == "custom":
        return days > 0
    if kind == "daily":
        return True
    if kind == "fixed":
        return bool(normalize_weekdays(str(_value(row, "freshness_schedule_days") or "").split(",")))
    if kind == "monthly":
        return 1 <= int(_value(row, "freshness_schedule_day") or 0) <= 31
    return False


def _exact_producer_flows(db, source_id: int):
    return db.execute(
        """SELECT DISTINCT f.*
             FROM flows f
             LEFT JOIN flow_file_source_bindings b
               ON b.flow_id=f.id AND b.active=1 AND b.source_id=?
            WHERE (f.sql_handoff_enabled=1 AND f.sql_target_source_id=?)
               OR b.source_id IS NOT NULL
            ORDER BY f.id""",
        (source_id, source_id),
    ).fetchall()


def _group_rules(items: Iterable[tuple[dict, object]]) -> dict[tuple, list[object]]:
    grouped: dict[tuple, list[object]] = {}
    for rule, owner in items:
        grouped.setdefault(rule_key(rule), []).append(owner)
    return grouped


def _managed_values(rule: dict | None) -> tuple:
    if not rule:
        return (None, None, None, None, None)
    kind = rule.get("type")
    return (
        "fixed" if kind == "weekly" else kind,
        1 if kind == "daily" else None,
        ",".join(day.capitalize() for day in normalize_weekdays(rule.get("days"))) or None,
        rule.get("time"),
        rule.get("day"),
    )


def reconcile_source(db, source_id: int, *, now: str | None = None, recalculate_probe: bool = False) -> dict:
    now = now or iso_utc(utc_now())
    source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        return {"source_id": source_id, "status": "missing", "changed": False, "skipped": True}
    mode = str(_value(source, "freshness_mode") or "inherit").casefold()
    before_rule = _source_rule(source)
    before_status = _value(source, "freshness_rule_status")

    # Preserve concrete rules written by older integrations that do not know
    # about freshness_mode yet. Managed rows always carry reconciliation
    # provenance/status, so this compatibility promotion cannot capture them.
    legacy_concrete = bool(
        str(_value(source, "freshness_rule_type") or "").strip()
        or int(_value(source, "custom_fresh_days") or 0) > 0
        or str(_value(source, "freshness_schedule_days") or "").strip()
    )
    if mode == "inherit" and before_status is None and _value(source, "freshness_rule_origin") is None and legacy_concrete:
        db.execute("UPDATE sources SET freshness_mode='manual' WHERE id=?", (source_id,))
        mode = "manual"

    if mode == "manual":
        valid = _manual_rule_valid(source)
        status = "manual" if valid else "manual_invalid"
        changed = (_value(source, "freshness_rule_origin") != "manual" or before_status != status)
        if changed:
            db.execute(
                "UPDATE sources SET freshness_rule_origin='manual', freshness_rule_status=? WHERE id=?",
                (status, source_id),
            )
        return {"source_id": source_id, "status": status, "changed": changed, "skipped": True}

    if mode == "disabled":
        values = (None, None, None, None, None, "disabled", "disabled", "[]", "[]", "[]", None, source_id)
        expected = (None, None, None, None, None, "disabled", "disabled", "[]", "[]", "[]", None)
        current = (
            _value(source, "freshness_rule_type"), _value(source, "custom_fresh_days"),
            _value(source, "freshness_schedule_days"), _value(source, "freshness_schedule_time"),
            _value(source, "freshness_schedule_day"), _value(source, "freshness_rule_origin"),
            before_status, _value(source, "freshness_producer_flow_ids", "[]"),
            _value(source, "freshness_conflicts_json", "[]"), _value(source, "freshness_warnings_json", "[]"),
            _value(source, "freshness_effective_from_at"),
        )
        changed = current != expected
        if changed:
            db.execute(
                """UPDATE sources SET freshness_rule_type=?, custom_fresh_days=?,
                   freshness_schedule_days=?, freshness_schedule_time=?, freshness_schedule_day=?,
                   freshness_timezone=NULL, freshness_rule_origin=?, freshness_rule_status=?,
                   freshness_producer_flow_ids=?, freshness_conflicts_json=?, freshness_warnings_json=?,
                   freshness_effective_from_at=? WHERE id=?""",
                values,
            )
        return {"source_id": source_id, "status": "disabled", "changed": changed, "skipped": True}

    refresh_text = str(_value(source, "refresh_schedule") or "").strip()
    legacy_row = db.execute(
        """SELECT raw_expression FROM source_schedule_evidence
           WHERE source_id=? AND origin='legacy_unknown' AND external_id='legacy'""",
        (source_id,),
    ).fetchone()
    if refresh_text and (not legacy_row or legacy_row["raw_expression"] != refresh_text):
        upsert_schedule_evidence(
            db, source_id=source_id, origin="legacy_unknown", external_id="legacy",
            expression=refresh_text, timezone_name=FLOW_TIMEZONE, active=True,
            authoritative=False, observed_at=now,
        )
    elif not refresh_text and legacy_row:
        db.execute(
            """UPDATE source_schedule_evidence SET active=0
               WHERE source_id=? AND origin='legacy_unknown' AND external_id='legacy'""",
            (source_id,),
        )

    evidence = db.execute(
        """SELECT * FROM source_schedule_evidence
           WHERE source_id=? AND active=1 ORDER BY origin, external_id""",
        (source_id,),
    ).fetchall()
    authoritative = [(_evidence_rule(row), row) for row in evidence if row["authoritative"] and row["supported"]]
    authoritative = [(rule, row) for rule, row in authoritative if rule]
    advisory = [(_evidence_rule(row), row) for row in evidence if not row["authoritative"] and row["supported"]]
    advisory = [(rule, row) for rule, row in advisory if rule]

    flows = _exact_producer_flows(db, source_id)
    producer_ids = [int(row["id"]) for row in flows]
    active_flow_rules = [(rule, row) for row in flows if row["enabled"] and (rule := _flow_rule(row))]
    paused_flow_rules = [(rule, row) for row in flows if not row["enabled"] and row["schedule_type"] != "manual" and (rule := _flow_rule(row))]
    auth_groups = _group_rules(authoritative)
    flow_groups = _group_rules(active_flow_rules)
    advisory_groups = _group_rules(advisory)

    effective = None
    origin = None
    status = "unmapped"
    conflicts: list[dict] = []
    unsupported_evidence = [
        {"id": int(row["id"]), "origin": row["origin"], "expression": row["raw_expression"]}
        for row in evidence if not row["supported"]
    ]
    warnings: list[dict] = (
        [{"code": "unsupported_source_schedule", "evidence": unsupported_evidence}]
        if unsupported_evidence else []
    )

    if len(auth_groups) > 1:
        status, origin = "conflict", "conflict"
        conflicts.append({"code": "authoritative_source_schedules_disagree", "evidence_ids": [int(row["id"]) for _, row in authoritative]})
    elif auth_groups:
        effective = authoritative[0][0]
        status, origin = "mapped", "source_schedule"
        shadowed = [int(row["id"]) for rule, row in active_flow_rules if rule_key(rule) != rule_key(effective)]
        if shadowed:
            warnings.append({"code": "schedule_collision", "shadowed_flow_ids": shadowed})
    elif len(flow_groups) > 1:
        status, origin = "conflict", "conflict"
        conflicts.append({"code": "flow_schedules_disagree", "flow_ids": [int(row["id"]) for _, row in active_flow_rules]})
    elif flow_groups:
        effective = active_flow_rules[0][0]
        mismatched_advisory = [int(row["id"]) for rule, row in advisory if rule_key(rule) != rule_key(effective)]
        if mismatched_advisory:
            effective = None
            status, origin = "conflict", "conflict"
            conflicts.append({"code": "advisory_schedule_disagrees_with_flow", "evidence_ids": mismatched_advisory, "flow_ids": producer_ids})
        else:
            status, origin = "mapped", "flow_schedule"
    elif paused_flow_rules:
        paused_groups = _group_rules(paused_flow_rules)
        effective = paused_flow_rules[0][0] if len(paused_groups) == 1 else None
        status, origin = "suspended", "flow_schedule"
    elif len(advisory_groups) > 1:
        status, origin = "conflict", "conflict"
        conflicts.append({"code": "advisory_schedules_disagree", "evidence_ids": [int(row["id"]) for _, row in advisory]})
    elif advisory_groups:
        effective = advisory[0][0]
        status, origin = "mapped", "legacy_source_schedule"

    rule_type, fresh_days, schedule_days, schedule_time, schedule_day = _managed_values(effective)
    timezone_name = effective.get("timezone") if effective else None
    producer_json = _json(producer_ids)
    conflict_json = _json(conflicts)
    warning_json = _json(warnings)
    previous_key = rule_key(before_rule)
    next_key = rule_key(effective)
    baseline = _value(source, "freshness_effective_from_at")
    if status == "mapped" and (before_status != "mapped" or previous_key != next_key or not baseline):
        baseline = now
    elif status not in {"mapped", "suspended"}:
        baseline = None

    expected = (
        rule_type, fresh_days, schedule_days, schedule_time, schedule_day,
        timezone_name, origin, status, producer_json, conflict_json, warning_json, baseline,
    )
    current = (
        _value(source, "freshness_rule_type"), _value(source, "custom_fresh_days"),
        _value(source, "freshness_schedule_days"), _value(source, "freshness_schedule_time"),
        _value(source, "freshness_schedule_day"), _value(source, "freshness_timezone"),
        _value(source, "freshness_rule_origin"), before_status,
        _value(source, "freshness_producer_flow_ids", "[]"),
        _value(source, "freshness_conflicts_json", "[]"),
        _value(source, "freshness_warnings_json", "[]"),
        _value(source, "freshness_effective_from_at"),
    )
    changed = current != expected
    if changed:
        db.execute(
            """UPDATE sources SET freshness_rule_type=?, custom_fresh_days=?,
               freshness_schedule_days=?, freshness_schedule_time=?, freshness_schedule_day=?,
               freshness_timezone=?, freshness_rule_origin=?, freshness_rule_status=?,
               freshness_producer_flow_ids=?, freshness_conflicts_json=?, freshness_warnings_json=?,
               freshness_effective_from_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (*expected, source_id),
        )
        if recalculate_probe:
            _recalculate_latest_probe(db, source_id)
    return {"source_id": source_id, "status": status, "changed": changed, "skipped": False}


def _recalculate_latest_probe(db, source_id: int) -> None:
    from app.scanner.prober import _compute_status_for_rule, _rule_for_source
    source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    probe = db.execute(
        """SELECT id, CAST(last_data_at AS TEXT) AS last_data_at FROM source_probes
           WHERE source_id=? ORDER BY probed_at DESC, id DESC LIMIT 1""",
        (source_id,),
    ).fetchone()
    if source and probe:
        db.execute(
            "UPDATE source_probes SET status=? WHERE id=?",
            (_compute_status_for_rule(probe["last_data_at"], _rule_for_source(source)), probe["id"]),
        )


def reconcile_all_sources(
    db,
    *,
    source_ids: Iterable[int] | None = None,
    recalculate_probes: bool = False,
) -> dict:
    ids = list(source_ids) if source_ids is not None else [int(row["id"]) for row in db.execute("SELECT id FROM sources ORDER BY id").fetchall()]
    counts = {"reconciled": 0, "changed": 0, "unchanged": 0, "conflicted": 0, "unmapped": 0, "skipped": 0}
    now = iso_utc(utc_now())
    for source_id in ids:
        result = reconcile_source(db, int(source_id), now=now, recalculate_probe=recalculate_probes)
        counts["reconciled"] += 1
        counts["changed" if result["changed"] else "unchanged"] += 1
        if result.get("skipped"):
            counts["skipped"] += 1
        if result["status"] == "conflict":
            counts["conflicted"] += 1
        if result["status"] in {"unmapped", "suspended"}:
            counts["unmapped"] += 1
    return counts


def source_freshness_payload(row) -> dict:
    mode = _value(row, "freshness_mode") or "inherit"
    status = _value(row, "freshness_rule_status") or "unmapped"
    rule = _source_rule(row)
    baseline = parse_monitoring_timestamp(_value(row, "freshness_effective_from_at"))
    evidence = parse_source_timestamp(
        _value(row, "latest_last_data_at", _value(row, "last_data_at"))
    )
    if rule and rule.get("time") and status in {"mapped", "manual"}:
        health = evaluate_timed_rule(
            rule, evidence_at=evidence,
            baseline_at=baseline if mode == "inherit" else None,
        )
    else:
        probe_status = _value(row, "latest_status", _value(row, "status"))
        health = {
            "status": (
                "healthy" if probe_status == "fresh" else
                "overdue" if probe_status in {"outdated", "stale"} else
                status
            ),
            "latest_due_at": None,
            "grace_deadline_at": None,
            "evidence_at": iso_utc(evidence),
            "baseline_at": iso_utc(baseline),
            "timezone": _value(row, "freshness_timezone") or FLOW_TIMEZONE,
        }
    return {
        "mode": mode,
        "origin": _value(row, "freshness_rule_origin"),
        "status": status,
        "rule": rule,
        "health": health,
        "timezone": _value(row, "freshness_timezone") or FLOW_TIMEZONE,
        "baseline_at": _value(row, "freshness_effective_from_at"),
        "producer_flow_ids": _loads(_value(row, "freshness_producer_flow_ids"), []),
        "warnings": _loads(_value(row, "freshness_warnings_json"), []),
        "conflicts": _loads(_value(row, "freshness_conflicts_json"), []),
    }


def _binding_target(db, flow, published_path: str | None = None) -> tuple[str, str] | None:
    if _value(flow, "output_mode") != "direct_replace":
        return None
    if static_flow_filename(_value(flow, "filename_template")) is None:
        return None
    configured = file_flow_target(flow).get("path")
    candidate = str(published_path or configured or "").strip()
    if not candidate or not ntpath.isabs(candidate):
        return None
    normalized = normalize_file_path(candidate)
    return (candidate, normalized) if normalized else None


def reconcile_file_binding(
    db,
    flow_id: int,
    *,
    published_path: str | None = None,
    reconcile_sources: bool = True,
) -> dict:
    flow = db.execute("SELECT * FROM flows WHERE id=?", (flow_id,)).fetchone()
    existing = db.execute(
        "SELECT * FROM flow_file_source_bindings WHERE flow_id=? AND active=1",
        (flow_id,),
    ).fetchone()
    old_source_id = int(existing["source_id"]) if existing else None
    target = _binding_target(db, flow, published_path) if flow else None
    source_id = None
    if target:
        matches = db.execute(
            """SELECT id, connection_info FROM sources
               WHERE COALESCE(archived,0)=0
                 AND lower(COALESCE(type,'')) IN ('csv','excel','file')
               ORDER BY id"""
        ).fetchall()
        exact = [int(row["id"]) for row in matches if normalize_file_path(row["connection_info"]) == target[1]]
        if len(exact) == 1:
            source_id = exact[0]
    if existing and source_id == old_source_id and target and existing["normalized_path"] == target[1]:
        return {"flow_id": flow_id, "source_id": source_id, "status": "confirmed", "changed": False}

    now = iso_utc(utc_now())
    if existing:
        db.execute(
            "UPDATE flow_file_source_bindings SET active=0, updated_at=? WHERE id=?",
            (now, existing["id"]),
        )
    if source_id is not None and target:
        origin = "published" if published_path else "configured"
        db.execute(
            """INSERT INTO flow_file_source_bindings
               (flow_id, source_id, target_path, normalized_path, origin, active,
                confirmed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(flow_id, source_id, normalized_path) DO UPDATE SET
                 target_path=excluded.target_path, origin=excluded.origin, active=1,
                 confirmed_at=excluded.confirmed_at, updated_at=excluded.updated_at""",
            (flow_id, source_id, target[0], target[1], origin, now, now, now),
        )
    if reconcile_sources:
        for affected in {old_source_id, source_id} - {None}:
            reconcile_source(db, int(affected))
    return {
        "flow_id": flow_id, "source_id": source_id,
        "status": "confirmed" if source_id is not None else "unresolved",
        "changed": bool(existing or source_id is not None),
    }


def reconcile_all_file_bindings(db) -> dict:
    counts = {"total": 0, "changed": 0, "confirmed": 0, "unresolved": 0}
    flow_ids = [int(row["id"]) for row in db.execute("SELECT id FROM flows ORDER BY id").fetchall()]
    for flow_id in flow_ids:
        published = db.execute(
            """SELECT published_file_path FROM flow_run_files frf
               JOIN flow_runs fr ON fr.id=frf.run_id
               WHERE fr.flow_id=? AND fr.status='succeeded'
                 AND frf.published_file_path IS NOT NULL
               ORDER BY fr.id DESC, frf.id DESC LIMIT 1""",
            (flow_id,),
        ).fetchone()
        result = reconcile_file_binding(
            db, flow_id,
            published_path=published["published_file_path"] if published else None,
            reconcile_sources=False,
        )
        counts["total"] += 1
        counts["changed"] += int(result["changed"])
        counts[result["status"]] += 1
    return counts


def initialize_freshness_data(db) -> None:
    now = iso_utc(utc_now())
    db.execute(
        """UPDATE flows SET freshness_effective_from_at=?
           WHERE enabled=1 AND schedule_type!='manual'
             AND freshness_effective_from_at IS NULL""",
        (now,),
    )
    marker = db.execute(
        "SELECT value FROM app_settings WHERE key='freshness_inheritance_migration_v1'"
    ).fetchone()
    if not marker:
        sources = db.execute("SELECT * FROM sources ORDER BY id").fetchall()
        for source in sources:
            source_id = int(source["id"])
            latest = db.execute(
                """SELECT action, detail FROM event_log
                   WHERE entity_type='source' AND entity_id=?
                     AND action IN ('freshness_rule_set','freshness_rule_reset','freshness_rule_auto_set')
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (source_id,),
            ).fetchone()
            action = latest["action"] if latest else None
            nonblank = bool(
                str(_value(source, "freshness_rule_type") or "").strip()
                or int(_value(source, "custom_fresh_days") or 0) > 0
                or str(_value(source, "freshness_schedule_days") or "").strip()
            )
            if action == "freshness_rule_auto_set" and nonblank and _legacy_auto_set_matches(source, latest["detail"]):
                mode, origin = "inherit", "source_schedule"
            elif nonblank:
                mode, origin = "manual", "manual"
            elif action == "freshness_rule_reset":
                mode, origin = "disabled", "disabled"
            else:
                mode, origin = "inherit", None
            db.execute(
                """UPDATE sources SET freshness_mode=?, freshness_rule_origin=?,
                   freshness_rule_status=?, freshness_effective_from_at=? WHERE id=?""",
                (
                    mode, origin,
                    "manual" if mode == "manual" else "disabled" if mode == "disabled" else "unmapped",
                    now if mode == "inherit" else None,
                    source_id,
                ),
            )
            refresh = str(_value(source, "refresh_schedule") or "").strip()
            if refresh:
                upsert_schedule_evidence(
                    db, source_id=source_id, origin="legacy_unknown", external_id="legacy",
                    expression=refresh, timezone_name=FLOW_TIMEZONE, active=True,
                    authoritative=False, observed_at=now,
                )
        db.execute(
            "INSERT INTO app_settings(key,value) VALUES ('freshness_inheritance_migration_v1',?)",
            (now,),
        )

    reconcile_all_file_bindings(db)
    reconcile_all_sources(db)
