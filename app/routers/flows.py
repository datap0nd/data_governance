"""Configurable website report downloads with external authenticated workers."""

from __future__ import annotations

import copy
import html
import json
import logging
import ntpath
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.config import DB_PATH, UPLOAD_PGHOST, UPLOAD_PGPORT
from app.database import get_db
from app import flow_paths, flow_layout, flow_capacity, flow_tasks, flow_parallel
from app.flow_credentials import asap_credential_status, save_asap_credentials
from app.flow_asap_exports import (
    public_asap_download_types,
    resolve_asap_download_type,
)
from app.flow_outlook import SUPPORTED_ATTACHMENT_EXTENSIONS
from app.flow_publish import new_local_file_storage_key, normalize_target_path
from app.freshness import (
    flow_freshness,
    host_timezone,
    iso_utc,
    localize_wall_time,
    next_occurrence,
    rule_key,
    schedule_rule,
    utc_now,
)
from app.flow_retention import RUN_FOLDER_KEEP, tombstone_name as retention_tombstone_name
from app.flow_local_runner import (
    HEADED_WORKER_ID, WORKER_ID as LOCAL_WORKER_ID, launch_local_worker, stop_local_worker,
)
from app.flow_sql import configuration_status as sql_configuration_status, discover_catalog as discover_sql_catalog
from app.routers.eventlog import get_actor, log_event
from app.scanner.findings import sync_managed_actions
from app.source_identity import (
    exact_identity_rows,
    flow_link_status,
    normalize_server,
    postgres_server_identity,
)


def _flow_server_identity() -> str:
    return postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT)

router = APIRouter(prefix="/api/flows", tags=["flows"])

CONTROL_TYPES = {"select", "multi_select", "text", "week"}
DOWNLOAD_MODES = {"single", "one_per_period", "one_per_week"}
PERIOD_STRATEGIES = {"none", "latest", "fixed", "rolling"}
FILE_FORMATS = {"csv", "xlsx", "html", "txt"}
# Recorded pre-processing applied while normalizing a downloaded Excel
# workbook to CSV, before header detection. GSCM's toolbar export frames
# every workbook with a blank first column and a title first row.
EXCEL_TRIMS = {"none", "first_row_and_column"}
SOURCE_TYPES = {"portal", "outlook", "file"}
SQL_MODES = {"append", "replace"}
SCHEDULE_TYPES = {"manual", "daily", "weekly", "monthly"}
SCAN_MODES = {"full", "partial"}
BROWSER_MODES = {"headless", "headed"}
OUTPUT_MODES = {"run_folders", "direct_replace", "private_snapshot"}
TRANSFORM_SCRIPT_SUFFIXES = {".py", ".ps1", ".exe"}
RUN_STALE_TIMEOUT_SECONDS = 600
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
RUN_TERMINAL = {"succeeded", "failed", "cancelled"}
RUN_STATUSES = {"queued", "claimed", "running", *RUN_TERMINAL}
ASAP_PORTAL_ADAPTER = "asap_portal"
GSCM_PORTAL_ADAPTER = "gscm_portal"
OUTLOOK_ATTACHMENT_ADAPTER = "outlook_attachment"
LOCAL_FILE_ADAPTER = "local_file"
INTERNAL_FLOW_ADAPTERS = {OUTLOOK_ATTACHMENT_ADAPTER, LOCAL_FILE_ADAPTER}
SUPPORTED_LOCAL_FILE_EXTENSIONS = frozenset({
    ".csv", ".xls", ".xlt", ".xlsb", ".xlsx", ".xlsm", ".xltx", ".xltm",
})
# Portals Metronome can inventory on its own. Each one is a separate website
# with its own structure: ASAP is catalogued by walking its report menus, GSCM
# by reading the bookmarks the user saved on its home screen.
DISCOVERY_ADAPTERS = {ASAP_PORTAL_ADAPTER, GSCM_PORTAL_ADAPTER}
DISCOVERY_LABELS = {ASAP_PORTAL_ADAPTER: "reports", GSCM_PORTAL_ADAPTER: "bookmarks"}
WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>0[1-9]|[1-4]\d|5[0-3])$")
FILENAME_TOKEN_RE = re.compile(r"\{(flow|report|export|week|start_period|end_period|year|week_number|index|date)\}")
SAFE_NAME_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _sync_flow_failure_actions(db, now: str) -> dict:
    """Expose the latest terminal Flow failure through shared Alerts.

    Queued, running, or cancelled retries do not erase the last confirmed
    failure. A later terminal success resolves it.
    """
    failed = db.execute(
        """SELECT f.id, f.name, f.last_error, r.id AS run_id,
                  r.trigger_type, r.error AS run_error, r.finished_at,
                  p.name AS owner_name,
                  (SELECT e.stage FROM flow_run_events e
                    WHERE e.run_id=r.id AND e.stage IS NOT NULL AND e.stage!=''
                    ORDER BY e.id DESC LIMIT 1) AS failure_stage
             FROM flows f
             LEFT JOIN people p ON p.id=f.owner_person_id
             JOIN flow_runs r ON r.id=(
                 SELECT r2.id FROM flow_runs r2
                  WHERE r2.flow_id=f.id
                    AND r2.status IN ('succeeded','failed')
                  ORDER BY r2.id DESC LIMIT 1
             )
            WHERE r.status='failed'"""
    ).fetchall()
    findings = [
        {
            "fingerprint": f"flow_failed:{row['id']}",
            "flow_id": row["id"],
            "assigned_to": row["owner_name"],
            "notes": row["last_error"] or f"Flow {row['name']} failed.",
            "occurrence": {
                "focus_type": "flow_run",
                "focus_id": row["run_id"],
                "observed_at": row["finished_at"] or now,
                "summary": f"Flow {row['name']} run #{row['run_id']} failed.",
                "evidence": {
                    "status": "failed",
                    "stage": row["failure_stage"],
                    "trigger_type": row["trigger_type"],
                    "error": row["run_error"] or row["last_error"],
                },
            },
        }
        for row in failed
    ]
    return sync_managed_actions(db, "flow_failed", findings, now)


def _flow_failure_context(db, run_id: int) -> dict | None:
    """Collect everything the flow owner needs to triage one failed run."""
    row = db.execute(
        """SELECT r.id AS run_id, r.flow_id, r.trigger_type, r.requested_by, r.worker_id,
                  r.error, r.created_at, r.started_at, r.finished_at,
                  f.name AS flow_name, f.target_folder, f.source_type,
                   f.outlook_subject_contains, f.local_file_path,
                  s.name AS site_name, rep.name AS report_name,
                  p.name AS owner_name, p.email AS owner_email
           FROM flow_runs r
           JOIN flows f ON f.id = r.flow_id
           JOIN flow_sites s ON s.id = f.site_id
           JOIN flow_reports rep ON rep.id = f.report_id
           LEFT JOIN people p ON p.id = f.owner_person_id
           WHERE r.id = ?""",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    context = dict(row)
    stage = db.execute(
        """SELECT stage FROM flow_run_events
           WHERE run_id=? AND stage IS NOT NULL AND stage != ''
           ORDER BY id DESC LIMIT 1""",
        (run_id,),
    ).fetchone()
    context["failure_stage"] = stage["stage"] if stage else None
    files = db.execute(
        "SELECT COUNT(*) AS saved FROM flow_run_files WHERE run_id=?", (run_id,)
    ).fetchone()
    context["files_saved"] = files["saved"] if files else 0
    return context


def _flow_failure_message(context: dict) -> dict:
    """One owner-facing Outlook alert for one failed flow run."""
    run_id = context["run_id"]
    failed_at = context.get("finished_at") or context.get("started_at") or context.get("created_at")
    stage = str(context.get("failure_stage") or "").replace("_", " ").strip()
    trigger = str(context.get("trigger_type") or "manual").replace("_", " ")
    requested_by = str(context.get("requested_by") or "").strip()
    files_saved = int(context.get("files_saved") or 0)

    def detail_row(label: str, value: str) -> str:
        return (
            "<tr>"
            '<td style="padding:10px 12px;border:1px solid #e1c5c5;'
            f'background:#fff7f7;font-size:12px;font-weight:700">{html.escape(label)}</td>'
            '<td style="padding:10px 12px;border:1px solid #e1c5c5;'
            f'background:#ffffff;font-size:12px">{html.escape(value)}</td>'
            "</tr>"
        )

    source_label = (
        f"Outlook Inbox subject containing {context.get('outlook_subject_contains')!r}"
        if context.get("source_type") == "outlook"
        else f"Configured file {context.get('local_file_path')}"
        if context.get("source_type") == "file"
        else f'{context["site_name"]} / {context["report_name"]}'
    )
    rows = [
        detail_row("Flow", context["flow_name"]),
        detail_row("Source", source_label),
        detail_row("Run", f"#{run_id} - {trigger}" + (f" by {requested_by}" if requested_by else "")),
        detail_row("Worker", str(context.get("worker_id") or "Never claimed by a worker")),
        detail_row("Failed at", str(failed_at or "Unknown")),
    ]
    if stage:
        rows.append(detail_row("Last stage", stage.capitalize()))
    rows.append(detail_row(
        "Files saved before failure",
        f"{files_saved} file(s) kept in the private worker snapshot store"
        if files_saved and context.get("source_type") == "file"
        else f"{files_saved} file(s) kept in {context['target_folder']}" if files_saved
        else "None - no file was downloaded",
    ))
    error = str(context.get("error") or "").strip() or "The run failed without an error message."
    body = f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="width:100%;border-collapse:collapse;background:#f3eeee;
                  font-family:Segoe UI,Arial,sans-serif">
      <tr>
        <td align="center" style="padding:24px 12px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="width:100%;max-width:760px;border-collapse:separate;
                        background:#ffffff;border:1px solid #dfcaca;border-radius:12px">
            <tr>
              <td style="padding:25px 28px;background:#8f2d2d;color:#ffffff;
                         border-radius:12px 12px 0 0">
                <div style="margin:0 0 7px;color:#f6dede;font-size:11px;
                            font-weight:700;letter-spacing:1.2px">
                  FLOW RUN FAILED
                </div>
                <div style="margin:0;color:#ffffff;font-size:25px;font-weight:700;
                            line-height:1.2">
                  {html.escape(context["flow_name"])}
                </div>
                <div style="margin-top:8px;color:#f9eaea;font-size:14px;line-height:1.4">
                  Metronome stopped run #{run_id} before it completed. Files already
                  downloaded are kept - nothing was deleted or overwritten.
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 10px">
                <table width="100%" cellspacing="0" cellpadding="0"
                       style="width:100%;border-collapse:collapse;color:#1a1814">
                  {"".join(rows)}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 28px">
                <div style="padding:16px;background:#fff2f2;border:1px solid #dcaeae;
                            border-radius:8px">
                  <div style="margin:0 0 6px;color:#7a2020;font-size:12px;
                              font-weight:700">
                    What needs attention
                  </div>
                  <div style="color:#451b1b;font-size:13px;line-height:1.5">
                    {html.escape(error)}
                  </div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:4px 28px 26px">
                <div style="margin-top:4px;color:#433f38;font-size:13px;line-height:1.5">
                  Open Metronome &gt; Flows &gt; Run history and use Expanded logs on
                  run #{run_id} to see every stage, timing, and the full error trail.
                  After fixing the cause, use Run to start a fresh download - reruns
                  never overwrite files, and only the oldest run folders beyond the
                  newest 3 are cleaned up.
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 28px;background:#f8f4f4;color:#746363;
                         border-top:1px solid #eadada;border-radius:0 0 12px 12px;
                         font-size:11px;line-height:1.4">
                Flow owner: {html.escape(context["owner_name"])}. You receive this
                email for every failed run of this flow. Detected by Metronome at
                {html.escape(_now().isoformat(timespec="minutes"))} local time.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """
    return {
        "to": context["owner_email"],
        "subject": f"Metronome flow failed: {context['flow_name']} (run #{run_id})"[:500],
        "html_body": body,
    }


def notify_flow_owner_of_failure(run_id: int) -> dict:
    """Email the owning person about a failed run. Never raises.

    Every path that turns a run failed calls this after its transaction commits:
    worker-reported failures, lost worker heartbeats, and worker restarts. One
    run fails exactly once (terminal states are never re-entered), so the owner
    receives exactly one email per failed run.
    """
    context = None
    try:
        with get_db() as db:
            context = _flow_failure_context(db, run_id)
        if not context:
            return {"status": "not_sent", "reason": "Run not found."}
        if not context.get("owner_name"):
            return {"status": "not_sent", "reason": "The flow has no owner."}
        if not str(context.get("owner_email") or "").strip():
            outcome = {
                "status": "not_sent",
                "owner_name": context["owner_name"],
                "reason": f"Owner {context['owner_name']} has no email mapped in Tools > Create Artifacts > People.",
            }
        else:
            from app.routers.email import _launch_outlook_payload

            _launch_outlook_payload([_flow_failure_message(context)], "send")
            outcome = {
                "status": "launched",
                "owner_name": context["owner_name"],
                "owner_email": context["owner_email"],
            }
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Could not notify the flow owner about failed run %s", run_id
        )
        outcome = {
            "status": "failed",
            "owner_name": (context or {}).get("owner_name"),
            "reason": str(exc).strip() or exc.__class__.__name__,
        }
    if context:
        try:
            with get_db() as db:
                log_event(
                    db, "flow", context["flow_id"], context["flow_name"],
                    "owner_alerted" if outcome["status"] == "launched" else "owner_alert_skipped",
                    f"run #{run_id}: {outcome.get('reason') or outcome.get('owner_email')}",
                )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not record the owner alert outcome for run %s", run_id
            )
    return outcome


def fail_stale_runs(
    timeout_seconds: int = RUN_STALE_TIMEOUT_SECONDS,
) -> dict:
    """Fail runs whose assigned worker has stopped responding."""
    now = _now()
    now_text = _iso(now)
    cutoff = _iso(now - timedelta(seconds=timeout_seconds))
    failed = []
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow_parallel.reap(db)
        rows = db.execute(
            """SELECT r.id, r.flow_id, r.worker_id
               FROM flow_runs r
               LEFT JOIN flow_workers w ON w.worker_id=r.worker_id
               WHERE r.status IN ('claimed','running')
                 AND COALESCE(w.last_seen_at, r.heartbeat_at, r.claimed_at) < ?""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            message = "The assigned browser worker stopped responding before the run finished."
            if flow_parallel._fanout(db, row['id']):
                flow_parallel.abort(db, row['id'], message + ' Finalization was not replayed; reconcile any uncertain SQL commit.', coordinator_stopped=True)
                flow_parallel.finish_aborted(db, row['id'])
                continue
            db.execute(
                """UPDATE flow_runs SET status='failed', error=?, finished_at=?, heartbeat_at=?
                   WHERE id=? AND status IN ('claimed','running')""",
                (message, now_text, now_text, row["id"]),
            )
            db.execute(
                """INSERT INTO flow_run_events
                   (run_id, status, stage, message, details_json, error, traceback, created_at)
                   VALUES (?, 'failed', ?, ?, '{}', ?, NULL, ?)""",
                (
                    row["id"], "worker_lost", message, message, now_text,
                ),
            )
            db.execute(
                """UPDATE flows SET last_run_at=?, last_status='failed', last_error=?, updated_at=?
                   WHERE id=?""",
                (now_text, message, now_text, row["flow_id"]),
            )
            _release_retention_ops(db, row["id"], now_text)
            if row["worker_id"]:
                db.execute(
                    """UPDATE flow_workers SET status='offline', current_run_id=NULL,
                       last_error=?, updated_at=? WHERE worker_id=?""",
                    (message, now_text, row["worker_id"]),
                )
            failed.append(row["id"])
        pending = db.execute("""SELECT e.id,e.run_id FROM flow_run_events e
            JOIN flow_runs r ON r.id=e.run_id
            WHERE e.stage='owner_alert_pending' AND r.status='failed'""").fetchall()
        for event in pending:
            db.execute("UPDATE flow_run_events SET stage='owner_alert_dispatch',message='Owner notification dispatched after parallel-run recovery.' WHERE id=?", (event['id'],))
            if event['run_id'] not in failed:
                failed.append(event['run_id'])
        _sync_flow_failure_actions(db, now_text)
    for run_id in failed:
        notify_flow_owner_of_failure(run_id)
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


def _clean_filename_template(
    value: str, file_format: str, compatible_suffixes: tuple[str, ...] | None = None,
) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter a filename template.")
    sample = FILENAME_TOKEN_RE.sub("sample", value)
    suffixes = compatible_suffixes or (f".{file_format}",)
    if not any(sample.casefold().endswith(suffix) for suffix in suffixes):
        expected = " or ".join(suffixes)
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


def _schedule_next(
    schedule_type: str, schedule_time: str | None, schedule_days: list[str],
    schedule_day: int | None = None,
) -> datetime | None:
    rule = schedule_rule(schedule_type, schedule_time, schedule_days, schedule_day)
    if rule is None:
        return None
    zone = host_timezone()
    after = localize_wall_time(_now(), zone).astimezone(timezone.utc)
    return next_occurrence(rule, after=after).astimezone(zone).replace(tzinfo=None)


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
    source_type: str = "portal"
    site_id: int | None = Field(default=None, ge=1)
    report_id: int | None = Field(default=None, ge=1)
    outlook_subject_contains: str | None = Field(default=None, max_length=500)
    local_file_path: str | None = Field(default=None, max_length=2000)
    local_file_worksheet: str | None = Field(default=None, max_length=500)
    export_views: list[str] = Field(default_factory=list, max_length=20)
    download_links: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = False
    selections: dict[str, Any] = Field(default_factory=dict)
    download_mode: str = "single"
    period_strategy: str = "latest"
    window_weeks: int | None = Field(default=None, ge=1, le=105)
    file_format: str = "csv"
    asap_download_type: str | None = None
    # NULL means inherit whatever the portal currently presents.  Creation
    # applies the checked defaults after the source adapter is known; keeping
    # the model defaults nullable prevents an old client from changing an
    # existing Flow merely by omitting these newly introduced fields.
    export_report_title: bool | None = None
    export_filter_details: bool | None = None
    excel_trim: str = "none"
    browser_mode: str = "headless"
    download_parallelism: int | None = Field(default=None, ge=1, le=5, strict=True)
    start_week: str | None = None
    end_week: str | None = None
    target_folder: str | None = Field(default=None, max_length=2000)
    filename_template: str | None = Field(default=None, max_length=500)
    output_mode: str = "run_folders"
    schedule_type: str = "manual"
    schedule_time: str | None = None
    schedule_days: list[str] = Field(default_factory=list)
    schedule_day: int | None = Field(default=None, ge=1, le=31)
    transform_enabled: bool = False
    transform_script_path: str | None = Field(default=None, max_length=2000)
    sql_handoff_enabled: bool = False
    sql_mode: str | None = None
    sql_uppercase: bool = False
    sql_database: str | None = Field(default=None, max_length=63)
    sql_schema: str | None = Field(default=None, max_length=63)
    sql_table: str | None = Field(default=None, max_length=63)
    sql_target_source_id: int | None = Field(default=None, ge=1)
    owner_person_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_flow(self):
        self.name = self.name.strip()
        self.source_type = self.source_type.strip().casefold()
        if self.source_type not in SOURCE_TYPES:
            raise ValueError("Flow source type must be a website report, Outlook attachment, or file.")
        self.target_folder = (self.target_folder or "").strip() or None
        self.output_mode = (self.output_mode or "run_folders").strip().casefold()
        if self.output_mode not in OUTPUT_MODES:
            raise ValueError("Output storage mode is unsupported.")
        self.export_views = list(dict.fromkeys(
            str(value).strip() for value in self.export_views if str(value).strip()
        ))
        self.download_links = list(dict.fromkeys(
            str(value).strip() for value in self.download_links if str(value).strip()
        ))
        if self.source_type == "file":
            self.local_file_path = (self.local_file_path or "").strip().strip('"')
            if not self.local_file_path:
                raise ValueError("Enter the full path and filename to read.")
            if not _is_absolute_worker_path(self.local_file_path):
                raise ValueError("Source file must use an absolute path visible to the worker.")
            if any(token in self.local_file_path for token in ("*", "?")):
                raise ValueError("Source file must name one exact file, without wildcards.")
            suffix = Path(self.local_file_path).suffix.casefold()
            if suffix not in SUPPORTED_LOCAL_FILE_EXTENSIONS:
                raise ValueError("Source file must be a supported CSV or Excel workbook.")
            if suffix == ".csv":
                self.local_file_worksheet = None
            elif self.local_file_worksheet is None or not self.local_file_worksheet.strip():
                raise ValueError("Enter the exact Excel worksheet name to load.")
            self.outlook_subject_contains = None
            self.site_id = None
            self.report_id = None
            self.export_views = []
            self.download_links = []
            self.selections = {}
            self.download_mode = "single"
            self.period_strategy = "none"
            self.window_weeks = None
            self.file_format = "auto"
            self.asap_download_type = None
            self.export_report_title = None
            self.export_filter_details = None
            self.excel_trim = "none"
            self.browser_mode = "headless"
            self.start_week = None
            self.end_week = None
            self.target_folder = None
            self.filename_template = "{original}"
            self.output_mode = "private_snapshot"
        elif self.source_type == "outlook":
            self.local_file_path = None
            self.local_file_worksheet = None
            self.outlook_subject_contains = (self.outlook_subject_contains or "").strip()
            if not self.outlook_subject_contains:
                raise ValueError("Enter text to find in the Outlook email subject.")
            self.site_id = None
            self.report_id = None
            self.export_views = []
            self.download_links = []
            self.selections = {}
            self.download_mode = "single"
            self.period_strategy = "none"
            self.window_weeks = None
            self.file_format = "auto"
            self.asap_download_type = None
            self.export_report_title = None
            self.export_filter_details = None
            self.excel_trim = "none"
            self.browser_mode = "headless"
            self.start_week = None
            self.end_week = None
            # The database column remains non-null. This source-specific
            # sentinel documents that the attachment basename is authoritative;
            # portal filename rendering never receives it.
            self.filename_template = "{original}"
        else:
            self.outlook_subject_contains = None
            self.local_file_path = None
            self.local_file_worksheet = None
            if self.site_id is None or self.report_id is None:
                raise ValueError("Choose a website and report.")
            self.file_format = self.file_format.strip().casefold()
            compatible_suffixes = None
            if self.asap_download_type is not None:
                download_type = resolve_asap_download_type(
                    self.asap_download_type, legacy_file_format=self.file_format,
                )
                self.asap_download_type = download_type.key
                self.file_format = download_type.file_format
                compatible_suffixes = download_type.compatible_suffixes
            elif self.file_format == "xlsx":
                # Old API clients do not yet send the semantic ASAP type.
                # Accept both Excel suffixes here; adapter validation later
                # decides whether this is an ASAP/GSCM/generic portal Flow.
                compatible_suffixes = (".xls", ".xlsx")
            if self.file_format not in FILE_FORMATS:
                raise ValueError("Unsupported download file format.")
            self.filename_template = _clean_filename_template(
                self.filename_template or "", self.file_format, compatible_suffixes,
            )
            self.excel_trim = (self.excel_trim or "none").strip().casefold()
            if self.excel_trim not in EXCEL_TRIMS:
                raise ValueError(
                    "Excel pre-processing must be 'none' or 'first_row_and_column'."
                )
            if self.download_mode not in DOWNLOAD_MODES:
                raise ValueError("Unsupported download mode.")
            if self.download_mode == "one_per_week":
                self.download_mode = "one_per_period"
                self.window_weeks = self.window_weeks or 1
            if self.period_strategy not in PERIOD_STRATEGIES:
                raise ValueError("Unsupported period strategy.")
            if self.browser_mode not in BROWSER_MODES:
                raise ValueError("Browser mode must be headed or headless.")
        if self.source_type != "portal":
            self.download_parallelism = 1
        if self.source_type != "file":
            if self.target_folder and not _is_absolute_worker_path(self.target_folder):
                raise ValueError("Target folder must be an absolute path visible to the worker.")
            if self.output_mode == "private_snapshot":
                raise ValueError("Private snapshot storage is reserved for file-source Flows.")
        if self.schedule_type not in SCHEDULE_TYPES:
            raise ValueError("Unsupported schedule type.")
        if self.source_type == "portal":
            self.start_week = _week_value(self.start_week, "Start week")
            self.end_week = _week_value(self.end_week, "End week")
            if self.period_strategy == "none":
                self.start_week = None
                self.end_week = None
                self.window_weeks = None
                self.download_mode = "single"
            elif not self.start_week:
                raise ValueError("Choose a Sell-out Week start.")
            elif self.period_strategy == "fixed":
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
            if len(self.export_views) > 1 and not any(
                token in self.filename_template for token in ("{export}", "{index}")
            ):
                raise ValueError("Multiple export views require an export or index token in the filename template.")
            if len(self.download_links) > 1 and not any(
                token in self.filename_template for token in ("{export}", "{index}")
            ):
                raise ValueError("Multiple download links require an export or index token in the filename template.")
        self.schedule_days = [str(day).strip().casefold() for day in self.schedule_days]
        if self.schedule_type != "monthly":
            self.schedule_day = None
        _schedule_next(
            self.schedule_type, self.schedule_time, self.schedule_days, self.schedule_day,
        )
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
        if self.asap_download_type in {"html", "plain_text"} and (
            self.transform_enabled or self.sql_handoff_enabled
        ):
            raise ValueError("HTML and Plain text ASAP exports are download-only.")
        if self.sql_handoff_enabled:
            self.sql_mode = (self.sql_mode or "").strip().casefold()
            if self.sql_mode not in SQL_MODES:
                raise ValueError("SQL write mode must be append rows or replace all rows.")
            for field_name in ("sql_database", "sql_schema", "sql_table"):
                value = (getattr(self, field_name) or "").strip()
                if not value:
                    raise ValueError("Choose a discovered SQL database, schema, and table.")
                setattr(self, field_name, value)
        else:
            self.sql_mode = self.sql_database = self.sql_schema = self.sql_table = None
            self.sql_target_source_id = None
            self.sql_uppercase = False
        return self


class WorkerRegister(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class OutlookSourceReceipt(BaseModel):
    kind: Literal["outlook"] = "outlook"
    identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    received_at: str | None = Field(default=None, max_length=100)
    attachment_name: str = Field(min_length=1, max_length=500)
    subject: str | None = Field(default=None, max_length=1000)


class LocalFileReceipt(BaseModel):
    kind: Literal["local_file"] = "local_file"
    identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    raw_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_path: str = Field(min_length=1, max_length=4000)
    worksheet: str | None = Field(default=None, max_length=500)
    config_revision: int = Field(ge=1)
    file_size: int = Field(ge=0)
    modified_at_ns: int | None = Field(default=None, ge=0)


#: The one progress field the run-log UI renders verbatim. ``error`` and
#: ``traceback`` already carry max_length caps; before this cap existed a
#: worker screen dump reached flow_run_events at 100,000 characters.
PROGRESS_MESSAGE_MAX_CHARS = 4_000


def _bound_progress_message(value: dict[str, Any]) -> dict[str, Any]:
    """Truncate, never reject: older workers must keep posting progress."""
    message = value.get("message") if isinstance(value, dict) else None
    if isinstance(message, str) and len(message) > PROGRESS_MESSAGE_MAX_CHARS:
        value = {
            **value,
            "message": message[:PROGRESS_MESSAGE_MAX_CHARS] + "… [truncated]",
        }
    return value


class WorkerProgress(BaseModel):
    status: Literal["running", "succeeded", "failed", "cancelled"]
    progress: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    timings: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    retention: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    error: str | None = Field(default=None, max_length=10000)
    traceback: str | None = Field(default=None, max_length=100000)
    source_receipt: LocalFileReceipt | OutlookSourceReceipt | None = None
    finalizer_token: str | None = Field(default=None, max_length=64)

    @field_validator("progress")
    @classmethod
    def _bound_message(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bound_progress_message(value)


class FolderRegister(BaseModel):
    run_folder: str = Field(min_length=1, max_length=4000)


class FlowEnabledWrite(BaseModel):
    enabled: bool


class FlowDeleteWrite(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)


MAX_DISCOVERED_OPTIONS = 5000


class DiscoveredFilter(BaseModel):
    filter_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=200)
    control_label: str = Field(min_length=1, max_length=300)
    control_type: str
    options: list[str] = Field(default_factory=list, max_length=MAX_DISCOVERED_OPTIONS)
    automation: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    position: int = Field(default=0, ge=0, le=1000)

    @field_validator("options", mode="before")
    @classmethod
    def cap_options(cls, value):
        """Keep a huge member list instead of failing the whole scan.

        A prompt with thousands of members (country or item lists) is a
        legitimate discovery. Truncating it costs a few tail options; failing
        validation costs the entire scan, which then has to be run again.
        """
        if isinstance(value, list) and len(value) > MAX_DISCOVERED_OPTIONS:
            return value[:MAX_DISCOVERED_OPTIONS]
        return value

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
    skipped_reports: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)

    @field_validator("progress")
    @classmethod
    def _bound_message(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bound_progress_message(value)

    @model_validator(mode="before")
    @classmethod
    def keep_valid_reports(cls, data):
        """Drop only the reports that fail validation, never the whole scan.

        A scan is expensive - up to 90 minutes of browser work - so one
        malformed report must not reject the post and force a full rerun.
        Offending reports are recorded in skipped_reports so the scan log
        names them and the rest of the catalog still lands.
        """
        if not isinstance(data, dict) or not isinstance(data.get("reports"), list):
            return data
        kept, skipped = [], []
        for item in data["reports"]:
            if isinstance(item, DiscoveredReport):
                kept.append(item)
                continue
            try:
                kept.append(DiscoveredReport.model_validate(item))
            except ValidationError as exc:
                name = str((item or {}).get("discovery_key") or (item or {}).get("name") or "unnamed report") \
                    if isinstance(item, dict) else "unnamed report"
                first = exc.errors()[0] if exc.errors() else {}
                where = ".".join(str(part) for part in first.get("loc", ()))
                skipped.append({
                    "report": name[:300],
                    "error": f"{where}: {first.get('msg', 'invalid report')}"[:300],
                })
        if not skipped:
            return data
        existing = data.get("skipped_reports") or []
        return {**data, "reports": kept, "skipped_reports": [*existing, *skipped]}


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


def _flow_out(db, flow_id: int, *, include_private_storage: bool = False) -> dict:
    row = db.execute(
        """SELECT f.*, s.name AS site_name, s.adapter AS source_adapter,
                  r.name AS report_name, r.automation_json AS report_automation_json,
                  p.name AS owner_name, p.email AS owner_email
           FROM flows f
           JOIN flow_sites s ON s.id = f.site_id
           JOIN flow_reports r ON r.id = f.report_id
           LEFT JOIN people p ON p.id = f.owner_person_id
           WHERE f.id = ?""",
        (flow_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Flow not found.")
    result = dict(row)
    result["folder_relative"] = (
        os.path.relpath(result["flow_folder"], flow_paths.get_flows_root(db))
        if result.get("flow_folder") and flow_paths.is_inside(result["flow_folder"], flow_paths.get_flows_root(db), resolve=False)
        else result.get("flow_folder")
    )
    category = _loads(result.pop("report_automation_json", None), {}).get("category_path", [])
    result["category_path"] = category if isinstance(category, list) else []
    result["enabled"] = bool(result["enabled"])
    result["sql_handoff_enabled"] = bool(result["sql_handoff_enabled"])
    result["sql_uppercase"] = bool(result.get("sql_uppercase"))
    result["transform_enabled"] = bool(result.get("transform_enabled"))
    for key in ("export_report_title", "export_filter_details"):
        result[key] = None if result.get(key) is None else bool(result[key])
    result["selections"] = _loads(result.pop("selections_json"), {})
    result["export_views"] = _loads(result.pop("export_views_json", None), [])
    result["download_links"] = _loads(result.pop("download_links_json", None), [])
    result["schedule_days"] = _loads(result.pop("schedule_days"), [])
    freshness_rule, freshness_health = flow_freshness(row)
    result["freshness_rule"] = freshness_rule
    result["freshness_health"] = freshness_health
    link = flow_link_status(db, row, server=_flow_server_identity())
    # Keep persisted diagnostic evidence separate from the exact target a
    # preview or run may safely use.
    result["sql_target_source_id"] = link.get("persisted_source_id")
    result["sql_target_effective_source_id"] = link.get("effective_source_id")
    result["sql_target_link_status"] = link["status"]
    result["sql_target_match_source_ids"] = link.get("matches", [])
    match_ids = result["sql_target_match_source_ids"]
    result["sql_target_exact_candidates"] = []
    if match_ids:
        placeholders = ",".join("?" * len(match_ids))
        exact_rows = db.execute(
            f"SELECT id, name FROM sources WHERE id IN ({placeholders}) ORDER BY id",
            match_ids,
        ).fetchall()
        result["sql_target_exact_candidates"] = [dict(item) for item in exact_rows]
    result["sql_target_legacy_suggestions"] = []
    if result["sql_handoff_enabled"] and link["status"] != "confirmed":
        display_target = f"{result.get('sql_schema')}.{result.get('sql_table')}".casefold()
        suggestions = db.execute(
            """SELECT id, name FROM sources
               WHERE COALESCE(archived, 0)=0 AND lower(name) LIKE ?
               ORDER BY id LIMIT 20""",
            (f"%{display_target}",),
        ).fetchall()
        result["sql_target_legacy_suggestions"] = [dict(item) for item in suggestions]
    if result["download_mode"] == "one_per_week":
        result["download_mode"] = "one_per_period"
        result["window_weeks"] = result["window_weeks"] or 1
    if result.get("source_type") == "file" and not include_private_storage:
        # This opaque URI is an internal retention key, not a destination the
        # user can act on. Keep it out of editor/list/diagnostic API payloads.
        result["target_folder"] = None
    return result


def _public_flow_job(job: dict) -> dict:
    """Hide a file Flow's internal retention URI from user-facing payloads."""
    result = copy.deepcopy(job)
    if (result.get("flow", {}).get("source_type") or "portal") == "file":
        result.setdefault("downloads", {}).pop("target_folder", None)
        result.setdefault("local_file", {}).pop("private_store_key", None)
    return result


def _outlook_source_ids(db) -> tuple[int, int]:
    row = db.execute(
        """SELECT s.id AS site_id, r.id AS report_id
           FROM flow_sites s JOIN flow_reports r ON r.site_id=s.id
           WHERE s.adapter=? AND r.source_kind='system'
           ORDER BY r.id LIMIT 1""",
        (OUTLOOK_ATTACHMENT_ADAPTER,),
    ).fetchone()
    if not row:
        site = db.execute(
            "SELECT id FROM flow_sites WHERE adapter=? ORDER BY id LIMIT 1",
            (OUTLOOK_ATTACHMENT_ADAPTER,),
        ).fetchone()
        if not site:
            raise HTTPException(
                500,
                "The internal Outlook flow source is missing. Restart Metronome to apply migrations.",
            )
        now = _iso(_now())
        db.execute(
            """INSERT OR IGNORE INTO flow_reports
               (site_id, name, report_url, download_text, automation_json, source_kind,
                stale, enabled, created_at, updated_at)
               VALUES (?, 'Inbox attachment', 'outlook://inbox', 'Save attachment',
                       '{"kind":"outlook_attachment"}', 'system', 0, 1, ?, ?)""",
            (site["id"], now, now),
        )
        report = db.execute(
            """SELECT id FROM flow_reports
               WHERE site_id=? AND source_kind='system' ORDER BY id LIMIT 1""",
            (site["id"],),
        ).fetchone()
        if not report:
            raise HTTPException(500, "The internal Outlook report could not be created.")
        return site["id"], report["id"]
    return row["site_id"], row["report_id"]


def _local_file_source_ids(db) -> tuple[int, int]:
    row = db.execute(
        """SELECT s.id AS site_id, r.id AS report_id
           FROM flow_sites s JOIN flow_reports r ON r.site_id=s.id
           WHERE s.adapter=? AND r.source_kind='system'
           ORDER BY r.id LIMIT 1""",
        (LOCAL_FILE_ADAPTER,),
    ).fetchone()
    if not row:
        site = db.execute(
            "SELECT id FROM flow_sites WHERE adapter=? ORDER BY id LIMIT 1",
            (LOCAL_FILE_ADAPTER,),
        ).fetchone()
        if not site:
            raise HTTPException(
                500,
                "The internal local-file flow source is missing. Restart Metronome to apply migrations.",
            )
        now = _iso(_now())
        db.execute(
            """INSERT OR IGNORE INTO flow_reports
               (site_id, name, report_url, download_text, automation_json, source_kind,
                stale, enabled, created_at, updated_at)
               VALUES (?, 'Configured file', 'local-file://source', 'Read file',
                       '{"kind":"local_file"}', 'system', 0, 1, ?, ?)""",
            (site["id"], now, now),
        )
        report = db.execute(
            """SELECT id FROM flow_reports
               WHERE site_id=? AND source_kind='system' ORDER BY id LIMIT 1""",
            (site["id"],),
        ).fetchone()
        if not report:
            raise HTTPException(500, "The internal local-file report could not be created.")
        return site["id"], report["id"]
    return row["site_id"], row["report_id"]


def _resolve_flow_source(db, body: FlowWrite) -> None:
    if body.source_type == "outlook":
        body.site_id, body.report_id = _outlook_source_ids(db)
    elif body.source_type == "file":
        body.site_id, body.report_id = _local_file_source_ids(db)


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


def _validate_owner(db, body: FlowWrite):
    if body.owner_person_id is None:
        return
    row = db.execute("SELECT id FROM people WHERE id=?", (body.owner_person_id,)).fetchone()
    if not row:
        raise HTTPException(400, "Choose a flow owner from Tools > Create Artifacts > People.")


def _normalize_new_sql_table(name: str) -> str:
    """Lowercase a new table name and convert whitespace to underscores.

    Applied only to tables Metronome will create itself. Existing discovered
    tables keep their exact catalog names, which can legitimately contain
    spaces and mixed case.
    """
    return re.sub(r"\s+", "_", (name or "").strip()).lower()


def _validate_sql_target(db, body: FlowWrite):
    if not body.sql_handoff_enabled:
        return
    if body.sql_mode == "replace":
        row = db.execute(
            """SELECT 1 FROM flow_sql_catalog
               WHERE database_name=? AND schema_name=? AND stale=0 LIMIT 1""",
            (body.sql_database, body.sql_schema),
        ).fetchone()
        if not row:
            raise HTTPException(400, "Choose a database and schema from the latest SQL catalog scan.")
        existing = db.execute(
            """SELECT 1 FROM flow_sql_catalog
               WHERE database_name=? AND schema_name=? AND table_name=? AND stale=0""",
            (body.sql_database, body.sql_schema, body.sql_table),
        ).fetchone()
        if not existing:
            body.sql_table = _normalize_new_sql_table(body.sql_table)
        return
    row = db.execute(
        """SELECT 1 FROM flow_sql_catalog
           WHERE database_name=? AND schema_name=? AND table_name=? AND stale=0""",
        (body.sql_database, body.sql_schema, body.sql_table),
    ).fetchone()
    if not row:
        raise HTTPException(400, "Choose a database, schema, and table from the latest SQL catalog scan.")


def _resolve_sql_target_source(
    db,
    body: FlowWrite,
    *,
    preserve_invalid_source_id: int | None = None,
) -> int | None:
    """Resolve or confirm the Flow's executable target identity."""
    if not body.sql_handoff_enabled:
        return None
    matches = exact_identity_rows(
        db,
        server=_flow_server_identity(),
        database=body.sql_database,
        schema=body.sql_schema,
        relation=body.sql_table,
    )
    match_ids = [int(row["source_id"]) for row in matches]
    if body.sql_target_source_id is not None:
        if body.sql_target_source_id not in match_ids:
            if (
                preserve_invalid_source_id is not None
                and int(body.sql_target_source_id) == int(preserve_invalid_source_id)
            ):
                return match_ids[0] if len(match_ids) == 1 else int(preserve_invalid_source_id)
            raise HTTPException(
                400,
                "The confirmed source does not exactly match this SQL server, database, schema, and table.",
            )
        return int(body.sql_target_source_id)
    if preserve_invalid_source_id is not None:
        return match_ids[0] if len(match_ids) == 1 else int(preserve_invalid_source_id)
    return match_ids[0] if len(match_ids) == 1 else None


def _validate_flow_selections(db, body: FlowWrite, *, new_flow: bool = False):
    source = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (body.site_id,)).fetchone()
    if source:
        candidate = {**body.model_dump(), "source_adapter": source["adapter"]}
        try:
            flow_paths.validate_flow(candidate, flow_paths.policy(db, candidate))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if body.source_type == "outlook":
        expected_site, expected_report = _outlook_source_ids(db)
        if body.site_id != expected_site or body.report_id != expected_report:
            raise HTTPException(400, "The Outlook flow source could not be resolved.")
        return
    if body.source_type == "file":
        expected_site, expected_report = _local_file_source_ids(db)
        if body.site_id != expected_site or body.report_id != expected_report:
            raise HTTPException(400, "The local-file flow source could not be resolved.")
        return
    report = db.execute(
        """SELECT r.site_id, r.automation_json, s.adapter FROM flow_reports r
           JOIN flow_sites s ON s.id = r.site_id
           WHERE r.id = ? AND r.enabled = 1 AND r.stale = 0""",
        (body.report_id,),
    ).fetchone()
    if not report or report["site_id"] != body.site_id:
        raise HTTPException(400, "Choose an enabled report from the selected website.")
    adapter = report["adapter"]
    if adapter == ASAP_PORTAL_ADAPTER:
        try:
            download_type = resolve_asap_download_type(
                body.asap_download_type, legacy_file_format=body.file_format,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        body.asap_download_type = download_type.key
        body.file_format = download_type.file_format
        if download_type.download_only and (
            body.transform_enabled or body.sql_handoff_enabled
        ):
            raise HTTPException(
                400, f"{download_type.label} is download-only; disable transformation and SQL handoff.",
            )
    else:
        body.asap_download_type = None
        body.export_report_title = None
        body.export_filter_details = None
        if body.file_format not in {"csv", "xlsx"}:
            raise HTTPException(400, "This website supports only CSV or Excel downloads.")
    if adapter == GSCM_PORTAL_ADAPTER and body.file_format != "xlsx":
        # GSCM's Nexacro toolbar only emits a workbook. Treating that file as a
        # CSV would hand SQL a binary blob renamed .csv.
        raise HTTPException(400, "GSCM exports an Excel workbook. Choose the Excel download type.")
    rows = db.execute(
        "SELECT * FROM flow_report_filters WHERE report_id = ? AND enabled = 1 ORDER BY position, id",
        (body.report_id,),
    ).fetchall()
    automation = _loads(report["automation_json"], {})
    available_views = [
        str(item.get("label") if isinstance(item, dict) else item).strip()
        for item in automation.get("export_views", [])
        if str(item.get("label") if isinstance(item, dict) else item).strip()
    ]
    fallback_view = str(automation.get("report_tab") or "").strip()
    if not available_views and fallback_view:
        available_views = [fallback_view]
    # Older catalog rows predate explicit export-view discovery. Keep those
    # flows runnable with their legacy report behavior. Once a scan has found
    # named views, an omitted selection means all discovered views so API
    # clients cannot silently reduce a bundle to only the first export.
    if not body.export_views and available_views:
        body.export_views = available_views
    invalid_views = [view for view in body.export_views if view not in available_views]
    if available_views and invalid_views:
        raise HTTPException(400, f"Export view was not found in the latest scan: {invalid_views[0]}")
    if adapter == ASAP_PORTAL_ADAPTER:
        capabilities = automation.get("asap_export_capabilities") or {}
        by_view = capabilities.get("views") or {}
        requested_views = body.export_views or ["__default__"]
        records = [by_view.get(view) for view in requested_views]
        known = bool(records) and all(
            isinstance(item, dict) and item.get("status") == "detected"
            for item in records
        )
        if known:
            available_types = set(records[0].get("download_types") or [])
            for record in records[1:]:
                available_types &= set(record.get("download_types") or [])
            if body.asap_download_type not in available_types:
                raise HTTPException(
                    400,
                    "The selected ASAP download type is unavailable in one or more selected export views.",
                )
            for option_key in ("export_report_title", "export_filter_details"):
                option_available = all(
                    bool(
                        ((record.get("options_by_type") or {}).get(
                            body.asap_download_type, {}
                        ).get(option_key) or {}).get("available")
                    )
                    for record in records
                )
                if new_flow and getattr(body, option_key) is None and option_available:
                    setattr(body, option_key, True)
                if getattr(body, option_key) is None:
                    continue
                if not option_available:
                    raise HTTPException(
                        400, f"{option_key.replace('_', ' ').title()} is unavailable in one or more selected export views.",
                    )
        elif new_flow:
            # A new Flow starts with both boxes checked. If discovery has not
            # verified the controls yet, the runner will still verify them
            # live and fail clearly if the portal does not expose one.
            if body.export_report_title is None:
                body.export_report_title = True
            if body.export_filter_details is None:
                body.export_filter_details = True
    definitions = {row["filter_key"]: row for row in rows}
    has_week_filter = any(row["control_type"] == "week" for row in rows)
    if has_week_filter and body.period_strategy == "none":
        raise HTTPException(400, "Choose a Sell-out Week period for this report.")
    if not has_week_filter and body.period_strategy != "none":
        raise HTTPException(400, "This report has no Sell-out Week prompt. Use no period selection.")
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


def _build_job(db, flow_id: int, *, force_reprocess: bool = False) -> dict:
    flow = _flow_out(db, flow_id, include_private_storage=True)
    if flow.get('sql_reconciliation_required'):
        raise HTTPException(409, 'A prior SQL commit may have completed. Reconcile the target and acknowledge it before another run.')
    paths = flow_paths.policy(db, flow)
    try:
        flow_paths.validate_flow(flow, paths)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    report = _report_out(db, flow["report_id"])
    if flow.get("period_strategy") == "none":
        weeks = []
    elif flow.get("period_strategy") == "rolling":
        weeks = _week_window(flow["start_week"], flow["window_weeks"])
    else:
        end_week = (
            _latest_discovered_week(report, flow["start_week"])
            if flow.get("period_strategy") == "latest"
            else flow["end_week"]
        )
        weeks = _week_range(flow["start_week"], end_week)
    if not weeks:
        periods = [None]
    elif flow.get("period_strategy") == "rolling" or flow["download_mode"] == "single":
        periods = [weeks]
    else:
        periods = _periods(weeks, flow.get("window_weeks") or 1)
    source_type = flow.get("source_type") or "portal"
    execution = {
        "mode": "local", "host": "bi_desktop", "browser_mode": flow["browser_mode"],
        "worker_id": HEADED_WORKER_ID if flow["browser_mode"] == "headed" else LOCAL_WORKER_ID,
        "download_parallelism": int(flow.get("download_parallelism") or 1),
    }
    if source_type == "file":
        execution["required_adapter"] = LOCAL_FILE_ADAPTER
    return {
        "schema_version": 3,
        **({"paths": paths} if paths else {}),
        "execution": {
            **execution,
        },
        "flow": {
            "id": flow["id"], "name": flow["name"],
            "source_type": source_type,
            "folder": flow.get("flow_folder"),
        },
        "site": {
            "id": flow["site_id"], "name": flow["site_name"],
            "adapter": report["adapter"], "auth_url": report["auth_url"],
        },
        "report": {
            "id": report["id"], "name": report["name"], "url": report["report_url"],
            "ready_text": report["ready_text"], "open_export_text": report["open_export_text"],
            "download_text": report["download_text"], "automation": report["automation"],
            "filters": report["filters"], "export_views": flow.get("export_views") or [],
            "download_links": flow.get("download_links") or [],
        },
        "selections": flow["selections"],
        "outlook_source": {
            "enabled": source_type == "outlook",
            "mailbox": "default",
            "folder": "inbox",
            "include_subfolders": False,
            "subject_contains": flow.get("outlook_subject_contains"),
            "supported_extensions": list(SUPPORTED_ATTACHMENT_EXTENSIONS),
            "attachment_policy": "exactly_one",
            "last_processed_identity": flow.get("outlook_last_identity"),
            "force_reprocess": bool(force_reprocess),
        },
        "local_file": {
            "enabled": source_type == "file",
            "path": flow.get("local_file_path"),
            "normalized_path": normalize_target_path(flow.get("local_file_path") or ""),
            "worksheet": flow.get("local_file_worksheet"),
            "config_revision": int(flow.get("local_file_config_revision") or 1),
            "previous_identity": flow.get("local_file_last_identity"),
            "force_reprocess": bool(force_reprocess),
            "private_store_key": flow.get("target_folder") if source_type == "file" else None,
            "supported_extensions": sorted(SUPPORTED_LOCAL_FILE_EXTENSIONS),
        },
        "downloads": {
            "mode": flow["download_mode"], "periods": periods,
            "period_strategy": flow.get("period_strategy") or "fixed",
            "period_unit": "week",
            "period_size": flow.get("window_weeks") or len(weeks),
            "file_format": flow.get("file_format") or "csv",
            "excel_trim": flow.get("excel_trim") or "none",
            **(
                {
                    "asap_download_type": flow.get("asap_download_type")
                    or resolve_asap_download_type(
                        None, legacy_file_format=flow.get("file_format"),
                    ).key,
                    "export_report_title": flow.get("export_report_title"),
                    "export_filter_details": flow.get("export_filter_details"),
                }
                if report["adapter"] == ASAP_PORTAL_ADAPTER else {}
            ),
            "period_start_week": weeks[0] if weeks else None,
            "period_end_week": weeks[-1] if weeks else None,
            "next_start_week": _week_window(weeks[-1], 2)[1] if weeks else None,
            "target_folder": flow["target_folder"],
            "filename_template": flow["filename_template"],
            "output_mode": flow.get("output_mode") or "run_folders",
            "collision_policy": (
                "replace_exact" if flow.get("output_mode") == "direct_replace"
                else "number_suffix"
            ),
            "delete_existing": False,
            "overwrite_existing": flow.get("output_mode") == "direct_replace",
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
            "server": _flow_server_identity(),
            "mode": flow.get("sql_mode"),
            "uppercase": bool(flow.get("sql_uppercase")),
            "database": flow.get("sql_database"),
            "schema": flow.get("sql_schema"),
            "table": flow.get("sql_table"),
        },
    }


def queue_flow_run_service(
    db,
    flow_id: int,
    *,
    requested_by: str | None,
    trigger_type: str,
) -> tuple[int, dict]:
    """Create one durable Flow run for manual, scheduled, or pipeline callers."""
    if trigger_type not in {"manual", "scheduled", "pipeline", "resume", "sql_retry"}:
        raise ValueError("Unsupported Flow trigger type.")
    # The folder-wide direct-publish availability check and the queued row are
    # one reservation. Pipeline callers that already wrote on this connection
    # already hold SQLite's write slot; otherwise acquire it here.
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    if not db.execute("SELECT 1 FROM flows WHERE id=?", (flow_id,)).fetchone():
        raise HTTPException(404, "Flow not found.")
    active = db.execute(
        """SELECT id FROM flow_runs WHERE flow_id=?
           AND status IN ('queued','claimed','running') LIMIT 1""",
        (flow_id,),
    ).fetchone()
    if active:
        raise HTTPException(409, "This flow already has an active run.")
    job = _build_job(db, flow_id)
    from app.routers.pipelines import (
        assert_no_active_flow_publish_run,
        assert_no_active_flow_target_run,
        flow_target_resource_key_from_job,
    )
    assert_no_active_flow_target_run(db, flow_target_resource_key_from_job(job))
    assert_no_active_flow_publish_run(db, job)
    cursor = db.execute(
        """INSERT INTO flow_runs
               (flow_id, trigger_type, status, requested_by, job_json, created_at)
           VALUES (?, ?, 'queued', ?, ?, ?)""",
        (flow_id, trigger_type, requested_by, _json(job), _iso(_now())),
    )
    return int(cursor.lastrowid), job


def queue_due_flows() -> dict:
    """Queue due scheduled flows without executing browser work in the API process."""
    from app.routers.pipelines import (
        assert_no_active_flow_publish_run,
        assert_flow_target_available,
        assert_resource_unlocked,
        flow_target_resource_key_from_job,
    )

    now = _now()
    now_text = _iso(now)
    queued = []
    modes = set()
    with get_db() as db:
        # Serialize target inspection and run creation with manual/resume/retry
        # callers, preventing two distinct Flows from claiming one relation.
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT * FROM flows
               WHERE enabled=1 AND schedule_type != 'manual'
                 AND next_run_at IS NOT NULL AND next_run_at <= ?
               ORDER BY next_run_at, id""",
            (now_text,),
        ).fetchall()
        for row in rows:
            try:
                # Covers both the Flow itself and another Flow writing the
                # same exact SQL target during a governed pipeline run.
                assert_resource_unlocked(db, "flow", str(row["id"]))
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                continue
            active = db.execute(
                "SELECT id FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1",
                (row["id"],),
            ).fetchone()
            days = _loads(row["schedule_days"], [])
            next_run = _schedule_next(
                row["schedule_type"], row["schedule_time"], days, row["schedule_day"],
            )
            db.execute(
                "UPDATE flows SET next_run_at=?, updated_at=? WHERE id=?",
                (_iso(next_run), now_text, row["id"]),
            )
            if active:
                continue
            try:
                job = _build_job(db, row["id"])
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                db.execute("UPDATE flows SET last_error=? WHERE id=?", (str(exc.detail), row["id"]))
                continue
            try:
                assert_flow_target_available(
                    db, flow_target_resource_key_from_job(job)
                )
                assert_no_active_flow_publish_run(db, job)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                continue
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
        sites = [dict(row) for row in db.execute(
            "SELECT * FROM flow_sites WHERE adapter NOT IN (?, ?) ORDER BY name",
            (OUTLOOK_ATTACHMENT_ADAPTER, LOCAL_FILE_ADAPTER),
        ).fetchall()]
        reports = [dict(row) for row in db.execute(
            """SELECT r.* FROM flow_reports r JOIN flow_sites s ON s.id=r.site_id
               WHERE s.adapter NOT IN (?, ?) ORDER BY r.name""",
            (OUTLOOK_ATTACHMENT_ADAPTER, LOCAL_FILE_ADAPTER),
        ).fetchall()]
        report_ids = [report["id"] for report in reports]
        filters = (
            db.execute(
                f"SELECT * FROM flow_report_filters WHERE report_id IN ({','.join('?' for _ in report_ids)}) ORDER BY report_id, position, id",
                report_ids,
            ).fetchall()
            if report_ids else []
        )
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
        site["supports_discovery"] = site["adapter"] in DISCOVERY_ADAPTERS
        site["supports_partial_scan"] = site["adapter"] == ASAP_PORTAL_ADAPTER
        site["discovery_noun"] = DISCOVERY_LABELS.get(site["adapter"], "reports")
    for report in reports:
        report["enabled"] = bool(report["enabled"])
        report["stale"] = bool(report.get("stale"))
        report["automation"] = _loads(report.pop("automation_json", None), {})
        report["filters"] = by_report.get(report["id"], [])
    return {
        "sites": sites,
        "reports": reports,
        "control_types": sorted(CONTROL_TYPES),
        "asap_download_types": public_asap_download_types(),
    }


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
    if body.adapter in INTERNAL_FLOW_ADAPTERS:
        raise HTTPException(400, "That adapter is an internal flow source, not a configurable website.")
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
        existing = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (site_id,)).fetchone()
        if existing and existing["adapter"] in INTERNAL_FLOW_ADAPTERS:
            raise HTTPException(400, "Internal flow sources cannot be edited.")
        if body.adapter in INTERNAL_FLOW_ADAPTERS:
            raise HTTPException(400, "That adapter is an internal flow source, not a configurable website.")
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
            site = db.execute("SELECT id, adapter FROM flow_sites WHERE id = ?", (body.site_id,)).fetchone()
            if not site:
                raise HTTPException(400, "Website not found.")
            if site["adapter"] in INTERNAL_FLOW_ADAPTERS:
                raise HTTPException(400, "Internal flow sources cannot contain user reports.")
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
        existing = db.execute(
            """SELECT s.adapter FROM flow_reports r JOIN flow_sites s ON s.id=r.site_id
               WHERE r.id=?""",
            (report_id,),
        ).fetchone()
        target_site = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (body.site_id,)).fetchone()
        if existing and existing["adapter"] in INTERNAL_FLOW_ADAPTERS:
            raise HTTPException(400, "Internal flow reports cannot be edited.")
        if target_site and target_site["adapter"] in INTERNAL_FLOW_ADAPTERS:
            raise HTTPException(400, "Internal flow sources cannot contain user reports.")
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


@router.get("/activity")
def flow_activity():
    """A read-only display snapshot, independent of the run-history limit."""
    columns = """r.id, r.flow_id, f.name AS flow_name, r.status,
                 r.created_at, r.claimed_at, r.started_at, r.finished_at"""
    cutoff = _iso(_now() - timedelta(seconds=90))
    with get_db() as db:
        db.execute("BEGIN")
        latest = db.execute(
            """SELECT f.id AS flow_id, r.id, f.name AS flow_name, r.status,
                       r.created_at, r.claimed_at, r.started_at, r.finished_at
                FROM flows f LEFT JOIN flow_runs r ON r.id=(
                    SELECT id FROM flow_runs WHERE flow_id=f.id ORDER BY id DESC LIMIT 1)
                ORDER BY f.id"""
        ).fetchall()
        active = db.execute(
            f"""SELECT {columns} FROM flow_runs r JOIN flows f ON f.id=r.flow_id
                WHERE r.status IN ('queued','claimed','running') ORDER BY r.id DESC"""
        ).fetchall()
        from app.flow_activity import row_progress
        summaries = {}
        for row in [*latest, *active]:
            item = dict(row)
            if item["id"] is not None and item["id"] not in summaries:
                run = db.execute("SELECT id,status,worker_id,job_json,progress_json,artifact_json FROM flow_runs WHERE id=?", (item["id"],)).fetchone()
                summaries[item["id"]] = row_progress(db, run)
        workers = db.execute(
            """SELECT count(*) AS total,
                      coalesce(sum(CASE WHEN last_seen_at>=? AND status!='offline'
                                        THEN 1 ELSE 0 END), 0) AS online
               FROM flow_workers""", (cutoff,),
        ).fetchone()
    return {"latest_runs": [{**dict(row), "progress": summaries.get(row["id"])} for row in latest],
            "active_runs": [{**dict(row), "progress": summaries[row["id"]]} for row in active],
            "workers": dict(workers)}


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
            public_row = dict(row)
            public_row.pop("job_json", None)
            timings = db.execute(
                "SELECT phase, duration_ms, item_count, status FROM flow_operation_timings WHERE run_id=? ORDER BY id",
                (row["id"],),
            ).fetchall()
            result.append({
                **public_row, "job": _public_flow_job(_loads(row["job_json"], {})),
                "progress": _loads(row["progress_json"], {}),
                "artifacts": _loads(row["artifact_json"], []),
                "timings": [dict(item) for item in timings],
            })
        return result


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    with get_db() as db:
        row = db.execute(
            """SELECT r.*, f.name AS flow_name, f.sql_reconciliation_required FROM flow_runs r
               JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (run_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run not found.")
        public_row = dict(row)
        public_row.pop("job_json", None)
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
            """SELECT period_key, file_path, filename, storage_scope, artifact_store_id,
                      file_size, checksum, row_count, published_file_path,
                      published_filename, publish_status, status, created_at
               FROM flow_run_files WHERE run_id=? ORDER BY id""",
            (run_id,),
        ).fetchall()
        return {
            **public_row,
            "job": _public_flow_job(_loads(row["job_json"], {})),
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
            "downloads": flow_parallel.snapshot(db, run_id),
        }


def _recovery_result(
    status: str,
    reason_code: str,
    message: str,
    *,
    http_status: int = 409,
    **internal,
) -> dict:
    return {
        "status": status,
        "reason_code": reason_code,
        "message": message,
        "http_status": http_status,
        **internal,
    }


def inspect_sql_retry_eligibility(
    db,
    run_id: int,
    *,
    verify_artifact_files: bool = True,
    verify_remote_artifacts: bool = True,
) -> dict:
    """Pure preflight shared by the SQL-retry endpoint and AI read tools."""
    source = db.execute(
        """SELECT r.*, f.name AS flow_name, f.sql_reconciliation_required FROM flow_runs r
           JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
        (run_id,),
    ).fetchone()
    if not source:
        return _recovery_result(
            "not_applicable", "run_not_found", "Source flow run not found.", http_status=404
        )
    from app.routers.pipelines import (
        assert_flow_target_available,
        assert_resource_unlocked,
        flow_target_resource_key_from_job,
    )
    try:
        assert_resource_unlocked(db, "flow", str(source["flow_id"]))
    except HTTPException as exc:
        return _recovery_result(
            "blocked", "pipeline_lock", str(exc.detail), http_status=exc.status_code,
            _source=source,
        )
    if source["status"] not in RUN_TERMINAL:
        return _recovery_result(
            "blocked", "run_active", "Wait for the source run to finish before retrying SQL.",
            _source=source,
        )
    if source['sql_reconciliation_required']:
        return _recovery_result('blocked', 'sql_reconciliation_required',
            'Reconcile the uncertain SQL commit and acknowledge it before retrying.', _source=source)
    downloads = flow_parallel.snapshot(db, run_id)
    if downloads and downloads['completed'] != downloads['total']:
        return _recovery_result('blocked', 'download_bundle_incomplete',
            'Resume the missing exports before retrying SQL; the complete bundle is required.', _source=source)
    source_job = _loads(source["job_json"], {})
    sql_target = source_job.get("sql_handoff", {})
    if not sql_target.get("enabled"):
        return _recovery_result(
            "not_applicable", "sql_disabled", "The source run did not have SQL handoff enabled.",
            http_status=400, _source=source,
        )
    source_artifacts = _loads(source["artifact_json"], [])
    transformed = bool(source_job.get("transformation", {}).get("enabled"))
    if transformed:
        candidates = [item for item in source_artifacts if item.get("status") == "transformed"]
    else:
        candidates = [
            item for item in source_artifacts
            if item.get("status") not in {"transformed", "source_snapshot"}
        ]
    artifacts = [
        {
            key: item.get(key)
            for key in (
                "file_path", "filename", "period_key", "file_size", "checksum",
                "row_count", "status", "source_receipt", "storage_scope",
                "artifact_store_id",
            )
            if item.get(key) is not None
        }
        for item in candidates if item.get("file_path") and item.get("filename")
    ]
    if not artifacts:
        return _recovery_result(
            "not_applicable", "no_sql_artifacts", "The source run has no saved SQL-ready CSV artifacts.",
            _source=source,
        )
    source_receipt = next(
        (item.get("source_receipt") for item in artifacts if item.get("source_receipt")),
        None,
    )
    if (
        (source_job.get("flow", {}).get("source_type") or "portal") == "file"
        and (
            not source_receipt
            or source_receipt.get("kind") != "local_file"
            or not _local_file_receipt_is_current(
                db, int(source["flow_id"]), source_receipt,
                source_job.get("local_file") or {},
            )
        )
    ):
        return _recovery_result(
            "blocked", "local_file_snapshot_stale",
            "This saved file snapshot no longer matches the Flow's current source configuration or successful identity. Run the Flow again.",
            _source=source,
        )
    incomplete_private = [
        item["filename"] for item in artifacts
        if item.get("storage_scope") == "worker_private"
        and (
            not item.get("artifact_store_id")
            or item.get("file_size") is None
            or not item.get("checksum")
        )
    ]
    if incomplete_private:
        return _recovery_result(
            "blocked", "private_artifact_identity_missing",
            "Saved private SQL artifacts do not have a complete worker-store, size, and checksum identity: "
            + ", ".join(incomplete_private[:10]),
            _source=source,
        )
    if verify_artifact_files:
        if not verify_remote_artifacts:
            from app.path_safety import is_remote_file_path

            remote = [
                item["filename"] for item in artifacts
                if item.get("storage_scope") != "worker_private"
                and is_remote_file_path(item["file_path"])
            ]
            if remote:
                return _recovery_result(
                    "blocked", "remote_artifact_unverified",
                    "Saved SQL artifacts are on a network location that the read-only investigator did not probe. Use the normal Retry SQL control to run its authoritative file check.",
                    _source=source,
                )
        missing = [
            item["filename"] for item in artifacts
            if item.get("storage_scope") != "worker_private"
            and not Path(item["file_path"]).is_file()
        ]
        if missing:
            return _recovery_result(
                "blocked", "artifact_missing",
                f"Saved SQL artifact is no longer available on the BI desktop: {', '.join(missing[:10])}",
                _source=source,
            )
    if _source_folder_unavailable(db, run_id):
        return _recovery_result(
            "blocked", "folder_unavailable",
            "The saved files from this run were removed (or are being removed) by run folder cleanup. Use Run to download them again.",
            _source=source,
        )
    active = db.execute(
        """SELECT id FROM flow_runs WHERE flow_id=?
           AND status IN ('queued','claimed','running') LIMIT 1""",
        (source["flow_id"],),
    ).fetchone()
    if active:
        return _recovery_result(
            "blocked", "flow_active", "This flow already has an active run.",
            _source=source, active_run_id=int(active["id"]),
        )

    job = copy.deepcopy(source_job)
    job["job_type"] = "sql_retry"
    job["execution"] = {
        "mode": "local", "host": "bi_desktop", "browser_mode": "headless",
        "worker_id": LOCAL_WORKER_ID,
    }
    if (source_job.get("flow", {}).get("source_type") or "portal") == "file":
        job["execution"]["required_adapter"] = LOCAL_FILE_ADAPTER
    job["transformation"] = {
        "enabled": False,
        "source_run_id": run_id,
        "source_was_transformed": transformed,
    }
    job["sql_retry"] = {"source_run_id": run_id, "artifacts": artifacts}
    private_store_ids = {
        str(item.get("artifact_store_id")) for item in artifacts
        if item.get("storage_scope") == "worker_private" and item.get("artifact_store_id")
    }
    if len(private_store_ids) > 1:
        return _recovery_result(
            "blocked", "artifact_store_mismatch",
            "Saved SQL artifacts belong to more than one private worker store.",
            _source=source,
        )
    if private_store_ids:
        required_store_id = next(iter(private_store_ids))
        job["execution"]["required_artifact_store_id"] = required_store_id
        job["sql_retry"]["required_artifact_store_id"] = required_store_id
    if source_receipt:
        job["source_receipt"] = source_receipt
        if source_receipt.get("kind", "outlook") == "outlook":
            job["outlook_source_receipt"] = source_receipt
    try:
        assert_flow_target_available(db, flow_target_resource_key_from_job(job))
    except HTTPException as exc:
        return _recovery_result(
            "blocked", "sql_target_busy", str(exc.detail), http_status=exc.status_code,
            _source=source,
        )
    return _recovery_result(
        "eligible", "sql_artifacts_ready",
        f"{len(artifacts)} saved artifact(s) passed the SQL-retry preflight.",
        http_status=200, _source=source, _job=job, _artifacts=artifacts,
        _transformed=transformed,
    )


def inspect_resume_eligibility(db, run_id: int) -> dict:
    """Pure preflight shared by the Resume endpoint and AI read tools."""
    source = db.execute(
        """SELECT r.*, f.name AS flow_name, f.source_type, f.sql_reconciliation_required FROM flow_runs r
           JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
        (run_id,),
    ).fetchone()
    if not source:
        return _recovery_result(
            "not_applicable", "run_not_found", "Source flow run not found.", http_status=404
        )
    from app.routers.pipelines import (
        assert_no_active_flow_publish_run,
        assert_flow_target_available,
        assert_resource_unlocked,
        flow_target_resource_key_from_job,
    )
    try:
        assert_resource_unlocked(db, "flow", str(source["flow_id"]))
    except HTTPException as exc:
        return _recovery_result(
            "blocked", "pipeline_lock", str(exc.detail), http_status=exc.status_code,
            _source=source,
        )
    if source["status"] not in {"failed", "cancelled"}:
        return _recovery_result(
            "not_applicable", "status_not_resumable", "Only a failed or cancelled run can be resumed.",
            _source=source,
        )
    if source['sql_reconciliation_required']:
        return _recovery_result('blocked', 'sql_reconciliation_required',
            'Reconcile the uncertain SQL commit and acknowledge it before resuming.', _source=source)
    if (source["source_type"] or "portal") in {"outlook", "file"}:
        return _recovery_result(
            "not_applicable", "source_no_resume",
            "Outlook attachment and file-source runs cannot be resumed. Use Run to process the source again, or Retry SQL for a saved file.",
            _source=source,
        )
    active = db.execute(
        """SELECT id FROM flow_runs WHERE flow_id=?
           AND status IN ('queued','claimed','running') LIMIT 1""",
        (source["flow_id"],),
    ).fetchone()
    if active:
        return _recovery_result(
            "blocked", "flow_active", "This flow already has an active run.",
            _source=source, active_run_id=int(active["id"]),
        )
    source_job = _loads(source["job_json"], {})
    try:
        job = _build_job(db, source["flow_id"])
    except (KeyError, TypeError, ValueError) as exc:
        return _recovery_result(
            "blocked", "flow_configuration_invalid",
            f"The current Flow configuration cannot be queued: {exc}",
            http_status=409, _source=source,
        )
    source_output_mode = (source_job.get("downloads") or {}).get("output_mode", "run_folders")
    current_output_mode = (job.get("downloads") or {}).get("output_mode", "run_folders")
    if source_output_mode != current_output_mode:
        return _recovery_result(
            "blocked", "output_mode_changed",
            "The Flow output storage mode changed after this run. Start a fresh Run instead of mixing storage layouts.",
            _source=source,
        )
    carried = (source_job.get("resume") or {}).get("completed") or []
    saved = [
        {**item, "source_run_id": run_id}
        for item in _loads(source["artifact_json"], [])
        if item.get("status") == "saved" and item.get("file_path")
    ]
    completed, seen = [], set()
    had_saved = False
    for item in [*carried, *saved]:
        identity = {"export_view": item.get("export_view"), "period_key": item.get("period_key")}
        key = _json(identity)
        if key in seen:
            continue
        seen.add(key)
        had_saved = True
        entry = (
            {**item, **identity}
            if current_output_mode == "direct_replace" or int(job.get('execution', {}).get('download_parallelism') or 1) > 1
            else {
                **identity,
                **({"file_path": item.get("file_path")} if item.get("file_path") else {}),
                **(
                    {"source_run_id": item.get("source_run_id")}
                    if isinstance(item.get("source_run_id"), int) else {}
                ),
            }
        )
        source_run = entry.get("source_run_id")
        if item.get("file_path"):
            if isinstance(source_run, int) and _source_folder_unavailable(db, source_run):
                continue
        completed.append(entry)
    if not had_saved:
        return _recovery_result(
            "not_applicable", "no_completed_files",
            "No file finished in that run. Use Run to start the flow from the beginning.",
            _source=source,
        )
    try:
        job["resume"] = {"from_run_id": run_id, "completed": completed}
        if current_output_mode == "direct_replace":
            stores = sorted({item["artifact_store_id"] for item in completed if item.get("artifact_store_id")})
            if stores:
                job["execution"]["required_artifact_store_ids"] = stores
            roots = set()
            for source_id in {item.get("source_run_id") for item in completed if item.get("source_run_id")}:
                stored = db.execute("SELECT job_json FROM flow_runs WHERE id=?", (source_id,)).fetchone()
                if stored:
                    prior = _loads(stored[0], {})
                    root = (prior.get("paths") or {}).get("artifact_store_root")
                    if root:
                        roots.add(root)
                    roots.update((prior.get("resume") or {}).get("artifact_store_roots") or [])
            job["resume"]["artifact_store_roots"] = sorted(roots)
        assert_flow_target_available(db, flow_target_resource_key_from_job(job))
        assert_no_active_flow_publish_run(db, job)
    except HTTPException as exc:
        return _recovery_result(
            "blocked", "target_busy", str(exc.detail), http_status=exc.status_code,
            _source=source,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _recovery_result(
            "blocked", "flow_configuration_invalid",
            f"The current Flow configuration cannot be queued: {exc}",
            http_status=409, _source=source,
        )
    return _recovery_result(
        "eligible", "completed_files_available",
        f"Resume can reuse {len(completed)} completed file(s); the endpoint will revalidate before queueing.",
        http_status=200, _source=source, _job=job, _completed=completed,
    )


def inspect_fresh_run_eligibility(db, flow_id: int) -> dict:
    """Read-only preview of whether a new manual run is currently unblocked."""
    flow = db.execute("SELECT id FROM flows WHERE id=?", (flow_id,)).fetchone()
    if not flow:
        return _recovery_result(
            "not_applicable", "flow_not_found", "Flow not found.", http_status=404
        )
    from app.routers.pipelines import (
        assert_no_active_flow_publish_run,
        assert_flow_target_available,
        assert_resource_unlocked,
        flow_target_resource_key_from_job,
    )
    try:
        assert_resource_unlocked(db, "flow", str(flow_id))
        active = db.execute(
            """SELECT id FROM flow_runs WHERE flow_id=?
               AND status IN ('queued','claimed','running') LIMIT 1""",
            (flow_id,),
        ).fetchone()
        if active:
            return _recovery_result(
                "blocked", "flow_active", "This flow already has an active run.",
                active_run_id=int(active["id"]),
            )
        job = _build_job(db, flow_id)
        assert_flow_target_available(db, flow_target_resource_key_from_job(job))
        assert_no_active_flow_publish_run(db, job)
    except HTTPException as exc:
        return _recovery_result(
            "blocked", "resource_busy", str(exc.detail), http_status=exc.status_code
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _recovery_result(
            "blocked", "flow_configuration_invalid",
            f"The current Flow configuration cannot be queued: {exc}",
            http_status=409,
        )
    return _recovery_result(
        "eligible", "manual_run_available",
        "A fresh manual run is currently unblocked; the Run endpoint will revalidate before queueing.",
        http_status=200,
    )


@router.post("/runs/{run_id}/retry-sql")
def retry_run_sql(run_id: int, request: Request):
    """Queue SQL only from a terminal run's saved SQL-ready CSV artifacts."""
    now = _iso(_now())
    # A disconnected network share can make a file probe slow. Perform that
    # I/O before taking SQLite's global write reservation, then revalidate all
    # database locks/state inside the transaction immediately before insert.
    with get_db() as db:
        file_preflight = inspect_sql_retry_eligibility(db, run_id)
    if file_preflight["status"] != "eligible":
        raise HTTPException(file_preflight["http_status"], file_preflight["message"])
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        eligibility = inspect_sql_retry_eligibility(
            db, run_id, verify_artifact_files=False
        )
        if eligibility["status"] != "eligible":
            raise HTTPException(eligibility["http_status"], eligibility["message"])
        source = eligibility["_source"]
        job = eligibility["_job"]
        artifacts = eligibility["_artifacts"]
        cursor = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, requested_by, job_json, created_at)
               VALUES (?, 'sql_retry', 'queued', ?, ?, ?)""",
            (source["flow_id"], get_actor(request), _json(job), now),
        )
        new_run_id = cursor.lastrowid
        db.execute(
            """INSERT OR IGNORE INTO flow_run_source_refs
               (consumer_run_id, source_run_id, created_at) VALUES (?, ?, ?)""",
            (new_run_id, run_id, now),
        )
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
        "job": _public_flow_job(job),
        "worker": worker,
        "source_run_id": run_id,
    }


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: int, request: Request):
    """Queue a fresh run that skips every file the source run already saved.

    The new run rebuilds the job from the flow's current configuration, so a
    'latest available' period range can legitimately grow. Skipping matches on
    each file's export view + period identity; anything unmatched downloads
    normally. Resuming a run that was itself a resume carries the earlier
    completed files forward, so chained resumes keep narrowing the remainder.
    """
    now = _iso(_now())
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        eligibility = inspect_resume_eligibility(db, run_id)
        if eligibility["status"] != "eligible":
            raise HTTPException(eligibility["http_status"], eligibility["message"])
        source = eligibility["_source"]
        job = eligibility["_job"]
        completed = eligibility["_completed"]
        cursor = db.execute(
            """INSERT INTO flow_runs (flow_id, trigger_type, status, requested_by, job_json, created_at)
               VALUES (?, 'resume', 'queued', ?, ?, ?)""",
            (source["flow_id"], get_actor(request), _json(job), now),
        )
        pinned = {run_id} | {
            entry["source_run_id"] for entry in completed
            if isinstance(entry.get("source_run_id"), int)
        }
        db.executemany(
            """INSERT OR IGNORE INTO flow_run_source_refs
               (consumer_run_id, source_run_id, created_at) VALUES (?, ?, ?)""",
            [(cursor.lastrowid, source_id, now) for source_id in sorted(pinned)],
        )
        new_run_id = cursor.lastrowid
        log_event(
            db, "flow", source["flow_id"], source["flow_name"], "run_resumed",
            f"run_id={new_run_id}; resumes run #{run_id}; skips {len(completed)} saved file(s)",
            get_actor(request),
        )
    worker = launch_local_worker(job["execution"]["browser_mode"])
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
        "job": _public_flow_job(job),
        "worker": worker,
        "resumes_run_id": run_id,
        "skipped_files": len(completed),
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
        item["pool"] = "headed" if item["capabilities"].get("headed") else "headless"
        item["slot"] = flow_capacity.slot_number(item["worker_id"], item["pool"])
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
    managed = not body.target_folder
    if (body.download_parallelism or 1) > 1 and not managed:
        raise HTTPException(422, "Parallel downloads require a managed shared folder. Create the flow with its automatic folder.")
    allocated = None
    flow_id = None
    now = _iso(_now())
    next_run = _iso(_schedule_next(
        body.schedule_type, body.schedule_time, body.schedule_days, body.schedule_day,
    )) if body.enabled else None
    try:
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            _resolve_flow_source(db, body)
            if body.source_type == "file":
                body.target_folder = new_local_file_storage_key()
            elif managed:
                adapter = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (body.site_id,)).fetchone()
                if adapter:
                    body.target_folder = str(Path(flow_paths.get_flows_root(db)) / flow_paths.source_folder_name(adapter[0]) / "pending")
            _validate_flow_selections(db, body, new_flow=True)
            _validate_sql_target(db, body)
            _validate_owner(db, body)
            sql_target_source_id = _resolve_sql_target_source(db, body)
            cursor = db.execute(
                """INSERT INTO flows
                   (name, source_type, site_id, report_id, outlook_subject_contains,
                    local_file_path, local_file_worksheet, local_file_config_revision,
                    export_views_json, download_links_json, enabled, selections_json, download_mode, period_strategy, window_weeks, file_format, asap_download_type, export_report_title, export_filter_details, excel_trim, start_week, end_week,
                    browser_mode, target_folder, filename_template, output_mode, schedule_type, schedule_time, schedule_days, next_run_at,
                    schedule_day,
                    transform_enabled, transform_script_path, sql_handoff_enabled, sql_mode, sql_uppercase, sql_database, sql_schema, sql_table, sql_target_source_id, owner_person_id, created_by, created_at, updated_at,
                    freshness_effective_from_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (body.name, body.source_type, body.site_id, body.report_id,
                 body.outlook_subject_contains, body.local_file_path, body.local_file_worksheet, 1,
                 _json(body.export_views), _json(body.download_links), body.enabled, _json(body.selections),
                 body.download_mode, body.period_strategy, body.window_weeks, body.file_format,
                 body.asap_download_type, body.export_report_title, body.export_filter_details,
                 body.excel_trim, body.start_week, body.end_week, body.browser_mode, body.target_folder,
                 body.filename_template, body.output_mode, body.schedule_type, body.schedule_time,
                 _json(body.schedule_days), next_run, body.schedule_day,
                 body.transform_enabled, body.transform_script_path,
                 body.sql_handoff_enabled, body.sql_mode, body.sql_uppercase,
                 body.sql_database, body.sql_schema, body.sql_table, sql_target_source_id,
                 body.owner_person_id, get_actor(request), now, now,
                 iso_utc(utc_now()) if body.enabled and body.schedule_type != "manual" else None),
            )
            flow_id = cursor.lastrowid
            db.execute("UPDATE flows SET download_parallelism=? WHERE id=?", (body.download_parallelism or 1, flow_id))
            if managed:
                adapter = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (body.site_id,)).fetchone()[0]
                allocated = flow_layout.create_flow_folder(flow_paths.get_flows_root(db), adapter, body.name, flow_id)
                db.execute("""UPDATE flows SET flow_folder=?, folder_slug=?, folder_state='managed',
                    target_folder=? WHERE id=?""", (str(allocated), allocated.name,
                    body.target_folder if body.source_type == "file" else str(allocated / "Downloads"), flow_id))
                if body.transform_enabled:
                    script = flow_layout.import_script(str(allocated), flow_id, body.transform_script_path)
                    db.execute("UPDATE flows SET transform_script_path=? WHERE id=?", (script, flow_id))
            from app.freshness_inheritance import reconcile_file_binding, reconcile_source
            reconcile_file_binding(db, flow_id, reconcile_sources=False)
            if sql_target_source_id is not None:
                reconcile_source(db, int(sql_target_source_id))
            log_event(db, "flow", flow_id, body.name, "created", f"sql_handoff={body.sql_handoff_enabled}", get_actor(request))
            saved = _flow_out(db, flow_id)
        return _generate_saved_standalone(saved)
    except sqlite3.IntegrityError as exc:
        if allocated:
            flow_layout.cleanup_empty_creation(allocated, flow_id)
        raise HTTPException(409, "A flow with that name already exists.") from exc
    except Exception as exc:
        if allocated:
            flow_layout.cleanup_empty_creation(allocated, flow_id)
        if isinstance(exc, (OSError, ValueError)):
            raise HTTPException(409, f"Flow folder could not be created: {exc}") from exc
        raise


@router.post("/{flow_id}/adopt-folder")
def adopt_flow_folder(flow_id: int, request: Request):
    allocated = None
    try:
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            flow = _flow_out(db, flow_id, include_private_storage=True)
            from app.routers.pipelines import assert_resource_unlocked
            assert_resource_unlocked(db, "flow", str(flow_id))
            if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')", (flow_id,)).fetchone():
                raise HTTPException(409, "Wait for this flow's active run before adopting a folder.")
            root = flow_paths.get_flows_root(db)
            if flow.get("flow_folder") and flow_paths.is_inside(flow["flow_folder"], root):
                flow_layout.read_manifest(flow["flow_folder"], flow_id)
                return _flow_out(db, flow_id)
            previous = flow["target_folder"] if flow["source_type"] != "file" else None
            allocated = flow_layout.create_flow_folder(root, flow["source_adapter"], flow["name"], flow_id)
            flow["flow_folder"] = str(allocated)
            if flow["source_type"] != "file":
                flow["target_folder"] = str(allocated / "Downloads")
            if flow["transform_enabled"]:
                if flow_paths.setting(db, "flows_paths_enforced", "0") == "1":
                    flow_paths.assert_inside(flow["transform_script_path"], root, label="Transformation script")
                flow["transform_script_path"] = flow_layout.import_script(str(allocated), flow_id, flow["transform_script_path"])
            flow_paths.validate_flow(flow, flow_paths.policy(db, flow))
            db.execute("""UPDATE flows SET flow_folder=?, folder_slug=?, folder_state='managed',
                target_folder=?, transform_script_path=?, updated_at=? WHERE id=?""", (str(allocated), allocated.name,
                flow["target_folder"], flow["transform_script_path"], _iso(_now()), flow_id))
            from app.freshness_inheritance import reconcile_file_binding
            reconcile_file_binding(db, flow_id)
            log_event(db, "flow", flow_id, flow["name"], "folder_adopted", "Historical files preserved", get_actor(request))
            saved = {**_flow_out(db, flow_id), "previous_target_folder": previous}
        return _generate_saved_standalone(saved)
    except Exception as exc:
        if allocated:
            flow_layout.cleanup_empty_creation(allocated, flow_id)
        if isinstance(exc, (OSError, ValueError)):
            raise HTTPException(409, f"Flow folder could not be adopted: {exc}") from exc
        raise


class SQLReconciled(BaseModel):
    acknowledged: Literal[True]


@router.post('/{flow_id}/sql-reconciled')
def acknowledge_sql_reconciliation(flow_id: int, body: SQLReconciled, request: Request):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow = db.execute('SELECT name FROM flows WHERE id=?', (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, 'Flow not found.')
        if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')", (flow_id,)).fetchone():
            raise HTTPException(409, 'Wait for the active run and its downloads to stop first.')
        db.execute('UPDATE flows SET sql_reconciliation_required=0 WHERE id=?', (flow_id,))
        log_event(db, 'flow', flow_id, flow['name'], 'sql_reconciled', 'Operator acknowledged target reconciliation; future runs unblocked.', get_actor(request))
    return {'flow_id': flow_id, 'sql_reconciliation_required': False}


@router.post("/{flow_id}/open-folder")
def open_flow_folder(flow_id: int, request: Request):
    from app.flow_folder_access import open_folder
    with get_db() as db:
        flow = _flow_out(db, flow_id, include_private_storage=True)
        rules = flow_paths.policy(db, flow)
    try:
        return open_folder(flow, rules, request)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


def _generate_saved_standalone(saved: dict) -> dict:
    if not saved.get("flow_folder"):
        return saved
    from app import flow_standalone
    try:
        with get_db() as db:
            job = _build_job(db, saved["id"], force_reprocess=True)
        result = flow_standalone.generate(job)
    except (OSError, ValueError, RuntimeError, HTTPException) as exc:
        result = {"state": "error", "message": str(exc)}
    return {**saved, "standalone": result}


@router.post("/{flow_id}/standalone")
def regenerate_standalone(flow_id: int):
    with get_db() as db:
        saved = _flow_out(db, flow_id)
    if not saved.get("flow_folder"):
        raise HTTPException(409, "Adopt a managed folder first.")
    result = _generate_saved_standalone(saved)["standalone"]
    if result["state"] == "error":
        raise HTTPException(409, result["message"])
    return result


@router.get("/{flow_id}/standalone")
def standalone_status(flow_id: int):
    from app import flow_standalone
    with get_db() as db:
        job = _build_job(db, flow_id, force_reprocess=True)
    return flow_standalone.status(job)


@router.post("/{flow_id}/repair-layout")
def repair_flow_layout(flow_id: int, request: Request):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        flow = _flow_out(db, flow_id, include_private_storage=True)
        from app.routers.pipelines import assert_resource_unlocked
        assert_resource_unlocked(db, "flow", str(flow_id))
        if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')", (flow_id,)).fetchone():
            raise HTTPException(409, "Wait for this flow's active run before repairing its folder.")
        if not flow.get("flow_folder"):
            raise HTTPException(409, "Adopt a managed folder first.")
        try:
            flow_paths.validate_flow(flow, flow_paths.policy(db, flow))
            folder = Path(flow["flow_folder"])
            # Missing whole folders may be recreated only at their stored path.
            # An existing folder without our exact marker is never adopted.
            if not folder.exists() and not folder.is_symlink():
                expected = Path(flow_paths.get_flows_root(db)) / flow_paths.source_folder_name(flow["source_adapter"]) / flow["folder_slug"]
                if expected != folder:
                    raise ValueError("Stored folder path does not match this flow's layout.")
                folder.mkdir()
                now = _iso(_now())
                flow_layout.write_manifest(folder, {"schema": "metronome-flow-folder", "layout_version": 1,
                    "flow_id": flow_id, "flow_name": flow["name"], "source_adapter": flow["source_adapter"],
                    "created_at": now, "updated_at": now, "deleted_at": None})
            result = flow_layout.ensure_layout(folder, flow_id)
            if flow["transform_enabled"]:
                script = flow_layout.import_script(str(folder), flow_id, flow["transform_script_path"])
                db.execute("UPDATE flows SET transform_script_path=? WHERE id=?", (script, flow_id))
            log_event(db, "flow", flow_id, flow["name"], "layout_repaired", actor=get_actor(request))
            saved = {**_flow_out(db, flow_id), "layout": result}
        except (OSError, ValueError) as exc:
            raise HTTPException(409, f"Layout could not be repaired: {exc}") from exc
    return _generate_saved_standalone(saved)


@router.post("/transform-script")
async def add_transform_script(request: Request, file: UploadFile = File(...)):
    """Store a user-selected script locally without committing it to the repository."""
    filename = Path(ntpath.basename(file.filename or "")).name
    suffix = Path(filename).suffix.casefold()
    if not filename or suffix not in TRANSFORM_SCRIPT_SUFFIXES:
        raise HTTPException(400, "Choose a .py, .ps1, or .exe transformation script.")
    if (not SAFE_NAME_RE.fullmatch(filename) or filename.endswith((".", " "))
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", filename.split(".")[0])):
        raise HTTPException(400, "Choose a script with a safe Windows filename.")
    content = await file.read(10 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(400, "The selected transformation script is empty.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Transformation scripts must be 10 MB or smaller.")
    import uuid
    with get_db() as db:
        root = flow_paths.get_flows_root(db)
    folder = Path(root) / ".metronome" / "uploads" / str(uuid.uuid4())
    flow_paths.assert_inside(str(folder), root, label="Upload folder")
    folder.mkdir(parents=True, exist_ok=False)
    candidate = folder / filename
    if candidate.exists():
        stem = candidate.stem
        for index in range(2, 10000):
            candidate = folder / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                break
        else:
            raise HTTPException(409, "Could not reserve a unique script filename.")
    with candidate.open("xb") as handle:
        handle.write(content)
    with get_db() as db:
        log_event(
            db, "flow_transform_script", None, candidate.name,
            "added", f"size={len(content)}", get_actor(request),
        )
    return {"script_path": str(candidate), "filename": candidate.name, "file_size": len(content)}


@router.put("/{flow_id}")
def update_flow(flow_id: int, body: FlowWrite, request: Request):
    now = _iso(_now())
    next_run = _iso(_schedule_next(
        body.schedule_type, body.schedule_time, body.schedule_days, body.schedule_day,
    )) if body.enabled else None
    with get_db() as db:
        existing = db.execute(
            """SELECT source_type, enabled, schedule_type, schedule_time, schedule_days,
                      schedule_day, freshness_effective_from_at,
                      sql_database, sql_schema, sql_table, sql_target_source_id,
                      target_folder, local_file_path, local_file_worksheet, flow_folder,
                      local_file_last_identity, local_file_config_revision, download_parallelism
               FROM flows WHERE id=?""",
            (flow_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Flow not found.")
        if body.download_parallelism is None:
            body.download_parallelism = existing['download_parallelism']
        if body.download_parallelism > 1 and not existing['flow_folder']:
            raise HTTPException(422, "Adopt a managed shared folder before enabling parallel downloads.")
        from app.routers.pipelines import assert_resource_unlocked
        assert_resource_unlocked(db, "flow", str(flow_id))
        if (existing["source_type"] or "portal") != body.source_type:
            raise HTTPException(409, "A flow's source category cannot be changed after creation.")
        _resolve_flow_source(db, body)
        if existing["flow_folder"] or not body.target_folder:
            body.target_folder = existing["target_folder"]
        if existing["flow_folder"]:
            if body.transform_enabled:
                try:
                    if flow_paths.setting(db, "flows_paths_enforced", "0") == "1":
                        flow_paths.assert_inside(body.transform_script_path, flow_paths.get_flows_root(db), label="Transformation script")
                    body.transform_script_path = flow_layout.import_script(existing["flow_folder"], flow_id, body.transform_script_path)
                except (OSError, ValueError) as exc:
                    raise HTTPException(409, f"Transformation script could not be placed: {exc}") from exc
            adapter = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (body.site_id,)).fetchone()
            if adapter:
                candidate = {**body.model_dump(), "flow_folder": existing["flow_folder"], "source_adapter": adapter[0]}
                try:
                    flow_paths.validate_flow(candidate, flow_paths.policy(db, candidate))
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
        local_file_revision = int(existing["local_file_config_revision"] or 1)
        local_file_last_identity = existing["local_file_last_identity"]
        if body.source_type == "file":
            body.target_folder = existing["target_folder"]
            source_changed = (
                normalize_target_path(existing["local_file_path"] or "")
                != normalize_target_path(body.local_file_path or "")
                or existing["local_file_worksheet"] != body.local_file_worksheet
            )
            if source_changed:
                local_file_revision += 1
                local_file_last_identity = None
        _validate_flow_selections(db, body)
        _validate_sql_target(db, body)
        _validate_owner(db, body)
        target_changed = (
            (existing["sql_database"] or None, existing["sql_schema"] or None, existing["sql_table"] or None)
            != (body.sql_database, body.sql_schema, body.sql_table)
        )
        if target_changed:
            body.sql_target_source_id = None
        preserve_invalid_source_id = (
            int(existing["sql_target_source_id"])
            if not target_changed
            and body.sql_handoff_enabled
            and existing["sql_target_source_id"] is not None
            else None
        )
        sql_target_source_id = _resolve_sql_target_source(
            db,
            body,
            preserve_invalid_source_id=preserve_invalid_source_id,
        )
        old_rule = schedule_rule(
            existing["schedule_type"], existing["schedule_time"],
            _loads(existing["schedule_days"], []), existing["schedule_day"],
        )
        new_rule = schedule_rule(
            body.schedule_type, body.schedule_time, body.schedule_days, body.schedule_day,
        )
        freshness_baseline = existing["freshness_effective_from_at"]
        if body.enabled and (
            not existing["enabled"]
            or rule_key(old_rule) != rule_key(new_rule)
            or not freshness_baseline
        ):
            freshness_baseline = iso_utc(utc_now())
        cursor = db.execute(
            """UPDATE flows SET name=?, source_type=?, site_id=?, report_id=?, outlook_subject_contains=?,
               local_file_path=?, local_file_worksheet=?, local_file_last_identity=?, local_file_config_revision=?,
               export_views_json=?, download_links_json=?, enabled=?, selections_json=?,
               download_mode=?, period_strategy=?, window_weeks=?, file_format=?, asap_download_type=?, export_report_title=?, export_filter_details=?, excel_trim=?, start_week=?, end_week=?, browser_mode=?, target_folder=?, filename_template=?, output_mode=?,
               schedule_type=?, schedule_time=?, schedule_days=?, schedule_day=?, next_run_at=?,
               transform_enabled=?, transform_script_path=?,
               sql_handoff_enabled=?, sql_mode=?, sql_uppercase=?, sql_database=?, sql_schema=?, sql_table=?, sql_target_source_id=?, owner_person_id=?, updated_at=?, freshness_effective_from_at=? WHERE id=?""",
            (body.name, body.source_type, body.site_id, body.report_id,
             body.outlook_subject_contains, body.local_file_path, body.local_file_worksheet,
             local_file_last_identity, local_file_revision,
             _json(body.export_views), _json(body.download_links), body.enabled, _json(body.selections),
             body.download_mode, body.period_strategy, body.window_weeks, body.file_format,
             body.asap_download_type, body.export_report_title, body.export_filter_details,
             body.excel_trim, body.start_week, body.end_week, body.browser_mode, body.target_folder,
             body.filename_template, body.output_mode, body.schedule_type, body.schedule_time,
             _json(body.schedule_days), body.schedule_day, next_run,
             body.transform_enabled, body.transform_script_path,
             body.sql_handoff_enabled, body.sql_mode, body.sql_uppercase,
             body.sql_database, body.sql_schema, body.sql_table, sql_target_source_id,
             body.owner_person_id, now, freshness_baseline, flow_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Flow not found.")
        db.execute("UPDATE flows SET download_parallelism=? WHERE id=?", (body.download_parallelism, flow_id))
        from app.freshness_inheritance import reconcile_file_binding, reconcile_source
        reconcile_file_binding(db, flow_id)
        for source_id in {existing["sql_target_source_id"], sql_target_source_id} - {None}:
            reconcile_source(db, int(source_id))
        log_event(db, "flow", flow_id, body.name, "updated", actor=get_actor(request))
        if existing["flow_folder"]:
            try:
                flow_layout.update_manifest(existing["flow_folder"], flow_id, flow_name=body.name)
            except (OSError, ValueError) as exc:
                log_event(db, "flow", flow_id, body.name, "folder_manifest_warning", str(exc), get_actor(request))
        saved = _flow_out(db, flow_id)
    return _generate_saved_standalone(saved)


class FlowInlineWrite(BaseModel):
    model_config = {"extra": "forbid"}
    owner_person_id: int | None = Field(default=None, ge=1, strict=True)
    browser_mode: Literal["headless", "headed"] | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("Provide owner_person_id or browser_mode.")
        if "browser_mode" in self.model_fields_set and self.browser_mode is None:
            raise ValueError("browser_mode must be headless or headed.")
        return self


@router.patch("/{flow_id}")
def patch_flow(flow_id: int, body: FlowInlineWrite, request: Request):
    changes = body.model_dump(exclude_unset=True)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        flow = db.execute("SELECT * FROM flows WHERE id=?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        from app.routers.pipelines import assert_resource_unlocked
        assert_resource_unlocked(db, "flow", str(flow_id))
        if body.owner_person_id is not None and not db.execute(
            "SELECT id FROM people WHERE id=?", (body.owner_person_id,),
        ).fetchone():
            raise HTTPException(400, "Choose an existing People record.")
        if "browser_mode" in changes and (flow["source_type"] or "portal") != "portal":
            raise HTTPException(400, "Browser mode is supported only for website flows.")
        db.execute(
            f"UPDATE flows SET {', '.join(key + '=?' for key in changes)}, updated_at=? WHERE id=?",
            (*changes.values(), _iso(_now()), flow_id),
        )
        log_event(db, "flow", flow_id, flow["name"], "updated",
                  detail=json.dumps({key: {"before": flow[key], "after": value}
                                     for key, value in changes.items()}), actor=get_actor(request))
        saved = {'id': flow_id, 'flow_folder': flow['flow_folder']}
    result = {"id": flow_id, **changes}
    if 'browser_mode' in changes and saved['flow_folder']:
        result['standalone'] = _generate_saved_standalone(saved)['standalone']
    return result


@router.patch("/{flow_id}/enabled")
def set_flow_enabled(flow_id: int, body: FlowEnabledWrite, request: Request):
    now = _iso(_now())
    with get_db() as db:
        flow = db.execute("SELECT * FROM flows WHERE id=?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        from app.routers.pipelines import assert_resource_unlocked
        assert_resource_unlocked(db, "flow", str(flow_id))
        if body.enabled and flow["schedule_type"] == "manual":
            raise HTTPException(400, "Choose a daily, weekly, or monthly schedule before activating this flow.")
        next_run = (
            _iso(_schedule_next(
                flow["schedule_type"], flow["schedule_time"],
                _loads(flow["schedule_days"], []), flow["schedule_day"],
            ))
            if body.enabled else None
        )
        baseline = iso_utc(utc_now()) if body.enabled else flow["freshness_effective_from_at"]
        db.execute(
            """UPDATE flows SET enabled=?, next_run_at=?,
               freshness_effective_from_at=?, updated_at=? WHERE id=?""",
            (body.enabled, next_run, baseline, now, flow_id),
        )
        from app.freshness_inheritance import reconcile_source
        linked_source_ids = {
            int(row["source_id"])
            for row in db.execute(
                "SELECT source_id FROM flow_file_source_bindings WHERE flow_id=? AND active=1",
                (flow_id,),
            ).fetchall()
        }
        if flow["sql_target_source_id"] is not None:
            linked_source_ids.add(int(flow["sql_target_source_id"]))
        for source_id in linked_source_ids:
            reconcile_source(db, source_id)
        log_event(
            db, "flow", flow_id, flow["name"],
            "activated" if body.enabled else "deactivated", actor=get_actor(request),
        )
        return _flow_out(db, flow_id)


@router.delete("/{flow_id}")
def delete_flow(flow_id: int, body: FlowDeleteWrite, request: Request):
    """Permanently remove one paused Flow and its database-held run history.

    Output files and transformation scripts are deliberately outside this
    operation: deleting a Flow must never turn into an unbounded filesystem
    cleanup. The exact-name body guard also protects direct API callers rather
    than relying on the browser confirmation alone.
    """
    now = _iso(_now())
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        flow = db.execute(
            "SELECT id, name, enabled, sql_target_source_id, flow_folder FROM flows WHERE id=?",
            (flow_id,),
        ).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        if body.confirmation != flow["name"]:
            raise HTTPException(400, "Type the exact flow name to confirm deletion.")
        if flow["enabled"]:
            raise HTTPException(409, "Pause the flow before deleting it.")

        from app.freshness_inheritance import reconcile_source
        from app.routers.pipelines import assert_resource_unlocked

        assert_resource_unlocked(db, "flow", str(flow_id))
        active = db.execute(
            """SELECT id FROM flow_runs
               WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1""",
            (flow_id,),
        ).fetchone()
        if active:
            raise HTTPException(
                409,
                f"Flow run #{active['id']} is still active. Stop it before deleting the flow.",
            )

        linked_source_ids = {
            int(row["source_id"])
            for row in db.execute(
                "SELECT source_id FROM flow_file_source_bindings WHERE flow_id=?",
                (flow_id,),
            ).fetchall()
        }
        if flow["sql_target_source_id"] is not None:
            linked_source_ids.add(int(flow["sql_target_source_id"]))

        run_count = int(db.execute(
            "SELECT COUNT(*) FROM flow_runs WHERE flow_id=?", (flow_id,),
        ).fetchone()[0])
        recorded_file_count = int(db.execute(
            """SELECT COUNT(*) FROM flow_run_files
               WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
            (flow_id,),
        ).fetchone()[0])
        if run_count:
            # Pipeline history keeps its own immutable step name/details. Only
            # detach the deleted Flow-run record so the pipeline remains usable.
            db.execute(
                """UPDATE pipeline_run_steps SET flow_run_id=NULL
                   WHERE flow_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id,),
            )
            db.execute(
                """UPDATE flow_workers SET current_run_id=NULL
                   WHERE current_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id,),
            )
            db.execute(
                """DELETE FROM flow_run_source_refs
                   WHERE consumer_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)
                      OR source_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id, flow_id),
            )
            db.execute(
                """DELETE FROM flow_retention_ops
                   WHERE source_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)
                      OR assigned_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id, flow_id),
            )
            db.execute(
                """DELETE FROM flow_operation_timings
                   WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id,),
            )
            db.execute(
                """DELETE FROM flow_run_events
                   WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id,),
            )
            db.execute(
                """DELETE FROM flow_run_files
                   WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)""",
                (flow_id,),
            )
            db.execute("""UPDATE flow_workers SET current_task_id=NULL WHERE current_task_id IN
                (SELECT id FROM flow_download_tasks WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?))""", (flow_id,))
            db.execute('DELETE FROM flow_download_tasks WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)', (flow_id,))
            db.execute('DELETE FROM flow_run_fanout WHERE run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)', (flow_id,))
            db.execute("DELETE FROM flow_runs WHERE flow_id=?", (flow_id,))

        # Keep closed incident records as audit evidence without a dangling FK.
        resolved_actions = db.execute(
            """UPDATE actions
               SET flow_id=NULL,
                   status=CASE WHEN status IN ('open','acknowledged','investigating')
                               THEN 'resolved' ELSE status END,
                   resolved_at=CASE WHEN status IN ('open','acknowledged','investigating')
                                    THEN COALESCE(resolved_at, ?) ELSE resolved_at END,
                   updated_at=?,
                   notes=COALESCE(notes, '') || ' [auto-resolved: flow deleted]'
               WHERE flow_id=?""",
            (now, now, flow_id),
        ).rowcount
        db.execute("DELETE FROM flow_file_source_bindings WHERE flow_id=?", (flow_id,))
        db.execute("DELETE FROM flows WHERE id=?", (flow_id,))
        for source_id in linked_source_ids:
            reconcile_source(db, source_id)
        if flow["flow_folder"]:
            try:
                flow_layout.update_manifest(flow["flow_folder"], flow_id, deleted_at=now)
            except (OSError, ValueError) as exc:
                log_event(db, "flow", flow_id, flow["name"], "folder_manifest_warning", str(exc), get_actor(request))
        log_event(
            db,
            "flow",
            flow_id,
            flow["name"],
            "deleted",
            (
                f"runs={run_count}; recorded_files={recorded_file_count}; "
                f"resolved_actions={resolved_actions}; filesystem_files_preserved=true"
            ),
            get_actor(request),
        )
    return {
        "id": flow_id,
        "name": flow["name"],
        "deleted": True,
        "deleted_runs": run_count,
        "preserved_files": recorded_file_count,
    }


def queue_flow_run(
    flow_id: int,
    *,
    actor: str | None,
    trigger_type: Literal["manual", "remote_test"] = "manual",
    allow_queued_resume: bool = True,
    require_enabled: bool = False,
    expected_name_sha256: str | None = None,
) -> dict:
    """Queue one Flow run through the shared, transaction-safe path.

    Remote callers disable queued-run resume and provide an exact-name digest,
    making an ID reuse or rename fail in the same transaction that creates the
    run.  The public HTTP route retains its historical manual behavior.
    """
    import hashlib

    now = _iso(_now())
    resumed = False
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        flow = db.execute(
            "SELECT id, name, source_type, enabled FROM flows WHERE id = ?",
            (flow_id,),
        ).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        if require_enabled and not bool(flow["enabled"]):
            raise HTTPException(409, "The exact Flow is disabled.")
        if expected_name_sha256 is not None:
            observed_digest = hashlib.sha256(flow["name"].encode("utf-8")).hexdigest()
            if observed_digest != expected_name_sha256:
                raise HTTPException(409, "The exact Flow identity changed.")
        from app.routers.pipelines import (
            assert_no_active_flow_publish_run,
            assert_flow_target_available,
            assert_resource_unlocked,
            flow_target_resource_key_from_job,
        )
        assert_resource_unlocked(db, "flow", str(flow_id))
        active = db.execute(
            """SELECT id, status, job_json FROM flow_runs
               WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1""",
            (flow_id,),
        ).fetchone()
        if active:
            if not allow_queued_resume:
                raise HTTPException(409, "This flow already has an active run.")
            if active["status"] != "queued":
                raise HTTPException(409, "This flow already has an active run.")
            # A queued run may be waiting because Windows could not start its
            # worker. Let Run retry the launcher without creating a duplicate.
            run_id = active["id"]
            job = _loads(active["job_json"], {})
            if (flow["source_type"] or "portal") in {"outlook", "file"}:
                source_key = "outlook_source" if flow["source_type"] == "outlook" else "local_file"
                job.setdefault(source_key, {})["force_reprocess"] = True
                db.execute(
                    """UPDATE flow_runs SET trigger_type=?, requested_by=?, job_json=?
                       WHERE id=?""",
                    (trigger_type, actor, _json(job), run_id),
                )
            assert_flow_target_available(
                db,
                flow_target_resource_key_from_job(job),
                exclude_run_id=int(run_id),
            )
            assert_no_active_flow_publish_run(db, job, exclude_run_id=int(run_id))
            resumed = True
            log_event(
                db, "flow", flow_id, flow["name"], "worker_restart_requested",
                f"run_id={run_id}", actor,
            )
        else:
            job = _build_job(
                db, flow_id,
                force_reprocess=(flow["source_type"] or "portal") in {"outlook", "file"},
            )
            assert_flow_target_available(db, flow_target_resource_key_from_job(job))
            assert_no_active_flow_publish_run(db, job)
            cursor = db.execute(
                """INSERT INTO flow_runs (flow_id, trigger_type, status, requested_by, job_json, created_at)
                   VALUES (?, ?, 'queued', ?, ?, ?)""",
                (flow_id, trigger_type, actor, _json(job), now),
            )
            run_id = cursor.lastrowid
            log_event(
                db, "flow", flow_id, flow["name"], "run_queued",
                f"run_id={run_id}; trigger_type={trigger_type}", actor,
            )
    worker = launch_local_worker(job["execution"]["browser_mode"])
    if worker.get("status") == "error":
        with get_db() as db:
            db.execute(
                "UPDATE flow_runs SET progress_json=? WHERE id=?",
                (_json({"stage": "waiting_for_bi_desktop", "message": worker.get("message")}), run_id),
            )
    return {
        "id": run_id, "flow_id": flow_id, "status": "queued", "job": _public_flow_job(job),
        "worker": worker, "resumed": resumed,
    }


@router.post("/{flow_id}/run")
def queue_run(flow_id: int, request: Request):
    return queue_flow_run(flow_id, actor=get_actor(request))


@router.post("/{flow_id}/stop")
def stop_run(flow_id: int, request: Request):
    """Cancel a queued run or close the exact worker assigned to an active run."""
    now = _iso(_now())
    process_id = None
    run_id = None
    browser_mode = "headless"
    stop_assigned_worker = False
    message = ""
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow = db.execute("SELECT id, name FROM flows WHERE id=?", (flow_id,)).fetchone()
        if not flow:
            raise HTTPException(404, "Flow not found.")
        from app.routers.pipelines import assert_resource_unlocked
        assert_resource_unlocked(db, "flow", str(flow_id))
        row = db.execute(
            """SELECT * FROM flow_runs WHERE flow_id=?
               AND status IN ('queued','claimed','running') ORDER BY id DESC LIMIT 1""",
            (flow_id,),
        ).fetchone()
        if not row:
            raise HTTPException(409, "This flow has no active run to stop.")
        if flow_parallel._fanout(db, row['id']):
            # Classify Stop atomically against task initialization. Release the
            # write transaction before the parallel path stops OS processes.
            db.commit()
            return flow_parallel.request_stop(row['id'])
        job = _loads(row["job_json"], {})
        browser_mode = job.get("execution", {}).get("browser_mode", "headless")
        run_id = row["id"]
        worker_id = row["worker_id"]
        stop_assigned_worker = row["status"] in {"claimed", "running"} and bool(worker_id)
        if stop_assigned_worker:
            worker = db.execute(
                "SELECT capabilities_json FROM flow_workers WHERE worker_id=? AND current_run_id=?",
                (worker_id, run_id),
            ).fetchone()
            capabilities = _loads(worker["capabilities_json"], {}) if worker else {}
            raw_pid = capabilities.get("process_id")
            process_id = raw_pid if isinstance(raw_pid, int) and raw_pid > 0 else None
            message = "Stop requested by user for the assigned browser worker."
        else:
            message = "Cancelled by user before a worker started this run."
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
        if stop_assigned_worker:
            db.execute(
                """UPDATE flow_workers SET status='offline', current_run_id=NULL, last_error=?, updated_at=?
                   WHERE worker_id=? AND current_run_id=?""",
                (message, now, worker_id, run_id),
            )
        log_event(db, "flow", flow_id, flow["name"], "run_cancelled", f"run_id={run_id}", get_actor(request))
    stopped = (
        stop_local_worker(browser_mode, process_id, worker_id=worker_id)
        if stop_assigned_worker
        else {"status": "not_needed", "message": "The run had not been assigned to a worker."}
    )
    if stop_assigned_worker:
        message = (
            "Run stopped and its assigned browser worker was closed."
            if stopped.get("status") == "stopped"
            else f"Run cancelled, but Windows could not confirm that the browser worker closed: {stopped.get('message') or stopped.get('status')}."
        )
    return {"run_id": run_id, "status": "cancelled", "message": message, "worker": stopped}


def ensure_local_worker() -> dict:
    """Ensure configured fixed slots; never stop busy slots after a reduction."""
    cutoff = _iso(_now() - timedelta(seconds=90))
    with get_db() as db:
        capacity = flow_capacity.headless_capacity(db)
        rows = {row["worker_id"]: row for row in db.execute(
            "SELECT worker_id, status, current_run_id, current_scan_id, current_task_id, last_seen_at FROM flow_workers")}
    results = []
    for slot in range(1, capacity + 1):
        identity = flow_capacity.worker_id(slot)
        row = rows.get(identity)
        if row and (row["current_run_id"] or row["current_scan_id"] or row['current_task_id']):
            results.append({"status": "busy", "mode": "local", "worker_id": identity})
        elif row and row["status"] != "offline" and row["last_seen_at"] and row["last_seen_at"] >= cutoff:
            results.append({"status": "online", "mode": "local", "worker_id": identity})
        else:
            results.append(launch_local_worker("headless") if slot == 1 else launch_local_worker("headless", slot=slot))
    return {**results[0], "headless_capacity": capacity, "slots": results}


def _scan_out(row) -> dict:
    result = dict(row)
    result["job"] = _loads(result.pop("job_json", None), {})
    result["progress"] = _loads(result.pop("progress_json", None), {})
    result["result"] = _loads(result.pop("result_json", None), {})
    return result


def _scan_browser_mode(db, site) -> str:
    """The browser mode a catalog scan of this site must run under.

    A GSCM scan walks the portal exactly the way a flow run does (gear,
    Setting, Favorite, tabs), so it must run on the same worker as the runs
    that are known to work against this site - same browser mode, same
    browser profile, same signed-in session. A scan pinned to the headless
    service while the site's runs execute on the headed worker opens a
    different browser with a different profile, where the same gear click
    fails. The most recent successful run decides the mode; before any run
    has succeeded, an enabled headed flow does. Other adapters keep
    background headless discovery.
    """
    if site["adapter"] != GSCM_PORTAL_ADAPTER:
        return "headless"
    recent = db.execute(
        """SELECT f.browser_mode FROM flow_runs r JOIN flows f ON f.id=r.flow_id
           WHERE f.site_id=? AND r.status='succeeded'
           ORDER BY r.finished_at DESC, r.id DESC LIMIT 1""",
        (site["id"],),
    ).fetchone()
    if recent and recent["browser_mode"] in BROWSER_MODES:
        return recent["browser_mode"]
    # "enabled" only means scheduled: a manual-only flow still runs headed,
    # so any headed flow on the site counts before a run has succeeded.
    headed = db.execute(
        "SELECT 1 FROM flows WHERE site_id=? AND browser_mode='headed' LIMIT 1",
        (site["id"],),
    ).fetchone()
    return "headed" if headed else "headless"


def _resolve_inherited_asap_export_settings(
    db, report_id: int, automation: dict, observed_at: str,
) -> None:
    """Pin legacy NULL checkbox choices only after a consistent live observation."""
    capabilities = automation.get("asap_export_capabilities") or {}
    by_view = capabilities.get("views") or {}
    if not by_view:
        return
    for row in db.execute(
        """SELECT id, export_views_json, file_format, asap_download_type,
                  export_report_title, export_filter_details
             FROM flows WHERE report_id=?
               AND (export_report_title IS NULL OR export_filter_details IS NULL)""",
        (report_id,),
    ).fetchall():
        download_type = resolve_asap_download_type(
            row["asap_download_type"], legacy_file_format=row["file_format"],
        ).key
        views = _loads(row["export_views_json"], [])
        if not views:
            views = [
                str(item.get("label") if isinstance(item, dict) else item).strip()
                for item in automation.get("export_views", [])
                if str(item.get("label") if isinstance(item, dict) else item).strip()
            ]
        if not views:
            views = [str(automation.get("report_tab") or "").strip() or "__default__"]
        records = [by_view.get(view) for view in views]
        if not records or not all(
            isinstance(record, dict) and record.get("status") == "detected"
            for record in records
        ):
            continue
        resolved = {}
        for option_key in ("export_report_title", "export_filter_details"):
            if row[option_key] is not None:
                continue
            observations = [
                ((record.get("options_by_type") or {}).get(download_type, {}).get(option_key) or {})
                for record in records
            ]
            if not all(item.get("available") and isinstance(item.get("checked"), bool)
                       for item in observations):
                continue
            values = {item["checked"] for item in observations}
            if len(values) == 1:
                resolved[option_key] = int(values.pop())
        if resolved:
            assignments = ", ".join(f"{key}=?" for key in resolved)
            db.execute(
                f"UPDATE flows SET {assignments}, updated_at=? WHERE id=?",
                (*resolved.values(), observed_at, row["id"]),
            )


def _queue_scan(db, site, trigger_type: str, requested_by: str | None, report=None,
                mode: str = "full") -> tuple[int, str]:
    """Queue one catalog scan; returns (scan id, its browser mode)."""
    active = db.execute(
        "SELECT id, job_json FROM flow_catalog_scans WHERE site_id=? AND status IN ('queued','claimed','running') LIMIT 1",
        (site["id"],),
    ).fetchone()
    if active:
        active_mode = _loads(active["job_json"], {}).get("execution", {}).get(
            "browser_mode", "headless",
        )
        return active["id"], active_mode
    browser_mode = _scan_browser_mode(db, site)
    report_automation = _loads(report["automation_json"], {}) if report else {}
    raw_category_path = report_automation.get("category_path", [])
    category_path = list(raw_category_path) if isinstance(raw_category_path, list) else []
    catalog_name = (
        str(category_path[-1]).strip()
        if category_path and str(category_path[-1]).strip()
        else str(report["name"]).strip() if report else ""
    )
    job = {
        "schema_version": 1,
        "job_type": "catalog_scan",
        "site": {
            "id": site["id"], "name": site["name"], "adapter": site["adapter"],
            "base_url": site["base_url"], "auth_url": site["auth_url"],
        },
        "execution": {"browser_mode": browser_mode},
        "discovery": {
            "scope": ["*"], "delete_missing": False, "max_duration_minutes": 90,
            "mode": mode if mode in SCAN_MODES else "full",
            # Kept for one release so workers predating target_report can still
            # execute a targeted refresh.
            "report_paths": [category_path] if report else [],
        },
        "target_report": (
            {
                "id": report["id"],
                "catalog_name": catalog_name,
                "category_path": category_path,
                "favorite_bookmark_id": report_automation.get("favorite_bookmark_id"),
            }
            if report else None
        ),
    }
    cursor = db.execute(
        """INSERT INTO flow_catalog_scans
           (site_id, trigger_type, status, requested_by, job_json, created_at)
           VALUES (?, ?, 'queued', ?, ?, ?)""",
        (site["id"], trigger_type, requested_by, _json(job), _iso(_now())),
    )
    return cursor.lastrowid, browser_mode


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


@router.get("/scans/{scan_id}/events")
def list_scan_events(scan_id: int, after_id: int = Query(default=0, ge=0),
                     limit: int = Query(default=400, ge=1, le=1000)):
    """The scan's progress log, oldest first, for the live catalog log window."""
    with get_db() as db:
        scan = db.execute(
            "SELECT id, status FROM flow_catalog_scans WHERE id=?", (scan_id,)
        ).fetchone()
        if not scan:
            raise HTTPException(404, "Scan not found.")
        rows = db.execute(
            """SELECT id, status, stage, message, created_at FROM flow_scan_events
               WHERE scan_id=? AND id>? ORDER BY id LIMIT ?""",
            (scan_id, after_id, limit),
        ).fetchall()
    return {
        "scan_id": scan_id,
        "scan_status": scan["status"],
        "events": [dict(row) for row in rows],
    }


@router.post("/scans/{scan_id}/stop")
def stop_scan(scan_id: int, request: Request):
    """Cancel one catalog scan and close its exact assigned worker process."""
    now = _iso(_now())
    process_id = None
    stop_assigned_worker = False
    worker_id = None
    site_id = None
    site_name = None
    message = ""
    with get_db() as db:
        row = db.execute(
            """SELECT c.*, s.name AS site_name, s.discovery_weekday, s.discovery_time
               FROM flow_catalog_scans c
               JOIN flow_sites s ON s.id=c.site_id
               WHERE c.id=?""",
            (scan_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Scan not found.")
        if row["status"] in RUN_TERMINAL:
            return {
                "scan_id": scan_id,
                "status": row["status"],
                "message": f"Scan already finished with status {row['status']}.",
                "worker": {"status": "not_needed"},
            }
        scan_browser_mode = _loads(row["job_json"], {}).get("execution", {}).get(
            "browser_mode", "headless",
        )
        site_id = row["site_id"]
        site_name = row["site_name"]
        worker_id = row["worker_id"]
        worker = None
        if row["status"] in {"claimed", "running"} and worker_id:
            worker = db.execute(
                """SELECT capabilities_json FROM flow_workers
                   WHERE worker_id=? AND current_scan_id=?""",
                (worker_id, scan_id),
            ).fetchone()
        stop_assigned_worker = worker is not None
        capabilities = _loads(worker["capabilities_json"], {}) if worker else {}
        raw_pid = capabilities.get("process_id")
        process_id = raw_pid if isinstance(raw_pid, int) and raw_pid > 0 else None
        message = (
            "Stop requested by user for the assigned catalog worker."
            if stop_assigned_worker
            else "Cancelled by user before a catalog worker started this scan."
        )
        progress = {"stage": "cancelled", "message": message}
        db.execute(
            """UPDATE flow_catalog_scans
               SET status='cancelled', progress_json=?, error=?, finished_at=?, heartbeat_at=?
               WHERE id=? AND status IN ('queued','claimed','running')""",
            (_json(progress), message, now, now, scan_id),
        )
        next_scan = _iso(_next_weekly_scan(row["discovery_weekday"], row["discovery_time"]))
        db.execute(
            """UPDATE flow_sites SET last_scan_at=?, last_scan_status='cancelled',
               last_scan_error=?, next_scan_at=?, updated_at=? WHERE id=?""",
            (now, message, next_scan, now, site_id),
        )
        if stop_assigned_worker:
            db.execute(
                """UPDATE flow_workers SET status='offline', current_scan_id=NULL,
                   last_error=?, updated_at=?
                   WHERE worker_id=? AND current_scan_id=?""",
                (message, now, worker_id, scan_id),
            )
        log_event(
            db, "flow_site", site_id, site_name, "scan_cancelled",
            f"scan_id={scan_id}", get_actor(request),
        )

    stopped = (
        stop_local_worker(scan_browser_mode, process_id, worker_id=worker_id)
        if stop_assigned_worker
        else {"status": "not_needed", "message": "The scan had not been assigned to a worker."}
    )
    if stop_assigned_worker:
        message = (
            "Scan stopped and its assigned catalog worker was closed."
            if stopped.get("status") == "stopped"
            else "Scan cancelled, but Windows could not confirm that the catalog worker closed: "
                 f"{stopped.get('message') or stopped.get('status')}."
        )
    with get_db() as db:
        progress = {"stage": "cancelled", "message": message}
        db.execute(
            """UPDATE flow_catalog_scans SET progress_json=?, error=?
               WHERE id=? AND status='cancelled'""",
            (_json(progress), message, scan_id),
        )
        db.execute(
            """UPDATE flow_sites SET last_scan_error=?, updated_at=? WHERE id=?""",
            (message, now, site_id),
        )
        db.execute(
            """INSERT INTO flow_scan_events
               (scan_id, status, stage, message, details_json, created_at)
               VALUES (?, 'cancelled', 'cancelled', ?, ?, ?)""",
            (scan_id, message, _json(progress), now),
        )
    return {"scan_id": scan_id, "status": "cancelled", "message": message, "worker": stopped}


@router.post("/sites/{site_id}/scan")
def queue_catalog_scan(site_id: int, request: Request, mode: str = Query(default="full")):
    with get_db() as db:
        site = db.execute("SELECT * FROM flow_sites WHERE id=? AND enabled=1", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "Website not found.")
        if site["adapter"] not in DISCOVERY_ADAPTERS:
            raise HTTPException(400, "This website does not support automatic discovery.")
        if mode not in SCAN_MODES:
            raise HTTPException(400, f"Scan mode must be one of: {', '.join(sorted(SCAN_MODES))}.")
        if site["adapter"] == GSCM_PORTAL_ADAPTER:
            # Reading GSCM's home-screen favorites is already a seconds-long
            # sweep, so it has no cheaper "names only" mode to fall back to.
            mode = "full"
        scan_id, browser_mode = _queue_scan(db, site, "manual", get_actor(request), mode=mode)
        log_event(db, "flow_site", site_id, site["name"], "scan_queued",
                  f"scan_id={scan_id}; mode={mode}; browser={browser_mode}", get_actor(request))
    worker = launch_local_worker(browser_mode)
    return {"id": scan_id, "site_id": site_id, "status": "queued", "mode": mode, "worker": worker}


@router.post("/reports/{report_id}/scan")
def queue_report_scan(report_id: int, request: Request):
    """Refresh one catalog report without staling or deleting other catalog entries."""
    with get_db() as db:
        report = db.execute(
            "SELECT * FROM flow_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not report:
            raise HTTPException(404, "Report not found.")
        category_path = _loads(report["automation_json"], {}).get("category_path", [])
        if not category_path:
            raise HTTPException(400, "Report does not define a discovery path on its website.")
        site = db.execute(
            "SELECT * FROM flow_sites WHERE id=? AND enabled=1", (report["site_id"],)
        ).fetchone()
        if not site or site["adapter"] not in DISCOVERY_ADAPTERS:
            raise HTTPException(400, "This website does not support targeted discovery.")
        scan_id, browser_mode = _queue_scan(db, site, "report", get_actor(request), report)
        log_event(
            db, "flow_report", report_id, report["name"], "scan_queued",
            f"scan_id={scan_id}; browser={browser_mode}", get_actor(request),
        )
    worker = launch_local_worker(browser_mode)
    return {"id": scan_id, "site_id": site["id"], "report_id": report_id,
            "status": "queued", "worker": worker}


def queue_due_catalog_scans() -> dict:
    now = _now()
    queued = []
    with get_db() as db:
        sites = db.execute(
            f"""SELECT * FROM flow_sites WHERE enabled=1 AND discovery_enabled=1
               AND adapter IN ({', '.join('?' * len(DISCOVERY_ADAPTERS))})
               AND (next_scan_at IS NULL OR next_scan_at <= ?) ORDER BY id""",
            (*sorted(DISCOVERY_ADAPTERS), _iso(now)),
        ).fetchall()
        modes: set[str] = set()
        for site in sites:
            scan_id, browser_mode = _queue_scan(db, site, "scheduled", "scheduler")
            queued.append(scan_id)
            modes.add(browser_mode)
            db.execute(
                "UPDATE flow_sites SET next_scan_at=?, updated_at=? WHERE id=?",
                (_iso(_next_weekly_scan(site["discovery_weekday"], site["discovery_time"], now)),
                 _iso(now), site["id"]),
            )
    if queued:
        workers = [launch_local_worker(browser_mode) for browser_mode in sorted(modes)]
        worker = workers[0] if len(workers) == 1 else {"status": "starting", "modes": sorted(modes)}
    else:
        worker = {"status": "not_needed", "mode": "local"}
    return {"queued": queued, "count": len(queued), "worker": worker}


def _reset_gscm_discovery_snapshot(db, site_id: int) -> dict:
    """Remove GSCM rows still stale after the incoming snapshot was upserted.

    A bookmark referenced by an existing Flow cannot be deleted without
    breaking that Flow's foreign key. Those rows remain available as stale,
    disabled tombstones if the new snapshot no longer contains them. Historical
    timing rows are retained but detached from disposable report ids.
    """
    rows = db.execute(
        """SELECT r.id,
                  EXISTS(SELECT 1 FROM flows f WHERE f.report_id=r.id) AS referenced
           FROM flow_reports r
           WHERE r.site_id=? AND r.source_kind='discovered' AND r.stale=1""",
        (site_id,),
    ).fetchall()
    disposable = [row["id"] for row in rows if not row["referenced"]]
    preserved = sum(1 for row in rows if row["referenced"])
    if disposable:
        placeholders = ", ".join("?" for _item in disposable)
        db.execute(
            f"UPDATE flow_operation_timings SET report_id=NULL WHERE report_id IN ({placeholders})",
            disposable,
        )
        db.execute(
            f"DELETE FROM flow_report_filters WHERE report_id IN ({placeholders})",
            disposable,
        )
        db.execute(
            f"DELETE FROM flow_reports WHERE id IN ({placeholders})",
            disposable,
        )
    return {
        "reset_report_count": len(disposable),
        "preserved_referenced_report_count": preserved,
    }


_GSCM_CATALOG_SUFFIX_RE = re.compile(r"^(.*?)(?: \((\d+)\))?$")


def _gscm_bookmark_id(automation: dict[str, Any]) -> str:
    """Exact outer-trimmed GSCM ``userreportid`` execution identity."""
    value = automation.get("favorite_bookmark_id")
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _gscm_catalog_identity(
    automation: dict[str, Any], discovery_key: str | None, name: str,
) -> tuple[str, ...]:
    """Path identity used only to migrate legacy ``Name (2)`` rows.

    A numeric suffix was historically added to make duplicate bookmark labels
    fit the catalog's unique name constraint. It is not a stable identity, so
    callers may use this value only when exactly one candidate matches.
    """
    raw_path = automation.get("category_path")
    if isinstance(raw_path, list):
        path = [str(part).strip() for part in raw_path if str(part).strip()]
    else:
        path = []
    if not path and discovery_key:
        path = [part.strip() for part in str(discovery_key).split(" > ") if part.strip()]
    if not path and str(name).strip():
        path = [str(name).strip()]
    if path:
        leaf = path[-1]
        match = _GSCM_CATALOG_SUFFIX_RE.fullmatch(path[-1])
        favorite_name = str(automation.get("favorite_name") or "").strip()
        base_name = match.group(1).strip() if match else ""
        # Only strip a suffix that catalog naming added. A real bookmark named
        # ``Budget (2)`` records that exact favorite_name; a synthetic copy
        # named ``Budget (2)`` still records favorite_name ``Budget``.
        if (
            match and match.group(2) and base_name and favorite_name
            and favorite_name.casefold() == base_name.casefold()
            and favorite_name.casefold() != leaf.casefold()
        ):
            path[-1] = base_name
    return tuple(part.casefold() for part in path)


def _gscm_tabless_catalog_identity(
    automation: dict[str, Any], discovery_key: str | None, name: str,
) -> tuple[str, ...]:
    """Legacy folder/name identity for rows whose historical scope was wrong."""
    identity = _gscm_catalog_identity(automation, discovery_key, name)
    return identity[1:] if len(identity) > 1 else identity


def _remove_duplicate_gscm_reports(
    db, canonical_id: int, duplicate_ids: list[int], seen_at: str,
) -> None:
    """Rewire references and delete rows proven equal by stable bookmark id."""
    if not duplicate_ids:
        return
    placeholders = ", ".join("?" for _item in duplicate_ids)
    db.execute(
        f"UPDATE flows SET report_id=?, updated_at=? WHERE report_id IN ({placeholders})",
        (canonical_id, seen_at, *duplicate_ids),
    )
    # Timings describe the same stable bookmark, so retain them on the
    # canonical row rather than discarding historical evidence.
    db.execute(
        f"UPDATE flow_operation_timings SET report_id=? WHERE report_id IN ({placeholders})",
        (canonical_id, *duplicate_ids),
    )
    db.execute(
        f"DELETE FROM flow_report_filters WHERE report_id IN ({placeholders})",
        duplicate_ids,
    )
    db.execute(
        f"DELETE FROM flow_reports WHERE id IN ({placeholders})",
        duplicate_ids,
    )


def _gscm_report_candidates(db, site_id: int) -> list[dict[str, Any]]:
    """Immutable pre-scan view used to plan every GSCM catalog upsert."""
    rows = db.execute(
        """SELECT r.id, r.name, r.discovery_key, r.automation_json, r.source_kind,
                  EXISTS(SELECT 1 FROM flows f WHERE f.report_id=r.id) AS referenced
           FROM flow_reports r WHERE r.site_id=? ORDER BY r.id""",
        (site_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "discovery_key": row["discovery_key"],
            "automation": _loads(row["automation_json"], {}),
            "source_kind": row["source_kind"],
            "referenced": bool(row["referenced"]),
        }
        for row in rows
    ]


def _ambiguous_gscm_migration(catalog_name: str) -> RuntimeError:
    return RuntimeError(
        f"GSCM catalog row {catalog_name!r} has more than one compatible legacy "
        "bookmark and cannot be assigned a stable favorite_bookmark_id safely. "
        "Keep the prior catalog and resolve the duplicate bookmark names first."
    )


def _plan_gscm_existing_reports(
    candidates: list[dict[str, Any]],
    reports: list[tuple[DiscoveredReport, str]],
) -> tuple[list[dict[str, Any] | None], dict[int, list[int]]]:
    """Resolve a whole GSCM batch against pre-scan rows without mutating it."""
    plans: list[dict[str, Any] | None] = [None] * len(reports)
    duplicate_groups: dict[int, list[int]] = {}
    claimed_ids: set[int] = set()
    unavailable_ids: set[int] = set()

    incoming_bookmark_ids: set[str] = set()
    for item, _catalog_name in reports:
        bookmark_id = _gscm_bookmark_id(item.automation)
        if not bookmark_id:
            continue
        if bookmark_id in incoming_bookmark_ids:
            raise RuntimeError(
                "GSCM discovery returned the same favorite_bookmark_id more than once; "
                "refusing to create ambiguous catalog rows."
            )
        incoming_bookmark_ids.add(bookmark_id)

    existing_by_bookmark: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        bookmark_id = _gscm_bookmark_id(candidate["automation"])
        if bookmark_id:
            existing_by_bookmark.setdefault(bookmark_id, []).append(candidate)

    # Stable ids are authoritative and are planned first, independent of scan
    # ordering. Duplicate database rows with one id share one canonical target.
    for index, (item, _catalog_name) in enumerate(reports):
        bookmark_id = _gscm_bookmark_id(item.automation)
        if not bookmark_id:
            continue
        matches = existing_by_bookmark.get(bookmark_id, [])
        if not matches:
            continue
        matches = sorted(
            matches, key=lambda candidate: (not candidate["referenced"], candidate["id"]),
        )
        canonical = matches[0]
        plans[index] = canonical
        claimed_ids.add(canonical["id"])
        unavailable_ids.update(candidate["id"] for candidate in matches)
        duplicate_groups[canonical["id"]] = [
            candidate["id"] for candidate in matches[1:]
        ]

    incoming_identity_counts: dict[tuple[str, ...], int] = {}
    for item, catalog_name in reports:
        identity = _gscm_catalog_identity(
            item.automation, item.discovery_key, catalog_name,
        )
        if identity:
            incoming_identity_counts[identity] = incoming_identity_counts.get(identity, 0) + 1

    def compatible_candidates(index: int) -> list[dict[str, Any]]:
        item, _catalog_name = reports[index]
        bookmark_id = _gscm_bookmark_id(item.automation)
        return [
            candidate for candidate in candidates
            if candidate["id"] not in claimed_ids
            and candidate["id"] not in unavailable_ids
            and (not bookmark_id or not _gscm_bookmark_id(candidate["automation"]))
        ]

    def normalized_candidates(
        index: int, compatible: list[dict[str, Any]],
    ) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
        item, catalog_name = reports[index]
        identity = _gscm_catalog_identity(
            item.automation, item.discovery_key, catalog_name,
        )
        normalized = [
            candidate for candidate in compatible
            if identity and _gscm_catalog_identity(
                candidate["automation"], candidate["discovery_key"], candidate["name"],
            ) == identity
        ]
        return identity, normalized

    # Phase 1: every unresolved stable row gets a chance to reserve its unique
    # full-scope legacy match before a different scope can attempt tabless repair.
    stable_unresolved = [
        index for index, plan in enumerate(plans)
        if plan is None and _gscm_bookmark_id(reports[index][0].automation)
    ]
    for index in stable_unresolved:
        item, catalog_name = reports[index]
        compatible = compatible_candidates(index)
        incoming_identity, normalized = normalized_candidates(index, compatible)
        if not normalized:
            continue
        if (
            len(normalized) > 1
            or incoming_identity_counts.get(incoming_identity, 0) > 1
        ):
            raise _ambiguous_gscm_migration(catalog_name)
        plans[index] = normalized[0]
        claimed_ids.add(normalized[0]["id"])

    # Phase 2: only stable rows still unresolved may repair a historical wrong
    # scope tab. Count only reports still competing after full-scope reservations.
    still_unresolved = [index for index, plan in enumerate(plans) if plan is None]
    remaining_tabless_counts: dict[tuple[str, ...], int] = {}
    for index in still_unresolved:
        item, catalog_name = reports[index]
        identity = _gscm_tabless_catalog_identity(
            item.automation, item.discovery_key, catalog_name,
        )
        if identity:
            remaining_tabless_counts[identity] = remaining_tabless_counts.get(identity, 0) + 1
    for index in still_unresolved:
        item, catalog_name = reports[index]
        if not _gscm_bookmark_id(item.automation):
            continue
        compatible = compatible_candidates(index)
        tabless_identity = _gscm_tabless_catalog_identity(
            item.automation, item.discovery_key, catalog_name,
        )
        tabless = [
            candidate for candidate in compatible
            if tabless_identity and _gscm_tabless_catalog_identity(
                candidate["automation"], candidate["discovery_key"], candidate["name"],
            ) == tabless_identity
        ]
        if not tabless:
            continue
        if (
            len(tabless) > 1
            or remaining_tabless_counts.get(tabless_identity, 0) > 1
        ):
            raise _ambiguous_gscm_migration(catalog_name)
        plans[index] = tabless[0]
        claimed_ids.add(tabless[0]["id"])

    # Phase 3: no-id rows may use only full synthetic-aware identity. Stable
    # migrations have already reserved every candidate they can safely own.
    no_id_unresolved = [
        index for index, plan in enumerate(plans)
        if plan is None and not _gscm_bookmark_id(reports[index][0].automation)
    ]
    for index in no_id_unresolved:
        item, catalog_name = reports[index]
        compatible = compatible_candidates(index)
        incoming_identity, normalized = normalized_candidates(index, compatible)
        migration_identity_count = incoming_identity_counts.get(incoming_identity, 0)
        exact = [
            candidate for candidate in compatible
            if (item.discovery_key is not None
                and candidate["discovery_key"] == item.discovery_key)
            or (candidate["discovery_key"] is None and candidate["name"] == catalog_name)
        ]
        if incoming_identity:
            # Matching text is not enough when synthetic-aware path evidence is
            # available. A literal ``Budget (2)`` and a generated second copy
            # share text but have different favorite_name identities.
            exact = [candidate for candidate in exact if candidate in normalized]
        if len(exact) > 1:
            raise _ambiguous_gscm_migration(catalog_name)
        existing = exact[0] if exact else None
        if existing is None and normalized:
            if len(normalized) > 1 or migration_identity_count > 1:
                raise _ambiguous_gscm_migration(catalog_name)
            existing = normalized[0]
        if existing is not None:
            plans[index] = existing
            claimed_ids.add(existing["id"])

    return plans, duplicate_groups


def _gscm_assignment_path(
    automation: dict[str, Any], discovery_key: str | None, name: str,
) -> list[str]:
    """Catalog path retained or allocated for one incomplete-scan row."""
    raw_path = automation.get("category_path")
    if isinstance(raw_path, list):
        path = [str(part).strip() for part in raw_path if str(part).strip()]
        if path:
            return path
    for value in (discovery_key, name):
        if value:
            path = [part.strip() for part in str(value).split(" > ") if part.strip()]
            if path:
                return path
    return []


def _gscm_incomplete_assignments(
    candidates: list[dict[str, Any]],
    reports: list[DiscoveredReport],
    desired_names: list[str],
    plans: list[dict[str, Any] | None],
) -> list[tuple[str, str | None, list[str]]]:
    """Keep stored identities and allocate new rows without subset collisions."""
    planned_ids = {existing["id"] for existing in plans if existing is not None}
    omitted = [candidate for candidate in candidates if candidate["id"] not in planned_ids]
    occupied_names = {candidate["name"] for candidate in omitted}
    occupied_keys = {
        candidate["discovery_key"] for candidate in omitted
        if candidate["discovery_key"] is not None
    }
    assignments: list[tuple[str, str | None, list[str]] | None] = [None] * len(reports)

    def allocate(
        item: DiscoveredReport, path: list[str], assigned_name: str,
        assigned_key: str | None,
    ) -> tuple[str, str | None, list[str]]:
        if assigned_name not in occupied_names and assigned_key not in occupied_keys:
            return assigned_name, assigned_key, path
        leaf = path[-1] if path else item.name.strip()
        match = _GSCM_CATALOG_SUFFIX_RE.fullmatch(leaf)
        favorite_name = str(item.automation.get("favorite_name") or "").strip()
        base_name = match.group(1).strip() if match else leaf
        if not (
            match and match.group(2) and base_name and favorite_name
            and favorite_name.casefold() == base_name.casefold()
            and favorite_name.casefold() != leaf.casefold()
        ):
            base_name = leaf
        parent_path = path[:-1]
        suffix = 2
        while True:
            allocated_path = [*parent_path, f"{base_name} ({suffix})"]
            allocated_name = " > ".join(allocated_path)
            if allocated_name not in occupied_names and allocated_name not in occupied_keys:
                return allocated_name, allocated_name, allocated_path
            suffix += 1

    # Resolve planned rows first. Omitted rows are hard blockers; planned rows
    # may exchange or correct assignments because they are staged before write.
    planned: list[tuple[int, bool, tuple[str, str | None, list[str]]]] = []
    for index, (item, desired_name, existing) in enumerate(
        zip(reports, desired_names, plans)
    ):
        if existing is None:
            continue
        desired_key = item.discovery_key
        desired_path = _gscm_assignment_path(item.automation, desired_key, desired_name)
        blocked = desired_name in occupied_names or desired_key in occupied_keys
        if blocked:
            assignment = (
                existing["name"], existing["discovery_key"],
                _gscm_assignment_path(
                    existing["automation"], existing["discovery_key"], existing["name"],
                ),
            )
        else:
            assignment = (desired_name, desired_key, desired_path)
        retained = (
            assignment[0] == existing["name"]
            and assignment[1] == existing["discovery_key"]
        )
        planned.append((index, retained, assignment))

    # Rows retaining their old slot reserve it before another planned row can
    # request that same slot; swaps among moving rows remain available.
    for index, _retained, assignment in sorted(planned, key=lambda value: not value[1]):
        item = reports[index]
        assigned_name, assigned_key, path = allocate(item, assignment[2], *assignment[:2])
        occupied_names.add(assigned_name)
        if assigned_key is not None:
            occupied_keys.add(assigned_key)
        assignments[index] = (assigned_name, assigned_key, path)

    # New rows come last and can never displace omitted or effective planned rows.
    for index, (item, desired_name, existing) in enumerate(
        zip(reports, desired_names, plans)
    ):
        if existing is not None:
            continue
        desired_key = item.discovery_key
        path = _gscm_assignment_path(item.automation, desired_key, desired_name)
        assigned_name, assigned_key, path = allocate(
            item, path, desired_name, desired_key,
        )
        occupied_names.add(assigned_name)
        if assigned_key is not None:
            occupied_keys.add(assigned_key)
        assignments[index] = (assigned_name, assigned_key, path)

    return [assignment for assignment in assignments if assignment is not None]


def _stage_gscm_incomplete_rows(
    db, site_id: int, candidates: list[dict[str, Any]], planned_ids: set[int],
    desired_names: list[str],
) -> None:
    """Temporarily free only rows participating in an incomplete update."""
    occupied = {candidate["name"] for candidate in candidates} | set(desired_names)
    for candidate in candidates:
        if candidate["id"] not in planned_ids:
            continue
        base = f"__gscm_incomplete_pending__{site_id}__{candidate['id']}__"
        staged_name = base
        suffix = 2
        while staged_name in occupied:
            staged_name = f"{base}{suffix}"
            suffix += 1
        occupied.add(staged_name)
        db.execute(
            "UPDATE flow_reports SET name=?, discovery_key=NULL WHERE id=?",
            (staged_name, candidate["id"]),
        )


def _stage_gscm_snapshot_rows(
    db, site_id: int, candidates: list[dict[str, Any]], desired_names: list[str],
    planned_ids: set[int],
) -> None:
    """Free complete-snapshot names and keys before collision-safe upserts."""
    occupied = {candidate["name"] for candidate in candidates} | set(desired_names)
    for candidate in candidates:
        if candidate["source_kind"] != "discovered" and candidate["id"] not in planned_ids:
            continue
        base = f"__gscm_snapshot_pending__{site_id}__{candidate['id']}__"
        staged_name = base
        suffix = 2
        while staged_name in occupied:
            staged_name = f"{base}{suffix}"
            suffix += 1
        occupied.add(staged_name)
        db.execute(
            "UPDATE flow_reports SET name=?, discovery_key=NULL WHERE id=?",
            (staged_name, candidate["id"]),
        )


def _restore_gscm_missing_tombstones(
    db, site_id: int, candidates: list[dict[str, Any]],
) -> None:
    """Restore missing referenced rows after staged names have served their purpose."""
    originals = {
        candidate["id"]: candidate
        for candidate in candidates if candidate["source_kind"] == "discovered"
    }
    rows = db.execute(
        """SELECT r.id, r.name FROM flow_reports r
           WHERE r.site_id=? AND r.source_kind='discovered' AND r.stale=1
             AND EXISTS(SELECT 1 FROM flows f WHERE f.report_id=r.id)
           ORDER BY r.id""",
        (site_id,),
    ).fetchall()
    occupied = {
        row["name"] for row in db.execute(
            "SELECT name FROM flow_reports WHERE site_id=?", (site_id,),
        ).fetchall()
    }
    for row in rows:
        original = originals.get(row["id"])
        if original is None:
            continue
        occupied.discard(row["name"])
        restored_name = original["name"]
        if restored_name in occupied:
            base = f"{restored_name} [missing bookmark #{row['id']}]"
            restored_name = base
            suffix = 2
            while restored_name in occupied:
                restored_name = f"{base} ({suffix})"
                suffix += 1
        occupied.add(restored_name)
        restored_key = original["discovery_key"]
        if restored_key and db.execute(
            "SELECT 1 FROM flow_reports WHERE site_id=? AND discovery_key=? AND id<>?",
            (site_id, restored_key, row["id"]),
        ).fetchone():
            restored_key = None
        db.execute(
            "UPDATE flow_reports SET name=?, discovery_key=? WHERE id=?",
            (restored_name, restored_key, row["id"]),
        )


def _apply_discovery(
    db, site_id: int, reports: list[DiscoveredReport], seen_at: str, *, complete: bool = True,
) -> dict:
    keys = {item.discovery_key for item in reports}
    reset_result = {}
    site = db.execute("SELECT adapter FROM flow_sites WHERE id=?", (site_id,)).fetchone()
    is_gscm = bool(site and site["adapter"] == GSCM_PORTAL_ADAPTER)
    incoming_path_counts: dict[tuple[str, str], int] = {}
    if site and site["adapter"] == ASAP_PORTAL_ADAPTER:
        for item in reports:
            path = [
                str(part).strip() for part in item.automation.get("category_path", [])
                if str(part).strip()
            ]
            if len(path) >= 2:
                identity = (path[0].casefold(), path[-1].casefold())
                incoming_path_counts[identity] = incoming_path_counts.get(identity, 0) + 1
    is_gscm_snapshot = bool(is_gscm and complete)
    if is_gscm_snapshot and not reports:
        return {
            "report_count": 0, "filter_count": 0, "discovery_keys": [],
            "complete": False, "ignored_empty_snapshot": True,
        }
    gscm_catalog_names = [
        " > ".join(
            str(part).strip()
            for part in (item.automation.get("category_path") or [])
            if str(part).strip()
        ) or item.name.strip()
        for item in reports
    ] if is_gscm else []
    gscm_candidates = _gscm_report_candidates(db, site_id) if is_gscm else []
    if is_gscm:
        gscm_plans, gscm_duplicate_groups = _plan_gscm_existing_reports(
            gscm_candidates, list(zip(reports, gscm_catalog_names)),
        )
        gscm_assignments = (
            list(zip(
                gscm_catalog_names,
                [item.discovery_key for item in reports],
                [
                    _gscm_assignment_path(
                        item.automation, item.discovery_key, gscm_catalog_names[index],
                    )
                    for index, item in enumerate(reports)
                ],
            ))
            if complete else _gscm_incomplete_assignments(
                gscm_candidates, reports, gscm_catalog_names, gscm_plans,
            )
        )
        if not complete:
            keys = {
                discovery_key for _name, discovery_key, _path in gscm_assignments
                if discovery_key is not None
            }
    else:
        gscm_plans, gscm_duplicate_groups, gscm_assignments = [], {}, []
    if complete:
        db.execute(
            "UPDATE flow_reports SET stale=1, enabled=0, updated_at=? WHERE site_id=? AND source_kind='discovered'",
            (seen_at, site_id),
        )
    if is_gscm:
        for canonical_id, duplicate_ids in gscm_duplicate_groups.items():
            _remove_duplicate_gscm_reports(db, canonical_id, duplicate_ids, seen_at)
        if complete:
            _stage_gscm_snapshot_rows(
                db, site_id, gscm_candidates, gscm_catalog_names,
                {plan["id"] for plan in gscm_plans if plan is not None},
            )
        else:
            _stage_gscm_incomplete_rows(
                db, site_id, gscm_candidates,
                {plan["id"] for plan in gscm_plans if plan is not None},
                [name for name, _key, _path in gscm_assignments],
            )
    report_ids = []
    filter_count = 0
    for index, item in enumerate(reports):
        automation = dict(item.automation)
        discovery_key = item.discovery_key
        if is_gscm:
            catalog_name, discovery_key, assigned_path = gscm_assignments[index]
            if not complete:
                automation["category_path"] = assigned_path
            existing = gscm_plans[index]
        else:
            catalog_name = " > ".join(
                str(part).strip()
                for part in (automation.get("category_path") or [])
                if str(part).strip()
            ) or item.name.strip()
            existing = db.execute(
                """SELECT id FROM flow_reports WHERE site_id=?
                   AND (discovery_key=? OR (discovery_key IS NULL AND name=?)) ORDER BY id LIMIT 1""",
                (site_id, item.discovery_key, catalog_name),
            ).fetchone()
        category_path = automation.get("category_path") or []
        relocated = None
        if site and site["adapter"] == ASAP_PORTAL_ADAPTER and len(category_path) >= 2:
            identity = (
                str(category_path[0]).strip().casefold(),
                str(category_path[-1]).strip().casefold(),
            )
            if incoming_path_counts.get(identity) == 1:
                candidates = []
                for candidate in db.execute(
                    """SELECT r.id, r.discovery_key, r.automation_json
                       FROM flow_reports r
                       WHERE r.site_id=? AND r.source_kind='discovered'
                         AND r.discovery_key<>?
                         AND EXISTS(SELECT 1 FROM flows f WHERE f.report_id=r.id)
                       ORDER BY r.id""",
                    (site_id, item.discovery_key),
                ).fetchall():
                    candidate_path = _loads(candidate["automation_json"], {}).get("category_path", [])
                    candidate_path = [
                        str(part).strip() for part in candidate_path if str(part).strip()
                    ]
                    if not candidate_path and candidate["discovery_key"]:
                        candidate_path = [
                            part.strip() for part in candidate["discovery_key"].split(" > ")
                            if part.strip()
                        ]
                    if len(candidate_path) >= 2 and (
                        candidate_path[0].casefold(), candidate_path[-1].casefold()
                    ) == identity:
                        candidates.append(candidate)
                if len(candidates) == 1:
                    relocated = candidates[0]
        if relocated:
            if existing and existing["id"] != relocated["id"]:
                # A corrected menu path may already have produced a duplicate
                # catalog row. Move saved flows to the current row instead of
                # leaving them attached to the stale, non-navigable path.
                db.execute(
                    "UPDATE flows SET report_id=?, updated_at=? WHERE report_id=?",
                    (existing["id"], seen_at, relocated["id"]),
                )
            elif not existing:
                # Preserve the referenced report id when the corrected path is
                # new. Uniqueness on root + leaf prevents merging same-named
                # reports that legitimately live in different menu columns.
                existing = relocated
        if (
            not existing and category_path
            and not (site and site["adapter"] == GSCM_PORTAL_ADAPTER)
        ):
            for candidate in db.execute(
                """SELECT id, automation_json FROM flow_reports
                   WHERE site_id=? AND source_kind='manual' ORDER BY id""",
                (site_id,),
            ).fetchall():
                candidate_path = _loads(candidate["automation_json"], {}).get("category_path", [])
                if candidate_path == category_path:
                    existing = candidate
                    break
        if existing:
            report_id = existing["id"]
            ready_text = item.ready_text
            download_text = item.download_text
            stored = None
            if automation.get("scan_mode") == "partial" or (
                site and site["adapter"] == GSCM_PORTAL_ADAPTER
                and not _gscm_bookmark_id(automation)
            ):
                stored = db.execute(
                    "SELECT ready_text, download_text, automation_json FROM flow_reports WHERE id=?",
                    (report_id,),
                ).fetchone()
            if automation.get("scan_mode") == "partial":
                # Merge over what a full scan already knows: export views,
                # report tab, and download text can only come from opening
                # the report, and flows depend on them.
                automation = {**_loads(stored["automation_json"], {}), **automation}
                ready_text = item.ready_text or stored["ready_text"]
                download_text = stored["download_text"] or item.download_text
            elif stored:
                stable_id = _loads(stored["automation_json"], {}).get(
                    "favorite_bookmark_id"
                )
                if stable_id is not None:
                    automation["favorite_bookmark_id"] = stable_id
            db.execute(
                """UPDATE flow_reports SET name=?, report_url=?, ready_text=?, download_text=?,
                   automation_json=?, discovery_key=?, source_kind='discovered', last_seen_at=?,
                   stale=0, enabled=1, updated_at=?
                   WHERE id=?""",
                (catalog_name, item.report_url, ready_text, download_text,
                 _json(automation), discovery_key, seen_at, seen_at, report_id),
            )
        else:
            cursor = db.execute(
                """INSERT INTO flow_reports
                   (site_id, name, report_url, ready_text, download_text, automation_json,
                    discovery_key, source_kind, last_seen_at, stale, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered', ?, 0, 1, ?, ?)""",
                (site_id, catalog_name, item.report_url, item.ready_text, item.download_text,
                 _json(automation), discovery_key, seen_at, seen_at, seen_at),
            )
            report_id = cursor.lastrowid
        report_ids.append(report_id)
        # A partial scan only inventories names and menu paths, so it knows
        # nothing about filters. Leave existing definitions untouched instead
        # of retiring every filter a full scan discovered.
        if item.automation.get("scan_mode") == "partial":
            continue
        if site and site["adapter"] == ASAP_PORTAL_ADAPTER:
            _resolve_inherited_asap_export_settings(
                db, report_id, automation, seen_at,
            )
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
    if is_gscm_snapshot:
        # Stable-id matching must see the prior rows before cleanup. Anything
        # rediscovered is active again by this point; only genuinely missing
        # rows remain stale. Referenced rows become tombstones, while disposable
        # rows and their filters are removed and their timings detached.
        reset_result = _reset_gscm_discovery_snapshot(db, site_id)
        _restore_gscm_missing_tombstones(db, site_id, gscm_candidates)
    return {
        "report_count": len(report_ids), "filter_count": filter_count,
        "discovery_keys": sorted(keys), "complete": complete, **reset_result,
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
        db.execute('BEGIN IMMEDIATE')
        registration_root = flow_paths.get_flows_root(db)
        recovery_roots = {str(Path(registration_root) / ".metronome" / "artifacts")}
        for queued in db.execute("SELECT job_json FROM flow_runs WHERE status='queued'"):
            queued_job = _loads(queued[0], {})
            root = (queued_job.get("paths") or {}).get("artifact_store_root")
            if root:
                recovery_roots.add(root)
            recovery_roots.update((queued_job.get("resume") or {}).get("artifact_store_roots") or [])
        existing_worker = db.execute(
            "SELECT * FROM flow_workers WHERE worker_id=?", (body.worker_id,)
        ).fetchone()
        previous_capabilities = _loads(
            existing_worker["capabilities_json"], {}
        ) if existing_worker else {}
        previous_pid = previous_capabilities.get("process_id")
        replacement_pid = body.capabilities.get("process_id")
        if existing_worker and existing_worker['stop_requested_pid'] is not None and previous_pid == replacement_pid:
            return {'worker_id': body.worker_id, 'status': 'stopping', 'flows_root': registration_root, 'artifact_store_roots': sorted(recovery_roots)}
        recovered_parallel = flow_parallel.recover_worker(db, existing_worker, replacement_pid) if existing_worker else False
        process_restarted = (
            existing_worker is not None
            and existing_worker["current_run_id"] is not None
            and previous_pid is not None
            and replacement_pid is not None
            and str(previous_pid) != str(replacement_pid)
            and not recovered_parallel
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
                _sync_flow_failure_actions(db, now)
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
        headed_work_pending = bool(body.capabilities.get('headed') and flow_capacity.pending_work(db, 'headed'))
    if interrupted_run_id is not None:
        notify_flow_owner_of_failure(interrupted_run_id)
    return {
        "worker_id": body.worker_id,
        "status": "idle",
        "interrupted_run_id": interrupted_run_id,
        "flows_root": registration_root,
        "artifact_store_roots": sorted(recovery_roots),
        "headed_work_pending": headed_work_pending,
    }


def _worker_artifact_stores(capabilities: dict) -> set[str]:
    extra = capabilities.get("artifact_store_ids")
    values = extra if isinstance(extra, list) else []
    return {value for value in [capabilities.get("artifact_store_id"), *values] if isinstance(value, str) and value}


@router.post("/worker/{worker_id}/claim")
def claim_run(worker_id: str):
    now = _iso(_now())
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        flow_parallel.reap(db)
        worker = db.execute("SELECT * FROM flow_workers WHERE worker_id=?", (worker_id,)).fetchone()
        if not worker:
            raise HTTPException(404, "Register this worker before claiming work.")
        if worker['stop_requested_pid'] is not None:
            return {'run': None, 'scan': None, 'stopping': True}
        if worker['current_task_id']:
            task = flow_parallel.claim_task(db, worker_id)
            return {'run': None, 'scan': None, 'task': task}
        capabilities = _loads(worker["capabilities_json"], {})
        artifact_stores = _worker_artifact_stores(capabilities)
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
        store_failure = False
        for candidate in queued_runs:
            candidate_job = _loads(candidate["job_json"], {})
            execution = candidate_job.get("execution", {})
            required_store = execution.get("required_artifact_store_id")
            if (
                required_store
                and execution.get("worker_id") == worker_id
                and required_store not in artifact_stores
            ):
                message = (
                    "SQL Retry cannot use its private artifacts because this worker's "
                    "profile store identity changed. Run the Flow again to download fresh files."
                )
                db.execute(
                    """UPDATE flow_runs SET status='failed', error=?, progress_json=?,
                       finished_at=?, heartbeat_at=? WHERE id=? AND status='queued'""",
                    (
                        message,
                        _json({"stage": "artifact_store_unavailable", "message": message}),
                        now, now, candidate["id"],
                    ),
                )
                db.execute(
                    """INSERT INTO flow_run_events
                       (run_id, status, stage, message, details_json, error, created_at)
                       VALUES (?, 'failed', 'artifact_store_unavailable', ?, ?, ?, ?)""",
                    (
                        candidate["id"], message,
                        _json({"required_artifact_store_id": required_store}), message, now,
                    ),
                )
                db.execute(
                    """UPDATE flows SET last_run_at=?, last_status='failed', last_error=?, updated_at=?
                       WHERE id=?""",
                    (now, message, now, candidate["flow_id"]),
                )
                store_failure = True
        if store_failure:
            _sync_flow_failure_actions(db, now)
            queued_runs = db.execute(
                "SELECT * FROM flow_runs WHERE status='queued' ORDER BY created_at, id"
            ).fetchall()

        # Finish active bundles before taking another parent run.
        task = flow_parallel.claim_task(db, worker_id)
        if task:
            return {'run': None, 'scan': None, 'task': task}

        # Recovery diagnostics must still reject an impossible producer-store
        # assignment when the pool is busy. Capacity gates new work only.
        if not flow_capacity.can_claim(db, worker_id, worker_mode):
            db.execute("UPDATE flow_workers SET status='idle', current_run_id=NULL, current_scan_id=NULL, last_seen_at=?, updated_at=? WHERE worker_id=?", (now, now, worker_id))
            return {"run": None, "scan": None, "waiting_for_capacity": True}

        def worker_can_claim(candidate) -> bool:
            job = _loads(candidate["job_json"], {})
            execution = job.get("execution", {})
            required_store = execution.get("required_artifact_store_id")
            required_adapter = execution.get("required_adapter")
            adapters = set(capabilities.get("adapters") or [])
            return (
                execution.get("browser_mode", "headless") == worker_mode
                and (not flow_tasks.enabled(job) or flow_tasks.supported(job, capabilities))
                and flow_parallel.portal_available(db, job)
                and (not required_adapter or required_adapter in adapters)
                and (not (job.get("paths") or {}).get("artifact_store_root") or capabilities.get("shared_flow_artifacts"))
                and set(execution.get("required_artifact_store_ids") or []).issubset(artifact_stores)
                and (
                    not required_store
                    or required_store in artifact_stores
                )
            )

        row = next((candidate for candidate in queued_runs if worker_can_claim(candidate)), None)
        if not row:
            # A scan runs on the worker whose mode its job names, exactly like
            # a run: a GSCM scan must walk the portal in the same browser,
            # profile, and session as the site's working flow runs. Scans
            # without a stored mode predate this routing and stay headless.
            queued_scans = db.execute(
                "SELECT * FROM flow_catalog_scans WHERE status='queued' ORDER BY created_at, id"
            ).fetchall()
            scan = next((candidate for candidate in queued_scans if (
                _loads(candidate["job_json"], {}).get("execution", {}).get("browser_mode", "headless")
                == worker_mode
                and flow_parallel.portal_available(db, _loads(candidate['job_json'], {}))
            )), None)
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
        if row["status"] in RUN_TERMINAL:
            return {"scan_id": scan_id, "status": row["status"], "ignored": True}
        site_adapter = db.execute(
            "SELECT adapter FROM flow_sites WHERE id=?", (row["site_id"],),
        ).fetchone()
        started = row["started_at"] or (now if body.status == "running" else None)
        finished = now if body.status in RUN_TERMINAL else None
        db.execute(
            """INSERT INTO flow_scan_events (scan_id, status, stage, message, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                scan_id, body.status, body.progress.get("stage"),
                body.progress.get("message"), _json(body.progress), now,
            ),
        )
        if (
            body.status == "succeeded"
            and site_adapter
            and site_adapter["adapter"] == GSCM_PORTAL_ADAPTER
            and body.skipped_reports
        ):
            # A rejected bookmark means the payload is not an authoritative
            # snapshot. Keep the last good GSCM catalog intact rather than
            # replacing it with a silently incomplete list.
            result = {
                "report_count": 0, "filter_count": 0, "discovery_keys": [],
                "complete": False, "ignored_incomplete_snapshot": True,
            }
        else:
            result = _apply_discovery(
                db, row["site_id"], body.reports, now, complete=body.complete,
            ) if body.status == "succeeded" else {}
        if body.skipped_reports:
            result = {**result, "skipped_reports": body.skipped_reports}
            db.execute(
                """INSERT INTO flow_scan_events (scan_id, status, stage, message, details_json, created_at)
                   VALUES (?, ?, 'reports_skipped', ?, ?, ?)""",
                (
                    scan_id, body.status,
                    f"{len(body.skipped_reports)} report(s) were rejected by validation and skipped: "
                    + "; ".join(
                        f"{item.get('report')} ({item.get('error')})"
                        for item in body.skipped_reports[:5]
                    ),
                    _json({"skipped_reports": body.skipped_reports}), now,
                ),
            )
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


RETENTION_OP_PENDING = ("issued", "quarantined")


def _folder_key(run_folder: str) -> str:
    """Host-independent normalized parent identity for Windows/UNC or POSIX paths."""
    raw = str(run_folder)
    if "\\" in raw or re.match(r"^[A-Za-z]:[/\\]", raw):
        return normalize_target_path(ntpath.dirname(raw))
    return os.path.normcase(os.path.normpath(str(Path(raw).parent)))


def _retention_sibling(run_folder: str, name: str) -> str:
    """Build a sibling without interpreting a worker's path on the server host."""
    raw = str(run_folder)
    if "\\" in raw or re.match(r"^[A-Za-z]:[/\\]", raw):
        return ntpath.join(ntpath.dirname(raw), name)
    return str(Path(raw).parent / name)


def _source_has_live_consumer(db, source_run_id: int) -> bool:
    return db.execute(
        """SELECT 1 FROM flow_run_source_refs sr
           JOIN flow_runs c ON c.id = sr.consumer_run_id
           WHERE sr.source_run_id=? AND c.status NOT IN ('succeeded','failed','cancelled')
           LIMIT 1""",
        (source_run_id,),
    ).fetchone() is not None


def _source_folder_unavailable(db, source_run_id: int) -> bool:
    """True when the source run's folder is pruned or scheduled for removal."""
    row = db.execute("SELECT folder_state FROM flow_runs WHERE id=?", (source_run_id,)).fetchone()
    if row and row["folder_state"] == "pruned":
        return True
    return db.execute(
        "SELECT 1 FROM flow_retention_ops WHERE source_run_id=? AND state IN ('issued','quarantined') LIMIT 1",
        (source_run_id,),
    ).fetchone() is not None


def _release_retention_ops(db, assigned_run_id: int, now: str):
    """Free unreported operations so a later run can pick them up."""
    db.execute(
        """UPDATE flow_retention_ops SET assigned_run_id=NULL, updated_at=?
           WHERE assigned_run_id=? AND state IN ('issued','quarantined')""",
        (now, assigned_run_id),
    )


def _assign_retention_ops(db, run_id: int, folder_key: str, now: str) -> list[dict]:
    """Pick the run folders this run should clean up, transactionally.

    Runs inside the registration transaction, with the registering run's own
    folder already counted: the newest RUN_FOLDER_KEEP recorded folders under
    this target keep their place, non-terminal runs and pinned sources are
    never candidates, and every deletion is pre-recorded as an operation with
    its tombstone path before the worker hears about it.
    """
    # Re-offer operations whose assigned run died before reporting.
    db.execute(
        """UPDATE flow_retention_ops SET assigned_run_id=NULL, updated_at=?
           WHERE state IN ('issued','quarantined') AND assigned_run_id IS NOT NULL
             AND assigned_run_id IN (
                 SELECT id FROM flow_runs WHERE status IN ('succeeded','failed','cancelled'))""",
        (now,),
    )
    recorded = db.execute(
        """SELECT id, status, run_folder FROM flow_runs
           WHERE folder_key=? AND folder_state='present' ORDER BY id DESC""",
        (folder_key,),
    ).fetchall()
    ops: list[dict] = []
    for row in recorded[RUN_FOLDER_KEEP:]:
        if row["status"] not in RUN_TERMINAL:
            continue
        if _source_has_live_consumer(db, row["id"]):
            continue
        pending = db.execute(
            """SELECT id, original_path, tombstone_path, state FROM flow_retention_ops
               WHERE source_run_id=? AND state IN ('issued','quarantined','abandoned')
               ORDER BY id DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if pending:
            continue  # covered by an existing operation, or deliberately abandoned
        cursor = db.execute(
            """INSERT INTO flow_retention_ops
               (source_run_id, original_path, tombstone_path, state, assigned_run_id, created_at, updated_at)
               VALUES (?, ?, '', 'issued', ?, ?, ?)""",
            (row["id"], row["run_folder"], run_id, now, now),
        )
        op_id = cursor.lastrowid
        original_name = ntpath.basename(str(row["run_folder"]).replace("/", "\\"))
        tombstone = _retention_sibling(
            row["run_folder"], retention_tombstone_name(original_name, op_id),
        )
        db.execute(
            "UPDATE flow_retention_ops SET tombstone_path=? WHERE id=?", (tombstone, op_id),
        )
    # Hand this run everything currently unassigned for its target: its own
    # fresh operations plus retries released by earlier runs' failures.
    unassigned = db.execute(
        """SELECT o.id, o.source_run_id, o.original_path, o.tombstone_path, o.state
           FROM flow_retention_ops o JOIN flow_runs src ON src.id = o.source_run_id
           WHERE src.folder_key=? AND o.state IN ('issued','quarantined')
             AND (o.assigned_run_id IS NULL OR o.assigned_run_id=?)""",
        (folder_key, run_id),
    ).fetchall()
    for op in unassigned:
        if _source_has_live_consumer(db, op["source_run_id"]):
            continue
        db.execute(
            "UPDATE flow_retention_ops SET assigned_run_id=?, updated_at=? WHERE id=?",
            (run_id, now, op["id"]),
        )
        ops.append({
            "op_id": op["id"], "source_run_id": op["source_run_id"],
            "original_path": op["original_path"], "tombstone_path": op["tombstone_path"],
            "state": op["state"],
        })
    return ops


def _apply_retention_results(db, run_id: int, results: list[dict], now: str):
    for item in results:
        op = db.execute(
            "SELECT * FROM flow_retention_ops WHERE id=? AND assigned_run_id=?",
            (item.get("op_id"), run_id),
        ).fetchone()
        if not op:
            continue
        outcome = item.get("outcome")
        detail = str(item.get("detail") or "")[:2000] or None
        if outcome == "deleted":
            db.execute(
                "UPDATE flow_retention_ops SET state='done', error=?, updated_at=? WHERE id=?",
                (detail, now, op["id"]),
            )
            db.execute(
                "UPDATE flow_runs SET folder_state='pruned', pruned_at=? WHERE id=?",
                (now, op["source_run_id"]),
            )
        elif outcome == "quarantined":
            db.execute(
                "UPDATE flow_retention_ops SET state='quarantined', error=?, updated_at=? WHERE id=?",
                (detail, now, op["id"]),
            )
        elif outcome == "skipped":
            # The worker's safety gate refused the path; retrying cannot help,
            # and the folder must stay untouched. Keep the reason on record.
            db.execute(
                "UPDATE flow_retention_ops SET state='abandoned', assigned_run_id=NULL, error=?, updated_at=? WHERE id=?",
                (detail, now, op["id"]),
            )
        else:  # failed, or anything unrecognized: release for a later retry
            db.execute(
                "UPDATE flow_retention_ops SET assigned_run_id=NULL, error=?, updated_at=? WHERE id=?",
                (detail, now, op["id"]),
            )


def _record_publish_name_drift(db, row, artifacts: list[dict], now: str) -> None:
    """Warn when one logical direct-output task changes its public basename."""
    previous_rows = db.execute(
        """SELECT artifact_json FROM flow_runs
           WHERE flow_id=? AND id<? AND artifact_json IS NOT NULL
             AND artifact_json LIKE '%"published_filename":%'
           ORDER BY id DESC LIMIT 1""",
        (row["flow_id"], row["id"]),
    ).fetchall()
    previous = {}
    for previous_row in previous_rows:
        for item in _loads(previous_row["artifact_json"], []):
            name = item.get("published_filename")
            if not name:
                continue
            key = _json({
                "export_view": item.get("export_view"),
                "period_key": item.get("period_key"),
            })
            previous.setdefault(key, str(name))
    for item in artifacts:
        current_name = item.get("published_filename")
        if not current_name:
            continue
        key = _json({
            "export_view": item.get("export_view"),
            "period_key": item.get("period_key"),
        })
        prior_name = previous.get(key)
        if not prior_name or os.path.normcase(prior_name) == os.path.normcase(str(current_name)):
            continue
        message = (
            f"The published filename changed from {prior_name} to {current_name}. "
            "The older stable file was intentionally left in place."
        )
        db.execute(
            """INSERT INTO flow_run_events
               (run_id, status, stage, message, details_json, created_at)
               VALUES (?, 'running', 'publish_name_changed', ?, ?, ?)""",
            (
                row["id"], message,
                _json({"previous_filename": prior_name, "published_filename": current_name}),
                now,
            ),
        )


@router.post("/worker/{worker_id}/runs/{run_id}/register_folder")
def register_run_folder(worker_id: str, run_id: int, body: FolderRegister):
    """Record the folder a run created, and assign its retention work.

    Registration and assignment share one transaction so the just-created
    folder is always counted in the keep window - the newest RUN_FOLDER_KEEP
    folders per target survive, and the worker receives only pre-recorded
    operations against paths this server stored itself.
    """
    now = _iso(_now())
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM flow_runs WHERE id=? AND worker_id=?", (run_id, worker_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run is not assigned to this worker.")
        if row["status"] in RUN_TERMINAL:
            return {"run_id": run_id, "ops": [], "ignored": True}
        run_folder = str(Path(body.run_folder))
        if row["run_folder"] and row["run_folder"] != run_folder:
            raise HTTPException(
                409,
                f"Run #{run_id} already registered a different folder: {row['run_folder']}",
            )
        db.execute(
            """UPDATE flow_runs SET run_folder=?, folder_key=?, folder_state='present',
               heartbeat_at=? WHERE id=?""",
            (run_folder, _folder_key(run_folder), now, run_id),
        )
        ops = _assign_retention_ops(db, run_id, _folder_key(run_folder), now)
    return {"run_id": run_id, "ops": ops}


def _outlook_receipt_is_current(db, flow_id: int, receipt: dict) -> bool:
    """Prevent an SQL retry of an older run from rolling dedup state backward."""
    current = db.execute(
        "SELECT outlook_last_identity, outlook_last_received_at FROM flows WHERE id=?",
        (flow_id,),
    ).fetchone()
    if not current or not current["outlook_last_identity"]:
        return True
    if current["outlook_last_identity"] == receipt.get("identity"):
        return True
    new_value = receipt.get("received_at")
    old_value = current["outlook_last_received_at"]
    if not new_value or not old_value:
        return False
    try:
        new_stamp = datetime.fromisoformat(str(new_value).replace("Z", "+00:00"))
        old_stamp = datetime.fromisoformat(str(old_value).replace("Z", "+00:00"))
        return new_stamp >= old_stamp
    except (TypeError, ValueError):
        return False


def _local_file_receipt_is_current(
    db, flow_id: int, receipt: dict, frozen_source: dict | None = None,
) -> bool:
    """Allow the newest file result/retry without permitting receipt rollback."""
    frozen = frozen_source or {}
    expected_revision = int(frozen.get("config_revision") or receipt.get("config_revision") or 0)
    expected_path = frozen.get("normalized_path") or receipt.get("normalized_path")
    expected_worksheet = (
        frozen.get("worksheet") if "worksheet" in frozen else receipt.get("worksheet")
    )
    expected_previous = (
        frozen.get("previous_identity")
        if "previous_identity" in frozen else receipt.get("previous_identity")
    )
    if (
        int(receipt.get("config_revision") or 0) != expected_revision
        or receipt.get("normalized_path") != expected_path
        or receipt.get("worksheet") != expected_worksheet
        or receipt.get("previous_identity") != expected_previous
    ):
        return False
    current = db.execute(
        """SELECT local_file_path, local_file_worksheet, local_file_last_identity,
                  local_file_config_revision
           FROM flows WHERE id=? AND source_type='file'""",
        (flow_id,),
    ).fetchone()
    if not current:
        return False
    if int(current["local_file_config_revision"] or 1) != expected_revision:
        return False
    if normalize_target_path(current["local_file_path"] or "") != expected_path:
        return False
    if current["local_file_worksheet"] != expected_worksheet:
        return False
    current_identity = current["local_file_last_identity"]
    return current_identity in {receipt.get("identity"), expected_previous}


@router.post("/worker/{worker_id}/runs/{run_id}/progress")
def update_run(worker_id: str, run_id: int, body: WorkerProgress):
    if body.status not in RUN_STATUSES:
        raise HTTPException(400, "Unsupported run status.")
    now = _iso(_now())
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM flow_runs WHERE id=? AND worker_id=?", (run_id, worker_id)).fetchone()
        if not row:
            raise HTTPException(404, "Run is not assigned to this worker.")
        if row["status"] in RUN_TERMINAL:
            return {"run_id": run_id, "status": row["status"], "ignored": True}
        flow_parallel.guard_progress(db, worker_id, run_id, body.status, body.finalizer_token, body.progress.get('stage'))
        started = row["started_at"] or (now if body.status == "running" else None)
        finished = now if body.status in RUN_TERMINAL else None
        # Artifacts only ever accumulate within a run. A report without any -
        # typically the final failed post after an exception unwound the
        # worker's download loop - must not erase files earlier progress
        # already recorded: Resume depends on that record.
        stored_artifacts = body.artifacts or _loads(row["artifact_json"], [])
        fanout = flow_parallel._fanout(db, run_id)
        if fanout and not fanout['finalizer_token']:
            # A coordinator snapshot can lag a helper's terminal report. The
            # task ledger is authoritative until bundle finalization begins.
            stored_artifacts = flow_parallel.snapshot(db, run_id)['artifacts']
        no_op = bool(body.progress.get("no_op"))
        db.execute(
            """UPDATE flow_runs SET status=?, progress_json=?, artifact_json=?, error=?,
               started_at=COALESCE(started_at, ?), finished_at=?, heartbeat_at=? WHERE id=?""",
            (body.status, _json(body.progress), _json(stored_artifacts), body.error,
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
        if body.progress.get("stage") == "publish_complete":
            _record_publish_name_drift(db, row, stored_artifacts, now)
        execution_success_at = (
            iso_utc(utc_now())
            if body.status == "succeeded" and row["trigger_type"] != "sql_retry"
            else None
        )
        db.execute(
            """UPDATE flows
               SET last_run_at=?,
                   last_success_at=CASE WHEN ?='succeeded' AND ?=0 THEN ? ELSE last_success_at END,
                   last_execution_success_at=CASE WHEN ? IS NOT NULL THEN ? ELSE last_execution_success_at END,
                   last_status=?, last_error=?, updated_at=?
               WHERE id=?""",
            (
                finished or started or now,
                body.status,
                int(no_op),
                finished,
                execution_success_at,
                execution_success_at,
                body.status,
                body.error,
                now,
                row["flow_id"],
            ),
        )
        job = _loads(row["job_json"], {})
        _store_timings(
            db, body.timings, operation_type="flow_download", run_id=run_id,
            site_id=job.get("site", {}).get("id"), report_id=job.get("report", {}).get("id"),
        )
        if body.retention:
            _apply_retention_results(db, run_id, body.retention, now)
        if body.status in RUN_TERMINAL:
            _release_retention_ops(db, run_id, now)
            downloads = job.get("downloads", {})
            if (
                body.status == "succeeded" and not no_op
                and downloads.get("period_strategy") == "rolling"
            ):
                db.execute(
                    """UPDATE flows SET start_week=?, updated_at=?
                       WHERE id=? AND start_week=?""",
                    (downloads.get("next_start_week"), now, row["flow_id"], downloads.get("period_start_week")),
                )
            if (
                body.status == "succeeded" and not no_op
                and body.source_receipt is not None
                and (job.get("flow", {}).get("source_type") or "portal") == "outlook"
            ):
                receipt = body.source_receipt.model_dump()
                if _outlook_receipt_is_current(db, row["flow_id"], receipt):
                    db.execute(
                        """UPDATE flows
                           SET outlook_last_identity=?, outlook_last_received_at=?,
                               outlook_last_attachment_name=?, outlook_last_subject=?, updated_at=?
                           WHERE id=?""",
                        (
                            receipt["identity"], receipt.get("received_at"),
                            receipt["attachment_name"], receipt.get("subject"),
                            now, row["flow_id"],
                        ),
                    )
            if (
                body.status == "succeeded" and not no_op
                and isinstance(body.source_receipt, LocalFileReceipt)
                and (job.get("flow", {}).get("source_type") or "portal") == "file"
            ):
                receipt = body.source_receipt.model_dump()
                frozen_source = job.get("local_file") or {}
                if _local_file_receipt_is_current(
                    db, row["flow_id"], receipt, frozen_source,
                ):
                    db.execute(
                        """UPDATE flows SET local_file_last_identity=?, updated_at=?
                           WHERE id=? AND source_type='file'
                             AND local_file_config_revision=?
                             AND (local_file_last_identity IS ? OR local_file_last_identity=?)""",
                        (
                            receipt["identity"], now, row["flow_id"],
                            receipt["config_revision"],
                            frozen_source.get("previous_identity"), receipt["identity"],
                        ),
                    )
            db.execute(
                """UPDATE flow_workers SET status='idle', current_run_id=NULL, last_error=?,
                   last_seen_at=?, updated_at=? WHERE worker_id=?""",
                (body.error, now, now, worker_id),
            )
            db.executemany(
                """INSERT INTO flow_run_files
                   (run_id, period_key, file_path, filename, storage_scope, artifact_store_id,
                    file_size, checksum, row_count, published_file_path, published_filename,
                    publish_status, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (run_id, _period_key_text(item.get("period_key")), str(item.get("file_path") or ""),
                     str(item.get("filename") or ""), item.get("storage_scope"),
                     item.get("artifact_store_id"), item.get("file_size"), item.get("checksum"),
                     item.get("row_count"), item.get("published_file_path"),
                     item.get("published_filename"), item.get("publish_status"),
                     str(item.get("status") or "saved"), now)
                    for item in stored_artifacts if item.get("file_path") and item.get("filename")
                ],
            )
            if body.status == "succeeded" and not no_op:
                published_paths = sorted({
                    str(item.get("published_file_path"))
                    for item in stored_artifacts if item.get("published_file_path")
                })
                if len(published_paths) == 1:
                    from app.freshness_inheritance import reconcile_file_binding
                    reconcile_file_binding(
                        db, int(row["flow_id"]), published_path=published_paths[0],
                    )
            _sync_flow_failure_actions(db, now)
        else:
            db.execute(
                "UPDATE flow_workers SET status='busy', last_seen_at=?, updated_at=? WHERE worker_id=?",
                (now, now, worker_id),
            )
    owner_alert = notify_flow_owner_of_failure(run_id) if body.status == "failed" else None
    return {"run_id": run_id, "status": body.status, "owner_alert": owner_alert}


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
        flow = _flow_out(db, flow_id)
        return {**flow, "layout": flow_layout.layout_status(flow.get("flow_folder"), flow_id)}
