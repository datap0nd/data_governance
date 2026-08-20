"""Create and migrate a Metronome database without starting the app.

setup.ps1 reads the site list to decide which portals need a one-time SSO
sign-in. Those rows are created by the schema migrations, which normally run
when the service starts - after setup has already read the database. The first
setup that ships a new portal therefore saw a table that did not contain it yet
and silently skipped its sign-in. Running the migrations first closes that gap.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The embedded Windows runtime runs isolated and can ignore PYTHONPATH, so the
# package root is bootstrapped here exactly as the flow worker does.
_CODE_DIR = Path(__file__).resolve().parent.parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_migrations.py DATABASE")
    os.environ["DG_DB_PATH"] = str(Path(sys.argv[1]).resolve())
    from app.database import init_db

    init_db()


if __name__ == "__main__":
    main()
