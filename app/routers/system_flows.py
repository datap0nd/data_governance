"""Capacity changes throttle future claims without interrupting current work."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import flow_capacity, flow_paths
from app.database import get_db
from app.local_access import require_app_access
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix='/api/system/flows', tags=['flows'])


class CapacityWrite(BaseModel):
    headless_capacity: int = Field(ge=1, le=5, strict=True)


@router.get('')
def get_capacity():
    with get_db() as db:
        return flow_capacity.state(db)


@router.put('')
def save_capacity(body: CapacityWrite, request: Request):
    require_app_access(request)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        flow_paths.save_setting(db, flow_capacity.CAPACITY_KEY, body.headless_capacity)
        log_event(db, 'system', None, 'Flows', 'capacity_changed',
                  f'headless_capacity={body.headless_capacity}', get_actor(request))
        return flow_capacity.state(db)


@router.post('/start')
def start_capacity(request: Request):
    require_app_access(request)
    from app.routers.flows import ensure_local_worker
    return ensure_local_worker()
