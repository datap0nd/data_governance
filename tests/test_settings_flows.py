"""Flow, SQL step, catalog and email limits on the Settings page.

For each setting: a changed value reaches the code that uses it. Limits a
worker obeys are frozen into the job when the server builds it; this file
covers the server side of that, and ``test_settings_flows_worker.py`` the
worker side.

Everything here is synthetic: a temporary SQLite database, fictional portals
and people, and stand-ins for workers, Outlook and Power BI.
"""
import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from app import (
    database, flow_capacity, flow_email_delivery, flow_job_limits, flow_portable, flow_python,
    flow_recordings, flow_retention, flow_sql, flow_standalone, flow_worker, pbi_visual_export,
    settings_registry as registry,
)
from app.routers import flows, recurrences
from test_audit_flows_api import quiet_worker, sql_runs  # noqa: F401
from test_flows import (  # noqa: F401
    _asap_site, _flow, _legacy_destination, _mark_discovered, _request, _seed_catalog, flow_db,
)

# Saving a Flow here is set-up, not the subject: see skip_flow_files in conftest.py.
pytestmark = pytest.mark.usefixtures("skip_flow_files")

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "app" / "settings_catalog_flows.py"
GROUPS = {"Run limits", "Downloads", "SQL step", "Scripts and catalogs",
          "Attachments and recipients", "Power BI visual exports"}
# key -> (default, minimum, maximum, when a change applies)
EXPECTED = {
    "flows.run_silent_minutes": (10, 5, 120, "now"),
    "flows.worker_online_seconds": (90, 60, 600, "now"),
    "flows.run_folders_kept": (3, 1, 50, "now"),
    "flows.recording_start_seconds": (120, 30, 900, "now"),
    "flows.failure_screenshots_kept": (20, 1, 100, "new_runs"),
    "flows.recording_test_folders_kept": (3, 1, 20, "new_runs"),
    "flows.download_start_minutes": (15, 1, 240, "new_runs"),
    "flows.download_handover_minutes": (60, 5, 480, "new_runs"),
    "flows.export_attempts": (3, 1, 10, "new_runs"),
    "flows.gscm_export_attempts": (2, 1, 10, "new_runs"),
    "flows.recorded_action_seconds": (120, 10, 1800, "new_runs"),
    "flows.network_replay": (True, None, None, "new_runs"),
    "flows.replay_max_age_days": (14, 1, 90, "new_runs"),
    "flows.replay_request_minutes": (5, 1, 60, "new_runs"),
    "flows.sql_connect_seconds": (60, 5, 600, "new_runs"),
    "flows.sql_lock_minutes": (10, 1, 240, "new_runs"),
    "flows.sql_statement_minutes": (30, 1, 1440, "new_runs"),
    "flows.view_lock_minutes": (10, 1, 240, "new_runs"),
    "flows.view_refresh_minutes": (60, 1, 1440, "new_runs"),
    "flows.script_default_minutes": (60, 1, 1440, "new_runs"),
    "flows.sql_catalog_time": ("05:30", None, None, "now"),
    "flows.catalog_scan_minutes": (90, 10, 720, "new_runs"),
    "flows.scan_overlay_minutes": (30, 1, 120, "new_runs"),
    "email.flow_attachment_mb": (20, 1, 150, "now"),
    "email.flow_max_recipients": (20, 1, 100, "now"),
    "email.visual_export_timeout_seconds": (120, 10, 1800, "now"),
    "email.visual_export_max_rows": (30000, 1, 30000, "now"),
    "email.visual_export_cut_off": ("send", None, None, "now"),
}
ENVIRONMENT = ("DG_FLOW_REPLAY", "DG_PBI_VISUAL_EXPORT_TIMEOUT_SECONDS", "DG_PBI_VISUAL_EXPORT_MAX_ROWS")
BUILT_IN_RUN_LIMITS = {
    "download_start_seconds": 900, "download_event_seconds": 3600, "export_attempts": 3,
    "gscm_export_attempts": 2, "recorded_action_seconds": 120, "network_replay": True,
    "replay_max_age_days": 14, "replay_request_seconds": 300, "script_timeout_seconds": 3600,
    "sql_connect_seconds": 60, "sql_lock_seconds": 600, "sql_statement_seconds": 1800,
    "view_lock_seconds": 600, "view_refresh_seconds": 3600, "failure_screenshots_kept": 20,
    "recording_test_folders_kept": 3,
}


@pytest.fixture
def app_db(flow_db, monkeypatch):  # noqa: F811
    """The temporary app database, with nothing configured through the environment."""
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(flows, "launch_local_worker", lambda *a, **k: {"status": "launched"})
    return flow_db


def _saved_flow(**overrides):
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    return flows.create_flow(_flow(site["id"], report["id"], **overrides), _request())


def _build(flow_id):
    with database.get_db() as db:
        return flows._build_job(db, flow_id)


def _stored_job(run_id):
    with database.get_db() as db:
        return json.loads(db.execute("SELECT job_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()[0])


# --- Declarations --------------------------------------------------------------

def test_the_catalog_declares_exactly_these_settings_with_ranges_units_and_plain_help():
    declared = set(re.findall(r'_(?:server|frozen)\("([a-z_.]+)"', CATALOG.read_text(encoding="utf-8")))
    assert declared == set(EXPECTED)
    for key, (default, minimum, maximum, applies) in EXPECTED.items():
        item = registry.definition(key)
        assert (item.default, item.minimum, item.maximum, item.applies) == (default, minimum, maximum, applies), key
        assert item.section == ("email" if key.startswith("email.") else "flows"), key
        assert item.group in GROUPS, key
        assert item.label and not item.label.endswith("."), key
        # One plain sentence, or two when the second says who keeps the old value.
        assert item.help.endswith(".") and len(item.help) < 260, key
        assert not re.search(r"DG_|_seconds|timeout|kwarg|job\[|frozen|\bworker process\b", item.help + item.label), key
        if item.kind in ("int", "float"):
            assert item.unit and minimum < maximum, key
        if applies == "new_runs":
            assert "already queued or in progress keep the old value." in item.help, key
        assert registry.validate(item, item.default) == item.default, key


def test_every_setting_has_code_that_reads_it_and_no_worker_module_reads_settings():
    sources = {path.relative_to(ROOT / "app").as_posix(): path.read_text(encoding="utf-8")
               for path in (ROOT / "app").rglob("*.py") if not path.name.startswith("settings_catalog")}
    frozen = {setting: name for table in (flow_job_limits.RUN, flow_job_limits.SCAN)
              for name, (setting, _scale) in table.items()}
    worker_side = ("flow_worker.py", "flow_sql.py", "flow_view_refresh.py", "flow_replay.py",
                   "flow_recording_runtime.py", "flow_recorder_worker.py", "flow_parallel_worker.py",
                   "flow_recording_gscm_bookmark.py", "flow_gscm.py")
    for key, (_default, _minimum, _maximum, applies) in EXPECTED.items():
        quoted = re.compile(r"['\"]" + re.escape(key) + r"['\"]")
        assert any(quoted.search(text) for text in sources.values()), f"nothing reads {key}"
        # A limit copied into jobs applies to new runs, and nothing else does.
        assert (key in frozen) == (applies == "new_runs"), key
    for setting, name in frozen.items():
        if name == "scan_budget_minutes":
            continue  # The server moves it to the scan's long-standing "discovery" section.
        quoted = re.compile(r"['\"]" + re.escape(name) + r"['\"]")
        assert any(quoted.search(sources[module]) for module in worker_side), f"no worker reads {name} ({setting})"
    # Workers get every value from the job: none of their modules opens the registry.
    for module in worker_side:
        assert "settings_registry" not in sources[module], module


def test_with_nothing_saved_a_job_carries_the_built_in_limits_and_changes_no_call(app_db):
    assert flow_job_limits.frozen() == BUILT_IN_RUN_LIMITS
    assert flow_job_limits.frozen(flow_job_limits.SCAN) == {
        "scan_budget_minutes": 90, "scan_overlay_seconds": 1800, "failure_screenshots_kept": 20}
    job = {"limits": flow_job_limits.frozen()}
    # ``given`` hands a function only a limit that differs from its built-in one.
    for name, built_in in BUILT_IN_RUN_LIMITS.items():
        if name not in flow_job_limits.SWITCHES:
            assert flow_job_limits.given(job, argument=(name, built_in)) == {}, name
    assert flow_sql.limits_argument(job) == {} and flow_sql.connect_limit(job) == {}


# --- Limits frozen into jobs ----------------------------------------------------

CHANGED = {
    "flows.download_start_minutes": 20, "flows.download_handover_minutes": 90, "flows.export_attempts": 5,
    "flows.gscm_export_attempts": 4, "flows.recorded_action_seconds": 300, "flows.network_replay": False,
    "flows.replay_max_age_days": 7, "flows.replay_request_minutes": 2, "flows.script_default_minutes": 45,
    "flows.sql_connect_seconds": 30, "flows.sql_lock_minutes": 3, "flows.sql_statement_minutes": 120,
    "flows.view_lock_minutes": 2, "flows.view_refresh_minutes": 240, "flows.failure_screenshots_kept": 5,
    "flows.recording_test_folders_kept": 8,
}
CHANGED_LIMITS = {
    "download_start_seconds": 1200, "download_event_seconds": 5400, "export_attempts": 5,
    "gscm_export_attempts": 4, "recorded_action_seconds": 300, "network_replay": False,
    "replay_max_age_days": 7, "replay_request_seconds": 120, "script_timeout_seconds": 2700,
    "sql_connect_seconds": 30, "sql_lock_seconds": 180, "sql_statement_seconds": 7200,
    "view_lock_seconds": 120, "view_refresh_seconds": 14400, "failure_screenshots_kept": 5,
    "recording_test_folders_kept": 8,
}


def test_a_newly_built_job_carries_the_limits_saved_in_settings(app_db):
    saved = _saved_flow()
    assert _build(saved["id"])["limits"] == BUILT_IN_RUN_LIMITS

    registry.save(CHANGED)

    job = _build(saved["id"])
    assert job["limits"] == CHANGED_LIMITS
    # Whole numbers in the worker's units survive the trip through the stored JSON.
    assert json.loads(json.dumps(job))["limits"] == CHANGED_LIMITS
    queued = flows.queue_run(saved["id"], _request())
    assert _stored_job(queued["id"])["limits"] == CHANGED_LIMITS


def test_a_setting_changed_after_a_job_was_built_does_not_alter_that_job(app_db):
    saved = _saved_flow()
    queued = flows.queue_run(saved["id"], _request())
    before = _stored_job(queued["id"])

    registry.save(CHANGED)

    assert _stored_job(queued["id"]) == before and before["limits"] == BUILT_IN_RUN_LIMITS
    flows.register_worker(flows.WorkerRegister(worker_id="limits-worker", display_name="Limits worker",
                                               capabilities={"shared_flow_artifacts": True}))
    # The worker is handed the job as it was queued, not today's settings.
    assert flows.claim_run("limits-worker")["run"]["job"]["limits"] == BUILT_IN_RUN_LIMITS
    assert _build(saved["id"])["limits"] == CHANGED_LIMITS


def test_a_sql_retry_keeps_the_limits_of_the_run_it_retries(sql_runs, monkeypatch):  # noqa: F811
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    registry.save({"flows.sql_statement_minutes": 45})
    saved = sql_runs.flow()
    source = sql_runs.finish(saved["id"], "transformation")
    assert _stored_job(source)["limits"]["sql_statement_seconds"] == 45 * 60

    registry.save({"flows.sql_statement_minutes": 90, "flows.sql_lock_minutes": 1})
    retried = flows.retry_run_sql(source, _request())

    job = _stored_job(retried["id"])
    assert job["job_type"] == "sql_retry"
    assert (job["limits"]["sql_statement_seconds"], job["limits"]["sql_lock_seconds"]) == (45 * 60, 600)


def test_a_retry_of_a_run_queued_before_limits_existed_still_carries_none(sql_runs):  # noqa: F811
    saved = sql_runs.flow()
    source = sql_runs.finish(saved["id"], "transformation")
    with database.get_db() as db:
        legacy = _stored_job(source)
        legacy.pop("limits")
        db.execute("UPDATE flow_runs SET job_json=? WHERE id=?", (json.dumps(legacy), source))

    retried = flows.retry_run_sql(source, _request())

    assert "limits" not in _stored_job(retried["id"])


def test_a_resume_keeps_the_limits_of_the_run_it_resumes(app_db):
    saved = _saved_flow()
    registry.save({"flows.download_start_minutes": 25})
    source = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(worker_id="limits-worker", display_name="Limits worker",
                                               capabilities={"shared_flow_artifacts": True}))
    flows.claim_run("limits-worker")
    flows.update_run("limits-worker", source["id"], flows.WorkerProgress(
        status="failed", error="synthetic failure", artifacts=[
            {"file_path": "/reports/a.csv", "filename": "a.csv", "status": "saved",
             "export_view": None, "period_key": "2026-W30"}]))

    registry.save({"flows.download_start_minutes": 40})
    with database.get_db() as db:
        resumed = flows.inspect_resume_eligibility(db, source["id"])
        assert resumed["status"] == "eligible", resumed
        assert resumed["_job"]["limits"]["download_start_seconds"] == 25 * 60
        # A run from before limits existed resumes with none, exactly as it ran.
        legacy = _stored_job(source["id"])
        legacy.pop("limits")
        db.execute("UPDATE flow_runs SET job_json=? WHERE id=?", (json.dumps(legacy), source["id"]))
        assert "limits" not in flows.inspect_resume_eligibility(db, source["id"])["_job"]
    assert _build(saved["id"])["limits"]["download_start_seconds"] == 40 * 60


def test_limits_change_no_recording_or_script_hash(app_db):
    saved = _saved_flow()
    before = _build(saved["id"])
    registry.save(CHANGED)
    after = _build(saved["id"])
    assert before["limits"] != after["limits"]

    # A recording's test evidence and a resume compare this hash.
    assert flow_recordings.config_hash(before) == flow_recordings.config_hash(after)
    legacy = copy.deepcopy(before)
    legacy.pop("limits")
    assert flow_recordings.config_hash(legacy) == flow_recordings.config_hash(after)
    # Generated scripts keep the built-in limits and do not go stale on a save.
    assert "limits" not in flow_standalone.freeze(after)
    assert flow_standalone.config_hash(flow_standalone.freeze(before)) == flow_standalone.config_hash(
        flow_standalone.freeze(after))
    assert flow_portable.configuration_hash(before) == flow_portable.configuration_hash(after)


def _queued_scan_job(site_id):
    with database.get_db() as db:
        scan_id, _mode = flows._queue_scan(
            db, dict(db.execute("SELECT * FROM flow_sites WHERE id=?", (site_id,)).fetchone()), "manual", "Analyst")
    return scan_id


def _scan_job(scan_id):
    with database.get_db() as db:
        return json.loads(db.execute("SELECT job_json FROM flow_catalog_scans WHERE id=?", (scan_id,)).fetchone()[0])


def test_a_catalog_scan_freezes_its_time_budget_and_loading_limit(app_db):
    site_id = flows.create_site(_asap_site(), _request())["id"]
    first = _scan_job(_queued_scan_job(site_id))
    assert first["discovery"]["max_duration_minutes"] == 90
    assert first["limits"] == {"scan_overlay_seconds": 1800, "failure_screenshots_kept": 20}

    registry.save({"flows.catalog_scan_minutes": 45, "flows.scan_overlay_minutes": 5,
                   "flows.failure_screenshots_kept": 3})

    # The scan already queued for this website is returned unchanged...
    assert _scan_job(_queued_scan_job(site_id)) == first
    with database.get_db() as db:
        db.execute("UPDATE flow_catalog_scans SET status='succeeded'")
    # ...and the next one carries the new values.
    later = _scan_job(_queued_scan_job(site_id))
    assert later["discovery"]["max_duration_minutes"] == 45
    assert later["limits"] == {"scan_overlay_seconds": 300, "failure_screenshots_kept": 3}


# --- Run limits read by the web service ----------------------------------------

def _claimed_run(worker="limits-worker"):
    saved = _saved_flow()
    queued = flows.queue_run(saved["id"], _request())
    flows.register_worker(flows.WorkerRegister(worker_id=worker, display_name="Limits worker",
                                               capabilities={"shared_flow_artifacts": True}))
    flows.claim_run(worker)
    return queued["id"]


def test_a_silent_run_is_failed_after_the_saved_number_of_minutes(app_db, monkeypatch):
    monkeypatch.setattr(flows, "notify_flow_owner_of_failure", lambda run_id: None)
    run_id = _claimed_run()
    monkeypatch.setattr(flows, "_now", lambda: datetime.fromisoformat("2026-08-13T10:07:00"))
    with database.get_db() as db:
        db.execute("UPDATE flow_workers SET last_seen_at='2026-08-13T10:00:00'")
        db.execute("UPDATE flow_runs SET heartbeat_at='2026-08-13T10:00:00' WHERE id=?", (run_id,))

    # Seven minutes of silence is inside the ten-minute default.
    assert flows.fail_stale_runs() == {"failed_run_ids": [], "count": 0}
    registry.save({"flows.run_silent_minutes": 5})

    assert flows.fail_stale_runs() == {"failed_run_ids": [run_id], "count": 1}
    assert flows.get_run(run_id)["status"] == "failed"


def test_a_worker_counts_as_online_for_the_saved_time_everywhere(app_db, monkeypatch):
    launched = []
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode, **kwargs: launched.append(kwargs) or {"status": "launched"})
    identity = flow_capacity.worker_id(1)
    flows.register_worker(flows.WorkerRegister(worker_id=identity, display_name="Background 1",
                                               capabilities={"shared_flow_artifacts": True}))
    with database.get_db() as db:
        # One background slot, last seen two minutes ago: offline with the 90-second default.
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES (?, '1')", (flow_capacity.CAPACITY_KEY,))
        db.execute("UPDATE flow_workers SET last_seen_at=?",
                   (flows._iso(flows._now() - timedelta(seconds=120)),))

    def seen_online():
        with database.get_db() as db:
            slot = flow_capacity.state(db)["slots"][0]
        ensured = flows.ensure_local_worker()["slots"][0]["status"]
        return (slot["online"], flows.list_workers()[0]["status"] != "offline",
                flows.flow_activity()["workers"]["online"], ensured)

    assert seen_online() == (False, False, 0, "launched") and len(launched) == 1
    registry.save({"flows.worker_online_seconds": 300})
    assert seen_online() == (True, True, 1, "online") and len(launched) == 1


def _register_run_folder(flow_id, target):
    queued = flows.queue_run(flow_id, _request())
    flows.claim_run("retention-worker")
    registration = flows.register_run_folder("retention-worker", queued["id"], flows.FolderRegister(
        run_folder=str(Path(target) / flow_retention.run_folder_name(queued["id"]))))
    flows.update_run("retention-worker", queued["id"], flows.WorkerProgress(status="succeeded"))
    return queued["id"], registration


@pytest.mark.parametrize("saved_number, kept", [(None, 3), (5, 5), (1, 1)])
def test_the_saved_number_of_run_folders_is_kept(app_db, saved_number, kept):
    if saved_number is not None:
        registry.save({"flows.run_folders_kept": saved_number})
    target = "/reports/downloads"
    saved = _legacy_destination(_saved_flow(target_folder=target), target)
    flows.register_worker(flows.WorkerRegister(worker_id="retention-worker", display_name="Retention worker",
                                               capabilities={"shared_flow_artifacts": True}))

    runs = [_register_run_folder(saved["id"], target) for _ in range(kept + 1)]

    # Nothing is removed while at most ``kept`` folders exist, this run's included;
    # the next run is told to remove the oldest one.
    assert [[op["source_run_id"] for op in registration["ops"]] for _run, registration in runs] == [
        *([[]] * kept), [runs[0][0]]]
    assert all(registration["keep"] == kept for _run, registration in runs)


def test_a_changed_number_of_run_folders_applies_to_the_next_run(app_db):
    target = "/reports/downloads"
    saved = _legacy_destination(_saved_flow(target_folder=target), target)
    flows.register_worker(flows.WorkerRegister(worker_id="retention-worker", display_name="Retention worker",
                                               capabilities={"shared_flow_artifacts": True}))
    runs = [_register_run_folder(saved["id"], target) for _ in range(3)]
    assert all(registration["ops"] == [] for _run, registration in runs)

    registry.save({"flows.run_folders_kept": 2})

    _fourth, registration = _register_run_folder(saved["id"], target)
    # This run and the third stay; the two older folders are removed.
    assert sorted(op["source_run_id"] for op in registration["ops"]) == [runs[0][0], runs[1][0]]


def test_the_worker_names_the_number_of_folders_the_server_keeps(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    messages = []
    job = {"flow": {"id": 4}, "downloads": {"target_folder": str(target), "output_mode": "run_folders"}}
    op = {"op_id": 9, "source_run_id": 1, "original_path": str(target / "missing"),
          "tombstone_path": str(target / ".#1_01-01-2026.op9.deleting")}

    def report(_status, detail):
        messages.append(detail["message"])

    flow_worker._prepare_run_folder(job, tmp_path / "profile", run_id=21, report_progress=report,
                                    register_folder=lambda _folder: {"ops": [op], "keep": 7})
    assert "keeping the newest 7" in messages[-1]
    # A server from before the setting says nothing, and kept three.
    flow_worker._prepare_run_folder(job, tmp_path / "profile", run_id=22, report_progress=report,
                                    register_folder=lambda _folder: {"ops": [op]})
    assert "keeping the newest 3" in messages[-1]


def _queued_recording_test(monkeypatch):
    from app import flow_local_runner
    from app.routers import flow_recordings as routes
    from test_flow_recordings import definition, draft_job

    monkeypatch.setattr(flow_local_runner, "launch_local_worker", lambda mode, *, slot: {"status": "starting"})
    saved, _job = draft_job()
    revision = routes.save_revision(saved["id"], routes.RevisionWrite(definition=definition()))["revision_id"]
    scan_id = routes.validate_revision(saved["id"], revision, _request())["scan_id"]
    return saved, scan_id


def _recording_status(scan_id, waited_seconds):
    started = (datetime.now(timezone.utc) - timedelta(seconds=waited_seconds)).isoformat()
    with database.get_db() as db:
        db.execute("UPDATE flow_catalog_scans SET created_at=?, progress_json=? WHERE id=?",
                   (started, json.dumps({"stage": "starting_worker", "startup_started_at": started}), scan_id))
        flow_recordings.reap(db)
        return db.execute("SELECT status FROM flow_catalog_scans WHERE id=?", (scan_id,)).fetchone()[0]


def test_a_recording_browser_gets_the_saved_time_to_open(app_db, monkeypatch):
    registry.save({"flows.recording_start_seconds": 600})
    _saved, scan_id = _queued_recording_test(monkeypatch)
    # Five minutes is past the two-minute default but inside the saved ten.
    assert _recording_status(scan_id, 300) == "queued"
    registry.save({"flows.recording_start_seconds": None})
    assert _recording_status(scan_id, 300) == "failed"


def test_a_recording_browser_that_has_not_opened_fails_after_the_default_two_minutes(app_db, monkeypatch):
    _saved, scan_id = _queued_recording_test(monkeypatch)
    assert _recording_status(scan_id, 100) == "queued"
    assert _recording_status(scan_id, 130) == "failed"


# --- Scripts and catalogs --------------------------------------------------------

def test_a_script_flow_without_its_own_limit_uses_the_saved_default(app_db):
    flow = {"source_type": "python", "python_scripts_json": '["/s/a.py"]'}
    assert flow_python.job_section(flow)["timeout_seconds"] == 3600
    assert flow_python.job_section(flow, default_timeout_minutes=45)["timeout_seconds"] == 45 * 60
    assert flow_python.job_section({**flow, "python_timeout_minutes": 10},
                                   default_timeout_minutes=45)["timeout_seconds"] == 600

    saved = _saved_flow()
    with database.get_db() as db:
        db.execute("UPDATE flows SET python_timeout_minutes=NULL WHERE id=?", (saved["id"],))
    assert _build(saved["id"])["python_source"]["timeout_seconds"] == 3600
    registry.save({"flows.script_default_minutes": 45})
    job = _build(saved["id"])
    assert job["python_source"]["timeout_seconds"] == 45 * 60 == job["limits"]["script_timeout_seconds"]
    # A Flow that names its own limit keeps it.
    with database.get_db() as db:
        db.execute("UPDATE flows SET python_timeout_minutes=10 WHERE id=?", (saved["id"],))
    assert _build(saved["id"])["python_source"]["timeout_seconds"] == 600


def _scheduler_with_catalog_job():
    from apscheduler.schedulers.background import BackgroundScheduler
    from app.freshness import host_timezone

    scheduler = BackgroundScheduler(timezone=host_timezone())
    scheduler.add_job(lambda: None, "cron", id=flows.SQL_CATALOG_REFRESH_JOB, **flows.sql_catalog_refresh_time())
    return scheduler


def _cron(scheduler):
    trigger = scheduler.get_job(flows.SQL_CATALOG_REFRESH_JOB).trigger
    fields = {field.name: str(field) for field in trigger.fields}
    return fields["hour"], fields["minute"], str(trigger.timezone)


def test_the_daily_sql_table_scan_runs_at_the_saved_dubai_time(app_db, monkeypatch):
    assert flows.sql_catalog_refresh_time() == {"hour": 5, "minute": 30}
    scheduler = _scheduler_with_catalog_job()
    assert _cron(scheduler) == ("5", "30", "Asia/Dubai")
    # The running service's job is found through app.main, as at run time.
    service = ModuleType("app.main")
    service._scheduler = scheduler
    monkeypatch.setitem(sys.modules, "app.main", service)

    changed = registry.save({"flows.sql_catalog_time": "21:45"})

    assert [item["key"] for item in changed] == ["flows.sql_catalog_time"]
    assert flows.sql_catalog_refresh_time() == {"hour": 21, "minute": 45}
    assert _cron(scheduler) == ("21", "45", "Asia/Dubai")
    # The start-up code asks the same function, so a restart keeps the saved time.
    assert _cron(_scheduler_with_catalog_job()) == ("21", "45", "Asia/Dubai")


def test_saving_the_scan_time_succeeds_when_the_job_cannot_be_moved(app_db, monkeypatch, caplog):
    class Broken:
        def get_job(self, _job_id):
            return object()

        def reschedule_job(self, *_args, **_kwargs):
            raise RuntimeError("scheduler is shutting down")

    service = ModuleType("app.main")
    service._scheduler = Broken()
    monkeypatch.setitem(sys.modules, "app.main", service)

    with caplog.at_level("ERROR", logger="settings"):
        changed = registry.save({"flows.sql_catalog_time": "06:15"})

    assert changed and registry.get("flows.sql_catalog_time") == "06:15"
    assert "keeps its old time until Metronome restarts" in caplog.text
    # A process that schedules nothing (a script, a test) has nothing to move.
    monkeypatch.setitem(sys.modules, "app.main", ModuleType("app.main"))
    assert flows.reschedule_sql_catalog_refresh() is False


# --- Flow emails ---------------------------------------------------------------

def test_attachments_are_left_out_above_the_saved_size(app_db):
    megabyte = 1024 * 1024
    files = [{"file_size": 12 * megabyte}, {"file_size": 7 * megabyte}]
    assert flow_email_delivery.attachment_plan(files) == (True, 19 * megabyte, None)
    assert flow_email_delivery.attachment_plan(files, extra_bytes=2 * megabyte)[2] == (
        "Attachments skipped: 21.0 MB exceeds the 20 MB limit; the email names the file location.")

    registry.save({"email.flow_attachment_mb": 10})
    attach, total, warning = flow_email_delivery.attachment_plan(files)
    assert (attach, total) == (False, 19 * megabyte)
    assert warning == "Attachments skipped: 19.0 MB exceeds the 10 MB limit; the email names the file location."
    registry.save({"email.flow_attachment_mb": 30})
    assert flow_email_delivery.attachment_plan(files, extra_bytes=2 * megabyte) == (True, 21 * megabyte, None)


def _email_step(count):
    return {"enabled": True, "recipients": [f"person{index}@example.test" for index in range(count)]}


def test_a_flow_cannot_be_saved_with_more_recipients_than_the_saved_number(app_db):
    assert len(flow_email_delivery.normalize_config(_email_step(20))["recipients"]) == 20
    with pytest.raises(ValueError, match="Choose at most 20 recipients"):
        flow_email_delivery.normalize_config(_email_step(21))

    registry.save({"email.flow_max_recipients": 30})
    assert len(_flow(1, 1, email_delivery=_email_step(30)).email_delivery["recipients"]) == 30
    with pytest.raises(ValueError, match="Choose at most 30 recipients"):
        _flow(1, 1, email_delivery=_email_step(31))


def test_lowering_the_recipient_limit_does_not_switch_off_a_saved_flows_email(app_db):
    registry.save({"email.flow_max_recipients": 30})
    saved = _saved_flow(email_delivery=_email_step(25))
    registry.save({"email.flow_max_recipients": 5})

    # The Flow keeps sending to the addresses it was saved with...
    assert len(flows.get_flow(saved["id"])["email_delivery"]["recipients"]) == 25
    job = _build(saved["id"])
    assert job["email_delivery"]["enabled"] is True and len(job["email_delivery"]["recipients"]) == 25
    # ...and the limit is applied the next time someone saves an Email step.
    with pytest.raises(ValueError, match="Choose at most 5 recipients"):
        _flow(1, 1, email_delivery=_email_step(25))


# --- Power BI visual exports -----------------------------------------------------

def _visual_request(monkeypatch, **arguments):
    sent = {}
    monkeypatch.setattr(pbi_visual_export, "get_access_token", lambda: {"access_token": "synthetic"})
    monkeypatch.setattr(pbi_visual_export, "_run_browser_with_retry",
                        lambda **kwargs: sent.update(kwargs["config"]) or {"data": "Column\n"})
    pbi_visual_export._run_visual_action(report_id="r", embed_url="https://app.powerbi.com/x", page_name="p",
                                         action="export", visual_name="v", **arguments)
    return sent["timeoutMs"], sent["maxRows"]


def test_visual_exports_use_the_saved_time_and_row_limits(app_db, monkeypatch):
    assert _visual_request(monkeypatch) == (120_000, 30_000)
    assert pbi_visual_export.visual_export_runtime_status()["timeout_seconds"] == 120
    assert pbi_visual_export.visual_export_runtime_status()["max_rows"] == 30_000

    # An install configured through the environment keeps its values...
    monkeypatch.setenv("DG_PBI_VISUAL_EXPORT_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("DG_PBI_VISUAL_EXPORT_MAX_ROWS", "5000")
    assert _visual_request(monkeypatch) == (300_000, 5_000)
    assert recurrences.export_row_limit() == 5_000
    # ...until the Settings page says otherwise.
    registry.save({"email.visual_export_timeout_seconds": 240, "email.visual_export_max_rows": 12_000})
    assert _visual_request(monkeypatch) == (240_000, 12_000)
    assert pbi_visual_export.visual_export_runtime_status()["max_rows"] == 12_000
    # A caller's own row limit still wins, and never exceeds Power BI's ceiling.
    assert _visual_request(monkeypatch, max_rows=1) == (240_000, 1)


def test_the_export_timeout_reaches_the_page_that_loads_power_bi(app_db):
    class Page:
        def __init__(self):
            self.timeouts = []

        def set_default_timeout(self, value):
            self.timeouts.append(value)

        def add_script_tag(self, *, url):
            return None

        def evaluate(self, _expression):
            return True

    page = Page()
    pbi_visual_export._load_power_bi_client(page)
    assert page.timeouts == [20_000]  # the default 120 seconds, capped for the script load
    registry.save({"email.visual_export_timeout_seconds": 15})
    pbi_visual_export._load_power_bi_client(page)
    assert page.timeouts == [20_000, 15_000]


_ALERT_CSV = "Subsidiary,Value\nNorth,15\nSouth,20\nNorth,30\n"


def _run_alert(tmp_path, monkeypatch, *, rows):
    from app import settings
    from test_recurrences import _seed_recurrence

    monkeypatch.setattr(database, "DB_PATH", database.DB_PATH)
    recurrence_id = _seed_recurrence(tmp_path / "governance.db")
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "governance.db"))
    monkeypatch.setattr(recurrences, "get_db", database.get_db)
    monkeypatch.setattr(recurrences.pbi_fetch, "fetch_dataset_last_refresh", lambda workspace_id, dataset_id: {
        "status": "Completed", "start_time": "2026-07-16T07:55:00Z", "end_time": "2026-07-16T08:00:00Z", "error": None})
    asked = []
    monkeypatch.setattr(recurrences, "export_visual_data", lambda *args, **kwargs: asked.append(args[4]) or {
        "data": _ALERT_CSV, "visual": {"name": "visual456", "type": "tableEx", "title": "Exceptions"}})
    launched = []
    monkeypatch.setattr(recurrences, "_launch_outlook_payload",
                        lambda messages, mode: launched.append(messages) or len(messages))
    if rows is not None:
        registry.save({"email.visual_export_max_rows": rows})
    return recurrence_id, asked, launched


def test_a_cut_off_table_is_sent_with_a_notice_by_default(tmp_path, monkeypatch):
    recurrence_id, asked, launched = _run_alert(tmp_path, monkeypatch, rows=3)

    result = recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="manual")

    assert asked == [3] and result["status"] == "sent" and len(launched) == 1
    assert all("The table was cut off at the Power BI export limit of 3 rows." in message["html_body"]
               for message in launched[0])
    detail = recurrences.recurrence_runs(recurrence_id)[0]["detail"]
    assert detail["export_limit_reached"] is True and detail["export_row_limit"] == 3


def test_a_cut_off_table_is_not_sent_when_settings_say_to_hold_it(tmp_path, monkeypatch):
    recurrence_id, _asked, launched = _run_alert(tmp_path, monkeypatch, rows=3)
    notified = []
    monkeypatch.setattr(recurrences, "_notify_owner_of_failure",
                        lambda *args: notified.append(args[2]) or {"status": "sent"})
    registry.save({"email.visual_export_cut_off": "hold"})

    with pytest.raises(RuntimeError, match="cut off at the Power BI export limit of 3 rows.*No email was sent"):
        recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="manual")

    assert launched == []
    run = recurrences.recurrence_runs(recurrence_id)[0]
    assert run["status"] == "failed" and "No email was sent" in run["error"]
    # The existing failure path tells the alert's owner.
    assert len(notified) == 1 and "cut off" in notified[0]


def test_holding_cut_off_tables_does_not_stop_a_complete_one(tmp_path, monkeypatch):
    recurrence_id, asked, launched = _run_alert(tmp_path, monkeypatch, rows=None)
    registry.save({"email.visual_export_cut_off": "hold"})

    result = recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="manual")

    assert asked == [30_000] and result["status"] == "sent" and len(launched) == 1
    assert all("cut off" not in message["html_body"] for message in launched[0])
