"""Scanner failure recipients and asynchronous Outlook notifications."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.settings import get_setting, set_setting
from app.scanner import modules


RECIPIENTS_SETTING = "scanner_failure_recipients"
EMAIL_PATTERN = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")


def normalize_recipients(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r"[,;\n]", values)
    if not isinstance(values, (list, tuple)):
        raise ValueError("Recipients must be a list of email addresses.")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        address = str(raw or "").strip()
        if not address:
            continue
        if len(address) > 254 or not EMAIL_PATTERN.fullmatch(address):
            raise ValueError(f"Invalid email address: {address}")
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(address)
    if len(result) > 50:
        raise ValueError("No more than 50 scanner failure recipients are allowed.")
    return result


def get_notification_settings() -> dict:
    raw = get_setting(RECIPIENTS_SETTING, "[]") or "[]"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    try:
        recipients = normalize_recipients(parsed)
    except ValueError:
        recipients = []
    return {"recipients": recipients, "enabled": bool(recipients)}


def save_notification_settings(values) -> dict:
    recipients = normalize_recipients(values)
    set_setting(RECIPIENTS_SETTING, json.dumps(recipients, separators=(",", ":")))
    return {"recipients": recipients, "enabled": bool(recipients)}


def _module_rows(run_ids: list[int]) -> list[dict]:
    rows = []
    for run_id in run_ids:
        row = modules.get_module_run(run_id)
        if row is not None:
            rows.append(row)
    return rows


def _message(rows: list[dict], *, stalled: bool = False, test: bool = False) -> dict:
    settings = get_notification_settings()
    recipients = settings["recipients"]
    if test:
        subject = "Metronome scanner notification test"
        body = (
            "<p>This is a test of the Metronome scanner failure notification contact point.</p>"
            "<p>Outlook accepted this message for asynchronous submission.</p>"
        )
    else:
        title = "Scanner work stalled" if stalled else "Scanner module failure"
        subject = f"Metronome: {title}"
        items = []
        for row in rows:
            definition = modules.MODULES_BY_KEY.get(row["module_key"], {})
            label = definition.get("label") or row["module_key"]
            summary = row.get("summary") or row.get("details", {}).get("message") or row["status"]
            items.append(
                "<li><strong>"
                + html.escape(str(label))
                + "</strong> — "
                + html.escape(str(summary))
                + "</li>"
            )
        body = (
            f"<p>{html.escape(title)} requires attention.</p>"
            f"<ul>{''.join(items)}</ul>"
            "<p>Open Metronome → Scanner for the redacted module log and current status.</p>"
        )
    return {
        "to": ";".join(recipients),
        "subject": subject,
        "html_body": body,
    }


def queue_notification(
    run_ids: list[int],
    *,
    stalled: bool = False,
) -> dict:
    settings = get_notification_settings()
    if not settings["enabled"]:
        return {"status": "disabled", "dispatch_id": None}
    rows = _module_rows(run_ids)
    if not rows:
        return {"status": "not_applicable", "dispatch_id": None}
    try:
        from app.routers.email import launch_outlook_dispatch

        dispatch = launch_outlook_dispatch(
            [_message(rows, stalled=stalled)],
            "send",
            purpose="scanner_failure",
        )
        modules.mark_notification(
            [int(row["id"]) for row in rows],
            dispatch_id=int(dispatch["id"]),
            status=str(dispatch.get("status") or "pending"),
            stalled=stalled,
        )
        return {"status": "pending", "dispatch_id": int(dispatch["id"])}
    except Exception as exc:
        modules.mark_notification(
            [int(row["id"]) for row in rows],
            dispatch_id=None,
            status="failed",
            error=str(exc),
            stalled=stalled,
        )
        return {"status": "failed", "dispatch_id": None, "error": str(exc)}


def notify_standalone_failure(module_run_id: int) -> dict:
    row = modules.get_module_run(module_run_id)
    if row is None or row["status"] != "failed" or row.get("notification_status"):
        return {"status": "not_applicable", "dispatch_id": None}
    return queue_notification([module_run_id])


def notify_full_refresh_failures(scanner_job_id: int) -> dict:
    rows = modules.runs_for_job(scanner_job_id)
    failures = [
        int(row["id"])
        for row in rows
        if row["status"] == "failed" and not row.get("notification_status")
    ]
    context_skips = [
        int(row["id"])
        for row in rows
        if row["status"] == "skipped" and not row.get("notification_status")
    ]
    return queue_notification(failures + context_skips) if failures else {
        "status": "not_applicable", "dispatch_id": None
    }


def queue_test_notification() -> dict:
    settings = get_notification_settings()
    if not settings["enabled"]:
        raise ValueError("Save at least one scanner failure recipient first.")
    from app.routers.email import launch_outlook_dispatch

    dispatch = launch_outlook_dispatch(
        [_message([], test=True)],
        "send",
        purpose="scanner_notification_test",
    )
    return {
        "status": dispatch.get("status") or "pending",
        "dispatch_id": int(dispatch["id"]),
        "message": "Test notification queued to Outlook.",
    }


def notify_stalled_module_runs() -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=modules.STALE_AFTER_SECONDS)).isoformat(
        timespec="seconds"
    )
    with get_db() as db:
        rows = db.execute(
            """SELECT id FROM scanner_module_runs
                WHERE status IN ('queued','running')
                  AND COALESCE(heartbeat_at, started_at) <= ?
                  AND stalled_notified_at IS NULL
                  AND notification_status IS NULL
                ORDER BY id""",
            (cutoff,),
        ).fetchall()
    results = [queue_notification([int(row["id"])], stalled=True) for row in rows]
    return {"stalled": len(rows), "notifications": results}
