from datetime import datetime, timezone

import pytest

from app import database
from app.routers import scheduled_tasks
from app.scanner.task_scheduler_runner import _sync_task_alerts, task_result_details


@pytest.fixture
def task_db(tmp_path):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    return database.DB_PATH


def _insert_task(db, task_id, name, *, script_id=None, last_result="1", enabled=1, last_run_time="2026-08-01T08:00:00+00:00"):
    db.execute(
        """INSERT INTO scheduled_tasks
           (id, task_name, task_path, status, enabled, script_id, last_result, last_run_time, archived)
           VALUES (?, ?, ?, 'Ready', ?, ?, ?, ?, 0)""",
        (task_id, name, f"\\{name}", enabled, script_id, last_result, last_run_time),
    )


@pytest.mark.parametrize(
    ("code", "state", "label"),
    [
        ("0", "success", "Succeeded"),
        ("0x41301", "running", "Running"),
        ("267011", "never_run", "Not run yet"),
        ("0x41325", "queued", "Queued"),
        ("1", "failed", "Failed (1)"),
    ],
)
def test_task_result_details(code, state, label):
    assert task_result_details(code) == (state, label)


def test_only_governed_actionable_task_failures_create_alerts(task_db):
    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        db.execute("INSERT INTO scripts (id, path, display_name) VALUES (1, 'C:/jobs/refresh.py', 'refresh.py')")
        _insert_task(db, 1, "Unlinked failure")
        _insert_task(db, 2, "Never run", script_id=1, last_run_time=None)
        _insert_task(db, 3, "Governed failure", script_id=1)
        created, resolved = _sync_task_alerts(db, now)

    assert created == 2
    assert resolved == 0
    with database.get_db() as db:
        actions = db.execute("SELECT type, scheduled_task_id, script_id FROM actions ORDER BY type").fetchall()
        assert {(row["type"], row["scheduled_task_id"], row["script_id"]) for row in actions} == {
            ("task_failed", 3, None),
            ("script_failed", None, 1),
        }


def test_disabling_failed_task_resolves_existing_alerts(task_db):
    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        db.execute("INSERT INTO scripts (id, path, display_name) VALUES (1, 'C:/jobs/refresh.py', 'refresh.py')")
        _insert_task(db, 1, "Governed failure", script_id=1)
        _sync_task_alerts(db, now)
        db.execute("UPDATE scheduled_tasks SET enabled = 0 WHERE id = 1")
        created, resolved = _sync_task_alerts(db, now)

    assert created == 0
    assert resolved == 2
    with database.get_db() as db:
        assert {row["status"] for row in db.execute("SELECT status FROM actions").fetchall()} == {"resolved"}


def test_scheduled_tasks_default_to_governed_rows(task_db):
    with database.get_db() as db:
        db.execute("INSERT INTO scripts (id, path, display_name) VALUES (1, 'C:/jobs/refresh.py', 'refresh.py')")
        _insert_task(db, 1, "Governed", script_id=1, last_result="0")
        _insert_task(db, 2, "Other Windows Task", last_result="0")

    assert [task.task_name for task in scheduled_tasks.list_scheduled_tasks()] == ["Governed"]
    assert {task.task_name for task in scheduled_tasks.list_scheduled_tasks(include_unlinked=True)} == {
        "Governed",
        "Other Windows Task",
    }


def test_database_restart_preserves_scheduled_task_action_foreign_key(task_db):
    with database.get_db() as db:
        _insert_task(db, 1, "Referenced task")
        db.execute(
            "INSERT INTO actions (type, scheduled_task_id) VALUES ('task_failed', 1)"
        )

    database.init_db()

    with database.get_db() as db:
        assert db.execute("SELECT task_name FROM scheduled_tasks WHERE id=1").fetchone()[0] == "Referenced task"
        assert db.execute("SELECT scheduled_task_id FROM actions").fetchone()[0] == 1
