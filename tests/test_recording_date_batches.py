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


def batched_definition(url='http://localhost/report'):
    value = definition(url)
    value['parameters']['start']['value'] = '2025-01-01'
    value['parameters']['end'].update(mode='fixed', value='2026-12-31')
    value['date_batch'] = {'start_parameter': 'start', 'end_parameter': 'end', 'weeks': 10}
    return value


def test_two_years_in_ten_week_ranges_have_no_gaps_overlaps_or_lost_tail():
    value = flow_recording.validate_definition(batched_definition())
    batches = flow_recording.resolve_batches(value, flow_recording.resolve_parameters(value))
    assert len(batches) == 11
    assert batches[0]['period'] == ['2025-01-01', '2025-03-11']
    assert batches[-1]['period'] == ['2026-12-02', '2026-12-31']
    days = []
    for item in batches:
        start, end = [date.fromisoformat(day) for day in item['period']]
        assert (end - start).days < 70
        days.extend(start + timedelta(days=i) for i in range((end - start).days + 1))
    assert len(days) == len(set(days)) == 730
    assert days == [date(2025, 1, 1) + timedelta(days=i) for i in range(730)]


@pytest.mark.parametrize('change,reason', [
    (lambda d: d['date_batch'].update(weeks=0), 'batch size'),
    (lambda d: d['date_batch'].update(weeks=True), 'batch size'),
    (lambda d: d['date_batch'].update(end_parameter='start'), 'distinct'),
    (lambda d: d['parameters']['end'].update(mode='portal_default'), 'fixed or calculated'),
    (lambda d: d['parameters']['end'].update(value='2024-01-01'), 'after'),
    (lambda d: d['parameters']['start'].update(value='1800-01-01'), '500'),
])
def test_invalid_batches_cannot_activate(change, reason):
    value = batched_definition(); change(value)
    with pytest.raises(ValueError, match=reason): flow_recording.validate_definition(value)


def test_batch_dates_freeze_worker_timezone_and_keep_leap_day():
    value = batched_definition()
    value['timezone'] = 'America/Los_Angeles'
    value['parameters']['start']['value'] = '2024-02-28'
    value['parameters']['end'].update(mode='calculated', expression='today')
    frozen = flow_recording.resolve_parameters(value, now=datetime(2024, 3, 1, 1, tzinfo=timezone.utc))
    # resolve_parameters accepts an explicit instant and resolves in Flow timezone.
    assert frozen['end'] == '2024-02-29'
    assert flow_recording.resolve_batches(value, frozen)[0]['period'] == ['2024-02-28', '2024-02-29']


def test_validating_a_frozen_batch_does_not_resolve_calculated_dates_again(monkeypatch):
    value = batched_definition()
    value['parameters']['start'].update(mode='calculated', expression='today')
    value['parameters']['end']['value'] = '2000-01-01'
    frozen = flow_recording.resolve_parameters(value, now=datetime(2000, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(flow_recording, 'resolve_parameters', lambda *a, **k: pytest.fail('Re-evaluated frozen dates'))
    flow_recording.validate_definition(value)
    assert flow_recording.resolve_batches(value, frozen)[0]['period'] == ['2000-01-01', '2000-01-01']


@pytest.fixture
def batch_server():
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
def test_same_batch_pipeline_downloads_validates_and_transforms_every_range(flow_db, tmp_path, batch_server, portable):
    url, requested = batch_server
    _, job = draft_job(url)
    value = batched_definition(url)
    value['parameters']['end']['value'] = '2025-05-25'
    # Keep the final short batch and capture two distinct outputs per range.
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
    expected = flow_recording.resolve_batches(value, job['recording_parameters'])
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
    assert requested == [item['period'] for item in expected for _ in range(2)]
    outputs = [item for item in artifacts if item.get('export_transport') == 'recorded_browser' and item.get('status') == 'saved']
    assert len(outputs) == 6
    assert {flow_tasks.task_key(item['export_view'], item['period_key']) for item in outputs} == {
        item['key'] for item in flow_tasks.task_matrix(job)}
    assert len({item['file_path'] for item in outputs}) == 6
    assert all(item['bundle_count'] == 6 for item in outputs)
    assert sorted(item['bundle_index'] for item in outputs) == list(range(1, 7))
    assert any('B,2025-05-21' in Path(item['file_path']).read_text(encoding='utf-8-sig')
               for item in artifacts if item.get('file_path') and item['file_path'].endswith('.csv'))


def test_validation_requires_every_batch_output_not_just_unique_step_ids(flow_db):
    saved, job = draft_job()
    value = batched_definition()
    revision = routes.save_revision(saved['id'], routes.RevisionWrite(definition=value))['revision_id']
    with database.get_db() as db:
        scan_id = flow_recordings.queue_operation(db, saved['id'], 'validate', 'test', revision_id=revision)
        row = db.execute('SELECT * FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone()
        frozen = json.loads(row['job_json'])
        matrix = flow_tasks.task_matrix(frozen['validation_job'])
        result = {'configuration_hash': frozen['configuration_hash'],
            'engine_hash': frozen['validation_job']['recording']['engine_hash'],
            'outputs': [{'step_id': item['export_view'], 'period_key': item['period'], 'checksum': 'a' * 64} for item in matrix]}
        partial = copy.deepcopy(result); partial['outputs'].pop()
        with pytest.raises(HTTPException, match='configuration'):
            flow_recordings.update_operation(db, row, 'worker', flows.ScanProgress(status='succeeded', recording_result=partial), '2026-09-05')
        flow_recordings.update_operation(db, row, 'worker', flows.ScanProgress(status='succeeded', recording_result=result), '2026-09-05')
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?', (revision,)).fetchone()[0] == 'validated'


def test_failed_later_batch_never_reaches_publication_transformation_or_sql(flow_db, tmp_path, batch_server, monkeypatch):
    from app import flow_worker, flow_sql
    from app.flow_recorder_worker import browser_session
    url, requested = batch_server
    _, job = draft_job(url)
    value = batched_definition(url)
    value['parameters']['end']['value'] = '2025-03-12'
    job['recording']['definition'] = value
    job['recording_parameters'] = flow_recording.resolve_parameters(value)
    job['sql_handoff']['enabled'] = True
    original = runtime.acquire
    def acquire(page, iteration, *args, **kwargs):
        if iteration['_recording_batch']['index'] == 2:
            step = next(s for s in iteration['recording']['definition']['steps'] if s['action'] == 'download')
            step['output']['headers'] = ['Wrong report']
        return original(page, iteration, *args, **kwargs)
    monkeypatch.setattr(runtime, 'acquire', acquire)
    monkeypatch.setattr(flow_worker, '_publish_direct_artifacts', lambda *a, **k: pytest.fail('Published an incomplete bundle'))
    monkeypatch.setattr(flow_worker, '_run_transformations', lambda *a, **k: pytest.fail('Transformed an incomplete bundle'))
    monkeypatch.setattr(flow_sql, 'load_artifacts', lambda *a, **k: pytest.fail('Loaded incomplete outputs into SQL'))
    profile = tmp_path / 'profile'
    with sync_playwright() as pw:
        with browser_session(pw, profile, 'chrome', headed=False) as (context, _):
            with pytest.raises(RuntimeError, match='columns'):
                runtime.execute_recorded_flow(context.new_page(), job, lambda *a, **k: None, profile,
                    run_id=56, register_folder=lambda _: {'ops': []})
    assert requested == [['2025-01-01', '2025-03-11'], ['2025-03-12', '2025-03-12']]


def test_review_can_configure_one_recording_for_ten_week_batches():
    root = Path(__file__).resolve().parents[1]
    data = {'flow': {'name': 'Date batch fixture'}, 'sessions': [],
        'revisions': [{'id': 1, 'status': 'draft', 'definition': definition()}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.set_content('<main>Batch fixture</main>')
        page.evaluate('''data=>{window.api=async()=>data;window.saved=[];
            window.apiPostJson=async(url,body)=>saved.push(body);window.apiPost=async()=>({});}''', data)
        page.add_script_tag(path=str(root / 'app/static/flow_recordings.js'))
        page.evaluate('()=>FlowRecordings.open(1)')
        page.locator('[data-date-mode]').nth(1).select_option('fixed')
        page.locator('[data-date-value]').nth(0).fill('2025-01-01')
        page.locator('[data-date-value]').nth(1).fill('2026-12-31')
        page.locator('[name=batchEnabled]').check()
        page.get_by_role('button', name='Save reviewed revision').click()
        value = page.evaluate('()=>saved[0].definition')
        assert value['date_batch'] == {'start_parameter': 'start', 'end_parameter': 'end', 'weeks': 10}
        assert len(flow_recording.resolve_batches(flow_recording.validate_definition(value), flow_recording.resolve_parameters(value))) == 11
        browser.close()


@pytest.mark.parametrize('transformed', [False, True])
def test_sql_retry_requires_each_range_once_including_transformed_outputs(flow_db, tmp_path, transformed):
    saved, job = draft_job()
    value = batched_definition()
    value['parameters']['end']['value'] = '2025-03-12'
    job['recording']['definition'] = value
    job['recording_parameters'] = flow_recording.resolve_parameters(value)
    job['sql_handoff']['enabled'] = True
    job['transformation']['enabled'] = transformed
    artifacts = [{'status': 'saved', 'export_view': task['export_view'], 'period_key': task['period'],
        'file_path': str(tmp_path / f"report{index}.csv"), 'filename': f"report{index}.csv"}
        for index, task in enumerate(flow_tasks.task_matrix(job))]
    if transformed:
        artifacts += [{**item, 'status': 'transformed'} for item in list(artifacts)]
    with database.get_db() as db:
        run = db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,artifact_json,created_at) VALUES (?,'manual','failed',?,?,'2026-09-05')",
            (saved['id'], json.dumps(job), json.dumps(artifacts))).lastrowid
        result = flows.inspect_sql_retry_eligibility(db, run, verify_artifact_files=False)
        assert result['reason_code'] == 'sql_artifacts_ready'
        # A duplicated range cannot substitute for a missing final range.
        artifacts[-1]['period_key'] = artifacts[0]['period_key']
        db.execute('UPDATE flow_runs SET artifact_json=? WHERE id=?', (json.dumps(artifacts), run))
        assert flows.inspect_sql_retry_eligibility(db, run, verify_artifact_files=False)['reason_code'] == 'download_bundle_incomplete'


def test_older_workers_cannot_claim_a_date_batch_run(flow_db):
    saved, job = draft_job()
    job['recording']['definition'] = batched_definition()
    job['recording_parameters'] = flow_recording.resolve_parameters(job['recording']['definition'])
    with database.get_db() as db:
        run = db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','queued',?,'2026-09-05')",
            (saved['id'], json.dumps(job))).lastrowid
    capabilities = {'headed': True, 'recorded_flows_v1': True, 'browser_switch_v1': True, 'shared_flow_artifacts': True}
    flows.register_worker(flows.WorkerRegister(worker_id='older', display_name='Older', capabilities=capabilities))
    assert flows.claim_run('older')['run'] is None
    capabilities['recorded_date_batches_v1'] = True
    flows.register_worker(flows.WorkerRegister(worker_id='current', display_name='Current', capabilities=capabilities))
    assert flows.claim_run('current')['run']['id'] == run
