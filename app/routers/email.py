"""Outlook alert summary email helpers.

Legacy task-summary endpoints remain for compatibility with older clients, but
the current product surface and scheduled owner emails are alert-only.
"""

import html
import json
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import BASE_DIR
from app.database import get_db
from app.routers.actions import list_actions as list_action_alerts
from app.routers.eventlog import get_actor, log_event
from app.routers.tasks import _get_links

router = APIRouter(prefix="/api/email", tags=["email"])

OUTLOOK_SCRIPT = BASE_DIR / "tools" / "outlook_task_email.ps1"
TASK_NAME = "DG_Outlook_Task_Email"

TASK_STATUS_LABELS = {
    "backlog": "Backlog",
    "todo": "To Do",
    "in_progress": "In Progress",
    "review": "Review",
}

ACTION_TYPE_LABELS = {
    "stale_source": "Degraded source",
    "outdated_source": "Degraded source",
    "error_source": "Source error",
    "broken_ref": "Broken reference",
    "changed_query": "Query changed",
    "refresh_failed": "Refresh failed",
    "refresh_overdue": "Refresh overdue",
    "task_failed": "Scheduled task failed",
    "script_failed": "Script failed",
    "schedule_mismatch": "Stale vs source",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PATH_SOURCE_TYPES = {"csv", "excel", "file", "folder", "sharepoint", "web"}
SQL_SOURCE_TYPES = {"postgres", "postgresql", "sql", "sql server", "mssql", "mysql", "oracle"}
SOURCE_ALERT_TYPES = {"stale_source", "outdated_source", "error_source"}
DEGRADED_SOURCE_RECOMMENDATION = (
    "Find out why these sources haven't updated. Check linked scripts and scheduled tasks. "
    "Once they're updated, refresh upstream reports."
)
WEEKDAY_ORDER = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


class OutlookEmailError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 500):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class PersonEmailUpdate(BaseModel):
    email: str | None = None
    include_all_alerts: bool | None = None


class SendSummariesRequest(BaseModel):
    owner_names: list[str] | None = None
    mode: str = "draft"


def _normalize_email(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _payload_path() -> Path:
    root = os.environ.get("PROGRAMDATA") or tempfile.gettempdir()
    directory = Path(root) / "DataGovernance"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return directory / f"outlook-task-email-{stamp}.json"


def _is_url(value: str | None) -> bool:
    return bool(value and value.lower().startswith(("http://", "https://")))


def _file_href(path: str | None) -> str | None:
    if not path:
        return None
    raw = path.strip()
    if _is_url(raw):
        return raw
    if raw.startswith("\\\\"):
        cleaned = raw.strip("\\").replace("\\", "/")
        return "file://" + quote(cleaned, safe="/:")
    if len(raw) >= 3 and raw[1] == ":" and raw[2] in ("\\", "/"):
        cleaned = raw.replace("\\", "/")
        return "file:///" + quote(cleaned, safe="/:")
    if raw.startswith("/"):
        return "file://" + quote(raw, safe="/:")
    return None


def _folder_from_path(path: str | None, source_type: str | None = None) -> str | None:
    if not path or _is_url(path):
        return path
    raw = path.strip().rstrip("\\/")
    if not raw:
        return None
    if (source_type or "").lower() == "folder":
        return raw
    sep = "\\" if "\\" in raw else "/"
    if sep not in raw:
        return raw
    leaf = raw.rsplit(sep, 1)[-1]
    if "." not in leaf:
        return raw
    return raw.rsplit(sep, 1)[0]


def _looks_like_source_path(path: str | None, source_type: str | None = None) -> bool:
    if not path:
        return False
    raw = path.strip()
    source_kind = (source_type or "").lower()
    if _is_url(raw) or raw.startswith("\\\\") or raw.startswith("/"):
        return True
    if len(raw) >= 3 and raw[1] == ":" and raw[2] in ("\\", "/"):
        return True
    return source_kind in PATH_SOURCE_TYPES and ("\\" in raw or "/" in raw)


def _source_link(source: dict | None) -> dict | None:
    if not source:
        return None
    raw_path = source.get("connection_info") or source.get("source_query") or source.get("name")
    if not _looks_like_source_path(raw_path, source.get("type")):
        return None
    source_kind = (source.get("type") or "").lower()
    target = raw_path if source_kind in {"csv", "excel", "file", "sharepoint", "web"} or _is_url(raw_path) else _folder_from_path(raw_path, source.get("type"))
    href = _file_href(target)
    if not target:
        return None
    link_label = "Source URL" if _is_url(target) else "Source File" if target == raw_path and source_kind != "folder" else "Source Folder"
    return {
        "source_id": source["id"],
        "source_name": source["name"],
        "link_label": link_label,
        "path": raw_path,
        "folder_path": target,
        "href": href,
    }


def _report_link(report: dict | None) -> dict | None:
    if not report:
        return None
    return {
        "report_id": report["id"],
        "report_name": report["name"],
        "powerbi_url": report.get("powerbi_url"),
        "tmdl_path": report.get("tmdl_path"),
    }


def _html_link(label: str, href: str | None) -> str:
    label_html = html.escape(label)
    if not href:
        return label_html
    return f'<a href="{html.escape(href, quote=True)}">{label_html}</a>'


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = value.replace("Z", "+00:00") if isinstance(value, str) else value
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def _duration_text_from_hours(hours: float | int | None) -> str:
    if hours is None:
        return "-"
    hours = max(0, float(hours))
    if hours < 48:
        whole_hours = max(1, int(round(hours)))
        return f"{whole_hours}h"
    days = max(1, int(round(hours / 24)))
    return f"{days}d"


def _age_text(value: str | None) -> str:
    dt = _parse_dt(value)
    if not dt:
        return "-"
    delta_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return _duration_text_from_hours(delta_hours)


def _source_raw_path(source: dict | None) -> str | None:
    if not source:
        return None
    return source.get("connection_info") or source.get("source_query") or source.get("name")


def _is_sql_source(source: dict | None) -> bool:
    return (source or {}).get("type", "").lower() in SQL_SOURCE_TYPES


def _path_leaf(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().rstrip("\\/").replace("\\", "/")
    if not normalized:
        return None
    return normalized.rsplit("/", 1)[-1] or normalized


def _source_display_name(source: dict | None) -> str:
    if not source:
        return "Unknown source"
    if _is_sql_source(source):
        return source.get("name") or "Unknown source"
    raw_path = _source_raw_path(source)
    if _looks_like_source_path(raw_path, source.get("type")):
        return _path_leaf(raw_path) or source.get("name") or "Unknown source"
    return source.get("name") or _path_leaf(raw_path) or "Unknown source"


def _source_href(source: dict | None) -> str | None:
    if not source or _is_sql_source(source):
        return None
    raw_path = _source_raw_path(source)
    if not _looks_like_source_path(raw_path, source.get("type")):
        return None
    return _file_href(raw_path)


def _source_cell_html(source: dict | None, fallback_name: str | None = None) -> str:
    label = _source_display_name(source) if source else (fallback_name or "Unknown source")
    return _html_link(label, _source_href(source))


def _source_text(source: dict | None, fallback_name: str | None = None) -> str:
    label = _source_display_name(source) if source else (fallback_name or "Unknown source")
    href = _source_href(source)
    return f"{label} ({href})" if href else label


def _fixed_schedule_max_gap_days(schedule_days: str | None) -> int | None:
    days = []
    for raw in (schedule_days or "").split(","):
        day = raw.strip().capitalize()
        if day in WEEKDAY_ORDER and WEEKDAY_ORDER[day] not in days:
            days.append(WEEKDAY_ORDER[day])
    if not days:
        return None
    days.sort()
    if len(days) == 1:
        return 7
    gaps = []
    for idx, day in enumerate(days):
        nxt = days[(idx + 1) % len(days)]
        gaps.append((nxt - day) % 7 or 7)
    return max(gaps)


def _source_max_age_days(source: dict | None) -> str:
    if not source:
        return "-"
    custom = source.get("custom_fresh_days")
    if custom:
        return str(custom)
    rule_type = (source.get("freshness_rule_type") or "").lower()
    if rule_type == "daily":
        return "1"
    if rule_type == "fixed":
        gap = _fixed_schedule_max_gap_days(source.get("freshness_schedule_days"))
        return str(gap) if gap else "-"
    return "-"


def _report_href(report: dict | None) -> str | None:
    if not report:
        return None
    return report.get("powerbi_url") or _file_href(report.get("tmdl_path"))


def _report_cell_html(report: dict | None, fallback_name: str | None = None) -> str:
    if not report:
        return html.escape(fallback_name or "Unknown report")
    return _html_link(report.get("report_name") or report.get("name") or fallback_name or "Unknown report", _report_href(report))


def _report_text(report: dict | None, fallback_name: str | None = None) -> str:
    if not report:
        return fallback_name or "Unknown report"
    label = report.get("report_name") or report.get("name") or fallback_name or "Unknown report"
    href = _report_href(report)
    return f"{label} ({href})" if href else label


def _report_links_html(reports: list[dict]) -> str:
    if not reports:
        return "-"
    return "<br>".join(_report_cell_html(r) for r in reports)


def _report_links_text(reports: list[dict]) -> str:
    if not reports:
        return "-"
    return "; ".join(_report_text(r) for r in reports)


def _td(value: str, extra_style: str = "") -> str:
    style = "padding:6px 8px;border:1px solid #d7dce2;vertical-align:top"
    if extra_style:
        style += ";" + extra_style
    return f'<td style="{style}">{value}</td>'


def _th(value: str) -> str:
    return f'<th style="padding:6px 8px;border:1px solid #d7dce2">{html.escape(value)}</th>'


def _priority_sort_key(row) -> tuple[int, str, int]:
    return (
        PRIORITY_ORDER.get((row["priority"] or "").lower(), 1),
        row["due_date"] or "9999-12-31",
        row["position"] or 0,
    )


def _format_due(due_date: str | None) -> str:
    if not due_date:
        return "No due date"
    try:
        return datetime.fromisoformat(due_date).strftime("%d %b %Y")
    except ValueError:
        return due_date


def _task_link_text(task: dict) -> str:
    links = task.get("linked_entities") or []
    parts = []
    for link in links:
        label = link.get("entity_type", "").replace("_", " ").title()
        name = link.get("entity_name") or f"ID {link.get('entity_id')}"
        parts.append(f"{label}: {name}")
    return ", ".join(parts)


def _build_task_summary(owner: dict, tasks: list[dict]) -> dict:
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    owner_name = owner["name"]
    subject = f"Pending task summary - {owner_name} - {today}"

    grouped = {}
    for task in tasks:
        grouped.setdefault(task["status"], []).append(task)

    lines = [
        f"Hi {owner_name},",
        "",
        f"Here is your pending task summary as of {today}.",
        "",
    ]
    for status, label in TASK_STATUS_LABELS.items():
        group = grouped.get(status) or []
        if not group:
            continue
        lines.append(f"{label} ({len(group)})")
        for task in group:
            due = _format_due(task.get("due_date"))
            links = _task_link_text(task)
            line = f"- [{task.get('priority', 'medium').upper()}] {task['title']} ({due})"
            if links:
                line += f" | {links}"
            lines.append(line)
            if task.get("description"):
                lines.append(f"  {task['description']}")
        lines.append("")

    lines.append("Thanks,")
    lines.append("Metronome")
    body_text = "\n".join(lines)

    html_parts = [
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#202124;max-width:760px\">",
        f"<p>Hi {html.escape(owner_name)},</p>",
        f"<p>Here is your pending task summary as of {html.escape(today)}.</p>",
    ]
    for status, label in TASK_STATUS_LABELS.items():
        group = grouped.get(status) or []
        if not group:
            continue
        html_parts.append(f"<h3 style=\"font-size:14px;margin:18px 0 8px\">{html.escape(label)} ({len(group)})</h3>")
        html_parts.append("<table style=\"width:100%;border-collapse:collapse;font-size:13px\">")
        html_parts.append(
            "<tr style=\"background:#f3f4f6;text-align:left\">"
            "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Priority</th>"
            "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Task</th>"
            "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Due</th>"
            "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Linked To</th>"
            "</tr>"
        )
        for task in group:
            desc = task.get("description")
            title_html = f"<strong>{html.escape(task['title'])}</strong>"
            if desc:
                title_html += f"<br><span style=\"color:#6b7280;font-size:12px\">{html.escape(desc)}</span>"
            html_parts.append(
                "<tr>"
                f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\">{html.escape(task.get('priority') or 'medium')}</td>"
                f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\">{title_html}</td>"
                f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\">{html.escape(_format_due(task.get('due_date')))}</td>"
                f"<td style=\"padding:6px 8px;border:1px solid #d7dce2;color:#4b5563\">{html.escape(_task_link_text(task) or '-')}</td>"
                "</tr>"
            )
        html_parts.append("</table>")
    html_parts.append("<p>Thanks,<br>Metronome</p></div>")

    return {
        "owner_name": owner_name,
        "email": owner.get("email"),
        "task_count": len(tasks),
        "subject": subject,
        "body_text": body_text,
        "body_html": "".join(html_parts),
        "mailto": f"mailto:{quote(owner.get('email') or '')}?subject={quote(subject)}&body={quote(body_text)}",
        "tasks": tasks,
    }


def _build_alert_summary(owner: dict, alerts: list[dict]) -> dict:
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    owner_name = owner["name"]
    subject = f"{len(alerts)} active alert{'s' if len(alerts) != 1 else ''} need attention - {owner_name} - {today}"

    failure_types = {"refresh_failed", "error_source", "script_failed", "task_failed", "broken_ref"}

    def priority(alert: dict) -> tuple[str, int]:
        impact = int(alert.get("impact_views_30d") or 0)
        reports = len(alert.get("report_links") or [])
        age = int(alert.get("asset_days") or 0)
        if alert.get("type") in failure_types or impact >= 100:
            return "Urgent", 0
        if impact > 0 or reports >= 3 or age >= 7:
            return "High", 1
        return "Normal", 2

    ranked = sorted(
        alerts,
        key=lambda alert: (
            priority(alert)[1],
            -(alert.get("impact_views_30d") or 0),
            -len(alert.get("report_links") or []),
            -(alert.get("asset_days") or 0),
            alert.get("asset_name") or "",
        ),
    )
    for alert in ranked:
        alert["email_priority"] = priority(alert)[0].lower()
    urgent_count = sum(1 for alert in ranked if priority(alert)[0] == "Urgent")
    affected_reports = {
        report.get("report_id")
        for alert in ranked
        for report in (alert.get("report_links") or [])
        if report.get("report_id") is not None
    }

    def impact_text(alert: dict) -> str:
        report_count = len(alert.get("report_links") or [])
        views = int(alert.get("impact_views_30d") or 0)
        bits = []
        if report_count:
            bits.append(f"{report_count} report{'s' if report_count != 1 else ''}")
        if views:
            bits.append(f"{views:,} weighted views in 30d")
        return ", ".join(bits) or "No measured report impact"

    def next_action(alert: dict) -> str:
        return alert.get("recommendation") or alert.get("triage_cta") or "Open the alert and review the evidence."

    def asset_html(alert: dict) -> str:
        if alert.get("report_detail"):
            return _report_cell_html(alert.get("report_detail"), alert.get("asset_name"))
        if alert.get("source_detail"):
            return _source_cell_html(alert.get("source_detail"), alert.get("asset_name"))
        return html.escape(alert.get("asset_name") or "Unknown asset")

    def asset_text(alert: dict) -> str:
        if alert.get("report_detail"):
            return _report_text(alert.get("report_detail"), alert.get("asset_name"))
        if alert.get("source_detail"):
            return _source_text(alert.get("source_detail"), alert.get("asset_name"))
        return alert.get("asset_name") or "Unknown asset"

    lines = [
        f"Hi {owner_name},",
        "",
        f"{len(ranked)} active alert{'s' if len(ranked) != 1 else ''} need attention as of {today}.",
        f"Urgent: {urgent_count} | Reports affected: {len(affected_reports)}",
        "",
        "WHAT TO DO FIRST",
    ]
    for index, alert in enumerate(ranked[:3], start=1):
        label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        lines.append(f"{index}. [{priority(alert)[0]}] {label}: {asset_text(alert)}")
        lines.append(f"   Impact: {impact_text(alert)} | Open: {alert.get('asset_days') or 0}d")
        lines.append(f"   Next action: {next_action(alert)}")

    lines.extend(["", "ALL ACTIVE ALERTS"])
    for alert in ranked:
        label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        lines.append(
            f"- [{priority(alert)[0]}] {label} | {asset_text(alert)} | "
            f"{impact_text(alert)} | Open {alert.get('asset_days') or 0}d"
        )
        lines.append(f"  Next action: {next_action(alert)}")

    lines.extend(["", "Thanks,", "Metronome"])
    body_text = "\n".join(lines)

    html_parts = [
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:13px;line-height:1.45;color:#1f2937;max-width:920px\">",
        f"<p>Hi {html.escape(owner_name)},</p>",
        f"<p><strong>{len(ranked)} active alert{'s' if len(ranked) != 1 else ''} need attention.</strong> This summary is ranked by failure risk, measured usage, affected reports, and age.</p>",
        '<table role="presentation" style="width:100%;border-collapse:collapse;margin:14px 0 20px"><tr>',
        f'<td style="padding:12px 14px;background:#f3f7f6;border:1px solid #d8e4e1"><div style="font-size:22px;font-weight:700">{len(ranked)}</div><div style="color:#5b6670">Active alerts</div></td>',
        f'<td style="padding:12px 14px;background:#fff7ed;border:1px solid #f1ddc7"><div style="font-size:22px;font-weight:700">{urgent_count}</div><div style="color:#5b6670">Urgent</div></td>',
        f'<td style="padding:12px 14px;background:#f3f7f6;border:1px solid #d8e4e1"><div style="font-size:22px;font-weight:700">{len(affected_reports)}</div><div style="color:#5b6670">Reports affected</div></td>',
        "</tr></table>",
        '<h2 style="font-size:16px;margin:0 0 10px">What to do first</h2>',
    ]
    for index, alert in enumerate(ranked[:3], start=1):
        label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        html_parts.append(
            '<div style="border:1px solid #d7dce2;border-left:4px solid #18766d;padding:11px 13px;margin:0 0 8px">'
            f'<div style="font-size:12px;color:#5b6670;margin-bottom:3px">{index}. {html.escape(priority(alert)[0])} - {html.escape(label)}</div>'
            f'<div style="font-weight:700;margin-bottom:4px">{asset_html(alert)}</div>'
            f'<div style="color:#5b6670">{html.escape(impact_text(alert))} - Open {int(alert.get("asset_days") or 0)}d</div>'
            f'<div style="margin-top:6px"><strong>Next action:</strong> {html.escape(next_action(alert))}</div>'
            "</div>"
        )

    html_parts.extend([
        f'<h2 style="font-size:16px;margin:22px 0 10px">All active alerts ({len(ranked)})</h2>',
        '<table style="width:100%;border-collapse:collapse;font-size:12px">',
        '<tr style="background:#eef3f2;text-align:left">'
        + _th("Priority") + _th("Issue") + _th("Asset") + _th("Impact") + _th("Open") + _th("Next action") + "</tr>",
    ])
    for alert in ranked:
        label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        html_parts.append(
            "<tr>"
            + _td(f"<strong>{html.escape(priority(alert)[0])}</strong>")
            + _td(html.escape(label))
            + _td(f"<strong>{asset_html(alert)}</strong>")
            + _td(html.escape(impact_text(alert)))
            + _td(f"{int(alert.get('asset_days') or 0)}d")
            + _td(html.escape(next_action(alert)), "color:#4b5563;min-width:210px")
            + "</tr>"
        )
    html_parts.append("</table>")

    html_parts.append("<p>Thanks,<br>Metronome</p></div>")

    return {
        "owner_name": owner_name,
        "email": owner.get("email"),
        "include_all_alerts": bool(owner.get("include_all_alerts")),
        "alert_count": len(alerts),
        "subject": subject,
        "body_text": body_text,
        "body_html": "".join(html_parts),
        "mailto": f"mailto:{quote(owner.get('email') or '')}?subject={quote(subject)}&body={quote(body_text)}",
        "alerts": alerts,
    }


def _load_task_summaries(owner_names: set[str] | None = None) -> list[dict]:
    with get_db() as db:
        people = db.execute(
            "SELECT id, name, role, email, created_at FROM people WHERE role = 'BI' ORDER BY name"
        ).fetchall()
        if owner_names:
            people = [p for p in people if p["name"] in owner_names]
        owners = {p["name"]: dict(p) for p in people}
        if not owners:
            return []

        placeholders = ",".join("?" for _ in owners)
        rows = db.execute(
            f"""SELECT * FROM tasks
                WHERE status != 'done'
                  AND assigned_to IN ({placeholders})
                ORDER BY assigned_to, status, due_date, position""",
            list(owners.keys()),
        ).fetchall()

        tasks_by_owner: dict[str, list[dict]] = {name: [] for name in owners}
        for row in rows:
            task = dict(row)
            task["email_owner"] = bool(task.get("email_owner"))
            task["linked_entities"] = [link.model_dump() for link in _get_links(db, row["id"])]
            tasks_by_owner[row["assigned_to"]].append(task)

    summaries = []
    for owner_name, owner in owners.items():
        owner_tasks = sorted(tasks_by_owner.get(owner_name) or [], key=_priority_sort_key)
        if owner_tasks:
            summaries.append(_build_task_summary(owner, owner_tasks))
    return summaries


def _load_alert_summaries(owner_names: set[str] | None = None) -> list[dict]:
    with get_db() as db:
        people = db.execute(
            """SELECT id, name, role, email, include_all_alerts, created_at
               FROM people
               WHERE role = 'BI'
               ORDER BY name"""
        ).fetchall()
        if owner_names:
            people = [p for p in people if p["name"] in owner_names]
        owners = {}
        for person in people:
            owner = dict(person)
            owner["include_all_alerts"] = bool(owner.get("include_all_alerts"))
            owners[owner["name"]] = owner
        if not owners:
            return []

        reports = {
            r["id"]: dict(r)
            for r in db.execute(
                "SELECT id, name, powerbi_url, tmdl_path, pbi_last_refresh_at FROM reports"
            ).fetchall()
        }
        sources = {
            s["id"]: dict(s)
            for s in db.execute("""
                SELECT s.id, s.name, s.type, s.connection_info, s.source_query,
                       s.custom_fresh_days, s.freshness_rule_type, s.freshness_schedule_days,
                       sp.status AS latest_status,
                       CAST(sp.last_data_at AS TEXT) AS last_data_at
                FROM sources s
                LEFT JOIN (
                    SELECT source_id, status, last_data_at,
                           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                    FROM source_probes
                ) sp ON sp.source_id = s.id AND sp.rn = 1
            """).fetchall()
        }
        report_rows = db.execute(
            """SELECT rt.source_id, r.id, r.name, r.powerbi_url, r.tmdl_path
               FROM report_tables rt
               JOIN reports r ON r.id = rt.report_id
               WHERE rt.source_id IS NOT NULL
               ORDER BY r.name"""
        ).fetchall()
        reports_by_source: dict[int, list[dict]] = {}
        for row in report_rows:
            reports_by_source.setdefault(row["source_id"], []).append(dict(row))

    include_all_owner_names = {
        name for name, owner in owners.items()
        if owner.get("include_all_alerts")
    }
    alert_rows = []
    for action in list_action_alerts():
        if action.status in ("resolved", "expected"):
            continue
        alert = action.model_dump()
        assigned_to = alert.get("assigned_to")
        if include_all_owner_names or assigned_to in owners:
            alert_rows.append(alert)

    alerts_by_owner: dict[str, list[dict]] = {name: [] for name in owners}
    for alert in alert_rows:
        report_links: list[dict] = []
        source_links: list[dict] = []

        report_id = alert.get("report_id") or (alert.get("asset_id") if alert.get("asset_type") == "report" else None)
        if report_id and reports.get(report_id):
            link = _report_link(reports[report_id])
            if link:
                report_links.append(link)

        source_id = alert.get("source_id") or (alert.get("asset_id") if alert.get("asset_type") == "source" else None)
        if source_id and sources.get(source_id):
            alert["source_detail"] = sources[source_id]
            link = _source_link(sources[source_id])
            if link:
                source_links.append(link)
            for report in reports_by_source.get(source_id, []):
                link = _report_link(report)
                if link:
                    report_links.append(link)

        if report_id and reports.get(report_id):
            alert["report_detail"] = reports[report_id]

        for detail in alert.get("detail_items") or []:
            detail_source = sources.get(detail.get("id"))
            if detail_source:
                detail["source_detail"] = detail_source
            link = _source_link(detail_source)
            if link:
                link["delta_hours"] = detail.get("delta_hours")
                source_links.append(link)

        dedup_reports = {r["report_id"]: r for r in report_links}
        dedup_sources = {s["source_id"]: s for s in source_links}
        alert["report_links"] = list(dedup_reports.values())
        alert["source_links"] = list(dedup_sources.values())
        alert["issue_label"] = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        alert["asset_name"] = alert.get("asset_name") or alert.get("source_name") or alert.get("report_name") or "Unknown asset"
        target_owner_names = set(include_all_owner_names)
        assigned_to = alert.get("assigned_to")
        if assigned_to in owners:
            target_owner_names.add(assigned_to)
        for owner_name in sorted(target_owner_names):
            alerts_by_owner[owner_name].append(alert)

    summaries = []
    for owner_name, owner in owners.items():
        owner_alerts = sorted(
            alerts_by_owner.get(owner_name) or [],
            key=lambda a: (-(a.get("asset_days") or 0), a.get("asset_name") or ""),
        )
        if owner_alerts:
            summaries.append(_build_alert_summary(owner, owner_alerts))
    return summaries


def _launch_outlook_payload(messages: list[dict], mode: str = "send") -> int:
    mode = (mode or "send").lower().strip()
    if mode not in {"draft", "send"}:
        raise OutlookEmailError("Mode must be draft or send", status_code=422)
    if platform.system() != "Windows":
        raise OutlookEmailError("Server-side Outlook sending is only available on Windows", status_code=400)
    if not OUTLOOK_SCRIPT.exists():
        raise OutlookEmailError(f"Outlook script not found: {OUTLOOK_SCRIPT}", status_code=404)
    if not messages:
        raise OutlookEmailError("No email messages to send", status_code=400)

    payload = {"mode": mode, "messages": messages}
    payload_path = _payload_path()
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    ps_cmd = (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{OUTLOOK_SCRIPT}" '
        f'-PayloadPath "{payload_path}"'
    )
    if mode == "send":
        ps_cmd += " -Send"

    try:
        subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True, timeout=10)
        subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/tr", ps_cmd, "/sc", "once", "/st", "00:00", "/it", "/f"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        subprocess.run(
            ["schtasks", "/run", "/tn", TASK_NAME],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise OutlookEmailError(f"Failed to launch Outlook email task: {exc.stderr or exc}") from exc

    return len(messages)


def _launch_outlook_messages(messages: list[dict], mode: str, event_name: str, request: Request) -> dict:
    try:
        count = _launch_outlook_payload(messages, mode)
    except OutlookEmailError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    with get_db() as db:
        log_event(
            db,
            "email",
            None,
            event_name,
            "sent" if mode == "send" else "drafted",
            f"{len(messages)} owner summaries",
            get_actor(request),
        )
    return {"status": "launched", "mode": mode, "count": count}


@router.get("/people")
def list_people_email_status():
    """Return people with email mapping and pending task counts."""
    with get_db() as db:
        rows = db.execute(
            """SELECT p.id, p.name, p.role, p.email, p.include_all_alerts, p.created_at,
                      SUM(CASE WHEN t.id IS NOT NULL AND t.status != 'done' THEN 1 ELSE 0 END) AS pending_task_count
               FROM people p
               LEFT JOIN tasks t ON t.assigned_to = p.name
               GROUP BY p.id, p.name, p.role, p.email, p.include_all_alerts, p.created_at
               ORDER BY CASE p.role WHEN 'BI' THEN 0 ELSE 1 END, p.name"""
        ).fetchall()
    alert_counts: dict[str, int] = {}
    all_alert_count = 0
    for action in list_action_alerts():
        if action.status in ("resolved", "expected"):
            continue
        all_alert_count += 1
        if not action.assigned_to:
            continue
        alert_counts[action.assigned_to] = alert_counts.get(action.assigned_to, 0) + 1
    result = []
    for row in rows:
        item = dict(row)
        item["include_all_alerts"] = bool(item.get("include_all_alerts"))
        item["pending_alert_count"] = (
            all_alert_count if item["include_all_alerts"] else alert_counts.get(item["name"], 0)
        )
        result.append(item)
    return result


@router.patch("/people/{person_id}")
def update_person_email(person_id: int, body: PersonEmailUpdate, request: Request):
    """Update the Outlook email settings linked to a person."""
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No changes provided")

    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")

        fields = []
        values = []
        detail_parts = []
        if "email" in data:
            email = _normalize_email(data.get("email"))
            fields.append("email = ?")
            values.append(email)
            detail_parts.append(email or "cleared")
        if "include_all_alerts" in data:
            include_all_alerts = bool(data.get("include_all_alerts"))
            fields.append("include_all_alerts = ?")
            values.append(1 if include_all_alerts else 0)
            detail_parts.append(f"include_all_alerts={include_all_alerts}")
        if not fields:
            raise HTTPException(status_code=400, detail="No changes provided")

        values.append(person_id)
        db.execute(f"UPDATE people SET {', '.join(fields)} WHERE id = ?", values)
        updated = db.execute(
            "SELECT email, include_all_alerts FROM people WHERE id = ?",
            (person_id,),
        ).fetchone()
        log_event(db, "person", person_id, row["name"], "email_updated", ", ".join(detail_parts), get_actor(request))
    return {
        "status": "updated",
        "id": person_id,
        "email": updated["email"],
        "include_all_alerts": bool(updated["include_all_alerts"]),
    }


@router.get("/task-summaries")
def get_task_summaries():
    """Build pending task email summaries grouped by BI owner."""
    return {"summaries": _load_task_summaries()}


@router.get("/alert-summaries")
def get_alert_summaries():
    """Build active alert email summaries grouped by BI owner."""
    return {"summaries": _load_alert_summaries()}


@router.post("/send-task-summaries")
def send_task_summaries(body: SendSummariesRequest, request: Request):
    """Create drafts or send pending task summaries through Outlook on the app host."""
    mode = body.mode.lower().strip()
    if mode not in {"draft", "send"}:
        raise HTTPException(status_code=422, detail="Mode must be draft or send")

    requested = set(body.owner_names or []) or None
    summaries = [s for s in _load_task_summaries(requested) if s.get("email")]
    if not summaries:
        raise HTTPException(status_code=400, detail="No owners with both pending tasks and email addresses")

    messages = [
        {"to": s["email"], "subject": s["subject"], "html_body": s["body_html"]}
        for s in summaries
    ]
    return _launch_outlook_messages(messages, mode, "task_summaries", request)


@router.post("/send-alert-summaries")
def send_alert_summaries(body: SendSummariesRequest, request: Request):
    """Create drafts or send active alert summaries through Outlook on the app host."""
    mode = body.mode.lower().strip()
    if mode not in {"draft", "send"}:
        raise HTTPException(status_code=422, detail="Mode must be draft or send")

    requested = set(body.owner_names or []) or None
    summaries = [s for s in _load_alert_summaries(requested) if s.get("email")]
    if not summaries:
        raise HTTPException(status_code=400, detail="No owners with both active alerts and email addresses")

    messages = [
        {"to": s["email"], "subject": s["subject"], "html_body": s["body_html"]}
        for s in summaries
    ]
    return _launch_outlook_messages(messages, mode, "alert_summaries", request)
