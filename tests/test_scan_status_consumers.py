import tempfile

from app import database
from app import main
from app.routers import scanner
from app.scanner import jobs, pg_cron, pg_deps


def _fresh_database(monkeypatch):
    temp_dir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(database, "DB_PATH", f"{temp_dir.name}/scanner-consumers.db")
    database.init_db()
    return temp_dir


def test_scheduled_scan_keeps_probe_inside_scan_lifecycle(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    submitted = []
    try:
        monkeypatch.setattr(
            scanner,
            "_submit_job",
            lambda job_id, worker, *args: submitted.append((job_id, worker, args)),
        )

        result = main._scheduled_scan(cancel_generation=17, stop_existing=False)

        assert result["accepted"] is True
        assert result["job"]["trigger_source"] == "scheduled_scan"
        assert result["job"]["context"] == {"includes_pbi_sync": False}
        assert submitted[0][1] is scanner._execute_scan_only_job
        assert submitted[0][2] == (17,)
    finally:
        temp_dir.cleanup()


def test_scheduled_scan_reuses_global_lane_instead_of_overlapping(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    submitted = []
    try:
        monkeypatch.setattr(
            scanner,
            "_submit_job",
            lambda job_id, worker, *args: submitted.append((job_id, worker, args)),
        )

        first = main._scheduled_scan(cancel_generation=18, stop_existing=False)
        second = main._scheduled_scan(cancel_generation=19, stop_existing=False)

        assert first["accepted"] is True
        assert second["accepted"] is False
        assert second["job_id"] == first["job_id"]
        assert len(submitted) == 1
    finally:
        temp_dir.cleanup()


def test_scheduled_overall_refresh_starts_usage_sync_after_warning_scan(monkeypatch):
    expected = {"accepted": True, "status": "queued", "job_id": 41}
    monkeypatch.setattr(scanner, "start_scheduled_full_scan_job", lambda: expected)

    assert main._scheduled_overall_refresh() == expected


def test_scheduled_overall_refresh_retries_when_scanner_lane_is_busy(monkeypatch):
    expected = {
        "accepted": False,
        "status": "running",
        "job_id": 40,
        "job": {
            "job_type": "source_probe",
            "trigger_source": "manual",
            "context": {},
        },
    }
    scheduled = []

    class Scheduler:
        def add_job(self, function, trigger, **kwargs):
            scheduled.append((function, trigger, kwargs))

        def get_job(self, _job_id):
            return None

    monkeypatch.setattr(scanner, "start_scheduled_full_scan_job", lambda: expected)
    monkeypatch.setattr(main, "_scheduler", Scheduler())

    result = main._scheduled_overall_refresh()

    assert result["accepted"] is False
    assert result["retry_scheduled_for"]
    assert len(scheduled) == 1
    assert scheduled[0][0] is main._scheduled_overall_refresh
    assert scheduled[0][1] == "date"
    assert scheduled[0][2]["id"] == "daily_overall_refresh_retry"


def test_manual_scan_keeps_probe_inside_scan_lifecycle(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    submitted = []
    try:
        monkeypatch.setattr(scanner, "_require_scan_access", lambda request: None)
        monkeypatch.setattr(
            scanner,
            "stop_pbi_sync_processes",
            lambda *args, **kwargs: {"scanner": {"generation": 31}},
        )
        monkeypatch.setattr(
            scanner,
            "_submit_job",
            lambda job_id, worker, *args: submitted.append((job_id, worker, args)),
        )

        result = scanner.do_scan(object())

        assert result["accepted"] is True
        assert result["job"]["status"] == "queued"
        assert submitted[0][1] is scanner._execute_full_scan_job
        assert submitted[0][2][0] == 31
    finally:
        temp_dir.cleanup()


def test_manual_scan_redacts_unexpected_error(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        job_id = jobs.create_job("full_scan")
        monkeypatch.setattr(
            scanner,
            "run_scan",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("password=api-secret")),
        )

        scanner._execute_scan_only_job(job_id, 32)
        result = jobs.get_job(job_id)

        assert result["status"] == "failed"
        assert result["result"]["error"] == "Redacted; review server logs."
        assert "api-secret" not in str(result)
    finally:
        temp_dir.cleanup()


def test_direct_postgres_component_endpoints_redact_errors(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        monkeypatch.setattr(
            pg_deps,
            "scan_pg_dependencies",
            lambda **kwargs: {"status": "failed", "error": "password=deps-secret"},
        )
        dependency_job = jobs.create_job("postgres_lineage")
        scanner._execute_postgres_lineage_job(dependency_job, None)

        monkeypatch.setattr(
            pg_cron,
            "scan_pg_cron",
            lambda: {"status": "failed", "error": "password=cron-secret"},
        )
        schedule_job = jobs.create_job("postgres_schedules")
        scanner._execute_postgres_cron_job(schedule_job, None)

        dependencies = jobs.get_job(dependency_job)
        schedules = jobs.get_job(schedule_job)
        assert dependencies["result"]["error"] == "Redacted; review server logs."
        assert schedules["result"]["error"] == "Redacted; review server logs."
        assert "secret" not in str({"dependencies": dependencies, "schedules": schedules})
    finally:
        temp_dir.cleanup()
