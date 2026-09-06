import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database, flow_portable, flow_recordings, flow_recording_diagnostics as diagnostics
from app import flow_recorder_worker, flow_recording_runtime, flow_worker
from app.routers import flow_recordings as routes, flows
from test_flow_recordings import draft_job
from test_flows import flow_db


def queued_test():
    saved, job = draft_job()
    value = job['recording']['definition']
    value['parameters'] = {}
    value['steps'] = [
        {'id': 'open', 'action': 'goto', 'page': 'page', 'args': ['https://private.example/reports?token=hidden']},
        {'id': 'setting', 'action': 'click', 'page': 'page', 'locator': [{'method': 'get_by_role', 'args': ['button'], 'kwargs': {'name': 'Setting'}}]},
        {'id': 'public', 'action': 'click', 'page': 'page', 'locator': [{'method': 'get_by_text', 'args': ['Public']}]},
        {'id': 'secret', 'action': 'fill', 'page': 'page', 'args': ['PrivateCustomerName'], 'locator': [{'method': 'get_by_label', 'args': ['Search']}]},
    ]
    # Seed an old frozen session directly: debug reading must handle old data
    # without revalidating or starting this recording.
    with database.get_db() as db:
        revision = db.execute("INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at) VALUES (?,?,'draft','2026-09-07T00:00:00Z')", (saved['id'], json.dumps(value))).lastrowid
        job['recording']['revision'] = revision
        scan = db.execute("INSERT INTO flow_catalog_scans(site_id,trigger_type,status,job_json,created_at,worker_id) VALUES (?,'recording_validate','running',?,'2026-09-07T00:00:00Z','test-worker')", (job['site']['id'], json.dumps({'validation_job': job, 'recording_operation': 'validate'}))).lastrowid
        db.execute("INSERT INTO flow_recording_sessions(scan_id,flow_id,operation,revision_id) VALUES (?,?,'validate',?)", (scan, saved['id'], revision))
    return saved, job, scan


def post(scan, status, detail, error=None):
    with database.get_db() as db:
        row = db.execute('SELECT * FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()
        return flow_recordings.update_operation(db, row, 'test-worker', flows.ScanProgress(status=status, progress=detail, error=error), '2026-09-07T00:00:01Z')


def test_debug_keeps_setting_probe_and_public_failure_from_frozen_definition(flow_db):
    saved, job, scan = queued_test()
    post(scan, 'running', {'stage': 'recorded_action', 'step_id': 'setting', 'message': 'Sending action.',
        'diagnostic': {'version': 1, 'phase': 'action_target', 'target': {'element_id': 'btnSetting', 'owner_id': 'menu', 'match_count': 1, 'visible_count': 1},
            'click': {'method': 'native', 'before': {'expanded': False, 'value': 'DO_NOT_EXPORT'}}},
        'step_outcomes': {'setting': {'outcome': 'running'}}})
    post(scan, 'running', {'stage': 'recorded_action', 'step_id': 'setting', 'message': 'Setting opened.',
        'diagnostic': {'version': 1, 'phase': 'action_finished', 'timing': {'default_seconds': 10},
            'click': {'confirmation': 'confirmed', 'after': {'expanded': True}}},
        'step_outcomes': {'setting': {'outcome': 'completed', 'confirmation': 'confirmed', 'message': 'Setting opened.'}}})
    post(scan, 'running', {'stage': 'recorded_action', 'step_id': 'public', 'message': 'Target unavailable.',
        'diagnostic': {'version': 1, 'phase': 'action_failed', 'prior_step_id': 'setting', 'target': {'match_count': 0},
            'error_type': 'TimeoutError', 'raw_html': 'DO_NOT_EXPORT'},
        'step_outcomes': {'public': {'outcome': 'failed', 'message': 'Locator.click: Timeout 120000ms exceeded.\nCall log:\nPrivateCustomerName'}}})
    post(scan, 'failed', {'stage': 'failed', 'message': 'Locator.click: Timeout 120000ms exceeded.'}, error='Locator.click: Timeout 120000ms exceeded.\nCall log:\nPrivateCustomerName')
    with database.get_db() as db:
        db.execute("UPDATE flow_recording_revisions SET definition_json=? WHERE id=?", (json.dumps({'steps': [{'id': 'wrong', 'label': 'NEW DRAFT LABEL'}]}), job['recording']['revision']))
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(f'/api/flows/{saved["id"]}/recordings/{scan}/debug')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/plain')
        assert response.headers['cache-control'] == 'no-store'
        text = response.text
        assert text.index('Click Setting') < text.index('Click Public')
        for expected in ['"element_id": "btnSetting"', '"method": "native"', '"before": {"expanded": false}', '"after": {"expanded": true}', '"match_count": 0', 'timed out after 120000 ms']:
            assert expected in text
        for forbidden in ['PrivateCustomerName', 'DO_NOT_EXPORT', 'NEW DRAFT LABEL', 'private.example', 'token=hidden', 'Call log:']:
            assert forbidden not in text
        assert client.get(f'/api/flows/{saved["id"] + 999}/recordings/{scan}/debug').status_code == 404
        assert client.get(f'/api/flows/{saved["id"]}/recordings/99999/debug').status_code == 404
    with database.get_db() as db:
        persisted = '\n'.join(row[0] for row in db.execute('SELECT details_json FROM flow_scan_events WHERE scan_id=?', (scan,)))
        assert 'DO_NOT_EXPORT' not in persisted


def test_diagnostic_allowlist_redacts_headers_paths_urls_and_entered_values():
    definition = {'steps': [{'action': 'fill', 'args': ['PrivateCustomerName']}]}
    samples = ['Authorization: Basic c2VjcmV0', 'Cookie: session=abcd; private=efgh',
        'https://private.example/report?access_token=secret', r'C:\Users\private\report.xlsx', r'\\share\private\file.xlsx',
        '/sensitive/reports/file.xlsx', 'PrivateCustomerName', 'name@example.internal']
    for sample in samples:
        result = diagnostics.sanitize_diagnostic({'phase': 'failed', 'action_label': sample,
            'target': {'recorded_locator': sample}, 'body': sample, 'cookies': sample}, definition)
        serialized = json.dumps(result)
        assert sample not in serialized
        assert 'body' not in result and 'cookies' not in result
    assert diagnostics.safe_error('Locator.fill: rejected value "PrivateCustomerName"', definition) == 'The recorded browser action failed.'
    assert diagnostics.safe_error('<html>Private report rows</html>') == 'Browser error details containing page content are excluded.'
    assert diagnostics.sanitize_diagnostic({'duration_ms': 10 ** 1000}) == {'version': 1}


def test_terminal_error_and_step_messages_are_concise_in_recording_list(flow_db):
    saved, _, scan = queued_test()
    raw = 'Locator.click: Timeout 120000ms exceeded.\nCall log:\n  waiting for PrivateCustomerName'
    post(scan, 'running', {'stage': 'recorded_action', 'message': raw,
        'step_outcomes': {'public': {'outcome': 'failed', 'message': raw}}})
    post(scan, 'failed', {'stage': 'failed', 'message': raw}, error=raw)
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(f'/api/flows/{saved["id"]}/recordings')
    assert response.status_code == 200
    session = next(item for item in response.json()['sessions'] if item['scan_id'] == scan)
    expected = 'Browser action timed out after 120000 ms.'
    assert session['error'] == expected
    progress = json.loads(session['progress_json'])
    assert progress['message'] == expected
    assert progress['step_outcomes']['public']['message'] == expected
    with database.get_db() as db:
        messages = '\n'.join(row[0] for row in db.execute('SELECT details_json FROM flow_scan_events WHERE scan_id=?', (scan,)))
    assert 'Call log:' not in messages and 'PrivateCustomerName' not in messages


def test_missing_error_and_cancelled_step_semantics_are_preserved(flow_db):
    _, _, scan = queued_test()
    post(scan, 'running', {'stage': 'recorded_action', 'message': None,
        'step_outcomes': {'setting': {'outcome': 'running', 'message': None}}})
    with database.get_db() as db:
        row = db.execute('SELECT error,progress_json FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()
    assert row['error'] is None
    assert json.loads(row['progress_json'])['message'] is None
    assert json.loads(row['progress_json'])['step_outcomes']['setting']['message'] is None
    post(scan, 'cancelled', {'stage': 'cancelled'})
    with database.get_db() as db:
        row = db.execute('SELECT error,progress_json FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()
    assert row['error'] is None
    assert json.loads(row['progress_json'])['step_outcomes']['setting'] == {'outcome': 'cancelled', 'message': 'Test cancelled.'}


def test_progress_returns_cancel_request_and_keeps_reservation_until_acknowledged(flow_db):
    _, _, scan = queued_test()
    with database.get_db() as db:
        db.execute("INSERT INTO flow_workers(worker_id,display_name,status,current_scan_id) VALUES ('test-worker','Test worker','scanning',?)", (scan,))
    assert post(scan, 'running', {'stage': 'recorded_action'})['cancel_requested'] is False
    with database.get_db() as db:
        job = json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()[0])
        job['cancel_requested'] = '2026-09-07T00:00:01Z'
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan))
    reply = post(scan, 'running', {'stage': 'recorded_action', 'step_outcomes': {'public': {'outcome': 'running'}}})
    assert reply['cancel_requested'] is True and reply['status'] == 'running'
    with database.get_db() as db:
        session = db.execute('SELECT status,finished_at FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()
        worker = db.execute("SELECT status,current_scan_id FROM flow_workers WHERE worker_id='test-worker'").fetchone()
    assert session['status'] == 'running' and session['finished_at'] is None
    assert worker['status'] == 'scanning' and worker['current_scan_id'] == scan
    reply = post(scan, 'cancelled', {'stage': 'cancelled'})
    assert reply['status'] == 'cancelled'
    with database.get_db() as db:
        worker = db.execute("SELECT status,current_scan_id FROM flow_workers WHERE worker_id='test-worker'").fetchone()
    assert worker['status'] == 'idle' and worker['current_scan_id'] is None


@pytest.mark.parametrize('stage', ['recorded_action', 'test_environment', 'authentication'])
def test_cancel_button_requests_validation_stop_before_force_close(flow_db, monkeypatch, stage):
    saved, _, scan = queued_test()
    post(scan, 'running', {'stage': stage, 'step_outcomes': {'public': {'outcome': 'running'}}})
    forced = []
    def force_stop(scan_id, request):
        forced.append(scan_id)
        return {'scan_id': scan_id, 'status': 'cancelled'}
    monkeypatch.setattr(flows, 'stop_scan', force_stop)
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app) as client:
        path = f'/api/flows/{saved["id"]}/recordings/{scan}/cancel'
        response = client.post(path)
        assert response.status_code == 200 and response.json()['status'] == 'cancelling'
        assert forced == []
        with database.get_db() as db:
            row = db.execute('SELECT status,job_json,progress_json FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()
            assert row['status'] == 'running'
            assert json.loads(row['progress_json'])['step_outcomes']['public']['outcome'] == 'running'
            job = json.loads(row['job_json'])
            assert job['cancel_requested']
        assert client.post(path).json()['status'] == 'cancelling'
        assert forced == []
        with database.get_db() as db:
            job['cancel_requested'] = (datetime.now(timezone.utc) - timedelta(seconds=11)).isoformat()
            db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan))
        assert client.post(path).json()['status'] == 'cancelled'
        assert forced == [scan]


@pytest.mark.parametrize('reason', ['legacy', 'worker_lost', 'startup'])
def test_debug_survives_missing_worker_or_legacy_events(flow_db, reason):
    saved, _, scan = queued_test()
    with database.get_db() as db:
        if reason == 'legacy':
            db.execute("UPDATE flow_catalog_scans SET status='failed',error='Original legacy error',job_json='{}' WHERE id=?", (scan,))
        elif reason == 'worker_lost':
            db.execute("UPDATE flow_catalog_scans SET heartbeat_at='2020-01-01T00:00:00Z' WHERE id=?", (scan,))
            flow_recordings.reap(db)
        else:
            db.execute("UPDATE flow_catalog_scans SET status='queued' WHERE id=?", (scan,))
            assert flow_recordings.fail_queued_operation(db, scan, 'The recording browser did not start.', datetime.now(timezone.utc))
        text = diagnostics.render_debug(db, saved['id'], scan)
        assert 'Status: failed' in text
        assert 'Original legacy error' in text if reason == 'legacy' else 'worker' in text or 'browser did not start' in text


def test_log_budget_preserves_failure_and_recent_details(flow_db, monkeypatch):
    saved, _, scan = queued_test()
    monkeypatch.setattr(diagnostics, 'MAX_TEXT', 7000)
    monkeypatch.setattr(diagnostics, 'MAX_EVENTS', 20)
    with database.get_db() as db:
        db.execute("UPDATE flow_catalog_scans SET status='failed',error='Public could not be selected.' WHERE id=?", (scan,))
        for index in range(35):
            db.execute("INSERT INTO flow_scan_events(scan_id,status,stage,message,details_json) VALUES (?,'running','recorded_action',?,?)", (scan,
                str(index) + ': ' + '漢字' * 500, json.dumps({'step_id': 'public', 'diagnostic': {'phase': 'action_target', 'target': {'match_count': index}}})))
        text = diagnostics.render_debug(db, saved['id'], scan)
    assert len(text.encode('utf-8')) <= 7000
    assert 'Failure: Public could not be selected.' in text
    assert '"match_count": 34' in text


@pytest.mark.parametrize('failure', ['authenticate', 'action', 'cancel'])
def test_worker_environment_survives_failure_and_trace_does_not_mask_it(flow_db, tmp_path, monkeypatch, failure):
    _, job = draft_job()
    monkeypatch.setattr(flow_portable, 'execution_hash', lambda: job['recording']['engine_hash'])
    monkeypatch.setattr(flow_worker, '_code_version', lambda: 'tested-worker')
    trace = SimpleNamespace(start=lambda **kwargs: None, stop=lambda **kwargs: (_ for _ in ()).throw(RuntimeError('trace stop failed')))
    page = SimpleNamespace(context=SimpleNamespace(browser=SimpleNamespace(version='browser-1'), tracing=trace))
    events = []
    def progress(status, detail):
        events.append(detail)
        if failure == 'cancel':
            raise flow_recorder_worker.RecordingCancelled('cancelled during progress')
    def authenticate(*args):
        if failure == 'authenticate':
            raise RuntimeError('original authentication failure')
    monkeypatch.setattr(flow_recorder_worker, 'authenticate', authenticate)
    monkeypatch.setattr(flow_recording_runtime, 'execute_recorded_flow', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('original action failure')))
    with pytest.raises(RuntimeError, match={'authenticate': 'original authentication', 'action': 'original action', 'cancel': 'cancelled during progress'}[failure]):
        flow_recorder_worker.validate({'id': 123, 'job': {'validation_job': job}}, page, tmp_path, progress)
    assert events[0]['diagnostic']['environment']['worker_version'] == 'tested-worker'
    assert events[0]['diagnostic']['environment']['browser_version'] == 'browser-1'


def test_technical_exception_preserves_cause_stack_and_redacts_browser_content():
    try:
        raise TimeoutError('Locator.click: Timeout 120000ms exceeded.\nCall log:\n'
            '  element is not enabled\n  <div>PrivateCustomerName</div> intercepts pointer events\n'
            '  Cookie: session=secret\n  https://private.example/report?token=secret')
    except TimeoutError as error:
        detail = diagnostics.sanitize_diagnostic({'exception': diagnostics.exception_detail(error)},
            {'steps': [{'action': 'fill', 'args': ['PrivateCustomerName']}]})
    exception = detail['exception']
    assert exception['browser_api'] == 'Locator.click'
    assert exception['timeout_ms'] == 120000
    assert {'timeout', 'not_enabled', 'pointer_intercepted'} <= set(exception['signals'])
    assert exception['stack'][-1]['file'] == 'test_recording_diagnostics.py'
    assert exception['stack'][-1]['line'] > 0
    assert 'PrivateCustomerName' not in json.dumps(detail) and 'private.example' not in json.dumps(detail)
    assert 'session=secret' not in json.dumps(detail)


def test_debug_always_includes_frozen_locator_and_rich_failure_evidence(flow_db):
    saved, job, scan = queued_test()
    step = next(s for s in job['recording']['definition']['steps'] if s['id'] == 'public')
    post(scan, 'running', {'stage': 'recorded_action', 'step_id': 'public',
        'diagnostic': {'phase': 'action_failed', 'call': diagnostics.execution_contract(step),
            'target': {'match_count': 1, 'candidates': [{'tag': 'button', 'aria_selected': None, 'hit_id': 'overlay', 'hit_is_target': False}]},
            'exception': {'type': 'TimeoutError', 'browser_api': 'Locator.click', 'signals': ['pointer_intercepted'],
                'stack': [{'file': 'flow_recording_runtime.py', 'function': 'execute', 'line': 250}]}},
        'step_outcomes': {'public': {'outcome': 'failed'}}})
    with database.get_db() as db:
        text = diagnostics.render_debug(db, saved['id'], scan)
    for expected in ['technical-v2', 'get_by_text', 'Public', 'pointer_intercepted',
                     '"aria_selected": null', '"hit_id": "overlay"', 'flow_recording_runtime.py',
                     '"post_click_verification": "none"', '"timeout_ms": 120000']:
        assert expected in text
    assert 'PrivateCustomerName' not in text


def test_historical_public_rule_explains_what_was_checked(flow_db):
    saved, _, scan = queued_test()
    post(scan, 'running', {'stage': 'recorded_action', 'step_id': 'public',
        'diagnostic': {'click': {'transition': 'public_selected', 'confirmation': 'transition_missing',
            'after': {'selected': False, 'open': True}}},
        'step_outcomes': {'public': {'outcome': 'failed'}}})
    with database.get_db() as db:
        text = diagnostics.render_debug(db, saved['id'], scan)
    assert 'Legacy selection rule:' in text
    assert 'aria-selected=true OR aria-pressed=true' in text
    assert 'getSelectStatus()=true OR get_selected()=true' in text
    assert 'individual signals are unavailable' in text
