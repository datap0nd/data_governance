"""Schedules, update checks and backups on the Settings page, and who may change them.

Covers the settings ``app/main.py`` reads, the listeners that reschedule a job
the moment its setting is saved, and the older settings routes that are now
limited to the host PC. The scheduler is real but started paused, so jobs can
be scheduled and replaced and never run. GitHub and the updater task are
stand-ins; nothing here installs anything.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import database, freshness, main, pipeline_insights_settings, settings, settings_registry as registry
from app.routers import pipeline_insights, pipelines
from app.routers import scanner as scanner_router
from app.scanner import runner

DUBAI = ZoneInfo("Asia/Dubai")
RETRY_JOB = "daily_overall_refresh_retry"
COLLEAGUE = ("203.0.113.9", 50000)
HINT = "On the host PC, open Metronome as http://localhost:8000."


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = str(tmp_path / "governance.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    monkeypatch.setattr(main, "DB_PATH", path)
    database.init_db()
    monkeypatch.setattr(freshness, "_grace", None)
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVITY_LOCK", threading.Lock())
    monkeypatch.setattr(main, "_AUTO_UPDATE_DRAIN_EVENT", threading.Event())
    monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVE_STARTS", 0)
    return path


@pytest.fixture
def scheduler(app_db, monkeypatch):
    """The real scheduler, paused: jobs are scheduled and replaced but never run."""
    real = BackgroundScheduler(timezone=main.host_timezone())
    real.start(paused=True)
    monkeypatch.setattr(main, "_scheduler", real)
    main._register_settings_listeners()  # what start-up does; safe to repeat
    yield real
    real.shutdown(wait=False)


class _RecordingScheduler:
    """Records what is scheduled, for tests that must show nothing was."""
    running = False

    def __init__(self):
        self.jobs = {}
        self.added = []

    def add_job(self, function, trigger, **kwargs):
        self.added.append(kwargs["id"])
        self.jobs[kwargs["id"]] = (function, trigger, kwargs)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        del self.jobs[job_id]


def _dubai(hour, minute, *, day=10):
    return datetime(2026, 11, day, hour, minute, tzinfo=DUBAI)


def _next_run(job):
    moment = job.next_run_time.astimezone(DUBAI)
    return moment.hour, moment.minute


# --- Backups -----------------------------------------------------------------------

def test_daily_backup_moves_to_the_saved_dubai_time_at_once(scheduler):
    main._configure_scheduler_jobs()

    job = scheduler.get_job("daily_backup")
    assert str(job.trigger) == "cron[hour='6', minute='0']"  # today's 06:00
    assert str(job.trigger.timezone) == "Asia/Dubai"
    assert job.func is main._scheduled_backup and _next_run(job) == (6, 0)

    registry.save({"backups.daily_time": "02:30"})

    job = scheduler.get_job("daily_backup")
    assert str(job.trigger) == "cron[hour='2', minute='30']"
    assert str(job.trigger.timezone) == "Asia/Dubai" and _next_run(job) == (2, 30)

    registry.save({"backups.daily_time": None})
    assert _next_run(scheduler.get_job("daily_backup")) == (6, 0)


def _old_backups(tmp_path, monkeypatch, app_db):
    monkeypatch.setattr(runner, "DB_PATH", app_db)
    folder = tmp_path / "backups"
    folder.mkdir()
    old = [f"governance_2020010{day}_000000.db" for day in (1, 2, 3)]
    for name in [*old, "governance_before_migration.db", "notes.txt"]:
        (folder / name).write_bytes(b"old")
    return folder, old


def _timestamped(folder):
    return sorted(path.name for path in folder.iterdir()
                  if path.name.startswith("governance_2") and path.suffix == ".db")


def test_backups_are_all_kept_until_a_number_is_saved(app_db, tmp_path, monkeypatch):
    folder, old = _old_backups(tmp_path, monkeypatch, app_db)

    runner._backup_db()
    kept = _timestamped(folder)
    assert kept[:3] == old and len(kept) == 4  # today: nothing is ever deleted

    registry.save({"backups.keep": 2})
    main._scheduled_backup()  # the daily job; a scan's own backup calls the same function

    kept = _timestamped(folder)
    assert len(kept) == 2 and kept[0] >= old[-1] and kept[-1] > old[-1]
    # Only Metronome's own timestamped backups are ever deleted.
    assert (folder / "governance_before_migration.db").exists() and (folder / "notes.txt").exists()

    registry.save({"backups.keep": 0})
    for name in old:
        (folder / name).write_bytes(b"old")
    runner._backup_db()
    assert _timestamped(folder)[:3] == old  # back to keeping everything


# --- Updates -----------------------------------------------------------------------

DEPLOYED = "a" * 40
TARGET = "b" * 40


def test_update_check_interval_follows_the_setting_and_is_rescheduled_at_once(scheduler, monkeypatch):
    before = datetime.now(timezone.utc)
    main._configure_scheduler_jobs()

    job = scheduler.get_job("automatic_main_update")
    assert job.trigger.interval == timedelta(seconds=90)  # today's interval
    assert timedelta(seconds=55) < job.next_run_time - before < timedelta(seconds=70)  # first run after a minute
    assert (job.max_instances, job.coalesce) == (1, True)
    assert main._auto_update_payload()["interval_seconds"] == 90

    registry.save({"updates.check_seconds": 300})

    job = scheduler.get_job("automatic_main_update")
    assert job.trigger.interval == timedelta(seconds=300)
    assert (job.func, job.max_instances, job.coalesce) == (main._scheduled_auto_update, 1, True)
    payload = main._auto_update_payload()
    assert (payload["interval_seconds"], payload["interval_minutes"]) == (300, 5)


def test_a_looked_up_version_is_reused_for_the_saved_interval(app_db, monkeypatch):
    fetched = []
    monkeypatch.setattr(main, "_UPDATE_CHECK_LOCK", threading.Lock())
    monkeypatch.setattr(main, "_fetch_latest_commit", lambda: fetched.append(True) or TARGET)

    def looked_up_two_minutes_ago():
        monkeypatch.setattr(
            main, "_UPDATE_CHECK",
            {"checked_at": main.time.time() - 120, "latest_commit": DEPLOYED, "error": None},
        )

    looked_up_two_minutes_ago()
    assert main._latest_commit() == (TARGET, None)  # older than today's 90 seconds: asked again
    assert fetched == [True]

    registry.save({"updates.check_seconds": 300})
    looked_up_two_minutes_ago()

    assert main._latest_commit() == (DEPLOYED, None)  # still current for five minutes
    assert fetched == [True]
    assert main._latest_commit(force=True) == (TARGET, None)  # Check now always asks


@pytest.fixture
def updater(app_db, tmp_path, monkeypatch):
    """An installed copy whose updater task always fails to start."""
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
    monkeypatch.setattr(main, "_AUTO_UPDATE_STATE", {
        "status": "starting", "last_checked_at": None, "last_attempt_at": None,
        "last_attempt_commit": None, "last_attempt_monotonic": 0.0, "last_error": None,
    })
    monkeypatch.setattr(main, "_registered_auto_update_task_ready", lambda: (True, None))
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    launches = []

    def launch():
        launches.append(datetime.now(timezone.utc))
        raise RuntimeError("Windows could not start the automatic update task")

    monkeypatch.setattr(main, "_launch_registered_auto_update_task", launch)
    settings.set_setting(main._AUTO_UPDATE_SETTING_KEY, "1")
    return launches


def _last_attempt():
    with database.get_db() as db:
        return dict(db.execute("SELECT * FROM app_update_attempts ORDER BY id DESC LIMIT 1").fetchone())


def _age_last_attempt(minutes):
    aged = datetime.fromisoformat(_last_attempt()["finished_at"]) - timedelta(minutes=minutes)
    with database.get_db() as db:
        db.execute(
            "UPDATE app_update_attempts SET finished_at=? WHERE id=?",
            (aged.isoformat(timespec="seconds"), _last_attempt()["id"]),
        )


def test_wait_after_a_failed_install_follows_the_setting(updater):
    assert main._scheduled_auto_update()["status"] == "launch_failed"

    # Today's thirty minutes: the same version is not tried again at the next checks.
    _age_last_attempt(29)
    waiting = main._scheduled_auto_update()
    assert waiting["status"] == "retry_wait" and len(updater) == 1
    assert datetime.fromisoformat(waiting["next_attempt_at"]) == datetime.fromisoformat(
        _last_attempt()["finished_at"]) + timedelta(minutes=30)

    registry.save({"updates.retry_minutes": 10})

    assert main._scheduled_auto_update()["status"] == "launch_failed"  # 29 minutes is now long enough
    assert len(updater) == 2
    _age_last_attempt(9)
    waiting = main._scheduled_auto_update()
    assert waiting["status"] == "retry_wait" and len(updater) == 2
    assert datetime.fromisoformat(waiting["next_attempt_at"]) == datetime.fromisoformat(
        _last_attempt()["finished_at"]) + timedelta(minutes=10)
    _age_last_attempt(2)
    assert main._scheduled_auto_update()["status"] == "launch_failed" and len(updater) == 3


# --- Daily refresh -----------------------------------------------------------------

def test_how_late_the_daily_refresh_may_start_follows_the_setting(scheduler):
    settings.set_overall_refresh_time(7, 0)
    main._configure_overall_refresh_job()
    assert scheduler.get_job("daily_overall_refresh").misfire_grace_time == 6 * 3600  # today's six hours

    # Start-up at 10:30 Dubai time, three and a half hours after the refresh was due.
    late = _dubai(10, 30)
    assert main._schedule_missed_overall_refresh(late) == late + timedelta(seconds=120)
    catch_up = scheduler.get_job(RETRY_JOB)
    assert datetime.fromisoformat(catch_up.kwargs["retry_until"]) == _dubai(13, 0)
    assert catch_up.misfire_grace_time == 6 * 3600
    scheduler.remove_job(RETRY_JOB)

    registry.save({"scan.late_start_hours": 2})

    # The saved value reaches the scheduled job at once, and later decisions too.
    assert scheduler.get_job("daily_overall_refresh").misfire_grace_time == 2 * 3600
    assert main._schedule_missed_overall_refresh(late) is None and scheduler.get_job(RETRY_JOB) is None
    early = _dubai(8, 0)
    assert main._schedule_missed_overall_refresh(early) == early + timedelta(seconds=120)
    catch_up = scheduler.get_job(RETRY_JOB)
    assert datetime.fromisoformat(catch_up.kwargs["retry_until"]) == _dubai(9, 0)
    assert catch_up.misfire_grace_time == 2 * 3600


def _refused_refresh(monkeypatch, *, due_minutes_ago):
    """Run the daily refresh while an update is draining; returns ``(result, retry job)``."""
    due = (datetime.now(DUBAI) - timedelta(minutes=due_minutes_ago)).replace(second=0, microsecond=0)
    settings.set_overall_refresh_time(due.hour, due.minute)
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    monkeypatch.setattr(
        scanner_router, "start_scheduled_full_scan_job",
        lambda: pytest.fail("a scan started while an update was draining"),
    )
    if main._scheduler.get_job(RETRY_JOB) is not None:
        main._scheduler.remove_job(RETRY_JOB)
    result = main._scheduled_overall_refresh(daily=True)
    assert result["status"] == "update_draining"
    return result, main._scheduler.get_job(RETRY_JOB), due


def test_a_refused_daily_refresh_retries_at_the_saved_interval(scheduler, monkeypatch):
    result, retry, due = _refused_refresh(monkeypatch, due_minutes_ago=10)
    wait = retry.trigger.run_date - datetime.now(timezone.utc)
    assert timedelta(minutes=4) < wait <= timedelta(minutes=5)  # today's five minutes
    assert datetime.fromisoformat(retry.kwargs["retry_until"]) == due + timedelta(hours=6)

    registry.save({"scan.retry_minutes": 20, "scan.late_start_hours": 1})
    result, retry, due = _refused_refresh(monkeypatch, due_minutes_ago=10)

    wait = retry.trigger.run_date - datetime.now(timezone.utc)
    assert timedelta(minutes=19) < wait <= timedelta(minutes=20)
    assert result["retry_scheduled_for"] == retry.trigger.run_date.isoformat(timespec="seconds")
    assert datetime.fromisoformat(retry.kwargs["retry_until"]) == due + timedelta(hours=1)

    # A retry that would fall after the late-start time is not scheduled at all.
    result, retry, _due = _refused_refresh(monkeypatch, due_minutes_ago=50)
    assert retry is None and "retry_scheduled_for" not in result


# --- A save on the Settings page reschedules at once ---------------------------------

def test_saving_the_daily_refresh_time_reschedules_the_job(scheduler):
    settings.set_overall_refresh_time(7, 0)
    main._configure_scheduler_jobs()
    assert _next_run(scheduler.get_job("daily_overall_refresh")) == (7, 0)

    changed = registry.save({"scan.daily_time": "06:40"})

    assert [(item["key"], item["old"], item["new"]) for item in changed] == [("scan.daily_time", "07:00", "06:40")]
    job = scheduler.get_job("daily_overall_refresh")
    assert str(job.trigger) == "cron[hour='6', minute='40']" and _next_run(job) == (6, 40)
    assert str(job.trigger.timezone) == "Asia/Dubai"
    assert job.kwargs == {"daily": True} and job.misfire_grace_time == 6 * 3600


def test_saving_the_weekly_insights_settings_reschedules_or_removes_the_job(scheduler):
    main._configure_scheduler_jobs()
    assert str(scheduler.get_job("weekly_pipeline_insights").trigger) == (
        "cron[day_of_week='sun', hour='10', minute='0']"
    )

    registry.save({"insights.weekday": "friday", "insights.time": "09:30"})

    job = scheduler.get_job("weekly_pipeline_insights")
    assert str(job.trigger) == "cron[day_of_week='fri', hour='9', minute='30']"
    assert str(job.trigger.timezone) == "Asia/Dubai"
    assert job.next_run_time.astimezone(DUBAI).strftime("%a %H:%M") == "Fri 09:30"

    # With both parts switched off there is nothing to schedule; one part brings it back.
    registry.save({"insights.samples": False, "insights.explanations": False})
    assert scheduler.get_job("weekly_pipeline_insights") is None
    registry.save({"insights.explanations": True})
    assert str(scheduler.get_job("weekly_pipeline_insights").trigger) == (
        "cron[day_of_week='fri', hour='9', minute='30']"
    )


def test_listeners_are_registered_once_however_often_start_up_runs(app_db, monkeypatch):
    recording = _RecordingScheduler()
    monkeypatch.setattr(main, "_scheduler", recording)
    for _ in range(3):
        main._register_settings_listeners()

    registry.save({"scan.daily_time": "05:15"})
    registry.save({"backups.daily_time": "01:45", "updates.check_seconds": 120})

    assert recording.added == ["daily_overall_refresh", "daily_backup", "automatic_main_update"]
    assert recording.jobs["daily_backup"][2]["hour"] == 1 and recording.jobs["daily_backup"][2]["minute"] == 45
    assert recording.jobs["automatic_main_update"][2]["seconds"] == 120


def test_a_job_that_cannot_be_rescheduled_never_fails_the_save(app_db, monkeypatch, caplog):
    class Broken:
        running = False

        def add_job(self, *_args, **_kwargs):
            raise RuntimeError("scheduler is shutting down")

        def get_job(self, _job_id):
            raise RuntimeError("scheduler is shutting down")

    monkeypatch.setattr(main, "_scheduler", Broken())
    main._register_settings_listeners()

    with caplog.at_level(logging.ERROR, logger="scheduler"):
        changed = registry.save({
            "scan.daily_time": "05:05", "scan.late_start_hours": 3, "insights.time": "11:15",
            "updates.check_seconds": 120, "backups.daily_time": "01:00",
        })

    assert sorted(item["key"] for item in changed) == [
        "backups.daily_time", "insights.time", "scan.daily_time", "scan.late_start_hours", "updates.check_seconds",
    ]
    assert settings.get_overall_refresh_time()["time"] == "05:05"
    assert registry.get("backups.daily_time") == "01:00" and registry.get("updates.check_seconds") == 120
    assert pipeline_insights_settings.get_pipeline_insights_settings().time == "11:15"
    for what in ("daily refresh", "weekly pipeline insights", "update check", "daily backup"):
        assert f"The {what} setting was saved but its job could not be rescheduled" in caplog.text


# --- Older settings routes: the host PC only -----------------------------------------

INSIGHTS = {
    "samples_scheduled": True, "explanations_scheduled": False, "weekday": "friday", "time": "09:30",
    "exclusions": [],
}
FULL_REFRESH = {"enabled": True, "report_allowlist": [7, "Orders"]}


def _app_wide_settings():
    with database.get_db() as db:
        events = db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
    return {
        "refresh": settings.get_overall_refresh_time()["time"],
        "insights": pipeline_insights_settings.get_pipeline_insights_settings().public_dict(),
        "full_refresh": pipelines.pipeline_settings(),
        "events": events,
    }


def test_older_settings_routes_refuse_another_computer_and_change_nothing(app_db, monkeypatch):
    recording = _RecordingScheduler()
    monkeypatch.setattr(main, "_scheduler", recording)
    settings.set_overall_refresh_time(7, 0)
    before = _app_wide_settings()
    colleague = TestClient(main.app, client=COLLEAGUE)

    for route, body, message in (
        ("/api/system/refresh-schedule", {"refresh_time": "05:05"},
         "Only the host PC can change the daily refresh time."),
        ("/api/admin/refresh-schedule", {"refresh_time": "05:05"},
         "Only the host PC can change the daily refresh time."),
        ("/api/pipeline-insights/settings", INSIGHTS,
         "Only the host PC can change the pipeline insights settings."),
        ("/api/pipelines/settings", FULL_REFRESH,
         "Only the host PC can change the full pipeline refresh settings."),
    ):
        response = colleague.put(route, json=body)
        assert response.status_code == 403, (route, response.text)
        assert response.json() == {"detail": f"{message} {HINT}"}

    assert _app_wide_settings() == before
    assert recording.added == []  # no job was rescheduled either
    # Everyone may still read what these routes change.
    assert colleague.get("/api/system/refresh-schedule").json()["refresh_time"] == "07:00"
    assert colleague.get("/api/pipeline-insights/settings").json()["weekday"] == before["insights"]["weekday"]
    assert colleague.get("/api/pipelines/settings").json() == before["full_refresh"]


def test_older_settings_routes_still_work_from_the_host_pc(app_db, monkeypatch):
    recording = _RecordingScheduler()
    monkeypatch.setattr(main, "_scheduler", recording)
    host = TestClient(main.app)

    refresh = host.put("/api/system/refresh-schedule", json={"refresh_time": "05:05"})
    insights = host.put("/api/pipeline-insights/settings", json=INSIGHTS)
    full_refresh = host.put("/api/pipelines/settings", json=FULL_REFRESH)

    assert (refresh.status_code, insights.status_code, full_refresh.status_code) == (200, 200, 200)
    assert refresh.json()["refresh_time"] == "05:05" and "reschedule_error" not in refresh.json()
    assert (insights.json()["weekday"], insights.json()["time"]) == ("friday", "09:30")
    assert full_refresh.json() == FULL_REFRESH
    saved = _app_wide_settings()
    assert saved["refresh"] == "05:05" and saved["full_refresh"] == FULL_REFRESH
    assert saved["insights"]["explanations_scheduled"] is False
    # And each route still reschedules its own job, as before.
    assert recording.added == ["daily_overall_refresh", "weekly_pipeline_insights"]
    assert (recording.jobs["daily_overall_refresh"][2]["hour"], recording.jobs["daily_overall_refresh"][2]["minute"]) == (5, 5)
    assert recording.jobs["weekly_pipeline_insights"][2]["day_of_week"] == "fri"


def test_the_same_rule_holds_when_the_routes_are_called_directly(app_db, monkeypatch):
    monkeypatch.setattr(main, "_scheduler", _RecordingScheduler())
    colleague = SimpleNamespace(state=SimpleNamespace(actor="Maya", person_id=4, is_host=False))
    host = SimpleNamespace(state=SimpleNamespace(actor="Host PC", person_id=None, is_host=True))
    before = _app_wide_settings()
    calls = (
        (main.update_refresh_schedule, main.RefreshScheduleRequest(refresh_time="05:05")),
        (pipeline_insights.put_settings, pipeline_insights.PipelineInsightsSettingsRequest(**INSIGHTS)),
        (pipelines.update_pipeline_settings, pipelines.PipelineSettingsWrite(**FULL_REFRESH)),
    )

    for route, body in calls:
        with pytest.raises(HTTPException) as refused:
            route(body, colleague)
        assert refused.value.status_code == 403 and refused.value.detail.endswith(HINT)
    assert _app_wide_settings() == before

    for route, body in calls:
        route(body, host)
    assert _app_wide_settings()["refresh"] == "05:05"
    assert pipelines.pipeline_settings() == FULL_REFRESH
