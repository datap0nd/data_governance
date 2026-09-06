"""Bounded recording diagnostics containing controls and outcomes, never inputs."""
from __future__ import annotations

import json
import math
import re

MAX_TEXT = 200_000
MAX_EVENTS = 1000
MAX_STEPS = 500
STATE_KEYS = {'expanded', 'selected', 'pressed', 'checked', 'visible', 'enabled', 'active', 'open', 'tab', 'scope'}


def _steps(definition):
    pending = list((definition or {}).get('steps') or [])
    result = []
    while pending and len(result) < MAX_STEPS:
        step = pending.pop(0)
        if not isinstance(step, dict):
            continue
        result.append(step)
        pending[:0] = step.get('steps') or []
    return result


def _entered_values(definition):
    values = []
    for step in _steps(definition):
        if step.get('action') not in {'fill', 'press_sequentially', 'select_option'}:
            continue
        pending = list(step.get('args') or [])
        while pending and len(values) < 1000:
            value = pending.pop()
            if isinstance(value, str) and value:
                values.append(value)
            elif isinstance(value, list):
                pending.extend(value[:100])
            elif isinstance(value, dict):
                pending.extend(value.values())
    for parameter in ((definition or {}).get('parameters') or {}).values():
        if isinstance(parameter, dict) and isinstance(parameter.get('value'), str) and parameter['value']:
            values.append(parameter['value'])
    return sorted(set(values), key=len, reverse=True)


def safe_text(value, definition=None, *, limit=600):
    if not isinstance(value, (str, int, float, bool)):
        return ''
    text = str(value)[:20_000]
    # Remove URL authority/path/query together, including encoded credential
    # values. Diagnostic control names do not need private portal addresses.
    text = re.sub(r'(?i)\b(?:https?|wss?|ftp|postgresql(?:\+\w+)?)://[^\s<>"\']+', '[URL]', text)
    text = re.sub(r'(?i)\b(?:https?|wss?)%3a%2f%2f[^\s<>"\']+', '[URL]', text)
    text = re.sub(r'(?i)\bauthorization\s*[:=]\s*(?:bearer|basic)\s+[^\s,;}]+', 'authorization=[redacted]', text)
    text = re.sub(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]', text)
    text = re.sub(r'(?im)\b(?:cookie|set-cookie)\s*:\s*[^\r\n]+', 'cookie: [redacted]', text)
    text = re.sub(r'''(?ix)(["']?(?:password|passwd|pwd|token|secret|authorization|cookie|set-cookie|api[_ -]?key|access_token|refresh_token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1[redacted]', text)
    text = re.sub(r'''(?i)(?:[A-Z]:[\\/]|\\\\)[^\r\n<>"']+''', '[local path]', text)
    text = re.sub(r'''(?<![\w:])/(?!/)[A-Za-z0-9_.-]+(?:/[^\s<>"']*)?''', '[local path]', text)
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '[email]', text)
    for entered in _entered_values(definition):
        if len(entered) >= 3:
            text = text.replace(entered, '[entered value]')
        else:
            text = re.sub(r'(?<!\w)' + re.escape(entered) + r'(?!\w)', '[entered value]', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text[:limit]


def safe_error(value, definition=None):
    # Playwright call logs can include entered values and page fragments.
    # The structured target/outcome below carries the actionable context.
    first = str(value or '').split('Call log:', 1)[0].splitlines()
    first = first[0] if first else ''
    if re.match(r'^(?:Locator|Page|Frame)\.', first):
        timeout = re.search(r'(?i)timeout\s+(\d+)\s*ms', first)
        if timeout:
            return f'Browser action timed out after {timeout[1]} ms.'
        if 'strict mode violation' in first.casefold():
            return 'The recorded target matched more than one element.'
        return 'The recorded browser action failed.'
    if re.search(r'<[A-Za-z!/][^>]*>', first):
        return 'Browser error details containing page content are excluded.'
    return safe_text(first, definition)


def action_label(step):
    action = step.get('action', 'action')
    if action in {'download', 'popup'}:
        for child in _steps({'steps': step.get('steps') or []}):
            if child.get('action') not in {'download', 'popup', 'wait', 'new_page', 'close'}:
                return action_label(child)
    if step.get('label'):
        return safe_text(step['label'], limit=160)
    name = ''
    for part in reversed(step.get('locator') or []):
        if not isinstance(part, dict):
            continue
        kwargs, args = part.get('kwargs') or {}, part.get('args') or []
        if isinstance(kwargs.get('name'), str):
            name = kwargs['name']
        elif part.get('method') in {'get_by_text', 'get_by_label', 'get_by_title', 'get_by_placeholder', 'get_by_alt_text', 'get_by_test_id'} and args and isinstance(args[0], str):
            name = args[0]
        if name:
            break
    verbs = {'click': 'Click', 'dblclick': 'Double-click', 'fill': 'Fill', 'press_sequentially': 'Type in',
             'select_option': 'Select in', 'check': 'Check', 'uncheck': 'Uncheck', 'set_checked': 'Set',
             'hover': 'Hover over', 'clear': 'Clear', 'press': 'Press key in', 'assert': 'Check',
             'goto': 'Open page', 'new_page': 'Open page', 'close': 'Close page', 'wait': 'Wait',
             'download': 'Download', 'popup': 'Open popup'}
    return safe_text(verbs.get(action, 'Action') + (' ' + name if name else ''), limit=160)


def _scalar(value, definition):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and abs(value) <= 1_000_000_000 and math.isfinite(value):
        return value
    if isinstance(value, str):
        return safe_text(value, definition, limit=400)
    return None


def _fields(value, keys, definition):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in keys:
        if key in value:
            clean = _scalar(value[key], definition)
            if clean is not None:
                result[key] = clean
    return result


def _locator(value, definition):
    if isinstance(value, str):
        return safe_text(value, definition, limit=1200)
    if not isinstance(value, list):
        return []
    parts = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        part = _fields(item, {'method'}, definition)
        part['args'] = [clean for arg in (item.get('args') or [])[:5] if (clean := _scalar(arg, definition)) is not None]
        part['kwargs'] = _fields(item.get('kwargs'), {'name', 'exact', 'has_text'}, definition)
        parts.append(part)
    return parts


def sanitize_diagnostic(detail, definition=None):
    if not isinstance(detail, dict):
        return {}
    result = {'version': 1, **_fields(detail, {'phase', 'action_label', 'duration_ms', 'error_type',
        'failure_reason', 'prior_step_id', 'prior_action_label'}, definition)}
    target = detail.get('target')
    if isinstance(target, dict):
        result['target'] = _fields(target, {'page', 'frame', 'method', 'element_id', 'owner_id',
            'match_count', 'visible_count', 'enabled_count'}, definition)
        for key in ('recorded_locator', 'replay_locator', 'frame_locator'):
            if key in target:
                result['target'][key] = _locator(target[key], definition)
        if isinstance(target.get('candidates'), list):
            result['target']['candidates'] = [_fields(item, {'role', 'name', 'tag', 'visible', 'enabled', 'selected', 'checked'}, definition)
                for item in target['candidates'][:8] if isinstance(item, dict)]
    if isinstance(detail.get('timing'), dict):
        result['timing'] = _fields(detail['timing'], {'default_seconds', 'override_seconds', 'explicit_wait_seconds',
            'effective_seconds', 'waited_seconds', 'timeout_ms', 'event_timeout_ms'}, definition)
    if isinstance(detail.get('click'), dict):
        click = detail['click']
        result['click'] = _fields(click, {'method', 'dispatched', 'completed', 'attempt', 'confirmation',
            'transition', 'native_available'}, definition)
        for key in ('before', 'after'):
            if isinstance(click.get(key), dict):
                result['click'][key] = _fields(click[key], STATE_KEYS, definition)
    if isinstance(detail.get('environment'), dict):
        result['environment'] = _fields(detail['environment'], {'browser_channel', 'browser_version', 'playwright_version',
            'python_version', 'worker_version', 'engine_hash', 'revision', 'run_id', 'recording_wait_seconds'}, definition)
    return result


def _loads(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _merge_diagnostic(previous, current):
    result = {**previous, **current}
    for key in ('target', 'timing', 'click'):
        if key in previous or key in current:
            result[key] = {**previous.get(key, {}), **current.get(key, {})}
    if previous.get('click', {}).get('before'):
        result['click']['before'] = previous['click']['before']
    return result


def render_debug(db, flow_id, scan_id):
    from fastapi import HTTPException
    row = db.execute('''SELECT c.*,s.flow_id,s.revision_id,s.operation FROM flow_recording_sessions s
        JOIN flow_catalog_scans c ON c.id=s.scan_id WHERE s.flow_id=? AND s.scan_id=?''', (flow_id, scan_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Recording session not found.')
    row = dict(row)
    frozen = _loads(row['job_json'])
    job = frozen.get('validation_job') or {}
    definition = (job.get('recording') or {}).get('definition') or {}
    steps = _steps(definition)
    indexed = {step.get('id'): step for step in steps}
    events = db.execute('''SELECT id,status,stage,message,details_json,created_at FROM flow_scan_events
        WHERE scan_id=? ORDER BY id DESC LIMIT ?''', (scan_id, MAX_EVENTS + 1)).fetchall()
    truncated = len(events) > MAX_EVENTS
    events = list(reversed(events[:MAX_EVENTS]))
    lines = ['Metronome recording debug log', f'Test: {scan_id} | Flow: {flow_id} | Revision: {row["revision_id"] or "not recorded"}',
        f'Status: {row["status"]} | Operation: {row["operation"]}',
        f'Created: {safe_text(row.get("created_at"))} | Finished: {safe_text(row.get("finished_at")) or "not finished"}',
        'Private URLs, local paths, entered values and browser snapshots are excluded.', '']
    progress = _loads(row.get('progress_json'))
    outcomes, diagnostics = {}, {}
    environment_event = db.execute('''SELECT details_json FROM flow_scan_events
        WHERE scan_id=? AND stage='test_environment' ORDER BY id LIMIT 1''', (scan_id,)).fetchone()
    if environment_event:
        environment = sanitize_diagnostic(_loads(environment_event['details_json']).get('diagnostic'), definition).get('environment')
        if environment:
            lines.append('Environment: ' + json.dumps(environment, ensure_ascii=False, sort_keys=True))
    for event in events:
        details = _loads(event['details_json'])
        if isinstance(details.get('step_outcomes'), dict):
            outcomes.update(details['step_outcomes'])
        diagnostic = sanitize_diagnostic(details.get('diagnostic'), definition)
        if diagnostic.get('environment') and not environment_event:
            lines.append('Environment: ' + json.dumps(diagnostic['environment'], ensure_ascii=False, sort_keys=True))
        if details.get('step_id'):
            step_id = details['step_id']
            diagnostics[step_id] = _merge_diagnostic(diagnostics.get(step_id, {}), diagnostic)
    if isinstance(progress.get('step_outcomes'), dict):
        outcomes.update(progress['step_outcomes'])
    if row.get('error'):
        lines.extend(['Failure: ' + safe_error(row['error'], definition), ''])
    failed_ids = [step.get('id') for step in steps if isinstance(outcomes.get(step.get('id')), dict)
        and outcomes[step.get('id')].get('outcome') == 'failed']
    focused = set(failed_ids)
    for failed_id in failed_ids:
        previous = diagnostics.get(failed_id, {}).get('prior_step_id')
        if previous in indexed:
            focused.add(previous)
        else:
            for step in reversed(steps[:steps.index(indexed[failed_id])]):
                if isinstance(outcomes.get(step.get('id')), dict) and outcomes[step.get('id')].get('outcome') == 'completed' and step.get('action') not in {'wait', 'assert', 'download', 'popup', 'new_page', 'close'}:
                    focused.add(step.get('id'))
                    break
    if focused:
        lines.append('Failure context')
        for step in steps:
            if step.get('id') in focused:
                lines.append(safe_text(action_label(step), definition, limit=160))
                diagnostic = diagnostics.get(step.get('id'))
                if diagnostic:
                    lines.append(json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))
        lines.append('')
    lines.append('Recorded steps')
    for index, step in enumerate(steps, 1):
        outcome = outcomes.get(step.get('id')) or {}
        if not isinstance(outcome, dict):
            outcome = {}
        status = outcome.get('outcome', 'not reached')
        label = safe_text(action_label(step), definition, limit=160)
        confirmation = outcome.get('confirmation')
        if status == 'completed' and step.get('action') in {'click', 'dblclick'}:
            status = 'confirmed' if confirmation == 'confirmed' else 'click sent'
        lines.append(f'{index}. {label} — {safe_text(status)}')
        if outcome.get('message') and (status == 'failed' or confirmation):
            lines.append('   ' + safe_error(outcome['message'], definition))
    if not steps:
        lines.append('This session has no frozen recording steps. Phase events are shown below.')
    lines.extend(['', 'Event timeline'])
    timeline = []
    if truncated:
        timeline.append(f'Earlier events omitted; showing the latest {MAX_EVENTS}.')
    for event in events:
        details = _loads(event['details_json'])
        diagnostic = sanitize_diagnostic(details.get('diagnostic'), definition)
        if diagnostic.get('phase') == 'wait':
            continue  # Effective waits remain in the final per-action timing.
        step = indexed.get(details.get('step_id'))
        label = safe_text(action_label(step), definition, limit=160) if step else safe_text(event['stage'] or event['status'])
        message = safe_error(event['message'], definition)
        outcome = safe_text(details.get('outcome') or event['status'])
        timeline.append(f'{safe_text(event["created_at"], limit=40)} | {label} | {outcome}' + (f' | {message}' if message else ''))
        if diagnostic.get('target') or diagnostic.get('click') or diagnostic.get('phase') in {'action_failed', 'output_failed', 'event_listener'}:
            timeline.append('   ' + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))
    if not events:
        timeline.append('No detailed events were recorded for this session.')
        if progress.get('message'):
            timeline.append(safe_error(progress['message'], definition))
    prefix = ('\n'.join(lines) + '\n').encode('utf-8')[:MAX_TEXT - 2000]
    remaining = MAX_TEXT - len(prefix) - 80
    tail = ('\n'.join(timeline) + '\n').encode('utf-8')
    if len(tail) > remaining:
        tail = b'[Earlier timeline text omitted to keep this log bounded.]\n' + tail[-remaining:]
    return prefix.decode('utf-8', errors='ignore') + tail.decode('utf-8', errors='ignore')
