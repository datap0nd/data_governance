"""Regression tests for the daily-refresh and automatic-update audit findings."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app import database, main, settings
from app.routers import scanner as scanner_router

DUBAI = ZoneInfo("Asia/Dubai")
RETRY_JOB = "daily_overall_refresh_retry"
SIX_HOURS = timedelta(hours=6)


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "governance.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(main, "DB_PATH", db_path)
    database.init_db()
    return db_path


class _Scheduler:
    """Records the jobs main.py asks APScheduler for."""

    def __init__(self):
        self.jobs = {}

    def add_job(self, function, trigger, **kwargs):
        self.jobs[kwargs["id"]] = (function, trigger, kwargs)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        del self.jobs[job_id]


@pytest.fixture
def scheduler(audit_db, monkeypatch):
    fake = _Scheduler()
    monkeypatch.setattr(main, "_scheduler", fake)
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVITY_LOCK", threading.Lock())
    monkeypatch.setattr(main, "_AUTO_UPDATE_DRAIN_EVENT", threading.Event())
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVE_STARTS", 0)
    return fake


def _dubai(hour, minute, *, day=10):
    return datetime(2026, 11, day, hour, minute, tzinfo=DUBAI)


def _full_scan_job(created_at, *, job_type="full_scan"):
    with database.get_db() as db:
        db.execute(
            """INSERT INTO scanner_jobs(job_type, trigger_source, status, created_at)
               VALUES (?, 'scheduled', 'completed', ?)""",
            (job_type, created_at),
        )


def _refresh_due_minutes_ago(minutes):
    """Schedule the daily refresh for a wall-clock minute that already passed."""
    due = (datetime.now(DUBAI) - timedelta(minutes=minutes)).replace(second=0, microsecond=0)
    settings.set_overall_refresh_time(due.hour, due.minute)
    return due


def _busy_lane():
    return {
        "accepted": False,
        "status": "running",
        "job_id": 40,
        "job": {"job_type": "source_probe", "trigger_source": "manual", "context": {}},
    }


# S-02 (1): a late daily run is not dropped after one second.


def test_daily_refresh_job_tolerates_a_six_hour_late_start(audit_db, monkeypatch):
    real = BackgroundScheduler(timezone=main.host_timezone())
    monkeypatch.setattr(main, "_scheduler", real)
    settings.set_overall_refresh_time(7, 0)

    main._configure_overall_refresh_job()

    job = real.get_job("daily_overall_refresh")
    assert job.misfire_grace_time == 6 * 60 * 60
    assert job.misfire_grace_time == main._OVERALL_REFRESH_MISFIRE_GRACE_SECONDS
    assert job.coalesce is True
    assert job.kwargs == {"daily": True}
    assert str(job.trigger.timezone) == "Asia/Dubai"


# S-02 (2): a run missed while the service was down starts once after start-up.


def test_missed_daily_refresh_is_caught_up_shortly_after_start_up(scheduler):
    settings.set_overall_refresh_time(7, 0)
    # 07:30 in Dubai, expressed in UTC: the schedule is Dubai wall-clock time.
    now = datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc)
    _full_scan_job("2026-11-09T03:00:05+00:00")  # yesterday's run
    _full_scan_job("2026-11-10T03:10:00+00:00", job_type="source_freshness")

    run_at = main._schedule_missed_overall_refresh(now)

    assert run_at == now + timedelta(seconds=120)
    function, trigger, job = scheduler.jobs[RETRY_JOB]
    assert function is main._scheduled_overall_refresh
    assert trigger == "date"
    assert job["run_date"] == run_at
    assert datetime.fromisoformat(job["kwargs"]["retry_until"]) == _dubai(13, 0)
    assert job["misfire_grace_time"] == main._OVERALL_REFRESH_MISFIRE_GRACE_SECONDS
    assert list(scheduler.jobs) == [RETRY_JOB]


@pytest.mark.parametrize(
    ("now", "full_scan_at"),
    [
        pytest.param(_dubai(6, 30), None, id="not due yet today"),
        pytest.param(_dubai(13, 30), None, id="grace expired"),
        pytest.param(_dubai(12, 59), None, id="would start after the grace"),
        pytest.param(_dubai(7, 30), "2026-11-10T03:00:02+00:00", id="scheduled run started"),
        pytest.param(_dubai(7, 30), "2026-11-10 03:20:00", id="manual full scan since"),
    ],
)
def test_start_up_does_not_repeat_or_revive_a_daily_refresh(scheduler, now, full_scan_at):
    settings.set_overall_refresh_time(7, 0)
    if full_scan_at:
        _full_scan_job(full_scan_at)

    assert main._schedule_missed_overall_refresh(now) is None
    assert scheduler.jobs == {}


def test_start_up_checks_for_a_missed_refresh_without_risking_the_scheduler(
    audit_db, monkeypatch
):
    real = BackgroundScheduler(timezone=main.host_timezone())
    monkeypatch.setattr(main, "_scheduler", real)
    checks = []

    def broken_check():
        checks.append(True)
        raise RuntimeError("scanner_jobs unavailable")

    monkeypatch.setattr(main, "_schedule_missed_overall_refresh", broken_check)

    refresh_time = main._configure_scheduler_jobs()

    assert checks == [True]
    assert refresh_time == settings.get_overall_refresh_time()
    assert real.get_job("daily_overall_refresh") is not None
    assert real.get_job("flow_dispatch") is not None


# S-02 (3): a refused or failed start is retried until the grace expires.


def test_daily_refresh_refused_by_an_update_drain_is_retried(scheduler, monkeypatch):
    due = _refresh_due_minutes_ago(10)
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    monkeypatch.setattr(
        scanner_router,
        "start_scheduled_full_scan_job",
        lambda: pytest.fail("a scan started while an update was draining"),
    )

    result = main._scheduled_overall_refresh(daily=True)

    assert result["status"] == "update_draining"
    function, trigger, job = scheduler.jobs[RETRY_JOB]
    assert function is main._scheduled_overall_refresh and trigger == "date"
    assert result["retry_scheduled_for"] == job["run_date"].isoformat(timespec="seconds")
    retry_in = job["run_date"] - datetime.now(timezone.utc)
    assert timedelta(minutes=4) < retry_in <= timedelta(minutes=main._OVERALL_REFRESH_RETRY_MINUTES)
    # The window is counted from the scheduled time, not from this attempt.
    assert datetime.fromisoformat(job["kwargs"]["retry_until"]) == due + SIX_HOURS
    assert main._AUTO_UPDATE_ACTIVE_STARTS == 0


def test_daily_refresh_that_raises_is_retried_instead_of_lost(scheduler, monkeypatch):
    _refresh_due_minutes_ago(10)

    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(scanner_router, "start_scheduled_full_scan_job", broken)

    result = main._scheduled_overall_refresh(daily=True)

    assert result["accepted"] is False
    assert result["status"] == "failed"
    assert result["retry_scheduled_for"]
    assert RETRY_JOB in scheduler.jobs
    assert main._AUTO_UPDATE_ACTIVE_STARTS == 0


def test_retry_keeps_the_original_deadline_and_stops_when_it_passes(scheduler, monkeypatch):
    attempts = []
    monkeypatch.setattr(
        scanner_router,
        "start_scheduled_full_scan_job",
        lambda: attempts.append(True) or _busy_lane(),
    )
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=1)).isoformat(timespec="seconds")

    retried = main._scheduled_overall_refresh(retry_until=deadline)

    assert retried["retry_scheduled_for"]
    assert scheduler.jobs[RETRY_JOB][2]["kwargs"] == {"retry_until": deadline}

    # Less than one retry interval left: this is the last attempt.
    scheduler.jobs.clear()
    closing_soon = (now + timedelta(minutes=2)).isoformat(timespec="seconds")
    last = main._scheduled_overall_refresh(retry_until=closing_soon)

    assert "retry_scheduled_for" not in last
    assert scheduler.jobs == {}
    assert len(attempts) == 2

    # A retry that wakes up after the deadline does not start a late refresh.
    expired = main._scheduled_overall_refresh(
        retry_until=(now - timedelta(minutes=1)).isoformat(timespec="seconds")
    )

    assert expired == {"accepted": False, "status": "expired"}
    assert scheduler.jobs == {}
    assert len(attempts) == 2


def test_daily_refresh_stops_retrying_when_its_grace_has_expired(scheduler, monkeypatch):
    _refresh_due_minutes_ago(6 * 60 - 2)
    monkeypatch.setattr(scanner_router, "start_scheduled_full_scan_job", _busy_lane)

    result = main._scheduled_overall_refresh(daily=True)

    assert result["accepted"] is False
    assert "retry_scheduled_for" not in result
    assert scheduler.jobs == {}


def test_accepted_refresh_clears_the_pending_retry(scheduler, monkeypatch):
    accepted = {"accepted": True, "status": "queued", "job_id": 41}
    monkeypatch.setattr(scanner_router, "start_scheduled_full_scan_job", lambda: accepted)
    scheduler.jobs[RETRY_JOB] = (main._scheduled_overall_refresh, "date", {"id": RETRY_JOB})

    assert main._scheduled_overall_refresh(daily=True) == accepted
    assert scheduler.jobs == {}


# S-03: a failed install is not relaunched on every 90-second watcher tick.

DEPLOYED = "a" * 40
TARGET = "b" * 40
OTHER_TARGET = "c" * 40


@pytest.fixture
def updater(audit_db, tmp_path, monkeypatch):
    """An installed (non-Git) copy whose elevated updater task is recorded."""
    install_dir = tmp_path / "installed-app"
    install_dir.mkdir()
    update_root = tmp_path / "updates"
    monkeypatch.setattr(main, "_CODE_DIR", install_dir)
    monkeypatch.setattr(main, "_UPDATE_ROOT", update_root)
    monkeypatch.setattr(main, "_UPDATE_REQUEST_PATH", update_root / "pending_update.json")
    monkeypatch.setattr(main, "_UPDATE_RECEIPT_DIR", update_root / "receipts")
    monkeypatch.setattr(main, "_APP_VERSION", f"20260828-120000-{DEPLOYED[:9]}")
    monkeypatch.setattr(main, "_AUTO_UPDATE_RUN_LOCK", threading.Lock())
    monkeypatch.setattr(main, "_AUTO_UPDATE_STATE_LOCK", threading.Lock())
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVITY_LOCK", threading.Lock())
    monkeypatch.setattr(main, "_AUTO_UPDATE_DRAIN_EVENT", threading.Event())
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVE_STARTS", 0)
    monkeypatch.setattr(
        main,
        "_AUTO_UPDATE_STATE",
        {
            "status": "starting",
            "last_checked_at": None,
            "last_attempt_at": None,
            "last_attempt_commit": None,
            "last_attempt_monotonic": 0.0,
            "last_error": None,
        },
    )
    monkeypatch.setattr(main, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(main, "_registered_auto_update_task_ready", lambda: (True, None))
    target = {"commit": TARGET}
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (target["commit"], None))
    launches = []
    behaviour = {"fail": False}

    def launch():
        launches.append(datetime.now(timezone.utc))
        if behaviour["fail"]:
            raise RuntimeError("Windows could not start the automatic update task")

    monkeypatch.setattr(main, "_launch_registered_auto_update_task", launch)
    settings.set_setting(main._AUTO_UPDATE_SETTING_KEY, "1")
    return {
        "launches": launches,
        "behaviour": behaviour,
        "target": target,
        "receipts": update_root / "receipts",
    }


def _attempts():
    with database.get_db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM app_update_attempts ORDER BY id")]


def _age_last_attempt(minutes):
    with database.get_db() as db:
        row = db.execute(
            "SELECT id, finished_at FROM app_update_attempts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        aged = datetime.fromisoformat(row["finished_at"]) - timedelta(minutes=minutes)
        db.execute(
            "UPDATE app_update_attempts SET finished_at=? WHERE id=?",
            (aged.isoformat(timespec="seconds"), row["id"]),
        )


def test_failed_install_is_not_relaunched_on_every_watcher_tick(updater):
    updater["behaviour"]["fail"] = True

    first = main._scheduled_auto_update()
    second = main._scheduled_auto_update()
    third = main._scheduled_auto_update()

    assert first["status"] == "launch_failed"
    assert len(updater["launches"]) == 1
    [attempt] = _attempts()
    assert (attempt["status"], attempt["stage"]) == ("failed", "launch_failed")
    for waiting in (second, third):
        assert waiting["status"] == "retry_wait"
        assert waiting["update_available"] is True
        assert waiting["last_attempt_commit"] == TARGET
        assert "could not start" in waiting["last_error"]
        assert datetime.fromisoformat(waiting["next_attempt_at"]) == datetime.fromisoformat(
            attempt["finished_at"]
        ) + timedelta(seconds=main._AUTO_UPDATE_RETRY_SECONDS)
        assert waiting["draining"] is False
    assert main._AUTO_UPDATE_RETRY_SECONDS == 30 * 60

    # Once the wait has passed the watcher tries the same commit again.
    _age_last_attempt(31)
    updater["behaviour"]["fail"] = False

    retried = main._scheduled_auto_update()

    assert retried["status"] == "update_launched"
    assert retried["next_attempt_at"] is None
    assert len(updater["launches"]) == 2
    assert [row["status"] for row in _attempts()] == ["failed", "launched"]


def test_rolled_back_install_waits_but_manual_update_launches_at_once(updater):
    import json

    launched = main._scheduled_auto_update()
    assert launched["status"] == "update_launched"
    [attempt] = _attempts()
    (updater["receipts"] / f"{attempt['attempt_id']}.json").write_text(
        json.dumps({
            "attempt_id": attempt["attempt_id"],
            "target_commit": TARGET,
            "status": "rolled_back",
            "message": "Old release restored.",
            "error": "health check failed",
        }),
        encoding="utf-8",
    )

    waiting = main._scheduled_auto_update()

    assert waiting["status"] == "retry_wait"
    assert "health check failed" in waiting["last_error"]
    assert len(updater["launches"]) == 1
    assert [(row["status"], row["stage"]) for row in _attempts()] == [("failed", "rolled_back")]

    manual = main._apply_latest_update()

    assert manual["status"] == "launched"
    assert manual["attempt"]["trigger_source"] == "manual"
    assert len(updater["launches"]) == 2


def test_stale_install_waits_before_the_next_automatic_attempt(updater):
    three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    with database.get_db() as db:
        db.execute(
            """INSERT INTO app_update_attempts
                   (attempt_id, from_commit, target_commit, trigger_source,
                    status, stage, active_slot, created_at, launched_at, updated_at)
               VALUES ('went-stale', ?, ?, 'automatic', 'launched', 'external_task', 1, ?, ?, ?)""",
            (DEPLOYED[:9], TARGET, three_hours_ago, three_hours_ago, three_hours_ago),
        )

    waiting = main._scheduled_auto_update()

    assert [(row["status"], row["stage"]) for row in _attempts()] == [("failed", "timed_out")]
    assert waiting["status"] == "retry_wait"
    assert updater["launches"] == []


def test_new_main_commit_launches_despite_a_recent_failed_install(updater):
    updater["behaviour"]["fail"] = True
    assert main._scheduled_auto_update()["status"] == "launch_failed"
    assert main._scheduled_auto_update()["status"] == "retry_wait"

    updater["behaviour"]["fail"] = False
    updater["target"]["commit"] = OTHER_TARGET

    result = main._scheduled_auto_update()

    assert result["status"] == "update_launched"
    assert result["last_attempt_commit"] == OTHER_TARGET
    assert result["next_attempt_at"] is None
    assert len(updater["launches"]) == 2
    assert [row["target_commit"] for row in _attempts()] == [TARGET, OTHER_TARGET]
