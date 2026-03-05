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
    _parse_m_expression,
    _extract_hashtable_value,
)

logger = logging.getLogger(__name__)


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
class PbixReport:
    """A report extracted from a .pbix file."""
    name: str
    file_path: str
    tables: list[PbixTable] = field(default_factory=list)
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
            for _, row in pq.iterrows():
                table_name = row.get("TableName") or row.get("tableName") or row.get("Name")
                expression = row.get("Expression") or row.get("expression")
                if table_name and expression:
                    m_expressions[table_name] = expression
    except Exception as e:
        logger.warning("Could not read power_query from %s: %s", file_path.name, e)

    # Get column schema
    column_map = {}
    try:
        schema = model.schema
        if schema is not None and len(schema) > 0:
            for _, row in schema.iterrows():
                table_name = row.get("TableName") or row.get("tableName")
                col_name = row.get("ColumnName") or row.get("columnName") or row.get("Name")
                if table_name and col_name:
                    if table_name not in column_map:
                        column_map[table_name] = []
                    column_map[table_name].append(col_name)
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
        business_owner=business_owner,
        report_owner=report_owner,
        layout=layout,
    )
