"""Clickable fictional preview: Flow owners, the User filter and view-only Flows.

The preview replaces only the API layer and the signed-in viewer: production
app.js renders the Flows list and builders. These checks walk the changed
controls for the host PC and for a colleague, including the refusal and
recovery paths, on desktop and narrow screens. They are synthetic browser
evidence, not live worker, Outlook or SQL verification.
"""
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_flow_triggers_preview import launch

ROOT = Path(__file__).resolve().parents[1]


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
            page.goto(f"http://127.0.0.1:{server.server_port}/static/recording-preview/flow-owners.html")
            page.locator("#flow-owner-filter").wait_for()
            yield page, evidence, errors
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def shot(page, evidence, name):
    page.screenshot(path=str(evidence / f"flow-owners-{name}.png"), full_page=True)


def row(page, name):
    return page.locator("#flow-workspace tr[data-flow-id]").filter(has_text=name)


def calls(page, method):
    return page.evaluate("method => window.previewCalls.filter(call => call.method === method)", method)


def test_host_filters_unassigned_flows_and_reassigns_them_in_place(preview):
    page, evidence, _errors = preview
    page.locator(".flow-group-toggle").first.click()
    owner_filter = page.get_by_label("Show flows for user")
    expect(owner_filter.locator("option")).to_have_text(
        ["All users (4)", "Maya Chen (1)", "Omar Malik (1)", "Sofia Rivera (0)", "Unassigned (2)"])
    expect(page.locator("#flow-workspace tr[data-flow-id]")).to_have_count(4)
    expect(page.get_by_label("Owner: Daily returns")).to_have_value("")

    owner_filter.select_option(label="Unassigned (2)")
    expect(page.locator("#flow-workspace tr[data-flow-id]")).to_have_count(2)
    expect(page.get_by_label("Show flows for user")).to_be_focused()
    shot(page, evidence, "host-unassigned")
    page.get_by_label("Owner: Legacy stock check").select_option(label="Omar Malik")
    expect(page.locator("#preview-status")).to_have_text("Legacy stock check now belongs to Omar Malik.")
    # The row stays so the host can keep working down the list; the counts update.
    expect(row(page, "Legacy stock check")).to_have_count(1)
    expect(page.get_by_label("Show flows for user").locator("option")).to_have_text(
        ["All users (4)", "Maya Chen (1)", "Omar Malik (2)", "Sofia Rivera (0)", "Unassigned (1)"])
    assert calls(page, "PATCH")[-1] == {"method": "PATCH", "path": "/api/flows/43", "body": {"owner_person_id": 2}}

    page.get_by_label("Show flows for user").select_option(label="Omar Malik (2)")
    expect(page.locator("#flow-workspace tr[data-flow-id]")).to_have_count(2)
    page.get_by_label("Owner: Customer feedback files").select_option(label="Maya Chen")
    expect(page.locator("#preview-status")).to_contain_text("Omar Malik is emailed about the change")
    page.get_by_label("Show flows for user").select_option(label="Sofia Rivera (0)")
    expect(page.locator(".flow-empty h2")).to_have_text("No of this user's production flows")
    expect(page.locator(".flow-empty")).to_contain_text("Choose All users to see every Flow.")


def test_host_builder_keeps_the_owner_choice(preview):
    page, evidence, _errors = preview
    page.get_by_label("Fictional viewer").select_option("host-maya")
    page.get_by_role("button", name="New Outlook flow").click()
    owner = page.locator("#flow-owner")
    page.locator("#flow-step-toggle-schedule").click()
    expect(owner).to_have_value("1")  # New Flows start with the person signed in on the host PC.
    expect(page.locator("#flow-owner-help")).to_contain_text("SQL insertion signs in as maya_sql, Maya Chen's own SQL user.")
    owner.select_option(label="Omar Malik (Business)")
    expect(page.locator("#flow-owner-help")).to_contain_text("whose password is not saved yet")
    shot(page, evidence, "host-builder-owner")
    page.get_by_role("button", name="Open Maya's flow").click()
    expect(page.locator("#flow-readonly-banner")).to_have_count(0)
    expect(page.get_by_role("button", name="Save changes")).to_be_visible()


def test_colleague_sees_others_flows_read_only_and_owns_new_ones(preview):
    page, evidence, _errors = preview
    page.get_by_label("Fictional viewer").select_option("omar")
    page.locator(".flow-group-toggle").first.click()
    expect(page.get_by_label("Show flows for user").locator("option").nth(2)).to_have_text("Omar Malik (you) (1)")
    expect(page.locator("#flow-workspace select.flow-inline-edit[data-field=owner_person_id]")).to_have_count(0)
    maya = row(page, "Regional orders")
    expect(maya).to_contain_text("Maya Chen")
    expect(maya.get_by_role("button", name="View Regional orders (read only)")).to_be_visible()
    expect(maya.locator(".flow-enabled-switch")).to_be_disabled()
    expect(maya.locator(".flow-run")).to_be_enabled()
    maya.locator("summary").click()
    expect(maya.locator(".flow-delete")).to_have_count(0)
    expect(maya.locator(".flow-classification")).to_have_count(0)
    mine = row(page, "Customer feedback files")
    expect(mine.locator(".flow-edit")).to_have_text("Edit")
    expect(mine.locator(".flow-enabled-switch")).to_be_enabled()
    shot(page, evidence, "colleague-list")

    maya.get_by_role("button", name="View Regional orders (read only)").click()
    banner = page.locator("#flow-readonly-banner")
    expect(banner).to_have_text("View only. This Flow belongs to Maya Chen. You can view and run it; "
                                "only Maya Chen or the host PC can change it.")
    expect(page.locator("#flow-name")).to_be_disabled()
    expect(page.get_by_role("button", name="Save changes")).to_have_count(0)
    page.locator("#flow-step-toggle-schedule").click()  # Every step can still be read.
    expect(page.locator("#flow-owner-name")).to_have_text("Maya Chen")
    expect(page.locator("#flow-schedule-type")).to_be_disabled()
    shot(page, evidence, "colleague-view-only")
    page.get_by_role("button", name="Back to Flows").click()
    expect(page.get_by_label("Show flows for user")).to_be_visible()

    page.get_by_role("button", name="New Outlook flow").click()
    page.locator("#flow-step-toggle-schedule").click()
    expect(page.locator("#flow-owner")).to_have_count(0)
    expect(page.locator("#flow-owner-name")).to_have_text("Omar Malik (you)")
    expect(page.locator("#flow-owner-help")).to_contain_text("Flows you create are assigned to you.")
    expect(page.locator("#flow-owner-help")).to_contain_text("whose password is not saved yet")
    shot(page, evidence, "colleague-new-flow")


def test_narrow_screen_keeps_filter_and_view_only_readable(preview):
    page, evidence, _errors = preview
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Fictional viewer").select_option("omar")
    expect(page.get_by_label("Show flows for user")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Flows toolbar overflows"
    shot(page, evidence, "narrow-list")
    page.get_by_role("button", name="Open Maya's flow").click()
    expect(page.locator("#flow-readonly-banner")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "View-only builder overflows"
    shot(page, evidence, "narrow-view-only")
