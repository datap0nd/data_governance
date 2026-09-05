import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database, flow_capacity as pool, flow_paths, flow_local_runner
from app.routers import flows, pipelines, system_flows
from test_flows import flow_db, _seed_catalog, _mark_discovered, _flow, _request
from tools.flow_pool_config import capacity as installer_capacity


@pytest.fixture(autouse=True)
def no_service_launch(monkeypatch):
    monkeypatch.setattr(flows, 'launch_local_worker', lambda *a, **k: {'status': 'test'})


def configure(value, mode='headless'):
    with database.get_db() as db:
        flow_paths.save_setting(db, pool.HEADED_CAPACITY_KEY if mode == 'headed' else pool.CAPACITY_KEY, value)


def worker(identity, *, headed=False):
    flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity,
        capabilities={'headed': headed, 'adapters': ['web_export'], 'shared_flow_artifacts': True}))


def queue_jobs(count, *, mode='headless', catalog=None):
    site, report = catalog or _seed_catalog()
    _mark_discovered(report['id'])
    runs = []
    for index in range(count):
        saved = flows.create_flow(_flow(site['id'], report['id'], name=f'Capacity {mode} {index}', browser_mode=mode), _request())
        runs.append(flows.queue_run(saved['id'], _request())['id'])
    return runs


@pytest.mark.parametrize('capacity', [1, 3, 5])
@pytest.mark.parametrize('mode', ['headless', 'headed'])
def test_independent_connections_cannot_oversubscribe(flow_db, capacity, mode):
    configure(capacity, mode)
    site, report = _seed_catalog()
    with database.get_db() as db:
        flow_paths.save_setting(db, f"flows_portal_capacity:{site['id']}", 32)
    runs = queue_jobs(7, mode=mode, catalog=(site, report))
    identities = [f'race-{index}' for index in range(8)]
    for identity in identities:
        worker(identity, headed=mode == 'headed')
    barrier = Barrier(len(identities))
    def claim(identity):
        barrier.wait(timeout=15)
        return identity, flows.claim_run(identity)
    with ThreadPoolExecutor(max_workers=len(identities)) as executor:
        results = list(executor.map(claim, identities))
    claimed = [(identity, result['run']['id']) for identity, result in results if result['run']]
    assert len(claimed) == capacity
    assert len({run for _, run in claimed}) == capacity
    assert all(run in runs for _, run in claimed)
    # A retry receives its own assignment before capacity is tested.
    for identity, run in claimed:
        assert flows.claim_run(identity)['run']['id'] == run


def test_scans_and_finalization_consume_capacity_and_lowering_drains(flow_db):
    configure(2)
    site, report = _seed_catalog()
    with database.get_db() as db:
        scan_id, _ = flows._queue_scan(db, site, 'manual', 'Analyst')
    worker(pool.worker_id(1)); worker(pool.worker_id(2)); worker('waiting')
    assert flows.claim_run(pool.worker_id(1))['scan']['id'] == scan_id
    run_ids = queue_jobs(2, catalog=(site, report))
    run = flows.claim_run(pool.worker_id(2))['run']
    assert run['id'] in run_ids
    flows.update_run(pool.worker_id(2), run['id'], flows.WorkerProgress(status='running', progress={'stage': 'sql_handoff'}))
    assert flows.claim_run('waiting')['waiting_for_capacity']
    configure(1)
    assert flows.claim_run(pool.worker_id(2))['run']['id'] == run['id']
    assert flows.claim_run(pool.worker_id(1))['scan']['id'] == scan_id
    flows.update_run(pool.worker_id(2), run['id'], flows.WorkerProgress(status='failed', error='fixture finished'))
    assert flows.claim_run('waiting')['waiting_for_capacity']
    flows.update_scan(pool.worker_id(1), scan_id, flows.ScanProgress(status='failed', error='fixture finished'))
    assert flows.claim_run(pool.worker_id(2))['waiting_for_capacity']  # disabled fixed slot
    assert flows.claim_run(pool.worker_id(1))['run']['id'] in run_ids


def test_headed_defaults_to_one_independently_of_background_capacity(flow_db):
    configure(5)
    queue_jobs(2, mode='headed')
    worker('visible-one', headed=True); worker('visible-two', headed=True); worker('background')
    assert flows.claim_run('visible-one')['run']
    assert flows.claim_run('visible-two')['waiting_for_capacity']
    assert flows.claim_run('background')['run'] is None


def test_capacity_api_persistence_validation_and_online_status(flow_db, monkeypatch):
    app = FastAPI(); app.include_router(system_flows.router)
    monkeypatch.setattr(system_flows, 'require_app_access', lambda request: None)
    with TestClient(app) as client:
        assert client.get('/api/system/flows').json()['headless_capacity'] == 12
        for invalid in [0, 33, -1, '3', True, 2.5]:
            assert client.put('/api/system/flows', json={'headless_capacity': invalid}).status_code == 422
            assert client.put('/api/system/flows', json={'headless_capacity': 3, 'headed_capacity': invalid}).status_code == 422
        assert client.put('/api/system/flows', json={'headless_capacity': 3, 'headed_capacity': 4}).json()['headed_capacity'] == 4
        result = client.put('/api/system/flows', json={'headless_capacity': 3})
        assert result.status_code == 200 and result.json()['online_capacity'] == 0
        worker(pool.worker_id(2))
        state = client.get('/api/system/flows').json()
        assert state['online_capacity'] == 1 and state['headless_capacity'] == 3
        assert state['slots'][1]['service_name'] == 'MXFlowsWorker2'
        assert state['headed_capacity'] == 4  # older clients do not reset it
        worker(pool.worker_id(3, 'headed'), headed=True)
        state = client.get('/api/system/flows').json()
        assert state['online_headed_capacity'] == 1
        assert state['headed_slots'][2]['task_name'] == 'Metronome_Flows_Headed3'
    with database.get_db() as db:
        readiness = pipelines._worker_readiness(db, {'headless'})[0]
        assert readiness['ready'] and readiness['worker_id'] == pool.worker_id(2)
        assert readiness['configured_capacity'] == 3
        visible = pipelines._worker_readiness(db, {'headed'})[0]
        assert visible['ready'] and visible['worker_id'] == pool.worker_id(3, 'headed')
        assert visible['configured_capacity'] == 4


def test_watchdog_only_starts_missing_configured_slots(flow_db, monkeypatch):
    configure(3); worker(pool.worker_id(1)); worker(pool.worker_id(3))
    starts = []
    monkeypatch.setattr(flows, 'launch_local_worker', lambda mode, **kwargs: starts.append((mode, kwargs)) or {'status': 'starting'})
    state = flows.ensure_local_worker()
    assert starts == [('headless', {'slot': 2})]
    assert len(state['slots']) == 3
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET status='offline' WHERE worker_id=?", (pool.worker_id(3),))
    starts.clear()
    flows.ensure_local_worker()
    assert starts == [('headless', {'slot': 2}), ('headless', {'slot': 3})]


def test_start_and_stop_use_exact_fixed_service_and_reject_unknown(monkeypatch):
    monkeypatch.setattr(flow_local_runner.platform, 'system', lambda: 'Windows')
    commands = []
    monkeypatch.setattr(flow_local_runner.subprocess, 'run', lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0, stdout='', stderr=''))
    assert flow_local_runner.launch_local_worker('headless', slot=4)['worker_id'] == pool.worker_id(4)
    assert commands[-1] == ['sc.exe', 'start', 'MXFlowsWorker4']
    flow_local_runner.stop_local_worker('headless', None, worker_id=pool.worker_id(3))
    assert commands[-1] == ['sc.exe', 'stop', 'MXFlowsWorker3']
    before = len(commands)
    assert flow_local_runner.stop_local_worker('headless', None, worker_id='remote-worker')['status'] == 'error'
    assert len(commands) == before
    flow_local_runner.stop_local_worker('headless', 44, worker_id=pool.worker_id(3))
    assert commands[-1] == ['taskkill.exe', '/PID', '44', '/T', '/F']


def test_installer_reads_saved_capacity_and_only_explicit_override_writes(tmp_path):
    path = tmp_path / 'settings.db'
    assert installer_capacity(path) == 12 and not path.exists()
    assert installer_capacity(path, 4) == 4
    before = path.read_bytes()
    assert installer_capacity(path) == 4 and path.read_bytes() == before
    assert installer_capacity(path, 2) == 2
    assert installer_capacity(path, mode='headed') == 1
    assert installer_capacity(path, 3, mode='headed') == 3
    assert installer_capacity(path, mode='headed') == 3 and installer_capacity(path) == 2
    with pytest.raises(ValueError):
        installer_capacity(path, 33)


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell service configuration')
@pytest.mark.parametrize('mode', ['headless', 'headed'])
def test_installer_slot_profiles_and_ids_match_server_without_running_services(mode):
    flag = '-Headed' if mode == 'headed' else ''
    command = f". ./tools/flow_pool.ps1; @(1..32 | ForEach-Object {{ Get-MetronomeFlowSlot -Slot $_ -BaseProfile 'C:\\Users\\BI\\.metronome-flow-browser' {flag} }}) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], capture_output=True, text=True, check=True)
    slots = json.loads(result.stdout)
    assert len({slot['Profile'] for slot in slots}) == 32
    for slot in slots:
        assert slot['WorkerId'] == pool.worker_id(slot['Slot'], mode)
        assert slot['ServiceName'] == pool.service_name(slot['Slot'])
        assert slot['TaskName'] == pool.task_name(slot['Slot'])
    assert slots[0]['Profile'].endswith('.metronome-flow-browser')
    source = Path('setup.ps1').read_text()
    assert source.index('foreach ($PoolSlot in 2..$FlowMaxSlots)') < source.index('& robocopy.exe')
    assert source.index('$PoolService.WaitForStatus') < source.index('& robocopy.exe')
    assert 'flow_pool_config.py' in source and '$ExpectedWorkerIds -contains' in source


def test_headed_launcher_starts_only_configured_interactive_tasks_and_stops_exact_slot(flow_db, monkeypatch):
    configure(3, 'headed')
    monkeypatch.setattr(flow_local_runner.platform, 'system', lambda: 'Windows')
    commands = []
    monkeypatch.setattr(flow_local_runner.subprocess, 'run', lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0, stdout='', stderr=''))
    result = flow_local_runner.launch_local_worker('headed')
    assert [slot['worker_id'] for slot in result['slots']] == [pool.worker_id(n, 'headed') for n in [1, 2, 3]]
    assert [command[1:] for command in commands] == [['/Run', '/TN', '\\' + pool.task_name(n)] for n in [1, 2, 3]]
    flow_local_runner.stop_local_worker('headed', None, worker_id=pool.worker_id(3, 'headed'))
    assert commands[-1][1:] == ['/End', '/TN', '\\Metronome_Flows_Headed3']
    before = len(commands)
    assert flow_local_runner.stop_local_worker('headed', None, worker_id='unknown')['status'] == 'error'
    assert len(commands) == before


def test_headed_capacity_reduction_preserves_assignments_but_disables_extra_slots(flow_db):
    configure(2, 'headed')
    queue_jobs(3, mode='headed')
    first, second = pool.worker_id(1, 'headed'), pool.worker_id(2, 'headed')
    worker(first, headed=True); worker(second, headed=True)
    one, two = flows.claim_run(first)['run'], flows.claim_run(second)['run']
    configure(1, 'headed')
    assert flows.claim_run(second)['run']['id'] == two['id']
    flows.update_run(second, two['id'], flows.WorkerProgress(status='failed', error='fixture'))
    flows.update_run(first, one['id'], flows.WorkerProgress(status='failed', error='fixture'))
    assert flows.claim_run(second)['waiting_for_capacity']
    assert flows.claim_run(first)['run']


def test_visible_helpers_stay_available_while_work_is_queued_or_running(flow_db):
    runs = queue_jobs(1, mode='headed')
    registration = flows.WorkerRegister(worker_id='visible-helper', display_name='Visible', capabilities={'headed':True})
    assert flows.register_worker(registration)['headed_work_pending']
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status='running' WHERE id=?", (runs[0],))
    assert flows.register_worker(registration)['headed_work_pending']
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status='succeeded' WHERE id=?", (runs[0],))
    assert not flows.register_worker(registration)['headed_work_pending']


@pytest.mark.parametrize('shared', [1, 12, 32])
def test_mixed_mode_claim_race_honors_one_shared_ceiling(flow_db, shared):
    configure(32); configure(32, 'headed')
    site, report = _seed_catalog()
    with database.get_db() as db:
        flow_paths.save_setting(db, pool.TOTAL_CAPACITY_KEY, shared)
        flow_paths.save_setting(db, f"flows_portal_capacity:{site['id']}", 32)
    per_mode = (shared + 5) // 2
    identities = []
    for mode in ['headless', 'headed']:
        queue_jobs(per_mode, mode=mode, catalog=(site, report))
        for slot in range(1, per_mode + 1):
            identity = pool.worker_id(slot, mode)
            worker(identity, headed=mode == 'headed')
            identities.append(identity)
    barrier = Barrier(len(identities))
    def claim(identity):
        barrier.wait(timeout=30)
        return identity, flows.claim_run(identity)
    with ThreadPoolExecutor(max_workers=len(identities)) as executor:
        results = list(executor.map(claim, identities))
    claimed = [(identity, result['run']['id']) for identity, result in results if result.get('run')]
    assert len(claimed) == shared
    assert len({run for _, run in claimed}) == shared
    with database.get_db() as db:
        assert pool.state(db)['active_total'] == shared
        flow_paths.save_setting(db, pool.TOTAL_CAPACITY_KEY, 1)
    # Lowering the shared ceiling cannot revoke either mode's assignments.
    for identity, run_id in claimed:
        assert flows.claim_run(identity)['run']['id'] == run_id
    idle = next(identity for identity, result in results if not result.get('run'))
    assert flows.claim_run(idle)['waiting_for_capacity']
    for identity, run_id in claimed:
        flows.update_run(identity, run_id, flows.WorkerProgress(status='failed', error='fixture finished'))
    assert flows.claim_run(idle)['run']


def test_shared_capacity_includes_visible_recording_reservations(flow_db):
    configure(32); configure(32, 'headed')
    site, report = _seed_catalog()
    with database.get_db() as db:
        flow_paths.save_setting(db, pool.TOTAL_CAPACITY_KEY, 1)
        scan_id, _ = flows._queue_scan(db, site, 'manual', 'Analyst')
        job = json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone()[0])
        job['execution']['browser_mode'] = 'headed'
        job['recording_operation'] = 'record'
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan_id))
    identity = pool.worker_id(32, 'headed')
    flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity,
        capabilities={'headed':True, 'flow_recorder_v1':True}))
    assert flows.claim_run(identity)['scan']['id'] == scan_id
    queue_jobs(1, catalog=(site, report))
    worker(pool.worker_id(32))
    assert flows.claim_run(pool.worker_id(32))['waiting_for_capacity']
    with database.get_db() as db:
        assert pool.state(db)['active_total'] == 1
        db.execute("UPDATE flow_catalog_scans SET status='cancelled' WHERE id=?", (scan_id,))
    assert flows.claim_run(pool.worker_id(32))['run']


def test_expanded_capacity_api_preserves_saved_limits_and_supports_slot_32(flow_db, monkeypatch):
    app = FastAPI(); app.include_router(system_flows.router)
    monkeypatch.setattr(system_flows, 'require_app_access', lambda request: None)
    site, _ = _seed_catalog()
    with TestClient(app) as client:
        state = client.get('/api/system/flows').json()
        assert state['total_capacity'] == 12 and state['max_slots'] == 32
        assert state['portals'][0]['capacity'] == 4
        for invalid in [0, 33, -1, '12', True, 2.5]:
            assert client.put('/api/system/flows', json={'headless_capacity':12, 'total_capacity':invalid}).status_code == 422
            assert client.put(f"/api/system/flows/portals/{site['id']}", json={'capacity':invalid}).status_code == 422
        result = client.put('/api/system/flows', json={'headless_capacity':32, 'headed_capacity':32, 'total_capacity':24})
        assert result.status_code == 200
        worker(pool.worker_id(32)); worker(pool.worker_id(32, 'headed'), headed=True)
        state = client.get('/api/system/flows').json()
        assert state['slots'][-1]['service_name'] == 'MXFlowsWorker32'
        assert state['headed_slots'][-1]['task_name'] == 'Metronome_Flows_Headed32'
        assert state['online_capacity'] == state['online_headed_capacity'] == 1
        # Clients predating the shared limit preserve it on later saves.
        state = client.put('/api/system/flows', json={'headless_capacity':16}).json()
        assert state['total_capacity'] == 24 and state['headed_capacity'] == 32
        state = client.put(f"/api/system/flows/portals/{site['id']}", json={'capacity':32}).json()
        assert next(portal for portal in state['portals'] if portal['id'] == site['id'])['capacity'] == 32


def test_installer_defaults_and_shared_override_match_server(flow_db, tmp_path):
    missing = tmp_path / 'not-created.db'
    with database.get_db() as db:
        assert installer_capacity(missing) == pool.capacity(db, 'headless') == 12
        assert installer_capacity(missing, mode='headed') == pool.capacity(db, 'headed') == 1
        assert installer_capacity(missing, mode='total') == pool.total_capacity(db) == 12
    assert not missing.exists()
    for mode in ['headless', 'headed', 'total']:
        assert installer_capacity(flow_db, 32, mode=mode) == 32
        assert installer_capacity(flow_db, mode=mode) == 32
    # The installer is invoked by filename from outside the checkout during setup.
    import sys
    result = subprocess.run([sys.executable, str(Path('tools/flow_pool_config.py').resolve()), str(flow_db), '--mode', 'total'],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == '32'
