from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import SourceOut, SourceUpdate

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
def list_sources():
    with get_db() as db:
        rows = db.execute("""
            SELECT s.*,
                   sp.status AS latest_status,
                   CAST(sp.last_data_at AS TEXT) AS latest_last_data_at,
                   (SELECT COUNT(*) FROM report_tables rt WHERE rt.source_id = s.id) AS report_count
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            ORDER BY s.name
        """).fetchall()

    return [
        SourceOut(
            id=r["id"],
            name=r["name"],
            type=r["type"],
            connection_info=r["connection_info"],
            source_query=r["source_query"],
            owner=r["owner"],
            refresh_schedule=r["refresh_schedule"],
            tags=r["tags"],
            discovered_by=r["discovered_by"],
            status=r["latest_status"] if r["latest_status"] else "unknown",
            last_updated=r["latest_last_data_at"],
            report_count=r["report_count"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int):
    with get_db() as db:
        r = db.execute("""
            SELECT s.*,
                   sp.status AS latest_status,
                   CAST(sp.last_data_at AS TEXT) AS latest_last_data_at,
                   (SELECT COUNT(*) FROM report_tables rt WHERE rt.source_id = s.id) AS report_count
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE s.id = ?
        """, (source_id,)).fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Source not found")

    return SourceOut(
        id=r["id"],
        name=r["name"],
        type=r["type"],
        connection_info=r["connection_info"],
        source_query=r["source_query"],
        owner=r["owner"],
        refresh_schedule=r["refresh_schedule"],
        tags=r["tags"],
        discovered_by=r["discovered_by"],
        status=r["latest_status"] or "unknown",
        last_updated=r["latest_last_data_at"],
        report_count=r["report_count"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, update: SourceUpdate):
    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")

        fields = []
        values = []
        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        if fields:
            values.append(source_id)
            db.execute(
                f"UPDATE sources SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    return get_source(source_id)


@router.get("/{source_id}/reports")
def get_source_reports(source_id: int):
    with get_db() as db:
        rows = db.execute("""
            SELECT r.id, r.name, r.owner, r.frequency, rt.table_name
            FROM report_tables rt
            JOIN reports r ON r.id = rt.report_id
            WHERE rt.source_id = ?
            ORDER BY r.name
        """, (source_id,)).fetchall()

    return [dict(r) for r in rows]


@router.get("/{source_id}/probes")
def get_source_probes(source_id: int):
    with get_db() as db:
        rows = db.execute("""
            SELECT * FROM source_probes
            WHERE source_id = ?
            ORDER BY probed_at DESC
            LIMIT 50
        """, (source_id,)).fetchall()

    return [dict(r) for r in rows]
