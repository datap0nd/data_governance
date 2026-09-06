from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database, flow_layout, flow_paths, flow_publish, flow_worker
from app.routers import flows
from test_flows import flow_db, _request, _seed_catalog, _flow


def test_repair_only_owned_missing_children_and_explicit_missing_folder(flow_db):
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site['id'], report['id'], target_folder=None), _request())
    folder = Path(saved['flow_folder'])
    def remove_generated():
        flow_layout.read_manifest(folder, saved['id'])
        for generated in (folder / 'Scripts').iterdir():
            if generated.is_dir():
                assert generated.name == 'versions'
                for version in generated.iterdir():
                    assert version.name.startswith('run_flow-') and version.suffix == '.py'
                    version.unlink()
                generated.rmdir()
            else:
                assert generated.name in {'run_flow.py', 'README.md', 'requirements.txt'}
                generated.unlink()
    remove_generated()
    (folder / 'Scripts').rmdir()
    assert flows.get_flow(saved['id'])['layout']['missing'] == ['Scripts']
    assert flows.repair_flow_layout(saved['id'], _request())['layout']['created'] == ['Scripts']
    remove_generated()
    flow_layout.cleanup_empty_creation(folder, saved['id'])
    assert not folder.exists()
    flows.repair_flow_layout(saved['id'], _request())
    assert flow_layout.read_manifest(folder, saved['id'])
    (folder / 'flow.json').unlink()
    with pytest.raises(HTTPException, match='could not be repaired'):
        flows.repair_flow_layout(saved['id'], _request())
    assert not (folder / 'flow.json').exists()


def test_save_copies_script_without_overwriting_original_or_queued_version(flow_db, tmp_path):
    site, report = _seed_catalog()
    script = tmp_path / 'transform.py'
    script.write_text('print(1)')
    body = _flow(site['id'], report['id'], target_folder=None, transform_enabled=True, transform_script_path=str(script))
    saved = flows.create_flow(body, _request())
    first = Path(saved['transform_script_path'])
    assert first.parent == Path(saved['flow_folder']) / 'Scripts'
    script.write_text('print(2)')
    edited = flows.update_flow(saved['id'], _flow(site['id'], report['id'], transform_enabled=True, transform_script_path=str(script)), _request())
    assert Path(edited['transform_script_path']).read_text() == 'print(2)'
    assert first.read_text() == 'print(1)' and script.read_text() == 'print(2)'


def test_new_shared_store_cross_profile_and_legacy_identity_unchanged(tmp_path):
    one, two, shared = tmp_path / 'one', tmp_path / 'two', tmp_path / 'shared'
    legacy = flow_publish.private_target_root(one, Path('target'))
    (legacy / 'old.csv').write_text('old')
    a = flow_publish.private_target_root(one, Path('target'), store_root=shared)
    b = flow_publish.private_target_root(two, Path('target'), store_root=shared)
    assert a == b and a != legacy
    assert flow_publish.artifact_store_id(one, store_root=shared) == flow_publish.artifact_store_id(two, store_root=shared)
    assert flow_publish.artifact_store_id(one) != flow_publish.artifact_store_id(two)
    assert (legacy / 'old.csv').read_text() == 'old'


def test_managed_local_execution_uses_shared_store_and_registers_run_not_parent(flow_db, tmp_path):
    source = tmp_path / 'input.csv'
    source.write_text('Code\nA\n')
    saved = flows.create_flow(flows.FlowWrite(name='Local', source_type='file', local_file_path=str(source)), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved['id'])
    registrations = []
    profile = tmp_path / 'profile'
    result = flow_worker.execute_local_file_job(job, lambda *a, **k: None, profile, run_id=991,
        register_folder=lambda path: registrations.append(path) or {'ops': []})
    artifact = result[0][-1]  # normalized CSV; raw bytes live in its source child
    assert flow_paths.is_inside(artifact['file_path'], job['paths']['artifact_store_root'])
    assert artifact['artifact_store_id'] == flow_publish.artifact_store_id(profile, store_root=Path(job['paths']['artifact_store_root']))
    assert registrations and Path(registrations[0]) == Path(artifact['file_path']).parent
    assert source.read_text() == 'Code\nA\n'
    assert not list((Path(saved['flow_folder']) / 'Downloads').iterdir())


def test_shared_jobs_require_capability_and_registration_returns_recovery_roots(flow_db, tmp_path):
    source = tmp_path / 'input.csv'
    saved = flows.create_flow(flows.FlowWrite(name='Local', source_type='file', local_file_path=str(source)), _request())
    with database.get_db() as db:
        run = flows.queue_flow_run_service(db, saved['id'], requested_by=None, trigger_type='manual')
    caps = {'adapters': ['local_file'], 'headed': False}
    info = flows.register_worker(flows.WorkerRegister(worker_id='old', display_name='Old', capabilities=caps))
    assert info['artifact_store_roots']
    assert flows.claim_run('old')['run'] is None
    caps['shared_flow_artifacts'] = True
    flows.register_worker(flows.WorkerRegister(worker_id='new', display_name='New', capabilities=caps))
    assert flows.claim_run('new')['run']['flow_id'] == saved['id']


def test_repair_refuses_linked_child(tmp_path):
    folder = flow_layout.create_flow_folder(str(tmp_path / 'root'), 'web_export', 'Flow', 1)
    (folder / 'Scripts').rmdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    try:
        (folder / 'Scripts').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Directory links unavailable')
    with pytest.raises(ValueError, match='link'):
        flow_layout.ensure_layout(folder, 1)


def test_resume_copies_old_store_artifact_and_rebrands_only_new_copy(tmp_path):
    from test_flow_publish import _artifact
    target, current, prior = [tmp_path / name for name in ('target', 'current', 'prior')]
    for folder in (target, current, prior):
        folder.mkdir()
    old = prior / 'old.csv'; old.write_text('Code\nA\n')
    new = current / 'new.csv'; new.write_text('Code\nB\n')
    carried = _artifact(old, index=1, count=2)
    fresh = _artifact(new, index=2, count=2)
    fresh['artifact_store_id'] = 'store-b'
    job = {'downloads': {'output_mode': 'direct_replace', 'target_folder': str(target)},
        'resume': {'completed': [carried]}, '_runtime_run_folder': str(current),
        '_runtime_artifact_store_id': 'store-b', '_runtime_artifact_store_ids': ['store-a', 'store-b']}
    output = flow_worker._publish_direct_artifacts(job, [fresh], run_id=2, report_progress=lambda *a: None)
    assert {item['artifact_store_id'] for item in output} == {'store-b'}
    assert carried['artifact_store_id'] == 'store-a' and old.read_text() == 'Code\nA\n'
    assert all(Path(item['file_path']).parent == current for item in output)
