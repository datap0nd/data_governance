"""Optional final Flow step: email the final file to configured recipients.

The file is exactly what SQL insertion receives, so the recipient gets the
normalized CSV (or the transformation result) rather than a notification.
The API process hands the message to Outlook through the same interactive
scheduled task the alert emails use; the run keeps the worker's data result
and records the email outcome separately, mirroring the Outlook receipt later.
Generated standalone scripts never email.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.database import get_db

EMAIL_PATTERN = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
MAX_RECIPIENTS = 20
MAX_ADDRESS_LENGTH = 254
MAX_SUBJECT_LENGTH = 200
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
PURPOSE = "flow_file"
STATUSES = ("pending", "submitted", "failed", "unknown", "skipped")
STATUS_LABELS = {
    "pending": "handed to Outlook, waiting for its receipt",
    "submitted": "submitted by Outlook",
    "failed": "not sent",
    "unknown": "no Outlook receipt within 24 hours",
    "skipped": "skipped",
}


def default_config() -> dict:
    return {"enabled": False, "recipients": [], "subject": None}


def split_recipients(value) -> list[str]:
    """Accept a list or one string separated by ``;``, ``,`` or newlines."""
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[,;\n]", value)
    if not isinstance(value, (list, tuple)):
        raise ValueError("Recipients must be email addresses separated by semicolons.")
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def normalize_recipients(value) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for address in split_recipients(value):
        if len(address) > MAX_ADDRESS_LENGTH or not EMAIL_PATTERN.fullmatch(address):
            raise ValueError(f"Invalid email address: {address}")
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(address)
    if len(result) > MAX_RECIPIENTS:
        raise ValueError(f"Choose at most {MAX_RECIPIENTS} recipients for the Email step.")
    return result


def normalize_config(value) -> dict:
    """Validate a Flow's saved Email step; absent or disabled input reads as Off."""
    if value is None:
        return default_config()
    if not isinstance(value, dict):
        raise ValueError("Email step settings must be an object.")
    enabled = bool(value.get("enabled"))
    if not enabled:
        return default_config()
    recipients = normalize_recipients(value.get("recipients"))
    if not recipients:
        raise ValueError("Enter at least one recipient email address for the Email step.")
    subject = value.get("subject")
    subject = str(subject).strip() if subject is not None else ""
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise ValueError(f"Keep the email subject within {MAX_SUBJECT_LENGTH} characters.")
    return {"enabled": True, "recipients": recipients, "subject": subject or None}


def saved_config(raw) -> dict:
    """Read a stored JSON column; malformed data means Off."""
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
        return normalize_config(value)
    except (ValueError, TypeError):
        return default_config()


def default_subject(flow_name: str, run_id: int) -> str:
    return f"Metronome flow file: {flow_name} (run #{run_id})"[:500]


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat(timespec="seconds")


def _loads(value, default):
    try:
        result = json.loads(value) if isinstance(value, str) and value else value
    except (ValueError, TypeError):
        return default
    return result if isinstance(result, type(default)) else default


def _file_record(item: dict) -> dict:
    return {
        "filename": str(item.get("filename") or ""),
        "file_path": str(item.get("file_path") or ""),
        "file_size": item.get("file_size"),
        "row_count": item.get("row_count"),
        "published_file_path": item.get("published_file_path"),
        "storage_scope": item.get("storage_scope"),
    }


def final_files(db, run_id: int) -> list[dict]:
    """The files SQL insertion reads for this run, following retry sources.

    A transformation replaces the downloaded CSV with its ``script_results``
    output, so that output is the final file. SQL-only retries carry their
    exact files in the job; view-refresh retries produced nothing new and
    inherit the files of the run they recover.
    """
    seen: set[int] = set()
    current: int | None = int(run_id)
    while current is not None and current not in seen:
        seen.add(current)
        row = db.execute("SELECT id, job_json FROM flow_runs WHERE id=?", (current,)).fetchone()
        if not row:
            return []
        job = _loads(row["job_json"], {})
        if job.get("job_type") == "sql_retry":
            carried = [
                _file_record(item) for item in (job.get("sql_retry") or {}).get("artifacts") or []
                if isinstance(item, dict) and item.get("file_path") and item.get("filename")
            ]
            if carried:
                return carried
        if job.get("job_type") == "view_retry":
            source = (job.get("view_retry") or {}).get("source_run_id")
            current = int(source) if source is not None else None
            continue
        transformed = bool((job.get("transformation") or {}).get("enabled"))
        for status in (("transformed", "saved") if transformed else ("saved",)):
            rows = db.execute(
                """SELECT filename, file_path, file_size, row_count, published_file_path, storage_scope
                   FROM flow_run_files WHERE run_id=? AND status=? AND file_path!='' ORDER BY id""",
                (current, status),
            ).fetchall()
            if rows:
                return [_file_record(dict(item)) for item in rows]
        return []
    return []


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.1f}"


def attachment_plan(files: list[dict]) -> tuple[bool, int, str | None]:
    """Attach everything within the cap; otherwise describe the location instead."""
    total = sum(int(item.get("file_size") or 0) for item in files)
    if total <= MAX_ATTACHMENT_BYTES:
        return True, total, None
    return False, total, (
        f"Attachments skipped: {_megabytes(total)} MB exceeds the "
        f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit; the email names the file location."
    )


def _source_label(context: dict) -> str:
    if context.get("source_type") == "outlook":
        return f"Outlook Inbox subject containing {context.get('outlook_subject_contains')!r}"
    if context.get("source_type") == "file":
        return f"Configured file {context.get('local_file_path')}"
    return f"{context.get('site_name')} / {context.get('report_name')}"


def _row_html(label: str, value: str) -> str:
    return (
        "<tr>"
        '<td style="padding:10px 12px;border:1px solid #cfdcdb;background:#f3f8f7;'
        f'font-size:12px;font-weight:700">{html.escape(label)}</td>'
        '<td style="padding:10px 12px;border:1px solid #cfdcdb;background:#ffffff;'
        f'font-size:12px">{value}</td>'
        "</tr>"
    )


def _file_line(item: dict) -> str:
    rows = item.get("row_count")
    detail = f" ({int(rows):,} rows)" if isinstance(rows, (int, float)) and rows is not None else ""
    return f"{html.escape(item.get('filename') or 'file')}{detail}"


def build_message(context: dict, *, attach: bool, warning: str | None = None) -> dict:
    """One Outlook message carrying the run's final file(s)."""
    config = context["config"]
    files = context["files"]
    run_id = context["run_id"]
    flow_name = context["flow_name"]
    sql = context.get("sql") or {}
    outcome = context.get("sql_outcome") or {}
    trigger = str(context.get("trigger_type") or "manual").replace("_", " ")
    requested_by = str(context.get("requested_by") or "").strip()
    finished = context.get("finished_at") or context.get("started_at") or context.get("created_at")
    file_list = "<br>".join(_file_line(item) for item in files) or "None"
    rows = [
        _row_html("Attached" if attach else "File", file_list),
        _row_html("Flow", html.escape(flow_name)),
        _row_html("Source", html.escape(_source_label(context))),
        _row_html("Run", html.escape(f"#{run_id} - {trigger}" + (f" by {requested_by}" if requested_by else ""))),
        _row_html("Finished at", html.escape(str(finished or "Unknown"))),
    ]
    if sql.get("enabled"):
        target = f"{sql.get('database')}.{sql.get('schema')}.{sql.get('table')}"
        written = outcome.get("rows_written")
        sql_text = f"{sql.get('mode') or 'append'} to {target}"
        if outcome.get("committed") and written is not None:
            sql_text += f" - {int(written):,} row(s) committed"
        rows.append(_row_html("SQL insertion", html.escape(sql_text)))
    if not attach:
        locations = "<br>".join(
            html.escape(str(item.get("published_file_path") or item.get("file_path") or ""))
            for item in files
        )
        rows.append(_row_html("File location", locations or "Not recorded"))
    intro = (
        f"The final file from run #{run_id} is attached. It is the same file SQL insertion receives."
        if attach
        else f"The final file from run #{run_id} is too large to attach ({html.escape(str(warning or ''))}). "
             "Open it from the location below."
    )
    owner = context.get("owner_name") or "no owner assigned"
    body = f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="width:100%;border-collapse:collapse;background:#eef3f2;
                  font-family:Segoe UI,Arial,sans-serif">
      <tr>
        <td align="center" style="padding:24px 12px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="width:100%;max-width:760px;border-collapse:separate;
                        background:#ffffff;border:1px solid #cfdcdb;border-radius:12px">
            <tr>
              <td style="padding:25px 28px;background:#1f6f6b;color:#ffffff;
                         border-radius:12px 12px 0 0">
                <div style="margin:0 0 7px;color:#d6ecea;font-size:11px;
                            font-weight:700;letter-spacing:1.2px">
                  FLOW FILE
                </div>
                <div style="margin:0;color:#ffffff;font-size:25px;font-weight:700;
                            line-height:1.2">
                  {html.escape(flow_name)}
                </div>
                <div style="margin-top:8px;color:#e6f2f1;font-size:14px;line-height:1.4">
                  {intro}
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
              <td style="padding:14px 28px;background:#f4f8f7;color:#5f6d6b;
                         border-top:1px solid #dfe8e7;border-radius:0 0 12px 12px;
                         font-size:11px;line-height:1.4">
                Sent by Metronome after every successful run of this Flow. Flow owner:
                {html.escape(str(owner))}. Run history in Metronome &gt; Flows keeps the
                complete log for run #{run_id}.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """
    message = {
        "to": "; ".join(config["recipients"]),
        "subject": (config.get("subject") or default_subject(flow_name, run_id))[:500],
        "html_body": body,
    }
    if attach:
        message["attachments"] = [
            {"path": item["file_path"], "filename": item["filename"]} for item in files
        ]
    return message


def run_context(db, run_id: int) -> dict | None:
    """Everything the Email step needs about one run, read in one connection."""
    row = db.execute(
        """SELECT r.id AS run_id, r.flow_id, r.status, r.trigger_type, r.requested_by,
                  r.created_at, r.started_at, r.finished_at, r.job_json, r.progress_json,
                  r.sql_outcome_json, r.email_status, r.email_detail, r.email_dispatch_id,
                  f.name AS flow_name, f.source_type, f.outlook_subject_contains,
                  f.local_file_path, s.name AS site_name, rep.name AS report_name,
                  p.name AS owner_name
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
    job = _loads(context.pop("job_json"), {})
    progress = _loads(context.pop("progress_json"), {})
    context["config"] = saved_config(json.dumps(job.get("email_delivery"))) if job.get("email_delivery") else default_config()
    context["no_op"] = bool(progress.get("no_op"))
    context["sql"] = job.get("sql_handoff") or {}
    context["sql_outcome"] = _loads(context.pop("sql_outcome_json"), {}) or {}
    context["files"] = final_files(db, run_id)
    return context


def run_summary(row, job: dict, *, files: list[dict] | None = None) -> dict | None:
    """The run payload's ``email`` block; ``None`` when the step is off."""
    config = job.get("email_delivery") if isinstance(job, dict) else None
    if not isinstance(config, dict) or not config.get("enabled"):
        return None
    keys = row.keys() if hasattr(row, "keys") else ()
    status = row["email_status"] if "email_status" in keys else None
    summary = {
        "status": status,
        "detail": row["email_detail"] if "email_detail" in keys else None,
        "dispatch_id": row["email_dispatch_id"] if "email_dispatch_id" in keys else None,
        "recipients": list(config.get("recipients") or []),
        "subject": config.get("subject"),
        "label": STATUS_LABELS.get(status or "", "not attempted yet"),
    }
    if files is not None:
        summary["files"] = [item.get("filename") for item in files]
    return summary


def _record_outcome(run_id: int, context: dict | None, outcome: dict, *, stage: str,
                    message: str, action: str, actor: str | None = None) -> None:
    now = _now()
    with get_db() as db:
        db.execute(
            "UPDATE flow_runs SET email_dispatch_id=?, email_status=?, email_detail=? WHERE id=?",
            (outcome.get("dispatch_id"), outcome["status"], outcome.get("detail"), run_id),
        )
        if context:
            db.execute(
                """INSERT INTO flow_run_events
                   (run_id, status, stage, message, details_json, error, traceback, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    run_id, context.get("status") or "succeeded", stage, message,
                    json.dumps({
                        "stage": stage, "email_status": outcome["status"],
                        "dispatch_id": outcome.get("dispatch_id"),
                        "recipient_count": len(context["config"].get("recipients") or []),
                        "files": [item.get("filename") for item in context.get("files") or []],
                        "attached": bool(outcome.get("attached")),
                    }),
                    outcome.get("detail") if outcome["status"] == "failed" else None,
                    now,
                ),
            )
            from app.routers.eventlog import log_event
            log_event(
                db, "flow", context["flow_id"], context["flow_name"], action,
                f"run #{run_id}: {message}"[:1000], actor,
            )


def deliver_run_file(run_id: int, *, resend: bool = False, actor: str | None = None) -> dict:
    """Hand the run's final file to Outlook. Never raises.

    ``update_run`` calls this once per terminal success after its transaction
    commits; ``Send again`` calls it with ``resend=True``. Every branch writes
    the outcome on the run so Run history and the run log show it.
    """
    log = logging.getLogger(__name__)
    context = None
    outcome: dict[str, Any]
    try:
        with get_db() as db:
            context = run_context(db, run_id)
        if context is None:
            return {"status": "skipped", "detail": "Run not found."}
        config = context["config"]
        if not config.get("enabled"):
            return {"status": "skipped", "detail": "The Email step is not enabled for this run."}
        if context["status"] != "succeeded":
            return {"status": "skipped", "detail": "Only a succeeded run sends its final file."}
        if context["no_op"]:
            outcome = {"status": "skipped", "detail": "Nothing new was found, so there is no file to send."}
            _record_outcome(run_id, context, outcome, stage="email_skipped",
                            message=outcome["detail"], action="email_skipped", actor=actor)
            return outcome
        files = context["files"]
        if not files:
            raise RuntimeError("No final file was recorded for this run.")
        attach, total, warning = attachment_plan(files)
        message = build_message(context, attach=attach, warning=warning)
        from app.routers.email import launch_outlook_dispatch

        dispatch = launch_outlook_dispatch([message], "send", purpose=PURPOSE)
        outcome = {
            "status": "pending", "dispatch_id": int(dispatch["id"]), "detail": warning,
            "attached": attach, "total_bytes": total,
            "recipients": list(config["recipients"]),
            "files": [item["filename"] for item in files],
        }
        summary = (
            f"Email with {len(files)} file(s) handed to Outlook for {len(config['recipients'])} recipient(s)."
            if attach else
            f"Email without attachments handed to Outlook for {len(config['recipients'])} recipient(s): {warning}"
        )
        if resend:
            summary = "Send again: " + summary
        _record_outcome(run_id, context, outcome, stage="email_delivery", message=summary,
                        action="email_resent" if resend else "email_launched", actor=actor)
    except Exception as exc:
        log.exception("Could not hand the final file of run %s to Outlook", run_id)
        detail = (str(exc).strip() or exc.__class__.__name__)[:4000]
        outcome = {"status": "failed", "detail": detail, "attached": False}
        try:
            _record_outcome(run_id, context, outcome, stage="email_failed",
                            message=f"Email could not be handed to Outlook: {detail}",
                            action="email_failed", actor=actor)
        except Exception:
            log.exception("Could not record the email outcome for run %s", run_id)
    return outcome


def mirror_receipts(db) -> int:
    """Copy reconciled Outlook receipts onto the runs that launched them."""
    rows = db.execute(
        """SELECT r.id, od.status, od.error FROM flow_runs r
           JOIN outlook_dispatches od ON od.id = r.email_dispatch_id
          WHERE r.email_status = 'pending' AND od.status != 'pending'"""
    ).fetchall()
    for row in rows:
        status = row["status"] if row["status"] in STATUSES else "unknown"
        db.execute(
            "UPDATE flow_runs SET email_status=?, email_detail=COALESCE(?, email_detail) WHERE id=?",
            (status, str(row["error"])[:4000] if row["error"] else None, row["id"]),
        )
    return len(rows)
