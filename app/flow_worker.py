"""Authenticated browser worker for Metronome Flows.

Run this under the Windows user that is authorized for the configured website.
The worker polls Metronome for jobs and never deletes or overwrites files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

# Keep direct-file execution compatible with the isolated Windows embedded
# runtime as well as the preferred ``python -m app.flow_worker`` launcher.
_CODE_DIR = Path(__file__).resolve().parent.parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

try:
    from app.flow_credentials import load_asap_credentials
except ModuleNotFoundError:  # setup.ps1 also invokes this file directly
    from flow_credentials import load_asap_credentials


ASAP_FRAME_SELECTOR = "iframe#content-frame"
ASAP_PORTAL_ADAPTER = "asap_portal"
AUTH_MARKER = ".asap_authenticated"


@contextmanager
def _exclusive_worker_lock(profile_dir: Path):
    """Prevent the service and login task from running duplicate workers."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / ".worker.lock"
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except OSError:
        pass
    try:
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class _Timings:
    def __init__(self):
        self.started = time.perf_counter()
        self.items: list[dict[str, Any]] = []

    def measure(self, phase: str, *, report_id: int | None = None, item_count: int | None = None):
        timings = self
        class Measurement:
            def __enter__(self):
                self.started = time.perf_counter()
                return self

            def __exit__(self, exc_type, exc, traceback):
                item = {
                    "phase": phase,
                    "duration_ms": round((time.perf_counter() - self.started) * 1000),
                    "status": "failed" if exc_type else "succeeded",
                }
                if report_id is not None:
                    item["report_id"] = report_id
                if item_count is not None:
                    item["item_count"] = item_count
                timings.items.append(item)
        return Measurement()

    def finish(self, *, item_count: int | None = None, status: str = "succeeded") -> list[dict[str, Any]]:
        total = {
            "phase": "total", "duration_ms": round((time.perf_counter() - self.started) * 1000),
            "status": status,
        }
        if item_count is not None:
            total["item_count"] = item_count
        return [*self.items, total]


def _api(client: httpx.Client, method: str, path: str, body: dict | None = None) -> dict:
    # Progress calls are part of the execution record, so a short-lived SQLite
    # write collision or local service restart must not kill the browser run.
    # Terminal updates are idempotent on the server and are therefore safe to
    # retry when the local API returns a transient 5xx response.
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = client.request(method, path, json=body, timeout=60)
            response.raise_for_status()
            return response.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            retryable = isinstance(exc, httpx.TransportError) or (
                exc.response is not None and exc.response.status_code >= 500
            )
            if not retryable or attempt == 5:
                raise
            time.sleep(attempt)
    raise RuntimeError("Local API request failed after retries.") from last_error


def _safe_output_path(folder: Path, filename: str) -> Path:
    """Return an unused output path without deleting or overwriting anything."""
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 10000):
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not find an unused filename after 9,999 attempts.")


def _render_filename(template: str, job: dict, period: str | list[str] | None, index: int) -> str:
    raw_week = period or job["downloads"].get("periods", [None])[0] or ""
    if isinstance(raw_week, list):
        start = raw_week[0] if raw_week else ""
        end = raw_week[-1] if raw_week else ""
        week = start if start == end else f"{start}_{end}"
        year = start.split("-W", 1)[0] if start else ""
        start_number = start.split("-W", 1)[1] if "-W" in start else ""
        end_number = end.split("-W", 1)[1] if "-W" in end else ""
        week_number = start_number if start_number == end_number else f"{start_number}-{end_number}"
    else:
        week = raw_week
        start = end = raw_week
        year, week_number = (week.split("-W", 1) + [""])[:2] if week else ("", "")
    def short_period(value: str) -> str:
        return f"W{value.split('-W', 1)[1]}" if "-W" in value else value
    values = {
        "flow": job["flow"]["name"],
        "report": job["report"]["name"],
        "week": week,
        "start_period": short_period(start),
        "end_period": short_period(end),
        "year": year,
        "week_number": week_number,
        "index": str(index),
        "date": date.today().isoformat(),
    }
    name = template
    for key, value in values.items():
        name = name.replace("{" + key + "}", str(value))
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f\s]+", "_", name).strip(" ._")
    expected = ".csv"
    if not name.casefold().endswith(expected):
        name += expected
    return name


def _click_named(page: Page | Frame, text: str):
    candidates = [
        page.get_by_role("button", name=text, exact=True),
        page.get_by_role("link", name=text, exact=True),
        page.get_by_text(text, exact=True),
    ]
    for locator in candidates:
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click()
                return
        except Exception:
            continue
    raise RuntimeError(f"Could not find visible control: {text}")


def _select_native_options_by_text(
    page: Page | Frame, values: list[str], expected_options: list[str] | None = None,
) -> list[str] | None:
    """Set and verify a native control's exact selection without mouse input.

    ASAP uses Select2 for Data Configuration. Its visible widget has no
    accessible name, while the owning ``select`` is hidden. Dimension and week
    prompts may render the same native control visibly. Identify the owner from
    this run's requested values, not from an all-or-nothing copy of the scanned
    catalog, because the catalog can contain a stale or decorated member.
    """
    requested = list(dict.fromkeys(str(item) for item in values))
    expected = set(str(item) for item in (expected_options or requested))
    selects = page.locator("select")
    candidates = []
    for index in range(selects.count()):
        select = selects.nth(index)
        labels = [re.sub(r"\s+", " ", text).strip() for text in select.locator("option").all_text_contents()]
        if not set(requested).issubset(set(labels)):
            continue
        overlap = len(set(labels) & expected)
        candidates.append((overlap, -len(labels), index, select))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda item: item[:3])
    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
        raise RuntimeError(
            f"ASAP exposed more than one native control for requested values: {requested}."
        )
    select = candidates[0][3]
    labels = [re.sub(r"\s+", " ", text).strip() for text in select.locator("option").all_text_contents()]
    indices = [labels.index(value) for value in requested]

    if select.is_visible():
        # ASAP's legacy report engine does not consume synthetic DOM change
        # events when it builds the report request. Drive the actual native
        # control with trusted keyboard input instead. For a multi-select,
        # Home establishes one known selection, Control+Space clears/toggles
        # the focused member, and Control+ArrowDown moves focus without the
        # mouse or a Control-click (which opens the portal's magnifier).
        select.focus()
        if select.get_attribute("multiple") is not None:
            select.press("Home")
            wanted_indices = set(indices)
            options = select.locator("option")
            # Reconcile every member from the control's live starting state.
            # This explicitly clears every stale selection before RUN and does
            # not assume which members ASAP preselected for this report.
            for index in range(options.count()):
                selected = bool(options.nth(index).evaluate("option => option.selected"))
                if selected != (index in wanted_indices):
                    select.press("Control+Space")
                if index < options.count() - 1:
                    select.press("Control+ArrowDown")
        else:
            select.press("Home")
            for _ in range(indices[0]):
                select.press("ArrowDown")
        select.press("Tab")
    else:
        # Select2 owns a hidden native select for Data Configuration. Notify
        # that owner through its jQuery bridge when present; retain native
        # input/change events for templates that do not load jQuery.
        select.evaluate(
            r"""(node, requested) => {
                const wanted = new Set(requested);
                for (const option of Array.from(node.options)) {
                    const label = String(option.textContent || option.label || '').replace(/\s+/g, ' ').trim();
                    option.selected = wanted.has(label);
                }
                const jq = window.jQuery;
                if (jq && jq(node).data('select2')) {
                    jq(node).trigger('change');
                } else {
                    node.dispatchEvent(new Event('input', {bubbles: true}));
                    node.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""",
            requested,
        )
    waiter = page.page if hasattr(page, "page") else page
    waiter.wait_for_timeout(1_000)
    observed = [
        re.sub(r"\s+", " ", text).strip()
        for text in select.locator("option:checked").all_text_contents()
    ]
    if set(observed) != set(requested):
        raise RuntimeError(
            f"ASAP native selection mismatch. Requested: {requested}. "
            f"Selected after native interaction: {observed}."
        )
    return observed


def _read_native_options_by_text(
    page: Page | Frame, values: list[str], expected_options: list[str] | None = None,
) -> list[str] | None:
    """Read the matching native control after all prompt changes settle."""
    requested = list(dict.fromkeys(str(item) for item in values))
    expected = set(str(item) for item in (expected_options or requested))
    selects = page.locator("select")
    candidates = []
    for index in range(selects.count()):
        select = selects.nth(index)
        labels = [re.sub(r"\s+", " ", text).strip() for text in select.locator("option").all_text_contents()]
        if not set(requested).issubset(set(labels)):
            continue
        candidates.append((len(set(labels) & expected), -len(labels), index, select))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda item: item[:3])
    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
        raise RuntimeError(f"ASAP exposed ambiguous native controls for: {requested}.")
    return [
        re.sub(r"\s+", " ", text).strip()
        for text in candidates[0][3].locator("option:checked").all_text_contents()
    ]


def _read_select2_value(page: Page | Frame, requested: list[str]) -> list[str] | None:
    """Read an already-selected lazy Select2 value without opening the menu."""
    if len(requested) != 1:
        return None
    wanted = requested[0]
    rendered = page.locator(".select2-selection__rendered:visible")
    matches = []
    for index in range(rendered.count()):
        item = rendered.nth(index)
        title = item.get_attribute("title") or ""
        text = item.text_content() or ""
        actual = re.sub(r"\s+", " ", title or text).strip()
        if actual == wanted:
            matches.append(actual)
    if len(matches) > 1:
        raise RuntimeError(f"ASAP exposed ambiguous Select2 values for: {requested}.")
    return matches or None


def _set_filter(page: Page | Frame, definition: dict, value: Any):
    if value in (None, "", []):
        return
    label = definition["control_label"]
    automation = definition.get("automation") or {}
    locator = page.locator(automation["locator"]) if automation.get("locator") else page.get_by_label(label, exact=True)
    control_type = definition["control_type"]
    values = value if isinstance(value, list) else [value]
    values = [str(item) for item in values]
    try:
        if control_type == "multi_select":
            locator.select_option(values)
        elif control_type == "select":
            locator.select_option(values[0])
        else:
            locator.fill(values[0])
        return
    except Exception:
        pass

    if control_type in {"select", "multi_select"}:
        try:
            if _select_native_options_by_text(page, values, definition.get("options")):
                return
        except Exception:
            pass

    # Enterprise report portals commonly render comboboxes and tree selectors
    # instead of native <select> elements. Keep this semantic and text based.
    trigger = page.get_by_role("combobox", name=label, exact=True)
    if not trigger.count():
        trigger = page.get_by_text(label, exact=True).first
    if not trigger.count():
        raise RuntimeError(f"Could not find report filter: {label}")
    trigger.click()
    for index, selected in enumerate(values):
        option = page.get_by_role("option", name=selected, exact=True)
        if not option.count():
            option = page.get_by_text(selected, exact=True)
        if not option.count():
            raise RuntimeError(f"Could not find {label} option: {selected}")
        option.first.click(modifiers=["Control"] if control_type == "multi_select" else [])
        if control_type == "multi_select" and index < len(values) - 1:
            trigger.click()


def _apply_configuration(page: Page, job: dict, period: str | list[str] | None):
    selections = dict(job.get("selections") or {})
    for definition in job["report"].get("filters", []):
        key = definition["filter_key"]
        value = period if definition["control_type"] == "week" and period else selections.get(key)
        _set_filter(page, definition, value)


def _week_to_asap(value: str) -> str:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
    if not match:
        raise RuntimeError(f"ASAP week must use YYYY-Www: {value}")
    return "".join(match.groups())


def _asap_frame(page: Page) -> Frame:
    handle = page.locator(ASAP_FRAME_SELECTOR)
    handle.wait_for(state="attached", timeout=120_000)
    frame = handle.element_handle().content_frame()
    if frame is None:
        raise RuntimeError("ASAP report frame did not become available.")
    return frame


def _asap_wait_for_results(page: Page, timeout_ms: int = 180_000) -> Frame:
    """Return the live report frame once ASAP has rendered result rows.

    Running a report replaces ``iframe#content-frame`` in the current ASAP UI.
    A Frame captured before clicking RUN can therefore remain detached while the
    replacement frame already shows the completed report. Re-resolve the iframe
    during the wait and return the replacement so export uses the same live UI.
    """
    deadline = time.monotonic() + (timeout_ms / 1_000)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            # MicroStrategy may briefly retain the old iframe element and add
            # the replacement as another frame. Inspect every current frame,
            # newest first, instead of trusting the first matching element.
            for frame in reversed(page.frames):
                rows = frame.get_by_text("Data rows:", exact=False).first
                if rows.count() and rows.is_visible():
                    return frame
        except Exception as exc:
            # Frame replacement can race any locator operation. The next poll
            # resolves the new element instead of retaining the detached frame.
            last_error = exc
        page.wait_for_timeout(500)
    detail = f" Last frame error: {last_error}" if last_error else ""
    raise RuntimeError(f"ASAP report rows did not render within {timeout_ms // 1000} seconds.{detail}")


def _asap_login_visible(page: Page) -> bool:
    try:
        password = page.locator('input[type="password"]:visible')
        return password.count() > 0 and password.first.is_visible()
    except Exception:
        return False


def _asap_authenticate_if_needed(page: Page, profile_dir: Path) -> bool:
    """Recover an expired ASAP session using the local DPAPI credential."""
    if not _asap_login_visible(page):
        return False
    # Browser profiles are isolated by mode, but both use the one account-level
    # DPAPI credential selected by METRONOME_FLOW_PROFILE during setup.
    credentials = load_asap_credentials()
    if not credentials:
        raise RuntimeError(
            "ASAP sign-in is required. Configure the encrypted BI desktop credential in Flows > Catalog."
        )
    visible_inputs = page.locator('input:visible')
    username = page.locator('input[type="text"]:visible, input:not([type]):visible').first
    password = page.locator('input[type="password"]:visible').first
    if not username.count() and visible_inputs.count() >= 2:
        username = visible_inputs.nth(0)
    username.fill(credentials["username"])
    password.fill(credentials["password"])
    submit = page.get_by_role("button", name=re.compile(r"^login$", re.I)).first
    if not submit.count():
        submit = page.locator('button[type="submit"]:visible, input[type="submit"]:visible').first
    if not submit.count():
        raise RuntimeError("ASAP sign-in form was found, but its Login action was not recognized.")
    submit.click()
    roots = _wait_for_navigation_roots(page, 120_000)
    if not roots:
        error = page.locator("text=/incorrect user id|incorrect password|try again/i").first
        detail = _clean_text(error.text_content()) if error.count() else "ASAP did not open after automatic sign-in."
        raise RuntimeError(f"ASAP automatic sign-in failed: {detail}")
    return True


def _asap_goto(page: Page, url: str, profile_dir: Path) -> bool:
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    # ASAP may render /expiredSession before redirecting to its SSO host. Wait
    # for either terminal state so a delayed login form is never missed.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _asap_login_visible(page):
            return _asap_authenticate_if_needed(page, profile_dir)
        if _navigation_roots(_visible_anchor_records(page)):
            return False
        page.wait_for_timeout(500)
    if _asap_login_visible(page):
        return _asap_authenticate_if_needed(page, profile_dir)
    return False


def _asap_open_report(page: Page, job: dict, profile_dir: Path) -> Frame:
    report = job["report"]
    automation = report.get("automation") or {}
    path = automation.get("category_path") or []
    if len(path) < 2:
        raise RuntimeError("ASAP reports need a category path with a menu and report name.")
    _asap_goto(page, job["site"].get("auth_url") or report["url"], profile_dir)
    def navigate():
        page.get_by_text(path[0], exact=True).first.click()
        if len(path) > 2:
            group = page.get_by_text(path[-2], exact=True)
            if group.count() and group.first.is_visible():
                group.first.hover()
        page.get_by_text(path[-1], exact=True).first.click()

    navigate()

    expected = automation.get("report_tab") or report.get("ready_text")
    last_error = None
    for attempt in range(2):
        try:
            frame = _asap_frame(page)
            if expected:
                frame.get_by_text(expected, exact=True).first.wait_for(state="visible", timeout=120_000)
            if frame.get_by_text("500", exact=True).count():
                raise RuntimeError("ASAP returned an internal server error while loading the report.")
            return frame
        except (PlaywrightTimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt:
                break
            page.go_back(wait_until="domcontentloaded", timeout=120_000)
            navigate()
    raise RuntimeError(f"ASAP report did not load after one retry: {last_error}")


def _asap_member_selected(option) -> bool | None:
    """Read selection state from MicroStrategy's ARIA, class, or row styling."""
    try:
        return option.evaluate(
            r"""node => {
                for (let current = node, depth = 0; current && depth < 5; current = current.parentElement, depth++) {
                    const aria = current.getAttribute('aria-selected') ?? current.getAttribute('aria-checked');
                    if (aria === 'true') return true;
                    if (aria === 'false') return false;
                    const classes = String(current.className || '');
                    if (/(^|[-_ ])(?:selected|checked)(?:$|[-_ ])/i.test(classes) || /itemSelected/i.test(classes)) return true;
                    const style = getComputedStyle(current);
                    const match = style.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                    if (match) {
                        const [r, g, b] = match.slice(1).map(Number);
                        if (b > r + 35 && b > g + 15 && b > 120) return true;
                    }
                }
                return null;
            }"""
        )
    except Exception:
        return None


def _asap_apply_configuration(
    frame: Frame, job: dict, period: str | list[str] | None,
) -> list[dict]:
    selections = dict(job.get("selections") or {})
    audit = []
    for definition in job["report"].get("filters", []):
        key = definition["filter_key"]
        value = period if definition["control_type"] == "week" and period else selections.get(key)
        if value in (None, "", []):
            continue
        values = value if isinstance(value, list) else [value]
        values = [_week_to_asap(str(item)) if definition["control_type"] == "week" else str(item) for item in values]
        actual = _select_native_options_by_text(
            frame, values, definition.get("options") or values,
        )
        if actual is None and definition["control_label"].casefold() == "data configuration":
            actual = _read_select2_value(frame, values)
        if actual is None:
            raise RuntimeError(
                f"ASAP {definition['control_label']} does not expose a native selection control "
                f"containing the requested values: {values}. The report was not run."
            )
        audit.append({
            "filter": definition["control_label"],
            "requested": values,
            "actual": actual,
            "verified": set(actual) == set(values),
            "options": definition.get("options") or values,
        })
    # A later prompt can cause ASAP to rebuild an earlier control. Do not trust
    # the per-control result alone. Wait for the report UI to settle, then audit
    # every configured control again before RUN is allowed.
    frame.page.wait_for_timeout(1_500)
    for item in audit:
        actual = _read_native_options_by_text(frame, item["requested"], item["options"])
        if actual is None and item["filter"].casefold() == "data configuration":
            actual = _read_select2_value(frame, item["requested"])
        item["actual"] = actual or []
        item["verified"] = actual is not None and set(actual) == set(item["requested"])
        item.pop("options", None)
        if not item["verified"]:
            raise RuntimeError(
                f"ASAP {item['filter']} changed after configuration settled. "
                f"Requested: {item['requested']}. Actual: {item['actual']}. The report was not run."
            )
    return audit


def _asap_verify_rendered_results(frame: Frame, filter_audit: list[dict]):
    """Verify the report canvas reflects Dimension and week before export."""
    canvas_left = 160
    for item in filter_audit:
        label = item["filter"].casefold()
        if label not in {"dimension", "sell-out week"}:
            continue
        missing = []
        for value in item["requested"]:
            locator = frame.get_by_text(value, exact=True)
            found_on_canvas = False
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    box = candidate.bounding_box()
                    if candidate.is_visible() and box and box["x"] >= canvas_left:
                        found_on_canvas = True
                        break
                except Exception:
                    continue
            if not found_on_canvas:
                missing.append(value)
        if missing:
            raise RuntimeError(
                f"ASAP rendered report does not match {item['filter']}. "
                f"Requested: {item['requested']}. Missing from report canvas: {missing}. "
                "The file was not exported and SQL was not changed."
            )


def _asap_download(page: Page, frame: Frame, job: dict):
    export_control = None
    # The current MicroStrategy report has two compact controls beside RUN.
    # The first is Export Options and the second is subtotal. Their icon-only
    # markup has no stable accessible label, so resolve the first visible
    # button-like control after RUN within the same toolbar.
    for root in reversed(page.frames):
        try:
            run = root.get_by_text("RUN", exact=True).first
            if not run.count() or not run.is_visible():
                continue
            export_control = run.locator("xpath=following::*[self::button or self::a or self::input or @role='button'][1]")
            if not export_control.count() or not export_control.first.is_visible():
                export_control = run.locator("xpath=following::*[contains(@class,'btn') or contains(@class,'button')][1]")
            if export_control.count() and export_control.first.is_visible():
                export_control = export_control.first
                break
            # Older MicroStrategy templates render both toolbar icons as
            # unlabelled images. Choose the first visible image after RUN and
            # before the report canvas, which is Export Options.
            images = run.locator("xpath=following::img")
            export_control = next(
                (images.nth(index) for index in range(min(images.count(), 6)) if images.nth(index).is_visible()),
                None,
            )
            if export_control is not None:
                break
            export_control = None
        except Exception:
            continue
        if export_control is not None:
            break
    if export_control is None:
        raise RuntimeError("Could not find the ASAP Export Options control beside RUN.")
    pages_before = set(page.context.pages)
    export_control.click()
    # ASAP sometimes opens the wizard as a page and sometimes as a modal/frame
    # in the existing page. Search both shapes instead of requiring a popup.
    format_option = None
    export_action = None
    wizard_pages = set()
    deadline = time.monotonic() + 60
    file_format = "csv"
    format_names = (
        "CSV file format", re.compile(r"^(?:CSV|Comma separated values)(?: file format)?$", re.I)
    )
    while time.monotonic() < deadline and (format_option is None or export_action is None):
        current_pages = page.context.pages
        popup = next((candidate for candidate in current_pages if candidate not in pages_before), None)
        roots = [root for candidate in reversed(current_pages) for root in [candidate, *reversed(candidate.frames)]]
        for root in roots:
            for locator in (
                root.get_by_label(format_names[0], exact=True),
                root.get_by_text(format_names[1]),
            ):
                try:
                    if locator.count() and locator.first.is_visible():
                        format_option = locator.first
                        wizard_pages.add(root if isinstance(root, Page) else root.page)
                        break
                except Exception:
                    continue
            for locator in (
                root.get_by_role("button", name="Export", exact=True),
                root.get_by_text("Export", exact=True),
            ):
                try:
                    if locator.count() and locator.first.is_visible():
                        export_action = locator.first
                        wizard_pages.add(root if isinstance(root, Page) else root.page)
                        break
                except Exception:
                    continue
        if format_option is None or export_action is None:
            page.wait_for_timeout(250)
    if format_option is None or export_action is None:
        raise RuntimeError(f"ASAP Export Wizard opened, but its {file_format.upper()} option or Export action was not recognized.")
    try:
        format_option.check()
    except Exception:
        format_option.click()
    # The export wizard may render in one popup while Edge attributes the
    # resulting download to another ASAP page. Listening only on the guessed
    # popup can therefore leave a visibly completed browser download waiting
    # until timeout. Subscribe to every current portal page before clicking.
    downloads = []
    observed_pages = list(page.context.pages)

    def capture_download(download):
        downloads.append(download)

    for candidate in observed_pages:
        candidate.on("download", capture_download)
    try:
        export_action.click()
        deadline = time.monotonic() + 180
        while not downloads and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        if not downloads:
            raise RuntimeError("ASAP export started, but Edge did not expose the completed download within 3 minutes.")
        export_pages = [
            candidate for candidate in wizard_pages
            if candidate not in pages_before and candidate is not page
        ]
        return downloads[0], export_pages
    finally:
        for candidate in observed_pages:
            candidate.remove_listener("download", capture_download)


def _slug_key(value: str, fallback: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not key or not key[0].isalpha():
        key = f"field_{key}" if key else fallback
    return key[:100]


def _unique_visible_text(locator, limit: int = 2000) -> list[str]:
    values = []
    for item in locator.all_inner_texts():
        value = re.sub(r"\s+", " ", item).strip()
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_asap_filter_label(label: str, control_type: str, options: list[str]) -> str:
    """Repair labels omitted by custom MicroStrategy prompt controls."""
    if (
        control_type == "select"
        and len(options) >= 1
        and len([part for part in label.split(" - ") if part.strip()]) == 3
        and all(len([part for part in value.split(" - ") if part.strip()]) == 3 for value in options)
    ):
        return "Data Configuration"
    return label


def _merge_asap_filter_definition(
    definitions: list[dict], label: str, control_type: str, options: list[str],
) -> None:
    """Add a discovered prompt or merge a second, more complete rendering."""
    label = _clean_text(label).rstrip(":")
    raw_options = list(dict.fromkeys(_clean_text(value) for value in options if _clean_text(value)))
    # Identify unnamed custom controls before discarding text duplicated by the
    # displayed selected value. Otherwise a two-option popup becomes a
    # one-option list and can no longer be recognized as Data Configuration.
    label = _normalize_asap_filter_label(label, control_type, raw_options)
    options = [
        value for value in raw_options
        if value and value != label and not re.fullmatch(r"\(all\)(?:\s*\(\d+\s+values?\))?", value, re.I)
        and "type to search" not in value.casefold()
    ]
    if not label or not options:
        return
    key = _slug_key(label, f"filter_{len(definitions) + 1}")
    existing = next((item for item in definitions if item["filter_key"] == key), None)
    if existing is not None:
        existing["options"] = list(dict.fromkeys([*existing["options"], *options]))
        return
    definitions.append({
        "filter_key": key, "label": label, "control_label": label,
        "control_type": control_type, "options": options, "automation": {},
        "required": False, "position": len(definitions),
    })


def _visible_anchor_records(page: Page) -> list[dict]:
    records = []
    selector = "a:visible,button:visible,[role=button]:visible,[role=menuitem]:visible,[onclick]:visible"
    for link in page.locator(selector).all():
        text = _clean_text(link.inner_text())
        box = link.bounding_box()
        if not text or not box:
            continue
        records.append({
            "link": link,
            "text": text,
            "href": link.get_attribute("href") or "",
            "onclick": link.get_attribute("onclick") or "",
            "box": box,
        })
    return records


def _wait_for_navigation_roots(page: Page, timeout_ms: int = 120_000) -> list[dict]:
    """Wait for the client-rendered ASAP shell without assuming a menu label."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        roots = _navigation_roots(_visible_anchor_records(page))
        if roots:
            return roots
        page.wait_for_timeout(500)
    return []


def _navigation_roots(records: list[dict]) -> list[dict]:
    """Find the dense horizontal row of top-level ASAP navigation links."""
    candidates = [
        item for item in records
        if item["box"]["y"] < 180 and item["box"]["height"] < 70 and len(item["text"]) <= 50
    ]
    if not candidates:
        return []
    buckets: dict[int, list[dict]] = {}
    for item in candidates:
        center = item["box"]["y"] + item["box"]["height"] / 2
        buckets.setdefault(round(center / 12), []).append(item)
    row = max(buckets.values(), key=lambda items: len({round(item["box"]["x"] / 20) for item in items}))
    if len(row) < 2:
        return []
    ignored = {"asap", "home", "logout", "log out", "help", "profile"}
    return [
        item for item in sorted(row, key=lambda item: item["box"]["x"])
        if item["text"].casefold() not in ignored
    ]


def _menu_report_paths(root: dict, before: list[dict], after: list[dict]) -> list[list[str]]:
    """Convert links revealed by one mega-menu into category/report paths."""
    before_keys = {
        (item["text"].casefold(), item["href"], item["onclick"], round(item["box"]["x"]), round(item["box"]["y"]))
        for item in before
    }
    revealed = [
        item for item in after
        if (item["text"].casefold(), item["href"], item["onclick"],
            round(item["box"]["x"]), round(item["box"]["y"])) not in before_keys
        and item["text"].casefold() != root["text"].casefold()
    ]
    if not revealed:
        return []

    columns: list[list[dict]] = []
    for item in sorted(revealed, key=lambda value: value["box"]["x"]):
        target = next((column for column in columns if abs(
            sum(entry["box"]["x"] for entry in column) / len(column) - item["box"]["x"]
        ) <= 70), None)
        (target if target is not None else columns.append([]) or columns[-1]).append(item)

    paths = []
    for column in columns:
        column.sort(key=lambda item: (item["box"]["y"], item["box"]["x"]))
        first = column[0]
        first_target = (first["href"] + first["onclick"]).casefold()
        has_heading = len(column) > 1 and "report" not in first_target
        group = first["text"] if has_heading else None
        for item in column[1:] if has_heading else column:
            target = (item["href"] + item["onclick"]).casefold()
            # The new ASAP UI no longer consistently includes "report" in the
            # target. Links revealed beneath a column heading are report leaves.
            if not has_heading and "report" not in target:
                continue
            path = [root["text"], group, item["text"]] if group else [root["text"], item["text"]]
            if path not in paths:
                paths.append(path)
    return paths


def _asap_discover_menu_reports(page: Page, scope: list[str]) -> list[list[str]]:
    # Discovery is deliberately page-driven. The old implementation required a
    # configured label such as "Mobile", which made every navigation rename a
    # deployment incident. Scope remains in the job schema for compatibility,
    # but the scanner now inventories every top-level menu it can see.
    roots = _wait_for_navigation_roots(page)
    if not roots:
        raise RuntimeError(
            f"ASAP top-level navigation did not render within 120 seconds "
            f"(URL: {page.url}, title: {_clean_text(page.title())})."
        )
    root_names = list(dict.fromkeys(root["text"] for root in roots))
    paths: list[list[str]] = []
    for root_name in root_names:
        # Mega-menu contents change the number and order of matching elements.
        # Re-resolve the navigation trigger instead of retaining an nth-based
        # Playwright locator from the initial DOM snapshot.
        current_roots = _navigation_roots(_visible_anchor_records(page))
        root = next((item for item in current_roots if item["text"] == root_name), None)
        if root is None:
            continue
        before = _visible_anchor_records(page)
        root["link"].click(timeout=15_000)
        after = before
        stable_signature: tuple[tuple[str, ...], ...] | None = None
        stable_polls = 0
        reveal_deadline = time.monotonic() + 10
        while time.monotonic() < reveal_deadline:
            page.wait_for_timeout(200)
            after = _visible_anchor_records(page)
            revealed_paths = _menu_report_paths(root, before, after)
            signature = tuple(tuple(path) for path in revealed_paths)
            if signature and signature == stable_signature:
                stable_polls += 1
            else:
                stable_signature = signature
                stable_polls = 0
            # ASAP builds large mega-menus incrementally. The first non-empty
            # snapshot can contain only a few columns, so wait until the menu
            # remains unchanged for roughly one second before cataloguing it.
            if signature and stable_polls >= 5:
                break
        for path in _menu_report_paths(root, before, after):
            if path not in paths:
                paths.append(path)
        # Close the menu before measuring the next root. Clicking the active
        # trigger is reversible and avoids confusing links from two menus.
        try:
            root["link"].click(timeout=5_000)
            page.wait_for_timeout(150)
        except Exception:
            pass
    if not paths:
        raise RuntimeError("ASAP navigation was detected, but no report links were revealed.")
    return paths


def _asap_discover_filters(frame: Frame) -> list[dict]:
    definitions = []

    def add_definition(label: str, control_type: str, options: list[str]):
        _merge_asap_filter_definition(definitions, label, control_type, options)

    def nearest_list_values(label_locator, *, require_search_marker: bool = False) -> list[str]:
        """Read the smallest visible MicroStrategy control containing a label."""
        ancestor = label_locator
        for _ in range(7):
            ancestor = ancestor.locator("xpath=parent::*")
            if not ancestor.count():
                break
            lines = list(dict.fromkeys(
                _clean_text(line) for line in ancestor.first.inner_text().splitlines() if _clean_text(line)
            ))
            has_marker = any(
                "type to search" in line.casefold() or re.search(r"\(\d+\s+values?\)", line, re.I)
                for line in lines
            )
            if len(lines) >= 3 and (has_marker or not require_search_marker):
                return lines[1:500]
        return []

    def wait_for_popup_options(timeout_ms: int = 5_000) -> list[str]:
        """Collect asynchronously rendered Select2 results until they settle."""
        selector = (
            "[role=option]:visible,li:visible,"
            ".select2-results__option:visible,[class*=select2-result]:visible"
        )
        collected = []
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since = None
        while time.monotonic() < deadline:
            current = _unique_visible_text(frame.locator(selector), 500)
            merged = list(dict.fromkeys([*collected, *current]))
            if merged != collected:
                collected = merged
                stable_since = time.monotonic()
            elif collected and stable_since is not None and time.monotonic() - stable_since >= 1.5:
                break
            frame.page.wait_for_timeout(250)
        return collected
    # Native controls are preferred because they expose complete option lists
    # without opening the control or changing report state. Select2 deliberately
    # hides its owning select, so restricting this scan to :visible drops values
    # that were not rendered in the popup snapshot.
    for control in frame.locator("select").all():
        options = list(dict.fromkeys(
            _clean_text(value) for value in control.locator("option").all_text_contents()
            if _clean_text(value)
        ))
        control_id = control.get_attribute("id") or ""
        label = ""
        if control_id:
            label_locator = frame.locator(f'label[for="{control_id}"]')
            if label_locator.count():
                label = re.sub(r"\s+", " ", label_locator.first.inner_text()).strip()
        if not label:
            aria = control.get_attribute("aria-label") or control.get_attribute("name") or ""
            label = re.sub(r"\s+", " ", aria).strip()
        # ASAP's Select2 Data Configuration owner has no accessible name. Its
        # three-part region choices identify the prompt without hardcoding any
        # actual region value into the repository.
        if not label and len(options) >= 2 and all(
            len([part for part in value.split(" - ") if part.strip()]) == 3
            for value in options
        ):
            label = options[0]
        if not label:
            continue
        add_definition(label, "select", options)

    # New ASAP renders some selects as asynchronous Select2 comboboxes. Open
    # them long enough for the remote results to settle, then restore the page
    # with Escape. A fixed 150 ms snapshot missed late-arriving values.
    custom_selects = frame.locator(
        "[role=combobox]:visible,button[aria-haspopup=listbox]:visible,input[aria-haspopup=listbox]:visible"
    )
    for control in custom_selects.all():
        label = _clean_text(control.get_attribute("aria-label") or control.get_attribute("name"))
        if not label:
            lines = nearest_list_values(control)
            label = lines[0] if lines else ""
        try:
            control.click(timeout=3_000)
            options = wait_for_popup_options()
        except Exception:
            options = []
        finally:
            try:
                frame.page.keyboard.press("Escape")
            except Exception:
                pass
        add_definition(label, "select", options)

    # Searchable member selectors expose their label and values as plain divs,
    # without heading or option roles. Their search/count marker is the stable
    # structural signal across reports.
    search_markers = frame.get_by_text(re.compile(r"type to search|\(\d+\s+values?\)", re.I))
    for marker in search_markers.all():
        block = marker
        for _ in range(6):
            block = block.locator("xpath=parent::*")
            if not block.count():
                break
            lines = list(dict.fromkeys(
                _clean_text(line) for line in block.first.inner_text().splitlines() if _clean_text(line)
            ))
            marker_index = next((i for i, line in enumerate(lines) if
                                 "type to search" in line.casefold() or re.search(r"\(\d+\s+values?\)", line, re.I)), -1)
            if marker_index > 0 and len(lines) > marker_index + 1:
                label = lines[marker_index - 1]
                control_type = "week" if "week" in label.casefold() else "multi_select"
                add_definition(label, control_type, lines[marker_index + 1:500])
                break

    # Dimension pickers use a named member list but no ARIA roles. The adapter
    # recognizes the UI control label, while every option remains page-driven.
    for dimension_label in frame.get_by_text(re.compile(r"^dimension:?$", re.I)).all():
        add_definition("Dimension", "multi_select", nearest_list_values(dimension_label))

    # The Installed Base report exposes its week prompt as a searchable
    # MicroStrategy member list. Depending on render timing the count/search
    # marker may not be returned as its own text node, so anchor discovery on
    # the stable semantic label as well.
    for week_label in frame.get_by_text(re.compile(r"^sell-out week:?$", re.I)).all():
        week_values = [
            value for value in nearest_list_values(week_label)
            if re.fullmatch(r"20\d{4}", value)
        ]
        add_definition("Sell-out Week", "week", week_values)

    # MicroStrategy list selectors are represented by a heading followed by a
    # member list. Detect the labels from the report's own prompt headings and
    # capture visible members without selecting them.
    prompt_labels = _unique_visible_text(frame.locator("h1:visible,h2:visible,h3:visible,h4:visible,[role=heading]:visible"), 300)
    ignored = {"run", "data rows", "data columns", "export wizard", "select dimensions"}
    for label in prompt_labels:
        normalized = label.casefold().rstrip(":")
        if not label or any(token in normalized for token in ignored):
            continue
        if any(item["label"].casefold() == normalized for item in definitions):
            continue
        heading = frame.get_by_text(label, exact=True).first
        following = heading.locator("xpath=following::*[@role='option' or self::option or self::li][position() <= 500]")
        options = _unique_visible_text(following, 500) if following.count() else []
        if not options:
            continue
        control_type = "week" if "week" in normalized else "multi_select"
        add_definition(label, control_type, options)
    return definitions


def _asap_activate_export_view(page: Page, frame: Frame) -> tuple[Frame, str | None]:
    """Open the report's export-oriented view using visible ASAP semantics."""
    visible = []
    # The current portal places this control in the outer ASAP shell, while
    # some older reports render it inside a nested MicroStrategy frame. The
    # control can also arrive after the report iframe itself is attached.
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and not visible:
        roots = list(dict.fromkeys([page.main_frame, frame, *page.frames]))
        for root in roots:
            candidates = root.get_by_text(re.compile(r"^Export Wizard(?:\s*\([^)]*\))?$", re.I))
            for candidate in candidates.all():
                try:
                    if candidate.is_visible():
                        label = _clean_text(candidate.inner_text())
                        if label and label.casefold() not in {item[0].casefold() for item in visible}:
                            visible.append((label, candidate))
                except Exception:
                    continue
        if not visible:
            page.wait_for_timeout(250)
    if not visible:
        return frame, None
    label, control = next(
        (item for item in visible if "detail" in item[0].casefold()),
        visible[0],
    )
    control.click(timeout=15_000)
    page.wait_for_timeout(500)
    active_frame = _asap_frame(page)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        markers = active_frame.get_by_text(
            re.compile(r"^(?:RUN|Dimension:?|Data Configuration:?|Sell-out Week:?)$", re.I)
        )
        if any(item.is_visible() for item in markers.all()):
            break
        page.wait_for_timeout(250)
    return active_frame, label


def discover_asap_catalog(page: Page, job: dict, report_progress, profile_dir: Path) -> tuple[list[dict], list[dict], bool]:
    timings = _Timings()
    deadline = time.monotonic() + 60 * int(job["discovery"].get("max_duration_minutes") or 90)
    site = job["site"]
    report_progress("running", {"stage": "navigation", "message": "Opening ASAP for catalog discovery."})
    with timings.measure("navigation"):
        _asap_goto(page, site.get("auth_url") or site.get("base_url"), profile_dir)
    target_paths = [path for path in job["discovery"].get("report_paths", []) if len(path) >= 2]
    with timings.measure("report_discovery"):
        paths = target_paths or _asap_discover_menu_reports(
            page, job["discovery"].get("scope") or ["Mobile"]
        )
    reports = []
    complete = not target_paths
    for index, path in enumerate(paths, start=1):
        if time.monotonic() >= deadline:
            complete = False
            report_progress("running", {
                "stage": "time_budget_reached",
                "message": "The 90-minute scan budget was reached. Keeping partial discoveries without marking unseen entries stale.",
                "report_index": index, "report_count": len(paths),
            })
            break
        report_progress("running", {
            "stage": "filter_inspection", "message": f"Inspecting report {index} of {len(paths)}.",
            "current_report": path[-1], "report_index": index, "report_count": len(paths),
        })
        lightweight_job = {
            "site": site,
            "report": {
                "name": path[-1], "url": site.get("base_url") or site.get("auth_url"),
                "ready_text": None, "automation": {"category_path": path},
            },
        }
        try:
            with timings.measure("report_navigation"):
                frame = _asap_open_report(page, lightweight_job, profile_dir)
                frame, ready_text = _asap_activate_export_view(page, frame)
            with timings.measure("filter_inspection"):
                filters = _asap_discover_filters(frame)
                report_title = frame.locator("title").text_content() or path[-1]
            discovery_key = " > ".join(path)
            reports.append({
                "discovery_key": discovery_key, "name": path[-1],
                "report_url": site.get("base_url") or site.get("auth_url"),
                "ready_text": ready_text, "download_text": "Export CSV",
                "automation": {
                    "category_path": path, "report_tab": ready_text,
                    "report_title": report_title, "export_text": ready_text,
                },
                "filters": filters,
            })
        except Exception as exc:
            complete = False
            timings.items.append({
                "phase": "report_inspection", "duration_ms": 0, "status": "failed",
                "metadata": {"path": path, "error": str(exc)},
            })
            _asap_goto(page, site.get("auth_url") or site.get("base_url"), profile_dir)
    return reports, timings.finish(item_count=len(reports), status="succeeded" if complete else "partial"), complete


def _normalize_csv(path: Path) -> dict:
    """Remove ASAP's title/blank preamble while preserving a standard CSV."""
    decoded = None
    encoding_used = None
    raw = path.read_bytes()
    encodings = ["utf-8-sig"]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:512]:
        encodings.extend(["utf-16", "utf-16-le", "utf-16-be"])
    encodings.extend(["cp1252", "latin-1"])
    for encoding in encodings:
        try:
            decoded = raw.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise RuntimeError(f"Could not decode downloaded CSV: {path.name}")
    lines = decoded.splitlines()
    sample = "\n".join(lines[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = max((",", ";", "\t", "|"), key=lambda item: sample.count(item))
    rows = list(csv.reader(lines, delimiter=delimiter))
    if not rows:
        raise RuntimeError("The downloaded CSV is empty.")
    header_index = 0
    if len(rows) >= 3 and len(rows[0]) == 1 and not any(str(value).strip() for value in rows[1]):
        header_index = 2
    elif len(rows[0]) < 2:
        header_index = next(
            (index for index, row in enumerate(rows[:20]) if len(row) >= 2 and sum(bool(str(value).strip()) for value in row) >= 2),
            0,
        )
    header = [str(value).strip() for value in rows[header_index]]
    if len(header) < 2:
        raise RuntimeError(
            "Downloaded CSV did not contain a usable delimited header after the ASAP preamble."
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows[header_index:])
    return {
        "preamble_rows_removed": header_index,
        "source_encoding": encoding_used,
        "source_delimiter": "tab" if delimiter == "\t" else delimiter,
        "columns": header,
    }


def _script_command(script_path: Path, input_path: Path, output_path: Path) -> list[str]:
    suffix = script_path.suffix.casefold()
    if suffix == ".py":
        return [sys.executable, str(script_path), "--input", str(input_path), "--output", str(output_path)]
    elif suffix == ".ps1":
        return [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script_path), "-InputPath", str(input_path), "-OutputPath", str(output_path),
        ]
    elif suffix == ".exe":
        return [str(script_path), "--input", str(input_path), "--output", str(output_path)]
    else:
        raise RuntimeError("Transformation script must be a .py, .ps1, or .exe file.")


def _run_transformations(artifacts: list[dict], config: dict) -> list[dict]:
    """Run one configured script once per download and return SQL-ready outputs."""
    if not config.get("enabled"):
        return artifacts
    script_path = Path(str(config.get("script_path") or ""))
    if not script_path.is_file():
        raise RuntimeError(f"Transformation script does not exist: {script_path}")
    results_folder = Path(artifacts[0]["file_path"]).parent / "script_results"
    results_folder.mkdir(parents=True, exist_ok=True)
    transformed = []
    for index, artifact in enumerate(artifacts, start=1):
        input_path = Path(artifact["file_path"])
        output_path = _safe_output_path(results_folder, input_path.name)
        environment = os.environ.copy()
        environment.update({
            "METRONOME_FLOW_INPUT": str(input_path),
            "METRONOME_FLOW_OUTPUT": str(output_path),
            "METRONOME_FLOW_RESULTS_DIR": str(results_folder),
        })
        completed = subprocess.run(
            _script_command(script_path, input_path, output_path),
            cwd=str(script_path.parent), env=environment, capture_output=True,
            text=True, timeout=60 * 60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0:
            detail = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(f"Transformation failed for {input_path.name}: {detail[-4000:]}")
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError(
                f"Transformation script completed for {input_path.name} but did not create {output_path}. "
                "The script must write --output (METRONOME_FLOW_OUTPUT)."
            )
        normalization = _normalize_csv(output_path)
        metadata = {**_csv_metadata(output_path), **normalization}
        transformed.append({
            **artifact,
            "file_path": str(output_path), "filename": output_path.name,
            "status": "transformed", "source_file_path": str(input_path),
            "script_path": str(script_path), "script_index": index,
            "script_stdout": stdout[-4000:], "script_stderr": stderr[-4000:],
            **metadata,
        })
    return transformed


def _csv_metadata(path: Path) -> dict:
    file_size = path.stat().st_size
    if file_size <= 0:
        raise RuntimeError("The downloaded CSV is empty.")
    prefix = path.read_bytes()[:512].lstrip().lower()
    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        raise RuntimeError("The download contains an HTML page instead of CSV data.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    row_count = None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            row_count = max(0, sum(1 for _ in csv.reader(handle)) - 1)
    except (UnicodeDecodeError, csv.Error):
        pass
    return {"file_size": file_size, "checksum": digest.hexdigest(), "row_count": row_count}


def execute_job(page: Page, job: dict, report_progress, profile_dir: Path) -> tuple[list[dict], list[dict]]:
    timings = _Timings()
    report_progress("running", {"stage": "opening_report", "message": "Opening the configured report."})
    is_asap = job["site"].get("adapter") == ASAP_PORTAL_ADAPTER
    with timings.measure("navigation", report_id=job["report"].get("id")):
        frame = _asap_open_report(page, job, profile_dir) if is_asap else None
    ready_text = job["report"].get("ready_text")
    open_export = job["report"].get("open_export_text")
    if not is_asap:
        page.goto(job["report"]["url"], wait_until="domcontentloaded", timeout=120_000)
        if ready_text:
            page.get_by_text(ready_text, exact=False).first.wait_for(state="visible", timeout=120_000)
        if open_export:
            _click_named(page, open_export)

    target = Path(job["downloads"]["target_folder"])
    if not target.is_dir():
        raise RuntimeError(f"Target folder does not exist: {target}")

    periods = job["downloads"].get("periods") or [None]
    artifacts = []
    for index, period in enumerate(periods, start=1):
        if index > 1:
            if is_asap:
                with timings.measure("navigation", report_id=job["report"].get("id")):
                    frame = _asap_open_report(page, job, profile_dir)
            else:
                page.goto(job["report"]["url"], wait_until="domcontentloaded", timeout=120_000)
                if ready_text:
                    page.get_by_text(ready_text, exact=False).first.wait_for(state="visible", timeout=120_000)
                if open_export:
                    _click_named(page, open_export)
        report_progress(
            "running",
            {"stage": "configuring", "message": f"Configuring download {index} of {len(periods)}.", "period": period},
            artifacts,
        )
        if is_asap:
            with timings.measure("configuration", report_id=job["report"].get("id")):
                filter_audit = _asap_apply_configuration(frame, job, period)
            report_progress(
                "running",
                {
                    "stage": "configuration_verified",
                    "message": f"Verified every filter for download {index} of {len(periods)}.",
                    "period": period,
                    "filter_audit": filter_audit,
                },
                artifacts,
            )
            report_progress(
                "running",
                {"stage": "report_execution", "message": f"Running report {index} of {len(periods)}.", "period": period},
                artifacts,
            )
            with timings.measure("report_execution", report_id=job["report"].get("id")):
                _click_named(frame, "RUN")
                # The current MicroStrategy UI completes report execution in
                # its iframe without a stable prompt-answer response. Waiting
                # for that internal URL left already-rendered reports stuck for
                # three minutes. Yield briefly for the loading overlay, then use
                # the rendered row summary as the portal's public readiness
                # signal.
                page.wait_for_timeout(1_000)
                frame = _asap_wait_for_results(page)
                _asap_verify_rendered_results(frame, filter_audit)
            report_progress(
                "running",
                {"stage": "file_export", "message": f"Exporting {job['downloads'].get('file_format', 'csv').upper()} {index} of {len(periods)}.", "period": period},
                artifacts,
            )
            with timings.measure("file_export", report_id=job["report"].get("id")):
                download, export_pages = _asap_download(page, frame, job)
        else:
            _apply_configuration(page, job, period)
            with page.expect_download(timeout=180_000) as pending:
                _click_named(page, job["report"]["download_text"])
            download = pending.value
            export_pages = []
        filename = _render_filename(job["downloads"]["filename_template"], job, period, index)
        output = _safe_output_path(target, filename)
        try:
            with timings.measure("file_transfer", report_id=job["report"].get("id")):
                # save_as waits for the browser download to finish. Keep the
                # export popup alive until that point, then close it before the
                # next period is configured.
                download.save_as(output)
                normalization = _normalize_csv(output)
                metadata = {**_csv_metadata(output), **normalization}
        finally:
            for export_page in export_pages:
                try:
                    if not export_page.is_closed():
                        export_page.close(run_before_unload=False)
                except Exception:
                    # The wizard may close itself after emitting the download.
                    # Treat that as already cleaned up.
                    pass
        artifacts.append({
            "period_key": period,
            "file_path": str(output),
            "filename": output.name,
            "status": "saved",
            **metadata,
        })
    return artifacts, timings.finish(item_count=len(artifacts))


def run_worker(server: str, worker_id: str, display_name: str, profile_dir: Path, headed: bool,
               once: bool, idle_exit_seconds: int = 0):
    with httpx.Client(base_url=server.rstrip("/"), headers={"User-Agent": "Metronome-Flow-Worker/1"}) as client:
        registration = {
            "worker_id": worker_id,
            "display_name": display_name,
            "capabilities": {"adapters": ["web_export", ASAP_PORTAL_ADAPTER], "headed": headed, "process_id": os.getpid(), "delete_existing": False, "overwrite_existing": False},
        }
        for attempt in range(60):
            try:
                _api(client, "POST", "/api/flows/worker/register", registration)
                break
            except (httpx.HTTPError, OSError) as exc:
                if attempt == 59:
                    raise RuntimeError(f"Could not register worker after 120 seconds: {exc}") from exc
                time.sleep(2)
        print(f"Worker {worker_id} registered with {server}.", flush=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel="msedge" if os.name == "nt" else None,
                headless=not headed,
                accept_downloads=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            idle_since = time.monotonic()
            while True:
                claimed = _api(client, "POST", f"/api/flows/worker/{worker_id}/claim")
                run = claimed.get("run")
                scan = claimed.get("scan")
                if not run and not scan:
                    if once:
                        break
                    if idle_exit_seconds and time.monotonic() - idle_since >= idle_exit_seconds:
                        break
                    time.sleep(10)
                    continue
                idle_since = time.monotonic()
                if scan:
                    scan_id = scan["id"]
                    scan_started = time.perf_counter()

                    def scan_progress(status: str, detail: dict, reports: list | None = None,
                                      timings: list | None = None, error: str | None = None,
                                      complete: bool = True):
                        _api(client, "POST", f"/api/flows/worker/{worker_id}/scans/{scan_id}/progress", {
                            "status": status, "progress": detail, "reports": reports or [],
                            "timings": timings or [], "error": error, "complete": complete,
                        })

                    try:
                        reports, timings, complete = discover_asap_catalog(page, scan["job"], scan_progress, profile_dir)
                        scan_progress(
                            "succeeded",
                            {"stage": "complete", "message": f"Discovered {len(reports)} report(s)."},
                            reports, timings, complete=complete,
                        )
                    except Exception as exc:
                        scan_progress(
                            "failed", {"stage": "failed", "message": str(exc)},
                            timings=[{"phase": "total", "duration_ms": round((time.perf_counter() - scan_started) * 1000), "status": "failed"}],
                            error=str(exc), complete=False,
                        )
                    if once:
                        break
                    continue
                run_id = run["id"]
                run_started = time.perf_counter()
                artifacts = []
                timings = []
                transformation_started = None
                sql_started = None

                def progress(status: str, detail: dict, artifacts: list | None = None,
                             timings: list | None = None, error: str | None = None,
                             traceback_text: str | None = None):
                    _api(client, "POST", f"/api/flows/worker/{worker_id}/runs/{run_id}/progress", {
                        "status": status, "progress": detail, "artifacts": artifacts or [],
                        "timings": timings or [], "error": error, "traceback": traceback_text,
                    })

                heartbeat_stop = threading.Event()

                def heartbeat_loop():
                    with httpx.Client(
                        base_url=server.rstrip("/"),
                        headers={"User-Agent": "Metronome-Flow-Worker/1"},
                    ) as heartbeat_client:
                        while not heartbeat_stop.wait(30):
                            try:
                                _api(
                                    heartbeat_client, "POST",
                                    f"/api/flows/worker/{worker_id}/runs/{run_id}/heartbeat",
                                )
                            except Exception:
                                pass

                heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
                heartbeat_thread.start()

                try:
                    artifacts, timings = execute_job(page, run["job"], progress, profile_dir)
                    sql_artifacts = artifacts
                    if run["job"].get("transformation", {}).get("enabled"):
                        progress(
                            "running",
                            {"stage": "transformation", "message": f"Transforming {len(artifacts)} downloaded file(s)."},
                            artifacts, timings,
                        )
                        transformation_started = time.perf_counter()
                        sql_artifacts = _run_transformations(artifacts, run["job"]["transformation"])
                        timings.insert(max(0, len(timings) - 1), {
                            "phase": "transformation",
                            "duration_ms": round((time.perf_counter() - transformation_started) * 1000),
                            "status": "succeeded", "item_count": len(sql_artifacts),
                        })
                        timings[-1]["duration_ms"] = round((time.perf_counter() - run_started) * 1000)
                        artifacts = [*artifacts, *sql_artifacts]
                        progress(
                            "running",
                            {
                                "stage": "transformation_complete",
                                "message": f"Created {len(sql_artifacts)} transformed file(s) in script_results.",
                                "results": [
                                    {
                                        "source": item.get("source_file_path"),
                                        "output": item.get("file_path"),
                                        "rows": item.get("row_count"),
                                        "stdout": item.get("script_stdout"),
                                        "stderr": item.get("script_stderr"),
                                    }
                                    for item in sql_artifacts
                                ],
                            },
                            artifacts, timings,
                        )
                    if run["job"].get("sql_handoff", {}).get("enabled"):
                        from app.flow_sql import load_artifacts
                        source_label = "transformed" if run["job"].get("transformation", {}).get("enabled") else "downloaded"
                        progress("running", {"stage": "sql_insertion", "message": f"Loading {source_label} files into SQL."}, artifacts, timings)
                        sql_started = time.perf_counter()
                        sql_result = load_artifacts(sql_artifacts, run["job"]["sql_handoff"])
                        timings.insert(max(0, len(timings) - 1), {
                            "phase": "sql_insertion",
                            "duration_ms": round((time.perf_counter() - sql_started) * 1000),
                            "status": "succeeded",
                        })
                        timings[-1]["duration_ms"] = round((time.perf_counter() - run_started) * 1000)
                        progress(
                            "running",
                            {
                                "stage": "sql_insertion_complete",
                                "message": f"Inserted {sql_result['rows_written']} row(s) from {sql_result['files_loaded']} file(s).",
                                **sql_result,
                            },
                            artifacts, timings,
                        )
                    progress(
                        "succeeded", {
                            "stage": "complete",
                            "message": (
                                f"Saved {len(sql_artifacts)} transformed CSV file(s) after {len(artifacts) - len(sql_artifacts)} download(s)."
                                if run["job"].get("transformation", {}).get("enabled")
                                else f"Saved {len(artifacts)} CSV file(s)."
                            ),
                        },
                        artifacts, timings,
                    )
                except Exception as exc:
                    if timings:
                        if transformation_started is not None and not any(
                            item.get("phase") == "transformation" for item in timings
                        ):
                            timings.insert(-1, {
                                "phase": "transformation",
                                "duration_ms": round((time.perf_counter() - transformation_started) * 1000),
                                "status": "failed",
                            })
                        if sql_started is not None and not any(
                            item.get("phase") == "sql_insertion" for item in timings
                        ):
                            timings.insert(-1, {
                                "phase": "sql_insertion",
                                "duration_ms": round((time.perf_counter() - sql_started) * 1000),
                                "status": "failed",
                            })
                        timings[-1].update({
                            "duration_ms": round((time.perf_counter() - run_started) * 1000),
                            "status": "failed",
                        })
                    else:
                        timings = [{"phase": "total", "duration_ms": round((time.perf_counter() - run_started) * 1000), "status": "failed"}]
                    progress(
                        "failed", {"stage": "failed", "message": str(exc)},
                        artifacts=artifacts, timings=timings,
                        error=str(exc), traceback_text=traceback.format_exc(),
                    )
                finally:
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=2)
                if once:
                    break
            context.close()


def authenticate_asap(profile_dir: Path, auth_url: str, timeout_minutes: int = 10):
    """Create the automation profile's SSO session in a visible Edge window."""
    with _exclusive_worker_lock(profile_dir) as acquired:
        if not acquired:
            raise RuntimeError("The Flows worker is still using the automation browser profile.")
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel="msedge" if os.name == "nt" else None,
                headless=False,
                accept_downloads=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            _asap_goto(page, auth_url, profile_dir)
            print("Complete ASAP sign-in in the browser window if prompted.", flush=True)
            roots = _wait_for_navigation_roots(page, timeout_minutes * 60_000)
            if not roots:
                current_url = page.url
                title = _clean_text(page.title())
                context.close()
                raise RuntimeError(
                    f"ASAP authentication did not complete within {timeout_minutes} minutes "
                    f"(URL: {current_url}, title: {title})."
                )
            (profile_dir / AUTH_MARKER).write_text(json.dumps({
                "authenticated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "host": re.sub(r"^https?://([^/]+).*$", r"\1", page.url),
            }), encoding="utf-8")
            print("ASAP automation browser authenticated.", flush=True)
            context.close()


def main():
    parser = argparse.ArgumentParser(description="Metronome authenticated download worker")
    parser.add_argument("--server", default=os.environ.get("METRONOME_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--worker-id", default=os.environ.get("METRONOME_FLOW_WORKER_ID", socket.gethostname().lower()))
    parser.add_argument("--name", default=os.environ.get("METRONOME_FLOW_WORKER_NAME", socket.gethostname()))
    parser.add_argument("--profile-dir", default=os.environ.get("METRONOME_FLOW_PROFILE", str(Path.home() / ".metronome-flow-browser")))
    parser.add_argument("--headed", action="store_true", help="Show the browser. Recommended for initial SSO setup.")
    parser.add_argument("--authenticate-url", help="Open a one-time visible ASAP SSO bootstrap and exit.")
    parser.add_argument("--authentication-timeout-minutes", type=int, default=10)
    parser.add_argument("--once", action="store_true", help="Claim at most one run, then exit.")
    parser.add_argument("--idle-exit-seconds", type=int, default=0, help="Exit after this many idle seconds.")
    args = parser.parse_args()
    profile_dir = Path(args.profile_dir)
    if args.authenticate_url:
        authenticate_asap(profile_dir, args.authenticate_url, args.authentication_timeout_minutes)
        return
    with _exclusive_worker_lock(profile_dir) as acquired:
        if not acquired:
            print("Another Metronome flow worker is already running.", flush=True)
            return
        run_worker(
            args.server, args.worker_id, args.name, profile_dir, args.headed,
            args.once, max(0, args.idle_exit_seconds),
        )


if __name__ == "__main__":
    main()
