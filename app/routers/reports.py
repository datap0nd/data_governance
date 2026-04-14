from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query
from app.database import get_db
from app.models import ReportOut, ReportUpdate, ReportTableOut

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", response_model=list[ReportOut])
def list_reports(include_archived: bool = Query(False)):
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE r.archived = 0"
        rows = db.execute(f"""
            SELECT r.*,
                   (SELECT COUNT(DISTINCT rt.source_id)
                    FROM report_tables rt WHERE rt.report_id = r.id AND rt.source_id IS NOT NULL
                   ) AS source_count,
                   (SELECT COUNT(*) FROM task_links tl
                    JOIN tasks t ON t.id = tl.task_id
                    WHERE tl.entity_type = 'report' AND tl.entity_id = r.id
                    AND t.status != 'done'
                   ) AS linked_task_count
            FROM reports r
            {archive_filter}
            ORDER BY r.name
        """).fetchall()

        # Batch: compute unused_pct for all reports in a few queries
        unused_pct_map = _batch_unused_pct(db)

        # Batch: derive report status for all reports in one query
        status_map = _batch_report_statuses(db)

        # Attach 30-day view counts
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        view_counts = db.execute(
            """SELECT report_id, SUM(view_count) as views, SUM(unique_users) as users
               FROM pbi_report_views
               WHERE view_date >= ? AND report_id IS NOT NULL
               GROUP BY report_id""",
            (cutoff,)
        ).fetchall()
        views_map = {r["report_id"]: {"views_30d": r["views"], "unique_users_30d": r["users"]} for r in view_counts}

    results = []
    for r in rows:
        rid = r["id"]
        status, worst_date = status_map.get(rid, ("current", None))
        view_data = views_map.get(rid, {})
        results.append(ReportOut(
            id=rid,
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
            unused_pct=unused_pct_map.get(rid),
            pbi_dataset_id=r["pbi_dataset_id"],
            pbi_refresh_schedule=r["pbi_refresh_schedule"],
            pbi_last_refresh_at=r["pbi_last_refresh_at"],
            pbi_refresh_status=r["pbi_refresh_status"],
            pbi_refresh_error=r["pbi_refresh_error"],
            linked_task_count=r["linked_task_count"],
            views_30d=view_data.get("views_30d"),
            unique_users_30d=view_data.get("unique_users_30d"),
            archived=bool(r["archived"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        ))
    return results


@router.get("/all-measures")
def all_measures():
    """Get all measures across all reports (bulk export)."""
    with get_db() as db:
        rows = db.execute("""
            SELECT rm.report_id, r.name AS report_name,
                   rm.table_name, rm.measure_name, rm.measure_dax
            FROM report_measures rm
            JOIN reports r ON r.id = rm.report_id
            ORDER BY r.name, rm.table_name, rm.measure_name
        """).fetchall()
    return [dict(r) for r in rows]


@router.get("/all-columns")
def all_columns():
    """Get all columns across all reports (bulk export)."""
    with get_db() as db:
        rows = db.execute("""
            SELECT rc.report_id, r.name AS report_name,
                   rc.table_name, rc.column_name
            FROM report_columns rc
            JOIN reports r ON r.id = rc.report_id
            ORDER BY r.name, rc.table_name, rc.column_name
        """).fetchall()
    return [dict(r) for r in rows]


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
        pbi_dataset_id=r["pbi_dataset_id"],
        pbi_refresh_schedule=r["pbi_refresh_schedule"],
        pbi_last_refresh_at=r["pbi_last_refresh_at"],
        pbi_refresh_status=r["pbi_refresh_status"],
        pbi_refresh_error=r["pbi_refresh_error"],
        archived=bool(r["archived"]),
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
        result = _batch_report_statuses(db)
    return result.get(report_id, ("current", None))


def _batch_report_statuses(db) -> dict[int, tuple[str, str | None]]:
    """Compute report statuses for all reports in a single query.

    Returns {report_id: (status, worst_source_updated)}.
    """
    rows = db.execute("""
        SELECT rt.report_id, sp.status, CAST(sp.last_data_at AS TEXT) AS last_data_at
        FROM report_tables rt
        JOIN sources s ON s.id = rt.source_id
        JOIN source_probes sp ON sp.source_id = rt.source_id
        WHERE sp.id = (
            SELECT sp2.id FROM source_probes sp2
            WHERE sp2.source_id = rt.source_id
            ORDER BY sp2.probed_at DESC LIMIT 1
        )
        AND COALESCE(sp.status, 'unknown') NOT IN ('unknown', 'no_connection')
        AND COALESCE(s.archived, 0) = 0
    """).fetchall()

    # Group by report_id
    report_data: dict[int, list] = {}
    for r in rows:
        report_data.setdefault(r["report_id"], []).append(r)

    result = {}
    for rid, probes in report_data.items():
        statuses = [p["status"] for p in probes]
        dates = [p["last_data_at"] for p in probes if p["last_data_at"]]
        worst_date = min(dates) if dates else None

        if "outdated" in statuses or "error" in statuses or "stale" in statuses:
            result[rid] = ("degraded", worst_date)
        else:
            result[rid] = ("healthy", worst_date)

    return result


def _batch_unused_pct(db) -> dict[int, int | None]:
    """Compute unused_pct for all reports in a few aggregate queries.

    Returns {report_id: unused_pct_or_None}.
    """
    # Total fields per report (measures + columns)
    totals = {}
    for row in db.execute("""
        SELECT report_id, COUNT(*) AS cnt FROM (
            SELECT report_id, table_name, measure_name AS field_name FROM report_measures
            UNION ALL
            SELECT report_id, table_name, column_name AS field_name FROM report_columns
        ) GROUP BY report_id
    """).fetchall():
        totals[row["report_id"]] = row["cnt"]

    if not totals:
        return {}

    # All defined fields (measures + columns) per report
    all_fields: dict[int, set] = {}
    for row in db.execute("""
        SELECT report_id, table_name, measure_name AS field_name FROM report_measures
        UNION ALL
        SELECT report_id, table_name, column_name AS field_name FROM report_columns
    """).fetchall():
        all_fields.setdefault(row["report_id"], set()).add((row["table_name"], row["field_name"]))

    # All used fields per report (referenced by visuals)
    used_fields: dict[int, set] = {}
    for row in db.execute("""
        SELECT rp.report_id, vf.table_name, vf.field_name
        FROM visual_fields vf
        JOIN report_visuals rv ON rv.id = vf.visual_id
        JOIN report_pages rp ON rp.id = rv.page_id
    """).fetchall():
        used_fields.setdefault(row["report_id"], set()).add((row["table_name"], row["field_name"]))

    result = {}
    for rid, total in totals.items():
        if total == 0:
            result[rid] = None
            continue
        defined = all_fields.get(rid, set())
        used = used_fields.get(rid, set())
        unused_count = len(defined - used)
        result[rid] = round(unused_count / total * 100)

    return result
