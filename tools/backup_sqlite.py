"""Create an atomic, WAL-aware SQLite backup.

This helper intentionally depends only on the Python standard library so the
Windows updater can use it before changing application code or dependencies.
The destination is replaced only after SQLite's online backup API completes
and the copied database passes ``PRAGMA quick_check``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def backup_sqlite(source: Path, destination: Path) -> dict[str, object]:
    """Back up *source* to *destination* and return a small receipt payload."""

    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {source}")
    if source == destination:
        raise ValueError("SQLite source and destination must be different files")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)

    try:
        # The SQLite backup API reads a transactionally consistent snapshot and
        # includes committed pages that still live in the source WAL file.
        with closing(sqlite3.connect(str(source), timeout=30)) as source_db:
            source_db.execute("PRAGMA busy_timeout = 30000")
            with closing(sqlite3.connect(str(temp_path), timeout=30)) as backup_db:
                source_db.backup(backup_db, pages=256, sleep=0.05)
                backup_db.commit()
                quick_check = backup_db.execute("PRAGMA quick_check").fetchone()
                if not quick_check or str(quick_check[0]).casefold() != "ok":
                    detail = quick_check[0] if quick_check else "no result"
                    raise sqlite3.DatabaseError(
                        f"SQLite backup integrity check failed: {detail}"
                    )

        # Flush the completed temporary database before the atomic rename.
        # Windows requires a writable descriptor for FlushFileBuffers, which
        # is what ``os.fsync`` delegates to.
        with temp_path.open("rb+") as backup_file:
            os.fsync(backup_file.fileno())
        os.replace(temp_path, destination)
        return {
            "source": str(source),
            "destination": str(destination),
            "bytes": destination.stat().st_size,
            "quick_check": "ok",
        }
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = backup_sqlite(args.source, args.destination)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"status": "succeeded", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
