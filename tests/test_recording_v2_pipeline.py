import copy
import csv
import json
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from playwright.sync_api import sync_playwright

from app import database, flow_portable, flow_recording, flow_recordings, flow_tasks
from app import flow_recording_runtime as runtime
from app.routers import flows, flow_recordings as routes
from test_flow_recordings import definition, draft_job
from test_flows import flow_db, _request


@pytest.fixture
def range_server():
    requested = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/export':
                query = parse_qs(url.query)
                start, end = query['start'][0], query['end'][0]
                requested.append([start, end])
                content = f'Code,Period\nA,{start}\n'.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Content-Disposition', 'attachment; filename="report.csv"')
            else:
                content = b'''<h1>Sales Report</h1>
                <label>Start<input id="start" value="2025-01-01"></label>
                <label>End<input id="end" value="2026-12-31"></label><span id="status">Idle</span>
                <button onclick="setTimeout(()=>document.querySelector('#status').textContent='Ready',30)">Generate</button>
                <button onclick="location.href='/export?start='+document.querySelector('#start').value+'&end='+document.querySelector('#end').value">Download</button>'''
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
            self.end_headers(); self.wfile.write(content)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/report', requested
    finally: server.shutdown(); thread.join(timeout=2); server.server_close()


@pytest.mark.parametrize('portable', [False, True])
def test_v2_wait_multiple_outputs_and_transform_match_portable(flow_db, tmp_path, range_server, portable):
    url, requested = range_server
    _, job = draft_job(url)
    value = definition(url)
    value['parameters']['start']['value'] = '2025-01-01'
    value['parameters']['end'].update(mode='fixed', value='2025-05-25')
    value['steps'].insert(1, {'id':'wait-user','action':'wait','page':'page','seconds':1})
    value['parameters']['end']['value'] = '2025-05-25'
    # Two outputs share one explicit range and one cooperative wait.
    download = next(s for s in value['steps'] if s['action'] == 'download')
    duplicate = copy.deepcopy(download)
    duplicate['id'] = 'second-download'; duplicate['steps'][0]['id'] = 'second-trigger'
    value['steps'].append(duplicate)
    job['recording']['definition'] = value
    job['recording_parameters'] = flow_recording.resolve_parameters(value)
    job['transformation']['enabled'] = True
    job['transformation']['script_path'] = str(Path(job['paths']['scripts_folder']) / 'embedded-transform.py')
    job['recording']['transformation_source'] = '''import argparse,pathlib
p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output');a=p.parse_args()
pathlib.Path(a.output).write_text(pathlib.Path(a.input).read_text().replace('A,','B,'))
'''

    if portable:
        script = tmp_path / 'standalone.py'
        script.write_text(flow_portable.source(job), encoding='utf-8')
        output = tmp_path / 'portable-output'
        result = subprocess.run([sys.executable, '-I', str(script), '--headless', '--output-root', str(output)],
            cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        log = next(output.rglob('*.jsonl'))
        artifacts = json.loads(log.read_text().splitlines()[-1])['artifacts']
    else:
        from app.flow_recorder_worker import browser_session
        profile = tmp_path / 'profile'
        with sync_playwright() as pw:
            with browser_session(pw, profile, 'chrome', headed=False) as (context, _):
                page = context.new_page()
                state = runtime.execute_recorded_flow(page, job, lambda *a, **k: None, profile,
                    run_id=55, register_folder=lambda _: {'ops': []})
                assert context.pages == [page]
                artifacts = state['artifacts']
    assert requested == [['2025-01-01', '2025-05-25']] * 2
    outputs = [item for item in artifacts if item.get('export_transport') == 'recorded_browser' and item.get('status') == 'saved']
    assert len(outputs) == 2
    assert {flow_tasks.task_key(item['export_view'], item['period_key']) for item in outputs} == {
        item['key'] for item in flow_tasks.task_matrix(job)}
    assert len({item['file_path'] for item in outputs}) == 2
    assert all(item['bundle_count'] == 2 for item in outputs)
    assert sorted(item['bundle_index'] for item in outputs) == list(range(1, 3))
    assert any('B,2025-01-01' in Path(item['file_path']).read_text(encoding='utf-8-sig')
               for item in artifacts if item.get('file_path') and item['file_path'].endswith('.csv'))
