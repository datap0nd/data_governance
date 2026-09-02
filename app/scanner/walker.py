"""
Folder walker for report discovery.

Discovers both providers in one bounded pass:
1. PBIX files parsed with PBIXRay
2. TMDL semantic-model exports parsed from their folder structure

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
    discovery: dict = field(default_factory=dict)


MAX_DISCOVERY_DEPTH = 4


def _bounded_directories(root: Path, max_depth: int = MAX_DISCOVERY_DEPTH):
    """Yield directories without recursively walking an unbounded network share."""
    pending = [(root, 0)]
    while pending:
        folder, depth = pending.pop(0)
        yield folder, depth
        if depth >= max_depth:
            continue
        try:
            children = sorted(
                (item for item in folder.iterdir() if item.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        except (OSError, PermissionError):
            continue
        pending.extend((child, depth + 1) for child in children)


def _discover_pbix_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for folder, _depth in _bounded_directories(root):
        try:
            found.extend(
                item for item in folder.iterdir()
                if item.is_file() and item.suffix.casefold() == ".pbix"
            )
        except (OSError, PermissionError):
            continue
    return sorted(set(found), key=lambda item: str(item).casefold())


def _discover_tmdl_report_dirs(root: Path) -> list[Path]:
    parents: dict[str, Path] = {}
    for folder, _depth in _bounded_directories(root):
        if not folder.name.casefold().endswith(".semanticmodel"):
            continue
        definition = folder / "Definition"
        if not definition.is_dir():
            definition = folder / "definition"
        tables = definition / "Tables"
        if not tables.is_dir():
            tables = definition / "tables"
        if not tables.is_dir():
            continue
        parent = folder.parent
        parents[str(parent.resolve()).casefold()] = parent
    return sorted(parents.values(), key=lambda item: str(item).casefold())


def _snapshot_mtime(report: DiscoveredReport) -> float | None:
    path = Path(report.tmdl_path)
    try:
        if path.is_file():
            return path.stat().st_mtime
        mtimes = [
            item.stat().st_mtime
            for item in path.rglob("*.tmdl")
            if item.is_file()
        ]
        return max(mtimes) if mtimes else path.stat().st_mtime
    except OSError as exc:
        logger.warning("Could not read snapshot metadata for %s: %s", report.tmdl_path, exc)
        return None


def _logical_report_key(report: DiscoveredReport, root: Path | None = None) -> str:
    """Return a path-qualified provider key for one logical report."""
    path = Path(report.tmdl_path)
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(root.resolve()) if root is not None else None
    except (OSError, ValueError):
        relative = None
    if relative is None:
        return report.name.strip().casefold()
    if relative.suffix.casefold() == ".pbix":
        relative = relative.with_suffix("")
    return "/".join(part.casefold() for part in relative.parts)


def _provider_snapshot_complete(report: DiscoveredReport) -> bool:
    discovery = getattr(report, "discovery", {}) or {}
    return bool(discovery.get("snapshot_complete", True)) and _snapshot_mtime(report) is not None


def _merge_report_discovery(
    pbix_reports: list[DiscoveredReport],
    tmdl_reports: list[DiscoveredReport],
    *,
    root: Path | None = None,
) -> list[DiscoveredReport]:
    """Merge provider snapshots deterministically without unioning model tables."""
    grouped: dict[str, dict[str, DiscoveredReport]] = {}
    for provider, reports in (("pbix", pbix_reports), ("tmdl", tmdl_reports)):
        for report in reports:
            key = _logical_report_key(report, root)
            current = grouped.setdefault(key, {}).get(provider)
            report_rank = (
                _provider_snapshot_complete(report),
                _snapshot_mtime(report) or float("-inf"),
            )
            current_rank = (
                _provider_snapshot_complete(current),
                _snapshot_mtime(current) or float("-inf"),
            ) if current is not None else None
            if current is None or report_rank >= current_rank:
                grouped[key][provider] = report

    merged: list[DiscoveredReport] = []
    for key in sorted(grouped):
        providers = grouped[key]
        pbix = providers.get("pbix")
        tmdl = providers.get("tmdl")
        if pbix is None or tmdl is None:
            report = pbix or tmdl
            provider = "pbix" if pbix is not None else "tmdl"
            prior = getattr(report, "discovery", {}) or {}
            modified_at = _snapshot_mtime(report)
            report.discovery = {
                **prior,
                "model_provider": provider,
                "providers_found": [provider],
                "model_modified_at": modified_at,
                "snapshot_complete": bool(prior.get("snapshot_complete", True)) and modified_at is not None,
            }
            merged.append(report)
            continue

        pbix_mtime = _snapshot_mtime(pbix)
        tmdl_mtime = _snapshot_mtime(tmdl)
        complete_candidates = [
            report for report in (pbix, tmdl) if _provider_snapshot_complete(report)
        ]
        candidates = complete_candidates or [pbix, tmdl]
        # TMDL wins exact ties because its text model is inspectable and is
        # normally the intentional source-control export.
        chosen = max(
            candidates,
            key=lambda report: (
                _snapshot_mtime(report) or float("-inf"),
                1 if report is tmdl else 0,
            ),
        )
        chosen_provider = "tmdl" if chosen is tmdl else "pbix"
        if getattr(pbix, "layout", None) is not None:
            chosen.layout = pbix.layout
            chosen.layout_diagnostic = getattr(pbix, "layout_diagnostic", None)
        pbix_tables = {table.table_name.casefold() for table in pbix.tables}
        tmdl_tables = {table.table_name.casefold() for table in tmdl.tables}
        chosen_prior = getattr(chosen, "discovery", {}) or {}
        provider_warnings = []
        for provider_name, candidate in (("pbix", pbix), ("tmdl", tmdl)):
            if _provider_snapshot_complete(candidate):
                continue
            candidate_issues = (getattr(candidate, "discovery", {}) or {}).get("issues") or []
            provider_warnings.append({
                "provider": provider_name,
                "issues": [str(item) for item in candidate_issues[:20]],
            })
        chosen.discovery = {
            **chosen_prior,
            "model_provider": chosen_provider,
            "providers_found": ["pbix", "tmdl"],
            "pbix_path": pbix.tmdl_path,
            "tmdl_path": tmdl.tmdl_path,
            "pbix_modified_at": pbix_mtime,
            "tmdl_modified_at": tmdl_mtime,
            "table_sets_disagree": pbix_tables != tmdl_tables,
            "pbix_only_tables": sorted(pbix_tables - tmdl_tables),
            "tmdl_only_tables": sorted(tmdl_tables - pbix_tables),
            "snapshot_complete": _provider_snapshot_complete(chosen),
            "provider_warnings": provider_warnings,
        }
        merged.append(chosen)

    # The reports table intentionally remains name-keyed. Do not silently
    # collapse two different folders onto that single row; surface one
    # incomplete placeholder so reconciliation preserves the trusted row.
    by_name: dict[str, list[DiscoveredReport]] = {}
    for report in merged:
        by_name.setdefault(report.name.strip().casefold(), []).append(report)
    result: list[DiscoveredReport] = []
    for name_key in sorted(by_name):
        matches = by_name[name_key]
        if len(matches) == 1:
            result.append(matches[0])
            continue
        placeholder = matches[0]
        ambiguous_paths = sorted(str(item.tmdl_path) for item in matches)
        placeholder.tables = []
        placeholder.measures = []
        placeholder.discovery = {
            "model_provider": "ambiguous",
            "providers_found": sorted({
                str((getattr(item, "discovery", {}) or {}).get("model_provider") or "unknown")
                for item in matches
            }),
            "snapshot_complete": False,
            "ambiguous_provider": True,
            "candidate_count": len(matches),
            "issues": ["Same-named reports were discovered at different logical paths"],
            "ambiguous_paths": ambiguous_paths,
        }
        result.append(placeholder)
    return result


def walk_reports_root(root_path: str | Path) -> list[DiscoveredReport]:
    """Walk the reports root folder and discover all reports.

    PBIX and TMDL candidates are parsed together and matching report snapshots
    are resolved deterministically.
    """
    root = Path(root_path).resolve()
    logger.info("walk_reports_root: root_path=%s resolved=%s exists=%s", root_path, root, root.exists())
    if not root.exists():
        logger.error("Reports root not found: %s", root)
        return []

    pbix_files = _discover_pbix_files(root)
    tmdl_dirs = _discover_tmdl_report_dirs(root)
    logger.info(
        "Discovery candidates: %d PBIX file(s), %d TMDL report folder(s)",
        len(pbix_files), len(tmdl_dirs),
    )
    pbix_reports = _walk_pbix(pbix_files) if pbix_files else []
    tmdl_reports = _walk_tmdl(root, report_dirs=tmdl_dirs) if tmdl_dirs else []
    return _merge_report_discovery(pbix_reports, tmdl_reports, root=root)


def diagnose_reports_root(root_path: str | Path) -> dict:
    """Walk through the discovery logic step by step and return diagnostics.

    Returns a dict with detailed info about what the scanner sees,
    useful for debugging why 0 reports are found.
    """
    raw_path = str(root_path)
    root = Path(root_path).resolve()
    result = {
        "raw_path": raw_path,
        "resolved_path": str(root),
        "exists": root.exists(),
        "is_dir": root.is_dir() if root.exists() else False,
        "mode": None,
        "steps": [],
        "pbix_files": [],
        "tmdl_folders": [],
        "errors": [],
        "directory_listing": [],
    }

    if not root.exists():
        result["errors"].append(f"Path does not exist: {root}")
        return result

    if not root.is_dir():
        result["errors"].append(f"Path is not a directory: {root}")
        return result

    # List root contents
    try:
        for entry in sorted(root.iterdir()):
            entry_info = {
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "is_file": entry.is_file(),
            }
            if entry.is_file():
                try:
                    entry_info["size_bytes"] = entry.stat().st_size
                except OSError:
                    pass
            result["directory_listing"].append(entry_info)
    except PermissionError as e:
        result["errors"].append(f"Permission denied listing directory: {e}")
        return result

    # Step 1: Check for .pbix files at root
    pbix_root = list(root.glob("*.pbix"))
    result["steps"].append({
        "action": f"Glob *.pbix in {root}",
        "found": len(pbix_root),
        "files": [f.name for f in pbix_root],
    })

    # Step 2: Check for .pbix files one level deep
    pbix_sub = list(root.glob("*/*.pbix"))
    result["steps"].append({
        "action": f"Glob */*.pbix in {root}",
        "found": len(pbix_sub),
        "files": [str(f.relative_to(root)) for f in pbix_sub],
    })

    all_pbix = _discover_pbix_files(root)
    if all_pbix:
        result["mode"] = "pbix"
        result["pbix_files"] = [str(f.relative_to(root)) for f in all_pbix]
        # Try opening each one with PBIXRay directly to surface real errors
        for pbix_path in all_pbix:
            # Step 1: Check file size
            try:
                size_kb = pbix_path.stat().st_size / 1024
                result["steps"].append({
                    "action": f"File {pbix_path.name}",
                    "result": f"{size_kb:.0f} KB",
                })
            except Exception as e:
                result["steps"].append({
                    "action": f"File {pbix_path.name}",
                    "result": f"Cannot stat: {e}",
                })
                result["errors"].append(f"{pbix_path.name}: cannot read file - {e}")
                continue

            # Step 2: Try PBIXRay directly (not through parse_pbix_file)
            try:
                from pbixray import PBIXRay
                model = PBIXRay(str(pbix_path))
                table_count = len(model.tables) if model.tables else 0
                pq_count = len(model.power_query) if model.power_query is not None else 0
                result["steps"].append({
                    "action": f"PBIXRay {pbix_path.name}",
                    "result": f"OK - {table_count} tables, {pq_count} power query expressions",
                })
            except ImportError:
                result["steps"].append({
                    "action": f"PBIXRay {pbix_path.name}",
                    "result": "ERROR: pbixray package not installed",
                })
                result["errors"].append(f"pbixray is not installed (pip install pbixray)")
                break
            except Exception as e:
                result["steps"].append({
                    "action": f"PBIXRay {pbix_path.name}",
                    "result": f"ERROR: {type(e).__name__}: {e}",
                })
                result["errors"].append(f"{pbix_path.name}: {type(e).__name__}: {e}")
    if not all_pbix:
        result["mode"] = "tmdl"
        result["steps"].append({
            "action": "No .pbix files found; scanning TMDL exports",
            "found": 0,
        })

    # Check for reports/ subdirectory
    reports_dir = root / "reports"
    if reports_dir.exists():
        scan_dir = reports_dir
        result["steps"].append({
            "action": f"Found reports/ subdirectory at {reports_dir}",
            "found": 1,
        })
    else:
        scan_dir = root
        result["steps"].append({
            "action": f"No reports/ subdirectory, scanning root directly: {root}",
            "found": 0,
        })

    # Walk bounded TMDL candidates, including nested project exports.
    diagnostic_dirs = _discover_tmdl_report_dirs(root)
    entries = diagnostic_dirs or [entry for entry in sorted(scan_dir.iterdir()) if entry.is_dir()]
    for entry in entries:
        if not entry.is_dir():
            continue

        folder_diag = {
            "folder": entry.name,
            "has_semantic_model": False,
            "has_definition": False,
            "has_tables": False,
            "tmdl_file_count": 0,
            "semantic_dirs_found": [],
            "skip_reason": None,
            "contents": [],
        }

        # List folder contents for debugging
        try:
            folder_diag["contents"] = [e.name for e in sorted(entry.iterdir())]
        except PermissionError:
            folder_diag["skip_reason"] = "Permission denied"
            result["tmdl_folders"].append(folder_diag)
            continue

        # Check for SemanticModel directory
        semantic_dirs = list(entry.glob("*.SemanticModel"))
        if not semantic_dirs:
            semantic_dirs = [
                d for d in entry.iterdir()
                if d.is_dir() and d.name.lower().endswith(".semanticmodel")
            ]

        if not semantic_dirs:
            folder_diag["skip_reason"] = "No *.SemanticModel directory found"
            result["tmdl_folders"].append(folder_diag)
            continue

        folder_diag["has_semantic_model"] = True
        folder_diag["semantic_dirs_found"] = [d.name for d in semantic_dirs]

        semantic_dir = semantic_dirs[0]
        definition_dir = semantic_dir / "Definition"
        if not definition_dir.exists():
            definition_dir = semantic_dir / "definition"
        if not definition_dir.exists():
            folder_diag["skip_reason"] = f"No Definition/ dir inside {semantic_dir.name}"
            result["tmdl_folders"].append(folder_diag)
            continue

        folder_diag["has_definition"] = True

        tables_dir = definition_dir / "Tables"
        if not tables_dir.exists():
            tables_dir = definition_dir / "tables"
        if not tables_dir.exists():
            folder_diag["skip_reason"] = f"No Tables/ dir inside {definition_dir.name}"
            result["tmdl_folders"].append(folder_diag)
            continue

        folder_diag["has_tables"] = True
        tmdl_files = list(tables_dir.glob("*.tmdl"))
        folder_diag["tmdl_file_count"] = len(tmdl_files)

        if not tmdl_files:
            folder_diag["skip_reason"] = "Tables/ dir exists but contains no .tmdl files"
        else:
            folder_diag["skip_reason"] = None  # This folder should parse successfully

        result["tmdl_folders"].append(folder_diag)

    if all_pbix and any(not item.get("skip_reason") for item in result["tmdl_folders"]):
        result["mode"] = "mixed"
    return result


def _walk_pbix(pbix_files: list[Path]) -> list[DiscoveredReport]:
    """Parse .pbix files using PBIXRay."""
    from app.scanner.pbix_parser import parse_pbix_file

    discovered = []
    for pbix_path in sorted(pbix_files):
        logger.info("Parsing: %s", pbix_path.name)
        report = parse_pbix_file(pbix_path)
        if report:
            dr = DiscoveredReport(
                name=report.name,
                tmdl_path=report.file_path,
                tables=report.tables,
                measures=report.measures,
                business_owner=report.business_owner,
                report_owner=report.report_owner,
                layout=report.layout,
            )
            dr.layout_diagnostic = getattr(report, "layout_diagnostic", None)
            dr.discovery = {
                "snapshot_complete": bool(getattr(report, "snapshot_complete", True)),
                "issues": list(getattr(report, "parse_issues", []) or []),
            }
            discovered.append(dr)
        else:
            logger.warning("Could not parse: %s", pbix_path.name)
            discovered.append(DiscoveredReport(
                name=pbix_path.stem,
                tmdl_path=str(pbix_path),
                discovery={
                    "snapshot_complete": False,
                    "issues": ["PBIX file could not be opened or parsed"],
                },
            ))

    return discovered


def _walk_tmdl(
    root: Path,
    *,
    report_dirs: list[Path] | None = None,
) -> list[DiscoveredReport]:
    """Walk the TMDL folder candidates found during mixed discovery."""
    reports_dir = root / "reports"
    if not reports_dir.exists():
        logger.info("_walk_tmdl: %s not found, using root directly", reports_dir)
        reports_dir = root  # try root directly
    else:
        logger.info("_walk_tmdl: scanning reports_dir=%s", reports_dir)

    discovered = []
    candidates = report_dirs
    if candidates is None:
        candidates = _discover_tmdl_report_dirs(root)
    if not candidates and reports_dir.is_dir():
        candidates = [item for item in sorted(reports_dir.iterdir()) if item.is_dir()]
    for report_dir in candidates:
        if not report_dir.is_dir():
            continue
        logger.info("_walk_tmdl: checking dir=%s", report_dir.name)
        try:
            report = _scan_tmdl_report_folder(report_dir)
        except OSError as exc:
            logger.warning("Could not read TMDL report %s: %s", report_dir, exc)
            report = DiscoveredReport(
                name=report_dir.name,
                tmdl_path=str(report_dir),
                discovery={
                    "snapshot_complete": False,
                    "issues": ["TMDL report files could not be read"],
                },
            )
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

    issues: list[str] = []
    expressions = {}
    expr_file = definition_dir / "expressions.tmdl"
    if expr_file.exists():
        try:
            expressions = parse_expressions_file(expr_file)
        except OSError as exc:
            logger.warning("Could not read %s: %s", expr_file, exc)
            issues.append("expressions.tmdl could not be read")

    tables = []
    try:
        tmdl_files = sorted(tables_dir.glob("*.tmdl"))
    except OSError as exc:
        logger.warning("Could not enumerate TMDL tables in %s: %s", tables_dir, exc)
        tmdl_files = []
        issues.append("TMDL table files could not be enumerated")
    if not tmdl_files:
        issues.append("TMDL Tables folder contained no table files")
    for tmdl_file in tmdl_files:
        try:
            parsed = parse_tmdl_file(tmdl_file)
        except (OSError, UnicodeError) as exc:
            logger.warning("Could not read TMDL table %s: %s", tmdl_file, exc)
            parsed = None
            issues.append(f"Could not read table file {tmdl_file.name}")
        if parsed:
            tables.append(parsed)
        else:
            logger.warning("Could not parse TMDL table %s", tmdl_file)
            issues.append(f"Could not parse table file {tmdl_file.name}")

    snapshot_complete = bool(tables) and not issues

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
        discovery={
            "snapshot_complete": snapshot_complete,
            "issues": issues,
        },
    )
