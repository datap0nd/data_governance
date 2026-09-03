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

**Where discovery gets its data.** Discovery walks the portal the same way a
flow run does - gear, Setting, Favorite, one scope tab at a time - and stops
where a run would select a row and press ``Go >>``: it reads the tab's
bookmarks and lists them instead. Activating each tab matters because GSCM
loads a scope's rows on selection; the application-level ``gds_bookmark``
dataset read at portal load holds only what has loaded so far, and trusting
it without the walk is what once reported a user's Public bookmarks missing.
Once a tab is activated, the dataset supplies authoritative stable ids and
scope when the runtime exposes it. The Setting dialog's ``grd_bookmark`` grid
is also inventoried when it binds, reconciled explicitly with dataset rows,
and retained as a discovery-only fallback source - nothing wider is scanned.
Execution requires the exact dataset ID/raw-name tuple and verified Grid state.

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
from typing import Any, NoReturn

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
CLOSE_LABELS = ("Close",)
#: Read off the live portal via DevTools (2026-08): the Favorite dialog
#: mounts as ``Setting0`` and its Go control is named ``btn_openFavorite``,
#: whose caption child (``:icontext``) renders the visible "Go" text.
GO_BUTTON_ID = (
    "mainframe.VFrameSet.TopFrame.Setting0.form.div_favorite.form.btn_openFavorite"
)
#: Tried after the id above, each through the exact-identity Go guard. The
#: all-lowercase twin guards
#: against a build (or a hand transcription) that lowercases component
#: paths; the ``btn_go`` shape is the id an earlier deployment carried.
GO_BUTTON_FALLBACK_IDS = (
    "mainframe.vframeset.topframe.setting0.form.div_favorite.form.btn_openfavorite",
    "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form.btn_go",
)
#: When no known id is on screen the button is rediscovered: by component
#: name in the live Nexacro tree, and by id shape in the DOM.
_GO_NAME_RE = re.compile(r"^btn_?(?:go\d*|open_?favorite\d*)$", re.IGNORECASE)
GO_ID_HINTS = ("btn_go", "btn_openfavorite")
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
#: Failure messages travel through run events into the log UI; an unbounded
#: inventory once produced 100,000-character errors nobody could read.
MAX_INVENTORY_CHARS = 1_800
#: Compact enough to travel with the existing run-event error messages while
#: still exposing the three independent pieces of the Favorite contract.
MAX_FAVORITE_STATE_CHARS = 400
#: How long a headed worker waits for a human to finish SSO and Knox MFA in
#: the visible window before giving up on the run.
MANUAL_LOGIN_WAIT_MS = 5 * 60_000

# Nexacro keeps the Favorite grid's scroll position in its own component
# state. The live grid exposes these controls even though ``scrollHeight`` on
# the surrounding HTML element never changes from its viewport height.
#: The live portal mounts the Setting dialog with a numbered frame name
#: (Setting0 on the current build, Setting1 on an earlier one), so the grid
#: is matched on the dialog-local tail of its path, never on that number.
FAVORITE_GRID_ID_SUFFIX = "div_favorite.form.grd_bookmark"
FAVORITE_SCROLL_PAGE_STEPS = 8
FAVORITE_SCROLL_RESET_PASSES = 40

DOWNLOAD_TEXT = "Excel download"

# A loaded bookmark must identify itself inside the active report work frame
# before the Excel handler may run.  MDI tab captions are deliberately not
# accepted: an inactive report's tab remains visible and could otherwise
# authorize exporting a different active report.
LOADED_TITLE_RETRY_REASONS = {
    "loaded-report-title-unavailable",
    "nexacro-unavailable",
    "missing-excel-component",
}

#: Samsung SSO's sign-in page. The automation profile holds one session per
#: portal, so ASAP can be signed in while GSCM is not - which is exactly what a
#: bare "the client did not render" message fails to convey.
LOGIN_PAGE_MARKERS = (
    "single sign on login", "please enter your password", "ad sso",
    "change password", "sign in", "log in", "knox", "verification code",
    "two-factor", "otp",
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

#: One bounded snapshot of the contract between the Nexacro application
#: dataset and the rendered Favorite grid.  It deliberately reports raw
#: ``publicscope`` values: an unknown value is evidence to map later, never a
#: reason to guess that the bookmark is Public.
_FAVORITE_STATE_JS = r"""(gridSuffix) => {
    const visible = element => {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = window.getComputedStyle(element);
        return style.visibility !== 'hidden' && style.display !== 'none';
    };
    const grids = [];
    let settingShell = false;
    for (const element of document.querySelectorAll('[id]')) {
        const id = String(element.id || '');
        if (/TopFrame\.Setting\d+(?:\.|$)/i.test(id)) settingShell = true;
        if (!id.endsWith(gridSuffix) || !visible(element)) continue;
        const rows = Array.from(element.querySelectorAll('[id*=".body.gridrow_"]'))
            .filter(row => /\.body\.gridrow_\d+$/.test(String(row.id || '')) && visible(row));
        grids.push({id, rows: rows.length});
    }
    let dataset = null;
    try {
        if (typeof nexacro !== 'undefined' && nexacro
                && typeof nexacro.getApplication === 'function') {
            const app = nexacro.getApplication();
            const ds = app && app.gds_bookmark;
            if (ds && typeof ds.getRowCount === 'function'
                    && typeof ds.getColumn === 'function') {
                const scopes = {};
                const count = ds.getRowCount();
                for (let index = 0; index < count; index++) {
                    const raw = String(ds.getColumn(index, 'publicscope') ?? '').trim() || '(blank)';
                    scopes[raw] = (scopes[raw] || 0) + 1;
                }
                dataset = {available: true, rows: count, scopes};
            }
        }
    } catch (error) { dataset = {available: false, error: String(error)}; }
    return {grids, dataset, setting_shell: settingShell};
}"""

#: Match a mounted component path without relying on a hard-coded Setting0 or
#: Setting1 frame number.  ``visibleOnly`` is false for the shell predicate:
#: mounting the frame is the effect of the gear click even while Nexacro is
#: still painting its children.
_COMPONENT_PATH_MATCH_JS = r"""(options) => {
    const pattern = new RegExp(options.pattern, 'i');
    for (const element of document.querySelectorAll('[id]')) {
        if (!pattern.test(String(element.id || ''))) continue;
        if (!options.visibleOnly) return true;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(element);
        if (style.visibility !== 'hidden' && style.display !== 'none') return true;
    }
    return false;
}"""

#: Select one exact bookmark identity through the dataset actually bound to the
#: visible Favorite grid.  ``rowposition`` alone is not proof: GSCM's Go handler
#: consumes the Grid's current/selected row, so both layers must agree.
_SELECT_BOOKMARK_ROW_JS = r"""(request) => {
    if (typeof nexacro === 'undefined' || !nexacro
            || typeof nexacro.getApplication !== 'function') {
        return {selected: false, reason: 'nexacro-unavailable'};
    }
    const exact = value => String(value ?? '').trim();
    const wantedId = exact(request.bookmark_id);
    const wantedName = exact(request.bookmark_name);
    if (!wantedId) return {selected: false, reason: 'empty-bookmark-id'};
    if (!wantedName) return {selected: false, reason: 'empty-bookmark-name'};
    const app = nexacro.getApplication();
    const fromCollection = (collection, key) => {
        if (!collection) return null;
        try { if (collection[key] != null) return collection[key]; } catch (error) {}
        try {
            if (typeof collection.get_item === 'function') {
                const item = collection.get_item(key);
                if (item != null) return item;
            }
        } catch (error) {}
        return null;
    };
    const resolveComponent = componentId => {
        let component = app;
        for (const part of String(componentId || '').split(':', 1)[0].split('.')) {
            if (!part) continue;
            let next = null;
            try { if (component && component[part] != null) next = component[part]; }
            catch (error) {}
            if (!next && component) {
                for (const collectionName of ['components', 'frames', 'all']) {
                    try { next = fromCollection(component[collectionName], part); }
                    catch (error) { next = null; }
                    if (next) break;
                }
            }
            if (!next) return null;
            component = next;
        }
        return component;
    };
    const visibleElement = element => {
        try {
            const rect = element.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return false;
            const style = window.getComputedStyle(element);
            return style.visibility !== 'hidden' && style.display !== 'none';
        } catch (error) { return false; }
    };
    const visibleGridIds = Array.from(document.querySelectorAll('[id]'))
        .filter(element => String(element.id || '').endsWith(request.grid_suffix)
            && visibleElement(element))
        .map(element => String(element.id));
    if (!visibleGridIds.length) {
        return {selected: false, reason: 'favorite-grid-not-visible'};
    }
    if (visibleGridIds.length !== 1) {
        return {selected: false, reason: 'ambiguous-favorite-grid',
                grid_id: visibleGridIds.slice(0, 4).join(' | ')};
    }
    const gridId = visibleGridIds[0];
    const grid = resolveComponent(gridId);
    if (!grid) {
        return {selected: false, reason: 'favorite-grid-component-unresolved',
                grid_id: gridId};
    }

    const resolveDataset = grid => {
        let dataset = null;
        let binding = null;
        try { if (typeof grid.getBindDataset === 'function') dataset = grid.getBindDataset(); }
        catch (error) { /* try the other supported forms */ }
        try { if (!dataset && grid._binddataset) dataset = grid._binddataset; }
        catch (error) {}
        try { if (!dataset && grid.binddataset) binding = grid.binddataset; }
        catch (error) {}
        try {
            if (!binding && typeof grid.get_binddataset === 'function') binding = grid.get_binddataset();
        } catch (error) {}
        if (typeof dataset === 'string') { binding = dataset; dataset = null; }
        if (!dataset && binding) {
            const name = String(binding).replace(/^@/, '');
            let owner = grid;
            for (let depth = 0; owner && depth < 12 && !dataset; depth++) {
                try {
                    dataset = owner[name]
                        || (owner.datasets && (owner.datasets[name]
                            || (typeof owner.datasets.get_item === 'function'
                                && owner.datasets.get_item(name))))
                        || (owner.form && owner.form[name]);
                } catch (error) {}
                try { owner = owner.parent; } catch (error) { owner = null; }
            }
            try { dataset = dataset || app[name]; } catch (error) {}
        }
        return dataset;
    };
    const dataset = resolveDataset(grid);
    if (!dataset || typeof dataset.getRowCount !== 'function'
            || typeof dataset.getColumn !== 'function'
            || typeof dataset.set_rowposition !== 'function') {
        return {selected: false, reason: 'bound-dataset-unavailable', grid_id: gridId};
    }
    const idRows = [];
    for (let index = 0; index < dataset.getRowCount(); index++) {
        if (exact(dataset.getColumn(index, 'userreportid')) === wantedId) idRows.push(index);
    }
    if (!idRows.length) {
        return {selected: false, reason: 'bookmark-id-not-in-bound-dataset', grid_id: gridId};
    }
    if (idRows.length !== 1) {
        return {selected: false, reason: 'duplicate-bookmark-id', grid_id: gridId,
                matching_rows: idRows.slice(0, 20)};
    }
    const rowIndex = idRows[0];
    const observedName = exact(dataset.getColumn(rowIndex, 'userreportname'));
    if (observedName !== wantedName) {
        return {selected: false, reason: 'bookmark-name-mismatch', grid_id: gridId,
                row_index: rowIndex, observed_name: observedName};
    }
    let selectType = '';
    try {
        selectType = exact(
            typeof grid.get_selecttype === 'function' ? grid.get_selecttype() : grid.selecttype
        ).toLowerCase();
    } catch (error) {}
    if (!['row', 'multirow', 'cell'].includes(selectType)) {
        return {selected: false, reason: 'unsupported-grid-selecttype', grid_id: gridId,
                select_type: selectType || '(blank)'};
    }
    if (selectType === 'multirow') {
        if (typeof grid.clearSelect !== 'function') {
            return {selected: false, reason: 'grid-clear-selection-unavailable',
                    grid_id: gridId, select_type: selectType};
        }
        try { grid.clearSelect(); }
        catch (error) {
            return {selected: false, reason: 'grid-clear-selection-error',
                    grid_id: gridId, select_type: selectType};
        }
    }
    try { dataset.set_rowposition(rowIndex); }
    catch (error) {
        return {selected: false, reason: 'rowposition-error', grid_id: gridId,
                attempted: true, row_index: rowIndex};
    }
    let actual = null;
    try {
        actual = typeof dataset.get_rowposition === 'function'
            ? dataset.get_rowposition() : dataset.rowposition;
    } catch (error) {}
    if (Number(actual) !== rowIndex) {
        return {selected: false, reason: 'rowposition-rejected', grid_id: gridId,
                attempted: true, row_index: rowIndex, row_position: Number(actual)};
    }
    const selectedId = exact(dataset.getColumn(rowIndex, 'userreportid'));
    const selectedName = exact(dataset.getColumn(rowIndex, 'userreportname'));
    if (selectedId !== wantedId || selectedName !== wantedName) {
        return {selected: false, reason: 'selected-id-mismatch', grid_id: gridId,
                attempted: true, row_index: rowIndex,
                observed_id: selectedId, observed_name: selectedName};
    }
    let selectedCell = null;
    if (['row', 'multirow'].includes(selectType)) {
        if (typeof grid.selectRow !== 'function') {
            return {selected: false, reason: 'grid-select-row-unavailable',
                    grid_id: gridId, select_type: selectType, attempted: true,
                    row_index: rowIndex};
        }
        try { grid.selectRow(rowIndex, true); }
        catch (error) {
            return {selected: false, reason: 'grid-select-row-error',
                    grid_id: gridId, select_type: selectType, attempted: true,
                    row_index: rowIndex};
        }
    } else {
        let cellIndex = 0;
        try {
            if (typeof grid.getBindCellIndex === 'function') {
                const bound = Number(grid.getBindCellIndex('body', 'userreportname'));
                if (Number.isFinite(bound) && bound >= 0) cellIndex = bound;
            }
            if (typeof grid.setCellPos !== 'function') {
                return {selected: false, reason: 'grid-cell-selection-unavailable',
                        grid_id: gridId, select_type: selectType, attempted: true,
                        row_index: rowIndex};
            }
            grid.setCellPos(cellIndex);
            selectedCell = cellIndex;
        } catch (error) {
            return {selected: false, reason: 'grid-cell-selection-error',
                    grid_id: gridId, select_type: selectType, attempted: true,
                    row_index: rowIndex};
        }
    }
    const currentRow = () => {
        try {
            return Number(typeof grid.get_currentrow === 'function'
                ? grid.get_currentrow() : grid.currentrow);
        } catch (error) { return NaN; }
    };
    const currentCell = () => {
        try {
            return Number(typeof grid.get_currentcell === 'function'
                ? grid.get_currentcell() : grid.currentcell);
        } catch (error) { return NaN; }
    };
    const selectedRows = () => {
        const numberList = value => {
            if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite);
            const number = Number(value);
            return Number.isFinite(number) && number >= 0 ? [number] : [];
        };
        let starts = [];
        let ends = [];
        try { starts = numberList(grid.selectstartrow); } catch (error) {}
        try { ends = numberList(grid.selectendrow); } catch (error) {}
        const rows = new Set();
        for (let index = 0; index < starts.length; index++) {
            const start = starts[index];
            const end = ends[index] ?? start;
            for (let row = Math.min(start, end); row <= Math.max(start, end); row++) rows.add(row);
        }
        return Array.from(rows).sort((left, right) => left - right);
    };
    // Re-read after selection: handlers are allowed to change the row.
    let verifiedPosition = null;
    try {
        verifiedPosition = typeof dataset.get_rowposition === 'function'
            ? dataset.get_rowposition() : dataset.rowposition;
    } catch (error) {}
    const verifiedId = Number(verifiedPosition) === rowIndex
        ? exact(dataset.getColumn(rowIndex, 'userreportid')) : '';
    const verifiedName = Number(verifiedPosition) === rowIndex
        ? exact(dataset.getColumn(rowIndex, 'userreportname')) : '';
    const verifiedCurrentRow = currentRow();
    const verifiedCurrentCell = currentCell();
    const verifiedSelectedRows = selectedRows();
    const rowSelectionValid = selectType === 'cell'
        ? Number.isFinite(verifiedCurrentCell) && verifiedCurrentCell === selectedCell
        : verifiedSelectedRows.length === 1 && verifiedSelectedRows[0] === rowIndex;
    if (Number(verifiedPosition) !== rowIndex || verifiedCurrentRow !== rowIndex
            || verifiedId !== wantedId || verifiedName !== wantedName || !rowSelectionValid) {
        return {selected: false, reason: 'post-selection-id-mismatch', grid_id: gridId,
                attempted: true,
                row_index: rowIndex, row_position: Number(verifiedPosition),
                current_row: verifiedCurrentRow, observed_id: verifiedId,
                observed_name: verifiedName, select_type: selectType,
                current_cell: verifiedCurrentCell,
                selected_rows: verifiedSelectedRows};
    }
    return {
        selected: true,
        attempted: true,
        strategy: 'bound-dataset-exact-identity',
        grid_id: gridId,
        row_index: rowIndex,
        current_row: verifiedCurrentRow,
        current_cell: verifiedCurrentCell,
        selected_rows: verifiedSelectedRows,
        select_type: selectType,
        bookmark_id: wantedId,
        bookmark_name: wantedName,
    };
}"""

#: Verify the exact selected Favorite and fire one native Go component in the
#: same JavaScript stack.  No DOM click exists between the safety gate and the
#: portal handler, so a recycled virtual row cannot race the dispatch.
_GUARDED_GO_CLICK_JS = r"""(request) => {
    if (typeof nexacro === 'undefined' || !nexacro
            || typeof nexacro.getApplication !== 'function') {
        return {fired: false, reason: 'nexacro-unavailable'};
    }
    const exact = value => String(value ?? '').trim();
    const wantedId = exact(request.bookmark_id);
    const wantedName = exact(request.bookmark_name);
    const componentId = exact(request.go_id).split(':', 1)[0];
    if (!wantedId || !wantedName) return {fired: false, reason: 'empty-bookmark-identity'};
    if (!componentId || !/(?:^|\.)(?:btn_?(?:go\d*|open_?favorite\d*))$/i.test(componentId)
            || !/(?:setting|div_favorite)/i.test(componentId)) {
        return {fired: false, reason: 'unsafe-go-component', component_id: componentId};
    }
    const app = nexacro.getApplication();
    const fromCollection = (collection, key) => {
        if (!collection) return null;
        try { if (collection[key] != null) return collection[key]; } catch (error) {}
        try {
            if (typeof collection.get_item === 'function') {
                const item = collection.get_item(key);
                if (item != null) return item;
            }
        } catch (error) {}
        return null;
    };
    const resolveComponent = componentId => {
        let component = app;
        for (const part of String(componentId || '').split(':', 1)[0].split('.')) {
            if (!part) continue;
            let next = null;
            try { if (component && component[part] != null) next = component[part]; }
            catch (error) {}
            if (!next && component) {
                for (const collectionName of ['components', 'frames', 'all']) {
                    try { next = fromCollection(component[collectionName], part); }
                    catch (error) { next = null; }
                    if (next) break;
                }
            }
            if (!next) return null;
            component = next;
        }
        return component;
    };
    const visibleElement = element => {
        try {
            const rect = element.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return false;
            const style = window.getComputedStyle(element);
            return style.visibility !== 'hidden' && style.display !== 'none';
        } catch (error) { return false; }
    };
    const gridIds = Array.from(document.querySelectorAll('[id]'))
        .filter(element => String(element.id || '').endsWith(request.grid_suffix)
            && visibleElement(element))
        .map(element => String(element.id));
    if (gridIds.length !== 1) {
        return {fired: false, reason: gridIds.length ? 'ambiguous-favorite-grid'
            : 'favorite-grid-not-visible', grid_count: gridIds.length,
            grid_id: gridIds.slice(0, 4).join(' | ')};
    }
    const gridId = gridIds[0];
    const grid = resolveComponent(gridId);
    if (!grid) {
        return {fired: false, reason: 'favorite-grid-component-unresolved',
                grid_id: gridId};
    }
    let dataset = null;
    let binding = null;
    try { if (typeof grid.getBindDataset === 'function') dataset = grid.getBindDataset(); }
    catch (error) {}
    try { if (!dataset && grid._binddataset) dataset = grid._binddataset; } catch (error) {}
    try { if (!dataset && grid.binddataset) binding = grid.binddataset; } catch (error) {}
    try {
        if (!binding && typeof grid.get_binddataset === 'function') binding = grid.get_binddataset();
    } catch (error) {}
    if (typeof dataset === 'string') { binding = dataset; dataset = null; }
    if (!dataset && binding) {
        const name = String(binding).replace(/^@/, '');
        let owner = grid;
        for (let depth = 0; owner && depth < 12 && !dataset; depth++) {
            try {
                dataset = owner[name]
                    || (owner.datasets && (owner.datasets[name]
                        || (typeof owner.datasets.get_item === 'function'
                            && owner.datasets.get_item(name))))
                    || (owner.form && owner.form[name]);
            } catch (error) {}
            try { owner = owner.parent; } catch (error) { owner = null; }
        }
        try { dataset = dataset || app[name]; } catch (error) {}
    }
    if (!dataset || typeof dataset.getColumn !== 'function'
            || typeof dataset.getRowCount !== 'function') {
        return {fired: false, reason: 'bound-dataset-unavailable', grid_id: gridId};
    }
    const idRows = [];
    for (let index = 0; index < dataset.getRowCount(); index++) {
        if (exact(dataset.getColumn(index, 'userreportid')) === wantedId) idRows.push(index);
    }
    if (idRows.length !== 1) {
        return {fired: false, reason: idRows.length ? 'duplicate-bookmark-id'
            : 'bookmark-id-not-in-bound-dataset', grid_id: gridId,
            matching_rows: idRows.slice(0, 20)};
    }
    let rowPosition = NaN;
    let currentRow = NaN;
    let currentCell = NaN;
    try { rowPosition = Number(typeof dataset.get_rowposition === 'function'
        ? dataset.get_rowposition() : dataset.rowposition); } catch (error) {}
    try { currentRow = Number(typeof grid.get_currentrow === 'function'
        ? grid.get_currentrow() : grid.currentrow); } catch (error) {}
    try { currentCell = Number(typeof grid.get_currentcell === 'function'
        ? grid.get_currentcell() : grid.currentcell); } catch (error) {}
    const observedId = Number.isFinite(rowPosition)
        ? exact(dataset.getColumn(rowPosition, 'userreportid')) : '';
    const observedName = Number.isFinite(rowPosition)
        ? exact(dataset.getColumn(rowPosition, 'userreportname')) : '';
    let selectType = '';
    try { selectType = exact(typeof grid.get_selecttype === 'function'
        ? grid.get_selecttype() : grid.selecttype).toLowerCase(); } catch (error) {}
    const numberList = value => {
        if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite);
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? [number] : [];
    };
    let starts = [];
    let ends = [];
    try { starts = numberList(grid.selectstartrow); } catch (error) {}
    try { ends = numberList(grid.selectendrow); } catch (error) {}
    const selectedRows = new Set();
    for (let index = 0; index < starts.length; index++) {
        const start = starts[index];
        const end = ends[index] ?? start;
        for (let row = Math.min(start, end); row <= Math.max(start, end); row++) {
            selectedRows.add(row);
        }
    }
    const selected = Array.from(selectedRows).sort((left, right) => left - right);
    const selectionValid = selectType === 'cell'
        ? Number.isFinite(currentCell) && currentCell >= 0
        : ['row', 'multirow'].includes(selectType)
            && selected.length === 1 && selected[0] === rowPosition;
    if (rowPosition < 0 || currentRow !== rowPosition
            || rowPosition !== idRows[0] || observedId !== wantedId
            || observedName !== wantedName || !selectionValid) {
        return {fired: false, reason: 'bookmark-selection-drift',
                grid_id: gridId, row_position: rowPosition,
                current_row: currentRow, observed_id: observedId,
                observed_name: observedName, select_type: selectType,
                current_cell: currentCell, selected_rows: selected};
    }
    const component = resolveComponent(componentId);
    if (!component) return {fired: false, reason: 'missing-go-component',
                            component_id: componentId};
    if (!component || typeof component.on_fire_onclick !== 'function') {
        return {fired: false, reason: 'go-onclick-unavailable', component_id: componentId};
    }
    try {
        component.on_fire_onclick(
            'lbutton', false, false, false,
            0, 0, 0, 0, 0, 0,
            component, component,
        );
        return {fired: true, strategy: 'guarded-native-go', component_id: componentId,
                bookmark_id: wantedId, bookmark_name: wantedName,
                row_position: rowPosition, current_row: currentRow,
                current_cell: currentCell, select_type: selectType,
                selected_rows: selected};
    } catch (error) {
        return {fired: false, reason: String(error && error.message ? error.message : error),
                component_id: componentId};
    }
}"""

#: Resolve the rendered title in the active WorkFrame and invoke the one
#: visible native Excel component in the same JavaScript stack.  Keeping the
#: proof and dispatch atomic prevents a tab/report change between a successful
#: title comparison and the export handler.  A title from TopFrame, MdiFrame,
#: Setting/Favorite, or another chrome surface can never authorize export.
_GUARDED_EXCEL_EXPORT_JS = r"""(request) => {
    const exact = value => String(value ?? '').trim();
    const wantedName = exact(request.bookmark_name);
    const wantedId = exact(request.bookmark_id);
    const configuredId = exact(request.excel_id).split(':', 1)[0];
    if (!wantedName || !wantedId) {
        return {fired: false, reason: 'empty-bookmark-identity',
                expected_id: wantedId, expected_name: wantedName};
    }
    if (typeof nexacro === 'undefined' || !nexacro
            || typeof nexacro.getApplication !== 'function') {
        return {fired: false, reason: 'nexacro-unavailable',
                expected_id: wantedId, expected_name: wantedName};
    }

    const visibleElement = element => {
        try {
            const rect = element.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return null;
            const style = window.getComputedStyle(element);
            if (style.visibility === 'hidden' || style.display === 'none') return null;
            return {x: Math.round(rect.left), y: Math.round(rect.top),
                    w: Math.round(rect.width), h: Math.round(rect.height)};
        } catch (error) { return null; }
    };
    const workFramePattern = /(?:^|\.)(?:work_?frame\d*)(?:\.|$)/i;
    const forbiddenSurface = /(?:^|\.)(?:topframe|mdiframe|leftframe|bottomframe|setting\d*|waitwindow)(?:\.|$)|(?:^|[._:])favorite(?:$|[._:])/i;
    const rankTitleId = identifier => {
        const id = String(identifier || '');
        if (/(?:^|[._:])(?:(?:sta|lbl|txt)_?)?(?:user)?bookmark_?(?:name|title)(?:$|[._:])/i.test(id)) return 0;
        if (/(?:^|[._:])(?:(?:sta|lbl|txt)_?)?(?:user)?report_?(?:name|title)(?:$|[._:])/i.test(id)) return 1;
        if (/(?:^|[._:])(?:(?:sta|lbl|txt)_?)?title(?:$|[._:])/i.test(id)) return 2;
        return null;
    };

    const titles = [];
    for (const element of document.querySelectorAll('[id]')) {
        const id = String(element.id || '');
        if (!workFramePattern.test(id) || forbiddenSurface.test(id)) continue;
        const rank = rankTitleId(id);
        if (rank == null) continue;
        const rect = visibleElement(element);
        if (!rect) continue;
        const text = exact(element.textContent);
        if (!text || text.length > 300) continue;
        let childHasSameText = false;
        for (const child of element.children) {
            if (exact(child.textContent) === text) { childHasSameText = true; break; }
        }
        if (childHasSameText) continue;
        titles.push({id, text, rank, ...rect});
    }
    titles.sort((left, right) => left.rank - right.rank
        || left.y - right.y || left.x - right.x || left.id.localeCompare(right.id));
    if (!titles.length) {
        return {fired: false, reason: 'loaded-report-title-unavailable',
                expected_id: wantedId, expected_name: wantedName, titles: []};
    }
    const bestRank = titles[0].rank;
    const best = titles.filter(item => item.rank === bestRank);
    const observedNames = Array.from(new Set(best.map(item => item.text)));
    if (observedNames.length !== 1) {
        return {fired: false, reason: 'ambiguous-loaded-report-title',
                expected_id: wantedId, expected_name: wantedName,
                observed_names: observedNames.slice(0, 10),
                titles: best.slice(0, 10)};
    }
    const observedName = observedNames[0];
    if (observedName !== wantedName) {
        return {fired: false, reason: 'loaded-report-title-mismatch',
                expected_id: wantedId, expected_name: wantedName,
                observed_name: observedName, titles: best.slice(0, 10)};
    }

    const app = nexacro.getApplication();
    const fromCollection = (collection, key) => {
        if (!collection) return null;
        try { if (collection[key] != null) return collection[key]; } catch (error) {}
        try {
            if (typeof collection.get_item === 'function') {
                const item = collection.get_item(key);
                if (item != null) return item;
            }
        } catch (error) {}
        return null;
    };
    const resolveComponent = componentId => {
        let component = app;
        for (const part of String(componentId || '').split(':', 1)[0].split('.')) {
            if (!part) continue;
            let next = null;
            try { if (component && component[part] != null) next = component[part]; }
            catch (error) {}
            if (!next && component) {
                for (const collectionName of ['components', 'frames', 'all']) {
                    try { next = fromCollection(component[collectionName], part); }
                    catch (error) { next = null; }
                    if (next) break;
                }
            }
            if (!next) return null;
            component = next;
        }
        return component;
    };
    const candidateIds = Array.from(document.querySelectorAll('[id]'))
        .filter(element => {
            const id = String(element.id || '').split(':', 1)[0];
            return /(?:^|\.)mdiframe(?:\.|$)/i.test(id)
                && /(?:^|\.)btn_exceldown$/i.test(id)
                && Boolean(visibleElement(element));
        })
        .map(element => String(element.id || '').split(':', 1)[0]);
    const uniqueIds = Array.from(new Set(candidateIds));
    let chosenIds = configuredId && uniqueIds.includes(configuredId)
        ? [configuredId] : uniqueIds;
    if (!chosenIds.length) {
        return {fired: false, reason: 'missing-excel-component',
                expected_id: wantedId, expected_name: wantedName,
                observed_name: observedName};
    }
    if (chosenIds.length !== 1) {
        return {fired: false, reason: 'ambiguous-excel-component',
                expected_id: wantedId, expected_name: wantedName,
                observed_name: observedName,
                component_ids: chosenIds.slice(0, 10)};
    }
    const targetId = chosenIds[0];
    const target = resolveComponent(targetId);
    if (!target || typeof target.on_fire_onclick !== 'function') {
        return {fired: false, reason: 'excel-onclick-unavailable',
                expected_id: wantedId, expected_name: wantedName,
                observed_name: observedName, component_id: targetId};
    }
    try {
        target.on_fire_onclick(
            'lbutton', false, false, false,
            0, 0, 0, 0, 0, 0,
            target, target,
        );
        return {fired: true,
                strategy: 'rendered-title-exact-guarded-native-export',
                expected_id: wantedId, expected_name: wantedName,
                observed_name: observedName, title_id: best[0].id,
                component_id: targetId};
    } catch (error) {
        return {fired: false, reason: 'excel-onclick-error',
                error: String(error && error.message ? error.message : error),
                expected_id: wantedId, expected_name: wantedName,
                observed_name: observedName, component_id: targetId};
    }
}"""

#: DOM fallback for older or differently packaged Nexacro deployments. This
#: deliberately starts at the Setting popup grid. A global ``textContent``
#: sweep can concatenate the top navigation labels into a phantom bookmark.
_FAVORITE_TREE_ROWS_JS = r"""() => {
    const grids = Array.from(document.querySelectorAll('[id]')).filter(element => {
        const id = element.id || '';
        return id.endsWith('div_favorite.form.grd_bookmark');
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

#: Visible username/password inputs. The SSO form's fields carry no text of
#: their own and are wider than the 80px cut-off in ``_ICON_CONTROLS_JS``, so
#: neither the label sweep nor the icon sweep can see them - this probe is the
#: only reliable way to recognise a sign-in form from the DOM.
_LOGIN_INPUTS_JS = """() => {
    const out = { password: 0, text: 0, ids: [] };
    for (const el of document.querySelectorAll('input')) {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        const type = (el.type || 'text').toLowerCase();
        if (type === 'password') out.password += 1;
        else if (type === 'text' || type === 'email') out.text += 1;
        else continue;
        if (el.id) out.ids.push(el.id.toLowerCase());
        if (el.name) out.ids.push(el.name.toLowerCase());
    }
    return (out.password || out.text) ? out : null;
}"""

#: Nexacro grids virtualize: only the rows in view exist in the DOM. Paging the
#: tallest scrollable container is how the rest are reached.
_SCROLL_TREE_JS = """() => {
    const grid = Array.from(document.querySelectorAll('[id]')).find(element =>
        (element.id || '').endsWith('div_favorite.form.grd_bookmark'));
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
        (element.id || '').endsWith('div_favorite.form.grd_bookmark'));
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
    const excluded = ['topframe.setting', 'div_favorite', 'mainframe.waitwindow'];
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
        const id = (element.id || '').toLowerCase();
        if (!fragments.some(fragment => id.includes(String(fragment).toLowerCase()))) continue;
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


#: Walk the live Nexacro component tree for Go-shaped buttons. A DOM id is
#: only as good as the guess that produced it; the component tree is the
#: portal's own registry of what exists, so a build that mounts the Favorite
#: dialog under a different frame path still reports its real Go button here.
#: Matches by component name (``btn_go``) or caption text (``Go >>``), and
#: reports the component's fully-qualified ``id`` - the same value the DOM
#: and the native click API both address.
_GO_COMPONENT_SEARCH_JS = """() => {
    if (typeof nexacro === 'undefined' || !nexacro
            || typeof nexacro.getApplication !== 'function') return null;
    const nameRe = /^btn_?go\\d*$/i;
    const textRe = /^\\s*go[\\s>\\u00bb!]*$/i;
    const out = [];
    const seen = new Set();
    const visit = (component, depth) => {
        if (!component || typeof component !== 'object') return;
        if (depth > 14 || out.length >= 40 || seen.has(component)) return;
        seen.add(component);
        try {
            const id = String(component.id || '');
            if (id && (nameRe.test(String(component.name || ''))
                    || textRe.test(String(component.text || '')))) {
                out.push({
                    id,
                    name: String(component.name || ''),
                    text: String(component.text || ''),
                });
            }
        } catch (error) { /* a getter that throws is not a candidate */ }
        for (const key of ['components', 'frames']) {
            let children = null;
            try { children = component[key]; } catch (error) { continue; }
            if (!children || typeof children.length !== 'number') continue;
            for (let index = 0; index < children.length && index < 300; index++) {
                try { visit(children[index], depth + 1); } catch (error) {}
            }
        }
        try { if (component.form) visit(component.form, depth + 1); } catch (error) {}
    };
    try {
        const app = nexacro.getApplication();
        visit(app.mainframe || app, 0);
    } catch (error) { return null; }
    return out;
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


def _component_path_matches(page, pattern: str, *, visible_only: bool = False) -> bool:
    """Whether a DOM component id matches one frame-number-agnostic pattern."""
    options = {"pattern": pattern, "visibleOnly": bool(visible_only)}
    return any(
        bool(value)
        for _root, value in _evaluate_everywhere(
            page, _COMPONENT_PATH_MATCH_JS, options,
        )
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


def favorite_state_report(page) -> str:
    """Describe the Favorite shell, rendered grid, and source dataset together.

    The report is diagnostic telemetry for one browser session.  It is never
    copied into bookmark automation metadata, where it would become stale.
    """
    grid_counts: dict[str, int] = {}
    setting_shell = False
    datasets: list[dict] = []
    for _root, state in _evaluate_everywhere(page, _FAVORITE_STATE_JS, FAVORITE_GRID_ID_SUFFIX):
        if not isinstance(state, dict):
            continue
        setting_shell = setting_shell or bool(state.get("setting_shell"))
        for grid in state.get("grids") or []:
            if not isinstance(grid, dict):
                continue
            identifier = str(grid.get("id") or "")
            if identifier:
                grid_counts[identifier] = max(
                    grid_counts.get(identifier, 0), int(grid.get("rows") or 0),
                )
        dataset = state.get("dataset")
        if isinstance(dataset, dict):
            datasets.append(dataset)

    grid_text = ", ".join(
        f"{_short_id(identifier, segments=5)}:{count} row(s)"
        for identifier, count in sorted(grid_counts.items())
    ) or "none"
    available = next(
        (item for item in datasets if item.get("available") is True), None,
    )
    if available is None:
        dataset_text = "unavailable"
    else:
        scopes = available.get("scopes") or {}
        scope_text = ",".join(
            f"{str(scope)[:40]}={int(count or 0)}"
            for scope, count in sorted(scopes.items(), key=lambda item: str(item[0]))
        ) or "none"
        dataset_text = f"{int(available.get('rows') or 0)} row(s) [{scope_text}]"
    report = (
        f"Favorite state: grids={grid_text}; gds_bookmark={dataset_text}; "
        f"Setting shell={'mounted' if setting_shell else 'absent'}."
    )
    if len(report) > MAX_FAVORITE_STATE_CHARS:
        report = report[: MAX_FAVORITE_STATE_CHARS - 1] + "…"
    return report


def _scope_tab(value: Any) -> str:
    normalized = _clean_name(value).casefold()
    for tab in SCOPE_TABS:
        if normalized == tab.casefold():
            return tab
    return ""


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
        identity_name = _exact_bookmark_name(row.get("userreportname"))
        name = _clean_name(identity_name)
        if not name or _normalize_label(name) in TREE_NOISE:
            continue
        scope = _clean_name(row.get("scope"))
        folder_path: list[str] = []
        _append_distinct(folder_path, BOOKMARK_SCOPE_NAMES.get(scope.upper(), scope))
        _append_distinct(folder_path, row.get("menugroupname"))
        _append_distinct(folder_path, row.get("menuname"))
        entries.append({
            "name": name,
            # ``name`` is safe, bounded catalog display text.  Execution uses
            # this raw dataset value instead: changing whitespace or case can
            # name a different saved Favorite and must fail closed.
            "identity_name": identity_name,
            "folder_path": folder_path,
            "tab": _scope_tab(row.get("publicscope")),
            "scope_raw": _clean_name(row.get("publicscope")),
            "bookmark_id": _exact_bookmark_id(row.get("userreportid")),
            "menu_id": _clean_name(row.get("menuid")),
            "scope": scope,
            "owner_id": _clean_name(row.get("userid")),
            "origin_owner_id": _clean_name(row.get("originuserid")),
            "source": "gds_bookmark",
        })
    return entries


def wait_for_bookmark_dataset(
    page, timeout_ms: int = BOOKMARK_DATASET_READY_TIMEOUT_MS, *, bookmark_id: str | None = None,
) -> list[dict] | None:
    """Wait for gds_bookmark, optionally until one stable id is present."""
    last_value: list[dict] | None = None
    wanted = _exact_bookmark_id(bookmark_id)
    poll_count = max(1, timeout_ms // IDLE_POLL_INTERVAL_MS)
    for _poll in range(poll_count):
        last_value = bookmark_dataset_entries(page)
        if last_value and (
            not wanted or any(
                _exact_bookmark_id(entry.get("bookmark_id")) == wanted
                for entry in last_value
            )
        ):
            return last_value
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    return last_value


def _select_bookmark_dataset_row(page, bookmark_id: str, bookmark_name: str) -> dict:
    """Select one exact ID/name in the bound Favorite grid, or explain why not."""
    wanted = _exact_bookmark_id(bookmark_id)
    wanted_name = _exact_bookmark_name(bookmark_name)
    if not wanted or not wanted_name:
        return {
            "selected": False,
            "reason": "empty-bookmark-id" if not wanted else "empty-bookmark-name",
        }
    request = {
        "bookmark_id": wanted,
        "bookmark_name": wanted_name,
        "grid_suffix": FAVORITE_GRID_ID_SUFFIX,
    }
    # This script mutates rowposition.  Unlike read-only inventories, do not
    # fan it out eagerly across every frame: stop after the first root that
    # verifies the requested stable id.
    failures: list[dict] = []
    for root in _roots(page):
        try:
            result = root.evaluate(_SELECT_BOOKMARK_ROW_JS, request)
        except Exception as exc:
            failures.append({"selected": False, "reason": f"evaluation-error:{exc}"})
            continue
        if isinstance(result, dict) and result.get("selected") is True:
            return result
        if isinstance(result, dict):
            failures.append(result)
            # Once a root found the relevant grid, let ``open_bookmark`` own
            # the retry. Never multiply its two-attempt mutation budget by
            # selecting again in another frame during this same attempt.
            if result.get("attempted") is True or result.get("grid_id") or result.get("reason") in {
                "ambiguous-favorite-grid", "duplicate-bookmark-id",
                "bookmark-name-mismatch", "unsupported-grid-selecttype",
                "grid-clear-selection-unavailable", "grid-clear-selection-error",
            }:
                return result
    # Unsupported iframe roots are common around a top-level Nexacro app. Do
    # not let their generic ``nexacro-unavailable`` result overwrite the
    # actionable failure reported by the root that actually saw the portal.
    informative = [
        result for result in failures
        if result.get("reason") != "nexacro-unavailable"
    ]
    return (informative or failures)[-1] if failures else {
        "selected": False,
        "reason": "selection-script-returned-no-result",
    }


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


def _short_id(identifier: Any, *, segments: int = 3) -> str:
    """The trailing component-path segments, which are the discriminating part.

    A full Nexacro path runs past 120 characters; the tail
    (``…div_favorite.form.grd_bookmark``) identifies the control just as well.
    """
    value = str(identifier or "")
    parts = value.split(".")
    if len(parts) <= segments:
        return value
    return "…" + ".".join(parts[-segments:])


def screen_inventory(
    page, *, keyword: str | None = None, max_chars: int = MAX_INVENTORY_CHARS,
) -> str:
    """A compact dump of what is actually on screen, for failure messages.

    Selector work against an undocumented Nexacro screen is guesswork until
    something reports the real ids. Every failure in this module carries this
    so one test run is enough to write the exact selector - which is why it
    reports icon-only controls too: the first version listed labelled elements
    only, and the control it most needed to reveal has no label. The whole
    report stays within ``max_chars`` because it ends up in run logs read by
    people; raise the budget when debugging interactively.
    """
    entries = []
    for _root, record in visible_text(page):
        text = record.get("text") or ""
        if keyword and keyword.casefold() not in text.casefold():
            continue
        identifier = record.get("id") or "(no id)"
        entries.append(
            f"{text[:40]!r} @({record.get('x')},{record.get('y')}) id={_short_id(identifier)}"
        )
        if len(entries) >= MAX_INVENTORY_ITEMS:
            break
    if not keyword:
        icons = [
            f"[icon] @({record.get('x')},{record.get('y')}) id={_short_id(record.get('id'))}"
            for _root, record in icon_controls(page, include_chrome=False)
        ]
        if icons:
            entries.append("ICON CONTROLS: " + " | ".join(icons[:MAX_INVENTORY_ITEMS]))
    lines: list[str] = []
    used = 0
    for index, entry in enumerate(entries):
        cost = len(entry) + (3 if lines else 0)
        if used + cost > max_chars:
            remaining_budget = max_chars - used - (3 if lines else 0)
            if remaining_budget > 40:
                lines.append(entry[: remaining_budget - 1] + "…")
            hidden = len(entries) - index
            lines.append(f"… (+{hidden} more)")
            break
        lines.append(entry)
        used += cost
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
            _fail_with_screen(
                page,
                f"GSCM popup blocked control {target_id!r} at "
                f"({target_rect.get('x')},{target_rect.get('y')},"
                f"{target_rect.get('w')},{target_rect.get('h')}). {details}.",
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


def _exact_bookmark_name(value: Any) -> str:
    """GSCM's exact bookmark label, trimming only its outer whitespace."""
    return str(value or "").strip()


def _exact_bookmark_id(value: Any) -> str:
    """GSCM's case-sensitive stable id, trimming only outer whitespace."""
    return str(value or "").strip()


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


class NotSignedInError(RuntimeError):
    """The automation profile is parked on the Samsung SSO / Knox MFA form."""


def portal_shell_rendered(page) -> bool:
    """Whether the Nexacro shell exists in any root - the signed-in proof."""
    return bool(_evaluate_everywhere(
        page, "(id) => !!document.getElementById(id)", "mainframe.VFrameSet"
    ))


def on_login_page(page) -> bool:
    """True when the browser is parked on the SSO or Knox MFA sign-in form."""
    # A rendered Nexacro shell is never the login form, whatever its text says.
    if portal_shell_rendered(page):
        return False
    # Substring match over the joined page text: SSO and Knox reword their
    # headings, so exact whole-label membership misses real sign-in pages.
    joined = " | ".join(
        _normalize_label(record.get("text")) for _root, record in visible_text(page)
    )
    marker_hits = sum(1 for marker in LOGIN_PAGE_MARKERS if marker in joined)
    if marker_hits >= 2:
        return True
    # The form's inputs carry no text and are wider than the icon sweep's 80px
    # cut-off, so probe for them directly.
    password_inputs = 0
    text_inputs = 0
    input_ids: list[str] = []
    for _root, found in _evaluate_everywhere(page, _LOGIN_INPUTS_JS):
        password_inputs += int(found.get("password") or 0)
        text_inputs += int(found.get("text") or 0)
        input_ids.extend(str(item) for item in found.get("ids") or [])
    if password_inputs and (marker_hits or text_inputs):
        return True
    identifiers = {
        str(record.get("id") or "").casefold()
        for _root, record in [*visible_text(page), *icon_controls(page)]
    }
    identifiers.update(identifier.casefold() for identifier in input_ids)
    identifiers.discard("")
    return sum(
        1 for marker in LOGIN_PAGE_ELEMENT_IDS
        if any(marker in identifier for identifier in identifiers)
    ) >= 2


def _not_signed_in_error() -> NotSignedInError:
    return NotSignedInError(
        "GSCM is not signed in: the automation browser is on the Samsung SSO "
        "login page. ASAP and GSCM are separate portals with separate sessions, "
        "so an ASAP scan can succeed while this one cannot. Sign the profile in "
        "to GSCM once, in a visible window, with:  python app\\flow_worker.py "
        "--profile-dir <profile> --authenticate-url "
        "https://mdscm.sec.samsung.net/nexa/index.html --authenticate-adapter "
        "gscm_portal   - or re-run setup.ps1, which now bootstraps every portal. "
        "Storing the encrypted BI-desktop credential in Flows > Catalog lets "
        "the worker sign back in automatically when the session expires."
    )


def _fail_with_screen(page, message: str) -> NoReturn:
    """Raise for a missing control, reporting sign-out as sign-out.

    An expired session mid-flow makes every later step fail with "X was not on
    screen"; checking the login form first turns that into the actionable
    sign-in error instead of a screen dump of the SSO page.
    """
    if on_login_page(page):
        raise _not_signed_in_error()
    raise RuntimeError(f"{message} On screen: {screen_inventory(page)}")


def wait_for_manual_login(
    page, *, timeout_ms: int = MANUAL_LOGIN_WAIT_MS, report_progress=None,
) -> None:
    """Poll until a human completes SSO/Knox in the visible window.

    Never navigates: the SSO and Knox redirects own the page until the portal
    shell renders. Raises ``NotSignedInError`` when nobody signs in within the
    budget.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    last_report = 0.0
    while time.monotonic() < deadline:
        if portal_shell_rendered(page):
            clear_screen(page)
            return
        now = time.monotonic()
        if report_progress and now - last_report >= 30:
            last_report = now
            remaining = int(deadline - now)
            report_progress(
                f"Waiting for sign-in in the visible browser window ({remaining}s left)."
            )
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    raise _not_signed_in_error()


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
    _fail_with_screen(
        page,
        f"GSCM did not render its Nexacro client within {timeout_ms // 1000} seconds. "
        f"Component not found: {component_id}.",
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
    """Whether the Setting dialog's Favorite panel, specifically, is visible."""
    if _component_path_matches(
        page,
        r"TopFrame\.Setting\d+\.form\.div_favorite(?:\.|$)",
        visible_only=True,
    ):
        return True
    if _component_visible(page, FAVORITE_GRID_ID_SUFFIX):
        return True
    # Some Nexacro builds expose the panel labels a beat before the grid
    # container.  Accept that only when the label's own component path proves
    # it belongs to Setting > Favorite.  A Public label elsewhere on the page
    # is unrelated and must not skip the gear or invalidate a successful Go.
    wanted = {_normalize_label(tab) for tab in SCOPE_TABS}
    for _root, record in visible_text(page):
        identifier = str(record.get("id") or "").casefold()
        if (
            re.search(r"topframe\.setting\d+(?:\.|$)", identifier)
            and "div_favorite" in identifier
            and _normalize_label(record.get("text")) in wanted
        ):
            return True
    return False


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
    if on_login_page(page):
        raise _not_signed_in_error()
    raise RuntimeError(
        "GSCM's Setting > Favorite dialog did not open, so its bookmark tabs "
        "(Private, Public, Custom) were never reachable. The gear that opens it "
        "carries no text, so it is found by id shape or by position in the top "
        "bar - neither worked here. " + top_bar_report(page)
    )


def _reach_favorite_panel(page) -> bool:
    """Select the Favorite panel and confirm its scope tabs rendered."""
    record = click_label(
        page, [FAVORITE_PANEL_LABEL], prefer_id_fragment="TopFrame.Setting",
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
    page, root_index: int, root, record: dict,
    tried: set[tuple[int, str]], spent_ids: set[str], *, ready_timeout_ms: int,
) -> bool:
    """Try one unique gear candidate and verify that Setting really opened."""
    component_ids = _component_element_ids(record.get("id"))
    if not component_ids:
        return False
    component_id = component_ids[0]
    attempt_key = (root_index, component_id)
    if attempt_key in tried or component_id in spent_ids or is_forbidden(component_id):
        return False
    tried.add(attempt_key)
    clicked_id = _click_record(page, root, record, timeout_ms=15_000)
    if clicked_id is None:
        return False
    # Only a dispatched click spends the id globally.  A root that did not
    # contain it must never prevent the same known id being tried in a frame.
    spent_ids.add(component_id)
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
    tried: set[tuple[int, str]] = set()
    spent_ids: set[str] = set()
    for root_index, root in enumerate(_roots(page)):
        if _activate_setting_record(
            page, root_index, root, {"id": SETTING_BUTTON_ID}, tried, spent_ids,
            ready_timeout_ms=DIALOG_READY_TIMEOUT_MS,
        ):
            return True
    # The component can exist in Nexacro's tree without a reachable DOM node.
    # Mirror the Go button's last resort before widening the search.
    if _native_click(page, SETTING_BUTTON_ID) and _wait_for_dialog_state(
        page, _setting_dialog_open, timeout_ms=DIALOG_READY_TIMEOUT_MS,
    ):
        return True
    hinted = click_by_id_hint(
        page, SETTING_BUTTON_HINTS, exclude_ids=spent_ids,
    )
    if hinted is not None:
        hinted_id = _component_element_ids(
            hinted.get("clicked_id") or hinted.get("id"),
        )[0]
        spent_ids.add(hinted_id)
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
    root_indexes = {id(root): index for index, root in enumerate(_roots(page))}
    for root, record in candidates[:MAX_GEAR_TRIES]:
        if _component_element_ids(record.get("id"))[:1] and (
            _component_element_ids(record.get("id"))[0] in spent_ids
        ):
            continue
        if _activate_setting_record(
            page, root_indexes.get(id(root), 0), root, record, tried, spent_ids,
            ready_timeout_ms=TAB_SETTLE_MS,
        ):
            return True
        _dismiss_stray_panel(page)
    return False


def _setting_dialog_open(page) -> bool:
    """The gear click is proven by a numbered Setting shell being mounted."""
    return _component_path_matches(page, r"TopFrame\.Setting\d+(?:\.|$)")


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
        page, [tab], prefer_id_fragment="TopFrame.Setting",
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


def _normalized_bookmark_path(path: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for index, part in enumerate(path or []):
        cleaned = _clean_name(part)
        if not cleaned:
            continue
        if index == 0:
            cleaned = BOOKMARK_SCOPE_NAMES.get(cleaned.upper(), cleaned)
        normalized.append(cleaned.casefold())
    return normalized


def _ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return True
    position = 0
    for item in haystack:
        if item == needle[position]:
            position += 1
            if position == len(needle):
                return True
    return False


def _paths_compatible(stored: list[str], rendered: list[str]) -> bool:
    """Tolerate harmless path detail drift without guessing across branches."""
    left = _normalized_bookmark_path(stored)
    right = _normalized_bookmark_path(rendered)
    return left == right or _ordered_subsequence(left, right) or _ordered_subsequence(right, left)


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
    identity_name = _exact_bookmark_name(entry.get("identity_name") or name)
    catalog_name = catalog_name or name
    tab = str(entry.get("tab") or "").strip()
    if tab not in SCOPE_TABS:
        raise ValueError(
            f"GSCM bookmark {name!r} has unknown scope "
            f"{entry.get('scope_raw')!r}; refusing to make it runnable."
        )
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
            "favorite_name": identity_name,
            "favorite_folder_path": list(entry.get("folder_path", [])),
            "favorite_element_id": entry.get("element_id") or None,
            "favorite_bookmark_id": entry.get("bookmark_id") or None,
            "favorite_menu_id": entry.get("menu_id") or None,
            "favorite_scope": entry.get("scope") or None,
            "favorite_scope_raw": entry.get("scope_raw") or None,
            "favorite_owner_id": entry.get("owner_id") or None,
            "favorite_origin_owner_id": entry.get("origin_owner_id") or None,
            "excel_btn_id": FALLBACK_EXCEL_BUTTON_ID,
        },
        "filters": [],
    }


def _tab_bookmarks(page, tab: str, notify) -> list[dict] | None:
    """One scope tab, activated the way a flow run activates it, then read.

    Returns ``None`` when the tab is not on this screen at all.  Dataset rows
    are accepted before rendered-grid readiness: that is the contract a run
    now uses as well, and it prevents a healthy dataset from being hidden by a
    Nexacro grid that failed to bind in this browser session.

    The ``gds_bookmark`` dataset supplies authoritative stable ids and scope
    once the tab is active. The rendered grid is also inventoried whenever it
    binds, then explicitly reconciled with the dataset; it remains the fallback
    identity source when the runtime does not expose the dataset. An exposed
    dataset with nothing under the tab, agreeing with an empty grid, is proof
    of a genuinely empty tab and skips the retry.
    """
    if not find_by_label(page, [tab]):
        return None
    activated_once = False
    for attempt in range(2):
        if attempt:
            notify(
                f"GSCM's {tab} tab listed nothing on the first activation; "
                "flipping scope and re-selecting it, as a flow run would."
            )
            alternate = next(
                (candidate for candidate in SCOPE_TABS
                 if candidate.casefold() != tab.casefold()),
                None,
            )
            if alternate:
                select_scope_tab(page, alternate)
        activated = select_scope_tab(page, tab, require_rows=False)
        if not activated:
            continue
        activated_once = True
        dataset = bookmark_dataset_entries(page)
        # Readiness is telemetry during a scan, not a prerequisite for trusting
        # stable dataset rows.  Still use the full bind budget before calling
        # the grid "never bound" so a merely slow portal is not misreported.
        # Re-read afterward because gds_bookmark often populates during this
        # wait even when the rendered grid never binds.
        rows_bound = wait_for_favorite_rows(page)
        refreshed_dataset = bookmark_dataset_entries(page)
        if refreshed_dataset is not None:
            dataset = refreshed_dataset
        scoped = [
            entry for entry in dataset or []
            if str(entry.get("tab") or "").casefold() == tab.casefold()
        ]
        rendered: list[dict] = []
        if rows_bound:
            leaves = _leaf_entries(collect_favorite_tree(
                page, lambda message: notify(f"{tab}: {message}"),
            ))
            if leaves:
                rendered = [
                    {**entry, "tab": tab, "scope_raw": tab, "source": "favorite_grid"}
                    for entry in leaves
                ]
        if scoped and not rows_bound:
            notify(
                f"GSCM's {tab} dataset contains {len(scoped)} bookmark(s), but "
                f"the Favorite grid never bound. {favorite_state_report(page)}"
            )
        if scoped or rendered:
            return [*scoped, *rendered]
        if dataset is not None:
            return []
    # A tab that was present and successfully activated is a valid empty scope
    # even on portal deployments that do not expose the backing dataset.  Only
    # a missing or unactivatable tab makes the scan incomplete.
    return [] if activated_once else None


def _entry_path_identity(entry: dict, *, include_tab: bool) -> tuple:
    path = tuple(_normalized_bookmark_path(entry.get("folder_path") or []))
    base = (path, _clean_name(entry.get("name")).casefold())
    if include_tab:
        return (_clean_name(entry.get("tab")).casefold(), *base)
    return base


def _reconcile_bookmark_entries(raw_entries: list[dict]) -> list[dict]:
    """Reconcile grid observations with authoritative dataset identities.

    A grid row has no stable id, while a dataset row can carry a scope that is
    different from the tab where a stale rendered row appeared.  Reconciliation
    is therefore explicit; a mere key collision is never treated as proof.
    """
    dataset_entries: list[dict] = []
    dataset_seen: set[tuple] = set()
    for entry in raw_entries:
        if entry.get("source") != "gds_bookmark":
            continue
        unique = (
            _exact_bookmark_id(entry.get("bookmark_id")),
            _entry_path_identity(entry, include_tab=True),
        )
        if unique in dataset_seen:
            continue
        dataset_seen.add(unique)
        dataset_entries.append(entry)

    by_path: dict[tuple, list[dict]] = {}
    for entry in dataset_entries:
        by_path.setdefault(_entry_path_identity(entry, include_tab=False), []).append(entry)

    reconciled_grid: list[dict] = []
    for entry in raw_entries:
        if entry.get("source") == "gds_bookmark":
            continue
        matches = by_path.get(_entry_path_identity(entry, include_tab=False), [])
        logical_matches: dict[tuple, dict] = {}
        for match in matches:
            stable_id = _exact_bookmark_id(match.get("bookmark_id"))
            key = ("id", stable_id) if stable_id else (
                "row", _entry_path_identity(match, include_tab=True),
            )
            logical_matches.setdefault(key, match)
        matches = list(logical_matches.values())
        # Multiple dataset rows can legitimately share one visual path/name in
        # different scopes.  In that case the observed tab is the only fact the
        # grid proves, so do not borrow either id.
        if len(matches) == 1:
            authority = matches[0]
            entry = {
                **entry,
                # Empty is authoritative too: an unknown publicscope must not
                # be converted into the grid's observed tab and made runnable.
                "tab": authority.get("tab") or "",
                "bookmark_id": authority.get("bookmark_id") or None,
                "identity_name": authority.get("identity_name") or entry.get("name"),
                "menu_id": authority.get("menu_id") or None,
                "scope": authority.get("scope") or None,
                # Preserve even a blank raw scope: blank is diagnostic evidence
                # of an unmapped value, not permission to borrow the grid tab.
                "scope_raw": (
                    authority.get("scope_raw")
                    if "scope_raw" in authority else entry.get("scope_raw")
                ),
                "owner_id": authority.get("owner_id") or None,
                "origin_owner_id": authority.get("origin_owner_id") or None,
                "source": "reconciled",
            }
        reconciled_grid.append(entry)

    entries: list[dict] = []
    seen_ids: set[str] = set()
    seen_unidentified_paths: set[tuple] = set()
    dataset_path_keys = {
        _entry_path_identity(entry, include_tab=True) for entry in dataset_entries
    }
    # Dataset rows go first because their stable id and raw scope are the
    # authoritative catalog representation.  Reconciled grid rows then collapse
    # into them by id/path while ambiguous rows retain their observed tab.
    for entry in [*dataset_entries, *reconciled_grid]:
        bookmark_id = _exact_bookmark_id(entry.get("bookmark_id"))
        if bookmark_id:
            if bookmark_id in seen_ids:
                continue
            seen_ids.add(bookmark_id)
        else:
            path_key = _entry_path_identity(entry, include_tab=True)
            # Explicit reconciliation already proved that this observation's
            # path is represented by one or more authoritative dataset rows.
            # When several ids share it we cannot enrich the grid row, but
            # retaining it would manufacture a third runnable bookmark.
            if entry.get("source") != "gds_bookmark" and path_key in dataset_path_keys:
                continue
            if path_key in seen_unidentified_paths:
                continue
            seen_unidentified_paths.add(path_key)
        entries.append(entry)
    return entries


_CATALOG_COPY_SUFFIX_RE = re.compile(r"^(.*?)(?: \((\d+)\))?$")


def _target_catalog_request(job: dict) -> dict | None:
    """Normalize current and one-release legacy targeted-scan payloads."""
    discovery = job.get("discovery") or {}
    target = job.get("target_report")
    target = dict(target) if isinstance(target, dict) else {}
    category_path = target.get("category_path")
    category_path = list(category_path) if isinstance(category_path, list) else []
    legacy_paths = [
        list(path) for path in discovery.get("report_paths") or []
        if isinstance(path, list) and path
    ]
    if not category_path and legacy_paths:
        category_path = legacy_paths[0]
    catalog_name = _clean_name(
        target.get("catalog_name")
        or (category_path[-1] if category_path else "")
    )
    bookmark_id = _exact_bookmark_id(target.get("favorite_bookmark_id"))
    if not (catalog_name or bookmark_id or legacy_paths):
        return None
    parsed = _CATALOG_COPY_SUFFIX_RE.fullmatch(catalog_name)
    source_name = _clean_name(parsed.group(1) if parsed else catalog_name)
    return {
        "bookmark_id": bookmark_id,
        "catalog_name": catalog_name or source_name,
        "source_name": source_name,
        "category_path": category_path,
    }


def _target_entries(entries: list[dict], target: dict) -> list[dict]:
    """Resolve one requested bookmark by stable id, then legacy name/path."""
    bookmark_id = _exact_bookmark_id(target.get("bookmark_id"))
    if bookmark_id:
        by_id = [
            entry for entry in entries
            if _exact_bookmark_id(entry.get("bookmark_id")) == bookmark_id
        ]
        if by_id:
            return by_id

    source_name = _clean_name(target.get("source_name")).casefold()
    by_name = [
        entry for entry in entries
        if _clean_name(entry.get("name")).casefold() == source_name
    ]
    category_path = target.get("category_path") or []
    if len(by_name) > 1 and category_path:
        requested_tab = _clean_name(category_path[0]).casefold()
        requested_folders = [_clean_name(part) for part in category_path[1:-1]]
        narrowed = [
            entry for entry in by_name
            if (
                not requested_tab
                or _clean_name(entry.get("tab")).casefold() == requested_tab
            ) and _paths_compatible(requested_folders, entry.get("folder_path") or [])
        ]
        if narrowed:
            by_name = narrowed
    if len(by_name) > 1:
        raise RuntimeError(
            f"The requested GSCM bookmark {target.get('catalog_name')!r} matches "
            "more than one saved bookmark and has no matching stable id. Run a "
            "full scan so the catalog can store favorite_bookmark_id."
        )
    return by_name


def discover_catalog(page, job: dict, report_progress) -> tuple[list[dict], bool]:
    """Catalog GSCM bookmarks by walking Setting > Favorite like a flow run.

    Discovery takes the exact route ``open_bookmark`` takes - portal, Setting
    gear, Favorite panel, one scope tab at a time with the run's rebind
    retries - and stops where a run would press ``Go >>``: it reads each
    activated tab's bookmarks and lists them. The in-memory dataset alone is
    trusted only when the dialog cannot be opened at all, because reading it
    without activating the tabs misses the scopes GSCM loads on selection.
    """
    url = portal_url(job)
    report_progress("running", {
        "stage": "navigation",
        "message": "Opening the GSCM portal for bookmark discovery.",
    })
    open_portal(page, url)

    def notify_discovery(message: str) -> None:
        report_progress("running", {
            "stage": "report_discovery", "message": message,
        })

    raw_entries: list[dict] = []

    dialog_error: RuntimeError | None = None
    incomplete_walk = False
    try:
        open_favorites_dialog(page, lambda message: report_progress(
            "running", {"stage": "navigation", "message": message},
        ))
    except NotSignedInError:
        raise
    except RuntimeError as error:
        dialog_error = error

    if dialog_error is None:
        for tab in SCOPE_TABS:
            tab_entries = _tab_bookmarks(page, tab, notify_discovery)
            if tab_entries is None:
                incomplete_walk = True
                notify_discovery(
                    f"GSCM's {tab} bookmark tab could not be found or activated; "
                    "the scan is incomplete and the prior snapshot will be preserved.",
                )
                continue
            raw_entries.extend(tab_entries)
            # Unknown publicscope rows do not belong to a known tab and would
            # otherwise disappear behind the per-tab filter. Preserve them as
            # evidence so the fail-closed warning below makes the scan partial.
            raw_entries.extend(
                entry for entry in (bookmark_dataset_entries(page) or [])
                if entry.get("tab") not in SCOPE_TABS
            )
            source = (
                " from GSCM's in-memory gds_bookmark dataset"
                if any(item.get("source") == "gds_bookmark" for item in tab_entries)
                else " from the Favorite grid" if tab_entries else ""
            )
            report_progress("running", {
                "stage": "report_discovery",
                "message": f"{tab}: {len(tab_entries)} bookmark observation(s){source}.",
                "item_count": len(tab_entries),
            })
    else:
        # The Setting gear could not be reached on this build, so the walk a
        # run would take is unavailable. The dataset is the only remaining
        # source; without it, the gear failure stands.
        dataset_entries = wait_for_bookmark_dataset(page)
        if not dataset_entries:
            raise dialog_error
        notify_discovery(
            "GSCM's Setting > Favorite dialog did not open; cataloguing from "
            "the in-memory gds_bookmark dataset without activating its tabs, "
            "which can miss scopes GSCM loads on selection.",
        )
        raw_entries.extend(dataset_entries)
        incomplete_walk = True

    if not raw_entries:
        _fail_with_screen(
            page,
            "GSCM exposed no bookmark rows in gds_bookmark or its Setting > "
            "Favorite tabs (Private, Public, Custom).",
        )

    # Preserve fail-closed evidence before stable-id reconciliation. A corrupt
    # dataset can list the same id once under Public and once under an unmapped
    # scope; final id dedupe must not let input order erase that ambiguity.
    raw_unknown_scope = [
        entry for entry in raw_entries
        if entry.get("source") == "gds_bookmark"
        and entry.get("tab") not in SCOPE_TABS
    ]
    entries = _reconcile_bookmark_entries(raw_entries)
    reconciled_unknown_scope = [
        entry for entry in entries if entry.get("tab") not in SCOPE_TABS
    ]
    unknown_by_identity: dict[tuple, dict] = {}
    for entry in [*raw_unknown_scope, *reconciled_unknown_scope]:
        bookmark_id = _exact_bookmark_id(entry.get("bookmark_id"))
        raw_scope = _clean_name(entry.get("scope_raw")).casefold()
        identity = (
            ("id", bookmark_id, raw_scope) if bookmark_id
            else (
                "path", _entry_path_identity(entry, include_tab=False), raw_scope,
            )
        )
        unknown_by_identity.setdefault(identity, entry)
    unknown_scope = list(unknown_by_identity.values())
    if unknown_scope:
        raw_values = sorted({
            _clean_name(entry.get("scope_raw")) or "(blank)"
            for entry in unknown_scope
        })
        notify_discovery(
            f"Skipped {len(unknown_scope)} GSCM bookmark(s) whose publicscope "
            f"is not mapped ({', '.join(raw_values)}). The scan is incomplete; "
            "the prior runnable snapshot will be preserved."
        )
        entries = [entry for entry in entries if entry.get("tab") in SCOPE_TABS]

    target = _target_catalog_request(job)
    complete = target is None and not unknown_scope and not incomplete_walk
    if target is not None:
        entries = _target_entries(entries, target)
        if not entries:
            raise RuntimeError(
                "The requested GSCM bookmark is no longer listed. "
                "Run a full scan to refresh the catalog."
            )

    if not entries:
        # Unknown-scope rows are evidence, not runnable reports.  Returning an
        # incomplete empty result keeps the last good server snapshot intact.
        return [], False

    report_progress("running", {
        "stage": "report_discovery",
        "message": f"Discovered {len(entries)} GSCM bookmark(s).",
        "item_count": len(entries),
    })
    names = (
        [target["catalog_name"] for _entry in entries]
        if target is not None
        else _catalog_names(entries)
    )
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
    # Reserve every literal label up front. Otherwise `[Budget, Budget,
    # Budget (2)]` gives the synthetic duplicate and the literal bookmark the
    # same catalog name, and the server's unique row constraint rejects the
    # whole scan.
    reserved = {_clean_name(entry.get("name")).casefold() for entry in entries}
    used: set[str] = set()
    next_suffix: dict[str, int] = {}
    names: list[str] = []
    for entry in entries:
        raw_name = _clean_name(entry.get("name"))
        key = raw_name.casefold()
        if key not in used:
            catalog_name = raw_name
        else:
            suffix = next_suffix.get(key, 2)
            while True:
                candidate = f"{raw_name} ({suffix})"
                candidate_key = candidate.casefold()
                suffix += 1
                if candidate_key not in reserved and candidate_key not in used:
                    catalog_name = candidate
                    break
            next_suffix[key] = suffix
        names.append(catalog_name)
        used.add(catalog_name.casefold())
    return names


# ── Download ──


def _bookmark_fail_with_screen(page, message: str) -> NoReturn:
    _fail_with_screen(page, f"{message} {favorite_state_report(page)}")


def _selection_result_report(result: dict | None) -> str:
    """Compact exact-selection diagnostics suitable for run-event messages."""
    if not isinstance(result, dict):
        return "no browser result"
    keys = (
        "reason", "strategy", "expected_id", "expected_name", "expected_scope",
        "observed_scope", "grid_id", "select_type", "row_index",
        "row_position", "current_row", "current_cell", "selected_rows", "bookmark_id",
        "bookmark_name", "observed_id", "observed_name", "component_id",
    )
    details = [f"{key}={result[key]!r}" for key in keys if key in result]
    return ", ".join(details)[:MAX_FAVORITE_STATE_CHARS] or "empty browser result"


def _authoritative_bookmark_entry(
    page, dataset: list[dict] | None, bookmark_id: str, name: str,
) -> dict | None:
    """One fail-closed exact ID/name row from an application dataset snapshot."""
    matches = [
        entry for entry in dataset or []
        if _exact_bookmark_id(entry.get("bookmark_id")) == bookmark_id
    ]
    if len(matches) > 1:
        observed = ", ".join(
            repr({
                "name": _exact_bookmark_name(
                    entry.get("identity_name") or entry.get("name")
                ),
                "scope": entry.get("tab") or entry.get("scope_raw") or "(blank)",
            })
            for entry in matches[:10]
        )
        raise RuntimeError(
            f"GSCM's gds_bookmark dataset listed stable id {bookmark_id!r} "
            f"{len(matches)} times ({observed}); refusing to choose one. "
            f"{favorite_state_report(page)}"
        )
    if matches:
        observed_name = _exact_bookmark_name(
            matches[0].get("identity_name") or matches[0].get("name")
        )
        if observed_name != _exact_bookmark_name(name):
            raise RuntimeError(
                f"GSCM stable id {bookmark_id!r} is named {observed_name!r}, not "
                f"the exact saved bookmark name {_exact_bookmark_name(name)!r}; "
                "refusing to open it. Re-scan the GSCM catalog and repoint the flow. "
                f"{favorite_state_report(page)}"
            )
    unknown_matches = [
        entry for entry in matches if entry.get("tab") not in SCOPE_TABS
    ]
    if unknown_matches:
        raw_values = sorted({
            _clean_name(entry.get("scope_raw")) or "(blank)"
            for entry in unknown_matches
        })
        raise RuntimeError(
            f"GSCM bookmark {name!r} has unknown publicscope "
            f"{', '.join(raw_values)}; refusing to choose Public. "
            f"{favorite_state_report(page)}"
        )
    known_tabs = {
        entry.get("tab") for entry in matches
        if entry.get("tab") in SCOPE_TABS
    }
    if len(known_tabs) > 1:
        raise RuntimeError(
            f"GSCM's gds_bookmark dataset listed stable id {bookmark_id!r} "
            "under more than one scope; refusing to choose one. "
            f"{favorite_state_report(page)}"
        )
    return matches[0] if matches else None


def open_bookmark(page, job: dict, report_progress=None) -> str:
    """Open one exact stable-ID/name bookmark and fail before any unsafe Go."""
    automation = (job.get("report") or {}).get("automation") or {}
    name = _exact_bookmark_name(automation.get("favorite_name"))
    stored_tab_raw = str(automation.get("favorite_tab") or "").strip()
    tab = _scope_tab(stored_tab_raw)
    stored_scope_raw = _clean_name(automation.get("favorite_scope_raw"))
    folder_path = [str(part) for part in (automation.get("favorite_folder_path") or [])]
    bookmark_id = _exact_bookmark_id(automation.get("favorite_bookmark_id"))
    if not name:
        raise RuntimeError(
            "This GSCM report has no bookmark reference. Scan the GSCM catalog again."
        )
    if not bookmark_id:
        raise RuntimeError(
            f"GSCM bookmark {name!r} has no stable favorite_bookmark_id. "
            "Re-scan the GSCM catalog and repoint the flow; the runner will not "
            "choose a virtualized row by name or folder path."
        )
    if stored_scope_raw and _scope_tab(stored_scope_raw) not in SCOPE_TABS:
        raise RuntimeError(
            f"GSCM bookmark {name!r} has unmapped stored publicscope "
            f"{stored_scope_raw!r}; refusing to execute it as {stored_tab_raw or 'Public'}. "
            "Run a fresh scan after the scope mapping is verified."
        )

    open_portal(page, portal_url(job))
    open_favorites_dialog(page, report_progress)
    # The portal populates gds_bookmark asynchronously after the Setting shell
    # appears. Resolve scope from the exact stable identity before selecting.
    dataset = wait_for_bookmark_dataset(page, bookmark_id=bookmark_id)
    authoritative = _authoritative_bookmark_entry(page, dataset, bookmark_id, name)
    scope_was_authoritative = authoritative is not None
    if authoritative:
        authoritative_tab = authoritative.get("tab") or ""
        if tab != authoritative_tab and report_progress:
            report_progress(
                f"GSCM bookmark {name!r} moved from {stored_tab_raw or '(unknown)'} "
                f"to {authoritative_tab}; using the scope recorded for stable id "
                f"{bookmark_id}."
            )
        tab = authoritative_tab

    if tab not in SCOPE_TABS:
        raise RuntimeError(
            f"GSCM bookmark {name!r} has no known Favorite scope "
            f"({stored_tab_raw or 'blank'}). Re-scan the catalog; the runner will "
            "not silently choose Public."
        )
    if not find_by_label(page, [tab]):
        _bookmark_fail_with_screen(page, f"GSCM's {tab} bookmark tab was not on screen.")
    if not select_scope_tab(page, tab, require_rows=False):
        _bookmark_fail_with_screen(page, f"GSCM's {tab} bookmark tab could not be activated.")

    # Scope rows can materialize only after activation. Re-read the exact
    # identity and allow one scope correction before the two-attempt selection
    # budget starts. Scope changes after that are unsafe.
    active_dataset = wait_for_bookmark_dataset(page, bookmark_id=bookmark_id)
    active_authoritative = _authoritative_bookmark_entry(
        page, active_dataset, bookmark_id, name,
    )
    if active_authoritative is None:
        _bookmark_fail_with_screen(
            page,
            f"GSCM stable id {bookmark_id!r} with exact name {name!r} was not "
            "present after activating its Favorite scope. Re-scan the catalog.",
        )
    active_tab = active_authoritative.get("tab") or ""
    if active_tab != tab:
        if scope_was_authoritative:
            _bookmark_fail_with_screen(
                page,
                f"GSCM stable id {bookmark_id!r} changed scope from {tab!r} to "
                f"{active_tab!r} before selection; refusing to continue.",
            )
        if report_progress:
            report_progress(
                f"GSCM bookmark {name!r} loaded under {active_tab} after activating "
                f"{tab}; correcting the scope for stable id {bookmark_id}."
            )
        tab = active_tab
        if not find_by_label(page, [tab]) or not select_scope_tab(
            page, tab, require_rows=False,
        ):
            _bookmark_fail_with_screen(
                page, f"GSCM's corrected {tab} bookmark tab could not be activated.",
            )
        corrected_dataset = wait_for_bookmark_dataset(page, bookmark_id=bookmark_id)
        corrected = _authoritative_bookmark_entry(
            page, corrected_dataset, bookmark_id, name,
        )
        if corrected is None or corrected.get("tab") != tab:
            observed_tab = (corrected or {}).get("tab") or "missing"
            raise RuntimeError(
                f"GSCM stable id {bookmark_id!r} changed scope again while the runner "
                f"was correcting its tab (expected {tab}, observed {observed_tab}); "
                f"refusing to guess. {favorite_state_report(page)}"
            )

    selected: dict = {"selected": False, "reason": "selection-not-attempted"}
    for selection_attempt in range(2):
        selected = _select_bookmark_dataset_row(page, bookmark_id, name)
        if selected.get("selected") is True:
            break
        if selected.get("reason") in {
            "duplicate-bookmark-id", "bookmark-name-mismatch",
            "empty-bookmark-id", "empty-bookmark-name", "ambiguous-favorite-grid",
        }:
            break
        if selection_attempt == 0:
            if not select_scope_tab(page, tab, require_rows=False):
                break
            retry_dataset = wait_for_bookmark_dataset(page, bookmark_id=bookmark_id)
            retry_authoritative = _authoritative_bookmark_entry(
                page, retry_dataset, bookmark_id, name,
            )
            if retry_authoritative is None:
                break
            retry_tab = retry_authoritative.get("tab") or ""
            if retry_tab != tab:
                _bookmark_fail_with_screen(
                    page,
                    f"GSCM stable id {bookmark_id!r} changed scope from {tab!r} "
                    f"to {retry_tab!r} during selection retry; refusing to continue.",
                )
    if report_progress:
        report_progress(f"Opening GSCM bookmark {' > '.join([*folder_path, name])}.")
    if selected.get("selected") is not True:
        selection_failure = {
            "expected_id": bookmark_id,
            "expected_name": name,
            "expected_scope": tab,
            "observed_scope": active_authoritative.get("tab") or "",
            **selected,
        }
        _bookmark_fail_with_screen(
            page,
            f"GSCM could not establish one exact selected row for stable id "
            f"{bookmark_id!r} and name {name!r}; refusing to press Go. "
            f"Selection result: {_selection_result_report(selection_failure)}.",
        )
    if report_progress:
        report_progress(
            f"Selected GSCM bookmark {name!r} by stable id {bookmark_id} "
            f"using {selected.get('strategy')}."
        )
    go_result = _click_go_button(page, bookmark_id, name)
    if go_result.get("activated") is not True:
        go_failure = {
            "expected_id": bookmark_id,
            "expected_name": name,
            "expected_scope": tab,
            "observed_scope": active_authoritative.get("tab") or "",
            **go_result,
        }
        _bookmark_fail_with_screen(
            page,
            f"GSCM's guarded Go action did not open exact bookmark {name!r} "
            f"({bookmark_id}). Result: {_selection_result_report(go_failure)}. "
            f"{_go_button_report(page)}",
        )
    wait_for_calculation(page, report_progress=report_progress)
    clear_screen(page)
    return selected.get("grid_id") or bookmark_id


def _go_button_fired(page) -> bool:
    """The Go click is proven only by the Favorite dialog closing."""
    page.wait_for_timeout(1_000)
    return not favorites_dialog_open(page)


def _looks_like_go_component(element_id: Any) -> bool:
    """Whether an id's terminal segment names a Go button (``btn_go``).

    A substring hint alone would also reach ``btn_gotohome``; requiring the
    terminal path segment to *be* a Go name keeps a wrong control from ever
    becoming a candidate.
    """
    component = str(element_id or "").split(":", 1)[0]
    return bool(_GO_NAME_RE.match(component.split(".")[-1]))


def _discover_go_candidates(page) -> list[str]:
    """Find the Favorite dialog's Go button without trusting one guessed id.

    Two independent sources: the live Nexacro component tree (which reports
    the real component paths, whatever frame this build mounts the dialog
    under - and sees an icon-styled Go that renders no caption text), and
    visible DOM ids shaped like a Go button. Candidates inside the Favorite
    dialog outrank the rest, and anything on the forbidden list never becomes
    a candidate.
    """
    candidates: dict[str, None] = {}
    for _root, records in _evaluate_everywhere(page, _GO_COMPONENT_SEARCH_JS):
        if not isinstance(records, list):
            continue
        for record in records:
            identifier = str((record or {}).get("id") or "")
            if identifier and not is_forbidden(identifier):
                candidates.setdefault(identifier)
    for _root, records in _evaluate_everywhere(page, _ID_MATCH_JS, list(GO_ID_HINTS)):
        for record in records:
            identifier = str((record or {}).get("id") or "")
            if (
                identifier
                and _looks_like_go_component(identifier)
                and not is_forbidden(identifier)
            ):
                candidates.setdefault(identifier)

    def rank(identifier: str) -> tuple:
        lowered = identifier.casefold()
        return (
            0 if "div_favorite" in lowered else 1 if "setting" in lowered else 2,
            0 if _looks_like_go_component(identifier) else 1,
            len(identifier),
        )

    return sorted(candidates, key=rank)


def _go_button_report(page) -> str:
    """What the Go hunt could see, for the failure message."""
    discovered = _discover_go_candidates(page)
    listed = ", ".join(_short_id(item) for item in discovered[:8]) or "none"
    return f"Go-shaped candidates on this screen: {listed}."


def _click_go_button(page, bookmark_id: str, bookmark_name: str) -> dict:
    """Atomically verify one selected identity and fire one native Go control."""
    clear_screen(page)
    tried: set[str] = set()
    failures: list[dict] = []
    for candidate in (
        GO_BUTTON_ID, *GO_BUTTON_FALLBACK_IDS, *_discover_go_candidates(page),
    ):
        component_ids = _component_element_ids(candidate)
        if not component_ids or component_ids[0] in tried:
            continue
        component_id = component_ids[0]
        tried.add(component_id)
        for root in _roots(page):
            try:
                result = root.evaluate(_GUARDED_GO_CLICK_JS, {
                    "bookmark_id": bookmark_id,
                    "bookmark_name": _exact_bookmark_name(bookmark_name),
                    "go_id": component_id,
                    "grid_suffix": FAVORITE_GRID_ID_SUFFIX,
                })
            except Exception as exc:
                failures.append({
                    "fired": False,
                    "reason": f"evaluation-error:{exc}",
                    "component_id": component_id,
                })
                continue
            if not isinstance(result, dict):
                failures.append({
                    "fired": False,
                    "reason": "guarded-go-returned-no-result",
                    "component_id": component_id,
                })
                continue
            if result.get("fired") is not True:
                failures.append(result)
                # Identity drift is authoritative. Trying another button after
                # the guard rejected the selected row would defeat the gate.
                if result.get("reason") == "bookmark-selection-drift":
                    return {**result, "activated": False}
                continue
            if _go_button_fired(page):
                return {**result, "activated": True}
            # The portal handler was dispatched. Never try a second Go control
            # and risk launching twice merely because the dialog stayed open.
            return {
                **result,
                "activated": False,
                "reason": "guarded-go-fired-but-dialog-stayed-open",
            }
    result = failures[-1] if failures else {
        "fired": False,
        "reason": "no-safe-go-component",
    }
    return {**result, "activated": False}


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


def _loaded_title_result_report(result: dict | None) -> str:
    """Compact diagnostics for the atomic rendered-title/export guard."""
    if not isinstance(result, dict):
        return "no browser result"
    keys = (
        "reason", "strategy", "expected_id", "expected_name", "observed_name",
        "observed_names", "title_id", "component_id", "component_ids", "titles",
        "error",
    )
    details = [f"{key}={result[key]!r}" for key in keys if key in result]
    return ", ".join(details)[:MAX_INVENTORY_CHARS] or "empty browser result"


def trigger_excel_export(page, job: dict, *, timeout_ms: int = 60_000) -> None:
    """Atomically verify the loaded title and dispatch native Excel export.

    Some bookmarks return the home shell to an idle state before the MDI work
    frame has finished mounting. Waiting only for the global busy overlay can
    therefore race both the rendered title and toolbar.  No DOM click is used:
    the exact, case-sensitive, outer-trimmed bookmark name is re-read from the
    active WorkFrame in the same browser-side operation that fires Excel.
    """
    automation = (job.get("report") or {}).get("automation") or {}
    bookmark_name = _exact_bookmark_name(automation.get("favorite_name"))
    bookmark_id = _exact_bookmark_id(automation.get("favorite_bookmark_id"))
    if not bookmark_name or not bookmark_id:
        raise RuntimeError(
            "GSCM cannot verify the loaded report before export because the flow "
            "has no exact bookmark ID/name identity. Re-scan the GSCM catalog and "
            "repoint the flow."
        )
    clear_screen(page)
    deadline = time.monotonic() + timeout_ms / 1000
    last_result: dict | None = None
    while time.monotonic() < deadline:
        for root in _roots(page):
            try:
                result = root.evaluate(_GUARDED_EXCEL_EXPORT_JS, {
                    "bookmark_id": bookmark_id,
                    "bookmark_name": bookmark_name,
                    "excel_id": excel_button_id(automation),
                })
            except Exception as exc:
                last_result = {"reason": "evaluation-error", "error": str(exc)}
                continue
            if not isinstance(result, dict):
                last_result = {"reason": "guarded-export-returned-no-result"}
                continue
            last_result = result
            if result.get("fired") is True:
                return
            reason = str(result.get("reason") or "")
            if reason not in LOADED_TITLE_RETRY_REASONS:
                _fail_with_screen(
                    page,
                    f"GSCM refused to export bookmark {bookmark_name!r} "
                    f"({bookmark_id}) because the rendered report title could "
                    "not prove the same loaded report. Guard result: "
                    f"{_loaded_title_result_report(result)}.",
                )
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    _fail_with_screen(
        page,
        f"GSCM did not find one authoritative rendered WorkFrame title and "
        f"Excel control for bookmark {bookmark_name!r} ({bookmark_id}) before "
        "the export timeout; no download was started. Last guard result: "
        f"{_loaded_title_result_report(last_result)}.",
    )
