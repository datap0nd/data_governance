"""Exact, scoped GSCM Favorite-grid selection for recorded click steps.

This deliberately does not reopen GSCM, activate a tab, expand folders, or
press Go.  The recording owns all of those actions.  Native selection remains
disabled until a work-PC qualification is stored with a future target.

Fallback navigation is limited to the known Favorite grid and uses only its
own scrollbar buttons: check the current view, walk to the confirmed top, then
move downward one overlapping page at a time.  Every movement waits for an
observed rendered-row change, rows are reacquired after each repaint, and the
step stops on the target, a confirmed end, a stalled scrollbar, cancellation,
or the 120-second deadline.  Mouse-wheel replay, forced clicks, cached row
handles and whole-page searches are never used.
"""
from __future__ import annotations

import time

from app import flow_gscm

TARGET_KIND = "gscm_favorite"
CAPABILITY = "gscm_bookmark_targets_v1"
DEADLINE_SECONDS = 120
#: Bounded wait for a rendered-row/scroll-position change after one movement.
RENDER_WAIT_SECONDS = 3.0
RENDER_POLL_MS = 100
#: Scrollbar presses per movement.  The Favorite view shows about nine rows, so
#: eight presses keep one rendered row of overlap between adjacent views.
PAGE_STEPS = flow_gscm.FAVORITE_SCROLL_PAGE_STEPS
#: A movement that produces no change is retried once before the position is
#: treated as a confirmed end, so a momentary stall is not mistaken for it.
END_CONFIRMATIONS = 2
CLICK_TIMEOUT_MS = 15_000


class BookmarkSelectionError(RuntimeError):
    """A failure that carries the helper's structured log for diagnostics."""

    def __init__(self, message: str, log: dict):
        super().__init__(message)
        self.diagnostic = {"bookmark": dict(log)}


def is_target(step: dict) -> bool:
    return isinstance(step.get("bookmark_target"), dict) and step["bookmark_target"].get("kind") == TARGET_KIND


def validate_target(step: dict) -> None:
    target = step.get("bookmark_target")
    if target is None:
        return
    if not isinstance(target, dict) or target.get("kind") != TARGET_KIND:
        raise ValueError("Unsupported recorded target type.")
    if step.get("action") != "click":
        raise ValueError("A GSCM Favorite target must be a click step.")
    name = target.get("bookmark_name")
    if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 300:
        raise ValueError("GSCM Favorite targets require the exact bookmark name.")
    for key in ("bookmark_id", "scope"):
        value = target.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 300):
            raise ValueError(f"Invalid GSCM Favorite {key}.")
    if target.get("native_strategy") is not None:
        raise ValueError("Native GSCM bookmark selection is not qualified on this worker.")


def _folder_key(path) -> tuple:
    return tuple(flow_gscm._clean_name(part).casefold() for part in (path or []) if flow_gscm._clean_name(part))


def _identity(page, target: dict, log: dict) -> tuple:
    """Resolve exactly one current-scope dataset identity; never use a row slot.

    Returns the identity and the folder keys of same-named competitors, which
    the rendered-row check must be able to rule out before clicking.
    """
    name = target["bookmark_name"]
    scope = target.get("scope")
    wanted_id = target.get("bookmark_id")
    entries = flow_gscm.bookmark_dataset_entries(page)
    if entries is None:
        log["reason"] = "dataset_unavailable"
        raise BookmarkSelectionError("GSCM Favorite data is unavailable in the current frame. Check that the recorded steps opened the Favorite list on this page.", log)
    same_name = [entry for entry in entries if entry.get("identity_name") == name
                 and (not scope or entry.get("tab") == scope)]
    log["collided"] = len(same_name) > 1
    matches = same_name
    if wanted_id:
        matches = [entry for entry in matches if entry.get("bookmark_id") == wanted_id]
    if not matches:
        log["reason"] = "identity_missing"
        raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} is not in the current scope. Check the recorded folder/tab steps and repair the target.", log)
    if len(matches) != 1:
        log["reason"] = "identity_ambiguous"
        raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} is ambiguous in the current scope. Repair this target with its stable bookmark ID.", log)
    identity = matches[0]
    if not identity.get("bookmark_id"):
        log["reason"] = "identity_unstable"
        raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} has no stable identity. Re-record or repair this target.", log)
    competitors = [_folder_key(entry.get("folder_path")) for entry in same_name if entry is not identity]
    if _folder_key(identity.get("folder_path")) in competitors:
        # The rendered grid can only tell same-named rows apart by the folder
        # rows above them.  Two bookmarks in one folder are indistinguishable
        # on screen, so stop instead of clicking whichever renders first.
        log["reason"] = "duplicates_share_folder"
        raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} exists more than once in the same folder, so its rendered row cannot be told apart. Rename one bookmark in GSCM or record the other route.", log)
    return identity, competitors


def _rendered_rows(page, name: str) -> list:
    return [(root, row) for root, row in flow_gscm.favorite_tree_rows(page)
            if str(row.get("text") or "").strip() == name]


def _folder_confirms(visible: tuple, wanted: tuple) -> bool:
    return bool(visible) and len(visible) <= len(wanted) and wanted[len(wanted) - len(visible):] == visible


def _rendered_target(page, identity: dict, competitors: list, log: dict):
    """Find the freshly rendered row for this identity, or ``(None, None)``."""
    name = identity["identity_name"]
    rows = _rendered_rows(page, name)
    if not rows:
        return None, None
    if not log.get("collided"):
        if len(rows) == 1:
            return rows[0]
        log["reason"] = "rendered_ambiguous"
        raise BookmarkSelectionError(f"GSCM rendered more than one row named {name!r} although its data holds one. Retest after the grid settles.", log)
    # Names collide: accept a rendered row only when the visible folder rows
    # above it confirm this identity's folder path and rule out every
    # same-named competitor.  Ancestors scrolled out of view never confirm.
    wanted = _folder_key(identity.get("folder_path"))
    element_ids = {str(row.get("id") or "") for _root, row in rows}
    confirmed = []
    for entry in flow_gscm.read_favorite_tree(page):
        if entry.get("element_id") not in element_ids:
            continue
        visible = _folder_key(entry.get("folder_path"))
        if _folder_confirms(visible, wanted) and not any(_folder_confirms(visible, other) for other in competitors):
            confirmed.append(entry["element_id"])
    if len(confirmed) == 1:
        log["folder_confirmed"] = True
        return next((root, row) for root, row in rows if str(row.get("id") or "") == confirmed[0])
    if len(confirmed) > 1:
        log["reason"] = "rendered_ambiguous"
        raise BookmarkSelectionError(f"GSCM rendered more than one row named {name!r} under the same visible folder. Retest after the grid settles.", log)
    return None, None


def _click_rendered(root, row: dict) -> None:
    element_id = str(row.get("id") or "")
    if not element_id:
        raise RuntimeError("GSCM rendered the bookmark without a usable row element; retry after rendering settles.")
    # Element IDs are generated by Nexacro, but this exact freshly rendered
    # element is only used for a normal single click, never as identity.
    root.locator("[id=" + repr(element_id) + "]").first.click(timeout=CLICK_TIMEOUT_MS)


def _scrollbar_button(page, part: str):
    """The current Favorite grid's own scrollbar button, or ``None``."""
    suffix = flow_gscm.FAVORITE_GRID_ID_SUFFIX + ".vscrollbar." + part
    for root in flow_gscm._roots(page):
        try:
            for selector in ("[id*='" + suffix + ":icontext']", "[id*='" + suffix + "']"):
                button = root.locator(selector).first
                if button.count():
                    return button
        except Exception:
            continue
    return None


def _move(page, button, deadline: float, log: dict) -> bool:
    """Press one scrollbar button for a page and wait for an observed repaint."""
    before = flow_gscm._tree_row_signature(page)
    for _step in range(PAGE_STEPS):
        button.click(timeout=CLICK_TIMEOUT_MS)
        log["presses"] = log.get("presses", 0) + 1
        page.wait_for_timeout(40)
    waited = time.monotonic()
    while True:
        if flow_gscm._tree_row_signature(page) != before:
            log["movements"] = log.get("movements", 0) + 1
            log["movement"] = True
            return True
        now = time.monotonic()
        if now - waited >= RENDER_WAIT_SECONDS or now >= deadline:
            return False
        page.wait_for_timeout(RENDER_POLL_MS)


def select(page, step: dict, progress=None) -> dict:
    """Reveal and click a bookmark without changing the recorded journey.

    ``progress`` is the recording's existing progress channel; it raises when
    the run is cancelled, so it is called before every movement.
    """
    target = step["bookmark_target"]
    name = target["bookmark_name"]
    started = time.monotonic()
    deadline = started + DEADLINE_SECONDS
    log = {"strategy": "favorite-scrollbar", "movement": False, "rendered": False,
           "movements": 0, "presses": 0, "top_established": False, "collided": False,
           "folder_confirmed": False, "reason": None, "scope": target.get("scope")}

    def heartbeat(phase: str) -> None:
        if progress:
            progress({"phase": phase, "bookmark": dict(log)})
        if time.monotonic() >= deadline:
            log["reason"] = "deadline"
            raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} was not rendered within the 120-second step deadline.", log)

    identity, competitors = _identity(page, target, log)
    name = identity["identity_name"]
    log["scope"] = identity.get("tab") or log["scope"]
    heartbeat("gscm_bookmark_resolved")

    root, row = _rendered_target(page, identity, competitors, log)
    if row is not None:
        log["strategy"] = "favorite-visible-row"
    else:
        decrement = _scrollbar_button(page, "decbutton")
        increment = _scrollbar_button(page, "incbutton")
        if decrement is None and increment is None:
            log["reason"] = "no_scrollbar"
            raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} is not rendered and the current Favorite grid has no scrollbar. Its ancestor folder may be collapsed or the recorded steps reached another frame; restore the recorded folder state and retry.", log)
        # Establish the top first: a target above the current view can never
        # be reached by moving downward.
        while decrement is not None:
            heartbeat("gscm_bookmark_to_top")
            if not _move(page, decrement, deadline, log):
                break
            root, row = _rendered_target(page, identity, competitors, log)
            if row is not None:
                break
        log["top_established"] = row is None
        unchanged = 0
        while row is None:
            heartbeat("gscm_bookmark_sweep")
            if increment is None:
                log["reason"] = "no_scrollbar"
                raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} is not rendered from the top of the current Favorite grid and it has no increment button. Its ancestor folder may be collapsed; restore the recorded folder state and retry.", log)
            if _move(page, increment, deadline, log):
                unchanged = 0
                root, row = _rendered_target(page, identity, competitors, log)
                continue
            unchanged += 1
            if unchanged >= END_CONFIRMATIONS:
                log["reason"] = "end_confirmed" if log["movements"] else "scrollbar_stalled"
                detail = " (the grid scrollbar did not move)" if not log["movements"] else ""
                raise BookmarkSelectionError(f"GSCM Favorite bookmark {name!r} was not rendered between the top and the end of the current Favorite list{detail}. Its ancestor folder may be collapsed; restore the recorded folder state and retry.", log)
    log["rendered"] = True
    _click_rendered(root, row)
    result = {**log, "bookmark_id": identity["bookmark_id"], "bookmark_name": name}
    if progress:
        progress({"phase": "gscm_bookmark_selected", "bookmark": dict(log)})
    return result
