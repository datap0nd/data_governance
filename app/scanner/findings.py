"""Persistent action lifecycle helpers for scanner-managed findings."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any


OPEN_STATUSES = ("open", "acknowledged", "investigating")
_SENSITIVE_KEY = re.compile(
    r"(?i)(password|secret|token|authorization|api[_-]?key|connection|job_json|plan_json|lease|path|folder)"
)


def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(
        r"(?i)(password|token|secret|authorization|api[_ -]?key)\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)postgresql(?:\+\w+)?://[^\s]+", "postgresql://[redacted]", text)
    text = re.sub(r"(?i)(?:[A-Z]:\\|\\\\)[^\r\n,;]+", "[path]", text)
    return text[:limit]


def _compact_evidence(value: Any, *, depth: int = 0) -> Any:
    """Return small, redacted occurrence metadata—not a diagnostic dump."""
    if depth >= 4:
        return "[depth-limited]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, 500)
    if isinstance(value, dict):
        result = {}
        for key in sorted(value, key=lambda item: str(item))[:25]:
            label = str(key)[:80]
            if _SENSITIVE_KEY.search(label):
                continue
            result[label] = _compact_evidence(value[key], depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_compact_evidence(item, depth=depth + 1) for item in value[:20]]
    return _safe_text(value, 500)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finding_evidence(action_type: str, finding: dict) -> tuple[str, dict]:
    evidence = _compact_evidence({
        "type": action_type,
        "source_id": finding.get("source_id"),
        "report_id": finding.get("report_id"),
        "flow_id": finding.get("flow_id"),
        "check_id": finding.get("check_id"),
        "notes": _safe_text(finding.get("notes"), 2000),
    })
    return _hash(evidence), evidence


def record_action_occurrence(
    db,
    action_id: int,
    occurrence: dict,
    now: str,
) -> bool:
    """Append one immutable, bounded occurrence and advance alert evidence.

    The exact focus tuple is idempotent for an alert occurrence. Detailed run
    evidence remains in the run-specific read models used by the investigator.
    """
    focus_type = _safe_text(occurrence.get("focus_type"), 80)
    focus_id = _safe_text(occurrence.get("focus_id"), 200)
    if not focus_type or not focus_id:
        raise ValueError("Alert occurrences require focus_type and focus_id")
    existing = db.execute(
        """SELECT id FROM action_occurrences
           WHERE action_id=? AND focus_type=? AND focus_id=?""",
        (action_id, focus_type, focus_id),
    ).fetchone()
    if existing:
        return False

    summary = _safe_text(occurrence.get("summary"), 500) or "Operational alert occurrence"
    evidence = _compact_evidence(occurrence.get("evidence") or {})
    evidence_json = _json(evidence)
    if len(evidence_json.encode("utf-8")) > 4096:
        evidence = {"truncated": True}
        evidence_json = _json(evidence)
    evidence_hash = _hash({
        "focus_type": focus_type,
        "focus_id": focus_id,
        "summary": summary,
        "evidence": evidence,
    })
    row = db.execute(
        "SELECT evidence_revision FROM actions WHERE id=?", (action_id,)
    ).fetchone()
    if not row:
        raise ValueError("Action not found")
    revision = int(row["evidence_revision"] or 0) + 1
    observed_at = _safe_text(occurrence.get("observed_at"), 80) or now
    db.execute(
        """INSERT INTO action_occurrences
               (action_id, evidence_revision, focus_type, focus_id, evidence_hash,
                summary, evidence_json, observed_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            action_id, revision, focus_type, focus_id, evidence_hash,
            summary, evidence_json, observed_at, now,
        ),
    )
    # The database trigger supersedes linked investigations from older evidence
    # revisions without rewriting their terminal status or trace.
    db.execute(
        """UPDATE actions SET evidence_revision=?, evidence_hash=?, updated_at=?
           WHERE id=?""",
        (revision, evidence_hash, now, action_id),
    )
    return True


def refresh_action_evidence(db, action_id: int, evidence_hash: str, now: str) -> bool:
    """Advance non-run evidence only when the diagnostic facts changed."""
    row = db.execute(
        "SELECT evidence_revision, evidence_hash FROM actions WHERE id=?", (action_id,)
    ).fetchone()
    if not row or row["evidence_hash"] == evidence_hash:
        return False
    db.execute(
        """UPDATE actions
           SET evidence_revision=?, evidence_hash=?, updated_at=? WHERE id=?""",
        (int(row["evidence_revision"] or 0) + 1, evidence_hash, now, action_id),
    )
    return True


def sync_managed_actions(
    db,
    action_type: str,
    findings: Iterable[dict],
    now: str,
) -> dict:
    """Upsert current findings and resolve scanner-managed findings that cleared.

    Every finding must contain a stable ``fingerprint`` and may include
    ``source_id``, ``report_id``, ``flow_id``, ``check_id``, ``assigned_to``,
    ``notes``, and a bounded ``occurrence`` with an exact focus.
    Actions marked ``expected`` remain suppressed while the finding persists.
    A resolved finding creates a new action if it later reappears.
    """
    current = {}
    created = 0
    updated = 0
    occurrences_created = 0
    action_ids: dict[str, int] = {}

    for raw in findings:
        finding = dict(raw)
        fingerprint = str(finding.get("fingerprint") or "").strip()
        if not fingerprint:
            raise ValueError("Managed findings require a fingerprint")
        current[fingerprint] = finding

        existing = db.execute(
            """SELECT id, status FROM actions
               WHERE type = ? AND fingerprint = ?
               ORDER BY CASE
                   WHEN status IN ('open','acknowledged','investigating','expected') THEN 0
                   ELSE 1
               END, id DESC LIMIT 1""",
            (action_type, fingerprint),
        ).fetchone()

        evidence_hash, _ = _finding_evidence(action_type, finding)
        values = (
            finding.get("source_id"),
            finding.get("report_id"),
            finding.get("flow_id"),
            finding.get("check_id"),
            finding.get("notes"),
            now,
        )
        if existing and existing["status"] != "resolved":
            action_id = int(existing["id"])
            # Ownership belongs to the Alert lifecycle once it is created.
            # Detector reruns may refresh its asset/evidence fields, but must
            # not undo a person's assignment or deliberate unassignment.
            db.execute(
                """UPDATE actions
                   SET source_id = ?, report_id = ?, flow_id = ?, check_id = ?,
                       notes = ?, updated_at = ?
                   WHERE id = ?""",
                (*values, action_id),
            )
            updated += 1
        else:
            cursor = db.execute(
                """INSERT INTO actions
               (source_id, report_id, flow_id, check_id, type, status, assigned_to, notes,
                fingerprint, evidence_revision, evidence_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, 0, NULL, ?, ?)""",
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
            action_id = int(cursor.lastrowid)
            created += 1

        action_ids[fingerprint] = action_id
        occurrence = finding.get("occurrence")
        if occurrence:
            occurrences_created += int(
                record_action_occurrence(db, action_id, occurrence, now)
            )
        else:
            refresh_action_evidence(db, action_id, evidence_hash, now)

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
        "occurrences_created": occurrences_created,
        "action_ids": action_ids,
    }
