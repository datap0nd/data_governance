"""Clickable fictional preview: production builder, recording editor, run history and run log.

The preview replaces only the API layer. These checks walk every changed control,
including failure and recovery, on desktop and narrow screens. They are synthetic
browser evidence, not live portal, worker or PostgreSQL verification.
"""
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from app.flow_recording import validate_definition

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def preview(tmp_path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'app')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence = Path(os.environ.get('PREVIEW_EVIDENCE_DIR') or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/static/recording-preview/templates-refresh.html')
        page.locator('#flow-view-refresh').wait_for(state='attached')
        open_after(page)
        yield page, evidence, errors
        assert not errors, errors
        browser.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def open_after(page):
    """The builder discloses one step at a time; SQL handoff lives under After download."""
    if not page.locator('#flow-view-refresh').is_visible():
        page.locator('#flow-step-toggle-after').click()
    page.locator('#flow-view-refresh').wait_for()


def shot(page, evidence, name):
    page.screenshot(path=str(evidence / f'{name}.png'), full_page=True)


def no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_refresh_setup_automatic_manual_blockers_and_invalidation(preview):
    page, evidence, _ = preview
    fieldset = page.locator('#flow-view-refresh')
    expect(fieldset.get_by_label('Automatic', exact=False)).to_be_checked()
    status = page.locator('#flow-view-refresh-auto-status')
    expect(status).to_contain_text('3 materialized view(s) will refresh in this order')
    expect(status).to_contain_text('Metadata verified')
    assert page.locator('#flow-view-refresh-auto-list li').count() == 3
    assert page.locator('#flow-view-refresh-auto-list li').first.inner_text().endswith('regional_orders_mv')
    shot(page, evidence, 'builder-automatic')
    # Blockers explain themselves beside the control with actions.
    for scenario, text, actions in [
        ('empty', 'Verified: no materialized view reads this table', []),
        ('missing', 'not in the PostgreSQL dependency metadata', ['Refresh metadata', 'Use Manual']),
        ('incomplete', 'no verified PostgreSQL identity', ['Refresh metadata', 'Use Manual']),
        ('stale', 'older than 48 hours', ['Refresh metadata']),
        ('cyclic', 'dependency cycle', ['Use Manual']),
    ]:
        page.select_option('#preview-discovery', scenario)
        page.get_by_role('button', name='Preview discovered views').click()
        expect(status).to_contain_text(text)
        for action in ['Refresh metadata', 'Use Manual']:
            expect(page.get_by_role('button', name=action, exact=True)).to_be_visible() if action in actions else expect(page.get_by_role('button', name=action, exact=True)).to_be_hidden()
        if scenario == 'missing':
            shot(page, evidence, 'builder-blocked-missing')
            page.get_by_role('button', name='Refresh metadata', exact=True).click()
            expect(status).to_contain_text('lineage recheck queued')
    # Changing the SQL destination invalidates the automatic preview.
    page.select_option('#preview-discovery', 'ok')
    page.get_by_role('button', name='Preview discovered views').click()
    expect(status).to_contain_text('will refresh in this order')
    page.locator('#flow-sql-table').fill('inventory_aging')
    page.locator('#flow-sql-table').dispatch_event('change')
    expect(status).to_contain_text('SQL destination changed')
    assert page.locator('#flow-view-refresh-auto-list li').count() == 0
    # Manual: catalog search, manual entry, verification failure and recovery.
    page.get_by_role('button', name='Preview discovered views').click()
    page.select_option('#preview-discovery', 'cyclic')
    page.get_by_role('button', name='Preview discovered views').click()
    page.get_by_role('button', name='Use Manual', exact=True).click()
    expect(fieldset.get_by_label('Manual', exact=False)).to_be_checked()
    expect(page.locator('#flow-view-refresh-manual-list')).to_contain_text('No views selected yet')
    page.locator('#flow-view-refresh-search').fill('orders')
    page.locator('.flow-view-refresh-result').first.wait_for()
    assert page.locator('.flow-view-refresh-result').count() == 2
    page.locator('.flow-view-refresh-result').nth(1).click()  # orders_by_region_mv first: out of order on purpose
    page.locator('.flow-view-refresh-result').first.click()
    expect(page.locator('#flow-view-refresh-manual-status')).to_contain_text('Verify the selection')
    page.get_by_role('button', name='Add view manually', exact=True).click()
    page.locator('#flow-view-refresh-name').fill('ghost_mv')
    page.locator('#flow-view-refresh-schema').fill('bi')
    page.get_by_role('button', name='Add view', exact=True).click()
    assert page.locator('#flow-view-refresh-manual-list li').count() == 3
    page.get_by_role('button', name='Verify selection', exact=True).click()
    manual_status = page.locator('#flow-view-refresh-manual-status')
    expect(manual_status).to_contain_text('ghost_mv does not exist')
    shot(page, evidence, 'builder-manual-verify-failed')
    page.get_by_role('button', name='Remove analytics.bi.ghost_mv').click()
    page.get_by_role('button', name='Verify selection', exact=True).click()
    expect(manual_status).to_contain_text('2 view(s) verified and ordered upstream first')
    items = page.locator('#flow-view-refresh-manual-list li')
    assert items.nth(0).inner_text().startswith('1') and 'regional_orders_mv' in items.nth(0).inner_text()
    assert 'orders_by_region_mv' in items.nth(1).inner_text()
    assert page.locator('#flow-view-refresh-manual-list .badge-green').count() == 2
    shot(page, evidence, 'builder-manual-verified')
    page.get_by_role('button', name='Save changes', exact=True).click()
    page.wait_for_function('window.previewPayload && window.previewPayload.post_sql_refresh')
    payload = page.evaluate('window.previewPayload.post_sql_refresh')
    assert payload['mode'] == 'manual' and [v['name'] for v in payload['views']] == ['regional_orders_mv', 'orders_by_region_mv']
    expect(page.locator('#preview-status')).to_contain_text('Flow saved')
    # Off is the default for new flows and stays selectable.
    page.evaluate("previewShow('new')")
    assert page.evaluate("document.querySelector('#flow-view-refresh').dataset.mode") == 'off'
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("previewShow('builder')")
    open_after(page)
    page.locator('#flow-view-refresh').get_by_label('Manual', exact=False).check()
    page.get_by_role('button', name='Add view manually', exact=True).click()
    no_overflow(page)
    shot(page, evidence, 'builder-narrow')


def test_template_picker_search_versions_preview_apply_undo_and_module_rules(preview):
    page, evidence, _ = preview
    page.evaluate("previewShow('recording')")
    expect(page.locator('[data-editor]')).to_contain_text('Choose from template')
    page.locator('[data-editor] [data-begin-template]').click()
    panel = page.locator('[data-template-panel]')
    items = panel.locator('.recording-template-item')
    expect(items).to_have_count(2)
    assert [items.nth(i).locator('strong').inner_text() for i in range(2)] == ['Inventory aging', 'Regional orders']
    assert 'GSCM · 7 steps' in items.nth(1).inner_text() and 'Active' in items.nth(1).inner_text()
    assert 'Draft' in items.nth(0).inner_text()
    shot(page, evidence, 'recording-template-list')
    panel.locator('[data-template-search]').fill('inven')
    expect(panel.locator('.recording-template-item')).to_have_count(1)
    panel.locator('[data-template-search]').fill('zzz')
    expect(panel).to_contain_text('No flow matches that search')
    panel.locator('[data-template-search]').fill('')
    panel.get_by_role('option', name='Regional orders').click()
    version = panel.locator('[data-template-version]')
    assert version.locator('option').count() == 2
    assert 'active' in version.locator('option:checked').inner_text() and 'default' in version.locator('option:checked').inner_text()
    panel.locator('.recording-template-steps li').first.wait_for()
    assert panel.locator('.recording-template-steps li').count() == 6
    expect(panel.locator('.recording-template-summary')).to_contain_text('7 steps · 1 date parameter(s) · 1 download(s) · GSCM bookmark targets')
    shot(page, evidence, 'recording-template-preview')
    # An older draft version can be chosen; its step list updates.
    version.select_option(index=0)
    page.wait_for_function("document.querySelectorAll('.recording-template-steps li').length === 7")
    # Cancel changes nothing.
    panel.get_by_role('button', name='Cancel', exact=True).click()
    expect(panel).to_be_hidden()
    assert page.locator('[data-card]').count() == 0
    # Apply replaces the editor with an independent draft and reports provenance.
    page.locator('[data-editor] [data-begin-template]').click()
    panel.get_by_role('option', name='Regional orders').click()
    panel.locator('.recording-template-steps li').first.wait_for()
    panel.get_by_role('button', name='Use this recording').click()
    page.wait_for_function("document.querySelectorAll('[data-card]').length === 6")
    expect(page.locator('[data-save-state]')).to_contain_text('Copied from Regional orders')
    expect(page.locator('[data-state]')).to_contain_text('6 steps recorded')
    calls = page.evaluate('previewCalls.filter(c=>c.path.endsWith("/copy"))')
    assert calls[-1]['body'] == {'source_flow_id': 7, 'source_revision_id': 3, 'site_id': 1}
    revisions = page.evaluate('previewData.revisions[11]')
    assert revisions[0]['status'] == 'draft' and revisions[0]['template_source']['source_flow_name'] == 'Regional orders'
    # The copied semantic target is editable independently from its display-only step name.
    page.get_by_role('button', name='Click “Setting”', exact=True).click()
    expect(page.get_by_label('Step name (display only)', exact=True)).to_be_visible()
    target_name = page.get_by_label('Target name (used during playback)', exact=True)
    expect(target_name).to_have_value('Setting')
    target_name.fill('Main')
    expect(page.get_by_role('button', name='Click “Main”', exact=True)).to_be_visible()
    shot(page, evidence, 'recording-template-applied')
    # Test recording is required before activation and stays free of SQL work.
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.wait_for_function('previewCalls.some(c=>c.path.endsWith("/validate"))')
    expect(page.locator('p[data-session]')).to_contain_text('Test passed')
    saved = page.evaluate('previewCalls.filter(c=>c.path.endsWith("/recordings/revisions")).at(-1).body.definition')
    main = next(s for s in saved['steps'] if s['id'] == 'regional-setting')
    assert main['locator'] == [{'method': 'get_by_role', 'args': ['button'], 'kwargs': {'name': 'Main'}}]
    # Record again is a visible one-click action and does not create another saved revision.
    before_record_again = page.evaluate('previewData.revisions[11].length')
    page.get_by_role('button', name='Record again', exact=True).click()
    expect(page.get_by_role('button', name='Finish recording', exact=True)).to_be_visible()
    assert page.evaluate('previewData.revisions[11].length') == before_record_again
    assert page.evaluate('previewCalls.filter(c=>c.method==="POST").at(-1).path').endswith('/recordings/start')
    page.get_by_role('button', name='Cancel recording', exact=True).click()
    page.wait_for_function('previewData.sessions[11][0].status === "cancelled"')
    # Replacing an existing recording keeps the previous steps through Undo and saved versions.
    page.evaluate("previewShow('recording', 7)")
    page.wait_for_function("document.querySelectorAll('[data-card]').length === 7")
    page.get_by_role('button', name='Choose from template', exact=True).click()
    panel.get_by_role('option', name='Inventory aging').click()
    panel.locator('.recording-template-steps li').first.wait_for()
    panel.get_by_role('button', name='Use this recording').click()
    page.wait_for_function("document.querySelectorAll('[data-card]').length === 6")
    assert page.evaluate('previewData.revisions[7].length') == 3
    undo = page.get_by_role('button', name='Undo', exact=True)
    expect(undo).to_be_visible()
    undo.click()
    page.wait_for_function("document.querySelectorAll('[data-card]').length === 7")
    expect(page.locator('[data-save-state]')).to_contain_text('Unsaved changes')
    page.get_by_role('button', name='Save draft', exact=True).click()
    page.wait_for_function('previewData.revisions[7].length === 4')
    page.get_by_text('More', exact=True).click()
    page.get_by_role('button', name='Saved versions', exact=True).click()
    expect(page.locator('[data-history-panel]')).to_contain_text('from Inventory aging')
    shot(page, evidence, 'recording-template-undo-history')
    # Module restriction: a pending ASAP website sees ASAP recordings only.
    page.evaluate("document.querySelector('.flow-recording-page').remove(); FlowRecordings.open(11, {name:'Returns report', site_id:2})")
    page.get_by_role('button', name='Choose from template', exact=True).click()
    expect(panel.locator('.recording-template-item')).to_have_count(1)
    assert panel.locator('.recording-template-item strong').inner_text() == 'Sell-out weekly'
    expect(panel).to_contain_text('ASAP Flow')
    # Empty and error states.
    page.evaluate("document.querySelector('.flow-recording-page').remove(); previewData.revisions[9]=[]; FlowRecordings.open(11, {name:'Returns report', site_id:2})")
    page.get_by_role('button', name='Choose from template', exact=True).click()
    expect(panel).to_contain_text('No other ASAP Flow has a saved recording yet')
    page.evaluate("document.querySelector('.flow-recording-page').remove(); window.__realApi=window.__realApi||window.api; const real=window.__realApi; window.api=async p=>{ if(p.includes('/templates')) throw Error('Connection lost. Try again.'); return real(p); }; FlowRecordings.open(11, {name:'Returns report'})")
    page.get_by_role('button', name='Choose from template', exact=True).click()
    expect(panel).to_contain_text('Connection lost')
    panel.get_by_role('button', name='Cancel', exact=True).click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("window.api=window.__realApi; document.querySelector('.flow-recording-page').remove(); previewShow('recording', 7)")
    page.wait_for_function("document.querySelectorAll('[data-card]').length === 7")
    page.get_by_role('button', name='Choose from template', exact=True).click()
    panel.get_by_role('option', name='Inventory aging').click()
    panel.locator('.recording-template-steps li').first.wait_for()
    no_overflow(page)
    shot(page, evidence, 'recording-template-narrow')


def test_replicate_copies_recording_by_default_into_a_paused_new_flow(preview):
    page, evidence, _ = preview
    page.evaluate("previewShow('new')")
    expect(page.locator('#flow-replicate-recording-field')).to_be_hidden()
    page.select_option('#flow-replicate-source', '7')
    expect(page.locator('#flow-replicate-recording-field')).to_be_visible()
    expect(page.locator('#flow-replicate-recording')).to_be_checked()
    page.get_by_role('button', name='Copy settings', exact=True).click()
    expect(page.locator('#flow-replicate-status')).to_contain_text('recording is copied as a new draft')
    assert page.locator('#flow-builder-form').get_attribute('data-replicate-recording') == '1'
    page.locator('#flow-name').fill('Regional orders EU')
    shot(page, evidence, 'replicate-new-flow')
    page.get_by_role('button', name='Create flow', exact=True).click()
    page.wait_for_function("document.querySelectorAll('[data-card]').length === 6")
    paths = page.evaluate('previewCalls.filter(c=>c.method==="POST").map(c=>c.path)')
    assert '/api/flows' in paths and '/api/flows/12/recordings/revisions/copy' in paths
    created = page.evaluate('previewData.flows.find(f=>f.id===12)')
    assert created['enabled'] is False and created['recording_revision_id'] is None
    assert page.evaluate('previewData.revisions[12][0].template_source.source_revision_id') == 3
    assert page.evaluate('previewPayload.recording_revision_id') is None
    shot(page, evidence, 'replicate-copied-recording')


def test_run_history_and_run_log_show_stages_and_retry_only_unfinished_views(preview):
    page, evidence, _ = preview
    page.evaluate("previewShow('runs')")
    row = page.locator('tr', has_text='#41')
    expect(row).to_contain_text('Materialized views: 1 of 3 refreshed · SQL insertion committed')
    expect(row.get_by_role('button', name='Retry view refresh')).to_be_visible()
    expect(page.locator('tr', has_text='#40')).to_contain_text('1 of 1 refreshed')
    assert page.locator('tr', has_text='#39').get_by_role('button', name='Retry view refresh').count() == 0
    shot(page, evidence, 'runs-retry-available')
    # Run log: SQL outcome stays separate from each view's status; retry only the unfinished views.
    page.evaluate("previewShow('log', 41)")
    frame = page.frame_locator('#preview-log')
    section = frame.locator('.flow-log-views')
    expect(section.locator('h2')).to_have_text('SQL insertion → Refresh materialized views')
    expect(section).to_contain_text('SQL insertion committed · 1284 row(s)')
    expect(section).to_contain_text('1 of 3 materialized view(s) refreshed')
    rows = section.locator('tbody tr')
    expect(rows).to_have_count(3)
    texts = [rows.nth(i).inner_text().lower() for i in range(3)]
    assert 'succeeded' in texts[0] and 'failed' in texts[1] and 'skipped' in texts[2]
    expect(rows.nth(1)).to_contain_text('lock timeout')
    expect(frame.locator('#flow-retry-views')).to_be_visible()
    assert frame.locator('#flow-retry-sql').count() == 0
    shot(page, evidence, 'run-log-failed-view')
    frame.locator('#flow-retry-views').click()
    page.wait_for_function('previewData.runs[0].id === 44')
    expect(frame.locator('h1')).to_contain_text('Run #44')
    expect(frame.locator('.flow-log-views')).to_contain_text('Refresh-only retry of run #41; nothing was downloaded, transformed or inserted again')
    expect(frame.locator('.flow-log-views')).to_contain_text('SQL insertion committed · 1284 row(s) in run #41')
    expect(frame.locator('.flow-log-views tbody tr').nth(2)).to_contain_text('succeeded')
    assert frame.locator('#flow-retry-views').count() == 0
    shot(page, evidence, 'run-log-retry-succeeded')
    # The retry can fail again; recovery stays available from the history table.
    page.evaluate("previewReset=document.getElementById('preview-reset'); previewReset.click()")
    page.select_option('#preview-retry', 'fail')
    page.evaluate("previewShow('runs')")
    page.locator('tr', has_text='#41').get_by_role('button', name='Retry view refresh').click()
    page.wait_for_function('previewData.runs[0].id === 44 && previewData.runs[0].status === "failed"')
    failed = page.locator('tr', has_text='#44')
    expect(failed).to_contain_text('failed')
    expect(failed.get_by_role('button', name='Retry view refresh')).to_be_visible()
    assert page.locator('tr', has_text='#41').get_by_role('button', name='Retry view refresh').count() == 0
    expect(page.locator('tr', has_text='#44')).to_contain_text('1 of 3 refreshed')
    shot(page, evidence, 'runs-retry-failed-again')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("previewShow('log', 44)")
    frame.locator('.flow-log-views').wait_for()
    no_overflow(page)
    assert page.frame(name=None, url=lambda u: 'run-log' in u).evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, evidence, 'run-log-narrow')


def test_copying_range_template_preserves_recording_version_three(preview):
    page, _, _ = preview
    page.evaluate("""
        () => {
            const definition = previewData.revisions[7].find(item => item.id === 3).definition;
            const index = definition.steps.findIndex(item => item.id === 'regional-setting');
            const source = structuredClone(definition.steps[index]);
            definition.version = 3;
            definition.steps[index] = {
                id: source.id,
                action: 'select_range',
                page: source.page,
                locator: [...source.locator, {method: 'locator', args: ['xpath=..'], kwargs: {}}],
                range: {
                    unit: 'week', start: '2026-W33', end: 'latest_selectable', selection: 'inclusive',
                    cell_selector: 'button.week', selected_state: 'auto', navigation: {kind: 'scroll'},
                    anchor_locator: source.locator, container_ancestor_levels: 1,
                    recorded_weeks: ['2026-W33'], source_step: source,
                },
            };
        }
    """)
    page.evaluate("previewShow('recording', 11)")
    page.locator('[data-editor] [data-begin-template]').click()
    panel = page.locator('[data-template-panel]')
    panel.get_by_role('option', name='Regional orders').click()
    panel.locator('.recording-template-steps li').first.wait_for()
    panel.get_by_role('button', name='Use this recording').click()
    page.get_by_role(
        'button', name='Select week range from 2026-W33', exact=True
    ).click()
    page.get_by_label('Step name (display only)', exact=True).fill('Weekly range')
    page.get_by_role('button', name='Save draft', exact=True).click()
    page.get_by_text('Draft saved', exact=True).wait_for()
    definition = page.evaluate(
        "previewCalls.filter(c=>c.path.endsWith('/recordings/revisions')).at(-1).body.definition"
    )
    assert definition['version'] == 3
    assert any(step['action'] == 'select_range' for step in definition['steps'])
    validate_definition(definition)


def test_email_step_builder_run_history_and_send_again(preview):
    page, evidence, _ = preview
    # Builder: the Email step is the last block of After download, independent of SQL.
    toggle = page.locator('#flow-email-enabled')
    expect(toggle).to_be_checked()
    expect(page.locator('#flow-email-recipients')).to_have_value('ops@example.test; regional.lead@example.test')
    titles = page.locator('#flow-step-body-after h3')
    assert titles.last.inner_text() == 'Email the final file'
    toggle.uncheck()
    expect(page.locator('#flow-email-fields')).to_be_hidden()
    toggle.check()
    expect(page.locator('#flow-email-fields')).to_be_visible()
    page.locator('#flow-sql-enabled').uncheck()
    expect(page.locator('#flow-email-fields')).to_be_visible()
    page.locator('#flow-sql-enabled').check()
    shot(page, evidence, 'builder-email-step')
    # An invalid address comes back from the server; the builder opens the step and focuses recipients.
    page.locator('#flow-email-recipients').fill('planner')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.locator('.flow-form-error')).to_contain_text('Invalid email address: planner')
    assert page.evaluate("document.activeElement.id") == 'flow-email-recipients'
    shot(page, evidence, 'builder-email-invalid')
    # Empty recipients are caught before submission and keep the entered work.
    page.locator('#flow-email-recipients').fill('')
    page.get_by_role('button', name='Save changes', exact=True).click()
    assert page.evaluate("document.activeElement.id") == 'flow-email-recipients'
    page.locator('#flow-email-recipients').fill('planner@example.test; ops@example.test')
    page.locator('#flow-email-subject').fill('Regional orders file')
    page.get_by_role('button', name='Save changes', exact=True).click()
    page.wait_for_function('window.previewPayload && window.previewPayload.email_delivery')
    assert page.evaluate('window.previewPayload.email_delivery') == {
        'enabled': True, 'recipients': ['planner@example.test', 'ops@example.test'], 'subject': 'Regional orders file',
    }
    expect(page.locator('#preview-status')).to_contain_text('Flow saved')
    # New flows start with the step off.
    page.evaluate("previewShow('new')")
    expect(page.locator('#flow-email-enabled')).not_to_be_checked()
    # Run history reuses one status text per run.
    page.evaluate("previewShow('runs')")
    expect(page.locator('tr', has_text='#40')).to_contain_text('Email submitted by Outlook')
    expect(page.locator('tr', has_text='#39')).to_contain_text('Email not sent: Failed to launch Outlook email task')
    expect(page.locator('tr', has_text='#38')).to_contain_text('Attachments skipped: 34.2 MB exceeds the 20 MB limit')
    expect(page.locator('tr', has_text='#41')).to_contain_text('Email: not attempted yet')
    shot(page, evidence, 'runs-email-status')
    # Run log: a failed hand-off explains itself and offers Send again.
    page.evaluate("previewShow('log', 39)")
    frame = page.frame_locator('#preview-log')
    section = frame.locator('.flow-log-email')
    expect(section.locator('h2')).to_have_text('Email the final file')
    expect(section).to_contain_text('Not sent')
    expect(section).to_contain_text('Access is denied')
    expect(section).to_contain_text('planner@example.test')
    expect(section).to_contain_text('sellout.csv')
    expect(frame.locator('#flow-resend-email')).to_be_visible()
    shot(page, evidence, 'run-log-email-failed')
    # Send again asks for confirmation; a failed hand-off reports beside the button and stays available.
    page.select_option('#preview-email', 'fail')
    page.once('dialog', lambda dialog: dialog.accept())
    frame.locator('#flow-resend-email').click()
    expect(frame.locator('#flow-resend-email-status')).to_contain_text('Email not sent: Email could not be handed to Outlook')
    expect(frame.locator('#flow-resend-email')).to_be_enabled()
    # Cancelling the confirmation sends nothing.
    page.select_option('#preview-email', 'submitted')
    page.once('dialog', lambda dialog: dialog.dismiss())
    frame.locator('#flow-resend-email').click()
    expect(section).to_contain_text('Not sent')
    page.once('dialog', lambda dialog: dialog.accept())
    frame.locator('#flow-resend-email').click()
    expect(section.locator('.flow-log-email-status')).to_contain_text('Submitted by Outlook')
    expect(page.locator('#preview-status')).to_contain_text('Send again in preview')
    shot(page, evidence, 'run-log-email-sent')
    # Over-cap runs say the file was described instead of attached; a failed run never attempted.
    page.evaluate("previewShow('log', 38)")
    expect(frame.locator('.flow-log-email')).to_contain_text('Attachments skipped: 34.2 MB exceeds the 20 MB limit')
    page.evaluate("previewShow('log', 41)")
    expect(frame.locator('.flow-log-email')).to_contain_text('Not attempted yet')
    assert frame.locator('#flow-resend-email').count() == 0
    # Narrow layout keeps the step and the run-log section readable.
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("previewShow('builder')")
    open_after(page)
    page.locator('#flow-email-recipients').scroll_into_view_if_needed()
    no_overflow(page)
    shot(page, evidence, 'builder-email-narrow')
    page.evaluate("previewShow('log', 39)")
    expect(frame.locator('.flow-log-email')).to_be_visible()
    assert page.evaluate("document.querySelector('#preview-log').contentDocument.documentElement.scrollWidth <= document.querySelector('#preview-log').clientWidth")
    shot(page, evidence, 'run-log-email-narrow')
