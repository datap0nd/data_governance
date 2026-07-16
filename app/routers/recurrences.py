"""Scheduled Outlook emails built from live Power BI table visuals."""

from __future__ import annotations

import calendar
import csv
import html
import io
import json
import logging
import re
import threading
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import PBI_VISUAL_EXPORT_MAX_ROWS
from app.database import get_db
from app.pbi_visual_export import (
    PbiVisualExportError,
    TABLE_VISUAL_TYPES,
    export_visual_data,
    list_visuals,
    visual_export_runtime_status,
)
from app.routers.email import OutlookEmailError, _launch_outlook_payload
from app.routers.eventlog import get_actor, log_event
from app.scanner import pbi_auth, pbi_fetch
from app.scanner.pbi_sync import pbi_auth_mode

router = APIRouter(prefix="/api/recurrences", tags=["recurrences"])
logger = logging.getLogger(__name__)

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
BUSINESS_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
RECURRENCE_TYPES = {"daily", "weekly", "weekdays", "monthly"}
RULE_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "not_contains",
    "is_empty",
    "not_empty",
}
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_RUN_LOCK = threading.Lock()
_RUNNING_IDS: set[int] = set()


class RecurrenceGroupIn(BaseModel):
    match_value: str = Field(max_length=500)
    display_name: str = Field(default="", max_length=200)
    recipients: str = Field(default="", max_length=4000)
    enabled: bool = True

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("recipients")
    @classmethod
    def strip_recipients(cls, value: str) -> str:
        return value.strip()


class RecurrenceRuleIn(BaseModel):
    column_name: str = Field(min_length=1, max_length=300)
    operator: str
    compare_value: str | None = Field(default=None, max_length=500)

    @field_validator("column_name", "operator")
    @classmethod
    def strip_rule_value(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_rule(self):
        if self.operator not in RULE_OPERATORS:
            raise ValueError("The selected row rule operator is not supported.")
        if self.operator not in {"is_empty", "not_empty"} and not str(self.compare_value or "").strip():
            raise ValueError("This row rule needs a comparison value.")
        return self


class RecurrenceRefreshError(RuntimeError):
    def __init__(self, message: str, refresh: dict | None = None):
        super().__init__(message)
        self.refresh = refresh or {}


class RecurrenceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    workspace_id: str
    workspace_name: str | None = Field(default=None, max_length=200)
    report_id: str
    report_name: str = Field(min_length=1, max_length=300)
    report_url: str | None = Field(default=None, max_length=2000)
    embed_url: str = Field(min_length=1, max_length=2000)
    dataset_id: str | None = None
    page_name: str = Field(min_length=1, max_length=300)
    page_display_name: str | None = Field(default=None, max_length=300)
    visual_name: str = Field(min_length=1, max_length=300)
    visual_title: str | None = Field(default=None, max_length=500)
    visual_type: str = Field(min_length=1, max_length=100)
    owner_name: str | None = Field(default=None, max_length=200)
    delivery_mode: Literal["single", "subgroups"] = "subgroups"
    recipients: str | None = Field(default=None, max_length=4000)
    group_column: str = Field(default="", max_length=300)
    subject_template: str = Field(
        default="{recurrence} ({row_count} rows)",
        min_length=1,
        max_length=500,
    )
    recurrence: str = "weekly"
    weekdays: list[str] = Field(default_factory=lambda: ["monday"])
    month_day: int | None = Field(default=None, ge=1, le=31)
    send_time: str = "08:00"
    groups: list[RecurrenceGroupIn] = Field(default_factory=list)
    rules: list[RecurrenceRuleIn] = Field(default_factory=list)

    @field_validator(
        "name",
        "report_name",
        "page_name",
        "visual_name",
        "visual_type",
        "group_column",
        "subject_template",
        "recurrence",
        "send_time",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("owner_name")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def validate_configuration(self):
        for identifier, label in ((self.workspace_id, "Workspace ID"), (self.report_id, "Report ID")):
            try:
                UUID(identifier)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f"{label} is not valid.") from exc
        if self.dataset_id:
            try:
                UUID(self.dataset_id)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("Dataset ID is not valid.") from exc
        embed_parts = urlsplit(self.embed_url)
        embed_host = (embed_parts.hostname or "").casefold()
        if embed_parts.scheme.casefold() != "https" or embed_host != "app.powerbi.com":
            raise ValueError("The report embed URL must be an HTTPS app.powerbi.com URL.")
        if self.report_url:
            report_parts = urlsplit(self.report_url)
            report_host = (report_parts.hostname or "").casefold()
            if report_parts.scheme.casefold() != "https" or report_host != "app.powerbi.com":
                raise ValueError("The report URL must be an HTTPS app.powerbi.com URL.")
        if self.visual_type.casefold() not in TABLE_VISUAL_TYPES:
            raise ValueError("The selected visual must be a Power BI table or matrix.")
        if self.recurrence not in RECURRENCE_TYPES:
            raise ValueError("Frequency must be daily, weekly, weekdays, or monthly.")
        _parse_send_time(self.send_time)
        normalized_days = _normalize_weekdays(self.weekdays)
        if self.recurrence == "weekly" and not normalized_days:
            raise ValueError("Choose at least one weekday for a weekly recurrence.")
        self.weekdays = normalized_days
        if self.recurrence == "monthly" and not self.month_day:
            raise ValueError("Choose a day of the month for a monthly recurrence.")
        self.recipients = str(self.recipients or "").strip() or None
        if self.delivery_mode == "single":
            _parse_recipients(self.recipients or "")
            self.group_column = ""
            self.groups = []
            return self
        if not self.group_column:
            raise ValueError("Choose a subgroup column or use single-email delivery.")
        enabled_groups = [group for group in self.groups if group.enabled]
        if not enabled_groups:
            raise ValueError("Configure at least one enabled subgroup or use single-email delivery.")
        seen_values: set[str] = set()
        for group in self.groups:
            key = _match_key(group.match_value)
            if key in seen_values:
                raise ValueError("Each subgroup value can be configured only once.")
            seen_values.add(key)
            if group.enabled:
                if not group.display_name:
                    raise ValueError("Every enabled subgroup needs a name.")
                _parse_recipients(group.recipients)
        return self


class VisualSelection(BaseModel):
    report_id: str
    embed_url: str
    page_name: str
    workspace_id: str | None = None
    dataset_id: str | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        try:
            UUID(self.report_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Report ID is not valid.") from exc
        for identifier, label in (
            (self.workspace_id, "Workspace ID"),
            (self.dataset_id, "Dataset ID"),
        ):
            if not identifier:
                continue
            try:
                UUID(identifier)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f"{label} is not valid.") from exc
        embed_parts = urlsplit(self.embed_url)
        if (
            embed_parts.scheme.casefold() != "https"
            or (embed_parts.hostname or "").casefold() != "app.powerbi.com"
        ):
            raise ValueError("The report embed URL must be an HTTPS app.powerbi.com URL.")
        if not self.page_name.strip():
            raise ValueError("Select a report page.")
        self.page_name = self.page_name.strip()
        return self


class VisualPreview(VisualSelection):
    visual_name: str = Field(min_length=1, max_length=300)


class RunRequest(BaseModel):
    mode: Literal["draft", "send"] = "send"


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _parse_send_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Send time must use HH:MM format.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Send time must use HH:MM format.")
    return hour, minute


def _normalize_weekdays(values: list[str] | str | None) -> list[str]:
    raw_values = values.replace(";", ",").split(",") if isinstance(values, str) else (values or [])
    result: list[str] = []
    for value in raw_values:
        key = str(value).strip().casefold()
        if key in WEEKDAYS and key not in result:
            result.append(key)
    return result


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _month_date(year: int, month: int, requested_day: int) -> date:
    return date(year, month, min(requested_day, calendar.monthrange(year, month)[1]))


def calculate_next_run(
    recurrence: str,
    send_time: str,
    weekdays: list[str] | str | None,
    month_day: int | None,
    now: datetime | None = None,
) -> datetime:
    current = now or _now()
    hour, minute = _parse_send_time(send_time)
    if recurrence not in RECURRENCE_TYPES:
        raise ValueError("Unsupported recurrence frequency.")
    if recurrence == "daily":
        candidate = datetime.combine(current.date(), time(hour, minute))
        return candidate if candidate > current else candidate + timedelta(days=1)
    if recurrence == "monthly":
        requested_day = month_day or 1
        year, month = current.year, current.month
        for _ in range(14):
            candidate = datetime.combine(_month_date(year, month, requested_day), time(hour, minute))
            if candidate > current:
                return candidate
            year, month = _next_month(year, month)
        raise ValueError("Could not calculate the next monthly run.")

    selected = BUSINESS_WEEKDAYS if recurrence == "weekdays" else (_normalize_weekdays(weekdays) or ["monday"])
    selected_numbers = {WEEKDAYS[day] for day in selected}
    for offset in range(14):
        day = current.date() + timedelta(days=offset)
        if day.weekday() not in selected_numbers:
            continue
        candidate = datetime.combine(day, time(hour, minute))
        if candidate > current:
            return candidate
    raise ValueError("Could not calculate the next weekly run.")


def _parse_recipients(value: str) -> list[str]:
    recipients = [item.strip() for item in re.split(r"[;,\n]+", value or "") if item.strip()]
    if not recipients:
        raise ValueError("Enter at least one recipient email address.")
    invalid = [recipient for recipient in recipients if not EMAIL_RE.fullmatch(recipient)]
    if invalid:
        raise ValueError(f"Recipient email is not valid: {invalid[0]}")
    return recipients


def _report_owner_name(db, dataset_id: str | None, report_name: str) -> str | None:
    row = db.execute(
        """SELECT owner
           FROM reports
           WHERE archived = 0
             AND (
                 (? IS NOT NULL AND pbi_dataset_id = ?)
                 OR lower(trim(name)) = lower(trim(?))
             )
           ORDER BY CASE
               WHEN ? IS NOT NULL AND pbi_dataset_id = ? THEN 0
               ELSE 1
           END, id
           LIMIT 1""",
        (dataset_id, dataset_id, report_name, dataset_id, dataset_id),
    ).fetchone()
    return str(row["owner"] or "").strip() or None if row else None


def _person_for_name(db, owner_name: str | None) -> dict | None:
    if not owner_name:
        return None
    row = db.execute(
        """SELECT id, name, role, email
           FROM people
           WHERE lower(trim(name)) = lower(trim(?))
           ORDER BY id
           LIMIT 1""",
        (owner_name,),
    ).fetchone()
    return dict(row) if row else None


def _ensure_write_owner(body: RecurrenceWrite) -> dict:
    with get_db() as db:
        if not body.owner_name:
            body.owner_name = _report_owner_name(db, body.dataset_id, body.report_name)
        owner = _person_for_name(db, body.owner_name)
    if not body.owner_name:
        raise ValueError(
            "The selected report has no report owner. Assign one on the Reports page "
            "or choose an alert owner."
        )
    if not owner:
        raise ValueError(
            f"Alert owner '{body.owner_name}' is not in Management > Create > People."
        )
    try:
        _parse_recipients(owner.get("email") or "")
    except ValueError as exc:
        raise ValueError(
            f"Alert owner '{owner['name']}' needs a valid email in "
            "Management > Create > People."
        ) from exc
    body.owner_name = owner["name"]
    return owner


def _resolve_recurrence_owner(recurrence: dict) -> dict | None:
    with get_db() as db:
        owner_name = str(recurrence.get("owner_name") or "").strip() or None
        if not owner_name:
            owner_name = _report_owner_name(
                db,
                recurrence.get("dataset_id"),
                recurrence["report_name"],
            )
            if owner_name:
                db.execute(
                    """UPDATE pbi_recurrences
                       SET owner_name = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (owner_name, recurrence["id"]),
                )
                recurrence["owner_name"] = owner_name
        return _person_for_name(db, owner_name)


def _require_successful_refresh(recurrence: dict) -> dict:
    if not recurrence.get("dataset_id"):
        raise RecurrenceRefreshError(
            "The alert has no semantic model ID, so Metronome cannot verify that the "
            "latest Power BI refresh succeeded."
        )
    try:
        refresh = pbi_fetch.fetch_dataset_last_refresh(
            recurrence["workspace_id"],
            recurrence["dataset_id"],
        )
    except Exception as exc:
        raise RecurrenceRefreshError(
            "Metronome could not verify the semantic model's latest refresh through "
            f"Power BI Service: {str(exc).strip() or exc.__class__.__name__}",
            {"status": "unavailable", "error": str(exc).strip() or exc.__class__.__name__},
        ) from exc
    status = str(refresh.get("status") or "").strip()
    if status.casefold() != "completed":
        completed_at = refresh.get("end_time") or refresh.get("start_time") or "unknown time"
        reason = refresh.get("error")
        message = (
            "The alert was blocked because the semantic model's latest refresh did not "
            f"succeed. Latest status: {status or 'no refresh history'} at {completed_at}."
        )
        if reason:
            message += f" Power BI reason: {reason}"
        raise RecurrenceRefreshError(message, refresh)
    return refresh


def _dedupe_headers(raw_headers: list[str]) -> list[str]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, raw_header in enumerate(raw_headers, start=1):
        base = (raw_header or "").lstrip("\ufeff").strip() or f"Column {index}"
        key = base.casefold()
        counts[key] = counts.get(key, 0) + 1
        headers.append(base if counts[key] == 1 else f"{base} ({counts[key]})")
    return headers


def parse_visual_csv(csv_text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.reader(io.StringIO((csv_text or "").replace("\x00", "")))
    try:
        headers = _dedupe_headers(next(reader))
    except StopIteration:
        return [], []
    rows: list[dict[str, str]] = []
    for values in reader:
        padded = list(values[: len(headers)]) + [""] * max(0, len(headers) - len(values))
        if not any(str(value).strip() for value in padded):
            continue
        rows.append({header: str(padded[index]) for index, header in enumerate(headers)})
    return headers, rows


def _match_key(value) -> str:
    return str(value if value is not None else "").strip().casefold()


def _decimal(value) -> Decimal | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^0-9,\.\-+]", "", text)
    if text.count(",") and text.count("."):
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") == 1:
        left, right = text.split(",", 1)
        text = left + ("." + right if len(right) != 3 else right)
    else:
        text = text.replace(",", "")
    if negative:
        text = "-" + text.lstrip("+")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def rule_matches(row: dict[str, str], rule: dict) -> bool:
    actual = str(row.get(rule["column_name"], ""))
    expected = str(rule.get("compare_value") or "")
    operator = rule["operator"]
    if operator == "is_empty":
        return not actual.strip()
    if operator == "not_empty":
        return bool(actual.strip())
    if operator == "contains":
        return expected.casefold() in actual.casefold()
    if operator == "not_contains":
        return expected.casefold() not in actual.casefold()
    if operator == "eq":
        return _match_key(actual) == _match_key(expected)
    if operator == "ne":
        return _match_key(actual) != _match_key(expected)
    actual_number, expected_number = _decimal(actual), _decimal(expected)
    if actual_number is None or expected_number is None:
        return False
    return {
        "gt": actual_number > expected_number,
        "gte": actual_number >= expected_number,
        "lt": actual_number < expected_number,
        "lte": actual_number <= expected_number,
    }.get(operator, False)


def apply_rules(rows: list[dict[str, str]], rules: list[dict]) -> list[dict[str, str]]:
    if not rules:
        return rows
    return [row for row in rows if all(rule_matches(row, rule) for rule in rules)]


def _format_subject(template: str, recurrence: dict, group: dict, row_count: int) -> str:
    replacements = {
        "{recurrence}": recurrence["name"],
        "{group}": group["display_name"],
        "{row_count}": str(row_count),
        "{report}": recurrence["report_name"],
        "{date}": _now().date().isoformat(),
    }
    subject = template
    for placeholder, value in replacements.items():
        subject = subject.replace(placeholder, str(value))
    return subject[:500]


def _alert_cadence(recurrence: dict) -> str:
    return {
        "daily": "Daily",
        "weekdays": "Weekday",
        "weekly": "Weekly",
        "monthly": "Monthly",
    }.get(str(recurrence.get("recurrence") or "").casefold(), "Scheduled")


def build_html_email(
    recurrence: dict,
    group: dict,
    columns: list[str],
    rows: list[dict[str, str]],
) -> str:
    heading_cells = "".join(
        f'<th style="padding:11px 12px;border:1px solid #0a6266;'
        f'background:#0d7377;color:#ffffff;text-align:left;font-size:12px;'
        f'font-weight:700;white-space:nowrap">{html.escape(column)}</th>'
        for column in columns
    )
    body_rows = []
    for index, row in enumerate(rows):
        background = "#ffffff" if index % 2 == 0 else "#f2f7f6"
        cells = "".join(
            f'<td style="padding:10px 12px;border:1px solid #d7e1df;'
            f'background:{background};color:#1a1814;font-size:12px;'
            f'vertical-align:top">{html.escape(str(row.get(column, "")))}</td>'
            for column in columns
        )
        body_rows.append(f"<tr>{cells}</tr>")
    report_button = ""
    if recurrence.get("report_url"):
        report_button = (
            '<table role="presentation" cellspacing="0" cellpadding="0" '
            'style="border-collapse:separate"><tr><td style="background:#ffffff;'
            'border:1px solid #ffffff;border-radius:6px">'
            f'<a href="{html.escape(recurrence["report_url"], quote=True)}" '
            'style="display:inline-block;padding:10px 16px;color:#0d7377;'
            'font-size:13px;font-weight:700;text-decoration:none">'
            "Open report</a></td></tr></table>"
        )
    recurrence_name = html.escape(recurrence["name"])
    group_name = html.escape(group["display_name"])
    report_name = html.escape(recurrence["report_name"])
    owner_name = html.escape(
        str(recurrence.get("owner_name") or "the report owner")
    )
    cadence = _alert_cadence(recurrence)
    return f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="width:100%;border-collapse:collapse;background:#eef2f1;
                  font-family:Segoe UI,Arial,sans-serif">
      <tr>
        <td align="center" style="padding:24px 12px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="width:100%;max-width:960px;border-collapse:separate;
                        background:#ffffff;border:1px solid #cfdad8;border-radius:12px">
            <tr>
              <td style="padding:26px 30px;background:#0d7377;color:#ffffff;
                         border-radius:12px 12px 0 0">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="vertical-align:middle">
                      <div style="margin:0 0 6px;color:#d8f0ef;font-size:11px;
                                  font-weight:700;letter-spacing:1.2px">
                        {cadence.upper()} ALERT
                      </div>
                      <div style="margin:0;color:#ffffff;font-size:25px;
                                  font-weight:700;line-height:1.2">
                        {recurrence_name}
                      </div>
                      <div style="margin-top:7px;color:#e7f5f4;font-size:14px;
                                  line-height:1.35">
                        {group_name}
                      </div>
                    </td>
                  </tr>
                </table>
                <div style="margin-top:20px">{report_button}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:0 30px">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="width:100%;border-collapse:collapse;background:#e8f3f2;
                              border:1px solid #c4ddda">
                  <tr>
                    <td style="padding:13px 14px;vertical-align:top">
                      <div style="color:#526561;font-size:10px;font-weight:700">REPORT</div>
                      <div style="margin-top:4px;color:#183c3d;font-size:13px;
                                  font-weight:600;line-height:1.3">{report_name}</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 30px 28px">
                <div style="margin:0 0 12px;color:#1a1814;font-size:15px;
                            font-weight:700">
                  Alert results
                </div>
                <div style="width:100%;overflow-x:auto">
                  <table width="100%" cellspacing="0" cellpadding="0"
                         style="width:100%;border-collapse:collapse;
                                border:1px solid #bfcfcd">
                    <thead><tr>{heading_cells}</tr></thead>
                    <tbody>{''.join(body_rows)}</tbody>
                  </table>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:15px 30px;background:#f5f7f6;color:#66716f;
                         border-top:1px solid #dbe3e1;border-radius:0 0 12px 12px;
                         font-size:12px;line-height:1.5">
                <div style="color:#344744;font-weight:600">
                  {cadence} alert created by the METO MX Analytics team.
                </div>
                <div style="margin-top:3px">
                  For questions or issues with this report, contact {owner_name},
                  the report owner.
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """


def _failure_notification_message(
    recurrence: dict,
    owner: dict,
    error: str,
    refresh: dict | None = None,
) -> dict:
    report_button = ""
    if recurrence.get("report_url"):
        report_button = (
            '<table role="presentation" cellspacing="0" cellpadding="0" '
            'style="border-collapse:separate"><tr><td style="background:#8f2d2d;'
            'border:1px solid #8f2d2d;border-radius:6px">'
            f'<a href="{html.escape(recurrence["report_url"], quote=True)}" '
            'style="display:inline-block;padding:10px 16px;color:#ffffff;'
            'font-size:13px;font-weight:700;text-decoration:none">'
            "Open the Power BI report</a></td></tr></table>"
        )
    refresh_html = ""
    if refresh:
        refresh_html = (
            "<tr>"
            '<td style="padding:10px 12px;border:1px solid #e1c5c5;'
            'background:#fff7f7;font-size:12px;font-weight:700">Latest refresh</td>'
            f'<td style="padding:10px 12px;border:1px solid #e1c5c5;'
            f'background:#ffffff;font-size:12px">'
            f'{html.escape(str(refresh.get("status") or "Unknown"))}'
            f' - {html.escape(str(refresh.get("end_time") or refresh.get("start_time") or "No timestamp"))}'
            "</td></tr>"
        )
    visual_label = recurrence.get("visual_title") or recurrence["visual_name"]
    page_label = recurrence.get("page_display_name") or recurrence["page_name"]
    body = f"""
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="width:100%;border-collapse:collapse;background:#f3eeee;
                  font-family:Segoe UI,Arial,sans-serif">
      <tr>
        <td align="center" style="padding:24px 12px">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="width:100%;max-width:760px;border-collapse:separate;
                        background:#ffffff;border:1px solid #dfcaca;border-radius:12px">
            <tr>
              <td style="padding:25px 28px;background:#8f2d2d;color:#ffffff;
                         border-radius:12px 12px 0 0">
                <div style="margin:0 0 7px;color:#f6dede;font-size:11px;
                            font-weight:700;letter-spacing:1.2px">
                  DELIVERY BLOCKED
                </div>
                <div style="margin:0;color:#ffffff;font-size:25px;font-weight:700;
                            line-height:1.2">
                  Power BI alert failed
                </div>
                <div style="margin-top:8px;color:#f9eaea;font-size:14px;line-height:1.4">
                  No alert data was sent. Metronome stopped the run before delivery.
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 10px">
                <div style="margin:0 0 14px;color:#1a1814;font-size:16px;
                            font-weight:700">
                  {html.escape(recurrence["name"])}
                </div>
                <table width="100%" cellspacing="0" cellpadding="0"
                       style="width:100%;border-collapse:collapse;color:#1a1814">
                  <tr>
                    <td style="padding:10px 12px;border:1px solid #e1c5c5;
                               background:#fff7f7;font-size:12px;font-weight:700">Report</td>
                    <td style="padding:10px 12px;border:1px solid #e1c5c5;
                               background:#ffffff;font-size:12px">
                      {html.escape(recurrence["report_name"])}
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:10px 12px;border:1px solid #e1c5c5;
                               background:#fff7f7;font-size:12px;font-weight:700">
                      Page / visual
                    </td>
                    <td style="padding:10px 12px;border:1px solid #e1c5c5;
                               background:#ffffff;font-size:12px">
                      {html.escape(page_label)} / {html.escape(visual_label)}
                    </td>
                  </tr>
                  {refresh_html}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 28px">
                <div style="padding:16px;background:#fff2f2;border:1px solid #dcaeae;
                            border-radius:8px">
                  <div style="margin:0 0 6px;color:#7a2020;font-size:12px;
                              font-weight:700">
                    What needs attention
                  </div>
                  <div style="color:#451b1b;font-size:13px;line-height:1.5">
                    {html.escape(error)}
                  </div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:4px 28px 26px">
                {report_button}
                <div style="margin-top:16px;color:#433f38;font-size:13px;line-height:1.5">
                  Fix the refresh, visual, permissions, or alert configuration.
                  Then use Create drafts to validate the full run before sending again.
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 28px;background:#f8f4f4;color:#746363;
                         border-top:1px solid #eadada;border-radius:0 0 12px 12px;
                         font-size:11px;line-height:1.4">
                Alert owner: {html.escape(owner["name"])}. Detected by Metronome at
                {html.escape(_now().isoformat(timespec="minutes"))} local time.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """
    return {
        "to": ";".join(_parse_recipients(owner.get("email") or "")),
        "subject": f"Metronome alert failed: {recurrence['name']}"[:500],
        "html_body": body,
    }


def _notify_owner_of_failure(
    recurrence: dict,
    owner: dict | None,
    error: str,
    refresh: dict | None = None,
) -> dict:
    if not owner:
        return {
            "status": "not_sent",
            "reason": "The alert owner could not be resolved from the People registry.",
        }
    try:
        message = _failure_notification_message(recurrence, owner, error, refresh)
        _launch_outlook_payload([message], "send")
        return {
            "status": "launched",
            "owner_name": owner["name"],
            "owner_email": message["to"],
        }
    except Exception as exc:
        logger.exception("Could not notify recurrence owner %s about a failure", owner.get("name"))
        return {
            "status": "failed",
            "owner_name": owner.get("name"),
            "reason": str(exc).strip() or exc.__class__.__name__,
        }


def _row_to_recurrence(db, row) -> dict:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["delivery_mode"] = item.get("delivery_mode") or "subgroups"
    item["recipients"] = item.get("recipients") or ""
    item["weekdays"] = _normalize_weekdays(item.get("weekdays"))
    groups = db.execute(
        "SELECT id, match_value, display_name, recipients, enabled FROM pbi_recurrence_groups WHERE recurrence_id = ? ORDER BY id",
        (item["id"],),
    ).fetchall()
    rules = db.execute(
        "SELECT id, column_name, operator, compare_value FROM pbi_recurrence_rules WHERE recurrence_id = ? ORDER BY position, id",
        (item["id"],),
    ).fetchall()
    item["groups"] = [{**dict(group), "enabled": bool(group["enabled"])} for group in groups]
    item["rules"] = [dict(rule) for rule in rules]
    return item


def _load_recurrence(db, recurrence_id: int) -> dict:
    row = db.execute("SELECT * FROM pbi_recurrences WHERE id = ?", (recurrence_id,)).fetchone()
    if not row:
        raise KeyError("Recurrence not found")
    return _row_to_recurrence(db, row)


def _save_children(db, recurrence_id: int, body: RecurrenceWrite) -> None:
    db.execute("DELETE FROM pbi_recurrence_groups WHERE recurrence_id = ?", (recurrence_id,))
    db.execute("DELETE FROM pbi_recurrence_rules WHERE recurrence_id = ?", (recurrence_id,))
    for group in body.groups:
        db.execute(
            """INSERT INTO pbi_recurrence_groups
               (recurrence_id, match_value, display_name, recipients, enabled)
               VALUES (?, ?, ?, ?, ?)""",
            (recurrence_id, group.match_value, group.display_name, group.recipients, int(group.enabled)),
        )
    for position, rule in enumerate(body.rules):
        db.execute(
            """INSERT INTO pbi_recurrence_rules
               (recurrence_id, position, column_name, operator, compare_value)
               VALUES (?, ?, ?, ?, ?)""",
            (recurrence_id, position, rule.column_name, rule.operator, rule.compare_value),
        )


def _write_values(body: RecurrenceWrite, next_run_at: str | None) -> tuple:
    return (
        body.name,
        int(body.enabled),
        body.workspace_id,
        body.workspace_name,
        body.report_id,
        body.report_name,
        body.report_url,
        body.embed_url,
        body.dataset_id,
        body.page_name,
        body.page_display_name,
        body.visual_name,
        body.visual_title,
        body.visual_type,
        body.owner_name,
        body.delivery_mode,
        body.recipients,
        body.group_column,
        body.subject_template,
        body.recurrence,
        ",".join(body.weekdays),
        body.month_day,
        body.send_time,
        next_run_at,
    )


def _advance_schedule(recurrence: dict) -> str | None:
    if not recurrence.get("enabled"):
        return None
    return _iso(
        calculate_next_run(
            recurrence["recurrence"],
            recurrence["send_time"],
            recurrence.get("weekdays"),
            recurrence.get("month_day"),
        )
    )


def _record_run_start(recurrence_id: int, trigger_type: str, mode: str) -> int:
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO pbi_recurrence_runs
               (recurrence_id, trigger_type, mode, status, started_at)
               VALUES (?, ?, ?, 'running', ?)""",
            (recurrence_id, trigger_type, mode, _iso(_now())),
        )
        return cursor.lastrowid


def _finish_run(
    recurrence_id: int,
    run_id: int,
    *,
    trigger_type: str,
    status: str,
    exported_rows: int = 0,
    matched_rows: int = 0,
    email_count: int = 0,
    detail: dict | None = None,
    error: str | None = None,
) -> None:
    now = _iso(_now())
    with get_db() as db:
        recurrence = _load_recurrence(db, recurrence_id)
        next_run = _advance_schedule(recurrence) if trigger_type == "scheduled" else recurrence.get("next_run_at")
        db.execute(
            """UPDATE pbi_recurrence_runs SET
                   status = ?, finished_at = ?, exported_rows = ?, matched_rows = ?,
                   email_count = ?, detail = ?, error = ?
               WHERE id = ?""",
            (status, now, exported_rows, matched_rows, email_count, json.dumps(detail or {}), error, run_id),
        )
        db.execute(
            """UPDATE pbi_recurrences SET
                   next_run_at = ?, last_run_at = ?,
                   last_sent_at = CASE WHEN ? = 'sent' THEN ? ELSE last_sent_at END,
                   last_status = ?, last_error = ?, last_row_count = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (next_run, now, status, now, status, error, matched_rows, recurrence_id),
        )


def run_recurrence(recurrence_id: int, *, mode: str = "send", trigger_type: str = "manual") -> dict:
    if mode not in {"send", "draft"}:
        raise ValueError("Mode must be send or draft.")
    with get_db() as db:
        _load_recurrence(db, recurrence_id)
    with _RUN_LOCK:
        if recurrence_id in _RUNNING_IDS:
            raise RuntimeError("This recurrence is already running.")
        _RUNNING_IDS.add(recurrence_id)

    run_id: int | None = None
    recurrence: dict | None = None
    owner: dict | None = None
    refresh: dict | None = None
    failure_notification_eligible = True
    try:
        run_id = _record_run_start(recurrence_id, trigger_type, mode)
        with get_db() as db:
            recurrence = _load_recurrence(db, recurrence_id)

        owner = _resolve_recurrence_owner(recurrence)
        if not owner:
            raise RuntimeError(
                "The alert owner is missing from Management > Create > People. "
                "Assign a valid owner before this alert can run."
            )
        try:
            _parse_recipients(owner.get("email") or "")
        except ValueError as exc:
            raise RuntimeError(
                f"Alert owner '{owner['name']}' needs a valid email in "
                "Management > Create > People."
            ) from exc
        refresh = _require_successful_refresh(recurrence)
        exported = export_visual_data(
            recurrence["report_id"],
            recurrence["embed_url"],
            recurrence["page_name"],
            recurrence["visual_name"],
            PBI_VISUAL_EXPORT_MAX_ROWS,
            workspace_id=recurrence["workspace_id"],
            dataset_id=recurrence.get("dataset_id"),
        )
        columns, rows = parse_visual_csv(exported["data"])
        delivery_mode = recurrence.get("delivery_mode") or "subgroups"
        required_columns = {rule["column_name"] for rule in recurrence["rules"]}
        if delivery_mode == "subgroups":
            required_columns.add(recurrence["group_column"])
        missing_columns = sorted(column for column in required_columns if column not in columns)
        if missing_columns:
            raise RuntimeError(
                "The visual schema changed and required columns are missing: " + ", ".join(missing_columns)
            )
        filtered_rows = apply_rules(rows, recurrence["rules"])
        messages: list[dict] = []
        group_results: list[dict] = []
        matched_total = 0
        delivery_groups = (
            [{
                "display_name": "All rows",
                "match_value": None,
                "recipients": recurrence.get("recipients") or "",
                "enabled": True,
            }]
            if delivery_mode == "single"
            else recurrence["groups"]
        )
        for group in delivery_groups:
            if not group["enabled"]:
                continue
            group_rows = filtered_rows if delivery_mode == "single" else [
                row for row in filtered_rows
                if _match_key(row.get(recurrence["group_column"])) == _match_key(group["match_value"])
            ]
            matched_total += len(group_rows)
            recipients = _parse_recipients(group["recipients"])
            group_results.append(
                {
                    "group": group["display_name"],
                    "match_value": group["match_value"],
                    "rows": len(group_rows),
                    "recipient_count": len(recipients),
                }
            )
            if not group_rows:
                continue
            messages.append(
                {
                    "to": ";".join(recipients),
                    "subject": _format_subject(
                        recurrence["subject_template"], recurrence, group, len(group_rows)
                    ),
                    "html_body": build_html_email(recurrence, group, columns, group_rows),
                }
            )

        if messages:
            _launch_outlook_payload(messages, mode)
            status = "sent" if mode == "send" else "drafted"
        else:
            status = "no_rows"
        failure_notification_eligible = False
        detail = {
            "columns": columns,
            "groups": group_results,
            "visual": exported.get("visual"),
            "owner": {"name": owner["name"], "email": owner["email"]},
            "refresh": refresh,
            "export_limit_reached": len(rows) >= PBI_VISUAL_EXPORT_MAX_ROWS,
        }
        _finish_run(
            recurrence_id,
            run_id,
            trigger_type=trigger_type,
            status=status,
            exported_rows=len(rows),
            matched_rows=matched_total,
            email_count=len(messages),
            detail=detail,
        )
        return {
            "status": status,
            "run_id": run_id,
            "exported_rows": len(rows),
            "matched_rows": matched_total,
            "email_count": len(messages),
            "groups": group_results,
        }
    except Exception as exc:
        error = str(exc).strip() or exc.__class__.__name__
        if isinstance(exc, RecurrenceRefreshError):
            refresh = exc.refresh
        failure_notification = (
            _notify_owner_of_failure(recurrence, owner, error, refresh)
            if mode == "send" and recurrence and failure_notification_eligible
            else {
                "status": "not_applicable",
                "reason": (
                    "Failure notifications are sent only when an actual send run fails "
                    "before its delivery outcome is established."
                ),
            }
        )
        if run_id is not None:
            _finish_run(
                recurrence_id,
                run_id,
                trigger_type=trigger_type,
                status="failed",
                detail={
                    "owner": (
                        {"name": owner.get("name"), "email": owner.get("email")}
                        if owner
                        else None
                    ),
                    "refresh": refresh,
                    "failure_notification": failure_notification,
                },
                error=error,
            )
        raise
    finally:
        with _RUN_LOCK:
            _RUNNING_IDS.discard(recurrence_id)


def dispatch_due_recurrences() -> list[dict]:
    now = _iso(_now())
    with get_db() as db:
        rows = db.execute(
            """SELECT id, name FROM pbi_recurrences
               WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
               ORDER BY next_run_at, id""",
            (now,),
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        try:
            result = run_recurrence(row["id"], mode="send", trigger_type="scheduled")
            results.append({"id": row["id"], "name": row["name"], **result})
        except Exception as exc:
            logger.exception("Power BI recurrence %s failed: %s", row["id"], exc)
            results.append({"id": row["id"], "name": row["name"], "status": "failed", "error": str(exc)})
    return results


@router.get("/status")
def recurrence_status():
    runtime = visual_export_runtime_status()
    return {
        "auth_mode": pbi_auth_mode(),
        "cached_account": pbi_auth.cached_token_available(),
        "system_timezone": str(datetime.now().astimezone().tzinfo),
        **runtime,
    }


@router.get("/reports")
def recurrence_reports():
    try:
        payload = pbi_fetch.fetch_workspace_reports()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with get_db() as db:
        local_rows = db.execute(
            """SELECT r.id, r.name, r.pbi_dataset_id,
                      COALESCE(p.name, r.owner) AS owner,
                      r.pbi_last_refresh_at, r.pbi_refresh_status,
                      p.email AS owner_email
               FROM reports r
               LEFT JOIN people p
                 ON lower(trim(p.name)) = lower(trim(r.owner))
               WHERE r.archived = 0"""
        ).fetchall()
        people = db.execute(
            "SELECT id, name, role, email FROM people ORDER BY name COLLATE NOCASE"
        ).fetchall()
    by_dataset = {
        row["pbi_dataset_id"]: dict(row) for row in local_rows if row["pbi_dataset_id"]
    }
    by_name = {row["name"].strip().casefold(): dict(row) for row in local_rows}
    for report in payload["reports"]:
        local = by_dataset.get(report.get("dataset_id")) or by_name.get(
            report["name"].strip().casefold(),
        )
        report["local_report_id"] = local.get("id") if local else None
        report["owner_name"] = local.get("owner") if local else None
        report["owner_email"] = local.get("owner_email") if local else None
        report["last_refresh_at"] = local.get("pbi_last_refresh_at") if local else None
        report["refresh_status"] = local.get("pbi_refresh_status") if local else None
    payload["reports"].sort(key=lambda report: report["name"].casefold())
    payload["people"] = [dict(person) for person in people]
    return payload


@router.get("/reports/{report_id}/pages")
def recurrence_report_pages(report_id: str, workspace_id: str):
    try:
        return {"pages": pbi_fetch.fetch_report_pages(workspace_id, report_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/visuals/discover")
def discover_report_visuals(body: VisualSelection):
    try:
        visuals = list_visuals(body.report_id, body.embed_url, body.page_name)
    except PbiVisualExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"visuals": visuals}


@router.post("/preview")
def preview_report_visual(body: VisualPreview):
    try:
        exported = export_visual_data(
            body.report_id,
            body.embed_url,
            body.page_name,
            body.visual_name,
            PBI_VISUAL_EXPORT_MAX_ROWS,
            workspace_id=body.workspace_id,
            dataset_id=body.dataset_id,
        )
        columns, rows = parse_visual_csv(exported["data"])
    except PbiVisualExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    values = {
        column: sorted({str(row.get(column, "")) for row in rows}, key=lambda value: value.casefold())[:500]
        for column in columns
    }
    return {
        "columns": columns,
        "rows": rows[:200],
        "row_count": len(rows),
        "export_method": exported.get("export_method"),
        "values": values,
        "values_limited": {column: len({str(row.get(column, "")) for row in rows}) > 500 for column in columns},
        "export_limit_reached": len(rows) >= PBI_VISUAL_EXPORT_MAX_ROWS,
        "visual": exported.get("visual"),
    }


@router.get("")
def list_recurrences():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM pbi_recurrences ORDER BY enabled DESC, name COLLATE NOCASE"
        ).fetchall()
        return [_row_to_recurrence(db, row) for row in rows]


@router.post("")
def create_recurrence(body: RecurrenceWrite, request: Request):
    try:
        _ensure_write_owner(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    next_run = _iso(
        calculate_next_run(body.recurrence, body.send_time, body.weekdays, body.month_day)
    ) if body.enabled else None
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO pbi_recurrences
               (name, enabled, workspace_id, workspace_name, report_id, report_name,
                report_url, embed_url, dataset_id, page_name, page_display_name,
                visual_name, visual_title, visual_type, owner_name, delivery_mode, recipients,
                group_column, subject_template, recurrence, weekdays, month_day,
                send_time, next_run_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*_write_values(body, next_run), get_actor(request)),
        )
        recurrence_id = cursor.lastrowid
        _save_children(db, recurrence_id, body)
        log_event(db, "recurrence", recurrence_id, body.name, "created", body.report_name, get_actor(request))
        return _load_recurrence(db, recurrence_id)


@router.get("/{recurrence_id}")
def get_recurrence(recurrence_id: int):
    with get_db() as db:
        try:
            return _load_recurrence(db, recurrence_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{recurrence_id}")
def update_recurrence(recurrence_id: int, body: RecurrenceWrite, request: Request):
    try:
        _ensure_write_owner(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    next_run = _iso(
        calculate_next_run(body.recurrence, body.send_time, body.weekdays, body.month_day)
    ) if body.enabled else None
    with get_db() as db:
        if not db.execute("SELECT id FROM pbi_recurrences WHERE id = ?", (recurrence_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Recurrence not found")
        db.execute(
            """UPDATE pbi_recurrences SET
                   name = ?, enabled = ?, workspace_id = ?, workspace_name = ?,
                   report_id = ?, report_name = ?, report_url = ?, embed_url = ?, dataset_id = ?,
                   page_name = ?, page_display_name = ?, visual_name = ?, visual_title = ?,
                   visual_type = ?, owner_name = ?, delivery_mode = ?, recipients = ?, group_column = ?,
                   subject_template = ?, recurrence = ?, weekdays = ?, month_day = ?,
                   send_time = ?, next_run_at = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (*_write_values(body, next_run), recurrence_id),
        )
        _save_children(db, recurrence_id, body)
        log_event(db, "recurrence", recurrence_id, body.name, "updated", body.report_name, get_actor(request))
        return _load_recurrence(db, recurrence_id)


@router.delete("/{recurrence_id}")
def delete_recurrence(recurrence_id: int, request: Request):
    with get_db() as db:
        row = db.execute("SELECT name FROM pbi_recurrences WHERE id = ?", (recurrence_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recurrence not found")
        db.execute("DELETE FROM pbi_recurrences WHERE id = ?", (recurrence_id,))
        log_event(db, "recurrence", recurrence_id, row["name"], "deleted", None, get_actor(request))
    return {"status": "deleted", "id": recurrence_id}


@router.get("/{recurrence_id}/runs")
def recurrence_runs(recurrence_id: int, limit: int = 25):
    limit = min(100, max(1, limit))
    with get_db() as db:
        if not db.execute("SELECT id FROM pbi_recurrences WHERE id = ?", (recurrence_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Recurrence not found")
        rows = db.execute(
            """SELECT * FROM pbi_recurrence_runs
               WHERE recurrence_id = ? ORDER BY started_at DESC, id DESC LIMIT ?""",
            (recurrence_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item.get("detail") or "{}")
        except json.JSONDecodeError:
            item["detail"] = {}
        result.append(item)
    return result


@router.post("/{recurrence_id}/run")
def run_recurrence_now(recurrence_id: int, body: RunRequest, request: Request):
    try:
        result = run_recurrence(recurrence_id, mode=body.mode, trigger_type="manual")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PbiVisualExportError, OutlookEmailError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with get_db() as db:
        recurrence = db.execute("SELECT name FROM pbi_recurrences WHERE id = ?", (recurrence_id,)).fetchone()
        log_event(
            db,
            "recurrence",
            recurrence_id,
            recurrence["name"] if recurrence else None,
            "run_now",
            result["status"],
            get_actor(request),
        )
    return result
