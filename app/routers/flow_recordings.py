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
        return {'status': row['status'], 'finish_requested': bool(json.loads(row['job_json']).get('finish_requested'))}


class RecordingDraft(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    site_id: int
    report_id: int | None = None
    report_url: str | None = None


class RevisionWrite(BaseModel):
    definition: dict


def _launch(scan_id):
    from app.flow_local_runner import launch_local_worker
    return {'scan_id': scan_id, 'worker': launch_local_worker('headed')}


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
            item['definition'] = json.loads(item.pop('definition_json'))
            item['evidence'] = json.loads(item.pop('evidence_json') or '{}')
            item.pop('transformation_source', None)
            revisions.append(item)
        sessions = [dict(row) for row in db.execute('''SELECT s.*, c.status,c.progress_json,c.error,c.result_json
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
        if not db.execute('SELECT 1 FROM flow_recording_sessions WHERE scan_id=? AND flow_id=?', (scan_id, flow_id)).fetchone():
            raise HTTPException(404, 'Recording session not found.')
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
            job['finish_requested'] = True
            db.execute('UPDATE flow_catalog_scans SET job_json=? WHERE id=?', (json.dumps(job), scan_id))
    return {'scan_id': scan_id, 'message': 'Finish requested. Close the recording browser to save its final actions.'}


@router.post('/{flow_id}/recordings/revisions')
def save_revision(flow_id: int, body: RevisionWrite):
    try:
        definition = flow_recording.validate_definition(body.definition, activation=False)
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
        db.execute("UPDATE flows SET execution_method='recorded',recording_revision_id=? WHERE id=?", (revision_id, flow_id))
        job = flows._build_job(db, flow_id)
        try:
            from app.flow_portable import generate
            result = generate(job)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
    return {'flow_id': flow_id, 'revision_id': revision_id, 'standalone': result}
