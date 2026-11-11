"""Focused safety-contract tests for the Windows unattended updater."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APPLY_UPDATE = ROOT / "tools" / "apply_update.ps1"
BACKUP_SQLITE = ROOT / "tools" / "backup_sqlite.py"
GIT_ATTRIBUTES = ROOT / ".gitattributes"


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


def test_update_archive_never_contains_anything_under_media(tmp_path):
    # The update downloads GitHub's archive of main, which git archive builds
    # and which honours export-ignore. Archive a fixture commit carrying the
    # repository's own .gitattributes, exactly as GitHub would.
    checkout = tmp_path / "checkout"
    for relative in (
        "media/film/audio/score.wav", "media/film/renders/animatic.mp4",
        "media/notes.md", "app/main.py", "app/media.py", "docs/media/guide.md",
    ):
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    (checkout / ".gitattributes").write_bytes(GIT_ATTRIBUTES.read_bytes())
    git = ["git", "-C", str(checkout), "-c", "user.name=Fixture",
           "-c", "user.email=fixture@example.invalid", "-c", "core.autocrlf=false"]
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True)
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True, capture_output=True)
    archive = tmp_path / "update.zip"
    subprocess.run([*git, "archive", "--format=zip", "-o", str(archive), "HEAD"],
                   check=True, capture_output=True)

    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert not [name for name in names if name == "media/" or name.startswith("media/")]
    assert {"app/main.py", "app/media.py", "docs/media/guide.md"} <= names
