"""
PBIX file parser using PBIXRay.

Reads .pbix files directly to extract table definitions,
Power Query M expressions, and metadata (Business Owner, Report Owner).
No need for TMDL exports or Power BI Desktop.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.scanner.tmdl_parser import (
    SourceInfo,
    METADATA_TABLES,
    is_auto_table,
    _parse_m_expression,
    _extract_hashtable_value,
)

logger = logging.getLogger(__name__)


def _df_col(df, *names) -> str | None:
    """Find a DataFrame column by trying names, with case-insensitive fallback."""
    cols = set(df.columns)
    for name in names:
        if name in cols:
            return name
    lower_map = {c.lower(): c for c in cols}
    for name in names:
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


@dataclass
class PbixTable:
    """A table extracted from a .pbix file."""
    table_name: str
    columns: list[str] = field(default_factory=list)
    m_expression: str | None = None
    source: SourceInfo | None = None
    is_metadata: bool = False
    metadata_value: str | None = None


@dataclass
class MeasureInfo:
    """A DAX measure extracted from a .pbix or .tmdl file."""
    table_name: str
    measure_name: str
    dax_expression: str | None = None


@dataclass
class PbixReport:
    """A report extracted from a .pbix file."""
    name: str
    file_path: str
    tables: list[PbixTable] = field(default_factory=list)
    measures: list[MeasureInfo] = field(default_factory=list)
    business_owner: str | None = None
    report_owner: str | None = None
    layout: object = None  # ReportLayout from layout_parser, if available


def parse_pbix_file(file_path: str | Path) -> PbixReport | None:
    """Parse a .pbix file and extract all table/source information.

    Returns a PbixReport with tables, sources, and owner metadata.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return None

    report_name = file_path.stem  # filename without .pbix

    try:
        from pbixray import PBIXRay
        model = PBIXRay(str(file_path))
    except Exception as e:
        logger.error("Failed to open %s: %s", file_path.name, e)
        return None

    tables = []
    business_owner = None
    report_owner = None

    # Get Power Query M expressions for each table
    m_expressions = {}
    try:
        pq = model.power_query
        if pq is not None and len(pq) > 0:
            tname_col = _df_col(pq, "TableName", "tableName", "Name", "Table")
            expr_col = _df_col(pq, "Expression", "expression", "Query")
            if tname_col and expr_col:
                for _, row in pq.iterrows():
                    table_name = row.get(tname_col)
                    expression = row.get(expr_col)
                    if table_name and expression:
                        m_expressions[str(table_name)] = str(expression)
            else:
                logger.warning("Unexpected power_query columns in %s: %s", file_path.name, list(pq.columns))
    except Exception as e:
        logger.warning("Could not read power_query from %s: %s", file_path.name, e)

    # Get column schema
    column_map = {}
    try:
        schema = model.schema
        if schema is not None and len(schema) > 0:
            tname_col = _df_col(schema, "TableName", "tableName", "Table")
            cname_col = _df_col(schema, "ColumnName", "columnName", "Name", "Column")
            if tname_col and cname_col:
                for _, row in schema.iterrows():
                    table_name = row.get(tname_col)
                    col_name = row.get(cname_col)
                    if table_name and col_name:
                        tname_str = str(table_name)
                        if tname_str not in column_map:
                            column_map[tname_str] = []
                        column_map[tname_str].append(str(col_name))
            else:
                logger.warning("Unexpected schema columns in %s: %s", file_path.name, list(schema.columns))
    except Exception as e:
        logger.warning("Could not read schema from %s: %s", file_path.name, e)

    # Get table names
    table_names = []
    try:
        table_names = model.tables or []
    except Exception as e:
        logger.warning("Could not read tables from %s: %s", file_path.name, e)
        # Fall back to tables we found in power_query
        table_names = list(m_expressions.keys())

    # Process each table
    for tname in table_names:
        # Skip Power BI auto-generated internal tables
        if is_auto_table(tname):
            continue

        expr = m_expressions.get(tname)
        columns = column_map.get(tname, [])

        is_metadata = tname in METADATA_TABLES
        metadata_value = None
        source = None

        if expr:
            if is_metadata:
                metadata_value = _extract_hashtable_value(expr)
            else:
                source = _parse_m_expression(expr)
                if source and source.source_type == "unknown":
                    logger.info("  [%s] table '%s': unknown source — expr: %.150s", report_name, tname, expr.replace("\n", " "))
                elif source:
                    logger.info("  [%s] table '%s': %s (%s)", report_name, tname, source.source_type, source.display_name)

        tables.append(PbixTable(
            table_name=tname,
            columns=columns,
            m_expression=expr,
            source=source,
            is_metadata=is_metadata,
            metadata_value=metadata_value,
        ))

        # Extract owners
        if is_metadata and metadata_value:
            if tname == "Business Owner":
                business_owner = metadata_value
            elif tname == "Report Owner":
                report_owner = metadata_value

    # Extract DAX measures
    measures = []
    try:
        dax_measures = model.dax_measures
        if dax_measures is not None and len(dax_measures) > 0:
            tbl_col = _df_col(dax_measures, "TableName", "tableName", "Table")
            name_col = _df_col(dax_measures, "Name", "name", "MeasureName", "DisplayName")
            expr_col = _df_col(dax_measures, "Expression", "expression", "MeasureExpression")
            if name_col:
                for _, row in dax_measures.iterrows():
                    tbl = str(row.get(tbl_col, "")) if tbl_col else ""
                    name = row.get(name_col)
                    expr = row.get(expr_col) if expr_col else ""
                    if name:
                        measures.append(MeasureInfo(
                            table_name=tbl,
                            measure_name=str(name),
                            dax_expression=str(expr) if expr else None,
                        ))
            else:
                logger.warning("Unexpected dax_measures columns in %s: %s", file_path.name, list(dax_measures.columns))
    except Exception as e:
        logger.warning("Could not read dax_measures from %s: %s", file_path.name, e)

    # Extract visual layout (pages, visuals, field references)
    layout = None
    try:
        from app.scanner.layout_parser import parse_pbix_layout
        layout = parse_pbix_layout(file_path)
    except Exception as e:
        logger.warning("Could not parse layout from %s: %s", file_path.name, e)

    return PbixReport(
        name=report_name,
        file_path=str(file_path),
        tables=tables,
        measures=measures,
        business_owner=business_owner,
        report_owner=report_owner,
        layout=layout,
    )
