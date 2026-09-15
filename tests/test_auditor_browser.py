"""Actual auditor controls and API, with a synthetic reader and run executor."""
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from app import database
from app.auditor import detection, engine, router, store
from test_data_auditor import audit_db, evidence, tmp_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ui(audit_db, monkeypatch):
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='completed'")
    store.write_settings({"paused": False, "time": "02:00"})
    monkeypatch.setattr("app.auditor.config.readiness", lambda: {"configured":True,
        "reader":{"available":True,"detail":"Synthetic reader ready."},
        "model":{"available":True,"detail":"Synthetic Qwen ready."}})
    async def reader(*_args, **_kwargs):
        return {"datasets": [{"id": "sales", "flow_id": audit_db.flow}]}
    monkeypatch.setattr(engine, "read_catalog", reader)
    def start():
        with database.get_db() as db:
            return db.execute("INSERT INTO auditor_runs(status,trigger_type,started_at,selected_flows) VALUES('running','manual',?,?)", (store.now().isoformat(), json.dumps([audit_db.flow]))).lastrowid
    monkeypatch.setattr(engine, "start", start)
    app = FastAPI()
    app.include_router(router.router)
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"))
    app.mount("/fixture", StaticFiles(directory=ROOT / "tests/fixtures/data-auditor", html=True))
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}/fixture/"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    folder = Path(os.environ.get("AUDITOR_EVIDENCE_DIR") or audit_db.root)
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            page.get_by_role("heading", name="AI Auditor", exact=True).wait_for()
            yield page, audit_db, folder
            assert not errors, errors
            (folder / "browser.json").write_text(json.dumps({"synthetic": True,
                "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "browser": browser.version, "viewport": page.viewport_size,
                "source_sha256": __import__("hashlib").sha256((ROOT / "app/static/data_auditor.js").read_bytes()).hexdigest()}, indent=2), encoding="utf-8")
        finally:
            browser.close(); server.should_exit = True; thread.join(timeout=5); sock.close()


def test_default_on_time_save_failure_run_stop_pause_and_navigation(ui):
    page, fixture, folder = ui
    expect(page.locator("#auditor-status-badge")).to_have_text("On")
    expect(page.get_by_text("Fictional flow", exact=True)).to_be_visible()
    assert page.get_by_label("Operator access key").count() == 0
    assert page.get_by_text("Flows to audit", exact=True).count() == 0
    page.get_by_role("button", name="Change time").click()
    page.get_by_label("Dubai start time").fill("03:15")
    page.route("**/api/auditor/settings", lambda route: route.fulfill(status=503, json={"detail": "Synthetic save outage"}))
    page.get_by_role("button", name="Save time").click()
    expect(page.locator("#auditor-feedback")).to_contain_text("Your choice is kept")
    expect(page.get_by_label("Dubai start time")).to_have_value("03:15")
    expect(page.locator("#auditor-time-dialog")).to_be_visible()
    page.screenshot(path=str(folder / "save-recovery.png"), full_page=True)
    page.unroute("**/api/auditor/settings")
    page.get_by_role("button", name="Save time").click()
    expect(page.locator("#auditor-feedback")).to_have_text("Audit time saved.")
    expect(page.locator("#auditor-schedule")).to_contain_text("03:15")
    page.route("**/api/auditor/run", lambda route: route.fulfill(status=503, json={"detail": "Synthetic reader unavailable"}))
    page.get_by_role("button", name="Run now").click()
    expect(page.locator("#auditor-feedback")).to_contain_text("Audit could not start")
    page.unroute("**/api/auditor/run")
    page.get_by_role("button", name="Run now").click()
    expect(page.locator("#auditor-feedback")).to_contain_text("Audit started")
    expect(page.locator("#auditor-run-state")).to_have_text("Running")
    expect(page.get_by_role("button", name="Run now")).to_be_disabled()
    page.get_by_role("button", name="Stop audit").click()
    expect(page.locator("#auditor-run-state")).to_have_text("Stopping")
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='cancelled' WHERE status='cancelling'")
    expect(page.locator("#auditor-run-state")).to_have_text("Cancelled", timeout=10000)
    expect(page.get_by_role("button", name="Stop audit")).to_be_hidden()
    page.get_by_role("button", name="Pause auditor").click()
    expect(page.locator("#auditor-status-badge")).to_have_text("Paused")
    assert store.read_settings()["paused"] is True
    expect(page.get_by_role("button", name="Run now")).to_be_disabled()
    page.get_by_role("button", name="Resume auditor").click()
    expect(page.locator("#auditor-status-badge")).to_have_text("On")
    page.get_by_role("link", name="Data Quality", exact=True).click()
    page.get_by_role("link", name="AI Auditor", exact=True).click()
    expect(page.locator("#auditor-schedule")).to_contain_text("03:15")
    assert page.evaluate("Object.values(localStorage).concat(Object.values(sessionStorage)).every(v => !/token|password/i.test(v))")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(folder / "narrow-controls.png"), full_page=True)


def test_partial_coverage_and_evidence_are_visible_and_escaped(ui):
    page, fixture, folder = ui
    store.write_settings({"paused": False, "time": "02:00"})
    with database.get_db() as db:
        audit_id = db.execute("INSERT INTO auditor_runs(status,trigger_type,started_at,selected_flows) VALUES('running','manual',?,?)", (store.now().isoformat(), json.dumps([fixture.flow]))).lastrowid
    current = evidence(5, 70000, fixture.flow)
    result = detection.finding(current, "row_count_change", "70,000 rows versus 100,000 (-30%). <script>throw 'injected'</script>", [evidence(4, 100000, fixture.flow)])
    store.emit(audit_id, result)
    with database.get_db() as db:
        db.execute("UPDATE auditor_runs SET status='completed_with_gaps',coverage=? WHERE id=?", (json.dumps({"checked":1,"findings":1,"model":"completed","unverified":[{"dataset_id":"stock","reason":"sql_unverified"}]}), audit_id))
    expect(page.locator("#auditor-run-state")).to_have_text("Completed with gaps", timeout=10000)
    expect(page.locator("#auditor-gaps")).to_contain_text("stock")
    page.get_by_role("button", name="View evidence").click()
    expect(page.locator("#auditor-evidence")).to_be_visible()
    expect(page.locator("#auditor-evidence-body")).to_contain_text("70,000")
    assert page.locator("#auditor-evidence-body script").count() == 0
    page.screenshot(path=str(folder / "finding-evidence.png"), full_page=True)
    page.get_by_role("button", name="Back to auditor").click()
    expect(page.locator("#auditor-evidence")).not_to_be_visible()
