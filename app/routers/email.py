"""Outlook task summary email helpers."""

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


class PersonEmailUpdate(BaseModel):
    email: str | None = None


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
    folder = _folder_from_path(raw_path, source.get("type"))
    href = _file_href(folder)
    if not folder:
        return None
    return {
        "source_id": source["id"],
        "source_name": source["name"],
        "link_label": "Source URL" if _is_url(folder) else "Source Folder",
        "path": raw_path,
        "folder_path": folder,
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
    lines.append("Data Governance")
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
    html_parts.append("<p>Thanks,<br>Data Governance</p></div>")

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
    subject = f"Active alert summary - {owner_name} - {today}"

    lines = [
        f"Hi {owner_name},",
        "",
        f"Here are your active alerts as of {today}.",
        "",
    ]
    for alert in alerts:
        label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        days = alert.get("asset_days") or 0
        lines.append(f"- [{label}] {alert['asset_name']} ({days}d)")
        if alert.get("recommendation"):
            lines.append(f"  Fix: {alert['recommendation']}")
        report_links = [r for r in alert.get("report_links", []) if r.get("powerbi_url")]
        if report_links:
            lines.append("  Power BI: " + ", ".join(f"{r['report_name']} ({r['powerbi_url']})" for r in report_links))
        source_links = [s for s in alert.get("source_links", []) if s.get("folder_path")]
        if source_links:
            lines.append("  Source folders:")
            for src in source_links:
                lines.append(f"    - {src['source_name']}: {src['folder_path']}")
        if alert.get("notes"):
            lines.append(f"  Notes: {alert['notes']}")
    lines.extend(["", "Thanks,", "Data Governance"])
    body_text = "\n".join(lines)

    html_parts = [
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#202124;max-width:860px\">",
        f"<p>Hi {html.escape(owner_name)},</p>",
        f"<p>Here are your active alerts as of {html.escape(today)}.</p>",
        "<table style=\"width:100%;border-collapse:collapse;font-size:13px\">",
        "<tr style=\"background:#f3f4f6;text-align:left\">"
        "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Issue</th>"
        "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Asset</th>"
        "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Days</th>"
        "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Fix Links</th>"
        "<th style=\"padding:6px 8px;border:1px solid #d7dce2\">Recommendation</th>"
        "</tr>",
    ]
    for alert in alerts:
        report_links = [
            _html_link(f"Power BI: {r['report_name']}", r.get("powerbi_url"))
            for r in alert.get("report_links", [])
            if r.get("powerbi_url")
        ]
        source_links = [
            _html_link(f"{s.get('link_label') or 'Source Folder'}: {s['source_name']}", s.get("href"))
            for s in alert.get("source_links", [])
            if s.get("folder_path")
        ]
        fix_links = "<br>".join(report_links + source_links) or "-"
        label = ACTION_TYPE_LABELS.get(alert["type"], alert["type"])
        html_parts.append(
            "<tr>"
            f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\">{html.escape(label)}</td>"
            f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\"><strong>{html.escape(alert['asset_name'])}</strong></td>"
            f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\">{alert.get('asset_days') or 0}d</td>"
            f"<td style=\"padding:6px 8px;border:1px solid #d7dce2\">{fix_links}</td>"
            f"<td style=\"padding:6px 8px;border:1px solid #d7dce2;color:#4b5563\">{html.escape(alert.get('recommendation') or '-')}</td>"
            "</tr>"
        )
    html_parts.append("</table><p>Thanks,<br>Data Governance</p></div>")

    return {
        "owner_name": owner_name,
        "email": owner.get("email"),
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
            "SELECT id, name, role, email, created_at FROM people WHERE role = 'BI' ORDER BY name"
        ).fetchall()
        if owner_names:
            people = [p for p in people if p["name"] in owner_names]
        owners = {p["name"]: dict(p) for p in people}
        if not owners:
            return []

        reports = {
            r["id"]: dict(r)
            for r in db.execute("SELECT id, name, powerbi_url, tmdl_path FROM reports").fetchall()
        }
        sources = {
            s["id"]: dict(s)
            for s in db.execute("SELECT id, name, type, connection_info, source_query FROM sources").fetchall()
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

    alert_rows = [
        a.model_dump()
        for a in list_action_alerts()
        if a.status not in ("resolved", "expected")
        and a.assigned_to in owners
    ]
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
            link = _source_link(sources[source_id])
            if link:
                source_links.append(link)
            for report in reports_by_source.get(source_id, []):
                link = _report_link(report)
                if link:
                    report_links.append(link)

        for detail in alert.get("detail_items") or []:
            detail_source = sources.get(detail.get("id"))
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
        alerts_by_owner[alert["assigned_to"]].append(alert)

    summaries = []
    for owner_name, owner in owners.items():
        owner_alerts = sorted(
            alerts_by_owner.get(owner_name) or [],
            key=lambda a: (-(a.get("asset_days") or 0), a.get("asset_name") or ""),
        )
        if owner_alerts:
            summaries.append(_build_alert_summary(owner, owner_alerts))
    return summaries


def _launch_outlook_messages(messages: list[dict], mode: str, event_name: str, request: Request) -> dict:
    if platform.system() != "Windows":
        raise HTTPException(status_code=400, detail="Server-side Outlook sending is only available on Windows")
    if not OUTLOOK_SCRIPT.exists():
        raise HTTPException(status_code=404, detail=f"Outlook script not found: {OUTLOOK_SCRIPT}")
    if not messages:
        raise HTTPException(status_code=400, detail="No email messages to send")

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
        raise HTTPException(status_code=500, detail=f"Failed to launch Outlook email task: {exc.stderr or exc}")

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
    return {"status": "launched", "mode": mode, "count": len(messages)}


@router.get("/people")
def list_people_email_status():
    """Return people with email mapping and pending task counts."""
    with get_db() as db:
        rows = db.execute(
            """SELECT p.id, p.name, p.role, p.email, p.created_at,
                      SUM(CASE WHEN t.id IS NOT NULL AND t.status != 'done' THEN 1 ELSE 0 END) AS pending_task_count
               FROM people p
               LEFT JOIN tasks t ON t.assigned_to = p.name
               GROUP BY p.id, p.name, p.role, p.email, p.created_at
               ORDER BY CASE p.role WHEN 'BI' THEN 0 ELSE 1 END, p.name"""
        ).fetchall()
    alert_counts: dict[str, int] = {}
    for action in list_action_alerts():
        if action.status in ("resolved", "expected") or not action.assigned_to:
            continue
        alert_counts[action.assigned_to] = alert_counts.get(action.assigned_to, 0) + 1
    result = []
    for row in rows:
        item = dict(row)
        item["pending_alert_count"] = alert_counts.get(item["name"], 0)
        result.append(item)
    return result


@router.patch("/people/{person_id}")
def update_person_email(person_id: int, body: PersonEmailUpdate, request: Request):
    """Update the Outlook email address linked to a person."""
    email = _normalize_email(body.email)
    with get_db() as db:
        row = db.execute("SELECT id, name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        db.execute("UPDATE people SET email = ? WHERE id = ?", (email, person_id))
        log_event(db, "person", person_id, row["name"], "email_updated", email or "cleared", get_actor(request))
    return {"status": "updated", "id": person_id, "email": email}


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
