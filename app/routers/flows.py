"""Configurable website report downloads with external authenticated workers."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.database import get_db
from app.flow_local_runner import WORKER_ID as LOCAL_WORKER_ID, launch_local_worker
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/flows", tags=["flows"])

CONTROL_TYPES = {"select", "multi_select", "text", "week"}
DOWNLOAD_MODES = {"single", "one_per_week"}
SCHEDULE_TYPES = {"manual", "daily", "weekly"}
RUN_TERMINAL = {"succeeded", "failed", "cancelled"}
RUN_STATUSES = {"queued", "claimed", "running", *RUN_TERMINAL}
WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>0[1-9]|[1-4]\d|5[0-3])$")
FILENAME_TOKEN_RE = re.compile(r"\{(flow|report|week|year|week_number|index|date)\}")
SAFE_NAME_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default):
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, ValueError):
        return default


def _validate_http_url(value: str, label: str) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{label} must be an HTTP or HTTPS URL.")
    return value


def _clean_filename_template(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter a filename template.")
    sample = FILENAME_TOKEN_RE.sub("sample", value)
    if not sample.casefold().endswith(".csv"):
        raise ValueError("The filename template must end in .csv.")
    if not SAFE_NAME_RE.fullmatch(sample):
        raise ValueError("The filename template contains Windows filename characters that are not allowed.")
    return value


def _is_absolute_worker_path(value: str) -> bool:
    return bool(
        value.startswith("/")
        or value.startswith("\\\\")
        or re.match(r"^[A-Za-z]:[\\/]", value)
    )


def _week_value(value: str | None, label: str) -> str | None:
    value = (value or "").strip() or None
    if value and not WEEK_RE.fullmatch(value):
        raise ValueError(f"{label} must use YYYY-Www format.")
    return value


def _week_range(start: str, end: str) -> list[str]:
    start_match = WEEK_RE.fullmatch(start)
    end_match = WEEK_RE.fullmatch(end)
    if not start_match or not end_match:
        raise ValueError("Week range must use YYYY-Www values.")
    try:
        current = datetime.fromisocalendar(int(start_match["year"]), int(start_match["week"]), 1)
        final = datetime.fromisocalendar(int(end_match["year"]), int(end_match["week"]), 1)
    except ValueError as exc:
        raise ValueError("The selected week does not exist in that year.") from exc
    if final < current:
        raise ValueError("End week cannot be before start week.")
    if (final - current).days > 7 * 104:
        raise ValueError("A flow can cover at most 105 weeks.")
    result = []
    while current <= final:
        year, week, _ = current.isocalendar()
        result.append(f"{year:04d}-W{week:02d}")
        current += timedelta(days=7)
    return result


def _schedule_next(schedule_type: str, schedule_time: str | None, schedule_days: list[str]) -> datetime | None:
    if schedule_type == "manual":
        return None
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time or ""):
        raise ValueError("Scheduled flows need a valid HH:MM time.")
    hour, minute = (int(part) for part in schedule_time.split(":"))
    now = _now()
    if schedule_type == "daily":
        candidate = now.replace(hour=hour, minute=minute, second=0)
        return candidate if candidate > now else candidate + timedelta(days=1)
    day_numbers = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    selected = {day_numbers[day] for day in schedule_days if day in day_numbers}
    if not selected:
        raise ValueError("Weekly flows need at least one weekday.")
    for offset in range(8):
        date = now.date() + timedelta(days=offset)
        if date.weekday() not in selected:
            continue
        candidate = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)
        if candidate > now:
            return candidate
    raise ValueError("Could not calculate the next run.")


class SiteWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    adapter: str = Field(default="web_export", min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=2000)
    auth_url: str | None = Field(default=None, max_length=2000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_urls(self):
        self.name = self.name.strip()
        self.adapter = self.adapter.strip()
        if self.base_url:
            self.base_url = _validate_http_url(self.base_url, "Base URL")
        if self.auth_url:
            self.auth_url = _validate_http_url(self.auth_url, "Authentication URL")
        return self


class FilterWrite(BaseModel):
    filter_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=200)
    control_label: str = Field(min_length=1, max_length=300)
    control_type: str = "select"
    options: list[str] = Field(default_factory=list, max_length=1000)
    automation: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    position: int = Field(default=0, ge=0, le=1000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_filter(self):
        self.label = self.label.strip()
        self.control_label = self.control_label.strip()
        if self.control_type not in CONTROL_TYPES:
            raise ValueError("Unsupported filter control type.")
        normalized = []
        for option in self.options:
            option = str(option).strip()
            if option and option not in normalized:
                normalized.append(option)
        self.options = normalized
        return self


class ReportWrite(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=200)
    report_url: str = Field(min_length=1, max_length=4000)
    ready_text: str | None = Field(default=None, max_length=500)
    open_export_text: str | None = Field(default=None, max_length=500)
    download_text: str = Field(default="Download CSV", min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    enabled: bool = True
    filters: list[FilterWrite] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_report(self):
        self.name = self.name.strip()
        self.report_url = _validate_http_url(self.report_url, "Report URL")
        self.ready_text = (self.ready_text or "").strip() or None
        self.open_export_text = (self.open_export_text or "").strip() or None
        self.download_text = self.download_text.strip()
        self.notes = (self.notes or "").strip() or None
        keys = [item.filter_key.casefold() for item in self.filters]
        if len(keys) != len(set(keys)):
            raise ValueError("Each report filter key must be unique.")
        return self


class FlowWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    site_id: int
    report_id: int
    enabled: bool = False
    selections: dict[str, Any] = Field(default_factory=dict)
    download_mode: str = "single"
    start_week: str | None = None
    end_week: str | None = None
    target_folder: str = Field(min_length=1, max_length=2000)
    filename_template: str = Field(min_length=1, max_length=500)
    schedule_type: str = "manual"
    schedule_time: str | None = None
    schedule_days: list[str] = Field(default_factory=list)
    sql_handoff_enabled: bool = False

    @model_validator(mode="after")
    def validate_flow(self):
        self.name = self.name.strip()
        self.target_folder = self.target_folder.strip()
        self.filename_template = _clean_filename_template(self.filename_template)
        if not _is_absolute_worker_path(self.target_folder):
            raise ValueError("Target folder must be an absolute path visible to the worker.")
        if self.download_mode not in DOWNLOAD_MODES:
            raise ValueError("Unsupported download mode.")
        if self.schedule_type not in SCHEDULE_TYPES:
            raise ValueError("Unsupported schedule type.")
        self.start_week = _week_value(self.start_week, "Start week")
        self.end_week = _week_value(self.end_week, "End week")
        if bool(self.start_week) != bool(self.end_week):
            raise ValueError("Choose both start and end week.")
        if self.start_week and self.end_week:
            _week_range(self.start_week, self.end_week)
        if self.download_mode == "one_per_week" and not self.start_week:
            raise ValueError("One download per week needs a start and end week.")
        if self.download_mode == "one_per_week" and "{week}" not in self.filename_template:
            raise ValueError("One download per week requires {week} in the filename template.")
        self.schedule_days = [str(day).strip().casefold() for day in self.schedule_days]
        _schedule_next(self.schedule_type, self.schedule_time, self.schedule_days)
        if self.sql_handoff_enabled:
            raise ValueError("SQL handoff is reserved for a later release and cannot be enabled.")
        return self


class WorkerRegister(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class WorkerProgress(BaseModel):
    status: Literal["running", "succeeded", "failed", "cancelled"]
    progress: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = Field(default=None, max_length=10000)


def _filter_row(row) -> dict:
    return {
        "id": row["id"], "filter_key": row["filter_key"], "label": row["label"],
        "control_label": row["control_label"], "control_type": row["control_type"],
        "options": _loads(row["options_json"], []),
        "automation": _loads(row["automation_json"], {}),
        "required": bool(row["required"]), "position": row["position"],
        "enabled": bool(row["enabled"]),
    }


def _report_out(db, report_id: int) -> dict:
    row = db.execute(
        """SELECT r.*, s.name AS site_name, s.adapter, s.auth_url
           FROM flow_reports r JOIN flow_sites s ON s.id = r.site_id WHERE r.id = ?""",
        (report_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Report not found.")
    filters = db.execute(
        "SELECT * FROM flow_report_filters WHERE report_id = ? ORDER BY position, id", (report_id,)
    ).fetchall()
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["filters"] = [_filter_row(item) for item in filters]
    return result


def _flow_out(db, flow_id: int) -> dict:
    row = db.execute(
        """SELECT f.*, s.name AS site_name, r.name AS report_name
           FROM flows f
           JOIN flow_sites s ON s.id = f.site_id
           JOIN flow_reports r ON r.id = f.report_id
           WHERE f.id = ?""",
        (flow_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Flow not found.")
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["sql_handoff_enabled"] = bool(result["sql_handoff_enabled"])
    result["selections"] = _loads(result.pop("selections_json"), {})
    result["schedule_days"] = _loads(result.pop("schedule_days"), [])
    return result


def _validate_flow_selections(db, body: FlowWrite):
    report = db.execute("SELECT site_id FROM flow_reports WHERE id = ? AND enabled = 1", (body.report_id,)).fetchone()
    if not report or report["site_id"] != body.site_id:
        raise HTTPException(400, "Choose a report from the selected website.")
    rows = db.execute(
        "SELECT * FROM flow_report_filters WHERE report_id = ? AND enabled = 1 ORDER BY position, id",
        (body.report_id,),
    ).fetchall()
    definitions = {row["filter_key"]: row for row in rows}
    unknown = sorted(set(body.selections) - set(definitions))
    if unknown:
        raise HTTPException(400, f"Unknown report filter: {unknown[0]}")
    for key, row in definitions.items():
        value = body.selections.get(key)
        options = _loads(row["options_json"], [])
        values = value if isinstance(value, list) else [value]
        present = [str(item).strip() for item in values if item is not None and str(item).strip()]
        if row["required"] and not present:
            raise HTTPException(400, f"Choose {row['label']}.")
        invalid = [item for item in present if options and item not in options]
        if invalid:
            raise HTTPException(400, f"Invalid {row['label']} value: {invalid[0]}")


def _build_job(db, flow_id: int) -> dict:
    flow = _flow_out(db, flow_id)
    report = _report_out(db, flow["report_id"])
    weeks = _week_range(flow["start_week"], flow["end_week"]) if flow["start_week"] else []
    periods = weeks if flow["download_mode"] == "one_per_week" else [None]
    return {
        "schema_version": 1,
        "execution": {"mode": "local", "host": "bi_desktop", "worker_id": LOCAL_WORKER_ID},
        "flow": {"id": flow["id"], "name": flow["name"]},
        "site": {
            "id": flow["site_id"], "name": flow["site_name"],
            "adapter": report["adapter"], "auth_url": report["auth_url"],
        },
        "report": {
            "id": report["id"], "name": report["name"], "url": report["report_url"],
            "ready_text": report["ready_text"], "open_export_text": report["open_export_text"],
            "download_text": report["download_text"], "filters": report["filters"],
        },
        "selections": flow["selections"],
        "downloads": {
            "mode": flow["download_mode"], "periods": periods,
            "target_folder": flow["target_folder"],
            "filename_template": flow["filename_template"],
            "collision_policy": "number_suffix",
            "delete_existing": False,
            "overwrite_existing": False,
        },
        "sql_handoff": {"enabled": False, "status": "not_implemented"},
    }


def queue_due_flows() -> dict:
    """Queue due scheduled flows without executing browser work in the API process."""
    now = _now()
    now_text = _iso(now)
    queued = []
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM flows
               WHERE enabled=1 AND schedule_type != 'manual'
                 AND next_run_at IS NOT NULL AND next_run_at <= ?
               ORDER BY next_run_at, id""",
            (now_text,),
        ).fetchall()
        for row in rows:
            active = db.execute(
                "SELECT id FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1",
                (row["id"],),
            ).fetchone()
            days = _loads(row["schedule_days"], [])
            next_run = _schedule_next(row["schedule_type"], row["schedule_time"], days)
            db.execute(
                "UPDATE flows SET next_run_at=?, updated_at=? WHERE id=?",
                (_iso(next_run), now_text, row["id"]),
            )
            if active:
                continue
            job = _build_job(db, row["id"])
            cursor = db.execute(
                """INSERT INTO flow_runs
                   (flow_id, trigger_type, status, requested_by, job_json, created_at)
                   VALUES (?, 'scheduled', 'queued', 'scheduler', ?, ?)""",
                (row["id"], _json(job), now_text),
            )
            queued.append(cursor.lastrowid)
    worker = launch_local_worker() if queued else {"status": "not_needed", "mode": "local"}
    return {"queued": queued, "count": len(queued), "worker": worker}


@router.get("/catalog")
def catalog():
    with get_db() as db:
        sites = [dict(row) for row in db.execute("SELECT * FROM flow_sites ORDER BY name").fetchall()]
        reports = [dict(row) for row in db.execute("SELECT * FROM flow_reports ORDER BY name").fetchall()]
        filters = db.execute("SELECT * FROM flow_report_filters ORDER BY report_id, position, id").fetchall()
    by_report: dict[int, list] = {}
    for row in filters:
        by_report.setdefault(row["report_id"], []).append(_filter_row(row))
    for site in sites:
        site["enabled"] = bool(site["enabled"])
    for report in reports:
        report["enabled"] = bool(report["enabled"])
        report["filters"] = by_report.get(report["id"], [])
    return {"sites": sites, "reports": reports, "control_types": sorted(CONTROL_TYPES)}


@router.post("/sites")
def create_site(body: SiteWrite, request: Request):
    now = _iso(_now())
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO flow_sites (name, adapter, base_url, auth_url, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (body.name, body.adapter, body.base_url, body.auth_url, body.enabled, now, now),
            )
            log_event(db, "flow_site", cursor.lastrowid, body.name, "created", actor=get_actor(request))
            row = db.execute("SELECT * FROM flow_sites WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A website with that name already exists.") from exc
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


@router.put("/sites/{site_id}")
def update_site(site_id: int, body: SiteWrite, request: Request):
    with get_db() as db:
        cursor = db.execute(
            """UPDATE flow_sites SET name=?, adapter=?, base_url=?, auth_url=?, enabled=?, updated_at=?
               WHERE id=?""",
            (body.name, body.adapter, body.base_url, body.auth_url, body.enabled, _iso(_now()), site_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Website not found.")
        log_event(db, "flow_site", site_id, body.name, "updated", actor=get_actor(request))
        row = db.execute("SELECT * FROM flow_sites WHERE id = ?", (site_id,)).fetchone()
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


@router.post("/reports")
def create_report(body: ReportWrite, request: Request):
    now = _iso(_now())
    try:
        with get_db() as db:
            if not db.execute("SELECT id FROM flow_sites WHERE id = ?", (body.site_id,)).fetchone():
                raise HTTPException(400, "Website not found.")
            cursor = db.execute(
                """INSERT INTO flow_reports
                   (site_id, name, report_url, ready_text, open_export_text, download_text, notes, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (body.site_id, body.name, body.report_url, body.ready_text, body.open_export_text,
                 body.download_text, body.notes, body.enabled, now, now),
            )
            report_id = cursor.lastrowid
            _replace_filters(db, report_id, body.filters, now)
            log_event(db, "flow_report", report_id, body.name, "created", actor=get_actor(request))
            return _report_out(db, report_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "That report name already exists for this website.") from exc


def _replace_filters(db, report_id: int, filters: list[FilterWrite], now: str):
    keys = [item.filter_key for item in filters]
    if keys:
        placeholders = ",".join("?" for _ in keys)
        db.execute(
            f"UPDATE flow_report_filters SET enabled=0, updated_at=? "
            f"WHERE report_id=? AND filter_key NOT IN ({placeholders})",
            (now, report_id, *keys),
        )
    else:
        db.execute(
            "UPDATE flow_report_filters SET enabled=0, updated_at=? WHERE report_id=?",
            (now, report_id),
        )
    db.executemany(
        """INSERT INTO flow_report_filters
           (report_id, filter_key, label, control_label, control_type, options_json,
            automation_json, required, position, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(report_id, filter_key) DO UPDATE SET
             label=excluded.label, control_label=excluded.control_label,
             control_type=excluded.control_type, options_json=excluded.options_json,
             automation_json=excluded.automation_json, required=excluded.required,
             position=excluded.position, enabled=excluded.enabled,
             updated_at=excluded.updated_at""",
        [
            (report_id, item.filter_key, item.label, item.control_label, item.control_type,
             _json(item.options), _json(item.automation), item.required, item.position,
             item.enabled, now, now)
            for item in filters
        ],
    )


@router.put("/reports/{report_id}")
def update_report(report_id: int, body: ReportWrite, request: Request):
    now = _iso(_now())
    with get_db() as db:
        cursor = db.execute(
            """UPDATE flow_reports SET site_id=?, name=?, report_url=?, ready_text=?, open_export_text=?,
               download_text=?, notes=?, enabled=?, updated_at=? WHERE id=?""",
            (body.site_id, body.name, body.report_url, body.ready_text, body.open_export_text,
             body.download_text, body.notes, body.enabled, now, report_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Report not found.")
        _replace_filters(db, report_id, body.filters, now)
        log_event(db, "flow_report", report_id, body.name, "updated", actor=get_actor(request))
        return _report_out(db, report_id)


@router.get("")
def list_flows():
    with get_db() as db:
        ids = [row["id"] for row in db.execute("SELECT id FROM flows ORDER BY updated_at DESC, name").fetchall()]
        return [_flow_out(db, flow_id) for flow_id in ids]


@router.get("/runs")
def list_runs(flow_id: int | None = None, limit: int = Query(default=100, ge=1, le=500)):
    with get_db() as db:
        sql = """SELECT r.*, f.name AS flow_name FROM flow_runs r JOIN flows f ON f.id=r.flow_id"""
        params: list[Any] = []
        if flow_id is not None:
            sql += " WHERE r.flow_id = ?"
            params.append(flow_id)
        sql += " ORDER BY r.created_at DESC, r.id DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()
        return [
            {**dict(row), "job": _loads(row["job_json"], {}),
             "progress": _loads(row["progress_json"], {}),
             "artifacts": _loads(row["artifact_json"], [])}
            for row in rows
        ]


@router.get("/workers")
def list_workers():
    cutoff = _iso(_now() - timedelta(seconds=90))
    with get_db() as db:
        rows = db.execute("SELECT * FROM flow_workers ORDER BY display_name").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["capabilities"] = _loads(item.pop("capabilities_json"), {})
        if not item["last_seen_at"] or item["last_seen_at"] < cutoff:
            item["status"] = "offline"
        result.append(item)
    return result


@router.post("")
def create_flow(body: FlowWrite, request: Request):
    now = _iso(_now())
    next_run = _iso(_schedule_next(body.schedule_type, body.schedule_time, body.schedule_days))
    try:
        with get_db() as db:
            _validate_flow_selections(db, body)
            cursor = db.execute(
                """INSERT INTO flows
                   (name, site_id, report_id, enabled, selections_json, download_mode, start_week, end_week,
                    target_folder, filename_template, schedule_type, schedule_time, schedule_days, next_run_at,
                    sql_handoff_enabled, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (body.name, body.site_id, body.report_id, body.enabled, _json(body.selections),
                 body.download_mode, body.start_week, body.end_week, body.target_folder,
                 body.filename_template, body.schedule_type, body.schedule_time,
                 _json(body.schedule_days), next_run, get_actor(request), now, now),
            )
            flow_id = cursor.lastrowid
            log_event(db, "flow", flow_id, body.name, "created", "SQL handoff disabled", get_actor(request))
            return _flow_out(db, flow_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A flow with that name already exists.") from exc


@router.put("/{flow_id}")
def update_flow(flow_id: int, body: FlowWrite, request: Request):
    now = _iso(_now())
    next_run = _iso(_schedule_next(body.schedule_type, body.schedule_time, body.schedule_days))
    with get_db() as db:
        _validate_flow_selections(db, body)
        cursor = db.execute(
            """UPDATE flows SET name=?, site_id=?, report_id=?, enabled=?, selections_json=?,
               download_mode=?, start_week=?, end_week=?, target_folder=?, filename_template=?,
               schedule_type=?, schedule_time=?, schedule_days=?, next_run_at=?,
               sql_handoff_enabled=0, updated_at=? WHERE id=?""",
            (body.name, body.site_id, body.report_id, body.enabled, _json(body.selections),
             body.download_mode, body.start_week, body.end_week, body.target_folder,
             body.filename_template, body.schedule_type, body.schedule_time,
             _json(body.schedule_days), next_run, now, flow_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Flow not found.")
        log_event(db, "flow", flow_id, body.name, "updated", actor=get_actor(request))
        return _flow_out(db, flow_id)


@router.post("/{flow_id}/run")
def queue_run(flow_id: int, request: Request):
    now = _iso(_now())
    with get_db() as db:
        flow = db.execute("SELECT id, name FROM flows WHERE id = ?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        active = db.execute(
            "SELECT id FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1",
            (flow_id,),
        ).fetchone()
        if active:
            raise HTTPException(409, "This flow already has an active run.")
        job = _build_job(db, flow_id)
        cursor = db.execute(
            """INSERT INTO flow_runs (flow_id, trigger_type, status, requested_by, job_json, created_at)
               VALUES (?, 'manual', 'queued', ?, ?, ?)""",
            (flow_id, get_actor(request), _json(job), now),
        )
        run_id = cursor.lastrowid
        log_event(db, "flow", flow_id, flow["name"], "run_queued", f"run_id={run_id}", get_actor(request))
    worker = launch_local_worker()
    if worker.get("status") == "error":
        with get_db() as db:
            db.execute(
                "UPDATE flow_runs SET progress_json=? WHERE id=?",
                (_json({"stage": "waiting_for_bi_desktop", "message": worker.get("message")}), run_id),
            )
    return {"id": run_id, "flow_id": flow_id, "status": "queued", "job": job, "worker": worker}


def ensure_local_worker() -> dict:
    """Restart the resident BI desktop worker when it is not online or busy."""
    cutoff = _iso(_now() - timedelta(seconds=90))
    with get_db() as db:
        row = db.execute(
            "SELECT status, current_run_id, last_seen_at FROM flow_workers WHERE worker_id=?",
            (LOCAL_WORKER_ID,),
        ).fetchone()
    if row and row["current_run_id"]:
        return {"status": "busy", "mode": "local", "worker_id": LOCAL_WORKER_ID}
    if row and row["last_seen_at"] and row["last_seen_at"] >= cutoff:
        return {"status": "online", "mode": "local", "worker_id": LOCAL_WORKER_ID}
    return launch_local_worker()


@router.post("/worker/register")
def register_worker(body: WorkerRegister):
    now = _iso(_now())
    with get_db() as db:
        db.execute(
            """INSERT INTO flow_workers
               (worker_id, display_name, capabilities_json, status, last_seen_at, created_at, updated_at)
               VALUES (?, ?, ?, 'idle', ?, ?, ?)
               ON CONFLICT(worker_id) DO UPDATE SET
                 display_name=excluded.display_name, capabilities_json=excluded.capabilities_json,
                 status=CASE WHEN flow_workers.current_run_id IS NULL THEN 'idle' ELSE flow_workers.status END,
                 last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at""",
            (body.worker_id, body.display_name, _json(body.capabilities), now, now, now),
        )
    return {"worker_id": body.worker_id, "status": "idle"}


@router.post("/worker/{worker_id}/claim")
def claim_run(worker_id: str):
    now = _iso(_now())
    with get_db() as db:
        worker = db.execute("SELECT * FROM flow_workers WHERE worker_id=?", (worker_id,)).fetchone()
        if not worker:
            raise HTTPException(404, "Register this worker before claiming work.")
        if worker["current_run_id"]:
            row = db.execute("SELECT * FROM flow_runs WHERE id=?", (worker["current_run_id"],)).fetchone()
            if row and row["status"] not in RUN_TERMINAL:
                db.execute("UPDATE flow_workers SET last_seen_at=?, updated_at=? WHERE worker_id=?", (now, now, worker_id))
                return {"run": {**dict(row), "job": _loads(row["job_json"], {})}}
        row = db.execute("SELECT * FROM flow_runs WHERE status='queued' ORDER BY created_at, id LIMIT 1").fetchone()
        if not row:
            db.execute(
                "UPDATE flow_workers SET status='idle', current_run_id=NULL, last_seen_at=?, updated_at=? WHERE worker_id=?",
                (now, now, worker_id),
            )
            return {"run": None}
        cursor = db.execute(
            """UPDATE flow_runs SET status='claimed', worker_id=?, claimed_at=?, heartbeat_at=?
               WHERE id=? AND status='queued'""",
            (worker_id, now, now, row["id"]),
        )
        if not cursor.rowcount:
            return {"run": None}
        db.execute(
            """UPDATE flow_workers SET status='busy', current_run_id=?, last_seen_at=?, updated_at=?
               WHERE worker_id=?""",
            (row["id"], now, now, worker_id),
        )
        claimed = db.execute("SELECT * FROM flow_runs WHERE id=?", (row["id"],)).fetchone()
        return {"run": {**dict(claimed), "job": _loads(claimed["job_json"], {})}}


@router.post("/worker/{worker_id}/runs/{run_id}/progress")
def update_run(worker_id: str, run_id: int, body: WorkerProgress):
    if body.status not in RUN_STATUSES:
        raise HTTPException(400, "Unsupported run status.")
    now = _iso(_now())
    with get_db() as db:
        row = db.execute("SELECT * FROM flow_runs WHERE id=? AND worker_id=?", (run_id, worker_id)).fetchone()
        if not row:
            raise HTTPException(404, "Run is not assigned to this worker.")
        started = row["started_at"] or (now if body.status == "running" else None)
        finished = now if body.status in RUN_TERMINAL else None
        db.execute(
            """UPDATE flow_runs SET status=?, progress_json=?, artifact_json=?, error=?,
               started_at=COALESCE(started_at, ?), finished_at=?, heartbeat_at=? WHERE id=?""",
            (body.status, _json(body.progress), _json(body.artifacts), body.error,
             started, finished, now, run_id),
        )
        db.execute(
            "UPDATE flows SET last_run_at=?, last_status=?, last_error=?, updated_at=? WHERE id=?",
            (finished or started or now, body.status, body.error, now, row["flow_id"]),
        )
        if body.status in RUN_TERMINAL:
            db.execute(
                """UPDATE flow_workers SET status='idle', current_run_id=NULL, last_error=?,
                   last_seen_at=?, updated_at=? WHERE worker_id=?""",
                (body.error, now, now, worker_id),
            )
            db.executemany(
                """INSERT INTO flow_run_files
                   (run_id, period_key, file_path, filename, file_size, checksum, row_count, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (run_id, item.get("period_key"), str(item.get("file_path") or ""),
                     str(item.get("filename") or ""), item.get("file_size"), item.get("checksum"),
                     item.get("row_count"), str(item.get("status") or "saved"), now)
                    for item in body.artifacts if item.get("file_path") and item.get("filename")
                ],
            )
        else:
            db.execute(
                "UPDATE flow_workers SET status='busy', last_seen_at=?, updated_at=? WHERE worker_id=?",
                (now, now, worker_id),
            )
    return {"run_id": run_id, "status": body.status}


@router.get("/{flow_id}")
def get_flow(flow_id: int):
    with get_db() as db:
        return _flow_out(db, flow_id)
