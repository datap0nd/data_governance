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
import sys
import time
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
    response = client.request(method, path, json=body, timeout=60)
    response.raise_for_status()
    return response.json()


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


def _render_filename(template: str, job: dict, period: str | None, index: int) -> str:
    week = period or job["downloads"].get("periods", [None])[0] or ""
    year, week_number = (week.split("-W", 1) + [""])[:2] if week else ("", "")
    values = {
        "flow": job["flow"]["name"],
        "report": job["report"]["name"],
        "week": week,
        "year": year,
        "week_number": week_number,
        "index": str(index),
        "date": date.today().isoformat(),
    }
    name = template
    for key, value in values.items():
        name = name.replace("{" + key + "}", str(value))
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name).strip(" .")
    if not name.casefold().endswith(".csv"):
        name += ".csv"
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
        option.first.click(modifiers=["Control"] if control_type == "multi_select" else [])
        if control_type == "multi_select" and index < len(values) - 1:
            trigger.click()


def _apply_configuration(page: Page, job: dict, period: str | None):
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


def _asap_select_list_values(frame: Frame, label: str, values: list[str]):
    heading = frame.get_by_text(label, exact=True).first
    heading.wait_for(state="visible", timeout=60_000)
    for value in values:
        candidates = frame.get_by_text(value, exact=True)
        option = next(
            (candidates.nth(index) for index in range(candidates.count()) if candidates.nth(index).is_visible()),
            None,
        )
        if option is None:
            raise RuntimeError(f"Could not find {label} option: {value}")
        option.click(modifiers=["Control"] if len(values) > 1 else [])


def _asap_apply_configuration(frame: Frame, job: dict, period: str | None):
    selections = dict(job.get("selections") or {})
    for definition in job["report"].get("filters", []):
        key = definition["filter_key"]
        value = period if definition["control_type"] == "week" and period else selections.get(key)
        if value in (None, "", []):
            continue
        values = value if isinstance(value, list) else [value]
        values = [_week_to_asap(str(item)) if definition["control_type"] == "week" else str(item) for item in values]
        if definition["control_type"] == "select":
            _set_filter(frame, definition, values[0])
        else:
            _asap_select_list_values(frame, definition["control_label"], values)


def _asap_download(page: Page, frame: Frame, job: dict):
    automation = job["report"].get("automation") or {}
    export_text = automation.get("export_text") or automation.get("report_tab") or "Export Wizard (Detail)"
    export_control = None
    for root in reversed(page.frames):
        for locator in (
            root.get_by_role("button", name=export_text, exact=True),
            root.get_by_role("link", name=export_text, exact=True),
            root.get_by_text(export_text, exact=True),
        ):
            try:
                if locator.count() and locator.first.is_visible():
                    export_control = locator.first
                    break
            except Exception:
                continue
        if export_control is not None:
            break
    if export_control is None:
        raise RuntimeError(f"Could not find visible ASAP export control: {export_text}")
    pages_before = set(page.context.pages)
    export_control.click()
    # ASAP sometimes opens the wizard as a page and sometimes as a modal/frame
    # in the existing page. Search both shapes instead of requiring a popup.
    csv_option = None
    export_action = None
    download_page = page
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and (csv_option is None or export_action is None):
        current_pages = page.context.pages
        popup = next((candidate for candidate in current_pages if candidate not in pages_before), None)
        roots = [root for candidate in reversed(current_pages) for root in [candidate, *reversed(candidate.frames)]]
        for root in roots:
            for locator in (
                root.get_by_label("CSV file format", exact=True),
                root.get_by_text(re.compile(r"^CSV(?: file format)?$", re.I)),
            ):
                try:
                    if locator.count() and locator.first.is_visible():
                        csv_option = locator.first
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
                        download_page = popup or (root if isinstance(root, Page) else root.page)
                        break
                except Exception:
                    continue
        if csv_option is None or export_action is None:
            page.wait_for_timeout(250)
    if csv_option is None or export_action is None:
        raise RuntimeError("ASAP Export Wizard opened, but its CSV option or Export action was not recognized.")
    try:
        csv_option.check()
    except Exception:
        csv_option.click()
    with download_page.expect_download(timeout=180_000) as pending:
        export_action.click()
    return pending.value


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
        and len(options) >= 2
        and len([part for part in label.split(" - ") if part.strip()]) == 3
        and all(len([part for part in value.split(" - ") if part.strip()]) == 3 for value in options)
    ):
        return "Data Configuration"
    return label


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
    used = set()

    def add_definition(label: str, control_type: str, options: list[str]):
        label = _clean_text(label).rstrip(":")
        options = [
            value for value in dict.fromkeys(_clean_text(value) for value in options)
            if value and value != label and not re.fullmatch(r"\(all\)(?:\s*\(\d+\s+values?\))?", value, re.I)
            and "type to search" not in value.casefold()
        ]
        # MicroStrategy's Data Configuration combobox has no accessible name
        # in the current ASAP UI. Its nearest text is the selected value, so
        # treating that value as the label makes the creator misleading and
        # leaves execution unable to find the control. The portal documents
        # these choices as three region positions separated by hyphens.
        label = _normalize_asap_filter_label(label, control_type, options)
        if not label or not options:
            return
        key = _slug_key(label, f"filter_{len(definitions) + 1}")
        if key in used:
            return
        used.add(key)
        definitions.append({
            "filter_key": key, "label": label, "control_label": label,
            "control_type": control_type, "options": options, "automation": {},
            "required": False, "position": len(definitions),
        })

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
    # Native controls are preferred because they expose complete option lists
    # without opening the control or changing report state.
    for index, control in enumerate(frame.locator("select:visible").all()):
        control_id = control.get_attribute("id") or ""
        label = ""
        if control_id:
            label_locator = frame.locator(f'label[for="{control_id}"]')
            if label_locator.count():
                label = re.sub(r"\s+", " ", label_locator.first.inner_text()).strip()
        if not label:
            aria = control.get_attribute("aria-label") or control.get_attribute("name") or ""
            label = re.sub(r"\s+", " ", aria).strip()
        if not label:
            continue
        options = _unique_visible_text(control.locator("option"))
        add_definition(label, "select", options)

    # New ASAP renders some selects as ARIA comboboxes. Open them only long
    # enough to read their visible option labels, then restore the page with
    # Escape. No selection is changed.
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
            frame.page.wait_for_timeout(150)
            options = _unique_visible_text(frame.locator("[role=option]:visible,li:visible"), 500)
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
                _asap_apply_configuration(frame, job, period)
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
            report_progress(
                "running",
                {"stage": "csv_export", "message": f"Exporting CSV {index} of {len(periods)}.", "period": period},
                artifacts,
            )
            with timings.measure("csv_export", report_id=job["report"].get("id")):
                download = _asap_download(page, frame, job)
        else:
            _apply_configuration(page, job, period)
            with page.expect_download(timeout=180_000) as pending:
                _click_named(page, job["report"]["download_text"])
            download = pending.value
        filename = _render_filename(job["downloads"]["filename_template"], job, period, index)
        output = _safe_output_path(target, filename)
        with timings.measure("file_transfer", report_id=job["report"].get("id")):
            download.save_as(output)
            metadata = _csv_metadata(output)
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
            "capabilities": {"adapters": ["web_export", ASAP_PORTAL_ADAPTER], "headed": headed, "delete_existing": False, "overwrite_existing": False},
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

                def progress(status: str, detail: dict, artifacts: list | None = None,
                             timings: list | None = None, error: str | None = None):
                    _api(client, "POST", f"/api/flows/worker/{worker_id}/runs/{run_id}/progress", {
                        "status": status, "progress": detail, "artifacts": artifacts or [],
                        "timings": timings or [], "error": error,
                    })

                try:
                    artifacts, timings = execute_job(page, run["job"], progress, profile_dir)
                    progress(
                        "succeeded", {"stage": "complete", "message": f"Saved {len(artifacts)} CSV file(s)."},
                        artifacts, timings,
                    )
                except Exception as exc:
                    progress(
                        "failed", {"stage": "failed", "message": str(exc)},
                        timings=[{"phase": "total", "duration_ms": round((time.perf_counter() - run_started) * 1000), "status": "failed"}],
                        error=str(exc),
                    )
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
