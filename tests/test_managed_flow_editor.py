"""The production editor with fictional data and bounded API substitutes."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest
from playwright.sync_api import sync_playwright, expect

from app import database, flow_paths
from app.routers import flows
from test_flows import flow_db, _request


def test_recorded_flow_needs_no_catalog_report_or_destination(flow_db, tmp_path):
    site = flows.create_site(flows.SiteWrite(name='Fictional GSCM', adapter='gscm_portal',
        base_url='https://example.test/', auth_url='https://example.test/'), _request())
    root = tmp_path / 'managed'
    with database.get_db() as db:
        flow_paths.save_setting(db, 'flows_root', str(root))
        flow_paths.save_setting(db, 'flows_paths_enforced', '1')
    saved = flows.create_flow(flows.FlowWrite(name='Orders', site_id=site['id'], execution_method='recorded',
        target_folder=str(tmp_path / 'ignored'), file_format='xlsx', filename_template='orders_{export}.xlsx'), _request())
    assert flow_paths.is_inside(saved['target_folder'], str(root / 'GSCM'))
    assert saved['flow_folder'] and not (tmp_path / 'ignored').exists()
    assert not flows.catalog()['reports']
    with database.get_db() as db:
        db.execute('UPDATE flow_reports SET stale=1, enabled=0 WHERE id=?', (saved['report_id'],))
        db.execute('UPDATE flows SET target_folder=? WHERE id=?', (str(root / 'GSCM'), saved['id']))
        job = flows._build_job(db, saved['id'], recording_draft=True,
            pending_settings=flows.FlowWrite.model_validate({**saved, 'target_folder': None}))
    assert job['downloads']['target_folder'] == str(Path(saved['flow_folder']) / 'Downloads')


def test_saving_legacy_flow_manages_future_output_and_preserves_history(flow_db, tmp_path):
    from test_flows import _seed_catalog, _flow
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site['id'], report['id']), _request())
    old = tmp_path / 'historic'; old.mkdir()
    history = old / 'orders.csv'; history.write_text('keep this history')
    with database.get_db() as db:
        db.execute('UPDATE flows SET name=?, flow_folder=NULL, target_folder=? WHERE id=?',
            ('Legacy orders', str(old), saved['id']))
    body = flows.FlowWrite.model_validate({**saved, 'name':'Legacy orders', 'target_folder':None})
    updated = flows.update_flow(saved['id'], body, _request())
    assert updated['flow_folder']
    assert updated['target_folder'] == str(Path(updated['flow_folder']) / 'Downloads')
    assert history.read_text() == 'keep this history'
    assert flows.update_flow(saved['id'], body, _request())['target_folder'] == updated['target_folder']


@pytest.fixture
def editor_url():
    handler = partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).parents[1] / 'app'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}/static/recording-preview/managed.html'
    server.shutdown(); server.server_close(); thread.join()


def test_editor_method_output_and_failure_recovery(editor_url, tmp_path):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width':1280, 'height':960})
        errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(editor_url)
        expect(page.locator('#flow-name')).to_be_visible()
        assert page.locator('form label').first.locator('input').get_attribute('id') == 'flow-name'
        assert page.locator('.flow-summary,.flow-step-status,.flow-section-head,#flow-target-folder').count() == 0
        expect(page.locator('#flow-report')).to_be_hidden()
        expect(page.locator('#flow-record-review')).to_be_visible()
        page.locator('#flow-name').fill('My orders')
        page.locator('#flow-execution-method').select_option('catalog')
        expect(page.locator('#flow-report')).to_be_visible()
        expect(page.locator('#flow-record-review')).to_be_hidden()
        page.locator('#flow-execution-method').select_option('recorded')
        expect(page.locator('#flow-report')).to_be_hidden()
        assert page.locator('#flow-name').input_value() == 'My orders'
        page.locator('#flow-record-review').click()
        assert page.evaluate('previewRecordingSettings.name') == 'My orders'
        page.locator('#flow-step-toggle-destination').click()
        expect(page.locator('#flow-output-mode-help')).to_contain_text('Power BI')
        page.locator('#flow-filename').fill('orders_{date}.xlsx')
        expect(page.locator('.flow-filename-preview')).to_contain_text('Remove date')
        page.locator('#flow-filename').fill('orders_{export}.xlsx')
        page.locator('#flow-output-mode').select_option('run_folders')
        expect(page.locator('#flow-output-mode-help')).to_contain_text('last 3')
        page.locator('#flow-output-mode').select_option('direct_replace')
        page.evaluate('window.previewFail=true')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('.flow-form-error')).to_contain_text('Save failed')
        assert page.locator('#flow-name').input_value() == 'My orders'
        page.evaluate('window.previewFail=false')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('#preview-status')).to_contain_text('Flow saved')
        assert page.evaluate('previewPayload.target_folder') is None
        page.locator('#preview-new').click()
        page.locator('#flow-name').fill('New recorded flow')
        expect(page.locator('#flow-report')).to_be_hidden()
        page.locator('#flow-record-start').click()
        page.wait_for_function("window.previewRecordingSettings.name === 'New recorded flow'")
        assert page.evaluate('previewPayload.report_id') is None
        assert page.locator('dialog').count() == 0
        for source in ['outlook', 'file']:
            page.evaluate("source => _flowShowView('builder', {_source_type:source})", source)
            expect(page.locator('#flow-name')).to_be_visible()
            assert page.locator('.flow-summary,.flow-step-status,#flow-target-folder').count() == 0
        page.set_viewport_size({'width':390, 'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
