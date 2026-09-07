import pytest

from app import flow_recording
from app import flow_recording_diagnostics as diagnostics
from app import flow_recording_gscm_bookmark as bookmarks
from app.flow_recording_nexacro import adapt_recording

VIEW = 9


def step(**target):
    return {"id": "bookmark", "action": "click", "page": "page", "locator": [],
            "bookmark_target": {"kind": "gscm_favorite", "bookmark_name": "Saved report", **target}}


def entry(name, bookmark_id, folder=("SCM", "Actual Sales"), tab="Public"):
    return {"identity_name": name, "bookmark_id": bookmark_id, "tab": tab, "folder_path": list(folder)}


class FakeGrid:
    """A virtualized, recycling Favorite grid driven only by scrollbar buttons."""

    def __init__(self, rows, *, start=0, stalled=False, render_delay=0, scrollbar=True, visible_ancestors=None):
        self.rows = rows  # (text, folder_path or None for folders)
        self.visible_ancestors = visible_ancestors  # how many ancestor folders the rendered tree can still see
        self.start = start
        self.stalled = stalled
        self.render_delay = render_delay
        self.scrollbar = scrollbar
        self.pending = None
        self.reads = 0
        self.presses = []
        self.clicked = []

    def _settle(self):
        if self.pending is not None:
            self.pending -= 1
            if self.pending <= 0:
                self.start, self.pending = self.next_start, None

    def _slot(self, index):
        return "grd_bookmark.body.gridrow_" + str(index - self.start)

    def rendered(self):
        self._settle()
        return [(self, {"id": self._slot(i), "text": self.rows[i][0], "x": 20 * len(self.rows[i][1] or []) + (0 if self.rows[i][1] is not None else 0), "y": 30 * (i - self.start), "w": 100, "h": 30, "is_folder": self.rows[i][1] is None})
                for i in range(self.start, min(len(self.rows), self.start + VIEW))]

    def tree(self):
        def visible(path):
            return path[-self.visible_ancestors:] if self.visible_ancestors else path
        return [{"name": row["text"], "folder_path": visible(self.rows[self.start + index][1] or []), "element_id": row["id"]}
                for index, (_root, row) in enumerate(self.rendered()) if self.rows[self.start + index][1] is not None]

    def signature(self):
        return tuple((row["id"], row["text"], row["y"]) for _root, row in self.rendered())

    def press(self, direction):
        self.presses.append(direction)
        if self.stalled:
            return
        limit = max(0, len(self.rows) - VIEW)
        target = min(limit, max(0, (self.next_start if self.pending is not None else self.start) + direction))
        if self.render_delay:
            self.next_start, self.pending = target, self.render_delay
        else:
            self.start = target

    def button(self, part):
        if not self.scrollbar:
            return None
        grid = self

        class Button:
            def click(self, timeout=None):
                grid.press(1 if part == "incbutton" else -1)
        return Button()


class FakePage:
    def wait_for_timeout(self, ms):
        pass


def wire(monkeypatch, grid, entries):
    monkeypatch.setattr(bookmarks, "RENDER_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(bookmarks, "RENDER_POLL_MS", 1)
    monkeypatch.setattr(bookmarks.flow_gscm, "bookmark_dataset_entries", lambda page: entries)
    monkeypatch.setattr(bookmarks.flow_gscm, "favorite_tree_rows", lambda page: grid.rendered())
    monkeypatch.setattr(bookmarks.flow_gscm, "read_favorite_tree", lambda page: grid.tree())
    monkeypatch.setattr(bookmarks.flow_gscm, "_tree_row_signature", lambda page: grid.signature())
    monkeypatch.setattr(bookmarks, "_scrollbar_button", lambda page, part: grid.button(part))
    monkeypatch.setattr(bookmarks, "_click_rendered", lambda root, row: grid.clicked.append(dict(row)))


def long_list(count=40, target_at=25, name="Saved report"):
    rows = [("Actual Sales", None)]
    for index in range(count):
        rows.append((name if index == target_at else "Report " + str(index), ["SCM", "Actual Sales"]))
    return rows


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


def test_repaired_recycled_row_activates_with_bookmark_target():
    recycled = [{"method": "locator", "args": ["#mainframe\\.Setting0\\.form\\.div_favorite\\.form\\.grd_bookmark\\.body\\.gridrow_7"], "kwargs": {}}]
    value = {"version": 2, "timezone": "UTC", "adapter": "gscm_portal", "parameters": {},
             "steps": [{**step(), "locator": recycled}, {"id": "download", "action": "download", "page": "page", "locator": [],
                        "output": {"format": "csv"}, "steps": [{"id": "trigger", "action": "click", "page": "page", "locator": []}]}]}
    assert flow_recording.validate_definition(value)["steps"][0]["bookmark_target"]["bookmark_name"] == "Saved report"
    del value["steps"][0]["bookmark_target"]
    with pytest.raises(ValueError, match="recycled|virtual grid"):
        flow_recording.validate_definition(value)


def test_import_suggests_favorite_target_and_reuses_recorded_text():
    grid = "#mainframe\\.Setting0\\.form\\.div_favorite\\.form\\.grd_bookmark"
    definition = {"steps": [
        {"id": "named", "action": "click", "page": "page", "locator": [
            {"method": "locator", "args": [grid], "kwargs": {}},
            {"method": "get_by_text", "args": [" Saved report "], "kwargs": {"exact": True}}]},
        {"id": "recycled", "action": "click", "page": "page", "locator": [
            {"method": "locator", "args": [grid + "\\.body\\.gridrow_7"], "kwargs": {}}]},
        {"id": "other", "action": "click", "page": "page", "locator": [
            {"method": "locator", "args": ["#mainframe\\.WorkFrame0\\.form\\.grd\\.body\\.gridrow_3"], "kwargs": {}}]}]}
    adapted = {item["id"]: item for item in adapt_recording(definition)["steps"]}
    assert adapted["named"]["bookmark_target"] == {"kind": "gscm_favorite", "bookmark_name": "Saved report"}
    assert "repair_reason" not in adapted["named"]
    assert "bookmark_target" not in adapted["recycled"]
    assert "Bookmark in GSCM Favorite list" in adapted["recycled"]["repair_reason"]
    assert "bookmark_target" not in adapted["other"] and "Favorite" not in adapted["other"]["repair_reason"]


def test_visible_bookmark_is_clicked_without_scrolling(monkeypatch):
    grid = FakeGrid(long_list(target_at=3))
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    result = bookmarks.select(FakePage(), step(scope="Public"))
    assert result["strategy"] == "favorite-visible-row" and result["movement"] is False
    assert grid.presses == [] and grid.clicked[0]["text"] == "Saved report"
    assert result["bookmark_id"] == "bookmark-1" and result["scope"] == "Public"


def test_offscreen_bookmark_below_is_reached_from_the_confirmed_top(monkeypatch):
    grid = FakeGrid(long_list(target_at=25), start=4)
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    phases = []
    result = bookmarks.select(FakePage(), step(), progress=lambda detail: phases.append(detail["phase"]))
    assert result["strategy"] == "favorite-scrollbar" and result["movement"] and result["top_established"]
    assert grid.presses[:bookmarks.PAGE_STEPS] == [-1] * bookmarks.PAGE_STEPS  # walked to the top first
    assert grid.clicked[0]["text"] == "Saved report" and result["rendered"]
    assert "gscm_bookmark_to_top" in phases and "gscm_bookmark_sweep" in phases and phases[-1] == "gscm_bookmark_selected"


def test_offscreen_bookmark_above_current_view_is_found(monkeypatch):
    grid = FakeGrid(long_list(target_at=2), start=20)
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    result = bookmarks.select(FakePage(), step())
    assert result["strategy"] == "favorite-scrollbar" and grid.clicked[0]["text"] == "Saved report"
    assert 1 not in grid.presses  # never swept downward past the target


def test_delayed_rendering_is_awaited_and_rows_reacquired(monkeypatch):
    grid = FakeGrid(long_list(target_at=25), render_delay=3)
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    result = bookmarks.select(FakePage(), step())
    assert result["movement"] and grid.clicked[0]["text"] == "Saved report"
    assert grid.clicked[0]["id"].startswith("grd_bookmark.body.gridrow_")


def test_changed_ordering_still_resolves_by_identity(monkeypatch):
    rows = long_list(target_at=30)
    rows.insert(1, ("New first report", ["SCM", "Actual Sales"]))
    grid = FakeGrid(rows)
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    assert bookmarks.select(FakePage(), step())["rendered"] and grid.clicked[0]["text"] == "Saved report"


def test_collapsed_ancestor_reports_confirmed_end(monkeypatch):
    grid = FakeGrid(long_list(target_at=50))  # identity exists, row never renders
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    with pytest.raises(bookmarks.BookmarkSelectionError, match="top and the end.*ancestor folder may be collapsed") as info:
        bookmarks.select(FakePage(), step())
    assert info.value.diagnostic["bookmark"]["reason"] == "end_confirmed"
    assert info.value.diagnostic["bookmark"]["top_established"] and info.value.diagnostic["bookmark"]["movements"] > 0
    assert grid.clicked == []


def test_stalled_scrollbar_is_reported_distinctly(monkeypatch):
    grid = FakeGrid(long_list(target_at=25), stalled=True)
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    with pytest.raises(bookmarks.BookmarkSelectionError, match="scrollbar did not move") as info:
        bookmarks.select(FakePage(), step())
    assert info.value.diagnostic["bookmark"]["reason"] == "scrollbar_stalled"
    assert len(grid.presses) == bookmarks.PAGE_STEPS * (1 + bookmarks.END_CONFIRMATIONS)


def test_missing_scrollbar_is_actionable(monkeypatch):
    grid = FakeGrid(long_list(target_at=25), scrollbar=False)
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    with pytest.raises(bookmarks.BookmarkSelectionError, match="has no scrollbar") as info:
        bookmarks.select(FakePage(), step())
    assert info.value.diagnostic["bookmark"]["reason"] == "no_scrollbar"


def test_wrong_frame_without_dataset_stops_before_any_search(monkeypatch):
    grid = FakeGrid(long_list())
    wire(monkeypatch, grid, None)
    with pytest.raises(bookmarks.BookmarkSelectionError, match="unavailable in the current frame") as info:
        bookmarks.select(FakePage(), step())
    assert info.value.diagnostic["bookmark"]["reason"] == "dataset_unavailable" and grid.presses == []


def test_missing_identity_names_scope(monkeypatch):
    grid = FakeGrid(long_list())
    wire(monkeypatch, grid, [entry("Other report", "bookmark-9")])
    with pytest.raises(bookmarks.BookmarkSelectionError, match="not in the current scope") as info:
        bookmarks.select(FakePage(), step(scope="Public"))
    assert info.value.diagnostic["bookmark"]["reason"] == "identity_missing" and grid.presses == []


def test_duplicate_names_require_stable_id_and_folder_confirmation(monkeypatch):
    rows = [("Actual Sales", None), ("Saved report", ["SCM", "Actual Sales"])] + [("Report " + str(i), ["SCM", "Actual Sales"]) for i in range(12)]
    rows += [("Forecast", None), ("Saved report", ["SCM", "Forecast"])]
    grid = FakeGrid(rows)
    entries = [entry("Saved report", "one", ("SCM", "Actual Sales")), entry("Saved report", "two", ("SCM", "Forecast"))]
    wire(monkeypatch, grid, entries)
    with pytest.raises(bookmarks.BookmarkSelectionError, match="ambiguous") as info:
        bookmarks.select(FakePage(), step(scope="Public"))
    assert info.value.diagnostic["bookmark"]["reason"] == "identity_ambiguous"
    result = bookmarks.select(FakePage(), step(scope="Public", bookmark_id="two"))
    assert result["collided"] and result["folder_confirmed"] and result["movement"]
    assert grid.clicked[0]["text"] == "Saved report" and grid.clicked[0]["y"] > 0
    assert grid.rows[grid.start + int(grid.clicked[0]["id"].rsplit("_", 1)[1])][1] == ["SCM", "Forecast"]


def test_duplicate_names_under_same_named_subfolders_need_a_distinguishing_ancestor(monkeypatch):
    # Both bookmarks sit in an "Actual Sales" subfolder; only the top-level
    # folder differs, and it scrolls out of view before the first duplicate.
    rows = [("SCM", None), ("Actual Sales", ["SCM"])] + [("Report " + str(i), ["SCM", "Actual Sales"]) for i in range(10)]
    rows += [("Saved report", ["SCM", "Actual Sales"])]
    rows += [("Forecast", None), ("Actual Sales", ["Forecast"])] + [("Other " + str(i), ["Forecast", "Actual Sales"]) for i in range(10)]
    rows += [("Saved report", ["Forecast", "Actual Sales"])]
    # The rendered tree only sees the immediate ancestor, like a scrolled real
    # grid whose top-level folder row is off-screen.
    grid = FakeGrid(rows, visible_ancestors=1)
    entries = [entry("Saved report", "one", ("SCM", "Actual Sales")), entry("Saved report", "two", ("Forecast", "Actual Sales"))]
    wire(monkeypatch, grid, entries)
    with pytest.raises(bookmarks.BookmarkSelectionError, match="top and the end") as info:
        bookmarks.select(FakePage(), step(bookmark_id="two"))
    assert info.value.diagnostic["bookmark"]["reason"] == "end_confirmed" and grid.clicked == []


def test_duplicate_names_in_one_folder_fail_closed(monkeypatch):
    grid = FakeGrid(long_list(target_at=3))
    entries = [entry("Saved report", "one"), entry("Saved report", "two")]
    wire(monkeypatch, grid, entries)
    with pytest.raises(bookmarks.BookmarkSelectionError, match="more than once in the same folder") as info:
        bookmarks.select(FakePage(), step(bookmark_id="two"))
    assert info.value.diagnostic["bookmark"]["reason"] == "duplicates_share_folder" and grid.presses == [] and grid.clicked == []


def test_cancellation_through_progress_stops_movement(monkeypatch):
    grid = FakeGrid(long_list(target_at=25))
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    calls = []

    class Cancelled(Exception):
        pass

    def progress(detail):
        calls.append(detail["phase"])
        if len(calls) == 3:
            raise Cancelled("Test cancelled.")
    with pytest.raises(Cancelled):
        bookmarks.select(FakePage(), step(), progress=progress)
    assert len(grid.presses) == bookmarks.PAGE_STEPS and grid.clicked == []


def test_deadline_stops_the_step(monkeypatch):
    grid = FakeGrid(long_list(target_at=25))
    wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
    monkeypatch.setattr(bookmarks, "DEADLINE_SECONDS", 0)
    with pytest.raises(bookmarks.BookmarkSelectionError, match="120-second step deadline") as info:
        bookmarks.select(FakePage(), step())
    assert info.value.diagnostic["bookmark"]["reason"] == "deadline" and grid.clicked == []


def test_run_log_keeps_bookmark_strategy_and_drops_names():
    detail = {"phase": "action_finished", "bookmark": {"strategy": "favorite-scrollbar", "movement": True, "rendered": True,
              "movements": 3, "presses": 24, "top_established": True, "collided": False, "folder_confirmed": False,
              "reason": None, "scope": "Public", "bookmark_name": "Saved report", "bookmark_id": "RC_1"}}
    clean = diagnostics.sanitize_diagnostic(detail)
    assert clean["bookmark"] == {"strategy": "favorite-scrollbar", "movement": True, "rendered": True, "movements": 3,
                                 "presses": 24, "top_established": True, "collided": False, "folder_confirmed": False, "scope": "Public"}


def test_portable_bundle_includes_the_helper():
    from app import flow_portable
    sources = flow_portable.execution_sources()
    assert "flow_recording_gscm_bookmark" in sources and "BookmarkSelectionError" in sources["flow_recording_gscm_bookmark"]
