"""HTML report refresh Flows: configuration, access, stage events, timings, history and Reports.

Synthetic fixtures only: a disposable SQLite database, a controller script that
is never executed, and console lines posted the way a worker posts them.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import database, html_reports, settings_registry, usage
from app.flow_script_live import LiveObserver
from app.routers import flows, lineage, pipelines, reports, system_settings
from app.scanner import control, pbi_sync
from test_flows import _request, flow_db  # noqa: F401  (fixture)

# Saving a Flow here is set-up, not the subject: see skip_flow_files in conftest.py.
pytestmark = pytest.mark.usefixtures("skip_flow_files")

KEY = "smp_trade_in_value_html"
WORKER = "test-worker"


@pytest.fixture
def queue(monkeypatch):
    """Queue runs without starting a worker process."""
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode="headless": {"status": "online"})


def _configure(tmp_path, **overrides):
    controller = tmp_path / "controller" / "refresh.py"
    controller.parent.mkdir(exist_ok=True)
    controller.write_text("raise SystemExit('never executed by these tests')\n", encoding="utf-8")
    values = {
        html_reports.CONTROLLER_SCRIPT: str(controller),
        html_reports.REPORT_ROOT: str(tmp_path / "reports"),
        html_reports.ALLOWED_REPORTS: [KEY, "smp_stock_cover_html"],
        html_reports.CONTROLLER_REPOSITORY: "https://github.example.com/team/dashboard-refresh-controller",
    }
    values.update(overrides)
    settings_registry.save(values)
    return controller


def _flow(**overrides):
    values = {
        "name": "Trade-in value", "source_type": "python",
        "html_report": {"report_key": KEY, "mode": "live",
                        "repository_url": "https://github.example.com/team/smp-trade-in-value-html",
                        "site_url": "https://pages.example.com/team/smp-trade-in-value-html/"},
        "schedule_type": "daily", "schedule_time": "07:30",
    }
    values.update(overrides)
    return flows.FlowWrite(**values)


def _guest(person_id=2):
    return SimpleNamespace(state=SimpleNamespace(actor="Guest", person_id=person_id, is_host=False))


def _people():
    with database.get_db() as db:
        db.execute("INSERT INTO people(id, name, role) VALUES (1, 'Ana Costa', 'BI')")
        db.execute("INSERT INTO people(id, name, role) VALUES (2, 'Omar Haddad', 'Business')")


def _event(stage, status, duration=None, *, report=KEY, version=1):
    payload = {"version": version, "report": report, "stage": stage, "status": status}
    if duration is not None:
        payload["duration_seconds"] = duration
    return "METRONOME_STAGE " + json.dumps(payload)


def _running(run_id, started="2026-11-17T08:00:00"):
    flows.register_worker(flows.WorkerRegister(
        worker_id=WORKER, display_name="Test", capabilities={"adapters": ["python_script"]},
    ))
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status='running', worker_id=?, started_at=? WHERE id=?",
                   (WORKER, started, run_id))


def _finish(run_id, status, finished="2026-11-17T08:02:00", error=None):
    with database.get_db() as db:
        db.execute("UPDATE flow_runs SET status=?, finished_at=?, error=? WHERE id=?",
                   (status, finished, error, run_id))


def _post(run_id, lines, first=1):
    """Console lines as a worker posts them: (seconds after 08:00 UTC, stream, text)."""
    body = flows.WorkerLive(lines=[
        flows.LiveLine(line_no=first + index, stream=stream, text=text, pid=7,
                       at=f"2026-11-17T08:{int(offset) // 60:02d}:{offset % 60:06.3f}+00:00")
        for index, (offset, stream, text) in enumerate(lines)
    ])
    return flows.update_run_live(WORKER, run_id, body)


# --- Configuration ---------------------------------------------------------------

def test_settings_refuse_unsafe_controller_values(flow_db):
    with pytest.raises(settings_registry.SettingsRejected) as refused:
        settings_registry.save({
            html_reports.CONTROLLER_REPOSITORY: "https://deploy:ghp_secret@github.example.com/team/controller",
            html_reports.CONTROLLER_SCRIPT: "D:\\Tools\\controller\\*.py",
            html_reports.REPORT_ROOT: "D:\\Reports\\{flow}",
            html_reports.ALLOWED_REPORTS: [KEY, "two words"],
            html_reports.ARGUMENTS: "--report {report} {token}",
            html_reports.DRY_RUN_ARGUMENTS: "",
            html_reports.LIVE_ARGUMENTS: '--publish "unclosed',
        })
    errors = refused.value.errors
    assert "user name, password or token" in errors[html_reports.CONTROLLER_REPOSITORY]
    assert "wildcards" in errors[html_reports.CONTROLLER_SCRIPT]
    assert "braces" in errors[html_reports.REPORT_ROOT]
    assert "two words is not a report ID" in errors[html_reports.ALLOWED_REPORTS]
    assert "{token} is not a known token" in errors[html_reports.ARGUMENTS]
    assert errors[html_reports.DRY_RUN_ARGUMENTS] == "This cannot be empty."
    with pytest.raises(settings_registry.SettingsRejected) as refused:
        settings_registry.save({html_reports.DRY_RUN_ARGUMENTS: "   "})
    assert refused.value.errors[html_reports.DRY_RUN_ARGUMENTS] == "This cannot be empty."
    assert "dry-run arguments" in html_reports.setup_problems({"dry_run_arguments": "", "script": "x", "allowed_reports": ["a"]})[0]
    assert "unclosed quote" in errors[html_reports.LIVE_ARGUMENTS]
    with pytest.raises(settings_registry.SettingsRejected) as refused:
        settings_registry.save({html_reports.ARGUMENTS: "--report-root {report_root}",
                                html_reports.CONTROLLER_SCRIPT: "D:\\Tools\\controller\\refresh.ps1"})
    assert "{report}" in refused.value.errors[html_reports.ARGUMENTS]
    assert ".py file" in refused.value.errors[html_reports.CONTROLLER_SCRIPT]
    # Nothing was saved, so nothing can be queued yet.
    assert html_reports.public_settings()["ready"] is False


def test_only_the_host_pc_changes_the_controller_settings(flow_db):
    with pytest.raises(flows.HTTPException) as refused:
        system_settings.save_settings(
            system_settings.SettingsWrite(values={html_reports.ALLOWED_REPORTS: ["anything"]}), _guest(),
        )
    assert refused.value.status_code == 403
    assert settings_registry.get(html_reports.ALLOWED_REPORTS) == []


def test_a_flow_is_saved_only_for_a_configured_controller_and_an_allowed_report(flow_db, tmp_path):
    with pytest.raises(flows.HTTPException) as refused:
        flows.create_flow(_flow(), _request())
    assert refused.value.status_code == 409
    assert "Settings > Flows > HTML reports" in refused.value.detail
    _configure(tmp_path)
    with pytest.raises(flows.HTTPException) as refused:
        flows.create_flow(_flow(html_report={"report_key": "not_on_the_list", "mode": "live"}), _request())
    assert refused.value.status_code == 409 and "not in the allowed list" in refused.value.detail
    for bad in ({"report_key": "two words", "mode": "live"},
                {"report_key": KEY, "mode": "publish"},
                {"report_key": KEY, "mode": "live", "command": "evil.exe"},
                {"report_key": KEY, "mode": "live", "site_url": "https://user:pass@pages.example.com/"},
                {"report_key": KEY, "mode": "live", "repository_url": "file:///C:/repo"}):
        with pytest.raises(ValidationError):
            _flow(html_report=bad)
    with pytest.raises(ValidationError):
        _flow(source_type="file", local_file_path="C:\\in.csv")
    assert flows.list_flows() == []


def test_the_browser_never_chooses_the_command(flow_db, tmp_path):
    controller = _configure(tmp_path)
    saved = flows.create_flow(_flow(
        python_scripts=["C:\\evil\\payload.py"], python_script_arguments=["--wipe"],
        python_interpreter="C:\\evil\\python.exe", python_run_mode="outputs",
    ), _request())
    assert saved["python_scripts"] == [str(controller)]
    assert saved["python_script_arguments"] == [f"--report {KEY} --report-root {tmp_path / 'reports'}"]
    assert saved["python_interpreter"] is None and saved["python_run_mode"] == "run"
    assert saved["html_report"]["mode"] == "live"
    # A generic edit without the block keeps the Flow an HTML report refresh.
    body = _flow(html_report=None, python_scripts=["C:\\evil\\payload.py"], name="Trade-in value (EU)")
    updated = flows.update_flow(saved["id"], body, _request())
    assert updated["html_report"]["report_key"] == KEY
    assert updated["python_scripts"] == [str(controller)]
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
    assert job["python_source"]["scripts"] == [str(controller)]
    assert job["python_source"]["mode"] == "run" and job["python_source"]["interpreter"] == ""
    assert job["flow"]["source_type"] == "python"


def test_only_the_owner_or_host_edits_and_anyone_may_run(flow_db, tmp_path, queue):
    _people()
    _configure(tmp_path)
    saved = flows.create_flow(_flow(owner_person_id=1), _request())
    with pytest.raises(flows.HTTPException) as refused:
        flows.update_flow(saved["id"], _flow(owner_person_id=1, name="Renamed"), _guest(person_id=2))
    assert refused.value.status_code == 403
    with pytest.raises(flows.HTTPException) as refused:
        flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=False), _guest(person_id=2))
    assert refused.value.status_code == 403
    owner_edit = flows.update_flow(saved["id"], _flow(owner_person_id=1, name="Renamed"), _guest(person_id=1))
    assert owner_edit["name"] == "Renamed"
    assert flows.queue_run(saved["id"], _guest(person_id=2), dry_run=True)["status"] == "queued"


# --- Queuing: fail closed, dry runs and one refresh per report ------------------------

def test_each_run_rebuilds_the_command_and_fails_closed(flow_db, tmp_path, queue):
    _configure(tmp_path)
    saved = flows.create_flow(_flow(enabled=True), _request())
    settings_registry.save({html_reports.ARGUMENTS: "{report} --root {report_root} --run {run_id}"})
    run = flows.queue_flow_run(saved["id"], actor="Analyst")
    assert run["job"]["python_source"]["arguments"] == [f'{KEY} --root {tmp_path / "reports"} --run "{{run_id}}"']
    flows.stop_run(saved["id"], _request())

    settings_registry.save({html_reports.ALLOWED_REPORTS: ["smp_stock_cover_html"]})
    with pytest.raises(flows.HTTPException) as refused:
        flows.queue_flow_run(saved["id"], actor="Analyst")
    assert refused.value.status_code == 409 and "not in the allowed list" in refused.value.detail
    # A scheduled run is not queued either; the reason is kept on the Flow.
    with database.get_db() as db:
        db.execute("UPDATE flows SET next_run_at='2020-01-01T00:00:00' WHERE id=?", (saved["id"],))
    assert flows.queue_due_flows()["count"] == 0
    assert "not in the allowed list" in flows.get_flow(saved["id"])["last_error"]

    settings_registry.save({html_reports.ALLOWED_REPORTS: [KEY], html_reports.CONTROLLER_SCRIPT: None})
    with pytest.raises(flows.HTTPException) as refused:
        flows.queue_flow_run(saved["id"], actor="Analyst")
    assert "Set the controller script" in refused.value.detail


def test_each_placeholder_becomes_exactly_one_argument(flow_db, tmp_path):
    root = tmp_path / "HTML Reports"
    _configure(tmp_path, **{html_reports.REPORT_ROOT: str(root),
                            html_reports.ARGUMENTS: '--report {report} --report-root "{report_root}" --name "{flow}" --day {date}'})
    saved = flows.create_flow(_flow(name="Trade in value"), _request())
    line = saved["python_script_arguments"][0]
    rendered = flows.flow_python.render_arguments(line, flow_name="Trade in value", run_id=7, date="2026-11-17")
    assert flows.flow_python.split_arguments(rendered) == [
        "--report", KEY, "--report-root", str(root), "--name", "Trade in value", "--day", "2026-11-17",
    ]
    preview = html_reports.public_settings()["previews"][KEY]
    assert preview["live"] == line and preview["dry_run"] == line + " --dry-run"


def test_dry_runs_are_labelled_and_never_publish(flow_db, tmp_path, queue):
    _configure(tmp_path, **{html_reports.LIVE_ARGUMENTS: "--publish"})
    live = flows.create_flow(_flow(), _request())
    dry = flows.create_flow(_flow(name="Stock cover", html_report={"report_key": "smp_stock_cover_html", "mode": "dry_run"}),
                            _request())
    test_run = flows.queue_run(live["id"], _request(), dry_run=True)
    assert test_run["job"]["html_report"]["publishes"] is False
    assert test_run["job"]["python_source"]["arguments"][0].endswith("--dry-run")
    # Asking again while that dry run waits restarts its worker, as Run does.
    assert flows.queue_run(live["id"], _request(), dry_run=True)["resumed"] is True
    flows.stop_run(live["id"], _request())
    published = flows.queue_run(live["id"], _request(), dry_run=False)
    assert published["job"]["html_report"]["publishes"] is True
    assert published["job"]["python_source"]["arguments"][0].endswith("--publish")
    with pytest.raises(flows.HTTPException) as refused:
        flows.queue_run(live["id"], _request(), dry_run=True)
    assert "live run that publishes is already queued" in refused.value.detail
    flows.stop_run(live["id"], _request())
    remote = flows.queue_flow_run(live["id"], actor="remote", trigger_type="remote_test")
    assert remote["job"]["html_report"]["publishes"] is False  # a remote test never publishes
    scheduled_test = flows.queue_run(dry["id"], _request(), dry_run=False)
    assert scheduled_test["job"]["html_report"]["mode"] == "dry_run"
    assert "--dry-run" in scheduled_test["job"]["python_source"]["arguments"][0]

    script = tmp_path / "plain.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    plain = flows.create_flow(flows.FlowWrite(name="Plain", source_type="python", python_scripts=[str(script)],
                                              python_run_mode="run"), _request())
    with pytest.raises(flows.HTTPException) as refused:
        flows.queue_run(plain["id"], _request(), dry_run=True)
    assert refused.value.status_code == 422 and "Only an HTML report refresh has a dry run" in refused.value.detail


def test_a_queued_live_run_is_not_resumed_after_the_flow_became_a_dry_run(flow_db, tmp_path, queue):
    _configure(tmp_path)
    saved = flows.create_flow(_flow(), _request())
    waiting = flows.queue_run(saved["id"], _request(), dry_run=False)
    assert waiting["job"]["html_report"]["publishes"] is True
    flows.update_flow(saved["id"], _flow(html_report={"report_key": KEY, "mode": "dry_run"}), _request())
    with pytest.raises(flows.HTTPException) as refused:
        flows.queue_run(saved["id"], _request(), dry_run=False)
    assert refused.value.status_code == 409
    assert "live run that publishes is already queued" in refused.value.detail
    flows.stop_run(saved["id"], _request())
    assert flows.queue_run(saved["id"], _request(), dry_run=False)["job"]["html_report"]["publishes"] is False


def test_one_refresh_per_report_at_a_time(flow_db, tmp_path, queue):
    _configure(tmp_path)
    first = flows.create_flow(_flow(), _request())
    second = flows.create_flow(_flow(name="Trade-in value test", html_report={"report_key": KEY, "mode": "dry_run"}),
                               _request())
    flows.queue_flow_run(first["id"], actor="Analyst")
    with pytest.raises(flows.HTTPException) as refused:
        flows.queue_flow_run(second["id"], actor="Analyst")
    assert refused.value.status_code == 409
    assert "already being refreshed by Flow 'Trade-in value'" in refused.value.detail


# --- Event parsing ---------------------------------------------------------------

@pytest.mark.parametrize(("line", "kind"), [
    ("plain console output", "none"),
    (" METRONOME_STAGE " + json.dumps({"version": 1}), "none"),  # must begin the line
    ("METRONOME_STAGE{}", "malformed"),
    ("METRONOME_STAGE {not json", "malformed"),
    ("METRONOME_STAGE [1, 2]", "malformed"),
    ("METRONOME_STAGE " + json.dumps({"version": "1", "report": KEY, "stage": "Build", "status": "started"}), "malformed"),
    ("METRONOME_STAGE " + json.dumps({"version": True, "report": KEY, "stage": "Build", "status": "started"}), "malformed"),
    (_event("Build", "started", version=2), "ignored"),
    (_event("Build", "paused"), "malformed"),
    (_event("", "started"), "malformed"),
    (_event("Build", "succeeded"), "malformed"),            # completion without duration_seconds
    (_event("Build", "failed", "12"), "malformed"),
    (_event("Build", "succeeded", -1), "malformed"),
    (_event("Build", "succeeded", True), "malformed"),
    ('METRONOME_STAGE {"version":1,"report":"r","stage":"Build","status":"failed","duration_seconds":NaN}', "malformed"),
    (_event("Build", "succeeded", 8 * 24 * 3600), "malformed"),
    (_event("Build", "started"), "event"),
    (_event("Build", "RUNNING"), "event"),
    (_event("Build", "failed", 0), "event"),
])
def test_parse_event_reads_only_well_formed_version_1_events(line, kind):
    assert html_reports.parse_event(line)[0] == kind


def test_only_the_used_fields_are_read_and_names_never_carry_credentials():
    kind, event = html_reports.parse_event("METRONOME_STAGE " + json.dumps({
        "version": 1, "report": KEY, "status": "succeeded", "duration_seconds": 4.25,
        "stage": "Push https://deploy:ghp_abcdefghijklmnopqrstuvwx@github.example.com/team/repo\twith token=abc123",
        "password": "hunter2", "message": "<script>alert(1)</script>", "started_at": "1999-01-01T00:00:00Z",
    }))
    assert kind == "event"
    assert event == {"report": KEY, "stage": event["stage"], "status": "succeeded", "duration_seconds": 4.25}
    assert "ghp_" not in event["stage"] and "deploy" not in event["stage"] and "abc123" not in event["stage"]
    assert event["stage"].startswith("Push https://[redacted]@github.example.com")
    assert html_reports.clean_text("Using ghp_" + "A" * 36) == "Using [redacted]"
    assert "abcdefghijkl" not in html_reports.clean_text("Authorization: Bearer abcdefghijkl")
    assert html_reports.clean_text("password=hunter2 then Build") == "password=[redacted] then Build"
    assert len(html_reports.clean_text("x" * 5000)) == html_reports.MAX_STAGE_NAME


# --- Live runs: timings, partial and malformed events --------------------------------

def test_live_lines_build_the_stage_timeline_once(flow_db, tmp_path, queue):
    _configure(tmp_path)
    saved = flows.create_flow(_flow(), _request())
    run_id = flows.queue_flow_run(saved["id"], actor="Analyst")["id"]
    _running(run_id)
    batch = [
        (0, "stdout", "Refreshing the report"),
        (1, "stdout", _event("Extract from meto_cloud", "started")),
        (2, "stderr", _event("Ignored on stderr", "started")),
        (3, "stdout", _event("Another report", "started", report="smp_stock_cover_html")),
        (43.5, "stdout", _event("Extract from meto_cloud", "succeeded", 42.4)),
        (44, "stdout", "METRONOME_STAGE {truncated"),
        (45, "stdout", _event("Validate data", "started")),
        (46, "stdout", _event("Validate data", "running")),  # a repeated start
    ]
    assert _post(run_id, batch)["accepted"] is True
    assert _post(run_id, batch)["accepted"] is True  # a retried post adds nothing
    run = flows.get_run(run_id)
    assert "stages_json" not in run
    block = run["html_report"]
    assert block["counts"] == {"events": 4, "malformed": 1, "ignored": 1, "duplicates": 1, "overflow": 0}
    assert [(stage["name"], stage["status"]) for stage in block["stages"]] == [
        ("Extract from meto_cloud", "succeeded"), ("Validate data", "running"),
    ]
    extract, validate = block["stages"]
    assert extract["started_at"] == "2026-11-17T08:00:01.000"
    assert extract["finished_at"] == "2026-11-17T08:00:43.500"
    assert extract["duration_seconds"] == 42.4  # the controller's figure, bounds-checked
    assert validate["started_at"] == "2026-11-17T08:00:45.000" and validate["duration_seconds"] is None
    assert run["live"]["stage"] == "Validate data"
    assert run["status"] == "running" and block["total_seconds"] is None
    # Later lines continue the same record.
    _post(run_id, [(50, "stdout", _event("Validate data", "succeeded", 5))], first=len(batch) + 1)
    assert flows.get_run(run_id)["html_report"]["stages"][1]["status"] == "succeeded"
    # The console keeps every original line.
    assert flows.get_run_output(run_id, after_line=0, limit=50)["lines"][5]["text"] == "METRONOME_STAGE {truncated"


def test_worker_console_lines_reach_the_timeline(flow_db, tmp_path, queue):
    """The live observer's own line format, timestamps included, is what the server reads."""
    _configure(tmp_path)
    saved = flows.create_flow(_flow(), _request())
    run_id = flows.queue_flow_run(saved["id"], actor="Analyst")["id"]
    _running(run_id)
    posts = []
    observer = LiveObserver(lambda body: posts.append(body) or {"terminal": False},
                            lambda status, detail: None, {"GIT_TOKEN": "s3cret-value"})
    observer.line("stdout", _event("Build HTML", "started"), 7)
    observer.line("stdout", _event("Build HTML s3cret-value", "started"), 7)
    observer.line("stdout", _event("Build HTML", "succeeded", 2.5), 7)
    observer.flush(force=True)
    flows.update_run_live(WORKER, run_id, flows.WorkerLive(**posts[0]))
    stages = flows.get_run(run_id)["html_report"]["stages"]
    assert [(stage["name"], stage["status"]) for stage in stages] == [
        ("Build HTML", "succeeded"), ("Build HTML [redacted]", "running"),
    ]
    assert datetime.fromisoformat(stages[0]["started_at"]) <= datetime.fromisoformat(stages[0]["finished_at"])


def test_a_completion_without_its_start_is_placed_by_its_duration(monkeypatch):
    # The work PC runs in Dubai time; stored instants stay UTC (AGENTS.md).
    monkeypatch.setenv("TZ", "Asia/Dubai")
    time.tzset()
    try:
        state = html_reports.apply_lines(
            html_reports.empty_state(),
            [{"line_no": 1, "stream": "stdout", "text": _event("Publish", "succeeded", 30), "at": "2026-11-17T08:01:00Z"}],
            KEY, received_at=datetime(2026, 11, 17, 9, 0),
        )
    finally:
        monkeypatch.undo()
        time.tzset()
    stage = state["stages"][0]
    assert stage["started_at"] == "2026-11-17T08:00:30.000" and stage["start_inferred"] is True
    # An unreadable capture time falls back to when the server received the line.
    state = html_reports.apply_lines(
        html_reports.empty_state(),
        [{"line_no": 1, "stream": "stdout", "text": _event("Build", "started"), "at": "yesterday"}],
        KEY, received_at=datetime(2026, 11, 17, 9, 0),
    )
    assert state["stages"][0]["started_at"] == "2026-11-17T09:00:00.000"
    # More than MAX_STAGES stages are counted, not stored.
    lines = [{"line_no": n, "stream": "stdout", "text": _event(f"Stage {n}", "started"), "at": "2026-11-17T08:00:00Z"}
             for n in range(1, html_reports.MAX_STAGES + 6)]
    state = html_reports.apply_lines(html_reports.empty_state(), lines, KEY, received_at=datetime(2026, 11, 17))
    assert len(state["stages"]) == html_reports.MAX_STAGES and state["overflow"] == 5


# --- Results: the exit code decides --------------------------------------------------

def _summary(status, lines, *, publishes=True, dropped=0):
    state = html_reports.apply_lines(html_reports.empty_state(), [
        {"line_no": n, "stream": "stdout", "text": text, "at": f"2026-11-17T08:00:{n:02d}Z"}
        for n, text in enumerate(lines, start=1)
    ], KEY, received_at=datetime(2026, 11, 17, 8))
    run = {"status": status, "started_at": "2026-11-17T08:00:00",
           "finished_at": None if status == "running" else "2026-11-17T08:01:30"}
    section = {"report_key": KEY, "mode": "live" if publishes else "dry_run", "publishes": publishes}
    return html_reports.summary(section, json.dumps(state), run, dropped=dropped)


def _texts(block):
    return " ".join(item["text"] for item in block["notices"])


def test_stage_events_never_turn_a_failed_or_stopped_run_into_a_success():
    every_stage_ok = [_event("Build", "started"), _event("Build", "succeeded", 1),
                      _event("Publish", "started"), _event("Publish", "succeeded", 1)]
    failed = _summary("failed", every_stage_ok)
    assert "exited with an error. The run is not a success." in _texts(failed)
    assert "Pushed to GitHub" not in _texts(failed)
    stopped = _summary("cancelled", [_event("Build", "started"), _event("Build", "succeeded", 1),
                                     _event("Publish", "started")])
    assert [stage["status"] for stage in stopped["stages"]] == ["succeeded", "incomplete"]
    assert stopped["stages"][1]["finished_at"] == "2026-11-17T08:01:30"
    assert stopped["incomplete_stages"] == ["Publish"]
    assert html_reports.history_line(stopped) == "Live · publishes · 1 of 2 stage(s) succeeded · stopped during Publish"
    assert stopped["total_seconds"] == 90


def test_a_success_with_a_doubtful_stage_record_says_so():
    failed_stage = _summary("succeeded", [_event("Validate", "started"), _event("Validate", "failed", 2)])
    assert "reported a failed stage but exited with code 0" in _texts(failed_stage)
    unfinished = _summary("succeeded", [_event("Validate", "started")])
    assert "1 stage(s) never reported completion" in _texts(unfinished)
    silent = _summary("succeeded", ["no events at all"], dropped=3)
    assert "reported no stages" in _texts(silent)
    assert "3 console line(s) were dropped" in _texts(silent)
    assert "GitHub Pages may still be deploying" in _texts(silent)
    assert "Dry run: nothing was published." in _texts(_summary("succeeded", [], publishes=False))
    malformed = _summary("failed", ["METRONOME_STAGE {"])
    assert "1 METRONOME_STAGE line(s) could not be read" in _texts(malformed)
    assert html_reports.summary(None, None, {"status": "succeeded"}) is None


def test_history_shows_the_stage_summary_for_refreshes_only(flow_db, tmp_path, queue):
    _configure(tmp_path)
    saved = flows.create_flow(_flow(), _request())
    run_id = flows.queue_flow_run(saved["id"], actor="Analyst")["id"]
    _running(run_id)
    _post(run_id, [(1, "stdout", _event("Extract", "started")), (9, "stdout", _event("Extract", "succeeded", 8)),
                   (10, "stdout", _event("Validate", "started")), (12, "stdout", _event("Validate", "failed", 2))])
    _finish(run_id, "failed", error="refresh.py exited with code 1")
    script = tmp_path / "plain.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    plain = flows.create_flow(flows.FlowWrite(name="Plain", source_type="python", python_scripts=[str(script)],
                                              python_run_mode="run"), _request())
    flows.queue_flow_run(plain["id"], actor="Analyst")
    history = {run["flow_id"]: run for run in flows.list_runs(flow_id=None, status=None, before_id=None, limit=10)}
    refresh = history[saved["id"]]
    assert "stages_json" not in refresh
    assert refresh["html_report"]["history"] == "Live · publishes · 1 of 2 stage(s) succeeded · failed at Validate"
    assert refresh["html_report"]["total_seconds"] == 120
    assert history[plain["id"]]["html_report"] is None
    assert flows.list_runs(flow_id=saved["id"], status="failed", before_id=None, limit=10)[0]["id"] == run_id


# --- Reports, lineage and the Power BI sync -------------------------------------------

def _report(name):
    return next(item for item in reports.list_reports(include_archived=True) if item.name == name)


def test_reports_lists_each_html_report_with_its_refresh_health(flow_db, tmp_path, queue):
    _people()
    _configure(tmp_path)
    saved = flows.create_flow(_flow(owner_person_id=1), _request())
    report = _report("Trade-in value")
    assert (report.kind, report.status, report.owner, report.frequency) == ("html", "unknown", "Ana Costa", "Daily 07:30 Dubai")
    assert report.site_url == "https://pages.example.com/team/smp-trade-in-value-html/"
    assert report.refresh_flow_name == "Trade-in value" and report.last_published is None

    dry = flows.queue_run(saved["id"], _request(), dry_run=True)["id"]
    _running(dry)
    _finish(dry, "succeeded", finished="2026-11-17T08:01:00")
    report = _report("Trade-in value")
    assert report.status == "healthy" and report.last_refresh_status == "succeeded"
    assert report.last_published is None  # a dry run publishes nothing

    live = flows.queue_flow_run(saved["id"], actor="Analyst")["id"]
    _running(live)
    _finish(live, "succeeded", finished="2026-11-17T09:00:00")
    assert _report("Trade-in value").last_published == "2026-11-17T09:00:00"

    failed = flows.queue_flow_run(saved["id"], actor="Analyst")["id"]
    _running(failed)
    _finish(failed, "failed", finished="2026-11-17T10:00:00")
    report = _report("Trade-in value")
    assert report.status == "degraded" and report.last_refresh_at == "2026-11-17T10:00:00"
    assert report.last_published == "2026-11-17T09:00:00"
    assert reports.get_report(report.id).kind == "html"

    with pytest.raises(flows.HTTPException) as refused:
        reports.refresh_report(report.id, _request())
    assert refused.value.status_code == 409
    diagram = lineage.get_lineage_diagram(report.id)
    assert diagram["report"]["kind"] == "html" and diagram["report"]["can_refresh"] is False
    assert diagram["refresh_flow"]["name"] == "Trade-in value" and diagram["refresh_flow"]["report_key"] == KEY
    assert diagram["refresh_flow"]["last_published_at"] == "2026-11-17T09:00:00"
    plan = pipelines.build_refresh_plan(report.id, "Analyst", probe_mvs=False)
    assert any("refreshed by its own Flow" in item for item in plan["blockers"])

    moved = flows.update_flow(saved["id"], _flow(owner_person_id=1, html_report={"report_key": "smp_stock_cover_html", "mode": "live"}),
                              _request())
    report = _report("Trade-in value")
    assert (report.status, report.last_refresh_at, report.last_published) == ("unknown", None, None)
    assert lineage.get_lineage_diagram(report.id)["refresh_flow"]["last_status"] is None
    flows.update_flow(moved["id"], _flow(owner_person_id=1), _request())
    assert _report("Trade-in value").last_published == "2026-11-17T09:00:00"

    renamed = flows.update_flow(saved["id"], _flow(owner_person_id=1, name="Trade-in value v2",
                                                   schedule_type="weekly", schedule_days=["monday"]), _request())
    report = _report("Trade-in value v2")
    assert report.frequency == "Weekly Mon 07:30 Dubai" and report.refresh_flow_id == renamed["id"]


def test_report_names_never_merge_and_a_deleted_flow_leaves_an_archived_report(flow_db, tmp_path):
    _configure(tmp_path)
    with database.get_db() as db:
        db.execute("INSERT INTO reports(name, tmdl_path) VALUES ('Sell-out Overview', 'x')")
    with pytest.raises(flows.HTTPException) as refused:
        flows.create_flow(_flow(name="Sell-out Overview"), _request())
    assert refused.value.status_code == 409 and "already has a report named" in refused.value.detail
    assert [item.kind for item in reports.list_reports(include_archived=True)] == ["powerbi"]
    assert flows.list_flows() == []

    saved = flows.create_flow(_flow(), _request())
    flows.delete_flow(saved["id"], flows.FlowDeleteWrite(confirmation="Trade-in value"), _request())
    report = _report("Trade-in value")
    assert report.archived is True and report.refresh_flow_id is None
    assert report.site_url  # links and history stay for governance
    # Deleting a Flow keeps its folder on disk; once someone removes it, a new
    # Flow of the same name takes the archived report back instead of a twin.
    shutil.rmtree(saved["flow_folder"])
    again = flows.create_flow(_flow(), _request())
    report = _report("Trade-in value")
    assert report.id is not None and report.archived is False and report.refresh_flow_id == again["id"]
    assert len([item for item in reports.list_reports(include_archived=True) if item.kind == "html"]) == 1


def test_power_bi_sync_and_usage_leave_html_reports_alone(flow_db, tmp_path):
    _configure(tmp_path)
    flows.create_flow(_flow(name="Alpha"), _request())
    with database.get_db() as db:
        db.execute("INSERT INTO reports(name, tmdl_path) VALUES ('Beta', 'x')")
        assert "alpha" not in usage._db_report_map(db)
    control.clear_pbi_callback_fence()
    try:
        result = pbi_sync.import_pbi_data({
            "workspace": "Governed Workspace", "synced_at": datetime.now().isoformat(),
            "reports": [{"report_name": "Beta", "dataset_id": "d-1", "web_url": "https://app.powerbi.test/beta",
                         "schedule": {"enabled": False, "days": [], "times": [], "timezone": ""}, "last_refresh": None},
                        {"report_name": "Alpha", "dataset_id": "d-2", "web_url": "https://app.powerbi.test/alpha",
                         "schedule": {"enabled": False, "days": [], "times": [], "timezone": ""}, "last_refresh": None}],
        })
    finally:
        control.clear_pbi_callback_fence()
    assert result["status"] == "completed"
    alpha = _report("Alpha")
    assert alpha.archived is False and alpha.kind == "html" and alpha.powerbi_url is None
    assert alpha.pbi_dataset_id is None
