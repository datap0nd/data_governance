"""Per-artifact query version history and change attribution.

Tracks two artifact kinds in ``query_versions``:

- ``report_table``: the M expression of one table in one Power BI report,
  identified by (report_id, table_name). This replaces the old source-level
  detection that compared the deduplicated ``sources.source_query`` value
  and could miss changes or blame a shared table.
- ``mv``: the SQL definition of a PostgreSQL materialized view that is
  tracked as a source, identified by (source_id, schema-qualified name).

Versions are retained indefinitely. Comparison uses a normalized hash:
line endings and trailing/outer whitespace are ignored, while comments and
query tokens stay meaningful. A revert to earlier text creates a new
version rather than reusing the old row, so history stays ordered.
"""

from __future__ import annotations

import hashlib

REPORT_TABLE_KIND = "report_table"
MV_KIND = "mv"

ACTIVE_ACTION_STATUSES = ("open", "acknowledged", "investigating")


def normalize_query_text(text: str | None) -> str:
    """Normalize line endings and trailing/outer whitespace for comparison."""
    if not text:
        return ""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in unified.split("\n")]
    return "\n".join(lines).strip()


def query_hash(text: str | None) -> str:
    return hashlib.sha256(normalize_query_text(text).encode("utf-8")).hexdigest()


def _latest_versions(db, artifact_kind: str, *, report_id: int | None = None,
                     source_id: int | None = None) -> dict[str, dict]:
    """Latest version row per artifact_name for one report or source."""
    if report_id is not None:
        rows = db.execute(
            """SELECT qv.* FROM query_versions qv
               WHERE qv.artifact_kind = ? AND qv.report_id = ?
                 AND qv.id = (
                     SELECT MAX(qv2.id) FROM query_versions qv2
                     WHERE qv2.artifact_kind = qv.artifact_kind
                       AND qv2.report_id = qv.report_id
                       AND qv2.artifact_name = qv.artifact_name
                 )""",
            (artifact_kind, report_id),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT qv.* FROM query_versions qv
               WHERE qv.artifact_kind = ? AND qv.source_id = ?
                 AND qv.id = (
                     SELECT MAX(qv2.id) FROM query_versions qv2
                     WHERE qv2.artifact_kind = qv.artifact_kind
                       AND qv2.source_id = qv.source_id
                       AND qv2.artifact_name = qv.artifact_name
                 )""",
            (artifact_kind, source_id),
        ).fetchall()
    return {row["artifact_name"]: dict(row) for row in rows}


def _record_version(db, *, artifact_kind: str, report_id: int | None,
                    source_id: int | None, artifact_name: str, language: str,
                    query_text: str | None, prev_version_id: int | None,
                    scan_run_id: int | None, change_kind: str, now: str) -> int:
    cursor = db.execute(
        """INSERT INTO query_versions
           (artifact_kind, report_id, source_id, artifact_name, language,
            query_text, normalized_hash, prev_version_id, scan_run_id,
            change_kind, detected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            artifact_kind,
            report_id,
            source_id,
            artifact_name,
            language,
            query_text,
            query_hash(query_text),
            prev_version_id,
            scan_run_id,
            change_kind,
            now,
        ),
    )
    return cursor.lastrowid


def _change_set_fingerprint(prefix: str, changes: list[dict]) -> str:
    digest = hashlib.sha256(
        "|".join(
            f"{change['artifact_name']}:{query_hash(change['query_text'])}"
            for change in sorted(changes, key=lambda c: c["artifact_name"])
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _supersede_and_upsert_action(db, *, fingerprint: str, report_id: int | None,
                                 source_id: int | None, owner: str | None,
                                 notes: str, now: str) -> int:
    """Resolve prior active query-change actions for the same artifact and
    create (or refresh) the action for the current change set."""
    placeholders = "report_id = ?" if report_id is not None else "source_id = ?"
    artifact_id = report_id if report_id is not None else source_id
    db.execute(
        f"""UPDATE actions
            SET status='resolved', resolved_at=?, updated_at=?,
                notes=COALESCE(notes, '') || ' [auto-resolved: superseded query change]'
            WHERE {placeholders} AND type='changed_query'
              AND fingerprint IS NOT NULL AND fingerprint != ?
              AND status IN ('open','acknowledged','investigating')""",
        (now, now, artifact_id, fingerprint),
    )
    existing = db.execute(
        "SELECT id FROM actions WHERE fingerprint = ? AND status != 'resolved'",
        (fingerprint,),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE actions SET notes = ?, assigned_to = COALESCE(assigned_to, ?), updated_at = ? WHERE id = ?",
            (notes, owner, now, existing["id"]),
        )
        return existing["id"]
    cursor = db.execute(
        """INSERT INTO actions
           (source_id, report_id, type, status, assigned_to, notes, fingerprint,
            created_at, updated_at)
           VALUES (?, ?, 'changed_query', 'open', ?, ?, ?, ?, ?)""",
        (source_id, report_id, owner, notes, fingerprint, now, now),
    )
    return cursor.lastrowid


def sync_report_query_versions(db, report_id: int, report_name: str,
                               table_expressions: dict[str, str | None],
                               scan_run_id: int | None, now: str) -> dict:
    """Version each report table's M expression and alert on real changes.

    ``table_expressions`` maps table_name -> raw M expression for every
    non-metadata table seen in this scan. The first observation of a report
    records baselines without alerting. Later scans treat added, removed,
    or edited table expressions as changes, grouped into a single
    ``changed_query`` action per report per scan.

    Returns {"changes": [...], "baselined": int, "action_id": int | None}.
    """
    present: dict[str, str] = {}
    for table_name, expression in table_expressions.items():
        if normalize_query_text(expression):
            present[table_name] = expression

    latest = _latest_versions(db, REPORT_TABLE_KIND, report_id=report_id)

    if not latest:
        baselined = 0
        for table_name, expression in present.items():
            _record_version(
                db,
                artifact_kind=REPORT_TABLE_KIND,
                report_id=report_id,
                source_id=None,
                artifact_name=table_name,
                language="m",
                query_text=expression,
                prev_version_id=None,
                scan_run_id=scan_run_id,
                change_kind="baseline",
                now=now,
            )
            baselined += 1
        return {"changes": [], "baselined": baselined, "action_id": None}

    changes: list[dict] = []
    for table_name, expression in present.items():
        prev = latest.get(table_name)
        if prev is None:
            changes.append({
                "artifact_name": table_name,
                "query_text": expression,
                "prev_version_id": None,
                "change_kind": "added",
            })
        elif prev["normalized_hash"] != query_hash(expression):
            changes.append({
                "artifact_name": table_name,
                "query_text": expression,
                "prev_version_id": prev["id"],
                "change_kind": "restored" if prev["query_text"] is None else "changed",
            })

    for table_name, prev in latest.items():
        if table_name in present:
            continue
        if prev["query_text"] is None:
            continue  # already recorded as removed
        changes.append({
            "artifact_name": table_name,
            "query_text": None,
            "prev_version_id": prev["id"],
            "change_kind": "removed",
        })

    if not changes:
        return {"changes": [], "baselined": 0, "action_id": None}

    version_ids = []
    for change in changes:
        version_id = _record_version(
            db,
            artifact_kind=REPORT_TABLE_KIND,
            report_id=report_id,
            source_id=None,
            artifact_name=change["artifact_name"],
            language="m",
            query_text=change["query_text"],
            prev_version_id=change["prev_version_id"],
            scan_run_id=scan_run_id,
            change_kind=change["change_kind"],
            now=now,
        )
        change["version_id"] = version_id
        version_ids.append(version_id)

    owner_row = db.execute(
        "SELECT NULLIF(TRIM(COALESCE(owner, '')), '') AS owner FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    owner = owner_row["owner"] if owner_row else None

    summary = ", ".join(
        f"{change['artifact_name']} ({change['change_kind']})"
        for change in sorted(changes, key=lambda c: c["artifact_name"])
    )
    notes = (
        f"{len(changes)} M query change(s) detected in report {report_name}: {summary}. "
        f"Use the query history to view each diff."
    )
    fingerprint = _change_set_fingerprint(f"changed_query:report:{report_id}", changes)
    action_id = _supersede_and_upsert_action(
        db,
        fingerprint=fingerprint,
        report_id=report_id,
        source_id=None,
        owner=owner,
        notes=notes,
        now=now,
    )
    db.executemany(
        "UPDATE query_versions SET action_id = ? WHERE id = ?",
        [(action_id, version_id) for version_id in version_ids],
    )
    return {"changes": changes, "baselined": 0, "action_id": action_id}


def _mv_owner(db, source_id: int) -> str | None:
    """Source owner, falling back to the owner of a linked (downstream) report."""
    row = db.execute(
        "SELECT NULLIF(TRIM(COALESCE(owner, '')), '') AS owner FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row and row["owner"]:
        return row["owner"]
    fallback = db.execute(
        """WITH RECURSIVE downstream(id) AS (
               SELECT ?
               UNION
               SELECT sd.source_id FROM source_dependencies sd
               JOIN downstream d ON sd.depends_on_id = d.id
           )
           SELECT r.owner FROM report_tables rt
           JOIN reports r ON r.id = rt.report_id
           WHERE rt.source_id IN (SELECT id FROM downstream)
             AND NULLIF(TRIM(COALESCE(r.owner, '')), '') IS NOT NULL
           ORDER BY r.id LIMIT 1""",
        (source_id,),
    ).fetchone()
    return fallback["owner"] if fallback else None


def sync_mv_query_version(db, source_id: int, mv_name: str, definition: str,
                          scan_run_id: int | None, now: str) -> dict:
    """Version one tracked MV's SQL definition and alert on real changes.

    The first observed definition becomes a baseline without an alert.
    Later definition changes create an MV-linked ``changed_query`` action
    owned by the source owner, falling back to a linked report's owner.

    Returns {"changed": bool, "baselined": bool, "action_id": int | None}.
    """
    if not normalize_query_text(definition):
        return {"changed": False, "baselined": False, "action_id": None}

    latest = _latest_versions(db, MV_KIND, source_id=source_id).get(mv_name)

    if latest is None:
        _record_version(
            db,
            artifact_kind=MV_KIND,
            report_id=None,
            source_id=source_id,
            artifact_name=mv_name,
            language="sql",
            query_text=definition,
            prev_version_id=None,
            scan_run_id=scan_run_id,
            change_kind="baseline",
            now=now,
        )
        return {"changed": False, "baselined": True, "action_id": None}

    if latest["normalized_hash"] == query_hash(definition):
        return {"changed": False, "baselined": False, "action_id": None}

    version_id = _record_version(
        db,
        artifact_kind=MV_KIND,
        report_id=None,
        source_id=source_id,
        artifact_name=mv_name,
        language="sql",
        query_text=definition,
        prev_version_id=latest["id"],
        scan_run_id=scan_run_id,
        change_kind="restored" if latest["query_text"] is None else "changed",
        now=now,
    )
    owner = _mv_owner(db, source_id)
    notes = (
        f"Materialized view definition changed for {mv_name}. "
        f"Use the query history to view the SQL diff."
    )
    fingerprint = _change_set_fingerprint(
        f"changed_query:mv:{source_id}",
        [{"artifact_name": mv_name, "query_text": definition}],
    )
    action_id = _supersede_and_upsert_action(
        db,
        fingerprint=fingerprint,
        report_id=None,
        source_id=source_id,
        owner=owner,
        notes=notes,
        now=now,
    )
    db.execute(
        "UPDATE query_versions SET action_id = ? WHERE id = ?",
        (action_id, version_id),
    )
    return {"changed": True, "baselined": False, "action_id": action_id}
