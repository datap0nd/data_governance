import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from app.database import get_db
from app.routers.eventlog import log_event, get_actor
from app.models import ActionOut, ActionUpdate
from app.usage import get_report_usage_map, get_source_usage_map, sync_usage_from_csv_if_configured

router = APIRouter(prefix="/api/actions", tags=["actions"])


# These scanner findings remain available in their dedicated product areas,
# but they are governance metadata rather than operational alerts.
NON_ALERT_ACTION_TYPES = {"best_practice", "broken_ref", "documentation_missing"}


def _compute_source_days_outdated(db) -> dict[int, int]:
    """For each source with outdated latest probe, days between now and last_data_at."""
    rows = db.execute("""
        SELECT sp.source_id, sp.status, sp.last_data_at
        FROM source_probes sp
        WHERE sp.id = (
            SELECT sp2.id FROM source_probes sp2
            WHERE sp2.source_id = sp.source_id
            ORDER BY sp2.probed_at DESC LIMIT 1
        )
    """).fetchall()
    result: dict[int, int] = {}
    now = datetime.now(timezone.utc)
    for r in rows:
        status = r["status"]
        last_data = r["last_data_at"]
        if status not in ("outdated", "stale", "error") or not last_data:
            continue
        try:
            dt = datetime.fromisoformat(last_data)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = max(0, (now - dt).days)
        except (ValueError, TypeError):
            days = 0
        result[r["source_id"]] = days
    return result


def _compute_report_context(db, source_days: dict[int, int]):
    """Return (source_reports_map, report_degradation_map).

    source_reports_map: {source_id: [(report_id, report_name), ...]}
    report_degradation_map: {report_id: total_degradation_days}

    Active (non-archived) reports are preferred, but archived reports are
    still included so the Alerts table matches what Sources page shows.
    """
    direct_rows = db.execute("""
        SELECT rt.source_id, rt.report_id, r.name AS report_name,
               COALESCE(r.archived, 0) AS archived
        FROM report_tables rt
        JOIN reports r ON r.id = rt.report_id
        WHERE rt.source_id IS NOT NULL
        ORDER BY archived ASC, r.name ASC
    """).fetchall()
    # Include reports reached through downstream lineage so an upstream MV
    # definition alert retains the impacted-report context.
    context_rows = db.execute("""
        WITH RECURSIVE source_reports(source_id, report_id) AS (
            SELECT rt.source_id, rt.report_id
            FROM report_tables rt
            WHERE rt.source_id IS NOT NULL
            UNION
            SELECT sd.depends_on_id, sr.report_id
            FROM source_dependencies sd
            JOIN source_reports sr ON sr.source_id = sd.source_id
        )
        SELECT sr.source_id, sr.report_id, r.name AS report_name,
               COALESCE(r.archived, 0) AS archived
        FROM source_reports sr
        JOIN reports r ON r.id = sr.report_id
        ORDER BY archived ASC, r.name ASC
    """).fetchall()

    source_reports: dict[int, list[tuple[int, str]]] = {}
    report_sources: dict[int, list[int]] = {}
    report_names: dict[int, str] = {}
    for r in context_rows:
        sid = r["source_id"]
        rid = r["report_id"]
        source_reports.setdefault(sid, []).append((rid, r["report_name"]))
    for r in direct_rows:
        sid = r["source_id"]
        rid = r["report_id"]
        report_sources.setdefault(rid, []).append(sid)
        report_names[rid] = r["report_name"]

    report_degradation: dict[int, int] = {}
    for rid, sids in report_sources.items():
        total = sum(source_days.get(sid, 0) for sid in sids)
        report_degradation[rid] = total

    return source_reports, report_degradation, report_names


def _compute_report_action_days(db) -> dict[int, int]:
    """Days since last successful refresh, keyed by report_id.

    For report-level actions (refresh_failed / refresh_overdue) we want a
    "days since problem started" metric analogous to source_days_outdated.
    Uses pbi_last_refresh_at as the reference point.
    """
    rows = db.execute(
        "SELECT id, pbi_last_refresh_at FROM reports WHERE archived = 0"
    ).fetchall()
    result: dict[int, int] = {}
    now = datetime.now(timezone.utc)
    for r in rows:
        last = r["pbi_last_refresh_at"]
        if not last:
            result[r["id"]] = 0
            continue
        try:
            ts = last.replace("Z", "+00:00") if isinstance(last, str) else last
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            result[r["id"]] = max(0, (now - dt).days)
        except (ValueError, TypeError, AttributeError):
            result[r["id"]] = 0
    return result


def _compute_report_stale_sources(db) -> dict[int, list[dict]]:
    """For each report, list sources with data newer than the report's last
    refresh. Each item is a dict with source id, name, and how far ahead
    so the frontend can render clickable links for troubleshooting.

    Empty list means the report is caught up.
    """
    rows = db.execute("""
        SELECT r.id AS report_id, r.pbi_last_refresh_at,
               s.id AS source_id, s.name AS source_name, sp.last_data_at
        FROM reports r
        JOIN report_tables rt ON rt.report_id = r.id
        JOIN sources s ON s.id = rt.source_id
        JOIN (
            SELECT source_id, last_data_at,
                   ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
            FROM source_probes WHERE last_data_at IS NOT NULL
        ) sp ON sp.source_id = s.id AND sp.rn = 1
        WHERE COALESCE(r.archived, 0) = 0
          AND COALESCE(s.archived, 0) = 0
          AND r.pbi_last_refresh_at IS NOT NULL
    """).fetchall()

    def _parse_dt(val):
        if not val:
            return None
        try:
            ts = val.replace("Z", "+00:00") if isinstance(val, str) else val
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError, AttributeError):
            return None

    result: dict[int, list[dict]] = {}
    for r in rows:
        report_dt = _parse_dt(r["pbi_last_refresh_at"])
        source_dt = _parse_dt(r["last_data_at"])
        if not report_dt or not source_dt:
            continue
        delta_hours = (source_dt - report_dt).total_seconds() / 3600
        if delta_hours <= 1:  # same 1h buffer as detector
            continue
        short = r["source_name"].replace("\\", "/").split("/")[-1]
        result.setdefault(r["report_id"], []).append({
            "id": r["source_id"],
            "name": short,
            "delta_hours": int(delta_hours),
            "source_last_data_at": source_dt.isoformat(),
            "report_last_refresh_at": report_dt.isoformat(),
        })

    # Sort each list by how far behind (most behind first)
    return {
        rid: sorted(items, key=lambda x: -x["delta_hours"])
        for rid, items in result.items()
    }


def _recommendation_for(action_type: str, detail_items: list[dict]) -> str | None:
    """Short actionable message shown when an alert row is expanded."""
    if action_type == "schedule_mismatch":
        if detail_items:
            return (
                f"Refresh this report to pick up updated data from "
                f"{len(detail_items)} source(s) that ran after its last refresh."
            )
        return "Refresh this report - one or more sources have fresher data."
    if action_type == "refresh_failed":
        return "Fix the Power BI refresh error shown below, then trigger a manual refresh."
    if action_type == "refresh_overdue":
        return "Trigger a manual refresh or verify the schedule is enabled and the gateway is up."
    if action_type in ("stale_source", "outdated_source", "error_source"):
        return "Find out why this source hasn't updated. Check its linked Flow or refresh process."
    if action_type == "empty_source":
        return "Check the latest refresh output. The source now has zero rows after previously having data."
    if action_type == "task_failed":
        return "Open the scheduled task, review the last run output, and re-run once the underlying issue is resolved."
    if action_type == "script_failed":
        return "Check the script log for errors. The linked scheduled task(s) may need to be re-run after fixing."
    if action_type == "flow_failed":
        return "Open the Flow, review its latest run error, and refresh it again after correcting the cause."
    if action_type == "pipeline_failed":
        return "Open the failed Pipeline occurrence, review what completed, and inspect the failed stage before running it again."
    if action_type == "pbi_reconnect":
        return "Reconnect the saved Power BI account from Scanner, then confirm an authenticated sync completes."
    if action_type == "broken_ref":
        return "Update the report to point at an existing source, or remove the unused table."
    if action_type == "changed_query":
        return "Open the saved query diff and confirm downstream usage is still correct."
    if action_type == "schedule_discrepancy":
        return "Move the upstream, source, or report schedule so each dependent step runs after its inputs."
    if action_type == "documentation_missing":
        return "Complete the report's business purpose and technical transformation documentation."
    if action_type == "dependency_stale":
        return "Review the materialized-view refresh job and refresh it after all upstream dependencies are healthy."
    if action_type == "data_quality":
        return "Inspect the latest check result and source data, then rerun the check after correction."
    return None


TRIAGE_TYPE_WEIGHT = {
    "refresh_failed": 900,
    "schedule_mismatch": 820,
    "stale_source": 760,
    "outdated_source": 760,
    "error_source": 760,
    "empty_source": 780,
    "refresh_overdue": 680,
    "task_failed": 620,
    "script_failed": 620,
    "flow_failed": 720,
    "pipeline_failed": 820,
    "pbi_reconnect": 950,
    "broken_ref": 540,
    "changed_query": 420,
    "data_quality": 850,
    "dependency_stale": 800,
    "schedule_discrepancy": 700,
    "documentation_missing": 260,
}


def _issue_reason(action_type: str) -> str:
    return {
        "refresh_failed": "Last PBI refresh failed",
        "refresh_overdue": "PBI refresh is overdue",
        "schedule_mismatch": "Report is behind newer source data",
        "stale_source": "Source is outside its freshness rule",
        "outdated_source": "Source is outside its freshness rule",
        "error_source": "Source probe is failing",
        "empty_source": "Source row count dropped to zero",
        "task_failed": "Scheduled task failed",
        "script_failed": "Script-linked refresh failed",
        "flow_failed": "Flow refresh failed",
        "pipeline_failed": "Full Pipeline failed",
        "pbi_reconnect": "Power BI account must be reconnected",
        "broken_ref": "Report points to a missing source",
        "changed_query": "Source query changed",
        "data_quality": "Automated data-quality check failed",
        "dependency_stale": "Materialized view is behind upstream data",
        "schedule_discrepancy": "Refresh schedules are in the wrong order",
        "documentation_missing": "Report documentation is incomplete",
    }.get(action_type, action_type.replace("_", " "))


def _triage_cta(asset_type: str | None, assigned_to: str | None) -> str:
    if not assigned_to:
        return "Assign owner"
    if asset_type == "report":
        return "Open report"
    if asset_type == "source":
        return "Open source"
    if asset_type == "flow":
        return "Open flow"
    return "Open details"


def _apply_triage(action: ActionOut) -> ActionOut:
    """Attach transparent fix-first scoring to an action.

    Usage is deliberately the dominant signal: one usage-impact point is
    worth more than issue-type and age tie-breakers combined.
    """
    if action.status in ("resolved", "expected"):
        action.triage_score = 0
        action.triage_reasons = []
        action.triage_cta = None
        return action

    impact = max(0, action.impact_views_30d or 0)
    days = max(0, action.asset_days or 0)
    issue_weight = TRIAGE_TYPE_WEIGHT.get(action.type, 400)
    status_weight = {
        "open": 300,
        "investigating": 160,
        "acknowledged": 90,
    }.get(action.status, 120)
    owner_weight = 75 if not action.assigned_to else 0
    affected_reports = len(action.report_names or [])
    report_weight = min(affected_reports, 10) * 20
    stale_gap_weight = 0
    if action.type == "schedule_mismatch" and action.detail_items:
        max_gap = max((int(item.get("delta_hours") or 0) for item in action.detail_items), default=0)
        stale_gap_weight = min(max_gap, 72) * 5

    action.triage_score = (
        impact * 1000
        + issue_weight
        + min(days, 30) * 25
        + status_weight
        + owner_weight
        + report_weight
        + stale_gap_weight
    )

    reasons: list[str] = []
    if impact:
        reasons.append(f"{impact:,} views in the last 30 days")
    else:
        reasons.append("No measured usage impact yet")
    reasons.append(_issue_reason(action.type))
    if action.type == "schedule_mismatch" and action.detail_items:
        count = len(action.detail_items)
        worst_gap = max((int(item.get("delta_hours") or 0) for item in action.detail_items), default=0)
        if worst_gap >= 48:
            gap = f"{worst_gap // 24}d"
        else:
            gap = f"{worst_gap}h"
        reasons.append(f"{count} source{'s' if count != 1 else ''} newer, worst gap {gap}")
    elif action.asset_type == "source" and affected_reports:
        reasons.append(f"Affects {affected_reports} report{'s' if affected_reports != 1 else ''}")
    if days:
        reasons.append(f"Degraded since {action.degraded_since[:10]}" if action.degraded_since else f"Degraded for {days}d")
    if not action.assigned_to:
        reasons.append("Unassigned")

    action.triage_reasons = reasons[:4]
    action.triage_cta = _triage_cta(action.asset_type, action.assigned_to)
    return action


@router.get("", response_model=list[ActionOut])
def list_actions(status: str | None = None):
    with get_db() as db:
        sync_usage_from_csv_if_configured(db)
        # Each action is about one asset - a source, report, scheduled task,
        # or script. For source-tied actions we also look up the latest probe
        # so we can skip actions on sources that are no longer outdated
        # (stale rows from before a freshness rule change will get auto-
        # resolved on next probe but we don't want them cluttering the UI
        # in the meantime).
        query = """
            SELECT a.*, s.name AS source_name, s.type AS source_type,
                   s.archived AS source_archived,
                   r.name AS report_name, r.archived AS report_archived,
                   r.pbi_refresh_error,
                   st.task_name AS task_name, st.archived AS task_archived,
                   sc.display_name AS script_name, sc.archived AS script_archived,
                   f.name AS flow_name,
                   sp.status AS latest_source_status,
                   sp.row_count AS latest_source_row_count
            FROM actions a
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN reports r ON r.id = a.report_id
            LEFT JOIN scheduled_tasks st ON st.id = a.scheduled_task_id
            LEFT JOIN scripts sc ON sc.id = a.script_id
            LEFT JOIN flows f ON f.id = a.flow_id
            LEFT JOIN (
                SELECT source_id, status, row_count,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC, id DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = a.source_id AND sp.rn = 1
        """
        params = []
        placeholders = ",".join("?" for _ in NON_ALERT_ACTION_TYPES)
        query += f" WHERE a.type NOT IN ({placeholders})"
        params.extend(sorted(NON_ALERT_ACTION_TYPES))
        if status:
            query += " AND a.status = ?"
            params.append(status)
        query += " ORDER BY a.created_at DESC"
        rows = db.execute(query, params).fetchall()

        query_change_map: dict[int, list[dict]] = {}
        action_ids = [int(row["id"]) for row in rows]
        if action_ids:
            placeholders = ",".join("?" for _ in action_ids)
            version_rows = db.execute(
                f"""SELECT action_id, id AS version_id, previous_version_id,
                            artifact_kind, artifact_name, language, detected_at
                     FROM query_versions
                     WHERE action_id IN ({placeholders})
                     ORDER BY artifact_name COLLATE NOCASE, id""",
                action_ids,
            ).fetchall()
            for version in version_rows:
                query_change_map.setdefault(int(version["action_id"]), []).append({
                    "version_id": version["version_id"],
                    "previous_version_id": version["previous_version_id"],
                    "artifact_kind": version["artifact_kind"],
                    "artifact_name": version["artifact_name"],
                    "language": version["language"],
                    "detected_at": version["detected_at"],
                })

        source_days = _compute_source_days_outdated(db)
        source_reports, report_degradation, _ = _compute_report_context(db, source_days)
        report_days = _compute_report_action_days(db)
        report_stale_sources = _compute_report_stale_sources(db)
        report_usage = get_report_usage_map(db)
        source_usage = get_source_usage_map(db)

    ACTIONABLE_STATUSES = {"outdated", "stale", "error"}
    FRESHNESS_ACTION_TYPES = {"stale_source", "outdated_source", "error_source"}

    results: list[ActionOut] = []
    for r in rows:
        sid = r["source_id"]
        rid = r["report_id"]
        tid = r["scheduled_task_id"] if "scheduled_task_id" in r.keys() else None
        scid = r["script_id"] if "script_id" in r.keys() else None
        fid = r["flow_id"] if "flow_id" in r.keys() else None
        latest = r["latest_source_status"]
        latest_row_count = r["latest_source_row_count"]

        # Source-tied: hide if the source is archived or no longer outdated
        if sid is not None:
            if r["source_archived"]:
                continue
            if r["type"] == "empty_source":
                if latest_row_count is not None and latest_row_count > 0:
                    continue
            elif r["type"] in FRESHNESS_ACTION_TYPES and latest is not None and latest not in ACTIONABLE_STATUSES:
                continue
        # Report-tied: hide if the report is archived
        if rid is not None and sid is None and r["report_archived"]:
            continue
        # Task-tied: hide if task archived
        if tid is not None and r["task_archived"]:
            continue
        # Script-tied: hide if script archived
        if scid is not None and r["script_archived"]:
            continue

        # Determine asset identity for this action - priority order mirrors
        # how the action was created: source > report > task > script
        if sid is not None:
            asset_type = "source"
            asset_id = sid
            asset_name = r["source_name"]
            if r["type"] == "empty_source" and r["created_at"]:
                try:
                    created = datetime.fromisoformat(r["created_at"])
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    asset_days = max(0, (datetime.now(timezone.utc) - created).days)
                except (ValueError, TypeError, AttributeError):
                    asset_days = 0
            else:
                asset_days = source_days.get(sid, 0)
            impact_views_30d = source_usage.get(sid, {}).get("impact_views_30d", 0)
        elif rid is not None:
            asset_type = "report"
            asset_id = rid
            asset_name = r["report_name"]
            asset_days = report_days.get(rid, 0)
            impact_views_30d = report_usage.get(rid, {}).get("impact_views_30d", 0)
        elif tid is not None:
            asset_type = "scheduled_task"
            asset_id = tid
            asset_name = r["task_name"]
            asset_days = 0  # no meaningful day count for task failures yet
            impact_views_30d = 0
        elif scid is not None:
            asset_type = "script"
            asset_id = scid
            asset_name = r["script_name"]
            asset_days = 0
            impact_views_30d = 0
        elif fid is not None:
            asset_type = "flow"
            asset_id = fid
            asset_name = r["flow_name"]
            try:
                created = datetime.fromisoformat(r["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                asset_days = max(0, (datetime.now(timezone.utc) - created).days)
            except (ValueError, TypeError, AttributeError):
                asset_days = 0
            impact_views_30d = 0
        elif r["type"] == "pbi_reconnect":
            asset_type = "system"
            asset_id = None
            asset_name = "Power BI connection"
            asset_days = 0
            impact_views_30d = 0
        else:
            asset_type = None
            asset_id = None
            asset_name = None
            asset_days = 0
            impact_views_30d = 0

        # For source-tied alerts, surface the top affected report
        linked = source_reports.get(sid, []) if sid else []
        names = [rn for _, rn in linked]
        top_rid, top_rname, top_days = None, None, 0
        for lrid, lrname in linked:
            d = report_degradation.get(lrid, 0)
            if d >= top_days:
                top_rid, top_rname, top_days = lrid, lrname, d

        # Troubleshooting details shown inline on the alert row
        detail_items: list[dict] = []
        if r["type"] == "schedule_mismatch" and rid is not None:
            detail_items = report_stale_sources.get(rid, [])
        recommendation = _recommendation_for(r["type"], detail_items)

        action = ActionOut(
            id=r["id"],
            source_id=sid,
            source_name=r["source_name"],
            source_type=r["source_type"],
            report_id=rid,
            report_name=r["report_name"],
            flow_id=fid,
            flow_name=r["flow_name"],
            report_names=names,
            top_report_id=top_rid,
            top_report_name=top_rname,
            top_report_degradation_days=top_days,
            source_days_outdated=source_days.get(sid, 0) if sid else 0,
            asset_type=asset_type,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_days=asset_days,
            detail_items=detail_items,
            recommendation=recommendation,
            type=r["type"],
            status=r["status"],
            assigned_to=r["assigned_to"],
            notes=r["notes"],
            fingerprint=r["fingerprint"] if "fingerprint" in r.keys() else None,
            check_id=r["check_id"] if "check_id" in r.keys() else None,
            impact_views_30d=impact_views_30d,
            degraded_since=r["created_at"],
            query_changes=query_change_map.get(int(r["id"]), []),
            pbi_refresh_error=r["pbi_refresh_error"] if rid is not None else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            resolved_at=r["resolved_at"],
        )
        results.append(_apply_triage(action))

    # Sort: open first; then by transparent fix-first score.
    def sort_key(a: ActionOut):
        is_closed = a.status in ("resolved", "expected")
        return (
            1 if is_closed else 0,
            -a.triage_score,
            -(datetime.fromisoformat(a.created_at).timestamp() if a.created_at else 0),
        )
    results.sort(key=sort_key)

    # Dedupe only identical managed findings. Distinct problems on the same
    # asset must remain visible (for example freshness plus data quality).
    seen: set[tuple] = set()
    deduped: list[ActionOut] = []
    for a in results:
        if a.asset_type is None or a.asset_id is None:
            deduped.append(a)
            continue
        family = (
            "source_freshness"
            if a.type in FRESHNESS_ACTION_TYPES
            else "changed_query"
            if a.type == "changed_query"
            else a.fingerprint or a.type
        )
        key = (a.asset_type, a.asset_id, family)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    rank = 1
    for action in deduped:
        if action.status in ("resolved", "expected"):
            continue
        action.triage_rank = rank
        rank += 1
    return deduped


@router.get("/{action_id}/occurrences")
def list_action_occurrences(action_id: int):
    """Return immutable exact-run occurrences and their latest analysis.

    ``actions`` is the canonical user-facing Alert lifecycle. Occurrences are
    append-only evidence revisions; AI traces remain separate audit records and
    are referenced here rather than copied into the Alert payload.
    """
    with get_db() as db:
        action = db.execute(
            """SELECT id, status, evidence_revision, evidence_hash
                 FROM actions WHERE id=?""",
            (action_id,),
        ).fetchone()
        if not action:
            raise HTTPException(status_code=404, detail="Alert not found")

        rows = db.execute(
            """SELECT id, action_id, evidence_revision, focus_type, focus_id,
                      summary, evidence_json, observed_at, created_at
                 FROM action_occurrences
                WHERE action_id=?
                ORDER BY evidence_revision DESC, id DESC
                LIMIT 50""",
            (action_id,),
        ).fetchall()
        analyses = db.execute(
            """SELECT ar.id, ar.action_evidence_revision, ar.status,
                      ar.superseded_at, ar.superseded_reason,
                      ar.created_at, ar.finished_at
                 FROM agent_runs ar
                WHERE ar.action_id=?
                  AND ar.action_evidence_revision IS NOT NULL
                  AND ar.focus_type IN ('flow_run','pipeline_run')
                  AND ar.id=(
                      SELECT MAX(newer.id) FROM agent_runs newer
                       WHERE newer.action_id=ar.action_id
                         AND newer.action_evidence_revision=ar.action_evidence_revision
                         AND newer.focus_type IN ('flow_run','pipeline_run')
                  )
                ORDER BY ar.action_evidence_revision DESC
                LIMIT 50""",
            (action_id,),
        ).fetchall()
        current_analysis = db.execute(
            """SELECT id, status, provider_mode, superseded_at,
                      superseded_reason, created_at, finished_at
                 FROM agent_runs
                WHERE action_id=? AND action_evidence_revision=?
                  AND focus_type='alert'
                ORDER BY id DESC LIMIT 1""",
            (action_id, int(action["evidence_revision"] or 0)),
        ).fetchone()

    latest_analysis: dict[int, dict] = {}
    for row in analyses:
        revision = row["action_evidence_revision"]
        if revision is None or int(revision) in latest_analysis:
            continue
        latest_analysis[int(revision)] = dict(row)

    current_revision = int(action["evidence_revision"] or 0)
    action_is_active = action["status"] in {"open", "acknowledged", "investigating"}
    occurrences = []
    for row in rows:
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except (TypeError, ValueError):
            evidence = {}
        if not isinstance(evidence, dict):
            evidence = {}
        revision = int(row["evidence_revision"])
        analysis = latest_analysis.get(revision)
        analysis_is_current = bool(
            analysis
            and not analysis.get("superseded_at")
            and action_is_active
            and revision == current_revision
        )
        occurrences.append({
            "occurrence_id": int(row["id"]),
            "action_id": int(row["action_id"]),
            "evidence_revision": revision,
            "focus_type": row["focus_type"],
            "focus_id": row["focus_id"],
            "status": evidence.get("status") or "recorded",
            "summary": row["summary"],
            "label": row["summary"],
            "evidence": evidence,
            "observed_at": row["observed_at"],
            "created_at": row["created_at"],
            "is_current": action_is_active and revision == current_revision,
            "latest_analysis_run_id": int(analysis["id"]) if analysis else None,
            "analysis_status": analysis.get("status") if analysis else None,
            "analysis_is_current": analysis_is_current,
            "analysis_superseded_reason": (
                analysis.get("superseded_reason") if analysis else None
            ),
        })

    return {
        "action_id": int(action["id"]),
        "action_status": action["status"],
        "evidence_revision": current_revision,
        "current_analysis_run_id": (
            int(current_analysis["id"]) if current_analysis else None
        ),
        "current_analysis_status": (
            current_analysis["status"] if current_analysis else "pending"
        ),
        "current_analysis_provider_mode": (
            current_analysis["provider_mode"] if current_analysis else None
        ),
        "current_analysis_is_current": bool(
            current_analysis
            and not current_analysis["superseded_at"]
            and action_is_active
        ),
        "occurrences": occurrences,
    }


# Alert message prefix per action type, for resolving the linked alert when a
# person resolves the action. Explicit map only - types without a known alert
# pattern leave alerts untouched.
_ACTION_ALERT_MESSAGE_PREFIX = {
    "stale_source": "Source data is outside freshness rule%",
    "outdated_source": "Source data is outside freshness rule%",
    "error_source": "Source data is outside freshness rule%",
    "empty_source": "Source row count dropped%",
}

_FRESHNESS_ALERT_ACTION_TYPES = {"stale_source", "outdated_source", "error_source"}


def _action_alert_is_still_supported(db, source_id: int, action_type: str) -> bool:
    """Return whether current probe evidence still supports reopening the alert."""
    if action_type in _FRESHNESS_ALERT_ACTION_TYPES:
        row = db.execute(
            """SELECT status FROM source_probes
               WHERE source_id = ?
               ORDER BY probed_at DESC, id DESC LIMIT 1""",
            (source_id,),
        ).fetchone()
        return bool(row and row["status"] in {"outdated", "stale", "error"})
    if action_type == "empty_source":
        row = db.execute(
            """SELECT row_count FROM source_probes
               WHERE source_id = ? AND row_count IS NOT NULL
               ORDER BY probed_at DESC, id DESC LIMIT 1""",
            (source_id,),
        ).fetchone()
        return bool(row and row["row_count"] == 0)
    return False


@router.patch("/{action_id}", response_model=ActionOut)
def update_action(action_id: int, update: ActionUpdate, request: Request):
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        existing = db.execute(
            "SELECT id, source_id, type, status, fingerprint FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Action not found")

        if (
            update.status in {"open", "acknowledged", "investigating", "expected"}
            and existing["fingerprint"]
        ):
            newer = db.execute(
                """SELECT id FROM actions
                    WHERE type=? AND fingerprint=? AND id!=?
                      AND status IN ('open','acknowledged','investigating','expected')
                    ORDER BY id DESC LIMIT 1""",
                (existing["type"], existing["fingerprint"], action_id),
            ).fetchone()
            if newer:
                raise HTTPException(
                    status_code=409,
                    detail="A newer active Alert already represents this issue.",
                )

        fields = ["updated_at = ?"]
        values = [now]

        for field_name, value in update.model_dump(exclude_unset=True).items():
            fields.append(f"{field_name} = ?")
            values.append(value)

        # Keep the lifecycle timestamp aligned when actions are closed/reopened.
        if update.status in ("resolved", "expected"):
            fields.append("resolved_at = ?")
            values.append(now)
        elif update.status is not None:
            fields.append("resolved_at = NULL")

        values.append(action_id)
        db.execute(
            f"UPDATE actions SET {', '.join(fields)} WHERE id = ?",
            values,
        )

        # Keep the latest linked alert in sync with explicit action lifecycle
        # changes. Reopening is evidence-gated so a stale UI action cannot
        # resurrect an alert after the source has recovered.
        message_prefix = _ACTION_ALERT_MESSAGE_PREFIX.get(existing["type"])
        if (update.status in ("resolved", "expected")
                and existing["source_id"] is not None and message_prefix):
            db.execute(
                """UPDATE alerts
                   SET resolution_status = 'resolved', resolved_at = ?,
                       acknowledged = 1, acknowledged_by = ?,
                       resolution_reason = 'Linked action resolved'
                   WHERE source_id = ? AND message LIKE ?
                     AND COALESCE(resolution_status, '') != 'resolved'""",
                (now, get_actor(request) or "user", existing["source_id"], message_prefix),
            )
        elif (update.status in ("open", "investigating")
              and existing["source_id"] is not None and message_prefix
              and _action_alert_is_still_supported(
                  db, existing["source_id"], existing["type"]
              )):
            db.execute(
                """UPDATE alerts
                   SET resolution_status = NULL, resolution_reason = NULL,
                       resolved_at = NULL, acknowledged = 0,
                       acknowledged_by = NULL
                   WHERE id = (
                       SELECT id FROM alerts
                       WHERE source_id = ? AND message LIKE ?
                       ORDER BY created_at DESC, id DESC LIMIT 1
                   )""",
                (existing["source_id"], message_prefix),
            )

        changed = ", ".join(k for k in update.model_dump(exclude_unset=True))
        log_event(db, "action", action_id, None, "updated", changed, get_actor(request))

        r = db.execute("""
            SELECT a.*, s.name AS source_name, r.name AS report_name
            FROM actions a
            LEFT JOIN sources s ON s.id = a.source_id
            LEFT JOIN reports r ON r.id = a.report_id
            WHERE a.id = ?
        """, (action_id,)).fetchone()

    return ActionOut(
        id=r["id"],
        source_id=r["source_id"],
        source_name=r["source_name"],
        report_id=r["report_id"],
        report_name=r["report_name"],
        type=r["type"],
        status=r["status"],
        assigned_to=r["assigned_to"],
        notes=r["notes"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        resolved_at=r["resolved_at"],
    )
