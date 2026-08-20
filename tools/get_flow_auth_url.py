"""Print the enabled portal sign-in targets from a Metronome SQLite DB.

One line per site, ``adapter<TAB>url``, so setup.ps1 can bootstrap each portal's
SSO session in turn. ASAP and GSCM are separate websites behind the same
Samsung SSO, and each needs its own visible sign-in against the automation
browser profile.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DISCOVERY_ADAPTERS = ("asap_portal", "gscm_portal")


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: get_flow_auth_url.py DATABASE [ADAPTER]")
    database = Path(sys.argv[1])
    if not database.is_file():
        return
    adapters = (sys.argv[2],) if len(sys.argv) == 3 else DISCOVERY_ADAPTERS
    placeholders = ", ".join("?" * len(adapters))
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""SELECT adapter, COALESCE(auth_url, base_url) FROM flow_sites
                WHERE enabled=1 AND adapter IN ({placeholders})
                  AND COALESCE(auth_url, base_url) IS NOT NULL
                ORDER BY adapter, id""",
            adapters,
        ).fetchall()
    seen = set()
    for adapter, url in rows:
        if not url or adapter in seen:
            continue
        seen.add(adapter)
        print(f"{adapter}\t{url}")


if __name__ == "__main__":
    main()
