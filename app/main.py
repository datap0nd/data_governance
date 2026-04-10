import logging
import re
import sqlite3
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.config import DB_PATH
from app.database import init_db
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, tasks, eventlog, people, scripts, scheduled_tasks, archive, power_automate, overview
from app.ai.router import router as ai_router

# Show scanner logs in the console
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

# In-memory cache for IP -> user resolution (cleared on register)
_ip_cache: dict[str, str | None] = {}


def _resolve_ip(ip: str) -> str | None:
    """Look up person_name for an IP address, with caching."""
    if ip in _ip_cache:
        return _ip_cache[ip]
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT person_name FROM user_ips WHERE ip_address = ?", (ip,)
        ).fetchone()
        conn.close()
        name = row["person_name"] if row else None
        _ip_cache[ip] = name
        return name
    except Exception:
        return None


def _is_localhost(ip: str) -> bool:
    """Check if an IP is localhost (IPv4, IPv6, or IPv4-mapped IPv6)."""
    return ip in ("127.0.0.1", "::1") or ip.startswith("::ffff:127.0.0.1")


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Resolve client IP to user identity on every request."""
    async def dispatch(self, request: StarletteRequest, call_next):
        ip = request.client.host if request.client else "unknown"
        request.state.client_ip = ip
        request.state.is_local = _is_localhost(ip)
        request.state.actor = _resolve_ip(ip)
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


@asynccontextmanager
async def lifespan(app):
    logging.getLogger(__name__).info("Database path: %s", DB_PATH)
    init_db()
    yield


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


@app.get("/api/me")
def get_me(request: Request):
    """Return the current user's identity based on IP."""
    ip = request.state.client_ip
    name = request.state.actor
    return {
        "ip": ip,
        "name": name,
        "is_local": request.state.is_local,
    }


@app.post("/api/register")
def register_user(body: RegisterRequest, request: Request):
    """Register or update the current IP's user identity."""
    ip = request.state.client_ip
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.execute(
            """INSERT INTO user_ips (ip_address, person_name)
               VALUES (?, ?)
               ON CONFLICT(ip_address) DO UPDATE SET person_name = ?""",
            (ip, name, name),
        )
        conn.commit()
    finally:
        conn.close()

    # Clear cache for this IP
    _ip_cache.pop(ip, None)

    return {"ip": ip, "name": name, "is_local": _is_localhost(ip)}


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
