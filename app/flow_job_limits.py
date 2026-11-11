"""Operator limits copied into a job when the server queues it.

A worker never reads Metronome's database, so the limits it obeys travel with
the work. The web service reads them from Settings once, when it builds a run
or queues a catalog scan, and stores them under ``job["limits"]`` in the units
the worker uses. From then on the job is the only source: a change on the
Settings page reaches new work, never a run that is already queued, in
progress, or retried from its stored job.

A job without the key (queued before the key existed, built by an older
server, or embedded in a generated script) uses the worker's built-in values,
exactly as before. So does a job whose limits equal the built-in ones: a
function is handed a limit only when the job changes it (``given``), so with
nothing changed in Settings every call is the one it always was.
"""
from __future__ import annotations

import copy
import math

KEY = "limits"

# name in the job -> (setting, how many of the job's unit one setting unit is).
# A scale of None copies the value as it is (an on/off switch).
RUN = {
    "download_start_seconds": ("flows.download_start_minutes", 60),
    "download_event_seconds": ("flows.download_handover_minutes", 60),
    "export_attempts": ("flows.export_attempts", 1),
    "gscm_export_attempts": ("flows.gscm_export_attempts", 1),
    "recorded_action_seconds": ("flows.recorded_action_seconds", 1),
    "network_replay": ("flows.network_replay", None),
    "replay_max_age_days": ("flows.replay_max_age_days", 1),
    "replay_request_seconds": ("flows.replay_request_minutes", 60),
    "script_timeout_seconds": ("flows.script_default_minutes", 60),
    "sql_connect_seconds": ("flows.sql_connect_seconds", 1),
    "sql_lock_seconds": ("flows.sql_lock_minutes", 60),
    "sql_statement_seconds": ("flows.sql_statement_minutes", 60),
    "view_lock_seconds": ("flows.view_lock_minutes", 60),
    "view_refresh_seconds": ("flows.view_refresh_minutes", 60),
    "failure_screenshots_kept": ("flows.failure_screenshots_kept", 1),
    "recording_test_folders_kept": ("flows.recording_test_folders_kept", 1),
}
# A catalog scan opens no export and writes no SQL; it carries only what it uses.
SCAN = {
    "scan_budget_minutes": ("flows.catalog_scan_minutes", 1),
    "scan_overlay_seconds": ("flows.scan_overlay_minutes", 60),
    "failure_screenshots_kept": ("flows.failure_screenshots_kept", 1),
}
SWITCHES = frozenset({"network_replay"})


def value(job, name: str, default=None):
    """The limit frozen into ``job``, or ``default`` when the job carries none.

    Anything that is not a usable value (a number above zero, or on/off for a
    switch) counts as absent, so a damaged job falls back to the built-in limit
    instead of running without one.
    """
    limits = job.get(KEY) if isinstance(job, dict) else None
    found = limits.get(name) if isinstance(limits, dict) else None
    if name in SWITCHES:
        return found if isinstance(found, bool) else default
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return default
    if not math.isfinite(found) or found <= 0:
        return default
    return found


def given(job, **arguments) -> dict:
    """Keyword arguments for the limits ``job`` changes: ``argument=(name, built_in)``.

    A limit the job does not carry, or carries with the built-in value,
    contributes nothing. The function is then called exactly as it always was
    and applies its own default.
    """
    found = {}
    for argument, (name, built_in) in arguments.items():
        limit = value(job, name)
        if limit is not None and limit != built_in:
            found[argument] = limit
    return found


def carry(job: dict, source_job) -> None:
    """Give a job that continues an earlier run that run's limits.

    A resume is built from the Flow as it is now, but it is the same run
    carrying on: like a retry made from the stored job, it keeps the limits
    frozen when the original was queued, or the built-in ones when the
    original carried none.
    """
    limits = source_job.get(KEY) if isinstance(source_job, dict) else None
    if isinstance(limits, dict):
        job[KEY] = copy.deepcopy(limits)
    else:
        job.pop(KEY, None)


def frozen(table: dict = RUN) -> dict:
    """The Settings values for ``table`` right now, in the worker's units.

    For the web service only, at the moment it builds a run or queues a scan.
    """
    from app import settings_registry

    saved = settings_registry.snapshot([setting for setting, _scale in table.values()])
    return {
        name: saved[setting] if scale is None else saved[setting] * scale
        for name, (setting, scale) in table.items()
    }
