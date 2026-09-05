"""Process-lifetime locks shared by scheduled and offline Flow execution."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def lock_root() -> Path:
    base = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) if os.name == 'nt' else Path(tempfile.gettempdir())
    return Path(os.environ.get('DG_FLOW_LOCK_ROOT') or base / 'Metronome' / 'execution-locks')


def resource_keys(job: dict) -> list[str]:
    from app.flow_publish import normalize_target_path
    keys = [f"flow:{job['flow']['id']}"]
    target = job.get('downloads', {}).get('target_folder')
    if target and not target.startswith('metronome-private://'):
        keys.append('output:' + normalize_target_path(os.path.realpath(target)))
    sql = job.get('sql_handoff') or {}
    if sql.get('enabled'):
        # Preserve PostgreSQL identifier case in the saved target identity.
        keys.append('sql:' + json.dumps([sql.get(key) for key in ('server', 'database', 'schema', 'table')]))
    return sorted(set(keys))


class ExecutionLocks:
    def __init__(self, keys: list[str], *, root: Path | None = None):
        self.root = root or lock_root()
        self.keys = sorted(set(keys))
        self.handles = []

    def acquire(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            for key in self.keys:
                path = self.root / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    raise RuntimeError('Flow lock is not a regular file.')
                handle = path.open('a+b')
                try:
                    if not path.stat().st_size:
                        handle.write(b'0'); handle.flush()
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, RuntimeError) as exc:
                    handle.close()
                    raise RuntimeError('Another Flow process is using this flow, output folder, SQL target or browser profile.') from exc
                self.handles.append(handle)
        except Exception:
            self.release()
            raise
        return self

    def release(self):
        for handle in reversed(self.handles):
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        self.handles.clear()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def job_lock(job: dict) -> ExecutionLocks:
    return ExecutionLocks(resource_keys(job))
