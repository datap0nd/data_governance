"""Organization-only Flow groups; batch runs reuse the individual run path."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.database import get_db
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix='/groups', tags=['flows'])
FlowId = Annotated[int, Field(strict=True, gt=0)]


class GroupWrite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    module: Literal['ASAP', 'GSCM', 'Outlook', 'Local', 'Web']
    classification: Literal['production', 'draft'] = 'production'
    flow_ids: list[FlowId] = Field(min_length=1, max_length=1000)
    version: int | None = Field(default=None, strict=True, ge=1)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Enter a group name.')
        return value


class GroupRun(BaseModel):
    flow_ids: list[FlowId] = Field(min_length=1, max_length=1000)


def module_of(flow):
    if flow['source_type'] == 'file':
        return 'Local'
    if flow['source_type'] == 'outlook':
        return 'Outlook'
    return {'asap_portal': 'ASAP', 'gscm_portal': 'GSCM'}.get(flow['source_adapter'], 'Web')


def _members(db, group_id):
    return db.execute('''SELECT f.id,f.name,f.source_type,f.classification,s.adapter AS source_adapter
        FROM flow_group_members m JOIN flows f ON f.id=m.flow_id
        JOIN flow_sites s ON s.id=f.site_id WHERE m.group_id=? ORDER BY f.id''', (group_id,)).fetchall()


def _get(db, group_id):
    group = db.execute('SELECT * FROM flow_groups WHERE id=?', (group_id,)).fetchone()
    if not group:
        raise HTTPException(404, 'This group no longer exists. Reopen the Flows list.')
    return {key: group[key] for key in ('id', 'name', 'module', 'classification', 'version')} | {
        'flow_ids': [row['id'] for row in _members(db, group_id)]}


def detach_if_scope_changed(db, flow_id):
    """A flow moved to another source or classification becomes ungrouped."""
    row = db.execute('''SELECT f.source_type,f.classification,s.adapter AS source_adapter,
        g.module,g.classification AS group_classification FROM flow_group_members m
        JOIN flow_groups g ON g.id=m.group_id JOIN flows f ON f.id=m.flow_id
        JOIN flow_sites s ON s.id=f.site_id WHERE f.id=?''', (flow_id,)).fetchone()
    if row and (module_of(row) != row['module'] or row['classification'] != row['group_classification']):
        db.execute('DELETE FROM flow_group_members WHERE flow_id=?', (flow_id,))


@router.get('')
def list_groups():
    with get_db() as db:
        return [_get(db, row['id']) for row in db.execute('SELECT id FROM flow_groups ORDER BY name_key,id')]


def _save(body, request, group_id=None):
    ids = sorted(set(body.flow_ids))
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        if group_id is not None:
            existing = _get(db, group_id)
            if body.version != existing['version']:
                raise HTTPException(409, 'This group changed elsewhere. Close and reopen the editor to review its current members.')
            if (body.module, body.classification) != (existing['module'], existing['classification']):
                raise HTTPException(422, 'A group must stay inside its source module and classification.')
        elif len(ids) < 2:
            raise HTTPException(422, 'Select at least 2 flows.')
        placeholders = ','.join('?' for _ in ids)
        selected = db.execute(f'''SELECT f.id,f.source_type,f.classification,s.adapter AS source_adapter
            FROM flows f JOIN flow_sites s ON s.id=f.site_id WHERE f.id IN ({placeholders})''', ids).fetchall()
        if len(selected) != len(ids):
            raise HTTPException(409, 'A selected flow no longer exists. Close and reopen the editor.')
        if any(module_of(flow) != body.module or flow['classification'] != body.classification for flow in selected):
            raise HTTPException(422, 'Choose flows from the same source module and classification.')
        name_key = body.name.casefold()
        duplicate = db.execute('SELECT id FROM flow_groups WHERE module=? AND classification=? AND name_key=?',
                               (body.module, body.classification, name_key)).fetchone()
        if duplicate and duplicate['id'] != group_id:
            raise HTTPException(409, f'A group named “{body.name}” already exists in {body.module}. Choose another name.')
        if group_id is None:
            group_id = db.execute('INSERT INTO flow_groups(name,name_key,module,classification) VALUES (?,?,?,?)',
                                  (body.name, name_key, body.module, body.classification)).lastrowid
        else:
            db.execute('UPDATE flow_groups SET name=?,name_key=?,version=version+1 WHERE id=?', (body.name, name_key, group_id))
        # Add/move first: replacing every member must not temporarily delete the group.
        for flow_id in ids:
            db.execute('''INSERT INTO flow_group_members(flow_id,group_id) VALUES (?,?)
                ON CONFLICT(flow_id) DO UPDATE SET group_id=excluded.group_id''', (flow_id, group_id))
        db.execute(f'DELETE FROM flow_group_members WHERE group_id=? AND flow_id NOT IN ({placeholders})', (group_id, *ids))
        log_event(db, 'flow_group', group_id, body.name, 'saved', f'flow_ids={ids}', get_actor(request))
        return _get(db, group_id)


@router.post('')
def create_group(body: GroupWrite, request: Request):
    return _save(body, request)


@router.put('/{group_id}')
def update_group(group_id: int, body: GroupWrite, request: Request):
    return _save(body, request, group_id)


@router.delete('/{group_id}')
def ungroup(group_id: int, request: Request, version: int):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        group = _get(db, group_id)
        if group['version'] != version:
            raise HTTPException(409, 'This group changed elsewhere. Reopen the Flows list before ungrouping.')
        db.execute('DELETE FROM flow_groups WHERE id=?', (group_id,))
        log_event(db, 'flow_group', group_id, group['name'], 'ungrouped', actor=get_actor(request))
        return {'message': f"{group['name']} ungrouped. All {len(group['flow_ids'])} flows kept."}


@router.post('/{group_id}/run')
def run_group(group_id: int, body: GroupRun, request: Request):
    from app.routers import flows
    actor = get_actor(request)
    jobs = []
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        group = _get(db, group_id)
        if sorted(set(body.flow_ids)) != group['flow_ids']:
            raise HTTPException(409, 'Group membership changed. Reopen the Flows list before running it. No flows queued.')
        now = flows._iso(flows._now())
        for member in _members(db, group_id):
            try:
                if module_of(member) != group['module'] or member['classification'] != group['classification']:
                    raise HTTPException(409, 'This flow moved to another source or classification. Edit the group first.')
                if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')", (member['id'],)).fetchone():
                    raise HTTPException(409, 'This flow already has an active run.')
                run_id, job = flows._queue_new_manual_run(db, member['id'], actor=actor, trigger_type='manual', now=now)
                jobs.append((run_id, member['id'], job))
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f"{member['name']}: {exc.detail} No flows queued.") from exc
        log_event(db, 'flow_group', group_id, group['name'], 'run_queued', f"run_ids={[item[0] for item in jobs]}", actor)
    # Start workers only after all jobs commit. A launcher failure leaves durable
    # queued jobs for the normal worker recovery / individual Start now action.
    workers = {}
    for mode in {job['execution']['browser_mode'] for _, _, job in jobs}:
        try:
            workers[mode] = flows.launch_local_worker(mode)
        except Exception:
            workers[mode] = {'status': 'error', 'message': 'Worker startup failed. Use Start now on the queued flow to retry.'}
    for run_id, _, job in jobs:
        worker = workers[job['execution']['browser_mode']]
        if worker.get('status') == 'error':
            with get_db() as db:
                db.execute("UPDATE flow_runs SET progress_json=? WHERE id=? AND status='queued'",
                           (flows._json({'stage': 'waiting_for_bi_desktop', 'message': worker.get('message')}), run_id))
    waiting = any(worker.get('status') == 'error' for worker in workers.values())
    return {'status': 'queued', 'runs': [{'id': run_id, 'flow_id': flow_id} for run_id, flow_id, _ in jobs],
            'message': f'{len(jobs)} flows queued together. ' + ('Worker startup needs attention; use Start now on a queued flow to retry.' if waiting else 'They will run as workers become available.')}
