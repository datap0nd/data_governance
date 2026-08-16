import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database
from app import flow_local_runner
from app import flow_worker
from app.routers import flows


@pytest.fixture()
def flow_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flows.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
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
            if value == "Repeated Label":
                return Collection([repeated_member, actual_label])
            if value == "Choice":
                return Collection([Node("choice anchor", parent=prompt)])
            raise AssertionError(value)

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


def test_asap_sell_out_country_uses_verified_native_selection(monkeypatch):
    calls = []
    countries = ["Saudi Arabia", "Morocco", "Iraq", "Egypt", "Pakistan", "Syria"]
    definition = {
        "filter_key": "sell_out_country",
        "control_label": "Sell-out Country",
        "control_type": "multi_select",
        "options": countries,
    }
    job = {
        "selections": {"sell_out_country": countries},
        "report": {"filters": [definition]},
    }

    monkeypatch.setattr(
        flow_worker, "_select_native_options_by_text",
        lambda _frame, values, options: calls.append((values, options)) or True,
    )
    monkeypatch.setattr(
        flow_worker, "_asap_select_list_values",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("visual fallback used")),
    )

    flow_worker._asap_apply_configuration(object(), job, None)

    assert calls == [(countries, countries)]


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
        "worker_id": "bi-desktop-headless",
    }
    assert queued["job"]["sql_handoff"] == {
        "enabled": False, "mode": None, "database": None, "schema": None, "table": None,
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
    assert flows._schedule_next("monthly", "08:00", [], 31) == datetime(2026, 3, 31, 8, 0)


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

    retry = flows.queue_run(saved["id"], _request())
    claimed = flows.claim_run(worker.worker_id)
    flows.update_run(
        worker.worker_id, claimed["run"]["id"],
        flows.WorkerProgress(status="succeeded"),
    )
    assert flows.get_flow(saved["id"])["start_week"] == "2026-W33"


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
        "enabled": True, "mode": "replace", "database": "warehouse",
        "schema": "reporting", "table": "inflow",
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
            sql_schema="reporting", sql_table="new managed target",
        ),
        _request(),
    )

    assert saved["sql_table"] == "new managed target"
    assert flows.queue_run(saved["id"], _request())["job"]["sql_handoff"] == {
        "enabled": True, "mode": "replace", "database": "warehouse",
        "schema": "reporting", "table": "new managed target",
    }


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


def test_inactive_scheduled_flow_has_no_next_run(flow_db):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    saved = flows.create_flow(_flow(site["id"], report["id"], enabled=False), _request())
    assert saved["schedule_type"] == "weekly"
    assert saved["next_run_at"] is None


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
    assert job["discovery"]["delete_missing"] is False


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


def test_database_schema_has_no_flow_delete_policy(flow_db):
    with database.get_db() as db:
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(flows)").fetchall()}
    assert "delete_existing" not in job_columns
    assert "cleanup_policy" not in job_columns


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


def test_service_starts_headless_worker_service_instead_of_child_process():
    source = Path(__file__).parents[1].joinpath("app", "flow_local_runner.py").read_text()
    assert '["sc.exe", "start", SERVICE_NAME]' in source
    assert '"System32", "schtasks.exe"' in source
    assert '[schtasks, "/Run", "/TN", HEADED_TASK_PATH]' in source
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
    assert 'New-ScheduledTaskAction -Execute $PyExe' in source
    assert "--worker-id bi-desktop-headed" in source
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
    assert "for attempt in range(60)" in source
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
    assert 'file_format: $("#flow-file-format").value' in source
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
    assert "ASAP will not open" in log_source
    assert "setTimeout(loadRun, 2000)" in log_source


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
        lambda mode, pid: stopped.append((mode, pid)) or {"status": "stopped", "process_id": pid},
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
        lambda mode, pid: stopped.append((mode, pid)) or {"status": "stopped"},
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
        lambda mode, pid: stopped.append((mode, pid)) or {"status": "stopped", "process_id": pid},
    )

    result = flows.stop_run(saved["id"], _request())

    assert result["run_id"] == queued["id"]
    assert result["status"] == "cancelled"
    assert stopped == [("headless", 9876)]


def test_asap_scraper_never_uses_control_modified_clicks():
    source = Path(flow_worker.__file__).read_text()
    assert 'modifiers=["Control"]' not in source
    assert 'keyboard.down("Control")' not in source
    assert 'keyboard.press("Control' not in source


def test_every_active_flow_renders_a_stop_button():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert '${activeRun ? `<button class="btn-sm btn-outline btn-danger-outline flow-stop"' in source
    assert 'activeRun.job?.execution?.browser_mode === "headed"' not in source
    index = Path(__file__).parents[1].joinpath("app", "static", "index.html").read_text()
    assert '/static/app.js?v=53' in index


def test_flow_builder_exposes_managed_snapshot_and_new_table_name():
    source = Path(__file__).parents[1].joinpath("app", "static", "app.js").read_text()
    assert "Managed snapshot refresh" in source
    assert 'id="flow-sql-table" list="flow-sql-table-options"' in source
    assert "Snapshot refresh creates a missing table" in source
    assert "Recreate and replace" not in source

    log_source = Path(__file__).parents[1].joinpath("app", "static", "flow_run_log.js").read_text()
    assert "This will refresh the managed snapshot" in log_source
    assert "drop and recreate" not in log_source
    log_html = Path(__file__).parents[1].joinpath("app", "static", "flow_run_log.html").read_text()
    assert '/static/flow_run_log.js?v=2' in log_html


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
    assert 'frame.locator("select").all()' in source
    assert 'frame.locator("select:visible").all()' not in source
    assert 'control.locator("option").all_text_contents()' in source
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


def test_worker_api_includes_validation_response_in_error():
    httpx = __import__("httpx")
    request = httpx.Request("POST", "http://127.0.0.1/progress")
    response = httpx.Response(
        422, request=request,
        json={"detail": [{"loc": ["body", "reports", 0, "filters", 0, "options"],
                          "msg": "List should have at most 2000 items"}]},
    )

    class Client:
        def request(self, *_args, **_kwargs):
            return response

    with pytest.raises(RuntimeError, match="options.*at most 2000 items"):
        flow_worker._api(Client(), "POST", "/progress")


def test_asap_download_observes_every_open_portal_page_and_uses_staging_folder():
    source = Path(__file__).parents[1].joinpath("app", "flow_worker.py").read_text()
    assert 'candidate.on("download", capture_download)' in source
    assert 'candidate.remove_listener("download", capture_download)' in source
    assert "download_page.expect_download" not in source
    assert "staged_file, export_pages = _asap_download" in source
    assert "export_page.close(" not in source
    assert "candidate for candidate in wizard_pages" in source
    assert "downloads_path=str(download_staging_dir)" in source
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
