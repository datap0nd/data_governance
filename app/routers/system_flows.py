"""Capacity changes throttle future claims without interrupting current work."""
from fastapi import APIRouter, Request, HTTPException
from typing import Literal
from pydantic import BaseModel, Field, model_validator
import json

from app import flow_capacity, flow_paths, flow_parallel, flow_browser, flow_recording_timing
from app.database import get_db
from app.local_access import require_app_access
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix='/api/system/flows', tags=['flows'])


class CapacityWrite(BaseModel):
    headless_capacity: int | None = Field(default=None, ge=1, le=flow_capacity.MAX_SLOTS, strict=True)
    total_capacity: int | None = Field(default=None, ge=1, le=flow_capacity.MAX_SLOTS, strict=True)
    browser_channel: Literal['chrome', 'msedge'] | None = None
    headed_capacity: int | None = Field(default=None, ge=1, le=flow_capacity.MAX_SLOTS, strict=True)
    recording_wait_seconds: int | None = Field(default=None, ge=1, le=flow_recording_timing.MAX_SECONDS, strict=True)

    @model_validator(mode='after')
    def require_setting(self):
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError('Choose a Flows setting to update.')
        return self


class GscmDiscovery(BaseModel):
    modules: list[str] = Field(default_factory=list, max_length=50)
    module_control_suffix: str | None = Field(default=None, max_length=200, pattern=r'^\.[A-Za-z0-9_.]+$')
    scope_tabs: list[Literal['Private', 'Public', 'Custom']] = Field(default=['Private', 'Public', 'Custom'], min_length=1)
    diagnostic_grid: bool = False


class PortalCapacityWrite(BaseModel):
    capacity: int = Field(ge=1, le=flow_capacity.MAX_SLOTS, strict=True)
    gscm_discovery: GscmDiscovery | None = None


def _state(db):
    return {**flow_capacity.state(db), 'browser_channel': flow_browser.configured(db),
        'recording_wait_seconds': flow_recording_timing.configured(db), 'portals': [
        {'id': row['id'], 'name': row['name'], 'adapter': row['adapter'], 'capacity': flow_parallel.portal_limit(db, row['id']),
         'gscm_discovery': json.loads(flow_paths.setting(db, f"gscm_discovery:{row['id']}", '{}'))}
        for row in db.execute("SELECT id,name,adapter FROM flow_sites WHERE adapter IN ('asap_portal','gscm_portal','web_export') ORDER BY name")]}


@router.get('')
def get_capacity():
    with get_db() as db:
        return _state(db)


@router.put('')
def save_capacity(body: CapacityWrite, request: Request):
    require_app_access(request)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        if body.headless_capacity is not None:
            flow_paths.save_setting(db, flow_capacity.CAPACITY_KEY, body.headless_capacity)
        if body.total_capacity is not None:
            flow_paths.save_setting(db, flow_capacity.TOTAL_CAPACITY_KEY, body.total_capacity)
        if body.browser_channel is not None:
            flow_paths.save_setting(db, flow_browser.SETTING, body.browser_channel)
        if body.headed_capacity is not None:
            flow_paths.save_setting(db, flow_capacity.HEADED_CAPACITY_KEY, body.headed_capacity)
        if body.recording_wait_seconds is not None:
            flow_paths.save_setting(db, flow_recording_timing.SETTING, body.recording_wait_seconds)
        log_event(db, 'system', None, 'Flows', 'settings_changed',
                  f'total_capacity={flow_capacity.total_capacity(db)}, headless_capacity={flow_capacity.capacity(db, "headless")}, headed_capacity={flow_capacity.capacity(db, "headed")}, browser={flow_browser.configured(db)}, recording_wait_seconds={flow_recording_timing.configured(db)}', get_actor(request))
        return _state(db)


@router.put('/portals/{site_id}')
def save_portal_capacity(site_id: int, body: PortalCapacityWrite, request: Request):
    require_app_access(request)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM flow_sites WHERE id=? AND adapter IN ('asap_portal','gscm_portal','web_export')", (site_id,)).fetchone():
            raise HTTPException(404, 'Portal not found.')
        flow_paths.save_setting(db, f'flows_portal_capacity:{site_id}', body.capacity)
        if body.gscm_discovery is not None:
            site = db.execute('SELECT adapter FROM flow_sites WHERE id=?', (site_id,)).fetchone()
            if site['adapter'] != 'gscm_portal':
                raise HTTPException(422, 'Bookmark discovery settings apply only to GSCM.')
            flow_paths.save_setting(db, f'gscm_discovery:{site_id}', body.gscm_discovery.model_dump_json())
        log_event(db, 'flow_site', site_id, 'Portal capacity', 'capacity_changed', f'capacity={body.capacity}', get_actor(request))
        return _state(db)


@router.post('/start')
def start_capacity(request: Request, mode: Literal['headless', 'headed'] = 'headless'):
    require_app_access(request)
    from app.routers.flows import ensure_local_worker, launch_local_worker
    if mode == 'headed':
        return launch_local_worker('headed')
    return ensure_local_worker()
