import json
from datetime import datetime, timezone
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
        flow_recordings.update_operation(db, row, 'test-worker', flows.ScanProgress(status=status, progress=detail, error=error), '2026-09-07T00:00:01Z')


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
