import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.database as database
from app.database import get_db
from app.routers import email


def test_simultaneous_outlook_handoffs_use_unique_tasks_and_independent_receipts(
    tmp_path, monkeypatch
):
    database.DB_PATH = str(tmp_path / "outlook.db")
    database.init_db()
    script = tmp_path / "outlook.ps1"
    script.write_text("# test", encoding="utf-8")
    monkeypatch.setattr(email, "OUTLOOK_SCRIPT", script)
    monkeypatch.setattr(email.platform, "system", lambda: "Windows")
    counter = iter(range(10))
    monkeypatch.setattr(
        email,
        "_payload_path",
        lambda: tmp_path / f"outlook-task-email-{next(counter)}.json",
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(email.subprocess, "run", fake_run)
    message = {"to": "owner@example.test", "subject": "Pipeline", "html_body": "ok"}

    first = email.launch_outlook_dispatch([message], pipeline_run_id=None, purpose="test")
    second = email.launch_outlook_dispatch([message], pipeline_run_id=None, purpose="test")

    assert first["task_name"] != second["task_name"]
    create_names = [command[3] for command in commands if command[:2] == ["schtasks", "/create"]]
    run_names = [command[3] for command in commands if command[:2] == ["schtasks", "/run"]]
    assert create_names == [first["task_name"], second["task_name"]]
    assert run_names == create_names

    with get_db() as db:
        rows = db.execute("SELECT * FROM outlook_dispatches ORDER BY id").fetchall()
    for row in rows:
        receipt = {
            "dispatch_id": row["id"],
            "status": "submitted",
            "submitted_count": 1,
        }
        open(row["receipt_path"], "w", encoding="utf-8").write(json.dumps(receipt))

    result = email.reconcile_outlook_dispatches()
    assert result["processed"] == 2
    with get_db() as db:
        statuses = [row[0] for row in db.execute("SELECT status FROM outlook_dispatches ORDER BY id")]
    assert statuses == ["submitted", "submitted"]
    delete_names = [command[3] for command in commands if command[:2] == ["schtasks", "/delete"]]
    assert delete_names == create_names


def _windows_outlook(tmp_path, monkeypatch, *, script, payload_dir):
    database.DB_PATH = str(tmp_path / "outlook.db")
    database.init_db()
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# test", encoding="utf-8")
    monkeypatch.setattr(email, "OUTLOOK_SCRIPT", script)
    monkeypatch.setattr(email.platform, "system", lambda: "Windows")
    payload_dir.mkdir(parents=True, exist_ok=True)
    counter = iter(range(10))
    monkeypatch.setattr(
        email, "_payload_path",
        lambda: payload_dir / f"outlook-task-email-2026091109300012345{next(counter)}.json",
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(email.subprocess, "run", fake_run)
    return commands


def test_task_command_stays_within_the_schtasks_limit_via_a_launcher(tmp_path, monkeypatch):
    # schtasks.exe refuses a /TR longer than 261 characters. The helper
    # script under a long checkout path plus the timestamped payload and
    # receipt paths exceeded that, so the "Email the final file" step failed
    # with "The value for /TR option cannot be more than 261 character(s)".
    script = tmp_path / "Users" / "bi.desktop" / "Desktop" / "Metronome" / "data_governance" / "tools" / "outlook_task_email.ps1"
    payload_dir = tmp_path / "ProgramData" / "DataGovernance"
    commands = _windows_outlook(tmp_path, monkeypatch, script=script, payload_dir=payload_dir)
    message = {"to": "owner@example.test", "subject": "Flow file", "html_body": "ok",
               "attachments": [{"path": str(tmp_path / "weekly.csv")}]}
    old_style = (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}" '
        f'-PayloadPath "{payload_dir}/outlook-task-email-20260911093000123450.json" '
        f'-ReceiptPath "{payload_dir}/outlook-task-email-20260911093000123450.receipt.json" -Send'
    )
    assert len(old_style) > email.SCHTASKS_COMMAND_LIMIT

    dispatch = email.launch_outlook_dispatch([message], "send", purpose="flow_file")

    create = next(command for command in commands if command[:2] == ["schtasks", "/create"])
    task_command = create[create.index("/tr") + 1]
    assert len(task_command) <= email.SCHTASKS_COMMAND_LIMIT
    launcher = payload_dir / "outlook-task-email-20260911093000123450.launch.ps1"
    assert task_command == (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{launcher}"'
    )
    launcher_text = launcher.read_text(encoding="utf-8-sig")
    assert launcher_text.splitlines() == [
        f"& '{script}' -PayloadPath '{payload_dir / 'outlook-task-email-20260911093000123450.json'}' "
        f"-ReceiptPath '{payload_dir / 'outlook-task-email-20260911093000123450.receipt.json'}' -Send",
        "exit $LASTEXITCODE",
    ]
    with get_db() as db:
        row = db.execute("SELECT * FROM outlook_dispatches WHERE id=?", (dispatch["id"],)).fetchone()
    assert row["status"] == "pending"
    assert Path(row["payload_path"]) == payload_dir / "outlook-task-email-20260911093000123450.json"

    # Draft mode omits -Send; a quote in a path is escaped for PowerShell.
    email.launch_outlook_dispatch([message], "draft", purpose="flow_file")
    second = (payload_dir / "outlook-task-email-20260911093000123451.launch.ps1").read_text(encoding="utf-8-sig")
    assert second.splitlines()[0].endswith(".receipt.json'")
    assert " -Send" not in second


def test_launcher_is_removed_with_the_payload_and_receipt(tmp_path, monkeypatch):
    script = tmp_path / "tools" / "outlook_task_email.ps1"
    payload_dir = tmp_path / "ProgramData" / "DataGovernance"
    _windows_outlook(tmp_path, monkeypatch, script=script, payload_dir=payload_dir)
    message = {"to": "owner@example.test", "subject": "Flow file", "html_body": "ok"}

    dispatch = email.launch_outlook_dispatch([message], "send", purpose="flow_file")

    with get_db() as db:
        row = db.execute("SELECT * FROM outlook_dispatches WHERE id=?", (dispatch["id"],)).fetchone()
    launcher = email._launcher_path(Path(row["payload_path"]))
    assert launcher.is_file()
    Path(row["receipt_path"]).write_text(
        json.dumps({"dispatch_id": row["id"], "status": "submitted", "submitted_count": 1}),
        encoding="utf-8",
    )

    assert email.reconcile_outlook_dispatches()["processed"] == 1

    assert not launcher.exists()
    assert not Path(row["payload_path"]).exists()
    assert not Path(row["receipt_path"]).exists()


def test_an_overlong_launcher_path_fails_the_dispatch_before_schtasks(tmp_path, monkeypatch):
    script = tmp_path / "tools" / "outlook_task_email.ps1"
    payload_dir = tmp_path / ("x" * 230)
    commands = _windows_outlook(tmp_path, monkeypatch, script=script, payload_dir=payload_dir)
    message = {"to": "owner@example.test", "subject": "Flow file", "html_body": "ok"}

    with pytest.raises(email.OutlookEmailError, match="too long for Windows Task Scheduler"):
        email.launch_outlook_dispatch([message], "send", purpose="flow_file")

    assert commands == []
    with get_db() as db:
        row = db.execute("SELECT status, error FROM outlook_dispatches ORDER BY id DESC").fetchone()
    assert row["status"] == "failed"
    assert "261" in row["error"]
