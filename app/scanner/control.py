"""Shared controls for scanner refresh work.

The app runs scanner work from HTTP request threads and APScheduler threads.
SQLite rows can outlive those threads after a restart, so this module keeps a
small in-process cancellation generation and also clears stale DB state.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from app.database import get_db

logger = logging.getLogger(__name__)

_CANCEL_LOCK = threading.Lock()
_CANCEL_GENERATION = 0
# Power BI callbacks can arrive from a separate PowerShell process.  They need
# a dedicated barrier because general scanner code may check cancellation while
# holding a DB transaction; reusing _CANCEL_LOCK around a DB write would invert
# that lock order and could deadlock.
_PBI_STOP_LOCK = threading.RLock()
_PBI_CALLBACKS_FENCED = False


class ScannerWorkCancelled(RuntimeError):
    """Raised when older scanner work should stop because newer work started."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_cancel_generation() -> int:
    with _CANCEL_LOCK:
        return _CANCEL_GENERATION


@contextmanager
def pbi_stop_barrier():
    """Serialize PBI launches/callback commits with stop publication."""
    with _PBI_STOP_LOCK:
        yield


def pbi_callbacks_fenced() -> bool:
    """Return whether DB stop persistence failed and callbacks must fail closed."""
    with _PBI_STOP_LOCK:
        return _PBI_CALLBACKS_FENCED


def clear_pbi_callback_fence() -> None:
    """Clear the fallback fence after a newer launch or recovery commits."""
    global _PBI_CALLBACKS_FENCED
    with _PBI_STOP_LOCK:
        _PBI_CALLBACKS_FENCED = False


def _terminalize_active_pbi_runs(db, now: str, log_note: str) -> int:
    db.execute(
        """CREATE TABLE IF NOT EXISTS pbi_sync_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_type   TEXT NOT NULL,
            attempt_id  TEXT,
            status      TEXT NOT NULL,
            started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            message     TEXT,
            details     TEXT
        )"""
    )
    cursor = db.execute(
        """UPDATE pbi_sync_runs
           SET finished_at = COALESCE(finished_at, ?),
               status = 'stopped',
               message = CASE
                   WHEN message IS NULL OR TRIM(message) = '' THEN ?
                   ELSE message || char(10) || ?
               END
           WHERE LOWER(COALESCE(status, '')) IN ('launched', 'pending')""",
        (now, log_note, log_note),
    )
    return cursor.rowcount if cursor.rowcount != -1 else 0


def request_stop_existing_work(
    reason: str, *, exclude_scanner_job_id: int | None = None
) -> dict:
    """Signal in-process work to stop without preempting atomic finalization.

    Active scan runners observe the generation change and write their terminal
    counters, components, log, and stopped status together. Truly orphaned rows
    are recovered during application startup.
    """
    global _CANCEL_GENERATION, _PBI_CALLBACKS_FENCED
    now = _now_iso()
    log_note = f"STOPPED: {reason}"
    result = {
        "status": "stopped",
        "generation": None,
        "scan_runs_stopped": 0,
        "scanner_jobs_stopped": 0,
        "probe_runs_stopped": 0,
        "pbi_runs_stopped": 0,
        "pbi_callbacks_fenced": False,
    }

    # This is the stop linearization point for out-of-process Power BI work.
    # A callback that acquired the same barrier first may finish; every callback
    # after this transaction must observe a terminal launch row. The new cancel
    # generation is not visible until either that commit succeeds or the
    # fail-closed in-memory callback fence has been installed.
    with _PBI_STOP_LOCK:
        try:
            with get_db() as db:
                db.execute("BEGIN IMMEDIATE")
                result["pbi_runs_stopped"] = _terminalize_active_pbi_runs(
                    db, now, log_note
                )
        except Exception:
            logger.exception("Could not mark Power BI sync work as stopped")
            result["status"] = "partial"
            # The DB row may still say launched. Keep every callback closed in
            # memory until a newer correlated launch commits (which makes old
            # attempts superseded) or recovery successfully repairs the rows.
            _PBI_CALLBACKS_FENCED = True
            result["pbi_callbacks_fenced"] = True
        else:
            _PBI_CALLBACKS_FENCED = False
        with _CANCEL_LOCK:
            _CANCEL_GENERATION += 1
            result["generation"] = _CANCEL_GENERATION

    try:
        from app.scanner.jobs import stop_active_jobs

        result["scanner_jobs_stopped"] = stop_active_jobs(
            reason,
            finished_at=now,
            exclude_job_id=exclude_scanner_job_id,
        )
        with get_db() as db:
            scan_row = db.execute(
                """SELECT COUNT(*) AS count FROM scan_runs
                   WHERE LOWER(COALESCE(status, '')) = 'running'"""
            ).fetchone()
            result["scan_runs_stopped"] = int(scan_row["count"] or 0)

            probe_cursor = db.execute(
                """UPDATE probe_runs
                   SET finished_at = ?,
                       status = 'stopped',
                       log = CASE
                           WHEN log IS NULL OR TRIM(log) = '' THEN ?
                           ELSE log || char(10) || ?
                       END
                   WHERE LOWER(COALESCE(status, '')) = 'running'""",
                (now, log_note, log_note),
            )
            result["probe_runs_stopped"] = probe_cursor.rowcount if probe_cursor.rowcount != -1 else 0

    except Exception:
        logger.exception("Could not mark existing scanner work as stopped")
        result["status"] = "partial"

    return result


def assert_not_cancelled(generation: int | None, label: str = "Scanner work") -> None:
    if generation is None:
        return
    if current_cancel_generation() != generation:
        raise ScannerWorkCancelled(f"{label} stopped because newer refresh work started.")
