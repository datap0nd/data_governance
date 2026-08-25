"""Read APIs for per-artifact query version history and diffs.

History listings are lightweight (no query text). Full text is loaded only
by the compare endpoint, which aligns two versions of the same artifact
into side-by-side diff rows.
"""

from difflib import SequenceMatcher

from fastapi import APIRouter, HTTPException, Query

from app.database import get_db
from app.scanner.query_history import normalize_query_text

router = APIRouter(prefix="/api/query-history", tags=["query-history"])


def _version_summary(row) -> dict:
    return {
        "id": row["id"],
        "artifact_kind": row["artifact_kind"],
        "report_id": row["report_id"],
        "source_id": row["source_id"],
        "artifact_name": row["artifact_name"],
        "language": row["language"],
        "change_kind": row["change_kind"],
        "prev_version_id": row["prev_version_id"],
        "scan_run_id": row["scan_run_id"],
        "action_id": row["action_id"],
        "detected_at": row["detected_at"],
        "has_text": row["query_text"] is not None,
    }


@router.get("/reports/{report_id}")
def report_query_history(report_id: int):
    """M-query history for one report, grouped by report table."""
    with get_db() as db:
        report = db.execute(
            "SELECT id, name FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        rows = db.execute(
            """SELECT id, artifact_kind, report_id, source_id, artifact_name,
                      language, change_kind, prev_version_id, scan_run_id,
                      action_id, detected_at, query_text
               FROM query_versions
               WHERE artifact_kind = 'report_table' AND report_id = ?
               ORDER BY artifact_name ASC, id ASC""",
            (report_id,),
        ).fetchall()

    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["artifact_name"], []).append(_version_summary(row))
    return {
        "report_id": report["id"],
        "report_name": report["name"],
        "tables": [
            {"table_name": name, "versions": versions}
            for name, versions in groups.items()
        ],
    }


@router.get("/sources/{source_id}")
def source_query_history(source_id: int):
    """MV definition history for one source."""
    with get_db() as db:
        source = db.execute(
            "SELECT id, name FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        rows = db.execute(
            """SELECT id, artifact_kind, report_id, source_id, artifact_name,
                      language, change_kind, prev_version_id, scan_run_id,
                      action_id, detected_at, query_text
               FROM query_versions
               WHERE artifact_kind = 'mv' AND source_id = ?
               ORDER BY artifact_name ASC, id ASC""",
            (source_id,),
        ).fetchall()

    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["artifact_name"], []).append(_version_summary(row))
    return {
        "source_id": source["id"],
        "source_name": source["name"],
        "artifacts": [
            {"artifact_name": name, "versions": versions}
            for name, versions in groups.items()
        ],
    }


def _diff_rows(before_text: str, after_text: str) -> list[dict]:
    """Aligned side-by-side rows for two query texts.

    Normalization strips line-ending and trailing-whitespace noise so the
    diff highlights meaningful edits (comments and tokens included).
    """
    before_lines = normalize_query_text(before_text).split("\n") if normalize_query_text(before_text) else []
    after_lines = normalize_query_text(after_text).split("\n") if normalize_query_text(after_text) else []

    rows: list[dict] = []
    matcher = SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append({
                    "kind": "context",
                    "left_line": i1 + offset + 1,
                    "right_line": j1 + offset + 1,
                    "left_text": before_lines[i1 + offset],
                    "right_text": after_lines[j1 + offset],
                })
        elif tag == "delete":
            for i in range(i1, i2):
                rows.append({
                    "kind": "removed",
                    "left_line": i + 1,
                    "right_line": None,
                    "left_text": before_lines[i],
                    "right_text": None,
                })
        elif tag == "insert":
            for j in range(j1, j2):
                rows.append({
                    "kind": "added",
                    "left_line": None,
                    "right_line": j + 1,
                    "left_text": None,
                    "right_text": after_lines[j],
                })
        else:  # replace: pair up lines so edits stay side by side
            span = max(i2 - i1, j2 - j1)
            for offset in range(span):
                left = i1 + offset if i1 + offset < i2 else None
                right = j1 + offset if j1 + offset < j2 else None
                rows.append({
                    "kind": "changed" if left is not None and right is not None
                            else "removed" if left is not None else "added",
                    "left_line": left + 1 if left is not None else None,
                    "right_line": right + 1 if right is not None else None,
                    "left_text": before_lines[left] if left is not None else None,
                    "right_text": after_lines[right] if right is not None else None,
                })
    return rows


def _artifact_key(row) -> tuple:
    return (row["artifact_kind"], row["report_id"], row["source_id"], row["artifact_name"])


@router.get("/compare")
def compare_query_versions(
    to_id: int = Query(..., description="Version shown as After"),
    from_id: int | None = Query(None, description="Version shown as Before; defaults to the After version's recorded predecessor. Pass 0 to compare against an explicitly empty Before."),
):
    """Aligned before/after diff between two versions of the same artifact."""
    with get_db() as db:
        to_row = db.execute(
            "SELECT * FROM query_versions WHERE id = ?", (to_id,)
        ).fetchone()
        if not to_row:
            raise HTTPException(status_code=404, detail="Version not found")

        from_row = None
        effective_from_id = from_id if from_id is not None else to_row["prev_version_id"]
        if effective_from_id == 0:
            effective_from_id = None
        if effective_from_id is not None:
            from_row = db.execute(
                "SELECT * FROM query_versions WHERE id = ?", (effective_from_id,)
            ).fetchone()
            if not from_row:
                raise HTTPException(status_code=404, detail="Version not found")
            if _artifact_key(from_row) != _artifact_key(to_row):
                raise HTTPException(
                    status_code=400,
                    detail="Versions belong to different artifacts and cannot be compared",
                )

    before_text = from_row["query_text"] if from_row else None
    after_text = to_row["query_text"]
    return {
        "artifact_kind": to_row["artifact_kind"],
        "artifact_name": to_row["artifact_name"],
        "language": to_row["language"],
        "report_id": to_row["report_id"],
        "source_id": to_row["source_id"],
        "from_version": _version_summary(from_row) if from_row else None,
        "to_version": _version_summary(to_row),
        "rows": _diff_rows(before_text or "", after_text or ""),
    }
