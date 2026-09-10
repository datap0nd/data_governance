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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import flow_recording
from app import flow_recording_diagnostics as diagnostics
from app import flow_recording_pacing


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


def observe_target(node, step):
    """Best-effort structural evidence; never collect values or page contents."""
    result = {'page': step['page'], 'recorded_locator': step.get('locator', []),
              'frame_locator': [part for part in step.get('locator', [])
                                if part['method'] in {'frame_locator', 'content_frame'}]}
    if not step.get('locator'):
        return result
    try:
        count = node.count()
        result['match_count'] = count
        candidates = node.evaluate_all('''nodes => nodes.slice(0, 8).map(el => {
            const doc = el.ownerDocument, view = doc.defaultView;
            const box = el.getBoundingClientRect(), style = view.getComputedStyle(el);
            let enabled = true;
            for (let n = el; n; n = n.parentElement) {
                if (n.disabled || n.getAttribute('aria-disabled') === 'true') enabled = false;
            }
            const hit = doc.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
            return {element_id: el.id, tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
                class_name: String(el.className || '').slice(0,400), connected: el.isConnected,
                visible: box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none',
                enabled, disabled: Boolean(el.disabled), aria_disabled: el.getAttribute('aria-disabled'),
                aria_selected: el.getAttribute('aria-selected'), aria_pressed: el.getAttribute('aria-pressed'),
                aria_expanded: el.getAttribute('aria-expanded'), userstatus: el.getAttribute('userstatus'),
                display: style.display, visibility: style.visibility, pointer_events: style.pointerEvents,
                z_index: style.zIndex, x: box.x, y: box.y, width: box.width, height: box.height,
                hit_is_target: Boolean(hit && (hit === el || el.contains(hit))),
                hit_id: hit?.id || '', hit_tag: hit?.tagName.toLowerCase() || '',
                hit_class: String(hit?.className || '').slice(0,400)};
        })''')
        result.update(candidates=candidates, sampled_count=len(candidates), omitted_candidates=max(0, count - len(candidates)),
                      visible_count=sum(item['visible'] for item in candidates), enabled_count=sum(item['enabled'] for item in candidates))
        if candidates:
            result['element_id'] = candidates[0]['element_id'] if count == 1 else ''
        if count == 1:
            context = node.evaluate('el => ({state:el.ownerDocument.readyState, name:el.ownerDocument.defaultView.name, url:el.ownerDocument.URL})', timeout=1000)
            result.update(document_state=context['state'], frame_name=context['name'],
                          frame_url_hash=hashlib.sha256(context['url'].encode()).hexdigest())
    except Exception as exc:
        result['probe_error'] = type(exc).__name__  # Probe failures never replace dispatch failures.
    return result


_WEEK_WITH_YEAR = re.compile(r'(?<!\d)(20\d{2})\s*[-/ ]?\s*[Ww]?\s*(0?[1-9]|[1-4]\d|5[0-3])(?!\d)')
_WEEK_ONLY = re.compile(r'(?<!\w)[Ww]\s*(0?[1-9]|[1-4]\d|5[0-3])(?!\d)')
_ISO_DATE = re.compile(r'(?<!\d)(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])(?!\d)')
_RANGE_REPAINT_TIMEOUT_SECONDS = 10


def _range_week(label: str, container_years: set[int]) -> tuple[int, int] | None:
    """Resolve a range cell's accessible text to one unambiguous ISO week."""
    text = ' '.join(str(label or '').split())
    matches = {(int(year), int(week)) for year, week in _WEEK_WITH_YEAR.findall(text)}
    for year, month, day in _ISO_DATE.findall(text):
        try:
            iso = date(int(year), int(month), int(day)).isocalendar()
        except ValueError:
            continue
        matches.add((iso.year, iso.week))
    if not matches:
        week_only = {int(value) for value in _WEEK_ONLY.findall(text)}
        if len(week_only) == 1 and len(container_years) == 1:
            matches.add((next(iter(container_years)), next(iter(week_only))))
        elif week_only and len(container_years) != 1:
            raise RuntimeError(
                f'Week range cell {text[:120]!r} has no unambiguous nearby year heading.'
            )
    valid = set()
    for year, week in matches:
        try:
            date.fromisocalendar(year, week, 1)
        except ValueError:
            continue
        valid.add((year, week))
    if len(valid) > 1:
        raise RuntimeError(f'Week range cell contains more than one date identity: {text[:120]!r}.')
    return next(iter(valid), None)


def _week_name(value: tuple[int, int]) -> str:
    return f'{value[0]:04d}-W{value[1]:02d}'


def _week_span(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    first, last = date.fromisocalendar(*start, 1), date.fromisocalendar(*end, 1)
    if first > last:
        raise RuntimeError('The newest selectable week is before the range start.')
    result = []
    while first <= last:
        iso = first.isocalendar()
        result.append((iso.year, iso.week))
        first += timedelta(days=7)
    return result


def _range_cell_snapshots(container, selector: str, selected_state: str = 'auto') -> tuple[object, list[dict], set[int]]:
    cells = container.locator(selector)
    snapshots = cells.evaluate_all('''nodes => nodes.map((el, index) => {
        const style = el.ownerDocument.defaultView.getComputedStyle(el);
        const box = el.getBoundingClientRect();
        const control = el.matches('input[type=checkbox],input[type=radio]') ? el
            : el.querySelector('input[type=checkbox],input[type=radio]');
        const attributes = ['aria-checked', 'aria-selected', 'aria-pressed'];
        let signal = null, selected = null;
        if (control && typeof control.checked === 'boolean') {
            signal = 'checked'; selected = Boolean(control.checked);
        } else {
            for (const name of attributes) {
                const value = el.getAttribute(name);
                if (value !== null) { signal = name; selected = value === 'true'; break; }
            }
        }
        let enabled = !el.disabled && el.getAttribute('aria-disabled') !== 'true';
        for (let parent = el.parentElement; parent; parent = parent.parentElement) {
            if (parent.disabled || parent.getAttribute('aria-disabled') === 'true') enabled = false;
        }
        return {index, label: [el.getAttribute('aria-label'), el.getAttribute('title'),
                el.innerText, el.textContent, el.value].filter(Boolean).join(' '),
            signal, selected, classes: [...el.classList], enabled, visible: box.width > 0 && box.height > 0
                && style.visibility !== 'hidden' && style.display !== 'none'};
    })''')
    container_text = container.evaluate("el => [el.getAttribute('aria-label'), el.innerText, el.textContent].filter(Boolean).join(' ')")
    years = {int(value) for value in re.findall(r'(?<!\d)20\d{2}(?!\d)', str(container_text or ''))}
    visible = [item for item in snapshots if item.get('visible')]
    if selected_state.startswith('class:'):
        token = selected_state.split(':', 1)[1]
        for item in visible:
            item['signal'] = selected_state
            item['selected'] = token in item.pop('classes', [])
    return cells, visible, years


def _range_control(container, selector: str, label: str):
    control = container.locator(selector)
    count = control.count()
    if count != 1:
        raise RuntimeError(f'The range box must expose exactly one {label} control; found {count}.')
    return control


def _range_control_enabled(control) -> bool:
    return not control.is_disabled() and control.get_attribute('aria-disabled') != 'true'


def _range_page_fingerprint(container, selector: str, selected_state: str) -> tuple:
    _cells, snapshots, years = _range_cell_snapshots(container, selector, selected_state)
    return tuple(sorted((_range_week(item.get('label', ''), years), item.get('enabled')) for item in snapshots))


def _range_click_and_wait_for_page(container, control, selector: str, selected_state: str, direction: str) -> None:
    before = _range_page_fingerprint(container, selector, selected_state)
    control.click(timeout=30_000)
    deadline = time.monotonic() + _RANGE_REPAINT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        after = _range_page_fingerprint(container, selector, selected_state)
        if after and after != before:
            return
        time.sleep(.1)
    raise RuntimeError(f'The range {direction} control did not reveal a different set of weeks.')


def _range_click_week(container, selector: str, selected_state: str, week: tuple[int, int]) -> None:
    """Click one logical week without letting a virtual list retarget an nth locator."""
    deadline = time.monotonic() + _RANGE_REPAINT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        cells, snapshots, years = _range_cell_snapshots(container, selector, selected_state)
        matches = [item for item in snapshots
                   if _range_week(item.get('label', ''), years) == week]
        if len(matches) != 1:
            time.sleep(.1)
            continue
        item = matches[0]
        if not item.get('enabled'):
            raise RuntimeError(f'{_week_name(week)} became disabled before it could be selected.')
        try:
            # A Playwright action scrolls its target first. Virtual calendars can
            # repaint on that scroll and silently retarget locator.nth(). This
            # atomic identity check plus DOM click acts only on the visible,
            # enabled element that produced this snapshot; state is still proven
            # separately below.
            cells.nth(item['index']).evaluate('''(el, expected) => {
                const label = [el.getAttribute('aria-label'), el.getAttribute('title'),
                    el.innerText, el.textContent, el.value].filter(Boolean).join(' ');
                if (label !== expected) throw new Error('range cell repainted');
                el.click();
            }''', item['label'])
        except Exception:
            time.sleep(.1)
            continue
        while time.monotonic() < deadline:
            _cells, refreshed, refreshed_years = _range_cell_snapshots(container, selector, selected_state)
            refreshed_matches = [candidate for candidate in refreshed
                                 if _range_week(candidate.get('label', ''), refreshed_years) == week]
            if len(refreshed_matches) == 1 and bool(refreshed_matches[0].get('selected')):
                return
            time.sleep(.1)
    raise RuntimeError(f'The range box did not confirm {_week_name(week)} selection after repainting.')


def _select_week_range(container, step, update) -> dict:
    """Select an exact, expanding ISO-week range inside one recorded box."""
    contract = step['range']
    selector = contract['cell_selector']
    required_signal = contract.get('selected_state', 'auto')
    start_match = re.fullmatch(r'(20\d{2})-W(\d{2})', contract['start'])
    start = (int(start_match.group(1)), int(start_match.group(2)))
    navigation = contract.get('navigation', {'kind': 'scroll'}).get('kind', 'scroll')
    container.wait_for(state='visible', timeout=120_000)
    if navigation == 'scroll':
        container.evaluate('el => { el.scrollTop = 0; }')
    elif navigation == 'controls':
        previous_selector = contract['navigation']['previous_selector']
        for _movement in range(100):
            previous = _range_control(container, previous_selector, 'previous')
            if not _range_control_enabled(previous):
                break
            _range_click_and_wait_for_page(container, previous, selector, required_signal, 'previous')
        else:
            raise RuntimeError('The range previous control did not reach a disabled boundary.')

    select_all_selector = contract.get('select_all_selector')
    if select_all_selector:
        select_all = _range_control(container, select_all_selector, 'Select all')
        state = select_all.get_attribute('aria-checked')
        if state not in {'true', 'false'}:
            raise RuntimeError('The range Select all control does not expose aria-checked state.')
        if state == 'false' and _range_control_enabled(select_all):
            select_all.click(timeout=30_000)

    seen: dict[tuple[int, int], dict] = {}
    selected_signals: set[str] = set()
    viewport_fingerprints: set[tuple] = set()
    movements = 0
    clicks = 0
    for _iteration in range(200):
        cells, snapshots, years = _range_cell_snapshots(container, selector, required_signal)
        current: dict[tuple[int, int], dict] = {}
        for item in snapshots:
            week = _range_week(item.get('label', ''), years)
            if week is None:
                continue
            if week in current:
                raise RuntimeError(f'The range box exposes {_week_name(week)} more than once.')
            if item.get('selected') is None or not item.get('signal'):
                raise RuntimeError(
                    f'The range box does not expose a readable selected state for {_week_name(week)}.'
                )
            if required_signal != 'auto' and item['signal'] != required_signal:
                raise RuntimeError(
                    f'{_week_name(week)} exposes {item["signal"]}, not the validated '
                    f'{required_signal} selected-state signal.'
                )
            selected_signals.add(item['signal'])
            current[week] = item
            seen[week] = {key: item[key] for key in ('enabled', 'selected', 'signal')}

        fingerprint = tuple(sorted((week, item['enabled'], item['selected']) for week, item in current.items()))
        position = container.evaluate('el => ({top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight})')
        viewport = (fingerprint, round(float(position['top'])))
        if viewport in viewport_fingerprints:
            break
        viewport_fingerprints.add(viewport)

        for week, item in sorted(current.items()):
            should_select = week >= start and item['enabled']
            if not should_select or bool(item['selected']):
                continue
            update(
                f'Selecting {_week_name(week)} in the range box.',
                {'range_week': _week_name(week), 'range_operation': 'select'},
            )
            _range_click_week(container, selector, required_signal, week)
            seen[week]['selected'] = True
            clicks += 1

        if navigation == 'controls':
            next_control = _range_control(container, contract['navigation']['next_selector'], 'next')
            if not _range_control_enabled(next_control):
                break
            _range_click_and_wait_for_page(container, next_control, selector, required_signal, 'next')
            movements += 1
            update('Scanning the next calendar page in the range box.', {'range_movement': movements})
            continue
        if navigation != 'scroll' or position['height'] <= position['client']:
            break
        moved = container.evaluate('''el => {
            const before = el.scrollTop;
            el.scrollTop = Math.min(el.scrollHeight, before + Math.max(1, el.clientHeight * .8));
            return {before, after:el.scrollTop};
        }''')
        if moved['after'] == moved['before']:
            break
        movements += 1
        update('Scanning the next weeks inside the range box.', {'range_movement': movements})

    enabled = sorted(week for week, item in seen.items() if week >= start and item['enabled'])
    if not enabled:
        raise RuntimeError(f'The range box did not expose {_week_name(start)} or a later selectable week.')
    latest = enabled[-1]
    expected = _week_span(start, latest)
    missing = [week for week in expected if week not in seen or not seen[week]['enabled']]
    if missing:
        raise RuntimeError(
            'The range box is missing selectable weeks: '
            + ', '.join(_week_name(week) for week in missing[:12])
            + ('…' if len(missing) > 12 else '')
        )
    not_selected = [week for week in expected if not seen[week].get('selected')]
    outside = [week for week, item in seen.items()
               if item.get('selected') and (week < start or week > latest)]
    if not_selected or outside:
        detail = []
        if not_selected:
            detail.append('not selected: ' + ', '.join(_week_name(value) for value in not_selected[:12]))
        if outside:
            detail.append('outside range: ' + ', '.join(_week_name(value) for value in outside[:12]))
        raise RuntimeError('The range box could not verify the exact week selection; ' + '; '.join(detail) + '.')
    return {
        'start_week': _week_name(start), 'end_week': _week_name(latest),
        'selected_weeks': len(expected), 'selection_clicks': clicks,
        'navigation_movements': movements, 'selected_state_signals': sorted(selected_signals),
    }


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
    pacing = flow_recording_pacing.Pacing(job)
    previous_step = None
    step_started = {}

    def notify(step, message, **extra):
        outcomes[step['id']] = {'outcome': extra.get('outcome'), 'message': message,
                                'failure_reason': extra.get('failure_reason'),
                                'confirmation': extra.get('confirmation'),
                                **{key: extra[key] for key in ('remaining_seconds', 'effective_wait_seconds') if key in extra}}
        detail = {'version': 1, 'action_label': diagnostics.action_label(step),
                  'call': diagnostics.execution_contract(step),
                  'phase': extra.get('outcome', 'running'),
                  'duration_ms': round((time.monotonic() - step_started.get(step['id'], time.monotonic())) * 1000),
                  **extra.pop('diagnostic', {})}
        if previous_step:
            detail.update(prior_step_id=previous_step['id'], prior_action_label=diagnostics.action_label(previous_step))
        extra['diagnostic'] = diagnostics.sanitize_diagnostic(detail, definition)
        progress('running', {'stage': 'recorded_action', 'message': message,
                 'step_id': step['id'], 'revision': job['recording']['revision'],
                 'action': step['action'], 'attempt': 1,
                 'step_outcomes': copy.deepcopy(outcomes),
                 **extra}, artifacts)

    def buffer(step, timing):
        seconds = timing['waited_seconds']
        if not seconds:
            return
        label = diagnostics.action_label(step)
        flow_recording_pacing.wait(seconds, lambda remaining: notify(step,
            f'Waiting {remaining} seconds before {label}.' if remaining else f'{label}: wait finished.',
            outcome='running', remaining_seconds=remaining,
            effective_wait_seconds=timing['effective_seconds'],
            diagnostic={'phase': 'wait', 'timing': timing}))

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
        nonlocal output_index, active_step, previous_step
        for step in steps:
            active_step = step
            action = step['action']
            step_started[step['id']] = time.monotonic()
            label = diagnostics.action_label(step)
            notify(step, f'{label}: starting.', outcome='started')
            if action == 'wait':
                flow_recording_pacing.wait(step['seconds'], lambda remaining: notify(step,
                    f'Waiting {remaining} seconds.', outcome='running', remaining_seconds=remaining,
                    diagnostic={'phase': 'wait', 'timing': {'explicit_wait_seconds': step['seconds']}}))
                pacing.credit += step['seconds']
                notify(step, 'Wait completed.', outcome='completed')
                continue
            if action == 'new_page':
                # Reuse the worker's initial page; additional pages are explicit.
                pages[step['page']] = page if len(pages) == 1 and step['page'] == 'page' else page.context.new_page()
                notify(step, 'Page opened.', outcome='completed')
                continue
            retain_default = any(parameter.get('step_id') == step['id'] and parameters.get(name) is None
                                 for name, parameter in definition.get('parameters', {}).items())
            timing = {}
            if action in flow_recording.ACTIONS and not retain_default:
                timing = pacing.interaction(step)
                # Buffer before any target-dependent read, not just dispatch:
                # an input may itself be created during the requested pause.
                buffer(step, timing)
            if action == 'click' and definition.get('adapter', job.get('site', {}).get('adapter')) == 'gscm_portal':
                from app.flow_recording_gscm_bookmark import is_target, select
                if is_target(step):
                    phases = {'gscm_bookmark_resolved': 'bookmark identity resolved; checking the rendered Favorite rows.',
                              'gscm_bookmark_to_top': 'moving the Favorite list to its top with its own scrollbar.',
                              'gscm_bookmark_sweep': 'sweeping the Favorite list downward with its own scrollbar.'}
                    # The progress channel raises on cancellation, which stops
                    # the helper before its next scrollbar movement.
                    result = select(pages[step['page']], step, lambda detail: notify(step,
                        f"{label}: {phases.get(detail.get('phase'), 'selecting exact GSCM Favorite bookmark.')}",
                        outcome='running', diagnostic=detail))
                    notify(step, 'GSCM Favorite bookmark selected.', outcome='completed', confirmation='exact_identity',
                           diagnostic={'phase': 'action_finished', 'timing': timing, 'bookmark': result})
                    previous_step = step
                    continue
            if action == 'select_range':
                container = locate(pages, step)
                result = _select_week_range(
                    container, step,
                    lambda message, detail: notify(
                        step, message, outcome='running',
                        diagnostic={'phase': 'range_selection', **detail},
                    ),
                )
                notify(
                    step,
                    f"Selected {result['start_week']} through {result['end_week']} "
                    f"({result['selected_weeks']} weeks).",
                    outcome='completed', confirmation='exact_range',
                    diagnostic={'phase': 'action_finished', 'range': result},
                )
                previous_step = step
                continue
            node = locate(pages, step)
            if timing:
                notify(step, f'{label}: sending action.', outcome='running', diagnostic={
                    'phase': 'action_target', 'target': observe_target(node, step), 'timing': timing})
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
                event_timeout = 1_800_000 + pacing.event_budget_ms(step['steps'])
                notify(step, 'Waiting for download.', outcome='running', diagnostic={
                    'phase': 'event_listener', 'timing': {'event_timeout_ms': event_timeout}})
                with pages[step['page']].expect_download(timeout=event_timeout) as pending:
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
                event_timeout = 120_000 + pacing.event_budget_ms(step['steps'])
                notify(step, 'Waiting for popup.', outcome='running', diagnostic={
                    'phase': 'event_listener', 'timing': {'event_timeout_ms': event_timeout}})
                with pages[step['page']].expect_popup(timeout=event_timeout) as pending:
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
                if action == 'click' and definition.get('adapter', job.get('site', {}).get('adapter')) == 'gscm_portal':
                    from app.flow_recording_clicks import click_recorded
                    result = click_recorded(node, step, args, kwargs, lambda detail: notify(step,
                        f'{label}: sending click.', outcome='running', diagnostic=detail))
                    if result is not None:
                        notify(step, result['message'], outcome='completed', confirmation=result['confirmation'],
                               diagnostic={'phase': 'action_finished', 'timing': timing,
                                           'target': observe_target(node, step), 'click': result['click']})
                        previous_step = step
                        continue
                getattr(node, action)(*[_value(item) for item in args], **kwargs)
            else:
                raise ValueError(f'Unsupported action {action}.')
            is_click = action in {'click', 'dblclick'}
            notify(step, 'Click sent.' if is_click else 'Action completed.', outcome='completed',
                   confirmation='not_requested' if is_click else None,
                   diagnostic={'phase': 'action_finished', 'timing': timing,
                               'click': {'method': 'playwright', 'dispatched': True, 'completed': True,
                                         'confirmation': 'not_requested', 'verification': 'none', 'retry_policy': 'never'} if is_click else {}})
            if action in flow_recording.ACTIONS:
                previous_step = step
    try:
        execute(definition['steps'])
    except Exception as exc:
        if active_step:
            try:
                failed_target = observe_target(locate(pages, active_step), active_step)
            except Exception as probe_exc:
                failed_target = {'probe_error': type(probe_exc).__name__}
            try:
                notify(active_step, diagnostics.safe_error(exc, definition), outcome='failed', failure_reason='recorded_action_failed',
                       diagnostic={'phase': 'action_failed', 'error_type': type(exc).__name__,
                                   'exception': diagnostics.exception_detail(exc), 'target': failed_target,
                                   'failure_reason': 'recorded_action_failed', **getattr(exc, 'diagnostic', {})})
            except Exception:
                pass  # Preserve the original failure, including cancellation.
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
                # The recording proves what the browser clicked, not which
                # semantic ASAP Export Wizard option produced the response.
                # Let the shared post-download pipeline follow the bytes just
                # as it did for scan-selected flows instead of inventing an
                # Excel assertion from an ``.xlsx`` output label.
                file_format=fmt,
                require_normalized_csv=fmt in {'csv', 'xlsx'} and needs_table, recorded_output=True,
                allow_raw_xlsx_fallback=False, excel_trim=job['downloads'].get('excel_trim', 'none'),
                # Recorded ASAP downloads still use ASAP's report/filter
                # preamble even though navigation came from a recording.
                # Reuse the scan-selected normalization contract so only the
                # final rectangular data section reaches SQL.
                csv_preamble=('asap' if job.get('site', {}).get('adapter') == 'asap_portal' else 'none'))
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
            try:
                notify(step, diagnostics.safe_error(exc, definition), outcome='failed', failure_reason='output_validation_failed',
                       diagnostic={'phase': 'output_failed', 'error_type': type(exc).__name__})
            except Exception:
                pass
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
        view_results = None
        if job.get('sql_handoff', {}).get('enabled'):
            from app import flow_view_refresh
            flow_view_refresh.precheck_before_sql(job, progress, artifacts, timings)
            state['sql_started'] = time.perf_counter()
            progress('running', {'stage': 'sql_insertion', 'message': 'Loading recorded-flow outputs into SQL.'}, artifacts, timings)
            state['sql_result'] = flow_sql.load_artifacts(sql_artifacts, job['sql_handoff'],
                progress=lambda detail: progress('running', detail, artifacts, timings))
            progress('running', {'stage': 'sql_insertion_complete',
                'message': f"Inserted {state['sql_result']['rows_written']} row(s) from {state['sql_result']['files_loaded']} file(s).",
                **state['sql_result']}, artifacts, timings)
            # Only a confirmed commit reaches this point; the refresh keeps it.
            view_results = flow_view_refresh.execute_after_sql(job, progress, artifacts, timings, state,
                checkpoint=flow_view_refresh.checkpoint_path(job) if job.get('_standalone') else None)
        timings[0].update(status='succeeded', duration_ms=round((time.perf_counter() - started) * 1000))
        progress('succeeded', {'stage': 'complete', 'message': f'Completed recorded flow with {len(artifacts)} artifacts.'
                             + (f' Refreshed {len(view_results)} materialized view(s).' if view_results else ''),
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
    parser.add_argument('--retry-views', action='store_true', help='Only refresh the materialized views a previous local run left unfinished; no download or SQL insertion.')
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
        from app import flow_view_refresh
        refresh_plan = job.get('post_sql_refresh') or {}
        refresh_views = flow_view_refresh.plan_views(job) if job['sql_handoff']['enabled'] else []
        # Bind recovery to the exact ordered identities used by the executor,
        # excluding presentation/discovery metadata on each planned view.
        refresh_identities = [{key: view[key] for key in ('database', 'schema', 'name')}
                              for view in refresh_views]
        if args.dry_run:
            print(json.dumps({'flow': job['flow']['name'], 'revision': job['recording']['revision'],
                'parameters': job['recording_parameters'], 'sql': job['sql_handoff']['enabled'],
                'transformation': job['transformation']['enabled'],
                'refresh_views': [flow_view_refresh.label(view) for view in refresh_views],
                'refresh_mode': refresh_plan.get('mode', 'off'), 'refresh_frozen_at': refresh_plan.get('discovered_at'),
                'refresh_blocked': refresh_plan.get('blocked')}))
            return 0
        if refresh_plan.get('blocked') and job['sql_handoff']['enabled']:
            raise RuntimeError('Refresh materialized views is blocked in the saved configuration: ' + str(refresh_plan['blocked'])
                               + ' Regenerate the script after fixing it, or run with --no-sql.')
        if args.retry_views:
            if not refresh_views:
                raise RuntimeError('This Flow has no materialized views to refresh.')
            job['job_type'] = flow_view_refresh.RETRY_JOB_TYPE
            job['_standalone'] = True
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
            if job['sql_handoff']['enabled'] and (journal.exists() or args.retry_views):
                try:
                    previous_sql = json.loads(journal.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    previous_sql = {}
                if not isinstance(previous_sql, dict):
                    previous_sql = {}
                if not (args.retry_views and previous_sql.get('outcome') == 'committed'
                        and previous_sql.get('target') == job['sql_handoff']):
                    raise RuntimeError('A previous standalone SQL outcome requires reconciliation; inspect sql-outcome.json before rerunning.'
                        + (' SQL insertion already committed; use --retry-views to finish the remaining views.'
                           if previous_sql.get('outcome') == 'committed' else ''))
                if previous_sql.get('refresh_views') != refresh_identities:
                    # Older journals deliberately stay blocked: a current plan
                    # cannot establish what the already-committed run owed.
                    raise RuntimeError('The saved SQL outcome requires reconciliation: its original materialized-view plan '
                                       'is missing or differs from this script. Use the original script with its matching '
                                       'journal, or reconcile the saved outcome before rerunning.')
            checkpoint = flow_view_refresh.checkpoint_path(job)
            if args.retry_views:
                if not (checkpoint and checkpoint.is_file()):
                    raise RuntimeError('No local view-refresh checkpoint exists; reconcile the committed SQL outcome before rerunning.')
                try:
                    saved_checkpoint = json.loads(checkpoint.read_text(encoding='utf-8'))
                    saved_views = saved_checkpoint['views']
                    if not isinstance(saved_views, list):
                        raise ValueError('Invalid checkpoint views')
                    identities = [{key: view[key] for key in ('database', 'schema', 'name')}
                                  for view in saved_views]
                    if identities != refresh_identities or any(
                        view.get('key') != flow_view_refresh.view_key(view)
                        or view.get('status') not in {'pending', 'running', 'succeeded', 'failed', 'skipped'}
                        for view in saved_views
                    ):
                        raise ValueError('Checkpoint does not match the committed plan')
                except (OSError, ValueError, KeyError, TypeError):
                    raise RuntimeError('The local view-refresh checkpoint requires reconciliation: it is unreadable '
                                       'or does not match the original materialized-view plan.') from None
                completed = [view['key'] for view in saved_views if view['status'] == 'succeeded']
                job['view_retry'] = {'source_run_id': None, 'completed': completed}
            elif job['sql_handoff']['enabled'] and refresh_views and checkpoint and checkpoint.is_file():
                raise RuntimeError('A previous local run left materialized views unfinished; run with --retry-views first or delete view-refresh-checkpoint.json.')
            with (logs / f'{run_id}.jsonl').open('x', encoding='utf-8') as log:
                def progress(status, detail, artifacts=None, timings=None, **extra):
                    if detail.get('stage') == 'sql_insertion':
                        with journal.open('x', encoding='utf-8') as marker:
                            json.dump({'run_id': str(run_id), 'target': job['sql_handoff'], 'outcome': 'unknown',
                                       'refresh_views': refresh_identities}, marker)
                            marker.flush()
                            os.fsync(marker.fileno())
                    elif detail.get('stage') == 'sql_insertion_complete':
                        # Keep the SQL replay barrier until the whole run is
                        # finished, but allow refresh-only recovery once the
                        # loader has confirmed its commit. Replace atomically
                        # so an interrupted write leaves the unknown barrier.
                        confirmed = journal.with_name(journal.name + '.tmp')
                        with confirmed.open('w', encoding='utf-8') as marker:
                            json.dump({'run_id': str(run_id), 'target': job['sql_handoff'], 'outcome': 'committed',
                                       'refresh_views': refresh_identities}, marker)
                            marker.flush()
                            os.fsync(marker.fileno())
                        os.replace(confirmed, journal)
                    log.write(json.dumps({'status': status, 'progress': detail, 'artifacts': artifacts or [], 'timings': timings or []}, default=str) + '\n')
                    log.flush()
                if args.retry_views:
                    job['_standalone'] = True
                    flow_worker.execute_flow(None, job, progress, profile, None, run_id=run_id,
                        register_folder=lambda folder: {'ops': []}, headed=False)
                    journal.unlink(missing_ok=True)
                    return 0
                job['_standalone'] = True
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
