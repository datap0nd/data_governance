"""Durable SQLite state for read-only Operations Investigator runs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app import config
from app.database import get_db

PROMPT_VERSION = "operations-incident-v1"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SUPPORTED_FOCUS_TABLES = {
    "flow_run": "flow_runs",
    "pipeline_run": "pipeline_runs",
}
ACTIVE_ACTION_STATUSES = {"open", "acknowledged", "investigating"}


class RunBindingError(ValueError):
    """A requested canonical-alert binding is not safe to create."""

    code = "invalid_alert_binding"


class RunBindingNotFound(RunBindingError):
    code = "alert_occurrence_not_found"


class RunBindingConflict(RunBindingError):
    code = "alert_binding_conflict"


class RunBindingUnsupported(RunBindingError):
    code = "alert_focus_unsupported"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def safe_error(value: Exception | str, limit: int = 2000) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)(password|token|secret|authorization|api[_ -]?key)\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)postgresql(?:\+\w+)?://[^\s]+", "postgresql://[redacted]", text)
    return (text.strip() or "Unknown AI error")[:limit]


def _focus_exists_in_db(db, focus_type: str, focus_id: int) -> bool:
    table = SUPPORTED_FOCUS_TABLES.get(focus_type)
    if not table:
        return False
    return db.execute(f"SELECT 1 FROM {table} WHERE id=?", (focus_id,)).fetchone() is not None


def focus_exists(focus_type: str, focus_id: int) -> bool:
    with get_db() as db:
        return _focus_exists_in_db(db, focus_type, focus_id)


def _current_occurrence_for_focus(db, focus_type: str, focus_id: int) -> dict | None:
    """Auto-bind an explicit run focus only when one active occurrence is unambiguous."""
    rows = db.execute(
        """SELECT o.id AS occurrence_id, o.action_id, o.evidence_revision,
                  o.focus_type, o.focus_id, o.summary, o.observed_at,
                  a.status AS action_status, a.evidence_revision AS current_revision
           FROM action_occurrences o
           JOIN actions a ON a.id=o.action_id
          WHERE o.focus_type=? AND o.focus_id=?
            AND a.status IN ('open','acknowledged','investigating')
            AND o.evidence_revision=a.evidence_revision
          ORDER BY o.observed_at DESC, o.id DESC
          LIMIT 2""",
        (focus_type, str(focus_id)),
    ).fetchall()
    if len(rows) > 1:
        raise RunBindingConflict(
            "More than one active alert points to this run; select an exact alert occurrence."
        )
    return dict(rows[0]) if rows else None


def _resolve_creation_binding(
    db,
    *,
    focus_type: str | None,
    focus_id: int | None,
    action_id: int | None,
    occurrence_id: int | None,
) -> dict[str, Any]:
    """Resolve the immutable server-side focus and revision inside the write lock."""
    if (action_id is None) != (occurrence_id is None):
        raise RunBindingConflict("action_id and occurrence_id must be supplied together.")

    occurrence: dict | None = None
    if action_id is not None:
        row = db.execute(
            """SELECT o.id AS occurrence_id, o.action_id, o.evidence_revision,
                      o.focus_type, o.focus_id, o.summary, o.observed_at,
                      a.status AS action_status,
                      a.evidence_revision AS current_revision
               FROM action_occurrences o
               JOIN actions a ON a.id=o.action_id
              WHERE o.id=? AND o.action_id=?""",
            (occurrence_id, action_id),
        ).fetchone()
        if not row:
            raise RunBindingNotFound("The selected alert occurrence no longer exists.")
        occurrence = dict(row)
    else:
        if focus_type is None or focus_id is None:
            raise RunBindingConflict("A standalone investigation requires an exact run focus.")
        occurrence = _current_occurrence_for_focus(db, focus_type, focus_id)

    if occurrence is not None:
        if occurrence["action_status"] not in ACTIVE_ACTION_STATUSES:
            raise RunBindingConflict("The selected alert is no longer active.")
        if int(occurrence["evidence_revision"]) != int(occurrence["current_revision"]):
            raise RunBindingConflict("The selected alert occurrence has been superseded by newer evidence.")
        resolved_type = str(occurrence["focus_type"])
        try:
            resolved_id = int(occurrence["focus_id"])
        except (TypeError, ValueError) as exc:
            raise RunBindingUnsupported(
                "The selected alert is not linked to a supported Flow or Pipeline run."
            ) from exc
        if resolved_type not in SUPPORTED_FOCUS_TABLES or resolved_id < 1:
            raise RunBindingUnsupported(
                "The selected alert is not linked to a supported Flow or Pipeline run."
            )
        if focus_type is not None and (focus_type != resolved_type or focus_id != resolved_id):
            raise RunBindingConflict(
                "The supplied run focus does not match the alert occurrence's server-side focus."
            )
        if not _focus_exists_in_db(db, resolved_type, resolved_id):
            raise RunBindingNotFound("The run linked to this alert occurrence no longer exists.")
        return {
            "focus_type": resolved_type,
            "focus_id": resolved_id,
            "action_id": int(occurrence["action_id"]),
            "occurrence_id": int(occurrence["occurrence_id"]),
            "action_evidence_revision": int(occurrence["evidence_revision"]),
        }

    assert focus_type is not None and focus_id is not None
    if focus_type not in SUPPORTED_FOCUS_TABLES or not _focus_exists_in_db(
        db, focus_type, focus_id
    ):
        raise RunBindingNotFound("The selected run no longer exists.")
    return {
        "focus_type": focus_type,
        "focus_id": focus_id,
        "action_id": None,
        "occurrence_id": None,
        "action_evidence_revision": None,
    }


def active_run_count() -> int:
    with get_db() as db:
        row = db.execute(
            """SELECT COUNT(*) AS count FROM agent_runs
               WHERE status IN ('queued','running') AND superseded_at IS NULL"""
        ).fetchone()
    return int(row["count"] if row else 0)


def active_focus_run(focus_type: str, focus_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute(
            """SELECT id FROM agent_runs
               WHERE focus_type=? AND focus_id=? AND status IN ('queued','running')
                 AND superseded_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (focus_type, str(focus_id)),
        ).fetchone()
    return get_run(int(row["id"])) if row else None


def _insert_run(db, *, question: str, binding: dict[str, Any], actor: str | None, now: str) -> int:
    cursor = db.execute(
        """INSERT INTO agent_runs
           (mode, question, focus_type, focus_id, status, actor, model,
            reasoning_effort, provider_mode, prompt_version, action_id,
            action_evidence_revision, created_at, updated_at)
           VALUES ('incident', ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            question,
            binding["focus_type"],
            str(binding["focus_id"]),
            actor,
            config.AI_MODEL,
            config.AI_REASONING_EFFORT,
            "mock" if config.AI_MOCK else "qwen",
            PROMPT_VERSION,
            binding["action_id"],
            binding["action_evidence_revision"],
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def create_run(
    *,
    question: str,
    focus_type: str | None = None,
    focus_id: int | None = None,
    actor: str | None,
    action_id: int | None = None,
    occurrence_id: int | None = None,
) -> int:
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        binding = _resolve_creation_binding(
            db,
            focus_type=focus_type,
            focus_id=focus_id,
            action_id=action_id,
            occurrence_id=occurrence_id,
        )
        return _insert_run(db, question=question, binding=binding, actor=actor, now=now)


def create_or_reuse_run(
    *,
    question: str,
    focus_type: str | None = None,
    focus_id: int | None = None,
    actor: str | None,
    action_id: int | None = None,
    occurrence_id: int | None = None,
    max_active: int = 10,
) -> tuple[int | None, bool]:
    """Resolve linkage and deduplicate one exact immutable evidence snapshot."""
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        binding = _resolve_creation_binding(
            db,
            focus_type=focus_type,
            focus_id=focus_id,
            action_id=action_id,
            occurrence_id=occurrence_id,
        )
        existing = db.execute(
            """SELECT id FROM agent_runs
               WHERE focus_type=? AND focus_id=? AND question=? AND actor IS ?
                 AND action_id IS ? AND action_evidence_revision IS ?
                 AND status IN ('queued','running') AND superseded_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (
                binding["focus_type"],
                str(binding["focus_id"]),
                question,
                actor,
                binding["action_id"],
                binding["action_evidence_revision"],
            ),
        ).fetchone()
        if existing:
            return int(existing["id"]), False
        count = db.execute(
            """SELECT COUNT(*) AS count FROM agent_runs
               WHERE status IN ('queued','running') AND superseded_at IS NULL"""
        ).fetchone()
        if int(count["count"] if count else 0) >= max_active:
            return None, False
        return _insert_run(db, question=question, binding=binding, actor=actor, now=now), True


def _supersession_reason_for_row(db, row: dict[str, Any]) -> str | None:
    """Return a durable reason when a linked run is no longer current."""
    if row.get("superseded_at"):
        return str(row.get("superseded_reason") or "alert_superseded")

    revision = row.get("action_evidence_revision")
    action_id = row.get("action_id")
    if revision is None and action_id is None:
        return None
    if revision is None:
        return "alert_binding_invalid"
    if action_id is None:
        return "alert_removed"
    action = db.execute(
        "SELECT status, evidence_revision FROM actions WHERE id=?", (action_id,)
    ).fetchone()
    if not action:
        return "alert_removed"
    status = str(action["status"] or "")
    if status == "resolved":
        return "alert_resolved"
    if status == "expected":
        return "alert_expected"
    if status not in ACTIVE_ACTION_STATUSES:
        return "alert_inactive"
    if int(action["evidence_revision"] or 0) != int(revision):
        return "alert_evidence_changed"
    return None


def _refresh_supersession(db, row: dict[str, Any], *, now: str | None = None) -> str | None:
    reason = _supersession_reason_for_row(db, row)
    if not reason or row.get("superseded_at"):
        return reason
    marked_at = now or _iso()
    db.execute(
        """UPDATE agent_runs
              SET superseded_at=COALESCE(superseded_at, ?),
                  superseded_reason=COALESCE(superseded_reason, ?), updated_at=?
            WHERE id=?""",
        (marked_at, reason, marked_at, row["id"]),
    )
    row["superseded_at"] = marked_at
    row["superseded_reason"] = reason
    return reason


def superseded_reason(run_id: int) -> str | None:
    """Refresh and return a run's supersession reason at an execution boundary."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        selected = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not selected:
            return None
        return _refresh_supersession(db, dict(selected))


def claim_run(run_id: int) -> dict | None:
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        selected = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not selected:
            return None
        row = dict(selected)
        reason = _refresh_supersession(db, row, now=now)
        if reason and row["status"] == "queued":
            db.execute(
                """UPDATE agent_runs
                      SET status='failed', error_code='agent_evidence_superseded',
                          error='The linked alert changed or closed before analysis started.',
                          finished_at=?, updated_at=?
                    WHERE id=? AND status='queued'""",
                (now, now, run_id),
            )
            return None
        cursor = db.execute(
            """UPDATE agent_runs SET status='running', started_at=?, updated_at=?
               WHERE id=? AND status='queued' AND cancel_requested=0""",
            (now, now, run_id),
        )
        if not cursor.rowcount:
            return None
        claimed = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return dict(claimed) if claimed else None


def is_cancel_requested(run_id: int) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT cancel_requested, status FROM agent_runs WHERE id=?", (run_id,)
        ).fetchone()
    return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))


def request_cancel(run_id: int) -> bool:
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        queued = db.execute(
            """UPDATE agent_runs SET status='cancelled', cancel_requested=1,
                      error_code='agent_cancelled', error='Investigation cancelled.',
                      finished_at=?, updated_at=?
               WHERE id=? AND status='queued'""",
            (now, now, run_id),
        )
        if queued.rowcount:
            return True
        running = db.execute(
            """UPDATE agent_runs SET cancel_requested=1, updated_at=?
               WHERE id=? AND status='running'""",
            (now, run_id),
        )
        if running.rowcount:
            return True
        return db.execute(
            "SELECT 1 FROM agent_runs WHERE id=?", (run_id,)
        ).fetchone() is not None


def start_step(
    run_id: int,
    *,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[int, int]:
    now = _iso()
    with get_db() as db:
        row = db.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_no FROM agent_steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        sequence = int(row["next_no"])
        cursor = db.execute(
            """INSERT INTO agent_steps
               (run_id, sequence_no, tool_call_id, tool_name, arguments_json,
                status, started_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (run_id, sequence, tool_call_id[:200], tool_name[:100], _json(arguments), now),
        )
        db.execute(
            """UPDATE agent_runs SET tool_call_count=tool_call_count+1, updated_at=?
               WHERE id=?""",
            (now, run_id),
        )
        return int(cursor.lastrowid), sequence


def finish_step(
    step_id: int,
    *,
    status: str,
    duration_ms: int,
    result: dict[str, Any] | None = None,
    error: Exception | str | None = None,
) -> None:
    with get_db() as db:
        db.execute(
            """UPDATE agent_steps SET status=?, result_json=?, error=?,
                      finished_at=?, duration_ms=? WHERE id=?""",
            (
                status,
                _json(result) if result is not None else None,
                safe_error(error) if error is not None else None,
                _iso(),
                max(0, int(duration_ms)),
                step_id,
            ),
        )


def add_evidence(run_id: int, step_id: int, evidence: list[dict[str, Any]]) -> None:
    with get_db() as db:
        db.executemany(
            """INSERT INTO agent_evidence
               (run_id, step_id, evidence_key, entity_type, entity_id, label,
                deep_link, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, evidence_key) DO UPDATE SET
                   step_id=excluded.step_id,
                   entity_type=excluded.entity_type,
                   entity_id=excluded.entity_id,
                   label=excluded.label,
                   deep_link=excluded.deep_link,
                   observed_at=excluded.observed_at""",
            [
                (
                    run_id,
                    step_id,
                    item["reference"],
                    item["entity_type"],
                    str(item["entity_id"]),
                    item["label"],
                    item.get("deep_link"),
                    item["observed_at"],
                )
                for item in evidence
            ],
        )


def evidence_keys(run_id: int) -> set[str]:
    with get_db() as db:
        rows = db.execute(
            "SELECT evidence_key FROM agent_evidence WHERE run_id=?", (run_id,)
        ).fetchall()
    return {row["evidence_key"] for row in rows}


def complete_run(run_id: int, result: dict[str, Any], usage: dict[str, int]) -> bool:
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        selected = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not selected:
            return False
        _refresh_supersession(db, dict(selected), now=now)
        cursor = db.execute(
            """UPDATE agent_runs SET status='completed', final_json=?, usage_json=?,
                      error_code=NULL, error=NULL, finished_at=?, updated_at=?
               WHERE id=? AND status='running' AND cancel_requested=0""",
            (_json(result), _json(usage), now, now, run_id),
        )
        if cursor.rowcount:
            return True
        db.execute(
            """UPDATE agent_runs SET status='cancelled', error_code='agent_cancelled',
                      error='Investigation cancelled.', finished_at=?, updated_at=?
               WHERE id=? AND status='running' AND cancel_requested=1""",
            (now, now, run_id),
        )
        return False


def fail_run(run_id: int, *, error_code: str, error: Exception | str) -> None:
    now = _iso()
    with get_db() as db:
        db.execute(
            """UPDATE agent_runs
               SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'failed' END,
                   error_code=CASE WHEN cancel_requested=1 THEN 'agent_cancelled' ELSE ? END,
                   error=CASE WHEN cancel_requested=1 THEN 'Investigation cancelled.' ELSE ? END,
                   finished_at=?, updated_at=?
               WHERE id=? AND status NOT IN ('completed','failed','cancelled')""",
            (error_code, safe_error(error), now, now, run_id),
        )


def recover_interrupted_runs() -> list[int]:
    """Fail interrupted work and return durable queued rows for resubmission."""
    now = _iso()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute(
            "SELECT * FROM agent_runs WHERE status IN ('queued','running')"
        ).fetchall()
        for selected in active:
            _refresh_supersession(db, dict(selected), now=now)
        db.execute(
            """UPDATE agent_steps SET status='failed', error='Interrupted by service restart.',
                      finished_at=?
               WHERE status='running' AND run_id IN (
                   SELECT id FROM agent_runs WHERE status='running'
               )""",
            (now,),
        )
        db.execute(
            """UPDATE agent_runs SET status='cancelled', error_code='agent_cancelled',
                      error='Investigation cancelled before service restart completed.',
                      finished_at=?, updated_at=?
               WHERE status IN ('queued','running') AND cancel_requested=1""",
            (now, now),
        )
        db.execute(
            """UPDATE agent_runs
                  SET status='failed', error_code='agent_evidence_superseded',
                      error='The linked alert changed or closed before analysis could continue.',
                      finished_at=?, updated_at=?
                WHERE status IN ('queued','running') AND cancel_requested=0
                  AND superseded_at IS NOT NULL""",
            (now, now),
        )
        db.execute(
            """UPDATE agent_runs SET status='failed', error_code='service_restart',
                      error='Investigation interrupted by the Metronome service restarting.',
                      finished_at=?, updated_at=?
               WHERE status='running' AND cancel_requested=0""",
            (now, now),
        )
        queued = db.execute(
            """SELECT id FROM agent_runs
               WHERE status='queued' AND cancel_requested=0
                 AND superseded_at IS NULL ORDER BY id"""
        ).fetchall()
    return [int(row["id"]) for row in queued]


def _alert_binding_details(db, row: dict[str, Any]) -> dict[str, Any] | None:
    revision = row.get("action_evidence_revision")
    action_id = row.get("action_id")
    if revision is None and action_id is None:
        return None
    action = None
    if action_id is not None:
        action = db.execute(
            "SELECT id, status, evidence_revision FROM actions WHERE id=?",
            (action_id,),
        ).fetchone()
    occurrence = None
    if action_id is not None and revision is not None:
        occurrence = db.execute(
            """SELECT id, focus_type, focus_id, summary, observed_at
                 FROM action_occurrences
                WHERE action_id=? AND evidence_revision=?""",
            (action_id, revision),
        ).fetchone()
    current_revision = int(action["evidence_revision"]) if action else None
    action_status = str(action["status"]) if action else None
    is_current = bool(
        action
        and revision is not None
        and action_status in ACTIVE_ACTION_STATUSES
        and current_revision == int(revision)
        and not row.get("superseded_at")
    )
    return {
        "action_id": int(action_id) if action_id is not None else None,
        "occurrence_id": int(occurrence["id"]) if occurrence else None,
        "evidence_revision": int(revision) if revision is not None else None,
        "current_evidence_revision": current_revision,
        "action_status": action_status,
        "focus": {
            "type": str(occurrence["focus_type"]),
            "id": int(occurrence["focus_id"]),
        } if occurrence else None,
        "summary": str(occurrence["summary"]) if occurrence else None,
        "observed_at": occurrence["observed_at"] if occurrence else None,
        "is_current": is_current,
        "superseded_at": row.get("superseded_at"),
        "superseded_reason": row.get("superseded_reason"),
    }


def get_run(run_id: int) -> dict | None:
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        selected = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not selected:
            return None
        row = dict(selected)
        _refresh_supersession(db, row)
        selected = db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not selected:
            return None
        row = dict(selected)
        binding = _alert_binding_details(db, row)
        steps = db.execute(
            """SELECT id, sequence_no, tool_name, status, error, started_at,
                      finished_at, duration_ms
               FROM agent_steps WHERE run_id=? ORDER BY sequence_no""",
            (run_id,),
        ).fetchall()
        evidence = db.execute(
            """SELECT evidence_key AS reference, entity_type, entity_id, label,
                      deep_link, observed_at
               FROM agent_evidence WHERE run_id=? ORDER BY id""",
            (run_id,),
        ).fetchall()
    result = row
    result["focus_id"] = int(result["focus_id"])
    decoded_result = _loads(result.pop("final_json"), None)
    result["usage"] = _loads(result.pop("usage_json"), {})
    result["steps"] = [dict(item) for item in steps]
    result["evidence"] = [dict(item) for item in evidence]
    result["alert_binding"] = binding
    result["occurrence_id"] = binding["occurrence_id"] if binding else None
    result["alert_evidence_revision"] = (
        binding["evidence_revision"] if binding else None
    )
    result["current_alert_evidence_revision"] = (
        binding["current_evidence_revision"] if binding else None
    )
    recommendations_current = bool(
        not result.get("superseded_at")
        and (binding is None or binding["is_current"])
    )
    result["is_current"] = recommendations_current
    result["stale"] = not recommendations_current
    result["superseded"] = bool(result.get("superseded_at"))
    result["recommendations_current"] = recommendations_current
    if isinstance(decoded_result, dict) and not recommendations_current:
        decoded_result = dict(decoded_result)
        historical = decoded_result.get("recommendations")
        if isinstance(historical, list):
            decoded_result["historical_recommendations"] = historical
        decoded_result["recommendations"] = []
    result["result"] = decoded_result
    result["read_only"] = True
    result["operational_actions_enabled"] = False
    return result
