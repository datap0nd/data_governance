"""Read or explicitly persist installer capacity using only sqlite/stdlib."""
import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.flow_limits import MAX_SLOTS, DEFAULT_TOTAL_CAPACITY, DEFAULT_HEADLESS_CAPACITY, DEFAULT_HEADED_CAPACITY


def capacity(path: Path, override: int | None = None, *, mode: str = 'headless') -> int:
    if mode not in {'headless', 'headed', 'total'}:
        raise ValueError('Unsupported browser mode.')
    key = f'flows_{mode}_capacity'
    default = {'total': DEFAULT_TOTAL_CAPACITY, 'headless': DEFAULT_HEADLESS_CAPACITY, 'headed': DEFAULT_HEADED_CAPACITY}[mode]
    if override is not None and (type(override) is not int or not 1 <= override <= MAX_SLOTS):
        raise ValueError(f'Worker capacity must be between 1 and {MAX_SLOTS}.')
    if not path.exists() and override is None:
        return default
    uri = path.resolve().as_uri() + ('?mode=rwc' if override is not None else '?mode=ro')
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        if override is not None:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)')
                db.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (key, str(override)))
            return override
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone():
            return default
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        try:
            return max(1, min(MAX_SLOTS, int(row[0]))) if row else default
        except (TypeError, ValueError):
            return default


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    parser.add_argument('--capacity', type=int, choices=range(1, MAX_SLOTS + 1))
    parser.add_argument('--mode', choices=['headless', 'headed', 'total'], default='headless')
    args = parser.parse_args()
    print(capacity(args.database, args.capacity, mode=args.mode))
