"""Email schedule API and dispatcher for owner alert summaries."""

import calendar
import html
from datetime import date, datetime, timezone, time, timedelta

from fastapi import APIRouter, HTTPException, Request

from app.config import (
    EMAIL_MAX_PBI_SYNC_AGE_HOURS,
    EMAIL_PBI_STALE_RETRY_MINUTES,
    EMAIL_PBI_SYNC_GRACE_MINUTES,
    EMAIL_REQUIRE_FRESH_PBI,
)
from app.database import get_db
from app.models import EmailScheduleOut, EmailScheduleUpdate
from app.routers.eventlog import get_actor, log_event
from app.scanner.pbi_sync import pbi_sync_freshness
from app.settings import get_overall_refresh_time

router = APIRouter(prefix="/api/email-schedules", tags=["email-schedules"])

TASK_SUMMARY_KEY = "task_summary"
PERSON_SCHEDULE_PREFIX = "person:"
DEFAULT_SUBJECT = "Task Board Summary"
PERSON_DEFAULT_SUBJECT = "Scheduled Email Summary"
_RECURRENCES = {"daily", "weekly", "monthly", "weekdays"}
_PERSON_RECURRENCES = {"daily", "weekdays"}
_PERSON_CONTENT_TYPES = {"alerts"}
_PERSON_DEFAULT_CONTENT_TYPES = ["alerts"]
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_BUSINESS_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
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


def _normalize_content_types(values: list[str] | str | None) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace(";", ",").split(",")
    else:
        raw_items = values or []
    seen = []
    for item in raw_items:
        key = str(item).strip().lower()
        if key in _PERSON_CONTENT_TYPES and key not in seen:
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
        raise ValueError("recurrence must be daily, weekly, weekdays, or monthly")

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

    selected = _BUSINESS_WEEKDAYS if recurrence == "weekdays" else (_normalize_weekdays(weekdays) or ["monday"])
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
    keys = set(row.keys())
    return EmailScheduleOut(
        id=row["id"],
        schedule_key=row["schedule_key"],
        person_id=row["person_id"] if "person_id" in keys else None,
        person_name=row["person_name"] if "person_name" in keys else None,
        person_email=row["person_email"] if "person_email" in keys else None,
        content_types=_normalize_content_types(row["content_types"] if "content_types" in keys else None),
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


def _person_schedule_key(person_id: int) -> str:
    return f"{PERSON_SCHEDULE_PREFIX}{person_id}"


def _select_schedule_with_person(db, schedule_id: int | None = None, schedule_key: str | None = None):
    where = "es.id = ?"
    value = schedule_id
    if schedule_key is not None:
        where = "es.schedule_key = ?"
        value = schedule_key
    return db.execute(
        f"""SELECT es.*, p.name AS person_name, p.email AS person_email
            FROM email_schedules es
            LEFT JOIN people p ON p.id = es.person_id
            WHERE {where}""",
        (value,),
    ).fetchone()


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


def _get_bi_person(db, person_id: int):
    person = db.execute(
        "SELECT id, name, role, email, created_at FROM people WHERE id = ?",
        (person_id,),
    ).fetchone()
    if not person or person["role"] != "BI":
        raise HTTPException(status_code=404, detail="BI profile not found")
    return person


def _ensure_person_schedule(db, person_id: int):
    person = _get_bi_person(db, person_id)
    schedule_key = _person_schedule_key(person_id)
    row = _select_schedule_with_person(db, schedule_key=schedule_key)
    if row:
        if row["person_id"] is None:
            db.execute("UPDATE email_schedules SET person_id = ? WHERE id = ?", (person_id, row["id"]))
            row = _select_schedule_with_person(db, schedule_id=row["id"])
        return row

    next_run = _calculate_next_run("weekdays", "09:00", _BUSINESS_WEEKDAYS, None)
    db.execute(
        """INSERT INTO email_schedules
           (schedule_key, person_id, content_types, enabled, recurrence, weekdays,
            send_time, subject, next_run_at)
           VALUES (?, ?, ?, 0, 'weekdays', ?, '09:00', ?, ?)""",
        (
            schedule_key,
            person["id"],
            ",".join(_PERSON_DEFAULT_CONTENT_TYPES),
            ",".join(_BUSINESS_WEEKDAYS),
            PERSON_DEFAULT_SUBJECT,
            _iso(next_run),
        ),
    )
    return _select_schedule_with_person(db, schedule_key=schedule_key)


def _parse_recipients(raw: str | None) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace(";", "\n").replace(",", "\n")
    return [part.strip() for part in normalized.splitlines() if part.strip()]


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


def _send_outlook_email(recipients: list[str], subject: str, html_body: str) -> int:
    if not recipients:
        raise RuntimeError("Add at least one recipient before sending.")

    from app.routers.email import _launch_outlook_payload

    _launch_outlook_payload(
        [{"to": "; ".join(recipients), "subject": subject, "html_body": html_body}],
        "send",
    )
    return len(recipients)


def _send_task_summary(schedule: dict) -> int:
    recipients = _parse_recipients(schedule.get("recipients"))
    html_body, _text_body = _build_task_summary_email()
    return _send_outlook_email(recipients, schedule.get("subject") or DEFAULT_SUBJECT, html_body)


def _pick_owner_summary(summaries: list[dict], owner_name: str) -> dict | None:
    return next((summary for summary in summaries if summary.get("owner_name") == owner_name), None)


def _summary_section(title: str, summary: dict) -> tuple[str, str]:
    html_body = summary.get("body_html") or ""
    text_body = summary.get("body_text") or ""
    return (
        f'<h3 style="margin:18px 0 8px;font-size:15px;color:#374151">{html.escape(title)}</h3>{html_body}',
        f"\n\n--- {title.upper()} ---\n{text_body}".rstrip(),
    )


def _build_person_schedule_email(person: dict, content_types: list[str]) -> tuple[str, str, str]:
    from app.routers.email import _load_alert_summaries

    owner_name = person["name"]
    alert_summary = _pick_owner_summary(_load_alert_summaries({owner_name}), owner_name)
    if not alert_summary:
        raise RuntimeError("No active alerts are available for this BI profile.")
    return (
        alert_summary.get("subject") or f"Active alerts - {owner_name}",
        alert_summary.get("body_html") or "",
        alert_summary.get("body_text") or "",
    )


def _send_person_summary(schedule: dict) -> int:
    person_id = schedule.get("person_id")
    if not person_id:
        raise RuntimeError("Schedule is not linked to a BI profile.")
    with get_db() as db:
        person = _get_bi_person(db, int(person_id))
        person = dict(person)
    email = (person.get("email") or "").strip()
    if not email:
        raise RuntimeError("Map an email address before enabling this schedule.")
    content_types = _normalize_content_types(schedule.get("content_types")) or _PERSON_DEFAULT_CONTENT_TYPES
    subject, html_body, _text_body = _build_person_schedule_email(person, content_types)
    return _send_outlook_email([email], subject, html_body)


class StalePbiSyncError(RuntimeError):
    pass


def _parse_aware_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def _required_pbi_sync_after() -> datetime | None:
    now_local = datetime.now().astimezone()
    refresh_time = get_overall_refresh_time()
    expected_local = datetime.combine(
        now_local.date(),
        time(refresh_time["hour"], refresh_time["minute"]),
        tzinfo=now_local.tzinfo,
    )
    if now_local < expected_local + timedelta(minutes=EMAIL_PBI_SYNC_GRACE_MINUTES):
        return None
    return expected_local.astimezone(timezone.utc)


def _require_fresh_pbi_for_scheduled_email(schedule: dict) -> None:
    if not EMAIL_REQUIRE_FRESH_PBI:
        return
    content_types = _normalize_content_types(schedule.get("content_types"))
    schedule_key = schedule.get("schedule_key") or ""
    needs_pbi_guard = schedule_key == TASK_SUMMARY_KEY or not content_types or "alerts" in content_types
    if not needs_pbi_guard:
        return
    freshness = pbi_sync_freshness(EMAIL_MAX_PBI_SYNC_AGE_HOURS, "refresh")
    if freshness.get("fresh"):
        required_after = _required_pbi_sync_after()
        if not required_after:
            return
        latest_success = freshness.get("latest_success") or {}
        finished_at = _parse_aware_dt(latest_success.get("finished_at"))
        if finished_at and finished_at >= required_after:
            return
        raise StalePbiSyncError(
            "Skipped scheduled email because today's Power BI sync has not completed yet."
        )
    latest_attempt = freshness.get("latest_attempt") or {}
    attempt_status = latest_attempt.get("status")
    attempt_time = latest_attempt.get("finished_at") or latest_attempt.get("started_at")
    reason = freshness.get("reason") or "Power BI sync is not fresh."
    if attempt_status:
        reason += f" Latest attempt: {attempt_status}"
        if attempt_time:
            reason += f" at {attempt_time}"
        if latest_attempt.get("message"):
            reason += f" ({latest_attempt['message']})"
    raise StalePbiSyncError(f"Skipped scheduled email because {reason}")


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


def _defer_result(schedule_id: int, error: str) -> None:
    retry_at = _now() + timedelta(minutes=EMAIL_PBI_STALE_RETRY_MINUTES)
    now_iso = _iso(_now())
    with get_db() as db:
        db.execute(
            """UPDATE email_schedules
               SET next_run_at = ?, last_error = ?, updated_at = ?
               WHERE id = ?""",
            (_iso(retry_at), error[:500], now_iso, schedule_id),
        )


@router.get("/task-summary", response_model=EmailScheduleOut)
def get_task_summary_schedule():
    with get_db() as db:
        row = _ensure_task_summary_schedule(db)
        return _row_to_out(row)


@router.get("/people", response_model=list[EmailScheduleOut])
def get_person_email_schedules():
    with get_db() as db:
        people = db.execute(
            "SELECT id FROM people WHERE role = 'BI' ORDER BY name"
        ).fetchall()
        rows = [_ensure_person_schedule(db, person["id"]) for person in people]
        return [_row_to_out(row) for row in rows]


@router.get("/people/{person_id}", response_model=EmailScheduleOut)
def get_person_email_schedule(person_id: int):
    with get_db() as db:
        row = _ensure_person_schedule(db, person_id)
        return _row_to_out(row)


@router.put("/people/{person_id}", response_model=EmailScheduleOut)
def update_person_email_schedule(person_id: int, body: EmailScheduleUpdate, request: Request):
    recurrence = (body.recurrence or "weekdays").lower()
    if recurrence not in _PERSON_RECURRENCES:
        raise HTTPException(status_code=400, detail="recurrence must be daily or weekdays")
    content_types = _normalize_content_types(body.content_types)
    if not content_types:
        raise HTTPException(status_code=400, detail="Choose at least one email content type")
    weekdays = _BUSINESS_WEEKDAYS if recurrence == "weekdays" else []
    try:
        next_run = _calculate_next_run(recurrence, body.send_time, weekdays, None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now_iso = _iso(_now())
    with get_db() as db:
        row = _ensure_person_schedule(db, person_id)
        if body.enabled and not (row["person_email"] or "").strip():
            raise HTTPException(status_code=400, detail="Map an email address before enabling this schedule")
        db.execute(
            """UPDATE email_schedules
               SET enabled = ?, recurrence = ?, weekdays = ?, month_day = NULL,
                   send_time = ?, content_types = ?, recipients = NULL, subject = ?,
                   next_run_at = ?, last_error = NULL, updated_at = ?
               WHERE id = ?""",
            (
                int(body.enabled),
                recurrence,
                ",".join(weekdays),
                body.send_time,
                ",".join(content_types),
                PERSON_DEFAULT_SUBJECT,
                _iso(next_run),
                now_iso,
                row["id"],
            ),
        )
        log_event(db, "email_schedule", row["id"], row["person_name"], "updated", actor=get_actor(request))
        updated = _select_schedule_with_person(db, schedule_id=row["id"])
        return _row_to_out(updated)


@router.put("/task-summary", response_model=EmailScheduleOut)
def update_task_summary_schedule(body: EmailScheduleUpdate, request: Request):
    recurrence = (body.recurrence or "weekly").lower()
    if recurrence not in _RECURRENCES:
        raise HTTPException(status_code=400, detail="recurrence must be daily, weekly, weekdays, or monthly")
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
            """SELECT es.*, p.name AS person_name, p.email AS person_email
               FROM email_schedules es
               LEFT JOIN people p ON p.id = es.person_id
               WHERE es.enabled = 1
                 AND es.next_run_at IS NOT NULL
                 AND es.next_run_at <= ?
               ORDER BY es.next_run_at""",
            (now_iso,),
        ).fetchall()
        schedules = [_schedule_dict(row) for row in rows]

    sent = 0
    for schedule in schedules:
        schedule_key = schedule.get("schedule_key") or ""
        if schedule_key == TASK_SUMMARY_KEY:
            label = "Task summary"
            send_fn = _send_task_summary
        elif schedule_key.startswith(PERSON_SCHEDULE_PREFIX):
            label = schedule.get("person_name") or f"BI profile {schedule.get('person_id')}"
            send_fn = _send_person_summary
        else:
            continue
        try:
            _require_fresh_pbi_for_scheduled_email(schedule)
            count = send_fn(schedule)
            _mark_result(schedule["id"], schedule, None)
            with get_db() as db:
                log_event(db, "email_schedule", schedule["id"], label, "sent", f"recipients={count}", "scheduler")
            sent += 1
        except StalePbiSyncError as exc:
            _defer_result(schedule["id"], str(exc))
            with get_db() as db:
                log_event(db, "email_schedule", schedule["id"], label, "deferred", str(exc), "scheduler")
        except Exception as exc:
            _mark_result(schedule["id"], schedule, str(exc))
            with get_db() as db:
                log_event(db, "email_schedule", schedule["id"], label, "send_failed", str(exc), "scheduler")
    return sent
