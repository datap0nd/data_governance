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
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


ASAP_FRAME_SELECTOR = "iframe#content-frame"
ASAP_PORTAL_ADAPTER = "asap_portal"


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


def _asap_open_report(page: Page, job: dict) -> Frame:
    report = job["report"]
    automation = report.get("automation") or {}
    path = automation.get("category_path") or []
    if len(path) < 2:
        raise RuntimeError("ASAP reports need a category path with a menu and report name.")
    page.goto(job["site"].get("auth_url") or report["url"], wait_until="domcontentloaded", timeout=120_000)
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
        option = heading.locator("xpath=following::*").filter(has_text=value).first
        if not option.count():
            raise RuntimeError(f"Could not find {label} option: {value}")
        option.first.click(modifiers=["Control"] if len(values) > 1 else [])


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
    export_selector = automation.get("export_selector", "button.report-export")
    with page.expect_popup(timeout=60_000) as popup_info:
        frame.locator(export_selector).first.click()
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded", timeout=60_000)
    popup.get_by_label("CSV file format", exact=True).check()
    with popup.expect_download(timeout=180_000) as pending:
        popup.get_by_role("button", name="Export", exact=True).click()
    return pending.value


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


def execute_job(page: Page, job: dict, report_progress) -> list[dict]:
    report_progress("running", {"stage": "opening_report", "message": "Opening the configured report."})
    is_asap = job["site"].get("adapter") == ASAP_PORTAL_ADAPTER
    frame = _asap_open_report(page, job) if is_asap else None
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
                frame = _asap_open_report(page, job)
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
            _asap_apply_configuration(frame, job, period)
            with page.expect_response(
                lambda response: "promptanswerm.do" in response.url,
                timeout=180_000,
            ):
                _click_named(frame, "RUN")
            frame.get_by_text("Data rows:", exact=False).first.wait_for(state="visible", timeout=180_000)
            download = _asap_download(page, frame, job)
        else:
            _apply_configuration(page, job, period)
            with page.expect_download(timeout=180_000) as pending:
                _click_named(page, job["report"]["download_text"])
            download = pending.value
        filename = _render_filename(job["downloads"]["filename_template"], job, period, index)
        output = _safe_output_path(target, filename)
        download.save_as(output)
        metadata = _csv_metadata(output)
        artifacts.append({
            "period_key": period,
            "file_path": str(output),
            "filename": output.name,
            "status": "saved",
            **metadata,
        })
    return artifacts


def run_worker(server: str, worker_id: str, display_name: str, profile_dir: Path, headed: bool, once: bool):
    with httpx.Client(base_url=server.rstrip("/"), headers={"User-Agent": "Metronome-Flow-Worker/1"}) as client:
        _api(client, "POST", "/api/flows/worker/register", {
            "worker_id": worker_id,
            "display_name": display_name,
            "capabilities": {"adapters": ["web_export", ASAP_PORTAL_ADAPTER], "headed": headed, "delete_existing": False, "overwrite_existing": False},
        })
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel="msedge" if os.name == "nt" else None,
                headless=not headed,
                accept_downloads=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            while True:
                claimed = _api(client, "POST", f"/api/flows/worker/{worker_id}/claim")
                run = claimed.get("run")
                if not run:
                    if once:
                        break
                    time.sleep(10)
                    continue
                run_id = run["id"]

                def progress(status: str, detail: dict, artifacts: list | None = None, error: str | None = None):
                    _api(client, "POST", f"/api/flows/worker/{worker_id}/runs/{run_id}/progress", {
                        "status": status, "progress": detail, "artifacts": artifacts or [], "error": error,
                    })

                try:
                    artifacts = execute_job(page, run["job"], progress)
                    progress("succeeded", {"stage": "complete", "message": f"Saved {len(artifacts)} CSV file(s)."}, artifacts)
                except Exception as exc:
                    progress("failed", {"stage": "failed", "message": str(exc)}, error=str(exc))
                if once:
                    break
            context.close()


def main():
    parser = argparse.ArgumentParser(description="Metronome authenticated download worker")
    parser.add_argument("--server", default=os.environ.get("METRONOME_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--worker-id", default=os.environ.get("METRONOME_FLOW_WORKER_ID", socket.gethostname().lower()))
    parser.add_argument("--name", default=os.environ.get("METRONOME_FLOW_WORKER_NAME", socket.gethostname()))
    parser.add_argument("--profile-dir", default=os.environ.get("METRONOME_FLOW_PROFILE", str(Path.home() / ".metronome-flow-browser")))
    parser.add_argument("--headed", action="store_true", help="Show the browser. Recommended for initial SSO setup.")
    parser.add_argument("--once", action="store_true", help="Claim at most one run, then exit.")
    args = parser.parse_args()
    run_worker(args.server, args.worker_id, args.name, Path(args.profile_dir), args.headed, args.once)


if __name__ == "__main__":
    main()
