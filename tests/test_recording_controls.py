"""Recording lifecycle regressions without a live portal or user credentials."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from playwright.sync_api import sync_playwright

from app import database, flow_capacity, flow_local_runner, flow_recorder_worker as recorder, flow_recordings
from app.routers import flows, flow_recordings as routes
from test_flow_recordings import CODEGEN, definition, draft_job
from test_flows import flow_db, _request


def register(identity, *, controls=True):
    flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name=identity, capabilities={
        'headed': True, 'browser_switch_v1': True, 'flow_recorder_v1': True, 'recorded_flows_v2': True,
        'flow_recorder_controls_v1': controls, 'process_id': 123}))


def test_recording_launches_and_pins_one_slot_not_the_headed_pool(flow_db, monkeypatch):
    from app.flow_paths import save_setting
    launches = []
    monkeypatch.setattr(flow_local_runner, 'launch_local_worker',
        lambda mode, *, slot: launches.append((mode, slot)) or {'status': 'starting'})
    saved, job = draft_job()
    with database.get_db() as db:
        save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, '5')
    first = routes.start_recording(saved['id'], _request())
    another = routes.create_draft(routes.RecordingDraft(name='Second recording', site_id=job['site']['id']), _request())
    second = routes.start_recording(another['id'], _request())
    assert launches == [('headed', 1), ('headed', 2)]
    # An old worker cannot steal a recording requiring Finish/Cancel support.
    register(flow_capacity.worker_id(1, 'headed'), controls=False)
    assert flows.claim_run(flow_capacity.worker_id(1, 'headed'))['scan'] is None
    register(flow_capacity.worker_id(1, 'headed'))
    register(flow_capacity.worker_id(2, 'headed'))
    assert flows.claim_run(flow_capacity.worker_id(2, 'headed'))['scan']['id'] == second['scan_id']
    assert flows.claim_run(flow_capacity.worker_id(1, 'headed'))['scan']['id'] == first['scan_id']


def test_launch_failure_is_visible_and_does_not_leave_a_stuck_session(flow_db, monkeypatch):
    monkeypatch.setattr(flow_local_runner, 'launch_local_worker', lambda *a, **k: {'status': 'error', 'message': 'Task missing'})
    saved, _ = draft_job()
    routes.start_recording(saved['id'], _request())
    session = routes.list_recordings(saved['id'])['sessions'][0]
    assert session['status'] == 'failed' and session['error'] == 'Task missing'
    with database.get_db() as db:
        flow_recordings.assert_flow_idle(db, saved['id'])


def test_recording_reservation_does_not_start_an_unrelated_queued_run(flow_db, monkeypatch):
    monkeypatch.setattr(flow_local_runner, 'launch_local_worker', lambda *a, **k: {'status': 'starting'})
    saved, job = draft_job()
    unrelated = routes.create_draft(routes.RecordingDraft(name='Other work', site_id=job['site']['id']), _request())
    with database.get_db() as db:
        queued = db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','queued',?,'2000-01-01')",
            (unrelated['id'], json.dumps({'execution': {'browser_mode': 'headed'}, 'flow': {'source_type': 'file'}}))).lastrowid
    recording = routes.start_recording(saved['id'], _request())
    identity = flow_capacity.worker_id(1, 'headed')
    register(identity)
    claimed = flows.claim_run(identity)
    assert claimed['run'] is None and claimed['scan']['id'] == recording['scan_id']
    with database.get_db() as db:
        assert db.execute('SELECT status FROM flow_runs WHERE id=?', (queued,)).fetchone()[0] == 'queued'


def test_recording_worker_does_not_launch_a_spare_catalog_browser(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app import flow_worker, flow_browser
    context = SimpleNamespace(pages=[object()])
    statuses = []
    def api(client, method, path, body=None):
        if path.endswith('/register'): return {}
        if path.endswith('/claim'): return {'scan': {'id': 1, 'job': {
            'recording_operation': 'record', 'browser_channel': 'chrome'}}}
        statuses.append(body['status']); return {}
    monkeypatch.setattr(flow_worker, '_api', api)
    monkeypatch.setattr(flow_worker, 'sync_playwright', lambda: nullcontext(object()))
    monkeypatch.setattr(flow_browser, 'launch', lambda *a, **k: pytest.fail('Opened an unnecessary catalog browser'))
    monkeypatch.setattr(recorder, 'browser_session', lambda *a, **k: nullcontext((context, tmp_path)))
    monkeypatch.setattr(recorder, 'reservation_heartbeat', lambda *a, **k: nullcontext())
    monkeypatch.setattr(recorder, 'record', lambda *a, **k: {'definition': definition()})
    flow_worker.run_worker('http://localhost:1', 'recorder', 'Recorder', tmp_path, True, True)
    assert statuses == ['succeeded']


def recording_session(flow_db, monkeypatch):
    monkeypatch.setattr(routes, '_launch', lambda scan_id: {'scan_id': scan_id})
    saved, _ = draft_job()
    scan_id = routes.start_recording(saved['id'], _request())['scan_id']
    register('recorder')
    flows.claim_run('recorder')
    return saved['id'], scan_id


def test_finish_waits_for_recording_and_is_exposed_to_worker_and_review(flow_db, monkeypatch):
    flow_id, scan_id = recording_session(flow_db, monkeypatch)
    with pytest.raises(HTTPException, match='recording window'):
        routes.finish_recording(flow_id, scan_id)
    flows.update_scan('recorder', scan_id, flows.ScanProgress(status='running', progress={'stage': 'recording'}))
    routes.finish_recording(flow_id, scan_id)
    assert routes.recording_control('recorder', scan_id)['finish_requested']
    assert routes.list_recordings(flow_id)['sessions'][0]['finish_requested']
    assert json.loads(routes.list_recordings(flow_id)['sessions'][0]['progress_json'])['stage'] == 'finishing'


def test_cancel_keeps_reservation_until_ack_and_discards_racing_success(flow_db, monkeypatch):
    flow_id, scan_id = recording_session(flow_db, monkeypatch)
    monkeypatch.setattr(flows, 'stop_local_worker', lambda *a, **k: pytest.fail('Cooperative cancel killed the worker'))
    flows.update_scan('recorder', scan_id, flows.ScanProgress(status='running', progress={'stage': 'recording'}))
    assert routes.cancel_recording(flow_id, scan_id, _request())['status'] == 'cancelling'
    assert routes.recording_control('recorder', scan_id)['cancel_requested']
    with database.get_db() as db:
        assert len(flow_capacity.assignments(db)) == 1
    with pytest.raises(HTTPException, match='cancelled'):
        routes.finish_recording(flow_id, scan_id)
    flows.update_scan('recorder', scan_id, flows.ScanProgress(status='succeeded', recording_result={'definition': definition()}))
    data = routes.list_recordings(flow_id)
    assert data['sessions'][0]['status'] == 'cancelled' and data['revisions'] == []
    with database.get_db() as db:
        assert flow_capacity.assignments(db) == []


def test_unresponsive_recorder_can_be_force_closed_after_grace_period(flow_db, monkeypatch):
    flow_id, scan_id = recording_session(flow_db, monkeypatch)
    stopped = []
    monkeypatch.setattr(flows, 'stop_local_worker', lambda mode, pid, **kw: stopped.append((mode, pid, kw)) or {'status': 'stopped'})
    flows.update_scan('recorder', scan_id, flows.ScanProgress(status='running', progress={'stage': 'recording'}))
    routes.cancel_recording(flow_id, scan_id, _request())
    with database.get_db() as db:
        job = json.loads(db.execute('SELECT job_json FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone()[0])
        job['cancel_requested'] = (datetime.now(timezone.utc) - timedelta(seconds=11)).isoformat()
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan_id))
    routes.cancel_recording(flow_id, scan_id, _request())
    assert stopped == [('headed', 123, {'worker_id': 'recorder'})]
    assert routes.list_recordings(flow_id)['sessions'][0]['status'] == 'cancelled'


@pytest.mark.parametrize('control', ['finish_requested', 'cancel_requested', 'missing_download', 'crashed'])
def test_cli_finish_cancel_and_auth_cleanup(tmp_path, monkeypatch, control):
    from app import flow_worker, flow_browser_state
    events = []
    class Context:
        def storage_state(self, **kwargs): return {'cookies': [], 'origins': []}
        def close(self): events.append('auth_closed')
    class Process:
        def __init__(self, command, **kwargs):
            self.returncode = 1 if control == 'crashed' else None
            self.pid = 12345
            assert events == ['auth_closed']
            source = CODEGEN.replace('    context.close()', '    context.storage_state(path="private-auth.json")\n    context.close()')
            if control == 'missing_download':
                start = source.index('    with page.expect_download()')
                end = source.index('    context.storage_state(')
                source = source[:start] + source[end:]
            Path(command[command.index('--output') + 1]).write_text(source, encoding='utf-8')
        def poll(self): return self.returncode
    def close(process):
        if process.poll() is None:
            events.append('cli_closed')
            process.returncode = -1
    monkeypatch.setattr(recorder, 'authenticate', lambda *a, **k: None)
    monkeypatch.setattr(recorder, '_close_recorder', close)
    monkeypatch.setattr(recorder.subprocess, 'Popen', Process)
    monkeypatch.setattr(recorder.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(flow_browser_state, 'protect_temporary_folder', lambda path: None)
    monkeypatch.setattr(flow_worker, '_api', lambda *a, **k: {'status': 'running',
        'finish_requested': control in {'finish_requested', 'missing_download'}, 'cancel_requested': control == 'cancel_requested'})
    args = (None, 'worker', {'id': 12, 'job': {'site': {'adapter': 'asap_portal'}, 'browser_channel': 'chrome', 'report_url': 'http://localhost/report'}},
        None, Context(), tmp_path, lambda *a, **k: None)
    if control in {'finish_requested', 'missing_download'}:
        result = recorder.record(*args)
        assert any(step['action'] == 'download' for step in result['definition']['steps']) == (control == 'finish_requested')
        assert 'private-auth.json' not in json.dumps(result)
        if control == 'missing_download':
            from app import flow_recording
            with pytest.raises(ValueError, match='download'):
                flow_recording.validate_definition(result['definition'])
    else:
        error = recorder.RecordingCancelled if control == 'cancel_requested' else RuntimeError
        with pytest.raises(error): recorder.record(*args)
    assert events == ['auth_closed'] + ([] if control == 'crashed' else ['cli_closed'])
    assert not list(tmp_path.rglob('codegen-auth-*'))


def test_close_recorder_terminates_its_owned_process_tree(tmp_path):
    child_pid = tmp_path / 'child.pid'
    code = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)"
    process = subprocess.Popen([sys.executable, '-c', code, str(child_pid)],
        start_new_session=os.name != 'nt', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 10
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert child_pid.exists()
        recorder._close_recorder(process)
        assert process.poll() is not None
        pid = int(child_pid.read_text())
        if os.name == 'nt':
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
            if handle:
                try: assert ctypes.windll.kernel32.WaitForSingleObject(handle, 3000) == 0
                finally: ctypes.windll.kernel32.CloseHandle(handle)
        else:
            status = Path(f'/proc/{pid}/stat')
            # A terminated child may remain a zombie until the container's init reaps it.
            assert not status.exists() or status.read_text().split()[2] == 'Z'
    finally:
        recorder._close_recorder(process)


def test_review_controls_survive_polling_errors_and_finish_without_closing_chrome():
    root = Path(__file__).resolve().parents[1]
    session = {'scan_id': 3, 'operation': 'record', 'status': 'running', 'progress_json': '{"stage":"recording"}'}
    data = {'flow': {'name': 'Fixture'}, 'sessions': [session], 'revisions': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.set_content('<main>Recording fixture</main>')
        page.evaluate('''data => {window.data=data;window.calls=[];window.polls=0;
            window.api=async()=>{ if (++window.polls===2) throw new Error('temporary disconnect');return data;};
            window.apiPost=async path=>{calls.push(path);if(path.endsWith('/finish')){
                data.sessions[0].finish_requested=true;data.sessions[0].progress_json='{"stage":"finishing"}';
            } else {data.sessions[0].cancel_requested=new Date().toISOString();} return {};};}''', data)
        page.add_script_tag(path=str(root / 'app/static/flow_recordings.js'))
        page.evaluate('() => FlowRecordings.open(1)')
        page.evaluate('() => window.originalButton=document.querySelector("[data-finish]")')
        page.wait_for_function('() => window.polls>=3')
        assert page.evaluate('() => originalButton===document.querySelector("[data-finish]")')
        page.get_by_role('button', name='Finish recording', exact=True).click()
        assert page.locator('[data-finish]').is_disabled()
        page.locator('[data-cancel]').click()
        assert page.evaluate('() => calls') == ['/api/flows/1/recordings/3/finish', '/api/flows/1/recordings/3/cancel']
        assert page.locator('[data-cancel]').inner_text() == 'Cancelling…'
        browser.close()


def test_worker_icon_moves_and_respects_reduced_motion():
    root = Path(__file__).resolve().parents[1]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(reduced_motion='no-preference')
        page.set_content('<span class="flow-worker-emoji">🤖</span>')
        page.add_style_tag(path=str(root / 'app/static/style.css'))
        before = page.locator('span').evaluate('(el)=>getComputedStyle(el).transform')
        page.wait_for_function('(before)=>getComputedStyle(document.querySelector("span")).transform!==before', arg=before)
        page.emulate_media(reduced_motion='reduce')
        assert page.locator('span').evaluate('(el)=>getComputedStyle(el).animationName') == 'none'
        browser.close()
