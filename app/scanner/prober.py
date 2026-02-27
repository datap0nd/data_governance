"""
PostgreSQL source prober — reads last-activity timestamps from a CSV file
and matches them to stored PostgreSQL sources.

Place `latest_upload_date.csv` in the data_governance project root (same level as app/).
Expected CSV columns: schema_name, table_name, last_activity
"""

import csv
import logging
from datetime import datetime, timezone

from app.config import BASE_DIR
from app.database import get_db

logger = logging.getLogger(__name__)

CSV_PATH = BASE_DIR.parent / "latest_upload_date.csv"

# Staleness thresholds (in days)
FRESH_MAX_DAYS = 31
STALE_MAX_DAYS = 90


def _compute_status(last_activity_str: str | None) -> str:
    """Compute freshness status based on age of last_activity.

    <= 31 days: fresh
    31-90 days: stale
    > 90 days: error
    Unparseable or missing: unknown
    """
    if not last_activity_str:
        return "unknown"

    try:
        # Try common date formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(last_activity_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return "unknown"

        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= FRESH_MAX_DAYS:
            return "fresh"
        elif age_days <= STALE_MAX_DAYS:
            return "stale"
        else:
            return "error"
    except Exception:
        return "unknown"


def run_probe() -> dict:
    """Read last-activity timestamps from CSV and store as probe results.

    Returns a summary dict with the number of sources matched/updated.
    """
    if not CSV_PATH.exists():
        return {"status": "skipped", "error": f"CSV not found: {CSV_PATH}"}

    now = datetime.now(timezone.utc).isoformat()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)

    matched = 0
    skipped = 0
    skipped_names = []

    with get_db() as db:
        for row in rows:
            if len(row) < 3:
                continue
            schema_name, table_name, last_activity = row[0].strip(), row[1].strip(), row[2].strip()
            match_pattern = f"%{schema_name}.{table_name}"

            # Find matching source: name ends with "schema.table" and type is postgresql
            source = db.execute(
                "SELECT id, name FROM sources WHERE name LIKE ? AND type = 'postgresql'",
                (match_pattern,),
            ).fetchone()

            if not source:
                skipped += 1
                skipped_names.append(f"{schema_name}.{table_name}")
                continue

            last_activity_str = last_activity if last_activity else None
            status = _compute_status(last_activity_str)

            db.execute(
                """INSERT INTO source_probes (source_id, probed_at, last_data_at, status)
                   VALUES (?, ?, ?, ?)""",
                (source["id"], now, last_activity_str, status),
            )

            # Auto-create action for stale/error sources (if not already open)
            if status in ("stale", "error"):
                existing_action = db.execute(
                    "SELECT id FROM actions WHERE source_id = ? AND type = ? AND status NOT IN ('resolved', 'expected')",
                    (source["id"], f"{status}_source"),
                ).fetchone()
                if not existing_action:
                    # Find an owner from linked reports
                    owner_row = db.execute(
                        """SELECT r.owner FROM report_tables rt
                           JOIN reports r ON r.id = rt.report_id
                           WHERE rt.source_id = ? AND r.owner IS NOT NULL
                           LIMIT 1""",
                        (source["id"],),
                    ).fetchone()
                    assigned = owner_row["owner"] if owner_row else None
                    action_type = f"{status}_source"
                    msg = f"Source data is {'older than 90 days' if status == 'error' else '31-90 days old'}"
                    db.execute(
                        """INSERT INTO actions (source_id, type, status, assigned_to, notes, created_at)
                           VALUES (?, ?, 'open', ?, ?, ?)""",
                        (source["id"], action_type, assigned, msg, now),
                    )

            matched += 1

    summary = {
        "probed_at": now,
        "matched": matched,
        "skipped": skipped,
        "total_rows": len(rows),
        "status": "completed",
        "csv_header": header,
    }
    if skipped_names:
        summary["skipped_tables"] = skipped_names[:20]
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
