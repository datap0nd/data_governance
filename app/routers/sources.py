from fastapi import APIRouter, HTTPException, Query, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import SourceOut, SourceUpdate, FreshnessRuleRequest

router = APIRouter(prefix="/api/sources", tags=["sources"])

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _row_value(r, key: str, default=None):
    return r[key] if key in r.keys() else default


def _source_out_from_row(r) -> SourceOut:
    custom_days = _row_value(r, "custom_fresh_days") or None
    rule_type = _row_value(r, "freshness_rule_type") or ("custom" if custom_days else None)
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
        status=r["latest_status"] if r["latest_status"] else "unknown",
        last_updated=r["latest_last_data_at"],
        report_count=r["report_count"],
        # Treat 0 as no rule (same as NULL) - present a clean view to the UI
        custom_fresh_days=custom_days,
        freshness_rule_type=rule_type,
        freshness_schedule_days=_row_value(r, "freshness_schedule_days"),
        upstream_id=_row_value(r, "upstream_id"),
        upstream_name=_row_value(r, "upstream_name"),
        upstream_refresh_day=_row_value(r, "upstream_refresh_day"),
        linked_scripts=r["linked_scripts"],
        linked_task_count=r["linked_task_count"] if "linked_task_count" in r.keys() else 0,
        archived=bool(r["archived"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


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
        _source_out_from_row(r)
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

    return _source_out_from_row(r)


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
    """Set a freshness rule for a source."""
    rule_type = (body.rule_type or ("custom" if body.fresh_days is not None else "")).strip().lower()
    if rule_type not in {"custom", "daily", "fixed"}:
        raise HTTPException(status_code=400, detail="rule_type must be custom, daily, or fixed")

    fresh_days = None
    schedule_days = None
    if rule_type == "custom":
        if body.fresh_days is None or body.fresh_days < 1:
            raise HTTPException(status_code=400, detail="fresh_days must be at least 1")
        fresh_days = body.fresh_days
    elif rule_type == "daily":
        fresh_days = 1
    elif rule_type == "fixed":
        raw_days = body.refresh_days or []
        days = []
        for day in raw_days:
            clean = str(day).strip().capitalize()
            if clean not in WEEKDAYS:
                raise HTTPException(status_code=400, detail=f"Invalid refresh day: {day}")
            if clean not in days:
                days.append(clean)
        if not days:
            raise HTTPException(status_code=400, detail="Choose at least one refresh day")
        if len(days) > 3:
            raise HTTPException(status_code=400, detail="Choose no more than 3 refresh days")
        schedule_days = ",".join(days)

    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")
        db.execute(
            """UPDATE sources
               SET freshness_rule_type = ?,
                   custom_fresh_days = ?,
                   freshness_schedule_days = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (rule_type, fresh_days, schedule_days, source_id),
        )
        detail = f"type={rule_type}"
        if fresh_days is not None:
            detail += f", healthy_days={fresh_days}"
        if schedule_days:
            detail += f", refresh_days={schedule_days}"
        log_event(db, "source", source_id, None, "freshness_rule_set", detail, get_actor(request))
    return {"status": "ok", "rule_type": rule_type, "fresh_days": fresh_days, "refresh_days": schedule_days}


@router.delete("/{source_id}/freshness-rule")
def delete_freshness_rule(source_id: int, request: Request):
    """Reset freshness thresholds to global defaults."""
    with get_db() as db:
        existing = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")
        cols = {r["name"] for r in db.execute("PRAGMA table_info(sources)").fetchall()}
        fields = ["custom_fresh_days = NULL", "updated_at = CURRENT_TIMESTAMP"]
        if "freshness_rule_type" in cols:
            fields.append("freshness_rule_type = NULL")
        if "freshness_schedule_days" in cols:
            fields.append("freshness_schedule_days = NULL")
        db.execute(
            f"UPDATE sources SET {', '.join(fields)} WHERE id = ?",
            (source_id,),
        )
        log_event(db, "source", source_id, None, "freshness_rule_reset", None, get_actor(request))
    return {"status": "ok", "fresh_days": None}
