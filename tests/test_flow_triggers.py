"""Several schedule triggers on one Flow.

The main schedule columns stay trigger 1; additional triggers add run times.
The scheduler queues at the earliest trigger, the Flow's own health checks the
latest due trigger, and inherited Source freshness keeps following trigger 1.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import database
from app.freshness import (
    additional_trigger_rules,
    evaluate_timed_rules,
    next_trigger_occurrence,
    schedule_rule,
    trigger_payload,
)
from app.freshness_inheritance import reconcile_source
from app.routers import flows
from test_flows import _flow, _mark_discovered, _request, _seed_catalog, flow_db  # noqa: F401

UTC = timezone.utc
# Monday 12 October 2026, 05:30 UTC = 09:30 in Dubai (UTC+4, no DST).
MONDAY_0930_DUBAI = datetime(2026, 10, 12, 5, 30)
DAILY_0800 = {"schedule_type": "daily", "schedule_time": "08:00", "schedule_days": []}


def _daily(time):
    return {"type": "daily", "time": time, "days": [], "day": None}


@pytest.fixture()
def clock(monkeypatch):
    monkeypatch.setattr(flows, "_now", lambda: MONDAY_0930_DUBAI)
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "launched", "mode": mode})


def _create(**overrides):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"], **{**DAILY_0800, **overrides}), _request())
    return site, report, saved


def _stored(flow_id, column):
    with database.get_db() as db:
        return db.execute(f"SELECT {column} FROM flows WHERE id=?", (flow_id,)).fetchone()[0]


def test_trigger_rules_validate_and_number_from_two():
    assert additional_trigger_rules(None) == []
    with pytest.raises(ValueError, match="Trigger 2 must be daily, weekly, or monthly."):
        additional_trigger_rules([{"type": "manual", "time": "08:00"}])
    with pytest.raises(ValueError, match="Trigger 3: Weekly rules need at least one weekday."):
        additional_trigger_rules([_daily("08:00"), {"type": "weekly", "time": "09:00", "days": []}])
    with pytest.raises(ValueError, match="Trigger 2: Scheduled rules need a valid HH:MM time."):
        additional_trigger_rules([{"type": "daily", "time": "25:00"}])
    with pytest.raises(ValueError, match="at most 24 triggers"):
        additional_trigger_rules([_daily(f"{hour:02d}:00") for hour in range(24)])
    assert len(additional_trigger_rules([_daily(f"{hour:02d}:00") for hour in range(23)])) == 23
    # Fields a schedule does not use are dropped rather than stored.
    rule = additional_trigger_rules([{"type": "daily", "time": "09:00", "days": ["monday"], "day": 3}])[0]
    assert trigger_payload(rule) == _daily("09:00")
    weekly = additional_trigger_rules([{"type": "weekly", "time": "09:00", "days": ["Friday", "monday"], "day": 3}])[0]
    assert trigger_payload(weekly) == {"type": "weekly", "time": "09:00", "days": ["monday", "friday"], "day": None}


def test_next_run_is_the_earliest_trigger():
    rules = [
        schedule_rule("daily", "08:00", timezone_name="UTC"),
        *additional_trigger_rules(
            [_daily("12:00"), {"type": "weekly", "time": "10:00", "days": ["tuesday"]}],
            timezone_name="UTC",
        ),
    ]
    monday = datetime(2026, 10, 12, 9, tzinfo=UTC)
    assert next_trigger_occurrence(rules, after=monday) == datetime(2026, 10, 12, 12, tzinfo=UTC)
    assert next_trigger_occurrence(rules, after=monday.replace(hour=13)) == datetime(2026, 10, 13, 8, tzinfo=UTC)
    assert next_trigger_occurrence(rules, after=datetime(2026, 10, 13, 9, tzinfo=UTC)) == datetime(2026, 10, 13, 10, tzinfo=UTC)
    assert next_trigger_occurrence([], after=monday) is None


def test_flow_health_checks_the_latest_due_trigger():
    morning = schedule_rule("daily", "06:00", timezone_name="UTC")
    evening = additional_trigger_rules([_daily("18:00")], timezone_name="UTC")[0]
    baseline = datetime(2026, 8, 1, tzinfo=UTC)
    as_of = datetime(2026, 9, 2, 18, 30, tzinfo=UTC)
    after_morning_run = datetime(2026, 9, 1, 7, tzinfo=UTC)
    # Only the morning trigger: a success after 1 Sep 06:00 is enough.
    assert evaluate_timed_rules([morning], evidence_at=after_morning_run, baseline_at=baseline, as_of=as_of)["status"] == "healthy"
    # With the evening trigger, 1 Sep 18:00 is the latest occurrence past its grace.
    result = evaluate_timed_rules([morning, evening], evidence_at=after_morning_run, baseline_at=baseline, as_of=as_of)
    assert result["status"] == "overdue"
    assert result["latest_due_at"] == "2026-09-01T18:00:00Z"
    assert evaluate_timed_rules(
        [morning, evening], evidence_at=datetime(2026, 9, 1, 19, tzinfo=UTC), baseline_at=baseline, as_of=as_of,
    )["status"] == "healthy"
    # Occurrences before the baseline are never enforced.
    assert evaluate_timed_rules(
        [morning, evening], evidence_at=None, baseline_at=datetime(2026, 9, 2, 12, tzinfo=UTC), as_of=as_of,
    )["status"] == "pending"


def test_create_saves_triggers_and_queues_the_earliest(flow_db, clock):
    _, _, saved = _create(additional_triggers=[
        _daily("11:00"),
        {"type": "weekly", "time": "10:00", "days": ["Monday"]},
        {"type": "monthly", "time": "07:00", "day": 31, "days": ["sunday"]},
    ])
    assert saved["additional_triggers"] == [
        _daily("11:00"),
        {"type": "weekly", "time": "10:00", "days": ["monday"], "day": None},
        {"type": "monthly", "time": "07:00", "days": [], "day": 31},
    ]
    # 09:30 Dubai: trigger 3 (Mondays 10:00 Dubai) is next, before 11:00 and tomorrow's 08:00.
    assert saved["next_run_at"] == "2026-10-12T06:00:00"
    assert saved["freshness_rule"] == schedule_rule("daily", "08:00")


def test_invalid_triggers_name_the_trigger(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    for triggers, message in [
        ([_daily("08:00")], "Trigger 2 repeats trigger 1."),
        ([_daily("10:00"), _daily("12:00"), _daily("10:00")], "Trigger 4 repeats trigger 2."),
        ([{"type": "weekly", "time": "10:00", "days": []}], "Trigger 2: Weekly rules need at least one weekday."),
    ]:
        with pytest.raises(ValidationError, match=message) as raised:
            _flow(site["id"], report["id"], **DAILY_0800, additional_triggers=triggers)
        assert raised.value.errors()[0]["loc"] == ("additional_triggers",)
    with pytest.raises(ValidationError, match="Choose a daily, weekly, or monthly schedule before adding more triggers."):
        _flow(site["id"], report["id"], enabled=False, schedule_type="manual", schedule_time=None,
              schedule_days=[], additional_triggers=[_daily("10:00")])
    # A weekly main schedule with the same weekdays in another order still repeats.
    with pytest.raises(ValidationError, match="Trigger 2 repeats trigger 1."):
        _flow(site["id"], report["id"], schedule_type="weekly", schedule_time="08:00",
              schedule_days=["friday", "Monday"],
              additional_triggers=[{"type": "weekly", "time": "08:00", "days": ["monday", "friday"]}])


def test_update_keeps_omitted_triggers_drops_redundant_and_manual_clears(flow_db, clock):
    site, report, saved = _create(additional_triggers=[_daily("11:00"), _daily("15:00")])
    flow_id = saved["id"]
    # An older client omits the field: the saved triggers stay.
    kept = flows.update_flow(flow_id, _flow(site["id"], report["id"], **DAILY_0800), _request())
    assert kept["additional_triggers"] == [_daily("11:00"), _daily("15:00")]
    # Moving trigger 1 onto a saved trigger drops the redundant copy.
    moved = flows.update_flow(flow_id, _flow(site["id"], report["id"], **{**DAILY_0800, "schedule_time": "11:00"}), _request())
    assert moved["additional_triggers"] == [_daily("15:00")]
    # An explicit list replaces the saved triggers.
    replaced = flows.update_flow(flow_id, _flow(site["id"], report["id"], **DAILY_0800, additional_triggers=[_daily("10:00")]), _request())
    assert replaced["additional_triggers"] == [_daily("10:00")]
    assert replaced["next_run_at"] == "2026-10-12T06:00:00"
    # Manual drops every trigger, including from an older client.
    manual = flows.update_flow(
        flow_id,
        _flow(site["id"], report["id"], enabled=False, schedule_type="manual", schedule_time=None, schedule_days=[]),
        _request(),
    )
    assert manual["additional_triggers"] == []
    assert _stored(flow_id, "additional_triggers_json") == "[]"


def test_changing_triggers_restarts_flow_health_baseline(flow_db, clock):
    site, report, saved = _create(additional_triggers=[_daily("11:00")])
    flow_id = saved["id"]
    with database.get_db() as db:
        db.execute("UPDATE flows SET freshness_effective_from_at='2026-01-01T00:00:00Z' WHERE id=?", (flow_id,))
    flows.update_flow(flow_id, _flow(site["id"], report["id"], **DAILY_0800, additional_triggers=[_daily("11:00")]), _request())
    assert _stored(flow_id, "freshness_effective_from_at") == "2026-01-01T00:00:00Z"
    flows.update_flow(flow_id, _flow(site["id"], report["id"], **DAILY_0800, additional_triggers=[_daily("11:00"), _daily("17:00")]), _request())
    assert _stored(flow_id, "freshness_effective_from_at") != "2026-01-01T00:00:00Z"


def test_activation_and_scheduler_use_every_trigger(flow_db, clock, monkeypatch):
    _, _, saved = _create(enabled=False, additional_triggers=[_daily("11:00"), _daily("15:00")])
    flow_id = saved["id"]
    assert saved["next_run_at"] is None
    activated = flows.set_flow_enabled(flow_id, flows.FlowEnabledWrite(enabled=True), _request())
    assert activated["next_run_at"] == "2026-10-12T07:00:00"  # 11:00 Dubai today
    # At 11:00 Dubai the scheduler queues one run and moves on to 15:00 the same day.
    monkeypatch.setattr(flows, "_now", lambda: datetime(2026, 10, 12, 7, 0))
    assert flows.queue_due_flows()["count"] == 1
    assert _stored(flow_id, "next_run_at") == "2026-10-12T11:00:00"
    # A due trigger while a run is still active advances without queuing another.
    monkeypatch.setattr(flows, "_now", lambda: datetime(2026, 10, 12, 11, 0))
    assert flows.queue_due_flows()["count"] == 0
    assert _stored(flow_id, "next_run_at") == "2026-10-13T04:00:00"  # tomorrow 08:00 Dubai
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM flow_runs WHERE flow_id=? AND trigger_type='scheduled'", (flow_id,)).fetchone()[0] == 1


def test_source_freshness_keeps_following_trigger_one(flow_db, clock):
    _, _, saved = _create(additional_triggers=[_daily("11:00"), _daily("15:00")])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO sources(id,name,type,discovered_by,freshness_mode)
               VALUES (900,'Feedback table','postgresql','manual','inherit')"""
        )
        db.execute("UPDATE flows SET sql_handoff_enabled=1, sql_target_source_id=900 WHERE id=?", (saved["id"],))
        assert reconcile_source(db, 900)["status"] == "mapped"
        source = db.execute("SELECT * FROM sources WHERE id=900").fetchone()
    assert (source["freshness_rule_type"], source["freshness_schedule_time"]) == ("daily", "08:00")


def test_handover_snapshot_carries_the_triggers(flow_db, clock):
    from app import flow_handover

    _, _, saved = _create(additional_triggers=[_daily("11:00")])
    with database.get_db() as db:
        snapshot = flow_handover.snapshot(db, saved["id"])
    assert snapshot["schedule"]["additional_triggers"] == [_daily("11:00")]
