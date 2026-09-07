"""Compatibility response for the retired TMDL Checker.

PBIX/TMDL catalog discovery and lineage continue through the scanner. Historical
checker findings remain in SQLite; this route does not scan files or change them.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/best-practices", include_in_schema=False)


@router.get("")
def get_findings():
    raise HTTPException(
        status_code=410,
        detail="TMDL Checker has been retired. PBIX/TMDL catalog discovery and lineage remain available in Scanner and Pipelines.",
    )
