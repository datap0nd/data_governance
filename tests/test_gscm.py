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
                 always_busy=False, busy_polls=0, popup_ids=()):
        self.trees = trees if trees is not None else {
            "Private": PRIVATE_TREE, "Public": PUBLIC_TREE, "Custom": CUSTOM_TREE,
        }
        self.dialog_open = dialog_open
        self.gear = gear
        self.url = url
        self.always_busy = always_busy
        self.busy_polls = busy_polls
        self.popup_ids = list(popup_ids)
        self.tab = "Public"
        self.clicks = []
        self.navigations = []
        self.wait_window_hidden = 0
        self.components = {"mainframe.VFrameSet", EXCEL_BUTTON}
        if gear:
            self.components.add(GEAR_ID)

    # -- state the adapter drives --

    def on_click(self, element_id):
        self.clicks.append(element_id)
        record = next(
            (item for item in self._screen() if item["id"] == element_id), None,
        )
        text = (record or {}).get("text", "")
        if element_id == GEAR_ID or text == "Setting":
            self.dialog_open = True
        elif text in self.trees:
            self.tab = text
        elif text == "Close":
            self.dialog_open = False

    def _screen(self):
        if not self.dialog_open:
            items = []
            if self.gear:
                items.append({"id": GEAR_ID, "text": "\u2699", "x": 1700, "y": 300, "w": 20, "h": 20})
            items.append(_label("Favorite", 1480, 447))  # the empty home widget
            return items
        return [*DIALOG_CHROME, *self.trees.get(self.tab, [])]

    # -- Playwright surface --

    @property
    def frames(self):
        return []

    def goto(self, url, **_kwargs):
        self.navigations.append(url)
        self.url = url

    def wait_for_timeout(self, _ms):
        return None

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
    assert "On screen:" in message


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
        flow_gscm.trigger_excel_export(page, _run_job())
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
