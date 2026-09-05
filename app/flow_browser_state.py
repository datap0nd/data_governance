"""Protected authentication state for fresh recorded browser contexts."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path


def state_path(profile, channel):
    if channel not in {'chrome', 'msedge'}:
        raise ValueError('Unsupported browser channel.')
    return Path(profile) / f'.recording-{channel}-auth.json'


def load(profile, channel):
    path = state_path(profile, channel)
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    if os.name == 'nt':
        from app.flow_credentials import _dpapi
        if record.get('format') != 'dpapi':
            raise ValueError('Recorded browser authentication must be Windows protected.')
        return json.loads(_dpapi(base64.b64decode(record['data']), False))
    if record.get('format') != 'user-private':
        raise ValueError('This browser authentication belongs to another machine.')
    return record['state']


def save(profile, channel, state):
    from app.flow_standalone import _atomic_text
    path = state_path(profile, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        from app.flow_credentials import _dpapi
        data = _dpapi(json.dumps(state).encode('utf-8'), True)
        record = {'format': 'dpapi', 'data': base64.b64encode(data).decode('ascii')}
    else:
        record = {'format': 'user-private', 'state': state}
    # Atomic writer creates a new file; restrict its creation mode on Unix.
    previous = os.umask(0o077) if os.name != 'nt' else None
    try:
        _atomic_text(path, json.dumps(record))
    finally:
        if previous is not None:
            os.umask(previous)


def protect_temporary_folder(path):
    """Codegen's public CLI needs a temporary plaintext storage-state file."""
    if os.name == 'nt':
        import csv
        identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True,
            capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        sid = next(csv.reader(identity.stdout.splitlines()))[1]
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'],
            check=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        os.chmod(path, 0o700)
