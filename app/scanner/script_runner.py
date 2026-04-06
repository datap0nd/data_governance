"""
Script runner - orchestrates script scanning and database storage.

1. Calls walk + parse from script_scanner
2. Upserts into scripts table
3. Clears and re-inserts script_tables
4. Matches write targets against existing sources
5. Returns summary dict
"""

import logging
import re
import socket
from datetime import datetime, timezone

from app.config import SCRIPTS_PATH, SCRIPTS_PATHS
from app.database import get_db
from app.scanner.script_scanner import walk_scripts
from app.scanner.task_scheduler_scanner import MACHINE_ALIASES

logger = logging.getLogger(__name__)


def _derive_machine(script_path: str) -> tuple[str, str]:
    """Derive hostname and alias from a script's file path.

    UNC paths like \\\\MX-SHARE\\Users\\... -> hostname=MX-SHARE, alias from MACHINE_ALIASES.
    Local paths -> hostname=local machine name.
    """
    # Match UNC path: \\server\share\...
    m = re.match(r'^\\\\([^\\]+)\\', script_path)
    if m:
        hostname = m.group(1)
        alias = MACHINE_ALIASES.get(hostname.lower(), hostname)
        return hostname, alias
    # Local path
    hostname = socket.gethostname()
    alias = MACHINE_ALIASES.get(hostname.lower(), "BI Desktop")
    return hostname, alias


def _match_source(db, table_name: str) -> int | None:
    """Try to match a table name against existing sources.

    Fuzzy match: source name ends with the table name (case-insensitive).
    For example, table 'analytics.fact_sales' matches source 'pg/analytics.fact_sales'.
    """
    table_lower = table_name.lower()
    rows = db.execute("SELECT id, name FROM sources").fetchall()
    for row in rows:
        source_name = (row["name"] or "").lower()
        if source_name.endswith(table_lower):
            return row["id"]
    return None


def run_script_scan(scripts_path: str | None = None, on_progress=None) -> dict:
    """Run a full script scan and store results.

    Scans all configured paths (SCRIPTS_PATHS) unless a single path is given.
    *on_progress* is an optional callback(message: str) for live logging.
    Returns a summary dict with scan statistics.
    """
    roots = [scripts_path] if scripts_path else SCRIPTS_PATHS
    now = datetime.now(timezone.utc).isoformat()

    if on_progress:
        on_progress(f"Starting script scan across {len(roots)} path(s)")

    try:
        results = []
        for root in roots:
            if on_progress:
                on_progress(f"Scanning: {root}")
            results.extend(walk_scripts(root, on_progress=on_progress))

        if on_progress:
            on_progress(f"Storing {len(results)} scripts in database...")

        scripts_found = 0
        scripts_updated = 0
        tables_linked = 0

        with get_db() as db:
            for result in results:
                # Upsert script record
                existing = db.execute(
                    "SELECT id FROM scripts WHERE path = ?",
                    (result.path,),
                ).fetchone()

                last_mod = result.last_modified.isoformat() if result.last_modified else None
                hostname, machine_alias = _derive_machine(result.path)

                if existing:
                    script_id = existing["id"]
                    db.execute(
                        """UPDATE scripts
                           SET display_name = ?, last_modified = ?, last_scanned = ?,
                               file_size = ?, hostname = ?, machine_alias = ?,
                               updated_at = ?
                           WHERE id = ?""",
                        (result.display_name, last_mod, now,
                         result.file_size, hostname, machine_alias,
                         now, script_id),
                    )
                    scripts_updated += 1
                else:
                    cursor = db.execute(
                        """INSERT INTO scripts (path, display_name, last_modified, last_scanned,
                                               file_size, hostname, machine_alias,
                                               created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (result.path, result.display_name, last_mod, now,
                         result.file_size, hostname, machine_alias,
                         now, now),
                    )
                    script_id = cursor.lastrowid
                    scripts_found += 1

                # Clear existing table references for this script
                db.execute("DELETE FROM script_tables WHERE script_id = ?", (script_id,))

                # Insert read references
                for table_name in result.tables_read:
                    source_id = _match_source(db, table_name)
                    db.execute(
                        """INSERT INTO script_tables (script_id, table_name, direction, source_id)
                           VALUES (?, ?, 'read', ?)
                           ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
                        (script_id, table_name, source_id),
                    )
                    if source_id:
                        tables_linked += 1

                # Insert write references
                for table_name in result.tables_written:
                    source_id = _match_source(db, table_name)
                    db.execute(
                        """INSERT INTO script_tables (script_id, table_name, direction, source_id)
                           VALUES (?, ?, 'write', ?)
                           ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
                        (script_id, table_name, source_id),
                    )
                    if source_id:
                        tables_linked += 1

        summary = {
            "status": "completed",
            "scripts_found": scripts_found,
            "scripts_updated": scripts_updated,
            "scripts_total": len(results),
            "tables_linked": tables_linked,
            "scanned_paths": roots,
        }
        logger.info("Script scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("Script scan failed")
        return {
            "status": "failed",
            "error": str(e),
        }
