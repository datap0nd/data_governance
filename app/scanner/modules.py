"""Canonical scanner module definitions and durable per-module run records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from app.database import get_db
from app.scanner.lifecycle import normalize_scan_status, redact_component_payload
from app.scanner.diagnostics import with_diagnostic


MODULE_DEFINITIONS = (
    {
        "key": "power_bi_metadata",
        "label": "Power BI metadata",
        "description": (
            "Reads report, dataset, refresh-status, and refresh-schedule metadata "
            "from the configured Power BI workspace. Local report discovery can "
            "still run when this service sync is unavailable."
        ),
        "scans": "Power BI workspace reports, datasets, refresh history, and schedules.",
        "prerequisites": "A configured Power BI connection; otherwise this module is skipped.",
        "legacy_component": None,
    },
    {
        "key": "report_catalog",
        "label": "PBIX / TMDL catalog",
        "description": (
            "Discovers PBIX files and TMDL semantic-model exports together, then "
            "updates reports, tables, measures, visuals, and source expressions."
        ),
        "scans": "PBIX and TMDL definitions within four folders of the configured root.",
        "prerequisites": "The report root must be reachable and contain at least one valid report.",
        "legacy_component": "core",
    },
    {
        "key": "postgres_lineage",
        "label": "PostgreSQL lineage",
        "description": (
            "Uses read-only PostgreSQL catalog queries to map materialized-view "
            "dependencies and detect definition changes."
        ),
        "scans": "Required PostgreSQL databases, materialized views, dependencies, and definitions.",
        "prerequisites": "Read-only PostgreSQL credentials; stored catalog data is used if discovery is stale.",
        "legacy_component": "postgres_dependencies",
    },
    {
        "key": "postgres_schedules",
        "label": "PostgreSQL schedules",
        "description": "Reads pg_cron jobs and associates refresh schedules with materialized views.",
        "scans": "Read-only pg_cron schedule metadata for governed PostgreSQL sources.",
        "prerequisites": (
            "PGHOST, PGUSER, and PGPASSWORD; pg_cron installed; USAGE on schema cron "
            "and SELECT on cron.job. SELECT on cron.job_run_details adds run history."
        ),
        "legacy_component": "postgres_schedules",
    },
    {
        "key": "relation_samples",
        "label": "Relation samples",
        "description": (
            "Caches bounded, unordered row previews for PostgreSQL relations "
            "reachable from active report and Flow pipelines."
        ),
        "scans": "Pipeline-reachable PostgreSQL tables, views, foreign tables, and materialized views.",
        "prerequisites": "Exact PostgreSQL identities and a matching read-only catalog connection.",
        "legacy_component": None,
    },
    {
        "key": "pipeline_explanations",
        "label": "Pipeline explanations",
        "description": (
            "Uses the configured local Qwen model to write one-sentence explanations "
            "for PostgreSQL and Power BI lineage connections."
        ),
        "scans": "Pipeline dependency edges, SQL/TMDL definitions, schemas, and bounded row evidence.",
        "prerequisites": "Local AI mode, the Pipeline explanations feature, and read-only PostgreSQL access.",
        "legacy_component": None,
    },
    {
        "key": "source_freshness",
        "label": "Source freshness",
        "description": (
            "Checks file timestamps and PostgreSQL activity, evaluates freshness "
            "rules and data-quality checks, and updates operational alerts."
        ),
        "scans": "File sources, PostgreSQL sources, dependency freshness, and configured checks.",
        "prerequisites": "Uses the currently stored source graph, which may be older than the latest catalog attempt.",
        "legacy_component": "probe",
    },
    {
        "key": "governance",
        "label": "Governance checks",
        "description": (
            "Runs best-practice, schedule-discrepancy, and documentation-completeness "
            "checks independently and names any failed sub-check."
        ),
        "scans": "Report best practices, refresh-chain schedules, and documentation coverage.",
        "prerequisites": "Uses the currently stored reports and sources.",
        "legacy_component": "governance",
    },
    {
        "key": "usage_metadata",
        "label": "Usage metadata",
        "description": (
            "Imports configured usage CSV exports and synchronizes Power BI usage "
            "activity, reporting each sub-step separately."
        ),
        "scans": "Configured usage files and Power BI activity events.",
        "prerequisites": "Usage files are optional; Power BI activity requires a configured connection.",
        "legacy_component": "usage",
    },
)

MODULES_BY_KEY = {item["key"]: item for item in MODULE_DEFINITIONS}
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "failed", "stopped", "skipped", "not_requested"}
)
STALE_AFTER_SECONDS = 600
MAX_DETAILS_CHARS = 24000
MAX_LOG_CHARS = 24000


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _loads(value: Any) -> dict:
    if not value:
        return {}
    try:
        result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _dumps(value: Mapping[str, Any] | None) -> str:
    safe = redact_component_payload(dict(value or {}))
    encoded = json.dumps(
        safe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if len(encoded) <= MAX_DETAILS_CHARS:
        return encoded
    compact = {
        "status": safe.get("status") if isinstance(safe, Mapping) else None,
        "diagnostic": safe.get("diagnostic") if isinstance(safe, Mapping) else None,
        "details_truncated": True,
    }
    if isinstance(safe, Mapping):
        for key, item in safe.items():
            if key in compact or not isinstance(item, (type(None), bool, int, float)):
                continue
            compact[key] = item
    encoded = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return encoded[:MAX_DETAILS_CHARS]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize(row, *, now: datetime | None = None) -> dict:
    data = dict(row)
    data["details"] = _loads(data.pop("details_json", None))
    status = normalize_scan_status(data.get("status"))
    heartbeat = _parse_time(data.get("heartbeat_at") or data.get("started_at"))
    age = max(0, int(((now or datetime.now(timezone.utc)) - heartbeat).total_seconds())) if heartbeat else None
    stalled = bool(status in ACTIVE_STATUSES and age is not None and age >= STALE_AFTER_SECONDS)
    data["status"] = status
    data["display_status"] = "stalled" if stalled else status
    data["active"] = status in ACTIVE_STATUSES
    data["is_stalled"] = stalled
    data["heartbeat_age_seconds"] = age
    return data


def create_module_run(
    module_key: str,
    *,
    scanner_job_id: int | None,
    scan_run_id: int | None = None,
    trigger_source: str | None = None,
    status: str = "running",
    details: Mapping[str, Any] | None = None,
) -> int:
    if module_key not in MODULES_BY_KEY:
        raise ValueError(f"Unknown scanner module: {module_key}")
    if trigger_source is None and scanner_job_id is not None:
        with get_db() as db:
            job = db.execute(
                "SELECT trigger_source FROM scanner_jobs WHERE id=?", (int(scanner_job_id),)
            ).fetchone()
        trigger_source = job["trigger_source"] if job is not None else "system"
    now = _iso()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO scanner_module_runs
                   (scanner_job_id, scan_run_id, module_key, trigger_source,
                    status, details_json, started_at, heartbeat_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scanner_job_id,
                scan_run_id,
                module_key,
                trigger_source or "system",
                status,
                _dumps(details),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def heartbeat_module_run(module_run_id: int | None, *, details: Mapping[str, Any] | None = None) -> None:
    if module_run_id is None:
        return
    with get_db() as db:
        db.execute(
            """UPDATE scanner_module_runs
                  SET heartbeat_at=?, details_json=COALESCE(?, details_json)
                WHERE id=? AND status IN ('queued','running')""",
            (_iso(), _dumps(details) if details is not None else None, int(module_run_id)),
        )


def attach_scan_run(module_run_id: int | None, scan_run_id: int) -> None:
    if module_run_id is None:
        return
    with get_db() as db:
        db.execute(
            "UPDATE scanner_module_runs SET scan_run_id=? WHERE id=?",
            (int(scan_run_id), int(module_run_id)),
        )


def finish_module_run(
    module_run_id: int | None,
    *,
    status: str,
    summary: str | None = None,
    details: Mapping[str, Any] | None = None,
    log: str | None = None,
) -> dict | None:
    if module_run_id is None:
        return None
    normalized = normalize_scan_status(status)
    if normalized not in TERMINAL_STATUSES:
        normalized = "failed"
    with get_db() as db:
        module_row = db.execute(
            "SELECT module_key FROM scanner_module_runs WHERE id=?", (int(module_run_id),)
        ).fetchone()
        module_key = module_row["module_key"] if module_row is not None else "scanner_module"
        prepared_details = with_diagnostic(
            module_key,
            normalized,
            details,
            fallback_summary=summary,
        )
        diagnostic_summary = prepared_details["diagnostic"]["operator_summary"]
        safe_summary = redact_component_payload(
            diagnostic_summary if normalized != "completed" else (summary or diagnostic_summary)
        )
        safe_log = redact_component_payload(log)[:MAX_LOG_CHARS] if log else None
        db.execute(
            """UPDATE scanner_module_runs
                  SET status=?, summary=?, details_json=?, log=?,
                      heartbeat_at=?, finished_at=?
                WHERE id=? AND status IN ('queued','running')""",
            (
                normalized,
                safe_summary,
                _dumps(prepared_details),
                safe_log,
                _iso(),
                _iso(),
                int(module_run_id),
            ),
        )
        row = db.execute(
            "SELECT * FROM scanner_module_runs WHERE id=?", (int(module_run_id),)
        ).fetchone()
    return _serialize(row) if row is not None else None


def get_module_run(module_run_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM scanner_module_runs WHERE id=?", (int(module_run_id),)
        ).fetchone()
    return _serialize(row) if row is not None else None


def list_module_runs(module_key: str, *, limit: int = 20) -> list[dict]:
    if module_key not in MODULES_BY_KEY:
        raise ValueError(f"Unknown scanner module: {module_key}")
    bounded = max(1, min(int(limit), 100))
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM scanner_module_runs
                WHERE module_key=? ORDER BY id DESC LIMIT ?""",
            (module_key, bounded),
        ).fetchall()
    return [_serialize(row) for row in rows]


def runs_for_job(scanner_job_id: int) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM scanner_module_runs
                WHERE scanner_job_id=? ORDER BY id""",
            (int(scanner_job_id),),
        ).fetchall()
    return [_serialize(row) for row in rows]


def recover_interrupted_module_runs(*, finished_at: str | None = None) -> int:
    """Terminalize module rows orphaned by a service restart."""
    note = "STOPPED: interrupted by restart"
    with get_db() as db:
        rows = db.execute(
            "SELECT id FROM scanner_module_runs WHERE status IN ('queued','running')"
        ).fetchall()
    for row in rows:
        finish_module_run(
            int(row["id"]),
            status="stopped",
            summary=note,
            details={"status": "stopped", "reason_code": "interrupted_by_restart"},
        )
    if finished_at and rows:
        placeholders = ",".join("?" for _ in rows)
        with get_db() as db:
            db.execute(
                f"""UPDATE scanner_module_runs SET heartbeat_at=?, finished_at=?
                     WHERE id IN ({placeholders})""",
                (finished_at, finished_at, *(int(row["id"]) for row in rows)),
            )
    return len(rows)


def finish_active_runs_for_scan(
    scan_run_id: int,
    *,
    status: str,
    summary: str,
    details: Mapping[str, Any] | None = None,
) -> int:
    """Close any module left active when scan orchestration exits early."""
    with get_db() as db:
        rows = db.execute(
            """SELECT id FROM scanner_module_runs
                WHERE scan_run_id=? AND status IN ('queued','running')""",
            (int(scan_run_id),),
        ).fetchall()
    for row in rows:
        finish_module_run(
            int(row["id"]), status=status, summary=summary, details=details
        )
    return len(rows)


def module_definitions_with_runs() -> list[dict]:
    result = []
    with get_db() as db:
        for definition in MODULE_DEFINITIONS:
            rows = db.execute(
                """SELECT * FROM scanner_module_runs
                    WHERE module_key=? ORDER BY id DESC LIMIT 25""",
                (definition["key"],),
            ).fetchall()
            serialized = [_serialize(row) for row in rows]
            current = next((row for row in serialized if row["active"]), None)
            last = next((row for row in serialized if not row["active"]), None)
            result.append({**definition, "current_run": current, "last_run": last})
    return result


def mark_notification(
    module_run_ids: list[int],
    *,
    dispatch_id: int | None,
    status: str,
    error: str | None = None,
    stalled: bool = False,
) -> None:
    if not module_run_ids:
        return
    placeholders = ",".join("?" for _ in module_run_ids)
    values = [dispatch_id, status, redact_component_payload(error) if error else None]
    stalled_sql = ", stalled_notified_at=?" if stalled else ""
    if stalled:
        values.append(_iso())
    values.extend(int(item) for item in module_run_ids)
    with get_db() as db:
        db.execute(
            f"""UPDATE scanner_module_runs
                   SET notification_dispatch_id=?, notification_status=?,
                       notification_error=?{stalled_sql}
                 WHERE id IN ({placeholders})""",
            tuple(values),
        )


def reconcile_notification_dispatch(dispatch_id: int, status: str, error: str | None) -> None:
    with get_db() as db:
        db.execute(
            """UPDATE scanner_module_runs
                  SET notification_status=?, notification_error=?
                WHERE notification_dispatch_id=?""",
            (status, redact_component_payload(error) if error else None, int(dispatch_id)),
        )
