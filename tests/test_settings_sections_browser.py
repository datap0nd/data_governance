"""AI, Paths, Flow workers and Premium Viewers as sections of System > Settings.

Synthetic: the production page in a real browser, with fictional HTTP
responses. No live app, database, portal or SQL access.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import re
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from test_flow_triggers_preview import launch

ROOT = Path(__file__).resolve().parents[1]
HOST = {"person_id": 1, "name": "Maya Chen", "is_local": True, "is_host": True, "is_admin": True}
COLLEAGUE = {"person_id": 2, "name": "Omar Malik", "is_local": False, "is_host": False, "is_admin": False}
HOST_ONLY = "On the host PC, open Metronome as http://localhost:8000."
# id, name in the rail, a control of the page's own form.
MOVED = [
    ("ai", "AI", "#ai-settings-form"),
    ("paths", "Paths", "#paths-form"),
    ("flow-settings", "Flow workers", "#flow-capacity-form"),
    ("premiumviewers", "Premium Viewers", "#premium-viewers-input"),
]


def _setting(key, label, value=3):
    return {"key": key, "label": label, "help": "", "kind": "int", "unit": "", "minimum": 1, "maximum": 9,
            "choices": [], "choice_labels": [], "placeholder": "", "optional": False, "default": 3, "value": value,
            "source": "default", "applies": "now", "applies_text": "Applies immediately", "source_text": "Default",
            "saved": None}


def _registry(state):
    # Like the real registry, the Workers group shows a value the Flow workers form also saves.
    return {"can_edit": state["me"]["is_host"], "sections": [
        {"id": "folders", "name": "Folders", "blurb": "Fixture folders.",
         "groups": [{"title": "Folders on the host PC", "settings": [_setting("folders.depth", "Folder depth")], "test": None}],
         "links": [{"label": "Flows root and enforced paths", "page": "paths", "note": "Shows which Flows a change would affect."}]},
        {"id": "flows", "name": "Flows", "blurb": "Fixture run limits.",
         "groups": [{"title": "Runs", "settings": [_setting("flows.attempts", "Attempts per run")], "test": None},
                    {"title": "Workers", "settings": [_setting("flows.visible_windows", "Visible browser windows",
                                                               state["flows"]["headed_capacity"])], "test": None}],
         "links": [{"label": "Worker status and per-portal limits", "page": "flow-settings", "note": ""}]},
        {"id": "updates", "name": "Updates, backups and logs", "blurb": "Fixture updates.",
         "groups": [{"title": "Backups", "settings": [_setting("backups.keep", "Backups kept")], "test": None}],
         "links": [{"label": "Install now and update history", "page": "updates", "note": ""}]},
    ]}


def _slot(number, configured):
    return {"slot": number, "worker_id": f"fixture-{number}", "configured": configured, "online": False,
            "status": "offline", "current_run_id": None, "current_scan_id": None, "current_task_id": None}


def _fresh_state(me):
    return {
        "me": dict(me), "calls": [], "unexpected": [], "fail": {},
        "paths": {"flows_root": "D:\\Metronome\\flows", "default": "D:\\Metronome\\flows", "source": "default",
                  "enforced": False, "source_folders": [{"name": "ASAP", "path": "D:\\Metronome\\flows\\ASAP"}],
                  "flows_outside_root": []},
        "flows": {"max_slots": 32, "total_capacity": 12, "active_total": 0, "headless_capacity": 2, "headed_capacity": 1,
                  "online_capacity": 0, "online_headed_capacity": 0, "active_headless": 0, "active_headed": 0,
                  "slots": [_slot(1, True), _slot(2, True), _slot(3, False)], "headed_slots": [_slot(1, True)],
                  "browser_channel": "chrome", "recording_wait_seconds": 10,
                  "portals": [{"id": 7, "name": "Fixture portal", "adapter": "asap_portal", "capacity": 4, "gscm_discovery": {}}]},
        "viewers": ["ana@example.test"],
        "portal": {"configured": False, "updated_at": None, "available": False, "source": None, "paused": False,
                   "paused_at": None, "paused_portal": None, "paused_refused": False, "failed_attempts": 0},
        "ai": {"mode": "preview", "effective_state": "deterministic_preview", "endpoint": "http://ai.example.test/v1/chat/completions",
               "model": "fixture-model", "provider_profile": "auto", "reasoning_effort": "medium", "max_tool_calls": 8,
               "max_model_turns": 6, "max_seconds": 180, "http_timeout_seconds": 90, "max_output_tokens": 4096,
               "temperature": 1, "top_p": 0.95, "operations_investigator_enabled": True,
               "automatic_alert_review_enabled": True, "alert_email_analysis_enabled": False,
               "documentation_suggestions_enabled": False, "pipeline_explanations_enabled": False,
               "api_key_configured": False, "configuration_source": "saved settings"},
    }


def _respond(state, route):
    request = route.request
    path, method = urlsplit(request.url).path, request.method
    body = request.post_data_json if method in {"PUT", "POST"} and request.post_data else None
    state["calls"].append((method, path, body))
    refusal = state["fail"].pop((method, path), None)
    if refusal:
        route.fulfill(status=refusal[0], json={"detail": refusal[1]})
        return
    if path == "/api/me":
        payload = state["me"]
    elif path == "/api/version":
        payload = {"version": "settings-sections-fixture", "up_to_date": True}
    elif path == "/api/system/remote-flow-control":
        payload = {"enabled": False}
    elif path == "/api/system/settings" and method == "GET":
        payload = _registry(state)
    elif path == "/api/system/settings" and method == "PUT":
        if "flows.visible_windows" in body["values"]:
            state["flows"]["headed_capacity"] = body["values"]["flows.visible_windows"]
        payload = {**_registry(state), "changed": list(body["values"])}
    elif path == "/api/system/logs":
        payload = {"directory": "D:\\Metronome\\logs", "exists": False, "file_count": 0, "total_bytes": 0, "largest": []}
    elif path == "/api/system/settings/portal-login":
        payload = state["portal"]
    elif path == "/api/system/settings/portal-login/resume" and method == "POST":
        state["portal"].update(paused=False, paused_at=None, paused_portal=None, paused_refused=False, failed_attempts=0)
        payload = state["portal"]
    elif path == "/api/system/paths" and method == "GET":
        payload = state["paths"]
    elif path == "/api/system/paths" and method == "PUT":
        state["paths"] = {**state["paths"], "flows_root": body["flows_root"], "enforced": body["enforced"], "source": "saved"}
        payload = state["paths"]
    elif path == "/api/system/paths/validate":
        payload = {"ok": True, **state["paths"], "flows_outside_root": [
            {"name": "Regional orders", "reason": "Outside the root", "target_folder": "E:\\old\\orders"}]}
    elif path == "/api/system/flows" and method == "GET":
        payload = state["flows"]
    elif path == "/api/system/flows" and method == "PUT":
        state["flows"] = {**state["flows"], **body}
        payload = state["flows"]
    elif path == "/api/system/flows/portals/7" and method == "PUT":
        state["flows"]["portals"][0]["capacity"] = body["capacity"]
        payload = state["flows"]
    elif path == "/api/system/flows/start":
        payload = {"slots": [{"worker_id": "fixture-1", "status": "starting"}]}
    elif path == "/api/usage/premium-viewers" and method == "GET":
        payload = {"weight": 5, "viewers": [{"email": email} for email in state["viewers"]]}
    elif path == "/api/usage/premium-viewers" and method == "PUT":
        state["viewers"] = list(body["emails"])
        payload = {"status": "saved", "count": len(state["viewers"]), "weight": 5}
    elif path == "/api/ai/settings" and method == "GET":
        payload = state["ai"]
    elif path == "/api/ai/settings" and method == "PUT":
        state["ai"] = {**state["ai"], **{key: value for key, value in body.items() if key in state["ai"]}}
        payload = state["ai"]
    elif path == "/api/ai/settings/models":
        payload = {"models": ["fixture-model", "fixture-model-large"]}
    elif path == "/api/system/updates":
        payload = {"version": "settings-sections-fixture", "current_commit": "abc123def0", "latest_commit": "abc123def0",
                   "up_to_date": True, "updater_ready": True, "auto_update": {"enabled": True, "interval_seconds": 90}}
    elif path == "/api/people":
        payload = []
    elif path == "/api/flows/catalog":
        payload = {"sites": [], "reports": [], "control_types": ["select", "text"], "asap_download_types": []}
    elif path == "/api/flows/estimates":
        payload = {}
    elif path == "/api/flows/sql/catalog":
        payload = {"configured": False, "missing": [], "targets": [], "scan": {"status": "never_scanned"}}
    elif path == "/api/flows/activity":
        payload = {"latest_runs": [], "active_runs": [], "workers": {"total": 0, "online": 0}, "schedule_date": "2026-11-11"}
    elif path in {"/api/flows", "/api/flows/runs", "/api/flows/workers", "/api/flows/scans"}:
        payload = []
    else:
        state["unexpected"].append((method, path))
        route.fulfill(status=404, json={"detail": "Unexpected fixture request"})
        return
    route.fulfill(status=200, json=payload)


@pytest.fixture
def settings_browser(tmp_path):
    """Yields open(address, me=HOST) -> (page, state). Each call is a fresh page load."""
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "app")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/static/index.html"
    opened = []
    try:
        with sync_playwright() as playwright:
            browser = launch(playwright)

            def open_page(address, me=HOST):
                state = _fresh_state(me)
                page = browser.new_page(viewport={"width": 1360, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/**", partial(_respond, state))
                page.goto(base + address)
                opened.append((page, state, errors))
                return page, state

            yield open_page
            for page, state, errors in opened:
                assert not errors, errors
                assert not state["unexpected"], state["unexpected"]
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _shot(page, name):
    folder = os.environ.get("METRONOME_UI_EVIDENCE_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"settings-sections-{name}.png"), full_page=True)


def _expect_address(page, ending):
    expect(page).to_have_url(re.compile(re.escape(ending) + "$"))


def _rail(page, section):
    return page.locator(f'#st-rail [data-st-section="{section}"]')


def _expect_section(page, section, name, control):
    """Settings is the page, `section` is the open one, and the moved page's own form is there."""
    expect(page.get_by_role("heading", level=1)).to_have_text(["Settings"])
    expect(_rail(page, section)).to_have_attribute("aria-current", "true")
    expect(page.locator("#st-rail [aria-current='true']")).to_have_count(1)
    expect(page.locator("#st-embed").get_by_role("heading", level=2, name=name, exact=True)).to_be_visible()
    expect(page.locator(control)).to_be_visible()
    expect(page.locator("#st-panel")).to_be_hidden()
    _expect_address(page, f"#settings/{section}")
    expect(page.locator('nav a[data-page="settings"]')).to_have_class("active")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), \
        "the page scrolls sideways at 1360px"


def _calls(state, method, path):
    return [body for called_method, called_path, body in state["calls"] if (called_method, called_path) == (method, path)]


def test_system_menu_no_longer_offers_the_moved_pages(settings_browser):
    page, _state = settings_browser("#settings")
    expect(page.locator("#st-page")).to_be_visible()
    menu = page.locator('nav .nav-group[aria-label="System pages"]')
    assert menu.locator('a[role="menuitem"]').all_text_contents() == [
        "Settings", "Changelog", "Event Log", "FAQ", "Scanner", "Updates"]
    for section, _name, _control in MOVED:
        expect(page.locator(f'nav a[data-page="{section}"]')).to_have_count(0)
    _shot(page, "system-menu")

    # Updates stayed a System page and kept the entry other code looks up by id.
    menu.locator(".nav-group-label").hover()
    page.locator("#btn-update-app").click()
    expect(page.get_by_role("heading", level=1, name="Updates", exact=True)).to_be_visible()
    _expect_address(page, "#updates")
    expect(page.locator("#btn-update-app")).to_have_class("active")
    expect(menu).to_have_class("nav-group active")


def test_settings_rail_offers_the_moved_pages_as_sections(settings_browser):
    page, state = settings_browser("#settings")
    expect(page.locator("#st-rail button")).to_have_text([
        "Folders", "Flows", "Updates, backups and logs", "AI", "Paths", "Flow workers", "Premium Viewers"])
    # The moved pages sit in their own labelled group, below the registry sections.
    order = page.locator("#st-rail > *").evaluate_all(
        "nodes => nodes.map(node => node.matches('button') ? node.dataset.stSection : node.textContent.trim())")
    assert order == ["folders", "flows", "updates", "More settings", "ai", "paths", "flow-settings", "premiumviewers"]
    # Plain #settings opens the first registry section; no moved page is fetched until asked for.
    expect(_rail(page, "folders")).to_have_attribute("aria-current", "true")
    _expect_address(page, "#settings/folders")
    assert not _calls(state, "GET", "/api/system/paths") and not _calls(state, "GET", "/api/system/flows")

    for section, name, control in MOVED:
        _rail(page, section).click()
        _expect_section(page, section, name, control)
        _shot(page, section)
    # Back to a registry section: its panel returns, the moved page is gone.
    _rail(page, "flows").click()
    expect(page.locator("#st-panel").get_by_role("heading", level=2, name="Flows", exact=True)).to_be_visible()
    expect(page.locator("#st-embed")).to_be_hidden()
    expect(page.locator("#premium-viewers-input")).to_have_count(0)
    _expect_address(page, "#settings/flows")


@pytest.mark.parametrize("section,name,control", MOVED)
def test_old_address_opens_settings_at_that_section(settings_browser, section, name, control):
    # Opened from a bookmark.
    page, _state = settings_browser(f"#{section}")
    _expect_section(page, section, name, control)

    # The old address typed while somewhere else in the app. It is rewritten in
    # place, so Back returns to where the user was instead of landing on the
    # old address and being sent forward again.
    page.evaluate("navigate('faq')")
    expect(page.get_by_role("heading", level=1, name="FAQ", exact=True)).to_be_visible()
    page.evaluate(f"location.hash = '#{section}'")
    _expect_section(page, section, name, control)
    page.go_back()
    expect(page.get_by_role("heading", level=1, name="FAQ", exact=True)).to_be_visible()
    _expect_address(page, "#faq")

    # Reached through navigate(), as the pages' own code and links do.
    page.evaluate(f"navigate('{section}')")
    _expect_section(page, section, name, control)
    page.go_back()
    expect(page.get_by_role("heading", level=1, name="FAQ", exact=True)).to_be_visible()


def test_registry_sections_keep_their_place_and_have_addresses(settings_browser):
    page, _state = settings_browser("#settings/flows")
    expect(page.locator("#st-panel").get_by_role("heading", level=2, name="Flows", exact=True)).to_be_visible()
    expect(_rail(page, "flows")).to_have_attribute("aria-current", "true")
    expect(page.locator('[data-st-key="flows.attempts"]')).to_be_visible()

    # A pointer from a registry section to a moved page opens that section in place.
    page.get_by_role("link", name="Worker status and per-portal limits").click()
    _expect_section(page, "flow-settings", "Flow workers", "#flow-capacity-form")
    page.go_back()
    expect(page.locator("#st-panel").get_by_role("heading", level=2, name="Flows", exact=True)).to_be_visible()
    _expect_address(page, "#settings/flows")
    # A pointer to a page that stayed in the System menu still goes to that page.
    _rail(page, "updates").click()
    page.get_by_role("link", name="Install now and update history").click()
    expect(page.get_by_role("heading", level=1, name="Updates", exact=True)).to_be_visible()
    # Returning through the menu reopens Settings where it was left.
    page.locator('nav .nav-group[aria-label="System pages"] .nav-group-label').hover()
    page.locator('nav a[data-page="settings"]').click()
    expect(_rail(page, "updates")).to_have_attribute("aria-current", "true")

    # An address that names no section falls back to the first one.
    other, _other_state = settings_browser("#settings/nowhere")
    expect(_rail(other, "folders")).to_have_attribute("aria-current", "true")
    _expect_address(other, "#settings/folders")


def test_paths_saves_through_its_api_and_recovers_from_a_refusal(settings_browser):
    page, state = settings_browser("#settings/paths")
    _expect_section(page, "paths", "Paths", "#paths-form")
    expect(page.locator("#paths-root")).to_have_value("D:\\Metronome\\flows")

    page.locator("#paths-root").fill("E:\\Flows")
    page.locator("#paths-enforced").check()
    page.get_by_role("button", name="Check impact").click()
    expect(page.locator("#paths-impact")).to_contain_text("Regional orders")
    assert _calls(state, "POST", "/api/system/paths/validate") == [{"flows_root": "E:\\Flows", "create": True, "enforced": True}]

    # The server refuses: its sentence is shown beside the button and nothing typed is lost.
    state["fail"][("PUT", "/api/system/paths")] = (409, "Wait for queued and active Flow runs to finish before changing paths.")
    page.get_by_role("button", name="Save paths").click()
    expect(page.locator("#paths-error")).to_have_text("Wait for queued and active Flow runs to finish before changing paths.")
    expect(page.locator("#paths-root")).to_have_value("E:\\Flows")
    expect(page.locator("#paths-enforced")).to_be_checked()
    expect(page.get_by_role("button", name="Save paths")).to_be_enabled()
    _shot(page, "paths-refused")

    # Trying again succeeds, says so, and stays on the Paths section with the saved value.
    page.get_by_role("button", name="Save paths").click()
    expect(page.locator(".toast")).to_have_text("Flow paths saved")
    assert _calls(state, "PUT", "/api/system/paths")[-1] == {"flows_root": "E:\\Flows", "create": True, "enforced": True}
    expect(page.locator("#paths-form")).to_contain_text("Value from saved.")
    expect(page.locator("#paths-root")).to_have_value("E:\\Flows")
    _expect_section(page, "paths", "Paths", "#paths-form")


def test_flow_workers_saves_through_its_api(settings_browser):
    page, state = settings_browser("#flow-settings")
    _expect_section(page, "flow-settings", "Flow workers", "#flow-capacity-form")
    page.locator("#flow-headed-capacity").select_option("3")
    page.locator("#flow-recording-wait").fill("25")
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".toast")).to_have_text("Flows settings saved")
    assert _calls(state, "PUT", "/api/system/flows") == [{
        "total_capacity": 12, "headless_capacity": 2, "headed_capacity": 3, "browser_channel": "chrome",
        "recording_wait_seconds": 25}]
    _expect_section(page, "flow-settings", "Flow workers", "#flow-capacity-form")
    expect(page.locator("#flow-headed-capacity")).to_have_value("3")
    expect(page.locator("#flow-recording-wait")).to_have_value("25")

    portal = page.locator(".flow-portal-capacity")
    portal.get_by_label("Fixture portal concurrent operations").select_option("6")
    portal.get_by_role("button", name="Save portal settings").click()
    expect(page.locator(".toast").filter(has_text="Portal limit saved")).to_be_visible()
    assert _calls(state, "PUT", "/api/system/flows/portals/7") == [{"capacity": 6}]

    page.get_by_role("button", name="Start visible workers").click()
    expect(page.locator("#flow-capacity-result")).to_have_text("fixture-1: starting")

    # The registry shows the same value under Flows > Workers; it is read again, not left stale.
    _rail(page, "flows").click()
    expect(page.locator('[data-st-key="flows.visible_windows"]')).to_have_value("3")
    _rail(page, "flow-settings").click()
    _expect_section(page, "flow-settings", "Flow workers", "#flow-capacity-form")

    # A refused save says why beside the buttons and keeps the choice.
    page.locator("#flow-headed-capacity").select_option("5")
    state["fail"][("PUT", "/api/system/flows")] = (403, f"Only the host PC can change Flows settings. {HOST_ONLY}")
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#flow-capacity-result")).to_have_text(f"Only the host PC can change Flows settings. {HOST_ONLY}")
    expect(page.locator("#flow-headed-capacity")).to_have_value("5")
    expect(page.get_by_role("button", name="Save settings")).to_be_enabled()


def test_a_registry_save_reaches_the_open_flow_workers_form(settings_browser):
    # The two hold the same value. Saved from the bar while the form is open,
    # it must not be sent back as it was by the form's own Save.
    page, state = settings_browser("#settings/flows")
    page.locator('[data-st-key="flows.visible_windows"]').fill("4")
    _rail(page, "flow-settings").click()
    _expect_section(page, "flow-settings", "Flow workers", "#flow-capacity-form")
    expect(page.locator("#flow-headed-capacity")).to_have_value("1")
    page.locator("#flow-recording-wait").fill("25")  # typed into the form, not saved yet

    page.get_by_role("button", name="Save changes").click()
    expect(page.locator(".toast")).to_contain_text("Saved 1 setting")
    expect(page.locator("#flow-headed-capacity")).to_have_value("4")
    expect(page.locator("#flow-recording-wait")).to_have_value("25")

    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".toast").filter(has_text="Flows settings saved")).to_be_visible()
    assert _calls(state, "PUT", "/api/system/flows")[-1]["headed_capacity"] == 4
    assert state["flows"]["headed_capacity"] == 4


def test_a_paused_portal_sign_in_is_announced_where_it_is_corrected_and_try_again_resumes_it(settings_browser):
    # The portal refused the saved password once; no worker submits it again.
    # Settings says so beside the fields that correct it.
    page, state = settings_browser("#settings")
    state["portal"].update(source="settings_file", paused=True, paused_refused=True, paused_portal="ASAP",
                           paused_at="2026-11-12T05:14:00+00:00", failed_attempts=1)
    page.reload()
    page.locator("#st-search").fill("portal")
    notice = page.get_by_role("alert").filter(has_text="The portal sign-in is paused.")
    expect(notice).to_contain_text("ASAP refused the user ID or password")
    expect(notice).to_contain_text("the account cannot be locked by repeated attempts")

    notice.get_by_role("button", name="Try again").click()
    expect(page.locator(".toast")).to_contain_text("will be tried again")
    expect(page.get_by_text("The portal sign-in is paused.")).to_have_count(0)
    assert _calls(state, "POST", "/api/system/settings/portal-login/resume") == [None]


def test_flows_page_keeps_its_own_settings_view(settings_browser):
    page, state = settings_browser("#flows")
    expect(page.get_by_role("heading", level=1, name="Flows", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Settings").click()
    expect(page.locator("#flow-capacity-form")).to_be_visible()
    page.locator("#flow-headless-capacity").select_option("4")
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".toast")).to_have_text("Flows settings saved")
    assert _calls(state, "PUT", "/api/system/flows")[0]["headless_capacity"] == 4
    # Saving there stays on Flows > Settings; it does not jump to System > Settings.
    expect(page.locator("#flow-headless-capacity")).to_have_value("4")
    expect(page.get_by_role("tab", name="Settings")).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("heading", level=1, name="Flows", exact=True)).to_be_visible()
    _expect_address(page, "#flows")
    expect(page.locator("#st-page")).to_have_count(0)


def test_premium_viewers_saves_through_its_api(settings_browser):
    page, state = settings_browser("#premiumviewers")
    _expect_section(page, "premiumviewers", "Premium Viewers", "#premium-viewers-input")
    expect(page.locator("#st-embed .page-header")).to_contain_text("1 premium viewer - 5x impact weight")
    page.locator("#premium-viewers-input").fill("ana@example.test\nben@example.test")

    state["fail"][("PUT", "/api/usage/premium-viewers")] = (503, "Connection interrupted. Try again.")
    page.get_by_role("button", name="Save Premium Viewers").click()
    expect(page.locator(".toast")).to_have_text("Save failed: Connection interrupted. Try again.")
    expect(page.locator("#premium-viewers-input")).to_have_value("ana@example.test\nben@example.test")
    expect(page.get_by_role("button", name="Save Premium Viewers")).to_be_enabled()

    page.get_by_role("button", name="Save Premium Viewers").click()
    expect(page.locator(".toast").filter(has_text="Saved 2 premium viewers")).to_be_visible()
    assert _calls(state, "PUT", "/api/usage/premium-viewers")[-1] == {"emails": ["ana@example.test", "ben@example.test"]}
    _expect_section(page, "premiumviewers", "Premium Viewers", "#premium-viewers-input")
    expect(page.locator("#st-embed .page-header")).to_contain_text("2 premium viewers - 5x impact weight")


def test_premium_viewers_is_redrawn_once_the_user_is_known(settings_browser):
    """The menu entry's page was redrawn when this browser's user became known; the section still is."""
    page, state = settings_browser("#premiumviewers", me={**HOST, "person_id": None, "name": None})
    expect(page.locator(".register-modal")).to_be_visible()
    expect(page.locator("#premium-viewers-input")).to_be_attached()
    page.wait_for_load_state("networkidle")
    before = len(_calls(state, "GET", "/api/usage/premium-viewers"))
    state["viewers"] = ["ana@example.test", "dev@example.test"]
    page.get_by_role("button", name="Continue as host PC").click()
    expect(page.locator(".register-modal")).to_have_count(0)
    expect(page.locator("#premium-viewers-input")).to_have_value("ana@example.test\ndev@example.test")
    assert len(_calls(state, "GET", "/api/usage/premium-viewers")) == before + 1
    _expect_section(page, "premiumviewers", "Premium Viewers", "#premium-viewers-input")


def test_ai_saves_through_its_api(settings_browser):
    page, state = settings_browser("#ai")
    _expect_section(page, "ai", "AI", "#ai-settings-form")
    # Opening the section loads the provider's models, as opening the page did.
    expect(page.locator("#ai-models-status")).to_have_text("2 models available from /models.")
    page.locator("#ai-feature-documentation").check()
    page.locator('input[name="ai-mode"][value="disabled"]').check()

    state["fail"][("PUT", "/api/ai/settings")] = (403, "AI connection settings can be changed only on this Metronome computer.")
    page.get_by_role("button", name="Save AI settings").click()
    expect(page.locator(".toast")).to_have_text(
        "AI settings were not saved: AI connection settings can be changed only on this Metronome computer.")
    expect(page.locator("#ai-feature-documentation")).to_be_checked()
    expect(page.get_by_role("button", name="Save AI settings")).to_be_enabled()

    page.get_by_role("button", name="Save AI settings").click()
    expect(page.locator(".toast").filter(has_text="AI settings saved for new work")).to_be_visible()
    saved = _calls(state, "PUT", "/api/ai/settings")[-1]
    assert saved["mode"] == "disabled" and saved["documentation_suggestions_enabled"] is True
    assert saved["endpoint"] == "http://ai.example.test/v1/chat/completions" and saved["model"] == "fixture-model"
    _expect_section(page, "ai", "AI", "#ai-settings-form")
    expect(page.locator("#st-embed .page-header .badge")).to_have_text("Disabled")


def test_another_computer_is_told_what_holds_for_each_section(settings_browser):
    page, state = settings_browser("#settings", me=COLLEAGUE)
    # Registry settings: read-only, as before.
    expect(page.locator(".st-notice")).to_contain_text("Only the host PC can change settings.")
    expect(page.locator('[data-st-key="folders.depth"]')).to_be_disabled()

    # Paths: the form is as usable as on its former page, the notice says who can save,
    # and the server's refusal is shown where the page always showed it.
    _rail(page, "paths").click()
    _expect_section(page, "paths", "Paths", "#paths-form")
    expect(page.locator(".st-notice")).to_have_text(
        f"Only the host PC can save changes to Paths. You are looking from another computer. {HOST_ONLY}")
    expect(page.locator("#paths-root")).to_be_enabled()
    page.locator("#paths-root").fill("E:\\Flows")
    state["fail"][("PUT", "/api/system/paths")] = (403, f"Only the host PC can change where Flows keep their files. {HOST_ONLY}")
    page.get_by_role("button", name="Save paths").click()
    expect(page.locator("#paths-error")).to_have_text(f"Only the host PC can change where Flows keep their files. {HOST_ONLY}")
    expect(page.locator("#paths-root")).to_have_value("E:\\Flows")
    _shot(page, "paths-another-computer")

    for section in ("ai", "flow-settings"):
        _rail(page, section).click()
        expect(page.locator(".st-notice")).to_contain_text("Only the host PC can save changes to")
    # Premium Viewers can be saved from any computer, as before: no notice, and the save goes through.
    _rail(page, "premiumviewers").click()
    _expect_section(page, "premiumviewers", "Premium Viewers", "#premium-viewers-input")
    expect(page.locator(".st-notice")).to_have_count(0)
    page.locator("#premium-viewers-input").fill("cara@example.test")
    page.get_by_role("button", name="Save Premium Viewers").click()
    expect(page.locator(".toast")).to_have_text("Saved 1 premium viewer")
    assert _calls(state, "PUT", "/api/usage/premium-viewers") == [{"emails": ["cara@example.test"]}]


def test_search_finds_a_moved_page_and_unsaved_work_survives(settings_browser):
    page, state = settings_browser("#settings")
    # An unsaved registry edit is counted in the rail and the save bar.
    page.locator('[data-st-key="folders.depth"]').fill("5")
    expect(page.locator(".st-savebar-message")).to_have_text("1 unsaved change")

    _rail(page, "paths").click()
    _expect_section(page, "paths", "Paths", "#paths-form")
    expect(page.locator(".st-savebar-message")).to_have_text("1 unsaved change in other sections")
    expect(_rail(page, "folders")).to_contain_text("1 unsaved")
    page.locator("#paths-root").fill("E:\\Half typed")

    # Searching hides the Paths form without discarding it, and offers the matching moved page.
    page.locator("#st-search").fill("worker")
    expect(page.locator("#paths-form")).to_be_hidden()
    result = page.locator("#st-panel").get_by_role("link", name="Flow workers", exact=True)
    expect(result).to_be_visible()
    expect(page.locator("#st-panel").get_by_role("link", name="Premium Viewers")).to_have_count(0)
    page.locator("#st-search").fill("")
    expect(page.locator("#paths-root")).to_have_value("E:\\Half typed")
    _expect_section(page, "paths", "Paths", "#paths-form")

    # Following a search result opens that section and clears the search.
    page.locator("#st-search").fill("worker")
    result.click()
    _expect_section(page, "flow-settings", "Flow workers", "#flow-capacity-form")
    expect(page.locator("#st-search")).to_have_value("")

    # The registry edit is still waiting where it was made, and saves from there.
    _rail(page, "folders").click()
    expect(page.locator('[data-st-key="folders.depth"]')).to_have_value("5")
    expect(page.locator(".st-savebar-message")).to_have_text("1 unsaved change")
    assert not _calls(state, "PUT", "/api/system/settings")


def test_a_moved_page_that_cannot_load_leaves_settings_usable(settings_browser):
    page, state = settings_browser("#settings")
    state["fail"][("GET", "/api/system/paths")] = (500, "The paths could not be read.")
    _rail(page, "paths").click()
    problem = page.locator("#st-embed [role='alert']")
    expect(problem.get_by_role("heading", level=2)).to_have_text("Could not load Paths")
    expect(problem).to_contain_text("The paths could not be read.")
    expect(page.locator("#st-rail button")).to_have_count(7)
    expect(_rail(page, "paths")).to_have_attribute("aria-current", "true")
    _shot(page, "paths-could-not-load")

    problem.get_by_role("button", name="Try again").click()
    _expect_section(page, "paths", "Paths", "#paths-form")
    # The same holds when the address itself opens the failing section.
    other, other_state = settings_browser("about:blank")
    other_state["fail"][("GET", "/api/system/flows")] = (500, "Workers could not be read.")
    other.goto(page.url.split("#")[0] + "#flow-settings")
    expect(other.locator("#st-embed [role='alert']")).to_contain_text("Workers could not be read.")
    expect(other.get_by_role("heading", level=1)).to_have_text(["Settings"])
    _rail(other, "folders").click()
    expect(other.locator('[data-st-key="folders.depth"]')).to_be_visible()
