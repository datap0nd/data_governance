import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database
from app import flow_local_runner
from app import flow_worker
from app.routers import flows, pipelines


@pytest.fixture()
def flow_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flows.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    with database.get_db() as db:
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('flows_browser_channel','msedge')")
    return db_path


def _request(actor="Analyst"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _asap_test_page(mouse=None):
    return SimpleNamespace(
        mouse=mouse or SimpleNamespace(move=lambda *_args, **_kwargs: None),
        wait_for_timeout=lambda _ms: None,
    )


def _assert_asap_plain_click(modifiers, kwargs):
    assert modifiers is None
    assert kwargs == {"button": "left", "click_count": 1, "delay": 100}


def test_asap_member_selected_never_uses_blue_styling_while_row_is_hovered():
    evaluated = []

    class Option:
        def evaluate(self, script):
            evaluated.append(script)
            return None

    assert flow_worker._asap_member_selected(Option()) is None
    assert "if (!current.matches(':hover'))" in evaluated[0]
    assert evaluated[0].index("if (!current.matches(':hover'))") < evaluated[0].index(
        "getComputedStyle(current)"
    )


def test_asap_multi_select_reconciles_retained_selection_to_exact_values(monkeypatch):
    events = []
    selected = {"Extra": True, "202619": True, "202620": False, "202621": False}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            selected[self.value] = not selected[self.value]

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            return Locator(value)

    monkeypatch.setattr(flow_worker, "_asap_member_selected", lambda option: selected[option.value])
    flow_worker._asap_select_list_values(
        Frame(), "Sell-out Week", ["202619", "202620", "202621"], list(selected),
    )

    assert events == [
        ("click", "Extra", ()),
        ("click", "202620", ()),
        ("click", "202621", ()),
    ]
    assert {key for key, value in selected.items() if value} == {"202619", "202620", "202621"}


def test_asap_week_dates_follow_live_sunday_to_saturday_calendar():
    assert flow_worker._asap_week_dates("2026-W22") == ("20260524", "20260530")
    assert flow_worker._asap_week_dates("2026-W33") == ("20260809", "20260815")


def test_asap_week_slider_discovery_expands_bounds_and_restores_handles(monkeypatch):
    values = ["202622", "202633"]
    bounds = [("202501", "202633"), ("202501", "202633")]

    class Handle:
        def __init__(self, index):
            self.index = index

        def get_attribute(self, name):
            return values[self.index] if name == "aria-valuetext" else None

        def inner_text(self):
            return ""

        def press(self, key):
            if key == "Home":
                values[self.index] = bounds[self.index][0]
            elif key == "End":
                values[self.index] = bounds[self.index][1]
            else:
                raw = values[self.index]
                current = datetime.fromisocalendar(int(raw[:4]), int(raw[4:]), 1)
                current += flow_worker.timedelta(days=7 if key == "ArrowRight" else -7)
                year, week, _weekday = current.isocalendar()
                values[self.index] = f"{year:04d}{week:02d}"

    handles = [Handle(0), Handle(1)]
    monkeypatch.setattr(flow_worker, "_asap_range_scope", lambda _frame, _label: (object(), handles))

    options, automation = flow_worker._asap_discover_week_slider(object())

    assert options[0] == "202501"
    assert options[-1] == "202633"
    assert "202520" in options
    assert automation == {"kind": "range_slider", "date_range_label": "Date"}
    assert values == ["202622", "202633"]


def test_asap_period_slider_has_no_coupled_date_range(monkeypatch):
    values = ["202623", "202634"]
    bounds = [("202601", "202634"), ("202601", "202634")]

    class Handle:
        def __init__(self, index):
            self.index = index

        def get_attribute(self, name):
            return values[self.index] if name == "aria-valuetext" else None

        def inner_text(self):
            return ""

        def press(self, key):
            if key == "Home":
                values[self.index] = bounds[self.index][0]
            elif key == "End":
                values[self.index] = bounds[self.index][1]
            else:
                raw = values[self.index]
                current = datetime.fromisocalendar(int(raw[:4]), int(raw[4:]), 1)
                current += flow_worker.timedelta(days=7 if key == "ArrowRight" else -7)
                year, week, _weekday = current.isocalendar()
                values[self.index] = f"{year:04d}{week:02d}"

    handles = [Handle(0), Handle(1)]
    monkeypatch.setattr(flow_worker, "_asap_range_scope", lambda _frame, label: (object(), handles))

    options, automation = flow_worker._asap_discover_period_slider(object())

    assert options[0] == "202601"
    assert options[-1] == "202634"
    assert automation == {"kind": "range_slider"}
    assert values == ["202623", "202634"]


def test_asap_collapsed_range_advances_upper_handle_first(monkeypatch):
    values = ["202519", "202519"]
    events = []

    class Handle:
        def __init__(self, index):
            self.index = index

        def get_attribute(self, name):
            return values[self.index] if name == "aria-valuetext" else None

        def inner_text(self):
            return ""

        def press(self, key):
            events.append((self.index, key))
            raw = values[self.index]
            current = datetime.fromisocalendar(int(raw[:4]), int(raw[4:]), 1)
            current += flow_worker.timedelta(days=7 if key == "ArrowRight" else -7)
            year, week, _weekday = current.isocalendar()
            candidate = f"{year:04d}{week:02d}"
            if self.index == 0 and flow_worker._asap_slider_ordinal(candidate, "week") > flow_worker._asap_slider_ordinal(values[1], "week"):
                return
            if self.index == 1 and flow_worker._asap_slider_ordinal(candidate, "week") < flow_worker._asap_slider_ordinal(values[0], "week"):
                return
            values[self.index] = candidate

    handles = [Handle(0), Handle(1)]
    monkeypatch.setattr(flow_worker, "_asap_range_scope", lambda _frame, _label: (object(), handles))

    flow_worker._asap_set_range(object(), "Week", "202520", "202520", "week")

    assert values == ["202520", "202520"]
    assert events[0] == (1, "ArrowRight")


def test_asap_range_uses_visible_labels_when_handles_have_no_values(monkeypatch):
    values = ["202622", "202633"]

    class Scope:
        def inner_text(self):
            return f"Week\n{values[0]}\n{values[1]}"

    class Handle:
        def __init__(self, index):
            self.index = index

        def get_attribute(self, _name):
            return None

        def inner_text(self):
            return ""

        def press(self, key):
            raw = values[self.index]
            current = datetime.fromisocalendar(int(raw[:4]), int(raw[4:]), 1)
            current += flow_worker.timedelta(days=7 if key == "ArrowRight" else -7)
            year, week, _weekday = current.isocalendar()
            candidate = f"{year:04d}{week:02d}"
            if self.index == 0 and flow_worker._asap_slider_ordinal(candidate, "week") > flow_worker._asap_slider_ordinal(values[1], "week"):
                return
            if self.index == 1 and flow_worker._asap_slider_ordinal(candidate, "week") < flow_worker._asap_slider_ordinal(values[0], "week"):
                return
            values[self.index] = candidate

    handles = [Handle(0), Handle(1)]
    monkeypatch.setattr(flow_worker, "_asap_range_scope", lambda _frame, _label: (Scope(), handles))

    flow_worker._asap_set_range(object(), "Week", "202620", "202620", "week")

    assert values == ["202620", "202620"]


def test_asap_manual_week_definition_infers_visible_range_slider(monkeypatch):
    ranges = []
    monkeypatch.setattr(flow_worker, "_asap_range_scope", lambda _frame, _label: (object(), [object(), object()]))
    monkeypatch.setattr(
        flow_worker,
        "_asap_set_range",
        lambda _frame, label, start, end, kind: ranges.append((label, start, end, kind)),
    )

    flow_worker._asap_apply_configuration(
        object(),
        {
            "selections": {},
            "report": {
                "filters": [{
                    "filter_key": "week",
                    "control_label": "Week",
                    "control_type": "week",
                    "options": ["202520", "202633"],
                    "automation": {},
                }],
            },
        },
        ["2025-W20", "2025-W20"],
    )

    assert ranges == [
        ("Week", "202520", "202520", "week"),
        ("Date", "20250511", "20250517", "date"),
    ]


def test_asap_period_range_does_not_drive_a_nonexistent_date_slider(monkeypatch):
    ranges = []
    monkeypatch.setattr(
        flow_worker,
        "_asap_set_range",
        lambda _frame, label, start, end, kind: ranges.append((label, start, end, kind)),
    )

    flow_worker._asap_apply_configuration(
        object(),
        {
            "selections": {},
            "report": {
                "filters": [{
                    "filter_key": "period",
                    "control_label": "Period",
                    "control_type": "week",
                    "options": ["202623", "202634"],
                    "automation": {"kind": "range_slider"},
                }],
            },
        },
        ["2026-W23", "2026-W34"],
    )

    assert ranges == [("Period", "202623", "202634", "week")]


def test_asap_week_retries_dropped_final_plain_click_with_unknown_unselected_state(monkeypatch):
    events = []
    selected = {"202629": True, "202630": True, "202631": True, "202632": False}
    dropped = {"202632": 1}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            if dropped.get(self.value, 0):
                dropped[self.value] -= 1
                return
            selected[self.value] = not selected[self.value]

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            return Locator(value)

    monkeypatch.setattr(
        flow_worker, "_asap_member_selected",
        lambda option: True if selected[option.value] else None,
    )

    flow_worker._asap_select_list_values(
        Frame(), "Sell-out Week", list(selected), list(selected),
    )

    assert events == [
        ("click", "202632", ()),
        ("click", "202632", ()),
    ]
    assert all(selected.values())


def test_asap_week_retries_dropped_final_click_after_clearing_hover_false_positive(monkeypatch):
    events = []
    selected = {"202627": True, "202628": True, "202629": False}
    dropped = {"202629": 1}
    hovered = {"value": None}

    class Mouse:
        def move(self, _x, _y):
            hovered["value"] = None

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            hovered["value"] = self.value
            if dropped.get(self.value, 0):
                dropped[self.value] -= 1
                return
            selected[self.value] = not selected[self.value]

    class Frame:
        page = _asap_test_page(Mouse())

        def get_by_text(self, value, exact=True):
            return Locator(value)

    # This models ASAP's blue hover looking identical to a blue selected row.
    # The first click is dropped but would be falsely accepted unless the
    # scraper moves the pointer away before checking the rendered state.
    monkeypatch.setattr(
        flow_worker, "_asap_member_selected",
        lambda option: True
        if selected[option.value] or hovered["value"] == option.value
        else None,
    )

    flow_worker._asap_select_list_values(
        Frame(), "Sell-out Week", list(selected), list(selected),
    )

    assert events == [
        ("click", "202629", ()),
        ("click", "202629", ()),
    ]
    assert all(selected.values())


def test_asap_week_waits_for_delayed_selection_confirmation_without_toggling_again(monkeypatch):
    events = []
    selected = {"202632": False}
    delayed_reads = {"202632": 0}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            selected[self.value] = not selected[self.value]
            delayed_reads[self.value] = 5

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            return Locator(value)

    def rendered_state(option):
        if delayed_reads[option.value]:
            delayed_reads[option.value] -= 1
            return None
        return True if selected[option.value] else None

    monkeypatch.setattr(flow_worker, "_asap_member_selected", rendered_state)

    flow_worker._asap_select_list_values(
        Frame(), "Sell-out Week", ["202632"], ["202632"],
    )

    assert events == [("click", "202632", ())]
    assert selected == {"202632": True}


def test_asap_week_does_not_toggle_off_an_already_selected_requested_default(monkeypatch):
    events = []
    selected = {"202631": False, "202632": True}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            selected[self.value] = not selected[self.value]

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            return Locator(value)

    monkeypatch.setattr(flow_worker, "_asap_member_selected", lambda option: selected[option.value])

    flow_worker._asap_select_list_values(
        Frame(), "Sell-out Week", ["202632"], list(selected),
    )

    assert events == []
    assert selected == {"202631": False, "202632": True}


def test_asap_list_scope_prefers_nearest_owner_when_labels_repeat():
    class Collection:
        def __init__(self, nodes):
            self.nodes = nodes
            self.first = nodes[0] if nodes else None

        def count(self):
            return len(self.nodes)

        def nth(self, index):
            return self.nodes[index]

    class Node:
        def __init__(self, name, texts=(), parent=None, area=100):
            self.name = name
            self.texts = set(texts)
            self.parent = parent
            self.area = area

        def is_visible(self):
            return True

        def locator(self, selector):
            assert selector == "xpath=parent::*"
            return Collection([self.parent] if self.parent else [])

        def get_by_text(self, value, exact=True):
            return Collection([Node(value)] if value in self.texts else [])

        def bounding_box(self):
            return {"width": self.area, "height": 1}

    root = Node("root", texts={"Choice"}, area=1000)
    unrelated = Node("unrelated", parent=root, area=100)
    prompt = Node("prompt", texts={"Choice"}, parent=root, area=80)
    repeated_member = Node("repeated member", parent=unrelated)
    actual_label = Node("actual label", parent=prompt)

    class Frame:
        page = _asap_test_page()

        def get_by_role(self, *_args, **_kwargs):
            return Collection([])

        def get_by_text(self, value, exact=True):
            assert value == "Repeated Label"
            return Collection([repeated_member, actual_label])

    assert flow_worker._asap_list_scope(Frame(), "Repeated Label", ["Choice"]) is prompt


def test_asap_dimension_plain_clicks_selected_members_off_then_requested_members_on(monkeypatch):
    events = []
    selected = {"Biz Sub": True, "Sold To": False, "Customer": True}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            selected[self.value] = not selected[self.value]

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            return Locator(value)

    monkeypatch.setattr(flow_worker, "_asap_member_selected", lambda option: selected[option.value])
    flow_worker._asap_select_list_values(
        Frame(), "Dimension", ["Sold To", "Customer"], list(selected),
    )

    assert events == [
        ("click", "Biz Sub", ()),
        ("click", "Customer", ()),
        ("click", "Sold To", ()),
        ("click", "Customer", ()),
    ]
    assert selected == {"Biz Sub": False, "Sold To": True, "Customer": True}


def test_asap_dimension_retries_dropped_clear_clicks_across_reconciliation_rounds(monkeypatch):
    events = []
    selected = {"Biz Sub": True, "Sold To": False}
    dropped = {"Biz Sub": 4}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def wait_for(self, **_kwargs):
            return None

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            if dropped.get(self.value, 0):
                dropped[self.value] -= 1
                return
            selected[self.value] = not selected[self.value]

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            return Locator(value)

    monkeypatch.setattr(
        flow_worker, "_asap_member_selected",
        lambda option: True if selected[option.value] else None,
    )

    flow_worker._asap_select_list_values(
        Frame(), "Dimension", ["Sold To"], list(selected),
    )

    assert events == [
        ("click", "Biz Sub", ()),
        ("click", "Biz Sub", ()),
        ("click", "Biz Sub", ()),
        ("click", "Biz Sub", ()),
        ("click", "Biz Sub", ()),
        ("click", "Sold To", ()),
    ]
    assert selected == {"Biz Sub": False, "Sold To": True}


def test_asap_dimension_scopes_duplicate_sell_out_week_member(monkeypatch):
    events = []
    selected = {"MKT Name": True, "Item": True, "Sell-out Week": True}

    class Locator:
        first = None

        def __init__(self, value):
            self.value = value
            self.first = self

        def count(self):
            return 1

        def nth(self, _index):
            return self

        def is_visible(self):
            return True

        def click(self, modifiers=None, **kwargs):
            _assert_asap_plain_click(modifiers, kwargs)
            events.append(("click", self.value, tuple(modifiers or [])))
            selected[self.value] = not selected[self.value]

    class DimensionScope:
        def get_by_text(self, value, exact=True):
            assert value in selected
            return Locator(value)

        def inner_text(self):
            return "Dimension\nMKT Name\nItem\nSell-out Week"

    class Frame:
        page = _asap_test_page()

        def get_by_text(self, value, exact=True):
            # A page-wide lookup for this value would resolve the separate Week
            # prompt heading first. The Dimension path must never use it.
            if value != "Dimension":
                raise AssertionError(f"page-wide member lookup used for {value}")
            return Locator(value)

    monkeypatch.setattr(
        flow_worker, "_asap_list_scope", lambda _frame, _label, _requested: DimensionScope(),
    )
    monkeypatch.setattr(flow_worker, "_asap_member_selected", lambda option: selected[option.value])

    flow_worker._asap_select_list_values(
        Frame(), "Dimension", ["MKT Name", "Item"], ["MKT Name", "Item"],
    )

    assert events == [
        ("click", "MKT Name", ()),
        ("click", "Item", ()),
        ("click", "Sell-out Week", ()),
        ("click", "MKT Name", ()),
        ("click", "Item", ()),
    ]
    assert selected == {"MKT Name": True, "Item": True, "Sell-out Week": False}


def test_asap_dimension_bypasses_native_selection(monkeypatch):
    calls = []
    definition = {
        "filter_key": "dimension",
        "control_label": "Dimension",
        "control_type": "multi_select",
        "options": ["Biz Sub", "Sold To"],
    }
    job = {"selections": {"dimension": ["Sold To"]}, "report": {"filters": [definition]}}

    monkeypatch.setattr(
        flow_worker, "_select_native_options_by_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("native control used")),
    )
    monkeypatch.setattr(
        flow_worker, "_asap_select_list_values",
        lambda _frame, label, values, options: calls.append((label, values, options)),
    )

    flow_worker._asap_apply_configuration(object(), job, None)

    assert calls == [("Dimension", ["Sold To"], ["Biz Sub", "Sold To"])]


def test_asap_category_bypasses_native_selection(monkeypatch):
    calls = []
    definition = {
        "filter_key": "category",
        "control_label": "Category",
        "control_type": "multi_select",
        "options": ["Weekly", "Daily"],
    }
    job = {"selections": {"category": ["Weekly"]}, "report": {"filters": [definition]}}

    monkeypatch.setattr(
        flow_worker, "_select_native_options_by_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("native control used")),
    )
    monkeypatch.setattr(
        flow_worker, "_asap_select_list_values",
        lambda _frame, label, values, options: calls.append((label, values, options)),
    )

    flow_worker._asap_apply_configuration(object(), job, None)

    assert calls == [("Category", ["Weekly"], ["Weekly", "Daily"])]


def test_asap_week_bypasses_native_selection_and_uses_visible_exact_value(monkeypatch):
    calls = []
    definition = {
        "filter_key": "sell_out_week",
        "control_label": "Sell-out Week",
        "control_type": "week",
        "options": ["202632", "202627"],
    }
    job = {
        "selections": {},
        "report": {"filters": [definition]},
    }

    monkeypatch.setattr(
        flow_worker, "_select_native_options_by_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("native control used")),
    )
    monkeypatch.setattr(
        flow_worker, "_asap_select_list_values",
        lambda _frame, label, values, options: calls.append((label, values, options)),
    )

    flow_worker._asap_apply_configuration(object(), job, "2026-W27")

    assert calls == [("Sell-out Week", ["202627"], ["202632", "202627"])]


def _site():
    return flows.SiteWrite(
        name="Report portal",
        auth_url="https://reports.example.test/login",
        base_url="https://reports.example.test",
    )


def _asap_site():
    return flows.SiteWrite(
        name="ASAP",
        adapter="asap_portal",
        auth_url="https://portal.example.test/portal/login/app",
        base_url="https://portal.example.test",
        discovery_enabled=True,
        discovery_scope=["Mobile"],
        discovery_weekday="saturday",
        discovery_time="06:00",
    )


def _gscm_site():
    return flows.SiteWrite(
        name="GSCM Test Portal",
        adapter="gscm_portal",
        auth_url="https://gscm.example.test/login",
        base_url="https://gscm.example.test",
        discovery_enabled=True,
    )


def _gscm_discovered_report(category_path, bookmark_id=None, *, favorite_name=None):
    catalog_name = category_path[-1]
    favorite_name = favorite_name or re.sub(r" \(\d+\)$", "", catalog_name)
    return flows.DiscoveredReport(
        discovery_key=" > ".join(category_path),
        name=catalog_name,
        report_url="https://gscm.example.test",
        download_text="Export Excel",
        automation={
            "kind": "gscm_favorite",
            "category_path": category_path,
            "favorite_tab": category_path[0],
            "favorite_name": favorite_name,
            "favorite_folder_path": category_path[1:-1],
            "favorite_bookmark_id": bookmark_id,
        },
    )


def _report(site_id):
    return flows.ReportWrite(
        site_id=site_id,
        name="Weekly movement",
        report_url="https://reports.example.test/report/weekly-movement",
        ready_text="Week",
        open_export_text="Export detail",
        download_text="Download CSV",
        filters=[
            flows.FilterWrite(
                filter_key="region",
                label="Region",
                control_label="Sell-in region",
                control_type="select",
                options=["Global", "North"],
                required=True,
            ),
            flows.FilterWrite(
                filter_key="week",
                label="Week",
                control_label="Week",
                control_type="week",
            ),
        ],
    )


def _asap_report(site_id):
    return flows.ReportWrite(
        site_id=site_id,
        name="Installed Base MENA",
        report_url="https://portal.example.test/portal/login/app",
        ready_text="Export Wizard (Detail)",
        download_text="Export CSV",
        automation={
            "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
            "report_tab": "Export Wizard (Detail)",
            "export_text": "Export Wizard (Detail)",
        },
        filters=[
            flows.FilterWrite(
                filter_key="data_configuration",
                label="Data configuration",
                control_label="Data Configuration",
                control_type="select",
                options=["MENA - Global - Global", "Global - Global - MENA", "Global - Global - CIS"],
                required=True,
            ),
            flows.FilterWrite(
                filter_key="week",
                label="Sell-out week",
                control_label="Sell-out Week",
                control_type="week",
            ),
        ],
    )


def _mark_discovered(report_id):
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET source_kind='discovered', stale=0, discovery_key=name WHERE id=?",
            (report_id,),
        )


def _flow(site_id, report_id, **overrides):
    data = {
        "name": "Weekly report download",
        "site_id": site_id,
        "report_id": report_id,
        "enabled": True,
        "selections": {"region": "Global"},
        "download_mode": "one_per_period",
        "period_strategy": "fixed",
        "window_weeks": 1,
        "browser_mode": "headless",
        "start_week": "2026-W30",
        "end_week": "2026-W32",
        "target_folder": r"C:\Reports\Downloads",
        "filename_template": "weekly_{week}.csv",
        "schedule_type": "weekly",
        "schedule_time": "08:00",
        "schedule_days": ["monday"],
        "sql_handoff_enabled": False,
    }
    data.update(overrides)
    return flows.FlowWrite(**data)


def _seed_catalog():
    site = flows.create_site(_site(), _request())
    report = flows.create_report(_report(site["id"]), _request())
    return site, report


def test_catalog_and_flow_configuration_persist_locally(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())

    assert saved["site_name"] == "Report portal"
    assert saved["report_name"] == "Weekly movement"
    assert saved["selections"] == {"region": "Global"}
    assert saved["sql_handoff_enabled"] is False
    assert saved["transform_enabled"] is False
    catalog = flows.catalog()
    assert catalog["reports"][0]["filters"][0]["options"] == ["Global", "North"]


def test_direct_output_mode_persists_and_freezes_into_the_worker_job(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], output_mode="direct_replace"), _request(),
    )
    queued = flows.queue_run(saved["id"], _request())

    assert saved["output_mode"] == "direct_replace"
    assert queued["job"]["downloads"]["output_mode"] == "direct_replace"
    assert queued["job"]["downloads"]["collision_policy"] == "replace_exact"
    assert queued["job"]["downloads"]["overwrite_existing"] is True


def test_output_mode_defaults_and_rejects_unknown_values(flow_db):
    site, report = _seed_catalog()
    body = _flow(site["id"], report["id"])
    assert body.output_mode == "run_folders"
    with pytest.raises(ValueError, match="Output storage"):
        _flow(site["id"], report["id"], output_mode="keep_everything")


def test_direct_flows_share_a_folder_lock_even_with_different_sql_targets(
    flow_db, monkeypatch,
):
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode},
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.executemany(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', ?, CURRENT_TIMESTAMP, 0)""",
            [("target_a",), ("target_b",)],
        )
    shared = {
        "output_mode": "direct_replace",
        "target_folder": r"C:\Stable\Exports",
        "sql_handoff_enabled": True,
        "sql_mode": "append",
        "sql_database": "warehouse",
        "sql_schema": "reporting",
    }
    first = flows.create_flow(
        _flow(
            site["id"], report["id"], name="Direct A",
            filename_template="daily_{date}_{index}.csv", sql_table="target_a", **shared,
        ),
        _request(),
    )
    second = flows.create_flow(
        _flow(
            site["id"], report["id"], name="Direct B",
            filename_template="weekly_{week}.csv", sql_table="target_b", **shared,
        ),
        _request(),
    )

    first_run = flows.queue_run(first["id"], _request())
    with pytest.raises(HTTPException, match="Direct Flow output folder"):
        flows.queue_run(second["id"], _request())

    with database.get_db() as db:
        active = db.execute(
            "SELECT flow_id FROM flow_runs WHERE status='queued' ORDER BY id"
        ).fetchall()
    assert [row["flow_id"] for row in active] == [first_run["flow_id"]]


def test_resume_is_blocked_when_output_storage_mode_changed(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], output_mode="direct_replace"), _request(),
    )
    source = flows.queue_run(saved["id"], _request())
    artifact = {
        "period_key": ["2026-W30"],
        "export_view": None,
        "status": "saved",
        "file_path": r"C:\worker\private\week30.csv",
        "filename": "week30.csv",
        "storage_scope": "worker_private",
        "artifact_store_id": "store-a",
    }
    with database.get_db() as db:
        db.execute(
            """UPDATE flow_runs SET status='failed', artifact_json=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (json.dumps([artifact]), source["id"]),
        )
        db.execute(
            "UPDATE flows SET output_mode='run_folders' WHERE id=?", (saved["id"],),
        )
        result = flows.inspect_resume_eligibility(db, source["id"])

    assert result["status"] == "blocked"
    assert result["reason_code"] == "output_mode_changed"


def test_private_sql_retry_is_deferred_to_and_claimed_by_matching_artifact_store(
    flow_db, monkeypatch,
):
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode},
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'private_target', CURRENT_TIMESTAMP, 0)"""
        )
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], output_mode="direct_replace",
            sql_handoff_enabled=True, sql_mode="append", sql_database="warehouse",
            sql_schema="reporting", sql_table="private_target",
        ),
        _request(),
    )
    source = flows.queue_run(saved["id"], _request())
    private = {
        "file_path": r"C:\moved-profile\run_artifacts\missing.csv",
        "filename": "missing.csv",
        "file_size": 123,
        "checksum": "a" * 64,
        "row_count": 1,
        "status": "saved",
        "storage_scope": "worker_private",
        "artifact_store_id": "store-a",
    }
    with database.get_db() as db:
        db.execute(
            """UPDATE flow_runs SET status='failed', artifact_json=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (json.dumps([private]), source["id"]),
        )

    retry = flows.retry_run_sql(source["id"], _request())
    assert retry["job"]["execution"]["required_artifact_store_id"] == "store-a"
    flows.register_worker(flows.WorkerRegister(
        worker_id="wrong-store", display_name="Wrong store",
        capabilities={"artifact_store_id": "store-b"},
    ))
    assert flows.claim_run("wrong-store")["run"] is None
    flows.register_worker(flows.WorkerRegister(
        worker_id="right-store", display_name="Right store",
        capabilities={"artifact_store_id": "store-a"},
    ))
    assert flows.claim_run("right-store")["run"]["id"] == retry["id"]

    with database.get_db() as db:
        moved_retry = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, requested_by, job_json, created_at)
               VALUES (?, 'sql_retry', 'queued', 'Analyst', ?, CURRENT_TIMESTAMP)""",
            (saved["id"], json.dumps(retry["job"])),
        ).lastrowid
    flows.register_worker(flows.WorkerRegister(
        worker_id=flows.LOCAL_WORKER_ID, display_name="Moved profile",
        capabilities={"artifact_store_id": "new-store"},
    ))
    assert flows.claim_run(flows.LOCAL_WORKER_ID)["run"] is None
    with database.get_db() as db:
        failed = db.execute(
            "SELECT status, error FROM flow_runs WHERE id=?", (moved_retry,),
        ).fetchone()
    assert failed["status"] == "failed"
    assert "profile store identity changed" in failed["error"]


def test_published_metadata_persists_and_extension_drift_records_warning(
    flow_db, monkeypatch,
):
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode},
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], output_mode="direct_replace"), _request(),
    )
    flows.register_worker(flows.WorkerRegister(
        worker_id="publish-worker", display_name="Publish worker", capabilities={},
    ))

    first = flows.queue_run(saved["id"], _request())
    assert flows.claim_run("publish-worker")["run"]["id"] == first["id"]
    old_artifact = {
        "period_key": ["2026-W30"], "export_view": None, "status": "saved",
        "file_path": r"C:\private\weekly.csv", "filename": "weekly.csv",
        "storage_scope": "worker_private", "artifact_store_id": "store-a",
        "file_size": 10, "checksum": "1" * 64,
        "published_file_path": r"C:\Stable\weekly.xls",
        "published_filename": "weekly.xls", "publish_status": "published",
    }
    flows.update_run(
        "publish-worker", first["id"],
        flows.WorkerProgress(status="succeeded", artifacts=[old_artifact]),
    )

    second = flows.queue_run(saved["id"], _request())
    assert flows.claim_run("publish-worker")["run"]["id"] == second["id"]
    new_artifact = {
        **old_artifact,
        "published_file_path": r"C:\Stable\weekly.xlsx",
        "published_filename": "weekly.xlsx",
    }
    flows.update_run(
        "publish-worker", second["id"],
        flows.WorkerProgress(
            status="running",
            progress={"stage": "publish_complete", "message": "Published."},
            artifacts=[new_artifact],
        ),
    )

    with database.get_db() as db:
        recorded = db.execute(
            """SELECT storage_scope, artifact_store_id, published_filename, publish_status
               FROM flow_run_files WHERE run_id=?""",
            (first["id"],),
        ).fetchone()
        warning = db.execute(
            """SELECT message FROM flow_run_events
               WHERE run_id=? AND stage='publish_name_changed'""",
            (second["id"],),
        ).fetchone()
    assert dict(recorded) == {
        "storage_scope": "worker_private",
        "artifact_store_id": "store-a",
        "published_filename": "weekly.xls",
        "publish_status": "published",
    }
    assert "intentionally left in place" in warning["message"]


def test_flow_builder_exposes_and_replicates_output_storage_setting():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text(
        encoding="utf-8",
    )
    assert source.count('id="flow-output-mode"') == 2
    assert 'output_mode: $("#flow-output-mode")?.value || "run_folders"' in source
    assert "...source" in source and "_flowShowView(\"builder\", copy)" in source
    assert "Outlook keeps the original attachment name" in source


def test_report_filter_update_keeps_historical_definition_for_saved_runs(flow_db):
    site, report = _seed_catalog()
    updated = _report(site["id"])
    updated.filters = [updated.filters[0]]
    flows.update_report(report["id"], updated, _request())

    with database.get_db() as db:
        rows = db.execute(
            "SELECT filter_key, enabled FROM flow_report_filters WHERE report_id=? ORDER BY filter_key",
            (report["id"],),
        ).fetchall()
    assert [(row["filter_key"], row["enabled"]) for row in rows] == [("region", 1), ("week", 0)]


def test_one_per_period_job_is_expanded_without_delete_or_overwrite(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())

    assert queued["job"]["downloads"]["periods"] == [["2026-W30"], ["2026-W31"], ["2026-W32"]]
    assert queued["job"]["downloads"]["collision_policy"] == "number_suffix"
    assert queued["job"]["transformation"] == {
        "enabled": False, "script_path": None, "output_subfolder": "script_results",
        "input_argument": "--input", "output_argument": "--output",
    }
    assert queued["job"]["downloads"]["delete_existing"] is False
    assert queued["job"]["downloads"]["overwrite_existing"] is False
    assert queued["job"]["execution"] == {
        "mode": "local", "host": "bi_desktop", "browser_mode": "headless",
        "worker_id": "bi-desktop-headless", "download_parallelism": 1, "browser_channel": "msedge",
    }
    assert queued["job"]["sql_handoff"] == {
        "enabled": False, "server": flows.normalize_server(flows.UPLOAD_PGHOST),
        "mode": None, "uppercase": False,
        "database": None, "schema": None, "table": None,
    }


def test_transformation_configuration_is_persisted_in_job(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(
        site["id"], report["id"], transform_enabled=True,
        transform_script_path=r"C:\Scripts\clean_report.py",
    ), _request())
    queued = flows.queue_run(saved["id"], _request())
    assert saved["transform_enabled"] is True
    assert queued["job"]["transformation"]["script_path"] == r"C:\Scripts\clean_report.py"


def test_transformation_requires_supported_absolute_script_path(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with pytest.raises(ValueError, match="absolute path"):
        _flow(site["id"], report["id"], transform_enabled=True, transform_script_path="clean.py")
    with pytest.raises(ValueError, match=".py, .ps1, or .exe"):
        _flow(site["id"], report["id"], transform_enabled=True, transform_script_path=r"C:\Scripts\clean.bat")


def test_single_csv_job_passes_the_whole_week_range_to_asap(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], download_mode="single",
            selections={"region": "Global"}, filename_template="range_{week}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())

    assert queued["job"]["downloads"]["periods"] == [["2026-W30", "2026-W31", "2026-W32"]]
    assert queued["job"]["downloads"]["delete_existing"] is False
    assert queued["job"]["downloads"]["overwrite_existing"] is False


def test_fixed_range_is_split_into_week_periods(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], download_mode="one_per_period",
            window_weeks=2, filename_template="period_{week}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["periods"] == [
        ["2026-W30", "2026-W31"], ["2026-W32"],
    ]
    assert queued["job"]["downloads"]["period_unit"] == "week"
    assert queued["job"]["downloads"]["period_size"] == 2


def test_start_to_latest_uses_newest_discovered_asap_week(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_report_filters SET options_json=? WHERE report_id=? AND control_type='week'",
            ('["202630","202631","202632","202633"]', report["id"]),
        )
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], period_strategy="latest", end_week=None,
            download_mode="one_per_period", window_weeks=2,
            filename_template="{flow}_{start_period}_{end_period}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["period_end_week"] == "2026-W33"
    assert queued["job"]["downloads"]["periods"] == [
        ["2026-W30", "2026-W31"], ["2026-W32", "2026-W33"],
    ]


def test_no_period_bundle_queues_every_discovered_export_view(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        automation = {
            "category_path": ["Mobile", "Smartphone"],
            "export_views": [
                {"label": "Export Wizard (Global/Region)", "filter_keys": []},
                {"label": "Export Wizard (Selected Countries)", "filter_keys": []},
            ],
        }
        db.execute(
            "UPDATE flow_reports SET automation_json=? WHERE id=?",
            (json.dumps(automation), report["id"]),
        )
        db.execute("DELETE FROM flow_report_filters WHERE report_id=?", (report["id"],))
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], selections={}, export_views=[],
            period_strategy="none", start_week=None, end_week=None,
            download_mode="single", window_weeks=None, file_format="xlsx",
            filename_template="{flow}_{export}.xlsx", schedule_type="monthly",
            schedule_days=[], schedule_day=1,
        ),
        _request(),
    )
    assert saved["export_views"] == [
        "Export Wizard (Global/Region)", "Export Wizard (Selected Countries)",
    ]
    assert saved["schedule_day"] == 1
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["periods"] == [None]
    assert queued["job"]["downloads"]["file_format"] == "xlsx"
    assert queued["job"]["report"]["export_views"] == saved["export_views"]
    database.init_db()
    reloaded = next(item for item in flows.list_flows() if item["id"] == saved["id"])
    assert reloaded["file_format"] == "xlsx"
    assert reloaded["filename_template"] == "{flow}_{export}.xlsx"


def test_multiple_export_views_require_unique_filename_token():
    with pytest.raises(ValueError, match="export or index token"):
        flows.FlowWrite(
            name="Bundle", site_id=1, report_id=1,
            export_views=["Global", "Countries"], selections={},
            period_strategy="none", download_mode="single", file_format="xlsx",
            target_folder=r"C:\Reports", filename_template="bundle.xlsx",
        )


def test_monthly_schedule_skips_months_without_selected_day(monkeypatch):
    monkeypatch.setattr(flows, "_now", lambda: datetime(2026, 2, 1, 9, 0))
    assert flows._schedule_next("monthly", "08:00", [], 31) == datetime(2026, 3, 31, 4, 0)


def test_filename_uses_flow_start_end_and_selected_format():
    worker = __import__("app.flow_worker", fromlist=["_render_filename"])
    job = {
        "flow": {"name": "Inflow Outflow"},
        "report": {"name": "Movement"},
        "downloads": {"periods": [["2026-W19", "2026-W27"]], "file_format": "csv"},
    }
    filename = worker._render_filename(
        "{flow}_{start_period}_{end_period}.csv", job,
        ["2026-W19", "2026-W27"], 1,
    )
    assert filename == "Inflow_Outflow_W19_W27.csv"


def test_excel_format_is_supported_and_keeps_xlsx_extension():
    body = flows.FlowWrite(
        name="Excel bundle", site_id=1, report_id=1, enabled=False,
        selections={}, download_mode="single", period_strategy="fixed",
        file_format="xlsx", start_week="2026-W30", end_week="2026-W31",
        target_folder=r"C:\Reports", filename_template="report.xlsx",
    )
    assert body.file_format == "xlsx"
    assert body.filename_template == "report.xlsx"


def _seed_asap_flow_catalog():
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    _mark_discovered(report["id"])
    return site, report


def _asap_flow_body(site_id, report_id, **overrides):
    settings = {
        "selections": {"data_configuration": "MENA - Global - Global"},
        "asap_download_type": "csv_file_format",
        "file_format": "csv",
        "filename_template": "asap_{week}.csv",
    }
    settings.update(overrides)
    return _flow(site_id, report_id, **settings)


def test_asap_catalog_exposes_five_semantic_download_types_verbatim(flow_db):
    labels = [item["label"] for item in flows.catalog()["asap_download_types"]]
    assert labels == [
        "Excel with plain text", "CSV file format", "Excel with formatting",
        "HTML", "Plain text",
    ]


def test_new_asap_flow_defaults_export_options_checked_and_jobs_keep_semantics(flow_db):
    site, report = _seed_asap_flow_catalog()
    saved = flows.create_flow(_asap_flow_body(site["id"], report["id"]), _request())

    assert saved["asap_download_type"] == "csv_file_format"
    assert saved["export_report_title"] is True
    assert saved["export_filter_details"] is True
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"] | {
        "asap_download_type": "csv_file_format",
        "export_report_title": True,
        "export_filter_details": True,
    } == queued["job"]["downloads"]


def test_existing_asap_null_export_options_remain_inherited_in_jobs(flow_db):
    site, report = _seed_asap_flow_catalog()
    saved = flows.create_flow(_asap_flow_body(site["id"], report["id"]), _request())
    with database.get_db() as db:
        db.execute(
            "UPDATE flows SET export_report_title=NULL, export_filter_details=NULL WHERE id=?",
            (saved["id"],),
        )

    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["export_report_title"] is None
    assert queued["job"]["downloads"]["export_filter_details"] is None


def test_asap_migration_backfills_semantic_type_but_not_checkbox_assumptions(flow_db):
    site, report = _seed_asap_flow_catalog()
    saved = flows.create_flow(_asap_flow_body(site["id"], report["id"]), _request())
    with database.get_db() as db:
        db.execute(
            """UPDATE flows SET asap_download_type=NULL,
                      export_report_title=NULL, export_filter_details=NULL
                 WHERE id=?""",
            (saved["id"],),
        )

    database.init_db()
    with database.get_db() as db:
        row = db.execute(
            "SELECT asap_download_type, export_report_title, export_filter_details FROM flows WHERE id=?",
            (saved["id"],),
        ).fetchone()
    assert dict(row) == {
        "asap_download_type": "csv_file_format",
        "export_report_title": None,
        "export_filter_details": None,
    }


def test_scan_resolves_only_consistent_inherited_asap_export_options(flow_db):
    site, report = _seed_asap_flow_catalog()
    saved = flows.create_flow(_asap_flow_body(site["id"], report["id"]), _request())
    with database.get_db() as db:
        db.execute(
            "UPDATE flows SET export_report_title=NULL, export_filter_details=NULL WHERE id=?",
            (saved["id"],),
        )
        automation = {
            "report_tab": "Export Wizard (Detail)",
            "asap_export_capabilities": {"views": {
                "Export Wizard (Detail)": {
                    "status": "detected", "download_types": ["csv_file_format"],
                    "options_by_type": {"csv_file_format": {
                        "export_report_title": {"available": True, "checked": False},
                        "export_filter_details": {"available": True, "checked": True},
                    }},
                },
            }},
        }
        flows._resolve_inherited_asap_export_settings(
            db, report["id"], automation, "2026-08-31T10:00:00",
        )
        row = db.execute(
            "SELECT export_report_title, export_filter_details FROM flows WHERE id=?",
            (saved["id"],),
        ).fetchone()

    assert row["export_report_title"] == 0
    assert row["export_filter_details"] == 1


def test_asap_capability_validation_uses_selected_view_intersection(flow_db):
    site, report = _seed_asap_flow_catalog()
    views = ["Global", "Countries"]
    options = {
        "export_report_title": {"available": True, "checked": True},
        "export_filter_details": {"available": True, "checked": True},
    }
    automation = {
        "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
        "export_views": [{"label": view, "filter_keys": ["data_configuration", "week"]} for view in views],
        "asap_export_capabilities": {"views": {
            "Global": {
                "status": "detected", "download_types": ["csv_file_format"],
                "options_by_type": {"csv_file_format": options},
            },
            "Countries": {
                "status": "detected", "download_types": ["excel_plain_text"],
                "options_by_type": {"excel_plain_text": options},
            },
        }},
    }
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET automation_json=? WHERE id=?",
            (json.dumps(automation), report["id"]),
        )

    body = _asap_flow_body(
        site["id"], report["id"], export_views=views,
        filename_template="asap_{export}_{week}.csv",
    )
    with database.get_db() as db, pytest.raises(HTTPException, match="unavailable"):
        flows._validate_flow_selections(db, body, new_flow=True)


def test_unknown_asap_capability_is_left_for_live_runner_verification(flow_db):
    site, report = _seed_asap_flow_catalog()
    automation = {
        "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
        "report_tab": "Export Wizard (Detail)",
        "asap_export_capabilities": {"status": "partial", "views": {
            "Export Wizard (Detail)": {"status": "unknown", "error": "timed out"},
        }},
    }
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET automation_json=? WHERE id=?",
            (json.dumps(automation), report["id"]),
        )
        body = _asap_flow_body(site["id"], report["id"])
        flows._validate_flow_selections(db, body, new_flow=True)
    assert body.asap_download_type == "csv_file_format"
    assert body.export_report_title is True
    assert body.export_filter_details is True


@pytest.mark.parametrize("download_type,suffix", [("html", ".html"), ("plain_text", ".txt")])
def test_asap_html_and_plain_text_are_download_only(download_type, suffix):
    with pytest.raises(ValueError, match="download-only"):
        flows.FlowWrite(
            name="Download", site_id=1, report_id=1, selections={},
            period_strategy="none", download_mode="single",
            asap_download_type=download_type,
            filename_template=f"report{suffix}", target_folder=r"C:\Reports",
            transform_enabled=True, transform_script_path=r"C:\Scripts\clean.py",
        )


def test_asap_filename_rendering_accepts_both_excel_suffixes_and_html_htm():
    base = {
        "flow": {"name": "Export"}, "report": {"name": "Report"},
        "downloads": {"periods": [None], "file_format": "xlsx"},
    }
    excel = {**base, "downloads": {**base["downloads"], "asap_download_type": "excel_with_formatting"}}
    html = {**base, "downloads": {**base["downloads"], "file_format": "html", "asap_download_type": "html"}}
    assert flow_worker._render_filename("formatted.xls", excel, None, 1) == "formatted.xls"
    assert flow_worker._render_filename("formatted", excel, None, 1) == "formatted.xlsx"
    assert flow_worker._render_filename("report.htm", html, None, 1) == "report.htm"


def test_non_asap_and_outlook_payloads_clear_asap_only_fields(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    portal = _flow(
        site["id"], report["id"], file_format="xlsx",
        asap_download_type="excel_plain_text", filename_template="weekly_{week}.xlsx",
        export_report_title=True, export_filter_details=False,
    )
    with database.get_db() as db:
        flows._validate_flow_selections(db, portal)
    assert portal.asap_download_type is None
    assert portal.export_report_title is None
    assert portal.export_filter_details is None

    outlook = flows.FlowWrite(
        name="Mail", source_type="outlook", outlook_subject_contains="Report",
        target_folder=r"C:\Reports", filename_template=None,
        asap_download_type="csv_file_format",
        export_report_title=True, export_filter_details=True,
    )
    assert outlook.asap_download_type is None
    assert outlook.export_report_title is None
    assert outlook.export_filter_details is None


def test_rolling_window_advances_only_after_success(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], download_mode="single",
            period_strategy="rolling", window_weeks=3, end_week=None,
            filename_template="window_{week}.csv",
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["periods"] == [["2026-W30", "2026-W31", "2026-W32"]]
    assert queued["job"]["downloads"]["next_start_week"] == "2026-W33"

    worker = flows.WorkerRegister(
        worker_id="rolling-worker", display_name="Rolling worker", capabilities={}
    )
    flows.register_worker(worker)
    claimed = flows.claim_run(worker.worker_id)
    flows.update_run(
        worker.worker_id, claimed["run"]["id"],
        flows.WorkerProgress(status="failed", error="test failure"),
    )
    assert flows.get_flow(saved["id"])["start_week"] == "2026-W30"
    assert flows.get_flow(saved["id"])["last_success_at"] is None
    with database.get_db() as db:
        failure = db.execute(
            "SELECT status, flow_id FROM actions WHERE type='flow_failed'"
        ).fetchone()
    assert failure["status"] == "open"
    assert failure["flow_id"] == saved["id"]

    retry = flows.queue_run(saved["id"], _request())
    claimed = flows.claim_run(worker.worker_id)
    flows.update_run(
        worker.worker_id, claimed["run"]["id"],
        flows.WorkerProgress(status="succeeded"),
    )
    refreshed = flows.get_flow(saved["id"])
    assert refreshed["start_week"] == "2026-W33"
    assert refreshed["last_success_at"] is not None
    with database.get_db() as db:
        failure = db.execute(
            "SELECT status FROM actions WHERE type='flow_failed' AND flow_id=?",
            (saved["id"],),
        ).fetchone()
    assert failure["status"] == "resolved"


def test_week_prompt_can_be_supplied_by_range_instead_of_selection(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    _mark_discovered(report["id"])

    body = _flow(
        site["id"], report["id"], download_mode="single",
        selections={"data_configuration": "MENA - Global - Global"},
    )
    with database.get_db() as db:
        flows._validate_flow_selections(db, body)


def test_manual_catalog_report_can_be_used_before_discovery(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())

    body = _flow(
        site["id"], report["id"], download_mode="single",
        selections={"data_configuration": "MENA - Global - Global"},
    )
    with database.get_db() as db:
        flows._validate_flow_selections(db, body)

    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'report.source_kind === "discovered"' not in source


def test_asap_report_navigation_metadata_stays_local_and_enters_job(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"],
            report["id"],
            selections={"data_configuration": "MENA - Global - Global"},
        ),
        _request(),
    )
    queued = flows.queue_run(saved["id"], _request())

    assert report["automation"]["category_path"][-1] == "Installed Base (MENA)"
    assert queued["job"]["site"]["adapter"] == "asap_portal"
    assert queued["job"]["report"]["automation"]["report_tab"] == "Export Wizard (Detail)"


def test_asap_week_conversion_uses_portal_member_format():
    worker = __import__("app.flow_worker", fromlist=["_week_to_asap"])
    assert worker._week_to_asap("2026-W03") == "202603"
    with pytest.raises(RuntimeError, match="YYYY-Www"):
        worker._week_to_asap("202603")


def test_sql_handoff_requires_discovered_target(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    body = _flow(
        site["id"], report["id"], sql_handoff_enabled=True,
        sql_mode="append", sql_database="warehouse", sql_schema="reporting", sql_table="inflow",
    )
    with pytest.raises(HTTPException, match="latest SQL catalog"):
        flows.create_flow(body, _request())


def test_sql_handoff_target_is_persisted_without_executing_insert(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'inflow', CURRENT_TIMESTAMP, 0)"""
        )
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], sql_handoff_enabled=True,
            sql_mode="replace", sql_database="warehouse",
            sql_schema="reporting", sql_table="inflow",
        ),
        _request(),
    )
    job = flows.queue_run(saved["id"], _request())["job"]
    assert job["sql_handoff"] == {
        "enabled": True, "server": flows.normalize_server(flows.UPLOAD_PGHOST),
        "mode": "replace", "uppercase": False,
        "database": "warehouse", "schema": "reporting", "table": "inflow",
    }


def test_sql_managed_snapshot_allows_new_table_name_in_discovered_schema(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'existing_target', CURRENT_TIMESTAMP, 0)"""
        )

    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], sql_handoff_enabled=True,
            sql_mode="replace", sql_database="warehouse",
            sql_schema="reporting", sql_table="NEW Managed Target",
        ),
        _request(),
    )

    assert saved["sql_table"] == "new_managed_target"
    assert flows.queue_run(saved["id"], _request())["job"]["sql_handoff"] == {
        "enabled": True, "server": flows.normalize_server(flows.UPLOAD_PGHOST),
        "mode": "replace", "uppercase": False,
        "database": "warehouse", "schema": "reporting", "table": "new_managed_target",
    }


def test_sql_replace_keeps_the_exact_name_of_an_existing_discovered_table(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'Reporting Area', 'Import First and Second Activation', CURRENT_TIMESTAMP, 0)"""
        )

    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], sql_handoff_enabled=True,
            sql_mode="replace", sql_database="warehouse",
            sql_schema="Reporting Area", sql_table="Import First and Second Activation",
        ),
        _request(),
    )

    assert saved["sql_table"] == "Import First and Second Activation"


def test_sql_uppercase_option_is_persisted_and_reaches_the_job(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'inflow', CURRENT_TIMESTAMP, 0)"""
        )
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], sql_handoff_enabled=True,
            sql_mode="append", sql_uppercase=True, sql_database="warehouse",
            sql_schema="reporting", sql_table="inflow",
        ),
        _request(),
    )
    assert saved["sql_uppercase"] is True
    assert flows.queue_run(saved["id"], _request())["job"]["sql_handoff"] == {
        "enabled": True, "server": flows.normalize_server(flows.UPLOAD_PGHOST),
        "mode": "append", "uppercase": True,
        "database": "warehouse", "schema": "reporting", "table": "inflow",
    }


def test_sql_uppercase_option_is_cleared_when_handoff_is_disabled(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], sql_handoff_enabled=False, sql_uppercase=True),
        _request(),
    )
    assert saved["sql_uppercase"] is False


def test_flow_activation_is_separate_from_editor(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"], enabled=False), _request())
    active = flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=True), _request())
    assert active["enabled"] is True
    assert active["next_run_at"] is not None
    paused = flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=False), _request())
    assert paused["enabled"] is False
    assert paused["next_run_at"] is None


def test_flow_delete_requires_exact_name_and_paused_state(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())

    with pytest.raises(HTTPException, match="exact flow name"):
        flows.delete_flow(
            saved["id"], flows.FlowDeleteWrite(confirmation="Weekly report"), _request(),
        )
    with pytest.raises(HTTPException, match="Pause the flow"):
        flows.delete_flow(
            saved["id"], flows.FlowDeleteWrite(confirmation=saved["name"]), _request(),
        )
    flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=False), _request())
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_runs
                   (flow_id, trigger_type, status, requested_by, job_json)
               VALUES (?, 'manual', 'queued', 'Analyst', '{}')""",
            (saved["id"],),
        )
    with pytest.raises(HTTPException, match="still active"):
        flows.delete_flow(
            saved["id"], flows.FlowDeleteWrite(confirmation=saved["name"]), _request(),
        )
    assert flows.get_flow(saved["id"])["name"] == saved["name"]


def test_flow_delete_removes_database_history_but_keeps_pipeline_history(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], enabled=False), _request(),
    )
    with database.get_db() as db:
        run_id = db.execute(
            """INSERT INTO flow_runs
                   (flow_id, trigger_type, status, requested_by, job_json)
               VALUES (?, 'manual', 'succeeded', 'Analyst', '{}')""",
            (saved["id"],),
        ).lastrowid
        db.execute(
            """INSERT INTO flow_run_files
                   (run_id, file_path, filename, status)
               VALUES (?, 'C:\\Reports\\kept.csv', 'kept.csv', 'saved')""",
            (run_id,),
        )
        report_asset_id = db.execute(
            "INSERT INTO reports(name) VALUES ('Flow deletion history report')",
        ).lastrowid
        pipeline_id = db.execute(
            """INSERT INTO pipeline_runs
                   (report_id, status, stage, plan_hash, plan_json)
               VALUES (?, 'succeeded', 'complete', 'hash', '{}')""",
            (report_asset_id,),
        ).lastrowid
        step_id = db.execute(
            """INSERT INTO pipeline_run_steps
                   (run_id, step_type, sequence_no, entity_type, entity_id,
                    entity_name, status, flow_run_id)
               VALUES (?, 'flow', 1, 'flow', ?, ?, 'succeeded', ?)""",
            (pipeline_id, str(saved["id"]), saved["name"], run_id),
        ).lastrowid

    result = flows.delete_flow(
        saved["id"], flows.FlowDeleteWrite(confirmation=saved["name"]), _request(),
    )

    assert result == {
        "id": saved["id"], "name": saved["name"], "deleted": True,
        "deleted_runs": 1, "preserved_files": 1,
    }
    with database.get_db() as db:
        assert db.execute("SELECT 1 FROM flows WHERE id=?", (saved["id"],)).fetchone() is None
        assert db.execute("SELECT 1 FROM flow_runs WHERE id=?", (run_id,)).fetchone() is None
        step = db.execute(
            "SELECT entity_name, flow_run_id FROM pipeline_run_steps WHERE id=?", (step_id,),
        ).fetchone()
        assert step["entity_name"] == saved["name"]
        assert step["flow_run_id"] is None
        event = db.execute(
            """SELECT action, detail FROM event_log
               WHERE entity_type='flow' AND entity_id=? ORDER BY id DESC LIMIT 1""",
            (saved["id"],),
        ).fetchone()
        assert event["action"] == "deleted"
        assert "filesystem_files_preserved=true" in event["detail"]


def test_inactive_scheduled_flow_has_no_next_run(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"], enabled=False), _request())
    assert saved["schedule_type"] == "weekly"
    assert saved["next_run_at"] is None
    assert saved["freshness_rule"]["type"] == "weekly"
    assert saved["freshness_health"]["status"] == "paused"


def test_manual_flow_cannot_be_activated(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], enabled=False, schedule_type="manual",
            schedule_time=None, schedule_days=[],
        ),
        _request(),
    )
    assert saved["freshness_rule"] is None
    assert saved["freshness_health"]["status"] == "not_monitored"
    with pytest.raises(HTTPException, match="daily, weekly, or monthly"):
        flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=True), _request())


def test_unknown_and_invalid_filter_values_are_rejected(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with pytest.raises(HTTPException, match="Unknown report filter"):
        flows.create_flow(
            _flow(site["id"], report["id"], selections={"region": "Global", "secret": "x"}),
            _request(),
        )
    with pytest.raises(HTTPException, match="Invalid Region"):
        flows.create_flow(
            _flow(site["id"], report["id"], selections={"region": "Unknown"}),
            _request(),
        )


def test_flow_accepts_manual_report_metadata(flow_db):
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    assert saved["report_id"] == report["id"]


def test_scan_discovery_upserts_and_marks_missing_stale_without_deleting(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base MENA",
        name="Installed Base MENA",
        report_url="https://portal.example.test",
        ready_text="Export Wizard",
        automation={"category_path": ["Mobile", "Installed Base", "Installed Base MENA"]},
        filters=[flows.DiscoveredFilter(
            filter_key="week", label="Sell-out Week", control_label="Sell-out Week",
            control_type="week", options=["202632"], position=0,
        )],
    )
    with database.get_db() as db:
        first = flows._apply_discovery(db, site["id"], [report], "2026-08-12T10:00:00")
        second = flows._apply_discovery(db, site["id"], [], "2026-08-19T10:00:00")
        row = db.execute("SELECT enabled, stale FROM flow_reports").fetchone()
    assert first["report_count"] == 1
    assert second["report_count"] == 0
    assert (row["enabled"], row["stale"]) == (0, 1)


def test_scan_discovery_keeps_duplicate_leaf_names_from_different_menu_paths(flow_db):
    site = flows.create_site(_asap_site(), _request())
    reports = [
        flows.DiscoveredReport(
            discovery_key=f"Mobile > {group} > Inflow Outflow",
            name="Inflow Outflow",
            report_url="https://portal.example.test",
            automation={"category_path": ["Mobile", group, "Inflow Outflow"]},
        )
        for group in ("Operations", "Inventory")
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(db, site["id"], reports, "2026-08-12T10:00:00")
        names = [row["name"] for row in db.execute(
            "SELECT name FROM flow_reports ORDER BY name"
        ).fetchall()]
    assert result["report_count"] == 2
    assert names == [
        "Mobile > Inventory > Inflow Outflow",
        "Mobile > Operations > Inflow Outflow",
    ]


def test_targeted_report_scan_queues_one_path_without_deleting_other_catalog_entries(flow_db, monkeypatch):
    site = flows.create_site(_asap_site(), _request())
    report = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base (MENA)",
        name="Installed Base (MENA)",
        report_url="https://portal.example.test",
        automation={"category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"]},
    )
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [report], "2026-08-12T10:00:00")
        report_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "online"})
    queued = flows.queue_report_scan(report_id, _request())
    with database.get_db() as db:
        scan = db.execute("SELECT job_json FROM flow_catalog_scans WHERE id=?", (queued["id"],)).fetchone()
    job = json.loads(scan["job_json"])
    assert job["discovery"]["report_paths"] == [["Mobile", "Installed Base", "Installed Base (MENA)"]]
    assert job["target_report"] == {
        "id": report_id,
        "catalog_name": "Installed Base (MENA)",
        "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
        "favorite_bookmark_id": None,
    }
    assert job["discovery"]["delete_missing"] is False


def test_gscm_targeted_scan_queues_stable_bookmark_identity_and_legacy_path(flow_db, monkeypatch):
    site = flows.create_site(_gscm_site(), _request())
    bookmark = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast (2)"], "user-report-22",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [bookmark], "2026-08-27T09:00:00", complete=False,
        )
        report_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "online"})

    queued = flows.queue_report_scan(report_id, _request())

    with database.get_db() as db:
        scan = db.execute(
            "SELECT job_json FROM flow_catalog_scans WHERE id=?", (queued["id"],)
        ).fetchone()
    job = json.loads(scan["job_json"])
    assert job["target_report"] == {
        "id": report_id,
        "catalog_name": "Inventory Forecast (2)",
        "category_path": ["Public", "Planning", "Inventory Forecast (2)"],
        "favorite_bookmark_id": "user-report-22",
    }
    assert job["discovery"]["report_paths"] == [
        ["Public", "Planning", "Inventory Forecast (2)"]
    ]


def test_queue_scan_target_report_falls_back_to_report_name_without_path(flow_db):
    site, report = _seed_catalog()
    with database.get_db() as db:
        site_row = db.execute("SELECT * FROM flow_sites WHERE id=?", (site["id"],)).fetchone()
        report_row = db.execute(
            "SELECT * FROM flow_reports WHERE id=?", (report["id"],)
        ).fetchone()
        scan_id, _browser_mode = flows._queue_scan(
            db, site_row, "report", "Analyst", report_row,
        )
        job = json.loads(db.execute(
            "SELECT job_json FROM flow_catalog_scans WHERE id=?", (scan_id,)
        ).fetchone()["job_json"])

    assert job["target_report"] == {
        "id": report["id"],
        "catalog_name": "Weekly movement",
        "category_path": [],
        "favorite_bookmark_id": None,
    }
    assert job["discovery"]["report_paths"] == [[]]


def test_gscm_discovery_updates_existing_row_by_bookmark_id_before_path(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    original = _gscm_discovered_report(
        ["Private", "Legacy Folder", "Inventory Forecast (2)"], "stable-42",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [original], "2026-08-20T09:00:00", complete=False,
        )
        original_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
        flows._store_timings(
            db, [{"phase": "stable_id", "duration_ms": 900}],
            operation_type="catalog_scan", site_id=site["id"], report_id=original_id,
        )

        corrected = _gscm_discovered_report(
            ["Public", "Current Folder", "Inventory Forecast"], "stable-42",
        )
        result = flows._apply_discovery(
            db, site["id"], [corrected], "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            "SELECT id, name, discovery_key, automation_json FROM flow_reports"
        ).fetchall()
        timing = db.execute(
            "SELECT report_id FROM flow_operation_timings WHERE phase='stable_id'"
        ).fetchone()

    assert len(rows) == 1
    assert rows[0]["id"] == original_id
    assert rows[0]["name"] == "Public > Current Folder > Inventory Forecast"
    assert rows[0]["discovery_key"] == corrected.discovery_key
    assert json.loads(rows[0]["automation_json"])["favorite_bookmark_id"] == "stable-42"
    assert timing["report_id"] == original_id
    assert result["reset_report_count"] == 0
    assert result["preserved_referenced_report_count"] == 0


def test_gscm_unique_tabless_legacy_migration_preserves_referenced_report(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    legacy = _gscm_discovered_report(
        ["Private", "Planning", "Inventory Forecast"], None,
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [legacy], "2026-08-20T09:00:00", complete=False,
        )
        legacy_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]

    saved = flows.create_flow(
        _flow(
            site["id"], legacy_id, name="Legacy scope bookmark",
            selections={}, download_mode="single", period_strategy="none",
            start_week=None, end_week=None, file_format="xlsx",
            filename_template="legacy.xlsx", browser_mode="headed",
        ),
        _request(),
    )
    corrected = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast"], "stable-public-id",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [corrected], "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            """SELECT id, name, discovery_key, stale, enabled, automation_json
               FROM flow_reports WHERE site_id=?""",
            (site["id"],),
        ).fetchall()
        saved_flow = db.execute(
            "SELECT report_id FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()

    assert len(rows) == 1
    assert rows[0]["id"] == legacy_id
    assert saved_flow["report_id"] == legacy_id
    assert rows[0]["name"] == "Public > Planning > Inventory Forecast"
    assert rows[0]["discovery_key"] == "Public > Planning > Inventory Forecast"
    assert (rows[0]["stale"], rows[0]["enabled"]) == (0, 1)
    assert json.loads(rows[0]["automation_json"])["favorite_bookmark_id"] == (
        "stable-public-id"
    )
    assert result["preserved_referenced_report_count"] == 0


def test_gscm_tabless_legacy_migration_refuses_multiple_scope_candidates(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    legacy = [
        _gscm_discovered_report(
            ["Private", "Planning", "Inventory Forecast"], None,
        ),
        _gscm_discovered_report(
            ["Custom", "Planning", "Inventory Forecast"], None,
        ),
    ]
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], legacy, "2026-08-20T09:00:00", complete=False,
        )

    corrected = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast"], "stable-public-id",
    )
    with database.get_db() as db:
        with pytest.raises(RuntimeError, match="more than one compatible legacy bookmark"):
            flows._apply_discovery(
                db, site["id"], [corrected], "2026-08-27T09:00:00", complete=True,
            )
        rows = db.execute(
            "SELECT name, stale, enabled FROM flow_reports WHERE site_id=? ORDER BY id",
            (site["id"],),
        ).fetchall()

    assert [row["name"] for row in rows] == [
        "Private > Planning > Inventory Forecast",
        "Custom > Planning > Inventory Forecast",
    ]
    assert all((row["stale"], row["enabled"]) == (0, 1) for row in rows)


@pytest.mark.parametrize("private_first", [False, True])
def test_gscm_full_scope_reservation_precedes_tabless_fallback_in_both_orders(
    flow_db, private_first,
):
    site = flows.create_site(_gscm_site(), _request())
    legacy = _gscm_discovered_report(
        ["Private", "Planning", "Inventory Forecast"], None,
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [legacy], "2026-08-20T09:00:00", complete=False,
        )
        legacy_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]

    saved = flows.create_flow(
        _flow(
            site["id"], legacy_id, name=f"Legacy scope {private_first}",
            selections={}, download_mode="single", period_strategy="none",
            start_week=None, end_week=None, file_format="xlsx",
            filename_template="legacy-order.xlsx", browser_mode="headed",
        ),
        _request(),
    )
    first_tab = "Private" if private_first else "Public"
    second_tab = "Public" if private_first else "Private"
    first = _gscm_discovered_report(
        [first_tab, "Planning", "Inventory Forecast"], f"{first_tab.lower()}-id",
    )
    second = _gscm_discovered_report(
        [second_tab, "Planning", "Inventory Forecast (2)"],
        f"{second_tab.lower()}-id",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [first, second], "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            """SELECT id, stale, enabled, automation_json FROM flow_reports
               WHERE site_id=? ORDER BY id""",
            (site["id"],),
        ).fetchall()
        saved_flow = db.execute(
            "SELECT report_id FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()

    by_bookmark = {
        json.loads(row["automation_json"])["favorite_bookmark_id"]: row for row in rows
    }
    assert set(by_bookmark) == {"private-id", "public-id"}
    assert by_bookmark["private-id"]["id"] == legacy_id
    assert by_bookmark["public-id"]["id"] != legacy_id
    assert saved_flow["report_id"] == legacy_id
    assert all((row["stale"], row["enabled"]) == (0, 1) for row in rows)
    assert result["report_count"] == 2


def test_gscm_duplicate_bookmark_ids_prefer_referenced_row_and_rewire_all_flows(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    paths = [
        ["Private", "Old", "Inventory Forecast"],
        ["Public", "Saved", "Inventory Forecast (2)"],
        ["Custom", "Duplicate", "Inventory Forecast (3)"],
    ]
    report_ids = []
    with database.get_db() as db:
        for index, path in enumerate(paths):
            report = _gscm_discovered_report(path, "duplicate-stable-id")
            cursor = db.execute(
                """INSERT INTO flow_reports
                   (site_id, name, report_url, download_text, automation_json,
                    discovery_key, source_kind, last_seen_at, stale, enabled,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?, 0, 1, ?, ?)""",
                (
                    site["id"], " > ".join(path), report.report_url,
                    report.download_text, json.dumps(report.automation),
                    report.discovery_key, f"2026-08-{20 + index}T09:00:00",
                    f"2026-08-{20 + index}T09:00:00",
                    f"2026-08-{20 + index}T09:00:00",
                ),
            )
            report_ids.append(cursor.lastrowid)

    flow_options = {
        "selections": {}, "download_mode": "single", "period_strategy": "none",
        "start_week": None, "end_week": None, "file_format": "xlsx",
        "filename_template": "bookmark.xlsx", "browser_mode": "headed",
    }
    first_flow = flows.create_flow(
        _flow(site["id"], report_ids[1], name="Referenced bookmark", **flow_options),
        _request(),
    )
    second_flow = flows.create_flow(
        _flow(site["id"], report_ids[2], name="Duplicate bookmark", **flow_options),
        _request(),
    )

    incoming = _gscm_discovered_report(
        ["Public", "Canonical", "Inventory Forecast"], "duplicate-stable-id",
    )
    with database.get_db() as db:
        flows._store_timings(
            db, [{"phase": "total", "duration_ms": 1250}],
            operation_type="catalog_scan", site_id=site["id"], report_id=report_ids[2],
        )
        result = flows._apply_discovery(
            db, site["id"], [incoming], "2026-08-27T09:00:00", complete=True,
        )
        reports = db.execute("SELECT id, name FROM flow_reports ORDER BY id").fetchall()
        saved_flows = db.execute(
            "SELECT id, report_id FROM flows WHERE id IN (?, ?) ORDER BY id",
            (first_flow["id"], second_flow["id"]),
        ).fetchall()
        timing = db.execute(
            "SELECT report_id FROM flow_operation_timings WHERE phase='total'"
        ).fetchone()

    canonical_id = report_ids[1]
    assert [(row["id"], row["name"]) for row in reports] == [
        (canonical_id, "Public > Canonical > Inventory Forecast")
    ]
    assert {row["report_id"] for row in saved_flows} == {canonical_id}
    assert timing["report_id"] == canonical_id
    assert result["reset_report_count"] == 0
    assert result["preserved_referenced_report_count"] == 0


def test_gscm_complete_snapshot_cleans_only_missing_unreferenced_rows(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    current = _gscm_discovered_report(
        ["Public", "Planning", "Current Bookmark"], "current-id",
    )
    missing = _gscm_discovered_report(
        ["Private", "Planning", "Missing Bookmark"], "missing-id",
    )
    referenced_missing = _gscm_discovered_report(
        ["Custom", "Planning", "Referenced Missing Bookmark"], "referenced-id",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [current, missing, referenced_missing],
            "2026-08-20T09:00:00", complete=False,
        )
        ids = {
            json.loads(row["automation_json"])["favorite_bookmark_id"]: row["id"]
            for row in db.execute(
                "SELECT id, automation_json FROM flow_reports WHERE site_id=?", (site["id"],)
            ).fetchall()
        }
        flows._store_timings(
            db,
            [
                {"phase": "current", "duration_ms": 1000},
                {"phase": "missing", "duration_ms": 1100, "report_id": ids["missing-id"]},
            ],
            operation_type="catalog_scan", site_id=site["id"], report_id=ids["current-id"],
        )

    saved = flows.create_flow(
        _flow(
            site["id"], ids["referenced-id"], name="Referenced missing bookmark",
            selections={}, download_mode="single", period_strategy="none",
            start_week=None, end_week=None, file_format="xlsx",
            filename_template="bookmark.xlsx", browser_mode="headed",
        ),
        _request(),
    )
    refreshed = _gscm_discovered_report(
        ["Public", "Planning", "Current Bookmark Renamed"], "current-id",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [refreshed], "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            "SELECT id, stale, enabled, automation_json FROM flow_reports WHERE site_id=?",
            (site["id"],),
        ).fetchall()
        states = {
            json.loads(row["automation_json"])["favorite_bookmark_id"]: row
            for row in rows
        }
        timings = {
            row["phase"]: row["report_id"]
            for row in db.execute(
                "SELECT phase, report_id FROM flow_operation_timings WHERE site_id=?",
                (site["id"],),
            ).fetchall()
        }
        saved_flow = db.execute(
            "SELECT report_id FROM flows WHERE id=?", (saved["id"],)
        ).fetchone()

    assert set(states) == {"current-id", "referenced-id"}
    assert states["current-id"]["id"] == ids["current-id"]
    assert (states["current-id"]["stale"], states["current-id"]["enabled"]) == (0, 1)
    assert states["referenced-id"]["id"] == ids["referenced-id"]
    assert (states["referenced-id"]["stale"], states["referenced-id"]["enabled"]) == (1, 0)
    assert saved_flow["report_id"] == ids["referenced-id"]
    assert timings == {"current": ids["current-id"], "missing": None}
    assert result["reset_report_count"] == 1
    assert result["preserved_referenced_report_count"] == 1


def test_gscm_legacy_number_suffix_merges_without_creating_duplicate(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    legacy = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast (2)"], None,
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [legacy], "2026-08-20T09:00:00", complete=False,
        )
        legacy_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]

        current = _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast"], "newly-visible-id",
        )
        flows._apply_discovery(
            db, site["id"], [current], "2026-08-27T09:00:00", complete=False,
        )
        rows = db.execute(
            "SELECT id, name, automation_json FROM flow_reports"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["id"] == legacy_id
    assert rows[0]["name"] == "Public > Planning > Inventory Forecast"
    assert json.loads(rows[0]["automation_json"])["favorite_bookmark_id"] == "newly-visible-id"


def test_gscm_no_id_rows_in_one_batch_are_never_legacy_matches_for_each_other(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    reports = [
        _gscm_discovered_report(["Public", "Planning", "Inventory Forecast"], None),
        _gscm_discovered_report(["Public", "Planning", "Inventory Forecast (2)"], None),
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], reports, "2026-08-27T09:00:00", complete=True,
        )
        names = [row["name"] for row in db.execute(
            "SELECT name FROM flow_reports WHERE site_id=? ORDER BY id", (site["id"],)
        ).fetchall()]

    assert result["report_count"] == 2
    assert names == [
        "Public > Planning > Inventory Forecast",
        "Public > Planning > Inventory Forecast (2)",
    ]


def test_gscm_literal_number_suffix_is_not_treated_as_generated_catalog_copy(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    original = _gscm_discovered_report(
        ["Public", "Planning", "Budget"], None, favorite_name="Budget",
    )
    literal = _gscm_discovered_report(
        ["Public", "Planning", "Budget (2)"], None, favorite_name="Budget (2)",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [original, literal], "2026-08-27T09:00:00", complete=True,
        )
        names = [row["name"] for row in db.execute(
            "SELECT name FROM flow_reports WHERE site_id=? ORDER BY id", (site["id"],)
        ).fetchall()]

    assert result["report_count"] == 2
    assert names == [
        "Public > Planning > Budget",
        "Public > Planning > Budget (2)",
    ]


def test_gscm_new_stable_id_does_not_migrate_text_equal_synthetic_suffix(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    synthetic = _gscm_discovered_report(
        ["Public", "Planning", "Budget (2)"], None, favorite_name="Budget",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [synthetic], "2026-08-20T09:00:00", complete=False,
        )
        synthetic_id = db.execute(
            "SELECT id FROM flow_reports WHERE site_id=?", (site["id"],)
        ).fetchone()["id"]

    literal = _gscm_discovered_report(
        ["Public", "Planning", "Budget (2)"], "literal-stable-id",
        favorite_name="Budget (2)",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [literal], "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            "SELECT id, name, automation_json FROM flow_reports WHERE site_id=?",
            (site["id"],),
        ).fetchall()

    assert result["report_count"] == 1
    assert result["reset_report_count"] == 1
    assert len(rows) == 1
    assert rows[0]["id"] != synthetic_id
    assert rows[0]["name"] == "Public > Planning > Budget (2)"
    automation = json.loads(rows[0]["automation_json"])
    assert automation["favorite_name"] == "Budget (2)"
    assert automation["favorite_bookmark_id"] == "literal-stable-id"


def test_gscm_planner_does_not_treat_two_null_discovery_keys_as_equal():
    candidates = []
    for report_id, leaf in [(1, "Alpha"), (2, "Beta")]:
        candidates.append({
            "id": report_id,
            "name": f"Public > Planning > {leaf}",
            "discovery_key": None,
            "automation": {
                "kind": "gscm_favorite",
                "category_path": ["Public", "Planning", leaf],
                "favorite_name": leaf,
            },
            "source_kind": "discovered",
            "referenced": False,
        })
    incoming = _gscm_discovered_report(
        ["Public", "Planning", "Alpha"], None,
    ).model_copy(update={"discovery_key": None})

    plans, duplicate_groups = flows._plan_gscm_existing_reports(
        candidates, [(incoming, "Public > Planning > Alpha")],
    )

    assert plans[0]["id"] == 1
    assert duplicate_groups == {}


def test_gscm_incomplete_subset_preserves_stable_catalog_assignments(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    full_snapshot = [
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast"], "bookmark-a",
        ),
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast (2)"], "bookmark-b",
        ),
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast (3)"], "bookmark-c",
        ),
    ]
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], full_snapshot, "2026-08-20T09:00:00", complete=True,
        )

    # The incomplete a/c subset renumbers c to ``(2)`` locally. That subset
    # assignment must not replace c's authoritative pre-scan ``(3)`` slot.
    incomplete = [
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast"], "bookmark-a",
        ),
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast (2)"], "bookmark-c",
        ),
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], incomplete, "2026-08-27T09:00:00", complete=False,
        )
        rows = db.execute(
            """SELECT name, discovery_key, automation_json FROM flow_reports
               WHERE site_id=? ORDER BY id""",
            (site["id"],),
        ).fetchall()

    by_bookmark = {
        json.loads(row["automation_json"])["favorite_bookmark_id"]: row for row in rows
    }
    expected = {
        "bookmark-a": "Public > Planning > Inventory Forecast",
        "bookmark-b": "Public > Planning > Inventory Forecast (2)",
        "bookmark-c": "Public > Planning > Inventory Forecast (3)",
    }
    assert set(by_bookmark) == set(expected)
    for bookmark_id, catalog_name in expected.items():
        row = by_bookmark[bookmark_id]
        assert row["name"] == catalog_name
        assert row["discovery_key"] == catalog_name
        assert json.loads(row["automation_json"])["category_path"] == catalog_name.split(" > ")
    assert result["discovery_keys"] == [expected["bookmark-a"], expected["bookmark-c"]]


def test_gscm_incomplete_stable_id_applies_unoccupied_path_correction(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    prior = _gscm_discovered_report(
        ["Private", "Old Folder", "Inventory Forecast (2)"], "bookmark-a",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [prior], "2026-08-20T09:00:00", complete=True,
        )
        report_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]

    corrected = _gscm_discovered_report(
        ["Public", "Current Folder", "Inventory Forecast"], "bookmark-a",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [corrected], "2026-08-27T09:00:00", complete=False,
        )
        row = db.execute(
            "SELECT id, name, discovery_key, automation_json FROM flow_reports"
        ).fetchone()

    corrected_name = "Public > Current Folder > Inventory Forecast"
    assert row["id"] == report_id
    assert row["name"] == corrected_name
    assert row["discovery_key"] == corrected_name
    assert json.loads(row["automation_json"])["category_path"] == corrected_name.split(" > ")
    assert result["discovery_keys"] == [corrected_name]


def test_gscm_incomplete_new_id_allocates_around_omitted_catalog_row(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    prior = [
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast"], "bookmark-a",
        ),
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast (2)"], "bookmark-b",
        ),
    ]
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], prior, "2026-08-20T09:00:00", complete=True,
        )

    incomplete = [
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast"], "bookmark-a",
        ),
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast (2)"], "bookmark-new",
        ),
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], incomplete, "2026-08-27T09:00:00", complete=False,
        )
        rows = db.execute(
            """SELECT name, discovery_key, automation_json FROM flow_reports
               WHERE site_id=? ORDER BY id""",
            (site["id"],),
        ).fetchall()

    by_bookmark = {
        json.loads(row["automation_json"])["favorite_bookmark_id"]: row for row in rows
    }
    allocated = "Public > Planning > Inventory Forecast (3)"
    assert by_bookmark["bookmark-b"]["name"] == "Public > Planning > Inventory Forecast (2)"
    assert by_bookmark["bookmark-new"]["name"] == allocated
    assert by_bookmark["bookmark-new"]["discovery_key"] == allocated
    assert json.loads(by_bookmark["bookmark-new"]["automation_json"])["category_path"] == (
        allocated.split(" > ")
    )
    assert result["discovery_keys"] == [
        "Public > Planning > Inventory Forecast", allocated,
    ]


def test_gscm_no_id_synthetic_suffix_does_not_bind_literal_text_owner(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    synthetic = _gscm_discovered_report(
        ["Public", "Planning", "Budget (3)"], "synthetic-id", favorite_name="Budget",
    )
    literal = _gscm_discovered_report(
        ["Public", "Planning", "Budget (2)"], "literal-id", favorite_name="Budget (2)",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [synthetic, literal], "2026-08-20T09:00:00", complete=True,
        )
        synthetic_id = db.execute(
            "SELECT id FROM flow_reports WHERE discovery_key=?",
            ("Public > Planning > Budget (3)",),
        ).fetchone()["id"]

    saved = flows.create_flow(
        _flow(
            site["id"], synthetic_id, name="Synthetic budget bookmark",
            selections={}, download_mode="single", period_strategy="none",
            start_week=None, end_week=None, file_format="xlsx",
            filename_template="budget.xlsx", browser_mode="headed",
        ),
        _request(),
    )
    incoming = _gscm_discovered_report(
        ["Public", "Planning", "Budget (2)"], None, favorite_name="Budget",
    )
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], [incoming], "2026-08-27T09:00:00", complete=False,
        )
        rows = db.execute(
            """SELECT id, name, discovery_key, automation_json, last_seen_at
               FROM flow_reports WHERE site_id=? ORDER BY id""",
            (site["id"],),
        ).fetchall()
        saved_flow = db.execute(
            "SELECT report_id FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()

    by_bookmark = {
        json.loads(row["automation_json"])["favorite_bookmark_id"]: row for row in rows
    }
    assert saved_flow["report_id"] == synthetic_id
    assert by_bookmark["synthetic-id"]["id"] == synthetic_id
    assert by_bookmark["synthetic-id"]["name"] == "Public > Planning > Budget (3)"
    assert by_bookmark["synthetic-id"]["last_seen_at"] == "2026-08-27T09:00:00"
    assert by_bookmark["literal-id"]["name"] == "Public > Planning > Budget (2)"
    assert by_bookmark["literal-id"]["last_seen_at"] == "2026-08-20T09:00:00"
    assert result["discovery_keys"] == ["Public > Planning > Budget (3)"]


def test_gscm_complete_no_id_synthetic_rows_do_not_repoint_sole_literal_owner(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    literal = _gscm_discovered_report(
        ["Public", "Planning", "Budget (2)"], "literal-id", favorite_name="Budget (2)",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [literal], "2026-08-20T09:00:00", complete=True,
        )
        literal_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]

    saved = flows.create_flow(
        _flow(
            site["id"], literal_id, name="Literal budget bookmark",
            selections={}, download_mode="single", period_strategy="none",
            start_week=None, end_week=None, file_format="xlsx",
            filename_template="literal-budget.xlsx", browser_mode="headed",
        ),
        _request(),
    )
    incoming = [
        _gscm_discovered_report(
            ["Public", "Planning", "Budget"], None, favorite_name="Budget",
        ),
        _gscm_discovered_report(
            ["Public", "Planning", "Budget (2)"], None, favorite_name="Budget",
        ),
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], incoming, "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            """SELECT id, name, discovery_key, stale, enabled, automation_json
               FROM flow_reports WHERE site_id=? ORDER BY id""",
            (site["id"],),
        ).fetchall()
        saved_flow = db.execute(
            "SELECT report_id FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()

    tombstone = next(row for row in rows if row["id"] == literal_id)
    active = [row for row in rows if row["id"] != literal_id]
    assert saved_flow["report_id"] == literal_id
    assert (tombstone["stale"], tombstone["enabled"]) == (1, 0)
    assert "missing bookmark" in tombstone["name"]
    assert tombstone["discovery_key"] is None
    assert json.loads(tombstone["automation_json"])["favorite_name"] == "Budget (2)"
    assert [row["name"] for row in active] == [
        "Public > Planning > Budget", "Public > Planning > Budget (2)",
    ]
    assert all(json.loads(row["automation_json"])["favorite_bookmark_id"] is None for row in active)
    assert result["preserved_referenced_report_count"] == 1


def test_gscm_rejects_duplicate_nonblank_bookmark_id_in_one_batch(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    reports = [
        _gscm_discovered_report(
            ["Public", "Planning", "Inventory Forecast"], "duplicate-id",
        ),
        _gscm_discovered_report(
            ["Private", "Planning", "Supply Forecast"], "duplicate-id",
        ),
    ]
    with database.get_db() as db:
        with pytest.raises(RuntimeError, match="same favorite_bookmark_id more than once"):
            flows._apply_discovery(
                db, site["id"], reports, "2026-08-27T09:00:00", complete=False,
            )
        count = db.execute(
            "SELECT COUNT(*) AS count FROM flow_reports WHERE site_id=?", (site["id"],)
        ).fetchone()["count"]

    assert count == 0


@pytest.mark.parametrize("stable_first", [False, True])
def test_gscm_stable_legacy_migration_is_ambiguous_in_both_input_orders(
    flow_db, stable_first,
):
    site = flows.create_site(_gscm_site(), _request())
    legacy = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast"], None,
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [legacy], "2026-08-20T09:00:00", complete=False,
        )

    no_id = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast"], None,
    )
    stable = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast (2)"], "new-stable-id",
    )
    reports = [stable, no_id] if stable_first else [no_id, stable]
    with database.get_db() as db:
        with pytest.raises(RuntimeError, match="more than one compatible legacy bookmark"):
            flows._apply_discovery(
                db, site["id"], reports, "2026-08-27T09:00:00", complete=False,
            )
        rows = db.execute(
            "SELECT automation_json FROM flow_reports WHERE site_id=?", (site["id"],)
        ).fetchall()

    assert len(rows) == 1
    assert json.loads(rows[0]["automation_json"])["favorite_bookmark_id"] is None


def test_gscm_stable_id_migration_refuses_ambiguous_legacy_suffix_rows(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    legacy = [
        _gscm_discovered_report(["Public", "Planning", "Inventory Forecast"], None),
        _gscm_discovered_report(["Public", "Planning", "Inventory Forecast (2)"], None),
    ]
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], legacy, "2026-08-20T09:00:00", complete=False,
        )

    migration = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast (2)"], "new-stable-id",
    )
    with database.get_db() as db:
        with pytest.raises(RuntimeError, match="more than one compatible legacy bookmark"):
            flows._apply_discovery(
                db, site["id"], [migration], "2026-08-27T09:00:00", complete=True,
            )
        rows = db.execute(
            "SELECT name, automation_json FROM flow_reports WHERE site_id=? ORDER BY id",
            (site["id"],),
        ).fetchall()

    assert [row["name"] for row in rows] == [
        "Public > Planning > Inventory Forecast",
        "Public > Planning > Inventory Forecast (2)",
    ]
    assert all(json.loads(row["automation_json"])["favorite_bookmark_id"] is None for row in rows)


def test_gscm_legacy_suffix_never_merges_different_stable_ids(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    original = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast"], "bookmark-one",
    )
    distinct = _gscm_discovered_report(
        ["Public", "Planning", "Inventory Forecast (2)"], "bookmark-two",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [original], "2026-08-20T09:00:00", complete=False,
        )
        flows._apply_discovery(
            db, site["id"], [distinct], "2026-08-27T09:00:00", complete=False,
        )
        rows = db.execute(
            "SELECT name, automation_json FROM flow_reports ORDER BY id"
        ).fetchall()

    assert [row["name"] for row in rows] == [
        "Public > Planning > Inventory Forecast",
        "Public > Planning > Inventory Forecast (2)",
    ]
    assert [json.loads(row["automation_json"])["favorite_bookmark_id"] for row in rows] == [
        "bookmark-one", "bookmark-two",
    ]


def test_gscm_complete_snapshot_swaps_suffix_assignments_without_unique_collision(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    initial = [
        _gscm_discovered_report(["Public", "Planning", "Inventory Forecast"], "bookmark-a"),
        _gscm_discovered_report(["Public", "Planning", "Inventory Forecast (2)"], "bookmark-b"),
    ]
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], initial, "2026-08-20T09:00:00", complete=True,
        )
        original_ids = {
            json.loads(row["automation_json"])["favorite_bookmark_id"]: row["id"]
            for row in db.execute(
                "SELECT id, automation_json FROM flow_reports WHERE site_id=?", (site["id"],)
            ).fetchall()
        }

        swapped = [
            _gscm_discovered_report(
                ["Public", "Planning", "Inventory Forecast"], "bookmark-b",
            ),
            _gscm_discovered_report(
                ["Public", "Planning", "Inventory Forecast (2)"], "bookmark-a",
            ),
        ]
        result = flows._apply_discovery(
            db, site["id"], swapped, "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            "SELECT id, name, automation_json FROM flow_reports WHERE site_id=? ORDER BY name",
            (site["id"],),
        ).fetchall()

    by_bookmark = {
        json.loads(row["automation_json"])["favorite_bookmark_id"]: row
        for row in rows
    }
    assert by_bookmark["bookmark-a"]["id"] == original_ids["bookmark-a"]
    assert by_bookmark["bookmark-a"]["name"].endswith("Inventory Forecast (2)")
    assert by_bookmark["bookmark-b"]["id"] == original_ids["bookmark-b"]
    assert by_bookmark["bookmark-b"]["name"].endswith("Inventory Forecast")
    assert result["reset_report_count"] == 0


def test_gscm_complete_snapshot_replaces_reused_paths_and_preserves_referenced_tombstone(flow_db):
    site = flows.create_site(_gscm_site(), _request())
    old_referenced = _gscm_discovered_report(
        ["Public", "Planning", "Shared Path"], "old-referenced-id",
    )
    old_disposable = _gscm_discovered_report(
        ["Private", "Planning", "Reusable Path"], "old-disposable-id",
    )
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [old_referenced, old_disposable],
            "2026-08-20T09:00:00", complete=False,
        )
        old_ids = {
            json.loads(row["automation_json"])["favorite_bookmark_id"]: row["id"]
            for row in db.execute(
                "SELECT id, automation_json FROM flow_reports WHERE site_id=?", (site["id"],)
            ).fetchall()
        }
        flows._store_timings(
            db, [{"phase": "replaced", "duration_ms": 700}],
            operation_type="catalog_scan", site_id=site["id"],
            report_id=old_ids["old-disposable-id"],
        )

    saved = flows.create_flow(
        _flow(
            site["id"], old_ids["old-referenced-id"], name="Missing referenced bookmark",
            selections={}, download_mode="single", period_strategy="none",
            start_week=None, end_week=None, file_format="xlsx",
            filename_template="bookmark.xlsx", browser_mode="headed",
        ),
        _request(),
    )
    replacements = [
        _gscm_discovered_report(["Public", "Planning", "Shared Path"], "new-referenced-id"),
        _gscm_discovered_report(["Private", "Planning", "Reusable Path"], "new-disposable-id"),
    ]
    with database.get_db() as db:
        result = flows._apply_discovery(
            db, site["id"], replacements, "2026-08-27T09:00:00", complete=True,
        )
        rows = db.execute(
            """SELECT id, name, discovery_key, stale, enabled, automation_json
               FROM flow_reports WHERE site_id=? ORDER BY id""",
            (site["id"],),
        ).fetchall()
        by_bookmark = {
            json.loads(row["automation_json"])["favorite_bookmark_id"]: row
            for row in rows
        }
        saved_flow = db.execute(
            "SELECT report_id FROM flows WHERE id=?", (saved["id"],)
        ).fetchone()
        timing = db.execute(
            "SELECT report_id FROM flow_operation_timings WHERE phase='replaced'"
        ).fetchone()

    assert set(by_bookmark) == {
        "old-referenced-id", "new-referenced-id", "new-disposable-id",
    }
    tombstone = by_bookmark["old-referenced-id"]
    assert tombstone["id"] == old_ids["old-referenced-id"]
    assert (tombstone["stale"], tombstone["enabled"]) == (1, 0)
    assert "missing bookmark" in tombstone["name"]
    assert tombstone["discovery_key"] is None
    assert saved_flow["report_id"] == tombstone["id"]
    assert by_bookmark["new-referenced-id"]["name"] == "Public > Planning > Shared Path"
    assert by_bookmark["new-disposable-id"]["name"] == "Private > Planning > Reusable Path"
    assert timing["report_id"] is None
    assert result["reset_report_count"] == 1
    assert result["preserved_referenced_report_count"] == 1


def test_targeted_manual_report_scan_queues_explicit_path_and_is_promoted_by_discovery(flow_db, monkeypatch):
    site = flows.create_site(_asap_site(), _request())
    manual = flows.create_report(_asap_report(site["id"]), _request())
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "online"})

    queued = flows.queue_report_scan(manual["id"], _request())
    with database.get_db() as db:
        scan = db.execute(
            "SELECT job_json FROM flow_catalog_scans WHERE id=?", (queued["id"],)
        ).fetchone()
        job = json.loads(scan["job_json"])
        assert job["discovery"]["report_paths"] == [
            ["Mobile", "Installed Base", "Installed Base (MENA)"]
        ]

        discovered = flows.DiscoveredReport(
            discovery_key="Mobile > Installed Base > Installed Base (MENA)",
            name="Installed Base (MENA)",
            report_url="https://portal.example.test",
            automation={
                "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"]
            },
        )
        result = flows._apply_discovery(
            db, site["id"], [discovered], "2026-08-15T05:30:00", complete=False
        )
        reports = db.execute(
            "SELECT id, source_kind, discovery_key FROM flow_reports ORDER BY id"
        ).fetchall()

    assert result["report_count"] == 1
    assert len(reports) == 1
    assert reports[0]["id"] == manual["id"]
    assert reports[0]["source_kind"] == "discovered"
    assert reports[0]["discovery_key"] == "Mobile > Installed Base > Installed Base (MENA)"


def _mtracker_discovery(group="Z8 Command Center"):
    path = ["Advanced", group, "M Tracker"]
    return flows.DiscoveredReport(
        discovery_key=" > ".join(path),
        name="M Tracker",
        report_url="https://portal.example.test/portal/login/app",
        automation={"category_path": path},
        filters=[flows.DiscoveredFilter(
            filter_key="region", label="Region", control_label="Region",
            control_type="select", options=["Global"], position=0,
        )],
    )


def test_asap_unique_report_relocation_preserves_saved_flow_reference(flow_db):
    site = flows.create_site(_asap_site(), _request())
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [_mtracker_discovery()], "2026-08-12T10:00:00"
        )
        old_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
    saved = flows.create_flow(_flow(
        site["id"], old_id, download_mode="single", period_strategy="none",
        start_week=None, end_week=None,
    ), _request())

    corrected = _mtracker_discovery("AI Insights")
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [corrected], "2026-08-19T10:00:00")
        reports = db.execute(
            "SELECT id, discovery_key, stale, enabled FROM flow_reports ORDER BY id"
        ).fetchall()
        flow = db.execute("SELECT report_id FROM flows WHERE id=?", (saved["id"],)).fetchone()

    assert len(reports) == 1
    assert reports[0]["id"] == old_id
    assert reports[0]["discovery_key"] == corrected.discovery_key
    assert (reports[0]["stale"], reports[0]["enabled"]) == (0, 1)
    assert flow["report_id"] == old_id


def test_asap_relocation_repairs_flow_when_corrected_catalog_row_already_exists(flow_db):
    site = flows.create_site(_asap_site(), _request())
    with database.get_db() as db:
        flows._apply_discovery(
            db, site["id"], [_mtracker_discovery()], "2026-08-12T10:00:00"
        )
        old_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
    saved = flows.create_flow(_flow(
        site["id"], old_id, download_mode="single", period_strategy="none",
        start_week=None, end_week=None,
    ), _request())
    corrected = _mtracker_discovery("AI Insights")
    duplicate = flows.create_report(flows.ReportWrite(
        site_id=site["id"], name="Advanced > AI Insights > M Tracker",
        report_url=corrected.report_url, automation=corrected.automation,
        filters=[flows.FilterWrite(
            filter_key="region", label="Region", control_label="Region",
            control_type="select", options=["Global"],
        )],
    ), _request())
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET source_kind='discovered', discovery_key=? WHERE id=?",
            (corrected.discovery_key, duplicate["id"]),
        )
        flows._apply_discovery(db, site["id"], [corrected], "2026-08-19T10:00:00")
        rows = db.execute(
            "SELECT id, discovery_key, stale, enabled FROM flow_reports ORDER BY id"
        ).fetchall()
        flow = db.execute("SELECT report_id FROM flows WHERE id=?", (saved["id"],)).fetchone()

    assert flow["report_id"] == duplicate["id"]
    assert next(row for row in rows if row["id"] == duplicate["id"])["stale"] == 0
    assert next(row for row in rows if row["id"] == old_id)["stale"] == 1


def test_scan_estimate_uses_recorded_median(flow_db):
    site = flows.create_site(_asap_site(), _request())
    with database.get_db() as db:
        flows._store_timings(db, [{"phase": "total", "duration_ms": 80_000}], operation_type="catalog_scan", site_id=site["id"])
        flows._store_timings(db, [{"phase": "total", "duration_ms": 100_000}], operation_type="catalog_scan", site_id=site["id"])
    estimate = flows.operation_estimates(site_id=site["id"])["catalog_scan"]
    assert estimate["estimated_ms"] == 100_000
    assert estimate["sample_count"] == 2


def test_worker_claim_and_completion_records_artifact(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    worker = flows.WorkerRegister(
        worker_id="personal-session",
        display_name="Authenticated browser",
        capabilities={"adapters": ["web_export"]},
    )
    flows.register_worker(worker)
    claimed = flows.claim_run(worker.worker_id)
    assert claimed["run"]["id"] == queued["id"]

    flows.update_run(
        worker.worker_id,
        queued["id"],
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "Saved 1 CSV file."},
            artifacts=[{
                "period_key": "2026-W30",
                "file_path": r"C:\Reports\Downloads\weekly_2026-W30.csv",
                "filename": "weekly_2026-W30.csv",
                "file_size": 123,
                "checksum": "abc",
                "row_count": 5,
                "status": "saved",
            }],
            timings=[
                {"phase": "navigation", "duration_ms": 1200},
                {"phase": "total", "duration_ms": 2400, "item_count": 1},
            ],
        ),
    )
    with database.get_db() as db:
        run = db.execute("SELECT status FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()
        artifact = db.execute("SELECT * FROM flow_run_files WHERE run_id=?", (queued["id"],)).fetchone()
        timing = db.execute("SELECT duration_ms FROM flow_operation_timings WHERE run_id=? AND phase='total'", (queued["id"],)).fetchone()
    assert run["status"] == "succeeded"
    assert artifact["filename"] == "weekly_2026-W30.csv"
    assert timing["duration_ms"] == 2400


def test_an_oversized_progress_message_is_truncated_not_stored_verbatim(flow_db):
    # Before this cap a GSCM screen dump reached flow_run_events at 100,000
    # characters and the run-log page rendered it verbatim.
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    worker_id = "verbose-worker"
    flows.register_worker(flows.WorkerRegister(
        worker_id=worker_id,
        display_name="Verbose worker",
        capabilities={"headed": False},
    ))
    assert flows.claim_run(worker_id)["run"]["id"] == queued["id"]

    flows.update_run(
        worker_id,
        queued["id"],
        flows.WorkerProgress(
            status="failed",
            progress={"stage": "failed", "message": "boom " + "x" * 100_000},
            error="boom",
        ),
    )
    with database.get_db() as db:
        event = db.execute(
            "SELECT message FROM flow_run_events WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (queued["id"],),
        ).fetchone()
    assert len(event["message"]) <= flows.PROGRESS_MESSAGE_MAX_CHARS + 20
    assert event["message"].endswith("[truncated]")


def test_terminal_sql_retry_serializes_list_period_key_instead_of_returning_500(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    worker_id = "sql-retry-worker"
    flows.register_worker(flows.WorkerRegister(
        worker_id=worker_id,
        display_name="SQL retry worker",
        capabilities={"headed": False},
    ))
    assert flows.claim_run(worker_id)["run"]["id"] == queued["id"]

    result = flows.update_run(
        worker_id,
        queued["id"],
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "Committed 41,872 rows."},
            artifacts=[{
                "period_key": ["2026-W27"],
                "file_path": r"C:\Reports\Downloads\week_27.csv",
                "filename": "week_27.csv",
                "file_size": 57_667_776,
                "checksum": "abc",
                "row_count": 41_872,
                "status": "saved",
            }],
            timings=[{"phase": "sql_insertion", "duration_ms": 4400, "status": "succeeded"}],
        ),
    )

    assert result["status"] == "succeeded"
    with database.get_db() as db:
        run = db.execute("SELECT status FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()
        file = db.execute(
            "SELECT period_key, row_count FROM flow_run_files WHERE run_id=?",
            (queued["id"],),
        ).fetchone()
    assert run["status"] == "succeeded"
    assert file["period_key"] == "2026-W27"
    assert file["row_count"] == 41_872


def test_worker_restart_fails_active_sql_run_without_replaying_it(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode="headless": {"status": "launched", "mode": mode},
    )
    queued = flows.queue_run(saved["id"], _request())
    with database.get_db() as db:
        job = json.loads(db.execute(
            "SELECT job_json FROM flow_runs WHERE id=?", (queued["id"],)
        ).fetchone()["job_json"])
        job["sql_handoff"] = {
            "enabled": True, "mode": "replace", "database": "postgres",
            "schema": "reporting", "table": "target",
        }
        db.execute(
            "UPDATE flow_runs SET job_json=? WHERE id=?",
            (json.dumps(job), queued["id"]),
        )

    worker_id = "restart-safe-worker"
    flows.register_worker(flows.WorkerRegister(
        worker_id=worker_id, display_name="SQL worker",
        capabilities={"headed": False, "process_id": 101},
    ))
    assert flows.claim_run(worker_id)["run"]["id"] == queued["id"]
    flows.update_run(
        worker_id, queued["id"],
        flows.WorkerProgress(
            status="running",
            progress={"stage": "sql_copy", "message": "COPY started"},
        ),
    )

    registered = flows.register_worker(flows.WorkerRegister(
        worker_id=worker_id, display_name="SQL worker",
        capabilities={"headed": False, "process_id": 202},
    ))

    assert registered["interrupted_run_id"] == queued["id"]
    detail = flows.get_run(queued["id"])
    assert detail["status"] == "failed"
    assert detail["events"][-1]["stage"] == "worker_restarted"
    assert detail["events"][-1]["details"]["automatic_replay"] is False
    assert "inspect the target before retrying" in detail["error"]
    assert flows.claim_run(worker_id)["run"] is None


def test_safe_output_path_never_overwrites(tmp_path):
    existing = tmp_path / "report.csv"
    existing.write_text("original")
    output = __import__("app.flow_worker", fromlist=["_safe_output_path"])._safe_output_path(tmp_path, "report.csv")
    assert output.name == "report (2).csv"
    assert existing.read_text() == "original"


def test_worker_source_contains_no_delete_or_overwrite_operation():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    forbidden = [".unlink(", ".rmdir(", "shutil.rmtree", "os.remove(", "os.unlink("]
    assert all(token not in source for token in forbidden)
    assert "_safe_output_path" in source


def test_asap_execution_uses_rendered_ui_not_internal_response_url():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert "expect_response" not in source
    assert "frame = _asap_wait_for_results(page)" in source
    assert '"stage": "report_execution"' in source
    assert '"stage": "file_export"' in source
    assert '"button.report-export"' not in source


def test_every_export_file_is_retried_individually_before_the_run_fails():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert "EXPORT_TASK_ATTEMPTS = 3" in source
    assert "artifacts.append(_export_task_with_retry(" in source
    assert '"stage": "export_retry"' in source


def test_database_schema_has_no_flow_delete_policy(flow_db):
    with database.get_db() as db:
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(flows)").fetchall()}
        artifact_columns = {
            row[1] for row in db.execute("PRAGMA table_info(flow_run_files)").fetchall()
        }
    assert "delete_existing" not in job_columns
    assert "cleanup_policy" not in job_columns
    assert "output_mode" in job_columns
    assert {
        "storage_scope", "artifact_store_id", "published_file_path",
        "published_filename", "publish_status",
    } <= artifact_columns


def test_output_mode_migration_defaults_existing_flows_to_run_folders(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "legacy-output-mode.db"
    legacy_schema = database.SCHEMA.replace(
        "    output_mode         TEXT NOT NULL DEFAULT 'run_folders',\n", "",
    )
    with sqlite3.connect(db_path) as db:
        db.executescript(legacy_schema)
        site_id = db.execute(
            "INSERT INTO flow_sites(name, adapter) VALUES ('Legacy site', 'web_export')"
        ).lastrowid
        report_id = db.execute(
            """INSERT INTO flow_reports(site_id, name, report_url)
               VALUES (?, 'Legacy report', 'https://example.test/report')""",
            (site_id,),
        ).lastrowid
        db.execute(
            """INSERT INTO flows(name, site_id, report_id, target_folder, filename_template)
               VALUES ('Legacy Flow', ?, ?, 'C:\\Exports', 'stable.xlsx')""",
            (site_id, report_id),
        )
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    database.init_db()

    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT output_mode FROM flows WHERE name='Legacy Flow'"
        ).fetchone()
    assert row[0] == "run_folders"


def test_database_migrates_existing_flow_catalog_before_discovery_index(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    import sqlite3
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE flow_sites (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, adapter TEXT NOT NULL DEFAULT 'web_export', base_url TEXT, auth_url TEXT, enabled INTEGER DEFAULT 1, created_at DATETIME, updated_at DATETIME);
            CREATE TABLE flow_reports (id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, name TEXT NOT NULL, report_url TEXT NOT NULL, ready_text TEXT, open_export_text TEXT, download_text TEXT, automation_json TEXT NOT NULL DEFAULT '{}', notes TEXT, enabled INTEGER DEFAULT 1, created_at DATETIME, updated_at DATETIME, UNIQUE(site_id, name));
        """)
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(flow_reports)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(flow_reports)")}
    assert "discovery_key" in columns
    assert "idx_flow_reports_discovery_key" in indexes


def test_database_upgrades_legacy_asap_site_adapter(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-asap.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sites (name, adapter, auth_url)
               VALUES ('ASAP', 'web_export', 'https://asap.sec.samsung.net/portal/login')"""
        )

    database.init_db()

    with database.get_db() as db:
        row = db.execute("SELECT adapter FROM flow_sites WHERE name='ASAP'").fetchone()
    assert row["adapter"] == "asap_portal"


def test_windows_worker_launcher_uses_direct_script_for_embedded_python():
    source = Path(__file__).parents[1].joinpath("tools", "run_flow_worker.ps1").read_text()
    assert '(Join-Path $CodeDir "app\\flow_worker.py")' in source


def test_setup_installs_headless_flow_worker_service():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert '$FlowServiceName = "MXFlowsWorker"' in source
    assert "install $FlowServiceName $PyExe" in source
    assert "start $FlowServiceName" in source
    assert "--worker-id bi-desktop-headless" in source
    assert "--name BI-desktop-headless" in source
    assert "flow_worker_error.log" in source
    assert "$WorkerStartedAt = Get-Date" in source
    assert "$WorkerStartedAt.AddSeconds(-5)" in source
    assert '/api/flows/workers' in source
    assert "Flows worker registered with Metronome." in source


def test_setup_does_not_require_visible_asap_login_without_encrypted_credential():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert '$FlowCredentialPath = Join-Path $FlowProfile ".asap_credentials"' in source
    assert "(Test-Path $FlowCredentialPath)" in source
    assert "ASAP automatic sign-in is not configured yet." in source


def test_setup_bounds_stale_port_process_cleanup():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert "$KillProcess.WaitForExit(10000)" in source
    assert "Timed out waiting for taskkill" in source


def test_setup_merges_new_nested_files_without_purging_local_files():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    command = next(line for line in source.splitlines() if "& robocopy.exe" in line)
    assert "robocopy.exe $Inner.FullName $CodeDir /E" in command
    assert "/MIR" not in command
    assert "/PURGE" not in command


def test_worker_launcher_appends_diagnostic_log():
    source = Path(__file__).parents[1].joinpath("tools", "run_flow_worker.ps1").read_text()
    assert 'Start-Transcript -Path $WorkerLog -Append' in source
    assert '"flow_worker_{0}.log"' in source


def test_service_starts_headless_worker_service_instead_of_child_process(monkeypatch):
    commands = []
    monkeypatch.setattr(flow_local_runner.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(flow_local_runner.subprocess, 'run', lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0, stdout='', stderr=''))
    flow_local_runner.launch_local_worker('headless')
    assert commands == [['sc.exe', 'start', 'MXFlowsWorker']]
    source = Path(__file__).parents[1].joinpath("app", "flow_local_runner.py").read_text()
    assert '"System32", "schtasks.exe"' in source
    assert '[schtasks, "/Run", "/TN", task_path]' in source
    assert 'HEADED_TASK_PATH = rf"\\{HEADED_TASK_NAME}"' in source
    assert "subprocess.Popen" not in source


@pytest.mark.parametrize("browser_mode", ["headless", "headed"])
def test_stop_worker_targets_exact_registered_process(browser_mode, monkeypatch):
    calls = []
    monkeypatch.setattr(flow_local_runner.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        flow_local_runner.subprocess, "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(
            returncode=0, stdout="stopped", stderr="",
        ),
    )

    result = flow_local_runner.stop_local_worker(browser_mode, 4321)

    assert result == {"status": "stopped", "process_id": 4321, "message": "stopped"}
    assert calls[0][0] == ["taskkill.exe", "/PID", "4321", "/T", "/F"]


def test_setup_registers_on_demand_interactive_headed_worker():
    source = Path(__file__).parents[1].joinpath("setup.ps1").read_text()
    assert '$HeadedFlowTaskName = "Metronome_Flows_Headed"' in source
    assert "New-ScheduledTaskPrincipal" in source
    assert "-LogonType Interactive" in source
    assert 'New-ScheduledTaskAction -Execute $HeadedPython' in source
    assert "Join-Path $PyDir 'pythonw.exe'" in source
    assert "--worker-id $($VisibleSlot.WorkerId)" in source
    assert "foreach ($VisibleSlot in 1..$FlowMaxSlots" in source
    assert "-TaskName $VisibleSlot.TaskName" in source
    assert "--headed --idle-exit-seconds 60" in source
    assert ".metronome-flow-browser-headed" in source


def test_setup_stops_headed_worker_before_replacing_runtime_code():
    root = Path(__file__).parents[1]
    for filename in ("setup.ps1", "setup_ps1_clean.txt"):
        source = root.joinpath(filename).read_text()
        stop = "Stop-ScheduledTask -TaskName $HeadedFlowTaskName"
        assert stop in source
        assert source.index(stop) < source.index("Expand-Archive -Path $ZipPath")


def test_setup_downloads_update_before_stopping_running_services():
    root = Path(__file__).parents[1]
    for filename in ("setup.ps1", "setup_ps1_clean.txt"):
        source = root.joinpath(filename).read_text()
        download = "Invoke-WebRequestWithRetry -Uri $ZipUrl"
        headed_stop = "Stop-ScheduledTask -TaskName $HeadedFlowTaskName"
        service_stop = '& $NssmExe stop $ServiceName'
        worker_stop = '& $NssmExe stop $FlowServiceName'
        assert source.index(download) < source.index(headed_stop)
        assert source.index(download) < source.index(service_stop)
        assert source.index(download) < source.index(worker_stop)


def test_setup_waits_for_headless_worker_to_stop_before_replacing_code():
    root = Path(__file__).parents[1]
    for filename in ("setup.ps1", "setup_ps1_clean.txt"):
        source = root.joinpath(filename).read_text()
        wait = "$existingFlowService.WaitForStatus("
        assert wait in source
        assert "Flows worker did not stop within 30 seconds" in source
        assert source.index(wait) < source.index("Expand-Archive -Path $ZipPath")


def test_worker_retries_registration_and_prevents_duplicates():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert "for attempt in range(300)" in source
    assert "_exclusive_worker_lock" in source
    assert "Another Metronome flow worker is already running." in source


def test_catalog_monitor_reports_worker_and_auto_refreshes():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "No BI desktop worker online" in source
    assert "Waiting for BI desktop worker to start." in source
    assert "_flowScheduleCatalogMonitor" in source


def test_flow_list_does_not_require_builder_only_sql_controls():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'if (fields) fields.hidden = !enabled;' in source
    assert 'if ($("#flow-sql-fields")) updateSqlFields();' in source


def test_flow_builder_uses_discovered_week_dropdowns():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "function _flowDiscoveredWeeks" in source
    assert "function _flowIsoWeekMonday" in source
    assert "function _flowIsoWeekValue" in source
    assert "cursor.setUTCDate(cursor.getUTCDate() + 7)" in source
    assert '<select id="flow-start-week" required>' in source
    assert '<select id="flow-end-week" required>' in source


def test_setup_fails_closed_when_python_dependencies_cannot_install():
    root = Path(__file__).parents[1]
    for filename in ("setup.ps1", "setup_ps1_clean.txt"):
        source = root.joinpath(filename).read_text()
        assert "install --upgrade setuptools wheel -q" in source
        assert "install --no-build-isolation -r requirements.txt -q" in source
        assert "Python dependency installation failed with exit code" in source


def test_flow_ui_uses_list_activation_bundle_formats_and_expanded_logs():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    log_source = Path(__file__).parents[1].joinpath("app", "static", "flow_run_log.js").read_text()
    assert "flow-enabled-switch" in source
    assert "Enable scheduled execution" not in source
    assert "asap_download_type: isAsap ? downloadValue : null" in source
    assert "export_report_title: isAsap" in source
    assert "export_filter_details: isAsap" in source
    assert 'id="flow-file-format"' in source
    assert 'data-flow-export-view' in source
    assert 'id="flow-schedule-day"' in source
    assert "Expanded logs" in source
    assert "/flow-runs/${run.id}" in source
    assert 'id="flow-transform-enabled"' in source
    assert 'id="flow-transform-browse"' in source
    assert "script_results" in source
    assert "Retry SQL only" in log_source
    assert "/retry-sql" in log_source
    assert "The source will not open" in log_source
    assert "setTimeout(loadRun, 2000)" in log_source


def test_flow_ui_requires_typed_confirmation_before_delete():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'class="btn-sm btn-outline btn-danger-outline flow-delete"' in source
    assert "function _flowDeleteDialog(flow)" in source
    assert "input.value !== flow.name" in source
    assert "Permanently delete flow" in source
    assert "Downloaded files and transformation scripts will stay on disk." in source
    assert 'id="flow-delete-confirmation" autocomplete="off" spellcheck="false" aria-describedby="flow-delete-match">' in source
    assert "await apiPatch(`/api/flows/${flow.id}/enabled`, { enabled: false })" in source
    assert "apiDelete(`/api/flows/${flow.id}`, { confirmation: input.value })" in source


def test_run_progress_events_and_traceback_are_persisted(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_runs SET worker_id='test-worker', status='claimed' WHERE id=?",
            (queued["id"],),
        )
    flows.update_run(
        "test-worker", queued["id"],
        flows.WorkerProgress(
            status="failed", progress={"stage": "sql_insertion", "message": "Insert failed"},
            error="wrong columns", traceback="Traceback: example",
        ),
    )
    detail = flows.get_run(queued["id"])
    assert detail["events"][0]["stage"] == "sql_insertion"
    assert detail["events"][0]["traceback"] == "Traceback: example"


def test_due_scheduler_queues_once_and_advances_next_run(flow_db, monkeypatch):
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "launched", "mode": mode})
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    with database.get_db() as db:
        db.execute("UPDATE flows SET next_run_at='2020-01-01T08:00:00' WHERE id=?", (saved["id"],))

    first = flows.queue_due_flows()
    second = flows.queue_due_flows()

    assert first["count"] == 1
    assert second["count"] == 0
    with database.get_db() as db:
        row = db.execute("SELECT next_run_at FROM flows WHERE id=?", (saved["id"],)).fetchone()
    assert row["next_run_at"] > "2020-01-01T08:00:00"


def test_manual_run_launches_bi_desktop_worker(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    launched = []
    monkeypatch.setattr(
        flows,
        "launch_local_worker",
        lambda mode: launched.append(mode) or {"status": "launched", "mode": mode},
    )

    queued = flows.queue_run(saved["id"], _request())

    assert launched == ["headless"]
    assert queued["worker"] == {"status": "launched", "mode": "headless"}


def test_manual_runs_block_cross_flow_target_using_frozen_job_snapshot(
    flow_db, monkeypatch
):
    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode}
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'shared_target', CURRENT_TIMESTAMP, 0)"""
        )
    common = {
        "sql_handoff_enabled": True,
        "sql_mode": "append",
        "sql_database": "warehouse",
        "sql_schema": "reporting",
        "sql_table": "shared_target",
    }
    first = flows.create_flow(
        _flow(site["id"], report["id"], name="First writer", **common), _request()
    )
    second = flows.create_flow(
        _flow(site["id"], report["id"], name="Second writer", **common), _request()
    )
    first_run = flows.queue_run(first["id"], _request())

    # A running job keeps its original target even if the mutable Flow record
    # is later changed. Collision detection must use the frozen job_json.
    with database.get_db() as db:
        db.execute(
            """UPDATE flows SET sql_handoff_enabled=0, sql_table='different_target'
               WHERE id=?""",
            (first["id"],),
        )
    with pytest.raises(HTTPException, match="First writer"):
        flows.queue_run(second["id"], _request())

    with database.get_db() as db:
        runs = db.execute(
            "SELECT id, flow_id, status FROM flow_runs ORDER BY id"
        ).fetchall()
    assert [(row["id"], row["flow_id"], row["status"]) for row in runs] == [
        (first_run["id"], first["id"], "queued")
    ]


def test_scheduler_queues_only_one_flow_per_physical_sql_target(flow_db, monkeypatch):
    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode}
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'shared_target', CURRENT_TIMESTAMP, 0)"""
        )
    common = {
        "sql_handoff_enabled": True,
        "sql_mode": "append",
        "sql_database": "warehouse",
        "sql_schema": "reporting",
        "sql_table": "shared_target",
    }
    first = flows.create_flow(
        _flow(site["id"], report["id"], name="First schedule", **common), _request()
    )
    second = flows.create_flow(
        _flow(site["id"], report["id"], name="Second schedule", **common), _request()
    )
    with database.get_db() as db:
        db.execute(
            "UPDATE flows SET next_run_at='2020-01-01T08:00:00' WHERE id IN (?, ?)",
            (first["id"], second["id"]),
        )

    result = flows.queue_due_flows()

    assert result["count"] == 1
    with database.get_db() as db:
        active = db.execute(
            """SELECT flow_id FROM flow_runs
               WHERE status IN ('queued','claimed','running') ORDER BY id"""
        ).fetchall()
    assert [row["flow_id"] for row in active] == [first["id"]]


def test_terminal_run_can_retry_sql_without_browser_or_download(flow_db, tmp_path, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode})
    queued = flows.queue_run(saved["id"], _request())
    artifact = tmp_path / "saved.csv"
    artifact.write_text("value\n1\n", encoding="utf-8")
    source_job = queued["job"]
    source_job["sql_handoff"] = {
        "enabled": True, "mode": "replace", "database": "db",
        "schema": "reporting", "table": "target",
    }
    source_artifacts = [{
        "file_path": str(artifact), "filename": artifact.name, "row_count": 1,
        "file_size": artifact.stat().st_size, "status": "saved",
    }]
    with database.get_db() as db:
        db.execute(
            """UPDATE flow_runs SET status='failed', job_json=?, artifact_json=?,
               error='copy failed', finished_at='2026-08-14T10:00:00' WHERE id=?""",
            (json.dumps(source_job), json.dumps(source_artifacts), queued["id"]),
        )

    retried = flows.retry_run_sql(queued["id"], _request())

    assert retried["source_run_id"] == queued["id"]
    assert retried["worker"] == {"status": "launched", "mode": "headless"}
    assert retried["job"]["job_type"] == "sql_retry"
    assert retried["job"]["execution"]["browser_mode"] == "headless"
    assert retried["job"]["sql_retry"]["artifacts"] == source_artifacts
    assert retried["job"]["transformation"]["enabled"] is False
    with database.get_db() as db:
        row = db.execute("SELECT trigger_type, status FROM flow_runs WHERE id=?", (retried["id"],)).fetchone()
    assert dict(row) == {"trigger_type": "sql_retry", "status": "queued"}

    worker = flows.WorkerRegister(
        worker_id="sql-retry-worker", display_name="SQL retry worker", capabilities={},
    )
    flows.register_worker(worker)
    claimed = flows.claim_run(worker.worker_id)
    assert claimed["run"]["id"] == retried["id"]
    flows.update_run(
        worker.worker_id, retried["id"], flows.WorkerProgress(status="succeeded"),
    )
    with database.get_db() as db:
        execution_success = db.execute(
            "SELECT last_execution_success_at FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()["last_execution_success_at"]
    assert execution_success is None


def test_sql_retry_uses_saved_target_for_cross_flow_collision(
    flow_db, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode}
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'shared_target', CURRENT_TIMESTAMP, 0)"""
        )
    common = {
        "sql_handoff_enabled": True,
        "sql_mode": "append",
        "sql_database": "warehouse",
        "sql_schema": "reporting",
        "sql_table": "shared_target",
    }
    first = flows.create_flow(
        _flow(site["id"], report["id"], name="Retry writer", **common), _request()
    )
    second = flows.create_flow(
        _flow(site["id"], report["id"], name="Active writer", **common), _request()
    )
    first_run = flows.queue_run(first["id"], _request())
    artifact = tmp_path / "retry.csv"
    artifact.write_text("value\n1\n", encoding="utf-8")
    with database.get_db() as db:
        db.execute(
            """UPDATE flow_runs SET status='failed', artifact_json=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                json.dumps([{
                    "file_path": str(artifact), "filename": artifact.name,
                    "status": "saved",
                }]),
                first_run["id"],
            ),
        )
        # Retry SQL keeps the source run's target, not this mutable Flow target.
        db.execute(
            """UPDATE flows SET sql_handoff_enabled=0, sql_table='different_target'
               WHERE id=?""",
            (first["id"],),
        )
    flows.queue_run(second["id"], _request())

    with pytest.raises(HTTPException, match="Active writer"):
        flows.retry_run_sql(first_run["id"], _request())


def test_resume_blocks_another_flow_writing_current_physical_target(
    flow_db, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipelines, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", "warehouse.example.test")
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode}
    )
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'shared_target', CURRENT_TIMESTAMP, 0)"""
        )
    common = {
        "sql_handoff_enabled": True,
        "sql_mode": "append",
        "sql_database": "warehouse",
        "sql_schema": "reporting",
        "sql_table": "shared_target",
    }
    first = flows.create_flow(
        _flow(site["id"], report["id"], name="Resume writer", **common), _request()
    )
    second = flows.create_flow(
        _flow(site["id"], report["id"], name="Active writer", **common), _request()
    )
    first_run = flows.queue_run(first["id"], _request())
    artifact = tmp_path / "resume.csv"
    artifact.write_text("value\n1\n", encoding="utf-8")
    with database.get_db() as db:
        db.execute(
            """UPDATE flow_runs SET status='failed', artifact_json=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                json.dumps([{
                    "file_path": str(artifact), "filename": artifact.name,
                    "status": "saved", "period_key": "2026-W30",
                }]),
                first_run["id"],
            ),
        )
    flows.queue_run(second["id"], _request())

    with pytest.raises(HTTPException, match="Active writer"):
        flows.resume_run(first_run["id"], _request())


def test_sql_retry_rejects_missing_saved_artifact(flow_db, tmp_path, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode})
    queued = flows.queue_run(saved["id"], _request())
    source_job = queued["job"]
    source_job["sql_handoff"] = {
        "enabled": True, "mode": "append", "database": "db",
        "schema": "reporting", "table": "target",
    }
    missing = tmp_path / "missing.csv"
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_runs SET status='failed', job_json=?, artifact_json=?, finished_at=? WHERE id=?",
            (
                json.dumps(source_job),
                json.dumps([{"file_path": str(missing), "filename": missing.name, "status": "saved"}]),
                "2026-08-14T10:00:00", queued["id"],
            ),
        )

    with pytest.raises(HTTPException, match="no longer available"):
        flows.retry_run_sql(queued["id"], _request())


def test_headed_flow_is_routed_only_to_headed_worker(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    launched = []
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode: launched.append(mode) or {"status": "launched", "mode": mode},
    )
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headless", display_name="Background", capabilities={"headed": False},
    ))
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible", capabilities={"headed": True},
    ))

    assert queued["job"]["execution"]["browser_mode"] == "headed"
    assert queued["job"]["execution"]["worker_id"] == "bi-desktop-headed"
    assert launched == ["headed"]
    assert flows.claim_run("bi-desktop-headless")["run"] is None
    assert flows.claim_run("bi-desktop-headed")["run"]["id"] == queued["id"]


def test_queued_run_can_retry_worker_launch_without_duplicate(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    launches = []
    monkeypatch.setattr(
        flows, "launch_local_worker",
        lambda mode: launches.append(mode) or {"status": "starting", "mode": mode},
    )

    first = flows.queue_run(saved["id"], _request())
    second = flows.queue_run(saved["id"], _request())

    assert first["id"] == second["id"]
    assert first["resumed"] is False
    assert second["resumed"] is True
    assert launches == ["headed", "headed"]
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0] == 1


def test_stop_cancels_assigned_run_and_targets_reported_worker_pid(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    stopped = []
    monkeypatch.setattr(
        flows, "stop_local_worker",
        lambda mode, pid, **kwargs: stopped.append((mode, pid)) or {"status": "stopped", "process_id": pid},
    )

    result = flows.stop_run(saved["id"], _request())

    assert result["run_id"] == queued["id"]
    assert result["status"] == "cancelled"
    assert stopped == [("headed", 4321)]
    assert flows.list_runs(flow_id=saved["id"], limit=100)[0]["status"] == "cancelled"


def test_stop_cancels_queued_run_without_stopping_another_flows_worker(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    first_flow = flows.create_flow(
        _flow(site["id"], report["id"], name="First", browser_mode="headed"), _request()
    )
    second_flow = flows.create_flow(
        _flow(site["id"], report["id"], name="Second", browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    first_run = flows.queue_run(first_flow["id"], _request())
    second_run = flows.queue_run(second_flow["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    assert flows.claim_run("bi-desktop-headed")["run"]["id"] == first_run["id"]
    stopped = []
    monkeypatch.setattr(
        flows, "stop_local_worker",
        lambda mode, pid, **kwargs: stopped.append((mode, pid)) or {"status": "stopped"},
    )

    result = flows.stop_run(second_flow["id"], _request())

    assert result["run_id"] == second_run["id"]
    assert result["worker"]["status"] == "not_needed"
    assert stopped == []
    assert flows.get_run(first_run["id"])["status"] == "claimed"
    assert flows.get_run(second_run["id"])["status"] == "cancelled"
    with database.get_db() as db:
        worker = db.execute(
            "SELECT status, current_run_id FROM flow_workers WHERE worker_id='bi-desktop-headed'"
        ).fetchone()
    assert dict(worker) == {"status": "busy", "current_run_id": first_run["id"]}


def test_stop_cancels_assigned_headless_run(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headless"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headless", display_name="Background",
        capabilities={"headed": False, "process_id": 9876},
    ))
    flows.claim_run("bi-desktop-headless")
    stopped = []
    monkeypatch.setattr(
        flows, "stop_local_worker",
        lambda mode, pid, **kwargs: stopped.append((mode, pid)) or {"status": "stopped", "process_id": pid},
    )

    result = flows.stop_run(saved["id"], _request())

    assert result["run_id"] == queued["id"]
    assert result["status"] == "cancelled"
    assert stopped == [("headless", 9876)]


def test_stop_cancels_queued_catalog_scan_without_stopping_worker(flow_db, monkeypatch):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    monkeypatch.setattr(flows, "launch_local_worker", lambda *_a, **_k: {"status": "starting"})
    queued = flows.queue_report_scan(report["id"], _request())
    stopped = []
    monkeypatch.setattr(
        flows, "stop_local_worker",
        lambda mode, pid, **kwargs: stopped.append((mode, pid)) or {"status": "stopped"},
    )

    result = flows.stop_scan(queued["id"], _request())

    assert result["status"] == "cancelled"
    assert result["worker"]["status"] == "not_needed"
    assert stopped == []
    scan = next(item for item in flows.list_scans(limit=50) if item["id"] == queued["id"])
    assert scan["status"] == "cancelled"
    assert scan["job"]["target_report"] == {
        "id": report["id"],
        "catalog_name": "Installed Base (MENA)",
        "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
        "favorite_bookmark_id": None,
    }
    assert flows.list_scan_events(queued["id"], after_id=0, limit=400)["events"][-1]["stage"] == "cancelled"


def test_stop_cancels_running_catalog_scan_and_ignores_late_success(flow_db, monkeypatch):
    site = flows.create_site(_asap_site(), _request())
    report = flows.create_report(_asap_report(site["id"]), _request())
    monkeypatch.setattr(flows, "launch_local_worker", lambda *_a, **_k: {"status": "starting"})
    queued = flows.queue_report_scan(report["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="catalog-worker", display_name="Catalog worker",
        capabilities={"headed": False, "process_id": 2468},
    ))
    claimed = flows.claim_run("catalog-worker")
    assert claimed["scan"]["id"] == queued["id"]
    flows.update_scan(
        "catalog-worker", queued["id"],
        flows.ScanProgress(
            status="running", progress={"stage": "report_discovery", "message": "Scanning report."},
            complete=False,
        ),
    )
    stopped = []
    monkeypatch.setattr(
        flows, "stop_local_worker",
        lambda mode, pid, **kwargs: stopped.append((mode, pid)) or {"status": "stopped", "process_id": pid},
    )

    result = flows.stop_scan(queued["id"], _request())
    late = flows.update_scan(
        "catalog-worker", queued["id"],
        flows.ScanProgress(status="succeeded", reports=[], complete=True),
    )

    assert result["status"] == "cancelled"
    assert stopped == [("headless", 2468)]
    assert late == {"scan_id": queued["id"], "status": "cancelled", "ignored": True}
    scan = next(item for item in flows.list_scans(limit=50) if item["id"] == queued["id"])
    assert scan["status"] == "cancelled"
    with database.get_db() as db:
        worker = db.execute(
            "SELECT status, current_scan_id FROM flow_workers WHERE worker_id='catalog-worker'"
        ).fetchone()
    assert dict(worker) == {"status": "offline", "current_scan_id": None}


def test_asap_scraper_never_uses_control_modified_clicks():
    source = Path(flow_worker.__file__).read_text()
    assert 'modifiers=["Control"]' not in source
    assert 'keyboard.down("Control")' not in source
    assert 'keyboard.press("Control' not in source


def test_every_active_flow_renders_a_stop_button():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'data-flow-focus="flow-stop-${flow.id}" ${activeRun ? "" : "hidden"}>Stop</button>' in source
    assert 'activeRun.job?.execution?.browser_mode === "headed"' not in source
    index = Path(__file__).parents[1].joinpath("app", "static", "index.html").read_text()
    # The exact number is irrelevant; the ?v=<digits> shape is what the
    # server's mtime-based cache-buster rewrite in main.py matches on.
    assert re.search(r"/static/app\.js\?v=\d+", index)


def test_flow_builder_names_the_write_modes_in_plain_language():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert ">Append rows</option>" in source
    assert ">Replace all rows</option>" in source
    assert 'id="flow-sql-table" list="flow-sql-table-options"' in source
    # The mode truncates and reloads; it never drops the table, so the
    # wording must not promise or imply that it does.
    assert "Replace all rows deletes every row in the table" in source
    assert "The table itself is kept" in source
    assert "Managed snapshot" not in source
    assert "drop" not in source.casefold().split("flow-sql-mode")[1][:1200]

    log_source = Path(__file__).parents[1].joinpath("app", "static", "flow_run_log.js").read_text()
    assert "This will replace all rows in" in log_source
    assert "managed snapshot" not in log_source.casefold()
    assert "drop and recreate" not in log_source


def test_flow_builder_can_replicate_an_existing_flow():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'id="flow-replicate-source"' in source
    assert 'id="flow-replicate-apply"' in source
    # A replicated draft is a new flow: no id, no schedule armed, its own
    # name, and the form still creates instead of updating.
    assert "id: null, name: \"\", enabled: false, _replicated_from: sourceId," in source
    assert 'existing?.id ? "Save changes" : "Create flow"' in source
    log_html = Path(__file__).parents[1].joinpath("app", "static", "flow_run_log.html").read_text()
    assert '/static/flow_run_log.js?v=3' in log_html


def test_asap_region_triplet_select_is_named_data_configuration():
    from app.flow_worker import _normalize_asap_filter_label

    options = [
        "MENA - Global - Global",
        "Global - Global - MENA",
        "Global - Global - CIS",
    ]
    assert _normalize_asap_filter_label(options[1], "select", options) == "Data Configuration"
    assert _normalize_asap_filter_label(options[1], "select", [options[1]]) == "Data Configuration"
    assert _normalize_asap_filter_label("Region", "select", options) == "Region"


def test_asap_duplicate_filter_discovery_merges_partial_and_complete_options():
    from app.flow_worker import _merge_asap_filter_definition

    definitions = []
    _merge_asap_filter_definition(
        definitions,
        "MENA - Global - Global",
        "select",
        ["MENA - Global - Global", "Global - Global - CIS"],
    )
    _merge_asap_filter_definition(
        definitions,
        "Global - Global - MENA",
        "select",
        [
            "MENA - Global - Global",
            "Global - Global - MENA",
            "Global - Global - CIS",
        ],
    )

    assert len(definitions) == 1
    assert definitions[0]["label"] == "Data Configuration"
    assert definitions[0]["options"] == [
        "MENA - Global - Global",
        "Global - Global - CIS",
        "Global - Global - MENA",
    ]


def test_asap_filter_discovery_reads_hidden_native_selects():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    # The select sweep runs inside the DOM so Select2's hidden owning selects
    # are read with their full option lists; only selects whose entire
    # container is hidden (other report tabs) are excluded.
    assert 'document.querySelectorAll("select")' in source
    assert 'frame.locator("select:visible")' not in source
    assert "Array.from(select.options)" in source
    assert "wait_for_popup_options(control)" in source
    assert "time.monotonic() - stable_since >= 1.5" in source
    assert ".select2-results__option:visible" in source
    assert "li:visible" not in source
    assert "[class*=select2-result]:visible" not in source
    assert 'control.get_attribute("aria-controls")' in source


def test_asap_popup_discovery_prefers_combobox_owned_listbox():
    class Collection:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Popup:
        def is_visible(self):
            return True

    popup = Popup()

    class Control:
        def get_attribute(self, name):
            return "popup-results" if name == "aria-controls" else None

    class Frame:
        def __init__(self):
            self.selectors = []

        def locator(self, selector):
            self.selectors.append(selector)
            assert selector == '[id="popup-results"]'
            return Collection([popup])

    frame = Frame()
    assert flow_worker._asap_owned_popup_roots(frame, Control()) == [popup]
    assert frame.selectors == ['[id="popup-results"]']


def test_hidden_select2_control_is_selected_through_owning_native_select():
    from app.flow_worker import _select_native_options_by_text

    class Options:
        def __init__(self, labels):
            self.labels = labels

        def all_text_contents(self):
            return self.labels

    class Select:
        def __init__(self, labels):
            self.labels = labels
            self.selected = None

        def locator(self, selector):
            if selector == "option:checked":
                selected = self.selected if isinstance(self.selected, list) else [self.selected]
                return Options([item for item in self.labels if item in selected])
            assert selector == "option"
            return Options(self.labels)

        def select_option(self, *, label, force):
            assert force is True
            self.selected = label

    class Selects:
        def __init__(self, controls):
            self.controls = controls

        def count(self):
            return len(self.controls)

        def nth(self, index):
            return self.controls[index]

    class Frame:
        def __init__(self, controls):
            self.controls = controls

        def locator(self, selector):
            assert selector == "select"
            return Selects(self.controls)

    unrelated = Select(["MENA - Global - Global"])
    data_configuration = Select([
        "MENA - Global - Global",
        "Global - Global - MENA",
        "Global - Global - CIS",
    ])
    options = data_configuration.labels

    assert _select_native_options_by_text(
        Frame([unrelated, data_configuration]), [options[0]], options,
    ) is True
    assert unrelated.selected is None
    assert data_configuration.selected == options[0]


def test_targeted_refresh_stales_replaced_filter_definitions(flow_db):
    site, report = _seed_catalog()
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_reports SET discovery_key='Mobile > Report A', source_kind='discovered' WHERE id=?",
            (report["id"],),
        )
        db.execute(
            """UPDATE flow_report_filters SET filter_key='old_label', label='Old label',
               control_label='Old label', source_kind='discovered'
               WHERE report_id=? AND filter_key='region'""",
            (report["id"],),
        )
        flows._apply_discovery(
            db,
            site["id"],
            [flows.DiscoveredReport(
                discovery_key="Mobile > Report A",
                name="Report A",
                report_url="https://example.com/report-a",
                automation={"category_path": ["Mobile", "Report A"]},
                filters=[flows.DiscoveredFilter(
                    filter_key="new_label", label="New label", control_label="New label",
                    control_type="select", options=["A", "B"], position=0,
                )],
            )],
            "2026-08-12T10:00:00",
            complete=False,
        )
        rows = db.execute(
            """SELECT filter_key, enabled, stale FROM flow_report_filters
               WHERE report_id=? AND filter_key IN ('new_label', 'old_label') ORDER BY filter_key""",
            (report["id"],),
        ).fetchall()
    assert [(row["filter_key"], row["enabled"], row["stale"]) for row in rows] == [
        ("new_label", 1, 0), ("old_label", 0, 1),
    ]


def test_asap_week_detection_requires_page_discovered_iso_week_members():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert 're.fullmatch(r"20\\d{4}", value)' in source
    assert 'add_definition("Sell-out Week", "week", week_values)' in source


def test_flow_accepts_standard_iso_week_against_asap_option(flow_db):
    site = flows.create_site(_asap_site(), _request())
    report = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base (MENA)",
        name="Installed Base (MENA)",
        report_url="https://portal.example.test",
        filters=[flows.DiscoveredFilter(
            filter_key="sell_out_week", label="Sell-out Week", control_label="Sell-out Week",
            control_type="week", options=["202633"], position=0,
        )],
    )
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [report], "2026-08-12T10:00:00")
        report_id = db.execute("SELECT id FROM flow_reports").fetchone()["id"]
        body = _flow(site["id"], report_id, selections={"sell_out_week": "2026-W33"})
        flows._validate_flow_selections(db, body)


def test_worker_api_retries_transient_server_errors(monkeypatch):
    attempts = []

    class Response:
        status_code = 500

        def raise_for_status(self):
            if len(attempts) < 3:
                request = __import__("httpx").Request("POST", "http://127.0.0.1/progress")
                raise __import__("httpx").HTTPStatusError(
                    "transient", request=request, response=self,
                )

        def json(self):
            return {"status": "running"}

    class Client:
        def request(self, *_args, **_kwargs):
            attempts.append(1)
            return Response()

    monkeypatch.setattr(flow_worker.time, "sleep", lambda _seconds: None)
    assert flow_worker._api(Client(), "POST", "/progress") == {"status": "running"}
    assert len(attempts) == 3


def test_asap_download_observes_every_open_portal_page_and_uses_staging_folder():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert 'candidate.on("download", capture_download)' in source
    assert 'candidate.remove_listener("download", capture_download)' in source
    assert "download_page.expect_download" not in source
    assert "staged_file, export_pages = _asap_download" in source
    assert "export_page.close(" not in source
    assert "candidate for candidate in wizard_pages" in source
    assert "downloads=download_staging_dir" in source
    assert "downloads[0].path()" not in source


def test_completed_download_is_normalized_locally_then_copied_without_overwrite(tmp_path):
    browser_file = tmp_path / "browser-download"
    browser_file.write_text(
        'Installed Base report\n\n"Week";"Value"\n"202627";"10"\n', encoding="utf-8",
    )
    output = tmp_path / "saved.csv"

    metadata = flow_worker._store_completed_download(browser_file, output)

    assert output.read_text(encoding="utf-8-sig") == "Week,Value\n202627,10\n"
    assert metadata["source_delimiter"] == ";"
    assert metadata["preamble_rows_removed"] == 2
    assert metadata["file_size"] == output.stat().st_size
    with pytest.raises(FileExistsError):
        flow_worker._store_completed_download(browser_file, output)


def test_staged_download_waits_for_a_new_stable_file(tmp_path, monkeypatch):
    old_file = tmp_path / "old-download"
    old_file.write_text("old", encoding="utf-8")
    before = flow_worker._download_staging_snapshot(tmp_path)
    new_file = tmp_path / "new-download"
    new_file.write_text("Week,Value\n202627,10\n", encoding="utf-8")
    monkeypatch.setattr(flow_worker.time, "sleep", lambda _seconds: None)

    assert flow_worker._wait_for_staged_download(tmp_path, before, timeout_seconds=1) == new_file


def test_staged_download_accepts_an_existing_path_overwritten_by_edge(tmp_path, monkeypatch):
    reused_file = tmp_path / "Installed Base.csv"
    reused_file.write_text("old content", encoding="utf-8")
    before = flow_worker._download_staging_snapshot(tmp_path)
    old_mtime = reused_file.stat().st_mtime_ns
    reused_file.write_text("Week,Value\n202627,10\n", encoding="utf-8")
    reused_file.touch()
    assert reused_file.stat().st_mtime_ns != old_mtime or reused_file.stat().st_size != before[reused_file.resolve()][1]
    monkeypatch.setattr(flow_worker.time, "sleep", lambda _seconds: None)

    assert flow_worker._wait_for_staged_download(
        tmp_path, before, timeout_seconds=1,
    ) == reused_file


def test_download_wait_error_does_not_claim_the_download_completed(tmp_path, monkeypatch):
    ticks = iter([0.0, 0.0, 1.1, 1.1])
    monkeypatch.setattr(flow_worker.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(flow_worker.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="no new or updated file appeared") as error:
        flow_worker._wait_for_staged_download(
            tmp_path, {}, timeout_seconds=5, start_timeout_seconds=1,
        )

    assert "completed" not in str(error.value).casefold()


def test_stale_browser_run_is_failed_and_worker_released(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    old = "2026-08-13T10:00:00"
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:05:00"))
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET last_seen_at=? WHERE worker_id=?", (old, "bi-desktop-headed"))
        db.execute("UPDATE flow_runs SET heartbeat_at=? WHERE id=?", (old, queued["id"]))

    # A large report can legitimately spend several minutes in Playwright's
    # blocking download/save call, where its Python heartbeat thread can be
    # starved. The production grace period must not reap that active transfer.
    assert flows.fail_stale_runs()["count"] == 0
    result = flows.fail_stale_runs(timeout_seconds=120)

    assert result == {"failed_run_ids": [queued["id"]], "count": 1}
    run = flows.get_run(queued["id"])
    assert run["status"] == "failed"
    assert "stopped responding" in run["error"]
    assert any(event["stage"] == "worker_lost" for event in run["events"])
    assert flows.list_flows()[0]["last_status"] == "failed"
    worker = next(item for item in flows.list_workers() if item["worker_id"] == "bi-desktop-headed")
    assert worker["current_run_id"] is None


def test_run_heartbeat_prevents_active_worker_from_being_reaped(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:05:00"))
    flows.heartbeat_run("bi-desktop-headed", queued["id"])

    assert flows.fail_stale_runs(timeout_seconds=120)["count"] == 0
    assert flows.get_run(queued["id"])["status"] == "claimed"


def test_long_running_flow_remains_active_with_fresh_heartbeat(flow_db, monkeypatch):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], browser_mode="headed"), _request()
    )
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode: {"status": "starting"})
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="bi-desktop-headed", display_name="Visible",
        capabilities={"headed": True, "process_id": 4321},
    ))
    flows.claim_run("bi-desktop-headed")
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:31:00"))
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_runs SET started_at=?, heartbeat_at=? WHERE id=?",
            ("2026-08-13T10:00:00", "2026-08-13T10:30:59", queued["id"]),
        )
        db.execute(
            "UPDATE flow_workers SET last_seen_at=? WHERE worker_id=?",
            ("2026-08-13T10:30:59", "bi-desktop-headed"),
        )
    result = flows.fail_stale_runs(timeout_seconds=600)

    assert result == {"failed_run_ids": [], "count": 0}
    run = flows.get_run(queued["id"])
    assert run["status"] == "claimed"
    assert run["error"] is None
    assert not any(event["stage"] == "runtime_limit" for event in run["events"])


# --- Flow ownership and failure alerts ---

def _person(name="Dana", role="BI", email="dana@example.test"):
    from app.models import PersonCreate
    from app.routers import people as people_router
    return people_router.create_person(
        PersonCreate(name=name, role=role, email=email), _request(),
    ).model_dump()


def _capture_outlook(monkeypatch):
    from app.routers import email as email_router
    sent = []
    monkeypatch.setattr(
        email_router, "_launch_outlook_payload",
        lambda messages, mode="send": sent.append((messages, mode)) or len(messages),
    )
    return sent


def test_flow_owner_persists_and_exposes_person_details(flow_db):
    person = _person()
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], owner_person_id=person["id"]), _request(),
    )
    assert saved["owner_person_id"] == person["id"]
    assert saved["owner_name"] == "Dana"
    assert saved["owner_email"] == "dana@example.test"
    listed = next(item for item in flows.list_flows() if item["id"] == saved["id"])
    assert listed["owner_name"] == "Dana"

    cleared = flows.update_flow(
        saved["id"], _flow(site["id"], report["id"], owner_person_id=None), _request(),
    )
    assert cleared["owner_person_id"] is None
    assert cleared["owner_name"] is None


def test_flow_owner_must_exist_in_people(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    with pytest.raises(HTTPException) as excinfo:
        flows.create_flow(
            _flow(site["id"], report["id"], owner_person_id=9999), _request(),
        )
    assert excinfo.value.status_code == 400
    assert "People" in excinfo.value.detail


def test_failed_run_emails_the_flow_owner(flow_db, monkeypatch):
    sent = _capture_outlook(monkeypatch)
    person = _person()
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], owner_person_id=person["id"]), _request(),
    )
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="alert-worker", display_name="Alert worker", capabilities={},
    ))
    flows.claim_run("alert-worker")
    result = flows.update_run(
        "alert-worker", queued["id"],
        flows.WorkerProgress(
            status="failed",
            progress={"stage": "report_execution", "message": "RUN never rendered"},
            error="ASAP report rows did not render within 600 seconds.",
        ),
    )

    assert result["owner_alert"]["status"] == "launched"
    assert result["owner_alert"]["owner_email"] == "dana@example.test"
    assert len(sent) == 1
    messages, mode = sent[0]
    assert mode == "send"
    message = messages[0]
    assert message["to"] == "dana@example.test"
    assert saved["name"] in message["subject"]
    assert f"run #{queued['id']}" in message["subject"]
    assert "FLOW RUN FAILED" in message["html_body"]
    assert "ASAP report rows did not render" in message["html_body"]
    assert "Report portal / Weekly movement" in message["html_body"]
    assert "Report execution" in message["html_body"]
    assert "Dana" in message["html_body"]

    # Replaying the terminal status must not email the owner a second time.
    flows.update_run(
        "alert-worker", queued["id"],
        flows.WorkerProgress(status="failed", error="duplicate report"),
    )
    assert len(sent) == 1


def test_failed_run_without_owner_or_email_sends_nothing(flow_db, monkeypatch):
    sent = _capture_outlook(monkeypatch)
    no_email = _person(name="Quiet", email=None)
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    unowned = flows.create_flow(_flow(site["id"], report["id"]), _request())
    owned = flows.create_flow(
        _flow(
            site["id"], report["id"], name="Owned without email",
            owner_person_id=no_email["id"],
        ),
        _request(),
    )
    flows.register_worker(flows.WorkerRegister(
        worker_id="quiet-worker", display_name="Quiet worker", capabilities={},
    ))
    for flow, reason in ((unowned, "no owner"), (owned, "no email")):
        queued = flows.queue_run(flow["id"], _request())
        flows.claim_run("quiet-worker")
        result = flows.update_run(
            "quiet-worker", queued["id"],
            flows.WorkerProgress(status="failed", error="boom"),
        )
        assert result["owner_alert"]["status"] == "not_sent", reason
    assert sent == []


def test_worker_loss_and_restart_failures_email_the_flow_owner(flow_db, monkeypatch):
    sent = _capture_outlook(monkeypatch)
    person = _person()
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], owner_person_id=person["id"]), _request(),
    )

    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="lost-worker", display_name="Lost worker",
        capabilities={"process_id": 100},
    ))
    flows.claim_run("lost-worker")
    old = "2026-08-13T10:00:00"
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:05:00"))
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET last_seen_at=? WHERE worker_id=?", (old, "lost-worker"))
        db.execute("UPDATE flow_runs SET heartbeat_at=? WHERE id=?", (old, queued["id"]))
    assert flows.fail_stale_runs(timeout_seconds=120)["count"] == 1
    assert len(sent) == 1
    assert "stopped responding" in sent[0][0][0]["html_body"]

    retry = flows.queue_run(saved["id"], _request())
    flows.claim_run("lost-worker")
    flows.register_worker(flows.WorkerRegister(
        worker_id="lost-worker", display_name="Lost worker",
        capabilities={"process_id": 101},
    ))
    assert len(sent) == 2
    assert f"run #{retry['id']}" in sent[1][0][0]["subject"]


# --- Resume from the last successful file ---

def _fail_run_with_saved_files(worker_id, run_id, weeks, error="boom"):
    flows.update_run(
        worker_id, run_id,
        flows.WorkerProgress(
            status="failed", error=error,
            artifacts=[
                {
                    "period_key": [week], "export_view": None, "status": "saved",
                    "file_path": rf"C:\Reports\Downloads\weekly_{week}.csv",
                    "filename": f"weekly_{week}.csv",
                }
                for week in weeks
            ],
        ),
    )


def test_resume_queues_a_run_that_skips_saved_files(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["periods"] == [["2026-W30"], ["2026-W31"], ["2026-W32"]]
    flows.register_worker(flows.WorkerRegister(
        worker_id="resume-worker", display_name="Resume worker", capabilities={},
    ))
    flows.claim_run("resume-worker")
    _fail_run_with_saved_files("resume-worker", queued["id"], ["2026-W30"])

    resumed = flows.resume_run(queued["id"], _request())
    assert resumed["resumes_run_id"] == queued["id"]
    assert resumed["skipped_files"] == 1
    assert resumed["job"]["resume"] == {
        "from_run_id": queued["id"],
        "completed": [{
            "export_view": None, "period_key": ["2026-W30"],
            "file_path": "C:\\Reports\\Downloads\\weekly_2026-W30.csv",
            "source_run_id": queued["id"],
        }],
    }
    assert flows.get_run(resumed["id"])["trigger_type"] == "resume"

    # Resuming a failed resume carries the earlier files forward.
    flows.claim_run("resume-worker")
    _fail_run_with_saved_files("resume-worker", resumed["id"], ["2026-W31"])
    chained = flows.resume_run(resumed["id"], _request())
    assert chained["skipped_files"] == 2
    assert [item["period_key"] for item in chained["job"]["resume"]["completed"]] == [
        ["2026-W30"], ["2026-W31"],
    ]


def test_resume_rejects_active_runs_and_runs_without_saved_progress(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="strict-worker", display_name="Strict worker", capabilities={},
    ))
    flows.claim_run("strict-worker")

    with pytest.raises(HTTPException) as excinfo:
        flows.resume_run(queued["id"], _request())
    assert excinfo.value.status_code == 409
    assert "failed or cancelled" in excinfo.value.detail

    flows.update_run(
        "strict-worker", queued["id"], flows.WorkerProgress(status="failed", error="boom"),
    )
    with pytest.raises(HTTPException) as excinfo:
        flows.resume_run(queued["id"], _request())
    assert excinfo.value.status_code == 409
    assert "No file finished" in excinfo.value.detail

    # A completed run is not resumable either.
    retry = flows.queue_run(saved["id"], _request())
    flows.claim_run("strict-worker")
    flows.update_run(
        "strict-worker", retry["id"], flows.WorkerProgress(status="succeeded"),
    )
    with pytest.raises(HTTPException) as excinfo:
        flows.resume_run(retry["id"], _request())
    assert excinfo.value.status_code == 409


def test_failed_report_without_artifacts_keeps_previously_saved_files(flow_db):
    """The worker's final failed post carries no artifacts when the download
    loop unwound on an exception. Files recorded by earlier progress posts
    must survive so the run stays resumable."""
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="wipe-worker", display_name="Wipe worker", capabilities={},
    ))
    flows.claim_run("wipe-worker")
    flows.update_run(
        "wipe-worker", queued["id"],
        flows.WorkerProgress(
            status="running",
            progress={"stage": "file_export", "message": "Export 2 of 3"},
            artifacts=[{
                "period_key": ["2026-W30"], "export_view": None, "status": "saved",
                "file_path": r"C:\Reports\Downloads\weekly_2026-W30.csv",
                "filename": "weekly_2026-W30.csv",
            }],
        ),
    )
    flows.update_run(
        "wipe-worker", queued["id"],
        flows.WorkerProgress(status="failed", error="menu item was not visible"),
    )

    detail = flows.get_run(queued["id"])
    assert detail["status"] == "failed"
    assert [item["filename"] for item in detail["artifacts"]] == ["weekly_2026-W30.csv"]
    assert [item["filename"] for item in detail["files"]] == ["weekly_2026-W30.csv"]

    resumed = flows.resume_run(queued["id"], _request())
    assert resumed["skipped_files"] == 1
    assert resumed["job"]["resume"]["completed"] == [
        {
            "export_view": None, "period_key": ["2026-W30"],
            "file_path": "C:\\Reports\\Downloads\\weekly_2026-W30.csv",
            "source_run_id": queued["id"],
        },
    ]


def test_worker_shares_the_artifact_list_with_its_failure_report():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    # The shared runner owns acquisition now; failures return the same partial
    # list through state to the worker's existing rich failure report.
    import ast
    tree = ast.parse(source)
    shared = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute_flow')
    acquire = next(node for node in ast.walk(shared) if isinstance(node, ast.Call) and any(isinstance(value, ast.Name) and value.id == 'execute_job' for value in ast.walk(node.func)))
    assert any(keyword.arg == 'artifacts' and isinstance(keyword.value, ast.Name) and keyword.value.id == 'artifacts' for keyword in acquire.keywords)
    assert 'state.update(artifacts=artifacts' in ast.get_source_segment(source, shared)
    worker = source[source.index('def run_worker('):]
    assert 'artifacts=artifacts, state=execution_state' in worker
    assert 'artifacts = execution_state.get("artifacts", artifacts)' in worker
    assert 'artifacts=artifacts, timings=timings' in worker


def test_scan_progress_posts_build_a_live_event_log(flow_db):
    site = flows.create_site(_asap_site(), _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="scan-log-worker", display_name="Scan log worker",
        capabilities={"adapters": ["asap_portal"]},
    ))
    with database.get_db() as db:
        scan_id, _browser_mode = flows._queue_scan(db, dict(
            db.execute("SELECT * FROM flow_sites WHERE id=?", (site["id"],)).fetchone()
        ), "manual", "Analyst")
        db.execute(
            "UPDATE flow_catalog_scans SET worker_id=?, status='claimed' WHERE id=?",
            ("scan-log-worker", scan_id),
        )

    flows.update_scan("scan-log-worker", scan_id, flows.ScanProgress(
        status="running",
        progress={"stage": "filter_inspection", "message": "Inspecting report 1 of 3."},
        complete=False,
    ))
    flows.update_scan("scan-log-worker", scan_id, flows.ScanProgress(
        status="running",
        progress={"stage": "html_dashboard_links", "message": "Dash: 2 download link(s) found."},
        complete=False,
    ))

    log = flows.list_scan_events(scan_id, after_id=0, limit=400)
    assert log["scan_id"] == scan_id
    stages = [event["stage"] for event in log["events"]]
    assert stages == ["filter_inspection", "html_dashboard_links"]
    assert "Inspecting report 1 of 3." in log["events"][0]["message"]

    # Incremental polling from the last seen id returns only new events.
    tail = flows.list_scan_events(scan_id, after_id=log["events"][0]["id"], limit=400)
    assert [event["stage"] for event in tail["events"]] == ["html_dashboard_links"]

    with pytest.raises(HTTPException) as excinfo:
        flows.list_scan_events(99999, after_id=0, limit=400)
    assert excinfo.value.status_code == 404


def test_catalog_ui_groups_reports_and_drops_manual_add():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "flow-add-report" not in source
    assert "_flowReportDialog" not in source
    assert 'class="flow-catalog-group"' in source
    assert "openCatalogTopics" in source
    assert 'id="flow-scan-log"' in source
    assert "scans/${scans[0].id}/events" in source


def test_oversized_option_lists_are_capped_not_rejected():
    """A prompt with thousands of members must not 422 the whole scan."""
    body = flows.ScanProgress(
        status="succeeded",
        reports=[{
            "discovery_key": "Mobile > Big > Big", "name": "Big",
            "report_url": "https://portal.example.test",
            "automation": {"category_path": ["Mobile", "Big", "Big"]},
            "filters": [
                {
                    "filter_key": "item", "label": "Item", "control_label": "Item",
                    "control_type": "multi_select",
                    # The size that used to 422 the whole scan now lands intact.
                    "options": [f"Item {index}" for index in range(2837)],
                },
                {
                    "filter_key": "country", "label": "Country", "control_label": "Country",
                    "control_type": "multi_select",
                    "options": [f"Country {index}" for index in range(flows.MAX_DISCOVERED_OPTIONS + 900)],
                },
            ],
        }],
    )
    assert len(body.reports[0].filters[0].options) == 2837
    assert len(body.reports[0].filters[1].options) == flows.MAX_DISCOVERED_OPTIONS
    assert body.skipped_reports == []


def test_invalid_reports_are_skipped_so_the_scan_still_lands(flow_db):
    site = flows.create_site(_asap_site(), _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id="skip-worker", display_name="Skip worker", capabilities={},
    ))
    with database.get_db() as db:
        scan_id, _browser_mode = flows._queue_scan(db, dict(
            db.execute("SELECT * FROM flow_sites WHERE id=?", (site["id"],)).fetchone()
        ), "manual", "Analyst")
        db.execute(
            "UPDATE flow_catalog_scans SET worker_id=?, status='claimed' WHERE id=?",
            ("skip-worker", scan_id),
        )

    body = flows.ScanProgress(
        status="succeeded",
        reports=[
            {  # valid
                "discovery_key": "Mobile > Good > Good", "name": "Good",
                "report_url": "https://portal.example.test",
                "automation": {"category_path": ["Mobile", "Good", "Good"]},
                "filters": [],
            },
            {  # invalid: control_type is not supported
                "discovery_key": "Mobile > Bad > Bad", "name": "Bad",
                "report_url": "https://portal.example.test",
                "automation": {"category_path": ["Mobile", "Bad", "Bad"]},
                "filters": [{
                    "filter_key": "x", "label": "X", "control_label": "X",
                    "control_type": "wormhole", "options": ["a"],
                }],
            },
        ],
    )
    assert [report.name for report in body.reports] == ["Good"]
    assert len(body.skipped_reports) == 1
    assert "Bad" in body.skipped_reports[0]["report"]

    result = flows.update_scan("skip-worker", scan_id, body)

    assert result["result"]["report_count"] == 1
    assert result["result"]["skipped_reports"][0]["report"].endswith("Bad")
    names = [report["name"] for report in flows.catalog()["reports"]]
    assert names == ["Mobile > Good > Good"]
    log = flows.list_scan_events(scan_id, after_id=0, limit=400)
    assert any(event["stage"] == "reports_skipped" for event in log["events"])


def _full_scan_mena_report():
    return flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base (MENA)",
        name="Installed Base (MENA)",
        report_url="https://portal.example.test",
        ready_text="Export Wizard (Detail)",
        automation={
            "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
            "report_tab": "Export Wizard (Detail)",
            "export_views": [{"label": "Export Wizard (Detail)", "filter_keys": ["region"]}],
            "asap_export_capabilities": {"status": "detected", "views": {
                "Export Wizard (Detail)": {
                    "status": "detected", "download_types": ["csv_file_format"],
                    "options_by_type": {"csv_file_format": {
                        "export_report_title": {"available": True, "checked": True},
                        "export_filter_details": {"available": True, "checked": False},
                    }},
                },
            }},
        },
        filters=[flows.DiscoveredFilter(
            filter_key="region", label="Region", control_label="Region",
            control_type="select", options=["Global"], required=True, position=0,
        )],
    )


def test_partial_scan_keeps_filters_discovered_by_a_full_scan(flow_db):
    site = flows.create_site(_asap_site(), _request())
    now = flows._iso(flows._now())
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [_full_scan_mena_report()], now)

    partial = flows.DiscoveredReport(
        discovery_key="Mobile > Installed Base > Installed Base (MENA)",
        name="Installed Base (MENA)",
        report_url="https://portal.example.test",
        automation={
            "category_path": ["Mobile", "Installed Base", "Installed Base (MENA)"],
            "scan_mode": "partial",
        },
        filters=[],
    )
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [partial], now)

    report = flows.catalog()["reports"][0]
    region = next(item for item in report["filters"] if item["filter_key"] == "region")
    assert region["enabled"] and not region["stale"]
    assert region["options"] == ["Global"]
    # Export views and ready text only a full scan can see must survive.
    assert report["automation"]["export_views"][0]["label"] == "Export Wizard (Detail)"
    assert report["automation"]["asap_export_capabilities"]["views"][
        "Export Wizard (Detail)"
    ]["download_types"] == ["csv_file_format"]
    assert report["ready_text"] == "Export Wizard (Detail)"


def test_scan_mode_is_validated_and_reaches_the_queued_job(flow_db, monkeypatch):
    site = flows.create_site(_asap_site(), _request())
    monkeypatch.setattr(flows, "launch_local_worker", lambda *_a, **_k: {"status": "starting"})

    queued = flows.queue_catalog_scan(site["id"], _request(), mode="partial")
    assert queued["mode"] == "partial"
    scan = next(item for item in flows.list_scans(site_id=None, limit=50) if item["id"] == queued["id"])
    assert scan["job"]["discovery"]["mode"] == "partial"

    with pytest.raises(HTTPException) as excinfo:
        flows.queue_catalog_scan(site["id"], _request(), mode="turbo")
    assert excinfo.value.status_code == 400


def test_builder_exposes_a_targeted_report_scan_and_quick_scan():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'id="flow-scan-report-now"' in source
    assert "/api/flows/reports/${reportId}/scan" in source
    assert "flow-scan-site-quick" in source
    assert "scan?mode=partial" in source and "scan?mode=full" in source
    assert 'class="btn-sm btn-outline btn-danger-outline flow-stop-scan"' in source
    assert "/api/flows/scans/${button.dataset.id}/stop" in source
    assert "_flowScanTargetsReport" in source


def _dashboard_report(site_id):
    """A discovered HTML dashboard: download links, no export views."""
    return flows.DiscoveredReport(
        discovery_key="Online > Digital Shelf > Digital Shelf",
        name="Digital Shelf",
        report_url="https://portal.example.test",
        download_text="Download CSV",
        automation={
            "category_path": ["Online", "Digital Shelf", "Digital Shelf"],
            "kind": "html_dashboard",
            "download_links": [
                {"label": "Download CSV", "href": "/files/shelf.csv", "download_attr": False},
                {"label": "Download raw data", "href": "", "download_attr": True},
            ],
        },
        filters=[],
    )


def test_dashboard_flow_persists_links_and_puts_them_in_the_job(flow_db):
    site = flows.create_site(_asap_site(), _request())
    with database.get_db() as db:
        flows._apply_discovery(db, site["id"], [_dashboard_report(site["id"])],
                               flows._iso(flows._now()))
    report = flows.catalog()["reports"][0]

    saved = flows.create_flow(
        _flow(
            site["id"], report["id"], name="Digital shelf pull",
            selections={}, export_views=[],
            download_links=["Download CSV", "Download raw data"],
            period_strategy="none", start_week=None, end_week=None,
            download_mode="single", window_weeks=None,
            filename_template="{flow}_{export}.csv", schedule_type="manual",
            schedule_days=[],
        ),
        _request(),
    )
    assert saved["download_links"] == ["Download CSV", "Download raw data"]

    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["report"]["download_links"] == ["Download CSV", "Download raw data"]
    assert queued["job"]["report"]["export_views"] == []

    reloaded = next(item for item in flows.list_flows() if item["id"] == saved["id"])
    assert reloaded["download_links"] == ["Download CSV", "Download raw data"]


def test_multiple_download_links_require_a_unique_filename_token():
    with pytest.raises(ValueError, match="download links require"):
        flows.FlowWrite(
            name="Dash", site_id=1, report_id=1,
            download_links=["A", "B"], selections={},
            period_strategy="none", download_mode="single", file_format="csv",
            target_folder=r"C:\Reports", filename_template="dash.csv",
        )


def test_dashboard_builder_section_replaces_export_views():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert 'id="flow-download-links-section"' in source
    assert "data-flow-download-link" in source
    assert "download_links: [...document.querySelectorAll" in source
    assert "_flowSyncExportSections" in source


# --- Run-folder retention: server-side registration, assignment, pinning ---


def _retention_folder(target, run_id):
    from app import flow_retention

    # str(Path(...)) matches the server's own normalization, so these
    # assertions hold on Windows (backslashes) and POSIX alike.
    return str(Path(target) / flow_retention.run_folder_name(run_id))


def _complete_registered_run(worker_id, flow_id, target, status="succeeded"):
    """Queue, claim, register a run folder, and finish the run."""
    queued = flows.queue_run(flow_id, _request())
    flows.claim_run(worker_id)
    registration = flows.register_run_folder(
        worker_id, queued["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, queued["id"])),
    )
    flows.update_run(worker_id, queued["id"], flows.WorkerProgress(status=status))
    return queued["id"], registration


def _retention_flow(target, **overrides):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], target_folder=target, **overrides), _request(),
    )
    flows.register_worker(flows.WorkerRegister(
        worker_id="retention-worker", display_name="Retention worker", capabilities={},
    ))
    return saved, site, report


def test_registration_counts_the_current_run_and_keeps_the_newest_three(flow_db):
    target = "/reports/downloads"
    saved, site, report = _retention_flow(target)

    run_ids = []
    for _ in range(3):
        run_id, registration = _complete_registered_run("retention-worker", saved["id"], target)
        run_ids.append(run_id)
        # With at most 3 recorded folders (the current one included), nothing
        # is ever assigned - the off-by-one would assign on the 3rd run.
        assert registration["ops"] == []

    queued = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    registration = flows.register_run_folder(
        "retention-worker", queued["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, queued["id"])),
    )
    assert [op["source_run_id"] for op in registration["ops"]] == [run_ids[0]]
    op = registration["ops"][0]
    assert op["original_path"] == _retention_folder(target, run_ids[0])
    assert op["tombstone_path"].endswith(f".op{op['op_id']}.deleting")
    assert Path(op["tombstone_path"]).parent == Path(target)


def test_folder_key_groups_path_spellings(flow_db):
    assert flows._folder_key("/reports//downloads/./#5_25-08-2026") == flows._folder_key(
        "/reports/downloads/#9_26-08-2026"
    )
    assert flows._folder_key("/reports/other/#5_25-08-2026") != flows._folder_key(
        "/reports/downloads/#5_25-08-2026"
    )
    assert flows._folder_key(r"C:\Reports\Downloads\#5_25-08-2026") == flows._folder_key(
        r"c:/reports/downloads/#9_26-08-2026"
    )
    assert flows._folder_key(r"\\Server\Share\Exports\#5_25-08-2026") == flows._folder_key(
        r"\\server\share\exports\#9_26-08-2026"
    )
    assert flows._folder_key(r"\\?\UNC\Server\Share\Exports\#5_25-08-2026") == flows._folder_key(
        r"\\server\share\exports\#9_26-08-2026"
    )


def test_an_active_runs_folder_is_never_assigned_for_cleanup(flow_db):
    # Exercise retention while two operations run; capacity defaults to one.
    with database.get_db() as db:
        flows.flow_paths.save_setting(db, 'flows_headless_capacity', 2)
    target = "/reports/downloads"
    saved, site, report = _retention_flow(target)
    # An old run that is still running (its worker is alive) sits below the
    # keep window but must never be assigned; two flows share the target.
    other = flows.create_flow(
        _flow(site["id"], report["id"], name="Second flow", target_folder=target),
        _request(),
    )
    flows.register_worker(flows.WorkerRegister(
        worker_id="stuck-worker", display_name="Stuck worker", capabilities={},
    ))
    stuck = flows.queue_run(other["id"], _request())
    flows.claim_run("stuck-worker")
    flows.register_run_folder(
        "stuck-worker", stuck["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, stuck["id"])),
    )  # never finishes

    for _ in range(3):
        _complete_registered_run("retention-worker", saved["id"], target)
    queued = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    registration = flows.register_run_folder(
        "retention-worker", queued["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, queued["id"])),
    )
    assert stuck["id"] not in [op["source_run_id"] for op in registration["ops"]]


def test_retention_outcomes_update_ops_and_release_failures_for_retry(flow_db):
    target = "/reports/downloads"
    saved, site, report = _retention_flow(target)
    old_ids = [_complete_registered_run("retention-worker", saved["id"], target)[0] for _ in range(3)]

    queued = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    registration = flows.register_run_folder(
        "retention-worker", queued["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, queued["id"])),
    )
    op = registration["ops"][0]

    # A transient failure releases the operation; the next run retries it.
    flows.update_run(
        "retention-worker", queued["id"],
        flows.WorkerProgress(status="running", retention=[
            {"op_id": op["op_id"], "outcome": "failed", "detail": "file is open in Excel"},
        ]),
    )
    flows.update_run("retention-worker", queued["id"], flows.WorkerProgress(status="succeeded"))
    with database.get_db() as db:
        row = db.execute("SELECT * FROM flow_retention_ops WHERE id=?", (op["op_id"],)).fetchone()
        assert row["state"] == "issued" and row["assigned_run_id"] is None
        assert "Excel" in row["error"]

    retry_run = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    retry_registration = flows.register_run_folder(
        "retention-worker", retry_run["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, retry_run["id"])),
    )
    retry_ops = {item["op_id"] for item in retry_registration["ops"]}
    assert op["op_id"] in retry_ops

    # A completed deletion marks the source folder pruned...
    flows.update_run(
        "retention-worker", retry_run["id"],
        flows.WorkerProgress(status="succeeded", retention=[
            {"op_id": op["op_id"], "outcome": "deleted", "detail": ""},
        ]),
    )
    with database.get_db() as db:
        row = db.execute("SELECT * FROM flow_retention_ops WHERE id=?", (op["op_id"],)).fetchone()
        assert row["state"] == "done"
        source = db.execute("SELECT folder_state, pruned_at FROM flow_runs WHERE id=?", (old_ids[0],)).fetchone()
        assert source["folder_state"] == "pruned" and source["pruned_at"]


def test_a_skipped_operation_is_abandoned_and_never_reassigned(flow_db):
    target = "/reports/downloads"
    saved, site, report = _retention_flow(target)
    for _ in range(3):
        _complete_registered_run("retention-worker", saved["id"], target)
    queued = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    registration = flows.register_run_folder(
        "retention-worker", queued["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, queued["id"])),
    )
    op = registration["ops"][0]
    flows.update_run(
        "retention-worker", queued["id"],
        flows.WorkerProgress(status="succeeded", retention=[
            {"op_id": op["op_id"], "outcome": "skipped",
             "detail": "the folder has no Metronome ownership marker"},
        ]),
    )
    next_run = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    next_registration = flows.register_run_folder(
        "retention-worker", next_run["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, next_run["id"])),
    )
    assert op["op_id"] not in {item["op_id"] for item in next_registration["ops"]}
    with database.get_db() as db:
        row = db.execute("SELECT state FROM flow_retention_ops WHERE id=?", (op["op_id"],)).fetchone()
        assert row["state"] == "abandoned"


def test_a_queued_resume_pins_its_source_folders_against_cleanup(flow_db):
    target = "/reports/downloads"
    # The resumable flow runs headed so the headless helper worker for the
    # second flow can never claim the queued resume out from under the test.
    saved, site, report = _retention_flow(target, browser_mode="headed")
    flows.register_worker(flows.WorkerRegister(
        worker_id="retention-worker", display_name="Retention worker",
        capabilities={"headed": True},
    ))

    failed = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    flows.register_run_folder(
        "retention-worker", failed["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, failed["id"])),
    )
    _fail_run_with_saved_files("retention-worker", failed["id"], ["2026-W30"])
    resumed = flows.resume_run(failed["id"], _request())
    with database.get_db() as db:
        refs = db.execute(
            "SELECT source_run_id FROM flow_run_source_refs WHERE consumer_run_id=?",
            (resumed["id"],),
        ).fetchall()
    assert [ref["source_run_id"] for ref in refs] == [failed["id"]]

    # Push the failed run's folder beyond the keep window with another flow
    # sharing the target: while the resume is queued, it must not be assigned.
    other = flows.create_flow(
        _flow(site["id"], report["id"], name="Second flow", target_folder=target),
        _request(),
    )
    flows.register_worker(flows.WorkerRegister(
        worker_id="other-worker", display_name="Other worker", capabilities={},
    ))
    for _ in range(3):
        _complete_registered_run("other-worker", other["id"], target)
    pusher = flows.queue_run(other["id"], _request())
    flows.claim_run("other-worker")
    registration = flows.register_run_folder(
        "other-worker", pusher["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, pusher["id"])),
    )
    assert failed["id"] not in [op["source_run_id"] for op in registration["ops"]]
    flows.update_run("other-worker", pusher["id"], flows.WorkerProgress(status="succeeded"))

    # Once the resume finishes, the pin no longer holds.
    flows.claim_run("retention-worker")
    flows.update_run("retention-worker", resumed["id"], flows.WorkerProgress(status="succeeded"))
    final = flows.queue_run(other["id"], _request())
    flows.claim_run("other-worker")
    final_registration = flows.register_run_folder(
        "other-worker", final["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, final["id"])),
    )
    assert failed["id"] in [op["source_run_id"] for op in final_registration["ops"]]


def test_sql_retry_is_rejected_once_the_source_folder_is_scheduled_for_cleanup(
    flow_db, tmp_path,
):
    target = str(tmp_path)
    with database.get_db() as db:
        db.execute(
            """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
               VALUES ('warehouse', 'reporting', 'inflow', CURRENT_TIMESTAMP, 0)"""
        )
    saved, site, report = _retention_flow(
        target, sql_handoff_enabled=True, sql_mode="append",
        sql_database="warehouse", sql_schema="reporting", sql_table="inflow",
    )
    queued = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    artifact = Path(target) / "weekly_2026-W30.csv"
    artifact.write_text("a,b\n1,2\n", encoding="utf-8")
    flows.update_run(
        "retention-worker", queued["id"],
        flows.WorkerProgress(status="succeeded", artifacts=[{
            "period_key": ["2026-W30"], "export_view": None, "status": "saved",
            "file_path": str(artifact), "filename": artifact.name,
        }]),
    )

    retried = flows.retry_run_sql(queued["id"], _request())
    with database.get_db() as db:
        refs = db.execute(
            "SELECT source_run_id FROM flow_run_source_refs WHERE consumer_run_id=?",
            (retried["id"],),
        ).fetchall()
        assert [ref["source_run_id"] for ref in refs] == [queued["id"]]
        db.execute("UPDATE flow_runs SET status='cancelled' WHERE id=?", (retried["id"],))
        db.execute(
            """INSERT INTO flow_retention_ops
               (source_run_id, original_path, tombstone_path, state, created_at, updated_at)
               VALUES (?, ?, ?, 'issued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (queued["id"], target, f"{target}/.x.op1.deleting"),
        )
    with pytest.raises(HTTPException, match="run folder cleanup"):
        flows.retry_run_sql(queued["id"], _request())


def test_retention_module_is_the_only_deletion_site_and_gates_every_path():
    worker_source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    forbidden = [".unlink(", ".rmdir(", "shutil.rmtree", "os.remove(", "os.unlink("]
    assert all(token not in worker_source for token in forbidden)
    retention_source = Path(__file__).parents[1].joinpath("app", "flow_retention.py").read_text()
    assert "_gate_reason" in retention_source
    assert "original.rename(tombstone)" in retention_source
    assert retention_source.index("original.rename(tombstone)") < retention_source.index(
        "shutil.rmtree(tombstone)", retention_source.index("original.rename(tombstone)")
    )
    assert "run_id=run_id, register_folder=register_folder" in worker_source


def test_resume_omits_pruned_entries_and_resumes_an_all_pruned_source(flow_db):
    target = "/reports/downloads"
    saved, site, report = _retention_flow(target)

    failed = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    flows.register_run_folder(
        "retention-worker", failed["id"],
        flows.FolderRegister(run_folder=_retention_folder(target, failed["id"])),
    )
    _fail_run_with_saved_files("retention-worker", failed["id"], ["2026-W30"])
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET folder_state='pruned' WHERE id=?", (failed["id"],))

    # Every saved file's folder is gone: the resume still queues, with an
    # empty completed list, so the worker downloads everything again. An
    # entry stripped only of its path would read as legacy-complete instead.
    resumed = flows.resume_run(failed["id"], _request())
    assert resumed["skipped_files"] == 0
    assert resumed["job"]["resume"] == {"from_run_id": failed["id"], "completed": []}


def test_register_folder_is_idempotent_and_rejects_a_different_path(flow_db):
    target = "/reports/downloads"
    saved, site, report = _retention_flow(target)
    for _ in range(3):
        _complete_registered_run("retention-worker", saved["id"], target)
    queued = flows.queue_run(saved["id"], _request())
    flows.claim_run("retention-worker")
    folder = flows.FolderRegister(run_folder=_retention_folder(target, queued["id"]))
    first = flows.register_run_folder("retention-worker", queued["id"], folder)
    again = flows.register_run_folder("retention-worker", queued["id"], folder)
    assert [op["op_id"] for op in first["ops"]] == [op["op_id"] for op in again["ops"]]
    with pytest.raises(HTTPException, match="already registered a different folder"):
        flows.register_run_folder(
            "retention-worker", queued["id"],
            flows.FolderRegister(run_folder=str(Path(target) / "somewhere-else")),
        )


def test_excel_trim_is_a_recorded_flow_setting_not_a_hardcoded_step(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(
        _flow(site["id"], report["id"], excel_trim="First_Row_And_Column"),
        _request(),
    )

    assert saved["excel_trim"] == "first_row_and_column"
    queued = flows.queue_run(saved["id"], _request())
    assert queued["job"]["downloads"]["excel_trim"] == "first_row_and_column"
    reloaded = next(item for item in flows.list_flows() if item["id"] == saved["id"])
    assert reloaded["excel_trim"] == "first_row_and_column"


def test_excel_trim_defaults_to_none_and_rejects_unknown_options():
    assert _flow(1, 1).excel_trim == "none"
    with pytest.raises(ValueError, match="pre-processing"):
        _flow(1, 1, excel_trim="drop_everything")
