import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import database, flow_handover, flow_layout, flow_standalone
from app.routers import flows, flow_recordings as recordings
from test_flows import flow_db, _request
from test_flow_standalone import local_job
from test_flow_recordings import draft_job, definition


def manifest(saved):
    return flow_layout.read_manifest(saved['flow_folder'], saved['id'])


def test_create_has_complete_json_script_and_operator_instructions(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    data = manifest(saved)
    config = data['configuration']
    assert config['name'] == saved['name']
    assert config['created_by'] == 'Analyst'
    assert config['schedule']['timezone'] == 'Asia/Dubai'
    assert config['source']['adapter'] == 'local_file'
    assert config['settings']['local_file_path'] == str(tmp_path / 'daily.csv')
    assert config['settings']['filename_template']
    assert config['settings']['output_mode']
    assert data['handover']['state'] == 'current'
    scripts = Path(saved['flow_folder']) / 'Scripts'
    assert 'Task Scheduler' in (scripts / 'README.md').read_text(encoding='utf-8')
    assert 'playwright' in (scripts / 'requirements.txt').read_text()


def test_python_runs_in_isolation_without_installed_app_or_server(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    script = tmp_path / 'independent.py'
    script.write_bytes(Path(saved['standalone']['launcher']).read_bytes())
    source = script.read_text(encoding='utf-8')
    assert 'from app.' not in source and 'import app' not in source
    env = {**os.environ, 'DG_FLOW_LOCK_ROOT': str(tmp_path / 'isolated-locks')}
    result = subprocess.run([sys.executable, '-I', str(script)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'succeeded'
    with database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM flow_runs').fetchone()[0] == 0


def test_owner_schedule_and_shared_changes_refresh_without_export(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    with database.get_db() as db:
        owner = db.execute("INSERT INTO people(name,email,role) VALUES ('Team Operator','operator@example.test','owner')").lastrowid
    original = manifest(saved)['handover']['launcher_hash']
    result = flows.patch_flow(saved['id'], flows.FlowInlineWrite(owner_person_id=owner), _request())
    assert result['standalone']['state'] == 'current'
    assert manifest(saved)['configuration']['owner']['name'] == 'Team Operator'
    assert manifest(saved)['handover']['launcher_hash'] != original
    body = flows.FlowWrite(name='Renamed daily source', source_type='file',
                          local_file_path=str(tmp_path / 'daily.csv'), schedule_type='daily', schedule_time='10:30')
    flows.update_flow(saved['id'], body, _request())
    flows.set_flow_enabled(saved['id'], flows.FlowEnabledWrite(enabled=True), _request())
    assert manifest(saved)['configuration']['schedule'] == {
        'type': 'daily', 'time': '10:30', 'days': [], 'day': None, 'enabled': True, 'timezone': 'Asia/Dubai'}
    with database.get_db() as db:
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('flows_recording_wait_seconds','35')")
    assert manifest(saved)['configuration']['defaults']['recording_wait_seconds'] == 35


def test_draft_always_has_python_and_saved_definition(flow_db, tmp_path):
    saved, _ = draft_job()
    assert saved['standalone']['state'] == 'draft'
    result = recordings.save_revision(saved['id'], recordings.RevisionWrite(definition=definition()))
    data = manifest(saved)
    assert data['configuration']['recording']['revisions'][0]['id'] == result['revision_id']
    assert data['configuration']['recording']['active_revision_id'] is None
    script = Path(data['handover']['launcher'])
    run = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 2 and 'cannot run yet' in run.stderr
    assert flows.standalone_status(saved['id'])['state'] == 'draft'


def test_rollback_and_unrelated_reads_leave_files_unchanged(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    original = (Path(saved['flow_folder']) / 'flow.json').read_bytes()
    with pytest.raises(RuntimeError):
        with database.get_db() as db:
            db.execute('UPDATE flows SET name=? WHERE id=?', ('Rolled back', saved['id']))
            raise RuntimeError('rollback')
    with database.get_db() as db:
        assert db.execute('SELECT name FROM flows WHERE id=?', (saved['id'],)).fetchone()[0] == saved['name']
    assert (Path(saved['flow_folder']) / 'flow.json').read_bytes() == original


def test_generation_failure_preserves_database_and_recovers_automatically(flow_db, tmp_path, monkeypatch):
    saved, _ = local_job(tmp_path)
    generate = flow_standalone.generate
    monkeypatch.setattr(flow_standalone, 'generate', lambda *_: (_ for _ in ()).throw(OSError('offline share')))
    result = flows.patch_flow(saved['id'], flows.FlowInlineWrite(owner_person_id=None), _request())
    # Force a meaningful saved configuration change after the equivalent owner save.
    with database.get_db() as db:
        db.execute('UPDATE flows SET name=? WHERE id=?', ('Still saved', saved['id']))
    assert manifest(saved)['handover']['state'] == 'error'
    assert manifest(saved)['configuration']['name'] == 'Still saved'
    monkeypatch.setattr(flow_standalone, 'generate', generate)
    flow_handover.after_commit(database.DB_PATH)
    assert manifest(saved)['handover']['state'] == 'current'


def test_modified_script_preserved_and_missing_script_recreated(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    script = Path(saved['standalone']['launcher'])
    script.write_text('# my manual edits\n')
    flow_handover.after_commit(database.DB_PATH)
    assert script.read_text() == '# my manual edits\n'
    assert flows.standalone_status(saved['id'])['state'] == 'error'
    script.rename(script.with_name('operator-copy.py'))
    flow_handover.after_commit(database.DB_PATH)
    assert flows.standalone_status(saved['id'])['state'] == 'current'


def test_metadata_excludes_secrets_and_retains_source_identity():
    value = flow_handover.safe_metadata({'name': 'Bookmark', 'password': 'secret',
        'url': 'https://user:secret@example.test/report?bookmark=public&access_token=secret'})
    assert 'secret' not in json.dumps(value)
    assert 'bookmark=public' in value['url']


def test_source_catalog_edits_refresh_json_and_script(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    with database.get_db() as db:
        db.execute('UPDATE flow_reports SET name=? WHERE id=?', ('Updated source', saved['report_id']))
    assert manifest(saved)['configuration']['report']['name'] == 'Updated source'
    assert 'Updated source' in Path(saved['standalone']['launcher']).read_text(encoding='utf-8')


def test_outlook_portable_embeds_and_materializes_its_helper(flow_db, tmp_path):
    saved = flows.create_flow(flows.FlowWrite(name='Inbox handover', source_type='outlook',
                             outlook_subject_contains='Fictional report'), _request())
    code = '''import runpy,sys,json
from pathlib import Path
runpy.run_path(sys.argv[1])
from _mf import flow_outlook as outlook
outlook.platform.system = lambda: 'Windows'
outlook._SCRIPT = Path('absent-installation/helper.ps1')
profile = Path(sys.argv[2])
def command(args, timeout, **kwargs):
    if '/run' in args:
        (profile / 'outlook_downloads/run-42/result.json').write_text(json.dumps({'status':'no_match'}))
outlook.acquire_attachment(run_id=42, profile_dir=profile, subject_contains='Fictional report',
    last_processed_identity=None, force_reprocess=True, command_runner=command)
print((profile / 'outlook_downloads/run-42/outlook_flow_attachment.ps1').read_text(encoding='utf-8-sig'))
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, saved['standalone']['launcher'], str(tmp_path / 'profile')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = Path('tools/outlook_flow_attachment.ps1').read_text(encoding='utf-8-sig')
    assert result.stdout.strip() == expected.strip()


def test_legacy_installed_launcher_is_upgraded_and_archived(flow_db, tmp_path):
    saved, job = local_job(tmp_path)
    script = Path(saved['standalone']['launcher'])
    script.rename(script.with_name('original-portable.py'))
    old = flow_standalone.generate_installed_launcher(job)
    assert 'from app.flow_standalone' in script.read_text()
    flow_handover.synchronize(database.DB_PATH, force=True)
    assert 'from app.flow_standalone' not in script.read_text()
    assert list((script.parent / 'versions').glob('*.py'))


def test_existing_operator_notes_and_dependencies_are_archived_before_refresh(flow_db, tmp_path):
    saved, _ = local_job(tmp_path)
    scripts = Path(saved['flow_folder']) / 'Scripts'
    (scripts / 'README.md').write_text('Team-specific recovery notes', encoding='utf-8')
    (scripts / 'requirements.txt').write_text('custom-transform-library==1.0', encoding='utf-8')
    flow_handover.synchronize(database.DB_PATH, flow_id=saved['id'], force=True)
    assert next((scripts / 'versions').glob('README-*.md')).read_text() == 'Team-specific recovery notes'
    assert next((scripts / 'versions').glob('requirements-*.txt')).read_text() == 'custom-transform-library==1.0'
    assert 'Task Scheduler' in (scripts / 'README.md').read_text()
    assert flows.standalone_status(saved['id'])['state'] == 'current'
