"""
Script scanner - walks a directory for .py files and extracts data I/O references.

Parses Python scripts to find:
- SQL tables they read from and write to (PostgreSQL)
- Excel/CSV files they read from and write to
- Web URLs they scrape or call

References are stored with type prefixes:
- No prefix: SQL table (e.g. "samsung_health.psi_data")
- [excel]: Excel file (e.g. "[excel]output.xlsx")
- [csv]: CSV file (e.g. "[csv]data.csv")
- [web]: Web scraping/API (e.g. "[web]amazon.ae")
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

# ── False positive filtering for SQL table names ──
# Only used for SQL table detection (FROM/JOIN/etc). File and URL refs
# are handled by their own specific patterns and don't need this.
_SQL_FALSE_POSITIVES = {
    # SQL keywords and functions
    "information_schema", "pg_catalog", "pg_temp", "pg_toast",
    "dual", "sysibm", "stdin", "stdout", "generate_series",
    "unnest", "lateral", "current_date", "current_timestamp",
    "now", "coalesce", "nullif", "cast", "extract", "interval",
    # SQL structural keywords that appear after FROM/JOIN
    "select", "where", "group", "order", "having", "limit", "offset",
    "union", "except", "intersect", "exists", "between", "like",
    "case", "when", "then", "else", "end", "null", "true", "false",
    "left", "right", "inner", "outer", "cross", "full", "natural",
    "table", "tables", "column", "columns", "row", "rows",
    "value", "values", "record", "records", "item", "items",
    "index", "constraint", "primary", "foreign", "unique", "check",
    "default", "cascade", "restrict", "references",
    # CTE / subquery aliases
    "base", "staging", "model", "shared", "source", "result", "results",
    "data", "output", "input", "final", "raw", "clean", "cleaned",
    "combined", "merged", "filtered", "transformed", "processed",
    "query", "subquery", "cte", "pivot", "unpivot", "temp", "tmp",
    # Common non-table identifiers
    "public", "dbo", "main", "keep", "external",
    # English words that appear in log/comment strings containing SQL keywords
    "your", "the", "this", "that", "here", "there", "where", "each",
    "their", "charts", "chart", "file", "files", "page", "pages",
    "text", "name", "type", "user", "users", "date", "count",
    "all", "any", "some", "many", "other", "others", "new", "old",
    "first", "last", "next", "previous", "above", "below",
    "start", "stop", "begin", "end", "open", "close",
    "list", "dict", "set", "map", "array", "object", "buffer",
    "error", "errors", "exception", "message", "messages",
    "status", "state", "event", "events", "action", "actions",
    "config", "settings", "options", "params", "args",
    "response", "request", "client", "server", "host", "port",
    "path", "url", "uri", "link", "href", "content",
    "competing", "promoter", "clipboard", "document", "window",
    "header", "footer", "body", "title", "label", "description",
    "field", "fields", "form", "forms", "view", "views",
    "report", "reports", "script", "scripts", "task", "tasks",
    "process", "thread", "worker", "job", "queue", "batch",
    "context", "session", "token", "key", "secret", "password",
    "email", "phone", "address", "city", "country", "region",
    "category", "tag", "tags", "group", "groups", "role", "roles",
    "format", "encoding", "charset", "locale", "timezone",
    "image", "images", "icon", "logo", "photo", "video", "audio",
    "size", "width", "height", "length", "depth", "weight",
    "color", "font", "style", "theme", "layout", "grid",
    "button", "click", "submit", "cancel", "save", "delete", "update",
    "total", "average", "minimum", "maximum", "summary", "detail",
    "success", "failure", "warning", "info", "debug", "trace",
    "true", "false", "none", "undefined", "nan", "inf",
    "slides", "slide",
    # Python modules and packages
    "os", "sys", "datetime", "pathlib", "sqlalchemy", "psycopg2", "pandas",
    "concurrent", "pil", "json", "requests", "logging", "math",
    "collections", "functools", "itertools", "typing", "abc",
    "csv", "time", "shutil", "subprocess", "hashlib", "base64",
    "urllib", "urllib3", "http", "html", "xml", "sqlite3", "decimal",
    "random", "string", "textwrap", "copy", "pickle", "gzip",
    "zipfile", "tarfile", "tempfile", "glob", "fnmatch", "stat",
    "argparse", "configparser", "threading", "multiprocessing",
    "asyncio", "socket", "ssl", "select", "signal", "struct",
    "codecs", "unicodedata", "pprint", "traceback", "warnings",
    "contextlib", "importlib", "pkgutil", "unittest", "doctest",
    "numpy", "scipy", "matplotlib", "seaborn", "sklearn",
    "google", "selenium", "webdriver", "beautifulsoup",
    "flask", "django", "fastapi", "uvicorn", "starlette",
    "dotenv", "openpyxl", "xlrd", "xlsxwriter", "boto3",
    "azure", "aws", "paramiko", "fabric", "celery",
    "lxml", "pypac", "qrcode", "win32com",
    # C / system identifiers
    "stdint", "stdin", "stdout", "stderr",
}


def _is_sql_false_positive(table: str) -> bool:
    """Check if a SQL table name candidate is a false positive."""
    normalized = table.lower().strip('"').strip("'")

    # Direct match
    if normalized in _SQL_FALSE_POSITIVES:
        return True

    parts = normalized.split(".")

    # Check each component
    for part in parts:
        cleaned = part.strip('"').strip("'")
        if cleaned in _SQL_FALSE_POSITIVES:
            return True

    # Single-word names <= 3 chars are almost never real tables
    if "." not in normalized and len(normalized) <= 3:
        return True

    # 3+ dot-separated parts are Python import paths, not SQL
    if len(parts) >= 3:
        return True

    # Common Python package prefixes
    _python_prefixes = {"app", "src", "lib", "test", "tests", "config", "utils",
                        "helpers", "core", "models", "views", "controllers"}
    if parts[0] in _python_prefixes:
        return True

    # Unqualified names (no dot) without underscores are almost always
    # English words or variable names, not SQL tables.
    # Real PostgreSQL tables use underscores: fact_sales, dim_customer, etc.
    if "." not in normalized and "_" not in normalized and len(normalized) < 15:
        return True

    return False


# ── SQL detection helpers ──

_SQL_INDICATORS = re.compile(
    r'\b(SELECT|INSERT|UPDATE|DELETE|WITH|COPY|REFRESH|CREATE|DROP|TRUNCATE)\b',
    re.IGNORECASE
)

_STRING_LITERAL = re.compile(
    r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"\n]*)"|\'([^\'\n]*)\'',
    re.DOTALL
)


def _extract_sql_strings(content: str) -> list[str]:
    """Extract string literals that contain SQL keywords."""
    sql_strings = []
    for m in _STRING_LITERAL.finditer(content):
        text = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
        if text and _SQL_INDICATORS.search(text):
            sql_strings.append(text)
    return sql_strings


def _extract_cte_names(sql_text: str) -> set[str]:
    """Extract CTE names from WITH...AS patterns."""
    names = set()
    for m in re.finditer(r'\bWITH\s+(\w+)\s+AS\s*\(', sql_text, re.IGNORECASE):
        names.add(m.group(1).lower())
    for m in re.finditer(r',\s*(\w+)\s+AS\s*\(', sql_text, re.IGNORECASE):
        names.add(m.group(1).lower())
    return names


def _normalize_table(name: str) -> str:
    """Lowercase and strip quotes from a table name."""
    name = name.strip().lower()
    parts = name.split(".")
    parts = [p.strip('"').strip("'").strip("`") for p in parts]
    return ".".join(parts)


def _resolve_table_variables(content: str) -> dict[str, str]:
    """Find variable assignments that look like table name definitions."""
    var_map = {}
    for m in re.finditer(
        r'\b(table_name|sql_[Tt]able|sql_actual_table_name|actual_table_name|'
        r'target_table|dest_table|tbl_name|tbl|sql_table_name)\s*=\s*'
        r'["\']([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)["\']',
        content
    ):
        var_map[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'\b(schema|sql_schema|actual_schema|target_schema)\s*=\s*'
        r'["\']([a-zA-Z_]\w*)["\']',
        content
    ):
        var_map[m.group(1)] = m.group(2)
    return var_map


# ── SQL table extraction ──

def _extract_to_sql_targets(content: str) -> set[str]:
    """Extract table names from to_sql() calls."""
    tables = set()
    var_map = _resolve_table_variables(content)
    for m in re.finditer(r'\.to_sql\s*\(', content):
        call_text = content[m.end():m.end() + 500]
        name_m = re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', call_text)
        if not name_m:
            name_m = re.match(r'\s*["\']([^"\']+)["\']', call_text)
        if name_m:
            table_name = name_m.group(1)
            schema_m = re.search(r'\bschema\s*=\s*["\']([^"\']+)["\']', call_text)
            if schema_m:
                tables.add(_normalize_table(f"{schema_m.group(1)}.{table_name}"))
            else:
                tables.add(_normalize_table(table_name))
        else:
            name_var_m = re.search(r'\bname\s*=\s*([a-zA-Z_]\w*)', call_text)
            if not name_var_m:
                name_var_m = re.match(r'\s*([a-zA-Z_]\w*)\s*,', call_text)
            if name_var_m:
                var_name = name_var_m.group(1)
                if var_name in var_map:
                    table_name = var_map[var_name]
                    schema_m = re.search(r'\bschema\s*=\s*["\']([^"\']+)["\']', call_text)
                    if not schema_m:
                        schema_var_m = re.search(r'\bschema\s*=\s*([a-zA-Z_]\w*)', call_text)
                        if schema_var_m and schema_var_m.group(1) in var_map:
                            schema_val = var_map[schema_var_m.group(1)]
                            tables.add(_normalize_table(f"{schema_val}.{table_name}"))
                        else:
                            tables.add(_normalize_table(table_name))
                    else:
                        tables.add(_normalize_table(f"{schema_m.group(1)}.{table_name}"))
    return tables


def _extract_write_tables(content: str) -> set[str]:
    """Extract SQL table names from write operations."""
    tables = set()

    tables |= _extract_to_sql_targets(content)

    # INSERT INTO [schema.]table
    for m in re.finditer(
        r'INSERT\s+INTO\s+(?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?',
        content, re.IGNORECASE
    ):
        raw = m.group(0)
        table_part = re.sub(r'^INSERT\s+INTO\s+', '', raw, flags=re.IGNORECASE).strip()
        tables.add(_normalize_table(table_part))

    # COPY schema.table FROM
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

    # REFRESH MATERIALIZED VIEW
    for m in re.finditer(
        r'REFRESH\s+MATERIALIZED\s+VIEW\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # CREATE [OR REPLACE] TABLE / MATERIALIZED VIEW
    for m in re.finditer(
        r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|MATERIALIZED\s+VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # DROP TABLE / MATERIALIZED VIEW
    for m in re.finditer(
        r'DROP\s+(?:TABLE|MATERIALIZED\s+VIEW)\s+(?:IF\s+EXISTS\s+)?((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # Wrapper functions with qualified table: Write_to_SQL(df, "schema.table")
    for m in re.finditer(
        r'\b\w*(?:write|insert|load|upload|to_sql)\w*\s*\([^)]{0,300}?["\']([a-zA-Z_]\w*\.[a-zA-Z_]\w*)["\']',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # Wrapper functions with bare table: SQL_insert_loop(path, "channel_mappings")
    for m in re.finditer(
        r'\b\w*(?:write|insert|load|upload)\w*\s*\([^)]{0,300}?["\']([a-zA-Z_]\w{3,})["\']',
        content, re.IGNORECASE
    ):
        candidate = m.group(1)
        if not re.search(r'[/\\:]', candidate):
            tables.add(_normalize_table(candidate))

    # f-string SQL variable resolution
    var_map = _resolve_table_variables(content)
    for m in re.finditer(
        r'(?:COPY|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|INSERT\s+INTO)\s+\{(\w+)\}',
        content, re.IGNORECASE
    ):
        var_name = m.group(1)
        if var_name in var_map:
            tables.add(_normalize_table(var_map[var_name]))

    # write_df_to_pg(df, schema_var, table_var) variable resolution
    for m in re.finditer(
        r'\bwrite_df_to_pg\s*\(\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)',
        content
    ):
        schema_var, table_var = m.group(1), m.group(2)
        if schema_var in var_map and table_var in var_map:
            tables.add(_normalize_table(f"{var_map[schema_var]}.{var_map[table_var]}"))
        elif table_var in var_map:
            tables.add(_normalize_table(var_map[table_var]))

    # copy_expert with COPY FROM STDIN
    if 'copy_expert' in content:
        for m in re.finditer(
            r'COPY\s+((?:[a-zA-Z_]\w*\.)?[a-zA-Z_]\w+)\s+FROM\s+STDIN',
            content, re.IGNORECASE
        ):
            tables.add(_normalize_table(m.group(1)))

    return {t for t in tables if not _is_sql_false_positive(t)}


def _extract_read_tables(content: str) -> set[str]:
    """Extract SQL table names from read operations."""
    tables = set()
    cte_names = set()

    for sql_text in _extract_sql_strings(content):
        cte_names |= _extract_cte_names(sql_text)

        for m in re.finditer(
            r'\bFROM\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)\s*',
            sql_text, re.IGNORECASE
        ):
            tables.add(_normalize_table(m.group(1)))

        for m in re.finditer(
            r'\bJOIN\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)',
            sql_text, re.IGNORECASE
        ):
            tables.add(_normalize_table(m.group(1)))

    # COPY...TO
    for m in re.finditer(
        r'COPY\s+((?:"[^"]+"|[a-zA-Z_]\w*)(?:\.(?:"[^"]+"|[a-zA-Z_]\w*))?)\s+TO\b',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # read_sql / read_sql_query
    for m in re.finditer(
        r'read_sql(?:_query|_table)?\s*\([^)]*["\'][^"\']*\b((?:[a-zA-Z_]\w*)(?:\.[a-zA-Z_]\w*)+)',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    # pd.read_sql_table("table_name")
    for m in re.finditer(
        r'read_sql_table\s*\(\s*["\']([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)["\']',
        content, re.IGNORECASE
    ):
        tables.add(_normalize_table(m.group(1)))

    tables -= cte_names

    return {t for t in tables if not _is_sql_false_positive(t)}


# ── File I/O detection ──

def _extract_filename(raw: str) -> str:
    """Extract just the filename from a path string."""
    # Handle Windows and Unix paths
    name = raw.replace("\\", "/").split("/")[-1]
    # Strip f-string braces
    name = re.sub(r'\{[^}]*\}', '*', name)
    return name


def _extract_file_writes(content: str) -> set[str]:
    """Detect Excel and CSV file write operations."""
    files = set()

    # .to_excel("path/file.xlsx")
    for m in re.finditer(
        r'\.to_excel\s*\(\s*(?:f)?["\']([^"\']+)["\']',
        content
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[excel]{fname}")

    # ExcelWriter("path/file.xlsx")
    for m in re.finditer(
        r'ExcelWriter\s*\(\s*(?:f)?["\']([^"\']+)["\']',
        content
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[excel]{fname}")

    # Workbook().save / workbook.save("file.xlsx")
    for m in re.finditer(
        r'\.save\s*\(\s*(?:f)?["\']([^"\']*\.xlsx?)["\']',
        content, re.IGNORECASE
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[excel]{fname}")

    # .to_csv("path/file.csv") - but NOT .to_csv(buffer) for COPY operations
    for m in re.finditer(
        r'\.to_csv\s*\(\s*(?:f)?["\']([^"\']+)["\']',
        content
    ):
        path = m.group(1)
        # Skip if it's a StringIO buffer pattern (used for COPY to SQL)
        if 'buffer' not in path.lower() and 'stringio' not in path.lower():
            fname = _extract_filename(path)
            if fname.endswith('.csv') or '/' in path or '\\' in path:
                files.add(f"[csv]{fname}")

    return files


def _extract_file_reads(content: str) -> set[str]:
    """Detect Excel, CSV, and PDF file read operations."""
    files = set()

    # pd.read_excel("path/file.xlsx")
    for m in re.finditer(
        r'read_excel\s*\(\s*(?:f)?["\']([^"\']+)["\']',
        content
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[excel]{fname}")

    # pd.read_excel(variable) - just mark as excel read
    if re.search(r'read_excel\s*\(', content) and '[excel]' not in ''.join(files):
        files.add("[excel]Excel files")

    # Workbooks.Open("file.xlsx") - COM automation
    for m in re.finditer(
        r'Workbooks\.Open\s*\(\s*(?:f)?(?:r)?["\']([^"\']+)["\']',
        content, re.IGNORECASE
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[excel]{fname}")

    # Workbooks.Open(variable) - COM automation with variable path
    if re.search(r'Workbooks\.Open\s*\(', content, re.IGNORECASE) and not any('[excel]' in f and f != '[excel]Excel files' for f in files):
        files.add("[excel]Excel files")

    # load_workbook("file.xlsx")
    for m in re.finditer(
        r'load_workbook\s*\(\s*(?:f)?["\']([^"\']+)["\']',
        content
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[excel]{fname}")

    # pd.read_csv("path/file.csv")
    for m in re.finditer(
        r'read_csv\s*\(\s*(?:f)?(?:r)?["\']([^"\']+)["\']',
        content
    ):
        fname = _extract_filename(m.group(1))
        files.add(f"[csv]{fname}")

    # pd.read_csv(variable) - just mark as csv read
    if re.search(r'read_csv\s*\(', content) and not any(f.startswith('[csv]') for f in files):
        files.add("[csv]CSV files")

    # PDF readers
    if re.search(r'PdfReader|PdfFileReader|fitz\.open|pdfplumber', content):
        files.add("[pdf]PDF files")

    return files


def _extract_url_reads(content: str) -> set[str]:
    """Detect web scraping and API call operations."""
    urls = set()

    # requests.get/post with URL string
    for m in re.finditer(
        r'requests\.(?:get|post)\s*\(\s*(?:f)?["\']https?://([^"\'/?]+)',
        content
    ):
        domain = m.group(1)
        urls.add(f"[web]{domain}")

    # BeautifulSoup usage (web scraping indicator)
    if re.search(r'BeautifulSoup\s*\(', content):
        if not urls:
            urls.add("[web]Web scraping")

    # Selenium WebDriver usage
    if re.search(r'webdriver\.\w+\s*\(|WebDriver\s*\(', content):
        if not urls:
            urls.add("[web]Web scraping (Selenium)")

    # get_content() or scrape_ functions with URLs
    for m in re.finditer(
        r'(?:get_content|scrape_\w+|fetch_\w+)\s*\(\s*(?:f)?["\']https?://([^"\'/?]+)',
        content
    ):
        domain = m.group(1)
        urls.add(f"[web]{domain}")

    return urls


# ── Main data class and parsing ──

@dataclass
class ScriptResult:
    path: str
    display_name: str
    last_modified: datetime | None
    file_size: int
    tables_read: set[str] = field(default_factory=set)
    tables_written: set[str] = field(default_factory=set)
    files_read: set[str] = field(default_factory=set)
    files_written: set[str] = field(default_factory=set)
    urls_read: set[str] = field(default_factory=set)


def parse_script(filepath: Path) -> ScriptResult | None:
    """Parse a Python script and extract all data I/O references."""
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
        files_written = _extract_file_writes(content)
        files_read = _extract_file_reads(content)
        urls_read = _extract_url_reads(content)

        return ScriptResult(
            path=str(filepath),
            display_name=filepath.name,
            last_modified=last_modified,
            file_size=file_size,
            tables_read=tables_read,
            tables_written=tables_written,
            files_read=files_read,
            files_written=files_written,
            urls_read=urls_read,
        )

    except Exception as e:
        logger.warning("Failed to parse %s: %s", filepath, e)
        return None


def walk_scripts(root_path: str, on_progress=None) -> list[ScriptResult]:
    """Walk a directory tree for .py files and parse each one.

    Returns scripts with ANY detected I/O references (SQL, file, or web).
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
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        py_files = [f for f in filenames if f.endswith(".py")]
        if py_files and on_progress:
            on_progress(f"Scanning {dirpath} ({len(py_files)} .py files)")

        for filename in py_files:
            filepath = Path(dirpath) / filename
            files_checked += 1
            result = parse_script(filepath)
            if result and _has_any_refs(result):
                results.append(result)
                if on_progress:
                    total = (len(result.tables_read) + len(result.tables_written)
                             + len(result.files_read) + len(result.files_written)
                             + len(result.urls_read))
                    on_progress(f"  Found: {filename} ({total} refs)")

    logger.info("Scanned %s - found %d scripts with data refs", root, len(results))
    if on_progress:
        on_progress(f"Walk complete: {files_checked} files checked, {len(results)} with data refs")
    return results


def _has_any_refs(result: ScriptResult) -> bool:
    """Check if a script result has any detected references."""
    return bool(result.tables_read or result.tables_written
                or result.files_read or result.files_written
                or result.urls_read)
