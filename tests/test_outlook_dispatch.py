import json
from types import SimpleNamespace

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
