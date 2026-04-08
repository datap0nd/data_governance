"""
Script scanner - walks a directory for .py files and extracts PostgreSQL table references.

Parses Python scripts to find which tables they read from and write to,
using regex patterns for common pandas, SQLAlchemy, and raw SQL operations.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories to skip when walking
SKIP_DIRS = {"site-packages", "__pycache__", ".venv", "node_modules", "RAG",
             ".git", ".tox", "env", "venv", ".eggs", "dist", "build"}

# Max file size to read (1 MB)
MAX_FILE_SIZE = 1_048_576

# Common false positives - Python modules, SQL keywords, built-in names
FALSE_POSITIVES = {
    # Python standard library / common modules
    "os", "sys", "datetime", "pathlib", "sqlalchemy", "psycopg2", "pandas",
    "concurrent", "pil", "json", "requests", "logging", "re", "math",
    "collections", "functools", "itertools", "typing", "abc", "io",
    "csv", "time", "shutil", "subprocess", "hashlib", "base64",
    "urllib", "http", "email", "html", "xml", "sqlite3", "decimal",
    "random", "string", "textwrap", "copy", "pickle", "gzip",
    "zipfile", "tarfile", "tempfile", "glob", "fnmatch", "stat",
    "argparse", "configparser", "threading", "multiprocessing",
    "asyncio", "socket", "ssl", "select", "signal", "struct",
    "codecs", "unicodedata", "pprint", "traceback", "warnings",
    "contextlib", "importlib", "pkgutil", "unittest", "doctest",
    "numpy", "scipy", "matplotlib", "seaborn", "sklearn",
    "sqlalchemy.engine", "sqlalchemy.orm", "psycopg2.extras",
    # SQL / system keywords
    "information_schema", "pg_catalog", "pg_temp", "pg_toast",
    "dual", "sysibm",
    # Common non-table identifiers
    "public", "dbo", "main", "temp", "tmp",
    # Common false positives from FROM regex matching Python code
    "your", "the", "this", "that", "here", "there", "where", "each",
    "google", "selenium", "webdriver", "selenium.webdriver",
    "aggregatedata", "current_date", "current_timestamp",
    "flask", "django", "fastapi", "uvicorn", "starlette",
    "dotenv", "openpyxl", "xlrd", "xlsxwriter", "boto3",
    "azure", "aws", "paramiko", "fabric", "celery",
}

# Qualified name pattern: schema.table or "schema"."table"
# Matches: schema.table, "schema"."table", schema."table", "schema".table
_IDENT = r'(?:"[^"]+"|[a-zA-Z_]\w*)'
_QUAL_TABLE = rf'({_IDENT}\.{_IDENT})'
_BARE_TABLE = rf'({_IDENT})'

# Combined: prefer qualified, fall back to bare
_TABLE_REF = rf'(?:{_QUAL_TABLE}|{_BARE_TABLE})'


@dataclass
class ScriptResult:
    path: str
    display_name: str
    last_modified: datetime | None
    file_size: int
    tables_read: set[str] = field(default_factory=set)
    tables_written: set[str] = field(default_factory=set)


def _normalize_table(name: str) -> str:
    """Lowercase and strip quotes from a table name."""
    name = name.strip().lower()
    # Strip quotes from each part
    parts = name.split(".")
    parts = [p.strip('"').strip("'").strip("`") for p in parts]
    return ".".join(parts)


def _is_false_positive(table: str) -> bool:
    """Check if a table name is a known false positive."""
    normalized = table.lower().strip('"').strip("'")
    # Check the full name
    if normalized in FALSE_POSITIVES:
        return True
    # Check each component
    for part in normalized.split("."):
        cleaned = part.strip('"').strip("'")
        if cleaned in FALSE_POSITIVES:
            return True
    # Filter out single-word names that look like Python keywords/builtins
    if "." not in normalized and len(normalized) <= 3:
        return True
    # Filter out Python import-style paths (app.module.submodule patterns)
    # These have 3+ dot-separated parts that look like package paths
    parts = normalized.split(".")
    if len(parts) >= 3:
        return True
    # Filter out names starting with common Python package prefixes
    _python_prefixes = {"app", "src", "lib", "test", "tests", "config", "utils",
                        "helpers", "core", "models", "views", "controllers"}
    if parts[0] in _python_prefixes:
        return True
    return False


def _extract_write_tables(content: str) -> set[str]:
    """Extract table names from write operations in the script content."""
    tables = set()

    # to_sql('table_name' or to_sql("table_name"
    for m in re.finditer(r'\.to_sql\s*\(\s*["\']([^"\']+)["\']', content):
        tables.add(_normalize_table(m.group(1)))

    # INSERT INTO [schema.]table
    for m in re.finditer(
        r'INSERT\s+INTO\s+(?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?',
        content, re.IGNORECASE
    ):
        raw = m.group(0)
        table_part = re.sub(r'^INSERT\s+INTO\s+', '', raw, flags=re.IGNORECASE).strip()
        tables.add(_normalize_table(table_part))

    # COPY schema.table FROM (pg COPY import)
    for m in re.finditer(
        r'COPY\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)\s+FROM\b',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # TRUNCATE [TABLE] [schema.]table
    for m in re.finditer(
        r'TRUNCATE\s+(?:TABLE\s+)?((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # REFRESH MATERIALIZED VIEW [schema.]table
    for m in re.finditer(
        r'REFRESH\s+MATERIALIZED\s+VIEW\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # CREATE [OR REPLACE] TABLE / MATERIALIZED VIEW [schema.]table
    for m in re.finditer(
        r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|MATERIALIZED\s+VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # DROP TABLE / MATERIALIZED VIEW [schema.]table
    for m in re.finditer(
        r'DROP\s+(?:TABLE|MATERIALIZED\s+VIEW)\s+(?:IF\s+EXISTS\s+)?((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # Wrapper functions: Write_to_SQL_wo(df, "schema.table"),
    # SQL_insert_loop("schema.table", ...), write_df_to_pg(df, "schema.table"), etc.
    for m in re.finditer(
        r'\b\w*(?:write|insert|load|upload|to_sql)\w*\s*\([^)]{0,300}?["\']([a-zA-Z_]\w*\.[a-zA-Z_]\w*)["\']',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    return {t for t in tables if not _is_false_positive(t)}


def _extract_read_tables(content: str) -> set[str]:
    """Extract table names from read operations in the script content."""
    tables = set()

    # FROM [schema.]table (in SQL context)
    # Exclude Python 'from X import Y' by checking what follows the table name
    for m in re.finditer(
        r'\bFROM\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)\s*',
        content, re.IGNORECASE
    ):
        # Skip if followed by 'import' (Python import statement)
        after = content[m.end():m.end()+10].strip().lower()
        if after.startswith("import"):
            continue
        tables.add(_normalize_table(m.group(1)))

    # JOIN [schema.]table
    for m in re.finditer(
        r'\bJOIN\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # COPY schema.table TO (pg COPY export)
    for m in re.finditer(
        r'COPY\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)\s+TO\b',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # read_sql.*schema.table (pandas read)
    for m in re.finditer(
        r'read_sql[^"\']*["\'][^"\']*\b((?:[a-zA-Z_]\w*)(?:\.[a-zA-Z_]\w*)+)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    return {t for t in tables if not _is_false_positive(t)}


def parse_script(filepath: Path) -> ScriptResult | None:
    """Parse a single Python script and extract table references.

    Returns a ScriptResult or None if the file cannot be read.
    """
    try:
        stat = filepath.stat()
        file_size = stat.st_size

        if file_size > MAX_FILE_SIZE:
            logger.warning("Skipping %s - file too large (%d bytes)", filepath, file_size)
            return None

        if file_size == 0:
            return None

        last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        content = filepath.read_text(encoding="utf-8", errors="replace")

        tables_written = _extract_write_tables(content)
        tables_read = _extract_read_tables(content)

        # Remove read tables that are also in write (avoid double counting for
        # something like INSERT INTO x SELECT FROM x)
        # Actually keep them - a script can both read from and write to the same table
        # But remove tables from reads that only appear because of write statements
        # e.g. INSERT INTO x should not also count x as a read

        return ScriptResult(
            path=str(filepath),
            display_name=filepath.name,
            last_modified=last_modified,
            file_size=file_size,
            tables_read=tables_read,
            tables_written=tables_written,
        )

    except Exception as e:
        logger.warning("Failed to parse %s: %s", filepath, e)
        return None


def walk_scripts(root_path: str, on_progress=None) -> list[ScriptResult]:
    """Walk a directory tree for .py files and parse each one.

    Skips directories in SKIP_DIRS.
    *on_progress* is an optional callback(message: str) for live logging.
    Returns a list of ScriptResult objects (only scripts with table references).
    """
    root = Path(root_path)
    if not root.exists():
        msg = f"Scripts path does not exist: {root}"
        logger.warning(msg)
        if on_progress:
            on_progress(msg)
        return []

    if on_progress:
        on_progress(f"Walking {root} ...")

    results = []
    files_checked = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out skip directories (modifying dirnames in-place)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        py_files = [f for f in filenames if f.endswith(".py")]
        if py_files and on_progress:
            on_progress(f"Scanning {dirpath} ({len(py_files)} .py files)")

        for filename in py_files:
            filepath = Path(dirpath) / filename
            files_checked += 1
            result = parse_script(filepath)
            if result and (result.tables_read or result.tables_written):
                results.append(result)
                if on_progress:
                    tables = len(result.tables_read) + len(result.tables_written)
                    on_progress(f"  Found: {filename} ({tables} table refs)")

    logger.info("Scanned %s - found %d scripts with table references", root, len(results))
    if on_progress:
        on_progress(f"Walk complete: {files_checked} files checked, {len(results)} with table refs")
    return results
