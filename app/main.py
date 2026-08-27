import logging
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.config import DB_PATH, UPLOAD_PGHOST
from app.database import get_db, init_db
from app.local_access import is_server_machine, require_app_access
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, data_quality, tasks, eventlog, people, archive, documentation, email, email_schedules, usage, data_import, recurrences, flows, query_history, pipelines
from app.settings import get_overall_refresh_time, set_overall_refresh_time
from app.source_identity import reconcile_all_flow_targets
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


_scheduler = BackgroundScheduler()


def _scheduled_backup():
    """Daily 6 AM backup of governance.db."""
    from app.scanner.runner import _backup_db
    log = logging.getLogger("scheduler")
    log.info("Running scheduled backup")
    _backup_db()
    log.info("Scheduled backup complete")


def _scheduled_scan(cancel_generation: int | None = None, stop_existing: bool = True):
    """Run a full report scan and source probe."""
    from app.scanner.runner import run_scan
    from app.scanner.prober import run_probe
    from app.scanner.control import ScannerWorkCancelled
    from app.scanner.pbi_sync import stop_pbi_sync_processes
    log = logging.getLogger("scheduler")
    log.info("Running scheduled full scan")
    stop_result = None
    generation = cancel_generation
    if stop_existing:
        stop_result = stop_pbi_sync_processes("New scheduled scan started.")
        generation = (stop_result.get("scanner") or {}).get("generation")
    try:
        result = run_scan(cancel_generation=generation, run_followup_probe=False)
        log.info("Scan result: %s", result.get("status"))
        if result.get("status") == "stopped":
            return {**result, "stop": stop_result}
        probe_result = run_probe(cancel_generation=generation)
        log.info("Probe result: %s", probe_result.get("statuses"))
        return {**result, "probe": probe_result, "stop": stop_result}
    except ScannerWorkCancelled as e:
        log.info("Scheduled scan stopped: %s", e)
        return {"status": "stopped", "message": str(e), "stop": stop_result}
    except Exception as e:
        log.exception("Scheduled scan failed: %s", e)
        return {"status": "failed", "error": str(e), "stop": stop_result}


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


def _scheduled_overall_refresh():
    """Daily overall refresh: report scan, source probe, then Power BI sync."""
    from app.scanner.control import ScannerWorkCancelled
    from app.scanner.pbi_sync import stop_pbi_sync_processes
    log = logging.getLogger("scheduler")
    log.info("Running scheduled overall refresh")
    stop_result = stop_pbi_sync_processes("New scheduled overall refresh started.")
    generation = (stop_result.get("scanner") or {}).get("generation")
    try:
        pbi_result = _scheduled_pbi_sync(
            cancel_generation=generation,
            stop_existing=False,
            wait=True,
        )
        if (pbi_result.get("status") or "").lower() not in {"completed", "skipped"}:
            log.warning("Scheduled overall refresh stopped before scan because PBI sync result was %s", pbi_result.get("status"))
            return {"status": "pbi_sync_not_completed", "pbi_sync": pbi_result, "stop": stop_result}
        scan_result = _scheduled_scan(cancel_generation=generation, stop_existing=False)
        usage_result = None
        if scan_result.get("status") == "completed":
            try:
                from app.scanner.pbi_sync import trigger_pbi_usage_sync

                usage_result = trigger_pbi_usage_sync(
                    cancel_existing=False,
                    cancel_generation=generation,
                )
            except Exception as exc:
                usage_result = {"status": "failed", "error": str(exc)}
                log.exception("Scheduled Power BI usage sync failed to start: %s", exc)
        log.info("Scheduled overall refresh complete")
        return {
            "status": scan_result.get("status"),
            "pbi_sync": pbi_result,
            "scan": scan_result,
            "pbi_usage_sync": usage_result,
            "stop": stop_result,
        }
    except ScannerWorkCancelled as e:
        log.info("Scheduled overall refresh stopped: %s", e)
        return {"status": "stopped", "message": str(e), "stop": stop_result}


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
        flows.queue_due_flows,
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
        flows.queue_due_catalog_scans,
        "interval",
        minutes=15,
        id="flow_catalog_scan_dispatch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        flows.refresh_sql_catalog,
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
        return reconcile_all_flow_targets(db, server=UPLOAD_PGHOST)


@asynccontextmanager
async def lifespan(app):
    logging.getLogger(__name__).info("Database path: %s", DB_PATH)
    init_db()
    reconciliation = _reconcile_startup_flow_targets()
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
    refresh_time = _configure_scheduler_jobs()
    _scheduler.start()
    flows.ensure_local_worker()
    logging.getLogger(__name__).info(
        "Scheduler started: backup at 06:00, overall refresh at %02d:%02d, email dispatch every minute",
        refresh_time["hour"],
        refresh_time["minute"],
    )

    yield

    _scheduler.shutdown(wait=False)
    pipelines.shutdown_pipeline_executor()


app = FastAPI(title="Metronome", version="0.1.0", lifespan=lifespan)
app.add_middleware(NoCacheStaticMiddleware)
app.add_middleware(UserIdentityMiddleware)

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
app.include_router(data_import.router)
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

#: Latest-commit lookup for the "am I on the latest version?" badge. Cached so
#: the UI polling the version never hammers GitHub (unauthenticated rate limit
#: is 60 requests/hour).
_UPDATE_CHECK_TTL_SECONDS = 15 * 60
_UPDATE_CHECK_LOCK = threading.Lock()
_UPDATE_CHECK: dict = {"checked_at": 0.0, "latest_commit": None, "error": None}
_LATEST_COMMIT_URL = (
    "https://api.github.com/repos/datap0nd/data_governance/commits/main"
)


def _deployed_commit() -> str | None:
    """The commit SHA setup.ps1 stamps into VERSION ("<timestamp>-<sha>")."""
    token = _APP_VERSION.rsplit("-", 1)[-1].strip().lower()
    if re.fullmatch(r"[0-9a-f]{7,40}", token) and not token.isdigit():
        return token
    return None


def _fetch_latest_commit() -> str:
    import httpx

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Metronome"}
    token = os.environ.get("DG_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(_LATEST_COMMIT_URL, headers=headers, timeout=5)
    response.raise_for_status()
    return str(response.json()["sha"]).lower()


def _latest_commit() -> tuple[str | None, str | None]:
    """(latest main commit, error), refreshed at most every 15 minutes."""
    with _UPDATE_CHECK_LOCK:
        if time.time() - _UPDATE_CHECK["checked_at"] < _UPDATE_CHECK_TTL_SECONDS:
            return _UPDATE_CHECK["latest_commit"], _UPDATE_CHECK["error"]
        try:
            _UPDATE_CHECK["latest_commit"] = _fetch_latest_commit()
            _UPDATE_CHECK["error"] = None
        except Exception as exc:
            _UPDATE_CHECK["error"] = f"{type(exc).__name__}: {exc}"
        _UPDATE_CHECK["checked_at"] = time.time()
        return _UPDATE_CHECK["latest_commit"], _UPDATE_CHECK["error"]


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
        up_to_date = latest.startswith(deployed) or deployed.startswith(latest)
    return {
        "version": _APP_VERSION,
        "commit": deployed,
        "latest_commit": latest,
        "up_to_date": up_to_date,
        "update_check_error": error,
    }


# ── Multi-user identity endpoints ──

class RegisterRequest(BaseModel):
    name: str
    client_key: str | None = None


class RefreshScheduleRequest(BaseModel):
    refresh_time: str


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


@app.post("/api/update")
def trigger_update(request: Request):
    """Launch setup.ps1 to update the app."""
    require_app_access(request)
    setup_path = Path(__file__).parent.parent / "setup.ps1"
    if not setup_path.exists():
        raise HTTPException(status_code=404, detail="setup.ps1 not found")
    # Launch via schtasks so it runs in the logged-in user's interactive session
    # (the NSSM service runs in session 0 which is non-interactive)
    task_name = "DG_Update"
    ps_cmd = f'powershell.exe -ExecutionPolicy Bypass -NoExit -File "{setup_path}"'
    try:
        subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"],
                       capture_output=True, timeout=10)
        subprocess.run(["schtasks", "/create", "/tn", task_name, "/tr", ps_cmd,
                        "/sc", "once", "/st", "00:00", "/it", "/f"],
                       capture_output=True, text=True, timeout=10, check=True)
        subprocess.run(["schtasks", "/run", "/tn", task_name],
                       capture_output=True, text=True, timeout=10, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch update: {e.stderr or e}")
    return {"status": "launched"}


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
