"""Fixed, read-only tools for evidence-backed Flow and Pipeline investigations."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_db
from app.path_safety import is_remote_file_path

MAX_TOOL_RESULT_BYTES = 64 * 1024


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
    text = re.sub(
        r"(?i)(password|passwd|token|secret|authorization|api[_ -]?key)\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
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


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], ToolEnvelope]
    progress_label: str
    focus_type: Literal["flow_run", "pipeline_run"]

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
        "resource_locks": [dict(item) for item in locks[:50]],
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
        else getattr(parsed, "run_id", None)
    )
    if scoped_id != focus_id:
        raise ValueError("Tool calls are locked to the exact run selected by the user.")
    envelope = spec.handler(parsed)
    size = len(json.dumps(envelope.to_dict(), ensure_ascii=False).encode("utf-8"))
    if size > MAX_TOOL_RESULT_BYTES:
        raise ValueError("The read tool result exceeded its safe size limit.")
    return envelope
