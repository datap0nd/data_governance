"""
Power BI best-practice checker — analyses TMDL files for common issues.

Each check returns a list of findings with report, table, issue description,
and severity (high / medium / low).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.scanner.tmdl_parser import parse_tmdl_file, ParsedTable, METADATA_TABLES


@dataclass
class Finding:
    report: str
    table: str
    rule: str
    issue: str
    severity: str  # "high", "medium", "low"


# ── Individual checks ──────────────────────────────────────────────────


def _check_local_file_source(parsed: ParsedTable, report_name: str) -> list[Finding]:
    """Flag data sources pointing to local drives (C:\\, D:\\, etc.)
    instead of shared network paths or databases."""
    if not parsed.source or not parsed.source.file_path:
        return []
    fp = parsed.source.file_path
    # Local drive letter pattern (C:\, D:\, etc.)
    if re.match(r'^[A-Za-z]:\\', fp):
        return [Finding(
            report=report_name,
            table=parsed.table_name,
            rule="No local file sources",
            issue=f"Source uses local path \"{fp}\". Use a shared network drive (\\\\server\\share\\...) or a database connection instead.",
            severity="high",
        )]
    return []


def _check_missing_owner(tables: list[ParsedTable], report_name: str,
                          owner_type: str) -> list[Finding]:
    """Flag reports missing a Report Owner or Business Owner metadata table."""
    has_owner = any(t.table_name == owner_type for t in tables)
    if not has_owner:
        return [Finding(
            report=report_name,
            table="—",
            rule=f"{owner_type} required",
            issue=f"Report is missing a \"{owner_type}\" table. Every report should declare a {owner_type.lower()} for accountability.",
            severity="medium",
        )]
    return []


def _check_date_column_as_string(parsed: ParsedTable, report_name: str) -> list[Finding]:
    """Flag columns whose name contains 'date' but whose dataType is string."""
    if parsed.is_metadata:
        return []
    findings = []
    text = Path(parsed.file_path).read_text(encoding="utf-8-sig") if parsed.file_path else ""
    # Parse column blocks: look for column name + dataType pairs
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
                    table=parsed.table_name,
                    rule="Date columns should use dateTime",
                    issue=f"Column \"{col_name}\" appears to be a date but is typed as string. Use dateTime for proper sorting and filtering.",
                    severity="medium",
                ))
            col_name = None
    return findings


def _check_hardcoded_server(parsed: ParsedTable, report_name: str,
                            expressions: dict[str, str]) -> list[Finding]:
    """Flag database sources with hardcoded server/IP instead of using
    expressions.tmdl parameters."""
    if not parsed.source or not parsed.source.server:
        return []
    # If the report already uses parameters, no issue
    if expressions:
        return []
    server = parsed.source.server
    # Only flag if it looks like a raw hostname or IP (not a parameter reference)
    if re.match(r'^\d+\.\d+\.\d+\.\d+$', server) or '.' in server:
        return [Finding(
            report=report_name,
            table=parsed.table_name,
            rule="Use parameters for connections",
            issue=f"Server \"{server}\" is hardcoded. Define Server/Database parameters in expressions.tmdl to simplify environment changes.",
            severity="low",
        )]
    return []


# ── Main entry point ───────────────────────────────────────────────────


def scan_report(report_dir: Path) -> list[Finding]:
    """Run all best-practice checks on a single report directory.

    Expects the standard TMDL layout:
        ReportName/ReportName.SemanticModel/Definition/Tables/*.tmdl
    """
    findings: list[Finding] = []
    report_name = report_dir.name

    # Locate Tables directory
    tables_dir = None
    for sm_dir in report_dir.iterdir():
        candidate = sm_dir / "Definition" / "Tables"
        if candidate.is_dir():
            tables_dir = candidate
            break

    if tables_dir is None:
        return findings

    # Parse expressions.tmdl if present
    expressions: dict[str, str] = {}
    expr_file = tables_dir.parent / "expressions.tmdl"
    if expr_file.exists():
        from app.scanner.tmdl_parser import parse_expressions_file
        expressions = parse_expressions_file(expr_file)

    # Parse all table files
    parsed_tables: list[ParsedTable] = []
    for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
        parsed = parse_tmdl_file(tmdl_file)
        if parsed:
            parsed_tables.append(parsed)

    # Report-level checks (owners)
    findings.extend(_check_missing_owner(parsed_tables, report_name, "Report Owner"))
    findings.extend(_check_missing_owner(parsed_tables, report_name, "Business Owner"))

    # Table-level checks
    for pt in parsed_tables:
        if pt.is_metadata:
            continue
        findings.extend(_check_local_file_source(pt, report_name))
        findings.extend(_check_date_column_as_string(pt, report_name))
        findings.extend(_check_hardcoded_server(pt, report_name, expressions))

    return findings


def scan_all(root: str | Path) -> list[Finding]:
    """Scan every report under the TMDL root and return all findings."""
    root = Path(root)
    findings: list[Finding] = []
    if not root.is_dir():
        return findings

    # Each top-level subdirectory is a report
    for report_dir in sorted(root.iterdir()):
        if report_dir.is_dir() and not report_dir.name.startswith("."):
            findings.extend(scan_report(report_dir))

    return findings
