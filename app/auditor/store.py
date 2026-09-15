"""Trusted host persistence; the model receives no handle to this module."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.database import get_db

DEFAULTS = {"enabled": False, "flow_ids": [], "overnight": False, "time": "02:00", "next_due": None}
SCHEMA = """
CREATE TABLE IF NOT EXISTS auditor_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auditor_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL,
 trigger_type TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 schedule_key TEXT UNIQUE, selected_flows TEXT NOT NULL, coverage TEXT NOT NULL DEFAULT '{}',
 reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS auditor_one_active
 ON auditor_runs((1)) WHERE status IN ('queued','running','cancelling');
CREATE TABLE IF NOT EXISTS auditor_profiles (
 key TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, run_id INTEGER NOT NULL,
 source TEXT NOT NULL, policy_revision TEXT NOT NULL, payload TEXT NOT NULL,
 observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auditor_profiles_dataset ON auditor_profiles(dataset_id,run_id);
CREATE TABLE IF NOT EXISTS auditor_baselines (
 key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auditor_findings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
 audit_id INTEGER NOT NULL, flow_id INTEGER NOT NULL, dataset_id TEXT NOT NULL,
 kind TEXT NOT NULL, message TEXT NOT NULL, evidence TEXT NOT NULL,
 alert_id INTEGER NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc)


def init():
    with get_db() as db:
        db.executescript(SCHEMA)


def next_due(clock, value):
    if not value["enabled"] or not value["overnight"]:
        return None
    hour, minute = map(int, value["time"].split(":"))
    local = clock.astimezone(ZoneInfo("Asia/Dubai"))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(timezone.utc).isoformat()


def read_settings(db=None):
    if db is None:
        with get_db() as conn:
            return read_settings(conn)
    row = db.execute("SELECT value FROM auditor_settings WHERE id=1").fetchone()
    return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def write_settings(value):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        value = {**value, "next_due": next_due(now(), value)}
        db.execute("INSERT INTO auditor_settings(id,value) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(value),))
        if not value["enabled"]:
            db.execute("UPDATE auditor_runs SET status='cancelling',reason='disabled' WHERE status IN ('queued','running')")
    return value


def active(db, audit_id):
    row = db.execute("SELECT status FROM auditor_runs WHERE id=?", (audit_id,)).fetchone()
    return row and row[0] in {"queued", "running"} and read_settings(db)["enabled"]


def recover():
    init()
    with get_db() as db:
        db.execute("UPDATE auditor_runs SET status='interrupted',reason='service_restarted',finished_at=? WHERE status IN ('queued','running','cancelling')", (now().isoformat(),))


def projection():
    with get_db() as db:
        run = db.execute("SELECT * FROM auditor_runs ORDER BY id DESC LIMIT 1").fetchone()
        latest = dict(run) if run else None
        if latest:
            latest["coverage"] = json.loads(latest["coverage"])
            latest["selected_flows"] = json.loads(latest["selected_flows"])
        findings = [dict(row) for row in db.execute("SELECT id,flow_id,dataset_id,kind,message,alert_id,last_seen_at FROM auditor_findings ORDER BY last_seen_at DESC LIMIT 30")]
    return {"settings": read_settings(), "latest": latest, "findings": findings}


def save_profile(audit_id, evidence):
    key = f"{evidence['policy_revision']}:{evidence['dataset_id']}:{evidence['run_id']}:{evidence['source']}"
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not active(db, audit_id):
            return False
        db.execute("INSERT INTO auditor_profiles VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at",
                   (key, evidence["dataset_id"], evidence["run_id"], evidence["source"], evidence["policy_revision"], json.dumps(evidence), evidence["observed_at"]))
    return True


def emit(audit_id, finding):
    timestamp = now().isoformat()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not active(db, audit_id):
            return
        old = db.execute("SELECT id FROM auditor_findings WHERE fingerprint=?", (finding["fingerprint"],)).fetchone()
        if old:
            db.execute("UPDATE auditor_findings SET last_seen_at=? WHERE id=?", (timestamp, old[0]))
            return
        alert_id = db.execute("INSERT INTO alerts(severity,message,created_at) VALUES('warning',?,?)", (finding["message"], timestamp)).lastrowid
        db.execute("""INSERT INTO auditor_findings(fingerprint,audit_id,flow_id,dataset_id,kind,message,evidence,alert_id,created_at,last_seen_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (finding["fingerprint"], audit_id, finding["flow_id"], finding["dataset_id"], finding["kind"], finding["message"], json.dumps(finding["evidence"]), alert_id, timestamp, timestamp))
        # Existing Alerts uses actions as its canonical incident list.
        flow = db.execute("SELECT id FROM flows WHERE id=?", (finding["flow_id"],)).fetchone()
        db.execute("INSERT INTO actions(flow_id,type,status,notes,fingerprint,created_at,updated_at) VALUES(?,'data_quality','open',?,?,?,?)",
                   (flow[0] if flow else None, finding["message"], "auditor:" + finding["fingerprint"], timestamp, timestamp))


def coverage_alert(audit_id, flow_ids):
    """One host-generated notice per selected scope; model prose is excluded."""
    import hashlib
    identity = "auditor-coverage:" + hashlib.sha256(json.dumps(sorted(flow_ids)).encode()).hexdigest()
    timestamp = now().isoformat()
    message = "Data auditor could not verify all selected data. Open Data Quality → AI Auditor to review coverage and the next action. Unverified data is not a clean result."
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM auditor_runs WHERE id=?", (audit_id,)).fetchone()
        if not row or row[0] not in {"unavailable", "completed_with_gaps"} or not read_settings(db)["enabled"]:
            return
        if db.execute("SELECT 1 FROM actions WHERE fingerprint=?", (identity,)).fetchone():
            return
        db.execute("INSERT INTO alerts(severity,message,created_at) VALUES('warning',?,?)", (message, timestamp))
        db.execute("INSERT INTO actions(type,status,notes,fingerprint,created_at,updated_at) VALUES('data_quality','open',?,?,?,?)", (message, identity, timestamp, timestamp))
