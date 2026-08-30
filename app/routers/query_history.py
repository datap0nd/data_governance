from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Query

from app.database import get_db
from app.models import QueryDiffOut, QueryHistoryGroup
from app.query_history import aligned_diff_rows


router = APIRouter(prefix="/api/query-history", tags=["query-history"])


def _version_summary(row) -> dict:
    return {
        "id": row["id"],
        "previous_version_id": row["previous_version_id"],
        "artifact_kind": row["artifact_kind"],
        "artifact_key": row["artifact_key"],
        "artifact_name": row["artifact_name"],
        "language": row["language"],
        "query_hash": row["query_hash"],
        "is_baseline": bool(row["is_baseline"]),
        "action_id": row["action_id"],
        "detected_at": row["detected_at"],
    }


def _history_groups(rows) -> list[QueryHistoryGroup]:
    groups: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        group = groups.setdefault(row["artifact_key"], {
            "artifact_kind": row["artifact_kind"],
            "artifact_key": row["artifact_key"],
            "artifact_name": row["artifact_name"],
            "language": row["language"],
            "versions": [],
        })
        group["versions"].append(_version_summary(row))
    return [QueryHistoryGroup(**group) for group in groups.values()]


@router.get("/report/{report_id}", response_model=list[QueryHistoryGroup])
def report_query_history(report_id: int):
    with get_db() as db:
        report = db.execute("SELECT id FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(404, "Report not found")
        rows = db.execute(
            """SELECT * FROM query_versions WHERE report_id = ?
               ORDER BY artifact_name COLLATE NOCASE, id DESC""",
            (report_id,),
        ).fetchall()
    return _history_groups(rows)


@router.get("/materialized-view/{source_id}", response_model=list[QueryHistoryGroup])
def materialized_view_query_history(source_id: int):
    with get_db() as db:
        source = db.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not source:
            raise HTTPException(404, "Source not found")
        rows = db.execute(
            """SELECT * FROM query_versions
               WHERE source_id = ? AND artifact_kind = 'materialized_view'
               ORDER BY artifact_name COLLATE NOCASE, id DESC""",
            (source_id,),
        ).fetchall()
    return _history_groups(rows)


@router.get("/compare", response_model=QueryDiffOut)
def compare_query_versions(
    from_version_id: int = Query(..., ge=1),
    to_version_id: int = Query(..., ge=1),
):
    with get_db() as db:
        before = db.execute("SELECT * FROM query_versions WHERE id = ?", (from_version_id,)).fetchone()
        after = db.execute("SELECT * FROM query_versions WHERE id = ?", (to_version_id,)).fetchone()
    if not before or not after:
        raise HTTPException(404, "Query version not found")
    if before["artifact_key"] != after["artifact_key"]:
        raise HTTPException(409, "Query versions belong to different artifacts")
    return QueryDiffOut(
        artifact_kind=after["artifact_kind"],
        artifact_key=after["artifact_key"],
        artifact_name=after["artifact_name"],
        language=after["language"],
        before=_version_summary(before),
        after=_version_summary(after),
        rows=aligned_diff_rows(before["query_text"], after["query_text"]),
    )
