from pathlib import Path

import pytest

from app import flow_worker
from app.flow_worker import (
    _asap_frame,
    _asap_goto,
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
