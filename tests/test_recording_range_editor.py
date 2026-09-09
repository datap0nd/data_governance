"""Synthetic browser coverage for the semantic week-range authoring journey."""
from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.flow_recording import validate_definition


ROOT = Path(__file__).resolve().parents[1]


def test_review_converts_recorded_week_clicks_to_one_range_step():
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
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(
                f"http://127.0.0.1:{server.server_port}/static/recording-preview/range.html"
            )
            page.get_by_role("button", name='Click “2026-W33”', exact=True).click()
            page.get_by_role("button", name="This is a range", exact=True).click()

            assert page.get_by_text("Range", exact=True).is_visible()
            assert page.get_by_label("Start week").input_value() == "2026-W32"
            assert page.get_by_label("End").input_value() == "Newest selectable week"
            page.get_by_label("Element box").select_option("2")
            page.get_by_role("button", name="Save draft", exact=True).click()
            page.get_by_text("Draft saved", exact=True).wait_for()
            definition = page.evaluate("()=>rangePreview.data.revisions[0].definition")
            validate_definition(definition)
            assert definition["version"] == 3
            assert [step["action"] for step in definition["steps"]] == [
                "goto", "select_range", "download",
            ]
            contract = definition["steps"][1]["range"]
            assert contract["start"] == "2026-W32"
            assert contract["end"] == "latest_selectable"
            assert contract["container_ancestor_levels"] == 2

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
