"""
PBIX Layout parser - extracts visual-level field references from Power BI reports.

Supports two internal .pbix formats:
1. Classic: single Report/Layout entry (UTF-16 LE or UTF-8 JSON blob)
2. PBIR (Power BI Report): definition/pages/*/page.json + definition/pages/*/visuals/*/visual.json

This module parses the layout data to discover which fields/measures each visual uses,
enabling visual-level lineage: source -> table -> field -> visual (on which page).
"""

import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VisualFieldRef:
    """A field reference used by a visual."""
    table_name: str
    field_name: str


@dataclass
class ParsedVisual:
    """A visual extracted from the Layout JSON."""
    visual_id: str
    visual_type: str
    title: str | None = None
    field_refs: list[VisualFieldRef] = field(default_factory=list)


@dataclass
class ParsedPage:
    """A page (section) from the report layout."""
    page_name: str
    page_ordinal: int = 0
    visuals: list[ParsedVisual] = field(default_factory=list)


@dataclass
class ReportLayout:
    """Full layout extracted from a .pbix file."""
    pages: list[ParsedPage] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_pbix_layout(file_path: str | Path, diagnostics: list[str] | None = None) -> ReportLayout | None:
    """Extract visual-level field references from a .pbix file.

    Tries classic Report/Layout first, then PBIR definition/ folder format.

    *diagnostics* is an optional list that gets filled with diagnostic messages
    (useful for surfacing info in the scan log when layout parsing fails).

    Returns None if the file cannot be parsed or has no layout.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return None

    if not zipfile.is_zipfile(str(file_path)):
        if diagnostics is not None:
            diagnostics.append("File is not a valid ZIP archive")
        return None

    try:
        with zipfile.ZipFile(str(file_path), "r") as zf:
            names = zf.namelist()
            names_lower = {n.lower(): n for n in names}

            # --- Strategy 1: Classic Report/Layout ---
            layout_entry = None
            for candidate in ("report/layout", "report/layout.json"):
                if candidate in names_lower:
                    layout_entry = names_lower[candidate]
                    break

            if layout_entry:
                logger.info("Classic layout found in %s: %s", file_path.name, layout_entry)
                raw = zf.read(layout_entry)
                layout_json = _decode_json(raw)
                if layout_json is None:
                    msg = f"Could not decode layout from {file_path.name} ({len(raw)} bytes)"
                    logger.warning(msg)
                    if diagnostics is not None:
                        diagnostics.append(msg)
                    return None
                return _parse_layout_json(layout_json, file_path.name)

            # --- Strategy 2: PBIR format (definition/pages/...) ---
            page_entries = sorted([n for n in names if _is_pbir_page(n)])
            visual_entries = sorted([n for n in names if _is_pbir_visual(n)])

            if page_entries or visual_entries:
                logger.info("PBIR format detected in %s: %d page entries, %d visual entries",
                            file_path.name, len(page_entries), len(visual_entries))
                if diagnostics is not None:
                    diagnostics.append(f"PBIR format: {len(page_entries)} pages, {len(visual_entries)} visuals")
                return _parse_pbir_layout(zf, page_entries, visual_entries, file_path.name)

            # --- Nothing found ---
            relevant = [n for n in names if any(k in n.lower() for k in
                        ("layout", "report", "page", "visual", "definition"))]
            all_top = sorted({n.split("/")[0] for n in names if "/" in n})
            msg = (f"No layout data found in {file_path.name}. "
                   f"Top-level folders: {all_top}. "
                   f"Relevant entries: {relevant[:20]}")
            logger.warning(msg)
            if diagnostics is not None:
                diagnostics.append(msg)
            return None

    except zipfile.BadZipFile as e:
        msg = f"Failed to open {file_path.name} as ZIP: {e}"
        logger.warning(msg)
        if diagnostics is not None:
            diagnostics.append(msg)
        return None


# ---------------------------------------------------------------------------
# Classic format: single Report/Layout JSON blob
# ---------------------------------------------------------------------------

def _decode_json(raw: bytes) -> dict | None:
    """Try multiple encodings to decode a JSON blob."""
    for encoding in ("utf-16-le", "utf-16", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(encoding)
            if text and text[0] == "\ufeff":
                text = text[1:]
            return json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
    return None


def _parse_layout_json(layout: dict, filename: str) -> ReportLayout:
    """Parse the classic Layout JSON structure into dataclasses."""
    result = ReportLayout()

    sections = layout.get("sections", [])
    for section in sections:
        try:
            page = _parse_section(section)
            if page:
                result.pages.append(page)
        except Exception as e:
            logger.warning("Failed to parse page in %s: %s", filename, e)

    total_visuals = sum(len(p.visuals) for p in result.pages)
    total_fields = sum(len(v.field_refs) for p in result.pages for v in p.visuals)
    logger.info("Layout parsed from %s: %d pages, %d visuals, %d field refs",
                filename, len(result.pages), total_visuals, total_fields)

    return result


def _parse_section(section: dict) -> ParsedPage | None:
    """Parse a single section (page) from the classic Layout JSON."""
    page_name = section.get("displayName") or section.get("name", "Unknown Page")
    page_ordinal = section.get("ordinal", 0)

    page = ParsedPage(page_name=page_name, page_ordinal=page_ordinal)

    for vc in section.get("visualContainers", []):
        try:
            visual = _parse_visual_container(vc)
            if visual:
                page.visuals.append(visual)
        except Exception as e:
            logger.debug("Skipped malformed visual on page '%s': %s", page_name, e)

    return page


def _parse_visual_container(vc: dict) -> ParsedVisual | None:
    """Parse a single visualContainer from the classic Layout JSON."""
    config_str = vc.get("config")
    if not config_str:
        return None

    config = json.loads(config_str) if isinstance(config_str, str) else config_str

    # Extract visual ID and type
    visual_id = config.get("name", "")
    single_visual = config.get("singleVisual", {})
    visual_type = single_visual.get("visualType", "unknown")

    # Skip visuals without data queries (text boxes, images, shapes, etc.)
    proto_query = single_visual.get("prototypeQuery")
    if not proto_query:
        return None

    title = _extract_visual_title(single_visual)
    field_refs = _extract_query_fields(proto_query)

    return ParsedVisual(
        visual_id=visual_id,
        visual_type=visual_type,
        title=title,
        field_refs=field_refs,
    )


# ---------------------------------------------------------------------------
# PBIR format: definition/pages/*/page.json + visuals/*/visual.json
# ---------------------------------------------------------------------------

def _is_pbir_page(entry_name: str) -> bool:
    """Check if a ZIP entry is a PBIR page definition."""
    lower = entry_name.lower()
    return lower.startswith("definition/pages/") and lower.endswith("/page.json")


def _is_pbir_visual(entry_name: str) -> bool:
    """Check if a ZIP entry is a PBIR visual definition."""
    lower = entry_name.lower()
    return (lower.startswith("definition/") and
            "/visuals/" in lower and
            lower.endswith("/visual.json"))


def _parse_pbir_layout(zf: zipfile.ZipFile, page_entries: list[str],
                        visual_entries: list[str], filename: str) -> ReportLayout:
    """Parse PBIR format layout from individual page/visual JSON files."""
    result = ReportLayout()

    # Build page folder -> ParsedPage map
    page_map: dict[str, ParsedPage] = {}
    for entry in page_entries:
        try:
            raw = zf.read(entry)
            page_json = json.loads(raw.decode("utf-8-sig"))
            page_name = page_json.get("displayName") or page_json.get("name", "Unknown Page")
            page_ordinal = page_json.get("ordinal", 0)
            # Extract the page folder path (e.g. "definition/pages/ReportSection1")
            page_folder = entry.rsplit("/", 1)[0]
            page = ParsedPage(page_name=page_name, page_ordinal=page_ordinal)
            page_map[page_folder.lower()] = page
        except Exception as e:
            logger.warning("Failed to parse PBIR page %s in %s: %s", entry, filename, e)

    # Parse visuals and assign to pages
    orphan_visuals = []
    for entry in visual_entries:
        try:
            raw = zf.read(entry)
            visual_json = json.loads(raw.decode("utf-8-sig"))
            visual = _parse_pbir_visual(visual_json)
            if not visual:
                continue

            # Find parent page by matching path prefix
            # e.g. "definition/pages/ReportSection1/visuals/abc/visual.json"
            #   -> page folder "definition/pages/ReportSection1"
            page_folder = _extract_pbir_page_folder(entry)
            if page_folder and page_folder.lower() in page_map:
                page_map[page_folder.lower()].visuals.append(visual)
            else:
                orphan_visuals.append(visual)
        except Exception as e:
            logger.debug("Failed to parse PBIR visual %s in %s: %s", entry, filename, e)

    # If we found visuals but no page entries, create a default page
    if orphan_visuals and not page_map:
        default_page = ParsedPage(page_name="Page 1", page_ordinal=0, visuals=orphan_visuals)
        page_map["__default__"] = default_page
    elif orphan_visuals:
        # Add orphans to the first page
        first_page = next(iter(page_map.values()))
        first_page.visuals.extend(orphan_visuals)

    # Build result sorted by ordinal
    result.pages = sorted(page_map.values(), key=lambda p: p.page_ordinal)

    total_visuals = sum(len(p.visuals) for p in result.pages)
    total_fields = sum(len(v.field_refs) for p in result.pages for v in p.visuals)
    logger.info("PBIR layout parsed from %s: %d pages, %d visuals, %d field refs",
                filename, len(result.pages), total_visuals, total_fields)

    return result


def _extract_pbir_page_folder(visual_entry: str) -> str | None:
    """Extract the page folder from a PBIR visual entry path.

    'definition/pages/ReportSection1/visuals/abc/visual.json'
    -> 'definition/pages/ReportSection1'
    """
    m = re.match(r'^(definition/pages/[^/]+)', visual_entry, re.IGNORECASE)
    return m.group(1) if m else None


def _parse_pbir_visual(visual_json: dict) -> ParsedVisual | None:
    """Parse a single PBIR visual.json into a ParsedVisual."""
    visual_id = visual_json.get("name", "")
    visual_obj = visual_json.get("visual", {})
    visual_type = visual_obj.get("visualType", "unknown")

    # Try to find the query data - multiple possible locations
    query = _find_query(visual_json)
    if not query:
        return None

    title = _extract_pbir_visual_title(visual_json)
    field_refs = _extract_query_fields(query)

    return ParsedVisual(
        visual_id=visual_id,
        visual_type=visual_type,
        title=title,
        field_refs=field_refs,
    )


def _find_query(obj) -> dict | None:
    """Recursively search for a query object containing From and Select arrays.

    Handles multiple PBIR query structures:
    - visual.query.Commands[].SemanticQueryDataShapeCommand.Query.{From, Select}
    - visual.prototypeQuery.{From, Select}
    - visual.query.{From, Select}
    """
    if isinstance(obj, dict):
        # Check if this dict has both From (list) and Select (list)
        from_val = obj.get("From")
        select_val = obj.get("Select")
        if isinstance(from_val, list) and isinstance(select_val, list):
            return obj

        # Recurse into values
        for value in obj.values():
            result = _find_query(value)
            if result:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = _find_query(item)
            if result:
                return result

    return None


def _extract_pbir_visual_title(visual_json: dict) -> str | None:
    """Extract title from a PBIR visual.json."""
    visual_obj = visual_json.get("visual", {})

    # Try vcObjects path (same as classic format)
    title = _extract_visual_title(visual_obj)
    if title:
        return title

    # Try objects path
    try:
        objects = visual_obj.get("objects", {})
        title_list = objects.get("title", [])
        if title_list and isinstance(title_list, list):
            props = title_list[0].get("properties", {})
            text_obj = props.get("text", {})
            expr = text_obj.get("expr", {})
            literal = expr.get("Literal", {})
            val = literal.get("Value", "")
            if val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            return val if val else None
    except (KeyError, IndexError, TypeError):
        pass

    return None


# ---------------------------------------------------------------------------
# Shared: field reference extraction
# ---------------------------------------------------------------------------

def _extract_query_fields(query: dict) -> list[VisualFieldRef]:
    """Extract deduplicated field references from a query dict with From/Select."""
    alias_map = {}
    for item in query.get("From", []):
        alias = item.get("Name")
        entity = item.get("Entity")
        if alias and entity:
            alias_map[alias] = entity

    field_refs = []

    for select_item in query.get("Select", []):
        field_refs.extend(_walk_expression(select_item, alias_map))

    for where_item in query.get("Where", []):
        field_refs.extend(_walk_expression(where_item, alias_map))

    for ob_item in query.get("OrderBy", []):
        field_refs.extend(_walk_expression(ob_item, alias_map))

    # Deduplicate
    seen = set()
    unique_refs = []
    for ref in field_refs:
        key = (ref.table_name, ref.field_name)
        if key not in seen:
            seen.add(key)
            unique_refs.append(ref)

    return unique_refs


def _extract_visual_title(single_visual: dict) -> str | None:
    """Try to extract user-set title from visual config."""
    try:
        vc_objects = single_visual.get("vcObjects", {})
        title_list = vc_objects.get("title", [])
        if title_list and isinstance(title_list, list):
            props = title_list[0].get("properties", {})
            text_obj = props.get("text", {})
            expr = text_obj.get("expr", {})
            literal = expr.get("Literal", {})
            val = literal.get("Value", "")
            if val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            return val if val else None
    except (KeyError, IndexError, TypeError):
        pass
    return None


def _extract_field_ref(select_item: dict, alias_map: dict) -> list[VisualFieldRef]:
    """Extract field references from a single Select item."""
    return _walk_expression(select_item, alias_map)


def _walk_expression(obj, alias_map: dict) -> list[VisualFieldRef]:
    """Recursively walk an expression tree and extract all field references.

    Power BI expressions are deeply nested trees. This walks the entire tree
    and picks out Column/Measure leaf nodes (Expression+Property) and
    HierarchyLevel nodes (Expression+Level).
    """
    refs = []

    if isinstance(obj, dict):
        # Leaf: Column or Measure reference - has both Expression and Property
        if "Property" in obj and "Expression" in obj:
            ref = _resolve_source_ref(obj, alias_map)
            if ref:
                refs.append(ref)
                return refs

        # Leaf: HierarchyLevel - has Expression and Level
        if "Level" in obj and "Expression" in obj:
            hierarchy = obj["Expression"].get("Hierarchy", {})
            inner_expr = hierarchy.get("Expression", {})
            source_ref = inner_expr.get("SourceRef", {})
            alias = source_ref.get("Source")
            level = obj.get("Level")
            if alias and level and alias in alias_map:
                refs.append(VisualFieldRef(
                    table_name=alias_map[alias],
                    field_name=level,
                ))
                return refs

        # Wrapper node - recurse into all dict values
        for value in obj.values():
            if isinstance(value, (dict, list)):
                refs.extend(_walk_expression(value, alias_map))

    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                refs.extend(_walk_expression(item, alias_map))

    return refs


def _resolve_source_ref(expr_obj: dict, alias_map: dict) -> VisualFieldRef | None:
    """Resolve a Column or Measure expression to a VisualFieldRef."""
    inner_expr = expr_obj.get("Expression", {})
    source_ref = inner_expr.get("SourceRef", {})
    alias = source_ref.get("Source")
    prop = expr_obj.get("Property")

    if alias and prop and alias in alias_map:
        return VisualFieldRef(
            table_name=alias_map[alias],
            field_name=prop,
        )
    return None
