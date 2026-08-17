from pathlib import Path

import pytest

from app import flow_worker
from app.flow_worker import (
    _asap_frame,
    _asap_goto,
    _asap_last_visible,
    _asap_wait_for_visible,
    _asap_wait_for_report_navigation,
    _menu_report_paths,
    _navigation_roots,
    _wait_for_navigation_roots,
)


def _record(text, x, y, *, href="", onclick=""):
    return {
        "link": object(),
        "text": text,
        "href": href,
        "onclick": onclick,
        "box": {"x": x, "y": y, "width": 70, "height": 24},
    }


def test_navigation_roots_are_inferred_from_top_link_row():
    records = [
        _record("ASAP", 10, 20),
        _record("Market", 160, 90),
        _record("Mobile", 235, 90),
        _record("Eco", 300, 90),
        _record("A report", 50, 250, href="/report/1"),
    ]

    assert [item["text"] for item in _navigation_roots(records)] == ["Market", "Mobile", "Eco"]


def test_fota_discovery_includes_visual_category_member_list():
    source = Path(flow_worker.__file__).read_text()

    assert 'add_definition("Category", "multi_select", category_options)' in source
    assert '{"weekly", "daily"}' in source


def test_revealed_menu_columns_become_report_paths_without_target_hardcoding():
    root = _record("Mobile", 235, 90)
    before = [root, _record("Market", 160, 90)]
    after = [
        *before,
        _record("Installed Base", 45, 145),
        _record("Installed Base", 45, 180, href="javascript:open('base')"),
        _record("Installed Base (MENA)", 45, 215, href="javascript:open('mena')"),
        _record("Regional FOTA", 150, 145),
        _record("Regional FOTA", 150, 180, href="javascript:open('fota')"),
    ]

    assert _menu_report_paths(root, before, after) == [
        ["Mobile", "Installed Base", "Installed Base"],
        ["Mobile", "Installed Base", "Installed Base (MENA)"],
        ["Mobile", "Regional FOTA", "Regional FOTA"],
    ]


def test_navigation_waits_for_client_rendered_controls(monkeypatch):
    snapshots = [[], [_record("Market", 160, 90), _record("Mobile", 235, 90)]]
    monkeypatch.setattr("app.flow_worker._visible_anchor_records", lambda _page: snapshots.pop(0))

    class Page:
        def wait_for_timeout(self, _milliseconds):
            pass

    assert [item["text"] for item in _wait_for_navigation_roots(Page(), 1_000)] == ["Market", "Mobile"]


def test_menu_discovery_waits_for_loading_overlay_before_click(monkeypatch):
    events = []

    class Link:
        def click(self, **_kwargs):
            events.append("click")

    root = _record("Mobile", 235, 90)
    root["link"] = Link()
    monkeypatch.setattr(flow_worker, "_wait_for_navigation_roots", lambda _page: [root])
    monkeypatch.setattr(flow_worker, "_visible_anchor_records", lambda _page: [root])
    monkeypatch.setattr(flow_worker, "_navigation_roots", lambda _records: [root])
    monkeypatch.setattr(
        flow_worker,
        "_asap_wait_for_loading_clear",
        lambda _page: events.append("wait"),
    )
    monkeypatch.setattr(
        flow_worker,
        "_menu_report_paths",
        lambda *_args: [["Mobile", "Regional FOTA", "Regional FOTA"]],
    )

    class Page:
        def wait_for_timeout(self, _milliseconds):
            pass

    assert flow_worker._asap_discover_menu_reports(Page(), []) == [
        ["Mobile", "Regional FOTA", "Regional FOTA"],
    ]
    assert events[0:2] == ["wait", "click"]


def test_asap_goto_waits_for_delayed_expired_session_redirect(monkeypatch, tmp_path):
    states = [False, False, True]
    calls = []
    monkeypatch.setattr("app.flow_worker._asap_login_visible", lambda _page: states.pop(0) if states else True)
    monkeypatch.setattr("app.flow_worker._visible_anchor_records", lambda _page: [])
    monkeypatch.setattr(
        "app.flow_worker._asap_authenticate_if_needed",
        lambda _page, profile: calls.append(profile) or True,
    )

    class Page:
        def goto(self, url, **_kwargs):
            calls.append(url)

        def wait_for_timeout(self, _milliseconds):
            pass

    assert _asap_goto(Page(), "https://portal.example/login", tmp_path) is True
    assert calls == ["https://portal.example/login", tmp_path]


def test_asap_frame_uses_newest_live_replacement():
    class Frame:
        def __init__(self, detached):
            self.detached = detached

        def is_detached(self):
            return self.detached

    class Element:
        def __init__(self, frame):
            self.frame = frame

        def content_frame(self):
            return self.frame

    old = Frame(True)
    current = Frame(False)

    class Locator:
        def wait_for(self, **_kwargs):
            pass

        def element_handles(self):
            return [old_element, current_element]

    old_element = Element(old)
    current_element = Element(current)

    class Page:
        def locator(self, _selector):
            return Locator()

    assert _asap_frame(Page()) is current


def test_asap_loading_wait_requires_four_clear_samples(monkeypatch):
    states = iter([True, False, False, False, False])
    waits = []
    monkeypatch.setattr(
        flow_worker, "_asap_loading_overlay_visible", lambda _page: next(states),
    )

    class Page:
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)

    flow_worker._asap_wait_for_loading_clear(Page(), timeout_ms=5_000)

    assert waits == [250, 250, 250, 250]


def test_asap_loading_overlay_detects_live_id_selector():
    selectors = []

    class Overlay:
        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

    class Frame:
        def locator(self, selector):
            selectors.append(selector)
            return Overlay()

    frame = Frame()

    class Page:
        main_frame = frame
        frames = [frame]

    assert flow_worker._asap_loading_overlay_visible(Page()) is True
    assert "#loading-spinner-container" in selectors[0]


def test_asap_results_accept_populated_raw_table_without_data_rows_marker(monkeypatch):
    class EmptyRows:
        @property
        def first(self):
            return self

        def count(self):
            return 0

    class Frame:
        def get_by_text(self, *_args, **_kwargs):
            return EmptyRows()

    frame = Frame()
    monkeypatch.setattr(flow_worker, "_asap_loading_overlay_visible", lambda _page: False)
    monkeypatch.setattr(flow_worker, "_asap_raw_table_ready", lambda candidate: candidate is frame)

    class Page:
        frames = [frame]

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("the populated raw table should be accepted immediately")

    assert flow_worker._asap_wait_for_results(Page(), timeout_ms=1_000) is frame


class _WaitPage:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def _empty_render_error():
    return RuntimeError(
        "ASAP report rows did not render within 600 seconds. "
        + flow_worker.ASAP_EMPTY_RESULT_DETAIL
    )


def test_asap_run_report_retries_once_after_silently_empty_rendering(monkeypatch):
    frame = object()
    attempts = []

    def fake_run(_page):
        attempts.append(1)
        if len(attempts) == 1:
            raise _empty_render_error()
        return frame

    monkeypatch.setattr(flow_worker, "_asap_run_report", fake_run)
    page = _WaitPage()
    retries = []

    assert flow_worker._asap_run_report_with_retry(page, on_retry=retries.append) is frame
    assert len(attempts) == 2
    assert len(retries) == 1
    assert page.waits == [1_500]


def test_asap_run_report_does_not_retry_while_overlay_still_visible(monkeypatch):
    attempts = []

    def fake_run(_page):
        attempts.append(1)
        raise RuntimeError(
            "ASAP report rows did not render within 600 seconds."
            " The ASAP loading overlay was still visible."
        )

    monkeypatch.setattr(flow_worker, "_asap_run_report", fake_run)

    with pytest.raises(RuntimeError, match="still visible"):
        flow_worker._asap_run_report_with_retry(_WaitPage())
    assert len(attempts) == 1


def test_asap_run_report_raises_after_second_empty_rendering(monkeypatch):
    attempts = []

    def fake_run(_page):
        attempts.append(1)
        raise _empty_render_error()

    monkeypatch.setattr(flow_worker, "_asap_run_report", fake_run)

    with pytest.raises(RuntimeError, match="did not render"):
        flow_worker._asap_run_report_with_retry(_WaitPage())
    assert len(attempts) == 2


def test_asap_table_control_score_accepts_only_compact_top_right_controls():
    table = {"x": 100, "y": 200, "width": 800, "height": 500}

    info = {"x": 870, "y": 205, "width": 20, "height": 20}
    left_filter = {"x": 120, "y": 205, "width": 20, "height": 20}
    full_overlay = {"x": 100, "y": 200, "width": 800, "height": 500}

    assert flow_worker._asap_table_control_score(table, info) is not None
    assert flow_worker._asap_table_control_score(table, left_filter) is None
    assert flow_worker._asap_table_control_score(table, full_overlay) is None


def test_asap_run_control_accepts_input_value_rendering():
    class Locator:
        def __init__(self, visible):
            self.visible = visible

        @property
        def first(self):
            return self

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

    input_run = Locator(True)

    class Root:
        def get_by_role(self, _role, **_kwargs):
            return Locator(False)

        def locator(self, selector):
            assert "input[type='button'][value='RUN' i]" in selector
            return input_run

        def get_by_text(self, _text, **_kwargs):
            raise AssertionError("the visible RUN input should be accepted before text fallback")

    assert flow_worker._asap_run_control(Root()) is input_run


def test_asap_export_action_accepts_input_value_rendering():
    class Locator:
        def __init__(self, visible):
            self.visible = visible

        @property
        def first(self):
            return self

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

    input_export = Locator(True)

    class Root:
        def get_by_role(self, _role, **_kwargs):
            return Locator(False)

        def locator(self, selector):
            assert "input[type='button'][value='Export' i]" in selector
            return input_export

        def get_by_text(self, _text, **_kwargs):
            raise AssertionError("the visible Export input should be accepted before text fallback")

    assert flow_worker._asap_export_action(Root()) is input_export


def test_asap_xlsx_format_prefers_flat_excel_export_and_accepts_legacy_label():
    preferred, pattern = flow_worker._asap_export_format_names("xlsx")

    assert preferred == "Excel with plain text"
    assert pattern.fullmatch("Excel with plain text")
    assert pattern.fullmatch("Excel file format")
    assert not pattern.fullmatch("Excel with formatting")


def test_asap_download_retries_one_wizard_recognition_failure(monkeypatch, tmp_path):
    attempts = []

    def download(*_args):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError(
                "ASAP Export Wizard opened, but its XLSX option or Export action "
                "was not recognized. Format option found: False. Export action found: False."
            )
        return tmp_path / "download.xlsx", []

    monkeypatch.setattr(flow_worker, "_asap_download", download)

    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = Page()
    result = flow_worker._asap_download_with_retry(
        page, object(), {}, tmp_path,
    )

    assert result == (tmp_path / "download.xlsx", [])
    assert attempts == [1, 2]
    assert page.waits == [1_500]


def test_asap_download_stops_after_second_wizard_recognition_failure(monkeypatch, tmp_path):
    attempts = []
    error = (
        "ASAP Export Wizard opened, but its XLSX option or Export action was not "
        "recognized. Format option found: False. Export action found: False."
    )

    def download(*_args):
        attempts.append(len(attempts) + 1)
        raise RuntimeError(error)

    monkeypatch.setattr(flow_worker, "_asap_download", download)

    class Page:
        def wait_for_timeout(self, _milliseconds):
            pass

    with pytest.raises(RuntimeError, match="ASAP Export Wizard opened"):
        flow_worker._asap_download_with_retry(Page(), object(), {}, tmp_path)

    assert attempts == [1, 2]


def test_asap_download_does_not_retry_other_failures(monkeypatch, tmp_path):
    attempts = []

    def download(*_args):
        attempts.append(len(attempts) + 1)
        raise RuntimeError("ASAP export started, but no download was emitted.")

    monkeypatch.setattr(flow_worker, "_asap_download", download)

    class Page:
        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("non-retryable failures must not wait")

    with pytest.raises(RuntimeError, match="no download was emitted"):
        flow_worker._asap_download_with_retry(Page(), object(), {}, tmp_path)

    assert attempts == [1]


def test_asap_download_retries_once_with_replaced_frame(monkeypatch, tmp_path):
    attempts = []
    original_frame = object()
    replacement_frame = object()

    def download(_page, frame, _job, _staging_dir):
        attempts.append(frame)
        if len(attempts) == 1:
            raise flow_worker.PlaywrightError("Frame was detached")
        return tmp_path / "download.csv", []

    monkeypatch.setattr(flow_worker, "_asap_download", download)
    monkeypatch.setattr(flow_worker, "_asap_frame", lambda _page: replacement_frame)

    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = Page()
    result = flow_worker._asap_download_with_retry(
        page, original_frame, {}, tmp_path,
    )

    assert result == (tmp_path / "download.csv", [])
    assert attempts == [original_frame, replacement_frame]
    assert page.waits == [1_500]


def test_asap_download_does_not_retry_other_playwright_errors(monkeypatch, tmp_path):
    attempts = []

    def download(*_args):
        attempts.append(len(attempts) + 1)
        raise flow_worker.PlaywrightError("Target page, context or browser has been closed")

    monkeypatch.setattr(flow_worker, "_asap_download", download)

    class Page:
        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("non-frame Playwright errors must not wait")

    with pytest.raises(flow_worker.PlaywrightError, match="browser has been closed"):
        flow_worker._asap_download_with_retry(Page(), object(), {}, tmp_path)

    assert attempts == [1]


def test_raw_table_excel_menu_item_is_a_direct_download_action():
    class Locator:
        def __init__(self, visible):
            self.visible = visible

        @property
        def first(self):
            return self

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

        def filter(self, **_kwargs):
            return self

    class Page:
        frames = []

        def __init__(self):
            self.context = type("Context", (), {"pages": [self]})()

        def get_by_role(self, _role, **_kwargs):
            return Locator(False)

        def get_by_text(self, pattern):
            assert pattern.fullmatch("Excel")
            return Locator(True)

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("the visible Excel menu item should be accepted immediately")

    action = flow_worker._asap_wait_for_raw_menu_download_action(Page(), "xlsx")

    assert action.is_visible()
    assert flow_worker._asap_wait_for_raw_menu_download_action(Page(), "csv") is None


def test_raw_table_export_control_accepts_direct_export_options_popup():
    popup = object()

    class Page:
        frames = []

        def __init__(self):
            self.context = type("Context", (), {"pages": [self, popup]})()

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("the new Export Options popup should be accepted immediately")

    page = Page()
    menu_export, wizard_opened = flow_worker._asap_wait_for_raw_menu_export_or_wizard(
        page, {page},
    )

    assert menu_export is None
    assert wizard_opened is True


def test_raw_table_excel_dialog_exposes_final_export_action():
    class Locator:
        def __init__(self, visible):
            self.visible = visible

        @property
        def first(self):
            return self

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

        def filter(self, **_kwargs):
            return self

    class Page:
        frames = []

        def __init__(self):
            self.context = type("Context", (), {"pages": [self]})()

        def get_by_text(self, pattern):
            assert pattern.fullmatch("Export to Excel")
            return Locator(True)

        def get_by_role(self, role, **kwargs):
            assert role == "button"
            assert kwargs == {"name": "Export", "exact": True}
            return Locator(True)

        def locator(self, _selector):
            return Locator(False)

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("the visible Export button should be accepted immediately")

    action = flow_worker._asap_wait_for_raw_export_confirmation(Page(), "xlsx")

    assert action.is_visible()
    assert flow_worker._asap_wait_for_raw_export_confirmation(Page(), "csv") is None


def test_export_view_waits_for_loading_overlay_before_and_after_click(monkeypatch):
    waits = []

    class Control:
        def click(self, **kwargs):
            assert kwargs == {"timeout": 30_000}

    class VisibleMarker:
        def is_visible(self):
            return True

    class Markers:
        def all(self):
            return [VisibleMarker()]

    class ActiveFrame:
        def get_by_text(self, *_args, **_kwargs):
            return Markers()

    active_frame = ActiveFrame()
    frame_values = iter(["fresh-before-click", active_frame])
    monkeypatch.setattr(
        flow_worker, "_asap_wait_for_loading_clear",
        lambda _page: waits.append("clear"),
    )
    monkeypatch.setattr(flow_worker, "_asap_frame", lambda _page: next(frame_values))

    candidate_frames = []
    monkeypatch.setattr(
        flow_worker, "_asap_export_view_candidates",
        lambda _page, frame: candidate_frames.append(frame) or [("Target view", Control())],
    )

    class Page:
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)

    returned_frame, selected = flow_worker._asap_activate_export_view(
        Page(), object(), "Target view",
    )

    assert candidate_frames == ["fresh-before-click"]
    assert waits[:3] == ["clear", 500, "clear"]
    assert returned_frame is active_frame
    assert selected == "Target view"


def test_asap_navigation_waits_for_replacement_and_requested_breadcrumb(monkeypatch):
    class Frame:
        def is_detached(self):
            return False

    previous = Frame()
    current = Frame()
    monkeypatch.setattr(flow_worker, "_asap_frame", lambda _page: current)

    class Item:
        def is_visible(self):
            return True

    class Locator:
        def all(self):
            return [Item()]

    class Target:
        def __init__(self):
            self.waited = None

        def wait_for(self, **kwargs):
            self.waited = kwargs

    class Page:
        def get_by_text(self, _label, exact=True):
            assert exact is True
            return Locator()

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("replacement and breadcrumb should be accepted immediately")

    target = Target()
    result = _asap_wait_for_report_navigation(
        Page(), previous, target,
        ["Market", "TechInsights Smartphone M/S", "All Products Demand"],
        timeout_ms=1_000,
    )

    assert result is current
    assert target.waited == {"state": "hidden", "timeout": 1_000}


def test_asap_navigation_accepts_stable_requested_breadcrumb_in_reused_frame(monkeypatch):
    class Frame:
        def is_detached(self):
            return False

    current = Frame()
    monkeypatch.setattr(flow_worker, "_asap_frame", lambda _page: current)
    monkeypatch.setattr(flow_worker, "_asap_frame_signature", lambda _frame: "same-body")
    monkeypatch.setattr(
        flow_worker, "_asap_text_visible_across_frames", lambda _page, _label: True,
    )

    class Target:
        def wait_for(self, **_kwargs):
            pass

    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = Page()
    result = _asap_wait_for_report_navigation(
        page, current, Target(), ["Market", "TechInsights Smartphone M/S"],
        previous_signature="same-body", timeout_ms=1_000,
    )

    assert result is current
    assert page.waits == [250, 250]


def test_asap_menu_wait_allows_delayed_hover_items(monkeypatch):
    expected = object()
    states = iter([None, None, expected])
    monkeypatch.setattr(flow_worker, "_asap_first_visible", lambda _locator: next(states))

    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = Page()
    assert _asap_wait_for_visible(page, object(), timeout_ms=1_000) is expected
    assert page.waits == [100, 100]


def test_asap_last_visible_chooses_leaf_when_group_reuses_label():
    class Item:
        def __init__(self, name, visible=True):
            self.name = name
            self.visible = visible

        def is_visible(self):
            return self.visible

    group = Item("group")
    hidden = Item("hidden", visible=False)
    leaf = Item("leaf")

    class Locator:
        def all(self):
            return [group, hidden, leaf]

    assert _asap_last_visible(Locator()) is leaf


def test_asap_menu_wait_can_prefer_last_duplicate_label(monkeypatch):
    expected = object()
    monkeypatch.setattr(flow_worker, "_asap_last_visible", lambda _locator: expected)

    class Page:
        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("visible duplicate leaf should be found immediately")

    assert _asap_wait_for_visible(
        Page(), object(), timeout_ms=1_000, prefer_last=True,
    ) is expected


def test_asap_navigation_accepts_stable_changed_body_when_iframe_is_reused(monkeypatch):
    class Frame:
        def is_detached(self):
            return False

    current = Frame()
    monkeypatch.setattr(flow_worker, "_asap_frame", lambda _page: current)
    monkeypatch.setattr(flow_worker, "_asap_frame_signature", lambda _frame: "new-body")

    class Item:
        def is_visible(self):
            return False

    class Locator:
        def all(self):
            return [Item()]

    class Target:
        def wait_for(self, **_kwargs):
            pass

    class Page:
        def __init__(self):
            self.waits = []

        def get_by_text(self, _label, exact=True):
            assert exact is True
            return Locator()

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = Page()
    result = _asap_wait_for_report_navigation(
        page, current, Target(),
        ["Market", "TechInsights Smartphone M/S", "All Products Demand"],
        previous_signature="old-body", timeout_ms=1_000,
    )

    assert result is current
    assert page.waits == [250, 250]


def test_targeted_scan_surfaces_path_error_when_no_report_is_discovered(monkeypatch, tmp_path):
    monkeypatch.setattr(flow_worker, "_asap_goto", lambda *_args: False)
    monkeypatch.setattr(
        flow_worker, "_asap_open_report",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("breadcrumb proof missing")),
    )
    job = {
        "site": {"base_url": "https://portal.example", "auth_url": None},
        "discovery": {
            "max_duration_minutes": 1,
            "scope": ["*"],
            "report_paths": [["Market", "Group", "Target Report"]],
        },
    }

    with pytest.raises(RuntimeError, match="Target Report: breadcrumb proof missing"):
        flow_worker.discover_asap_catalog(
            object(), job, lambda *_args: None, tmp_path,
        )


def test_execute_job_downloads_every_export_view_before_returning(monkeypatch, tmp_path):
    activated = []
    staged = []

    monkeypatch.setattr(flow_worker, "_asap_open_report", lambda *_args: object())
    monkeypatch.setattr(
        flow_worker, "_asap_activate_export_view",
        lambda _page, frame, label: (activated.append(label) or frame, label),
    )
    monkeypatch.setattr(flow_worker, "_asap_apply_configuration", lambda *_args: None)
    monkeypatch.setattr(flow_worker, "_has_named_control", lambda *_args: False)

    def fake_download(_page, _frame, _job, staging_dir):
        path = Path(staging_dir) / f"source-{len(staged) + 1}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value,units\nitem,1\n", encoding="utf-8")
        staged.append(path)
        return path, []

    monkeypatch.setattr(flow_worker, "_asap_download", fake_download)
    progress = []
    job = {
        "flow": {"id": 1, "name": "ASAP TI"},
        "site": {"adapter": "asap_portal"},
        "report": {
            "id": 1, "name": "TechInsights Smartphone M/S", "filters": [],
            "automation": {"export_views": [
                {"label": "Export Wizard (Global/Region)", "filter_keys": []},
                {"label": "Export Wizard (Selected Countries)", "filter_keys": []},
            ]},
            "export_views": [
                "Export Wizard (Global/Region)", "Export Wizard (Selected Countries)",
            ],
        },
        "selections": {},
        "downloads": {
            "periods": [None], "target_folder": str(tmp_path), "file_format": "csv",
            "filename_template": "{flow}_{export}.csv",
        },
    }

    artifacts, _timings = flow_worker.execute_job(
        object(), job, lambda *args: progress.append(args), tmp_path,
        tmp_path / "staging",
    )

    assert activated == job["report"]["export_views"]
    assert len(artifacts) == 2
    assert [item["export_view"] for item in artifacts] == activated
    assert [item["bundle_index"] for item in artifacts] == [1, 2]
    for artifact, label in zip(artifacts, activated):
        assert label in Path(artifact["file_path"]).read_text(encoding="utf-8-sig")
