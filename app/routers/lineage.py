import re

from fastapi import APIRouter, HTTPException
from app.config import PBI_WORKSPACE, UPLOAD_PGHOST
from app.database import get_db
from app.models import LineageEdge
from app.source_identity import inspect_flow_target

router = APIRouter(prefix="/api/lineage", tags=["lineage"])


def _normalized_object_name(value: str | None) -> str:
    value = (value or "").strip().casefold()
    value = value.replace('"', "").replace("`", "").replace("[", "").replace("]", "")
    return re.sub(r"[\\/]+", ".", value).strip(".")


def _source_matches_flow_target(source: dict, flow: dict) -> bool:
    """Match a governed source to a Flow SQL handoff target."""
    table = _normalized_object_name(flow.get("sql_table"))
    schema = _normalized_object_name(flow.get("sql_schema"))
    if not table:
        return False
    target = f"{schema}.{table}" if schema else table
    candidates = {
        _normalized_object_name(source.get("name")),
        _normalized_object_name(source.get("connection_info")),
    }
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == target or candidate.endswith(f".{target}"):
            return True
        if not schema and candidate.rsplit(".", 1)[-1] == table:
            return True
    return False


def _postgres_ref(source_name: str | None) -> dict | None:
    """Extract the schema and relation from a PostgreSQL lineage source name."""
    cleaned = (source_name or "").strip().replace("\\", "/").split("/")[-1]
    cleaned = cleaned.replace('"', "").replace("`", "").replace("[", "").replace("]", "")
    parts = [part.strip() for part in cleaned.split(".") if part.strip()]
    if len(parts) < 2:
        return None
    return {"schema": parts[-2], "name": parts[-1]}


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
                sp.status AS source_status,
                CAST(sp.last_data_at AS TEXT) AS source_last_data_at,
                sp.row_count AS source_row_count
            FROM report_tables rt
            JOIN sources s ON s.id = rt.source_id
            JOIN reports r ON r.id = rt.report_id
            LEFT JOIN (
                SELECT source_id, status, last_data_at, row_count,
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
            source_last_data_at=r["source_last_data_at"],
            source_row_count=r["source_row_count"],
        )
        for r in rows
    ]


@router.get("/report/{report_id}/diagram")
def get_lineage_diagram(report_id: int):
    """Full lineage chain for a single report: visuals -> fields -> tables -> sources -> upstream."""
    with get_db() as db:
        # 1. Report info
        report = db.execute(
            """SELECT id, name, owner, business_owner, archived, pbi_dataset_id
               FROM reports WHERE id = ?""",
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

        # 4. Follow every source dependency reachable from the report.
        # UNION (rather than UNION ALL) makes the closure terminate safely if
        # bad dependency data contains a cycle.
        direct_source_ids = sorted({r["source_id"] for r in table_rows if r["source_id"]})
        source_ids = list(direct_source_ids)
        if direct_source_ids:
            direct_ph = ",".join("?" * len(direct_source_ids))
            reachable_rows = db.execute(f"""
                WITH RECURSIVE reachable_sources(id) AS (
                    SELECT id
                    FROM sources
                    WHERE id IN ({direct_ph})

                    UNION

                    SELECT sd.depends_on_id
                    FROM source_dependencies sd
                    JOIN reachable_sources rs ON rs.id = sd.source_id
                )
                SELECT id
                FROM reachable_sources
                ORDER BY id
            """, direct_source_ids).fetchall()
            source_ids = [r["id"] for r in reachable_rows]

        # 5. Direct and recursively upstream sources with latest probe status
        sources = []
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            source_rows = db.execute(f"""
                SELECT s.id, s.name, s.type, s.owner, s.upstream_id, s.connection_info,
                       s.refresh_schedule, s.custom_fresh_days,
                       s.freshness_rule_type, s.freshness_schedule_days,
                       spi.server_name, spi.database_name, spi.schema_name,
                       spi.relation_name, spi.relation_kind, spi.verified_at,
                       sp.status, CAST(sp.last_data_at AS TEXT) AS last_data_at,
                       sp.row_count
                FROM sources s
                LEFT JOIN source_postgres_identities spi ON spi.source_id=s.id
                LEFT JOIN (
                    SELECT source_id, status, last_data_at, row_count,
                           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                    FROM source_probes
                ) sp ON sp.source_id = s.id AND sp.rn = 1
                WHERE s.id IN ({placeholders})
                ORDER BY s.name
            """, source_ids).fetchall()
            sources = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type"],
                    "connection_info": r["connection_info"],
                    "status": r["status"] or "unknown",
                    "last_data_at": r["last_data_at"],
                    "row_count": r["row_count"],
                    "owner": r["owner"],
                    "upstream_id": r["upstream_id"],
                    "refresh_schedule": r["refresh_schedule"],
                    "custom_fresh_days": r["custom_fresh_days"],
                    "freshness_rule_type": r["freshness_rule_type"],
                    "freshness_schedule_days": r["freshness_schedule_days"],
                    "postgres_identity": (
                        {
                            "server": r["server_name"], "database": r["database_name"],
                            "schema": r["schema_name"], "relation": r["relation_name"],
                            "kind": r["relation_kind"], "verified_at": r["verified_at"],
                        }
                        if r["server_name"] is not None else None
                    ),
                }
                for r in source_rows
            ]

        # 6. Upstream systems linked to any source in the dependency chain
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

        # 7. Every source dependency in the reachable chain (MV -> upstream)
        source_deps = []
        if source_ids:
            dep_ph = ",".join("?" * len(source_ids))
            dep_rows = db.execute(f"""
                SELECT sd.source_id, sd.depends_on_id,
                       s.name AS depends_on_name, s.type AS depends_on_type,
                       s.custom_fresh_days AS depends_on_custom_fresh_days,
                       s.freshness_rule_type AS depends_on_freshness_rule_type,
                       s.freshness_schedule_days AS depends_on_freshness_schedule_days,
                       sp.status AS depends_on_status,
                       CAST(sp.last_data_at AS TEXT) AS depends_on_last_data_at,
                       sp.row_count AS depends_on_row_count
                FROM source_dependencies sd
                JOIN sources s ON s.id = sd.depends_on_id
                LEFT JOIN (
                    SELECT source_id, status, last_data_at, row_count,
                           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                    FROM source_probes
                ) sp ON sp.source_id = sd.depends_on_id AND sp.rn = 1
                WHERE sd.source_id IN ({dep_ph})
                ORDER BY sd.source_id, sd.depends_on_id
            """, source_ids).fetchall()
            source_deps = [
                {
                    "source_id": r["source_id"],
                    "depends_on_id": r["depends_on_id"],
                    "depends_on_name": r["depends_on_name"],
                    "depends_on_type": r["depends_on_type"],
                    "depends_on_custom_fresh_days": r["depends_on_custom_fresh_days"],
                    "depends_on_freshness_rule_type": r["depends_on_freshness_rule_type"],
                    "depends_on_freshness_schedule_days": r["depends_on_freshness_schedule_days"],
                    "depends_on_status": r["depends_on_status"] or "unknown",
                    "depends_on_last_data_at": r["depends_on_last_data_at"],
                    "depends_on_row_count": r["depends_on_row_count"],
                }
                for r in dep_rows
            ]

        materialized_source_ids = {dep["source_id"] for dep in source_deps}
        for source in sources:
            source["is_materialized_view"] = source["id"] in materialized_source_ids
            identity = source.get("postgres_identity") or {}
            source["postgres_ref"] = (
                {"schema": identity["schema"], "name": identity["relation"]}
                if source["is_materialized_view"] and identity
                else (
                    _postgres_ref(source["name"])
                    if source["is_materialized_view"] and (source["type"] or "").casefold() == "postgresql"
                    else None
                )
            )

        # 8. Flows that load a SQL target represented anywhere in this report
        # pipeline. A Flow is upstream of its target source.
        flows = []
        legacy_flow_suggestions = []
        if sources:
            flow_rows = db.execute(
                """SELECT f.*,
                          EXISTS(
                              SELECT 1 FROM flow_runs fr
                              WHERE fr.flow_id=f.id
                                AND fr.status IN ('queued','claimed','running')
                          ) AS has_active_run
                   FROM flows f
                   WHERE f.sql_handoff_enabled=1
                     AND NULLIF(TRIM(f.sql_table), '') IS NOT NULL
                   ORDER BY f.name"""
            ).fetchall()
            for row in flow_rows:
                flow = dict(row)
                resolution = inspect_flow_target(db, flow, server=UPLOAD_PGHOST)
                effective_source_id = resolution.get("effective_source_id")
                if effective_source_id not in source_ids:
                    suggested_source_ids = [
                        source["id"] for source in sources
                        if _source_matches_flow_target(source, flow)
                    ]
                    if suggested_source_ids:
                        legacy_flow_suggestions.append({
                            "id": flow["id"], "name": flow["name"],
                            "target_source_ids": suggested_source_ids,
                            "sql_database": flow.get("sql_database"),
                            "sql_schema": flow.get("sql_schema"),
                            "sql_table": flow.get("sql_table"),
                            "executable": False,
                            "reason": "Legacy display-name suggestion; confirm the exact SQL target in the Flow editor.",
                        })
                    continue
                target_source_ids = [int(effective_source_id)]
                last_success_at = flow.get("last_success_at")
                if not last_success_at and flow.get("last_status") == "succeeded":
                    last_success_at = flow.get("last_run_at")
                flows.append({
                    "id": flow["id"],
                    "name": flow["name"],
                    "target_source_ids": target_source_ids,
                    "sql_database": flow.get("sql_database"),
                    "sql_schema": flow.get("sql_schema"),
                    "sql_table": flow.get("sql_table"),
                    "last_run_at": flow.get("last_run_at"),
                    "last_success_at": last_success_at,
                    "last_status": flow.get("last_status"),
                    "last_error": flow.get("last_error"),
                    "has_active_run": bool(flow.get("has_active_run")),
                    "sql_target_link_status": resolution["status"],
                    "sql_target_persisted": bool(resolution.get("persisted_valid")),
                    "executable": True,
                })

    return {
        "report": {
            "id": report["id"],
            "name": report["name"],
            "status": status,
            "owner": report["owner"],
            "archived": bool(report["archived"]),
            "pbi_dataset_id": report["pbi_dataset_id"],
            "can_refresh": bool(report["pbi_dataset_id"] or PBI_WORKSPACE),
        },
        "pages": pages_list,
        "tables": tables,
        "sources": sources,
        "source_deps": source_deps,
        "flows": flows,
        "legacy_flow_suggestions": legacy_flow_suggestions,
        "upstreams": upstreams,
    }
