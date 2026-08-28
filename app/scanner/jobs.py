"""Durable, live status records for scanner work.

The historic ``scan_runs`` and ``probe_runs`` tables describe completed domain
results.  Scanner jobs are the operational view: they exist before work starts,
carry a heartbeat and current phase while it runs, and retain a bounded result
when it finishes.  This makes a focused PostgreSQL lineage recheck observable
even if the browser that started it disconnects.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from app.database import get_db
from app.scanner.lifecycle import normalize_scan_status, redact_component_payload


ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
TERMINAL_JOB_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "failed", "stopped"}
)


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


# Ten minutes without a phase/heartbeat is exceptional for the catalog calls.
# It is deliberately a UI health signal rather than an automatic terminal
# transition: a temporarily slow network call can still finish truthfully.
STALE_AFTER_SECONDS = _bounded_int_env(
    "DG_SCANNER_STALE_AFTER_SECONDS", 600, 60, 3600
)
MAX_MESSAGE_CHARS = 1000
MAX_RESULT_CHARS = 24000


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _json(value: Any, *, max_chars: int = MAX_RESULT_CHARS) -> str:
    encoded = json.dumps(
        redact_component_payload(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    if len(encoded) <= max_chars:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "message": "Result details were too large; review the component history.",
        },
        separators=(",", ":"),
    )


def _loads(value: Any) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _message(value: Any) -> str | None:
    if value is None:
        return None
    safe = redact_component_payload(str(value)).strip()
    if not safe:
        return None
    return safe[:MAX_MESSAGE_CHARS]


def create_job(
    job_type: str,
    *,
    trigger_source: str = "manual",
    current_step: str = "Queued",
    message: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> int:
    """Create a queued operation before any slow work starts.

    Scanner catalog mutations are globally serialized. Call ``reserve_job``
    when the caller wants to surface/reuse the currently active operation.
    """
    job, created = reserve_job(
        job_type,
        trigger_source=trigger_source,
        current_step=current_step,
        message=message,
        context=context,
    )
    if not created:
        raise RuntimeError(
            f"Scanner job {job['id']} ({job['job_type']}) is already active."
        )
    return int(job["id"])


def reserve_job(
    job_type: str,
    *,
    trigger_source: str = "manual",
    current_step: str = "Queued",
    message: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> tuple[dict, bool]:
    """Atomically reserve the single scanner worker slot.

    The ``BEGIN IMMEDIATE`` makes the active-row check and insert one SQLite
    decision, preventing two simultaneous browser clicks (or app workers) from
    creating overlapping catalog mutations.
    """
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            """SELECT * FROM scanner_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, id DESC
                LIMIT 1"""
        ).fetchone()
        if existing is not None:
            return _serialize(existing), False
        cursor = db.execute(
            """INSERT INTO scanner_jobs
                   (job_type, trigger_source, status, current_step, message,
                    context_json, created_at, heartbeat_at)
               VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)""",
            (
                str(job_type),
                str(trigger_source or "manual"),
                _message(current_step),
                _message(message),
                _json(dict(context or {})),
                now,
                now,
            ),
        )
        job_id = int(cursor.lastrowid)
        row = db.execute("SELECT * FROM scanner_jobs WHERE id=?", (job_id,)).fetchone()
        return _serialize(row), True


def mark_running(
    job_id: int,
    *,
    current_step: str,
    message: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
) -> bool:
    now = _iso()
    with get_db() as db:
        cursor = db.execute(
            """UPDATE scanner_jobs
                  SET status='running', started_at=COALESCE(started_at, ?),
                      heartbeat_at=?, current_step=?, message=?,
                      progress_current=?, progress_total=?
                WHERE id=? AND status IN ('queued', 'running')""",
            (
                now,
                now,
                _message(current_step),
                _message(message),
                progress_current,
                progress_total,
                int(job_id),
            ),
        )
        return bool(cursor.rowcount)


def heartbeat(
    job_id: int | None,
    *,
    current_step: str,
    message: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    db=None,
) -> bool:
    """Update live phase/progress without reviving terminal work.

    Callers already holding SQLite's write slot may pass that connection. This
    avoids opening a second writer that would block behind the caller's own
    transaction. The caller controls when that shared transaction is committed.
    """
    if job_id is None:
        return False
    now = _iso()
    values = (
        now,
        _message(current_step),
        _message(message),
        progress_current,
        progress_total,
        int(job_id),
    )
    statement = """UPDATE scanner_jobs
                  SET heartbeat_at=?, current_step=?,
                      message=COALESCE(?, message),
                      progress_current=COALESCE(?, progress_current),
                      progress_total=COALESCE(?, progress_total)
                WHERE id=? AND status IN ('queued', 'running')"""
    if db is not None:
        cursor = db.execute(statement, values)
        return bool(cursor.rowcount)
    with get_db() as connection:
        cursor = connection.execute(
            statement,
            values,
        )
        return bool(cursor.rowcount)


def attach_scan_run(job_id: int | None, scan_run_id: int) -> None:
    if job_id is None:
        return
    with get_db() as db:
        db.execute(
            """UPDATE scanner_jobs SET scan_run_id=?
                WHERE id=? AND status IN ('queued', 'running')""",
            (int(scan_run_id), int(job_id)),
        )


def _result_message(job_type: str, status: str, result: Mapping[str, Any]) -> str:
    if job_type == "postgres_lineage":
        databases = result.get("databases")
        if isinstance(databases, Mapping):
            failed = [
                f"{name} ({details.get('stage') or 'scan'})"
                for name, details in databases.items()
                if isinstance(details, Mapping)
                and normalize_scan_status(details.get("status")) == "failed"
            ]
            if failed:
                return "Lineage could not be refreshed for: " + ", ".join(failed)
        if status == "completed":
            return (
                f"Lineage refreshed: {int(result.get('mvs_found') or 0)} materialized "
                f"views, {int(result.get('deps_created') or 0)} dependencies."
            )
    explicit = result.get("message")
    if explicit:
        return str(explicit)
    labels = {
        "completed": "Completed.",
        "completed_with_warnings": "Completed with warnings.",
        "failed": "Failed; see the component details below.",
        "stopped": "Stopped.",
    }
    return labels.get(status, status.replace("_", " ").capitalize() + ".")


def finish_job(
    job_id: int | None,
    *,
    status: str,
    result: Mapping[str, Any] | None = None,
    message: str | None = None,
) -> bool:
    """Atomically finish an active job; a concurrent stop always wins."""
    if job_id is None:
        return False
    normalized = normalize_scan_status(status)
    if normalized in {"not_requested", "skipped"}:
        normalized = "completed"
    if normalized not in TERMINAL_JOB_STATUSES:
        normalized = "failed"
    safe_result = dict(result or {})
    final_message = message or _result_message("", normalized, safe_result)
    with get_db() as db:
        row = db.execute(
            "SELECT job_type FROM scanner_jobs WHERE id=?", (int(job_id),)
        ).fetchone()
        if row is not None and message is None:
            final_message = _result_message(row["job_type"], normalized, safe_result)
        cursor = db.execute(
            """UPDATE scanner_jobs
                  SET status=?, current_step=?, message=?, result_json=?,
                      heartbeat_at=?, finished_at=?
                WHERE id=? AND status IN ('queued', 'running')""",
            (
                normalized,
                "Finished" if normalized in {"completed", "completed_with_warnings"} else normalized.replace("_", " ").capitalize(),
                _message(final_message),
                _json(safe_result),
                _iso(),
                _iso(),
                int(job_id),
            ),
        )
        return bool(cursor.rowcount)


def stop_active_jobs(
    reason: str,
    *,
    finished_at: str | None = None,
    exclude_job_id: int | None = None,
) -> int:
    now = finished_at or _iso()
    exclusion = " AND id != ?" if exclude_job_id is not None else ""
    params: tuple[Any, ...] = (
        (_message(reason), now, now, int(exclude_job_id))
        if exclude_job_id is not None
        else (_message(reason), now, now)
    )
    with get_db() as db:
        cursor = db.execute(
            f"""UPDATE scanner_jobs
                  SET status='stopped', current_step='Stopped', message=?,
                      heartbeat_at=?, finished_at=?
                WHERE status IN ('queued', 'running'){exclusion}""",
            params,
        )
        return cursor.rowcount if cursor.rowcount != -1 else 0


def recover_interrupted_jobs(*, finished_at: str | None = None) -> int:
    return stop_active_jobs(
        "Stopped because the Metronome service restarted.", finished_at=finished_at
    )


def _serialize(row: Mapping[str, Any], *, now: datetime | None = None) -> dict:
    data = dict(row)
    data["context"] = _loads(data.pop("context_json", None))
    data["result"] = _loads(data.pop("result_json", None))
    status = normalize_scan_status(data.get("status"))
    current_time = now or datetime.now(timezone.utc)
    heartbeat_time = _parse_time(data.get("heartbeat_at") or data.get("created_at"))
    heartbeat_age = (
        max(0, int((current_time - heartbeat_time).total_seconds()))
        if heartbeat_time
        else None
    )
    is_stale = bool(
        status in ACTIVE_JOB_STATUSES
        and heartbeat_age is not None
        and heartbeat_age >= STALE_AFTER_SECONDS
    )
    data["status"] = status
    data["display_status"] = "stale" if is_stale else status
    data["active"] = status in ACTIVE_JOB_STATUSES
    data["is_stale"] = is_stale
    data["heartbeat_age_seconds"] = heartbeat_age
    data["stale_after_seconds"] = STALE_AFTER_SECONDS
    return data


def get_job(job_id: int, *, now: datetime | None = None) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM scanner_jobs WHERE id=?", (int(job_id),)).fetchone()
    return _serialize(row, now=now) if row is not None else None


def list_jobs(*, limit: int = 30, now: datetime | None = None) -> list[dict]:
    bounded_limit = max(1, min(int(limit), 100))
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM scanner_jobs ORDER BY id DESC LIMIT ?", (bounded_limit,)
        ).fetchall()
    return [_serialize(row, now=now) for row in rows]


def find_active_job(job_types: set[str] | frozenset[str]) -> dict | None:
    if not job_types:
        return None
    placeholders = ",".join("?" for _ in job_types)
    with get_db() as db:
        row = db.execute(
            f"""SELECT * FROM scanner_jobs
                 WHERE status IN ('queued', 'running')
                   AND job_type IN ({placeholders})
                 ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, id DESC
                 LIMIT 1""",
            tuple(sorted(job_types)),
        ).fetchone()
    return _serialize(row) if row is not None else None
