"""Explicit worksheet selection shared by Flow configuration and execution."""
from __future__ import annotations

import json
import re


CAPABILITY = "excel_worksheets_v1"


class WorksheetError(RuntimeError):
    """A completed workbook needs a configuration or data correction, not a retry."""

    def __init__(self, message: str, *, workbook: str, available: list[str],
                 selected: list[str] | None = None, code: str = "excel_worksheets"):
        self.details = {
            "code": code, "workbook": workbook,
            "available_sheets": available, "selected_sheets": selected or [],
            "sql_started": False,
        }
        super().__init__(
            f"{message} Workbook: {workbook!r}. "
            f"Worksheets: {', '.join(repr(name) for name in available) or 'none'}. "
            "SQL was not started. The original workbook is preserved."
        )


def normalize_config(value: dict | None) -> dict | None:
    """None is the default: exactly one worksheet. No inferred exclusions."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"mode", "names"}:
        raise ValueError("Excel worksheets must specify a mode and exact worksheet names.")
    mode, names = value["mode"], value["names"]
    if not isinstance(mode, str) or mode not in {"single", "append"}:
        raise ValueError("Choose one named worksheet or append named worksheets.")
    if not isinstance(names, list) or not names or any(
        not isinstance(name, str) or not name.strip() for name in names
    ):
        raise ValueError("Enter the exact worksheet names, one per line.")
    if len(names) != len(set(names)):
        raise ValueError("Each worksheet name must be listed only once.")
    if mode == "single" and len(names) != 1:
        raise ValueError("Loading one worksheet requires exactly one worksheet name.")
    if mode == "append" and len(names) < 2:
        raise ValueError("Appending worksheets requires at least two worksheet names.")
    return {"mode": mode, "names": list(names)}


def saved_config(value: str | None) -> dict | None:
    # Invalid persisted settings must fail closed instead of choosing other data.
    return normalize_config(json.loads(value)) if value else None


def worker_supported(job: dict, capabilities: dict) -> bool:
    config = job.get("downloads", {}).get("excel_worksheets")
    if not config:
        return True
    # Older workers already implement exact selection for local-file sources.
    if (job.get("flow", {}).get("source_type") == "file"
            and config == {"mode": "single", "names": [job.get("local_file", {}).get("worksheet")]}):
        return True
    return bool(capabilities.get(CAPABILITY))


def failure_details(error: Exception) -> dict:
    """Keep worksheet evidence when the portal wraps a completed-download error."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, WorksheetError):
            return {"stage": "file_normalization_failed", "excel": error.details}
        error = error.__cause__
    return {}


def select_names(available: list[str], config: dict | None, *, workbook: str) -> list[str]:
    config = normalize_config(config)
    if config is None:
        if len(available) > 1:
            raise WorksheetError(
                "This Excel has more than one sheet. Please enable the option in Flows. "
                "In After download, enable 'Choose how to load Excel worksheets'.",
                workbook=workbook, available=available, code="excel_multiple_sheets",
            )
        if not available:
            raise WorksheetError("This Excel has no worksheets to load.",
                                 workbook=workbook, available=available)
        return list(available)
    names = config["names"]
    missing = [name for name in names if available.count(name) != 1]
    if missing:
        raise WorksheetError(
            f"Excel worksheet {', '.join(repr(name) for name in missing)} was not found exactly once. "
            "Check the exact names in Flows > After download.",
            workbook=workbook, available=available, selected=names,
            code="excel_sheet_missing",
        )
    return list(names)


def normalized_header(header: list[str]) -> list[str]:
    return [re.sub(r"\W+", "_", value).strip("_").casefold() or f"col_{index}"
            for index, value in enumerate(header)]


def require_compatible_headers(expected: list[str], actual: list[str], *, workbook: str,
                               first: str, current: str, available: list[str],
                               selected: list[str]) -> None:
    if normalized_header(expected) == normalized_header(actual):
        return
    raise WorksheetError(
        f"Cannot append worksheets in {workbook!r}: {first!r} ({len(expected)} columns) and "
        f"{current!r} ({len(actual)} columns) have different columns. "
        f"Expected: {expected!r}. Found: {actual!r}. "
        "Selected worksheets must have compatible columns in the same order. "
        "Correct the workbook or change the named worksheets in Flows > After download.",
        workbook=workbook, available=available, selected=selected,
        code="excel_columns_mismatch",
    )
