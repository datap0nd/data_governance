import pytest
from app import flow_recording as model
from app import flow_recording_runtime as runtime
from test_flow_recordings import definition
from test_flows import flow_db


@pytest.mark.parametrize('seconds',[0,601,True,1.5,'5'])
def test_wait_rejects_invalid_duration(seconds):
    value=definition(); value['steps'].insert(1,dict(id='wait',action='wait',page='page',seconds=seconds))
    with pytest.raises(ValueError,match='seconds'):
        model.validate_definition(value)


def test_wait_roundtrip_and_v1_reader():
    value=definition(); value['version']=1
    model.validate_definition(value)
    value['steps'].insert(1,dict(id='wait',action='wait',page='page',seconds=5,label='Let the portal settle'))
    with pytest.raises(ValueError): model.validate_definition(value)
    value['version']=2
    assert model.validate_definition(value)['steps'][1]['label']=='Let the portal settle'


def test_page_dependency_rejects_use_before_popup_or_after_close():
    value=definition()
    popup=dict(id='popup',action='popup',page='page',result_page='page1',steps=[dict(id='trigger',action='click',page='page',locator=[dict(method='get_by_text',args=['Open'],kwargs={'exact':True})])])
    use=dict(id='use',action='goto',page='page1',args=['https://example.test'])
    value['steps'].extend([popup,use])
    model.validate_definition(value,activation=False)
    value['steps'][-2:]=[use,popup]
    with pytest.raises(ValueError,match='page'): model.validate_definition(value,activation=False)
    value['steps'][-2:]=[popup,dict(id='close',action='close',page='page1'),use]
    with pytest.raises(ValueError,match='page'): model.validate_definition(value,activation=False)


def test_review_preserves_explicit_assertions_without_inventing_page_checks():
    value=definition()
    locator=[dict(method='frame_locator',args=['iframe'],kwargs={}),dict(method='get_by_role',args=['heading'],kwargs={'name':'Sales Report','exact':True})]
    assertion=dict(id='title',action='assert',assertion='to_be_visible',page='page',locator=locator,args=[])
    value['steps'].insert(2,assertion)
    proposed=model.suggest_review(value)
    assert proposed['steps']==value['steps']
    assert not {'identity', 'identity_candidates', 'readiness'} & proposed.keys()
    assert value['identity']['text']=='Sales Report'


def test_wait_cancellation_stops_before_browser_interaction(tmp_path):
    value=definition(); value['steps'].insert(0,dict(id='wait',action='wait',page='page',seconds=600))
    job={'recording':{'definition':value,'revision':1},'recording_parameters':model.resolve_parameters(value)}
    events=[]
    def progress(status,detail,*args):
        events.append(detail)
        if detail['outcome']=='running': raise RuntimeError('Cancelled by user')
    with pytest.raises(RuntimeError,match='Cancelled'):
        runtime.acquire(object(),job,progress,tmp_path,tmp_path/'staging',target=tmp_path/'output',run_id=1,artifacts=[])
    assert [e['outcome'] for e in events]==['started','running','failed']


@pytest.mark.parametrize('operation',['run','record'])
def test_v1_only_worker_cannot_claim_v2_work(flow_db,monkeypatch,operation):
    import json
    from app import database
    from app.routers import flows, flow_recordings as routes
    from test_flow_recordings import draft_job
    from test_flows import _request
    saved,job=draft_job()
    if operation=='run':
        with database.get_db() as db:
            identifier=db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','queued',?,'2026-09-05')",(saved['id'],json.dumps(job))).lastrowid
    else:
        monkeypatch.setattr(routes,'_launch',lambda scan_id:{'scan_id':scan_id})
        identifier=routes.start_recording(saved['id'],_request())['scan_id']
    capabilities={'headed':True,'recorded_flows_v1':True,'browser_switch_v1':True,'shared_flow_artifacts':True,'flow_recorder_v1':True,'flow_recorder_controls_v1':True}
    flows.register_worker(flows.WorkerRegister(worker_id='older',display_name='Older',capabilities=capabilities))
    claimed=flows.claim_run('older')
    assert claimed.get('run') is None and claimed.get('scan') is None
    capabilities['recorded_flows_v2']=True
    flows.register_worker(flows.WorkerRegister(worker_id='current',display_name='Current',capabilities=capabilities))
    claimed=flows.claim_run('current')
    assert (claimed.get('run') or claimed.get('scan'))['id']==identifier


@pytest.mark.parametrize('terminal',['succeeded','failed','cancelled'])
def test_terminal_test_messages_preserve_visible_step_outcomes(flow_db,terminal):
    import json
    from app import database, flow_recordings, flow_tasks
    from app.routers import flows, flow_recordings as routes
    from test_flow_recordings import draft_job
    saved,job=draft_job()
    revision=routes.save_revision(saved['id'],routes.RevisionWrite(definition=job['recording']['definition']))['revision_id']
    with database.get_db() as db:
        scan=flow_recordings.queue_operation(db,saved['id'],'validate','test',revision_id=revision)
        row=db.execute('SELECT * FROM flow_catalog_scans WHERE id=?',(scan,)).fetchone()
        outcome={'outcome':'completed' if terminal=='succeeded' else 'failed' if terminal=='failed' else 'running','message':'Observed result'}
        flow_recordings.update_operation(db,row,'worker',flows.ScanProgress(status='running',progress={'stage':'recorded_action','step_outcomes':{'step':outcome}}),'2026-09-05T00:00:00Z')
        row=db.execute('SELECT * FROM flow_catalog_scans WHERE id=?',(scan,)).fetchone()
        frozen=json.loads(row['job_json'])
        result={'configuration_hash':frozen['configuration_hash'],'engine_hash':frozen['validation_job']['recording']['engine_hash'],
                'outputs':[{'step_id':t['export_view'],'period_key':t['period'],'checksum':'a'*64} for t in flow_tasks.task_matrix(frozen['validation_job'])]}
        flow_recordings.update_operation(db,row,'worker',flows.ScanProgress(status=terminal,progress={'stage':'complete'},recording_result=result),'2026-09-05T00:00:01Z')
        progress=json.loads(db.execute('SELECT progress_json FROM flow_catalog_scans WHERE id=?',(scan,)).fetchone()[0])
        assert progress['step_outcomes']['step']['outcome']==('cancelled' if terminal=='cancelled' else outcome['outcome'])



def test_validation_worker_checks_its_actual_execution_hash_before_authentication(monkeypatch,tmp_path):
    from app import flow_recorder_worker
    monkeypatch.setattr(flow_recorder_worker,'authenticate',lambda *args:pytest.fail('Authenticated before checking execution version'))
    with pytest.raises(RuntimeError,match='same execution version'):
        flow_recorder_worker.validate({'job':{'validation_job':{'recording':{'engine_hash':'obsolete'}}}},object(),tmp_path,lambda *args:None)


def test_validation_requires_worker_engine_check_capability(flow_db):
    from app import database, flow_recordings
    from app.routers import flows, flow_recordings as routes
    from test_flow_recordings import draft_job
    saved,job=draft_job()
    revision=routes.save_revision(saved['id'],routes.RevisionWrite(definition=job['recording']['definition']))['revision_id']
    with database.get_db() as db:
        scan=flow_recordings.queue_operation(db,saved['id'],'validate','test',revision_id=revision)
    caps={'headed':True,'recorded_flows_v2':True,'browser_switch_v1':True,'flow_recorder_v1':True,'flow_recorder_controls_v1':True}
    flows.register_worker(flows.WorkerRegister(worker_id='old-review',display_name='Old review',capabilities=caps))
    assert flows.claim_run('old-review').get('scan') is None
    caps['recorded_validation_engine_v1']=True
    flows.register_worker(flows.WorkerRegister(worker_id='current-review',display_name='Current review',capabilities=caps))
    assert flows.claim_run('current-review')['scan']['id']==scan
