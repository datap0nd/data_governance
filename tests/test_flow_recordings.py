import copy
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database, flow_browser, flow_portable, flow_recording, flow_recordings
from app.routers import flows, flow_recordings as routes
from test_flows import flow_db, _request


CODEGEN = '''import re
from playwright.sync_api import Playwright, sync_playwright, expect

def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="chrome", headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("http://localhost/report")
    page.get_by_label("Start").fill("2026-01-01")
    page.get_by_label("End").fill("2026-09-05")
    page.get_by_role("button", name="Generate").click()
    expect(page.locator("#status")).to_have_text("Ready")
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download").click()
    download = download_info.value
    context.close()
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
'''


def definition(url='http://localhost/report'):
    value = flow_recording.import_codegen(CODEGEN)
    steps = list(flow_recording.walk_steps(value['steps']))
    next(s for s in steps if s['action'] == 'goto')['args'] = [url]
    value['identity'] = {'target': {'page':'page', 'locator':[{'method':'get_by_text','args':['Sales Report'],'kwargs':{'exact':True}}]},'text':'Sales Report'}
    value['readiness'] = {'mode':'changed_text', 'trigger_step_id':next(s['id'] for s in steps if s['action'] == 'click'),
        'target': {'page':'page','locator':[{'method':'locator','args':['#status'],'kwargs':{}}]}}
    fills = [s for s in steps if s['action'] == 'fill']
    value['parameters'] = {'start':{'mode':'fixed','value':'2026-01-01','step_id':fills[0]['id'],'format':'%Y-%m-%d','not_after':'end'},
        'end':{'mode':'portal_default','step_id':fills[1]['id'],'format':'%Y-%m-%d'}}
    download = next(s for s in steps if s['action'] == 'download')
    download['output'] = {'format':'csv','headers':['Code','Period'],'allow_empty':False,
        'period_checks':[{'column':'Period','parameter':'start'}]}
    return value


def draft_job(url='http://localhost/report', adapter='asap_portal', *, wait_seconds=1):
    with database.get_db() as db:
        db.execute("DELETE FROM app_settings WHERE key='flows_browser_channel'")
    site = flows.create_site(flows.SiteWrite(name='Recorded Portal', adapter=adapter, base_url=url, auth_url=url),_request())
    saved = routes.create_draft(routes.RecordingDraft(name='Portable sales',site_id=site['id'],report_url=url),_request())
    with database.get_db() as db:
        job = flows._build_job(db,saved['id'],recording_draft=True)
    # Unrelated download/portable scenarios use the shortest supported pacing.
    # Default-ten and elapsed-time behavior have dedicated runtime coverage.
    job['execution']['recording_wait_seconds'] = wait_seconds
    job['recording'] = {'revision':1,'definition':definition(url),'transformation_source':None,
        'definition_hash':flow_recording.digest(definition(url)), 'engine_hash':flow_portable.execution_hash()}
    job['recording_parameters'] = flow_recording.resolve_parameters(job['recording']['definition'])
    return saved, job


def test_import_preserves_events_and_dates_without_running_source():
    value = definition()
    assert flow_recording.validate_definition(value) is value
    assert len([s for s in flow_recording.walk_steps(value['steps']) if s['action']=='download']) == 1
    assert flow_recording.resolve_parameters(value) == {'start':'2026-01-01','end':None}


@pytest.mark.parametrize('statement', ['__import__("os").system("anything")', 'page.evaluate("alert(1)")',
    'page.get_by_text("Delete").click(force=True)', 'page.mouse.click(100, 100)', 'exec("anything")'])
def test_unsafe_or_unsupported_recording_is_never_imported(statement):
    with pytest.raises(ValueError):
        flow_recording.import_codegen(CODEGEN.replace('    context.close()', '    '+statement+'\n    context.close()'))


def test_activation_needs_a_download_but_no_page_checks():
    value = flow_recording.import_codegen(CODEGEN)
    flow_recording.validate_definition(value)
    assert 'identity' not in value and 'readiness' not in value
    value = definition(); value.pop('readiness'); value.pop('identity')
    flow_recording.validate_definition(value)
    value = definition(); value['steps'] = [s for s in value['steps'] if s['action'] != 'download']
    flow_recording.validate_definition(value,activation=False)
    with pytest.raises(ValueError,match='download'): flow_recording.validate_definition(value)


def test_calculated_dates_use_flow_timezone_and_calendar_boundaries():
    value = definition(); value['timezone']='America/Los_Angeles'
    value['parameters']['start'].update(mode='calculated',expression='month_start')
    assert flow_recording.resolve_parameters(value,now=datetime(2026,1,1,0,1,tzinfo=timezone.utc))['start']=='2025-12-01'
    value['parameters']['start']['expression']='previous_month_end'
    assert flow_recording.resolve_parameters(value,now=datetime(2024,3,2,tzinfo=timezone.utc))['start']=='2024-02-29'
    with pytest.raises(ValueError): flow_recording.resolve_parameters(value,{'start':'2026-02-30'})


def test_nexacro_recording_never_activates_recycled_cells():
    from app.flow_recording_nexacro import adapt_recording
    value = definition(); value['adapter']='gscm_portal'
    click = next(s for s in flow_recording.walk_steps(value['steps']) if s['action']=='click')
    click['locator']=[{'method':'locator','args':['#mainframe\\.Setting0\\.form\\.grd\\.body\\.gridrow_299'],'kwargs':{}}]
    value = adapt_recording(value)
    with pytest.raises(ValueError,match='recycled'): flow_recording.validate_definition(value)
    click = next(s for s in flow_recording.walk_steps(value['steps']) if s['action']=='click')
    del click['repair_reason']
    with pytest.raises(ValueError,match='virtual grid'): flow_recording.validate_definition(value)


def test_portable_script_dry_run_has_no_metronome_import_or_adjacent_configuration(flow_db,tmp_path):
    _, job = draft_job()
    generated = flow_portable.generate(job)
    standalone = tmp_path/'only-file.py'
    standalone.write_text(Path(generated['launcher']).read_text(encoding='utf-8'),encoding='utf-8')
    result = subprocess.run([sys.executable,'-I',str(standalone),'--dry-run'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['parameters'] == {'start':'2026-01-01','end':None}
    from app.flow_standalone import status
    assert status(job)['state']=='current'
    changed=copy.deepcopy(job); changed['execution']['browser_channel']='msedge'
    assert status(changed)['state']=='stale'
    assert flow_portable.generate(job)['launcher_hash']==generated['launcher_hash']
    Path(generated['launcher']).write_text('# user changes\n',encoding='utf-8')
    with pytest.raises(ValueError,match='modified'): flow_portable.generate(job)
    assert Path(generated['script_revision']).is_file()


@pytest.fixture()
def report_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/export'):
                content = b'Code,Period\nA,2026-01-01\n'
                self.send_response(200); self.send_header('Content-Type','text/csv')
                self.send_header('Content-Disposition','attachment; filename="report.csv"'); self.end_headers(); self.wfile.write(content)
            else:
                content = b'''<h1>Sales Report</h1><label>Start<input id="start" value="2020-01-01"></label><label>End<input value="2026-09-05"></label><span id="status">Idle</span><button onclick="setTimeout(()=>document.querySelector('#status').textContent='Ready',30)">Generate</button><button onclick="window.location='/export'">Download</button>'''
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(content)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/report'
    finally: server.shutdown();thread.join(timeout=2);server.server_close()


@pytest.mark.parametrize('channel',['chrome','msedge'])
def test_same_portable_pipeline_runs_real_download_on_both_browsers(flow_db,tmp_path,report_server,channel):
    _,job = draft_job(report_server)
    job['execution']['browser_channel']=channel
    job['recording']['definition'].pop('identity')
    job['recording']['definition'].pop('readiness')
    # A real embedded Python transformation has no adjacent dependency.
    job['transformation']['enabled']=True
    job['recording']['transformation_source']='''import argparse, pathlib
p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output');a=p.parse_args()
pathlib.Path(a.output).write_text(pathlib.Path(a.input).read_text().replace('A,','B,'))
'''
    file=tmp_path/'portable.py';file.write_text(flow_portable.source(job),encoding='utf-8')
    root=tmp_path/'portable-output'
    result=subprocess.run([sys.executable,'-I',str(file),'--headless','--output-root',str(root)],
        cwd=tmp_path,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stderr
    files=list(root.rglob('*.csv'))
    assert any('B,2026-01-01' in p.read_text(encoding='utf-8-sig') for p in files)
    logs=list(root.rglob('*.jsonl')); assert logs
    events=[json.loads(line) for line in logs[0].read_text().splitlines()]
    assert events[-1]['status']=='succeeded'
    artifact=next(a for a in events[-1]['artifacts'] if a.get('recording_defaults'))
    assert artifact['recording_defaults']=={'end':'2026-09-05'}
    repeated=subprocess.run([sys.executable,'-I',str(file),'--headless','--output-root',str(root)],
        cwd=tmp_path,capture_output=True,text=True,timeout=90)
    assert repeated.returncode==0,repeated.stderr


def test_browser_global_choice_is_frozen_and_does_not_rewrite_flows(flow_db):
    from app.flow_paths import save_setting
    _,job=draft_job()
    with database.get_db() as db:
        assert flow_browser.configured(db)=='chrome'
        save_setting(db,flow_browser.SETTING,'msedge')
        changed=flows._build_job(db,job['flow']['id'],recording_draft=True)
    assert job['execution']['browser_channel']=='chrome'
    assert changed['execution']['browser_channel']=='msedge'
    assert flow_browser.profile_for(Path('profile'),'chrome') != flow_browser.profile_for(Path('profile'),'msedge')
    assert not flow_browser.can_claim(job,{})
    assert flow_browser.can_claim(job,{flow_browser.CAPABILITY:True})


def test_revision_validation_and_activation_freezes_configuration(flow_db,monkeypatch):
    _,job=draft_job()
    flow_id=job['flow']['id']
    revision=routes.save_revision(flow_id,routes.RevisionWrite(definition=job['recording']['definition']))['revision_id']
    with database.get_db() as db:
        scan_id=flow_recordings.queue_operation(db,flow_id,'validate','test',revision_id=revision)
        row=db.execute('SELECT * FROM flow_catalog_scans WHERE id=?',(scan_id,)).fetchone()
        frozen=json.loads(row['job_json'])
        output=next(s for s in flow_recording.walk_steps(job['recording']['definition']['steps']) if s['action']=='download')
        flow_recordings.update_operation(db,row,'test-worker',flows.ScanProgress(status='succeeded',progress={},recording_result={
            'configuration_hash':frozen['configuration_hash'],'engine_hash':frozen['validation_job']['recording']['engine_hash'],
            'outputs':[{'step_id':output['id'],'checksum':'a'*64}]}),'2026-09-05T00:00:00+00:00')
    result=routes.activate_revision(flow_id,revision)
    assert result['standalone']['kind']=='portable_recorded'
    with database.get_db() as db:
        queued=flows._build_job(db,flow_id)
        assert queued['recording']['revision']==revision
        assert queued['downloads']['network_replay'] is False
        db.execute("UPDATE flows SET filename_template='changed_{index}.csv' WHERE id=?",(flow_id,))
        with pytest.raises(HTTPException,match='validate'): flows._build_job(db,flow_id)


def test_recording_uses_capacity_and_requires_capable_visible_worker(flow_db,monkeypatch):
    monkeypatch.setattr(routes,'_launch',lambda scan_id:{'scan_id':scan_id})
    saved,job=draft_job()
    started=routes.start_recording(saved['id'],_request())
    def register(identity,recorder=False):
        flows.register_worker(flows.WorkerRegister(worker_id=identity,display_name=identity,
            capabilities={'headed':True,'browser_switch_v1':True,'flow_recorder_v1':recorder,'recorded_flows_v2':recorder,'recorded_validation_engine_v1':recorder,'flow_recorder_controls_v1':recorder,'process_id':123}))
    register('old')
    assert flows.claim_run('old')['scan'] is None
    register('new',True)
    assert flows.claim_run('new')['scan']['id']==started['scan_id']
    with database.get_db() as db:
        with pytest.raises(HTTPException,match='recording'): flow_recordings.assert_flow_idle(db,saved['id'])
    assert routes.recording_control('new',started['scan_id'])['status']=='claimed'


def test_cancellation_preserves_catalog_status_and_fences_late_worker(flow_db,monkeypatch):
    monkeypatch.setattr(routes,'_launch',lambda scan_id:{'scan_id':scan_id})
    monkeypatch.setattr(flows,'stop_local_worker',lambda *a,**k:{'status':'stopped'})
    saved,job=draft_job()
    started=routes.start_recording(saved['id'],_request())
    flows.register_worker(flows.WorkerRegister(worker_id='new',display_name='new',capabilities={
        'headed':True,'browser_switch_v1':True,'flow_recorder_v1':True,'recorded_flows_v2':True,'recorded_validation_engine_v1':True,'flow_recorder_controls_v1':True,'process_id':123}))
    flows.claim_run('new')
    with database.get_db() as db:
        before=dict(db.execute('SELECT * FROM flow_sites WHERE id=?',(job['site']['id'],)).fetchone())
    routes.cancel_recording(saved['id'],started['scan_id'],_request())
    with database.get_db() as db:
        after=dict(db.execute('SELECT * FROM flow_sites WHERE id=?',(job['site']['id'],)).fetchone())
        assert before==after
        assert db.execute('SELECT stop_requested_pid FROM flow_workers WHERE worker_id="new"').fetchone()[0]==123
    assert flows.claim_run('new')['stopping']


def test_recorded_unknown_sql_commit_blocks_new_execution(flow_db):
    saved,job=draft_job()
    job['sql_handoff']['enabled']=True
    with database.get_db() as db:
        run=db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','running',?,'2026-09-05')",(saved['id'],json.dumps(job))).lastrowid
        db.execute("INSERT INTO flow_run_events(run_id,status,stage,created_at) VALUES (?,'running','sql_insertion','2026-09-05')",(run,))
        db.execute("UPDATE flow_runs SET status='failed' WHERE id=?",(run,))
        assert db.execute('SELECT sql_reconciliation_required FROM flows WHERE id=?',(saved['id'],)).fetchone()[0]==1
        with pytest.raises(HTTPException,match='SQL'): flows._build_job(db,saved['id'])


def test_ui_can_review_gscm_without_code_and_keeps_portal_default_date():
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]
    data={'flow':{'name':'GSCM sales','source_adapter':'gscm_portal'},'sessions':[],
        'revisions':[{'id':1,'status':'draft','definition':definition()}]}
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.set_content('<main>Fixture</main>')
        page.evaluate('''data => { window.saved=[]; window.api=async()=>data;
            window.apiPostJson=async(url,body)=>{window.saved.push({url,body});return {revision_id:1};}; window.apiPost=async()=>({}); }''',data)
        for script in ('flow_recording_model.js','flow_recording_editor.js'):
            page.add_script_tag(path=str(root / 'app/static' / script))
        page.add_script_tag(path=str(root/'app/static/flow_recordings.js'))
        page.evaluate('() => FlowRecordings.open(1)')
        assert page.locator('.flow-recording-page').is_visible()
        page.get_by_role('button',name='Enter value in “End”',exact=True).click()
        page.locator('[data-date-mode]').select_option('portal_default')
        page.get_by_role('button',name='Save draft',exact=True).click()
        value=page.evaluate('() => saved[0].body.definition')
        assert value['parameters']['end']['mode']=='portal_default'
        flow_recording.validate_definition(value)
        browser.close()


@pytest.mark.parametrize('change',[
    lambda d: d.update(steps=[None]),
    lambda d: d['parameters']['start'].update(step_id='absent',target={'page':'page','locator':[]}),
    lambda d: d['steps'][2].update(page='unknown'),
    lambda d: d['steps'][2].update(kwargs={'timeout':0}),
    lambda d: next(s for s in flow_recording.walk_steps(d['steps']) if s['action']=='download')['output'].update(period_checks=[{'column':'Date','parameter':'absent'}]),
])
def test_malformed_recordings_are_rejected(change):
    value=definition();change(value)
    with pytest.raises(ValueError): flow_recording.validate_definition(value)


def test_worker_restart_expires_recording_and_late_results_cannot_activate(flow_db,monkeypatch):
    monkeypatch.setattr(routes,'_launch',lambda scan_id:{'scan_id':scan_id})
    saved,job=draft_job()
    started=routes.start_recording(saved['id'],_request())
    flows.register_worker(flows.WorkerRegister(worker_id='new',display_name='new',capabilities={
        'headed':True,'browser_switch_v1':True,'flow_recorder_v1':True,'recorded_flows_v2':True,'recorded_validation_engine_v1':True,'flow_recorder_controls_v1':True,'process_id':123}))
    flows.claim_run('new')
    with database.get_db() as db:
        flow_recordings.reap(db,restarted_worker='new')
    response=flows.update_scan('new',started['scan_id'],flows.ScanProgress(status='succeeded',recording_result={'definition':definition()}))
    assert response['ignored'] and response['status']=='failed'
    assert routes.list_recordings(saved['id'])['revisions']==[]


def test_recorder_uses_public_cli_and_closes_auth_context_before_profile_handoff(flow_db,tmp_path,monkeypatch):
    from app import flow_recorder_worker as worker, flow_browser_state
    _,job=draft_job()
    sequence=[]
    class Context:
        def close(self): sequence.append('closed')
        def storage_state(self, **kwargs): return {'cookies':[], 'origins':[]}
    class Process:
        returncode=0
        def __init__(self,command,**kwargs):
            assert sequence==['authenticated','closed']
            assert command[1:4]==['-m','playwright','codegen']
            assert command[command.index('--channel')+1]=='chrome'
            assert '--user-data-dir' not in command
            assert json.loads(Path(command[command.index('--load-storage')+1]).read_text(encoding='utf-8')) == {'cookies':[], 'origins':[]}
            Path(command[command.index('--output')+1]).write_text(CODEGEN,encoding='utf-8')
        def poll(self):return 0
    monkeypatch.setattr(worker,'authenticate',lambda *a,**k:sequence.append('authenticated'))
    monkeypatch.setattr(flow_browser_state,'protect_temporary_folder',lambda path:None)
    monkeypatch.setattr(worker.subprocess,'Popen',Process)
    result=worker.record(None,'worker',{'id':5,'job':{'browser_channel':'chrome','site':job['site'],'report_url':'http://localhost/report'}},
        None,Context(),tmp_path,lambda *a,**k:None)
    assert result['definition']['steps']


def test_recorded_authentication_state_is_protected_and_separate_per_browser(tmp_path):
    from app import flow_browser_state
    state={'cookies':[{'name':'fixture', 'value':'test-session-only'}], 'origins':[]}
    flow_browser_state.save(tmp_path,'chrome',state)
    assert flow_browser_state.load(tmp_path,'chrome')==state
    assert flow_browser_state.load(tmp_path,'msedge') is None
    if os.name=='nt':
        assert 'test-session-only' not in flow_browser_state.state_path(tmp_path,'chrome').read_text(encoding='utf-8')
    private=tmp_path/'private';private.mkdir()
    flow_browser_state.protect_temporary_folder(private)
    (private/'fixture.txt').write_text('test',encoding='utf-8')


def test_recorded_sql_retry_rejects_a_partial_bundle(flow_db):
    saved,job=draft_job()
    job['sql_handoff']['enabled']=True
    output=next(s for s in flow_recording.walk_steps(job['recording']['definition']['steps']) if s['action']=='download')
    missing=copy.deepcopy(output);missing['id']='missing';missing['steps'][0]['id']='missing-trigger'
    job['recording']['definition']['steps'].append(missing)
    with database.get_db() as db:
        run=db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,artifact_json,created_at) VALUES (?,'manual','failed',?,?,'2026-09-05')",
            (saved['id'],json.dumps(job),json.dumps([{'status':'saved','export_view':output['id'],'file_path':'report.csv','filename':'report.csv'}]))).lastrowid
        result=flows.inspect_sql_retry_eligibility(db,run)
    assert result['reason_code']=='download_bundle_incomplete'


@pytest.mark.parametrize('aware',[False,True])
def test_recording_lease_expiry_accepts_utc_and_offset_timestamps(flow_db,monkeypatch,aware):
    from datetime import timedelta
    monkeypatch.setattr(routes,'_launch',lambda scan_id:{'scan_id':scan_id})
    saved,job=draft_job()
    started=routes.start_recording(saved['id'],_request())
    flows.register_worker(flows.WorkerRegister(worker_id='lease',display_name='lease',capabilities={
        'headed':True,'browser_switch_v1':True,'flow_recorder_v1':True,'recorded_flows_v2':True,'recorded_validation_engine_v1':True,'flow_recorder_controls_v1':True,'process_id':123}))
    flows.claim_run('lease')
    stamp=(datetime.now(timezone(timedelta(hours=5))) if aware else datetime.now(timezone.utc).replace(tzinfo=None))-timedelta(minutes=4)
    with database.get_db() as db:
        db.execute('UPDATE flow_catalog_scans SET heartbeat_at=? WHERE id=?',(stamp.isoformat(),started['scan_id']))
        flow_recordings.reap(db)
        assert db.execute('SELECT status FROM flow_catalog_scans WHERE id=?',(started['scan_id'],)).fetchone()[0]=='failed'
