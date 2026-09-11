"""Real ASGI API + isolated SQLite; Chromium drives the shipped recording editor."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from app import database, flow_recording, flow_recordings, flow_recorder_worker
from app.routers import flows, flow_recordings as routes
from test_flows import flow_db, _request
from test_flow_recordings import CODEGEN, draft_job, definition, report_server


def client_for(monkeypatch):
    app=FastAPI()
    app.include_router(flows.router)
    app.include_router(routes.router)
    monkeypatch.setattr(routes,'_launch',lambda scan_id:{'scan_id':scan_id})
    return TestClient(app)


def complete(scan_id, result=None, status='succeeded'):
    with database.get_db() as db:
        row=db.execute('SELECT * FROM flow_catalog_scans WHERE id=?',(scan_id,)).fetchone()
        job=json.loads(row['job_json'])
        if result is None:
            outputs=[{'step_id':s['id'],'checksum':'a'*64} for s in flow_recording.walk_steps(job['validation_job']['recording']['definition']['steps']) if s['action']=='download']
            result={'configuration_hash':job['configuration_hash'],'engine_hash':job['validation_job']['recording']['engine_hash'],'outputs':outputs}
        flow_recordings.update_operation(db,row,'synthetic-worker',flows.ScanProgress(status=status,progress={},recording_result=result),datetime.now(timezone.utc).isoformat())


def test_raw_import_tests_directly_and_identical_save_is_preserved(flow_db,monkeypatch):
    saved,_=draft_job();client=client_for(monkeypatch);base=f"/api/flows/{saved['id']}/recordings"
    raw=flow_recording.import_codegen(CODEGEN)
    r=client.post(base+'/revisions',json={'definition':raw});assert r.status_code==200,r.text
    revision=r.json()['revision_id']
    assert client.post(base+'/revisions',json={'definition':raw}).json()['revision_id']==revision
    queued=client.post(f'{base}/revisions/{revision}/validate')
    assert queued.status_code==200,queued.text
    reopened=client.get(base).json();assert len(reopened['revisions'])==1
    assert reopened['revisions'][0]['definition']['steps']==raw['steps']
    assert not reopened['revisions'][0]['definition'].get('identity')
    assert not reopened['revisions'][0]['definition'].get('readiness')
    assert reopened['sessions'][0]['status']=='queued'


def test_pending_snapshot_atomic_apply_and_active_evidence(flow_db,monkeypatch):
    saved,job=draft_job();client=client_for(monkeypatch);fid=saved['id'];base=f'/api/flows/{fid}'
    revision=client.post(base+'/recordings/revisions',json={'definition':job['recording']['definition']}).json()['revision_id']
    pending=flows.FlowWrite.model_validate(saved).model_dump();pending.update(name='Pending name',filename_template='pending_{index}.xlsx',target_folder=None)
    # Match the real editor: no client-owned destination, even with enforcement.
    with database.get_db() as db:
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('flows_paths_enforced','1')")
    queued=client.post(f'{base}/recordings/revisions/{revision}/validate',json={'settings':pending});assert queued.status_code==200,queued.text
    with database.get_db() as db:
        frozen=json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?',(queued.json()['scan_id'],)).fetchone()[0])
        assert frozen['validation_job']['flow']['name']=='Pending name'
        assert db.execute('SELECT name FROM flows WHERE id=?',(fid,)).fetchone()[0]==saved['name']
    complete(queued.json()['scan_id']);pending['recording_revision_id']=revision
    # Settings that differ from the tested configuration save without a new test
    # and run with the current settings; the job only records that no matching
    # evidence exists.
    changed={**pending,'filename_template':'other_{index}.xlsx'}
    assert client.put(base,json=changed).status_code==200
    with database.get_db() as db:
        row=db.execute('SELECT filename_template,recording_revision_id FROM flows WHERE id=?',(fid,)).fetchone()
        assert row['filename_template']=='other_{index}.xlsx' and row['recording_revision_id']==revision
        untested=flows._build_job(db,fid)
        assert untested['recording']['revision']==revision and untested['recording']['tested'] is False
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()[0]=='validated'
    applied=client.put(base,json=pending);assert applied.status_code==200,applied.text
    with database.get_db() as db:
        assert flows._build_job(db,fid)['recording']['tested'] is True
    # Evidence from the previous hash format remains runnable after upgrading.
    with database.get_db() as db:
        active_job=flows._build_job(db,fid)
        db.execute('UPDATE flow_recording_revisions SET config_hash=? WHERE id=?',(flow_recordings.config_hash(active_job,legacy=True),revision))
        upgraded=flows._build_job(db,fid)
        assert upgraded['recording']['revision']==revision and upgraded['recording']['tested'] is True
    # Schedule-only changes preserve evidence.
    scheduled={**pending,'schedule_type':'daily','schedule_time':'08:00'}
    assert client.put(base,json=scheduled).status_code==200
    with database.get_db() as db:
        assert flows._build_job(db,fid)['recording']['tested'] is True
    assert client.post(base+'/recordings/revisions',json={'definition':job['recording']['definition']}).json()['revision_id']==revision
    retry=client.post(f'{base}/recordings/revisions/{revision}/validate',json={'settings':pending}).json()
    assert retry['revision_id']!=revision
    complete(retry['scan_id'],status='failed')
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()[0]=='validated'
        assert flows._build_job(db,fid)['recording']['revision']==revision


def test_untested_draft_saves_and_runs_without_confirmation(flow_db,monkeypatch):
    saved,job=draft_job();client=client_for(monkeypatch);fid=saved['id'];base=f'/api/flows/{fid}'
    revision=client.post(base+'/recordings/revisions',json={'definition':job['recording']['definition']}).json()['revision_id']
    pending=flows.FlowWrite.model_validate(saved).model_dump()
    pending.update(name='Saved untested',recording_revision_id=revision,target_folder=None)

    applied=client.put(base,json=pending)
    assert applied.status_code==200,applied.text
    with database.get_db() as db:
        row=db.execute('SELECT * FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()
        assert row['status']=='draft' and row['validated_at'] is None and row['config_hash'] is None
        runnable=flows._build_job(db,fid)
        assert runnable['recording']['revision']==revision and runnable['recording']['tested'] is False
        assert runnable['recording']['engine_hash'] is None
        assert runnable['flow']['name']=='Saved untested'
    # The retired waiver flag from older clients is accepted and ignored.
    assert client.put(base,json={**pending,'allow_untested_recording':True}).status_code==200
    # Enabling a schedule needs no test either.
    enabled={**pending,'enabled':True,'schedule_type':'daily','schedule_time':'08:00'}
    assert client.put(base,json=enabled).status_code==200
    with database.get_db() as db:
        assert db.execute('SELECT enabled FROM flows WHERE id=?',(fid,)).fetchone()[0]==1
    # Testing stays available; a failed test never removes the active draft.
    queued=client.post(f'{base}/recordings/revisions/{revision}/validate',json={'settings':pending})
    assert queued.status_code==200,queued.text
    assert queued.json()['revision_id']==revision
    complete(queued.json()['scan_id'],status='failed')
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()[0]=='draft'
        assert flows._build_job(db,fid)['recording']['revision']==revision


def test_save_rejects_non_runnable_recording_and_rolls_back(flow_db,monkeypatch):
    saved,job=draft_job();client=client_for(monkeypatch);fid=saved['id'];base=f'/api/flows/{fid}'
    invalid=job['recording']['definition']
    invalid['steps']=[step for step in invalid['steps'] if step['action']!='download']
    revision=client.post(base+'/recordings/revisions',json={'definition':invalid}).json()['revision_id']
    pending=flows.FlowWrite.model_validate(saved).model_dump()
    pending.update(name='Must roll back',recording_revision_id=revision,target_folder=None)

    rejected=client.put(base,json=pending)
    assert rejected.status_code==422,rejected.text
    with database.get_db() as db:
        flow=db.execute('SELECT name,recording_revision_id FROM flows WHERE id=?',(fid,)).fetchone()
        revision_row=db.execute('SELECT status,config_hash FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()
        assert flow['name']==saved['name'] and flow['recording_revision_id'] is None
        assert revision_row['status']=='draft' and revision_row['config_hash'] is None


def test_browser_real_api_save_test_return_apply(flow_db,monkeypatch,tmp_path,report_server):
    monkeypatch.setattr(flow_recorder_worker,'authenticate',lambda *a,**k:None)
    saved,_=draft_job(report_server);client=client_for(monkeypatch);fid=saved['id'];base=f'/api/flows/{fid}'
    raw=flow_recording.import_codegen(CODEGEN.replace('http://localhost/report',report_server).replace('    page.get_by_label("Start")', '    page.get_by_label("Start").click()\n    page.get_by_label("Start")',1))
    next(s for s in flow_recording.walk_steps(raw['steps']) if s['action']=='download')['output']['format']='csv'
    revision=client.post(base+'/recordings/revisions',json={'definition':raw}).json()['revision_id']
    root=Path(__file__).resolve().parents[1]
    with sync_playwright() as pw:
        # Validation workers launch their browser with this exact staging
        # directory. Keep the browser/API journey faithful to that contract so
        # recorded ASAP completion can observe the stable staged file.
        browser=pw.chromium.launch(
            channel='chrome', headless=True,
            downloads_path=str(tmp_path/'profile'/'downloads'),
        )
        page=browser.new_page(viewport={'width':1280,'height':900})
        page.route('http://recording.test/**',lambda route: route.fulfill(status=200,content_type='text/html',body='<div id="flow-workspace"><form id="flow-builder-form"><input id="pending-name" value="Untouched"></form></div>'))
        page.goto('http://recording.test/')
        def api_route(route):
            req=route.request
            response=client.request(req.method,req.url.split('recording.test')[1],content=req.post_data,headers={'content-type':'application/json'})
            route.fulfill(status=response.status_code,content_type='application/json',body=response.text)
        page.route('**/api/**',api_route)
        page.route('**/static/fonts/*',lambda route:route.fulfill(path=str(root/'app/static/fonts'/route.request.url.rsplit('/',1)[1]),content_type='font/ttf'))
        page.add_style_tag(path=str(root/'app/static/style.css'))
        page.evaluate('''()=>{window._flowsState={view:'builder'};window._flowRecordingSelections=new Map();window._flowAcceptRecording=(id,r)=>window.selectedRevision=r;
          window.api=async(path,options)=>{const r=await fetch(path,options),d=await r.json();if(!r.ok)throw Error(d.detail);return d;};
          window.apiPostJson=(path,body)=>api(path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
          window.apiPost=path=>apiPostJson(path,{});}''')
        for script in ('flow_recording_model.js','flow_recording_editor.js','flow_recordings.js'):page.add_script_tag(path=str(root/'app/static'/script))
        pending=flows.FlowWrite.model_validate(saved).model_dump()
        page.locator('#pending-name').fill('Kept while recording')
        page.evaluate('x=>FlowRecordings.open(x.id,x.settings)',{'id':fid,'settings':pending})
        if os.environ.get('METRONOME_RECORDING_EVIDENCE_DIR'):
            evidence=Path(os.environ['METRONOME_RECORDING_EVIDENCE_DIR']);evidence.mkdir(parents=True,exist_ok=True)
            for width,height,name in [(1440,1000,'desktop'),(1280,800,'laptop'),(390,844,'narrow')]:
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(evidence/f'{name}.png'),full_page=True)
            page.set_viewport_size({'width':1280,'height':900})
        page.get_by_role('button',name='Test recording',exact=True).click()
        page.wait_for_function("()=>document.querySelector('[data-test]').textContent==='Testing…'")
        assert not page.get_by_text('How do we know the report is ready?',exact=True).count()
        assert not page.get_by_text('Check the recorded page',exact=True).count()
        with database.get_db() as db:
            row=db.execute('SELECT * FROM flow_catalog_scans ORDER BY id DESC LIMIT 1').fetchone();scan_id=row['id'];job=json.loads(row['job_json'])
        assert not job['validation_job']['recording']['definition'].get('identity')
        assert not job['validation_job']['recording']['definition'].get('readiness')
        portal=browser.new_page()
        result=flow_recorder_worker.validate({'id':scan_id,'job':job},portal,tmp_path/'profile',lambda *args:None)
        complete(scan_id,result)
        page.get_by_text('Test passed. Back to Edit Flow, then Save.',exact=True).wait_for()
        page.get_by_role('button',name='Back to Edit Flow',exact=True).click()
        assert page.locator('#pending-name').input_value()=='Kept while recording'
        pending['recording_revision_id']=page.evaluate('selectedRevision')
        applied=client.put(base,json=pending);assert applied.status_code==200,applied.text
        assert result['sql_executed'] is False and result['outputs']
        with database.get_db() as db: assert flows._build_job(db,fid)['recording']['revision']==pending['recording_revision_id']
        browser.close()


def test_changed_transform_source_saves_and_runs_with_current_bytes(flow_db,monkeypatch,tmp_path):
    saved,job=draft_job();client=client_for(monkeypatch);fid=saved['id'];base=f'/api/flows/{fid}'
    revision=client.post(base+'/recordings/revisions',json={'definition':job['recording']['definition']}).json()['revision_id']
    script=tmp_path/'transform.py';script.write_text('# version one\n')
    pending=flows.FlowWrite.model_validate(saved).model_dump()
    pending.update(transform_enabled=True,transform_script_path=str(script))
    queued=client.post(f'{base}/recordings/revisions/{revision}/validate',json={'settings':pending});assert queued.status_code==200,queued.text
    complete(queued.json()['scan_id']);pending['recording_revision_id']=revision
    # A transformation edited after the test saves without a retest and runs
    # with the current bytes; only the evidence match is lost.
    script.write_text('# changed after test\n')
    result=client.put(base,json=pending);assert result.status_code==200,result.text
    saved_path=Path(result.json()['transform_script_path'])
    assert saved_path!=script and saved_path.read_text()=='# changed after test\n'
    with database.get_db() as db:
        current=flows._build_job(db,fid)
        assert current['recording']['transformation_source']=='# changed after test\n' and current['recording']['tested'] is False
    # Restoring the tested bytes restores the evidence match; the imported
    # location itself is not part of the evidence.
    pending['transform_script_path']=str(saved_path)
    saved_path.write_text('# version one\n')
    assert client.put(base,json=pending).status_code==200
    with database.get_db() as db:
        assert flows._build_job(db,fid)['recording']['tested'] is True
    saved_path.write_text('# version two\n')
    retry=client.post(f'{base}/recordings/revisions/{revision}/validate',json={'settings':pending})
    assert retry.status_code==200,retry.text
    assert retry.json()['revision_id']!=revision
