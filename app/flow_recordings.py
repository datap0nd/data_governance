"""Recording revision lifecycle using the existing worker scan reservations."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException

from app import flow_recording, flow_browser


def reap(db, *, restarted_worker=None, timeout_seconds=180):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=timeout_seconds)
    sessions = db.execute('''SELECT c.* FROM flow_recording_sessions s
        JOIN flow_catalog_scans c ON c.id=s.scan_id WHERE c.status IN ('claimed','running')''').fetchall()
    for row in sessions:
        stamp = row['heartbeat_at'] or row['claimed_at']
        try:
            # Older scan claims use server-local naive timestamps; recording
            # heartbeats use aware UTC. astimezone handles both conventions.
            last = datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(timezone.utc) if stamp else now
        except (TypeError, ValueError):
            last = now
        if row['worker_id'] != restarted_worker and last >= cutoff:
            continue
        message = 'The recording worker restarted or its reservation expired. Start a new session.'
        db.execute("UPDATE flow_catalog_scans SET status='failed',error=?,finished_at=? WHERE id=?", (message,now.isoformat(),row['id']))
        db.execute("UPDATE flow_recording_revisions SET status='draft' WHERE id=(SELECT revision_id FROM flow_recording_sessions WHERE scan_id=?) AND status='validating'", (row['id'],))
        # Fence the old process until a new PID registers. A late result cannot
        # resurrect this terminal session or claim another profile operation.
        db.execute("""UPDATE flow_workers SET status='offline',current_scan_id=NULL,
            stop_requested_pid=COALESCE(json_extract(capabilities_json,'$.process_id'),-1)
            WHERE worker_id=? AND current_scan_id=?""", (row['worker_id'],row['id']))


def assert_flow_idle(db, flow_id):
    reap(db)
    active = db.execute('''SELECT c.id FROM flow_recording_sessions s
        JOIN flow_catalog_scans c ON c.id=s.scan_id
        WHERE s.flow_id=? AND c.status IN ('queued','claimed','running')''', (flow_id,)).fetchone()
    if active:
        raise HTTPException(409, 'Finish or cancel the active recording/validation before changing this Flow.')


def config_hash(job):
    clean = copy.deepcopy(job)
    for key in ('recording', 'recording_parameters', 'resume', 'sql_retry', 'job_type'):
        clean.pop(key, None)
    # Browser validation runs headed; the intended production mode remains a
    # compatibility attribute tested independently when changing it.
    clean.get('execution', {}).pop('worker_id', None)
    clean.get('execution', {}).pop('browser_channel', None)
    clean.get('downloads', {}).pop('network_replay', None)
    clean.pop('outlook_source', None)
    clean.pop('local_file', None)
    clean.get('report', {}).pop('automation', None)
    clean.get('report', {}).pop('filters', None)
    return flow_recording.digest(clean)


def attach_job(db, flow, job, *, allow_draft=False):
    job['execution']['download_parallelism'] = 1
    job['downloads']['network_replay'] = False
    for key in ('asap_download_type', 'export_report_title', 'export_filter_details'):
        job['downloads'].pop(key, None)
    revision = db.execute('SELECT * FROM flow_recording_revisions WHERE id=? AND flow_id=?',
                          (flow.get('recording_revision_id'), flow['id'])).fetchone()
    if not revision or revision['status'] != 'validated' or revision['config_hash'] != config_hash(job):
        if allow_draft:
            return
        raise HTTPException(409, 'Record and validate this Flow configuration before running or enabling it.')
    definition = flow_recording.validate_definition(json.loads(revision['definition_json']))
    from app.flow_portable import execution_hash
    engine_hash = json.loads(revision['evidence_json'] or '{}').get('engine_hash')
    if engine_hash != execution_hash():
        raise HTTPException(409, 'The recorded execution core changed; validate a new revision before running.')
    job['recording'] = {'revision': revision['id'], 'definition': definition,
                        'transformation_source': revision['transformation_source'],
                        'definition_hash': flow_recording.digest(definition), 'engine_hash': engine_hash}
    job['recording_parameters'] = flow_recording.resolve_parameters(definition)


def queue_operation(db, flow_id, operation, actor, *, revision_id=None):
    from app.routers import flows
    assert_flow_idle(db, flow_id)
    flow = flows._flow_out(db, flow_id)
    if flow['source_adapter'] not in {'asap_portal', 'gscm_portal'} or not flow.get('flow_folder'):
        raise HTTPException(409, 'Recording needs an ASAP/GSCM Flow with a managed folder.')
    if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')", (flow_id,)).fetchone():
        raise HTTPException(409, 'Wait for the active Flow run to finish.')
    from app.routers.pipelines import assert_resource_unlocked
    assert_resource_unlocked(db, 'flow', str(flow_id))
    site = dict(db.execute('SELECT * FROM flow_sites WHERE id=?', (flow['site_id'],)).fetchone())
    job = {'schema_version': 1, 'job_type': 'catalog_scan', 'recording_operation': operation,
           'recording_flow_id': flow_id, 'execution': {'browser_mode': 'headed', 'browser_channel': flow_browser.configured(db)}, 'browser_channel': flow_browser.configured(db),
           'site': {'id': site['id'], 'name': site['name'], 'adapter': site['adapter'],
                    'auth_url': site['auth_url'], 'base_url': site['base_url']},
           'flow_folder': flow['flow_folder'], 'report_url': flows._report_out(db, flow['report_id'])['report_url']}
    if operation == 'validate':
        row = db.execute('SELECT * FROM flow_recording_revisions WHERE id=? AND flow_id=?', (revision_id, flow_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Recording revision not found.')
        definition = flow_recording.validate_definition(json.loads(row['definition_json']))
        snapshot = flows._build_job(db, flow_id, recording_draft=True)
        from app.flow_portable import freeze_transformation, execution_hash
        snapshot['flow']['execution_method'] = 'recorded'
        snapshot['execution']['download_parallelism'] = 1
        snapshot['downloads']['network_replay'] = False
        for key in ('asap_download_type', 'export_report_title', 'export_filter_details'):
            snapshot['downloads'].pop(key, None)
        transform = freeze_transformation(snapshot)
        if row['status'] == 'validated' and (row['config_hash'] != config_hash(snapshot)
                or row['transformation_source'] != transform
                or json.loads(row['evidence_json'] or '{}').get('engine_hash') != execution_hash()):
            raise HTTPException(409, 'The configuration or execution core changed. Save a new reviewed revision before validation.')
        snapshot['recording'] = {'revision': revision_id, 'definition': definition,
                                  'definition_hash': flow_recording.digest(definition), 'transformation_source': transform, 'engine_hash': execution_hash()}
        snapshot['recording_parameters'] = flow_recording.resolve_parameters(definition)
        job.update(validation_job=snapshot, configuration_hash=config_hash(snapshot), browser_channel=flow_browser.configured(db))
        db.execute('UPDATE flow_recording_revisions SET status=?, config_hash=?, transformation_source=? WHERE id=?',
                   ('validating', job['configuration_hash'], transform, revision_id))
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.execute('''INSERT INTO flow_catalog_scans
        (site_id,trigger_type,status,requested_by,job_json,created_at) VALUES (?,?,'queued',?,?,?)''',
        (flow['site_id'], 'recording_' + operation, actor, json.dumps(job), now))
    scan_id = cursor.lastrowid
    db.execute('INSERT INTO flow_recording_sessions(scan_id,flow_id,operation,revision_id) VALUES (?,?,?,?)',
               (scan_id, flow_id, operation, revision_id))
    return scan_id


def update_operation(db, row, worker_id, body, now):
    session = db.execute('SELECT * FROM flow_recording_sessions WHERE scan_id=?', (row['id'],)).fetchone()
    if not session:
        raise HTTPException(409, 'Recording session no longer exists.')
    result = body.recording_result or {}
    if len(json.dumps(result)) > 1_000_000:
        raise HTTPException(422, 'Recording result exceeds its size limit.')
    terminal = body.status in {'succeeded', 'failed', 'cancelled'}
    if body.status == 'succeeded':
        if session['operation'] == 'record':
            definition = flow_recording.validate_definition(result.get('definition'), activation=False)
            cursor = db.execute('''INSERT INTO flow_recording_revisions
                (flow_id,definition_json,status,created_at) VALUES (?,?,'draft',?)''',
                (session['flow_id'], flow_recording.canonical(definition), now))
            db.execute('UPDATE flow_recording_sessions SET revision_id=? WHERE scan_id=?', (cursor.lastrowid, row['id']))
            result = {'revision_id': cursor.lastrowid}
        else:
            frozen = json.loads(row['job_json'])
            expected = frozen['validation_job']['recording']
            expected_steps = {step['id'] for step in flow_recording.walk_steps(expected['definition']['steps']) if step['action'] == 'download'}
            outputs = result.get('outputs', [])
            valid_outputs = isinstance(outputs, list) and all(isinstance(item, dict) for item in outputs)
            actual_steps = {item.get('step_id') for item in outputs} if valid_outputs else set()
            if (result.get('configuration_hash') != frozen['configuration_hash']
                    or result.get('engine_hash') != expected['engine_hash']
                    or actual_steps != expected_steps or len(outputs) != len(expected_steps)
                    or any(not isinstance(item.get('checksum'), str) or len(item['checksum']) != 64 for item in outputs)):
                raise HTTPException(422, 'Validation result does not match the frozen Flow configuration.')
            db.execute('UPDATE flow_recording_revisions SET status=?, evidence_json=?, validated_at=? WHERE id=?',
                       ('validated', json.dumps(result), now, session['revision_id']))
    elif terminal and session['revision_id'] and session['operation'] == 'validate':
        db.execute("UPDATE flow_recording_revisions SET status='draft' WHERE id=?", (session['revision_id'],))
    db.execute('''INSERT INTO flow_scan_events(scan_id,status,stage,message,details_json,created_at)
        VALUES (?,?,?,?,?,?)''', (row['id'], body.status, body.progress.get('stage'), body.progress.get('message'), json.dumps(body.progress), now))
    db.execute('''UPDATE flow_catalog_scans SET status=?,progress_json=?,result_json=?,error=?,
        started_at=COALESCE(started_at,?),finished_at=?,heartbeat_at=? WHERE id=?''',
        (body.status, json.dumps(body.progress), json.dumps(result), body.error, now, now if terminal else None, now, row['id']))
    if terminal:
        db.execute("UPDATE flow_workers SET current_scan_id=NULL,status='idle',last_seen_at=?,updated_at=?,last_error=? WHERE worker_id=?", (now, now, body.error, worker_id))
    else:
        db.execute("UPDATE flow_workers SET status='scanning',last_seen_at=?,updated_at=? WHERE worker_id=?", (now, now, worker_id))
    # Recording and validation never publish catalog snapshots or alter the
    # site's last successful discovery timestamp.
    return {'scan_id': row['id'], 'status': body.status, 'result': result}
