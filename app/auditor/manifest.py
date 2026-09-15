"""Publish an exact, sanitized snapshot for the separate reader process."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

from app import database
from app.database import get_db
from auditor_reader.policy import Dataset, Policy

_lock = threading.Lock()
_PERIOD = re.compile(r"^[0-9A-Za-z_. -]{1,40}$")


def stamp(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _periods(value):
    if isinstance(value, str) and _PERIOD.fullmatch(value):
        return value
    if isinstance(value, list):
        return [_periods(item) for item in value[:100]]
    return None


def safe_job(job, source_type="unknown"):
    downloads = job.get("downloads", {}) if isinstance(job.get("downloads"), dict) else {}
    target = job.get("sql_handoff", {}) if isinstance(job.get("sql_handoff"), dict) else {}
    transformation = job.get("transformation", {}) if isinstance(job.get("transformation"), dict) else {}
    local = job.get("local_file", {}) if isinstance(job.get("local_file"), dict) else {}
    outlook = job.get("outlook_source", {}) if isinstance(job.get("outlook_source"), dict) else {}
    return {
        "acquisition_type": str(source_type)[:32],
        "selections_revision": stamp(job.get("selections")),
        "report_revision": stamp(job.get("report")),
        "local_file": {"config_revision": stamp(local.get("config_revision")),
                       "path_revision": stamp(local.get("path")),
                       "worksheet_revision": stamp(local.get("worksheet"))},
        "outlook_source": {"subject_revision": stamp(outlook.get("subject_contains"))},
        "downloads": {**{key: downloads.get(key) for key in
                         ("mode", "period_strategy", "period_unit", "period_size", "file_format",
                          "excel_trim", "excel_worksheets", "asap_download_type",
                          "export_report_title", "export_filter_details")},
                      "periods": _periods(downloads.get("periods"))},
        "transformation": {"enabled": bool(transformation.get("enabled")),
                           "revision": stamp(transformation.get("script_path"))},
        "sql_handoff": {key: target.get(key) for key in
                        ("enabled", "mode", "uppercase", "server", "database", "schema", "table")},
    }


def _artifacts(raw):
    output = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        path = item.get("file_path")
        checksum = item.get("checksum")
        if (isinstance(path, str) and path and isinstance(checksum, str)
                and len(checksum) == 64 and Path(path).suffix.casefold() == ".csv"):
            output.append({key: item.get(key) for key in
                           ("file_path", "status", "checksum", "period_key", "script_checksum")})
    return output[:64]


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def discover():
    rows = []
    datasets = []
    flow_ids = []
    with get_db() as source:
        flows = source.execute("SELECT id,name,source_type FROM flows ORDER BY id").fetchall()
        for flow in flows:
            flow_id = int(flow["id"])
            flow_ids.append(flow_id)
            allowed_paths = []
            latest_target = {}
            run_rows = source.execute("""SELECT id,flow_id,status,trigger_type,job_json,artifact_json,
                    finished_at,progress_json FROM flow_runs WHERE flow_id=?
                    ORDER BY id DESC LIMIT 36""", (flow_id,)).fetchall()
            for row in run_rows:
                try:
                    job_raw = json.loads(row["job_json"] or "{}")
                    artifacts = _artifacts(json.loads(row["artifact_json"] or "[]"))
                    progress_raw = json.loads(row["progress_json"] or "{}")
                except (TypeError, ValueError):
                    job_raw, artifacts, progress_raw = {}, [], {}
                job = safe_job(job_raw, flow["source_type"])
                if not latest_target and row["status"] == "succeeded":
                    latest_target = job.get("sql_handoff", {})
                allowed_paths.extend(item["file_path"] for item in artifacts)
                rows.append((row["id"], flow_id, row["status"], row["trigger_type"],
                             json.dumps(job), json.dumps(artifacts), row["finished_at"],
                             json.dumps({"no_op": bool(progress_raw.get("no_op"))})))
            datasets.append(Dataset(id=f"flow_{flow_id}", flow_id=flow_id, columns=(),
                artifact_paths=tuple(sorted(set(allowed_paths))),
                schema_name=str(latest_target.get("schema") or ""),
                table_name=str(latest_target.get("table") or ""),
                source_server=str(latest_target.get("server") or ""),
                source_database=str(latest_target.get("database") or ""),
                sql_uppercase=bool(latest_target.get("uppercase")),
                exclusive_replace_target=latest_target.get("mode") == "replace",
                period_comparison="completed_windows"))
    return rows, datasets, flow_ids


def publish(cfg):
    manifest_path = Path(cfg.manifest_path)
    policy_path = Path(cfg.policy_path)
    if (not manifest_path.is_absolute() or not policy_path.is_absolute()
            or manifest_path.resolve() == Path(database.DB_PATH).resolve()
            or manifest_path.parent != policy_path.parent):
        raise ValueError("Managed auditor exchange paths are invalid.")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    rows, datasets, flow_ids = discover()
    policy = Policy(manifest_db=str(manifest_path), datasets=tuple(datasets))
    with _lock:
        fd, temporary = tempfile.mkstemp(prefix="manifest.", suffix=".sqlite", dir=manifest_path.parent)
        os.close(fd)
        try:
            db = sqlite3.connect(temporary)
            db.executescript("""CREATE TABLE manifest_format(version INTEGER NOT NULL);
                INSERT INTO manifest_format VALUES(2);
                CREATE TABLE flow_runs(id INTEGER PRIMARY KEY,flow_id INTEGER,status TEXT,trigger_type TEXT,
                job_json TEXT,artifact_json TEXT,finished_at TEXT,progress_json TEXT);""")
            db.executemany("INSERT INTO flow_runs VALUES(?,?,?,?,?,?,?,?)", rows)
            db.commit()
            db.close()
            os.replace(temporary, manifest_path)
            _write_json(policy_path, policy.model_dump(mode="json"))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"flow_ids": flow_ids, "datasets": [item.id for item in datasets],
            "catalog_revision": policy.revision}
