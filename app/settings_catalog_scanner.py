"""Scanner, freshness and update settings read by the web service.

Imported by ``app.settings_catalog``. Declare a setting here only together
with the code that reads it through ``settings_registry.get``.
"""
from __future__ import annotations

from app.settings_registry import Setting, register, subscribe  # noqa: F401
