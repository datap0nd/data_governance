"""Fixed, read-only tools for evidence-backed Flow and Pipeline investigations."""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_db
from app.path_safety import is_remote_file_path

MAX_TOOL_RESULT_BYTES = 64 * 1024
MAX_ALERT_CONTEXT_BYTES = 56 * 1024
_SENSITIVE_CONTEXT_KEY = re.compile(
    r"(?i)(password|secret|token|authorization|api[_-]?key|connection|query|plan|path|folder|recipient)"
)


def _artifact_availability(value: Any) -> tuple[bool | None, str]:
    if is_remote_file_path(value):
        return None, "not_probed_remote"
    try:
        exists = Path(str(value)).is_file()
    except OSError:
        return None, "probe_failed"
    return exists, "available" if exists else "missing"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _loads(value: str | None, default):
    try:
        parsed = json.loads(value) if value else default
        return parsed
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any, limit: int = 1000, *, tail: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value)
    # Redact complete bearer/header values before the generic key matcher; if
    # "Authorization: Bearer ..." is split at whitespace first, the token can
    # otherwise survive after the word "Bearer".
    text = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\bauthorization\s*[=:]\s*(?:bearer|basic)?\s*[^\s,;}]+",
        "authorization=[redacted]",
        text,
    )
    # JSON/log payloads often quote both the sensitive key and value. Preserve
    # the shape for diagnosis while replacing the entire value.
    quoted_secret = re.compile(
        r"(?i)([\"'](?:password|passwd|token|secret|authorization|api[_ -]?key)[\"']\s*:\s*)"
        r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
    )
    text = quoted_secret.sub(
        lambda match: f"{match.group(1)}{match.group('quote')}[redacted]{match.group('quote')}",
        text,
    )
    text = re.sub(
        r"(?i)(password|passwd|token|secret|authorization|api[_ -]?key)\s*[=:]\s*([\"']).*?\2",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(password|passwd|token|secret|authorization|api[_ -]?key)\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)postgresql(?:\+\w+)?://[^\s]+", "postgresql://[redacted]", text)
    text = re.sub(
        r"([\"'])(?:[A-Za-z]:\\|\\\\)[^\"'\r\n]+\1",
        "[local path]",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\)[^\r\n<>\"']+",
        "[local path]",
        text,
    )
    text = text.strip()
    if len(text) <= limit:
        return text
    return ("…" + text[-limit:]) if tail else (text[:limit] + "…")


@dataclass(frozen=True)
class Evidence:
    reference: str
    entity_type: str
    entity_id: str
    label: str
    deep_link: str
    observed_at: str


@dataclass(frozen=True)
class ToolEnvelope:
    data: dict[str, Any]
    evidence: tuple[Evidence, ...]
    observed_at: str
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "data": self.data,
            "evidence": [asdict(item) for item in self.evidence],
            "observed_at": self.observed_at,
            "truncated": self.truncated,
        }


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlowRunArgs(_Args):
    run_id: int = Field(ge=1)


class FlowRunEventsArgs(_Args):
    run_id: int = Field(ge=1)
    limit: int = Field(default=30, ge=1, le=30)


class FlowRunArtifactsArgs(_Args):
    run_id: int = Field(ge=1)
    limit: int = Field(default=50, ge=1, le=50)


class CompareFlowRunsArgs(_Args):
    run_id: int = Field(ge=1)
    baseline_run_id: int | None = Field(default=None, ge=1)


class PipelineRunArgs(_Args):
    run_id: int = Field(ge=1)


class PipelineFlowRunArgs(_Args):
    pipeline_run_id: int = Field(ge=1)
    flow_run_id: int = Field(ge=1)


class PipelineFlowRunEventsArgs(PipelineFlowRunArgs):
    limit: int = Field(default=30, ge=1, le=30)


class PipelineFlowRunArtifactsArgs(PipelineFlowRunArgs):
    limit: int = Field(default=50, ge=1, le=50)


class AlertContextArgs(_Args):
    action_id: int = Field(ge=1)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], ToolEnvelope]
    progress_label: str
    focus_type: Literal["flow_run", "pipeline_run", "alert"]

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


def _public_eligibility(item: dict) -> dict:
    result = {
        "status": item["status"],
        "reason_code": item["reason_code"],
        "message": _safe_text(item["message"], 600),
    }
    if item.get("active_run_id"):
        result["active_run_id"] = item["active_run_id"]
    return result


def _get_flow_run(args: FlowRunArgs) -> ToolEnvelope:
    from app.routers.flows import (
        inspect_fresh_run_eligibility,
        inspect_resume_eligibility,
        inspect_sql_retry_eligibility,
    )

    observed = _iso()
    with get_db() as db:
        row = db.execute(
            """SELECT r.id, r.flow_id, r.trigger_type, r.status, r.requested_by,
                      r.worker_id, r.progress_json, r.error, r.created_at, r.claimed_at,
                      r.started_at, r.finished_at, r.heartbeat_at, r.job_json,
                      f.name AS flow_name, f.source_type
               FROM flow_runs r JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (args.run_id,),
        ).fetchone()
        if not row:
            raise LookupError("Flow run not found.")
        job = _loads(row["job_json"], {})
        progress = _loads(row["progress_json"], {})
        last_event = db.execute(
            """SELECT id, status, stage, message, error, created_at
               FROM flow_run_events WHERE run_id=? ORDER BY id DESC LIMIT 1""",
            (args.run_id,),
        ).fetchone()
        timings = db.execute(
            """SELECT phase, duration_ms, item_count, status, recorded_at
               FROM flow_operation_timings WHERE run_id=? ORDER BY id""",
            (args.run_id,),
        ).fetchall()
        linked_pipeline = db.execute(
            """SELECT run_id FROM pipeline_run_steps
               WHERE flow_run_id=? ORDER BY id DESC LIMIT 1""",
            (args.run_id,),
        ).fetchone()
        required_worker = (job.get("execution") or {}).get("worker_id")
        worker = None
        if required_worker:
            worker_row = db.execute(
                """SELECT worker_id, display_name, status, current_run_id, last_error, last_seen_at
                   FROM flow_workers WHERE worker_id=?""",
                (required_worker,),
            ).fetchone()
            if worker_row:
                worker = dict(worker_row)
                worker["registered"] = True
                cutoff = _now() - timedelta(seconds=90)
                try:
                    seen = datetime.fromisoformat(str(worker["last_seen_at"]).replace("Z", "+00:00"))
                    if seen.tzinfo is None:
                        seen = seen.replace(tzinfo=timezone.utc)
                    if seen < cutoff:
                        worker["status"] = "offline"
                except (TypeError, ValueError):
                    worker["status"] = "offline"
                worker["last_error"] = _safe_text(worker.get("last_error"), 500)
            else:
                worker = {
                    "worker_id": required_worker,
                    "display_name": None,
                    "registered": False,
                    "status": "not_registered",
                    "current_run_id": None,
                    "last_error": None,
                    "last_seen_at": None,
                }
        resume = inspect_resume_eligibility(db, args.run_id)
        retry_sql = inspect_sql_retry_eligibility(
            db, args.run_id, verify_remote_artifacts=False
        )
        fresh = inspect_fresh_run_eligibility(db, int(row["flow_id"]))

    sql = job.get("sql_handoff") or {}
    transformation = job.get("transformation") or {}
    data = {
        "run": {
            "id": int(row["id"]),
            "flow_id": int(row["flow_id"]),
            "flow_name": row["flow_name"],
            "source_type": row["source_type"] or "portal",
            "trigger_type": row["trigger_type"],
            "status": row["status"],
            "requested_by": row["requested_by"],
            "worker_id": row["worker_id"],
            "created_at": row["created_at"],
            "claimed_at": row["claimed_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "heartbeat_at": row["heartbeat_at"],
            "error": _safe_text(row["error"], 1500),
            "progress": {
                "stage": progress.get("stage"),
                "message": _safe_text(progress.get("message"), 500),
            },
        },
        "frozen_configuration": {
            "browser_mode": (job.get("execution") or {}).get("browser_mode"),
            "download_mode": (job.get("downloads") or {}).get("mode"),
            "period_count": len((job.get("downloads") or {}).get("periods") or []),
            "export_view_count": len((job.get("report") or {}).get("export_views") or []),
            "transformation_enabled": bool(transformation.get("enabled")),
            "sql_handoff": {
                "enabled": bool(sql.get("enabled")),
                "mode": sql.get("mode"),
                "database": sql.get("database"),
                "schema": sql.get("schema"),
                "table": sql.get("table"),
            },
        },
        "last_event": (
            {
                "id": int(last_event["id"]),
                "status": last_event["status"],
                "stage": last_event["stage"],
                "message": _safe_text(last_event["message"], 500),
                "error": _safe_text(last_event["error"], 800),
                "created_at": last_event["created_at"],
            }
            if last_event else None
        ),
        "timings": [dict(item) for item in timings[:40]],
        "required_worker": worker,
        "linked_pipeline_run_id": int(linked_pipeline["run_id"]) if linked_pipeline else None,
        "recovery_preflight": {
            "resume": _public_eligibility(resume),
            "retry_sql": _public_eligibility(retry_sql),
            "run_fresh": _public_eligibility(fresh),
            "note": "These are read-only snapshots. The normal endpoint revalidates before any operation is queued.",
        },
    }
    evidence = (
        Evidence(
            reference=f"flow_run:{args.run_id}",
            entity_type="flow_run",
            entity_id=str(args.run_id),
            label=f"Flow run #{args.run_id}: {row['flow_name']}",
            deep_link=f"/flow-runs/{args.run_id}",
            observed_at=observed,
        ),
    )
    return ToolEnvelope(data=data, evidence=evidence, observed_at=observed)


def _get_flow_run_events(args: FlowRunEventsArgs) -> ToolEnvelope:
    observed = _iso()
    with get_db() as db:
        run = db.execute(
            """SELECT r.id, f.name AS flow_name FROM flow_runs r
               JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (args.run_id,),
        ).fetchone()
        if not run:
            raise LookupError("Flow run not found.")
        rows = db.execute(
            """SELECT id, status, stage, message, error, traceback, created_at
               FROM flow_run_events WHERE run_id=? ORDER BY id DESC LIMIT ?""",
            (args.run_id, args.limit),
        ).fetchall()
    rows = list(reversed(rows))
    events = []
    evidence = [
        Evidence(
            reference=f"flow_run:{args.run_id}", entity_type="flow_run",
            entity_id=str(args.run_id), label=f"Flow run #{args.run_id}: {run['flow_name']}",
            deep_link=f"/flow-runs/{args.run_id}", observed_at=observed,
        )
    ]
    for row in rows:
        ref = f"flow_run_event:{row['id']}"
        events.append({
            "id": int(row["id"]), "status": row["status"], "stage": row["stage"],
            "message": _safe_text(row["message"], 400),
            "error": _safe_text(row["error"], 700),
            "traceback_tail": _safe_text(row["traceback"], 1000, tail=True),
            "created_at": row["created_at"], "evidence_ref": ref,
        })
        evidence.append(Evidence(
            reference=ref, entity_type="flow_run_event", entity_id=str(row["id"]),
            label=f"Run #{args.run_id} event {row['id']}: {row['stage'] or row['status']}",
            deep_link=f"/flow-runs/{args.run_id}", observed_at=observed,
        ))
    return ToolEnvelope(
        data={"run_id": args.run_id, "events": events, "returned": len(events)},
        evidence=tuple(evidence), observed_at=observed,
    )


def _get_flow_run_artifacts(args: FlowRunArtifactsArgs) -> ToolEnvelope:
    from app.routers.flows import inspect_resume_eligibility, inspect_sql_retry_eligibility

    observed = _iso()
    with get_db() as db:
        run = db.execute(
            """SELECT r.id, r.artifact_json, r.folder_state, f.name AS flow_name
               FROM flow_runs r JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (args.run_id,),
        ).fetchone()
        if not run:
            raise LookupError("Flow run not found.")
        files = db.execute(
            """SELECT id, period_key, file_path, filename, file_size, checksum,
                      row_count, status, created_at
               FROM flow_run_files WHERE run_id=? ORDER BY id LIMIT ?""",
            (args.run_id, args.limit),
        ).fetchall()
        resume = inspect_resume_eligibility(db, args.run_id)
        retry_sql = inspect_sql_retry_eligibility(
            db, args.run_id, verify_remote_artifacts=False
        )
    artifacts = []
    evidence = [Evidence(
        reference=f"flow_run:{args.run_id}", entity_type="flow_run",
        entity_id=str(args.run_id), label=f"Flow run #{args.run_id}: {run['flow_name']}",
        deep_link=f"/flow-runs/{args.run_id}", observed_at=observed,
    )]
    for row in files:
        ref = f"flow_run_file:{row['id']}"
        file_still_exists, availability = _artifact_availability(row["file_path"])
        artifacts.append({
            "id": int(row["id"]), "period_key": row["period_key"],
            "filename": _safe_text(row["filename"], 300), "file_size": row["file_size"],
            "checksum": row["checksum"], "row_count": row["row_count"],
            "status": row["status"], "file_still_exists": file_still_exists,
            "availability": availability,
            "created_at": row["created_at"], "evidence_ref": ref,
        })
        evidence.append(Evidence(
            reference=ref, entity_type="flow_run_file", entity_id=str(row["id"]),
            label=f"Run #{args.run_id} file: {row['filename']}",
            deep_link=f"/flow-runs/{args.run_id}", observed_at=observed,
        ))
    return ToolEnvelope(
        data={
            "run_id": args.run_id,
            "folder_state": run["folder_state"],
            "recorded_files": artifacts,
            "artifact_record_count": len(_loads(run["artifact_json"], [])),
            "recovery_preflight": {
                "resume": _public_eligibility(resume),
                "retry_sql": _public_eligibility(retry_sql),
            },
        },
        evidence=tuple(evidence), observed_at=observed,
    )


def _run_comparison_projection(db, row) -> dict:
    job = _loads(row["job_json"], {})
    event = db.execute(
        """SELECT stage, status, created_at FROM flow_run_events
           WHERE run_id=? ORDER BY id DESC LIMIT 1""",
        (row["id"],),
    ).fetchone()
    timings = db.execute(
        """SELECT phase, duration_ms, item_count, status
           FROM flow_operation_timings WHERE run_id=? ORDER BY id""",
        (row["id"],),
    ).fetchall()
    files = db.execute(
        """SELECT COUNT(*) AS file_count, COALESCE(SUM(row_count), 0) AS row_count
           FROM flow_run_files WHERE run_id=?""",
        (row["id"],),
    ).fetchone()
    sql = job.get("sql_handoff") or {}
    selections = job.get("selections") or {}
    safe_selections = {}
    if isinstance(selections, dict):
        for key, value in list(selections.items())[:30]:
            safe_key = _safe_text(key, 100)
            if not safe_key:
                continue
            values = value if isinstance(value, list) else [value]
            safe_selections[safe_key] = [
                _safe_text(item, 120) for item in values[:20]
            ]
    export_views = (job.get("report") or {}).get("export_views") or []
    return {
        "id": int(row["id"]), "status": row["status"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "error": _safe_text(row["error"], 900),
        "last_stage": event["stage"] if event else None,
        "timings": [dict(item) for item in timings[:40]],
        "files": int(files["file_count"] or 0), "rows": int(files["row_count"] or 0),
        "configuration": {
            "source_type": (job.get("flow") or {}).get("source_type"),
            "browser_mode": (job.get("execution") or {}).get("browser_mode"),
            "download_mode": (job.get("downloads") or {}).get("mode"),
            "selections": safe_selections,
            "export_views": [_safe_text(item, 160) for item in export_views[:30]],
            "transformation_enabled": bool((job.get("transformation") or {}).get("enabled")),
            "sql_target": {
                "enabled": bool(sql.get("enabled")), "mode": sql.get("mode"),
                "database": sql.get("database"), "schema": sql.get("schema"),
                "table": sql.get("table"),
            },
        },
    }


def _compare_flow_runs(args: CompareFlowRunsArgs) -> ToolEnvelope:
    observed = _iso()
    with get_db() as db:
        current = db.execute(
            """SELECT r.*, f.name AS flow_name FROM flow_runs r
               JOIN flows f ON f.id=r.flow_id WHERE r.id=?""",
            (args.run_id,),
        ).fetchone()
        if not current:
            raise LookupError("Flow run not found.")
        if args.baseline_run_id is None:
            baseline = db.execute(
                """SELECT * FROM flow_runs WHERE flow_id=? AND id<? AND status='succeeded'
                   ORDER BY id DESC LIMIT 1""",
                (current["flow_id"], args.run_id),
            ).fetchone()
        else:
            baseline = db.execute(
                "SELECT * FROM flow_runs WHERE id=?", (args.baseline_run_id,)
            ).fetchone()
            if baseline and int(baseline["flow_id"]) != int(current["flow_id"]):
                raise ValueError("The comparison run belongs to a different Flow.")
            if baseline and (
                int(baseline["id"]) >= int(current["id"])
                or baseline["status"] != "succeeded"
            ):
                raise ValueError(
                    "The comparison baseline must be an earlier successful run of this Flow."
                )
        if not baseline:
            raise LookupError("No earlier successful run is available for comparison.")
        current_data = _run_comparison_projection(db, current)
        baseline_data = _run_comparison_projection(db, baseline)
    changed = [
        key for key in current_data["configuration"]
        if current_data["configuration"].get(key) != baseline_data["configuration"].get(key)
    ]
    evidence = tuple(
        Evidence(
            reference=f"flow_run:{row['id']}", entity_type="flow_run", entity_id=str(row["id"]),
            label=f"Flow run #{row['id']}: {current['flow_name']}",
            deep_link=f"/flow-runs/{row['id']}", observed_at=observed,
        )
        for row in (current, baseline)
    )
    return ToolEnvelope(
        data={"current": current_data, "baseline": baseline_data, "changed_configuration_fields": changed},
        evidence=evidence, observed_at=observed,
    )


def _get_pipeline_run(args: PipelineRunArgs) -> ToolEnvelope:
    from app.routers.pipelines import _worker_readiness

    observed = _iso()
    with get_db() as db:
        run = db.execute(
            """SELECT pr.id, pr.report_id, pr.status, pr.stage, pr.trigger_type,
                      pr.requested_by, pr.error, pr.requires_inspection,
                      pr.notification_status, pr.notification_error,
                      pr.created_at, pr.started_at, pr.finished_at, pr.updated_at,
                      pr.plan_json, r.name AS report_name
               FROM pipeline_runs pr JOIN reports r ON r.id=pr.report_id WHERE pr.id=?""",
            (args.run_id,),
        ).fetchone()
        if not run:
            raise LookupError("Pipeline run not found.")
        steps = db.execute(
            """SELECT id, step_type, sequence_no, entity_type, entity_id, entity_name,
                      status, flow_run_id, started_at, finished_at, duration_ms,
                      row_count, row_count_status, error
               FROM pipeline_run_steps WHERE run_id=? ORDER BY sequence_no, id LIMIT 50""",
            (args.run_id,),
        ).fetchall()
        locks = db.execute(
            """SELECT resource_type, resource_key FROM pipeline_resource_locks
               WHERE run_id=? ORDER BY resource_type, resource_key""",
            (args.run_id,),
        ).fetchall()
        plan = _loads(run["plan_json"], {})
        worker_modes = {
            item.get("browser_mode")
            for item in (plan.get("flows") or [])
            if item.get("browser_mode") in {"headless", "headed"}
        }
        workers = _worker_readiness(db, worker_modes)
    data = {
        "run": {
            "id": int(run["id"]), "report_id": int(run["report_id"]),
            "report_name": run["report_name"], "status": run["status"],
            "stage": run["stage"], "trigger_type": run["trigger_type"],
            "requested_by": run["requested_by"], "error": _safe_text(run["error"], 1500),
            "requires_inspection": bool(run["requires_inspection"]),
            "notification_status": run["notification_status"],
            "notification_error": _safe_text(run["notification_error"], 800),
            "notification_semantics": "submitted means handed to Outlook; it is not proof of delivery",
            "created_at": run["created_at"], "started_at": run["started_at"],
            "finished_at": run["finished_at"], "updated_at": run["updated_at"],
        },
        "plan_summary": {
            "flow_count": len(plan.get("flows") or []),
            "materialized_view_count": len(plan.get("materialized_views") or []),
            "blockers": [_safe_text(item, 500) for item in (plan.get("blockers") or [])[:20]],
            "warnings": [_safe_text(item, 500) for item in (plan.get("warnings") or [])[:20]],
        },
        "steps": [
            {
                "id": int(step["id"]), "step_type": step["step_type"],
                "sequence_no": step["sequence_no"], "entity_type": step["entity_type"],
                "entity_id": step["entity_id"], "entity_name": step["entity_name"],
                "status": step["status"], "flow_run_id": step["flow_run_id"],
                "started_at": step["started_at"], "finished_at": step["finished_at"],
                "duration_ms": step["duration_ms"], "row_count": step["row_count"],
                "row_count_status": step["row_count_status"],
                "error": _safe_text(step["error"], 800),
                "evidence_ref": f"pipeline_step:{step['id']}",
            }
            for step in steps
        ],
        "resource_locks": [
            {
                "resource_type": item["resource_type"],
                "resource_key": _safe_text(item["resource_key"], 500),
            }
            for item in locks[:50]
        ],
        "worker_readiness": workers,
    }
    evidence = [Evidence(
        reference=f"pipeline_run:{args.run_id}", entity_type="pipeline_run",
        entity_id=str(args.run_id), label=f"Pipeline run #{args.run_id}: {run['report_name']}",
        deep_link="/#lineage", observed_at=observed,
    )]
    evidence.extend(
        Evidence(
            reference=f"pipeline_step:{step['id']}", entity_type="pipeline_step",
            entity_id=str(step["id"]),
            label=f"Pipeline #{args.run_id} step {step['sequence_no']}: {step['step_type']}",
            deep_link="/#lineage", observed_at=observed,
        )
        for step in steps
    )
    return ToolEnvelope(data=data, evidence=tuple(evidence), observed_at=observed)


def _pipeline_flow_context(args: PipelineFlowRunArgs):
    with get_db() as db:
        linked = db.execute(
            """SELECT 1 FROM pipeline_run_steps
               WHERE run_id=? AND flow_run_id=? LIMIT 1""",
            (args.pipeline_run_id, args.flow_run_id),
        ).fetchone()
        pipeline = db.execute(
            """SELECT pr.id, r.name AS report_name
               FROM pipeline_runs pr JOIN reports r ON r.id=pr.report_id
               WHERE pr.id=?""",
            (args.pipeline_run_id,),
        ).fetchone()
    if not pipeline:
        raise LookupError("Pipeline run not found.")
    if not linked:
        raise ValueError("That Flow run is not linked to the focused Pipeline run.")
    return pipeline


def _pipeline_flow_envelope(
    args: PipelineFlowRunArgs,
    *,
    pipeline,
    child: ToolEnvelope,
    data_key: str,
) -> ToolEnvelope:
    observed = _iso()
    pipeline_evidence = Evidence(
        reference=f"pipeline_run:{args.pipeline_run_id}",
        entity_type="pipeline_run",
        entity_id=str(args.pipeline_run_id),
        label=f"Pipeline run #{args.pipeline_run_id}: {pipeline['report_name']}",
        deep_link="/#lineage",
        observed_at=observed,
    )
    return ToolEnvelope(
        data={
            "pipeline_run_id": args.pipeline_run_id,
            data_key: child.data,
        },
        evidence=(pipeline_evidence, *child.evidence),
        observed_at=observed,
    )


def _get_pipeline_flow_run(args: PipelineFlowRunArgs) -> ToolEnvelope:
    """Read one Flow run only when a step links it to the focused Pipeline."""
    pipeline = _pipeline_flow_context(args)
    child = _get_flow_run(FlowRunArgs(run_id=args.flow_run_id))
    return _pipeline_flow_envelope(
        args, pipeline=pipeline, child=child, data_key="linked_flow_run"
    )


def _get_pipeline_flow_run_events(args: PipelineFlowRunEventsArgs) -> ToolEnvelope:
    pipeline = _pipeline_flow_context(args)
    child = _get_flow_run_events(
        FlowRunEventsArgs(run_id=args.flow_run_id, limit=args.limit)
    )
    return _pipeline_flow_envelope(
        args, pipeline=pipeline, child=child, data_key="linked_flow_events"
    )


def _get_pipeline_flow_run_artifacts(args: PipelineFlowRunArtifactsArgs) -> ToolEnvelope:
    pipeline = _pipeline_flow_context(args)
    child = _get_flow_run_artifacts(
        FlowRunArtifactsArgs(run_id=args.flow_run_id, limit=args.limit)
    )
    return _pipeline_flow_envelope(
        args, pipeline=pipeline, child=child, data_key="linked_flow_artifacts"
    )


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[depth-limited]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, 500)
    if isinstance(value, dict):
        return {
            str(key)[:80]: _safe_context_value(item, depth=depth + 1)
            for key, item in list(value.items())[:30]
            if not _SENSITIVE_CONTEXT_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_context_value(item, depth=depth + 1) for item in value[:20]]
    return _safe_text(value, 500)


def _safe_rows(
    rows,
    fields: tuple[str, ...],
    *,
    limit: int = 20,
    text_limit: int = 500,
) -> list[dict[str, Any]]:
    """Project database rows through one bounded redaction boundary."""
    result: list[dict[str, Any]] = []
    for row in list(rows)[:limit]:
        item: dict[str, Any] = {}
        for field in fields:
            if field not in row.keys():
                continue
            value = row[field]
            item[field] = _safe_text(value, text_limit) if isinstance(value, str) else value
        result.append(item)
    return result


def _context_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_freshness_profile(
    source: Any,
    probes: list[Any],
    activity: list[Any],
    linked_flows: list[Any],
) -> dict[str, Any]:
    """Build deterministic cadence evidence; never infer business meaning.

    The model may explain these measurements, but words such as ``mapping`` or
    ``master`` remain weak naming hints rather than proof of update cadence.
    """
    now = _now()
    data_dates: list[datetime] = []
    for row in [*activity, *probes]:
        parsed = _context_datetime(row["last_data_at"])
        if parsed and parsed not in data_dates:
            data_dates.append(parsed)
    data_dates.sort()
    intervals = [
        round((later - earlier).total_seconds() / 86400, 1)
        for earlier, later in zip(data_dates, data_dates[1:])
        if later > earlier
    ]
    latest_data = data_dates[-1] if data_dates else None
    current_age = (
        round((now - latest_data).total_seconds() / 86400, 1)
        if latest_data else None
    )
    rule_type = str(source["freshness_rule_type"] or "").strip() or (
        "custom" if source["custom_fresh_days"] is not None else "none"
    )
    threshold = (
        int(source["custom_fresh_days"])
        if source["custom_fresh_days"] is not None else None
    )
    median_interval = round(statistics.median(intervals), 1) if intervals else None
    maximum_interval = max(intervals) if intervals else None
    cadence_ratio = (
        round(median_interval / threshold, 2)
        if median_interval is not None and threshold and threshold > 0 else None
    )
    if len(data_dates) < 3:
        rule_fit = "insufficient_history"
    elif rule_type == "fixed_schedule":
        rule_fit = "compare_with_explicit_schedule"
    elif cadence_ratio is not None and cadence_ratio > 1.5:
        rule_fit = "possible_rule_too_strict"
    elif cadence_ratio is not None:
        rule_fit = "historical_cadence_within_rule"
    else:
        rule_fit = "rule_not_comparable"

    lowered_name = str(source["name"] or "").casefold()
    semantic_hints = sorted({
        token for token in (
            "mapping", "master", "reference", "lookup", "dimension", "calendar"
        ) if token in lowered_name
    })
    failed_flows = [
        {
            "id": row["id"],
            "name": _safe_text(row["name"], 200),
            "last_status": row["last_status"],
            "last_error": _safe_text(row["last_error"], 500),
        }
        for row in linked_flows
        if str(row["last_status"] or "").casefold() in {"failed", "error", "timed_out"}
    ]
    return {
        "configured_rule": {
            "type": rule_type,
            "fresh_days": threshold,
            "stale_days": source["custom_stale_days"],
            "schedule_days": _safe_text(source["freshness_schedule_days"], 200),
            "declared_refresh_schedule": _safe_text(source["refresh_schedule"], 300),
        },
        "current_age_days": current_age,
        "history": {
            "distinct_change_points": len(data_dates),
            "coverage_days": (
                round((data_dates[-1] - data_dates[0]).total_seconds() / 86400, 1)
                if len(data_dates) > 1 else 0
            ),
            "change_dates": [value.isoformat() for value in data_dates[-24:]],
            "recent_intervals_days": intervals[-23:],
            "median_interval_days": median_interval,
            "maximum_interval_days": maximum_interval,
        },
        "rule_fit_signal": rule_fit,
        "cadence_to_rule_ratio": cadence_ratio,
        "name_semantic_hints": semantic_hints,
        "naming_hint_warning": (
            "Name hints are weak inference only; do not call this a master or reference source without corroborating cadence, schedule, notes, or prior resolution evidence."
        ),
        "linked_flow_failures": failed_flows[:5],
        "interpretation_guardrail": (
            "Suggest reviewing a freshness rule only when measured cadence, an explicit schedule, or prior operator evidence supports it. Never suppress or change the rule automatically."
        ),
    }


def _get_alert_context(args: AlertContextArgs) -> ToolEnvelope:
    """Read one canonical Alert and a broad but safe operational neighbourhood.

    This is deliberately a server-defined projection rather than arbitrary SQL.
    It excludes credentials, connection strings, query/file contents, local
    paths, email recipients, and raw execution plans while retaining the facts
    needed to assess whether the Alert is supported by current evidence.
    """
    observed = _iso()
    with get_db() as db:
        action = db.execute(
            """SELECT a.id, a.type, a.status, a.assigned_to, a.notes,
                      a.source_id, a.report_id, a.flow_id, a.check_id,
                      a.scheduled_task_id, a.script_id, a.fingerprint,
                      a.evidence_revision, a.created_at, a.updated_at,
                      s.name AS source_name, s.type AS source_type,
                      r.name AS report_name, r.owner AS report_owner,
                      f.name AS flow_name, f.source_type AS flow_source_type,
                      st.task_name, st.status AS task_status,
                      sc.display_name AS script_name
                 FROM actions a
                 LEFT JOIN sources s ON s.id=a.source_id
                 LEFT JOIN reports r ON r.id=a.report_id
                 LEFT JOIN flows f ON f.id=a.flow_id
                 LEFT JOIN scheduled_tasks st ON st.id=a.scheduled_task_id
                 LEFT JOIN scripts sc ON sc.id=a.script_id
                WHERE a.id=?""",
            (args.action_id,),
        ).fetchone()
        if not action:
            raise LookupError("Alert not found.")

        revision = int(action["evidence_revision"] or 0)
        occurrence = db.execute(
            """SELECT id, evidence_revision, focus_type, focus_id, summary,
                      evidence_json, observed_at, created_at
                 FROM action_occurrences
                WHERE action_id=? AND evidence_revision=?
                ORDER BY id DESC LIMIT 1""",
            (args.action_id, revision),
        ).fetchone()
        occurrence_data = None
        if occurrence:
            raw_evidence = _loads(occurrence["evidence_json"], {})
            occurrence_data = {
                "id": int(occurrence["id"]),
                "evidence_revision": int(occurrence["evidence_revision"]),
                "focus_type": occurrence["focus_type"],
                "focus_id": occurrence["focus_id"],
                "summary": _safe_text(occurrence["summary"], 500),
                "evidence": (
                    _safe_context_value(raw_evidence)
                    if isinstance(raw_evidence, dict) else {}
                ),
                "observed_at": occurrence["observed_at"],
            }

        source_context = None
        if action["source_id"] is not None:
            source = db.execute(
                """SELECT id, name, type, owner, refresh_schedule,
                          custom_fresh_days, custom_stale_days,
                          freshness_rule_type, freshness_schedule_days,
                          archived, updated_at
                     FROM sources WHERE id=?""",
                (action["source_id"],),
            ).fetchone()
            probes = db.execute(
                """SELECT id, probed_at, last_data_at, row_count, status, message
                     FROM source_probes WHERE source_id=?
                     ORDER BY probed_at DESC, id DESC LIMIT 12""",
                (action["source_id"],),
            ).fetchall()
            activity = db.execute(
                """SELECT id, observed_at, last_data_at, row_count, status
                     FROM source_activity_history WHERE source_id=?
                     ORDER BY last_data_at DESC, id DESC LIMIT 36""",
                (action["source_id"],),
            ).fetchall()
            linked_flows = db.execute(
                """SELECT id, name, enabled, source_type, schedule_type,
                          schedule_time, schedule_days, schedule_day,
                          last_run_at, last_success_at, last_status, last_error
                     FROM flows
                    WHERE sql_target_source_id=?
                    ORDER BY enabled DESC, name LIMIT 10""",
                (action["source_id"],),
            ).fetchall()
            prior_alerts = db.execute(
                """SELECT id, type, status, notes, created_at, updated_at, resolved_at
                     FROM actions
                    WHERE source_id=? AND id<>?
                    ORDER BY updated_at DESC, id DESC LIMIT 10""",
                (action["source_id"], args.action_id),
            ).fetchall()
            linked_reports = db.execute(
                """SELECT DISTINCT r.id, r.name, r.owner, r.pbi_last_refresh_at,
                          r.pbi_refresh_status, r.pbi_refresh_error
                     FROM report_tables rt JOIN reports r ON r.id=rt.report_id
                    WHERE rt.source_id=? AND COALESCE(r.archived, 0)=0
                    ORDER BY r.name LIMIT 20""",
                (action["source_id"],),
            ).fetchall()
            upstream = db.execute(
                """SELECT s.id, s.name, s.type
                     FROM source_dependencies sd JOIN sources s ON s.id=sd.depends_on_id
                    WHERE sd.source_id=? ORDER BY s.name LIMIT 20""",
                (action["source_id"],),
            ).fetchall()
            downstream = db.execute(
                """SELECT s.id, s.name, s.type
                     FROM source_dependencies sd JOIN sources s ON s.id=sd.source_id
                    WHERE sd.depends_on_id=? ORDER BY s.name LIMIT 20""",
                (action["source_id"],),
            ).fetchall()
            source_context = {
                "source": _safe_rows([source], tuple(source.keys()), limit=1)[0] if source else None,
                "freshness_profile": _source_freshness_profile(
                    source, list(probes), list(activity), list(linked_flows)
                ) if source else None,
                "latest_probes": _safe_rows(
                    probes, ("id", "probed_at", "last_data_at", "row_count", "status", "message"), limit=12
                ),
                "activity_history": _safe_rows(
                    activity, ("id", "observed_at", "last_data_at", "row_count", "status"), limit=36
                ),
                "linked_flows": _safe_rows(
                    linked_flows,
                    ("id", "name", "enabled", "source_type", "schedule_type",
                     "schedule_time", "schedule_days", "schedule_day", "last_run_at",
                     "last_success_at", "last_status", "last_error"),
                    limit=10,
                ),
                "prior_alerts": _safe_rows(
                    prior_alerts,
                    ("id", "type", "status", "notes", "created_at", "updated_at", "resolved_at"),
                    limit=10,
                ),
                "linked_reports": _safe_rows(
                    linked_reports,
                    ("id", "name", "owner", "pbi_last_refresh_at", "pbi_refresh_status", "pbi_refresh_error"),
                ),
                "upstream_sources": _safe_rows(upstream, ("id", "name", "type")),
                "downstream_sources": _safe_rows(downstream, ("id", "name", "type")),
            }

        report_context = None
        if action["report_id"] is not None:
            report = db.execute(
                """SELECT id, name, owner, business_owner, frequency,
                          last_published, pbi_last_refresh_at, pbi_refresh_status,
                          pbi_refresh_error, archived, updated_at
                     FROM reports WHERE id=?""",
                (action["report_id"],),
            ).fetchone()
            tables = db.execute(
                """SELECT rt.id, rt.table_name, rt.last_scanned,
                          s.id AS source_id, s.name AS source_name, s.type AS source_type,
                          sp.status AS source_status, sp.last_data_at
                     FROM report_tables rt
                     LEFT JOIN sources s ON s.id=rt.source_id
                     LEFT JOIN source_probes sp ON sp.id=(
                         SELECT sp2.id FROM source_probes sp2
                          WHERE sp2.source_id=s.id ORDER BY sp2.probed_at DESC, sp2.id DESC LIMIT 1
                     )
                    WHERE rt.report_id=? ORDER BY rt.table_name LIMIT 30""",
                (action["report_id"],),
            ).fetchall()
            latest_pipeline = db.execute(
                """SELECT id, status, stage, trigger_type, error, requires_inspection,
                          notification_status, created_at, started_at, finished_at, updated_at
                     FROM pipeline_runs WHERE report_id=? ORDER BY id DESC LIMIT 1""",
                (action["report_id"],),
            ).fetchone()
            report_context = {
                "report": _safe_rows([report], tuple(report.keys()), limit=1)[0] if report else None,
                "tables_and_sources": _safe_rows(
                    tables,
                    ("id", "table_name", "last_scanned", "source_id", "source_name", "source_type", "source_status", "last_data_at"),
                    limit=30,
                ),
                "latest_pipeline_run": (
                    _safe_rows([latest_pipeline], tuple(latest_pipeline.keys()), limit=1)[0]
                    if latest_pipeline else None
                ),
            }

        flow_context = None
        if action["flow_id"] is not None:
            flow = db.execute(
                """SELECT id, name, source_type, enabled, filename_template,
                          schedule_type, schedule_time, schedule_days, schedule_day,
                          last_run_at, last_success_at, last_status, last_error,
                          transform_enabled, sql_handoff_enabled, sql_mode,
                          sql_database, sql_schema, sql_table, sql_target_source_id,
                          updated_at
                     FROM flows WHERE id=?""",
                (action["flow_id"],),
            ).fetchone()
            latest_runs = db.execute(
                """SELECT id, trigger_type, status, requested_by, worker_id,
                          error, created_at, started_at, finished_at, heartbeat_at
                     FROM flow_runs WHERE flow_id=? ORDER BY id DESC LIMIT 5""",
                (action["flow_id"],),
            ).fetchall()
            flow_context = {
                "flow": _safe_rows([flow], tuple(flow.keys()), limit=1)[0] if flow else None,
                "latest_runs": _safe_rows(
                    latest_runs,
                    ("id", "trigger_type", "status", "requested_by", "worker_id", "error", "created_at", "started_at", "finished_at", "heartbeat_at"),
                    limit=5,
                ),
            }

        check_context = None
        if action["check_id"] is not None:
            check = db.execute(
                "SELECT id, name, source_id, type, severity, enabled, updated_at FROM checks WHERE id=?",
                (action["check_id"],),
            ).fetchone()
            check_results = db.execute(
                """SELECT id, ran_at, status, value, message
                     FROM check_results WHERE check_id=? ORDER BY ran_at DESC, id DESC LIMIT 5""",
                (action["check_id"],),
            ).fetchall()
            check_context = {
                "check": _safe_rows([check], tuple(check.keys()), limit=1)[0] if check else None,
                "latest_results": _safe_rows(
                    check_results, ("id", "ran_at", "status", "value", "message"), limit=5
                ),
            }

        latest_scan = db.execute(
            """SELECT id, started_at, finished_at, reports_scanned, sources_found,
                      new_sources, broken_refs, status, log
                 FROM scan_runs ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        latest_probe_run = db.execute(
            """SELECT id, started_at, finished_at, sources_probed, fresh, stale,
                      outdated, unknown, status, log
                 FROM probe_runs ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        latest_pbi_sync = db.execute(
            """SELECT id, sync_type, status, started_at, finished_at, message
                 FROM pbi_sync_runs ORDER BY id DESC LIMIT 1"""
        ).fetchone()

    action_data = {
        "id": int(action["id"]),
        "type": action["type"],
        "status": action["status"],
        "assigned_to": _safe_text(action["assigned_to"], 200),
        "notes": _safe_text(action["notes"], 1600),
        "evidence_revision": revision,
        "created_at": action["created_at"],
        "updated_at": action["updated_at"],
        "asset": {
            "source_id": action["source_id"], "source_name": _safe_text(action["source_name"], 300),
            "report_id": action["report_id"], "report_name": _safe_text(action["report_name"], 300),
            "flow_id": action["flow_id"], "flow_name": _safe_text(action["flow_name"], 300),
            "scheduled_task_id": action["scheduled_task_id"], "task_name": _safe_text(action["task_name"], 300),
            "script_id": action["script_id"], "script_name": _safe_text(action["script_name"], 300),
        },
    }
    data: dict[str, Any] = {
        "alert": action_data,
        "current_occurrence": occurrence_data,
        "source_context": source_context,
        "report_context": report_context,
        "flow_context": flow_context,
        "check_context": check_context,
        "platform_observation": {
            "latest_scan": _safe_rows(
                [latest_scan],
                ("id", "started_at", "finished_at", "reports_scanned", "sources_found", "new_sources", "broken_refs", "status", "log"),
                limit=1,
            )[0] if latest_scan else None,
            "latest_probe_run": _safe_rows(
                [latest_probe_run],
                ("id", "started_at", "finished_at", "sources_probed", "fresh", "stale", "outdated", "unknown", "status", "log"),
                limit=1,
            )[0] if latest_probe_run else None,
            "latest_pbi_sync": _safe_rows(
                [latest_pbi_sync], ("id", "sync_type", "status", "started_at", "finished_at", "message"), limit=1
            )[0] if latest_pbi_sync else None,
        },
        "scope_note": (
            "Read-only, bounded operational metadata. Credentials, connection strings, "
            "raw queries/plans, file contents, local paths, and email recipients are excluded."
        ),
    }
    evidence: list[Evidence] = [Evidence(
        reference=f"alert:{args.action_id}",
        entity_type="alert",
        entity_id=str(args.action_id),
        label=f"Alert #{args.action_id}: {action['type']}",
        deep_link="/#alerts",
        observed_at=observed,
    )]
    if action["source_id"] is not None:
        source_id = str(action["source_id"])
        evidence.extend((
            Evidence(
                reference=f"source:{source_id}",
                entity_type="source",
                entity_id=source_id,
                label=f"Source configuration: {action['source_name']}",
                deep_link=f"/#sources/{source_id}",
                observed_at=observed,
            ),
            Evidence(
                reference=f"source_cadence:{source_id}",
                entity_type="source_cadence",
                entity_id=source_id,
                label=f"Measured source cadence: {action['source_name']}",
                deep_link=f"/#sources/{source_id}",
                observed_at=observed,
            ),
        ))

    # Exact run occurrences get the richer existing read projection as part of
    # the automatic Alert review, without widening the model's tool authority.
    if occurrence:
        try:
            linked_id = int(occurrence["focus_id"])
        except (TypeError, ValueError):
            linked_id = 0
        child: ToolEnvelope | None = None
        if occurrence["focus_type"] == "flow_run" and linked_id > 0:
            child = _get_flow_run(FlowRunArgs(run_id=linked_id))
            events = _get_flow_run_events(FlowRunEventsArgs(run_id=linked_id, limit=8))
            artifacts = _get_flow_run_artifacts(FlowRunArtifactsArgs(run_id=linked_id, limit=12))
            data["focused_run"] = child.data
            data["focused_run_events"] = events.data
            data["focused_run_artifacts"] = artifacts.data
            evidence.extend([*child.evidence, *events.evidence, *artifacts.evidence])
        elif occurrence["focus_type"] == "pipeline_run" and linked_id > 0:
            child = _get_pipeline_run(PipelineRunArgs(run_id=linked_id))
            data["focused_run"] = child.data
            evidence.extend(child.evidence)
        elif occurrence["focus_type"] == "pbi_sync" and linked_id > 0:
            with get_db() as db:
                sync = db.execute(
                    """SELECT id, sync_type, status, started_at, finished_at, message
                         FROM pbi_sync_runs WHERE id=?""",
                    (linked_id,),
                ).fetchone()
            if sync:
                data["focused_pbi_sync"] = _safe_rows(
                    [sync], ("id", "sync_type", "status", "started_at", "finished_at", "message"), limit=1
                )[0]

    # The same entity may be returned by multiple bounded run projections.
    evidence = list({item.reference: item for item in evidence}.values())

    def payload_size() -> int:
        return len(json.dumps(
            {"data": data, "evidence": [asdict(item) for item in evidence]},
            ensure_ascii=False,
        ).encode("utf-8"))

    def referenced_keys(value: Any) -> set[str]:
        refs: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_ref" and isinstance(item, str):
                    refs.add(item)
                else:
                    refs.update(referenced_keys(item))
        elif isinstance(value, list):
            for item in value:
                refs.update(referenced_keys(item))
        return refs

    truncated = False
    if payload_size() > MAX_ALERT_CONTEXT_BYTES:
        truncated = True
        if source_context:
            source_context["linked_reports"] = source_context["linked_reports"][:10]
            source_context["upstream_sources"] = source_context["upstream_sources"][:10]
            source_context["downstream_sources"] = source_context["downstream_sources"][:10]
            source_context["latest_probes"] = source_context["latest_probes"][:3]
            source_context["activity_history"] = source_context["activity_history"][:18]
            source_context["linked_flows"] = source_context["linked_flows"][:5]
            source_context["prior_alerts"] = source_context["prior_alerts"][:5]
        if report_context:
            report_context["tables_and_sources"] = report_context["tables_and_sources"][:12]
        if flow_context:
            flow_context["latest_runs"] = flow_context["latest_runs"][:3]
        if isinstance(data.get("focused_run"), dict):
            focused = data["focused_run"]
            if isinstance(focused.get("steps"), list):
                focused["steps"] = focused["steps"][:15]
            if isinstance(focused.get("resource_locks"), list):
                focused["resource_locks"] = focused["resource_locks"][:15]
            if isinstance(focused.get("timings"), list):
                focused["timings"] = focused["timings"][:15]
        data["truncation"] = {
            "truncated": True,
            "reason": "The Alert neighbourhood exceeded the bounded model context; newest and most relevant records were retained.",
        }
        allowed = referenced_keys(data) | {f"alert:{args.action_id}"}
        if occurrence and occurrence["focus_type"] in {"flow_run", "pipeline_run"}:
            allowed.add(f"{occurrence['focus_type']}:{occurrence['focus_id']}")
        evidence = [item for item in evidence if item.reference in allowed]

    if payload_size() > MAX_ALERT_CONTEXT_BYTES:
        # Last-resort compact projection keeps the current facts and explicit
        # truncation signal instead of failing the entire Alert assessment.
        if isinstance(data.get("focused_run"), dict):
            focused = data["focused_run"]
            data["focused_run"] = {
                key: focused[key]
                for key in ("run", "last_event", "recovery_preflight", "plan_summary", "steps")
                if key in focused
            }
            if isinstance(data["focused_run"].get("steps"), list):
                data["focused_run"]["steps"] = data["focused_run"]["steps"][:5]
        data.pop("focused_run_events", None)
        data.pop("focused_run_artifacts", None)
        if source_context:
            source_context["linked_reports"] = source_context["linked_reports"][:5]
            source_context["upstream_sources"] = source_context["upstream_sources"][:5]
            source_context["downstream_sources"] = source_context["downstream_sources"][:5]
            source_context["latest_probes"] = source_context["latest_probes"][:2]
            source_context["activity_history"] = source_context["activity_history"][:8]
            source_context["linked_flows"] = source_context["linked_flows"][:3]
            source_context["prior_alerts"] = source_context["prior_alerts"][:3]
        if report_context:
            report_context["tables_and_sources"] = report_context["tables_and_sources"][:5]
        if flow_context:
            flow_context["latest_runs"] = flow_context["latest_runs"][:2]
        for key in ("latest_scan", "latest_probe_run"):
            item = data["platform_observation"].get(key)
            if isinstance(item, dict):
                item.pop("log", None)
        allowed = referenced_keys(data) | {f"alert:{args.action_id}"}
        if occurrence and occurrence["focus_type"] in {"flow_run", "pipeline_run"}:
            allowed.add(f"{occurrence['focus_type']}:{occurrence['focus_id']}")
        evidence = [item for item in evidence if item.reference in allowed]

    return ToolEnvelope(
        data=data,
        evidence=tuple(evidence),
        observed_at=observed,
        truncated=truncated,
    )


TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "get_flow_run",
            "Read the exact focused Flow run summary, frozen safe configuration projection, worker state, timings, and server-computed recovery preflight.",
            FlowRunArgs, _get_flow_run, "Reading Flow run", "flow_run",
        ),
        ToolSpec(
            "get_flow_run_events",
            "Read the focused Flow run's bounded event timeline and diagnostic traceback tails. Local paths and secrets are redacted.",
            FlowRunEventsArgs, _get_flow_run_events, "Reading run events", "flow_run",
        ),
        ToolSpec(
            "get_flow_run_artifacts",
            "Read bounded metadata for the focused Flow run's files without returning local file paths.",
            FlowRunArtifactsArgs, _get_flow_run_artifacts, "Checking run artifacts", "flow_run",
        ),
        ToolSpec(
            "compare_flow_runs",
            "Compare the focused Flow run with an earlier successful run from the same Flow using server-defined projections.",
            CompareFlowRunsArgs, _compare_flow_runs, "Comparing prior run", "flow_run",
        ),
        ToolSpec(
            "get_pipeline_run",
            "Read the exact focused Pipeline run, bounded steps, locks, inspection flag, and precise Outlook submission status.",
            PipelineRunArgs, _get_pipeline_run, "Reading Pipeline run", "pipeline_run",
        ),
        ToolSpec(
            "get_pipeline_flow_run",
            "Read one Flow run only when a recorded step explicitly links it to the focused Pipeline run.",
            PipelineFlowRunArgs, _get_pipeline_flow_run, "Reading linked Flow run", "pipeline_run",
        ),
        ToolSpec(
            "get_pipeline_flow_run_events",
            "Read the bounded event timeline for a Flow run explicitly linked to the focused Pipeline run.",
            PipelineFlowRunEventsArgs, _get_pipeline_flow_run_events,
            "Reading linked Flow events", "pipeline_run",
        ),
        ToolSpec(
            "get_pipeline_flow_run_artifacts",
            "Read bounded artifact metadata without paths for a Flow run explicitly linked to the focused Pipeline run.",
            PipelineFlowRunArtifactsArgs, _get_pipeline_flow_run_artifacts,
            "Checking linked Flow artifacts", "pipeline_run",
        ),
        ToolSpec(
            "get_alert_context",
            "Read the focused canonical Alert, its current evidence revision, linked asset health, exact run occurrence when present, and latest scanner/probe/Power BI observations through a bounded redacted projection.",
            AlertContextArgs, _get_alert_context, "Reviewing Alert evidence", "alert",
        ),
    )
}


def specs_for_focus(focus_type: str) -> list[ToolSpec]:
    return [spec for spec in TOOL_SPECS.values() if spec.focus_type == focus_type]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    focus_type: str,
    focus_id: int,
) -> ToolEnvelope:
    spec = TOOL_SPECS.get(name)
    if spec is None or spec.focus_type != focus_type:
        raise ValueError("That tool is not available for this investigation.")
    parsed = spec.args_model.model_validate(arguments)
    scoped_id = (
        parsed.pipeline_run_id
        if isinstance(parsed, PipelineFlowRunArgs)
        else parsed.action_id
        if isinstance(parsed, AlertContextArgs)
        else getattr(parsed, "run_id", None)
    )
    if scoped_id != focus_id:
        raise ValueError("Tool calls are locked to the exact run or Alert selected by the server.")
    envelope = spec.handler(parsed)
    size = len(json.dumps(envelope.to_dict(), ensure_ascii=False).encode("utf-8"))
    if size > MAX_TOOL_RESULT_BYTES:
        raise ValueError("The read tool result exceeded its safe size limit.")
    return envelope
