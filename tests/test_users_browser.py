"""Production Users UI with fictional HTTP responses; no live app or SQL access."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import os
import re
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def users_browser():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "app"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = {"people": [
        {"id": 1, "name": "Maya Chen", "role": "BI", "email": "maya@example.test", "sql_username": "maya_chen"},
        {"id": 2, "name": "Omar Malik", "role": "Business", "email": "omar@example.test", "sql_username": None},
        {"id": 3, "name": "Sofia Rivera", "role": "BI", "email": None, "sql_username": None},
    ], "next_id": 4, "fail_next": None, "calls": [], "unexpected": [], "defer": False}
    base = f"http://127.0.0.1:{server.server_port}/static/index.html"

    def respond(route):
        request = route.request
        path = urlsplit(request.url).path
        method = request.method
        state["calls"].append((method, path))
        if path.startswith("/api/people") and state["fail_next"] == method:
            state["fail_next"] = None
            route.fulfill(status=503, json={"detail": "Connection interrupted. Try again."})
            return
        if path == "/api/people" and method == "GET":
            payload = state["people"]
        elif path.startswith("/api/people") and method in {"POST", "PATCH", "DELETE"}:
            if state["defer"]:
                state["pending"] = route
                return
            if method == "POST":
                payload = {**request.post_data_json, "id": state["next_id"]}
                state["next_id"] += 1
                state["people"].append(payload)
            elif method == "PATCH":
                payload = next(person for person in state["people"] if person["id"] == int(path.rsplit("/", 1)[1]))
                payload.update(request.post_data_json)
            else:
                person_id = int(path.rsplit("/", 1)[1])
                state["people"] = [person for person in state["people"] if person["id"] != person_id]
                payload = {"status": "deleted", "id": person_id}
        elif path == "/api/me":
            payload = {"name": "Fictional Tester", "is_local": True, "is_admin": True}
        elif path == "/api/version":
            payload = {"version": "users-ui-fixture", "up_to_date": True}
        elif path == "/api/ai/settings":
            payload = {"mode": "disabled", "operations_investigator_enabled": False}
        elif path == "/api/system/remote-flow-control":
            payload = {"enabled": False}
        elif path == "/api/create/options":
            payload = {"source_types": ["postgresql"], "upstream_types": ["database"], "existing_upstream_systems": [], "existing_sources": []}
        elif path == "/api/create/custom-entries":
            payload = []
        elif path == "/api/data-quality/checks":
            payload = [{"id": 91, "name": "Fixture row count", "source_name": "Fixture source",
                        "type": "row_count_range", "config": {"min_rows": 1}, "enabled": True,
                        "severity": "critical", "latest_status": "pass", "latest_value": 42}]
        elif path == "/api/data-quality/types":
            payload = {"types": ["row_count_range"]}
        elif path == "/api/dashboard":
            payload = {"reports_total": 0, "sources_total": 0, "sources_fresh": 0, "sources_stale": 0, "sources_outdated": 0, "sources_unknown": 0, "alerts_active": 0, "last_scan": None}
        elif path in {"/api/sources", "/api/reports", "/api/actions", "/api/schedules/health-trend", "/api/dashboard/user-activity"}:
            payload = []
        else:
            state["unexpected"].append((method, path))
            route.fulfill(status=404, json={"detail": "Unexpected fixture request"})
            return
        route.fulfill(status=201 if method == "POST" else 200, json=payload)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", respond)
            page.goto(base + "#users")
            expect(page.get_by_role("heading", name="Users", exact=True)).to_be_visible()
            yield page, state, base
            assert not errors, errors
            assert not state["unexpected"], state["unexpected"]
            print("Users browser:", browser.version, "UTC:", datetime.now(timezone.utc).isoformat())
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_users_profile_journey(users_browser, tmp_path):
    page, state, _base = users_browser
    evidence = Path(os.environ.get("METRONOME_UI_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    expect(page.get_by_role("heading", name="Users", exact=True)).to_be_visible()
    expect(page.get_by_role("region", name="User directory")).to_be_visible()
    expect(page.locator('#app')).to_contain_text('next successful SQL load, including existing tables')
    rows = page.locator("#users-list tbody tr")
    expect(rows).to_have_count(3)
    expect(page.locator("#users-count")).to_have_text("3 users · 1 SQL identity linked")
    search = page.get_by_role("searchbox", name="Search users")

    # Search all three supported fields, clear empty results, combine role/query.
    for query, expected in [("MAYA", "Maya Chen"), ("omar@example.test", "Omar Malik"),
                            ("maya_chen", "Maya Chen")]:
        search.fill(query)
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text(expected)
    search.fill("no-match")
    expect(page.locator("#users-list")).to_contain_text("No matching users.")
    expect(page.locator("#users-list")).to_contain_text("Try another name, email or SQL username")
    search.fill("")
    for label, count in [("Business", 1), ("BI", 2), ("Everyone", 3)]:
        button = page.get_by_role("button", name=label, exact=True)
        button.click()
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(page.locator("[data-user-role][aria-pressed=true]")).to_have_count(1)
        expect(rows).to_have_count(count)
    page.get_by_role("button", name="Business", exact=True).click()
    search.fill("Maya")
    expect(page.locator("#users-list")).to_contain_text("No matching users.")
    page.get_by_role("button", name="Everyone", exact=True).click()
    expect(rows).to_have_count(1)
    search.fill("")

    # Keyboard activation, accessible field labels, tab sequence and cancellation.
    add = page.get_by_role("button", name="Add user", exact=True)
    add.focus()
    page.keyboard.press("Enter")
    name = page.get_by_label("Name", exact=True)
    role = page.get_by_label("Role", exact=True)
    email = page.get_by_label(re.compile(r"^Email"))
    sql = page.get_by_label(re.compile(r"^SQL username"))
    expect(page.locator('#user-editor')).to_contain_text('Use an existing PostgreSQL role')
    expect(page.locator('#user-editor')).to_contain_text('Clearing this field leaves existing table ownership unchanged')
    expect(name).to_be_focused()
    for field in [role, email, sql]:
        page.keyboard.press("Tab")
        expect(field).to_be_focused()
    name.fill("Discard this draft")
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#user-editor")).to_be_empty()
    expect(add).to_be_focused()
    expect(rows).to_have_count(3)

    add.click()
    save = page.locator("#user-save")
    save.click()
    expect(name).to_be_focused()
    assert name.evaluate("element => !element.validity.valid"), "Empty name accepted"
    name.fill("   ")
    save.click()
    expect(page.locator("#user-form-feedback")).to_have_text("Enter a name.")
    name.fill("Test Colleague")
    email.fill("invalid-email")
    save.click()
    assert email.evaluate("element => !element.validity.valid"), "Invalid email accepted"
    expect(rows).to_have_count(3)
    email.fill("colleague@example.test")
    sql.fill("é" * 32)
    save.click()
    expect(page.locator("#user-form-feedback")).to_have_text("SQL username must be 63 UTF-8 bytes or fewer.")
    sql.fill("colleague_sql")

    # Fail the actual HTTP request once, then recover with the same form.
    state["fail_next"] = "POST"
    save.click()
    expect(page.locator("#user-form-feedback")).to_contain_text("Your changes are still here")
    expect(name).to_have_value("Test Colleague")
    expect(email).to_have_value("colleague@example.test")
    expect(sql).to_have_value("colleague_sql")
    expect(save).to_be_enabled()
    expect(page.get_by_role("button", name="Cancel", exact=True)).to_be_enabled()
    expect(rows).to_have_count(3)
    save.click()
    expect(page.locator("#users-feedback")).to_have_text("Test Colleague added.")
    expect(page.locator("#users-feedback")).to_have_attribute("role", "status")
    expect(page.locator("#users-feedback")).to_have_attribute("aria-live", "polite")
    expect(rows).to_have_count(4)
    expect(page.locator("#users-count")).to_have_text("4 users · 2 SQL identities linked")

    # Edits can be cancelled; optional email/SQL values can be cleared on retry.
    page.get_by_role("button", name="Edit Test Colleague").click()
    name.fill("Unsaved rename")
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_role("button", name="Edit Test Colleague")).to_be_visible()
    page.get_by_role("button", name="Edit Test Colleague").click()
    role.select_option("Business")
    sql.fill("")
    email.fill("")
    state["fail_next"] = "PATCH"
    save.click()
    expect(page.locator("#user-form-feedback")).to_contain_text("Your changes are still here")
    expect(role).to_have_value("Business")
    expect(sql).to_have_value("")
    save.click()
    expect(page.locator("#users-feedback")).to_have_text("Test Colleague updated.")
    updated = rows.filter(has_text="Test Colleague")
    for text in ["Business", "Not linked", "Not added"]:
        expect(updated).to_contain_text(text)
    expect(page.locator("#users-count")).to_have_text("4 users · 1 SQL identity linked")
    page.get_by_role("button", name="Business", exact=True).click()
    expect(rows).to_have_count(2)
    page.get_by_role("button", name="Everyone", exact=True).click()

    # Confirm deletion consequences, safe focus, cancel, failure and retry.
    page.get_by_role("button", name="Edit Test Colleague").click()
    page.get_by_role("button", name="Delete user", exact=True).click()
    expect(page.locator("#user-delete-confirm")).to_contain_text("owner set to Unassigned")
    expect(page.locator("#user-delete-confirm")).to_contain_text("SQL username will be removed")
    expect(page.get_by_role("button", name="Keep user")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#user-delete-confirm")).to_be_empty()
    expect(page.get_by_role("heading", name="Edit user", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Delete user", exact=True)).to_be_focused()
    expect(page.get_by_role("button", name="Edit Test Colleague")).to_be_visible()
    page.get_by_role("button", name="Delete user", exact=True).click()
    state["fail_next"] = "DELETE"
    page.locator("#user-delete-yes").click()
    expect(page.locator("#user-delete-feedback")).to_contain_text("Could not delete")
    expect(page.locator("#user-delete-yes")).to_be_enabled()
    expect(page.get_by_role("button", name="Keep user")).to_be_enabled()
    expect(page.get_by_role("button", name="Edit Test Colleague")).to_be_visible()
    page.locator("#user-delete-yes").click()
    expect(page.locator("#users-feedback")).to_have_text("Test Colleague deleted. Their flows were kept.")
    expect(rows).to_have_count(3)
    expect(page.get_by_role("button", name="Edit Test Colleague")).to_have_count(0)
    expect(add).to_be_focused()
    page.get_by_role("button", name="Edit Maya Chen").click()
    expect(sql).to_have_value("maya_chen")
    page.screenshot(path=str(evidence / "users-desktop.png"), full_page=True)
    page.get_by_role("button", name="Cancel", exact=True).click()

    # Mobile checks stay on Users, including editor and inline confirmation.
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Users directory overflows"
    search.fill("maya_chen")
    expect(rows).to_have_count(1)
    page.get_by_role("button", name="Edit Maya Chen").click()
    expect(name).to_have_value("Maya Chen")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Users editor overflows"
    page.get_by_role("button", name="Delete user", exact=True).click()
    expect(page.get_by_role("button", name="Keep user")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Delete confirmation overflows"
    page.get_by_role("button", name="Keep user").click()
    page.screenshot(path=str(evidence / "users-mobile.png"), full_page=True)
    page.get_by_role("button", name="Cancel", exact=True).click()
    search.fill("")
    page.get_by_role("button", name="BI", exact=True).click()
    expect(rows).to_have_count(2)
    page.get_by_role("button", name="Everyone", exact=True).click()

    assert len(state["people"]) == 3
    assert not any("best-practices" in path for _method, path in state["calls"])
    print("Users screenshots:", evidence)
    for name in ("index.html", "app.js", "users.js", "users.css", "style.css"):
        print(name, hashlib.sha256((ROOT / "app/static" / name).read_bytes()).hexdigest())


def test_users_load_failure_empty_directory_and_navigation(users_browser):
    page, state, base = users_browser
    state["fail_next"] = "GET"
    page.reload()
    expect(page.get_by_role("heading", name="Could not load users")).to_be_visible()
    expect(page.get_by_role("button", name="Add user", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Try again").click()
    expect(page.locator("#users-list tbody tr")).to_have_count(3)
    state["people"] = []
    page.reload()
    expect(page.locator("#users-list")).to_contain_text("Give your flows a person to turn to.")
    expect(page.get_by_role("button", name="Add user", exact=True)).to_be_enabled()
    assert page.locator('nav a[data-page="users"]').count() == 1
    assert page.locator('nav a[data-page="bestpractices"]').count() == 0
    page.evaluate("navigate('create')")
    expect(page.get_by_role("heading", name="Create Artifacts", exact=True)).to_be_visible()
    for label in ["Report", "Data Source", "Upstream System"]:
        expect(page.get_by_role("button", name=label, exact=True)).to_be_visible()
    expect(page.locator('.create-tab, #people-name-input')).to_have_count(0)
    page.evaluate("navigate('dataquality')")
    expect(page.get_by_role("heading", name="Data Quality", exact=True)).to_be_visible()
    expect(page.locator('#dt-data-quality tbody tr').filter(has_text="Fixture row count")).to_contain_text("high")
    page.locator('nav a[data-page="users"]').click()
    expect(page.get_by_role("heading", name="Users", exact=True)).to_be_visible()
    # Old checker links fall back through the existing unknown-route behavior.
    page.goto(base + "?legacy=checker#bestpractices")
    expect(page.locator('.stat-label').filter(has_text="Active Alerts")).to_be_visible()
    expect(page.locator('#dashboard-alerts')).to_be_visible()
    assert not any("best-practices" in path for _method, path in state["calls"])


def test_users_pending_save_cannot_replace_editor_and_navigation_is_safe(users_browser):
    page, state, _base = users_browser
    page.get_by_role("button", name="Edit Maya Chen").click()
    page.get_by_label("Name", exact=True).fill("Maya Updated")
    state["defer"] = True
    page.locator("#user-save").click()
    expect(page.locator("#user-form")).to_have_attribute("aria-busy", "true")
    expect(page.locator("#users-add")).to_be_disabled()
    expect(page.get_by_role("button", name="Edit Omar Malik")).to_be_disabled()
    expect(page.get_by_role("button", name="Cancel", exact=True)).to_be_disabled()
    expect(page.locator("#user-form-feedback")).to_have_text("Saving user…")
    page.evaluate("openUserEditor()")
    expect(page.get_by_label("Name", exact=True)).to_have_value("Maya Updated")
    page.evaluate("navigate('create')")
    expect(page.get_by_role("heading", name="Create Artifacts", exact=True)).to_be_visible()
    route = state["pending"]
    payload = {**route.request.post_data_json, "id": 1}
    state["people"][0] = payload
    state["defer"] = False
    route.fulfill(json=payload)
    page.locator('nav a[data-page="users"]').click()
    expect(page.get_by_role("button", name="Edit Maya Updated")).to_be_visible()
    expect(page.locator("#user-editor")).to_be_empty()


def test_sql_owner_help_and_run_log_confirm_only_committed_changes(users_browser, tmp_path):
    page, _state, _base = users_browser
    owner = {'name': 'Maya Chen', 'email': None, 'sql_username': 'Maya <BI> "owner"'}
    help_text = page.evaluate('owner => _flowOwnerHelp(owner)', owner)
    assert '&lt;BI&gt;' in help_text and 'Already queued runs keep their saved owner' in help_text
    assert 'unchanged' in page.evaluate('_flowOwnerHelp(null)')
    assert 'will not be changed' in page.evaluate('_flowOwnerHelp({name:"Maya"})')
    page.goto('about:blank')
    page.set_content(f'<base href="{_base}"><body class="flow-log-body"><main class="flow-log-page" id="flow-run-log"></main></body>')
    page.add_style_tag(path=str(ROOT / 'app/static/style.css'))
    page.add_script_tag(path=str(ROOT / 'app/static/flow_run_log.js'))
    run = {'id': 42, 'flow_name': 'Fictional sales', 'status': 'failed',
           'job': {'sql_handoff': {'enabled': True, 'owner_username': owner['sql_username'],
                   'database': 'analytics', 'schema': 'public', 'table': 'sales', 'mode': 'replace'}},
           'sql_outcome': {'committed': False}, 'error': 'SQL ownership permission denied; PostgreSQL confirmed rollback.'}
    page.evaluate('run => render(run)', run)
    section = page.locator('section').filter(has=page.get_by_role('heading', name='SQL table ownership', exact=True))
    expect(section).to_contain_text('Requested owner: Maya <BI> "owner"')
    expect(section).to_contain_text('confirmed only after the SQL load commits')
    expect(page.get_by_role('heading', name='Final error')).to_be_visible()
    assert page.locator('bi').count() == 0
    evidence = Path(os.environ.get('METRONOME_UI_EVIDENCE_DIR', str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / 'sql-owner-rollback.png'), full_page=True)
    run.update(status='succeeded', error=None, sql_outcome={'committed': True, 'owner_username': owner['sql_username']})
    page.evaluate('run => render(run)', run)
    expect(section).to_contain_text('Committed table owner: Maya <BI> "owner"')
    expect(section).to_contain_text('Refresh table Properties in pgAdmin')
    expect(page.get_by_role('heading', name='Final error')).to_have_count(0)
    page.screenshot(path=str(evidence / 'sql-owner-committed.png'), full_page=True)
