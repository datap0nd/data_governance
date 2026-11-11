"""Regression tests for the replay and recording audit findings.

Every test here failed on the code it was written against and pins the
corrected behaviour. Fixtures are synthetic: no portal, Power BI tenant or
mailbox is reached, and every host name is a reserved example name.
"""

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import flow_replay, flow_worker


# ── F-02: network replay is keyed by the Flow and its report filters ──


TASK_KEY = json.dumps({"export_view": None, "period_key": "2026-W30"}, sort_keys=True)
EXPORT_URL = "https://portal.example.internal/export/csv"


def _replay_job(selections=None, flow_id=11):
    job = {
        "site": {"id": 7, "name": "ASAP", "adapter": "asap_portal"},
        "report": {"id": 42, "name": "Weekly sell-out"},
        "downloads": {"file_format": "csv"},
    }
    if flow_id is not None:
        job["flow"] = {"id": flow_id, "name": "Weekly sell-out"}
    if selections is not None:
        job["selections"] = selections
    return job


def _store_recipe(profile: Path, job: dict) -> None:
    staged = profile / "staged.csv"
    profile.mkdir(parents=True, exist_ok=True)
    staged.write_text("week,region,qty\n" + "2026-W30,MENA,5\n" * 40, encoding="utf-8")
    recorder = SimpleNamespace(best_request=lambda: {
        "url": EXPORT_URL, "method": "POST",
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "post_b64": base64.b64encode(b"region=MENA").decode("ascii"),
    })
    assert flow_replay.store_capture(profile, job, TASK_KEY, recorder, staged)


def test_f02_same_selections_in_another_order_share_one_recipe_key():
    saved = _replay_job({"region": ["MENA", "EU"], "channel": "Retail", "brand": ["B", "A"]})
    reordered = _replay_job({"brand": ["A", "B"], "channel": "Retail", "region": ["EU", "MENA"]})

    assert flow_replay.recipe_key(saved, TASK_KEY) == flow_replay.recipe_key(reordered, TASK_KEY)


@pytest.mark.parametrize("other", [
    {"region": ["MENA"], "channel": "Retail"},
    {"region": ["MENA", "EU"], "channel": "Online"},
    {"region": ["MENA", "EU"]},
    {},
])
def test_f02_different_selections_never_share_a_recipe_key(other):
    saved = _replay_job({"region": ["MENA", "EU"], "channel": "Retail"})

    assert flow_replay.recipe_key(saved, TASK_KEY) != flow_replay.recipe_key(
        _replay_job(other), TASK_KEY,
    )


def test_f02_two_flows_on_one_report_never_share_a_recipe_key():
    selections = {"region": ["MENA"]}

    assert flow_replay.recipe_key(_replay_job(selections, flow_id=11), TASK_KEY) != (
        flow_replay.recipe_key(_replay_job(selections, flow_id=12), TASK_KEY)
    )
    # A job without a Flow section still produces a stable key of its own.
    orphan = flow_replay.recipe_key(_replay_job(selections, flow_id=None), TASK_KEY)
    assert orphan == flow_replay.recipe_key(_replay_job(selections, flow_id=None), TASK_KEY)
    assert orphan != flow_replay.recipe_key(_replay_job(selections, flow_id=11), TASK_KEY)


def test_f02_recipe_stored_for_one_selection_set_is_not_loaded_for_another(tmp_path):
    recorded = _replay_job({"region": ["MENA", "EU"]})
    _store_recipe(tmp_path, recorded)

    assert flow_replay.load_recipe(tmp_path, recorded, TASK_KEY)["url"] == EXPORT_URL
    assert flow_replay.load_recipe(
        tmp_path, _replay_job({"region": ["EU", "MENA"]}), TASK_KEY,
    )["url"] == EXPORT_URL
    assert flow_replay.load_recipe(tmp_path, _replay_job({"region": ["EU"]}), TASK_KEY) is None
    assert flow_replay.load_recipe(tmp_path, _replay_job({}), TASK_KEY) is None
    assert flow_replay.load_recipe(
        tmp_path, _replay_job({"region": ["MENA", "EU"]}, flow_id=12), TASK_KEY,
    ) is None


def test_f02_recipe_keyed_without_the_flow_filters_is_never_found_again(tmp_path):
    """A recipe saved by the old key simply stops matching: the browser re-records."""
    job = _replay_job({"region": ["MENA"]})
    old_key = json.dumps({
        "site": 7, "report": 42, "task": TASK_KEY, "asap_download_type": None,
        "export_report_title": None, "export_filter_details": None,
    }, sort_keys=True)
    tmp_path.joinpath(flow_replay.RECIPES_FILENAME).write_text(json.dumps({old_key: {
        "version": 2, "url": EXPORT_URL, "method": "GET", "headers": {}, "post_b64": None,
        "captured_at": datetime.now(timezone.utc).isoformat(), "expected_kind": "text",
    }}), encoding="utf-8")

    assert flow_replay.load_recipe(tmp_path, job, TASK_KEY) is None


def test_f02_worker_does_not_replay_another_filter_selection(tmp_path, monkeypatch):
    """The run with edited filters drives the browser instead of the stale request."""
    target = tmp_path / "target"
    target.mkdir()
    profile = tmp_path / "profile"

    def job(selections):
        return {
            "flow": {"id": 1, "name": "Weekly"},
            "site": {"id": 7, "name": "ASAP", "adapter": "asap_portal"},
            "report": {"id": 42, "name": "Weekly", "filters": [], "export_views": []},
            "selections": selections,
            "downloads": {
                "periods": [None], "target_folder": str(target), "file_format": "csv",
                "filename_template": "weekly_export.csv",
            },
        }

    recorded = job({"region": ["MENA"]})
    profile.mkdir()
    (profile / flow_replay.RECIPES_FILENAME).write_text(json.dumps({
        flow_replay.recipe_key(recorded, flow_worker._export_task_key(None, None)): {
            "version": flow_replay.RECIPE_VERSION, "url": EXPORT_URL, "method": "GET",
            "headers": {}, "post_b64": None,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "expected_kind": "text", "baseline_bytes": 10_000,
        },
    }), encoding="utf-8")

    replayed = []

    class Fetcher:
        def fetch(self, url, **kwargs):
            replayed.append(url)
            body = ("week,region,qty\n" + "2026-W30,MENA,5\n" * 100).encode()
            return SimpleNamespace(ok=True, body=lambda: body, dispose=lambda: None)

    class Context:
        request = Fetcher()
        pages = []

        def on(self, *_args):
            pass

        def remove_listener(self, *_args):
            pass

    monkeypatch.setattr(flow_worker, "_asap_open_report", lambda *_args: object())
    monkeypatch.setattr(
        flow_worker, "_asap_activate_export_view", lambda _page, frame, label: (frame, label),
    )
    monkeypatch.setattr(flow_worker, "_asap_apply_configuration", lambda *_args: None)
    monkeypatch.setattr(flow_worker, "_has_named_control", lambda *_args: False)

    def browser_download(_page, _frame, _job, staging_dir, **_kwargs):
        path = Path(staging_dir) / "source.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("week,region,qty\n2026-W30,EU,1\n", encoding="utf-8")
        return path, []

    monkeypatch.setattr(flow_worker, "_asap_download", browser_download)

    artifacts, _timings = flow_worker.execute_job(
        SimpleNamespace(context=Context()), job({"region": ["EU"]}), lambda *_args: None,
        profile, tmp_path / "staging", run_id=9, register_folder=lambda _folder: {"ops": []},
    )

    assert replayed == []
    assert [item["export_transport"] for item in artifacts] == ["browser"]
    assert "EU" in Path(artifacts[0]["file_path"]).read_text(encoding="utf-8-sig")


# ── F-13: copying a recording keeps the source's own recording version ──


from app import database, flow_recording  # noqa: E402
from app.routers import flows, flow_recordings as recording_routes  # noqa: E402
from test_flows import flow_db, _request  # noqa: E402,F401
from test_flow_recordings import definition as plain_definition, draft_job  # noqa: E402

_DOWNLOAD_STEP = {
    "id": "download", "action": "download", "page": "page", "locator": [],
    "steps": [{"id": "trigger", "action": "click", "page": "page", "locator": [
        {"method": "get_by_role", "args": ["button"], "kwargs": {"name": "Export"}}]}],
    "output": {"format": "xlsx"},
}
_HANDLE = [
    {"method": "locator", "args": ["#week-prompt"], "kwargs": {}},
    {"method": "get_by_role", "args": ["slider"], "kwargs": {"name": "Week end"}},
]


def _range_control_definition(kind="week"):
    """A recording with a ``set_range`` step: version 4 for weeks, 5 for months."""
    unit, fmt, offset = (
        ("month", "%Y%m", "offset_months") if kind == "month" else ("week", "%G-W%V", "offset_weeks")
    )
    end = {"mode": "calculated", "expression": "latest_selectable", offset: 0}
    start = (
        {"mode": "calculated", "expression": "oldest_selectable", offset: 0}
        if kind == "month" else {"mode": "portal_default"}
    )
    return {
        "version": 2, "timezone": "Asia/Dubai",
        "parameters": {
            "start": {"step_id": "range", "role": "start", "unit": unit, "format": fmt, **start},
            "end": {"step_id": "range", "role": "end", "unit": unit, "format": fmt, **end},
        },
        "steps": [
            {"id": "open", "action": "new_page", "page": "page"},
            {"id": "range", "action": "set_range", "page": "page",
             "locator": [{"method": "locator", "args": ["#week-prompt"], "kwargs": {}}],
             "range": {"kind": kind, "anchor_locator": _HANDLE, "container_ancestor_levels": 1,
                       "source_step": {"id": "range", "action": "click", "page": "page",
                                       "locator": _HANDLE, "args": [], "kwargs": {}}}},
            _DOWNLOAD_STEP,
        ],
    }


def _week_box_definition():
    """A recording with a ``select_range`` step, which needs version 3."""
    return {
        "version": 2, "timezone": "Asia/Dubai", "parameters": {},
        "steps": [
            {"id": "open", "action": "new_page", "page": "page"},
            {"id": "weeks", "action": "select_range", "page": "page",
             "locator": [{"method": "locator", "args": ["#weeks"], "kwargs": {}}],
             "range": {"unit": "week", "start": "2026-W32", "end": "latest_selectable",
                       "selection": "inclusive", "cell_selector": "button.week",
                       "selected_state": "aria-pressed", "navigation": {"kind": "scroll"}}},
            _DOWNLOAD_STEP,
        ],
    }


def _save_recording(flow_id, value):
    return recording_routes.save_revision(
        flow_id, recording_routes.RevisionWrite(definition=value),
    )["revision_id"]


def _stored_definition(revision_id):
    with database.get_db() as db:
        return json.loads(db.execute(
            "SELECT definition_json FROM flow_recording_revisions WHERE id=?", (revision_id,),
        ).fetchone()[0])


def _destination_flow(site_id, name="Second flow"):
    return recording_routes.create_draft(recording_routes.RecordingDraft(
        name=name, site_id=site_id, report_url="http://localhost/other"), _request())


@pytest.mark.parametrize(("build", "version", "action"), [
    (_week_box_definition, 3, "select_range"),
    (_range_control_definition, 4, "set_range"),
    (lambda: _range_control_definition("month"), 5, "set_range"),
])
def test_f13_copying_a_recording_with_a_range_step_keeps_its_version(flow_db, build, version, action):
    source, job = draft_job()
    revision = _save_recording(source["id"], build())
    assert _stored_definition(revision)["version"] == version
    destination = _destination_flow(job["site"]["id"])

    copied = recording_routes.copy_template(
        destination["id"], recording_routes.TemplateCopy(source_flow_id=source["id"]),
    )

    stored = _stored_definition(copied["revision_id"])
    assert stored["version"] == version and stored["timezone"] == "Asia/Dubai"
    assert stored["steps"] == _stored_definition(revision)["steps"]
    assert stored["parameters"] == _stored_definition(revision)["parameters"]
    assert any(step["action"] == action for step in flow_recording.walk_steps(stored["steps"]))
    # The copy is a usable draft: it still passes activation-strength validation.
    flow_recording.validate_definition(stored)


def test_f13_plain_copy_is_not_downgraded_and_still_forces_dubai_time(flow_db):
    source, job = draft_job()
    revision = _save_recording(source["id"], plain_definition())
    recorded = _stored_definition(revision)
    assert recorded["version"] == 4  # what a new recording is imported as
    with database.get_db() as db:
        db.execute(
            "UPDATE flow_recording_revisions SET definition_json=? WHERE id=?",
            (flow_recording.canonical({**recorded, "timezone": "Europe/Lisbon"}), revision),
        )
    destination = _destination_flow(job["site"]["id"])

    copied = recording_routes.copy_template(
        destination["id"], recording_routes.TemplateCopy(source_flow_id=source["id"]),
    )

    stored = _stored_definition(copied["revision_id"])
    assert stored["version"] == 4
    assert stored["timezone"] == "Asia/Dubai"


def test_f13_replicated_flow_copies_a_range_recording(flow_db):
    source, _job = draft_job()
    revision = _save_recording(source["id"], _range_control_definition())
    with database.get_db() as db:
        db.execute("UPDATE flows SET recording_revision_id=? WHERE id=?", (revision, source["id"]))
        original = flows._flow_out(db, source["id"])
    replica = flows.create_flow(flows.FlowWrite.model_validate({
        **original, "name": "Portable sales copy", "enabled": False,
        "recording_revision_id": None, "target_folder": None,
    }), _request())

    copied = recording_routes.copy_template(
        replica["id"], recording_routes.TemplateCopy(source_flow_id=source["id"]),
    )

    stored = _stored_definition(copied["revision_id"])
    assert stored["version"] == 4
    assert [step["action"] for step in stored["steps"]] == ["new_page", "set_range", "download"]
    with database.get_db() as db:
        row = db.execute(
            "SELECT flow_id, status FROM flow_recording_revisions WHERE id=?", (copied["revision_id"],),
        ).fetchone()
    assert dict(row) == {"flow_id": replica["id"], "status": "draft"}


# ── F-14: a table cut off at the export row limit is named in the email and run ──


from app.routers import recurrences  # noqa: E402
from test_recurrences import _seed_recurrence  # noqa: E402

_ALERT_CSV = "Subsidiary,Value\nNorth,15\nSouth,20\nNorth,30\n"


def _run_alert(tmp_path, monkeypatch, *, limit, mode="send"):
    # The seeding helper assigns the database path; restore it after the test.
    monkeypatch.setattr(database, "DB_PATH", database.DB_PATH)
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences.pbi_fetch, "fetch_dataset_last_refresh",
        lambda workspace_id, dataset_id: {
            "status": "Completed", "start_time": "2026-07-16T07:55:00Z",
            "end_time": "2026-07-16T08:00:00Z", "error": None,
        },
    )
    monkeypatch.setattr(recurrences, "PBI_VISUAL_EXPORT_MAX_ROWS", limit)
    monkeypatch.setattr(recurrences, "export_visual_data", lambda *args, **kwargs: {
        "data": _ALERT_CSV,
        "visual": {"name": "visual456", "type": "tableEx", "title": "Exceptions"},
    })
    launched = []
    monkeypatch.setattr(
        recurrences, "_launch_outlook_payload",
        lambda messages, sent_mode: launched.append((messages, sent_mode)) or len(messages),
    )
    result = recurrences.run_recurrence(recurrence_id, mode=mode, trigger_type="manual")
    return recurrence_id, result, launched


def test_f14_cut_off_export_is_named_near_the_top_of_every_email_and_on_the_run(tmp_path, monkeypatch):
    recurrence_id, result, launched = _run_alert(tmp_path, monkeypatch, limit=3)

    # The cut-off table is still delivered, as before.
    assert result["status"] == "sent" and result["email_count"] == 2
    messages, mode = launched[0]
    assert mode == "send" and len(messages) == 2
    for message in messages:
        body = message["html_body"]
        assert "Incomplete data" in body
        assert "The table was cut off at the Power BI export limit of 3 rows." in body
        assert "Only the first 3 rows were exported" in body
        # Near the top: before the report block, the alert note and the table.
        notice = body.index("Incomplete data")
        assert notice < body.index(">REPORT<") < body.index("ALERT RESULTS") < body.index("<thead>")
        assert "Metronome" not in body

    run = recurrences.recurrence_runs(recurrence_id)[0]
    assert run["status"] == "sent" and run["error"] is None
    assert run["detail"]["export_limit_reached"] is True
    assert run["detail"]["export_row_limit"] == 3
    assert run["detail"]["warning"] == (
        "The table was cut off at the Power BI export limit of 3 rows. "
        "Only the first 3 rows were exported, so any rows beyond them are missing."
    )


def test_f14_complete_export_sends_no_incomplete_data_notice(tmp_path, monkeypatch):
    recurrence_id, result, launched = _run_alert(tmp_path, monkeypatch, limit=4, mode="draft")

    assert result["status"] == "drafted"
    assert all("Incomplete data" not in message["html_body"] for message in launched[0][0])
    assert all("cut off" not in message["html_body"] for message in launched[0][0])
    detail = recurrences.recurrence_runs(recurrence_id)[0]["detail"]
    assert detail["export_limit_reached"] is False
    assert detail["export_row_limit"] == 4
    assert detail["warning"] is None


def test_f14_warning_names_the_real_limit_and_can_refuse_delivery():
    assert recurrences._export_limit_warning(29_999, 30_000) is None
    warning = recurrences._export_limit_warning(30_000, 30_000)
    assert "export limit of 30,000 rows" in warning and "first 30,000 rows" in warning
    assert "export limit of 1,200 rows" in recurrences._export_limit_warning(1_200, 1_200)
    # The future "do not send" choice takes the existing failure path.
    with pytest.raises(RuntimeError, match="cut off at the Power BI export limit of 1,200 rows.*No email was sent"):
        recurrences._export_limit_warning(1_200, 1_200, send_cut_off=False)
    assert recurrences._export_limit_warning(1_199, 1_200, send_cut_off=False) is None


def test_f14_preview_reports_the_row_limit_it_exported_with(monkeypatch):
    monkeypatch.setattr(recurrences, "PBI_VISUAL_EXPORT_MAX_ROWS", 3)
    monkeypatch.setattr(recurrences, "export_visual_data", lambda *args, **kwargs: {
        "data": _ALERT_CSV, "export_method": "visual_export", "visual": {},
    })

    preview = recurrences.preview_report_visual(recurrences.VisualPreview(
        workspace_id="11111111-1111-1111-1111-111111111111",
        report_id="22222222-2222-2222-2222-222222222222",
        embed_url="https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
        page_name="ReportSection123", visual_name="visual456",
    ))

    assert preview["export_limit_reached"] is True and preview["export_row_limit"] == 3


# ── F-15: the Execute Queries fallback never treats an unread filter as "no filter" ──


import subprocess  # noqa: E402

from app import pbi_visual_export, pbi_visual_query  # noqa: E402

_PBI_REPORT = (
    "22222222-2222-2222-2222-222222222222",
    "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
    "ReportSection", "visual1",
)
_PBI_MODEL = {
    "workspace_id": "11111111-1111-1111-1111-111111111111",
    "dataset_id": "33333333-3333-3333-3333-333333333333",
}


def _fallback_spec(unreadable):
    return {
        "fields": [{
            "target": {"table": "Operations", "column": "Subsidiary"},
            "displayName": "Subsidiary", "formatString": "",
        }],
        "filters": [],
        "unreadableFilters": unreadable,
    }


def _forbid_power_bi_requests(monkeypatch):
    monkeypatch.setattr(
        pbi_visual_query, "get_access_token",
        lambda: pytest.fail("No Power BI token may be requested for an unfiltered fallback"),
    )
    monkeypatch.setattr(
        pbi_visual_query.httpx, "Client",
        lambda *args, **kwargs: pytest.fail("No Execute Queries request may be sent"),
    )


def _collect_query_spec(failing):
    """Run the embedded ``collectQuerySpec`` against fake Power BI objects in Node."""
    runtime = pbi_visual_export._RUNTIME_HTML
    helpers = runtime[
        runtime.index("const safeCall = async"):runtime.index("const resolveQueryFallback = async")
    ]
    script = """
    const failing = new Set(%s);
    const filter = name => ({target: {table: "T", column: name}, operator: "In", values: [name]});
    const reads = name => async () => {
      if (failing.has(name)) throw new Error("blocked");
      return [filter(name)];
    };
    const report = {getFilters: reads("report")};
    const page = {getFilters: reads("page")};
    const selectedVisual = {
      name: "v1", type: "tableEx", getFilters: reads("visual"),
      getCapabilities: async () => ({dataRoles: []}),
      getDataFields: async () => [], getProperty: async () => null,
    };
    const slicer = {name: "s1", type: "slicer", getSlicerState: async () => {
      if (failing.has("slicer")) throw new Error("blocked");
      return {filters: [filter("slicer")]};
    }};
    (async () => {
      %s
      const spec = await collectQuerySpec(report, page, selectedVisual, [selectedVisual, slicer]);
      console.log(JSON.stringify({
        unreadableFilters: spec.unreadableFilters,
        sources: spec.filters.map(item => item.source),
      }));
    })();
    """ % (json.dumps(sorted(failing)), helpers)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize(("failing", "unreadable", "sources"), [
    (set(), [], ["report", "page", "visual", "slicer:s1"]),
    ({"report"}, ["report"], ["page", "visual", "slicer:s1"]),
    ({"page"}, ["page"], ["report", "visual", "slicer:s1"]),
    ({"visual"}, ["visual"], ["report", "page", "slicer:s1"]),
    ({"report", "page", "visual"}, ["report", "page", "visual"], ["slicer:s1"]),
    # Slicer-state reads stay tolerant: only report, page and visual filters fail closed.
    ({"slicer"}, [], ["report", "page", "visual"]),
])
def test_f15_browser_script_names_each_filter_level_it_could_not_read(failing, unreadable, sources):
    spec = _collect_query_spec(failing)

    assert spec == {"unreadableFilters": unreadable, "sources": sources}
    if unreadable:
        with pytest.raises(pbi_visual_query.PbiVisualQueryError, match="No email was sent"):
            pbi_visual_query.build_visual_query(_fallback_spec(spec["unreadableFilters"]), 100)
    else:
        assert "EVALUATE" in pbi_visual_query.build_visual_query(_fallback_spec([]), 100)["dax"]


def test_f15_browser_script_no_longer_swallows_filter_read_failures():
    runtime = pbi_visual_export._RUNTIME_HTML

    for swallowed in (
        "safeCall(() => report.getFilters(), [])",
        "safeCall(() => page.getFilters(), [])",
        "safeCall(() => selectedVisual.getFilters(), [])",
    ):
        assert swallowed not in runtime
    for level in ('readFilters("report", report)', 'readFilters("page", page)',
                  'readFilters("visual", selectedVisual)'):
        assert level in runtime
    assert "unreadableFilters.push(source)" in runtime
    assert "safeCall(() => visual.getSlicerState(), null)" in runtime


@pytest.mark.parametrize(("unreadable", "named"), [
    (["report"], "the report filters"),
    (["page"], "the page filters"),
    (["visual"], "the visual filters"),
    (["visual", "report"], "the report and visual filters"),
    (["visual", "page", "report"], "the report, page and visual filters"),
    (["something else"], "the report, page or visual filters"),
])
def test_f15_fallback_query_names_the_unread_filter_level_and_is_not_built(unreadable, named):
    with pytest.raises(pbi_visual_query.PbiVisualQueryError) as raised:
        pbi_visual_query.build_visual_query(_fallback_spec(unreadable), 100)

    message = str(raised.value)
    assert f"Power BI did not return {named}" in message
    assert message.endswith("No email was sent.")


def test_f15_export_fails_closed_without_querying_the_semantic_model(monkeypatch):
    monkeypatch.setattr(pbi_visual_export, "_run_visual_action", lambda **kwargs: {
        "exportError": {"message": "Error running visual data query."},
        "querySpec": _fallback_spec(["page"]),
        "visual": {"name": "visual1", "type": "table", "title": "Exceptions"},
    })
    _forbid_power_bi_requests(monkeypatch)

    with pytest.raises(pbi_visual_export.PbiVisualExportError) as raised:
        pbi_visual_export.export_visual_data(*_PBI_REPORT, **_PBI_MODEL)

    assert "semantic-model fallback failed" in str(raised.value)
    assert "did not return the page filters" in str(raised.value)
    assert "No email was sent." in str(raised.value)


def test_f15_expression_title_is_not_evaluated_under_fewer_filters(monkeypatch):
    monkeypatch.setattr(pbi_visual_export, "_run_visual_action", lambda **kwargs: {
        "data": "Subsidiary\r\nNorth\r\n",
        "querySpec": _fallback_spec(["report"]),
        "visual": {"name": "visual1", "type": "pivotTable", "title": "", "titleSource": "expression",
                   "titleTarget": {"table": "Measures", "measure": "Title"}},
    })
    _forbid_power_bi_requests(monkeypatch)

    result = pbi_visual_export.export_visual_data(*_PBI_REPORT, **_PBI_MODEL)

    # The visual's own export is unaffected; only the wider title query is skipped.
    assert result["export_method"] == "visual_export"
    assert result["data"] == "Subsidiary\r\nNorth\r\n"
    assert result["visual"]["title"] == ""


def test_f15_alert_run_sends_no_data_when_a_filter_level_was_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", database.DB_PATH)
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(
        recurrences.pbi_fetch, "fetch_dataset_last_refresh",
        lambda workspace_id, dataset_id: {
            "status": "Completed", "start_time": "2026-07-16T07:55:00Z",
            "end_time": "2026-07-16T08:00:00Z", "error": None,
        },
    )
    monkeypatch.setattr(pbi_visual_export, "_run_visual_action", lambda **kwargs: {
        "exportError": {"message": "Error running visual data query."},
        "querySpec": _fallback_spec(["visual"]),
        "visual": {"name": "visual456", "type": "tableEx", "title": "Exceptions"},
    })
    _forbid_power_bi_requests(monkeypatch)
    launched = []
    monkeypatch.setattr(
        recurrences, "_launch_outlook_payload",
        lambda messages, mode: launched.append((messages, mode)) or len(messages),
    )

    with pytest.raises(pbi_visual_export.PbiVisualExportError, match="did not return the visual filters"):
        recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="scheduled")

    # Only the owner's failure notice went out; no alert data reached a recipient.
    assert [[message["to"] for message in messages] for messages, _mode in launched] == [["owner@example.com"]]
    assert "DELIVERY BLOCKED" in launched[0][0][0]["html_body"]
    assert "did not return the visual filters" in launched[0][0][0]["html_body"]
    run = recurrences.recurrence_runs(recurrence_id)[0]
    assert run["status"] == "failed" and run["email_count"] == 0
    assert "No email was sent." in run["error"]


# ── F-16: recording sessions do not leave unsanitised files behind ──


import logging  # noqa: E402
import os  # noqa: E402

from app import flow_browser_state, flow_portable, flow_recording_runtime  # noqa: E402
from app import flow_recorder_worker as recorder  # noqa: E402
from test_flow_recordings import CODEGEN  # noqa: E402

_TYPED_SECRET = "typed-fixture-secret"


def _record_session(tmp_path, monkeypatch, *, outcome, scan_id=12):
    """Run ``recorder.record`` against a fake codegen CLI that ends as ``outcome``."""
    source = CODEGEN
    if outcome == "password_typed":
        source = source.replace(
            '    page.get_by_role("button", name="Generate").click()',
            f'    page.get_by_label("Password").fill("{_TYPED_SECRET}")\n'
            '    page.get_by_role("button", name="Generate").click()',
        )
    else:
        source = source.replace('"2026-01-01"', f'"{_TYPED_SECRET}"')
    assert _TYPED_SECRET in source

    class Context:
        def storage_state(self, **kwargs):
            return {"cookies": [{"name": "session", "value": "fixture-cookie"}], "origins": []}

        def close(self):
            pass

    class Process:
        def __init__(self, command, stdout=None, **kwargs):
            self.returncode = 1 if outcome == "crashed" else None
            self.pid = 12345
            stdout.write("recorder output\n")
            Path(command[command.index("--output") + 1]).write_text(source, encoding="utf-8")

        def poll(self):
            return self.returncode

    def close(process):
        if process.poll() is None:
            process.returncode = -1

    monkeypatch.setattr(recorder, "authenticate", lambda *args, **kwargs: None)
    monkeypatch.setattr(recorder, "_close_recorder", close)
    monkeypatch.setattr(recorder.subprocess, "Popen", Process)
    monkeypatch.setattr(recorder.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(flow_browser_state, "protect_temporary_folder", lambda path: None)
    monkeypatch.setattr(flow_worker, "_api", lambda *args, **kwargs: {
        "status": "running",
        "finish_requested": outcome in {"finished", "password_typed"},
        "cancel_requested": outcome == "cancelled",
    })
    scan = {"id": scan_id, "job": {
        "site": {"adapter": "asap_portal"}, "browser_channel": "chrome",
        "report_url": "http://localhost/report",
    }}
    return recorder.record(None, "worker", scan, None, Context(), tmp_path, lambda *args, **kwargs: None)


def _plain_text_files_holding(root, needle):
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
        and needle in path.read_text(encoding="utf-8", errors="ignore")
    )


@pytest.mark.parametrize(("outcome", "error", "message"), [
    ("cancelled", recorder.RecordingCancelled, "Recording cancelled"),
    ("password_typed", ValueError, "Authentication inputs cannot be included"),
])
def test_f16_cancelled_or_rejected_session_leaves_no_raw_script_log_or_state(
        tmp_path, monkeypatch, outcome, error, message):
    with pytest.raises(error, match=message):
        _record_session(tmp_path, monkeypatch, outcome=outcome)

    session = tmp_path / "recordings" / "12"
    assert not (session / "codegen.py").exists()
    assert not (session / "recorder.log").exists()
    assert not list(tmp_path.rglob("state.json")) and not list(tmp_path.rglob("codegen-auth-*"))
    # The value typed during the session is in no file left under the profile.
    assert _plain_text_files_holding(tmp_path, _TYPED_SECRET) == []
    assert not list(session.iterdir())


def test_f16_crashed_recorder_drops_its_raw_script_but_keeps_the_log_it_points_to(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="Check the local recorder log"):
        _record_session(tmp_path, monkeypatch, outcome="crashed")

    session = tmp_path / "recordings" / "12"
    assert not (session / "codegen.py").exists()
    assert (session / "recorder.log").read_text(encoding="utf-8") == "recorder output\n"
    assert _plain_text_files_holding(tmp_path, _TYPED_SECRET) == []


def test_f16_session_start_removes_plain_text_state_left_by_a_killed_worker(tmp_path, monkeypatch):
    killed = tmp_path / "recordings" / "7" / "codegen-auth-killed"
    killed.mkdir(parents=True)
    (killed / "state.json").write_text('{"cookies":[{"value":"leftover-cookie"}]}', encoding="utf-8")
    kept = tmp_path / "recordings" / "7" / "codegen.py"
    kept.write_text("# an earlier accepted recording\n", encoding="utf-8")
    # Anything reached through a link is outside these directories: never followed.
    outside = tmp_path / "outside" / "codegen-auth-elsewhere"
    outside.mkdir(parents=True)
    (outside / "state.json").write_text("not ours", encoding="utf-8")
    linked = True
    try:
        os.symlink(outside, tmp_path / "recordings" / "7" / "codegen-auth-link", target_is_directory=True)
        os.symlink(tmp_path / "outside", tmp_path / "recordings" / "8", target_is_directory=True)
    except OSError:
        linked = False

    result = _record_session(tmp_path, monkeypatch, outcome="finished")

    assert result["definition"]["steps"]
    assert not killed.exists()
    assert _plain_text_files_holding(tmp_path, "leftover-cookie") == []
    assert kept.is_file()
    assert (outside / "state.json").read_text(encoding="utf-8") == "not ours"
    if linked:
        assert (tmp_path / "recordings" / "7" / "codegen-auth-link").is_symlink()
    # An accepted session keeps its own script and log, as before.
    assert (tmp_path / "recordings" / "12" / "codegen.py").is_file()
    assert not list(tmp_path.rglob("recordings/12/codegen-auth-*"))


def _validate_recording_test(tmp_path, monkeypatch, scan_id, *, fail=False):
    job = {
        "recording": {"engine_hash": "recording-engine", "revision": 7},
        "execution": {},
        "paths": {"flow_folder": str(tmp_path / "flow")},
        "downloads": {},
        "sql_handoff": {"enabled": False},
    }
    monkeypatch.setattr(flow_portable, "execution_hash", lambda: "recording-engine")
    monkeypatch.setattr(flow_worker, "_code_version", lambda: "tested-worker")
    monkeypatch.setattr(recorder, "authenticate", lambda *args: None)
    page = SimpleNamespace(context=SimpleNamespace(
        browser=SimpleNamespace(version="browser-1"),
        tracing=SimpleNamespace(
            start=lambda **kwargs: None,
            stop=lambda path: Path(path).write_bytes(b"trace"),
        ),
    ))

    def execute(_page, validation_job, _progress, _profile, *args, state, **kwargs):
        if fail:
            raise RuntimeError("the recorded step failed")
        state["artifacts"] = [{
            "status": "saved", "export_view": "download", "filename": "validated.csv",
            "checksum": "a" * 64, "row_count": 1,
        }]

    monkeypatch.setattr(flow_recording_runtime, "execute_recorded_flow", execute)
    return recorder.validate(
        {"id": scan_id, "job": {"validation_job": job, "configuration_hash": "tested-config"}},
        page, tmp_path / "profile", lambda *args: None,
    )


def _seed_validation_folders(tmp_path, names):
    root = tmp_path / "flow" / ".recording-validation"
    for name in names:
        (root / name / "run").mkdir(parents=True)
        (root / name / "trace.zip").write_bytes(b"old trace")
        (root / name / "run" / "export.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("fail", [False, True])
def test_f16_only_the_newest_three_validation_folders_are_kept(tmp_path, monkeypatch, fail):
    root = _seed_validation_folders(tmp_path, ["3", "9", "10", "11", "operator-notes"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("not a validation folder", encoding="utf-8")
    linked = True
    try:
        os.symlink(outside, root / "2", target_is_directory=True)
    except OSError:
        linked = False

    if fail:
        with pytest.raises(RuntimeError, match="the recorded step failed"):
            _validate_recording_test(tmp_path, monkeypatch, 12, fail=True)
    else:
        result = _validate_recording_test(tmp_path, monkeypatch, 12)
        assert result["trace_path"] == str(root / "12" / "trace.zip")

    assert (root / "12" / "trace.zip").read_bytes() == b"trace"
    remaining = sorted(entry.name for entry in root.iterdir() if not entry.is_symlink())
    # 12 is this test, 11 and 10 the two before it; 9 and 3 are removed whole.
    assert remaining == ["10", "11", "12", "operator-notes"]
    assert (root / "11" / "run" / "export.csv").is_file()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "not a validation folder"
    if linked:
        assert (root / "2").is_symlink()


def test_f16_a_failed_delete_is_a_logged_warning_not_a_failed_session(tmp_path, monkeypatch, caplog):
    root = _seed_validation_folders(tmp_path, ["8", "9", "10", "11"])

    def locked(path, *args, **kwargs):
        raise PermissionError(13, "The trace is open in another program", str(path))

    monkeypatch.setattr(recorder.shutil, "rmtree", locked)
    with caplog.at_level(logging.WARNING, logger=recorder.logger.name):
        result = _validate_recording_test(tmp_path, monkeypatch, 12)

    assert result["outputs"][0]["filename"] == "validated.csv"
    assert sorted(entry.name for entry in root.iterdir()) == ["10", "11", "12", "8", "9"]
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2 and all("PermissionError" in text for text in warnings)
    assert all(".recording-validation/" in text for text in warnings)


# ── F-18: a recycled row slot or a folder is never clicked as the bookmark ──


from app import flow_recording_gscm_bookmark as bookmarks  # noqa: E402
from test_recording_gscm_bookmark import (  # noqa: E402
    FakeGrid, FakePage, entry as bookmark_entry, long_list, step as bookmark_step, wire,
)


class _RepaintingGrid(FakeGrid):
    """Scrolls by ``shift`` rows between the first read of its rows and the next."""

    def __init__(self, rows, *, shift, **kwargs):
        super().__init__(rows, **kwargs)
        self.shift = shift

    def rendered(self):
        self.reads += 1
        if self.reads == 2:
            self.start += self.shift
        return super().rendered()


@pytest.mark.parametrize(("shift", "description"), [
    (1, "the bookmark now sits in another slot; its old slot shows the next row"),
    (5, "the bookmark has scrolled out of the rendered rows"),
])
def test_f18_row_that_moved_before_the_click_is_not_clicked(monkeypatch, shift, description):
    grid = _RepaintingGrid(long_list(target_at=3), shift=shift)
    wire(monkeypatch, grid, [bookmark_entry("Saved report", "bookmark-1")])

    with pytest.raises(bookmarks.BookmarkSelectionError, match="moved while its row was being selected") as info:
        bookmarks.select(FakePage(), bookmark_step(scope="Public"))

    assert info.value.diagnostic["bookmark"]["reason"] == "rendered_changed"
    assert grid.clicked == [], description


def test_f18_settled_row_is_read_again_and_then_clicked(monkeypatch):
    grid = FakeGrid(long_list(target_at=3))
    wire(monkeypatch, grid, [bookmark_entry("Saved report", "bookmark-1")])
    waits = []

    class Page(FakePage):
        def wait_for_timeout(self, ms):
            waits.append(ms)

    reads = []
    rendered = grid.rendered
    monkeypatch.setattr(bookmarks.flow_gscm, "favorite_tree_rows", lambda page: reads.append(1) or rendered())

    result = bookmarks.select(Page(), bookmark_step(scope="Public"))

    assert result["strategy"] == "favorite-visible-row" and result["reason"] is None
    assert len(reads) == 2 and waits == [bookmarks.RENDER_POLL_MS]
    assert [row["text"] for row in grid.clicked] == ["Saved report"]


def test_f18_folder_with_the_bookmark_name_is_never_clicked(monkeypatch):
    # The bookmark's own folder is collapsed: only a folder of the same name renders.
    rows = [("Saved report", None)] + [("Report " + str(index), ["Saved report"]) for index in range(20)]
    grid = FakeGrid(rows)
    wire(monkeypatch, grid, [bookmark_entry("Saved report", "bookmark-1")])

    with pytest.raises(bookmarks.BookmarkSelectionError, match="was not rendered") as info:
        bookmarks.select(FakePage(), bookmark_step())

    assert info.value.diagnostic["bookmark"]["reason"] == "end_confirmed"
    assert grid.clicked == []


def test_f18_bookmark_under_a_folder_of_the_same_name_is_clicked_not_the_folder(monkeypatch):
    rows = [("Saved report", None), ("Saved report", ["Saved report"])]
    rows += [("Report " + str(index), ["Saved report"]) for index in range(5)]
    grid = FakeGrid(rows)
    wire(monkeypatch, grid, [bookmark_entry("Saved report", "bookmark-1", folder=("Saved report",))])

    result = bookmarks.select(FakePage(), bookmark_step())

    assert result["rendered"] and result["reason"] is None
    assert [(row["text"], row["is_folder"], row["id"]) for row in grid.clicked] == [
        ("Saved report", False, "grd_bookmark.body.gridrow_1"),
    ]


# ── F-21: the failure notice names its time in Dubai time, not as a UTC instant ──


def test_f21_failure_notice_shows_the_detection_time_in_dubai_time(monkeypatch):
    from app import flow_clock

    class Clock(datetime):
        @classmethod
        def now(cls, zone=None):
            return datetime(2026, 12, 31, 21, 30, tzinfo=timezone.utc).astimezone(zone)

    monkeypatch.setattr(flow_clock, "datetime", Clock)
    monkeypatch.setattr(recurrences, "_now", lambda: datetime(2026, 12, 31, 21, 30))

    message = recurrences._failure_notification_message(
        {"name": "Weekly exceptions", "report_name": "Operations", "report_url": None,
         "visual_title": "Exceptions", "visual_name": "visual456",
         "page_display_name": "Summary", "page_name": "ReportSection123"},
        {"name": "Report Owner", "email": "owner@example.com"},
        "The visual schema changed.",
    )

    body = " ".join(message["html_body"].split())
    # 21:30 UTC on 31 December is 01:30 on 1 January in Dubai.
    assert "Detected by Metronome at 01 Jan 2027, 01:30 (Dubai time)." in body
    assert "2026-12-31T21:30" not in body and "local time" not in body


# ── F-23(a): a saved bookmark named like portal chrome is still a bookmark ──


from app import flow_gscm  # noqa: E402

_CHROME_NAMES = ["Dashboard", "Latest", "Layout", "Setting", "Go", "Public"]


def test_f23a_dataset_bookmarks_named_like_portal_chrome_are_kept(monkeypatch):
    names = [*_CHROME_NAMES, "Regional sales"]
    monkeypatch.setattr(flow_gscm, "bookmark_dataset_rows", lambda page: [
        {"userreportid": f"RC_{index}", "userreportname": name, "scope": "AS",
         "publicscope": "Private", "menugroupname": "Sales", "menuname": "Actual sales",
         "menuid": "M1", "userid": "analyst", "originuserid": "analyst"}
        for index, name in enumerate(names)
    ] + [{"userreportid": "RC_BLANK", "userreportname": "  ", "scope": "AS"}])

    entries = flow_gscm.bookmark_dataset_entries(object())

    # Only the row with no name at all is dropped.
    assert [entry["name"] for entry in entries] == names
    assert [entry["bookmark_id"] for entry in entries] == [f"RC_{index}" for index in range(len(names))]
    assert all(entry["source"] == "gds_bookmark" for entry in entries)


def test_f23a_rendered_tree_still_ignores_portal_chrome_labels(monkeypatch):
    labels = [*_CHROME_NAMES, "Regional sales"]
    monkeypatch.setattr(flow_gscm, "favorite_tree_rows", lambda page: [
        (None, {"id": f"row_{index}", "text": label, "x": 20, "y": 30 * index, "w": 100, "is_folder": False})
        for index, label in enumerate(labels)
    ])

    assert [entry["name"] for entry in flow_gscm.read_favorite_tree(object())] == ["Regional sales"]


# ── F-22: the not-signed-in error names no portal host ──


def test_f22_not_signed_in_error_carries_no_portal_address():
    import re

    message = str(flow_gscm._not_signed_in_error())

    # Still actionable: the command, its flags and the adapter are all there.
    assert "GSCM is not signed in" in message and "separate portals" in message
    assert "--authenticate-url <the GSCM site's sign-in address> --authenticate-adapter gscm_portal" in message
    # No address of any kind: neither a URL scheme nor a dotted host name.
    assert "://" not in message
    hosts = [
        word for word in re.findall(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", message)
        if word not in {"flow_worker.py", "setup.ps1"}
    ]
    assert hosts == []


# ── F-23(b): a week box that never reaches its end is not verified as complete ──


def test_f23b_week_box_scan_fails_closed_when_it_never_reaches_the_last_screen(monkeypatch):
    """Every screen shows the same two selected weeks at a new scroll position."""
    position = {"top": 0}

    class Container:
        def wait_for(self, **kwargs):
            pass

        def evaluate(self, script, *args):
            return {"top": position["top"], "height": 1_000_000, "client": 96}

    def snapshots(container, selector, selected_state):
        visible = [
            {"index": index, "label": label, "signal": "aria-pressed", "selected": True,
             "enabled": True, "visible": True}
            for index, label in enumerate(["2026-W32", "2026-W33"])
        ]
        return None, visible, {2026}

    def scroll(container, *, reset=False):
        before = position["top"]
        position["top"] = 0 if reset else before + 76
        return {"before": before, "after": position["top"]}

    monkeypatch.setattr(flow_recording_runtime, "_range_cell_snapshots", snapshots)
    monkeypatch.setattr(flow_recording_runtime, "_range_scroll", scroll)
    step = {"id": "weeks", "action": "select_range", "range": {
        "unit": "week", "start": "2026-W32", "end": "latest_selectable", "selection": "inclusive",
        "cell_selector": "button.week", "selected_state": "aria-pressed",
        "navigation": {"kind": "scroll"},
    }}
    updates = []

    with pytest.raises(RuntimeError, match="did not reach its last weeks within 200 screens"):
        flow_recording_runtime._select_week_range(
            Container(), step, lambda message, detail: updates.append(detail),
        )

    assert updates[-1] == {"range_movement": 200}


# ── F-23(c): the navigation URL guard refuses credentials, not report codes ──


def _navigation_definition(url):
    return {"version": 2, "timezone": "Asia/Dubai", "parameters": {}, "steps": [
        {"id": "open", "action": "new_page", "page": "page"},
        {"id": "go", "action": "goto", "page": "page", "locator": [], "args": [url], "kwargs": {}},
    ]}


@pytest.mark.parametrize("parameter", [
    "token", "access_token", "id_token", "password", "secret", "client_secret", "ticket",
    "code", "auth_code", "authorization_code",
    # Spellings the narrower rule must keep refusing too.
    "CODE", "authCode", "authorizationCode", "passcode", "sessionToken", "refresh-token",
])
def test_f23c_navigation_url_with_a_credential_parameter_is_refused(parameter):
    for url in (
        f"https://portal.example.internal/report?{parameter}=fixture-value",
        f"https://portal.example.internal/report?menu=7&{parameter}=fixture-value&view=2",
    ):
        with pytest.raises(ValueError, match="without authentication parameters"):
            flow_recording.validate_definition(_navigation_definition(url), activation=False)


@pytest.mark.parametrize("parameter", [
    "reportCode", "menuCode", "zipcode", "ZipCode", "countrycode", "barcode", "itemCode",
    "decode", "encoded", "report", "menu",
])
def test_f23c_navigation_url_with_an_ordinary_code_parameter_is_accepted(parameter):
    for url in (
        f"https://portal.example.internal/report?{parameter}=A17",
        f"https://portal.example.internal/report?menu=7&{parameter}=A17&view=2",
    ):
        definition = flow_recording.validate_definition(_navigation_definition(url), activation=False)
        assert definition["steps"][1]["args"] == [url]


# ── F-23(d): a date slider can travel several years and still has a hard cap ──


from datetime import date, timedelta  # noqa: E402

from app import flow_range_slider  # noqa: E402


class _DateHandle:
    def __init__(self, day):
        self.day = day
        self.presses = 0

    def press(self, key):
        self.presses += 1
        self.day += timedelta(days=1 if key == "ArrowRight" else -1)

    def get_attribute(self, name):
        return self.day.strftime("%Y%m%d") if name == "aria-valuenow" else None

    def inner_text(self):
        return ""


def test_f23d_date_slider_travels_past_a_thousand_days_and_stops_at_the_cap():
    assert flow_range_slider.MAX_KEY_PRESSES == 5_000
    start = date(2020, 1, 1)
    handle = _DateHandle(start)

    target = start + timedelta(days=1_500)
    flow_range_slider.move_slider(handle, target.strftime("%Y%m%d"), "date")
    assert handle.day == target and handle.presses == 1_500

    edge = target - timedelta(days=5_000)
    flow_range_slider.move_slider(handle, edge.strftime("%Y%m%d"), "date")
    assert handle.day == edge and handle.presses == 6_500

    beyond = edge + timedelta(days=5_001)
    with pytest.raises(RuntimeError, match=r"Date slider target is more than 5,000 steps from its current value\."):
        flow_range_slider.move_slider(handle, beyond.strftime("%Y%m%d"), "date")
    # Refused before a single key press: the handle has not moved.
    assert handle.day == edge and handle.presses == 6_500
