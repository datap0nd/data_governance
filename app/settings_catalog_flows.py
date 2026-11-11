"""Flow run, worker, SQL step and delivery settings.

Imported by ``app.settings_catalog``. A value a worker process uses is copied
into each job when the job is queued (``settings_registry.snapshot``), because
workers never read Metronome's database; such a setting is declared with
``applies="new_runs"``.
"""
from __future__ import annotations

from app.settings_registry import Setting, register, subscribe  # noqa: F401
