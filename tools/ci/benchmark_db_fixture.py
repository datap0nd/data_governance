"""Measure real empty-database initialization against immutable template copies."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path


def schema(path: Path) -> list[tuple]:
    with contextlib.closing(sqlite3.connect(path)) as connection:
        return connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 2:
        raise SystemExit("at least two iterations are required")
    with tempfile.TemporaryDirectory(prefix="metronome-fixture-benchmark-") as temporary:
        root = Path(temporary)
        os.environ["DG_DB_PATH"] = str(root / "bootstrap.db")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from app import database

        original = database.DB_PATH
        direct_times = []
        direct_paths = []
        try:
            for index in range(args.iterations):
                target = root / f"direct-{index}.db"
                database.DB_PATH = str(target)
                started = time.perf_counter()
                database.init_db()
                direct_times.append(time.perf_counter() - started)
                direct_paths.append(target)
            template = root / "template.db"
            database.DB_PATH = str(template)
            started = time.perf_counter()
            database.init_db()
            template_setup = time.perf_counter() - started
            copy_times = []
            copy_paths = []
            for index in range(args.iterations):
                target = root / f"copy-{index}.db"
                started = time.perf_counter()
                shutil.copy2(template, target)
                copy_times.append(time.perf_counter() - started)
                copy_paths.append(target)
        finally:
            database.DB_PATH = original
        expected = schema(template)
        schema_equal = all(schema(path) == expected for path in direct_paths + copy_paths)
        payload = {
            "schema_version": 1,
            "iterations": args.iterations,
            "direct_median_seconds": round(statistics.median(direct_times), 6),
            "template_setup_seconds": round(template_setup, 6),
            "copy_median_seconds": round(statistics.median(copy_times), 6),
            "schema_equal": schema_equal,
        }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if schema_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
