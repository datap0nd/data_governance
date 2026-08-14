"""Configurable website report downloads with external authenticated workers."""

from __future__ import annotations

import copy
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import DB_PATH
from app.database import get_db
from app.flow_credentials import asap_credential_status, save_asap_credentials
from app.flow_local_runner import (
    HEADED_WORKER_ID, WORKER_ID as LOCAL_WORKER_ID, launch_local_worker, stop_headed_worker,
)
from app.flow_sql import configuration_status as sql_configuration_status, discover_catalog as discover_sql_catalog
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/flows", tags=["flows"])

CONTROL_TYPES = {"select", "multi_select", "text", "week"}
DOWNLOAD_MODES = {"single", "one_per_period", "one_per_week"}
PERIOD_STRATEGIES = {"latest", "fixed", "rolling"}
FILE_FORMATS = {"csv"}
SQL_MODES = {"append", "replace"}
SCHEDULE_TYPES = {"manual", "daily", "weekly"}
BROWSER_MODES = {"headless", "headed"}
TRANSFORM_SCRIPT_SUFFIXES = {".py", ".ps1", ".exe"}
RUN_STALE_TIMEOUT_SECONDS = 600
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
RUN_TERMINAL = {"succeeded", "failed", "cancelled"}
RUN_STATUSES = {"queued", "claimed", "running", *RUN_TERMINAL}
ASAP_PORTAL_ADAPTER = "asap_portal"
WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>0[1-9]|[1-4]\d|5[0-3])$")
FILENAME_TOKEN_RE = re.compile(r"\{(flow|report|week|start_period|end_period|year|week_number|index|date)\}")
SAFE_NAME_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def fail_stale_runs(timeout_seconds: int = RUN_STALE_TIMEOUT_SECONDS) -> dict:
    """Fail work whose assigned browser stopped heartbeating."""
    now = _now()
    now_text = _iso(now)
    cutoff = _iso(now - timedelta(seconds=timeout_seconds))
    failed = []
    message = "The assigned browser worker stopped responding before the run finished."
    with get_db() as db:
        rows = db.execute(
            """SELECT r.id, r.flow_id, r.worker_id
               FROM flow_runs r
               LEFT JOIN flow_workers w ON w.worker_id=r.worker_id
               WHERE r.status IN ('claimed','running')
                 AND COALESCE(w.last_seen_at, r.heartbeat_at, r.claimed_at) < ?""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            db.execute(
                """UPDATE flow_runs SET status='failed', error=?, finished_at=?, heartbeat_at=?
                   WHERE id=? AND status IN ('claimed','running')""",
                (message, now_text, now_text, row["id"]),
            )
            db.execute(
                """INSERT INTO flow_run_events
                   (run_id, status, stage, message, details_json, error, traceback, created_at)
                   VALUES (?, 'failed', 'worker_lost', ?, '{}', ?, NULL, ?)""",
                (row["id"], message, message, now_text),
            )
            db.execute(
                """UPDATE flows SET last_run_at=?, last_status='failed', last_error=?, updated_at=?
                   WHERE id=?""",
                (now_text, message, now_text, row["flow_id"]),
            )
            if row["worker_id"]:
                db.execute(
                    """UPDATE flow_workers SET status='offline', current_run_id=NULL,
                       last_error=?, updated_at=? WHERE worker_id=?""",
                    (message, now_text, row["worker_id"]),
                )
            failed.append(row["id"])
    return {"failed_run_ids": failed, "count": len(failed)}


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


def _clean_filename_template(value: str, file_format: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter a filename template.")
    sample = FILENAME_TOKEN_RE.sub("sample", value)
    expected = ".csv"
    if not sample.casefold().endswith(expected):
        raise ValueError(f"The filename template must end in {expected}.")
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


def _week_window(start: str, count: int) -> list[str]:
    start_match = WEEK_RE.fullmatch(start)
    if not start_match:
        raise ValueError("Start week must use YYYY-Www format.")
    try:
        current = datetime.fromisocalendar(int(start_match["year"]), int(start_match["week"]), 1)
    except ValueError as exc:
        raise ValueError("The selected start week does not exist in that year.") from exc
    result = []
    for _ in range(count):
        year, week, _ = current.isocalendar()
        result.append(f"{year:04d}-W{week:02d}")
        current += timedelta(days=7)
    return result


def _periods(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


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


def _next_weekly_scan(weekday: str, time_value: str, now: datetime | None = None) -> datetime:
    now = now or _now()
    hour, minute = (int(part) for part in time_value.split(":"))
    target = WEEKDAYS[weekday]
    for offset in range(8):
        candidate_date = now.date() + timedelta(days=offset)
        if candidate_date.weekday() != target:
            continue
        candidate = datetime.combine(candidate_date, datetime.min.time()).replace(hour=hour, minute=minute)
        if candidate > now:
            return candidate
    raise ValueError("Could not calculate the next discovery scan.")


class SiteWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    adapter: str = Field(default="web_export", min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=2000)
    auth_url: str | None = Field(default=None, max_length=2000)
    discovery_enabled: bool = False
    discovery_interval_hours: int = Field(default=168, ge=168, le=168)
    discovery_scope: list[str] = Field(default_factory=lambda: ["Mobile"], min_length=1, max_length=20)
    discovery_weekday: str = "saturday"
    discovery_time: str = "06:00"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_urls(self):
        self.name = self.name.strip()
        self.adapter = self.adapter.strip()
        if self.base_url:
            self.base_url = _validate_http_url(self.base_url, "Base URL")
        if self.auth_url:
            self.auth_url = _validate_http_url(self.auth_url, "Authentication URL")
        self.discovery_scope = list(dict.fromkeys(item.strip() for item in self.discovery_scope if item.strip()))
        if not self.discovery_scope:
            raise ValueError("Choose at least one ASAP menu to scan.")
        self.discovery_weekday = self.discovery_weekday.strip().casefold()
        if self.discovery_weekday not in WEEKDAYS:
            raise ValueError("Choose a valid discovery weekday.")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.discovery_time):
            raise ValueError("Discovery time must use HH:MM format.")
        return self


class CredentialWrite(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def clean_credentials(self):
        self.username = self.username.strip()
        if not self.username or not self.password:
            raise ValueError("ASAP username and password are required.")
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
    automation: dict[str, Any] = Field(default_factory=dict)
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
        if len(_json(self.automation)) > 20000:
            raise ValueError("Report automation configuration is too large.")
        path = self.automation.get("category_path")
        if path is not None:
            if not isinstance(path, list) or len(path) < 2 or not all(isinstance(item, str) and item.strip() for item in path):
                raise ValueError("Report menu path needs at least a category and report name.")
            self.automation["category_path"] = [item.strip() for item in path]
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
    period_strategy: str = "latest"
    window_weeks: int | None = Field(default=None, ge=1, le=105)
    file_format: str = "csv"
    browser_mode: str = "headless"
    start_week: str | None = None
    end_week: str | None = None
    target_folder: str = Field(min_length=1, max_length=2000)
    filename_template: str = Field(min_length=1, max_length=500)
    schedule_type: str = "manual"
    schedule_time: str | None = None
    schedule_days: list[str] = Field(default_factory=list)
    transform_enabled: bool = False
    transform_script_path: str | None = Field(default=None, max_length=2000)
    sql_handoff_enabled: bool = False
    sql_mode: str | None = None
    sql_database: str | None = None
    sql_schema: str | None = None
    sql_table: str | None = None

    @model_validator(mode="after")
    def validate_flow(self):
        self.name = self.name.strip()
        self.target_folder = self.target_folder.strip()
        self.file_format = self.file_format.strip().casefold()
        if self.file_format not in FILE_FORMATS:
            raise ValueError("Download type must be CSV.")
        self.filename_template = _clean_filename_template(self.filename_template, self.file_format)
        if not _is_absolute_worker_path(self.target_folder):
            raise ValueError("Target folder must be an absolute path visible to the worker.")
        if self.download_mode not in DOWNLOAD_MODES:
            raise ValueError("Unsupported download mode.")
        if self.download_mode == "one_per_week":
            self.download_mode = "one_per_period"
            self.window_weeks = self.window_weeks or 1
        if self.period_strategy not in PERIOD_STRATEGIES:
            raise ValueError("Unsupported period strategy.")
        if self.browser_mode not in BROWSER_MODES:
            raise ValueError("Browser mode must be headed or headless.")
        if self.schedule_type not in SCHEDULE_TYPES:
            raise ValueError("Unsupported schedule type.")
        self.start_week = _week_value(self.start_week, "Start week")
        self.end_week = _week_value(self.end_week, "End week")
        if not self.start_week:
            raise ValueError("Choose a Sell-out Week start.")
        if self.period_strategy == "fixed":
            if not self.end_week:
                raise ValueError("Choose a Sell-out Week end.")
            _week_range(self.start_week, self.end_week)
            if self.download_mode == "one_per_period" and not self.window_weeks:
                raise ValueError("Choose how many weeks each period should contain.")
            if self.download_mode == "single":
                self.window_weeks = None
        elif self.period_strategy == "rolling":
            if not self.window_weeks:
                raise ValueError("Choose how many weeks each file should contain.")
            self.download_mode = "one_per_period"
            self.end_week = None
        else:
            self.end_week = None
            if self.download_mode == "one_per_period" and not self.window_weeks:
                raise ValueError("Choose how many weeks each download should contain.")
            if self.download_mode == "single":
                self.window_weeks = None
        if self.download_mode == "one_per_period" and not any(
            token in self.filename_template for token in ("{week}", "{start_period}", "{end_period}", "{index}")
        ):
            raise ValueError("Multiple downloads require a period or index token in the filename template.")
        self.schedule_days = [str(day).strip().casefold() for day in self.schedule_days]
        _schedule_next(self.schedule_type, self.schedule_time, self.schedule_days)
        if self.transform_enabled:
            self.transform_script_path = (self.transform_script_path or "").strip()
            if not self.transform_script_path:
                raise ValueError("Choose a transformation script.")
            if not _is_absolute_worker_path(self.transform_script_path):
                raise ValueError("Transformation script must use an absolute path visible to the BI desktop worker.")
            if Path(self.transform_script_path).suffix.casefold() not in TRANSFORM_SCRIPT_SUFFIXES:
                raise ValueError("Transformation script must be a .py, .ps1, or .exe file.")
        else:
            self.transform_script_path = None
        if self.sql_handoff_enabled:
            self.sql_mode = (self.sql_mode or "").strip().casefold()
            if self.sql_mode not in SQL_MODES:
                raise ValueError("SQL write mode must be append or recreate and replace.")
            for field_name in ("sql_database", "sql_schema", "sql_table"):
                value = (getattr(self, field_name) or "").strip()
                if not value:
                    raise ValueError("Choose a discovered SQL database, schema, and table.")
                setattr(self, field_name, value)
        else:
            self.sql_mode = self.sql_database = self.sql_schema = self.sql_table = None
        return self


class WorkerRegister(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class WorkerProgress(BaseModel):
    status: Literal["running", "succeeded", "failed", "cancelled"]
    progress: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    timings: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    error: str | None = Field(default=None, max_length=10000)
    traceback: str | None = Field(default=None, max_length=100000)


class FlowEnabledWrite(BaseModel):
    enabled: bool


class DiscoveredFilter(BaseModel):
    filter_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=200)
    control_label: str = Field(min_length=1, max_length=300)
    control_type: str
    options: list[str] = Field(default_factory=list, max_length=2000)
    automation: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    position: int = Field(default=0, ge=0, le=1000)

    @field_validator("control_type")
    @classmethod
    def valid_control_type(cls, value: str):
        if value not in CONTROL_TYPES:
            raise ValueError("Unsupported discovered control type.")
        return value


class DiscoveredReport(BaseModel):
    discovery_key: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=200)
    report_url: str = Field(min_length=1, max_length=4000)
    ready_text: str | None = Field(default=None, max_length=500)
    download_text: str = Field(default="Export CSV", min_length=1, max_length=500)
    automation: dict[str, Any] = Field(default_factory=dict)
    filters: list[DiscoveredFilter] = Field(default_factory=list, max_length=200)


class ScanProgress(BaseModel):
    status: Literal["running", "succeeded", "failed", "cancelled"]
    progress: dict[str, Any] = Field(default_factory=dict)
    reports: list[DiscoveredReport] = Field(default_factory=list, max_length=1000)
    timings: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    complete: bool = True
    error: str | None = Field(default=None, max_length=10000)


def _filter_row(row) -> dict:
    return {
        "id": row["id"], "filter_key": row["filter_key"], "label": row["label"],
        "control_label": row["control_label"], "control_type": row["control_type"],
        "options": _loads(row["options_json"], []),
        "automation": _loads(row["automation_json"], {}),
        "required": bool(row["required"]), "position": row["position"],
        "enabled": bool(row["enabled"]),
        "source_kind": row["source_kind"] if "source_kind" in row.keys() else "manual",
        "last_seen_at": row["last_seen_at"] if "last_seen_at" in row.keys() else None,
        "stale": bool(row["stale"]) if "stale" in row.keys() else False,
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
    result["automation"] = _loads(result.pop("automation_json", None), {})
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
    result["transform_enabled"] = bool(result.get("transform_enabled"))
    result["selections"] = _loads(result.pop("selections_json"), {})
    result["schedule_days"] = _loads(result.pop("schedule_days"), [])
    if result["download_mode"] == "one_per_week":
        result["download_mode"] = "one_per_period"
        result["window_weeks"] = result["window_weeks"] or 1
    return result


def _latest_discovered_week(report: dict, start_week: str) -> str:
    """Return the newest week exposed by the report's latest discovery scan."""
    available = []
    for definition in report.get("filters", []):
        if definition.get("control_type") != "week" or definition.get("stale"):
            continue
        for option in definition.get("options") or []:
            raw = str(option).strip()
            value = f"{raw[:4]}-W{raw[4:]}" if re.fullmatch(r"\d{6}", raw) else raw
            if WEEK_RE.fullmatch(value):
                available.append(value)
    available = sorted(set(available))
    if not available:
        raise HTTPException(409, "The latest ASAP scan did not discover Sell-out Week options. Refresh this report first.")
    latest = available[-1]
    if latest < start_week:
        raise HTTPException(409, f"Start week is after the latest discovered ASAP week ({latest}).")
    return latest


def _validate_sql_target(db, body: FlowWrite):
    if not body.sql_handoff_enabled:
        return
    row = db.execute(
        """SELECT 1 FROM flow_sql_catalog
           WHERE database_name=? AND schema_name=? AND table_name=? AND stale=0""",
        (body.sql_database, body.sql_schema, body.sql_table),
    ).fetchone()
    if not row:
        raise HTTPException(400, "Choose a database, schema, and table from the latest SQL catalog scan.")


def _validate_flow_selections(db, body: FlowWrite):
    report = db.execute(
        """SELECT site_id FROM flow_reports
           WHERE id = ? AND enabled = 1 AND stale = 0 AND source_kind = 'discovered'""",
        (body.report_id,),
    ).fetchone()
    if not report or report["site_id"] != body.site_id:
        raise HTTPException(400, "Choose a report discovered from the selected website.")
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
        if row["required"] and not present and not (row["control_type"] == "week" and body.start_week):
            raise HTTPException(400, f"Choose {row['label']}.")
        comparable = [item.replace("-W", "") if row["control_type"] == "week" else item for item in present]
        invalid = [present[index] for index, item in enumerate(comparable) if options and item not in options]
        if invalid:
            raise HTTPException(400, f"Invalid {row['label']} value: {invalid[0]}")


def _build_job(db, flow_id: int) -> dict:
    flow = _flow_out(db, flow_id)
    report = _report_out(db, flow["report_id"])
    if flow.get("period_strategy") == "rolling":
        weeks = _week_window(flow["start_week"], flow["window_weeks"])
    else:
        end_week = (
            _latest_discovered_week(report, flow["start_week"])
            if flow.get("period_strategy") == "latest"
            else flow["end_week"]
        )
        weeks = _week_range(flow["start_week"], end_week)
    if flow.get("period_strategy") == "rolling" or flow["download_mode"] == "single":
        periods = [weeks]
    else:
        periods = _periods(weeks, flow.get("window_weeks") or 1)
    return {
        "schema_version": 1,
        "execution": {
            "mode": "local", "host": "bi_desktop", "browser_mode": flow["browser_mode"],
            "worker_id": HEADED_WORKER_ID if flow["browser_mode"] == "headed" else LOCAL_WORKER_ID,
        },
        "flow": {"id": flow["id"], "name": flow["name"]},
        "site": {
            "id": flow["site_id"], "name": flow["site_name"],
            "adapter": report["adapter"], "auth_url": report["auth_url"],
        },
        "report": {
            "id": report["id"], "name": report["name"], "url": report["report_url"],
            "ready_text": report["ready_text"], "open_export_text": report["open_export_text"],
            "download_text": report["download_text"], "automation": report["automation"],
            "filters": report["filters"],
        },
        "selections": flow["selections"],
        "downloads": {
            "mode": flow["download_mode"], "periods": periods,
            "period_strategy": flow.get("period_strategy") or "fixed",
            "period_unit": "week",
            "period_size": flow.get("window_weeks") or len(weeks),
            "file_format": flow.get("file_format") or "csv",
            "period_start_week": weeks[0],
            "period_end_week": weeks[-1],
            "next_start_week": _week_window(weeks[-1], 2)[1],
            "target_folder": flow["target_folder"],
            "filename_template": flow["filename_template"],
            "collision_policy": "number_suffix",
            "delete_existing": False,
            "overwrite_existing": False,
        },
        "transformation": {
            "enabled": bool(flow.get("transform_enabled")),
            "script_path": flow.get("transform_script_path"),
            "output_subfolder": "script_results",
            "input_argument": "--input",
            "output_argument": "--output",
        },
        "sql_handoff": {
            "enabled": bool(flow.get("sql_handoff_enabled")),
            "mode": flow.get("sql_mode"),
            "database": flow.get("sql_database"),
            "schema": flow.get("sql_schema"),
            "table": flow.get("sql_table"),
        },
    }


def queue_due_flows() -> dict:
    """Queue due scheduled flows without executing browser work in the API process."""
    now = _now()
    now_text = _iso(now)
    queued = []
    modes = set()
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
            modes.add(job["execution"]["browser_mode"])
    workers = [launch_local_worker(mode) for mode in sorted(modes)]
    if not workers:
        worker = {"status": "not_needed", "mode": "local"}
    elif len(workers) == 1:
        worker = workers[0]
    else:
        worker = {"status": "starting", "workers": workers}
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
        site["discovery_enabled"] = bool(site.get("discovery_enabled"))
        site["discovery_scope"] = _loads(site.pop("discovery_scope_json", None), ["Mobile"])
        site["credentials_configured"] = (
            asap_credential_status()["configured"] if site["adapter"] == ASAP_PORTAL_ADAPTER else False
        )
    for report in reports:
        report["enabled"] = bool(report["enabled"])
        report["stale"] = bool(report.get("stale"))
        report["automation"] = _loads(report.pop("automation_json", None), {})
        report["filters"] = by_report.get(report["id"], [])
    return {"sites": sites, "reports": reports, "control_types": sorted(CONTROL_TYPES)}


@router.post("/sites/{site_id}/credentials")
def configure_site_credentials(site_id: int, body: CredentialWrite, request: Request):
    with get_db() as db:
        site = db.execute("SELECT id, name, adapter FROM flow_sites WHERE id=?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "Website not found.")
        if site["adapter"] != ASAP_PORTAL_ADAPTER:
            raise HTTPException(400, "Local credential storage is only supported for ASAP.")
    try:
        result = save_asap_credentials(body.username, body.password)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(500, f"ASAP credentials were not stored: {exc}") from exc
    with get_db() as db:
        log_event(db, "flow_site", site_id, site["name"], "credentials_configured", actor=get_actor(request))
    return result


@router.post("/sites")
def create_site(body: SiteWrite, request: Request):
    now = _iso(_now())
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO flow_sites
                   (name, adapter, base_url, auth_url, discovery_enabled, discovery_interval_hours,
                    discovery_scope_json, discovery_weekday, discovery_time, next_scan_at,
                    enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (body.name, body.adapter, body.base_url, body.auth_url, body.discovery_enabled,
                 body.discovery_interval_hours, _json(body.discovery_scope), body.discovery_weekday,
                 body.discovery_time, _iso(_next_weekly_scan(body.discovery_weekday, body.discovery_time))
                 if body.discovery_enabled else None, body.enabled, now, now),
            )
            log_event(db, "flow_site", cursor.lastrowid, body.name, "created", actor=get_actor(request))
            row = db.execute("SELECT * FROM flow_sites WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A website with that name already exists.") from exc
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["discovery_enabled"] = bool(result["discovery_enabled"])
    result["discovery_scope"] = _loads(result.pop("discovery_scope_json", None), ["Mobile"])
    return result


@router.put("/sites/{site_id}")
def update_site(site_id: int, body: SiteWrite, request: Request):
    with get_db() as db:
        cursor = db.execute(
            """UPDATE flow_sites SET name=?, adapter=?, base_url=?, auth_url=?, discovery_enabled=?,
               discovery_interval_hours=?, discovery_scope_json=?,
               discovery_weekday=?, discovery_time=?,
               next_scan_at=CASE WHEN ? THEN ? ELSE NULL END,
               enabled=?, updated_at=?
               WHERE id=?""",
            (body.name, body.adapter, body.base_url, body.auth_url, body.discovery_enabled,
             body.discovery_interval_hours, _json(body.discovery_scope), body.discovery_weekday,
             body.discovery_time, body.discovery_enabled,
             _iso(_next_weekly_scan(body.discovery_weekday, body.discovery_time)),
             body.enabled, _iso(_now()), site_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Website not found.")
        log_event(db, "flow_site", site_id, body.name, "updated", actor=get_actor(request))
        row = db.execute("SELECT * FROM flow_sites WHERE id = ?", (site_id,)).fetchone()
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["discovery_enabled"] = bool(result["discovery_enabled"])
    result["discovery_scope"] = _loads(result.pop("discovery_scope_json", None), ["Mobile"])
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
                   (site_id, name, report_url, ready_text, open_export_text, download_text,
                    automation_json, notes, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (body.site_id, body.name, body.report_url, body.ready_text, body.open_export_text,
                 body.download_text, _json(body.automation), body.notes, body.enabled, now, now),
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
               download_text=?, automation_json=?, notes=?, enabled=?, updated_at=? WHERE id=?""",
            (body.site_id, body.name, body.report_url, body.ready_text, body.open_export_text,
             body.download_text, _json(body.automation), body.notes, body.enabled, now, report_id),
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
        result = []
        for row in rows:
            timings = db.execute(
                "SELECT phase, duration_ms, item_count, status FROM flow_operation_timings WHERE run_id=? ORDER BY id",
                (row["id"],),
            ).fetchall()
            result.append({
                **dict(row), "job": _loads(row["job_json"], {}),
                "progress": _loads(row["progress_json"], {}),
                "artifacts": _loads(row["artifact_json"], []),
                "timings": [dict(item) for item in timings],
            })
        return result


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    with get_db() as db:
        row = db.execute(
            """SELECT r.*, f.name AS flow_name FROM flow_runs r
               JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (run_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run not found.")
        timings = db.execute(
            """SELECT phase, duration_ms, item_count, status, metadata_json, recorded_at
               FROM flow_operation_timings WHERE run_id=? ORDER BY id""",
            (run_id,),
        ).fetchall()
        events = db.execute(
            """SELECT id, status, stage, message, details_json, error, traceback, created_at
               FROM flow_run_events WHERE run_id=? ORDER BY id""",
            (run_id,),
        ).fetchall()
        files = db.execute(
            """SELECT period_key, file_path, filename, file_size, checksum, row_count, status, created_at
               FROM flow_run_files WHERE run_id=? ORDER BY id""",
            (run_id,),
        ).fetchall()
        return {
            **dict(row),
            "job": _loads(row["job_json"], {}),
            "progress": _loads(row["progress_json"], {}),
            "artifacts": _loads(row["artifact_json"], []),
            "timings": [
                {**dict(item), "metadata": _loads(item["metadata_json"], {})}
                for item in timings
            ],
            "events": [
                {**dict(item), "details": _loads(item["details_json"], {})}
                for item in events
            ],
            "files": [dict(item) for item in files],
        }


@router.post("/runs/{run_id}/retry-sql")
def retry_run_sql(run_id: int, request: Request):
    """Queue SQL only from a terminal run's saved SQL-ready CSV artifacts."""
    now = _iso(_now())
    with get_db() as db:
        source = db.execute(
            """SELECT r.*, f.name AS flow_name FROM flow_runs r
               JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (run_id,),
        ).fetchone()
        if not source:
            raise HTTPException(404, "Source flow run not found.")
        if source["status"] not in RUN_TERMINAL:
            raise HTTPException(409, "Wait for the source run to finish before retrying SQL.")
        source_job = _loads(source["job_json"], {})
        sql_target = source_job.get("sql_handoff", {})
        if not sql_target.get("enabled"):
            raise HTTPException(400, "The source run did not have SQL handoff enabled.")
        source_artifacts = _loads(source["artifact_json"], [])
        transformed = bool(source_job.get("transformation", {}).get("enabled"))
        if transformed:
            candidates = [item for item in source_artifacts if item.get("status") == "transformed"]
        else:
            candidates = [item for item in source_artifacts if item.get("status") != "transformed"]
        artifacts = [
            {
                key: item.get(key)
                for key in ("file_path", "filename", "period_key", "file_size", "checksum", "row_count", "status")
                if item.get(key) is not None
            }
            for item in candidates if item.get("file_path") and item.get("filename")
        ]
        if not artifacts:
            raise HTTPException(409, "The source run has no saved SQL-ready CSV artifacts.")
        missing = [item["filename"] for item in artifacts if not Path(item["file_path"]).is_file()]
        if missing:
            raise HTTPException(
                409,
                f"Saved SQL artifact is no longer available on the BI desktop: {', '.join(missing[:10])}",
            )
        active = db.execute(
            """SELECT id FROM flow_runs WHERE flow_id=?
               AND status IN ('queued','claimed','running') LIMIT 1""",
            (source["flow_id"],),
        ).fetchone()
        if active:
            raise HTTPException(409, "This flow already has an active run.")

        job = copy.deepcopy(source_job)
        job["job_type"] = "sql_retry"
        job["execution"] = {
            "mode": "local", "host": "bi_desktop", "browser_mode": "headless",
            "worker_id": LOCAL_WORKER_ID,
        }
        job["transformation"] = {
            "enabled": False,
            "source_run_id": run_id,
            "source_was_transformed": transformed,
        }
        job["sql_retry"] = {"source_run_id": run_id, "artifacts": artifacts}
        cursor = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, requested_by, job_json, created_at)
               VALUES (?, 'sql_retry', 'queued', ?, ?, ?)""",
            (source["flow_id"], get_actor(request), _json(job), now),
        )
        new_run_id = cursor.lastrowid
        log_event(
            db, "flow", source["flow_id"], source["flow_name"], "sql_retry_queued",
            f"source_run_id={run_id}; run_id={new_run_id}; files={len(artifacts)}",
            get_actor(request),
        )
    worker = launch_local_worker("headless")
    if worker.get("status") == "error":
        with get_db() as db:
            db.execute(
                "UPDATE flow_runs SET progress_json=? WHERE id=?",
                (_json({"stage": "waiting_for_bi_desktop", "message": worker.get("message")}), new_run_id),
            )
    return {
        "id": new_run_id,
        "flow_id": source["flow_id"],
        "status": "queued",
        "job": job,
        "worker": worker,
        "source_run_id": run_id,
    }


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


@router.get("/sql/catalog")
def sql_catalog():
    status = sql_configuration_status()
    with get_db() as db:
        rows = db.execute(
            """SELECT database_name, schema_name, table_name
               FROM flow_sql_catalog WHERE stale=0
               ORDER BY database_name, schema_name, table_name"""
        ).fetchall()
        state = db.execute("SELECT * FROM flow_sql_catalog_state WHERE id=1").fetchone()
    return {
        **status,
        "targets": [
            {"database": row["database_name"], "schema": row["schema_name"], "table": row["table_name"]}
            for row in rows
        ],
        "scan": dict(state) if state else {"status": "never_scanned"},
    }


def refresh_sql_catalog() -> dict:
    """Refresh read-only SQL target metadata using the existing write role."""
    now = _iso(_now())
    try:
        result = discover_sql_catalog()
        with get_db() as db:
            db.execute("UPDATE flow_sql_catalog SET stale=1")
            for item in result["targets"]:
                db.execute(
                    """INSERT INTO flow_sql_catalog
                       (database_name, schema_name, table_name, last_seen_at, stale)
                       VALUES (?, ?, ?, ?, 0)
                       ON CONFLICT(database_name, schema_name, table_name) DO UPDATE SET
                         last_seen_at=excluded.last_seen_at, stale=0""",
                    (item["database"], item["schema"], item["table"], now),
                )
            error = "; ".join(f"{item['database']}: {item['error']}" for item in result["errors"]) or None
            db.execute(
                """INSERT INTO flow_sql_catalog_state
                   (id, status, last_scan_at, duration_ms, target_count, error)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     last_scan_at=excluded.last_scan_at, duration_ms=excluded.duration_ms,
                     target_count=excluded.target_count, error=excluded.error""",
                ("partial" if result["errors"] else "succeeded", now, result["duration_ms"], len(result["targets"]), error),
            )
        return {**result, "status": "partial" if result["errors"] else "succeeded", "last_scan_at": now}
    except Exception as exc:
        logging.getLogger(__name__).exception("SQL catalog scan failed")
        with get_db() as db:
            db.execute(
                """INSERT INTO flow_sql_catalog_state (id, status, last_scan_at, target_count, error)
                   VALUES (1, 'failed', ?, 0, ?)
                   ON CONFLICT(id) DO UPDATE SET status='failed', last_scan_at=excluded.last_scan_at,
                     error=excluded.error""",
                (now, str(exc)[:5000]),
            )
        return {"status": "failed", "last_scan_at": now, "error": str(exc)}


@router.post("/sql/catalog/refresh")
def refresh_sql_catalog_now(request: Request):
    result = refresh_sql_catalog()
    if result["status"] == "failed":
        raise HTTPException(502, result["error"])
    with get_db() as db:
        log_event(db, "flow_sql_catalog", None, "SQL targets", "refreshed", f"targets={len(result['targets'])}", get_actor(request))
    return result


@router.post("")
def create_flow(body: FlowWrite, request: Request):
    now = _iso(_now())
    next_run = _iso(_schedule_next(body.schedule_type, body.schedule_time, body.schedule_days)) if body.enabled else None
    try:
        with get_db() as db:
            _validate_flow_selections(db, body)
            _validate_sql_target(db, body)
            cursor = db.execute(
                """INSERT INTO flows
                   (name, site_id, report_id, enabled, selections_json, download_mode, period_strategy, window_weeks, file_format, start_week, end_week,
                    browser_mode, target_folder, filename_template, schedule_type, schedule_time, schedule_days, next_run_at,
                    transform_enabled, transform_script_path, sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (body.name, body.site_id, body.report_id, body.enabled, _json(body.selections),
                 body.download_mode, body.period_strategy, body.window_weeks, body.file_format, body.start_week, body.end_week, body.browser_mode, body.target_folder,
                 body.filename_template, body.schedule_type, body.schedule_time,
                 _json(body.schedule_days), next_run, body.transform_enabled, body.transform_script_path,
                 body.sql_handoff_enabled, body.sql_mode,
                 body.sql_database, body.sql_schema, body.sql_table, get_actor(request), now, now),
            )
            flow_id = cursor.lastrowid
            log_event(db, "flow", flow_id, body.name, "created", f"sql_handoff={body.sql_handoff_enabled}", get_actor(request))
            return _flow_out(db, flow_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A flow with that name already exists.") from exc


@router.post("/transform-script")
async def add_transform_script(request: Request, file: UploadFile = File(...)):
    """Store a user-selected script locally without committing it to the repository."""
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.casefold()
    if not filename or suffix not in TRANSFORM_SCRIPT_SUFFIXES:
        raise HTTPException(400, "Choose a .py, .ps1, or .exe transformation script.")
    content = await file.read(10 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(400, "The selected transformation script is empty.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Transformation scripts must be 10 MB or smaller.")
    folder = Path(DB_PATH).resolve().parent / "flow_scripts"
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / filename
    if candidate.exists():
        stem = candidate.stem
        for index in range(2, 10000):
            candidate = folder / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                break
        else:
            raise HTTPException(409, "Could not reserve a unique script filename.")
    candidate.write_bytes(content)
    with get_db() as db:
        log_event(
            db, "flow_transform_script", None, candidate.name,
            "added", f"size={len(content)}", get_actor(request),
        )
    return {"script_path": str(candidate), "filename": candidate.name, "file_size": len(content)}


@router.put("/{flow_id}")
def update_flow(flow_id: int, body: FlowWrite, request: Request):
    now = _iso(_now())
    next_run = _iso(_schedule_next(body.schedule_type, body.schedule_time, body.schedule_days)) if body.enabled else None
    with get_db() as db:
        _validate_flow_selections(db, body)
        _validate_sql_target(db, body)
        cursor = db.execute(
            """UPDATE flows SET name=?, site_id=?, report_id=?, enabled=?, selections_json=?,
               download_mode=?, period_strategy=?, window_weeks=?, file_format=?, start_week=?, end_week=?, browser_mode=?, target_folder=?, filename_template=?,
               schedule_type=?, schedule_time=?, schedule_days=?, next_run_at=?,
               transform_enabled=?, transform_script_path=?,
               sql_handoff_enabled=?, sql_mode=?, sql_database=?, sql_schema=?, sql_table=?, updated_at=? WHERE id=?""",
            (body.name, body.site_id, body.report_id, body.enabled, _json(body.selections),
             body.download_mode, body.period_strategy, body.window_weeks, body.file_format, body.start_week, body.end_week, body.browser_mode, body.target_folder,
             body.filename_template, body.schedule_type, body.schedule_time,
             _json(body.schedule_days), next_run, body.transform_enabled, body.transform_script_path,
             body.sql_handoff_enabled, body.sql_mode,
             body.sql_database, body.sql_schema, body.sql_table, now, flow_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Flow not found.")
        log_event(db, "flow", flow_id, body.name, "updated", actor=get_actor(request))
        return _flow_out(db, flow_id)


@router.patch("/{flow_id}/enabled")
def set_flow_enabled(flow_id: int, body: FlowEnabledWrite, request: Request):
    now = _iso(_now())
    with get_db() as db:
        flow = db.execute("SELECT * FROM flows WHERE id=?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        if body.enabled and flow["schedule_type"] == "manual":
            raise HTTPException(400, "Choose a daily or weekly schedule before activating this flow.")
        next_run = (
            _iso(_schedule_next(flow["schedule_type"], flow["schedule_time"], _loads(flow["schedule_days"], [])))
            if body.enabled else None
        )
        db.execute(
            "UPDATE flows SET enabled=?, next_run_at=?, updated_at=? WHERE id=?",
            (body.enabled, next_run, now, flow_id),
        )
        log_event(
            db, "flow", flow_id, flow["name"],
            "activated" if body.enabled else "deactivated", actor=get_actor(request),
        )
        return _flow_out(db, flow_id)


@router.post("/{flow_id}/run")
def queue_run(flow_id: int, request: Request):
    now = _iso(_now())
    resumed = False
    with get_db() as db:
        flow = db.execute("SELECT id, name FROM flows WHERE id = ?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        active = db.execute(
            """SELECT id, status, job_json FROM flow_runs
               WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1""",
            (flow_id,),
        ).fetchone()
        if active:
            if active["status"] != "queued":
                raise HTTPException(409, "This flow already has an active run.")
            # A queued run may be waiting because Windows could not start its
            # worker. Let Run retry the launcher without creating a duplicate.
            run_id = active["id"]
            job = _loads(active["job_json"], {})
            resumed = True
            log_event(
                db, "flow", flow_id, flow["name"], "worker_restart_requested",
                f"run_id={run_id}", get_actor(request),
            )
        else:
            job = _build_job(db, flow_id)
            cursor = db.execute(
                """INSERT INTO flow_runs (flow_id, trigger_type, status, requested_by, job_json, created_at)
                   VALUES (?, 'manual', 'queued', ?, ?, ?)""",
                (flow_id, get_actor(request), _json(job), now),
            )
            run_id = cursor.lastrowid
            log_event(db, "flow", flow_id, flow["name"], "run_queued", f"run_id={run_id}", get_actor(request))
    worker = launch_local_worker(job["execution"]["browser_mode"])
    if worker.get("status") == "error":
        with get_db() as db:
            db.execute(
                "UPDATE flow_runs SET progress_json=? WHERE id=?",
                (_json({"stage": "waiting_for_bi_desktop", "message": worker.get("message")}), run_id),
            )
    return {
        "id": run_id, "flow_id": flow_id, "status": "queued", "job": job,
        "worker": worker, "resumed": resumed,
    }


@router.post("/{flow_id}/stop")
def stop_run(flow_id: int, request: Request):
    """Cancel a run and immediately close its exact headed worker tree."""
    now = _iso(_now())
    process_id = None
    run_id = None
    with get_db() as db:
        flow = db.execute("SELECT id, name FROM flows WHERE id=?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        row = db.execute(
            """SELECT * FROM flow_runs WHERE flow_id=?
               AND status IN ('queued','claimed','running') ORDER BY id DESC LIMIT 1""",
            (flow_id,),
        ).fetchone()
        if not row:
            raise HTTPException(409, "This flow has no active run to stop.")
        job = _loads(row["job_json"], {})
        if job.get("execution", {}).get("browser_mode") != "headed":
            raise HTTPException(400, "Immediate Stop is available only for headed browser runs.")
        run_id = row["id"]
        worker_id = row["worker_id"] or HEADED_WORKER_ID
        worker = db.execute("SELECT capabilities_json FROM flow_workers WHERE worker_id=?", (worker_id,)).fetchone()
        capabilities = _loads(worker["capabilities_json"], {}) if worker else {}
        raw_pid = capabilities.get("process_id")
        process_id = raw_pid if isinstance(raw_pid, int) and raw_pid > 0 else None
        message = "Stopped by user. The headed browser process was closed."
        db.execute(
            """UPDATE flow_runs SET status='cancelled', error=?, progress_json=?,
               finished_at=?, heartbeat_at=? WHERE id=?""",
            (message, _json({"stage": "cancelled", "message": message}), now, now, run_id),
        )
        db.execute(
            """UPDATE flows SET last_run_at=?, last_status='cancelled', last_error=?, updated_at=?
               WHERE id=?""",
            (now, message, now, flow_id),
        )
        db.execute(
            """UPDATE flow_workers SET status='offline', current_run_id=NULL, last_error=?, updated_at=?
               WHERE worker_id=?""",
            (message, now, worker_id),
        )
        log_event(db, "flow", flow_id, flow["name"], "run_cancelled", f"run_id={run_id}", get_actor(request))
    stopped = stop_headed_worker(process_id)
    return {"run_id": run_id, "status": "cancelled", "worker": stopped}


def ensure_local_worker() -> dict:
    """Restart the resident BI desktop worker when it is not online or busy."""
    cutoff = _iso(_now() - timedelta(seconds=90))
    with get_db() as db:
        row = db.execute(
            "SELECT status, current_run_id, current_scan_id, last_seen_at FROM flow_workers WHERE worker_id=?",
            (LOCAL_WORKER_ID,),
        ).fetchone()
    if row and (row["current_run_id"] or row["current_scan_id"]):
        return {"status": "busy", "mode": "local", "worker_id": LOCAL_WORKER_ID}
    if row and row["last_seen_at"] and row["last_seen_at"] >= cutoff:
        return {"status": "online", "mode": "local", "worker_id": LOCAL_WORKER_ID}
    return launch_local_worker("headless")


def _scan_out(row) -> dict:
    result = dict(row)
    result["job"] = _loads(result.pop("job_json", None), {})
    result["progress"] = _loads(result.pop("progress_json", None), {})
    result["result"] = _loads(result.pop("result_json", None), {})
    return result


def _queue_scan(db, site, trigger_type: str, requested_by: str | None, report=None) -> int:
    active = db.execute(
        "SELECT id FROM flow_catalog_scans WHERE site_id=? AND status IN ('queued','claimed','running') LIMIT 1",
        (site["id"],),
    ).fetchone()
    if active:
        return active["id"]
    job = {
        "schema_version": 1,
        "job_type": "catalog_scan",
        "site": {
            "id": site["id"], "name": site["name"], "adapter": site["adapter"],
            "base_url": site["base_url"], "auth_url": site["auth_url"],
        },
        "discovery": {
            "scope": ["*"], "delete_missing": False, "max_duration_minutes": 90,
            "report_paths": [
                _loads(report["automation_json"], {}).get("category_path", [])
            ] if report else [],
        },
    }
    cursor = db.execute(
        """INSERT INTO flow_catalog_scans
           (site_id, trigger_type, status, requested_by, job_json, created_at)
           VALUES (?, ?, 'queued', ?, ?, ?)""",
        (site["id"], trigger_type, requested_by, _json(job), _iso(_now())),
    )
    return cursor.lastrowid


@router.get("/scans")
def list_scans(site_id: int | None = None, limit: int = Query(default=50, ge=1, le=200)):
    with get_db() as db:
        sql = """SELECT c.*, s.name AS site_name FROM flow_catalog_scans c
                 JOIN flow_sites s ON s.id=c.site_id"""
        params: list[Any] = []
        if site_id is not None:
            sql += " WHERE c.site_id=?"
            params.append(site_id)
        sql += " ORDER BY c.created_at DESC, c.id DESC LIMIT ?"
        params.append(limit)
        result = []
        for row in db.execute(sql, params).fetchall():
            item = _scan_out(row)
            timings = db.execute(
                "SELECT phase, duration_ms, item_count, status FROM flow_operation_timings WHERE scan_id=? ORDER BY id",
                (row["id"],),
            ).fetchall()
            item["timings"] = [dict(timing) for timing in timings]
            result.append(item)
        return result


@router.post("/sites/{site_id}/scan")
def queue_catalog_scan(site_id: int, request: Request):
    with get_db() as db:
        site = db.execute("SELECT * FROM flow_sites WHERE id=? AND enabled=1", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "Website not found.")
        if site["adapter"] != ASAP_PORTAL_ADAPTER:
            raise HTTPException(400, "Automatic discovery is currently available for ASAP only.")
        scan_id = _queue_scan(db, site, "manual", get_actor(request))
        log_event(db, "flow_site", site_id, site["name"], "scan_queued", f"scan_id={scan_id}", get_actor(request))
    worker = launch_local_worker()
    return {"id": scan_id, "site_id": site_id, "status": "queued", "worker": worker}


@router.post("/reports/{report_id}/scan")
def queue_report_scan(report_id: int, request: Request):
    """Refresh one discovered report without staling or deleting other catalog entries."""
    with get_db() as db:
        report = db.execute(
            "SELECT * FROM flow_reports WHERE id=? AND source_kind='discovered'", (report_id,)
        ).fetchone()
        if not report:
            raise HTTPException(404, "Discovered report not found.")
        site = db.execute(
            "SELECT * FROM flow_sites WHERE id=? AND enabled=1", (report["site_id"],)
        ).fetchone()
        if not site or site["adapter"] != ASAP_PORTAL_ADAPTER:
            raise HTTPException(400, "Targeted discovery is currently available for ASAP only.")
        scan_id = _queue_scan(db, site, "report", get_actor(request), report)
        log_event(
            db, "flow_report", report_id, report["name"], "scan_queued",
            f"scan_id={scan_id}", get_actor(request),
        )
    worker = launch_local_worker()
    return {"id": scan_id, "site_id": site["id"], "report_id": report_id,
            "status": "queued", "worker": worker}


def queue_due_catalog_scans() -> dict:
    now = _now()
    queued = []
    with get_db() as db:
        sites = db.execute(
            """SELECT * FROM flow_sites WHERE enabled=1 AND discovery_enabled=1
               AND adapter=? AND (next_scan_at IS NULL OR next_scan_at <= ?) ORDER BY id""",
            (ASAP_PORTAL_ADAPTER, _iso(now)),
        ).fetchall()
        for site in sites:
            scan_id = _queue_scan(db, site, "scheduled", "scheduler")
            queued.append(scan_id)
            db.execute(
                "UPDATE flow_sites SET next_scan_at=?, updated_at=? WHERE id=?",
                (_iso(_next_weekly_scan(site["discovery_weekday"], site["discovery_time"], now)),
                 _iso(now), site["id"]),
            )
    worker = launch_local_worker() if queued else {"status": "not_needed", "mode": "local"}
    return {"queued": queued, "count": len(queued), "worker": worker}


def _apply_discovery(
    db, site_id: int, reports: list[DiscoveredReport], seen_at: str, *, complete: bool = True,
) -> dict:
    keys = {item.discovery_key for item in reports}
    if complete:
        db.execute(
            "UPDATE flow_reports SET stale=1, enabled=0, updated_at=? WHERE site_id=? AND source_kind='discovered'",
            (seen_at, site_id),
        )
    report_ids = []
    filter_count = 0
    for item in reports:
        category_path = item.automation.get("category_path") or []
        catalog_name = " > ".join(
            str(part).strip() for part in category_path if str(part).strip()
        ) or item.name.strip()
        existing = db.execute(
            """SELECT id FROM flow_reports WHERE site_id=?
               AND (discovery_key=? OR (discovery_key IS NULL AND name=?)) ORDER BY id LIMIT 1""",
            (site_id, item.discovery_key, catalog_name),
        ).fetchone()
        if existing:
            report_id = existing["id"]
            db.execute(
                """UPDATE flow_reports SET name=?, report_url=?, ready_text=?, download_text=?,
                   automation_json=?, discovery_key=?, source_kind='discovered', last_seen_at=?,
                   stale=0, enabled=1, updated_at=?
                   WHERE id=?""",
                (catalog_name, item.report_url, item.ready_text, item.download_text,
                 _json(item.automation), item.discovery_key, seen_at, seen_at, report_id),
            )
        else:
            cursor = db.execute(
                """INSERT INTO flow_reports
                   (site_id, name, report_url, ready_text, download_text, automation_json,
                    discovery_key, source_kind, last_seen_at, stale, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered', ?, 0, 1, ?, ?)""",
                (site_id, catalog_name, item.report_url, item.ready_text, item.download_text,
                 _json(item.automation), item.discovery_key, seen_at, seen_at, seen_at),
            )
            report_id = cursor.lastrowid
        report_ids.append(report_id)
        # A targeted refresh is authoritative for the report it inspected,
        # even though it is intentionally not authoritative for the rest of
        # the site catalog. Mark prior discovered definitions stale before
        # upserting the current set. Nothing is deleted.
        db.execute(
            "UPDATE flow_report_filters SET stale=1, enabled=0, updated_at=? WHERE report_id=? AND source_kind='discovered'",
            (seen_at, report_id),
        )
        for definition in item.filters:
            db.execute(
                """INSERT INTO flow_report_filters
                   (report_id, filter_key, label, control_label, control_type, options_json,
                    automation_json, required, position, source_kind, last_seen_at, stale,
                    enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, 0, 1, ?, ?)
                   ON CONFLICT(report_id, filter_key) DO UPDATE SET
                     label=excluded.label, control_label=excluded.control_label,
                     control_type=excluded.control_type, options_json=excluded.options_json,
                     automation_json=excluded.automation_json, required=excluded.required,
                     position=excluded.position, source_kind='discovered',
                     last_seen_at=excluded.last_seen_at, stale=0, enabled=1,
                     updated_at=excluded.updated_at""",
                (report_id, definition.filter_key, definition.label, definition.control_label,
                 definition.control_type, _json(definition.options), _json(definition.automation),
                 definition.required, definition.position, seen_at, seen_at, seen_at),
            )
            filter_count += 1
    return {
        "report_count": len(report_ids), "filter_count": filter_count,
        "discovery_keys": sorted(keys), "complete": complete,
    }


def _store_timings(
    db, timings: list[dict[str, Any]], *, operation_type: str, site_id: int | None = None,
    report_id: int | None = None, run_id: int | None = None, scan_id: int | None = None,
):
    rows = []
    for item in timings:
        phase = str(item.get("phase") or "").strip()
        duration_ms = item.get("duration_ms")
        if not phase or not isinstance(duration_ms, int) or duration_ms < 0:
            continue
        rows.append((
            operation_type, phase, run_id, scan_id, site_id, item.get("report_id") or report_id,
            duration_ms, item.get("item_count"), str(item.get("status") or "succeeded"),
            _json(item.get("metadata") or {}), _iso(_now()),
        ))
    db.executemany(
        """INSERT INTO flow_operation_timings
           (operation_type, phase, run_id, scan_id, site_id, report_id, duration_ms,
            item_count, status, metadata_json, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _period_key_text(value: Any) -> str | None:
    """Convert an artifact period into the TEXT form stored in SQLite."""
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


@router.get("/estimates")
def operation_estimates(site_id: int | None = None, report_id: int | None = None):
    estimates = {}
    with get_db() as db:
        for operation_type, fallback_ms in (("catalog_scan", 20 * 60_000), ("flow_download", 5 * 60_000)):
            phase_rows = db.execute(
                """SELECT DISTINCT phase FROM flow_operation_timings
                   WHERE operation_type=? ORDER BY phase""", (operation_type,)
            ).fetchall()
            phases = {}
            for phase in ["total", *(row["phase"] for row in phase_rows if row["phase"] != "total")]:
                sql = """SELECT duration_ms FROM flow_operation_timings
                         WHERE operation_type=? AND phase=? AND status='succeeded'"""
                params: list[Any] = [operation_type, phase]
                if site_id is not None:
                    sql += " AND site_id=?"
                    params.append(site_id)
                if report_id is not None and operation_type == "flow_download":
                    sql += " AND report_id=?"
                    params.append(report_id)
                sql += " ORDER BY recorded_at DESC LIMIT 10"
                values = [row["duration_ms"] for row in db.execute(sql, params).fetchall()]
                if not values and phase != "total":
                    continue
                estimate = sorted(values)[len(values) // 2] if values else fallback_ms
                phases[phase] = {"estimated_ms": estimate, "sample_count": len(values)}
            total = phases["total"]
            source = (
                f"median of {total['sample_count']} recent successful operation(s)"
                if total["sample_count"] else "conservative fallback until history exists"
            )
            estimates[operation_type] = {
                "estimated_ms": total["estimated_ms"], "sample_count": total["sample_count"],
                "source": source, "phases": phases,
            }
    return estimates


@router.post("/worker/register")
def register_worker(body: WorkerRegister):
    now = _iso(_now())
    interrupted_run_id = None
    with get_db() as db:
        existing_worker = db.execute(
            "SELECT * FROM flow_workers WHERE worker_id=?", (body.worker_id,)
        ).fetchone()
        previous_capabilities = _loads(
            existing_worker["capabilities_json"], {}
        ) if existing_worker else {}
        previous_pid = previous_capabilities.get("process_id")
        replacement_pid = body.capabilities.get("process_id")
        process_restarted = (
            existing_worker is not None
            and existing_worker["current_run_id"] is not None
            and previous_pid is not None
            and replacement_pid is not None
            and str(previous_pid) != str(replacement_pid)
        )
        if process_restarted:
            run = db.execute(
                "SELECT * FROM flow_runs WHERE id=?",
                (existing_worker["current_run_id"],),
            ).fetchone()
            if run and run["status"] not in RUN_TERMINAL:
                interrupted_run_id = run["id"]
                job = _loads(run["job_json"], {})
                sql_enabled = bool(job.get("sql_handoff", {}).get("enabled"))
                message = (
                    "The SQL worker restarted before it could confirm the prior run's terminal "
                    "outcome. Metronome did not replay the SQL mutation automatically; inspect "
                    "the target before retrying."
                    if sql_enabled else
                    "The browser worker restarted before the prior run finished. Metronome did "
                    "not replay the run automatically."
                )
                details = {
                    "stage": "worker_restarted",
                    "message": message,
                    "previous_process_id": previous_pid,
                    "replacement_process_id": replacement_pid,
                    "automatic_replay": False,
                    "sql_outcome": "unknown" if sql_enabled else None,
                }
                db.execute(
                    """UPDATE flow_runs SET status='failed', progress_json=?, error=?,
                       finished_at=?, heartbeat_at=? WHERE id=?""",
                    (_json(details), message, now, now, run["id"]),
                )
                db.execute(
                    """INSERT INTO flow_run_events
                       (run_id, status, stage, message, details_json, error, traceback, created_at)
                       VALUES (?, 'failed', 'worker_restarted', ?, ?, ?, NULL, ?)""",
                    (run["id"], message, _json(details), message, now),
                )
                db.execute(
                    """UPDATE flows SET last_run_at=?, last_status='failed', last_error=?, updated_at=?
                       WHERE id=?""",
                    (now, message, now, run["flow_id"]),
                )
            db.execute(
                """UPDATE flow_workers SET status='offline', current_run_id=NULL,
                   current_scan_id=NULL, updated_at=? WHERE worker_id=?""",
                (now, body.worker_id),
            )
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
    return {
        "worker_id": body.worker_id,
        "status": "idle",
        "interrupted_run_id": interrupted_run_id,
    }


@router.post("/worker/{worker_id}/claim")
def claim_run(worker_id: str):
    now = _iso(_now())
    with get_db() as db:
        worker = db.execute("SELECT * FROM flow_workers WHERE worker_id=?", (worker_id,)).fetchone()
        if not worker:
            raise HTTPException(404, "Register this worker before claiming work.")
        capabilities = _loads(worker["capabilities_json"], {})
        worker_mode = "headed" if capabilities.get("headed") else "headless"
        if worker["current_scan_id"]:
            scan = db.execute("SELECT * FROM flow_catalog_scans WHERE id=?", (worker["current_scan_id"],)).fetchone()
            if scan and scan["status"] not in RUN_TERMINAL:
                db.execute("UPDATE flow_workers SET last_seen_at=?, updated_at=? WHERE worker_id=?", (now, now, worker_id))
                return {"run": None, "scan": {**dict(scan), "job": _loads(scan["job_json"], {})}}
        if worker["current_run_id"]:
            row = db.execute("SELECT * FROM flow_runs WHERE id=?", (worker["current_run_id"],)).fetchone()
            if row and row["status"] not in RUN_TERMINAL:
                db.execute("UPDATE flow_workers SET last_seen_at=?, updated_at=? WHERE worker_id=?", (now, now, worker_id))
                return {"run": {**dict(row), "job": _loads(row["job_json"], {})}, "scan": None}
        queued_runs = db.execute(
            "SELECT * FROM flow_runs WHERE status='queued' ORDER BY created_at, id"
        ).fetchall()
        row = next((candidate for candidate in queued_runs if (
            _loads(candidate["job_json"], {}).get("execution", {}).get("browser_mode", "headless")
            == worker_mode
        )), None)
        if not row:
            # Discovery remains background-only. The interactive worker exists
            # to make a selected download visible while building or debugging.
            scan = db.execute(
                "SELECT * FROM flow_catalog_scans WHERE status='queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone() if worker_mode == "headless" else None
            if scan:
                cursor = db.execute(
                    """UPDATE flow_catalog_scans SET status='claimed', worker_id=?, claimed_at=?, heartbeat_at=?
                       WHERE id=? AND status='queued'""",
                    (worker_id, now, now, scan["id"]),
                )
                if cursor.rowcount:
                    db.execute(
                        """UPDATE flow_workers SET status='scanning', current_scan_id=?, last_seen_at=?, updated_at=?
                           WHERE worker_id=?""",
                        (scan["id"], now, now, worker_id),
                    )
                    claimed_scan = db.execute("SELECT * FROM flow_catalog_scans WHERE id=?", (scan["id"],)).fetchone()
                    return {"run": None, "scan": {**dict(claimed_scan), "job": _loads(claimed_scan["job_json"], {})}}
            db.execute(
                "UPDATE flow_workers SET status='idle', current_run_id=NULL, current_scan_id=NULL, last_seen_at=?, updated_at=? WHERE worker_id=?",
                (now, now, worker_id),
            )
            return {"run": None, "scan": None}
        cursor = db.execute(
            """UPDATE flow_runs SET status='claimed', worker_id=?, claimed_at=?, heartbeat_at=?
               WHERE id=? AND status='queued'""",
            (worker_id, now, now, row["id"]),
        )
        if not cursor.rowcount:
            return {"run": None, "scan": None}
        db.execute(
            """UPDATE flow_workers SET status='busy', current_run_id=?, last_seen_at=?, updated_at=?
               WHERE worker_id=?""",
            (row["id"], now, now, worker_id),
        )
        claimed = db.execute("SELECT * FROM flow_runs WHERE id=?", (row["id"],)).fetchone()
        return {"run": {**dict(claimed), "job": _loads(claimed["job_json"], {})}, "scan": None}


@router.post("/worker/{worker_id}/scans/{scan_id}/progress")
def update_scan(worker_id: str, scan_id: int, body: ScanProgress):
    now = _iso(_now())
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM flow_catalog_scans WHERE id=? AND worker_id=?", (scan_id, worker_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Scan is not assigned to this worker.")
        started = row["started_at"] or (now if body.status == "running" else None)
        finished = now if body.status in RUN_TERMINAL else None
        result = _apply_discovery(
            db, row["site_id"], body.reports, now, complete=body.complete,
        ) if body.status == "succeeded" else {}
        _store_timings(
            db, body.timings, operation_type="catalog_scan", site_id=row["site_id"], scan_id=scan_id,
        )
        db.execute(
            """UPDATE flow_catalog_scans SET status=?, progress_json=?, result_json=?, error=?,
               started_at=COALESCE(started_at, ?), finished_at=?, heartbeat_at=? WHERE id=?""",
            (body.status, _json(body.progress), _json(result), body.error, started, finished, now, scan_id),
        )
        if body.status in RUN_TERMINAL:
            site = db.execute(
                "SELECT discovery_weekday, discovery_time FROM flow_sites WHERE id=?", (row["site_id"],)
            ).fetchone()
            next_scan = _iso(_next_weekly_scan(site["discovery_weekday"], site["discovery_time"]))
            db.execute(
                """UPDATE flow_sites SET last_scan_at=?, last_scan_status=?, last_scan_error=?,
                   next_scan_at=?, updated_at=? WHERE id=?""",
                (now, body.status, body.error, next_scan, now, row["site_id"]),
            )
            db.execute(
                """UPDATE flow_workers SET status='idle', current_scan_id=NULL, last_error=?,
                   last_seen_at=?, updated_at=? WHERE worker_id=?""",
                (body.error, now, now, worker_id),
            )
        else:
            db.execute(
                "UPDATE flow_workers SET status='scanning', last_seen_at=?, updated_at=? WHERE worker_id=?",
                (now, now, worker_id),
            )
    return {"scan_id": scan_id, "status": body.status, "result": result}


@router.post("/worker/{worker_id}/runs/{run_id}/progress")
def update_run(worker_id: str, run_id: int, body: WorkerProgress):
    if body.status not in RUN_STATUSES:
        raise HTTPException(400, "Unsupported run status.")
    now = _iso(_now())
    with get_db() as db:
        row = db.execute("SELECT * FROM flow_runs WHERE id=? AND worker_id=?", (run_id, worker_id)).fetchone()
        if not row:
            raise HTTPException(404, "Run is not assigned to this worker.")
        if row["status"] in RUN_TERMINAL:
            return {"run_id": run_id, "status": row["status"], "ignored": True}
        started = row["started_at"] or (now if body.status == "running" else None)
        finished = now if body.status in RUN_TERMINAL else None
        db.execute(
            """UPDATE flow_runs SET status=?, progress_json=?, artifact_json=?, error=?,
               started_at=COALESCE(started_at, ?), finished_at=?, heartbeat_at=? WHERE id=?""",
            (body.status, _json(body.progress), _json(body.artifacts), body.error,
             started, finished, now, run_id),
        )
        db.execute(
            """INSERT INTO flow_run_events
               (run_id, status, stage, message, details_json, error, traceback, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, body.status, body.progress.get("stage"), body.progress.get("message"),
                _json(body.progress), body.error, body.traceback, now,
            ),
        )
        db.execute(
            "UPDATE flows SET last_run_at=?, last_status=?, last_error=?, updated_at=? WHERE id=?",
            (finished or started or now, body.status, body.error, now, row["flow_id"]),
        )
        job = _loads(row["job_json"], {})
        _store_timings(
            db, body.timings, operation_type="flow_download", run_id=run_id,
            site_id=job.get("site", {}).get("id"), report_id=job.get("report", {}).get("id"),
        )
        if body.status in RUN_TERMINAL:
            downloads = job.get("downloads", {})
            if body.status == "succeeded" and downloads.get("period_strategy") == "rolling":
                db.execute(
                    """UPDATE flows SET start_week=?, updated_at=?
                       WHERE id=? AND start_week=?""",
                    (downloads.get("next_start_week"), now, row["flow_id"], downloads.get("period_start_week")),
                )
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
                    (run_id, _period_key_text(item.get("period_key")), str(item.get("file_path") or ""),
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


@router.post("/worker/{worker_id}/runs/{run_id}/heartbeat")
def heartbeat_run(worker_id: str, run_id: int):
    now = _iso(_now())
    with get_db() as db:
        row = db.execute(
            "SELECT status FROM flow_runs WHERE id=? AND worker_id=?", (run_id, worker_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run is not assigned to this worker.")
        if row["status"] in RUN_TERMINAL:
            return {"run_id": run_id, "status": row["status"], "terminal": True}
        db.execute("UPDATE flow_runs SET heartbeat_at=? WHERE id=?", (now, run_id))
        db.execute(
            "UPDATE flow_workers SET last_seen_at=?, updated_at=? WHERE worker_id=?",
            (now, now, worker_id),
        )
    return {"run_id": run_id, "status": row["status"], "terminal": False}


@router.get("/{flow_id}")
def get_flow(flow_id: int):
    with get_db() as db:
        return _flow_out(db, flow_id)
