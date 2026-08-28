import json
import tempfile

import pytest

from app import database, main
from app.routers import scanner, schedules
from app.scanner import control, lifecycle, prober, runner


def _fresh_database(monkeypatch):
    temp_dir = tempfile.TemporaryDirectory()
    db_path = f"{temp_dir.name}/scan-lifecycle.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return temp_dir, db_path


def _stub_scan_components(monkeypatch, pg_result, *, cron_result=None):
    from app import usage
    from app.routers import best_practices, documentation, schedules
    from app.scanner import pg_cron, pg_deps

    observations = []

    def observe(component):
        with database.get_db() as db:
            row = db.execute(
                "SELECT status, finished_at FROM scan_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        observations.append((component, row["status"], row["finished_at"]))

    def scan_dependencies(scan_run_id=None, **_kwargs):
        observe("postgres_dependencies")
        return pg_result

    def scan_schedules():
        observe("postgres_schedules")
        return cron_result or {"status": "completed"}

    def sync_usage(db):
        observe("usage")
        return {"status": "completed"}

    def best_practice_scan(persist=False):
        observe("best_practices")
        return {"status": "completed"}

    def schedule_scan(persist=True):
        observe("schedule_discrepancies")
        return {"status": "completed"}

    def documentation_scan():
        observe("documentation")
        return {"status": "completed"}

    monkeypatch.setattr(runner, "_backup_db", lambda: None)
    monkeypatch.setattr(runner, "walk_reports_root", lambda root: [])
    monkeypatch.setattr(runner, "deduplicate_sources", lambda reports: {})
    monkeypatch.setattr(pg_deps, "scan_pg_dependencies", scan_dependencies)
    monkeypatch.setattr(pg_cron, "scan_pg_cron", scan_schedules)
    monkeypatch.setattr(usage, "sync_usage_from_csv_if_configured", sync_usage)
    monkeypatch.setattr(best_practices, "run_best_practice_scan", best_practice_scan)
    monkeypatch.setattr(schedules, "run_schedule_discrepancy_scan", schedule_scan)
    monkeypatch.setattr(
        documentation,
        "sync_documentation_completeness_actions",
        documentation_scan,
    )
    return observations


def test_component_serialization_redacts_errors_and_preserves_database_results():
    component = lifecycle.component_result(
        {
            "status": "completed_with_warnings",
            "databases": {
                "warehouse": {"status": "completed", "deps_created": 3},
                "staging": {
                    "status": "failed",
                    "error": "password=do-not-store connection refused",
                    "log": "postgresql://admin:do-not-store@db/staging",
                    "definition_error": "C:/private/driver.log",
                },
            },
        },
        required=True,
    )

    encoded = lifecycle.serialize_components({"postgres_dependencies": component})
    decoded = lifecycle.parse_components(encoded)

    assert "do-not-store" not in encoded
    assert decoded["postgres_dependencies"]["databases"]["warehouse"]["deps_created"] == 3
    failed = decoded["postgres_dependencies"]["databases"]["staging"]
    assert failed["error"] == "Redacted; review server logs."
    assert failed["log"] == "Redacted; review server logs."
    assert failed["definition_error"] == "Redacted; review server logs."


def test_stop_request_leaves_scan_running_for_atomic_runner_finalization(monkeypatch):
    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        with database.get_db() as db:
            cursor = db.execute(
                """INSERT INTO scan_runs(started_at, status, log)
                   VALUES ('2026-01-03', 'running', 'core complete')"""
            )
            scan_id = int(cursor.lastrowid)

        result = control.request_stop_existing_work("new scan requested")

        assert result["scan_runs_stopped"] == 1
        with database.get_db() as db:
            row = db.execute(
                "SELECT status, finished_at, components_json FROM scan_runs WHERE id=?",
                (scan_id,),
            ).fetchone()
        assert row["status"] == "running"
        assert row["finished_at"] is None
        assert row["components_json"] is None
    finally:
        temp_dir.cleanup()


def test_data_quality_exception_downgrades_probe_status(monkeypatch):
    from app.checks import data_quality

    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        monkeypatch.setattr(
            data_quality,
            "run_quality_checks",
            lambda: (_ for _ in ()).throw(RuntimeError("password=do-not-return")),
        )

        result = prober.run_probe()

        assert result["status"] == "completed_with_warnings"
        assert result["data_quality"]["status"] == "failed"
        with database.get_db() as db:
            row = db.execute(
                "SELECT status FROM probe_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["status"] == "completed_with_warnings"
        redacted = lifecycle.redact_component_payload(result)
        assert "do-not-return" not in json.dumps(redacted)
    finally:
        temp_dir.cleanup()


def test_schedule_health_trend_keeps_warning_complete_probe_counts(monkeypatch):
    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        with database.get_db() as db:
            db.execute(
                """INSERT INTO probe_runs
                       (started_at, finished_at, sources_probed, fresh, stale,
                        outdated, unknown, no_rule, status, log)
                   VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 4, 3, 1,
                           0, 0, 0, 'completed_with_warnings', 'quality failed')"""
            )

        trend = schedules.get_health_trend()

        assert trend
        assert trend[-1]["healthy"] == 3
        assert trend[-1]["degraded"] == 1
        assert trend[-1]["unknown"] == 0
    finally:
        temp_dir.cleanup()


def test_scan_schema_api_and_restart_recovery_are_legacy_safe(monkeypatch):
    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        with database.get_db() as db:
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(scan_runs)").fetchall()
            }
            db.execute(
                """INSERT INTO scan_runs(started_at, finished_at, status, log)
                   VALUES ('2026-01-01', '2026-01-01', 'completed', 'legacy')"""
            )
            legacy = db.execute(
                "SELECT * FROM scan_runs WHERE status='completed'"
            ).fetchone()

        assert "components_json" in columns
        assert scanner._scan_run_out(legacy).components is None
        assert main._recover_startup_scan_runs() == 0

        with database.get_db() as db:
            db.execute(
                """INSERT INTO scan_runs(started_at, status, log)
                   VALUES ('2026-01-02', 'running', 'core started')"""
            )

        assert main._recover_startup_scan_runs() == 1
        assert main._recover_startup_scan_runs() == 0
        with database.get_db() as db:
            recovered = db.execute(
                "SELECT * FROM scan_runs WHERE started_at='2026-01-02'"
            ).fetchone()
        recovered_out = scanner._scan_run_out(recovered)
        assert recovered_out.status == "stopped"
        assert recovered_out.finished_at is not None
        assert "interrupted by restart" in recovered_out.log
        assert recovered_out.components["recovery"]["reason_code"] == "interrupted_by_restart"
    finally:
        temp_dir.cleanup()


def test_runner_stays_running_until_warning_components_finish(monkeypatch):
    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        raw_pg_result = {
            "status": "completed_with_warnings",
            "changed_queries": 2,
            "required_databases": ["warehouse", "staging"],
            "databases": {
                "warehouse": {"status": "completed", "deps_created": 4},
                "staging": {
                    "status": "failed",
                    "error": "password=database-secret connection refused",
                },
            },
        }
        observations = _stub_scan_components(monkeypatch, raw_pg_result)

        result = runner.run_scan("unused", run_followup_probe=False)

        assert result["status"] == "completed_with_warnings"
        assert observations
        assert all(status == "running" and finished is None for _, status, finished in observations)
        assert set(result["components"]) == {
            "core",
            "postgres_dependencies",
            "postgres_schedules",
            "usage",
            "probe",
            "governance",
        }
        assert result["components"]["probe"]["status"] == "not_requested"
        assert result["components"]["governance"]["status"] == "completed"
        pg_component = result["components"]["postgres_dependencies"]
        assert pg_component["databases"]["warehouse"]["deps_created"] == 4
        assert pg_component["databases"]["staging"]["error"] == "Redacted; review server logs."

        with database.get_db() as db:
            stored = db.execute(
                "SELECT * FROM scan_runs WHERE id = ?", (result["scan_id"],)
            ).fetchone()
        assert stored["status"] == "completed_with_warnings"
        assert stored["finished_at"] is not None
        assert stored["changed_queries"] == 2
        assert "database-secret" not in stored["components_json"]
        api_run = scanner._scan_run_out(stored)
        assert api_run.components["postgres_dependencies"]["databases"]["staging"][
            "error"
        ] == "Redacted; review server logs."
    finally:
        temp_dir.cleanup()


def test_pg_skips_are_neutral_only_without_active_postgres_work(monkeypatch):
    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        observations = _stub_scan_components(
            monkeypatch,
            {"status": "skipped", "databases": {}, "reason": "not configured"},
            cron_result={"status": "skipped", "reason": "not configured"},
        )
        neutral = runner.run_scan("unused", run_followup_probe=False)

        assert observations
        assert neutral["status"] == "completed"
        assert neutral["components"]["postgres_dependencies"]["status"] == "not_requested"
        assert neutral["components"]["postgres_schedules"]["status"] == "not_requested"

        with database.get_db() as db:
                db.execute(
                    """INSERT INTO sources(name, type, discovered_by, archived)
                   VALUES ('sales.orders', 'postgresql', 'manual', 0)"""
                )

        observations.clear()
        required = runner.run_scan("unused", run_followup_probe=False)

        assert required["status"] == "completed_with_warnings"
        assert required["components"]["postgres_dependencies"]["status"] == "skipped"
        assert required["components"]["postgres_dependencies"]["required"] is True
        assert required["components"]["postgres_schedules"]["status"] == "skipped"
    finally:
        temp_dir.cleanup()


def test_core_failure_gets_one_redacted_terminal_update(monkeypatch):
    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        monkeypatch.setattr(runner, "_backup_db", lambda: None)

        def fail_core(root):
            raise RuntimeError("password=core-secret catalog failed")

        monkeypatch.setattr(runner, "walk_reports_root", fail_core)

        result = runner.run_scan("unused", run_followup_probe=False)

        assert result["status"] == "failed"
        assert result["error"] == "Redacted; review server logs."
        assert "core-secret" not in json.dumps(result)
        with database.get_db() as db:
            stored = db.execute(
                "SELECT * FROM scan_runs WHERE id = ?", (result["scan_id"],)
            ).fetchone()
        assert stored["status"] == "failed"
        assert stored["finished_at"] is not None
        assert "core-secret" not in (stored["components_json"] or "")
        assert lifecycle.parse_components(stored["components_json"])["core"]["error"] == (
            "Redacted; review server logs."
        )
    finally:
        temp_dir.cleanup()


def test_hard_pg_interruption_remains_running_until_startup_recovery(monkeypatch):
    from app.scanner import pg_deps

    temp_dir, _ = _fresh_database(monkeypatch)
    try:
        monkeypatch.setattr(runner, "_backup_db", lambda: None)
        monkeypatch.setattr(runner, "walk_reports_root", lambda root: [])
        monkeypatch.setattr(runner, "deduplicate_sources", lambda reports: {})

        def interrupt_process(scan_run_id=None, **_kwargs):
            raise SystemExit("simulated hard interruption")

        monkeypatch.setattr(pg_deps, "scan_pg_dependencies", interrupt_process)

        with pytest.raises(SystemExit, match="simulated hard interruption"):
            runner.run_scan("unused", run_followup_probe=False)

        with database.get_db() as db:
            interrupted = db.execute(
                "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert interrupted["status"] == "running"
        assert interrupted["finished_at"] is None

        assert main._recover_startup_scan_runs() == 1
        with database.get_db() as db:
            recovered = db.execute(
                "SELECT * FROM scan_runs WHERE id = ?", (interrupted["id"],)
            ).fetchone()
        assert recovered["status"] == "stopped"
        assert recovered["finished_at"] is not None
        assert "interrupted by restart" in recovered["log"]
    finally:
        temp_dir.cleanup()
