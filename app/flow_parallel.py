"""Durable download leases and a single finalizer for one parent Flow run.

All mutating helpers run inside the caller's BEGIN IMMEDIATE transaction.
Tasks acquire data only. The coordinator retains the normal Flow reservations
and process locks until the complete bundle has been finalized or drained.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from app import flow_capacity, flow_paths, flow_tasks
from app.flow_limits import MAX_SLOTS, DEFAULT_PORTAL_CAPACITY

LEASE_SECONDS = 90
TASK_ACTIVE = ('claimed', 'cancelling')
TASK_TERMINAL = ('succeeded', 'failed', 'cancelled')


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_timestamp() -> str:
    """Shared Flow tables use local wall time; lease tables use aware UTC."""
    from app.routers import flows
    return flows._iso(flows._now())


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _job(row):
    return json.loads(row['job_json'])


def _fanout(db, run_id):
    return db.execute('SELECT * FROM flow_run_fanout WHERE run_id=?', (run_id,)).fetchone()


def _owner(db, worker_id, run_id):
    run = db.execute("SELECT * FROM flow_runs WHERE id=? AND worker_id=? AND status IN ('claimed','running')", (run_id, worker_id)).fetchone()
    if not run:
        raise HTTPException(409, 'This coordinator no longer owns the active run.')
    return run


def _event(db, run_id, stage, message, details=None):
    db.execute("INSERT INTO flow_run_events(run_id,status,stage,message,details_json,created_at) VALUES (?,?,?,?,?,?)", (run_id, stage if stage in TASK_TERMINAL else 'running', stage, message, _json(details or {}), local_timestamp()))


def portal_limit(db, site_id) -> int:
    try:
        return max(1, min(MAX_SLOTS, int(flow_paths.setting(db, f'flows_portal_capacity:{site_id}', str(DEFAULT_PORTAL_CAPACITY)))))
    except (ValueError, TypeError):
        return DEFAULT_PORTAL_CAPACITY


def portal_available(db, job, *, existing_worker=None) -> bool:
    if job.get('site', {}).get('adapter') not in {'web_export', 'asap_portal', 'gscm_portal'}:
        return True
    site_id = job.get('site', {}).get('id')
    occupied = set()
    for row in db.execute("""SELECT 'run' AS kind,id,worker_id,job_json FROM flow_runs WHERE status IN ('claimed','running')
        UNION ALL SELECT 'scan',id,worker_id,job_json FROM flow_catalog_scans WHERE status IN ('claimed','running')"""):
        if _job(row).get('site', {}).get('id') == site_id:
            occupied.add(row['worker_id'] or f"{row['kind']}:{row['id']}")
    for row in db.execute("""SELECT t.worker_id,r.job_json FROM flow_download_tasks t JOIN flow_runs r ON r.id=t.run_id
        WHERE t.state IN ('claimed','cancelling')"""):
        if _job(row).get('site', {}).get('id') == site_id:
            occupied.add(row['worker_id'])
    return existing_worker in occupied or len(occupied) < portal_limit(db, site_id)


def initialize(db, worker_id, run_id, run_date, completed_keys):
    run = _owner(db, worker_id, run_id)
    job = _job(run)
    if not flow_tasks.enabled(job) or not (job.get('paths') or {}).get('artifact_store_root'):
        raise HTTPException(409, 'This run is not a managed parallel download job.')
    existing = _fanout(db, run_id)
    if existing:
        if existing['coordinator_id'] != worker_id:
            raise HTTPException(409, 'Another coordinator owns this bundle.')
        return snapshot(db, run_id)
    from app.flow_retention import read_marker
    folder = run['run_folder']
    marker = read_marker(Path(folder)) if folder else None
    if not marker or marker.get('run_id') != run_id or marker.get('flow_id') != run['flow_id']:
        raise HTTPException(409, 'Register the owned run folder before creating tasks.')
    matrix = flow_tasks.task_matrix(job)
    if len(matrix) > 5000 or len({task['key'] for task in matrix}) != len(matrix):
        raise HTTPException(409, 'The task matrix must contain at most 5,000 distinct exports.')
    carried = {flow_tasks.task_key(item.get('export_view'), item.get('period_key')): item for item in (job.get('resume') or {}).get('completed') or []}
    if not set(completed_keys).issubset(carried):
        raise HTTPException(409, 'Carried tasks must belong to the frozen Resume evidence.')
    now = timestamp()
    db.execute("INSERT INTO flow_run_fanout(run_id,coordinator_id,run_folder,matrix_json,run_date,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (run_id, worker_id, folder, _json(matrix), run_date, now, now))
    for task in matrix:
        artifact = carried.get(task['key']) if task['key'] in completed_keys else None
        if artifact:
            artifact = {**artifact, 'bundle_index': task['ordinal'], 'bundle_count': len(matrix), 'status': 'saved'}
        db.execute("INSERT INTO flow_download_tasks(run_id,task_key,ordinal,state,artifact_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (run_id, task['key'], task['ordinal'], 'succeeded' if artifact else 'queued', _json([artifact] if artifact else []), now, now))
    local_now = local_timestamp()
    db.execute("UPDATE flow_runs SET status='running',started_at=COALESCE(started_at,?),heartbeat_at=? WHERE id=?", (local_now, local_now, run_id))
    _event(db, run_id, 'parallel_downloads', f'Prepared {len(matrix)} ordered export tasks; up to {flow_tasks.parallelism(job)} download slots.', {'total': len(matrix), 'parallelism': flow_tasks.parallelism(job)})
    return snapshot(db, run_id)


def snapshot(db, run_id):
    parent = _fanout(db, run_id)
    if not parent:
        return None
    tasks = [dict(row) for row in db.execute('SELECT * FROM flow_download_tasks WHERE run_id=? ORDER BY ordinal', (run_id,))]
    artifacts = [item for task in tasks if task['state'] == 'succeeded' for item in json.loads(task['artifact_json'])]
    return {'run_id': run_id, 'state': parent['state'], 'total': len(tasks),
            'completed': sum(task['state'] == 'succeeded' for task in tasks),
            'active': sum(task['state'] in TASK_ACTIVE for task in tasks),
            'drained': not any(task['state'] in TASK_ACTIVE for task in tasks),
            'error': parent['error'], 'terminal_status': parent['terminal_status'],
            'run_folder': parent['run_folder'], 'run_date': parent['run_date'],
            'artifacts': artifacts,
            'tasks': [{key: task[key] for key in ('id','ordinal','task_key','state','attempt','worker_id','output_folder','error')} for task in tasks]}


def _payload(db, task, job=None):
    parent = _fanout(db, task['run_id'])
    job = copy.deepcopy(job or _job(db.execute('SELECT job_json FROM flow_runs WHERE id=?', (task['run_id'],)).fetchone()))
    # A downloader never consumes old Resume files or performs downstream work.
    # Keep the saved SQL/transform flags for download normalization requirements.
    job.pop('resume', None)
    job['execution'].pop('required_artifact_store_id', None)
    job['execution'].pop('required_artifact_store_ids', None)
    job['_runtime_task_date'] = parent['run_date']
    job['_runtime_run_folder'] = parent['run_folder']
    return {**dict(task), 'job': job, 'run_folder': parent['run_folder'], 'run_date': parent['run_date']}


def claim_task(db, worker_id, *, run_id=None):
    reap(db)
    worker = db.execute('SELECT * FROM flow_workers WHERE worker_id=?', (worker_id,)).fetchone()
    if not worker:
        raise HTTPException(404, 'Register the worker before claiming a download task.')
    if worker['stop_requested_pid'] is not None:
        return None
    caps = json.loads(worker['capabilities_json'] or '{}')
    mode = 'headed' if caps.get('headed') else 'headless'
    if not caps.get(flow_tasks.CAPABILITY) or not caps.get('shared_flow_artifacts'):
        return None
    if worker['current_task_id']:
        existing = db.execute("SELECT * FROM flow_download_tasks WHERE id=? AND worker_id=? AND state='claimed'", (worker['current_task_id'], worker_id)).fetchone()
        if existing:
            return _payload(db, existing)
    if worker['current_scan_id']:
        return None
    candidates = db.execute("""SELECT t.*, r.job_json, r.worker_id AS coordinator_id
        FROM flow_download_tasks t JOIN flow_runs r ON r.id=t.run_id JOIN flow_run_fanout f ON f.run_id=r.id
        WHERE t.state='queued' AND f.state='downloading' AND r.status IN ('claimed','running')
          AND (? IS NULL OR t.run_id=?) ORDER BY t.run_id,t.ordinal""", (run_id, run_id)).fetchall()
    for task in candidates:
        job = _job(task)
        from app import flow_browser
        if not flow_browser.can_claim(job, caps):
            continue
        if job.get('execution', {}).get('browser_mode', 'headless') != mode or not flow_tasks.supported(job, caps):
            continue
        owner = task['coordinator_id'] == worker_id and worker['current_run_id'] == task['run_id']
        if worker['current_run_id'] and not owner:
            continue
        if not owner and not flow_capacity.can_claim(db, worker_id, mode):
            return None
        if not portal_available(db, job, existing_worker=worker_id if owner else None):
            continue
        if job.get('site', {}).get('adapter') not in (caps.get('adapters') or []):
            continue
        from app.flow_publish import artifact_store_id
        required = artifact_store_id(Path('.'), store_root=Path(job['paths']['artifact_store_root']))
        stores = [caps.get('artifact_store_id'), *(caps.get('artifact_store_ids') or [])]
        if required not in stores:
            continue
        active = db.execute("SELECT COUNT(*) FROM flow_download_tasks WHERE run_id=? AND state IN ('claimed','cancelling')", (task['run_id'],)).fetchone()[0]
        if active >= flow_tasks.parallelism(job):
            continue
        token = uuid.uuid4().hex
        now = timestamp()
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()
        parent = _fanout(db, task['run_id'])
        output = str(Path(parent['run_folder']) / '.tasks' / f"{task['ordinal']:05d}-{token}")
        changed = db.execute("UPDATE flow_download_tasks SET state='claimed',attempt=attempt+1,lease_token=?,worker_id=?,lease_expires_at=?,output_folder=?,updated_at=? WHERE id=? AND state='queued'", (token, worker_id, expiry, output, now, task['id'])).rowcount
        if not changed:
            continue
        local_now = local_timestamp()
        db.execute("UPDATE flow_workers SET current_task_id=?,status='busy',last_seen_at=?,updated_at=? WHERE worker_id=?", (task['id'], local_now, local_now, worker_id))
        return _payload(db, db.execute('SELECT * FROM flow_download_tasks WHERE id=?', (task['id'],)).fetchone(), job)
    return None


def _active_lease(db, worker_id, task_id, token):
    task = db.execute('SELECT * FROM flow_download_tasks WHERE id=? AND worker_id=? AND lease_token=?', (task_id, worker_id, token)).fetchone()
    if not task or task['state'] != 'claimed' or task['lease_expires_at'] < timestamp():
        raise HTTPException(409, 'The download lease is stale or cancelled.')
    parent = _fanout(db, task['run_id'])
    if parent['state'] != 'downloading':
        raise HTTPException(409, 'This bundle no longer accepts download results.')
    return task


def report_task(db, worker_id, task_id, token, status, progress, artifacts):
    # Duplicate terminal delivery is harmless, but never changes committed data.
    previous = db.execute('SELECT * FROM flow_download_tasks WHERE id=?', (task_id,)).fetchone()
    if previous and previous['worker_id'] == worker_id and previous['lease_token'] == token and previous['state'] == 'cancelling' and status == 'cancelled':
        db.execute("UPDATE flow_download_tasks SET state='cancelled',updated_at=? WHERE id=?", (timestamp(), task_id))
        db.execute("UPDATE flow_workers SET current_task_id=NULL,stop_requested_pid=NULL WHERE worker_id=? AND current_task_id=?", (worker_id, task_id))
        return {'state': 'cancelled'}
    if previous and previous['worker_id'] == worker_id and previous['lease_token'] == token and previous['state'] in TASK_TERMINAL:
        return {'state': previous['state'], 'ignored': True}
    task = _active_lease(db, worker_id, task_id, token)
    now = timestamp()
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()
    if status == 'succeeded':
        expected_count = db.execute('SELECT COUNT(*) FROM flow_download_tasks WHERE run_id=?', (task['run_id'],)).fetchone()[0]
        if len(artifacts) != 1:
            raise HTTPException(409, 'A download task must produce exactly one export artifact.')
        artifact = artifacts[0]
        if (artifact.get('status') != 'saved' or artifact.get('bundle_index') != task['ordinal'] or artifact.get('bundle_count') != expected_count
                or flow_tasks.task_key(artifact.get('export_view'), artifact.get('period_key')) != task['task_key']):
            raise HTTPException(409, 'The artifact does not match its whole-bundle task identity.')
        for key in ('file_path', 'original_file_path', 'deliverable_file_path'):
            if artifact.get(key) and not flow_paths.is_inside(artifact[key], task['output_folder']):
                raise HTTPException(409, 'The task result points outside its immutable output folder.')
        if not artifact.get('checksum') or not isinstance(artifact.get('file_size'), int):
            raise HTTPException(409, 'The task result needs a checksum and file size.')
    # Lease heartbeats must not erase the worker's current visible action.
    if status == 'running' and progress.get('stage') == 'task_heartbeat':
        progress = json.loads(task['progress_json'] or '{}')
    else:
        # Later bookkeeping messages can return to file_export after file
        # normalization. Preserve completed milestones for this exact lease.
        prior = json.loads(task['progress_json'] or '{}')
        milestones = set(prior.get('_download_milestones') or [])
        stage = progress.get('stage')
        if stage in {'file_export', 'file_transfer', 'file_normalization', 'file_validation'}:
            milestones.add(stage)
        progress = {**progress, '_download_milestones': sorted(milestones)}
    db.execute('UPDATE flow_download_tasks SET state=?,progress_json=?,artifact_json=?,lease_expires_at=?,error=?,updated_at=? WHERE id=? AND lease_token=?',
               ('claimed' if status == 'running' else status, _json(progress), _json(artifacts if status == 'succeeded' else []), expiry,
                progress.get('message') if status == 'failed' else None, now, task_id, token))
    local_now = local_timestamp()
    db.execute('UPDATE flow_workers SET last_seen_at=?,updated_at=? WHERE worker_id=?', (local_now, local_now, worker_id))
    # The coordinator can also execute an export. Its task progress is proof
    # of life for its parent run; helpers must never mask a lost coordinator.
    db.execute("UPDATE flow_runs SET heartbeat_at=? WHERE id=? AND worker_id=? AND status IN ('claimed','running')", (local_now, task['run_id'], worker_id))
    if status != 'running':
        db.execute("UPDATE flow_workers SET current_task_id=NULL,status=CASE WHEN current_run_id IS NULL THEN 'idle' ELSE 'busy' END WHERE worker_id=? AND current_task_id=?", (worker_id, task_id))
        _event(db, task['run_id'], 'download_task_' + status, f"Export {task['ordinal']} {status}.", {'ordinal': task['ordinal'], 'worker_id': worker_id})
    if status in {'failed','cancelled'}:
        abort(db, task['run_id'], progress.get('message') or 'A download task failed.')
    state = snapshot(db, task['run_id'])
    if state['artifacts']:
        db.execute('UPDATE flow_runs SET artifact_json=? WHERE id=?', (_json(state['artifacts']), task['run_id']))
    return {'state': status, 'completed': state['completed'], 'total': state['total']}


def abort(db, run_id, message, *, terminal='failed', coordinator_stopped=False):
    parent = _fanout(db, run_id)
    if not parent or parent['state'] == 'complete':
        return
    if parent['sql_started']:
        db.execute('UPDATE flows SET sql_reconciliation_required=1 WHERE id=(SELECT flow_id FROM flow_runs WHERE id=?)', (run_id,))
        message += ' SQL may have committed; reconcile the target before another run.'
    now = timestamp()
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()
    db.execute("UPDATE flow_run_fanout SET state='aborting',terminal_status=COALESCE(terminal_status,?),error=COALESCE(error,?),coordinator_stopped=MAX(coordinator_stopped,?),abort_deadline=COALESCE(abort_deadline,?),updated_at=? WHERE run_id=?", (terminal, message, int(coordinator_stopped), deadline, now, run_id))
    db.execute("UPDATE flow_download_tasks SET state='cancelled',updated_at=? WHERE run_id=? AND state='queued'", (now, run_id))
    db.execute("UPDATE flow_download_tasks SET state='cancelling',updated_at=? WHERE run_id=? AND state='claimed'", (now, run_id))


def reap(db):
    now = timestamp()
    expired = db.execute("SELECT * FROM flow_download_tasks WHERE state IN ('claimed','cancelling') AND lease_expires_at < ?", (now,)).fetchall()
    for task in expired:
        db.execute("UPDATE flow_download_tasks SET state=?,error='Download lease expired',updated_at=? WHERE id=?", ('failed' if task['state'] == 'claimed' else 'cancelled', now, task['id']))
        db.execute("UPDATE flow_workers SET current_task_id=NULL WHERE worker_id=? AND current_task_id=?", (task['worker_id'], task['id']))
        abort(db, task['run_id'], 'A download worker stopped reporting. Completed tasks are preserved for Resume.')
    db.execute("UPDATE flow_run_fanout SET coordinator_stopped=1 WHERE state='aborting' AND abort_deadline < ?", (now,))
    for row in db.execute("SELECT run_id FROM flow_run_fanout WHERE state='aborting' AND coordinator_stopped=1").fetchall():
        finish_aborted(db, row['run_id'])


def finish_aborted(db, run_id):
    state = snapshot(db, run_id)
    if not state or state['state'] != 'aborting' or not state['drained']:
        return False
    from app.routers import flows
    now = local_timestamp()
    terminal = state['terminal_status'] or 'failed'
    run = db.execute('SELECT * FROM flow_runs WHERE id=?', (run_id,)).fetchone()
    message = state['error'] or 'Parallel run stopped.'
    artifacts = json.loads(run['artifact_json'] or '[]')
    recorded = {flow_tasks.task_key(item.get('export_view'), item.get('period_key')) for item in artifacts if item.get('status') == 'saved'}
    artifacts.extend(item for item in state['artifacts'] if flow_tasks.task_key(item.get('export_view'), item.get('period_key')) not in recorded)
    db.execute('UPDATE flow_runs SET status=?,error=?,finished_at=?,heartbeat_at=?,progress_json=?,artifact_json=? WHERE id=?', (terminal, message, now, now, _json({'stage': terminal, 'message': message}), _json(artifacts), run_id))
    db.execute('UPDATE flows SET last_status=?,last_error=?,last_run_at=?,updated_at=? WHERE id=?', (terminal, message, now, now, run['flow_id']))
    db.execute("UPDATE flow_workers SET current_run_id=NULL,current_task_id=NULL,status='offline',updated_at=? WHERE current_run_id=?", (now, run_id))
    db.execute("UPDATE flow_run_fanout SET state='complete',updated_at=? WHERE run_id=?", (timestamp(), run_id))
    flows._release_retention_ops(db, run_id, now)
    db.executemany("""INSERT INTO flow_run_files
        (run_id,period_key,file_path,filename,storage_scope,artifact_store_id,file_size,checksum,row_count,status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", [
        (run_id, flows._period_key_text(item.get('period_key')), item['file_path'], item['filename'],
         item.get('storage_scope'), item.get('artifact_store_id'), item.get('file_size'), item.get('checksum'),
         item.get('row_count'), item.get('status', 'saved'), now)
        for item in artifacts if item.get('file_path') and item.get('filename')])
    flows._sync_flow_failure_actions(db, now)
    _event(db, run_id, terminal, message)
    if terminal == 'failed':
        # Reaping also runs inside claim transactions. Defer the established
        # owner notification until the watchdog can send after committing.
        _event(db, run_id, 'owner_alert_pending', 'Owner notification queued after parallel-run recovery.')
    return True


def claim_finalizer(db, worker_id, run_id):
    _owner(db, worker_id, run_id)
    reap(db)
    parent = _fanout(db, run_id)
    state = snapshot(db, run_id)
    if not parent or parent['coordinator_id'] != worker_id or state['completed'] != state['total'] or state['active'] or parent['state'] not in {'downloading', 'finalizing'}:
        raise HTTPException(409, 'Finalization requires every expected download to succeed.')
    if parent['state'] == 'downloading':
        token = uuid.uuid4().hex
        db.execute("UPDATE flow_run_fanout SET state='finalizing',finalizer_token=?,updated_at=? WHERE run_id=? AND state='downloading'", (token, timestamp(), run_id))
        _event(db, run_id, 'finalizing', 'All exports completed. Validating the full bundle before publication, transformation and SQL.')
    else:
        token = parent['finalizer_token']
    return {**snapshot(db, run_id), 'finalizer_token': token}


def guard_progress(db, worker_id, run_id, status, token, stage):
    parent = _fanout(db, run_id)
    if not parent:
        job = _job(db.execute('SELECT job_json FROM flow_runs WHERE id=?', (run_id,)).fetchone())
        if flow_tasks.enabled(job) and (status == 'succeeded' or stage in {'direct_publish','publish_complete','transformation','transformation_complete','sql_insertion','sql_insertion_complete'}):
            raise HTTPException(409, 'Parallel jobs require a complete validated task bundle.')
        return
    if parent['coordinator_id'] != worker_id or parent['state'] == 'complete':
        raise HTTPException(409, 'This coordinator no longer owns finalization.')
    if parent['state'] == 'aborting':
        if status not in {'failed','cancelled'} or not snapshot(db, run_id)['drained']:
            raise HTTPException(409, 'The cancelled bundle must drain before its parent becomes terminal.')
    elif parent['state'] == 'finalizing':
        if not token or token != parent['finalizer_token']:
            raise HTTPException(409, 'The finalizer token is stale.')
        if stage == 'sql_insertion':
            db.execute('UPDATE flow_run_fanout SET sql_started=1 WHERE run_id=?', (run_id,))
        if status in {'failed','cancelled'} and parent['sql_started']:
            db.execute('UPDATE flows SET sql_reconciliation_required=1 WHERE id=(SELECT flow_id FROM flow_runs WHERE id=?)', (run_id,))
    elif status == 'succeeded' or stage in {'direct_publish','publish_complete','transformation','transformation_complete','sql_insertion','sql_insertion_complete'}:
        raise HTTPException(409, 'The complete bundle has not acquired finalization.')
    if status in {'succeeded','failed','cancelled'}:
        if parent['state'] == 'downloading' and not snapshot(db, run_id)['drained']:
            raise HTTPException(409, 'Drain downloads before terminating their parent run.')
        db.execute("UPDATE flow_run_fanout SET state='complete',updated_at=? WHERE run_id=?", (timestamp(), run_id))


def recover_worker(db, worker, replacement_pid):
    """Fence the old process; a new registration cannot inherit its leases."""
    old_pid = json.loads(worker['capabilities_json'] or '{}').get('process_id')
    if old_pid is None or replacement_pid is None or str(old_pid) == str(replacement_pid):
        return False
    now = timestamp()
    if worker['current_task_id']:
        task = db.execute("SELECT * FROM flow_download_tasks WHERE id=? AND state IN ('claimed','cancelling')", (worker['current_task_id'],)).fetchone()
        if task:
            db.execute("UPDATE flow_download_tasks SET state='failed',error='Download worker restarted',updated_at=? WHERE id=?", (now, task['id']))
            abort(db, task['run_id'], 'A download worker restarted. Completed tasks are preserved for Resume.')
    parent = _fanout(db, worker['current_run_id']) if worker['current_run_id'] else None
    if parent and parent['state'] != 'complete':
        job = _job(db.execute('SELECT job_json FROM flow_runs WHERE id=?', (parent['run_id'],)).fetchone())
        message = 'The coordinator restarted; the run was not replayed automatically.'
        if parent['sql_started'] and job.get('sql_handoff', {}).get('enabled'):
            message += ' The SQL commit outcome is unknown. Reconcile the target before retrying.'
        abort(db, parent['run_id'], message, coordinator_stopped=True)
        db.execute('UPDATE flow_workers SET current_run_id=NULL WHERE worker_id=?', (worker['worker_id'],))
    db.execute('UPDATE flow_workers SET current_task_id=NULL,stop_requested_pid=NULL WHERE worker_id=?', (worker['worker_id'],))
    reap(db)
    return bool(parent)


def stop_download_workers(run_id, *, exclude_worker=None, include_coordinator=False):
    """Fence exact assignments before stopping them; never kill a replacement."""
    from concurrent.futures import ThreadPoolExecutor
    from app.database import get_db
    from app.flow_local_runner import stop_local_worker
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        task_workers = [dict(row) for row in db.execute("""SELECT w.*,t.id AS task_id FROM flow_workers w
            JOIN flow_download_tasks t ON t.id=w.current_task_id
            WHERE t.run_id=? AND t.state='cancelling'""", (run_id,))]
        targets = {row['worker_id']: row for row in task_workers if row['worker_id'] != exclude_worker}
        if include_coordinator:
            row = db.execute('SELECT * FROM flow_workers WHERE current_run_id=?', (run_id,)).fetchone()
            if row:
                targets.setdefault(row['worker_id'], {**dict(row), 'task_id': row['current_task_id']})
        for target in targets.values():
            pid = json.loads(target['capabilities_json'] or '{}').get('process_id')
            target['pid'] = pid if type(pid) is int and pid > 0 else None
            target['fence'] = target['pid'] or -1
            db.execute("UPDATE flow_workers SET stop_requested_pid=?,status='stopping' WHERE worker_id=?", (target['fence'], target['worker_id']))
    def stop(target):
        # The latch prevents this worker from claiming another operation while
        # Windows processes the stop. New-PID registration clears the latch.
        with get_db() as db:
            current = db.execute('SELECT stop_requested_pid,capabilities_json FROM flow_workers WHERE worker_id=?', (target['worker_id'],)).fetchone()
        if not current or current['stop_requested_pid'] != target['fence'] or json.loads(current['capabilities_json'] or '{}').get('process_id') != json.loads(target['capabilities_json'] or '{}').get('process_id'):
            return target, {'status': 'replaced'}
        if target['pid'] is None:
            return target, {'status': 'unconfirmed', 'message': 'No process ID; the fenced task must acknowledge cancellation or its lease must expire.'}
        mode = 'headed' if json.loads(target['capabilities_json'] or '{}').get('headed') else 'headless'
        return target, stop_local_worker(mode, target['pid'], worker_id=target['worker_id'])
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=len(targets)) as executor:
        results = list(executor.map(stop, targets.values()))
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        parent = _fanout(db, run_id)
        for target, result in results:
            if result['status'] not in {'stopped', 'replaced'}:
                continue
            if target['task_id']:
                db.execute("UPDATE flow_download_tasks SET state='cancelled',updated_at=? WHERE id=? AND worker_id=? AND state='cancelling'", (timestamp(), target['task_id'], target['worker_id']))
            db.execute("UPDATE flow_workers SET current_task_id=NULL,stop_requested_pid=NULL,status='offline' WHERE worker_id=? AND stop_requested_pid=?", (target['worker_id'], target['fence']))
            if include_coordinator and parent and parent['coordinator_id'] == target['worker_id']:
                db.execute('UPDATE flow_run_fanout SET coordinator_stopped=1 WHERE run_id=?', (run_id,))
        reap(db)
    return [{'worker_id': target['worker_id'], **result} for target, result in results]


def request_stop(run_id):
    from app.database import get_db
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        parent = _fanout(db, run_id)
        message = 'Stop requested; download leases are fenced and active workers are draining.'
        run = db.execute('SELECT job_json,status FROM flow_runs WHERE id=?', (run_id,)).fetchone()
        if not parent or parent['state'] == 'complete':
            return {'run_id': run_id, 'status': run['status'], 'message': 'The run already finished.', 'workers': []}
        if parent['sql_started'] and _job(run).get('sql_handoff', {}).get('enabled'):
            message += ' The SQL commit outcome may be unknown; reconcile the target before retrying.'
        abort(db, run_id, message, terminal='cancelled')
        _event(db, run_id, 'cancelling', message)
    stopped = stop_download_workers(run_id, include_coordinator=True)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        reap(db)
        row = db.execute('SELECT status FROM flow_runs WHERE id=?', (run_id,)).fetchone()
        return {'run_id': run_id, 'status': row['status'], 'message': message, 'workers': stopped}
