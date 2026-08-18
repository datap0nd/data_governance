"""In-process log capture for the AI Logs report.

Everything durable (runs, scans, events) already lives in the database; this
module adds the piece only the running process knows - warnings, errors, and
app-namespace log records since the app started - in a bounded ring buffer,
plus the app start timestamp the AI Logs report anchors on.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

APP_STARTED_AT = datetime.now().replace(microsecond=0)

_BUFFER: deque[str] = deque(maxlen=1000)


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("uvicorn.access"):
            return
        try:
            _BUFFER.append(self.format(record))
        except Exception:
            pass


def install() -> None:
    """Attach the ring buffer to the root logger. Idempotent."""
    root = logging.getLogger()
    if any(isinstance(handler, _RingBufferHandler) for handler in root.handlers):
        return
    handler = _RingBufferHandler(level=logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    # Let app-namespace INFO records reach the buffer without changing the
    # global logging configuration for anything else.
    app_logger = logging.getLogger("app")
    if app_logger.level in (logging.NOTSET, logging.WARNING):
        app_logger.setLevel(logging.INFO)


def process_log_lines() -> list[str]:
    return list(_BUFFER)
