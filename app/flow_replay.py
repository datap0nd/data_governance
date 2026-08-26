"""Network-level export replay for ASAP and GSCM flow downloads.

Driving a portal UI is the slow, fragile part of a flow run: overlays swallow
clicks, popups float over controls, grids virtualize, and every element must
be found before it can be used. None of that fragility lives in the HTTP
request the portal ultimately sends to produce the export file. This module
captures that request once and replays it directly on later runs.

**Capture.** While the browser performs a normal UI-driven export, a
context-wide recorder observes every request, response, and download in the
window between "export triggered" and "file staged". The request that
actually produced the file is identified - by the download's own URL when the
browser reported one, otherwise by the last response that looked like a file
export - and stored as a *recipe*: method, URL, filtered headers, and body.
Recipes live next to the browser profile they belong to, because they are
only valid for that profile's signed-in sessions.

**Replay.** The next run of the same export task issues the recorded request
through the browser context's own HTTP client (``context.request``), which
shares the profile's live SSO cookies. No page renders, no popup needs
clearing, no element needs finding. The response is validated by content, not
by name: an HTML sign-in or error page, an empty body, or the wrong file
family rejects the replay and the run falls back to the browser flow - which
re-records a fresh recipe as it succeeds.

Safety properties, in order of importance:

- A recipe is keyed by site, report, export view, *and* requested period, so
  a request recorded for one configuration is never replayed for another.
- A replayed file passes the exact same content detection, container
  validation, and normalization as a browser download does.
- Recipes expire after ``RECIPE_MAX_AGE_DAYS`` and are refreshed by every
  successful browser export, bounding how stale a recorded request can get.
- A recipe that fails to replay - or replays into a file that fails
  processing - is forgotten immediately, so a broken recipe costs one HTTP
  round trip once, not on every run.
- ``Cookie`` and ``Authorization`` headers are never stored: authentication
  always comes live from the browser profile at replay time. A portal that
  authorizes exports some other way simply falls back to the browser flow.

Kill switches: set ``DG_FLOW_REPLAY=0`` in the worker's environment, or
``downloads.network_replay: false`` on one flow.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECIPES_FILENAME = ".export_replay.json"
RECIPE_VERSION = 1
RECIPE_MAX_AGE_DAYS = 14
#: Requests observed while one export runs. The window also contains the
#: portal's own chatter (telemetry, keepalives), so keep enough history that
#: the export request itself cannot be evicted mid-download.
MAX_RECORDED_REQUESTS = 500
MAX_EXPORT_RESPONSES = 50
#: A form post bigger than this is a file upload, not an export request.
MAX_RECIPE_BODY_BYTES = 256 * 1024
#: Anything smaller than this is an error stub, not report data.
MIN_REPLAY_BYTES = 256
#: The portal backend still has to run the report's query during replay, so
#: the budget matches a slow interactive export rather than a fast API call.
REPLAY_TIMEOUT_MS = 300_000

#: Never persisted. Cookies and authorization come live from the browser
#: profile at replay time; connection management belongs to the HTTP client.
DROPPED_HEADERS = frozenset({
    "cookie", "cookie2", "authorization", "proxy-authorization",
    "content-length", "host", "connection", "keep-alive",
    "accept-encoding", "transfer-encoding", "upgrade",
})

#: Response content types that mark a file export as opposed to page chatter.
EXPORT_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml",
    "application/vnd.ms-excel", "application/x-msexcel", "application/excel",
    "application/octet-stream", "application/zip",
    "text/csv", "application/csv",
)

#: Samsung SSO markers, mirrored from the GSCM adapter: a text response that
#: reads like the sign-in page is a session failure, not report data.
SIGN_IN_TEXT_MARKERS = (
    "single sign on", "please enter your password", "verification code",
    "<html", "<!doctype",
)


def enabled(job: dict) -> bool:
    """Whether capture and replay apply to this job at all.

    The environment variable is the operator's kill switch and wins over the
    per-flow setting; a flow can also opt out on its own with
    ``downloads.network_replay: false``.
    """
    if os.environ.get("DG_FLOW_REPLAY", "").strip().casefold() in {
        "0", "false", "off", "no",
    }:
        return False
    setting = (job.get("downloads") or {}).get("network_replay")
    return True if setting is None else bool(setting)


def _sniff_kind(head: bytes) -> str:
    """Classify file bytes into the families replay validation compares.

    ``excel`` covers every container the store pipeline accepts (OOXML and
    XLSB are ZIP packages, legacy XLS is OLE); ``text`` covers CSV. The full
    format nuance stays in the worker's ``_detect_download_format`` - this
    only has to keep a login page from being saved as data.
    """
    if not head:
        return "empty"
    if head.startswith(b"PK"):
        return "excel"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel"
    if head.startswith(b"%PDF"):
        return "pdf"
    decoded = head.decode("latin-1", errors="replace")
    stripped = decoded.lstrip("\ufeff \t\r\n").casefold()
    if stripped.startswith(("<html", "<!doctype", "<?xml", "<head", "<body")):
        return "html"
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "binary"
    return "text"


def _looks_like_sign_in(head: bytes) -> bool:
    decoded = head.decode("latin-1", errors="replace").casefold()
    return any(marker in decoded for marker in SIGN_IN_TEXT_MARKERS)


def _looks_like_export_response(headers: dict[str, str]) -> bool:
    lowered = {str(key).casefold(): str(value) for key, value in headers.items()}
    if "attachment" in lowered.get("content-disposition", "").casefold():
        return True
    content_type = lowered.get("content-type", "").casefold()
    return any(content_type.startswith(prefix) for prefix in EXPORT_CONTENT_TYPES)


class ExportRequestRecorder:
    """Observe one export interaction and identify the request behind it.

    Attached at the browser-context level so wizard popups and dashboard
    pages are covered without knowing which page the portal will use. Every
    handler swallows its own errors: recording must never break the export
    it is recording.
    """

    def __init__(self, context):
        self._context = context
        self._requests: deque[dict] = deque(maxlen=MAX_RECORDED_REQUESTS)
        self._export_responses: deque[dict] = deque(maxlen=MAX_EXPORT_RESPONSES)
        self._download_urls: list[str] = []
        self._pages: list = []
        try:
            context.on("request", self._record_request)
            context.on("response", self._record_response)
            context.on("page", self._record_page)
            for page in list(getattr(context, "pages", []) or []):
                self._record_page(page)
        except Exception:
            pass

    def detach(self) -> None:
        for target, event, handler in (
            (self._context, "request", self._record_request),
            (self._context, "response", self._record_response),
            (self._context, "page", self._record_page),
            *[(page, "download", self._record_download) for page in self._pages],
        ):
            try:
                target.remove_listener(event, handler)
            except Exception:
                continue

    def _record_page(self, page) -> None:
        try:
            page.on("download", self._record_download)
            self._pages.append(page)
        except Exception:
            pass

    def _record_download(self, download) -> None:
        try:
            url = str(download.url or "")
        except Exception:
            return
        if url:
            self._download_urls.append(url)

    def _record_request(self, request) -> None:
        try:
            post = request.post_data_buffer
        except Exception:
            post = None
        if post and len(post) > MAX_RECIPE_BODY_BYTES:
            return
        try:
            record = {
                "url": str(request.url),
                "method": str(request.method or "GET").upper(),
                "headers": dict(request.headers or {}),
                "post_b64": base64.b64encode(post).decode("ascii") if post else None,
            }
        except Exception:
            return
        self._requests.append(record)

    def _record_response(self, response) -> None:
        try:
            if _looks_like_export_response(dict(response.headers or {})):
                self._export_responses.append({"url": str(response.url)})
        except Exception:
            pass

    def best_request(self) -> dict | None:
        """The recorded request that produced the export, or ``None``.

        The browser's own download URL is authoritative when one was emitted.
        The response fallback exists because Chromium does not surface a
        network response for every download it accepts.
        """
        by_url: dict[str, dict] = {}
        for record in self._requests:
            by_url[record["url"]] = record  # the latest request per URL wins
        for url in reversed(self._download_urls):
            if url in by_url:
                return by_url[url]
        for response in reversed(self._export_responses):
            if response["url"] in by_url:
                return by_url[response["url"]]
        return None


# ── Recipe storage ──


def _recipes_path(profile_dir: Path) -> Path:
    return Path(profile_dir) / RECIPES_FILENAME


def _load_all(profile_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads(_recipes_path(profile_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_all(profile_dir: Path, data: dict[str, dict]) -> None:
    path = _recipes_path(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".json.tmp")
    staged.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1),
        encoding="utf-8",
    )
    staged.replace(path)


def recipe_key(job: dict, task_key: str) -> str:
    """One recipe per (site, report, export view, period) - never broader.

    Periods are part of ``task_key``, so a recipe recorded for one week can
    never be replayed to satisfy a request for another: it simply won't be
    found, and the browser flow runs as before.
    """
    site = job.get("site") or {}
    report = job.get("report") or {}
    return json.dumps({
        "site": site.get("id") or site.get("name"),
        "report": report.get("id") or report.get("name"),
        "task": task_key,
    }, sort_keys=True)


def _recipe_expired(recipe: dict) -> bool:
    try:
        captured = datetime.fromisoformat(str(recipe.get("captured_at")))
    except (TypeError, ValueError):
        return True
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - captured
    return age.days >= RECIPE_MAX_AGE_DAYS or age.total_seconds() < 0


def load_recipe(profile_dir: Path, job: dict, task_key: str) -> dict | None:
    recipe = _load_all(profile_dir).get(recipe_key(job, task_key))
    if not isinstance(recipe, dict):
        return None
    if recipe.get("version") != RECIPE_VERSION or not recipe.get("url"):
        return None
    if _recipe_expired(recipe):
        forget_recipe(profile_dir, job, task_key)
        return None
    return recipe


def forget_recipe(profile_dir: Path, job: dict, task_key: str) -> None:
    """Drop one recipe so a failing replay is attempted exactly once.

    The next successful browser export records a fresh recipe; keeping a
    broken one would add a doomed HTTP round trip to every future run.
    """
    data = _load_all(profile_dir)
    if data.pop(recipe_key(job, task_key), None) is not None:
        try:
            _save_all(profile_dir, data)
        except OSError:
            pass


def store_capture(
    profile_dir: Path, job: dict, task_key: str, recorder: ExportRequestRecorder,
    staged_file: Path,
) -> bool:
    """Persist the export request behind a just-completed browser download.

    Returns ``False`` without storing when no request could be identified
    with confidence, or when the staged file itself is not a data file - a
    recipe must never be built from a baseline the validator would reject.
    """
    request = recorder.best_request()
    if request is None:
        return False
    url = str(request.get("url") or "")
    if not url.casefold().startswith(("https://", "http://")):
        return False
    staged = Path(staged_file)
    try:
        with staged.open("rb") as handle:
            head = handle.read(4096)
        size = staged.stat().st_size
    except OSError:
        return False
    kind = _sniff_kind(head)
    if kind not in {"excel", "text"}:
        return False
    headers = {
        key: value for key, value in (request.get("headers") or {}).items()
        if key.casefold() not in DROPPED_HEADERS and not key.startswith(":")
    }
    recipe = {
        "version": RECIPE_VERSION,
        "url": url,
        "method": str(request.get("method") or "GET").upper(),
        "headers": headers,
        "post_b64": request.get("post_b64"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "expected_kind": kind,
        "baseline_bytes": size,
    }
    data = _load_all(profile_dir)
    data[recipe_key(job, task_key)] = recipe
    _save_all(profile_dir, data)
    return True


# ── Replay ──


def _replay_suffix(head: bytes, expected_kind: str) -> str:
    if expected_kind == "excel":
        return ".xls" if head.startswith(b"\xd0\xcf\x11\xe0") else ".xlsx"
    return ".csv"


def _staging_target(staging_dir: Path, suffix: str) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for index in range(1000):
        candidate = staging_dir / f"replay_{stamp}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"No free replay staging filename under {staging_dir}.")


def try_replay(
    context, recipe: dict, staging_dir: Path,
    *, timeout_ms: int = REPLAY_TIMEOUT_MS,
) -> Path | None:
    """Issue the recorded request and stage its response as a download.

    Returns the staged file on success and ``None`` on any doubt - a network
    error, a non-2xx status, a body from the wrong file family, or anything
    that reads like a sign-in page. Never raises for a replay-specific
    failure: the caller's browser flow is always the fallback.
    """
    post = None
    if recipe.get("post_b64"):
        try:
            post = base64.b64decode(str(recipe["post_b64"]))
        except (ValueError, TypeError):
            return None
    try:
        response = context.request.fetch(
            str(recipe["url"]),
            method=str(recipe.get("method") or "GET"),
            headers=dict(recipe.get("headers") or {}) or None,
            data=post,
            timeout=timeout_ms,
            fail_on_status_code=False,
        )
    except Exception:
        return None
    try:
        if not response.ok:
            return None
        body = response.body()
    except Exception:
        return None
    finally:
        try:
            response.dispose()
        except Exception:
            pass
    if len(body) < MIN_REPLAY_BYTES:
        return None
    head = body[:4096]
    expected_kind = str(recipe.get("expected_kind") or "excel")
    if _sniff_kind(head) != expected_kind:
        return None
    if expected_kind == "text" and _looks_like_sign_in(head):
        return None
    target = _staging_target(Path(staging_dir), _replay_suffix(head, expected_kind))
    try:
        target.write_bytes(body)
    except OSError:
        return None
    return target
