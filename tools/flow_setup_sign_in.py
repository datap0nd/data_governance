"""Print how many worker browsers of each kind setup.ps1 signs in.

    python tools/flow_setup_sign_in.py

The number is the Settings value "Worker browsers signed in during setup"
(System > Settings > Flows), read from the database named by DG_DB_PATH. Setup
must never stop because this could not be read, so any failure prints the
default and exits 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The installer uses an isolated embedded Python and invokes this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.flow_limits import DEFAULT_SETUP_SIGN_IN_BROWSERS, MAX_SLOTS


def browsers() -> int:
    try:
        from app import flow_capacity

        value = flow_capacity.setup_sign_in_browsers()
    except Exception:
        return DEFAULT_SETUP_SIGN_IN_BROWSERS
    return value if 1 <= value <= MAX_SLOTS else DEFAULT_SETUP_SIGN_IN_BROWSERS


if __name__ == "__main__":
    print(browsers())
