import sqlite3
import threading
import uuid
from types import SimpleNamespace

import pytest

from app import database, main
from app.scanner import control
from app.scanner import jobs as scanner_jobs
from app.scanner import pbi_fetch, pbi_sync


@pytest.fixture
def pbi_db(tmp_path, monkeypatch):
    control.clear_pbi_callback_fence()
    db_path = tmp_path / "governance.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    yield db_path
    control.clear_pbi_callback_fence()


def _attempt() -> str:
    return str(uuid.uuid4())


def _record_launch(attempt_id: str, *, message: str = "launched") -> int:
    run_id = pbi_sync._record_sync_run(
        "refresh", "launched", message, attempt_id=attempt_id
    )
    assert run_id is not None
    return run_id


def _seed_reports():
    with database.get_db() as db:
        db.execute("INSERT INTO reports(id, name, archived) VALUES (1, 'Alpha', 0)")
        db.execute("INSERT INTO reports(id, name, archived) VALUES (2, 'Beta', 0)")


def _payload(attempt_id=None, *, dataset_id="dataset-first", report_name="Alpha"):
    payload = {
        "workspace": "Governed Workspace",
        "synced_at": "2026-08-28T10:00:00+00:00",
        "reports": [
            {
                "report_name": report_name,
                "dataset_id": dataset_id,
                "web_url": "https://app.powerbi.com/groups/workspace/reports/report",
                "schedule": {"enabled": True, "days": ["Monday"], "times": ["08:00"]},
                "last_refresh": {
                    "end_time": "2026-08-28T09:00:00+00:00",
                    "status": "Completed",
                },
            }
        ],
    }
    if attempt_id is not None:
        payload["attempt_id"] = attempt_id
    return payload


def test_database_migrates_uuid_attempt_column_and_unique_index(pbi_db):
    with database.get_db() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(pbi_sync_runs)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(pbi_sync_runs)")}
    assert "attempt_id" in columns
    assert "idx_pbi_sync_runs_attempt" in indexes


def test_existing_sync_table_upgrades_before_attempt_index_is_created(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy-governance.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE pbi_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at DATETIME,
                finished_at DATETIME,
                message TEXT,
                details TEXT
            )"""
        )
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    database.init_db()

    with database.get_db() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(pbi_sync_runs)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(pbi_sync_runs)")}
    assert "attempt_id" in columns
    assert "idx_pbi_sync_runs_attempt" in indexes


def test_service_principal_launch_is_recorded_before_process_and_passes_attempt(
    pbi_db, monkeypatch
):
    observed = {}

    def fake_popen(command, **_kwargs):
        attempt_id = command[command.index("-AttemptId") + 1]
        with database.get_db() as db:
            row = db.execute(
                "SELECT * FROM pbi_sync_runs WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        assert row is not None
        assert row["status"] == "launched"
        observed["attempt_id"] = attempt_id
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(pbi_sync.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pbi_sync, "service_principal_configured", lambda: True)
    monkeypatch.setattr(pbi_sync.subprocess, "Popen", fake_popen)

    result = pbi_sync.trigger_pbi_sync(
        "Governed Workspace", cancel_existing=False
    )

    assert result["status"] == "launched"
    assert result["attempt_id"] == observed["attempt_id"]
    assert str(uuid.UUID(result["attempt_id"])) == result["attempt_id"]


def test_cached_account_launch_is_recorded_before_thread_and_passes_attempt(
    pbi_db, monkeypatch
):
    attempt_id = _attempt()
    observed = {}

    def fake_refresh(workspace, cancel_generation=None, attempt_id=None):
        observed.update(
            workspace=workspace,
            cancel_generation=cancel_generation,
            attempt_id=attempt_id,
        )
        return {"status": "completed"}

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            with database.get_db() as db:
                row = db.execute(
                    "SELECT status FROM pbi_sync_runs WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
            assert row["status"] == "launched"
            self.target()

    monkeypatch.setattr(pbi_fetch, "run_refresh_sync", fake_refresh)
    monkeypatch.setattr(pbi_sync.threading, "Thread", ImmediateThread)

    result = pbi_sync._launch_cached_account_sync(
        "refresh", "Governed Workspace", 17, attempt_id=attempt_id
    )

    assert result["attempt_id"] == attempt_id
    assert observed == {
        "workspace": "Governed Workspace",
        "cancel_generation": 17,
        "attempt_id": attempt_id,
    }


def test_cached_account_thread_start_failure_terminalizes_attempt(
    pbi_db, monkeypatch
):
    attempt_id = _attempt()

    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread quota exhausted")

    monkeypatch.setattr(pbi_sync.threading, "Thread", BrokenThread)

    result = pbi_sync._launch_cached_account_sync(
        "refresh", "Governed Workspace", 19, attempt_id=attempt_id
    )

    assert result["status"] == "error"
    assert result["attempt_id"] == attempt_id
    with database.get_db() as db:
        rows = db.execute(
            "SELECT status, message FROM pbi_sync_runs WHERE attempt_id=?",
            (attempt_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "thread quota exhausted" in rows[0]["message"]


def test_interactive_launch_row_exists_before_scheduled_task_can_start(
    pbi_db, monkeypatch
):
    observed = {}

    def fake_run(command, **_kwargs):
        if "/create" in command:
            task_command = command[command.index("/tr") + 1]
            observed["task_command"] = task_command
        if "/run" in command:
            attempt_id = observed["task_command"].split('-AttemptId "', 1)[1].split('"', 1)[0]
            with database.get_db() as db:
                row = db.execute(
                    "SELECT status FROM pbi_sync_runs WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
            assert row["status"] == "launched"
            observed["attempt_id"] = attempt_id
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pbi_sync.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pbi_sync, "service_principal_configured", lambda: False)
    monkeypatch.setattr(pbi_sync, "cached_account_available", lambda: False)
    monkeypatch.setattr(
        pbi_sync,
        "_run_rdp_console_guard",
        lambda: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(pbi_sync.subprocess, "run", fake_run)

    result = pbi_sync.trigger_pbi_sync(
        "Governed Workspace", cancel_existing=False
    )

    assert result["status"] == "launched"
    assert result["attempt_id"] == observed["attempt_id"]


def test_cached_fetch_carries_attempt_into_import_payload(monkeypatch):
    attempt_id = _attempt()
    captured = {}
    monkeypatch.setattr(
        pbi_fetch,
        "fetch_refresh_payload",
        lambda workspace, generation, attempt: {
            "workspace": workspace,
            "reports": [],
            "attempt_id": attempt,
        },
    )

    def fake_import(payload, generation):
        captured.update(payload=payload, generation=generation)
        return {"status": "completed"}

    monkeypatch.setattr(pbi_sync, "import_pbi_data", fake_import)

    result = pbi_fetch.run_refresh_sync("Governed Workspace", 23, attempt_id)

    assert result["status"] == "completed"
    assert captured["payload"]["attempt_id"] == attempt_id
    assert captured["generation"] == 23


def test_valid_import_atomically_completes_launch_and_duplicate_is_ignored(pbi_db):
    _seed_reports()
    attempt_id = _attempt()
    launch_id = _record_launch(attempt_id)

    first = pbi_sync.import_pbi_data(_payload(attempt_id))
    duplicate = pbi_sync.import_pbi_data(
        _payload(attempt_id, dataset_id="dataset-stale", report_name="Beta")
    )

    assert first["status"] == "completed"
    assert duplicate["status"] == "ignored"
    assert duplicate["reason"] == "duplicate_or_terminal_attempt"
    with database.get_db() as db:
        rows = db.execute(
            "SELECT * FROM pbi_sync_runs WHERE attempt_id=?", (attempt_id,)
        ).fetchall()
        alpha = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports WHERE id=1"
        ).fetchone()
        beta = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports WHERE id=2"
        ).fetchone()
    assert len(rows) == 1
    assert rows[0]["id"] == launch_id
    assert rows[0]["status"] == "completed"
    assert alpha[:] == ("dataset-first", 0)
    assert beta[:] == (None, 1)


@pytest.mark.parametrize(
    ("callback_kind", "expected_reason"),
    [
        ("stopped", "stopped_attempt"),
        ("superseded", "superseded_attempt"),
        ("unknown", "uncorrelated_attempt"),
        ("missing", "missing_attempt_id"),
    ],
)
def test_inactive_or_uncorrelated_callbacks_cannot_mutate_reports(
    pbi_db, callback_kind, expected_reason
):
    _seed_reports()
    attempt_id = _attempt()
    launch_id = _record_launch(attempt_id)
    callback_attempt = attempt_id

    if callback_kind == "stopped":
        with database.get_db() as db:
            db.execute(
                "UPDATE pbi_sync_runs SET status='stopped', finished_at=CURRENT_TIMESTAMP WHERE id=?",
                (launch_id,),
            )
    elif callback_kind == "superseded":
        _record_launch(_attempt(), message="newer launch")
    elif callback_kind == "unknown":
        callback_attempt = _attempt()
    elif callback_kind == "missing":
        callback_attempt = None

    result = pbi_sync.import_pbi_data(_payload(callback_attempt))

    assert result["status"] == "ignored"
    assert result["reason"] == expected_reason
    with database.get_db() as db:
        rows = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in rows] == [(None, 0), (None, 0)]


def test_legacy_import_works_only_before_any_correlated_attempt_exists(pbi_db):
    _seed_reports()

    result = pbi_sync.import_pbi_data(_payload())

    assert result["status"] == "completed"
    with database.get_db() as db:
        run = db.execute(
            "SELECT status, attempt_id FROM pbi_sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        report = db.execute(
            "SELECT pbi_dataset_id FROM reports WHERE id=1"
        ).fetchone()
    assert run[:] == ("completed", None)
    assert report["pbi_dataset_id"] == "dataset-first"


def test_stop_service_updated_launch_row_rejects_late_import(
    pbi_db, monkeypatch
):
    _seed_reports()
    attempt_id = _attempt()
    _record_launch(attempt_id)
    monkeypatch.setattr(pbi_sync.platform, "system", lambda: "Linux")

    stop_result = pbi_sync.stop_pbi_sync_processes("operator stop")
    late_result = pbi_sync.import_pbi_data(_payload(attempt_id))

    assert stop_result["status"] == "stopped"
    assert late_result["status"] == "ignored"
    assert late_result["reason"] == "stopped_attempt"
    with database.get_db() as db:
        launch = db.execute(
            "SELECT status FROM pbi_sync_runs WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        reports = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports ORDER BY id"
        ).fetchall()
    assert launch["status"] == "stopped"
    assert [tuple(row) for row in reports] == [(None, 0), (None, 0)]


def test_stop_and_callback_are_serialized_before_cancel_generation_is_published(
    pbi_db, monkeypatch
):
    _seed_reports()
    attempt_id = _attempt()
    _record_launch(attempt_id)
    stop_inside_transaction = threading.Event()
    release_stop = threading.Event()
    original_terminalize = control._terminalize_active_pbi_runs

    def gated_terminalize(db, now, note):
        stop_inside_transaction.set()
        assert release_stop.wait(5)
        return original_terminalize(db, now, note)

    monkeypatch.setattr(control, "_terminalize_active_pbi_runs", gated_terminalize)
    stop_result = {}
    callback_result = {}
    generation_before_stop = control.current_cancel_generation()

    stop_thread = threading.Thread(
        target=lambda: stop_result.update(
            control.request_stop_existing_work("race regression")
        )
    )
    stop_thread.start()
    assert stop_inside_transaction.wait(5)
    assert control.current_cancel_generation() == generation_before_stop

    callback_thread = threading.Thread(
        target=lambda: callback_result.update(
            pbi_sync.import_pbi_data(_payload(attempt_id))
        )
    )
    callback_thread.start()
    assert callback_thread.is_alive()
    release_stop.set()
    stop_thread.join(5)
    callback_thread.join(5)

    assert not stop_thread.is_alive()
    assert not callback_thread.is_alive()
    assert stop_result["pbi_runs_stopped"] == 1
    assert stop_result["generation"] == generation_before_stop + 1
    assert callback_result["status"] in {"stopped", "ignored"}
    with database.get_db() as db:
        launch = db.execute(
            "SELECT status FROM pbi_sync_runs WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        reports = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports ORDER BY id"
        ).fetchall()
    assert launch["status"] == "stopped"
    assert [tuple(row) for row in reports] == [(None, 0), (None, 0)]


def test_failed_stop_terminalization_installs_fail_closed_callback_fence(
    pbi_db, monkeypatch
):
    _seed_reports()
    stale_attempt = _attempt()
    _record_launch(stale_attempt)
    generation_before_stop = control.current_cancel_generation()

    def fail_terminalization(_db, _now, _note):
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(
        control, "_terminalize_active_pbi_runs", fail_terminalization
    )

    stop_result = control.request_stop_existing_work("busy DB regression")
    stale_callback = pbi_sync.import_pbi_data(_payload(stale_attempt))

    assert stop_result["status"] == "partial"
    assert stop_result["pbi_callbacks_fenced"] is True
    assert stop_result["generation"] == generation_before_stop + 1
    assert control.pbi_callbacks_fenced() is True
    assert stale_callback["status"] == "ignored"
    assert stale_callback["reason"] == "stop_fence_active"
    with database.get_db() as db:
        stale_row = db.execute(
            "SELECT status FROM pbi_sync_runs WHERE attempt_id=?",
            (stale_attempt,),
        ).fetchone()
        reports = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports ORDER BY id"
        ).fetchall()
    # The injected DB failure leaves the durable row launched, which is exactly
    # why the in-memory fence must prevent its callback from reaching metadata.
    assert stale_row["status"] == "launched"
    assert [tuple(row) for row in reports] == [(None, 0), (None, 0)]

    # A newer launch can lift the fence only after its row commits; the stale
    # attempt is then rejected durably by normal supersession logic.
    newer_attempt = _attempt()
    _record_launch(newer_attempt)
    assert control.pbi_callbacks_fenced() is False
    stale_after_new_launch = pbi_sync.import_pbi_data(_payload(stale_attempt))
    assert stale_after_new_launch["status"] == "ignored"
    assert stale_after_new_launch["reason"] == "superseded_attempt"


def test_startup_stops_correlated_and_legacy_launches_and_rejects_late_callbacks(
    pbi_db
):
    _seed_reports()
    legacy_id = pbi_sync._record_sync_run("refresh", "launched", "legacy launch")
    attempt_id = _attempt()
    _record_launch(attempt_id)

    recovered = main._recover_startup_pbi_syncs()
    recovered_again = main._recover_startup_pbi_syncs()
    correlated_late = pbi_sync.import_pbi_data(_payload(attempt_id))
    legacy_late = pbi_sync.import_pbi_data(_payload())

    assert legacy_id is not None
    assert recovered == 2
    assert recovered_again == 0
    assert correlated_late["status"] == "ignored"
    assert correlated_late["reason"] == "stopped_attempt"
    assert legacy_late["status"] == "ignored"
    assert legacy_late["reason"] == "missing_attempt_id"
    with database.get_db() as db:
        statuses = db.execute(
            "SELECT status FROM pbi_sync_runs ORDER BY id"
        ).fetchall()
        reports = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports ORDER BY id"
        ).fetchall()
    assert [row["status"] for row in statuses] == ["stopped", "stopped"]
    assert [tuple(row) for row in reports] == [(None, 0), (None, 0)]


def test_startup_legacy_stop_fences_late_callback_without_correlated_history(pbi_db):
    _seed_reports()
    pbi_sync._record_sync_run("refresh", "launched", "legacy launch")

    assert main._recover_startup_pbi_syncs() == 1
    result = pbi_sync.import_pbi_data(_payload())

    assert result["status"] == "ignored"
    assert result["reason"] == "stopped_legacy_attempt"
    with database.get_db() as db:
        reports = db.execute(
            "SELECT pbi_dataset_id, archived FROM reports ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in reports] == [(None, 0), (None, 0)]


def test_run_status_updates_same_attempt_and_rejects_duplicate_terminal_callback(pbi_db):
    attempt_id = _attempt()
    launch_id = _record_launch(attempt_id)

    completed_id = pbi_sync._record_sync_run(
        "refresh",
        "failed",
        "Power BI sign-in failed.",
        {"attempt_id": attempt_id},
    )
    duplicate_id = pbi_sync._record_sync_run(
        "refresh",
        "completed",
        "late success",
        {"attempt_id": attempt_id},
    )

    assert completed_id == launch_id
    assert duplicate_id is None
    with database.get_db() as db:
        rows = db.execute(
            "SELECT status, message FROM pbi_sync_runs WHERE attempt_id=?",
            (attempt_id,),
        ).fetchall()
    assert [tuple(row) for row in rows] == [("failed", "Power BI sign-in failed.")]


def test_wait_reads_only_launched_attempt_and_heartbeats_active_scanner_job(
    pbi_db, monkeypatch
):
    attempt_id = _attempt()
    launch_id = _record_launch(attempt_id)
    # A later uncorrelated row must never be mistaken for this attempt's result.
    pbi_sync._record_sync_run("refresh", "failed", "unrelated legacy callback")
    job_id = scanner_jobs.create_job("full_scan", current_step="Starting")
    scanner_jobs.mark_running(job_id, current_step="Starting")

    def finish_on_first_poll(_seconds):
        pbi_sync._record_sync_run(
            "refresh", "completed", "matching completion", attempt_id=attempt_id
        )

    monkeypatch.setattr(pbi_sync.time, "sleep", finish_on_first_poll)

    result = pbi_sync.wait_for_pbi_sync_completion(
        {
            "status": "launched",
            "run_id": launch_id,
            "attempt_id": attempt_id,
        },
        timeout_seconds=5,
        operation_id=job_id,
    )

    assert result["status"] == "completed"
    assert result["run"]["attempt_id"] == attempt_id
    job = scanner_jobs.get_job(job_id)
    assert job["status"] == "running"
    assert job["current_step"] == "Syncing Power BI metadata"


def test_wait_heartbeat_does_not_revive_terminal_scanner_job(pbi_db):
    attempt_id = _attempt()
    launch_id = _record_launch(attempt_id)
    pbi_sync._record_sync_run(
        "refresh", "completed", "matching completion", attempt_id=attempt_id
    )
    job_id = scanner_jobs.create_job("full_scan", current_step="Starting")
    scanner_jobs.finish_job(job_id, status="completed", result={"status": "completed"})

    result = pbi_sync.wait_for_pbi_sync_completion(
        {
            "status": "launched",
            "run_id": launch_id,
            "attempt_id": attempt_id,
        },
        timeout_seconds=5,
        operation_id=job_id,
    )

    assert result["status"] == "completed"
    job = scanner_jobs.get_job(job_id)
    assert job["status"] == "completed"
    assert job["current_step"] == "Finished"


def test_powershell_refresh_callbacks_include_attempt_id():
    script = pbi_sync.PS1_SCRIPT.read_text(encoding="utf-8")
    assert "[string]$AttemptId" in script
    assert "$payload.attempt_id = $AttemptId" in script
    assert "$correlatedDetails.attempt_id = $AttemptId" in script
    assert "$output.attempt_id = $AttemptId" in script
    assert "Start-CorrelatedPbiConnectWatchdog" in script
    assert "trap {" in script
    assert "Unhandled Power BI refresh sync failure" in script
    trap_block = script[script.index("trap {"):script.index("Import-Module")]
    assert 'Report-SyncStatus -Status "failed"' in trap_block
    assert script.index("trap {") < script.index("Import-Module")
