"""
PostgreSQL materialized view dependency scanner.

Uses pg_depend + pg_rewrite to find real table dependencies for each
materialized view, registers upstream tables as sources, and stores
dependency edges in source_dependencies.

READ-ONLY: Only SELECT queries are used against PostgreSQL.
"""

import logging
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
from app.config import PGHOST, PGDATABASE, UPLOAD_PGHOST
from app.source_identity import (
    exact_identity_rows,
    normalize_server,
    reconcile_all_flow_targets,
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


def scan_pg_dependencies(scan_run_id: int | None = None) -> dict:
    """Scan PostgreSQL for materialized view dependencies.

    Uses pg_depend/pg_rewrite catalog tables to find real table dependencies
    for each MV (no SQL text parsing). This gives accurate results even for
    complex MVs with CTEs, subqueries, dblink, string literals, etc.

    For each MV that is tracked as a source:
    1. Query pg_depend for its table/MV dependencies
    2. Register upstream tables as sources
    3. Create dependency edges

    READ-ONLY: Only SELECT queries against PostgreSQL.

    Returns summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    pg_conn = _get_pg_connection()

    if pg_conn is None:
        return {
            "status": "skipped",
            "reason": "No PostgreSQL credentials configured",
            "changed_queries": 0,
        }

    try:
        pg_cur = pg_conn.cursor()

        # Get all MV dependencies via pg_depend + pg_rewrite.
        # This returns (mv_schema, mv_name, dep_schema, dep_name, dep_kind)
        # where dep_kind is 'r' (table), 'm' (materialized view), or 'v' (view).
        # READ-ONLY: SELECT from system catalogs only.
        pg_cur.execute("""
            SELECT DISTINCT
                ns_mv.nspname  AS mv_schema,
                c_mv.relname   AS mv_name,
                ns_dep.nspname AS dep_schema,
                c_dep.relname  AS dep_name,
                c_dep.relkind  AS dep_kind
            FROM pg_depend d
            JOIN pg_rewrite rw  ON rw.oid = d.objid
            JOIN pg_class c_mv  ON c_mv.oid = rw.ev_class
            JOIN pg_namespace ns_mv ON ns_mv.oid = c_mv.relnamespace
            JOIN pg_class c_dep ON c_dep.oid = d.refobjid
            JOIN pg_namespace ns_dep ON ns_dep.oid = c_dep.relnamespace
            WHERE c_mv.relkind = 'm'
              AND d.deptype = 'n'
              AND d.classid = 'pg_rewrite'::regclass
              AND c_dep.relkind IN ('r', 'm', 'v')
              AND c_dep.oid != c_mv.oid
              AND ns_dep.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY ns_mv.nspname, c_mv.relname, ns_dep.nspname, c_dep.relname
        """)
        dep_rows = pg_cur.fetchall()

        # Definition capture is intentionally independent of dependency
        # discovery. If permissions or a test adapter cannot expose pg_matviews,
        # lineage still refreshes and no false query-change alert is produced.
        mv_definitions: dict[str, str] = {}
        definition_error = None
        try:
            pg_cur.execute("""
                SELECT schemaname, matviewname, definition
                FROM pg_matviews
                ORDER BY schemaname, matviewname
            """)
            definition_rows = pg_cur.fetchall()
            for row in definition_rows:
                if len(row) != 3:
                    raise ValueError("PostgreSQL adapter returned an unexpected pg_matviews row")
                schema, name, definition = row
                mv_definitions[f"{schema}.{name}"] = definition or ""
        except Exception as exc:
            definition_error = str(exc)
            logger.warning("MV definition capture skipped: %s", exc)

        # Group by MV
        mv_deps = {}
        for mv_schema, mv_name, dep_schema, dep_name, dep_kind in dep_rows:
            mv_key = f"{mv_schema}.{mv_name}"
            if mv_key not in mv_deps:
                mv_deps[mv_key] = []
            mv_deps[mv_key].append((dep_schema, dep_name, dep_kind))

        mvs_found = 0
        deps_created = 0
        changed_queries = 0
        log_lines = []
        query_change_lines = []

        with get_db() as db:
            # Clear old dependency edges (rebuild each time)
            db.execute("DELETE FROM source_dependencies WHERE discovered_by = 'pg_matviews'")

            # An upstream dependency can itself be an MV that is not in the
            # local source inventory yet. Keep resolving newly created MVs
            # until the reachable catalog graph is exhausted. Processing each
            # MV once also makes cycles harmless.
            pending_mvs = set(mv_deps)
            while pending_mvs:
                progressed = False
                for full_mv_name in sorted(pending_mvs):
                    refs = mv_deps[full_mv_name]
                    mv_schema, mv_name = full_mv_name.split(".", 1)

                    # Only an exact structured identity can anchor a tracked
                    # MV. An upstream MV created while processing a downstream
                    # relation becomes eligible on the next fixed-point pass.
                    mv_identity = _one_exact_identity(
                        db,
                        server=PGHOST,
                        database=PGDATABASE,
                        schema=mv_schema,
                        relation=mv_name,
                    )
                    if mv_identity is None:
                        continue

                    pending_mvs.remove(full_mv_name)
                    progressed = True
                    mv_source_id = int(mv_identity["source_id"])
                    mvs_found += 1
                    _claim_identity(
                        db,
                        source_id=mv_source_id,
                        server=PGHOST,
                        database=PGDATABASE,
                        schema=mv_schema,
                        relation=mv_name,
                        relation_kind="materialized_view",
                        verified_at=now,
                    )

                    for dep_schema, dep_table, dep_kind in refs:
                        kind = {"m": "materialized_view", "v": "view", "r": "table"}.get(
                            dep_kind, "table"
                        )
                        dep_source_id = _find_or_create_source(
                            db,
                            server=PGHOST,
                            database=PGDATABASE,
                            schema=dep_schema,
                            table=dep_table,
                            now=now,
                            relation_kind=kind,
                        )
                        if dep_source_id and dep_source_id != mv_source_id:
                            try:
                                db.execute(
                                    """INSERT INTO source_dependencies (source_id, depends_on_id, discovered_by, created_at)
                                       VALUES (?, ?, 'pg_matviews', ?)""",
                                    (mv_source_id, dep_source_id, now),
                                )
                                deps_created += 1
                            except Exception:
                                pass  # UNIQUE constraint

                    ref_names = [f"{s}.{t}" for s, t, _kind in refs]
                    log_lines.append(f"MV: {full_mv_name} -> {', '.join(ref_names)}")

                if not progressed:
                    break

            # Version only catalog MVs already present in the active Metronome
            # graph. This includes report sources and reachable upstream MVs,
            # while unrelated database objects remain invisible.
            active_source_ids = get_active_source_ids(db)
            for full_mv_name, definition in sorted(mv_definitions.items()):
                mv_schema, mv_name = full_mv_name.split(".", 1)
                mv_identity = _one_exact_identity(
                    db,
                    server=PGHOST,
                    database=PGDATABASE,
                    schema=mv_schema,
                    relation=mv_name,
                )
                if mv_identity is None:
                    continue
                source_id = int(mv_identity["source_id"])
                if source_id not in active_source_ids:
                    continue

                mv_source = db.execute(
                    "SELECT id, name, owner FROM sources WHERE id=?",
                    (source_id,),
                ).fetchone()
                if not mv_source:
                    raise PostgresIdentityResolutionError(
                        f"PostgreSQL identity references missing source {source_id}"
                    )
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
                               JOIN downstream_sources ds ON sd.depends_on_id = ds.id
                           )
                           SELECT r.owner FROM report_tables rt
                           JOIN reports r ON r.id = rt.report_id
                           JOIN downstream_sources ds ON ds.id = rt.source_id
                           WHERE 1 = 1
                             AND COALESCE(r.archived, 0) = 0
                             AND NULLIF(TRIM(r.owner), '') IS NOT NULL
                           ORDER BY r.id LIMIT 1""",
                        (source_id,),
                    ).fetchone()
                    owner = owner_row["owner"] if owner_row else None

                db.execute(
                    """UPDATE actions
                       SET status='resolved', resolved_at=?, updated_at=?,
                           notes=COALESCE(notes, '') || ' [auto-resolved: superseded query change]'
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
                           (source_id, type, status, assigned_to, notes, fingerprint, created_at, updated_at)
                           VALUES (?, 'changed_query', 'open', ?, ?, ?, ?, ?)""",
                        (source_id, owner, notes, fingerprint, now, now),
                    )
                    action_id = int(cursor.lastrowid)
                link_versions_to_action(db, [observation.version_id], action_id)
                query_change_lines.append(f"CHANGED MV QUERY: {full_mv_name}")

            # Clean up orphaned sources created by pg_deps or pg_matviews
            # that no longer have any dependency edges or script references
            db.execute("""
                DELETE FROM sources
                WHERE discovered_by IN ('pg_deps', 'pg_matviews')
                  AND id NOT IN (SELECT depends_on_id FROM source_dependencies)
                  AND id NOT IN (SELECT source_id FROM source_dependencies)
                  AND id NOT IN (SELECT source_id FROM report_tables WHERE source_id IS NOT NULL)
                  AND id NOT IN (SELECT source_id FROM script_tables WHERE source_id IS NOT NULL)
                  AND id NOT IN (SELECT source_id FROM query_versions WHERE source_id IS NOT NULL)
            """)

            # Identity discovery is one transaction. Reconcile only after the
            # complete batch exists so Flows never observe a partial catalog.
            reconcile_all_flow_targets(db, server=UPLOAD_PGHOST)

            sources_created = db.execute(
                "SELECT COUNT(*) FROM sources WHERE discovered_by = 'pg_deps' AND created_at = ?",
                (now,),
            ).fetchone()[0]

        summary = {
            "status": "completed",
            "mvs_found": mvs_found,
            "deps_created": deps_created,
            "sources_created": sources_created,
            "changed_queries": changed_queries,
            "definition_status": "skipped" if definition_error else "completed",
            "log": "\n".join(log_lines) if log_lines else "No MV dependencies found.",
            "query_change_log": "\n".join(query_change_lines),
        }
        if definition_error:
            summary["definition_error"] = definition_error
        logger.info("PG dependency scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("PG dependency scan failed: %s", e)
        return {"status": "failed", "error": str(e)}

    finally:
        pg_conn.close()
