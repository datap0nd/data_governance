from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.database import init_db
from app.routers import sources, reports, scanner, lineage, alerts, dashboard

app = FastAPI(title="Data Governance Panel", version="0.1.0")

# Register API routers
app.include_router(dashboard.router)
app.include_router(sources.router)
app.include_router(reports.router)
app.include_router(scanner.router)
app.include_router(lineage.router)
app.include_router(alerts.router)

# Serve static files (the web panel)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def serve_panel():
    """Serve the main panel page."""
    return FileResponse(str(static_dir / "index.html"))
