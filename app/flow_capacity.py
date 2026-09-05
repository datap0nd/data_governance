"""Shared capacity accounting and fixed local worker slot identities."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from app.flow_paths import setting

CAPACITY_KEY = 'flows_headless_capacity'
HEADED_CAPACITY_KEY = 'flows_headed_capacity'
MAX_SLOTS = 5


def headless_capacity(db) -> int:
    return capacity(db, 'headless')


def capacity(db, mode: str) -> int:
    key = HEADED_CAPACITY_KEY if mode == 'headed' else CAPACITY_KEY
    try:
        return max(1, min(MAX_SLOTS, int(setting(db, key, '1'))))
    except (TypeError, ValueError):
        return 1


def worker_id(slot: int, mode: str = 'headless') -> str:
    if mode not in {'headless', 'headed'}:
        raise ValueError('Unsupported browser mode.')
    if type(slot) is not int or not 1 <= slot <= MAX_SLOTS:
        raise ValueError('Worker slot must be between 1 and 5.')
    return f'bi-desktop-{mode}' + (f'-{slot}' if slot > 1 else '')


def service_name(slot: int) -> str:
    worker_id(slot)
    return 'MXFlowsWorker' + (str(slot) if slot > 1 else '')


def task_name(slot: int) -> str:
    worker_id(slot, 'headed')
    return 'Metronome_Flows_Headed' + (str(slot) if slot > 1 else '')


def slot_number(identity: str, mode: str = 'headless') -> int | None:
    return next((slot for slot in range(1, MAX_SLOTS + 1) if worker_id(slot, mode) == identity), None)


def pending_work(db, mode: str) -> bool:
    """Keep on-demand helpers alive while a coordinator prepares its downloads."""
    rows = db.execute("""SELECT job_json FROM flow_runs WHERE status IN ('queued','claimed','running')
        UNION ALL SELECT job_json FROM flow_catalog_scans WHERE status IN ('queued','claimed','running')""")
    return any(json.loads(row['job_json'] or '{}').get('execution', {}).get('browser_mode', 'headless') == mode for row in rows)


def assignments(db, mode: str) -> list[dict]:
    """Call under the claim transaction; include scans and every run phase."""
    rows = db.execute("""SELECT 'run' AS kind, id, worker_id, job_json FROM flow_runs
        WHERE status IN ('claimed','running') UNION ALL
        SELECT 'scan', id, worker_id, job_json FROM flow_catalog_scans
        WHERE status IN ('claimed','running')""").fetchall()
    result = []
    for row in rows:
        job = json.loads(row['job_json'] or '{}')
        if job.get('execution', {}).get('browser_mode', 'headless') == mode:
            result.append({'kind': row['kind'], 'id': row['id'], 'worker_id': row['worker_id']})
    occupied = {item['worker_id'] for item in result if item['worker_id']}
    for task in db.execute("""SELECT t.id,t.worker_id,r.job_json FROM flow_download_tasks t
            JOIN flow_runs r ON r.id=t.run_id WHERE t.state IN ('claimed','cancelling')"""):
        job = json.loads(task['job_json'] or '{}')
        if job.get('execution', {}).get('browser_mode', 'headless') == mode and task['worker_id'] not in occupied:
            result.append({'kind': 'task', 'id': task['id'], 'worker_id': task['worker_id']})
            occupied.add(task['worker_id'])
    return result


def can_claim(db, identity: str, mode: str) -> bool:
    limit = capacity(db, mode)
    slot = slot_number(identity, mode)
    if slot and slot > limit:
        return False
    return len(assignments(db, mode)) < limit


def state(db) -> dict:
    background = _slot_state(db, 'headless')
    visible = _slot_state(db, 'headed')
    return {'headless_capacity': capacity(db, 'headless'), 'headed_capacity': capacity(db, 'headed'),
            'online_capacity': sum(item['online'] and item['configured'] for item in background),
            'online_headed_capacity': sum(item['online'] and item['configured'] for item in visible),
            'active_headless': len(assignments(db, 'headless')), 'active_headed': len(assignments(db, 'headed')),
            'slots': background, 'headed_slots': visible}


def _slot_state(db, mode: str) -> list[dict]:
    limit = capacity(db, mode)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    rows = {row['worker_id']: dict(row) for row in db.execute('SELECT * FROM flow_workers')}
    slots = []
    for slot in range(1, MAX_SLOTS + 1):
        identity = worker_id(slot, mode)
        row = rows.get(identity, {})
        online = bool(row.get('last_seen_at') and row['last_seen_at'] >= cutoff and row.get('status') != 'offline')
        slots.append({'slot': slot, 'worker_id': identity,
                      **({'task_name': task_name(slot)} if mode == 'headed' else {'service_name': service_name(slot)}),
                      'configured': slot <= limit, 'online': online,
                      'status': row.get('status', 'not_registered') if online else 'offline',
                      'current_run_id': row.get('current_run_id'), 'current_scan_id': row.get('current_scan_id')})
        slots[-1]['current_task_id'] = row.get('current_task_id')
    return slots
