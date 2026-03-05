"""
Folder walker for report discovery.

Supports two modes:
1. PBIX mode (primary): Find .pbix files in the reports folder and parse them with PBIXRay
2. TMDL mode (fallback): Walk TMDL export folder structure

Expected folder structure for PBIX mode:
  {REPORTS_ROOT}/*.pbix
  or
  {REPORTS_ROOT}/subfolder/*.pbix

Expected folder structure for TMDL mode:
  {REPORTS_ROOT}/{report_name}/{report_name}.SemanticModel/Definition/Tables/*.tmdl
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.scanner.tmdl_parser import ParsedTable, parse_tmdl_file, parse_expressions_file

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredReport:
    """A report discovered by walking the folder structure."""
    name: str
    tmdl_path: str  # path to the report folder or .pbix file
    tables: list = field(default_factory=list)  # ParsedTable or PbixTable
    measures: list = field(default_factory=list)  # MeasureInfo list
    expressions: dict[str, str] = field(default_factory=dict)
    business_owner: str | None = None
    report_owner: str | None = None
    layout: object = None  # ReportLayout from layout_parser (PBIX mode only)


def walk_reports_root(root_path: str | Path) -> list[DiscoveredReport]:
    """Walk the reports root folder and discover all reports.

    First looks for .pbix files, then falls back to TMDL folder structure.
    """
    root = Path(root_path).resolve()
    logger.info("walk_reports_root: root_path=%s resolved=%s exists=%s", root_path, root, root.exists())
    if not root.exists():
        logger.error("Reports root not found: %s", root)
        return []

    # Look for .pbix files
    pbix_files = list(root.glob("*.pbix"))
    if not pbix_files:
        # Also check one level of subfolders
        pbix_files = list(root.glob("*/*.pbix"))

    if pbix_files:
        logger.info("Found %d .pbix files, using PBIX mode", len(pbix_files))
        return _walk_pbix(pbix_files)

    # Fall back to TMDL folder structure
    logger.info("No .pbix files found, trying TMDL mode")
    return _walk_tmdl(root)


def _walk_pbix(pbix_files: list[Path]) -> list[DiscoveredReport]:
    """Parse .pbix files using PBIXRay."""
    from app.scanner.pbix_parser import parse_pbix_file

    discovered = []
    for pbix_path in sorted(pbix_files):
        logger.info("Parsing: %s", pbix_path.name)
        report = parse_pbix_file(pbix_path)
        if report:
            discovered.append(DiscoveredReport(
                name=report.name,
                tmdl_path=report.file_path,
                tables=report.tables,
                measures=report.measures,
                business_owner=report.business_owner,
                report_owner=report.report_owner,
                layout=report.layout,
            ))
        else:
            logger.warning("Could not parse: %s", pbix_path.name)

    return discovered


def _walk_tmdl(root: Path) -> list[DiscoveredReport]:
    """Walk TMDL folder structure (fallback mode)."""
    reports_dir = root / "reports"
    if not reports_dir.exists():
        logger.info("_walk_tmdl: %s not found, using root directly", reports_dir)
        reports_dir = root  # try root directly
    else:
        logger.info("_walk_tmdl: scanning reports_dir=%s", reports_dir)

    discovered = []
    for report_dir in sorted(reports_dir.iterdir()):
        if not report_dir.is_dir():
            continue
        logger.info("_walk_tmdl: checking dir=%s", report_dir.name)
        report = _scan_tmdl_report_folder(report_dir)
        if report:
            logger.info("_walk_tmdl: discovered report '%s' with %d tables", report.name, len(report.tables))
            discovered.append(report)
        else:
            logger.info("_walk_tmdl: skipped %s (no semantic model found)", report_dir.name)

    logger.info("_walk_tmdl: total discovered=%d", len(discovered))
    return discovered


# Keep the old TMDL walker as fallback
def walk_tmdl_root(root_path: str | Path) -> list[DiscoveredReport]:
    """Walk TMDL root folder (legacy, kept for tests)."""
    root = Path(root_path)
    reports_dir = root / "reports"
    discovered = []

    if not reports_dir.exists():
        return discovered

    for report_dir in sorted(reports_dir.iterdir()):
        if not report_dir.is_dir():
            continue
        report = _scan_tmdl_report_folder(report_dir)
        if report:
            discovered.append(report)

    return discovered


def _scan_tmdl_report_folder(report_dir: Path) -> DiscoveredReport | None:
    """Scan a single report folder for its semantic model definition."""
    report_name = report_dir.name

    semantic_dirs = list(report_dir.glob("*.SemanticModel"))
    if not semantic_dirs:
        semantic_dirs = [
            d for d in report_dir.iterdir()
            if d.is_dir() and d.name.lower().endswith(".semanticmodel")
        ]
    if not semantic_dirs:
        return None

    semantic_dir = semantic_dirs[0]
    definition_dir = semantic_dir / "Definition"
    if not definition_dir.exists():
        definition_dir = semantic_dir / "definition"
    if not definition_dir.exists():
        return None

    tables_dir = definition_dir / "Tables"
    if not tables_dir.exists():
        tables_dir = definition_dir / "tables"
    if not tables_dir.exists():
        return None

    expressions = {}
    expr_file = definition_dir / "expressions.tmdl"
    if expr_file.exists():
        expressions = parse_expressions_file(expr_file)

    tables = []
    for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
        parsed = parse_tmdl_file(tmdl_file)
        if parsed:
            tables.append(parsed)

    if not tables:
        return None

    business_owner = None
    report_owner = None
    # Collect measures from TMDL tables
    from app.scanner.pbix_parser import MeasureInfo
    measures = []
    for t in tables:
        if t.is_metadata and t.metadata_value:
            if t.table_name == "Business Owner":
                business_owner = t.metadata_value
            elif t.table_name == "Report Owner":
                report_owner = t.metadata_value
        for mname, mdax in getattr(t, "measures", []):
            measures.append(MeasureInfo(table_name=t.table_name, measure_name=mname, dax_expression=mdax))

    return DiscoveredReport(
        name=report_name,
        tmdl_path=str(report_dir),
        tables=tables,
        measures=measures,
        expressions=expressions,
        business_owner=business_owner,
        report_owner=report_owner,
    )
