"""Shared capacity accounting and fixed local worker slot identities."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from app.flow_paths import setting

CAPACITY_KEY = 'flows_headless_capacity'
MAX_SLOTS = 5


def headless_capacity(db) -> int:
    try:
        return max(1, min(MAX_SLOTS, int(setting(db, CAPACITY_KEY, '1'))))
    except (TypeError, ValueError):
        return 1


def worker_id(slot: int) -> str:
    if type(slot) is not int or not 1 <= slot <= MAX_SLOTS:
        raise ValueError('Headless slot must be between 1 and 5.')
    return 'bi-desktop-headless' + (f'-{slot}' if slot > 1 else '')


def service_name(slot: int) -> str:
    worker_id(slot)
    return 'MXFlowsWorker' + (str(slot) if slot > 1 else '')


def slot_number(identity: str) -> int | None:
    return next((slot for slot in range(1, MAX_SLOTS + 1) if worker_id(slot) == identity), None)


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
    if mode == 'headless':
        occupied = {item['worker_id'] for item in result if item['worker_id']}
        for task in db.execute("SELECT id,worker_id FROM flow_download_tasks WHERE state IN ('claimed','cancelling')"):
            if task['worker_id'] not in occupied:
                result.append({'kind': 'task', 'id': task['id'], 'worker_id': task['worker_id']})
                occupied.add(task['worker_id'])
    return result


def can_claim(db, identity: str, mode: str) -> bool:
    capacity = 1 if mode == 'headed' else headless_capacity(db)
    slot = slot_number(identity)
    if mode == 'headless' and slot and slot > capacity:
        return False
    return len(assignments(db, mode)) < capacity


def state(db) -> dict:
    capacity = headless_capacity(db)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    rows = {row['worker_id']: dict(row) for row in db.execute('SELECT * FROM flow_workers')}
    slots = []
    for slot in range(1, MAX_SLOTS + 1):
        identity = worker_id(slot)
        row = rows.get(identity, {})
        online = bool(row.get('last_seen_at') and row['last_seen_at'] >= cutoff and row.get('status') != 'offline')
        slots.append({'slot': slot, 'worker_id': identity, 'service_name': service_name(slot),
                      'configured': slot <= capacity, 'online': online,
                      'status': row.get('status', 'not_registered') if online else 'offline',
                      'current_run_id': row.get('current_run_id'), 'current_scan_id': row.get('current_scan_id')})
        slots[-1]['current_task_id'] = row.get('current_task_id')
    return {'headless_capacity': capacity, 'headed_capacity': 1,
            'online_capacity': sum(item['online'] and item['configured'] for item in slots),
            'active_headless': len(assignments(db, 'headless')), 'active_headed': len(assignments(db, 'headed')),
            'slots': slots}
