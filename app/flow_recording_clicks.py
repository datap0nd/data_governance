"""Dispatch a recorded Nexacro control once, without guessing its application effect."""
from __future__ import annotations

import time


_CAPTURE_JS = r"""el => {
    const observedId = String(el.id || '');
    const ownerId = observedId.split(':', 1)[0];
    const recognized = /^mainframe(?:\.[A-Za-z0-9_]+)*\.TopFrame(?:\.form\.div_main\.form\.btn_setting|\.Setting\d+\.form\.(?:btn_favorite|div_favorite\.form\.btn_(?:public|private|custom)))$/i.test(ownerId);
    let owner = null;
    if (recognized) {
        for (let current = el; current; current = current.parentElement) {
            if (current.id === ownerId) { owner = current; break; }
        }
    }
    return {el, owner, recognized, observedId, ownerId};
}"""


def click_recorded(node, step, args, kwargs, diagnostic_callback=None):
    """Use an observed owning button for known captions; dispatch exactly once.

    Playwright still checks visibility, stability, hit testing and enabled state.
    A successful dispatch does not prove that the application changed views.
    No selected-state assertion, native fallback, or post-click polling is used.
    """
    if (step.get('action') != 'click' or args or kwargs.get('button', 'left') != 'left'
            or kwargs.get('modifiers') or kwargs.get('click_count', 1) != 1
            or kwargs.get('position') or kwargs.get('trial') or kwargs.get('force')):
        return None
    timeout = kwargs.get('timeout', 120_000)
    timeout = 120_000 if timeout is None or timeout == 0 else timeout
    deadline = time.monotonic() + timeout / 1000
    element = node.element_handle(timeout=timeout)
    if element is None:
        return None
    captured = owner_handle = None
    try:
        captured = element.evaluate_handle(_CAPTURE_JS)
        identity = captured.evaluate('c => ({recognized:c.recognized, element_id:c.observedId, owner_id:c.ownerId})')
        if not identity['recognized']:
            return None
        owner_handle = captured.evaluate_handle('c => c.owner')
        owner = owner_handle.as_element()
        target = {'element_id': identity['element_id'], 'owner_id': identity['owner_id'],
                  'dispatch_target': 'observed_owner' if owner else 'recorded_element'}
        click = {'method': 'playwright.element_handle.click', 'dispatched': False,
                 'attempt': 1, 'confirmation': 'not_requested', 'verification': 'none',
                 'retry_policy': 'never', 'timeout_ms': timeout}
        if diagnostic_callback:
            diagnostic_callback({'phase': 'click_dispatch', 'target': target, 'click': dict(click)})
        # Exact handle/realm captured from this locator; never search another frame.
        (owner or element).click(*args, **{**kwargs, 'timeout': max(1, int((deadline - time.monotonic()) * 1000))})
        click.update(dispatched=True, completed=True)
        if diagnostic_callback:
            diagnostic_callback({'phase': 'click_returned', 'target': target, 'click': dict(click)})
        return {'message': 'Click sent.', 'confirmation': 'not_requested', 'click': click}
    finally:
        for handle in (owner_handle, captured, element):
            if handle is not None:
                try:
                    handle.dispose()
                except Exception:
                    pass  # Cleanup must not mask dispatch failure or cancellation.
