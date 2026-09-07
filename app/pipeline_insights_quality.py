"""Evidence digests and quality gates for Pipeline connection explanations.

The local model only sees supplied evidence, so this module turns a stored
SQL or Power Query definition into a small, deterministic digest of what the
definition actually does (relations, joins, filters, grouping, calculations).
The digest is sent with the prompt as a checklist, used to reject answers that
skip any evidenced operation, and rendered into the fallback text so the
Pipelines page stays useful even when no validated AI text exists.
"""

from __future__ import annotations

import re
from typing import Any


MIN_TECHNICAL_CHARS = 80
MIN_BUSINESS_CHARS = 40
MAX_PREDICATE_CHARS = 220

_SQL_KEYWORDS = frozenset({
    "on", "using", "where", "group", "order", "limit", "having", "window",
    "union", "except", "intersect", "left", "right", "full", "inner", "cross",
    "natural", "join", "outer", "lateral", "as", "select", "from", "with",
    "and", "or", "not", "in", "is", "null", "case", "when", "then", "else",
    "end", "distinct", "offset", "fetch", "for", "values", "returning",
    "true", "false", "between", "like", "ilike", "exists", "any", "all", "some",
    "coalesce", "nullif", "cast", "extract", "interval", "date", "current_date",
    "now", "asc", "desc", "nulls", "first", "last", "array", "text", "integer",
    "bigint", "smallint", "numeric", "boolean", "character", "varying", "varchar",
    "timestamp", "timestamptz", "without", "with", "time", "zone", "double",
    "precision", "real", "json", "jsonb", "uuid", "regclass", "array_agg",
})
_CLAUSE_BOUNDARY = re.compile(
    r"\b(?:(?:left|right|full|inner|cross|natural)\s+(?:outer\s+)?join|join|where|"
    r"group\s+by|order\s+by|limit|having|window|union|except|intersect|on|using)\b",
    re.I,
)
_IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
_RELATION = rf"({_IDENT}(?:\.{_IDENT})?)"
_ALIAS = rf"(?:\s+(?:as\s+)?({_IDENT}))?"
_AGGREGATES = (
    "sum", "count", "avg", "min", "max", "string_agg", "array_agg", "bool_or",
    "bool_and", "percentile_cont", "percentile_disc", "json_agg", "jsonb_agg",
)
_FILTER_WORDS = ("filter", "where", "predicate", "only ", "exclud", "restrict", "limited to", "keeps rows", "rows with")
_JOIN_WORDS = ("join", "merge", "matched", "lookup")
_GROUP_WORDS = ("group", "aggregat", "sum", "count", "average", "total", "per ", "summar", "roll")
_CALC_WORDS = ("calculat", "derived", "computed", "expression", "adds", "added column", "custom column", "case ")
_GENERIC_SHAPES = (
    re.compile(r"^\s*[\w.\"]+\s+(?:feeds|supplies|provides|populates|loads)\s+(?:data\s+)?(?:to|into|for)?\s*[\w.\"]+\.?\s*$", re.I),
    re.compile(r"^\s*(?:the\s+)?(?:source|relation|table)\s+(?:feeds|supplies|provides)\s+(?:the\s+)?(?:target|downstream|materialized view|view|table)\.?\s*$", re.I),
)


def _strip_sql_comments(sql: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _unquote(identifier: str) -> str:
    return identifier.strip().strip('"')


def _short_name(relation: str) -> str:
    return _unquote(relation.split(".")[-1])


def _is_keyword(identifier: str | None) -> bool:
    return bool(identifier) and _unquote(identifier).casefold() in _SQL_KEYWORDS


def _clip(text: str, limit: int = MAX_PREDICATE_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clause_body(text: str, start: int) -> str:
    """Text from `start` to the next clause keyword at parenthesis depth zero.

    pg_get_viewdef wraps every predicate in parentheses, so a WHERE clause is
    scanned with depth tracking instead of stopping at the first ')'.
    """
    depth = 0
    index = start
    in_string = False
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "'":
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        elif char == ";":
            break
        elif depth == 0 and char.isalpha():
            match = _CLAUSE_BOUNDARY.match(text, index)
            if match and (index == 0 or not (text[index - 1].isalnum() or text[index - 1] in "_.\"")):
                break
            end = index
            while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            index = max(end, index + 1)
            continue
        index += 1
    return _unwrap(text[start:index].strip())


def _unwrap(clause: str) -> str:
    """Strip parentheses that wrap the whole clause, as pg_get_viewdef emits."""
    while clause.startswith("(") and clause.endswith(")"):
        depth = 0
        for position, char in enumerate(clause):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and position != len(clause) - 1:
                    return clause
        clause = clause[1:-1].strip()
    return clause


def _identifiers(text: str) -> list[str]:
    """Unqualified identifiers in a clause, without keywords or literals."""
    found: list[str] = []
    cleaned = re.sub(r"'(?:[^']|'')*'", " ", text)
    cleaned = re.sub(r"::\s*[A-Za-z_][\w\s]*?(?=[\s,)\]]|$)", " ", cleaned)
    for match in re.finditer(rf"{_IDENT}(?:\.{_IDENT})*", cleaned):
        token = match.group(0)
        parts = [_unquote(part) for part in re.findall(_IDENT, token)]
        if not parts or parts[-1].casefold() in _SQL_KEYWORDS:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", parts[-1]):
            continue
        if parts[-1] not in found:
            found.append(parts[-1])
    return found


def sql_facts(sql: str) -> dict[str, Any]:
    """Best-effort structural facts about a SELECT definition."""
    facts: dict[str, Any] = {
        "relations": [], "base_relation": None, "joins": [], "filters": [],
        "group_by": [], "aggregates": [], "distinct": False, "set_operations": [],
        "conditional": False, "window": False, "has_definition": bool(sql and sql.strip()),
    }
    if not facts["has_definition"]:
        return facts
    text = _strip_sql_comments(sql)
    lowered = text.casefold()

    def add_relation(name: str) -> None:
        clean = ".".join(_unquote(part) for part in re.findall(_IDENT, name))
        if clean and clean not in facts["relations"]:
            facts["relations"].append(clean)

    base = re.search(rf"\bfrom\s+(?:\(\s*)*{_RELATION}{_ALIAS}", text, re.I)
    if base and not _is_keyword(base.group(1)):
        add_relation(base.group(1))
        facts["base_relation"] = facts["relations"][0]
    join_pattern = re.compile(
        rf"\b((?:(?:left|right|full|inner|cross|natural)\s+(?:outer\s+)?)?join)\s+"
        rf"{_RELATION}{_ALIAS}"
        rf"(?:\s+(on)\s+|\s+using\s*\(([^)]*)\))?",
        re.I,
    )
    for match in join_pattern.finditer(text):
        relation, alias = match.group(2), match.group(3)
        if _is_keyword(relation):
            continue
        add_relation(relation)
        condition = _clause_body(text, match.end()) if match.group(4) else ""
        using = (match.group(5) or "").strip()
        columns = _identifiers(condition) if condition else [_unquote(c) for c in using.split(",") if c.strip()]
        facts["joins"].append({
            "type": re.sub(r"\s+", " ", match.group(1)).upper(),
            "relation": ".".join(_unquote(p) for p in re.findall(_IDENT, relation)),
            "alias": _unquote(alias) if alias and not _is_keyword(alias) else None,
            "condition": _clip(condition) if condition else (f"USING ({using})" if using else ""),
            "columns": [c for c in columns if c not in facts["relations"] and c != (alias or "")],
        })
    for match in re.finditer(r"\bwhere\s+", text, re.I):
        predicate = _clause_body(text, match.end())
        if predicate:
            facts["filters"].append({"predicate": _clip(predicate), "columns": _identifiers(predicate)})
    group = re.search(r"\bgroup\s+by\s+", text, re.I)
    if group:
        facts["group_by"] = _identifiers(_clause_body(text, group.end()))
    for name in _AGGREGATES:
        if re.search(rf"\b{name}\s*\(", lowered):
            facts["aggregates"].append(name.upper())
    facts["distinct"] = bool(re.search(r"\bselect\s+distinct\b", lowered))
    for op in ("union", "except", "intersect"):
        if re.search(rf"\b{op}\b", lowered):
            facts["set_operations"].append(op.upper())
    facts["conditional"] = bool(re.search(r"\bcase\b", lowered))
    facts["window"] = bool(re.search(r"\bover\s*\(", lowered))
    return facts


def m_facts(tmdl: str) -> dict[str, Any]:
    """Best-effort facts about a Power Query (M) partition expression."""
    facts: dict[str, Any] = {
        "has_definition": bool(tmdl and tmdl.strip()), "filters": [], "joins": [],
        "group_by": False, "calculations": [], "column_selection": False,
        "renames": False, "type_changes": False, "sql_passthrough": False,
        "native_sql": "",
    }
    if not facts["has_definition"]:
        return facts
    text = tmdl
    for match in re.finditer(r"Table\.SelectRows\s*\([^,]+,\s*each\s+(.+?)\)\s*(?:,|\n|$)", text, re.S):
        facts["filters"].append({"predicate": _clip(re.sub(r"\s+", " ", match.group(1))),
                                 "columns": re.findall(r"\[([^\]]+)\]", match.group(1))})
    for match in re.finditer(r"Table\.(NestedJoin|Join|Combine|FuzzyNestedJoin|FuzzyJoin)\s*\((.*?)\)\s*(?:,|\n|$)", text, re.S):
        args = re.sub(r"\s+", " ", match.group(2))
        facts["joins"].append({"type": match.group(1), "columns": re.findall(r"\"([^\"]+)\"", args)[:8],
                               "arguments": _clip(args)})
    facts["group_by"] = bool(re.search(r"Table\.Group\s*\(", text))
    facts["calculations"] = re.findall(r"Table\.AddColumn\s*\(\s*[^,]+,\s*\"([^\"]+)\"", text)
    facts["column_selection"] = bool(re.search(r"Table\.(?:RemoveColumns|SelectColumns)\s*\(", text))
    facts["renames"] = bool(re.search(r"Table\.RenameColumns\s*\(", text))
    facts["type_changes"] = bool(re.search(r"Table\.TransformColumnTypes\s*\(", text))
    native = re.search(r"(?:Value\.NativeQuery\s*\([^,]+,\s*|Query\s*=\s*)\"((?:[^\"\\]|\\.)*)\"", text, re.S)
    if native:
        facts["sql_passthrough"] = True
        facts["native_sql"] = native.group(1).replace("#(lf)", "\n").replace('""', '"')
    return facts


def evidence_digest(candidate: dict) -> dict[str, Any]:
    """Checklist sent with the prompt and used by the quality gate."""
    if candidate["edge_kind"] == "postgres_dependency":
        facts = sql_facts(candidate.get("definition") or "")
        source = facts_for_source(facts, candidate["from_name"])
        return {"kind": "sql", "facts": facts, "source_role": source,
                "requirements": requirements(candidate, facts, None)}
    m = m_facts(candidate.get("tmdl") or "")
    nested = sql_facts(m["native_sql"]) if m["native_sql"] else None
    return {"kind": "power_query", "facts": m, "native_sql_facts": nested,
            "requirements": requirements(candidate, nested, m)}


def facts_for_source(facts: dict, source_name: str) -> str:
    short = _short_name(source_name)
    if facts.get("base_relation") and _short_name(facts["base_relation"]) == short:
        return "base"
    for join in facts.get("joins", []):
        if _short_name(join["relation"]) == short:
            return "joined"
    if any(_short_name(r) == short for r in facts.get("relations", [])):
        return "referenced"
    return "unknown" if facts.get("has_definition") else "no_definition"


def requirements(candidate: dict, facts: dict | None, m: dict | None) -> list[str]:
    items: list[str] = []
    if facts and facts.get("has_definition"):
        for join in facts["joins"]:
            cols = ", ".join(join["columns"]) or "the join condition"
            items.append(f"Describe the {join['type']} to {join['relation']} and name its columns ({cols}).")
        for flt in facts["filters"]:
            items.append(f"State the row filter exactly: WHERE {flt['predicate']}.")
        if facts["group_by"] or facts["aggregates"]:
            items.append(
                "Explain the grouping (" + (", ".join(facts["group_by"]) or "no GROUP BY columns")
                + ") and aggregates (" + (", ".join(facts["aggregates"]) or "none") + ")."
            )
        if facts["set_operations"]:
            items.append("Explain the " + ", ".join(facts["set_operations"]) + " set operation.")
        if facts["conditional"]:
            items.append("Explain the CASE conditional logic and which column it produces.")
        if facts["window"]:
            items.append("Explain the window function and its partitioning.")
        if not any((facts["joins"], facts["filters"], facts["group_by"], facts["aggregates"], facts["set_operations"])):
            items.append("Say explicitly that the definition contains no join, filter, grouping, or aggregation, and name the projected columns.")
    if m and m.get("has_definition"):
        for flt in m["filters"]:
            items.append(f"State the Power Query row filter exactly: {flt['predicate']}.")
        for join in m["joins"]:
            items.append(f"Describe the Power Query {join['type']} and the columns it matches ({', '.join(join['columns']) or 'unnamed'}).")
        if m["group_by"]:
            items.append("Explain the Table.Group step and its aggregations.")
        if m["calculations"]:
            items.append("Explain the added column(s) " + ", ".join(m["calculations"]) + " and how each is calculated.")
        if m["column_selection"]:
            items.append("State which columns are removed or kept.")
        if m["sql_passthrough"]:
            items.append("Interpret the native SQL query embedded in the M expression.")
        if not any((m["filters"], m["joins"], m["group_by"], m["calculations"], m["column_selection"], m["sql_passthrough"])):
            items.append("Say explicitly that Power Query applies no filter, join, grouping, or calculation beyond loading the relation.")
    if not items:
        items.append("No SQL or Power Query definition was supplied: say which evidence is missing and describe only what the schemas show.")
    return items


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in words)


def _known_columns(candidate: dict) -> set[str]:
    names = {str(col.get("name")) for col in candidate.get("source_schema", []) + candidate.get("target_schema", [])}
    for relation in candidate.get("related_relations", []):
        names.update(str(col.get("name")) for col in relation.get("columns", []))
    return {name for name in names if name}


def assess_explanation(item: Any, candidate: dict) -> list[str]:
    """Return the reasons an explanation is too generic to publish; empty means accepted."""
    reasons: list[str] = []
    business = item.business_context
    technical = item.technical_logic
    combined = f"{business} {technical}"
    lowered = combined.casefold()
    for shape in _GENERIC_SHAPES:
        if shape.match(technical):
            reasons.append("technical_logic only restates that one object feeds another; it must describe the transformation.")
            break
    if len(technical) < MIN_TECHNICAL_CHARS:
        reasons.append(f"technical_logic is too short ({len(technical)} characters); describe joins, filters, projections, and calculations.")
    if len(business) < MIN_BUSINESS_CHARS:
        reasons.append("business_context is too short; state the business subject and how the target uses the data.")
    known = _known_columns(candidate)
    if known and not item.evidence_columns:
        reasons.append("evidence_columns is empty; name every column the paragraphs rely on.")
    for column in item.evidence_columns:
        if column.casefold() not in lowered:
            reasons.append(f"evidence_columns lists {column} but neither paragraph mentions it.")
    digest = candidate.get("evidence_digest") or evidence_digest(candidate)
    facts = digest["facts"] if digest["kind"] == "sql" else (digest.get("native_sql_facts") or None)
    if facts and facts.get("has_definition"):
        if facts["joins"]:
            if not _mentions(technical, _JOIN_WORDS):
                reasons.append(f"the definition contains {len(facts['joins'])} join(s) but technical_logic never describes a join.")
            named = [j["relation"] for j in facts["joins"] if _short_name(j["relation"]).casefold() in lowered]
            if not named:
                reasons.append("technical_logic does not name any joined relation (" + ", ".join(j["relation"] for j in facts["joins"]) + ").")
            join_columns = {c for j in facts["joins"] for c in j["columns"] if c in known}
            if join_columns and not join_columns.intersection(item.evidence_columns):
                reasons.append("the join condition columns (" + ", ".join(sorted(join_columns)) + ") are not identified in evidence_columns.")
        if facts["filters"]:
            if not _mentions(technical, _FILTER_WORDS):
                reasons.append("the definition filters rows with WHERE but technical_logic does not state the filter predicate.")
            filter_columns = {c for f in facts["filters"] for c in f["columns"] if c in known}
            if filter_columns and not filter_columns.intersection(item.evidence_columns):
                reasons.append("the filter columns (" + ", ".join(sorted(filter_columns)) + ") are not identified in evidence_columns.")
        if (facts["group_by"] or facts["aggregates"]) and not _mentions(technical, _GROUP_WORDS):
            reasons.append("the definition groups or aggregates rows but technical_logic does not explain the grouping or aggregates.")
        if facts["conditional"] and not _mentions(technical, _CALC_WORDS + ("condition", "when ", "otherwise", "case")):
            reasons.append("the definition contains CASE logic that technical_logic does not explain.")
    if digest["kind"] == "power_query":
        m = digest["facts"]
        if m.get("has_definition"):
            if m["filters"] and not _mentions(technical, _FILTER_WORDS):
                reasons.append("Power Query filters rows with Table.SelectRows but technical_logic does not state the filter.")
            if m["joins"] and not _mentions(technical, _JOIN_WORDS + ("append", "combine")):
                reasons.append("Power Query merges or appends tables but technical_logic does not describe it.")
            if m["group_by"] and not _mentions(technical, _GROUP_WORDS):
                reasons.append("Power Query groups rows with Table.Group but technical_logic does not explain the grouping.")
            if m["calculations"] and not _mentions(technical, _CALC_WORDS + ("column",)):
                reasons.append("Power Query adds calculated columns (" + ", ".join(m["calculations"]) + ") that technical_logic does not explain.")
            if m["column_selection"] and "column" not in technical.casefold():
                reasons.append("Power Query removes or selects columns but technical_logic does not say which.")
    return reasons


def _join_sentence(join: dict, *, name_relation: bool = True) -> str:
    kind = join["type"].replace("JOIN", "join")
    target = f" to {join['relation']}" if name_relation else ""
    condition = join["condition"]
    if condition.startswith("USING ("):
        return f"a {kind}{target} using {condition[len('USING ('):-1].strip()}"
    if condition:
        return f"a {kind}{target} on {condition}"
    return f"a {kind}{target}"


def describe_sql(candidate: dict, facts: dict) -> str:
    """Plain-language paragraph derived from the SQL definition itself."""
    to_name = candidate["to_name"]
    from_name = candidate["from_name"]
    kind = (candidate.get("target_identity") or {}).get("relation_kind") or "view"
    kind_label = "materialized view" if kind == "materialized_view" else "view"
    if not facts.get("has_definition"):
        return (
            f"This connection exists because PostgreSQL dependency discovery shows that {to_name} "
            f"depends on {from_name}, but no SQL definition for {to_name} has been captured yet, so "
            "the exact join, filter, and calculation logic cannot be derived; run the PostgreSQL "
            "dependency scan or Pipeline explanations to capture it."
        )
    parts: list[str] = []
    role = facts_for_source(facts, from_name)
    relations = ", ".join(facts["relations"]) if facts["relations"] else "its definition"
    parts.append(f"{to_name} is a {kind_label} built from {relations}.")
    if role == "base":
        parts.append(f"{from_name} is the base relation of the query, so every output row starts from its rows.")
    elif role == "joined":
        joins = [j for j in facts["joins"] if _short_name(j["relation"]) == _short_name(from_name)]
        parts.append(f"{from_name} enters through " + " and ".join(_join_sentence(j, name_relation=False) for j in joins) + ".")
    elif role == "referenced":
        parts.append(f"{from_name} is referenced inside the definition (for example in a subquery or expression).")
    else:
        parts.append(f"PostgreSQL dependency discovery links {from_name} to this definition even though its name is not visible in the captured SQL.")
    other_joins = [j for j in facts["joins"] if role != "joined" or _short_name(j["relation"]) != _short_name(from_name)]
    if other_joins:
        parts.append("It also uses " + "; ".join(_join_sentence(j) for j in other_joins) + ".")
    for flt in facts["filters"]:
        parts.append(f"Rows are limited by WHERE {flt['predicate']}.")
    if facts["group_by"] or facts["aggregates"]:
        grouping = ", ".join(facts["group_by"]) if facts["group_by"] else "no explicit GROUP BY columns"
        aggregates = ", ".join(facts["aggregates"]) if facts["aggregates"] else "no aggregate function"
        parts.append(f"Results are grouped by {grouping} using {aggregates}.")
    if facts["set_operations"]:
        parts.append("The query combines result sets with " + ", ".join(facts["set_operations"]) + ".")
    if facts["distinct"]:
        parts.append("Duplicate rows are removed with SELECT DISTINCT.")
    if facts["conditional"]:
        parts.append("It contains CASE conditional logic that derives values per row.")
    if facts["window"]:
        parts.append("It applies window functions over partitions of the rows.")
    if not any((facts["joins"], facts["filters"], facts["group_by"], facts["aggregates"], facts["set_operations"], facts["conditional"])):
        parts.append("The definition contains no join, filter, grouping, or aggregation; it projects columns straight through.")
    parts.append(f"This connection exists because PostgreSQL dependency discovery shows that {to_name} depends on {from_name}.")
    return " ".join(parts)


def describe_power_query(candidate: dict, m: dict) -> str:
    to_name = candidate["to_name"]
    from_name = candidate["from_name"]
    parts = [f"The Power BI table {to_name} loads the PostgreSQL relation {from_name} through Power Query."]
    if not m.get("has_definition"):
        parts.append("No M partition expression was captured for this table, so only the source binding is known.")
    else:
        if m["sql_passthrough"]:
            nested = sql_facts(m["native_sql"])
            if nested["has_definition"]:
                summary = describe_sql({**candidate, "definition": m["native_sql"]}, nested)
                parts.append("It runs a native SQL query: " + summary.split(" This connection exists because")[0])
            else:
                parts.append("It runs a native SQL query against the source.")
        for flt in m["filters"]:
            parts.append(f"Rows are filtered where {flt['predicate']}.")
        for join in m["joins"]:
            columns = ", ".join(join["columns"]) if join["columns"] else "unnamed columns"
            parts.append(f"It applies {join['type']} matching on {columns}.")
        if m["group_by"]:
            parts.append("Rows are grouped with Table.Group.")
        if m["calculations"]:
            parts.append("It adds the calculated column(s) " + ", ".join(m["calculations"]) + ".")
        if m["column_selection"]:
            parts.append("It removes or selects specific columns.")
        if m["renames"]:
            parts.append("Columns are renamed for the model.")
        if m["type_changes"]:
            parts.append("Column data types are converted.")
        if not any((m["filters"], m["joins"], m["group_by"], m["calculations"], m["column_selection"], m["sql_passthrough"])):
            parts.append("Power Query applies no filter, join, grouping, or calculation beyond loading the relation.")
    columns = candidate.get("semantic_columns") or []
    fields = candidate.get("visual_fields") or []
    if columns:
        parts.append(f"The semantic model exposes {len(columns)} column(s): " + ", ".join(columns[:12]) + ("…" if len(columns) > 12 else "") + ".")
    if fields:
        parts.append("Visuals use " + ", ".join(fields[:12]) + ("…" if len(fields) > 12 else "") + ".")
    return " ".join(parts)


REASON_TEXT = {
    None: "No AI explanation has been generated for this connection yet; run Pipeline explanations from Scanner.",
    "not_generated": "No AI explanation has been generated for this connection yet; run Pipeline explanations from Scanner.",
    "generic_output": "The local AI answer was rejected because it did not describe the exact join columns, filter predicates, or calculations; it is retried on the next run.",
    "low_confidence": "The local AI model reported low confidence in its interpretation, so it was discarded.",
    "invalid_model_output": "The local AI returned output that failed validation.",
    "provider_unavailable": "The local AI endpoint was unavailable during the last run.",
    "provider_circuit_open": "The local AI endpoint failed repeatedly during the last run, so remaining connections were skipped.",
    "feature_disabled": "Pipeline connection explanations are switched off under System > AI.",
    "ai_disabled": "Local AI mode is disabled under System > AI.",
    "preview_mode": "AI runs in preview mode, which never generates Pipeline explanations.",
    "stale": "The definition or schema changed after this explanation was generated; rerun Pipeline explanations.",
    "insights_unavailable": "The Pipeline Insights cache could not be read; review the server log.",
}


def fallback_text(candidate: dict, error_code: str | None = None) -> str:
    """Two paragraphs: what the stored definition proves, then why no AI text exists."""
    if candidate["edge_kind"] == "postgres_dependency":
        first = describe_sql(candidate, sql_facts(candidate.get("definition") or ""))
    else:
        first = describe_power_query(candidate, m_facts(candidate.get("tmdl") or ""))
    reason = REASON_TEXT.get(error_code, REASON_TEXT[None])
    second = (
        "No validated AI interpretation of the exact join columns, filter predicates, "
        f"calculations, or aggregations is available. {reason}"
    )
    return f"{first}\n\n{second}"
