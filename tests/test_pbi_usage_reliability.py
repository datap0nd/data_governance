from datetime import datetime, timedelta, timezone

import pytest

from app import database
from app import usage
from app.routers import scanner as scanner_router
from app.scanner import jobs as scanner_jobs
from app.scanner import modules as scanner_modules
from app.scanner import notifications as scanner_notifications
from app.scanner import pbi_fetch, pbi_sync
from app.scanner.lifecycle import (
    component_result,
    normalize_scan_status,
    rollup_requested_component_status,
)


class _FakeClient:
    def __init__(self, *, timeout, proxy):
        self.timeout = timeout
        self.proxy = proxy

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.fixture
def usage_fetch(monkeypatch):
    monkeypatch.setattr(
        pbi_fetch,
        "get_access_token",
        lambda: {"access_token": "safe-test-token", "account": "operator@example.test"},
    )
    monkeypatch.setattr(pbi_fetch, "_already_synced_usage_days", lambda: set())
    monkeypatch.setattr(pbi_fetch, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(pbi_fetch.httpx, "Client", _FakeClient)


def test_usage_fetch_classifies_immediate_authorization_rejection(usage_fetch, monkeypatch):
    def denied(*_args, **_kwargs):
        raise pbi_fetch.PbiFetchError(
            "Power BI API returned 403 and token=must-not-persist",
            permission=True,
            status_code=403,
        )

    monkeypatch.setattr(pbi_fetch, "_get_json", denied)

    result = pbi_fetch.fetch_usage_payload(days_back=3)

    assert result["status"] == "failed"
    assert result["reason_code"] == "power_bi_usage_authorization_denied"
    assert result["requested_days"] == 3
    assert result["successful_days"] == 0
    assert result["failed_days"] == 1
    assert result["skipped_days"] == 2
    assert "401/403" in result["diagnostic"]["operator_summary"]
    assert "tenant setting" in " ".join(result["diagnostic"]["remediation"])
    assert "must-not-persist" not in str(result)


def test_usage_fetch_reports_partial_day_failure(usage_fetch, monkeypatch):
    calls = 0

    def one_success_one_failure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise pbi_fetch.PbiFetchError("temporary upstream failure")
        return {"activityEventEntities": [], "lastResultSet": True}

    monkeypatch.setattr(pbi_fetch, "_get_json", one_success_one_failure)

    result = pbi_fetch.fetch_usage_payload(days_back=2)

    assert result["status"] == "completed_with_warnings"
    assert result["reason_code"] == "power_bi_usage_partial_failure"
    assert result["successful_days"] == 1
    assert result["failed_days"] == 1
    assert result["zero_activity_days"] == 1
    assert len(result["days_synced"]) == 1


def test_usage_fetch_fails_when_all_requested_days_fail(usage_fetch, monkeypatch):
    monkeypatch.setattr(
        pbi_fetch,
        "_get_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            pbi_fetch.PbiFetchError("temporary upstream failure")
        ),
    )

    result = pbi_fetch.fetch_usage_payload(days_back=2)

    assert result["status"] == "failed"
    assert result["reason_code"] == "power_bi_usage_all_days_failed"
    assert result["successful_days"] == 0
    assert result["failed_days"] == 2
    assert result["days_synced"] == []


def test_usage_fetch_completes_when_successful_days_have_no_activity(usage_fetch, monkeypatch):
    monkeypatch.setattr(
        pbi_fetch,
        "_get_json",
        lambda *_args, **_kwargs: {"activityEventEntities": [], "lastResultSet": True},
    )

    result = pbi_fetch.fetch_usage_payload(days_back=2)

    assert result["status"] == "completed"
    assert result["successful_days"] == 2
    assert result["zero_activity_days"] == 2
    assert result["entries"] == []


def test_usage_fetch_no_due_days_is_completed_and_already_current(usage_fetch, monkeypatch):
    today = datetime.now(timezone.utc).date()
    monkeypatch.setattr(
        pbi_fetch,
        "_already_synced_usage_days",
        lambda: {(today - timedelta(days=offset)).isoformat() for offset in (1, 2)},
    )
    monkeypatch.setattr(
        pbi_fetch,
        "_get_json",
        lambda *_args, **_kwargs: pytest.fail("No Activity Events request should be made"),
    )

    result = pbi_fetch.fetch_usage_payload(days_back=2)

    assert result["status"] == "completed"
    assert result["reason_code"] == "power_bi_usage_already_current"
    assert result["requested_days"] == 0


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    db_path = tmp_path / "usage-reliability.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def test_usage_import_rejects_attempt_with_no_successful_days(usage_db):
    result = pbi_sync.import_pbi_usage_data({
        "status": "completed",
        "entries": [{"report_name": "Should Not Import", "date": "2026-09-01"}],
        "days_synced": ["2026-09-01"],
        "requested_days": 2,
        "successful_days": 0,
        "failed_days": 2,
    })

    assert result["status"] == "failed"
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM pbi_usage_days").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM pbi_report_views").fetchone()[0] == 0
        run = db.execute(
            "SELECT status, details FROM pbi_sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run["status"] == "failed"
    assert "power_bi_usage_all_days_failed" in run["details"]


def test_usage_import_retains_partial_success_and_warning_status(usage_db):
    result = pbi_sync.import_pbi_usage_data({
        "status": "completed_with_warnings",
        "reason_code": "power_bi_usage_partial_failure",
        "operator_summary": "One day will be retried.",
        "entries": [],
        "days_synced": ["2026-09-01"],
        "requested_days": 2,
        "successful_days": 1,
        "failed_days": 1,
        "zero_activity_days": 1,
    })

    assert result["status"] == "completed_with_warnings"
    with database.get_db() as db:
        assert db.execute("SELECT date FROM pbi_usage_days").fetchone()[0] == "2026-09-01"
        assert db.execute(
            "SELECT status FROM pbi_sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == "completed_with_warnings"


def test_timeout_normalizes_to_failed_and_cannot_be_hidden_by_csv():
    assert normalize_scan_status("timeout") == "failed"
    components = {
        "csv_import": component_result({"status": "completed"}),
        "power_bi_usage": component_result({"status": "timeout"}),
    }
    assert rollup_requested_component_status(components) == "failed"


def test_usage_wait_returns_failed_timeout_with_stable_diagnostic(usage_db, monkeypatch):
    launch_id = pbi_sync._record_sync_run("usage", "launched", "started")
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(pbi_sync.time, "monotonic", lambda: next(ticks))

    result = pbi_sync.wait_for_pbi_sync_completion(
        {"status": "launched", "run_id": launch_id},
        sync_type="usage",
        timeout_seconds=1,
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "power_bi_usage_timeout"
    assert result["diagnostic"]["health_impact"] == "error"


def test_usage_wait_preserves_terminal_warning_details(usage_db):
    launch_id = pbi_sync._record_sync_run("usage", "launched", "started")
    pbi_sync._record_sync_run(
        "usage",
        "completed_with_warnings",
        "One day will be retried.",
        {
            "reason_code": "power_bi_usage_partial_failure",
            "successful_days": 1,
            "failed_days": 1,
        },
    )

    result = pbi_sync.wait_for_pbi_sync_completion(
        {"status": "launched", "run_id": launch_id},
        sync_type="usage",
        timeout_seconds=1,
    )

    assert result["status"] == "completed_with_warnings"
    assert result["reason_code"] == "power_bi_usage_partial_failure"
    assert result["successful_days"] == 1
    assert result["failed_days"] == 1
    assert pbi_sync.latest_successful_pbi_sync("usage")["status"] == "completed_with_warnings"


def test_partial_power_bi_warning_downgrades_mixed_usage_rollup():
    components = {
        "csv_import": component_result({"status": "completed"}),
        "power_bi_usage": component_result({"status": "completed_with_warnings"}),
    }
    assert rollup_requested_component_status(components) == "completed_with_warnings"


def test_authorization_diagnostic_survives_failed_component_redaction():
    diagnostic = {
        "health_impact": "error",
        "reason_code": "power_bi_usage_authorization_denied",
        "operator_summary": "Power BI rejected Activity Events access (HTTP 403).",
        "remediation": ["Assign the required tenant role."],
        "facts": {"requested_days": 2, "failed_days": 1},
    }
    result = component_result({
        "status": "failed",
        "message": "token=secret raw failure",
        "diagnostic": diagnostic,
    })

    assert result["message"] == "Redacted; review server logs."
    assert result["diagnostic"] == diagnostic


def test_usage_module_surfaces_the_power_bi_failure_explanation(usage_db, monkeypatch):
    diagnostic = {
        "health_impact": "error",
        "reason_code": "power_bi_usage_authorization_denied",
        "operator_summary": "Power BI rejected Activity Events access (HTTP 403).",
        "remediation": ["Assign the required Power BI administrator role."],
        "facts": {"requested_days": 2, "failed_days": 1},
    }
    monkeypatch.setattr(
        usage,
        "sync_usage_from_csv_if_configured",
        lambda _db: {"status": "completed", "imported": 1},
    )
    monkeypatch.setattr(pbi_sync, "service_principal_configured", lambda: True)
    monkeypatch.setattr(pbi_sync, "cached_account_available", lambda: False)
    monkeypatch.setattr(
        pbi_sync,
        "trigger_pbi_usage_sync_and_wait",
        lambda **_kwargs: {"status": "failed", "diagnostic": diagnostic},
    )
    monkeypatch.setattr(scanner_notifications, "notify_standalone_failure", lambda _id: None)
    job_id = scanner_jobs.create_job("usage_metadata")

    scanner_router._execute_usage_metadata_job(job_id, None)

    run = scanner_modules.list_module_runs("usage_metadata", limit=1)[0]
    assert run["status"] == "failed"
    assert run["summary"] == diagnostic["operator_summary"]
    assert run["details"]["diagnostic"] == diagnostic
    assert run["details"]["csv_import"]["status"] == "completed"
    assert run["details"]["power_bi_usage"]["status"] == "failed"


def test_powershell_usage_path_tracks_the_same_outcomes():
    script = pbi_sync.PS1_USAGE_SCRIPT.read_text(encoding="utf-8")
    assert "New-UsageDiagnostic" in script
    assert 'power_bi_usage_authorization_denied' in script
    assert 'power_bi_usage_all_days_failed' in script
    assert 'power_bi_usage_partial_failure' in script
    assert 'power_bi_usage_already_current' in script
    assert 'if ($syncedDaysList.Count -eq 0 -and $failedDays -gt 0)' in script
    assert 'successful_days' in script
    assert 'zero_activity_days' in script
