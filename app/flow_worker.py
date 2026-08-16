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
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timedelta
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
ASAP_LOADING_OVERLAY_SELECTOR = (
    "#loading-spinner-container, .loading-spinner-container, .loading-overlay"
)
ASAP_REPORT_RESULT_TIMEOUT_MS = 10 * 60 * 1_000
XLSX_HEADER_LABEL_HINTS = frozenset({
    "sell_out_region",
    "sell_out_subsidiary",
    "sell_out_country",
    "country_code",
    "operator",
    "province",
    "latitude",
    "longitude",
    "category",
    "biz_sub",
    "series",
    "mkt_name",
    "item",
})
REQUESTED_WEEK = re.compile(r"^(20\d{2})-W(\d{2})$", re.IGNORECASE)


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


def _render_filename(
    template: str, job: dict, period: str | list[str] | None, index: int,
    export_view: str | None = None,
) -> str:
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
        "export": re.sub(r"[^A-Za-z0-9]+", "_", export_view or "export").strip("_").casefold(),
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
    expected = f".{job['downloads'].get('file_format') or 'csv'}"
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
) -> bool:
    """Select an enhanced native control by its option labels.

    ASAP uses Select2 for Data Configuration. Its visible widget has no
    accessible name, while the owning ``select`` and ``option`` elements are
    hidden. Playwright can still use ``select_option`` on that native control,
    which also emits the change event Select2 and the report listen for.
    """
    expected = [str(item) for item in (expected_options or values)]
    selects = page.locator("select")
    for index in range(selects.count()):
        select = selects.nth(index)
        labels = [re.sub(r"\s+", " ", text).strip() for text in select.locator("option").all_text_contents()]
        if not all(item in labels for item in expected):
            continue
        selected = values if len(values) > 1 else values[0]
        select.select_option(label=selected, force=True)
        actual = [
            re.sub(r"\s+", " ", text).strip()
            for text in select.locator("option:checked").all_text_contents()
        ]
        if set(actual) != set(values):
            raise RuntimeError(
                f"ASAP native selection mismatch. Requested: {values}. Selected: {actual}."
            )
        return True
    return False


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
        option.first.click()
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


def _asap_week_dates(value: str) -> tuple[str, str]:
    """Return ASAP's Sunday-to-Saturday dates for one ISO-numbered week."""
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
    if not match:
        raise RuntimeError(f"ASAP week must use YYYY-Www: {value}")
    try:
        monday = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:
        raise RuntimeError(f"ASAP week does not exist: {value}") from exc
    sunday = monday - timedelta(days=1)
    saturday = sunday + timedelta(days=6)
    return sunday.strftime("%Y%m%d"), saturday.strftime("%Y%m%d")


def _asap_range_scope(frame: Frame, label: str):
    """Return the smallest visible prompt containing a two-handle range control."""
    labels = frame.get_by_text(re.compile(rf"^{re.escape(label)}:?$", re.I))
    for label_index in range(labels.count()):
        prompt = labels.nth(label_index)
        try:
            if not prompt.is_visible():
                continue
        except Exception:
            continue
        ancestor = prompt
        for _depth in range(8):
            ancestor = ancestor.locator("xpath=parent::*")
            if not ancestor.count():
                break
            handles = ancestor.locator("[role=slider],input[type=range]")
            visible = []
            for handle_index in range(handles.count()):
                handle = handles.nth(handle_index)
                try:
                    if handle.is_visible():
                        visible.append(handle)
                except Exception:
                    continue
            if len(visible) == 2:
                return ancestor, visible
    raise RuntimeError(f"ASAP {label} range slider was not found.")


def _asap_slider_value(handle, value_pattern: str) -> str | None:
    """Read one semantic slider value without trusting its screen position."""
    for attribute in (
        "aria-valuetext", "aria-valuenow", "value", "data-value", "data-val",
        "data-current-value",
    ):
        try:
            raw = _clean_text(handle.get_attribute(attribute))
        except Exception:
            raw = ""
        match = re.search(value_pattern, raw)
        if match:
            return match.group(0)
    try:
        match = re.search(value_pattern, _clean_text(handle.inner_text()))
    except Exception:
        match = None
    return match.group(0) if match else None


def _asap_slider_ordinal(value: str, kind: str) -> int:
    if kind == "week":
        match = re.fullmatch(r"(\d{4})(\d{2})", value)
        if not match:
            raise RuntimeError(f"ASAP Week slider exposed an invalid value: {value}")
        try:
            return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1).toordinal() // 7
        except ValueError as exc:
            raise RuntimeError(f"ASAP Week slider exposed a nonexistent week: {value}") from exc
    if kind == "date":
        try:
            return datetime.strptime(value, "%Y%m%d").date().toordinal()
        except ValueError as exc:
            raise RuntimeError(f"ASAP Date slider exposed an invalid value: {value}") from exc
    raise RuntimeError(f"Unsupported ASAP slider kind: {kind}")


def _asap_move_slider(handle, target: str, kind: str, *, current: str | None = None,
                      read_value=None) -> None:
    """Move one focused slider handle by keyboard and verify its exact value."""
    pattern = r"20\d{4}" if kind == "week" else r"20\d{6}"
    read_value = read_value or (lambda: _asap_slider_value(handle, pattern))
    current = current or read_value()
    if current is None:
        handle.press("Home")
        current = read_value()
    if current is None:
        raise RuntimeError(f"ASAP {kind.title()} slider did not expose its current value.")
    delta = _asap_slider_ordinal(target, kind) - _asap_slider_ordinal(current, kind)
    if abs(delta) > 1_000:
        raise RuntimeError(
            f"ASAP {kind.title()} slider target is more than 1,000 steps from its current value."
        )
    key = "ArrowRight" if delta > 0 else "ArrowLeft"
    for _step in range(abs(delta)):
        handle.press(key)
    actual = None
    for _attempt in range(10):
        actual = read_value()
        if actual == target:
            break
        time.sleep(0.05)
    if actual != target:
        raise RuntimeError(
            f"ASAP {kind.title()} slider mismatch. Requested: {target}. Selected: {actual or 'unknown'}."
        )


def _asap_range_values(scope, handles: list, kind: str) -> list[str | None]:
    """Read both range values from handle semantics or the control's visible labels."""
    pattern = r"20\d{4}" if kind == "week" else r"20\d{6}"
    values = [_asap_slider_value(handle, pattern) for handle in handles]
    if None not in values:
        return values
    try:
        visible = re.findall(rf"(?<!\d){pattern}(?!\d)", _clean_text(scope.inner_text()))
    except Exception:
        visible = []
    if len(visible) == len(handles):
        return [value or visible[index] for index, value in enumerate(values)]
    return values


def _asap_set_range(frame: Frame, label: str, start: str, end: str, kind: str) -> None:
    """Set and read back an ASAP two-handle range without coordinate guessing."""
    if _asap_slider_ordinal(end, kind) < _asap_slider_ordinal(start, kind):
        raise RuntimeError(f"ASAP {label} range ends before it starts: {start} to {end}")
    scope, handles = _asap_range_scope(frame, label)
    current = _asap_range_values(scope, handles, kind)
    # When advancing a collapsed one-period range, the upper handle must move
    # first or the lower handle is constrained by the old upper value.
    if current[1] and _asap_slider_ordinal(start, kind) > _asap_slider_ordinal(current[1], kind):
        order = ((1, end), (0, start))
    elif current[0] and _asap_slider_ordinal(end, kind) < _asap_slider_ordinal(current[0], kind):
        order = ((0, start), (1, end))
    else:
        order = ((0, start), (1, end))
    for index, target in order:
        _asap_move_slider(
            handles[index], target, kind, current=current[index],
            read_value=lambda index=index: _asap_range_values(scope, handles, kind)[index],
        )
        current = _asap_range_values(scope, handles, kind)
    actual = _asap_range_values(scope, handles, kind)
    if actual != [start, end]:
        raise RuntimeError(
            f"ASAP {label} range did not match the flow. Requested: {[start, end]}. Selected: {actual}."
        )


def _asap_week_options(start: str, end: str) -> list[str]:
    """Expand compact ASAP week bounds into every valid YYYYWW value."""
    start_match = re.fullmatch(r"(\d{4})(\d{2})", start)
    end_match = re.fullmatch(r"(\d{4})(\d{2})", end)
    if not start_match or not end_match:
        return []
    try:
        current = date.fromisocalendar(int(start_match.group(1)), int(start_match.group(2)), 1)
        final = date.fromisocalendar(int(end_match.group(1)), int(end_match.group(2)), 1)
    except ValueError:
        return []
    if final < current or (final - current).days > 7 * 104:
        return []
    values = []
    while current <= final:
        year, week, _weekday = current.isocalendar()
        values.append(f"{year:04d}{week:02d}")
        current += timedelta(days=7)
    return values


def _asap_discover_week_slider(frame: Frame) -> tuple[list[str], dict] | None:
    """Read the complete Week range and restore the report's original handles."""
    try:
        scope, handles = _asap_range_scope(frame, "Week")
    except RuntimeError:
        return None
    original = _asap_range_values(scope, handles, "week")
    if None in original:
        return None
    handles[0].press("Home")
    minimum = _asap_range_values(scope, handles, "week")[0]
    _asap_move_slider(
        handles[0], original[0], "week", current=minimum,
        read_value=lambda: _asap_range_values(scope, handles, "week")[0],
    )
    handles[1].press("End")
    maximum = _asap_range_values(scope, handles, "week")[1]
    _asap_move_slider(
        handles[1], original[1], "week", current=maximum,
        read_value=lambda: _asap_range_values(scope, handles, "week")[1],
    )
    if _asap_range_values(scope, handles, "week") != original:
        raise RuntimeError("ASAP Week slider could not be restored after discovery.")
    options = _asap_week_options(minimum or "", maximum or "")
    if not options:
        return None
    return options, {"kind": "range_slider", "date_range_label": "Date"}


def _asap_frame(page: Page) -> Frame:
    handle = page.locator(ASAP_FRAME_SELECTOR)
    handle.wait_for(state="attached", timeout=120_000)
    # ASAP replaces the report iframe during navigation. For a short interval
    # both the detached predecessor and its replacement can remain in the DOM.
    # Always choose the newest live frame so discovery cannot inspect the
    # report that was open before the requested menu item was clicked.
    for element in reversed(handle.element_handles()):
        frame = element.content_frame()
        if frame is not None and not frame.is_detached():
            return frame
    raise RuntimeError("ASAP report frame did not become available.")


def _asap_first_visible(locator):
    for item in locator.all():
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _asap_last_visible(locator):
    """Return the last visible match when a menu group and leaf share a label."""
    for item in reversed(locator.all()):
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _asap_loading_overlay_visible(page: Page) -> bool:
    """Return whether ASAP is currently blocking report interaction.

    ASAP leaves report controls visible beneath its loading overlay. Playwright
    therefore considers the control actionable until the overlay intercepts
    the actual pointer event. Inspect every live frame because the overlay can
    move with MicroStrategy's replacement iframe.
    """
    roots = list(dict.fromkeys([page.main_frame, *page.frames]))
    for root in roots:
        try:
            overlays = root.locator(ASAP_LOADING_OVERLAY_SELECTOR)
            for index in range(overlays.count()):
                if overlays.nth(index).is_visible():
                    return True
        except Exception:
            continue
    return False


def _asap_wait_for_loading_clear(page: Page, timeout_ms: int = ASAP_REPORT_RESULT_TIMEOUT_MS):
    """Wait for a sustained clear state before clicking an ASAP control."""
    deadline = time.monotonic() + (timeout_ms / 1_000)
    clear_polls = 0
    while time.monotonic() < deadline:
        if _asap_loading_overlay_visible(page):
            clear_polls = 0
        else:
            clear_polls += 1
            # A single clear DOM sample can occur between replacement frames.
            # Require one full second without the blocking overlay.
            if clear_polls >= 4:
                return
        page.wait_for_timeout(250)
    raise RuntimeError(
        f"ASAP loading overlay did not clear within {timeout_ms // 1_000} seconds."
    )


def _asap_wait_for_visible(
    page: Page, *locators, timeout_ms: int = 15_000, prefer_last: bool = False,
):
    """Wait for a portal menu item that may appear after hover animation."""
    visible_match = _asap_last_visible if prefer_last else _asap_first_visible
    deadline = time.monotonic() + (timeout_ms / 1_000)
    while time.monotonic() < deadline:
        for locator in locators:
            item = visible_match(locator)
            if item is not None:
                return item
        page.wait_for_timeout(100)
    return None


def _asap_frame_signature(frame: Frame | None) -> str | None:
    if frame is None or frame.is_detached():
        return None
    try:
        text = _clean_text(frame.locator("body").inner_text(timeout=2_000))
    except Exception:
        return None
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _asap_text_visible_across_frames(page: Page, label: str) -> bool:
    """Find an exact ASAP breadcrumb in either the portal shell or report frame."""
    roots = [getattr(page, "main_frame", page), *getattr(page, "frames", [])]
    for root in list(dict.fromkeys(roots)):
        try:
            if _asap_first_visible(root.get_by_text(label, exact=True)) is not None:
                return True
        except Exception:
            continue
    return False


def _asap_wait_for_report_navigation(
    page: Page, previous_frame: Frame | None, target_control, path: list[str],
    previous_signature: str | None = None, timeout_ms: int = 120_000,
) -> Frame:
    """Wait until the clicked menu item closes and the requested report becomes stable."""
    try:
        target_control.wait_for(state="hidden", timeout=min(timeout_ms, 30_000))
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            f"ASAP did not leave the menu item for the requested report: {path[-1]}"
        ) from exc

    deadline = time.monotonic() + (timeout_ms / 1_000)
    stable_signature = None
    stable_polls = 0
    last_path_visibility: dict[str, bool] = {}
    last_frame_replaced = False
    last_content_changed = False
    while time.monotonic() < deadline:
        try:
            current = _asap_frame(page)
            frame_replaced = previous_frame is None or current is not previous_frame
            if previous_frame is not None:
                frame_replaced = frame_replaced or previous_frame.is_detached()
            visible_path = True
            last_path_visibility = {}
            for label in path[-2:]:
                label_visible = _asap_text_visible_across_frames(page, label)
                last_path_visibility[label] = label_visible
                if not label_visible:
                    visible_path = False
            current_signature = _asap_frame_signature(current)
            content_changed = bool(
                previous_signature and current_signature
                and current_signature != previous_signature
            )
            last_frame_replaced = frame_replaced
            last_content_changed = content_changed
            if visible_path:
                # Reopening the report already preserved in ASAP can reuse
                # both the iframe and its body. The exact live breadcrumb is
                # still strong proof once that body is stable for three polls.
                if current_signature == stable_signature:
                    stable_polls += 1
                else:
                    stable_signature = current_signature
                    stable_polls = 1
                if frame_replaced or stable_polls >= 3:
                    return current
            # Some ASAP renderings do not expose breadcrumb labels to the
            # accessibility tree even though the exact menu link was clicked.
            # For a reused iframe, the closed target control plus a changed,
            # stable report body is the reliable navigation proof.
            if content_changed and not visible_path:
                if current_signature == stable_signature:
                    stable_polls += 1
                else:
                    stable_signature = current_signature
                    stable_polls = 1
                if stable_polls >= 3:
                    return current
        except Exception:
            pass
        page.wait_for_timeout(250)
    raise RuntimeError(
        "ASAP did not finish navigating to the requested report breadcrumb: "
        + " > ".join(path)
        + f". Visible path labels: {last_path_visibility}; "
        + f"iframe replaced: {last_frame_replaced}; body changed: {last_content_changed}."
    )


def _asap_raw_table_ready(frame: Frame) -> bool:
    """Recognize TechInsights raw-data views that omit the Data rows marker."""
    try:
        hint = frame.get_by_text(re.compile(r"to download raw data", re.I)).first
        if not hint.count() or not hint.is_visible():
            return False
        tables = frame.locator("table:visible")
        for index in range(tables.count()):
            rows = tables.nth(index).locator("tr")
            visible_rows = 0
            for row_index in range(rows.count()):
                if rows.nth(row_index).is_visible():
                    visible_rows += 1
                    if visible_rows >= 2:
                        return True
    except Exception:
        return False
    return False


def _asap_wait_for_results(
    page: Page, timeout_ms: int = ASAP_REPORT_RESULT_TIMEOUT_MS,
) -> Frame:
    """Return the live report frame once ASAP has rendered result rows.

    Running a report replaces ``iframe#content-frame`` in the current ASAP UI.
    A Frame captured before clicking RUN can therefore remain detached while the
    replacement frame already shows the completed report. Re-resolve the iframe
    during the wait and return the replacement so export uses the same live UI.
    """
    deadline = time.monotonic() + (timeout_ms / 1_000)
    last_error: Exception | None = None
    last_loading_state = False
    while time.monotonic() < deadline:
        try:
            last_loading_state = _asap_loading_overlay_visible(page)
            # MicroStrategy may briefly retain the old iframe element and add
            # the replacement as another frame. Inspect every current frame,
            # newest first, instead of trusting the first matching element.
            for frame in reversed(page.frames):
                rows = frame.get_by_text("Data rows:", exact=False).first
                if rows.count() and rows.is_visible():
                    return frame
                # TechInsights export views render the populated raw table
                # directly and never add MicroStrategy's Data rows label.
                # Require both the report's raw-data instruction and at least
                # two visible table rows, with no blocking overlay remaining.
                if not last_loading_state and _asap_raw_table_ready(frame):
                    return frame
        except Exception as exc:
            # Frame replacement can race any locator operation. The next poll
            # resolves the new element instead of retaining the detached frame.
            last_error = exc
        page.wait_for_timeout(500)
    detail = f" Last frame error: {last_error}" if last_error else ""
    detail += (
        " The ASAP loading overlay was still visible."
        if last_loading_state else
        " The loading overlay cleared, but neither a Data rows marker nor a populated raw table appeared."
    )
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
    def navigate(previous_frame: Frame | None, previous_signature: str | None):
        root = _asap_wait_for_visible(
            page,
            page.get_by_role("link", name=path[0], exact=True),
            page.get_by_text(path[0], exact=True),
        )
        if root is None:
            raise RuntimeError(f"ASAP menu was not visible: {path[0]}")
        root.click()
        if len(path) > 2:
            visible_group = _asap_wait_for_visible(
                page,
                page.get_by_role("link", name=path[-2], exact=True),
                page.get_by_text(path[-2], exact=True),
            )
            if visible_group is not None:
                visible_group.hover()
        repeated_label = len(path) > 2 and path[-1].casefold() == path[-2].casefold()
        target = _asap_wait_for_visible(
            page,
            page.get_by_role("link", name=path[-1], exact=True),
            page.get_by_text(path[-1], exact=True),
            prefer_last=repeated_label,
        )
        if target is None:
            raise RuntimeError(f"ASAP report menu item was not visible: {path[-1]}")
        target.click()
        return _asap_wait_for_report_navigation(
            page, previous_frame, target, path, previous_signature,
        )

    try:
        previous_frame = _asap_frame(page)
    except Exception:
        previous_frame = None
    previous_signature = _asap_frame_signature(previous_frame)
    frame = navigate(previous_frame, previous_signature)

    expected = automation.get("report_tab") or report.get("ready_text")
    last_error = None
    for attempt in range(2):
        try:
            if expected:
                # Export-view links can live in the outer ASAP shell or in a
                # nested MicroStrategy frame. Waiting in only content-frame
                # made the same report appear missing depending on portal
                # rendering timing.
                deadline = time.monotonic() + 120
                found_expected = False
                while time.monotonic() < deadline and not found_expected:
                    for root in list(dict.fromkeys([page.main_frame, frame, *page.frames])):
                        try:
                            matches = root.get_by_text(expected, exact=True)
                            if any(item.is_visible() for item in matches.all()):
                                found_expected = True
                                break
                        except Exception:
                            continue
                    if not found_expected:
                        page.wait_for_timeout(250)
                if not found_expected:
                    raise RuntimeError(f"ASAP report did not show its expected view: {expected}")
            if frame.get_by_text("500", exact=True).count():
                raise RuntimeError("ASAP returned an internal server error while loading the report.")
            return frame
        except (PlaywrightTimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt:
                break
            page.go_back(wait_until="domcontentloaded", timeout=120_000)
            try:
                previous_frame = _asap_frame(page)
            except Exception:
                previous_frame = None
            previous_signature = _asap_frame_signature(previous_frame)
            frame = navigate(previous_frame, previous_signature)
    raise RuntimeError(f"ASAP report did not load after one retry: {last_error}")


def _asap_member_selected(option) -> bool | None:
    """Read selection state from ASAP's ARIA, class, or non-hovered row styling."""
    try:
        return option.evaluate(
            r"""node => {
                for (let current = node, depth = 0; current && depth < 5; current = current.parentElement, depth++) {
                    const aria = current.getAttribute('aria-selected') ?? current.getAttribute('aria-checked');
                    if (aria === 'true') return true;
                    if (aria === 'false') return false;
                    const classes = String(current.className || '');
                    if (/(^|[-_ ])(?:selected|checked)(?:$|[-_ ])/i.test(classes) || /itemSelected/i.test(classes)) return true;
                    // ASAP uses a blue background for both selection and hover.
                    // Styling is only evidence after the pointer has left the row.
                    if (!current.matches(':hover')) {
                        const style = getComputedStyle(current);
                        const match = style.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                        if (match) {
                            const [r, g, b] = match.slice(1).map(Number);
                            if (b > r + 35 && b > g + 15 && b > 120) return true;
                        }
                    }
                }
                return null;
            }"""
        )
    except Exception:
        return None


def _asap_list_scope(frame: Frame, label: str, requested: list[str]):
    """Resolve the semantic or smallest structural owner of a prompt list."""
    # Prefer the WAI-ARIA listbox contract when the portal exposes it. This is
    # both stricter and more resilient than page-wide text matching.
    try:
        listboxes = frame.get_by_role("listbox", name=label, exact=True)
        for index in range(listboxes.count()):
            listbox = listboxes.nth(index)
            if not listbox.is_visible():
                continue
            if all(any(
                listbox.get_by_text(value, exact=True).nth(option_index).is_visible()
                for option_index in range(listbox.get_by_text(value, exact=True).count())
            ) for value in requested):
                return listbox
    except Exception:
        pass

    # Legacy MicroStrategy controls do not always expose listbox semantics.
    # In that case, narrow from every matching label to the nearest ancestor
    # containing every requested member, then use only descendants of it.
    labels = frame.get_by_text(label, exact=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        matches = []
        for index in range(labels.count()):
            heading = labels.nth(index)
            try:
                if not heading.is_visible():
                    continue
                # Lightweight test doubles and older adapters do not expose
                # locator traversal. Their frame is already the list scope.
                if not callable(getattr(heading, "locator", None)):
                    return frame
                ancestor = heading
                for depth in range(1, 9):
                    parents = ancestor.locator("xpath=parent::*")
                    if not parents.count():
                        break
                    ancestor = parents.first
                    contains_requested = True
                    for value in requested:
                        candidates = ancestor.get_by_text(value, exact=True)
                        if not any(
                            candidates.nth(option_index).is_visible()
                            for option_index in range(candidates.count())
                        ):
                            contains_requested = False
                            break
                    if not contains_requested:
                        continue
                    box = ancestor.bounding_box()
                    area = box["width"] * box["height"] if box else float("inf")
                    matches.append((depth, area, ancestor))
                    break
            except Exception:
                continue
        if matches:
            return min(matches, key=lambda match: (match[0], match[1]))[2]
        frame.page.wait_for_timeout(250)
    raise RuntimeError(
        f"Could not isolate the ASAP {label} prompt containing: {requested}. "
        "The report was not run with an ambiguous filter."
    )


def _asap_live_list_values(scope, label: str) -> list[str]:
    """Read list members rendered inside an isolated ASAP prompt."""
    values = []
    try:
        options = scope.locator("[role='option'], option, [aria-selected], [aria-checked]")
        for index in range(options.count()):
            option = options.nth(index)
            if not option.is_visible():
                continue
            value = _clean_text(option.inner_text() or option.get_attribute("value"))
            if value and value not in values:
                values.append(value)
    except Exception:
        pass
    if values:
        return values

    # Visual-only legacy lists have no option role. The already-isolated
    # container is the boundary, so its distinct rendered lines are the safest
    # generic fallback. Selection-state inspection later discards non-options.
    try:
        lines = list(dict.fromkeys(
            _clean_text(line) for line in scope.inner_text().splitlines() if _clean_text(line)
        ))
    except Exception:
        return []
    for value in lines:
        if value.casefold().rstrip(":") == label.casefold().rstrip(":"):
            continue
        if _is_asap_list_metadata(value):
            continue
        values.append(value)
    return values


def _asap_select_list_values(
    frame: Frame, label: str, values: list[str], available_values: list[str] | None = None,
):
    if not values:
        return

    requested = list(dict.fromkeys(values))
    scope = _asap_list_scope(frame, label, requested)

    def park_pointer():
        """Remove hover styling before reading a row's rendered state."""
        frame.page.mouse.move(2, 2)
        frame.page.wait_for_timeout(100)

    def visible_option(value: str):
        candidates = scope.get_by_text(value, exact=True)
        option = next(
            (candidates.nth(index) for index in range(candidates.count()) if candidates.nth(index).is_visible()),
            None,
        )
        if option is None:
            raise RuntimeError(f"Could not find {label} option: {value}")
        return option

    available = list(dict.fromkeys([
        *(available_values or []), *_asap_live_list_values(scope, label), *requested,
    ]))

    # A previous interaction may have left the pointer over a blue hover row.
    # Never use that transient styling as the initial selected-state snapshot.
    park_pointer()

    def selected_states() -> dict[str, bool]:
        result = {}
        for value in available:
            try:
                state = _asap_member_selected(visible_option(value))
            except RuntimeError:
                continue
            if state is not None:
                result[value] = state
        return result

    def stable_exact_selection(samples: int = 3) -> tuple[bool, dict[str, bool]]:
        states = {}
        for sample in range(samples):
            states = selected_states()
            actual = {value for value, selected in states.items() if selected}
            if actual != set(requested) or any(states.get(value) is not True for value in requested):
                return False, states
            if sample + 1 < samples:
                frame.page.wait_for_timeout(150)
        return True, states

    def member_state(value: str) -> bool | None:
        try:
            return _asap_member_selected(visible_option(value))
        except RuntimeError:
            return None

    def set_member_state(value: str, selected: bool, attempts: int = 3) -> bool:
        """Click one member until the rendered portal state confirms the result."""
        for _attempt in range(attempts):
            state = member_state(value)
            if (selected and state is True) or (not selected and state is not True):
                return True
            # Playwright's delay is the time between mouse-down and mouse-up.
            # Keep this a single ordinary left click, with no keyboard modifier.
            visible_option(value).click(button="left", click_count=1, delay=100)
            park_pointer()
            for _sample in range(10):
                frame.page.wait_for_timeout(150)
                state = member_state(value)
                if (selected and state is True) or (not selected and state is not True):
                    return True
        return False

    def reconcile_requested_selection(rounds: int = 4) -> tuple[bool, dict[str, bool]]:
        """Make the rendered selection exactly match requested using plain clicks."""
        states = {}
        for _round in range(rounds):
            exact, states = stable_exact_selection()
            if exact:
                return True, states
            extras = [
                value for value, selected in states.items()
                if selected and value not in requested
            ]
            # MicroStrategy commonly represents an unselected row as neither
            # true nor false. Missing therefore means anything not confirmed
            # true, including an absent or unknown state.
            missing = [value for value in requested if states.get(value) is not True]
            for value in extras:
                set_member_state(value, False)
            for value in missing:
                set_member_state(value, True)
            frame.page.wait_for_timeout(300)
        return stable_exact_selection()

    if label.casefold().rstrip(":") == "dimension":
        # Dimension is the one ASAP prompt that opens with a retained member.
        # Its list rows toggle with a normal left click. Clear every visibly
        # selected member first, then select the Metronome values the same way.
        # Do not use Ctrl or manipulate the hidden native owner for this prompt.
        initial_states = selected_states()
        if not initial_states:
            raise RuntimeError(
                "ASAP Dimension did not expose a verifiable selected state. "
                "The report was not run with an unverified filter."
            )
        cleared_states = initial_states
        for _round in range(4):
            retained = [value for value, selected in cleared_states.items() if selected]
            if not retained:
                break
            for value in retained:
                set_member_state(value, False)
            frame.page.wait_for_timeout(300)
            cleared_states = selected_states()
        retained = [value for value, selected in cleared_states.items() if selected]
        if retained:
            raise RuntimeError(
                f"ASAP Dimension could not be cleared with plain clicks. Still selected: {retained}."
            )

        exact, final_states = reconcile_requested_selection()
        actual = [value for value, selected in final_states.items() if selected]
        missing = [value for value in requested if final_states.get(value) is not True]
        extras = [value for value in actual if value not in requested]
        if not exact or missing or extras:
            raise RuntimeError(
                f"ASAP Dimension selection did not match the flow. "
                f"Requested: {requested}. Selected: {actual}."
            )
        return

    # The rendered list toggles each value with a normal left click. Do not
    # assume a click landed: MicroStrategy can drop a rapid final click and it
    # reports an unselected row as an unknown state rather than explicit false.
    # Confirm each change and retry it before the final exact-set check.
    exact, final_states = reconcile_requested_selection()
    if final_states:
        actual = [value for value, selected in final_states.items() if selected]
        if not exact:
            raise RuntimeError(
                f"ASAP {label} selection did not match the flow. "
                f"Requested: {requested}. Selected: {actual}."
            )
        return
    raise RuntimeError(
        f"ASAP {label} did not expose a verifiable selected state. "
        "The report was not run with an unverified filter."
    )


def _asap_apply_configuration(frame: Frame, job: dict, period: str | list[str] | None):
    selections = dict(job.get("selections") or {})
    for definition in job["report"].get("filters", []):
        key = definition["filter_key"]
        value = period if definition["control_type"] == "week" and period else selections.get(key)
        if value in (None, "", []):
            continue
        values = value if isinstance(value, list) else [value]
        values = [_week_to_asap(str(item)) if definition["control_type"] == "week" else str(item) for item in values]
        automation = definition.get("automation") or {}
        range_slider = definition["control_type"] == "week" and automation.get("kind") == "range_slider"
        if definition["control_type"] == "week" and not range_slider:
            try:
                _asap_range_scope(frame, definition["control_label"])
                range_slider = True
            except Exception:
                # This is only capability probing. The established visible-list
                # path below remains authoritative when no live slider is found.
                pass
        if range_slider:
            _asap_set_range(frame, definition["control_label"], values[0], values[-1], "week")
            start_date = _asap_week_dates(str(value[0] if isinstance(value, list) else value))[0]
            end_date = _asap_week_dates(str(value[-1] if isinstance(value, list) else value))[1]
            _asap_set_range(
                frame, str(automation.get("date_range_label") or "Date"),
                start_date, end_date, "date",
            )
            continue
        visible_list_only = (
            definition["control_label"].casefold().rstrip(":")
            in {"dimension", "category", "sell-out country"}
            or definition["control_type"] == "week"
        )
        if visible_list_only:
            _asap_select_list_values(
                frame, definition["control_label"], values, definition.get("options") or values,
            )
            continue
        if definition["control_type"] == "select":
            _set_filter(frame, definition, values[0])
        else:
            if _select_native_options_by_text(frame, values, definition.get("options") or values):
                continue
            _asap_select_list_values(
                frame, definition["control_label"], values, definition.get("options") or values,
            )


DOWNLOAD_START_TIMEOUT_SECONDS = 60
DOWNLOAD_STALL_TIMEOUT_SECONDS = 90
DOWNLOAD_MAX_TIMEOUT_SECONDS = 10 * 60


def _download_file_state(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _download_staging_snapshot(staging_dir: Path) -> dict[Path, tuple[int, int]]:
    staging_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {}
    for candidate in staging_dir.iterdir():
        try:
            if candidate.is_file():
                snapshot[candidate.resolve()] = _download_file_state(candidate)
        except OSError:
            continue
    return snapshot


def _wait_for_staged_download(
    staging_dir: Path,
    files_before: dict[Path, tuple[int, int]],
    timeout_seconds: int = DOWNLOAD_MAX_TIMEOUT_SECONDS,
    start_timeout_seconds: int = DOWNLOAD_START_TIMEOUT_SECONDS,
    stall_timeout_seconds: int = DOWNLOAD_STALL_TIMEOUT_SECONDS,
) -> Path:
    """Return a new or overwritten stable local file without Playwright path calls."""
    started = time.monotonic()
    deadline = started + timeout_seconds
    last_activity = started
    observed: dict[Path, tuple[int, int]] = {}
    last_candidate = None
    last_candidate_state = None
    stable_checks = 0
    while time.monotonic() < deadline:
        candidates = []
        for candidate in staging_dir.iterdir():
            try:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                state = _download_file_state(candidate)
            except OSError:
                continue
            if files_before.get(resolved) == state:
                continue
            if observed.get(resolved) != state:
                observed[resolved] = state
                last_activity = time.monotonic()
            if not candidate.name.casefold().endswith((".crdownload", ".tmp")):
                candidates.append((candidate, state))
        if candidates:
            candidate, state = max(candidates, key=lambda item: item[1][0])
            if candidate == last_candidate and state[1] > 0 and state == last_candidate_state:
                stable_checks += 1
                if stable_checks >= 3:
                    return candidate
            else:
                last_candidate = candidate
                last_candidate_state = state
                stable_checks = 0
        else:
            last_candidate = None
            last_candidate_state = None
            stable_checks = 0
        now = time.monotonic()
        if not observed and now - started >= start_timeout_seconds:
            raise RuntimeError(
                "ASAP started an Edge download, but no new or updated file appeared "
                f"in the local staging folder within {start_timeout_seconds} seconds: {staging_dir}"
            )
        if observed and now - last_activity >= stall_timeout_seconds:
            observed_summary = ", ".join(
                f"{path.name} ({state[1]} bytes)" for path, state in observed.items()
            )
            raise RuntimeError(
                f"The Edge download stopped changing for {stall_timeout_seconds} seconds in "
                f"the local staging folder: {staging_dir}. Observed: {observed_summary}"
            )
        time.sleep(0.5)
    observed_summary = ", ".join(
        f"{path.name} ({state[1]} bytes)" for path, state in observed.items()
    ) or "none"
    raise RuntimeError(
        f"The Edge download did not produce a stable finished file within {timeout_seconds} "
        f"seconds in the local staging folder: {staging_dir}. Observed: {observed_summary}"
    )


def _asap_table_control_score(
    table_box: dict[str, float] | None, control_box: dict[str, float] | None,
) -> float | None:
    """Rank a compact control rendered at the raw table's top-right corner."""
    if not table_box or not control_box:
        return None
    width = control_box.get("width", 0)
    height = control_box.get("height", 0)
    if not (4 <= width <= 64 and 4 <= height <= 64):
        return None
    table_left = table_box["x"]
    table_top = table_box["y"]
    table_right = table_left + table_box["width"]
    center_x = control_box["x"] + width / 2
    center_y = control_box["y"] + height / 2
    if center_x < table_left + table_box["width"] * 0.65 or center_x > table_right + 24:
        return None
    if center_y < table_top - 40 or center_y > table_top + 72:
        return None
    return abs(center_x - (table_right - 14)) + abs(center_y - (table_top + 14))


def _asap_run_control(root: Page | Frame):
    """Return ASAP's RUN control across button, input, and text renderings."""
    for build_locator in (
        lambda: root.get_by_role("button", name=re.compile(r"^RUN$", re.I)),
        lambda: root.locator(
            "input[type='button'][value='RUN' i]:visible,"
            "input[type='submit'][value='RUN' i]:visible"
        ),
        lambda: root.get_by_text("RUN", exact=True),
    ):
        try:
            locator = build_locator()
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def _asap_export_action(root: Page | Frame):
    """Return a visible Export action across button, input, and text renderings."""
    for build_locator in (
        lambda: root.get_by_role("button", name="Export", exact=True),
        lambda: root.locator(
            "input[type='button'][value='Export' i]:visible,"
            "input[type='submit'][value='Export' i]:visible"
        ),
        lambda: root.get_by_text("Export", exact=True),
    ):
        try:
            locator = build_locator()
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def _asap_export_format_names(file_format: str) -> tuple[str, re.Pattern]:
    """Return the preferred raw export label and compatible MicroStrategy variants."""
    if file_format == "xlsx":
        return (
            "Excel with plain text",
            re.compile(
                r"^(?:Microsoft )?Excel(?: workbook| file format| with plain text)?"
                r"(?: \(.*\.xlsx.*\))?$",
                re.I,
            ),
        )
    return (
        "CSV file format",
        re.compile(r"^(?:CSV|Comma separated values)(?: file format)?$", re.I),
    )


def _asap_raw_table_information_control(root: Page | Frame, canvas):
    """Find an unlabeled hover control by its documented table-corner position."""
    try:
        table_box = canvas.bounding_box()
    except Exception:
        return None
    candidates = root.locator(
        "button:visible,a:visible,[role='button']:visible,input[type='button']:visible,"
        "img:visible,[class*='info' i]:visible,[class*='icon' i]:visible"
    )
    ranked = []
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        try:
            score = _asap_table_control_score(table_box, candidate.bounding_box())
            if score is None:
                continue
            signal = " ".join(filter(None, (
                candidate.get_attribute("title"),
                candidate.get_attribute("aria-label"),
                candidate.get_attribute("class"),
                candidate.get_attribute("alt"),
            ))).casefold()
            if re.search(r"info|export|more", signal):
                score -= 100
            ranked.append((score, candidate))
        except Exception:
            continue
    return min(ranked, key=lambda item: item[0])[1] if ranked else None


def _asap_wait_for_raw_menu_download_action(
    page: Page, file_format: str, timeout_ms: int = 10_000,
):
    """Return a raw-table menu item that starts the requested download directly."""
    if file_format != "xlsx":
        return None
    excel_pattern = re.compile(r"^(?:Microsoft )?Excel$", re.I)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for candidate in reversed(page.context.pages):
            for root in [candidate, *reversed(candidate.frames)]:
                for locator in (
                    root.get_by_role("menuitem", name=excel_pattern),
                    root.get_by_text(excel_pattern),
                ):
                    try:
                        if locator.count() and locator.first.is_visible():
                            return locator.first
                    except Exception:
                        continue
        page.wait_for_timeout(100)
    return None


def _asap_wait_for_raw_menu_export_or_wizard(
    page: Page, pages_before: set, timeout_ms: int = 10_000,
):
    """Return the intermediate menu action, or report that Export Options opened directly."""
    deadline = time.monotonic() + timeout_ms / 1000
    wizard_pattern = re.compile(r"^(?:Excel|CSV) file format$", re.I)
    while time.monotonic() < deadline:
        current_pages = page.context.pages
        if any(candidate not in pages_before for candidate in current_pages):
            return None, True
        for candidate in reversed(current_pages):
            for root in [candidate, *reversed(candidate.frames)]:
                try:
                    wizard_marker = root.get_by_text(wizard_pattern)
                    if wizard_marker.count() and wizard_marker.first.is_visible():
                        return None, True
                except Exception:
                    pass
                for locator in (
                    root.get_by_role("menuitem", name="Export", exact=True),
                    root.get_by_role("button", name="Export", exact=True),
                    root.get_by_text("Export", exact=True),
                ):
                    try:
                        if locator.count() and locator.first.is_visible():
                            return locator.first, False
                    except Exception:
                        continue
        page.wait_for_timeout(200)
    return None, False


def _asap_wait_for_raw_export_confirmation(
    page: Page, file_format: str, timeout_ms: int = 10_000,
):
    """Find the final Export button in ASAP's compact raw-table dialog."""
    if file_format != "xlsx":
        return None
    title_pattern = re.compile(r"^Export to (?:Microsoft )?Excel$", re.I)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for candidate in reversed(page.context.pages):
            for root in [candidate, *reversed(candidate.frames)]:
                try:
                    title = root.get_by_text(title_pattern)
                    if not title.count() or not title.first.is_visible():
                        continue
                except Exception:
                    continue
                action = _asap_export_action(root)
                if action is not None:
                    return action
        page.wait_for_timeout(100)
    return None


def _asap_download(page: Page, frame: Frame, job: dict, staging_dir: Path):
    export_control = None
    opens_export_menu = False
    file_format = str(job.get("downloads", {}).get("file_format") or "csv").casefold()
    # The current MicroStrategy report has two compact controls beside RUN.
    # The first is Export Options and the second is subtotal. Their icon-only
    # markup has no stable accessible label, so resolve the first visible
    # button-like control after RUN within the same toolbar.
    for root in reversed(page.frames):
        try:
            run = _asap_run_control(root)
            if run is None:
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
        # Some raw-data views have no prompt RUN toolbar. Their export action
        # appears only after the rendered table is hovered and its information
        # icon is opened, matching ASAP's own download tutorial.
        for root in reversed(page.frames):
            try:
                canvas = next((
                    locator.first
                    for selector in (
                        "table:visible", "[role=grid]:visible",
                        "[class*='grid' i]:visible", "[class*='table' i]:visible",
                        "[class*='report' i]:visible",
                    )
                    if (locator := root.locator(selector)).count()
                    and locator.first.is_visible()
                ), root.locator("table:visible").first)
                if canvas.count() and canvas.is_visible():
                    canvas.hover()
                    page.wait_for_timeout(750)
                candidates = [
                    root.get_by_role(
                        "button", name=re.compile(r"^(?:information|info|more info)$", re.I),
                    ),
                    root.locator(
                        "[title*='information' i]:visible,[title='Info' i]:visible,"
                        "[aria-label*='information' i]:visible,[aria-label='Info' i]:visible,"
                        "img[alt*='information' i]:visible,img[alt='Info' i]:visible"
                    ),
                ]
                export_control = next(
                    (
                        locator.first for locator in candidates
                        if locator.count() and locator.first.is_visible()
                    ),
                    None,
                )
                if export_control is None and canvas.count() and canvas.is_visible():
                    export_control = _asap_raw_table_information_control(root, canvas)
                if export_control is not None:
                    opens_export_menu = True
                    break
            except Exception:
                continue
    if export_control is None:
        raise RuntimeError(
            "Could not find either the ASAP Export Options control beside RUN "
            "or the raw-table information control."
        )
    pages_before = set(page.context.pages)
    export_control.click()
    direct_download_action = None
    if opens_export_menu:
        menu_export, wizard_opened = _asap_wait_for_raw_menu_export_or_wizard(
            page, pages_before,
        )
        if menu_export is None and not wizard_opened:
            raise RuntimeError("ASAP raw-table information menu opened, but its Export action was not visible.")
        if menu_export is not None:
            menu_export.click()
            direct_download_action = _asap_wait_for_raw_menu_download_action(page, file_format)
    # ASAP sometimes opens the wizard as a page and sometimes as a modal/frame
    # in the existing page. Search both shapes instead of requiring a popup.
    format_option = None
    export_action = None
    wizard_pages = set()
    deadline = time.monotonic() + 60
    format_names = _asap_export_format_names(file_format)
    while (
        direct_download_action is None
        and time.monotonic() < deadline
        and (format_option is None or export_action is None)
    ):
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
            action = _asap_export_action(root)
            if action is not None:
                export_action = action
                wizard_pages.add(root if isinstance(root, Page) else root.page)
        if format_option is None or export_action is None:
            page.wait_for_timeout(250)
    if direct_download_action is None and (format_option is None or export_action is None):
        raise RuntimeError(
            f"ASAP Export Wizard opened, but its {file_format.upper()} option or Export action "
            f"was not recognized. Format option found: {format_option is not None}. "
            f"Export action found: {export_action is not None}."
        )
    if direct_download_action is None:
        try:
            format_option.check()
        except Exception:
            format_option.click()
    # The export wizard may render in one popup while Edge attributes the
    # resulting download to another ASAP page. Listening only on the guessed
    # popup can therefore leave a visibly completed browser download waiting
    # until timeout. Subscribe to every current portal page before clicking.
    files_before = _download_staging_snapshot(staging_dir)
    downloads = []
    observed_pages = list(page.context.pages)

    def capture_download(download):
        downloads.append(download)

    for candidate in observed_pages:
        candidate.on("download", capture_download)
    try:
        (direct_download_action or export_action).click()
        if direct_download_action is not None:
            confirmation = _asap_wait_for_raw_export_confirmation(page, file_format)
            if confirmation is None:
                raise RuntimeError(
                    "ASAP raw-table Excel dialog opened, but its final Export action was not visible."
                )
            confirmation.click()
        deadline = time.monotonic() + 180
        while not downloads and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        if not downloads:
            raise RuntimeError("ASAP export started, but Edge did not expose the completed download within 3 minutes.")
        staged_file = _wait_for_staged_download(staging_dir, files_before)
        export_pages = [
            candidate for candidate in wizard_pages
            if candidate not in pages_before and candidate is not page
        ]
        return staged_file, export_pages
    finally:
        for candidate in observed_pages:
            candidate.remove_listener("download", capture_download)


def _asap_download_with_retry(
    page: Page, frame: Frame, job: dict, staging_dir: Path,
):
    """Retry one transient export-wizard recognition failure.

    A long ASAP run repeatedly opens the same MicroStrategy export wizard. In
    practice, the popup can occasionally open without exposing either of its
    controls to Playwright. Reopening that same export once is safe because no
    download has started at the point where this specific error is raised.
    """
    retryable_prefix = "ASAP Export Wizard opened, but its "
    retryable_suffix = "option or Export action was not recognized."
    for attempt in range(2):
        try:
            return _asap_download(page, frame, job, staging_dir)
        except RuntimeError as exc:
            message = str(exc)
            if (
                attempt == 1
                or not message.startswith(retryable_prefix)
                or retryable_suffix not in message
            ):
                raise
            page.wait_for_timeout(1_500)
    raise AssertionError("unreachable")


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


def _is_asap_list_metadata(value: str) -> bool:
    """Return whether a rendered prompt line describes the list, not a member."""
    cleaned = _clean_text(value)
    return (
        "type to search" in cleaned.casefold()
        or bool(re.fullmatch(
            r"\(all\)(?:\s*(?:\(\d+\s+values?\)|\d+\s+values?))?", cleaned, re.I,
        ))
    )


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
        if value and value != label and not _is_asap_list_metadata(value)
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
        # The portal renders its navigation shell before removing the blocking
        # loading overlay. Playwright can therefore resolve a menu link while
        # the overlay still intercepts the click. Wait for a sustained clear
        # state before resolving the live link used for this interaction.
        _asap_wait_for_loading_clear(page)
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


def _asap_owned_popup_roots(frame: Frame, control) -> list:
    """Resolve only popups owned by the active combobox when possible."""
    roots = []
    owner_ids = _clean_text(
        control.get_attribute("aria-controls") or control.get_attribute("aria-owns")
    ).split()
    for owner_id in owner_ids:
        owned = frame.locator(f"[id={json.dumps(owner_id)}]")
        for index in range(owned.count()):
            candidate = owned.nth(index)
            if candidate.is_visible():
                roots.append(candidate)
    if roots:
        return roots

    # Legacy Select2 can omit ownership attributes. Its open dropdown is still
    # a bounded listbox/dropdown, so use that container rather than scanning
    # every list item in the report frame.
    candidates = frame.locator(
        "[role=listbox]:visible,.select2-dropdown:visible,.select2-results:visible"
    )
    return [
        candidates.nth(index) for index in range(candidates.count())
        if candidates.nth(index).is_visible()
    ]


def _asap_discover_filters(frame: Frame) -> list[dict]:
    definitions = []

    def add_definition(
        label: str, control_type: str, options: list[str], automation: dict | None = None,
    ):
        _merge_asap_filter_definition(definitions, label, control_type, options)
        if automation:
            key = _slug_key(label, f"filter_{len(definitions)}")
            definition = next(
                (item for item in definitions if item["filter_key"] == key), None,
            )
            if definition is not None:
                definition["automation"] = {**definition.get("automation", {}), **automation}

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
            member_values = [
                value for value in lines[1:]
                if value.casefold().rstrip(":") != lines[0].casefold().rstrip(":")
                and not _is_asap_list_metadata(value)
            ]
            # Some MicroStrategy prompts wrap the label, search box, and
            # member list in separate nested containers. Do not stop at the
            # metadata-only wrapper. Continue outward until at least one real
            # member is present.
            if member_values and (has_marker or not require_search_marker):
                return lines[1:500]
        return []

    def wait_for_popup_options(control, timeout_ms: int = 5_000) -> list[str]:
        """Collect leaf options from the active asynchronous popup."""
        selector = (
            "[role=option]:visible,option:visible,"
            "li.select2-results__option:visible,li.select2-result:visible"
        )
        collected = []
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since = None
        while time.monotonic() < deadline:
            current = []
            for root in _asap_owned_popup_roots(frame, control):
                current = list(dict.fromkeys([
                    *current, *_unique_visible_text(root.locator(selector), 500),
                ]))
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
            options = wait_for_popup_options(control)
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
                label_index = next(
                    (
                        index for index in range(marker_index - 1, -1, -1)
                        if not _is_asap_list_metadata(lines[index])
                    ),
                    -1,
                )
                if label_index < 0:
                    continue
                label = lines[label_index]
                control_type = "week" if "week" in label.casefold() else "multi_select"
                add_definition(label, control_type, lines[label_index + 1:500])
                break

    # Dimension pickers use a named member list but no ARIA roles. The adapter
    # recognizes the UI control label, while every option remains page-driven.
    for dimension_label in frame.get_by_text(re.compile(r"^dimension:?$", re.I)).all():
        add_definition("Dimension", "multi_select", nearest_list_values(dimension_label))

    # Regional FOTA renders Sell-out Country as a searchable visual member
    # list, not a heading, native select, or ARIA listbox. Anchor directly on
    # the live prompt label and keep its values page-driven. The dedicated path
    # makes discovery independent of whether the search/count marker is split
    # into a sibling container by the current MicroStrategy rendering.
    for country_label in frame.get_by_text(re.compile(r"^sell-out country:?$", re.I)).all():
        add_definition(
            "Sell-out Country", "multi_select",
            nearest_list_values(country_label, require_search_marker=True),
        )

    # Regional FOTA renders Category as the same visual-only member list used
    # by Dimension. It has no search/count marker or native select, so the
    # generic discovery paths above cannot see it. Capture the two live report
    # members explicitly; execution still resolves and verifies the visible
    # list before changing its exact selection.
    category_options = []
    for category_label in frame.get_by_text(re.compile(r"^category:?$", re.I)).all():
        for value in nearest_list_values(category_label):
            if value.casefold() in {"weekly", "daily"} and value not in category_options:
                category_options.append(value)
    if category_options:
        add_definition("Category", "multi_select", category_options)

    # Regional FOTA exposes Week and Date as coupled two-handle sliders rather
    # than searchable member lists. Catalog the complete Week domain while
    # recording that execution must also drive the derived Date range.
    week_slider = _asap_discover_week_slider(frame)
    if week_slider:
        options, automation = week_slider
        add_definition("Week", "week", options, automation)

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


def _asap_export_view_candidates(page: Page, frame: Frame) -> list[tuple[str, Any]]:
    """Return every visible export view without changing the report."""
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
    return visible


def _asap_activate_export_view(
    page: Page, frame: Frame, requested_label: str | None = None,
) -> tuple[Frame, str | None]:
    """Open one exact export-oriented view using visible ASAP semantics."""
    # The report shell exposes its tabs before the default report has finished
    # loading. Wait until the overlay is stably gone, then resolve the tab
    # again so the click cannot target a control from a replaced frame.
    _asap_wait_for_loading_clear(page)
    visible = _asap_export_view_candidates(page, _asap_frame(page))
    if not visible:
        if requested_label:
            raise RuntimeError(f"ASAP export view is no longer visible: {requested_label}")
        return frame, None
    if requested_label:
        selected = next(
            (item for item in visible if item[0].casefold() == requested_label.casefold()), None,
        )
        if selected is None:
            available = ", ".join(item[0] for item in visible)
            raise RuntimeError(
                f"ASAP export view is no longer visible: {requested_label}. Available: {available}"
            )
        label, control = selected
    else:
        label, control = next(
            (item for item in visible if "detail" in item[0].casefold()), visible[0],
        )
    control.click(timeout=30_000)
    page.wait_for_timeout(500)
    _asap_wait_for_loading_clear(page)
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
    failures = []
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
                export_view_labels = [
                    label for label, _control in _asap_export_view_candidates(page, frame)
                ]
            filters = []
            export_views = []
            ready_text = export_view_labels[0] if export_view_labels else None
            report_title = path[-1]
            with timings.measure("filter_inspection"):
                labels_to_scan = export_view_labels or [None]
                for view_index, export_view_label in enumerate(labels_to_scan):
                    if view_index:
                        frame = _asap_open_report(page, lightweight_job, profile_dir)
                    active_frame, selected_label = _asap_activate_export_view(
                        page, frame, export_view_label,
                    )
                    discovered = _asap_discover_filters(active_frame)
                    view_filter_keys = []
                    for definition in discovered:
                        view_filter_keys.append(definition["filter_key"])
                        existing = next(
                            (item for item in filters if item["filter_key"] == definition["filter_key"]),
                            None,
                        )
                        if existing is None:
                            filters.append(definition)
                        else:
                            existing["options"] = list(dict.fromkeys([
                                *existing.get("options", []), *definition.get("options", []),
                            ]))
                    if selected_label:
                        export_views.append({
                            "label": selected_label, "filter_keys": view_filter_keys,
                        })
                    if view_index == 0:
                        report_title = active_frame.locator("title").text_content() or path[-1]
            for position, definition in enumerate(filters):
                definition["position"] = position
            discovery_key = " > ".join(path)
            reports.append({
                "discovery_key": discovery_key, "name": path[-1],
                "report_url": site.get("base_url") or site.get("auth_url"),
                "ready_text": ready_text, "download_text": "Export CSV",
                "automation": {
                    "category_path": path, "report_tab": ready_text,
                    "report_title": report_title, "export_text": ready_text,
                    "export_views": export_views,
                },
                "filters": filters,
            })
        except Exception as exc:
            complete = False
            failures.append(f"{' > '.join(path)}: {exc}")
            timings.items.append({
                "phase": "report_inspection", "duration_ms": 0, "status": "failed",
                "metadata": {"path": path, "error": str(exc)},
            })
            _asap_goto(page, site.get("auth_url") or site.get("base_url"), profile_dir)
    if target_paths and not reports and failures:
        raise RuntimeError("ASAP targeted catalog scan failed. " + " | ".join(failures))
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


def _excel_cell_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return "" if value is None else value


def _validated_requested_weeks(period: Any) -> list[str]:
    """Return the exact contiguous week list supplied by the Metronome job."""
    if period is None:
        return []
    values = period if isinstance(period, list) else [period]
    weeks: list[str] = []
    mondays: list[date] = []
    for value in values:
        match = REQUESTED_WEEK.fullmatch(str(value).strip())
        if not match:
            raise RuntimeError(
                f"Metronome supplied an invalid requested week {value!r}; expected YYYY-Www."
            )
        year, week = (int(item) for item in match.groups())
        try:
            monday = date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            raise RuntimeError(
                f"Metronome supplied a nonexistent requested week: {year:04d}-W{week:02d}."
            ) from exc
        compact = f"{year:04d}{week:02d}"
        if compact in weeks:
            raise RuntimeError(f"Metronome supplied a duplicate requested week: {compact}.")
        weeks.append(compact)
        mondays.append(monday)
    if any(current - previous != timedelta(days=7) for previous, current in zip(mondays, mondays[1:])):
        raise RuntimeError(
            f"Metronome supplied requested weeks that are not ordered and contiguous: {weeks}."
        )
    return weeks


def _xlsx_expand_multi_week_metric_header(
    requested_weeks: list[str], header: list[str], data_rows: list[list[Any]],
    worksheet_title: str,
) -> tuple[list[str], list[str], str | None]:
    """Recover ASAP's multi-level week headings without guessing column count.

    In the live flat Excel matrix, the lower header row ends in ``Metrics``.
    Each selected week occupies one value column, but only the first value
    column has that lower-level label. The prior normalizer used the lower
    header width and silently truncated every later week. Metronome passes the
    complete in-memory period list that it set and read back in ASAP, so the
    physical value-column count must prove a one-to-one mapping to that list.
    """
    if len(requested_weeks) < 2:
        return header, [], None

    # The current Regional FOTA export can also arrive with a fully expanded
    # header: ``Weekly, 202630, 202631``. ``Weekly`` is not a dimension in the
    # file. It is a constant descriptor whose row value is ``Sell-out``. Keep
    # the explicit week columns, but remove that descriptor only when both the
    # requested period list and every populated descriptor value prove the
    # shape. This avoids teaching the downstream flow-specific script to
    # tolerate an ambiguous extra column.
    explicit_week_columns = [
        (index, str(value).strip()) for index, value in enumerate(header)
        if re.fullmatch(r"20\d{2}(?:0[1-9]|[1-4]\d|5[0-3])", str(value).strip())
    ]
    if explicit_week_columns:
        explicit_weeks = [value for _, value in explicit_week_columns]
        if explicit_weeks != requested_weeks:
            raise RuntimeError(
                "Downloaded multi-week Excel columns do not match Metronome's requested weeks "
                f"on sheet {worksheet_title!r}. Requested weeks: {requested_weeks}; explicit "
                f"week columns: {explicit_weeks}."
            )
        descriptor_indexes = [
            index for index, value in enumerate(header)
            if str(value).strip().casefold() in {"weekly", "metric", "metrics"}
        ]
        if len(descriptor_indexes) == 1:
            descriptor_index = descriptor_indexes[0]
            descriptor_labels = {
                re.sub(r"\W+", "_", str(row[descriptor_index]).strip()).strip("_").casefold()
                for row in data_rows
                if len(row) > descriptor_index and str(row[descriptor_index]).strip()
            }
            recognized_descriptor = (
                next(iter(descriptor_labels))
                if len(descriptor_labels) == 1 and descriptor_labels <= {"sell_out", "fota"}
                else None
            )
            if recognized_descriptor:
                for row in data_rows:
                    if len(row) > descriptor_index:
                        row.pop(descriptor_index)
                return (
                    [value for index, value in enumerate(header) if index != descriptor_index],
                    explicit_weeks,
                    recognized_descriptor,
                )
        return header, [], None

    metric_indexes = [
        index for index, value in enumerate(header)
        if str(value).strip().casefold() in {"metric", "metrics"}
    ]
    if len(metric_indexes) != 1 or metric_indexes[0] != len(header) - 1:
        return header, [], None
    metric_index = metric_indexes[0]
    max_data_width = max((len(row) for row in data_rows), default=0)
    metric_labels = {
        re.sub(r"\W+", "_", str(row[metric_index]).strip()).strip("_").casefold()
        for row in data_rows if len(row) > metric_index and str(row[metric_index]).strip()
    }
    recognized_metric_label = (
        next(iter(metric_labels))
        if len(metric_labels) == 1 and metric_labels <= {"sell_out", "fota"}
        else None
    )
    if recognized_metric_label:
        expected_width = metric_index + 1 + len(requested_weeks)
        if max_data_width != expected_width:
            actual_values = max(0, max_data_width - metric_index - 1)
            raise RuntimeError(
                "Downloaded multi-week Excel matrix does not contain one numeric value column "
                f"per requested week on sheet {worksheet_title!r}. Requested weeks: "
                f"{requested_weeks}; metric label: {recognized_metric_label!r}; expected numeric "
                f"week columns: {len(requested_weeks)}; observed numeric week columns: "
                f"{actual_values}."
            )
        for row in data_rows:
            if len(row) > metric_index:
                row.pop(metric_index)
        return [*header[:metric_index], *requested_weeks], requested_weeks, recognized_metric_label
    expected_width = metric_index + len(requested_weeks)
    if max_data_width == expected_width:
        return [*header[:metric_index], *requested_weeks], requested_weeks, None
    if max_data_width <= len(header):
        raise RuntimeError(
            "Downloaded multi-week Excel matrix exposes one Metrics column but no distinct "
            f"value column per requested week on sheet {worksheet_title!r}. Requested weeks: "
            f"{requested_weeks}; header width: {len(header)}; maximum data width: {max_data_width}."
        )
    raise RuntimeError(
        "Downloaded multi-week Excel matrix width does not match its requested weeks on sheet "
        f"{worksheet_title!r}. Requested weeks: {requested_weeks}; Metrics starts at column "
        f"{metric_index + 1}; expected width: {expected_width}; maximum data width: "
        f"{max_data_width}."
    )


def _normalize_xlsx(source: Path, output: Path, *, requested_weeks: list[str]) -> dict:
    """Convert populated workbook sheets into one normalized UTF-8 CSV."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel normalization requires openpyxl. Re-run setup.ps1.") from exc
    try:
        workbook = load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        raise RuntimeError(f"Downloaded Excel workbook could not be opened: {source.name}") from exc
    common_header: list[str] | None = None
    common_normalized: list[str] | None = None
    output_rows: list[list[Any]] = []
    source_sheets = []
    recovered_week_columns: list[str] = []
    removed_metric_label: str | None = None
    preamble_rows_removed = 0
    try:
        for worksheet in workbook.worksheets:
            rows = []
            for raw_row in worksheet.iter_rows(values_only=True):
                values = [_excel_cell_value(value) for value in raw_row]
                while values and str(values[-1]).strip() == "":
                    values.pop()
                if values:
                    rows.append(values)
            if not rows:
                continue
            header_candidates = []
            for index, row in enumerate(rows[:50]):
                populated = sum(bool(str(value).strip()) for value in row)
                if populated < 2:
                    continue
                candidate_names = [
                    re.sub(r"\W+", "_", str(value).strip()).strip("_").casefold()
                    or f"col_{column_index}"
                    for column_index, value in enumerate(row)
                ]
                # Dense data rows can be wider than the actual Excel header.
                # Repeated dimension values make those rows invalid column
                # definitions, so never let them outrank a unique header row.
                if len(candidate_names) != len(set(candidate_names)):
                    continue
                # Weekly exports expose their value column as a compact YYYYWW
                # label. Prefer a candidate containing that strong header
                # signal before falling back to density. Otherwise a wide data
                # row whose values happen to be unique can still win.
                has_year_week_header = any(
                    re.fullmatch(r"20\d{2}(?:0[1-9]|[1-4]\d|5[0-3])", str(value).strip())
                    for value in row
                )
                header_label_hits = len(set(candidate_names) & XLSX_HEADER_LABEL_HINTS)
                header_candidates.append(
                    (header_label_hits, has_year_week_header, populated, len(row), -index, index)
                )
            header_index = max(header_candidates)[-1] if header_candidates else None
            if header_index is None:
                continue
            header = [str(value).strip() for value in rows[header_index]]
            if len(header) < 2:
                continue
            data_rows = rows[header_index + 1:]
            header, sheet_week_columns, sheet_metric_label = _xlsx_expand_multi_week_metric_header(
                requested_weeks, header, data_rows, worksheet.title,
            )
            if sheet_metric_label:
                if removed_metric_label and removed_metric_label != sheet_metric_label:
                    raise RuntimeError(
                        "Downloaded Excel workbook exposed different metric labels across "
                        f"populated sheets: {removed_metric_label!r} and {sheet_metric_label!r}."
                    )
                removed_metric_label = sheet_metric_label
            if sheet_week_columns:
                if recovered_week_columns and recovered_week_columns != sheet_week_columns:
                    raise RuntimeError(
                        "Downloaded Excel workbook resolved different multi-week columns across "
                        f"its populated sheets: {recovered_week_columns} and {sheet_week_columns}."
                    )
                recovered_week_columns = sheet_week_columns
            normalized = [
                re.sub(r"\W+", "_", value).strip("_").casefold() or f"col_{index}"
                for index, value in enumerate(header)
            ]
            if common_header is None:
                common_header = header
                common_normalized = normalized
            elif normalized != common_normalized:
                raise RuntimeError(
                    "Downloaded Excel workbook has populated sheets with different columns: "
                    f"{', '.join(source_sheets)} and {worksheet.title}."
                )
            width = len(common_header)
            for row_number, row in enumerate(data_rows, start=header_index + 2):
                if len(row) > width and any(
                    str(value).strip() for value in row[width:]
                ):
                    raise RuntimeError(
                        "Downloaded Excel row contains populated cells beyond the resolved header "
                        f"on sheet {worksheet.title!r}, row {row_number}. Header width: {width}; "
                        f"row width: {len(row)}. Refusing to discard data."
                    )
                values = list(row[:width]) + [""] * max(0, width - len(row))
                if any(str(value).strip() for value in values):
                    output_rows.append(values)
            source_sheets.append(worksheet.title)
            preamble_rows_removed += header_index
    finally:
        workbook.close()
    if common_header is None or not output_rows:
        raise RuntimeError("Downloaded Excel workbook did not contain a usable table with data rows.")
    with output.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(common_header)
        writer.writerows(output_rows)
    return {
        "preamble_rows_removed": preamble_rows_removed,
        "source_encoding": "xlsx",
        "source_delimiter": None,
        "source_sheets": source_sheets,
        "columns": common_header,
        "recovered_week_columns": recovered_week_columns,
        "removed_metric_label": removed_metric_label,
    }


def _add_export_view_column(path: Path, export_view: str | None) -> dict:
    """Add durable source lineage to every artifact in a multi-export bundle."""
    if not export_view:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise RuntimeError(f"Normalized artifact is empty: {path.name}")
    lineage_name = "Metronome Export View"
    normalized = {
        re.sub(r"\W+", "_", str(value).strip()).strip("_").casefold()
        for value in rows[0]
    }
    if "metronome_export_view" in normalized:
        raise RuntimeError(
            f"Downloaded artifact already contains the reserved column {lineage_name}."
        )
    temporary = path.with_name(f".{path.name}.lineage.tmp")
    with temporary.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([*rows[0], lineage_name])
        for row in rows[1:]:
            writer.writerow([*row, export_view])
    temporary.replace(path)
    return {"lineage_column": lineage_name, "export_view": export_view}


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
            "METRONOME_FLOW_PERIODS": json.dumps(artifact.get("period_key") or []),
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


def _store_completed_download(
    local_path: Path, output: Path, *, file_format: str = "csv",
    export_view: str | None = None, requested_period: Any = None,
) -> dict:
    """Normalize the browser-local file, then copy it to the final target.

    Passing a UNC path to Playwright's ``download.save_as`` and asking for
    ``download.path`` can both hang after Edge visibly finishes. Monitor a
    known local download folder instead, then copy the complete file to the
    final target in exclusive-create mode so nothing can be overwritten.
    """
    local_path = Path(local_path)
    file_format = str(file_format or "csv").casefold()
    if file_format == "xlsx":
        original_size = local_path.stat().st_size
        if original_size <= 0:
            raise RuntimeError("The downloaded Excel workbook is empty.")
        with local_path.open("rb") as source, output.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
        if output.stat().st_size != original_size:
            raise RuntimeError(f"Downloaded Excel workbook was not copied completely to {output}.")
        normalized_output = _safe_output_path(
            output.parent, f"{output.stem}_normalized.csv",
        )
        requested_weeks = _validated_requested_weeks(requested_period)
        normalization = _normalize_xlsx(
            output, normalized_output, requested_weeks=requested_weeks,
        )
        lineage = _add_export_view_column(normalized_output, export_view)
        metadata = {**_csv_metadata(normalized_output), **normalization, **lineage}
        return {
            **metadata,
            "file_path": str(normalized_output),
            "filename": normalized_output.name,
            "original_file_path": str(output),
            "original_filename": output.name,
            "original_file_size": original_size,
        }
    if file_format != "csv":
        raise RuntimeError(f"Unsupported downloaded file format: {file_format}")
    normalization = _normalize_csv(local_path)
    lineage = _add_export_view_column(local_path, export_view)
    metadata = {**_csv_metadata(local_path), **normalization, **lineage}
    with local_path.open("rb") as source, output.open("xb") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    if output.stat().st_size != metadata["file_size"]:
        raise RuntimeError(f"Downloaded CSV was not copied completely to {output}.")
    return {**metadata, "file_path": str(output), "filename": output.name}


def _asap_filters_for_export_view(job: dict, export_view: str | None) -> list[dict]:
    """Limit configuration to controls catalogued for the selected export view."""
    definitions = list(job.get("report", {}).get("filters") or [])
    if not export_view:
        return definitions
    catalog_views = (job.get("report", {}).get("automation") or {}).get("export_views") or []
    catalog_view = next(
        (
            item for item in catalog_views
            if isinstance(item, dict)
            and str(item.get("label") or "").casefold() == export_view.casefold()
        ),
        None,
    )
    if catalog_view is None or "filter_keys" not in catalog_view:
        return definitions
    allowed = set(catalog_view.get("filter_keys") or [])
    return [item for item in definitions if item.get("filter_key") in allowed]


def _has_named_control(page: Page | Frame, text: str) -> bool:
    for locator in (
        page.get_by_role("button", name=text, exact=True),
        page.get_by_role("link", name=text, exact=True),
        page.get_by_text(text, exact=True),
    ):
        try:
            if locator.count() and locator.first.is_visible():
                return True
        except Exception:
            continue
    return False


def execute_job(
    page: Page, job: dict, report_progress, profile_dir: Path,
    download_staging_dir: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    timings = _Timings()
    report_progress("running", {"stage": "opening_report", "message": "Opening the configured report."})
    is_asap = job["site"].get("adapter") == ASAP_PORTAL_ADAPTER
    ready_text = job["report"].get("ready_text")
    open_export = job["report"].get("open_export_text")

    target = Path(job["downloads"]["target_folder"])
    if not target.is_dir():
        raise RuntimeError(f"Target folder does not exist: {target}")

    periods = job["downloads"].get("periods") or [None]
    export_views = job.get("report", {}).get("export_views") or [None]
    tasks = [
        {"period": period, "export_view": export_view}
        for export_view in export_views for period in periods
    ]
    artifacts = []
    for index, task in enumerate(tasks, start=1):
        period = task["period"]
        export_view = task["export_view"]
        with timings.measure("navigation", report_id=job["report"].get("id")):
            if is_asap:
                frame = _asap_open_report(page, job, profile_dir)
                frame, selected_view = _asap_activate_export_view(page, frame, export_view)
                if export_view and selected_view != export_view:
                    raise RuntimeError(
                        f"ASAP activated the wrong export view. Requested: {export_view}. "
                        f"Activated: {selected_view}."
                    )
            else:
                page.goto(job["report"]["url"], wait_until="domcontentloaded", timeout=120_000)
                frame = None
                if ready_text:
                    page.get_by_text(ready_text, exact=False).first.wait_for(
                        state="visible", timeout=120_000,
                    )
                if open_export:
                    _click_named(page, open_export)
        report_progress(
            "running",
            {
                "stage": "configuring",
                "message": f"Configuring export {index} of {len(tasks)}: {export_view or job['report']['name']}.",
                "period": period, "export_view": export_view,
                "item_index": index, "item_count": len(tasks),
            },
            artifacts,
        )
        if is_asap:
            view_job = {
                **job,
                "report": {
                    **job["report"],
                    "filters": _asap_filters_for_export_view(job, export_view),
                },
            }
            with timings.measure("configuration", report_id=job["report"].get("id")):
                _asap_apply_configuration(frame, view_job, period)
            if _has_named_control(frame, "RUN"):
                report_progress(
                    "running",
                    {
                        "stage": "report_execution",
                        "message": f"Running export {index} of {len(tasks)}: {export_view or job['report']['name']}.",
                        "period": period, "export_view": export_view,
                    },
                    artifacts,
                )
                with timings.measure("report_execution", report_id=job["report"].get("id")):
                    _asap_wait_for_loading_clear(page)
                    frame = _asap_frame(page)
                    _click_named(frame, "RUN")
                    page.wait_for_timeout(1_000)
                    frame = _asap_wait_for_results(page)
            report_progress(
                "running",
                {
                    "stage": "file_export",
                    "message": f"Exporting {job['downloads'].get('file_format', 'csv').upper()} {index} of {len(tasks)}: {export_view or job['report']['name']}.",
                    "period": period, "export_view": export_view,
                },
                artifacts,
            )
            with timings.measure("file_export", report_id=job["report"].get("id")):
                staged_file, export_pages = _asap_download_with_retry(
                    page, frame, job, download_staging_dir or profile_dir / "downloads",
                )
        else:
            _apply_configuration(page, job, period)
            with page.expect_download(timeout=180_000) as pending:
                _click_named(page, job["report"]["download_text"])
            download = pending.value
            export_pages = []
        filename = _render_filename(
            job["downloads"]["filename_template"], job, period, index, export_view,
        )
        output = _safe_output_path(target, filename)
        with timings.measure("file_transfer", report_id=job["report"].get("id")):
            # ASAP closes its own export wizard after emitting the download.
            # Do not query or close that vanished page object: Edge can leave
            # it half-detached and any later Playwright call can block forever.
            # The next period reopens the report in the surviving main page.
            if is_asap:
                metadata = _store_completed_download(
                    staged_file, output,
                    file_format=job["downloads"].get("file_format") or "csv",
                    export_view=export_view,
                    requested_period=period,
                )
            else:
                download.save_as(output)
                normalization = _normalize_csv(output)
                lineage = _add_export_view_column(output, export_view)
                metadata = {
                    **_csv_metadata(output), **normalization, **lineage,
                    "file_path": str(output), "filename": output.name,
                }
        artifacts.append({
            "period_key": period,
            "export_view": export_view,
            "bundle_index": index,
            "bundle_count": len(tasks),
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
            download_staging_dir = profile_dir / "downloads"
            download_staging_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel="msedge" if os.name == "nt" else None,
                headless=not headed,
                accept_downloads=True,
                downloads_path=str(download_staging_dir),
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
                sql_result = None

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
                    sql_only = run["job"].get("job_type") == "sql_retry"
                    if sql_only:
                        if not run["job"].get("sql_handoff", {}).get("enabled"):
                            raise RuntimeError("SQL-only retry has no enabled SQL target.")
                        sql_artifacts = run["job"].get("sql_retry", {}).get("artifacts") or []
                        if not sql_artifacts:
                            raise RuntimeError("SQL-only retry has no saved CSV artifacts.")
                        artifacts = sql_artifacts
                        timings = [{"phase": "total", "duration_ms": 0, "status": "running"}]
                        progress(
                            "running",
                            {
                                "stage": "sql_retry",
                                "message": (
                                    f"Reusing {len(sql_artifacts)} saved CSV file(s) from run "
                                    f"#{run['job'].get('sql_retry', {}).get('source_run_id')}. "
                                    "ASAP will not be opened or downloaded again."
                                ),
                            },
                            artifacts, timings,
                        )
                    else:
                        artifacts, timings = execute_job(
                            page, run["job"], progress, profile_dir, download_staging_dir,
                        )
                        sql_artifacts = artifacts
                    if not sql_only and run["job"].get("transformation", {}).get("enabled"):
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
                        if sql_only:
                            source_label = "saved"
                        progress("running", {"stage": "sql_insertion", "message": f"Loading {source_label} files into SQL."}, artifacts, timings)
                        sql_started = time.perf_counter()

                        def sql_progress(detail: dict):
                            progress("running", detail, artifacts, timings)

                        sql_result = load_artifacts(
                            sql_artifacts, run["job"]["sql_handoff"], progress=sql_progress,
                        )
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
                                f"SQL-only retry committed {sql_result['rows_written']} row(s) from {sql_result['files_loaded']} saved file(s)."
                                if sql_only
                                else f"Saved the full {len(sql_artifacts)}-export bundle and committed {sql_result['rows_written']} row(s) to {sql_result['target']}."
                                if sql_result is not None
                                else f"Saved {len(sql_artifacts)} transformed CSV file(s) after {len(artifacts) - len(sql_artifacts)} download(s)."
                                if run["job"].get("transformation", {}).get("enabled")
                                else f"Saved {len(artifacts)} {str(run['job'].get('downloads', {}).get('file_format') or 'csv').upper()} export(s)."
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
                    failure_message = str(exc)
                    failure_detail = {"stage": "failed", "message": failure_message}
                    failure_traceback = traceback.format_exc()
                    try:
                        progress(
                            "failed", failure_detail,
                            artifacts=artifacts, timings=timings,
                            error=failure_message, traceback_text=failure_traceback,
                        )
                    except Exception as report_exc:
                        # A rich terminal payload must never leave the run in
                        # ``running`` if one optional diagnostic field is
                        # rejected or a response is lost. A minimal idempotent
                        # terminal update gives the server a second, smaller
                        # chance to close the run. If the first request already
                        # committed, the endpoint returns the terminal state.
                        fallback_message = (
                            f"{failure_message} Terminal diagnostic reporting initially failed: "
                            f"{type(report_exc).__name__}: {report_exc}"
                        )[:10000]
                        print(fallback_message, file=sys.stderr, flush=True)
                        _api(
                            client, "POST",
                            f"/api/flows/worker/{worker_id}/runs/{run_id}/progress",
                            {
                                "status": "failed",
                                "progress": {
                                    "stage": "failed",
                                    "message": fallback_message,
                                    "terminal_report_fallback": True,
                                },
                                "artifacts": [],
                                "timings": [],
                                "error": fallback_message,
                                "traceback": None,
                            },
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
