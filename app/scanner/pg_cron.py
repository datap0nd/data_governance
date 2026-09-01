"""
pg_cron refresh schedule scanner.

Reads cron.job and cron.job_run_details to discover MV refresh schedules
and their execution history. Stores the cron expression in sources.refresh_schedule.

READ-ONLY: Only SELECT queries are used against PostgreSQL.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from app.config import FLOW_TIMEZONE, PGHOST, PGPORT
from app.database import get_db
from app.freshness_inheritance import (
    expire_schedule_evidence_generation,
    reconcile_all_sources,
    upsert_schedule_evidence,
)
from app.scanner.prober import _get_pg_connection
from app.scanner.tmdl_parser import _mask_sql_noncode
from app.source_identity import postgres_server_identity

logger = logging.getLogger(__name__)

# Exact PostgreSQL identifiers for a schema-qualified REFRESH target. The
# terminal lookahead prevents a quoted/hyphenated name from being truncated
# into a different valid source identity.
_PG_IDENTIFIER = r'(?:"(?:[^"]|"")*"|[A-Za-z_][\w$]*)'
_REFRESH_MV_RE = re.compile(
    rf"\bREFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?"
    rf"(?P<schema>{_PG_IDENTIFIER})\s*\.\s*"
    rf"(?P<relation>{_PG_IDENTIFIER})"
    rf"(?=\s*(?:WITH\s+(?:NO\s+)?DATA\s*)?(?:;|$))",
    re.IGNORECASE,
)


def _postgres_identifier_value(token: str) -> str:
    token = str(token or "")
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('""', '"')
    return token.lower()


def _parse_mv_from_command(command: str) -> tuple[str, str] | None:
    """Extract (schema, mv_name) from a pg_cron command string.

    Only one executable, schema-qualified target is exact enough to attach.
    Unqualified names depend on the job role's search_path, and comments or
    string literals are never executable refresh statements.
    """
    searchable = _mask_sql_noncode(command or "")
    matches = list(_REFRESH_MV_RE.finditer(searchable))
    if len(matches) != 1:
        return None
    m = matches[0]
    schema = _postgres_identifier_value(m.group("schema"))
    mv_name = _postgres_identifier_value(m.group("relation"))
    return (schema, mv_name)


def _exact_mv_source(db, *, server: str, database: str, schema: str, relation: str):
    """Return one exact, active MV source or ``None``.

    Display names and connection strings are intentionally excluded.  They are
    presentation fields and cannot distinguish identically named relations in
    different PostgreSQL databases or clusters.  Ambiguous exact identities
    also fail closed rather than updating an arbitrary source.
    """
    matches = db.execute(
        """SELECT s.id, s.name
             FROM sources s
             JOIN source_postgres_identities spi ON spi.source_id = s.id
            WHERE s.archived = 0
              AND spi.server_name = ?
              AND spi.database_name = ?
              AND spi.schema_name = ?
              AND spi.relation_name = ?
              AND spi.relation_kind = 'materialized_view'
            ORDER BY s.id""",
        (server, database, schema, relation),
    ).fetchall()
    return matches[0] if len(matches) == 1 else None


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
    server = postgres_server_identity(PGHOST, PGPORT)
    pg_conn = _get_pg_connection()

    if pg_conn is None:
        return {"status": "skipped", "reason": "No PostgreSQL credentials configured"}

    try:
        pg_cur = pg_conn.cursor()
        generation = uuid.uuid4().hex

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

        pg_cur.execute("SELECT current_setting('cron.timezone', true), current_setting('TimeZone', true)")
        timezone_row = pg_cur.fetchone() or (None, None)
        cron_timezone = str(timezone_row[0] or timezone_row[1] or FLOW_TIMEZONE)

        if not jobs:
            with get_db() as db:
                expire_schedule_evidence_generation(db, origin="pg_cron", generation=generation)
                reconcile_all_sources(db)
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

                # pg_cron's database column identifies the database where the
                # command runs.  Match all physical coordinates so duplicate
                # schema/relation names cannot borrow each other's schedule.
                database_name = str(database or "").strip()
                source = None
                if server and database_name:
                    source = _exact_mv_source(
                        db,
                        server=server,
                        database=database_name,
                        schema=schema,
                        relation=mv_name,
                    )

                if not source:
                    identity = (
                        f"{server or '(unconfigured server)'}/"
                        f"{database_name or '(unknown database)'}/{full_name}"
                    )
                    log_lines.append(f"CRON: {identity} - no unique exact source found")
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
                upsert_schedule_evidence(
                    db,
                    source_id=int(source["id"]),
                    origin="pg_cron",
                    external_id=str(jobid),
                    expression=str(schedule),
                    timezone_name=cron_timezone,
                    active=bool(active),
                    authoritative=True,
                    generation=generation,
                    observed_at=now,
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
                        f"CRON: {server}/{database_name}/{full_name} -> schedule={schedule}, "
                        f"last_run={run_time_str} ({run_status})"
                    )
                else:
                    log_lines.append(
                        f"CRON: {server}/{database_name}/{full_name} -> "
                        f"schedule={schedule}, no run history"
                    )

            # Expire only after the complete remote result has been processed;
            # a failed/partial scan leaves the last trusted evidence active.
            expire_schedule_evidence_generation(
                db, origin="pg_cron", generation=generation,
            )
            reconcile_all_sources(db)

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
