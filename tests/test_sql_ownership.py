"""SQL owner identity propagation, worker fencing and portable compatibility."""
import copy
import json

import pytest

from app import database, flow_sql, flow_recordings, flow_portable
from app.routers import flows
from test_flows import flow_db, _request
from test_view_refresh import _sql_flow, SERVER


def person(username='maya_reports'):
    with database.get_db() as db:
        return db.execute("INSERT INTO people(name,role,sql_username) VALUES ('Maya','BI',?)", (username,)).lastrowid


def test_current_identity_is_frozen_per_job_and_can_be_cleared(flow_db, monkeypatch):
    monkeypatch.setattr(flows, 'UPLOAD_PGHOST', SERVER)
    identity = person()
    saved = _sql_flow(owner_person_id=identity)
    with database.get_db() as db:
        original = flows._build_job(db, saved['id'])
        db.execute('UPDATE people SET sql_username=? WHERE id=?', ('Maya "BI" :owner', identity))
        changed = flows._build_job(db, saved['id'])
        assert original['sql_handoff']['owner_username'] == 'maya_reports'
        assert changed['sql_handoff']['owner_username'] == 'Maya "BI" :owner'
        db.execute('UPDATE people SET sql_username=NULL WHERE id=?', (identity,))
        assert 'owner_username' not in flows._build_job(db, saved['id'])['sql_handoff']
        db.execute('UPDATE people SET sql_username=? WHERE id=?', ('maya_reports', identity))
        db.execute('UPDATE flows SET sql_handoff_enabled=0 WHERE id=?', (saved['id'],))
        assert 'owner_username' not in flows._build_job(db, saved['id'])['sql_handoff']


def test_worker_claim_rejects_old_worker_and_preserves_queued_owner(flow_db, monkeypatch):
    monkeypatch.setattr(flows, 'UPLOAD_PGHOST', SERVER)
    monkeypatch.setattr(flows, 'launch_local_worker', lambda *a, **k: {'status': 'launched'})
    identity = person()
    saved = _sql_flow(owner_person_id=identity)
    queued = flows.queue_run(saved['id'], _request())
    with database.get_db() as db:
        db.execute('UPDATE people SET sql_username=? WHERE id=?', ('new_owner', identity))
    for worker, capability in [('old', False), ('new', True)]:
        flows.register_worker(flows.WorkerRegister(worker_id=worker, display_name=worker,
            capabilities={'shared_flow_artifacts': True, flow_sql.OWNERSHIP_CAPABILITY: capability}))
        claimed = flows.claim_run(worker)['run']
        if capability:
            assert claimed['id'] == queued['id']
            assert claimed['job']['sql_handoff']['owner_username'] == 'maya_reports'
        else:
            assert claimed is None


@pytest.mark.parametrize('nested', [False, True])
def test_capability_contract_for_runs_and_recording_validation(nested):
    job = {'sql_handoff': {'enabled': True, 'owner_username': 'maya'}}
    candidate = {'validation_job': job} if nested else job
    assert not flow_sql.ownership_supported(candidate, {})
    assert flow_sql.ownership_supported(candidate, {flow_sql.OWNERSHIP_CAPABILITY: True})
    job['sql_handoff']['enabled'] = False
    assert flow_sql.ownership_supported(candidate, {})
    job['sql_handoff'] = {'enabled': True}
    assert flow_sql.ownership_supported(candidate, {})


def test_recording_validation_claim_requires_ownership_capability(flow_db):
    from test_flow_recordings import draft_job
    from app.routers import flow_recordings as routes
    saved, job = draft_job()
    revision = routes.save_revision(saved['id'], routes.RevisionWrite(definition=job['recording']['definition']))['revision_id']
    with database.get_db() as db:
        scan_id = flow_recordings.queue_operation(db, saved['id'], 'validate', 'test', revision_id=revision)
        queued = json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone()[0])
        queued['validation_job']['sql_handoff'].update(enabled=True, owner_username='maya')
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(queued), scan_id))
    for capable in [False, True]:
        flows.register_worker(flows.WorkerRegister(worker_id='recorder', display_name='Recorder', capabilities={
            'headed': True, 'browser_switch_v1': True, 'flow_recorder_v1': True, 'recorded_flows_v2': True, 'recorded_flows_v3': True,
            'recorded_validation_engine_v1': True, 'flow_recorder_controls_v1': True,
            flow_sql.OWNERSHIP_CAPABILITY: capable}))
        claimed = flows.claim_run('recorder')['scan']
        assert claimed['id'] == scan_id if capable else claimed is None


def test_owner_change_does_not_invalidate_browser_recording_but_changes_portable(flow_db):
    from test_flow_recordings import draft_job
    _, job = draft_job()
    changed = copy.deepcopy(job)
    changed['sql_handoff']['owner_username'] = 'maya'
    assert flow_recordings.config_hash(changed) == flow_recordings.config_hash(job)
    before, after = flow_portable.source(job), flow_portable.source(changed)
    assert before != after
    assert '_ownership_preflight' in after and '_apply_ownership' in after


def test_pending_recording_settings_use_selected_owner_not_old_join(flow_db, monkeypatch):
    from test_flow_recordings import draft_job
    # The public pending-settings path is tested with the same model used by
    # recording validation, so it cannot reuse the saved owner's joined fields.
    saved, job = draft_job()
    old, new = person('old_sql'), person('new_sql')
    with database.get_db() as db:
        db.execute('UPDATE flows SET owner_person_id=? WHERE id=?', (old, saved['id']))
    body = flows.FlowWrite(name=saved['name'], execution_method='recorded',
        site_id=saved['site_id'], report_id=saved['report_id'], owner_person_id=new,
        sql_handoff_enabled=True, sql_mode='replace', sql_database='analytics',
        sql_schema='public', sql_table='flow_table', filename_template='report.csv')
    monkeypatch.setattr(flows, '_validate_sql_target', lambda *a, **k: None)
    with database.get_db() as db:
        pending = flows._build_job(db, saved['id'], recording_draft=True, pending_settings=body)
    assert pending['sql_handoff']['owner_username'] == 'new_sql'


@pytest.mark.parametrize('value', [1, ['maya'], 'bad\x00role', 'bad\nrole', 'x' * 64])
def test_invalid_owner_is_rejected_before_connecting(value, monkeypatch):
    monkeypatch.setattr(flow_sql, '_engine', lambda *a: pytest.fail('invalid role reached SQL'))
    with pytest.raises(ValueError):
        flow_sql.load_artifacts([], {'database': 'db', 'schema': 'public', 'table': 't', 'owner_username': value})


def test_confirmed_owner_is_saved_in_run_outcome(flow_db, monkeypatch):
    monkeypatch.setattr(flows, 'UPLOAD_PGHOST', SERVER)
    monkeypatch.setattr(flows, 'launch_local_worker', lambda *a, **k: {'status': 'launched'})
    saved = _sql_flow(owner_person_id=person())
    queued = flows.queue_run(saved['id'], _request())
    progress = flows.WorkerProgress(status='running', progress={'stage': 'sql_insertion_complete',
        'owner_username': 'maya_reports', 'previous_owner': 'loader', 'owner_changed': True,
        'target': 'analytics.public.flow_table', 'rows_written': 2})
    with database.get_db() as db:
        flows._record_sql_outcome(db, queued['id'], progress, '2026-09-09T00:00:00+00:00')
        outcome = json.loads(db.execute('SELECT sql_outcome_json FROM flow_runs WHERE id=?', (queued['id'],)).fetchone()[0])
    assert outcome['committed'] and outcome['owner_username'] == 'maya_reports'
