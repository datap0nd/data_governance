"""Shared archive semantics for sources.

Archiving a source must never strand its open actions/alerts: archived
sources are excluded from probing and from active-alert surfaces, so
nothing would ever close those entries again. Every code path that sets
sources.archived = 1 goes through archive_source() so the cleanup is
uniform (scanner passes and the archive API alike).
"""


def archive_source(db, source_id: int, now: str, reason: str,
                   action_note: str = " [auto-resolved: source archived]") -> None:
    """Archive a source and resolve its open actions and alerts.

    reason becomes the alerts.resolution_reason; action_note is appended to
    actions.notes. Unarchiving is intentionally flag-only (callers just flip
    the column back): resolved entries are not reopened because their
    validity is unknown until the next probe, which recreates anything real.
    """
    db.execute(
        "UPDATE sources SET archived = 1, updated_at = ? WHERE id = ?",
        (now, source_id),
    )
    db.execute(
        """UPDATE actions SET status = 'resolved', resolved_at = ?,
                              updated_at = ?,
                              notes = COALESCE(notes, '') || ?
           WHERE source_id = ? AND status NOT IN ('resolved', 'expected')""",
        (now, now, action_note, source_id),
    )
    db.execute(
        """UPDATE alerts SET resolution_status = 'resolved', resolved_at = ?,
                             acknowledged = 1, acknowledged_by = 'auto',
                             resolution_reason = ?
           WHERE source_id = ?
             AND COALESCE(resolution_status, '') != 'resolved'""",
        (now, reason, source_id),
    )
