import logging
import re
import socket
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, tasks, eventlog, people, scripts, scheduled_tasks, archive, power_automate, overview, custom_reports, documentation
from app.ai.router import router as ai_router

# Show scanner logs in the console
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

# In-memory caches for identity resolution (cleared on register)
_identity_cache: dict[tuple[str, str | None], str | None] = {}
_hostname_cache: dict[str, str | None] = {}


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
        conn.execute(
            """INSERT INTO user_ips
               (ip_address, person_name, hostname, client_key, created_at, updated_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ip_address) DO UPDATE SET
                   person_name = excluded.person_name,
                   hostname = COALESCE(excluded.hostname, user_ips.hostname),
                   client_key = COALESCE(excluded.client_key, user_ips.client_key),
                   updated_at = excluded.updated_at,
                   last_seen_at = excluded.last_seen_at""",
            (ip, name, hostname, client_key, now, now, now),
        )
        if client_key:
            conn.execute(
                """INSERT INTO user_devices
                   (client_key, person_name, last_ip_address, hostname, created_at, updated_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(client_key) DO UPDATE SET
                       last_ip_address = excluded.last_ip_address,
                       hostname = COALESCE(excluded.hostname, user_devices.hostname),
                       last_seen_at = excluded.last_seen_at""",
                (client_key, name, ip, hostname, now, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def _is_localhost(ip: str) -> bool:
    """Check if an IP is localhost (IPv4, IPv6, or IPv4-mapped IPv6)."""
    return ip in ("127.0.0.1", "::1") or ip.startswith("::ffff:127.0.0.1")


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Resolve client IP to user identity on every request."""
    async def dispatch(self, request: StarletteRequest, call_next):
        ip = request.client.host if request.client else "unknown"
        client_key = _clean_client_key(
            request.headers.get("x-client-key") or request.cookies.get("mx_client_key")
        )
        request.state.client_ip = ip
        request.state.client_key = client_key
        request.state.is_local = _is_localhost(ip)
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
    """Daily 7 AM full scan + probe."""
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


@asynccontextmanager
async def lifespan(app):
    logging.getLogger(__name__).info("Database path: %s", DB_PATH)
    init_db()

    # Daily backup at 6:00 AM, full scan at 7:00 AM
    _scheduler.add_job(_scheduled_backup, "cron", hour=6, minute=0, id="daily_backup")
    _scheduler.add_job(_scheduled_scan, "cron", hour=7, minute=0, id="daily_scan")
    _scheduler.start()
    logging.getLogger(__name__).info("Scheduler started: backup at 06:00, scan at 07:00")

    yield

    _scheduler.shutdown(wait=False)


app = FastAPI(title="MX Analytics", version="0.1.0", lifespan=lifespan)
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
        conn.execute(
            """INSERT INTO user_ips (ip_address, person_name, hostname, client_key, created_at, updated_at, last_seen_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(ip_address) DO UPDATE SET
                   person_name = excluded.person_name,
                   hostname = COALESCE(excluded.hostname, user_ips.hostname),
                   client_key = COALESCE(excluded.client_key, user_ips.client_key),
                   updated_at = CURRENT_TIMESTAMP,
                   last_seen_at = CURRENT_TIMESTAMP""",
            (ip, name, hostname, client_key),
        )
        if client_key:
            conn.execute(
                """INSERT INTO user_devices
                   (client_key, person_name, last_ip_address, hostname, created_at, updated_at, last_seen_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                   ON CONFLICT(client_key) DO UPDATE SET
                       person_name = excluded.person_name,
                       last_ip_address = excluded.last_ip_address,
                       hostname = COALESCE(excluded.hostname, user_devices.hostname),
                       updated_at = CURRENT_TIMESTAMP,
                       last_seen_at = CURRENT_TIMESTAMP""",
                (client_key, name, ip, hostname),
            )
        conn.commit()
    finally:
        conn.close()

    # Clear identity cache entries that may include this IP or client key.
    for key in list(_identity_cache):
        if key[0] == ip or key[1] == client_key:
            _identity_cache.pop(key, None)

    return {"ip": ip, "client_key": client_key, "hostname": hostname, "name": name, "is_local": _is_localhost(ip)}


@app.post("/api/update")
def trigger_update(request: Request):
    """Launch setup.ps1 to update the app. Localhost only."""
    ip = request.client.host if request.client else ""
    if not _is_localhost(ip):
        raise HTTPException(status_code=403, detail="Update restricted to server machine")
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
