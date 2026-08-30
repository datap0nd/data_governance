"""Small, structured, operator-facing error history.

The Windows service console streams are useful as a last-resort diagnostic,
but they are not an operator interface.  This module mirrors ERROR-and-higher
Python records into a bounded JSONL history that the web app can render as
individual, readable incidents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import DB_PATH


MAX_FILE_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 4
MAX_DETAIL_CHARS = 16_000
MAX_SUMMARY_CHARS = 1_000
MAX_ERROR_MESSAGE_CHARS = 2_000
_HANDLER_MARKER = "_metronome_operator_error_handler"
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")


def get_operator_log_path() -> Path:
    configured = os.environ.get("DG_OPERATOR_LOG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(DB_PATH).expanduser().resolve().parent / "logs" / "operator_errors.jsonl"


def sanitize_operator_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    text = _CREDENTIAL_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[redacted]", text
    )
    text = _BEARER_TOKEN.sub("Bearer [redacted]", text)
    text = _URL_USERINFO.sub(r"\1[redacted]@", text)
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def public_exception_summary(exc: BaseException) -> str:
    """Return a bounded useful failure reason without credential-shaped text."""
    error_type = type(exc).__name__
    message = sanitize_operator_text(exc, limit=MAX_ERROR_MESSAGE_CHARS).strip()
    return f"{error_type}: {message}" if message else error_type


def _area_for_record(record: logging.LogRecord) -> str:
    explicit = str(getattr(record, "operator_area", "") or "").strip()
    if explicit:
        return explicit[:80]
    name = record.name.casefold()
    if "scanner" in name or "pbi_" in name:
        return "Scanner"
    if name.startswith("app.ai"):
        return "AI"
    if "flow" in name:
        return "Flows"
    if "pipeline" in name:
        return "Pipelines"
    if "email" in name:
        return "Email"
    if "auto_update" in name or "update" in name:
        return "Updates"
    if "database" in name:
        return "Database"
    return "Application"


class OperatorErrorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        created_at = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
        summary = sanitize_operator_text(record.getMessage(), limit=MAX_SUMMARY_CHARS)
        error_type = None
        error_message = None
        technical_detail = None
        if record.exc_info:
            exception = record.exc_info[1]
            if exception is not None:
                error_type = type(exception).__name__
                error_message = sanitize_operator_text(
                    exception, limit=MAX_ERROR_MESSAGE_CHARS
                )
            technical_detail = sanitize_operator_text(
                self.formatException(record.exc_info), limit=MAX_DETAIL_CHARS
            )
        elif record.stack_info:
            technical_detail = sanitize_operator_text(
                record.stack_info, limit=MAX_DETAIL_CHARS
            )

        identity = "|".join(
            (
                created_at,
                record.name,
                summary,
                error_type or "",
                error_message or "",
            )
        )
        payload = {
            "id": hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:16],
            "created_at": created_at,
            "level": record.levelname.lower(),
            "area": _area_for_record(record),
            "logger": record.name,
            "summary": summary,
            "error_type": error_type,
            "error_message": error_message,
            "technical_detail": technical_detail,
            "operation_id": getattr(record, "operation_id", None),
            "scan_id": getattr(record, "scan_id", None),
            "job_id": getattr(record, "job_id", None),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def install_operator_error_handler(path: Path | None = None) -> Path | None:
    """Install one bounded ERROR mirror on the process root logger."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            uvicorn_error = logging.getLogger("uvicorn.error")
            if not uvicorn_error.propagate and handler not in uvicorn_error.handlers:
                uvicorn_error.addHandler(handler)
            return Path(getattr(handler, "baseFilename", ""))

    destination = Path(path or get_operator_log_path())
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            destination,
            maxBytes=MAX_FILE_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
    except OSError:
        # Logging must never prevent the web process from starting.
        return None
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(OperatorErrorFormatter())
    root.addHandler(handler)
    # Uvicorn deliberately disables propagation for its server-error logger.
    # Attach the same bounded handler so unhandled request/startup exceptions
    # also reach the operator page without duplicating ordinary app records.
    uvicorn_error = logging.getLogger("uvicorn.error")
    if not uvicorn_error.propagate and handler not in uvicorn_error.handlers:
        uvicorn_error.addHandler(handler)
    return destination


def _history_files(path: Path) -> list[Path]:
    # RotatingFileHandler numbers backups newest-first (.1 is newer than .4).
    candidates = [path]
    candidates.extend(Path(f"{path}.{index}") for index in range(1, BACKUP_COUNT + 1))
    return [candidate for candidate in candidates if candidate.is_file()]


def read_operator_errors(
    *,
    path: Path | None = None,
    limit: int = 100,
    area: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    destination = Path(path or get_operator_log_path())
    bounded_limit = min(250, max(1, int(limit)))
    area_filter = (area or "").strip().casefold()
    search_filter = (search or "").strip().casefold()
    records: list[dict[str, Any]] = []
    invalid_lines = 0

    # Current file is newest, then .1, .2, etc. Read each file backwards so we
    # can stop as soon as the requested bounded result is filled.
    for candidate in _history_files(destination):
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                invalid_lines += 1
                continue
            if not isinstance(item, dict):
                invalid_lines += 1
                continue
            if area_filter and str(item.get("area") or "").casefold() != area_filter:
                continue
            if search_filter:
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in (
                        "area",
                        "summary",
                        "error_type",
                        "error_message",
                        "technical_detail",
                    )
                ).casefold()
                if search_filter not in haystack:
                    continue
            records.append(item)
            if len(records) >= bounded_limit:
                break
        if len(records) >= bounded_limit:
            break

    files = _history_files(destination)
    return {
        "errors": records,
        "count": len(records),
        "invalid_lines": invalid_lines,
        "storage": {
            "files": len(files),
            "bytes": sum(item.stat().st_size for item in files),
            "maximum_bytes": MAX_FILE_BYTES * (BACKUP_COUNT + 1),
        },
    }


def prune_rotated_service_logs(log_dir: Path | None = None) -> int:
    """Remove obsolete NSSM rotations after a service restart.

    The active ``mx_analytics*.log`` files are never touched.  Only timestamped
    rotations created by NSSM are eligible. The newest small rotation is kept
    for last-resort diagnosis; oversized rotations are removed because their
    useful failures are now captured in the structured history.
    """
    root = Path(log_dir or get_operator_log_path().parent)
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return 0
    removed = 0
    for prefix in ("mx_analytics", "mx_analytics_error", "flow_worker", "flow_worker_error"):
        try:
            matches = sorted(
                (
                    item
                    for item in resolved_root.iterdir()
                    if item.is_file()
                    and item.name.startswith(prefix + "-")
                    and item.suffix.casefold() == ".log"
                    and item.resolve().parent == resolved_root
                ),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            continue
        for index, item in enumerate(matches):
            try:
                oversized = item.stat().st_size > 20 * 1024 * 1024
                if index > 0 or oversized:
                    item.unlink()
                    removed += 1
            except OSError:
                continue
    return removed
