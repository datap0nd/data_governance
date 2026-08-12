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
from playwright.sync_api import Page, sync_playwright


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


def _click_named(page: Page, text: str):
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


def _set_filter(page: Page, definition: dict, value: Any):
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
    page.goto(job["report"]["url"], wait_until="domcontentloaded", timeout=120_000)
    ready_text = job["report"].get("ready_text")
    if ready_text:
        page.get_by_text(ready_text, exact=False).first.wait_for(state="visible", timeout=120_000)
    open_export = job["report"].get("open_export_text")
    if open_export:
        _click_named(page, open_export)

    target = Path(job["downloads"]["target_folder"])
    if not target.is_dir():
        raise RuntimeError(f"Target folder does not exist: {target}")

    periods = job["downloads"].get("periods") or [None]
    artifacts = []
    for index, period in enumerate(periods, start=1):
        if index > 1:
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
            "capabilities": {"adapters": ["web_export"], "headed": headed, "delete_existing": False, "overwrite_existing": False},
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
