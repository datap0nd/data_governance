"""GSCM (Nexacro) portal adapter: bookmark discovery and Excel export.

GSCM (`mdscm.sec.samsung.net`) is a TOBESOFT Nexacro client, not an HTML
application. Nothing on the page is a link, a table, or a form control: every
element is a Nexacro component whose DOM ``id`` is its absolute path in the
component tree, for example::

    mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown

That shape drives three decisions in this module:

* **Bookmarks, not menus.** ASAP is catalogued by walking its menu tree. GSCM
  users instead save a fully configured report - filters, period, dimensions -
  as a *favorite* on the portal home screen. One favorite is one catalog
  report, so discovery reads the favorites list rather than the sitemap. The
  filters live inside GSCM, which is why a discovered GSCM report carries no
  Metronome prompts.
* **Ids are matched by shape, not by literal.** The reverse-engineering notes
  give exact ids, but the segment naming the home-screen section
  (``div_section4_MOBILE``) is layout-dependent. Every lookup here matches the
  stable trailing component name and only falls back to the full literal path.
* **The framework fights automation.** Nexacro parks a full-screen wait
  overlay over the page and floats un-anchored popup cards above everything
  else. Both swallow clicks and both can outlive the work that raised them, so
  every interaction clears them first and clicks with ``force``.

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

#: Component name of the label inside one favorites card. The card container is
#: this element's grandparent in the component path.
FAVORITE_LABEL_COMPONENT = "stc_userreportname"

#: Component name of the global Excel export button on the MDI toolbar.
EXCEL_BUTTON_COMPONENT = "btn_exceldown"

#: Documented absolute paths, used only when the shape match finds nothing.
FALLBACK_FAVORITE_LIST_ID = (
    "mainframe.VFrameSet.HomeFrame.form.div_main.form.div_section4_MOBILE.form"
    ".div_favorite.form.div_favorite.form.div_list.form"
)
FALLBACK_EXCEL_BUTTON_ID = (
    "mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown"
)

PORTAL_READY_TIMEOUT_MS = 180_000
BOOKMARK_SETTLE_TIMEOUT_MS = 300_000
#: The wait overlay flickers between backend calls. Only treat the screen as
#: idle once it has stayed hidden across this many consecutive polls.
IDLE_POLLS_REQUIRED = 6
IDLE_POLL_INTERVAL_MS = 500
#: Nexacro renders the grid a beat after the overlay clears.
POST_IDLE_SETTLE_MS = 3_000

DISCOVERY_CATEGORY = "Favorites"
DOWNLOAD_TEXT = "Excel download"

_UNSAFE_NAME_RE = re.compile(r"[\x00-\x1f]+")


# ── Page primitives ──

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

_FAVORITES_JS = """(component) => {
    const byCard = new Map();
    for (const element of document.querySelectorAll(`[id*='${component}']`)) {
        const id = element.id || '';
        const marker = `.form.${component}`;
        const cut = id.indexOf(marker);
        if (cut < 0) continue;
        const cardId = id.slice(0, cut);
        if (!cardId) continue;
        const text = (element.textContent || '').trim();
        const previous = byCard.get(cardId);
        if (!previous || text.length > previous.name.length) {
            byCard.set(cardId, { card_id: cardId, label_id: id.split(':')[0], name: text });
        }
    }
    return Array.from(byCard.values());
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


def unlock_wait_window(page) -> bool:
    """Force the Nexacro wait overlay out of the way.

    The overlay owns every mouse event while it is up. Hiding it is safe: it is
    a progress indicator, not a guard, and the backend call it represents keeps
    running either way.
    """
    return bool(page.evaluate(_HIDE_WAIT_WINDOW_JS, WAIT_WINDOW_ID))


def wait_window_visible(page) -> bool:
    return bool(page.evaluate(_WAIT_WINDOW_VISIBLE_JS, WAIT_WINDOW_ID))


def dismiss_popups(page) -> int:
    """Close floating Nexacro notification cards that block background clicks."""
    closed = 0
    for element_id in page.evaluate(_POPUP_CLOSERS_JS) or []:
        try:
            page.locator(f"[id='{_css_escape(element_id)}']").first.click(
                force=True, timeout=5_000,
            )
            closed += 1
        except Exception:
            # A popup that vanished on its own is the expected outcome, not a
            # failure: the sweep exists to clear the screen, not to audit it.
            continue
    return closed


def clear_screen(page) -> None:
    """One call for the two things that must be true before any click."""
    unlock_wait_window(page)
    dismiss_popups(page)
    unlock_wait_window(page)


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def portal_url(job: dict) -> str:
    """The address that renders the GSCM home screen with its favorites list."""
    site = job.get("site") or {}
    report = job.get("report") or {}
    for candidate in (report.get("url"), site.get("auth_url"), site.get("base_url")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    raise RuntimeError("GSCM has no portal URL. Set the website's base or authentication URL.")


def open_portal(page, url: str, *, timeout_ms: int = PORTAL_READY_TIMEOUT_MS) -> None:
    """Land on the GSCM home screen, reusing an already-open portal tab.

    Re-navigating a live Nexacro session tears down the whole component tree
    and replays the SSO handshake, so only navigate when the page is not
    already inside the portal.
    """
    host = _host(url)
    if not host or host not in _host(page.url or ""):
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    wait_for_component(page, "mainframe.VFrameSet", timeout_ms=timeout_ms)
    clear_screen(page)


def _host(url: str) -> str:
    match = re.match(r"^https?://([^/]+)", str(url or "").strip(), re.IGNORECASE)
    return match.group(1).casefold() if match else ""


def wait_for_component(page, component_id: str, *, timeout_ms: int) -> None:
    """Wait for one Nexacro component to exist in the DOM.

    Nexacro compiles its tree after the document is ready, so a loaded page
    proves nothing about the application being up.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if page.evaluate(
                "(id) => !!document.getElementById(id)", component_id,
            ):
                return
        except Exception as exc:  # navigation in flight
            last_error = exc
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    detail = f" Last error: {last_error}" if last_error else ""
    raise RuntimeError(
        f"GSCM did not render its Nexacro client within {timeout_ms // 1000} seconds. "
        f"Component not found: {component_id}. Sign in to GSCM in the automation "
        f"browser profile, then retry.{detail}"
    )


def wait_for_calculation(
    page, *, timeout_ms: int = BOOKMARK_SETTLE_TIMEOUT_MS,
    report_progress=None,
) -> bool:
    """Block until the wait overlay has stayed down long enough to trust.

    Returns ``True`` when the screen went idle on its own and ``False`` when
    the budget ran out - the caller still proceeds, because a stuck overlay is
    the documented failure mode, not proof that the data is missing.
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
                report_progress("GSCM is running the bookmark's query.")
        else:
            idle_polls += 1
            if idle_polls >= IDLE_POLLS_REQUIRED:
                page.wait_for_timeout(POST_IDLE_SETTLE_MS)
                return True
        page.wait_for_timeout(IDLE_POLL_INTERVAL_MS)
    unlock_wait_window(page)
    page.wait_for_timeout(POST_IDLE_SETTLE_MS)
    return False


# ── Discovery ──


def read_favorites(page) -> list[dict]:
    """Every named bookmark card on the GSCM home screen.

    Cards are keyed by their container id (``...div_list.form.div_data01``),
    which is both the double-click target and a stable identity across scans.
    """
    records = page.evaluate(_FAVORITES_JS, FAVORITE_LABEL_COMPONENT) or []
    favorites = []
    seen: set[str] = set()
    for record in records:
        card_id = str(record.get("card_id") or "").strip()
        name = _clean_name(record.get("name"))
        if not card_id or not name or card_id in seen:
            continue
        seen.add(card_id)
        favorites.append({
            "card_id": card_id,
            "label_id": str(record.get("label_id") or "").strip() or None,
            "name": name,
        })
    favorites.sort(key=lambda item: (item["name"].casefold(), item["card_id"]))
    return favorites


def _clean_name(value: Any) -> str:
    name = _UNSAFE_NAME_RE.sub(" ", str(value or "")).strip()
    return re.sub(r"\s+", " ", name)[:200]


def excel_button_id(automation: dict | None = None) -> str:
    configured = str((automation or {}).get("excel_btn_id") or "").strip()
    return configured or FALLBACK_EXCEL_BUTTON_ID


def discovered_report(favorite: dict, report_url: str, catalog_name: str | None = None) -> dict:
    """One favorites card as a Metronome catalog entry.

    GSCM owns the filters, so the entry declares no prompts. That is what makes
    a GSCM flow a one-click download: the bookmark *is* the configuration.

    ``catalog_name`` disambiguates two favorites saved under the same label.
    The raw label is still stored as ``favorite_name`` so the run-time lookup
    keeps matching what GSCM actually renders.
    """
    name = favorite["name"]
    catalog_name = catalog_name or name
    return {
        "discovery_key": f"{DISCOVERY_CATEGORY} > {catalog_name}",
        "name": catalog_name,
        "report_url": report_url,
        "ready_text": None,
        "download_text": DOWNLOAD_TEXT,
        "automation": {
            "kind": "gscm_favorite",
            "category_path": [DISCOVERY_CATEGORY, catalog_name],
            "favorite_id": favorite["card_id"],
            "favorite_label_id": favorite.get("label_id"),
            "favorite_name": name,
            "excel_btn_id": FALLBACK_EXCEL_BUTTON_ID,
        },
        "filters": [],
    }


def discover_catalog(page, job: dict, report_progress) -> tuple[list[dict], bool]:
    """Catalog every GSCM bookmark visible to the signed-in user.

    Returns the discovered reports and whether the sweep was complete. A
    targeted scan (one report path) narrows the result but must report itself
    as incomplete so the server does not stale every other bookmark.
    """
    url = portal_url(job)
    report_progress("running", {
        "stage": "navigation",
        "message": "Opening the GSCM portal home screen for bookmark discovery.",
    })
    open_portal(page, url)

    report_progress("running", {
        "stage": "report_discovery",
        "message": "Reading the home-screen favorites list.",
    })
    favorites = _read_favorites_with_retry(page)
    if not favorites:
        raise RuntimeError(
            "No GSCM bookmarks were found on the home screen. Save at least one "
            "report as a favorite in GSCM, then scan again. If favorites are "
            "visible in your own browser, the automation profile is signed in as "
            "a different user."
        )

    discovery = job.get("discovery") or {}
    requested = {
        str(path[-1]).strip().casefold()
        for path in discovery.get("report_paths") or []
        if isinstance(path, list) and path
    }
    complete = not requested
    if requested:
        favorites = [item for item in favorites if item["name"].casefold() in requested]
        if not favorites:
            raise RuntimeError(
                "The requested GSCM bookmark is no longer on the home screen. "
                "Run a full scan to refresh the catalog."
            )

    report_progress("running", {
        "stage": "report_discovery",
        "message": f"Discovered {len(favorites)} GSCM bookmark(s): "
                   + ", ".join(item["name"] for item in favorites),
        "item_count": len(favorites),
    })
    return [
        discovered_report(item, url, catalog_name)
        for item, catalog_name in zip(favorites, _catalog_names(favorites))
    ], complete


def _catalog_names(favorites: list[dict]) -> list[str]:
    """Unique catalog names for bookmarks GSCM lets the user save twice.

    ``flow_reports`` is keyed by (site, name), so two favorites labelled the
    same would collapse into one catalog row and one of them would silently
    disappear from the catalog.
    """
    seen: dict[str, int] = {}
    names = []
    for item in favorites:
        key = item["name"].casefold()
        seen[key] = seen.get(key, 0) + 1
        names.append(item["name"] if seen[key] == 1 else f"{item['name']} ({seen[key]})")
    return names


def _read_favorites_with_retry(page, attempts: int = 3) -> list[dict]:
    """The favorites list is filled by a backend call after the frame renders."""
    favorites: list[dict] = []
    for attempt in range(attempts):
        favorites = read_favorites(page)
        if favorites:
            return favorites
        if attempt < attempts - 1:
            page.wait_for_timeout(5_000)
            clear_screen(page)
    return favorites


# ── Download ──


def open_bookmark(page, job: dict, report_progress=None) -> str:
    """Load the report behind one bookmark, applying its saved configuration."""
    automation = (job.get("report") or {}).get("automation") or {}
    favorite_id = str(automation.get("favorite_id") or "").strip()
    favorite_name = str(automation.get("favorite_name") or "").strip()
    if not favorite_id and not favorite_name:
        raise RuntimeError(
            "This GSCM report has no bookmark reference. Scan the GSCM catalog again."
        )

    open_portal(page, portal_url(job))
    target_id = _resolve_favorite_id(page, favorite_id, favorite_name)
    if report_progress:
        report_progress(f"Opening GSCM bookmark {favorite_name or target_id}.")
    clear_screen(page)
    page.locator(f"[id='{_css_escape(target_id)}']").first.dblclick(
        force=True, timeout=60_000,
    )
    wait_for_calculation(page, report_progress=report_progress)
    clear_screen(page)
    return target_id


def _resolve_favorite_id(page, favorite_id: str, favorite_name: str) -> str:
    """Prefer the catalogued card id; fall back to matching the label text.

    GSCM renumbers the ``div_dataNN`` cards when the user reorders favorites,
    so a stored id can point at the wrong report after a reshuffle. Matching
    the name keeps a flow pointing at the report the user chose.
    """
    favorites = _read_favorites_with_retry(page)
    matches = [
        item for item in favorites
        if favorite_name and item["name"].casefold() == favorite_name.casefold()
    ]
    if len(matches) > 1:
        # Two favorites share this label, so the name no longer identifies one
        # of them. The catalogued card id is the only remaining discriminator.
        for item in matches:
            if item["card_id"] == favorite_id:
                return favorite_id
        return matches[0]["card_id"]
    if matches:
        return matches[0]["card_id"]
    if favorite_id and any(item["card_id"] == favorite_id for item in favorites):
        return favorite_id
    if favorite_id and not favorites:
        # Discovery could not read the list; trust the catalogued id and let
        # the click surface the real failure.
        return favorite_id
    available = ", ".join(item["name"] for item in favorites) or "none"
    raise RuntimeError(
        f"GSCM bookmark {favorite_name or favorite_id!r} is not on the home screen. "
        f"Available bookmarks: {available}. Re-scan the GSCM catalog."
    )


def trigger_excel_export(page, job: dict) -> None:
    """Click the MDI toolbar's Excel button for the active work frame."""
    automation = (job.get("report") or {}).get("automation") or {}
    clear_screen(page)
    button = page.locator(f"[id='{_css_escape(excel_button_id(automation))}']").first
    if not button.count():
        button = page.locator(f"[id*='{EXCEL_BUTTON_COMPONENT}']").first
    if not button.count():
        raise RuntimeError(
            "GSCM's Excel export button was not found on the toolbar. The bookmark "
            "may have opened a screen that cannot export."
        )
    button.click(force=True, timeout=60_000)
