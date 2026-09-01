import json
import logging
import os
import re
import socket
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
from pydantic import BaseModel
from urllib.parse import urlencode

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.config import DB_PATH, UPLOAD_PGHOST, UPLOAD_PGPORT
from app.database import get_db, init_db
from app.local_access import is_server_machine, require_app_access
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, data_quality, tasks, eventlog, people, archive, documentation, email, email_schedules, usage, materialized_views, recurrences, flows, query_history, pipelines
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
from app.scanner.pbi_auth import resolve_proxy
from app.ai.router import router as ai_router

# Show scanner logs in the console
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

# In-memory caches for identity resolution (cleared on register)
_identity_cache: dict[tuple[str, str | None], str | None] = {}
_hostname_cache: dict[str, str | None] = {}


def _ensure_identity_schema(conn: sqlite3.Connection):
    """Create or repair the lightweight user identity tables."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_ips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address  TEXT NOT NULL UNIQUE,
            person_name TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for stmt in (
        "ALTER TABLE user_ips ADD COLUMN hostname TEXT",
        "ALTER TABLE user_ips ADD COLUMN client_key TEXT",
        "ALTER TABLE user_ips ADD COLUMN last_seen_at DATETIME",
        "ALTER TABLE user_ips ADD COLUMN updated_at DATETIME",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_devices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key      TEXT NOT NULL UNIQUE,
            person_name     TEXT NOT NULL,
            last_ip_address TEXT,
            hostname        TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_devices_client_key ON user_devices(client_key)")


def _clean_client_key(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if len(value) > 128:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        return None
    return value


def _resolve_hostname(ip: str) -> str | None:
    """Best-effort reverse DNS lookup for LAN hostnames."""
    if ip in _hostname_cache:
        return _hostname_cache[ip]
    try:
        host = socket.gethostbyaddr(ip)[0]
    except Exception:
        host = None
    _hostname_cache[ip] = host
    return host


def _resolve_identity(ip: str, client_key: str | None = None) -> str | None:
    """Look up person_name by browser/device key first, then IP address."""
    cache_key = (ip, client_key)
    if cache_key in _identity_cache:
        return _identity_cache[cache_key]
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_identity_schema(conn)
        row = None
        if client_key:
            row = conn.execute(
                "SELECT person_name FROM user_devices WHERE client_key = ?",
                (client_key,),
            ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT person_name FROM user_ips WHERE ip_address = ?", (ip,)
            ).fetchone()
        conn.close()
        name = row["person_name"] if row else None
        _identity_cache[cache_key] = name
        return name
    except Exception:
        return None


def _mark_identity_seen(ip: str, client_key: str | None, name: str | None, hostname: str | None):
    """Update last-seen metadata without changing an existing person's name."""
    if not name:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        _ensure_identity_schema(conn)
        existing_ip = conn.execute("SELECT id FROM user_ips WHERE ip_address = ? LIMIT 1", (ip,)).fetchone()
        if existing_ip:
            conn.execute(
                """UPDATE user_ips
                   SET person_name = ?,
                       hostname = COALESCE(?, hostname),
                       client_key = COALESCE(?, client_key),
                       updated_at = ?,
                       last_seen_at = ?
                   WHERE id = ?""",
                (name, hostname, client_key, now, now, existing_ip[0]),
            )
        else:
            conn.execute(
                """INSERT INTO user_ips
                   (ip_address, person_name, hostname, client_key, created_at, updated_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ip, name, hostname, client_key, now, now, now),
            )
        if client_key:
            existing_device = conn.execute("SELECT id FROM user_devices WHERE client_key = ? LIMIT 1", (client_key,)).fetchone()
            if existing_device:
                conn.execute(
                    """UPDATE user_devices
                       SET last_ip_address = ?,
                           hostname = COALESCE(?, hostname),
                           last_seen_at = ?
                       WHERE id = ?""",
                    (ip, hostname, now, existing_device[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO user_devices
                       (client_key, person_name, last_ip_address, hostname, created_at, updated_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (client_key, name, ip, hostname, now, now, now),
                )
        conn.commit()
    finally:
        conn.close()


def _is_localhost(ip: str) -> bool:
    """Check if a request came from the machine running the app."""
    return is_server_machine(ip)


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Resolve client IP to user identity on every request."""
    async def dispatch(self, request: StarletteRequest, call_next):
        ip = request.client.host if request.client else "unknown"
        client_key = _clean_client_key(
            request.headers.get("x-client-key") or request.cookies.get("dg_client_key")
        )
        request.state.client_ip = ip
        request.state.client_key = client_key
        request.state.is_local = _is_localhost(ip)
        request.state.is_admin = True
        request.state.actor = _resolve_identity(ip, client_key)
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
    re.compile(r"^/api/flows/runs/\d+/(?:retry-sql|resume)$"),
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
_OVERALL_REFRESH_RETRY_MINUTES = 5


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


@_tracked_scheduled_start("overall refresh")
def _scheduled_overall_refresh():
    """Submit the daily overall refresh through the durable scanner lane."""
    from app.routers.scanner import start_scheduled_full_scan_job

    log = logging.getLogger("scheduler")
    log.info("Running scheduled overall refresh")
    result = start_scheduled_full_scan_job()
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
                minutes=_OVERALL_REFRESH_RETRY_MINUTES
            )
            _scheduler.add_job(
                _scheduled_overall_refresh,
                "date",
                run_date=retry_at,
                id=_OVERALL_REFRESH_RETRY_JOB_ID,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            result = dict(result)
            result["retry_scheduled_for"] = retry_at.isoformat(timespec="seconds")
            log.info(
                "Scanner lane busy; scheduled overall refresh will retry at %s",
                result["retry_scheduled_for"],
            )
    else:
        # A prior collision may have left a date retry pending. Once the full
        # scheduled run is accepted, that extra retry would be duplicate work.
        retry_job = _scheduler.get_job(_OVERALL_REFRESH_RETRY_JOB_ID)
        if retry_job is not None:
            _scheduler.remove_job(_OVERALL_REFRESH_RETRY_JOB_ID)
    log.info("Scheduled overall refresh submission: %s", result.get("status"))
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
        id="daily_overall_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return refresh_time


def _configure_scheduler_jobs() -> dict:
    _scheduler.add_job(_scheduled_backup, "cron", hour=6, minute=0, id="daily_backup", replace_existing=True)
    refresh_time = _configure_overall_refresh_job()
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
    _scheduler.add_job(
        _scheduled_auto_update,
        "interval",
        seconds=_AUTO_UPDATE_INTERVAL_SECONDS,
        id="automatic_main_update",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=60),
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
        hour=5,
        minute=30,
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


@asynccontextmanager
async def lifespan(app):
    logging.getLogger(__name__).info("Database path: %s", DB_PATH)
    init_db()
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
        "AI runtime: mode=%s model=%s operations=%s alert_auto=%s email=%s docs=%s",
        ai_settings.mode,
        ai_settings.model,
        ai_settings.feature_enabled("operations_investigator"),
        ai_settings.feature_enabled("automatic_alert_review"),
        ai_settings.feature_enabled("alert_email_analysis"),
        ai_settings.feature_enabled("documentation_suggestions")
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
            "Scheduler started: backup at 06:00, overall refresh at %02d:%02d, "
            "email dispatch every minute, main update check every %d minutes (enabled=%s)",
            refresh_time["hour"],
            refresh_time["minute"],
            _AUTO_UPDATE_INTERVAL_SECONDS // 60,
            _auto_update_enabled(),
        )
    else:
        logging.getLogger(__name__).error(
            "Metronome web is available, but background scheduling did not start"
        )

    yield

    if getattr(_scheduler, "running", False):
        _scheduler.shutdown(wait=False)
    scanner.shutdown_scanner_executor()
    from app.ai.operations_agent import shutdown_executor as shutdown_ai_executor
    shutdown_ai_executor()
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
app.include_router(changelog.router)
app.include_router(schedules.router)
app.include_router(create.router)
app.include_router(best_practices.router)
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
app.include_router(flows.router)
app.include_router(pipelines.router)

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
#: Five-minute checks stay comfortably below GitHub's unauthenticated limit
#: while keeping office installs close to main.
_UPDATE_CHECK_TTL_SECONDS = 5 * 60
_UPDATE_CHECK_LOCK = threading.Lock()
_UPDATE_CHECK: dict = {"checked_at": 0.0, "latest_commit": None, "error": None}
_LATEST_COMMIT_URL = (
    "https://api.github.com/repos/datap0nd/data_governance/commits/main"
)
_TESTS_WORKFLOW_RUNS_URL = (
    "https://api.github.com/repos/datap0nd/data_governance/"
    "actions/workflows/tests.yml/runs"
)
_TESTS_WORKFLOW_PATH = ".github/workflows/tests.yml"
_TESTS_GATE_TTL_SECONDS = 60
_TESTS_GATE_LOCK = threading.Lock()
_TESTS_GATE_CACHE: dict[str, tuple[float, dict]] = {}
_AUTO_UPDATE_SETTING_KEY = "automatic_main_updates_enabled"
_AUTO_UPDATE_INTERVAL_SECONDS = 5 * 60
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
}


def _github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Metronome",
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
    payload = _github_api_json(_LATEST_COMMIT_URL)
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid main commit response")
    commit = str(payload.get("sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("GitHub returned an invalid main commit identifier")
    return commit


def _latest_commit(*, force: bool = False) -> tuple[str | None, str | None]:
    """Return ``(latest main commit, error)`` using the shared short cache."""
    with _UPDATE_CHECK_LOCK:
        if (
            not force
            and time.time() - _UPDATE_CHECK["checked_at"] < _UPDATE_CHECK_TTL_SECONDS
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


def _fetch_tests_workflow_gate(target_commit: str) -> dict:
    """Return GitHub's Tests result for one exact main-branch commit."""
    target = str(target_commit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", target):
        raise RuntimeError("Tests can only be verified for a full Git commit SHA")
    payload = _github_api_json(
        _TESTS_WORKFLOW_RUNS_URL,
        params={
            "head_sha": target,
            "branch": "main",
            "event": "push",
            "per_page": 10,
        },
    )
    if not isinstance(payload, dict) or not isinstance(
        payload.get("workflow_runs"), list
    ):
        raise RuntimeError("GitHub returned an invalid Tests workflow response")

    matching_runs: list[dict] = []
    for candidate in payload["workflow_runs"]:
        if not isinstance(candidate, dict):
            continue
        workflow_path = str(candidate.get("path") or "").split("@", 1)[0]
        if (
            str(candidate.get("head_sha") or "").strip().lower() != target
            or str(candidate.get("head_branch") or "").strip() != "main"
            or str(candidate.get("event") or "").strip() != "push"
            or workflow_path != _TESTS_WORKFLOW_PATH
        ):
            continue
        matching_runs.append(candidate)

    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not matching_runs:
        return _tests_gate_record(
            target,
            "pending",
            "The Tests workflow has not started for this main commit yet.",
            checked_at=checked_at,
        )

    def run_order(run: dict) -> tuple[int, int, int]:
        values = []
        for key in ("run_number", "run_attempt", "id"):
            try:
                values.append(int(run.get(key) or 0))
            except (TypeError, ValueError):
                values.append(0)
        return tuple(values)

    run = max(matching_runs, key=run_order)
    status = str(run.get("status") or "").strip().casefold()
    conclusion = str(run.get("conclusion") or "").strip().casefold() or None
    normalized_run = dict(run)
    normalized_run["status"] = status or None
    normalized_run["conclusion"] = conclusion
    if status != "completed":
        return _tests_gate_record(
            target,
            "pending",
            "The Tests workflow is still running for this main commit.",
            checked_at=checked_at,
            run=normalized_run,
        )
    if conclusion == "success":
        return _tests_gate_record(
            target,
            "passed",
            "The exact main commit passed the Tests workflow.",
            checked_at=checked_at,
            run=normalized_run,
        )
    return _tests_gate_record(
        target,
        "failed",
        f"The Tests workflow finished with {conclusion or 'no conclusion'}.",
        checked_at=checked_at,
        run=normalized_run,
    )


def _tests_gate(target_commit: str, *, force: bool = False) -> dict:
    """Return a short-lived, exact-SHA-keyed workflow gate result."""
    target = str(target_commit or "").strip().lower()
    now = time.monotonic()
    with _TESTS_GATE_LOCK:
        cached = _TESTS_GATE_CACHE.get(target)
        if (
            not force
            and cached
            and now - cached[0] < _TESTS_GATE_TTL_SECONDS
        ):
            return dict(cached[1])
        try:
            result = _fetch_tests_workflow_gate(target)
        except Exception as exc:
            if cached and cached[1].get("state") == "passed":
                # A transient office-network failure cannot invalidate a
                # successful result already observed for this immutable SHA.
                result = dict(cached[1])
                result["message"] = (
                    "This exact main commit previously passed Tests. The latest "
                    "GitHub status refresh timed out, so the verified result was reused."
                )
                result["verification_warning"] = _safe_update_error(
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                result = _tests_gate_record(
                    target or None,
                    "unavailable",
                    "Metronome could not verify the Tests workflow; installation is blocked.",
                    checked_at=checked_at,
                    error=f"{type(exc).__name__}: {exc}",
                )
        _TESTS_GATE_CACHE[target] = (now, result)
        if len(_TESTS_GATE_CACHE) > 8:
            prior_targets = [key for key in _TESTS_GATE_CACHE if key != target]
            if prior_targets:
                oldest = min(
                    prior_targets,
                    key=lambda key: _TESTS_GATE_CACHE[key][0],
                )
                _TESTS_GATE_CACHE.pop(oldest, None)
        return dict(result)


def _tests_gate_for_status(
    deployed_commit: str | None,
    latest_commit: str | None,
    *,
    force: bool = False,
) -> dict:
    if not latest_commit:
        return _tests_gate_record(
            None,
            "not_checked",
            "A main commit must be found before Tests can be checked.",
        )
    if _commits_match(deployed_commit, latest_commit):
        return _tests_gate_record(
            latest_commit,
            "not_required",
            "The installed commit already matches GitHub main.",
        )
    return _tests_gate(latest_commit, force=force)


def _tests_gate_auto_status(gate: dict) -> str:
    return {
        "pending": "waiting_for_tests",
        "failed": "tests_failed",
        "unavailable": "tests_check_failed",
    }.get(str(gate.get("state") or ""), "tests_check_failed")


def _set_auto_update_state(**changes) -> None:
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
    deployed = _deployed_commit()
    latest = latest_commit or _UPDATE_CHECK.get("latest_commit")
    error = check_error if check_error is not None else _UPDATE_CHECK.get("error")
    enabled = _auto_update_enabled()
    return {
        "enabled": enabled,
        "branch": "main",
        "interval_minutes": _AUTO_UPDATE_INTERVAL_SECONDS // 60,
        "task_name": _AUTO_UPDATE_TASK_NAME,
        "draining": _AUTO_UPDATE_DRAIN_EVENT.is_set(),
        "deployed_commit": deployed,
        "latest_commit": latest,
        "update_available": (
            bool(deployed and latest) and not _commits_match(deployed, latest)
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

        latest, error = _latest_commit(force=force)
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
    if deployed and latest:
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
    up_to_date = _commits_match(deployed, latest) if deployed and latest else None
    tests_gate = (
        _tests_gate_record(
            latest,
            "not_checked",
            "The latest main commit could not be confirmed, so Tests were not checked.",
            error=error,
        )
        if error
        else _tests_gate_for_status(
            deployed,
            latest,
            force=force_check,
        )
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
            and latest
            and not error
            and not up_to_date
            and not active_attempt
        ),
    }


# ── Multi-user identity endpoints ──

class RegisterRequest(BaseModel):
    name: str
    client_key: str | None = None


class RefreshScheduleRequest(BaseModel):
    refresh_time: str


class AutoUpdateSettingsRequest(BaseModel):
    enabled: bool


@app.get("/api/me")
def get_me(request: Request):
    """Return the current user's identity based on IP."""
    ip = request.state.client_ip
    client_key = request.state.client_key
    hostname = _resolve_hostname(ip)
    name = request.state.actor
    if name:
        _mark_identity_seen(ip, client_key, name, hostname)
    return {
        "ip": ip,
        "client_key": client_key,
        "hostname": hostname,
        "name": name,
        "is_local": request.state.is_local,
        "is_admin": request.state.is_admin,
    }


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
    require_app_access(request)
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
    """Register or update the current IP's user identity."""
    ip = request.state.client_ip
    client_key = _clean_client_key(body.client_key) or request.state.client_key
    hostname = _resolve_hostname(ip)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        _ensure_identity_schema(conn)
        existing_ip = conn.execute("SELECT id FROM user_ips WHERE ip_address = ? LIMIT 1", (ip,)).fetchone()
        if existing_ip:
            conn.execute(
                """UPDATE user_ips
                   SET person_name = ?,
                       hostname = COALESCE(?, hostname),
                       client_key = COALESCE(?, client_key),
                       updated_at = CURRENT_TIMESTAMP,
                       last_seen_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (name, hostname, client_key, existing_ip[0]),
            )
        else:
            conn.execute(
                """INSERT INTO user_ips (ip_address, person_name, hostname, client_key, created_at, updated_at, last_seen_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (ip, name, hostname, client_key),
            )
        if client_key:
            existing_device = conn.execute("SELECT id FROM user_devices WHERE client_key = ? LIMIT 1", (client_key,)).fetchone()
            if existing_device:
                conn.execute(
                    """UPDATE user_devices
                       SET person_name = ?,
                           last_ip_address = ?,
                           hostname = COALESCE(?, hostname),
                           updated_at = CURRENT_TIMESTAMP,
                           last_seen_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (name, ip, hostname, existing_device[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO user_devices
                       (client_key, person_name, last_ip_address, hostname, created_at, updated_at, last_seen_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                    (client_key, name, ip, hostname),
                )
        conn.commit()
    finally:
        conn.close()

    # Clear identity cache entries that may include this IP or client key.
    for key in list(_identity_cache):
        if key[0] == ip or key[1] == client_key:
            _identity_cache.pop(key, None)

    return {
        "ip": ip,
        "client_key": client_key,
        "hostname": hostname,
        "name": name,
        "is_local": _is_localhost(ip),
        "is_admin": True,
    }


@app.get("/api/system/updates")
def get_system_updates(request: Request):
    """Return automatic-main watcher, workload, and updater state."""
    require_app_access(request)
    return _system_update_status()


@app.put("/api/system/updates")
def set_system_updates(payload: AutoUpdateSettingsRequest, request: Request):
    """Enable or disable unattended installation of new main commits."""
    require_app_access(request)
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
    if _commits_match(deployed, latest):
        _release_update_drain()
        return {
            "status": "up_to_date",
            "current_commit": deployed,
            "latest_commit": latest,
        }

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
    require_app_access(request)
    return _apply_latest_update()


@app.post("/api/update", status_code=202)
def trigger_update(request: Request):
    """Backward-compatible alias for the safe System Updates action."""
    require_app_access(request)
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
