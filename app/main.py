import logging
import re
import socket
import sqlite3
import subprocess
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

from app.config import DB_PATH
from app.database import init_db
from app.local_access import is_server_machine, require_app_access
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, tasks, eventlog, people, scripts, scheduled_tasks, archive, power_automate, overview, custom_reports, documentation, email, email_schedules, usage
from app.settings import get_overall_refresh_time, set_overall_refresh_time
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


def _scheduled_scan():
    """Run a full report scan and source probe."""
    from app.scanner.runner import run_scan
    from app.scanner.prober import run_probe
    log = logging.getLogger("scheduler")
    log.info("Running scheduled full scan")
    try:
        result = run_scan()
        log.info("Scan result: %s", result.get("status"))
        probe_result = run_probe()
        log.info("Probe result: %s", probe_result.get("statuses"))
    except Exception as e:
        log.exception("Scheduled scan failed: %s", e)


def _scheduled_pbi_sync():
    """Daily Power BI Service refresh metadata sync."""
    from app.scanner.pbi_sync import trigger_pbi_sync_or_defer
    log = logging.getLogger("scheduler")
    log.info("Running scheduled PBI sync")
    try:
        result = trigger_pbi_sync_or_defer("scheduled_overall_refresh")
        log.info("PBI sync result: %s", result.get("status"))
    except Exception as e:
        log.exception("Scheduled PBI sync failed: %s", e)


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
    log = logging.getLogger("scheduler")
    log.info("Running scheduled overall refresh")
    _scheduled_pbi_sync()
    _scheduled_scan()
    log.info("Scheduled overall refresh launched")


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


def _configure_overall_refresh_job() -> dict:
    refresh_time = get_overall_refresh_time()
    _scheduler.add_job(
        _scheduled_overall_refresh,
        "cron",
        hour=refresh_time["hour"],
        minute=refresh_time["minute"],
        id="daily_overall_refresh",
        replace_existing=True,
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
    return refresh_time


@asynccontextmanager
async def lifespan(app):
    logging.getLogger(__name__).info("Database path: %s", DB_PATH)
    init_db()

    # Daily backup plus a user-configurable overall refresh.
    refresh_time = _configure_scheduler_jobs()
    _scheduler.start()
    logging.getLogger(__name__).info(
        "Scheduler started: backup at 06:00, overall refresh at %02d:%02d, email dispatch every minute",
        refresh_time["hour"],
        refresh_time["minute"],
    )

    yield

    _scheduler.shutdown(wait=False)


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
app.include_router(ai_router)
app.include_router(changelog.router)
app.include_router(schedules.router)
app.include_router(create.router)
app.include_router(best_practices.router)
app.include_router(tasks.router)
app.include_router(eventlog.router)
app.include_router(people.router)
app.include_router(scripts.router)
app.include_router(scheduled_tasks.router)
app.include_router(archive.router)
app.include_router(power_automate.router)
app.include_router(overview.router)
app.include_router(custom_reports.router)
app.include_router(documentation.router)
app.include_router(email.router)
app.include_router(email_schedules.router)
app.include_router(usage.router)

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


@app.get("/api/version")
def get_version():
    return {"version": _APP_VERSION}


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
    require_app_access(request)
    if not getattr(_scheduler, "running", False):
        raise HTTPException(status_code=503, detail="Scheduler is not running")
    run_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    job_id = f"manual_overall_refresh_{int(run_at.timestamp())}"
    _scheduler.add_job(
        _scheduled_overall_refresh,
        "date",
        run_date=run_at,
        id=job_id,
        replace_existing=True,
    )
    return {"status": "queued", "job_id": job_id, "run_at": run_at.isoformat()}


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


@app.get("/{path:path}")
def spa_catch_all(path: str):
    """Catch-all route for SPA - serve index.html for non-API, non-static paths."""
    if path.startswith("api/") or path.startswith("static/"):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return _serve_index()
