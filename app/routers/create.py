"""Manual entry creation — reports, sources, upstream systems."""

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.database import get_db
from app.models import (
    CreateSourceRequest,
    CreateReportRequest,
    CreateUpstreamRequest,
    CustomEntryOut,
)

router = APIRouter(prefix="/api/create", tags=["create"])


@router.get("/options")
def get_create_options():
    """Return all dropdown options for the create forms."""
    with get_db() as db:
        owners = db.execute("""
            SELECT DISTINCT owner FROM (
                SELECT DISTINCT owner FROM reports WHERE owner IS NOT NULL AND owner != ''
                UNION
                SELECT DISTINCT business_owner AS owner FROM reports WHERE business_owner IS NOT NULL AND business_owner != ''
            ) ORDER BY owner
        """).fetchall()
        upstreams = db.execute(
            "SELECT id, name, code FROM upstream_systems ORDER BY name"
        ).fetchall()

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {
        "source_types": ["postgresql", "csv", "excel", "sql server", "folder", "sql", "mysql", "oracle"],
        "weekdays": weekdays,
        "report_frequencies": [f"Weekly - {d}" for d in weekdays],
        "owners": [r["owner"] for r in owners],
        "upstream_systems": [dict(r) for r in upstreams],
        "upstream_codes": ["GSCM", "ASAP"],
    }


@router.post("/source")
def create_source(req: CreateSourceRequest):
    """Create a new data source manually."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO sources (name, type, connection_info, source_query, owner, refresh_schedule, tags, discovered_by, upstream_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?)""",
                (req.name, req.type, req.connection_info, req.source_query,
                 req.owner, req.refresh_schedule, req.tags, req.upstream_id, now, now),
            )
            source_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A source with that name already exists")

    return {"id": source_id, "name": req.name, "type": req.type, "status": "created"}


@router.post("/report")
def create_report(req: CreateReportRequest):
    """Create a new report manually."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO reports (name, owner, business_owner, frequency, powerbi_url, discovered_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)""",
                (req.name, req.owner, req.business_owner, req.frequency,
                 req.powerbi_url, now, now),
            )
            report_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A report with that name already exists")

    return {"id": report_id, "name": req.name, "status": "created"}


@router.post("/upstream")
def create_upstream(req: CreateUpstreamRequest):
    """Create a new upstream data source manually."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO upstream_systems (name, code, refresh_day, discovered_by, created_at)
                   VALUES (?, ?, ?, 'manual', ?)""",
                (req.name, req.code, req.refresh_day, now),
            )
            upstream_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An upstream system with that name already exists")

    return {"id": upstream_id, "name": req.name, "code": req.code, "status": "created"}


@router.get("/custom-entries", response_model=list[CustomEntryOut])
def list_custom_entries():
    """List all manually-created entries across sources, reports, and upstream systems."""
    with get_db() as db:
        rows = db.execute("""
            SELECT id, 'source' AS entity_type, name, type AS detail, CAST(created_at AS TEXT) AS created_at
            FROM sources WHERE discovered_by = 'manual'
            UNION ALL
            SELECT id, 'report' AS entity_type, name, frequency AS detail, CAST(created_at AS TEXT) AS created_at
            FROM reports WHERE discovered_by = 'manual'
            UNION ALL
            SELECT id, 'upstream' AS entity_type, name, code AS detail, CAST(created_at AS TEXT) AS created_at
            FROM upstream_systems WHERE discovered_by = 'manual'
            ORDER BY created_at DESC
        """).fetchall()

    return [
        CustomEntryOut(
            id=r["id"],
            entity_type=r["entity_type"],
            name=r["name"],
            detail=r["detail"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
