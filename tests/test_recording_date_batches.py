import copy
import csv
import json
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from playwright.sync_api import sync_playwright

from app import database, flow_portable, flow_recording, flow_recordings, flow_tasks
from app import flow_recording_runtime as runtime
from app.routers import flows, flow_recordings as routes
from test_flow_recordings import definition, draft_job
from test_flows import flow_db, _request


def batched_definition():
    value = definition()
    value['date_batch'] = {'start_parameter':'start','end_parameter':'end','weeks':10}
    return value


@pytest.mark.parametrize('batch', [{}, None, {'weeks':10}])
def test_batch_presence_is_rejected_including_drafts(batch):
    value = definition(); value['date_batch'] = batch
    for activation in (False, True):
        with pytest.raises(ValueError, match='batch'):
            flow_recording.validate_definition(value, activation=activation)


def test_retirement_pauses_and_cancels_queued_only_preserving_history(flow_db):
    from app.recording_v2_migration import MARKER, initialize_recording_v2
    saved, job = draft_job()
    value = batched_definition(); job['recording']['definition'] = value
    original = json.dumps(value); frozen = json.dumps(job)
    with database.get_db() as db:
        db.execute('DELETE FROM app_settings WHERE key=?', (MARKER,))
        revision = db.execute("INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at) VALUES (?,?,'validated','2026-09-01')", (saved['id'],original)).lastrowid
        db.execute('UPDATE flows SET enabled=1,recording_revision_id=? WHERE id=?',(revision,saved['id']))
        for status in ('queued','running','succeeded'):
            db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual',?,?,'2026-09-01')", (saved['id'],status,frozen))
        initialize_recording_v2(db); initialize_recording_v2(db)
        flow = db.execute('SELECT * FROM flows WHERE id=?',(saved['id'],)).fetchone()
        assert not flow['enabled'] and flow['next_run_at'] is None and flow['recording_review_reason']=='date_batch_removed'
        rows = db.execute('SELECT * FROM flow_runs ORDER BY id').fetchall()
        assert [r['status'] for r in rows] == ['cancelled','running','succeeded']
        assert all(r['job_json']==frozen for r in rows)
        assert 'batching' in rows[0]['error']
        assert db.execute('SELECT definition_json FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()[0]==original


def test_conversion_requires_explicit_dates_and_preserves_original(flow_db):
    saved, _ = draft_job(); original = json.dumps(batched_definition())
    with database.get_db() as db:
        revision = db.execute("INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at) VALUES (?,?,'validated','2026-09-01')",(saved['id'],original)).lastrowid
    with pytest.raises(HTTPException) as error:
        routes.convert_single_range(saved['id'],revision,routes.SingleRangeWrite(start='2025-02-01',end='2025-01-01'))
    assert error.value.status_code == 422
    new = routes.convert_single_range(saved['id'],revision,routes.SingleRangeWrite(start='2025-01-01',end='2025-02-01'))['revision_id']
    with database.get_db() as db:
        row=db.execute('SELECT * FROM flow_recording_revisions WHERE id=?',(new,)).fetchone()
        value=json.loads(row['definition_json'])
        assert row['status']=='draft' and value['version']==2 and 'date_batch' not in value
        assert value['parameters']['start']['value']=='2025-01-01'
        assert value['parameters']['end']['value']=='2025-02-01'
        assert db.execute('SELECT definition_json FROM flow_recording_revisions WHERE id=?',(revision,)).fetchone()[0]==original
    with pytest.raises(HTTPException):
        routes.save_revision(saved['id'],routes.RevisionWrite(definition=batched_definition()))


def test_retired_job_cannot_be_retried_into_sql(flow_db):
    saved,job=draft_job(); job['recording']['definition']=batched_definition(); job['sql_handoff']['enabled']=True
    with database.get_db() as db:
        run=db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','failed',?,'2026-09-01')",(saved['id'],json.dumps(job))).lastrowid
        assert flows.inspect_sql_retry_eligibility(db,run,verify_artifact_files=False)['reason_code']=='date_batch_removed'
    with pytest.raises(ValueError,match='batch'):
        flow_tasks.task_matrix(job)
