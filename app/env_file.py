"""Read Metronome's project ``.env`` settings file; standard library only.

``app.config`` applies it to the service environment at import. The desktop
launcher (``app/flow_desktop_host.py``) applies the same file to a script it
starts in the signed-in Windows session, so both read it with one set of
rules. Diagnostics carry names and line numbers, never values.
"""
from __future__ import annotations

import re
from pathlib import Path

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def read(path: Path | None) -> tuple[dict[str, str], dict]:
    """The non-empty settings in ``path`` (a later line wins) and a value-free status."""
    status = {"path": str(path) if path else "", "exists": False, "loaded_names": [],
              "ignored_lines": [], "error": None}
    values: dict[str, str] = {}
    if path is None:
        return values, status
    try:
        status["exists"] = path.exists()
        if not status["exists"]:
            return values, status
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].strip()
            if "=" not in stripped:
                status["ignored_lines"].append(line_number)
                continue
            name, value = stripped.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not NAME_RE.fullmatch(name):
                status["ignored_lines"].append(line_number)
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                values[name] = value
                status["loaded_names"].append(name)
    except OSError as exc:
        values = {}
        status["error"] = f"{type(exc).__name__} while reading settings file"
    except UnicodeError as exc:
        values = {}
        status["error"] = f"{type(exc).__name__} while decoding settings file"
    return values, status
