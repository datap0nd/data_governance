"""Task endpoints accept frozen run identities, never arbitrary commands."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import flow_parallel as parallel
from app.database import get_db

router = APIRouter(prefix='/api/flows/worker', tags=['flows'])


class Initialize(BaseModel):
    run_date: date
    completed_keys: list[str] = Field(default_factory=list, max_length=5000)


class TaskReport(BaseModel):
    lease_token: str = Field(min_length=32, max_length=32)
    status: Literal['running','succeeded','failed','cancelled']
    progress: dict = Field(default_factory=dict)
    artifacts: list[dict] = Field(default_factory=list, max_length=1)


class Abort(BaseModel):
    message: str = Field(min_length=1, max_length=10000)


@router.post('/{worker_id}/runs/{run_id}/tasks')
def initialize(worker_id: str, run_id: int, body: Initialize):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        return parallel.initialize(db, worker_id, run_id, body.run_date.isoformat(), body.completed_keys)


@router.post('/{worker_id}/runs/{run_id}/tasks/claim')
def claim_own_task(worker_id: str, run_id: int):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        parallel._owner(db, worker_id, run_id)
        return {'task': parallel.claim_task(db, worker_id, run_id=run_id), 'bundle': parallel.snapshot(db, run_id)}


@router.get('/{worker_id}/runs/{run_id}/tasks')
def status(worker_id: str, run_id: int):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        parallel._owner(db, worker_id, run_id)
        parallel.reap(db)
        return parallel.snapshot(db, run_id)


@router.post('/{worker_id}/tasks/{task_id}/progress')
def task_progress(worker_id: str, task_id: int, body: TaskReport):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        return parallel.report_task(db, worker_id, task_id, body.lease_token, body.status, body.progress, body.artifacts)


@router.post('/{worker_id}/runs/{run_id}/finalizer')
def finalizer(worker_id: str, run_id: int):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        return parallel.claim_finalizer(db, worker_id, run_id)


@router.post('/{worker_id}/runs/{run_id}/tasks/abort')
def abort(worker_id: str, run_id: int, body: Abort):
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        parallel._owner(db, worker_id, run_id)
        parallel.abort(db, run_id, body.message)
    parallel.stop_download_workers(run_id, exclude_worker=worker_id)
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        parallel.reap(db)
        return parallel.snapshot(db, run_id)
