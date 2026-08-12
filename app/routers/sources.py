import re

from fastapi import APIRouter, HTTPException, Query, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import SourceOut, SourceUpdate, FreshnessRuleRequest
from app.usage import get_source_usage_map, sync_usage_from_csv_if_configured
from app.asset_visibility import get_active_source_ids

router = APIRouter(prefix="/api/sources", tags=["sources"])

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEKDAY_BY_CRON = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}
_WEEKDAY_ALIASES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _row_value(r, key: str, default=None):
    return r[key] if key in r.keys() else default


def _source_out_from_row(r, usage: dict | None = None) -> SourceOut:
    custom_days = _row_value(r, "custom_fresh_days") or None
    rule_type = _row_value(r, "freshness_rule_type") or ("custom" if custom_days else None)
    usage = usage or {}
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
        row_count=_row_value(r, "latest_row_count"),
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
        views_30d=usage.get("views_30d"),
        unique_users_30d=usage.get("unique_users_30d"),
        archived=bool(r["archived"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _owner_suggestions(rows) -> dict[int, dict]:
    """Choose the unique most common report owner for each source.

    A source is deliberately left untouched when the leading owners are tied.
    Report IDs are counted once even if a report references several tables from
    the same source.
    """
    grouped: dict[int, dict[str, dict]] = {}
    for row in rows:
        owner = str(row["owner"] or "").strip()
        if not owner:
            continue
        source_id = int(row["source_id"])
        owner_key = owner.casefold()
        source_owners = grouped.setdefault(source_id, {})
        bucket = source_owners.setdefault(owner_key, {"owner": owner, "report_ids": set()})
        bucket["report_ids"].add(int(row["report_id"]))

    suggestions = {}
    for source_id, owners in grouped.items():
        ranked = sorted(
            owners.values(),
            key=lambda item: (-len(item["report_ids"]), item["owner"].casefold()),
        )
        top_count = len(ranked[0]["report_ids"])
        tied = sum(1 for item in ranked if len(item["report_ids"]) == top_count) > 1
        suggestions[source_id] = {
            "owner": None if tied else ranked[0]["owner"],
            "owner_report_count": top_count,
            "total_owned_reports": len(set().union(*(item["report_ids"] for item in ranked))),
            "tied": tied,
        }
    return suggestions


def _cron_value(value: str) -> int | None:
    clean = value.strip().lower()
    if clean.isdigit():
        number = int(clean)
        return number if 0 <= number <= 7 else None
    return _WEEKDAY_ALIASES.get(clean[:3])


def _cron_weekdays(field: str) -> list[str] | None:
    """Parse a standard cron day-of-week field, or return None if ambiguous."""
    field = field.strip().lower()
    if field == "*":
        return list(WEEKDAYS)
    if any(marker in field for marker in ("/", "#", "l", "?")):
        return None

    values: set[int] = set()
    for token in field.split(","):
        token = token.strip()
        if not token:
            return None
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start = _cron_value(start_raw)
            end = _cron_value(end_raw)
            if start is None or end is None or start > end:
                return None
            values.update(range(start, end + 1))
        else:
            value = _cron_value(token)
            if value is None:
                return None
            values.add(value)

    names = {_WEEKDAY_BY_CRON[value] for value in values}
    return [day for day in WEEKDAYS if day in names]


def _freshness_rule_from_schedule(schedule: str | None) -> dict | None:
    """Infer a freshness rule only from an explicit source refresh schedule."""
    raw = str(schedule or "").strip()
    if not raw or "disabled" in raw.casefold():
        return None

    lower = raw.casefold()
    if lower in {"daily", "every day", "@daily", "@hourly"}:
        return {"rule_type": "daily", "fresh_days": 1, "refresh_days": []}
    if lower == "@weekly":
        return {"rule_type": "fixed", "fresh_days": None, "refresh_days": ["Sunday"]}

    cron_parts = raw.split()
    if len(cron_parts) == 5:
        days = _cron_weekdays(cron_parts[4])
        if days:
            if len(days) == 7:
                return {"rule_type": "daily", "fresh_days": 1, "refresh_days": []}
            return {"rule_type": "fixed", "fresh_days": None, "refresh_days": days}

    named_days = [
        day for day in WEEKDAYS
        if re.search(rf"\b{day.casefold()}\b", lower)
        or re.search(rf"\b{day[:3].casefold()}\b", lower)
    ]
    if named_days:
        return {"rule_type": "fixed", "fresh_days": None, "refresh_days": named_days}
    return None


@router.get("", response_model=list[SourceOut])
def list_sources(include_archived: bool = Query(False)):
    with get_db() as db:
        sync_usage_from_csv_if_configured(db)
        active_source_ids = get_active_source_ids(db)
        archive_filter = "" if include_archived else "WHERE s.archived = 0"
        rows = db.execute(f"""
            SELECT s.*,
                   sp.status AS latest_status,
                   CAST(sp.last_data_at AS TEXT) AS latest_last_data_at,
                   sp.row_count AS latest_row_count,
                   (SELECT COUNT(*)
                    FROM report_tables rt
                    JOIN reports r ON r.id = rt.report_id
                    WHERE rt.source_id = s.id
                      AND COALESCE(r.archived, 0) = 0
                   ) AS report_count,
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
                SELECT source_id, status, last_data_at, row_count,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            LEFT JOIN upstream_systems us ON us.id = s.upstream_id
            {archive_filter}
            ORDER BY s.name
        """).fetchall()
        usage_map = get_source_usage_map(db)

    visible_rows = [
        r for r in rows
        if (include_archived or r["id"] in active_source_ids)
    ]
    return [_source_out_from_row(r, usage_map.get(r["id"])) for r in visible_rows]


@router.post("/auto-assign-owners")
def auto_assign_source_owners(request: Request):
    """Fill blank source owners from the unique most common report owner."""
    actor = get_actor(request)
    with get_db() as db:
        candidates = db.execute(
            """SELECT id, name
               FROM sources
               WHERE COALESCE(archived, 0) = 0
                 AND (owner IS NULL OR trim(owner) = '')
               ORDER BY id"""
        ).fetchall()
        owner_rows = db.execute(
            """SELECT s.id AS source_id, r.id AS report_id, r.owner
               FROM sources s
               JOIN report_tables rt ON rt.source_id = s.id
               JOIN reports r ON r.id = rt.report_id
               WHERE COALESCE(s.archived, 0) = 0
                 AND COALESCE(r.archived, 0) = 0
                 AND (s.owner IS NULL OR trim(s.owner) = '')
                 AND r.owner IS NOT NULL
                 AND trim(r.owner) != ''
               GROUP BY s.id, r.id, r.owner"""
        ).fetchall()
        suggestions = _owner_suggestions(owner_rows)
        assigned = []
        tied = 0
        no_report_owner = 0
        for source in candidates:
            suggestion = suggestions.get(source["id"])
            if not suggestion:
                no_report_owner += 1
                continue
            if suggestion["tied"]:
                tied += 1
                continue
            owner = suggestion["owner"]
            cursor = db.execute(
                """UPDATE sources
                   SET owner = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND (owner IS NULL OR trim(owner) = '')""",
                (owner, source["id"]),
            )
            if not cursor.rowcount:
                continue
            detail = (
                f"assigned {owner} from {suggestion['owner_report_count']} of "
                f"{suggestion['total_owned_reports']} linked reports with owners"
            )
            log_event(db, "source", source["id"], source["name"], "owner_auto_assigned", detail, actor)
            assigned.append({
                "source_id": source["id"],
                "source_name": source["name"],
                **suggestion,
            })

    return {
        "assigned": len(assigned),
        "skipped_ties": tied,
        "skipped_no_report_owner": no_report_owner,
        "assignments": assigned,
    }


@router.post("/auto-set-freshness-rules")
def auto_set_source_freshness_rules(request: Request):
    """Fill only missing freshness rules from explicit source schedules."""
    actor = get_actor(request)
    with get_db() as db:
        candidates = db.execute(
            """SELECT id, name, refresh_schedule
               FROM sources
               WHERE COALESCE(archived, 0) = 0
                 AND COALESCE(trim(freshness_rule_type), '') = ''
                 AND COALESCE(custom_fresh_days, 0) = 0
                 AND COALESCE(trim(freshness_schedule_days), '') = ''
               ORDER BY id"""
        ).fetchall()
        configured = []
        skipped = 0
        for source in candidates:
            rule = _freshness_rule_from_schedule(source["refresh_schedule"])
            if not rule:
                skipped += 1
                continue
            schedule_days = ",".join(rule["refresh_days"]) or None
            cursor = db.execute(
                """UPDATE sources
                   SET freshness_rule_type = ?,
                       custom_fresh_days = ?,
                       freshness_schedule_days = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?
                     AND COALESCE(trim(freshness_rule_type), '') = ''
                     AND COALESCE(custom_fresh_days, 0) = 0
                     AND COALESCE(trim(freshness_schedule_days), '') = ''""",
                (rule["rule_type"], rule["fresh_days"], schedule_days, source["id"]),
            )
            if not cursor.rowcount:
                continue
            rule_label = "daily" if rule["rule_type"] == "daily" else schedule_days
            detail = f"set {rule_label} from source refresh schedule: {source['refresh_schedule']}"
            log_event(db, "source", source["id"], source["name"], "freshness_rule_auto_set", detail, actor)
            configured.append({
                "source_id": source["id"],
                "source_name": source["name"],
                "rule_type": rule["rule_type"],
                "refresh_days": rule["refresh_days"],
                "source_schedule": source["refresh_schedule"],
            })

    return {
        "configured": len(configured),
        "skipped_unsupported_schedule": skipped,
        "rules": configured,
    }


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int):
    with get_db() as db:
        sync_usage_from_csv_if_configured(db)
        r = db.execute("""
            SELECT s.*,
                   sp.status AS latest_status,
                   CAST(sp.last_data_at AS TEXT) AS latest_last_data_at,
                   sp.row_count AS latest_row_count,
                   (SELECT COUNT(*)
                    FROM report_tables rt
                    JOIN reports r ON r.id = rt.report_id
                    WHERE rt.source_id = s.id
                      AND COALESCE(r.archived, 0) = 0
                   ) AS report_count,
                   us.name AS upstream_name,
                   us.refresh_day AS upstream_refresh_day,
                   (SELECT GROUP_CONCAT(sc.display_name, ', ')
                    FROM script_tables st
                    JOIN scripts sc ON sc.id = st.script_id
                    WHERE st.source_id = s.id AND COALESCE(sc.archived, 0) = 0
                   ) AS linked_scripts
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status, last_data_at, row_count,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            LEFT JOIN upstream_systems us ON us.id = s.upstream_id
            WHERE s.id = ?
        """, (source_id,)).fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Source not found")

    with get_db() as db:
        usage_map = get_source_usage_map(db)
    return _source_out_from_row(r, usage_map.get(source_id))


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
              AND COALESCE(r.archived, 0) = 0
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
