"""
pg_cron refresh schedule scanner.

Reads cron.job and cron.job_run_details to discover MV refresh schedules
and their execution history. Stores the cron expression in sources.refresh_schedule.

READ-ONLY: Only SELECT queries are used against PostgreSQL.
"""

import logging
import re
from datetime import datetime, timezone

from app.database import get_db
from app.scanner.prober import _get_pg_connection

logger = logging.getLogger(__name__)

# Pattern to extract MV name from REFRESH MATERIALIZED VIEW [CONCURRENTLY] [schema.]name
_REFRESH_MV_RE = re.compile(
    r"REFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?(?:\"?(\w+)\"?\.)?\"?(\w+)\"?",
    re.IGNORECASE,
)


def _parse_mv_from_command(command: str) -> tuple[str, str] | None:
    """Extract (schema, mv_name) from a pg_cron command string.

    Returns (schema, table) or None if the command isn't an MV refresh.
    """
    m = _REFRESH_MV_RE.search(command or "")
    if not m:
        return None
    schema = m.group(1) or "public"
    mv_name = m.group(2)
    return (schema, mv_name)


def scan_pg_cron() -> dict:
    """Scan pg_cron for materialized view refresh schedules.

    For each cron job that refreshes an MV:
    1. Match the MV to a source in our DB
    2. Store the cron schedule in sources.refresh_schedule
    3. Store last run info from cron.job_run_details

    READ-ONLY: Only SELECT queries against PostgreSQL.

    Returns summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    pg_conn = _get_pg_connection()

    if pg_conn is None:
        return {"status": "skipped", "reason": "No PostgreSQL credentials configured"}

    try:
        pg_cur = pg_conn.cursor()

        # Check if pg_cron is installed
        try:
            pg_cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'cron' AND table_name = 'job'"
            )
            if not pg_cur.fetchone():
                return {"status": "skipped", "reason": "pg_cron not installed"}
        except Exception:
            return {"status": "skipped", "reason": "pg_cron not installed"}

        # READ-ONLY: SELECT from cron.job
        pg_cur.execute(
            "SELECT jobid, schedule, command, database, username, active FROM cron.job ORDER BY jobid"
        )
        jobs = pg_cur.fetchall()

        if not jobs:
            return {"status": "completed", "jobs_found": 0, "matched": 0}

        # READ-ONLY: SELECT from cron.job_run_details (last run per job)
        run_details = {}
        try:
            pg_cur.execute("""
                SELECT DISTINCT ON (jobid) jobid, status, return_message,
                       start_time, end_time
                FROM cron.job_run_details
                ORDER BY jobid, start_time DESC
            """)
            for row in pg_cur.fetchall():
                run_details[row[0]] = {
                    "status": row[1],
                    "message": row[2],
                    "start_time": row[3],
                    "end_time": row[4],
                }
        except Exception as e:
            logger.warning("Could not read cron.job_run_details: %s", e)

        matched = 0
        log_lines = []

        with get_db() as db:
            for jobid, schedule, command, database, username, active in jobs:
                parsed = _parse_mv_from_command(command)
                if not parsed:
                    continue

                schema, mv_name = parsed
                full_name = f"{schema}.{mv_name}"

                # Find matching source
                source = db.execute(
                    "SELECT id, name FROM sources WHERE name LIKE ? AND archived = 0",
                    (f"%{full_name}",),
                ).fetchone()
                if not source:
                    source = db.execute(
                        "SELECT id, name FROM sources WHERE connection_info LIKE ? AND archived = 0",
                        (f"%{full_name}%",),
                    ).fetchone()

                if not source:
                    log_lines.append(f"CRON: {full_name} - no matching source found")
                    continue

                matched += 1

                # Build schedule info string
                schedule_info = schedule
                if not active:
                    schedule_info += " (disabled)"

                # Update source with cron schedule
                db.execute(
                    "UPDATE sources SET refresh_schedule = ?, updated_at = ? WHERE id = ?",
                    (schedule_info, now, source["id"]),
                )

                # Store last run info in probe message if available
                run = run_details.get(jobid)
                if run:
                    run_status = run["status"]
                    run_time = run["start_time"]
                    if run_time and hasattr(run_time, "strftime"):
                        run_time_str = run_time.strftime("%Y-%m-%d %H:%M")
                    else:
                        run_time_str = str(run_time) if run_time else "unknown"
                    log_lines.append(
                        f"CRON: {full_name} -> schedule={schedule}, "
                        f"last_run={run_time_str} ({run_status})"
                    )
                else:
                    log_lines.append(f"CRON: {full_name} -> schedule={schedule}, no run history")

        summary = {
            "status": "completed",
            "jobs_found": len(jobs),
            "mv_jobs": sum(1 for j in jobs if _parse_mv_from_command(j[2])),
            "matched": matched,
            "log": "\n".join(log_lines) if log_lines else "No MV cron jobs found.",
        }
        logger.info("pg_cron scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("pg_cron scan failed: %s", e)
        return {"status": "failed", "error": str(e)}

    finally:
        pg_conn.close()
