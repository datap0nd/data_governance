import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import HTTPException

from app import database, flow_capacity, flow_parallel as parallel, flow_tasks, flow_worker as worker, flow_paths, flow_publish
from app.flow_parallel_worker import task_output_folder, assemble_bundle
from app.routers import flows, flow_tasks as routes
from test_flows import flow_db, _request, _seed_catalog, _mark_discovered, _flow


@pytest.fixture
def bundle(flow_db, tmp_path, monkeypatch, request):
    config = getattr(request, 'param', 'headless')
    mode = config if isinstance(config, str) else config.get('mode', 'headless')
    options = {} if isinstance(config, str) else config.get('flow', {})
    slots = options.get('download_parallelism', 3)
    monkeypatch.setattr(flows, 'launch_local_worker', lambda *a, **k: {'status':'test'})
    site, report = _seed_catalog(); _mark_discovered(report['id'])
    saved = flows.create_flow(_flow(site['id'], report['id'], target_folder=None,
        browser_mode=mode, filename_template='export_{index}_{week}.csv',
        **{'download_parallelism': 3, **options}), _request())
    queued = flows.queue_run(saved['id'], _request())
    with database.get_db() as db:
        flow_paths.save_setting(db, flow_capacity.CAPACITY_KEY, slots)
        flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, slots)
        job = flows._build_job(db, saved['id'])
    profile = tmp_path / 'coordinator'
    store = flow_publish.artifact_store_id(profile, store_root=Path(job['paths']['artifact_store_root']))
    for slot in range(1,6):
        register(f'worker-{slot}', store, pid=1000+slot, headed=mode == 'headed')
    run = flows.claim_run('worker-1')['run']
    folder = worker._prepare_run_folder(job, profile, run_id=run['id'],
        register_folder=lambda folder: flows.register_run_folder('worker-1', run['id'], flows.FolderRegister(run_folder=folder)),
        report_progress=lambda *a: None)
    state = routes.initialize('worker-1', run['id'], routes.Initialize(run_date=date.today()))
    return {'saved':saved, 'run':run, 'job':job, 'profile':profile, 'folder':folder, 'state':state, 'store':store, 'site':site}


def register(identity, store, *, pid=99, capable=True, headed=False, headed_capable=True):
    flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity,
        capabilities={'process_id':pid, 'adapters':['web_export','asap_portal'], 'shared_flow_artifacts':True,
                      'artifact_store_ids':[store], 'headed':headed,
                      flow_tasks.CAPABILITY:capable, flow_tasks.HEADED_CAPABILITY:headed_capable}))


def claim(identity, run_id=None):
    if run_id is not None:
        return routes.claim_own_task(identity, run_id)['task']
    return flows.claim_run(identity).get('task')


def complete(task, *, content='Code\nAbc\n'):
    job = task['job']
    target = task_output_folder(job, task, task['run_id'])
    spec = flow_tasks.task_matrix(job)[task['ordinal'] - 1]
    filename = worker._render_filename(job['downloads']['filename_template'], job, spec['period'], spec['ordinal'], spec['export_view'])
    path = target / filename; path.write_text(content)
    artifact = worker._decorate_artifact_storage({**worker._csv_metadata(path), 'filename':path.name, 'file_path':str(path),
        'status':'saved', 'period_key':spec['period'], 'export_view':spec['export_view'], 'bundle_index':spec['ordinal'], 'bundle_count':len(flow_tasks.task_matrix(job))}, job, Path('profile'))
    routes.task_progress(task['worker_id'], task['id'], routes.TaskReport(lease_token=task['lease_token'], status='succeeded', artifacts=[artifact]))
    return artifact


def complete_all(bundle):
    while True:
        task = claim('worker-1', bundle['run']['id'])
        if not task:
            break
        complete(task)
    return routes.finalizer('worker-1', bundle['run']['id'])


def test_matrix_preserves_views_links_period_order_and_serial_keys():
    job = {'site':{'adapter':'web_export'}, 'report':{'export_views':['A','B']}, 'downloads':{'periods':[['W1'],['W2']]}}
    matrix = flow_tasks.task_matrix(job)
    assert [(task['ordinal'],task['export_view'],task['period']) for task in matrix] == [(1,'A',['W1']),(2,'A',['W2']),(3,'B',['W1']),(4,'B',['W2'])]
    assert [task['key'] for task in matrix] == [worker._export_task_key(task['export_view'],task['period']) for task in matrix]
    job['site']['adapter']='asap_portal'; job['report']['download_links']=['Link one','Link two']
    assert [task['download_link'] for task in flow_tasks.task_matrix(job)] == ['Link one','Link one','Link two','Link two']


@pytest.mark.parametrize('bundle', ['headless', 'headed'], indirect=True)
def test_claim_race_counts_coordinator_tasks_and_global_limit(bundle):
    barrier=Barrier(4)
    def race(identity):
        barrier.wait(timeout=10)
        return claim(identity)
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks=[task for task in executor.map(race,['worker-2','worker-3','worker-4','worker-5']) if task]
    assert len(tasks)==2 and len({task['id'] for task in tasks})==2
    own=claim('worker-1',bundle['run']['id'])
    assert own and own['id'] not in {task['id'] for task in tasks}
    with database.get_db() as db:
        assert len(flow_capacity.assignments(db,bundle['job']['execution']['browser_mode']))==3
    idle=next(identity for identity in ['worker-2','worker-3','worker-4','worker-5'] if identity not in {task['worker_id'] for task in tasks})
    assert claim(idle) is None


def test_old_workers_cannot_claim_task_or_parallel_parent(bundle):
    register('old',bundle['store'],capable=False)
    assert flows.claim_run('old')['run'] is None and claim('old') is None
    # Another parallel parent is also capability gated, even with free capacity.
    with database.get_db() as db:
        db.execute("UPDATE flow_download_tasks SET state='cancelled' WHERE run_id=?",(bundle['run']['id'],))
    second=flows.create_flow(_flow(bundle['site']['id'],bundle['saved']['report_id'], name='Second parallel', target_folder=None, download_parallelism=2),_request())
    flows.queue_run(second['id'],_request())
    assert flows.claim_run('old')['run'] is None


@pytest.mark.parametrize('bundle', ['headless', 'headed'], indirect=True)
def test_helpers_cannot_cross_browser_modes(bundle):
    headed = bundle['job']['execution']['browser_mode'] == 'headed'
    register('other-mode', bundle['store'], headed=not headed)
    assert claim('other-mode') is None
    assert claim('worker-2')
    with database.get_db() as db:
        assert len(flow_capacity.assignments(db, 'headed' if headed else 'headless')) == 2
        assert not flow_capacity.assignments(db, 'headless' if headed else 'headed')


@pytest.mark.parametrize('bundle', ['headed'], indirect=True)
def test_headed_parent_and_helpers_require_new_protocol_capability(bundle):
    register('old-headed', bundle['store'], headed=True, headed_capable=False)
    assert claim('old-headed') is None
    second = flows.create_flow(_flow(bundle['site']['id'], bundle['saved']['report_id'],
        name='Visible parallel', target_folder=None, browser_mode='headed', download_parallelism=3), _request())
    queued = flows.queue_run(second['id'], _request())
    assert flows.claim_run('old-headed')['run'] is None
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_runs WHERE id=?', (queued['id'],)).fetchone()[0] == 'queued'


@pytest.mark.parametrize('bundle', ['headed'], indirect=True)
def test_headed_task_refuses_invisible_executor_before_download(bundle, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.flow_parallel_worker import execute_task
    app = FastAPI(); app.include_router(flows.router); app.include_router(routes.router)
    task = claim('worker-2')
    monkeypatch.setattr(worker, 'execute_job', lambda *a, **k: pytest.fail('Mismatched worker must not download'))
    with TestClient(app) as client:
        assert not execute_task(client, 'worker-2', task, None, bundle['profile'], None, headed=False)
    with database.get_db() as db:
        result = db.execute('SELECT state,error FROM flow_download_tasks WHERE id=?', (task['id'],)).fetchone()
        assert result['state'] == 'failed' and 'browser mode' in result['error']


def test_portal_and_flow_limits_hold_even_with_free_global_slots(bundle):
    with database.get_db() as db:
        flow_paths.save_setting(db,flow_capacity.CAPACITY_KEY,5)
        flow_paths.save_setting(db,f"flows_portal_capacity:{bundle['site']['id']}",2)
    assert claim('worker-2')
    assert claim('worker-3') is None
    assert claim('worker-1',bundle['run']['id'])
    with database.get_db() as db:
        flow_paths.save_setting(db,f"flows_portal_capacity:{bundle['site']['id']}",5)
    assert claim('worker-3')
    assert claim('worker-4') is None


def test_shared_limit_counts_other_mode_and_counts_coordinator_only_once(bundle):
    with database.get_db() as db:
        flow_paths.save_setting(db, flow_capacity.TOTAL_CAPACITY_KEY, 2)
        flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, 32)
        scan_id, _ = flows._queue_scan(db, bundle['site'], 'manual', 'Analyst')
        job = json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone()[0])
        job['execution']['browser_mode'] = 'headed'
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan_id))
    register('visible-scanner', bundle['store'], headed=True)
    assert flows.claim_run('visible-scanner')['scan']['id'] == scan_id
    assert claim('worker-2') is None
    own = claim('worker-1', bundle['run']['id'])
    assert own
    with database.get_db() as db:
        assert len(flow_capacity.assignments(db)) == 2
        db.execute("UPDATE flow_catalog_scans SET status='cancelled' WHERE id=?", (scan_id,))
    helper = claim('worker-2')
    assert helper
    with database.get_db() as db:
        assert len(flow_capacity.assignments(db)) == 2
        db.execute("UPDATE flow_download_tasks SET state='cancelling' WHERE id=?", (helper['id'],))
    assert claim('worker-3') is None  # a cancelling helper retains its reservation


def test_catalog_download_parallelism_accepts_32_but_recordings_remain_sequential():
    body = _flow(1, 1, name='Wide bundle', download_parallelism=32)
    assert body.download_parallelism == 32
    job = {'execution':{'download_parallelism':32}, 'flow':{'source_type':'portal'}}
    assert flow_tasks.parallelism(job) == 32
    job['flow']['execution_method'] = 'recorded'
    assert flow_tasks.parallelism(job) == 1
    with pytest.raises(ValueError):
        _flow(1, 1, name='Too many', download_parallelism=33)


def test_duplicate_and_stale_reports_cannot_change_committed_artifact(bundle):
    task=claim('worker-2'); artifact=complete(task)
    duplicate=routes.task_progress('worker-2',task['id'],routes.TaskReport(lease_token=task['lease_token'],status='failed',progress={'message':'late failure'}))
    assert duplicate['ignored'] and duplicate['state']=='succeeded'
    with pytest.raises(HTTPException):
        routes.task_progress('worker-3',task['id'],routes.TaskReport(lease_token='0'*32,status='succeeded',artifacts=[artifact]))
    assert routes.status('worker-1',bundle['run']['id'])['artifacts']==[artifact]


def test_finalizer_requires_all_tasks_and_owns_one_token(bundle):
    with pytest.raises(HTTPException):
        routes.finalizer('worker-1',bundle['run']['id'])
    with pytest.raises(HTTPException):
        flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='succeeded'))
    state=complete_all(bundle)
    assert routes.finalizer('worker-1',bundle['run']['id'])['finalizer_token']==state['finalizer_token']
    with pytest.raises(HTTPException):
        routes.finalizer('worker-2',bundle['run']['id'])
    with pytest.raises(HTTPException):
        flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='running',progress={'stage':'sql_insertion'},finalizer_token='stale'))
    artifacts=assemble_bundle(bundle['job'],state,bundle['profile'])
    assert [item['bundle_index'] for item in artifacts]==[1,2,3]
    assert all(Path(item['file_path']).parent==bundle['folder'] for item in artifacts)


def test_bundle_tampering_blocks_every_copy_and_downstream_step(bundle):
    state=complete_all(bundle)
    Path(state['artifacts'][-1]['file_path']).write_text('tampered')
    with pytest.raises(RuntimeError,match='changed|incomplete'):
        assemble_bundle(bundle['job'],state,bundle['profile'])
    assert not list(bundle['folder'].glob('*.csv'))


def test_task_paths_and_wrong_ordinal_are_rejected(bundle):
    task=claim('worker-2')
    wrong={**task,'output_folder':str(bundle['folder'].parent/'outside')}
    with pytest.raises(RuntimeError,match='does not match'):
        task_output_folder(task['job'],wrong,task['run_id'])
    artifact=complete(task)
    task2=claim('worker-3')
    with pytest.raises(HTTPException):
        routes.task_progress('worker-3',task2['id'],routes.TaskReport(lease_token=task2['lease_token'],status='succeeded',artifacts=[artifact]))


def test_lease_expiry_fences_late_success_and_drains_parent(bundle):
    task=claim('worker-2')
    with database.get_db() as db:
        db.execute("UPDATE flow_download_tasks SET lease_expires_at='2000-01-01' WHERE id=?",(task['id'],))
        parallel.reap(db)
        state=parallel.snapshot(db,bundle['run']['id'])
    assert state['state']=='aborting' and state['drained']
    result=routes.task_progress('worker-2',task['id'],routes.TaskReport(lease_token=task['lease_token'],status='succeeded',artifacts=[]))
    assert result['ignored'] and result['state']=='failed'
    assert not routes.status('worker-1',bundle['run']['id'])['artifacts']


def test_restart_fences_download_lease_without_replaying(bundle):
    task=claim('worker-2')
    register('worker-2',bundle['store'],pid=9999)
    state=routes.status('worker-1',bundle['run']['id'])
    assert state['state']=='aborting' and state['tasks'][0]['state']=='failed'
    assert claim('worker-2') is None


def test_unknown_sql_blocks_future_runs_until_operator_acknowledges(bundle):
    state=complete_all(bundle)
    token=state['finalizer_token']
    flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='running',progress={'stage':'sql_insertion'},finalizer_token=token))
    flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='failed',error='connection lost after commit',finalizer_token=token))
    with pytest.raises(HTTPException,match='Reconcile'):
        with database.get_db() as db:
            flows._build_job(db,bundle['saved']['id'])
    with database.get_db() as db:
        assert flows.inspect_sql_retry_eligibility(db,bundle['run']['id'])['reason_code']=='sql_reconciliation_required'
        assert flows.inspect_resume_eligibility(db,bundle['run']['id'])['reason_code']=='sql_reconciliation_required'
    flows.acknowledge_sql_reconciliation(bundle['saved']['id'],flows.SQLReconciled(acknowledged=True),_request())
    with database.get_db() as db:
        assert flows._build_job(db,bundle['saved']['id'])


def test_actual_task_download_keeps_full_index_and_never_finalizes(bundle,tmp_path,monkeypatch):
    first=claim('worker-1',bundle['run']['id'])
    task=claim('worker-2'); assert task['ordinal']==2
    job=task['job']; job['site']['adapter']='asap_portal'
    calls=[]
    monkeypatch.setattr(worker,'_asap_open_report',lambda *a: object())
    monkeypatch.setattr(worker,'_asap_activate_export_view',lambda page,frame,label:(frame,label))
    monkeypatch.setattr(worker,'_asap_apply_configuration',lambda *a:None)
    monkeypatch.setattr(worker,'_has_named_control',lambda *a:False)
    def download(page,frame,job,staging):
        path=tmp_path/'fake.csv'; path.write_text('Code,Qty\nAbc,3\n'); calls.append(path); return path,[]
    monkeypatch.setattr(worker,'_asap_download',download)
    monkeypatch.setattr(worker,'_run_transformations',lambda *a:pytest.fail('Task transformed data'))
    from app import flow_sql
    monkeypatch.setattr(flow_sql,'load_artifacts',lambda *a,**k:pytest.fail('Task reached SQL'))
    artifacts,timings=worker.execute_job(object(),job,lambda *a:None,bundle['profile'],tmp_path/'staging',
        run_id=task['run_id'],register_folder=lambda *a:pytest.fail('Task ran retention'),task_assignment=task)
    assert len(calls)==1 and artifacts[0]['bundle_index']==2 and artifacts[0]['bundle_count']==3
    assert Path(artifacts[0]['file_path']).name=='export_2_2026-W31.csv'


@pytest.mark.parametrize('bundle', ['headless', 'headed'], indirect=True)
def test_shared_runner_receives_full_bundle_before_transform_and_sql(bundle,monkeypatch):
    state=complete_all(bundle)
    job=bundle['job']; job['sql_handoff']['enabled']=True; job['transformation']['enabled']=True
    script=Path(job['paths']['flow_folder'])/'Scripts'/'fixture.py'; script.write_text('pass')
    job['transformation']['script_path']=str(script)
    events=[]
    original=worker._publish_direct_artifacts
    monkeypatch.setattr(worker,'_publish_direct_artifacts',lambda *a,**k: events.append('publish') or original(*a,**k))
    def transform(items,config):
        assert len(items)==3 and [item['bundle_index'] for item in items]==[1,2,3]
        events.append('transform'); return [{**item,'status':'transformed'} for item in items]
    monkeypatch.setattr(worker,'_run_transformations',transform)
    from app import flow_sql
    def sql(items,config,**kwargs):
        assert len(items)==3 and all(item['status']=='transformed' for item in items)
        events.append('sql'); return {'rows_written':3,'files_loaded':3,'target':'Db.Mixed.Table'}
    monkeypatch.setattr(flow_sql,'load_artifacts',sql)
    def acquire(*a,**kwargs):
        assert kwargs['headed'] == (job['execution']['browser_mode'] == 'headed')
        return assemble_bundle(job,state,bundle['profile']),[{'phase':'total','duration_ms':1,'status':'succeeded'}]
    worker.execute_flow(None,job,lambda *a,**k:None,bundle['profile'],run_id=bundle['run']['id'],register_folder=lambda *a:{},acquire_bundle=acquire,headed=job['execution']['browser_mode']=='headed')
    assert events==['publish','transform','sql']


def test_lagging_parent_progress_does_not_erase_helper_success(bundle):
    task=claim('worker-2'); artifact=complete(task)
    flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='running',artifacts=[]))
    with database.get_db() as db:
        assert json.loads(db.execute('SELECT artifact_json FROM flow_runs WHERE id=?',(bundle['run']['id'],)).fetchone()[0])==[artifact]


@pytest.mark.parametrize('bundle', ['headless', 'headed'], indirect=True)
def test_stop_drains_all_exact_workers_and_preserves_history(bundle,monkeypatch):
    from app import flow_local_runner
    saved=complete(claim('worker-2'))
    own=claim('worker-1',bundle['run']['id']); helper=claim('worker-3')
    stopped=[]
    def stop(mode,pid,*,worker_id):
        assert mode == bundle['job']['execution']['browser_mode']
        stopped.append((worker_id,pid))
        return {'status':'stopped'}
    monkeypatch.setattr(flow_local_runner,'stop_local_worker',stop)
    result=flows.stop_run(bundle['saved']['id'],_request())
    assert result['status']=='cancelled'
    assert set(stopped)=={('worker-1',1001),('worker-3',1003)}
    with database.get_db() as db:
        assert parallel.snapshot(db,bundle['run']['id'])['drained']
        assert db.execute('SELECT count(*) FROM flow_run_files WHERE run_id=?',(bundle['run']['id'],)).fetchone()[0]==1
        assert not flow_capacity.assignments(db,bundle['job']['execution']['browser_mode'])
    assert Path(saved['file_path']).is_file()
    for task in [own,helper]:
        assert routes.task_progress(task['worker_id'],task['id'],routes.TaskReport(lease_token=task['lease_token'],status='succeeded'))['ignored']


def test_unconfirmed_stop_keeps_reservations_until_fenced_leases_expire(bundle,monkeypatch):
    from app import flow_local_runner
    claim('worker-2'); claim('worker-1',bundle['run']['id'])
    monkeypatch.setattr(flow_local_runner,'stop_local_worker',lambda *a,**k:{'status':'unconfirmed'})
    result=flows.stop_run(bundle['saved']['id'],_request())
    assert result['status']=='running'
    with pytest.raises(HTTPException,match='active run'):
        flows.queue_run(bundle['saved']['id'],_request())
    assert flows.claim_run('worker-2')['stopping']
    with database.get_db() as db:
        db.execute("UPDATE flow_download_tasks SET lease_expires_at='2000-01-01' WHERE run_id=?",(bundle['run']['id'],))
        parallel.reap(db)
        assert db.execute('SELECT status FROM flow_runs WHERE id=?',(bundle['run']['id'],)).fetchone()[0]=='running'
        db.execute("UPDATE flow_run_fanout SET abort_deadline='2000-01-01' WHERE run_id=?",(bundle['run']['id'],))
        parallel.reap(db)
        assert db.execute('SELECT status FROM flow_runs WHERE id=?',(bundle['run']['id'],)).fetchone()[0]=='cancelled'


def test_late_cancel_ack_cannot_clear_a_new_stop_latch(bundle):
    task=claim('worker-2'); complete(task)
    register('worker-2',bundle['store'],pid=8888)
    with database.get_db() as db:
        db.execute('UPDATE flow_workers SET stop_requested_pid=8888 WHERE worker_id=?',('worker-2',))
    routes.task_progress('worker-2',task['id'],routes.TaskReport(lease_token=task['lease_token'],status='cancelled'))
    assert flows.claim_run('worker-2')['stopping']


def test_coordinator_restart_during_sql_is_fenced_and_never_replayed(bundle):
    state=complete_all(bundle)
    flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='running',progress={'stage':'sql_insertion'},finalizer_token=state['finalizer_token']))
    register('worker-1',bundle['store'],pid=9999)
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_runs WHERE id=?',(bundle['run']['id'],)).fetchone()[0]=='failed'
        assert db.execute('SELECT sql_reconciliation_required FROM flows WHERE id=?',(bundle['saved']['id'],)).fetchone()[0]==1
    assert flows.claim_run('worker-1')['run'] is None
    assert flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status='succeeded',finalizer_token=state['finalizer_token']))['ignored']


def test_resume_pins_source_and_only_carries_verified_successes(bundle):
    from app.flow_parallel_worker import _valid_carried
    artifact=complete(claim('worker-2'))
    with database.get_db() as db:
        parallel.abort(db,bundle['run']['id'],'fixture failure',coordinator_stopped=True)
        parallel.finish_aborted(db,bundle['run']['id'])
        assert flows.inspect_sql_retry_eligibility(db,bundle['run']['id'])['reason_code']=='download_bundle_incomplete'
    resumed=flows.resume_run(bundle['run']['id'],_request())
    with database.get_db() as db:
        job=json.loads(db.execute('SELECT job_json FROM flow_runs WHERE id=?',(resumed['id'],)).fetchone()[0])
        refs=db.execute('SELECT source_run_id FROM flow_run_source_refs WHERE consumer_run_id=?',(resumed['id'],)).fetchall()
    assert [row[0] for row in refs]==[bundle['run']['id']]
    assert len(_valid_carried(job))==1
    assert job['resume']['completed'][0]['checksum']==artifact['checksum']
    Path(artifact['file_path']).write_text('corrupt')
    assert not _valid_carried(job)


def test_portal_scan_and_task_claims_share_limit(bundle):
    with database.get_db() as db:
        flow_paths.save_setting(db,f"flows_portal_capacity:{bundle['site']['id']}",1)
        scan_id,_=flows._queue_scan(db,bundle['site'],'manual','Analyst')
    assert flows.claim_run('worker-2')['scan'] is None
    # The coordinator already occupies this portal slot and can do its own task.
    assert claim('worker-1',bundle['run']['id'])
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_catalog_scans WHERE id=?',(scan_id,)).fetchone()[0]=='queued'


@pytest.mark.parametrize('bundle', ['headless', 'headed'], indirect=True)
def test_coordinator_api_path_completes_download_only_tasks_and_finalizes(bundle,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.flow_parallel_worker import acquire_bundle
    app=FastAPI(); app.include_router(flows.router); app.include_router(routes.router)
    monkeypatch.setattr(worker,'_prepare_run_folder',lambda *a,**k:bundle['folder'])
    seen=[]
    def download(page,job,progress,profile,staging,*,artifacts,run_id,register_folder,headed,task_assignment):
        assert headed == (bundle['job']['execution']['browser_mode'] == 'headed')
        task=task_assignment
        target=task_output_folder(job,task,run_id)
        spec=flow_tasks.task_matrix(job)[task['ordinal']-1]
        path=target/worker._render_filename(job['downloads']['filename_template'],job,spec['period'],spec['ordinal'],spec['export_view'])
        path.write_text('Code\nA\n')
        progress('running',{'stage':'file_export'})
        seen.append(spec['ordinal'])
        return [{**worker._csv_metadata(path),'file_path':str(path),'filename':path.name,'status':'saved','period_key':spec['period'],'export_view':spec['export_view'],'bundle_index':spec['ordinal'],'bundle_count':3}],[]
    monkeypatch.setattr(worker,'execute_job',download)
    transport={}
    def progress(status,detail,artifacts=None):
        flows.update_run('worker-1',bundle['run']['id'],flows.WorkerProgress(status=status,progress=detail,artifacts=artifacts or [],finalizer_token=transport.get('finalizer_token')))
    with TestClient(app) as client:
        artifacts,timings=acquire_bundle(client,'worker-1',transport,None,bundle['job'],progress,bundle['profile'],None,artifacts=[],run_id=bundle['run']['id'],register_folder=lambda *a:{},headed=bundle['job']['execution']['browser_mode']=='headed')
    assert seen==[1,2,3] and len(artifacts)==3 and transport['finalizer_token']
    assert all(Path(item['file_path']).parent==bundle['folder'] for item in artifacts)


@pytest.mark.parametrize('direct',[False,True])
def test_original_deliverable_and_normalized_roles_survive_assembly(bundle,direct):
    state=complete_all(bundle)
    bundle['job']['downloads']['output_mode']='direct_replace' if direct else 'run_folders'
    item=state['artifacts'][0]; path=Path(item['file_path'])
    original=path.with_suffix('.xlsx'); original.write_bytes(b'fake workbook bytes')
    normalized=path.with_name(path.stem+'_normalized.csv'); path.rename(normalized)
    item.update(file_path=str(normalized),filename=normalized.name,
        original_file_path=str(original),original_filename=original.name,
        original_file_size=original.stat().st_size,original_checksum=flow_publish.read_size_checksum(original)['checksum'],
        deliverable_file_path=str(original),deliverable_filename=original.name,
        deliverable_file_size=original.stat().st_size,deliverable_checksum=flow_publish.read_size_checksum(original)['checksum'])
    result=assemble_bundle(bundle['job'],state,bundle['profile'])
    assert Path(result[0]['file_path']).suffix=='.csv'
    assert Path(result[0]['original_file_path']).suffix=='.xlsx'
    assert result[0]['deliverable_file_path']==result[0]['original_file_path' if direct else 'file_path']


@pytest.mark.parametrize('bundle', ['headless', 'headed'], indirect=True)
def test_three_actual_task_executors_can_download_concurrently(bundle,monkeypatch,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.flow_parallel_worker import execute_task
    app=FastAPI(); app.include_router(flows.router); app.include_router(routes.router)
    tasks=[claim('worker-1',bundle['run']['id']),claim('worker-2'),claim('worker-3')]
    barrier=Barrier(3)
    monkeypatch.setattr(worker,'_asap_open_report',lambda *a:object())
    monkeypatch.setattr(worker,'_asap_activate_export_view',lambda page,frame,label:(frame,label))
    monkeypatch.setattr(worker,'_asap_apply_configuration',lambda *a:None)
    monkeypatch.setattr(worker,'_has_named_control',lambda *a:False)
    def download(page,frame,job,staging):
        barrier.wait(timeout=10)
        staging.mkdir(parents=True,exist_ok=True)
        path=staging/'fake.csv'; path.write_text('Code,Quantity\nMixedCase,3\n'); return path,[]
    monkeypatch.setattr(worker,'_asap_download',download)
    def execute(task):
        task['job']['site']['adapter']='asap_portal'
        profile=tmp_path/task['worker_id']
        with TestClient(app) as client:
            return execute_task(client,task['worker_id'],task,object(),profile,profile/'staging',headed=bundle['job']['execution']['browser_mode']=='headed')
    with ThreadPoolExecutor(max_workers=3) as executor:
        outcomes=list(executor.map(execute,tasks))
        with database.get_db() as db:
            diagnostics=parallel.snapshot(db,bundle['run']['id'])
        assert all(outcomes), diagnostics
    state=routes.finalizer('worker-1',bundle['run']['id'])
    artifacts=assemble_bundle(bundle['job'],state,bundle['profile'])
    assert [item['bundle_index'] for item in artifacts]==[1,2,3]
    assert all('MixedCase' in Path(item['file_path']).read_text() for item in artifacts)


def test_parallel_settings_validate_persist_and_headed_is_parallel(bundle,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import system_flows
    monkeypatch.setattr(system_flows,'require_app_access',lambda request:None)
    app=FastAPI(); app.include_router(system_flows.router)
    url=f"/api/system/flows/portals/{bundle['site']['id']}"
    with TestClient(app) as client:
        for value in [0,33,True,2.5,'3']:
            assert client.put(url,json={'capacity':value}).status_code==422
        assert client.put(url,json={'capacity':2}).status_code==200
        state=client.get('/api/system/flows').json()
        assert next(item for item in state['portals'] if item['id']==bundle['site']['id'])['capacity']==2
    headed=_flow(bundle['site']['id'],bundle['saved']['report_id'],download_parallelism=5,browser_mode='headed')
    assert headed.download_parallelism==5
    job=copy.deepcopy(bundle['job']); job['job_type']='sql_retry'
    assert not flow_tasks.enabled(job)


def test_inline_browser_switch_keeps_parallelism_and_refreshes_standalone(bundle):
    saved = flows.create_flow(_flow(bundle['site']['id'], bundle['saved']['report_id'],
        name='Inline visible', target_folder=None, download_parallelism=3), _request())
    result = flows.patch_flow(saved['id'], flows.FlowInlineWrite(browser_mode='headed'), _request())
    generated = result['standalone']
    assert generated['state'] == 'current'
    import ast
    module = ast.parse(Path(generated['launcher']).read_text(encoding='utf-8'))
    assignment = next(node for node in module.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'FLOW' for target in node.targets))
    frozen = json.loads(ast.literal_eval(assignment.value.args[0]))
    assert frozen['execution']['browser_mode'] == 'headed'
    assert flow_tasks.parallelism(frozen) == 1  # standalone has no server task pool
    with database.get_db() as db:
        job = flows._build_job(db, saved['id'])
    assert job['execution']['browser_mode'] == 'headed' and flow_tasks.parallelism(job) == 3


def test_visible_worker_keeps_helpers_during_preparation_and_idles_after_download(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app import flow_parallel_worker
    clock = [0]
    launches, registrations, completed = [], [], []
    context = SimpleNamespace(pages=[object()], close=lambda: completed.append('closed'))
    def launch(profile, **options):
        launches.append((profile, options)); return context
    monkeypatch.setattr(worker, 'sync_playwright', lambda: nullcontext(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))))
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(worker.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def api(client, method, path, body=None):
        assert clock[0] <= 280, 'Worker failed to exit after the idle timeout'
        if path.endswith('/register'):
            registrations.append(body['capabilities'].copy())
            return {'headed_work_pending': clock[0] < 100}
        assert path.endswith('/claim')
        return {'task': {'id': 1, 'job': {'execution': {'browser_mode': 'headed', 'browser_channel': 'msedge'}}}} if clock[0] == 100 else {}
    def execute(client, identity, task, page, profile, staging, *, headed):
        assert headed and identity == 'bi-desktop-headed-2'
        completed.append(task['id']); clock[0] += 120
    monkeypatch.setattr(worker, '_api', api)
    monkeypatch.setattr(flow_parallel_worker, 'execute_task', execute)
    worker.run_worker('http://127.0.0.1:1', 'bi-desktop-headed-2', 'Visible 2', tmp_path/'profile-2', True, False, idle_exit_seconds=60)
    assert completed == [1, 'closed'] and clock[0] == 280
    assert launches[0][0] == str(tmp_path/'profile-2') and launches[0][1]['headless'] is False
    assert registrations[0][flow_tasks.HEADED_CAPABILITY] and registrations[0]['slot'] == 2


def test_deleting_paused_parallel_flow_removes_ledger_and_preserves_files(bundle):
    artifact=complete(claim('worker-2'))
    with database.get_db() as db:
        parallel.abort(db,bundle['run']['id'],'fixture',coordinator_stopped=True)
        parallel.finish_aborted(db,bundle['run']['id'])
    flows.set_flow_enabled(bundle['saved']['id'],flows.FlowEnabledWrite(enabled=False),_request())
    flows.delete_flow(bundle['saved']['id'],flows.FlowDeleteWrite(confirmation=bundle['saved']['name']),_request())
    with database.get_db() as db:
        assert not db.execute('SELECT * FROM flow_download_tasks').fetchall()
        assert not db.execute('SELECT * FROM flow_run_fanout').fetchall()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert Path(artifact['file_path']).is_file()


def test_stop_racing_task_initialization_cannot_leave_live_children(bundle,monkeypatch):
    from app import flow_local_runner
    with database.get_db() as db:
        db.execute('DELETE FROM flow_download_tasks WHERE run_id=?',(bundle['run']['id'],))
        db.execute('DELETE FROM flow_run_fanout WHERE run_id=?',(bundle['run']['id'],))
    monkeypatch.setattr(flow_local_runner,'stop_local_worker',lambda *a,**k:{'status':'stopped'})
    monkeypatch.setattr(flows,'stop_local_worker',lambda *a,**k:{'status':'stopped'})
    barrier=Barrier(2)
    def initialize():
        barrier.wait(timeout=10)
        try:
            routes.initialize('worker-1',bundle['run']['id'],routes.Initialize(run_date=date.today()))
        except HTTPException as exc:
            assert exc.status_code==409
    def stop():
        barrier.wait(timeout=10)
        return flows.stop_run(bundle['saved']['id'],_request())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=[executor.submit(initialize),executor.submit(stop)]
        for result in results:
            result.result(timeout=15)
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_runs WHERE id=?',(bundle['run']['id'],)).fetchone()[0]=='cancelled'
        assert not db.execute("SELECT * FROM flow_download_tasks WHERE state IN ('queued','claimed','cancelling')").fetchall()


def test_recovery_defers_owner_alert_until_commit_and_dispatches_once(bundle,monkeypatch):
    with database.get_db() as db:
        parallel.abort(db,bundle['run']['id'],'fixture',coordinator_stopped=True)
        parallel.reap(db)
    notified=[]
    def notify(run_id):
        with database.get_db() as db:
            assert db.execute('SELECT status FROM flow_runs WHERE id=?',(run_id,)).fetchone()[0]=='failed'
        notified.append(run_id)
    monkeypatch.setattr(flows,'notify_flow_owner_of_failure',notify)
    flows.fail_stale_runs(); flows.fail_stale_runs()
    assert notified==[bundle['run']['id']]


@pytest.mark.parametrize("offset", [-12, 0, 9])
def test_watchdog_reads_aware_parallel_heartbeats_as_instants(bundle, monkeypatch, offset):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    monkeypatch.setattr(flows, '_now', lambda: now)
    contact = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=offset))).isoformat()
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET last_seen_at=? WHERE worker_id='worker-1'", (contact,))
        db.execute("UPDATE flow_runs SET heartbeat_at=? WHERE id=?", (contact, bundle['run']['id']))
    assert flows.fail_stale_runs()['count'] == 0
    assert routes.status('worker-1', bundle['run']['id'])['state'] == 'downloading'


def test_parallel_task_heartbeat_uses_local_worker_time_and_keeps_coordinator_alive(bundle, monkeypatch):
    from datetime import datetime, timedelta
    local_now = datetime.now().replace(microsecond=0) + timedelta(hours=2)
    monkeypatch.setattr(flows, '_now', lambda: local_now)
    tasks = [claim('worker-1', bundle['run']['id']), claim('worker-2'), claim('worker-3')]
    for task in tasks:
        routes.task_progress(task['worker_id'], task['id'], routes.TaskReport(
            lease_token=task['lease_token'], status='running', progress={'stage':'task_heartbeat'}))
    with database.get_db() as db:
        for task in tasks:
            contact = db.execute('SELECT last_seen_at FROM flow_workers WHERE worker_id=?', (task['worker_id'],)).fetchone()[0]
            assert contact == flows._iso(local_now)
        assert db.execute('SELECT heartbeat_at FROM flow_runs WHERE id=?', (bundle['run']['id'],)).fetchone()[0] == flows._iso(local_now)
    monkeypatch.setattr(flows, '_now', lambda: local_now + timedelta(seconds=60))
    assert flows.fail_stale_runs()['count'] == 0
    assert routes.status('worker-1', bundle['run']['id'])['state'] == 'downloading'


def test_helpers_cannot_keep_a_dead_coordinator_alive(bundle):
    from datetime import timedelta
    task = claim('worker-2')
    old = flows._iso(flows._now() - timedelta(hours=1))
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET last_seen_at=? WHERE worker_id='worker-1'", (old,))
        db.execute('UPDATE flow_runs SET heartbeat_at=? WHERE id=?', (old, bundle['run']['id']))
    routes.task_progress('worker-2', task['id'], routes.TaskReport(
        lease_token=task['lease_token'], status='running', progress={'stage':'task_heartbeat'}))
    flows.fail_stale_runs()
    with database.get_db() as db:
        state = parallel.snapshot(db, bundle['run']['id'])
        assert state['state'] == 'aborting'
        assert 'Finalization was not replayed' in state['error']
        assert 'SQL' not in state['error']  # No SQL work had started.
