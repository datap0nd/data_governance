"""Official Power BI Execute Queries fallback for table visual data.

The browser client supplies only structured visual field targets and filters.
This module converts that metadata into escaped DAX. It never accepts or stores
arbitrary DAX from the UI.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from decimal import Decimal
from uuid import UUID

import httpx

from app.scanner.pbi_auth import get_access_token, resolve_proxy

PBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"
REQUEST_TIMEOUT_SECONDS = 120

_AGGREGATIONS = {
    "sum": "SUM",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNTA",
    "countnonnull": "COUNTA",
    "average": "AVERAGE",
    "avg": "AVERAGE",
    "distinctcount": "DISTINCTCOUNT",
    "median": "MEDIAN",
    "standarddeviation": "STDEV.S",
    "stdev": "STDEV.S",
    "variance": "VAR.S",
}
_NUMERIC_AGGREGATIONS = {
    2: None,
    3: "SUM",
    4: "MIN",
    5: "MAX",
    6: "COUNTA",
    7: "AVERAGE",
    8: "DISTINCTCOUNT",
}


class PbiVisualQueryError(RuntimeError):
    """Raised when a visual cannot be reproduced safely through Execute Queries."""


def _uuid_text(value: str, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PbiVisualQueryError(f"{label} is not a valid Power BI identifier.") from exc


def _dax_table(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PbiVisualQueryError("A visual field is missing its semantic model table name.")
    return "'" + value.replace("'", "''") + "'"


def _dax_name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PbiVisualQueryError(f"A visual field is missing its {label} name.")
    return "[" + value.replace("]", "]]") + "]"


def _dax_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _dax_literal(value) -> str:
    if value is None:
        return "BLANK()"
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise PbiVisualQueryError("A Power BI filter contains a non-finite number.")
        return format(value, "f") if isinstance(value, Decimal) else repr(value)
    if isinstance(value, str):
        return _dax_string(value)
    raise PbiVisualQueryError("A Power BI filter contains an unsupported value type.")


def _target_key(target: dict) -> tuple:
    if not isinstance(target, dict):
        raise PbiVisualQueryError("A Power BI field target is not valid.")
    return (
        target.get("table"),
        target.get("column"),
        target.get("measure"),
        target.get("hierarchy"),
        target.get("hierarchyLevel"),
        str(target.get("aggregationFunction") or "").casefold(),
    )


def _aggregation_function(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        if value == 1:
            raise PbiVisualQueryError(
                "A visual column uses its model-default aggregation, which cannot be reproduced safely. "
                "Set the field to an explicit aggregation in Power BI."
            )
        if value in _NUMERIC_AGGREGATIONS:
            return _NUMERIC_AGGREGATIONS[value]
    normalized = re.sub(r"[^a-z]", "", str(value).casefold())
    if normalized in {"none", "donotsummarize"}:
        return None
    if normalized in {"default", "modeldefault"}:
        raise PbiVisualQueryError(
            "A visual column uses its model-default aggregation, which cannot be reproduced safely. "
            "Set the field to an explicit aggregation in Power BI."
        )
    function = _AGGREGATIONS.get(normalized)
    if not function:
        raise PbiVisualQueryError(
            f"The visual uses unsupported aggregation '{value}'. No email was sent."
        )
    return function


def _target_expression(target: dict) -> tuple[str, str]:
    if target.get("daxExpression") is not None or (
        target.get("name") is not None and not target.get("table")
    ):
        raise PbiVisualQueryError(
            "The selected visual contains a visual calculation. Execute Queries cannot reproduce "
            "that calculation safely, so no email was sent."
        )
    if target.get("hierarchy") or target.get("hierarchyLevel"):
        raise PbiVisualQueryError(
            "The selected visual contains a hierarchy field. This fallback currently supports "
            "columns, explicit aggregations, and measures only."
        )
    if target.get("percentOfGrandTotal"):
        raise PbiVisualQueryError(
            "The selected visual uses percent of grand total. This fallback will not send values "
            "that might differ from the visual."
        )
    table = _dax_table(target.get("table"))
    if target.get("measure"):
        return "value", table + _dax_name(target["measure"], "measure")
    if target.get("column"):
        column = table + _dax_name(target["column"], "column")
        aggregation = _aggregation_function(target.get("aggregationFunction"))
        return ("value", f"{aggregation}({column})") if aggregation else ("group", column)
    raise PbiVisualQueryError(
        "The selected visual contains a field type that Execute Queries cannot reproduce safely."
    )


def _field_header(field: dict, target: dict) -> str:
    candidates = (
        field.get("displayName"),
        field.get("fieldName"),
        target.get("measure"),
        target.get("column"),
        field.get("roleDisplayName"),
        field.get("roleName"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Value"


def _deduplicate_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for header in headers:
        count = counts.get(header, 0) + 1
        counts[header] = count
        result.append(header if count == 1 else f"{header} ({count})")
    return result


def _filter_kind(filter_body: dict) -> str:
    schema = str(filter_body.get("$schema") or "").casefold()
    if "#basic" in schema:
        return "basic"
    if "#advanced" in schema:
        return "advanced"
    if "#topn" in schema:
        return "topn"
    if "#relativedate" in schema:
        return "relative_date"
    if "#relativetime" in schema:
        return "relative_time"
    filter_type = filter_body.get("filterType")
    if isinstance(filter_type, str):
        normalized = filter_type.casefold().replace("_", "")
        return {
            "basic": "basic",
            "advanced": "advanced",
            "topn": "topn",
            "relativedate": "relative_date",
            "relativetime": "relative_time",
        }.get(normalized, "unsupported")
    return {
        0: "advanced",
        1: "basic",
        4: "relative_date",
        5: "topn",
        7: "relative_time",
    }.get(filter_type, "unsupported")


def _set_expression(values: list) -> str:
    return "{" + ", ".join(_dax_literal(value) for value in values) + "}"


def _basic_condition(expression: str, operator: str, values: list) -> str | None:
    normalized = operator.casefold()
    if normalized == "all":
        return None
    if normalized not in {"in", "notin"}:
        raise PbiVisualQueryError(f"Unsupported Power BI basic filter operator '{operator}'.")
    if not values:
        return "FALSE()" if normalized == "in" else "TRUE()"
    condition = f"{expression} IN {_set_expression(values)}"
    return condition if normalized == "in" else f"NOT ({condition})"


def _advanced_condition(expression: str, condition: dict) -> str | None:
    operator = str(condition.get("operator") or "None")
    value = condition.get("value")
    if operator == "None":
        return None
    comparisons = {
        "LessThan": "<",
        "LessThanOrEqual": "<=",
        "GreaterThan": ">",
        "GreaterThanOrEqual": ">=",
        "Is": "=",
        "IsNot": "<>",
    }
    if operator in comparisons:
        return f"{expression} {comparisons[operator]} {_dax_literal(value)}"
    if operator == "Contains":
        return f"CONTAINSSTRING({expression}, {_dax_literal(value)})"
    if operator == "DoesNotContain":
        return f"NOT CONTAINSSTRING({expression}, {_dax_literal(value)})"
    if operator == "StartsWith":
        literal = _dax_literal(value)
        return f"LEFT({expression}, LEN({literal})) = {literal}"
    if operator == "DoesNotStartWith":
        literal = _dax_literal(value)
        return f"LEFT({expression}, LEN({literal})) <> {literal}"
    if operator == "IsBlank":
        return f"ISBLANK({expression})"
    if operator == "IsNotBlank":
        return f"NOT ISBLANK({expression})"
    if operator == "IsEmptyString":
        return f"{expression} = \"\""
    if operator == "IsNotEmptyString":
        return f"{expression} <> \"\""
    raise PbiVisualQueryError(f"Unsupported Power BI advanced filter operator '{operator}'.")


def _advanced_filter_condition(expression: str, filter_body: dict) -> str | None:
    conditions = [
        compiled
        for condition in filter_body.get("conditions") or []
        if (compiled := _advanced_condition(expression, condition))
    ]
    if not conditions:
        return None
    logical = str(filter_body.get("logicalOperator") or "And").casefold()
    if logical not in {"and", "or"}:
        raise PbiVisualQueryError("The visual uses an unsupported advanced-filter join.")
    separator = " && " if logical == "and" else " || "
    return separator.join(f"({condition})" for condition in conditions)


def build_visual_query(query_spec: dict, max_rows: int) -> dict:
    """Build escaped DAX plus ordered output metadata from a visual query spec."""
    if not isinstance(query_spec, dict):
        raise PbiVisualQueryError("The Power BI visual query definition is missing.")
    fields = query_spec.get("fields") or []
    if not isinstance(fields, list) or not fields:
        raise PbiVisualQueryError("The selected visual has no readable data fields.")
    max_rows = max(1, min(30000, int(max_rows)))

    group_expressions: list[str] = []
    named_expressions: list[tuple[str, str]] = []
    value_aliases: dict[tuple, str] = {}
    output_columns: list[dict] = []

    def add_value_expression(target: dict, expression: str, *, prefix: str = "__value") -> str:
        key = _target_key(target)
        existing = value_aliases.get(key)
        if existing:
            return existing
        alias = f"{prefix}{len(named_expressions)}"
        named_expressions.append((alias, expression))
        value_aliases[key] = alias
        return alias

    for field in fields:
        target = field.get("target") if isinstance(field, dict) else None
        if not isinstance(target, dict):
            raise PbiVisualQueryError("The selected visual contains an invalid data field.")
        if target.get("hidden"):
            continue
        field_kind, expression = _target_expression(target)
        if field_kind == "group":
            if expression not in group_expressions:
                group_expressions.append(expression)
            output_expression = expression
        else:
            alias = add_value_expression(target, expression)
            output_expression = _dax_name(alias, "query alias")
        output_columns.append(
            {
                "header": _field_header(field, target),
                "output_alias": f"__out{len(output_columns)}",
                "expression": output_expression,
                "format_string": str(field.get("formatString") or "").strip(),
            }
        )

    if not output_columns:
        raise PbiVisualQueryError("The selected visual contains only hidden fields.")

    pre_filters: list[str] = []
    post_conditions: list[str] = []
    seen_filters: set[str] = set()
    for wrapped_filter in query_spec.get("filters") or []:
        if not isinstance(wrapped_filter, dict):
            continue
        filter_body = wrapped_filter.get("filter")
        source = str(wrapped_filter.get("source") or "visual")
        if not isinstance(filter_body, dict):
            continue
        fingerprint = json.dumps(filter_body, sort_keys=True, separators=(",", ":"), default=str)
        if fingerprint in seen_filters:
            continue
        seen_filters.add(fingerprint)
        kind = _filter_kind(filter_body)
        if kind in {"topn", "relative_date", "relative_time", "unsupported"}:
            raise PbiVisualQueryError(
                f"The {source} uses a {kind.replace('_', ' ')} filter that this fallback cannot "
                "reproduce safely. No email was sent."
            )
        target = filter_body.get("target")
        if not isinstance(target, dict):
            raise PbiVisualQueryError(
                f"The {source} uses a multi-field or identity filter that cannot be reproduced safely."
            )
        target_kind, target_expression = _target_expression(target)
        if target_kind == "group":
            if kind == "basic":
                operator = str(filter_body.get("operator") or "All")
                values = filter_body.get("values") or []
                if operator.casefold() == "all":
                    continue
                if operator.casefold() == "in":
                    pre_filters.append(
                        f"KEEPFILTERS(TREATAS({_set_expression(values)}, {target_expression}))"
                    )
                else:
                    condition = _basic_condition(target_expression, operator, values)
                    if condition:
                        pre_filters.append(
                            f"KEEPFILTERS(FILTER(ALL({target_expression}), {condition}))"
                        )
            else:
                condition = _advanced_filter_condition(target_expression, filter_body)
                if condition:
                    pre_filters.append(
                        f"KEEPFILTERS(FILTER(ALL({target_expression}), {condition}))"
                    )
            continue

        alias = add_value_expression(target, target_expression, prefix="__filter")
        alias_expression = _dax_name(alias, "query alias")
        if kind == "basic":
            condition = _basic_condition(
                alias_expression,
                str(filter_body.get("operator") or "All"),
                filter_body.get("values") or [],
            )
        else:
            condition = _advanced_filter_condition(alias_expression, filter_body)
        if condition:
            post_conditions.append(condition)

    summary_arguments = [*group_expressions, *pre_filters]
    summary_arguments.extend(
        f"{_dax_string(alias)}, {expression}" for alias, expression in named_expressions
    )
    if not summary_arguments:
        raise PbiVisualQueryError("The selected visual could not be converted into a semantic query.")

    base_lines = ",\n        ".join(summary_arguments)
    filtered_expression = (
        "FILTER(__Base, "
        + " && ".join(f"({condition})" for condition in post_conditions)
        + ")"
        if post_conditions
        else "__Base"
    )
    sort_expressions = group_expressions or [
        _dax_name(alias, "query alias") for alias, _ in named_expressions
    ]
    sort_arguments = ", ".join(
        item for expression in sort_expressions for item in (expression, "ASC")
    )
    select_parts: list[str] = []
    headers = _deduplicate_headers([column["header"] for column in output_columns])
    for column, header in zip(output_columns, headers, strict=True):
        expression = column["expression"]
        if column["format_string"]:
            expression = f"FORMAT({expression}, {_dax_string(column['format_string'])})"
        select_parts.append(f"{_dax_string(column['output_alias'])}, {expression}")
        column["header"] = header

    dax = (
        "EVALUATE\n"
        "VAR __Base =\n"
        "    SUMMARIZECOLUMNS(\n"
        f"        {base_lines}\n"
        "    )\n"
        f"VAR __Filtered = {filtered_expression}\n"
        f"VAR __Limited = TOPN({max_rows + 1}, __Filtered, {sort_arguments})\n"
        "RETURN\n"
        "    SELECTCOLUMNS(\n"
        "        __Limited,\n"
        "        " + ",\n        ".join(select_parts) + "\n"
        "    )"
    )
    return {"dax": dax, "columns": output_columns, "max_rows": max_rows}


def _value_message(value) -> str:
    if isinstance(value, dict):
        for key in ("message", "errorDescription", "details"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            found = _value_message(candidate)
            if found:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _value_message(candidate)
            if found:
                return found
    return ""


def _response_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    return _value_message(body) or json.dumps(body, default=str)[:500]


def _row_value(row: dict, alias: str):
    for key in (f"[{alias}]", alias):
        if key in row:
            return row[key]
    suffix = f"[{alias}]"
    for key, value in row.items():
        if str(key).endswith(suffix):
            return value
    return None


def execute_visual_query(
    *,
    workspace_id: str,
    dataset_id: str,
    query_spec: dict,
    max_rows: int,
) -> dict:
    """Execute a generated visual query with the existing cached delegated account."""
    workspace_guid = _uuid_text(workspace_id, "Workspace ID")
    dataset_guid = _uuid_text(dataset_id, "Dataset ID")
    built = build_visual_query(query_spec, max_rows)
    url = f"{PBI_API_BASE}/groups/{workspace_guid}/datasets/{dataset_guid}/executeQueries"
    auth = get_access_token()
    payload = {
        "queries": [{"query": built["dax"]}],
        "serializerSettings": {"includeNulls": True},
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, proxy=resolve_proxy(url)) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {auth['access_token']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code in {401, 403}:
        raise PbiVisualQueryError(
            "the cached Power BI account needs semantic model Read and Build permission, and the "
            "tenant setting 'Dataset Execute Queries REST API' must be enabled. The same cached "
            "account is being used; no separate token is required."
        )
    if response.status_code >= 400:
        raise PbiVisualQueryError(
            f"Power BI Execute Queries returned {response.status_code}: {_response_message(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise PbiVisualQueryError("Power BI Execute Queries returned a non-JSON response.") from exc
    results = body.get("results") or []
    if not results:
        raise PbiVisualQueryError("Power BI Execute Queries returned no results.")
    if results[0].get("error"):
        raise PbiVisualQueryError(
            "Power BI rejected the generated visual query: "
            + (_value_message(results[0]["error"]) or str(results[0]["error"])[:500])
        )
    tables = results[0].get("tables") or []
    if not tables:
        raise PbiVisualQueryError("Power BI Execute Queries returned no table.")
    table = tables[0]
    if table.get("error"):
        detail = _value_message(table["error"]) or str(table["error"])[:500]
        raise PbiVisualQueryError(f"Power BI rejected the generated table query: {detail}")
    rows = table.get("rows") or []
    if len(rows) > built["max_rows"]:
        raise PbiVisualQueryError(
            f"the visual contains more than the configured {built['max_rows']:,} row limit. "
            "No partial email was sent."
        )

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(column["header"] for column in built["columns"])
    for row in rows:
        writer.writerow(_row_value(row, column["output_alias"]) for column in built["columns"])
    return {
        "data": output.getvalue(),
        "row_count": len(rows),
        "columns": [column["header"] for column in built["columns"]],
    }
