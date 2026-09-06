"""Bounded Nexacro navigation for the exact control a recording resolved.

This is deliberately not bookmark navigation. Only Setting, Favorite and its
three scope buttons can receive one native fallback; all other clicks retain
ordinary Playwright dispatch.
"""
from __future__ import annotations

import time

from app import flow_gscm


class RecordingClickError(RuntimeError):
    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


_CAPTURE_JS = r"""el => {
    const doc = el.ownerDocument, view = doc.defaultView;
    const observedId = String(el.id || '');
    const ownerId = observedId.split(':', 1)[0];
    let kind = null, scope = null, parentId = null;
    const setting = /^(mainframe(?:\.[A-Za-z0-9_]+)*\.TopFrame)\.form\.div_main\.form\.btn_setting$/i.exec(ownerId);
    const favorite = /^(mainframe(?:\.[A-Za-z0-9_]+)*\.TopFrame\.Setting\d+\.form)\.btn_favorite$/i.exec(ownerId);
    const tab = /^(mainframe(?:\.[A-Za-z0-9_]+)*\.TopFrame\.Setting\d+\.form\.div_favorite\.form)\.btn_(public|private|custom)$/i.exec(ownerId);
    if (setting) { kind = 'setting'; parentId = setting[1]; }
    else if (favorite) { kind = 'favorite'; parentId = favorite[1]; }
    else if (tab) { kind = 'scope'; parentId = tab[1]; scope = tab[2].toLowerCase(); }
    const resolve = () => {
        if (!view.nexacro || typeof view.nexacro.getApplication !== 'function') return null;
        let component = view.nexacro.getApplication();
        for (const part of ownerId.split('.')) {
            if (!component) return null;
            component = component[part] ?? component.components?.[part];
        }
        return component || null;
    };
    const component = kind ? resolve() : null;
    let owner = null;
    for (let current = el; current; current = current.parentElement) {
        if (current.id === ownerId) { owner = current; break; }
    }
    return {el, owner, doc, view, observedId, ownerId, kind, scope, parentId,
        component, handler: component?.on_fire_onclick, resolve};
}"""


_STATE_JS = r"""captured => {
    const {el, doc, observedId, ownerId, kind, scope, parentId} = captured;
    const visible = node => {
        if (!node?.isConnected) return false;
        const rect = node.getBoundingClientRect(), style = doc.defaultView.getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    const enabled = node => {
        for (let current = node; current; current = current.parentElement) {
            if (current.disabled || current.getAttribute('aria-disabled') === 'true') return false;
        }
        return true;
    };
    const ownerNodes = ownerId ? [...doc.querySelectorAll('#' + CSS.escape(ownerId))] : [];
    const owners = ownerNodes.filter(visible);
    const owner = captured.owner || el;
    let component = null;
    try { component = captured.resolve(); } catch (_) {}
    let nativeEnabled = true;
    for (let current = component; current; current = current.parent) {
        const state = typeof current.get_enable === 'function' ? current.get_enable() : current.enable;
        const shown = typeof current.get_visible === 'function' ? current.get_visible() : current.visible;
        if (state === false || shown === false) { nativeEnabled = false; break; }
    }
    const targetVisible = visible(el) && visible(owner);
    const targetEnabled = enabled(el) && enabled(owner) && nativeEnabled;
    const sameTarget = el.isConnected && owner.isConnected && el.ownerDocument === doc && String(el.id || '') === observedId
        && (!captured.owner || captured.owner.id === ownerId)
        && component === captured.component && component?.on_fire_onclick === captured.handler;
    const exactVisible = prefix => [...doc.querySelectorAll('[id]')].some(node =>
        (node.id === prefix || node.id.startsWith(prefix + '.') || node.id.startsWith(prefix + ':')) && visible(node));
    let confirmed = false, transition = null, open = null, selectedState = null;
    if (kind === 'setting') {
        const prefix = parentId + '.Setting';
        confirmed = [...doc.querySelectorAll('[id]')].some(node => node.id.startsWith(prefix)
            && /^\d+(?:$|\.|:)/.test(node.id.slice(prefix.length)) && visible(node));
        open = confirmed;
        transition = 'setting_visible';
    } else if (kind === 'favorite') {
        confirmed = exactVisible(parentId + '.div_favorite');
        open = confirmed;
        transition = 'favorite_visible';
    } else if (kind === 'scope') {
        let selected = false;
        for (const node of [el, owner]) {
            if (node.getAttribute('aria-selected') === 'true' || node.getAttribute('aria-pressed') === 'true'
                    || /(?:^|\s)selected(?:$|\s)/.test(node.getAttribute('userstatus') || '')) selected = true;
        }
        if (component && typeof component.getSelectStatus === 'function') selected ||= component.getSelectStatus() === true;
        if (component && typeof component.get_selected === 'function') selected ||= component.get_selected() === true;
        open = exactVisible(parentId.replace(/\.form$/, ''));
        selectedState = selected;
        confirmed = targetVisible && selected && open;
        transition = scope + '_selected';
    }
    return {kind, scope, component_id: ownerId, element_id: observedId,
        visible: targetVisible, enabled: targetEnabled, owner_count: owners.length,
        same_target: sameTarget, native_available: typeof component?.on_fire_onclick === 'function',
        confirmed, transition, open, selected: selectedState};
}"""


# Use the existing reviewed native entry point, within the captured element's
# own JavaScript realm. Re-resolve immediately before dispatch so replacement
# components or handlers cannot inherit the original click's authorization.
_FALLBACK_JS = r"""captured => {
    const state = (""" + _STATE_JS + r""")(captured);
    if (state.confirmed) return {fired: false, reason: 'already_confirmed', state};
    if (!state.same_target || !state.visible || !state.enabled || state.owner_count > 1)
        return {fired: false, reason: 'target_changed', state};
    if (!state.native_available) return {fired: false, reason: 'native_unavailable', state};
    const result = (""" + flow_gscm._NATIVE_COMPONENT_CLICK_JS + r""")(captured.ownerId);
    return {...result, state};
}"""


def click_recorded(node, step, args, kwargs, diagnostic_callback=None, *,
                   settle_timeout_ms=flow_gscm.DIALOG_READY_TIMEOUT_MS,
                   proof_timeout_ms=15_000, poll_ms=100):
    """Return None for a generic click, or a confirmed navigation result.

    The caller must restrict this helper to GSCM click actions. Keyword-only
    budgets are test seams; all work is also capped by the recorded timeout.
    Callback exceptions propagate, including cancellation signals.
    """
    if (step.get('action') != 'click' or args or kwargs.get('button', 'left') != 'left'
            or kwargs.get('modifiers') or kwargs.get('click_count', 1) != 1
            or kwargs.get('position') or kwargs.get('trial') or kwargs.get('force')):
        return None
    timeout = kwargs.get('timeout', 120_000)
    timeout = 120_000 if timeout is None or timeout == 0 else timeout
    deadline = time.monotonic() + max(0, timeout) / 1000
    element = node.element_handle(timeout=max(1, int(timeout)))
    if element is None:
        return None
    captured = element.evaluate_handle(_CAPTURE_JS)
    owner_handle = None
    try:
        before = captured.evaluate(_STATE_JS)
        if not before['kind']:
            return None
        # A recorded gear click may intentionally close an existing popup.
        # Do not reinterpret it as an instruction to open the popup again.
        if before['kind'] == 'setting' and before['confirmed']:
            return None
        match_count = node.count()
        frame_locator = []
        for part in step.get('locator', []):
            if part.get('method') in {'frame_locator', 'content_frame'}:
                frame_locator.append(part)
        target = {'element_id': before['element_id'], 'owner_id': before['component_id'],
            'frame_locator': frame_locator, 'match_count': match_count,
            'visible_count': int(before['visible']), 'enabled_count': int(before['enabled'])}
        click = {'method': 'playwright', 'dispatched': False, 'attempt': 1,
            'confirmation': 'pending', 'transition': before['transition'],
            'native_available': before['native_available'], 'before': before, 'after': before}

        def emit(phase, state=None):
            if state is not None:
                click['after'] = state
            detail = {'phase': phase, 'target': dict(target), 'click': dict(click)}
            if diagnostic_callback:
                diagnostic_callback(detail)
            return detail

        def fail(reason, state):
            click['confirmation'] = reason
            detail = emit('click_failed', state)
            label = before['scope'].title() if before['scope'] else before['kind'].title()
            message = f'{label} was not selected.' if before['kind'] == 'scope' else f'{label} did not open.'
            raise RecordingClickError(message, detail)

        if match_count != 1 or before['owner_count'] > 1:
            fail('target_not_actionable', before)
        emit('click_dispatch')
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        if not remaining:
            fail('timeout', before)
        # The owning element is an observed ancestor, never a guessed sibling
        # or a first match. Playwright still performs its normal hit testing.
        owner_handle = captured.evaluate_handle('captured => captured.owner')
        owning_element = owner_handle.as_element()
        (owning_element or element).click(*args, **{**kwargs, 'timeout': remaining})
        click['dispatched'] = True

        def wait_for_transition(budget_ms):
            stop = min(deadline, time.monotonic() + max(0, budget_ms) / 1000)
            next_notice = 0
            while True:
                state = captured.evaluate(_STATE_JS)
                if state['confirmed']:
                    return state
                now = time.monotonic()
                if now >= next_notice:
                    emit('click_waiting', state)
                    next_notice = now + 1
                if now >= stop:
                    return state
                time.sleep(min(max(1, poll_ms) / 1000, stop - now))

        after = wait_for_transition(settle_timeout_ms)
        native_fallback = False
        if not after['confirmed']:
            if time.monotonic() >= deadline:
                fail('timeout', after)
            emit('click_fallback_check', after)
            result = captured.evaluate(_FALLBACK_JS)
            if result.get('reason') == 'already_confirmed':
                after = result['state']
            elif not result.get('fired'):
                fail(result.get('reason', 'native_failed'), result.get('state', after))
            else:
                native_fallback = True
                click.update(method='nexacro', attempt=2)
                emit('click_native_dispatch', result.get('state', after))
                after = wait_for_transition(proof_timeout_ms)
        if not after['confirmed']:
            fail('transition_missing', after)
        click['confirmation'] = 'confirmed'
        emit('click_confirmed', after)
        label = before['scope'].title() if before['scope'] else before['kind'].title()
        return {'message': f'{label} ' + ('selected.' if before['kind'] == 'scope' else 'opened.'),
            'outcome': 'completed', 'confirmation': 'confirmed', 'control': before['kind'],
            'component_id': before['component_id'], 'native_fallback': native_fallback,
            'evidence': after}
    finally:
        for handle in (owner_handle, captured, element):
            if handle is not None:
                try:
                    handle.dispose()
                except Exception:
                    # A closed frame must not mask cancellation or the error
                    # that caused this helper to unwind.
                    pass
