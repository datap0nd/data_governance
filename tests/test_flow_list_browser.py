"""Synthetic full-stack browser checks of the Flows list: no sub-groups, and its filters."""
import json
import os
import socket
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from app import database, freshness
from app.routers import flow_groups, flows
from test_flows import _asap_report, _asap_site, _flow, _mark_discovered, _request, _seed_catalog, flow_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
# Thursday 29 October 2026, 10:00 in Dubai.
THURSDAY_1000_DUBAI = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)


def _finish_run(flow_id, status):
    queued = flows.queue_run(flow_id, _request())
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status=?, finished_at=CURRENT_TIMESTAMP WHERE id=?", (status, queued["id"]))


@pytest.fixture
def ui(flow_db, tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, "utc_now", lambda: THURSDAY_1000_DUBAI)
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "launched", "mode": mode})
    web_site, web_report = _seed_catalog()
    _mark_discovered(web_report["id"])
    asap_site = flows.create_site(_asap_site(), _request())
    asap_report = flows.create_report(_asap_report(asap_site["id"]), _request())
    _mark_discovered(asap_report["id"])
    asap = {"selections": {"data_configuration": "MENA - Global - Global"}, "target_folder": None}
    daily = flows.create_flow(_flow(asap_site["id"], asap_report["id"], name="ASAP daily sales",
                                    schedule_type="daily", schedule_days=[], **asap), _request())
    stock = flows.create_flow(_flow(asap_site["id"], asap_report["id"], name="ASAP weekly stock",
                                    schedule_days=["friday"], enabled=False, **asap), _request())
    movement = flows.create_flow(_flow(web_site["id"], web_report["id"], name="Web movement",
                                       schedule_days=["friday"], target_folder=None), _request())
    source = tmp_path / "daily.csv"
    source.write_text("Code\nAbc\n", encoding="utf-8")
    local = flows.create_flow(flows.FlowWrite(name="Local snapshot", source_type="file", local_file_path=str(source)), _request())
    _finish_run(daily["id"], "succeeded")
    _finish_run(stock["id"], "failed")
    with database.get_db() as db:
        for flow, database_name in ((daily, "postgres"), (stock, "meto_db")):
            db.execute("""UPDATE flows SET sql_handoff_enabled=1, sql_mode='append', sql_database=?,
                          sql_schema='reporting', sql_table=? WHERE id=?""",
                       (database_name, f"synthetic_{flow['id']}", flow["id"]))
    # Saved before sub-groups were removed: both Flows must still be listed.
    flow_groups.create_group(flow_groups.GroupWrite(name="ASAP tracker", module="ASAP",
                                                    flow_ids=[daily["id"], stock["id"]]), _request())
    app = FastAPI(); app.include_router(flows.router)
    app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'))
    app.mount('/fixture', StaticFiles(directory=ROOT / 'tests' / 'fixtures' / 'flow-list', html=True))
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}/fixture/'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    evidence = Path(os.environ.get('FLOW_LIST_EVIDENCE_DIR') or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    names = {"daily": daily["id"], "stock": stock["id"], "movement": movement["id"], "local": local["id"]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            page.locator('body[data-ready="true"]').wait_for()
            (evidence / 'flow-list-browser.json').write_text(json.dumps({
                'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'browser': browser.version, 'synthetic': True, 'viewport': page.viewport_size,
            }, indent=2), encoding='utf-8')
            yield page, names, evidence
            assert not errors, errors
        finally:
            browser.close(); server.should_exit = True; thread.join(timeout=5); sock.close()


def visible(page):
    return sorted(int(value) for value in page.locator('#flow-workspace tr[data-flow-id]:visible').evaluate_all(
        'rows => rows.map(row => row.dataset.flowId)'))


def choose(page, label, option):
    page.get_by_label(label, exact=True).select_option(label=option)
    expect(page.get_by_label(label, exact=True)).to_be_focused()


def test_sources_list_every_flow_without_sub_groups(ui):
    page, names, evidence = ui
    expect(page.locator('#flow-workspace tr[data-flow-id]')).to_have_count(4)
    page.locator('#flow-group-ASAP').click()
    for key in ('daily', 'stock'):
        expect(page.locator(f'tr[data-flow-id="{names[key]}"]')).to_be_visible()
    for gone in ('ASAP tracker', 'Run group', 'Group'):
        expect(page.get_by_role('button', name=gone, exact=True)).to_have_count(0)
    expect(page.locator('.topic-heading')).to_have_count(0)
    expect(page.locator('.flow-status-strip')).to_contain_text('4 production flows')
    expect(page.locator('#flow-filters-clear')).to_be_hidden()
    page.screenshot(path=str(evidence / 'flow-list-asap-open.png'), full_page=True)


def test_each_filter_narrows_the_list_and_clearing_restores_every_flow(ui):
    page, names, evidence = ui
    expect(page.locator('#flow-group-rows-ASAP')).to_be_hidden()
    choose(page, 'Source', 'ASAP')
    # Choosing a filter opens the sources with a match.
    assert visible(page) == sorted([names['daily'], names['stock']])
    expect(page.locator('.flow-status-strip')).to_contain_text('Showing 2 of 4 production flows')
    choose(page, 'Active', 'Inactive')
    assert visible(page) == [names['stock']]
    page.get_by_role('button', name='Clear filters').click()
    expect(page.get_by_label('Source', exact=True)).to_be_focused()
    expect(page.get_by_label('Source', exact=True)).to_have_value('all')
    expect(page.locator('.flow-status-strip')).to_contain_text('4 production flows')
    expect(page.locator('#flow-filters-clear')).to_be_hidden()

    for option, expected in (('Failed', ['stock']), ('Succeeded', ['daily'])):
        choose(page, 'Last run', option)
        assert visible(page) == sorted(names[key] for key in expected), option
    choose(page, 'Last run', 'Any result')
    # Thursday in Dubai: the daily Flow runs today and tomorrow; Web movement on Friday.
    # The weekly stock Flow is paused and the local Flow is manual.
    for option, expected in (('Today', ['daily']), ('Tomorrow', ['daily', 'movement']), ('Next 3 days', ['daily', 'movement'])):
        choose(page, 'Scheduled', option)
        assert visible(page) == sorted(names[key] for key in expected), option
    choose(page, 'Scheduled', 'Any day')
    database = page.get_by_label('Database', exact=True)
    assert database.locator('option').all_inner_texts() == ['All databases', 'meto_db', 'postgres', 'No SQL output']
    for option, expected in (('No SQL output', ['movement', 'local']), ('postgres', ['daily']), ('meto_db', ['stock'])):
        choose(page, 'Database', option)
        assert visible(page) == sorted(names[key] for key in expected), option
    page.screenshot(path=str(evidence / 'flow-list-filtered.png'), full_page=True)

    choose(page, 'Source', 'Local')
    expect(page.get_by_role('heading', name='No production flows match these filters')).to_be_visible()
    expect(page.locator('.flow-empty')).to_contain_text('Clear filters to see every Flow.')
    page.screenshot(path=str(evidence / 'flow-list-no-match.png'), full_page=True)
    page.get_by_role('button', name='Clear filters').click()
    expect(page.locator('#flow-workspace tr[data-flow-id]')).to_have_count(4)


def test_filters_survive_reload_and_fit_a_phone_width(ui):
    page, names, evidence = ui
    choose(page, 'Database', 'postgres')
    page.reload()
    page.locator('body[data-ready="true"]').wait_for()
    expect(page.get_by_label('Database', exact=True)).to_have_value('db:postgres')
    assert visible(page) == [names['daily']]
    expect(page.locator('.flow-status-strip')).to_contain_text('Showing 1 of 4 production flows')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    for label in ('Source', 'Active', 'Last run', 'Scheduled', 'Database'):
        expect(page.get_by_label(label, exact=True)).to_be_visible()
    page.screenshot(path=str(evidence / 'flow-list-narrow.png'), full_page=True)


def test_last_run_filter_follows_runs_that_finish_while_the_page_is_open(ui):
    page, names, evidence = ui
    choose(page, 'Last run', 'Failed')
    assert visible(page) == [names['stock']]
    row = lambda key: page.locator(f'#flow-workspace tr[data-flow-id="{names[key]}"]')
    # A run that fails elsewhere joins the list on the next activity poll.
    _finish_run(names['daily'], 'failed')
    expect(row('daily')).to_have_count(1, timeout=20000)
    # Starting a run keeps its Flow in view; a success then takes it out of Failed.
    queued = flows.queue_run(names['stock'], _request())
    expect(page.locator(f'#flow-execution-rows tr[data-flow-id="{names["stock"]}"]')).to_have_count(1, timeout=20000)
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status='succeeded', finished_at=CURRENT_TIMESTAMP WHERE id=?", (queued["id"],))
    expect(row('stock')).to_have_count(0, timeout=20000)
    assert visible(page) == [names['daily']]
    expect(page.get_by_label('Last run', exact=True)).to_have_value('failed')
