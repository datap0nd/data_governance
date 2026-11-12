import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from app.freshness import host_timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
from pydantic import BaseModel, Field
from urllib.parse import urlencode

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.config import DB_PATH, UPLOAD_PGHOST, UPLOAD_PGPORT
from app.database import get_db, init_db
from app import remote_flow_control
from app.routers.system_paths import router as system_paths_router
from app.routers.system_flows import router as system_flows_router
from app.routers.system_logs import router as system_logs_router
from app.routers.system_settings import router as system_settings_router
from app import log_housekeeping, logging_setup
from app.routers.flow_tasks import router as flow_tasks_router
from app.local_access import is_host_request, is_server_machine, require_app_access, require_host
from app import user_identity
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, data_quality, tasks, eventlog, people, archive, documentation, email, email_schedules, usage, materialized_views, recurrences, flows, query_history, pipelines, pipeline_insights
from app.settings import (
    get_overall_refresh_time,
    get_setting,
    set_overall_refresh_time,
    set_setting,
)
from app.source_identity import postgres_server_identity, reconcile_all_flow_targets
from app.scanner.lifecycle import (
    recover_interrupted_scan_runs,
)
from app.scanner import limits as operator_limits
from app.scanner.pbi_auth import resolve_proxy
from app.ai.router import router as ai_router
from app.auditor.router import router as auditor_router
from app.auditor import engine as auditor_engine, store as auditor_store

# Show scanner logs in the console, without the request and scheduler chatter
# that used to fill the service logs (see app/logging_setup.py).
logging_setup.configure()

def _is_localhost(ip: str) -> bool:
    """Check if a request came from the machine running the app."""
    return is_server_machine(ip)


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Resolve the browser's chosen person and host-PC status on every request."""
    async def dispatch(self, request: StarletteRequest, call_next):
        ip = request.client.host if request.client else "unknown"
        client_key = user_identity.clean_client_key(
            request.headers.get("x-client-key") or request.cookies.get("dg_client_key")
        )
        identity = user_identity.resolve(ip, client_key)
        request.state.client_ip = ip
        request.state.client_key = client_key
        request.state.is_local = _is_localhost(ip)
        request.state.is_host = is_host_request(request)
        request.state.is_admin = True
        request.state.identity = identity
        request.state.person_id = identity["person_id"] if identity else None
        request.state.actor = (
            identity["name"] if identity
            else user_identity.HOST_ACTOR if request.state.is_host else None
        )
        response = await call_next(request)
        return response


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Prevent browser from caching static JS/CSS files."""
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


_DRAINED_WORK_START_PATHS = (
    re.compile(
        r"^/api/scanner/(?:run|probe|jobs/[^/]+|pbi-sync|"
        r"pg-deps|pg-cron|pbi-usage-sync)$"
    ),
    re.compile(r"^/api/flows/\d+/run$"),
    re.compile(r"^/api/flows/runs/\d+/(?:retry-sql|retry-views|resume)$"),
    re.compile(r"^/api/flows/(?:sites|reports)/\d+/scan$"),
    re.compile(r"^/api/flows/sql/catalog/refresh$"),
    re.compile(r"^/api/pipelines/reports/\d+/runs$"),
    re.compile(r"^/api/pipelines/runs/\d+/resend-summary$"),
    re.compile(r"^/api/reports/\d+/refresh$"),
    re.compile(r"^/api/materialized-views/\d+/refresh$"),
    re.compile(r"^/api/data-quality/(?:run|checks/\d+/run)$"),
    re.compile(r"^/api/recurrences/(?:\d+/run|visuals/discover|preview)$"),
    re.compile(r"^/api/ai/(?:chat|settings/test|operations/runs)$"),
    re.compile(r"^/api/documentation/(?:ai-suggest/\d+|ai-suggest-all)$"),
    re.compile(r"^/api/email/(?:send-task-summaries|send-alert-summaries)$"),
    re.compile(r"^/api/email-schedules/task-summary/send-now$"),
    re.compile(r"^/api/usage/sync$"),
    re.compile(r"^/api/system/refresh-now$"),
)


class UpdateDrainMiddleware(BaseHTTPMiddleware):
    """Reject only *new* production work during the short update drain.

    Worker progress, completion, cancellation, reads, and settings remain
    available so already-running work can finish normally.
    """

    async def dispatch(self, request: StarletteRequest, call_next):
        starts_work = request.method.upper() == "POST" and any(
            pattern.fullmatch(request.url.path)
            for pattern in _DRAINED_WORK_START_PATHS
        )
        if not starts_work:
            return await call_next(request)
        if not _try_begin_update_sensitive_work():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Metronome is finishing active work for an automatic main "
                        "update. Start this operation again after the app restarts."
                    )
                },
                headers={"Retry-After": "60"},
            )
        try:
            return await call_next(request)
        finally:
            _finish_update_sensitive_work()


_scheduler = BackgroundScheduler(timezone=host_timezone())
_OVERALL_REFRESH_RETRY_JOB_ID = "daily_overall_refresh_retry"
_PIPELINE_INSIGHTS_RETRY_JOB_ID = "weekly_pipeline_insights_retry"
_OVERALL_REFRESH_RETRY_MINUTES = 5
#: How late the daily overall refresh may still start: after a restart, PC
#: sleep, a busy scanner or an update drain. One constant, so it can become an
#: operator setting. APScheduler's own default drops a run that is 1s late.
_OVERALL_REFRESH_MISFIRE_GRACE_SECONDS = 6 * 60 * 60
#: Start-up settles (worker launch, update receipt) before a missed run starts.
_OVERALL_REFRESH_CATCH_UP_DELAY_SECONDS = 120
#: When the daily database backup runs: Dubai time, like every schedule here.
_DAILY_BACKUP_TIME = "06:00"
_SETTINGS_LISTENERS_REGISTERED = False


def _overall_refresh_grace_seconds() -> int:
    """How late the daily refresh may still start (Settings), in seconds."""
    hours = operator_limits.value(
        "scan.late_start_hours", _OVERALL_REFRESH_MISFIRE_GRACE_SECONDS / 3600
    )
    return max(60, int(float(hours) * 3600))


def _overall_refresh_retry_minutes() -> float:
    """How long after a refused start the daily refresh tries again (Settings)."""
    return max(
        1.0, float(operator_limits.value("scan.retry_minutes", _OVERALL_REFRESH_RETRY_MINUTES))
    )


def _daily_backup_time() -> tuple[int, int]:
    """The hour and minute of the daily database backup (Settings), Dubai time."""
    value = str(operator_limits.value("backups.daily_time", _DAILY_BACKUP_TIME))
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(value)
    except ValueError:
        hour, minute = (int(part) for part in _DAILY_BACKUP_TIME.split(":"))
    return hour, minute


def _scheduled_work_is_draining() -> bool:
    event = globals().get("_AUTO_UPDATE_DRAIN_EVENT")
    return bool(event and event.is_set())


def _try_begin_update_sensitive_work() -> bool:
    """Atomically reject new work after drain starts, otherwise track it."""
    global _AUTO_UPDATE_ACTIVE_STARTS
    with _AUTO_UPDATE_ACTIVITY_LOCK:
        if _AUTO_UPDATE_DRAIN_EVENT.is_set():
            return False
        _AUTO_UPDATE_ACTIVE_STARTS += 1
        return True


def _finish_update_sensitive_work() -> None:
    global _AUTO_UPDATE_ACTIVE_STARTS
    with _AUTO_UPDATE_ACTIVITY_LOCK:
        _AUTO_UPDATE_ACTIVE_STARTS = max(0, _AUTO_UPDATE_ACTIVE_STARTS - 1)


def _request_update_drain() -> None:
    with _AUTO_UPDATE_ACTIVITY_LOCK:
        _AUTO_UPDATE_DRAIN_EVENT.set()


def _release_update_drain() -> None:
    with _AUTO_UPDATE_ACTIVITY_LOCK:
        _AUTO_UPDATE_DRAIN_EVENT.clear()


def _tracked_scheduled_start(work_name: str):
    """Track scheduler jobs that can create work absent from durable queues."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            if not _try_begin_update_sensitive_work():
                logging.getLogger("scheduler").debug(
                    "Skipping %s while an exact-main update waits for idle",
                    work_name,
                )
                return {"status": "update_draining"}
            try:
                return function(*args, **kwargs)
            finally:
                _finish_update_sensitive_work()
        return run
    return decorate


@_tracked_scheduled_start("daily backup")
def _scheduled_backup():
    """Daily 6 AM backup of governance.db."""
    from app.scanner.runner import _backup_db
    log = logging.getLogger("scheduler")
    log.info("Running scheduled backup")
    _backup_db()
    log.info("Scheduled backup complete")


@_tracked_scheduled_start("scheduled scan")
def _scheduled_scan(cancel_generation: int | None = None, stop_existing: bool = True):
    """Submit a report scan through the durable global scanner lane."""
    from app.routers.scanner import start_scheduled_scan_job

    return start_scheduled_scan_job(
        cancel_generation=cancel_generation,
        stop_existing=stop_existing,
    )


@_tracked_scheduled_start("Power BI sync")
def _scheduled_pbi_sync(
    cancel_generation: int | None = None,
    *,
    stop_existing: bool = True,
    wait: bool = False,
):
    """Daily Power BI Service refresh metadata sync."""
    from app.scanner.pbi_sync import trigger_pbi_sync_and_wait, trigger_pbi_sync_or_defer
    log = logging.getLogger("scheduler")
    log.info("Running scheduled PBI sync")
    try:
        if wait:
            result = trigger_pbi_sync_and_wait(
                "scheduled_overall_refresh",
                cancel_existing=stop_existing,
                cancel_generation=cancel_generation,
            )
        else:
            result = trigger_pbi_sync_or_defer(
                "scheduled_overall_refresh",
                cancel_existing=stop_existing,
                cancel_generation=cancel_generation,
            )
        log.info("PBI sync result: %s", result.get("status"))
        return result
    except Exception as e:
        log.exception("Scheduled PBI sync failed: %s", e)
        return {"status": "failed", "error": str(e)}


@_tracked_scheduled_start("pending Power BI sync retry")
def _scheduled_pending_pbi_sync_retry():
    """Retry scheduled PBI sync after RDP/lock-screen conditions clear."""
    from app.scanner.pbi_sync import retry_pending_pbi_sync
    log = logging.getLogger("scheduler")
    try:
        result = retry_pending_pbi_sync()
        if result.get("status") not in {"idle", "waiting"}:
            log.info("Pending PBI sync retry result: %s", result.get("status"))
    except Exception as e:
        log.exception("Pending PBI sync retry failed: %s", e)


def _latest_overall_refresh_due(now: datetime) -> datetime:
    """Return the most recent daily refresh time at or before *now*.

    The refresh time is a wall-clock value in the schedule (Dubai) timezone,
    the same one the scheduler's cron trigger uses.
    """
    refresh_time = get_overall_refresh_time()
    local_now = now.astimezone(host_timezone())
    due = local_now.replace(
        hour=refresh_time["hour"],
        minute=refresh_time["minute"],
        second=0,
        microsecond=0,
    )
    if due > local_now:
        due -= timedelta(days=1)
    return due


def _full_scan_started_since(moment: datetime) -> bool:
    """Return whether any full scan was queued at or after *moment*."""
    with get_db() as db:
        row = db.execute(
            """SELECT created_at FROM scanner_jobs
                WHERE job_type='full_scan' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if row is None or not row["created_at"]:
        return False
    try:
        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created >= moment


def _schedule_overall_refresh_attempt(
    run_at: datetime, retry_until: datetime, grace_seconds: int | None = None,
) -> None:
    """Queue the one pending late attempt (start-up catch-up or retry)."""
    if grace_seconds is None:
        grace_seconds = _overall_refresh_grace_seconds()
    _scheduler.add_job(
        _scheduled_overall_refresh,
        "date",
        run_date=run_at,
        kwargs={"retry_until": retry_until.isoformat(timespec="seconds")},
        id=_OVERALL_REFRESH_RETRY_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # The attempt enforces ``retry_until`` itself, so a late wake-up must
        # reach it instead of being dropped by the one-second default.
        misfire_grace_time=grace_seconds,
    )


def _schedule_missed_overall_refresh(now: datetime | None = None) -> datetime | None:
    """Start today's refresh shortly after start-up if its time was missed.

    The in-memory scheduler forgets a run that fell due while the service was
    down. Catch it up once when the scheduled time passed within the misfire
    grace and no full scan has been started since.
    """
    now = now or datetime.now(timezone.utc)
    due = _latest_overall_refresh_due(now)
    grace_seconds = _overall_refresh_grace_seconds()
    retry_until = due + timedelta(seconds=grace_seconds)
    run_at = now + timedelta(seconds=_OVERALL_REFRESH_CATCH_UP_DELAY_SECONDS)
    if run_at > retry_until or _full_scan_started_since(due):
        return None
    _schedule_overall_refresh_attempt(run_at, retry_until, grace_seconds)
    logging.getLogger("scheduler").info(
        "Overall refresh due at %s was missed; starting it at %s",
        due.isoformat(timespec="seconds"),
        run_at.astimezone(host_timezone()).isoformat(timespec="seconds"),
    )
    return run_at


@_tracked_scheduled_start("overall refresh")
def _start_overall_refresh():
    from app.routers.scanner import start_scheduled_full_scan_job

    return start_scheduled_full_scan_job()


def _scheduled_overall_refresh(*, daily: bool = False, retry_until: str | None = None):
    """Submit the daily overall refresh through the durable scanner lane.

    A start that is refused (update drain, busy scanner lane) or raises is
    retried every few minutes until its window closes. The daily cron run
    (``daily``) may be late by the misfire grace counted from its scheduled
    time, a retry carries that deadline in ``retry_until``, and a run
    requested at any other time gets a window of the same length from now.
    """
    log = logging.getLogger("scheduler")
    now = datetime.now(timezone.utc)
    grace_seconds = _overall_refresh_grace_seconds()
    grace = timedelta(seconds=grace_seconds)
    if retry_until is not None:
        try:
            deadline = datetime.fromisoformat(retry_until)
        except ValueError:
            deadline = now
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if now > deadline:
            log.warning(
                "Overall refresh was not started before %s; waiting for the next scheduled run",
                retry_until,
            )
            return {"accepted": False, "status": "expired"}
    elif daily:
        # A cron fire can land a moment before its nominal time.
        deadline = _latest_overall_refresh_due(now + timedelta(minutes=1)) + grace
    else:
        deadline = now + grace

    log.info("Running scheduled overall refresh")
    try:
        result = _start_overall_refresh()
    except Exception as exc:
        log.exception("Scheduled overall refresh could not be submitted")
        result = {"accepted": False, "status": "failed", "error_type": type(exc).__name__}
    if not result.get("accepted"):
        active = result.get("job") if isinstance(result.get("job"), dict) else {}
        context = active.get("context") if isinstance(active.get("context"), dict) else {}
        already_running_this_schedule = bool(
            active.get("job_type") == "full_scan"
            and active.get("trigger_source") == "scheduled"
            and context.get("includes_usage_sync")
        )
        if not already_running_this_schedule:
            retry_at = datetime.now(timezone.utc) + timedelta(
                minutes=_overall_refresh_retry_minutes()
            )
            result = dict(result)
            if retry_at <= deadline:
                _schedule_overall_refresh_attempt(retry_at, deadline, grace_seconds)
                result["retry_scheduled_for"] = retry_at.isoformat(timespec="seconds")
                log.info(
                    "Overall refresh not started (%s); it will retry at %s",
                    result.get("status"),
                    result["retry_scheduled_for"],
                )
            else:
                log.warning(
                    "Overall refresh not started (%s) and its window closes at %s; "
                    "waiting for the next scheduled run",
                    result.get("status"),
                    deadline.isoformat(timespec="seconds"),
                )
    else:
        # A prior collision may have left a date retry pending. Once the full
        # scheduled run is accepted, that extra retry would be duplicate work.
        retry_job = _scheduler.get_job(_OVERALL_REFRESH_RETRY_JOB_ID)
        if retry_job is not None:
            _scheduler.remove_job(_OVERALL_REFRESH_RETRY_JOB_ID)
    log.info("Scheduled overall refresh submission: %s", result.get("status"))
    return result


@_tracked_scheduled_start("weekly Pipeline Insights")
def _scheduled_pipeline_insights(attempt: int = 0):
    from app.pipeline_insights_settings import get_pipeline_insights_settings
    from app.routers.scanner import start_scheduled_pipeline_insights_job

    settings = get_pipeline_insights_settings()
    module_keys = tuple(
        key for key, enabled in (
            ("relation_samples", settings.samples_scheduled),
            ("pipeline_explanations", settings.explanations_scheduled),
        ) if enabled
    )
    if not module_keys:
        return {"status": "disabled", "accepted": False}
    result = start_scheduled_pipeline_insights_job(module_keys)
    if not result.get("accepted") and attempt < 12:
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        _scheduler.add_job(
            _scheduled_pipeline_insights,
            "date",
            run_date=retry_at,
            args=[attempt + 1],
            id=_PIPELINE_INSIGHTS_RETRY_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        result = dict(result)
        result["retry_scheduled_for"] = retry_at.isoformat(timespec="seconds")
    elif result.get("accepted"):
        retry = _scheduler.get_job(_PIPELINE_INSIGHTS_RETRY_JOB_ID)
        if retry is not None:
            _scheduler.remove_job(_PIPELINE_INSIGHTS_RETRY_JOB_ID)
    return result


@_tracked_scheduled_start("scheduled email")
def _scheduled_email_dispatch():
    """Check configured email schedules and send anything due."""
    from app.routers.email_schedules import dispatch_due_email_schedules
    log = logging.getLogger("scheduler")
    try:
        sent = dispatch_due_email_schedules()
        if sent:
            log.info("Email schedules sent: %s", sent)
    except Exception as e:
        log.exception("Email schedule dispatch failed: %s", e)


@_tracked_scheduled_start("scanner notification watchdog")
def _scheduled_scanner_notification_watchdog():
    """Notify once for stalled scanner modules and reconcile Outlook receipts."""
    from app.routers.email import reconcile_outlook_dispatches
    from app.scanner.notifications import notify_stalled_module_runs

    log = logging.getLogger("scheduler")
    try:
        reconciled = reconcile_outlook_dispatches()
        stalled = notify_stalled_module_runs()
        if reconciled.get("processed") or reconciled.get("unknown") or stalled.get("stalled"):
            log.info(
                "Scanner notification watchdog: reconciled=%s stalled=%s",
                reconciled,
                stalled,
            )
    except Exception as exc:
        log.exception("Scanner notification watchdog failed: %s", exc)


@_tracked_scheduled_start("Power BI recurrence")
def _scheduled_recurrence_dispatch():
    """Export due Power BI visuals and launch subgroup emails."""
    from app.routers.recurrences import dispatch_due_recurrences
    log = logging.getLogger("scheduler")
    try:
        results = dispatch_due_recurrences()
        if results:
            log.info("Power BI recurrence runs: %s", results)
    except Exception as e:
        log.exception("Power BI recurrence dispatch failed: %s", e)


@_tracked_scheduled_start("Alert AI enrichment")
def _scheduled_alert_ai_enrichment():
    """Attach advisory local-model analysis to new canonical Alert revisions."""
    from app.ai.operations_agent import enrich_active_alerts
    log = logging.getLogger("scheduler")
    try:
        result = enrich_active_alerts()
        if result["queued"]:
            log.info("Queued automatic Alert analyses: %s", result["queued"])
    except Exception as exc:
        # AI is advisory. A provider outage must never interrupt detector,
        # scanner, Flow, Pipeline, or email scheduler work.
        log.exception("Automatic Alert analysis failed: %s", exc)


@_tracked_scheduled_start("scheduled Flow")
def _scheduled_flow_dispatch():
    return flows.queue_due_flows()


@_tracked_scheduled_start("scheduled Flow catalog scan")
def _scheduled_flow_catalog_dispatch():
    return flows.queue_due_catalog_scans()


@_tracked_scheduled_start("Flow SQL catalog refresh")
def _scheduled_flow_sql_catalog_refresh():
    return flows.refresh_sql_catalog()


def _configure_overall_refresh_job() -> dict:
    refresh_time = get_overall_refresh_time()
    _scheduler.add_job(
        _scheduled_overall_refresh,
        "cron",
        hour=refresh_time["hour"],
        minute=refresh_time["minute"],
        kwargs={"daily": True},
        id="daily_overall_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_overall_refresh_grace_seconds(),
    )
    return refresh_time


def _configure_backup_job() -> str:
    """Schedule the daily database backup at its saved time; returns ``HH:MM``."""
    hour, minute = _daily_backup_time()
    _scheduler.add_job(
        _scheduled_backup, "cron", hour=hour, minute=minute, id="daily_backup",
        replace_existing=True,
    )
    return f"{hour:02d}:{minute:02d}"


def _configure_auto_update_job(*, first_run_at: datetime | None = None) -> int:
    """Schedule the update check at its saved interval; returns the seconds."""
    seconds = _update_check_seconds()
    first_run = {"next_run_time": first_run_at} if first_run_at is not None else {}
    _scheduler.add_job(
        _scheduled_auto_update,
        "interval",
        seconds=seconds,
        id="automatic_main_update",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        **first_run,
    )
    return seconds


def _reschedule_after_save(what: str, reschedule):
    """A settings listener that reschedules one job and never raises into the save."""
    def apply() -> None:
        try:
            reschedule()
        except Exception:
            logging.getLogger("scheduler").exception(
                "The %s setting was saved but its job could not be rescheduled", what
            )

    return apply


def _register_settings_listeners() -> None:
    """Reschedule jobs the moment their setting is saved on the Settings page.

    The older per-page routes reschedule after they save; a save through the
    settings registry reaches the same functions through these listeners.
    """
    global _SETTINGS_LISTENERS_REGISTERED
    from app import freshness, settings_registry

    # Start-up may have read the grace before the database was ready.
    freshness.refresh_grace_period()
    if _SETTINGS_LISTENERS_REGISTERED:
        return
    for keys, what, reschedule in (
        (("scan.daily_time", "scan.late_start_hours"), "daily refresh",
         lambda: _configure_overall_refresh_job()),
        (("insights.weekday", "insights.time", "insights.samples", "insights.explanations"),
         "weekly pipeline insights", lambda: configure_pipeline_insights_job()),
        (("updates.check_seconds",), "update check", lambda: _configure_auto_update_job()),
        (("backups.daily_time",), "daily backup", lambda: _configure_backup_job()),
    ):
        settings_registry.subscribe(keys, _reschedule_after_save(what, reschedule))
    _SETTINGS_LISTENERS_REGISTERED = True


def configure_pipeline_insights_job() -> dict:
    from app.pipeline_insights_settings import get_pipeline_insights_settings

    settings = get_pipeline_insights_settings()
    if not settings.samples_scheduled and not settings.explanations_scheduled:
        for job_id in ("weekly_pipeline_insights", _PIPELINE_INSIGHTS_RETRY_JOB_ID):
            if _scheduler.get_job(job_id) is not None:
                _scheduler.remove_job(job_id)
        return settings.public_dict()
    hour, minute = (int(part) for part in settings.time.split(":"))
    _scheduler.add_job(
        _scheduled_pipeline_insights,
        "cron",
        day_of_week=settings.weekday[:3],
        hour=hour,
        minute=minute,
        id="weekly_pipeline_insights",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return settings.public_dict()


def pipeline_insights_schedule_payload() -> dict:
    from app.pipeline_insights_settings import get_pipeline_insights_settings

    settings = get_pipeline_insights_settings()
    job = _scheduler.get_job("weekly_pipeline_insights")
    next_run = getattr(job, "next_run_time", None) if job else None
    return {
        "next_run_at": next_run.isoformat() if next_run else None,
        "scheduler_running": bool(getattr(_scheduler, "running", False)),
        "timezone": str(host_timezone()),
        **settings.public_dict(),
    }


@_tracked_scheduled_start("signed remote Flow test")
def _scheduled_remote_flow_control():
    return remote_flow_control.poll_once()


def _scheduled_log_housekeeping():
    logging_setup.refresh_request_log_mode()
    return log_housekeeping.run()


def _configure_scheduler_jobs() -> dict:
    _configure_backup_job()
    # The first run is two minutes after start-up: an update restarts every
    # service, which is when nssm rotates an oversized log, and that file
    # should go now rather than at the next six-hourly pass.
    logging_setup.refresh_request_log_mode()
    _scheduler.add_job(
        _scheduled_log_housekeeping, "interval", hours=6, id="log_housekeeping",
        replace_existing=True, max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    refresh_time = _configure_overall_refresh_job()
    # A failed catch-up check must never keep the scheduler from starting.
    _run_optional_startup_step(
        "missed overall refresh catch-up", _schedule_missed_overall_refresh
    )
    _scheduler.add_job(auditor_engine.tick, "interval", seconds=30,
                       id="data_auditor_tick", replace_existing=True,
                       max_instances=1, coalesce=True)
    configure_pipeline_insights_job()
    _scheduler.add_job(
        _scheduled_email_dispatch,
        "interval",
        minutes=1,
        id="email_schedule_dispatch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_scanner_notification_watchdog,
        "interval",
        minutes=1,
        id="scanner_notification_watchdog",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_recurrence_dispatch,
        "interval",
        minutes=1,
        id="pbi_recurrence_dispatch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_pending_pbi_sync_retry,
        "interval",
        minutes=1,
        id="pending_pbi_sync_retry",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_alert_ai_enrichment,
        "interval",
        seconds=30,
        id="alert_ai_enrichment",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _configure_auto_update_job(first_run_at=datetime.now(timezone.utc) + timedelta(seconds=60))
    _scheduler.add_job(
        _scheduled_remote_flow_control,
        "interval",
        seconds=30,
        id="signed_remote_flow_control",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    _scheduler.add_job(
        _scheduled_flow_dispatch,
        "interval",
        minutes=1,
        id="flow_dispatch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        flows.fail_stale_runs,
        "interval",
        seconds=10,
        id="flow_stale_run_reaper",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        pipelines.pipeline_tick,
        "interval",
        seconds=5,
        id="pipeline_full_refresh_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_flow_catalog_dispatch,
        "interval",
        minutes=15,
        id="flow_catalog_scan_dispatch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_flow_sql_catalog_refresh,
        "cron",
        **flows.sql_catalog_refresh_time(),
        id="flow_sql_catalog_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        flows.ensure_local_worker,
        "interval",
        minutes=1,
        id="flow_local_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return refresh_time


def _reconcile_startup_flow_targets() -> dict:
    """Repair uniquely resolvable legacy Flow links after schema migration."""
    with get_db() as db:
        return reconcile_all_flow_targets(
            db,
            server=postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT),
        )


def _recover_startup_scan_runs() -> int:
    """Close scan rows left running when the prior service process ended."""
    with get_db() as db:
        return recover_interrupted_scan_runs(db)


def _recover_startup_pbi_syncs() -> int:
    """Reject callbacks from Power BI processes launched before this restart."""
    from app.scanner.pbi_sync import recover_interrupted_pbi_syncs

    return recover_interrupted_pbi_syncs()


def _run_optional_startup_step(name: str, function, *, default=None):
    """Keep ancillary recovery work from taking down the web application."""
    try:
        return function()
    except Exception:
        logging.getLogger(__name__).exception(
            "Startup step failed; Metronome will continue without %s", name
        )
        return default


def _sync_portal_sign_in_copy() -> None:
    """Flow scripts read the portal sign-in from the encrypted file: make it the settings file's."""
    from app import flow_credentials

    try:
        outcome = flow_credentials.sync_credential_file()
    except Exception as exc:
        # The kind of failure only: the record holds the password.
        logging.getLogger(__name__).warning(
            "The encrypted copy of the portal sign-in was not written (%s)", type(exc).__name__
        )
        return
    if outcome in ("written", "removed"):
        logging.getLogger(__name__).info("Encrypted copy of the portal sign-in: %s", outcome)


def _start_runtime():
    """Recover interrupted work, then start the scheduler and Flow workers."""
    from app.pipeline_insights_db import init_pipeline_insights_db
    init_pipeline_insights_db()
    _sync_portal_sign_in_copy()
    startup_update_attempt = _run_optional_startup_step(
        "update-attempt reconciliation",
        _reconcile_update_attempts,
        # If reconciliation is broken, fail closed for new operational work
        # while still bringing the read-only web UI online.
        default={"active": True, "attempt_id": "unresolved-startup-update"},
    )
    if startup_update_attempt and startup_update_attempt.get("active"):
        # The external task has started this new process but has not yet
        # published its terminal health receipt. Keep every new work starter
        # behind the drain barrier until that receipt is reconciled.
        _request_update_drain()
        logging.getLogger(__name__).info(
            "Update attempt %s is awaiting external health verification",
            startup_update_attempt.get("attempt_id"),
        )
    from app.ai.runtime_config import initialize_runtime_settings

    ai_settings = initialize_runtime_settings()
    logging.getLogger(__name__).info(
        "AI runtime: mode=%s model=%s operations=%s alert_auto=%s email=%s docs=%s pipeline_explanations=%s",
        ai_settings.mode,
        ai_settings.model,
        ai_settings.feature_enabled("operations_investigator"),
        ai_settings.feature_enabled("automatic_alert_review"),
        ai_settings.feature_enabled("alert_email_analysis"),
        ai_settings.feature_enabled("documentation_suggestions")
        and ai_settings.qwen_enabled,
        ai_settings.feature_enabled("pipeline_explanations")
        and ai_settings.qwen_enabled,
    )
    from app.scanner.jobs import recover_interrupted_jobs

    interrupted_jobs = _run_optional_startup_step(
        "scanner-job recovery", recover_interrupted_jobs, default=0
    )
    if interrupted_jobs:
        logging.getLogger(__name__).warning(
            "Recovered %d scanner job(s) interrupted by restart", interrupted_jobs
        )
    from app.scanner.modules import recover_interrupted_module_runs

    interrupted_modules = _run_optional_startup_step(
        "scanner-module recovery", recover_interrupted_module_runs, default=0
    )
    if interrupted_modules:
        logging.getLogger(__name__).warning(
            "Recovered %d scanner module run(s) interrupted by restart",
            interrupted_modules,
        )
    interrupted_pbi_syncs = _run_optional_startup_step(
        "Power BI sync recovery", _recover_startup_pbi_syncs, default=0
    )
    if interrupted_pbi_syncs:
        logging.getLogger(__name__).warning(
            "Recovered %d Power BI sync attempt(s) interrupted by restart",
            interrupted_pbi_syncs,
        )
    interrupted_scans = _run_optional_startup_step(
        "scan recovery", _recover_startup_scan_runs, default=0
    )
    if interrupted_scans:
        logging.getLogger(__name__).warning(
            "Recovered %d scan run(s) interrupted by restart", interrupted_scans
        )
    reconciliation = _run_optional_startup_step(
        "Flow target reconciliation",
        _reconcile_startup_flow_targets,
        default={
            "total": 0,
            "changed": 0,
            "confirmed": 0,
            "ambiguous": 0,
            "unresolved": 0,
        },
    )
    logging.getLogger(__name__).info(
        "Flow target reconciliation: total=%d changed=%d confirmed=%d "
        "ambiguous=%d unresolved=%d",
        reconciliation["total"],
        reconciliation["changed"],
        reconciliation["confirmed"],
        reconciliation["ambiguous"],
        reconciliation["unresolved"],
    )

    _run_optional_startup_step("settings listeners", _register_settings_listeners)
    # Daily backup plus a user-configurable overall refresh.
    refresh_time = _run_optional_startup_step(
        "background-job configuration", _configure_scheduler_jobs
    )
    scheduler_started = False
    if refresh_time is not None:
        scheduler_started = bool(
            _run_optional_startup_step(
                "background scheduler",
                lambda: _scheduler.start() or True,
                default=False,
            )
        )
    _run_optional_startup_step("local Flow worker start", flows.ensure_local_worker)
    # Let Pipeline restart reconciliation establish authoritative unknown/
    # terminal states before queued read-only investigations can observe it.
    _run_optional_startup_step(
        "Pipeline restart reconciliation", pipelines.pipeline_tick
    )
    from app.ai.operations_agent import recover_and_start as recover_ai_runs
    recovered_ai_runs = _run_optional_startup_step(
        "AI investigation recovery", recover_ai_runs, default=0
    )
    if recovered_ai_runs:
        logging.getLogger(__name__).info(
            "Resubmitted %d queued AI investigation(s)", recovered_ai_runs
        )
    _run_optional_startup_step(
        "initial alert AI enrichment", _scheduled_alert_ai_enrichment
    )
    if scheduler_started:
        logging.getLogger(__name__).info(
            "Scheduler started: backup at %02d:%02d, overall refresh at %02d:%02d, "
            "email dispatch every minute, main update check every %d seconds (enabled=%s)",
            *_daily_backup_time(),
            refresh_time["hour"],
            refresh_time["minute"],
            _update_check_seconds(),
            _auto_update_enabled(),
        )
    else:
        logging.getLogger(__name__).error(
            "Metronome web is available, but background scheduling did not start"
        )


@asynccontextmanager
async def lifespan(app):
    logging.getLogger(__name__).info("Database path: %s", DB_PATH)
    init_db()
    auditor_store.recover()
    from app import flow_handover
    # Refreshing every Flow's continuity files can take many minutes on a
    # shared folder, and an update rewrites all of them. The web port opens
    # only once this yields, so hold the refreshes startup commits would
    # trigger and do one pass in the background instead.
    flow_handover.hold()
    try:
        _start_runtime()
    finally:
        flow_handover.catch_up_in_background(DB_PATH)

    yield

    if getattr(_scheduler, "running", False):
        _scheduler.shutdown(wait=False)
    scanner.shutdown_scanner_executor()
    from app.ai.operations_agent import shutdown_executor as shutdown_ai_executor
    shutdown_ai_executor()
    auditor_engine.shutdown()
    pipelines.shutdown_pipeline_executor()


app = FastAPI(title="Metronome", version="0.1.0", lifespan=lifespan)
app.add_middleware(NoCacheStaticMiddleware)
app.add_middleware(UserIdentityMiddleware)
app.add_middleware(UpdateDrainMiddleware)

# Register API routers
app.include_router(dashboard.router)
app.include_router(sources.router)
app.include_router(reports.router)
app.include_router(scanner.router)
app.include_router(lineage.router)
app.include_router(alerts.router)
app.include_router(actions.router)
app.include_router(query_history.router)
app.include_router(ai_router)
app.include_router(auditor_router)
app.include_router(changelog.router)
app.include_router(schedules.router)
app.include_router(create.router)
app.include_router(best_practices.router)  # Retired checker: compatibility 410 only.
app.include_router(data_quality.router)
app.include_router(tasks.router)
app.include_router(eventlog.router)
app.include_router(people.router)
app.include_router(archive.router)
app.include_router(documentation.router)
app.include_router(email.router)
app.include_router(email_schedules.router)
app.include_router(usage.router)
app.include_router(materialized_views.router)
app.include_router(recurrences.router)
from app.routers.flow_recordings import router as flow_recordings_router
app.include_router(flow_recordings_router)
app.include_router(flows.router)
app.include_router(system_paths_router)
app.include_router(system_flows_router)
app.include_router(system_logs_router)
app.include_router(system_settings_router)
app.include_router(flow_tasks_router)
app.include_router(pipelines.router)
app.include_router(pipeline_insights.router)

# Serve static files (the web panel)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Auto-incrementing cache buster based on file modification time
def _cache_ver():
    js_path = static_dir / "app.js"
    css_path = static_dir / "style.css"
    t = max(js_path.stat().st_mtime if js_path.exists() else 0,
            css_path.stat().st_mtime if css_path.exists() else 0)
    return str(int(t))

def _serve_index():
    """Serve index.html with dynamic cache-busting version."""
    html = (static_dir / "index.html").read_text()
    ver = _cache_ver()
    html = re.sub(r'\?v=\d+', f'?v={ver}', html)
    return HTMLResponse(content=html, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    })


def _get_version() -> str:
    """Get the version from VERSION file, or fall back to git, or 'dev'."""
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent.parent),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "dev"

_APP_VERSION = _get_version()

#: Latest-commit lookup shared by the version badge and automatic updater.
#: Share lookups across the UI and watcher to avoid redundant GitHub requests.
_UPDATE_CHECK_TTL_SECONDS = 90
_UPDATE_CHECK_LOCK = threading.Lock()
_UPDATE_CHECK: dict = {"checked_at": 0.0, "latest_commit": None, "error": None}
_LATEST_COMMIT_URL = (
    "https://api.github.com/repos/datap0nd/data_governance/commits/main"
)
_TESTS_WORKFLOW_PATH = ".github/workflows/tests.yml"
_TESTS_GATE_LOCK = threading.Lock()
_TESTS_GATE_CACHE: dict[str, tuple[float, dict]] = {}
_AUTO_UPDATE_SETTING_KEY = "automatic_main_updates_enabled"
_AUTO_UPDATE_INTERVAL_SECONDS = 90
_AUTO_UPDATE_RETRY_SECONDS = 30 * 60
_AUTO_UPDATE_RESERVATION_STALE_SECONDS = 10 * 60
_AUTO_UPDATE_ATTEMPT_STALE_SECONDS = 2 * 60 * 60
_AUTO_UPDATE_TASK_NAME = "Metronome_Auto_Update"
_AUTO_UPDATE_RUN_LOCK = threading.Lock()
_AUTO_UPDATE_STATE_LOCK = threading.Lock()
_AUTO_UPDATE_ACTIVITY_LOCK = threading.Lock()
_AUTO_UPDATE_DRAIN_EVENT = threading.Event()
_AUTO_UPDATE_ACTIVE_STARTS = 0
_AUTO_UPDATE_STATE: dict = {
    "status": "starting",
    "last_checked_at": None,
    "last_attempt_at": None,
    "last_attempt_commit": None,
    "last_attempt_monotonic": 0.0,
    "last_error": None,
    "next_attempt_at": None,
}


def _update_check_seconds(built_in: int | None = None) -> int:
    """How often Metronome looks for a new version (Settings), in seconds.

    One setting drives the watcher's interval and how long a looked-up commit
    is reused; ``built_in`` is the constant of whichever is asking.
    """
    if built_in is None:
        built_in = _AUTO_UPDATE_INTERVAL_SECONDS
    return max(1, int(operator_limits.value("updates.check_seconds", built_in)))


def _auto_update_retry_seconds() -> int:
    """How long a failed install holds back another try of the same version (Settings)."""
    minutes = operator_limits.value("updates.retry_minutes", _AUTO_UPDATE_RETRY_SECONDS / 60)
    return max(0, int(float(minutes) * 60))


def _github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Metronome",
        "Cache-Control": "no-cache",
    }
    token = os.environ.get("DG_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_api_json_via_powershell(request_url: str):
    """Use Windows' native proxy and certificate stack as a network fallback."""
    child_env = os.environ.copy()
    child_env["METRONOME_GITHUB_REQUEST_URL"] = request_url
    script = r"""
$ErrorActionPreference = 'Stop'
$headers = @{
    'Accept' = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent' = 'Metronome'
    'Cache-Control' = 'no-cache'
}
if ($env:DG_GITHUB_TOKEN) {
    $headers['Authorization'] = 'Bearer ' + $env:DG_GITHUB_TOKEN
}
$payload = Invoke-RestMethod -Uri $env:METRONOME_GITHUB_REQUEST_URL `
    -Headers $headers -TimeoutSec 30
$payload | ConvertTo-Json -Depth 20 -Compress
"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=40,
        creationflags=flags,
        env=child_env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "PowerShell request failed").strip()
        raise RuntimeError(detail[-500:])
    try:
        return json.loads((completed.stdout or "").lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Windows GitHub fallback returned invalid JSON") from exc


def _windows_github_fallback_available() -> bool:
    return os.name == "nt"


def _github_api_json(url: str, *, params: dict | None = None):
    """Read GitHub through the office proxy, with a Windows-native fallback."""
    import httpx

    request_url = url
    if params:
        request_url = f"{url}?{urlencode(params)}"
    try:
        response = httpx.get(
            url,
            headers=_github_api_headers(),
            params=params,
            proxy=resolve_proxy(url),
            follow_redirects=True,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except Exception as primary_error:
        if not _windows_github_fallback_available():
            raise
        try:
            payload = _github_api_json_via_powershell(request_url)
            logging.getLogger("auto_update").warning(
                "Python GitHub request failed; Windows network fallback succeeded: %s",
                _safe_update_error(primary_error),
            )
            return payload
        except Exception as fallback_error:
            raise RuntimeError(
                "GitHub request failed through both the application and the "
                f"Windows network stack: {primary_error}; {fallback_error}"
            ) from primary_error


def _deployed_commit() -> str | None:
    """The commit SHA setup.ps1 stamps into VERSION ("<timestamp>-<sha>")."""
    token = _APP_VERSION.rsplit("-", 1)[-1].strip().lower()
    # A Git SHA prefix is hexadecimal and may legitimately contain only
    # decimal digits.  Timestamp-only VERSION stamps end in six digits, which
    # is shorter than the accepted seven-character commit prefix.
    if re.fullmatch(r"[0-9a-f]{7,40}", token):
        return token
    return None


def _fetch_latest_commit() -> str:
    # A forced local refresh alone does not defeat a stale office-proxy cache.
    # Preserve the same unique request URL through the Windows fallback.
    payload = _github_api_json(
        _LATEST_COMMIT_URL, params={"_metronome_check": uuid.uuid4().hex}
    )
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid main commit response")
    commit = str(payload.get("sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("GitHub returned an invalid main commit identifier")
    return commit


def _latest_commit(*, force: bool = False) -> tuple[str | None, str | None]:
    """Return ``(latest main commit, error)`` using the shared short cache."""
    reuse_seconds = 0 if force else _update_check_seconds(_UPDATE_CHECK_TTL_SECONDS)
    with _UPDATE_CHECK_LOCK:
        if (
            not force
            and time.time() - _UPDATE_CHECK["checked_at"] < reuse_seconds
        ):
            return _UPDATE_CHECK["latest_commit"], _UPDATE_CHECK["error"]
        try:
            _UPDATE_CHECK["latest_commit"] = _fetch_latest_commit()
            _UPDATE_CHECK["error"] = None
        except Exception as exc:
            _UPDATE_CHECK["error"] = f"{type(exc).__name__}: {exc}"
        _UPDATE_CHECK["checked_at"] = time.time()
        return _UPDATE_CHECK["latest_commit"], _UPDATE_CHECK["error"]


def _commits_match(deployed: str | None, latest: str | None) -> bool:
    return bool(
        deployed
        and latest
        and (latest.startswith(deployed) or deployed.startswith(latest))
    )


def _auto_update_enabled() -> bool:
    try:
        raw = get_setting(_AUTO_UPDATE_SETTING_KEY, "1")
    except Exception as exc:
        # Startup/version reporting must keep working even while SQLite is
        # temporarily unavailable.  The installed default remains enabled.
        logging.getLogger("auto_update").warning(
            "Could not read automatic-update setting; using enabled default: %s",
            _safe_update_error(exc),
        )
        raw = "1"
    return str(raw or "").strip().casefold() in {"1", "true", "yes", "on"}


def _safe_update_error(value: object) -> str:
    message = " ".join(str(value or "Update failed").split())
    token = os.environ.get("DG_GITHUB_TOKEN")
    if token:
        message = message.replace(token, "[redacted]")
    message = re.sub(
        r"(?i)(authorization|token|password|secret)\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        message,
    )
    lowered = message.casefold()
    if (
        "handshake operation timed out" in lowered
        or "operation has timed out" in lowered
        or "operation timed out" in lowered
        or (
            "timed out" in lowered
            and ("ssl" in lowered or "github" in lowered)
        )
    ):
        return (
            "GitHub did not answer before the office network timeout. "
            "Metronome will retry automatically; no test or installation failed."
        )
    return message[:500]


def _tests_gate_record(
    target_commit: str | None,
    state: str,
    message: str,
    *,
    checked_at: str | None = None,
    error: str | None = None,
    run: dict | None = None,
) -> dict:
    run = run or {}
    return {
        "workflow": "Tests",
        "workflow_file": "tests.yml",
        "workflow_path": _TESTS_WORKFLOW_PATH,
        "target_commit": target_commit,
        "state": state,
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "run_id": run.get("id"),
        "run_attempt": run.get("run_attempt"),
        "url": run.get("html_url"),
        "checked_at": checked_at,
        "message": message,
        "error": _safe_update_error(error) if error else None,
    }


def _set_auto_update_state(**changes) -> None:
    # ``next_attempt_at`` only describes the ``retry_wait`` status.
    changes.setdefault("next_attempt_at", None)
    with _AUTO_UPDATE_STATE_LOCK:
        _AUTO_UPDATE_STATE.update(changes)


def _auto_update_payload(
    *,
    latest_commit: str | None = None,
    check_error: str | None = None,
) -> dict:
    with _AUTO_UPDATE_STATE_LOCK:
        state = dict(_AUTO_UPDATE_STATE)
    state.pop("last_attempt_monotonic", None)
    if _UPDATE_CHECK.get("checked_at"):
        state["last_checked_at"] = datetime.fromtimestamp(
            _UPDATE_CHECK["checked_at"], timezone.utc
        ).isoformat(timespec="seconds")
    deployed = _deployed_commit()
    latest = latest_commit or _UPDATE_CHECK.get("latest_commit")
    error = check_error if check_error is not None else _UPDATE_CHECK.get("error")
    enabled = _auto_update_enabled()
    interval_seconds = _update_check_seconds()
    return {
        "enabled": enabled,
        "branch": "main",
        "interval_minutes": interval_seconds / 60,
        "interval_seconds": interval_seconds,
        "task_name": _AUTO_UPDATE_TASK_NAME,
        "draining": _AUTO_UPDATE_DRAIN_EVENT.is_set(),
        "deployed_commit": deployed,
        "latest_commit": latest,
        "update_available": (
            bool(deployed and latest and not error) and not _commits_match(deployed, latest)
        ),
        "check_error": _safe_update_error(error) if error else None,
        **state,
    }


def _launch_registered_auto_update_task() -> None:
    """Start the pre-registered elevated task that survives service restart."""
    if os.name != "nt":
        raise RuntimeError("Automatic updates require the installed Windows service")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    query = subprocess.run(
        ["schtasks", "/query", "/tn", _AUTO_UPDATE_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=flags,
    )
    if query.returncode != 0:
        raise RuntimeError(
            "Automatic update task is not installed; run setup.ps1 once to register it"
        )
    launch = subprocess.run(
        ["schtasks", "/run", "/tn", _AUTO_UPDATE_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=flags,
    )
    if launch.returncode != 0:
        raise RuntimeError(
            "Windows could not start the automatic update task: "
            + (launch.stderr or launch.stdout or f"exit {launch.returncode}")
        )


def _registered_auto_update_task_ready() -> tuple[bool, str | None]:
    """Return whether interactive setup provisioned the fixed elevated task."""
    if (_CODE_DIR / ".git").exists():
        return False, (
            "Automatic installs are disabled in a Git working copy; use a setup.ps1 "
            "production install so local source changes cannot be overwritten"
        )
    if os.name != "nt":
        return False, "Automatic updates require the installed Windows service"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", _AUTO_UPDATE_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=flags,
        )
    except Exception as exc:
        return False, _safe_update_error(exc)
    if result.returncode == 0:
        return True, None
    return False, (
        "Automatic update task is not installed; run setup.ps1 once to register it"
    )


_CODE_DIR = Path(__file__).resolve().parent.parent
_UPDATE_ROOT = _CODE_DIR.parent / "updates"
_UPDATE_REQUEST_PATH = _UPDATE_ROOT / "pending_update.json"
_UPDATE_RECEIPT_DIR = _UPDATE_ROOT / "receipts"


def _table_exists(db, table_name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _active_update_work(db) -> dict[str, int]:
    """Return in-flight production work that should finish before restart."""
    checks = {
        "scanner_jobs": ("scanner_jobs", "status IN ('queued','running')"),
        "scan_runs": ("scan_runs", "status='running'"),
        "flow_runs": ("flow_runs", "status IN ('queued','claimed','running')"),
        "flow_download_tasks": ("flow_download_tasks", "state IN ('claimed','cancelling')"),
        "flow_catalog_scans": (
            "flow_catalog_scans",
            "status IN ('queued','claimed','running')",
        ),
        "pipeline_runs": (
            "pipeline_runs",
            "status NOT IN ('succeeded','failed')",
        ),
        "pipeline_notifications": (
            "pipeline_run_steps",
            "step_type='notification' AND status IN ('pending','running')",
        ),
        # A ``pending`` row records a deferred future retry, not a running
        # process. It survives the restart and resumes afterward; only a
        # launched sync must finish before services stop.
        "pbi_sync_runs": ("pbi_sync_runs", "status='launched'"),
        "pbi_recurrence_runs": ("pbi_recurrence_runs", "status='running'"),
        # AI investigations are advisory, durable, and recovered after a
        # restart, so they must never keep production code pinned while a local
        # model drains its queue.
        "outlook_dispatches": ("outlook_dispatches", "status='pending'"),
    }
    active: dict[str, int] = {}
    for label, (table_name, predicate) in checks.items():
        if not _table_exists(db, table_name):
            continue
        count = int(
            db.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE {predicate}"
            ).fetchone()[0]
        )
        if count:
            active[label] = count
    with _AUTO_UPDATE_ACTIVITY_LOCK:
        in_flight_starts = int(_AUTO_UPDATE_ACTIVE_STARTS)
    if in_flight_starts:
        active["in_flight_operations"] = in_flight_starts
    return active


def _update_attempt_dict(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["active"] = item.pop("active_slot", None) == 1
    return item


def _read_update_receipt(attempt_id: str) -> dict | None:
    path = _UPDATE_RECEIPT_DIR / f"{attempt_id}.json"
    try:
        if not path.exists() or path.stat().st_size > 128 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("attempt_id") != attempt_id:
        return None
    return value


def _update_attempt_age_seconds(attempt: dict) -> float:
    raw = attempt.get("launched_at") or attempt.get("created_at")
    if not raw:
        return 0.0
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _reconcile_update_attempts() -> dict | None:
    """Reconcile the external updater receipt after this service restarts."""
    with get_db() as db:
        row = db.execute(
            """SELECT * FROM app_update_attempts
                WHERE active_slot=1
                ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        attempt = dict(row)
        receipt = _read_update_receipt(attempt["attempt_id"])
        deployed = _deployed_commit()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stale_after = (
            _AUTO_UPDATE_RESERVATION_STALE_SECONDS
            if attempt.get("status") == "reserved" and not receipt
            else _AUTO_UPDATE_ATTEMPT_STALE_SECONDS
        )
        stale = _update_attempt_age_seconds(attempt) > stale_after
        if receipt:
            receipt_target = str(receipt.get("target_commit") or "").lower()
            receipt_status = str(receipt.get("status") or "").casefold()
            if receipt_target != attempt["target_commit"]:
                receipt_status = "failed"
                receipt["error"] = "Updater receipt target did not match its reservation."
            if receipt_status == "succeeded":
                if _commits_match(deployed, attempt["target_commit"]):
                    db.execute(
                        """UPDATE app_update_attempts
                              SET status='succeeded', stage='healthy', message=?,
                                  error=NULL, active_slot=NULL, finished_at=?, updated_at=?
                            WHERE id=?""",
                        (
                            str(receipt.get("message") or "Update installed and verified.")[:1000],
                            now,
                            now,
                            attempt["id"],
                        ),
                    )
                else:
                    db.execute(
                        """UPDATE app_update_attempts
                              SET status='failed', stage='version_mismatch',
                                  message='Updater reported success but the deployed version does not match.',
                                  error='Expected commit was not deployed.', active_slot=NULL,
                                  finished_at=?, updated_at=? WHERE id=?""",
                        (now, now, attempt["id"]),
                    )
            elif receipt_status in {"failed", "rolled_back"}:
                db.execute(
                    """UPDATE app_update_attempts
                          SET status='failed', stage='rolled_back', message=?, error=?,
                              active_slot=NULL, finished_at=?, updated_at=?
                        WHERE id=?""",
                    (
                        str(receipt.get("message") or "Automatic update failed.")[:1000],
                        _safe_update_error(receipt.get("error") or "Update rolled back"),
                        now,
                        now,
                        attempt["id"],
                    ),
                )
            elif stale:
                db.execute(
                    """UPDATE app_update_attempts
                          SET status='failed', stage='timed_out',
                              message='The external updater did not finish in time.',
                              error='Automatic update attempt timed out.',
                              active_slot=NULL, finished_at=?, updated_at=?
                        WHERE id=?""",
                    (now, now, attempt["id"]),
                )
            else:
                db.execute(
                    """UPDATE app_update_attempts
                          SET status='verifying', stage=?, message=?, updated_at=?
                        WHERE id=?""",
                    (
                        str(receipt.get("stage") or "verifying")[:100],
                        str(receipt.get("message") or "Waiting for updater verification.")[:1000],
                        now,
                        attempt["id"],
                    ),
                )
        elif stale:
            db.execute(
                """UPDATE app_update_attempts
                      SET status='failed', stage='timed_out',
                          message='The external updater stopped reporting progress.',
                          error='No updater receipt was received before the timeout.',
                          active_slot=NULL, finished_at=?, updated_at=?
                    WHERE id=?""",
                (now, now, attempt["id"]),
            )
        elif _commits_match(deployed, attempt["target_commit"]):
            # The new process is healthy enough to answer, but the external
            # task may still be polling its health endpoint before receipt.
            db.execute(
                """UPDATE app_update_attempts
                      SET status='verifying', stage='service_started',
                          message='New service started; waiting for updater health receipt.',
                          updated_at=? WHERE id=?""",
                (now, attempt["id"]),
            )
        updated = _update_attempt_dict(
            db.execute(
                "SELECT * FROM app_update_attempts WHERE id=?",
                (attempt["id"],),
            ).fetchone()
        )
    if updated and not updated.get("active"):
        _release_update_drain()
    return updated


def _latest_update_attempts() -> tuple[dict | None, dict | None]:
    _reconcile_update_attempts()
    with get_db() as db:
        active = db.execute(
            """SELECT * FROM app_update_attempts
                WHERE active_slot=1 ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        latest = db.execute(
            "SELECT * FROM app_update_attempts ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _update_attempt_dict(active), _update_attempt_dict(latest)


def _atomic_write_update_request(payload: dict) -> None:
    _UPDATE_ROOT.mkdir(parents=True, exist_ok=True)
    _UPDATE_RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = _UPDATE_ROOT / f"pending_update.{payload['attempt_id']}.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, _UPDATE_REQUEST_PATH)


def _remove_update_request_if_owned(attempt_id: str) -> None:
    """Remove a failed launch request without touching a newer reservation."""
    try:
        if not _UPDATE_REQUEST_PATH.exists() or _UPDATE_REQUEST_PATH.stat().st_size > 128 * 1024:
            return
        current = json.loads(_UPDATE_REQUEST_PATH.read_text(encoding="utf-8"))
        if isinstance(current, dict) and current.get("attempt_id") == attempt_id:
            _UPDATE_REQUEST_PATH.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError):
        return


def _reserve_and_launch_update(target_commit: str, trigger_source: str) -> dict:
    target = str(target_commit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", target):
        raise RuntimeError("The update target is not a full Git commit identifier")
    if trigger_source not in {"automatic", "manual"}:
        raise RuntimeError("The update trigger source is invalid")

    attempt_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    from_commit = _deployed_commit()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM app_update_attempts WHERE active_slot=1 LIMIT 1"
        ).fetchone()
        if existing is not None:
            return _update_attempt_dict(existing)
        db.execute(
            """INSERT INTO app_update_attempts
                   (attempt_id, from_commit, target_commit, trigger_source,
                    status, stage, message, active_slot, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'reserved', 'preparing',
                       'Preparing exact-commit update request.', 1, ?, ?)""",
            (
                attempt_id,
                from_commit,
                target,
                trigger_source,
                created_at,
                created_at,
            ),
        )

    request_payload = {
        "version": 1,
        "attempt_id": attempt_id,
        "target_commit": target,
        "from_commit": from_commit,
        "trigger_source": trigger_source,
        "code_dir": str(_CODE_DIR),
        "database_path": str(Path(DB_PATH).resolve()),
        "receipt_path": str(_UPDATE_RECEIPT_DIR / f"{attempt_id}.json"),
        "created_at": created_at,
    }
    try:
        _atomic_write_update_request(request_payload)
        _launch_registered_auto_update_task()
    except Exception as exc:
        _remove_update_request_if_owned(attempt_id)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_db() as db:
            db.execute(
                """UPDATE app_update_attempts
                      SET status='failed', stage='launch_failed', error=?,
                          message='The elevated updater did not start.',
                          active_slot=NULL, finished_at=?, updated_at=?
                    WHERE attempt_id=?""",
                (_safe_update_error(exc), now, now, attempt_id),
            )
        raise

    launched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            """UPDATE app_update_attempts
                  SET status='launched', stage='external_task',
                      message='Elevated updater launched; waiting for restart.',
                      launched_at=?, updated_at=?
                WHERE attempt_id=?""",
            (launched_at, launched_at, attempt_id),
        )
        row = db.execute(
            "SELECT * FROM app_update_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
    return _update_attempt_dict(row)


def _automatic_retry_wait(target_commit: str) -> tuple[dict, datetime] | None:
    """Return the failed attempt that still holds back an automatic relaunch.

    A commit whose install failed, rolled back, could not launch or went
    stale would otherwise be relaunched on every watcher tick, stopping the
    services each time. The watcher waits the retry time from the Settings
    page (``_AUTO_UPDATE_RETRY_SECONDS`` unless changed) after
    the most recent attempt for the same commit ended; the result is that
    attempt and when the next automatic one is allowed. Manual updates and a
    different target commit are never held back.
    """
    with get_db() as db:
        row = db.execute(
            """SELECT * FROM app_update_attempts
                WHERE target_commit=? ORDER BY id DESC LIMIT 1""",
            (str(target_commit or "").strip().lower(),),
        ).fetchone()
    if row is None or row["status"] != "failed":
        return None
    attempt = dict(row)
    ended_raw = (
        attempt.get("finished_at") or attempt.get("updated_at") or attempt.get("created_at")
    )
    try:
        ended = datetime.fromisoformat(str(ended_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    retry_at = ended + timedelta(seconds=_auto_update_retry_seconds())
    if retry_at <= datetime.now(timezone.utc):
        return None
    return attempt, retry_at


def _scheduled_auto_update(*, force: bool = False) -> dict:
    """Check GitHub main and launch the exact-SHA updater when it changes."""
    if not _AUTO_UPDATE_RUN_LOCK.acquire(blocking=False):
        _set_auto_update_state(status="check_in_progress")
        return _auto_update_payload()
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        active_attempt, _ = _latest_update_attempts()
        if active_attempt:
            _release_update_drain()
            _set_auto_update_state(
                status="waiting_for_update_restart",
                last_checked_at=now,
                last_attempt_at=active_attempt.get("launched_at")
                or active_attempt.get("created_at"),
                last_attempt_commit=active_attempt.get("target_commit"),
                last_error=active_attempt.get("error"),
            )
            return _auto_update_payload()

        if not _auto_update_enabled():
            _release_update_drain()
            _set_auto_update_state(
                status="disabled",
                last_checked_at=now,
                last_error=None,
            )
            return _auto_update_payload()

        if (_CODE_DIR / ".git").exists():
            _release_update_drain()
            _set_auto_update_state(
                status="developer_checkout",
                last_checked_at=now,
                last_error=(
                    "Automatic installs do not overwrite a Git working copy; "
                    "use the setup.ps1 production install"
                ),
            )
            return _auto_update_payload()

        deployed = _deployed_commit()
        if not deployed:
            _release_update_drain()
            _set_auto_update_state(
                status="not_deployed",
                last_checked_at=now,
                last_error=(
                    "VERSION has no deployed commit stamp; run setup.ps1 once "
                    "before enabling automatic updates"
                ),
            )
            return _auto_update_payload()

        latest, error = _latest_commit(force=True)
        if error or not latest:
            _release_update_drain()
            _set_auto_update_state(
                status="check_failed",
                last_checked_at=now,
                last_error=_safe_update_error(error or "GitHub returned no main commit"),
            )
            return _auto_update_payload(latest_commit=latest, check_error=error)

        if _commits_match(deployed, latest):
            _release_update_drain()
            _set_auto_update_state(
                status="up_to_date",
                last_checked_at=now,
                last_error=None,
            )
            return _auto_update_payload(latest_commit=latest)

        retry_wait = _automatic_retry_wait(latest)
        if retry_wait is not None:
            failed_attempt, retry_at = retry_wait
            _release_update_drain()
            _set_auto_update_state(
                status="retry_wait",
                last_checked_at=now,
                last_attempt_at=failed_attempt.get("finished_at")
                or failed_attempt.get("launched_at")
                or failed_attempt.get("created_at"),
                last_attempt_commit=failed_attempt.get("target_commit"),
                last_error=_safe_update_error(
                    failed_attempt.get("error")
                    or failed_attempt.get("message")
                    or "The last install of this commit did not succeed."
                ),
                next_attempt_at=retry_at.isoformat(timespec="seconds"),
            )
            return _auto_update_payload(latest_commit=latest)

        # The watcher has one job: when GitHub main differs from the installed
        # commit, launch the existing elevated task. That task invokes the
        # same setup.ps1 operators already use manually.
        _release_update_drain()
        try:
            attempt = _reserve_and_launch_update(latest, "automatic")
        except Exception as exc:
            _release_update_drain()
            _set_auto_update_state(
                status="launch_failed",
                last_checked_at=now,
                last_attempt_at=now,
                last_attempt_commit=latest,
                last_attempt_monotonic=time.monotonic(),
                last_error=_safe_update_error(exc),
            )
            logging.getLogger("auto_update").error(
                "Automatic main update could not launch: %s",
                _safe_update_error(exc),
            )
        else:
            attempt_commit = attempt.get("target_commit") or latest
            _set_auto_update_state(
                status="update_launched",
                last_checked_at=now,
                last_attempt_at=attempt.get("launched_at") or now,
                last_attempt_commit=attempt_commit,
                last_attempt_monotonic=time.monotonic(),
                last_error=None,
            )
            logging.getLogger("auto_update").info(
                "New main commit %s detected; elevated update task launched",
                str(attempt_commit)[:12],
            )
        return _auto_update_payload(latest_commit=latest)
    finally:
        _AUTO_UPDATE_RUN_LOCK.release()


@app.get("/api/version")
def get_version():
    deployed = _deployed_commit()
    latest = None
    error = None
    if deployed:
        latest, error = _latest_commit()
    else:
        error = "VERSION carries no commit stamp; re-run setup.ps1 to enable the check"
    up_to_date = None
    if deployed and latest and not error:
        up_to_date = _commits_match(deployed, latest)
    return {
        "version": _APP_VERSION,
        "commit": deployed,
        "latest_commit": latest,
        "up_to_date": up_to_date,
        "update_check_error": error,
        "auto_update": _auto_update_payload(
            latest_commit=latest,
            check_error=error,
        ),
    }


def _system_update_status(*, force_check: bool = False) -> dict:
    """Build the operator-facing state shared by the System Updates page."""
    latest, error = _latest_commit(force=force_check)
    active_attempt, latest_attempt = _latest_update_attempts()
    with get_db() as db:
        active_work = _active_update_work(db)
    updater_ready, updater_error = _registered_auto_update_task_ready()
    deployed = _deployed_commit()
    up_to_date = _commits_match(deployed, latest) if deployed and latest and not error else None
    # CI is informational here. Rendering update controls must not wait for a
    # second network request (or its office-network fallback).
    with _TESTS_GATE_LOCK:
        cached_gate = _TESTS_GATE_CACHE.get(latest)
        tests_gate = dict(cached_gate[1]) if cached_gate and not error else _tests_gate_record(
            latest, "not_checked", "CI status is available on GitHub.", error=error
        )
    auto_update = _auto_update_payload(
        latest_commit=latest,
        check_error=error,
    )
    return {
        "version": _APP_VERSION,
        "current_commit": deployed,
        "latest_commit": latest,
        "up_to_date": up_to_date,
        "update_check_error": _safe_update_error(error) if error else None,
        "auto_update": auto_update,
        "updater_ready": updater_ready,
        "updater_error": updater_error,
        "active_work": active_work,
        "active_attempt": active_attempt,
        "latest_attempt": latest_attempt,
        "tests_gate": tests_gate,
        "can_apply": bool(
            updater_ready
            and deployed
            and not active_attempt
        ),
    }


# ── Multi-user identity endpoints ──

class RegisterRequest(BaseModel):
    person_id: int = Field(ge=1, strict=True)
    client_key: str | None = None


class RefreshScheduleRequest(BaseModel):
    refresh_time: str


class AutoUpdateSettingsRequest(BaseModel):
    enabled: bool


class RemoteFlowControlSettingsRequest(BaseModel):
    enabled: bool


def _identity_payload(request: Request, identity: dict | None, client_key: str | None) -> dict:
    ip = request.state.client_ip
    return {
        "ip": ip,
        "client_key": client_key,
        "hostname": user_identity.resolve_hostname(ip),
        "person_id": identity["person_id"] if identity else None,
        "name": identity["name"] if identity else None,
        "is_local": request.state.is_local,
        "is_host": request.state.is_host,
        "is_admin": request.state.is_admin,
    }


@app.get("/api/me")
def get_me(request: Request):
    """Return the person this browser chose in Users, and host-PC status."""
    identity = request.state.identity
    payload = _identity_payload(request, identity, request.state.client_key)
    if identity:
        user_identity.mark_seen(payload["ip"], payload["client_key"], identity, payload["hostname"])
    return payload


def _parse_refresh_time(value: str) -> tuple[int, int]:
    raw = (value or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="refresh_time must be HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="refresh_time must be HH:MM") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise HTTPException(status_code=400, detail="refresh_time must be HH:MM")
    return hour, minute


def _refresh_schedule_payload() -> dict:
    refresh_time = get_overall_refresh_time()
    job = _scheduler.get_job("daily_overall_refresh")
    next_run = getattr(job, "next_run_time", None) if job else None
    return {
        "refresh_time": refresh_time["time"],
        "hour": refresh_time["hour"],
        "minute": refresh_time["minute"],
        "next_run_at": next_run.isoformat() if next_run else None,
        "scheduler_running": bool(getattr(_scheduler, "running", False)),
        "timezone": str(host_timezone()),
    }


@app.get("/api/admin/refresh-schedule", include_in_schema=False)
@app.get("/api/system/refresh-schedule")
def get_refresh_schedule(request: Request):
    """Return the configurable overall refresh schedule."""
    require_app_access(request)
    return _refresh_schedule_payload()


@app.put("/api/admin/refresh-schedule", include_in_schema=False)
@app.put("/api/system/refresh-schedule")
def update_refresh_schedule(body: RefreshScheduleRequest, request: Request):
    """Persist and reschedule the daily overall refresh time."""
    require_host(request, "Only the host PC can change the daily refresh time.")
    hour, minute = _parse_refresh_time(body.refresh_time)
    try:
        saved = set_overall_refresh_time(hour, minute)
    except sqlite3.OperationalError as exc:
        logging.getLogger(__name__).exception("Could not save refresh schedule")
        raise HTTPException(status_code=503, detail=f"Could not save refresh schedule: {exc}") from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Could not save refresh schedule")
        raise HTTPException(status_code=500, detail=f"Could not save refresh schedule: {exc}") from exc

    reschedule_error = None
    try:
        _configure_overall_refresh_job()
    except Exception as exc:
        reschedule_error = str(exc)
        logging.getLogger(__name__).exception("Could not reschedule overall refresh job")

    try:
        from app.routers.eventlog import log_event

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA busy_timeout = 15000")
            log_event(
                conn,
                "app_settings",
                None,
                "Overall refresh",
                "updated",
                saved["time"],
                getattr(request.state, "actor", None),
            )
            conn.commit()
    except Exception:
        logging.getLogger(__name__).exception("Could not log refresh schedule update")
    try:
        payload = _refresh_schedule_payload()
    except Exception:
        logging.getLogger(__name__).exception("Could not build refresh schedule payload")
        payload = {
            "refresh_time": saved["time"],
            "hour": saved["hour"],
            "minute": saved["minute"],
            "next_run_at": None,
            "scheduler_running": bool(getattr(_scheduler, "running", False)),
        }
    if reschedule_error:
        payload["reschedule_error"] = reschedule_error
    return payload


@app.post("/api/admin/refresh-now", include_in_schema=False)
@app.post("/api/system/refresh-now")
def run_refresh_now(request: Request):
    """Queue a one-off overall refresh for immediate testing."""
    from app.scanner.pbi_sync import stop_pbi_sync_processes

    require_app_access(request)
    if not getattr(_scheduler, "running", False):
        raise HTTPException(status_code=503, detail="Scheduler is not running")
    stop_result = stop_pbi_sync_processes("Manual refresh now requested.")
    removed_jobs = []
    for job in list(_scheduler.get_jobs()):
        if job.id.startswith("manual_overall_refresh_"):
            _scheduler.remove_job(job.id)
            removed_jobs.append(job.id)
    run_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    job_id = f"manual_overall_refresh_{int(run_at.timestamp())}"
    _scheduler.add_job(
        _scheduled_overall_refresh,
        "date",
        run_date=run_at,
        id=job_id,
        replace_existing=True,
    )
    return {
        "status": "queued",
        "job_id": job_id,
        "run_at": run_at.isoformat(),
        "removed_jobs": removed_jobs,
        "stop": stop_result,
    }


@app.post("/api/register")
def register_user(body: RegisterRequest, request: Request):
    """Sign this browser in as a person chosen from Users.

    A computer chooses once. Moving a signed-in computer to someone else is a
    host-PC action (Users > Sign out), so nobody can take over another
    person's Flows by picking their name.
    """
    ip = request.state.client_ip
    client_key = user_identity.clean_client_key(body.client_key) or request.state.client_key
    with get_db() as db:
        person = db.execute("SELECT id, name FROM people WHERE id = ?", (body.person_id,)).fetchone()
    if not person:
        raise HTTPException(status_code=404, detail="That user no longer exists. Choose another user.")
    current = user_identity.resolve(ip, client_key)
    if current and current["person_id"] != person["id"] and not request.state.is_host:
        raise HTTPException(
            status_code=409,
            detail=f"This computer is already signed in as {current['name']}. "
                   "Ask the host PC to sign it out in Users first.",
        )
    hostname = user_identity.resolve_hostname(ip)
    user_identity.bind(ip, client_key, hostname, person["id"], person["name"])
    identity = {"person_id": person["id"], "name": person["name"]}
    return _identity_payload(request, identity, client_key)


@app.get("/api/system/updates")
def get_system_updates(request: Request):
    """Return automatic-main watcher, workload, and updater state."""
    require_app_access(request)
    return _system_update_status()


def _require_local_remote_control(request: Request) -> None:
    """Keep the enable switch and browser hand-off on the server machine.

    The peer address must be this machine's. That alone is not enough: a page
    from another site, open in a browser on the host PC, also arrives from
    this machine, so a request the identity middleware judged not to be the
    host's own is refused as well.
    """
    ip = request.client.host if request.client else ""
    judged = getattr(getattr(request, "state", None), "is_host", None)
    if not _is_localhost(ip) or judged is False:
        raise HTTPException(status_code=403, detail="Remote Flow control is local-only.")


@app.get("/api/system/remote-flow-control")
def get_remote_flow_control(request: Request):
    _require_local_remote_control(request)
    return remote_flow_control.status_payload()


@app.put("/api/system/remote-flow-control")
def set_remote_flow_control(
    payload: RemoteFlowControlSettingsRequest, request: Request
):
    _require_local_remote_control(request)
    return remote_flow_control.set_enabled(payload.enabled)


@app.get("/api/system/remote-flow-control/navigation")
def get_remote_flow_navigation(request: Request):
    _require_local_remote_control(request)
    return {"intent": remote_flow_control.pending_navigation()}


@app.post("/api/system/remote-flow-control/navigation/{command_id}/ack")
def acknowledge_remote_flow_navigation(command_id: str, request: Request):
    _require_local_remote_control(request)
    if not remote_flow_control.acknowledge_navigation(command_id):
        raise HTTPException(status_code=404, detail="Navigation intent not found.")
    return {"acknowledged": True}


@app.put("/api/system/updates")
def set_system_updates(payload: AutoUpdateSettingsRequest, request: Request):
    """Enable or disable unattended installation of new main commits."""
    require_host(request, "Only the host PC can turn automatic updates on or off.")
    set_setting(_AUTO_UPDATE_SETTING_KEY, "1" if payload.enabled else "0")
    active_attempt, _ = _latest_update_attempts()
    _release_update_drain()
    _set_auto_update_state(
        status=(
            "waiting_for_update_restart"
            if active_attempt
            else "enabled" if payload.enabled else "disabled"
        ),
        last_error=active_attempt.get("error") if active_attempt else None,
    )
    return _system_update_status()


@app.post("/api/system/updates/check")
def check_system_updates(request: Request):
    """Force a fresh main lookup without installing it from this request."""
    require_app_access(request)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest, error = _latest_commit(force=True)
    deployed = _deployed_commit()
    if error or not latest:
        _set_auto_update_state(
            status="check_failed",
            last_checked_at=now,
            last_error=_safe_update_error(error or "GitHub returned no main commit"),
        )
    elif not deployed:
        _set_auto_update_state(
            status="not_deployed",
            last_checked_at=now,
            last_error="Run setup.ps1 once before using automatic updates",
        )
    elif _commits_match(deployed, latest):
        _set_auto_update_state(
            status="up_to_date",
            last_checked_at=now,
            last_error=None,
        )
    else:
        _set_auto_update_state(
            status="update_available",
            last_checked_at=now,
            last_error=None,
        )
    return _system_update_status()


def _apply_latest_update() -> dict:
    latest, error = _latest_commit(force=True)
    if error or not latest:
        raise HTTPException(
            status_code=502,
            detail=_safe_update_error(error or "GitHub returned no main commit"),
        )
    deployed = _deployed_commit()
    if not deployed:
        raise HTTPException(
            status_code=409,
            detail="Run setup.ps1 once before using unattended updates.",
        )
    # Manual update is also the repair path: run setup even for the same SHA.
    # The automatic watcher still skips an already-installed commit.

    active_attempt, _ = _latest_update_attempts()
    if active_attempt:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "An update is already in progress.",
                "attempt": active_attempt,
            },
        )

    _release_update_drain()
    updater_ready, updater_error = _registered_auto_update_task_ready()
    if not updater_ready:
        _release_update_drain()
        raise HTTPException(status_code=503, detail=updater_error)

    try:
        attempt = _reserve_and_launch_update(latest, "manual")
    except Exception as exc:
        _release_update_drain()
        raise HTTPException(status_code=503, detail=_safe_update_error(exc)) from exc
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _set_auto_update_state(
        status="update_launched",
        last_checked_at=now,
        last_attempt_at=attempt.get("launched_at") or now,
        last_attempt_commit=attempt.get("target_commit") or latest,
        last_attempt_monotonic=time.monotonic(),
        last_error=None,
    )
    return {
        "status": "launched",
        "current_commit": deployed,
        "latest_commit": latest,
        "attempt": attempt,
    }


@app.post("/api/system/updates/apply", status_code=202)
def apply_system_update(request: Request):
    """Launch setup.ps1 for the latest exact main commit."""
    require_host(request, "Only the host PC can install an update.")
    return _apply_latest_update()


@app.post("/api/update", status_code=202)
def trigger_update(request: Request):
    """Backward-compatible alias for the safe System Updates action."""
    require_host(request, "Only the host PC can install an update.")
    return _apply_latest_update()


@app.get("/")
def serve_panel():
    """Serve the main panel page."""
    return _serve_index()


@app.get("/flow-runs/{run_id}")
def serve_flow_run_log(run_id: int):
    """Serve the dedicated full-page diagnostic view for one flow run."""
    return HTMLResponse(
        content=(static_dir / "flow_run_log.html").read_text(),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/{path:path}")
def spa_catch_all(path: str):
    """Catch-all route for SPA - serve index.html for non-API, non-static paths."""
    if path.startswith("api/") or path.startswith("static/"):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return _serve_index()
