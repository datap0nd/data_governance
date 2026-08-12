"""Print the first enabled ASAP authentication URL from a Metronome SQLite DB."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: get_flow_auth_url.py DATABASE")
    database = Path(sys.argv[1])
    if not database.is_file():
        return
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """SELECT COALESCE(auth_url, base_url) FROM flow_sites
               WHERE enabled=1 AND adapter='asap_portal'
                 AND COALESCE(auth_url, base_url) IS NOT NULL
               ORDER BY id LIMIT 1"""
        ).fetchone()
    if row and row[0]:
        print(row[0])


if __name__ == "__main__":
    main()
