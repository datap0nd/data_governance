"""Clickable fictional preview: several schedule triggers on one Flow.

The preview replaces only the API layer: production app.js renders the
builders and the Flows list. These checks walk every changed control,
including the validation failure and its recovery, on desktop and narrow
screens. They are synthetic browser evidence, not live worker, Outlook or
scheduler verification.
"""
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def launch_candidates():
    """Chrome channel first, then Playwright's own Chromium, then any Chromium build in the browsers folder."""
    yield {"channel": "chrome"}
    yield {}
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers and Path(browsers).is_dir():
        # The headless shell comes first: the full browser refuses a long TMPDIR such as
        # the per-run temporary root tools/check.py sets (its singleton socket path overflows).
        for pattern in ("chromium_headless_shell-*/chrome-linux/headless_shell", "chromium-*/chrome-linux/chrome"):
            for executable in sorted(Path(browsers).glob(pattern), reverse=True):
                yield {"executable_path": str(executable)}


def launch(pw):
    """Try each candidate browser in turn; skip cleanly when none launches."""
    failures = []
    for kwargs in launch_candidates():
        try:
            return pw.chromium.launch(headless=True, **kwargs)
        except PlaywrightError as error:
            failures.append(str(error).splitlines()[0])
    pytest.skip("No Chrome or Chromium browser can launch here: " + " | ".join(failures))


@pytest.fixture
def preview(tmp_path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "app")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence = Path(os.environ.get("PREVIEW_EVIDENCE_DIR") or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = launch(pw)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/static/recording-preview/flow-triggers.html")
            page.locator("#flow-builder-form").wait_for()
            yield page, evidence, errors
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def shot(page, evidence, name):
    page.screenshot(path=str(evidence / f"flow-triggers-{name}.png"), full_page=True)


def no_overflow(page):
    overflow = page.evaluate("""() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        elements: [...document.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > innerWidth + 1)
            .slice(0, 12).map(el => ({tag: el.tagName, id: el.id, className: typeof el.className === 'string' ? el.className : '', right: Math.round(el.getBoundingClientRect().right)}))})""")
    assert overflow["scrollWidth"] <= overflow["width"], overflow


def open_schedule(page):
    body = page.locator("#flow-step-body-schedule")
    if not body.is_visible():
        page.locator("#flow-step-toggle-schedule").click()
    expect(body).to_be_visible()


def payload(page):
    return page.evaluate("window.previewPayload")


def test_triggers_add_remove_save_fail_and_recover(preview):
    page, evidence, _ = preview
    open_schedule(page)
    rows = page.locator(".flow-trigger")
    # The saved Flow shows trigger 1 (the schedule above) plus two more rows.
    expect(page.locator("#flow-triggers")).to_be_visible()
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".flow-trigger-name")).to_have_text("Trigger 2")
    expect(page.locator("#flow-trigger-2-time")).to_have_value("11:00")
    expect(page.locator("#flow-trigger-3-time")).to_have_value("14:00")
    expect(page.locator("#flow-triggers-help")).to_contain_text("Trigger 1 is the schedule above")
    expect(page.locator("#flow-triggers-help")).to_contain_text("A run that finds no new file finishes without changes.")
    shot(page, evidence, "saved")

    # Add copies the last trigger one hour later and focuses its time.
    page.get_by_role("button", name="Add trigger", exact=True).click()
    expect(rows).to_have_count(3)
    expect(page.locator("#flow-trigger-4-time")).to_be_focused()
    expect(page.locator("#flow-trigger-4-time")).to_have_value("15:00")
    expect(page.locator("#flow-trigger-4-type")).to_have_value("weekly")
    expect(rows.nth(2).locator(".flow-trigger-weekdays input:checked")).to_have_count(5)
    # Changing a row's schedule swaps its weekday and day-of-month fields.
    page.select_option("#flow-trigger-4-type", "monthly")
    expect(rows.nth(2).locator(".flow-trigger-weekdays")).to_be_hidden()
    expect(page.locator("#flow-trigger-4-day")).to_be_visible()
    page.locator("#flow-trigger-4-day").fill("15")
    # Removing trigger 3 renumbers the rest and keeps their values.
    page.get_by_role("button", name="Remove trigger 3").click()
    expect(rows).to_have_count(2)
    expect(rows.nth(1).locator(".flow-trigger-name")).to_have_text("Trigger 3")
    expect(page.locator("#flow-trigger-3-type")).to_have_value("monthly")
    expect(page.locator("#flow-trigger-3-day")).to_have_value("15")
    expect(page.locator("#flow-trigger-4-time")).to_have_count(0)
    expect(page.get_by_role("button", name="Remove trigger 3")).to_be_focused()
    shot(page, evidence, "edited")

    # Validation failure: the message sits beside the actions, the named
    # trigger is focused and every row keeps its value.
    page.select_option("#preview-outcome", "duplicate")
    page.get_by_role("button", name="Save changes", exact=True).click()
    error = page.locator(".flow-form-error")
    expect(error).to_contain_text("Trigger 3 repeats trigger 2.")
    expect(page.locator("#flow-trigger-3-time")).to_be_focused()
    expect(page.locator("#flow-trigger-2-time")).to_have_value("11:00")
    expect(page.locator("#flow-trigger-3-day")).to_have_value("15")
    shot(page, evidence, "validation-error")

    # Recovery: the same form saves once the outcome allows it.
    page.select_option("#preview-outcome", "saved")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.locator("#preview-status")).to_have_text("Flow saved")
    page.wait_for_function("window.previewPayload !== null")
    body = payload(page)
    assert body["schedule_type"] == "weekly"
    assert body["additional_triggers"] == [
        {"type": "weekly", "time": "11:00", "days": WEEKDAYS, "day": None},
        {"type": "monthly", "time": "15:00", "days": [], "day": 15},
    ]


def test_manual_hides_triggers_and_new_flow_starts_empty(preview):
    page, evidence, _ = preview
    open_schedule(page)
    # Manual hides More triggers and sends none.
    page.select_option("#flow-schedule-type", "manual")
    expect(page.locator("#flow-triggers")).to_be_hidden()
    page.get_by_role("button", name="Save changes", exact=True).click()
    page.wait_for_function("window.previewPayload !== null")
    assert payload(page)["schedule_type"] == "manual"
    assert payload(page)["additional_triggers"] == []

    page.locator('.preview-nav [data-screen="new"]').click()
    open_schedule(page)
    expect(page.locator("#flow-triggers")).to_be_hidden()
    page.select_option("#flow-schedule-type", "daily")
    expect(page.locator("#flow-triggers")).to_be_visible()
    expect(page.locator(".flow-trigger")).to_have_count(0)
    page.locator("#flow-schedule-time").fill("07:30")
    page.get_by_role("button", name="Add trigger", exact=True).click()
    expect(page.locator("#flow-trigger-2-type")).to_have_value("daily")
    expect(page.locator("#flow-trigger-2-time")).to_have_value("08:30")
    shot(page, evidence, "new-flow")

    # Python Flows do not promise the Outlook/file no-op.
    page.locator('.preview-nav [data-screen="python"]').click()
    open_schedule(page)
    expect(page.locator("#flow-triggers-help")).to_contain_text("Trigger 1 is the schedule above")
    expect(page.locator("#flow-triggers-help")).not_to_contain_text("no new file")


def test_trigger_limit_and_list_label(preview):
    page, evidence, _ = preview
    open_schedule(page)
    add = page.get_by_role("button", name="Add trigger", exact=True)
    for _ in range(21):
        add.click()
    expect(page.locator(".flow-trigger")).to_have_count(23)
    expect(add).to_be_disabled()
    expect(page.locator("#flow-trigger-limit")).to_have_text("A Flow can have at most 24 triggers.")
    page.get_by_role("button", name="Remove trigger 24").click()
    expect(add).to_be_enabled()
    expect(page.locator("#flow-trigger-limit")).to_be_hidden()

    page.locator('.preview-nav [data-screen="list"]').click()
    expect(page.locator("td", has_text="+ 2 more triggers")).to_have_count(1)
    shot(page, evidence, "list")


def test_narrow_screen_keeps_trigger_rows_inside_the_page(preview):
    page, evidence, _ = preview
    page.set_viewport_size({"width": 390, "height": 844})
    open_schedule(page)
    page.get_by_role("button", name="Add trigger", exact=True).click()
    page.select_option("#flow-trigger-4-type", "monthly")
    expect(page.get_by_role("button", name="Remove trigger 4")).to_be_visible()
    no_overflow(page)
    shot(page, evidence, "narrow")
