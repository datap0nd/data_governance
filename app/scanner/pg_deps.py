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
from app.scanner.prober import _get_pg_connection

logger = logging.getLogger(__name__)


def _find_or_create_source(db, schema: str, table: str, now: str) -> int | None:
    """Find existing source by schema.table pattern or create a new one.

    Returns source ID.
    """
    full_name = f"{schema}.{table}"

    # Try exact match on name ending with schema.table
    row = db.execute(
        "SELECT id FROM sources WHERE name LIKE ? AND archived = 0",
        (f"%{full_name}",),
    ).fetchone()
    if row:
        return row["id"]

    # Try matching just the table part in connection_info
    row = db.execute(
        "SELECT id FROM sources WHERE connection_info LIKE ? AND archived = 0",
        (f"%{full_name}%",),
    ).fetchone()
    if row:
        return row["id"]

    # Try matching just the table name (scanner may use db.table instead of schema.table)
    row = db.execute(
        "SELECT id FROM sources WHERE (name LIKE ? OR connection_info LIKE ?) AND archived = 0",
        (f"%.{table}", f"%.{table}%"),
    ).fetchone()
    if row:
        return row["id"]

    # Create new source for this upstream table
    cursor = db.execute(
        """INSERT INTO sources (name, type, connection_info, discovered_by, created_at, updated_at)
           VALUES (?, 'postgresql', ?, 'pg_deps', ?, ?)""",
        (full_name, full_name, now, now),
    )
    # Insert initial unknown probe
    db.execute(
        "INSERT INTO source_probes (source_id, probed_at, status, message) VALUES (?, ?, 'unknown', ?)",
        (cursor.lastrowid, now, "Discovered as MV dependency"),
    )
    return cursor.lastrowid


def scan_pg_dependencies() -> dict:
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
        return {"status": "skipped", "reason": "No PostgreSQL credentials configured"}

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

        if not dep_rows:
            return {"status": "completed", "mvs_found": 0, "deps_created": 0}

        # Group by MV
        mv_deps = {}
        for mv_schema, mv_name, dep_schema, dep_name, dep_kind in dep_rows:
            mv_key = f"{mv_schema}.{mv_name}"
            if mv_key not in mv_deps:
                mv_deps[mv_key] = []
            mv_deps[mv_key].append((dep_schema, dep_name))

        mvs_found = 0
        deps_created = 0
        log_lines = []

        with get_db() as db:
            # Clear old dependency edges (rebuild each time)
            db.execute("DELETE FROM source_dependencies WHERE discovered_by = 'pg_matviews'")

            for full_mv_name, refs in mv_deps.items():
                # Find this MV in our sources table
                mv_source = db.execute(
                    "SELECT id FROM sources WHERE name LIKE ? AND archived = 0",
                    (f"%{full_mv_name}",),
                ).fetchone()
                if not mv_source:
                    mv_source = db.execute(
                        "SELECT id FROM sources WHERE connection_info LIKE ? AND archived = 0",
                        (f"%{full_mv_name}%",),
                    ).fetchone()

                if not mv_source:
                    continue

                mv_source_id = mv_source["id"]
                mvs_found += 1

                for dep_schema, dep_table in refs:
                    dep_source_id = _find_or_create_source(db, dep_schema, dep_table, now)
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

                ref_names = [f"{s}.{t}" for s, t in refs]
                log_lines.append(f"MV: {full_mv_name} -> {', '.join(ref_names)}")

            # Clean up orphaned sources created by pg_deps or pg_matviews
            # that no longer have any dependency edges or script references
            db.execute("""
                DELETE FROM sources
                WHERE discovered_by IN ('pg_deps', 'pg_matviews')
                  AND id NOT IN (SELECT depends_on_id FROM source_dependencies)
                  AND id NOT IN (SELECT source_id FROM source_dependencies)
                  AND id NOT IN (SELECT source_id FROM report_tables WHERE source_id IS NOT NULL)
                  AND id NOT IN (SELECT source_id FROM script_tables WHERE source_id IS NOT NULL)
            """)

            sources_created = db.execute(
                "SELECT COUNT(*) FROM sources WHERE discovered_by = 'pg_deps' AND created_at = ?",
                (now,),
            ).fetchone()[0]

        summary = {
            "status": "completed",
            "mvs_found": mvs_found,
            "deps_created": deps_created,
            "sources_created": sources_created,
            "log": "\n".join(log_lines) if log_lines else "No MV dependencies found.",
        }
        logger.info("PG dependency scan completed: %s", summary)
        return summary

    except Exception as e:
        logger.exception("PG dependency scan failed: %s", e)
        return {"status": "failed", "error": str(e)}

    finally:
        pg_conn.close()
