"""
Power BI best-practice checker — analyses reports for common issues.

Works with both PBIX and TMDL modes via the existing scanner walker.
Each check returns findings with report, table, issue, and severity.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.scanner.walker import walk_reports_root, DiscoveredReport


@dataclass
class Finding:
    report: str
    table: str
    rule: str
    issue: str
    severity: str  # "high", "medium", "low"


# ── Individual checks ──────────────────────────────────────────────────


def _check_local_file_source(table, report_name: str) -> list[Finding]:
    """Flag data sources pointing to local drives (C:\\, D:\\, etc.)."""
    source = getattr(table, "source", None)
    if not source or not source.file_path:
        return []
    fp = source.file_path
    if re.match(r'^[A-Za-z]:\\', fp):
        return [Finding(
            report=report_name,
            table=table.table_name,
            rule="No local file sources",
            issue=f'Source uses local path "{fp}". Use a shared network drive (\\\\server\\share\\...) or a database connection instead.',
            severity="high",
        )]
    return []


def _check_missing_owner(report: DiscoveredReport, owner_type: str) -> list[Finding]:
    """Flag reports missing a Report Owner or Business Owner."""
    attr = "report_owner" if owner_type == "Report Owner" else "business_owner"
    value = getattr(report, attr, None)

    # Also check if there's a metadata table with that name
    has_table = any(t.table_name == owner_type for t in report.tables)

    if not value and not has_table:
        return [Finding(
            report=report.name,
            table="—",
            rule=f"{owner_type} required",
            issue=f'Report is missing a "{owner_type}" table. Every report should declare a {owner_type.lower()} for accountability.',
            severity="medium",
        )]
    return []


def _check_date_column_as_string(table, report_name: str) -> list[Finding]:
    """Flag columns named *date* that are typed as string (TMDL mode only)."""
    if getattr(table, "is_metadata", False):
        return []
    file_path = getattr(table, "file_path", None)
    if not file_path or not Path(file_path).exists():
        return []

    findings = []
    text = Path(file_path).read_text(encoding="utf-8-sig")
    col_name = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("column "):
            col_name = stripped[7:].strip().strip("'")
        elif stripped.startswith("dataType:") and col_name:
            dtype = stripped.split(":", 1)[1].strip()
            if dtype == "string" and re.search(r'date', col_name, re.IGNORECASE):
                findings.append(Finding(
                    report=report_name,
                    table=table.table_name,
                    rule="Date columns should use dateTime",
                    issue=f'Column "{col_name}" appears to be a date but is typed as string. Use dateTime for proper sorting and filtering.',
                    severity="medium",
                ))
            col_name = None
    return findings


def _check_directquery_mode(table, report_name: str) -> list[Finding]:
    """Flag tables using DirectQuery mode instead of Import."""
    mode = getattr(table, "mode", None)
    if mode and mode.lower() == "directquery":
        return [Finding(
            report=report_name,
            table=table.table_name,
            rule="Avoid DirectQuery mode",
            issue=f'Table uses DirectQuery mode, which queries the source on every interaction. Use Import mode for better performance unless real-time data is required.',
            severity="medium",
        )]
    return []


def _check_duplicate_sources(tables, report_name: str) -> list[Finding]:
    """Flag multiple tables in the same report pulling from the same source."""
    seen: dict[str, str] = {}  # connection_key -> first table name
    findings = []
    for table in tables:
        if getattr(table, "is_metadata", False):
            continue
        source = getattr(table, "source", None)
        if not source:
            continue
        key = source.connection_key
        if key.startswith("unknown::"):
            continue
        if key in seen:
            findings.append(Finding(
                report=report_name,
                table=table.table_name,
                rule="Duplicate data source",
                issue=f'Same source as table "{seen[key]}". Consolidate into a single source table or use a reference query to avoid redundant refreshes.',
                severity="low",
            ))
        else:
            seen[key] = table.table_name
    return findings


# ── Main entry point ───────────────────────────────────────────────────


def check_report(report: DiscoveredReport) -> list[Finding]:
    """Run all best-practice checks on a discovered report."""
    findings: list[Finding] = []

    # Report-level checks
    findings.extend(_check_missing_owner(report, "Report Owner"))
    findings.extend(_check_missing_owner(report, "Business Owner"))
    findings.extend(_check_duplicate_sources(report.tables, report.name))

    # Table-level checks
    for table in report.tables:
        if getattr(table, "is_metadata", False):
            continue
        findings.extend(_check_local_file_source(table, report.name))
        findings.extend(_check_date_column_as_string(table, report.name))
        findings.extend(_check_directquery_mode(table, report.name))

    return findings


def scan_all(root: str | Path) -> list[Finding]:
    """Scan every report under the root using the existing walker."""
    reports = walk_reports_root(root)
    findings: list[Finding] = []
    for report in reports:
        findings.extend(check_report(report))
    return findings
