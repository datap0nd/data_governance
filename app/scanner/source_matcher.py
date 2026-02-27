"""
Source matcher — deduplicates sources across reports.

When 10 reports all pull from the same PostgreSQL database,
that's 1 source, not 10. This module handles that deduplication.
"""

from app.scanner.tmdl_parser import SourceInfo, resolve_parameters
from app.scanner.walker import DiscoveredReport


def deduplicate_sources(reports: list[DiscoveredReport]) -> dict[str, SourceInfo]:
    """Find all unique data sources across all reports.

    Returns a dict of {connection_key: SourceInfo}.
    """
    sources: dict[str, SourceInfo] = {}

    for report in reports:
        for table in report.tables:
            if table.source is None:
                continue

            # Resolve parameters (e.g., Server → "localhost")
            resolved = resolve_parameters(table.source, report.expressions)

            key = resolved.connection_key
            if key not in sources:
                sources[key] = resolved

    return sources
