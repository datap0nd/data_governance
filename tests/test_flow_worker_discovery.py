from app.flow_worker import _menu_report_paths, _navigation_roots, _wait_for_navigation_roots


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
