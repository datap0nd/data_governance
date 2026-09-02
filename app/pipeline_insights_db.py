"""Rebuildable SQLite storage for Pipeline samples and edge explanations."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import PIPELINE_INSIGHTS_DB_PATH
from app.database import SQLITE_BUSY_TIMEOUT_MS


SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relation_samples (
    identity_key       TEXT PRIMARY KEY,
    source_id          INTEGER,
    server_name        TEXT NOT NULL,
    database_name      TEXT NOT NULL,
    schema_name        TEXT NOT NULL,
    relation_name      TEXT NOT NULL,
    relation_kind      TEXT NOT NULL,
    columns_json       TEXT,
    rows_json          TEXT,
    sample_hash        TEXT,
    sampled_at         TEXT,
    truncated          INTEGER NOT NULL DEFAULT 0,
    last_attempt_at    TEXT NOT NULL,
    last_attempt_status TEXT NOT NULL,
    error_code         TEXT,
    error_message      TEXT
);
CREATE INDEX IF NOT EXISTS idx_relation_samples_source
    ON relation_samples(source_id);

CREATE TABLE IF NOT EXISTS relation_schemas (
    identity_key       TEXT PRIMARY KEY,
    columns_json       TEXT NOT NULL,
    observed_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edge_explanations (
    edge_key           TEXT PRIMARY KEY,
    edge_kind          TEXT NOT NULL,
    from_key           TEXT NOT NULL,
    to_key             TEXT NOT NULL,
    text               TEXT NOT NULL,
    origin             TEXT NOT NULL,
    confidence         TEXT,
    structural_hash    TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    model              TEXT,
    generated_at       TEXT NOT NULL,
    last_attempt_at    TEXT NOT NULL,
    last_attempt_status TEXT NOT NULL,
    error_code         TEXT
);
CREATE INDEX IF NOT EXISTS idx_edge_explanations_kind
    ON edge_explanations(edge_kind);
"""


def _connect() -> sqlite3.Connection:
    path = Path(PIPELINE_INSIGHTS_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_pipeline_insights_db() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()
        if row is not None and int(row["value"]) > SCHEMA_VERSION:
            raise RuntimeError("Pipeline Insights cache was created by a newer Metronome version")
        conn.execute(
            """INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_insights_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
