"""Deterministic recorded-flow execution, shared by workers and portable files."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import flow_recording


def _value(value):
    if isinstance(value, dict) and set(value) == {'regex'}:
        return re.compile(value['regex'])
    if isinstance(value, list):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    return value


def locate(pages, target):
    node = pages[target['page']]
    for part in target.get('locator', []):
        method = part['method']
        if method not in flow_recording.LOCATORS | {'first', 'last', 'content_frame'}:
            raise ValueError('Unsupported locator method.')
        node = getattr(node, method) if method in {'first', 'last', 'content_frame'} else getattr(node, method)(
            *[_value(value) for value in part.get('args', [])], **_value(part.get('kwargs', {})))
    return node


def acquire(page, job, progress, profile_dir, staging, *, target, run_id, artifacts):
    from playwright.sync_api import expect
    from app import flow_worker
    definition = flow_recording.validate_definition(job['recording']['definition'])
    pages = {'page': page}
    parameters = job.get('recording_parameters') or flow_recording.resolve_parameters(definition)
    actual_parameters = dict(parameters)
    defaults = {}
    prior_defaults = (job.get('resume') or {}).get('recording_defaults', {})
    output_count = sum(step['action'] == 'download' for step in flow_recording.walk_steps(definition['steps']))
    output_index = 0
    captured = []
    staging.mkdir(parents=True, exist_ok=True)

    outcomes = {}

    def notify(step, message, **extra):
        outcomes[step['id']] = {'outcome': extra.get('outcome'), 'message': message,
                                'failure_reason': extra.get('failure_reason')}
        progress('running', {'stage': 'recorded_action', 'message': message,
                 'step_id': step['id'], 'revision': job['recording']['revision'],
                 'action': step['action'], 'attempt': 1, 'expected_outcome': 'action completed',
                 'step_outcomes': copy.deepcopy(outcomes),
                 **extra}, artifacts)

    def read_defaults(*, final=False):
        for name, parameter in definition.get('parameters', {}).items():
            if parameter['mode'] != 'portal_default' or name in defaults:
                continue
            if not parameter.get('target'):
                continue
            if parameter['target']['page'] not in pages:
                if final:
                    raise RuntimeError(f'Date parameter {name} could not be read from its recorded page.')
                continue
            value = locate(pages, parameter['target']).input_value(timeout=30_000)
            datetime.strptime(value, parameter.get('format', '%Y-%m-%d'))
            defaults[name] = actual_parameters[name] = value
        for name, expected in prior_defaults.items():
            if (name in defaults or final) and defaults.get(name) != expected:
                raise RuntimeError('Portal defaults changed since the original run. Start a new run.')
        dates = {}
        for name, parameter in definition.get('parameters', {}).items():
            if actual_parameters.get(name) is not None:
                dates[name] = datetime.strptime(actual_parameters[name], parameter.get('format', '%Y-%m-%d'))
        for name, parameter in definition.get('parameters', {}).items():
            end = parameter.get('not_after')
            if end and name in dates and end in dates and dates[name] > dates[end]:
                raise RuntimeError(f'Date parameter {name} must not be after {end}.')

    active_step = None

    def execute(steps):
        nonlocal output_index, active_step
        for step in steps:
            active_step = step
            action = step['action']
            notify(step, f"{step['id']}: {action}", outcome='started')
            if action == 'wait':
                deadline = time.monotonic() + step['seconds']
                heartbeat = 0
                while time.monotonic() < deadline:
                    if time.monotonic() >= heartbeat:
                        notify(step, 'Waiting between actions.', outcome='running')
                        heartbeat = time.monotonic() + 1
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                notify(step, 'Wait completed.', outcome='completed')
                continue
            if action == 'new_page':
                # Reuse the worker's initial page; additional pages are explicit.
                pages[step['page']] = page if len(pages) == 1 and step['page'] == 'page' else page.context.new_page()
                notify(step, 'Page opened.', outcome='completed')
                continue
            node = locate(pages, step)
            if action in {'fill', 'press_sequentially'} and node.get_attribute('type') == 'password':
                raise RuntimeError('Authentication values cannot be replayed or exported in a recorded Flow.')
            args, kwargs = copy.deepcopy(step.get('args', [])), _value(step.get('kwargs', {}))
            kwargs.setdefault('timeout', 120_000)
            if step.get('expected_text'):
                expect(node).to_have_text(step['expected_text'])
            skip = False
            for name, parameter in definition.get('parameters', {}).items():
                if parameter.get('step_id') != step['id']:
                    continue
                value = parameters.get(name)
                if value is None:
                    current = node.input_value(timeout=30_000)
                    datetime.strptime(current, parameter.get('format', '%Y-%m-%d'))
                    defaults[name] = actual_parameters[name] = current
                    skip = True
                elif action in {'fill', 'press_sequentially', 'select_option'}:
                    args = [value]
                else:
                    raise ValueError('Date parameters must reference value-setting actions.')
            if skip:
                notify(step, 'Portal default retained.', outcome='completed')
                continue
            if action == 'download':
                read_defaults()
                # Optional input locators may be attached to fixed/calculated
                # parameters absent from the original recording.
                for name, parameter in definition.get('parameters', {}).items():
                    if parameter.get('target') and not parameter.get('step_id') and parameters.get(name) is not None:
                        raise ValueError('Fixed/calculated parameters require a recorded step before report generation.')
                files_before = flow_worker._download_staging_snapshot(staging)
                with pages[step['page']].expect_download(timeout=1_800_000) as pending:
                    execute(step['steps'])
                    active_step = step
                download = pending.value
                output_index += 1
                suffix = Path(download.suggested_filename).suffix or '.download'
                staged = staging / f'{uuid.uuid4().hex}{suffix}'
                if step['output'].get('completion') == 'staging':
                    completed = flow_worker._asap_dashboard_event_staged_download(staging, files_before, step['id'])
                else:
                    completed = flow_worker._completed_edge_download(download, step['id'])
                flow_worker._copy_with_checksum(completed, staged)
                captured.append((step, staged, output_index))
                notify(step, 'Download completed.', outcome='completed')
                continue
            if action == 'popup':
                with pages[step['page']].expect_popup(timeout=120_000) as pending:
                    execute(step['steps'])
                    active_step = step
                pages[step['result_page']] = pending.value
                notify(step, 'Popup opened.', outcome='completed')
                continue
            if action == 'assert':
                getattr(expect(node), step['assertion'])(*[_value(item) for item in args], **kwargs)
            elif action == 'close':
                node.close()
                pages.pop(step['page'], None)
            elif action == 'goto':
                node.goto(*args, **{**kwargs, 'wait_until': 'domcontentloaded'})
            elif action in flow_recording.ACTIONS:
                getattr(node, action)(*[_value(item) for item in args], **kwargs)
            else:
                raise ValueError(f'Unsupported action {action}.')
            notify(step, 'Action completed.', outcome='completed')
    try:
        execute(definition['steps'])
    except Exception as exc:
        if active_step:
            notify(active_step, str(exc), outcome='failed', failure_reason='recorded_action_failed')
        raise
    if len(captured) != output_count:
        raise RuntimeError('The recording did not produce its complete expected output bundle.')
    read_defaults(final=True)
    # All files are captured before publication, so a later failed interaction
    # cannot leave a partially published direct-output bundle.
    for step, staged, index in captured:
        try:
            notify(step, 'Validating downloaded output.', outcome='running')
            specification = step['output']
            fmt = specification['format']
            filename = flow_worker._render_filename(job['downloads']['filename_template'], job, None, index, step.get('label') or step['id'])
            filename = str(Path(filename).with_suffix({'xlsx': '.xlsx', 'csv': '.csv', 'html': '.html', 'txt': '.txt'}[fmt]))
            output = flow_worker._safe_output_path(target, filename)
            if any(Path(item['file_path']).resolve() == output.resolve() for item in artifacts if item.get('file_path')):
                raise RuntimeError('Recorded outputs have duplicate filenames. Include {index} in the filename template.')
            downstream = job.get('transformation', {}).get('enabled') or job.get('sql_handoff', {}).get('enabled')
            if downstream and fmt in {'html', 'txt'}:
                raise ValueError('HTML/text downloads cannot be transformed or loaded into SQL.')
            needs_table = bool(downstream or specification.get('min_rows') or specification.get('headers')
                or specification.get('period_checks') or job['downloads'].get('excel_trim', 'none') != 'none')
            metadata = flow_worker._store_completed_download(staged, output,
                file_format=fmt, asap_download_type={'html': 'html', 'txt': 'plain_text', 'csv': 'csv_file_format', 'xlsx': 'excel_plain_text'}[fmt],
                require_normalized_csv=fmt in {'csv', 'xlsx'} and needs_table, recorded_output=True,
                allow_raw_xlsx_fallback=False, excel_trim=job['downloads'].get('excel_trim', 'none'), csv_preamble='none')
            minimum_rows = specification.get('min_rows', 0)
            if minimum_rows:
                csv_path = metadata.get('normalized_file_path') or metadata.get('file_path')
                if not csv_path or Path(csv_path).suffix.lower() != '.csv':
                    raise RuntimeError('Data row checks require a normalized CSV output.')
                with open(csv_path, encoding='utf-8-sig', newline='') as stream:
                    rows = csv.reader(stream)
                    next(rows, None)  # The first row contains column names.
                    row_count = sum(any(cell.strip() for cell in row) for row in rows)
                metadata['row_count'] = row_count
                if row_count < minimum_rows:
                    raise RuntimeError(f"{step['id']}: downloaded data has {row_count} rows; at least {minimum_rows} required.")
            if specification.get('headers'):
                csv_path = metadata.get('normalized_file_path') or metadata.get('file_path')
                if not csv_path or Path(csv_path).suffix.lower() != '.csv':
                    raise RuntimeError('Schema validation requires a normalized CSV output.')
                with open(csv_path, encoding='utf-8-sig', newline='') as stream:
                    header = next(csv.reader(stream), [])
                if header != specification['headers']:
                    raise RuntimeError(f"{step['id']}: output columns do not match the expected report schema.")
            for check in specification.get('period_checks', []):
                csv_path = metadata.get('normalized_file_path') or metadata['file_path']
                expected = actual_parameters.get(check['parameter'])
                if expected is None:
                    raise RuntimeError('The report period cannot be verified without a resolved parameter.')
                with open(csv_path, encoding='utf-8-sig', newline='') as stream:
                    rows = csv.DictReader(stream)
                    if check['column'] not in (rows.fieldnames or []):
                        raise RuntimeError('The expected period column is absent from the report.')
                    if any(row[check['column']] != expected for row in rows):
                        raise RuntimeError('The downloaded report period does not match this run.')
            artifact = {**metadata, 'bundle_index': index, 'bundle_count': output_count,
                        'export_view': step['id'], 'period_key': None, 'status': 'saved',
                        'export_transport': 'recorded_browser', 'recording_revision': job['recording']['revision'],
                        'recording_parameters': actual_parameters, 'recording_defaults': defaults}
            artifacts.append(flow_worker._decorate_artifact_storage(artifact, job, profile_dir))
            notify(step, 'Downloaded output validated.', outcome='completed')
        except Exception as exc:
            notify(step, str(exc), outcome='failed', failure_reason='output_validation_failed')
            raise
    return artifacts


def execute_recorded_flow(page, job, progress, profile_dir, download_staging_dir=None, *,
                          run_id, register_folder, headed=False, artifacts=None, state=None,
                          run_started=None, **unused):
    from app import flow_worker, flow_paths, flow_sql
    job = copy.deepcopy(job)
    artifacts = artifacts if artifacts is not None else []
    state = state if state is not None else {}
    started = run_started or time.perf_counter()
    timings = [{'phase': 'total', 'status': 'running', 'duration_ms': 0}]
    state.update(artifacts=artifacts, timings=timings, sql_started=None, transformation_started=None)
    # Never skip session setup based on a previous attempt. Re-execution uses
    # the frozen original parameters and validates portal defaults again.
    if job.get('resume'):
        completed = job['resume'].get('completed') or []
        prior = [item.get('recording_defaults') for item in completed if item.get('recording_defaults') is not None]
        if prior and any(item != prior[0] for item in prior):
            raise RuntimeError('Recovery outputs have inconsistent portal defaults; start a new run.')
        if prior:
            job['resume']['recording_defaults'] = prior[0]
    flow_paths.assert_job_paths(job)
    target = flow_worker._prepare_run_folder(job, profile_dir, run_id=run_id,
        register_folder=register_folder, report_progress=progress)
    try:
        definition = flow_recording.validate_definition(job['recording']['definition'])
        acquire(page, job, progress, profile_dir, download_staging_dir or profile_dir / 'downloads',
                target=target, run_id=run_id, artifacts=artifacts)
        artifacts = flow_worker._publish_direct_artifacts(job, artifacts, run_id=run_id, report_progress=progress)
        state['artifacts'] = artifacts
        sql_artifacts = artifacts
        if job.get('transformation', {}).get('enabled'):
            state['transformation_started'] = time.perf_counter()
            transform = copy.deepcopy(job['transformation'])
            source = job['recording'].get('transformation_source')
            if source is None:
                raise ValueError('Recorded flows require a frozen Python transformation.')
            with tempfile.TemporaryDirectory(prefix='metronome-transform-') as temporary:
                script = Path(temporary) / 'transform.py'
                script.write_text(source, encoding='utf-8')
                transform['script_path'] = str(script)
                sql_artifacts = flow_worker._run_transformations(artifacts, transform)
            artifacts.extend(sql_artifacts)
        if job.get('sql_handoff', {}).get('enabled'):
            state['sql_started'] = time.perf_counter()
            progress('running', {'stage': 'sql_insertion', 'message': 'Loading recorded-flow outputs into SQL.'}, artifacts, timings)
            state['sql_result'] = flow_sql.load_artifacts(sql_artifacts, job['sql_handoff'],
                progress=lambda detail: progress('running', detail, artifacts, timings))
        timings[0].update(status='succeeded', duration_ms=round((time.perf_counter() - started) * 1000))
        progress('succeeded', {'stage': 'complete', 'message': f'Completed recorded flow with {len(artifacts)} artifacts.',
                             'recording_revision': job['recording']['revision']}, artifacts, timings)
    except Exception:
        timings[0].update(status='failed', duration_ms=round((time.perf_counter() - started) * 1000))
        raise
    return state


def standalone_main(job, argv=None):
    from app import flow_worker
    from app.flow_execution_lock import ExecutionLocks, resource_keys
    parser = argparse.ArgumentParser(description='Run this portable recorded Flow with Python libraries and caller credentials.')
    parser.add_argument('--dry-run', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--headed', dest='headed', action='store_true', default=None)
    modes.add_argument('--headless', dest='headed', action='store_false')
    parser.add_argument('--no-transform', action='store_true')
    parser.add_argument('--no-sql', action='store_true')
    parser.add_argument('--parameter', action='append', default=[], metavar='NAME=VALUE')
    parser.add_argument('--profile-dir', type=Path)
    parser.add_argument('--output-root', type=Path, help='Use a dedicated root for this portable Flow on this machine.')
    args = parser.parse_args(argv)
    job = copy.deepcopy(job)
    try:
        flow_recording.validate_definition(job['recording']['definition'])
        overrides = dict(item.split('=', 1) for item in args.parameter)
        job['recording_parameters'] = flow_recording.resolve_parameters(job['recording']['definition'], overrides)
        if args.no_transform:
            job['transformation']['enabled'] = False
        if args.no_sql:
            job['sql_handoff']['enabled'] = False
        if args.dry_run:
            print(json.dumps({'flow': job['flow']['name'], 'revision': job['recording']['revision'],
                'parameters': job['recording_parameters'], 'sql': job['sql_handoff']['enabled'],
                'transformation': job['transformation']['enabled']}))
            return 0
        if args.output_root:
            from app import flow_layout, flow_paths
            root = Path(flow_paths.clean_absolute(str(args.output_root.resolve())))
            if root == Path(root.anchor):
                raise ValueError('Choose a dedicated output root, not the filesystem root.')
            source = root / flow_paths.source_folder_name(job['site']['adapter'])
            flow_paths.assert_inside(str(source), str(root))
            source.mkdir(parents=True, exist_ok=True)
            folder = source / flow_layout.flow_folder_slug(job['flow']['name'], job['flow']['id'])
            flow_paths.assert_inside(str(folder), str(root))
            owner = flow_recording.digest({'flow': job['flow']['id'], 'source': job['site']['adapter'],
                'original_folder': job['paths']['flow_folder']})
            if folder.exists():
                if flow_layout.read_manifest(folder, job['flow']['id']).get('portable_owner') != owner:
                    raise ValueError('Output folder belongs to another Flow. Choose a new output root.')
            else:
                folder.mkdir()
                flow_layout.write_manifest(folder, {'schema': 'metronome-flow-folder', 'layout_version': 1,
                    'flow_id': job['flow']['id'], 'flow_name': job['flow']['name'],
                    'source_adapter': job['site']['adapter'], 'portable_owner': owner})
            flow_layout.ensure_layout(folder, job['flow']['id'])
            job['paths'] = {'version': 1, 'enforced': False, 'flows_root': str(args.output_root.resolve()),
                'source_folder': flow_paths.source_folder_name(job['site']['adapter']), 'flow_folder': str(folder)}
            job['downloads']['target_folder'] = str(folder / 'Downloads')
            job['transformation']['script_path'] = str(folder / 'Scripts' / 'embedded-transform.py')
        profile = args.profile_dir or Path(job['paths']['flows_root']) / '.metronome' / 'standalone-profile'
        headed = args.headed if args.headed is not None else job['execution']['browser_mode'] == 'headed'
        run_id = uuid.uuid4().int
        logs = Path(job['paths']['flow_folder']) / 'Scripts' / 'standalone-logs'
        with ExecutionLocks([*resource_keys(job), 'profile:' + os.path.normcase(str(profile.resolve()))]):
            logs.mkdir(parents=True, exist_ok=True)
            journal = logs / 'sql-outcome.json'
            if job['sql_handoff']['enabled'] and journal.exists():
                raise RuntimeError('A previous standalone SQL outcome requires reconciliation; inspect sql-outcome.json before rerunning.')
            with (logs / f'{run_id}.jsonl').open('x', encoding='utf-8') as log:
                def progress(status, detail, artifacts=None, timings=None, **extra):
                    if detail.get('stage') == 'sql_insertion':
                        with journal.open('x', encoding='utf-8') as marker:
                            json.dump({'run_id': str(run_id), 'target': job['sql_handoff'], 'outcome': 'unknown'}, marker)
                            marker.flush()
                            os.fsync(marker.fileno())
                    log.write(json.dumps({'status': status, 'progress': detail, 'artifacts': artifacts or [], 'timings': timings or []}, default=str) + '\n')
                    log.flush()
                with flow_worker._exclusive_worker_lock(profile) as owned:
                    if not owned:
                        raise RuntimeError('The browser profile is already in use.')
                    with flow_worker.sync_playwright() as playwright:
                        from app import flow_browser
                        from app.flow_recorder_worker import authenticate, browser_session
                        with browser_session(playwright, profile, flow_browser.channel_for(job), headed=headed,
                                timezone=job['recording']['definition']['timezone']) as (context, _profile):
                            page = context.new_page()
                            authenticate(page, job, profile, progress, headed=headed)
                            execute_recorded_flow(page, job, progress, profile, run_id=run_id,
                                register_folder=lambda folder: {'ops': []}, headed=headed)
                            if job['sql_handoff']['enabled']:
                                journal.unlink(missing_ok=True)
        return 0
    except Exception as exc:
        print(f'Recorded Flow failed: {exc}', file=sys.stderr)
        return 1
