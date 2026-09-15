"""Restricted reader process: fixed aggregate reads, no mutation routes.

The Windows installer runs this module under a separate local identity. Tool
restrictions bound Qwen's application access; OS/network isolation of the model
server remains a deployment property and is never claimed by this service.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from .policy import InspectionError, ReadRequest, digest, load_policy, load_reader_config
from .postgres import profile_sql
from .profiles import discover_columns, profile_csv

app = FastAPI(title="Metronome restricted data reader", docs_url=None,
              redoc_url=None, openapi_url=None)
_lock = threading.Lock()


def reader_config():
    value = load_reader_config()
    if value:
        return value
    return {"reader_token": os.environ.get("METRONOME_AUDIT_READER_TOKEN", ""),
            "category_key": os.environ.get("METRONOME_AUDIT_CATEGORY_KEY", ""),
            "reader_dsn": os.environ.get("METRONOME_AUDIT_READER_DSN", ""),
            "manifest_path": "", "policy_path": os.environ.get("METRONOME_AUDIT_READER_POLICY", "")}


def authorize(request):
    token = str(reader_config().get("reader_token") or "")
    try:
        loopback = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        loopback = False
    if (not loopback or request.headers.get("forwarded") or request.headers.get("x-forwarded-for")
            or len(token) < 32 or not hmac.compare_digest(
                request.headers.get("authorization", "").encode(), ("Bearer " + token).encode())):
        raise HTTPException(401, "reader_access_required")


def revision(policy):
    key = str(reader_config().get("category_key") or "")
    return digest([policy.revision, digest(key)])


@contextmanager
def manifest(policy):
    configured = str(reader_config().get("manifest_path") or "")
    if configured and Path(configured).resolve() != Path(policy.manifest_db).resolve():
        raise InspectionError("manifest_identity_mismatch")
    uri = Path(policy.manifest_db).absolute().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions = [row[0] for row in db.execute("SELECT version FROM manifest_format")] if "manifest_format" in tables else []
        if tables != {"manifest_format", "flow_runs"} or versions not in ([1], [2]):
            raise InspectionError("separate_sanitized_manifest_required")
        yield db
    finally:
        db.close()


def _run(db, dataset, run_id):
    row = db.execute("""SELECT id,job_json,artifact_json,finished_at,progress_json
        FROM flow_runs WHERE id=? AND flow_id=? AND status='succeeded'
        AND trigger_type NOT IN ('sql_retry','view_retry')""", (run_id, dataset.flow_id)).fetchone()
    if not row or json.loads(row["progress_json"] or "{}").get("no_op"):
        raise InspectionError("completed_run_required")
    return row


def _eligible_artifacts(row):
    job = json.loads(row["job_json"] or "{}")
    artifacts = json.loads(row["artifact_json"] or "[]")
    transformed = bool(job.get("transformation", {}).get("enabled"))
    wanted = "transformed" if transformed else "saved"
    return job, [item for item in artifacts if item.get("status") == wanted]


def _resolved_dataset(db, dataset, policy, run_id=None):
    if run_id is None:
        row = db.execute("""SELECT id,job_json,artifact_json,finished_at,progress_json
            FROM flow_runs WHERE flow_id=? AND status='succeeded'
            AND trigger_type NOT IN ('sql_retry','view_retry')
            AND COALESCE(json_extract(progress_json,'$.no_op'),0)=0 ORDER BY id DESC LIMIT 1""",
            (dataset.flow_id,)).fetchone()
        if not row:
            return dataset, None, "no_completed_runs"
    else:
        row = _run(db, dataset, run_id)
    job, artifacts = _eligible_artifacts(row)
    if not artifacts:
        return dataset, row, "supported_output_missing"
    try:
        columns = discover_columns(artifacts, dataset, policy, lambda: False)
    except InspectionError as exc:
        return dataset, row, str(exc)
    return dataset.model_copy(update={"columns": columns}), row, None


def catalog(policy):
    output = []
    with manifest(policy) as db:
        for configured in policy.datasets:
            dataset, latest, gap = _resolved_dataset(db, configured, policy)
            rows = db.execute("""SELECT id,finished_at FROM flow_runs
                WHERE flow_id=? AND status='succeeded' AND trigger_type NOT IN ('sql_retry','view_retry')
                AND COALESCE(json_extract(progress_json,'$.no_op'),0)=0
                ORDER BY id DESC LIMIT 36""", (dataset.flow_id,)).fetchall()
            context = {}
            if latest:
                job = json.loads(latest["job_json"] or "{}")
                context = {"acquisition_type": job.get("acquisition_type"),
                           "reporting_periods": job.get("downloads", {}).get("periods"),
                           "period_strategy": job.get("downloads", {}).get("period_strategy"),
                           "transformation_enabled": bool(job.get("transformation", {}).get("enabled")),
                           "outputs": ["download"] + (["sql"] if job.get("sql_handoff", {}).get("enabled") else [])}
            output.append({"id": dataset.id, "flow_id": dataset.flow_id,
                "columns": [column.name for column in dataset.columns],
                "sql_enabled": bool(dataset.schema_name and dataset.table_name),
                "availability": "ready" if not gap else "unverified",
                "gap_reason": gap, "context": context,
                "runs": [dict(row) for row in rows]})
    return {"catalog_revision": revision(policy), "datasets": output}


def _target_stable(db, dataset, run_id):
    if not dataset.exclusive_replace_target:
        return False
    active = db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running') LIMIT 1", (dataset.flow_id,)).fetchone()
    latest = db.execute("SELECT MAX(id) FROM flow_runs WHERE flow_id=? AND trigger_type<>'view_retry'", (dataset.flow_id,)).fetchone()[0]
    return not active and latest == run_id


def period_values(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in period_values(item)]
    return []


def reporting_scope(job, artifacts, dataset, finished_at):
    downloads = job.get("downloads", {})
    periods = sorted(set(period_values(downloads.get("periods"))))
    signature = periods
    if dataset.period_comparison == "completed_windows" and periods:
        try:
            starts = []
            for period in periods:
                match = re.fullmatch(r"(\d{4})-W(\d{2})", period)
                if not match:
                    raise ValueError()
                starts.append(date.fromisocalendar(int(match[1]), int(match[2]), 1))
            finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00")).date()
            if all(start + timedelta(days=7) <= finished for start in starts):
                signature = {"completed_weeks": [(max(starts) - start).days // 7 for start in starts]}
        except ValueError:
            pass
    download_scope = {key: downloads.get(key) for key in
                      ("mode", "period_strategy", "period_unit", "period_size", "file_format",
                       "excel_trim", "excel_worksheets", "asap_download_type",
                       "export_report_title", "export_filter_details")}
    scripts = sorted({item.get("script_checksum") or "unverified" for item in artifacts}) if job.get("transformation", {}).get("enabled") else []
    return digest({"acquisition_type": job.get("acquisition_type"),
        "selections": job.get("selections_revision", job.get("selections")),
        "report": job.get("report_revision", job.get("report")),
        "local_file": job.get("local_file"), "outlook_source": job.get("outlook_source"),
        "downloads": download_scope, "periods": signature,
        "transformation": job.get("transformation"), "scripts": scripts,
        "dependencies_revision": dataset.transformation_revision, "sql": job.get("sql_handoff")})


def _verified_dsn(dataset):
    dsn = str(reader_config().get("reader_dsn") or "")
    if not dsn:
        raise InspectionError("sql_reader_not_configured")
    from psycopg2.extensions import parse_dsn
    parameters = parse_dsn(dsn)
    host = str(parameters.get("host") or "").casefold().rstrip(".")
    requested = dataset.source_server.casefold().rstrip(".")
    requested = requested.split(":", 1)[0]
    database = str(parameters.get("dbname") or "")
    if not host or requested != host or dataset.source_database != database:
        # This check occurs before psycopg2 receives the DSN, so credentials are
        # never sent to a target named only by an editable Flow.
        raise InspectionError("sql_server_identity_unverified")
    return dsn


def inspect(body, policy, cancelled):
    configured = policy.dataset(body.dataset_id)
    key = str(reader_config().get("category_key") or "")
    if len(key) < 32:
        raise InspectionError("category_key_not_configured")
    salt = key.encode()
    with manifest(policy) as db:
        dataset, row, gap = _resolved_dataset(db, configured, policy, body.run_id)
        if gap:
            raise InspectionError(gap)
        job, artifacts = _eligible_artifacts(row)
        initial_stable = _target_stable(db, dataset, body.run_id)
    scope = reporting_scope(job, artifacts, dataset, row["finished_at"])
    target = job.get("sql_handoff", {})
    comparable = (initial_stable and target.get("enabled") and target.get("mode") == "replace"
                  and target.get("schema") == dataset.schema_name and target.get("table") == dataset.table_name)
    if body.source == "download":
        profile = profile_csv(artifacts, dataset, policy, salt, cancelled)
    else:
        if not dataset.schema_name or not dataset.table_name:
            raise InspectionError("sql_not_registered")
        profile = profile_sql(dataset, _verified_dsn(dataset), salt, cancelled)
        comparable = comparable and not profile.get("in_recovery")
        profile.pop("database", None)
        profile.pop("in_recovery", None)
        with manifest(policy) as db:
            comparable = comparable and _target_stable(db, dataset, body.run_id)
    expected = set(period_values(job.get("downloads", {}).get("periods")))
    observed = set(period_values([item.get("period_key") for item in artifacts]))
    profile["missing_periods"] = len(expected - observed) if observed else None
    transformed = bool(job.get("transformation", {}).get("enabled"))
    baseline_eligible = not transformed or all(item.get("script_checksum") for item in artifacts)
    dataset_revision = digest({"flow_id": dataset.flow_id,
        "source": [dataset.source_server, dataset.source_database, dataset.schema_name, dataset.table_name],
        "schema": profile.get("schema"), "scope": scope,
        "transformation": dataset.transformation_revision,
        "category_key_revision": digest(key)})
    result = {"dataset_id": dataset.id, "dataset_revision": dataset_revision,
              "flow_id": dataset.flow_id, "run_id": body.run_id,
              "baseline_eligible": baseline_eligible, "source": body.source,
              "finished_at": row["finished_at"], "scope": scope,
              "catalog_revision": revision(policy), "sql_comparable": bool(comparable),
              "observed_at": datetime.now(timezone.utc).isoformat(), "profile": profile}
    result["evidence_id"] = digest([dataset_revision, body.run_id, body.source,
                                    profile.get("identity"), profile.get("schema")])
    return result


@app.get("/catalog")
def read_catalog(request: Request):
    authorize(request)
    try:
        return catalog(load_policy())
    except Exception:
        raise HTTPException(503, "reader_catalog_unavailable") from None


@app.post("/inspect")
async def read_profile(body: ReadRequest, request: Request):
    authorize(request)
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "reader_busy")
    cancellation = threading.Event()
    deadline = time.monotonic() + 120
    def cancelled():
        return cancellation.is_set() or time.monotonic() >= deadline
    try:
        task = asyncio.create_task(asyncio.to_thread(inspect, body, load_policy(), cancelled))
        while not task.done():
            if await request.is_disconnected() or time.monotonic() >= deadline:
                cancellation.set()
            await asyncio.wait({task}, timeout=0.1)
        result = await task
        if cancelled():
            raise InspectionError("inspection_cancelled_or_timed_out")
        return result
    except InspectionError as exc:
        raise HTTPException(422, str(exc)) from None
    except asyncio.CancelledError:
        cancellation.set()
        if "task" in locals():
            await asyncio.shield(task)
        raise
    except Exception:
        raise HTTPException(503, "inspection_unavailable") from None
    finally:
        cancellation.set()
        _lock.release()
