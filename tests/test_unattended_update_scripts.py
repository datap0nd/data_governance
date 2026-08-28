"""Focused safety-contract tests for the Windows unattended updater."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APPLY_UPDATE = ROOT / "tools" / "apply_update.ps1"
BACKUP_SQLITE = ROOT / "tools" / "backup_sqlite.py"
SETUP = ROOT / "setup.ps1"


def test_sqlite_backup_includes_committed_wal_pages():
    # Avoid pytest's Windows tmp_path symlink aliases: this host disables
    # following their symlink type during session cleanup.
    with tempfile.TemporaryDirectory(prefix=".updater-test-", dir=ROOT) as temp:
        temp_path = Path(temp)
        source = temp_path / "source.db"
        destination = temp_path / "backup.db"
        writer = sqlite3.connect(source)
        try:
            assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            writer.execute("CREATE TABLE facts(value TEXT NOT NULL)")
            writer.executemany(
                "INSERT INTO facts(value) VALUES (?)", [("first",), ("second",)]
            )
            writer.commit()
            assert source.with_name(source.name + "-wal").exists()
            # The updater takes an online snapshot and then atomically replaces
            # it with a quiesced snapshot after the services stop.
            with closing(sqlite3.connect(destination)) as old_backup:
                old_backup.execute("CREATE TABLE obsolete(value INTEGER)")
                old_backup.commit()

            completed = subprocess.run(
                [sys.executable, str(BACKUP_SQLITE), str(source), str(destination)],
                check=False,
                capture_output=True,
                text=True,
            )
            assert completed.returncode == 0, completed.stderr

            with closing(sqlite3.connect(destination)) as backup:
                assert backup.execute(
                    "SELECT value FROM facts ORDER BY rowid"
                ).fetchall() == [("first",), ("second",)]
                assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            writer.close()


def test_failed_sqlite_backup_does_not_replace_existing_destination():
    with tempfile.TemporaryDirectory(prefix=".updater-test-", dir=ROOT) as temp:
        temp_path = Path(temp)
        source = temp_path / "broken.db"
        destination = temp_path / "existing.db"
        source.write_bytes(b"not a sqlite database")
        with closing(sqlite3.connect(destination)) as existing:
            existing.execute("CREATE TABLE marker(value INTEGER)")
            existing.execute("INSERT INTO marker VALUES (7)")
            existing.commit()

        completed = subprocess.run(
            [sys.executable, str(BACKUP_SQLITE), str(source), str(destination)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        with closing(sqlite3.connect(destination)) as existing:
            assert existing.execute("SELECT value FROM marker").fetchone() == (7,)


def test_unattended_worker_has_pinned_serialized_noninteractive_contract():
    source = APPLY_UPDATE.read_text(encoding="utf-8")

    assert "^[0-9a-fA-F]{40}$" in source
    assert "request.target_commit" in source
    assert '"Global\\Metronome_Auto_Update"' in source
    assert 'Join-Path $CodeDir ".git"' in source
    assert '"pending_update.json"' in source
    assert "request.code_dir" in source
    assert "request.database_path" in source
    assert "request.receipt_path" in source
    assert "archive/$Sha.zip" in source
    assert "compileall" in source
    assert '"target_commit"' in source
    assert '"stage"' in source
    assert '"error"' in source
    assert "--no-index" in source
    assert "Wait-AppVersion -Sha $TargetSha" in source
    assert "[System.IO.File]::Replace" in source
    assert "Invoke-RobocopyChecked -Source $CodeBackup -Destination $CodeDir -Mirror" in source
    assert "if ($DatabaseMutationPossible)" in source
    assert "$CodeMutationStarted = $true" in source
    assert "$DatabaseMutationPossible = $true" in source
    assert 'Stop-ServiceChecked -Name $ServiceName' in source
    assert 'Start-ServiceChecked -Name $ServiceName' in source

    # The elevated task is provisioned once by interactive setup; its worker
    # must never prompt, spawn a browser/UAC child, or recreate service users.
    assert "Start-Process" not in source
    assert "Get-Credential" not in source
    assert "-Verb RunAs" not in source
    assert "nssm.exe install" not in source
    assert "nssm.exe set" not in source


def test_receipt_idempotency_is_checked_only_after_mutex_ownership():
    source = APPLY_UPDATE.read_text(encoding="utf-8")

    mutex = source.index("$MutexAcquired = $Mutex.WaitOne(0)")
    lock_failure = source.index("if (-not $MutexAcquired)", mutex)
    receipt_check = source.index("if (Test-Path -LiteralPath $ReceiptPath)", lock_failure)
    first_receipt_write = source.index(
        'Save-Receipt -Status "running" -Stage "validating_request"', receipt_check
    )
    lock_branch = source[lock_failure:receipt_check]

    assert mutex < lock_failure < receipt_check < first_receipt_write
    assert "Save-Receipt" not in lock_branch
    assert '@("succeeded", "failed", "rolled_back")' in source
    assert "Existing update receipt does not match this exact attempt" in source


def test_database_backup_precedes_runtime_stop_and_live_mutation():
    source = APPLY_UPDATE.read_text(encoding="utf-8")
    backup = source.index('-Description "WAL-aware SQLite backup"')
    mutation = source.index("$MutationStarted = $true", backup)
    stop = source.index("Stop-ExistingRuntime", mutation)
    overlay = source.index("Invoke-RobocopyChecked -Source $StagedCode", stop)
    assert backup < mutation < stop < overlay


def test_complete_wheelhouse_is_built_and_proved_before_runtime_stop():
    source = APPLY_UPDATE.read_text(encoding="utf-8")
    prepare = source.index("Prepare-CompleteWheelhouse -StagedCode")
    stop = source.index("$MutationStarted = $true", prepare)
    offline_install = source.index('"--find-links", $Wheelhouse', stop)
    assert '"pip", "wheel"' in source
    assert '"pip", "download"' in source
    assert '"--only-binary", ":all:"' in source
    assert prepare < stop < offline_install


def test_restart_orders_main_before_flow_worker():
    source = APPLY_UPDATE.read_text(encoding="utf-8")
    function = source.split("function Start-PreviousRuntime {", 1)[1].split(
        "function Resume-HeadedTask {", 1
    )[0]
    main_start = function.index("Start-ServiceChecked -Name $ServiceName")
    flow_start = function.index("Start-ServiceChecked -Name $FlowServiceName")
    assert main_start < flow_start


def test_retention_keeps_current_and_two_prior_without_following_links():
    source = APPLY_UPDATE.read_text(encoding="utf-8")

    assert "function Invoke-SafeUpdateRetention" in source
    assert "Select-Object -First 2" in source
    assert "Test-TreeContainsReparsePoint" in source
    assert "System.IO.FileAttributes]::ReparsePoint" in source
    assert "Retention roots are not direct children" in source
    assert "Invoke-SafeUpdateRetention -CurrentAttemptId $AttemptId" in source
    assert "Retention is maintenance only" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows scheduled updater")
def test_terminal_worker_retains_only_two_prior_attempt_artifact_sets():
    with tempfile.TemporaryDirectory(prefix=".updater-retention-", dir=ROOT) as temp:
        project = Path(temp)
        install = project / "installed"
        tools = install / "tools"
        updates = project / "updates"
        attempts = updates / "attempts"
        logs = updates / "logs"
        receipts = updates / "receipts"
        for directory in (tools, attempts, logs, receipts):
            directory.mkdir(parents=True, exist_ok=True)
        worker_source = APPLY_UPDATE.read_text(encoding="utf-8").replace(
            '$MutexName = "Global\\Metronome_Auto_Update"',
            f'$MutexName = "Local\\Metronome_Retention_Test_{uuid.uuid4().hex}"',
        )
        (tools / "apply_update.ps1").write_text(worker_source, encoding="utf-8")

        older = [str(uuid.uuid4()) for _ in range(4)]
        base_time = time.time() - 600
        for index, attempt_id in enumerate(older):
            workspace = attempts / attempt_id
            workspace.mkdir()
            (workspace / "marker.txt").write_text(attempt_id, encoding="utf-8")
            log = logs / f"{attempt_id}.log"
            receipt = receipts / f"{attempt_id}.json"
            log.write_text("old log", encoding="utf-8")
            receipt.write_text("{}", encoding="utf-8")
            timestamp = base_time + index
            for artifact in (workspace, log, receipt):
                os.utime(artifact, (timestamp, timestamp))

        current = str(uuid.uuid4())
        request = updates / "pending_update.json"
        request.write_text(
            json.dumps(
                {
                    "version": 1,
                    "attempt_id": current,
                    "target_commit": "b" * 40,
                    "from_commit": "a" * 9,
                    "trigger_source": "automatic",
                    "code_dir": str(install.resolve()),
                    "database_path": str((project / "governance.db").resolve()),
                    "receipt_path": str((receipts / f"{current}.json").resolve()),
                    "created_at": "2026-08-28T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(tools / "apply_update.ps1"),
                "-RequestPath",
                str(request),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode != 0

        retained = set(older[-2:])
        for attempt_id in older:
            diagnostic = completed.stdout + "\n" + completed.stderr
            assert (attempts / attempt_id).exists() is (attempt_id in retained), diagnostic
            assert (logs / f"{attempt_id}.log").exists() is (attempt_id in retained)
            assert (receipts / f"{attempt_id}.json").exists() is (
                attempt_id in retained
            )
        assert (logs / f"{current}.log").is_file()
        current_receipt = json.loads(
            (receipts / f"{current}.json").read_text(encoding="utf-8")
        )
        assert current_receipt["status"] == "failed"
        assert current_receipt["attempt_id"] == current


def test_setup_registers_fixed_elevated_on_demand_update_task():
    source = SETUP.read_text(encoding="utf-8")

    assert '$AutoUpdateTaskName = "Metronome_Auto_Update"' in source
    assert '"tools\\apply_update.ps1"' in source
    assert '"pending_update.json"' in source
    assert "-NonInteractive" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "-ExecutionTimeLimit (New-TimeSpan -Seconds 0)" in source
    assert "-Password $ServicePassword -RunLevel Highest" in source
    assert "Register-ScheduledTask -TaskName $AutoUpdateTaskName" in source
