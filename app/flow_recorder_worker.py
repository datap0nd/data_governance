"""Headed worker operations for the supported Playwright codegen CLI."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
import threading
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app import flow_recording


@contextmanager
def browser_session(playwright, base_profile, channel, *, headed=True, timezone='UTC'):
    from app import flow_browser, flow_browser_state
    state = flow_browser_state.load(base_profile, channel)
    if state is None:
        # Seed once from the user's existing dedicated automation profile.
        # Downloads then use fresh contexts: repeated persistent-profile
        # downloads crash both installed Chromium browsers on our Windows ARM
        # fixture. No browser flags, policy changes or history edits are used.
        seed = flow_browser.launch(playwright, base_profile, channel, headed=headed)
        try:
            state = seed.storage_state(indexed_db=True)
        finally:
            seed.close()
    browser = playwright.chromium.launch(channel=channel, headless=not headed,
        downloads_path=str(base_profile / 'downloads'))
    context = browser.new_context(storage_state=state, accept_downloads=True,
        timezone_id=timezone, viewport={'width': 1440, 'height': 900})
    try:
        yield context, base_profile
    finally:
        try:
            if browser.is_connected():
                try:
                    flow_browser_state.save(base_profile, channel, context.storage_state(indexed_db=True))
                except Exception:
                    # A crashed/explicitly closed context must not mask the
                    # operation's result or replace the last usable state.
                    pass
        finally:
            browser.close()


@contextmanager
def reservation_heartbeat(server, worker_id, scan_id):
    import httpx
    from app.flow_worker import _api
    stop = threading.Event()
    def loop():
        with httpx.Client(base_url=server.rstrip('/')) as client:
            while not stop.wait(20):
                try:
                    _api(client, 'GET', f'/api/flows/worker/{worker_id}/recordings/{scan_id}/control')
                except Exception:
                    pass
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def authenticate(page, job, profile, progress, *, headed=True):
    from app import flow_worker, flow_gscm
    site = job['site']
    url = site.get('auth_url') or site.get('base_url') or job.get('report_url') or job.get('report', {}).get('url')
    progress('running', {'stage': 'authentication', 'message': 'Preparing the portal session. Complete sign-in if prompted.'})
    if site['adapter'] == 'gscm_portal':
        flow_worker._gscm_call(page, headed,
            lambda message: progress('running', {'stage': 'authentication', 'message': message}),
            lambda: flow_gscm.open_portal(page, url), profile)
    else:
        flow_worker._asap_goto(page, url, profile)


def record(client, worker_id, scan, page, context, profile, progress):
    from app import flow_worker
    from tzlocal import get_localzone_name
    job, scan_id = scan['job'], scan['id']
    authenticate(page, job, profile, progress)
    zone = get_localzone_name()
    private = profile / 'recordings' / str(scan_id)
    private.mkdir(parents=True, exist_ok=False)
    raw = private / 'codegen.py'
    # The worker retains its process/profile lock throughout the handoff.
    # The authentication browser must exit before codegen owns the profile.
    from app import flow_browser_state
    channel = job['browser_channel']
    state = context.storage_state(indexed_db=True)
    flow_browser_state.save(profile, channel, state)
    context.close()
    temporary = tempfile.TemporaryDirectory(prefix='codegen-auth-', dir=private)
    storage = Path(temporary.name) / 'state.json'
    try:
        flow_browser_state.protect_temporary_folder(Path(temporary.name))
        storage.write_text(json.dumps(state), encoding='utf-8')
        command = [sys.executable, '-m', 'playwright', 'codegen', '--target=python',
                   '--output', str(raw), '--load-storage', str(storage), '--save-storage', str(storage),
                   '--channel', channel, '--timezone', zone, '--viewport-size', '1440,900', job['report_url']]
        return _run_recorder(command, client, worker_id, scan_id, raw, private, zone, job, profile, channel, storage, progress)
    finally:
        temporary.cleanup()


def _run_recorder(command, client, worker_id, scan_id, raw, private, zone, job, profile, channel, storage, progress):
    from app import flow_worker, flow_browser_state
    progress('running', {'stage': 'recording', 'message': 'Record the report and its downloads in Playwright. Close the recording browser when finished.'})
    with (private / 'recorder.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        started = time.monotonic()
        try:
            while process.poll() is None:
                control = flow_worker._api(client, 'GET', f'/api/flows/worker/{worker_id}/recordings/{scan_id}/control')
                if control['status'] in {'cancelled', 'failed'}:
                    raise RuntimeError('Recording was cancelled or its worker reservation expired.')
                if time.monotonic() - started > 4 * 60 * 60:
                    raise RuntimeError('Recording exceeded four hours. Start a new recording.')
                time.sleep(2)
            if process.returncode != 0:
                raise RuntimeError('Playwright recording ended unexpectedly. Check the local recorder log and record again.')
            if not raw.is_file():
                raise RuntimeError('Playwright produced no recording. Click Record in the Inspector and try again.')
            definition = flow_recording.import_codegen(raw.read_text(encoding='utf-8'), timezone=zone)
            definition['adapter'] = job['site']['adapter']
            definition['browser_channel'] = channel
            flow_browser_state.save(profile, channel, json.loads(storage.read_text(encoding='utf-8')))
            if definition['adapter'] == 'gscm_portal':
                from app.flow_recording_nexacro import adapt_recording
                definition = adapt_recording(definition)
            return {'definition': definition}
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                else:
                    process.terminate()
                process.wait(timeout=15)


def validate(scan, page, profile, progress):
    from app.flow_recording_runtime import execute_recorded_flow
    job = copy.deepcopy(scan['job']['validation_job'])
    authenticate(page, job, profile, progress)
    # Validation uses private output but keeps the path contract under this
    # source's own folder. It never publishes production downloads or SQL.
    private = Path(job['paths']['flow_folder']) / '.recording-validation' / str(scan['id'])
    private.mkdir(parents=True, exist_ok=False)
    job['paths'] = {**job['paths'], 'enforced': False, 'flow_folder': None, 'artifact_store_root': None, 'scripts_folder': None}
    job['downloads'].update(target_folder=str(private), output_mode='run_folders')
    job['sql_handoff']['enabled'] = False
    state = {}
    try:
        page.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        execute_recorded_flow(page, job, lambda status, detail, artifacts=None, timings=None, **extra:
            progress('running', detail), profile, profile / 'downloads', run_id=scan['id'],
            register_folder=lambda _: {'ops': []}, headed=True, state=state)
        outputs = [{'step_id': item.get('export_view'), 'filename': item['filename'],
                    'checksum': item.get('checksum'), 'rows': item.get('row_count'),
                    'parameters': item.get('recording_parameters'), 'defaults': item.get('recording_defaults')}
                   for item in state['artifacts'] if item.get('status') == 'saved']
        return {'configuration_hash': scan['job']['configuration_hash'], 'outputs': outputs,
                'trace_path': str(private / 'trace.zip'), 'sql_executed': False,
                'engine_hash': job['recording']['engine_hash']}
    finally:
        page.context.tracing.stop(path=str(private / 'trace.zip'))
