from app import main
from app.routers import scanner
from app.scanner import pbi_sync, pg_cron, pg_deps, runner


def test_scheduled_scan_keeps_probe_inside_scan_lifecycle(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner,
        "run_scan",
        lambda **kwargs: calls.append(kwargs) or {
            "status": "completed_with_warnings",
            "reports_scanned": 4,
            "sources_found": 9,
            "probe": {"status": "completed", "statuses": {"fresh": 9}},
        },
    )

    result = main._scheduled_scan(cancel_generation=17, stop_existing=False)

    assert result["status"] == "completed_with_warnings"
    assert result["probe"]["status"] == "completed"
    assert calls == [{"cancel_generation": 17, "run_followup_probe": True}]


def test_scheduled_scan_does_not_run_probe_after_core_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner,
        "run_scan",
        lambda **kwargs: calls.append(kwargs) or {"status": "failed", "error": "core"},
    )

    result = main._scheduled_scan(cancel_generation=18, stop_existing=False)

    assert result["status"] == "failed"
    assert "probe" not in result
    assert calls == [{"cancel_generation": 18, "run_followup_probe": True}]


def test_scheduled_overall_refresh_starts_usage_sync_after_warning_scan(monkeypatch):
    usage_calls = []
    monkeypatch.setattr(
        pbi_sync,
        "stop_pbi_sync_processes",
        lambda message: {"scanner": {"generation": 23}},
    )
    monkeypatch.setattr(main, "_scheduled_pbi_sync", lambda **kwargs: {"status": "completed"})
    monkeypatch.setattr(
        main,
        "_scheduled_scan",
        lambda **kwargs: {"status": "completed_with_warnings", "reports_scanned": 4},
    )
    monkeypatch.setattr(
        pbi_sync,
        "trigger_pbi_usage_sync",
        lambda **kwargs: usage_calls.append(kwargs) or {"status": "launched"},
    )

    result = main._scheduled_overall_refresh()

    assert result["status"] == "completed_with_warnings"
    assert result["pbi_usage_sync"] == {"status": "launched"}
    assert usage_calls == [{"cancel_existing": False, "cancel_generation": 23}]


def test_manual_scan_keeps_probe_inside_scan_lifecycle(monkeypatch):
    scan_calls = []
    monkeypatch.setattr(scanner, "_require_scan_access", lambda request: None)
    monkeypatch.setattr(
        scanner,
        "stop_pbi_sync_processes",
        lambda message: {"scanner": {"generation": 31}},
    )
    monkeypatch.setattr(
        scanner,
        "trigger_pbi_sync_and_wait",
        lambda *args, **kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(
        scanner,
        "run_scan",
        lambda **kwargs: scan_calls.append(kwargs) or {
            "status": "completed_with_warnings",
            "components": {"postgres_dependencies": {"status": "failed"}},
            "probe": {"status": "completed"},
        },
    )

    result = scanner.do_scan(object())

    assert result["status"] == "completed_with_warnings"
    assert result["components"]["postgres_dependencies"]["status"] == "failed"
    assert result["probe"]["status"] == "completed"
    assert scan_calls == [{"cancel_generation": 31, "run_followup_probe": True}]


def test_manual_scan_redacts_unexpected_error(monkeypatch):
    monkeypatch.setattr(scanner, "_require_scan_access", lambda request: None)
    monkeypatch.setattr(
        scanner,
        "stop_pbi_sync_processes",
        lambda message: {"scanner": {"generation": 32}},
    )
    monkeypatch.setattr(
        scanner,
        "trigger_pbi_sync_and_wait",
        lambda *args, **kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(
        scanner,
        "run_scan",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("password=api-secret")),
    )

    result = scanner.do_scan(object())

    assert result["status"] == "failed"
    assert result["error"] == "Redacted; review server logs."
    assert "api-secret" not in str(result)


def test_direct_postgres_component_endpoints_redact_errors(monkeypatch):
    monkeypatch.setattr(scanner, "_require_scan_access", lambda request: None)
    monkeypatch.setattr(
        pg_deps,
        "scan_pg_dependencies",
        lambda: {"status": "failed", "error": "password=deps-secret"},
    )
    monkeypatch.setattr(
        pg_cron,
        "scan_pg_cron",
        lambda: {"status": "failed", "error": "password=cron-secret"},
    )

    dependencies = scanner.do_pg_deps(object())
    schedules = scanner.do_pg_cron(object())

    assert dependencies["error"] == "Redacted; review server logs."
    assert schedules["error"] == "Redacted; review server logs."
    assert "secret" not in str({"dependencies": dependencies, "schedules": schedules})
