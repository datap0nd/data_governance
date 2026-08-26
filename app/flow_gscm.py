"""GSCM (Nexacro) portal adapter: bookmark discovery and Excel export.

GSCM (`mdscm.sec.samsung.net`) is a TOBESOFT Nexacro client, not an HTML
application. Nothing on the page is a link, a table, or a form control: every
element is a Nexacro component whose DOM ``id`` is its absolute path in the
component tree, for example::

    mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown

**Where the bookmarks live.** Not on the home screen. GSCM keeps them in the
Setting dialog: ``Setting > Favorite``, scoped by a business dropdown (MX), and
split across three tabs - ``Private``, ``Public``, and ``Custom``. Each tab
holds a folder tree (``SCM > Actual Sales > MENA_Actual_sales``), and a report
is opened by selecting its row and pressing ``Go >>``. The home screen's own
Favorite widget shows only the entries a user has *pinned*, which is usually
empty - reading it finds nothing even for a user with hundreds of bookmarks.

**Where discovery gets its data.** The loaded Nexacro application exposes the
``gds_bookmark`` dataset that backs the Favorite dialog. Discovery reads that
dataset directly, preserving bookmark ids, scope, category, and name without
depending on virtualized DOM rows. A DOM fallback remains for deployments that
do not expose the application dataset, but it is scoped strictly to the
Setting dialog's ``grd_bookmark`` grid. Navigation and opening a selected
bookmark still use the rendered controls.

**The framework fights automation.** Nexacro parks a full-screen wait overlay
over the page and floats un-anchored popup cards above everything else. Both
swallow clicks and both outlive the work that raised them, so every interaction
clears them first and clicks with ``force``.

The module holds no Playwright import: it drives whatever page object it is
handed, which keeps the automation unit-testable without a browser.
"""

from __future__ import annotations

import re
import time
from typing import Any

GSCM_PORTAL_ADAPTER = "gscm_portal"

#: Full-screen Nexacro overlay (z-index 2,000,000) shown while the backend
#: computes. It routinely stays up after the data has landed.
WAIT_WINDOW_ID = "mainframe.waitwindow"

#: Component name of the global Excel export button on the MDI toolbar.
EXCEL_BUTTON_COMPONENT = "btn_exceldown"
FALLBACK_EXCEL_BUTTON_ID = (
    "mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown"
)

#: Labels inside the Setting dialog. Order matters: the panel is opened first,
#: then one scope tab is selected, then a row is run with the Go button.
FAVORITE_PANEL_LABEL = "Favorite"
SCOPE_TABS = ("Private", "Public", "Custom")
GO_LABELS = ("Go >>", "Go»", "Go »", "Go")
CLOSE_LABELS = ("Close",)
GO_BUTTON_ID = (
    "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form.btn_go"
)
#: The gear that opens Setting, read off the live portal. Tried first and
#: exactly; the hints below only matter if GSCM renames it.
SETTING_BUTTON_ID = "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting"
#: Ordered most specific first. Order is what makes this safe: the top bar also
#: holds btn_user and btn_notice, and a generic hint that matched one of those
#: first would open a profile popover instead of Setting.
SETTING_BUTTON_HINTS = (
    "btn_setting", "btn_config", "btn_setup", "btn_env", "btn_pref",
    "btn_option", "btn_gear", "setting", "config", "gear", "preference",
)
#: The gear carries no text, so when no id hint matches it is looked for by
#: position instead: a control in the top frame, on the right of the business
#: pills (All / MX / DA / ...), which sit at the far right of that bar.
TOP_BAR_MAX_Y = 60
TOP_BAR_MIN_X = 1_000
MAX_GEAR_TRIES = 20
#: Framework furniture that is visible, text-less, and in the top bar, but is
#: never a control worth clicking. Scrollbar arrows alone outnumbered the real
#: buttons and crowded the gear off the end of the candidate list.
ICON_CHROME_MARKERS = (
    "scrollbar", "decbutton", "incbutton", "trackbar", "thumb",
    ":icontext", "sta_", "static", "_line", "shadow", "border",
)
#: Controls this adapter must never click, matched against both the visible
#: label and the component id. GSCM's Favorite dialog sits next to Save,
#: Unselect, and a pin toggle on every row: the scan reads and runs reports, it
#: never edits what the user has stored. A hint that would reach one of these
#: is dropped rather than tried.
FORBIDDEN_CLICK_WORDS = (
    "save", "delete", "remove", "del_", "drop", "clear", "reset", "unselect",
    "apply", "submit", "confirm", "ok_", "btn_ok", "pin", "unpin", "share",
    "new", "add", "edit", "modify", "update", "upload", "import", "export_all",
)

#: Rows that are chrome, not bookmarks.
TREE_NOISE = {
    "alphabet", "latest", "unselect", "select", "save", "close", "go", "go >>",
    "favorite", "layout", "dashboard", "installation", "setting",
    *(item.casefold() for item in SCOPE_TABS),
}

#: Nexacro's bookmark dataset stores the top-level module as a short scope
#: code. Preserve unknown codes rather than inventing a module name.
BOOKMARK_SCOPE_NAMES = {
    "AS": "SCM",
    "MT": "MDM",
}

BOOKMARK_DATASET_COLUMNS = (
    "userreportid", "userid", "originuserid", "userreportname",
    "menuscope", "gbm", "menuid", "menuname", "menugroupid",
    "menugroupname", "scope", "publicscope", "publicscopevalue",
)

PORTAL_READY_TIMEOUT_MS = 180_000
DIALOG_READY_TIMEOUT_MS = 60_000
BOOKMARK_DATASET_READY_TIMEOUT_MS = 30_000
BOOKMARK_SETTLE_TIMEOUT_MS = 300_000
#: Switching Favorite scope triggers an asynchronous server request before the
#: virtual grid rebinds. Under peak load GSCM regularly needs more than the 15
#: seconds this wait originally allowed, so the budget covers a slow backend.
FAVORITE_ROWS_TIMEOUT_MS = 45_000
POPUP_VERIFY_TIMEOUT_MS = 2_000
POPUP_VERIFY_INTERVAL_MS = 250
#: The wait overlay flickers between backend calls. Only treat the screen as
#: idle once it has stayed hidden across this many consecutive polls.
IDLE_POLLS_REQUIRED = 6
IDLE_POLL_INTERVAL_MS = 500
#: Nexacro renders a panel a beat after the overlay clears.
POST_IDLE_SETTLE_MS = 3_000
TAB_SETTLE_MS = 2_500
#: Two rows are on the same tree level when their left edges are within this
#: many pixels. Nexacro indents one level by roughly 12-16px.
INDENT_TOLERANCE_PX = 6
MAX_INVENTORY_ITEMS = 120

# Nexacro keeps the Favorite grid's scroll position in its own component
# state. The live grid exposes these controls even though ``scrollHeight`` on
# the surrounding HTML element never changes from its viewport height.
FAVORITE_GRID_ID_SUFFIX = "Setting1.form.div_favorite.form.grd_bookmark"
FAVORITE_SCROLL_PAGE_STEPS = 8
FAVORITE_SCROLL_RESET_PASSES = 40

DOWNLOAD_TEXT = "Excel download"

#: Samsung SSO's sign-in page. The automation profile holds one session per
#: portal, so ASAP can be signed in while GSCM is not - which is exactly what a
#: bare "the client did not render" message fails to convey.
LOGIN_PAGE_MARKERS = (
    "single sign on login", "please enter your password", "ad sso",
    "change password", "sign in", "log in",
)
LOGIN_PAGE_ELEMENT_IDS = ("submitbutton", "loginmessage", "userid", "password")

_UNSAFE_NAME_RE = re.compile(r"[\x00-\x1f]+")


# ── Browser-side scripts ──

_HIDE_WAIT_WINDOW_JS = """(waitWindowId) => {
    const overlay = document.getElementById(waitWindowId);
    if (!overlay) return false;
    const wasVisible = overlay.style.display !== 'none'
        && overlay.style.visibility !== 'hidden';
    overlay.style.display = 'none';
    overlay.style.visibility = 'hidden';
    return wasVisible;
}"""

_WAIT_WINDOW_VISIBLE_JS = """(waitWindowId) => {
    const overlay = document.getElementById(waitWindowId);
    if (!overlay) return false;
    const style = window.getComputedStyle(overlay);
    return style.display !== 'none' && style.visibility !== 'hidden'
        && overlay.getClientRects().length > 0;
}"""

#: Every visible element that renders its own short text, with the geometry the
#: tree reconstruction needs. Elements whose text comes entirely from a child
#: are skipped so one label is reported once, at its leaf-most node.
_VISIBLE_TEXT_JS = """() => {
    const out = [];
    for (const element of document.querySelectorAll('*')) {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(element);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        const text = (element.textContent || '').trim();
        if (!text || text.length > 200) continue;
        let childHasSameText = false;
        for (const child of element.children) {
            if ((child.textContent || '').trim() === text) { childHasSameText = true; break; }
        }
        if (childHasSameText) continue;
        out.push({
            id: element.id || '',
            text: text,
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
        });
        if (out.length >= 4000) break;
    }
    return out;
}"""

#: Read the in-memory source of truth behind the Favorite dialog. Returning
#: ``null`` means this Nexacro root does not expose the dataset. An available
#: but empty dataset is returned explicitly so callers can distinguish it from
#: an unsupported runtime.
_BOOKMARK_DATASET_JS = """(columns) => {
    if (typeof nexacro === 'undefined' || !nexacro
            || typeof nexacro.getApplication !== 'function') return null;
    const app = nexacro.getApplication();
    const ds = app && app.gds_bookmark;
    if (!ds || typeof ds.getRowCount !== 'function'
            || typeof ds.getColumn !== 'function') return null;
    const rows = [];
    for (let rowIndex = 0; rowIndex < ds.getRowCount(); rowIndex++) {
        const row = {};
        for (const column of columns) row[column] = ds.getColumn(rowIndex, column);
        rows.push(row);
    }
    return {available: true, rows};
}"""

#: DOM fallback for older or differently packaged Nexacro deployments. This
#: deliberately starts at the Setting popup grid. A global ``textContent``
#: sweep can concatenate the top navigation labels into a phantom bookmark.
_FAVORITE_TREE_ROWS_JS = r"""() => {
    const grids = Array.from(document.querySelectorAll('[id]')).filter(element => {
        const id = element.id || '';
        return id.endsWith('Setting1.form.div_favorite.form.grd_bookmark');
    });
    const out = [];
    for (const grid of grids) {
        for (const row of grid.querySelectorAll('[id*=".body.gridrow_"]')) {
            if (!/\.body\.gridrow_\d+$/.test(row.id || '')) continue;
            const label = Array.from(row.querySelectorAll('[id]')).find(element =>
                (element.id || '').toLowerCase().includes('treeitemtext'));
            if (!label) continue;
            const labelRect = label.getBoundingClientRect();
            const rowRect = row.getBoundingClientRect();
            const style = window.getComputedStyle(label);
            if (labelRect.width <= 0 || labelRect.height <= 0
                    || style.visibility === 'hidden' || style.display === 'none') continue;
            const button = Array.from(row.querySelectorAll('[id]')).find(element =>
                (element.id || '').toLowerCase().includes('treeitembutton'));
            let isFolder = false;
            if (button) {
                const buttonStyle = window.getComputedStyle(button);
                const buttonRect = button.getBoundingClientRect();
                isFolder = buttonStyle.visibility !== 'hidden'
                    && buttonStyle.display !== 'none'
                    && buttonRect.width > 0 && buttonRect.height > 0;
            }
            out.push({
                id: row.id || label.id || '',
                text: (label.textContent || '').trim(),
                x: Math.round(labelRect.left),
                y: Math.round(rowRect.top),
                w: Math.round(labelRect.width),
                h: Math.round(rowRect.height),
                is_folder: isFolder,
            });
        }
    }
    return out;
}"""

#: Visible controls that carry no text at all. The Setting gear is one of
#: these, which is why a text-only inventory could not report it and left the
#: adapter guessing at its id.
_ICON_CONTROLS_JS = """() => {
    const out = [];
    for (const element of document.querySelectorAll('[id]')) {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        if (rect.width > 80 || rect.height > 80) continue;
        const style = window.getComputedStyle(element);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        if ((element.textContent || '').trim()) continue;
        out.push({
            id: element.id,
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
        });
        if (out.length >= 400) break;
    }
    return out;
}"""

#: Nexacro grids virtualize: only the rows in view exist in the DOM. Paging the
#: tallest scrollable container is how the rest are reached.
_SCROLL_TREE_JS = """() => {
    const grid = Array.from(document.querySelectorAll('[id]')).find(element =>
        (element.id || '').endsWith('Setting1.form.div_favorite.form.grd_bookmark'));
    if (!grid) return null;
    let best = null;
    let bestOverflow = 0;
    for (const element of [grid, ...grid.querySelectorAll('*')]) {
        const overflow = element.scrollHeight - element.clientHeight;
        if (overflow <= 8) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 120 || rect.height < 80) continue;
        if (overflow > bestOverflow) { bestOverflow = overflow; best = element; }
    }
    if (!best) return null;
    const before = best.scrollTop;
    best.scrollTop = Math.min(best.scrollHeight, before + Math.max(60, best.clientHeight - 40));
    return { moved: best.scrollTop !== before, top: best.scrollTop, max: bestOverflow };
}"""

_RESET_TREE_JS = """() => {
    const grid = Array.from(document.querySelectorAll('[id]')).find(element =>
        (element.id || '').endsWith('Setting1.form.div_favorite.form.grd_bookmark'));
    if (!grid) return null;
    let best = null;
    let bestOverflow = 0;
    for (const element of [grid, ...grid.querySelectorAll('*')]) {
        const overflow = element.scrollHeight - element.clientHeight;
        if (overflow <= 8) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 120 || rect.height < 80) continue;
        if (overflow > bestOverflow) { bestOverflow = overflow; best = element; }
    }
    if (!best) return {available: true, moved: false};
    const before = best.scrollTop;
    best.scrollTop = 0;
    return {available: true, moved: before !== 0};
}"""

_POPUP_RECORDS_JS = """() => {
    const popupPattern = /(?:^|[._:])(notice|alert|message|msg|confirm)(?:$|[._:])/;
    const closePattern = /(?:^|[._:])(?:btn_?)?(?:close|x)(?:$|[._:])/;
    const excluded = ['topframe.setting1', 'div_favorite', 'mainframe.waitwindow'];
    const visibleRect = element => {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return null;
        const style = window.getComputedStyle(element);
        if (style.visibility === 'hidden' || style.display === 'none') return null;
        return {
            x: Math.round(rect.left), y: Math.round(rect.top),
            w: Math.round(rect.width), h: Math.round(rect.height),
        };
    };
    const isPopupId = value => {
        const lowered = String(value || '').toLowerCase();
        return lowered.includes('popup') || lowered.includes('pdv_')
            || popupPattern.test(lowered);
    };
    const isExcluded = value => {
        const lowered = String(value || '').toLowerCase();
        return excluded.some(fragment => lowered.includes(fragment));
    };
    const terminalId = value => String(value || '').split(':', 1)[0].split('.').pop();
    const isCloseControl = element => {
        const id = (element.id || '').toLowerCase();
        const text = (element.textContent || '').trim().toLowerCase();
        return id.includes('close') || closePattern.test(id)
            || ['close', 'x', '\u00d7'].includes(text);
    };

    const byContainer = new Map();
    for (const close of document.querySelectorAll('[id]')) {
        const closeRect = visibleRect(close);
        if (!closeRect || !isCloseControl(close) || isExcluded(close.id)) continue;
        const popupAncestors = [];
        for (let current = close; current; current = current.parentElement) {
            if (!current.id || isExcluded(current.id) || !isPopupId(current.id)) continue;
            const rect = visibleRect(current);
            if (rect) popupAncestors.push({element: current, rect});
        }
        // Nexacro component ids preserve ancestry even in builds that render
        // component nodes as DOM siblings rather than nested descendants.
        for (const candidate of document.querySelectorAll('[id]')) {
            if (!candidate.id || candidate === close || isExcluded(candidate.id)) continue;
            if (!close.id.startsWith(candidate.id + '.') || !isPopupId(candidate.id)) continue;
            const rect = visibleRect(candidate);
            if (rect && !popupAncestors.some(item => item.element === candidate)) {
                popupAncestors.push({element: candidate, rect});
            }
        }
        if (!popupAncestors.length) continue;
        popupAncestors.sort((a, b) => {
            const aDirect = isPopupId(terminalId(a.element.id)) ? 0 : 1;
            const bDirect = isPopupId(terminalId(b.element.id)) ? 0 : 1;
            return (aDirect - bDirect)
                || ((b.rect.w * b.rect.h) - (a.rect.w * a.rect.h));
        });
        const container = popupAncestors[0];
        const containerId = container.element.id || close.id;
        if (!byContainer.has(containerId)) {
            byContainer.set(containerId, {
                container_id: containerId,
                x: container.rect.x, y: container.rect.y,
                w: container.rect.w, h: container.rect.h,
                closers: [],
            });
        }
        byContainer.get(containerId).closers.push({
            id: close.id || '',
            text: (close.textContent || '').trim().slice(0, 80),
            x: closeRect.x, y: closeRect.y, w: closeRect.w, h: closeRect.h,
        });
    }
    return Array.from(byContainer.values());
}"""

_ELEMENT_RECT_JS = """(targetIds) => {
    for (const id of targetIds) {
        const element = document.getElementById(id);
        if (!element) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(element);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        return {
            id: element.id || id,
            x: Math.round(rect.left), y: Math.round(rect.top),
            w: Math.round(rect.width), h: Math.round(rect.height),
        };
    }
    return null;
}"""

_ID_MATCH_JS = """(hints) => {
    const ids = [];
    for (const element of document.querySelectorAll('[id]')) {
        const lowered = (element.id || '').toLowerCase();
        if (!hints.some(hint => lowered.includes(hint))) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        ids.push({ id: element.id, x: Math.round(rect.left), y: Math.round(rect.top) });
    }
    return ids;
}"""

_COMPONENT_VISIBLE_JS = """(fragments) => {
    for (const element of document.querySelectorAll('[id]')) {
        const id = element.id || '';
        if (!fragments.some(fragment => id.includes(fragment))) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(element);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        return true;
    }
    return false;
}"""

#: Fire the Nexacro component rather than only its rendered caption node.
#:
#: Nexacro paints captions in children such as ``btn_public:icontext``.  A DOM
#: click on that child can highlight the control without dispatching the
#: component's registered ``onclick`` handler.  Resolve the public component
#: path from the application object and use Nexacro's own event entry point as
#: a verified fallback.  This never guesses a component: callers must supply
#: an id that was already observed on screen.
_NATIVE_COMPONENT_CLICK_JS = """(elementId) => {
    if (typeof nexacro === 'undefined' || !nexacro
            || typeof nexacro.getApplication !== 'function') {
        return {available: false, fired: false, reason: 'nexacro-unavailable'};
    }
    const componentId = String(elementId || '').split(':', 1)[0];
    if (!componentId) return {available: false, fired: false, reason: 'empty-id'};
    const app = nexacro.getApplication();
    let component = app;
    for (const part of componentId.split('.')) {
        if (!part) continue;
        if (component && component[part] != null) {
            component = component[part];
            continue;
        }
        if (component && component.components && component.components[part] != null) {
            component = component.components[part];
            continue;
        }
        return {available: false, fired: false, reason: `missing:${part}`};
    }
    if (!component || typeof component.on_fire_onclick !== 'function') {
        return {available: !!component, fired: false, reason: 'onclick-unavailable'};
    }
    try {
        component.on_fire_onclick(
            'lbutton', false, false, false,
            0, 0, 0, 0, 0, 0,
            component, component,
        );
        return {available: true, fired: true, component_id: componentId};
    } catch (error) {
        return {
            available: true,
            fired: false,
            reason: String(error && error.message ? error.message : error),
        };
    }
}"""


# ── Roots: the portal may render panels inside frames ──


def _roots(page) -> list:
    """The page plus every frame, so a panel inside an iframe is still seen."""
    roots = [page]
    for attribute in ("frames",):
        try:
            for frame in getattr(page, attribute, None) or []:
                if frame not in roots:
                    roots.append(frame)
        except Exception:
            continue
    return roots


def _evaluate_everywhere(page, script, argument=None) -> list[tuple[Any, Any]]:
    """Run one script in every root, returning each root's non-empty result."""
    results = []
    for root in _roots(page):
        try:
            value = root.evaluate(script, argument) if argument is not None else root.evaluate(script)
        except Exception:
            continue
        if value:
            results.append((root, value))
    return results


def _component_visible(page, *fragments: str) -> bool:
    """Whether any live root exposes a visible component path fragment."""
    wanted = [str(fragment) for fragment in fragments if str(fragment)]
    if not wanted:
        return False
    return any(
        bool(value)
        for _root, value in _evaluate_everywhere(page, _COMPONENT_VISIBLE_JS, wanted)
    )


def _dedupe(items: list[tuple[Any, dict]]) -> list[tuple[Any, dict]]:
    """The page and its main frame report the same elements twice."""
    seen: set[tuple] = set()
    unique = []
    for root, record in items:
        key = (record.get("id"), record.get("text"), record.get("x"), record.get("y"))
        if key in seen:
            continue
        seen.add(key)
        unique.append((root, record))
    return unique


def visible_text(page) -> list[tuple[Any, dict]]:
    """Every visible label on screen, paired with the root that renders it."""
    items = []
    for root, records in _evaluate_everywhere(page, _VISIBLE_TEXT_JS):
        for record in records:
            items.append((root, record))
    return _dedupe(items)


def favorite_tree_rows(page) -> list[tuple[Any, dict]]:
    """Visible Favorite rows from the Setting popup grid only."""
    items = []
    for root, records in _evaluate_everywhere(page, _FAVORITE_TREE_ROWS_JS):
        for record in records:
            items.append((root, record))
    return _dedupe(items)


def bookmark_dataset_rows(page) -> list[dict] | None:
    """Return ``gds_bookmark`` rows, or ``None`` when it is unavailable.

    Frames can expose the same application dataset more than once. The first
    available copy is authoritative because each copy represents the same
    Nexacro application, not another bookmark scope.
    """
    available_empty = False
    for root in _roots(page):
        try:
            value = root.evaluate(_BOOKMARK_DATASET_JS, list(BOOKMARK_DATASET_COLUMNS))
        except Exception:
            continue
        if isinstance(value, dict) and value.get("available") is True:
            rows = value.get("rows")
            cleaned = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            if cleaned:
                return cleaned
            available_empty = True
    return [] if available_empty else None


def _scope_tab(value: Any) -> str:
    normalized = _clean_name(value).casefold()
    for tab in SCOPE_TABS:
        if normalized == tab.casefold():
            return tab
    return _clean_name(value).title() or "Public"


def _append_distinct(parts: list[str], value: Any) -> None:
    cleaned = _clean_name(value)
    if cleaned and (not parts or parts[-1].casefold() != cleaned.casefold()):
        parts.append(cleaned)


def bookmark_dataset_entries(page) -> list[dict] | None:
    """Convert the active Nexacro bookmark dataset into catalog tree leaves."""
    rows = bookmark_dataset_rows(page)
    if rows is None:
        return None
    entries = []
    for row in rows:
        name = _clean_name(row.get("userreportname"))
        if not name or _normalize_label(name) in TREE_NOISE:
            continue
        scope = _clean_name(row.get("scope"))
        folder_path: list[str] = []
        _append_distinct(folder_path, BOOKMARK_SCOPE_NAMES.get(scope.upper(), scope))
        _append_distinct(folder_path, row.get("menugroupname"))
        _append_distinct(folder_path, row.get("menuname"))
        entries.append({
            "name": name,
            "folder_path": folder_path,
            "tab": _scope_tab(row.get("publicscope")),
            "bookmark_id": _clean_name(row.get("userreportid")),
            "menu_id": _clean_name(row.get("menuid")),
            "scope": scope,
            "owner_id": _clean_name(row.get("userid")),
            "origin_owner_id": _clean_name(row.get("originuserid")),
            "source": "gds_bookmark",
        })
    return entries


def wait_for_bookmark_dataset(page, timeout_ms: int = BOOKMARK_DATASET_READY_TIMEOUT_MS) -> list[dict] | None:
    """Wait for Nexacro to populate gds_bookmark after the portal shell loads."""
    last_value: list[dict] | None = None
    poll_count = max(1, timeout_ms // IDLE_POLL_INTERVAL_MS)
    for _poll in range(poll_count):
        last_value = bookmark_dataset_entries(page)
        if last_value:
            return last_value
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    return last_value


def _is_icon_chrome(element_id: str) -> bool:
    lowered = str(element_id or "").casefold()
    return any(marker in lowered for marker in ICON_CHROME_MARKERS)


def icon_controls(page, *, include_chrome: bool = True) -> list[tuple[Any, dict]]:
    """Visible controls with no text, such as the Setting gear.

    ``include_chrome=False`` drops scrollbar arrows and static decoration,
    which are text-less and in the top bar but never worth clicking.
    """
    items = []
    for root, records in _evaluate_everywhere(page, _ICON_CONTROLS_JS):
        for record in records:
            if not include_chrome and _is_icon_chrome(record.get("id")):
                continue
            items.append((root, record))
    return _dedupe(items)


def top_bar_report(page) -> str:
    """A short, screenshot-sized dump of the bar that holds the Setting gear.

    The full inventory runs to a hundred lines and gets truncated on screen
    before reaching the part that matters. When the gear is what went missing,
    report the gear's neighbourhood and nothing else.
    """
    icons = [
        (record.get("id"), record.get("x"), record.get("y"))
        for _root, record in icon_controls(page)
        if record.get("y", 0) <= TOP_BAR_MAX_Y
    ]
    icons.sort(key=lambda item: -(item[1] or 0))
    labels = [
        (record.get("text"), record.get("x"))
        for _root, record in visible_text(page)
        if record.get("y", 0) <= TOP_BAR_MAX_Y and len(record.get("text") or "") < 30
    ]
    labels.sort(key=lambda item: -(item[1] or 0))
    icon_text = " | ".join(f"{identifier} @({x},{y})" for identifier, x, y in icons[:30]) or "NONE FOUND"
    label_text = " | ".join(f"{text!r}@{x}" for text, x in labels[:15]) or "none"
    return (
        f"Top-bar icon controls ({len(icons)}): {icon_text}. "
        f"Top-bar labels: {label_text}."
    )


def screen_inventory(page, *, keyword: str | None = None) -> str:
    """A compact dump of what is actually on screen, for failure messages.

    Selector work against an undocumented Nexacro screen is guesswork until
    something reports the real ids. Every failure in this module carries this
    so one test run is enough to write the exact selector - which is why it
    reports icon-only controls too: the first version listed labelled elements
    only, and the control it most needed to reveal has no label.
    """
    lines = []
    for _root, record in visible_text(page):
        text = record.get("text") or ""
        if keyword and keyword.casefold() not in text.casefold():
            continue
        identifier = record.get("id") or "(no id)"
        lines.append(f"{text[:60]!r} @({record.get('x')},{record.get('y')}) id={identifier}")
        if len(lines) >= MAX_INVENTORY_ITEMS:
            lines.append("... (truncated)")
            break
    icons = [
        f"[icon] @({record.get('x')},{record.get('y')}) id={record.get('id')}"
        for _root, record in icon_controls(page)
        if not keyword
    ]
    if icons:
        lines.append("ICON CONTROLS: " + " | ".join(icons[:MAX_INVENTORY_ITEMS]))
    return " | ".join(lines) or "nothing visible"


# ── Page primitives ──


def unlock_wait_window(page) -> bool:
    """Force the Nexacro wait overlay out of the way.

    The overlay owns every mouse event while it is up. Hiding it is safe: it is
    a progress indicator, not a guard, and the backend call it represents keeps
    running either way.
    """
    hidden = False
    for _root, value in _evaluate_everywhere(page, _HIDE_WAIT_WINDOW_JS, WAIT_WINDOW_ID):
        hidden = hidden or bool(value)
    return hidden


def wait_window_visible(page) -> bool:
    return any(
        bool(value)
        for _root, value in _evaluate_everywhere(page, _WAIT_WINDOW_VISIBLE_JS, WAIT_WINDOW_ID)
    )


def popup_records(page) -> list[tuple[Any, dict]]:
    """Visible transient popup containers and their observed close controls."""
    items = []
    seen: set[tuple] = set()
    for root, records in _evaluate_everywhere(page, _POPUP_RECORDS_JS):
        for record in records:
            if not isinstance(record, dict):
                continue
            key = (
                record.get("container_id"), record.get("x"), record.get("y"),
                record.get("w"), record.get("h"),
            )
            if key in seen:
                continue
            seen.add(key)
            items.append((root, record))
    return items


def _safe_popup_closers(record: dict) -> list[dict]:
    closers = []
    seen: set[str] = set()
    for closer in record.get("closers") or []:
        if not isinstance(closer, dict):
            continue
        component_ids = _component_element_ids(closer.get("id"))
        if not component_ids:
            continue
        component_id = component_ids[0]
        if component_id in seen or is_forbidden(closer.get("id"), closer.get("text")):
            continue
        seen.add(component_id)
        closers.append(closer)
    return closers


def _click_popup_closer(root, closer: dict) -> None:
    """Try the owning DOM component before its rendered caption child."""
    for element_id in _component_element_ids(closer.get("id")):
        if is_forbidden(element_id, closer.get("text")):
            continue
        try:
            root.locator(f"[id='{_css_escape(element_id)}']").first.click(
                force=True, timeout=5_000,
            )
            return
        except Exception:
            # Auto-vanishing notices routinely disappear between inventory and
            # click. Verification below decides whether anything remains.
            continue


def dismiss_popups(page) -> list[dict]:
    """Dismiss observed popups and briefly verify only when one was present.

    Popup cleanup remains best-effort. Callers decide whether a surviving
    popup matters by comparing it with the control they are about to click.
    """
    pending = popup_records(page)
    if not pending:
        return []

    dom_attempted: set[tuple[str, str]] = set()
    native_attempted: set[tuple[str, str]] = set()
    poll_count = max(1, POPUP_VERIFY_TIMEOUT_MS // POPUP_VERIFY_INTERVAL_MS)
    for _poll in range(poll_count):
        for root, record in pending:
            container_id = str(record.get("container_id") or "")
            closers = _safe_popup_closers(record)
            closer = next((item for item in closers if (
                container_id, _component_element_ids(item.get("id"))[0]
            ) not in native_attempted), None)
            if closer is None:
                continue
            component_id = _component_element_ids(closer.get("id"))[0]
            key = (container_id, component_id)
            if key not in dom_attempted:
                _click_popup_closer(root, closer)
                dom_attempted.add(key)
            elif key not in native_attempted:
                _native_click(page, closer.get("id"))
                native_attempted.add(key)

        page.wait_for_timeout(POPUP_VERIFY_INTERVAL_MS)
        pending = popup_records(page)
        if not pending:
            return []
    return [record for _root, record in pending]


def _target_rect(page, target: dict | str | None) -> dict | None:
    if not target:
        return None
    record = {"id": target} if isinstance(target, str) else dict(target)
    try:
        if float(record.get("w") or 0) > 0 and float(record.get("h") or 0) > 0:
            return {
                "id": record.get("id") or record.get("element_id") or record.get("text") or "",
                "x": float(record.get("x") or 0), "y": float(record.get("y") or 0),
                "w": float(record["w"]), "h": float(record["h"]),
            }
    except (TypeError, ValueError):
        pass
    element_id = record.get("id") or record.get("element_id")
    target_ids = _component_element_ids(element_id)
    if not target_ids:
        return None
    for _root, value in _evaluate_everywhere(page, _ELEMENT_RECT_JS, target_ids):
        if isinstance(value, dict):
            return value
    return None


def _rects_overlap(first: dict, second: dict) -> bool:
    try:
        return (
            float(first["x"]) < float(second["x"]) + float(second["w"])
            and float(first["x"]) + float(first["w"]) > float(second["x"])
            and float(first["y"]) < float(second["y"]) + float(second["h"])
            and float(first["y"]) + float(first["h"]) > float(second["y"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _popup_description(record: dict) -> str:
    rect = f"({record.get('x')},{record.get('y')},{record.get('w')},{record.get('h')})"
    close_ids = [str(item.get("id") or "") for item in record.get("closers") or []]
    return (
        f"container={record.get('container_id') or '(unknown)'} rect={rect} "
        f"close={','.join(close_ids) or '(none)'}"
    )


def clear_screen(page, target: dict | str | None = None) -> list[dict]:
    """Clear click blockers and reject only proven target obstruction."""
    unlock_wait_window(page)
    remaining = dismiss_popups(page)
    unlock_wait_window(page)
    if not remaining or not target:
        return remaining
    target_rect = _target_rect(page, target)
    if target_rect:
        blockers = [record for record in remaining if _rects_overlap(record, target_rect)]
        if blockers:
            target_id = target_rect.get("id") or (
                target if isinstance(target, str) else (target or {}).get("id")
            )
            details = "; ".join(_popup_description(record) for record in blockers)
            raise RuntimeError(
                f"GSCM popup blocked control {target_id!r} at "
                f"({target_rect.get('x')},{target_rect.get('y')},"
                f"{target_rect.get('w')},{target_rect.get('h')}). {details}. "
                "On screen: " + screen_inventory(page)
            )
    return remaining


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _component_element_ids(element_id: str | None) -> list[str]:
    """Return the Nexacro component id before its rendered caption child.

    Live controls commonly expose both ``btn_name`` and
    ``btn_name:icontext``.  Playwright can successfully click the latter
    without Nexacro firing the Button or Tab handler, so always try the parent
    component first while retaining the observed child as a compatibility
    fallback.
    """
    observed = str(element_id or "").strip()
    if not observed:
        return []
    component = observed.split(":", 1)[0]
    return list(dict.fromkeys([component, observed]))


def _native_click(page, element_id: str | None) -> bool:
    """Fire one already-observed Nexacro component through its own event API."""
    component_ids = _component_element_ids(element_id)
    if not component_ids:
        return False
    component_id = component_ids[0]
    if is_forbidden(component_id):
        return False
    for root in _roots(page):
        try:
            result = root.evaluate(_NATIVE_COMPONENT_CLICK_JS, component_id)
        except Exception:
            continue
        if isinstance(result, dict) and result.get("fired") is True:
            return True
    return False


def _click_record(page, root, record: dict, *, timeout_ms: int) -> str | None:
    """Click a visible record's owning component, then its caption fallback."""
    clear_screen(page, target=record)
    for element_id in _component_element_ids(record.get("id")):
        try:
            root.locator(f"[id='{_css_escape(element_id)}']").first.click(
                force=True, timeout=timeout_ms,
            )
            return element_id
        except Exception:
            continue
    if record.get("text"):
        try:
            root.locator(f"text={record['text']}").first.click(
                force=True, timeout=timeout_ms,
            )
            return str(record.get("id") or "") or None
        except Exception:
            pass
    return None


def _clean_name(value: Any) -> str:
    name = _UNSAFE_NAME_RE.sub(" ", str(value or "")).strip()
    return re.sub(r"\s+", " ", name)[:200]


def _normalize_label(value: Any) -> str:
    return re.sub(r"[\s»>]+", " ", str(value or "")).strip().casefold()


def is_forbidden(*values) -> bool:
    """True when any of ``values`` names a control we must never click."""
    for value in values:
        lowered = str(value or "").casefold()
        if any(word in lowered for word in FORBIDDEN_CLICK_WORDS):
            return True
    return False


def find_by_label(page, labels) -> list[tuple[Any, dict]]:
    """Visible elements whose whole text is one of ``labels``.

    Smallest first: a tab's own label is a tighter box than the panel that
    contains it, and clicking the tight box is what selects the tab.
    """
    wanted = {_normalize_label(label) for label in labels}
    matches = [
        (root, record) for root, record in visible_text(page)
        if _normalize_label(record.get("text")) in wanted
        and not is_forbidden(record.get("id"))
    ]
    matches.sort(key=lambda item: item[1].get("w", 0) * item[1].get("h", 0))
    return matches


def click_label(
    page, labels, *, timeout_ms: int = 30_000, prefer_id_fragment: str | None = None,
) -> dict | None:
    """Click the tightest visible control carrying one of ``labels``."""
    if is_forbidden(*labels):
        raise RuntimeError(f"Refusing to click a control named {labels!r} in GSCM.")
    clear_screen(page)
    matches = find_by_label(page, labels)
    if prefer_id_fragment:
        matches.sort(key=lambda item: (
            0 if prefer_id_fragment in str(item[1].get("id") or "") else 1,
            item[1].get("w", 0) * item[1].get("h", 0),
        ))
    for root, record in matches:
        clicked_id = _click_record(page, root, record, timeout_ms=timeout_ms)
        if clicked_id is not None:
            return {**record, "clicked_id": clicked_id}
    return None


def _hint_rank(element_id: str, hints) -> int:
    lowered = str(element_id or "").casefold()
    for index, hint in enumerate(hints):
        if hint in lowered:
            return index
    return len(hints)


def click_by_id_hint(
    page, hints, *, timeout_ms: int = 30_000, exclude_ids: set[str] | None = None,
) -> dict | None:
    """Click an icon-only control located by the shape of its component id.

    Ranked by which hint matched before anything else. Sorting these by screen
    position instead let a later, vaguer hint win on geometry: the top bar's
    profile icon sits further right than the gear, so the search opened a user
    popover and reported the gear missing.
    """
    hints = list(hints)
    excluded = {
        component_id
        for value in (exclude_ids or set())
        for component_id in _component_element_ids(value)[:1]
    }
    clear_screen(page)
    candidates = []
    for root, records in _evaluate_everywhere(page, _ID_MATCH_JS, hints):
        for record in records:
            candidates.append((root, record))
    # A toolbar gear sits at the top of the screen; prefer the topmost match.
    candidates = [
        item for item in candidates
        if not is_forbidden(item[1].get("id"))
        and _component_element_ids(item[1].get("id"))[:1] != []
        and _component_element_ids(item[1].get("id"))[0] not in excluded
    ]
    candidates.sort(key=lambda item: (
        _hint_rank(item[1].get("id"), hints), item[1].get("y", 0), -item[1].get("x", 0),
    ))
    for root, record in candidates:
        clicked_id = _click_record(page, root, record, timeout_ms=timeout_ms)
        if clicked_id is not None:
            return {**record, "clicked_id": clicked_id}
    return None


def portal_url(job: dict) -> str:
    """The address that renders the GSCM portal."""
    site = job.get("site") or {}
    report = job.get("report") or {}
    for candidate in (report.get("url"), site.get("auth_url"), site.get("base_url")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    raise RuntimeError("GSCM has no portal URL. Set the website's base or authentication URL.")


def _host(url: str) -> str:
    match = re.match(r"^https?://([^/]+)", str(url or "").strip(), re.IGNORECASE)
    return match.group(1).casefold() if match else ""


def open_portal(page, url: str, *, timeout_ms: int = PORTAL_READY_TIMEOUT_MS) -> None:
    """Land on the GSCM portal, reusing an already-open tab.

    Re-navigating a live Nexacro session tears down the whole component tree
    and replays the SSO handshake, so only navigate when the page is not
    already inside the portal.
    """
    host = _host(url)
    if not host or host not in _host(page.url or ""):
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    wait_for_component(page, "mainframe.VFrameSet", timeout_ms=timeout_ms)
    clear_screen(page)


def reload_portal(page, job: dict, *, timeout_ms: int = PORTAL_READY_TIMEOUT_MS) -> None:
    """Force a fresh Nexacro component tree before retrying a failed export.

    ``open_portal`` intentionally reuses a healthy same-host session.  That is
    wrong after a failed bookmark activation: the stale Setting popup and its
    empty virtual grid survive into the next worker attempt.  A real retry must
    reload the shell while keeping the same authenticated browser profile.
    """
    page.goto(portal_url(job), wait_until="domcontentloaded", timeout=timeout_ms)
    wait_for_component(page, "mainframe.VFrameSet", timeout_ms=timeout_ms)
    clear_screen(page)


def on_login_page(page) -> bool:
    """True when the browser is parked on the SSO sign-in form."""
    labels = {_normalize_label(record.get("text")) for _root, record in visible_text(page)}
    if sum(1 for marker in LOGIN_PAGE_MARKERS if marker in labels) >= 2:
        return True
    identifiers = {
        str(record.get("id") or "").casefold()
        for _root, record in [*visible_text(page), *icon_controls(page)]
    }
    return sum(1 for marker in LOGIN_PAGE_ELEMENT_IDS if marker in identifiers) >= 2


def _not_signed_in_error() -> RuntimeError:
    return RuntimeError(
        "GSCM is not signed in: the automation browser is on the Samsung SSO "
        "login page. ASAP and GSCM are separate portals with separate sessions, "
        "so an ASAP scan can succeed while this one cannot. Sign the profile in "
        "to GSCM once, in a visible window, with:  python app\\flow_worker.py "
        "--profile-dir <profile> --authenticate-url "
        "https://mdscm.sec.samsung.net/nexa/index.html --authenticate-adapter "
        "gscm_portal   - or re-run setup.ps1, which now bootstraps every portal."
    )


def wait_for_component(page, component_id: str, *, timeout_ms: int) -> None:
    """Wait for one Nexacro component to exist in any frame.

    Nexacro compiles its tree after the document is ready, so a loaded page
    proves nothing about the application being up.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    checked_login = False
    while time.monotonic() < deadline:
        if _evaluate_everywhere(page, "(id) => !!document.getElementById(id)", component_id):
            return
        if not checked_login:
            # Give SSO a moment to redirect before judging, then stop waiting
            # three minutes for a client that will never load behind a form.
            page.wait_for_timeout(5_000)
            checked_login = True
            if on_login_page(page):
                raise _not_signed_in_error()
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    if on_login_page(page):
        raise _not_signed_in_error()
    raise RuntimeError(
        f"GSCM did not render its Nexacro client within {timeout_ms // 1000} seconds. "
        f"Component not found: {component_id}. On screen: {screen_inventory(page)}"
    )


def wait_for_calculation(
    page, *, timeout_ms: int = BOOKMARK_SETTLE_TIMEOUT_MS, report_progress=None,
) -> bool:
    """Block until the wait overlay has stayed down long enough to trust.

    Returns ``True`` when the screen went idle on its own and ``False`` when
    the budget ran out - the caller still proceeds, because a stuck overlay is
    a known GSCM behavior, not proof that the data is missing.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    idle_polls = 0
    announced = False
    while time.monotonic() < deadline:
        try:
            busy = wait_window_visible(page)
        except Exception:
            busy = False
        if busy:
            idle_polls = 0
            if report_progress and not announced:
                announced = True
                report_progress("GSCM is running the report's query.")
        else:
            idle_polls += 1
            if idle_polls >= IDLE_POLLS_REQUIRED:
                page.wait_for_timeout(POST_IDLE_SETTLE_MS)
                return True
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    unlock_wait_window(page)
    page.wait_for_timeout(POST_IDLE_SETTLE_MS)
    return False


# ── The Setting > Favorite dialog ──


def favorites_dialog_open(page) -> bool:
    """The Favorite panel is up when its component or scope tabs are visible."""
    if _component_visible(page, "TopFrame.Setting1.form.div_favorite"):
        return True
    visible = {_normalize_label(record.get("text")) for _root, record in visible_text(page)}
    return any(_normalize_label(tab) in visible for tab in SCOPE_TABS)


def _wait_for_dialog_state(page, predicate, *, timeout_ms: int) -> bool:
    """Poll a Nexacro dialog predicate while dispatching its browser events."""
    poll_count = max(1, timeout_ms // IDLE_POLL_INTERVAL_MS)
    for _poll in range(poll_count):
        if predicate(page):
            return True
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    return bool(predicate(page))


def open_favorites_dialog(page, report_progress=None) -> None:
    """Open Setting and select its Favorite panel.

    The gear that opens Setting carries no text, so it is found by id shape;
    everything after it is clicked by its visible label.
    """
    clear_screen(page)
    if favorites_dialog_open(page):
        return

    if report_progress:
        report_progress("Opening GSCM Setting > Favorite.")
    if _open_setting(page) and _reach_favorite_panel(page):
        return
    raise RuntimeError(
        "GSCM's Setting > Favorite dialog did not open, so its bookmark tabs "
        "(Private, Public, Custom) were never reachable. The gear that opens it "
        "carries no text, so it is found by id shape or by position in the top "
        "bar - neither worked here. " + top_bar_report(page)
    )


def _reach_favorite_panel(page) -> bool:
    """Select the Favorite panel and confirm its scope tabs rendered."""
    record = click_label(
        page, [FAVORITE_PANEL_LABEL], prefer_id_fragment="TopFrame.Setting1",
    )
    if record is None:
        return False
    clear_screen(page)
    if _wait_for_dialog_state(
        page, favorites_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
    ):
        return True
    # A Nexacro caption child can accept the browser click without firing the
    # panel component.  Only after the full render budget expires do we invoke
    # that exact observed component natively, avoiding a premature double-click.
    if _native_click(page, record.get("clicked_id") or record.get("id")):
        clear_screen(page)
        return _wait_for_dialog_state(
            page, favorites_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
        )
    return False


def _activate_setting_record(
    page, root, record: dict, tried_ids: set[str], *, ready_timeout_ms: int,
) -> bool:
    """Try one unique gear candidate and verify that Setting really opened."""
    component_ids = _component_element_ids(record.get("id"))
    if not component_ids:
        return False
    component_id = component_ids[0]
    if component_id in tried_ids or is_forbidden(component_id):
        return False
    tried_ids.add(component_id)
    clicked_id = _click_record(page, root, record, timeout_ms=15_000)
    if clicked_id is None:
        return False
    if _wait_for_dialog_state(
        page, _setting_dialog_open, timeout_ms=ready_timeout_ms,
    ):
        return True
    if _native_click(page, clicked_id):
        return _wait_for_dialog_state(
            page, _setting_dialog_open, timeout_ms=ready_timeout_ms,
        )
    return False


def _open_setting(page) -> bool:
    """Open the Setting dialog, whose gear carries no text.

    Try the id shapes first. When none matches - which is what happened
    against the live portal - fall back to position: the gear sits in the top
    bar to the right of the business pills. Candidates are tried right to left
    and each is checked, so a wrong guess costs one harmless icon click rather
    than a failed scan. Nothing on the forbidden list is ever a candidate.
    """
    tried_ids: set[str] = set()
    for root in _roots(page):
        if _activate_setting_record(
            page, root, {"id": SETTING_BUTTON_ID}, tried_ids,
            ready_timeout_ms=DIALOG_READY_TIMEOUT_MS,
        ):
            return True
    hinted = click_by_id_hint(
        page, SETTING_BUTTON_HINTS, exclude_ids=tried_ids,
    )
    if hinted is not None:
        hinted_id = _component_element_ids(
            hinted.get("clicked_id") or hinted.get("id"),
        )[0]
        tried_ids.add(hinted_id)
        if _wait_for_dialog_state(
            page, _setting_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
        ):
            return True
        if _native_click(page, hinted_id) and _wait_for_dialog_state(
            page, _setting_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
        ):
            return True
    labelled = click_label(page, ["Setting", "Settings"])
    if labelled is not None:
        labelled_id = labelled.get("clicked_id") or labelled.get("id")
        if _wait_for_dialog_state(
            page, _setting_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
        ):
            return True
        if _native_click(page, labelled_id) and _wait_for_dialog_state(
            page, _setting_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
        ):
            return True

    # Right to left across the whole bar: the gear sits past the business
    # pills, but their width varies with the signed-in user's brands, so an
    # absolute cut-off would miss it on some accounts. Ordering by x is what
    # matters; the cut-off only decides where to start.
    candidates = [
        (root, record) for root, record in icon_controls(page, include_chrome=False)
        if record.get("y", 0) <= TOP_BAR_MAX_Y
        and not is_forbidden(record.get("id"))
    ]
    candidates.sort(key=lambda item: (
        0 if item[1].get("x", 0) >= TOP_BAR_MIN_X else 1, -item[1].get("x", 0),
    ))
    for root, record in candidates[:MAX_GEAR_TRIES]:
        if _component_element_ids(record.get("id"))[:1] and (
            _component_element_ids(record.get("id"))[0] in tried_ids
        ):
            continue
        if _activate_setting_record(
            page, root, record, tried_ids, ready_timeout_ms=TAB_SETTLE_MS,
        ):
            return True
        _dismiss_stray_panel(page)
    return False


def _setting_dialog_open(page) -> bool:
    """The Setting dialog is up when its left rail is on screen."""
    if _component_visible(page, "TopFrame.Setting1"):
        return True
    visible = {_normalize_label(record.get("text")) for _root, record in visible_text(page)}
    rail = {"favorite", "layout", "dashboard", "installation"}
    return len(rail & visible) >= 2 or favorites_dialog_open(page)


def _dismiss_stray_panel(page) -> None:
    """Close whatever a wrong icon opened, without touching stored data."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    clear_screen(page)


def select_scope_tab(page, tab: str, *, require_rows: bool = False) -> bool:
    """Switch scope and optionally prove its virtual grid actually rebound."""
    record = click_label(
        page, [tab], prefer_id_fragment="TopFrame.Setting1",
    )
    if record is None:
        return False
    page.wait_for_timeout(TAB_SETTLE_MS)
    clear_screen(page)
    native_fired = False
    if ":" in str(record.get("id") or ""):
        # The observed label is a rendered child (``:text``/``:icontext``),
        # which is precisely the live Nexacro shape that can swallow the DOM
        # click. Re-firing its parent tab is idempotent and forces the rebind.
        native_fired = _native_click(
            page, record.get("clicked_id") or record.get("id"),
        )
        if native_fired:
            page.wait_for_timeout(TAB_SETTLE_MS)
            clear_screen(page)
    if not require_rows or wait_for_favorite_rows(page):
        return True
    # The caption click itself is not proof that Nexacro dispatched the Tab
    # component's event.  Fire the exact observed component natively, then
    # require the target bookmark grid to populate before claiming success.
    if native_fired or not _native_click(
        page, record.get("clicked_id") or record.get("id"),
    ):
        return False
    page.wait_for_timeout(TAB_SETTLE_MS)
    clear_screen(page)
    return wait_for_favorite_rows(page)


def wait_for_favorite_rows(page, *, timeout_ms: int = FAVORITE_ROWS_TIMEOUT_MS) -> bool:
    """Wait for Nexacro to populate the selected Favorite scope.

    The tab caption changes before the virtual bookmark grid finishes
    rebinding. Treating that intermediate empty grid as the final catalog made
    a real Public bookmark look deleted, especially on a retry immediately
    after an export.
    """
    poll_count = max(1, timeout_ms // IDLE_POLL_INTERVAL_MS)
    for _poll in range(poll_count):
        if favorite_tree_rows(page):
            return True
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    return bool(favorite_tree_rows(page))


def read_favorite_tree(page, seed: list[tuple[int, str]] | None = None) -> list[dict]:
    """Rebuild the visible rows in the Setting popup's Favorite grid.

    The tree is a Nexacro grid with no semantic nesting in the DOM: depth is
    expressed purely as horizontal indentation. Rows are therefore ordered by
    their vertical position and nested by comparing left edges, which is how
    the screen itself communicates the hierarchy.

    ``seed`` carries the folder stack that was open at the top of the view.
    Once the tree has been scrolled, a row's parents are off-screen, and
    rebuilding the stack from the visible rows alone would file it under the
    wrong folder - or under none.
    """
    rows = []
    for root, record in favorite_tree_rows(page):
        name = _clean_name(record.get("text"))
        if not name or _normalize_label(name) in TREE_NOISE:
            continue
        rows.append({
            "root": root,
            "id": record.get("id") or "",
            "name": name,
            "x": record.get("x", 0),
            "y": record.get("y", 0),
            "w": record.get("w", 0),
            "is_folder": record.get("is_folder"),
        })
    if not rows:
        return []

    rows.sort(key=lambda row: (row["y"], row["x"]))
    stack: list[tuple[int, str]] = list(seed or [])
    entries = []
    for row in rows:
        while stack and row["x"] <= stack[-1][0] + INDENT_TOLERANCE_PX:
            stack.pop()
        folder_path = [name for _x, name in stack]
        entries.append({
            "name": row["name"],
            "folder_path": folder_path,
            "element_id": row["id"],
            "indent": row["x"],
            "y": row["y"],
            "root": row["root"],
            "is_folder": row.get("is_folder"),
            "stack": [*stack, (row["x"], row["name"])],
        })
        stack.append((row["x"], row["name"]))
    return entries


def scroll_tree(page) -> bool:
    """Page the Favorite grid down, including Nexacro virtual scrollbars.

    Setting ``scrollTop`` is sufficient in some builds. The production build
    keeps its virtual row position inside the Nexacro Grid component, whose
    own increment button is the reliable live control. Move eight rows per
    pass so adjacent pages retain one rendered row of overlap. Keyboard and
    DOM scrolling remain fallbacks for older builds. Compare rendered rows so
    a cosmetic scroll is never mistaken for actual paging.
    """
    before = _tree_row_signature(page)
    for root in _roots(page):
        try:
            increment = root.locator(
                f"[id*='{FAVORITE_GRID_ID_SUFFIX}.vscrollbar.incbutton:icontext']"
            ).first
            if not increment.count():
                increment = root.locator(
                    f"[id*='{FAVORITE_GRID_ID_SUFFIX}.vscrollbar.incbutton']"
                ).first
            if not increment.count():
                continue
            for _step in range(FAVORITE_SCROLL_PAGE_STEPS):
                increment.click(force=True, timeout=15_000)
                page.wait_for_timeout(40)
            page.wait_for_timeout(300)
            if _tree_row_signature(page) != before:
                return True
        except Exception:
            continue
    for root in _roots(page):
        try:
            grid = root.locator(f"[id$='{FAVORITE_GRID_ID_SUFFIX}']").first
            if not grid.count():
                continue
            grid.hover(timeout=15_000)
            page.mouse.wheel(0, 720)
            page.wait_for_timeout(300)
            if _tree_row_signature(page) != before:
                return True
        except Exception:
            continue
    for root in _roots(page):
        try:
            grid = root.locator(f"[id$='{FAVORITE_GRID_ID_SUFFIX}']").first
            if not grid.count():
                continue
            grid.click(force=True, timeout=15_000)
            page.keyboard.press("PageDown")
            page.wait_for_timeout(300)
            if _tree_row_signature(page) != before:
                return True
        except Exception:
            continue
    for _root, value in _evaluate_everywhere(page, _SCROLL_TREE_JS):
        if isinstance(value, dict) and value.get("moved"):
            page.wait_for_timeout(250)
            if _tree_row_signature(page) != before:
                return True
    return False


def reset_tree(page) -> bool:
    """Return the virtualized Favorite grid to its first visible row.

    Do not trust ``Control+Home`` alone. After one export attempt the live
    Nexacro component can retain its internal row position while the HTML grid
    accepts keyboard focus, making the next retry start near the bottom. Walk
    the native decrement button upward until a full page produces no rendered
    row change, which proves that the first row has been reached.
    """
    found_native_scrollbar = False
    for root in _roots(page):
        try:
            decrement = root.locator(
                f"[id*='{FAVORITE_GRID_ID_SUFFIX}.vscrollbar.decbutton:icontext']"
            ).first
            if not decrement.count():
                decrement = root.locator(
                    f"[id*='{FAVORITE_GRID_ID_SUFFIX}.vscrollbar.decbutton']"
                ).first
            if not decrement.count():
                continue
            found_native_scrollbar = True
            previous = _tree_row_signature(page)
            for _pass in range(FAVORITE_SCROLL_RESET_PASSES):
                for _step in range(FAVORITE_SCROLL_PAGE_STEPS):
                    decrement.click(force=True, timeout=15_000)
                    page.wait_for_timeout(20)
                page.wait_for_timeout(200)
                current = _tree_row_signature(page)
                if current == previous:
                    return True
                previous = current
        except Exception:
            continue
    if found_native_scrollbar:
        return False
    for root in _roots(page):
        try:
            grid = root.locator(f"[id$='{FAVORITE_GRID_ID_SUFFIX}']").first
            if not grid.count():
                continue
            grid.click(force=True, timeout=15_000)
            page.keyboard.press("Control+Home")
            page.wait_for_timeout(250)
            return True
        except Exception:
            continue
    for _root, value in _evaluate_everywhere(page, _RESET_TREE_JS):
        if isinstance(value, dict) and value.get("available"):
            return True
    return False


def _tree_row_signature(page) -> tuple[tuple[str, str, int], ...]:
    """The rendered virtual rows, used to prove that paging changed data."""
    return tuple(
        (str(record.get("id") or ""), str(record.get("text") or ""), int(record.get("y") or 0))
        for _root, record in favorite_tree_rows(page)
    )


def _find_tree_entry(
    page, name: str, folder_path: list[str], *, max_passes: int = 40,
) -> dict | None:
    """Find one exact tree row while it is still rendered and clickable."""
    reset_tree(page)
    seed: list[tuple[int, str]] = []
    expected_path = [part.casefold() for part in folder_path]
    for page_index in range(max_passes):
        entries = read_favorite_tree(page, seed if page_index else None)
        for entry in entries:
            if entry["name"].casefold() != name.casefold():
                continue
            if [part.casefold() for part in entry["folder_path"]] == expected_path:
                return entry
        seed = list(entries[-1]["stack"]) if entries else seed
        if not scroll_tree(page):
            break
        page.wait_for_timeout(250)
    return None


def _reveal_tree_path(page, folder_path: list[str], leaf_name: str) -> bool:
    """Expand only the catalogued folders needed to reveal one bookmark.

    Nexacro virtualizes the grid, so element ids collected on an earlier page
    can point at a different row after scrolling. Each folder is therefore
    found again immediately before it is clicked.
    """
    parents: list[str] = []
    for index, folder_name in enumerate(folder_path):
        folder = _find_tree_entry(page, folder_name, parents)
        if not folder:
            return False
        wanted_name = folder_path[index + 1] if index + 1 < len(folder_path) else leaf_name
        wanted_path = [*parents, folder_name]
        if _find_tree_entry(page, wanted_name, wanted_path):
            parents.append(folder_name)
            continue
        folder = _find_tree_entry(page, folder_name, parents)
        if not folder:
            return False
        _click_entry(page, folder)
        page.wait_for_timeout(TAB_SETTLE_MS)
        if not _find_tree_entry(page, wanted_name, wanted_path):
            return False
        parents.append(folder_name)
    return bool(_find_tree_entry(page, leaf_name, folder_path))


def collect_favorite_tree(page, report_progress=None, *, max_passes: int = 40) -> list[dict]:
    """Every row in the current tab, scrolling and expanding as needed.

    Two things hide rows. The grid virtualizes, so only what is in view exists
    in the DOM; and folders start collapsed, so their children are not rendered
    at all. This scrolls to the bottom collecting rows, then clicks rows whose
    visible ``treeitembutton`` identifies them as folders and repeats until
    nothing new appears.

    Clicking a tree row only selects or expands it - the report itself opens on
    ``Go >>`` - so an unnecessary click costs nothing.
    """
    collected: dict[tuple, dict] = {}
    seed: list[tuple[int, str]] = []

    def absorb(*, carry: bool = False) -> int:
        """Read the visible rows once.

        ``carry`` continues the previous screenful's folder stack, which is
        only correct while scrolling down through one continuous pass. A read
        that starts again from the top of the tree must not carry it, or every
        row is filed one level deeper than it belongs and reappears as a
        duplicate entry.
        """
        nonlocal seed
        added = 0
        entries = read_favorite_tree(page, seed if carry else None)
        for entry in entries:
            key = (tuple(entry["folder_path"]), entry["name"].casefold())
            if key not in collected:
                collected[key] = entry
                added += 1
        seed = list(entries[-1]["stack"]) if entries else []
        return added

    absorb()
    for _pass in range(max_passes):
        if not scroll_tree(page):
            break
        page.wait_for_timeout(400)
        absorb(carry=True)

    expanded: set[tuple] = set()
    for _round in range(max_passes):
        folders = {
            (tuple(entry["folder_path"]), entry["name"].casefold())
            for entry in _folder_entries(list(collected.values()))
        }
        candidates = [
            entry for key, entry in collected.items()
            if key in folders and key not in expanded
            and not is_forbidden(entry.get("element_id"))
        ]
        if not candidates:
            break
        opened_any = False
        for entry in candidates:
            key = (tuple(entry["folder_path"]), entry["name"].casefold())
            expanded.add(key)
            try:
                _click_entry(page, entry)
            except Exception:
                continue
            page.wait_for_timeout(250)
            if absorb():
                opened_any = True
        if not opened_any:
            break
        if report_progress:
            report_progress(f"Expanded the tree to {len(collected)} row(s).")

    return sorted(collected.values(), key=lambda entry: (entry["folder_path"], entry["y"]))


def _leaf_entries(entries: list[dict]) -> list[dict]:
    """The report rows, excluding the folders that contain them.

    Nexacro renders a visible ``treeitembutton`` for a folder and hides that
    control for a bookmark leaf. Fall back to indentation only when a runtime
    does not expose that control state.
    """
    if any(entry.get("is_folder") is not None for entry in entries):
        return [entry for entry in entries if entry.get("is_folder") is False]
    leaves = []
    for index, entry in enumerate(entries):
        following = entries[index + 1] if index + 1 < len(entries) else None
        if following and following["indent"] > entry["indent"] + INDENT_TOLERANCE_PX:
            continue  # a folder: the next row is indented under it
        leaves.append(entry)
    return leaves


def _folder_entries(entries: list[dict]) -> list[dict]:
    """Rows that are folders, and so may hide children until expanded."""
    leaves = {id(entry) for entry in _leaf_entries(entries)}
    return [entry for entry in entries if id(entry) not in leaves]


# ── Discovery ──


def discovered_report(entry: dict, report_url: str, catalog_name: str | None = None) -> dict:
    """One bookmark row as a Metronome catalog entry.

    GSCM owns the filters, so the entry declares no prompts. That is what makes
    a GSCM flow a one-click download: the bookmark *is* the configuration.
    """
    name = entry["name"]
    catalog_name = catalog_name or name
    tab = entry.get("tab") or "Public"
    return {
        "discovery_key": f"{tab} > {' > '.join([*entry.get('folder_path', []), catalog_name])}",
        "name": catalog_name,
        "report_url": report_url,
        "ready_text": None,
        "download_text": DOWNLOAD_TEXT,
        "automation": {
            "kind": "gscm_favorite",
            "category_path": [tab, *entry.get("folder_path", []), catalog_name],
            "favorite_tab": tab,
            "favorite_name": name,
            "favorite_folder_path": list(entry.get("folder_path", [])),
            "favorite_element_id": entry.get("element_id") or None,
            "favorite_bookmark_id": entry.get("bookmark_id") or None,
            "favorite_menu_id": entry.get("menu_id") or None,
            "favorite_scope": entry.get("scope") or None,
            "favorite_owner_id": entry.get("owner_id") or None,
            "favorite_origin_owner_id": entry.get("origin_owner_id") or None,
            "excel_btn_id": FALLBACK_EXCEL_BUTTON_ID,
        },
        "filters": [],
    }


def discover_catalog(page, job: dict, report_progress) -> tuple[list[dict], bool]:
    """Catalog GSCM bookmarks from memory, with a scoped DOM fallback."""
    url = portal_url(job)
    report_progress("running", {
        "stage": "navigation",
        "message": "Opening the GSCM portal for bookmark discovery.",
    })
    open_portal(page, url)
    dataset_entries = wait_for_bookmark_dataset(page)
    if not dataset_entries:
        # Most GSCM builds expose the application-level dataset as soon as the
        # portal loads. Only depend on the fragile Setting gear when this build
        # has not loaded the dataset yet.
        open_favorites_dialog(page, lambda message: report_progress(
            "running", {"stage": "navigation", "message": message},
        ))
        dataset_entries = bookmark_dataset_entries(page)
    entries: list[dict] = []
    seen: set[tuple] = set()
    if dataset_entries:
        for entry in dataset_entries:
            identity = entry.get("bookmark_id") or (
                entry["tab"], tuple(entry["folder_path"]), entry["name"].casefold(),
            )
            key = ("dataset", identity)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        report_progress("running", {
            "stage": "report_discovery",
            "message": (
                f"Read {len(entries)} bookmark(s) from GSCM's in-memory "
                "gds_bookmark dataset."
            ),
            "item_count": len(entries),
        })
    else:
        fallback_reason = (
            "was empty" if dataset_entries == [] else "was not exposed by this Nexacro runtime"
        )
        report_progress("running", {
            "stage": "report_discovery",
            "message": (
                f"GSCM's gds_bookmark dataset {fallback_reason}; reading only "
                "the Setting Favorite grid as a fallback."
            ),
        })
        for tab in SCOPE_TABS:
            if not select_scope_tab(page, tab):
                report_progress("running", {
                    "stage": "report_discovery",
                    "message": f"GSCM has no {tab} bookmark tab on this screen; skipping it.",
                })
                continue
            leaves = _leaf_entries(collect_favorite_tree(page, lambda message: report_progress(
                "running", {"stage": "report_discovery", "message": f"{tab}: {message}"},
            )))
            added = 0
            for entry in leaves:
                key = (tab, tuple(entry["folder_path"]), entry["name"].casefold())
                if key in seen:
                    continue
                seen.add(key)
                entries.append({**entry, "tab": tab})
                added += 1
            report_progress("running", {
                "stage": "report_discovery",
                "message": f"{tab}: {added} bookmark(s).",
                "item_count": added,
            })

    if not entries:
        raise RuntimeError(
            "GSCM exposed no bookmark rows in gds_bookmark or its Setting > "
            "Favorite tabs (Private, Public, Custom). On screen: "
            + screen_inventory(page)
        )

    discovery = job.get("discovery") or {}
    requested = {
        str(path[-1]).strip().casefold()
        for path in discovery.get("report_paths") or []
        if isinstance(path, list) and path
    }
    complete = not requested
    if requested:
        entries = [item for item in entries if item["name"].casefold() in requested]
        if not entries:
            raise RuntimeError(
                "The requested GSCM bookmark is no longer listed. "
                "Run a full scan to refresh the catalog."
            )

    report_progress("running", {
        "stage": "report_discovery",
        "message": f"Discovered {len(entries)} GSCM bookmark(s).",
        "item_count": len(entries),
    })
    names = _catalog_names(entries)
    return [
        discovered_report(entry, url, catalog_name)
        for entry, catalog_name in zip(entries, names)
    ], complete


def _catalog_names(entries: list[dict]) -> list[str]:
    """Unique catalog names for bookmarks GSCM lets the user save twice.

    ``flow_reports`` is keyed by (site, name), so two rows labelled the same -
    common when the same report is filed under several folders - would collapse
    into one catalog row and quietly drop one of them.
    """
    seen: dict[str, int] = {}
    names = []
    for entry in entries:
        key = entry["name"].casefold()
        seen[key] = seen.get(key, 0) + 1
        names.append(entry["name"] if seen[key] == 1 else f"{entry['name']} ({seen[key]})")
    return names


# ── Download ──


def open_bookmark(page, job: dict, report_progress=None) -> str:
    """Open one bookmark: select its row in the Favorite tree and press Go."""
    automation = (job.get("report") or {}).get("automation") or {}
    name = str(automation.get("favorite_name") or "").strip()
    tab = str(automation.get("favorite_tab") or "Public").strip() or "Public"
    folder_path = [str(part) for part in (automation.get("favorite_folder_path") or [])]
    if not name:
        raise RuntimeError(
            "This GSCM report has no bookmark reference. Scan the GSCM catalog again."
        )

    open_portal(page, portal_url(job))
    open_favorites_dialog(page, report_progress)
    for scope_attempt in range(3):
        if not find_by_label(page, [tab]):
            raise RuntimeError(
                f"GSCM's {tab} bookmark tab was not on screen. On screen: "
                + screen_inventory(page)
            )
        if select_scope_tab(page, tab, require_rows=True):
            break
        # A real refresh changes scope before returning.  Re-clicking the same
        # caption three times leaves Nexacro's empty binding untouched.
        if scope_attempt < 2:
            alternate = next(
                (candidate for candidate in SCOPE_TABS if candidate.casefold() != tab.casefold()),
                None,
            )
            if alternate:
                select_scope_tab(page, alternate)
            if scope_attempt == 1:
                # Flipping scope twice did not force a rebind, so the stalled
                # bookmark request is stuck in this client. Rebuild the whole
                # Nexacro component tree before the final attempt.
                reload_portal(page, job)
                open_favorites_dialog(page, report_progress)
    else:
        raise RuntimeError(
            f"GSCM's {tab} bookmark tab stayed empty after three activation and "
            "rebind attempts. The portal did not finish rendering its bookmark grid."
        )

    entry = _resolve_entry(page, name, folder_path, tab)
    if report_progress:
        report_progress(f"Opening GSCM bookmark {' > '.join([*folder_path, name])}.")
    _click_entry(page, entry)
    if not _click_go_button(page):
        raise RuntimeError(
            "GSCM's Go button was not on screen after selecting the bookmark. "
            "On screen: " + screen_inventory(page)
        )
    wait_for_calculation(page, report_progress=report_progress)
    clear_screen(page)
    return entry.get("element_id") or name


def _click_go_button(page) -> bool:
    """Activate the Favorite dialog's native Go button.

    The live Nexacro build renders the ``Go >>`` caption in a child text node.
    Clicking that node can leave the selected row highlighted without firing
    the Button component. Prefer the stable component id so the report work
    frame actually opens, then retain the caption lookup for older builds.
    """
    clear_screen(page)
    for root in _roots(page):
        try:
            button = root.locator(f"[id='{_css_escape(GO_BUTTON_ID)}']").first
            if not button.count():
                continue
        except Exception:
            continue
        clear_screen(page, target=GO_BUTTON_ID)
        try:
            button.click(force=True, timeout=30_000)
            page.wait_for_timeout(1_000)
            if not favorites_dialog_open(page):
                return True
        except Exception:
            continue
    clicked = click_label(page, GO_LABELS)
    if clicked is None:
        return False
    page.wait_for_timeout(1_000)
    return not favorites_dialog_open(page)


def _resolve_entry(page, name: str, folder_path: list[str], tab: str) -> dict:
    """Find the catalogued row again, preferring an exact folder-path match.

    The same report name appears under several folders, so the path is what
    distinguishes them. Matching on name alone would silently download a
    different report than the flow was built for.
    """
    if folder_path:
        for attempt in range(3):
            if attempt:
                # Re-selecting the scope resets Nexacro's virtual grid after a
                # pass that ended with recycled rows or a retained scroll
                # position. The catalog identity is stable, so retry the exact
                # path before falling back to a full tree inventory.
                select_scope_tab(page, tab)
                wait_for_favorite_rows(page)
            exact = _find_tree_entry(page, name, folder_path)
            if exact and exact.get("is_folder") is not True:
                return exact
            if _reveal_tree_path(page, folder_path, name):
                exact = _find_tree_entry(page, name, folder_path)
                if exact and exact.get("is_folder") is not True:
                    return exact

    leaves = _leaf_entries(collect_favorite_tree(page))
    by_name = [item for item in leaves if item["name"].casefold() == name.casefold()]
    if not by_name:
        available = ", ".join(sorted({item["name"] for item in leaves})[:30]) or "none"
        raise RuntimeError(
            f"GSCM bookmark {name!r} is no longer in the {tab} tab. "
            f"Listed there: {available}. Re-scan the GSCM catalog."
        )
    if folder_path:
        exact = [
            item for item in by_name
            if [part.casefold() for part in item["folder_path"]]
            == [part.casefold() for part in folder_path]
        ]
        if exact:
            return exact[0]
    return by_name[0]


def _click_entry(page, entry: dict) -> None:
    root = entry.get("root")
    clear_screen(page, target={
        "id": entry.get("element_id"), "text": entry.get("name"),
        "x": entry.get("x"), "y": entry.get("y"),
        "w": entry.get("w"), "h": entry.get("h"),
    })
    selector = (
        f"[id='{_css_escape(entry['element_id'])}']" if entry.get("element_id")
        else f"text={entry['name']}"
    )
    target = (root or page).locator(selector).first
    target.click(force=True, timeout=60_000)


def excel_button_id(automation: dict | None = None) -> str:
    configured = str((automation or {}).get("excel_btn_id") or "").strip()
    return configured or FALLBACK_EXCEL_BUTTON_ID


def trigger_excel_export(page, job: dict, *, timeout_ms: int = 60_000) -> None:
    """Wait for and click the active work frame's Excel toolbar button.

    Some bookmarks return the home shell to an idle state before the MDI work
    frame has finished mounting. Waiting only for the global busy overlay can
    therefore race the toolbar and falsely report that the bookmark cannot
    export.
    """
    automation = (job.get("report") or {}).get("automation") or {}
    clear_screen(page)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for root in _roots(page):
            for selector in (
                f"[id='{_css_escape(excel_button_id(automation))}']",
                f"[id*='{EXCEL_BUTTON_COMPONENT}']",
            ):
                try:
                    button = root.locator(selector).first
                    if button.count():
                        button.click(force=True, timeout=60_000)
                        return
                except Exception:
                    continue
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    raise RuntimeError(
        "GSCM's Excel export button was not found on the toolbar. The bookmark "
        "may have opened a screen that cannot export. On screen: "
        + screen_inventory(page)
    )
