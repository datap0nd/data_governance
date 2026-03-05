"""
Source matcher — deduplicates sources across reports.

When 10 reports all pull from the same database,
that's 1 source, not 10.
"""

from app.scanner.tmdl_parser import SourceInfo, resolve_parameters, is_auto_table
from app.scanner.walker import DiscoveredReport

# Source types that are internal/calculated, not real external data sources
_SKIP_SOURCE_TYPES = {"unknown", "calculated"}


def deduplicate_sources(reports: list[DiscoveredReport]) -> dict[str, SourceInfo]:
    """Find all unique data sources across all reports.

    Returns a dict of {connection_key: SourceInfo}.
    """
    sources: dict[str, SourceInfo] = {}

    for report in reports:
        expressions = getattr(report, "expressions", {})

        for table in report.tables:
            source = getattr(table, "source", None)
            is_metadata = getattr(table, "is_metadata", False)
            table_name = getattr(table, "table_name", "")

            if source is None or is_metadata or is_auto_table(table_name):
                continue

            # Skip calculated/unknown — not real external sources
            if source.source_type in _SKIP_SOURCE_TYPES:
                continue

            # Resolve parameters for TMDL tables
            if expressions:
                resolved = resolve_parameters(source, expressions)
            else:
                resolved = source

            key = resolved.connection_key
            if key not in sources:
                sources[key] = resolved

    return sources
