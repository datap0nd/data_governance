"""
Source prober - checks freshness of data sources.

Supports:
1. File-based sources (CSV, Excel): checks file modification time at the path
2. PostgreSQL sources: READ-ONLY queries using track_commit_timestamp
   (MAX(pg_xact_commit_timestamp(xmin)) per table for real last-write time)

Requires: track_commit_timestamp = on in postgresql.conf

WARNING: PostgreSQL probing uses PGUSER/PGPASSWORD/PGHOST environment variables.
These credentials must ONLY be used for SELECT queries.
NEVER use them for INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE,
or ANY other write/DDL operation. This is a strict, non-negotiable constraint.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import BASE_DIR, PGHOST, PGUSER, PGPASSWORD, PGDATABASE
from app.database import get_db

logger = logging.getLogger(__name__)

# Staleness thresholds (in days)
FRESH_MAX_DAYS = 31
STALE_MAX_DAYS = 90

# Source types that reference local/network files
FILE_SOURCE_TYPES = {"csv", "excel", "folder"}

# PostgreSQL source types
PG_SOURCE_TYPES = {"postgresql"}


def _compute_status(last_activity_str: str | None,
                    fresh_max: int = FRESH_MAX_DAYS,
                    stale_max: int = STALE_MAX_DAYS) -> str:
    """Compute freshness status based on age of last_activity.

    <= fresh_max days: fresh
    fresh_max-stale_max days: stale
    > stale_max days: outdated
    Unparseable or missing: unknown
    """
    if not last_activity_str:
        return "unknown"

    try:
        # Try fromisoformat first (handles full ISO 8601 with microseconds and timezone)
        dt = None
        try:
            dt = datetime.fromisoformat(last_activity_str)
        except (ValueError, TypeError):
            # Fall back to common date formats
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    dt = datetime.strptime(last_activity_str, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return "unknown"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= fresh_max:
            return "fresh"
        elif age_days <= stale_max:
            return "stale"
        else:
            return "outdated"
    except Exception:
        return "unknown"


def _find_file(file_path: str) -> Path | None:
    """Try to locate a file at its original path or common fallback locations."""
    p = Path(file_path)
    if p.exists():
        return p

    # Extract filename - handle both Unix and Windows path separators
    # On Linux, Path("C:\Data\file.xlsx").name returns the whole string with backslashes
    name = file_path.replace("\\", "/").split("/")[-1]

    search_dirs = [
        BASE_DIR,
        BASE_DIR / "test_data" / "sample_files",
        BASE_DIR / "test_data",
    ]
    for d in search_dirs:
        candidate = d / name
        if candidate.exists():
            return candidate

    return None


def _probe_file_source(db, source_id: int, file_path: str, now: str,
                       fresh_max: int = FRESH_MAX_DAYS, stale_max: int = STALE_MAX_DAYS) -> str:
    """Probe a file-based source by checking file existence and modification time.

    Returns the computed status.
    """
    p = _find_file(file_path)
    if not p:
        # File not accessible - mark as unknown (can't determine freshness)
        db.execute(
            "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
            (source_id, now, f"File not accessible: {file_path}"),
        )
        return "unknown"

    mod_time = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    status = _compute_status(mod_time.isoformat(), fresh_max, stale_max)

    db.execute(
        "INSERT INTO source_probes (source_id, probed_at, last_data_at, status, message) VALUES (?, ?, ?, ?, ?)",
        (source_id, now, mod_time.isoformat(), status, f"File modified: {mod_time.strftime('%Y-%m-%d %H:%M')}"),
    )
    return status


# ---------------------------------------------------------------------------
# PostgreSQL probing - READ-ONLY via track_commit_timestamp
#
# Uses MAX(pg_xact_commit_timestamp(xmin)) to get real last-write time.
# Requires: track_commit_timestamp = on in postgresql.conf
#
# WARNING: All queries below are strictly SELECT-only.
# NEVER add INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or TRUNCATE here.
# ---------------------------------------------------------------------------

def _get_pg_connection():
    """Get a PostgreSQL connection using environment credentials.

    Returns None if credentials are not configured.
    Connection is opened in READ-ONLY mode via SET default_transaction_read_only.
    """
    if not PGHOST or not PGUSER or not PGPASSWORD:
        return None

    try:
        import psycopg2
        conn = psycopg2.connect(
            host=PGHOST,
            user=PGUSER,
            password=PGPASSWORD,
            database=PGDATABASE,
            connect_timeout=10,
        )
        # Force read-only at the session level as an extra safeguard
        conn.set_session(readonly=True, autocommit=True)
        return conn
    except Exception as e:
        logger.warning("PostgreSQL connection failed: %s", e)
        return None


def _parse_pg_table_ref(connection_info: str, source_name: str):
    """Parse schema and table name from a source's connection_info or name.

    connection_info is typically: server/database/schema.table
    source_name is typically: server/database/schema.table

    Returns (schema, table) or None if unparseable.
    """
    ref = connection_info or source_name or ""
    # Extract the last segment which should be schema.table
    parts = ref.replace("\\", "/").split("/")
    if not parts:
        return None

    table_part = parts[-1]  # e.g. "bi_reporting.daily_sales"
    if "." in table_part:
        schema, table = table_part.split(".", 1)
        return (schema.strip(), table.strip())

    # No schema prefix - try as public.table
    if table_part.strip():
        return ("public", table_part.strip())

    return None


def _probe_pg_sources(db, pg_sources, now, log_lines) -> dict:
    """Probe PostgreSQL sources using track_commit_timestamp.

    Queries MAX(pg_xact_commit_timestamp(xmin)) per table to get the
    real last-write time. Requires track_commit_timestamp = on in
    postgresql.conf.

    READ-ONLY: Only SELECT queries are used. No data is modified in PostgreSQL.

    Returns dict of status counts.
    """
    statuses = {"fresh": 0, "stale": 0, "outdated": 0, "unknown": 0}
    pg_conn = _get_pg_connection()

    if pg_conn is None:
        for src in pg_sources:
            db.execute(
                "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
                (src["id"], now, "PostgreSQL credentials not configured"),
            )
            statuses["unknown"] += 1
            short = src["name"].replace("\\", "/").split("/")[-1]
            log_lines.append(f"PG: {short} - unknown (no credentials)")
        return statuses

    try:
        pg_cur = pg_conn.cursor()

        for src in pg_sources:
            fm = src["custom_fresh_days"] or FRESH_MAX_DAYS
            sm = src["custom_stale_days"] or STALE_MAX_DAYS
            parsed = _parse_pg_table_ref(src["connection_info"], src["name"])

            if not parsed:
                db.execute(
                    "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
                    (src["id"], now, f"Cannot parse table reference: {src['connection_info']}"),
                )
                statuses["unknown"] += 1
                short = src["name"].replace("\\", "/").split("/")[-1]
                log_lines.append(f"PG: {short} - unknown (bad ref)")
                continue

            schema, table = parsed
            short = f"{schema}.{table}"

            try:
                # READ-ONLY: get last write time via track_commit_timestamp
                # Also grab row count from pg_stat for context
                pg_cur.execute(
                    f"""SELECT MAX(pg_xact_commit_timestamp(xmin)) AS last_write,
                               COUNT(*) AS row_count
                        FROM "{schema}"."{table}" """
                )
                row = pg_cur.fetchone()

                if row is None:
                    db.execute(
                        "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
                        (src["id"], now, f"Table not found: {short}"),
                    )
                    statuses["unknown"] += 1
                    log_lines.append(f"PG: {short} - unknown (not found)")
                    continue

                last_write, row_count = row

                if last_write:
                    if last_write.tzinfo is None:
                        last_write = last_write.replace(tzinfo=timezone.utc)
                    latest_iso = last_write.isoformat()
                    status = _compute_status(latest_iso, fm, sm)
                    msg = f"Last write: {last_write.strftime('%Y-%m-%d %H:%M')} ({row_count:,} rows)"
                    db.execute(
                        "INSERT INTO source_probes (source_id, probed_at, last_data_at, row_count, status, message) VALUES (?, ?, ?, ?, ?, ?)",
                        (src["id"], now, latest_iso, row_count, status, msg),
                    )
                else:
                    # Table exists but empty or no commit timestamps
                    status = "unknown"
                    msg = f"No commit timestamps ({row_count:,} rows)"
                    db.execute(
                        "INSERT INTO source_probes (source_id, probed_at, row_count, status, message) VALUES (?, ?, ?, 'unknown', ?)",
                        (src["id"], now, row_count, msg),
                    )

                statuses[status] = statuses.get(status, 0) + 1
                _create_action_and_alert(db, src["id"], status, now, fm, sm)
                log_lines.append(f"PG: {short} - {status} ({msg})")

            except Exception as e:
                logger.warning("PG probe failed for %s: %s", short, e)
                db.execute(
                    "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
                    (src["id"], now, f"Query failed: {e}"),
                )
                statuses["unknown"] += 1
                log_lines.append(f"PG: {short} - unknown (error: {e})")

    finally:
        pg_conn.close()

    return statuses


def _create_action_and_alert(db, source_id: int, status: str, now: str,
                             fresh_max: int = FRESH_MAX_DAYS, stale_max: int = STALE_MAX_DAYS):
    """Create an action item and alert for stale/outdated sources if not already open."""
    if status not in ("stale", "outdated"):
        return

    action_type = f"{status}_source"
    severity = "critical" if status == "outdated" else "warning"
    msg = (f"Source data is older than {stale_max} days" if status == "outdated"
           else f"Source data is {fresh_max}-{stale_max} days old")

    # Find owner for assignment (from linked report)
    owner_row = db.execute(
        """SELECT r.owner FROM report_tables rt
           JOIN reports r ON r.id = rt.report_id
           WHERE rt.source_id = ? AND r.owner IS NOT NULL
           LIMIT 1""",
        (source_id,),
    ).fetchone()
    assigned = owner_row["owner"] if owner_row else None

    # Action
    existing_action = db.execute(
        "SELECT id FROM actions WHERE source_id = ? AND type = ? AND status NOT IN ('resolved', 'expected')",
        (source_id, action_type),
    ).fetchone()
    if not existing_action:
        db.execute(
            "INSERT INTO actions (source_id, type, status, assigned_to, notes, created_at) VALUES (?, ?, 'open', ?, ?, ?)",
            (source_id, action_type, assigned, msg, now),
        )

    # Alert
    existing_alert = db.execute(
        "SELECT id FROM alerts WHERE source_id = ? AND severity = ? AND acknowledged = 0",
        (source_id, severity),
    ).fetchone()
    if not existing_alert:
        db.execute(
            "INSERT INTO alerts (source_id, severity, message, assigned_to, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, severity, msg, assigned, now),
        )


def _backfill_alert_owners(db):
    """Assign owners to alerts that don't have one yet, based on linked report owners."""
    unassigned = db.execute(
        "SELECT id, source_id FROM alerts WHERE assigned_to IS NULL"
    ).fetchall()
    for alert in unassigned:
        if not alert["source_id"]:
            continue
        owner_row = db.execute(
            """SELECT r.owner FROM report_tables rt
               JOIN reports r ON r.id = rt.report_id
               WHERE rt.source_id = ? AND r.owner IS NOT NULL
               LIMIT 1""",
            (alert["source_id"],),
        ).fetchone()
        if owner_row:
            db.execute(
                "UPDATE alerts SET assigned_to = ? WHERE id = ?",
                (owner_row["owner"], alert["id"]),
            )


def run_probe() -> dict:
    """Probe all sources for freshness.

    1. File-based sources (Excel): check file modification time
    2. PostgreSQL sources: READ-ONLY query against pg_stat_user_tables
    3. Other DB sources: mark as unknown (no direct connection)

    Returns a summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    probed = 0
    file_probed = 0
    pg_probed = 0
    skipped = 0
    statuses = {"fresh": 0, "stale": 0, "outdated": 0, "unknown": 0}
    log_lines = []

    with get_db() as db:
        # 1. Probe file-based sources
        file_sources = db.execute(
            "SELECT id, name, type, connection_info, custom_fresh_days, custom_stale_days FROM sources WHERE type IN ('csv', 'excel', 'folder')"
        ).fetchall()

        for src in file_sources:
            file_path = src["connection_info"] or src["name"]
            fm = src["custom_fresh_days"] or FRESH_MAX_DAYS
            sm = src["custom_stale_days"] or STALE_MAX_DAYS
            status = _probe_file_source(db, src["id"], file_path, now, fm, sm)
            statuses[status] = statuses.get(status, 0) + 1
            _create_action_and_alert(db, src["id"], status, now, fm, sm)
            file_probed += 1
            probed += 1
            short = file_path.replace("\\", "/").split("/")[-1]
            log_lines.append(f"FILE: {short} - {status}")

        # 2. Probe PostgreSQL sources (READ-ONLY)
        pg_sources = db.execute(
            "SELECT id, name, type, connection_info, custom_fresh_days, custom_stale_days FROM sources WHERE type = 'postgresql'"
        ).fetchall()

        if pg_sources:
            pg_statuses = _probe_pg_sources(db, pg_sources, now, log_lines)
            for k, v in pg_statuses.items():
                statuses[k] = statuses.get(k, 0) + v
            pg_probed = len(pg_sources)
            probed += pg_probed

        # 3. Mark remaining DB sources (non-file, non-PG) as unknown
        unprobed_db = db.execute(
            """SELECT s.id, s.name, s.type
               FROM sources s
               WHERE s.type NOT IN ('csv', 'excel', 'folder', 'postgresql')
               AND NOT EXISTS (
                   SELECT 1 FROM source_probes sp
                   WHERE sp.source_id = s.id AND sp.probed_at = ?
               )""",
            (now,),
        ).fetchall()

        for src in unprobed_db:
            db.execute(
                "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
                (src["id"], now, f"No direct connection ({src['type']})"),
            )
            statuses["unknown"] = statuses.get("unknown", 0) + 1
            probed += 1
            short = src["name"].replace("\\", "/").split("/")[-1]
            log_lines.append(f"SKIP: {short} - unknown ({src['type']}, no connection)")

    log_text = "\n".join(log_lines) if log_lines else "No sources to probe."
    finished = datetime.now(timezone.utc).isoformat()

    # Record probe run
    with get_db() as db:
        db.execute(
            """INSERT INTO probe_runs (started_at, finished_at, sources_probed, fresh, stale, outdated, unknown, status, log)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
            (now, finished, probed, statuses.get("fresh", 0), statuses.get("stale", 0),
             statuses.get("outdated", 0), statuses.get("unknown", 0), log_text),
        )

        # Backfill: assign owners to alerts that don't have one yet
        _backfill_alert_owners(db)

    summary = {
        "probed_at": now,
        "probed": probed,
        "file_probed": file_probed,
        "pg_probed": pg_probed,
        "skipped": skipped,
        "statuses": statuses,
        "status": "completed",
        "log": log_text,
    }
    logger.info("Probe completed: %s", summary)
    return summary
