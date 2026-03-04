"""Manual entry creation — reports, sources, upstream systems."""

import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

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
        reports = db.execute(
            "SELECT id, name FROM reports ORDER BY name"
        ).fetchall()

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {
        "source_types": ["postgresql", "csv", "excel", "sql server", "folder", "sql", "mysql", "oracle"],
        "weekdays": weekdays,
        "report_frequencies": [f"Weekly - {d}" for d in weekdays],
        "owners": [r["owner"] for r in owners],
        "upstream_systems": [dict(r) for r in upstreams],
        "upstream_codes": ["GSCM", "ASAP"],
        "reports": [dict(r) for r in reports],
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

            # Link source to selected reports via report_tables
            if req.report_ids:
                for report_id in req.report_ids:
                    db.execute(
                        """INSERT OR IGNORE INTO report_tables (report_id, table_name, source_id, source_expression, last_scanned)
                           VALUES (?, ?, ?, 'manual entry', ?)""",
                        (report_id, req.name, source_id, now),
                    )
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


# ── DELETE endpoints ──


@router.delete("/source/{source_id}")
def delete_source(source_id: int):
    """Delete a manually-created source and clean up FK references."""
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM sources WHERE id = ? AND discovered_by = 'manual'",
            (source_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Manual source not found")
        db.execute("DELETE FROM report_tables WHERE source_id = ?", (source_id,))
        db.execute("DELETE FROM source_probes WHERE source_id = ?", (source_id,))
        db.execute("UPDATE alerts SET source_id = NULL WHERE source_id = ?", (source_id,))
        db.execute("UPDATE actions SET source_id = NULL WHERE source_id = ?", (source_id,))
        db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"status": "deleted", "id": source_id}


@router.delete("/report/{report_id}")
def delete_report(report_id: int):
    """Delete a manually-created report and clean up FK references."""
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM reports WHERE id = ? AND discovered_by = 'manual'",
            (report_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Manual report not found")
        db.execute("DELETE FROM report_tables WHERE report_id = ?", (report_id,))
        db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    return {"status": "deleted", "id": report_id}


@router.delete("/upstream/{upstream_id}")
def delete_upstream(upstream_id: int):
    """Delete a manually-created upstream system and clean up FK references."""
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM upstream_systems WHERE id = ? AND discovered_by = 'manual'",
            (upstream_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Manual upstream system not found")
        db.execute("UPDATE sources SET upstream_id = NULL WHERE upstream_id = ?", (upstream_id,))
        db.execute("DELETE FROM upstream_systems WHERE id = ?", (upstream_id,))
    return {"status": "deleted", "id": upstream_id}


# ── PATCH endpoints ──

_SOURCE_FIELDS = {"name", "type", "connection_info", "source_query", "owner", "refresh_schedule", "tags", "upstream_id"}
_REPORT_FIELDS = {"name", "owner", "business_owner", "frequency", "powerbi_url"}
_UPSTREAM_FIELDS = {"name", "code", "refresh_day"}


@router.patch("/source/{source_id}")
def update_source(source_id: int, body: dict[str, Any] = Body(...)):
    """Partially update a manually-created source."""
    updates = {k: v for k, v in body.items() if k in _SOURCE_FIELDS}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [source_id]
    with get_db() as db:
        cursor = db.execute(
            f"UPDATE sources SET {set_clause} WHERE id = ? AND discovered_by = 'manual'",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Manual source not found")
    return {"status": "updated", "id": source_id}


@router.patch("/report/{report_id}")
def update_report(report_id: int, body: dict[str, Any] = Body(...)):
    """Partially update a manually-created report."""
    updates = {k: v for k, v in body.items() if k in _REPORT_FIELDS}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [report_id]
    with get_db() as db:
        cursor = db.execute(
            f"UPDATE reports SET {set_clause} WHERE id = ? AND discovered_by = 'manual'",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Manual report not found")
    return {"status": "updated", "id": report_id}


@router.patch("/upstream/{upstream_id}")
def update_upstream(upstream_id: int, body: dict[str, Any] = Body(...)):
    """Partially update a manually-created upstream system."""
    updates = {k: v for k, v in body.items() if k in _UPSTREAM_FIELDS}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [upstream_id]
    with get_db() as db:
        cursor = db.execute(
            f"UPDATE upstream_systems SET {set_clause} WHERE id = ? AND discovered_by = 'manual'",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Manual upstream system not found")
    return {"status": "updated", "id": upstream_id}
