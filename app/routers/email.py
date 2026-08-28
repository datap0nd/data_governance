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
import uuid
from datetime import datetime, timedelta, timezone
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

# Every scheduled alert email leads with the product name so it is recognisable
# in a crowded inbox and filterable by rule.
ALERT_SUBJECT_PREFIX = "Metronome alerts"

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
    "schedule_mismatch": "Upstream data is newer",
    "flow_failed": "Flow failed",
    "pipeline_failed": "Full Pipeline failed",
    "pbi_reconnect": "Power BI reconnect required",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PATH_SOURCE_TYPES = {"csv", "excel", "file", "folder", "sharepoint", "web"}
SQL_SOURCE_TYPES = {"postgres", "postgresql", "sql", "sql server", "mssql", "mysql", "oracle"}
SOURCE_ALERT_TYPES = {"stale_source", "outdated_source", "error_source"}
DEGRADED_SOURCE_RECOMMENDATION = (
    "Find out why these sources haven't updated. Check their linked Flows or refresh processes. "
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


def _alert_degraded_since(value: str | None) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%d %b %Y") if dt else "Unknown"


def _alert_artifact(alert: dict) -> tuple[str, str]:
    if alert.get("report_detail") or alert.get("asset_type") == "report":
        return "Power BI", "powerbi"
    source_type = str((alert.get("source_detail") or {}).get("type") or "").casefold()
    if source_type in SQL_SOURCE_TYPES:
        return "SQL", "sql"
    if source_type in {"excel", "csv", "file", "folder"}:
        return "Excel", "excel"
    if source_type in {"web", "sharepoint"}:
        return "Web", "web"
    if alert.get("asset_type") == "script":
        return "Python", "python"
    if alert.get("asset_type") == "scheduled_task":
        return "Windows", "windows"
    return "Data", "data"


def _current_alert_ai_assessments(action_ids: list[int]) -> dict[int, dict]:
    """Return only completed analyses for the current immutable Alert revision."""
    if not action_ids:
        return {}
    placeholders = ",".join("?" for _ in action_ids)
    with get_db() as db:
        rows = db.execute(
            f"""SELECT ar.id, ar.action_id, ar.provider_mode, ar.final_json,
                       ar.finished_at
                  FROM agent_runs ar
                  JOIN actions a ON a.id=ar.action_id
                 WHERE ar.action_id IN ({placeholders})
                   AND ar.status='completed'
                   AND ar.focus_type='alert'
                   AND ar.superseded_at IS NULL
                   AND ar.action_evidence_revision=a.evidence_revision
                   AND a.status IN ('open','acknowledged','investigating')
                   AND ar.id=(
                       SELECT MAX(newer.id) FROM agent_runs newer
                        WHERE newer.action_id=ar.action_id
                          AND newer.action_evidence_revision=ar.action_evidence_revision
                          AND newer.status='completed'
                          AND newer.focus_type='alert'
                          AND newer.superseded_at IS NULL
                   )""",
            action_ids,
        ).fetchall()
    assessments: dict[int, dict] = {}
    for row in rows:
        # Refresh the context-hash boundary immediately before email use. This
        # catches live probe/report/check changes even from legacy producers
        # that did not increment actions.evidence_revision.
        from app.ai import run_store

        current_run = run_store.get_run(int(row["id"]))
        if (
            not current_run
            or current_run.get("status") != "completed"
            or not current_run.get("is_current")
        ):
            continue
        result = current_run.get("result")
        if not isinstance(result, dict) or not str(result.get("conclusion") or "").strip():
            continue
        recommendations = result.get("recommendations") or []
        first = recommendations[0] if recommendations and isinstance(recommendations[0], dict) else {}
        assessments[int(row["action_id"])] = {
            "run_id": int(row["id"]),
            "provider_mode": row["provider_mode"],
            "assessment": result.get("alert_assessment") or "uncertain",
            "confidence": result.get("confidence") or "low",
            "conclusion": str(result["conclusion"]).strip()[:900],
            "recommendation_title": str(first.get("title") or "").strip()[:200] or None,
            "recommendation_rationale": str(first.get("rationale") or "").strip()[:600] or None,
            "finished_at": row["finished_at"],
        }
    return assessments


def _artifact_mark_html(kind: str) -> str:
    if kind == "powerbi":
        mark = (
            '<span style="display:inline-block;width:3px;height:8px;background:#e2aa13;margin-right:1px;vertical-align:bottom"></span>'
            '<span style="display:inline-block;width:3px;height:13px;background:#e2aa13;margin-right:1px;vertical-align:bottom"></span>'
            '<span style="display:inline-block;width:3px;height:18px;background:#e2aa13;vertical-align:bottom"></span>'
        )
        background = "#fff8d6"
        foreground = "#6b5200"
    elif kind == "excel":
        mark, background, foreground = "X", "#217346", "#ffffff"
    elif kind == "sql":
        mark, background, foreground = "SQL", "#e7f3f6", "#176b78"
    elif kind == "python":
        mark, background, foreground = "PY", "#e8f0f8", "#315f86"
    elif kind == "windows":
        mark, background, foreground = "WIN", "#eaf3fb", "#24678f"
    elif kind == "web":
        mark, background, foreground = "WEB", "#edf3f1", "#35665e"
    else:
        mark, background, foreground = "DATA", "#eef1f0", "#59625e"
    return (
        f'<span style="display:inline-block;width:28px;min-width:28px;height:24px;line-height:24px;'
        f'text-align:center;background:{background};color:{foreground};font-size:8px;font-weight:700;'
        f'border-radius:3px;vertical-align:middle">{mark}</span>'
    )


def _artifact_cell_html(alert: dict) -> str:
    label, kind = _alert_artifact(alert)
    return (
        '<span style="white-space:nowrap">'
        + _artifact_mark_html(kind)
        + f'<span style="display:inline-block;margin-left:7px;vertical-align:middle">{html.escape(label)}</span>'
        + "</span>"
    )


def _build_alert_summary(
    owner: dict,
    alerts: list[dict],
    *,
    include_ai_analysis: bool = True,
) -> dict:
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    owner_name = owner["name"]
    subject = (
        f"{ALERT_SUBJECT_PREFIX} - {len(alerts)} active alert{'s' if len(alerts) != 1 else ''}"
        f" need attention - {owner_name} - {today}"
    )
    ranked = sorted(
        alerts,
        key=lambda alert: (
            -(alert.get("triage_score") or 0),
            -(alert.get("impact_views_30d") or 0),
            -len(alert.get("report_links") or []),
            alert.get("degraded_since") or alert.get("created_at") or "9999",
            alert.get("asset_name") or "",
        ),
    )
    for alert in ranked:
        artifact_label, artifact_kind = _alert_artifact(alert)
        alert["artifact_label"] = artifact_label
        alert["artifact_kind"] = artifact_kind

    def next_action(alert: dict) -> str:
        assessment = alert.get("ai_assessment") or {}
        if include_ai_analysis and assessment.get("recommendation_title"):
            title = assessment["recommendation_title"]
            rationale = assessment.get("recommendation_rationale")
            return f"{title} — {rationale}" if rationale else title
        return alert.get("recommendation") or alert.get("triage_cta") or "Open the asset and investigate the issue."

    def ai_assessment_text(alert: dict) -> str | None:
        if not include_ai_analysis:
            return None
        assessment = alert.get("ai_assessment") or {}
        if not assessment:
            return (
                "Pending or unavailable; the deterministic Alert remains active "
                "and email delivery is not blocked."
            )
        label = str(assessment.get("assessment") or "uncertain").replace("_", " ").title()
        confidence = str(assessment.get("confidence") or "low").title()
        return f"{label} ({confidence} confidence): {assessment['conclusion']}"

    def ai_assessment_label(alert: dict) -> str:
        assessment = alert.get("ai_assessment") or {}
        provider_mode = assessment.get("provider_mode")
        if provider_mode == "qwen":
            return "Qwen assessment"
        if provider_mode == "mock":
            return "Deterministic preview"
        return "Automated assessment"

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

    def refresh_error(alert: dict) -> str | None:
        return alert.get("pbi_refresh_error") or (alert.get("report_detail") or {}).get("pbi_refresh_error")

    lines = [
        f"Hi {owner_name},",
        "",
        f"{len(ranked)} active alert{'s' if len(ranked) != 1 else ''} need attention as of {today}.",
        "They are ordered by views, failure risk, affected reports, and time degraded.",
        "",
        "Artifact | Issue and name | Degraded since | Views",
    ]
    for alert in ranked:
        artifact_label, _ = _alert_artifact(alert)
        issue_label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        degraded_since = _alert_degraded_since(alert.get("degraded_since") or alert.get("created_at"))
        views = int(alert.get("impact_views_30d") or 0)
        lines.append(f"{artifact_label} | {issue_label}: {asset_text(alert)} | {degraded_since} | {views:,}")
        if refresh_error(alert):
            lines.append(f"PBI Refresh Error: {refresh_error(alert)}")
        if ai_assessment_text(alert):
            lines.append(f"{ai_assessment_label(alert)}: {ai_assessment_text(alert)}")
        lines.append(f"Next action: {next_action(alert)}")
        lines.append("")
    lines.extend(["Thanks,", "Metronome"])
    body_text = "\n".join(lines)

    html_parts = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;line-height:1.45;color:#1f2937;max-width:920px">',
        f"<p>Hi {html.escape(owner_name)},</p>",
        f"<p><strong>{len(ranked)} active alert{'s' if len(ranked) != 1 else ''} need attention.</strong> They are ordered by views, failure risk, affected reports, and time degraded.</p>",
        '<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:14px">',
        '<tr style="background:#eef3f2;text-align:left">'
        + _th("Artifact") + _th("Issue and name") + _th("Degraded since") + _th("Views") + "</tr>",
    ]
    for alert in ranked:
        issue_label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        degraded_since = _alert_degraded_since(alert.get("degraded_since") or alert.get("created_at"))
        views = int(alert.get("impact_views_30d") or 0)
        error_html = ""
        if refresh_error(alert):
            error_html = (
                '<div style="margin-top:7px;padding:7px 8px;background:#fff2f1;color:#9f2f2f;'
                'border:1px solid #efd0ce;word-break:break-word">'
                f'<strong>PBI Refresh Error:</strong> {html.escape(refresh_error(alert))}</div>'
            )
        assessment_html = ""
        if ai_assessment_text(alert):
            provider_label = ai_assessment_label(alert)
            assessment_html = (
                '<div style="margin-top:7px;padding:7px 8px;background:#eef6ff;color:#244a70;'
                'border:1px solid #ccdff2;word-break:break-word">'
                f'<strong>{html.escape(provider_label)}:</strong> {html.escape(ai_assessment_text(alert))}</div>'
            )
        issue_html = (
            f'<strong>{html.escape(issue_label)}</strong><br>{asset_html(alert)}'
            + error_html
            + assessment_html
            + f'<div style="margin-top:7px;color:#4b5563"><strong>Next action:</strong> {html.escape(next_action(alert))}</div>'
        )
        html_parts.append(
            "<tr>"
            + _td(_artifact_cell_html(alert), "min-width:105px")
            + _td(issue_html, "min-width:300px")
            + _td(html.escape(degraded_since), "white-space:nowrap")
            + _td(f"{views:,}", "white-space:nowrap;text-align:right")
            + "</tr>"
        )
    html_parts.extend(["</table>", "<p>Thanks,<br>Metronome</p></div>"])

    return {
        "owner_name": owner_name,
        "email": owner.get("email"),
        "include_all_alerts": bool(owner.get("include_all_alerts")),
        "alert_count": len(alerts),
        "subject": subject,
        "body_text": body_text,
        "body_html": "".join(html_parts),
        "mailto": f"mailto:{quote(owner.get('email') or '')}?subject={quote(subject)}&body={quote(body_text)}",
        "alerts": ranked,
        "ai_analysis_enabled": include_ai_analysis,
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


def _load_alert_summaries(
    owner_names: set[str] | None = None,
    *,
    ai_settings=None,
) -> list[dict]:
    from app.ai.runtime_config import load_runtime_settings

    ai_settings = ai_settings or load_runtime_settings()
    include_ai_analysis = ai_settings.feature_enabled("alert_email_analysis")
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
                """SELECT id, name, powerbi_url, tmdl_path, pbi_last_refresh_at,
                          pbi_refresh_status, pbi_refresh_error
                   FROM reports"""
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

    ai_assessments = (
        _current_alert_ai_assessments([int(alert["id"]) for alert in alert_rows])
        if include_ai_analysis
        else {}
    )
    for alert in alert_rows:
        alert["ai_assessment"] = ai_assessments.get(int(alert["id"]))

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
            summaries.append(
                _build_alert_summary(
                    owner,
                    owner_alerts,
                    include_ai_analysis=include_ai_analysis,
                )
            )
    return summaries


def launch_outlook_dispatch(
    messages: list[dict],
    mode: str = "send",
    *,
    pipeline_run_id: int | None = None,
    purpose: str = "email",
) -> dict:
    """Launch one uniquely named interactive Outlook handoff.

    Launching the scheduled task is not delivery.  The PowerShell helper writes
    an atomic receipt only after Outlook's ``Send``/``Display`` call returns.
    """
    mode = (mode or "send").lower().strip()
    if mode not in {"draft", "send"}:
        raise OutlookEmailError("Mode must be draft or send", status_code=422)
    if platform.system() != "Windows":
        raise OutlookEmailError("Server-side Outlook sending is only available on Windows", status_code=400)
    if not OUTLOOK_SCRIPT.exists():
        raise OutlookEmailError(f"Outlook script not found: {OUTLOOK_SCRIPT}", status_code=404)
    if not messages:
        raise OutlookEmailError("No email messages to send", status_code=400)

    dispatch_token = uuid.uuid4().hex
    task_name = f"{TASK_NAME}_{dispatch_token}"
    payload = {"mode": mode, "messages": messages, "dispatch_token": dispatch_token}
    payload_path = _payload_path()
    receipt_path = payload_path.with_suffix(".receipt.json")
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO outlook_dispatches
                   (pipeline_run_id, purpose, task_name, payload_path, receipt_path,
                    status, message_count)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (
                pipeline_run_id, purpose, task_name, str(payload_path),
                str(receipt_path), len(messages),
            ),
        )
        dispatch_id = int(cursor.lastrowid)
    payload["dispatch_id"] = dispatch_id
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    ps_cmd = (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{OUTLOOK_SCRIPT}" '
        f'-PayloadPath "{payload_path}" -ReceiptPath "{receipt_path}"'
    )
    if mode == "send":
        ps_cmd += " -Send"

    try:
        subprocess.run(
            ["schtasks", "/create", "/tn", task_name, "/tr", ps_cmd, "/sc", "once", "/st", "00:00", "/it", "/f"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        subprocess.run(
            ["schtasks", "/run", "/tn", task_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        with get_db() as db:
            db.execute(
                "UPDATE outlook_dispatches SET status='failed', error=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc.stderr or exc)[:4000], dispatch_id),
            )
        raise OutlookEmailError(f"Failed to launch Outlook email task: {exc.stderr or exc}") from exc

    return {
        "id": dispatch_id, "task_name": task_name, "status": "pending",
        "message_count": len(messages),
    }


def _launch_outlook_payload(messages: list[dict], mode: str = "send") -> int:
    """Compatibility wrapper for existing alert/recurrence callers."""
    return int(launch_outlook_dispatch(messages, mode)["message_count"])


def _delete_outlook_task(task_name: str) -> bool:
    if platform.system() != "Windows":
        return True
    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return True
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not remove Outlook scheduled task %s", task_name, exc_info=True
        )
        return False


def reconcile_outlook_dispatches() -> dict:
    """Consume receipts and mark old unknown handoffs without retrying them."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    processed = 0
    unknown = 0
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM outlook_dispatches WHERE status='pending' ORDER BY id"
        ).fetchall()
    for row in rows:
        receipt_path = Path(row["receipt_path"])
        if receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
                if int(receipt.get("dispatch_id") or 0) != int(row["id"]):
                    raise ValueError("Receipt dispatch ID does not match.")
                status = "submitted" if receipt.get("status") == "submitted" else "failed"
                error = receipt.get("error")
            except Exception as exc:
                status = "unknown"
                error = f"Invalid Outlook receipt: {exc}"
            with get_db() as db:
                db.execute(
                    """UPDATE outlook_dispatches SET status=?, error=?,
                              submitted_at=CASE WHEN ?='submitted' THEN CURRENT_TIMESTAMP ELSE submitted_at END,
                              processed_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (status, str(error)[:4000] if error else None, status, row["id"]),
                )
            cleaned = _delete_outlook_task(row["task_name"])
            receipt_path.unlink(missing_ok=True)
            Path(row["payload_path"]).unlink(missing_ok=True)
            if cleaned:
                with get_db() as db:
                    db.execute(
                        "UPDATE outlook_dispatches SET cleanup_at=CURRENT_TIMESTAMP WHERE id=?",
                        (row["id"],),
                    )
            processed += 1
            continue
        if str(row["created_at"]) < cutoff:
            with get_db() as db:
                db.execute(
                    """UPDATE outlook_dispatches SET status='unknown',
                              error='No Outlook receipt arrived within 24 hours; not retried to avoid duplication.',
                              processed_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (row["id"],),
                )
            cleaned = _delete_outlook_task(row["task_name"])
            Path(row["payload_path"]).unlink(missing_ok=True)
            receipt_path.unlink(missing_ok=True)
            if cleaned:
                with get_db() as db:
                    db.execute(
                        "UPDATE outlook_dispatches SET cleanup_at=CURRENT_TIMESTAMP WHERE id=?",
                        (row["id"],),
                    )
            unknown += 1

    with get_db() as db:
        old_rows = db.execute(
            """SELECT id, task_name, payload_path, receipt_path FROM outlook_dispatches
               WHERE created_at < ? AND cleanup_at IS NULL""",
            (cutoff,),
        ).fetchall()
    for row in old_rows:
        cleaned = _delete_outlook_task(row["task_name"])
        Path(row["payload_path"]).unlink(missing_ok=True)
        Path(row["receipt_path"]).unlink(missing_ok=True)
        if cleaned:
            with get_db() as db:
                db.execute(
                    "UPDATE outlook_dispatches SET cleanup_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],),
                )

    root = _payload_path().parent
    oldest = datetime.now(timezone.utc) - timedelta(hours=24)
    for pattern in ("outlook-task-email-*.json", "outlook-task-email-*.receipt.json"):
        for candidate in root.glob(pattern):
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
                if modified < oldest:
                    candidate.unlink(missing_ok=True)
            except OSError:
                continue
    return {"processed": processed, "unknown": unknown}


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
    from app.ai.runtime_config import load_runtime_settings

    ai_settings = load_runtime_settings()
    enabled = ai_settings.feature_enabled("alert_email_analysis")
    return {
        "summaries": _load_alert_summaries(ai_settings=ai_settings),
        "ai_analysis": {
            "enabled": enabled,
            "state": "enabled" if enabled else "disabled",
            "reason": (
                None
                if enabled
                else "AI analysis is disabled in System > AI."
            ),
            "mode": ai_settings.mode,
        },
    }


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
