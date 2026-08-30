"""Live Power BI semantic-model acquisition.

Production scans read the workspace, try XMLA/TOM first, and use Fabric
``getDefinition`` only as a capability fallback.  Acquisition is completed in
memory before callers mutate the local catalog.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import time
from pathlib import PurePosixPath

import httpx

from app.config import (
    FABRIC_API_BASE,
    PBI_METADATA_TIMEOUT_SECONDS,
    PBI_TOM_HELPER,
    PBI_WORKSPACE,
)
from app.scanner.pbi_auth import FABRIC_SCOPE, get_access_token, resolve_proxy
from app.scanner.pbi_fetch import fetch_workspace_reports
from app.scanner.pbix_parser import MeasureInfo
from app.scanner.tmdl_parser import (
    METADATA_TABLES,
    ParsedTable,
    _extract_hashtable_value,
    _parse_m_expression,
    parse_expressions_text,
    parse_tmdl_text,
)
from app.scanner.walker import DiscoveredReport

logger = logging.getLogger(__name__)


class PowerBiMetadataError(RuntimeError):
    """Raised when no live metadata provider can return a complete snapshot."""


def _compact_provider_error(value: object, *, provider: str) -> str:
    """Turn provider failures into one safe, operator-readable sentence."""
    text = " ".join(str(value or "unknown failure").split())
    lowered = text.casefold()
    if provider == "xmla" and (
        "helper is unavailable at" in lowered
        or "helper is not configured" in lowered
    ):
        return "the local metadata helper is not installed"
    if "aadsts65002" in lowered or "not preauthorized for the requested api" in lowered:
        return (
            "the saved Microsoft sign-in client cannot request the Fabric definition "
            "permission; XMLA/TOM or a tenant app registration is required"
        )
    if len(text) > 280:
        return text[:277].rstrip() + "..."
    return text


def _live_failure_message(failures: list[dict[str, str]]) -> str:
    model_count = len({item["dataset_id"] for item in failures})
    report_names = list(dict.fromkeys(item["report_name"] for item in failures))
    xmla_errors = list(
        dict.fromkeys(
            _compact_provider_error(item["xmla_error"], provider="xmla")
            for item in failures
        )
    )
    fabric_errors = list(
        dict.fromkeys(
            _compact_provider_error(item["fabric_error"], provider="fabric")
            for item in failures
        )
    )

    def summarize(values: list[str]) -> str:
        suffix = f" (+{len(values) - 1} other cause(s))" if len(values) > 1 else ""
        return values[0] + suffix

    shown_names = ", ".join(report_names[:5])
    if len(report_names) > 5:
        shown_names += f", +{len(report_names) - 5} more"
    return (
        "Live semantic-model acquisition was incomplete; the existing catalog was retained. "
        f"Could not refresh {model_count} semantic model(s) used by "
        f"{len(report_names)} report(s). XMLA/TOM: {summarize(xmla_errors)}. "
        f"Fabric getDefinition: {summarize(fabric_errors)}. "
        f"Affected reports: {shown_names}."
    )


def _modes(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _report_from_tom_model(report: dict, workspace: dict, model: dict) -> DiscoveredReport:
    tables: list[ParsedTable] = []
    measures: list[MeasureInfo] = []
    business_owner = None
    report_owner = None

    for item in model.get("tables") or []:
        table_name = str(item.get("name") or "").strip()
        if not table_name:
            continue
        expressions = [
            str(partition.get("expression") or "").strip()
            for partition in item.get("partitions") or []
            if str(partition.get("expression") or "").strip()
        ]
        expression = "\n\n".join(expressions) or None
        is_metadata = table_name in METADATA_TABLES
        metadata_value = _extract_hashtable_value(expression) if is_metadata and expression else None
        source = _parse_m_expression(expression) if expression and not is_metadata else None
        parsed = ParsedTable(
            table_name=table_name,
            columns=[str(column) for column in item.get("columns") or [] if str(column)],
            measures=[],
            partition_name=(item.get("partitions") or [{}])[0].get("name"),
            mode=_modes((item.get("partitions") or [{}])[0].get("mode")),
            m_expression=expression,
            source=source,
            file_path=f"xmla://{report.get('dataset_id')}/{table_name}",
            is_metadata=is_metadata,
            metadata_value=metadata_value,
        )
        for measure in item.get("measures") or []:
            name = str(measure.get("name") or "").strip()
            if not name:
                continue
            dax = measure.get("expression")
            parsed.measures.append((name, dax))
            measures.append(
                MeasureInfo(
                    table_name=table_name,
                    measure_name=name,
                    dax_expression=dax,
                )
            )
        tables.append(parsed)
        if is_metadata and metadata_value:
            if table_name == "Business Owner":
                business_owner = metadata_value
            elif table_name == "Report Owner":
                report_owner = metadata_value

    if not tables:
        raise PowerBiMetadataError(
            f"XMLA returned no tables for semantic model {report.get('dataset_id')}."
        )
    return DiscoveredReport(
        name=report["name"],
        tmdl_path=f"xmla://{workspace['id']}/{report['dataset_id']}",
        tables=tables,
        measures=measures,
        expressions={
            str(name): str(value)
            for name, value in (model.get("expressions") or {}).items()
            if str(name).strip()
        },
        business_owner=business_owner,
        report_owner=report_owner,
        workspace_id=workspace["id"],
        pbi_report_id=report["id"],
        dataset_id=report["dataset_id"],
        powerbi_url=report.get("web_url"),
        metadata_provider="xmla_tom",
    )


def _read_with_tom(report: dict, workspace: dict) -> DiscoveredReport:
    helper = str(PBI_TOM_HELPER or "").strip()
    if not helper:
        raise PowerBiMetadataError("XMLA/TOM helper is not configured.")
    from pathlib import Path

    helper_path = Path(helper)
    if not helper_path.is_file():
        raise PowerBiMetadataError(f"XMLA/TOM helper is unavailable at {helper_path}.")
    token_record = get_access_token()
    token = token_record["access_token"]
    request = {
        "workspace": workspace["name"],
        "datasetId": report["dataset_id"],
        "datasetName": report.get("dataset_name") or report["name"],
        "accessToken": token,
        "accessTokenExpiresAt": int(
            token_record.get("access_token_expires_at") or (time.time() + 300)
        ),
    }
    try:
        completed = subprocess.run(
            [str(helper_path)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=max(30, int(PBI_METADATA_TIMEOUT_SECONDS)),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise PowerBiMetadataError("XMLA/TOM metadata acquisition timed out.") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "XMLA helper failed").strip()
        message = message.replace(token, "[redacted]")
        raise PowerBiMetadataError(message[:1000])
    try:
        model = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise PowerBiMetadataError("XMLA helper returned invalid JSON.") from exc
    if model.get("error"):
        raise PowerBiMetadataError(str(model["error"])[:1000])
    return _report_from_tom_model(report, workspace, model)


def _fabric_response_json(response: httpx.Response, *, operation: str) -> dict:
    if response.status_code in (401, 403):
        raise PowerBiMetadataError(
            f"Fabric rejected {operation} with {response.status_code}; the saved account "
            "needs read-write access plus SemanticModel.ReadWrite.All (or Item.ReadWrite.All)."
        )
    if response.status_code >= 400:
        raise PowerBiMetadataError(
            f"Fabric {operation} failed with {response.status_code}: {response.text[:500]}"
        )
    try:
        return response.json() if response.content else {}
    except ValueError as exc:
        raise PowerBiMetadataError(f"Fabric {operation} returned invalid JSON.") from exc


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After") or response.headers.get("retry-after")
    try:
        return min(10.0, max(0.5, float(value or 2)))
    except ValueError:
        return 2.0


def _fabric_definition(workspace_id: str, dataset_id: str) -> dict:
    token = get_access_token(scope=FABRIC_SCOPE)["access_token"]
    url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/semanticModels/"
        f"{dataset_id}/getDefinition?format=TMDL"
    )
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + max(30, int(PBI_METADATA_TIMEOUT_SECONDS))
    with httpx.Client(
        timeout=min(60, max(15, int(PBI_METADATA_TIMEOUT_SECONDS))),
        proxy=resolve_proxy(url),
    ) as client:
        response = client.post(url, headers=headers)
        if response.status_code == 200:
            return _fabric_response_json(response, operation="getDefinition")
        if response.status_code != 202:
            return _fabric_response_json(response, operation="getDefinition")
        poll_url = response.headers.get("Location") or response.headers.get("location")
        operation_id = (
            response.headers.get("x-ms-operation-id")
            or response.headers.get("X-MS-OPERATION-ID")
        )
        if not poll_url:
            raise PowerBiMetadataError("Fabric accepted getDefinition but supplied no poll URL.")
        while time.monotonic() < deadline:
            time.sleep(_retry_after_seconds(response))
            response = client.get(poll_url, headers=headers)
            if response.status_code in (202, 429):
                continue
            state = _fabric_response_json(response, operation="getDefinition polling")
            status = str(state.get("status") or "").casefold()
            if not status and (state.get("definition") or state.get("parts")):
                return state
            if status in {"running", "notstarted", "undefined"}:
                poll_url = (
                    response.headers.get("Location")
                    or response.headers.get("location")
                    or poll_url
                )
                continue
            if status == "failed":
                error = state.get("error") or {}
                message = error.get("message") if isinstance(error, dict) else error
                raise PowerBiMetadataError(
                    f"Fabric getDefinition operation failed: {message or 'no reason supplied'}."
                )
            if status == "succeeded":
                result_url = (
                    response.headers.get("Location")
                    or response.headers.get("location")
                    or (
                        f"{FABRIC_API_BASE}/operations/{operation_id}/result"
                        if operation_id
                        else None
                    )
                )
                if not result_url:
                    raise PowerBiMetadataError(
                        "Fabric completed getDefinition but supplied no result URL."
                    )
                result = client.get(result_url, headers=headers)
                return _fabric_response_json(result, operation="getDefinition result")
            raise PowerBiMetadataError(
                f"Fabric returned an unexpected operation status: {status or 'missing'}."
            )
    raise PowerBiMetadataError("Fabric getDefinition timed out.")


def _decode_definition_parts(payload: dict) -> dict[str, str]:
    definition = payload.get("definition") or payload
    parts = definition.get("parts") if isinstance(definition, dict) else None
    if not isinstance(parts, list):
        raise PowerBiMetadataError("Fabric getDefinition returned no definition parts.")
    decoded: dict[str, str] = {}
    for part in parts:
        path = str(part.get("path") or "").replace("\\", "/").strip("/")
        raw = part.get("payload")
        if not path or not isinstance(raw, str):
            continue
        payload_type = str(part.get("payloadType") or "InlineBase64").casefold()
        try:
            text = (
                base64.b64decode(raw).decode("utf-8-sig")
                if "base64" in payload_type
                else raw
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise PowerBiMetadataError(f"Fabric definition part {path!r} is invalid.") from exc
        decoded[path] = text
    return decoded


def _read_with_fabric(report: dict, workspace: dict) -> DiscoveredReport:
    parts = _decode_definition_parts(
        _fabric_definition(workspace["id"], report["dataset_id"])
    )
    expressions: dict[str, str] = {}
    tables: list[ParsedTable] = []
    for path, text in sorted(parts.items()):
        leaf = PurePosixPath(path).name.casefold()
        if leaf == "expressions.tmdl":
            expressions.update(parse_expressions_text(text))
        if not leaf.endswith(".tmdl") or "/tables/" not in f"/{path.casefold()}":
            continue
        parsed = parse_tmdl_text(text, file_path=f"fabric://{workspace['id']}/{path}")
        if parsed:
            tables.append(parsed)
    if not tables:
        raise PowerBiMetadataError(
            f"Fabric returned no TMDL tables for semantic model {report.get('dataset_id')}."
        )

    business_owner = None
    report_owner = None
    measures: list[MeasureInfo] = []
    for table in tables:
        if table.is_metadata and table.metadata_value:
            if table.table_name == "Business Owner":
                business_owner = table.metadata_value
            elif table.table_name == "Report Owner":
                report_owner = table.metadata_value
        for name, dax in table.measures:
            measures.append(
                MeasureInfo(
                    table_name=table.table_name,
                    measure_name=name,
                    dax_expression=dax,
                )
            )
    return DiscoveredReport(
        name=report["name"],
        tmdl_path=f"fabric://{workspace['id']}/{report['dataset_id']}",
        tables=tables,
        measures=measures,
        expressions=expressions,
        business_owner=business_owner,
        report_owner=report_owner,
        workspace_id=workspace["id"],
        pbi_report_id=report["id"],
        dataset_id=report["dataset_id"],
        powerbi_url=report.get("web_url"),
        metadata_provider="fabric_get_definition",
    )


def read_live_reports(workspace_name: str | None = None) -> list[DiscoveredReport]:
    """Read a complete live snapshot or raise before catalog mutation."""
    payload = fetch_workspace_reports(workspace_name or PBI_WORKSPACE)
    workspace = payload["workspace"]
    reports = [report for report in payload["reports"] if report.get("dataset_id")]
    if not reports:
        raise PowerBiMetadataError(
            f"Workspace {workspace.get('name')!r} contains no readable semantic models."
        )

    model_cache: dict[str, DiscoveredReport] = {}
    failures: list[dict[str, str]] = []
    model_failures: dict[str, tuple[str, str]] = {}
    discovered: list[DiscoveredReport] = []
    for report in reports:
        dataset_id = str(report["dataset_id"])
        template = model_cache.get(dataset_id)
        if template is None:
            failed_model = model_failures.get(dataset_id)
            if failed_model is None:
                xmla_error = None
                try:
                    template = _read_with_tom(report, workspace)
                except Exception as exc:
                    xmla_error = str(exc)
                    logger.info("XMLA/TOM unavailable for %s: %s", report["name"], exc)
                    try:
                        template = _read_with_fabric(report, workspace)
                    except Exception as fabric_exc:
                        failed_model = (xmla_error, str(fabric_exc))
                        model_failures[dataset_id] = failed_model
            if failed_model is not None:
                failures.append(
                    {
                        "dataset_id": dataset_id,
                        "report_name": str(report["name"]),
                        "xmla_error": failed_model[0],
                        "fabric_error": failed_model[1],
                    }
                )
                continue
            model_cache[dataset_id] = template

        discovered.append(
            DiscoveredReport(
                name=report["name"],
                tmdl_path=template.tmdl_path,
                tables=template.tables,
                measures=template.measures,
                expressions=template.expressions,
                business_owner=template.business_owner,
                report_owner=template.report_owner,
                workspace_id=workspace["id"],
                pbi_report_id=report["id"],
                dataset_id=dataset_id,
                powerbi_url=report.get("web_url"),
                metadata_provider=template.metadata_provider,
            )
        )
    if failures:
        raise PowerBiMetadataError(_live_failure_message(failures))
    return discovered
