from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import flow_portable, flow_recording
from app import flow_recording_runtime as range_runtime
from app.flow_recording_runtime import _select_week_range
from test_flows import flow_db


def _range_step(*, start="2026-W32", selector="button.week", selected_state="aria-pressed"):
    return {
        "id": "week-range",
        "action": "select_range",
        "page": "page",
        "locator": [{"method": "locator", "args": ["#weeks"], "kwargs": {}}],
        "range": {
            "unit": "week",
            "start": start,
            "end": "latest_selectable",
            "selection": "inclusive",
            "cell_selector": selector,
            "selected_state": selected_state,
            "navigation": {"kind": "scroll"},
        },
    }


def test_revision_save_promotes_range_definition_to_version_three(flow_db):
    import json

    from app import database
    from app.routers import flow_recordings as routes
    from test_flow_recordings import draft_job

    saved, _job = draft_job()
    submitted = {
        "version": 2,
        "timezone": "Asia/Dubai",
        "parameters": {},
        "steps": [_range_step()],
    }
    revision_id = routes.save_revision(
        saved["id"], routes.RevisionWrite(definition=submitted)
    )["revision_id"]
    with database.get_db() as db:
        stored = json.loads(
            db.execute(
                "SELECT definition_json FROM flow_recording_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()[0]
        )
    assert stored["version"] == 3
    flow_recording.validate_definition(stored, activation=False)


@contextmanager
def _page(markup):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(markup)
        try:
            yield page
        finally:
            browser.close()


def _week_button(week, *, selected=False, disabled=False):
    return (
        f'<button class="week" aria-label="{week}" aria-pressed="'
        f'{str(selected).lower()}" {"disabled" if disabled else ""} '
        "onclick=\"this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') !== 'true')\">"
        f"{week}</button>"
    )


def _box(buttons, *, year="2026"):
    return (
        f'<div id="weeks" aria-label="Weekly periods {year}" '
        'style="height:96px;overflow:auto">'
        + "".join(f'<div style="height:48px">{button}</div>' for button in buttons)
        + "</div>"
    )


def test_range_selects_new_week_and_skips_disabled_future_week():
    buttons = [
        _week_button("2026-W31"),
        _week_button("2026-W32", selected=True),
        _week_button("2026-W33"),
        _week_button("2026-W34"),
        _week_button("2026-W35", disabled=True),
    ]
    updates = []
    with _page(_box(buttons)) as page:
        result = _select_week_range(
            page.locator("#weeks"), _range_step(),
            lambda message, detail: updates.append((message, detail)),
        )
        states = page.locator("button.week").evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('aria-pressed'))"
        )

    assert result["start_week"] == "2026-W32"
    assert result["end_week"] == "2026-W34"
    assert result["selected_weeks"] == 3
    assert states == ["false", "true", "true", "true", "false"]
    assert any(detail.get("range_week") == "2026-W34" for _, detail in updates)


def test_range_fails_for_an_unexpected_selection_before_start():
    buttons = [_week_button("2026-W31", selected=True), _week_button("2026-W32")]
    with _page(_box(buttons)) as page, pytest.raises(RuntimeError, match="outside range"):
        _select_week_range(page.locator("#weeks"), _range_step(), lambda *_args: None)


def test_range_accepts_a_validation_proven_selected_class():
    markup = '''<div id="weeks" aria-label="Weekly periods 2026">
      <button class="week selected" aria-label="2026-W32" onclick="this.classList.toggle('selected')">W32</button>
      <button class="week" aria-label="2026-W33" onclick="this.classList.toggle('selected')">W33</button>
    </div>'''
    with _page(markup) as page:
        result = _select_week_range(
            page.locator("#weeks"), _range_step(selected_state="class:selected"), lambda *_args: None,
        )
    assert result["selected_state_signals"] == ["class:selected"]


@pytest.mark.parametrize("signal", ["checked", "aria-checked", "aria-selected"])
def test_range_accepts_supported_selected_state_signals(signal):
    if signal == "checked":
        cells = '<input class="week" type="checkbox" aria-label="2026-W32">'
    else:
        cells = (
            f'<button class="week" aria-label="2026-W32" {signal}="false" '
            f'onclick="this.setAttribute(\'{signal}\',\'true\')">W32</button>'
        )
    with _page(f'<div id="weeks" aria-label="Weekly periods 2026">{cells}</div>') as page:
        result = _select_week_range(
            page.locator("#weeks"), _range_step(selector=".week", selected_state=signal), lambda *_args: None,
        )
    assert result["selected_state_signals"] == [signal]


def test_range_uses_native_select_all_but_still_verifies_each_cell():
    markup = '''<div id="weeks" aria-label="Weekly periods 2026">
      <button class="all" aria-checked="false" onclick="this.setAttribute('aria-checked','true');document.querySelectorAll('.week').forEach(n=>n.setAttribute('aria-pressed','true'))">Select all</button>
      <button class="week" aria-label="2026-W32" aria-pressed="false">W32</button>
      <button class="week" aria-label="2026-W33" aria-pressed="false">W33</button>
    </div>'''
    step = _range_step()
    step["range"]["select_all_selector"] = "button.all"
    with _page(markup) as page:
        result = _select_week_range(page.locator("#weeks"), step, lambda *_args: None)
    assert result["selection_clicks"] == 0
    assert result["selected_weeks"] == 2


def test_range_reacquires_virtualized_cells_after_each_scroll():
    markup = '''<div id="weeks" aria-label="Weekly periods 2026" style="height:80px;overflow:auto;position:relative">
      <div class="viewport" style="position:sticky;top:0;height:40px;background:white;z-index:1"></div><div style="height:320px"></div>
    </div><script>
      const pages=[['2026-W32','2026-W33'],['2026-W34','2026-W35'],['2026-W36','2026-W37'],['2026-W38']];
      const box=document.querySelector('#weeks');
      function render(){const index=Math.min(pages.length-1,Math.floor(box.scrollTop/64));document.querySelector('.viewport').innerHTML=pages[index].map(w=>`<button class="week" aria-label="${w}" aria-pressed="false" ${w==='2026-W38'?'disabled':''} onclick="this.setAttribute('aria-pressed','true')">${w}</button>`).join('')}
      box.onscroll=render;render();
    </script>'''
    with _page(markup) as page:
        result = _select_week_range(page.locator("#weeks"), _range_step(), lambda *_args: None)
    assert result["end_week"] == "2026-W37"
    assert result["selected_weeks"] == 6


def test_range_traverses_validated_calendar_controls():
    markup = '''<div id="weeks" aria-label="Weekly periods 2026">
      <button class="prev" disabled>Previous</button><div class="page"></div><button class="next">Next</button>
    </div><script>
      const pages=[['2026-W32','2026-W33'],['2026-W34','2026-W35']];let index=0;
      function render(){document.querySelector('.page').innerHTML=pages[index].map(w=>`<button class="week" aria-label="${w}" aria-pressed="false" onclick="this.setAttribute('aria-pressed','true')">${w}</button>`).join('');document.querySelector('.prev').disabled=index===0;document.querySelector('.next').disabled=index===pages.length-1;}
      document.querySelector('.prev').onclick=()=>{index--;render()};document.querySelector('.next').onclick=()=>{index++;render()};render();
    </script>'''
    step = _range_step()
    step["range"]["navigation"] = {"kind": "controls", "previous_selector": ".prev", "next_selector": ".next"}
    with _page(markup) as page:
        result = _select_week_range(page.locator("#weeks"), step, lambda *_args: None)
    assert result["end_week"] == "2026-W35"
    assert result["navigation_movements"] == 1


def test_range_fails_when_calendar_navigation_stalls(monkeypatch):
    markup = '''<div id="weeks" aria-label="Weekly periods 2026">
      <button class="previous" disabled>Previous</button>
      <button class="week" aria-label="2026-W32" aria-pressed="false" onclick="this.setAttribute('aria-pressed','true')">W32</button>
      <button class="next">Next</button>
    </div>'''
    step = _range_step()
    step["range"]["navigation"] = {
        "kind": "controls", "previous_selector": ".previous", "next_selector": ".next",
    }
    monkeypatch.setattr(range_runtime, "_RANGE_REPAINT_TIMEOUT_SECONDS", 0.1)
    with _page(markup) as page, pytest.raises(RuntimeError, match="did not reveal"):
        _select_week_range(page.locator("#weeks"), step, lambda *_args: None)


def test_range_selection_honors_cancellation_from_progress_callback():
    def cancel(*_args):
        raise RuntimeError("Cancelled by user")

    with _page(_box([_week_button("2026-W32"), _week_button("2026-W33")])) as page:
        with pytest.raises(RuntimeError, match="Cancelled"):
            _select_week_range(page.locator("#weeks"), _range_step(), cancel)
        assert page.locator("button.week").evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('aria-pressed'))"
        ) == ["false", "false"]


def test_range_crosses_iso_year_boundary():
    buttons = [
        _week_button("2020-W52"),
        _week_button("2020-W53"),
        _week_button("2021-W01"),
        _week_button("2021-W02"),
    ]
    with _page(_box(buttons, year="2020 2021")) as page:
        result = _select_week_range(
            page.locator("#weeks"), _range_step(start="2020-W52"), lambda *_args: None,
        )
    assert result["end_week"] == "2021-W02"
    assert result["selected_weeks"] == 4


def test_range_uses_unique_container_year_for_week_only_labels():
    buttons = [_week_button("W32"), _week_button("W33")]
    with _page(_box(buttons)) as page:
        result = _select_week_range(
            page.locator("#weeks"), _range_step(), lambda *_args: None,
        )
    assert result["end_week"] == "2026-W33"


def test_range_rejects_week_only_labels_with_ambiguous_year_headings():
    buttons = [_week_button("W52"), _week_button("W01")]
    with _page(_box(buttons, year="2025 2026")) as page, \
            pytest.raises(RuntimeError, match="unambiguous nearby year"):
        _select_week_range(
            page.locator("#weeks"), _range_step(start="2025-W52"), lambda *_args: None,
        )


@pytest.mark.parametrize(
    ("buttons", "message"),
    [
        ([_week_button("2026-W32"), _week_button("2026-W32")], "more than once"),
        ([_week_button("2026-W32"), _week_button("2026-W34")], "missing selectable weeks"),
    ],
)
def test_range_fails_closed_for_duplicate_or_missing_weeks(buttons, message):
    with _page(_box(buttons)) as page, pytest.raises(RuntimeError, match=message):
        _select_week_range(page.locator("#weeks"), _range_step(), lambda *_args: None)


def test_range_requires_observable_selection_state():
    markup = '<div id="weeks"><button class="week" aria-label="2026-W32">W32</button></div>'
    with _page(markup) as page, pytest.raises(RuntimeError, match="readable selected state"):
        _select_week_range(
            page.locator("#weeks"), _range_step(selected_state="auto"), lambda *_args: None,
        )


def test_version_three_range_contract_and_legacy_readers():
    definition = {"version": 3, "timezone": "UTC", "parameters": {}, "steps": [
        {"id": "open", "action": "new_page", "page": "page"},
        _range_step(),
        {"id": "download", "action": "download", "page": "page", "locator": [],
         "steps": [{"id": "trigger", "action": "click", "page": "page", "locator": [
             {"method": "get_by_role", "args": ["button"], "kwargs": {"name": "Download"}},
         ]}], "output": {"format": "xlsx"}},
    ]}
    assert flow_recording.validate_definition(definition)["version"] == 3
    for version in (1, 2):
        legacy = {"version": version, "timezone": "UTC", "parameters": {}, "steps": [
            {"id": "open", "action": "new_page", "page": "page"},
            {"id": "download", "action": "download", "page": "page", "locator": [],
             "steps": [{"id": "trigger", "action": "click", "page": "page", "locator": [
                 {"method": "get_by_text", "args": ["Download"], "kwargs": {"exact": True}},
             ]}], "output": {"format": "xlsx"}},
        ]}
        assert flow_recording.validate_definition(legacy)["version"] == version
    assert "_select_week_range" in flow_portable.execution_sources()["flow_recording_runtime"]
