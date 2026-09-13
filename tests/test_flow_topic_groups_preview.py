"""Synthetic browser walkthrough of the review-only topic-group prototype."""
import functools
import json
import os
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def preview(tmp_path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'app')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence = Path(os.environ.get('PREVIEW_EVIDENCE_DIR') or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors, external = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: external.append(request.url) if not request.url.startswith(f'http://127.0.0.1:{server.server_port}/') else None)
        page.goto(f'http://127.0.0.1:{server.server_port}/static/recording-preview/groups.html')
        page.get_by_role('button', name='M Tracker group', exact=True).wait_for()
        (evidence / 'browser.json').write_text(json.dumps({
            'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'browser': browser.version, 'viewport': page.viewport_size, 'synthetic': True,
        }, indent=2), encoding='utf-8')
        try:
            yield page, evidence
            assert not errors, errors
            assert not external, external
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def shot(page, evidence, name):
    page.screenshot(path=str(evidence / f'{name}.png'), full_page=True)


def menu(page, topic_id=1):
    page.locator(f'[data-topic-id="{topic_id}"] summary').click()


def test_collapse_group_and_individual_run_recovery(preview):
    page, evidence = preview
    group = page.get_by_role('button', name='M Tracker group', exact=True)
    expect(group).to_have_attribute('aria-expanded', 'false')
    expect(page.locator('tr[data-flow-id="1"]')).to_have_count(0)
    group_box = group.bounding_box()
    photo_box = page.locator('tr[data-flow-id="3"] strong').bounding_box()
    assert abs(group_box['x'] - photo_box['x']) < 2
    shot(page, evidence, 'groups-collapsed')
    group.click()
    expect(page.locator('tr[data-flow-id="1"]')).to_be_visible()
    expect(page.locator('tr[data-flow-id="2"]')).to_contain_text('analytics.public.m_tracker_subs')
    shot(page, evidence, 'groups-expanded')
    page.locator('#flow-group-ASAP').click()
    expect(group).to_be_hidden()
    page.locator('#flow-group-ASAP').click()
    expect(group).to_have_attribute('aria-expanded', 'true')
    page.locator('tr[data-flow-id="1"] .flow-edit').click()
    expect(page.locator('#existing-dialog')).to_contain_text('analytics.public.m_tracker_country')
    page.get_by_role('button', name='Back to flows', exact=True).click()
    page.locator('tr[data-flow-id="1"] .flow-run').click()
    expect(page.locator('#flow-execution-rows tr')).to_have_count(1)
    expect(page.locator('[data-topic-run="1"]')).to_be_disabled()
    expect(page.locator('tr[data-flow-id="2"] .flow-run')).to_be_enabled()
    page.locator('tr[data-flow-id="1"] .flow-stop').click()
    expect(page.locator('#flow-execution-pane')).to_be_hidden()
    expect(page.locator('tr[data-flow-id="1"]')).to_be_visible()
    page.get_by_role('button', name='Run group', exact=True).click()
    expect(page.locator('#flow-execution-rows tr')).to_have_count(2)
    expect(page.locator('#groups-feedback')).to_contain_text('2 flows queued together')
    shot(page, evidence, 'group-running')
    page.get_by_text('Try recovery & scale', exact=True).click()
    page.get_by_role('button', name='Finish active runs', exact=True).click()
    expect(page.locator('#flow-execution-pane')).to_be_hidden()
    expect(page.locator('tr[data-flow-id="1"]')).to_be_visible()
    page.locator('tr[data-flow-id="3"] .flow-run').click()
    expect(page.locator('#flow-execution-rows tr')).to_have_count(1)
    expect(page.locator('#flow-execution-rows')).to_contain_text('Photo')


def test_create_edit_move_ungroup_and_save_failure(preview):
    page, evidence = preview
    page.get_by_text('Try recovery & scale', exact=True).click()
    page.get_by_label('Next save fails', exact=True).check()
    page.get_by_role('button', name='Group flows in ASAP', exact=True).click()
    expect(page.locator('#topic-choices .topic-choice')).to_have_count(5)
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-error')).to_contain_text('Enter a group name')
    page.get_by_label('Group name', exact=True).fill('Connectivity')
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-error')).to_contain_text('Select at least 2 flows')
    page.get_by_label('Wi-Fi', exact=True).check()
    page.get_by_label('TI', exact=True).check()
    page.get_by_label('Flows', exact=True).fill('does not exist')
    expect(page.locator('#topic-choices')).to_contain_text('No matching flows')
    expect(page.locator('#topic-selection')).to_contain_text('2 selected')
    page.get_by_label('Flows', exact=True).fill('')
    expect(page.get_by_label('Wi-Fi', exact=True)).to_be_checked()
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-error')).to_contain_text('Your name and selection are kept')
    expect(page.get_by_label('Group name', exact=True)).to_have_value('Connectivity')
    expect(page.get_by_label('TI', exact=True)).to_be_checked()
    shot(page, evidence, 'group-save-recovery')
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-dialog')).to_be_hidden()
    expect(page.get_by_role('button', name='Connectivity group', exact=True)).to_be_visible()
    menu(page, 2)
    page.get_by_role('button', name='Edit group', exact=True).click()
    page.get_by_label('Group name', exact=True).fill('M Tracker')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.locator('#topic-error')).to_contain_text('already exists in ASAP')
    page.get_by_label('Group name', exact=True).fill('Weekly topics')
    page.get_by_label('Photo', exact=True).check()
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.locator('[data-topic-id="2"]')).to_contain_text('3 flows')
    menu(page, 2)
    page.get_by_role('button', name='Edit group', exact=True).click()
    page.get_by_label('Group name', exact=True).fill('Abandoned name')
    page.get_by_role('button', name='Cancel', exact=True).click()
    expect(page.get_by_role('button', name='Weekly topics group', exact=True)).to_be_visible()
    # A grouped flow can be moved without editing its configuration.
    menu(page, 2)
    page.get_by_role('button', name='Edit group', exact=True).click()
    page.get_by_label('M Tracker Country', exact=False).check()
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.locator('[data-topic-id="1"]')).to_contain_text('1 flow')
    expect(page.locator('[data-topic-id="2"]')).to_contain_text('4 flows')
    menu(page, 2)
    page.get_by_role('button', name='Ungroup', exact=True).click()
    expect(page.get_by_role('button', name='Weekly topics group', exact=True)).to_have_count(0)
    expect(page.locator('tr[data-flow-id="1"]')).to_contain_text('analytics.public.m_tracker_country')
    expect(page.locator('#groups-feedback')).to_contain_text('All 4 flows kept')
    page.get_by_role('button', name='Group flows in GSCM', exact=True).click()
    expect(page.locator('#topic-choices .topic-choice')).to_have_count(1)
    expect(page.locator('#topic-choices')).to_contain_text('Inventory')
    page.get_by_role('button', name='Close group editor', exact=True).click()


def test_blocked_batch_waiting_capacity_and_search_at_scale(preview):
    page, evidence = preview
    page.get_by_text('Try recovery & scale', exact=True).click()
    page.get_by_label('Next run is blocked', exact=True).check()
    page.get_by_role('button', name='Run group', exact=True).click()
    expect(page.locator('#flow-execution-pane')).to_be_hidden()
    expect(page.locator('[data-topic-id="1"]')).to_contain_text('No flows queued')
    expect(page.locator('[data-topic-run="1"]')).to_be_enabled()
    shot(page, evidence, 'group-run-blocked')
    page.get_by_label('One worker available', exact=True).check()
    page.get_by_role('button', name='Run group', exact=True).click()
    expect(page.locator('#flow-execution-rows tr')).to_have_count(2)
    expect(page.locator('#flow-execution-rows')).to_contain_text('Waiting for an available worker')
    expect(page.locator('#groups-feedback')).to_contain_text('1 running; the rest wait for a worker')
    page.get_by_role('button', name='Finish active runs', exact=True).click()
    page.get_by_role('button', name='Use 200 flows', exact=True).click()
    page.get_by_role('button', name='Group flows in ASAP', exact=True).click()
    expect(page.locator('#topic-choices .topic-choice')).to_have_count(197)
    page.get_by_label('Group name', exact=True).fill('Regional')
    page.get_by_label('Flows', exact=True).fill('Regional report 199')
    page.get_by_label('Regional report 199', exact=True).check()
    page.get_by_label('Flows', exact=True).fill('Regional report 200')
    page.get_by_label('Regional report 200', exact=True).check()
    expect(page.locator('#topic-selection')).to_contain_text('2 selected')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, evidence, 'group-search-narrow')
    page.get_by_role('button', name='Create group', exact=True).click()
    expect(page.locator('#topic-dialog')).to_be_hidden()
    expect(page.get_by_role('button', name='Regional group', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Reset preview', exact=True).click()
    expect(page.get_by_role('button', name='M Tracker group', exact=True)).to_be_visible()
