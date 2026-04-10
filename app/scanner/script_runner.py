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

from app.config import SCRIPTS_PATH, SCRIPTS_PATHS, PGHOST, PGUSER, PGPASSWORD, PGDATABASE
from app.database import get_db
from app.scanner.script_scanner import parse_script, walk_scripts
from app.scanner.task_scheduler_scanner import MACHINE_ALIASES

logger = logging.getLogger(__name__)


def _resolve_pg_schemas(table_names: set[str]) -> dict[str, str]:
    """Query PostgreSQL to resolve unqualified table names to schema.table.

    Only resolves names that don't already have a dot (schema prefix).
    Returns a map of original_name -> schema.original_name.
    READ-ONLY: only SELECT on pg_class/pg_namespace system catalogs.
    """
    unqualified = {t for t in table_names if "." not in t and not t.startswith("[")}
    if not unqualified or not PGHOST or not PGUSER or not PGPASSWORD:
        return {}

    try:
        import psycopg2
        conn = psycopg2.connect(
            host=PGHOST, user=PGUSER, password=PGPASSWORD,
            database=PGDATABASE, connect_timeout=10,
        )
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute("""
            SELECT n.nspname, c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = ANY(%s)
              AND c.relkind IN ('r', 'v', 'm', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        """, (list(unqualified),))
        result = {}
        for schema, table in cur.fetchall():
            result[table] = f"{schema}.{table}"
        cur.close()
        conn.close()
        return result
    except Exception as e:
        logger.warning("PG schema resolution failed: %s", e)
        return {}


def _derive_machine(script_path: str) -> tuple[str, str]:
    """Derive hostname and alias from a script's file path.

    UNC paths are distinguished by both hostname AND user folder:
      \\\\MX-SHARE\\Users\\METOMX\\...  -> "Shared" (shared network drive)
      \\\\MX-SHARE\\Users\\meto.mx\\... -> "Admin"  (admin PC via network)
      \\\\METO-MX02\\...               -> "BI Desktop"
    Local paths -> local machine name.
    """
    p = script_path.lower().replace("/", "\\")

    # Check path-based aliases first (more specific than hostname alone)
    if "\\mx-share\\users\\meto.mx\\" in p:
        return "MX-Share", "Admin"
    if "\\mx-share\\users\\metomx\\" in p:
        return "MX-Share", "Shared"

    # Fall back to hostname-only matching
    m = re.match(r'^\\\\([^\\]+)\\', script_path)
    if m:
        hostname = m.group(1)
        alias = MACHINE_ALIASES.get(hostname.lower(), hostname)
        return hostname, alias

    # Local path
    hostname = socket.gethostname()
    alias = MACHINE_ALIASES.get(hostname.lower(), hostname)
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


def _store_script_refs(db, script_id, result, now, source_cache=None, schema_map=None):
    """Clear and re-insert script_tables for a single script.

    *source_cache* is an optional pre-loaded dict for _match_source lookups.
    *schema_map* maps unqualified table names to schema.table (from PG catalog).
    Returns the number of tables successfully linked to sources.
    """
    tables_linked = 0
    schema_map = schema_map or {}

    db.execute("DELETE FROM script_tables WHERE script_id = ?", (script_id,))

    def _qualify(table_name):
        """Qualify a table name with its schema if known."""
        if "." in table_name or table_name.startswith("["):
            return table_name
        return schema_map.get(table_name, table_name)

    def _cached_match(table_name):
        if source_cache is not None:
            tl = table_name.lower()
            for sid, sname in source_cache.items():
                if sname.endswith(tl):
                    return sid
            return None
        return _match_source(db, table_name)

    for table_name in result.tables_read:
        qualified = _qualify(table_name)
        source_id = _cached_match(qualified)
        db.execute(
            """INSERT INTO script_tables (script_id, table_name, direction, source_id)
               VALUES (?, ?, 'read', ?)
               ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
            (script_id, qualified, source_id),
        )
        if source_id:
            tables_linked += 1

    for table_name in result.tables_written:
        qualified = _qualify(table_name)
        source_id = _cached_match(qualified)
        db.execute(
            """INSERT INTO script_tables (script_id, table_name, direction, source_id)
               VALUES (?, ?, 'write', ?)
               ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
            (script_id, qualified, source_id),
        )
        if source_id:
            tables_linked += 1

    for ref in result.files_read:
        db.execute(
            """INSERT INTO script_tables (script_id, table_name, direction)
               VALUES (?, ?, 'read')
               ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
            (script_id, ref),
        )

    for ref in result.files_written:
        db.execute(
            """INSERT INTO script_tables (script_id, table_name, direction)
               VALUES (?, ?, 'write')
               ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
            (script_id, ref),
        )

    for ref in result.urls_read:
        db.execute(
            """INSERT INTO script_tables (script_id, table_name, direction)
               VALUES (?, ?, 'read')
               ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
            (script_id, ref),
        )

    return tables_linked


def reparse_scripts(on_progress=None) -> dict:
    """Re-parse all known scripts without walking directories.

    Reads file paths from the scripts table, re-reads and re-parses each file,
    and updates script_tables with fresh detection results. Skips the slow
    network directory walk entirely.
    """
    if on_progress:
        on_progress("Starting re-parse (no directory walk)")

    try:
        now = datetime.now(timezone.utc).isoformat()
        scripts_parsed = 0
        scripts_failed = 0
        scripts_removed = 0
        tables_linked = 0

        with get_db() as db:
            rows = db.execute(
                "SELECT id, path FROM scripts WHERE COALESCE(archived, 0) = 0"
            ).fetchall()

            if on_progress:
                on_progress(f"Re-parsing {len(rows)} scripts from database...")

            # Pre-load sources for matching
            source_rows = db.execute("SELECT id, name FROM sources").fetchall()
            source_cache = {r["id"]: (r["name"] or "").lower() for r in source_rows}

            from pathlib import Path

            # First pass: parse all files and collect unqualified table names
            parsed_results = []
            all_sql_tables = set()
            for row in rows:
                script_id = row["id"]
                filepath = Path(row["path"])

                if not filepath.exists():
                    scripts_failed += 1
                    if on_progress:
                        on_progress(f"  Missing: {filepath.name}")
                    parsed_results.append((script_id, filepath, None))
                    continue

                result = parse_script(filepath)
                if not result:
                    scripts_failed += 1
                    parsed_results.append((script_id, filepath, None))
                    continue

                parsed_results.append((script_id, filepath, result))
                all_sql_tables |= result.tables_read | result.tables_written

            # Resolve unqualified table names via PostgreSQL catalog
            if on_progress:
                unq = [t for t in all_sql_tables if "." not in t and not t.startswith("[")]
                if unq:
                    on_progress(f"Resolving {len(unq)} unqualified table names via PostgreSQL...")
            schema_map = _resolve_pg_schemas(all_sql_tables)
            if on_progress and schema_map:
                on_progress(f"  Resolved {len(schema_map)} table schemas")

            # Second pass: store results with qualified names
            for script_id, filepath, result in parsed_results:
                if result is None:
                    continue

                last_mod = result.last_modified.isoformat() if result.last_modified else None
                db.execute(
                    """UPDATE scripts SET last_modified = ?, last_scanned = ?,
                       file_size = ?, updated_at = ? WHERE id = ?""",
                    (last_mod, now, result.file_size, now, script_id),
                )

                linked = _store_script_refs(db, script_id, result, now, source_cache, schema_map)
                tables_linked += linked
                scripts_parsed += 1

                if on_progress and scripts_parsed % 20 == 0:
                    on_progress(f"  Parsed {scripts_parsed}/{len(rows)}...")

        summary = {
            "status": "completed",
            "scripts_parsed": scripts_parsed,
            "scripts_failed": scripts_failed,
            "scripts_total": len(rows),
            "tables_linked": tables_linked,
        }
        logger.info("Script re-parse completed: %s", summary)
        if on_progress:
            on_progress(f"Re-parse done: {scripts_parsed} parsed, {scripts_failed} failed, {tables_linked} linked")
        return summary

    except Exception as e:
        logger.exception("Script re-parse failed")
        return {"status": "failed", "error": str(e)}


def run_script_scan(scripts_path: str | None = None, on_progress=None, new_only: bool = False) -> dict:
    """Run a script scan and store results.

    Scans all configured paths (SCRIPTS_PATHS) unless a single path is given.
    If *new_only* is True, skip files already in the DB (faster).
    *on_progress* is an optional callback(message: str) for live logging.
    Returns a summary dict with scan statistics.
    """
    roots = [scripts_path] if scripts_path else SCRIPTS_PATHS
    now = datetime.now(timezone.utc).isoformat()
    mode = "new only" if new_only else "full"

    if on_progress:
        on_progress(f"Starting {mode} script scan across {len(roots)} path(s)")

    try:
        results = []
        for root in roots:
            if on_progress:
                on_progress(f"Scanning: {root}")
            results.extend(walk_scripts(root, on_progress=on_progress))

        if on_progress:
            on_progress(f"Storing {len(results)} scripts in database...")

        # Resolve unqualified SQL table names via PostgreSQL catalog
        all_sql_tables = set()
        for r in results:
            all_sql_tables |= r.tables_read | r.tables_written
        schema_map = _resolve_pg_schemas(all_sql_tables)
        if on_progress and schema_map:
            on_progress(f"Resolved {len(schema_map)} table schemas from PostgreSQL")

        scripts_found = 0
        scripts_updated = 0
        tables_linked = 0

        with get_db() as db:
            # Pre-load known paths for new_only mode
            known_paths = set()
            if new_only:
                known_paths = {r["path"] for r in db.execute("SELECT path FROM scripts").fetchall()}
                if on_progress:
                    on_progress(f"{len(known_paths)} scripts already in DB, skipping those")

            for result in results:
                # Upsert script record
                existing = db.execute(
                    "SELECT id FROM scripts WHERE path = ?",
                    (result.path,),
                ).fetchone()

                if new_only and existing:
                    continue  # Skip existing in new-only mode

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

                linked = _store_script_refs(db, script_id, result, now, schema_map=schema_map)
                tables_linked += linked

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
