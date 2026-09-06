import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database, flow_paths, flow_portable, flow_recordings, flow_recording_timing as timing
from app.routers import flow_recordings as routes, flows, system_flows
from test_flow_recordings import draft_job
from test_flows import flow_db, _request


def test_shared_wait_defaults_positive_validation_and_partial_save(flow_db, monkeypatch):
    app = FastAPI()
    app.include_router(system_flows.router)
    app.include_router(routes.router)
    monkeypatch.setattr(system_flows, 'require_app_access', lambda request: None)
    saved, _ = draft_job()
    with TestClient(app) as client:
        before = client.get('/api/system/flows').json()
        assert before['recording_wait_seconds'] == 10
        for invalid in [0, -1, 601, True, '20', 1.5]:
            assert client.put('/api/system/flows', json={'recording_wait_seconds': invalid}).status_code == 422
        assert client.put('/api/system/flows', json={}).status_code == 422
        updated = client.put('/api/system/flows', json={'recording_wait_seconds': 25})
        assert updated.status_code == 200
        assert updated.json()['recording_wait_seconds'] == 25
        assert updated.json()['headless_capacity'] == before['headless_capacity']
        assert client.get(f'/api/flows/{saved["id"]}/recordings').json()['recording_wait_seconds'] == 25
        assert client.put('/api/system/flows', json={'headless_capacity': 3}).json()['recording_wait_seconds'] == 25


@pytest.mark.parametrize('value', [0, -1, 601, True, '10', None, 2.5])
def test_job_wait_is_validated_without_coercion(value):
    with pytest.raises(ValueError, match='1 to 600'):
        timing.for_job({'execution': {'recording_wait_seconds': value}})


def test_legacy_job_and_invalid_persisted_setting_use_default(flow_db):
    assert timing.for_job({}) == 10
    with database.get_db() as db:
        for invalid in ['0', '-2', 'wrong', '1.5', '601']:
            flow_paths.save_setting(db, timing.SETTING, invalid)
            assert timing.configured(db) == 10


def activate_test_revision(saved, job):
    revision = routes.save_revision(saved['id'], routes.RevisionWrite(definition=job['recording']['definition']))['revision_id']
    with database.get_db() as db:
        scan = flow_recordings.queue_operation(db, saved['id'], 'validate', 'test', revision_id=revision)
        row = db.execute('SELECT * FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()
        frozen = json.loads(row['job_json'])
        from app.flow_tasks import task_matrix
        result = {'configuration_hash': frozen['configuration_hash'], 'engine_hash': frozen['validation_job']['recording']['engine_hash'],
            'outputs': [{'step_id': task['export_view'], 'period_key': task['period'], 'checksum': 'a' * 64}
                for task in task_matrix(frozen['validation_job'])]}
        flow_recordings.update_operation(db, row, 'test-worker', flows.ScanProgress(status='succeeded', recording_result=result), '2026-09-07T00:00:00Z')
        db.execute('UPDATE flows SET recording_revision_id=? WHERE id=?', (revision, saved['id']))
    return scan, revision, frozen


def test_setting_freezes_new_tests_and_runs_without_invalidating_revision(flow_db, monkeypatch):
    monkeypatch.setattr(flow_portable, 'execution_hash', lambda: 'e' * 64)
    saved, job = draft_job()
    scan, revision, frozen = activate_test_revision(saved, job)
    assert frozen['validation_job']['execution']['recording_wait_seconds'] == 10
    with database.get_db() as db:
        before = flows._build_job(db, saved['id'])
        flow_paths.save_setting(db, timing.SETTING, 25)
        after = flows._build_job(db, saved['id'])
        assert after['execution']['recording_wait_seconds'] == 25
        assert before['recording'] == after['recording']
        assert flow_recordings.config_hash(before) == flow_recordings.config_hash(after)
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?', (revision,)).fetchone()[0] == 'validated'
        assert json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?', (scan,)).fetchone()[0])['validation_job']['execution']['recording_wait_seconds'] == 10
        changed = copy.deepcopy(after)
        changed['downloads']['filename_template'] = 'different.xlsx'
        assert flow_recordings.config_hash(before) != flow_recordings.config_hash(changed)


def test_resume_keeps_original_timing_after_global_preference_changes(flow_db, monkeypatch):
    monkeypatch.setattr(flow_portable, 'execution_hash', lambda: 'e' * 64)
    saved, job = draft_job()
    activate_test_revision(saved, job)
    with database.get_db() as db:
        original = flows._build_job(db, saved['id'])
        run_id = db.execute('''INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,artifact_json,created_at)
            VALUES (?,'manual','failed',?,?,'2026-09-07T00:00:00Z')''', (saved['id'], json.dumps(original),
                json.dumps([{'recording_defaults': {'end': '2026-09-05'}}]))).lastrowid
        flow_paths.save_setting(db, timing.SETTING, 40)
        result = flows.inspect_resume_eligibility(db, run_id)
        assert result['status'] == 'eligible', result
        assert result['_job']['execution']['recording_wait_seconds'] == 10
