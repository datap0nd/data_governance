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

from app.config import FLOW_TIMEZONE, PGHOST, PGPORT, PGUSER, PGPASSWORD
from app.database import get_db
from app.asset_visibility import get_active_source_ids
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


def _permission_error(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) == "42501" or "permission denied" in str(exc).casefold()


def _schedule_context() -> dict:
    """Return safe local facts used to choose warning versus neutral severity."""
    with get_db() as db:
        active_source_ids = get_active_source_ids(db)
        mv_ids = [
            int(row["source_id"])
            for row in db.execute(
                """SELECT spi.source_id
                     FROM source_postgres_identities spi
                     JOIN sources s ON s.id=spi.source_id
                    WHERE spi.relation_kind='materialized_view'
                      AND COALESCE(s.archived, 0)=0"""
            ).fetchall()
            if int(row["source_id"]) in active_source_ids
        ]
        counts = reconcile_all_sources(db, source_ids=mv_ids) if mv_ids else {"unmapped": 0}
        prior = int(db.execute(
            """SELECT COUNT(*) AS count FROM source_schedule_evidence
                WHERE origin='pg_cron' AND active=1"""
        ).fetchone()["count"])
    return {
        "governed_mvs": len(mv_ids),
        "schedule_evidence_needed": int(counts.get("unmapped") or 0),
        "prior_pg_cron_evidence": prior,
    }


def _prior_evidence_external_ids() -> set[str]:
    with get_db() as db:
        return {
            str(row["external_id"])
            for row in db.execute(
                """SELECT external_id FROM source_schedule_evidence
                    WHERE origin='pg_cron' AND active=1"""
            ).fetchall()
        }


def _result(
    status: str,
    reason_code: str,
    operator_summary: str,
    *,
    remediation: list[str] | None = None,
    facts: dict | None = None,
    **details,
) -> dict:
    return {
        "status": status,
        "reason_code": reason_code,
        "message": operator_summary,
        "diagnostic": {
            "health_impact": (
                "error" if status == "failed" else
                "warning" if status == "completed_with_warnings" else "none"
            ),
            "reason_code": reason_code,
            "operator_summary": operator_summary,
            "remediation": remediation or [],
            "facts": facts or {},
        },
        **details,
    }


def _unavailable_result(reason_code: str, summary: str, remediation: list[str]) -> dict:
    context = _schedule_context()
    warning = context["schedule_evidence_needed"] > 0
    return _result(
        "completed_with_warnings" if warning else "skipped",
        reason_code,
        summary,
        remediation=remediation,
        facts=context,
        reason=summary,
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
        if not PGHOST or not PGUSER or not PGPASSWORD:
            return _unavailable_result(
                "postgres_credentials_not_configured",
                "PostgreSQL schedules were skipped because scanner credentials are not configured.",
                ["Configure PGHOST, PGUSER, and PGPASSWORD for the read-only scanner account."],
            )
        return _unavailable_result(
            "postgres_connection_failed",
            "PostgreSQL schedules could not connect with the configured scanner connection.",
            ["Verify the PostgreSQL host, port, database, credentials, and network access."],
        )

    try:
        pg_cur = pg_conn.cursor()
        generation = uuid.uuid4().hex

        # Check extension presence and catalog visibility separately. All
        # probes remain read-only and avoid conflating permission failures
        # with a genuinely absent pg_cron installation.
        try:
            pg_cur.execute(
                """SELECT
                       EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_cron'),
                       to_regclass('cron.job') IS NOT NULL,
                       has_schema_privilege(current_user, 'cron', 'USAGE'),
                       has_table_privilege(current_user, 'cron.job', 'SELECT'),
                       COALESCE((SELECT rolsuper OR rolbypassrls
                                   FROM pg_roles WHERE rolname=current_user), false)"""
            )
            capability = pg_cur.fetchone() or (False, False, False, False, False)
            installed, table_visible, schema_usage, table_select, global_visibility = map(bool, capability)
            if not installed:
                return _unavailable_result(
                    "pg_cron_not_installed",
                    "PostgreSQL schedules were skipped because pg_cron is not installed in this database.",
                    ["Install and enable pg_cron in the PostgreSQL database if schedule discovery is required."],
                )
            if not table_visible or not schema_usage or not table_select:
                return _unavailable_result(
                    "pg_cron_permission_denied",
                    "PostgreSQL schedules were skipped because the scanner cannot read cron.job.",
                    ["Grant the scanner account USAGE on schema cron and SELECT on cron.job."],
                )
        except Exception as exc:
            logger.warning("Could not inspect pg_cron capabilities: %s", exc)
            if _permission_error(exc):
                return _unavailable_result(
                    "pg_cron_permission_denied",
                    "PostgreSQL schedules were skipped because pg_cron catalog access was denied.",
                    ["Grant the scanner account USAGE on schema cron and SELECT on cron.job."],
                )
            return _unavailable_result(
                "pg_cron_job_query_failed",
                "PostgreSQL schedules could not verify the pg_cron catalog.",
                ["Verify pg_cron catalog visibility and rerun PostgreSQL schedules."],
            )

        # READ-ONLY: SELECT from cron.job
        try:
            pg_cur.execute(
                "SELECT jobid, schedule, command, database, username, active FROM cron.job ORDER BY jobid"
            )
            jobs = pg_cur.fetchall()
        except Exception as exc:
            logger.warning("Could not read cron.job: %s", exc)
            reason_code = "pg_cron_permission_denied" if _permission_error(exc) else "pg_cron_job_query_failed"
            return _unavailable_result(
                reason_code,
                "PostgreSQL schedules could not read the pg_cron job catalog.",
                ["Verify that the scanner account has USAGE on cron and SELECT on cron.job."],
            )

        pg_cur.execute("SELECT current_setting('cron.timezone', true), current_setting('TimeZone', true)")
        timezone_row = pg_cur.fetchone() or (None, None)
        cron_timezone = str(timezone_row[0] or timezone_row[1] or FLOW_TIMEZONE)

        prior_external_ids = _prior_evidence_external_ids()
        if not jobs:
            context = _schedule_context()
            if context["prior_pg_cron_evidence"] > 0:
                facts = {
                    **context,
                    "jobs_found": 0,
                    "prior_evidence_retained": context["prior_pg_cron_evidence"],
                }
                return _result(
                    "completed_with_warnings",
                    "pg_cron_no_visible_jobs",
                    "No pg_cron jobs were visible, so previous trusted schedule evidence was retained.",
                    remediation=[
                        "Confirm the scanner account can see all expected cron jobs before clearing prior schedule evidence."
                    ],
                    facts=facts,
                    jobs_found=0,
                    mv_jobs=0,
                    matched=0,
                    prior_evidence_retained=context["prior_pg_cron_evidence"],
                )
            with get_db() as db:
                expire_schedule_evidence_generation(db, origin="pg_cron", generation=generation)
                reconciliation = reconcile_all_sources(db)
            warning = context["schedule_evidence_needed"] > 0
            return _result(
                "completed_with_warnings" if warning else "completed",
                "pg_cron_no_visible_jobs",
                "No pg_cron jobs were visible."
                + (" Governed materialized views still need schedule evidence." if warning else ""),
                remediation=["Create visible pg_cron refresh jobs for governed materialized views if schedules are expected."],
                facts={**context, "jobs_found": 0},
                jobs_found=0,
                mv_jobs=0,
                matched=0,
                reconciliation=reconciliation,
            )

        # READ-ONLY: SELECT from cron.job_run_details (last run per job)
        run_details = {}
        run_history_available = True
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
            run_history_available = False

        matched = 0
        log_lines = []
        visible_external_ids = {str(job[0]) for job in jobs}
        missing_prior_ids = prior_external_ids - visible_external_ids
        snapshot_authoritative = bool(global_visibility or not missing_prior_ids)

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
            if snapshot_authoritative:
                expire_schedule_evidence_generation(
                    db, origin="pg_cron", generation=generation,
                )
            reconciliation = reconcile_all_sources(db)

        facts = {
            "jobs_found": len(jobs),
            "mv_jobs": sum(1 for j in jobs if _parse_mv_from_command(j[2])),
            "matched": matched,
        }
        if not snapshot_authoritative:
            facts["prior_evidence_retained"] = len(missing_prior_ids)
            summary = _result(
                "completed_with_warnings",
                "pg_cron_snapshot_incomplete",
                "Visible pg_cron schedules were refreshed, but previously trusted jobs were not visible and were retained.",
                remediation=[
                    "Confirm that the scanner role can see jobs owned by every expected pg_cron user."
                ],
                facts=facts,
                **facts,
                reconciliation=reconciliation,
                run_history_available=run_history_available,
                log="\n".join(log_lines),
            )
        elif run_history_available:
            summary = _result(
                "completed",
                "pg_cron_scan_completed",
                f"PostgreSQL schedules refreshed from {len(jobs)} visible pg_cron job(s).",
                facts=facts,
                **facts,
                reconciliation=reconciliation,
                log="\n".join(log_lines) if log_lines else "No MV cron jobs found.",
            )
        else:
            summary = _result(
                "completed_with_warnings",
                "pg_cron_run_history_unavailable",
                "PostgreSQL schedules were refreshed, but pg_cron run history was unavailable.",
                remediation=[
                    "Grant SELECT on cron.job_run_details to include last-run history; schedules were still retained."
                ],
                facts=facts,
                **facts,
                reconciliation=reconciliation,
                run_history_available=False,
                log="\n".join(log_lines) if log_lines else "No MV cron jobs found.",
            )
        logger.info("pg_cron scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("pg_cron scan failed: %s", e)
        reason_code = "pg_cron_permission_denied" if _permission_error(e) else "pg_cron_job_query_failed"
        return _result(
            "failed",
            reason_code,
            "PostgreSQL schedule discovery failed while reading the pg_cron catalog.",
            remediation=["Review the server log, verify pg_cron read access, and rerun the module."],
        )

    finally:
        pg_conn.close()
