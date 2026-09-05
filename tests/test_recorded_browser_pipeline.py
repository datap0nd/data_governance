"""Actual Chromium interactions against a synthetic, changing report server."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from app import flow_browser, flow_recording, flow_recording_runtime as runtime
from test_flow_recordings import draft_job, report_server
from test_flows import flow_db


@pytest.fixture
def popup_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/export'):
                content = b'Code,Period\nA,2026-01-01\n'
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Content-Disposition', 'attachment; filename="report.csv"')
            else:
                if self.path == '/popup':
                    content = b'<h1>Sales Report</h1><a href="/export">Download</a>'
                elif self.path == '/frame':
                    content = b'<button onclick="window.open(\'/popup\')">Export window</button>'
                else:
                    content = b'''<h1>Sales Report</h1><iframe id="report" src="/frame"></iframe>
                        <div id="loader" style="display:none">Loading</div>
                        <button onclick="document.querySelector('#loader').style.display='block';
                        setTimeout(()=>{const f=document.querySelector('#report'); f.replaceWith(f.cloneNode());
                        document.querySelector('#loader').outerHTML='<div id=loader style=display:none>Done</div>';},100)">Generate</button>'''
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/report'
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_iframe_replacement_popup_and_two_correlated_downloads(flow_db, tmp_path, popup_server):
    source = f'''from playwright.sync_api import Playwright, sync_playwright, expect
def run(playwright: Playwright):
    browser = playwright.chromium.launch()
    context = browser.new_context()
    page = context.new_page()
    page.goto({popup_server!r})
    page.get_by_role("button", name="Generate").click()
    with page.expect_popup() as popup_info:
        page.locator("#report").content_frame.get_by_role("button", name="Export window").click()
    page1 = popup_info.value
    expect(page1.get_by_role("heading")).to_have_text("Sales Report")
    with page1.expect_download() as info:
        page1.get_by_role("link", name="Download").click()
    download = info.value
    with page1.expect_download() as info2:
        page1.get_by_role("link", name="Download").click()
    download2 = info2.value
    context.close()
    browser.close()
with sync_playwright() as playwright:
    run(playwright)
'''
    value = flow_recording.import_codegen(source)
    steps = list(flow_recording.walk_steps(value['steps']))
    value['identity'] = {'text': 'Sales Report', 'target': {'page': 'page1', 'locator': [
        {'method': 'get_by_role', 'args': ['heading'], 'kwargs': {}}]}}
    value['readiness'] = {'mode': 'loading_cycle', 'trigger_step_id': next(s['id'] for s in steps if s['action'] == 'click'),
        'target': {'page': 'page', 'locator': [{'method': 'locator', 'args': ['#loader'], 'kwargs': {}}]}}
    for step in steps:
        if step['action'] == 'download':
            step['output'] = {'format': 'csv', 'headers': ['Code', 'Period']}
    _, job = draft_job(popup_server)
    job['recording']['definition'] = value
    job['recording_parameters'] = {}
    job['downloads']['output_mode'] = 'direct_replace'
    profile = tmp_path / 'profile'
    with sync_playwright() as playwright:
        context = flow_browser.launch(playwright, profile, 'chrome', headed=False, downloads=profile / 'downloads')
        try:
            state = runtime.execute_recorded_flow(context.pages[0], job, lambda *a, **k: None, profile,
                run_id=10, register_folder=lambda _: {'ops': []})
        finally:
            context.close()
    outputs = state['artifacts']
    assert len(outputs) == 2
    assert len({item['export_view'] for item in outputs}) == 2
    assert all(item['publish_status'] == 'published' for item in outputs)


@pytest.mark.parametrize('failure', ['schema', 'period', 'default_changed', 'date_range'])
def test_acquisition_checks_fail_before_publication_and_sql(flow_db, tmp_path, report_server, failure, monkeypatch):
    from app import flow_sql
    _, job = draft_job(report_server)
    job['sql_handoff']['enabled'] = True
    value = job['recording']['definition']
    download = next(s for s in flow_recording.walk_steps(value['steps']) if s['action'] == 'download')
    if failure == 'schema':
        download['output']['headers'] = ['Wrong']
    elif failure == 'period':
        job['recording_parameters']['start'] = '2026-02-01'
    elif failure == 'default_changed':
        job['resume'] = {'recording_defaults': {'end': '2026-09-04'}}
    else:
        job['recording_parameters']['start'] = '2027-01-01'
    monkeypatch.setattr(flow_sql, 'load_artifacts', lambda *a, **k: pytest.fail('Invalid acquisition reached SQL'))
    profile = tmp_path / 'profile'
    with sync_playwright() as playwright:
        context = flow_browser.launch(playwright, profile, 'chrome', headed=False, downloads=profile / 'downloads')
        try:
            with pytest.raises(RuntimeError):
                runtime.execute_recorded_flow(context.pages[0], job, lambda *a, **k: None, profile,
                    run_id=11, register_folder=lambda _: {'ops': []})
        finally:
            context.close()
