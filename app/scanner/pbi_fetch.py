"""Headless Power BI REST fetch using the saved Microsoft account token.

Python port of tools/pbi_refresh_sync.ps1 and tools/pbi_usage_sync.ps1 data
fetching. Runs entirely inside the app process with a silently refreshed
access token from app.scanner.pbi_auth, so no PowerShell window, account
picker, or unlocked desktop session is needed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx

from app.database import get_db
from app.config import PBI_WORKSPACE
from app.scanner.control import assert_not_cancelled
from app.scanner.pbi_auth import get_access_token, resolve_proxy

logger = logging.getLogger(__name__)

PBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"
REQUEST_TIMEOUT_SECONDS = 120
MAX_ACTIVITY_PAGES_PER_DAY = 200


class PbiFetchError(RuntimeError):
    """Raised when the Power BI REST API rejects or fails a request."""

    def __init__(self, message: str, *, permission: bool = False):
        super().__init__(message)
        self.permission = permission


def _uuid_text(value: str, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PbiFetchError(f"{label} is not a valid Power BI identifier.") from exc


def _get_json(client: httpx.Client, token: str, url: str, params: dict | None = None) -> dict:
    response = client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if response.status_code in (401, 403):
        raise PbiFetchError(
            f"Power BI API returned {response.status_code} for {url.split('?')[0]}. "
            "The signed-in account may lack access or the token may be stale.",
            permission=True,
        )
    if response.status_code >= 400:
        raise PbiFetchError(
            f"Power BI API error {response.status_code} for {url.split('?')[0]}: {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise PbiFetchError(f"Power BI API returned non-JSON for {url.split('?')[0]}") from exc


def _find_workspace(client: httpx.Client, token: str, workspace_name: str) -> dict | None:
    escaped = workspace_name.replace("'", "''")
    body = _get_json(
        client, token, f"{PBI_API_BASE}/groups", params={"$filter": f"name eq '{escaped}'"}
    )
    for group in body.get("value") or []:
        if (group.get("name") or "").strip().lower() == workspace_name.strip().lower():
            return group
    # Fallback: list and match case-insensitively (filter is case sensitive)
    body = _get_json(client, token, f"{PBI_API_BASE}/groups", params={"$top": 5000})
    for group in body.get("value") or []:
        if (group.get("name") or "").strip().lower() == workspace_name.strip().lower():
            return group
    return None


def fetch_workspace_reports(workspace_name: str | None = None) -> dict:
    """Return the live report list for the configured Power BI workspace."""
    selected_workspace = (workspace_name or PBI_WORKSPACE or "").strip()
    if not selected_workspace:
        raise PbiFetchError("No Power BI workspace is configured. Set DG_PBI_WORKSPACE.")

    auth = get_access_token()
    token = auth["access_token"]
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, proxy=resolve_proxy(PBI_API_BASE)) as client:
        workspace = _find_workspace(client, token, selected_workspace)
        if not workspace:
            account = auth.get("account") or "the signed-in account"
            raise PbiFetchError(f"Workspace '{selected_workspace}' was not found for {account}.")
        workspace_id = _uuid_text(workspace.get("id"), "Workspace ID")
        reports = _get_json(
            client,
            token,
            f"{PBI_API_BASE}/groups/{workspace_id}/reports",
        ).get("value") or []

    return {
        "workspace": {"id": workspace_id, "name": workspace.get("name") or selected_workspace},
        "reports": [
            {
                "id": report.get("id"),
                "name": report.get("name"),
                "dataset_id": report.get("datasetId"),
                "web_url": report.get("webUrl"),
                "embed_url": report.get("embedUrl"),
            }
            for report in reports
            if report.get("id") and report.get("name") and report.get("embedUrl")
        ],
    }


def fetch_report_pages(workspace_id: str, report_id: str) -> list[dict]:
    """Return live report pages using stable technical page names."""
    workspace_guid = _uuid_text(workspace_id, "Workspace ID")
    report_guid = _uuid_text(report_id, "Report ID")
    auth = get_access_token()
    token = auth["access_token"]
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, proxy=resolve_proxy(PBI_API_BASE)) as client:
        body = _get_json(
            client,
            token,
            f"{PBI_API_BASE}/groups/{workspace_guid}/reports/{report_guid}/pages",
        )
    pages = body.get("value") or []
    return [
        {
            "name": page.get("name"),
            "display_name": page.get("displayName") or page.get("name"),
            "order": page.get("order", index),
        }
        for index, page in enumerate(pages)
        if page.get("name")
    ]


def fetch_refresh_payload(workspace_name: str, cancel_generation: int | None = None) -> dict:
    """Build the same payload shape that pbi_refresh_sync.ps1 POSTs back."""
    auth = get_access_token()
    token = auth["access_token"]

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, proxy=resolve_proxy(PBI_API_BASE)) as client:
        workspace = _find_workspace(client, token, workspace_name)
        if not workspace:
            raise PbiFetchError(f"Workspace '{workspace_name}' not found for {auth.get('account') or 'the signed-in account'}.")
        ws_id = workspace["id"]

        reports = (_get_json(client, token, f"{PBI_API_BASE}/groups/{ws_id}/reports").get("value")) or []
        datasets = (_get_json(client, token, f"{PBI_API_BASE}/groups/{ws_id}/datasets").get("value")) or []

        dataset_reports: dict[str, list[str]] = {}
        report_urls: dict[str, str] = {}
        for report in reports:
            dataset_id = report.get("datasetId")
            name = report.get("name")
            if not dataset_id or not name:
                continue
            dataset_reports.setdefault(dataset_id, []).append(name)
            report_urls[name] = report.get("webUrl")

        results = []
        for dataset in datasets:
            assert_not_cancelled(cancel_generation, "Power BI refresh sync")
            report_names = dataset_reports.get(dataset.get("id"))
            if not report_names:
                continue

            try:
                raw_schedule = _get_json(
                    client, token, f"{PBI_API_BASE}/groups/{ws_id}/datasets/{dataset['id']}/refreshSchedule"
                )
                schedule = {
                    "enabled": raw_schedule.get("enabled"),
                    "days": list(raw_schedule.get("days") or []),
                    "times": list(raw_schedule.get("times") or []),
                    "timezone": raw_schedule.get("localTimeZoneId"),
                }
            except PbiFetchError:
                schedule = {"enabled": False, "days": [], "times": [], "timezone": ""}

            last_refresh = None
            try:
                history = _get_json(
                    client,
                    token,
                    f"{PBI_API_BASE}/groups/{ws_id}/datasets/{dataset['id']}/refreshes",
                    params={"$top": 1},
                )
                entries = history.get("value") or []
                if entries:
                    entry = entries[0]
                    last_refresh = {
                        "start_time": entry.get("startTime"),
                        "end_time": entry.get("endTime"),
                        "status": entry.get("status"),
                        "error": entry.get("serviceExceptionJson") or None,
                    }
            except PbiFetchError:
                last_refresh = None

            for report_name in report_names:
                results.append(
                    {
                        "report_name": report_name,
                        "dataset_name": dataset.get("name"),
                        "dataset_id": dataset.get("id"),
                        "web_url": report_urls.get(report_name),
                        "schedule": schedule,
                        "last_refresh": last_refresh,
                    }
                )

    return {
        "workspace": workspace_name,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "reports": results,
    }


def run_refresh_sync(workspace_name: str, cancel_generation: int | None = None) -> dict:
    """Fetch refresh metadata headlessly and import it into the DB."""
    from app.scanner.pbi_sync import import_pbi_data

    payload = fetch_refresh_payload(workspace_name, cancel_generation)
    logger.info("Headless PBI refresh fetch found %s report entries", len(payload["reports"]))
    return import_pbi_data(payload, cancel_generation)


def _already_synced_usage_days() -> set[str]:
    with get_db() as db:
        rows = db.execute("SELECT date FROM pbi_usage_days").fetchall()
    return {row["date"] for row in rows}


def fetch_usage_payload(days_back: int = 30, cancel_generation: int | None = None) -> dict:
    """Build the same payload shape that pbi_usage_sync.ps1 POSTs back."""
    auth = get_access_token()
    token = auth["access_token"]

    synced = _already_synced_usage_days()
    today = datetime.now(timezone.utc).date()
    days_to_fetch = []
    for offset in range(1, days_back + 1):
        day = (today - timedelta(days=offset)).isoformat()
        if day not in synced:
            days_to_fetch.append(day)

    entries: list[dict] = []
    days_synced: list[str] = []

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, proxy=resolve_proxy(PBI_API_BASE)) as client:
        for day in days_to_fetch:
            assert_not_cancelled(cancel_generation, "Power BI usage sync")
            url = f"{PBI_API_BASE}/admin/activityevents"
            params = {
                "startDateTime": f"'{day}T00:00:00.000Z'",
                "endDateTime": f"'{day}T23:59:59.999Z'",
            }
            view_counts: dict[str, int] = {}
            user_sets: dict[str, set] = {}
            try:
                pages = 0
                while url and pages < MAX_ACTIVITY_PAGES_PER_DAY:
                    body = _get_json(client, token, url, params=params)
                    params = None  # continuationUri already carries the query
                    pages += 1
                    for event in body.get("activityEventEntities") or []:
                        if event.get("Activity") != "ViewReport" or not event.get("ReportName"):
                            continue
                        name = event["ReportName"]
                        view_counts[name] = view_counts.get(name, 0) + 1
                        if event.get("UserId"):
                            user_sets.setdefault(name, set()).add(event["UserId"])
                    if body.get("lastResultSet") or not body.get("continuationToken"):
                        break
                    url = body.get("continuationUri")
            except PbiFetchError as exc:
                if exc.permission:
                    raise PbiFetchError(
                        "Power BI activity events permission error: the signed-in account needs "
                        "the Power BI/Fabric administrator role for usage sync.",
                        permission=True,
                    ) from exc
                logger.warning("Usage fetch failed for %s: %s", day, exc)
                continue

            for name, count in view_counts.items():
                entries.append(
                    {
                        "report_name": name,
                        "date": day,
                        "view_count": count,
                        "unique_users": len(user_sets.get(name) or ()),
                    }
                )
            days_synced.append(day)

    return {"entries": entries, "days_synced": days_synced}


def run_usage_sync(days_back: int = 30, cancel_generation: int | None = None) -> dict:
    """Fetch usage activity headlessly and import it into the DB."""
    from app.scanner.pbi_sync import import_pbi_usage_data

    payload = fetch_usage_payload(days_back, cancel_generation)
    logger.info(
        "Headless PBI usage fetch: %s day(s), %s report entries",
        len(payload["days_synced"]),
        len(payload["entries"]),
    )
    return import_pbi_usage_data(payload)
