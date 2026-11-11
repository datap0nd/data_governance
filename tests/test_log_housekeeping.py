"""Service logs stay bounded: what is written, what is deleted, and who may delete."""
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database, log_housekeeping, logging_setup, settings
from app.routers import system_logs

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
MB = 1024 * 1024


def _log(folder, name, *, size=1024, age_days=0.0):
    path = folder / name
    with path.open("wb") as handle:
        handle.truncate(size)
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def logs(tmp_path, monkeypatch):
    folder = tmp_path / "logs"
    folder.mkdir()
    monkeypatch.setenv("DG_LOG_DIR", str(folder))
    return folder


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(settings, "DB_PATH", str(path))
    database.init_db()
    return path


def test_logs_nobody_wrote_to_for_the_keep_period_are_deleted(logs):
    old_rotated = _log(logs, "mx_analytics-20261001T080000.000.log", age_days=40)
    old_plain = _log(logs, "flow_worker_legacy.log", age_days=40)
    recent_rotated = _log(logs, "mx_analytics-20261110T080000.000.log", age_days=1)
    active = _log(logs, "mx_analytics.log", age_days=0)

    result = log_housekeeping.prune(logs, keep_days=14, max_total_bytes=0, now=NOW)

    assert not old_rotated.exists() and not old_plain.exists()
    assert recent_rotated.exists() and active.exists()
    assert (result["deleted_files"], result["deleted_bytes"]) == (2, 2048)
    assert result["remaining_bytes"] == 2048


def test_size_limit_removes_rotated_logs_oldest_first_and_never_an_active_log(logs):
    active = _log(logs, "mx_analytics_error.log", size=6 * MB)
    oldest = _log(logs, "mx_analytics-20261108T080000.000.log", size=4 * MB, age_days=3)
    middle = _log(logs, "flow_worker-20261109T080000.000.log", size=4 * MB, age_days=2)
    newest = _log(logs, "mx_analytics-20261110T080000.000.log", size=4 * MB, age_days=1)

    result = log_housekeeping.prune(logs, keep_days=0, max_total_bytes=12 * MB, now=NOW)

    assert not oldest.exists() and not middle.exists()
    assert newest.exists() and active.exists()
    assert result["remaining_bytes"] == 10 * MB
    assert result["over_limit"] is False


def test_an_active_log_larger_than_the_limit_is_reported_not_deleted(logs):
    active = _log(logs, "mx_analytics.log", size=8 * MB)
    rotated = _log(logs, "mx_analytics-20261110T080000.000.log", size=2 * MB, age_days=1)

    result = log_housekeeping.prune(logs, keep_days=0, max_total_bytes=4 * MB, now=NOW)

    assert active.exists() and not rotated.exists()
    assert result["over_limit"] is True
    assert result["remaining_bytes"] == 8 * MB


def test_only_log_files_directly_in_the_folder_are_considered(logs, tmp_path):
    keep = [_log(logs, "notes.txt", age_days=90), _log(logs, "governance.db", age_days=90)]
    nested = logs / "archive"
    nested.mkdir()
    keep.append(_log(nested, "old.log", age_days=90))
    outside = _log(tmp_path, "outside.log", age_days=90)
    link = logs / "linked.log"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link = None

    result = log_housekeeping.prune(logs, keep_days=14, max_total_bytes=1, now=NOW)

    assert all(path.exists() for path in keep)
    assert outside.exists()
    assert result["deleted_files"] == 0
    if link is not None:
        assert link.is_symlink()


def test_a_missing_folder_is_not_an_error(tmp_path):
    result = log_housekeeping.prune(tmp_path / "absent", keep_days=14, max_total_bytes=MB, now=NOW)
    assert result["deleted_files"] == 0
    assert log_housekeeping.summary(tmp_path / "absent")["exists"] is False


def test_summary_lists_the_largest_files_first(logs):
    _log(logs, "mx_analytics.log", size=3 * MB)
    _log(logs, "mx_analytics-20261110T080000.000.log", size=5 * MB, age_days=1)
    _log(logs, "flow_worker.log", size=1 * MB)

    state = log_housekeeping.summary(logs, largest=2)

    assert (state["file_count"], state["total_bytes"], state["rotated_bytes"]) == (3, 9 * MB, 5 * MB)
    assert [(item["name"], item["rotated"]) for item in state["largest"]] == [
        ("mx_analytics-20261110T080000.000.log", True), ("mx_analytics.log", False),
    ]


def test_rotated_names_match_what_nssm_and_the_worker_launcher_produce():
    assert log_housekeeping.ROTATED.match("mx_analytics-20261111T072500.123.log")
    assert log_housekeeping.ROTATED.match("flow_worker_bi-desktop-headed-2-20261111T072500.123.log")
    assert not log_housekeeping.ROTATED.match("mx_analytics.log")
    assert not log_housekeeping.ROTATED.match("flow_worker_bi-desktop-headed-2.log")


def test_run_uses_the_saved_limits_and_clamps_them(logs, app_db):
    assert (log_housekeeping.keep_days(), log_housekeeping.max_total_mb()) == (14, 1024)
    settings.set_setting(log_housekeeping.KEEP_DAYS_KEY, "3")
    settings.set_setting(log_housekeeping.MAX_TOTAL_MB_KEY, "5")  # below the minimum
    assert (log_housekeeping.keep_days(), log_housekeeping.max_total_mb()) == (3, 50)
    settings.set_setting(log_housekeeping.KEEP_DAYS_KEY, "not a number")
    assert log_housekeeping.keep_days() == 14

    settings.set_setting(log_housekeeping.KEEP_DAYS_KEY, "3")
    stale = _log(logs, "mx_analytics-20261101T080000.000.log", age_days=5)
    fresh = _log(logs, "mx_analytics.log")
    result = log_housekeeping.run(now=NOW)
    assert not stale.exists() and fresh.exists()
    assert result["deleted_files"] == 1


def test_update_transcripts_keep_the_newest_twenty(tmp_path):
    folder = tmp_path / "updates" / "logs"
    folder.mkdir(parents=True)
    for index in range(25):
        _log(folder, f"attempt-{index:02d}.log", age_days=30 + index)

    deleted = log_housekeeping._prune_update_files(tmp_path / "updates", keep_days=14, now=NOW)

    assert deleted == 5
    assert sorted(path.name for path in folder.iterdir())[-1] == "attempt-19.log"
    assert len(list(folder.iterdir())) == 20


def _access(method, path, status):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.5:51000", method, path, "1.1", status), None,
    )


@pytest.fixture
def request_log_mode():
    before = logging_setup.request_log_mode()
    yield logging_setup.set_request_log_mode
    logging_setup.set_request_log_mode(before)


def test_request_log_keeps_failures_and_changes_and_drops_polling(request_log_mode):
    access = logging_setup.AccessLogFilter()
    request_log_mode("changes")
    assert not access.filter(_access("GET", "/api/flows", 200))
    assert not access.filter(_access("POST", "/api/flows/worker/bi-desktop-headless/claim", 200))
    assert not access.filter(_access("POST", "/api/flows/worker/register", 200))
    assert access.filter(_access("PUT", "/api/flows/12", 200))
    assert access.filter(_access("POST", "/api/flows/12/run", 200))
    assert access.filter(_access("GET", "/api/flows/999", 404))
    assert access.filter(_access("POST", "/api/flows/worker/register", 500))

    request_log_mode("errors")
    assert not access.filter(_access("PUT", "/api/flows/12", 200))
    assert access.filter(_access("GET", "/api/flows/999", 404))

    request_log_mode("all")
    assert access.filter(_access("GET", "/api/flows", 200))

    assert request_log_mode("nonsense") == "changes"


def test_request_log_passes_a_record_it_does_not_recognise(request_log_mode):
    request_log_mode("changes")
    access = logging_setup.AccessLogFilter()
    odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "started", None, None)
    assert access.filter(odd)
    assert access.filter(_access("GET", "/api/flows", "not-a-status"))


def test_an_identical_message_is_written_a_few_times_then_counted():
    clock = [0.0]
    limiter = logging_setup.RepeatLimiter(burst=3, window=60.0, clock=lambda: clock[0])

    def record(text="Job failed: database is locked"):
        return logging.LogRecord("scheduler", logging.ERROR, __file__, 1, "%s", (text,), None)

    assert [limiter.filter(record()) for _ in range(10)] == [True] * 3 + [False] * 7
    assert limiter.filter(record("a different message"))

    clock[0] = 61.0
    after_quiet = record()
    assert limiter.filter(after_quiet)
    assert "repeated 7 more time(s)" in after_quiet.getMessage()

    clock[0] = 130.0
    plain = record()
    assert limiter.filter(plain)
    assert plain.getMessage() == "Job failed: database is locked"


def test_configure_quietens_the_scheduler_and_filters_requests():
    logging_setup.configure()
    assert logging.getLogger("apscheduler.executors.default").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert any(isinstance(item, logging_setup.AccessLogFilter)
               for item in logging.getLogger("uvicorn.access").filters)


def test_saved_request_log_mode_is_applied_on_refresh(app_db, request_log_mode):
    settings.set_setting(logging_setup.REQUEST_LOG_KEY, "errors")
    assert logging_setup.refresh_request_log_mode() == "errors"
    settings.set_setting(logging_setup.REQUEST_LOG_KEY, "garbage")
    assert logging_setup.refresh_request_log_mode() == "changes"


def _from(is_host):
    return SimpleNamespace(state=SimpleNamespace(actor="Host PC" if is_host else "Maya",
                                                 person_id=None if is_host else 4, is_host=is_host))


def test_anyone_sees_log_usage_but_only_the_host_pc_cleans_up(logs, app_db):
    stale = _log(logs, "mx_analytics-20260901T080000.000.log", size=2 * MB, age_days=60)
    _log(logs, "mx_analytics.log", size=MB)

    state = system_logs.get_logs()
    assert (state["total_bytes"], state["keep_days"], state["max_total_mb"]) == (3 * MB, 14, 1024)
    assert state["request_log"] in logging_setup.REQUEST_LOG_MODES

    with pytest.raises(HTTPException) as refused:
        system_logs.prune_logs(_from(is_host=False))
    assert refused.value.status_code == 403
    assert stale.exists()

    cleaned = system_logs.prune_logs(_from(is_host=True))
    assert not stale.exists()
    assert (cleaned["deleted_files"], cleaned["deleted_bytes"], cleaned["total_bytes"]) == (1, 2 * MB, MB)
    with database.get_db() as db:
        event = db.execute("SELECT entity_name, action, actor FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(event) == ("Logs", "pruned", "Host PC")
