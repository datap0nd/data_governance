"""AI Logs - an AI-friendly diagnostic dump of everything since app start.

The report is designed to be pasted into an external AI assistant that can
browse the target portal: it carries enough context (architecture, run
timelines, stage-by-stage events, errors, tracebacks, scan output, process
logs) for that assistant to diagnose WHY an automation failed and write
instructions back. Secrets never enter the report: it is built from stored
run metadata and log records, never from credentials or session tokens.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.ai_logs import APP_STARTED_AT, process_log_lines
from app.database import get_db

router = APIRouter(prefix="/api/ai-logs", tags=["ai-logs"])

MAX_RUNS = 25
MAX_EVENTS_PER_RUN = 40
MAX_SCANS = 8
MAX_EVENT_LOG = 150
MAX_TRACEBACK_CHARS = 1500
MAX_MESSAGE_CHARS = 500

_PREAMBLE = """\
==================================================================
METRONOME AI DIAGNOSTIC LOG
==================================================================
You are reading a diagnostic dump from Metronome, an internal data-
governance app. One of its features ("Flows") automates downloading
reports from the ASAP portal - a Samsung web portal wrapping an embedded
MicroStrategy Library instance - using a Playwright-driven Edge browser
on a BI desktop (the "flow worker"), plus the MicroStrategy REST API
under the portal's /lib/api context path for catalog scanning.

Key vocabulary:
- flow: a configured report download (filters, periods, export views,
  target folder, optional transformation and SQL insertion).
- run: one execution of a flow. Runs emit stage events (navigation,
  configuring, report_execution, file_export, file_transfer,
  transformation, sql_insertion) and may retry per file (export_retry),
  re-run empty renders, resume from saved files (resume_skip), or fall
  back between scan methods (microstrategy_fallback / ui_fallback).
- catalog scan: discovery of ASAP's reports/filters, MicroStrategy-first
  (REST API) with a rendered-UI fallback.

YOUR TASK: read the timelines and errors below, then (with your ability
to browse the live portal) determine WHY the failing step failed - wrong
selector, changed DOM, portal behavior, timing, API contract - and write
back concrete, actionable instructions: which step is wrong, what the
portal actually does, and exactly what to change. Be specific (selectors,
endpoints, payloads, waits). Structure your reply as:
  1. DIAGNOSIS - root cause per failure, with evidence.
  2. PORTAL FINDINGS - what you observed live that the logs could not see.
  3. INSTRUCTIONS - precise changes for the engineer/AI maintaining
     Metronome (it drives Playwright + the MicroStrategy REST API).
==================================================================
"""


def _clip(value, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} chars trimmed]"


def _loads(value, default):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _app_version() -> str:
    try:
        return Path(__file__).resolve().parents[2].joinpath("VERSION").read_text().strip()
    except Exception:
        return "unknown"


def _section(title: str) -> str:
    return f"\n=== {title} " + "=" * max(4, 60 - len(title)) + "\n"


def _build_report(since: datetime, window_label: str) -> str:
    now = datetime.now().replace(microsecond=0)
    since_text = since.isoformat(timespec="seconds")
    lines: list[str] = [_PREAMBLE]
    lines.append(f"Generated: {now.isoformat(timespec='seconds')} (app host local time)")
    lines.append(f"Window: {window_label} (since {since_text})")
    lines.append(f"App started: {APP_STARTED_AT.isoformat(timespec='seconds')}")
    lines.append(f"App version: {_app_version()}")
    lines.append(f"Host: {platform.system()} {platform.release()}; Python {sys.version.split()[0]}")
    lines.append("Note: database timestamps are app-host local time except the")
    lines.append("event-log audit trail, which is stored in UTC.")

    with get_db() as db:
        lines.append(_section("SITES"))
        for row in db.execute(
            """SELECT name, adapter, base_url, auth_url, enabled,
                      last_scan_at, last_scan_status, last_scan_error
               FROM flow_sites ORDER BY name"""
        ).fetchall():
            lines.append(
                f"- {row['name']} [{row['adapter']}] enabled={bool(row['enabled'])} "
                f"url={row['auth_url'] or row['base_url'] or '-'}"
            )
            lines.append(
                f"  last scan: {row['last_scan_at'] or 'never'} "
                f"status={row['last_scan_status'] or '-'}"
                + (f" error={_clip(row['last_scan_error'])}" if row["last_scan_error"] else "")
            )

        lines.append(_section("WORKERS"))
        workers = db.execute(
            "SELECT worker_id, display_name, status, last_seen_at, last_error FROM flow_workers ORDER BY worker_id"
        ).fetchall()
        if not workers:
            lines.append("(no workers have ever registered)")
        for row in workers:
            lines.append(
                f"- {row['worker_id']} ({row['display_name']}) status={row['status']} "
                f"last_seen={row['last_seen_at'] or 'never'}"
                + (f" last_error={_clip(row['last_error'])}" if row["last_error"] else "")
            )

        lines.append(_section("FLOWS"))
        for row in db.execute(
            """SELECT f.name, f.enabled, f.schedule_type, f.last_run_at, f.last_status,
                      f.last_error, f.browser_mode, p.name AS owner_name
               FROM flows f LEFT JOIN people p ON p.id = f.owner_person_id
               ORDER BY f.name"""
        ).fetchall():
            lines.append(
                f"- {row['name']} enabled={bool(row['enabled'])} schedule={row['schedule_type']} "
                f"browser={row['browser_mode']} owner={row['owner_name'] or '-'} "
                f"last_run={row['last_run_at'] or 'never'} last_status={row['last_status'] or '-'}"
            )
            if row["last_error"]:
                lines.append(f"  last_error: {_clip(row['last_error'])}")

        lines.append(_section(f"CATALOG SCANS (last {MAX_SCANS} in window)"))
        scans = db.execute(
            """SELECT s.*, site.name AS site_name FROM flow_catalog_scans s
               JOIN flow_sites site ON site.id = s.site_id
               WHERE s.created_at >= ? ORDER BY s.created_at DESC LIMIT ?""",
            (since_text, MAX_SCANS),
        ).fetchall()
        if not scans:
            lines.append("(no catalog scans in this window)")
        for row in scans:
            progress = _loads(row["progress_json"], {})
            result = _loads(row["result_json"], {})
            lines.append(
                f"scan #{row['id']} site={row['site_name']} status={row['status']} "
                f"trigger={row['trigger_type']} created={row['created_at']} "
                f"finished={row['finished_at'] or '-'}"
            )
            if progress.get("stage") or progress.get("message"):
                lines.append(f"  last progress: [{progress.get('stage')}] {_clip(progress.get('message'))}")
            if result:
                lines.append(
                    f"  result: reports={result.get('report_count')} filters={result.get('filter_count')} "
                    f"complete={result.get('complete')}"
                )
            if row["error"]:
                lines.append(f"  error: {_clip(row['error'], MAX_TRACEBACK_CHARS)}")

        lines.append(_section(f"FLOW RUNS (last {MAX_RUNS} in window, newest first)"))
        runs = db.execute(
            """SELECT r.*, f.name AS flow_name FROM flow_runs r
               JOIN flows f ON f.id = r.flow_id
               WHERE r.created_at >= ? ORDER BY r.created_at DESC LIMIT ?""",
            (since_text, MAX_RUNS),
        ).fetchall()
        if not runs:
            lines.append("(no flow runs in this window)")
        for row in runs:
            job = _loads(row["job_json"], {})
            artifacts = _loads(row["artifact_json"], [])
            saved = [item for item in artifacts if item.get("status") == "saved"]
            lines.append(
                f"\nrun #{row['id']} flow={row['flow_name']} status={row['status']} "
                f"trigger={row['trigger_type']} worker={row['worker_id'] or '-'} "
                f"created={row['created_at']} started={row['started_at'] or '-'} "
                f"finished={row['finished_at'] or '-'}"
            )
            downloads = job.get("downloads", {})
            lines.append(
                f"  job: periods={len(downloads.get('periods') or [])} "
                f"export_views={len(job.get('report', {}).get('export_views') or [])} "
                f"format={downloads.get('file_format')} "
                f"browser={job.get('execution', {}).get('browser_mode')} "
                f"resume_from={job.get('resume', {}).get('from_run_id') or '-'}"
            )
            lines.append(f"  files saved: {len(saved)} of {len(artifacts)} artifact entries")
            if row["error"]:
                lines.append(f"  error: {_clip(row['error'], MAX_TRACEBACK_CHARS)}")
            events = db.execute(
                """SELECT status, stage, message, error, traceback, created_at
                   FROM flow_run_events WHERE run_id=? ORDER BY id DESC LIMIT ?""",
                (row["id"], MAX_EVENTS_PER_RUN),
            ).fetchall()
            for event in reversed(events):
                lines.append(
                    f"    {event['created_at']} [{event['status']}/{event['stage'] or '-'}] "
                    f"{_clip(event['message'])}"
                )
                if event["error"] and event["error"] != row["error"]:
                    lines.append(f"      error: {_clip(event['error'])}")
                if event["traceback"] and event["status"] == "failed":
                    lines.append(f"      traceback (tail): {_clip(event['traceback'][-MAX_TRACEBACK_CHARS:], MAX_TRACEBACK_CHARS)}")
            timings = db.execute(
                """SELECT phase, duration_ms, status FROM flow_operation_timings
                   WHERE run_id=? ORDER BY id""",
                (row["id"],),
            ).fetchall()
            if timings:
                lines.append("  timings: " + "; ".join(
                    f"{item['phase']}={item['duration_ms']}ms({item['status']})" for item in timings
                ))

        lines.append(_section(f"EVENT LOG (last {MAX_EVENT_LOG} in window, UTC)"))
        for row in db.execute(
            """SELECT created_at, entity_type, entity_name, action, detail, actor
               FROM event_log WHERE created_at >= datetime(?, 'utc')
               ORDER BY created_at DESC LIMIT ?""",
            (since_text, MAX_EVENT_LOG),
        ).fetchall():
            lines.append(
                f"- {row['created_at']} {row['entity_type']}/{row['entity_name'] or '-'} "
                f"{row['action']}"
                + (f": {_clip(row['detail'], 240)}" if row["detail"] else "")
                + (f" (by {row['actor']})" if row["actor"] else "")
            )

    lines.append(_section("APP PROCESS LOG (ring buffer since app start)"))
    process_lines = process_log_lines()
    if not process_lines:
        lines.append("(no captured process log records)")
    lines.extend(process_lines)

    lines.append(_section("END"))
    lines.append(
        "Truncation rules: long messages clipped to "
        f"{MAX_MESSAGE_CHARS} chars, tracebacks to their last {MAX_TRACEBACK_CHARS} chars, "
        f"{MAX_RUNS} runs / {MAX_EVENTS_PER_RUN} events per run / {MAX_SCANS} scans max. "
        "Ask for a narrower window if something you need was clipped."
    )
    return "\n".join(lines) + "\n"


@router.get("", response_class=PlainTextResponse)
def ai_logs(hours: int | None = Query(default=None, ge=1, le=24 * 30)):
    """Build the AI-friendly diagnostic report.

    Without ``hours`` the window starts at app start; with it, at now-hours.
    """
    if hours:
        since = datetime.now().replace(microsecond=0) - timedelta(hours=hours)
        label = f"last {hours} hour(s)"
    else:
        since = APP_STARTED_AT
        label = "since app start"
    return _build_report(since, label)
