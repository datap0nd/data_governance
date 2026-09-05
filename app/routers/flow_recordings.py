from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import get_db
from app import flow_recording, flow_recordings
from app.routers import flows

router = APIRouter(prefix='/api/flows', tags=['Flow recordings'])


@router.get('/worker/{worker_id}/recordings/{scan_id}/control')
def recording_control(worker_id: str, scan_id: int):
    with get_db() as db:
        row = db.execute('SELECT * FROM flow_catalog_scans WHERE id=? AND worker_id=?', (scan_id, worker_id)).fetchone()
        if not row or not json.loads(row['job_json']).get('recording_operation'):
            raise HTTPException(404, 'Recording is not assigned to this worker.')
        now = datetime.now(timezone.utc).isoformat()
        if row['status'] in {'claimed', 'running'}:
            db.execute('UPDATE flow_catalog_scans SET heartbeat_at=? WHERE id=?', (now, scan_id))
            db.execute('UPDATE flow_workers SET last_seen_at=?,updated_at=? WHERE worker_id=? AND current_scan_id=?', (now, now, worker_id, scan_id))
        job = json.loads(row['job_json'])
        return {'status': row['status'], 'finish_requested': bool(job.get('finish_requested')),
                'cancel_requested': bool(job.get('cancel_requested'))}


class RecordingDraft(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    site_id: int
    report_id: int | None = None
    report_url: str | None = None


class RevisionWrite(BaseModel):
    definition: dict


class SingleRangeWrite(BaseModel):
    start: str = Field(min_length=1, max_length=32)
    end: str = Field(min_length=1, max_length=32)


def _launch(scan_id):
    from app.flow_local_runner import launch_local_worker
    from app import flow_capacity
    # Pick and pin one slot under the same transaction. Concurrent recording
    # requests account for each other's queued reservations as well as work.
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM flow_catalog_scans WHERE id=?', (scan_id,)).fetchone()
        if row['status'] != 'queued':
            return {'scan_id': scan_id, 'worker': {'status': 'already_assigned'}}
        occupied = [item['worker_id'] for item in flow_capacity.assignments(db, 'headed')]
        for pending in db.execute("SELECT job_json FROM flow_catalog_scans WHERE status='queued' AND id<>?", (scan_id,)):
            occupied.append(json.loads(pending['job_json']).get('execution', {}).get('worker_id'))
        slot = min(range(1, flow_capacity.capacity(db, 'headed') + 1),
                   key=lambda number: occupied.count(flow_capacity.worker_id(number, 'headed')))
        job = json.loads(row['job_json'])
        job['execution']['worker_id'] = flow_capacity.worker_id(slot, 'headed')
        db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan_id))
    worker = launch_local_worker('headed', slot=slot)
    if worker.get('status') == 'error':
        with get_db() as db:
            changed = db.execute("UPDATE flow_catalog_scans SET status='failed',error=?,finished_at=? WHERE id=? AND status='queued'",
                       (worker.get('message', 'Could not start recording worker.'), datetime.now(timezone.utc).isoformat(), scan_id))
            if changed.rowcount:
                db.execute("UPDATE flow_recording_revisions SET status='draft' WHERE id=(SELECT revision_id FROM flow_recording_sessions WHERE scan_id=?) AND status='validating'", (scan_id,))
            else:
                worker = {'status': 'already_assigned'}
    return {'scan_id': scan_id, 'worker': worker}


@router.post('/recordings/draft')
def create_draft(body: RecordingDraft, request: Request):
    with get_db() as db:
        site = db.execute('SELECT * FROM flow_sites WHERE id=? AND enabled=1', (body.site_id,)).fetchone()
        if not site or site['adapter'] not in {'asap_portal', 'gscm_portal'}:
            raise HTTPException(400, 'Choose an enabled ASAP or GSCM website.')
        url = body.report_url or site['base_url'] or site['auth_url']
    report_id = body.report_id
    if not report_id:
        # Manual route registration needs no successful catalog scan.
        report = flows.create_report(flows.ReportWrite(site_id=body.site_id,
            name=body.name + ' recording', report_url=url,
            automation={'kind': 'recorded_route'}), request)
        report_id = report['id']
    return flows.create_flow(flows.FlowWrite(name=body.name, site_id=body.site_id,
        report_id=report_id, execution_method='recorded', period_strategy='none',
        file_format='xlsx', filename_template='{report}_{index}.xlsx', browser_mode='headed'), request)


@router.get('/{flow_id}/recordings')
def list_recordings(flow_id: int):
    with get_db() as db:
        flow_recordings.reap(db)
        flow = flows._flow_out(db, flow_id)
        revisions = []
        for row in db.execute('SELECT * FROM flow_recording_revisions WHERE flow_id=? ORDER BY id DESC', (flow_id,)):
            item = dict(row)
            item['definition'] = flow_recording.suggest_review(json.loads(item.pop('definition_json')))
            item['evidence'] = json.loads(item.pop('evidence_json') or '{}')
            item.pop('transformation_source', None)
            revisions.append(item)
        sessions = [dict(row) for row in db.execute('''SELECT s.*, c.status,c.progress_json,c.error,c.result_json,
            json_extract(c.job_json,'$.finish_requested') AS finish_requested,
            json_extract(c.job_json,'$.cancel_requested') AS cancel_requested
            FROM flow_recording_sessions s JOIN flow_catalog_scans c ON c.id=s.scan_id
            WHERE s.flow_id=? ORDER BY c.id DESC LIMIT 10''', (flow_id,))]
        return {'flow': flow, 'revisions': revisions, 'sessions': sessions}


@router.post('/{flow_id}/recordings/start')
def start_recording(flow_id: int, request: Request):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        scan_id = flow_recordings.queue_operation(db, flow_id, 'record', flows.get_actor(request))
    return _launch(scan_id)


@router.post('/{flow_id}/recordings/{scan_id}/cancel')
def cancel_recording(flow_id: int, scan_id: int, request: Request):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''SELECT c.* FROM flow_recording_sessions s JOIN flow_catalog_scans c ON c.id=s.scan_id
            WHERE s.scan_id=? AND s.flow_id=?''', (scan_id, flow_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Recording session not found.')
        job = json.loads(row['job_json'])
        stage = json.loads(row['progress_json'] or '{}').get('stage')
        now = datetime.now(timezone.utc)
        requested = job.get('cancel_requested')
        if row['status'] == 'running' and stage in {'recording', 'finishing', 'cancelling'}:
            if not requested or (now - datetime.fromisoformat(requested)).total_seconds() < 10:
                job['cancel_requested'] = requested or now.isoformat()
                progress = {'stage': 'cancelling', 'message': 'Closing this recording and discarding its unsaved actions.'}
                db.execute('UPDATE flow_catalog_scans SET job_json=?,progress_json=? WHERE id=?',
                           (json.dumps(job), json.dumps(progress), scan_id))
                return {'scan_id': scan_id, 'status': 'cancelling', 'message': progress['message']}
    # Authentication/validation may be blocked inside a portal call. The
    # existing exact-worker stop is also the fallback for an unresponsive CLI.
    return flows.stop_scan(scan_id, request)


@router.post('/{flow_id}/recordings/{scan_id}/finish')
def finish_recording(flow_id: int, scan_id: int):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('''SELECT c.* FROM flow_recording_sessions s JOIN flow_catalog_scans c ON c.id=s.scan_id
            WHERE s.flow_id=? AND s.scan_id=? AND s.operation='record' ''', (flow_id, scan_id)).fetchone()
        if not session:
            raise HTTPException(404, 'Recording session not found.')
        if session['status'] in {'queued', 'claimed', 'running'}:
            job = json.loads(session['job_json'])
            if job.get('cancel_requested'):
                raise HTTPException(409, 'This recording is being cancelled.')
            if json.loads(session['progress_json'] or '{}').get('stage') not in {'recording', 'finishing'}:
                raise HTTPException(409, 'Wait for the recording window to open before finishing.')
            job['finish_requested'] = True
            progress = {'stage': 'finishing', 'message': 'Saving the recorded actions and closing the recording windows.'}
            db.execute('UPDATE flow_catalog_scans SET job_json=?,progress_json=? WHERE id=?', (json.dumps(job), json.dumps(progress), scan_id))
    return {'scan_id': scan_id, 'message': 'Finishing recording. Its reviewed steps will appear here.'}


@router.post('/{flow_id}/recordings/revisions')
def save_revision(flow_id: int, body: RevisionWrite):
    try:
        definition = flow_recording.validate_definition({**body.definition, 'version': 2, 'timezone': 'Asia/Dubai'}, activation=False)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(flow_recording.canonical(definition)) > 1_000_000:
        raise HTTPException(422, 'Recording is too large.')
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flows._flow_out(db, flow_id)
        flow_recordings.assert_flow_idle(db, flow_id)
        cursor = db.execute('''INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at)
            VALUES (?,?,'draft',?)''', (flow_id, flow_recording.canonical(definition), datetime.now(timezone.utc).isoformat()))
    return {'revision_id': cursor.lastrowid}


@router.post('/{flow_id}/recordings/revisions/{revision_id}/convert-single-range')
def convert_single_range(flow_id: int, revision_id: int, body: SingleRangeWrite):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow_recordings.assert_flow_idle(db, flow_id)
        row = db.execute('SELECT * FROM flow_recording_revisions WHERE flow_id=? AND id=?', (flow_id, revision_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Recording not found.')
        definition = json.loads(row['definition_json'])
        batch = definition.pop('date_batch', None)
        if not isinstance(batch, dict):
            raise HTTPException(409, 'This recording does not need single-range conversion.')
        try:
            parameters = definition['parameters']
            start, end = parameters[batch['start_parameter']], parameters[batch['end_parameter']]
            if batch['start_parameter'] == batch['end_parameter'] or start.get('step_id') == end.get('step_id'):
                raise ValueError('Choose distinct start and end input steps.')
            first = datetime.strptime(body.start, start.get('format', '%Y-%m-%d'))
            last = datetime.strptime(body.end, end.get('format', '%Y-%m-%d'))
            if first > last:
                raise ValueError('Start date must not be after end date.')
            start.update(mode='fixed', value=body.start, not_after=batch['end_parameter'])
            end.update(mode='fixed', value=body.end)
            definition.update(version=2, timezone='Asia/Dubai')
            flow_recording.validate_definition(definition, activation=False)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        cursor = db.execute('''INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at)
            VALUES (?,?,'draft',?)''', (flow_id, flow_recording.canonical(definition), datetime.now(timezone.utc).isoformat()))
    return {'revision_id': cursor.lastrowid}


@router.post('/{flow_id}/recordings/revisions/{revision_id}/validate')
def validate_revision(flow_id: int, revision_id: int, request: Request):
    try:
        with get_db() as db:
            db.execute('BEGIN IMMEDIATE')
            scan_id = flow_recordings.queue_operation(db, flow_id, 'validate', flows.get_actor(request), revision_id=revision_id)
        return _launch(scan_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{flow_id}/recordings/revisions/{revision_id}/activate')
def activate_revision(flow_id: int, revision_id: int):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow_recordings.assert_flow_idle(db, flow_id)
        if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')", (flow_id,)).fetchone():
            raise HTTPException(409, 'Wait for the active Flow run to finish.')
        row = db.execute("SELECT * FROM flow_recording_revisions WHERE flow_id=? AND id=? AND status='validated'", (flow_id, revision_id)).fetchone()
        if not row:
            raise HTTPException(409, 'Validate this revision successfully before activation.')
        db.execute("UPDATE flows SET execution_method='recorded',recording_revision_id=?,recording_review_reason=NULL WHERE id=?", (revision_id, flow_id))
        job = flows._build_job(db, flow_id)
        try:
            from app.flow_portable import generate
            result = generate(job)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
    return {'flow_id': flow_id, 'revision_id': revision_id, 'standalone': result}
