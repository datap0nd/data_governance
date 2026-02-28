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
from datetime import datetime, timezone
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


def _compute_status(last_activity_str: str | None) -> str:
    """Compute freshness status based on age of last_activity.

    <= 31 days: fresh
    31-90 days: stale
    > 90 days: outdated
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
        if age_days <= FRESH_MAX_DAYS:
            return "fresh"
        elif age_days <= STALE_MAX_DAYS:
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


def _probe_file_source(db, source_id: int, file_path: str, now: str) -> str:
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
    status = _compute_status(mod_time.isoformat())

    db.execute(
        "INSERT INTO source_probes (source_id, probed_at, last_data_at, status, message) VALUES (?, ?, ?, ?, ?)",
        (source_id, now, mod_time.isoformat(), status, f"File modified: {mod_time.strftime('%Y-%m-%d %H:%M')}"),
    )
    return status


def _create_action_and_alert(db, source_id: int, status: str, now: str):
    """Create an action item and alert for stale/outdated sources if not already open."""
    if status not in ("stale", "outdated"):
        return

    action_type = f"{status}_source"
    severity = "critical" if status == "outdated" else "warning"
    msg = f"Source data is {'older than 90 days' if status == 'outdated' else '31-90 days old'}"

    # Action
    existing_action = db.execute(
        "SELECT id FROM actions WHERE source_id = ? AND type = ? AND status NOT IN ('resolved', 'expected')",
        (source_id, action_type),
    ).fetchone()
    if not existing_action:
        owner_row = db.execute(
            """SELECT r.owner FROM report_tables rt
               JOIN reports r ON r.id = rt.report_id
               WHERE rt.source_id = ? AND r.owner IS NOT NULL
               LIMIT 1""",
            (source_id,),
        ).fetchone()
        assigned = owner_row["owner"] if owner_row else None
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
            "INSERT INTO alerts (source_id, severity, message, created_at) VALUES (?, ?, ?, ?)",
            (source_id, severity, msg, now),
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
            "SELECT id, name, type, connection_info FROM sources WHERE type IN ('csv', 'excel', 'folder')"
        ).fetchall()

        for src in file_sources:
            file_path = src["connection_info"] or src["name"]
            status = _probe_file_source(db, src["id"], file_path, now)
            statuses[status] = statuses.get(status, 0) + 1
            _create_action_and_alert(db, src["id"], status, now)
            file_probed += 1
            probed += 1
            short = file_path.replace("\\", "/").split("/")[-1]
            log_lines.append(f"FILE: {short} → {status}")

        # 2. Probe PostgreSQL sources from CSV
        if CSV_PATH.exists():
            with open(CSV_PATH, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                _header = next(reader, None)
                csv_rows = list(reader)

            for row in csv_rows:
                if len(row) < 3:
                    continue
                schema_name, table_name, last_activity = row[0].strip(), row[1].strip(), row[2].strip()
                match_pattern = f"%{schema_name}.{table_name}"

                source = db.execute(
                    "SELECT id, name FROM sources WHERE name LIKE ? AND type = 'postgresql'",
                    (match_pattern,),
                ).fetchone()

                if not source:
                    skipped += 1
                    continue

                last_activity_str = last_activity if last_activity else None
                status = _compute_status(last_activity_str)

                db.execute(
                    "INSERT INTO source_probes (source_id, probed_at, last_data_at, status) VALUES (?, ?, ?, ?)",
                    (source["id"], now, last_activity_str, status),
                )
                statuses[status] = statuses.get(status, 0) + 1
                _create_action_and_alert(db, source["id"], status, now)
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


def probe_debug() -> dict:
    """Return diagnostic info to help debug matching issues."""
    if not CSV_PATH.exists():
        return {"error": f"CSV not found: {CSV_PATH}"}

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        csv_rows = [row for row in reader if len(row) >= 3]

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
