from fastapi import APIRouter, HTTPException
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
                r.name AS report_name,
                sp.status AS source_status
            FROM report_tables rt
            JOIN sources s ON s.id = rt.source_id
            JOIN reports r ON r.id = rt.report_id
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            ORDER BY s.name, r.name
        """).fetchall()

    return [
        LineageEdge(
            source_id=r["source_id"],
            source_name=r["source_name"],
            source_type=r["source_type"],
            report_id=r["report_id"],
            report_name=r["report_name"],
            source_status=r["source_status"] or "unknown",
        )
        for r in rows
    ]


@router.get("/report/{report_id}/diagram")
def get_lineage_diagram(report_id: int):
    """Full lineage chain for a single report: visuals -> fields -> tables -> sources -> upstream."""
    with get_db() as db:
        # 1. Report info
        report = db.execute(
            "SELECT id, name, owner, business_owner FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        # Derive report status
        from app.routers.reports import _derive_report_status
        status, _ = _derive_report_status(report_id)

        # 2. Pages / visuals / fields
        vis_rows = db.execute("""
            SELECT rp.page_name, rp.page_ordinal,
                   rv.id AS visual_db_id, rv.visual_id, rv.visual_type, rv.title,
                   vf.table_name, vf.field_name
            FROM report_pages rp
            JOIN report_visuals rv ON rv.page_id = rp.id
            LEFT JOIN visual_fields vf ON vf.visual_id = rv.id
            WHERE rp.report_id = ?
            ORDER BY rp.page_ordinal, rv.visual_type, vf.table_name, vf.field_name
        """, (report_id,)).fetchall()

        # Group into pages -> visuals -> fields
        pages = {}
        for r in vis_rows:
            pk = r["page_name"]
            if pk not in pages:
                pages[pk] = {
                    "page_name": r["page_name"],
                    "page_ordinal": r["page_ordinal"],
                    "visuals": {},
                }
            vk = r["visual_db_id"]
            if vk not in pages[pk]["visuals"]:
                pages[pk]["visuals"][vk] = {
                    "visual_db_id": r["visual_db_id"],
                    "visual_id": r["visual_id"],
                    "visual_type": r["visual_type"],
                    "title": r["title"],
                    "fields": [],
                }
            if r["table_name"]:
                pages[pk]["visuals"][vk]["fields"].append({
                    "table": r["table_name"],
                    "field": r["field_name"],
                })

        pages_list = []
        for p in sorted(pages.values(), key=lambda x: x["page_ordinal"]):
            pages_list.append({
                "page_name": p["page_name"],
                "page_ordinal": p["page_ordinal"],
                "visuals": list(p["visuals"].values()),
            })

        # 3. Tables with source linkage
        table_rows = db.execute("""
            SELECT rt.table_name, rt.source_id, rt.source_expression
            FROM report_tables rt
            WHERE rt.report_id = ?
            ORDER BY rt.table_name
        """, (report_id,)).fetchall()

        tables = [
            {"table_name": r["table_name"], "source_id": r["source_id"], "source_expression": r["source_expression"]}
            for r in table_rows
        ]

        # 4. Sources with latest probe status
        source_ids = list({r["source_id"] for r in table_rows if r["source_id"]})
        sources = []
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            source_rows = db.execute(f"""
                SELECT s.id, s.name, s.type, s.owner, s.upstream_id,
                       sp.status, CAST(sp.last_data_at AS TEXT) AS last_data_at
                FROM sources s
                LEFT JOIN (
                    SELECT source_id, status, last_data_at,
                           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                    FROM source_probes
                ) sp ON sp.source_id = s.id AND sp.rn = 1
                WHERE s.id IN ({placeholders})
            """, source_ids).fetchall()
            sources = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type"],
                    "status": r["status"] or "unknown",
                    "last_data_at": r["last_data_at"],
                    "owner": r["owner"],
                    "upstream_id": r["upstream_id"],
                }
                for r in source_rows
            ]

        # 5. Upstream systems
        upstream_ids = list({s["upstream_id"] for s in sources if s["upstream_id"]})
        upstreams = []
        if upstream_ids:
            placeholders = ",".join("?" * len(upstream_ids))
            up_rows = db.execute(f"""
                SELECT id, name, code, refresh_day
                FROM upstream_systems
                WHERE id IN ({placeholders})
            """, upstream_ids).fetchall()
            upstreams = [
                {"id": r["id"], "name": r["name"], "code": r["code"], "refresh_day": r["refresh_day"]}
                for r in up_rows
            ]

        # 6. Scripts that write to these sources
        scripts = []
        if source_ids:
            src_ph = ",".join("?" * len(source_ids))
            script_rows = db.execute(f"""
                SELECT DISTINCT sc.id, sc.display_name, sc.hostname, sc.machine_alias,
                       st.source_id
                FROM script_tables st
                JOIN scripts sc ON sc.id = st.script_id
                WHERE st.direction = 'write' AND st.source_id IN ({src_ph})
            """, source_ids).fetchall()
            script_map = {}
            for r in script_rows:
                sid = r["id"]
                if sid not in script_map:
                    script_map[sid] = {
                        "id": r["id"],
                        "display_name": r["display_name"],
                        "hostname": r["hostname"],
                        "machine_alias": r["machine_alias"],
                        "source_ids": [],
                    }
                src_id = r["source_id"]
                if src_id not in script_map[sid]["source_ids"]:
                    script_map[sid]["source_ids"].append(src_id)
            scripts = list(script_map.values())

        # 7. Scheduled tasks linked to these scripts
        scheduled_tasks = []
        script_id_list = [s["id"] for s in scripts]
        if script_id_list:
            task_ph = ",".join("?" * len(script_id_list))
            task_rows = db.execute(f"""
                SELECT id, task_name, status, last_run_time, last_result,
                       schedule_type, enabled, script_id,
                       hostname, machine_alias
                FROM scheduled_tasks
                WHERE script_id IN ({task_ph})
            """, script_id_list).fetchall()
            scheduled_tasks = [
                {
                    "id": r["id"],
                    "task_name": r["task_name"],
                    "status": r["status"],
                    "last_run_time": r["last_run_time"],
                    "last_result": r["last_result"],
                    "schedule_type": r["schedule_type"],
                    "enabled": bool(r["enabled"]),
                    "script_id": r["script_id"],
                    "machine_alias": r["machine_alias"],
                }
                for r in task_rows
            ]

    return {
        "report": {
            "id": report["id"],
            "name": report["name"],
            "status": status,
            "owner": report["owner"],
        },
        "pages": pages_list,
        "tables": tables,
        "sources": sources,
        "upstreams": upstreams,
        "scripts": scripts,
        "scheduled_tasks": scheduled_tasks,
    }
