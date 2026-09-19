"""Synthetic browser coverage for the date range control authoring journey."""
from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.flow_recording import validate_definition
from test_python_scripts_preview import launch


ROOT = Path(__file__).resolve().parents[1]


def test_advanced_setting_converts_one_recorded_handle_click_to_a_date_range_control():
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(QuietHandler, directory=str(ROOT / "app")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = launch(playwright)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(f"http://127.0.0.1:{server.server_port}/static/recording-preview/slider.html")
            page.get_by_role("button", name="Click “Week end”", exact=True).click()
            page.get_by_text("Advanced", exact=True).click()
            setting = page.get_by_label("This is a date range control", exact=True)
            assert setting.is_enabled()
            assert page.get_by_label("This is a range step", exact=True).is_enabled()
            setting.check()

            assert page.get_by_text("Date range", exact=True).is_visible()
            assert page.get_by_role("button", name="Set week range", exact=True).is_visible()
            assert page.get_by_label("This is a range step", exact=True).is_disabled()
            assert page.get_by_label("Start behavior").input_value() == "portal_default"
            assert page.get_by_label("End behavior").input_value() == "latest_selectable"
            assert page.get_by_label("Start parameter name").input_value() == "start"
            assert page.get_by_label("End parameter name").input_value() == "end"
            assert page.get_by_label("Weeks to add to the end").input_value() == "0"
            assert page.get_by_label("Week starts on").input_value() == "sunday"
            assert page.get_by_label("Element box").input_value() == "0"

            page.get_by_label("Start behavior").select_option("fixed")
            start_week = page.get_by_label("Start week")
            start_week.fill("2026-W1")
            start_week.press("Tab")
            assert "ISO week" in page.locator("[data-error]").inner_text()
            start_week.fill("2026-W01")
            start_week.press("Tab")
            assert page.locator("[data-error]").inner_text() == ""
            page.get_by_label("Weeks to add to the end").fill("-1")
            page.get_by_label("Weeks to add to the end").press("Tab")
            page.get_by_label("Element box").select_option("2")
            page.get_by_label("Week starts on").select_option("monday")
            page.get_by_label("Control values").select_option("date")
            assert page.get_by_label("Week starts on").input_value() == "monday"
            page.get_by_label("Start format").select_option("%G%V")
            assert page.get_by_label("Start week").input_value() == "202601"
            page.get_by_label("End parameter name").fill("last_week")
            page.get_by_label("End parameter name").press("Tab")
            assert page.get_by_label("End parameter name").input_value() == "last_week"
            page.get_by_label("Start parameter name").fill("last_week")
            page.get_by_label("Start parameter name").press("Tab")
            assert "unique" in page.locator("[data-error]").inner_text()

            # Unchecking restores the recorded click and drops the parameters; checking starts over.
            page.get_by_label("This is a date range control", exact=True).uncheck()
            assert page.get_by_role("button", name="Click “Week end”", exact=True).is_visible()
            assert page.get_by_label("This is a range step", exact=True).is_enabled()
            page.get_by_label("This is a date range control", exact=True).check()
            assert page.get_by_label("Start behavior").input_value() == "portal_default"
            assert page.get_by_label("Element box").input_value() == "0"
            page.get_by_label("Control values").select_option("month")
            assert page.get_by_role("button", name="Set month range", exact=True).is_visible()
            assert page.get_by_text("Month range", exact=True).is_visible()
            assert page.get_by_label("Start behavior").input_value() == "oldest_selectable"
            assert page.get_by_label("End behavior").input_value() == "latest_selectable"
            assert page.get_by_label("Months to add to the start").input_value() == "0"
            assert page.get_by_label("Months to add to the end").input_value() == "0"
            assert page.get_by_label("Start format").input_value() == "%Y%m"
            assert page.get_by_label("Week starts on").count() == 0
            page.get_by_role("button", name="Save draft", exact=True).click()
            page.get_by_text("Draft saved", exact=True).wait_for()
            definition = page.evaluate("()=>sliderPreview.data.revisions[0].definition")
            validate_definition(definition)
            assert definition["version"] == 5
            assert [step["action"] for step in definition["steps"]] == ["goto", "set_range", "download"]
            contract = definition["steps"][1]["range"]
            assert contract["kind"] == "month"
            assert "week_days" not in contract
            assert contract["container_ancestor_levels"] == 0
            assert contract["source_step"]["action"] == "click"
            assert definition["parameters"]["start"] == {
                "step_id": "week-handle", "role": "start", "unit": "month", "mode": "calculated",
                "expression": "oldest_selectable", "offset_months": 0, "format": "%Y%m"}
            assert definition["parameters"]["end"] == {
                "step_id": "week-handle", "role": "end", "unit": "month", "mode": "calculated",
                "expression": "latest_selectable", "offset_months": 0, "format": "%Y%m"}

            page.get_by_role("button", name="Open asap.example.test", exact=True).click()
            assert page.get_by_label("This is a date range control", exact=True).is_disabled()
            assert page.get_by_text("A date range control needs an element target.", exact=True).is_visible()

            page.get_by_role("button", name="Set month range", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
