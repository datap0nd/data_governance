"""Derived continuity files. SQLite remains authoritative; no schema or import changes."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import flow_layout, flow_standalone

TABLES = {'flows', 'flow_sites', 'flow_reports', 'flow_report_filters',
          'flow_recording_revisions', 'people', 'app_settings'}
_LOCK = threading.RLock()
_SECRET = {'password', 'passwd', 'cookie', 'cookies', 'authorization', 'credentials',
           'access_token', 'refresh_token', 'client_secret', 'api_key', 'token'}
DRAFT_HEADER = '# Metronome saved Flow draft v1\n'


def safe_metadata(value):
    """Metadata may describe access, but never exports authentication material."""
    if isinstance(value, dict):
        return {key: '[omitted credential]' if key.casefold() in _SECRET else safe_metadata(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [safe_metadata(item) for item in value]
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        url = urlsplit(value)
        return urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path,
                          urlencode([(k, '[omitted credential]' if k.casefold() in _SECRET else v)
                                     for k, v in parse_qsl(url.query)]), url.fragment))
    return value


def snapshot(db, flow_id):
    from app.routers import flows
    from app import flow_browser, flow_recording_timing
    flow = flows._flow_out(db, flow_id, include_private_storage=True)
    report = flows._report_out(db, flow['report_id'])
    site = dict(db.execute('SELECT * FROM flow_sites WHERE id=?', (flow['site_id'],)).fetchone())
    settings = {key: flow.get(key) for key in flows.FlowWrite.model_fields if key in flow}
    revisions = []
    for row in db.execute('''SELECT id,status,definition_json,created_at FROM flow_recording_revisions
            WHERE flow_id=? AND (id=? OR id=(SELECT MAX(id) FROM flow_recording_revisions WHERE flow_id=?))
            ORDER BY id''', (flow_id, flow.get('recording_revision_id'), flow_id)):
        revision = dict(row)
        revision['definition'] = json.loads(revision.pop('definition_json'))
        revisions.append(revision)
    return safe_metadata({
        'schema_version': 1, 'source_of_truth': 'Metronome database',
        'flow_id': flow_id, 'name': flow['name'], 'settings': settings,
        'owner': {'id': flow.get('owner_person_id'), 'name': flow.get('owner_name'), 'email': flow.get('owner_email')},
        'created_by': flow.get('created_by'), 'created_at': flow.get('created_at'),
        'schedule': {'type': flow['schedule_type'], 'time': flow['schedule_time'],
                     'days': flow['schedule_days'], 'day': flow.get('schedule_day'),
                     'enabled': flow['enabled'], 'timezone': 'Asia/Dubai'},
        'source': {key: site.get(key) for key in ('id', 'name', 'adapter', 'base_url', 'auth_url')},
        'report': {key: report.get(key) for key in ('id', 'name', 'report_url', 'automation', 'filters',
                                                    'ready_text', 'open_export_text', 'download_text')},
        'paths': {**(flows.flow_paths.policy(db, flow) or {}),
                  'flow_folder': flow['flow_folder'], 'output_folder': flow['target_folder'],
                  'flows_root': flows.flow_paths.get_flows_root(db)},
        'defaults': {'browser_channel': flow_browser.configured(db),
                     'recording_wait_seconds': flow_recording_timing.configured(db)},
        'recording': {'active_revision_id': flow.get('recording_revision_id'), 'revisions': revisions},
        'sql_reconciliation_required': bool(flow.get('sql_reconciliation_required')),
    })


def _draft(folder, flow_id, metadata, reason):
    """Keep incomplete work on disk without accidentally running an older active file."""
    target = folder / 'Scripts' / 'run_flow.py'
    flow_layout._regular(target)
    previous = flow_layout.read_manifest(folder, flow_id).get('standalone') or {}
    if target.exists() and hashlib.sha256(target.read_text(encoding='utf-8').encode()).hexdigest() != previous.get('launcher_hash'):
        raise ValueError('Scripts/run_flow.py was modified; preserve or rename it before saving again.')
    content = DRAFT_HEADER + 'import json, sys\n'
    content += 'FLOW = json.loads(' + repr(json.dumps(metadata, ensure_ascii=False)) + ')\n'
    content += 'if __name__ == "__main__":\n'
    content += '    print(' + repr('This saved Flow cannot run yet: ' + reason) + ', file=sys.stderr)\n    raise SystemExit(2)\n'
    flow_standalone._atomic_text(target, content)
    result = {'state': 'draft', 'message': reason, 'launcher': str(target),
              'launcher_hash': hashlib.sha256(content.encode()).hexdigest()}
    flow_layout.update_manifest(folder, flow_id, standalone=result)
    return result


def _write_companion(folder, name, content):
    """Preserve any existing operator notes/dependencies before refreshing a copy."""
    target = folder / 'Scripts' / name
    flow_layout._regular(target)
    if target.exists():
        previous = target.read_text(encoding='utf-8')
        if previous == content:
            return
        versions = folder / 'Scripts' / 'versions'
        flow_layout._regular(versions)
        versions.mkdir(exist_ok=True)
        digest = hashlib.sha256(previous.encode()).hexdigest()
        archived = versions / f'{target.stem}-{digest}{target.suffix}'
        flow_layout._regular(archived)
        if archived.exists():
            if archived.read_text(encoding='utf-8') != previous:
                raise ValueError('An archived Flow companion file was modified; preserve it before saving again.')
        else:
            flow_standalone._atomic_text(archived, previous)
    flow_standalone._atomic_text(target, content)


def sync_flow(db, flow_id, *, force=False):
    from app.routers import flows
    from fastapi import HTTPException
    from app import flow_portable
    with _LOCK:
        metadata = snapshot(db, flow_id)
        folder = Path(metadata['paths']['flow_folder'])
        flows.flow_paths.assert_inside(str(folder), metadata['paths']['flows_root'], label='Flow folder')
        manifest = flow_layout.read_manifest(folder, flow_id)
        flow_layout.ensure_layout(folder, flow_id)
        docs = (Path(__file__).resolve().parent.parent / 'docs' / 'flow_standalone.md').read_text(encoding='utf-8')
        # Include execution source so the next startup/save refreshes shipped code.
        digest = flow_standalone.config_hash({'metadata': metadata, 'documentation': docs, 'engine': flow_portable.execution_hash(),
                                              'catalog_engine': hashlib.sha256(str(flow_portable.execution_sources('catalog')).encode()).hexdigest()})
        previous = manifest.get('handover') or {}
        target = folder / 'Scripts' / 'run_flow.py'
        flow_layout._regular(target)
        intact = target.is_file() and hashlib.sha256(target.read_text(encoding='utf-8').encode()).hexdigest() == previous.get('launcher_hash')
        companions = all((folder / 'Scripts' / name).is_file() for name in ('README.md', 'requirements.txt'))
        if not force and intact and companions and previous.get('snapshot_hash') == digest and previous.get('state') in {'current', 'draft'}:
            return previous
        # Save the descriptive snapshot even when execution settings are incomplete.
        flow_layout.update_manifest(folder, flow_id, flow_name=metadata['name'], configuration=metadata,
                                    handover={'state': 'updating', 'snapshot_hash': digest})
        try:
            job = flows._build_job(db, flow_id, force_reprocess=True)
        except (HTTPException, ValueError, OSError) as exc:
            reason = str(getattr(exc, 'detail', exc))
            result = _draft(folder, flow_id, metadata, reason)
        else:
            job['handover'] = metadata
            result = flow_standalone.generate(job)
        _write_companion(folder, 'README.md', docs)
        # Execution dependencies only; Outlook uses the embedded PowerShell helper.
        requirements = '\n'.join(flow_portable.DEPENDENCIES) + '\n'
        _write_companion(folder, 'requirements.txt', requirements)
        result = {**result, 'snapshot_hash': digest}
        flow_layout.update_manifest(folder, flow_id, handover=result)
        return result


def synchronize(db_path, *, flow_id=None, force=False):
    """Read committed data only. A mirror failure must never undo the saved Flow."""
    results = {}
    with _LOCK:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            rows = db.execute('SELECT id,flow_folder FROM flows WHERE flow_folder IS NOT NULL' +
                              (' AND id=?' if flow_id is not None else ''), (flow_id,) if flow_id is not None else ()).fetchall()
            for row in rows:
                try:
                    results[row['id']] = sync_flow(db, row['id'], force=force)
                except Exception as exc:
                    result = {'state': 'error', 'message': str(exc)}
                    results[row['id']] = result
                    try:
                        flow_layout.update_manifest(row['flow_folder'], row['id'], handover=result)
                    except Exception:
                        pass
                    logging.getLogger(__name__).warning('Flow %s continuity files could not be updated (%s).', row['id'], type(exc).__name__)
    return results


def after_commit(db_path):
    try:
        synchronize(db_path)
    except Exception as exc:
        logging.getLogger(__name__).warning('Flow continuity synchronization unavailable (%s).', type(exc).__name__)
