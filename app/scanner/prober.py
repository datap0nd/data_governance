"""
Source prober — checks freshness of data sources.

Supports:
1. File-based sources (CSV, Excel): checks file modification time at the path
2. PostgreSQL sources: reads last-activity timestamps from a CSV file

Place `latest_upload_date.csv` in the data_governance project root (same level as app/).
Expected CSV columns: schema_name, table_name, last_activity
"""

import csv
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import BASE_DIR
from app.database import get_db

logger = logging.getLogger(__name__)

CSV_PATH = BASE_DIR.parent / "latest_upload_date.csv"

# Staleness thresholds (in days)
FRESH_MAX_DAYS = 31
STALE_MAX_DAYS = 90

# Source types that reference local/network files
FILE_SOURCE_TYPES = {"csv", "excel", "folder"}


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

    # Extract filename — handle both Unix and Windows path separators
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
        # File not accessible — mark as unknown (can't determine freshness)
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

    1. File-based sources (CSV, Excel): check file modification time
    2. PostgreSQL sources: read from latest_upload_date.csv
    3. Other DB sources: mark as unknown (can't probe without connection)

    Returns a summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    probed = 0
    file_probed = 0
    csv_probed = 0
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
            log_lines.append(f"FILE: {short} → {status}")

        # 2. Probe PostgreSQL sources from CSV
        if CSV_PATH.exists():
            from app.scanner import read_csv_rows
            all_rows = read_csv_rows(CSV_PATH)
            csv_rows = all_rows[1:] if all_rows else []  # skip header row

            for row in csv_rows:
                if len(row) < 3:
                    continue
                schema_name, table_name, last_activity = row[0].strip(), row[1].strip(), row[2].strip()
                match_pattern = f"%{schema_name}.{table_name}"

                source = db.execute(
                    "SELECT id, name, custom_fresh_days, custom_stale_days FROM sources WHERE name LIKE ? AND type = 'postgresql'",
                    (match_pattern,),
                ).fetchone()

                if not source:
                    skipped += 1
                    continue

                fm = source["custom_fresh_days"] or FRESH_MAX_DAYS
                sm = source["custom_stale_days"] or STALE_MAX_DAYS
                last_activity_str = last_activity if last_activity else None
                status = _compute_status(last_activity_str, fm, sm)

                db.execute(
                    "INSERT INTO source_probes (source_id, probed_at, last_data_at, status) VALUES (?, ?, ?, ?)",
                    (source["id"], now, last_activity_str, status),
                )
                statuses[status] = statuses.get(status, 0) + 1
                _create_action_and_alert(db, source["id"], status, now, fm, sm)
                csv_probed += 1
                probed += 1
                log_lines.append(f"PG: {schema_name}.{table_name} → {status}")

        # 3. Mark remaining DB sources as unknown if not yet probed this run
        db_sources = db.execute(
            "SELECT id, name, type FROM sources WHERE type NOT IN ('csv', 'excel', 'folder', 'postgresql')"
        ).fetchall()

        for src in db_sources:
            # Check if we already probed this source in a previous step
            recent = db.execute(
                "SELECT id FROM source_probes WHERE source_id = ? AND probed_at = ?",
                (src["id"], now),
            ).fetchone()
            if recent:
                continue

            db.execute(
                "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'no_connection', ?)",
                (src["id"], now, f"Cannot probe {src['type']} sources without direct connection"),
            )
            statuses["unknown"] = statuses.get("unknown", 0) + 1
            probed += 1
            log_lines.append(f"DB: {src['name'][:40]} → unknown (no connection)")

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
        "csv_probed": csv_probed,
        "skipped": skipped,
        "statuses": statuses,
        "status": "completed",
        "log": log_text,
    }
    logger.info("Probe completed: %s", summary)
    return summary


def simulate_probe() -> dict:
    """Generate simulated source_probes entries with randomized freshness.

    Uses source type to weight probabilities realistically:
    - Database sources: 70% fresh, 20% stale, 10% outdated
    - File sources (csv, excel): 50% fresh, 30% stale, 20% outdated
    - Other sources: 60% fresh, 25% stale, 15% outdated
    """
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    probed = 0
    statuses = {"fresh": 0, "stale": 0, "outdated": 0}
    log_lines = []

    # Weights: (fresh, stale, outdated)
    DB_TYPES = {"postgresql", "sql", "sqlserver", "mysql", "oracle", "sql server"}
    FILE_TYPES = {"csv", "excel", "folder"}

    def _random_last_data(source_type: str,
                          fresh_max: int = FRESH_MAX_DAYS,
                          stale_max: int = STALE_MAX_DAYS) -> tuple[str, str]:
        src_t = (source_type or "").lower()
        if src_t in DB_TYPES:
            weights = [0.70, 0.20, 0.10]
        elif src_t in FILE_TYPES:
            weights = [0.50, 0.30, 0.20]
        else:
            weights = [0.60, 0.25, 0.15]

        bucket = random.choices(["fresh", "stale", "outdated"], weights=weights, k=1)[0]
        if bucket == "fresh":
            days_ago = random.randint(0, fresh_max)
        elif bucket == "stale":
            days_ago = random.randint(fresh_max + 1, stale_max)
        else:
            days_ago = random.randint(stale_max + 1, max(stale_max + 2, 365))

        # Add some hour/minute variation
        hours_ago = random.randint(0, 23)
        mins_ago = random.randint(0, 59)
        dt = now - timedelta(days=days_ago, hours=hours_ago, minutes=mins_ago)
        status = _compute_status(dt.isoformat(), fresh_max, stale_max)
        return dt.isoformat(), status

    with get_db() as db:
        # Delete previous simulated probes
        db.execute("DELETE FROM source_probes WHERE message = 'simulated'")

        sources = db.execute("SELECT id, name, type, custom_fresh_days, custom_stale_days FROM sources").fetchall()

        for src in sources:
            fm = src["custom_fresh_days"] or FRESH_MAX_DAYS
            sm = src["custom_stale_days"] or STALE_MAX_DAYS
            last_data_str, status = _random_last_data(src["type"], fm, sm)
            db.execute(
                "INSERT INTO source_probes (source_id, probed_at, last_data_at, status, message) VALUES (?, ?, ?, ?, 'simulated')",
                (src["id"], now_str, last_data_str, status),
            )
            _create_action_and_alert(db, src["id"], status, now_str, fm, sm)
            statuses[status] = statuses.get(status, 0) + 1
            probed += 1
            short = src["name"].replace("\\", "/").split("/")[-1]
            log_lines.append(f"SIM: {short} → {status}")

    log_text = "\n".join(log_lines) if log_lines else "No sources to simulate."
    finished = datetime.now(timezone.utc).isoformat()

    # Record probe run
    with get_db() as db:
        db.execute(
            """INSERT INTO probe_runs (started_at, finished_at, sources_probed, fresh, stale, outdated, unknown, status, log)
               VALUES (?, ?, ?, ?, ?, ?, 0, 'completed', ?)""",
            (now_str, finished, probed, statuses.get("fresh", 0), statuses.get("stale", 0),
             statuses.get("outdated", 0), log_text),
        )

        # Backfill: assign owners to alerts that don't have one yet
        _backfill_alert_owners(db)

    summary = {
        "probed_at": now_str,
        "probed": probed,
        "matched": probed,
        "skipped": 0,
        "statuses": statuses,
        "status": "completed",
        "log": log_text,
    }
    logger.info("Simulated probe completed: %s", summary)
    return summary


def probe_debug() -> dict:
    """Return diagnostic info to help debug matching issues."""
    if not CSV_PATH.exists():
        return {"error": f"CSV not found: {CSV_PATH}"}

    from app.scanner import read_csv_rows
    all_rows = read_csv_rows(CSV_PATH)
    header = all_rows[0] if all_rows else None
    csv_rows = [row for row in all_rows[1:] if len(row) >= 3]

    csv_samples = [
        {"schema": r[0].strip(), "table": r[1].strip(), "pattern": f"%{r[0].strip()}.{r[1].strip()}"}
        for r in csv_rows[:10]
    ]

    with get_db() as db:
        pg_sources = db.execute(
            "SELECT id, name, type FROM sources WHERE type = 'postgresql' LIMIT 20"
        ).fetchall()

    return {
        "csv_path": str(CSV_PATH),
        "csv_header": header,
        "csv_row_count": len(csv_rows),
        "csv_samples": csv_samples,
        "postgresql_sources": [{"id": s["id"], "name": s["name"]} for s in pg_sources],
    }
