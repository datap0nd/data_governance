"""Read or explicitly persist installer capacity using only sqlite/stdlib."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def capacity(path: Path, override: int | None = None, *, mode: str = 'headless') -> int:
    if mode not in {'headless', 'headed'}:
        raise ValueError('Unsupported browser mode.')
    key = f'flows_{mode}_capacity'
    if override is not None and not 1 <= override <= 5:
        raise ValueError('Worker capacity must be between 1 and 5.')
    if not path.exists() and override is None:
        return 1
    uri = path.resolve().as_uri() + ('?mode=rwc' if override is not None else '?mode=ro')
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        if override is not None:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)')
                db.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (key, str(override)))
            return override
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone():
            return 1
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        try:
            return max(1, min(5, int(row[0]))) if row else 1
        except (TypeError, ValueError):
            return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    parser.add_argument('--capacity', type=int, choices=range(1, 6))
    parser.add_argument('--mode', choices=['headless', 'headed'], default='headless')
    args = parser.parse_args()
    print(capacity(args.database, args.capacity, mode=args.mode))
