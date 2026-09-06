"""Recording startup failures use saved drafts and never consume busy time."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import database, flow_capacity, flow_local_runner, flow_paths, flow_recordings
from app.routers import flow_recordings as routes, flows
from test_flow_recordings import definition, draft_job
from test_flows import flow_db, _request


@pytest.fixture
def startup(flow_db, monkeypatch):
    launches = []
    monkeypatch.setattr(flow_local_runner, 'launch_local_worker',
                        lambda mode, *, slot: launches.append(slot) or {'status': 'starting'})
    saved, job = draft_job()
    revision = routes.save_revision(saved['id'], routes.RevisionWrite(definition=definition()))['revision_id']
    response = routes.validate_revision(saved['id'], revision, _request())
    return saved, job, revision, response['scan_id'], launches


def session(scan_id):
    with database.get_db() as db:
        return dict(db.execute('SELECT * FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone())


def age_start(scan_id):
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with database.get_db() as db:
        db.execute('UPDATE flow_catalog_scans SET created_at=?,progress_json=? WHERE id=?',
                   (old, json.dumps({'stage': 'starting_worker', 'startup_started_at': old}), scan_id))


def register(identity):
    flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity, capabilities={
        'headed': True, 'process_id': 123, 'browser_switch_v1': True,
        'flow_recorder_v1': True, 'flow_recorder_controls_v1': True,
        'recorded_flows_v2': True, 'recorded_validation_engine_v1': True}))


def test_task_start_success_without_worker_fails_and_retry_preserves_actions(startup):
    saved, _, revision, scan_id, launches = startup
    assert json.loads(session(scan_id)['progress_json'])['message'] == 'Waiting for worker…'
    with database.get_db() as db:
        original = db.execute('SELECT definition_json FROM flow_recording_revisions WHERE id=?', (revision,)).fetchone()[0]
    age_start(scan_id)
    failed = routes.list_recordings(saved['id'])['sessions'][0]
    assert failed['status'] == 'failed'
    assert 'did not start' in failed['error'] and 'Try again' in failed['error']
    with database.get_db() as db:
        row = db.execute('SELECT * FROM flow_recording_revisions WHERE id=?', (revision,)).fetchone()
        assert row['status'] == 'draft' and row['definition_json'] == original
        flow_recordings.assert_flow_idle(db, saved['id'])
    retried = routes.validate_revision(saved['id'], revision, _request())
    assert launches == [1, 1]  # The failed request no longer reserves its slot.
    identity = flow_capacity.worker_id(1, 'headed')
    register(identity)
    assert flows.claim_run(identity)['scan']['id'] == retried['scan_id']
    assert session(scan_id)['status'] == 'failed'


@pytest.mark.parametrize('reason', ['worker', 'headed_capacity', 'global_capacity', 'portal_capacity'])
def test_busy_time_does_not_expire_startup_and_fresh_allowance_follows(startup, reason):
    saved, job, _, scan_id, _ = startup
    other = routes.create_draft(routes.RecordingDraft(name='Other work', site_id=job['site']['id']), _request())
    identity = flow_capacity.worker_id(1, 'headed')
    blocker_identity = identity if reason == 'worker' else 'other-worker'
    blocker_mode = 'headless' if reason == 'global_capacity' else 'headed'
    with database.get_db() as db:
        flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, '2')
        if reason == 'headed_capacity':
            flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, '1')
        if reason == 'global_capacity':
            flow_paths.save_setting(db, flow_capacity.TOTAL_CAPACITY_KEY, '1')
        if reason == 'portal_capacity':
            flow_paths.save_setting(db, f"flows_portal_capacity:{job['site']['id']}", '1')
        blocker = db.execute("""INSERT INTO flow_runs(flow_id,trigger_type,status,worker_id,job_json,created_at)
            VALUES (?,'manual','running',?,?,?)""", (other['id'], blocker_identity,
            json.dumps({'execution': {'browser_mode': blocker_mode}, 'site': job['site']}),
            datetime.now(timezone.utc).isoformat())).lastrowid
    age_start(scan_id)
    waiting = routes.list_recordings(saved['id'])['sessions'][0]
    assert waiting['status'] == 'queued'
    progress = json.loads(waiting['progress_json'])
    assert progress['stage'] == 'waiting_for_capacity'
    expected = {'worker': 'current work', 'headed_capacity': 'browser slot',
                'global_capacity': 'browser slot', 'portal_capacity': 'this website'}[reason]
    assert expected in progress['message']
    assert 'startup_started_at' not in progress
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status='succeeded' WHERE id=?", (blocker,))
    ready = routes.list_recordings(saved['id'])['sessions'][0]
    assert ready['status'] == 'queued'
    started = datetime.fromisoformat(json.loads(ready['progress_json'])['startup_started_at'])
    assert (datetime.now(timezone.utc) - started).total_seconds() < 10
    age_start(scan_id)
    assert routes.list_recordings(saved['id'])['sessions'][0]['status'] == 'failed'


def test_earlier_recording_reservation_wait_is_not_a_startup_failure(startup):
    saved, job, _, first, _ = startup
    other = routes.create_draft(routes.RecordingDraft(name='Next recording', site_id=job['site']['id']), _request())
    with database.get_db() as db:
        flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, '1')
    second = routes.start_recording(other['id'], _request())['scan_id']
    age_start(second)
    waiting = routes.list_recordings(other['id'])['sessions'][0]
    assert waiting['status'] == 'queued'
    assert 'earlier recording' in json.loads(waiting['progress_json'])['message']
    with database.get_db() as db:
        flow_recordings.fail_queued_operation(db, first, 'Synthetic startup failure', datetime.now(timezone.utc))
    assert routes.list_recordings(other['id'])['sessions'][0]['status'] == 'queued'
    assert json.loads(session(second)['progress_json'])['stage'] == 'starting_worker'


@pytest.mark.parametrize('launch_status', ['error', 'skipped'])
def test_unsupported_or_rejected_launch_restores_draft(flow_db, monkeypatch, launch_status):
    monkeypatch.setattr(flow_local_runner, 'launch_local_worker',
                        lambda *args, **kwargs: {'status': launch_status, 'message': 'Visible worker unavailable'})
    saved, _ = draft_job()
    revision = routes.save_revision(saved['id'], routes.RevisionWrite(definition=definition()))['revision_id']
    response = routes.validate_revision(saved['id'], revision, _request())
    assert session(response['scan_id'])['status'] == 'failed'
    assert session(response['scan_id'])['error'] == 'Visible worker unavailable'
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?', (revision,)).fetchone()[0] == 'draft'
        flow_recordings.assert_flow_idle(db, saved['id'])


def test_late_launch_error_cannot_fail_claimed_validation(flow_db, monkeypatch):
    saved, _ = draft_job()
    revision = routes.save_revision(saved['id'], routes.RevisionWrite(definition=definition()))['revision_id']
    identity = flow_capacity.worker_id(1, 'headed')
    register(identity)
    def launch(*args, **kwargs):
        assert flows.claim_run(identity)['scan']
        return {'status': 'error', 'message': 'Late task-start error'}
    monkeypatch.setattr(flow_local_runner, 'launch_local_worker', launch)
    response = routes.validate_revision(saved['id'], revision, _request())
    assert response['worker']['status'] == 'already_assigned'
    assert session(response['scan_id'])['status'] == 'claimed'
    with database.get_db() as db:
        assert not flow_recordings.fail_queued_operation(db, response['scan_id'], 'Timeout', datetime.now(timezone.utc))
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?', (revision,)).fetchone()[0] == 'validating'


def test_queued_progress_cannot_overwrite_a_claimed_scan(startup):
    _, _, _, scan_id, _ = startup
    age_start(scan_id)
    stale = session(scan_id)
    identity = flow_capacity.worker_id(1, 'headed')
    register(identity)
    assert flows.claim_run(identity)['scan']['id'] == scan_id
    with database.get_db() as db:
        db.execute('UPDATE flow_catalog_scans SET progress_json=? WHERE id=?',
                   (json.dumps({'stage': 'browser_launch', 'message': 'Opening browser'}), scan_id))
        flow_recordings.refresh_queued_operation(db, stale, datetime.now(timezone.utc))
    assert session(scan_id)['status'] == 'claimed'
    assert json.loads(session(scan_id)['progress_json'])['stage'] == 'browser_launch'


def test_claimed_operation_uses_heartbeat_not_old_startup_timer(startup):
    saved, _, _, scan_id, _ = startup
    age_start(scan_id)
    identity = flow_capacity.worker_id(1, 'headed')
    register(identity)
    assert flows.claim_run(identity)['scan']['id'] == scan_id
    assert routes.list_recordings(saved['id'])['sessions'][0]['status'] == 'claimed'


@pytest.mark.parametrize('capable', [True, False])
def test_fresh_registration_cannot_keep_an_unclaimed_request_alive(startup, capable):
    saved, _, _, scan_id, _ = startup
    identity = flow_capacity.worker_id(1, 'headed')
    if capable:
        register(identity)
    else:
        flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity,
            capabilities={'headed': True, 'process_id': 123}))
        assert flows.claim_run(identity)['scan'] is None
    age_start(scan_id)
    assert routes.list_recordings(saved['id'])['sessions'][0]['status'] == 'failed'


def test_disabling_reserved_slot_fails_and_retry_selects_enabled_slot(startup):
    saved, _, revision, scan_id, launches = startup
    with database.get_db() as db:
        job = json.loads(session(scan_id)['job_json'])
        job['execution']['worker_id'] = flow_capacity.worker_id(2, 'headed')
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan_id))
        flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, '1')
    result = routes.list_recordings(saved['id'])
    assert result['sessions'][0]['status'] == 'failed'
    assert 'no longer enabled' in result['sessions'][0]['error']
    assert result['revisions'][0]['status'] == 'draft'
    retried = routes.validate_revision(saved['id'], revision, _request())
    assert launches == [1, 1]
    assert json.loads(session(retried['scan_id'])['job_json'])['execution']['worker_id'] == flow_capacity.worker_id(1, 'headed')
