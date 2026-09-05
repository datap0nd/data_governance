"""Retire batch execution without changing historical recordings or jobs."""
import json
from datetime import datetime, timezone

MARKER = 'recording_v2_migration'


def initialize_recording_v2(db):
    if db.execute('SELECT 1 FROM app_settings WHERE key=?', (MARKER,)).fetchone():
        return
    now = datetime.now(timezone.utc).isoformat()
    for row in db.execute('''SELECT r.* FROM flow_recording_revisions r WHERE r.id=(
        SELECT MAX(other.id) FROM flow_recording_revisions other WHERE other.flow_id=r.flow_id)''').fetchall():
        definition = json.loads(row['definition_json'])
        reason = 'date_batch_removed' if 'date_batch' in definition else 'recording_v2'
        db.execute('UPDATE flows SET enabled=0,next_run_at=NULL,recording_review_reason=? WHERE id=?',
                   (reason, row['flow_id']))
        if reason == 'recording_v2':
            definition.update(version=2, timezone='Asia/Dubai')
            db.execute('''INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at,evidence_json)
                VALUES (?,?,'draft',?,?)''', (row['flow_id'], json.dumps(definition), now,
                json.dumps({'migration': MARKER, 'message': 'Test this recording before enabling it.'})))
    for table in ('flow_runs', 'flow_catalog_scans'):
        for row in db.execute(f"SELECT id,job_json FROM {table} WHERE status='queued'").fetchall():
            job = json.loads(row['job_json'])
            recorded = job.get('validation_job', job).get('recording', {}).get('definition', {})
            if 'date_batch' in recorded:
                db.execute(f"UPDATE {table} SET status='cancelled',finished_at=?,error=? WHERE id=?",
                           (now, 'Date batching was removed. Convert this recording to a single range and test it.', row['id']))
    db.execute('INSERT INTO app_settings(key,value) VALUES (?,?)', (MARKER, now))
