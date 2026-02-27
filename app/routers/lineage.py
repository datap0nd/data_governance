from fastapi import APIRouter
from app.database import get_db
from app.models import LineageEdge

router = APIRouter(prefix="/api/lineage", tags=["lineage"])


@router.get("", response_model=list[LineageEdge])
def get_lineage():
    """Get all source-to-report lineage edges."""
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT
                s.id AS source_id,
                s.name AS source_name,
                s.type AS source_type,
                r.id AS report_id,
                r.name AS report_name
            FROM report_tables rt
            JOIN sources s ON s.id = rt.source_id
            JOIN reports r ON r.id = rt.report_id
            ORDER BY s.name, r.name
        """).fetchall()

    return [
        LineageEdge(
            source_id=r["source_id"],
            source_name=r["source_name"],
            source_type=r["source_type"],
            report_id=r["report_id"],
            report_name=r["report_name"],
        )
        for r in rows
    ]
