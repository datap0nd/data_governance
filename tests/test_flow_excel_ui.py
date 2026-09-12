"""Real Flow controls and run logs with fictional HTTP data; no portal or SQL."""
from copy import deepcopy
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE_FLOW = {
    "id": 901, "name": "Regional orders", "source_type": "outlook",
    "outlook_subject_contains": "Regional orders", "enabled": False,
    "classification": "production", "execution_method": "catalog",
    "site_id": 91, "report_id": 91, "site_name": "Fixture reports",
    "file_format": "xlsx", "filename_template": "{flow}_{export}.xlsx",
    "period_strategy": "none", "download_mode": "single", "export_views": [],
    "local_file_path": "C:\\Fictional\\regional-orders.xlsx",
    "excel_worksheets": None, "excel_trim": "none", "transform_enabled": False,
    "sql_handoff_enabled": True, "sql_mode": "replace", "sql_database": "FixtureDB",
    "sql_schema": "reporting", "sql_table": "regional_orders", "sql_uppercase": False,
    "post_sql_refresh": {"mode": "off", "views": []}, "schedule_type": "manual",
    "schedule_days": [], "browser_mode": "headless", "output_mode": "run_folders",
    "email_delivery": {"enabled": False, "recipients": []},
}
EXCEL_ERROR = {
    "code": "excel_multiple_sheets", "workbook": "Regional-orders.xlsx",
    "available_sheets": [" North ", "South", "Totals"], "selected_sheets": [],
    "sql_started": False,
}


def fixture_server(source_type="outlook", port=0):
    state = {"flow": {**deepcopy(BASE_FLOW), "source_type": source_type},
             "writes": [], "errors": [], "fail_next": None}
    state["run"] = {
        "id": 902, "flow_id": 901, "flow_name": "Regional orders", "status": "failed",
        "error": "This Excel has more than one sheet. Please enable the option in Flows.",
        "events": [{"stage": "file_normalization_failed", "status": "failed",
                    "message": "Workbook has multiple worksheets.", "details": {"excel": EXCEL_ERROR}}],
        "job": {"sql_handoff": {"enabled": True, "mode": "replace"}},
        # A partial bundle must never advertise an SQL-only retry for a worksheet error.
        "artifacts": [{"file_path": "fixture/earlier.csv", "filename": "earlier.csv"}],
    }

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT / "app"), **kwargs)

        def log_message(self, *_args):
            pass

        def reply(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            payloads = {
                "/api/me": {"name": "Fictional Tester", "is_local": True, "is_admin": True},
                "/api/version": {"version": "worksheet-fixture", "up_to_date": True},
                "/api/ai/settings": {"mode": "disabled", "operations_investigator_enabled": False},
                "/api/system/remote-flow-control": {"enabled": False},
                "/api/flows/catalog": {
                    "sites": [{"id": 91, "name": "Fixture reports", "enabled": True, "adapter": "web_export"}],
                    "reports": [{"id": 91, "site_id": 91, "name": "Regional orders", "enabled": True,
                                 "filters": [], "automation": {}, "export_views": []}],
                    "asap_download_types": [],
                },
                "/api/flows": [state["flow"]], "/api/flows/runs": [],
                "/api/flows/workers": [], "/api/flows/scans": [],
                "/api/flows/estimates": {}, "/api/people": [],
                "/api/flows/activity": {"active_runs": [], "latest_runs": [], "workers": {"online": 0}},
                "/api/flows/sql/catalog": {"configured": True, "targets": [{
                    "database": "FixtureDB", "schema": "reporting", "table": "regional_orders"}], "scan": {}},
                "/api/flows/runs/902": state["run"],
            }
            if path in payloads:
                return self.reply(payloads[path])
            if path.startswith("/api/"):
                state["errors"].append(("GET", path))
                return self.reply({"detail": "Unexpected fictional request"}, 404)
            if path == "/":
                self.path = "/static/index.html"
            elif path == "/flow-runs/902":
                self.path = "/static/flow_run_log.html"
            return super().do_GET()

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/api/flows/901":
                state["errors"].append(("PUT", self.path))
                return self.reply({"detail": "Unexpected fictional request"}, 404)
            if state["fail_next"]:
                failure = state["fail_next"]
                state["fail_next"] = None
                return self.reply(failure[1], failure[0])
            state["writes"].append(body)
            state["flow"].update(body)
            return self.reply(state["flow"])

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, state


@pytest.fixture
def excel_ui(request, tmp_path):
    server, state = fixture_server(getattr(request, "param", "outlook"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            base = f"http://127.0.0.1:{server.server_port}"
            yield page, state, base
            assert not errors, errors
            assert not state["errors"], state["errors"]
            print("Worksheet UI:", browser.version, datetime.now(timezone.utc).isoformat())
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def edit(page, base):
    page.goto(base + "/?edit_flow=901#flows")
    expect(page.locator("#flow-excel-names")).to_be_visible()


def test_excel_failure_recovery_and_saved_choices(excel_ui, tmp_path):
    page, state, base = excel_ui
    evidence = Path(os.environ.get("METRONOME_UI_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    page.goto(base + "/flow-runs/902")
    expect(page.get_by_role("heading", name="Excel processing failed")).to_be_visible()
    expect(page.get_by_text("SQL was not started. The original workbook is preserved.")).to_be_visible()
    expect(page.get_by_role("button", name="Retry SQL only")).to_have_count(0)
    page.screenshot(path=str(evidence / "worksheet-error.png"), full_page=True)
    page.get_by_role("link", name="Choose worksheets").click()
    names = page.locator("#flow-excel-names")
    expect(names).to_be_visible()
    expect(page.locator("#flow-excel-enabled")).to_be_checked()
    expect(page.locator("#flow-excel-fields").get_by_role("radio")).to_have_count(2)
    save = page.get_by_role("button", name="Save changes", exact=True)
    save.click()
    expect(page.locator("#flow-excel-error")).to_have_text("Enter the worksheet names to load.")
    assert state["writes"] == []
    page.locator('[data-excel-sheet=" North "]').click()
    page.locator('[data-excel-sheet="South"]').click()
    expect(names).to_have_value(" North \nSouth")
    page.get_by_role("radio", name="Load one named worksheet").check()
    expect(names).to_have_value(" North \nSouth")  # switching never discards a name
    save.click()
    expect(page.locator("#flow-excel-error")).to_contain_text("exactly one")
    page.get_by_role("radio", name="Append named worksheets").check()
    names.fill("South\nSouth")
    save.click()
    expect(page.locator("#flow-excel-error")).to_contain_text("only once")
    names.fill(" North \nSouth")
    state["fail_next"] = (503, {"detail": "Connection interrupted. Try again."})
    save.click()
    expect(page.locator(".flow-form-error")).to_contain_text("Connection interrupted")
    expect(names).to_have_value(" North \nSouth")
    state["fail_next"] = (422, {"detail": [{"loc": ["body", "excel_worksheets"], "msg": "Worksheet choices rejected."}]})
    save.click()
    expect(page.locator("#flow-excel-error")).to_have_text("Worksheet choices rejected.")
    expect(names).to_have_value(" North \nSouth")
    page.screenshot(path=str(evidence / "worksheet-settings-desktop.png"), full_page=True)
    save.click()
    expect(page.locator("#flow-builder-form")).to_have_count(0)
    assert state["flow"]["excel_worksheets"] == {"mode": "append", "names": [" North ", "South"]}
    assert state["flow"]["sql_mode"] == "replace"
    assert state["flow"]["sql_table"] == "regional_orders"
    edit(page, base)
    expect(names).to_have_value(" North \nSouth")
    page.get_by_role("radio", name="Load one named worksheet").check()
    names.fill("Totals")
    save.click()
    expect(page.locator("#flow-builder-form")).to_have_count(0)
    assert state["flow"]["excel_worksheets"] == {"mode": "single", "names": ["Totals"]}
    edit(page, base)
    names.fill("Unsaved")
    page.get_by_role("button", name="Cancel", exact=True).click()
    assert state["flow"]["excel_worksheets"]["names"] == ["Totals"]
    edit(page, base)
    expect(names).to_have_value("Totals")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(names).to_be_visible()
    page.screenshot(path=str(evidence / "worksheet-settings-mobile.png"), full_page=True)
    assert page.locator("#flow-builder-form").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.locator("#flow-excel-enabled").uncheck()
    expect(names).to_be_hidden()
    save.click()
    expect(page.locator("#flow-builder-form")).to_have_count(0)
    assert state["flow"]["excel_worksheets"] is None
    assert state["flow"]["sql_mode"] == "replace"


@pytest.mark.parametrize("excel_ui", ["file", "portal"], indirect=True)
def test_excel_setting_in_each_builder_defaults_off(excel_ui):
    page, state, base = excel_ui
    page.goto(base + "/#flows")
    page.locator('.flow-group-toggle').first.click()
    page.locator('.flow-edit[data-id="901"]').click()
    page.get_by_role("button", name="After download").click()
    expect(page.locator("#flow-excel-enabled")).not_to_be_checked()
    expect(page.locator("#flow-excel-fields")).to_be_hidden()
    page.locator("#flow-excel-enabled").check()
    page.get_by_role("radio", name="Load one named worksheet").check()
    page.locator("#flow-excel-names").fill(" Exact sheet ")
    if state["flow"]["source_type"] == "file":
        page.get_by_role("button", name="Source", exact=False).first.click()
        page.locator("#flow-local-file-path").fill("C:\\Fictional\\regional.csv")
        page.get_by_role("button", name="After download").click()
        expect(page.locator("#flow-excel-worksheets")).to_be_hidden()
        page.get_by_role("button", name="Source", exact=False).first.click()
        page.locator("#flow-local-file-path").fill("C:\\Fictional\\regional.xlsx")
        page.get_by_role("button", name="After download").click()
        expect(page.locator("#flow-excel-names")).to_have_value(" Exact sheet ")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.locator("#flow-builder-form")).to_have_count(0)
    assert state["flow"]["excel_worksheets"] == {"mode": "single", "names": [" Exact sheet "]}
    assert state["flow"]["sql_mode"] == "replace"


def test_flow_builder_frontend_contract():
    subprocess.run(["node", "tests/test_flow_builder_contract.mjs"], cwd=ROOT, check=True)


def test_flow_save_frontend_contract():
    subprocess.run(["node", "tests/test_flow_save_without_test.mjs"], cwd=ROOT, check=True)


if __name__ == "__main__":
    # A local clickable fixture using the production assets; no connection to an app database.
    fixture, _state = fixture_server(port=8772)
    print("Fictional worksheet UI: http://127.0.0.1:8772/flow-runs/902", flush=True)
    fixture.serve_forever()
