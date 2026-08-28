"""Durable, report-scoped full-pipeline refresh orchestration."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import PBI_WORKSPACE, UPLOAD_PGHOST
from app.database import get_db
from app.flow_diagnostics import (
    build_flow_diagnostics,
    diagnostic_blocker_messages,
    included_flow_ids,
    legacy_flow_suggestions,
)
from app.flow_local_runner import HEADED_WORKER_ID, WORKER_ID, launch_local_worker
from app.flow_sql import _engine, _quote_identifier, configuration_status
from app.routers.eventlog import get_actor, log_event
from app.scanner.findings import sync_managed_actions
from app.scanner.pbi_fetch import (
    PbiFetchError,
    fetch_dataset_refresh_by_request_id,
    fetch_refresh_execution_details,
    resolve_report_dataset,
    trigger_dataset_refresh,
)
from app.settings import get_setting, set_setting
from app.source_identity import inspect_flow_target, normalize_server, reconcile_flow_target

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

TERMINAL_RUN_STATES = {"succeeded", "failed"}
FLOW_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
PBI_TERMINAL_STATES = {"completed", "failed", "disabled"}
IDENTITY_MAX_AGE = timedelta(hours=48)
PLAN_TTL = timedelta(minutes=5)
WORKER_READY_AGE = timedelta(seconds=90)
WORKER_START_DEADLINE = timedelta(seconds=60)
FLOW_CLAIM_DEADLINE = timedelta(minutes=10)
FLOW_WATCHDOG = timedelta(hours=12)
PBI_WATCHDOG = timedelta(hours=6)
PBI_POLL_SECONDS = 15

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pipeline-operation")
_futures: dict[str, Future] = {}
_future_lock = threading.Lock()
_shutdown_event = threading.Event()


class RunCreate(BaseModel):
    plan_token: str = Field(min_length=10, max_length=200)


class PipelineSettingsWrite(BaseModel):
    enabled: bool
    report_allowlist: list[int | str] = Field(default_factory=list, max_length=500)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _safe_error(exc: Exception | str) -> str:
    value = str(exc)
    value = re.sub(r"(?i)(password|token|secret)\s*[=:]\s*[^\s;]+", r"\1=[redacted]", value)
    value = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "postgresql://[redacted]", value)
    return value.strip()[:4000] or "Unknown error"


def _valid_email(value: str | None) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", (value or "").strip()))


def _setting_bool(key: str, default: bool = False) -> bool:
    return (get_setting(key, "1" if default else "0") or "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _allowlist() -> list[int | str]:
    raw = _loads(get_setting("pipeline_full_refresh_report_allowlist", "[]"), [])
    return raw if isinstance(raw, list) else []


def _feature_enabled_for(report) -> bool:
    if _setting_bool("pipeline_full_refresh_enabled"):
        return True
    allowed = _allowlist()
    return int(report["id"]) in {item for item in allowed if isinstance(item, int)} or (
        (report["name"] or "").strip().casefold()
        in {str(item).strip().casefold() for item in allowed if isinstance(item, str)}
    )


def _resolve_person(db, name: str | None) -> tuple[dict | None, str | None]:
    wanted = (name or "").strip()
    if not wanted:
        return None, "name is blank"
    rows = db.execute(
        "SELECT id, name, email FROM people WHERE lower(trim(name))=lower(trim(?)) ORDER BY id",
        (wanted,),
    ).fetchall()
    if len(rows) != 1:
        return None, f"matched {len(rows)} People records"
    if not _valid_email(rows[0]["email"]):
        return None, "the matching People record has no valid email"
    return dict(rows[0]), None


def _resolve_recipient(db, report, requester: str | None) -> tuple[dict | None, str]:
    owner, owner_error = _resolve_person(db, report["owner"])
    if owner:
        return {
            "name": owner["name"], "email": owner["email"], "source": "report_owner",
            "reason": "reports.owner uniquely matched People",
        }, ""
    fallback, requester_error = _resolve_person(db, requester)
    if fallback:
        return {
            "name": fallback["name"], "email": fallback["email"], "source": "requester",
            "reason": f"Report owner could not be used ({owner_error}); requester fallback selected.",
        }, ""
    return None, (
        f"No notification recipient: report owner {owner_error}; "
        f"requester {requester_error}."
    )


def _source_closure(db, report_id: int) -> tuple[list[int], list[tuple[int, int]]]:
    direct = [
        int(row[0]) for row in db.execute(
            "SELECT DISTINCT source_id FROM report_tables WHERE report_id=? AND source_id IS NOT NULL",
            (report_id,),
        ).fetchall()
    ]
    seen = set(direct)
    edges: list[tuple[int, int]] = []
    pending = list(direct)
    while pending:
        source_id = pending.pop()
        for row in db.execute(
            "SELECT depends_on_id FROM source_dependencies WHERE source_id=? ORDER BY depends_on_id",
            (source_id,),
        ).fetchall():
            depends_on = int(row[0])
            edges.append((source_id, depends_on))
            if depends_on not in seen:
                seen.add(depends_on)
                pending.append(depends_on)
    return sorted(seen), sorted(set(edges))


def _topological_mvs(mv_ids: set[int], edges: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    upstream = {source_id: [] for source_id in mv_ids}
    for source_id, depends_on in edges:
        if source_id in mv_ids and depends_on in mv_ids:
            upstream[source_id].append(depends_on)
    ordered: list[int] = []
    visiting: set[int] = set()
    visited: set[int] = set()
    cycle: list[int] = []

    def visit(node: int, trail: list[int]):
        nonlocal cycle
        if node in visiting:
            index = trail.index(node) if node in trail else 0
            cycle = trail[index:] + [node]
            return
        if node in visited or cycle:
            return
        visiting.add(node)
        for dependency in sorted(upstream[node]):
            visit(dependency, trail + [node])
        visiting.remove(node)
        visited.add(node)
        ordered.append(node)

    for mv_id in sorted(mv_ids):
        visit(mv_id, [])
    return ordered, cycle


def _worker_readiness(db, modes: set[str]) -> list[dict]:
    cutoff = _iso(_now() - WORKER_READY_AGE)
    result = []
    for mode in sorted(modes):
        worker_id = HEADED_WORKER_ID if mode == "headed" else WORKER_ID
        row = db.execute(
            "SELECT worker_id, display_name, status, last_seen_at FROM flow_workers WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        ready = bool(row and row["last_seen_at"] and row["last_seen_at"] >= cutoff)
        result.append({
            "mode": mode, "worker_id": worker_id, "ready": ready,
            "status": row["status"] if row else "not_registered",
            "last_seen_at": row["last_seen_at"] if row else None,
            "action": None if ready else "will_start_before_queueing",
        })
    return result


def _probe_materialized_views(mvs: list[dict]) -> tuple[list[str], list[str]]:
    """Validate existence/role permission and identify active refresh activity."""
    if not mvs:
        return [], []
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        from sqlalchemy import text
    except ImportError:
        return ["SQLAlchemy/psycopg2 are unavailable for MV preflight."], []
    by_database: dict[str, list[dict]] = {}
    for mv in mvs:
        by_database.setdefault(mv["database"], []).append(mv)
    for database, items in by_database.items():
        engine = None
        try:
            engine = _engine(database)
            with engine.connect() as connection:
                version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
                active_queries = [
                    str(row[0]) for row in connection.execute(text(
                        "SELECT query FROM pg_stat_activity WHERE pid<>pg_backend_pid() "
                        "AND state<>'idle' AND query ~* 'refresh[[:space:]]+materialized[[:space:]]+view'"
                    )).fetchall()
                ]
                for item in items:
                    permission_sql = (
                        "SELECT c.relkind, pg_has_role(current_user,c.relowner,'MEMBER') AS owner_member"
                        + (", has_table_privilege(c.oid,'MAINTAIN') AS can_maintain" if version >= 170000 else "")
                        + " FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                          "WHERE n.nspname=:schema AND c.relname=:relation"
                    )
                    row = connection.execute(
                        text(permission_sql),
                        {"schema": item["schema"], "relation": item["relation"]},
                    ).mappings().first()
                    label = f"{database}.{item['schema']}.{item['relation']}"
                    if not row or row["relkind"] != "m":
                        blockers.append(f"Materialized view does not exist: {label}.")
                        continue
                    allowed = bool(row["owner_member"] or row.get("can_maintain", False))
                    if not allowed:
                        blockers.append(f"DG_UPLOAD_* role cannot refresh {label}.")
                    needle = item["relation"].casefold()
                    if any(needle in query.casefold() for query in active_queries):
                        blockers.append(f"Another PostgreSQL session is already refreshing {label}.")
                        warnings.append(f"Possible pg_cron/manual refresh collision on {label}.")
        except Exception as exc:
            blockers.append(f"MV preflight failed for database {database}: {_safe_error(exc)}")
        finally:
            if engine is not None:
                engine.dispose()
    return blockers, warnings


def _flow_target_resource_key(
    *, server: str | None = None, database: str | None, schema: str | None, relation: str | None
) -> str | None:
    """Return the canonical lock key for one physical Flow SQL target."""
    coordinates = (
        normalize_server(server if server is not None else UPLOAD_PGHOST),
        (database or "").strip(),
        (schema or "").strip(),
        (relation or "").strip(),
    )
    if not all(coordinates[1:]):
        return None
    return "|".join(coordinates)


def _flow_target_key_for_id(db, flow_id: int | str) -> str | None:
    row = db.execute(
        """SELECT sql_handoff_enabled, sql_database, sql_schema, sql_table
           FROM flows WHERE id=?""",
        (flow_id,),
    ).fetchone()
    if not row or not row["sql_handoff_enabled"]:
        return None
    return _flow_target_resource_key(
        database=row["sql_database"], schema=row["sql_schema"], relation=row["sql_table"]
    )


def flow_target_resource_key_from_job(job_or_json) -> str | None:
    """Return the physical target frozen into a durable Flow job snapshot."""
    job = _loads(job_or_json, {}) if isinstance(job_or_json, str) else (job_or_json or {})
    target = job.get("sql_handoff") or {}
    if not target.get("enabled"):
        return None
    return _flow_target_resource_key(
        server=target.get("server") if "server" in target else UPLOAD_PGHOST,
        database=target.get("database"),
        schema=target.get("schema"),
        relation=target.get("table"),
    )


def active_flow_target_run(
    db, target_resource_key: str | None, *, exclude_run_id: int | None = None
):
    """Find an active run whose immutable job writes the physical target."""
    if not target_resource_key:
        return None
    rows = db.execute(
        """SELECT fr.id, fr.flow_id, fr.job_json, f.name AS flow_name
           FROM flow_runs fr
           JOIN flows f ON f.id=fr.flow_id
           WHERE fr.status IN ('queued','claimed','running')
           ORDER BY fr.id"""
    ).fetchall()
    for row in rows:
        if exclude_run_id is not None and int(row["id"]) == int(exclude_run_id):
            continue
        if flow_target_resource_key_from_job(row["job_json"]) == target_resource_key:
            return row
    return None


def assert_flow_target_available(
    db, target_resource_key: str | None, *, exclude_run_id: int | None = None
) -> None:
    """Block pipeline locks and active Flow jobs for one physical SQL target."""
    if not target_resource_key:
        return
    owner = resource_lock_owner(db, "flow_target", target_resource_key)
    if owner is not None:
        raise HTTPException(409, f"Flow SQL target is reserved by full-pipeline run #{owner}.")
    assert_no_active_flow_target_run(
        db, target_resource_key, exclude_run_id=exclude_run_id
    )


def assert_no_active_flow_target_run(
    db, target_resource_key: str | None, *, exclude_run_id: int | None = None
) -> None:
    """Block an existing direct/pipeline Flow job without inspecting pipeline locks."""
    active = active_flow_target_run(
        db, target_resource_key, exclude_run_id=exclude_run_id
    )
    if active is not None:
        raise HTTPException(
            409,
            f"Flow SQL target is already being written by Flow "
            f"'{active['flow_name']}' run #{active['id']}.",
        )


def _resource_specs(plan: dict) -> list[tuple[str, str]]:
    resources = [("report", str(plan["report"]["id"])), ("dataset", plan["powerbi"]["dataset_id"])]
    resources.extend(("flow", str(flow["id"])) for flow in plan["flows"])
    resources.extend(
        ("flow_target", flow["target_resource_key"])
        for flow in plan["flows"]
        if flow.get("target_resource_key")
    )
    resources.extend(("mv", mv["resource_key"]) for mv in plan["materialized_views"])
    return resources


def resource_lock_owner(db, resource_type: str, resource_key: str) -> int | None:
    row = db.execute(
        "SELECT run_id FROM pipeline_resource_locks WHERE resource_type=? AND resource_key=?",
        (resource_type, str(resource_key)),
    ).fetchone()
    return int(row["run_id"]) if row else None


def assert_resource_unlocked(db, resource_type: str, resource_key: str) -> None:
    owner = resource_lock_owner(db, resource_type, resource_key)
    if owner is not None:
        raise HTTPException(409, f"Resource is reserved by full-pipeline run #{owner}.")
    # Every existing manual Flow mutation/start path calls this helper with the
    # Flow ID. Also honor a full-pipeline lock held by another Flow that writes
    # the same physical PostgreSQL relation.
    if resource_type == "flow":
        target_key = _flow_target_key_for_id(db, resource_key)
        if target_key:
            target_owner = resource_lock_owner(db, "flow_target", target_key)
            if target_owner is not None:
                raise HTTPException(
                    409,
                    f"Flow SQL target is reserved by full-pipeline run #{target_owner}.",
                )


def _plan_snapshot(plan: dict) -> dict:
    return {
        "report": plan["report"],
        "recipient": plan["recipient"],
        "source_ids": plan["source_ids"],
        "source_edges": plan["source_edges"],
        "flows": plan["flows"],
        "flow_diagnostics": plan["flow_diagnostics"],
        "legacy_flow_suggestions": plan["legacy_flow_suggestions"],
        "materialized_views": plan["materialized_views"],
        "powerbi": plan["powerbi"],
        "configuration_versions": plan["configuration_versions"],
    }


def build_refresh_plan(report_id: int, requester: str | None, *, probe_mvs: bool = True) -> dict:
    blockers: list[str] = []
    warnings: list[str] = []
    with get_db() as db:
        report = db.execute(
            "SELECT id, name, owner, powerbi_url, pbi_dataset_id, updated_at FROM reports WHERE id=?",
            (report_id,),
        ).fetchone()
        if not report:
            raise HTTPException(404, "Report not found.")
        if not _feature_enabled_for(report):
            blockers.append("Full-pipeline refresh is disabled for this report.")

        recipient, recipient_error = _resolve_recipient(db, report, requester)
        if recipient_error:
            blockers.append(recipient_error)

        source_ids, source_edges = _source_closure(db, report_id)
        identities: dict[int, dict] = {}
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            rows = db.execute(
                f"""SELECT s.id, s.name, s.type, s.updated_at, spi.server_name,
                           spi.database_name, spi.schema_name, spi.relation_name,
                           spi.relation_kind, spi.verified_at
                    FROM sources s
                    LEFT JOIN source_postgres_identities spi ON spi.source_id=s.id
                    WHERE s.id IN ({placeholders}) ORDER BY s.id""",
                source_ids,
            ).fetchall()
            for row in rows:
                if (row["type"] or "").casefold() == "postgresql" and not row["server_name"]:
                    blockers.append(f"PostgreSQL source identity is missing for {row['name']}.")
                    continue
                if row["server_name"]:
                    verified = _parse_time(row["verified_at"])
                    if not verified or _now() - verified > IDENTITY_MAX_AGE:
                        blockers.append(f"PostgreSQL source identity is stale for {row['name']}.")
                    identities[int(row["id"])] = {
                        "source_id": int(row["id"]), "source_name": row["name"],
                        "server": row["server_name"], "database": row["database_name"],
                        "schema": row["schema_name"], "relation": row["relation_name"],
                        "kind": row["relation_kind"], "verified_at": row["verified_at"],
                    }

        mv_ids = {
            source_id for source_id, identity in identities.items()
            if identity["kind"] == "materialized_view"
        } | {source_id for source_id, _ in source_edges if source_id in identities}
        mv_order, cycle = _topological_mvs(mv_ids, source_edges)
        if cycle:
            blockers.append("Materialized-view dependency cycle: " + " -> ".join(map(str, cycle)) + ".")
        materialized_views = []
        for source_id in mv_order:
            identity = identities.get(source_id)
            if not identity:
                blockers.append(f"Materialized-view identity is missing for source #{source_id}.")
                continue
            item = dict(identity)
            item["resource_key"] = "|".join(
                [identity["server"], identity["database"], identity["schema"], identity["relation"]]
            )
            materialized_views.append(item)

        flow_diagnostics = build_flow_diagnostics(
            db,
            source_ids,
            server=UPLOAD_PGHOST,
        )
        blockers.extend(diagnostic_blocker_messages(flow_diagnostics))
        executable_flow_ids = included_flow_ids(flow_diagnostics)
        flow_rows = db.execute(
            """SELECT id, name, browser_mode, sql_handoff_enabled, sql_database,
                      sql_schema, sql_table, sql_target_source_id, updated_at
               FROM flows WHERE sql_handoff_enabled=1 ORDER BY id"""
        ).fetchall()
        flows = []
        for row in flow_rows:
            if int(row["id"]) not in executable_flow_ids:
                continue
            inspection = inspect_flow_target(db, row, server=UPLOAD_PGHOST)
            effective = inspection.get("effective_source_id")
            effective_id = int(effective) if effective is not None else None
            if effective_id is not None:
                target = {
                    "server": normalize_server(UPLOAD_PGHOST),
                    "database": (row["sql_database"] or "").strip(),
                    "schema": (row["sql_schema"] or "").strip(),
                    "table": (row["sql_table"] or "").strip(),
                }
                target_resource_key = _flow_target_resource_key(
                    server=target["server"], database=target["database"],
                    schema=target["schema"], relation=target["table"],
                )
                active = active_flow_target_run(db, target_resource_key)
                if active:
                    if int(active["flow_id"]) == int(row["id"]):
                        blockers.append(
                            f"Flow '{row['name']}' already has active run #{active['id']}."
                        )
                    else:
                        blockers.append(
                            f"Flow '{row['name']}' SQL target is already being written by "
                            f"Flow '{active['flow_name']}' run #{active['id']}."
                        )
                flows.append({
                    "id": int(row["id"]), "name": row["name"],
                    "browser_mode": row["browser_mode"],
                    "target_source_id": effective_id,
                    "persisted_target_source_id": inspection.get("persisted_source_id"),
                    "target": target,
                    "target_resource_key": target_resource_key,
                    "updated_at": row["updated_at"],
                })
        by_target: dict[str, list[str]] = {}
        for flow in flows:
            by_target.setdefault(flow["target_resource_key"], []).append(flow["name"])
        for names in by_target.values():
            if len(names) > 1:
                blockers.append("Multiple selected Flows write one target: " + ", ".join(names) + ".")

        workers = _worker_readiness(db, {flow["browser_mode"] for flow in flows})
        for worker in workers:
            if not worker["ready"]:
                warnings.append(
                    f"{worker['mode'].title()} Flow worker will be started and must register within 60 seconds."
                )

        settings_rows = db.execute(
            """SELECT key, value, updated_at FROM app_settings
               WHERE key IN ('pipeline_full_refresh_enabled','pipeline_full_refresh_report_allowlist')
               ORDER BY key"""
        ).fetchall()
        config_versions = {
            "report_updated_at": report["updated_at"],
            "settings": [dict(row) for row in settings_rows],
            "identity_verified_at": sorted(
                (source_id, item["verified_at"]) for source_id, item in identities.items()
            ),
        }

        credential_status = configuration_status()
        if not credential_status["configured"]:
            blockers.append("DG_UPLOAD_* credentials are unavailable: " + ", ".join(credential_status["missing"]) + ".")

        locked_resources = []
        # Power BI is resolved below; check Flow/MV/report locks now.
        provisional_resources = [("report", str(report_id))]
        provisional_resources += [("flow", str(flow["id"])) for flow in flows]
        provisional_resources += [
            ("flow_target", flow["target_resource_key"])
            for flow in flows if flow.get("target_resource_key")
        ]
        provisional_resources += [("mv", mv["resource_key"]) for mv in materialized_views]
        for resource_type, resource_key in provisional_resources:
            owner = resource_lock_owner(db, resource_type, resource_key)
            if owner is not None:
                locked_resources.append({"type": resource_type, "key": resource_key, "run_id": owner})
                blockers.append(f"{resource_type} resource is reserved by pipeline run #{owner}.")

    powerbi = {"workspace_id": None, "workspace_name": PBI_WORKSPACE, "dataset_id": None, "report_url": report["powerbi_url"]}
    try:
        resolved = resolve_report_dataset(PBI_WORKSPACE, report["name"])
        powerbi = {
            "workspace_id": resolved["workspace"]["id"],
            "workspace_name": resolved["workspace"]["name"],
            "dataset_id": resolved["dataset_id"],
            "report_id": resolved.get("report_id"),
            "report_url": resolved.get("web_url") or report["powerbi_url"],
        }
    except Exception as exc:
        blockers.append(f"Power BI target could not be resolved: {_safe_error(exc)}")

    if powerbi["dataset_id"]:
        with get_db() as db:
            owner = resource_lock_owner(db, "dataset", powerbi["dataset_id"])
            if owner is not None:
                locked_resources.append({"type": "dataset", "key": powerbi["dataset_id"], "run_id": owner})
                blockers.append(f"Power BI dataset is reserved by pipeline run #{owner}.")

    if probe_mvs and credential_status["configured"] and materialized_views:
        mv_blockers, mv_warnings = _probe_materialized_views(materialized_views)
        blockers.extend(mv_blockers)
        warnings.extend(mv_warnings)

    plan = {
        "report": {"id": int(report["id"]), "name": report["name"], "owner": report["owner"]},
        "requester": requester,
        "recipient": recipient,
        "source_ids": source_ids,
        "source_edges": source_edges,
        "flows": flows,
        "flow_diagnostics": flow_diagnostics,
        "legacy_flow_suggestions": legacy_flow_suggestions(flow_diagnostics),
        "materialized_views": materialized_views,
        "powerbi": powerbi,
        "worker_readiness": workers,
        "credential_status": credential_status,
        "locked_resources": locked_resources,
        "warnings": list(dict.fromkeys(warnings)),
        "blockers": list(dict.fromkeys(blockers)),
        "configuration_versions": config_versions,
        "generated_at": _iso(),
        "expires_at": _iso(_now() + PLAN_TTL),
        "estimated_duration_seconds": (
            len(flows) * 600 + len(materialized_views) * 300 + 600
        ),
    }
    snapshot = _plan_snapshot(plan)
    plan["plan_hash"] = hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()
    return plan


def _step_details(row) -> dict:
    item = dict(row)
    item["details"] = _loads(item.pop("details_json", None), {})
    return item


def get_pipeline_run(run_id: int) -> dict:
    with get_db() as db:
        run = db.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Pipeline run not found.")
        steps = db.execute(
            "SELECT * FROM pipeline_run_steps WHERE run_id=? ORDER BY sequence_no, id",
            (run_id,),
        ).fetchall()
        locks = db.execute(
            "SELECT resource_type, resource_key FROM pipeline_resource_locks WHERE run_id=? ORDER BY resource_type, resource_key",
            (run_id,),
        ).fetchall()
    result = dict(run)
    result["plan"] = _loads(result.pop("plan_json"), {})
    result["steps"] = [_step_details(row) for row in steps]
    result["resource_locks"] = [dict(row) for row in locks]
    return result


def _release_locks(db, run_id: int) -> None:
    db.execute("DELETE FROM pipeline_resource_locks WHERE run_id=?", (run_id,))


def _sync_pipeline_failure_actions(db, now: str) -> dict:
    """Keep one report-scoped alert with immutable exact-run occurrences.

    A newer in-progress run does not clear the previous failure. Only a later
    terminal success for the same report resolves its active failure alert.
    """
    failed = db.execute(
        """SELECT pr.id AS run_id, pr.report_id, pr.stage, pr.error,
                  pr.requires_inspection, pr.finished_at,
                  r.name AS report_name, r.owner
             FROM pipeline_runs pr
             JOIN reports r ON r.id=pr.report_id
            WHERE COALESCE(r.archived, 0)=0
              AND pr.status='failed'
              AND pr.id=(
                  SELECT pr2.id FROM pipeline_runs pr2
                   WHERE pr2.report_id=pr.report_id
                     AND pr2.status IN ('succeeded','failed')
                   ORDER BY pr2.id DESC LIMIT 1
              )"""
    ).fetchall()
    findings = [
        {
            "fingerprint": f"pipeline_failed:{row['report_id']}",
            "report_id": row["report_id"],
            "assigned_to": row["owner"],
            "notes": row["error"] or f"Pipeline for {row['report_name']} failed.",
            "occurrence": {
                "focus_type": "pipeline_run",
                "focus_id": row["run_id"],
                "observed_at": row["finished_at"] or now,
                "summary": (
                    f"Pipeline run #{row['run_id']} for {row['report_name']} failed."
                ),
                "evidence": {
                    "status": "failed",
                    "stage": row["stage"],
                    "requires_inspection": bool(row["requires_inspection"]),
                    "error": row["error"],
                },
            },
        }
        for row in failed
    ]
    return sync_managed_actions(db, "pipeline_failed", findings, now)


def _fail_pipeline(
    run_id: int,
    error: str,
    *,
    requires_inspection: bool = False,
    db=None,
) -> None:
    if db is None:
        with get_db() as connection:
            _fail_pipeline(
                run_id, error, requires_inspection=requires_inspection, db=connection
            )
        return
    run = db.execute("SELECT status FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["status"] in TERMINAL_RUN_STATES:
        return
    now = _iso()
    db.execute(
        """UPDATE pipeline_run_steps SET status='skipped', finished_at=?,
                  error=COALESCE(error, 'Skipped because an upstream stage failed.')
           WHERE run_id=? AND status='pending' AND step_type!='notification'""",
        (now, run_id),
    )
    db.execute(
        """UPDATE pipeline_runs SET status='failed', stage='failed', error=?,
                  requires_inspection=?, finished_at=?, updated_at=? WHERE id=?""",
        (_safe_error(error), int(requires_inspection), now, now, run_id),
    )
    _release_locks(db, run_id)
    _sync_pipeline_failure_actions(db, now)


def _succeed_pipeline(run_id: int) -> None:
    with get_db() as db:
        now = _iso()
        db.execute(
            """UPDATE pipeline_runs SET status='succeeded', stage='succeeded',
                      finished_at=?, updated_at=? WHERE id=?""",
            (now, now, run_id),
        )
        _release_locks(db, run_id)
        _sync_pipeline_failure_actions(db, now)


def _set_stage(run_id: int, stage: str, *, db=None) -> None:
    if db is None:
        with get_db() as connection:
            _set_stage(run_id, stage, db=connection)
        return
    db.execute(
        "UPDATE pipeline_runs SET status=?, stage=?, updated_at=? WHERE id=?",
        (stage, stage, _iso(), run_id),
    )


def _flow_metrics(db, flow_run_id: int) -> dict:
    run = db.execute("SELECT * FROM flow_runs WHERE id=?", (flow_run_id,)).fetchone()
    files = db.execute(
        "SELECT filename, file_size, row_count, status FROM flow_run_files WHERE run_id=? ORDER BY id",
        (flow_run_id,),
    ).fetchall()
    events = db.execute(
        "SELECT stage, details_json, error FROM flow_run_events WHERE run_id=? ORDER BY id",
        (flow_run_id,),
    ).fetchall()
    timings = db.execute(
        "SELECT phase, duration_ms, item_count, status FROM flow_operation_timings WHERE run_id=? ORDER BY id",
        (flow_run_id,),
    ).fetchall()
    sql_rows = None
    for event in events:
        details = _loads(event["details_json"], {})
        if "rows_written" in details:
            sql_rows = details.get("rows_written")
    return {
        "flow_status": run["status"] if run else "unknown",
        "files": [dict(row) for row in files],
        "artifact_rows": sum(int(row["row_count"] or 0) for row in files),
        "sql_rows_written": sql_rows,
        "timings": [dict(row) for row in timings],
        "progress": _loads(run["progress_json"], {}) if run else {},
        "error": run["error"] if run else "Flow run record is missing.",
    }


def _workers_ready_for_plan(db, plan: dict) -> bool:
    return all(item["ready"] for item in _worker_readiness(
        db, {flow["browser_mode"] for flow in plan["flows"]}
    ))


def _launch_required_workers(run_id: int, modes: list[str]) -> None:
    errors = []
    for mode in modes:
        result = launch_local_worker(mode)
        if result.get("status") == "error":
            errors.append(f"{mode}: {result.get('message')}")
    if errors:
        _fail_pipeline(run_id, "Could not start required Flow worker: " + "; ".join(errors))


def _prepare_or_monitor_flows(run_id: int, run, plan: dict) -> None:
    if run["stage"] == "queued":
        if not plan["flows"]:
            _set_stage(run_id, "refreshing_mvs" if plan["materialized_views"] else "refreshing_powerbi")
            return
        _set_stage(run_id, "preparing_workers")
        modes = sorted({flow["browser_mode"] for flow in plan["flows"]})
        _submit_future(f"workers:{run_id}", _launch_required_workers, run_id, modes)
        return

    if run["stage"] == "preparing_workers":
        with get_db() as db:
            if not _workers_ready_for_plan(db, plan):
                started = _parse_time(run["updated_at"] or run["started_at"] or run["created_at"]) or _now()
                if _now() - started >= WORKER_START_DEADLINE:
                    _fail_pipeline(run_id, "Required Flow worker did not register within 60 seconds.", db=db)
                return
            from app.routers.flows import queue_flow_run_service
            try:
                step = db.execute(
                    """SELECT id, entity_id FROM pipeline_run_steps
                       WHERE run_id=? AND step_type='flow' AND status='pending'
                       ORDER BY sequence_no LIMIT 1""",
                    (run_id,),
                ).fetchone()
                if step:
                    flow_run_id, _job = queue_flow_run_service(
                        db,
                        int(step["entity_id"]),
                        requested_by=run["requested_by"],
                        trigger_type="pipeline",
                    )
                    db.execute(
                        """UPDATE pipeline_run_steps SET status='running', flow_run_id=?,
                                  started_at=? WHERE id=?""",
                        (flow_run_id, _iso(), step["id"]),
                    )
            except Exception as exc:
                _fail_pipeline(run_id, f"Could not queue pipeline Flow runs: {_safe_error(exc)}", db=db)
                return
        _set_stage(run_id, "running_flows")
        return

    if run["stage"] != "running_flows":
        return
    with get_db() as db:
        steps = db.execute(
            "SELECT * FROM pipeline_run_steps WHERE run_id=? AND step_type='flow' ORDER BY sequence_no",
            (run_id,),
        ).fetchall()
        all_succeeded = True
        pending_steps = []
        active_present = False
        for step in steps:
            if step["status"] == "pending":
                pending_steps.append(step)
                all_succeeded = False
                continue
            flow_run = db.execute("SELECT * FROM flow_runs WHERE id=?", (step["flow_run_id"],)).fetchone()
            if not flow_run:
                db.execute(
                    "UPDATE pipeline_run_steps SET status='unknown', error=?, finished_at=? WHERE id=?",
                    ("Flow run disappeared.", _iso(), step["id"]),
                )
                _fail_pipeline(run_id, "A durable Flow run record disappeared.", requires_inspection=True, db=db)
                return
            created = _parse_time(flow_run["created_at"]) or _now()
            if flow_run["status"] == "queued" and _now() - created >= FLOW_CLAIM_DEADLINE:
                db.execute(
                    "UPDATE flow_runs SET status='cancelled', error=?, finished_at=? WHERE id=? AND status='queued'",
                    ("Pipeline claim deadline exceeded.", _iso(), flow_run["id"]),
                )
                db.execute(
                    "UPDATE pipeline_run_steps SET status='cancelled', error=?, finished_at=? WHERE id=?",
                    ("Flow was never claimed within 10 minutes.", _iso(), step["id"]),
                )
                _fail_pipeline(run_id, f"Flow '{step['entity_name']}' was never claimed.", db=db)
                return
            if flow_run["status"] not in FLOW_TERMINAL_STATES:
                all_succeeded = False
                active_present = True
                if _now() - created >= FLOW_WATCHDOG:
                    details = _flow_metrics(db, int(flow_run["id"]))
                    details["may_still_be_active"] = flow_run["status"] in {"claimed", "running"}
                    db.execute(
                        "UPDATE pipeline_run_steps SET status='failed', error=?, details_json=?, finished_at=? WHERE id=?",
                        ("Pipeline Flow watchdog exceeded; Flow was not forcibly stopped.", _json(details), _iso(), step["id"]),
                    )
                    _fail_pipeline(run_id, f"Flow '{step['entity_name']}' exceeded the pipeline watchdog and may still be active.", db=db)
                    return
                continue
            metrics = {
                **_loads(step["details_json"], {}),
                **_flow_metrics(db, int(flow_run["id"])),
            }
            status = "succeeded" if flow_run["status"] == "succeeded" else flow_run["status"]
            db.execute(
                """UPDATE pipeline_run_steps SET status=?, details_json=?, error=?, finished_at=?,
                          duration_ms=CAST((julianday(?) - julianday(started_at))*86400000 AS INTEGER)
                   WHERE id=? AND status='running'""",
                (status, _json(metrics), metrics.get("error"), _iso(), _iso(), step["id"]),
            )
            if status != "succeeded":
                _fail_pipeline(run_id, f"Flow '{step['entity_name']}' ended with {status}.", db=db)
                return
        if pending_steps and not active_present:
            from app.routers.flows import queue_flow_run_service
            next_step = pending_steps[0]
            try:
                flow_run_id, _job = queue_flow_run_service(
                    db,
                    int(next_step["entity_id"]),
                    requested_by=run["requested_by"],
                    trigger_type="pipeline",
                )
                db.execute(
                    """UPDATE pipeline_run_steps SET status='running', flow_run_id=?, started_at=?
                       WHERE id=?""",
                    (flow_run_id, _iso(), next_step["id"]),
                )
            except Exception as exc:
                _fail_pipeline(run_id, f"Could not queue the next Flow: {_safe_error(exc)}", db=db)
            return
        if all_succeeded:
            _set_stage(run_id, "refreshing_mvs" if plan["materialized_views"] else "refreshing_powerbi", db=db)


def _run_mv_stage(run_id: int) -> None:
    try:
        with get_db() as db:
            steps = db.execute(
                """SELECT * FROM pipeline_run_steps
                   WHERE run_id=? AND step_type='mv' AND status IN ('pending','running')
                   ORDER BY sequence_no""",
                (run_id,),
            ).fetchall()
        from sqlalchemy import text
        for step in steps:
            if _shutdown_event.is_set():
                return
            details = _loads(step["details_json"], {})
            database = details["database"]
            schema = details["schema"]
            relation = details["relation"]
            token = str(uuid.uuid4())
            with get_db() as db:
                db.execute(
                    """UPDATE pipeline_run_steps SET status='running', operation_token=?,
                              started_at=COALESCE(started_at, ?) WHERE id=? AND status='pending'""",
                    (token, _iso(), step["id"]),
                )
            engine = _engine(database)
            started = time.perf_counter()
            try:
                qualified = f"{_quote_identifier(schema)}.{_quote_identifier(relation)}"
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '600000ms'"))
                    connection.execute(text("SET LOCAL statement_timeout = '3600000ms'"))
                    connection.execute(text(f"REFRESH MATERIALIZED VIEW {qualified}"))
                refresh_ms = int((time.perf_counter() - started) * 1000)
                count = None
                count_status = "available"
                count_error = None
                try:
                    with engine.begin() as connection:
                        connection.execute(text("SET LOCAL statement_timeout = '300000ms'"))
                        count = int(connection.execute(text(f"SELECT COUNT(*) FROM {qualified}")).scalar_one())
                except Exception as exc:
                    count_status = "unavailable"
                    count_error = _safe_error(exc)
                details.update({
                    "committed": True, "refresh_duration_ms": refresh_ms,
                    "count_error": count_error,
                })
                with get_db() as db:
                    db.execute(
                        """UPDATE pipeline_run_steps SET status='succeeded', finished_at=?,
                                  duration_ms=?, row_count=?, row_count_status=?, details_json=?
                           WHERE id=?""",
                        (_iso(), refresh_ms, count, count_status, _json(details), step["id"]),
                    )
            except Exception as exc:
                error = _safe_error(exc)
                details.update({"committed": False, "diagnostic": "Lock timeout may indicate pg_cron or a manual refresh collision."})
                with get_db() as db:
                    db.execute(
                        "UPDATE pipeline_run_steps SET status='failed', error=?, details_json=?, finished_at=? WHERE id=?",
                        (error, _json(details), _iso(), step["id"]),
                    )
                _fail_pipeline(run_id, f"MV refresh failed for {database}.{schema}.{relation}: {error}")
                return
            finally:
                engine.dispose()
        _set_stage(run_id, "refreshing_powerbi")
    except Exception as exc:
        logger.exception("Pipeline MV stage failed")
        _fail_pipeline(run_id, f"MV stage failed: {_safe_error(exc)}")


def _run_powerbi_stage(run_id: int, *, resume: bool = False) -> None:
    try:
        with get_db() as db:
            run = db.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
            step = db.execute(
                "SELECT * FROM pipeline_run_steps WHERE run_id=? AND step_type='powerbi' LIMIT 1",
                (run_id,),
            ).fetchone()
        if not run or not step:
            return
        request_id = step["external_request_id"]
        if not request_id:
            if resume:
                with get_db() as db:
                    db.execute(
                        "UPDATE pipeline_run_steps SET status='unknown', error=?, finished_at=? WHERE id=?",
                        ("Restart occurred before the Power BI request ID was persisted.", _iso(), step["id"]),
                    )
                _fail_pipeline(run_id, "Power BI mutation outcome is unknown after restart.", requires_inspection=True)
                return
            run_plan = _loads(run["plan_json"], {})
            result = trigger_dataset_refresh(
                run_plan.get("powerbi", {}).get("workspace_name") or PBI_WORKSPACE,
                run["dataset_id"],
                notify_option="NoNotification",
            )
            request_id = result.get("request_id")
            if not request_id:
                with get_db() as db:
                    db.execute(
                        "UPDATE pipeline_run_steps SET status='unknown', error=?, details_json=?, finished_at=? WHERE id=?",
                        ("Power BI accepted the request without a request ID.", _json(result), _iso(), step["id"]),
                    )
                _fail_pipeline(run_id, "Power BI accepted a refresh but returned no request ID.", requires_inspection=True)
                return
            with get_db() as db:
                db.execute(
                    "UPDATE pipeline_run_steps SET external_request_id=?, details_json=? WHERE id=?",
                    (request_id, _json({"trigger": result}), step["id"]),
                )

        started = _parse_time(step["started_at"]) or _now()
        while _now() - started < PBI_WATCHDOG:
            if _shutdown_event.is_set():
                return
            history = fetch_dataset_refresh_by_request_id(
                run["workspace_id"], run["dataset_id"], request_id
            )
            if history:
                with get_db() as db:
                    db.execute(
                        "UPDATE pipeline_run_steps SET details_json=? WHERE id=?",
                        (_json({"history": history}), step["id"]),
                    )
                status = (history.get("status") or "").casefold()
                if status in PBI_TERMINAL_STATES:
                    details: dict[str, Any] = {"history": history}
                    execution = fetch_refresh_execution_details(
                        run["workspace_id"], run["dataset_id"], request_id
                    )
                    if execution:
                        details["execution_details"] = execution
                    duration_ms = int((_now() - started).total_seconds() * 1000)
                    if status == "completed":
                        with get_db() as db:
                            db.execute(
                                """UPDATE pipeline_run_steps SET status='succeeded', finished_at=?,
                                          duration_ms=?, details_json=? WHERE id=?""",
                                (_iso(), duration_ms, _json(details), step["id"]),
                            )
                        _succeed_pipeline(run_id)
                    else:
                        error = history.get("error") or f"Power BI refresh ended with {history.get('status')}."
                        with get_db() as db:
                            db.execute(
                                """UPDATE pipeline_run_steps SET status='failed', finished_at=?,
                                          duration_ms=?, details_json=?, error=? WHERE id=?""",
                                (_iso(), duration_ms, _json(details), error, step["id"]),
                            )
                        _fail_pipeline(run_id, error)
                    return
            if _shutdown_event.wait(PBI_POLL_SECONDS):
                return
        with get_db() as db:
            db.execute(
                "UPDATE pipeline_run_steps SET status='failed', error=?, finished_at=? WHERE id=?",
                ("Power BI did not reach a terminal state within six hours.", _iso(), step["id"]),
            )
        _fail_pipeline(run_id, "Power BI refresh watchdog expired.")
    except Exception as exc:
        logger.exception("Pipeline Power BI stage failed")
        _fail_pipeline(run_id, f"Power BI stage failed: {_safe_error(exc)}")


def _summary_html(run: dict) -> str:
    def esc(value):
        rendered = str(value if value is not None else "—")
        rendered = re.sub(r"(?i)(?:[a-z]:\\|\\\\)[^\s<]+", "[local path redacted]", rendered)
        return html.escape(rendered)

    def scalar_metrics(value, prefix="", output=None):
        output = output if output is not None else []
        if len(output) >= 30:
            return output
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                if key_text.casefold() in {"serviceexceptionjson", "messages", "refreshattempts"}:
                    continue
                scalar_metrics(item, f"{prefix}.{key_text}" if prefix else key_text, output)
        elif isinstance(value, list):
            for index, item in enumerate(value[:10]):
                scalar_metrics(item, f"{prefix}[{index}]", output)
        elif value is not None and any(
            marker in prefix.casefold()
            for marker in ("row", "duration", "object", "partition", "processed", "status", "time")
        ):
            output.append((prefix, value))
        return output

    rows = []
    for step in run["steps"]:
        detail = step.get("details") or {}
        metrics = []
        if step["step_type"] == "flow":
            metrics.append(f"browser: {esc(detail.get('browser_mode'))}")
            metrics.append(f"SQL rows: {esc(detail.get('sql_rows_written'))}")
            metrics.append(f"artifact rows: {esc(detail.get('artifact_rows'))}")
            file_items = [item for item in detail.get("files", []) if item.get("filename")]
            if file_items:
                metrics.append("files: " + esc(", ".join(
                    f"{item['filename']} ("
                    f"{str(item.get('row_count')) + ' rows' if item.get('row_count') is not None else 'rows unavailable'}, "
                    f"{str(item.get('file_size')) + ' bytes' if item.get('file_size') is not None else 'size unavailable'})"
                    for item in file_items
                )))
            phase_text = ", ".join(
                f"{item.get('phase')}: {item.get('duration_ms')} ms"
                for item in detail.get("timings", [])
            )
            if phase_text:
                metrics.append("timings: " + esc(phase_text))
        elif step["step_type"] == "mv":
            metrics.append(f"committed: {esc(detail.get('committed'))}")
            metrics.append(
                f"rows: {esc(step.get('row_count')) if step.get('row_count_status') == 'available' else 'unavailable'}"
            )
            if detail.get("count_error"):
                metrics.append("count error: " + esc(detail["count_error"]))
        elif step["step_type"] == "powerbi":
            history = detail.get("history") or {}
            metrics.append(f"request: {esc(step.get('external_request_id'))}")
            metrics.append(f"history: {esc(history.get('status'))}")
            attempts = history.get("attempts") or []
            metrics.append(f"attempts: {esc(len(attempts))}")
            if attempts:
                metrics.append("attempt details: " + esc(", ".join(
                    "/".join(str(attempt.get(key) or "—") for key in ("type", "startTime", "endTime", "status"))
                    for attempt in attempts
                )))
            returned = scalar_metrics(detail.get("execution_details") or {})
            if returned:
                metrics.append("execution metrics: " + esc(", ".join(f"{key}={value}" for key, value in returned)))
        rows.append(
            "<tr>"
            f"<td>{esc(step['sequence_no'] + 1)}</td><td>{esc(step['step_type'])}</td>"
            f"<td>{esc(step.get('entity_name'))}</td><td>{esc(step['status'])}</td>"
            f"<td>{esc(step.get('duration_ms'))} ms</td><td>{'<br>'.join(metrics)}</td>"
            f"<td>{esc(step.get('error'))}</td></tr>"
        )
    plan = run["plan"]
    recipient = plan.get("recipient") or {}
    started = _parse_time(run.get("started_at"))
    finished = _parse_time(run.get("finished_at"))
    runtime = int((finished - started).total_seconds()) if started and finished else None
    report_url = plan.get("powerbi", {}).get("report_url")
    link_html = f'<a href="{esc(report_url)}">Open Power BI report</a>' if report_url else "—"
    return (
        "<html><body><h2>Metronome full-pipeline refresh</h2>"
        f"<p><b>Report:</b> {esc(plan.get('report', {}).get('name'))}<br>"
        f"<b>Status:</b> {esc(run['status'])}<br><b>Requester:</b> {esc(run.get('requested_by'))}<br>"
        f"<b>Recipient:</b> {esc(recipient.get('name'))} ({esc(recipient.get('reason'))})<br>"
        f"<b>Started:</b> {esc(run.get('started_at'))}<br><b>Finished:</b> {esc(run.get('finished_at'))}<br>"
        f"<b>Total runtime:</b> {esc(runtime)} seconds<br><b>Link:</b> {link_html}<br>"
        f"<b>Error:</b> {esc(run.get('error'))}</p>"
        "<table border='1' cellpadding='5' cellspacing='0'><thead><tr>"
        "<th>#</th><th>Stage</th><th>Item</th><th>Status</th><th>Runtime</th><th>Metrics</th><th>Error</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></body></html>"
    )


def _submit_summary(run_id: int) -> None:
    try:
        run = get_pipeline_run(run_id)
        recipient = run["plan"].get("recipient") or {}
        message = {
            "to": recipient.get("email"),
            "subject": f"Metronome pipeline {run['status']}: {run['plan']['report']['name']}",
            "html_body": _summary_html(run),
        }
        from app.routers.email import launch_outlook_dispatch
        dispatch = launch_outlook_dispatch(
            [message], mode="send", pipeline_run_id=run_id, purpose="pipeline_summary"
        )
        with get_db() as db:
            db.execute(
                "UPDATE pipeline_runs SET notification_status='pending_receipt', notification_error=NULL, updated_at=? WHERE id=?",
                (_iso(), run_id),
            )
            db.execute(
                """UPDATE pipeline_run_steps SET status='running', started_at=COALESCE(started_at, ?),
                          details_json=? WHERE run_id=? AND step_type='notification'""",
                (_iso(), _json({"dispatch_id": dispatch["id"], "handoff": "pending_receipt"}), run_id),
            )
    except Exception as exc:
        with get_db() as db:
            db.execute(
                "UPDATE pipeline_runs SET notification_status='unknown', notification_error=?, updated_at=? WHERE id=?",
                (_safe_error(exc), _iso(), run_id),
            )
            db.execute(
                """UPDATE pipeline_run_steps SET status='unknown', error=?, finished_at=?
                   WHERE run_id=? AND step_type='notification'""",
                (_safe_error(exc), _iso(), run_id),
            )


def _submit_future(key: str, fn, *args, **kwargs) -> None:
    with _future_lock:
        current = _futures.get(key)
        if current and not current.done():
            return
        future = _executor.submit(fn, *args, **kwargs)
        _futures[key] = future


def shutdown_pipeline_executor() -> None:
    """Stop polling on app shutdown; durable state is resumed after restart."""
    _shutdown_event.set()
    _executor.shutdown(wait=False, cancel_futures=True)


def _reap_futures() -> None:
    with _future_lock:
        for key, future in list(_futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception:
                    logger.exception("Pipeline operation future failed: %s", key)
                _futures.pop(key, None)


def _claim_tick_lease(run_id: int) -> str | None:
    token = str(uuid.uuid4())
    now = _iso()
    expires = _iso(_now() + timedelta(seconds=20))
    with get_db() as db:
        cursor = db.execute(
            """UPDATE pipeline_runs SET lease_token=?, lease_expires_at=?
               WHERE id=? AND (lease_token IS NULL OR lease_expires_at IS NULL OR lease_expires_at < ?)""",
            (token, expires, run_id, now),
        )
    return token if cursor.rowcount else None


def _release_tick_lease(run_id: int, token: str) -> None:
    with get_db() as db:
        db.execute(
            """UPDATE pipeline_runs SET lease_token=NULL, lease_expires_at=NULL
               WHERE id=? AND lease_token=?""",
            (run_id, token),
        )


def pipeline_tick() -> dict:
    """Perform short transitions and delegate every blocking operation."""
    _reap_futures()
    def reconcile_outlook():
        from app.routers.email import reconcile_outlook_dispatches
        return reconcile_outlook_dispatches()

    _submit_future("outlook:reconcile", reconcile_outlook)
    with get_db() as db:
        runs = db.execute(
            "SELECT * FROM pipeline_runs WHERE status NOT IN ('succeeded','failed') ORDER BY id"
        ).fetchall()
        terminal_notifications = db.execute(
            """SELECT id FROM pipeline_runs WHERE status IN ('succeeded','failed')
               AND notification_status='pending' ORDER BY id LIMIT 10"""
        ).fetchall()
        receipts = db.execute(
            """SELECT pr.id, od.status, od.error FROM pipeline_runs pr
               JOIN outlook_dispatches od ON od.pipeline_run_id=pr.id
               WHERE pr.notification_status='pending_receipt'
                 AND od.id=(SELECT MAX(od2.id) FROM outlook_dispatches od2 WHERE od2.pipeline_run_id=pr.id)
               ORDER BY od.id DESC"""
        ).fetchall()
        # Also backfill/repair canonical alerts for terminal runs created by an
        # older Metronome build. In-progress retries deliberately leave the
        # prior failure active until a terminal success is recorded.
        _sync_pipeline_failure_actions(db, _iso())
        for receipt in receipts:
            if receipt["status"] == "submitted":
                db.execute(
                    "UPDATE pipeline_runs SET notification_status='submitted', updated_at=? WHERE id=?",
                    (_iso(), receipt["id"]),
                )
                db.execute(
                    """UPDATE pipeline_run_steps SET status='succeeded', finished_at=?,
                              duration_ms=CAST((julianday(?) - julianday(started_at))*86400000 AS INTEGER)
                       WHERE run_id=? AND step_type='notification'""",
                    (_iso(), _iso(), receipt["id"]),
                )
            elif receipt["status"] in {"failed", "unknown"}:
                db.execute(
                    "UPDATE pipeline_runs SET notification_status=?, notification_error=?, updated_at=? WHERE id=?",
                    (receipt["status"], receipt["error"], _iso(), receipt["id"]),
                )
                db.execute(
                    """UPDATE pipeline_run_steps SET status=?, error=?, finished_at=?
                       WHERE run_id=? AND step_type='notification'""",
                    (receipt["status"], receipt["error"], _iso(), receipt["id"]),
                )
    for receipt in terminal_notifications:
        _submit_future(f"email:{receipt['id']}", _submit_summary, int(receipt["id"]))

    for run in runs:
        run_id = int(run["id"])
        lease_token = _claim_tick_lease(run_id)
        if not lease_token:
            continue
        try:
            with get_db() as db:
                run = db.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] in TERMINAL_RUN_STATES:
                continue
            plan = _loads(run["plan_json"], {})
            if run["stage"] in {"queued", "preparing_workers", "running_flows"}:
                _prepare_or_monitor_flows(run_id, run, plan)
            elif run["stage"] == "refreshing_mvs":
                with get_db() as db:
                    mv_state = db.execute(
                        """SELECT
                               MAX(CASE WHEN status='running' THEN id END) AS running_id,
                               SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
                               SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count
                           FROM pipeline_run_steps WHERE run_id=? AND step_type='mv'""",
                        (run_id,),
                    ).fetchone()
                key = f"mv:{run_id}"
                with _future_lock:
                    live = key in _futures and not _futures[key].done()
                if mv_state["running_id"] and not live:
                    with get_db() as db:
                        db.execute(
                            "UPDATE pipeline_run_steps SET status='unknown', error=?, finished_at=? WHERE id=?",
                            ("Restart crossed an MV commit boundary.", _iso(), mv_state["running_id"]),
                        )
                    _fail_pipeline(run_id, "MV outcome is unknown after restart.", requires_inspection=True)
                elif mv_state["succeeded_count"] and mv_state["pending_count"] and not live:
                    _fail_pipeline(
                        run_id,
                        "Restart occurred between committed materialized views; remaining views were not replayed.",
                        requires_inspection=True,
                    )
                else:
                    _submit_future(key, _run_mv_stage, run_id)
            elif run["stage"] == "refreshing_powerbi":
                with get_db() as db:
                    step = db.execute(
                        "SELECT * FROM pipeline_run_steps WHERE run_id=? AND step_type='powerbi' LIMIT 1",
                        (run_id,),
                    ).fetchone()
                    if step and step["status"] == "pending":
                        db.execute(
                            "UPDATE pipeline_run_steps SET status='running', operation_token=?, started_at=? WHERE id=?",
                            (str(uuid.uuid4()), _iso(), step["id"]),
                        )
                if step:
                    key = f"pbi:{run_id}"
                    with _future_lock:
                        live = key in _futures and not _futures[key].done()
                    if not live:
                        _submit_future(
                            key,
                            _run_powerbi_stage,
                            run_id,
                            resume=bool(step["status"] == "running"),
                        )
        except Exception as exc:
            logger.exception("Pipeline tick failed for run %s", run_id)
            _fail_pipeline(run_id, f"Pipeline transition failed: {_safe_error(exc)}")
        finally:
            _release_tick_lease(run_id, lease_token)
    return {"active_runs": len(runs)}


@router.get("/reports/{report_id}/refresh-plan")
def refresh_plan(report_id: int, request: Request):
    requester = get_actor(request)
    plan = build_refresh_plan(report_id, requester)
    token = str(uuid.uuid4())
    with get_db() as db:
        db.execute("DELETE FROM pipeline_plan_previews WHERE expires_at < ?", (_iso(),))
        db.execute(
            """INSERT INTO pipeline_plan_previews
                   (token, report_id, requested_by, plan_hash, plan_json, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token, report_id, requester, plan["plan_hash"], _json(plan), plan["expires_at"]),
        )
    plan["plan_token"] = token
    return plan


def _confirm_flow_targets_for_run(db, plan: dict) -> None:
    """Revalidate and persist the previewed exact targets in the run transaction."""
    for expected in plan["flows"]:
        row = db.execute(
            """SELECT id, sql_handoff_enabled, sql_database, sql_schema, sql_table,
                      sql_target_source_id
               FROM flows WHERE id=?""",
            (expected["id"],),
        ).fetchone()
        if not row:
            raise HTTPException(409, "A selected Flow no longer exists. Preview the pipeline again.")

        target_key = _flow_target_resource_key(
            database=row["sql_database"], schema=row["sql_schema"], relation=row["sql_table"]
        ) if row["sql_handoff_enabled"] else None
        inspection = inspect_flow_target(db, row, server=UPLOAD_PGHOST)
        effective = inspection.get("effective_source_id")
        effective_id = int(effective) if effective is not None else None
        expected_id = int(expected["target_source_id"])
        if effective_id != expected_id or target_key != expected.get("target_resource_key"):
            raise HTTPException(
                409,
                f"Flow '{expected['name']}' SQL target changed. Preview the pipeline again.",
            )

        # The preview checked this too, but a direct Flow can be queued in the
        # gap before this BEGIN IMMEDIATE transaction acquires the write slot.
        # Recheck its frozen job target before creating governed locks.
        assert_flow_target_available(db, target_key)

        # This is the only point in the preview/confirmation path that mutates
        # a Flow link. The reconciler updates updated_at only when the FK changes.
        reconcile_flow_target(db, int(row["id"]), server=UPLOAD_PGHOST)
        confirmed = inspect_flow_target(db, int(row["id"]), server=UPLOAD_PGHOST)
        confirmed_effective = confirmed.get("effective_source_id")
        confirmed_persisted = confirmed.get("persisted_source_id")
        if (
            confirmed_effective is None
            or int(confirmed_effective) != expected_id
            or confirmed_persisted is None
            or int(confirmed_persisted) != expected_id
        ):
            raise HTTPException(
                409,
                f"Flow '{expected['name']}' SQL target could not be confirmed. "
                "Preview the pipeline again.",
            )


@router.post("/reports/{report_id}/runs", status_code=201)
def create_pipeline_run(report_id: int, body: RunCreate, request: Request):
    requester = get_actor(request)
    with get_db() as db:
        preview = db.execute(
            "SELECT * FROM pipeline_plan_previews WHERE token=? AND report_id=?",
            (body.plan_token, report_id),
        ).fetchone()
    if not preview or (_parse_time(preview["expires_at"]) or _now()) <= _now():
        raise HTTPException(409, "Refresh plan expired. Preview the pipeline again.")
    if (preview["requested_by"] or "") != (requester or ""):
        raise HTTPException(403, "Refresh plans can only be confirmed by their requester.")
    current = build_refresh_plan(report_id, requester)
    if current["plan_hash"] != preview["plan_hash"]:
        raise HTTPException(409, "Pipeline configuration changed. Preview the pipeline again.")
    if current["blockers"]:
        raise HTTPException(409, {"message": "Pipeline preflight is blocked.", "blockers": current["blockers"]})
    plan = current
    now = _iso()
    try:
        with get_db() as db:
            # Reserve the SQLite write slot before re-resolving targets so link
            # confirmation, run creation, steps, and locks are one atomic unit.
            db.execute("BEGIN IMMEDIATE")
            _confirm_flow_targets_for_run(db, plan)
            cursor = db.execute(
                """INSERT INTO pipeline_runs
                       (report_id, status, stage, requested_by, recipient_name, recipient_email,
                        recipient_source, workspace_id, dataset_id, plan_hash, plan_json,
                        started_at, created_at, updated_at)
                   VALUES (?, 'queued', 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report_id, requester, plan["recipient"]["name"], plan["recipient"]["email"],
                    plan["recipient"]["source"], plan["powerbi"]["workspace_id"],
                    plan["powerbi"]["dataset_id"], plan["plan_hash"], _json(plan), now, now, now,
                ),
            )
            run_id = int(cursor.lastrowid)
            for resource_type, resource_key in _resource_specs(plan):
                db.execute(
                    "INSERT INTO pipeline_resource_locks(resource_type, resource_key, run_id) VALUES (?, ?, ?)",
                    (resource_type, resource_key, run_id),
                )
            sequence = 0
            for flow in plan["flows"]:
                details = {
                    "browser_mode": flow["browser_mode"],
                    "target_source_id": flow["target_source_id"],
                    "target_resource_key": flow["target_resource_key"],
                    "target": flow["target"],
                }
                db.execute(
                    """INSERT INTO pipeline_run_steps
                           (run_id, step_type, sequence_no, entity_type, entity_id, entity_name, details_json)
                       VALUES (?, 'flow', ?, 'flow', ?, ?, ?)""",
                    (run_id, sequence, str(flow["id"]), flow["name"], _json(details)),
                )
                sequence += 1
            for mv in plan["materialized_views"]:
                db.execute(
                    """INSERT INTO pipeline_run_steps
                           (run_id, step_type, sequence_no, entity_type, entity_id, entity_name, details_json)
                       VALUES (?, 'mv', ?, 'source', ?, ?, ?)""",
                    (run_id, sequence, str(mv["source_id"]), mv["source_name"], _json(mv)),
                )
                sequence += 1
            db.execute(
                """INSERT INTO pipeline_run_steps
                       (run_id, step_type, sequence_no, entity_type, entity_id, entity_name, details_json)
                   VALUES (?, 'powerbi', ?, 'dataset', ?, ?, ?)""",
                (run_id, sequence, plan["powerbi"]["dataset_id"], plan["report"]["name"], _json(plan["powerbi"])),
            )
            sequence += 1
            db.execute(
                """INSERT INTO pipeline_run_steps
                       (run_id, step_type, sequence_no, entity_type, entity_id, entity_name, details_json)
                   VALUES (?, 'notification', ?, 'person', ?, ?, ?)""",
                (
                    run_id, sequence, plan["recipient"]["email"], plan["recipient"]["name"],
                    _json({"recipient_source": plan["recipient"]["source"], "reason": plan["recipient"]["reason"]}),
                ),
            )
            db.execute("DELETE FROM pipeline_plan_previews WHERE token=?", (body.plan_token,))
            log_event(db, "pipeline", run_id, plan["report"]["name"], "queued", actor=requester)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A selected resource was reserved by another pipeline run.") from exc
    return get_pipeline_run(run_id)


@router.get("/reports/{report_id}/runs/latest")
def latest_pipeline_run(report_id: int):
    with get_db() as db:
        rows = db.execute(
            """SELECT id, status, stage, requested_by, created_at, started_at, finished_at,
                      notification_status, error
               FROM pipeline_runs WHERE report_id=? ORDER BY id DESC LIMIT 10""",
            (report_id,),
        ).fetchall()
    if not rows:
        return None
    result = get_pipeline_run(int(rows[0]["id"]))
    result["recent_runs"] = [dict(row) for row in rows]
    return result


@router.get("/runs/{run_id}")
def pipeline_run(run_id: int):
    return get_pipeline_run(run_id)


@router.post("/runs/{run_id}/resend-summary")
def resend_summary(run_id: int, request: Request):
    with get_db() as db:
        run = db.execute("SELECT status FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Pipeline run not found.")
        if run["status"] not in TERMINAL_RUN_STATES:
            raise HTTPException(409, "Wait for the pipeline to finish before sending its summary.")
        db.execute(
            "UPDATE pipeline_runs SET notification_status='pending', notification_error=NULL, updated_at=? WHERE id=?",
            (_iso(), run_id),
        )
        db.execute(
            """UPDATE pipeline_run_steps SET status='pending', error=NULL, started_at=NULL,
                      finished_at=NULL, duration_ms=NULL
               WHERE run_id=? AND step_type='notification'""",
            (run_id,),
        )
        log_event(db, "pipeline", run_id, f"Pipeline #{run_id}", "summary_resend_requested", actor=get_actor(request))
    return {"run_id": run_id, "notification_status": "pending"}


@router.get("/settings")
def pipeline_settings():
    return {"enabled": _setting_bool("pipeline_full_refresh_enabled"), "report_allowlist": _allowlist()}


@router.put("/settings")
def update_pipeline_settings(body: PipelineSettingsWrite, request: Request):
    set_setting("pipeline_full_refresh_enabled", "1" if body.enabled else "0")
    set_setting("pipeline_full_refresh_report_allowlist", _json(body.report_allowlist))
    with get_db() as db:
        log_event(db, "system", None, "Full-pipeline refresh", "settings_updated", _json(body.model_dump()), get_actor(request))
    return pipeline_settings()
