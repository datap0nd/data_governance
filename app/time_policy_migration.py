"""One-time conversion of legacy host-wall-clock records to UTC storage."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.flow_clock import TIMEZONE

MARKER = 'dubai_time_policy_v1'


def initialize_time_policy(db, *, now=None, legacy_zone=None):
    if db.execute('SELECT 1 FROM app_settings WHERE key=?', (MARKER,)).fetchone():
        return
    now = now or datetime.now(timezone.utc)
    if legacy_zone is None:
        # Only migration reads the old machine zone. Runtime never uses it.
        from tzlocal import get_localzone_name
        legacy_zone = get_localzone_name()
    old_zone = ZoneInfo(legacy_zone)
    cutoff = now.astimezone(timezone.utc)
    tables = ('flows', 'flow_runs', 'flow_catalog_scans', 'flow_workers',
              'flow_sites', 'email_schedules', 'pbi_recurrences', 'pbi_recurrence_runs')
    db.execute('''CREATE TABLE IF NOT EXISTS time_policy_backup (
        table_name TEXT, row_id INTEGER, values_json TEXT NOT NULL,
        PRIMARY KEY(table_name,row_id))''')
    # These columns were written by the old router _now/_iso wall-clock helpers.
    # UTC monitoring/freshness evidence, task leases, JSON snapshots and source
    # timestamps are intentionally excluded.
    local_columns = {'last_run_at', 'last_success_at', 'last_scan_at', 'last_sent_at',
                     'claimed_at', 'started_at', 'finished_at', 'heartbeat_at', 'last_seen_at'}
    for table in tables:
        columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        chosen = columns & local_columns
        if table in {'flow_runs', 'flow_catalog_scans', 'pbi_recurrence_runs'}:
            chosen |= columns & {'created_at'}
        for row in db.execute(f'SELECT rowid AS migration_row_id,* FROM {table}').fetchall():
            original, converted = {}, {}
            for column in sorted(chosen):
                value = row[column]
                if not value:
                    continue
                try:
                    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                except (TypeError, ValueError):
                    continue
                if parsed.tzinfo is None:
                    original[column] = value
                    converted[column] = parsed.replace(tzinfo=old_zone).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
            if converted:
                db.execute('INSERT OR IGNORE INTO time_policy_backup VALUES (?,?,?)',
                           (table, row['migration_row_id'], json.dumps(original)))
                db.execute(f'UPDATE {table} SET ' + ','.join(f'{c}=?' for c in converted) + ' WHERE rowid=?',
                           (*converted.values(), row['migration_row_id']))
    from app.freshness import schedule_rule, next_occurrence

    def next_at(kind, clock, days, day=None):
        if isinstance(days, str):
            try:
                days = json.loads(days)
            except ValueError:
                days = days.split(',')
        if kind == 'weekdays':
            kind, days = 'weekly', ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
        rule = schedule_rule(kind, clock, days or [], day, timezone_name=TIMEZONE)
        return next_occurrence(rule, after=cutoff).replace(tzinfo=None).isoformat() if rule else None

    for row in db.execute('SELECT * FROM flows').fetchall():
        due = next_at(row['schedule_type'], row['schedule_time'], row['schedule_days'], row['schedule_day']) if row['enabled'] else None
        db.execute('UPDATE flows SET next_run_at=?,freshness_effective_from_at=? WHERE id=?',
                   (due, cutoff.isoformat(), row['id']))
    for table in ('email_schedules', 'pbi_recurrences'):
        for row in db.execute(f'SELECT * FROM {table}').fetchall():
            due = next_at(row['recurrence'], row['send_time'], row['weekdays'], row['month_day']) if row['enabled'] else None
            db.execute(f'UPDATE {table} SET next_run_at=? WHERE id=?', (due, row['id']))
    for row in db.execute('SELECT * FROM flow_sites').fetchall():
        due = next_at('weekly', row['discovery_time'], [row['discovery_weekday']]) if row['enabled'] and row['discovery_enabled'] else None
        db.execute('UPDATE flow_sites SET next_scan_at=? WHERE id=?', (due, row['id']))
    for row in db.execute('''SELECT r.* FROM flow_recording_revisions r
        WHERE r.id=(SELECT MAX(r2.id) FROM flow_recording_revisions r2 WHERE r2.flow_id=r.flow_id)''').fetchall():
        definition = json.loads(row['definition_json'])
        definition['timezone'] = TIMEZONE
        db.execute('''INSERT INTO flow_recording_revisions(flow_id,definition_json,status,created_at,evidence_json)
            VALUES (?,?,'draft',?,?)''', (row['flow_id'], json.dumps(definition), cutoff.isoformat(),
            json.dumps({'migration': MARKER, 'message': 'Test this recording before enabling it.'})))
        db.execute('UPDATE flows SET enabled=0,next_run_at=NULL WHERE id=?', (row['flow_id'],))
    db.execute('INSERT INTO app_settings(key,value) VALUES (?,?)',
               (MARKER, json.dumps({'cutoff': cutoff.isoformat(), 'legacy_zone': legacy_zone})))
