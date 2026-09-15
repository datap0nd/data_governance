"""Run separately: uvicorn auditor_reader.service:app --no-access-log.

Deploy under a distinct OS account with read ACLs on the manifest and approved
artifact roots, a read-only filesystem, and network access only to PostgreSQL.
The inference server has no network path to either this service or Metronome.
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

from .policy import InspectionError, ReadRequest, digest, load_policy
from .postgres import profile_sql
from .profiles import profile_csv

app = FastAPI(title="Metronome restricted data reader", docs_url=None, redoc_url=None, openapi_url=None)
_lock = threading.Lock()


def authorize(request):
    token = os.environ.get("METRONOME_AUDIT_READER_TOKEN", "")
    try:
        loopback = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        loopback = False
    if (request.url.scheme != "https" and not loopback) or len(token) < 32 or not hmac.compare_digest(request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()):
        raise HTTPException(401, "reader_access_required")


def revision(policy):
    return digest([policy.revision, digest(os.environ.get("METRONOME_AUDIT_CATEGORY_KEY", ""))])


@contextmanager
def manifest(policy):
    # Never use app.database: it opens SQLite with write authority and hooks.
    uri = Path(policy.manifest_db).absolute().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {"manifest_format", "flow_runs"} or [row[0] for row in db.execute("SELECT version FROM manifest_format")] != [1]:
            raise InspectionError("separate_sanitized_manifest_required")
        yield db
    finally:
        db.close()


def catalog(policy):
    output = []
    with manifest(policy) as db:
        for dataset in policy.datasets:
            rows = db.execute("""SELECT id, finished_at FROM flow_runs
                WHERE flow_id=? AND status='succeeded' AND trigger_type NOT IN ('sql_retry','view_retry')
                AND COALESCE(json_extract(progress_json,'$.no_op'),0)=0
                ORDER BY id DESC LIMIT 36""", (dataset.flow_id,)).fetchall()
            output.append({"id": dataset.id, "flow_id": dataset.flow_id,
                "columns": [c.name for c in dataset.columns], "sql_enabled": bool(dataset.fingerprint),
                "runs": [dict(row) for row in rows]})
    return {"policy_revision": revision(policy), "datasets": output}


def _run(db, dataset, run_id):
    row = db.execute("""SELECT id, job_json, artifact_json, finished_at, progress_json
        FROM flow_runs WHERE id=? AND flow_id=? AND status='succeeded'
        AND trigger_type NOT IN ('sql_retry','view_retry')""", (run_id, dataset.flow_id)).fetchone()
    if not row or json.loads(row["progress_json"] or "{}").get("no_op"):
        raise InspectionError("completed_run_required")
    return row


def _target_stable(db, dataset, run_id):
    # Configuration must separately attest this is an exclusive replace target.
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
            pass  # Unrecognized/partial periods retain exact scope.
    # Exclude storage names, timestamps, and transient run-folder paths. Hash
    # actual acquisition and transformation choices, including script version.
    download_scope = {key: downloads.get(key) for key in ("mode", "period_strategy", "period_unit", "period_size", "file_format", "excel_trim", "excel_worksheets", "asap_download_type", "export_report_title", "export_filter_details")}
    scripts = sorted({a.get("script_checksum") or "unverified" for a in artifacts}) if job.get("transformation", {}).get("enabled") else []
    return digest({"selections": job.get("selections"), "report": job.get("report"),
        "local_file": {k: job.get("local_file", {}).get(k) for k in ("path", "worksheet", "config_revision")},
        "outlook_subject": job.get("outlook_source", {}).get("subject_contains"),
        "downloads": download_scope, "periods": signature,
        "transformation": job.get("transformation"), "scripts": scripts,
        "dependencies_revision": dataset.transformation_revision, "sql": job.get("sql_handoff")})


def inspect(body, policy, cancelled):
    dataset = policy.dataset(body.dataset_id)
    salt = os.environ.get("METRONOME_AUDIT_CATEGORY_KEY", "").encode()
    if len(salt) < 32:
        raise InspectionError("category_key_not_configured")
    with manifest(policy) as db:
        row = _run(db, dataset, body.run_id)
        job = json.loads(row["job_json"])
        artifacts = json.loads(row["artifact_json"] or "[]")
        initial_stable = _target_stable(db, dataset, body.run_id)
    transformed = bool(job.get("transformation", {}).get("enabled"))
    artifacts = [a for a in artifacts if a.get("status") == ("transformed" if transformed else "saved")]
    scope = reporting_scope(job, artifacts, dataset, row["finished_at"])
    target = job.get("sql_handoff", {})
    comparable = initial_stable and target.get("enabled") and target.get("mode") == "replace" and target.get("schema") == dataset.schema_name and target.get("table") == dataset.table_name
    if body.source == "download":
        profile = profile_csv(artifacts, dataset, policy, salt, cancelled)
    else:
        if not dataset.fingerprint:
            raise InspectionError("sql_not_approved")
        profile = profile_sql(dataset, os.environ.get("METRONOME_AUDIT_READER_DSN", ""), salt, cancelled)
        comparable = comparable and bool(dataset.source_server) and target.get("server") == dataset.source_server and target.get("database") == profile.get("database") and not profile.get("in_recovery")
        profile.pop("database", None)
        profile.pop("in_recovery", None)
        with manifest(policy) as db:
            comparable = comparable and _target_stable(db, dataset, body.run_id)
    expected = set(period_values(job.get("downloads", {}).get("periods")))
    observed = set(period_values([a.get("period_key") for a in artifacts]))
    profile["missing_periods"] = len(expected - observed) if observed else None
    baseline_eligible = not transformed or (bool(dataset.transformation_revision) and all(a.get("script_checksum") for a in artifacts))
    return {"dataset_id": dataset.id, "flow_id": dataset.flow_id, "run_id": body.run_id,
            "baseline_eligible": baseline_eligible,
            "source": body.source, "finished_at": row["finished_at"], "scope": scope,
            "policy_revision": revision(policy), "sql_comparable": bool(comparable),
            "observed_at": datetime.now(timezone.utc).isoformat(), "profile": profile}


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
        policy = load_policy()
        task = asyncio.create_task(asyncio.to_thread(inspect, body, policy, cancelled))
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
        # Do not release single-query ownership before the reader has stopped.
        if "task" in locals():
            await asyncio.shield(task)
        raise
    except Exception:
        raise HTTPException(503, "inspection_unavailable") from None
    finally:
        cancellation.set()
        _lock.release()
