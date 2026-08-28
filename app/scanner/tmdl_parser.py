"""
TMDL table file parser.

Parses .tmdl files from Power BI semantic model exports to extract:
- Table name
- Partition M expression
- Source type (csv, excel, sql)
- Source details (file path, server, database, etc.)
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.source_identity import normalize_server


@dataclass
class SourceInfo:
    """Extracted data source information from a TMDL table."""
    source_type: str  # "csv", "excel", "sql", "unknown"
    file_path: str | None = None       # for csv/excel
    server: str | None = None          # for sql
    database: str | None = None        # for sql
    sql_table: str | None = None       # for sql (schema.table)
    sql_query: str | None = None       # for sql (native query)
    sheet_or_table: str | None = None  # for excel (sheet/table name)
    delimiter: str | None = None       # for csv
    raw_expression: str = ""           # the full M expression

    # Source types that are database connections
    DB_TYPES = {"sql", "postgresql", "mysql", "oracle", "odbc", "oledb", "ssas", "redshift", "snowflake", "bigquery"}
    # Source types that use file paths
    FILE_TYPES = {"excel", "sharepoint", "web"}

    @property
    def unresolved_fingerprint(self) -> str:
        """Return a stable, non-reversible label for an unresolved query source.

        A PostgreSQL connector expression without a resolved relation must not
        collapse into every other query on the same server/database.  Keep the
        raw expression out of names and keys while retaining a deterministic
        identity for repeat scans.
        """
        material = "\0".join(
            (
                normalize_server(self.server),
                (self.database or "").strip(),
                self.raw_expression or self.sql_query or "postgresql-source",
            )
        )
        return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:12]

    @property
    def connection_key(self) -> str:
        """Unique key to identify this source for deduplication.

        Database sources are deduplicated at the table level
        (same server + database + table = same source).
        """
        if self.source_type in self.FILE_TYPES and self.file_path:
            return f"{self.source_type}::{self.file_path.lower()}"
        elif self.source_type in self.DB_TYPES and self.server:
            if self.source_type == "postgresql":
                # PostgreSQL hosts are case-insensitive, but quoted database,
                # schema, and relation spelling is part of the physical
                # identity and must survive source deduplication unchanged.
                parts = [self.source_type, normalize_server(self.server)]
                if self.database:
                    parts.append(self.database.strip())
                if self.sql_table:
                    parts.append(self.sql_table.strip())
                else:
                    parts.append(f"unresolved-query-{self.unresolved_fingerprint}")
                return "::".join(parts)
            parts = [self.source_type, self.server.lower()]
            if self.database:
                parts.append(self.database.lower())
            if self.sql_table:
                parts.append(self.sql_table.lower())
            return "::".join(parts)
        return f"unknown::{self.raw_expression[:100]}"

    @property
    def display_name(self) -> str:
        """Human-readable name for this source."""
        if self.source_type in self.FILE_TYPES and self.file_path:
            return Path(self.file_path).name
        elif self.source_type in self.DB_TYPES and self.server:
            # For PostgreSQL, just show schema.table (skip server IP and database name)
            if self.source_type == "postgresql":
                if self.sql_table:
                    return _clean_identifier(self.sql_table)
                # This is intentionally a clean identifier: runner cleanup
                # must retain the distinct unresolved source so the UI can
                # diagnose it, rather than merging or archiving it by name.
                return f"unresolved_pg_query_{self.unresolved_fingerprint}"
            parts = []
            if self.database:
                parts.append(_clean_identifier(self.database))
            if self.sql_table:
                parts.append(_clean_identifier(self.sql_table))
            if parts:
                return f"{_clean_identifier(self.server)}/{'/'.join(parts)}"
            return _clean_identifier(self.server)
        return "Unknown Source"

    @property
    def connection_info(self) -> str:
        """Connection string or path for storage."""
        if self.source_type in self.FILE_TYPES and self.file_path:
            return self.file_path
        elif self.source_type in self.DB_TYPES:
            parts = [_clean_identifier(self.server) or "?"]
            if self.database:
                parts.append(_clean_identifier(self.database))
            if self.sql_table:
                parts.append(_clean_identifier(self.sql_table))
            return "/".join(parts)
        return ""


@dataclass
class ParsedTable:
    """A parsed table from a TMDL file."""
    table_name: str
    columns: list[str] = field(default_factory=list)
    measures: list[tuple[str, str | None]] = field(default_factory=list)  # [(name, dax)]
    partition_name: str | None = None
    mode: str | None = None  # "import" or "directQuery"
    m_expression: str | None = None
    source: SourceInfo | None = None
    file_path: str = ""  # path to the .tmdl file
    is_metadata: bool = False  # True for Business Owner / Report Owner tables
    metadata_value: str | None = None  # The extracted owner name


# Tables that contain report metadata (not data sources)
METADATA_TABLES = {"Business Owner", "Report Owner"}

# Prefixes for Power BI auto-generated internal tables (not real data)
_AUTO_TABLE_PREFIXES = (
    "LocalDateTable_",
    "DateTableTemplate_",
    "LocalDate_",
)


def is_auto_table(name: str) -> bool:
    """Return True if this table name is a Power BI auto-generated internal table."""
    return name.startswith(_AUTO_TABLE_PREFIXES)


def path_has_file_extension(path: str | None) -> bool:
    """Return True when the final path segment looks like a file."""
    if not path:
        return False
    clean = str(path).strip().strip('"').rstrip("\\/")
    if not clean:
        return False
    return bool(PureWindowsPath(clean).suffix or PurePosixPath(clean.replace("\\", "/")).suffix)


# A drive-letter path under another user's Windows profile. Analysts register
# reports whose connections point at their own Downloads/Desktop folders;
# the server's service account can never see those, so probing them is noise
# rather than a data-freshness signal.
LOCAL_USER_PATH = re.compile(r"^[A-Za-z]:[\\/]Users[\\/](?!Public[\\/])[^\\/]+[\\/]", re.IGNORECASE)


def is_folder_like_file_source(source: "SourceInfo | None") -> bool:
    """Return True for folder paths used to combine files."""
    if source is None:
        return False
    expr = source.raw_expression or ""
    if re.search(r'Folder\.Files\s*\(', expr):
        return True
    if source.source_type in {"csv", "excel", "folder", "file"} and source.file_path:
        return not path_has_file_extension(source.file_path)
    return False


def parse_tmdl_file(file_path: str | Path) -> ParsedTable | None:
    """Parse a single .tmdl table file and extract source information."""
    file_path = Path(file_path)
    if not file_path.exists():
        return None

    text = file_path.read_text(encoding="utf-8-sig")  # handle BOM
    lines = text.splitlines()

    if not lines:
        return None

    table_name = _extract_table_name(lines)
    if not table_name:
        return None

    columns = _extract_columns(lines)
    measures = _extract_measures(lines)
    partition_name, mode, m_expression = _extract_partition(lines)

    # Check if this is a metadata table (Business Owner / Report Owner)
    is_metadata = table_name in METADATA_TABLES
    metadata_value = None
    source = None

    if m_expression:
        if is_metadata:
            metadata_value = _extract_hashtable_value(m_expression)
        else:
            source = _parse_m_expression(m_expression)

    return ParsedTable(
        table_name=table_name,
        columns=columns,
        measures=measures,
        partition_name=partition_name,
        mode=mode,
        m_expression=m_expression,
        source=source,
        file_path=str(file_path),
        is_metadata=is_metadata,
        metadata_value=metadata_value,
    )


def parse_expressions_file(file_path: str | Path) -> dict[str, str]:
    """Parse expressions.tmdl to extract named parameters.

    Returns a dict of {parameter_name: value}.
    e.g. {"Server": "localhost", "Database": "Contoso"}
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return {}

    text = file_path.read_text(encoding="utf-8-sig")
    params = {}

    # Match: expression Name = "value" meta [...]
    # or:   expression Name = value meta [...]
    for match in re.finditer(
        r'^expression\s+(\S+)\s*=\s*"([^"]*)"',
        text,
        re.MULTILINE,
    ):
        params[match.group(1)] = match.group(2)

    return params


def _extract_table_name(lines: list[str]) -> str | None:
    """Extract table name from the first line."""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("table "):
            name = stripped[6:].strip()
            # Remove single quotes if present
            if name.startswith("'") and name.endswith("'"):
                name = name[1:-1]
            return name
    return None


def _extract_columns(lines: list[str]) -> list[str]:
    """Extract column names from the TMDL file."""
    columns = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("column "):
            col_name = stripped[7:].strip()
            if col_name.startswith("'") and col_name.endswith("'"):
                col_name = col_name[1:-1]
            columns.append(col_name)
    return columns


def _extract_measures(lines: list[str]) -> list[tuple[str, str | None]]:
    """Extract measure names and DAX expressions from the TMDL file.

    Returns list of (measure_name, dax_expression) tuples.
    """
    measures = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("measure "):
            rest = stripped[8:].strip()
            # Remove quotes: measure 'Total Sales' = SUM(...)
            if rest.startswith("'"):
                end_quote = rest.find("'", 1)
                if end_quote > 0:
                    name = rest[1:end_quote]
                    dax = rest[end_quote + 1:].lstrip(" =").strip() or None
                else:
                    name = rest[1:]
                    dax = None
            else:
                # measure TotalSales = SUM(...)
                parts = rest.split("=", 1)
                name = parts[0].strip()
                dax = parts[1].strip() if len(parts) > 1 else None
            measures.append((name, dax))
    return measures


def _extract_partition(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    """Extract partition name, mode, and M expression.

    Returns (partition_name, mode, m_expression).
    """
    partition_name = None
    mode = None
    m_expression = None
    in_partition = False
    in_source = False
    source_lines = []
    source_indent = None
    backtick_mode = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect partition declaration
        if re.match(r"^\t?partition\s+", line.lstrip("\t")) and "= m" in stripped:
            name_part = stripped.split("=")[0].replace("partition", "").strip()
            if name_part.startswith("'") and name_part.endswith("'"):
                name_part = name_part[1:-1]
            partition_name = name_part
            in_partition = True
            continue

        if not in_partition:
            continue

        # Inside partition block
        if stripped.startswith("mode:"):
            mode = stripped.split(":", 1)[1].strip()
            continue

        # Detect source = (start of M expression)
        if stripped.startswith("source") and "=" in stripped:
            after_eq = stripped.split("=", 1)[1].strip()

            # Check for triple-backtick mode
            if after_eq.startswith("```"):
                backtick_mode = True
                # Content after ``` on the same line
                rest = after_eq[3:].strip()
                if rest:
                    source_lines.append(rest)
                in_source = True
                continue
            elif after_eq:
                # Inline expression (single line)
                source_lines.append(after_eq)
                in_source = True
                continue
            else:
                # Multi-line expression starts on next line
                in_source = True
                continue

        if in_source:
            if backtick_mode:
                # In backtick mode, read until closing ```
                if stripped.rstrip().endswith("```") and len(stripped.rstrip()) >= 3:
                    # Don't include the closing backticks
                    before_close = stripped.rstrip()[:-3].strip()
                    if before_close:
                        source_lines.append(before_close)
                    break
                source_lines.append(stripped)
            else:
                # In indentation mode: the M expression is indented deeper
                # than the partition properties. It ends when we hit a line
                # at the partition property level or higher.
                raw_tabs = len(line) - len(line.lstrip("\t"))

                if source_indent is None and stripped:
                    source_indent = raw_tabs

                # If we hit a non-empty line at a lower indent, we're done
                if stripped and source_indent is not None and raw_tabs < source_indent:
                    # Check if this is still part of the M expression
                    # (annotation, next column, etc. means we're done)
                    if stripped.startswith("annotation") or stripped.startswith("column") or stripped.startswith("partition") or stripped.startswith("table"):
                        break
                    # Could be a continuation at different indent
                    if raw_tabs <= 1:
                        break

                if stripped:
                    source_lines.append(stripped)

    if source_lines:
        m_expression = "\n".join(source_lines)

    return partition_name, mode, m_expression


def _extract_hashtable_value(expr: str) -> str | None:
    """Extract the value from a #table expression like #table({"Col"}, {{"Value"}})."""
    match = re.search(r'#table\s*\(\s*\{[^}]*\}\s*,\s*\{\s*\{\s*"([^"]+)"', expr)
    if match:
        return match.group(1)
    return None


def _parse_m_expression(expr: str) -> SourceInfo:
    """Parse a Power Query M expression to extract source details."""
    source = SourceInfo(source_type="unknown", raw_expression=expr)

    # Detect CSV source: Csv.Document(File.Contents("path"), ...)
    if re.search(r'Csv\.Document\s*\(', expr):
        source.source_type = "excel"
        file_match = re.search(r'File\.Contents\s*\(\s*"([^"]+)"', expr)
        if file_match:
            source.file_path = file_match.group(1)
        delim_match = re.search(r'Delimiter\s*=\s*"([^"]*)"', expr)
        if delim_match:
            source.delimiter = delim_match.group(1)
        return source

    # Detect Excel source: Excel.Workbook(File.Contents("path"), ...)
    if re.search(r'Excel\.Workbook\s*\(', expr):
        source.source_type = "excel"
        file_match = re.search(r'File\.Contents\s*\(\s*"([^"]+)"', expr)
        if file_match:
            source.file_path = file_match.group(1)
        sheet_match = re.search(r'Item\s*=\s*"([^"]+)"', expr)
        if sheet_match:
            source.sheet_or_table = sheet_match.group(1)
        return source

    # Detect database sources — all follow the pattern: Function("server", "database", ...)
    # Each connector has a different function name but same argument structure
    DB_CONNECTORS = [
        (r'Sql\.Database\s*\(', "Sql.Database", "sql"),
        (r'Sql\.Databases\s*\(', "Sql.Databases", "sql"),
        (r'PostgreSQL\.Database\s*\(', "PostgreSQL.Database", "postgresql"),
        (r'MySQL\.Database\s*\(', "MySQL.Database", "mysql"),
        (r'Oracle\.Database\s*\(', "Oracle.Database", "oracle"),
        (r'Odbc\.DataSource\s*\(', "Odbc.DataSource", "odbc"),
        (r'OleDb\.DataSource\s*\(', "OleDb.DataSource", "oledb"),
        (r'AnalysisServices\.Database\s*\(', "AnalysisServices.Database", "ssas"),
        (r'AmazonRedshift\.Database\s*\(', "AmazonRedshift.Database", "redshift"),
        (r'Snowflake\.Databases\s*\(', "Snowflake.Databases", "snowflake"),
        (r'GoogleBigQuery\.Database\s*\(', "GoogleBigQuery.Database", "bigquery"),
    ]

    for pattern, func_name, source_type in DB_CONNECTORS:
        if re.search(pattern, expr):
            source.source_type = source_type
            args = _extract_function_args(expr, func_name)
            if args and len(args) >= 1:
                source.server = _unquote(args[0])
            if args and len(args) >= 2:
                source.database = _unquote(args[1])
            # Power Query M escapes a quote inside a string as ``""`` (not
            # with a backslash). Decode the M literal before parsing SQL so a
            # query such as schema_.""table_name"" retains its real relation.
            source.sql_query = _extract_m_query(expr)
            # Extract the specific table being accessed
            source.sql_table = _extract_table_navigation(
                expr,
                decoded_sql=source.sql_query,
                source_type=source_type,
            )
            return source

    # Detect SharePoint sources
    if re.search(r'SharePoint\.Files\s*\(', expr) or re.search(r'SharePoint\.Tables\s*\(', expr):
        source.source_type = "sharepoint"
        url_match = re.search(r'SharePoint\.\w+\s*\(\s*"([^"]+)"', expr)
        if url_match:
            source.file_path = url_match.group(1)
        return source

    # Detect Web sources
    if re.search(r'Web\.Contents\s*\(', expr) or re.search(r'Web\.Page\s*\(', expr):
        source.source_type = "web"
        url_match = re.search(r'Web\.\w+\s*\(\s*"([^"]+)"', expr)
        if url_match:
            source.file_path = url_match.group(1)
        return source

    # Detect folder sources (loads files from a directory)
    if re.search(r'Folder\.Files\s*\(', expr):
        source.source_type = "excel"
        path_match = re.search(r'Folder\.Files\s*\(\s*"([^"]+)"', expr)
        if path_match:
            source.file_path = path_match.group(1)
        return source

    # Detect calculated/internal tables — not real external sources
    # #table() literal, Table.FromRows, Table.FromList, Table.FromColumns, {record} syntax
    if re.search(r'#table\s*\(', expr) or re.search(r'Table\.From(Rows|List|Columns|Records)\s*\(', expr):
        source.source_type = "calculated"
        return source

    # Date scaffolding functions (auto-generated date tables)
    if re.search(r'#date\s*\(|#datetime\s*\(|List\.Dates\s*\(|List\.DateTimes\s*\(|Calendar\s*\(', expr):
        source.source_type = "calculated"
        return source

    # Literal record/list expressions
    if expr.strip().startswith("{") or expr.strip().startswith("#"):
        source.source_type = "calculated"
        return source

    # If we still can't identify it, log the first 200 chars for debugging
    _log_unknown_expression(expr)

    return source


def _log_unknown_expression(expr: str):
    """Log unrecognized M expressions for debugging."""
    import logging
    logger = logging.getLogger(__name__)
    # Find the first function call pattern to help identify what it is
    func_match = re.search(r'(\w+\.\w+)\s*\(', expr)
    if func_match:
        logger.warning("Unknown source type — function: %s | expression: %.200s", func_match.group(1), expr)
    else:
        logger.warning("Unknown source type — no function found | expression: %.200s", expr)


# A clean table identifier: starts with letter or underscore, then word chars.
# Optionally prefixed with a schema in the same shape.
_CLEAN_TABLE_RE = re.compile(r'^[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?$')
_QUOTED_COMPONENT = r'"(?:[^"]|"")+"'
_QUOTED_TABLE_RE = re.compile(
    rf'^(?:{_QUOTED_COMPONENT}|[A-Za-z_][\w]*)(?:\.(?:{_QUOTED_COMPONENT}|[A-Za-z_][\w]*))?$'
)

# Extensions that indicate the "name" is actually a file, not a database table.
_FILE_EXTENSIONS = (".xlsx", ".xls", ".csv", ".txt", ".json", ".parquet",
                    ".xlsm", ".xlsb", ".pbix", ".zip", ".gz")

def _validate_table_name(name: str | None) -> str | None:
    """Return name if it looks like a clean table identifier, else None.

    This keeps junk out of the sql_table field while allowing explicitly
    quoted PostgreSQL identifiers. Quoted spelling is retained because case,
    spaces, and punctuation are part of the exact relation identity.
    """
    if not name:
        return None
    n = name.strip()
    if not n:
        return None
    # Reject anything that looks like a file
    low = n.lower()
    if any(low.endswith(ext) for ext in _FILE_EXTENSIONS):
        return None
    # Accept bare or schema.table shape
    if _CLEAN_TABLE_RE.match(n):
        return n
    if _QUOTED_TABLE_RE.match(n) and "\x00" not in n and "\n" not in n and "\r" not in n:
        return n
    return None


def _navigation_identifier(value: str) -> str | None:
    """Encode a trusted M navigation component as a PostgreSQL identifier."""
    value = (value or "").strip()
    if not value or "\x00" in value or any(ord(char) < 32 for char in value):
        return None
    if len(value.encode("utf-8")) > 63:
        return None
    if re.fullmatch(r"[A-Za-z_][\w]*", value):
        return value
    return '"' + value.replace('"', '""') + '"'


def _read_m_string(text: str, quote_index: int) -> tuple[str, int] | None:
    """Decode one M string literal starting at ``quote_index``.

    M represents an embedded quote as two double quotes. Common ``#(...)``
    character escapes are decoded as well so multi-line native SQL remains
    parseable after TMDL serialization.
    """
    if quote_index < 0 or quote_index >= len(text) or text[quote_index] != '"':
        return None
    result: list[str] = []
    index = quote_index + 1
    simple_escapes = {
        "cr": "\r",
        "lf": "\n",
        "tab": "\t",
        "quote": '"',
        "#": "#",
    }
    while index < len(text):
        char = text[index]
        if char == '"':
            if index + 1 < len(text) and text[index + 1] == '"':
                result.append('"')
                index += 2
                continue
            return "".join(result), index + 1
        if char == "#" and index + 1 < len(text) and text[index + 1] == "(":
            close = text.find(")", index + 2)
            if close != -1:
                body = text[index + 2:close]
                decoded: list[str] = []
                valid = True
                for token in (part.strip() for part in body.split(",")):
                    lowered = token.casefold()
                    if lowered in simple_escapes:
                        decoded.append(simple_escapes[lowered])
                    elif re.fullmatch(r"[0-9A-Fa-f]{4,8}", token):
                        try:
                            codepoint = int(token, 16)
                            if codepoint > 0x10FFFF:
                                raise ValueError
                            decoded.append(chr(codepoint))
                        except ValueError:
                            valid = False
                            break
                    else:
                        valid = False
                        break
                if valid and decoded:
                    result.extend(decoded)
                    index = close + 1
                    continue
        result.append(char)
        index += 1
    return None


def _decode_m_string_literal(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw.startswith('"'):
        return None
    decoded = _read_m_string(raw, 0)
    if decoded is None or raw[decoded[1]:].strip():
        return None
    return decoded[0]


def _extract_m_assignment_string(expr: str, name: str) -> str | None:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*", expr, re.IGNORECASE)
    if not match:
        return None
    quote_index = match.end()
    if quote_index >= len(expr) or expr[quote_index] != '"':
        return None
    decoded = _read_m_string(expr, quote_index)
    return decoded[0] if decoded is not None else None


def _extract_m_query(expr: str) -> str | None:
    """Return decoded native SQL from Value.NativeQuery or a Query option."""
    native_args = _extract_function_args(expr, "Value.NativeQuery")
    if len(native_args) >= 2:
        native_query = _decode_m_string_literal(native_args[1])
        if native_query is not None:
            return native_query
    return _extract_m_assignment_string(expr, "Query")


_SQL_IDENTIFIER = (
    r'(?:(?:"(?:[^"]|"")*")'
    r'|(?:\[(?:[^\]]|\]\])*\])'
    r'|(?:`(?:[^`]|``)*`)'
    r'|(?:[A-Za-z_][\w$]*))'
)
_SQL_RELATION_RE = re.compile(
    rf'\b(?:FROM|JOIN)\s+'
    rf'(?:ONLY\s+)?'
    rf'(?P<first>{_SQL_IDENTIFIER})'
    rf'(?:\s*\.\s*(?P<second>{_SQL_IDENTIFIER}))?'
    rf'(?![\w$"\[\]`]|\s*\.)',
    re.IGNORECASE,
)
_SQL_IDENTIFIER_RE = re.compile(_SQL_IDENTIFIER)
_SQL_FROM_RE = re.compile(r"\bFROM\b", re.IGNORECASE)
_SQL_FROM_CLAUSE_END = re.compile(
    r"\b(?:WHERE|GROUP|HAVING|ORDER|LIMIT|OFFSET|FETCH|FOR|WINDOW|QUALIFY|"
    r"UNION|EXCEPT|INTERSECT|RETURNING|CONNECT|START)\b",
    re.IGNORECASE,
)


def _mask_sql_noncode(sql: str) -> str:
    """Mask SQL comments and string bodies while preserving character offsets."""
    masked = list(sql)
    index = 0
    length = len(sql)

    def hide(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if masked[position] not in {"\r", "\n"}:
                masked[position] = " "

    while index < length:
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            end = length if end == -1 else end
            hide(index, end)
            index = end
            continue
        if sql.startswith("/*", index):
            start = index
            index += 2
            depth = 1
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            hide(start, index)
            continue
        if sql[index] == "'":
            start = index
            index += 1
            while index < length:
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                # Backslash escaping is not standard SQL, but accepting it
                # here prevents an E'...' or MySQL literal from exposing fake
                # FROM text to the conservative identity parser.
                if sql[index] == "\\" and index + 1 < length:
                    index += 2
                else:
                    index += 1
            hide(start, index)
            continue
        if sql[index] == "$" and (
            index == 0 or not re.match(r"[\w$]", sql[index - 1])
        ):
            delimiter_match = re.match(r"\$(?:[A-Za-z_][\w$]*)?\$", sql[index:])
            if delimiter_match:
                delimiter = delimiter_match.group(0)
                end = sql.find(delimiter, index + len(delimiter))
                end = length if end == -1 else end + len(delimiter)
                hide(index, end)
                index = end
                continue
        index += 1
    return "".join(masked)


def _skip_sql_parenthesized(sql: str, start: int) -> int | None:
    if start >= len(sql) or sql[start] != "(":
        return None
    depth = 0
    index = start
    while index < len(sql):
        char = sql[index]
        if char in {'"', "[", "`"}:
            token = _SQL_IDENTIFIER_RE.match(sql, index)
            if token:
                index = token.end()
                continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _from_clause_has_top_level_comma(sql: str, start: int) -> bool:
    """Return whether one FROM clause uses a comma-separated source list.

    ``sql`` has already had comments and string literals masked. Parentheses
    are tracked relative to this FROM clause so commas inside function calls,
    derived tables, row constructors, and alias column lists do not count.
    """
    depth = 0
    index = start
    while index < len(sql):
        char = sql[index]
        if char in {'"', "[", "`"}:
            token = _SQL_IDENTIFIER_RE.match(sql, index)
            if token:
                index = token.end()
                continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            if depth == 0:
                return False
            depth -= 1
            index += 1
            continue
        if depth == 0:
            if char == ",":
                return True
            if char == ";" or _SQL_FROM_CLAUSE_END.match(sql, index):
                return False
        index += 1
    return False


def _has_comma_separated_from(sql: str) -> bool:
    """Reject SQL whose physical inputs cannot fit one SourceInfo identity."""
    return any(
        _from_clause_has_top_level_comma(sql, match.end())
        for match in _SQL_FROM_RE.finditer(sql)
    )


def _sql_cte_names(sql: str, *, source_type: str | None = None) -> set[str]:
    """Return top-level CTE aliases so they are not mistaken for relations."""
    start = re.match(r"\s*WITH\s+(?:RECURSIVE\s+)?", sql, re.IGNORECASE)
    if not start:
        return set()
    names: set[str] = set()
    index = start.end()
    while index < len(sql):
        index += len(sql[index:]) - len(sql[index:].lstrip())
        token = _SQL_IDENTIFIER_RE.match(sql, index)
        if not token:
            break
        alias = _sql_identifier_value(token.group(0), source_type=source_type)
        index = token.end()
        index += len(sql[index:]) - len(sql[index:].lstrip())
        if index < len(sql) and sql[index] == "(":
            end = _skip_sql_parenthesized(sql, index)
            if end is None:
                break
            index = end
            index += len(sql[index:]) - len(sql[index:].lstrip())
        as_match = re.match(
            r"AS\s+(?:(?:NOT\s+)?MATERIALIZED\s+)?",
            sql[index:],
            re.IGNORECASE,
        )
        if not as_match:
            break
        index += as_match.end()
        if index >= len(sql) or sql[index] != "(":
            break
        end = _skip_sql_parenthesized(sql, index)
        if end is None:
            break
        names.add(alias)
        index = end
        index += len(sql[index:]) - len(sql[index:].lstrip())
        if index >= len(sql) or sql[index] != ",":
            break
        index += 1
    return names


def _sql_identifier_value(
    token: str,
    *,
    source_type: str | None = None,
) -> str:
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('""', '"')
    if token.startswith("[") and token.endswith("]"):
        return token[1:-1].replace("]]", "]")
    if token.startswith("`") and token.endswith("`"):
        return token[1:-1].replace("``", "`")
    if source_type == "postgresql":
        # PostgreSQL folds unquoted SQL identifiers to lowercase. Preserve
        # quoted tokens above exactly, and do this only for parsed SQL: TMDL
        # navigation values already contain the catalog's resolved spelling.
        return re.sub(r"[A-Z]", lambda match: match.group(0).lower(), token)
    return token


def _extract_sql_relation(
    sql: str | None,
    *,
    source_type: str | None = None,
) -> str | None:
    if not sql:
        return None
    searchable = _mask_sql_noncode(sql)
    if _has_comma_separated_from(searchable):
        # SourceInfo can represent one exact relation only. A comma join is a
        # multi-input query even when both inputs happen to share a name; do
        # not claim the first relation and create executable false lineage.
        return None
    cte_names = _sql_cte_names(searchable, source_type=source_type)
    candidates: dict[str, str] = {}
    reserved = {"lateral", "only", "select", "table", "unnest", "values"}
    for match in _SQL_RELATION_RE.finditer(searchable):
        first_token = match.group("first")
        first_value = _sql_identifier_value(first_token, source_type=source_type)
        if (
            first_token[:1] not in {'"', "[", "`"}
            and first_value.casefold() in reserved
        ):
            continue
        second_token = match.group("second")
        if second_token is None and first_value in cte_names:
            continue
        after = match.end()
        while after < len(searchable) and searchable[after].isspace():
            after += 1
        # A FROM/JOIN function is not a table identity. Leave it unresolved.
        if after < len(searchable) and searchable[after] == "(":
            continue

        first = _navigation_identifier(first_value)
        if second_token is None:
            candidate = _validate_table_name(first)
        else:
            second = _navigation_identifier(
                _sql_identifier_value(second_token, source_type=source_type)
            )
            candidate = _validate_table_name(
                f"{first}.{second}" if first and second else None
            )
        if candidate:
            candidates[candidate] = candidate
        if len(candidates) > 1:
            # SourceInfo can hold only one exact relation. Claiming the first
            # of multiple inputs would create false executable lineage.
            return None
    return next(iter(candidates.values()), None)


def _extract_table_navigation(
    expr: str,
    *,
    decoded_sql: str | None = None,
    source_type: str | None = None,
) -> str | None:
    """Extract the schema and table name from M navigation patterns.

    Handles multiple patterns used by different connectors:
      Source{[Schema="public",Item="orders"]}[Data]
      Source{[Name="orders",Kind="Table"]}[Data]
      Source{[Name="orders"]}[Data]
      Source{[Schema="public", Item="orders"]}[Data]  (with spaces)

    For native queries, tries to extract the table from the SQL.

    Every candidate is validated via _validate_table_name before being
    returned - this ensures sql_table only contains clean identifiers and
    never a parenthesised blob or filename. Callers treat None as
    "couldn't find a clean table" and degrade gracefully.
    """
    # Pattern 1: Schema + Item (most common for SQL Server, PostgreSQL)
    match = re.search(r'Schema\s*=\s*"([^"]+)"\s*,\s*Item\s*=\s*"([^"]+)"', expr)
    if match:
        schema = _navigation_identifier(match.group(1))
        relation = _navigation_identifier(match.group(2))
        candidate = f"{schema}.{relation}" if schema and relation else None
        if (v := _validate_table_name(candidate)):
            return v

    # Pattern 2: Item + Schema (reversed order)
    match = re.search(r'Item\s*=\s*"([^"]+)"\s*,\s*Schema\s*=\s*"([^"]+)"', expr)
    if match:
        schema = _navigation_identifier(match.group(2))
        relation = _navigation_identifier(match.group(1))
        candidate = f"{schema}.{relation}" if schema and relation else None
        if (v := _validate_table_name(candidate)):
            return v

    # Pattern 3: Name + Kind="Table" (PostgreSQL often uses this; Kind gate
    # keeps us from matching navigation into views, functions, or columns)
    match = re.search(r'Name\s*=\s*"([^"]+)"\s*,\s*Kind\s*=\s*"Table"', expr)
    if match:
        if (v := _validate_table_name(_navigation_identifier(match.group(1)))):
            return v

    # Pattern 4: Just Name= - only accept if it passes strict validation.
    # Name="..." is too generic in M (it can reference parameters, columns,
    # Power Query steps, etc.), so we lean hard on the validator here.
    for m in re.finditer(r'Name\s*=\s*"([^"]+)"', expr):
        if (v := _validate_table_name(m.group(1))):
            return v

    # Pattern 5: Native SQL. Prefer the already decoded query, then decode it
    # here for direct helper callers, and finally allow plain SQL test/import
    # callers. Parsing raw M first would misread doubled quote delimiters.
    sql_candidates = [decoded_sql, _extract_m_query(expr), expr]
    seen: set[str] = set()
    for sql in sql_candidates:
        if not sql or sql in seen:
            continue
        seen.add(sql)
        if (relation := _extract_sql_relation(sql, source_type=source_type)):
            return relation

    return None


def _extract_function_args(expr: str, func_name: str) -> list[str]:
    """Extract top-level arguments from a function call in M expression."""
    pattern = re.escape(func_name) + r"\s*\("
    match = re.search(pattern, expr)
    if not match:
        return []

    start = match.end()
    depth = 1
    args = []
    current = []

    i = start
    while i < len(expr) and depth > 0:
        ch = expr[i]
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(current).strip())
            else:
                current.append(ch)
        elif ch == "," and depth == 1:
            args.append("".join(current).strip())
            current = []
        elif ch == '"':
            # Preserve the raw literal for callers while skipping commas and
            # parentheses inside it according to M's doubled-quote rules.
            decoded = _read_m_string(expr, i)
            if decoded is None:
                current.append(expr[i:])
                i = len(expr)
                break
            end = decoded[1]
            current.append(expr[i:end])
            i = end - 1
        else:
            current.append(ch)
        i += 1

    return args


def _unquote(s: str) -> str:
    """Decode a surrounding Power Query M string literal when present."""
    s = s.strip()
    if s.startswith('"'):
        decoded = _decode_m_string_literal(s)
        if decoded is not None:
            return decoded
    return s


def _clean_identifier(s: str) -> str:
    """Clean a database identifier by removing parser artifacts.

    Strips trailing parenthetical content, square brackets, and extra whitespace
    from server names, database names, and table names.
    """
    if not s:
        return s
    # Remove M-style quoting: #"Quoted Name" -> Quoted Name
    if s.startswith('#"') and s.endswith('"'):
        s = s[2:-1]
    # Strip SQL Server bracket quoting: [dbo] -> dbo
    s = re.sub(r'^\[([^\]]+)\]$', r'\1', s)
    # Strip trailing parenthetical content: "server (instance)" -> "server"
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    return s.strip()


def resolve_parameters(source: SourceInfo, params: dict[str, str]) -> SourceInfo:
    """Replace parameter references in source info with actual values.

    If source.server is a bare identifier (not a path/URL), look it up in params.
    Same for source.database.
    """
    if source.source_type in source.DB_TYPES:
        if source.server and source.server in params:
            source.server = params[source.server]
        if source.database and source.database in params:
            source.database = params[source.database]
    return source
