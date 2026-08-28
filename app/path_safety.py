"""Non-blocking path classification shared by operational preflights."""

from __future__ import annotations

import os
import re
from typing import Any


def is_remote_file_path(value: Any) -> bool:
    """Identify UNC and mapped-network paths without opening the target."""
    raw = str(value or "").strip()
    if raw.startswith(("\\\\", "//")):
        return True
    match = re.match(r"^[A-Za-z]:[\\/]", raw)
    if os.name != "nt" or not match:
        return False
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(raw[:3])) == 4
    except (AttributeError, OSError, TypeError, ValueError):
        return False
