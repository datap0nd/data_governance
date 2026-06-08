"""Shared controls for scanner refresh work.

The app runs scanner work from HTTP request threads and APScheduler threads.
SQLite rows can outlive those threads after a restart, so this module keeps a
small in-process cancellation generation and also clears stale DB state.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.database import get_db

logger = logging.getLogger(__name__)

_CANCEL_LOCK = threading.Lock()
_CANCEL_GENERATION = 0


class ScannerWorkCancelled(RuntimeError):
    """Raised when older scanner work should stop because newer work started."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_cancel_generation() -> int:
    with _CANCEL_LOCK:
        return _CANCEL_GENERATION


def request_stop_existing_work(reason: str) -> dict:
    """Signal in-process work to stop and mark stale running rows as stopped."""
    global _CANCEL_GENERATION
    with _CANCEL_LOCK:
        _CANCEL_GENERATION += 1
        generation = _CANCEL_GENERATION

    now = _now_iso()
    log_note = f"STOPPED: {reason}"
    result = {
        "status": "stopped",
        "generation": generation,
        "scan_runs_stopped": 0,
        "probe_runs_stopped": 0,
        "pbi_runs_stopped": 0,
    }

    try:
        with get_db() as db:
            scan_cursor = db.execute(
                """UPDATE scan_runs
                   SET finished_at = ?,
                       status = 'stopped',
                       log = CASE
                           WHEN log IS NULL OR TRIM(log) = '' THEN ?
                           ELSE log || char(10) || ?
                       END
                   WHERE LOWER(COALESCE(status, '')) = 'running'""",
                (now, log_note, log_note),
            )
            result["scan_runs_stopped"] = scan_cursor.rowcount if scan_cursor.rowcount != -1 else 0

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

            db.execute(
                """CREATE TABLE IF NOT EXISTS pbi_sync_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_type   TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME,
                    message     TEXT,
                    details     TEXT
                )"""
            )
            pbi_cursor = db.execute(
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
            result["pbi_runs_stopped"] = pbi_cursor.rowcount if pbi_cursor.rowcount != -1 else 0
    except Exception:
        logger.exception("Could not mark existing scanner work as stopped")
        result["status"] = "partial"

    return result


def assert_not_cancelled(generation: int | None, label: str = "Scanner work") -> None:
    if generation is None:
        return
    if current_cancel_generation() != generation:
        raise ScannerWorkCancelled(f"{label} stopped because newer refresh work started.")
