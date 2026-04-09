"""Power Automate Flows - manual CRUD for tracking PA flows."""

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from app.database import get_db
from app.models import PowerAutomateFlowOut, PowerAutomateFlowCreate, PowerAutomateFlowUpdate
from app.routers.eventlog import log_event, get_actor

router = APIRouter(prefix="/api/power-automate-flows", tags=["power-automate"])


def _build_flow_out(row) -> PowerAutomateFlowOut:
    keys = row.keys()
    # Derive last_run_time from output source probe data if available,
    # fall back to manually entered value
    last_run = row["last_run_time"]
    if "probe_last_data_at" in keys and row["probe_last_data_at"]:
        last_run = row["probe_last_data_at"]
    return PowerAutomateFlowOut(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        owner=row["owner"],
        schedule=row["schedule"],
        source_url=row["source_url"],
        output_source_id=row["output_source_id"],
        output_source_name=row["output_source_name"] if "output_source_name" in keys else None,
        output_description=row["output_description"],
        status=row["status"],
        account=row["account"],
        last_run_time=last_run,
        notes=row["notes"],
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[PowerAutomateFlowOut])
def list_flows(include_archived: bool = Query(False)):
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE pa.archived = 0"
        rows = db.execute(f"""
            SELECT pa.*, s.name AS output_source_name,
                   CAST(sp.last_data_at AS TEXT) AS probe_last_data_at,
                   sp.status AS probe_status
            FROM power_automate_flows pa
            LEFT JOIN sources s ON s.id = pa.output_source_id
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = pa.output_source_id AND sp.rn = 1
            {archive_filter}
            ORDER BY pa.name
        """).fetchall()
    return [_build_flow_out(r) for r in rows]


@router.get("/options")
def get_flow_options():
    """Return dropdown options for the create/edit form."""
    with get_db() as db:
        sources = db.execute(
            "SELECT id, name FROM sources WHERE archived = 0 ORDER BY name"
        ).fetchall()
        owners = db.execute("""
            SELECT DISTINCT owner FROM (
                SELECT DISTINCT owner FROM reports WHERE owner IS NOT NULL AND owner != ''
                UNION
                SELECT DISTINCT owner FROM power_automate_flows WHERE owner IS NOT NULL AND owner != ''
            ) ORDER BY owner
        """).fetchall()
        accounts = db.execute("""
            SELECT DISTINCT account FROM power_automate_flows
            WHERE account IS NOT NULL AND account != ''
            ORDER BY account
        """).fetchall()
        people = db.execute("SELECT id, name, role FROM people ORDER BY name").fetchall()
    return {
        "sources": [dict(r) for r in sources],
        "owners": [r["owner"] for r in owners],
        "accounts": [r["account"] for r in accounts],
        "people": [dict(r) for r in people],
        "statuses": ["active", "paused", "error", "disabled"],
    }


@router.get("/{flow_id}", response_model=PowerAutomateFlowOut)
def get_flow(flow_id: int):
    with get_db() as db:
        row = db.execute("""
            SELECT pa.*, s.name AS output_source_name,
                   CAST(sp.last_data_at AS TEXT) AS probe_last_data_at,
                   sp.status AS probe_status
            FROM power_automate_flows pa
            LEFT JOIN sources s ON s.id = pa.output_source_id
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = pa.output_source_id AND sp.rn = 1
            WHERE pa.id = ?
        """, (flow_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Power Automate flow not found")
    return _build_flow_out(row)


@router.post("", response_model=PowerAutomateFlowOut)
def create_flow(req: PowerAutomateFlowCreate, request: Request):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO power_automate_flows
                   (name, description, owner, schedule, source_url,
                    output_source_id, output_description, status, account,
                    last_run_time, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (req.name, req.description, req.owner, req.schedule,
                 req.source_url, req.output_source_id, req.output_description,
                 req.status, req.account, req.last_run_time, req.notes, now, now),
            )
            flow_id = cursor.lastrowid
            log_event(db, "power_automate", flow_id, req.name, "created",
                      f"status={req.status}", get_actor(request))
            row = db.execute("""
                SELECT pa.*, s.name AS output_source_name,
                       CAST(sp.last_data_at AS TEXT) AS probe_last_data_at,
                       sp.status AS probe_status
                FROM power_automate_flows pa
                LEFT JOIN sources s ON s.id = pa.output_source_id
                LEFT JOIN (
                    SELECT source_id, status, last_data_at,
                           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                    FROM source_probes
                ) sp ON sp.source_id = pa.output_source_id AND sp.rn = 1
                WHERE pa.id = ?
            """, (flow_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A flow with that name already exists")
    return _build_flow_out(row)


@router.patch("/{flow_id}", response_model=PowerAutomateFlowOut)
def update_flow(flow_id: int, req: PowerAutomateFlowUpdate, request: Request):
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [flow_id]
    with get_db() as db:
        cursor = db.execute(
            f"UPDATE power_automate_flows SET {set_clause} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Power Automate flow not found")
        log_event(db, "power_automate", flow_id, None, "updated",
                  ", ".join(k for k in updates if k != "updated_at"), get_actor(request))
        row = db.execute("""
            SELECT pa.*, s.name AS output_source_name,
                   CAST(sp.last_data_at AS TEXT) AS probe_last_data_at,
                   sp.status AS probe_status
            FROM power_automate_flows pa
            LEFT JOIN sources s ON s.id = pa.output_source_id
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = pa.output_source_id AND sp.rn = 1
            WHERE pa.id = ?
        """, (flow_id,)).fetchone()
    return _build_flow_out(row)


@router.delete("/{flow_id}")
def delete_flow(flow_id: int, request: Request):
    with get_db() as db:
        row = db.execute(
            "SELECT id, name FROM power_automate_flows WHERE id = ?", (flow_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Power Automate flow not found")
        db.execute("DELETE FROM power_automate_flows WHERE id = ?", (flow_id,))
        log_event(db, "power_automate", flow_id, row["name"], "deleted",
                  actor=get_actor(request))
    return {"status": "deleted", "id": flow_id}


@router.post("/{flow_id}/archive")
def archive_flow(flow_id: int, request: Request):
    with get_db() as db:
        row = db.execute("SELECT id, archived FROM power_automate_flows WHERE id = ?", (flow_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Power Automate flow not found")
        new_val = 0 if row["archived"] else 1
        db.execute("UPDATE power_automate_flows SET archived = ?, updated_at = ? WHERE id = ?",
                   (new_val, datetime.now(timezone.utc).isoformat(), flow_id))
        action = "archived" if new_val else "unarchived"
        log_event(db, "power_automate", flow_id, None, action, actor=get_actor(request))
    return {"status": action, "id": flow_id}
