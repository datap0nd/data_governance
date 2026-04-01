"""
PBIX Layout parser — extracts visual-level field references from Power BI reports.

A .pbix file is a ZIP containing a Report/Layout file (UTF-16 LE JSON).
This module parses that JSON to discover which fields/measures each visual uses,
enabling visual-level lineage: source → table → field → visual (on which page).
"""

import json
import logging
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


def parse_pbix_layout(file_path: str | Path) -> ReportLayout | None:
    """Extract visual-level field references from a .pbix file.

    Opens the .pbix as a ZIP, reads Report/Layout (UTF-16 LE JSON),
    and extracts pages → visuals → field references.

    Returns None if the file cannot be parsed or has no layout.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return None

    if not zipfile.is_zipfile(str(file_path)):
        return None

    try:
        with zipfile.ZipFile(str(file_path), "r") as zf:
            # Find the Layout file — try common paths
            layout_entry = None
            for name in ("Report/Layout", "Report/layout"):
                if name in zf.namelist():
                    layout_entry = name
                    break

            if layout_entry is None:
                logger.info("No Report/Layout found in %s", file_path.name)
                return None

            raw = zf.read(layout_entry)

        # Decode UTF-16 LE (with or without BOM)
        try:
            text = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            text = raw.decode("utf-16")

        # Strip BOM if present
        if text and text[0] == "\ufeff":
            text = text[1:]

        layout_json = json.loads(text)

    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning("Failed to parse layout from %s: %s", file_path.name, e)
        return None

    return _parse_layout_json(layout_json, file_path.name)


def _parse_layout_json(layout: dict, filename: str) -> ReportLayout:
    """Parse the Layout JSON structure into our dataclasses."""
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
    """Parse a single section (page) from the Layout JSON."""
    page_name = section.get("displayName") or section.get("name", "Unknown Page")
    page_ordinal = section.get("ordinal", 0)

    page = ParsedPage(page_name=page_name, page_ordinal=page_ordinal)

    for vc in section.get("visualContainers", []):
        try:
            visual = _parse_visual_container(vc)
            if visual:
                page.visuals.append(visual)
        except Exception:
            # Skip malformed visuals silently
            pass

    return page


def _parse_visual_container(vc: dict) -> ParsedVisual | None:
    """Parse a single visualContainer from the Layout JSON."""
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

    # Extract title if set
    title = _extract_visual_title(single_visual)

    # Build alias → entity (table name) map from the From array
    alias_map = {}
    for item in proto_query.get("From", []):
        alias = item.get("Name")
        entity = item.get("Entity")
        if alias and entity:
            alias_map[alias] = entity

    # Extract field references from Select array
    field_refs = []
    for select_item in proto_query.get("Select", []):
        refs = _extract_field_ref(select_item, alias_map)
        field_refs.extend(refs)

    # Also extract from Where clause (filter conditions, slicers)
    for where_item in proto_query.get("Where", []):
        field_refs.extend(_walk_expression(where_item, alias_map))

    # Also extract from OrderBy clause
    for ob_item in proto_query.get("OrderBy", []):
        field_refs.extend(_walk_expression(ob_item, alias_map))

    # Deduplicate
    seen = set()
    unique_refs = []
    for ref in field_refs:
        key = (ref.table_name, ref.field_name)
        if key not in seen:
            seen.add(key)
            unique_refs.append(ref)

    return ParsedVisual(
        visual_id=visual_id,
        visual_type=visual_type,
        title=title,
        field_refs=unique_refs,
    )


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
            # Value is typically like "'My Title'" — strip quotes
            if val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            return val if val else None
    except (KeyError, IndexError, TypeError):
        pass
    return None


def _extract_field_ref(select_item: dict, alias_map: dict) -> list[VisualFieldRef]:
    """Extract field references from a single Select item.

    Recursively walks the expression tree to find all Column and Measure
    references, handling wrapper types like Aggregation, Filter, Arithmetic,
    Comparison, Not, And, Or, ScopedEval, DateSpan, etc.
    """
    return _walk_expression(select_item, alias_map)


def _walk_expression(obj, alias_map: dict) -> list[VisualFieldRef]:
    """Recursively walk an expression tree and extract all field references.

    Power BI expressions are deeply nested trees. Instead of handling each
    wrapper type individually, this walks the entire tree and picks out
    Column/Measure leaf nodes (which have Expression+Property) and
    HierarchyLevel nodes (which have Expression+Level).
    """
    refs = []

    if isinstance(obj, dict):
        # Leaf: Column or Measure reference -- has both Expression and Property
        if "Property" in obj and "Expression" in obj:
            ref = _resolve_source_ref(obj, alias_map)
            if ref:
                refs.append(ref)
                return refs  # Leaf node, don't recurse deeper

        # Leaf: HierarchyLevel -- has Expression and Level
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

        # Wrapper node -- recurse into all dict values
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
