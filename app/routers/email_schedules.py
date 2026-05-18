"""Email schedule API and dispatcher for task summary emails."""

import calendar
import html
import os
import smtplib
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.models import EmailScheduleOut, EmailScheduleUpdate
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/email-schedules", tags=["email-schedules"])

TASK_SUMMARY_KEY = "task_summary"
DEFAULT_SUBJECT = "Task Board Summary"
_RECURRENCES = {"daily", "weekly", "monthly"}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_STATUS_LABELS = {
    "backlog": "Backlog",
    "todo": "To Do",
    "in_progress": "In Progress",
    "review": "Review",
}
_ENTITY_TABLES = {
    "report": ("reports", "name"),
    "source": ("sources", "name"),
    "script": ("scripts", "display_name"),
    "upstream_system": ("upstream_systems", "name"),
    "scheduled_task": ("scheduled_tasks", "task_name"),
}
_ENTITY_LABELS = {
    "report": "Report",
    "source": "Source",
    "script": "Script",
    "upstream_system": "Upstream",
    "scheduled_task": "Scheduled Task",
}


def _looks_like_windows_drive(value: str) -> bool:
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _email_href(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    lower = raw.lower()
    if lower.startswith(("http://", "https://", "mailto:")):
        return raw
    if lower.startswith("file://"):
        return raw
    if raw.startswith("\\\\"):
        return "file:" + raw.replace("\\", "/")
    if _looks_like_windows_drive(raw):
        return "file:///" + raw.replace("\\", "/")
    if raw.startswith("/"):
        return "file://" + raw
    return None


def _html_link(label: str, href: str | None) -> str:
    label_html = html.escape(label or "-")
    if not href:
        return label_html
    return (
        f'<a href="{html.escape(href, quote=True)}" target="_blank" '
        f'rel="noopener" style="color:#2563eb;text-decoration:none">{label_html}</a>'
    )


def _html_link_items(items: list[dict]) -> str:
    if not items:
        return '<span style="color:#999">-</span>'
    parts = []
    for item in items:
        name = _html_link(item["name"], item.get("href"))
        path = item.get("path")
        path_html = ""
        if path:
            path_href = _email_href(path)
            path_label = _html_link(path, path_href)
            path_html = (
                '<div style="margin-top:2px;color:#777;font-size:11px;'
                f'word-break:break-all">Path: {path_label}</div>'
            )
        parts.append(f'<div style="margin-bottom:6px"><strong>{name}</strong>{path_html}</div>')
    return "".join(parts)


def _text_link_items(items: list[dict]) -> str:
    if not items:
        return "-"
    chunks = []
    for item in items:
        chunk = item["name"]
        if item.get("href"):
            chunk += f" <{item['href']}>"
        if item.get("path"):
            chunk += f" path: {item['path']}"
        chunks.append(chunk)
    return "; ".join(chunks)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _parse_send_time(value: str | None) -> tuple[int, int]:
    raw = (value or "09:00").strip()
    parts = raw.split(":")
    if len(parts) < 2:
        raise ValueError("send_time must be HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("send_time must be HH:MM")
    return hour, minute


def _normalize_weekdays(values: list[str] | str | None) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace(";", ",").split(",")
    else:
        raw_items = values or []
    seen = []
    for item in raw_items:
        key = str(item).strip().lower()
        if key in _WEEKDAYS and key not in seen:
            seen.append(key)
    return seen


def _next_month(year: int, month: int) -> tuple[int, int]:
    month += 1
    if month > 12:
        return year + 1, 1
    return year, month


def _clamped_month_date(year: int, month: int, requested_day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(max(requested_day, 1), last_day))


def _calculate_next_run(
    recurrence: str,
    send_time: str | None,
    weekdays: list[str] | str | None,
    month_day: int | None,
    now: datetime | None = None,
) -> datetime:
    current = now or _now()
    hour, minute = _parse_send_time(send_time)
    recurrence = (recurrence or "weekly").lower()
    if recurrence not in _RECURRENCES:
        raise ValueError("recurrence must be daily, weekly, or monthly")

    if recurrence == "daily":
        candidate = datetime.combine(current.date(), time(hour, minute))
        return candidate if candidate > current else candidate + timedelta(days=1)

    if recurrence == "monthly":
        requested_day = month_day or 1
        if requested_day < 1 or requested_day > 31:
            raise ValueError("month_day must be between 1 and 31")
        year, month = current.year, current.month
        for _ in range(14):
            candidate = datetime.combine(_clamped_month_date(year, month, requested_day), time(hour, minute))
            if candidate > current:
                return candidate
            year, month = _next_month(year, month)
        return current + timedelta(days=31)

    selected = _normalize_weekdays(weekdays) or ["monday"]
    selected_numbers = {_WEEKDAYS[d] for d in selected}
    for offset in range(14):
        day = current.date() + timedelta(days=offset)
        if day.weekday() not in selected_numbers:
            continue
        candidate = datetime.combine(day, time(hour, minute))
        if candidate > current:
            return candidate
    return current + timedelta(days=7)


def _row_to_out(row) -> EmailScheduleOut:
    return EmailScheduleOut(
        id=row["id"],
        schedule_key=row["schedule_key"],
        enabled=bool(row["enabled"]),
        recurrence=row["recurrence"] or "weekly",
        weekdays=_normalize_weekdays(row["weekdays"]),
        month_day=row["month_day"],
        send_time=row["send_time"] or "09:00",
        recipients=row["recipients"],
        subject=row["subject"],
        last_sent_at=row["last_sent_at"],
        next_run_at=row["next_run_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _ensure_task_summary_schedule(db):
    row = db.execute(
        "SELECT * FROM email_schedules WHERE schedule_key = ?",
        (TASK_SUMMARY_KEY,),
    ).fetchone()
    if row:
        return row

    next_run = _calculate_next_run("weekly", "09:00", ["monday"], None)
    db.execute(
        """INSERT INTO email_schedules
           (schedule_key, enabled, recurrence, weekdays, send_time, subject, next_run_at)
           VALUES (?, 0, 'weekly', 'monday', '09:00', ?, ?)""",
        (TASK_SUMMARY_KEY, DEFAULT_SUBJECT, _iso(next_run)),
    )
    return db.execute(
        "SELECT * FROM email_schedules WHERE schedule_key = ?",
        (TASK_SUMMARY_KEY,),
    ).fetchone()


def _parse_recipients(raw: str | None) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace(";", "\n").replace(",", "\n")
    return [part.strip() for part in normalized.splitlines() if part.strip()]


def _smtp_config() -> dict:
    host = os.environ.get("DG_SMTP_HOST") or os.environ.get("SMTP_HOST")
    sender = os.environ.get("DG_SMTP_FROM") or os.environ.get("SMTP_FROM") or os.environ.get("DG_SMTP_USER") or os.environ.get("SMTP_USER")
    if not host or not sender:
        raise RuntimeError("SMTP is not configured. Set DG_SMTP_HOST and DG_SMTP_FROM.")
    port = int(os.environ.get("DG_SMTP_PORT") or os.environ.get("SMTP_PORT") or "587")
    return {
        "host": host,
        "port": port,
        "user": os.environ.get("DG_SMTP_USER") or os.environ.get("SMTP_USER"),
        "password": os.environ.get("DG_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD"),
        "sender": sender,
        "use_tls": (os.environ.get("DG_SMTP_TLS") or "true").lower() in ("1", "true", "yes"),
        "use_ssl": (os.environ.get("DG_SMTP_SSL") or "false").lower() in ("1", "true", "yes"),
    }


def _task_link_columns(db, task_id: int) -> dict[str, list[dict]]:
    rows = db.execute(
        "SELECT entity_type, entity_id FROM task_links WHERE task_id = ? ORDER BY created_at",
        (task_id,),
    ).fetchall()
    result = {"reports": [], "sources": [], "other": []}
    for row in rows:
        entity_type = row["entity_type"]
        entity_id = row["entity_id"]
        if entity_type == "report":
            found = db.execute(
                "SELECT name, powerbi_url, tmdl_path FROM reports WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if found:
                path = found["tmdl_path"]
                href = found["powerbi_url"] or _email_href(path)
                result["reports"].append({"name": found["name"], "href": href, "path": path})
            continue
        if entity_type == "source":
            found = db.execute(
                "SELECT name, connection_info, source_query FROM sources WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if found:
                path = found["connection_info"] or found["source_query"]
                result["sources"].append({"name": found["name"], "href": _email_href(path), "path": path})
            continue

        table_info = _ENTITY_TABLES.get(entity_type)
        name = None
        path = None
        if table_info:
            found = db.execute(
                f"SELECT {table_info[1]} FROM {table_info[0]} WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if found:
                name = found[0]
            if entity_type == "script":
                detail = db.execute("SELECT path FROM scripts WHERE id = ?", (entity_id,)).fetchone()
                path = detail["path"] if detail else None
            elif entity_type == "scheduled_task":
                detail = db.execute(
                    "SELECT task_path, action_command FROM scheduled_tasks WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                if detail:
                    path = detail["task_path"] or detail["action_command"]
        label = _ENTITY_LABELS.get(entity_type, entity_type)
        result["other"].append({
            "name": f"{label}: {name or 'ID ' + str(entity_id)}",
            "href": _email_href(path),
            "path": path,
        })
    return result


def _build_task_summary_email() -> tuple[str, str]:
    with get_db() as db:
        rows = db.execute("""
            SELECT id, title, description, status, priority
            FROM tasks
            WHERE status != 'done'
            ORDER BY CASE status
                WHEN 'backlog' THEN 0 WHEN 'todo' THEN 1
                WHEN 'in_progress' THEN 2 WHEN 'review' THEN 3 ELSE 4 END,
                position, created_at DESC
        """).fetchall()

        tasks = []
        for row in rows:
            item = dict(row)
            item.update(_task_link_columns(db, row["id"]))
            tasks.append(item)

    today = _now().strftime("%d %b %Y")
    grouped: dict[str, list[dict]] = {key: [] for key in _STATUS_LABELS}
    for task in tasks:
        grouped.setdefault(task["status"], []).append(task)

    html_parts = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:1100px">',
        '<h2 style="margin:0 0 4px;font-size:16px">Task Board Summary</h2>',
        f'<p style="margin:0 0 16px;color:#666;font-size:13px">{html.escape(today)} - {len(tasks)} active task{"s" if len(tasks) != 1 else ""}</p>',
    ]
    text_parts = [f"TASK BOARD SUMMARY\n{today}\n"]

    for status, label in _STATUS_LABELS.items():
        items = grouped.get(status, [])
        if not items:
            continue
        html_parts.append(
            f'<h3 style="margin:16px 0 6px;font-size:14px;color:#555;border-bottom:1px solid #ddd;padding-bottom:4px">{html.escape(label)} ({len(items)})</h3>'
        )
        html_parts.append(
            '<table style="width:100%;border-collapse:collapse;font-size:13px">'
            '<tr style="background:#f5f5f5;text-align:left">'
            '<th style="padding:5px 8px;border:1px solid #ddd">Task</th>'
            '<th style="padding:5px 8px;border:1px solid #ddd;width:70px">Priority</th>'
            '<th style="padding:5px 8px;border:1px solid #ddd;width:190px">Reports</th>'
            '<th style="padding:5px 8px;border:1px solid #ddd;width:230px">Sources</th>'
            '<th style="padding:5px 8px;border:1px solid #ddd;width:170px">Other Links</th>'
            '</tr>'
        )
        text_parts.append(f"\n--- {label.upper()} ({len(items)}) ---")
        for task in items:
            priority = task["priority"] or "-"
            priority_color = "#e74c3c" if priority == "high" else "#999" if priority == "low" else "#333"
            desc = f'<br><span style="color:#888;font-size:12px">{html.escape(task["description"])}</span>' if task["description"] else ""
            html_parts.append(
                "<tr>"
                f'<td style="padding:5px 8px;border:1px solid #ddd"><strong>{html.escape(task["title"])}</strong>{desc}</td>'
                f'<td style="padding:5px 8px;border:1px solid #ddd;color:{priority_color}">{html.escape(priority)}</td>'
                f'<td style="padding:5px 8px;border:1px solid #ddd;font-size:12px;color:#333">{_html_link_items(task["reports"])}</td>'
                f'<td style="padding:5px 8px;border:1px solid #ddd;font-size:12px;color:#333">{_html_link_items(task["sources"])}</td>'
                f'<td style="padding:5px 8px;border:1px solid #ddd;font-size:12px;color:#333">{_html_link_items(task["other"])}</td>'
                "</tr>"
            )
            line = f"[{priority.upper()}] {task['title']}"
            reports = _text_link_items(task["reports"])
            sources = _text_link_items(task["sources"])
            other = _text_link_items(task["other"])
            if reports != "-":
                line += f" | Reports: {reports}"
            if sources != "-":
                line += f" | Sources: {sources}"
            if other != "-":
                line += f" | Other: {other}"
            text_parts.append(line)
        html_parts.append("</table>")

    html_parts.append("</div>")
    return "".join(html_parts), "\n".join(text_parts) + "\n"


def _send_task_summary(schedule: dict) -> int:
    recipients = _parse_recipients(schedule.get("recipients"))
    if not recipients:
        raise RuntimeError("Add at least one recipient before sending.")

    cfg = _smtp_config()
    html_body, text_body = _build_task_summary_email()
    msg = EmailMessage()
    msg["Subject"] = schedule.get("subject") or DEFAULT_SUBJECT
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if cfg["use_ssl"]:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20) as smtp:
            if cfg["user"] and cfg["password"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as smtp:
            if cfg["use_tls"]:
                smtp.starttls()
            if cfg["user"] and cfg["password"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    return len(recipients)


def _schedule_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _mark_result(schedule_id: int, schedule: dict, error: str | None) -> None:
    next_run = _calculate_next_run(
        schedule.get("recurrence") or "weekly",
        schedule.get("send_time") or "09:00",
        _normalize_weekdays(schedule.get("weekdays")),
        schedule.get("month_day"),
        _now() + timedelta(seconds=1),
    )
    now_iso = _iso(_now())
    with get_db() as db:
        if error:
            db.execute(
                """UPDATE email_schedules
                   SET next_run_at = ?, last_error = ?, updated_at = ?
                   WHERE id = ?""",
                (_iso(next_run), error[:500], now_iso, schedule_id),
            )
        else:
            db.execute(
                """UPDATE email_schedules
                   SET last_sent_at = ?, next_run_at = ?, last_error = NULL, updated_at = ?
                   WHERE id = ?""",
                (now_iso, _iso(next_run), now_iso, schedule_id),
            )


@router.get("/task-summary", response_model=EmailScheduleOut)
def get_task_summary_schedule():
    with get_db() as db:
        row = _ensure_task_summary_schedule(db)
        return _row_to_out(row)


@router.put("/task-summary", response_model=EmailScheduleOut)
def update_task_summary_schedule(body: EmailScheduleUpdate, request: Request):
    recurrence = (body.recurrence or "weekly").lower()
    if recurrence not in _RECURRENCES:
        raise HTTPException(status_code=400, detail="recurrence must be daily, weekly, or monthly")
    weekdays = _normalize_weekdays(body.weekdays)
    if recurrence == "weekly" and not weekdays:
        weekdays = ["monday"]
    month_day = body.month_day if recurrence == "monthly" else None
    if recurrence == "monthly" and (month_day is None or month_day < 1 or month_day > 31):
        raise HTTPException(status_code=400, detail="month_day must be between 1 and 31")
    try:
        next_run = _calculate_next_run(recurrence, body.send_time, weekdays, month_day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    subject = (body.subject or DEFAULT_SUBJECT).strip() or DEFAULT_SUBJECT
    now_iso = _iso(_now())
    with get_db() as db:
        row = _ensure_task_summary_schedule(db)
        db.execute(
            """UPDATE email_schedules
               SET enabled = ?, recurrence = ?, weekdays = ?, month_day = ?,
                   send_time = ?, recipients = ?, subject = ?,
                   next_run_at = ?, last_error = NULL, updated_at = ?
               WHERE id = ?""",
            (
                int(body.enabled),
                recurrence,
                ",".join(weekdays),
                month_day,
                body.send_time,
                body.recipients,
                subject,
                _iso(next_run),
                now_iso,
                row["id"],
            ),
        )
        log_event(db, "email_schedule", row["id"], "Task summary", "updated", actor=get_actor(request))
        updated = db.execute("SELECT * FROM email_schedules WHERE id = ?", (row["id"],)).fetchone()
        return _row_to_out(updated)


@router.post("/task-summary/send-now", response_model=EmailScheduleOut)
def send_task_summary_now(request: Request):
    with get_db() as db:
        row = _ensure_task_summary_schedule(db)
        schedule = _schedule_dict(row)

    try:
        count = _send_task_summary(schedule)
    except RuntimeError as exc:
        _mark_result(schedule["id"], schedule, str(exc))
        with get_db() as db:
            log_event(db, "email_schedule", schedule["id"], "Task summary", "send_failed", str(exc), get_actor(request))
            updated = db.execute("SELECT * FROM email_schedules WHERE id = ?", (schedule["id"],)).fetchone()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"Email send failed: {exc}"
        _mark_result(schedule["id"], schedule, detail)
        with get_db() as db:
            log_event(db, "email_schedule", schedule["id"], "Task summary", "send_failed", detail, get_actor(request))
            updated = db.execute("SELECT * FROM email_schedules WHERE id = ?", (schedule["id"],)).fetchone()
        raise HTTPException(status_code=500, detail=detail) from exc

    _mark_result(schedule["id"], schedule, None)
    with get_db() as db:
        log_event(db, "email_schedule", schedule["id"], "Task summary", "sent", f"recipients={count}", get_actor(request))
        updated = db.execute("SELECT * FROM email_schedules WHERE id = ?", (schedule["id"],)).fetchone()
    return _row_to_out(updated)


def dispatch_due_email_schedules() -> int:
    """Send due email schedules. Called by the app scheduler."""
    now_iso = _iso(_now())
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM email_schedules
               WHERE enabled = 1
                 AND next_run_at IS NOT NULL
                 AND next_run_at <= ?
               ORDER BY next_run_at""",
            (now_iso,),
        ).fetchall()
        schedules = [_schedule_dict(row) for row in rows]

    sent = 0
    for schedule in schedules:
        if schedule.get("schedule_key") != TASK_SUMMARY_KEY:
            continue
        try:
            count = _send_task_summary(schedule)
            _mark_result(schedule["id"], schedule, None)
            with get_db() as db:
                log_event(db, "email_schedule", schedule["id"], "Task summary", "sent", f"recipients={count}", "scheduler")
            sent += 1
        except Exception as exc:
            _mark_result(schedule["id"], schedule, str(exc))
            with get_db() as db:
                log_event(db, "email_schedule", schedule["id"], "Task summary", "send_failed", str(exc), "scheduler")
    return sent
