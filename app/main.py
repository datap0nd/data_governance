import logging
import time

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path

from app.database import init_db
from app.routers import sources, reports, scanner, lineage, alerts, dashboard, actions, changelog, schedules, create, best_practices, tasks
from app.ai.router import router as ai_router

# Show scanner logs in the console
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

app = FastAPI(title="Data Governance Panel", version="0.1.0")

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
    html = html.replace("?v=7", f"?v={ver}")
    return HTMLResponse(content=html, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    })


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def serve_panel():
    """Serve the main panel page."""
    return _serve_index()


@app.get("/{path:path}")
def spa_catch_all(path: str):
    """Catch-all route for SPA — serve index.html for non-API, non-static paths."""
    if path.startswith("api/") or path.startswith("static/"):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return _serve_index()
