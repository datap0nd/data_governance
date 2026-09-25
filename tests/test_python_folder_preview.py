"""Clickable fictional preview of the PROPOSED Python Flow folder choice.

Production app.js renders the Python builder and the Flows list; the preview
replaces only the API layer and adds the proposed "Flow folder" control, which
the application does not have yet. These checks walk that control on desktop
and narrow screens, including a validation failure and its recovery. They are
synthetic browser evidence for owner review, not an implemented feature.
"""
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_python_scripts_preview import launch, no_overflow, open_step, shot

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r"C:\Stock\jobs\refresh_stock.py"


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
            page.goto(f"http://127.0.0.1:{server.server_port}/static/recording-preview/python-folder.html")
            page.locator("#preview-folder-choice").wait_for()
            yield page, evidence, errors
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def expand_python_group(page):
    toggle = page.locator("#flow-group-Python")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    expect(page.locator("#flow-group-rows-Python")).to_be_visible()


def test_proposed_folder_choice_walkthrough(preview):
    page, evidence, _ = preview
    choice = page.locator("#preview-folder-choice")
    help_text = page.locator("#preview-folder-help")
    # A new Python Flow starts with no Metronome folder; the choice sits beside the run mode.
    expect(choice.locator("legend")).to_contain_text("Flow folder")
    expect(page.locator('input[name="preview-folder-mode"][value="none"]')).to_be_checked()
    expect(page.locator('input[name="flow-python-mode"][value="run"]')).to_be_checked()
    expect(help_text).to_have_text("Nothing is created on disk for this Flow. The scripts run in place, from any folder.")
    no_overflow(page)
    shot(page, evidence, "folder-new-run-default")
    # Choosing the managed folder names the exact folder, following the typed Flow name.
    page.locator("#flow-name").fill("Weekly stock refresh")
    page.locator("#flow-python-script-1").fill(SCRIPT)
    page.locator('input[name="preview-folder-mode"][value="managed"]').check()
    expect(help_text).to_contain_text(r"C:\Metronome\Flows\Python\Weekly stock refresh with Downloads and Scripts")
    shot(page, evidence, "folder-new-managed")
    # A file-producing Flow without a Metronome folder asks for its Output folder.
    page.locator('input[name="flow-python-mode"][value="outputs"]').check()
    page.locator('input[name="preview-folder-mode"][value="none"]').check()
    expect(help_text).to_have_text("Nothing is created on disk for this Flow. Choose the Output folder in the Output step.")
    open_step(page, "destination")
    output_folder = page.locator("#flow-target-folder")
    expect(output_folder).to_be_visible()
    expect(output_folder).to_have_attribute("required", "")
    expect(page.locator("#flow-destination small")).to_contain_text("even while Enforce paths is on")
    no_overflow(page)
    shot(page, evidence, "folder-new-output-folder")
    # Failure and recovery: a relative folder is refused beside the actions, the field is focused
    # and every entered value stays; a full path then saves.
    output_folder.fill(r"reports\stock")
    page.get_by_role("button", name="Create flow", exact=True).click()
    expect(page.locator(".flow-form-error")).to_contain_text("Output folder must be a full path")
    expect(output_folder).to_be_focused()
    expect(page.locator("#flow-name")).to_have_value("Weekly stock refresh")
    expect(page.locator("#flow-python-script-1")).to_have_value(SCRIPT)
    shot(page, evidence, "folder-new-validation")
    output_folder.fill(r"\\fileserver\reports\stock")
    page.get_by_role("button", name="Create flow", exact=True).click()
    expect(page.locator("#preview-status")).to_have_text("Flow saved · Preview · no Metronome folder")
    payload = page.evaluate("window.previewPayload")
    assert payload["managed_folder"] is False and payload["target_folder"] == r"\\fileserver\reports\stock"
    expand_python_group(page)
    created = page.locator("tr[data-flow-id]").filter(has_text="Weekly stock refresh")
    expect(created).to_contain_text(r"\\fileserver\reports\stock")
    shot(page, evidence, "folder-list-new-flow")
    # An existing managed Flow can drop its folder: the folder stays on disk and the save says so.
    page.locator(".preview-nav button[data-screen='builder']").click()
    expect(page.locator('input[name="preview-folder-mode"][value="managed"]')).to_be_checked()
    page.locator('input[name="preview-folder-mode"][value="none"]').check()
    expect(help_text).to_contain_text(r"Metronome stops using Python\Excel price refresh. The folder and its files stay on disk")
    shot(page, evidence, "folder-edit-drop-managed")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.locator("#preview-status")).to_have_text(
        r"Flow saved · Preview · no Metronome folder; Python\Excel price refresh stays on disk")
    expand_python_group(page)
    edited = page.locator("tr[data-flow-id='31']")
    expect(edited).to_contain_text("Runs 1 script(s)")
    edited.locator("summary").click()
    edited.locator(".flow-open-folder").click()
    expect(page.locator("#preview-status")).to_have_text(
        r"Excel price refresh has no Metronome folder: its scripts run from C:\Reports\excel_jobs.")
    # Narrow screen: the choice and its help wrap without horizontal scrolling.
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".preview-nav button[data-screen='new']").click()
    expect(page.locator("#preview-folder-choice")).to_be_visible()
    no_overflow(page)
    shot(page, evidence, "folder-mobile-new")
    page.locator(".preview-nav button[data-screen='list']").click()
    expand_python_group(page)
    no_overflow(page)
    shot(page, evidence, "folder-mobile-list")
