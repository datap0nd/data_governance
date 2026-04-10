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


def _match_source_sql(table_name: str, source_cache: dict) -> int | None:
    """Match a SQL table name against source cache.

    Tries exact match first, then boundary-aware endswith.
    source_cache is {source_id: source_name_lowercase}.
    """
    tl = table_name.lower()
    # Exact match
    for sid, sname in source_cache.items():
        if sname == tl:
            return sid
    # Boundary-aware endswith: source name must end with the table name,
    # preceded by a slash, dot, or start-of-string (prevents partial matches
    # like "old_channel_mappings" matching "channel_mappings")
    for sid, sname in source_cache.items():
        if sname.endswith(tl):
            prefix_len = len(sname) - len(tl)
            if prefix_len == 0 or sname[prefix_len - 1] in ("/", ".", "\\"):
                return sid
    return None


def _match_source_file(ref: str, source_cache: dict, conn_cache: dict) -> int | None:
    """Match a file reference from script scanner against sources.

    Script file refs look like: [excel]C:\\path\\to\\file.xlsx
    Source names for files are just the filename: file.xlsx
    Source connection_info contains the full path.

    source_cache is {source_id: source_name_lowercase}.
    conn_cache is {source_id: connection_info_lowercase}.
    """
    # Extract the actual path from the tagged ref: [type]path -> path
    m = re.match(r'^\[[^\]]+\](.+)$', ref)
    if not m:
        return None
    filepath = m.group(1).lower().replace("/", "\\")
    filename = filepath.rsplit("\\", 1)[-1] if "\\" in filepath else filepath

    # Try matching the full path against connection_info
    for sid, conn in conn_cache.items():
        if conn and filepath in conn.replace("/", "\\"):
            return sid

    # Try matching just the filename against source names
    for sid, sname in source_cache.items():
        if sname == filename:
            return sid

    return None


def _match_source_url(ref: str, source_cache: dict, conn_cache: dict) -> int | None:
    """Match a URL reference from script scanner against sources.

    Script URL refs look like: [web-scraping]https://example.com/data
    Source names/connection_info for web sources contain the URL.
    """
    m = re.match(r'^\[[^\]]+\](.+)$', ref)
    if not m:
        return None
    url = m.group(1).lower()

    # Try matching URL against source names or connection_info
    for sid, sname in source_cache.items():
        if url in sname or sname in url:
            return sid
    for sid, conn in conn_cache.items():
        if conn and (url in conn or conn in url):
            return sid

    return None


def _store_script_refs(db, script_id, result, now, source_cache=None,
                       conn_cache=None, schema_map=None):
    """Clear and re-insert script_tables for a single script.

    *source_cache* is {source_id: source_name_lowercase} for matching.
    *conn_cache* is {source_id: connection_info_lowercase} for file/URL matching.
    *schema_map* maps unqualified table names to schema.table (from PG catalog).
    Returns the number of tables successfully linked to sources.
    """
    tables_linked = 0
    schema_map = schema_map or {}
    source_cache = source_cache or {}
    conn_cache = conn_cache or {}

    db.execute("DELETE FROM script_tables WHERE script_id = ?", (script_id,))

    def _qualify(table_name):
        """Qualify a table name with its schema if known."""
        if "." in table_name or table_name.startswith("["):
            return table_name
        return schema_map.get(table_name, table_name)

    def _insert(table_name, direction, source_id=None):
        db.execute(
            """INSERT INTO script_tables (script_id, table_name, direction, source_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(script_id, table_name, direction) DO NOTHING""",
            (script_id, table_name, direction, source_id),
        )

    for table_name in result.tables_read:
        qualified = _qualify(table_name)
        source_id = _match_source_sql(qualified, source_cache)
        _insert(qualified, "read", source_id)
        if source_id:
            tables_linked += 1

    for table_name in result.tables_written:
        qualified = _qualify(table_name)
        source_id = _match_source_sql(qualified, source_cache)
        _insert(qualified, "write", source_id)
        if source_id:
            tables_linked += 1

    for ref in result.files_read:
        source_id = _match_source_file(ref, source_cache, conn_cache)
        _insert(ref, "read", source_id)
        if source_id:
            tables_linked += 1

    for ref in result.files_written:
        source_id = _match_source_file(ref, source_cache, conn_cache)
        _insert(ref, "write", source_id)
        if source_id:
            tables_linked += 1

    for ref in result.urls_read:
        source_id = _match_source_url(ref, source_cache, conn_cache)
        _insert(ref, "read", source_id)
        if source_id:
            tables_linked += 1

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
            source_rows = db.execute("SELECT id, name, connection_info FROM sources").fetchall()
            source_cache = {r["id"]: (r["name"] or "").lower() for r in source_rows}
            conn_cache = {r["id"]: (r["connection_info"] or "").lower() for r in source_rows}

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

                linked = _store_script_refs(db, script_id, result, now, source_cache, conn_cache, schema_map)
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
            # Pre-load sources for matching
            source_rows = db.execute("SELECT id, name, connection_info FROM sources").fetchall()
            source_cache = {r["id"]: (r["name"] or "").lower() for r in source_rows}
            conn_cache = {r["id"]: (r["connection_info"] or "").lower() for r in source_rows}

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

                linked = _store_script_refs(db, script_id, result, now,
                                           source_cache, conn_cache, schema_map)
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
