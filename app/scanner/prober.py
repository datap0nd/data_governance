"""
PostgreSQL prober — connects to a PostgreSQL database and queries
last-activity timestamps, then matches them to stored sources.

Config files (in project root, same level as app/):
  - SQL_Credentials.txt  — line 1 = username, line 2 = password
  - PostgresDB.txt       — PostgreSQL host/connection string
  - query.txt            — SQL query returning (schema_name, table_name, last_activity)
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from app.config import CREDENTIALS_DIR
from app.database import get_db

logger = logging.getLogger(__name__)


def _read_file(name: str) -> str:
    """Read a config file from the credentials directory, stripped."""
    path = CREDENTIALS_DIR / name
    return path.read_text().strip()


def _get_connection():
    """Build a psycopg2 connection from the config files."""
    creds = _read_file("SQL_Credentials.txt").splitlines()
    username = creds[0].strip()
    password = creds[1].strip()
    host = _read_file("PostgresDB.txt")

    return psycopg2.connect(host=host, user=username, password=password)


def run_probe() -> dict:
    """Probe PostgreSQL for last-activity timestamps and store results.

    Returns a summary dict with the number of sources matched/updated.
    """
    query = _read_file("query.txt")
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
    finally:
        conn.close()

    matched = 0
    skipped = 0

    with get_db() as db:
        for row in rows:
            schema_name, table_name, last_activity = row[0], row[1], row[2]

            # Find matching source: name ends with "schema.table" and type is postgresql
            source = db.execute(
                "SELECT id FROM sources WHERE name LIKE ? AND type = 'postgresql'",
                (f"%{schema_name}.{table_name}",),
            ).fetchone()

            if not source:
                skipped += 1
                continue

            # Convert last_activity to string if it's a datetime
            if isinstance(last_activity, datetime):
                last_activity_str = last_activity.isoformat()
            else:
                last_activity_str = str(last_activity) if last_activity else None

            db.execute(
                """INSERT INTO source_probes (source_id, probed_at, last_data_at, status)
                   VALUES (?, ?, ?, 'fresh')""",
                (source["id"], now, last_activity_str),
            )
            matched += 1

    summary = {
        "probed_at": now,
        "matched": matched,
        "skipped": skipped,
        "total_rows": len(rows),
        "status": "completed",
    }
    logger.info("Probe completed: %s", summary)
    return summary
