"""Manage and execute scheduled data-quality checks."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.checks.data_quality import (
    CHECK_TYPES,
    SEVERITIES,
    CheckDefinitionError,
    resolve_disabled_check,
    run_quality_checks,
    validate_config,
)
from app.database import get_db
from app.models import DataQualityCheckCreate, DataQualityCheckOut, DataQualityCheckUpdate
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


def _check_out(row) -> DataQualityCheckOut:
    try:
        config = json.loads(row["config"] or "{}")
    except json.JSONDecodeError:
        config = {}
    return DataQualityCheckOut(
        id=row["id"],
        name=row["name"] or CHECK_TYPES.get(row["type"], {}).get("label") or row["type"],
        source_id=row["source_id"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        type=row["type"],
        config=config,
        severity=row["severity"],
        enabled=bool(row["enabled"]),
        latest_status=row["latest_status"],
        latest_value=row["latest_value"],
        latest_message=row["latest_message"],
        latest_ran_at=row["latest_ran_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _select_checks(db, check_id: int | None = None):
    where = "WHERE c.id = ?" if check_id is not None else ""
    params = (check_id,) if check_id is not None else ()
    return db.execute(
        f"""SELECT c.*, s.name AS source_name, s.type AS source_type,
                   cr.status AS latest_status, cr.value AS latest_value,
                   cr.message AS latest_message, cr.ran_at AS latest_ran_at
            FROM checks c
            JOIN sources s ON s.id = c.source_id
            LEFT JOIN check_results cr ON cr.id = (
                SELECT cr2.id FROM check_results cr2
                WHERE cr2.check_id = c.id ORDER BY cr2.ran_at DESC, cr2.id DESC LIMIT 1
            )
            {where}
            ORDER BY c.enabled DESC, c.name, c.id""",
        params,
    ).fetchall()


def _validate_definition(source_id: int, check_type: str, config: dict, severity: str):
    if severity not in SEVERITIES:
        raise HTTPException(400, f"Severity must be one of: {', '.join(sorted(SEVERITIES))}")
    try:
        normalized = validate_config(check_type, config)
    except CheckDefinitionError as exc:
        raise HTTPException(400, str(exc)) from exc
    with get_db() as db:
        source = db.execute(
            "SELECT id, type FROM sources WHERE id = ? AND COALESCE(archived, 0) = 0",
            (source_id,),
        ).fetchone()
    if not source:
        raise HTTPException(404, "Source not found")
    if CHECK_TYPES[check_type]["uses_postgres"] and source["type"] != "postgresql":
        raise HTTPException(400, "This check type requires a PostgreSQL source")
    return normalized


@router.get("/types")
def list_check_types():
    return {
        "types": [{"type": key, **value} for key, value in CHECK_TYPES.items()],
        "severities": sorted(SEVERITIES),
    }


@router.get("/checks", response_model=list[DataQualityCheckOut])
def list_checks():
    with get_db() as db:
        return [_check_out(row) for row in _select_checks(db)]


@router.post("/checks", response_model=DataQualityCheckOut)
def create_check(body: DataQualityCheckCreate, request: Request):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Check name is required")
    config = _validate_definition(body.source_id, body.type, body.config, body.severity)
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO checks
               (name, source_id, type, config, severity, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, body.source_id, body.type, json.dumps(config), body.severity, int(body.enabled), now, now),
        )
        log_event(db, "data_quality_check", cursor.lastrowid, name, "created", actor=get_actor(request))
        row = _select_checks(db, cursor.lastrowid)[0]
    return _check_out(row)


@router.patch("/checks/{check_id}", response_model=DataQualityCheckOut)
def update_check(check_id: int, body: DataQualityCheckUpdate, request: Request):
    with get_db() as db:
        existing = db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
    if not existing:
        raise HTTPException(404, "Check not found")

    data = body.model_dump(exclude_unset=True)
    source_id = data.get("source_id", existing["source_id"])
    check_type = data.get("type", existing["type"])
    severity = data.get("severity", existing["severity"])
    raw_config = data.get("config")
    if raw_config is None:
        try:
            raw_config = json.loads(existing["config"] or "{}")
        except json.JSONDecodeError:
            raw_config = {}
    config = _validate_definition(source_id, check_type, raw_config, severity)
    if "name" in data:
        data["name"] = (data["name"] or "").strip()
        if not data["name"]:
            raise HTTPException(400, "Check name is required")
    data.update({"source_id": source_id, "type": check_type, "severity": severity, "config": json.dumps(config)})
    if "enabled" in data:
        data["enabled"] = int(bool(data["enabled"]))
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        set_clause = ", ".join(f"{key} = ?" for key in data)
        db.execute(f"UPDATE checks SET {set_clause} WHERE id = ?", [*data.values(), check_id])
        log_event(db, "data_quality_check", check_id, data.get("name"), "updated", ", ".join(data), get_actor(request))
        row = _select_checks(db, check_id)[0]
    if not row["enabled"]:
        resolve_disabled_check(check_id)
    return _check_out(row)


@router.delete("/checks/{check_id}")
def disable_check(check_id: int, request: Request):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        row = db.execute("SELECT id, name FROM checks WHERE id = ?", (check_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Check not found")
        db.execute("UPDATE checks SET enabled = 0, updated_at = ? WHERE id = ?", (now, check_id))
        log_event(db, "data_quality_check", check_id, row["name"], "disabled", actor=get_actor(request))
    resolve_disabled_check(check_id)
    return {"status": "disabled", "id": check_id}


@router.post("/run")
def run_all_checks():
    return run_quality_checks()


@router.post("/checks/{check_id}/run")
def run_one_check(check_id: int):
    with get_db() as db:
        exists = db.execute("SELECT id FROM checks WHERE id = ? AND enabled = 1", (check_id,)).fetchone()
    if not exists:
        raise HTTPException(404, "Enabled check not found")
    return run_quality_checks([check_id])


@router.get("/checks/{check_id}/results")
def list_check_results(check_id: int, limit: int = 50):
    safe_limit = max(1, min(int(limit), 200))
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM check_results WHERE check_id = ?
               ORDER BY ran_at DESC, id DESC LIMIT ?""",
            (check_id, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]
