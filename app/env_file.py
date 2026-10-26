"""Read Metronome's project ``.env`` settings file; standard library only.

``app.config`` applies it to the service environment at import. The desktop
launcher (``app/flow_desktop_host.py``) applies the same file to a script it
starts in the signed-in Windows session, so both read it with one set of
rules. Diagnostics carry names and line numbers, never values.
"""
from __future__ import annotations

import codecs
import os
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


def write_value(path: Path, name: str, value: str | None) -> None:
    """Set one setting to ``value``, or remove it when ``value`` is None.

    Every other line is kept. The existing file is rewritten in place, never
    replaced or created, because setup gives it a restricted access list that
    a new file would not inherit. Values are double-quoted, which ``read``
    strips, so leading or trailing spaces and quotes survive unchanged.
    """
    if not NAME_RE.fullmatch(name):
        raise ValueError("Invalid setting name.")
    if value is not None and (not value or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("A setting value must be non-empty and on one line.")
    with path.open("r+b") as handle:
        raw = handle.read()
        bom = raw.startswith(codecs.BOM_UTF8)
        original = raw[len(codecs.BOM_UTF8):].decode("utf-8") if bom else raw.decode("utf-8")
        newline = "\r\n" if "\r\n" in original else "\n"
        lines = re.split(r"\r?\n", original)
        if lines and lines[-1] == "":
            lines.pop()
        kept = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("export "):
                stripped = stripped[7:].strip()
            if "=" in stripped and stripped.split("=", 1)[0].strip() == name:
                continue
            kept.append(line)
        if value is not None:
            kept.append(f'{name}="{value}"')
        content = (newline.join(kept) + newline) if kept else ""
        handle.seek(0)
        handle.truncate()
        handle.write((codecs.BOM_UTF8 if bom else b"") + content.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
