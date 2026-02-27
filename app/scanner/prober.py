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

CSV_PATH = BASE_DIR / "latest_upload_date.csv"


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
    if skipped_names:
        summary["skipped_tables"] = skipped_names[:20]
    logger.info("Probe completed: %s", summary)
    return summary
