"""Persistent action lifecycle helpers for scanner-managed findings."""

from __future__ import annotations

from collections.abc import Iterable


OPEN_STATUSES = ("open", "acknowledged", "investigating")


def sync_managed_actions(
    db,
    action_type: str,
    findings: Iterable[dict],
    now: str,
) -> dict:
    """Upsert current findings and resolve scanner-managed findings that cleared.

    Every finding must contain a stable ``fingerprint`` and may include
    ``source_id``, ``report_id``, ``flow_id``, ``check_id``, ``assigned_to``,
    and ``notes``.
    Actions marked ``expected`` remain suppressed while the finding persists.
    A resolved finding creates a new action if it later reappears.
    """
    current = {}
    created = 0
    updated = 0

    for raw in findings:
        finding = dict(raw)
        fingerprint = str(finding.get("fingerprint") or "").strip()
        if not fingerprint:
            raise ValueError("Managed findings require a fingerprint")
        current[fingerprint] = finding

        existing = db.execute(
            """SELECT id, status FROM actions
               WHERE type = ? AND fingerprint = ?
               ORDER BY id DESC LIMIT 1""",
            (action_type, fingerprint),
        ).fetchone()

        values = (
            finding.get("source_id"),
            finding.get("report_id"),
            finding.get("flow_id"),
            finding.get("check_id"),
            finding.get("assigned_to"),
            finding.get("notes"),
            now,
        )
        if existing and existing["status"] != "resolved":
            db.execute(
                """UPDATE actions
                   SET source_id = ?, report_id = ?, flow_id = ?, check_id = ?, assigned_to = ?,
                       notes = ?, updated_at = ?
                   WHERE id = ?""",
                (*values, existing["id"]),
            )
            updated += 1
            continue

        db.execute(
            """INSERT INTO actions
               (source_id, report_id, flow_id, check_id, type, status, assigned_to, notes,
                fingerprint, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)""",
            (
                finding.get("source_id"),
                finding.get("report_id"),
                finding.get("flow_id"),
                finding.get("check_id"),
                action_type,
                finding.get("assigned_to"),
                finding.get("notes"),
                fingerprint,
                now,
                now,
            ),
        )
        created += 1

    active_rows = db.execute(
        """SELECT id, fingerprint FROM actions
           WHERE type = ? AND fingerprint IS NOT NULL
             AND status IN ('open', 'acknowledged', 'investigating')""",
        (action_type,),
    ).fetchall()
    resolved = 0
    for row in active_rows:
        if row["fingerprint"] in current:
            continue
        result = db.execute(
            """UPDATE actions
               SET status = 'resolved', resolved_at = ?, updated_at = ?,
                   notes = COALESCE(notes, '') || ' [auto-resolved: finding cleared]'
               WHERE id = ?""",
            (now, now, row["id"]),
        )
        resolved += result.rowcount or 0

    return {
        "current": len(current),
        "created": created,
        "updated": updated,
        "resolved": resolved,
    }
