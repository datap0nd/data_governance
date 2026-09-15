"""Trusted publisher for a separate, sanitized, read-only reader manifest.

The reader must never receive the application's SQLite file: that file also
contains operational settings and secrets unrelated to inspection.
"""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from app.database import get_db

_lock = threading.Lock()


def stamp(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def safe_job(job):
    downloads = job.get("downloads", {})
    target = job.get("sql_handoff", {})
    transformation = job.get("transformation", {})
    return {"selections": stamp(job.get("selections")), "report": stamp(job.get("report")),
        "local_file": {"config_revision": stamp(job.get("local_file", {}).get("config_revision")),
                       "path": stamp(job.get("local_file", {}).get("path")),
                       "worksheet": stamp(job.get("local_file", {}).get("worksheet"))},
        "outlook_source": {"subject_contains": stamp(job.get("outlook_source", {}).get("subject_contains"))},
        "downloads": {k: downloads.get(k) for k in ("mode", "periods", "period_strategy", "period_unit", "period_size", "file_format", "excel_trim", "excel_worksheets", "asap_download_type", "export_report_title", "export_filter_details")},
        "transformation": {"enabled": bool(transformation.get("enabled")), "script_path": stamp(transformation.get("script_path"))},
        "sql_handoff": {k: target.get(k) for k in ("enabled", "mode", "uppercase", "server", "database", "schema", "table")}}


def publish(cfg):
    path = Path(cfg.manifest_path)
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
        raise ValueError("Audit manifest requires an administrator-created directory and an absolute file path.")
    rows = []
    with get_db() as source:
        for flow_id in cfg.flow_ids:
            for row in source.execute("SELECT id,flow_id,status,trigger_type,job_json,artifact_json,finished_at,progress_json FROM flow_runs WHERE flow_id=? ORDER BY id DESC LIMIT 100", (flow_id,)):
                job = safe_job(json.loads(row["job_json"]))
                artifacts = [{key: item.get(key) for key in ("file_path", "status", "checksum", "period_key", "script_checksum")}
                             for item in json.loads(row["artifact_json"] or "[]")]
                progress = {"no_op": bool(json.loads(row["progress_json"] or "{}").get("no_op"))}
                rows.append((row["id"], row["flow_id"], row["status"], row["trigger_type"], json.dumps(job), json.dumps(artifacts), row["finished_at"], json.dumps(progress)))
    with _lock:
        db = sqlite3.connect(path, timeout=5)
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {"manifest_format", "flow_runs"}:
                raise ValueError("Refusing to use an unrelated database as the audit manifest.")
            if tables:
                if db.execute("SELECT version FROM manifest_format").fetchall() != [(1,)]:
                    raise ValueError("Unsupported audit manifest format.")
            else:
                db.executescript("""CREATE TABLE manifest_format(version INTEGER NOT NULL);
                    INSERT INTO manifest_format VALUES(1);
                    CREATE TABLE flow_runs(id INTEGER PRIMARY KEY,flow_id INTEGER,status TEXT,trigger_type TEXT,
                    job_json TEXT,artifact_json TEXT,finished_at TEXT,progress_json TEXT);""")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM flow_runs")
            db.executemany("INSERT INTO flow_runs VALUES(?,?,?,?,?,?,?,?)", rows)
            db.commit()
        finally:
            db.close()
