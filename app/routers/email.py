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
from app.routers.eventlog import get_actor, log_event
from app.routers.tasks import _get_links

router = APIRouter(prefix="/api/email", tags=["email"])

OUTLOOK_SCRIPT = BASE_DIR / "tools" / "outlook_task_email.ps1"
TASK_NAME = "DG_Outlook_Task_Email"

STATUS_LABELS = {
    "backlog": "Backlog",
    "todo": "To Do",
    "in_progress": "In Progress",
    "review": "Review",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


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


def _build_summary(owner: dict, tasks: list[dict]) -> dict:
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
    for status, label in STATUS_LABELS.items():
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
    for status, label in STATUS_LABELS.items():
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
            summaries.append(_build_summary(owner, owner_tasks))
    return summaries


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
    return [dict(r) for r in rows]


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


@router.post("/send-task-summaries")
def send_task_summaries(body: SendSummariesRequest, request: Request):
    """Create drafts or send pending task summaries through Outlook on the app host."""
    mode = body.mode.lower().strip()
    if mode not in {"draft", "send"}:
        raise HTTPException(status_code=422, detail="Mode must be draft or send")
    if platform.system() != "Windows":
        raise HTTPException(status_code=400, detail="Server-side Outlook sending is only available on Windows")
    if not OUTLOOK_SCRIPT.exists():
        raise HTTPException(status_code=404, detail=f"Outlook script not found: {OUTLOOK_SCRIPT}")

    requested = set(body.owner_names or []) or None
    summaries = [s for s in _load_task_summaries(requested) if s.get("email")]
    if not summaries:
        raise HTTPException(status_code=400, detail="No owners with both pending tasks and email addresses")

    messages = [
        {"to": s["email"], "subject": s["subject"], "html_body": s["body_html"]}
        for s in summaries
    ]
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
            "task_summaries",
            "sent" if mode == "send" else "drafted",
            f"{len(messages)} owner summaries",
            get_actor(request),
        )
    return {"status": "launched", "mode": mode, "count": len(messages)}
