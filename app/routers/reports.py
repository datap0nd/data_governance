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
        status, worst_date = _derive_report_status(r["id"])
        results.append(ReportOut(
            id=r["id"],
            name=r["name"],
            tmdl_path=r["tmdl_path"],
            owner=r["owner"],
            business_owner=r["business_owner"],
            recipients=r["recipients"],
            frequency=r["frequency"],
            last_published=r["last_published"],
            powerbi_url=r["powerbi_url"],
            status=status,
            source_count=r["source_count"],
            worst_source_updated=worst_date,
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

    status, worst_date = _derive_report_status(r["id"])
    return ReportOut(
        id=r["id"],
        name=r["name"],
        tmdl_path=r["tmdl_path"],
        owner=r["owner"],
        business_owner=r["business_owner"],
        recipients=r["recipients"],
        frequency=r["frequency"],
        last_published=r["last_published"],
        powerbi_url=r["powerbi_url"],
        status=status,
        source_count=r["source_count"],
        worst_source_updated=worst_date,
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


@router.get("/{report_id}/visuals")
def get_report_visuals(report_id: int):
    """Get all visuals grouped by page for a report."""
    with get_db() as db:
        rows = db.execute("""
            SELECT rp.page_name, rp.page_ordinal,
                   rv.visual_id, rv.visual_type, rv.title,
                   vf.table_name, vf.field_name
            FROM report_pages rp
            JOIN report_visuals rv ON rv.page_id = rp.id
            LEFT JOIN visual_fields vf ON vf.visual_id = rv.id
            WHERE rp.report_id = ?
            ORDER BY rp.page_ordinal, rv.visual_type, vf.table_name, vf.field_name
        """, (report_id,)).fetchall()

    # Group into nested structure: pages -> visuals -> fields
    pages = {}
    for r in rows:
        page_key = r["page_name"]
        if page_key not in pages:
            pages[page_key] = {
                "page_name": r["page_name"],
                "page_ordinal": r["page_ordinal"],
                "visuals": {},
            }
        vis_key = r["visual_id"]
        if vis_key not in pages[page_key]["visuals"]:
            pages[page_key]["visuals"][vis_key] = {
                "visual_id": r["visual_id"],
                "visual_type": r["visual_type"],
                "title": r["title"],
                "fields": [],
            }
        if r["table_name"]:
            pages[page_key]["visuals"][vis_key]["fields"].append({
                "table": r["table_name"],
                "field": r["field_name"],
            })

    # Convert to sorted list
    result = []
    for p in sorted(pages.values(), key=lambda x: x["page_ordinal"]):
        result.append({
            "page_name": p["page_name"],
            "page_ordinal": p["page_ordinal"],
            "visuals": list(p["visuals"].values()),
        })
    return result


def _derive_report_status(report_id: int) -> tuple[str, str | None]:
    """Derive report status and worst source date from its sources' latest probe statuses.

    Returns (status, worst_source_updated) tuple.
    """
    with get_db() as db:
        rows = db.execute("""
            SELECT sp.status, CAST(sp.last_data_at AS TEXT) AS last_data_at
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
        return "current", None

    # Ignore unknown/no_connection sources — they shouldn't affect report status
    known_rows = [r for r in rows if r["status"] not in ("unknown", "no_connection", None)]
    if not known_rows:
        return "current", None

    statuses = [r["status"] for r in known_rows]
    dates = [r["last_data_at"] for r in known_rows if r["last_data_at"]]
    worst_date = min(dates) if dates else None

    if "outdated" in statuses or "error" in statuses:
        return "degraded", worst_date
    if "stale" in statuses:
        return "at risk", worst_date
    return "healthy", worst_date
