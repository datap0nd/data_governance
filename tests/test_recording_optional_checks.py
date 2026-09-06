"""Real downloads replay without page questions; data checks are opt-in."""
import csv
import io
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import openpyxl
import pytest
from playwright.sync_api import sync_playwright

from app import flow_browser, flow_portable, flow_recording, flow_recording_runtime as runtime
from test_flow_recordings import definition, draft_job
from test_flows import flow_db


@pytest.fixture
def downloads_server():
    exports = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in exports:
                content = exports[self.path]
                self.send_response(200)
                self.send_header('Content-Disposition', f'attachment; filename="{Path(self.path).name}"')
                self.send_header('Content-Type', 'application/octet-stream')
            else:
                content = ('<nav><button>Reports</button><button>Exports</button></nav>'
                    '<label>Date<input value="2026-09-06"></label>' + ''.join(
                    f'<a href="{path}">{Path(path).name}</a>' for path in exports)).encode()
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
        yield f'http://127.0.0.1:{server.server_port}/report', exports
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def export_bytes(fmt, rows):
    if fmt == 'xlsx':
        workbook = openpyxl.Workbook()
        for row in rows:
            workbook.active.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()
        return buffer.getvalue()
    buffer = io.StringIO(newline='')
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


def replay_job(url, outputs):
    _, job = draft_job(url)
    steps = [dict(id='open', action='goto', page='page', args=[url])]
    for index, specification in enumerate(outputs, 1):
        fmt = specification['format']
        steps.append(dict(id=f'download-{index}', action='download', page='page', output=specification,
            steps=[dict(id=f'click-{index}', action='click', page='page',
                locator=[dict(method='get_by_role', args=['link'], kwargs={'name': f'{index}.{fmt}', 'exact': True})])]))
    job['recording']['definition'] = dict(version=2, timezone='UTC', steps=steps, parameters={})
    job['recording_parameters'] = {}
    job['downloads'].update(output_mode='direct_replace', filename_template='output_{index}.' + outputs[0]['format'],
        file_format=outputs[0]['format'], asap_download_type=None, excel_trim='none')
    return job


def run_browser(job, profile, *, state=None, events=None):
    events = events if events is not None else []
    with sync_playwright() as playwright:
        context = flow_browser.launch(playwright, profile, 'chrome', headed=False, downloads=profile / 'downloads')
        try:
            return runtime.execute_recorded_flow(context.pages[0], job,
                lambda status, detail, *args: events.append(detail), profile,
                run_id=21, register_folder=lambda _: {'ops': []}, state=state)
        finally:
            context.close()


@pytest.mark.parametrize('minimum', [-1, True, 1.5, '3', None])
def test_minimum_rows_must_be_nonnegative_integer(minimum):
    value = definition()
    next(step for step in flow_recording.walk_steps(value['steps']) if step['action'] == 'download')['output']['min_rows'] = minimum
    with pytest.raises(ValueError, match='Minimum data rows'):
        flow_recording.validate_definition(value)


@pytest.mark.parametrize('fmt', ['html', 'txt'])
def test_row_checks_require_tabular_output(fmt):
    value = definition()
    next(step for step in flow_recording.walk_steps(value['steps']) if step['action'] == 'download')['output'] = dict(format=fmt, min_rows=1)
    with pytest.raises(ValueError, match='Data checks'):
        flow_recording.validate_definition(value)


@pytest.mark.parametrize('fmt,rows', [('csv', [['Code', 'Amount']]), ('xlsx', []), ('xlsx', [['Code', 'Amount']])])
def test_download_without_checks_accepts_empty_valid_data_and_ignores_old_metadata(flow_db, tmp_path, downloads_server, fmt, rows):
    url, exports = downloads_server
    exports[f'/1.{fmt}'] = export_bytes(fmt, rows)
    job = replay_job(url, [dict(format=fmt, allow_empty=False)])
    if fmt == 'csv':
        job['recording']['definition']['steps'][-1]['output']['min_rows'] = 0
    # Old generated requirements may reference deleted steps and absent pages.
    job['recording']['definition'].update(identity={'text': 'Outdated title', 'target': {'page': 'absent'}},
        readiness={'mode': 'changed_text', 'trigger_step_id': 'deleted', 'target': {'page': 'absent'}})
    state = run_browser(job, tmp_path / 'profile')
    assert len(state['artifacts']) == 1
    output = Path(job['downloads']['target_folder']) / f'output_1.{fmt}'
    assert output.read_bytes() == exports[f'/1.{fmt}']
    assert state['artifacts'][0]['publish_status'] == 'published'


@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
def test_optional_minimum_counts_nonblank_data_rows_without_header(flow_db, tmp_path, downloads_server, fmt):
    url, exports = downloads_server
    rows = [['Code', 'Amount'], ['A', 1], [' ', ' '], ['B', 2], ['', ''], ['C', 3]]
    exports[f'/1.{fmt}'] = export_bytes(fmt, rows)
    job = replay_job(url, [dict(format=fmt, min_rows=3)])
    state = run_browser(job, tmp_path / 'profile')
    assert state['artifacts'][0]['publish_status'] == 'published'
    assert state['artifacts'][0]['row_count'] == 3


@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
def test_optional_minimum_fails_before_publishing_any_output(flow_db, tmp_path, downloads_server, fmt):
    url, exports = downloads_server
    exports[f'/1.{fmt}'] = export_bytes(fmt, [['Code', 'Amount'], ['A', 1], ['B', 2], ['C', 3]])
    exports[f'/2.{fmt}'] = export_bytes(fmt, [['Code', 'Amount'], ['A', 1], [' ', ' '], ['B', 2]])
    job = replay_job(url, [dict(format=fmt, min_rows=3), dict(format=fmt, min_rows=3)])
    destination = Path(job['downloads']['target_folder'])
    previous = destination / f'output_1.{fmt}'
    previous.write_bytes(b'previous successful output')
    events, state = [], {}
    with pytest.raises(RuntimeError, match='download-2: downloaded data has 2 rows; at least 3 required'):
        run_browser(job, tmp_path / 'profile', state=state, events=events)
    assert previous.read_bytes() == b'previous successful output'
    assert not (destination / f'output_2.{fmt}').exists()
    assert not any(event.get('stage') == 'direct_publish' for event in events)
    assert events[-1]['failure_reason'] == 'output_validation_failed'


def test_explicit_recorded_assertions_still_execute(flow_db, tmp_path, downloads_server):
    url, exports = downloads_server
    exports['/1.csv'] = export_bytes('csv', [['Code'], ['A']])
    job = replay_job(url, [dict(format='csv')])
    job['recording']['definition']['steps'].insert(1, dict(id='recorded-check', action='assert', assertion='to_have_title',
        page='page', args=['Explicit expected title'], kwargs={'timeout': 100}))
    events = []
    with pytest.raises(AssertionError):
        run_browser(job, tmp_path / 'profile', events=events)
    assert events[-1]['step_id'] == 'recorded-check'
    assert events[-1]['failure_reason'] == 'recorded_action_failed'


def test_dates_follow_recorded_order_without_a_generation_trigger():
    value = definition()
    value.pop('identity')
    value.pop('readiness')
    fill = next(step for step in value['steps'] if step['action'] == 'fill')
    value['steps'].remove(fill)
    value['steps'].append(fill)
    assert flow_recording.validate_definition(value) is value


@pytest.mark.parametrize('part', [dict(method='first'), dict(method='last'), dict(method='nth', args=[1]),
    dict(method='locator', args=['a:nth-child(2)'])])
def test_supported_positional_locators_do_not_require_a_manual_repair(part):
    value = definition()
    click = next(step for step in flow_recording.walk_steps(value['steps']) if step['action'] == 'click')
    click['locator'].append(part)
    assert flow_recording.validate_definition(value) is value


@pytest.mark.parametrize('part', [dict(method='last'), dict(method='nth', args=[1])])
def test_recorded_positional_click_replays_as_captured(flow_db, tmp_path, downloads_server, part):
    url, exports = downloads_server
    exports['/other.csv'] = export_bytes('csv', [['Code'], ['Wrong download']])
    exports['/1.csv'] = export_bytes('csv', [['Code'], ['Recorded download']])
    job = replay_job(url, [dict(format='csv')])
    job['recording']['definition']['steps'][-1]['steps'][0]['locator'] = [dict(method='get_by_role', args=['link']), part]
    run_browser(job, tmp_path / 'profile')
    assert (Path(job['downloads']['target_folder']) / 'output_1.csv').read_bytes() == exports['/1.csv']


def test_later_page_default_is_not_read_before_page_exists(flow_db, tmp_path, downloads_server):
    url, exports = downloads_server
    exports['/1.csv'] = export_bytes('csv', [['Code'], ['A']])
    job = replay_job(url, [dict(format='csv')])
    value = job['recording']['definition']
    value['steps'].extend([dict(id='new', action='new_page', page='later'),
        dict(id='open-later', action='goto', page='later', args=[url])])
    value['parameters'] = {'date': dict(mode='portal_default', format='%Y-%m-%d',
        target=dict(page='later', locator=[dict(method='get_by_label', args=['Date'], kwargs={})]))}
    state = run_browser(job, tmp_path / 'profile')
    assert state['artifacts'][0]['recording_defaults'] == {'date': '2026-09-06'}


@pytest.mark.parametrize('fmt,minimum,count', [('xlsx', 0, 0), ('csv', 3, 3), ('csv', 3, 2)])
def test_portable_replays_without_page_questions_and_honors_optional_rows(flow_db, tmp_path, downloads_server, fmt, minimum, count):
    url, exports = downloads_server
    exports[f'/1.{fmt}'] = export_bytes(fmt, [['Code', 'Amount'], *[[str(index), index] for index in range(count)]])
    job = replay_job(url, [dict(format=fmt, min_rows=minimum)])
    job['execution']['browser_channel'] = 'chrome'
    script = tmp_path / 'portable.py'
    script.write_text(flow_portable.source(job), encoding='utf-8')
    output_root = tmp_path / 'portable-output'
    result = subprocess.run([sys.executable, '-I', str(script), '--headless', '--output-root', str(output_root)],
        cwd=tmp_path, capture_output=True, text=True, timeout=90)
    published = list(output_root.glob(f'*/*/Downloads/output_1.{fmt}'))
    if count < minimum:
        assert result.returncode == 1
        assert 'downloaded data has 2 rows; at least 3 required' in result.stderr
        assert not published
    else:
        assert result.returncode == 0, result.stderr
        assert len(published) == 1
        if not minimum:
            assert published[0].read_bytes() == exports[f'/1.{fmt}']
