"""Workers obey the limits frozen into their job and pick up changed connections.

A worker never reads Metronome's database: every limit below arrives in
``job["limits"]``. Each test drives the real worker function with stand-ins
for the browser, the portal and PostgreSQL, three ways: a job without limits
(queued before they existed) behaves exactly as before, a job carrying the
built-in values calls everything exactly as before, and a changed value is
obeyed. Nothing here opens a browser, a portal or a database.
"""
import json
import os
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import (
    config, flow_job_limits, flow_recorder_worker, flow_recording_gscm_bookmark as bookmarks,
    flow_recording_runtime as runtime, flow_replay, flow_sql, flow_view_refresh, flow_worker,
)
from test_flow_replay import JOB as REPLAY_JOB, TASK_KEY, _capture
from test_recording_gscm_bookmark import FakeGrid, FakePage, entry, long_list, step, wire
from test_view_refresh import FakeEngines, view

RUN_ID = 77
# What a job carries when nothing was changed on the Settings page.
BUILT_IN = {
    "download_start_seconds": 900, "download_event_seconds": 3600, "export_attempts": 3,
    "gscm_export_attempts": 2, "recorded_action_seconds": 120, "network_replay": True,
    "replay_max_age_days": 14, "replay_request_seconds": 300, "script_timeout_seconds": 3600,
    "sql_connect_seconds": 60, "sql_lock_seconds": 600, "sql_statement_seconds": 1800,
    "view_lock_seconds": 600, "view_refresh_seconds": 3600, "failure_screenshots_kept": 20,
    "recording_test_folders_kept": 3,
}


def _with(job, **limits):
    """``job`` as the server would queue it with these limits changed."""
    return {**job, "limits": {**BUILT_IN, **limits}}


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(flow_worker.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(flow_worker.time, "sleep", fake.sleep)
    return fake


# --- The worker loop ----------------------------------------------------------------

class _Page:
    def is_closed(self):
        return False

    def wait_for_function(self, *_args, **_kwargs):
        return None


class _Browser:
    def __init__(self):
        self.pages = [_Page()]

    def close(self):
        return None


def _run_worker_over(tmp_path, monkeypatch, work, *, refresh=None):
    """Drive the real worker loop over ``work`` (claim responses), then let it go idle and exit."""
    moment, order, reports = [0], [], []
    pending = list(work)
    monkeypatch.setattr(flow_worker, "sync_playwright", lambda: nullcontext(SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=lambda _profile, **_options: _Browser()))))
    monkeypatch.setattr(flow_worker.time, "monotonic", lambda: moment[0])
    monkeypatch.setattr(flow_worker.time, "sleep", lambda seconds: moment.__setitem__(0, moment[0] + seconds))
    monkeypatch.setattr(flow_worker.time, "perf_counter", lambda: float(moment[0]))

    def api(_client, _method, path, body=None):
        assert moment[0] <= 300, "the worker did not exit when idle"
        if path.endswith("/register"):
            return {}
        if path.endswith("/claim"):
            order.append("claim")
            return pending.pop(0) if pending else {}
        reports.append((path, body))
        return {}

    monkeypatch.setattr(flow_worker, "_api", api)
    real = flow_worker._refresh_connection_settings

    def refreshed():
        order.append("refresh")
        return (refresh or real)()

    monkeypatch.setattr(flow_worker, "_refresh_connection_settings", refreshed)
    flow_worker.run_worker("http://127.0.0.1:1", "bi-desktop-headless", "Headless", tmp_path / "profile",
                           False, False, idle_exit_seconds=20)
    return order, reports


def test_the_worker_looks_for_changed_connection_settings_before_every_claim(tmp_path, monkeypatch):
    order, _reports = _run_worker_over(tmp_path, monkeypatch, [])

    assert len(order) >= 4 and order == ["refresh", "claim"] * (len(order) // 2)


def test_a_settings_file_that_cannot_be_read_never_stops_the_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "refresh_if_changed", lambda: (_ for _ in ()).throw(PermissionError("locked by a save")))
    monkeypatch.setattr(flow_worker, "_settings_refresh_problem", None)

    order, _reports = _run_worker_over(tmp_path, monkeypatch, [])

    assert order.count("claim") >= 2 and order.count("refresh") == order.count("claim")
    # Said once, with the kind of failure only, however many loops it lasts.
    printed = capsys.readouterr().err
    assert printed.count("Could not re-read the settings file (PermissionError)") == 1
    assert "locked by a save" not in printed


# --- Changed connection settings ---------------------------------------------------

LIVE_FILE_NAMES = ("PGHOST", "PGPORT", "PGDATABASE", "DG_UPLOAD_PGHOST", "DG_UPLOAD_PGPORT", "DG_UPLOAD_PGDATABASE",
                   "DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD")


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """The protected settings file of a worker process, restored afterwards."""
    environment = dict(os.environ)
    live = {name: getattr(config, name) for name in config.LIVE_NAMES}
    for name in LIVE_FILE_NAMES:
        os.environ.pop(name, None)
    path = tmp_path / ".env"
    path.write_text("DG_UPLOAD_PGHOST=first.example.test\nDG_UPLOAD_PGDATABASE=analytics\n"
                    "DG_UPLOAD_PGUSER=writer\nDG_UPLOAD_PGPASSWORD=first-password\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    monkeypatch.setattr(config, "_names_from_file", set())
    monkeypatch.setattr(config, "_env_file_signature", None)
    monkeypatch.setattr(config, "_STARTING_ENVIRONMENT", dict(os.environ))
    monkeypatch.setattr(flow_sql, "_REDACTED_PASSWORDS", set())
    monkeypatch.setattr(flow_worker, "_settings_refresh_problem", None)
    yield path
    monkeypatch.undo()
    os.environ.clear()
    os.environ.update(environment)
    for name, value in live.items():
        current = getattr(config, name)
        if current != value:
            for module in list(sys.modules.values()):
                if module is not None and config._is_metronome_module(module) and module.__dict__.get(name) == current:
                    setattr(module, name, value)
            setattr(config, name, value)


def _connection(engine):
    return engine.url.host, engine.url.port, engine.url.username, engine.url.password


def test_a_worker_connects_with_the_host_and_password_saved_while_it_runs(settings_file, capsys):
    assert flow_worker._refresh_connection_settings()
    assert _connection(flow_sql._engine("analytics")) == ("first.example.test", 5432, "writer", "first-password")
    capsys.readouterr()

    # The host PC saves a new server and password while this worker is idle.
    settings_file.write_text("DG_UPLOAD_PGHOST=second.example.test\nDG_UPLOAD_PGPORT=6543\n"
                             "DG_UPLOAD_PGDATABASE=analytics\nDG_UPLOAD_PGUSER=loader\n"
                             "DG_UPLOAD_PGPASSWORD=second-password\n", encoding="utf-8")
    # Before the next loop looks at the file, nothing has changed...
    assert _connection(flow_sql._engine("analytics"))[0] == "first.example.test"
    changed = flow_worker._refresh_connection_settings()

    # ...and the connection made after it uses the new values.
    assert {"UPLOAD_PGHOST", "UPLOAD_PGPORT", "UPLOAD_PGUSER", "UPLOAD_PGPASSWORD"} <= set(changed)
    assert _connection(flow_sql._engine("analytics")) == ("second.example.test", 6543, "loader", "second-password")
    assert flow_sql.configuration_status()["host"] == "second.example.test"
    # The worker's log names what changed, never a value.
    printed = capsys.readouterr()
    assert "UPLOAD_PGPASSWORD" in printed.out and "second-password" not in printed.out + printed.err
    # Neither password this process connected with can appear in a reported error.
    error = flow_sql._database_error(RuntimeError("login failed for first-password / second-password"))
    assert "first-password" not in error and "second-password" not in error
    # An unchanged file is not read again.
    assert flow_worker._refresh_connection_settings() == []


def test_the_sql_step_holds_no_connection_value_from_import(settings_file):
    for name in ("UPLOAD_PGHOST", "UPLOAD_PGPORT", "UPLOAD_PGDATABASE", "UPLOAD_PGUSER", "UPLOAD_PGPASSWORD"):
        assert not hasattr(flow_sql, name), name
    config.refresh()
    assert flow_sql.configuration_status()["configured"] is True
    settings_file.write_text("DG_UPLOAD_PGHOST=first.example.test\nDG_UPLOAD_PGDATABASE=analytics\n", encoding="utf-8")
    config.refresh()
    # The shared account was removed: an unassigned Flow can no longer connect.
    assert flow_sql.configuration_status()["missing"] == ["DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD"]
    with pytest.raises(RuntimeError, match="SQL handoff is not configured"):
        flow_sql._engine("analytics")


# --- Reading a limit from a job --------------------------------------------------

def test_a_job_without_usable_limits_falls_back_to_the_built_in_value():
    for job in ({}, {"limits": None}, {"limits": []}, {"limits": {}}, None,
                {"limits": {"sql_lock_seconds": "600"}}, {"limits": {"sql_lock_seconds": True}},
                {"limits": {"sql_lock_seconds": 0}}, {"limits": {"sql_lock_seconds": -5}},
                {"limits": {"sql_lock_seconds": float("nan")}}, {"limits": {"sql_lock_seconds": float("inf")}}):
        assert flow_job_limits.value(job, "sql_lock_seconds", 600) == 600, job
        assert flow_job_limits.given(job, lock=("sql_lock_seconds", 600)) == {}, job
    assert flow_job_limits.value({"limits": {"sql_lock_seconds": 180}}, "sql_lock_seconds", 600) == 180
    # A switch is on or off, never a number or a word.
    for damaged in (1, 0, "off", None):
        assert flow_job_limits.value({"limits": {"network_replay": damaged}}, "network_replay") is None
    assert flow_job_limits.value({"limits": {"network_replay": False}}, "network_replay") is False


def test_a_function_is_handed_only_a_limit_the_job_changes():
    arguments = {"lock": ("sql_lock_seconds", 600), "statement": ("sql_statement_seconds", 1800)}
    assert flow_job_limits.given({"limits": BUILT_IN}, **arguments) == {}
    assert flow_job_limits.given(_with({}, sql_statement_seconds=7200), **arguments) == {"statement": 7200}
    assert flow_job_limits.given(_with({}, sql_lock_seconds=60, sql_statement_seconds=60), **arguments) == {
        "lock": 60, "statement": 60}


# --- Downloads -------------------------------------------------------------------

class _DashboardPage:
    """A dashboard whose download link never produces a file."""

    def __init__(self, clock):
        self.clock = clock
        self.context = SimpleNamespace(pages=[], on=lambda *_a: None, remove_listener=lambda *_a: None)

    def wait_for_timeout(self, milliseconds):
        self.clock.now += milliseconds / 1_000


@pytest.mark.parametrize("job, seconds", [
    ({}, 900), ({"limits": BUILT_IN}, 900), (_with({}, download_start_seconds=1200), 1200),
    (_with({}, download_start_seconds=60), 60),
])
def test_an_asap_download_waits_the_frozen_time_to_start(tmp_path, monkeypatch, clock, job, seconds):
    monkeypatch.setattr(flow_worker, "_asap_dashboard_link_locator",
                        lambda *_a, **_k: SimpleNamespace(click=lambda **_kwargs: None))

    with pytest.raises(RuntimeError, match=f"create a local staging file within {seconds} seconds: Main data"):
        flow_worker._asap_download_dashboard_link(_DashboardPage(clock), "Main data", tmp_path / "staging", job)

    assert seconds <= clock.now < seconds + 1


@pytest.mark.parametrize("job, expected", [
    ({}, {}), ({"limits": BUILT_IN}, {}), (_with({}, download_start_seconds=1200), {"start_timeout_seconds": 1200}),
])
def test_the_start_limit_reaches_the_wait_for_the_staged_file(tmp_path, monkeypatch, clock, job, expected):
    staged = tmp_path / "export.csv"
    passed = []
    monkeypatch.setattr(flow_worker, "_asap_dashboard_link_locator",
                        lambda *_a, **_k: SimpleNamespace(click=lambda **_kwargs: None))
    monkeypatch.setattr(flow_worker, "_asap_wait_for_dashboard_download_signal", lambda *_a, **_k: "staging")
    monkeypatch.setattr(flow_worker, "_wait_for_staged_download",
                        lambda _dir, _before, **kwargs: passed.append(kwargs) or staged)

    assert flow_worker._asap_download_dashboard_link(
        _DashboardPage(clock), "Main data", tmp_path / "staging", job) == staged

    # Without a changed limit the wait is called exactly as it always was.
    assert passed == [{"progress": None, "label": "The ASAP dashboard download 'Main data'", **expected}]


class _NoDownloadPage:
    """A page on which no download ever starts; records how long it was asked to wait."""

    def __init__(self):
        self.timeouts, self.waits, self.context = [], [], None

    def expect_download(self, timeout=None):
        self.timeouts.append(timeout)
        raise flow_worker.PlaywrightTimeoutError("no download event")

    def goto(self, *_args, **_kwargs):
        return None

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def test_a_gscm_download_waits_the_frozen_time_for_the_browser():
    page = _NoDownloadPage()
    for arguments in ({}, {"event_timeout_seconds": 5400}):
        with pytest.raises(RuntimeError, match="did not emit its native download event"):
            flow_worker._edge_completed_download(page, lambda: None, **arguments)
    assert page.timeouts == [3_600_000, 5_400_000]


def _gscm_job(tmp_path):
    return {
        "site": {"adapter": "gscm_portal", "base_url": "https://gscm.example.test/"},
        "flow": {"name": "GSCM bookmark"},
        "report": {"id": 7, "name": "Public > SCM > Saved report", "url": "https://gscm.example.test/nexa/index.html",
                   "export_views": [], "automation": {"favorite_tab": "Public", "favorite_name": "Saved report",
                                                      "favorite_folder_path": ["SCM"]}},
        "downloads": {"target_folder": str(tmp_path), "periods": [None], "filename_template": "gscm.xlsx",
                      "file_format": "xlsx"},
    }


def _web_job(tmp_path):
    return {
        "site": {"adapter": "web_export"}, "flow": {"name": "Weekly movement"},
        "report": {"id": 3, "name": "Weekly movement", "url": "https://reports.example.test/weekly",
                   "ready_text": None, "open_export_text": None, "download_text": "Download CSV", "filters": [],
                   "export_views": []},
        "downloads": {"target_folder": str(tmp_path), "periods": [None], "filename_template": "weekly.csv",
                      "file_format": "csv"},
        "selections": {},
    }


def _execute(page, job, tmp_path):
    return flow_worker.execute_job(page, job, lambda *_a, **_k: None, tmp_path, tmp_path / "staging",
                                   run_id=RUN_ID, register_folder=lambda _folder: {"ops": []})


@pytest.fixture
def gscm(tmp_path, monkeypatch):
    """A GSCM export whose portal steps are stand-ins; records what the worker passes on."""
    staged = tmp_path / "gscm.xlsx"
    staged.write_bytes(b"PK\x03\x04workbook")
    seen = SimpleNamespace(downloads=[], opened=0, fail_open=False)

    def open_bookmark(_page, _job, report_progress=None):
        seen.opened += 1
        if seen.fail_open:
            raise RuntimeError("GSCM's Favorite dialog did not open.")

    def edge_download(_page, trigger, **kwargs):
        trigger()
        seen.downloads.append({key: value for key, value in kwargs.items() if key not in ("staging_dir", "progress")})
        return staged

    monkeypatch.setattr(flow_worker.flow_gscm, "open_bookmark", open_bookmark)
    monkeypatch.setattr(flow_worker.flow_gscm, "reload_portal", lambda *_a: None)
    monkeypatch.setattr(flow_worker.flow_gscm, "trigger_excel_export", lambda *_a, **_k: None)
    monkeypatch.setattr(flow_worker, "_edge_completed_download", edge_download)
    monkeypatch.setattr(flow_worker, "_store_completed_download",
                        lambda _staged, output, **_kwargs: {"file_path": str(output), "filename": output.name})
    monkeypatch.delenv("DG_FLOW_REPLAY", raising=False)
    return seen


@pytest.mark.parametrize("limits, expected", [
    (None, {}), (BUILT_IN, {}), ({**BUILT_IN, "download_event_seconds": 5400}, {"event_timeout_seconds": 5400}),
])
def test_a_gscm_run_passes_on_only_a_changed_download_limit(tmp_path, gscm, limits, expected):
    job = _gscm_job(tmp_path) if limits is None else {**_gscm_job(tmp_path), "limits": limits}

    artifacts, _timings = _execute(_NoDownloadPage(), job, tmp_path)

    assert artifacts[0]["status"] == "saved" and gscm.downloads == [expected]


@pytest.mark.parametrize("limits, attempts", [(None, 2), (BUILT_IN, 2), ({**BUILT_IN, "gscm_export_attempts": 4}, 4),
                                             ({**BUILT_IN, "gscm_export_attempts": 1}, 1)])
def test_a_gscm_file_is_tried_the_frozen_number_of_times(tmp_path, gscm, limits, attempts):
    gscm.fail_open = True
    job = _gscm_job(tmp_path) if limits is None else {**_gscm_job(tmp_path), "limits": limits}

    with pytest.raises(RuntimeError, match="Favorite dialog did not open"):
        _execute(_NoDownloadPage(), job, tmp_path)

    assert gscm.opened == attempts and gscm.downloads == []


@pytest.mark.parametrize("limits, attempts, timeout", [
    (None, 3, 3_600_000), (BUILT_IN, 3, 3_600_000),
    ({**BUILT_IN, "export_attempts": 5, "download_event_seconds": 600}, 5, 600_000),
    # The GSCM number does not apply to another website.
    ({**BUILT_IN, "gscm_export_attempts": 9}, 3, 3_600_000),
])
def test_other_websites_use_their_own_tries_and_the_frozen_download_wait(tmp_path, monkeypatch, limits, attempts, timeout):
    monkeypatch.setattr(flow_worker, "_apply_configuration", lambda *_a: None)
    monkeypatch.setattr(flow_worker, "_click_named", lambda *_a: None)
    page = _NoDownloadPage()
    job = _web_job(tmp_path) if limits is None else {**_web_job(tmp_path), "limits": limits}

    with pytest.raises(flow_worker.PlaywrightTimeoutError):
        _execute(page, job, tmp_path)

    assert page.timeouts == [timeout] * attempts
    assert page.waits == [5_000] * (attempts - 1)


# --- Recorded actions ------------------------------------------------------------

class _RecordedPage:
    """Records the time limit each recorded action is given, then stops the replay."""

    def __init__(self):
        self.seen, self.context = [], None

    def goto(self, *_args, **kwargs):
        self.seen.append(("goto", kwargs["timeout"]))

    def expect_popup(self, timeout=None):
        self.seen.append(("popup", timeout))
        raise RuntimeError("stop after the popup wait")

    def expect_download(self, timeout=None):
        self.seen.append(("download", timeout))
        raise RuntimeError("stop after the download wait")


_CLICK = {"id": "press", "action": "click", "page": "page",
          "locator": [{"method": "get_by_role", "args": ["link"], "kwargs": {"name": "Export"}}]}
_DOWNLOAD = {"id": "file", "action": "download", "page": "page", "output": {"format": "csv"}, "steps": [_CLICK]}
_POPUP = {"id": "window", "action": "popup", "page": "page", "result_page": "page1",
          "steps": [{**_CLICK, "id": "open-window"}]}


def _recorded_job(*steps, own_timeout=None):
    opening = {"id": "open", "action": "goto", "page": "page", "args": ["https://portal.example.test/report"]}
    if own_timeout:
        opening["kwargs"] = {"timeout": own_timeout}
    return {"recording": {"revision": 1, "definition": {"version": 2, "timezone": "UTC", "parameters": {},
                                                         "steps": [opening, *steps]}},
            "execution": {"recording_wait_seconds": 1}, "site": {"adapter": "asap_portal"}, "downloads": {}}


def _replay(job, tmp_path):
    page = _RecordedPage()
    with pytest.raises(RuntimeError, match="stop after"):
        runtime.acquire(page, job, lambda *_a, **_k: None, tmp_path, tmp_path / "staging",
                        target=tmp_path, run_id=1, artifacts=[])
    return page.seen


def test_recorded_actions_get_the_frozen_time_limit(tmp_path):
    legacy = _recorded_job(_DOWNLOAD)
    # One second of recorded pacing is added to every event wait, as before.
    assert _replay(legacy, tmp_path) == [("goto", 120_000), ("download", 3_601_000)]
    assert _replay(_with(legacy), tmp_path) == [("goto", 120_000), ("download", 3_601_000)]

    changed = _with(legacy, recorded_action_seconds=300, download_event_seconds=5400)
    assert _replay(changed, tmp_path) == [("goto", 300_000), ("download", 5_401_000)]

    popups = _recorded_job(_POPUP, _DOWNLOAD)
    assert _replay(popups, tmp_path) == [("goto", 120_000), ("popup", 121_000)]
    assert _replay(_with(popups, recorded_action_seconds=45), tmp_path) == [("goto", 45_000), ("popup", 46_000)]


def test_an_action_recorded_with_its_own_time_limit_keeps_it(tmp_path):
    job = _with(_recorded_job(_DOWNLOAD, own_timeout=15_000), recorded_action_seconds=300)
    assert _replay(job, tmp_path)[0] == ("goto", 15_000)


def test_range_controls_wait_the_frozen_time_to_appear():
    waited = []

    class Control:
        def wait_for(self, *, state, timeout):
            waited.append((state, timeout))
            raise RuntimeError("stop after the wait")

    week_step = {"range": {"cell_selector": "td", "start": "2026-W01", "end": "2026-W02"}}
    slider_step = {"range": {"kind": "week"}}
    for arguments in ({}, {"timeout_ms": 300_000}):
        with pytest.raises(RuntimeError, match="stop after the wait"):
            runtime._select_week_range(Control(), week_step, lambda *_a: None, **arguments)
        with pytest.raises(RuntimeError, match="stop after the wait"):
            runtime._set_slider_range(Control(), slider_step, {}, {}, lambda *_a: None, **arguments)
    assert waited == [("visible", 120_000)] * 2 + [("visible", 300_000)] * 2


def test_a_gscm_bookmark_step_gets_the_frozen_time_limit(monkeypatch):
    moment = [0.0]

    def monotonic():
        moment[0] += 0.01  # time passes, so the helper's short waits for a repaint end
        return moment[0]

    monkeypatch.setattr(bookmarks.time, "monotonic", monotonic)

    def select(**arguments):
        # Every look at the list takes a minute of the step's time.
        grid = FakeGrid(long_list(target_at=25))
        wire(monkeypatch, grid, [entry("Saved report", "bookmark-1")])
        moment[0] = 0.0

        def progress(_detail):
            moment[0] += 60

        return bookmarks.select(FakePage(), step(), progress, **arguments), grid

    with pytest.raises(bookmarks.BookmarkSelectionError, match="120-second step deadline"):
        select()
    with pytest.raises(bookmarks.BookmarkSelectionError, match="180-second step deadline"):
        select(deadline_seconds=180)
    result, grid = select(deadline_seconds=3600)
    assert result["bookmark_name"] == "Saved report" and len(grid.clicked) == 1


# --- Network replay ---------------------------------------------------------------

@pytest.mark.parametrize("frozen, environment, flow_choice, expected", [
    (None, None, None, True),        # as before: on
    (None, "0", None, False),        # as before: the worker's environment switches it off
    (None, None, False, False),      # as before: one Flow opts out
    (True, "0", None, True),         # the choice frozen into the job wins over the environment
    (False, None, None, False),
    (False, "1", True, False),
    (True, None, False, False),      # a Flow that opted out stays out
])
def test_replay_follows_the_choice_frozen_into_the_job(monkeypatch, frozen, environment, flow_choice, expected):
    monkeypatch.delenv("DG_FLOW_REPLAY", raising=False)
    if environment is not None:
        monkeypatch.setenv("DG_FLOW_REPLAY", environment)
    job = {"downloads": {} if flow_choice is None else {"network_replay": flow_choice}}
    if frozen is not None:
        job["limits"] = {"network_replay": frozen}
    assert flow_replay.enabled(job) is expected


def _aged_recipe(tmp_path, days):
    assert _capture(tmp_path)
    path = tmp_path / flow_replay.RECIPES_FILENAME
    data = json.loads(path.read_text())
    key = flow_replay.recipe_key(REPLAY_JOB, TASK_KEY)
    data[key]["captured_at"] = (datetime.now(timezone.utc) - timedelta(days=days, hours=1)).isoformat()
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("limits, age_days, kept", [
    (None, 13, True), (None, 14, False), (BUILT_IN, 13, True),
    ({"replay_max_age_days": 7}, 7, False), ({"replay_max_age_days": 7}, 6, True),
    ({"replay_max_age_days": 30}, 20, True),
])
def test_a_saved_export_request_is_reused_for_the_frozen_number_of_days(tmp_path, limits, age_days, kept):
    _aged_recipe(tmp_path, age_days)
    job = REPLAY_JOB if limits is None else {**REPLAY_JOB, "limits": limits}

    found = flow_replay.load_recipe(tmp_path, job, TASK_KEY)

    assert (found is not None) is kept
    # An expired request is thrown away, not just skipped; the limits are no part of its key.
    stored = json.loads((tmp_path / flow_replay.RECIPES_FILENAME).read_text())
    assert (flow_replay.recipe_key(REPLAY_JOB, TASK_KEY) in stored) is kept


@pytest.mark.parametrize("limits, expected", [
    (None, [{}]), (BUILT_IN, [{}]), ({**BUILT_IN, "replay_request_seconds": 120}, [{"timeout_ms": 120_000}]),
    ({**BUILT_IN, "network_replay": False}, []),
])
def test_a_repeated_export_request_gets_the_frozen_time_and_switch(tmp_path, monkeypatch, gscm, limits, expected):
    replays = []
    monkeypatch.setattr(flow_replay, "load_recipe", lambda *_a: {"url": "https://gscm.example.test/export"})
    monkeypatch.setattr(flow_replay, "try_replay", lambda _context, _recipe, _staging, **kwargs: replays.append(kwargs))
    monkeypatch.setattr(flow_replay, "forget_recipe", lambda *_a: None)
    monkeypatch.setattr(flow_replay, "store_capture", lambda *_a: False)
    monkeypatch.setattr(flow_replay, "ExportRequestRecorder", lambda _context: SimpleNamespace(detach=lambda: None))
    page = _NoDownloadPage()
    page.context = object()
    job = _gscm_job(tmp_path) if limits is None else {**_gscm_job(tmp_path), "limits": limits}

    artifacts, _timings = _execute(page, job, tmp_path)

    # The request did not replay here, so the file still arrives through the browser.
    assert replays == expected and artifacts[0]["export_transport"] == "browser"


def test_the_replay_time_limit_is_the_one_given_to_the_request(tmp_path):
    asked = []
    context = SimpleNamespace(request=SimpleNamespace(
        fetch=lambda _url, **kwargs: asked.append(kwargs["timeout"]) or (_ for _ in ()).throw(RuntimeError("offline"))))
    recipe = {"url": "https://gscm.example.test/export"}
    assert flow_replay.try_replay(context, recipe, tmp_path) is None
    assert flow_replay.try_replay(context, recipe, tmp_path, timeout_ms=120_000) is None
    assert asked == [300_000, 120_000]


# --- SQL step ----------------------------------------------------------------------

SQL_TARGET = {"database": "db", "schema": "reporting", "table": "target", "mode": "append"}


def _recording_engine(log):
    """A PostgreSQL stand-in that records how it was asked for and what it was told."""
    class Result:
        def fetchall(self):
            return [("value", "NO", None, "NO", "NEVER")]

    class Connection:
        connection = SimpleNamespace(cursor=lambda: SimpleNamespace(copy_expert=lambda *_a: None, close=lambda: None))

        def begin(self):
            return SimpleNamespace(commit=lambda: None, rollback=lambda: None)

        def execute(self, statement, *_args, **_kwargs):
            if str(statement).startswith("SET LOCAL"):
                log.append(str(statement))
                return None
            return Result()

        def close(self):
            return None

    def engine(database, *credentials, **kwargs):
        log.append(("engine", kwargs))
        return SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None)

    return engine


def _rows(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("value\n1\n", encoding="utf-8")
    return [{"file_path": str(path)}]


@pytest.mark.parametrize("limits, connecting, lock, statement", [
    (None, {}, 600, 1800),
    ({"sql_lock_seconds": 180}, {}, 180, 1800),
    ({"sql_connect_seconds": 30, "sql_lock_seconds": 60, "sql_statement_seconds": 7200},
     {"connect_timeout_seconds": 30}, 60, 7200),
    ({"sql_statement_seconds": "soon"}, {}, 600, 1800),
])
def test_the_sql_step_obeys_the_limits_it_is_given(tmp_path, monkeypatch, limits, connecting, lock, statement):
    log, events = [], []
    monkeypatch.setattr(flow_sql, "_engine", _recording_engine(log))
    arguments = {} if limits is None else {"limits": limits}

    result = flow_sql.load_artifacts(_rows(tmp_path), SQL_TARGET, progress=events.append, **arguments)

    assert result["rows_written"] == 1
    assert log == [("engine", connecting), f"SET LOCAL lock_timeout = '{lock}s'",
                   f"SET LOCAL statement_timeout = '{statement}s'"]
    # The run log names the limits in force.
    copy_started = next(event for event in events if event["stage"] == "sql_copy" and event["status"] == "started")
    assert copy_started["timeout_seconds"] == statement


def test_the_connection_limit_becomes_the_drivers_connect_timeout(monkeypatch):
    for name, value in (("UPLOAD_PGHOST", "warehouse.example.test"), ("UPLOAD_PGPORT", "5432"),
                        ("UPLOAD_PGDATABASE", "analytics"), ("UPLOAD_PGUSER", "writer"),
                        ("UPLOAD_PGPASSWORD", "synthetic-password")):
        monkeypatch.setattr(config, name, value)
    assert flow_sql._engine("analytics").url.query["connect_timeout"] == "60"
    assert flow_sql._engine("analytics", connect_timeout_seconds=30).url.query["connect_timeout"] == "30"


def _sql_retry_job(tmp_path):
    saved = tmp_path / "saved.csv"
    saved.write_text("value\n1\n", encoding="utf-8")
    return {"job_type": "sql_retry", "flow": {"id": 7, "name": "Committed", "source_type": "portal"},
            "sql_handoff": {"enabled": True, **SQL_TARGET},
            "sql_retry": {"source_run_id": 3, "artifacts": [
                {"file_path": str(saved), "filename": saved.name, "row_count": 1, "status": "saved"}]}}


@pytest.mark.parametrize("limits, expected", [
    (None, {}), (BUILT_IN, {}),
    ({**BUILT_IN, "sql_statement_seconds": 7200}, {"limits": {"sql_statement_seconds": 7200}}),
    ({**BUILT_IN, "sql_connect_seconds": 30, "sql_lock_seconds": 60},
     {"limits": {"sql_connect_seconds": 30, "sql_lock_seconds": 60}}),
])
def test_a_run_hands_the_sql_step_only_the_limits_it_changes(tmp_path, monkeypatch, limits, expected):
    calls = []

    def load_artifacts(artifacts, target, progress=None, **kwargs):
        calls.append(kwargs)
        return {"rows_written": 1, "files_loaded": len(artifacts), "target": "db.reporting.target"}

    monkeypatch.setattr(flow_sql, "load_artifacts", load_artifacts)
    job = _sql_retry_job(tmp_path) if limits is None else {**_sql_retry_job(tmp_path), "limits": limits}

    flow_worker.execute_flow(None, job, lambda *_a, **_k: None, tmp_path / "profile",
                             run_id=11, register_folder=lambda _folder: {"ops": []})

    # With nothing changed the load is called exactly as it always was.
    assert calls == [expected]


def _refresh_job(**limits):
    job = {"sql_handoff": {"enabled": True, **SQL_TARGET},
           "post_sql_refresh": {"mode": "manual", "views": [view("bi", "sales_mv")]}}
    return _with(job, **limits) if limits else job


@pytest.mark.parametrize("job, lock, statement", [
    (_refresh_job(), 600_000, 3_600_000),
    ({**_refresh_job(), "limits": BUILT_IN}, 600_000, 3_600_000),
    (_refresh_job(view_lock_seconds=120, view_refresh_seconds=14_400), 120_000, 14_400_000),
])
def test_a_materialized_view_refresh_obeys_the_frozen_limits(job, lock, statement):
    engines = FakeEngines()

    results = flow_view_refresh.execute_after_sql(
        job, lambda *_a, **_k: None, [], [{"phase": "total"}], {}, engine_factory=engines)

    assert [item["status"] for item in results] == ["succeeded"]
    told = [sql for _database, sql in engines.events if sql.startswith("SET LOCAL")]
    assert told == [f"SET LOCAL lock_timeout = '{lock}ms'", f"SET LOCAL statement_timeout = '{statement}ms'"]


@pytest.mark.parametrize("job, expected", [
    (_refresh_job(), [()]), ({**_refresh_job(), "limits": BUILT_IN}, [()]),
    (_refresh_job(sql_connect_seconds=30), [((None,), {"connect_timeout_seconds": 30})]),
])
def test_a_view_refresh_connects_with_the_same_frozen_limit_as_the_load(monkeypatch, job, expected):
    engines, asked = FakeEngines(), []

    def engine(database, *credentials, **kwargs):
        asked.append((credentials, kwargs) if credentials or kwargs else ())
        return engines(database)

    monkeypatch.setattr(flow_sql, "_engine", engine)

    flow_view_refresh.execute_after_sql(job, lambda *_a, **_k: None, [], [{"phase": "total"}], {})

    assert asked == expected


# --- Scripts -------------------------------------------------------------------------

def test_a_transformation_script_is_stopped_after_the_frozen_time(tmp_path, monkeypatch):
    script = tmp_path / "transform.py"
    script.write_text("pass\n", encoding="utf-8")
    source = tmp_path / "rows.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    limits = []

    def run(_command, **kwargs):
        limits.append(kwargs["timeout"])
        raise RuntimeError("stop before the script runs")

    monkeypatch.setattr(flow_worker.subprocess, "run", run)
    config_ = {"enabled": True, "script_path": str(script)}
    for arguments in ({}, {"timeout_seconds": 2700}):
        with pytest.raises(RuntimeError, match="stop before the script runs"):
            flow_worker._run_transformations([{"file_path": str(source)}], config_, **arguments)
    assert limits == [3600, 2700]


@pytest.mark.parametrize("limits, expected", [
    (None, {}), (BUILT_IN, {}), ({**BUILT_IN, "script_timeout_seconds": 2700}, {"timeout_seconds": 2700}),
])
def test_a_run_hands_its_transformation_only_a_changed_time_limit(tmp_path, monkeypatch, limits, expected):
    calls = []
    artifact = {"file_path": str(tmp_path / "rows.csv"), "status": "saved"}
    monkeypatch.setattr(flow_worker, "_publish_direct_artifacts", lambda _job, artifacts, **_k: artifacts)
    monkeypatch.setattr(flow_worker, "_run_transformations",
                        lambda artifacts, config_, **kwargs: calls.append(kwargs) or artifacts)
    job = {"flow": {"id": 7, "name": "Transformed", "source_type": "portal"},
           "transformation": {"enabled": True, "script_path": "transform.py"}, "sql_handoff": {"enabled": False},
           "downloads": {"file_format": "csv"}}
    if limits is not None:
        job["limits"] = limits

    flow_worker.execute_flow(None, job, lambda *_a, **_k: None, tmp_path / "profile", run_id=11,
                             register_folder=lambda _folder: {"ops": []},
                             acquire_bundle=lambda *_a, **_k: ([artifact], [{"phase": "total", "duration_ms": 1}]))

    assert calls == [expected]


def test_a_script_flow_without_a_time_limit_in_its_job_uses_the_frozen_default():
    # Every job a server builds names the limit; this is the fallback for one that does not.
    assert flow_job_limits.value({}, "script_timeout_seconds", flow_worker.flow_python.SCRIPT_TIMEOUT_SECONDS) == 3600
    assert flow_job_limits.value(_with({}, script_timeout_seconds=2700), "script_timeout_seconds", 3600) == 2700


# --- Screenshots and recording tests ----------------------------------------------

def _screenshots(profile, count, **arguments):
    page = SimpleNamespace(screenshot=lambda path, full_page: Path(path).write_bytes(b"png"))
    for index in range(count):
        assert flow_worker._failure_screenshot(page, profile, f"run-{index}", **arguments)
    return sorted(path.name for path in (profile / "diagnostics").glob("failure-*.png"))


def test_a_worker_keeps_the_frozen_number_of_failure_screenshots(tmp_path):
    assert len(_screenshots(tmp_path / "default", 25)) == 20
    assert _screenshots(tmp_path / "three", 8, keep=3) == ["failure-00.png", "failure-01.png", "failure-02.png"]
    assert len(_screenshots(tmp_path / "hundred", 120, keep=100)) == 100


@pytest.mark.parametrize("limits, expected", [
    (None, {}), ({"scan_overlay_seconds": 1800, "failure_screenshots_kept": 20}, {}),
    ({"failure_screenshots_kept": 5}, {"keep": 5}),
])
def test_a_failed_scan_keeps_the_frozen_number_of_screenshots(tmp_path, monkeypatch, limits, expected):
    kept = []
    monkeypatch.setattr(flow_worker, "discover_asap_catalog",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("The portal menu did not open.")))
    monkeypatch.setattr(flow_worker, "_failure_screenshot",
                        lambda _page, _profile, _label, **kwargs: kept.append(kwargs) or "saved.png")
    job = {"site": {"adapter": "asap_portal"}, "execution": {"browser_mode": "headless", "browser_channel": "chrome"}}
    if limits is not None:
        job["limits"] = limits

    _order, reports = _run_worker_over(tmp_path, monkeypatch, [{"scan": {"id": 5, "job": job}}])

    assert kept == [expected]
    assert reports[-1][1]["status"] == "failed" and "Screenshot: saved.png" in reports[-1][1]["error"]


def _test_folders(root, count):
    for number in range(1, count + 1):
        (root / str(number)).mkdir(parents=True)
    return root / str(count)


def test_the_frozen_number_of_recording_test_folders_is_kept(tmp_path):
    default = tmp_path / "default"
    flow_recorder_worker._prune_validation_folders(default, _test_folders(default, 9))
    assert sorted(int(path.name) for path in default.iterdir()) == [7, 8, 9]

    changed = tmp_path / "changed"
    current = _test_folders(changed, 9)
    arguments = flow_job_limits.given(_with({}, recording_test_folders_kept=5), keep=("recording_test_folders_kept", 3))
    flow_recorder_worker._prune_validation_folders(changed, current, **arguments)
    assert sorted(int(path.name) for path in changed.iterdir()) == [5, 6, 7, 8, 9]

    one = tmp_path / "one"
    flow_recorder_worker._prune_validation_folders(one, _test_folders(one, 4), keep=1)
    assert [path.name for path in one.iterdir()] == ["4"]  # the test that just ran is never removed


# --- Catalog scans -------------------------------------------------------------------

class _OverlayPage:
    def __init__(self, clock):
        self.clock = clock

    def wait_for_timeout(self, milliseconds):
        self.clock.now += milliseconds / 1_000

    def is_closed(self):
        return False


def _scan_job(**extra):
    return {"site": {"base_url": "https://portal.example.test", "auth_url": None},
            "discovery": {"max_duration_minutes": 90, "scope": ["*"],
                          "report_paths": [["Market", "Group", "Target Report"]]}, **extra}


@pytest.mark.parametrize("job, seconds", [
    (_scan_job(), 1800), (_scan_job(limits={"scan_overlay_seconds": 1800, "failure_screenshots_kept": 20}), 1800),
    (_scan_job(limits={"scan_overlay_seconds": 120}), 120),
])
def test_a_catalog_scan_skips_a_report_still_loading_after_the_frozen_time(monkeypatch, tmp_path, clock, job, seconds):
    monkeypatch.setattr(flow_worker, "_asap_goto", lambda *_args: False)
    monkeypatch.setattr(flow_worker, "_asap_open_report", lambda *_args: object())
    monkeypatch.setattr(flow_worker, "_asap_export_view_candidates", lambda *_args: [])
    monkeypatch.setattr(flow_worker, "_asap_loading_overlay_visible", lambda _page: True)

    with pytest.raises(RuntimeError, match=f"Target Report: ASAP loading overlay did not clear within {seconds} seconds"):
        flow_worker.discover_asap_catalog(_OverlayPage(clock), job, lambda *_a, **_k: None, tmp_path)

    assert seconds <= clock.now < seconds + 5


@pytest.mark.parametrize("limits, expected", [
    (None, {}), ({"scan_overlay_seconds": 1800}, {}), ({"scan_overlay_seconds": 300}, {"overlay_seconds": 300}),
])
def test_the_menu_inventory_of_a_scan_gets_the_same_loading_limit(monkeypatch, tmp_path, clock, limits, expected):
    asked = []
    monkeypatch.setattr(flow_worker, "_asap_goto", lambda *_args: False)
    monkeypatch.setattr(flow_worker, "_asap_discover_menu_reports", lambda _page, _scope, **kwargs: asked.append(kwargs) or [])
    job = _scan_job() if limits is None else _scan_job(limits=limits)
    job["discovery"]["report_paths"] = []

    reports, _timings, complete = flow_worker.discover_asap_catalog(
        _OverlayPage(clock), job, lambda *_a, **_k: None, tmp_path)

    assert (reports, complete, asked) == ([], True, [expected])


@pytest.mark.parametrize("frozen_minutes, budget", [(90, 90), (45, 45), (None, 90)])
def test_a_catalog_scan_stops_at_the_frozen_time_budget_and_names_it(monkeypatch, tmp_path, clock, frozen_minutes, budget):
    job = _scan_job()
    if frozen_minutes is None:
        del job["discovery"]["max_duration_minutes"]  # a scan queued without one keeps the built-in budget
    else:
        job["discovery"]["max_duration_minutes"] = frozen_minutes

    def scan(sign_in_seconds):
        events = []
        clock.now = 0.0
        monkeypatch.setattr(flow_worker, "_asap_goto", lambda *_args: clock.sleep(sign_in_seconds))
        monkeypatch.setattr(flow_worker, "_asap_open_report",
                            lambda *_args: (_ for _ in ()).throw(RuntimeError("the report did not open")))
        try:
            result = flow_worker.discover_asap_catalog(
                _OverlayPage(clock), job, lambda _status, detail: events.append(detail), tmp_path)
        except RuntimeError as exc:
            result = str(exc)
        return result, [event["stage"] for event in events], events[-1]["message"]

    # One second inside the budget the report is still started...
    result, stages, _message = scan(budget * 60 - 1)
    assert "filter_inspection" in stages and "time_budget_reached" not in stages
    assert "the report did not open" in result
    # ...and once the budget is used up it is not, and the scan says which budget that was.
    (reports, _timings, complete), stages, message = scan(budget * 60)
    assert reports == [] and complete is False and stages[-1] == "time_budget_reached"
    assert message.startswith(f"The {budget}-minute scan budget was reached.")
