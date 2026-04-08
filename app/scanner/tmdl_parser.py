"""
TMDL table file parser.

Parses .tmdl files from Power BI semantic model exports to extract:
- Table name
- Partition M expression
- Source type (csv, excel, sql)
- Source details (file path, server, database, etc.)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


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
    def connection_key(self) -> str:
        """Unique key to identify this source for deduplication.

        Database sources are deduplicated at the table level
        (same server + database + table = same source).
        """
        if self.source_type in self.FILE_TYPES and self.file_path:
            return f"{self.source_type}::{self.file_path.lower()}"
        elif self.source_type in self.DB_TYPES and self.server:
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
            # For PostgreSQL, just show database.table (skip server IP)
            if self.source_type == "postgresql" and self.sql_table:
                if self.database:
                    return f"{self.database}.{self.sql_table}"
                return self.sql_table
            parts = []
            if self.database:
                parts.append(self.database)
            if self.sql_table:
                parts.append(self.sql_table)
            if parts:
                return f"{self.server}/{'/'.join(parts)}"
            return self.server
        return "Unknown Source"

    @property
    def connection_info(self) -> str:
        """Connection string or path for storage."""
        if self.source_type in self.FILE_TYPES and self.file_path:
            return self.file_path
        elif self.source_type in self.DB_TYPES:
            parts = [self.server or "?"]
            if self.database:
                parts.append(self.database)
            if self.sql_table:
                parts.append(self.sql_table)
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
            # Check for native query in options
            query_match = re.search(r'Query\s*=\s*"((?:[^"\\]|\\.)*)"', expr, re.DOTALL)
            if query_match:
                source.sql_query = query_match.group(1)
            # Also check Value.NativeQuery pattern
            native_match = re.search(r'Value\.NativeQuery\s*\([^,]+,\s*"((?:[^"\\]|\\.)*)"', expr, re.DOTALL)
            if native_match:
                source.sql_query = native_match.group(1)
            # Extract the specific table being accessed
            source.sql_table = _extract_table_navigation(expr)
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


def _extract_table_navigation(expr: str) -> str | None:
    """Extract the schema and table name from M navigation patterns.

    Handles multiple patterns used by different connectors:
      Source{[Schema="public",Item="orders"]}[Data]
      Source{[Name="orders",Kind="Table"]}[Data]
      Source{[Name="orders"]}[Data]
      Source{[Schema="public", Item="orders"]}[Data]  (with spaces)

    For native queries, tries to extract the table from the SQL.
    """
    # Pattern 1: Schema + Item (most common for SQL Server, PostgreSQL)
    match = re.search(r'Schema\s*=\s*"([^"]+)"\s*,\s*Item\s*=\s*"([^"]+)"', expr)
    if match:
        return f"{match.group(1)}.{match.group(2)}"

    # Pattern 2: Item + Schema (reversed order)
    match = re.search(r'Item\s*=\s*"([^"]+)"\s*,\s*Schema\s*=\s*"([^"]+)"', expr)
    if match:
        return f"{match.group(2)}.{match.group(1)}"

    # Pattern 3: Name + Kind (PostgreSQL often uses this)
    match = re.search(r'Name\s*=\s*"([^"]+)"\s*,\s*Kind\s*=\s*"Table"', expr)
    if match:
        return match.group(1)

    # Pattern 4: Just Name= (simpler navigation)
    match = re.search(r'Name\s*=\s*"([^"]+)"', expr)
    if match:
        return match.group(1)

    # Pattern 5: Try to get table from native query (SELECT ... FROM schema.table)
    match = re.search(r'(?:FROM|JOIN)\s+["\[]?(\w+)["\]]?\s*\.\s*["\[]?(\w+)["\]]?', expr, re.IGNORECASE)
    if match:
        return f"{match.group(1)}.{match.group(2)}"

    # Pattern 6: Simple FROM table
    match = re.search(r'(?:FROM|JOIN)\s+["\[]?(\w+)["\]]?\s', expr, re.IGNORECASE)
    if match:
        return match.group(1)

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
            # Read string literal
            current.append(ch)
            i += 1
            while i < len(expr) and expr[i] != '"':
                current.append(expr[i])
                i += 1
            if i < len(expr):
                current.append(expr[i])
        else:
            current.append(ch)
        i += 1

    return args


def _unquote(s: str) -> str:
    """Remove surrounding double quotes from a string."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def resolve_parameters(source: SourceInfo, params: dict[str, str]) -> SourceInfo:
    """Replace parameter references in source info with actual values.

    If source.server is a bare identifier (not a path/URL), look it up in params.
    Same for source.database.
    """
    if source.source_type == "sql":
        if source.server and source.server in params:
            source.server = params[source.server]
        if source.database and source.database in params:
            source.database = params[source.database]
    return source
