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

        # Pre-compute unused % for all reports
        unused_pct_map = {}
        for r in rows:
            rid = r["id"]
            total = db.execute("""
                SELECT (SELECT COUNT(*) FROM report_measures WHERE report_id = ?)
                     + (SELECT COUNT(*) FROM report_columns WHERE report_id = ?) AS total
            """, (rid, rid)).fetchone()["total"]
            if total > 0:
                used_fields = db.execute("""
                    SELECT DISTINCT vf.table_name, vf.field_name
                    FROM visual_fields vf
                    JOIN report_visuals rv ON rv.id = vf.visual_id
                    JOIN report_pages rp ON rp.id = rv.page_id
                    WHERE rp.report_id = ?
                """, (rid,)).fetchall()
                used_set = {(uf["table_name"], uf["field_name"]) for uf in used_fields}
                measures = db.execute(
                    "SELECT table_name, measure_name FROM report_measures WHERE report_id = ?", (rid,)
                ).fetchall()
                columns = db.execute(
                    "SELECT table_name, column_name FROM report_columns WHERE report_id = ?", (rid,)
                ).fetchall()
                unused = sum(1 for m in measures if (m["table_name"], m["measure_name"]) not in used_set)
                unused += sum(1 for c in columns if (c["table_name"], c["column_name"]) not in used_set)
                unused_pct_map[rid] = round(unused / total * 100)
            else:
                unused_pct_map[rid] = None

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
            unused_pct=unused_pct_map.get(r["id"]),
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


@router.get("/{report_id}/unused")
def get_unused(report_id: int):
    """Get unused measures, columns, and tables for a report."""
    with get_db() as db:
        # All measures and columns for this report
        all_measures = db.execute(
            "SELECT table_name, measure_name, measure_dax FROM report_measures WHERE report_id = ?",
            (report_id,),
        ).fetchall()
        all_columns = db.execute(
            "SELECT table_name, column_name FROM report_columns WHERE report_id = ?",
            (report_id,),
        ).fetchall()

        # All fields referenced by visuals in this report
        used_fields = db.execute("""
            SELECT DISTINCT vf.table_name, vf.field_name
            FROM visual_fields vf
            JOIN report_visuals rv ON rv.id = vf.visual_id
            JOIN report_pages rp ON rp.id = rv.page_id
            WHERE rp.report_id = ?
        """, (report_id,)).fetchall()

        used_set = {(r["table_name"], r["field_name"]) for r in used_fields}
        used_tables = {r["table_name"] for r in used_fields}

        # Unused measures
        unused_measures = []
        for m in all_measures:
            if (m["table_name"], m["measure_name"]) not in used_set:
                unused_measures.append({
                    "table_name": m["table_name"],
                    "name": m["measure_name"],
                    "dax": m["measure_dax"],
                })

        # Unused columns
        unused_columns = []
        for c in all_columns:
            if (c["table_name"], c["column_name"]) not in used_set:
                unused_columns.append({
                    "table_name": c["table_name"],
                    "name": c["column_name"],
                })

        # Unused tables — tables in report_tables not referenced by any visual
        all_tables = db.execute(
            "SELECT table_name FROM report_tables WHERE report_id = ?",
            (report_id,),
        ).fetchall()
        all_table_names = {r["table_name"] for r in all_tables}
        unused_tables = sorted(all_table_names - used_tables) if used_tables else []

        total_fields = len(all_measures) + len(all_columns)
        unused_fields = len(unused_measures) + len(unused_columns)
        unused_pct = round(unused_fields / total_fields * 100) if total_fields > 0 else 0

    return {
        "total_measures": len(all_measures),
        "total_columns": len(all_columns),
        "total_fields": total_fields,
        "unused_measures": unused_measures,
        "unused_columns": unused_columns,
        "unused_tables": unused_tables,
        "unused_fields_count": unused_fields,
        "unused_pct": unused_pct,
        "total_tables": len(all_table_names),
        "unused_tables_count": len(unused_tables),
    }


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
