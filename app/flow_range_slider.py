"""Two-handle date range controls, moved by keyboard and proven by read-back.

Shared by the ASAP catalog method and recorded ``set_range`` steps. Nothing
here trusts a screen position: each handle moves with arrow keys by the
difference between its semantic value and the target, and the control's own
value is read back until it matches exactly. A control that never shows the
requested range is a failure before any download starts.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from app import flow_recording

PATTERNS = {'week': r'20\d{4}', 'date': r'20\d{6}'}
MAX_KEY_PRESSES = 1_000
# A control may publish its value some time after a key press; a value counts
# as settled only after it has held still for this many consecutive reads.
SETTLE_READS = 4
SETTLE_INTERVAL_SECONDS = 0.05
SETTLE_ATTEMPTS = 60
VALUE_ATTRIBUTES = ('aria-valuetext', 'aria-valuenow', 'value', 'data-value', 'data-val', 'data-current-value')
HANDLE_SELECTOR = '[role=slider],input[type=range]'


def clean_text(value) -> str:
    return re.sub(r'\s+', ' ', value or '').strip()


def pattern(kind: str) -> str:
    try:
        return PATTERNS[kind]
    except KeyError:
        raise RuntimeError(f'Unsupported range control kind: {kind}') from None


def slider_value(handle, value_pattern: str) -> str | None:
    """Read one semantic slider value without trusting its screen position."""
    for attribute in VALUE_ATTRIBUTES:
        try:
            raw = clean_text(handle.get_attribute(attribute))
        except Exception:
            raw = ''
        match = re.search(value_pattern, raw)
        if match:
            return match.group(0)
    try:
        match = re.search(value_pattern, clean_text(handle.inner_text()))
    except Exception:
        match = None
    return match.group(0) if match else None


def slider_ordinal(value: str, kind: str) -> int:
    if kind == 'week':
        match = re.fullmatch(r'(\d{4})(\d{2})', value or '')
        if not match:
            raise RuntimeError(f'Week slider exposed an invalid value: {value}')
        try:
            return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1).toordinal() // 7
        except ValueError as exc:
            raise RuntimeError(f'Week slider exposed a nonexistent week: {value}') from exc
    if kind == 'date':
        try:
            return datetime.strptime(value or '', '%Y%m%d').date().toordinal()
        except ValueError as exc:
            raise RuntimeError(f'Date slider exposed an invalid value: {value}') from exc
    raise RuntimeError(f'Unsupported range control kind: {kind}')


def move_slider(handle, target: str, kind: str, *, current: str | None = None,
                read_value=None) -> None:
    """Move one focused slider handle by keyboard and verify its exact value."""
    value_pattern = pattern(kind)
    read_value = read_value or (lambda: slider_value(handle, value_pattern))
    current = current or read_value()
    if current is None:
        handle.press('Home')
        current = read_value()
    if current is None:
        raise RuntimeError(f'{kind.title()} slider did not expose its current value.')
    delta = slider_ordinal(target, kind) - slider_ordinal(current, kind)
    if abs(delta) > MAX_KEY_PRESSES:
        raise RuntimeError(
            f'{kind.title()} slider target is more than {MAX_KEY_PRESSES:,} steps from its current value.'
        )
    key = 'ArrowRight' if delta > 0 else 'ArrowLeft'
    for _step in range(abs(delta)):
        handle.press(key)
    actual = None
    for _attempt in range(10):
        actual = read_value()
        if actual == target:
            break
        time.sleep(0.05)
    if actual != target:
        raise RuntimeError(
            f'{kind.title()} slider mismatch. Requested: {target}. Selected: {actual or "unknown"}.'
        )


def range_values(scope, handles: list, kind: str) -> list[str | None]:
    """Read both range values from handle semantics or the control's visible labels."""
    value_pattern = pattern(kind)
    values = [slider_value(handle, value_pattern) for handle in handles]
    if None not in values:
        return values
    try:
        visible = re.findall(rf'(?<!\d){value_pattern}(?!\d)', clean_text(scope.inner_text()))
    except Exception:
        visible = []
    if len(visible) == len(handles):
        return [value or visible[index] for index, value in enumerate(values)]
    return values


def find_handles(container) -> list:
    """The exactly two visible handles of one range control inside a recorded box."""
    handles = container.locator(HANDLE_SELECTOR)
    visible = []
    for index in range(handles.count()):
        handle = handles.nth(index)
        try:
            if handle.is_visible():
                visible.append(handle)
        except Exception:
            continue
    if len(visible) != 2:
        raise RuntimeError(
            'The date range control box must contain exactly two visible slider handles; '
            f'found {len(visible)}.'
        )
    return visible


def set_range_values(scope, handles: list, start: str, end: str, kind: str, *,
                     label: str = 'Date range control', progress=None) -> list[str]:
    """Set and read back a two-handle range without coordinate guessing."""
    if slider_ordinal(end, kind) < slider_ordinal(start, kind):
        raise RuntimeError(f'{label} range ends before it starts: {start} to {end}')
    current = range_values(scope, handles, kind)
    # When advancing a collapsed one-period range, the upper handle must move
    # first or the lower handle is constrained by the old upper value.
    if current[1] and slider_ordinal(start, kind) > slider_ordinal(current[1], kind):
        order = ((1, end), (0, start))
    elif current[0] and slider_ordinal(end, kind) < slider_ordinal(current[0], kind):
        order = ((0, start), (1, end))
    else:
        order = ((0, start), (1, end))
    for index, target in order:
        if progress:
            progress({'handle': index, 'target': target, 'current': current[index]})
        move_slider(
            handles[index], target, kind, current=current[index],
            read_value=lambda index=index: range_values(scope, handles, kind)[index],
        )
        current = range_values(scope, handles, kind)
    actual = range_values(scope, handles, kind)
    if actual != [start, end]:
        raise RuntimeError(
            f'{label} range did not match the flow. Requested: {[start, end]}. Selected: {actual}.'
        )
    return actual


def week_options(start: str, end: str) -> list[str]:
    """Expand compact week bounds into every valid YYYYWW value."""
    start_match = re.fullmatch(r'(\d{4})(\d{2})', start)
    end_match = re.fullmatch(r'(\d{4})(\d{2})', end)
    if not start_match or not end_match:
        return []
    try:
        current = date.fromisocalendar(int(start_match.group(1)), int(start_match.group(2)), 1)
        final = date.fromisocalendar(int(end_match.group(1)), int(end_match.group(2)), 1)
    except ValueError:
        return []
    if final < current or (final - current).days > 7 * 104:
        return []
    values = []
    while current <= final:
        year, week, _weekday = current.isocalendar()
        values.append(f'{year:04d}{week:02d}')
        current += timedelta(days=7)
    return values


def week_of(day: date, week_days: str = 'sunday') -> date:
    """Monday of the ISO week that owns ``day`` under the control's week convention."""
    return flow_recording.week_monday(day, week_days)


def week_bounds(monday: date, week_days: str = 'sunday') -> tuple[date, date]:
    """First and last calendar day of the week that ISO-starts on ``monday``."""
    return flow_recording.week_bounds(monday, week_days)


def settled_value(read):
    """A value that held still for SETTLE_READS consecutive reads after a key press."""
    previous, held = read(), 1
    for _attempt in range(SETTLE_ATTEMPTS):
        time.sleep(SETTLE_INTERVAL_SECONDS)
        current = read()
        held = held + 1 if current == previous else 1
        previous = current
        if held >= SETTLE_READS:
            return current
    raise RuntimeError('The range control value did not settle after a key press.')


def read_extreme(scope, handles: list, kind: str, *, end: bool = True) -> str:
    """Send a handle to its End (or Home) and return the control's proven limit.

    The value is read until it holds still, a second press must leave it
    unchanged, and a declared aria-valuemax/aria-valuemin must agree, so a
    slowly updating control can never hand back a stale value as its limit.
    """
    index, key, side = (1, 'End', 'newest') if end else (0, 'Home', 'oldest')
    handle = handles[index]

    def read():
        return range_values(scope, handles, kind)[index]

    handle.press(key)
    value = settled_value(read)
    handle.press(key)
    if value is None or settled_value(read) != value:
        raise RuntimeError(f'The range control did not settle on its {side} value.')
    try:
        declared = clean_text(handle.get_attribute('aria-valuemax' if end else 'aria-valuemin'))
    except Exception:
        declared = ''
    match = re.search(pattern(kind), declared)
    if match and match.group(0) != value:
        raise RuntimeError(
            f'The range control shows {value} as its {side} value but declares {match.group(0)}.'
        )
    return value


def week_dates(value: str) -> tuple[str, str]:
    """Return ASAP's Sunday-to-Saturday dates for one ISO-numbered week."""
    match = re.fullmatch(r'(\d{4})-W(\d{2})', value)
    if not match:
        raise RuntimeError(f'Week must use YYYY-Www: {value}')
    try:
        monday = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:
        raise RuntimeError(f'Week does not exist: {value}') from exc
    first, last = week_bounds(monday, 'sunday')
    return first.strftime('%Y%m%d'), last.strftime('%Y%m%d')
