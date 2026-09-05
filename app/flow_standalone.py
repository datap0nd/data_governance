"""Versioned installed-code launchers. No server calls or credential export."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
LAUNCHER_HEADER = '# Metronome managed standalone launcher v1\n'


def canonical(job: dict) -> str:
    return json.dumps(job, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def config_hash(job: dict) -> str:
    return hashlib.sha256(canonical(job).encode('utf-8')).hexdigest()


def freeze(job: dict) -> dict:
    frozen = copy.deepcopy(job)
    for key in list(frozen):
        if key.startswith('_') or key in {'resume', 'sql_retry', 'source_receipt', 'job_type'}:
            frozen.pop(key)
    # The launcher executes sequentially regardless of the server pool size.
    frozen.get('execution', {}).pop('worker_id', None)
    frozen.get('execution', {}).pop('download_parallelism', None)
    frozen.get('local_file', {})['force_reprocess'] = True
    frozen.get('outlook_source', {})['force_reprocess'] = True
    frozen.get('local_file', {}).pop('previous_identity', None)
    frozen.get('outlook_source', {}).pop('last_processed_identity', None)
    forbidden = {'password', 'passwd', 'cookie', 'cookies', 'authorization', 'credentials', 'access_token', 'refresh_token', 'client_secret', 'api_key'}
    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.casefold() in forbidden:
                    raise ValueError('The Flow configuration contains credential material and cannot be exported.')
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str) and value.startswith(('http://', 'https://')):
            from urllib.parse import urlsplit, parse_qsl
            url = urlsplit(value)
            if url.username or url.password or any(key.casefold() in forbidden for key, _ in parse_qsl(url.query)):
                raise ValueError('A Flow URL contains credential material and cannot be exported.')
    check(frozen)
    return frozen


def launcher_source(code_dir: Path, bundle_name: str) -> str:
    # repr handles quotes, Unicode and Windows paths without source injection.
    return LAUNCHER_HEADER + (
        'import sys\nfrom pathlib import Path\n'
        f'sys.path.insert(0, {str(code_dir)!r})\n'
        'from app.flow_standalone import main\n'
        f'if __name__ == "__main__":\n    raise SystemExit(main(bundle=Path(__file__).with_name({bundle_name!r})))\n'
    )


def _atomic_text(path: Path, content: str):
    from app.flow_layout import _regular
    _regular(path)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def generate(job: dict, *, code_dir: Path | None = None) -> dict:
    if job.get('flow', {}).get('execution_method') == 'recorded':
        from app.flow_portable import generate as generate_portable
        return generate_portable(job)
    from app import flow_layout, flow_paths
    flow_paths.assert_job_paths(job)
    folder = (job.get('paths') or {}).get('flow_folder')
    if not folder:
        raise ValueError('Adopt a managed folder before generating a standalone launcher.')
    flow_layout.ensure_layout(folder, job['flow']['id'])
    scripts = Path(folder) / 'Scripts'
    launcher = scripts / 'run_flow.py'
    flow_layout._regular(launcher)
    if launcher.exists() and not launcher.read_text(encoding='utf-8').startswith(LAUNCHER_HEADER):
        raise ValueError('Scripts/run_flow.py is not a managed launcher; preserve or rename it first.')
    frozen = freeze(job)
    digest = config_hash(frozen)
    name = f'flow-config-{digest}.json'
    config = scripts / name
    payload = canonical({'version': VERSION, 'config_hash': digest, 'job': frozen})
    flow_layout._regular(config)
    if config.exists():
        if config.read_text(encoding='utf-8') != payload:
            raise ValueError('The existing standalone configuration has been modified.')
    else:
        with config.open('x', encoding='utf-8') as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    content = launcher_source(code_dir or Path(__file__).resolve().parent.parent, name)
    _atomic_text(launcher, content)
    flow_layout.update_manifest(folder, job['flow']['id'], standalone={
        'version': VERSION, 'config_hash': digest, 'launcher_hash': hashlib.sha256(content.encode()).hexdigest(),
        'generated_at': datetime.now(timezone.utc).isoformat()})
    return {'state': 'current', 'launcher': str(launcher), 'config_hash': digest}


def status(job: dict) -> dict:
    from app.flow_layout import read_manifest, _regular
    folder = (job.get('paths') or {}).get('flow_folder')
    if not folder:
        return {'state': 'unmanaged'}
    try:
        stored = read_manifest(folder, job['flow']['id']).get('standalone') or {}
        path = Path(folder) / 'Scripts' / 'run_flow.py'
        _regular(path)
        if stored.get('kind') == 'portable_recorded':
            from app.flow_portable import configuration_hash
            valid = path.is_file() and hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest() == stored.get('launcher_hash')
            current = valid and stored.get('config_hash') == configuration_hash(job)
            return {'state': 'current' if current else 'modified' if not valid else 'stale', 'launcher': str(path), **stored}
        if not stored or not path.is_file():
            return {'state': 'missing'}
        content = path.read_text(encoding='utf-8')
        expected = config_hash(freeze(job))
        config = path.with_name(f"flow-config-{stored.get('config_hash')}.json")
        _regular(config)
        bundle = json.loads(config.read_text(encoding='utf-8'))
        valid = bundle.get('version') == VERSION and config_hash(bundle['job']) == stored.get('config_hash')
        current = valid and stored.get('config_hash') == expected and hashlib.sha256(content.encode()).hexdigest() == stored.get('launcher_hash')
        return {'state': 'current' if current else 'stale', 'launcher': str(path), **stored}
    except (OSError, ValueError, KeyError):
        return {'state': 'missing_or_invalid'}


def run(job: dict, *, sql: bool | None = None, headed: bool | None = None, no_transform: bool = False) -> dict:
    from contextlib import ExitStack
    from app import flow_worker, flow_paths
    from app.flow_layout import _regular
    from app.flow_execution_lock import ExecutionLocks, resource_keys
    job = copy.deepcopy(job)
    if sql is not None:
        job['sql_handoff']['enabled'] = sql
    if no_transform:
        job['transformation']['enabled'] = False
    if headed is None:
        headed = job.get('execution', {}).get('browser_mode') == 'headed'
    flow_paths.assert_job_paths(job)
    root = Path(job['paths']['flows_root'])
    profile = root / '.metronome' / 'standalone-profile'
    flow_paths.assert_inside(str(profile), str(root), label='Standalone profile')
    run_id = uuid.uuid4().int
    state = {}
    with ExecutionLocks([*resource_keys(job), 'profile:' + os.path.normcase(str(profile.resolve()))]), ExitStack() as stack:
        logs = Path(job['paths']['flow_folder']) / 'Scripts' / 'standalone-logs'
        _regular(logs)
        flow_paths.assert_inside(str(logs), job['paths']['flow_folder'], label='Standalone logs')
        logs.mkdir(exist_ok=True)
        log_path = logs / f'{run_id}.jsonl'
        log = stack.enter_context(log_path.open('x', encoding='utf-8'))
        def progress(status, detail, artifacts=None, timings=None, **extra):
            log.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'status': status,
                'progress': detail, 'artifacts': artifacts or [], 'timings': timings or [], **extra}, default=str) + '\n')
            log.flush()
        register = lambda _folder: {'ops': []}
        try:
            page = staging = None
            if job['flow'].get('source_type', 'portal') == 'portal' and job.get('job_type') != 'sql_retry':
                profile.mkdir(parents=True, exist_ok=True)
                if not stack.enter_context(flow_worker._exclusive_worker_lock(profile)):
                    raise RuntimeError('The standalone browser profile is already in use.')
                playwright = stack.enter_context(flow_worker.sync_playwright())
                staging = profile / 'downloads'; staging.mkdir(exist_ok=True)
                from app import flow_browser
                browser = flow_browser.launch(playwright, profile, flow_browser.channel_for(job),
                    headed=headed, downloads=staging)
                stack.callback(browser.close)
                page = browser.pages[0] if browser.pages else browser.new_page()
            flow_worker.execute_flow(page, job, progress, profile, staging, run_id=run_id,
                register_folder=register, headed=headed, state=state)
            return {'run_id': str(run_id), 'status': 'succeeded', 'log': str(log_path), 'artifacts': state['artifacts']}
        except Exception as exc:
            progress('failed', {'stage': 'failed', 'message': str(exc)}, state.get('artifacts'), state.get('timings'))
            raise


def main(argv=None, *, bundle: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run a frozen Flow with installed code and caller credentials; no server is contacted.')
    if bundle is None:
        parser.add_argument('bundle', type=Path)
    parser.add_argument('--dry-run', action='store_true', help='Read configuration and print a redacted summary; create nothing.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--headed', action='store_const', const=True, default=None)
    mode.add_argument('--headless', dest='headed', action='store_const', const=False)
    parser.add_argument('--no-transform', action='store_true')
    parser.add_argument('--no-sql', action='store_true', help='Explicitly skip the saved SQL handoff for this run.')
    parser.add_argument('--refresh-db', type=Path, help='Resolve current periods/config from an explicitly supplied local database, opened read-only.')
    args = parser.parse_args(argv)
    try:
        data = json.loads((bundle or args.bundle).read_text(encoding='utf-8'))
        if data.get('version') != VERSION or config_hash(data['job']) != data.get('config_hash'):
            raise ValueError('Standalone configuration version or checksum is invalid.')
        job = data['job']
        if args.dry_run:
            if args.refresh_db:
                raise ValueError('--dry-run uses the frozen bundle; omit --refresh-db.')
            print(json.dumps({'flow_id': job['flow']['id'], 'source': job['flow'].get('source_type'), 'periods': len(job['downloads'].get('periods') or []),
                'sql': bool(not args.no_sql and job['sql_handoff'].get('enabled')), 'transform': bool(not args.no_transform and job['transformation'].get('enabled')), 'config_hash': data['config_hash']}))
            return 0
        if args.refresh_db:
            import sqlite3
            from contextlib import closing
            from app.routers.flows import _build_job
            with closing(sqlite3.connect(args.refresh_db.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                job = freeze(_build_job(db, job['flow']['id'], force_reprocess=True))
        result = run(job, sql=False if args.no_sql else None, headed=args.headed, no_transform=args.no_transform)
        print(json.dumps({key: value for key, value in result.items() if key != 'artifacts'}))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'Standalone Flow failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
