"""
PostgreSQL materialized view dependency scanner.

Uses pg_depend + pg_rewrite to find real table dependencies for each
materialized view, registers upstream tables as sources, and stores
dependency edges in source_dependencies.

READ-ONLY: Only SELECT queries are used against PostgreSQL.
"""

import logging
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.database import get_db
from app.asset_visibility import get_active_source_ids
from app.query_history import (
    MATERIALIZED_VIEW_KIND,
    link_versions_to_action,
    mv_artifact_key,
    observe_query,
)
from app.scanner.prober import _get_pg_connection
from app.scanner.control import assert_not_cancelled
from app.scanner.jobs import heartbeat as scanner_job_heartbeat
from app.config import PGHOST, PGDATABASE, PGPASSWORD, UPLOAD_PGHOST
from app.source_identity import (
    exact_identity_rows,
    normalize_server,
    reconcile_flow_target,
    upsert_postgres_identity,
)

logger = logging.getLogger(__name__)


class PostgresIdentityResolutionError(RuntimeError):
    """Raised when a physical PostgreSQL relation cannot be mapped safely."""


def _one_exact_identity(
    db, *, server: str, database: str, schema: str, relation: str
):
    matches = exact_identity_rows(
        db,
        server=server,
        database=database,
        schema=schema,
        relation=relation,
    )
    if len(matches) > 1:
        source_ids = ", ".join(str(int(row["source_id"])) for row in matches)
        raise PostgresIdentityResolutionError(
            f"Ambiguous PostgreSQL identity for {database}.{schema}.{relation} "
            f"on {normalize_server(server) or '<unknown server>'}: sources {source_ids}"
        )
    return matches[0] if matches else None


def _claim_identity(
    db,
    *,
    source_id: int,
    server: str,
    database: str,
    schema: str,
    relation: str,
    relation_kind: str,
    verified_at: str,
) -> None:
    result = upsert_postgres_identity(
        db,
        source_id=source_id,
        server=server,
        database=database,
        schema=schema,
        relation=relation,
        relation_kind=relation_kind,
        verified_at=verified_at,
    )
    if result and result.get("status") == "conflict":
        raise PostgresIdentityResolutionError(
            f"Source {source_id} already belongs to a different PostgreSQL relation"
        )


def _new_source_name(
    db, *, server: str, database: str, schema: str, relation: str
) -> str:
    """Choose a readable unique label while the structured identity stays authoritative."""
    full_name = f"{schema}.{relation}"
    if not db.execute("SELECT 1 FROM sources WHERE name=?", (full_name,)).fetchone():
        return full_name

    host = normalize_server(server) or "unknown-host"
    qualified = f"{full_name} [{database}@{host}]"
    if not db.execute("SELECT 1 FROM sources WHERE name=?", (qualified,)).fetchone():
        return qualified

    suffix = 2
    while db.execute(
        "SELECT 1 FROM sources WHERE name=?", (f"{qualified} #{suffix}",)
    ).fetchone():
        suffix += 1
    return f"{qualified} #{suffix}"


def _find_or_create_source(
    db,
    *,
    server: str,
    database: str,
    schema: str,
    table: str,
    now: str,
    relation_kind: str = "table",
) -> int:
    """Resolve one exact physical relation or create a newly identified source.

    Display names and connection-info suffixes are deliberately ignored. They
    cannot distinguish identical schema/table names in different databases.
    """
    exact = _one_exact_identity(
        db,
        server=server,
        database=database,
        schema=schema,
        relation=table,
    )
    if exact is not None:
        source_id = int(exact["source_id"])
        _claim_identity(
            db,
            source_id=source_id,
            server=server,
            database=database,
            schema=schema,
            relation=table,
            relation_kind=relation_kind,
            verified_at=now,
        )
        return source_id

    full_name = f"{schema}.{table}"
    source_name = _new_source_name(
        db,
        server=server,
        database=database,
        schema=schema,
        relation=table,
    )
    connection_info = f"{normalize_server(server)}/{database}/{full_name}"
    cursor = db.execute(
        """INSERT INTO sources (name, type, connection_info, discovered_by, created_at, updated_at)
           VALUES (?, 'postgresql', ?, 'pg_deps', ?, ?)""",
        (source_name, connection_info, now, now),
    )
    source_id = int(cursor.lastrowid)
    db.execute(
        "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
        (source_id, now, "Discovered as MV dependency"),
    )
    _claim_identity(
        db,
        source_id=source_id,
        server=server,
        database=database,
        schema=schema,
        relation=table,
        relation_kind=relation_kind,
        verified_at=now,
    )
    return source_id


@dataclass(frozen=True)
class _DatabaseCatalog:
    dependency_rows: tuple[tuple, ...]
    definitions: dict[tuple[str, str], str]
    definition_error: str | None = None
    parent_kinds: dict[tuple[str, str], str] = field(default_factory=dict)


_DEPENDENCY_SQL = """
    SELECT DISTINCT
        ns_mv.nspname  AS mv_schema,
        c_mv.relname   AS mv_name,
        c_mv.relkind   AS parent_kind,
        ns_dep.nspname AS dep_schema,
        c_dep.relname  AS dep_name,
        c_dep.relkind  AS dep_kind
    FROM pg_depend d
    JOIN pg_rewrite rw  ON rw.oid = d.objid
    JOIN pg_class c_mv  ON c_mv.oid = rw.ev_class
    JOIN pg_namespace ns_mv ON ns_mv.oid = c_mv.relnamespace
    JOIN pg_class c_dep ON c_dep.oid = d.refobjid
    JOIN pg_namespace ns_dep ON ns_dep.oid = c_dep.relnamespace
    WHERE c_mv.relkind IN ('m', 'v')
      AND d.deptype = 'n'
      AND d.classid = 'pg_rewrite'::regclass
      AND c_dep.relkind IN ('r', 'p', 'm', 'v')
      AND c_dep.oid != c_mv.oid
      AND ns_dep.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY ns_mv.nspname, c_mv.relname, ns_dep.nspname, c_dep.relname
"""


_DEFINITION_SQL = """
    SELECT schemaname, matviewname, definition
    FROM pg_matviews
    ORDER BY schemaname, matviewname
"""


def _redact_error(value: object) -> str:
    """Return a bounded diagnostic without credentials or URL userinfo."""
    message = str(value or "Unknown PostgreSQL error")
    if PGPASSWORD:
        message = message.replace(PGPASSWORD, "[redacted]")
    message = re.sub(
        r"(?i)\b(password|passwd|pwd)\s*=\s*[^\s;]+",
        r"\1=[redacted]",
        message,
    )
    message = re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1[redacted]@", message)
    return message[:1000]


def _required_databases() -> tuple[list[str], dict[str, list[str]], set[str]]:
    """Return exact database names and why each one needs dependency discovery."""
    origins: dict[str, set[str]] = {}

    def add(value, origin: str) -> None:
        database = (value or "").strip()
        if database:
            origins.setdefault(database, set()).add(origin)

    add(PGDATABASE, "configured")
    with get_db() as db:
        active_source_ids = get_active_source_ids(db)
        identity_rows = db.execute(
            """SELECT spi.source_id, spi.database_name
               FROM source_postgres_identities spi
               JOIN sources s ON s.id=spi.source_id
               WHERE COALESCE(s.archived, 0)=0 AND spi.server_name=?
                 AND NULLIF(TRIM(spi.database_name), '') IS NOT NULL""",
            (normalize_server(PGHOST),),
        ).fetchall()
        for row in identity_rows:
            if int(row["source_id"]) in active_source_ids:
                add(row["database_name"], "identity")

        flow_rows = db.execute(
            """SELECT DISTINCT sql_database
               FROM flows
               WHERE sql_handoff_enabled=1
                 AND NULLIF(TRIM(sql_database), '') IS NOT NULL"""
        ).fetchall()
        for row in flow_rows:
            add(row["sql_database"], "flow")

    databases = sorted(origins, key=lambda value: (value.casefold(), value))
    flow_host_mismatches = set()
    if normalize_server(UPLOAD_PGHOST) != normalize_server(PGHOST):
        flow_host_mismatches = {
            database for database, values in origins.items() if "flow" in values
        }
    origin_order = {"configured": 0, "identity": 1, "flow": 2}
    serialized = {
        database: sorted(origins[database], key=lambda value: origin_order[value])
        for database in databases
    }
    return databases, serialized, flow_host_mismatches


def _connection_for_database(database: str):
    """Call the database-aware helper while tolerating legacy no-arg test doubles."""
    try:
        parameters = inspect.signature(_get_pg_connection).parameters.values()
    except (TypeError, ValueError):
        parameters = ()

    accepts_keyword = any(
        parameter.name == "database" or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_keyword:
        return _get_pg_connection(database=database)

    accepts_positional = any(
        parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for parameter in parameters
    )
    if accepts_positional:
        return _get_pg_connection(database)
    if database != (PGDATABASE or "").strip():
        raise RuntimeError(
            "The PostgreSQL connection helper cannot select database "
            f"{database!r}."
        )
    return _get_pg_connection()


def _fetch_database_catalog(database: str) -> _DatabaseCatalog:
    """Fetch a complete dependency snapshot before opening a SQLite write batch."""
    pg_conn = _connection_for_database(database)
    if pg_conn is None:
        raise RuntimeError(
            f"PostgreSQL connection unavailable for database {database!r}."
        )

    try:
        pg_cur = pg_conn.cursor()
        pg_cur.execute(_DEPENDENCY_SQL)
        dependency_rows = []
        parent_kinds: dict[tuple[str, str], str] = {}
        for row in pg_cur.fetchall():
            if len(row) == 6:
                mv_schema, mv_name, parent_kind, dep_schema, dep_name, dep_kind = row
                parent_kinds[(mv_schema, mv_name)] = str(parent_kind)
                dependency_rows.append(
                    (mv_schema, mv_name, dep_schema, dep_name, dep_kind)
                )
                continue
            if len(row) != 5:
                raise ValueError(
                    "PostgreSQL adapter returned an unexpected dependency row"
                )
            # Compatibility for lightweight test/legacy adapters. Historical
            # rows only described materialized-view parents.
            parent_kinds[(row[0], row[1])] = "m"
            dependency_rows.append(tuple(row))

        definitions: dict[tuple[str, str], str] = {}
        definition_error = None
        try:
            pg_cur.execute(_DEFINITION_SQL)
            for row in pg_cur.fetchall():
                if len(row) != 3:
                    raise ValueError(
                        "PostgreSQL adapter returned an unexpected pg_matviews row"
                    )
                schema, name, definition = row
                definitions[(schema, name)] = definition or ""
        except Exception as exc:
            definition_error = _redact_error(exc)
            logger.warning(
                "MV definition capture skipped for database %s: %s",
                database,
                definition_error,
            )

        return _DatabaseCatalog(
            dependency_rows=tuple(dependency_rows),
            definitions=definitions,
            definition_error=definition_error,
            parent_kinds=parent_kinds,
        )
    finally:
        pg_conn.close()


def scan_pg_dependencies(
    scan_run_id: int | None = None,
    *,
    operation_id: int | None = None,
    cancel_generation: int | None = None,
) -> dict:
    """Refresh dependency lineage independently for every required database."""
    assert_not_cancelled(cancel_generation, "PostgreSQL lineage scan")
    scanner_job_heartbeat(
        operation_id,
        current_step="Discovering PostgreSQL databases",
        message="Resolving databases required by reports and Flow SQL targets.",
    )
    now = datetime.now(timezone.utc).isoformat()
    databases, origins, flow_host_mismatches = _required_databases()
    if not databases:
        return {
            "status": "not_requested",
            "required_databases": [],
            "database_origins": {},
            "flow_server_mismatch_databases": [],
            "databases": {},
            "mvs_found": 0,
            "deps_created": 0,
            "sources_created": 0,
            "changed_queries": 0,
            "definition_status": "not_requested",
            "log": "No PostgreSQL databases require dependency discovery.",
            "query_change_log": "",
        }

    database_results: dict[str, dict] = {}
    total_databases = len(databases)
    for database_index, database in enumerate(databases, start=1):
        assert_not_cancelled(cancel_generation, "PostgreSQL lineage scan")
        scanner_job_heartbeat(
            operation_id,
            current_step="Reading PostgreSQL catalog",
            message=f"Scanning database {database}.",
            progress_current=database_index - 1,
            progress_total=total_databases,
        )
        # A Flow's physical target is on UPLOAD_PGHOST. When that differs from
        # the read-only catalog host, a Flow-only requirement cannot safely be
        # satisfied by scanning a same-named database on PGHOST.
        if database in flow_host_mismatches and origins[database] == ["flow"]:
            database_results[database] = {
                "status": "failed",
                "stage": "configuration",
                "reason_code": "server_mismatch",
                "error": (
                    "Flow target server does not match the configured PostgreSQL "
                    "catalog server. Prior lineage was retained."
                ),
                "mvs_found": 0,
                "deps_created": 0,
                "sources_created": 0,
                "changed_queries": 0,
                "definition_status": "not_requested",
                "log": (
                    "Catalog scan was not attempted because the Flow target uses "
                    "a different PostgreSQL server."
                ),
                "query_change_log": "",
            }
            scanner_job_heartbeat(
                operation_id,
                current_step="Reading PostgreSQL catalog",
                message=f"Skipped database {database}: catalog server mismatch.",
                progress_current=database_index,
                progress_total=total_databases,
            )
            continue

        try:
            catalog = _fetch_database_catalog(database)
        except Exception as exc:
            error = _redact_error(exc)
            logger.warning(
                "PostgreSQL dependency catalog fetch failed for %s: %s",
                database,
                error,
            )
            database_results[database] = {
                "status": "failed",
                "stage": "fetch",
                "error": error,
                "mvs_found": 0,
                "deps_created": 0,
                "sources_created": 0,
                "changed_queries": 0,
                "definition_status": "not_requested",
                "log": "Catalog fetch failed; prior lineage was retained.",
                "query_change_log": "",
            }
            scanner_job_heartbeat(
                operation_id,
                current_step="Reading PostgreSQL catalog",
                message=f"Database {database} could not be read; prior lineage was retained.",
                progress_current=database_index,
                progress_total=total_databases,
            )
            continue

        assert_not_cancelled(cancel_generation, "PostgreSQL lineage scan")
        scanner_job_heartbeat(
            operation_id,
            current_step="Applying lineage snapshot",
            message=f"Reconciling materialized views and Flow targets in {database}.",
            progress_current=database_index - 1,
            progress_total=total_databases,
        )
        try:
            database_results[database] = _apply_database_catalog(
                database,
                catalog,
                scan_run_id=scan_run_id,
                now=now,
            )
            if database in flow_host_mismatches:
                database_results[database]["status"] = "completed_with_warnings"
                database_results[database]["reason_code"] = "server_mismatch"
                database_results[database]["warning"] = (
                    "The database catalog was refreshed on PGHOST, but Flow "
                    "targets use a different UPLOAD_PGHOST and were not verified "
                    "by this scan."
                )
        except Exception as exc:
            error = _redact_error(exc)
            logger.warning(
                "PostgreSQL dependency batch failed for %s: %s", database, error
            )
            database_results[database] = {
                "status": "failed",
                "stage": "apply",
                "error": error,
                "mvs_found": 0,
                "deps_created": 0,
                "sources_created": 0,
                "changed_queries": 0,
                "definition_status": (
                    "skipped" if catalog.definition_error else "completed"
                ),
                "log": "Catalog apply failed; prior lineage was retained.",
                "query_change_log": "",
            }
            if catalog.definition_error:
                database_results[database]["definition_error"] = _redact_error(
                    catalog.definition_error
                )
        scanner_job_heartbeat(
            operation_id,
            current_step="Applying lineage snapshot",
            message=f"Finished database {database}.",
            progress_current=database_index,
            progress_total=total_databases,
        )

    successful = [
        result
        for result in database_results.values()
        if result["status"] in ("completed", "completed_with_warnings")
    ]
    failed = [
        result for result in database_results.values() if result["status"] == "failed"
    ]
    warned = [
        result
        for result in database_results.values()
        if result["status"] == "completed_with_warnings"
    ]
    if successful and (failed or warned):
        status = "completed_with_warnings"
    elif successful:
        status = "completed"
    else:
        status = "failed"

    def total(key: str) -> int:
        return sum(int(result.get(key) or 0) for result in successful)

    log_sections = []
    query_sections = []
    for database in databases:
        result = database_results[database]
        log_sections.append(f"[{database}] {result.get('log') or result['status']}")
        if result.get("query_change_log"):
            query_sections.append(f"[{database}] {result['query_change_log']}")

    definition_status = (
        "skipped"
        if any(result.get("definition_status") == "skipped" for result in successful)
        else ("completed" if successful else "not_requested")
    )
    summary = {
        "status": status,
        "required_databases": databases,
        "database_origins": origins,
        "flow_server_mismatch_databases": sorted(
            flow_host_mismatches, key=lambda value: (value.casefold(), value)
        ),
        "databases": database_results,
        "mvs_found": total("mvs_found"),
        "deps_created": total("deps_created"),
        "sources_created": total("sources_created"),
        "changed_queries": total("changed_queries"),
        "definition_status": definition_status,
        "log": "\n".join(log_sections),
        "query_change_log": "\n".join(query_sections),
    }
    if not successful:
        errors = [result.get("error", "unknown failure") for result in failed]
        combined = errors[0] if len(errors) == 1 else "; ".join(errors)
        summary["error"] = _redact_error(combined)
    scanner_job_heartbeat(
        operation_id,
        current_step="Finalizing PostgreSQL lineage",
        message=f"Processed {total_databases} database(s).",
        progress_current=total_databases,
        progress_total=total_databases,
    )
    logger.info("PG dependency scan completed: %s", summary)
    return summary


def _reconcile_database_flows(db, database: str) -> dict:
    """Reconcile only Flow targets owned by the committed database batch."""
    counts: dict[str, int] = {}
    flow_ids = db.execute(
        """SELECT id FROM flows
           WHERE sql_handoff_enabled=1 AND sql_database=?
           ORDER BY id""",
        (database,),
    ).fetchall()
    for row in flow_ids:
        result = reconcile_flow_target(db, int(row["id"]), server=UPLOAD_PGHOST)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _delete_database_edges(db, *, server: str, database: str) -> None:
    db.execute(
        """DELETE FROM source_dependencies
           WHERE discovered_by='pg_matviews'
             AND source_id IN (
                 SELECT source_id FROM source_postgres_identities
                 WHERE server_name=? AND database_name=?
             )""",
        (normalize_server(server), database),
    )


def _cleanup_database_orphans(db, *, server: str, database: str) -> int:
    """Delete unreferenced scanner sources only inside one physical database."""
    candidates = db.execute(
        """SELECT s.id
           FROM sources s
           JOIN source_postgres_identities spi ON spi.source_id=s.id
           WHERE s.discovered_by IN ('pg_deps', 'pg_matviews')
             AND spi.server_name=? AND spi.database_name=?
             AND NOT EXISTS (
                 SELECT 1 FROM source_dependencies sd
                 WHERE sd.source_id=s.id OR sd.depends_on_id=s.id
             )
             AND NOT EXISTS (SELECT 1 FROM report_tables rt WHERE rt.source_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM script_tables st WHERE st.source_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM query_versions qv WHERE qv.source_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM flows f WHERE f.sql_target_source_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM checks c WHERE c.source_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.source_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.source_id=s.id)
             AND NOT EXISTS (
                 SELECT 1 FROM task_links tl
                 WHERE tl.entity_type='source' AND tl.entity_id=s.id
             )
             AND NOT EXISTS (
                 SELECT 1 FROM power_automate_flows paf WHERE paf.output_source_id=s.id
             )
           ORDER BY s.id""",
        (normalize_server(server), database),
    ).fetchall()
    source_ids = [int(row["id"]) for row in candidates]
    for source_id in source_ids:
        db.execute("DELETE FROM source_probes WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM sources WHERE id=?", (source_id,))
    return len(source_ids)


def _apply_database_catalog(
    database: str,
    catalog: _DatabaseCatalog,
    *,
    scan_run_id: int | None,
    now: str,
) -> dict:
    """Atomically replace one database's known materialized-view lineage."""
    mv_dependencies: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for mv_schema, mv_name, dep_schema, dep_name, dep_kind in catalog.dependency_rows:
        mv_dependencies.setdefault((mv_schema, mv_name), []).append(
            (dep_schema, dep_name, dep_kind)
        )

    mvs_found = 0
    deps_created = 0
    changed_queries = 0
    log_lines: list[str] = []
    query_change_lines: list[str] = []

    with get_db() as db:
        _delete_database_edges(db, server=PGHOST, database=database)

        pending_relations = set(mv_dependencies)
        while pending_relations:
            progressed = False
            for mv_schema, mv_name in sorted(pending_relations):
                refs = mv_dependencies[(mv_schema, mv_name)]
                parent_kind_code = catalog.parent_kinds.get((mv_schema, mv_name), "m")
                parent_kind = "view" if parent_kind_code == "v" else "materialized_view"
                mv_source_id = _find_or_create_source(
                    db,
                    server=PGHOST,
                    database=database,
                    schema=mv_schema,
                    table=mv_name,
                    now=now,
                    relation_kind=parent_kind,
                )

                pending_relations.remove((mv_schema, mv_name))
                progressed = True
                if parent_kind_code == "m":
                    mvs_found += 1

                for dep_schema, dep_table, dep_kind in refs:
                    kind = {
                        "m": "materialized_view",
                        "v": "view",
                        "r": "table",
                        "p": "table",
                    }.get(
                        dep_kind, "table"
                    )
                    dep_source_id = _find_or_create_source(
                        db,
                        server=PGHOST,
                        database=database,
                        schema=dep_schema,
                        table=dep_table,
                        now=now,
                        relation_kind=kind,
                    )
                    if dep_source_id == mv_source_id:
                        continue
                    cursor = db.execute(
                        """INSERT OR IGNORE INTO source_dependencies
                               (source_id, depends_on_id, discovered_by, created_at)
                           VALUES (?, ?, 'pg_matviews', ?)""",
                        (mv_source_id, dep_source_id, now),
                    )
                    if cursor.rowcount:
                        deps_created += 1

                full_mv_name = f"{mv_schema}.{mv_name}"
                ref_names = [f"{schema}.{name}" for schema, name, _kind in refs]
                label = "VIEW" if parent_kind_code == "v" else "MV"
                log_lines.append(f"{label}: {full_mv_name} -> {', '.join(ref_names)}")

            if not progressed:
                break

        active_source_ids = get_active_source_ids(db)
        for (mv_schema, mv_name), definition in sorted(catalog.definitions.items()):
            mv_identity = _one_exact_identity(
                db,
                server=PGHOST,
                database=database,
                schema=mv_schema,
                relation=mv_name,
            )
            if mv_identity is None:
                continue
            source_id = int(mv_identity["source_id"])
            if source_id not in active_source_ids:
                continue

            mv_source = db.execute(
                "SELECT id, name, owner FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if not mv_source:
                raise PostgresIdentityResolutionError(
                    f"PostgreSQL identity references missing source {source_id}"
                )
            full_mv_name = f"{mv_schema}.{mv_name}"
            observation = observe_query(
                db,
                artifact_kind=MATERIALIZED_VIEW_KIND,
                artifact_key=mv_artifact_key(source_id),
                report_id=None,
                source_id=source_id,
                artifact_name=full_mv_name,
                language="sql",
                query_text=definition,
                scan_run_id=scan_run_id,
                detected_at=now,
            )
            if not observation.changed:
                continue

            changed_queries += 1
            fingerprint = f"changed_query:mv:{source_id}:{observation.query_hash[:16]}"
            owner = mv_source["owner"]
            if not owner:
                owner_row = db.execute(
                    """WITH RECURSIVE downstream_sources(id) AS (
                           SELECT ?
                           UNION
                           SELECT sd.source_id
                           FROM source_dependencies sd
                           JOIN downstream_sources ds ON sd.depends_on_id=ds.id
                       )
                       SELECT r.owner FROM report_tables rt
                       JOIN reports r ON r.id=rt.report_id
                       JOIN downstream_sources ds ON ds.id=rt.source_id
                       WHERE COALESCE(r.archived, 0)=0
                         AND NULLIF(TRIM(r.owner), '') IS NOT NULL
                       ORDER BY r.id LIMIT 1""",
                    (source_id,),
                ).fetchone()
                owner = owner_row["owner"] if owner_row else None

            db.execute(
                """UPDATE actions
                   SET status='resolved', resolved_at=?, updated_at=?,
                       notes=COALESCE(notes, '') ||
                             ' [auto-resolved: superseded query change]'
                   WHERE source_id=? AND report_id IS NULL AND type='changed_query'
                     AND fingerprint!=?
                     AND status IN ('open','acknowledged','investigating')""",
                (now, now, source_id, fingerprint),
            )
            prior = db.execute(
                """SELECT id FROM actions
                   WHERE fingerprint=? AND status!='resolved'
                   ORDER BY id DESC LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            notes = f"Materialized view definition changed for {full_mv_name}."
            if prior:
                action_id = int(prior["id"])
                db.execute(
                    "UPDATE actions SET notes=?, assigned_to=?, updated_at=? WHERE id=?",
                    (notes, owner, now, action_id),
                )
            else:
                cursor = db.execute(
                    """INSERT INTO actions
                           (source_id, type, status, assigned_to, notes,
                            fingerprint, created_at, updated_at)
                       VALUES (?, 'changed_query', 'open', ?, ?, ?, ?, ?)""",
                    (source_id, owner, notes, fingerprint, now, now),
                )
                action_id = int(cursor.lastrowid)
            link_versions_to_action(db, [observation.version_id], action_id)
            query_change_lines.append(f"CHANGED MV QUERY: {full_mv_name}")

        _cleanup_database_orphans(db, server=PGHOST, database=database)
        flow_reconciliation = _reconcile_database_flows(db, database)
        sources_created = db.execute(
            """SELECT COUNT(*)
               FROM sources s
               JOIN source_postgres_identities spi ON spi.source_id=s.id
               WHERE s.discovered_by='pg_deps' AND s.created_at=?
                 AND spi.server_name=? AND spi.database_name=?""",
            (now, normalize_server(PGHOST), database),
        ).fetchone()[0]

    result = {
        "status": (
            "completed_with_warnings" if catalog.definition_error else "completed"
        ),
        "mvs_found": mvs_found,
        "deps_created": deps_created,
        "sources_created": int(sources_created),
        "changed_queries": changed_queries,
        "definition_status": "skipped" if catalog.definition_error else "completed",
        "log": "\n".join(log_lines) if log_lines else "No MV dependencies found.",
        "query_change_log": "\n".join(query_change_lines),
        "flow_reconciliation": flow_reconciliation,
    }
    if catalog.definition_error:
        result["definition_error"] = _redact_error(catalog.definition_error)
    return result
