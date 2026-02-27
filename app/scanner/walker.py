"""
Folder walker for TMDL report exports.

Walks the TMDL root folder to discover all reports and their table definitions.

Expected folder structure (Windows paths):
  {TMDL_ROOT}/reports/{report_name}/{report_name}.SemanticModel/Definition/Tables/*.tmdl
  {TMDL_ROOT}/reports/{report_name}/{report_name}.SemanticModel/Definition/expressions.tmdl
"""

from dataclasses import dataclass, field
from pathlib import Path
from app.scanner.tmdl_parser import ParsedTable, parse_tmdl_file, parse_expressions_file


@dataclass
class DiscoveredReport:
    """A report discovered by walking the TMDL folder structure."""
    name: str
    tmdl_path: str  # path to the report's root folder
    tables: list[ParsedTable] = field(default_factory=list)
    expressions: dict[str, str] = field(default_factory=dict)  # parameters from expressions.tmdl


def walk_tmdl_root(root_path: str | Path) -> list[DiscoveredReport]:
    """Walk the TMDL root folder and discover all reports with their tables.

    Args:
        root_path: Path to the TMDL root (e.g., C:\\Users\\user\\documents\\projects\\data_governance)

    Returns:
        List of discovered reports with parsed table information.
    """
    root = Path(root_path)
    reports_dir = root / "reports"
    discovered = []

    if not reports_dir.exists():
        return discovered

    # Each subfolder in reports/ is a report
    for report_dir in sorted(reports_dir.iterdir()):
        if not report_dir.is_dir():
            continue

        report = _scan_report_folder(report_dir)
        if report:
            discovered.append(report)

    return discovered


def _scan_report_folder(report_dir: Path) -> DiscoveredReport | None:
    """Scan a single report folder for its semantic model definition."""
    report_name = report_dir.name

    # Find the .SemanticModel folder
    # Pattern: {report_name}.SemanticModel or any *.SemanticModel folder
    semantic_dirs = list(report_dir.glob("*.SemanticModel"))
    if not semantic_dirs:
        # Also try case-insensitive match
        semantic_dirs = [
            d for d in report_dir.iterdir()
            if d.is_dir() and d.name.lower().endswith(".semanticmodel")
        ]
    if not semantic_dirs:
        return None

    semantic_dir = semantic_dirs[0]
    definition_dir = semantic_dir / "Definition"
    if not definition_dir.exists():
        # Try lowercase
        definition_dir = semantic_dir / "definition"
    if not definition_dir.exists():
        return None

    tables_dir = definition_dir / "Tables"
    if not tables_dir.exists():
        # Try lowercase
        tables_dir = definition_dir / "tables"
    if not tables_dir.exists():
        return None

    # Parse expressions.tmdl for parameter resolution
    expressions = {}
    expr_file = definition_dir / "expressions.tmdl"
    if expr_file.exists():
        expressions = parse_expressions_file(expr_file)

    # Parse each .tmdl file in the Tables directory
    tables = []
    for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
        parsed = parse_tmdl_file(tmdl_file)
        if parsed:
            tables.append(parsed)

    if not tables:
        return None

    return DiscoveredReport(
        name=report_name,
        tmdl_path=str(report_dir),
        tables=tables,
        expressions=expressions,
    )
