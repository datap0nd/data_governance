"""Open only configured Flow folders, and only in a local interactive session."""
import os
from pathlib import Path

from app import flow_layout, flow_paths


def interactive_session() -> bool:
    if os.name != 'nt':
        return False
    import ctypes
    session = ctypes.c_ulong()
    return bool(ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) and session.value)


def open_folder(flow: dict, rules: dict | None, request) -> dict:
    raw = flow.get('flow_folder') or (flow.get('target_folder') if flow.get('source_type') != 'file' else None)
    if not raw:
        raise ValueError('Adopt a managed folder before opening this Local flow folder.')
    path = Path(flow_paths.clean_absolute(raw))
    flow_paths.validate_flow(flow, rules)
    flow_layout._regular(path)
    if flow.get('flow_folder'):
        flow_layout.read_manifest(path, flow['id'])
    if not path.is_dir():
        raise ValueError('The configured folder is missing. Use Repair folder layout for a managed flow.')
    local = (request.client and request.client.host in {'127.0.0.1', '::1'}
        and request.url.hostname in {'localhost', '127.0.0.1', '::1'}
        and not request.headers.get('forwarded') and not request.headers.get('x-forwarded-for'))
    if local and interactive_session():
        os.startfile(str(path))
        return {'path': str(path), 'opened': True}
    return {'path': str(path), 'opened': False, 'message': 'Copy this path and open it on the worker PC.'}
