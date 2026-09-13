"""Durable grouping and atomic manual batches with synthetic flows/workers."""
import json
import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import database, flow_paths
from app.routers import flow_groups as groups, flows
from test_flows import flow_db, _seed_catalog, _mark_discovered, _flow, _request


@pytest.fixture
def grouped_flows(flow_db, monkeypatch):
    launches = []
    # check.ps1 supplies one run root; each disposable database needs its own
    # managed folders because flow IDs and names restart in every test.
    with database.get_db() as db:
        root = Path(flow_paths.get_flows_root(db)) / ('g-' + hashlib.sha256(str(flow_db).encode()).hexdigest()[:8])
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('flows_root',?)", (str(root),))
    monkeypatch.setattr(flows, 'launch_local_worker', lambda mode: launches.append(mode) or {'status': 'launched'})
    site, report = _seed_catalog()
    _mark_discovered(report['id'])
    saved = [flows.create_flow(_flow(site['id'], report['id'], name=name, target_folder=None), _request())
             for name in ['M Tracker Country', 'M Tracker Subs', 'Photo', 'Wi-Fi', 'TI']]
    return saved, launches


def create(saved, name='M Tracker', **extra):
    return groups.create_group(groups.GroupWrite(name=name, module='Web', flow_ids=[flow['id'] for flow in saved], **extra), _request())


def run(group):
    return groups.run_group(group['id'], groups.GroupRun(flow_ids=group['flow_ids']), _request())


def raw_flows():
    with database.get_db() as db:
        return [dict(row) for row in db.execute('SELECT * FROM flows ORDER BY id')]


def test_membership_persists_without_modifying_flow_configuration(grouped_flows):
    saved, _ = grouped_flows
    before = raw_flows()
    group = create(saved[:2])
    database.init_db()
    assert groups.list_groups() == [group]
    changed = groups.update_group(group['id'], groups.GroupWrite(**{**group, 'name': 'Tracker', 'flow_ids': [flow['id'] for flow in saved[2:4]]}), _request())
    assert changed['id'] == group['id'] and changed['flow_ids'] == [saved[2]['id'], saved[3]['id']]
    assert changed['version'] > group['version']
    assert raw_flows() == before
    groups.ungroup(changed['id'], _request(), changed['version'])
    assert groups.list_groups() == [] and raw_flows() == before


def test_moves_are_unique_stale_edits_fail_and_empty_groups_disappear(grouped_flows):
    saved, _ = grouped_flows
    first = create(saved[:2])
    second = create(saved[2:4], 'Other')
    moved = groups.update_group(second['id'], groups.GroupWrite(**{**second, 'flow_ids': [flow['id'] for flow in saved[:4]]}), _request())
    assert groups.list_groups() == [moved]
    with pytest.raises(HTTPException) as error:
        groups.update_group(second['id'], groups.GroupWrite(**second), _request())
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        groups.ungroup(moved['id'], _request(), second['version'])
    assert error.value.status_code == 409
    assert all(group['id'] != first['id'] for group in groups.list_groups())


@pytest.mark.parametrize('changes,status', [
    ({'flow_ids': [999999]}, 422),
    ({'flow_ids': [999999, 999998]}, 409),
    ({'module': 'ASAP'}, 422),
    ({'classification': 'draft'}, 422),
    ({'name': ' m TRACKER '}, 409),
    ({'flow_ids': [True, 2]}, 422),
    ({'name': '   '}, 422),
])
def test_group_validation_is_atomic_and_http_routes_are_reachable(grouped_flows, changes, status):
    saved, _ = grouped_flows
    original = create(saved[:2])
    app = FastAPI(); app.include_router(flows.router)
    with TestClient(app) as client:
        response = client.post('/api/flows/groups', json={'name': 'Other', 'module': 'Web', 'flow_ids': [flow['id'] for flow in saved[2:4]], **changes})
        assert response.status_code == status, response.text
        assert client.get('/api/flows/groups').json() == [original]


def test_classification_change_and_flow_delete_detach_members(grouped_flows):
    saved, _ = grouped_flows
    group = create(saved[:2])
    flows.patch_flow(saved[0]['id'], flows.FlowInlineWrite(classification='draft'), _request())
    current = groups.list_groups()[0]
    assert current['flow_ids'] == [saved[1]['id']] and current['version'] > group['version']
    with database.get_db() as db:
        db.execute('DELETE FROM flows WHERE id=?', (saved[1]['id'],))
    assert groups.list_groups() == []


def test_batch_queues_both_before_launch_and_workers_can_claim_concurrently(grouped_flows, monkeypatch):
    saved, _ = grouped_flows
    group = create(saved[:2])
    observed = []
    def launch(mode):
        with database.get_db() as db:
            observed.append((mode, db.execute('SELECT COUNT(*) FROM flow_runs').fetchone()[0]))
        return {'status': 'launched'}
    monkeypatch.setattr(flows, 'launch_local_worker', launch)
    result = run(group)
    assert result['status'] == 'queued' and len(result['runs']) == 2
    assert observed == [('headless', 2)]
    with database.get_db() as db:
        queued = db.execute('SELECT * FROM flow_runs ORDER BY id').fetchall()
        assert queued[0]['created_at'] == queued[1]['created_at']
        for row in queued:
            assert row['trigger_type'] == 'manual' and row['requested_by'] == 'Analyst'
            job = json.loads(row['job_json'])
            expected = flows._build_job(db, row['flow_id'])
            for key in ('downloads', 'sql_handoff', 'transformation', 'execution'):
                assert job[key] == expected[key]
    for identity in ('synthetic-1', 'synthetic-2'):
        flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity,
            capabilities={'adapters': ['web_export'], 'shared_flow_artifacts': True, 'headed': False}))
    claimed = [flows.claim_run(identity)['run'] for identity in ('synthetic-1', 'synthetic-2')]
    assert {item['flow_id'] for item in claimed} == set(group['flow_ids'])


def test_second_member_validation_failure_rolls_back_batch_and_audit(grouped_flows, monkeypatch):
    saved, launches = grouped_flows
    group = create(saved[:2])
    original = flows._build_job
    def build(db, flow_id, **kwargs):
        if flow_id == saved[1]['id']:
            raise HTTPException(409, 'The saved report needs attention.')
        return original(db, flow_id, **kwargs)
    monkeypatch.setattr(flows, '_build_job', build)
    with pytest.raises(HTTPException, match='M Tracker Subs.*No flows queued'):
        run(group)
    with database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM flow_runs').fetchone()[0] == 0
    assert launches == []
    monkeypatch.setattr(flows, '_build_job', original)
    assert len(run(group)['runs']) == 2


def test_conflicting_shared_output_is_rejected_for_whole_group(grouped_flows):
    saved, launches = grouped_flows
    group = create(saved[:2])
    shared = saved[0]['target_folder']
    with database.get_db() as db:
        db.execute("UPDATE flows SET flow_folder=NULL,folder_state='legacy',output_mode='direct_replace',target_folder=? WHERE id IN (?,?)", (shared, *group['flow_ids']))
    with pytest.raises(HTTPException, match='No flows queued'):
        run(group)
    with database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM flow_runs').fetchone()[0] == 0
    assert not launches


def test_duplicate_batch_submissions_and_active_member_never_duplicate_runs(grouped_flows):
    saved, launches = grouped_flows
    group = create(saved[:2])
    def attempt():
        try:
            return run(group)['status']
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(map(str, results)) == ['409', 'queued']
    assert launches == ['headless']
    with database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM flow_runs').fetchone()[0] == 2
    retried = flows.queue_run(saved[0]['id'], _request())
    assert retried['resumed'] is True


def test_stale_membership_does_not_run_unseen_members(grouped_flows):
    saved, launches = grouped_flows
    group = create(saved[:2])
    groups.update_group(group['id'], groups.GroupWrite(**{**group, 'flow_ids': [flow['id'] for flow in saved[:3]]}), _request())
    with pytest.raises(HTTPException, match='membership changed'):
        run(group)
    assert launches == []


@pytest.mark.parametrize('throws', [False, True])
def test_worker_start_failure_keeps_durable_queue_for_start_now(grouped_flows, monkeypatch, throws):
    saved, _ = grouped_flows
    group = create(saved[:2])
    def fail(_mode):
        if throws:
            raise OSError('Synthetic worker failure')
        return {'status': 'error', 'message': 'Synthetic worker failure'}
    monkeypatch.setattr(flows, 'launch_local_worker', fail)
    result = run(group)
    assert 'startup needs attention' in result['message']
    with database.get_db() as db:
        rows = db.execute('SELECT status,progress_json FROM flow_runs').fetchall()
        assert len(rows) == 2
        assert all(row['status'] == 'queued' and 'waiting_for_bi_desktop' in row['progress_json'] for row in rows)
    monkeypatch.setattr(flows, 'launch_local_worker', lambda _mode: {'status': 'launched'})
    assert flows.queue_run(saved[0]['id'], _request())['resumed'] is True


def test_local_group_forces_fresh_reads_without_altering_schedules(flow_db, monkeypatch):
    monkeypatch.setattr(flows, 'launch_local_worker', lambda _mode: {'status': 'launched'})
    with database.get_db() as db:
        source = Path(flow_paths.get_flows_root(db)) / 'Sources' / 'synthetic-group.csv'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('name,value\nExample,1\n', encoding='utf-8')
    saved = [flows.create_flow(flows.FlowWrite(name=f'Local {index}', source_type='file', local_file_path=str(source)), _request()) for index in range(2)]
    group = groups.create_group(groups.GroupWrite(name='Local inputs', module='Local', flow_ids=[flow['id'] for flow in saved]), _request())
    before = raw_flows()
    run(group)
    with database.get_db() as db:
        assert all(json.loads(row[0])['local_file']['force_reprocess'] for row in db.execute('SELECT job_json FROM flow_runs'))
    assert before == raw_flows()


def test_existing_frontend_contracts():
    root = Path(__file__).resolve().parents[1]
    for name in ('test_flow_groups.mjs', 'test_flow_sorting.mjs', 'test_flow_activity_polling.mjs'):
        subprocess.run(['node', str(root / 'tests' / name)], cwd=root, check=True, capture_output=True, text=True)
