"""Production editor with a fictional in-memory API; no worker or portal claims."""
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from app.flow_recording import validate_definition

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def optional_editor():
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'app')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.goto(f'http://127.0.0.1:{server.server_port}/static/recording-preview/optional.html')
        page.get_by_role('button', name='Test recording', exact=True).wait_for()
        yield page
        browser.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def latest_definition(page):
    return page.evaluate('()=>optionalPreview.data.revisions[0].definition')


def test_recorded_clicks_test_immediately_and_waits_survive_editing(optional_editor):
    page = optional_editor
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.get_by_text('Test passed. Back to Edit Flow, then Save.', exact=True).wait_for()
    assert page.evaluate('()=>optionalPreview.calls.map(c=>c.path.split("/").pop())') == ['revisions', 'validate']
    assert 'identity' not in latest_definition(page)
    assert 'readiness' not in latest_definition(page)
    assert page.get_by_text('How do we know the report is ready?', exact=True).count() == 0
    page.get_by_role('button', name='Add wait', exact=True).click()
    page.get_by_label('Wait in seconds', exact=True).fill('60')
    page.get_by_label('Wait in seconds', exact=True).press('Tab')
    page.get_by_role('button', name='Move up', exact=True).click()
    page.get_by_role('button', name='Save draft', exact=True).click()
    saved = latest_definition(page)
    assert [step['action'] for step in saved['steps']] == ['goto', 'click', 'wait', 'download']
    assert saved['steps'][2]['seconds'] == 60
    page.get_by_role('button', name='Back to Edit Flow', exact=True).click()
    page.get_by_role('button', name='Recording', exact=True).click()
    page.get_by_role('button', name='Wait 60 seconds', exact=True).click()
    assert page.get_by_label('Wait in seconds', exact=True).input_value() == '60'


def test_optional_download_check_failure_remove_recovery_and_undo(optional_editor):
    page = optional_editor
    page.get_by_role('button', name='Click “Download Excel”', exact=True).click()
    assert page.get_by_label('Minimum data rows', exact=True).count() == 0
    page.get_by_role('button', name='Add data check', exact=True).click()
    assert page.get_by_label('Minimum data rows', exact=True).input_value() == '4'
    page.get_by_label('Step name', exact=True).fill('Orders workbook')
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.locator('[data-session]').filter(has_text='Downloaded 3 data rows; this check requires at least 4.').wait_for()
    assert latest_definition(page)['steps'][-1]['output']['min_rows'] == 4
    assert page.get_by_label('Step name', exact=True).input_value() == 'Orders workbook'
    page.get_by_role('button', name='Remove data check', exact=True).click()
    assert page.get_by_label('Minimum data rows', exact=True).count() == 0
    assert page.get_by_label('Step name', exact=True).input_value() == 'Orders workbook'
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.get_by_text('Test passed. Back to Edit Flow, then Save.', exact=True).wait_for()
    assert 'min_rows' not in latest_definition(page)['steps'][-1]['output']
    page.get_by_role('button', name='Undo', exact=True).click()
    assert page.get_by_label('Minimum data rows', exact=True).input_value() == '4'
    assert page.get_by_text('Unsaved changes', exact=True).is_visible()


def test_optional_checks_mobile_and_existing_columns_remain_removable(optional_editor):
    page = optional_editor
    page.get_by_role('button', name='Click “Download Excel”', exact=True).click()
    page.get_by_role('button', name='Add data check', exact=True).click()
    page.get_by_text('Columns and dates', exact=True).click()
    page.get_by_label('Expected columns, in order', exact=True).fill('Region, Orders')
    page.get_by_label('Expected columns, in order', exact=True).press('Tab')
    page.get_by_role('button', name='Save draft', exact=True).click()
    assert latest_definition(page)['steps'][-1]['output']['headers'] == ['Region', 'Orders']
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('[data-card].selected + .recording-details').count() == 1
    assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
    page.get_by_role('button', name='Remove data check', exact=True).click()
    page.get_by_role('button', name='Save draft', exact=True).click()
    assert latest_definition(page)['steps'][-1]['output'] == {'format': 'xlsx'}


@pytest.mark.parametrize('output_format', ['html', 'txt'])
def test_non_tabular_formats_do_not_offer_data_checks_and_changes_are_recoverable(optional_editor, output_format):
    page = optional_editor
    page.get_by_role('button', name='Click “Download Excel”', exact=True).click()
    page.get_by_label('Output format').select_option(output_format)
    assert page.get_by_role('button', name='Add data check', exact=True).count() == 0
    assert page.get_by_role('button', name='Add row count check', exact=True).count() == 0
    page.get_by_role('button', name='Save draft', exact=True).click()
    validate_definition(latest_definition(page))
    page.get_by_label('Output format').select_option('csv')
    page.get_by_role('button', name='Add data check', exact=True).click()
    page.get_by_label('Step name', exact=True).fill('Saved download action')
    page.get_by_label('Output format').select_option(output_format)
    assert page.get_by_label('Minimum data rows', exact=True).count() == 0
    page.get_by_text('Data checks require XLSX or CSV. Change the format or remove this check.', exact=True).wait_for()
    page.get_by_role('button', name='Remove data check', exact=True).click()
    assert page.get_by_role('button', name='Add data check', exact=True).count() == 0
    assert page.get_by_label('Step name', exact=True).input_value() == 'Saved download action'
    page.get_by_role('button', name='Test recording', exact=True).click()
    page.get_by_text('Test passed. Back to Edit Flow, then Save.', exact=True).wait_for()
    validate_definition(latest_definition(page))
    assert latest_definition(page)['steps'][-1]['output'] == {'format': output_format}


def test_legacy_non_tabular_column_check_can_be_removed(optional_editor):
    page = optional_editor
    page.get_by_role('button', name='Back to Edit Flow', exact=True).click()
    page.evaluate("()=>optionalPreview.data.revisions[0].definition.steps[2].output={format:'html',headers:['Region']}")
    page.get_by_role('button', name='Recording', exact=True).click()
    page.get_by_role('button', name='Click “Download Excel”', exact=True).click()
    assert page.get_by_role('button', name='Add data check', exact=True).count() == 0
    assert page.get_by_role('button', name='Add row count check', exact=True).count() == 0
    page.get_by_role('button', name='Remove data check', exact=True).click()
    page.get_by_role('button', name='Save draft', exact=True).click()
    validate_definition(latest_definition(page))
    assert latest_definition(page)['steps'][-1]['output'] == {'format': 'html'}
