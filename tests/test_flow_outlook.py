import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import database, flow_outlook, flow_worker
from app.routers import flows


@pytest.fixture()
def outlook_db(tmp_path, monkeypatch):
    db_path = tmp_path / "outlook-flows.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def _request(actor="Analyst"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _outlook_flow(**overrides):
    values = {
        "name": "Inbox data",
        "source_type": "outlook",
        "outlook_subject_contains": "CODE 42",
        "target_folder": r"C:\Reports\Downloads",
        "schedule_type": "daily",
        "schedule_time": "08:00",
    }
    values.update(overrides)
    return flows.FlowWrite(**values)


def test_outlook_helper_and_python_use_the_same_excel_extension_contract():
    script = Path(__file__).parents[1].joinpath(
        "tools", "outlook_flow_attachment.ps1",
    ).read_text(encoding="utf-8")
    definition = next(
        line for line in script.splitlines() if line.startswith("$SupportedDataExtensions")
    )
    helper_extensions = tuple(re.findall(r'"(\.[a-z0-9]+)"', definition))

    assert helper_extensions == flow_outlook.SUPPORTED_ATTACHMENT_EXTENSIONS
    assert not ({".xla", ".xlam", ".xll"} & set(helper_extensions))


def test_outlook_flow_uses_hidden_internal_catalog_source_and_headless_job(outlook_db):
    saved = flows.create_flow(_outlook_flow(), _request())

    assert saved["source_type"] == "outlook"
    assert saved["filename_template"] == "{original}"
    assert saved["file_format"] == "auto"
    assert saved["source_adapter"] == "outlook_attachment"
    assert all(site["adapter"] != "outlook_attachment" for site in flows.catalog()["sites"])

    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
    assert job["execution"]["browser_mode"] == "headless"
    assert job["outlook_source"] == {
        "enabled": True,
        "mailbox": "default",
        "folder": "inbox",
        "include_subfolders": False,
        "subject_contains": "CODE 42",
        "supported_extensions": [
            ".csv", ".xls", ".xlsb", ".xlsm", ".xlsx", ".xlt", ".xltm", ".xltx",
        ],
        "attachment_policy": "exactly_one",
        "last_processed_identity": None,
        "force_reprocess": False,
    }


def test_manual_run_rewrites_an_existing_scheduled_outlook_job_to_force_reprocess(
    outlook_db, monkeypatch,
):
    monkeypatch.setattr(flows, "launch_local_worker", lambda _mode: {"status": "started"})
    saved = flows.create_flow(_outlook_flow(), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        cursor = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, requested_by, job_json, created_at)
               VALUES (?, 'scheduled', 'queued', 'scheduler', ?, ?)""",
            (saved["id"], flows._json(job), flows._iso(flows._now())),
        )
        run_id = cursor.lastrowid

    result = flows.queue_run(saved["id"], _request("Manual user"))
    assert result["id"] == run_id
    assert result["job"]["outlook_source"]["force_reprocess"] is True
    with database.get_db() as db:
        row = db.execute("SELECT trigger_type, requested_by, job_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()
    assert row["trigger_type"] == "manual"
    assert row["requested_by"] == "Manual user"
    assert json.loads(row["job_json"])["outlook_source"]["force_reprocess"] is True


def test_outlook_success_receipt_advances_dedup_but_no_op_does_not(outlook_db):
    saved = flows.create_flow(_outlook_flow(), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        cursor = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, worker_id, job_json, created_at, started_at)
               VALUES (?, 'scheduled', 'running', 'worker', ?, ?, ?)""",
            (saved["id"], flows._json(job), flows._iso(flows._now()), flows._iso(flows._now())),
        )
        producing_run = cursor.lastrowid

    receipt = flows.OutlookSourceReceipt(
        identity="a" * 64,
        received_at="2026-08-26T08:00:00+01:00",
        attachment_name="data.csv",
        subject="CODE 42 daily data",
    )
    flows.update_run(
        "worker", producing_run,
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "saved", "no_op": False},
            source_receipt=receipt,
        ),
    )
    with database.get_db() as db:
        state = db.execute(
            "SELECT outlook_last_identity, last_success_at FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()
        job = flows._build_job(db, saved["id"])
        cursor = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, worker_id, job_json, created_at, started_at)
               VALUES (?, 'scheduled', 'running', 'worker', ?, ?, ?)""",
            (saved["id"], flows._json(job), flows._iso(flows._now()), flows._iso(flows._now())),
        )
        no_op_run = cursor.lastrowid
    assert state["outlook_last_identity"] == "a" * 64
    first_success = state["last_success_at"]

    flows.update_run(
        "worker", no_op_run,
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "already processed", "no_op": True},
        ),
    )
    with database.get_db() as db:
        state = db.execute(
            "SELECT outlook_last_identity, last_success_at FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()
    assert state["outlook_last_identity"] == "a" * 64
    assert state["last_success_at"] == first_success


def test_outlook_csv_first_row_allows_one_column_and_rejects_blank_or_duplicate_headers(tmp_path):
    valid = tmp_path / "valid.csv"
    valid.write_text("Code\nA\n", encoding="utf-8")
    result = flow_worker._normalize_csv(valid, preamble="none", strict_headers=True)
    assert result["columns"] == ["Code"]
    assert result["preamble_rows_removed"] == 0

    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("Code,code\nA,B\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate column headers"):
        flow_worker._normalize_csv(duplicate, preamble="none", strict_headers=True)

    blank = tmp_path / "blank.csv"
    blank.write_text("Code,\nA,B\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="blank column header"):
        flow_worker._normalize_csv(blank, preamble="none", strict_headers=True)


def test_outlook_no_op_does_not_register_a_run_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(
        flow_worker.flow_outlook,
        "acquire_attachment",
        lambda **_kwargs: {
            "status": "already_processed",
            "message": "The attachment was already processed.",
        },
    )
    registered = []
    job = {
        "flow": {"id": 1, "source_type": "outlook"},
        "report": {"id": 1},
        "downloads": {"target_folder": str(tmp_path)},
        "outlook_source": {
            "subject_contains": "CODE 42",
            "last_processed_identity": "a" * 64,
            "force_reprocess": False,
        },
    }
    artifacts, _timings, outcome = flow_worker.execute_outlook_job(
        job, lambda *_args, **_kwargs: None, tmp_path,
        run_id=7, register_folder=lambda path: registered.append(path),
    )
    assert artifacts == []
    assert outcome["no_op"] is True
    assert registered == []
    assert not list(tmp_path.glob("#7_*"))


def test_outlook_task_result_identity_is_verified(monkeypatch, tmp_path):
    monkeypatch.setattr(flow_outlook.platform, "system", lambda: "Windows")

    def runner(args, _timeout, check=True):
        if "/run" not in args:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        exchange = tmp_path / "outlook_downloads" / "run-9"
        request = json.loads((exchange / "request.json").read_text(encoding="utf-8"))
        output = Path(request["output_folder"])
        output.mkdir(parents=True, exist_ok=True)
        saved = output / "daily.csv"
        saved.write_text("Code\nA\n", encoding="utf-8")
        identity = flow_outlook.attachment_identity("store", "entry", 1, "daily.csv")
        (exchange / "result.json").write_text(json.dumps({
            "status": "saved", "saved_path": str(saved), "saved_name": "daily.csv",
            "attachment_name": "daily.csv", "identity": identity,
            "store_id": "store", "entry_id": "entry", "attachment_index": 1,
            "subject": "CODE 42 daily", "received_at": "2026-08-26T08:00:00+01:00",
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = flow_outlook.acquire_attachment(
        run_id=9, profile_dir=tmp_path, subject_contains="CODE 42",
        last_processed_identity=None, force_reprocess=False,
        command_runner=runner,
    )
    assert result["status"] == "saved"
    assert result["receipt"]["identity"] == flow_outlook.attachment_identity(
        "store", "entry", 1, "daily.csv",
    )
