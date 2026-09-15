"""Synthetic filesystem/save integration; no external services or business data."""
import ast
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database, flow_folder_rename, flow_layout, flow_paths
from app.flow_execution_lock import ExecutionLocks
from app.routers import flows, flow_recordings as recordings
from test_flows import flow_db, _flow, _request, _seed_catalog
from test_flow_standalone import local_job
from test_flow_recordings import draft_job, definition


def update(saved, **values):
    body = flows.FlowWrite.model_validate({**saved, **values})
    return flows.update_flow(saved['id'], body, _request())


def frozen(saved):
    source = Path(saved['standalone']['launcher']).read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'FLOW' for t in n.targets))
    return json.loads(ast.literal_eval(node.value.args[0]))


@pytest.mark.parametrize('name,expected', [('Daily orders', 'Daily orders'), ('bad/name?', 'badname'),
    ('CON.py', 'Flow CON.py'), ('  Daily   orders. ', 'Daily orders'), ('...', 'Flow'),
    ('x' * 100, 'x' * 72)])
def test_folder_names_are_safe_and_do_not_append_identity(name, expected):
    assert flow_layout.flow_folder_slug(name, 987654) == expected


def test_legacy_id_folder_migrates_on_unchanged_save(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    folder = Path(saved['flow_folder'])
    legacy = folder.with_name(folder.name + f' (id {saved["id"]})')
    folder.rename(legacy)
    with database.get_db() as db:
        db.execute('UPDATE flows SET flow_folder=?,folder_slug=? WHERE id=?', (str(legacy), legacy.name, saved['id']))
    updated = update(saved)
    assert Path(updated['flow_folder']) == folder
    assert not legacy.exists()
    assert updated['standalone']['state'] == 'current'
    assert frozen(updated)['paths']['flow_folder'] == str(folder)


@pytest.mark.parametrize('occupied', ['Weekly orders', 'weekly orders', 'Weekly orders?'])
def test_collision_preserves_files_and_saved_name_and_retry_works(flow_db, tmp_path, occupied):
    saved, _ = local_job(tmp_path)
    old = Path(saved['flow_folder'])
    other = old.with_name('Weekly orders'); other.mkdir()
    (other / 'user.txt').write_text('keep')
    before = (old / 'flow.json').read_bytes()
    with pytest.raises(HTTPException, match='already exists'):
        update(saved, name=occupied)
    assert (other / 'user.txt').read_text() == 'keep'
    assert (old / 'flow.json').read_bytes() == before
    with database.get_db() as db:
        assert db.execute('SELECT name FROM flows WHERE id=?', (saved['id'],)).fetchone()[0] == saved['name']
    assert Path(update(saved, name='Available name')['flow_folder']).name == 'Available name'


def test_creation_rejects_sanitized_case_insensitive_collision(tmp_path):
    folder = flow_layout.create_flow_folder(str(tmp_path / 'root'), 'web_export', 'Orders?', 1)
    with pytest.raises(FileExistsError, match='already exists'):
        flow_layout.create_flow_folder(str(tmp_path / 'root'), 'web_export', 'orders', 2)
    assert flow_layout.read_manifest(folder, 1)['flow_id'] == 1


def test_case_only_rename_updates_actual_directory_spelling(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    saved = update(saved, name='Daily orders')
    updated = update(saved, name='DAILY ORDERS')
    parent = Path(updated['flow_folder']).parent
    assert 'DAILY ORDERS' in [p.name for p in parent.iterdir()]
    assert 'Daily orders' not in [p.name for p in parent.iterdir()]
    assert frozen(updated)['flow']['name'] == 'DAILY ORDERS'


@pytest.mark.parametrize('failure', ['move', 'commit'])
def test_failed_rename_restores_folder_database_and_script(flow_db, tmp_path, monkeypatch, failure):
    saved, _ = local_job(tmp_path)
    old = Path(saved['flow_folder'])
    original = (old / 'Scripts' / 'run_flow.py').read_bytes()
    marker = (old / 'flow.json').read_bytes()
    with monkeypatch.context() as patch:
        if failure == 'move':
            patch.setattr(flow_folder_rename, '_move', lambda *_: (_ for _ in ()).throw(PermissionError('folder busy')))
        else:
            @contextmanager
            def fail_commit():
                with database.get_db() as db:
                    yield db
                    raise OSError('commit failure')
            patch.setattr(flows, 'get_db', fail_commit)
        with pytest.raises(HTTPException, match='folder busy|commit failure'):
            update(saved, name='Renamed')
    assert (old / 'Scripts' / 'run_flow.py').read_bytes() == original
    assert json.loads((old / 'flow.json').read_bytes()) == json.loads(marker)
    assert not old.with_name('Renamed').exists()
    with database.get_db() as db:
        row = db.execute('SELECT name,flow_folder FROM flows WHERE id=?', (saved['id'],)).fetchone()
        assert tuple(row) == (saved['name'], str(old))
    assert update(saved, name='Renamed')['standalone']['state'] == 'current'


def test_interrupted_move_is_recovered_before_next_save(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    old = Path(saved['flow_folder']); new = old.with_name('Interrupted')
    flow_layout.update_manifest(old, saved['id'], folder_rename={'from':str(old), 'to':str(new)})
    old.rename(new)  # Simulate process exit after filesystem move, before DB commit.
    updated = update(saved, name='Recovered')
    assert Path(updated['flow_folder']).name == 'Recovered'
    assert not new.exists() and not old.exists()
    assert 'folder_rename' not in flow_layout.read_manifest(updated['flow_folder'], saved['id'])
    assert updated['standalone']['state'] == 'current'


def test_interrupted_case_only_move_restores_database_spelling(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    saved = update(saved, name='Original')
    old = Path(saved['flow_folder']); new = old.with_name('ORIGINAL')
    flow_layout.update_manifest(old, saved['id'], folder_rename={'from':str(old), 'to':str(new)})
    old.rename(new)
    updated = update(saved)
    assert Path(updated['flow_folder']).name == 'Original'
    assert 'Original' in [p.name for p in old.parent.iterdir()]
    assert 'ORIGINAL' not in [p.name for p in old.parent.iterdir()]


def test_pending_publication_prevents_moving_recovery_journal(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    folder = Path(saved['flow_folder'])
    journal = folder / 'Downloads' / '.metronome-publish-fixture.json'
    journal.write_text('fictional interrupted publication')
    with pytest.raises(HTTPException, match='interrupted output publication'):
        update(saved, name='Later')
    assert journal.read_text() == 'fictional interrupted publication'


def test_move_cannot_replace_even_an_empty_concurrent_destination(tmp_path):
    old = tmp_path / 'old'; new = tmp_path / 'new'
    old.mkdir(); new.mkdir()
    with pytest.raises(OSError):
        flow_folder_rename._move(old, new)
    assert old.is_dir() and new.is_dir()


@pytest.mark.parametrize('state', ['queued', 'claimed', 'running'])
def test_rename_waits_for_active_run(flow_db, tmp_path, state):
    saved, job = local_job(tmp_path)
    with database.get_db() as db:
        db.execute('INSERT INTO flow_runs(flow_id,trigger_type,status,job_json) VALUES (?,?,?,?)',
                   (saved['id'], 'manual', state, json.dumps(job)))
    with pytest.raises(HTTPException, match='active Flow run'):
        update(saved, name='Later')
    assert Path(saved['flow_folder']).is_dir()


def test_save_waits_for_offline_execution_lock(flow_db, tmp_path, monkeypatch):
    saved, _ = local_job(tmp_path)
    monkeypatch.setenv('DG_FLOW_LOCK_ROOT', str(tmp_path / 'locks'))
    with ExecutionLocks([f'flow:{saved["id"]}']):
        with pytest.raises(HTTPException, match='Another Flow process'):
            update(saved, name='Later')


def test_history_and_transform_paths_follow_rename_and_source_is_preserved(flow_db, tmp_path):
    site, report = _seed_catalog()
    script = tmp_path / 'transform.py'; script.write_text('print("fictional transform")')
    saved = flows.create_flow(_flow(site['id'], report['id'], name='Old name', enabled=False,
        transform_enabled=True, transform_script_path=str(script)), _request())
    old = Path(saved['flow_folder'])
    run_folder = old / 'Downloads' / '#99_15-09-2026'; run_folder.mkdir()
    output = run_folder / 'report.csv'; output.write_text('value\n1\n')
    private = str(tmp_path / 'private' / 'report.csv')
    with database.get_db() as db:
        job = flows._build_job(db, saved['id'])
        run = db.execute('INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,run_folder,folder_key,artifact_json) VALUES (?,?,?,?,?,?,?)',
            (saved['id'], 'manual', 'succeeded', json.dumps(job), str(run_folder), flows._folder_key(str(run_folder)),
             json.dumps([{'file_path':str(output)}, {'file_path':private}]))).lastrowid
        db.execute('INSERT INTO flow_run_files(run_id,file_path,filename,published_file_path,status) VALUES (?,?,?,?,?)',
            (run, private, 'report.csv', str(output), 'saved'))
    updated = update(saved, name='New name')
    new = Path(updated['flow_folder'])
    assert Path(updated['transform_script_path']).is_file()
    assert Path(updated['transform_script_path']).parent == new / 'Scripts'
    assert script.read_text() == 'print("fictional transform")'
    with database.get_db() as db:
        row = db.execute('SELECT * FROM flow_runs WHERE id=?', (run,)).fetchone()
        assert Path(row['run_folder']).parent == new / 'Downloads'
        assert row['folder_key'] == flows._folder_key(row['run_folder'])
        artifacts = json.loads(row['artifact_json'])
        assert Path(artifacts[0]['file_path']).read_text() == 'value\n1\n'
        assert artifacts[1]['file_path'] == private
        assert json.loads(row['job_json'])['flow']['name'] == 'Old name'
        files = db.execute('SELECT * FROM flow_run_files WHERE run_id=?', (run,)).fetchone()
        assert files['file_path'] == private and Path(files['published_file_path']).is_file()
        flow_paths.assert_job_paths(flows._build_job(db, saved['id']))


def test_generated_python_refreshes_before_save_returns_and_executes_new_settings(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    old_script = Path(saved['standalone']['launcher']).read_bytes()
    source = tmp_path / 'changed.csv'; source.write_text('Code\nNEW\n')
    updated = update(saved, name='Updated daily', local_file_path=str(source), browser_mode='headed')
    script = Path(updated['standalone']['launcher'])
    assert script.read_bytes() != old_script
    job = frozen(updated)
    assert job['flow']['name'] == 'Updated daily'
    assert job['local_file']['path'] == str(source)
    result = subprocess.run([sys.executable, '-I', str(script)], cwd=tmp_path,
        env={**os.environ, 'DG_FLOW_LOCK_ROOT':str(tmp_path / 'locks')}, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'succeeded'


@pytest.mark.parametrize('method', ['catalog', 'recorded'])
def test_in_place_transform_edit_refreshes_generated_python_on_unchanged_save(flow_db, tmp_path, method):
    if method == 'recorded':
        saved, _ = draft_job()
        revision = recordings.save_revision(saved['id'], recordings.RevisionWrite(definition=definition()))
        saved = update(saved, recording_revision_id=revision['revision_id'])
    else:
        saved, _ = local_job(tmp_path)
    source = tmp_path / 'transform.py'; source.write_text('print("first transform")')
    saved = update(saved, transform_enabled=True, transform_script_path=str(source))
    script = Path(saved['standalone']['launcher']); before = script.read_bytes()
    Path(saved['transform_script_path']).write_text('print("second transform")')
    updated = update(saved)
    assert updated['standalone']['state'] == 'current'
    assert script.read_bytes() != before
    import hashlib
    assert frozen(updated)['handover']['transformation_sha256'] == hashlib.sha256(b'print("second transform")').hexdigest()
    if method == 'recorded':
        assert frozen(updated)['recording']['transformation_source'] == 'print("second transform")'
    else:
        assert Path(frozen(updated)['transformation']['script_path']).read_text() == 'print("second transform")'


def test_recording_save_refreshes_active_script_without_running_recording(flow_db):
    saved, _ = draft_job()
    first = recordings.save_revision(saved['id'], recordings.RevisionWrite(definition=definition()))
    saved = update(saved, recording_revision_id=first['revision_id'])
    before = Path(saved['standalone']['launcher']).read_bytes()
    changed = definition('https://example.test/updated-report')
    second = recordings.save_revision(saved['id'], recordings.RevisionWrite(definition=changed))
    saved = update(saved, name='Updated recorded flow', recording_revision_id=second['revision_id'])
    assert saved['standalone']['state'] == 'current'
    assert Path(saved['standalone']['launcher']).read_bytes() != before
    assert frozen(saved)['recording']['revision'] == second['revision_id']
    assert frozen(saved)['recording']['definition'] == changed
