"""What Metronome writes to its service logs.

The web service's stdout and stderr go to files under ``<project>/logs``.
Three things made those files large: every HTTP request was logged, including
the workers' polling and the pages' status refreshes; the scheduler logged two
lines for every job run, several of which run every few seconds; and a failure
that repeats on every tick was written out in full each time.

``configure()`` keeps application messages at INFO, turns the scheduler's and
HTTP client's routine lines down to warnings, logs only requests worth keeping
(see ``AccessLogFilter``), and stops one identical message from being written
more than a few times a minute.
"""
from __future__ import annotations

import logging
import threading
import time

REQUEST_LOG_KEY = "logs.request_log"
# changes: failed requests and successful requests that change something.
# errors:  failed requests only.  all: every request, for diagnosis.
REQUEST_LOG_MODES = ("changes", "errors", "all")
DEFAULT_REQUEST_LOG_MODE = "changes"

# Workers register, claim and report progress several times a minute each.
_WORKER_PROTOCOL_PREFIX = "/api/flows/worker/"
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_QUIET_LIBRARIES = (
    "apscheduler.executors.default", "apscheduler.scheduler", "httpx", "httpcore",
)

_request_log_mode = DEFAULT_REQUEST_LOG_MODE
_configured = False


def request_log_mode() -> str:
    return _request_log_mode


def set_request_log_mode(mode: str | None) -> str:
    """Apply a saved mode at once; an unknown value falls back to the default."""
    global _request_log_mode
    _request_log_mode = mode if mode in REQUEST_LOG_MODES else DEFAULT_REQUEST_LOG_MODE
    return _request_log_mode


def refresh_request_log_mode() -> str:
    """Read the saved mode. Called at start-up and on a timer, never from a
    logging call, so writing a log line cannot wait on the database."""
    try:
        from app.settings import get_setting

        return set_request_log_mode(get_setting(REQUEST_LOG_KEY, DEFAULT_REQUEST_LOG_MODE))
    except Exception:
        return _request_log_mode


class AccessLogFilter(logging.Filter):
    """Decide which uvicorn access lines are written.

    uvicorn logs ``(client, method, path, http_version, status)``. A record in
    any other shape passes unchanged, so a uvicorn upgrade cannot silence the
    log by accident.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        mode = _request_log_mode
        if mode == "all":
            return True
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        try:
            method = str(args[1]).upper()
            path = str(args[2])
            status = int(args[4])
        except (TypeError, ValueError):
            return True
        if status >= 400:
            return True
        if mode == "errors":
            return False
        if method in _READ_METHODS:
            return False
        return not path.startswith(_WORKER_PROTOCOL_PREFIX)


class RepeatLimiter(logging.Filter):
    """Write one identical message at most ``burst`` times per ``window``.

    The first line after a quiet window says how many copies were dropped, so
    a failure that repeats on every scheduler tick stays visible without
    filling the disk. Messages are compared after formatting, so lines that
    differ in any detail are all written.
    """

    def __init__(self, burst: int = 5, window: float = 60.0, clock=time.monotonic) -> None:
        super().__init__()
        self.burst = burst
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[tuple, list] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        key = (record.name, record.levelno, message[:300])
        now = self._clock()
        with self._lock:
            state = self._seen.get(key)
            if state is None or now - state[0] >= self.window:
                dropped = state[2] if state else 0
                if len(self._seen) > 2000:
                    self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.window}
                self._seen[key] = [now, 1, 0]
                if dropped:
                    record.msg = f"{message} [repeated {dropped} more time(s) in the previous minute]"
                    record.args = None
                return True
            state[1] += 1
            if state[1] <= self.burst:
                return True
            state[2] += 1
            return False


def configure() -> None:
    """Set up logging once for the web service process."""
    global _configured
    root = logging.getLogger()
    already_there = set(root.handlers)
    # A no-op when something else (a test runner, an embedding host) already
    # installed handlers; only the handler created here gets the limiter.
    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
    if _configured:
        return
    _configured = True
    for name in _QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    limiter = RepeatLimiter()
    for handler in root.handlers:
        if handler not in already_there:
            handler.addFilter(limiter)
    # uvicorn configures this logger before it imports the application.
    logging.getLogger("uvicorn.access").addFilter(AccessLogFilter())
