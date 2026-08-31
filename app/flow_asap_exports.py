"""Canonical ASAP Export Wizard options shared by Flows components."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ASAPDownloadType:
    key: str
    label: str
    file_format: str
    preferred_suffix: str
    compatible_suffixes: tuple[str, ...]
    content_family: str
    download_only: bool = False


ASAP_DOWNLOAD_TYPES = (
    ASAPDownloadType(
        "excel_plain_text", "Excel with plain text", "xlsx", ".xlsx",
        (".xls", ".xlsx"), "excel",
    ),
    ASAPDownloadType(
        "csv_file_format", "CSV file format", "csv", ".csv", (".csv",), "csv",
    ),
    ASAPDownloadType(
        "excel_with_formatting", "Excel with formatting", "xlsx", ".xlsx",
        (".xls", ".xlsx"), "excel",
    ),
    ASAPDownloadType(
        "html", "HTML", "html", ".html", (".html", ".htm"), "html", True,
    ),
    ASAPDownloadType(
        "plain_text", "Plain text", "txt", ".txt", (".txt",), "text", True,
    ),
)

ASAP_DOWNLOAD_TYPE_BY_KEY = {item.key: item for item in ASAP_DOWNLOAD_TYPES}
ASAP_DOWNLOAD_TYPE_BY_LABEL = {item.label.casefold(): item for item in ASAP_DOWNLOAD_TYPES}
ASAP_DOWNLOAD_TYPE_KEYS = frozenset(ASAP_DOWNLOAD_TYPE_BY_KEY)

ASAP_EXPORT_CHECKBOXES = {
    "export_report_title": "Export Report Title",
    "export_filter_details": "Export filter details",
}

LEGACY_FILE_FORMAT_TO_ASAP_TYPE = {
    "xlsx": "excel_plain_text",
    "xls": "excel_plain_text",
    "csv": "csv_file_format",
}


def asap_type_for_legacy_file_format(file_format: str | None) -> str:
    """Map old Flow/job payloads to the semantic Export Wizard choice."""
    return LEGACY_FILE_FORMAT_TO_ASAP_TYPE.get(
        str(file_format or "csv").strip().casefold(), "csv_file_format",
    )


def resolve_asap_download_type(
    download_type: str | None, *, legacy_file_format: str | None = None,
) -> ASAPDownloadType:
    key = str(download_type or "").strip().casefold()
    if not key:
        key = asap_type_for_legacy_file_format(legacy_file_format)
    try:
        return ASAP_DOWNLOAD_TYPE_BY_KEY[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported ASAP download type: {download_type!r}.") from exc


def public_asap_download_types() -> list[dict]:
    """JSON-safe registry for the Flow builder."""
    return [
        {
            **asdict(item),
            "compatible_suffixes": list(item.compatible_suffixes),
            "downstream_eligible": not item.download_only,
        }
        for item in ASAP_DOWNLOAD_TYPES
    ]
