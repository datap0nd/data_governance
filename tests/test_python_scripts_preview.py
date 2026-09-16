"""Clickable fictional preview: source picker, Python builder, Flows list and run history.

The preview replaces only the API layer: production app.js renders every screen.
These checks walk every changed control, including the validation failure and
its recovery, on desktop and narrow screens. They are synthetic browser
evidence, not live worker, Python or PostgreSQL verification.
"""
import functools
import os
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_1 = r"C:\Metronome\Flows\Python\fetch_orders.py"
SCRIPT_2 = r"C:\Metronome\Flows\Python\clean_orders.py"
FAILED_RUN_ERROR = "Python script clean_orders.py (step 2 of 2) failed with exit code 1: KeyError 'region'"


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
            page.goto(f"http://127.0.0.1:{server.server_port}/static/recording-preview/python-scripts.html")
            page.locator("#flow-source-python").wait_for()
            yield page, evidence, errors
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def shot(page, evidence, name):
    page.screenshot(path=str(evidence / f"{name}.png"), full_page=True)


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def open_step(page, key):
    body = page.locator(f"#flow-step-body-{key}")
    if not body.is_visible():
        page.locator(f"#flow-step-toggle-{key}").click()
    expect(body).to_be_visible()


def test_python_builder_list_and_run_history_walkthrough(preview):
    page, evidence, _ = preview
    # Source picker: the Python card sits with the other sources and opens the Python builder.
    expect(page.locator(".flow-source-card")).to_have_count(5)
    shot(page, evidence, "picker")
    page.locator("#flow-source-python").click()
    form = page.locator("#flow-builder-form")
    expect(form).to_have_attribute("data-source-type", "python")
    expect(page.locator("#flow-step-body-source")).to_be_visible()
    no_overflow(page)
    # Script rows: one empty row to start, Remove disabled until a second row exists, renumbering on remove.
    rows = page.locator(".flow-python-script")
    expect(rows).to_have_count(1)
    expect(page.locator(".flow-python-remove").first).to_be_disabled()
    page.locator("#flow-name").fill("Orders extract v2")
    page.locator("#flow-python-script-1").fill(SCRIPT_1)
    page.get_by_role("button", name="Add another script").click()
    expect(rows).to_have_count(2)
    expect(page.locator(".flow-python-remove").first).to_be_enabled()
    page.get_by_role("button", name="Add another script").click()
    expect(rows).to_have_count(3)
    page.locator("#flow-python-script-3").fill(r"C:\scripts\wrong.py")
    page.locator(".flow-python-remove").nth(2).click()
    expect(rows).to_have_count(2)
    expect(page.locator("#flow-python-script-3")).to_have_count(0)
    expect(rows.nth(1).locator(".flow-python-step")).to_have_text("2.")
    # Browse uploads through the existing script endpoint and fills that row only.
    with page.expect_file_chooser() as chooser:
        page.locator(".flow-python-browse").nth(1).click()
    chooser.value.set_files({"name": "clean_orders.py", "mimeType": "text/x-python", "buffer": b"import sys\n"})
    expect(page.locator("#flow-python-script-2")).to_have_value(re.compile(r"clean_orders\.py$"))
    expect(page.locator("#flow-python-script-1")).to_have_value(SCRIPT_1)
    expect(page.locator("#preview-status")).to_contain_text("Python script added: clean_orders.py")
    shot(page, evidence, "builder-scripts")
    # Output: the final-file fields show by default; choosing SQL swaps them for the SQL controls.
    open_step(page, "destination")
    expect(page.locator("#flow-python-output-file")).to_be_checked()
    expect(page.locator("#flow-python-file-fields")).to_be_visible()
    expect(page.locator("#flow-python-sql-fields")).to_be_hidden()
    expect(page.locator("#flow-filename")).to_have_value("{flow}.csv")
    page.select_option("#flow-file-format", "xlsx")
    expect(page.locator("#flow-filename")).to_have_value("{flow}.xlsx")
    expect(page.locator(".flow-filename-preview")).to_contain_text("Orders extract v2.xlsx")
    shot(page, evidence, "builder-output-file")
    page.locator("#flow-python-output-sql").check()
    expect(page.locator("#flow-python-file-fields")).to_be_hidden()
    expect(page.locator("#flow-python-sql-fields")).to_be_visible()
    expect(page.locator("#flow-sql-fields")).to_be_visible()
    assert page.evaluate("document.querySelector('#flow-sql-enabled').checked") is True
    page.locator("#flow-sql-table").fill("orders_clean")
    page.locator("#flow-sql-table").dispatch_event("change")
    page.locator("#flow-view-refresh").get_by_label("Manual", exact=False).check()
    page.locator("#flow-view-refresh-search").fill("region")
    page.locator(".flow-view-refresh-result").first.wait_for()
    page.locator(".flow-view-refresh-result").first.click()
    page.get_by_role("button", name="Verify selection", exact=True).click()
    expect(page.locator("#flow-view-refresh-manual-status")).to_contain_text("1 view(s) verified")
    shot(page, evidence, "builder-output-sql")
    # Validation failure: the message sits beside the actions, the form keeps every value and the first script is focused.
    page.select_option("#preview-outcome", "invalid")
    page.get_by_role("button", name="Create flow", exact=True).click()
    error = page.locator(".flow-form-error")
    expect(error).to_contain_text("Flow not saved: Python scripts must be .py files.")
    expect(page.locator("#flow-step-body-source")).to_be_visible()
    assert page.evaluate("document.activeElement.id") == "flow-python-script-1"
    expect(page.locator("#flow-python-script-1")).to_have_value(SCRIPT_1)
    expect(page.locator("#flow-python-script-2")).to_have_value(re.compile(r"clean_orders\.py$"))
    expect(page.locator("#flow-name")).to_have_value("Orders extract v2")
    expect(page.locator("#flow-python-output-sql")).to_be_checked()
    expect(page.get_by_role("button", name="Create flow", exact=True)).to_be_enabled()
    shot(page, evidence, "builder-validation-error")
    # Successful save: the payload follows the API contract and the list gains a Python group.
    page.select_option("#preview-outcome", "saved")
    page.get_by_role("button", name="Create flow", exact=True).click()
    page.wait_for_function("window.previewPayload && window.previewPayload.source_type === 'python'")
    payload = page.evaluate("window.previewPayload")
    assert payload["python_scripts"][0] == SCRIPT_1 and payload["python_scripts"][1].endswith("clean_orders.py")
    assert len(payload["python_scripts"]) == 2
    assert payload["sql_handoff_enabled"] is True and payload["file_format"] == "csv"
    assert payload["filename_template"] == "{flow}.csv"
    assert payload["transform_enabled"] is False and payload["transform_script_path"] is None
    assert payload["browser_mode"] == "headless" and payload["site_id"] is None and payload["output_mode"] == "run_folders"
    assert payload["post_sql_refresh"]["mode"] == "manual" and payload["post_sql_refresh"]["views"][0]["name"] == "orders_by_region_mv"
    expect(page.locator("#flow-builder-form")).to_have_count(0)  # the save returned to the list
    group = page.locator("#flow-group-Python")
    expect(group).to_be_visible()
    expect(group).to_contain_text("3")
    group.click()
    rows = page.locator('tr[data-flow-group="Python"]')
    expect(rows).to_have_count(3)
    created = rows.filter(has_text="Orders extract v2")
    expect(created).to_contain_text("2 Python script(s)")
    expect(created).to_contain_text("Final CSV → SQL")
    expect(created).to_contain_text("fetch_orders.py → clean_orders.py")
    expect(created.locator("td").nth(5)).to_have_text("—")
    expect(rows.filter(has_text="Supplier lead times")).to_contain_text("Final XLSX file")
    no_overflow(page)
    shot(page, evidence, "list-python-group")
    # Run on the saved Flow: the Python-specific toast, then a queued run in run history.
    created.locator(".flow-run").click()
    expect(page.locator("#preview-status")).to_have_text("Run queued. The worker will run the Python scripts in order.")
    queued_run_id = max(run["id"] for run in page.evaluate("window.previewData.runs"))
    assert queued_run_id == 53
    shot(page, evidence, "list-run-queued")
    # Run history names the failing script and step, and the succeeded run's summary.
    page.evaluate("previewShow('runs')")
    failed = page.locator(".flow-table tbody tr").filter(has_text="#52")
    expect(failed).to_contain_text(FAILED_RUN_ERROR)
    expect(failed).to_contain_text("Python scripts")
    expect(page.locator(".flow-table tbody tr").filter(has_text="#51")).to_contain_text("Ran 2 Python script(s) and saved Orders extract.csv")
    queued = page.locator(".flow-table tbody tr").filter(has_text=f"#{queued_run_id}")
    expect(queued).to_contain_text("Orders extract v2")
    expect(queued).to_contain_text("Queued")
    expect(queued).to_contain_text("Python scripts")
    expect(page.locator(".flow-resume")).to_have_count(0)
    no_overflow(page)
    shot(page, evidence, "runs")
    # Editing an existing Python Flow restores its scripts and SQL choice.
    page.evaluate("previewShow('builder')")
    expect(page.locator(".flow-python-script")).to_have_count(2)
    expect(page.locator("#flow-python-script-2")).to_have_value(SCRIPT_2)
    open_step(page, "destination")
    expect(page.locator("#flow-python-output-sql")).to_be_checked()
    expect(page.locator("#flow-sql-table")).to_have_value("orders_clean")
    # Narrow screen: every step still fits without horizontal scrolling.
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("previewShow('new')")
    page.locator("#flow-python-script-1").fill(SCRIPT_1)
    page.get_by_role("button", name="Add another script").click()
    no_overflow(page)
    shot(page, evidence, "builder-mobile-scripts")
    open_step(page, "destination")
    page.locator("#flow-python-output-sql").check()
    no_overflow(page)
    shot(page, evidence, "builder-mobile-output")
    page.evaluate("previewShow('list')")
    no_overflow(page)
    page.evaluate("previewShow('runs')")
    no_overflow(page)
    shot(page, evidence, "runs-mobile")
