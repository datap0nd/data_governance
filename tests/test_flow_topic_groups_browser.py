"""Synthetic full-stack browser checks; launcher calls are replaced with fixtures."""
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
from app.routers import flows
from test_flow_topic_groups import grouped_flows, create
from test_flows import flow_db

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ui(grouped_flows, tmp_path):
    saved, launches = grouped_flows
    create(saved[:2])
    app = FastAPI(); app.include_router(flows.router)
    app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'))
    app.mount('/fixture', StaticFiles(directory=ROOT / 'tests' / 'fixtures' / 'flow-groups', html=True))
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}/fixture/'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    evidence = Path(os.environ.get('GROUP_EVIDENCE_DIR') or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            page.get_by_role('button', name='M Tracker group', exact=True).wait_for()
            (evidence / 'implementation-browser.json').write_text(json.dumps({
                'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'browser': browser.version, 'synthetic': True, 'viewport': page.viewport_size,
            }, indent=2), encoding='utf-8')
            yield page, saved, launches, evidence
            assert not errors, errors
        finally:
            browser.close(); server.should_exit = True; thread.join(timeout=5); sock.close()


def test_saved_groups_reload_move_sort_and_ungroup_through_real_api(ui):
    page, saved, _, evidence = ui
    expect(page.locator(f'tr[data-flow-id="{saved[0]["id"]}"]')).to_be_hidden()
    page.get_by_role('button', name='M Tracker group', exact=True).click()
    expect(page.locator(f'tr[data-flow-id="{saved[0]["id"]}"]')).to_be_visible()
    page.locator('#flow-sort-name').click()
    expect(page.locator(f'tr[data-flow-id="{saved[0]["id"]}"]')).to_be_visible()
    page.get_by_role('button', name='Group flows in Web', exact=True).click()
    page.get_by_label('Group name', exact=True).fill('Connectivity')
    page.get_by_label('Flows', exact=True).fill('Photo')
    page.get_by_label('Photo', exact=True).check()
    page.get_by_label('Flows', exact=True).fill('TI')
    page.get_by_label('TI', exact=True).check()
    page.route('**/api/flows/groups', lambda route: route.fulfill(status=503, json={'detail': 'Synthetic save failure'}) if route.request.method == 'POST' else route.continue_())
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-error')).to_contain_text('Your name and selection are kept')
    expect(page.get_by_label('Group name', exact=True)).to_have_value('Connectivity')
    page.unroute('**/api/flows/groups')
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-dialog')).to_have_count(0)
    page.reload()
    expect(page.get_by_role('button', name='Connectivity group', exact=True)).to_be_visible()
    topic = page.locator('tr.topic-heading').filter(has_text='Connectivity')
    topic.locator('summary').click()
    topic.get_by_role('button', name='Edit group', exact=True).click()
    page.get_by_label('Group name', exact=True).fill('M Tracker')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.locator('#topic-error')).to_contain_text('already exists')
    page.get_by_label('Group name', exact=True).fill('Weekly topics')
    page.get_by_label('M Tracker Country', exact=False).check()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(evidence / 'implementation-editor-narrow.png'), full_page=True)
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.locator('#topic-dialog')).to_have_count(0)
    page.set_viewport_size({'width': 1440, 'height': 1080})
    page.reload()
    topic = page.locator('tr.topic-heading').filter(has_text='Weekly topics')
    expect(topic).to_contain_text('3 flows')
    page.screenshot(path=str(evidence / 'implementation-groups.png'), full_page=True)
    topic.locator('summary').click()
    topic.get_by_role('button', name='Ungroup', exact=True).click()
    expect(page.get_by_role('button', name='Weekly topics group', exact=True)).to_have_count(0)
    page.reload()
    expect(page.locator('tr[data-flow-id]')).to_have_count(5)
    expect(page.get_by_role('button', name='Weekly topics group', exact=True)).to_have_count(0)


def test_actual_group_queue_polling_completion_failure_and_individual_recovery(ui):
    page, saved, launches, evidence = ui
    page.route('**/api/flows/groups/*/run', lambda route: route.fulfill(status=409, json={'detail': 'M Tracker Subs: output is busy. No flows queued.'}))
    page.get_by_role('button', name='Run group', exact=True).click()
    expect(page.locator('.topic-summary')).to_contain_text('No flows queued')
    expect(page.locator('#flow-execution-pane')).to_be_hidden()
    page.unroute('**/api/flows/groups/*/run')
    page.get_by_role('button', name='Run group', exact=True).click()
    expect(page.locator('#flow-execution-rows tr')).to_have_count(2)
    expect(page.locator('[data-topic-run]')).to_be_disabled()
    assert launches == ['headless']
    page.screenshot(path=str(evidence / 'implementation-queued.png'), full_page=True)
    # Polling changes row locations without rebuilding the editor or losing its work.
    page.get_by_role('button', name='Group flows in Web', exact=True).click()
    page.get_by_label('Group name', exact=True).fill('Unsaved work')
    page.get_by_label('Photo', exact=True).check()
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status='succeeded',finished_at=CURRENT_TIMESTAMP")
    expect(page.locator('#flow-execution-pane')).to_be_hidden(timeout=10000)
    expect(page.get_by_label('Group name', exact=True)).to_have_value('Unsaved work')
    expect(page.get_by_label('Photo', exact=True)).to_be_checked()
    page.get_by_role('button', name='Cancel', exact=True).click()
    expect(page.locator('tr.topic-member')).to_have_count(2)
    expect(page.locator('[data-topic-run]')).to_be_enabled()
    member = page.locator(f'tr[data-flow-id="{saved[0]["id"]}"]')
    member.locator('.flow-run').click()
    expect(page.locator('#flow-execution-rows tr')).to_have_count(1)
    expect(page.locator('[data-topic-run]')).to_be_disabled()
    page.locator('#flow-execution-rows .flow-stop').click()
    expect(page.locator('#flow-execution-pane')).to_be_hidden()
    expect(page.locator('[data-topic-run]')).to_be_enabled()
