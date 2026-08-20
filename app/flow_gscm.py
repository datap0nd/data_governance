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
#: The dialog is opened by a gear icon with no text, so it is the one control
#: that must be found by id shape.
SETTING_BUTTON_HINTS = ("btn_setting", "btn_set", "btn_config", "setting", "config")
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
MAX_INVENTORY_ITEMS = 120

DOWNLOAD_TEXT = "Excel download"

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


def visible_text(page) -> list[tuple[Any, dict]]:
    """Every visible label on screen, paired with the root that renders it."""
    items = []
    for root, records in _evaluate_everywhere(page, _VISIBLE_TEXT_JS):
        for record in records:
            items.append((root, record))
    return items


def screen_inventory(page, *, keyword: str | None = None) -> str:
    """A compact dump of what is actually on screen, for failure messages.

    Selector work against an undocumented Nexacro screen is guesswork until
    something reports the real ids. Every failure in this module carries this
    so one test run is enough to write the exact selector.
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


def find_by_label(page, labels) -> list[tuple[Any, dict]]:
    """Visible elements whose whole text is one of ``labels``.

    Smallest first: a tab's own label is a tighter box than the panel that
    contains it, and clicking the tight box is what selects the tab.
    """
    wanted = {_normalize_label(label) for label in labels}
    matches = [
        (root, record) for root, record in visible_text(page)
        if _normalize_label(record.get("text")) in wanted
    ]
    matches.sort(key=lambda item: item[1].get("w", 0) * item[1].get("h", 0))
    return matches


def click_label(page, labels, *, timeout_ms: int = 30_000) -> dict | None:
    """Click the tightest visible control carrying one of ``labels``."""
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


def click_by_id_hint(page, hints, *, timeout_ms: int = 30_000) -> dict | None:
    """Click an icon-only control located by the shape of its component id."""
    clear_screen(page)
    candidates = []
    for root, records in _evaluate_everywhere(page, _ID_MATCH_JS, list(hints)):
        for record in records:
            candidates.append((root, record))
    # A toolbar gear sits at the top of the screen; prefer the topmost match.
    candidates.sort(key=lambda item: (item[1].get("y", 0), -item[1].get("x", 0)))
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


def wait_for_component(page, component_id: str, *, timeout_ms: int) -> None:
    """Wait for one Nexacro component to exist in any frame.

    Nexacro compiles its tree after the document is ready, so a loaded page
    proves nothing about the application being up.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if _evaluate_everywhere(page, "(id) => !!document.getElementById(id)", component_id):
            return
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    raise RuntimeError(
        f"GSCM did not render its Nexacro client within {timeout_ms // 1000} seconds. "
        f"Component not found: {component_id}. Sign in to GSCM in the automation "
        f"browser profile, then retry. On screen: {screen_inventory(page)}"
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
    if click_by_id_hint(page, SETTING_BUTTON_HINTS) is None:
        # Some builds expose a labelled entry point instead of a bare gear.
        click_label(page, ["Setting", "Settings"])
    page.wait_for_timeout(TAB_SETTLE_MS)
    click_label(page, [FAVORITE_PANEL_LABEL])
    page.wait_for_timeout(TAB_SETTLE_MS)
    clear_screen(page)

    deadline = time.monotonic() + DIALOG_READY_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if favorites_dialog_open(page):
            return
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    raise RuntimeError(
        "GSCM's Setting > Favorite dialog did not open, so its bookmark tabs "
        "(Private, Public, Custom) were never reachable. On screen: "
        + screen_inventory(page)
    )


def select_scope_tab(page, tab: str) -> bool:
    """Switch the Favorite panel to one of Private / Public / Custom."""
    if click_label(page, [tab]) is None:
        return False
    page.wait_for_timeout(TAB_SETTLE_MS)
    clear_screen(page)
    return True


def read_favorite_tree(page) -> list[dict]:
    """Rebuild the folder tree currently shown in the Favorite panel.

    The tree is a Nexacro grid with no semantic nesting in the DOM: depth is
    expressed purely as horizontal indentation. Rows are therefore ordered by
    their vertical position and nested by comparing left edges, which is how
    the screen itself communicates the hierarchy.
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
        })
    if not rows:
        return []

    # The tree occupies the densest vertical band on screen. Keep the rows that
    # share a left-edge family with the majority, so page chrome elsewhere in
    # the dialog does not become a phantom folder.
    rows.sort(key=lambda row: (row["y"], row["x"]))
    stack: list[tuple[int, str]] = []
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
            "root": row["root"],
        })
        stack.append((row["x"], row["name"]))
    return entries


def _leaf_entries(entries: list[dict]) -> list[dict]:
    """Rows that no deeper row nests under: the reports, not the folders."""
    leaves = []
    for index, entry in enumerate(entries):
        following = entries[index + 1] if index + 1 < len(entries) else None
        if following and following["indent"] > entry["indent"] + INDENT_TOLERANCE_PX:
            continue  # a folder: the next row is indented under it
        leaves.append(entry)
    return leaves


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
        leaves = _leaf_entries(read_favorite_tree(page))
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
    leaves = _leaf_entries(read_favorite_tree(page))
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
