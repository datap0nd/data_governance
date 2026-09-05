"""Download-only task execution and coordinator bundle assembly."""
from __future__ import annotations

import copy
import re
import threading
import time
from app.flow_clock import dubai_today
from datetime import date
from pathlib import Path

import httpx

from app import flow_layout, flow_paths, flow_publish, flow_retention, flow_tasks


def task_output_folder(job, task, run_id):
    parent = Path(task['run_folder'])
    marker = flow_retention.read_marker(parent)
    if not marker or marker.get('run_id') != run_id or marker.get('flow_id') != job['flow']['id']:
        raise RuntimeError('Download task has no owned parent run folder.')
    root = job['paths']['artifact_store_root'] if job['downloads'].get('output_mode') == 'direct_replace' else job['downloads']['target_folder']
    flow_paths.assert_inside(str(parent), root, label='Task parent')
    token = task['lease_token']
    if not re.fullmatch('[0-9a-f]{32}', token) or type(task['ordinal']) is not int or task['ordinal'] < 1:
        raise RuntimeError('Invalid download task lease.')
    tasks = parent / '.tasks'
    expected = tasks / f"{task['ordinal']:05d}-{token}"
    if Path(task['output_folder']) != expected:
        raise RuntimeError('Download task output does not match its lease.')
    for path in (parent, tasks, expected):
        flow_layout._regular(path)
    tasks.mkdir(exist_ok=True)
    expected.mkdir(exist_ok=False)
    job['_runtime_run_folder'] = str(parent)
    job['_runtime_task_date'] = task['run_date']
    return expected


def execute_task(client, worker_id, task, page, profile_dir, staging, *, headed=False):
    """No publication, transformations, SQL or retention calls are possible here."""
    from app import flow_worker as worker
    job = copy.deepcopy(task['job'])
    endpoint = f"/api/flows/worker/{worker_id}/tasks/{task['id']}/progress"
    stopped = threading.Event()
    cancelled = threading.Event()
    artifacts = []
    timings = []
    def send(status, detail, items=None):
        return worker._api(client, 'POST', endpoint, {
            'lease_token': task['lease_token'], 'status': status,
            'progress': worker._bounded_detail(detail), 'artifacts': items or [],
        })
    def progress(status, detail, *_args, **_kwargs):
        if cancelled.is_set():
            raise RuntimeError('The download task was cancelled or lost its lease.')
        send('running', detail)
    def heartbeat():
        with httpx.Client(base_url=str(client.base_url), headers={'User-Agent': 'Metronome-Flow-Worker/1'}) as heartbeat_client:
            while not stopped.wait(20):
                try:
                    worker._api(heartbeat_client, 'POST', endpoint, {'lease_token': task['lease_token'], 'status': 'running', 'progress': {'stage': 'task_heartbeat'}})
                except Exception:
                    cancelled.set()
                    return
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if (job.get('execution', {}).get('browser_mode') == 'headed') != headed:
            raise RuntimeError('Download task browser mode does not match this worker.')
        send('running', {'stage': 'download_task', 'message': f"Downloading export {task['ordinal']}."})
        artifacts, timings = worker.execute_job(page, job, progress, profile_dir, staging,
            artifacts=artifacts, run_id=task['run_id'], register_folder=lambda _: {}, headed=headed, task_assignment=task)
        send('succeeded', {'stage': 'task_complete', 'timings': timings}, artifacts)
        return True
    except Exception as exc:
        try:
            send('failed', {'stage': 'task_failed', 'message': str(exc), 'timings': timings})
        except Exception:
            # A cancellation acknowledgement proves this task has unwound. It
            # cannot replace a successful result or resurrect an expired lease.
            try:
                send('cancelled', {'stage': 'task_cancelled', 'message': str(exc)})
            except Exception:
                pass
        return False
    finally:
        stopped.set()
        thread.join(timeout=2)


def _valid_carried(job):
    from app.flow_worker import _validated_resume_artifacts
    return {key: item for key, item in _validated_resume_artifacts(job).items()
            if item.get('file_path') and item.get('checksum') and isinstance(item.get('file_size'), int)
            and flow_publish.artifact_file_valid(item, require_deliverable=bool(item.get('deliverable_file_path')))}


def assemble_bundle(job, state, profile_dir):
    """Validate the complete matrix and every file before copying final inputs."""
    from app import flow_worker as worker
    matrix = flow_tasks.task_matrix(job)
    expected = {task['key']: task for task in matrix}
    items = state['artifacts']
    if len(items) != len(matrix) or len({flow_tasks.task_key(item.get('export_view'), item.get('period_key')) for item in items}) != len(matrix):
        raise RuntimeError('Finalization requires every distinct expected export exactly once.')
    task_rows = {task['task_key']: task for task in state['tasks']}
    carried = _valid_carried(job)
    parent = Path(state['run_folder'])
    flow_layout._regular(parent)
    flow_layout._regular(parent / '.tasks')
    marker = flow_retention.read_marker(parent)
    if not marker or marker.get('flow_id') != job['flow']['id'] or marker.get('run_id') != state['run_id']:
        raise RuntimeError('Finalization parent ownership changed.')
    job['_runtime_task_date'] = state['run_date']
    for item in items:
        key = flow_tasks.task_key(item.get('export_view'), item.get('period_key'))
        spec = expected.get(key)
        record = task_rows.get(key)
        if not spec or not record or record['state'] != 'succeeded' or item.get('status') != 'saved' or item.get('bundle_index') != spec['ordinal'] or item.get('bundle_count') != len(matrix):
            raise RuntimeError('Finalization found a mismatched task identity or ordinal.')
        output = record['output_folder']
        if not output and key not in carried:
            raise RuntimeError('Carried export no longer matches validated Resume evidence.')
        rendered = Path(worker._render_filename(job['downloads']['filename_template'], job, spec['period'], spec['ordinal'], spec['export_view']))
        suffix = r'(?: \((?:[2-9]|[1-9][0-9]+)\))?'
        pattern = re.escape(rendered.stem) + suffix + r'(?:_normalized)?' + suffix
        for path_key, size_key, checksum_key in (
            ('file_path', 'file_size', 'checksum'),
            ('deliverable_file_path', 'deliverable_file_size', 'deliverable_checksum'),
            ('original_file_path', 'original_file_size', 'original_checksum'),
        ):
            value = item.get(path_key)
            if not value:
                if path_key == 'file_path':
                    raise RuntimeError('Export has no primary file.')
                continue
            path = Path(value)
            flow_layout._regular(path)
            if output:
                flow_layout._regular(Path(output))
                flow_paths.assert_inside(str(path), output, label='Task artifact')
                if not re.fullmatch(pattern, path.stem):
                    raise RuntimeError('Task filename does not preserve its whole-bundle ordinal.')
            observed = flow_publish.read_size_checksum(path)
            if observed['file_size'] != item.get(size_key) or observed['checksum'] != item.get(checksum_key):
                raise RuntimeError('An export changed or is incomplete; downstream processing was blocked.')
        if (job.get('sql_handoff', {}).get('enabled') or job.get('transformation', {}).get('enabled')) and Path(item['file_path']).suffix.casefold() != '.csv':
            raise RuntimeError('Downstream processing requires a normalized CSV for every export.')
    # Copy only after ALL exports pass. Source task outputs remain immutable;
    # collision suffixes are assigned in the same order as serial execution.
    result = []
    for item in sorted(items, key=lambda item: item['bundle_index']):
        clone = worker._copy_carried_artifact(item, parent)
        for path_key, name_key in (('file_path','filename'), ('original_file_path','original_filename'), ('deliverable_file_path','deliverable_filename')):
            if clone.get(path_key):
                clone[name_key] = Path(clone[path_key]).name
        result.append(worker._decorate_artifact_storage(clone, job, profile_dir))
    job['_runtime_run_folder'] = str(parent)
    job['_runtime_bundle_count'] = len(matrix)
    return result


def acquire_bundle(client, worker_id, transport_state, page, job, progress, profile_dir, staging,
                   *, artifacts, run_id, register_folder, headed=False):
    from app import flow_worker as worker
    started = time.perf_counter()
    worker._prepare_run_folder(job, profile_dir, run_id=run_id, register_folder=register_folder, report_progress=progress)
    job['_runtime_artifact_store_id'] = worker._job_store_id(job, profile_dir)
    job['_runtime_artifact_store_ids'] = list(worker._runtime_store_ids(job, profile_dir))
    base = f'/api/flows/worker/{worker_id}/runs/{run_id}'
    try:
        state = worker._api(client, 'POST', base + '/tasks', {'run_date': dubai_today().isoformat(), 'completed_keys': list(_valid_carried(job))})
        while True:
            artifacts[:] = state['artifacts']
            if state['state'] == 'aborting':
                raise RuntimeError(state['error'] or 'Parallel downloads stopped.')
            progress('running', {'stage': 'parallel_downloads', 'message': f"Downloaded {state['completed']} of {state['total']} exports; {state['active']} active slots.",
                                 'completed': state['completed'], 'total': state['total'], 'active_slots': state['active']}, artifacts)
            if state['completed'] == state['total']:
                break
            claimed = worker._api(client, 'POST', base + '/tasks/claim')
            if claimed.get('task'):
                execute_task(client, worker_id, claimed['task'], page, profile_dir, staging, headed=headed)
            else:
                time.sleep(1)
            state = worker._api(client, 'GET', base + '/tasks')
        state = worker._api(client, 'POST', base + '/finalizer')
        transport_state['finalizer_token'] = state['finalizer_token']
        artifacts[:] = assemble_bundle(job, state, profile_dir)
        duration = round((time.perf_counter() - started) * 1000)
        return artifacts, [{'phase': 'parallel_download', 'duration_ms': duration, 'status': 'succeeded', 'item_count': len(artifacts)},
                           {'phase': 'total', 'duration_ms': duration, 'status': 'succeeded'}]
    except Exception as exc:
        # Release parent reservations only after every active task acknowledges
        # cancellation, Windows confirms its stop, or its fenced lease expires.
        try:
            state = worker._api(client, 'POST', base + '/tasks/abort', {'message': str(exc)[:10000]})
            while not state['drained']:
                time.sleep(1)
                state = worker._api(client, 'GET', base + '/tasks')
            artifacts[:] = state['artifacts']
        except Exception:
            # If the server is unreachable, durable leases retain the parent
            # reservation and stale-worker recovery closes it without replay.
            pass
        raise
