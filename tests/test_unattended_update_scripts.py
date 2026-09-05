"""Focused safety-contract tests for the Windows unattended updater."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APPLY_UPDATE = ROOT / "tools" / "apply_update.ps1"
BACKUP_SQLITE = ROOT / "tools" / "backup_sqlite.py"
SETUP = ROOT / "setup.ps1"
UPDATE_APP = ROOT / "update_app.ps1"


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


def test_unattended_task_is_only_a_validating_bridge_to_setup():
    source = APPLY_UPDATE.read_text(encoding="utf-8")

    assert "^[0-9a-fA-F]{40}$" in source
    assert "request.target_commit" in source
    assert '"Global\\Metronome_Auto_Update"' in source
    assert '"pending_update.json"' in source
    assert "request.code_dir" in source
    assert "request.receipt_path" in source
    assert '$SetupScript = Join-Path $CodeDir "setup.ps1"' in source
    assert "$env:DG_UPDATE_COMMIT_SHA = $TargetSha" in source
    assert "-File $SetupScript -Unattended" in source
    assert "setup.ps1 installed the detected GitHub main commit" in source

    # This file must remain a bridge. setup.ps1 owns download, backup,
    # installation, dependency handling, service registration, and restart.
    assert "Start-Process" not in source
    assert "Get-Credential" not in source
    assert "-Verb RunAs" not in source
    assert "nssm.exe install" not in source
    assert "nssm.exe set" not in source
    assert "pip wheel" not in source
    assert "pip download" not in source
    assert "robocopy.exe" not in source
    assert "Expand-Archive" not in source
    assert "Stop-Service" not in source


def test_setup_has_a_noninteractive_mode_for_the_update_task():
    source = SETUP.read_text(encoding="utf-8")

    assert "[switch]$Unattended" in source
    assert '$RelaunchArguments += "-Unattended"' in source
    assert 'throw "Unattended setup cannot request a Windows password' in source
    assert 'throw "Metronome failed its localhost health check after unattended setup' in source
    assert "if (-not $Unattended)" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows scheduled updater")
def test_bridge_records_a_missing_setup_failure_and_removes_its_request():
    with tempfile.TemporaryDirectory(prefix=".updater-bridge-", dir=ROOT) as temp:
        project = Path(temp)
        install = project / "installed"
        tools = install / "tools"
        updates = project / "updates"
        receipts = updates / "receipts"
        for directory in (tools, receipts):
            directory.mkdir(parents=True, exist_ok=True)
        worker_source = APPLY_UPDATE.read_text(encoding="utf-8").replace(
            '$MutexName = "Global\\Metronome_Auto_Update"',
            f'$MutexName = "Local\\Metronome_Bridge_Test_{uuid.uuid4().hex}"',
        )
        (tools / "apply_update.ps1").write_text(worker_source, encoding="utf-8")

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
        current_receipt = json.loads(
            (receipts / f"{current}.json").read_text(encoding="utf-8")
        )
        assert current_receipt["status"] == "failed"
        assert current_receipt["stage"] == "setup_failed"
        assert current_receipt["attempt_id"] == current
        assert "setup.ps1 was not found" in current_receipt["error"]
        assert not request.exists()


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


def test_manual_update_uses_only_the_authoritative_setup_script():
    source = UPDATE_APP.read_text(encoding="utf-8")

    assert 'Join-Path $CodeDir "setup.ps1"' in source
    assert "DG_UPDATE_COMMIT_SHA" in source
    assert "setup_ps1_clean.txt" not in source
    assert "Copy-Item $Template" not in source


def test_manual_update_requires_a_fresh_sha_and_propagates_setup_failure():
    source = UPDATE_APP.read_text(encoding="utf-8")
    assert "_metronome_check=" in source
    assert '"Cache-Control" = "no-cache"' in source
    assert "^[0-9a-fA-F]{40}$" in source
    assert "Continuing with a normal update" not in source
    assert 'throw "Could not confirm the newest version.' in source
    assert "exit $LASTEXITCODE" in source
