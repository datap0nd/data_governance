from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
from pathlib import Path
import tempfile

import pytest

from app import database
from app.routers import scanner
from app.scanner import jobs, pg_deps, prober


def _fresh_database(monkeypatch):
    temp_dir = tempfile.TemporaryDirectory()
    db_path = f"{temp_dir.name}/scanner-observability.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return temp_dir


def test_jobs_expose_live_phase_and_detect_stale_running_and_queued(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        running_id = jobs.create_job("postgres_lineage", message="accepted")
        jobs.mark_running(
            running_id,
            current_step="Reading PostgreSQL catalog",
            message="Scanning warehouse.",
            progress_current=1,
            progress_total=3,
        )
        old = (datetime.now(timezone.utc) - timedelta(seconds=jobs.STALE_AFTER_SECONDS + 5)).isoformat()
        with database.get_db() as db:
            db.execute(
                "UPDATE scanner_jobs SET heartbeat_at=? WHERE id=?",
                (old, running_id),
            )

        running = jobs.get_job(running_id)
        assert running["current_step"] == "Reading PostgreSQL catalog"
        assert running["progress_current"] == 1
        assert running["progress_total"] == 3
        assert running["status"] == "running"
        assert running["display_status"] == "stale"
        assert running["is_stale"] is True

        jobs.stop_active_jobs("test transition")
        queued_id = jobs.create_job("source_probe", message="waiting for worker")
        with database.get_db() as db:
            db.execute(
                "UPDATE scanner_jobs SET heartbeat_at=? WHERE id=?",
                (old, queued_id),
            )
        queued = jobs.get_job(queued_id)
        assert queued["status"] == "queued"
        assert queued["display_status"] == "stale"
        assert queued["is_stale"] is True
    finally:
        temp_dir.cleanup()


def test_stop_is_immediate_and_late_worker_cannot_revive_job(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        job_id = jobs.create_job("full_scan")
        jobs.mark_running(job_id, current_step="Syncing Power BI metadata")

        assert jobs.stop_active_jobs("Stopped by user") == 1
        assert jobs.finish_job(
            job_id,
            status="completed",
            result={"status": "completed"},
        ) is False

        stored = jobs.get_job(job_id)
        assert stored["status"] == "stopped"
        assert stored["message"] == "Stopped by user"
        assert stored["finished_at"] is not None
    finally:
        temp_dir.cleanup()


def test_skipped_component_finishes_job_truthfully(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        job_id = jobs.create_job("postgres_schedules")
        jobs.mark_running(job_id, current_step="Reading PostgreSQL schedules")

        assert jobs.finish_job(
            job_id,
            status="skipped",
            result={"status": "skipped", "reason": "pg_cron not installed"},
        ) is True
        stored = jobs.get_job(job_id)
        assert stored["status"] == "completed"
        assert stored["result"]["status"] == "skipped"
        assert stored["message"] == "Completed."
    finally:
        temp_dir.cleanup()


def test_lineage_start_reuses_existing_job_and_never_overlaps(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    submitted = []
    try:
        monkeypatch.setattr(scanner, "_require_scan_access", lambda request: None)
        monkeypatch.setattr(
            scanner,
            "_submit_job",
            lambda job_id, worker, *args: submitted.append((job_id, worker, args)),
        )

        first = scanner.start_postgres_lineage_job(object(), report_id=42)
        second = scanner.start_postgres_lineage_job(object(), report_id=42)

        assert first["accepted"] is True
        assert first["reused"] is False
        assert first["job"]["context"] == {"report_id": 42}
        assert second["accepted"] is False
        assert second["reused"] is True
        assert second["job_id"] == first["job_id"]
        assert len(submitted) == 1
    finally:
        temp_dir.cleanup()


def test_full_scan_job_covers_pre_scan_power_bi_failure(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    observed = []
    try:
        job_id = jobs.create_job("full_scan")

        def pbi_sync(*args, **kwargs):
            observed.append(jobs.get_job(job_id))
            return {"status": "failed", "message": "refresh unavailable"}

        monkeypatch.setattr(scanner, "trigger_pbi_sync_and_wait", pbi_sync)
        scanner._execute_full_scan_job(job_id, 7, {"status": "stopped"})

        assert observed[0]["status"] == "running"
        assert observed[0]["current_step"] == "Syncing Power BI metadata"
        final = jobs.get_job(job_id)
        assert final["status"] == "failed"
        assert final["result"]["status"] == "pbi_sync_not_completed"
        assert "before report discovery" in final["message"]
    finally:
        temp_dir.cleanup()


def test_full_scan_job_preserves_cancelled_pbi_as_stopped(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        job_id = jobs.create_job("full_scan")
        monkeypatch.setattr(
            scanner,
            "trigger_pbi_sync_and_wait",
            lambda *args, **kwargs: {"status": "cancelled", "message": "user stopped"},
        )

        scanner._execute_full_scan_job(job_id, 8, {})

        assert jobs.get_job(job_id)["status"] == "stopped"
    finally:
        temp_dir.cleanup()


def test_postgres_dependency_scan_reports_each_database_phase(monkeypatch):
    calls = []
    catalog = pg_deps._DatabaseCatalog(
        dependency_rows=(), definitions={}, definition_error=None
    )
    monkeypatch.setattr(
        pg_deps,
        "_required_databases",
        lambda: (["staging", "warehouse"], {"staging": ["configured"], "warehouse": ["flow"]}, set()),
    )
    monkeypatch.setattr(pg_deps, "_fetch_database_catalog", lambda database: catalog)
    monkeypatch.setattr(
        pg_deps,
        "_apply_database_catalog",
        lambda database, catalog, **kwargs: {
            "status": "completed",
            "mvs_found": 1,
            "deps_created": 2,
            "sources_created": 0,
            "changed_queries": 0,
            "definition_status": "completed",
            "log": "done",
            "query_change_log": "",
        },
    )
    monkeypatch.setattr(
        pg_deps,
        "scanner_job_heartbeat",
        lambda job_id, **kwargs: calls.append((job_id, kwargs)),
    )

    result = pg_deps.scan_pg_dependencies(operation_id=91, cancel_generation=None)

    assert result["status"] == "completed"
    assert result["deps_created"] == 4
    assert calls[0][1]["current_step"] == "Discovering PostgreSQL databases"
    assert any(
        values["current_step"] == "Reading PostgreSQL catalog"
        and values["message"] == "Scanning database staging."
        and values["progress_current"] == 0
        for _, values in calls
    )
    assert any(
        values["current_step"] == "Applying lineage snapshot"
        and values["message"] == "Finished database warehouse."
        and values["progress_current"] == 2
        for _, values in calls
    )
    assert calls[-1][1]["current_step"] == "Finalizing PostgreSQL lineage"


def test_interrupted_jobs_are_recovered_on_restart(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        job_id = jobs.create_job("postgres_lineage")
        jobs.mark_running(job_id, current_step="Reading PostgreSQL catalog")

        assert jobs.recover_interrupted_jobs() == 1
        assert jobs.recover_interrupted_jobs() == 0
        recovered = jobs.get_job(job_id)
        assert recovered["status"] == "stopped"
        assert "service restarted" in recovered["message"]
    finally:
        temp_dir.cleanup()


def test_cancelling_queued_future_does_not_deadlock_its_callback():
    future = Future()
    callback_ran = []

    def forget(_future):
        with scanner._job_future_lock:
            scanner._job_futures.pop(901, None)
        callback_ran.append(True)

    future.add_done_callback(forget)
    with scanner._job_future_lock:
        scanner._job_futures[901] = future

    scanner._cancel_queued_jobs()

    assert future.cancelled() is True
    assert callback_ran == [True]


def test_uncaught_worker_crash_is_terminalized(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)

    class InlineExecutor:
        def submit(self, function):
            future = Future()
            try:
                future.set_result(function())
            except Exception as exc:
                future.set_exception(exc)
            return future

    try:
        job_id = jobs.create_job("source_probe")
        monkeypatch.setattr(scanner, "_executor", lambda: InlineExecutor())

        def crash(_job_id):
            jobs.mark_running(_job_id, current_step="Starting")
            raise RuntimeError("boom")

        scanner._submit_job(job_id, crash)

        stored = jobs.get_job(job_id)
        assert stored["status"] == "failed"
        assert stored["message"] == (
            "Scanner worker crashed before it recorded a terminal result."
        )
        assert stored["result"]["error_type"] == "RuntimeError"
    finally:
        temp_dir.cleanup()


def test_submit_failure_does_not_leave_queued_job(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)

    class RejectingExecutor:
        def submit(self, *_args, **_kwargs):
            raise RuntimeError("executor unavailable")

    try:
        job_id = jobs.create_job("source_probe")
        monkeypatch.setattr(scanner, "_executor", lambda: RejectingExecutor())

        with pytest.raises(RuntimeError, match="executor unavailable"):
            scanner._submit_job(job_id, lambda _job_id: None)

        stored = jobs.get_job(job_id)
        assert stored["status"] == "failed"
        assert stored["message"] == "Scanner worker could not be started."
    finally:
        temp_dir.cleanup()


def test_probe_heartbeats_share_writer_and_remain_visible(monkeypatch):
    temp_dir = _fresh_database(monkeypatch)
    try:
        source_file = Path(temp_dir.name) / "daily.csv"
        source_file.write_text("value\n1\n", encoding="utf-8")
        with database.get_db() as db:
            db.execute(
                """INSERT INTO sources(name, type, connection_info, archived)
                   VALUES ('daily.csv', 'csv', ?, 0)""",
                (str(source_file),),
            )

        job_id = jobs.create_job("source_probe")
        jobs.mark_running(job_id, current_step="Starting source probe")
        result = prober.run_probe(operation_id=job_id)

        assert result["status"] in {"completed", "completed_with_warnings"}
        live = jobs.get_job(job_id)
        assert live["current_step"] == "Finalizing source probe"
        assert live["heartbeat_at"] is not None
    finally:
        temp_dir.cleanup()
