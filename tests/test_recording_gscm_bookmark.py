import pytest

from app import flow_recording
from app import flow_recording_gscm_bookmark as bookmarks


def step(**target):
    return {"id": "bookmark", "action": "click", "page": "page", "locator": [],
            "bookmark_target": {"kind": "gscm_favorite", "bookmark_name": "Saved report", **target}}


def test_bookmark_target_requires_exact_name_and_rejects_unqualified_native_strategy():
    value = {"version": 2, "timezone": "UTC", "adapter": "gscm_portal", "parameters": {},
             "steps": [step(), {"id": "download", "action": "download", "page": "page", "locator": [],
                         "output": {"format": "csv"}, "steps": [{"id": "trigger", "action": "click", "page": "page", "locator": []}]}]}
    assert flow_recording.validate_definition(value)["steps"][0]["bookmark_target"]["bookmark_name"] == "Saved report"
    value["steps"][0]["bookmark_target"]["bookmark_name"] = " "
    with pytest.raises(ValueError, match="exact bookmark name"):
        flow_recording.validate_definition(value)
    value["steps"][0]["bookmark_target"] = {"kind": "gscm_favorite", "bookmark_name": "Saved report", "native_strategy": "old"}
    with pytest.raises(ValueError, match="not qualified"):
        flow_recording.validate_definition(value)


def test_visible_bookmark_is_clicked_without_scrolling(monkeypatch):
    clicked = []
    row = {"id": "fresh-row", "text": "Saved report"}
    monkeypatch.setattr(bookmarks.flow_gscm, "bookmark_dataset_entries", lambda page: [
        {"identity_name": "Saved report", "bookmark_id": "bookmark-1", "tab": "Public"}])
    monkeypatch.setattr(bookmarks.flow_gscm, "favorite_tree_rows", lambda page: [(object(), row)])
    monkeypatch.setattr(bookmarks, "_click_rendered", lambda root, selected: clicked.append((root, selected)))
    result = bookmarks.select(object(), step(scope="Public"))
    assert result["strategy"] == "favorite-visible-row"
    assert result["movement"] is False
    assert clicked and clicked[0][1] is row


def test_offscreen_bookmark_reacquires_after_scroll(monkeypatch):
    calls = {"rows": 0, "scroll": 0}
    monkeypatch.setattr(bookmarks.flow_gscm, "bookmark_dataset_entries", lambda page: [
        {"identity_name": "Saved report", "bookmark_id": "bookmark-1", "tab": "Public"}])
    def rows(page):
        calls["rows"] += 1
        return [] if calls["rows"] == 1 else [(object(), {"id": "recycled-now-fresh", "text": "Saved report"})]
    monkeypatch.setattr(bookmarks.flow_gscm, "favorite_tree_rows", rows)
    monkeypatch.setattr(bookmarks, "_scroll_once", lambda page: calls.__setitem__("scroll", calls["scroll"] + 1) or True)
    monkeypatch.setattr(bookmarks, "_click_rendered", lambda root, row: None)
    result = bookmarks.select(object(), step(scope="Public"))
    assert result["strategy"] == "favorite-scrollbar"
    assert calls["scroll"] == 1


def test_duplicate_name_requires_stable_id_and_stalled_grid_is_actionable(monkeypatch):
    entries = [{"identity_name": "Saved report", "bookmark_id": "one", "tab": "Public"},
               {"identity_name": "Saved report", "bookmark_id": "two", "tab": "Public"}]
    monkeypatch.setattr(bookmarks.flow_gscm, "bookmark_dataset_entries", lambda page: entries)
    with pytest.raises(RuntimeError, match="ambiguous"):
        bookmarks.select(object(), step(scope="Public"))
    monkeypatch.setattr(bookmarks.flow_gscm, "favorite_tree_rows", lambda page: [])
    monkeypatch.setattr(bookmarks, "_scroll_once", lambda page: False)
    with pytest.raises(RuntimeError, match="ancestor may be collapsed"):
        bookmarks.select(object(), step(scope="Public", bookmark_id="two"))
