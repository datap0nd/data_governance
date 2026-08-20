"""GSCM portal adapter: bookmark discovery, catalog wiring, and flow runs.

GSCM is a separate website from ASAP with its own client framework and its own
data. These tests pin the parts of that separation that are easy to erode:
bookmarks are read from the Nexacro home screen rather than a menu tree, a GSCM
report carries no Metronome filters, and a GSCM flow downloads once with no
period selection.
"""

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


# ── Fake Nexacro page ──


class FakeLocator:
    def __init__(self, page, element_id, matches):
        self.page = page
        self.element_id = element_id
        self.matches = matches

    @property
    def first(self):
        return self

    def count(self):
        return len(self.matches)

    def click(self, **kwargs):
        if not self.matches:
            raise RuntimeError(f"no element for {self.element_id}")
        self.page.clicks.append((self.matches[0], kwargs))

    def dblclick(self, **kwargs):
        if not self.matches:
            raise RuntimeError(f"no element for {self.element_id}")
        self.page.double_clicks.append((self.matches[0], kwargs))


class FakeNexacroPage:
    """Enough of a Playwright page to drive the GSCM adapter without a browser."""

    def __init__(self, *, element_ids, favorites, url="https://mdscm.sec.samsung.net/nexa/index.html",
                 busy_polls=0, always_busy=False, popup_ids=()):
        self.element_ids = set(element_ids)
        self.favorites = list(favorites)
        self.url = url
        self.busy_polls = busy_polls
        self.always_busy = always_busy
        self.popup_ids = list(popup_ids)
        self.clicks = []
        self.double_clicks = []
        self.navigations = []
        self.waited_ms = 0
        self.wait_window_hidden = 0

    def goto(self, url, **_kwargs):
        self.navigations.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def locator(self, selector):
        wanted = selector[len("[id='"):-len("']")] if selector.startswith("[id='") else None
        if wanted is not None:
            wanted = wanted.replace("\\'", "'").replace("\\\\", "\\")
            matches = [wanted] if wanted in self.element_ids else []
        else:
            fragment = selector[len("[id*='"):-len("']")]
            matches = [item for item in sorted(self.element_ids) if fragment in item]
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
        if "byCard" in script:
            return [
                {
                    "card_id": item["card_id"],
                    "label_id": f"{item['card_id']}.form.stc_userreportname",
                    "name": item["name"],
                }
                for item in self.favorites
            ]
        if "lowered.includes('popup')" in script:
            return list(self.popup_ids)
        if "getElementById(id)" in script:
            return argument in self.element_ids
        raise AssertionError(f"unexpected evaluate: {script[:60]}")


EXCEL_BUTTON = flow_gscm.FALLBACK_EXCEL_BUTTON_ID
CARD_ONE = (
    "mainframe.VFrameSet.HomeFrame.form.div_main.form.div_section4_MOBILE.form"
    ".div_favorite.form.div_favorite.form.div_list.form.div_data01"
)
CARD_TWO = CARD_ONE[:-2] + "02"


def _page(**overrides):
    favorites = overrides.pop("favorites", [
        {"card_id": CARD_ONE, "name": "Biz_trip_GSCM"},
        {"card_id": CARD_TWO, "name": "Sell-in weekly"},
    ])
    element_ids = overrides.pop("element_ids", {
        "mainframe.VFrameSet", EXCEL_BUTTON, CARD_ONE, CARD_TWO,
    })
    return FakeNexacroPage(element_ids=element_ids, favorites=favorites, **overrides)


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


# ── Discovery ──


def test_bookmarks_are_read_from_the_home_screen_not_a_menu_tree():
    page = _page()
    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])

    assert complete is True
    assert [item["name"] for item in reports] == ["Biz_trip_GSCM", "Sell-in weekly"]
    assert [item["discovery_key"] for item in reports] == [
        "Favorites > Biz_trip_GSCM", "Favorites > Sell-in weekly",
    ]
    first = reports[0]
    assert first["automation"]["favorite_id"] == CARD_ONE
    assert first["automation"]["category_path"] == ["Favorites", "Biz_trip_GSCM"]
    assert first["automation"]["kind"] == "gscm_favorite"
    # GSCM owns the filters. A discovered bookmark must not invent Metronome
    # prompts, or the flow builder would ask the user to configure them twice.
    assert first["filters"] == []


def test_discovery_clears_the_wait_overlay_before_reading_the_screen():
    page = _page()
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert page.wait_window_hidden >= 1


def test_discovery_closes_blocking_popup_cards():
    closer = "mainframe.VFrameSet.HomeFrame.form.FORM_NOTICE.Popup.form.closebutton"
    page = _page(popup_ids=[closer])
    page.element_ids.add(closer)
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert closer in [item[0] for item in page.clicks]


def test_discovery_reuses_an_open_portal_tab_instead_of_reloading_the_session():
    page = _page()
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert page.navigations == []


def test_discovery_navigates_when_the_tab_is_somewhere_else():
    page = _page(url="https://intranet.example.test/home")
    flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert page.navigations == ["https://mdscm.sec.samsung.net/nexa/index.html"]


def test_empty_favorites_list_is_an_actionable_error_not_an_empty_catalog():
    page = _page(favorites=[])
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
    assert "No GSCM bookmarks" in str(excinfo.value)


def test_targeted_scan_narrows_to_one_bookmark_and_reports_itself_incomplete():
    page = _page()
    job = _scan_job()
    job["discovery"]["report_paths"] = [["Favorites", "Biz_trip_GSCM"]]
    reports, complete = flow_gscm.discover_catalog(page, job, _collect_progress()[1])

    assert [item["name"] for item in reports] == ["Biz_trip_GSCM"]
    # An incomplete sweep must not let the server stale every other bookmark.
    assert complete is False


# ── Download ──


def _run_job():
    return {
        "site": _scan_job()["site"],
        "report": {
            "id": 7, "name": "Favorites > Biz_trip_GSCM",
            "url": "https://mdscm.sec.samsung.net/nexa/index.html",
            "automation": {
                "kind": "gscm_favorite",
                "category_path": ["Favorites", "Biz_trip_GSCM"],
                "favorite_id": CARD_ONE,
                "favorite_name": "Biz_trip_GSCM",
                "excel_btn_id": EXCEL_BUTTON,
            },
        },
    }


def test_opening_a_bookmark_double_clicks_its_card_and_waits_for_the_query():
    page = _page(busy_polls=3)
    opened = flow_gscm.open_bookmark(page, _run_job())

    assert opened == CARD_ONE
    assert page.double_clicks[0][0] == CARD_ONE
    # Nexacro re-parents components under its own layout engine, so a
    # structural visibility check would reject a perfectly clickable card.
    assert page.double_clicks[0][1]["force"] is True


def test_a_reordered_favorites_list_still_opens_the_bookmark_the_user_chose():
    # GSCM renumbers div_dataNN when favorites are reordered, so the stored id
    # can point at a different report. The name is the durable identity.
    page = _page(favorites=[
        {"card_id": CARD_ONE, "name": "Sell-in weekly"},
        {"card_id": CARD_TWO, "name": "Biz_trip_GSCM"},
    ])
    assert flow_gscm.open_bookmark(page, _run_job()) == CARD_TWO


def test_a_deleted_bookmark_names_what_is_still_available():
    page = _page(favorites=[{"card_id": CARD_TWO, "name": "Sell-in weekly"}])
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.open_bookmark(page, _run_job())
    assert "Sell-in weekly" in str(excinfo.value)


def test_excel_export_clicks_the_mdi_toolbar_button():
    page = _page()
    flow_gscm.trigger_excel_export(page, _run_job())
    assert page.clicks[-1][0] == EXCEL_BUTTON


def test_excel_export_falls_back_to_the_component_name_when_the_path_changed():
    moved = "mainframe.VFrameSet.MdiFrame.form.div_toolbar.form.btn_exceldown"
    page = _page(element_ids={"mainframe.VFrameSet", CARD_ONE, CARD_TWO, moved})
    flow_gscm.trigger_excel_export(page, _run_job())
    assert page.clicks[-1][0] == moved


def test_missing_excel_button_is_reported_rather_than_silently_skipped():
    page = _page(element_ids={"mainframe.VFrameSet", CARD_ONE, CARD_TWO})
    with pytest.raises(RuntimeError) as excinfo:
        flow_gscm.trigger_excel_export(page, _run_job())
    assert "Excel export button" in str(excinfo.value)


def test_a_stuck_wait_overlay_is_forced_down_instead_of_hanging_the_run():
    # The documented failure mode: the overlay outlives the query it announced.
    page = _page(always_busy=True)
    settled = flow_gscm.wait_for_calculation(page, timeout_ms=2_000)
    assert settled is False
    assert page.wait_window_hidden >= 1


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
    # A cheap names-only pass is an ASAP concept; GSCM's sweep is one screen.
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


def _discover_into_catalog(site_id, page=None):
    """Push the worker's discovery payload through the real scan endpoint."""
    page = page or _page()
    reports, complete = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])
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


def test_discovered_bookmarks_become_catalog_reports(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])

    catalog = flows.catalog()
    names = sorted(
        report["name"] for report in catalog["reports"] if report["site_id"] == site["id"]
    )
    assert names == ["Favorites > Biz_trip_GSCM", "Favorites > Sell-in weekly"]
    bookmark = next(
        report for report in catalog["reports"] if report["name"].endswith("Biz_trip_GSCM")
    )
    assert bookmark["automation"]["favorite_id"] == CARD_ONE
    assert bookmark["filters"] == []


def test_a_gscm_flow_downloads_one_file_with_no_period_selection(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    catalog = flows.catalog()
    bookmark = next(
        report for report in catalog["reports"] if report["name"].endswith("Biz_trip_GSCM")
    )

    saved = flows.create_flow(flows.FlowWrite(
        name="GSCM biz trip",
        site_id=site["id"],
        report_id=bookmark["id"],
        period_strategy="none",
        file_format="xlsx",
        target_folder="C:\\Reports",
        filename_template="{flow}_{date}.xlsx",
    ), _request())
    queued = flows.queue_run(saved["id"], _request())

    job = queued["job"]
    assert job["site"]["adapter"] == "gscm_portal"
    assert job["downloads"]["periods"] == [None]
    assert job["report"]["automation"]["favorite_id"] == CARD_ONE
    assert job["report"]["filters"] == []


def test_a_gscm_flow_cannot_ask_for_a_week_range_gscm_does_not_expose(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    _discover_into_catalog(site["id"])
    catalog = flows.catalog()
    bookmark = next(
        report for report in catalog["reports"] if report["name"].endswith("Biz_trip_GSCM")
    )
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
    catalog = flows.catalog()
    bookmark = next(
        report for report in catalog["reports"] if report["name"].endswith("Biz_trip_GSCM")
    )
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


def test_two_bookmarks_with_the_same_label_both_reach_the_catalog():
    # flow_reports is keyed by (site, name), so identical labels would collapse
    # into one row and quietly drop a bookmark the user saved.
    page = _page(favorites=[
        {"card_id": CARD_ONE, "name": "Weekly"},
        {"card_id": CARD_TWO, "name": "Weekly"},
    ])
    reports, _ = flow_gscm.discover_catalog(page, _scan_job(), _collect_progress()[1])

    assert [item["name"] for item in reports] == ["Weekly", "Weekly (2)"]
    assert len({item["discovery_key"] for item in reports}) == 2
    # The raw GSCM label is preserved so the run-time lookup still matches.
    assert [item["automation"]["favorite_name"] for item in reports] == ["Weekly", "Weekly"]


def test_an_ambiguous_label_falls_back_to_the_catalogued_card_id():
    page = _page(favorites=[
        {"card_id": CARD_ONE, "name": "Weekly"},
        {"card_id": CARD_TWO, "name": "Weekly"},
    ])
    job = _run_job()
    job["report"]["automation"]["favorite_name"] = "Weekly"
    job["report"]["automation"]["favorite_id"] = CARD_TWO
    assert flow_gscm.open_bookmark(page, job) == CARD_TWO
