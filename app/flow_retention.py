"""Per-run download folders and the retention operations that clean them up.

Every producing flow run saves its immutable files into an owned subfolder,
named ``#<run_id>_<dd-mm-yyyy>``. The parent is either the configured target
(``run_folders``) or the target's hashed worker-private store
(``direct_replace``). The Metronome server keeps only the newest three run
folders per parent: it records each folder when the worker registers it,
decides transactionally which recorded folders are old enough to remove, and
hands the worker pre-recorded retention operations to execute.

This module is the only place in the worker that deletes anything, and it
deletes only what an operation from the server names: a path the server itself
recorded when that run registered its folder, double-checked here against the
on-disk ownership marker before anything is touched. User files and folders in
the configured target folder are never retention candidates.

Deletion is a two-step, crash-safe protocol. The folder is first renamed to
the operation's server-chosen tombstone name (atomic on the same volume: it
either fully succeeds or leaves the folder fully intact), then the tombstone
is removed. A crash between the two steps leaves remains only under the
tombstone path the database already knows, and the next assigned run
reconciles it - execution always inspects the real state of both paths before
acting, so every operation is safe to retry.
"""

from __future__ import annotations

import json
import re
import shutil
from app.flow_clock import dubai_today
from datetime import timezone, date, datetime
from pathlib import Path

RUN_FOLDER_KEEP = 3
MARKER_NAME = ".metronome_run.json"
# The run-folder shape this module creates, plus the " (2)" suffix used when a
# user's own folder already occupies the exact name.
RUN_FOLDER_RE = re.compile(r"^#(\d+)_(\d{2})-(\d{2})-(\d{4})(?: \(\d+\))?$")
TOMBSTONE_RE = re.compile(r"^\.#\d+_\d{2}-\d{2}-\d{4}(?: \(\d+\))?\.op\d+\.deleting$")


def run_folder_name(run_id: int, on_date: date | None = None) -> str:
    return f"#{run_id}_{(on_date or dubai_today()).strftime('%d-%m-%Y')}"


def tombstone_name(folder_name: str, op_id: int) -> str:
    """The server-side name choice, mirrored here for tests and the server."""
    return f".{folder_name}.op{op_id}.deleting"


def read_marker(folder: Path) -> dict | None:
    try:
        loaded = json.loads((folder / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def create_run_folder(target: Path, run_id: int, flow_id: int | None) -> Path:
    """Create (or re-enter) this run's folder inside the target folder.

    The ownership marker written here is what later allows a retention
    operation to touch the folder. An existing folder with the exact name is
    reused only when its marker names this same run (a re-claimed run);
    anything unmarked or foreign belongs to the user, so a suffixed sibling is
    created instead and the user's folder is left alone.
    """
    base = run_folder_name(run_id)
    candidate = target / base
    for index in range(2, 10000):
        if candidate.exists():
            marker = read_marker(candidate)
            if marker is not None and marker.get("run_id") == run_id:
                return candidate
            candidate = target / f"{base} ({index})"
            continue
        try:
            candidate.mkdir()
        except FileExistsError:
            continue  # raced into existence; re-evaluate its marker
        (candidate / MARKER_NAME).write_text(
            json.dumps({
                "run_id": run_id,
                "flow_id": flow_id,
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }),
            encoding="utf-8",
        )
        return candidate
    raise RuntimeError(f"Could not create a run folder for run #{run_id} in {target}.")


def _is_junction(path: Path) -> bool:
    probe = getattr(path, "is_junction", None)
    return bool(probe()) if callable(probe) else False


def _tombstone_gate_reason(tombstone: Path) -> str | None:
    """Why the object at the tombstone path must not be removed, or None.

    A tombstone can in principle be replaced between attempts, so the object
    is re-checked without following links before anything recursive runs.
    """
    if tombstone.is_symlink() or _is_junction(tombstone):
        return "the tombstone path is a symlink or junction"
    if not tombstone.is_dir():
        return "the tombstone path is not a folder"
    return None


def _gate_reason(target: Path, original: Path, source_run_id: int) -> str | None:
    """Why this operation's original path must not be touched, or None."""
    if original.parent != target:
        return "the recorded path is not directly inside this flow's target folder"
    if not RUN_FOLDER_RE.fullmatch(original.name):
        return "the recorded path is not named like a Metronome run folder"
    if original.is_symlink() or _is_junction(original):
        return "the recorded path is a symlink or junction"
    if not original.is_dir():
        return "the recorded path is not a folder"
    marker = read_marker(original)
    if marker is None:
        return "the folder has no Metronome ownership marker"
    if marker.get("run_id") != source_run_id:
        return f"the folder's ownership marker names run #{marker.get('run_id')}, not #{source_run_id}"
    return None


def execute_ops(target: Path, ops: list[dict]) -> list[dict]:
    """Execute the server-assigned retention operations against one target.

    Reconcile-first: each operation looks at the real state of its original
    and tombstone paths before acting, so a retry after any crash does the
    right thing. Results (one dict per operation: ``op_id``, ``outcome``,
    ``detail``) are reported back to the server, which updates the operation's
    state or releases it for a later run. Nothing here raises: a filesystem
    error is an outcome, not a run failure.
    """
    results = []
    for op in ops:
        op_id = op.get("op_id")
        source_run_id = op.get("source_run_id")
        original = Path(str(op.get("original_path") or ""))
        tombstone = Path(str(op.get("tombstone_path") or ""))

        def record(outcome: str, detail: str = ""):
            results.append({"op_id": op_id, "outcome": outcome, "detail": detail})

        if not op_id or not source_run_id or not original.name or not tombstone.name:
            record("skipped", "the operation is incomplete")
            continue
        if tombstone.parent != target or not TOMBSTONE_RE.fullmatch(tombstone.name):
            record("skipped", "the tombstone path is not a Metronome tombstone inside this target folder")
            continue
        try:
            original_present = original.is_symlink() or original.exists()
            tombstone_present = tombstone.is_symlink() or tombstone.exists()
            if original_present and tombstone_present:
                record(
                    "skipped",
                    "both the folder and its tombstone exist - the state is ambiguous, nothing was deleted",
                )
                continue
            if not original_present and tombstone_present:
                # A previous attempt crashed between rename and delete.
                reason = _tombstone_gate_reason(tombstone)
                if reason:
                    record("skipped", reason)
                    continue
                shutil.rmtree(tombstone)
                record("deleted", "reconciled a tombstone left by an earlier attempt")
                continue
            if not original_present:
                record("deleted", "an earlier attempt already removed the folder")
                continue
            reason = _gate_reason(target, original, source_run_id)
            if reason:
                record("skipped", reason)
                continue
            try:
                original.rename(tombstone)
            except OSError as exc:
                record("failed", f"the folder could not be renamed for removal: {exc}")
                continue
            try:
                shutil.rmtree(tombstone)
            except OSError as exc:
                record("quarantined", f"the folder was renamed for removal but could not be fully deleted yet: {exc}")
                continue
            record("deleted", "")
        except OSError as exc:
            record("failed", str(exc))
    return results
