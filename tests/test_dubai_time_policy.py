import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app import config, database, flow_clock
from app.routers import flows, recurrences, email_schedules
from app.time_policy_migration import MARKER, initialize_time_policy


def test_saved_clock_times_use_dubai_and_store_utc(monkeypatch):
    assert config.FLOW_TIMEZONE == 'Asia/Dubai'
    monkeypatch.setattr(flows, '_now', lambda: datetime(2026, 9, 5, 4, 30))
    assert flows._schedule_next('daily', '09:00', []) == datetime(2026, 9, 5, 5)
    for calculate in (recurrences.calculate_next_run, email_schedules._calculate_next_run):
        assert calculate('daily', '09:00', [], None, datetime(2026, 9, 5, 4, 30)) == datetime(2026, 9, 5, 5)
        assert calculate('daily', '09:00', [], None, datetime(2026, 9, 5, 5)) == datetime(2026, 9, 6, 5)


def test_dubai_calendar_crosses_midnight_independent_of_host(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, zone):
            return datetime(2026, 12, 31, 21, tzinfo=timezone.utc).astimezone(zone)
    monkeypatch.setattr(flow_clock, 'datetime', Clock)
    assert flow_clock.dubai_today().isoformat() == '2027-01-01'


def test_migration_keeps_instants_clock_hours_and_frozen_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'clock.db'))
    database.init_db()
    with database.get_db() as db:
        db.execute('DELETE FROM app_settings WHERE key=?', (MARKER,))
        db.execute("INSERT INTO flow_sites(id,name) VALUES (100,'Test')")
        db.execute("INSERT INTO flow_reports(id,site_id,name,report_url) VALUES (100,100,'Report','https://example.test/report')")
        db.execute("""INSERT INTO flows(id,name,site_id,report_id,target_folder,filename_template,
            enabled,schedule_type,schedule_time,last_run_at) VALUES (100,'Test',100,100,'test','test',1,'daily','09:00','2026-07-01T09:00:00')""")
        db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at,finished_at) VALUES (100,'manual','succeeded',?, '2026-07-01T09:00:00','2026-07-01T08:05:00Z')", ('{"frozen":"unchanged"}',))
        initialize_time_policy(db, now=datetime(2026, 9, 5, 6, tzinfo=timezone.utc), legacy_zone='Europe/Lisbon')
        flow = db.execute('SELECT * FROM flows WHERE id=100').fetchone()
        assert flow['schedule_time'] == '09:00'
        assert flow['next_run_at'] == '2026-09-06T05:00:00'
        assert flow['last_run_at'] == '2026-07-01T08:00:00'
        run = db.execute('SELECT * FROM flow_runs').fetchone()
        assert run['job_json'] == '{"frozen":"unchanged"}'
        assert run['created_at'] == '2026-07-01T08:00:00'
        assert run['finished_at'] == '2026-07-01T08:05:00Z'
        original = dict(flow)
        initialize_time_policy(db, now=datetime(2027, 1, 1, tzinfo=timezone.utc), legacy_zone='UTC')
        assert dict(db.execute('SELECT * FROM flows').fetchone()) == original


def test_recording_migration_creates_draft_and_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'record.db'))
    database.init_db()
    with database.get_db() as db:
        db.execute('DELETE FROM app_settings WHERE key=?', (MARKER,))
        db.execute("INSERT INTO flow_sites(id,name) VALUES (100,'Test')")
        db.execute("INSERT INTO flow_reports(id,site_id,name,report_url) VALUES (100,100,'Report','https://example.test/report')")
        db.execute("INSERT INTO flows(id,name,site_id,report_id,target_folder,filename_template,enabled) VALUES (100,'Test',100,100,'test','test',1)")
        original = json.dumps({'version':1,'timezone':'Europe/Lisbon','parameters':{'start':{'value':'2025-01-01'}}})
        db.execute("INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at) VALUES (100,?,'validated','2026-09-01')", (original,))
        initialize_time_policy(db, legacy_zone='UTC')
        revisions = db.execute('SELECT * FROM flow_recording_revisions ORDER BY id').fetchall()
        assert len(revisions) == 2 and revisions[0]['definition_json'] == original
        definition = json.loads(revisions[1]['definition_json'])
        assert revisions[1]['status'] == 'draft' and definition['timezone'] == 'Asia/Dubai'
        assert definition['parameters']['start']['value'] == '2025-01-01'
        assert not db.execute('SELECT enabled FROM flows').fetchone()[0]


def test_display_uses_dubai_even_in_another_browser_timezone():
    source = Path('app/static/app.js').read_text(encoding='utf-8')
    start = source.index('function parseAppTimestamp(')
    end = source.index('function exportTableCSV(', start)
    script = source[start:end] + "\nconsole.log(formatCompactTimestamp('2026-12-31T21:30:00'));console.log(formatCompactTimestamp('2026-12-31T21:30:00Z'));"
    import os
    result = subprocess.run(['node','-e',script], env={**os.environ,'TZ':'America/New_York'}, capture_output=True,text=True,check=True)
    assert result.stdout.splitlines() == ['2027-01-01-01-30', '2027-01-01-01-30']
