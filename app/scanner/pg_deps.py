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
from app.scanner.prober import _get_pg_connection
from app.scanner.control import assert_not_cancelled
from app.scanner.jobs import heartbeat as scanner_job_heartbeat
from app.config import (
    PGHOST,
    PGPORT,
    PGDATABASE,
    PGPASSWORD,
    PG_SCAN_STATEMENT_TIMEOUT_SECONDS,
    UPLOAD_PGDATABASE,
    UPLOAD_PGHOST,
    UPLOAD_PGPASSWORD,
    UPLOAD_PGPORT,
    UPLOAD_PGUSER,
)
from app.scanner.report_source_identities import (
    complete_report_postgres_identity_reconciliation,
    finalize_report_postgres_identity_relinks,
    pending_report_postgres_identity_target_source_ids,
    reconcile_all_report_postgres_identities,
    reconcile_report_postgres_identities,
)
from app.source_identity import (
    exact_identity_rows,
    normalize_server,
    unique_postgres_source_name,
    postgres_server_identity,
    reconcile_flow_target,
    upsert_postgres_identity,
)

logger = logging.getLogger(__name__)


def _primary_server_identity() -> str:
    return postgres_server_identity(PGHOST, PGPORT)


def _flow_server_identity() -> str:
    return postgres_server_identity(UPLOAD_PGHOST, UPLOAD_PGPORT)


def _catalog_endpoints_match() -> bool:
    return bool(_primary_server_identity()) and (
        _primary_server_identity() == _flow_server_identity()
    )


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
    """Choose a readable unique label while structured identity stays authoritative."""
    return unique_postgres_source_name(
        db,
        server=server,
        database=database,
        schema=schema,
        relation=relation,
    )


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
    relations: dict[tuple[str, str], str] = field(default_factory=dict)
    requested_relations: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _CatalogScanTarget:
    database: str
    server: str
    credential_profile: str
    origins: tuple[str, ...]
    reconcile_flow_targets: bool


def _catalog_scan_targets(
    databases: list[str],
    origins: dict[str, list[str]],
) -> list[_CatalogScanTarget]:
    """Expand database names into exact physical PostgreSQL endpoints.

    A report catalog and a Flow target may use the same database name on two
    different servers or ports. They are separate scan targets; collapsing
    them by database name is precisely what hid valid Flow lineage.
    """
    targets: list[_CatalogScanTarget] = []
    same_endpoint = _catalog_endpoints_match()
    for database in databases:
        database_origins = tuple(origins[database])
        has_flow = "flow" in database_origins
        has_primary = any(origin != "flow" for origin in database_origins)
        if same_endpoint:
            targets.append(
                _CatalogScanTarget(
                    database=database,
                    server=_primary_server_identity(),
                    credential_profile="read_only",
                    origins=database_origins,
                    reconcile_flow_targets=has_flow,
                )
            )
            continue
        if has_primary:
            targets.append(
                _CatalogScanTarget(
                    database=database,
                    server=_primary_server_identity(),
                    credential_profile="read_only",
                    origins=tuple(
                        origin for origin in database_origins if origin != "flow"
                    ),
                    reconcile_flow_targets=False,
                )
            )
        if has_flow:
            targets.append(
                _CatalogScanTarget(
                    database=database,
                    server=_flow_server_identity(),
                    credential_profile="flow_target",
                    origins=("flow",),
                    reconcile_flow_targets=True,
                )
            )
    return targets


def _catalog_result_keys(targets: list[_CatalogScanTarget]) -> list[str]:
    """Return readable, collision-free keys for endpoint result objects.

    PostgreSQL database names may legally contain our presentation suffixes,
    so uniqueness cannot be inferred from text such as ``[Flow target]``.
    Allocate keys as one ordered batch and disambiguate any collision before
    results are written; this prevents a later endpoint from hiding a failure.
    """
    database_counts: dict[str, int] = {}
    for target in targets:
        database_counts[target.database] = database_counts.get(target.database, 0) + 1

    used: set[str] = set()
    keys: list[str] = []
    for target in targets:
        if database_counts[target.database] == 1 or target.credential_profile == "read_only":
            base = target.database
        else:
            base = f"{target.database} [Flow target]"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base} [{suffix}]"
            suffix += 1
        used.add(candidate)
        keys.append(candidate)
    return keys


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
      AND d.refclassid = 'pg_class'::regclass
      AND c_dep.relkind IN ('r', 'p', 'm', 'v', 'f')
      AND c_dep.oid != c_mv.oid
      AND ns_dep.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY ns_mv.nspname, c_mv.relname, ns_dep.nspname, c_dep.relname
"""


_DEFINITION_SQL = """
    SELECT schemaname, matviewname, definition
    FROM pg_matviews
    ORDER BY schemaname, matviewname
"""


def _referenced_relations(
    *,
    server: str,
    database: str,
    pending_source_ids: tuple[int, ...] | list[int] | set[int] | frozenset[int] = (),
) -> tuple[tuple[str, str], ...]:
    """Return only locally referenced exact relations for one endpoint."""
    with get_db() as db:
        active_source_ids = get_active_source_ids(db)
        active_source_ids.update(int(source_id) for source_id in pending_source_ids)
        active_source_ids.update(
            int(row["sql_target_source_id"])
            for row in db.execute(
                """SELECT sql_target_source_id FROM flows
                     WHERE sql_handoff_enabled=1 AND sql_target_source_id IS NOT NULL"""
            ).fetchall()
        )
        if not active_source_ids:
            return ()
        placeholders = ",".join("?" for _ in active_source_ids)
        rows = db.execute(
            f"""SELECT schema_name, relation_name
                  FROM source_postgres_identities
                 WHERE server_name=? AND database_name=?
                   AND source_id IN ({placeholders})
                 ORDER BY schema_name, relation_name""",
            (normalize_server(server), database, *sorted(active_source_ids)),
        ).fetchall()
    return tuple((str(row["schema_name"]), str(row["relation_name"])) for row in rows)


def _mark_catalog_verification_failed(*, server: str, database: str) -> None:
    """Retain prior links while making the failed verification explicit."""
    with get_db() as db:
        db.execute(
            """UPDATE report_tables
                  SET source_resolution_status='verification_failed',
                      source_resolution_reason='catalog_not_completed'
                WHERE source_id IN (
                    SELECT source_id FROM source_postgres_identities
                     WHERE server_name=? AND database_name=?
                )""",
            (normalize_server(server), database),
        )


def _redact_error(value: object) -> str:
    """Return a bounded diagnostic without credentials or URL userinfo."""
    message = str(value or "Unknown PostgreSQL error")
    if PGPASSWORD:
        message = message.replace(PGPASSWORD, "[redacted]")
    if UPLOAD_PGPASSWORD:
        message = message.replace(UPLOAD_PGPASSWORD, "[redacted]")
    message = re.sub(
        r"(?i)\b(password|passwd|pwd)\s*=\s*[^\s;]+",
        r"\1=[redacted]",
        message,
    )
    message = re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1[redacted]@", message)
    return message[:1000]


def _required_databases(
    report_catalog_targets: tuple[dict, ...] | list[dict] = (),
) -> tuple[list[str], dict[str, list[str]], list[dict]]:
    """Return scan databases, their origins, and exact unconfigured endpoints."""
    origins: dict[str, set[str]] = {}

    def add(value, origin: str) -> None:
        database = (value or "").strip()
        if database:
            origins.setdefault(database, set()).add(origin)

    primary_server = _primary_server_identity()
    flow_server = _flow_server_identity()
    unconfigured_targets: dict[tuple[str, str], dict] = {}

    def add_endpoint(server_value, database_value) -> None:
        server = normalize_server(server_value)
        database = str(database_value or "").strip()
        if primary_server and server == primary_server:
            add(database, "identity")
        elif flow_server and server == flow_server:
            add(database, "flow")
        elif database:
            unconfigured_targets[(server, database)] = {
                "server": server,
                "database": database,
                "reason_code": "unconfigured_catalog_endpoint",
            }

    add(PGDATABASE, "configured")
    with get_db() as db:
        active_source_ids = get_active_source_ids(db)
        identity_rows = db.execute(
            """SELECT spi.source_id, spi.server_name, spi.database_name
               FROM source_postgres_identities spi
               JOIN sources s ON s.id=spi.source_id
               WHERE COALESCE(s.archived, 0)=0
                 AND NULLIF(TRIM(spi.database_name), '') IS NOT NULL""",
        ).fetchall()
        for row in identity_rows:
            if int(row["source_id"]) in active_source_ids:
                add_endpoint(row["server_name"], row["database_name"])

        flow_rows = db.execute(
            """SELECT DISTINCT sql_database
               FROM flows
               WHERE sql_handoff_enabled=1
                 AND NULLIF(TRIM(sql_database), '') IS NOT NULL"""
        ).fetchall()
        for row in flow_rows:
            add(row["sql_database"], "flow")

    for target in report_catalog_targets:
        add_endpoint(target.get("server"), target.get("database"))

    databases = sorted(origins, key=lambda value: (value.casefold(), value))
    origin_order = {"configured": 0, "identity": 1, "flow": 2}
    serialized = {
        database: sorted(origins[database], key=lambda value: origin_order[value])
        for database in databases
    }
    return databases, serialized, [
        unconfigured_targets[key]
        for key in sorted(
            unconfigured_targets,
            key=lambda item: (item[0].casefold(), item[0], item[1].casefold(), item[1]),
        )
    ]


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


def _flow_catalog_connection(database: str):
    """Open the Flow target database in a forced read-only session.

    Some installations intentionally configure only the dedicated Flow SQL
    credentials.  Those credentials already reach the physical target, so a
    focused catalog scan may use them for SELECT-only ``pg_depend`` discovery
    instead of declaring the Flow's own database unscannable.  PostgreSQL
    enforces read-only mode for the session before any catalog query runs.
    """
    if not UPLOAD_PGHOST:
        return None
    connection = None
    try:
        import psycopg2

        connection_kwargs = {
            "host": UPLOAD_PGHOST,
            "port": int(UPLOAD_PGPORT),
            "database": database or UPLOAD_PGDATABASE,
            "connect_timeout": 10,
            "options": (
                f"-c statement_timeout={int(PG_SCAN_STATEMENT_TIMEOUT_SECONDS) * 1000} "
                "-c lock_timeout=30000 "
                "-c application_name=Metronome_Lineage"
            ),
        }
        if UPLOAD_PGUSER:
            connection_kwargs["user"] = UPLOAD_PGUSER
        if UPLOAD_PGPASSWORD:
            connection_kwargs["password"] = UPLOAD_PGPASSWORD
        connection = psycopg2.connect(
            **connection_kwargs,
        )
        connection.set_session(readonly=True, autocommit=True)
        return connection
    except Exception as exc:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        logger.warning("Flow PostgreSQL catalog connection failed: %s", _redact_error(exc))
        return None


def _fetch_database_catalog(
    database: str,
    *,
    use_flow_credentials: bool = False,
    referenced_relations: tuple[tuple[str, str], ...] = (),
) -> _DatabaseCatalog:
    """Fetch a complete dependency snapshot before opening a SQLite write batch."""
    pg_conn = (
        _flow_catalog_connection(database)
        if use_flow_credentials
        else _connection_for_database(database)
    )
    if pg_conn is None:
        raise RuntimeError(
            f"PostgreSQL {'Flow target ' if use_flow_credentials else ''}connection "
            f"unavailable for database {database!r}."
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

        relations: dict[tuple[str, str], str] = {}
        for parent, kind in parent_kinds.items():
            relations[parent] = kind
        for _parent_schema, _parent_name, schema, name, kind in dependency_rows:
            relations[(schema, name)] = str(kind)

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
                relations[(schema, name)] = "m"
        except Exception as exc:
            definition_error = _redact_error(exc)
            logger.warning(
                "MV definition capture skipped for database %s: %s",
                database,
                definition_error,
            )

        requested_relations = tuple(sorted(set(referenced_relations)))
        if requested_relations:
            values = ",".join("(%s,%s)" for _ in requested_relations)
            parameters = tuple(value for pair in requested_relations for value in pair)
            pg_cur.execute(
                f"""SELECT ns.nspname, cls.relname, cls.relkind
                       FROM pg_class cls
                       JOIN pg_namespace ns ON ns.oid=cls.relnamespace
                      WHERE (ns.nspname, cls.relname) IN ({values})
                        AND cls.relkind IN ('r','p','m','v','f')
                      ORDER BY ns.nspname, cls.relname""",
                parameters,
            )
            for row in pg_cur.fetchall():
                if len(row) != 3:
                    raise ValueError("PostgreSQL adapter returned an unexpected relation row")
                relations[(str(row[0]), str(row[1]))] = str(row[2])

        return _DatabaseCatalog(
            dependency_rows=tuple(dependency_rows),
            definitions=definitions,
            definition_error=definition_error,
            parent_kinds=parent_kinds,
            relations=relations,
            requested_relations=requested_relations,
        )
    finally:
        pg_conn.close()


def _fetch_catalog_for_target(
    database: str,
    *,
    use_flow_credentials: bool,
    referenced_relations: tuple[tuple[str, str], ...],
) -> _DatabaseCatalog:
    """Call the catalog reader while tolerating older injected adapters."""
    try:
        parameters = inspect.signature(_fetch_database_catalog).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_references = any(
        parameter.name == "referenced_relations"
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    kwargs = {"use_flow_credentials": use_flow_credentials}
    if accepts_references:
        kwargs["referenced_relations"] = referenced_relations
    return _fetch_database_catalog(database, **kwargs)


def scan_pg_dependencies(
    scan_run_id: int | None = None,
    *,
    report_id: int | None = None,
    operation_id: int | None = None,
    cancel_generation: int | None = None,
) -> dict:
    """Refresh dependency lineage independently for every required database."""
    assert_not_cancelled(cancel_generation, "PostgreSQL lineage scan")
    report_identity_reconciliation = (
        reconcile_report_postgres_identities(report_id, defer_relinks=True)
        if report_id is not None
        else reconcile_all_report_postgres_identities(defer_relinks=True)
    )
    scanner_job_heartbeat(
        operation_id,
        current_step="Discovering PostgreSQL databases",
        message="Resolving databases required by reports and Flow SQL targets.",
    )
    now = datetime.now(timezone.utc).isoformat()
    databases, origins, unconfigured_catalog_targets = _required_databases(
        report_identity_reconciliation.get("catalog_targets", ())
    )
    report_catalog_keys = {
        (
            normalize_server(target.get("server")),
            str(target.get("database") or "").strip(),
        )
        for target in report_identity_reconciliation.get("catalog_targets", ())
    }
    reconciliation_unconfigured_targets = [
        target
        for target in unconfigured_catalog_targets
        if report_id is not None
        and (target["server"], target["database"]) in report_catalog_keys
    ]
    for target in reconciliation_unconfigured_targets:
        report_identity_reconciliation["unconfigured_catalog_targets"] += 1
        report_identity_reconciliation["issues"].append(
            {
                "reason_code": "unconfigured_catalog_endpoint",
                "server": target["server"],
                "database": target["database"],
            }
        )
    catalog_targets = _catalog_scan_targets(databases, origins)
    if not databases:
        complete_report_postgres_identity_reconciliation(
            report_identity_reconciliation
        )
        if reconciliation_unconfigured_targets:
            report_identity_reconciliation["status"] = "completed_with_warnings"
        final_databases, final_origins, unconfigured_catalog_targets = _required_databases(
            report_identity_reconciliation.get("catalog_targets", ())
        )
        final_catalog_targets = _catalog_scan_targets(final_databases, final_origins)
        unattempted_catalog_targets = [
            {
                "database": target.database,
                "server": target.server,
                "credential_profile": target.credential_profile,
                "origins": list(target.origins),
                "reason_code": "catalog_target_became_active_during_scan",
            }
            for target in final_catalog_targets
        ]
        reconciliation_status = report_identity_reconciliation.get("status")
        status = (
            "not_requested"
            if (
                reconciliation_status in {"completed", "not_requested"}
                and not unconfigured_catalog_targets
                and not unattempted_catalog_targets
            )
            else "completed_with_warnings"
        )
        return {
            "status": status,
            "required_databases": final_databases,
            "database_origins": final_origins,
            "flow_server_mismatch_databases": [],
            "flow_target_catalog_databases": sorted(
                {
                    target.database
                    for target in final_catalog_targets
                    if target.credential_profile == "flow_target"
                },
                key=lambda value: (value.casefold(), value),
            ),
            "catalog_targets": [
                {
                    "database": target.database,
                    "server": target.server,
                    "credential_profile": target.credential_profile,
                    "origins": list(target.origins),
                }
                for target in final_catalog_targets
            ],
            "superseded_catalog_targets": [],
            "unattempted_catalog_targets": unattempted_catalog_targets,
            "unconfigured_catalog_targets": unconfigured_catalog_targets,
            "report_identity_reconciliation": report_identity_reconciliation,
            "databases": {},
            "mvs_found": 0,
            "deps_created": 0,
            "sources_created": 0,
            "definition_status": "not_requested",
            "log": (
                "One or more active PostgreSQL catalog targets were not scanned; "
                "rerun lineage to use the final target set."
                if unattempted_catalog_targets
                else "An active PostgreSQL endpoint is not configured for "
                "read-only catalog scanning."
                if unconfigured_catalog_targets
                else "No PostgreSQL databases require dependency discovery."
            ),
        }

    flow_target_catalog_databases = {
        target.database
        for target in catalog_targets
        if target.credential_profile == "flow_target"
    }

    database_results: dict[str, dict] = {}
    total_targets = len(catalog_targets)
    result_keys = _catalog_result_keys(catalog_targets)
    for target_index, (target, result_key) in enumerate(
        zip(catalog_targets, result_keys),
        start=1,
    ):
        database = target.database
        assert_not_cancelled(cancel_generation, "PostgreSQL lineage scan")
        scanner_job_heartbeat(
            operation_id,
            current_step="Reading PostgreSQL catalog",
            message=f"Scanning {database} on {target.server or 'the configured server'}.",
            progress_current=target_index - 1,
            progress_total=total_targets,
        )
        use_flow_credentials = target.credential_profile == "flow_target"
        credential_profile = target.credential_profile
        catalog_server = target.server
        protected_source_ids = (
            pending_report_postgres_identity_target_source_ids(
                report_identity_reconciliation,
                server=catalog_server,
                database=database,
            )
        )
        referenced_relations = _referenced_relations(
            server=catalog_server,
            database=database,
            pending_source_ids=protected_source_ids,
        )
        try:
            catalog = _fetch_catalog_for_target(
                database,
                use_flow_credentials=use_flow_credentials,
                referenced_relations=referenced_relations,
            )
        except Exception as primary_exc:
            # If both roles point to the same endpoint, the dedicated Flow account
            # is a safe availability fallback for SELECT-only catalog work.
            same_server_flow_fallback = (
                not use_flow_credentials and _catalog_endpoints_match()
            )
            if same_server_flow_fallback:
                use_flow_credentials = True
                credential_profile = "flow_target"
                catalog_server = _flow_server_identity()
                try:
                    catalog = _fetch_catalog_for_target(
                        database,
                        use_flow_credentials=True,
                        referenced_relations=referenced_relations,
                    )
                    fetch_error = None
                except Exception as fallback_exc:
                    catalog = None
                    fetch_error = fallback_exc
            else:
                catalog = None
                fetch_error = primary_exc
            if catalog is None:
                exc = fetch_error
                error = _redact_error(exc)
                logger.warning(
                    "PostgreSQL dependency catalog fetch failed for %s: %s",
                    database,
                    error,
                )
                _mark_catalog_verification_failed(
                    server=catalog_server,
                    database=database,
                )
                database_results[result_key] = {
                    "status": "failed",
                    "stage": "fetch",
                    "database": database,
                    "error": error,
                    "mvs_found": 0,
                    "deps_created": 0,
                    "sources_created": 0,
                    "definition_status": "not_requested",
                    "catalog_server": normalize_server(catalog_server),
                    "credential_profile": credential_profile,
                    "log": "Catalog fetch failed; prior lineage was retained.",
                }
                scanner_job_heartbeat(
                    operation_id,
                    current_step="Reading PostgreSQL catalog",
                    message=f"Database {database} could not be read; prior lineage was retained.",
                    progress_current=target_index,
                    progress_total=total_targets,
                )
                continue

        assert_not_cancelled(cancel_generation, "PostgreSQL lineage scan")
        scanner_job_heartbeat(
            operation_id,
            current_step="Applying lineage snapshot",
            message=f"Reconciling materialized views and Flow targets in {database}.",
            progress_current=target_index - 1,
            progress_total=total_targets,
        )
        try:
            applied_result = _apply_database_catalog(
                database,
                catalog,
                server=catalog_server,
                protected_source_ids=protected_source_ids,
                scan_run_id=scan_run_id,
                now=now,
            )
        except Exception as exc:
            error = _redact_error(exc)
            logger.warning(
                "PostgreSQL dependency batch failed for %s: %s", database, error
            )
            database_results[result_key] = {
                "status": "failed",
                "stage": "apply",
                "database": database,
                "error": error,
                "mvs_found": 0,
                "deps_created": 0,
                "sources_created": 0,
                "definition_status": (
                    "skipped" if catalog.definition_error else "completed"
                ),
                "log": "Catalog apply failed; prior lineage was retained.",
                "catalog_server": normalize_server(catalog_server),
                "credential_profile": credential_profile,
            }
            if catalog.definition_error:
                database_results[result_key]["definition_error"] = _redact_error(
                    catalog.definition_error
                )
        else:
            database_results[result_key] = applied_result
            applied_result["database"] = database
            applied_result["catalog_server"] = normalize_server(catalog_server)
            applied_result["credential_profile"] = credential_profile
            try:
                finalize_report_postgres_identity_relinks(
                    report_identity_reconciliation,
                    server=catalog_server,
                    database=database,
                    verified_source_ids=applied_result.get("verified_source_ids") or (),
                )
            except Exception as exc:
                # The catalog transaction has already committed. Report that
                # truthfully and retain its counts/edges while the deferred
                # completion path leaves the prior report link in place.
                error = _redact_error(exc)
                logger.warning(
                    "PostgreSQL catalog committed but report relink failed for %s: %s",
                    database,
                    error,
                )
                applied_result["status"] = "completed_with_warnings"
                applied_result["warning_stage"] = "report_relink"
                warning_stages = list(applied_result.get("warning_stages") or [])
                if "report_relink" not in warning_stages:
                    warning_stages.append("report_relink")
                applied_result["warning_stages"] = warning_stages
                applied_result["report_relink_error"] = error
                applied_result["log"] = (
                    f"{applied_result.get('log') or ''}\n"
                    "Catalog lineage was committed; the prior report link was retained "
                    "because report relinking failed."
                ).strip()
        scanner_job_heartbeat(
            operation_id,
            current_step="Applying lineage snapshot",
            message=f"Finished database {database}.",
            progress_current=target_index,
            progress_total=total_targets,
        )

    complete_report_postgres_identity_reconciliation(
        report_identity_reconciliation
    )
    # A focused repair can replace a report's stale source anchor. Reclassify
    # endpoint warnings after every deferred relink has either committed or
    # been abandoned so an obsolete, now-inactive source cannot leave the job
    # in a false warning state. Exact unconfigured report targets are passed
    # again and therefore remain visible when their catalog was not scannable.
    final_databases, final_origins, unconfigured_catalog_targets = _required_databases(
        report_identity_reconciliation.get("catalog_targets", ())
    )
    final_catalog_targets = _catalog_scan_targets(final_databases, final_origins)
    final_catalog_keys = {
        (normalize_server(target.server), target.database)
        for target in final_catalog_targets
    }
    initial_target_results = {
        (normalize_server(target.server), target.database): (target, result_key)
        for target, result_key in zip(catalog_targets, result_keys)
    }
    superseded_catalog_targets = []
    for target, result_key in zip(catalog_targets, result_keys):
        target_key = (normalize_server(target.server), target.database)
        if target_key in final_catalog_keys or result_key not in database_results:
            continue
        result = database_results[result_key]
        prior_status = result.get("status")
        result["attempt_status"] = prior_status
        result["status"] = "superseded"
        result["superseded_after_report_relink"] = True
        result["log"] = (
            f"{result.get('log') or ''}\n"
            "This catalog target became inactive after the selected report was "
            "safely relinked; its attempt no longer affects current lineage health."
        ).strip()
        superseded_catalog_targets.append(
            {
                "database": target.database,
                "server": target.server,
                "credential_profile": target.credential_profile,
                "result_key": result_key,
                "attempt_status": prior_status,
            }
        )
    unattempted_catalog_targets = []
    for final_target in final_catalog_targets:
        target_key = (normalize_server(final_target.server), final_target.database)
        initial = initial_target_results.get(target_key)
        if initial is None or initial[1] not in database_results:
            unattempted_catalog_targets.append(
                {
                    "database": final_target.database,
                    "server": final_target.server,
                    "credential_profile": final_target.credential_profile,
                    "origins": list(final_target.origins),
                    "reason_code": "catalog_target_became_active_during_scan",
                }
            )
            continue
        result = database_results[initial[1]]
        _refresh_final_flow_reconciliation(
            result,
            database=final_target.database,
            server=final_target.server,
            required=final_target.reconcile_flow_targets,
        )
    databases = final_databases
    origins = final_origins
    flow_target_catalog_databases = {
        target.database
        for target in final_catalog_targets
        if target.credential_profile == "flow_target"
    }
    if reconciliation_unconfigured_targets:
        report_identity_reconciliation["status"] = "completed_with_warnings"
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
    identity_warning = report_identity_reconciliation.get("status") not in {
        "completed",
        "not_requested",
    }
    if not final_catalog_targets:
        status = (
            "completed_with_warnings"
            if (
                identity_warning
                or unconfigured_catalog_targets
            )
            else "not_requested"
        )
    elif successful:
        status = (
            "completed_with_warnings"
            if (
                failed
                or warned
                or identity_warning
                or unconfigured_catalog_targets
                or unattempted_catalog_targets
            )
            else "completed"
        )
    elif failed:
        status = "failed"
    else:
        # The final target set changed after the start snapshot and none of its
        # current obligations has a completed catalog attempt yet.
        status = "completed_with_warnings"

    def total(key: str) -> int:
        return sum(int(result.get(key) or 0) for result in successful)

    log_sections = []
    for result_key, result in database_results.items():
        log_sections.append(
            f"[{result_key}] {result.get('log') or result['status']}"
        )

    definition_status = (
        "skipped"
        if any(result.get("definition_status") == "skipped" for result in successful)
        else ("completed" if successful else "not_requested")
    )
    summary = {
        "status": status,
        "required_databases": databases,
        "database_origins": origins,
        "flow_server_mismatch_databases": [],
        "flow_target_catalog_databases": sorted(
            flow_target_catalog_databases,
            key=lambda value: (value.casefold(), value),
        ),
        "catalog_targets": [
            {
                "database": target.database,
                "server": target.server,
                "credential_profile": target.credential_profile,
                "origins": list(target.origins),
            }
            for target in final_catalog_targets
        ],
        "superseded_catalog_targets": superseded_catalog_targets,
        "unattempted_catalog_targets": unattempted_catalog_targets,
        "unconfigured_catalog_targets": unconfigured_catalog_targets,
        "report_identity_reconciliation": report_identity_reconciliation,
        "databases": database_results,
        "mvs_found": total("mvs_found"),
        "deps_created": total("deps_created"),
        "sources_created": total("sources_created"),
        "definition_status": definition_status,
        "log": "\n".join(log_sections),
    }
    if status == "failed":
        errors = [result.get("error", "unknown failure") for result in failed]
        combined = (
            errors[0]
            if len(errors) == 1
            else "; ".join(errors)
            if errors
            else "Required PostgreSQL catalog scan did not complete."
        )
        summary["error"] = _redact_error(combined)
    scanner_job_heartbeat(
        operation_id,
        current_step="Finalizing PostgreSQL lineage",
        message=f"Processed {total_targets} PostgreSQL catalog target(s).",
        progress_current=total_targets,
        progress_total=total_targets,
    )
    logger.info("PG dependency scan completed: %s", summary)
    return summary


def _reconcile_database_flows(db, database: str, *, server: str) -> dict:
    """Reconcile only Flow targets owned by the committed database batch."""
    counts: dict[str, int] = {}
    flow_ids = db.execute(
        """SELECT id FROM flows
           WHERE sql_handoff_enabled=1 AND sql_database=?
           ORDER BY id""",
        (database,),
    ).fetchall()
    for row in flow_ids:
        result = reconcile_flow_target(db, int(row["id"]), server=server)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _refresh_final_flow_reconciliation(
    result: dict,
    *,
    database: str,
    server: str,
    required: bool,
) -> None:
    """Make a committed catalog result reflect the final Flow obligation.

    Flows can be enabled, moved, or disabled while a long catalog read is in
    progress. Catalog data is already committed at this point, so repeat only
    the local exact-identity reconciliation against the final Flow snapshot.
    Other warning stages (definition capture or report relinking) are retained.
    """
    if result.get("status") not in {"completed", "completed_with_warnings"}:
        return

    warning_stages = list(result.get("warning_stages") or [])
    prior_warning_stage = result.get("warning_stage")
    if prior_warning_stage and prior_warning_stage not in warning_stages:
        warning_stages.append(prior_warning_stage)
    warning_stages = [
        stage for stage in warning_stages if stage != "flow_reconciliation"
    ]
    prior_counts = result.get("flow_reconciliation")

    if not required:
        if prior_counts or result.get("flow_targets_needing_attention"):
            result["flow_reconciliation_superseded"] = True
            result["log"] = (
                f"{result.get('log') or ''}\n"
                "The earlier Flow target result no longer affects current health "
                "because this database is not a final Flow obligation."
            ).strip()
        result["flow_reconciliation"] = {}
        result["flow_targets_needing_attention"] = 0
        result.pop("flow_reconciliation_error", None)
    else:
        try:
            with get_db() as db:
                counts = _reconcile_database_flows(db, database, server=server)
        except Exception as exc:
            error = _redact_error(exc)
            result["flow_reconciliation_error"] = error
            result["flow_reconciliation"] = {}
            result["flow_targets_needing_attention"] = 0
            warning_stages.append("flow_reconciliation")
            result["log"] = (
                f"{result.get('log') or ''}\n"
                "Final Flow target reconciliation could not be completed."
            ).strip()
        else:
            warning_counts = {
                status: int(count)
                for status, count in counts.items()
                if status not in {"confirmed", "disabled"} and int(count or 0) > 0
            }
            attention = sum(warning_counts.values())
            result["flow_reconciliation"] = counts
            result["flow_targets_needing_attention"] = attention
            result.pop("flow_reconciliation_error", None)
            if attention:
                warning_stages.append("flow_reconciliation")
            detail = ", ".join(
                f"{status}={count}" for status, count in sorted(counts.items())
            )
            result["log"] = (
                f"{result.get('log') or ''}\n"
                "Final Flow target check"
                + (f": {detail}." if detail else ": no enabled targets remain.")
            ).strip()

    # Preserve order while eliminating duplicate stages.
    warning_stages = list(dict.fromkeys(warning_stages))
    if warning_stages:
        result["status"] = "completed_with_warnings"
        result["warning_stages"] = warning_stages
        result["warning_stage"] = warning_stages[-1]
    else:
        result["status"] = "completed"
        result.pop("warning_stages", None)
        result.pop("warning_stage", None)


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


def _cleanup_database_orphans(
    db,
    *,
    server: str,
    database: str,
    protected_source_ids: tuple[int, ...] | set[int] | frozenset[int] = (),
) -> int:
    """Delete unreferenced scanner sources only inside one physical database."""
    protected = {int(source_id) for source_id in protected_source_ids}
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
    source_ids = [
        int(row["id"])
        for row in candidates
        if int(row["id"]) not in protected
    ]
    for source_id in source_ids:
        db.execute("DELETE FROM source_probes WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM sources WHERE id=?", (source_id,))
    return len(source_ids)


def _apply_database_catalog(
    database: str,
    catalog: _DatabaseCatalog,
    *,
    server: str,
    protected_source_ids: tuple[int, ...] | set[int] | frozenset[int] = (),
    scan_run_id: int | None,
    now: str,
) -> dict:
    """Atomically replace one database's known materialized-view lineage."""
    mv_dependencies: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for mv_schema, mv_name, dep_schema, dep_name, dep_kind in catalog.dependency_rows:
        mv_dependencies.setdefault((mv_schema, mv_name), []).append(
            (dep_schema, dep_name, dep_kind)
        )
    # pg_depend has no allowed relation row for constant/function-only MVs.
    # pg_matviews is still authoritative that those parents exist, so seed
    # every captured definition even when it has zero table dependencies.
    for mv_identity in catalog.definitions:
        mv_dependencies.setdefault(mv_identity, [])

    mvs_found = 0
    deps_created = 0
    log_lines: list[str] = []

    with get_db() as db:
        _delete_database_edges(db, server=server, database=database)

        verified_source_ids: set[int] = set()
        for (schema, relation), kind_code in sorted(catalog.relations.items()):
            relation_kind = {
                "m": "materialized_view",
                "v": "view",
                "r": "table",
                "p": "table",
                "f": "foreign_table",
            }.get(kind_code, "table")
            source_id = _find_or_create_source(
                db,
                server=server,
                database=database,
                schema=schema,
                table=relation,
                now=now,
                relation_kind=relation_kind,
            )
            verified_source_ids.add(source_id)
            db.execute(
                """UPDATE report_tables
                      SET source_resolution_status='resolved',
                          source_resolution_reason=NULL
                    WHERE source_id=?""",
                (source_id,),
            )

        missing_relations = sorted(
            set(catalog.requested_relations) - set(catalog.relations)
        )
        missing_source_ids: set[int] = set()
        for schema, relation in missing_relations:
            rows = exact_identity_rows(
                db,
                server=server,
                database=database,
                schema=schema,
                relation=relation,
            )
            for row in rows:
                source_id = int(row["source_id"])
                missing_source_ids.add(source_id)
                db.execute(
                    """UPDATE report_tables
                          SET source_id=NULL,
                              source_resolution_status='unresolved',
                              source_resolution_reason='postgres_relation_not_found'
                        WHERE source_id=?""",
                    (source_id,),
                )
                db.execute(
                    "UPDATE flows SET sql_target_source_id=NULL WHERE sql_target_source_id=?",
                    (source_id,),
                )
            log_lines.append(
                f"UNRESOLVED: {schema}.{relation} was referenced locally but does not exist in the PostgreSQL catalog"
            )

        pending_relations = set(mv_dependencies)
        while pending_relations:
            progressed = False
            for mv_schema, mv_name in sorted(pending_relations):
                refs = mv_dependencies[(mv_schema, mv_name)]
                parent_kind_code = catalog.parent_kinds.get((mv_schema, mv_name), "m")
                parent_kind = "view" if parent_kind_code == "v" else "materialized_view"
                mv_source_id = _find_or_create_source(
                    db,
                    server=server,
                    database=database,
                    schema=mv_schema,
                    table=mv_name,
                    now=now,
                    relation_kind=parent_kind,
                )
                verified_source_ids.add(mv_source_id)

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
                        "f": "foreign_table",
                    }.get(
                        dep_kind, "table"
                    )
                    dep_source_id = _find_or_create_source(
                        db,
                        server=server,
                        database=database,
                        schema=dep_schema,
                        table=dep_table,
                        now=now,
                        relation_kind=kind,
                    )
                    verified_source_ids.add(dep_source_id)
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
                log_lines.append(
                    f"{label}: {full_mv_name} -> {', '.join(ref_names)}"
                    if ref_names
                    else f"{label}: {full_mv_name} (no relation dependencies found)"
                )

            if not progressed:
                break

        _cleanup_database_orphans(
            db,
            server=server,
            database=database,
            protected_source_ids=protected_source_ids,
        )
        # Flow links are reconciled once, after all catalog targets and
        # deferred report relinks complete. This avoids publishing a transient
        # start-snapshot result when Flows change during a long catalog read.
        flow_reconciliation = {}
        sources_created = db.execute(
            """SELECT COUNT(*)
               FROM sources s
               JOIN source_postgres_identities spi ON spi.source_id=s.id
               WHERE s.discovered_by='pg_deps' AND s.created_at=?
                 AND spi.server_name=? AND spi.database_name=?""",
            (now, normalize_server(server), database),
        ).fetchone()[0]

    flow_warning_statuses = {
        status: int(count)
        for status, count in flow_reconciliation.items()
        if status not in {"confirmed", "disabled"} and int(count or 0) > 0
    }
    flow_targets_needing_attention = sum(flow_warning_statuses.values())
    warning_stages = []
    if catalog.definition_error:
        warning_stages.append("materialized_view_definitions")
    if flow_targets_needing_attention:
        warning_stages.append("flow_reconciliation")
        detail = ", ".join(
            f"{status}={count}" for status, count in sorted(flow_warning_statuses.items())
        )
        log_lines.append(
            "Flow SQL target reconciliation needs attention"
            + (f": {detail}." if detail else ".")
        )

    result = {
        "status": "completed_with_warnings" if warning_stages else "completed",
        "mvs_found": mvs_found,
        "deps_created": deps_created,
        "sources_created": int(sources_created),
        "definition_status": "skipped" if catalog.definition_error else "completed",
        "verified_source_ids": sorted(verified_source_ids),
        "missing_referenced_relations": [
            {"schema": schema, "relation": relation}
            for schema, relation in missing_relations
        ],
        "missing_referenced_source_ids": sorted(missing_source_ids),
        "log": "\n".join(log_lines) if log_lines else "No MV dependencies found.",
        "flow_reconciliation": flow_reconciliation,
        "flow_targets_needing_attention": flow_targets_needing_attention,
    }
    if warning_stages:
        result["warning_stages"] = warning_stages
        if len(warning_stages) == 1:
            result["warning_stage"] = warning_stages[0]
    if catalog.definition_error:
        result["definition_error"] = _redact_error(catalog.definition_error)
    return result
