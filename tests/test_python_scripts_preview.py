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
FAILED_RUN_ERROR = "Python script clean_orders.py (step 4 of 4) failed with exit code 1: KeyError 'region'"
RUNNING_STEP = "Running script 2 of 4: fetch_orders.py -sheet U."


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
    overflow = page.evaluate("""() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        elements: [...document.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > innerWidth + 1)
            .slice(0, 12).map(el => ({tag: el.tagName, id: el.id, className: typeof el.className === 'string' ? el.className : '', right: Math.round(el.getBoundingClientRect().right)}))})""")
    assert overflow["scrollWidth"] <= overflow["width"], overflow


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
    # The new Flow defaults to run-only; this walkthrough covers the existing
    # file and SQL path, so select it explicitly.
    page.locator('input[name="flow-python-mode"][value="outputs"]').check()
    expect(page.locator("#flow-step-body-source")).to_be_visible()
    no_overflow(page)
    # Script rows: one empty row to start, Remove disabled until a second row exists, renumbering on remove.
    rows = page.locator(".flow-python-script")
    expect(rows).to_have_count(1)
    expect(page.locator(".flow-python-remove").first).to_be_disabled()
    page.locator("#flow-name").fill("Orders extract v2")
    page.locator("#flow-python-script-1").fill(SCRIPT_1)
    # Each row carries an optional Arguments line and a Values textarea whose run count updates live.
    arguments_1 = page.locator("#flow-python-arguments-1")
    expect(arguments_1).to_have_attribute("aria-label", "Script 1 arguments")
    expect(arguments_1).to_have_attribute("placeholder", "-sheet T")
    expect(page.locator("label[for='flow-python-arguments-1']")).to_contain_text("Arguments")
    expect(page.locator("label[for='flow-python-arguments-1']")).to_contain_text("(optional)")
    expect(page.locator("label[for='flow-python-values-1']")).to_contain_text("Values, one per run")
    expect(page.locator("#flow-python-script-help")).to_contain_text("Every script receives --output")
    expect(rows.first.locator(".flow-python-values-help")).to_contain_text("Paste one value per line; the script runs once per value.")
    arguments_1.fill("-sheet T")
    values_1 = page.locator("#flow-python-values-1")
    count_1 = rows.first.locator(".flow-python-values-count")
    expect(values_1).to_have_attribute("aria-label", "Script 1 values")
    expect(count_1).to_have_text("1 run")
    values_1.fill("T\nU\n\nV\n")
    expect(count_1).to_have_text("3 runs")
    values_1.fill("")
    expect(count_1).to_have_text("1 run")
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
    # Renumbering keeps every id, label target and aria-label aligned with the row number.
    page.get_by_role("button", name="Add another script").click()
    expect(rows).to_have_count(3)
    page.locator("#flow-python-arguments-3").fill("--third")
    page.locator("#flow-python-values-3").fill("a\nb")
    expect(rows.nth(2).locator(".flow-python-values-count")).to_have_text("2 runs")
    page.locator(".flow-python-remove").nth(1).click()
    expect(rows).to_have_count(2)
    renumbered = rows.nth(1)
    expect(renumbered.locator(".flow-python-step")).to_have_text("2.")
    expect(renumbered.locator(".flow-python-script-arguments")).to_have_attribute("id", "flow-python-arguments-2")
    expect(renumbered.locator(".flow-python-script-arguments")).to_have_attribute("aria-label", "Script 2 arguments")
    expect(renumbered.locator(".flow-python-arguments-label")).to_have_attribute("for", "flow-python-arguments-2")
    expect(renumbered.locator(".flow-python-script-values")).to_have_attribute("id", "flow-python-values-2")
    expect(renumbered.locator(".flow-python-script-values")).to_have_attribute("aria-label", "Script 2 values")
    expect(renumbered.locator(".flow-python-values-label")).to_have_attribute("for", "flow-python-values-2")
    expect(page.locator("#flow-python-arguments-3")).to_have_count(0)
    expect(page.locator("#flow-python-arguments-2")).to_have_value("--third")
    expect(page.locator("#flow-python-values-2")).to_have_value("a\nb")
    expect(page.locator("#flow-python-arguments-1")).to_have_value("-sheet T")
    page.locator("#flow-python-arguments-2").fill("")
    page.locator("#flow-python-values-2").fill("")
    expect(renumbered.locator(".flow-python-values-count")).to_have_text("1 run")
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
    # Unclosed quote in the arguments: the message sits beside Save and the first row's Arguments field takes focus.
    page.select_option("#preview-outcome", "unclosed")
    page.get_by_role("button", name="Create flow", exact=True).click()
    expect(error).to_contain_text("Flow not saved: Script arguments have an unclosed quote.")
    expect(page.locator("#flow-step-body-source")).to_be_visible()
    assert page.evaluate("document.activeElement.id") == "flow-python-arguments-1"
    expect(page.locator("#flow-python-arguments-1")).to_have_value("-sheet T")
    expect(page.locator("#flow-python-script-1")).to_have_value(SCRIPT_1)
    expect(page.get_by_role("button", name="Create flow", exact=True)).to_be_enabled()
    shot(page, evidence, "builder-arguments-error")
    # Successful save: the payload follows the API contract and the list gains a Python group.
    page.select_option("#preview-outcome", "saved")
    page.get_by_role("button", name="Create flow", exact=True).click()
    page.wait_for_function("window.previewPayload && window.previewPayload.source_type === 'python'")
    payload = page.evaluate("window.previewPayload")
    assert payload["python_scripts"][0] == SCRIPT_1 and payload["python_scripts"][1].endswith("clean_orders.py")
    assert len(payload["python_scripts"]) == 2
    assert payload["python_script_arguments"] == ["-sheet T", ""]
    assert payload["python_script_values"] == [[], []]
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
    expect(created).to_contain_text("fetch_orders.py -sheet T → clean_orders.py")
    expect(created.locator("td").nth(5)).to_have_text("—")
    expect(rows.filter(has_text="Supplier lead times")).to_contain_text("Final XLSX file")
    # Rows with values show the count instead of the values; the running Flow sits in the execution pane.
    expect(rows.filter(has_text="Supplier lead times")).to_contain_text("fetch_lead_times.py -sheet (3 values)")
    expect(rows.filter(has_text="fetch_orders.py -sheet (3 values) → clean_orders.py")).to_have_count(1)
    expect(page.locator("#flow-execution-pane")).to_be_visible()
    expect(page.locator("#flow-execution-rows")).to_contain_text("Orders extract")
    no_overflow(page)
    shot(page, evidence, "list-python-group")
    # Run on the saved Flow: the Python-specific toast, then a queued run in run history.
    created.locator(".flow-run").click()
    expect(page.locator("#preview-status")).to_have_text("Run queued. The worker will run the Python scripts in order.")
    queued_run_id = max(run["id"] for run in page.evaluate("window.previewData.runs"))
    assert queued_run_id == 54
    shot(page, evidence, "list-run-queued")
    # Run history names the failing script and step, and the succeeded run's summary.
    page.evaluate("previewShow('runs')")
    failed = page.locator(".flow-table tbody tr").filter(has_text="#52")
    expect(failed).to_contain_text(FAILED_RUN_ERROR)
    expect(failed).to_contain_text("Python scripts")
    expect(page.locator(".flow-table tbody tr").filter(has_text="#51")).to_contain_text("Ran 4 Python script run(s) and saved 1 file(s)")
    # One run per value: the running run names the value it is on; the single-script Flow delivered three files.
    running = page.locator(".flow-table tbody tr").filter(has_text="#53")
    expect(running).to_contain_text(RUNNING_STEP)
    expect(running).to_contain_text("Running")
    expect(page.locator(".flow-table tbody tr").filter(has_text="#50")).to_contain_text("Ran 3 Python script run(s) and saved 3 file(s)")
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
    expect(page.locator("#flow-python-arguments-1")).to_have_value("-sheet")
    expect(page.locator("#flow-python-values-1")).to_have_value("T\nU\nV")
    expect(page.locator(".flow-python-script").first.locator(".flow-python-values-count")).to_have_text("3 runs")
    expect(page.locator("#flow-python-arguments-2")).to_have_value("")
    expect(page.locator("#flow-python-values-2")).to_have_value("")
    open_step(page, "destination")
    expect(page.locator("#flow-python-output-sql")).to_be_checked()
    expect(page.locator("#flow-sql-table")).to_have_value("orders_clean")
    # Adding a value and saving sends the aligned lists; the list label shows the new count.
    open_step(page, "source")
    page.locator("#flow-python-values-1").fill("T\nU\nV\nW")
    expect(page.locator(".flow-python-script").first.locator(".flow-python-values-count")).to_have_text("4 runs")
    shot(page, evidence, "builder-values")
    page.get_by_role("button", name="Save changes", exact=True).click()
    page.wait_for_function("window.previewPayload && window.previewPayload.python_script_values && window.previewPayload.python_script_values[0].length === 4")
    payload = page.evaluate("window.previewPayload")
    assert payload["python_scripts"] == [SCRIPT_1, SCRIPT_2]
    assert payload["python_script_arguments"] == ["-sheet", ""]
    assert payload["python_script_values"] == [["T", "U", "V", "W"], []]
    expect(page.locator("#flow-builder-form")).to_have_count(0)
    expect(page.locator('tr[data-flow-group="Python"]').filter(has_text="fetch_orders.py -sheet (4 values) → clean_orders.py")).to_have_count(1)
    # Narrow screen: every step still fits without horizontal scrolling.
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("previewShow('new')")
    page.locator('input[name="flow-python-mode"][value="outputs"]').check()
    page.locator("#flow-python-script-1").fill(SCRIPT_1)
    page.get_by_role("button", name="Add another script").click()
    page.locator("#flow-python-arguments-2").fill('-sheet "Q 1" --region GCC')
    page.locator("#flow-python-values-2").fill("North\nSouth\nEast")
    expect(page.locator(".flow-python-script").nth(1).locator(".flow-python-values-count")).to_have_text("3 runs")
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
