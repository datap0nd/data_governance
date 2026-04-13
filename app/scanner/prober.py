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
FRESH_MAX_DAYS = 0

# Source types that reference local/network files
FILE_SOURCE_TYPES = {"csv", "excel", "folder"}

# PostgreSQL source types
PG_SOURCE_TYPES = {"postgresql"}


def _compute_status(last_activity_str: str | None,
                    fresh_max: int = FRESH_MAX_DAYS,
                    stale_max: int = 0) -> str:
    """Compute freshness status based on age of last_activity.

    <= fresh_max days: fresh
    > fresh_max days: outdated
    Unparseable or missing: unknown
    """
    if not last_activity_str:
        return "unknown"

    try:
        dt = None
        try:
            dt = datetime.fromisoformat(last_activity_str)
        except (ValueError, TypeError):
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
    ]
    for d in search_dirs:
        candidate = d / name
        if candidate.exists():
            return candidate

    return None


def _probe_file_source(db, source_id: int, file_path: str, now: str,
                       fresh_max: int = FRESH_MAX_DAYS) -> str:
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
    status = _compute_status(mod_time.isoformat(), fresh_max)

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
    statuses = {"fresh": 0, "outdated": 0, "unknown": 0}
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
                    status = _compute_status(latest_iso, fm)
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
                _create_action_and_alert(db, src["id"], status, now, fm)
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
                             fresh_max: int = FRESH_MAX_DAYS):
    """Create an action item and alert for outdated sources if not already open."""
    if status != "outdated":
        return

    action_type = "stale_source"
    severity = "critical"
    msg = f"Source data is older than {fresh_max} days"

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


def _check_dependency_freshness(db, now: str, log_lines: list):
    """Check if any MV's upstream sources have newer data than the MV itself.

    For each source that has dependencies (via source_dependencies),
    compare the MV's last_data_at with each upstream source's last_data_at.
    If upstream is newer, create a warning alert.
    """
    deps = db.execute("""
        SELECT sd.source_id, sd.depends_on_id,
               s_mv.name AS mv_name,
               s_up.name AS upstream_name,
               sp_mv.last_data_at AS mv_last_data,
               sp_up.last_data_at AS upstream_last_data
        FROM source_dependencies sd
        JOIN sources s_mv ON s_mv.id = sd.source_id
        JOIN sources s_up ON s_up.id = sd.depends_on_id
        LEFT JOIN (
            SELECT source_id, last_data_at,
                   ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
            FROM source_probes WHERE last_data_at IS NOT NULL
        ) sp_mv ON sp_mv.source_id = sd.source_id AND sp_mv.rn = 1
        LEFT JOIN (
            SELECT source_id, last_data_at,
                   ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
            FROM source_probes WHERE last_data_at IS NOT NULL
        ) sp_up ON sp_up.source_id = sd.depends_on_id AND sp_up.rn = 1
        WHERE s_mv.archived = 0 AND s_up.archived = 0
    """).fetchall()

    for dep in deps:
        mv_data = dep["mv_last_data"]
        up_data = dep["upstream_last_data"]

        if not mv_data or not up_data:
            continue

        try:
            mv_dt = datetime.fromisoformat(mv_data)
            up_dt = datetime.fromisoformat(up_data)
            if mv_dt.tzinfo is None:
                mv_dt = mv_dt.replace(tzinfo=timezone.utc)
            if up_dt.tzinfo is None:
                up_dt = up_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        if up_dt > mv_dt:
            mv_name = dep["mv_name"].replace("\\", "/").split("/")[-1]
            up_name = dep["upstream_name"].replace("\\", "/").split("/")[-1]
            delta = up_dt - mv_dt
            hours = int(delta.total_seconds() / 3600)

            msg = (
                f"Upstream {up_name} has data from "
                f"{up_dt.strftime('%Y-%m-%d %H:%M')} but MV {mv_name} "
                f"last refreshed {mv_dt.strftime('%Y-%m-%d %H:%M')} "
                f"({hours}h behind)"
            )

            # Only create alert if no existing unresolved one for this MV
            existing = db.execute(
                """SELECT id FROM alerts
                   WHERE source_id = ? AND severity = 'warning'
                   AND message LIKE '%behind%'
                   AND acknowledged = 0 AND resolution_status IS NULL""",
                (dep["source_id"],),
            ).fetchone()
            if not existing:
                # Find owner from linked report
                owner_row = db.execute(
                    """SELECT r.owner FROM report_tables rt
                       JOIN reports r ON r.id = rt.report_id
                       WHERE rt.source_id = ? AND r.owner IS NOT NULL
                       LIMIT 1""",
                    (dep["source_id"],),
                ).fetchone()
                assigned = owner_row["owner"] if owner_row else None

                db.execute(
                    "INSERT INTO alerts (source_id, severity, message, assigned_to, created_at) VALUES (?, 'warning', ?, ?, ?)",
                    (dep["source_id"], msg, assigned, now),
                )

            log_lines.append(f"DEP: {mv_name} <- {up_name} ({hours}h behind)")


def run_probe() -> dict:
    """Probe all sources for freshness.

    1. File-based sources (Excel): check file modification time
    2. PostgreSQL sources: READ-ONLY query using track_commit_timestamp
    3. Other DB sources: mark as unknown (no direct connection)
    4. Dependency freshness: flag MVs with stale upstream data

    Returns a summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    probed = 0
    file_probed = 0
    pg_probed = 0
    skipped = 0
    statuses = {"fresh": 0, "outdated": 0, "unknown": 0}
    log_lines = []

    with get_db() as db:
        # 1. Probe file-based sources
        file_sources = db.execute(
            "SELECT id, name, type, connection_info, custom_fresh_days FROM sources WHERE type IN ('csv', 'excel', 'folder')"
        ).fetchall()

        for src in file_sources:
            file_path = src["connection_info"] or src["name"]
            fm = src["custom_fresh_days"] or FRESH_MAX_DAYS
            status = _probe_file_source(db, src["id"], file_path, now, fm)
            statuses[status] = statuses.get(status, 0) + 1
            _create_action_and_alert(db, src["id"], status, now, fm)
            file_probed += 1
            probed += 1
            short = file_path.replace("\\", "/").split("/")[-1]
            log_lines.append(f"FILE: {short} - {status}")

        # 2. Probe PostgreSQL sources (READ-ONLY)
        pg_sources = db.execute(
            "SELECT id, name, type, connection_info, custom_fresh_days FROM sources WHERE type = 'postgresql'"
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

        # 4. Dependency freshness check: flag MVs whose upstream data is newer
        _check_dependency_freshness(db, now, log_lines)

    log_text = "\n".join(log_lines) if log_lines else "No sources to probe."
    finished = datetime.now(timezone.utc).isoformat()

    # Record probe run
    with get_db() as db:
        db.execute(
            """INSERT INTO probe_runs (started_at, finished_at, sources_probed, fresh, stale, outdated, unknown, status, log)
               VALUES (?, ?, ?, ?, 0, ?, ?, 'completed', ?)""",
            (now, finished, probed, statuses.get("fresh", 0),
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
