"""Shared scan-run lifecycle and component-result helpers.

The report scan is a multi-component operation.  This module keeps terminal
status semantics, safe component serialization, and the single terminal row
update in one place so API and scheduler callers do not have to reinterpret
the runner's results.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping


SUCCESSFUL_SCAN_STATUSES = frozenset({"completed", "completed_with_warnings"})
TERMINAL_SCAN_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "failed", "stopped"}
)

_STATUS_ALIASES = {
    "complete": "completed",
    "success": "completed",
    "succeeded": "completed",
    "ok": "completed",
    "unchanged": "completed",
    "warning": "completed_with_warnings",
    "warnings": "completed_with_warnings",
    "partial": "completed_with_warnings",
    "error": "failed",
    "cancelled": "stopped",
    "canceled": "stopped",
}
_ERROR_KEYS = frozenset(
    {"error", "errors", "exception", "exceptions", "traceback", "stacktrace", "stack_trace"}
)
_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "credentials",
    }
)
_FAILED_COMPONENT_STATUSES = frozenset({"failed", "error"})
_REDACTED_ERROR = "Redacted; review server logs."
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*([^\s,;]+)"
)
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")


def normalize_scan_status(status: Any, default: str = "unknown") -> str:
    """Return one stable lower-case status token."""
    if status is None:
        return default
    normalized = str(status).strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return default
    return _STATUS_ALIASES.get(normalized, normalized)


def is_successful_scan_status(status: Any) -> bool:
    """Return whether downstream independent work may follow this scan."""
    return normalize_scan_status(status) in SUCCESSFUL_SCAN_STATUSES


def _redact_string(value: str) -> str:
    value = _CREDENTIAL_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    value = _URL_USERINFO.sub(r"\1[redacted]@", value)
    # Bound persisted component payloads even if a driver returns a huge error.
    if len(value) > 4000:
        return value[:3997] + "..."
    return value


def redact_component_payload(value: Any) -> Any:
    """Recursively redact component errors and credential-shaped values.

    Component exceptions remain available in server logs.  Persisted/API
    payloads intentionally expose only a generic error so connection strings,
    paths, and driver details cannot escape through ``components_json``.
    """
    if isinstance(value, Mapping):
        status = normalize_scan_status(value.get("status"))
        failed = status in _FAILED_COMPONENT_STATUSES
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.strip().lower().replace("-", "_")
            if lowered in _SECRET_KEYS:
                redacted[key] = "[redacted]"
            elif (
                lowered in _ERROR_KEYS
                or lowered.endswith("_error")
                or lowered.endswith("_errors")
            ):
                redacted[key] = _REDACTED_ERROR
            elif failed and lowered in {"message", "log", "detail", "details", "reason"}:
                redacted[key] = _REDACTED_ERROR
            else:
                redacted[key] = redact_component_payload(item)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [redact_component_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def component_result(
    result: Any = None,
    *,
    requested: bool = True,
    required: bool = False,
) -> dict[str, Any]:
    """Normalize and redact one component result without discarding details."""
    if not requested:
        payload = dict(result) if isinstance(result, Mapping) else {}
        payload["status"] = "not_requested"
        payload["requested"] = False
        payload["required"] = bool(required)
        return redact_component_payload(payload)

    if isinstance(result, Mapping):
        payload = dict(result)
        status = normalize_scan_status(payload.get("status"), default="completed")
        definition_status = normalize_scan_status(payload.get("definition_status"))
        database_results = payload.get("databases")
        database_warning = isinstance(database_results, Mapping) and any(
            normalize_scan_status(item.get("status")) not in {"completed", "not_requested"}
            for item in database_results.values()
            if isinstance(item, Mapping)
        )
        if status == "completed" and (
            definition_status
            in {"skipped", "failed", "completed_with_warnings"}
            or database_warning
        ):
            status = "completed_with_warnings"
    else:
        payload = {} if result is None else {"result": result}
        status = "completed"

    payload["status"] = status
    payload["requested"] = True
    payload["required"] = bool(required)
    return redact_component_payload(payload)


def component_has_warning(component: Mapping[str, Any]) -> bool:
    """Return whether a non-core component should downgrade overall success."""
    status = normalize_scan_status(component.get("status"))
    requested = bool(component.get("requested", True))
    required = bool(component.get("required", False))
    if status == "completed":
        return False
    if status == "not_requested":
        return required
    if not requested and not required:
        return False
    return True


def terminal_status_for_components(components: Mapping[str, Mapping[str, Any]]) -> str:
    """Calculate the truthful terminal status after all components finish."""
    core = components.get("core") or {}
    core_status = normalize_scan_status(core.get("status"))
    if core_status == "stopped":
        return "stopped"
    if core_status not in SUCCESSFUL_SCAN_STATUSES:
        return "failed"
    if core_status == "completed_with_warnings":
        return "completed_with_warnings"
    return (
        "completed_with_warnings"
        if any(component_has_warning(item) for name, item in components.items() if name != "core")
        else "completed"
    )


def serialize_components(components: Mapping[str, Any] | None) -> str | None:
    """Serialize a redacted component map for ``scan_runs.components_json``."""
    if components is None:
        return None
    return json.dumps(
        redact_component_payload(components),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def parse_components(value: Any) -> dict[str, Any] | None:
    """Parse old/new component storage; legacy NULL rows remain readable."""
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        return redact_component_payload(value)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return redact_component_payload(parsed)


def finish_scan_run(
    db,
    scan_id: int,
    *,
    status: str,
    reports_scanned: int,
    sources_found: int,
    new_sources: int,
    changed_queries: int,
    broken_refs: int,
    components: Mapping[str, Any],
    log: str,
    finished_at: str | None = None,
) -> str:
    """Write every terminal field atomically, without reviving a stopped run.

    Returns the status now stored in the row.  A concurrent stop request wins
    because this update is restricted to rows that are still ``running``.
    """
    normalized_status = normalize_scan_status(status)
    if normalized_status not in TERMINAL_SCAN_STATUSES:
        raise ValueError(f"Invalid terminal scan status: {status!r}")
    finished = finished_at or datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE scan_runs
           SET finished_at = ?, reports_scanned = ?, sources_found = ?,
               new_sources = ?, changed_queries = ?, broken_refs = ?,
               status = ?, components_json = ?, log = ?
           WHERE id = ? AND LOWER(COALESCE(status, '')) = 'running'""",
        (
            finished,
            int(reports_scanned or 0),
            int(sources_found or 0),
            int(new_sources or 0),
            int(changed_queries or 0),
            int(broken_refs or 0),
            normalized_status,
            serialize_components(components),
            log or "",
            scan_id,
        ),
    )
    row = db.execute("SELECT status FROM scan_runs WHERE id = ?", (scan_id,)).fetchone()
    if row is None:
        raise LookupError(f"Scan run {scan_id} no longer exists")
    return normalize_scan_status(row["status"] if hasattr(row, "keys") else row[0])


def recover_interrupted_scan_runs(db, *, finished_at: str | None = None) -> int:
    """Mark scan rows orphaned by a service restart as stopped, idempotently."""
    finished = finished_at or datetime.now(timezone.utc).isoformat()
    note = "STOPPED: interrupted by restart"
    recovery_components = serialize_components(
        {
            "recovery": {
                "status": "stopped",
                "requested": True,
                "required": True,
                "reason_code": "interrupted_by_restart",
            }
        }
    )
    cursor = db.execute(
        """UPDATE scan_runs
           SET finished_at = ?, status = 'stopped',
               components_json = COALESCE(components_json, ?),
               log = CASE
                   WHEN log IS NULL OR TRIM(log) = '' THEN ?
                   ELSE log || char(10) || ?
               END
           WHERE LOWER(COALESCE(status, '')) = 'running'""",
        (finished, recovery_components, note, note),
    )
    return cursor.rowcount if cursor.rowcount != -1 else 0
