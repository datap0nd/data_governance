"""MicroStrategy REST adapter for ASAP catalog and report data.

The browser is used only to establish the user's SSO session.  Playwright's
browser-context request client shares that session cookie jar, so the worker
can exchange it for a short-lived ``X-MSTR-AuthToken`` and use documented
MicroStrategy Library APIs from that point onward.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPORT_OBJECT_TYPE = 3
FOLDER_OBJECT_TYPE = 8
PAGE_SIZE = 200
RESULT_PAGE_SIZE = 5000


class MicroStrategyAPIError(RuntimeError):
    """A documented MicroStrategy API contract was unavailable or invalid."""


def library_base_url(site: dict[str, Any]) -> str:
    """Return the Library application root on the configured ASAP origin."""
    configured = str(site.get("library_url") or "").strip()
    if configured:
        return configured.rstrip("/")
    source = str(site.get("auth_url") or site.get("base_url") or "").strip()
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MicroStrategyAPIError("ASAP needs a valid portal URL before API discovery can run.")
    return f"{parsed.scheme}://{parsed.netloc}/MicroStrategyLibrary"


def _items(payload: Any, *preferred_keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (*preferred_keys, "items", "results", "objects"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _object_type(item: dict[str, Any]) -> int | None:
    value = item.get("type")
    if isinstance(value, dict):
        value = value.get("id") or value.get("value")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slug(value: str, fallback: str = "prompt") -> str:
    result = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").casefold()
    if not result or not result[0].isalpha():
        result = f"{fallback}_{result}".strip("_")
    return result[:100]


def _canonical_week(value: str) -> str:
    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})-?W?(\d{2})", text, re.I)
    return f"{match.group(1)}-W{match.group(2)}" if match else text


def _prompt_label(prompt: dict[str, Any]) -> str:
    return str(prompt.get("title") or prompt.get("name") or "Prompt").strip()


def _prompt_key(prompt: dict[str, Any]) -> str:
    return str(prompt.get("key") or prompt.get("id") or "").strip()


def _control_type(prompt: dict[str, Any], option_names: list[str]) -> str:
    prompt_type = str(prompt.get("type") or "").upper()
    label = _prompt_label(prompt)
    if "week" in label.casefold() or (
        option_names and all(re.fullmatch(r"\d{4}(?:-?W)?\d{2}", item, re.I) for item in option_names)
    ):
        return "week"
    if prompt_type == "VALUE":
        return "text"
    try:
        maximum = int(prompt.get("max"))
    except (TypeError, ValueError):
        maximum = 0
    return "select" if maximum == 1 else "multi_select"


class MicroStrategyAPI:
    """Small synchronous client over Playwright's shared API request context."""

    def __init__(self, request_context: Any, base_url: str, auth_token: str):
        self.request_context = request_context
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api"
        self.auth_token = auth_token

    @classmethod
    def from_browser(cls, page: Any, site: dict[str, Any]) -> "MicroStrategyAPI":
        base_url = library_base_url(site)
        response = page.context.request.get(
            f"{base_url}/api/auth/token",
            headers={"Accept": "application/json"},
            timeout=60_000,
        )
        try:
            token = response.headers.get("x-mstr-authtoken")
            if response.status != 204 or not token:
                detail = response.text()[:500] if response.status != 204 else "missing X-MSTR-AuthToken"
                raise MicroStrategyAPIError(
                    "The authenticated browser session could not create a MicroStrategy API token "
                    f"(HTTP {response.status}: {detail}). Verify that Library REST and browser SSO are enabled."
                )
            return cls(page.context.request, base_url, token)
        finally:
            response.dispose()

    def _call(
        self,
        method: str,
        path: str,
        *,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[Any, dict[str, str]]:
        headers = {"Accept": "application/json", "X-MSTR-AuthToken": self.auth_token}
        if project_id:
            headers["X-MSTR-ProjectID"] = project_id
        options: dict[str, Any] = {"headers": headers, "timeout": 180_000}
        if params:
            options["params"] = {key: str(value) for key, value in params.items()}
        if body is not None:
            options["data"] = body
            headers["Content-Type"] = "application/json"
        response = getattr(self.request_context, method.casefold())(
            f"{self.api_url}/{path.lstrip('/')}", **options
        )
        try:
            if response.status not in expected:
                detail = response.text()[:1000]
                raise MicroStrategyAPIError(
                    f"MicroStrategy {method.upper()} {path} returned HTTP {response.status}: {detail}"
                )
            payload = None if response.status == 204 else response.json()
            return payload, dict(response.headers)
        finally:
            response.dispose()

    def get(self, path: str, **kwargs) -> Any:
        return self._call("get", path, **kwargs)[0]

    def post(self, path: str, **kwargs) -> Any:
        return self._call("post", path, **kwargs)[0]

    def put(self, path: str, **kwargs) -> Any:
        return self._call("put", path, **kwargs)[0]

    def delete(self, path: str, **kwargs) -> Any:
        return self._call("delete", path, **kwargs)[0]

    def projects(self) -> list[dict[str, Any]]:
        payload = self.get("projects")
        projects = _items(payload, "projects")
        return [item for item in projects if item.get("id") and item.get("status", 0) in (0, None)]

    def _folder_objects(self, project_id: str, folder_id: str) -> list[dict[str, Any]]:
        payload = self.get(
            f"folders/{folder_id}",
            project_id=project_id,
            params={"type": f"{REPORT_OBJECT_TYPE},{FOLDER_OBJECT_TYPE}", "hidden": "false"},
        )
        return _items(payload, "contents", "children")

    def reports(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        """Traverse Shared Reports by stable object IDs, never by rendered layout."""
        project_id = str(project["id"])
        found: list[dict[str, Any]] = []
        seen_folders: set[str] = set()
        stack: list[tuple[str, list[str]]] = [("preDefined/7", ["Shared Reports"])]
        while stack:
            folder_id, path = stack.pop()
            if folder_id in seen_folders:
                continue
            seen_folders.add(folder_id)
            for item in self._folder_objects(project_id, folder_id):
                object_id = str(item.get("id") or "").strip()
                name = str(item.get("name") or "").strip()
                if not object_id or not name:
                    continue
                object_type = _object_type(item)
                if object_type == FOLDER_OBJECT_TYPE:
                    stack.append((object_id, [*path, name]))
                elif object_type == REPORT_OBJECT_TYPE:
                    found.append({**item, "folder_path": path, "project_id": project_id})
        return found

    def _create_report_instance(self, project_id: str, report_id: str) -> dict[str, Any]:
        payload = self.post(
            f"v2/reports/{report_id}/instances",
            project_id=project_id,
            params={"offset": 0, "limit": RESULT_PAGE_SIZE},
            body={},
            expected=(200, 201),
        )
        if not isinstance(payload, dict) or not payload.get("instanceId"):
            raise MicroStrategyAPIError(f"Report {report_id} did not return a MicroStrategy instance ID.")
        return payload

    def _delete_report_instance(self, project_id: str, report_id: str, instance_id: str):
        try:
            self.delete(
                f"reports/{report_id}/instances/{instance_id}",
                project_id=project_id,
                expected=(204, 404),
            )
        except MicroStrategyAPIError:
            # Instance cleanup must not replace a successful catalog or export
            # with a failure on older Library versions that expire it eagerly.
            pass

    def prompts(self, project_id: str, report_id: str, instance_id: str) -> list[dict[str, Any]]:
        payload = self.get(
            f"reports/{report_id}/instances/{instance_id}/prompts",
            project_id=project_id,
        )
        return _items(payload, "prompts")

    def prompt_options(
        self,
        project_id: str,
        report_id: str,
        instance_id: str,
        prompt: dict[str, Any],
    ) -> list[dict[str, Any]]:
        prompt_type = str(prompt.get("type") or "").upper()
        if prompt_type == "VALUE":
            return []
        suffix = "objects" if prompt_type == "OBJECTS" else "elements"
        key = _prompt_key(prompt)
        if not key:
            raise MicroStrategyAPIError(f"Prompt {_prompt_label(prompt)!r} has no stable key or ID.")
        result: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self.get(
                f"reports/{report_id}/instances/{instance_id}/prompts/{key}/{suffix}",
                project_id=project_id,
                params={"offset": offset, "limit": PAGE_SIZE},
            )
            page = _items(payload, suffix)
            result.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        return result

    def catalog_report(self, project: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        project_id = str(project["id"])
        report_id = str(report["id"])
        instance = self._create_report_instance(project_id, report_id)
        instance_id = str(instance["instanceId"])
        try:
            definitions: list[dict[str, Any]] = []
            used_keys: set[str] = set()
            for position, prompt in enumerate(self.prompts(project_id, report_id, instance_id)):
                key = _prompt_key(prompt)
                prompt_type = str(prompt.get("type") or "").upper()
                if not key or prompt_type not in {"ELEMENTS", "OBJECTS", "VALUE"}:
                    raise MicroStrategyAPIError(
                        f"Report {report.get('name')} uses unsupported prompt type {prompt_type or 'unknown'}."
                    )
                options = self.prompt_options(project_id, report_id, instance_id, prompt)
                option_names = [str(item.get("name") or "").strip() for item in options]
                control_type = _control_type(prompt, option_names)
                if control_type == "week":
                    option_names = [_canonical_week(item) for item in option_names]
                label = _prompt_label(prompt)
                filter_key = _slug(label)
                if filter_key in used_keys:
                    filter_key = f"{filter_key[:90]}_{position + 1}"
                used_keys.add(filter_key)
                answer_map: dict[str, dict[str, Any]] = {}
                for raw_name, display_name, option in zip(
                    [str(item.get("name") or "").strip() for item in options], option_names, options
                ):
                    if not display_name or not option.get("id"):
                        continue
                    answer = {"id": str(option["id"]), "name": raw_name or display_name}
                    if option.get("type") is not None:
                        answer["type"] = option["type"]
                    answer_map[display_name] = answer
                definitions.append({
                    "filter_key": filter_key,
                    "label": label,
                    "control_label": label,
                    "control_type": control_type,
                    "options": list(dict.fromkeys(item for item in option_names if item)),
                    "automation": {
                        "provider": "microstrategy_rest",
                        "prompt_key": key,
                        "prompt_type": prompt_type,
                        "answers": answer_map,
                    },
                    "required": bool(prompt.get("required")),
                    "position": position,
                })
            folder_path = [str(item) for item in report.get("folder_path") or []]
            project_name = str(project.get("name") or project_id)
            discovery_key = f"{project_id}:{report_id}"
            return {
                "discovery_key": discovery_key,
                "name": str(report.get("name") or report_id),
                "report_url": f"{self.base_url}/app/{project_id}/{report_id}",
                "ready_text": None,
                "download_text": "MicroStrategy Data API",
                "automation": {
                    "provider": "microstrategy_rest",
                    "api_version": "v2",
                    "project_id": project_id,
                    "project_name": project_name,
                    "object_id": report_id,
                    "object_type": REPORT_OBJECT_TYPE,
                    "category_path": [project_name, *folder_path, str(report.get("name") or report_id)],
                },
                "filters": definitions,
            }
        finally:
            self._delete_report_instance(project_id, report_id, instance_id)

    def discover_catalog(
        self,
        *,
        report_progress=None,
        selected_refs: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        projects = self.projects()
        if not projects:
            raise MicroStrategyAPIError("The MicroStrategy session has no accessible projects.")
        report_objects: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for project in projects:
            for report in self.reports(project):
                reference = (str(project.get("id")), str(report.get("id")))
                if selected_refs and reference not in selected_refs:
                    continue
                report_objects.append((project, report))
        if not report_objects:
            if selected_refs:
                raise MicroStrategyAPIError(
                    "The requested report object was not returned by the current MicroStrategy catalog."
                )
            raise MicroStrategyAPIError(
                "MicroStrategy returned no accessible grid reports. The existing governance catalog was not changed."
            )
        for index, (project, report) in enumerate(report_objects, start=1):
            if report_progress:
                report_progress("running", {
                    "stage": "prompt_catalog",
                    "message": f"Reading API prompts for report {index} of {len(report_objects)}.",
                    "current_report": report.get("name"),
                    "report_index": index,
                    "report_count": len(report_objects),
                })
            result.append(self.catalog_report(project, report))
        return result

    @staticmethod
    def _selected_values(job: dict[str, Any], definition: dict[str, Any], period: Any) -> list[str]:
        value = period if definition.get("control_type") == "week" and period else (
            job.get("selections") or {}
        ).get(definition["filter_key"])
        if value in (None, "", []):
            return []
        values = value if isinstance(value, list) else [value]
        return [_canonical_week(str(item)) if definition.get("control_type") == "week" else str(item) for item in values]

    def _answer_body(self, job: dict[str, Any], prompts: list[dict[str, Any]], period: Any) -> dict[str, Any]:
        by_key = {
            str((definition.get("automation") or {}).get("prompt_key")): definition
            for definition in job["report"].get("filters", [])
        }
        answers = []
        for prompt in prompts:
            if prompt.get("closed") is True:
                continue
            key = _prompt_key(prompt)
            definition = by_key.get(key)
            if not definition:
                raise MicroStrategyAPIError(
                    f"Prompt {_prompt_label(prompt)!r} is not in the latest weekly catalog. Refresh the catalog first."
                )
            values = self._selected_values(job, definition, period)
            if not values:
                if prompt.get("required") or definition.get("required"):
                    raise MicroStrategyAPIError(f"Required prompt {_prompt_label(prompt)!r} has no flow value.")
                answers.append({"key": key, "type": str(prompt.get("type") or "").upper(), "answers": []})
                continue
            automation = definition.get("automation") or {}
            prompt_type = str(prompt.get("type") or automation.get("prompt_type") or "").upper()
            if prompt_type == "VALUE":
                prompt_answers: Any = values
            else:
                mapping = automation.get("answers") or {}
                missing = [value for value in values if value not in mapping]
                if missing:
                    raise MicroStrategyAPIError(
                        f"{definition['label']} value {missing[0]!r} has no current MicroStrategy object ID. Refresh the catalog."
                    )
                prompt_answers = []
                for value in values:
                    mapped = mapping[value]
                    answer = {"id": mapped["id"]}
                    if prompt_type == "OBJECTS" and mapped.get("type") is not None:
                        answer["type"] = mapped["type"]
                    prompt_answers.append(answer)
            answers.append({"key": key, "type": prompt_type, "answers": prompt_answers})
        return {"prompts": answers}

    def execute_report(self, job: dict[str, Any], period: Any) -> list[dict[str, Any]]:
        automation = job["report"].get("automation") or {}
        project_id = str(automation.get("project_id") or "")
        report_id = str(automation.get("object_id") or "")
        if automation.get("provider") != "microstrategy_rest" or not project_id or not report_id:
            raise MicroStrategyAPIError("This report predates API discovery. Refresh the ASAP catalog before running it.")
        payload = self._create_report_instance(project_id, report_id)
        instance_id = str(payload["instanceId"])
        try:
            for _ in range(10):
                if int(payload.get("status") or 0) != 2:
                    break
                prompts = self.prompts(project_id, report_id, instance_id)
                open_prompts = [item for item in prompts if item.get("closed") is not True]
                if not open_prompts:
                    raise MicroStrategyAPIError("MicroStrategy reports unresolved prompts but returned no open prompt definitions.")
                self.put(
                    f"reports/{report_id}/instances/{instance_id}/prompts/answers",
                    project_id=project_id,
                    body=self._answer_body(job, open_prompts, period),
                    expected=(204,),
                )
                payload = self.get(
                    f"v2/reports/{report_id}/instances/{instance_id}",
                    project_id=project_id,
                    params={"offset": 0, "limit": RESULT_PAGE_SIZE},
                )
            if int(payload.get("status") or 0) == 2:
                raise MicroStrategyAPIError("MicroStrategy still has unresolved nested prompts after 10 API rounds.")
            pages = [payload]
            paging = (payload.get("data") or {}).get("paging") or {}
            total = int(paging.get("total") or 0)
            current = int(paging.get("current") or 0)
            offset = int(paging.get("offset") or 0) + current
            while offset < total:
                page = self.get(
                    f"v2/reports/{report_id}/instances/{instance_id}",
                    project_id=project_id,
                    params={"offset": offset, "limit": RESULT_PAGE_SIZE},
                )
                pages.append(page)
                page_paging = (page.get("data") or {}).get("paging") or {}
                received = int(page_paging.get("current") or 0)
                if received <= 0:
                    raise MicroStrategyAPIError("MicroStrategy result paging stopped before all rows were returned.")
                offset += received
            return pages
        finally:
            self._delete_report_instance(project_id, report_id, instance_id)

    def download_csv(self, job: dict[str, Any], period: Any, output: Path) -> Path:
        pages = self.execute_report(job, period)
        header, rows = report_pages_to_rows(pages)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        return output


def _grid_parts(payload: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    grid = ((payload.get("definition") or {}).get("grid") or {})
    if grid.get("crossTab"):
        raise MicroStrategyAPIError("Cross-tab reports are not supported for lossless CSV export yet.")
    row_units = grid.get("rows") or []
    header: list[str] = []
    for unit in row_units:
        forms = unit.get("forms") or []
        if not forms:
            header.append(str(unit.get("name") or "Attribute"))
        elif len(forms) == 1:
            header.append(str(unit.get("name") or forms[0].get("name") or "Attribute"))
        else:
            header.extend(
                f"{unit.get('name') or 'Attribute'} {form.get('name') or index + 1}"
                for index, form in enumerate(forms)
            )
    metrics = grid.get("metrics") or []
    if not metrics:
        for unit in grid.get("columns") or []:
            if unit.get("type") == "templateMetrics":
                metrics.extend(unit.get("elements") or [])
            elif unit:
                raise MicroStrategyAPIError("Column attributes require cross-tab expansion and cannot be exported losslessly yet.")
    header.extend(str(metric.get("name") or f"Metric {index + 1}") for index, metric in enumerate(metrics))
    return header, row_units, metrics


def report_pages_to_rows(pages: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    """Flatten a normal MicroStrategy Data API v2 grid without guessing."""
    if not pages:
        raise MicroStrategyAPIError("MicroStrategy returned no report result pages.")
    header, _, _ = _grid_parts(pages[0])
    rows: list[list[Any]] = []
    for page in pages:
        page_header, row_units, metrics = _grid_parts(page)
        if page_header != header:
            raise MicroStrategyAPIError("MicroStrategy changed the report definition between result pages.")
        data = page.get("data") or {}
        row_headers = (data.get("headers") or {}).get("rows") or []
        metric_values = (data.get("metricValues") or {}).get("raw") or []
        for row_index, indices in enumerate(row_headers):
            if len(indices) != len(row_units):
                raise MicroStrategyAPIError("MicroStrategy row header shape does not match the report definition.")
            row: list[Any] = []
            for unit, element_index in zip(row_units, indices):
                elements = unit.get("elements") or []
                try:
                    element = elements[int(element_index)]
                except (IndexError, TypeError, ValueError):
                    raise MicroStrategyAPIError("MicroStrategy returned an invalid attribute element index.") from None
                values = element.get("formValues") or [element.get("name") or ""]
                expected = max(1, len(unit.get("forms") or []))
                if len(values) != expected:
                    raise MicroStrategyAPIError("MicroStrategy attribute forms do not match the returned values.")
                row.extend(values)
            values = metric_values[row_index] if row_index < len(metric_values) else []
            if len(values) != len(metrics):
                raise MicroStrategyAPIError("MicroStrategy metric values do not match the report definition.")
            row.extend("" if value is None else value for value in values)
            rows.append(row)
    if not header:
        raise MicroStrategyAPIError("MicroStrategy returned a report with no exportable columns.")
    return header, rows
