from fastapi import APIRouter, HTTPException, Query, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import SourceOut, SourceUpdate, FreshnessRuleRequest

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
def list_sources(include_archived: bool = Query(False)):
    with get_db() as db:
        archive_filter = "" if include_archived else "WHERE s.archived = 0"
        rows = db.execute(f"""
            SELECT s.*,
                   sp.status AS latest_status,
                   CAST(sp.last_data_at AS TEXT) AS latest_last_data_at,
                   (SELECT COUNT(*) FROM report_tables rt WHERE rt.source_id = s.id) AS report_count,
                   us.name AS upstream_name,
                   us.refresh_day AS upstream_refresh_day,
                   (SELECT GROUP_CONCAT(sc.display_name, ', ')
                    FROM script_tables st
                    JOIN scripts sc ON sc.id = st.script_id
                    WHERE st.source_id = s.id AND COALESCE(sc.archived, 0) = 0
                   ) AS linked_scripts,
                   (SELECT COUNT(*) FROM task_links tl
                    JOIN tasks t ON t.id = tl.task_id
                    WHERE tl.entity_type = 'source' AND tl.entity_id = s.id
                    AND t.status != 'done'
                   ) AS linked_task_count
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            LEFT JOIN upstream_systems us ON us.id = s.upstream_id
            {archive_filter}
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
            # Treat 0 as no rule (same as NULL) - present a clean view to the UI
            custom_fresh_days=r["custom_fresh_days"] or None,
            upstream_id=r["upstream_id"],
            upstream_name=r["upstream_name"],
            upstream_refresh_day=r["upstream_refresh_day"],
            linked_scripts=r["linked_scripts"],
            linked_task_count=r["linked_task_count"],
            archived=bool(r["archived"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
        if (r["latest_status"] or "unknown") not in ("unknown", "no_connection") or r["discovered_by"] in ("manual", "pg_deps")
    ]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int):
    with get_db() as db:
        r = db.execute("""
            SELECT s.*,
                   sp.status AS latest_status,
                   CAST(sp.last_data_at AS TEXT) AS latest_last_data_at,
                   (SELECT COUNT(*) FROM report_tables rt WHERE rt.source_id = s.id) AS report_count,
                   us.name AS upstream_name,
                   us.refresh_day AS upstream_refresh_day,
                   (SELECT GROUP_CONCAT(sc.display_name, ', ')
                    FROM script_tables st
                    JOIN scripts sc ON sc.id = st.script_id
                    WHERE st.source_id = s.id AND COALESCE(sc.archived, 0) = 0
                   ) AS linked_scripts
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            LEFT JOIN upstream_systems us ON us.id = s.upstream_id
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
        # Treat 0 as no rule (same as NULL)
        custom_fresh_days=r["custom_fresh_days"] or None,
        upstream_id=r["upstream_id"],
        upstream_name=r["upstream_name"],
        upstream_refresh_day=r["upstream_refresh_day"],
        linked_scripts=r["linked_scripts"],
        archived=bool(r["archived"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, update: SourceUpdate, request: Request):
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
            changed = ", ".join(k for k in update.model_dump(exclude_unset=True))
            log_event(db, "source", source_id, None, "updated", changed, get_actor(request))

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


@router.get("/{source_id}/scripts")
def get_source_scripts(source_id: int):
    """Get scripts linked to this source via script_tables."""
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT sc.id, sc.display_name, sc.path, sc.owner,
                   st.direction, st.table_name
            FROM script_tables st
            JOIN scripts sc ON sc.id = st.script_id
            WHERE st.source_id = ? AND COALESCE(sc.archived, 0) = 0
            ORDER BY sc.display_name
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
def set_freshness_rule(source_id: int, body: FreshnessRuleRequest, request: Request):
    """Set custom freshness thresholds for a source.

    fresh_days must be at least 1. 0 is not allowed - use DELETE to clear the rule.
    """
    if body.fresh_days < 1:
        raise HTTPException(status_code=400, detail="fresh_days must be at least 1 - use DELETE to clear the rule")
    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")
        db.execute(
            "UPDATE sources SET custom_fresh_days = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (body.fresh_days, source_id),
        )
        log_event(db, "source", source_id, None, "freshness_rule_set", f"healthy_days={body.fresh_days}", get_actor(request))
    return {"status": "ok", "fresh_days": body.fresh_days}


@router.delete("/{source_id}/freshness-rule")
def delete_freshness_rule(source_id: int, request: Request):
    """Reset freshness thresholds to global defaults."""
    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")
        db.execute(
            "UPDATE sources SET custom_fresh_days = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (source_id,),
        )
        log_event(db, "source", source_id, None, "freshness_rule_reset", None, get_actor(request))
    return {"status": "ok", "fresh_days": None}
