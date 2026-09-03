"""GSCM portal adapter: bookmark discovery, catalog wiring, and flow runs.

GSCM is a separate website from ASAP with its own client framework and its own
data. Its bookmarks live in the Setting > Favorite dialog, split across the
Private, Public, and Custom tabs, each holding a folder tree that is nested by
on-screen indentation rather than by DOM structure. These tests pin that model
against a fake Nexacro screen, plus the catalog and flow wiring around it.
"""

import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database, flow_gscm
from app.routers import flows


@pytest.fixture()
def flow_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flows.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def _request(actor="Analyst"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


# ── A fake Nexacro screen ──
#
# The real dialog renders every label as its own absolutely-positioned element.
# The fake mirrors that: a list of (id, text, x, y) records that the adapter
# reads back through its visible-text script.

EXCEL_BUTTON = flow_gscm.FALLBACK_EXCEL_BUTTON_ID
GEAR_ID = "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting"
SETTING_SHELL_ID = "mainframe.VFrameSet.TopFrame.Setting0"
SETTING_FORM_ID = f"{SETTING_SHELL_ID}.form"
FAVORITE_PANEL_ID = f"{SETTING_SHELL_ID}.form.div_favorite"
FAVORITE_FORM_ID = f"{FAVORITE_PANEL_ID}.form"
FAVORITE_GRID_ID = f"{FAVORITE_FORM_ID}.grd_bookmark"
LOADED_TITLE_ID = (
    "mainframe.VFrameSet.WorkFrame0.form.div_report.form.sta_bookmarkTitle:text"
)

#: Indentation levels the real tree uses, in pixels.
ROOT_X, FOLDER_X, LEAF_X = 780, 800, 820


def _label(text, x, y, element_id=None):
    return {"id": element_id or f"cmp.{re.sub(r'[^A-Za-z0-9]+', '_', text)}_{y}",
            "text": text, "x": x, "y": y, "w": 120, "h": 18}


PUBLIC_TREE = [
    _label("SCM", ROOT_X, 560),
    _label("Actual Sales", FOLDER_X, 584),
    _label("MENA_Actual_sales", LEAF_X, 608),
    _label("MX B2B Actual Sales", LEAF_X, 630),
    _label("B2B Biz Plan", FOLDER_X, 654),
    _label("B2B BO Fcst V2", LEAF_X, 678),
]
PRIVATE_TREE = [
    _label("My reports", ROOT_X, 560),
    _label("Biz_trip_GSCM", FOLDER_X, 584),
]
CUSTOM_TREE = []

#: A report row shows an open icon and a pin icon on its right; a folder row
#: shows neither. That is how the adapter tells them apart.
LEAF_NAMES = {
    "MENA_Actual_sales", "MX B2B Actual Sales", "B2B BO Fcst V2",
    "Biz_trip_GSCM", "Weekly PSI", "CS_IRAN", "CS_SEEG",
}


def _icons_for(rows):
    """Left glyph on every row; the open and pin pair only on report rows."""
    icons = []
    for row in rows:
        icons.append({
            "id": f"{row['id']}.glyph", "x": row["x"] - 18, "y": row["y"], "w": 14, "h": 14,
        })
        if row["text"] not in LEAF_NAMES:
            continue
        icons.append({"id": f"{row['id']}.open", "x": 1290, "y": row["y"], "w": 14, "h": 14})
        icons.append({"id": f"{row['id']}.pin", "x": 1322, "y": row["y"], "w": 14, "h": 14})
    return icons

DIALOG_CHROME = [
    _label("Favorite", 600, 500, f"{SETTING_FORM_ID}.btn_favorite:text"),
    _label("Layout", 600, 600, f"{SETTING_FORM_ID}.btn_layout:text"),
    _label("Dashboard", 600, 700, f"{SETTING_FORM_ID}.btn_dashboard:text"),
    _label("Installation", 600, 800, f"{SETTING_FORM_ID}.btn_installation:text"),
    _label("Private", 900, 500, f"{FAVORITE_FORM_ID}.btn_private:text"),
    _label("Public", 975, 500, f"{FAVORITE_FORM_ID}.btn_public:text"),
    _label("Custom", 1045, 500, f"{FAVORITE_FORM_ID}.btn_custom:text"),
    _label("Alphabet", 810, 535, f"{FAVORITE_FORM_ID}.btn_alphabet:text"),
    _label("Latest", 900, 535, f"{FAVORITE_FORM_ID}.btn_latest:text"),
    _label("Unselect", 1340, 535, f"{FAVORITE_FORM_ID}.btn_unselect:text"),
    _label("Go >>", 1190, 945, f"{flow_gscm.GO_BUTTON_ID}:text"),
    _label("Save", 1270, 945, f"{FAVORITE_FORM_ID}.btn_save:text"),
    _label("Close", 1350, 945, f"{FAVORITE_FORM_ID}.btn_close:text"),
]


class FakeLocator:
    def __init__(self, page, selector, matches):
        self.page = page
        self.selector = selector
        self.matches = matches

    @property
    def first(self):
        return self

    def count(self):
        return len(self.matches)

    def click(self, **kwargs):
        if not self.matches:
            raise RuntimeError(f"no element for {self.selector}")
        self.page.on_click(self.matches[0])


class FakeFrame:
    """One additional Playwright root backed by the same Nexacro page state."""

    def __init__(self, page, components=()):
        self.page = page
        self.components = set(components)

    def locator(self, selector):
        return self.page._locator(selector, self.components)

    def evaluate(self, script, argument=None):
        return self.page._evaluate(script, argument, self.components)


class FakeGscmPage:
    """Enough of a Playwright page to drive the adapter without a browser."""

    def __init__(self, *, trees=None, dialog_open=False, gear=True,
                 url="https://mdscm.sec.samsung.net/nexa/index.html",
                 always_busy=False, busy_polls=0, popup_ids=(),
                 hidden_rows=None, gear_id=None, scroll_rows=None,
                 dataset_rows=None, popup_records=None,
                 popup_dom_noop_ids=(), popup_persistent_ids=(),
                 popup_cascades=None, setting_open=None,
                 frame_components=(), grid_bound=True,
                 dataset_selection_mode="success", loaded_report_titles=None):
        # hidden_rows: {folder name: [rows revealed when that folder is clicked]}
        self.hidden_rows = dict(hidden_rows or {})
        # scroll_rows: rows that only exist once the tree has been paged down.
        self.scroll_rows = list(scroll_rows or [])
        self.scrolled = set()
        self.gear_id = gear_id or GEAR_ID
        self.revealed = {}
        self.trees = trees if trees is not None else {
            "Private": PRIVATE_TREE, "Public": PUBLIC_TREE, "Custom": CUSTOM_TREE,
        }
        self.dialog_open = dialog_open
        self.setting_open = dialog_open if setting_open is None else setting_open
        self.gear = gear
        self.url = url
        self.always_busy = always_busy
        self.busy_polls = busy_polls
        self.popups = [dict(record) for record in (popup_records or [])]
        self.popups.extend({
            "container_id": element_id,
            "x": 1100, "y": 0, "w": 300, "h": 160,
            "closers": [{
                "id": element_id, "text": "", "x": 1370, "y": 10,
                "w": 20, "h": 20,
            }],
        } for element_id in popup_ids)
        self.popup_dom_noop_ids = {
            flow_gscm._component_element_ids(value)[0]
            for value in popup_dom_noop_ids
        }
        self.popup_persistent_ids = {
            flow_gscm._component_element_ids(value)[0]
            for value in popup_persistent_ids
        }
        self.popup_cascades = dict(popup_cascades or {})
        self.dataset_rows = dataset_rows
        self.grid_bound = grid_bound
        self.dataset_selection_mode = dataset_selection_mode
        self.selected_bookmark_id = None
        self.selected_bookmark_name = None
        self.selected_row_index = None
        self.grid_current_row = None
        self.selected_rows = []
        self.selection_attempts = 0
        self.guarded_go_attempts = 0
        self.guarded_go_fires = 0
        self.guarded_export_attempts = 0
        self.guarded_export_fires = 0
        self.loaded_report_titles = (
            None if loaded_report_titles is None
            else [str(value) for value in loaded_report_titles]
        )
        self.tab = "Public"
        self.clicks = []
        self.navigations = []
        self.wait_window_hidden = 0
        self.waits = []
        self.components = {"mainframe.VFrameSet", EXCEL_BUTTON}
        if gear:
            self.components.add(self.gear_id)
        self._frames = [FakeFrame(self, items) for items in frame_components]

    # -- state the adapter drives --

    def on_click(self, element_id):
        self.clicks.append(element_id)
        popup = self._popup_for_closer(element_id)
        if popup is not None:
            component_id = flow_gscm._component_element_ids(element_id)[0]
            if component_id not in self.popup_dom_noop_ids:
                self._dismiss_popup(popup, component_id)
            return
        clicked_component = flow_gscm._component_element_ids(element_id)[0]
        record = next((
            item for item in self._screen()
            if item["id"] == element_id
            or flow_gscm._component_element_ids(item["id"])[0] == clicked_component
        ), None)
        text = (record or {}).get("text", "")
        if element_id == self.gear_id or text == "Setting":
            self.setting_open = True
        elif text == "Favorite" and self.setting_open:
            self.dialog_open = True
        elif text in self.trees:
            self.tab = text
        elif text == "Close":
            self.dialog_open = False
            self.setting_open = False
        elif text == "Go >>" or element_id == flow_gscm.GO_BUTTON_ID:
            self.dialog_open = False
            self.setting_open = False
        elif text in self.hidden_rows:
            revealed = self.revealed.setdefault(self.tab, [])
            for row in self.hidden_rows.pop(text):
                if row not in revealed:
                    revealed.append(row)

    def _rows(self):
        # Rows revealed by expanding a folder, and rows below the fold, belong
        # to the tab they were found in - switching tabs shows a different set.
        base = self.trees.get(self.tab, [])
        if not base:
            return []
        rows = [*base, *self.revealed.get(self.tab, [])]
        if self.tab in self.scrolled:
            rows = [*rows, *self.scroll_rows]
        return rows

    def _screen(self):
        if self.dialog_open:
            return [*DIALOG_CHROME, *self._rows()]
        if self.setting_open:
            return list(DIALOG_CHROME[:4])
        if not self.dialog_open:
            screen = [_label("Favorite", 1480, 447)]  # the empty home widget
            for index, title in enumerate(self.loaded_report_titles or []):
                screen.append(_label(
                    title, 260, 110 + index * 22,
                    LOADED_TITLE_ID.replace(":text", f"_{index}:text"),
                ))
            return screen
        return []

    def _visible_components(self, components=None):
        visible = set(self.components if components is None else components)
        if not self.dialog_open:
            # Nexacro can leave component-tree records mounted after the
            # Favorite panel closes.  They are not visible controls and must
            # not make dialog predicates (or Go verification) report success.
            panel = re.compile(
                r"TopFrame\.Setting\d+\.form\.div_favorite(?:\.|$)", re.I,
            )
            visible = {
                item for item in visible
                if not panel.search(str(item))
            }
        if components is None:
            if self.setting_open:
                visible.add(SETTING_SHELL_ID)
            if self.dialog_open:
                visible.update({FAVORITE_PANEL_ID, FAVORITE_GRID_ID})
        visible.update(item["id"].split(":", 1)[0] for item in self._screen())
        return visible

    # -- Playwright surface --

    @property
    def frames(self):
        return list(self._frames)

    def goto(self, url, **_kwargs):
        self.navigations.append(url)
        self.url = url

    def wait_for_timeout(self, _ms):
        self.waits.append(_ms)

    def _popup_for_closer(self, element_id):
        component_id = flow_gscm._component_element_ids(element_id)[0]
        for popup in self.popups:
            for closer in popup.get("closers") or []:
                candidates = flow_gscm._component_element_ids(closer.get("id"))
                if candidates and candidates[0] == component_id:
                    return popup
        return None

    def _dismiss_popup(self, popup, component_id):
        if component_id in self.popup_persistent_ids:
            return
        if popup in self.popups:
            self.popups.remove(popup)
        self.popups.extend(
            dict(record) for record in self.popup_cascades.get(component_id, [])
        )

    def _popup_close_ids(self):
        return {
            element_id
            for popup in self.popups
            for closer in popup.get("closers") or []
            for element_id in flow_gscm._component_element_ids(closer.get("id"))
        }

    def _icon_records(self, components=None):
        if not self.dialog_open:
            visible = self._visible_components(components)
            return (
                [{"id": self.gear_id, "x": 1699, "y": 14, "w": 20, "h": 20}]
                if self.gear_id in visible else []
            )
        return _icons_for(self._rows())

    def _root_icon_records(self, components=None):
        try:
            return self._icon_records(components)
        except TypeError:
            # Existing purpose-built fakes predate multi-root support and
            # override _icon_records() without a root argument.
            return self._icon_records()

    def locator(self, selector):
        return self._locator(selector)

    def _locator(self, selector, components=None):
        visible_components = self._visible_components(components)
        if selector.startswith("[id='"):
            wanted = selector[len("[id='"):-len("']")].replace("\\'", "'").replace("\\\\", "\\")
            matches = [wanted] if any(
                item["id"] == wanted for item in self._screen()
            ) or wanted in visible_components or wanted in self._popup_close_ids() else []
        elif selector.startswith("[id*='"):
            fragment = selector[len("[id*='"):-len("']")]
            matches = sorted(item for item in visible_components if fragment in item)
        else:
            text = selector[len("text="):]
            matches = [item["id"] for item in self._screen() if item["text"] == text]
        return FakeLocator(self, selector, matches)

    def evaluate(self, script, argument=None):
        return self._evaluate(script, argument)

    def _evaluate(self, script, argument=None, components=None):
        visible_components = self._visible_components(components)
        if script == getattr(flow_gscm, "_FAVORITE_STATE_JS", None):
            scopes = {}
            for row in self.dataset_rows or []:
                raw = str(row.get("publicscope") or "").strip() or "(blank)"
                scopes[raw] = scopes.get(raw, 0) + 1
            dataset = (
                {"available": True, "rows": len(self.dataset_rows), "scopes": scopes}
                if self.dataset_rows is not None else None
            )
            grids = (
                [{"id": FAVORITE_GRID_ID, "rows": len(self._rows())}]
                if FAVORITE_GRID_ID in visible_components else []
            )
            return {
                "grids": grids,
                "dataset": dataset,
                "setting_shell": any(
                    re.search(r"TopFrame\.Setting\d+(?:\.|$)", item, re.I)
                    for item in visible_components
                ),
            }
        if script == getattr(flow_gscm, "_COMPONENT_PATH_MATCH_JS", None):
            pattern = re.compile(str((argument or {}).get("pattern") or ""), re.I)
            return any(pattern.search(item) for item in visible_components)
        if script == getattr(flow_gscm, "_VISIBLE_COMPONENT_IDS_JS", None):
            suffix = str((argument or {}).get("suffix") or "")
            return sorted({
                str(item).split(":", 1)[0]
                for item in visible_components
                if str(item).split(":", 1)[0].endswith(suffix)
            })
        if script == getattr(flow_gscm, "_RENDERED_REPORT_TITLES_JS", None):
            records = []
            for index, value in enumerate(self.loaded_report_titles or []):
                identifier = (
                    LOADED_TITLE_ID if index == 0
                    else LOADED_TITLE_ID.replace(":text", f"_{index}:text")
                )
                records.append({
                    "id": identifier.split(":", 1)[0],
                    "dom_id": identifier,
                    "text": str(value).strip(),
                    "rank": 0,
                    "x": 260,
                    "y": 110 + index * 22,
                })
            return records
        if script == getattr(flow_gscm, "_COMPONENT_VISIBLE_JS", None):
            fragments = [str(item).casefold() for item in argument or []]
            return any(
                any(fragment in identifier.casefold() for fragment in fragments)
                for identifier in visible_components
            )
        if script == getattr(flow_gscm, "_SELECT_BOOKMARK_ROW_JS", None):
            self.selection_attempts += 1
            wanted = str((argument or {}).get("bookmark_id") or "").strip()
            wanted_name = str((argument or {}).get("bookmark_name") or "").strip()
            assert (argument or {}).get("grid_id") == FAVORITE_GRID_ID
            mode = self.dataset_selection_mode
            if mode == "first_rejected_then_success" and self.selection_attempts > 1:
                mode = "success"
            reasons = {
                "missing_bind": "bound-dataset-unavailable",
                "rejected_rowposition": "rowposition-rejected",
                "id_mismatch": "post-selection-id-mismatch",
                "current_row_mismatch": "post-selection-id-mismatch",
                "ambiguous_grid": "ambiguous-favorite-grid",
                "ambiguous_selection": "post-selection-id-mismatch",
                "unsupported_selecttype": "unsupported-grid-selecttype",
                "first_rejected_then_success": "rowposition-rejected",
            }
            if mode in reasons or not self.grid_bound:
                return {
                    "selected": False,
                    "reason": reasons.get(
                        mode, "bound-dataset-unavailable",
                    ),
                    "grid_id": FAVORITE_GRID_ID,
                }
            matches = [
                (index, row) for index, row in enumerate(self.dataset_rows or [])
                if str(row.get("userreportid") or "").strip() == wanted
            ]
            if not matches:
                return {
                    "selected": False,
                    "reason": "bookmark-id-not-in-bound-dataset",
                    "grid_id": FAVORITE_GRID_ID,
                }
            if len(matches) != 1:
                return {
                    "selected": False,
                    "reason": "duplicate-bookmark-id",
                    "grid_id": FAVORITE_GRID_ID,
                    "matching_rows": [index for index, _row in matches],
                }
            matching = matches[0]
            observed_name = str(matching[1].get("userreportname") or "").strip()
            if observed_name != wanted_name:
                return {
                    "selected": False,
                    "reason": "bookmark-name-mismatch",
                    "grid_id": FAVORITE_GRID_ID,
                    "row_index": matching[0],
                    "observed_name": observed_name,
                }
            self.selected_bookmark_id = wanted
            self.selected_bookmark_name = observed_name
            self.selected_row_index = matching[0]
            self.grid_current_row = matching[0]
            self.selected_rows = [matching[0]]
            return {
                "selected": True,
                "strategy": "bound-dataset-exact-identity",
                "grid_id": FAVORITE_GRID_ID,
                "row_index": matching[0],
                "current_row": matching[0],
                "selected_rows": [matching[0]],
                "select_type": "row",
                "bookmark_id": wanted,
                "bookmark_name": wanted_name,
            }
        if script == getattr(flow_gscm, "_GUARDED_GO_CLICK_JS", None):
            self.guarded_go_attempts += 1
            request = argument or {}
            wanted = str(request.get("bookmark_id") or "").strip()
            wanted_name = str(request.get("bookmark_name") or "").strip()
            assert request.get("grid_suffix") == flow_gscm.FAVORITE_GRID_ID_SUFFIX
            assert request.get("grid_id") == FAVORITE_GRID_ID
            component_id = str(request.get("go_id") or "").split(":", 1)[0]
            matches = [
                (index, row) for index, row in enumerate(self.dataset_rows or [])
                if str(row.get("userreportid") or "").strip() == wanted
            ]
            if len(matches) != 1:
                return {
                    "fired": False,
                    "reason": "duplicate-bookmark-id" if matches
                    else "bookmark-id-not-in-bound-dataset",
                    "matching_rows": [index for index, _row in matches],
                }
            row_index, row = matches[0]
            observed_name = str(row.get("userreportname") or "").strip()
            if self.dataset_selection_mode == "guard_drift":
                self.grid_current_row = max(0, row_index - 1)
            selection_valid = (
                self.selected_row_index == row_index
                and self.grid_current_row == row_index
                and self.selected_rows == [row_index]
                and self.selected_bookmark_id == wanted
                and observed_name == wanted_name
            )
            if not selection_valid:
                return {
                    "fired": False,
                    "reason": "bookmark-selection-drift",
                    "row_position": self.selected_row_index,
                    "current_row": self.grid_current_row,
                    "selected_rows": list(self.selected_rows),
                    "observed_id": self.selected_bookmark_id or "",
                    "observed_name": observed_name,
                }
            if self.dataset_selection_mode == "guard_unavailable":
                return {
                    "fired": False,
                    "reason": "go-onclick-unavailable",
                    "component_id": component_id,
                }
            if component_id not in visible_components:
                return {
                    "fired": False,
                    "reason": "missing-go-component",
                    "component_id": component_id,
                }
            self.guarded_go_fires += 1
            if self.dataset_selection_mode != "guard_no_close":
                self.dialog_open = False
                self.setting_open = False
                if self.loaded_report_titles is None:
                    self.loaded_report_titles = [wanted_name]
            return {
                "fired": True,
                "strategy": "guarded-native-go",
                "component_id": component_id,
                "bookmark_id": wanted,
                "bookmark_name": wanted_name,
                "row_position": row_index,
                "current_row": row_index,
                "select_type": "row",
                "selected_rows": [row_index],
            }
        if script == getattr(flow_gscm, "_GUARDED_EXCEL_EXPORT_JS", None):
            self.guarded_export_attempts += 1
            request = argument or {}
            wanted_id = str(request.get("bookmark_id") or "").strip()
            wanted_name = str(request.get("bookmark_name") or "").strip()
            titles = [str(value).strip() for value in self.loaded_report_titles or []]
            if not titles:
                return {
                    "fired": False,
                    "reason": "loaded-report-title-unavailable",
                    "expected_id": wanted_id,
                    "expected_name": wanted_name,
                    "titles": [],
                }
            observed = list(dict.fromkeys(titles))
            if len(observed) != 1:
                return {
                    "fired": False,
                    "reason": "ambiguous-loaded-report-title",
                    "expected_id": wanted_id,
                    "expected_name": wanted_name,
                    "observed_names": observed,
                }
            if observed[0] != wanted_name:
                return {
                    "fired": False,
                    "reason": "loaded-report-title-mismatch",
                    "expected_id": wanted_id,
                    "expected_name": wanted_name,
                    "observed_name": observed[0],
                    "title_id": LOADED_TITLE_ID,
                }
            title_id = str(request.get("title_id") or "").split(":", 1)[0]
            expected_title_ids = {
                (LOADED_TITLE_ID if index == 0 else LOADED_TITLE_ID.replace(
                    ":text", f"_{index}:text",
                )).split(":", 1)[0]
                for index, _value in enumerate(titles)
            }
            if title_id not in expected_title_ids:
                return {
                    "fired": False,
                    "reason": "loaded-report-title-component-unresolved",
                    "expected_id": wanted_id,
                    "expected_name": wanted_name,
                    "title_id": title_id,
                }
            component_id = str(request.get("excel_id") or "").split(":", 1)[0]
            if component_id not in visible_components:
                return {
                    "fired": False,
                    "reason": "excel-onclick-unavailable",
                    "expected_id": wanted_id,
                    "expected_name": wanted_name,
                    "observed_name": observed[0],
                    "component_id": component_id,
                }
            self.guarded_export_fires += 1
            self.clicks.append(component_id)
            return {
                "fired": True,
                "strategy": "rendered-title-exact-guarded-native-export",
                "expected_id": wanted_id,
                "expected_name": wanted_name,
                "observed_name": observed[0],
                "title_id": LOADED_TITLE_ID,
                "component_id": component_id,
            }
        if "app.gds_bookmark" in script:
            if self.dataset_rows is None:
                return None
            return {"available": True, "rows": list(self.dataset_rows)}
        if "overlay.style.display = 'none'" in script:
            self.wait_window_hidden += 1
            return True
        if "style.display !== 'none'" in script:
            if self.always_busy:
                return True
            if self.busy_polls > 0:
                self.busy_polls -= 1
                return True
            return False
        if "childHasSameText" in script:
            return list(self._screen())
        if "is_folder: isFolder" in script:
            return [
                {**row, "is_folder": row["text"] not in LEAF_NAMES}
                for row in self._rows()
            ]
        if "textContent || \'\').trim()) continue" in script or "out.push({\n            id: element.id," in script:
            return self._root_icon_records(components)
        if "scrollHeight - element.clientHeight" in script:
            if self.scroll_rows and self.trees.get(self.tab) and self.tab not in self.scrolled:
                self.scrolled.add(self.tab)
                return {"moved": True, "top": 100, "max": 400}
            return {"moved": False, "top": 0, "max": 0}
        if "const popupPattern" in script:
            return [
                {**popup, "closers": [dict(item) for item in popup.get("closers") or []]}
                for popup in self.popups
            ]
        if "targetIds" in script:
            for element_id in argument or []:
                records = [*self._screen(), *self._root_icon_records(components)]
                match = next((item for item in records if item.get("id") == element_id), None)
                if match:
                    return {
                        "id": element_id, "x": match.get("x", 0), "y": match.get("y", 0),
                        "w": match.get("w", 0), "h": match.get("h", 0),
                    }
            return None
        if "on_fire_onclick" in script and isinstance(argument, str):
            popup = self._popup_for_closer(argument)
            if popup is not None:
                component_id = flow_gscm._component_element_ids(argument)[0]
                self._dismiss_popup(popup, component_id)
                return {"available": True, "fired": True, "component_id": component_id}
            return {"available": False, "fired": False, "reason": "missing"}
        if "hints.some" in script:
            return [
                {"id": item, "x": 1700, "y": 300}
                for item in sorted(visible_components)
                if any(hint in item.lower() for hint in argument)
            ]
        if "out.password" in script:
            return self._login_inputs()
        if "getElementById(id)" in script:
            return argument in visible_components
        raise AssertionError(f"unexpected evaluate: {script[:70]}")

    def _login_inputs(self):
        """What the sign-in input probe sees. The portal has no such inputs."""
        return None


def _scan_job():
    return {
        "site": {
            "id": 2, "name": "GSCM", "adapter": "gscm_portal",
            "base_url": "https://mdscm.sec.samsung.net/",
            "auth_url": "https://mdscm.sec.samsung.net/nexa/index.html",
        },
        "discovery": {"scope": ["*"], "report_paths": []},
    }


def _collect_progress():
    events = []

    def report_progress(status, detail, *args, **kwargs):
        events.append((status, detail))

    return events, report_progress


def _popup(
    container_id="mainframe.VFrameSet.TopFrame.form.div_notice",
    close_id=None,
    *, x=1100, y=0, w=300, h=160, close_text="",
):
    close_id = close_id or f"{container_id}.form.btn_close:icontext"
    return {
        "container_id": container_id,
        "x": x, "y": y, "w": w, "h": h,
        "closers": [{
            "id": close_id, "text": close_text,
            "x": x + w - 30, "y": y + 10, "w": 20, "h": 20,
        }],
    }


def _discover(page=None, job=None):
    page = page or FakeGscmPage()
    return page, flow_gscm.discover_catalog(page, job or _scan_job(), _collect_progress()[1])


# ── Discovery ──


def test_bookmarks_come_from_the_setting_dialog_not_the_home_widget():
    # The home screen's Favorite widget lists only pinned entries and is empty
    # for most users. The bookmarks live behind Setting > Favorite.
    page, (reports, complete) = _discover()

    assert complete is True
    assert page.dialog_open is True
    assert GEAR_ID in page.clicks
    names = [item["name"] for item in reports]
    assert "MENA_Actual_sales" in names
    assert "B2B BO Fcst V2" in names


def test_every_scope_tab_is_read_and_tagged():
    _page, (reports, _complete) = _discover()
    tabs = {item["automation"]["favorite_tab"] for item in reports}
    assert tabs == {"Private", "Public"}  # Custom is empty in the fixture
    private = next(item for item in reports if item["name"] == "Biz_trip_GSCM")
    assert private["automation"]["favorite_tab"] == "Private"


DATASET_BOOKMARKS = [
    {
        "userreportid": "RC_994973", "userid": "john.doe",
        "originuserid": "john.doe", "userreportname": "Biz_Trip_Account_Portion",
        "menuscope": "MP", "gbm": "MOBILE", "menuid": "AS470",
        "menuname": "Sell-in Biz Plan", "menugroupid": "AS313",
        "menugroupname": "Sell-in Biz Plan", "scope": "AS",
        "publicscope": "PRIVATE", "publicscopevalue": "",
    },
    {
        "userreportid": "RC_1000937", "userid": "external.user",
        "originuserid": "external.user", "userreportname": "MX B2B FFF8 Actual Sales",
        "menuscope": "MP", "gbm": "MOBILE", "menuid": "AS470",
        "menuname": "Actual Sales", "menugroupid": "AS313",
        "menugroupname": "Actual Sales", "scope": "AS",
        "publicscope": "PUBLIC", "publicscopevalue": "",
    },
    {
        "userreportid": "RC_811969", "userid": "external.user",
        "originuserid": "external.user", "userreportname": "Asia_Actual_sales",
        "menuscope": "MP", "gbm": "MOBILE", "menuid": "AS470",
        "menuname": "Actual Sales", "menugroupid": "AS313",
        "menugroupname": "Actual Sales", "scope": "AS",
        "publicscope": "PUBLIC", "publicscopevalue": "",
    },
]


def _dataset_bookmark(name, bookmark_id, publicscope, *, scope="AS"):
    return {
        "userreportid": bookmark_id,
        "userid": "external.user",
        "originuserid": "external.user",
        "userreportname": name,
        "menuscope": "MP",
        "gbm": "MOBILE",
        "menuid": "AS470",
        "menuname": "Actual Sales",
        "menugroupid": "AS313",
        "menugroupname": "Actual Sales",
        "scope": scope,
        "publicscope": publicscope,
        "publicscopevalue": "",
    }


def test_favorite_state_report_names_grid_dataset_scopes_and_setting_shell():
    page = FakeGscmPage(dialog_open=True, dataset_rows=DATASET_BOOKMARKS)

    report = flow_gscm.favorite_state_report(page)

    assert "grd_bookmark" in report
    assert "6 row(s)" in report
    assert "PRIVATE=1" in report
    assert "PUBLIC=2" in report
    assert "Setting shell=mounted" in report
    assert len(report) <= flow_gscm.MAX_FAVORITE_STATE_CHARS


def test_discovery_prefers_the_nexacro_dataset_for_each_activated_tab():
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=DATASET_BOOKMARKS,
    )
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert complete is True
    assert [report["name"] for report in reports] == [
        "Biz_Trip_Account_Portion", "MX B2B FFF8 Actual Sales", "Asia_Actual_sales",
    ]
    # The walk goes through Setting > Favorite exactly like a flow run...
    assert GEAR_ID in page.clicks
    assert page.dialog_open is True
    # ...but the per-tab dataset read spares the virtualized grid sweep.
    assert not page.scrolled
    assert any("gds_bookmark" in detail["message"] for _status, detail in events)
    assert any(
        "grid never bound" in detail["message"]
        and "Favorite state:" in detail["message"]
        for _status, detail in events
    )


def test_discovery_rereads_a_dataset_that_populates_during_an_unbound_grid_wait():
    target = _dataset_bookmark("Weekly PSI", "RC_DELAYED", "PRIVATE")

    class DatasetDuringWaitPage(FakeGscmPage):
        def __init__(self):
            super().__init__(
                trees={"Private": [], "Public": [], "Custom": []},
                dataset_rows=None,
            )
            self.populated_during_wait = False

        def _evaluate(self, script, argument=None, components=None):
            if (
                script == flow_gscm._FAVORITE_TREE_ROWS_JS
                and not self.populated_during_wait
            ):
                self.dataset_rows = [target]
                self.populated_during_wait = True
            return super()._evaluate(script, argument, components)

    page = DatasetDuringWaitPage()
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert complete is True
    assert page.populated_during_wait is True
    report = next(item for item in reports if item["name"] == "Weekly PSI")
    assert report["automation"]["favorite_tab"] == "Private"
    assert report["automation"]["favorite_bookmark_id"] == "RC_DELAYED"
    assert any(
        "Private dataset contains 1 bookmark" in detail["message"]
        and "grid never bound" in detail["message"]
        for _status, detail in events
    )


def test_discovery_activates_each_tab_the_way_a_flow_run_does():
    # The live failure this pins: gds_bookmark read at portal load held only
    # the user's own rows, so the scan reported the Public bookmarks missing
    # while every flow run - which selects the tab - found them. Activating a
    # scope tab is what makes GSCM load that scope.
    class LazyScopePage(FakeGscmPage):
        def __init__(self):
            super().__init__(
                trees={"Private": [], "Public": [], "Custom": []},
                dataset_rows=[
                    row for row in DATASET_BOOKMARKS
                    if row["publicscope"] == "PRIVATE"
                ],
            )

        def on_click(self, element_id):
            super().on_click(element_id)
            if self.tab == "Public":
                self.dataset_rows = list(DATASET_BOOKMARKS)

    page = LazyScopePage()
    reports, complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert complete is True
    assert GEAR_ID in page.clicks
    names = {item["name"] for item in reports}
    assert {"MX B2B FFF8 Actual Sales", "Asia_Actual_sales"} <= names
    public = next(item for item in reports if item["name"] == "Asia_Actual_sales")
    assert public["automation"]["favorite_tab"] == "Public"


def test_an_empty_first_tab_activation_is_flipped_and_reselected_like_a_run():
    # GSCM sometimes leaves a newly selected scope's grid empty until the
    # scope is flipped away and back - the same refresh open_bookmark uses.
    class StalledRebindPage(FakeGscmPage):
        def __init__(self):
            super().__init__()
            self.public_selections = 0

        def _rows(self):
            if self.tab == "Public" and self.public_selections < 2:
                return []
            return super()._rows()

        def on_click(self, element_id):
            clicked_component = flow_gscm._component_element_ids(element_id)[0]
            record = next(
                (
                    item for item in self._screen()
                    if flow_gscm._component_element_ids(item["id"])[0]
                    == clicked_component
                ),
                None,
            )
            super().on_click(element_id)
            if (record or {}).get("text") == "Public":
                self.public_selections += 1

    page = StalledRebindPage()
    reports, _complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert page.public_selections >= 2
    assert "MENA_Actual_sales" in {item["name"] for item in reports}


def test_a_bookmark_read_from_a_grid_and_the_dataset_is_catalogued_once():
    # The live failure this pins: the dataset served a bookmark under one tab
    # while another tab's rendered grid showed the same row. Grid rows carry
    # no bookmark id, so id-keyed dedupe never fired: every such bookmark
    # landed twice and every catalog name gained a "(2)" twin. The dataset
    # builds its folder path to mirror the rendered tree, so (folder path,
    # name) identifies the bookmark across both sources.
    page = FakeGscmPage(
        trees={
            "Private": [
                _label("SCM", ROOT_X, 560),
                _label("Actual Sales", FOLDER_X, 584),
                _label("MX B2B Actual Sales", LEAF_X, 608),
            ],
            "Public": [],
            "Custom": [],
        },
        dataset_rows=[{
            "userreportid": "RC_777001", "userid": "external.user",
            "originuserid": "external.user", "userreportname": "MX B2B Actual Sales",
            "menuscope": "MP", "gbm": "MOBILE", "menuid": "AS470",
            "menuname": "Actual Sales", "menugroupid": "AS313",
            "menugroupname": "Actual Sales", "scope": "AS",
            "publicscope": "PUBLIC", "publicscopevalue": "",
        }],
    )

    reports, _complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    names = [item["name"] for item in reports]
    assert names.count("MX B2B Actual Sales") == 1
    assert not any("(2)" in name for name in names)
    # Dataset identity and scope are authoritative over a stale rendered row.
    survivor = next(
        item for item in reports if item["name"] == "MX B2B Actual Sales"
    )
    assert survivor["automation"]["favorite_tab"] == "Public"
    assert survivor["automation"]["favorite_bookmark_id"] == "RC_777001"


def test_same_path_in_private_and_public_with_distinct_ids_keeps_both():
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[
            _dataset_bookmark("Weekly PSI", "RC_PRIVATE", "PRIVATE"),
            _dataset_bookmark("Weekly PSI", "RC_PUBLIC", "PUBLIC"),
        ],
    )

    reports, _complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert [item["name"] for item in reports] == ["Weekly PSI", "Weekly PSI (2)"]
    assert {
        item["automation"]["favorite_tab"] for item in reports
    } == {"Private", "Public"}
    assert {
        item["automation"]["favorite_bookmark_id"] for item in reports
    } == {"RC_PRIVATE", "RC_PUBLIC"}


def test_dataset_rows_replace_idless_grid_twins_in_each_scope():
    shared_tree = [
        _label("SCM", ROOT_X, 560),
        _label("Actual Sales", FOLDER_X, 584),
        _label("Weekly PSI", LEAF_X, 608),
    ]
    page = FakeGscmPage(
        trees={
            "Private": list(shared_tree),
            "Public": list(shared_tree),
            "Custom": [],
        },
        dataset_rows=[
            _dataset_bookmark("Weekly PSI", "RC_PRIVATE", "PRIVATE"),
            _dataset_bookmark("Weekly PSI", "RC_PUBLIC", "PUBLIC"),
        ],
    )

    reports, complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert complete is True
    assert len(reports) == 2
    assert {
        (
            item["automation"]["favorite_tab"],
            item["automation"]["favorite_bookmark_id"],
        )
        for item in reports
    } == {
        ("Private", "RC_PRIVATE"),
        ("Public", "RC_PUBLIC"),
    }
    assert all(item["automation"]["favorite_bookmark_id"] for item in reports)


def test_the_same_stable_id_seen_under_two_tabs_is_catalogued_once():
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[
            _dataset_bookmark("Weekly PSI", "RC_SHARED", "PRIVATE"),
            _dataset_bookmark("Weekly PSI", "RC_SHARED", "PUBLIC"),
        ],
    )

    reports, _complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert len(reports) == 1
    assert reports[0]["automation"]["favorite_bookmark_id"] == "RC_SHARED"


def test_grid_only_same_path_on_two_tabs_keeps_both_observations():
    shared_tree = [
        _label("SCM", ROOT_X, 560),
        _label("Actual Sales", FOLDER_X, 584),
        _label("Weekly PSI", LEAF_X, 608),
    ]
    page = FakeGscmPage(trees={
        "Private": list(shared_tree),
        "Public": list(shared_tree),
        "Custom": [],
    })

    reports, _complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert [item["name"] for item in reports] == ["Weekly PSI", "Weekly PSI (2)"]
    assert {
        item["automation"]["favorite_tab"] for item in reports
    } == {"Private", "Public"}
    assert all(
        item["automation"]["favorite_bookmark_id"] is None for item in reports
    )


def test_a_tab_the_dataset_does_not_cover_is_read_from_its_grid():
    # A rendered Private row missing from gds_bookmark must not vanish just
    # because the dataset serves the other tabs.
    page = FakeGscmPage(dataset_rows=[
        row for row in DATASET_BOOKMARKS if row["publicscope"] == "PUBLIC"
    ])
    reports = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )[0]
    names = {item["name"] for item in reports}
    assert "Biz_trip_GSCM" in names               # the Private grid
    assert "MX B2B FFF8 Actual Sales" in names    # the dataset


def test_a_dataset_that_loads_after_tab_activation_is_still_used():
    class DelayedDatasetPage(FakeGscmPage):
        def __init__(self):
            super().__init__(dataset_rows=DATASET_BOOKMARKS)
            self.dataset_reads = 0

        def evaluate(self, script, argument=None):
            if "app.gds_bookmark" in script:
                self.dataset_reads += 1
                if self.dataset_reads < 2:
                    return None
            return super().evaluate(script, argument)

    page = DelayedDatasetPage()
    reports, complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert complete is True
    names = {item["name"] for item in reports}
    # Private was read from the grid before the dataset materialized; Public
    # came from the late dataset, stable bookmark id included.
    assert "Biz_trip_GSCM" in names
    assert "MX B2B FFF8 Actual Sales" in names
    public = next(
        item for item in reports if item["name"] == "MX B2B FFF8 Actual Sales"
    )
    assert public["automation"]["favorite_bookmark_id"] == "RC_1000937"


@pytest.mark.parametrize(
    ("name", "tab", "bookmark_id", "folder_path"),
    [
        ("Biz_Trip_Account_Portion", "Private", "RC_994973", ["SCM", "Sell-in Biz Plan"]),
        ("MX B2B FFF8 Actual Sales", "Public", "RC_1000937", ["SCM", "Actual Sales"]),
        ("Asia_Actual_sales", "Public", "RC_811969", ["SCM", "Actual Sales"]),
    ],
)
def test_dataset_rows_reconstruct_stable_bookmark_identity(
    name, tab, bookmark_id, folder_path,
):
    reports = flow_gscm.discover_catalog(
        FakeGscmPage(dataset_rows=DATASET_BOOKMARKS),
        _scan_job(), _collect_progress()[1],
    )[0]
    report = next(item for item in reports if item["name"] == name)

    assert report["automation"]["favorite_tab"] == tab
    assert report["automation"]["favorite_folder_path"] == folder_path
    assert report["automation"]["favorite_bookmark_id"] == bookmark_id
    assert report["automation"]["favorite_menu_id"] == "AS470"
    assert report["automation"]["favorite_scope_raw"] == tab.upper()


def test_unknown_publicscope_is_warned_skipped_and_marks_scan_incomplete():
    unknown = _dataset_bookmark("Unmapped scope", "RC_UNKNOWN", "PARTNER")
    known = _dataset_bookmark("Runnable scope", "RC_PUBLIC", "PUBLIC")
    page = FakeGscmPage(gear=False, dataset_rows=[unknown, known])
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert complete is False
    assert [item["name"] for item in reports] == ["Runnable scope"]
    assert reports[0]["automation"]["favorite_scope_raw"] == "PUBLIC"
    assert any(
        "PARTNER" in detail["message"] and "Skipped" in detail["message"]
        for _status, detail in events
    )
    entries = flow_gscm.bookmark_dataset_entries(page)
    unmapped = next(item for item in entries if item["bookmark_id"] == "RC_UNKNOWN")
    assert unmapped["tab"] == ""
    assert unmapped["scope_raw"] == "PARTNER"


def test_unknown_publicscope_in_a_normal_dialog_scan_is_also_incomplete():
    unknown = _dataset_bookmark("Unmapped dialog row", "RC_UNKNOWN", "PARTNER")
    known = _dataset_bookmark("Runnable dialog row", "RC_PUBLIC", "PUBLIC")
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[unknown, known],
    )
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert complete is False
    assert [item["name"] for item in reports] == ["Runnable dialog row"]
    assert any(
        "PARTNER" in detail["message"]
        and "prior runnable snapshot will be preserved" in detail["message"]
        for _status, detail in events
    )


@pytest.mark.parametrize(
    "unknown_first",
    [
        pytest.param(True, id="unknown-before-known"),
        pytest.param(False, id="known-before-unknown"),
    ],
)
def test_same_stable_id_with_known_and_unknown_scopes_is_always_incomplete(
    unknown_first,
):
    known = _dataset_bookmark("Weekly PSI", "RC_SHARED", "PUBLIC")
    unknown = _dataset_bookmark("Weekly PSI", "RC_SHARED", "PARTNER")
    rows = [unknown, known] if unknown_first else [known, unknown]
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=rows,
    )
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert complete is False
    assert any(
        "PARTNER" in detail["message"]
        for _status, detail in events
    )
    runnable = [
        item for item in reports
        if item["automation"]["favorite_bookmark_id"] == "RC_SHARED"
    ]
    # A conservative merge may omit the conflicted stable id altogether.  If
    # it retains the independently runnable observation, it must retain only
    # the known Public scope and never turn PARTNER into a runnable tab.
    assert len(runnable) <= 1
    if runnable:
        assert runnable[0]["automation"]["favorite_tab"] == "Public"
        assert runnable[0]["automation"]["favorite_scope_raw"] == "PUBLIC"
    assert not any(
        item["automation"]["favorite_scope_raw"] == "PARTNER"
        for item in reports
    )


def test_unknown_scope_cannot_make_a_matching_idless_grid_row_runnable():
    shared_tree = [
        _label("SCM", ROOT_X, 560),
        _label("Actual Sales", FOLDER_X, 584),
        _label("Weekly PSI", LEAF_X, 608),
    ]
    page = FakeGscmPage(
        trees={"Private": [], "Public": shared_tree, "Custom": []},
        dataset_rows=[
            _dataset_bookmark("Weekly PSI", "", ""),
        ],
    )
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert reports == []
    assert complete is False
    assert any(
        "(blank)" in detail["message"]
        for _status, detail in events
    )
    entries = flow_gscm.bookmark_dataset_entries(page)
    assert entries[0]["scope_raw"] == ""


def test_dataset_only_fallback_is_never_an_authoritative_snapshot():
    page = FakeGscmPage(
        gear=False,
        dataset_rows=[
            _dataset_bookmark("Dataset fallback", "RC_FALLBACK", "PUBLIC"),
        ],
    )
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert [item["name"] for item in reports] == ["Dataset fallback"]
    assert complete is False
    assert any(
        "without activating its tabs" in detail["message"]
        for _status, detail in events
    )


def test_a_missing_scope_tab_makes_an_otherwise_valid_scan_incomplete():
    class MissingCustomTabPage(FakeGscmPage):
        def _screen(self):
            return [
                row for row in super()._screen()
                if row.get("text") != "Custom"
            ]

    page = MissingCustomTabPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[
            _dataset_bookmark("Runnable dialog row", "RC_PUBLIC", "PUBLIC"),
        ],
    )
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert [item["name"] for item in reports] == ["Runnable dialog row"]
    assert complete is False
    assert any(
        "Custom bookmark tab could not be found or activated" in detail["message"]
        for _status, detail in events
    )


def test_runner_refuses_a_stable_id_with_unknown_publicscope():
    page = FakeGscmPage(
        dataset_rows=[_dataset_bookmark("Unmapped scope", "RC_UNKNOWN", "PARTNER")],
    )

    with pytest.raises(RuntimeError, match=r"unmapped stored publicscope.*PARTNER"):
        flow_gscm.open_bookmark(
            page,
            _run_job(
                name="Unmapped scope", folder=("SCM", "Actual Sales"),
                tab="", bookmark_id="RC_UNKNOWN", scope_raw="PARTNER",
            ),
        )
    assert page.navigations == []
    assert page.clicks == []


def test_runner_refuses_an_authoritative_dataset_row_with_unknown_scope():
    page = FakeGscmPage(
        dataset_rows=[_dataset_bookmark("Unmapped scope", "RC_UNKNOWN", "PARTNER")],
    )

    with pytest.raises(RuntimeError, match=r"unknown publicscope.*PARTNER"):
        flow_gscm.open_bookmark(
            page,
            _run_job(
                name="Unmapped scope", folder=("SCM", "Actual Sales"),
                tab="Public", bookmark_id="RC_UNKNOWN", scope_raw=None,
            ),
        )

    assert page.selected_bookmark_id is None


def test_runner_refuses_a_stored_unknown_scope_without_dataset_identity():
    page = FakeGscmPage(dataset_rows=None)

    with pytest.raises(RuntimeError, match=r"no stable favorite_bookmark_id"):
        flow_gscm.open_bookmark(
            page,
            _run_job(
                name="Unmapped scope", folder=("SCM", "Actual Sales"),
                tab="Public", bookmark_id=None, scope_raw="PARTNER",
            ),
        )

    assert page.selected_bookmark_id is None
    assert not any(row["id"] in page.clicks for row in PUBLIC_TREE)
    assert page.navigations == []
    assert page.clicks == []


def test_dataset_discovery_cannot_catalogue_concatenated_global_navigation():
    corrupt_navigation = _label(
        "Biz InfoAXSCMChannelPromotionMDMSupplyNews RoomAdmin", 0, 0,
        element_id="mainframe.VFrameSet.TopFrame.form.nexacontainer",
    )
    page = FakeGscmPage(dataset_rows=DATASET_BOOKMARKS)
    original_screen = page._screen
    page._screen = lambda: [corrupt_navigation, *original_screen()]

    reports = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])[0]

    assert all("Biz InfoAXSCM" not in report["name"] for report in reports)


def test_folder_nesting_comes_from_indentation():
    _page, (reports, _complete) = _discover()
    mena = next(item for item in reports if item["name"] == "MENA_Actual_sales")
    assert mena["automation"]["favorite_folder_path"] == ["SCM", "Actual Sales"]
    assert mena["automation"]["category_path"] == [
        "Public", "SCM", "Actual Sales", "MENA_Actual_sales",
    ]
    assert mena["discovery_key"] == "Public > SCM > Actual Sales > MENA_Actual_sales"


def test_folders_are_not_catalogued_as_reports():
    _page, (reports, _complete) = _discover()
    names = {item["name"] for item in reports}
    # "Actual Sales" and "B2B Biz Plan" have rows nested under them.
    assert "Actual Sales" not in names
    assert "B2B Biz Plan" not in names
    assert "SCM" not in names


def test_dialog_chrome_never_becomes_a_bookmark():
    _page, (reports, _complete) = _discover()
    names = {item["name"].casefold() for item in reports}
    for chrome in ("public", "private", "custom", "save", "close", "go >>", "alphabet"):
        assert chrome not in names


def test_a_discovered_bookmark_declares_no_metronome_filters():
    # GSCM owns the filters. Inventing prompts here would ask the user to
    # configure the same report twice.
    _page, (reports, _complete) = _discover()
    assert all(item["filters"] == [] for item in reports)


def test_an_already_open_dialog_is_reused():
    page = FakeGscmPage(dialog_open=True)
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert GEAR_ID not in page.clicks


def test_the_known_setting_gear_is_tried_in_the_second_root():
    page = FakeGscmPage(
        gear=False,
        frame_components=[(GEAR_ID,)],
    )

    flow_gscm.open_favorites_dialog(page)

    assert page.dialog_open is True
    assert page.clicks.count(GEAR_ID) == 1


def test_a_known_setting_component_without_a_dom_node_uses_native_click():
    class NativeTreeOnlySettingPage(FakeGscmPage):
        def __init__(self):
            super().__init__(gear=False)
            self.native_clicks = []

        def evaluate(self, script, argument=None):
            if (
                script == flow_gscm._NATIVE_COMPONENT_CLICK_JS
                and str(argument).split(":", 1)[0] == GEAR_ID
            ):
                self.native_clicks.append(GEAR_ID)
                self.setting_open = True
                return {
                    "available": True, "fired": True,
                    "component_id": GEAR_ID,
                }
            return super().evaluate(script, argument)

    page = NativeTreeOnlySettingPage()

    flow_gscm.open_favorites_dialog(page)

    assert page.dialog_open is True
    assert page.native_clicks == [GEAR_ID]


def test_setting_shell_is_not_mistaken_for_the_favorite_panel():
    page = FakeGscmPage(gear=False, setting_open=True, dialog_open=False)

    assert flow_gscm._setting_dialog_open(page) is True
    assert flow_gscm.favorites_dialog_open(page) is False

    flow_gscm.open_favorites_dialog(page)

    assert page.dialog_open is True
    assert any("btn_favorite" in item for item in page.clicks)


def test_a_stray_public_label_cannot_fake_an_open_favorite_panel():
    class StrayPublicPage(FakeGscmPage):
        def _screen(self):
            rows = super()._screen()
            if not self.dialog_open:
                return [
                    *rows,
                    _label(
                        "Public", 250, 180,
                        "mainframe.HomeFrame.form.sta_public:text",
                    ),
                ]
            return rows

    page = StrayPublicPage(gear=False)

    assert flow_gscm.favorites_dialog_open(page) is False


def test_a_stray_public_label_does_not_reject_a_successful_go_click():
    class StrayPublicAfterGoPage(FakeGscmPage):
        def _screen(self):
            rows = super()._screen()
            if not self.dialog_open:
                return [
                    *rows,
                    _label(
                        "Public", 250, 180,
                        "mainframe.HomeFrame.form.sta_public:text",
                    ),
                ]
            return rows

    page = StrayPublicAfterGoPage(
        dialog_open=True,
        dataset_rows=[_dataset_bookmark(
            "MENA_Actual_sales", "RC_MENA", "PUBLIC",
        )],
    )
    selected = flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )

    assert selected["selected"] is True
    assert flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", FAVORITE_GRID_ID,
    )["activated"] is True
    assert page.dialog_open is False


def test_a_portal_tab_already_open_is_not_reloaded():
    page, _result = _discover()
    assert page.navigations == []


def test_a_tab_elsewhere_navigates_to_the_portal():
    page = FakeGscmPage(url="https://intranet.example.test/home")
    page.navigations = []
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert page.navigations == ["https://mdscm.sec.samsung.net/nexa/index.html"]


def test_an_unreachable_dialog_reports_what_was_on_screen(monkeypatch):
    # The failure that cost a live test run: the scan could not tell "no
    # bookmarks" from "looking in the wrong place". It must now say.
    monkeypatch.setattr(flow_gscm, "DIALOG_READY_TIMEOUT_MS", 200)
    page = FakeGscmPage(gear=False)
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    message = str(excinfo.value)
    assert "Setting > Favorite" in message
    # The report must name the gear's neighbourhood, not dump the whole screen:
    # a hundred-line inventory is truncated before reaching what matters.
    assert "Top-bar icon controls" in message
    assert "Top-bar labels" in message


def test_empty_tabs_report_the_screen_rather_than_an_empty_catalog():
    page = FakeGscmPage(trees={"Private": [], "Public": [], "Custom": []})
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert "On screen:" in str(excinfo.value)


def test_the_same_name_under_two_folders_keeps_both_catalog_entries():
    tree = [
        _label("SCM", ROOT_X, 560),
        _label("Asia", FOLDER_X, 584),
        _label("Weekly PSI", LEAF_X, 608),
        _label("MENA", FOLDER_X, 632),
        _label("Weekly PSI", LEAF_X, 656),
    ]
    page = FakeGscmPage(trees={"Private": [], "Public": tree, "Custom": []})
    _reports = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])[0]
    assert [item["name"] for item in _reports] == ["Weekly PSI", "Weekly PSI (2)"]
    assert len({item["discovery_key"] for item in _reports}) == 2
    # The raw label is preserved so the run-time lookup still matches GSCM.
    assert all(item["automation"]["favorite_name"] == "Weekly PSI" for item in _reports)
    assert [item["automation"]["favorite_folder_path"] for item in _reports] == [
        ["SCM", "Asia"], ["SCM", "MENA"],
    ]


def test_targeted_scan_narrows_and_reports_itself_incomplete():
    job = _scan_job()
    job["discovery"]["report_paths"] = [["Public", "SCM", "Actual Sales", "MENA_Actual_sales"]]
    _page, (reports, complete) = _discover(job=job)
    assert [item["name"] for item in reports] == ["MENA_Actual_sales"]
    # An incomplete sweep must not let the server stale every other bookmark.
    assert complete is False


def test_target_report_matches_stable_id_and_preserves_the_catalog_name():
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=DATASET_BOOKMARKS,
    )
    job = _scan_job()
    job["target_report"] = {
        "id": 41,
        "catalog_name": "Renamed catalog row (2)",
        "category_path": ["Public", "SCM", "Actual Sales", "Renamed catalog row (2)"],
        "favorite_bookmark_id": "RC_811969",
    }

    reports, complete = flow_gscm.discover_catalog(
        page, job, _collect_progress()[1],
    )

    assert complete is False
    assert [item["name"] for item in reports] == ["Renamed catalog row (2)"]
    assert reports[0]["automation"]["favorite_name"] == "Asia_Actual_sales"
    assert reports[0]["automation"]["favorite_bookmark_id"] == "RC_811969"


def test_target_report_falls_back_to_the_unsuffixed_source_name():
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=DATASET_BOOKMARKS,
    )
    job = _scan_job()
    job["target_report"] = {
        "id": 42,
        "catalog_name": "MX B2B FFF8 Actual Sales (2)",
        "category_path": [
            "Public", "SCM", "Actual Sales", "MX B2B FFF8 Actual Sales (2)",
        ],
        "favorite_bookmark_id": "RC_NO_LONGER_PRESENT",
    }

    reports, complete = flow_gscm.discover_catalog(
        page, job, _collect_progress()[1],
    )

    assert complete is False
    assert [item["name"] for item in reports] == ["MX B2B FFF8 Actual Sales (2)"]
    assert reports[0]["automation"]["favorite_name"] == "MX B2B FFF8 Actual Sales"


def test_wait_overlay_is_cleared_before_reading():
    page, _result = _discover()
    assert page.wait_window_hidden >= 1


def test_a_stuck_wait_overlay_is_forced_down_instead_of_hanging_the_run():
    page = FakeGscmPage(always_busy=True)
    assert flow_gscm.wait_for_calculation(page, timeout_ms=2_000) is False
    assert page.wait_window_hidden >= 1


# ── Portal popup cleanup ──


def test_popup_detector_uses_id_vocabulary_without_z_index_guesses():
    script = flow_gscm._POPUP_RECORDS_JS
    for marker in ("popup", "notice", "alert", "message", "msg", "confirm", "pdv_"):
        assert marker in script
    assert "topframe.setting" in script
    assert "div_favorite" in script
    assert "mainframe.waitwindow" in script
    assert "zIndex" not in script


@pytest.mark.parametrize(
    ("container_id", "close_id", "close_text"),
    [
        ("mainframe.portal_popup", "mainframe.portal_popup.close", ""),
        (
            "mainframe.VFrameSet.TopFrame.form.div_notice",
            "mainframe.VFrameSet.TopFrame.form.div_notice.form.btn_close:icontext",
            "",
        ),
        (
            "mainframe.VFrameSet.TopFrame.form.div_msg",
            "mainframe.VFrameSet.TopFrame.form.div_msg.form.btn_x:icontext",
            "×",
        ),
    ],
)
def test_popup_close_uses_the_owning_component_first(container_id, close_id, close_text):
    page = FakeGscmPage(popup_records=[_popup(
        container_id, close_id, close_text=close_text,
    )])

    assert flow_gscm.clear_screen(page) == []

    assert page.popups == []
    assert page.clicks[0] == flow_gscm._component_element_ids(close_id)[0]
    assert page.waits == [flow_gscm.POPUP_VERIFY_INTERVAL_MS]


def test_clear_screen_adds_no_verification_wait_when_no_popup_was_seen():
    page = FakeGscmPage()

    assert flow_gscm.clear_screen(page) == []

    assert page.waits == []


def test_a_popup_that_survives_dom_click_uses_the_exact_native_component():
    close_id = "mainframe.VFrameSet.TopFrame.form.div_alert.form.btn_close:icontext"
    component_id = flow_gscm._component_element_ids(close_id)[0]
    page = FakeGscmPage(
        popup_records=[_popup(
            "mainframe.VFrameSet.TopFrame.form.div_alert", close_id,
        )],
        popup_dom_noop_ids=[component_id],
    )

    assert flow_gscm.clear_screen(page) == []

    assert component_id in page.clicks
    assert page.popups == []
    assert page.waits == [
        flow_gscm.POPUP_VERIFY_INTERVAL_MS,
        flow_gscm.POPUP_VERIFY_INTERVAL_MS,
    ]


def test_cascading_popups_are_each_closed_within_the_same_verification_pass():
    first_close = "mainframe.first_popup.btn_close"
    second_close = "mainframe.second_notice.btn_x:icontext"
    first_component = flow_gscm._component_element_ids(first_close)[0]
    second_component = flow_gscm._component_element_ids(second_close)[0]
    page = FakeGscmPage(
        popup_records=[_popup("mainframe.first_popup", first_close)],
        popup_cascades={first_component: [
            _popup("mainframe.second_notice", second_close),
        ]},
    )

    assert flow_gscm.clear_screen(page) == []

    assert first_component in page.clicks
    assert second_component in page.clicks
    assert page.popups == []


class AutoVanishingPopupPage(FakeGscmPage):
    def evaluate(self, script, argument=None):
        if "const popupPattern" in script and self.popups:
            records = super().evaluate(script, argument)
            self.popups.clear()
            return records
        return super().evaluate(script, argument)


def test_an_auto_vanishing_popup_is_not_an_error():
    page = AutoVanishingPopupPage(popup_records=[_popup()])

    assert flow_gscm.clear_screen(page, target=GEAR_ID) == []
    assert page.popups == []


def test_a_persistent_non_overlapping_popup_remains_best_effort():
    close_id = "mainframe.side_notice.btn_close"
    component_id = flow_gscm._component_element_ids(close_id)[0]
    page = FakeGscmPage(
        popup_records=[_popup(
            "mainframe.side_notice", close_id, x=100, y=500, w=200, h=120,
        )],
        popup_persistent_ids=[component_id],
    )

    remaining = flow_gscm.clear_screen(page, target=GEAR_ID)

    assert [item["container_id"] for item in remaining] == ["mainframe.side_notice"]
    assert component_id in page.clicks


def test_a_persistent_overlapping_popup_stops_before_the_gear_click():
    close_id = "mainframe.top_notice.btn_close"
    component_id = flow_gscm._component_element_ids(close_id)[0]
    page = FakeGscmPage(
        popup_records=[_popup(
            "mainframe.top_notice", close_id, x=1600, y=0, w=140, h=80,
        )],
        popup_persistent_ids=[component_id],
    )

    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.open_favorites_dialog(page)

    message = str(excinfo.value)
    assert "popup blocked control" in message
    assert "mainframe.top_notice" in message
    assert close_id in message
    assert GEAR_ID in message
    assert "On screen:" in message
    assert GEAR_ID not in page.clicks


def test_a_forbidden_popup_control_is_never_clicked():
    close_id = "mainframe.confirm_popup.form.btn_save_close"
    page = FakeGscmPage(popup_records=[_popup(
        "mainframe.confirm_popup", close_id, x=1600, y=0, w=140, h=80,
    )])

    with pytest.raises(RuntimeError, match="popup blocked control"):
        flow_gscm.open_favorites_dialog(page)

    assert not any("save" in element_id.casefold() for element_id in page.clicks)
    assert GEAR_ID not in page.clicks


def test_dataset_scan_and_saved_flow_inherit_the_existing_popup_cleanup_path():
    dataset_page = FakeGscmPage(
        popup_records=[_popup()],
        dataset_rows=[{
            "userreportid": "RC_1", "userreportname": "Dataset bookmark",
            "scope": "AS", "publicscope": "Public", "menuid": "AS470",
        }],
    )
    reports, _complete = flow_gscm.discover_catalog(
        dataset_page, _scan_job(), _collect_progress()[1],
    )
    # The dataset row and rendered-grid-only bookmarks share popup cleanup.
    assert {"Biz_trip_GSCM", "Dataset bookmark"} <= {
        item["name"] for item in reports
    }
    assert dataset_page.popups == []

    flow_page = FakeGscmPage(
        popup_records=[_popup()],
        dataset_rows=[_dataset_bookmark(
            "MENA_Actual_sales", "RC_MENA", "PUBLIC",
        )],
    )
    flow_gscm.open_bookmark(flow_page, _run_job())
    assert flow_page.popups == []
    close_component = flow_gscm._component_element_ids(
        _popup()["closers"][0]["id"],
    )[0]
    assert flow_page.clicks.index(close_component) < flow_page.clicks.index(GEAR_ID)


# ── Download ──


def _run_job(
    name="MENA_Actual_sales", folder=("SCM", "Actual Sales"), tab="Public",
    bookmark_id="RC_MENA", scope_raw=None,
):
    return {
        "site": _scan_job()["site"],
        "report": {
            "id": 7, "name": f"{tab} > {name}",
            "url": "https://mdscm.sec.samsung.net/nexa/index.html",
            "automation": {
                "kind": "gscm_favorite",
                "favorite_tab": tab,
                "favorite_name": name,
                "favorite_folder_path": list(folder),
                "favorite_bookmark_id": bookmark_id,
                "favorite_scope_raw": scope_raw,
                "excel_btn_id": EXCEL_BUTTON,
            },
        },
    }


def _clicked_texts(page):
    lookup = {
        element_id: item["text"]
        for item in DIALOG_CHROME
        for element_id in flow_gscm._component_element_ids(item["id"])
    }
    for tree in (PUBLIC_TREE, PRIVATE_TREE):
        lookup.update({item["id"]: item["text"] for item in tree})
    return [lookup.get(item, item) for item in page.clicks]


@pytest.mark.parametrize(
    ("automation_name", "dataset_name"),
    [
        ("Exact name", "exact name"),
        ("Exact  name", "Exact name"),
    ],
)
def test_exact_bookmark_name_rejects_case_or_internal_whitespace_changes(
    automation_name, dataset_name,
):
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark(dataset_name, "RC_EXACT", "PUBLIC"),
    ])

    with pytest.raises(RuntimeError, match=r"not the exact saved bookmark name"):
        flow_gscm.open_bookmark(
            page,
            _run_job(name=automation_name, bookmark_id="RC_EXACT"),
        )

    assert page.selection_attempts == 0
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


def test_exact_bookmark_name_ignores_outer_whitespace_only():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("  Exact  name  ", "RC_EXACT", "PUBLIC"),
    ])

    flow_gscm.open_bookmark(
        page,
        _run_job(name="\tExact  name\n", bookmark_id="RC_EXACT"),
    )

    assert page.selected_bookmark_name == "Exact  name"
    assert page.guarded_go_fires == 1


def test_stable_bookmark_id_is_case_sensitive():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])

    with pytest.raises(RuntimeError, match=r"stable id 'rc_mena'.*was not present"):
        flow_gscm.open_bookmark(
            page, _run_job(bookmark_id="rc_mena"),
        )

    assert page.selection_attempts == 0
    assert page.guarded_go_fires == 0


@pytest.mark.parametrize(
    ("name", "bookmark_id", "message"),
    [
        ("", "RC_MENA", "no bookmark reference"),
        ("MENA_Actual_sales", None, "no stable favorite_bookmark_id"),
    ],
)
def test_missing_execution_identity_fails_before_opening_the_portal(
    name, bookmark_id, message,
):
    page = FakeGscmPage()

    with pytest.raises(RuntimeError, match=message):
        flow_gscm.open_bookmark(
            page, _run_job(name=name, bookmark_id=bookmark_id),
        )

    assert page.navigations == []
    assert page.clicks == []


def test_duplicate_stable_id_fails_before_selection_or_go():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_DUP", "PUBLIC"),
        _dataset_bookmark("MENA_Actual_sales", "RC_DUP", "PUBLIC"),
    ])

    with pytest.raises(RuntimeError, match=r"listed stable id 'RC_DUP' 2 times"):
        flow_gscm.open_bookmark(
            page, _run_job(bookmark_id="RC_DUP"),
        )

    assert page.selection_attempts == 0
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


@pytest.mark.parametrize(
    "selection_mode",
    [
        "ambiguous_grid", "current_row_mismatch", "ambiguous_selection",
        "unsupported_selecttype",
    ],
)
def test_unverifiable_grid_state_blocks_go(selection_mode):
    page = FakeGscmPage(
        dataset_rows=[_dataset_bookmark(
            "MENA_Actual_sales", "RC_MENA", "PUBLIC",
        )],
        dataset_selection_mode=selection_mode,
    )

    with pytest.raises(RuntimeError, match=r"refusing to press Go"):
        flow_gscm.open_bookmark(page, _run_job())

    assert page.selection_attempts == (1 if selection_mode == "ambiguous_grid" else 2)
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


def test_hundreds_of_rows_select_the_second_to_last_by_exact_identity():
    rows = [
        _dataset_bookmark(f"Bookmark {index}", f"RC_{index}", "PUBLIC")
        for index in range(300)
    ]
    rows[-2] = _dataset_bookmark(
        "Second to last exact target", "RC_TARGET", "PUBLIC",
    )
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=rows,
    )

    result = flow_gscm.open_bookmark(
        page,
        _run_job(
            name="Second to last exact target", folder=(),
            bookmark_id="RC_TARGET",
        ),
    )

    assert result == FAVORITE_GRID_ID
    assert page.selected_row_index == 298
    assert page.selected_bookmark_id == "RC_TARGET"
    assert page.scrolled == set()
    assert page.guarded_go_fires == 1


def test_recycled_dom_row_id_cannot_authorize_a_different_bookmark():
    recycled_id = f"{FAVORITE_GRID_ID}.body.gridrow_0"
    page = FakeGscmPage(
        trees={
            "Private": [],
            "Public": [_label(
                "Wrong rendered bookmark", LEAF_X, 608, recycled_id,
            )],
            "Custom": [],
        },
        dataset_rows=[_dataset_bookmark(
            "Exact dataset bookmark", "RC_EXACT", "PUBLIC",
        )],
    )

    flow_gscm.open_bookmark(
        page,
        _run_job(
            name="Exact dataset bookmark", folder=(), bookmark_id="RC_EXACT",
        ),
    )

    assert recycled_id not in page.clicks
    assert page.selected_bookmark_name == "Exact dataset bookmark"
    assert page.guarded_go_fires == 1


def test_selection_drift_at_guard_leaves_dialog_open_and_never_fires_go():
    page = FakeGscmPage(
        dataset_rows=[
            _dataset_bookmark("Other", "RC_OTHER", "PUBLIC"),
            _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
        ],
        dataset_selection_mode="guard_drift",
    )

    with pytest.raises(RuntimeError, match=r"bookmark-selection-drift") as excinfo:
        flow_gscm.open_bookmark(page, _run_job())

    assert "current_row=0" in str(excinfo.value)
    assert "observed_name='MENA_Actual_sales'" in str(excinfo.value)
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


def test_opening_a_bookmark_selects_its_row_then_presses_go():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    flow_gscm.open_bookmark(page, _run_job())
    target_id = next(
        row["id"] for row in PUBLIC_TREE if row["text"] == "MENA_Actual_sales"
    )
    assert target_id not in page.clicks
    assert page.selected_bookmark_id == "RC_MENA"
    assert page.selected_bookmark_name == "MENA_Actual_sales"
    assert page.guarded_go_fires == 1


def test_dataset_first_run_succeeds_without_rendered_grid_rows():
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[
            _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
        ],
    )
    progress = []

    result = flow_gscm.open_bookmark(
        page,
        _run_job(
            name="MENA_Actual_sales", folder=("SCM", "Actual Sales"),
            tab="Private", bookmark_id="RC_MENA",
        ),
        progress.append,
    )

    assert result == FAVORITE_GRID_ID
    assert page.selected_bookmark_id == "RC_MENA"
    assert page.tab == "Public"
    assert not any(row["id"] in page.clicks for row in PUBLIC_TREE)
    assert any("moved from Private to Public" in message for message in progress)


def test_runner_waits_for_the_requested_id_then_corrects_scope_before_selection(
    monkeypatch,
):
    unrelated = _dataset_bookmark("Another report", "RC_OTHER", "PRIVATE")
    target = _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC")

    class DelayedTargetPage(FakeGscmPage):
        def __init__(self):
            super().__init__(
                trees={"Private": [], "Public": [], "Custom": []},
                dataset_rows=[unrelated],
            )
            self.dataset_reads = 0

        def evaluate(self, script, argument=None):
            if script == flow_gscm._BOOKMARK_DATASET_JS:
                self.dataset_reads += 1
                self.dataset_rows = (
                    [unrelated] if self.dataset_reads == 1 else [unrelated, target]
                )
            return super().evaluate(script, argument)

    page = DelayedTargetPage()
    progress = []

    result = flow_gscm.open_bookmark(
        page,
        _run_job(
            name="MENA_Actual_sales", folder=("SCM", "Actual Sales"),
            tab="Private", bookmark_id="RC_MENA",
        ),
        progress.append,
    )

    assert result == FAVORITE_GRID_ID
    assert page.dataset_reads >= 2
    assert page.tab == "Public"
    assert page.selected_bookmark_id == "RC_MENA"
    assert any("moved from Private to Public" in message for message in progress)


def test_authoritative_scope_instability_fails_immediately_before_selection():
    public = _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC")
    custom = _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "CUSTOM")

    class ScopeDriftPage(FakeGscmPage):
        def __init__(self):
            super().__init__(dataset_rows=[public])
            self.dataset_reads = 0

        def _evaluate(self, script, argument=None, components=None):
            if script == flow_gscm._BOOKMARK_DATASET_JS:
                self.dataset_reads += 1
                self.dataset_rows = [public] if self.dataset_reads == 1 else [custom]
            return super()._evaluate(script, argument, components)

    page = ScopeDriftPage()

    with pytest.raises(RuntimeError, match=r"changed scope from 'Public' to 'Custom'"):
        flow_gscm.open_bookmark(page, _run_job())

    assert page.selection_attempts == 0
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


def test_open_bookmark_owns_one_retry_and_reselects_the_authoritative_scope(monkeypatch):
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[
            _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "CUSTOM"),
        ],
        dataset_selection_mode="first_rejected_then_success",
    )
    progress = []

    result = flow_gscm.open_bookmark(
        page,
        _run_job(
            name="MENA_Actual_sales", folder=("SCM", "Actual Sales"),
            tab="Public", bookmark_id="RC_MENA",
        ),
        progress.append,
    )

    assert result == FAVORITE_GRID_ID
    assert page.selection_attempts == 2
    assert page.selected_bookmark_id == "RC_MENA"
    assert page.tab == "Custom"
    assert page.dialog_open is False
    assert page.guarded_go_fires == 1
    assert any("moved from Public to Custom" in message for message in progress)


def test_runner_validates_a_late_native_selection_and_rejects_unknown_scope(
    monkeypatch,
):
    unrelated = _dataset_bookmark("Another report", "RC_OTHER", "PUBLIC")
    target = _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PARTNER")

    class UnknownTargetAfterActivationPage(FakeGscmPage):
        def __init__(self):
            super().__init__(
                trees={"Private": [], "Public": [], "Custom": []},
                dataset_rows=[unrelated],
            )
            self.dataset_reads_before_activation = 0
            self.public_activated = False
            self.native_selection_attempts = 0

        def on_click(self, element_id):
            clicked_component = flow_gscm._component_element_ids(element_id)[0]
            record = next((
                item for item in self._screen()
                if flow_gscm._component_element_ids(item["id"])[0]
                == clicked_component
            ), None)
            super().on_click(element_id)
            if (record or {}).get("text") == "Public":
                self.public_activated = True
                # The row is selectable by stable id as soon as the stored tab
                # activates, but its newly exposed scope is not runnable.
                self.dataset_rows = [unrelated, target]

        def _evaluate(self, script, argument=None, components=None):
            if script == flow_gscm._BOOKMARK_DATASET_JS and not self.public_activated:
                self.dataset_reads_before_activation += 1
            if script == flow_gscm._SELECT_BOOKMARK_ROW_JS:
                self.native_selection_attempts += 1
            return super()._evaluate(script, argument, components)

    page = UnknownTargetAfterActivationPage()

    with pytest.raises(RuntimeError, match=r"unknown publicscope.*PARTNER"):
        flow_gscm.open_bookmark(
            page,
            _run_job(
                name="MENA_Actual_sales", folder=("SCM", "Actual Sales"),
                tab="Public", bookmark_id="RC_MENA", scope_raw=None,
            ),
        )

    assert page.dataset_reads_before_activation > 0
    assert page.native_selection_attempts == 0
    assert page.selected_bookmark_id is None
    assert page.dialog_open is True
    go_component = flow_gscm._component_element_ids(flow_gscm.GO_BUTTON_ID)[0]
    assert not any(
        flow_gscm._component_element_ids(clicked)[0] == go_component
        for clicked in page.clicks
    )


@pytest.mark.parametrize(
    "selection_mode",
    ["missing_bind", "rejected_rowposition", "id_mismatch"],
)
def test_unverified_native_dataset_selection_fails_without_tree_fallback(selection_mode):
    page = FakeGscmPage(
        dataset_rows=[
            _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
        ],
        dataset_selection_mode=selection_mode,
    )

    with pytest.raises(RuntimeError, match=r"refusing to press Go"):
        flow_gscm.open_bookmark(page, _run_job(bookmark_id="RC_MENA"))

    target_id = next(
        row["id"] for row in PUBLIC_TREE if row["text"] == "MENA_Actual_sales"
    )
    assert page.selected_bookmark_id is None
    assert target_id not in page.clicks
    assert page.selection_attempts == 2
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


def test_native_dataset_selection_stops_after_the_first_verified_root():
    class SelectionRoot:
        def __init__(self, result, *, mutation_trap=False):
            self.frames = []
            self.result = result
            self.calls = 0
            self.mutations = 0
            self.mutation_trap = mutation_trap

        def evaluate(self, script, argument=None):
            if script == flow_gscm._VISIBLE_COMPONENT_IDS_JS:
                return [FAVORITE_GRID_ID]
            assert script == flow_gscm._SELECT_BOOKMARK_ROW_JS
            assert argument["bookmark_id"] == "RC_MENA"
            self.calls += 1
            if self.mutation_trap:
                self.mutations += 1
            return dict(self.result)

    page = SelectionRoot({"selected": False, "reason": "not-in-page-root"})
    first_frame = SelectionRoot({
        "selected": True,
        "strategy": "bound-dataset-exact-identity",
        "bookmark_id": "RC_MENA",
        "bookmark_name": "MENA_Actual_sales",
    })
    later_frame = SelectionRoot(
        {"selected": True, "strategy": "should-never-run"},
        mutation_trap=True,
    )
    page.frames = [first_frame, later_frame]

    result = flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )

    assert result["strategy"] == "bound-dataset-exact-identity"
    assert page.calls == 1
    assert first_frame.calls == 1
    assert later_frame.calls == 0
    assert later_frame.mutations == 0


def test_one_selection_attempt_cannot_mutate_again_in_a_later_root():
    class SelectionRoot:
        def __init__(self, result):
            self.frames = []
            self.result = result
            self.calls = 0

        def evaluate(self, script, argument=None):
            if script == flow_gscm._VISIBLE_COMPONENT_IDS_JS:
                return [FAVORITE_GRID_ID]
            assert script == flow_gscm._SELECT_BOOKMARK_ROW_JS
            self.calls += 1
            return dict(self.result)

    page = SelectionRoot({
        "selected": False,
        "attempted": True,
        "reason": "rowposition-rejected",
    })
    later_frame = SelectionRoot({
        "selected": True,
        "attempted": True,
        "strategy": "should-never-run",
    })
    page.frames = [later_frame]

    result = flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )

    assert result["reason"] == "rowposition-rejected"
    assert page.calls == 1
    assert later_frame.calls == 0


def test_native_selection_script_resolves_the_exact_visible_grid_path():
    script = flow_gscm._SELECT_BOOKMARK_ROW_JS

    assert "document.querySelectorAll('[id]')" not in script
    assert "endsWith(request.grid_suffix)" in script
    assert "request.grid_id" in script
    assert "const grid = resolveComponent(gridId);" in script
    assert "collection.get_item(key)" in script
    assert "['components', 'frames', 'all']" in script
    assert "visit(app.mainframe" not in script
    assert "verifiedCurrentRow !== rowIndex" in script
    assert "verifiedSelectedRows.length === 1" in script


def test_unsupported_frame_cannot_hide_the_portal_roots_selection_failure():
    class SelectionRoot:
        def __init__(self, result):
            self.frames = []
            self.result = result

        def evaluate(self, script, argument=None):
            if script == flow_gscm._VISIBLE_COMPONENT_IDS_JS:
                return [FAVORITE_GRID_ID]
            assert script == flow_gscm._SELECT_BOOKMARK_ROW_JS
            return dict(self.result)

    page = SelectionRoot({
        "selected": False,
        "reason": "favorite-grid-component-missing",
    })
    page.frames = [SelectionRoot({
        "selected": False,
        "reason": "nexacro-unavailable",
    })]

    result = flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )

    assert result["reason"] == "favorite-grid-component-missing"


def test_exact_visible_grid_path_can_be_resolved_in_another_native_root():
    class SelectionRoot:
        def __init__(self, result):
            self.frames = []
            self.result = result
            self.calls = 0

        def evaluate(self, script, argument=None):
            if script == flow_gscm._VISIBLE_COMPONENT_IDS_JS:
                return [FAVORITE_GRID_ID]
            assert script == flow_gscm._SELECT_BOOKMARK_ROW_JS
            self.calls += 1
            return dict(self.result)

    page = SelectionRoot({
        "selected": False,
        "reason": "favorite-grid-component-unresolved",
        "grid_id": FAVORITE_GRID_ID,
    })
    later_frame = SelectionRoot({
        "selected": True,
        "strategy": "resolved-in-native-root",
    })
    page.frames = [later_frame]

    result = flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )

    assert result["strategy"] == "resolved-in-native-root"
    assert page.calls == 1
    assert later_frame.calls == 1


class SplitDomNativeGscmPage(FakeGscmPage):
    """Production topology where rendered paths and Nexacro live separately."""

    class NativeRoot:
        def __init__(self, owner):
            self.owner = owner
            self.requests = []

        def evaluate(self, script, argument=None):
            if script in {
                flow_gscm._VISIBLE_COMPONENT_IDS_JS,
                flow_gscm._RENDERED_REPORT_TITLES_JS,
            }:
                return []
            if script in {
                flow_gscm._SELECT_BOOKMARK_ROW_JS,
                flow_gscm._GUARDED_GO_CLICK_JS,
                flow_gscm._GUARDED_EXCEL_EXPORT_JS,
            }:
                self.requests.append((script, dict(argument or {})))
                return FakeGscmPage._evaluate(
                    self.owner, script, argument,
                    components=self.owner.components,
                )
            return None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.native_root = self.NativeRoot(self)
        self._frames = [self.native_root]

    def _evaluate(self, script, argument=None, components=None):
        if script in {
            flow_gscm._SELECT_BOOKMARK_ROW_JS,
            flow_gscm._GUARDED_GO_CLICK_JS,
            flow_gscm._GUARDED_EXCEL_EXPORT_JS,
        }:
            return {"selected" if script == flow_gscm._SELECT_BOOKMARK_ROW_JS else "fired": False,
                    "reason": "nexacro-unavailable"}
        return super()._evaluate(script, argument, components)


def test_exact_paths_bridge_split_dom_and_nexacro_roots_for_go_and_export():
    page = SplitDomNativeGscmPage(
        dialog_open=True,
        dataset_rows=[_dataset_bookmark(
            "MENA_Actual_sales", "RC_MENA", "PUBLIC",
        )],
    )
    page.components.add(flow_gscm.GO_BUTTON_ID)

    selected = flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )
    assert selected["selected"] is True
    assert flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", selected["grid_id"],
    )["activated"] is True
    flow_gscm.trigger_excel_export(page, _run_job(), timeout_ms=1_000)

    native_requests = [request for _script, request in page.native_root.requests]
    assert any(request.get("grid_id") == FAVORITE_GRID_ID for request in native_requests)
    assert any(request.get("title_id") == LOADED_TITLE_ID.split(":", 1)[0]
               and request.get("excel_id") == EXCEL_BUTTON
               for request in native_requests)
    assert page.guarded_go_fires == 1
    assert page.guarded_export_fires == 1


def test_opening_a_bookmark_prefers_the_native_nexacro_go_button():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    page.components.add(flow_gscm.GO_BUTTON_ID)

    flow_gscm.open_bookmark(page, _run_job())

    assert page.guarded_go_fires == 1
    target_id = next(row["id"] for row in PUBLIC_TREE if row["text"] == "MENA_Actual_sales")
    assert target_id not in page.clicks
    assert flow_gscm.GO_BUTTON_ID not in page.clicks
    assert flow_gscm.GO_BUTTON_ID + ":text" not in page.clicks


def test_opening_a_bookmark_selects_the_tab_it_was_catalogued_under():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("Biz_trip_GSCM", "RC_PRIVATE", "PRIVATE"),
    ])
    flow_gscm.open_bookmark(
        page,
        _run_job(
            name="Biz_trip_GSCM", folder=(), tab="Private",
            bookmark_id="RC_PRIVATE",
        ),
    )
    assert page.tab == "Private"


def test_repeated_rendered_names_cannot_override_the_exact_dataset_identity():
    tree = [
        _label("SCM", ROOT_X, 560),
        _label("Asia", FOLDER_X, 584),
        _label("Weekly PSI", LEAF_X, 608, element_id="row.asia.weekly"),
        _label("MENA", FOLDER_X, 632),
        _label("Weekly PSI", LEAF_X, 656, element_id="row.mena.weekly"),
    ]
    page = FakeGscmPage(
        trees={"Private": [], "Public": tree, "Custom": []},
        dataset_rows=[_dataset_bookmark("Weekly PSI", "RC_WEEKLY", "PUBLIC")],
    )
    flow_gscm.open_bookmark(
        page,
        _run_job(
            name="Weekly PSI", folder=("SCM", "MENA"),
            bookmark_id="RC_WEEKLY",
        ),
    )
    assert "row.mena.weekly" not in page.clicks
    assert "row.asia.weekly" not in page.clicks
    assert page.selected_bookmark_id == "RC_WEEKLY"
    assert page.guarded_go_fires == 1


@pytest.mark.parametrize(
    ("stored", "rendered"),
    [
        (["SCM", "Actual Sales"], ["scm", "actual sales"]),
        (["AS", "Actual Sales"], ["SCM", "Actual Sales"]),
        (["SCM", "Actual Sales"], ["SCM", "Region", "Actual Sales"]),
        (["SCM", "Region", "Actual Sales"], ["AS", "Actual Sales"]),
    ],
)
def test_bookmark_paths_tolerate_case_module_codes_and_ordered_detail(stored, rendered):
    assert flow_gscm._paths_compatible(stored, rendered) is True
    assert flow_gscm._paths_compatible(rendered, stored) is True


def test_bookmark_paths_do_not_cross_unrelated_branches():
    assert flow_gscm._paths_compatible(
        ["SCM", "Actual Sales"], ["SCM", "Sell-in Biz Plan"],
    ) is False


def test_dataset_inaccessible_session_fails_without_reloading_or_tree_fallback():
    page = FakeGscmPage()

    with pytest.raises(RuntimeError, match="was not present") as excinfo:
        flow_gscm.open_bookmark(page, _run_job())

    assert len(page.navigations) <= 1
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True
    assert "Favorite state:" in str(excinfo.value)


def test_a_deleted_bookmark_names_what_is_still_listed():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.open_bookmark(
            page,
            _run_job(name="Gone report", folder=(), bookmark_id="RC_GONE"),
        )
    message = str(excinfo.value)
    assert "Gone report" in message
    assert "RC_GONE" in message


def test_excel_export_clicks_the_mdi_toolbar_button():
    page = FakeGscmPage(loaded_report_titles=["MENA_Actual_sales"])
    flow_gscm.trigger_excel_export(page, _run_job())
    assert page.clicks[-1] == EXCEL_BUTTON
    assert page.guarded_export_fires == 1


def test_excel_export_falls_back_to_the_component_name_when_the_path_changed():
    page = FakeGscmPage(loaded_report_titles=["MENA_Actual_sales"])
    moved = "mainframe.VFrameSet.MdiFrame.form.div_toolbar.form.btn_exceldown"
    page.components.discard(EXCEL_BUTTON)
    page.components.add(moved)
    flow_gscm.trigger_excel_export(page, _run_job())
    assert page.clicks[-1] == moved
    assert page.guarded_export_fires == 1


@pytest.mark.parametrize(
    "rendered",
    ["mena_actual_sales", "MENA_Actual_ sales", "MENA_Actual_sales 2"],
)
def test_excel_export_blocks_a_non_exact_rendered_bookmark_title(rendered):
    page = FakeGscmPage(loaded_report_titles=[rendered])

    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.trigger_excel_export(page, _run_job())

    message = str(excinfo.value)
    assert "loaded-report-title-mismatch" in message
    assert "expected_name='MENA_Actual_sales'" in message
    assert f"observed_name={rendered!r}" in message
    assert page.guarded_export_fires == 0
    assert EXCEL_BUTTON not in page.clicks


def test_excel_export_ignores_outer_whitespace_in_the_rendered_title():
    page = FakeGscmPage(loaded_report_titles=["  MENA_Actual_sales\r\n"])

    flow_gscm.trigger_excel_export(page, _run_job())

    assert page.guarded_export_fires == 1
    assert page.clicks[-1] == EXCEL_BUTTON


def test_excel_export_blocks_ambiguous_loaded_report_titles():
    page = FakeGscmPage(loaded_report_titles=[
        "MENA_Actual_sales", "MX B2B Actual Sales",
    ])

    with pytest.raises(RuntimeError, match="ambiguous-loaded-report-title"):
        flow_gscm.trigger_excel_export(page, _run_job())

    assert page.guarded_export_fires == 0
    assert EXCEL_BUTTON not in page.clicks


def test_native_title_drift_after_dom_observation_blocks_export():
    page = SplitDomNativeGscmPage(
        loaded_report_titles=["MENA_Actual_sales"],
    )
    native_evaluate = page.native_root.evaluate

    def drift_before_native_guard(script, argument=None):
        if script == flow_gscm._GUARDED_EXCEL_EXPORT_JS:
            page.loaded_report_titles = ["Different_report"]
        return native_evaluate(script, argument)

    page.native_root.evaluate = drift_before_native_guard

    with pytest.raises(RuntimeError, match="loaded-report-title-mismatch"):
        flow_gscm.trigger_excel_export(page, _run_job(), timeout_ms=1_000)

    assert page.guarded_export_fires == 0
    assert EXCEL_BUTTON not in page.clicks


def test_excel_export_blocks_when_active_workframe_title_is_unavailable():
    page = FakeGscmPage(loaded_report_titles=[])

    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.trigger_excel_export(page, _run_job(), timeout_ms=1)

    assert "loaded-report-title-unavailable" in str(excinfo.value)
    assert page.guarded_export_fires == 0
    assert EXCEL_BUTTON not in page.clicks


def test_excel_title_guard_requires_the_stable_bookmark_identity():
    page = FakeGscmPage(loaded_report_titles=["MENA_Actual_sales"])
    job = _run_job(bookmark_id=None)

    with pytest.raises(RuntimeError, match="no exact bookmark ID/name identity"):
        flow_gscm.trigger_excel_export(page, job)

    assert page.guarded_export_attempts == 0
    assert page.guarded_export_fires == 0


def test_excel_title_proof_excludes_inactive_mdi_and_favorite_surfaces():
    source = flow_gscm._GUARDED_EXCEL_EXPORT_JS
    inventory = flow_gscm._RENDERED_REPORT_TITLES_JS

    assert "workFramePattern" in inventory
    assert "mdiframe" in inventory
    assert "favorite" in inventory
    assert "rankTitleId" in inventory
    assert "const target = resolveComponent(excelId);" in source
    assert "document.querySelectorAll('[id]')" not in source
    assert "visit(app.mainframe" not in source
    assert "observedName !== wantedName" in source
    assert source.index("observedName !== wantedName") < source.index(
        "target.on_fire_onclick",
    )


def test_missing_excel_button_reports_the_screen():
    page = FakeGscmPage(loaded_report_titles=["MENA_Actual_sales"])
    page.components.discard(EXCEL_BUTTON)
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.trigger_excel_export(page, _run_job(), timeout_ms=1)
    assert "On screen:" in str(excinfo.value)
    assert "missing-excel-component" in str(excinfo.value)
    assert page.guarded_export_fires == 0


# ── Server wiring ──


def _gscm_site():
    return flows.SiteWrite(
        name="GSCM test",
        adapter="gscm_portal",
        auth_url="https://mdscm.sec.samsung.net/nexa/index.html",
        base_url="https://mdscm.sec.samsung.net/",
        discovery_enabled=True,
        discovery_scope=["Favorites"],
    )


def test_gscm_site_is_registered_out_of_the_box(flow_db):
    catalog = flows.catalog()
    gscm = [site for site in catalog["sites"] if site["adapter"] == "gscm_portal"]
    assert len(gscm) == 1
    assert gscm[0]["name"] == "GSCM"
    assert gscm[0]["supports_discovery"] is True
    # GSCM has no Metronome-held credential: its session lives in the
    # automation browser profile behind Samsung SSO.
    assert gscm[0]["credentials_configured"] is False
    # A cheap names-only pass is an ASAP concept; GSCM's sweep is one dialog.
    assert gscm[0]["supports_partial_scan"] is False


def test_registering_gscm_is_idempotent_across_migrations(flow_db):
    database.init_db()
    catalog = flows.catalog()
    assert len([site for site in catalog["sites"] if site["adapter"] == "gscm_portal"]) == 1


def test_scanning_a_gscm_site_queues_a_bookmark_scan(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    queued = flows.queue_catalog_scan(site["id"], _request(), mode="partial")

    assert queued["status"] == "queued"
    # GSCM has no partial mode to fall back to.
    assert queued["mode"] == "full"
    scan = flows.list_scans(site_id=site["id"], limit=50)[0]
    assert scan["job"]["site"]["adapter"] == "gscm_portal"


def test_scanning_an_unsupported_website_is_refused(flow_db):
    site = flows.create_site(flows.SiteWrite(
        name="Other portal", base_url="https://reports.example.test",
    ), _request())
    with pytest.raises(HTTPException) as excinfo:
        flows.queue_catalog_scan(site["id"], _request())
    assert excinfo.value.status_code == 400


def test_scheduled_discovery_covers_gscm_as_well_as_asap(flow_db):
    flows.create_site(_gscm_site(), _request())
    flows.create_site(flows.SiteWrite(
        name="ASAP test", adapter="asap_portal",
        auth_url="https://portal.example.test/portal/login/app",
        discovery_enabled=True,
    ), _request())
    with database.get_db() as db:
        db.execute("UPDATE flow_sites SET next_scan_at=NULL WHERE discovery_enabled=1")

    result = flows.queue_due_catalog_scans()
    with database.get_db() as db:
        adapters = {
            row["adapter"]
            for row in db.execute(
                """SELECT s.adapter FROM flow_catalog_scans c
                   JOIN flow_sites s ON s.id=c.site_id""",
            ).fetchall()
        }
    assert result["count"] >= 2
    assert {"gscm_portal", "asap_portal"} <= adapters


def _discover_into_catalog(site_id):
    """Push the worker's discovery payload through the real scan endpoint."""
    reports, complete = flow_gscm.discover_catalog(
        FakeGscmPage(), _scan_job(), _collect_progress()[1],
    )
    with database.get_db() as db:
        site = db.execute("SELECT * FROM flow_sites WHERE id=?", (site_id,)).fetchone()
        scan_id, _browser_mode = flows._queue_scan(db, site, "manual", "Analyst")
        db.execute(
            "UPDATE flow_catalog_scans SET worker_id='w1', status='claimed' WHERE id=?",
            (scan_id,),
        )
    flows.update_scan("w1", scan_id, flows.ScanProgress(
        status="succeeded",
        progress={"stage": "complete", "message": "done"},
        reports=[flows.DiscoveredReport(**item) for item in reports],
        complete=complete,
    ))
    return scan_id


def _gscm_discovered(name, bookmark_id):
    return flows.DiscoveredReport(
        discovery_key=f"Public > SCM > Actual Sales > {name}",
        name=name,
        report_url="https://mdscm.sec.samsung.net/nexa/index.html",
        download_text="Excel download",
        automation={
            "kind": "gscm_favorite",
            "category_path": ["Public", "SCM", "Actual Sales", name],
            "favorite_tab": "Public",
            "favorite_name": name,
            "favorite_folder_path": ["SCM", "Actual Sales"],
            "favorite_bookmark_id": bookmark_id,
        },
        filters=[],
    )


def test_raw_bookmark_identity_round_trips_unchanged_into_a_run_job(flow_db):
    raw_name = "  Weekly  PSI\x01Unsafe  "
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark(raw_name, "RC_RAW", "PUBLIC"),
    ])
    entry = flow_gscm.bookmark_dataset_entries(page)[0]
    discovered = flow_gscm.discovered_report(
        entry, "https://mdscm.sec.samsung.net/nexa/index.html",
    )

    assert discovered["name"] == "Weekly PSI Unsafe"
    assert discovered["automation"]["favorite_name"] == "Weekly  PSI\x01Unsafe"

    validated = flows.DiscoveredReport.model_validate_json(
        flows.DiscoveredReport(**discovered).model_dump_json(),
    )
    assert validated.automation["favorite_name"] == "Weekly  PSI\x01Unsafe"

    site = flows.create_site(_gscm_site(), _request())
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [validated], "2026-09-03T10:00:00",
        )
        report = db.execute(
            "SELECT id, automation_json FROM flow_reports WHERE site_id=?",
            (site["id"],),
        ).fetchone()
        stored = json.loads(report["automation_json"])
    assert stored["favorite_name"] == "Weekly  PSI\x01Unsafe"

    saved = flows.create_flow(flows.FlowWrite(
        name="Raw identity flow",
        site_id=site["id"],
        report_id=report["id"],
        period_strategy="none",
        file_format="xlsx",
        target_folder="C:\\Reports",
        filename_template="{flow}.xlsx",
    ), _request())
    job = flows.queue_run(saved["id"], _request())["job"]

    assert job["report"]["name"].endswith("Weekly PSI Unsafe")
    assert job["report"]["automation"]["favorite_name"] == (
        "Weekly  PSI\x01Unsafe"
    )
    assert job["report"]["automation"]["favorite_bookmark_id"] == "RC_RAW"


def _catalogued(site_id, suffix):
    return next(
        report for report in flows.catalog()["reports"]
        if report["site_id"] == site_id and report["name"].endswith(suffix)
    )


def test_discovered_bookmarks_become_catalog_reports(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])

    bookmark = _catalogued(site["id"], "MENA_Actual_sales")
    assert bookmark["name"] == "Public > SCM > Actual Sales > MENA_Actual_sales"
    assert bookmark["automation"]["favorite_tab"] == "Public"
    assert bookmark["filters"] == []


def test_literal_copy_suffix_is_reserved_from_synthetic_names_end_to_end(flow_db):
    page = FakeGscmPage(
        trees={"Private": [], "Public": [], "Custom": []},
        dataset_rows=[
            _dataset_bookmark("Budget", "RC_BUDGET_1", "PUBLIC"),
            _dataset_bookmark("Budget", "RC_BUDGET_2", "PUBLIC"),
            _dataset_bookmark("Budget (2)", "RC_LITERAL_2", "PUBLIC"),
        ],
    )
    reports, complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert complete is True
    assert [report["name"] for report in reports] == [
        "Budget", "Budget (3)", "Budget (2)",
    ]

    site = flows.create_site(_gscm_site(), _request())
    with database.get_db() as db:
        result = flows._apply_discovery(
            db,
            site["id"],
            [flows.DiscoveredReport(**report) for report in reports],
            "2026-08-27T12:00:00",
            complete=complete,
        )
        rows = db.execute(
            "SELECT name, automation_json FROM flow_reports WHERE site_id=? ORDER BY id",
            (site["id"],),
        ).fetchall()

    assert result["report_count"] == 3
    assert [row["name"].rsplit(" > ", 1)[-1] for row in rows] == [
        "Budget", "Budget (3)", "Budget (2)",
    ]
    assert {
        json.loads(row["automation_json"])["favorite_bookmark_id"] for row in rows
    } == {"RC_BUDGET_1", "RC_BUDGET_2", "RC_LITERAL_2"}


def test_each_gscm_scan_preserves_the_same_stable_id_catalog_row(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    bookmark = _gscm_discovered("Current bookmark", "RC_1")
    with database.get_db() as db:
        first = flows._apply_discovery(
            db, site["id"], [bookmark], "2026-08-21T10:00:00",
        )
        first_id = db.execute(
            "SELECT id FROM flow_reports WHERE site_id=?", (site["id"],),
        ).fetchone()["id"]
        flows._store_timings(
            db, [{"phase": "report_discovery", "duration_ms": 50}],
            operation_type="catalog_scan", site_id=site["id"], report_id=first_id,
        )

        second = flows._apply_discovery(
            db, site["id"], [bookmark], "2026-08-21T11:00:00",
        )
        current = db.execute(
            "SELECT id, enabled, stale FROM flow_reports WHERE site_id=?", (site["id"],),
        ).fetchall()
        timing_report_id = db.execute(
            "SELECT report_id FROM flow_operation_timings WHERE site_id=?", (site["id"],),
        ).fetchone()["report_id"]

    assert first["reset_report_count"] == 0
    assert second["reset_report_count"] == 0
    assert len(current) == 1
    assert current[0]["id"] == first_id
    assert (current[0]["enabled"], current[0]["stale"]) == (1, 0)
    assert timing_report_id == first_id


def test_gscm_snapshot_preserves_only_missing_bookmarks_used_by_flows(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    disposable = _gscm_discovered("Old unreferenced bookmark", "RC_OLD")
    referenced = _gscm_discovered("Old referenced bookmark", "RC_USED")
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [disposable, referenced], "2026-08-21T10:00:00",
        )
        referenced_id = db.execute(
            "SELECT id FROM flow_reports WHERE discovery_key=?", (referenced.discovery_key,),
        ).fetchone()["id"]

    flows.create_flow(flows.FlowWrite(
        name="Existing GSCM flow",
        site_id=site["id"],
        report_id=referenced_id,
        period_strategy="none",
        file_format="xlsx",
        target_folder="C:\\Reports",
        filename_template="{flow}.xlsx",
    ), _request())

    replacement = _gscm_discovered("New bookmark", "RC_NEW")
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [replacement], "2026-08-21T11:00:00",
        )
        rows = db.execute(
            "SELECT id, discovery_key, enabled, stale FROM flow_reports WHERE site_id=? ORDER BY id",
            (site["id"],),
        ).fetchall()

    assert result["reset_report_count"] == 1
    assert result["preserved_referenced_report_count"] == 1
    assert len(rows) == 2
    tombstone = next(row for row in rows if row["id"] == referenced_id)
    active = next(row for row in rows if row["discovery_key"] == replacement.discovery_key)
    assert (tombstone["enabled"], tombstone["stale"]) == (0, 1)
    assert (active["enabled"], active["stale"]) == (1, 0)
    assert all(row["discovery_key"] != disposable.discovery_key for row in rows)


def test_an_empty_gscm_snapshot_cannot_erase_the_last_good_catalog(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    bookmark = _gscm_discovered("Last good bookmark", "RC_GOOD")
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [bookmark], "2026-08-21T10:00:00",
        )
        result = flows._apply_discovery(
            db, site["id"], [], "2026-08-21T11:00:00",
        )
        row = db.execute(
            "SELECT enabled, stale FROM flow_reports WHERE site_id=?", (site["id"],),
        ).fetchone()

    assert result["ignored_empty_snapshot"] is True
    assert result["complete"] is False
    assert (row["enabled"], row["stale"]) == (1, 0)


def test_a_gscm_snapshot_with_rejected_bookmarks_keeps_the_last_good_catalog(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    existing = _gscm_discovered("Last complete bookmark", "RC_GOOD")
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [existing], "2026-08-21T10:00:00",
        )
        site_row = dict(db.execute(
            "SELECT * FROM flow_sites WHERE id=?", (site["id"],),
        ).fetchone())
        scan_id, _browser_mode = flows._queue_scan(db, site_row, "manual", "Analyst")
        db.execute(
            "UPDATE flow_catalog_scans SET worker_id='gscm-worker', status='claimed' WHERE id=?",
            (scan_id,),
        )

    replacement = _gscm_discovered("Incomplete replacement", "RC_NEW")
    body = flows.ScanProgress(status="succeeded", reports=[
        replacement.model_dump(),
        {
            "discovery_key": "Public > Invalid", "name": "Invalid",
            "report_url": "https://mdscm.sec.samsung.net/nexa/index.html",
            "automation": {},
            "filters": [{
                "filter_key": "bad", "label": "Bad", "control_label": "Bad",
                "control_type": "unsupported", "options": ["x"],
            }],
        },
    ])
    response = flows.update_scan("gscm-worker", scan_id, body)
    with database.get_db() as db:
        rows = db.execute(
            "SELECT discovery_key, enabled, stale FROM flow_reports WHERE site_id=?",
            (site["id"],),
        ).fetchall()

    assert response["result"]["ignored_incomplete_snapshot"] is True
    assert len(response["result"]["skipped_reports"]) == 1
    assert [(row["discovery_key"], row["enabled"], row["stale"]) for row in rows] == [
        (existing.discovery_key, 1, 0),
    ]


def test_a_gscm_flow_downloads_one_file_with_no_period_selection(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    bookmark = _catalogued(site["id"], "MENA_Actual_sales")

    saved = flows.create_flow(flows.FlowWrite(
        name="GSCM MENA actuals",
        site_id=site["id"],
        report_id=bookmark["id"],
        period_strategy="none",
        file_format="xlsx",
        target_folder="C:\\Reports",
        filename_template="{flow}_{date}.xlsx",
    ), _request())
    job = flows.queue_run(saved["id"], _request())["job"]

    assert job["site"]["adapter"] == "gscm_portal"
    assert job["downloads"]["periods"] == [None]
    assert job["report"]["automation"]["favorite_name"] == "MENA_Actual_sales"
    assert job["report"]["filters"] == []


def test_a_gscm_flow_cannot_ask_for_a_week_range_gscm_does_not_expose(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    bookmark = _catalogued(site["id"], "MENA_Actual_sales")
    with pytest.raises(HTTPException) as excinfo:
        flows.create_flow(flows.FlowWrite(
            name="GSCM weekly",
            site_id=site["id"],
            report_id=bookmark["id"],
            period_strategy="latest",
            start_week="2026-W30",
            file_format="xlsx",
            target_folder="C:\\Reports",
            filename_template="{flow}.xlsx",
        ), _request())
    assert excinfo.value.status_code == 400


def test_a_gscm_flow_cannot_claim_to_download_csv(flow_db):
    # GSCM's toolbar export is a workbook. Accepting file_format="csv" would
    # send SQL a renamed binary instead of rows.
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    bookmark = _catalogued(site["id"], "MENA_Actual_sales")
    with pytest.raises(HTTPException) as excinfo:
        flows.create_flow(flows.FlowWrite(
            name="GSCM as csv",
            site_id=site["id"],
            report_id=bookmark["id"],
            period_strategy="none",
            file_format="csv",
            target_folder="C:\\Reports",
            filename_template="{flow}.csv",
        ), _request())
    assert excinfo.value.status_code == 400
    assert "Excel" in excinfo.value.detail


# ── The live-portal failures ──


def test_the_setting_gear_is_found_by_position_when_no_id_hint_matches():
    # The live portal's gear id matched none of the hints, and the scan failed
    # with "Setting > Favorite dialog did not open". It has no text either, so
    # position in the top bar is the only remaining signal.
    page = FakeGscmPage(gear_id="mainframe.VFrameSet.TopFrame.form.div_main.form.btn_env")
    _reports, _complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )
    assert page.dialog_open is True
    assert "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_env" in page.clicks


@pytest.mark.parametrize("suffix", [":text", ":icontext"])
def test_nexacro_child_label_ids_promote_the_parent_component_first(suffix):
    """Nexacro's visible child is not always the component that owns onclick.

    The live failure inventory named ``btn_setting:icontext`` and similar label
    children.  Clicking that HTML child can be a no-op; the adapter must try the
    native Button component before the rendered child id.
    """
    parent = "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting"

    candidates = list(flow_gscm._component_element_ids(parent + suffix))

    assert candidates
    assert candidates[0] == parent


class NativeOnlyControlPage(FakeGscmPage):
    """Setting/Public DOM clicks are inert; native Nexacro clicks change state."""

    PUBLIC_COMPONENT_ID = (
        "mainframe.VFrameSet.TopFrame.Setting0.form.div_favorite.form.btn_public"
    )
    PUBLIC_LABEL_ID = PUBLIC_COMPONENT_ID + ":text"

    def __init__(self, **kwargs):
        super().__init__(gear_id=GEAR_ID, **kwargs)
        self.native_clicks = []

    def _screen(self):
        rows = super()._screen()
        return [
            {**row, "id": self.PUBLIC_LABEL_ID}
            if row.get("text") == "Public" else row
            for row in rows
        ]

    def on_click(self, element_id):
        if element_id in {GEAR_ID, self.PUBLIC_LABEL_ID, self.PUBLIC_COMPONENT_ID}:
            # Playwright successfully clicked a rendered DOM node, but Nexacro
            # did not dispatch the component's onclick handler.
            self.clicks.append(element_id)
            return
        super().on_click(element_id)

    @staticmethod
    def _argument_ids(argument):
        if isinstance(argument, str):
            return [argument]
        if isinstance(argument, dict):
            return [str(value) for value in argument.values()]
        if isinstance(argument, (list, tuple)):
            return [str(value) for value in argument]
        return []

    def evaluate(self, script, argument=None):
        if "on_fire_onclick" in script:
            candidates = self._argument_ids(argument)
            if GEAR_ID in candidates:
                self.native_clicks.append(GEAR_ID)
                self.setting_open = True
                return {"available": True, "fired": True, "component_id": GEAR_ID}
            if self.PUBLIC_COMPONENT_ID in candidates:
                self.native_clicks.append(self.PUBLIC_COMPONENT_ID)
                self.tab = "Public"
                return {
                    "available": True,
                    "fired": True,
                    "component_id": self.PUBLIC_COMPONENT_ID,
                }
        return super().evaluate(script, argument)


class NativeOnlyGoButtonPage(FakeGscmPage):
    """The Go button's DOM click is inert; only the native fire opens the report."""

    GO_LABEL_ID = flow_gscm.GO_BUTTON_ID + ":text"

    def __init__(self, *, native_works=True, **kwargs):
        super().__init__(dialog_open=True, **kwargs)
        self.native_works = native_works
        self.native_clicks = []
        self.components.add(flow_gscm.GO_BUTTON_ID)

    def _screen(self):
        rows = super()._screen()
        return [
            {**row, "id": self.GO_LABEL_ID} if row.get("text") == "Go >>" else row
            for row in rows
        ]

    def on_click(self, element_id):
        if element_id in {self.GO_LABEL_ID, flow_gscm.GO_BUTTON_ID}:
            # Playwright's click landed on a rendered node, but Nexacro never
            # dispatched the Button's onclick - the dialog stays open.
            self.clicks.append(element_id)
            return
        super().on_click(element_id)

    def evaluate(self, script, argument=None):
        if "on_fire_onclick" in script and isinstance(argument, str):
            if argument.split(":", 1)[0] == flow_gscm.GO_BUTTON_ID:
                self.native_clicks.append(argument)
                if self.native_works:
                    self.dialog_open = False
                    self.setting_open = False
                    return {"available": True, "fired": True, "component_id": argument}
                return {"available": True, "fired": False, "reason": "onclick-unavailable"}
        return super().evaluate(script, argument)


def test_go_button_uses_only_the_atomic_guarded_native_dispatch():
    source = flow_gscm._GUARDED_GO_CLICK_JS
    assert "endsWith(request.grid_suffix)" in source
    assert "const grid = resolveComponent(gridId);" in source
    assert "const component = resolveComponent(componentId);" in source
    assert "visit(app.mainframe" not in source

    page = NativeOnlyGoButtonPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    assert flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )["selected"] is True

    result = flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", FAVORITE_GRID_ID,
    )

    assert result["activated"] is True
    assert page.dialog_open is False
    assert page.clicks == []
    assert page.native_clicks == []
    assert page.guarded_go_fires == 1


def test_go_button_failure_is_reported_as_activation_not_visibility():
    page = FakeGscmPage(
        dialog_open=True,
        dataset_rows=[_dataset_bookmark(
            "MENA_Actual_sales", "RC_MENA", "PUBLIC",
        )],
        dataset_selection_mode="guard_unavailable",
    )
    assert flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )["selected"] is True

    assert flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", FAVORITE_GRID_ID,
    )["activated"] is False

    job = {
        "site": {"auth_url": "https://mdscm.sec.samsung.net/nexa/index.html"},
        "report": {"automation": {"favorite_name": "MENA_Actual_sales",
                                  "favorite_tab": "Public",
                                  "favorite_bookmark_id": "RC_MENA",
                                  "favorite_folder_path": ["SCM", "Actual Sales"]}},
    }
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.open_bookmark(page, job)
    assert "guarded Go action did not open" in str(excinfo.value)
    assert "Go-shaped candidates" in str(excinfo.value)


class RelocatedGoButtonPage(FakeGscmPage):
    """A build that mounts the Favorite dialog under a different frame path.

    The hardcoded ``GO_BUTTON_ID`` does not exist here, and the Go button is
    icon-styled: it renders no ``Go >>`` caption text at all, so the label
    fallback cannot see it either. Only the Nexacro component tree reports
    its real id.
    """

    RELOCATED_GO_ID = (
        "mainframe.HFrameSet.TopFrame.SettingPopup.form.div_favorite.form.btn_go"
    )

    def __init__(self, **kwargs):
        super().__init__(dialog_open=True, **kwargs)
        self.components.add(self.RELOCATED_GO_ID)

    def _screen(self):
        return [row for row in super()._screen() if row.get("text") != "Go >>"]

    def evaluate(self, script, argument=None):
        if "btn_?go" in script:
            return [{"id": self.RELOCATED_GO_ID, "name": "btn_go", "text": ""}]
        return super().evaluate(script, argument)

    def on_click(self, element_id):
        if element_id == self.RELOCATED_GO_ID:
            self.clicks.append(element_id)
            self.dialog_open = False
            return
        super().on_click(element_id)


def test_go_button_is_rediscovered_when_the_hardcoded_id_is_stale():
    # The live failure: the Go button is plainly on screen, but this build
    # does not carry the component path the adapter guessed, and the icon
    # button renders no caption for the label fallback to find.
    page = RelocatedGoButtonPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    assert flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )["selected"] is True

    assert flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", FAVORITE_GRID_ID,
    )["activated"] is True

    assert page.RELOCATED_GO_ID not in page.clicks
    assert page.guarded_go_fires == 1
    assert page.dialog_open is False


def test_selection_drift_between_go_candidates_blocks_the_next_candidate():
    class DriftAfterMissingCandidatePage(RelocatedGoButtonPage):
        def evaluate(self, script, argument=None):
            if (
                script == flow_gscm._GUARDED_GO_CLICK_JS
                and str((argument or {}).get("go_id") or "").split(":", 1)[0]
                != self.RELOCATED_GO_ID
            ):
                self.guarded_go_attempts += 1
                self.grid_current_row = 0
                return {"fired": False, "reason": "missing-go-component"}
            return super().evaluate(script, argument)

    page = DriftAfterMissingCandidatePage(dataset_rows=[
        _dataset_bookmark("Other", "RC_OTHER", "PUBLIC"),
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    page.components.add(flow_gscm.GO_BUTTON_ID)
    assert flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )["selected"] is True

    result = flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", FAVORITE_GRID_ID,
    )

    assert result["activated"] is False
    assert result["reason"] == "bookmark-selection-drift"
    assert page.guarded_go_fires == 0
    assert page.dialog_open is True


def test_go_candidates_prefer_the_favorite_dialog_and_drop_forbidden_controls():
    class ManyGoButtonsPage(FakeGscmPage):
        def __init__(self):
            super().__init__(dialog_open=True)
            # A DOM id that merely contains the hint must not become a
            # candidate: "btn_gotohome" is not a Go button.
            self.components.add("mainframe.TopFrame.form.btn_gotohome")

        def evaluate(self, script, argument=None):
            if "btn_?go" in script:
                return [
                    {"id": "mainframe.WorkFrame.form.btn_go", "name": "btn_go", "text": ""},
                    {"id": flow_gscm.GO_BUTTON_ID, "name": "btn_go", "text": "Go >>"},
                    # "save" is forbidden: a Go-named control inside a save
                    # panel is never worth the risk.
                    {"id": "mainframe.SavePanel.form.btn_go", "name": "btn_go", "text": ""},
                ]
            return super().evaluate(script, argument)

    page = ManyGoButtonsPage()
    candidates = flow_gscm._discover_go_candidates(page)

    assert candidates[0] == flow_gscm.GO_BUTTON_ID
    assert "mainframe.WorkFrame.form.btn_go" in candidates
    assert all("SavePanel" not in item for item in candidates)
    assert all("gotohome" not in item for item in candidates)


def test_setting_falls_back_to_native_nexacro_click_when_dom_click_is_a_noop():
    page = NativeOnlyControlPage()

    flow_gscm.open_favorites_dialog(page)

    assert page.dialog_open is True
    assert GEAR_ID in page.clicks  # the inert DOM click was attempted first
    assert GEAR_ID in page.native_clicks


def test_public_tab_falls_back_from_text_child_to_native_parent_component():
    page = NativeOnlyControlPage(dialog_open=True)
    page.tab = "Private"

    assert flow_gscm.select_scope_tab(page, "Public") is True

    assert page.tab == "Public"
    assert page.PUBLIC_COMPONENT_ID in page.clicks
    assert page.native_clicks[-1] == page.PUBLIC_COMPONENT_ID


def test_the_inventory_reports_icon_only_controls():
    # A text-only inventory could not show the gear, which is exactly the
    # control the failure needed to reveal.
    page = FakeGscmPage(gear_id="mainframe.VFrameSet.TopFrame.form.div_main.form.btn_env")
    inventory = flow_gscm.screen_inventory(page)
    assert "ICON CONTROLS:" in inventory
    assert "btn_env" in inventory


def test_a_collapsed_folder_is_expanded_so_its_reports_are_catalogued():
    # Your Public tree shows SCM collapsed next to MDM expanded. A scan that
    # reads only what is rendered would silently miss everything under SCM.
    tree = [
        _label("MDM", ROOT_X, 560),
        _label("Channel Site", FOLDER_X, 584),
    ]
    hidden = {"Channel Site": [
        _label("CS_IRAN", LEAF_X, 608),
        _label("CS_SEEG", LEAF_X, 630),
    ]}
    page = FakeGscmPage(
        trees={"Private": [], "Public": tree, "Custom": []}, hidden_rows=hidden,
    )
    reports = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])[0]
    names = {item["name"] for item in reports}
    assert names == {"CS_IRAN", "CS_SEEG"}
    assert all(
        item["automation"]["favorite_folder_path"] == ["MDM", "Channel Site"]
        for item in reports
    )


def test_rows_below_the_fold_are_reached_by_scrolling():
    # The tree has a scrollbar and Nexacro grids virtualize: only the rows in
    # view exist in the DOM.
    page = FakeGscmPage(
        trees={"Private": [], "Public": [
            _label("MDM", ROOT_X, 560),
            _label("Channel Site", FOLDER_X, 584),
            _label("CS_IRAN", LEAF_X, 608),
        ], "Custom": []},
        scroll_rows=[_label("CS_SEEG", LEAF_X, 900)],
    )
    reports = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])[0]
    assert {"CS_IRAN", "CS_SEEG"} == {item["name"] for item in reports}


def test_virtual_grid_uses_its_native_increment_button_before_fallbacks():
    class NativeScrollbarPage(FakeGscmPage):
        def __init__(self):
            super().__init__(
                dialog_open=True,
                trees={"Private": [], "Public": [
                    _label("SCM", ROOT_X, 560),
                    _label("Actual Sales", FOLDER_X, 584),
                    _label("CS_IRAN", LEAF_X, 608),
                ], "Custom": []},
                scroll_rows=[_label("CS_SEEG", LEAF_X, 900)],
            )
            self.increment_id = (
                "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form."
                "grd_bookmark.vscrollbar.incbutton:icontext"
            )
            self.components.add(self.increment_id)

        def on_click(self, element_id):
            super().on_click(element_id)
            if element_id == self.increment_id:
                self.scrolled.add(self.tab)

    page = NativeScrollbarPage()

    assert flow_gscm.scroll_tree(page) is True
    assert page.clicks.count(page.increment_id) == flow_gscm.FAVORITE_SCROLL_PAGE_STEPS
    assert "CS_SEEG" in {entry["name"] for entry in flow_gscm.read_favorite_tree(page)}


def test_virtual_grid_reset_proves_the_native_scrollbar_reached_the_top():
    class NativeScrollbarPage(FakeGscmPage):
        def __init__(self):
            super().__init__(
                dialog_open=True,
                trees={"Private": [], "Public": [
                    _label("SCM", ROOT_X, 560),
                    _label("Actual Sales", FOLDER_X, 584),
                    _label("CS_IRAN", LEAF_X, 608),
                ], "Custom": []},
                scroll_rows=[_label("CS_SEEG", LEAF_X, 900)],
            )
            self.decrement_id = (
                "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form."
                "grd_bookmark.vscrollbar.decbutton:icontext"
            )
            self.components.add(self.decrement_id)
            self.scrolled.add("Public")

        def on_click(self, element_id):
            super().on_click(element_id)
            if element_id == self.decrement_id:
                self.scrolled.discard(self.tab)

    page = NativeScrollbarPage()

    assert flow_gscm.reset_tree(page) is True
    assert page.clicks.count(page.decrement_id) == 2 * flow_gscm.FAVORITE_SCROLL_PAGE_STEPS
    assert "CS_SEEG" not in {entry["name"] for entry in flow_gscm.read_favorite_tree(page)}


def test_report_rows_are_told_from_folders_by_the_tree_expand_control():
    # Folder rows expose a visible treeitembutton. Nexacro keeps the same
    # control hidden on bookmark leaves, including leaves at the end of a tree.
    page = FakeGscmPage()
    flow_gscm.open_favorites_dialog(page)
    flow_gscm.select_scope_tab(page, "Public")
    entries = flow_gscm.read_favorite_tree(page)
    by_name = {entry["name"]: entry for entry in entries}
    assert by_name["MENA_Actual_sales"]["is_folder"] is False
    assert by_name["Actual Sales"]["is_folder"] is True
    assert by_name["SCM"]["is_folder"] is True


def test_wait_for_favorite_rows_pumps_nexacro_until_grid_is_populated(monkeypatch):
    page = FakeGscmPage()
    checks = iter([[], [], [(page, {"text": "SCM"})]])
    monkeypatch.setattr(flow_gscm, "favorite_tree_rows", lambda _page: next(checks))

    assert flow_gscm.wait_for_favorite_rows(page, timeout_ms=10_000) is True


def test_expansion_never_clicks_a_report_row():
    # Selecting a report is harmless, but the tree sweep should still only
    # touch folders: fewer clicks, and nothing near the per-row pin control.
    page = FakeGscmPage()
    flow_gscm.open_favorites_dialog(page)
    flow_gscm.select_scope_tab(page, "Public")
    flow_gscm.collect_favorite_tree(page)
    clicked = set(page.clicks)
    leaf_ids = {
        row["id"] for row in PUBLIC_TREE if row["text"] in LEAF_NAMES
    }
    assert not (clicked & leaf_ids)


# ── Nothing this adapter clicks may change stored data ──


@pytest.mark.parametrize("label", ["Save", "Unselect", "Delete", "Apply", "Remove"])
def test_destructive_labels_are_refused_outright(label):
    page = FakeGscmPage(dialog_open=True)
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.click_label(page, [label])
    assert "Refusing to click" in str(excinfo.value)


def test_a_save_control_is_never_a_candidate_for_a_label_click():
    page = FakeGscmPage(dialog_open=True)
    flow_gscm.click_label(page, ["Public"])
    save_id = next(row["id"] for row in DIALOG_CHROME if row["text"] == "Save")
    assert save_id not in page.clicks


def test_a_full_scan_never_touches_save_unselect_or_a_pin():
    page = FakeGscmPage()
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    forbidden = {row["id"] for row in DIALOG_CHROME if row["text"] in {"Save", "Unselect"}}
    assert not (set(page.clicks) & forbidden)
    assert not any(item.endswith(".pin") for item in page.clicks)


def test_a_run_never_touches_save_unselect_or_a_pin():
    page = FakeGscmPage(dataset_rows=[
        _dataset_bookmark("MENA_Actual_sales", "RC_MENA", "PUBLIC"),
    ])
    flow_gscm.open_bookmark(page, _run_job())
    forbidden = {row["id"] for row in DIALOG_CHROME if row["text"] in {"Save", "Unselect"}}
    assert not (set(page.clicks) & forbidden)
    assert not any(item.endswith(".pin") for item in page.clicks)


def test_a_gear_candidate_named_save_is_never_tried():
    page = FakeGscmPage(gear_id="mainframe.VFrameSet.TopFrame.form.div_main.form.btn_save_all")
    with pytest.raises(RuntimeError):
        flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert not any("save" in item.casefold() for item in page.clicks)


# ── Not signed in ──


class FakeLoginPage(FakeGscmPage):
    """The Samsung SSO form, exactly as the live scan reported it."""

    def _screen(self):
        return [
            _label("Single Sign On Login", 465, 236, element_id=""),
            _label("Please enter your password.", 465, 266, element_id="loginMessage"),
            _label("Login", 465, 409, element_id="submitButton"),
            _label("AD SSO", 415, 575, element_id="contact"),
            _label("Change Password", 581, 692, element_id=""),
        ]

    def _icon_records(self):
        return []

    def evaluate(self, script, argument=None):
        if "getElementById(id)" in script:
            return False  # the Nexacro client never loads behind the form
        return super().evaluate(script, argument)


def test_the_sso_form_is_reported_as_not_signed_in():
    page = FakeLoginPage()
    assert flow_gscm.on_login_page(page) is True
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.wait_for_component(page, "mainframe.VFrameSet", timeout_ms=30_000)
    message = str(excinfo.value)
    assert "not signed in" in message
    # ASAP succeeding in the same worker is the confusing part; say so.
    assert "separate portals" in message
    assert "--authenticate-adapter" in message


def test_a_signed_in_portal_is_not_mistaken_for_the_login_form():
    assert flow_gscm.on_login_page(FakeGscmPage()) is False


class FakeKnoxPage(FakeGscmPage):
    """A Knox MFA step whose wording matches none of the exact SSO labels."""

    def _screen(self):
        return [
            _label("Knox Approval is required to continue", 465, 236, element_id=""),
            _label("Enter the verification code sent to your device", 465, 266, element_id=""),
        ]

    def _icon_records(self):
        return []

    def evaluate(self, script, argument=None):
        if "getElementById(id)" in script:
            return False
        return super().evaluate(script, argument)


class FakeRewordedSsoPage(FakeGscmPage):
    """One recognisable phrase only; the input probe must corroborate it.

    The username/password boxes carry no textContent and are wider than the
    icon sweep's 80px cut-off, so only the direct input probe can see them.
    """

    def _screen(self):
        return [_label("Sign in to your account", 465, 236, element_id="")]

    def _icon_records(self):
        return []

    def _login_inputs(self):
        return {"password": 1, "text": 1, "ids": ["userid", "password"]}

    def evaluate(self, script, argument=None):
        if "getElementById(id)" in script:
            return False
        return super().evaluate(script, argument)


def test_a_knox_mfa_page_is_reported_as_not_signed_in():
    # The exact-label markers miss Knox's wording; substring matching over the
    # joined page text must not.
    assert flow_gscm.on_login_page(FakeKnoxPage()) is True


def test_the_input_probe_recognises_a_reworded_sign_in_form():
    assert flow_gscm.on_login_page(FakeRewordedSsoPage()) is True


class SessionExpiredAtFavoritesPage(FakeGscmPage):
    """SSO reclaims the page the moment the Setting dialog is opened.

    This is the mid-flow expiry that used to fail with "bookmark tab was not
    on screen" plus a screen dump of the SSO form itself.
    """

    def __init__(self):
        super().__init__()
        self.expired = False

    def on_click(self, element_id):
        super().on_click(element_id)
        if self.dialog_open:
            self.expired = True

    def _screen(self):
        if self.expired:
            return [
                _label("Single Sign On Login", 465, 236, element_id=""),
                _label("Please enter your password.", 465, 266, element_id="loginMessage"),
                _label("Login", 465, 409, element_id="submitButton"),
                _label("AD SSO", 415, 575, element_id="contact"),
            ]
        return super()._screen()

    def _icon_records(self):
        return [] if self.expired else super()._icon_records()

    def evaluate(self, script, argument=None):
        if self.expired and "getElementById(id)" in script:
            return False
        return super().evaluate(script, argument)


def test_a_session_that_expires_mid_flow_reports_sign_in_not_a_screen_dump():
    page = SessionExpiredAtFavoritesPage()
    job = {
        "site": {"auth_url": "https://mdscm.sec.samsung.net/nexa/index.html"},
        "report": {"automation": {"favorite_name": "MENA_Actual_sales",
                                  "favorite_tab": "Public",
                                  "favorite_bookmark_id": "RC_MENA"}},
    }
    with pytest.raises(flow_gscm.NotSignedInError) as excinfo:
        flow_gscm.open_bookmark(page, job)
    message = str(excinfo.value)
    assert "not signed in" in message
    assert "bookmark tab was not on screen" not in message


def test_fail_with_screen_reports_sign_out_as_sign_out():
    with pytest.raises(flow_gscm.NotSignedInError):
        flow_gscm._fail_with_screen(FakeLoginPage(), "Anything was not on screen.")


def test_fail_with_screen_keeps_the_screen_dump_when_signed_in():
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm._fail_with_screen(FakeGscmPage(), "The gizmo was not on screen.")
    assert not isinstance(excinfo.value, flow_gscm.NotSignedInError)
    assert "On screen:" in str(excinfo.value)


def test_the_inventory_is_bounded_for_failure_messages():
    long_prefix = "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form.grd_bookmark.body"
    rows = [
        _label(f"Row {index}", 800, 500 + index,
               element_id=f"{long_prefix}.gridrow_{index}.cell_{index}_0.treeitemtext_{index}")
        for index in range(300)
    ]
    page = FakeGscmPage(trees={"Private": [], "Public": rows, "Custom": []},
                        dialog_open=True)

    inventory = flow_gscm.screen_inventory(page)

    assert len(inventory) <= flow_gscm.MAX_INVENTORY_CHARS + 50
    assert "(+" in inventory  # says how much was withheld
    assert long_prefix not in inventory  # ids are trimmed to their tails
    # the discriminating tail segments survive the trim
    assert "treeitemtext_0" in inventory


def test_wait_for_manual_login_returns_once_the_portal_renders():
    page = FakeGscmPage()
    page.components.discard("mainframe.VFrameSet")
    polls = []

    def wait_for_timeout(ms):
        polls.append(ms)
        if len(polls) >= 3:
            page.components.add("mainframe.VFrameSet")

    page.wait_for_timeout = wait_for_timeout
    progress = []

    flow_gscm.wait_for_manual_login(page, report_progress=progress.append)

    assert page.navigations == []  # SSO owns the redirects; never navigate
    assert len(polls) >= 3


def test_wait_for_manual_login_gives_up_with_the_sign_in_error(monkeypatch):
    page = FakeLoginPage()
    progress = []
    with pytest.raises(flow_gscm.NotSignedInError):
        flow_gscm.wait_for_manual_login(
            page, timeout_ms=2_000, report_progress=progress.append,
        )


def test_the_login_check_does_not_wait_out_the_full_budget():
    # The old message arrived after three minutes of polling for a client that
    # cannot load while a sign-in form owns the page.
    page = FakeLoginPage()
    waits = []
    page.wait_for_timeout = waits.append
    with pytest.raises(RuntimeError):
        flow_gscm.wait_for_component(page, "mainframe.VFrameSet", timeout_ms=180_000)
    assert sum(waits) < 30_000


def test_setup_registers_gscm_before_reading_the_sign_in_list(tmp_path):
    """The ordering bug that skipped GSCM's one-time sign-in during setup.

    setup.ps1 reads the site list to decide which portals to bootstrap, but the
    row is created by the migrations, which used to run only when the service
    started - after that read.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    database_path = tmp_path / "fresh.db"
    subprocess.run(
        [sys.executable, str(root / "tools" / "apply_migrations.py"), str(database_path)],
        check=True, capture_output=True,
    )
    listed = subprocess.run(
        [sys.executable, str(root / "tools" / "get_flow_auth_url.py"), str(database_path)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "gscm_portal\thttps://mdscm.sec.samsung.net/nexa/index.html" in listed


# ── The real top bar ──
#
# Read off the live portal's failure report. The gear is btn_setting @(1224,12);
# btn_user sits further right, and scrollbar arrows outnumber the real buttons.

LIVE_TOP_BAR_ICONS = [
    {"id": "mainframe.VFrameSet.HomeFrame.form.div_main.form.vscrollbar.decbutton", "x": 1357, "y": 49},
    {"id": "mainframe.VFrameSet.HomeFrame.form.div_main.form.vscrollbar.decbutton:icontext", "x": 1357, "y": 49},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.vscrollbar.decbutton", "x": 1345, "y": 1},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.vscrollbar.decbutton:icontext", "x": 1345, "y": 1},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.sta_confidential", "x": 1283, "y": 13},
    {"id": "mainframe.VFrameSet.TopFrame.form.pdv_logout.form.vscrollbar.decbutton", "x": 1281, "y": 36},
    {"id": "mainframe.VFrameSet.HomeFrame.form.vscrollbar.decbutton", "x": 1275, "y": 43},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_user", "x": 1250, "y": 12},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting", "x": 1224, "y": 12},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_scmLang", "x": 1197, "y": 12},
    {"id": "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_notice", "x": 1169, "y": 12},
]
LIVE_GEAR_ID = "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting"
LIVE_USER_ID = "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_user"


class LiveTopBarPage(FakeGscmPage):
    """The portal's real top bar, where the gear is not the rightmost icon."""

    def __init__(self, **kwargs):
        super().__init__(gear_id=LIVE_GEAR_ID, **kwargs)
        self.components.update(item["id"] for item in LIVE_TOP_BAR_ICONS)

    def _icon_records(self):
        if self.dialog_open:
            return _icons_for(self._rows())
        return [{**item, "w": 20, "h": 20} for item in LIVE_TOP_BAR_ICONS]


def test_the_live_gear_is_found_and_the_profile_icon_is_not_clicked():
    page = LiveTopBarPage()
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert LIVE_GEAR_ID in page.clicks
    # btn_user sits further right than the gear. Ranking candidates by screen
    # position opened a profile popover and reported the gear missing.
    assert LIVE_USER_ID not in page.clicks


def test_scrollbar_arrows_never_crowd_out_a_real_control():
    # Eight of the eleven text-less controls in the top bar are scrollbar
    # arrows and static decoration, which pushed the gear past the try limit.
    page = LiveTopBarPage()
    chrome = [
        item for _root, item in flow_gscm.icon_controls(page, include_chrome=False)
    ]
    identifiers = {item["id"] for item in chrome}
    assert LIVE_GEAR_ID in identifiers
    assert not any("scrollbar" in item for item in identifiers)
    assert not any(item.endswith(":icontext") for item in identifiers)


def test_id_hints_are_ranked_by_specificity_not_by_position():
    assert flow_gscm._hint_rank(LIVE_GEAR_ID, flow_gscm.SETTING_BUTTON_HINTS) == 0
    # btn_user must not be reachable through any Setting hint at all.
    assert flow_gscm._hint_rank(LIVE_USER_ID, flow_gscm.SETTING_BUTTON_HINTS) == len(
        flow_gscm.SETTING_BUTTON_HINTS
    )


def test_a_renamed_gear_still_falls_back_to_position():
    renamed = "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_zzz"
    page = LiveTopBarPage()
    page.components.discard(LIVE_GEAR_ID)
    page.components.add(renamed)
    page.gear_id = renamed
    page._icon_records = lambda: (
        _icons_for(page._rows()) if page.dialog_open else
        [{"id": renamed, "x": 1224, "y": 12, "w": 20, "h": 20}]
    )
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert renamed in page.clicks


def test_go_button_id_matches_the_live_portal_inspection():
    # Read off the real portal via DevTools: the dialog mounts as Setting0
    # and the Go control is btn_openFavorite, captioned by an :icontext child.
    assert flow_gscm.GO_BUTTON_ID == (
        "mainframe.VFrameSet.TopFrame.Setting0.form.div_favorite.form.btn_openFavorite"
    )
    assert flow_gscm._looks_like_go_component(
        "mainframe.vframeset.topframe.setting0.form.div_favorite.form"
        ".btn_openFavorite:icontext"
    )


def test_go_button_still_works_on_the_previous_builds_id():
    old_id = "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form.btn_go"

    class OldBuildPage(FakeGscmPage):
        def _screen(self):
            return [
                {**row, "id": old_id + ":text"}
                if row.get("text") == "Go >>" else row
                for row in super()._screen()
            ]

        def on_click(self, element_id):
            if element_id == old_id:
                self.clicks.append(element_id)
                self.dialog_open = False
                self.setting_open = False
                return
            super().on_click(element_id)

    page = OldBuildPage(
        dialog_open=True,
        dataset_rows=[_dataset_bookmark(
            "MENA_Actual_sales", "RC_MENA", "PUBLIC",
        )],
    )
    page.components.add(old_id)
    assert flow_gscm._select_bookmark_dataset_row(
        page, "RC_MENA", "MENA_Actual_sales",
    )["selected"] is True

    assert flow_gscm._click_go_button(
        page, "RC_MENA", "MENA_Actual_sales", FAVORITE_GRID_ID,
    )["activated"] is True
    assert old_id not in page.clicks
    assert page.guarded_go_fires == 1


def test_favorite_grid_matching_never_depends_on_the_setting_frame_index():
    # The dialog mounts as Setting0 on the current build and Setting1 on an
    # earlier one; every grid script matches the dialog-local tail instead.
    for script in (
        flow_gscm._FAVORITE_TREE_ROWS_JS,
        flow_gscm._SCROLL_TREE_JS,
        flow_gscm._RESET_TREE_JS,
    ):
        assert "Setting1" not in script
        assert "div_favorite.form.grd_bookmark" in script
    assert "Setting" not in flow_gscm.FAVORITE_GRID_ID_SUFFIX


# ── Scan worker routing ──


def _register_both_workers():
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headless", display_name="BI desktop - headless",
        capabilities={"headed": False},
    ))
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="BI desktop - headed",
        capabilities={"headed": True},
    ))


def test_gscm_scan_runs_on_the_same_worker_mode_as_the_sites_flows(flow_db, monkeypatch):
    """A scan of a site whose runs are headed must not go to the headless worker.

    The scan walks the portal exactly like a run (gear, Setting, Favorite),
    so it needs the same browser, profile, and session. Pinned to the
    headless service it opened a different browser where the same gear click
    failed with "the Setting > Favorite dialog did not open".
    """
    launched = []
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode="headless": launched.append(mode) or {"status": "starting", "mode": mode},
    )
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    bookmark = _catalogued(site["id"], "MENA_Actual_sales")
    flows.create_flow(flows.FlowWrite(
        name="GSCM headed flow",
        site_id=site["id"],
        report_id=bookmark["id"],
        period_strategy="none",
        file_format="xlsx",
        browser_mode="headed",
        target_folder="C:\\Reports",
        filename_template="{flow}.xlsx",
    ), _request())

    queued = flows.queue_catalog_scan(site["id"], _request(), mode="full")

    scan = flows.list_scans(site_id=site["id"], limit=50)[0]
    assert scan["job"]["execution"] == {"browser_mode": "headed"}
    assert launched[-1] == "headed"

    _register_both_workers()
    assert flows.claim_run("bi-desktop-headless")["scan"] is None
    claimed = flows.claim_run("bi-desktop-headed")
    assert claimed["scan"]["id"] == queued["id"]


def test_gscm_scan_mode_follows_the_most_recent_successful_run(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    bookmark = _catalogued(site["id"], "MENA_Actual_sales")
    saved = flows.create_flow(flows.FlowWrite(
        name="GSCM headed flow",
        site_id=site["id"],
        report_id=bookmark["id"],
        period_strategy="none",
        file_format="xlsx",
        browser_mode="headed",
        target_folder="C:\\Reports",
        filename_template="{flow}.xlsx",
    ), _request())
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_runs (flow_id, trigger_type, status, job_json, created_at, finished_at)
               VALUES (?, 'manual', 'succeeded', '{}', '2026-08-27T08:00:00', '2026-08-27T08:05:00')""",
            (saved["id"],),
        )
        site_row = dict(db.execute(
            "SELECT * FROM flow_sites WHERE id=?", (site["id"],),
        ).fetchone())
        assert flows._scan_browser_mode(db, site_row) == "headed"
        # The flow later switches to headless and succeeds there: the scan
        # follows the newest proof of what actually works on this site.
        db.execute("UPDATE flows SET browser_mode='headless' WHERE id=?", (saved["id"],))
        db.execute(
            """INSERT INTO flow_runs (flow_id, trigger_type, status, job_json, created_at, finished_at)
               VALUES (?, 'manual', 'succeeded', '{}', '2026-08-27T09:00:00', '2026-08-27T09:05:00')""",
            (saved["id"],),
        )
        assert flows._scan_browser_mode(db, site_row) == "headless"


def test_asap_scans_and_legacy_scan_jobs_stay_on_the_headless_worker(flow_db, monkeypatch):
    import json as _json_module

    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode="headless": {"status": "starting", "mode": mode},
    )
    asap = flows.create_site(flows.SiteWrite(
        name="ASAP test", adapter="asap_portal",
        auth_url="https://portal.example.test/portal/login/app",
        discovery_enabled=True,
    ), _request())
    queued = flows.queue_catalog_scan(asap["id"], _request(), mode="full")
    scan = flows.list_scans(site_id=asap["id"], limit=50)[0]
    assert scan["job"]["execution"] == {"browser_mode": "headless"}

    # A scan queued before browser-mode routing carries no execution block.
    with database.get_db() as db:
        job_json = db.execute(
            "SELECT job_json FROM flow_catalog_scans WHERE id=?", (queued["id"],),
        ).fetchone()["job_json"]
        job = _json_module.loads(job_json)
        job.pop("execution", None)
        db.execute(
            "UPDATE flow_catalog_scans SET job_json=? WHERE id=?",
            (_json_module.dumps(job), queued["id"]),
        )

    _register_both_workers()
    assert flows.claim_run("bi-desktop-headed")["scan"] is None
    assert flows.claim_run("bi-desktop-headless")["scan"]["id"] == queued["id"]
