"""
PostgreSQL materialized view dependency scanner.

Queries pg_matviews for MV definitions, parses SQL to extract
referenced tables, registers upstream tables as sources, and
stores dependency edges in source_dependencies.

READ-ONLY: Only SELECT queries are used against PostgreSQL.
"""

import logging
import re
from datetime import datetime, timezone

from app.database import get_db
from app.scanner.prober import _get_pg_connection

logger = logging.getLogger(__name__)

# Regex to find table references in SQL: schema.table or just table
# Matches after FROM, JOIN, and comma-separated table lists
# Excludes common SQL keywords that could be mistaken for table names
_SQL_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "on",
    "as", "is", "null", "true", "false", "case", "when", "then",
    "else", "end", "group", "by", "order", "having", "limit",
    "offset", "union", "all", "intersect", "except", "join",
    "inner", "outer", "left", "right", "full", "cross", "natural",
    "using", "lateral", "with", "recursive", "distinct", "into",
    "values", "insert", "update", "delete", "create", "alter",
    "drop", "index", "table", "view", "materialized", "exists",
    "between", "like", "ilike", "similar", "any", "some",
    "coalesce", "nullif", "cast", "extract", "interval",
    "current_date", "current_timestamp", "now", "generate_series",
    "row_number", "rank", "dense_rank", "over", "partition",
    "filter", "within", "array", "unnest", "lateral",
    "pg_stat_user_tables", "information_schema",
}


def _parse_table_refs_from_sql(sql: str) -> list[tuple[str, str]]:
    """Extract (schema, table) references from a SQL string.

    Returns list of (schema, table) tuples. Unqualified tables
    get schema='public'.
    """
    if not sql:
        return []

    refs = set()

    # Normalize: collapse whitespace, remove comments
    clean = re.sub(r'--[^\n]*', ' ', sql)
    clean = re.sub(r'/\*.*?\*/', ' ', clean, flags=re.DOTALL)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Pattern: schema.table or just table after FROM/JOIN keywords
    # Match schema.table (with optional quoting)
    pattern = r'(?:FROM|JOIN)\s+((?:"[^"]+"|[a-zA-Z_][a-zA-Z0-9_]*)\.(?:"[^"]+"|[a-zA-Z_][a-zA-Z0-9_]*))'
    for m in re.finditer(pattern, clean, re.IGNORECASE):
        full = m.group(1)
        parts = full.replace('"', '').split('.', 1)
        if len(parts) == 2:
            schema, table = parts[0].strip(), parts[1].strip()
            if schema.lower() not in _SQL_KEYWORDS and table.lower() not in _SQL_KEYWORDS:
                refs.add((schema, table))

    # Also match unqualified table names after FROM/JOIN
    pattern2 = r'(?:FROM|JOIN)\s+(?!"[^"]+"\.|[a-zA-Z_][a-zA-Z0-9_]*\.)("?[a-zA-Z_][a-zA-Z0-9_]*"?)'
    for m in re.finditer(pattern2, clean, re.IGNORECASE):
        name = m.group(1).replace('"', '').strip()
        if name.lower() not in _SQL_KEYWORDS:
            refs.add(("public", name))

    # Also handle comma-separated tables in FROM: FROM a, b, c
    from_pattern = r'FROM\s+(.+?)(?:WHERE|GROUP|ORDER|HAVING|LIMIT|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|UNION|INTERSECT|EXCEPT|;|\)|\s*$)'
    for m in re.finditer(from_pattern, clean, re.IGNORECASE):
        from_clause = m.group(1)
        for part in from_clause.split(','):
            part = part.strip().split()[0] if part.strip() else ""
            part = part.replace('"', '')
            if not part or part.lower() in _SQL_KEYWORDS:
                continue
            if '.' in part:
                s, t = part.split('.', 1)
                if s.lower() not in _SQL_KEYWORDS and t.lower() not in _SQL_KEYWORDS:
                    refs.add((s, t))
            elif part.lower() not in _SQL_KEYWORDS:
                refs.add(("public", part))

    return list(refs)


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

    For each source that is a materialized view:
    1. Get its SQL definition from pg_matviews
    2. Parse referenced tables
    3. Register upstream tables as sources
    4. Create dependency edges

    READ-ONLY: Only SELECT queries against PostgreSQL.

    Returns summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    pg_conn = _get_pg_connection()

    if pg_conn is None:
        return {"status": "skipped", "reason": "No PostgreSQL credentials configured"}

    try:
        pg_cur = pg_conn.cursor()

        # Get all materialized views and their definitions
        # READ-ONLY: SELECT from pg_matviews
        pg_cur.execute(
            "SELECT schemaname, matviewname, definition FROM pg_matviews ORDER BY schemaname, matviewname"
        )
        mv_rows = pg_cur.fetchall()

        if not mv_rows:
            return {"status": "completed", "mvs_found": 0, "deps_created": 0}

        mvs_found = 0
        deps_created = 0
        sources_created = 0
        log_lines = []

        with get_db() as db:
            # Clear old dependency edges (rebuild each time)
            db.execute("DELETE FROM source_dependencies WHERE discovered_by = 'pg_matviews'")

            for schema, mvname, definition in mv_rows:
                full_mv_name = f"{schema}.{mvname}"

                # Find this MV in our sources table
                mv_source = db.execute(
                    "SELECT id FROM sources WHERE name LIKE ? AND archived = 0",
                    (f"%{full_mv_name}",),
                ).fetchone()
                if not mv_source:
                    # Also try connection_info
                    mv_source = db.execute(
                        "SELECT id FROM sources WHERE connection_info LIKE ? AND archived = 0",
                        (f"%{full_mv_name}%",),
                    ).fetchone()

                if not mv_source:
                    # This MV isn't tracked as a source - skip it
                    continue

                mv_source_id = mv_source["id"]
                mvs_found += 1

                # Parse SQL definition for table references
                refs = _parse_table_refs_from_sql(definition)

                # Filter out self-references
                refs = [(s, t) for s, t in refs if f"{s}.{t}" != full_mv_name]

                if not refs:
                    log_lines.append(f"MV: {full_mv_name} - no upstream tables found")
                    continue

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
                            pass  # UNIQUE constraint - already exists

                ref_names = [f"{s}.{t}" for s, t in refs]
                log_lines.append(f"MV: {full_mv_name} -> {', '.join(ref_names)}")

            # Count newly created sources
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
