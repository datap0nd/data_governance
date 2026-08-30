"""Versioning and comparison helpers for Power Query and PostgreSQL MV SQL."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from difflib import SequenceMatcher


REPORT_M_KIND = "report_m"
MATERIALIZED_VIEW_KIND = "materialized_view"


def normalize_query_text(value: str | None) -> str:
    """Remove platform/outer whitespace noise without changing query tokens."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def hash_query(value: str | None) -> str:
    return hashlib.sha256(normalize_query_text(value).encode("utf-8")).hexdigest()


def report_artifact_key(report_id: int, table_name: str) -> str:
    return f"report:{int(report_id)}:table:{table_name}"


def mv_artifact_key(source_id: int) -> str:
    return f"materialized-view:{int(source_id)}"


@dataclass(frozen=True)
class QueryObservation:
    changed: bool
    version_id: int
    previous_version_id: int | None
    query_hash: str


def _insert_version(
    db,
    *,
    artifact_kind: str,
    artifact_key: str,
    report_id: int | None,
    source_id: int | None,
    artifact_name: str,
    language: str,
    query_text: str | None,
    previous_version_id: int | None,
    scan_run_id: int | None,
    is_baseline: bool,
    detected_at: str,
) -> int:
    normalized = normalize_query_text(query_text)
    cursor = db.execute(
        """INSERT INTO query_versions
           (artifact_kind, artifact_key, report_id, source_id, artifact_name,
            language, query_text, query_hash, previous_version_id, scan_run_id,
            is_baseline, detected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            artifact_kind,
            artifact_key,
            report_id,
            source_id,
            artifact_name,
            language,
            normalized,
            hash_query(normalized),
            previous_version_id,
            scan_run_id,
            1 if is_baseline else 0,
            detected_at,
        ),
    )
    return int(cursor.lastrowid)


def observe_query(
    db,
    *,
    artifact_kind: str,
    artifact_key: str,
    report_id: int | None,
    source_id: int | None,
    artifact_name: str,
    language: str,
    query_text: str | None,
    scan_run_id: int | None,
    detected_at: str,
    has_saved_baseline: bool = False,
    saved_baseline_text: str | None = None,
    saved_baseline_source_id: int | None = None,
    saved_baseline_at: str | None = None,
) -> QueryObservation:
    """Record a baseline or a changed query, returning the current version.

    Existing report_tables data is used as a migration baseline. This lets the
    first v2 scan detect a real edit since the last legacy scan instead of
    silently replacing the old expression.
    """
    latest = db.execute(
        """SELECT id, query_hash FROM query_versions
           WHERE artifact_key = ? ORDER BY id DESC LIMIT 1""",
        (artifact_key,),
    ).fetchone()

    if latest is None and has_saved_baseline:
        baseline_id = _insert_version(
            db,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            report_id=report_id,
            source_id=saved_baseline_source_id,
            artifact_name=artifact_name,
            language=language,
            query_text=saved_baseline_text,
            previous_version_id=None,
            scan_run_id=None,
            is_baseline=True,
            detected_at=saved_baseline_at or detected_at,
        )
        latest = {"id": baseline_id, "query_hash": hash_query(saved_baseline_text)}

    current_hash = hash_query(query_text)
    if latest is None:
        version_id = _insert_version(
            db,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            report_id=report_id,
            source_id=source_id,
            artifact_name=artifact_name,
            language=language,
            query_text=query_text,
            previous_version_id=None,
            scan_run_id=scan_run_id,
            is_baseline=True,
            detected_at=detected_at,
        )
        return QueryObservation(False, version_id, None, current_hash)

    if latest["query_hash"] == current_hash:
        return QueryObservation(False, int(latest["id"]), None, current_hash)

    version_id = _insert_version(
        db,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        report_id=report_id,
        source_id=source_id,
        artifact_name=artifact_name,
        language=language,
        query_text=query_text,
        previous_version_id=int(latest["id"]),
        scan_run_id=scan_run_id,
        is_baseline=False,
        detected_at=detected_at,
    )
    return QueryObservation(True, version_id, int(latest["id"]), current_hash)


def link_versions_to_action(db, version_ids: list[int], action_id: int) -> None:
    if not version_ids:
        return
    placeholders = ",".join("?" for _ in version_ids)
    db.execute(
        f"UPDATE query_versions SET action_id = ? WHERE id IN ({placeholders})",
        (action_id, *version_ids),
    )


def aligned_diff_rows(before: str, after: str) -> list[dict]:
    """Return line-aligned rows suitable for a responsive side-by-side diff."""
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    matcher = SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, (old_text, new_text) in enumerate(zip(before_lines[i1:i2], after_lines[j1:j2])):
                rows.append({
                    "kind": "context",
                    "before_line": i1 + offset + 1,
                    "after_line": j1 + offset + 1,
                    "before_text": old_text,
                    "after_text": new_text,
                })
            continue
        old_block = before_lines[i1:i2]
        new_block = after_lines[j1:j2]
        for offset in range(max(len(old_block), len(new_block))):
            old_text = old_block[offset] if offset < len(old_block) else None
            new_text = new_block[offset] if offset < len(new_block) else None
            kind = "changed" if old_text is not None and new_text is not None else "removed" if old_text is not None else "added"
            rows.append({
                "kind": kind,
                "before_line": i1 + offset + 1 if old_text is not None else None,
                "after_line": j1 + offset + 1 if new_text is not None else None,
                "before_text": old_text,
                "after_text": new_text,
            })
    return rows
