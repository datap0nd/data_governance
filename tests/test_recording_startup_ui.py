"""Real recording controls and polling against fictional worker responses."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest
from playwright.sync_api import sync_playwright, expect


@pytest.fixture
def startup_page():
    handler = partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).parents[1] / 'app'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1280, 'height':960})
            page.add_init_script('window.RECORDING_STARTUP_AUTOPLAY=false;')
            errors=[]
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/static/recording-preview/startup.html')
            expect(page.get_by_role('button', name='Test recording', exact=True)).to_be_visible()
            yield page
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_test_startup_failure_retry_and_cancel_preserve_actions(startup_page):
    page=startup_page
    page.get_by_role('button', name='Click “Download Excel”', exact=True).click()
    page.get_by_label('Step name', exact=True).fill('Download my weekly workbook')
    action_ids=page.locator('[data-card]').evaluate_all('(cards)=>cards.map(c=>c.dataset.card)')
    page.get_by_role('button', name='Test recording', exact=True).click()
    expect(page.locator('p[data-session]')).to_have_text('Waiting for worker…')
    expect(page.get_by_role('button', name='Testing…', exact=True)).to_be_disabled()
    calls=page.evaluate('RecordingStartupPreview.calls')
    assert [c['path'] for c in calls] == ['/api/flows/901/recordings/revisions', '/api/flows/901/recordings/revisions/2/validate']
    saved=calls[0]['body']['definition']
    assert saved['steps'][-1]['label']=='Download my weekly workbook'

    page.evaluate("RecordingStartupPreview.transition('claimed')")
    expect(page.locator('p[data-session]')).to_have_text('Opening browser…')
    page.evaluate("RecordingStartupPreview.transition('running')")
    expect(page.locator('p[data-session]')).to_have_text('Testing recording…')
    page.evaluate("RecordingStartupPreview.transition('failed', {message:'Starting worker…'}, 'The browser could not open. Try Test recording again.')")
    expect(page.locator('p[data-session]')).to_have_text('The browser could not open. Try Test recording again.')
    expect(page.get_by_role('button', name='Test recording', exact=True)).to_be_enabled()
    expect(page.get_by_label('Step name', exact=True)).to_have_value('Download my weekly workbook')
    assert page.locator('[data-card]').evaluate_all('(cards)=>cards.map(c=>c.dataset.card)')==action_ids

    page.get_by_role('button', name='Test recording', exact=True).click()
    expect(page.locator('p[data-session]')).to_have_text('Waiting for worker…')
    page.get_by_role('button', name='Cancel test', exact=True).click()
    expect(page.locator('p[data-session]')).to_have_text('Test cancelled.')
    expect(page.get_by_role('button', name='Test recording', exact=True)).to_be_enabled()
    expect(page.get_by_label('Step name', exact=True)).to_have_value('Download my weekly workbook')
    assert page.get_by_role('button', name='Cancel test', exact=True).count()==0
    assert page.evaluate('RecordingStartupPreview.data.flow.enabled') is False
    assert all('/activate' not in c['path'] for c in page.evaluate('RecordingStartupPreview.calls'))


def test_terminal_failure_has_feedback_without_error_and_fits_narrow_screen(startup_page):
    page=startup_page
    page.set_viewport_size({'width':390, 'height':844})
    page.get_by_role('button', name='Test recording', exact=True).click()
    expect(page.locator('p[data-session]')).to_have_text('Waiting for worker…')
    page.evaluate("RecordingStartupPreview.transition('failed', {message:'Starting worker…'})")
    expect(page.locator('p[data-session]')).to_have_text('Could not complete this session. Try again.')
    expect(page.get_by_role('button', name='Test recording', exact=True)).to_be_enabled()
    assert page.locator('p[data-session]').bounding_box()['y'] < page.viewport_size['height']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('[data-card]').count()==4
    page.get_by_role('button', name='Back to Edit Flow', exact=True).click()
    expect(page.get_by_role('button', name='Review recording', exact=True)).to_be_visible()
    page.get_by_role('button', name='Review recording', exact=True).click()
    expect(page.locator('p[data-session]')).to_have_text('Could not complete this session. Try again.')
    assert page.locator('[data-card]').count()==4
