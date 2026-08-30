"""Operator-facing structured application errors."""

from fastapi import APIRouter, Query

from app.operator_errors import read_operator_errors


router = APIRouter(prefix="/api/system/errors", tags=["system-errors"])


@router.get("")
def list_operator_errors(
    limit: int = Query(default=100, ge=1, le=250),
    area: str | None = Query(default=None, max_length=80),
    search: str | None = Query(default=None, max_length=200),
):
    return read_operator_errors(limit=limit, area=area, search=search)
