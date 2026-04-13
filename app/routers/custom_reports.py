"""Custom Reports - CRUD for recurring tasks with stakeholders and documentation."""

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from app.database import get_db
from app.models import CustomReportOut, CustomReportCreate, CustomReportUpdate
from app.routers.eventlog import log_event, get_actor

router = APIRouter(prefix="/api/custom-reports", tags=["custom-reports"])


def _build_report_out(row) -> CustomReportOut:
    return CustomReportOut(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        frequency=row["frequency"],
        owner=row["owner"],
        stakeholders=row["stakeholders"],
        steps=row["steps"],
        data_sources=row["data_sources"],
        output_description=row["output_description"],
        estimated_hours=row["estimated_hours"],
        status=row["status"],
        last_completed=row["last_completed"],
        tags=row["tags"],
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[CustomReportOut])
def list_custom_reports(include_archived: bool = Query(False)):
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE archived = 0"
        rows = db.execute(f"""
            SELECT * FROM custom_reports {archive_filter} ORDER BY name
        """).fetchall()
    return [_build_report_out(r) for r in rows]


@router.get("/options")
def get_custom_report_options():
    """Return dropdown options for the create/edit form."""
    with get_db() as db:
        people = db.execute("SELECT id, name, role FROM people ORDER BY name").fetchall()
        owners = db.execute("""
            SELECT DISTINCT owner FROM custom_reports
            WHERE owner IS NOT NULL AND owner != ''
            ORDER BY owner
        """).fetchall()
    return {
        "people": [dict(r) for r in people],
        "owners": [r["owner"] for r in owners],
        "statuses": ["active", "paused", "archived"],
        "frequencies": ["Daily", "Weekly", "Bi-weekly", "Monthly", "Quarterly", "Yearly", "Ad-hoc"],
    }


@router.get("/{report_id}", response_model=CustomReportOut)
def get_custom_report(report_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM custom_reports WHERE id = ?", (report_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Custom report not found")
    return _build_report_out(row)


@router.post("", response_model=CustomReportOut)
def create_custom_report(req: CustomReportCreate, request: Request):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO custom_reports
                   (name, description, frequency, owner, stakeholders, steps,
                    data_sources, output_description, estimated_hours, status,
                    last_completed, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (req.name, req.description, req.frequency, req.owner,
                 req.stakeholders, req.steps, req.data_sources,
                 req.output_description, req.estimated_hours, req.status,
                 req.last_completed, req.tags, now, now),
            )
            report_id = cursor.lastrowid
            log_event(db, "custom_report", report_id, req.name, "created",
                      f"status={req.status}", get_actor(request))
            row = db.execute(
                "SELECT * FROM custom_reports WHERE id = ?", (report_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A custom report with that name already exists")
    return _build_report_out(row)


@router.patch("/{report_id}", response_model=CustomReportOut)
def update_custom_report(report_id: int, req: CustomReportUpdate, request: Request):
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [report_id]
    with get_db() as db:
        cursor = db.execute(
            f"UPDATE custom_reports SET {set_clause} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Custom report not found")
        log_event(db, "custom_report", report_id, None, "updated",
                  ", ".join(k for k in updates if k != "updated_at"), get_actor(request))
        row = db.execute(
            "SELECT * FROM custom_reports WHERE id = ?", (report_id,)
        ).fetchone()
    return _build_report_out(row)


@router.delete("/{report_id}")
def delete_custom_report(report_id: int, request: Request):
    with get_db() as db:
        row = db.execute(
            "SELECT id, name FROM custom_reports WHERE id = ?", (report_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Custom report not found")
        db.execute("DELETE FROM custom_reports WHERE id = ?", (report_id,))
        log_event(db, "custom_report", report_id, row["name"], "deleted",
                  actor=get_actor(request))
    return {"status": "deleted", "id": report_id}


@router.post("/{report_id}/archive")
def archive_custom_report(report_id: int, request: Request):
    with get_db() as db:
        row = db.execute("SELECT id, archived FROM custom_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Custom report not found")
        new_val = 0 if row["archived"] else 1
        db.execute("UPDATE custom_reports SET archived = ?, updated_at = ? WHERE id = ?",
                   (new_val, datetime.now(timezone.utc).isoformat(), report_id))
        action = "archived" if new_val else "unarchived"
        log_event(db, "custom_report", report_id, None, action, actor=get_actor(request))
    return {"status": action, "id": report_id}
