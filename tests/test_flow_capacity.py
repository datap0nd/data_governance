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


def configure(value):
    with database.get_db() as db:
        flow_paths.save_setting(db, pool.CAPACITY_KEY, value)


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
def test_independent_connections_cannot_oversubscribe(flow_db, capacity):
    configure(capacity)
    runs = queue_jobs(7)
    identities = [f'race-{index}' for index in range(8)]
    for identity in identities:
        worker(identity)
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


def test_headed_is_one_even_when_background_capacity_is_five(flow_db):
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
        assert client.get('/api/system/flows').json()['headless_capacity'] == 1
        for invalid in [0, 6, -1, '3', True, 2.5]:
            assert client.put('/api/system/flows', json={'headless_capacity': invalid}).status_code == 422
        result = client.put('/api/system/flows', json={'headless_capacity': 3})
        assert result.status_code == 200 and result.json()['online_capacity'] == 0
        worker(pool.worker_id(2))
        state = client.get('/api/system/flows').json()
        assert state['online_capacity'] == 1 and state['headless_capacity'] == 3
        assert state['slots'][1]['service_name'] == 'MXFlowsWorker2'
    with database.get_db() as db:
        readiness = pipelines._worker_readiness(db, {'headless'})[0]
        assert readiness['ready'] and readiness['worker_id'] == pool.worker_id(2)
        assert readiness['configured_capacity'] == 3


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
    assert installer_capacity(path) == 1 and not path.exists()
    assert installer_capacity(path, 4) == 4
    before = path.read_bytes()
    assert installer_capacity(path) == 4 and path.read_bytes() == before
    assert installer_capacity(path, 2) == 2
    with pytest.raises(ValueError):
        installer_capacity(path, 6)


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell service configuration')
def test_installer_slot_profiles_and_ids_match_server_without_running_services():
    command = ". ./tools/flow_pool.ps1; @(1..5 | ForEach-Object { Get-MetronomeFlowSlot -Slot $_ -BaseProfile 'C:\\Users\\BI\\.metronome-flow-browser' }) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], capture_output=True, text=True, check=True)
    slots = json.loads(result.stdout)
    assert len({slot['Profile'] for slot in slots}) == 5
    for slot in slots:
        assert slot['WorkerId'] == pool.worker_id(slot['Slot'])
        assert slot['ServiceName'] == pool.service_name(slot['Slot'])
    assert slots[0]['Profile'].endswith('.metronome-flow-browser')
    source = Path('setup.ps1').read_text()
    assert source.index('foreach ($PoolSlot in 2..5)') < source.index('& robocopy.exe')
    assert source.index('$PoolService.WaitForStatus') < source.index('& robocopy.exe')
    assert 'flow_pool_config.py' in source and '$ExpectedWorkerIds -contains' in source
