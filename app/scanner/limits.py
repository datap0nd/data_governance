"""Operator limits from the Settings page, read at the moment they are used.

The scanner, freshness and update code each keep their built-in limit as a
module constant and pass it here. The constant is what applies until the
setting is given another value, on the Settings page or through the
environment variable it replaced, so the code and the page cannot drift apart
and nothing changes for an install that never opens the page.

Pass the plain built-in constant, never one already read from an environment
variable: the setting falls back to that variable itself, and a value saved on
the page has to win over it.

A read costs one small database query. Call these once per operation (one
scan, one probe run, one request), never once per report, source or row.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def value(key: str, built_in: Any) -> Any:
    """The setting ``key`` right now; ``built_in`` while it is left at its default."""
    return values({key: built_in})[key]


def values(built_ins: Mapping[str, Any]) -> dict[str, Any]:
    """Several settings read together: ``{key: built_in}`` in, ``{key: value}`` out."""
    try:
        from app import settings_registry

        current = settings_registry.snapshot(built_ins)
        return {
            key: built_in if current[key] == settings_registry.definition(key).default else current[key]
            for key, built_in in built_ins.items()
        }
    except Exception:
        # Work in progress must not stop because a setting could not be read.
        logger.exception("Could not read settings %s; using the built-in values", ", ".join(built_ins))
        return dict(built_ins)
