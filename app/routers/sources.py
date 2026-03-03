from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import SourceOut, SourceUpdate, FreshnessRuleRequest

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
            custom_fresh_days=r["custom_fresh_days"],
            custom_stale_days=r["custom_stale_days"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
        if (r["latest_status"] or "unknown") not in ("unknown", "no_connection")
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
        custom_fresh_days=r["custom_fresh_days"],
        custom_stale_days=r["custom_stale_days"],
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


@router.put("/{source_id}/freshness-rule")
def set_freshness_rule(source_id: int, body: FreshnessRuleRequest):
    """Set custom freshness thresholds for a source."""
    if body.fresh_days >= body.stale_days:
        raise HTTPException(status_code=400, detail="fresh_days must be less than stale_days")
    if body.fresh_days < 1:
        raise HTTPException(status_code=400, detail="fresh_days must be at least 1")
    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")
        db.execute(
            "UPDATE sources SET custom_fresh_days = ?, custom_stale_days = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (body.fresh_days, body.stale_days, source_id),
        )
    return {"status": "ok", "fresh_days": body.fresh_days, "stale_days": body.stale_days}


@router.delete("/{source_id}/freshness-rule")
def delete_freshness_rule(source_id: int):
    """Reset freshness thresholds to global defaults."""
    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")
        db.execute(
            "UPDATE sources SET custom_fresh_days = NULL, custom_stale_days = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (source_id,),
        )
    return {"status": "ok", "fresh_days": None, "stale_days": None}
