import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import database, flow_standalone as standalone, flow_worker
from app.flow_execution_lock import ExecutionLocks, resource_keys
from app.routers import flows
from test_flows import flow_db, _request


def local_job(tmp_path):
    source = tmp_path / 'daily.csv'; source.write_text('Code\nAbc\n')
    saved = flows.create_flow(flows.FlowWrite(name='Offline "quoted" flow', source_type='file', local_file_path=str(source)), _request())
    with database.get_db() as db:
        return saved, flows._build_job(db, saved['id'], force_reprocess=True)


def test_generated_launcher_is_atomic_versioned_and_detects_stale_config(flow_db, tmp_path):
    saved, job = local_job(tmp_path)
    assert saved['standalone']['state'] == 'current'
    assert standalone.status(job)['state'] == 'current'
    path = Path(saved['standalone']['launcher'])
    compile(path.read_text(), str(path), 'exec')
    compile(standalone.launcher_source(Path("C:/odd 'folder\\code"), "quote'config.json"), 'launcher', 'exec')
    job['flow']['name'] = 'renamed'
    assert standalone.status(job)['state'] == 'stale'
    previous = list(path.parent.glob('flow-config-*.json'))
    standalone.generate(job)
    assert all(p.exists() for p in previous)
    assert standalone.status(job)['state'] == 'current'


def test_dry_run_creates_nothing_and_redacts_paths(flow_db, tmp_path):
    saved, job = local_job(tmp_path)
    before = set(tmp_path.rglob('*'))
    result = subprocess.run([sys.executable, saved['standalone']['launcher'], '--dry-run'], capture_output=True, text=True, check=True)
    assert set(tmp_path.rglob('*')) == before
    data = json.loads(result.stdout)
    assert data['sql'] is False and data['flow_id'] == saved['id']
    assert str(tmp_path) not in result.stdout and job['local_file']['path'] not in result.stdout


def test_offline_local_dispatch_has_no_api_and_keeps_server_history_unchanged(flow_db, tmp_path, monkeypatch):
    saved, job = local_job(tmp_path)
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    monkeypatch.setattr(flow_worker, '_api', lambda *a, **k: pytest.fail('Offline execution must not contact the API'))
    result = standalone.run(job)
    assert result['status'] == 'succeeded'
    assert int(result['run_id']) > 2**63
    assert len(result['artifacts']) == 2
    assert Path(result['log']).is_file()
    with database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM flow_runs').fetchone()[0] == 0
        assert db.execute('SELECT local_file_last_identity FROM flows WHERE id=?', (saved['id'],)).fetchone()[0] is None
    assert Path(job['local_file']['path']).read_text() == 'Code\nAbc\n'


def test_offline_uses_shared_publication_transform_and_sql_defaults(flow_db, tmp_path, monkeypatch):
    _, job = local_job(tmp_path)
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    events = []
    job['transformation']['enabled'] = True
    job['transformation']['script_path'] = str(Path(job['paths']['flow_folder']) / 'Scripts' / 'test.py')
    Path(job['transformation']['script_path']).write_text('pass')
    job['sql_handoff']['enabled'] = True
    publish = flow_worker._publish_direct_artifacts
    def record_publish(*a, **k):
        events.append('publish'); return publish(*a, **k)
    def transform(items, _config):
        events.append('transform')
        assert len(items) == 1 and items[0]['status'] == 'saved'
        return [{**items[0], 'status': 'transformed'}]
    monkeypatch.setattr(flow_worker, '_publish_direct_artifacts', record_publish)
    monkeypatch.setattr(flow_worker, '_run_transformations', transform)
    from app import flow_sql
    def load(items, target, **kwargs):
        events.append('sql')
        assert len(items) == 1 and items[0]['status'] == 'transformed'
        return {'rows_written': 1, 'files_loaded': 1, 'target': 'Db.Schema.Table'}
    monkeypatch.setattr(flow_sql, 'load_artifacts', load)
    standalone.run(job)
    assert events == ['publish','transform','sql']
    events.clear()
    standalone.run(job, sql=False)
    assert events == ['publish','transform']


def test_process_locks_compete_and_release_on_failure(tmp_path):
    root = tmp_path / 'locks'
    with ExecutionLocks(['output:a'], root=root):
        code = 'from pathlib import Path; from app.flow_execution_lock import ExecutionLocks; ExecutionLocks(["output:a"], root=Path(__import__("sys").argv[1])).acquire()'
        result = subprocess.run([sys.executable, '-c', code, str(root)], capture_output=True, text=True)
        assert result.returncode != 0 and 'Another Flow process' in result.stderr
    with ExecutionLocks(['output:a'], root=root):
        pass
    job = {'flow': {'id': 1}, 'downloads': {'target_folder': r'C:\Reports'}, 'sql_handoff': {'enabled': True, 'server': 's', 'database': 'Db', 'schema': 'Mixed', 'table': 'Table'}}
    keys = resource_keys(job)
    assert len(keys) == 3 and 'Mixed' in keys[-1]


def test_bundles_reject_credentials_and_foreign_launcher(flow_db, tmp_path):
    saved, job = local_job(tmp_path)
    job['report']['automation']['password'] = 'secret'
    with pytest.raises(ValueError, match='credential'):
        standalone.freeze(job)
    job['report']['automation'].pop('password')
    Path(saved['standalone']['launcher']).write_text('print("user file")')
    with pytest.raises(ValueError, match='not a managed launcher'):
        standalone.generate(job)
    assert 'user file' in Path(saved['standalone']['launcher']).read_text()


def test_dry_run_reports_saved_sql_enabled_without_connecting(flow_db, tmp_path, capsys):
    _, job = local_job(tmp_path)
    job['sql_handoff']['enabled'] = True
    generated = standalone.generate(job)
    bundle = Path(generated['launcher']).with_name('flow-config-' + generated['config_hash'] + '.json')
    assert standalone.main(['--dry-run'], bundle=bundle) == 0
    assert json.loads(capsys.readouterr().out)['sql'] is True
    assert standalone.main(['--dry-run', '--no-sql'], bundle=bundle) == 0
    assert json.loads(capsys.readouterr().out)['sql'] is False


def test_partial_portal_failure_keeps_files_and_logs_without_api(flow_db, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    _, job = local_job(tmp_path)
    job['flow']['source_type'] = 'portal'
    job['local_file']['enabled'] = False
    job['downloads']['target_folder'] = str(Path(job['paths']['flow_folder']) / 'Downloads')
    job['downloads']['output_mode'] = 'run_folders'
    job['paths']['source_folder'] = 'Local'  # test-only fake portal in an owned fixture
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    events = []
    browser = SimpleNamespace(pages=[object()], close=lambda: events.append('closed'))
    def launch(*args, **kwargs):
        assert args[0].endswith('standalone-profile') and kwargs['headless']
        return browser
    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    monkeypatch.setattr(flow_worker, 'sync_playwright', playwright)
    monkeypatch.setattr(flow_worker, '_api', lambda *a, **k: pytest.fail('API called offline'))
    partial = Path(job['downloads']['target_folder']) / 'partial.csv'
    def fail(page, job, progress, profile, staging, artifacts, **kwargs):
        assert kwargs['register_folder'](str(partial.parent)) == {'ops': []}
        partial.write_text('Code\nA\n')
        artifacts.append({'file_path':str(partial), 'status':'saved'})
        raise RuntimeError('second export failed')
    monkeypatch.setattr(flow_worker, 'execute_job', fail)
    with pytest.raises(RuntimeError, match='second export'):
        standalone.run(job)
    assert partial.is_file() and events == ['closed']
    logs = list((Path(job['paths']['flow_folder']) / 'Scripts' / 'standalone-logs').glob('*.jsonl'))
    failed = json.loads(logs[0].read_text().splitlines()[-1])
    assert failed['status'] == 'failed' and 'second export failed' in failed['progress']['message']
    assert failed['artifacts'] == [{'file_path': str(partial), 'status': 'saved'}]
    assert failed['timings'][-1]['status'] == 'failed'


def test_worker_uses_same_resource_lock_for_entire_run():
    source = Path('app/flow_worker.py').read_text(encoding='utf-8')
    acquire = source.index('execution_locks.acquire()')
    release = source.index('execution_locks.release()', acquire)
    assert acquire < source.index('execute_flow(page, run["job"]', acquire) < release
    shared = source[source.index('def execute_flow('):source.index('def run_worker(')]
    assert 'sql_result = load_artifacts(' in shared
    assert 'artifacts = _publish_direct_artifacts(' in shared
