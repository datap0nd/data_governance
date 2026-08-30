"""Automatic main updater: exact-SHA reservation and restart hand-off."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading

import pytest
from fastapi.testclient import TestClient

from app import database, main, settings


DEPLOYED = "a" * 40
TARGET = "b" * 40
OTHER_TARGET = "c" * 40
REAL_TESTS_GATE = main._tests_gate


@pytest.fixture(autouse=True)
def _disable_host_proxy_discovery(monkeypatch):
    monkeypatch.setattr(main, "resolve_proxy", lambda _url: None)


def _passed_tests_gate(target, *, force=False):
    return {
        "workflow": "Tests",
        "workflow_file": "tests.yml",
        "workflow_path": ".github/workflows/tests.yml",
        "target_commit": target,
        "state": "passed",
        "status": "completed",
        "conclusion": "success",
        "run_id": 123,
        "run_attempt": 1,
        "url": "https://github.example/actions/runs/123",
        "checked_at": "2026-08-28T12:00:00+00:00",
        "message": "The exact main commit passed the Tests workflow.",
        "error": None,
    }


def _workflow_run(
    target=TARGET,
    *,
    status="completed",
    conclusion="success",
    event="push",
    branch="main",
    path=".github/workflows/tests.yml@main",
    run_id=123,
    run_number=12,
    run_attempt=1,
):
    return {
        "id": run_id,
        "run_number": run_number,
        "run_attempt": run_attempt,
        "head_sha": target,
        "head_branch": branch,
        "event": event,
        "path": path,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.example/actions/runs/{run_id}",
    }


@pytest.fixture
def update_env(monkeypatch):
    # Avoid pytest's Windows ``current`` directory link, which is unavailable
    # on locked-down office machines with symlink evaluation disabled.
    with tempfile.TemporaryDirectory(
        prefix="metronome-auto-update-", dir=Path.cwd()
    ) as temporary:
        temp_path = Path(temporary)
        db_path = str(temp_path / "auto-update.db")
        monkeypatch.setattr(database, "DB_PATH", db_path)
        monkeypatch.setattr(settings, "DB_PATH", db_path)
        monkeypatch.setattr(main, "DB_PATH", db_path)
        database.init_db()

        install_dir = temp_path / "installed-app"
        install_dir.mkdir()
        monkeypatch.setattr(main, "_CODE_DIR", install_dir)
        update_root = temp_path / "updates"
        monkeypatch.setattr(main, "_UPDATE_ROOT", update_root)
        monkeypatch.setattr(main, "_UPDATE_REQUEST_PATH", update_root / "pending_update.json")
        monkeypatch.setattr(main, "_UPDATE_RECEIPT_DIR", update_root / "receipts")
        monkeypatch.setattr(main, "_APP_VERSION", f"20260828-120000-{DEPLOYED[:9]}")
        monkeypatch.setattr(main, "_AUTO_UPDATE_RUN_LOCK", threading.Lock())
        monkeypatch.setattr(main, "_AUTO_UPDATE_STATE_LOCK", threading.Lock())
        monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVITY_LOCK", threading.Lock())
        monkeypatch.setattr(main, "_TESTS_GATE_LOCK", threading.Lock())
        monkeypatch.setattr(main, "_TESTS_GATE_CACHE", {})
        monkeypatch.setattr(main, "_tests_gate", _passed_tests_gate)
        monkeypatch.setattr(main, "_AUTO_UPDATE_DRAIN_EVENT", threading.Event())
        monkeypatch.setattr(main, "_AUTO_UPDATE_ACTIVE_STARTS", 0)
        monkeypatch.setattr(
            main,
            "_AUTO_UPDATE_STATE",
            {
                "status": "starting",
                "last_checked_at": None,
                "last_attempt_at": None,
                "last_attempt_commit": None,
                "last_attempt_monotonic": 0.0,
                "last_error": None,
            },
        )
        main._UPDATE_CHECK.update(
            {"checked_at": 0.0, "latest_commit": None, "error": None}
        )
        settings.set_setting(main._AUTO_UPDATE_SETTING_KEY, "1")
        yield {
            "db_path": db_path,
            "code_dir": install_dir,
            "root": update_root,
            "request": update_root / "pending_update.json",
            "receipts": update_root / "receipts",
        }


def _attempt_rows():
    with database.get_db() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM app_update_attempts ORDER BY id"
            ).fetchall()
        ]


def test_disabled_check_does_not_contact_github_or_launch(update_env, monkeypatch):
    settings.set_setting(main._AUTO_UPDATE_SETTING_KEY, "0")
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: pytest.fail("disabled updater contacted GitHub"),
    )
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda *_args, **_kwargs: pytest.fail("disabled updater launched"),
    )

    result = main._scheduled_auto_update(force=True)

    assert result["enabled"] is False
    assert result["status"] == "disabled"
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    assert _attempt_rows() == []


def test_git_checkout_is_never_automatically_overwritten(update_env, monkeypatch):
    (update_env["code_dir"] / ".git").mkdir()
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: pytest.fail("developer checkout contacted GitHub"),
    )
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda *_args, **_kwargs: pytest.fail("developer checkout launched updater"),
    )

    result = main._scheduled_auto_update(force=True)
    ready, error = main._registered_auto_update_task_ready()

    assert result["status"] == "developer_checkout"
    assert result["draining"] is False
    assert ready is False
    assert "Git working copy" in error
    assert _attempt_rows() == []


def test_up_to_date_check_does_not_reserve_or_launch(update_env, monkeypatch):
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (DEPLOYED, None))
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda *_args, **_kwargs: pytest.fail("up-to-date updater launched"),
    )

    result = main._scheduled_auto_update(force=True)

    assert result["status"] == "up_to_date"
    assert result["update_available"] is False
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    assert _attempt_rows() == []


def test_new_commit_launches_setup_bridge_even_while_work_is_active(
    update_env, monkeypatch
):
    with database.get_db() as db:
        db.execute(
            """INSERT INTO scanner_jobs(job_type, trigger_source, status)
               VALUES ('full_scan', 'manual', 'running')"""
        )
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    calls = []
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: calls.append((target, source))
        or {
            "attempt_id": "attempt-while-busy",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )

    result = main._scheduled_auto_update(force=True)

    assert result["status"] == "update_launched"
    assert calls == [(TARGET, "automatic")]
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()


def test_nonterminal_pipeline_stage_is_busy(update_env):
    with database.get_db() as db:
        report_id = db.execute(
            "INSERT INTO reports(name) VALUES ('Updater pipeline')"
        ).lastrowid
        db.execute(
            """INSERT INTO pipeline_runs
                   (report_id, status, stage, plan_hash, plan_json)
               VALUES (?, 'refreshing_powerbi', 'refreshing_powerbi', 'hash', '{}')""",
            (report_id,),
        )
        active = main._active_update_work(db)

    assert active["pipeline_runs"] == 1


def test_deferred_pbi_retry_is_not_active_and_does_not_start_during_drain(
    update_env, monkeypatch
):
    from app.scanner import pbi_sync

    with database.get_db() as db:
        db.execute(
            """INSERT INTO pbi_sync_runs(sync_type, status, message)
               VALUES ('refresh', 'pending', 'Waiting for desktop')"""
        )
        assert "pbi_sync_runs" not in main._active_update_work(db)

    monkeypatch.setattr(
        pbi_sync,
        "retry_pending_pbi_sync",
        lambda: pytest.fail("deferred Power BI sync started during drain"),
    )
    main._request_update_drain()

    result = main._scheduled_pending_pbi_sync_retry()

    assert result == {"status": "update_draining"}
    assert main._AUTO_UPDATE_ACTIVE_STARTS == 0


def test_idle_check_reserves_exact_latest_commit(update_env, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))

    def reserve(target, source):
        calls.append((target, source))
        return {
            "attempt_id": "attempt-1",
            "target_commit": target,
            "status": "launched",
            "active": True,
        }

    monkeypatch.setattr(main, "_reserve_and_launch_update", reserve)

    result = main._scheduled_auto_update(force=True)

    assert calls == [(TARGET, "automatic")]
    assert result["status"] == "update_launched"
    assert result["last_attempt_commit"] == TARGET
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()


def test_scheduled_update_does_not_wait_for_a_tests_gate(update_env, monkeypatch):
    latest_calls = []

    def latest(*, force=False):
        latest_calls.append(force)
        return TARGET, None

    monkeypatch.setattr(main, "_latest_commit", latest)
    monkeypatch.setattr(
        main,
        "_tests_gate",
        lambda *_args, **_kwargs: pytest.fail("automatic setup launch checked CI"),
    )
    calls = []
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: calls.append((target, source))
        or {
            "attempt_id": "direct-setup",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )
    main._AUTO_UPDATE_DRAIN_EVENT.set()

    result = main._scheduled_auto_update(force=True)

    assert result["status"] == "update_launched"
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    assert latest_calls == [True]
    assert calls == [(TARGET, "automatic")]


def test_scheduled_update_launches_the_single_detected_exact_commit(
    update_env, monkeypatch
):
    calls = []
    latest_calls = []
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: latest_calls.append(True) or (TARGET, None),
    )
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: calls.append((target, source))
        or {
            "attempt_id": "single-detection",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )

    result = main._scheduled_auto_update(force=True)

    assert result["status"] == "update_launched"
    assert result["latest_commit"] == TARGET
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    assert latest_calls == [True]
    assert calls == [(TARGET, "automatic")]


def test_scheduled_launch_failure_releases_drain(update_env, monkeypatch):
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))

    def fail_reservation(*_args, **_kwargs):
        raise RuntimeError("updater task could not launch")

    monkeypatch.setattr(main, "_reserve_and_launch_update", fail_reservation)

    result = main._scheduled_auto_update(force=True)

    assert result["status"] == "launch_failed"
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()


def test_concurrent_reservations_create_one_attempt_request_and_launch(
    update_env, monkeypatch
):
    launches = []
    launch_lock = threading.Lock()

    def launch():
        with launch_lock:
            launches.append(True)

    monkeypatch.setattr(main, "_launch_registered_auto_update_task", launch)

    with ThreadPoolExecutor(max_workers=8) as executor:
        attempts = list(
            executor.map(
                lambda _index: main._reserve_and_launch_update(TARGET, "automatic"),
                range(8),
            )
        )

    assert len({attempt["attempt_id"] for attempt in attempts}) == 1
    assert len(_attempt_rows()) == 1
    assert launches == [True]
    request = json.loads(update_env["request"].read_text(encoding="utf-8"))
    assert request["attempt_id"] == attempts[0]["attempt_id"]
    assert request["target_commit"] == TARGET
    assert request["from_commit"] == DEPLOYED[:9]
    assert request["database_path"] == update_env["db_path"]


def test_existing_active_reservation_wins_over_a_different_target(
    update_env, monkeypatch
):
    launches = []
    monkeypatch.setattr(
        main, "_launch_registered_auto_update_task", lambda: launches.append(True)
    )

    first = main._reserve_and_launch_update(TARGET, "automatic")
    second = main._reserve_and_launch_update(OTHER_TARGET, "manual")

    assert second["attempt_id"] == first["attempt_id"]
    assert second["target_commit"] == TARGET
    assert second["trigger_source"] == "automatic"
    assert launches == [True]
    assert json.loads(update_env["request"].read_text())["target_commit"] == TARGET


def test_launch_failure_is_terminal_and_clears_request(update_env, monkeypatch):
    def fail_launch():
        raise RuntimeError("scheduled task rejected token=top-secret")

    monkeypatch.setenv("DG_GITHUB_TOKEN", "top-secret")
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", fail_launch)

    with pytest.raises(RuntimeError, match="scheduled task rejected"):
        main._reserve_and_launch_update(TARGET, "automatic")

    [attempt] = _attempt_rows()
    assert attempt["status"] == "failed"
    assert attempt["stage"] == "launch_failed"
    assert attempt["active_slot"] is None
    assert attempt["finished_at"]
    assert "top-secret" not in attempt["error"]
    assert "[redacted]" in attempt["error"]
    assert not update_env["request"].exists()


def test_success_receipt_completes_active_attempt_after_restart(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", lambda: None)
    attempt = main._reserve_and_launch_update(TARGET, "automatic")
    receipt_path = update_env["receipts"] / f"{attempt['attempt_id']}.json"
    receipt_path.write_text(
        json.dumps(
            {
                "attempt_id": attempt["attempt_id"],
                "target_commit": TARGET,
                "status": "succeeded",
                "message": "Service and worker healthy.",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "_APP_VERSION", f"20260828-130000-{TARGET[:9]}")

    reconciled = main._reconcile_update_attempts()

    assert reconciled["status"] == "succeeded"
    assert reconciled["stage"] == "healthy"
    assert reconciled["active"] is False
    assert reconciled["finished_at"]
    active, latest = main._latest_update_attempts()
    assert active is None
    assert latest["attempt_id"] == attempt["attempt_id"]


def test_active_attempt_precedes_github_and_up_to_date_branch(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", lambda: None)
    attempt = main._reserve_and_launch_update(TARGET, "automatic")
    monkeypatch.setattr(main, "_APP_VERSION", f"20260828-130000-{TARGET[:9]}")
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: pytest.fail("active attempt unnecessarily queried GitHub"),
    )
    monkeypatch.setattr(
        main,
        "_tests_gate",
        lambda *_args, **_kwargs: pytest.fail(
            "active attempt unnecessarily re-checked Tests"
        ),
    )

    result = main._scheduled_auto_update(force=True)

    assert result["status"] == "waiting_for_update_restart"
    assert result["last_attempt_commit"] == TARGET
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    active, _latest = main._latest_update_attempts()
    assert active["attempt_id"] == attempt["attempt_id"]


def test_disabling_keeps_an_already_launched_attempt_without_a_drain(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", lambda: None)
    attempt = main._reserve_and_launch_update(TARGET, "automatic")
    settings.set_setting(main._AUTO_UPDATE_SETTING_KEY, "0")
    main._AUTO_UPDATE_DRAIN_EVENT.clear()
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: pytest.fail("active disabled attempt queried GitHub"),
    )

    result = main._scheduled_auto_update(force=True)

    assert result["enabled"] is False
    assert result["status"] == "waiting_for_update_restart"
    assert result["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    assert result["last_attempt_commit"] == TARGET
    active, _latest = main._latest_update_attempts()
    assert active["attempt_id"] == attempt["attempt_id"]


def test_lifespan_reconciles_and_drains_active_update_before_runtime_start(
    update_env, monkeypatch
):
    from app.ai import operations_agent, runtime_config
    from app.scanner import jobs

    calls = []

    class Scheduler:
        running = False

        def start(self):
            calls.append("scheduler_start")
            self.running = True

        def shutdown(self, wait=False):
            calls.append("scheduler_shutdown")
            self.running = False

    active_attempt = {
        "attempt_id": "startup-attempt",
        "target_commit": TARGET,
        "status": "verifying",
        "active": True,
    }
    monkeypatch.setattr(main, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        main,
        "_reconcile_update_attempts",
        lambda: calls.append("reconcile_update") or active_attempt,
    )

    def request_drain():
        calls.append("request_drain")
        main._AUTO_UPDATE_DRAIN_EVENT.set()

    monkeypatch.setattr(main, "_request_update_drain", request_drain)
    monkeypatch.setattr(
        runtime_config,
        "initialize_runtime_settings",
        lambda: calls.append("initialize_ai")
        or SimpleNamespace(
            mode="mock",
            model="test",
            qwen_enabled=False,
            feature_enabled=lambda _name: False,
        ),
    )
    monkeypatch.setattr(jobs, "recover_interrupted_jobs", lambda: 0)
    monkeypatch.setattr(main, "_recover_startup_pbi_syncs", lambda: 0)
    monkeypatch.setattr(main, "_recover_startup_scan_runs", lambda: 0)
    monkeypatch.setattr(
        main,
        "_reconcile_startup_flow_targets",
        lambda: {
            "total": 0,
            "changed": 0,
            "confirmed": 0,
            "ambiguous": 0,
            "unresolved": 0,
        },
    )
    monkeypatch.setattr(
        main, "_configure_scheduler_jobs", lambda: calls.append("configure_jobs") or {"hour": 6, "minute": 0}
    )
    monkeypatch.setattr(main, "_scheduler", Scheduler())
    monkeypatch.setattr(main.flows, "ensure_local_worker", lambda: calls.append("worker_start"))
    monkeypatch.setattr(main.pipelines, "pipeline_tick", lambda: calls.append("pipeline_tick"))
    monkeypatch.setattr(operations_agent, "recover_and_start", lambda: 0)
    monkeypatch.setattr(main, "_scheduled_alert_ai_enrichment", lambda: None)
    monkeypatch.setattr(main.scanner, "shutdown_scanner_executor", lambda: None)
    monkeypatch.setattr(operations_agent, "shutdown_executor", lambda: None)
    monkeypatch.setattr(main.pipelines, "shutdown_pipeline_executor", lambda: None)

    async def run_lifespan():
        async with main.lifespan(main.app):
            assert main._AUTO_UPDATE_DRAIN_EVENT.is_set()

    asyncio.run(run_lifespan())

    assert calls.index("init_db") < calls.index("reconcile_update")
    assert calls.index("reconcile_update") < calls.index("request_drain")
    assert calls.index("request_drain") < calls.index("initialize_ai")
    assert calls.index("request_drain") < calls.index("scheduler_start")


def test_lifespan_keeps_web_available_when_optional_startup_repairs_fail(
    update_env, monkeypatch
):
    from app.ai import operations_agent, runtime_config
    from app.scanner import jobs

    entered = []

    def fail(label):
        def run():
            raise RuntimeError(label)

        return run

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "_reconcile_update_attempts", fail("updates"))
    monkeypatch.setattr(
        runtime_config,
        "initialize_runtime_settings",
        lambda: SimpleNamespace(
            mode="mock",
            model="test",
            qwen_enabled=False,
            feature_enabled=lambda _name: False,
        ),
    )
    monkeypatch.setattr(jobs, "recover_interrupted_jobs", fail("scanner recovery"))
    monkeypatch.setattr(main, "_recover_startup_pbi_syncs", fail("pbi recovery"))
    monkeypatch.setattr(main, "_recover_startup_scan_runs", fail("scan recovery"))
    monkeypatch.setattr(main, "_reconcile_startup_flow_targets", fail("flow targets"))
    monkeypatch.setattr(main, "_configure_scheduler_jobs", fail("scheduler config"))
    monkeypatch.setattr(main.flows, "ensure_local_worker", fail("worker"))
    monkeypatch.setattr(main.pipelines, "pipeline_tick", fail("pipelines"))
    monkeypatch.setattr(operations_agent, "recover_and_start", fail("ai recovery"))
    monkeypatch.setattr(main, "_scheduled_alert_ai_enrichment", fail("alert ai"))
    monkeypatch.setattr(main.scanner, "shutdown_scanner_executor", lambda: None)
    monkeypatch.setattr(operations_agent, "shutdown_executor", lambda: None)
    monkeypatch.setattr(main.pipelines, "shutdown_pipeline_executor", lambda: None)

    async def run_lifespan():
        async with main.lifespan(main.app):
            entered.append(True)

    asyncio.run(run_lifespan())

    assert entered == [True]


def test_failed_receipt_releases_reservation_and_redacts_error(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", lambda: None)
    monkeypatch.setenv("DG_GITHUB_TOKEN", "receipt-secret")
    attempt = main._reserve_and_launch_update(TARGET, "manual")
    receipt_path = update_env["receipts"] / f"{attempt['attempt_id']}.json"
    receipt_path.write_text(
        json.dumps(
            {
                "attempt_id": attempt["attempt_id"],
                "target_commit": TARGET,
                "status": "rolled_back",
                "message": "Old release restored.",
                "error": "token=receipt-secret failed health check",
            }
        ),
        encoding="utf-8",
    )

    reconciled = main._reconcile_update_attempts()

    assert reconciled["status"] == "failed"
    assert reconciled["stage"] == "rolled_back"
    assert reconciled["active"] is False
    assert "receipt-secret" not in reconciled["error"]
    assert "[redacted]" in reconciled["error"]


def test_receipt_for_a_different_commit_fails_closed(update_env, monkeypatch):
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", lambda: None)
    attempt = main._reserve_and_launch_update(TARGET, "automatic")
    receipt_path = update_env["receipts"] / f"{attempt['attempt_id']}.json"
    receipt_path.write_text(
        json.dumps(
            {
                "attempt_id": attempt["attempt_id"],
                "target_commit": OTHER_TARGET,
                "status": "succeeded",
                "message": "Wrong update claimed success.",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "_APP_VERSION", f"20260828-130000-{TARGET[:9]}")

    reconciled = main._reconcile_update_attempts()

    assert reconciled["status"] == "failed"
    assert reconciled["active"] is False
    assert "target did not match" in reconciled["error"]


def test_unlaunched_reservation_times_out_after_ten_minutes(update_env):
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO app_update_attempts
                   (attempt_id, from_commit, target_commit, trigger_source,
                    status, stage, active_slot, created_at, updated_at)
               VALUES ('reserved-crash', ?, ?, 'automatic',
                       'reserved', 'preparing', 1, ?, ?)""",
            (DEPLOYED[:9], TARGET, old, old),
        )

    reconciled = main._reconcile_update_attempts()

    assert reconciled["status"] == "failed"
    assert reconciled["stage"] == "timed_out"
    assert reconciled["active"] is False


def test_launched_attempt_gets_full_health_verification_timeout(update_env):
    eleven_minutes_ago = (
        datetime.now(timezone.utc) - timedelta(minutes=11)
    ).isoformat()
    with database.get_db() as db:
        db.execute(
            """INSERT INTO app_update_attempts
                   (attempt_id, from_commit, target_commit, trigger_source,
                    status, stage, active_slot, created_at, launched_at, updated_at)
               VALUES ('launched-slow', ?, ?, 'automatic',
                       'launched', 'external_task', 1, ?, ?, ?)""",
            (
                DEPLOYED[:9],
                TARGET,
                eleven_minutes_ago,
                eleven_minutes_ago,
                eleven_minutes_ago,
            ),
        )

    still_active = main._reconcile_update_attempts()
    assert still_active["status"] == "launched"
    assert still_active["active"] is True

    three_hours_ago = (
        datetime.now(timezone.utc) - timedelta(hours=3)
    ).isoformat()
    with database.get_db() as db:
        db.execute(
            """UPDATE app_update_attempts
                  SET created_at=?, launched_at=?, updated_at=?
                WHERE attempt_id='launched-slow'""",
            (three_hours_ago, three_hours_ago, three_hours_ago),
        )

    timed_out = main._reconcile_update_attempts()
    assert timed_out["status"] == "failed"
    assert timed_out["stage"] == "timed_out"
    assert timed_out["active"] is False


def test_numeric_hex_deployed_commit_is_not_mistaken_for_a_timestamp(monkeypatch):
    # setup.ps1 stamps a nine-character SHA prefix. A valid prefix has a
    # material chance of containing digits only and must remain recognizable.
    monkeypatch.setattr(main, "_APP_VERSION", "20260828-130000-012345678")

    assert main._deployed_commit() == "012345678"


@pytest.mark.parametrize(
    "target",
    [
        "",
        "2" * 39,
        "2" * 41,
        "g" * 40,
        "2" * 39 + "/",
        "main",
        "refs/heads/main",
    ],
)
def test_reservation_rejects_anything_except_a_full_hex_sha(
    update_env, monkeypatch, target
):
    monkeypatch.setattr(
        main,
        "_launch_registered_auto_update_task",
        lambda: pytest.fail("invalid SHA reached task launcher"),
    )

    with pytest.raises(RuntimeError, match="full Git commit"):
        main._reserve_and_launch_update(target, "automatic")

    assert _attempt_rows() == []
    assert not update_env["request"].exists()


def test_github_commit_response_requires_a_full_hex_sha(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sha": "abcdef0123456789"}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())

    with pytest.raises(RuntimeError, match="invalid main commit"):
        main._fetch_latest_commit()


def test_github_lookup_uses_windows_network_fallback(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("office proxy required")
        ),
    )
    monkeypatch.setattr(main, "_windows_github_fallback_available", lambda: True)
    observed = []
    monkeypatch.setattr(
        main,
        "_github_api_json_via_powershell",
        lambda url: observed.append(url) or {"sha": TARGET},
    )

    assert main._fetch_latest_commit() == TARGET
    assert observed == [main._LATEST_COMMIT_URL]


def test_github_lookup_passes_resolved_office_proxy(monkeypatch):
    import httpx

    observed = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sha": TARGET}

    def proxy(url):
        observed["proxy_target"] = url
        return "http://office-proxy:8080"

    monkeypatch.setattr(main, "resolve_proxy", proxy)

    def get(_url, **kwargs):
        observed.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, "get", get)

    assert main._fetch_latest_commit() == TARGET
    assert observed["proxy"] == "http://office-proxy:8080"
    assert observed["proxy_target"] == main._LATEST_COMMIT_URL
    assert observed["timeout"] == 20


def test_tests_workflow_gate_accepts_only_the_exact_main_push(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "workflow_runs": [
                    _workflow_run(OTHER_TARGET, run_id=999, run_number=99),
                    _workflow_run(
                        TARGET,
                        event="pull_request",
                        run_id=998,
                        run_number=98,
                    ),
                    _workflow_run(
                        TARGET,
                        path=".github/workflows/other.yml@main",
                        run_id=997,
                        run_number=97,
                    ),
                    _workflow_run(TARGET),
                ]
            }

    def get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "get", get)

    result = main._fetch_tests_workflow_gate(TARGET)

    assert result["state"] == "passed"
    assert result["target_commit"] == TARGET
    assert result["run_id"] == 123
    assert captured["url"].endswith("/actions/workflows/tests.yml/runs")
    assert captured["params"] == {
        "head_sha": TARGET,
        "branch": "main",
        "event": "push",
        "per_page": 10,
    }


@pytest.mark.parametrize(
    ("workflow_status", "conclusion", "expected_state"),
    [
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "failed"),
        ("completed", "skipped", "failed"),
    ],
)
def test_tests_workflow_gate_fails_closed_until_success(
    monkeypatch, workflow_status, conclusion, expected_state
):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "workflow_runs": [
                    _workflow_run(
                        status=workflow_status,
                        conclusion=conclusion,
                    )
                ]
            }

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())

    result = main._fetch_tests_workflow_gate(TARGET)

    assert result["state"] == expected_state
    assert result["status"] == workflow_status
    assert result["conclusion"] == conclusion


def test_tests_workflow_gate_treats_wrong_or_missing_runs_as_pending(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "workflow_runs": [
                    _workflow_run(OTHER_TARGET),
                    _workflow_run(TARGET, branch="feature"),
                ]
            }

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())

    result = main._fetch_tests_workflow_gate(TARGET)

    assert result["state"] == "pending"
    assert result["run_id"] is None


def test_tests_gate_cache_is_short_lived_and_keyed_by_exact_sha(
    update_env, monkeypatch
):
    calls = []

    def fetch(target):
        calls.append(target)
        return _passed_tests_gate(target)

    monkeypatch.setattr(main, "_fetch_tests_workflow_gate", fetch)

    first = REAL_TESTS_GATE(TARGET)
    second = REAL_TESTS_GATE(TARGET)
    other = REAL_TESTS_GATE(OTHER_TARGET)
    forced = REAL_TESTS_GATE(TARGET, force=True)

    assert first["state"] == second["state"] == other["state"] == "passed"
    assert forced["state"] == "passed"
    assert calls == [TARGET, OTHER_TARGET, TARGET]


def test_tests_gate_api_failure_is_unavailable_and_redacts_token(
    update_env, monkeypatch
):
    monkeypatch.setenv("DG_GITHUB_TOKEN", "ci-secret")
    monkeypatch.setattr(
        main,
        "_fetch_tests_workflow_gate",
        lambda _target: (_ for _ in ()).throw(
            RuntimeError("authorization=ci-secret was rejected")
        ),
    )

    result = REAL_TESTS_GATE(TARGET, force=True)

    assert result["state"] == "unavailable"
    assert "ci-secret" not in result["error"]
    assert "[redacted]" in result["error"]


def test_tests_gate_reuses_prior_exact_sha_pass_during_network_timeout(
    update_env, monkeypatch
):
    calls = 0

    def fetch(target):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _passed_tests_gate(target)
        raise RuntimeError("SSL handshake operation timed out")

    monkeypatch.setattr(main, "_fetch_tests_workflow_gate", fetch)

    first = REAL_TESTS_GATE(TARGET, force=True)
    second = REAL_TESTS_GATE(TARGET, force=True)

    assert first["state"] == second["state"] == "passed"
    assert second["target_commit"] == TARGET
    assert second["run_id"] == first["run_id"]
    assert "previously passed" in second["message"]
    assert "retry automatically" in second["verification_warning"]


def test_advisory_agent_queue_does_not_block_production_update(update_env):
    with database.get_db() as db:
        db.execute(
            """INSERT INTO agent_runs
                   (question, focus_type, focus_id, status, actor, model,
                    provider_mode, config_fingerprint, prompt_version)
               VALUES ('queued advisory', 'alert', '1', 'queued', 'System',
                       'local-model', 'local', 'fingerprint', 'v1'),
                      ('running advisory', 'alert', '2', 'running', 'System',
                       'local-model', 'local', 'fingerprint', 'v1')"""
        )
        active = main._active_update_work(db)

    assert "agent_runs" not in active


def test_update_timeout_is_operator_readable():
    raw = (
        "RuntimeError: GitHub request failed: _ssl.c:1011: The handshake "
        "operation timed out; Invoke-RestMethod: The operation has timed out. "
        "CategoryInfo InvalidOperation WebException"
    )

    result = main._safe_update_error(raw)

    assert result == (
        "GitHub did not answer before the office network timeout. "
        "Metronome will retry automatically; no test or installation failed."
    )


def test_system_updates_get_exposes_readiness_and_apply_gate(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    client = TestClient(main.app)

    response = client.get("/api/system/updates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_commit"] == DEPLOYED[:9]
    assert payload["latest_commit"] == TARGET
    assert payload["updater_ready"] is True
    assert payload["active_work"] == {}
    assert payload["tests_gate"]["state"] == "passed"
    assert payload["tests_gate"]["target_commit"] == TARGET
    assert payload["can_apply"] is True
    assert payload["auto_update"]["enabled"] is True


def test_system_updates_tests_are_informational_for_the_latest_sha(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    pending = _passed_tests_gate(TARGET)
    pending.update(
        state="pending",
        status="queued",
        conclusion=None,
        message="Tests are queued.",
    )
    monkeypatch.setattr(main, "_tests_gate", lambda *_args, **_kwargs: pending)
    client = TestClient(main.app)

    response = client.get("/api/system/updates")

    assert response.status_code == 200
    assert response.json()["tests_gate"]["state"] == "pending"
    assert response.json()["can_apply"] is True


def test_system_updates_cannot_apply_a_cached_sha_when_main_check_failed(
    update_env, monkeypatch
):
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: (TARGET, "GitHub request failed"),
    )
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    monkeypatch.setattr(
        main,
        "_tests_gate",
        lambda *_args, **_kwargs: pytest.fail(
            "stale latest SHA should not be CI-gated after a main lookup failure"
        ),
    )
    client = TestClient(main.app)

    response = client.get("/api/system/updates")

    assert response.status_code == 200
    assert response.json()["tests_gate"]["state"] == "not_checked"
    assert response.json()["can_apply"] is False


def test_system_updates_put_persists_enabled_setting(update_env, monkeypatch):
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    client = TestClient(main.app)
    main._AUTO_UPDATE_DRAIN_EVENT.set()

    response = client.put("/api/system/updates", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["auto_update"]["enabled"] is False
    assert response.json()["auto_update"]["draining"] is False
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()
    assert settings.get_setting(main._AUTO_UPDATE_SETTING_KEY) == "0"


def test_check_api_forces_refresh_without_applying(update_env, monkeypatch):
    force_values = []
    gate_force_values = []

    def latest(*, force=False):
        force_values.append(force)
        return TARGET, None

    monkeypatch.setattr(main, "_latest_commit", latest)
    monkeypatch.setattr(
        main,
        "_tests_gate",
        lambda target, *, force=False: gate_force_values.append((target, force))
        or _passed_tests_gate(target, force=force),
    )
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda *_args, **_kwargs: pytest.fail("check-only API applied update"),
    )
    client = TestClient(main.app)

    response = client.post("/api/system/updates/check")

    assert response.status_code == 200
    assert force_values[0] is True
    # The response still reports CI as informational status, but the update
    # check itself no longer waits on or force-refreshes that gate.
    assert gate_force_values[0] == (TARGET, False)
    assert response.json()["auto_update"]["status"] == "update_available"
    assert response.json()["can_apply"] is True


@pytest.mark.parametrize(
    "path", ["/api/system/updates/apply", "/api/update"]
)
def test_apply_apis_launch_the_server_selected_exact_sha(
    update_env, monkeypatch, path
):
    calls = []
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )

    def reserve(target, source):
        calls.append((target, source))
        return {
            "attempt_id": "manual-attempt",
            "target_commit": target,
            "trigger_source": source,
            "status": "launched",
            "launched_at": "2026-08-28T13:00:00+00:00",
            "active": True,
        }

    monkeypatch.setattr(main, "_reserve_and_launch_update", reserve)
    client = TestClient(main.app)

    response = client.post(path, json={"target_commit": OTHER_TARGET})

    assert response.status_code == 202
    assert response.json()["status"] == "launched"
    assert response.json()["latest_commit"] == TARGET
    assert calls == [(TARGET, "manual")]


def test_apply_api_launches_setup_without_waiting_for_tests(update_env, monkeypatch):
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main,
        "_tests_gate",
        lambda *_args, **_kwargs: pytest.fail("manual setup launch checked CI"),
    )
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    calls = []
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: calls.append((target, source))
        or {
            "attempt_id": "manual-direct-setup",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    client = TestClient(main.app)

    response = client.post("/api/system/updates/apply")

    assert response.status_code == 202
    assert response.json()["status"] == "launched"
    assert calls == [(TARGET, "manual")]
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()


def test_apply_api_launches_the_single_detected_exact_sha(
    update_env, monkeypatch
):
    calls = []
    latest_calls = []
    monkeypatch.setattr(
        main,
        "_latest_commit",
        lambda **_kwargs: latest_calls.append(True) or (TARGET, None),
    )
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: calls.append((target, source))
        or {
            "attempt_id": "single-manual-detection",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )
    client = TestClient(main.app)

    response = client.post("/api/system/updates/apply")

    assert response.status_code == 202
    assert response.json()["latest_commit"] == TARGET
    assert latest_calls == [True]
    assert calls == [(TARGET, "manual")]
    assert not main._AUTO_UPDATE_DRAIN_EVENT.is_set()


def test_apply_api_launches_setup_while_work_is_active(
    update_env, monkeypatch
):
    with database.get_db() as db:
        db.execute(
            """INSERT INTO scanner_jobs(job_type, trigger_source, status)
               VALUES ('lineage', 'manual', 'running')"""
        )
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    calls = []
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: calls.append((target, source))
        or {
            "attempt_id": "busy-manual",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )
    client = TestClient(main.app)

    response = client.post("/api/system/updates/apply")

    assert response.status_code == 202
    assert calls == [(TARGET, "manual")]


def test_apply_api_returns_existing_active_attempt_without_second_launch(
    update_env, monkeypatch
):
    monkeypatch.setattr(main, "_launch_registered_auto_update_task", lambda: None)
    active = main._reserve_and_launch_update(TARGET, "automatic")
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    monkeypatch.setattr(
        main, "_registered_auto_update_task_ready", lambda: (True, None)
    )
    client = TestClient(main.app)

    response = client.post("/api/system/updates/apply")

    assert response.status_code == 409
    assert response.json()["detail"]["attempt"]["attempt_id"] == active["attempt_id"]


def test_update_drain_middleware_blocks_new_work_but_not_worker_progress(
    update_env,
):
    main._AUTO_UPDATE_DRAIN_EVENT.set()
    client = TestClient(main.app)

    start = client.post("/api/flows/999/run")
    progress = client.post(
        "/api/flows/worker/test-worker/runs/999/progress",
        json={},
    )

    assert start.status_code == 503
    assert start.headers["retry-after"] == "60"
    assert "finishing active work" in start.json()["detail"]
    # Progress/completion routes deliberately remain open so work already in
    # flight can drain rather than being stranded by the maintenance barrier.
    assert progress.status_code != 503


def test_auto_update_launches_setup_while_a_production_request_is_running(
    update_env, monkeypatch
):
    from app.routers import data_quality

    started = threading.Event()
    release = threading.Event()

    def blocked_quality_run():
        started.set()
        assert release.wait(10), "test did not release the data-quality request"
        return {"status": "completed"}

    monkeypatch.setattr(data_quality, "run_quality_checks", blocked_quality_run)
    monkeypatch.setattr(main, "_latest_commit", lambda **_kwargs: (TARGET, None))
    launches = []
    monkeypatch.setattr(
        main,
        "_reserve_and_launch_update",
        lambda target, source: launches.append((target, source))
        or {
            "attempt_id": "after-drain",
            "target_commit": target,
            "status": "launched",
            "active": True,
        },
    )
    client = TestClient(main.app)

    with ThreadPoolExecutor(max_workers=1) as executor:
        request_future = executor.submit(client.post, "/api/data-quality/run")
        assert started.wait(10), "production request never reached its endpoint"

        launched = main._scheduled_auto_update(force=True)

        assert launched["status"] == "update_launched"
        assert launches == [(TARGET, "automatic")]
        release.set()
        assert request_future.result(timeout=10).status_code == 200
