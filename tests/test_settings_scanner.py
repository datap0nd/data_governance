"""Scanner, probe, Power BI and freshness limits on the Settings page.

Every setting is checked: a saved value is honoured by the code that does the
work. Fixtures are synthetic: folders and files under ``tmp_path``, a stand-in
PostgreSQL driver and stand-in Power BI answers. Nothing here reaches a real
portal, database or Power BI.

The daily refresh, update and backup settings, the listeners and the host-only
routes are in ``tests/test_settings_scanner_schedules.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import config, database, freshness, settings, settings_registry as registry
from app.checks import data_quality
from app.scanner import (
    jobs,
    limits,
    modules,
    notifications,
    pbi_fetch,
    pbi_sync,
    pbix_parser,
    pg_deps,
    prober,
    runner,
    walker,
)

ROOT = Path(__file__).resolve().parents[1]
DUBAI = ZoneInfo("Asia/Dubai")
ENVIRONMENT_NAMES = (
    "DG_SCANNER_STALE_AFTER_SECONDS", "DG_PG_SCAN_STATEMENT_TIMEOUT_SECONDS", "DG_PBI_USAGE_DAYS_BACK",
)


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """A fresh app database for the registry, with no environment overrides."""
    path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(settings, "DB_PATH", str(path))
    database.init_db()
    for name in ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    # The grace is held in memory; start each test from an unread value and
    # give the earlier one back afterwards.
    monkeypatch.setattr(freshness, "_grace", None)
    return path


# --- Declarations ----------------------------------------------------------------

def test_constants_read_from_the_environment_agree_with_the_plain_defaults(app_db):
    # These are read from the environment at import, so compare only when unset there.
    if "DG_PG_SCAN_STATEMENT_TIMEOUT_SECONDS" not in config._STARTING_ENVIRONMENT:
        assert config.PG_SCAN_STATEMENT_TIMEOUT_SECONDS == prober.STATEMENT_TIMEOUT_SECONDS
    if "DG_SCANNER_STALE_AFTER_SECONDS" not in config._STARTING_ENVIRONMENT:
        assert jobs.STALE_AFTER_SECONDS == jobs.DEFAULT_STALE_AFTER_SECONDS
    if "DG_PBI_USAGE_DAYS_BACK" not in config._STARTING_ENVIRONMENT:
        assert config.PBI_USAGE_DAYS_BACK == pbi_fetch.USAGE_DAYS_BACK
    # The usage script is only told the days when they differ from its own default.
    script = (ROOT / "tools" / "pbi_usage_sync.ps1").read_text(encoding="utf-8")
    assert f"[int]$DaysBack = {pbi_sync.PS1_USAGE_DEFAULT_DAYS_BACK}," in script
    assert pbi_sync.PS1_USAGE_DEFAULT_DAYS_BACK == pbi_fetch.USAGE_DAYS_BACK


def test_values_set_through_the_old_environment_variables_are_kept(app_db, monkeypatch):
    monkeypatch.setenv("DG_SCANNER_STALE_AFTER_SECONDS", "120")
    monkeypatch.setenv("DG_PG_SCAN_STATEMENT_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("DG_PBI_USAGE_DAYS_BACK", "14")

    assert jobs.stale_after_seconds() == 120
    assert prober.pg_session_limits()[1] == "-c statement_timeout=300000 -c lock_timeout=30000"
    assert pbi_fetch.usage_days_back() == 14
    # A value saved on the page then wins over the variable, the default included.
    registry.save({
        "pbi.usage_days": 7, "scan.stalled_after_seconds": 600, "probe.statement_timeout_seconds": 180,
    })
    assert pbi_fetch.usage_days_back() == 7
    assert jobs.stale_after_seconds() == 600
    assert prober.pg_session_limits()[1] == "-c statement_timeout=180000 -c lock_timeout=30000"


def test_a_setting_that_cannot_be_read_never_stops_the_work(app_db, monkeypatch):
    def broken(_keys):
        raise RuntimeError("settings are unavailable")

    monkeypatch.setattr(registry, "snapshot", broken)

    assert limits.value("scan.folder_depth", 4) == 4
    assert walker.discovery_limits() == walker.DiscoveryLimits(depth=4, skipped=frozenset())
    assert prober.pg_session_limits() == (10, "-c statement_timeout=180000 -c lock_timeout=30000")


# --- What a scan reads -------------------------------------------------------------

def _pbix(path, megabytes=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * int(megabytes * 1024 * 1024) or b"pbix")
    return path


def _tmdl_report(root, name):
    tables = root / name / f"{name}.SemanticModel" / "Definition" / "Tables"
    tables.mkdir(parents=True)
    (tables / "Orders.tmdl").write_text("table Orders\n\tcolumn Id\n", encoding="utf-8")
    return root / name


def test_folder_depth_follows_the_setting(app_db, tmp_path):
    root = tmp_path / "reports"
    four = _pbix(root / "one" / "two" / "three" / "four" / "Four.pbix")
    five = _pbix(four.parent / "five" / "Five.pbix")
    three = _pbix(root / "one" / "two" / "three" / "Three.pbix")

    assert walker._discover_pbix_files(root) == [four, three]  # today's four levels

    registry.save({"scan.folder_depth": 5})
    assert walker._discover_pbix_files(root) == [five, four, three]
    registry.save({"scan.folder_depth": 3})
    assert walker._discover_pbix_files(root) == [three]
    with pytest.raises(registry.SettingsRejected):
        registry.save({"scan.folder_depth": 1})  # would hide every model export


def test_skipped_folder_names_are_left_out_wherever_they_sit(app_db, tmp_path):
    root = tmp_path / "reports"
    live = _pbix(root / "Live" / "Sales.pbix")
    _pbix(root / "Archive" / "Old Sales.pbix")
    _pbix(root / "Live" / "archive" / "Older Sales.pbix")
    _pbix(root / "Live" / "Archive 2024" / "Kept.pbix")  # a different name: not skipped
    _tmdl_report(root, "Orders")
    _tmdl_report(root / "Drafts", "Orders Draft")

    assert len(walker._discover_pbix_files(root)) == 4  # nothing is skipped by default
    assert len(walker._discover_tmdl_report_dirs(root)) == 2

    registry.save({"scan.skip_folders": "archive; Drafts"})

    assert walker._discover_pbix_files(root) == [root / "Live" / "Archive 2024" / "Kept.pbix", live]
    assert walker._discover_tmdl_report_dirs(root) == [root / "Orders"]
    assert [report.name for report in walker.walk_reports_root(root) if report.tables] == ["Orders"]
    diagnosis = walker.diagnose_reports_root(root)
    assert sorted(diagnosis["pbix_files"]) == [
        os.path.join("Live", "Archive 2024", "Kept.pbix"), os.path.join("Live", "Sales.pbix"),
    ]
    assert {"action": "Folder names skipped (Settings page)", "found": 2, "files": ["archive", "drafts"]} in (
        diagnosis["steps"]
    )


def test_a_skipped_name_is_a_folder_name_not_a_path(app_db):
    with pytest.raises(registry.SettingsRejected) as refused:
        registry.save({"scan.skip_folders": ["Archive", "Old/2023"]})
    assert refused.value.errors == {
        "scan.skip_folders": "Old/2023 is a path. Enter the folder's own name only.",
    }
    assert registry.get("scan.skip_folders") == []


def test_a_pbix_over_the_size_limit_is_left_unread_with_its_reason(app_db, tmp_path, monkeypatch):
    big = _pbix(tmp_path / "Big.pbix", megabytes=1.5)
    small = _pbix(tmp_path / "Small.pbix")
    opened = []
    monkeypatch.setattr(
        pbix_parser, "parse_pbix_file",
        lambda path: opened.append(path.name) or pbix_parser.PbixReport(name=path.stem, file_path=str(path)),
    )

    by_default = walker._walk_pbix([big, small])
    assert opened == ["Big.pbix", "Small.pbix"]  # no limit until one is set
    assert all(report.discovery["snapshot_complete"] for report in by_default)

    opened.clear()
    registry.save({"scan.max_pbix_mb": 1})
    unread, read = walker._walk_pbix([big, small])

    assert opened == ["Small.pbix"]
    assert read.discovery["snapshot_complete"] is True
    assert (unread.name, unread.tables, unread.discovery["snapshot_complete"]) == ("Big", [], False)
    assert unread.discovery["over_size_limit"] is True
    assert unread.discovery["issues"] == [
        "PBIX file is 1.5 MB, over the 1 MB limit on the Settings page; it was not opened",
    ]


def test_a_scan_finishes_and_says_why_a_large_pbix_was_not_read(app_db, tmp_path, monkeypatch):
    root = tmp_path / "reports"
    _tmdl_report(root, "Orders")
    _pbix(root / "Huge.pbix", megabytes=1.5)
    monkeypatch.setattr(runner, "_backup_db", lambda: None)
    monkeypatch.setitem(
        sys.modules, "pbixray",
        SimpleNamespace(PBIXRay=lambda _path: pytest.fail("the oversized file was opened")),
    )
    registry.save({"scan.max_pbix_mb": 1})

    result = runner.run_scan(str(root), run_followups=False, run_followup_probe=False)

    assert result["status"] in {"completed", "completed_with_warnings"}
    assert result["reports_preserved_incomplete"] == 1
    with database.get_db() as db:
        assert [row["name"] for row in db.execute("SELECT name FROM reports")] == ["Orders"]
        log = db.execute("SELECT log FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()["log"]
    assert "DISCOVERY WARNING: Huge snapshot was incomplete" in log
    assert "over the 1 MB limit on the Settings page" in log


# --- When a scan has trouble -------------------------------------------------------

@pytest.fixture
def outlook(app_db, monkeypatch):
    """Record the stalled-scan email instead of handing it to Outlook."""
    from app.routers import email

    sent = []

    def launch(messages, mode, purpose):
        with database.get_db() as db:
            dispatch_id = db.execute(
                """INSERT INTO outlook_dispatches
                       (purpose, task_name, payload_path, receipt_path, status, message_count)
                   VALUES (?, ?, 'payload', 'receipt', 'pending', 1)""",
                (purpose, f"settings-test-{len(sent)}"),
            ).lastrowid
        sent.append(messages[0]["subject"])
        return {"id": int(dispatch_id), "status": "pending"}

    monkeypatch.setattr(email, "launch_outlook_dispatch", launch)
    notifications.save_notification_settings(["ops@example.com"])
    return sent


def test_one_stalled_threshold_drives_the_badges_and_the_email(outlook):
    job_id = jobs.create_job("full_scan")
    run_id = modules.create_module_run("report_catalog", scanner_job_id=job_id)
    quiet_since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    with database.get_db() as db:
        db.execute("UPDATE scanner_jobs SET heartbeat_at=? WHERE id=?", (quiet_since, job_id))
        db.execute("UPDATE scanner_module_runs SET heartbeat_at=? WHERE id=?", (quiet_since, run_id))

    # Five quiet minutes are inside today's ten.
    job = jobs.get_job(job_id)
    assert (job["is_stale"], job["stale_after_seconds"]) == (False, 600)
    assert modules.get_module_run(run_id)["is_stalled"] is False
    assert notifications.notify_stalled_module_runs()["stalled"] == 0 and outlook == []

    registry.save({"scan.stalled_after_seconds": 120})

    job = jobs.get_job(job_id)
    assert (job["is_stale"], job["display_status"], job["stale_after_seconds"]) == (True, "stale", 120)
    assert [item["is_stale"] for item in jobs.list_jobs()] == [True]
    assert jobs.find_active_job({"full_scan"})["is_stale"] is True
    assert modules.get_module_run(run_id)["display_status"] == "stalled"
    assert [item["is_stalled"] for item in modules.runs_for_job(job_id)] == [True]
    assert [item["is_stalled"] for item in modules.list_module_runs("report_catalog")] == [True]
    catalog = next(item for item in modules.module_definitions_with_runs() if item["key"] == "report_catalog")
    assert catalog["current_run"]["is_stalled"] is True
    assert notifications.notify_stalled_module_runs()["stalled"] == 1
    assert len(outlook) == 1 and "stalled" in outlook[0].casefold()


# --- Read-only PostgreSQL checks ---------------------------------------------------

@pytest.fixture
def postgres(app_db, monkeypatch):
    """A stand-in driver that records how each read-only session is opened."""
    calls = []

    class Connection:
        def set_session(self, **kwargs):
            calls.append(("session", kwargs))

    def connect(**kwargs):
        calls.append(("connect", kwargs))
        return Connection()

    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(connect=connect))
    for module in (prober, pg_deps):
        monkeypatch.setattr(module, "PGHOST", "db.internal")
        monkeypatch.setattr(module, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(prober, "PGUSER", "reader")
    monkeypatch.setattr(prober, "PGPASSWORD", "not-a-real-password")
    return calls


def _sessions(calls):
    """``(connect_timeout, options)`` of each connection, after proving it is read-only."""
    opened = [kwargs for kind, kwargs in calls if kind == "connect"]
    forced = [kwargs for kind, kwargs in calls if kind == "session"]
    assert forced == [{"readonly": True, "autocommit": True}] * len(opened)
    calls.clear()
    return [(kwargs["connect_timeout"], kwargs["options"]) for kwargs in opened]


def _open_every_probe_session():
    assert prober._get_pg_connection(database="warehouse") is not None
    assert prober._get_flow_pg_connection(database="warehouse") is not None
    assert pg_deps._flow_catalog_connection("warehouse") is not None


def test_probe_sessions_use_the_saved_timeouts_and_stay_read_only(postgres):
    _open_every_probe_session()
    today = "-c statement_timeout=180000 -c lock_timeout=30000"
    assert _sessions(postgres) == [
        (10, today), (10, today), (10, f"{today} -c application_name=Metronome_Lineage"),
    ]

    registry.save({
        "probe.connect_timeout_seconds": 25,
        "probe.lock_timeout_seconds": 5,
        "probe.statement_timeout_seconds": 600,
    })
    _open_every_probe_session()

    saved = "-c statement_timeout=600000 -c lock_timeout=5000"
    assert _sessions(postgres) == [
        (25, saved), (25, saved), (25, f"{saved} -c application_name=Metronome_Lineage"),
    ]


def test_data_quality_checks_open_their_session_with_the_same_limits(postgres, monkeypatch):
    from app.source_identity import postgres_server_identity

    monkeypatch.setattr(data_quality, "PGHOST", "db.internal")
    monkeypatch.setattr(data_quality, "UPLOAD_PGHOST", "")
    registry.save({"probe.statement_timeout_seconds": 45})
    source = {
        "server_name": postgres_server_identity("db.internal", data_quality.PGPORT),
        "database_name": "warehouse", "schema_name": "sales", "relation_name": "orders",
    }

    connection, error = data_quality._postgres_check_connection(source, {})

    assert error is None and connection is not None
    assert _sessions(postgres) == [(10, "-c statement_timeout=45000 -c lock_timeout=30000")]


# --- Power BI ----------------------------------------------------------------------

class _PowerBi:
    """Stands in for httpx.Client and records the timeout each one was given."""
    timeouts: list = []

    def __init__(self, *, timeout, proxy):
        type(self).timeouts.append(timeout)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.fixture
def power_bi(app_db, monkeypatch):
    _PowerBi.timeouts = []
    monkeypatch.setattr(pbi_fetch, "get_access_token", lambda: {"access_token": "test-token"})
    monkeypatch.setattr(pbi_fetch, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(pbi_fetch, "_already_synced_usage_days", lambda: set())
    monkeypatch.setattr(pbi_fetch.httpx, "Client", _PowerBi)
    return _PowerBi.timeouts


WORKSPACE = "11111111-1111-1111-1111-111111111111"
REPORT = "22222222-2222-2222-2222-222222222222"


def test_power_bi_requests_wait_for_the_saved_time(power_bi, monkeypatch):
    monkeypatch.setattr(pbi_fetch, "_get_json", lambda *_args, **_kwargs: {"value": [], "lastResultSet": True})

    pbi_fetch.fetch_report_pages(WORKSPACE, REPORT)
    pbi_fetch.fetch_dataset_last_refresh(WORKSPACE, REPORT)
    pbi_fetch.fetch_usage_payload(days_back=1)
    assert power_bi == [120, 120, 120]

    registry.save({"pbi.request_timeout_seconds": 45})
    power_bi.clear()
    pbi_fetch.fetch_report_pages(WORKSPACE, REPORT)
    pbi_fetch.fetch_dataset_last_refresh(WORKSPACE, REPORT)
    pbi_fetch.fetch_usage_payload(days_back=1)
    assert power_bi == [45, 45, 45]


def _endless_activity(monkeypatch):
    """Power BI always has another page; returns the pages served per day."""
    served = {}

    def get_json(_client, _token, url, params=None):
        day = url.rsplit("day=", 1)[1] if params is None else params["startDateTime"][1:11]
        served[day] = served.get(day, 0) + 1
        return {
            "activityEventEntities": [{"Activity": "ViewReport", "ReportName": "Busy", "UserId": "a@example.test"}],
            "continuationToken": "next",
            "continuationUri": f"https://api.test/next?day={day}",
        }

    monkeypatch.setattr(pbi_fetch, "_get_json", get_json)
    return served


def test_activity_pages_read_per_day_follow_the_setting(power_bi, monkeypatch):
    served = _endless_activity(monkeypatch)

    by_default = pbi_fetch.fetch_usage_payload(days_back=1)
    assert list(served.values()) == [200]
    assert by_default["days_synced"] == [] and by_default["failed_days"] == 1

    served.clear()
    registry.save({"pbi.activity_pages_per_day": 3})
    limited = pbi_fetch.fetch_usage_payload(days_back=2)

    assert list(served.values()) == [3, 3]
    # A day cut off at the limit is still never recorded as complete.
    assert limited["days_synced"] == [] and limited["failed_days"] == 2
    assert {entry["view_count"] for entry in limited["entries"]} == {3}


def _usage_sync_launches(monkeypatch, *, service_principal):
    """Run a usage sync as the Windows host would; returns what it was asked to fetch."""
    import threading

    asked = {"days": [], "arguments": []}
    done = threading.Event()

    def run_usage_sync(days_back, cancel_generation=None):
        asked["days"].append(days_back)
        done.set()
        return {"status": "completed"}

    def launch_script(script, arguments, sync_type, **_kwargs):
        asked["arguments"].append((script.name, list(arguments), sync_type))
        return {"status": "launched"}

    monkeypatch.setattr(pbi_sync.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pbi_sync, "service_principal_configured", lambda: service_principal)
    monkeypatch.setattr(pbi_sync, "cached_account_available", lambda: True)
    monkeypatch.setattr(pbi_sync, "_launch_powershell_background", launch_script)
    monkeypatch.setattr(pbi_fetch, "run_usage_sync", run_usage_sync)

    def launch():
        done.clear()
        result = pbi_sync.trigger_pbi_usage_sync(cancel_existing=False)
        assert result["status"] == "launched"
        if not service_principal:
            assert done.wait(10), "the usage sync never started"

    return asked, launch


def test_usage_sync_with_the_saved_account_looks_back_the_saved_days(app_db, monkeypatch):
    asked, launch = _usage_sync_launches(monkeypatch, service_principal=False)

    launch()
    registry.save({"pbi.usage_days": 7})
    launch()

    assert asked["days"] == [30, 7]


def test_usage_sync_script_is_told_the_days_only_when_they_differ_from_its_own(app_db, monkeypatch):
    asked, launch = _usage_sync_launches(monkeypatch, service_principal=True)

    launch()
    registry.save({"pbi.usage_days": 7})
    launch()

    today = ["-ApiBase", "http://localhost:8000", "-NoPause"]  # exactly the command used before
    assert asked["arguments"] == [
        ("pbi_usage_sync.ps1", today, "usage"),
        ("pbi_usage_sync.ps1", ["-ApiBase", "http://localhost:8000", "-DaysBack", "7", "-NoPause"], "usage"),
    ]


def test_a_scan_waits_the_saved_time_for_a_power_bi_sync(app_db, monkeypatch):
    def waited():
        launch_id = pbi_sync._record_sync_run("usage", "launched", "started")
        # The clock jumps a day between starting to wait and the first check.
        ticks = iter((0.0, 86400.0))
        monkeypatch.setattr(pbi_sync.time, "monotonic", lambda: next(ticks))
        return pbi_sync.wait_for_pbi_sync_completion(
            {"status": "launched", "run_id": launch_id}, sync_type="usage",
        )

    by_default = waited()
    assert by_default["message"] == "Power BI usage did not report completion within 1200 seconds."

    registry.save({"pbi.sync_wait_minutes": 2})
    saved = waited()

    assert saved["status"] == "failed" and saved["reason_code"] == "power_bi_usage_timeout"
    assert saved["message"] == "Power BI usage did not report completion within 120 seconds."


# --- Freshness and alerts ----------------------------------------------------------

def _daily_rule_due_hours_ago(hours):
    """A daily rule whose latest occurrence fell due ``hours`` ago, in Dubai time."""
    due = datetime.now(DUBAI) - timedelta(hours=hours)
    return {"type": "daily", "time": due.strftime("%H:%M"), "days": [], "day": None, "timezone": "Asia/Dubai"}


def test_grace_after_a_scheduled_time_follows_the_setting(app_db):
    # Due 30 hours ago and again 6 hours ago; the last data is 40 hours old.
    rule = _daily_rule_due_hours_ago(30)
    last_data = datetime.now(timezone.utc) - timedelta(hours=40)
    flow = {
        "schedule_type": "daily", "schedule_time": rule["time"], "schedule_days": "[]", "schedule_day": None,
        "enabled": 1, "freshness_effective_from_at": None, "last_execution_success_at": last_data.isoformat(),
    }

    # Today's 24 hours: the run due 30 hours ago is past its grace and was missed.
    assert freshness.grace_period() == timedelta(hours=24)
    assert prober._compute_status_for_rule(last_data.isoformat(), rule) == "outdated"
    assert freshness.flow_freshness(flow)[1]["status"] == "overdue"

    registry.save({"freshness.grace_hours": 48})  # applied at once, no restart

    # Now only the run due 54 hours ago is past its grace, and the data is newer.
    assert freshness.grace_period() == timedelta(hours=48)
    assert prober._compute_status_for_rule(last_data.isoformat(), rule) == "fresh"
    assert freshness.flow_freshness(flow)[1]["status"] == "healthy"

    registry.save({"freshness.grace_hours": None})
    assert prober._compute_status_for_rule(last_data.isoformat(), rule) == "outdated"


def _file_source(db, source_id, path, *, age_days):
    path.write_text("id\n1\n2\n", encoding="utf-8")
    modified = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(path, (modified, modified))
    db.execute(
        """INSERT INTO sources (id, name, type, connection_info, discovered_by, archived,
                                freshness_mode, freshness_rule_type, custom_fresh_days)
           VALUES (?, ?, 'csv', ?, 'test', 0, 'manual', 'custom', 1)""",
        (source_id, str(path), str(path)),
    )


def _probe(monkeypatch):
    monkeypatch.setattr(prober, "PGHOST", "")
    monkeypatch.setattr(prober, "UPLOAD_PGHOST", "")
    monkeypatch.setattr(data_quality, "run_quality_checks", lambda: {})
    return prober.run_probe()


def _freshness_alerts():
    with database.get_db() as db:
        return [
            (row["source_id"], row["severity"], row["resolution_status"])
            for row in db.execute(
                """SELECT source_id, severity, resolution_status FROM alerts
                    WHERE message LIKE 'Source data is outside freshness rule%' ORDER BY id"""
            )
        ]


def test_alert_level_for_an_outdated_source_follows_the_setting(app_db, tmp_path, monkeypatch):
    with database.get_db() as db:
        _file_source(db, 1, tmp_path / "orders.csv", age_days=10)

    _probe(monkeypatch)
    assert _freshness_alerts() == [(1, "critical", None)]  # today's level

    registry.save({"freshness.alert_severity": "warning"})
    with database.get_db() as db:
        _file_source(db, 2, tmp_path / "returns.csv", age_days=10)
    _probe(monkeypatch)

    # The new alert has the saved level; the open one keeps its own and is not raised twice.
    assert _freshness_alerts() == [(1, "critical", None), (2, "warning", None)]

    # Once the files are current again, alerts of either level close.
    for name in ("orders.csv", "returns.csv"):
        os.utime(tmp_path / name)
    _probe(monkeypatch)
    assert _freshness_alerts() == [(1, "critical", "resolved"), (2, "warning", "resolved")]


def _schedule_mismatches():
    with database.get_db() as db:
        return [row["status"] for row in db.execute(
            "SELECT status FROM actions WHERE type='schedule_mismatch' ORDER BY id")]


def test_how_far_a_report_may_lag_its_source_follows_the_setting(app_db, tmp_path, monkeypatch):
    # The source changed two hours ago; the report last refreshed five hours ago.
    refreshed = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(timespec="seconds")
    path = tmp_path / "orders.csv"
    with database.get_db() as db:
        _file_source(db, 1, path, age_days=0)
        db.execute(
            "INSERT INTO reports (id, name, archived, pbi_last_refresh_at) VALUES (1, 'Orders', 0, ?)",
            (refreshed,),
        )
        db.execute("INSERT INTO report_tables (report_id, table_name, source_id) VALUES (1, 'Orders', 1)")
    changed = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(path, (changed, changed))

    _probe(monkeypatch)
    assert _schedule_mismatches() == ["open"]  # three hours behind, more than today's one

    registry.save({"freshness.report_lag_hours": 6})
    _probe(monkeypatch)
    assert _schedule_mismatches() == ["resolved"]

    registry.save({"freshness.report_lag_hours": 2})
    _probe(monkeypatch)
    assert _schedule_mismatches() == ["resolved", "open"]


def test_freshness_history_is_kept_for_the_saved_number_of_days(app_db, tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)

    def history():
        with database.get_db() as db:
            rows = db.execute("SELECT probed_at FROM source_probes WHERE source_id=1 ORDER BY probed_at")
            return [round((now - datetime.fromisoformat(row["probed_at"])).total_seconds() / 86400) for row in rows]

    with database.get_db() as db:
        _file_source(db, 1, tmp_path / "orders.csv", age_days=0)
        for age in (100, 40, 10):
            db.execute(
                """INSERT INTO source_probes (source_id, probed_at, last_data_at, row_count, status)
                   VALUES (1, ?, ?, 2, 'fresh')""",
                ((now - timedelta(days=age)).isoformat(), (now - timedelta(days=age)).isoformat()),
            )

    _probe(monkeypatch)
    assert history() == [40, 10, 0]  # today's ninety days

    registry.save({"freshness.probe_history_days": 30})
    _probe(monkeypatch)
    assert history() == [10, 0, 0]
