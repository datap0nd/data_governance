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

**Why this module matches on text and geometry, not on ids.** Every id here
would have to be guessed from a screenshot. Instead the navigation clicks
controls by their visible label (``Favorite``, ``Public``, ``Go``) and rebuilds
the folder tree from the on-screen indentation of each row, which survives a
Nexacro layout change that would break an absolute component path. When a step
still cannot find what it needs, it raises with an inventory of what *was* on
screen (see :func:`screen_inventory`) so the next fix is informed by the real
DOM rather than another guess.

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

PORTAL_READY_TIMEOUT_MS = 180_000
DIALOG_READY_TIMEOUT_MS = 60_000
BOOKMARK_SETTLE_TIMEOUT_MS = 300_000
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
#: A row's action icons sit on the same line as its label.
ROW_ICON_TOLERANCE_PX = 10
MAX_INVENTORY_ITEMS = 120

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
    let best = null;
    let bestOverflow = 0;
    for (const element of document.querySelectorAll('*')) {
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

_POPUP_CLOSERS_JS = """() => {
    const ids = [];
    for (const element of document.querySelectorAll("[id]")) {
        const id = element.id || '';
        const lowered = id.toLowerCase();
        if (!lowered.includes('popup')) continue;
        if (!lowered.includes('close')) continue;
        if (element.getClientRects().length === 0) continue;
        ids.push(id);
    }
    return ids;
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


def dismiss_popups(page) -> int:
    """Close floating Nexacro notification cards that block background clicks."""
    closed = 0
    for root, ids in _evaluate_everywhere(page, _POPUP_CLOSERS_JS):
        for element_id in ids:
            try:
                root.locator(f"[id='{_css_escape(element_id)}']").first.click(
                    force=True, timeout=5_000,
                )
                closed += 1
            except Exception:
                # A popup that vanished on its own is the expected outcome, not
                # a failure: the sweep clears the screen, it does not audit it.
                continue
    return closed


def clear_screen(page) -> None:
    """One call for the two things that must be true before any click."""
    unlock_wait_window(page)
    dismiss_popups(page)
    unlock_wait_window(page)


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


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


def click_label(page, labels, *, timeout_ms: int = 30_000) -> dict | None:
    """Click the tightest visible control carrying one of ``labels``."""
    if is_forbidden(*labels):
        raise RuntimeError(f"Refusing to click a control named {labels!r} in GSCM.")
    clear_screen(page)
    for root, record in find_by_label(page, labels):
        try:
            locator = (
                root.locator(f"[id='{_css_escape(record['id'])}']").first
                if record.get("id")
                else root.locator(f"text={record['text']}").first
            )
            locator.click(force=True, timeout=timeout_ms)
            return record
        except Exception:
            continue
    return None


def _hint_rank(element_id: str, hints) -> int:
    lowered = str(element_id or "").casefold()
    for index, hint in enumerate(hints):
        if hint in lowered:
            return index
    return len(hints)


def click_by_id_hint(page, hints, *, timeout_ms: int = 30_000) -> dict | None:
    """Click an icon-only control located by the shape of its component id.

    Ranked by which hint matched before anything else. Sorting these by screen
    position instead let a later, vaguer hint win on geometry: the top bar's
    profile icon sits further right than the gear, so the search opened a user
    popover and reported the gear missing.
    """
    hints = list(hints)
    clear_screen(page)
    candidates = []
    for root, records in _evaluate_everywhere(page, _ID_MATCH_JS, hints):
        for record in records:
            candidates.append((root, record))
    # A toolbar gear sits at the top of the screen; prefer the topmost match.
    candidates = [item for item in candidates if not is_forbidden(item[1].get("id"))]
    candidates.sort(key=lambda item: (
        _hint_rank(item[1].get("id"), hints), item[1].get("y", 0), -item[1].get("x", 0),
    ))
    for root, record in candidates:
        try:
            root.locator(f"[id='{_css_escape(record['id'])}']").first.click(
                force=True, timeout=timeout_ms,
            )
            return record
        except Exception:
            continue
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
    """The dialog is up when its scope tabs are on screen."""
    visible = {_normalize_label(record.get("text")) for _root, record in visible_text(page)}
    return any(_normalize_label(tab) in visible for tab in SCOPE_TABS)


def open_favorites_dialog(page, report_progress=None) -> None:
    """Open Setting and select its Favorite panel.

    The gear that opens Setting carries no text, so it is found by id shape;
    everything after it is clicked by its visible label.
    """
    clear_screen(page)
    if favorites_dialog_open(page):
        click_label(page, [FAVORITE_PANEL_LABEL])
        page.wait_for_timeout(TAB_SETTLE_MS)
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
    click_label(page, [FAVORITE_PANEL_LABEL])
    page.wait_for_timeout(TAB_SETTLE_MS)
    clear_screen(page)
    deadline = time.monotonic() + DIALOG_READY_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if favorites_dialog_open(page):
            return True
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    return False


def _open_setting(page) -> bool:
    """Open the Setting dialog, whose gear carries no text.

    Try the id shapes first. When none matches - which is what happened
    against the live portal - fall back to position: the gear sits in the top
    bar to the right of the business pills. Candidates are tried right to left
    and each is checked, so a wrong guess costs one harmless icon click rather
    than a failed scan. Nothing on the forbidden list is ever a candidate.
    """
    for root in _roots(page):
        try:
            gear = root.locator(f"[id='{_css_escape(SETTING_BUTTON_ID)}']").first
            if not gear.count():
                continue
            gear.click(force=True, timeout=15_000)
        except Exception:
            continue
        page.wait_for_timeout(TAB_SETTLE_MS)
        if _setting_dialog_open(page):
            return True
    if click_by_id_hint(page, SETTING_BUTTON_HINTS) is not None:
        page.wait_for_timeout(TAB_SETTLE_MS)
        if _setting_dialog_open(page):
            return True
    if click_label(page, ["Setting", "Settings"]) is not None:
        page.wait_for_timeout(TAB_SETTLE_MS)
        if _setting_dialog_open(page):
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
        try:
            root.locator(f"[id='{_css_escape(record['id'])}']").first.click(
                force=True, timeout=15_000,
            )
        except Exception:
            continue
        page.wait_for_timeout(TAB_SETTLE_MS)
        if _setting_dialog_open(page):
            return True
        _dismiss_stray_panel(page)
    return False


def _setting_dialog_open(page) -> bool:
    """The Setting dialog is up when its left rail is on screen."""
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


def select_scope_tab(page, tab: str) -> bool:
    """Switch the Favorite panel to one of Private / Public / Custom."""
    if click_label(page, [tab]) is None:
        return False
    page.wait_for_timeout(TAB_SETTLE_MS)
    clear_screen(page)
    return True


def read_favorite_tree(page, seed: list[tuple[int, str]] | None = None) -> list[dict]:
    """Rebuild the folder tree currently shown in the Favorite panel.

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
    for root, record in visible_text(page):
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
        })
    if not rows:
        return []
    # A report row carries its own action icons (open, pin) on the right; a
    # folder row does not. That is a far better leaf test than "the next row is
    # indented", which cannot see a folder that is currently collapsed.
    icons = [record for _root, record in icon_controls(page)]

    # The tree occupies the densest vertical band on screen. Keep the rows that
    # share a left-edge family with the majority, so page chrome elsewhere in
    # the dialog does not become a phantom folder.
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
            "has_actions": _row_has_actions(row, icons),
            "icons_seen": bool(icons),
            "stack": [*stack, (row["x"], row["name"])],
        })
        stack.append((row["x"], row["name"]))
    return entries


def scroll_tree(page) -> bool:
    """Page the favorites tree down one screen. True when it actually moved."""
    for _root, value in _evaluate_everywhere(page, _SCROLL_TREE_JS):
        if isinstance(value, dict) and value.get("moved"):
            return True
    return False


def collect_favorite_tree(page, report_progress=None, *, max_passes: int = 40) -> list[dict]:
    """Every row in the current tab, scrolling and expanding as needed.

    Two things hide rows. The grid virtualizes, so only what is in view exists
    in the DOM; and folders start collapsed, so their children are not rendered
    at all. This scrolls to the bottom collecting rows, then clicks the rows
    that might be collapsed folders and repeats until nothing new appears.

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


def _row_has_actions(row: dict, icons: list[dict]) -> bool:
    """Whether this row shows the per-report action icons on its right.

    Every row carries an icon on its *left* - a folder or a document glyph -
    so only an icon past the label's right edge counts. That is the open and
    pin pair, which GSCM renders for reports and not for folders.
    """
    row_y = row.get("y", 0)
    row_right = row.get("x", 0) + row.get("w", 0)
    return any(
        abs(icon.get("y", 0) - row_y) <= ROW_ICON_TOLERANCE_PX
        and icon.get("x", 0) > row_right
        for icon in icons
    )


def _leaf_entries(entries: list[dict]) -> list[dict]:
    """The report rows, excluding the folders that contain them.

    Prefer the action icons each report row carries. Fall back to indentation
    when no icons were reported at all, so the reconstruction still works on a
    screen that renders its actions differently.
    """
    if any(entry.get("icons_seen") for entry in entries):
        # The screen renders row icons, so their absence is meaningful: this
        # row is a folder, even when it is collapsed and looks like a leaf.
        return [entry for entry in entries if entry.get("has_actions")]
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
            "excel_btn_id": FALLBACK_EXCEL_BUTTON_ID,
        },
        "filters": [],
    }


def discover_catalog(page, job: dict, report_progress) -> tuple[list[dict], bool]:
    """Catalog GSCM's bookmarks across the Private, Public, and Custom tabs."""
    url = portal_url(job)
    report_progress("running", {
        "stage": "navigation",
        "message": "Opening the GSCM portal for bookmark discovery.",
    })
    open_portal(page, url)
    open_favorites_dialog(page, lambda message: report_progress(
        "running", {"stage": "navigation", "message": message},
    ))

    entries: list[dict] = []
    seen: set[tuple] = set()
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
            "GSCM's Setting > Favorite dialog opened, but none of its tabs "
            "(Private, Public, Custom) listed a bookmark. On screen: "
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
    if not select_scope_tab(page, tab):
        raise RuntimeError(
            f"GSCM's {tab} bookmark tab was not on screen. On screen: "
            + screen_inventory(page)
        )

    entry = _resolve_entry(page, name, folder_path, tab)
    if report_progress:
        report_progress(f"Opening GSCM bookmark {' > '.join([*folder_path, name])}.")
    _click_entry(page, entry)
    if click_label(page, GO_LABELS) is None:
        raise RuntimeError(
            "GSCM's Go button was not on screen after selecting the bookmark. "
            "On screen: " + screen_inventory(page)
        )
    wait_for_calculation(page, report_progress=report_progress)
    clear_screen(page)
    return entry.get("element_id") or name


def _resolve_entry(page, name: str, folder_path: list[str], tab: str) -> dict:
    """Find the catalogued row again, preferring an exact folder-path match.

    The same report name appears under several folders, so the path is what
    distinguishes them. Matching on name alone would silently download a
    different report than the flow was built for.
    """
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
    selector = (
        f"[id='{_css_escape(entry['element_id'])}']" if entry.get("element_id")
        else f"text={entry['name']}"
    )
    target = (root or page).locator(selector).first
    target.click(force=True, timeout=60_000)


def excel_button_id(automation: dict | None = None) -> str:
    configured = str((automation or {}).get("excel_btn_id") or "").strip()
    return configured or FALLBACK_EXCEL_BUTTON_ID


def trigger_excel_export(page, job: dict) -> None:
    """Click the MDI toolbar's Excel button for the active work frame."""
    automation = (job.get("report") or {}).get("automation") or {}
    clear_screen(page)
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
    raise RuntimeError(
        "GSCM's Excel export button was not found on the toolbar. The bookmark "
        "may have opened a screen that cannot export. On screen: "
        + screen_inventory(page)
    )
