"""Live Power BI visual discovery and summarized-data export.

The official JavaScript client identifies the exact saved visual and first
attempts Power BI's summarized export. If that API rejects a table that uses
model or report measures, the authoring extension reads the visual's current
field bindings and filters for an official Execute Queries REST fallback.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
import threading
from pathlib import Path
from urllib.parse import urlsplit

from app.config import PBI_VISUAL_EXPORT_MAX_ROWS, PBI_VISUAL_EXPORT_TIMEOUT_SECONDS
from app.pbi_visual_query import (
    PbiVisualQueryError,
    apply_visual_field_formats,
    execute_visual_query,
)
from app.scanner.pbi_auth import get_access_token

logger = logging.getLogger(__name__)

POWER_BI_CLIENT_URLS = (
    "https://cdn.jsdelivr.net/npm/powerbi-client@2.23.1/dist/powerbi.min.js",
    "https://unpkg.com/powerbi-client@2.23.1/dist/powerbi.min.js",
)
POWER_BI_AUTHORING_URLS = (
    "https://cdn.jsdelivr.net/npm/powerbi-report-authoring@3.0.0/dist/powerbi-report-authoring.min.js",
    "https://unpkg.com/powerbi-report-authoring@3.0.0/dist/powerbi-report-authoring.min.js",
)
TABLE_VISUAL_TYPES = {"table", "tableex", "pivottable", "matrix"}
_BROWSER_START_ATTEMPTS = 2
_VISUAL_ACTION_LOCK = threading.Lock()


class PbiVisualExportError(RuntimeError):
    """Raised when an embedded report or selected visual cannot be exported."""


_RUNTIME_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>
    html, body, #report {{ width: 100%; height: 100%; margin: 0; overflow: hidden; }}
  </style>
</head>
<body>
  <div id="report"></div>
  <script>
    window.metronomeRun = function(config) {{
      return new Promise((resolve, reject) => {{
        const container = document.getElementById("report");
        const models = window["powerbi-client"].models;
        let finished = false;
        let loadOperationStarted = false;
        let lastReportError = null;
        let activePage = null;
        let activeVisual = null;
        let pageVisuals = [];
        let fallbackStarted = false;
        let queryFallbackArmed = false;
        const stop = (fn, value) => {{
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          fn(value);
        }};
        const errorDetails = (error) => {{
          const detail = error && error.detail ? error.detail : error;
          const technicalDetails = detail && detail.technicalDetails ? detail.technicalDetails : {{}};
          return {{
            message: (detail && (detail.detailedMessage || detail.message)) ||
                     (error && error.message) || "Power BI returned an unknown error.",
            detailedMessage: detail && detail.detailedMessage ? detail.detailedMessage : null,
            errorCode: detail && detail.errorCode ? detail.errorCode : null,
            level: detail && detail.level ? detail.level : null,
            requestId: technicalDetails.requestId || null
          }};
        }};
        const describeError = error => errorDetails(error).message;
        const isVisualQueryError = (...errors) => errors.some(error => {{
          const details = error && error.message ? error : errorDetails(error);
          const text = [details.message, details.detailedMessage, details.errorCode]
            .filter(Boolean).join(" ").toLowerCase();
          return text.includes("error running visual data query") ||
                 text.includes("visual data query");
        }});
        const safeCall = async (operation, fallback = null) => {{
          try {{ return await operation(); }} catch (_) {{ return fallback; }}
        }};
        const resolveVisualTitle = async visual => {{
          for (const propertyName of ["titleText", "text"]) {{
            const titleProperty = await safeCall(
              () => visual.getProperty({{
                objectName: "title",
                propertyName
              }}),
              null
            );
            const propertyTitle = titleProperty && typeof titleProperty.value === "string"
              ? titleProperty.value.trim()
              : "";
            if (propertyTitle) return {{title: propertyTitle, titleSource: "property"}};
          }}

          const descriptorTitle = typeof visual.title === "string"
            ? visual.title.trim()
            : "";
          const normalizedDescriptor = descriptorTitle.toLowerCase().replace(/[^a-z0-9]/g, "");
          const normalizedType = String(visual.type || "").toLowerCase().replace(/[^a-z0-9]/g, "");
          const genericTitles = new Set(["matrix", "pivottable", "table", "tableex"]);
          if (
            descriptorTitle &&
            normalizedDescriptor !== normalizedType &&
            !genericTitles.has(normalizedDescriptor)
          ) {{
            return {{title: descriptorTitle, titleSource: "descriptor"}};
          }}
          return {{title: "", titleSource: null}};
        }};
        const describeVisual = async visual => ({{
          name: visual.name,
          type: visual.type,
          ...(await resolveVisualTitle(visual))
        }});
        const collectQuerySpec = async (report, page, selectedVisual, visuals) => {{
          const capabilities = await selectedVisual.getCapabilities();
          const roles = capabilities && Array.isArray(capabilities.dataRoles)
            ? capabilities.dataRoles
            : [];
          const fields = [];
          for (const role of roles) {{
            const targets = await selectedVisual.getDataFields(role.name);
            for (let index = 0; index < targets.length; index += 1) {{
              const target = targets[index];
              fields.push({{
                roleName: role.name,
                roleDisplayName: role.displayName || role.name,
                roleKind: role.kind,
                index,
                target,
                displayName: await safeCall(
                  () => selectedVisual.getDataFieldDisplayName(role.name, index),
                  null
                ),
                formatString: await safeCall(
                  () => selectedVisual.getFieldFormatString(role.name, index),
                  null
                ),
                fieldName: await safeCall(
                  () => selectedVisual.getDataFieldName(role.name, index),
                  null
                )
              }});
            }}
          }}

          const filters = [];
          const addFilters = (source, values) => {{
            for (const filter of values || []) filters.push({{source, filter}});
          }};
          addFilters("report", await report.getFilters());
          addFilters("page", await page.getFilters());
          addFilters("visual", await selectedVisual.getFilters());
          for (const visual of visuals) {{
            if (visual.name === selectedVisual.name || visual.type.toLowerCase() !== "slicer") continue;
            const state = await visual.getSlicerState();
            addFilters(`slicer:${{visual.name}}`, state && state.filters);
          }}
          return {{fields, filters}};
        }};
        const resolveQueryFallback = async exportError => {{
          if (fallbackStarted || finished || !activePage || !activeVisual) return false;
          fallbackStarted = true;
          try {{
            const querySpec = await collectQuerySpec(
              report,
              activePage,
              activeVisual,
              pageVisuals
            );
            stop(resolve, {{
              exportError,
              querySpec,
              visual: await describeVisual(activeVisual)
            }});
          }} catch (querySpecError) {{
            stop(
              reject,
              `${{exportError.message}} The visual query definition could not be read: ` +
              describeError(querySpecError)
            );
          }}
          return true;
        }};
        const timer = setTimeout(
          () => stop(
            reject,
            lastReportError
              ? `Power BI did not finish the visual export. ${{lastReportError.message}}`
              : "Power BI did not finish the visual export within the configured timeout."
          ),
          config.timeoutMs
        );

        window.powerbi.reset(container);
        const report = window.powerbi.load(container, {{
          type: "report",
          id: config.reportId,
          embedUrl: config.embedUrl,
          accessToken: config.accessToken,
          tokenType: models.TokenType.Aad,
          pageName: config.pageName,
          permissions: models.Permissions.Read,
          settings: {{
            panes: {{
              filters: {{ visible: false, expanded: false }},
              pageNavigation: {{ visible: false }}
            }}
          }}
        }});

        report.on("error", async event => {{
          lastReportError = errorDetails(event);
          if (
            config.action === "export" &&
            queryFallbackArmed &&
            isVisualQueryError(lastReportError)
          ) {{
            await resolveQueryFallback(lastReportError);
          }}
        }});
        report.on("loaded", async () => {{
          if (loadOperationStarted || finished) return;
          loadOperationStarted = true;
          try {{
            const pages = await report.getPages();
            const selectedPage = pages.find(page => page.name === config.pageName);
            if (!selectedPage) {{
              throw new Error("The saved report page no longer exists.");
            }}
            activePage = selectedPage;
            if (!selectedPage.isActive) {{
              await selectedPage.setActive();
            }}
            const visuals = await selectedPage.getVisuals();
            pageVisuals = visuals;
            if (config.action === "list") {{
              let listStarted = false;
              report.on("rendered", async () => {{
                if (listStarted || finished) return;
                listStarted = true;
                const describedVisuals = await Promise.all(
                  visuals.map(async visual => ({{
                    ...(await describeVisual(visual)),
                    displayMode: visual.layout && visual.layout.displayState
                      ? visual.layout.displayState.mode
                      : null
                  }}))
                );
                stop(resolve, describedVisuals);
              }});
              await report.render();
              return;
            }}
            const selectedVisual = visuals.find(visual => visual.name === config.visualName);
            if (!selectedVisual) {{
              throw new Error("The saved table visual no longer exists on this page.");
            }}
            activeVisual = selectedVisual;
            await Promise.all(
              visuals.map(visual => selectedPage.setVisualDisplayState(
                visual.name,
                visual.name === selectedVisual.name
                  ? models.VisualContainerDisplayMode.Visible
                  : models.VisualContainerDisplayMode.Hidden
              ))
            );
            let exportStarted = false;
            report.on("rendered", async () => {{
              if (exportStarted || finished || fallbackStarted) return;
              exportStarted = true;
              try {{
                const querySpec = await safeCall(
                  () => collectQuerySpec(report, activePage, selectedVisual, pageVisuals),
                  null
                );
                const result = await selectedVisual.exportData(
                  models.ExportDataType.Summarized,
                  config.maxRows
                );
                stop(resolve, {{
                  data: result.data,
                  querySpec,
                  visual: await describeVisual(selectedVisual)
                }});
              }} catch (error) {{
                const exportError = errorDetails(error);
                if (isVisualQueryError(exportError, lastReportError)) {{
                  await resolveQueryFallback(exportError);
                  return;
                }}
                stop(reject, exportError.message);
              }}
            }});
            lastReportError = null;
            queryFallbackArmed = true;
            await report.render();
          }} catch (error) {{
            const operationError = errorDetails(error);
            if (
              config.action === "export" &&
              isVisualQueryError(operationError, lastReportError) &&
              await resolveQueryFallback(operationError)
            ) {{
              return;
            }}
            stop(reject, operationError.message);
          }}
        }});
      }});
    }};
  </script>
</body>
</html>
"""


def _load_power_bi_client(page) -> str:
    """Load the browser client from the published npm package with a fallback CDN."""
    attempts: list[str] = []
    page.set_default_timeout(min(20000, PBI_VISUAL_EXPORT_TIMEOUT_SECONDS * 1000))
    for source_url in POWER_BI_CLIENT_URLS:
        source_host = urlsplit(source_url).hostname or source_url
        try:
            page.add_script_tag(url=source_url)
            ready = page.evaluate(
                "() => !!window.powerbi && !!window['powerbi-client'] && "
                "!!window['powerbi-client'].models"
            )
            if ready:
                return source_url
            attempts.append(f"{source_host}: script loaded without the Power BI client globals")
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            attempts.append(f"{source_host}: {message}")
    raise PbiVisualExportError(
        "The Power BI JavaScript client could not load. Allow cdn.jsdelivr.net or unpkg.com "
        "through the Windows system proxy. " + " | ".join(attempts)
    )


def _load_power_bi_authoring(page) -> str:
    """Load Microsoft's report-authoring extension after the core client."""
    attempts: list[str] = []
    for source_url in POWER_BI_AUTHORING_URLS:
        source_host = urlsplit(source_url).hostname or source_url
        try:
            page.add_script_tag(url=source_url)
            ready = page.evaluate(
                "() => !!window['powerbi-client'] && "
                "typeof window['powerbi-client'].VisualDescriptor.prototype.getDataFields === 'function'"
            )
            if ready:
                return source_url
            attempts.append(f"{source_host}: script loaded without the authoring APIs")
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            attempts.append(f"{source_host}: {message}")
    raise PbiVisualExportError(
        "The Power BI report-authoring client could not load. Allow cdn.jsdelivr.net or unpkg.com "
        "through the Windows system proxy. " + " | ".join(attempts)
    )


def _edge_candidates() -> list[Path]:
    candidates: list[Path] = []
    if platform.system() == "Windows":
        for root_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(root_name)
            if root:
                candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    elif platform.system() == "Darwin":
        candidates.extend(
            [
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
        )
    return [path for path in candidates if path.exists()]


def visual_export_runtime_status() -> dict:
    """Return a cheap readiness check without launching a browser."""
    edge_paths = _edge_candidates()
    return {
        "playwright_installed": importlib.util.find_spec("playwright") is not None,
        "edge_detected": bool(edge_paths) if platform.system() in {"Windows", "Darwin"} else False,
        "edge_path": str(edge_paths[0]) if edge_paths else None,
        "timeout_seconds": PBI_VISUAL_EXPORT_TIMEOUT_SECONDS,
        "max_rows": PBI_VISUAL_EXPORT_MAX_ROWS,
    }


def _launch_browser(playwright):
    launch_args = {
        "headless": True,
        "args": [
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-default-apps",
        ],
    }
    try:
        return playwright.chromium.launch(channel="msedge", **launch_args)
    except Exception as channel_error:
        for executable in _edge_candidates():
            try:
                return playwright.chromium.launch(executable_path=str(executable), **launch_args)
            except Exception:
                logger.debug("Could not launch browser at %s", executable, exc_info=True)
        raise PbiVisualExportError(
            "Headless Microsoft Edge could not start. Confirm Edge is installed for the Windows "
            "account running Metronome and that browser execution is not blocked by policy."
        ) from channel_error


def _is_transient_browser_close(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "browser has been closed",
            "target closed",
        )
    )


def _safe_close(resource) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        logger.debug("Playwright resource had already closed", exc_info=True)


def _run_browser_attempt(playwright, config: dict):
    browser = None
    context = None
    try:
        browser = _launch_browser(playwright)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=False,
        )
        page = context.new_page()
        page.set_default_timeout(PBI_VISUAL_EXPORT_TIMEOUT_SECONDS * 1000)
        page.set_content(_RUNTIME_HTML, wait_until="domcontentloaded")
        _load_power_bi_client(page)
        _load_power_bi_authoring(page)
        page.set_default_timeout(PBI_VISUAL_EXPORT_TIMEOUT_SECONDS * 1000)
        return page.evaluate("config => window.metronomeRun(config)", config)
    finally:
        _safe_close(context)
        _safe_close(browser)


def _run_browser_with_retry(
    *,
    playwright_factory,
    playwright_error_types: tuple[type[BaseException], ...],
    config: dict,
):
    # Edge is relatively expensive and can exit during startup when multiple API
    # requests launch headless instances at the same time. Serialize all visual
    # work in this process, and retry a closed target once with a fresh process.
    with _VISUAL_ACTION_LOCK:
        for attempt in range(1, _BROWSER_START_ATTEMPTS + 1):
            try:
                with playwright_factory() as playwright:
                    return _run_browser_attempt(playwright, config)
            except PbiVisualExportError:
                raise
            except playwright_error_types as exc:
                if attempt < _BROWSER_START_ATTEMPTS and _is_transient_browser_close(exc):
                    logger.warning(
                        "Headless Edge closed during Power BI visual export startup; retrying once"
                    )
                    continue
                message = (
                    str(exc).strip().splitlines()[0]
                    if str(exc).strip()
                    else exc.__class__.__name__
                )
                if _is_transient_browser_close(exc):
                    message = (
                        "Headless Edge closed before the Power BI export page was ready, "
                        "including one retry with a fresh browser. Technical detail: " + message
                    )
                raise PbiVisualExportError(f"Power BI visual export failed: {message}") from exc
            except Exception as exc:
                message = str(exc).strip() or exc.__class__.__name__
                raise PbiVisualExportError(f"Power BI visual export failed: {message}") from exc

    raise PbiVisualExportError("Power BI visual export failed before Edge could start.")


def _run_visual_action(
    *,
    report_id: str,
    embed_url: str,
    page_name: str,
    action: str,
    visual_name: str | None = None,
    max_rows: int | None = None,
):
    if action not in {"list", "export"}:
        raise ValueError("action must be list or export")
    if action == "export" and not visual_name:
        raise PbiVisualExportError("A visual must be selected before data can be exported.")

    auth = get_access_token()
    config = {
        "action": action,
        "reportId": report_id,
        "embedUrl": embed_url,
        "pageName": page_name,
        "visualName": visual_name,
        "maxRows": min(30000, max_rows or PBI_VISUAL_EXPORT_MAX_ROWS),
        "timeoutMs": PBI_VISUAL_EXPORT_TIMEOUT_SECONDS * 1000,
        "accessToken": auth["access_token"],
    }

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PbiVisualExportError(
            "Power BI visual export is not installed. Run setup.ps1 to install the Playwright dependency."
        ) from exc

    return _run_browser_with_retry(
        playwright_factory=sync_playwright,
        playwright_error_types=(PlaywrightTimeoutError, PlaywrightError),
        config=config,
    )


def list_visuals(report_id: str, embed_url: str, page_name: str) -> list[dict]:
    """Return the live visual descriptors for one report page."""
    result = _run_visual_action(
        report_id=report_id,
        embed_url=embed_url,
        page_name=page_name,
        action="list",
    )
    visuals = []
    for visual in result or []:
        visual_type = str(visual.get("type") or "")
        visuals.append(
            {
                **visual,
                "is_table": visual_type.casefold() in TABLE_VISUAL_TYPES,
            }
        )
    return visuals


def export_visual_data(
    report_id: str,
    embed_url: str,
    page_name: str,
    visual_name: str,
    max_rows: int | None = None,
    *,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
) -> dict:
    """Export summarized CSV data for the exact saved visual identifier."""
    result = _run_visual_action(
        report_id=report_id,
        embed_url=embed_url,
        page_name=page_name,
        action="export",
        visual_name=visual_name,
        max_rows=max_rows,
    )
    if isinstance(result, dict) and result.get("exportError") and result.get("querySpec"):
        if not workspace_id or not dataset_id:
            raise PbiVisualExportError(
                "Power BI's visual export API cannot query this table. Re-select the report so "
                "Metronome can use its workspace and semantic model for the REST fallback."
            )
        try:
            fallback = execute_visual_query(
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                query_spec=result["querySpec"],
                max_rows=min(30000, max_rows or PBI_VISUAL_EXPORT_MAX_ROWS),
            )
        except PbiVisualQueryError as exc:
            raise PbiVisualExportError(
                f"Power BI visual export failed, and the semantic-model fallback failed: {exc}"
            ) from exc
        return {
            **fallback,
            "visual": result.get("visual") or {},
            "export_method": "execute_queries",
        }
    if not isinstance(result, dict) or not isinstance(result.get("data"), str):
        raise PbiVisualExportError("Power BI returned an empty visual export response.")
    return {
        **result,
        "data": apply_visual_field_formats(result["data"], result.get("querySpec")),
        "export_method": "visual_export",
    }
