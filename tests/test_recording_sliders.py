"""Date range controls: recorded ``set_range`` steps driven by week parameters."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import flow_portable, flow_range_slider, flow_recording
from app import flow_recording_runtime as runtime
from app.flow_recording_runtime import _set_slider_range
from test_flows import flow_db


HANDLE_CLICK = {
    "id": "week-range", "action": "click", "page": "page",
    "locator": [{"method": "locator", "args": ["#week-prompt"], "kwargs": {}},
                {"method": "get_by_role", "args": ["slider"], "kwargs": {"name": "Week end"}}],
    "args": [], "kwargs": {},
}


def _slider_step(kind="week", week_days=None, levels=1):
    contract = {"kind": kind, "anchor_locator": HANDLE_CLICK["locator"],
                "container_ancestor_levels": levels, "source_step": HANDLE_CLICK}
    if week_days:
        contract["week_days"] = week_days
    return {"id": "week-range", "action": "set_range", "page": "page",
            "locator": [{"method": "locator", "args": ["#week-prompt"], "kwargs": {}}],
            "range": contract}


def _parameters(start=None, end=None):
    start = start or {"mode": "portal_default"}
    end = end or {"mode": "calculated", "expression": "latest_selectable", "offset_weeks": 0}
    return {"start": {"step_id": "week-range", "role": "start", "unit": "week", "format": "%G-W%V", **start},
            "end": {"step_id": "week-range", "role": "end", "unit": "week", "format": "%G-W%V", **end}}


def _month_parameters(start=None, end=None):
    start = start or {"mode": "calculated", "expression": "oldest_selectable", "offset_months": 0}
    end = end or {"mode": "calculated", "expression": "latest_selectable", "offset_months": 0}
    return {"start": {"step_id": "week-range", "role": "start", "unit": "month", "format": "%Y%m", **start},
            "end": {"step_id": "week-range", "role": "end", "unit": "month", "format": "%Y%m", **end}}


def _definition(step=None, parameters=None, version=4):
    return {"version": version, "timezone": "Asia/Dubai",
            "parameters": _parameters() if parameters is None else parameters,
            "steps": [{"id": "open", "action": "new_page", "page": "page"}, step or _slider_step(),
                      {"id": "download", "action": "download", "page": "page", "locator": [],
                       "steps": [{"id": "trigger", "action": "click", "page": "page", "locator": [
                           {"method": "get_by_role", "args": ["button"], "kwargs": {"name": "Export"}}]}],
                       "output": {"format": "xlsx"}}]}


def _isolate_flow_root(flow_db):
    from app import database
    # The PowerShell verifier supplies one shared external root for the run;
    # split it per test. CI leaves it unset and already derives a unique root
    # from each pytest tmp_path, so no override is needed there.
    base = os.environ.get("DG_FLOWS_ROOT")
    if not base:
        return
    root = Path(base) / (
        "recording-slider-" + hashlib.sha256(str(flow_db).encode()).hexdigest()[:12])
    with database.get_db() as db:
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('flows_root',?)", (str(root),))


# --- contract -----------------------------------------------------------------

def test_definition_with_date_range_control_validates_at_version_four():
    assert flow_recording.validate_definition(_definition())["version"] == 4
    with pytest.raises(ValueError, match="version 4"):
        flow_recording.validate_definition(_definition(version=3))
    assert flow_recording.VERSION == 5
    assert flow_recording.import_codegen(__import__("test_flow_recordings").CODEGEN)["version"] == 4


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda d: d["parameters"].pop("end"), "exactly one start and one end"),
    (lambda d: d["parameters"]["start"].update(role="end"), "exactly one start and one end"),
    (lambda d: d["parameters"]["start"].update(unit="day", format="%Y-%m-%d"), "matching week or month parameters"),
    (lambda d: d["parameters"]["end"].update(expression="latest_week"), "Unsupported week calculation"),
    (lambda d: d["parameters"]["end"].update(offset_weeks=521), "within ten years"),
    (lambda d: d["parameters"]["end"].update(offset_weeks="2"), "within ten years"),
    (lambda d: d["parameters"]["start"].update(mode="fixed", value="2025-W53"), "does not exist"),
    (lambda d: d["parameters"]["start"].update(mode="fixed", value="202601"), "YYYY-Www"),
    (lambda d: d["parameters"]["start"].update(format="%Y-%m-%d"), "Unsupported week format"),
    (lambda d: d["steps"][1]["range"].update(kind="quarter"), "weeks, dates or months"),
    (lambda d: d["steps"][1]["range"].update(week_days="friday"), "Sunday or Monday"),
    (lambda d: d["steps"][1]["range"].update(container_ancestor_levels=7), "1–6 parent levels"),
    (lambda d: d["steps"][1].update(locator=[]), "element box"),
    (lambda d: d["parameters"].update(extra={"step_id": "trigger", "role": "start", "unit": "week",
                                             "mode": "fixed", "value": "2026-W01"}), "belong to a date range control"),
])
def test_invalid_date_range_definitions_are_rejected(mutate, message):
    definition = _definition()
    mutate(definition)
    with pytest.raises(ValueError, match=message):
        flow_recording.validate_definition(definition)


def test_day_parameters_keep_their_existing_rules():
    from test_flow_recordings import definition
    value = definition()
    assert flow_recording.validate_definition(value)["version"] == 4
    value["parameters"]["start"]["unit"] = "week"
    with pytest.raises(ValueError, match="belong to a date range control"):
        flow_recording.validate_definition(value)


def test_month_range_contract_requires_version_five_and_month_parameters():
    definition = _definition(
        step=_slider_step(kind="month", levels=0), parameters=_month_parameters(), version=5)
    assert flow_recording.validate_definition(definition)["version"] == 5
    assert flow_recording.resolve_parameters(definition) == {"start": None, "end": None}
    with pytest.raises(ValueError, match="version 5"):
        flow_recording.validate_definition({**definition, "version": 4})
    wrong = _definition(step=_slider_step(kind="month", levels=0), version=5)
    with pytest.raises(ValueError, match="needs month parameters"):
        flow_recording.validate_definition(wrong)
    fixed = _definition(step=_slider_step(kind="month", levels=0), parameters=_month_parameters(
        start={"mode": "fixed", "value": "202601"},
        end={"mode": "fixed", "value": "202612"}), version=5)
    assert flow_recording.resolve_parameters(fixed) == {"start": "202601", "end": "202612"}
    fixed["parameters"]["end"]["value"] = "202613"
    with pytest.raises(ValueError, match="YYYYMM"):
        flow_recording.validate_definition(fixed)


def test_month_ordinals_cross_year_boundaries_one_month_at_a_time():
    assert flow_range_slider.slider_ordinal("202701", "month") - flow_range_slider.slider_ordinal("202612", "month") == 1
    assert flow_recording.add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)


def test_revision_save_promotes_date_range_definition_to_version_four(flow_db):
    from app import database
    from app.routers import flow_recordings as routes
    from test_flow_recordings import draft_job

    _isolate_flow_root(flow_db)
    saved, _job = draft_job()
    submitted = {**_definition(version=2)}
    revision_id = routes.save_revision(saved["id"], routes.RevisionWrite(definition=submitted))["revision_id"]
    with database.get_db() as db:
        stored = json.loads(db.execute(
            "SELECT definition_json FROM flow_recording_revisions WHERE id=?", (revision_id,)).fetchone()[0])
    assert stored["version"] == 4
    flow_recording.validate_definition(stored, activation=False)


def test_v3_worker_cannot_claim_version_four_work(flow_db):
    from app import database
    from app.routers import flows
    from test_flow_recordings import draft_job

    _isolate_flow_root(flow_db)
    saved, job = draft_job()
    job["recording"]["definition"] = _definition(version=4)
    assert job["recording"]["definition"]["version"] == 4
    with database.get_db() as db:
        db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','queued',?,'2026-09-17')",
                   (saved["id"], json.dumps(job)))
    capabilities = {"headed": True, "recorded_flows_v1": True, "recorded_flows_v2": True, "recorded_flows_v3": True,
                    "browser_switch_v1": True, "shared_flow_artifacts": True}
    flows.register_worker(flows.WorkerRegister(worker_id="worker", display_name="Worker", capabilities=capabilities))
    assert flows.claim_run("worker").get("run") is None
    capabilities["recorded_flows_v4"] = True
    flows.register_worker(flows.WorkerRegister(worker_id="worker", display_name="Worker", capabilities=capabilities))
    assert flows.claim_run("worker").get("run") is not None


def test_v4_worker_cannot_claim_month_range_work(flow_db):
    from app import database
    from app.routers import flows
    from test_flow_recordings import draft_job

    _isolate_flow_root(flow_db)
    saved, job = draft_job()
    job["recording"]["definition"] = _definition(
        step=_slider_step(kind="month", levels=0), parameters=_month_parameters(), version=5)
    with database.get_db() as db:
        db.execute("INSERT INTO flow_runs(flow_id,trigger_type,status,job_json,created_at) VALUES (?,'manual','queued',?,'2026-09-19')",
                   (saved["id"], json.dumps(job)))
    capabilities = {"headed": True, "recorded_flows_v1": True, "recorded_flows_v2": True,
                    "recorded_flows_v3": True, "recorded_flows_v4": True,
                    "browser_switch_v1": True, "shared_flow_artifacts": True}
    flows.register_worker(flows.WorkerRegister(worker_id="worker", display_name="Worker", capabilities=capabilities))
    assert flows.claim_run("worker").get("run") is None
    capabilities["recorded_flows_v5"] = True
    flows.register_worker(flows.WorkerRegister(worker_id="worker", display_name="Worker", capabilities=capabilities))
    assert flows.claim_run("worker").get("run") is not None


# --- resolution ---------------------------------------------------------------

def test_week_parameters_resolve_calendar_weeks_in_dubai_time():
    definition = _definition(parameters=_parameters(
        start={"mode": "calculated", "expression": "current_week", "offset_weeks": -3},
        end={"mode": "calculated", "expression": "previous_week"}))
    resolved = flow_recording.resolve_parameters(definition, now=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc))
    assert resolved == {"start": "2025-W50", "end": "2025-W52"}
    # Sunday evening in UTC is already Monday in Dubai: the week turns there.
    turned = flow_recording.resolve_parameters(
        _definition(parameters=_parameters(start={"mode": "calculated", "expression": "current_week"})),
        now=datetime(2025, 12, 28, 20, 30, tzinfo=timezone.utc))
    assert turned["start"] == "2026-W01"
    assert turned["end"] is None  # the newest selectable week exists only on the live control


def test_calendar_weeks_follow_the_control_week_convention_on_a_sunday():
    # Sunday 24 May 2026, 04:30 in Dubai: an ASAP (Sunday-start) week has already turned.
    sunday = datetime(2026, 5, 24, 0, 30, tzinfo=timezone.utc)
    parameters = _parameters(start={"mode": "calculated", "expression": "current_week"},
                             end={"mode": "calculated", "expression": "previous_week"})
    by_default = flow_recording.resolve_parameters(_definition(parameters=parameters), now=sunday)
    assert by_default == {"start": "2026-W22", "end": "2026-W21"}
    explicit = flow_recording.resolve_parameters(
        _definition(step=_slider_step(kind="date", week_days="sunday"), parameters=parameters), now=sunday)
    assert explicit == {"start": "2026-W22", "end": "2026-W21"}
    iso = flow_recording.resolve_parameters(
        _definition(step=_slider_step(week_days="monday"), parameters=parameters), now=sunday)
    assert iso == {"start": "2026-W21", "end": "2026-W20"}
    # Any other day of the week is the same under both conventions.
    wednesday = datetime(2026, 5, 27, 0, 30, tzinfo=timezone.utc)
    assert flow_recording.resolve_parameters(_definition(parameters=parameters), now=wednesday)["start"] == "2026-W22"
    assert flow_recording.resolve_parameters(
        _definition(step=_slider_step(week_days="monday"), parameters=parameters), now=wednesday)["start"] == "2026-W22"


def test_week_parameters_accept_fixed_values_and_overrides_in_their_own_format():
    compact = _definition(parameters=_parameters(
        start={"mode": "fixed", "value": "202601", "format": "%G%V"},
        end={"mode": "calculated", "expression": "latest_selectable", "format": "%G%V"}))
    assert flow_recording.resolve_parameters(compact) == {"start": "202601", "end": None}
    assert flow_recording.resolve_parameters(compact, {"end": "202610"})["end"] == "202610"
    with pytest.raises(ValueError, match="YYYYWW"):
        flow_recording.resolve_parameters(compact, {"end": "2026-W10"})
    with pytest.raises(ValueError, match="YYYY-Www"):
        flow_recording.resolve_parameters(_definition(), {"end": "202610"})
    assert flow_recording.parse_parameter_value({"unit": "week"}, "2026-W22") == datetime(2026, 5, 25)
    assert flow_recording.format_week(date(2026, 5, 27), "%G%V") == "202622"


def test_week_conventions_map_dates_to_weeks_and_back():
    assert flow_range_slider.week_of(date(2026, 5, 24), "sunday") == date(2026, 5, 25)
    assert flow_range_slider.week_of(date(2026, 5, 24), "monday") == date(2026, 5, 18)
    assert flow_range_slider.week_bounds(date(2026, 5, 25), "sunday") == (date(2026, 5, 24), date(2026, 5, 30))
    assert flow_range_slider.week_bounds(date(2026, 5, 25), "monday") == (date(2026, 5, 25), date(2026, 5, 31))
    assert flow_range_slider.week_dates("2026-W22") == ("20260524", "20260530")


# --- playback -----------------------------------------------------------------

@contextmanager
def _page(markup):
    from playwright.sync_api import sync_playwright
    from test_python_scripts_preview import launch

    with sync_playwright() as playwright:
        # Chrome channel first, as in CI; a checkout without it falls back to the bundled Chromium.
        browser = launch(playwright)
        page = browser.new_page()
        page.set_content(markup)
        try:
            yield page
        finally:
            browser.close()


def _slider_page(values, current, *, handles=2, delay_ms=0, declared_max=None):
    """A keyboard-driven two-handle range whose only truth is aria-valuetext.

    ``delay_ms`` publishes each new value that long after the key press, as a
    control that recomputes asynchronously would; ``declared_max`` sets an
    aria-valuemax on the upper handle.
    """
    markup = ['<div id="week-prompt"><span>Week:</span>']
    for index, value in enumerate(current[:handles]):
        name = "Week start" if index == 0 else "Week end"
        text = f' aria-valuetext="{value}"' if value else ""
        maximum = f' aria-valuemax="{declared_max}"' if declared_max and index == handles - 1 else ""
        markup.append(f'<div role="slider" tabindex="0" aria-label="{name}"{text}{maximum} '
                      'style="display:inline-block;width:16px;height:16px;background:#888"></div>')
    markup.append("</div><script>")
    markup.append(f"const values={json.dumps(values)};const delay={int(delay_ms)};")
    markup.append('''const handles=[...document.querySelectorAll('[role=slider]')];
      const pending=new Map();
      const index=h=>values.indexOf(pending.get(h)??h.getAttribute('aria-valuetext'));
      handles.forEach((h,i)=>h.addEventListener('keydown',e=>{
        const lower=index(handles[0]),upper=index(handles[handles.length-1]);let next=index(h);
        if(e.key==='ArrowRight')next+=1;else if(e.key==='ArrowLeft')next-=1;else if(e.key==='Home')next=0;else if(e.key==='End')next=values.length-1;else return;
        const min=i===0?0:lower,max=i===0?upper:values.length-1;
        next=Math.max(min,Math.min(max,next));
        const value=values[next];
        if(delay){pending.set(h,value);setTimeout(()=>{h.setAttribute('aria-valuetext',value);pending.delete(h);},delay);}
        else h.setAttribute('aria-valuetext',value);
      }));</script>''')
    return "".join(markup)


WEEKS = flow_range_slider.week_options("202501", "202633")
MONTHS = [f"2026{month:02d}" for month in range(1, 10)]


def _handles(page):
    return page.locator("[role=slider]").evaluate_all("nodes => nodes.map(n => n.getAttribute('aria-valuetext'))")


def test_month_range_expands_fully_and_auto_detects_the_two_handle_container():
    definition = _definition(
        step=_slider_step(kind="month", levels=0), parameters=_month_parameters(), version=5)
    # Model a noUi slider: the recording lands on the touch area inside one
    # handle, while the smallest shared ancestor containing both handles is
    # several levels above it.
    markup = _slider_page(MONTHS, ["202608", "202609"]).replace(
        '<div id="week-prompt"><span>Week:</span>',
        '<div id="week-prompt"><span>Month:</span><div class="recorded"><span class="touch" '
        'style="display:inline-block;width:8px;height:8px"></span></div>')
    updates = []
    with _page(markup) as page:
        # Put the recorded touch target into the first handle before playback.
        page.locator("[role=slider]").first.evaluate(
            "(handle)=>handle.append(document.querySelector('.recorded'))")
        result = _set_slider_range(
            page.locator(".touch"), definition["steps"][1], definition,
            flow_recording.resolve_parameters(definition),
            lambda message, detail: updates.append((message, detail)),
        )
        assert _handles(page) == ["202601", "202609"]
    assert result["control_values"] == ["202601", "202609"]
    assert result["actual"] == {"start": "202601", "end": "202609"}
    assert result["oldest_selectable"] == "202601"
    assert result["latest_selectable"] == "202609"
    assert result["container_ancestor_levels"] >= 2
    phases = [detail["phase"] for _, detail in updates]
    assert phases[:4] == ["range_container", "range_read", "range_oldest", "range_latest"]


def test_auto_detection_fails_closed_without_one_two_handle_ancestor():
    definition = _definition(
        step=_slider_step(kind="month", levels=0), parameters=_month_parameters(), version=5)
    with _page(_slider_page(MONTHS, ["202608"], handles=1)) as page, \
            pytest.raises(RuntimeError, match="Could not find one range control"):
        _set_slider_range(
            page.locator("[role=slider]"), definition["steps"][1], definition,
            flow_recording.resolve_parameters(definition), lambda *_args: None,
        )


def test_fixed_start_and_newest_selectable_end_move_and_verify_the_handles():
    definition = _definition(parameters=_parameters(start={"mode": "fixed", "value": "2026-W20"}))
    parameters = flow_recording.resolve_parameters(definition)
    updates = []
    with _page(_slider_page(WEEKS, ["202622", "202630"])) as page:
        result = _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                                   lambda message, detail: updates.append((message, detail)))
        assert _handles(page) == ["202620", "202633"]
    assert result["control_values"] == ["202620", "202633"]
    assert result["actual"] == {"start": "2026-W20", "end": "2026-W33"}
    assert result["live"] == {"end": True}
    assert result["latest_selectable"] == "2026-W33"
    assert [detail["phase"] for _, detail in updates][:3] == ["range_read", "range_latest", "range_move"]


def test_newest_selectable_week_waits_for_a_slowly_updating_control():
    definition = _definition(parameters=_parameters(start={"mode": "fixed", "value": "2026-W20"}))
    parameters = flow_recording.resolve_parameters(definition)
    with _page(_slider_page(WEEKS, ["202622", "202630"], delay_ms=120)) as page:
        result = _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                                   lambda *_args: None)
        assert _handles(page) == ["202620", "202633"]
    assert result["latest_selectable"] == "2026-W33"
    assert result["actual"] == {"start": "2026-W20", "end": "2026-W33"}


def test_newest_selectable_week_must_match_a_declared_maximum():
    definition = _definition(parameters=_parameters(start={"mode": "fixed", "value": "2026-W20"}))
    parameters = flow_recording.resolve_parameters(definition)
    with _page(_slider_page(WEEKS, ["202622", "202630"], declared_max="202640")) as page, \
            pytest.raises(RuntimeError, match="declares 202640"):
        _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                          lambda *_args: None)
    with _page(_slider_page(WEEKS, ["202622", "202630"], declared_max="202633")) as page:
        result = _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                                   lambda *_args: None)
    assert result["latest_selectable"] == "2026-W33"


def test_read_extreme_rejects_a_control_that_never_settles(monkeypatch):
    monkeypatch.setattr(flow_range_slider, "SETTLE_ATTEMPTS", 6)

    class Handle:
        def __init__(self):
            self.value = 202600

        def get_attribute(self, name):
            self.value += 1
            return str(self.value) if name == "aria-valuetext" else None

        def inner_text(self):
            return ""

        def press(self, key):
            pass

    class Scope:
        def inner_text(self):
            return ""

    with pytest.raises(RuntimeError, match="did not settle"):
        flow_range_slider.read_extreme(Scope(), [Handle(), Handle()], "week", end=True)


def test_rolling_window_counts_back_from_the_newest_selectable_week():
    definition = _definition(parameters=_parameters(
        start={"mode": "calculated", "expression": "latest_selectable", "offset_weeks": -7}))
    parameters = flow_recording.resolve_parameters(definition)
    assert parameters == {"start": None, "end": None}
    with _page(_slider_page(WEEKS, ["202622", "202630"])) as page:
        result = _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                                   lambda *_args: None)
        assert _handles(page) == ["202626", "202633"]
    assert result["actual"] == {"start": "2026-W26", "end": "2026-W33"}
    assert result["live"] == {"start": True, "end": True}


def test_portal_default_start_keeps_the_portal_value_and_records_it():
    definition = _definition(parameters=_parameters(end={"mode": "fixed", "value": "202630", "format": "%G%V"}))
    parameters = flow_recording.resolve_parameters(definition)
    with _page(_slider_page(WEEKS, ["202622", "202625"])) as page:
        result = _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                                   lambda *_args: None)
        assert _handles(page) == ["202622", "202630"]
    assert result["actual"] == {"start": "2026-W22", "end": "202630"}
    assert result["live"] == {"start": True}


@pytest.mark.parametrize(("week_days", "expected"), [
    ("sunday", ["20260524", "20260606"]),
    ("monday", ["20260525", "20260607"]),
])
def test_date_control_sets_the_calendar_days_of_the_week_parameters(week_days, expected):
    days = [(date(2026, 5, 1) + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(120)]
    definition = _definition(step=_slider_step(kind="date", week_days=week_days), parameters=_parameters(
        start={"mode": "fixed", "value": "2026-W22"}, end={"mode": "fixed", "value": "2026-W23"}))
    parameters = flow_recording.resolve_parameters(definition)
    with _page(_slider_page(days, ["20260601", "20260615"])) as page:
        result = _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition, parameters,
                                   lambda *_args: None)
        assert _handles(page) == expected
    assert result["actual"] == {"start": "2026-W22", "end": "2026-W23"}


def test_date_control_fails_closed_when_its_newest_week_is_incomplete():
    # The control ends on Tuesday 2026-06-09 inside week 24 (Sunday 7 June to Saturday 13 June);
    # the week's last day is beyond the control, so the range cannot be proven.
    days = [(date(2026, 5, 1) + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(40)]
    definition = _definition(step=_slider_step(kind="date"), parameters=_parameters(
        start={"mode": "calculated", "expression": "latest_selectable", "offset_weeks": -1}))
    with _page(_slider_page(days, ["20260601", "20260603"])) as page, pytest.raises(RuntimeError, match="mismatch"):
        _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition,
                          flow_recording.resolve_parameters(definition), lambda *_args: None)


def test_range_beyond_the_control_fails_before_any_download():
    definition = _definition(parameters=_parameters(
        start={"mode": "fixed", "value": "2026-W38"}, end={"mode": "fixed", "value": "2026-W40"}))
    with _page(_slider_page(WEEKS, ["202622", "202630"])) as page, pytest.raises(RuntimeError, match="mismatch"):
        _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition,
                          flow_recording.resolve_parameters(definition), lambda *_args: None)


def test_end_before_start_is_rejected_without_touching_the_control():
    definition = _definition(parameters=_parameters(
        start={"mode": "fixed", "value": "2026-W30"}, end={"mode": "fixed", "value": "2026-W20"}))
    with _page(_slider_page(WEEKS, ["202622", "202630"])) as page:
        with pytest.raises(RuntimeError, match="before its start"):
            _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition,
                              flow_recording.resolve_parameters(definition), lambda *_args: None)
        assert _handles(page) == ["202622", "202630"]


def test_a_box_without_two_handles_fails():
    definition = _definition()
    with _page(_slider_page(WEEKS, ["202622"], handles=1)) as page, \
            pytest.raises(RuntimeError, match="exactly two visible slider handles"):
        _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition,
                          flow_recording.resolve_parameters(definition), lambda *_args: None)


def test_portal_default_needs_a_readable_handle_value():
    definition = _definition()
    with _page(_slider_page(WEEKS, ["", "202630"])) as page, \
            pytest.raises(RuntimeError, match="current start value"):
        _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition,
                          flow_recording.resolve_parameters(definition), lambda *_args: None)


def test_cancellation_from_the_progress_callback_stops_before_the_handles_move():
    definition = _definition(parameters=_parameters(start={"mode": "fixed", "value": "2026-W20"}))

    def cancel(message, detail):
        if detail.get("phase") == "range_move":
            raise RuntimeError("Cancelled by user")

    with _page(_slider_page(WEEKS, ["202622", "202630"])) as page:
        with pytest.raises(RuntimeError, match="Cancelled"):
            _set_slider_range(page.locator("#week-prompt"), definition["steps"][1], definition,
                              flow_recording.resolve_parameters(definition), cancel)
        assert _handles(page) == ["202622", "202633"]  # only the newest-week probe ran


def test_acquire_plays_the_date_range_control_and_records_its_parameters(tmp_path):
    definition = _definition(parameters=_parameters(start={"mode": "fixed", "value": "2026-W20"}))
    job = {"recording": {"definition": definition, "revision": 1},
           "recording_parameters": flow_recording.resolve_parameters(definition)}
    events = []

    def progress(status, detail, *args):
        events.append(detail)
        if detail.get("step_id") == "download" and detail.get("outcome") == "started":
            raise RuntimeError("Stop before download")

    with _page(_slider_page(WEEKS, ["202622", "202630"])) as page:
        with pytest.raises(RuntimeError, match="Stop before download"):
            runtime.acquire(page, job, progress, tmp_path, tmp_path / "staging",
                            target=tmp_path / "output", run_id=1, artifacts=[])
        assert _handles(page) == ["202620", "202633"]
    completed = next(e for e in events if e["step_id"] == "week-range" and e["outcome"] == "completed")
    assert completed["message"] == "Set week range 202620 through 202633 (2026-W20 to 2026-W33)."
    assert completed["confirmation"] == "exact_range"
    phases = [e["diagnostic"]["phase"] for e in events if e["step_id"] == "week-range"]
    assert "range_latest" in phases and "range_handle" in phases


def test_portable_scripts_embed_the_shared_slider_driver():
    sources = flow_portable.execution_sources()
    assert "_set_slider_range" in sources["flow_recording_runtime"]
    assert "def set_range_values" in sources["flow_range_slider"]
    assert "def find_handles" in sources["flow_range_slider"]
