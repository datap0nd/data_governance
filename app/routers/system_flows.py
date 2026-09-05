"""Capacity changes throttle future claims without interrupting current work."""
from fastapi import APIRouter, Request, HTTPException
from typing import Literal
from pydantic import BaseModel, Field

from app import flow_capacity, flow_paths, flow_parallel
from app.database import get_db
from app.local_access import require_app_access
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix='/api/system/flows', tags=['flows'])


class CapacityWrite(BaseModel):
    headless_capacity: int = Field(ge=1, le=5, strict=True)
    headed_capacity: int | None = Field(default=None, ge=1, le=5, strict=True)


class PortalCapacityWrite(BaseModel):
    capacity: int = Field(ge=1, le=5, strict=True)


def _state(db):
    return {**flow_capacity.state(db), 'portals': [
        {'id': row['id'], 'name': row['name'], 'capacity': flow_parallel.portal_limit(db, row['id'])}
        for row in db.execute("SELECT id,name FROM flow_sites WHERE adapter IN ('asap_portal','gscm_portal','web_export') ORDER BY name")]}


@router.get('')
def get_capacity():
    with get_db() as db:
        return _state(db)


@router.put('')
def save_capacity(body: CapacityWrite, request: Request):
    require_app_access(request)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow_paths.save_setting(db, flow_capacity.CAPACITY_KEY, body.headless_capacity)
        if body.headed_capacity is not None:
            flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, body.headed_capacity)
        log_event(db, 'system', None, 'Flows', 'capacity_changed',
                  f'headless_capacity={body.headless_capacity}, headed_capacity={flow_capacity.capacity(db, "headed")}', get_actor(request))
        return _state(db)


@router.put('/portals/{site_id}')
def save_portal_capacity(site_id: int, body: PortalCapacityWrite, request: Request):
    require_app_access(request)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM flow_sites WHERE id=? AND adapter IN ('asap_portal','gscm_portal','web_export')", (site_id,)).fetchone():
            raise HTTPException(404, 'Portal not found.')
        flow_paths.save_setting(db, f'flows_portal_capacity:{site_id}', body.capacity)
        log_event(db, 'flow_site', site_id, 'Portal capacity', 'capacity_changed', f'capacity={body.capacity}', get_actor(request))
        return _state(db)


@router.post('/start')
def start_capacity(request: Request, mode: Literal['headless', 'headed'] = 'headless'):
    require_app_access(request)
    from app.routers.flows import ensure_local_worker, launch_local_worker
    if mode == 'headed':
        return launch_local_worker('headed')
    return ensure_local_worker()
