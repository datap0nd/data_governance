"""GSCM portal adapter: bookmark discovery, catalog wiring, and flow runs.

GSCM is a separate website from ASAP with its own client framework and its own
data. Its bookmarks live in the Setting > Favorite dialog, split across the
Private, Public, and Custom tabs, each holding a folder tree that is nested by
on-screen indentation rather than by DOM structure. These tests pin that model
against a fake Nexacro screen, plus the catalog and flow wiring around it.
"""

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
    _label("Favorite", 600, 500),
    _label("Layout", 600, 600),
    _label("Dashboard", 600, 700),
    _label("Installation", 600, 800),
    _label("Private", 900, 500),
    _label("Public", 975, 500),
    _label("Custom", 1045, 500),
    _label("Alphabet", 810, 535),
    _label("Latest", 900, 535),
    _label("Unselect", 1340, 535),
    _label("Go >>", 1190, 945),
    _label("Save", 1270, 945),
    _label("Close", 1350, 945),
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


class FakeGscmPage:
    """Enough of a Playwright page to drive the adapter without a browser."""

    def __init__(self, *, trees=None, dialog_open=False, gear=True,
                 url="https://mdscm.sec.samsung.net/nexa/index.html",
                 always_busy=False, busy_polls=0, popup_ids=(),
                 hidden_rows=None, gear_id=None, scroll_rows=None,
                 dataset_rows=None):
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
        self.gear = gear
        self.url = url
        self.always_busy = always_busy
        self.busy_polls = busy_polls
        self.popup_ids = list(popup_ids)
        self.dataset_rows = dataset_rows
        self.tab = "Public"
        self.clicks = []
        self.navigations = []
        self.wait_window_hidden = 0
        self.components = {"mainframe.VFrameSet", EXCEL_BUTTON}
        if gear:
            self.components.add(self.gear_id)

    # -- state the adapter drives --

    def on_click(self, element_id):
        self.clicks.append(element_id)
        record = next(
            (item for item in self._screen() if item["id"] == element_id), None,
        )
        text = (record or {}).get("text", "")
        if element_id == self.gear_id or text == "Setting":
            self.dialog_open = True
        elif text in self.trees:
            self.tab = text
        elif text == "Close":
            self.dialog_open = False
        elif text == "Go >>" or element_id == flow_gscm.GO_BUTTON_ID:
            self.dialog_open = False
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
        if not self.dialog_open:
            return [_label("Favorite", 1480, 447)]  # the empty home widget
        return [*DIALOG_CHROME, *self._rows()]

    # -- Playwright surface --

    @property
    def frames(self):
        return []

    def goto(self, url, **_kwargs):
        self.navigations.append(url)
        self.url = url

    def wait_for_timeout(self, _ms):
        return None

    def _icon_records(self):
        if not self.dialog_open:
            return (
                [{"id": self.gear_id, "x": 1699, "y": 14, "w": 20, "h": 20}]
                if self.gear else []
            )
        return _icons_for(self._rows())

    def locator(self, selector):
        if selector.startswith("[id='"):
            wanted = selector[len("[id='"):-len("']")].replace("\\'", "'").replace("\\\\", "\\")
            matches = [wanted] if any(
                item["id"] == wanted for item in self._screen()
            ) or wanted in self.components else []
        elif selector.startswith("[id*='"):
            fragment = selector[len("[id*='"):-len("']")]
            matches = sorted(item for item in self.components if fragment in item)
        else:
            text = selector[len("text="):]
            matches = [item["id"] for item in self._screen() if item["text"] == text]
        return FakeLocator(self, selector, matches)

    def evaluate(self, script, argument=None):
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
            return self._icon_records()
        if "scrollHeight - element.clientHeight" in script:
            if self.scroll_rows and self.trees.get(self.tab) and self.tab not in self.scrolled:
                self.scrolled.add(self.tab)
                return {"moved": True, "top": 100, "max": 400}
            return {"moved": False, "top": 0, "max": 0}
        if "lowered.includes('popup')" in script:
            return list(self.popup_ids)
        if "hints.some" in script:
            return [
                {"id": item, "x": 1700, "y": 300}
                for item in sorted(self.components)
                if any(hint in item.lower() for hint in argument)
            ]
        if "getElementById(id)" in script:
            return argument in self.components
        raise AssertionError(f"unexpected evaluate: {script[:70]}")


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


def test_discovery_prefers_the_complete_nexacro_bookmark_dataset():
    page = FakeGscmPage(dataset_rows=DATASET_BOOKMARKS)
    events, progress = _collect_progress()

    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), progress)

    assert complete is True
    assert [report["name"] for report in reports] == [
        "Biz_Trip_Account_Portion", "MX B2B FFF8 Actual Sales", "Asia_Actual_sales",
    ]
    assert page.dialog_open is False
    assert GEAR_ID not in page.clicks
    assert not page.scrolled
    assert any("gds_bookmark" in detail["message"] for _status, detail in events)


def test_discovery_waits_for_the_bookmark_dataset_to_finish_loading():
    class DelayedDatasetPage(FakeGscmPage):
        def __init__(self):
            super().__init__(dataset_rows=DATASET_BOOKMARKS)
            self.dataset_reads = 0

        def evaluate(self, script, argument=None):
            if "app.gds_bookmark" in script:
                self.dataset_reads += 1
                if self.dataset_reads < 4:
                    return None
            return super().evaluate(script, argument)

    page = DelayedDatasetPage()
    reports, complete = flow_gscm.discover_catalog(
        page, _scan_job(), _collect_progress()[1],
    )

    assert complete is True
    assert page.dataset_reads == 4
    assert len(reports) == len(DATASET_BOOKMARKS)
    assert page.dialog_open is False
    assert GEAR_ID not in page.clicks


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


def test_wait_overlay_is_cleared_before_reading():
    page, _result = _discover()
    assert page.wait_window_hidden >= 1


def test_a_stuck_wait_overlay_is_forced_down_instead_of_hanging_the_run():
    page = FakeGscmPage(always_busy=True)
    assert flow_gscm.wait_for_calculation(page, timeout_ms=2_000) is False
    assert page.wait_window_hidden >= 1


# ── Download ──


def _run_job(name="MENA_Actual_sales", folder=("SCM", "Actual Sales"), tab="Public"):
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
                "excel_btn_id": EXCEL_BUTTON,
            },
        },
    }


def _clicked_texts(page):
    lookup = {item["id"]: item["text"] for item in DIALOG_CHROME}
    for tree in (PUBLIC_TREE, PRIVATE_TREE):
        lookup.update({item["id"]: item["text"] for item in tree})
    return [lookup.get(item, item) for item in page.clicks]


def test_opening_a_bookmark_selects_its_row_then_presses_go():
    page = FakeGscmPage()
    flow_gscm.open_bookmark(page, _run_job())
    texts = _clicked_texts(page)
    assert "MENA_Actual_sales" in texts
    assert texts.index("MENA_Actual_sales") < texts.index("Go >>")


def test_opening_a_bookmark_prefers_the_native_nexacro_go_button():
    page = FakeGscmPage()
    page.components.add(flow_gscm.GO_BUTTON_ID)

    flow_gscm.open_bookmark(page, _run_job())

    assert flow_gscm.GO_BUTTON_ID in page.clicks
    target_id = next(row["id"] for row in PUBLIC_TREE if row["text"] == "MENA_Actual_sales")
    assert page.clicks.index(target_id) < page.clicks.index(flow_gscm.GO_BUTTON_ID)
    assert "Go >>" not in _clicked_texts(page)


def test_opening_a_bookmark_selects_the_tab_it_was_catalogued_under():
    page = FakeGscmPage()
    flow_gscm.open_bookmark(page, _run_job(name="Biz_trip_GSCM", folder=(), tab="Private"))
    assert page.tab == "Private"


def test_the_folder_path_disambiguates_a_repeated_report_name():
    # The same report name is filed under several folders. Matching on the name
    # alone would download a different report than the flow was built for.
    tree = [
        _label("SCM", ROOT_X, 560),
        _label("Asia", FOLDER_X, 584),
        _label("Weekly PSI", LEAF_X, 608, element_id="row.asia.weekly"),
        _label("MENA", FOLDER_X, 632),
        _label("Weekly PSI", LEAF_X, 656, element_id="row.mena.weekly"),
    ]
    page = FakeGscmPage(trees={"Private": [], "Public": tree, "Custom": []})
    flow_gscm.open_bookmark(page, _run_job(name="Weekly PSI", folder=("SCM", "MENA")))
    assert "row.mena.weekly" in page.clicks
    assert "row.asia.weekly" not in page.clicks


def test_resolve_entry_expands_only_the_catalogued_folder_path(monkeypatch):
    page = FakeGscmPage()
    expanded = set()
    folders = {
        ((), "SCM"): {"name": "SCM", "folder_path": [], "is_folder": True},
        (("SCM",), "Sell-in Biz Plan"): {
            "name": "Sell-in Biz Plan", "folder_path": ["SCM"], "is_folder": True,
        },
    }
    target = {
        "name": "SIBP_CI_Series_ASP_Global",
        "folder_path": ["SCM", "Sell-in Biz Plan"],
        "is_folder": False,
    }

    def find(_page, name, parents, **_kwargs):
        key = (tuple(parents), name)
        if key == (("SCM",), "Sell-in Biz Plan") and "SCM" not in expanded:
            return None
        if key == (("SCM", "Sell-in Biz Plan"), target["name"]):
            return target if "Sell-in Biz Plan" in expanded else None
        return folders.get(key)

    monkeypatch.setattr(flow_gscm, "_find_tree_entry", find)
    monkeypatch.setattr(
        flow_gscm, "_click_entry", lambda _page, entry: expanded.add(entry["name"]),
    )
    monkeypatch.setattr(flow_gscm, "collect_favorite_tree", lambda _page: [])

    assert flow_gscm._resolve_entry(
        page, target["name"], target["folder_path"], "Public",
    ) == target
    assert expanded == {"SCM", "Sell-in Biz Plan"}


def test_resolve_entry_retries_the_exact_path_after_virtual_grid_misses(monkeypatch):
    page = FakeGscmPage(dialog_open=True)
    target = {
        "name": "SIBP_CI_Series_ASP_Global",
        "folder_path": ["SCM", "Sell-in Biz Plan"],
        "is_folder": False,
    }
    attempts = 0
    reselections = []

    def find(_page, name, parents, **_kwargs):
        nonlocal attempts
        if name == target["name"] and parents == target["folder_path"]:
            attempts += 1
            return target if attempts == 3 else None
        return None

    monkeypatch.setattr(flow_gscm, "_find_tree_entry", find)
    monkeypatch.setattr(flow_gscm, "_reveal_tree_path", lambda *_args: False)
    monkeypatch.setattr(
        flow_gscm, "select_scope_tab",
        lambda _page, tab: reselections.append(tab) or True,
    )

    assert flow_gscm._resolve_entry(
        page, target["name"], target["folder_path"], "Public",
    ) == target
    assert reselections == ["Public", "Public"]


def test_a_deleted_bookmark_names_what_is_still_listed():
    page = FakeGscmPage()
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.open_bookmark(page, _run_job(name="Gone report", folder=()))
    message = str(excinfo.value)
    assert "Gone report" in message
    assert "MENA_Actual_sales" in message


def test_excel_export_clicks_the_mdi_toolbar_button():
    page = FakeGscmPage()
    flow_gscm.trigger_excel_export(page, _run_job())
    assert page.clicks[-1] == EXCEL_BUTTON


def test_excel_export_falls_back_to_the_component_name_when_the_path_changed():
    page = FakeGscmPage()
    moved = "mainframe.VFrameSet.MdiFrame.form.div_toolbar.form.btn_exceldown"
    page.components.discard(EXCEL_BUTTON)
    page.components.add(moved)
    flow_gscm.trigger_excel_export(page, _run_job())
    assert page.clicks[-1] == moved


def test_missing_excel_button_reports_the_screen():
    page = FakeGscmPage()
    page.components.discard(EXCEL_BUTTON)
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.trigger_excel_export(page, _run_job(), timeout_ms=1)
    assert "On screen:" in str(excinfo.value)


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
        scan_id = flows._queue_scan(db, site, "manual", "Analyst")
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


def test_each_gscm_scan_replaces_the_previous_unreferenced_snapshot(flow_db):
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
    assert second["reset_report_count"] == 1
    assert len(current) == 1
    assert current[0]["id"] != first_id
    assert (current[0]["enabled"], current[0]["stale"]) == (1, 0)
    assert timing_report_id is None


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
        scan_id = flows._queue_scan(db, site_row, "manual", "Analyst")
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
    page = FakeGscmPage()
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
