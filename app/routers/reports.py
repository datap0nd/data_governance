from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import ReportOut, ReportUpdate, ReportTableOut

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", response_model=list[ReportOut])
def list_reports():
    with get_db() as db:
        rows = db.execute("""
            SELECT r.*,
                   (SELECT COUNT(DISTINCT rt.source_id)
                    FROM report_tables rt WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
                   ) AS source_count
            FROM reports r
            ORDER BY r.name
        """).fetchall()

    results = []
    for r in rows:
        status = _derive_report_status(r["id"])
        results.append(ReportOut(
            id=r["id"],
            name=r["name"],
            tmdl_path=r["tmdl_path"],
            owner=r["owner"],
            recipients=r["recipients"],
            frequency=r["frequency"],
            last_published=r["last_published"],
            status=status,
            source_count=r["source_count"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        ))
    return results


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: int):
    with get_db() as db:
        r = db.execute("""
            SELECT r.*,
                   (SELECT COUNT(DISTINCT rt.source_id)
                    FROM report_tables rt WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
                   ) AS source_count
            FROM reports r
            WHERE r.id = ?
        """, (report_id,)).fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Report not found")

    return ReportOut(
        id=r["id"],
        name=r["name"],
        tmdl_path=r["tmdl_path"],
        owner=r["owner"],
        recipients=r["recipients"],
        frequency=r["frequency"],
        last_published=r["last_published"],
        status=_derive_report_status(r["id"]),
        source_count=r["source_count"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.patch("/{report_id}", response_model=ReportOut)
def update_report(report_id: int, update: ReportUpdate):
    with get_db() as db:
        existing = db.execute("SELECT id FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Report not found")

        fields = []
        values = []
        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        if fields:
            values.append(report_id)
            db.execute(
                f"UPDATE reports SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    return get_report(report_id)


@router.get("/{report_id}/tables", response_model=list[ReportTableOut])
def get_report_tables(report_id: int):
    with get_db() as db:
        rows = db.execute("""
            SELECT rt.*, s.name AS source_name
            FROM report_tables rt
            LEFT JOIN sources s ON s.id = rt.source_id
            WHERE rt.report_id = ?
            ORDER BY rt.table_name
        """, (report_id,)).fetchall()

    return [
        ReportTableOut(
            id=r["id"],
            report_id=r["report_id"],
            table_name=r["table_name"],
            source_id=r["source_id"],
            source_name=r["source_name"],
            source_expression=r["source_expression"],
            last_scanned=r["last_scanned"],
        )
        for r in rows
    ]


def _derive_report_status(report_id: int) -> str:
    """Derive report status from its sources' latest probe statuses."""
    with get_db() as db:
        rows = db.execute("""
            SELECT sp.status
            FROM report_tables rt
            JOIN source_probes sp ON sp.source_id = rt.source_id
            WHERE rt.report_id = ?
            AND sp.id = (
                SELECT sp2.id FROM source_probes sp2
                WHERE sp2.source_id = rt.source_id
                ORDER BY sp2.probed_at DESC LIMIT 1
            )
        """, (report_id,)).fetchall()

    if not rows:
        return "unknown"

    statuses = [r["status"] for r in rows]
    if "error" in statuses:
        return "error"
    if "stale" in statuses:
        return "stale sources"
    return "current"
