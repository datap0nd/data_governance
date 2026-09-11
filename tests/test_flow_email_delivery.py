"""Optional final Email step: the run's final file is handed to Outlook.

Outlook, schtasks and the BI desktop are synthetic: the hand-off is captured
through the same monkeypatches tests/test_outlook_dispatch.py uses, so these
checks prove the payload, the recorded outcome and the receipt mirror, not a
real delivery.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.database as database
from app import flow_email_delivery as delivery
from app import flow_handover, flow_recordings, flow_standalone
from app.routers import email as email_router
from app.routers import flows
from test_flows import _flow, _mark_discovered, _person, _request, _seed_catalog, flow_db  # noqa: F401

RECIPIENTS = ["ops@example.test", "lead@example.test"]
EMAIL = {"enabled": True, "recipients": RECIPIENTS, "subject": None}


@pytest.fixture
def outlook(tmp_path, monkeypatch):
    """Capture Outlook hand-offs the way the dispatch tests do; nothing runs."""
    script = tmp_path / "outlook.ps1"
    script.write_text("# test", encoding="utf-8")
    monkeypatch.setattr(email_router, "OUTLOOK_SCRIPT", script)
    monkeypatch.setattr(email_router.platform, "system", lambda: "Windows")
    payloads: list[Path] = []
    counter = iter(range(100))

    def payload_path():
        path = tmp_path / f"outlook-task-email-{next(counter)}.json"
        payloads.append(path)
        return path

    monkeypatch.setattr(email_router, "_payload_path", payload_path)
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(email_router.subprocess, "run", fake_run)

    def messages(index=-1):
        return json.loads(payloads[index].read_text(encoding="utf-8"))["messages"]

    return SimpleNamespace(payloads=payloads, commands=commands, messages=messages)


def _artifact(name, *, size=1024, rows=12, period="2026-W30", status="saved"):
    return {
        "file_path": rf"C:\Reports\Downloads\#5_11-09-2026\{name}", "filename": name,
        "file_size": size, "row_count": rows, "status": status, "period_key": period,
    }


def _saved_flow(monkeypatch, email=EMAIL, **overrides):
    monkeypatch.setattr(flows, "launch_local_worker", lambda mode, **kwargs: {"status": "starting"})
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    return flows.create_flow(_flow(site["id"], report["id"], email_delivery=email, **overrides), _request())


def _another_flow(saved, email=None, **overrides):
    """A second Flow on the same seeded catalog (website names are unique)."""
    return flows.create_flow(
        _flow(saved["site_id"], saved["report_id"], email_delivery=email, **overrides), _request(),
    )


def _finish(flow_id, status="succeeded", artifacts=None, progress=None, worker="mail-worker"):
    queued = flows.queue_run(flow_id, _request())
    flows.register_worker(flows.WorkerRegister(
        worker_id=worker, display_name="Mail worker", capabilities={"shared_flow_artifacts": True},
    ))
    flows.claim_run(worker)
    body = flows.WorkerProgress(
        status=status,
        progress=progress or {"stage": "complete", "message": "Saved 2 export(s).", "no_op": False},
        artifacts=artifacts if artifacts is not None else [_artifact("weekly_2026-W30.csv"), _artifact("weekly_2026-W31.csv", period="2026-W31")],
        error="synthetic failure" if status == "failed" else None,
    )
    return queued["id"], flows.update_run(worker, queued["id"], body)


def _run_row(run_id):
    with database.get_db() as db:
        return dict(db.execute("SELECT * FROM flow_runs WHERE id=?", (run_id,)).fetchone())


def _events(run_id):
    with database.get_db() as db:
        return [dict(row) for row in db.execute(
            "SELECT stage, status, message, error FROM flow_run_events WHERE run_id=? ORDER BY id", (run_id,)
        )]


def _log_actions(flow_id):
    with database.get_db() as db:
        return [row[0] for row in db.execute(
            "SELECT action FROM event_log WHERE entity_type='flow' AND entity_id=? ORDER BY id", (flow_id,)
        )]


def test_normalize_config_rules():
    assert delivery.normalize_config(None) == {"enabled": False, "recipients": [], "subject": None}
    assert delivery.normalize_config({"enabled": False, "recipients": ["kept@example.test"]}) == delivery.default_config()
    assert delivery.normalize_config({"enabled": True, "recipients": "a@x.test; B@x.test,\n a@X.test", "subject": " Weekly file "}) == {
        "enabled": True, "recipients": ["a@x.test", "B@x.test"], "subject": "Weekly file",
    }
    assert delivery.normalize_config({"enabled": True, "recipients": ["a@x.test"], "subject": ""})["subject"] is None
    with pytest.raises(ValueError, match="at least one recipient"):
        delivery.normalize_config({"enabled": True, "recipients": []})
    with pytest.raises(ValueError, match="Invalid email address: planner"):
        delivery.normalize_config({"enabled": True, "recipients": ["planner"]})
    with pytest.raises(ValueError, match="at most 20"):
        delivery.normalize_config({"enabled": True, "recipients": [f"p{i}@x.test" for i in range(21)]})
    with pytest.raises(ValueError, match="200 characters"):
        delivery.normalize_config({"enabled": True, "recipients": ["a@x.test"], "subject": "s" * 201})
    with pytest.raises(ValueError, match="must be an object"):
        delivery.normalize_config("ops@example.test")
    assert delivery.saved_config("not json") == delivery.default_config()
    assert delivery.saved_config(json.dumps(EMAIL)) == EMAIL


def test_flow_write_reports_the_email_field_in_validation_errors():
    with pytest.raises(ValidationError) as excinfo:
        _flow(1, 1, email_delivery={"enabled": True, "recipients": ["planner"]})
    assert any("email_delivery" in error["loc"] for error in excinfo.value.errors())
    assert "Invalid email address: planner" in str(excinfo.value)


def test_email_step_persists_independently_of_sql(flow_db, monkeypatch):
    saved = _saved_flow(monkeypatch)
    assert saved["sql_handoff_enabled"] is False
    assert saved["email_delivery"] == EMAIL
    listed = next(item for item in flows.list_flows() if item["id"] == saved["id"])
    assert listed["email_delivery"] == EMAIL
    site_id, report_id = saved["site_id"], saved["report_id"]
    # An older client that omits the field keeps the saved setting.
    kept = flows.update_flow(saved["id"], _flow(site_id, report_id), _request())
    assert kept["email_delivery"] == EMAIL
    # Disabling clears the recipients; the frozen job follows the saved setting.
    cleared = flows.update_flow(
        saved["id"], _flow(site_id, report_id, email_delivery={"enabled": False}), _request(),
    )
    assert cleared["email_delivery"] == delivery.default_config()
    restored = flows.update_flow(
        saved["id"], _flow(site_id, report_id, email_delivery={**EMAIL, "subject": "Weekly file"}), _request(),
    )
    assert restored["email_delivery"]["subject"] == "Weekly file"
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
    assert job["email_delivery"] == {**EMAIL, "subject": "Weekly file"}


def test_email_config_stays_out_of_recording_script_and_handover_hashes(flow_db, monkeypatch):
    saved = _saved_flow(monkeypatch)
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        snapshot = flow_handover.snapshot(db, saved["id"])
    without = {key: value for key, value in job.items() if key != "email_delivery"}
    assert flow_recordings.config_hash(job) == flow_recordings.config_hash(without)
    frozen = flow_standalone.freeze(job)
    assert "email_delivery" not in frozen
    assert flow_standalone.config_hash(frozen) == flow_standalone.config_hash(flow_standalone.freeze(without))
    assert "ops@example.test" not in flow_standalone.canonical(frozen)
    assert "email_delivery" not in snapshot["settings"]
    assert "ops@example.test" not in json.dumps(snapshot)


def test_succeeded_run_hands_the_final_file_to_outlook(flow_db, monkeypatch, outlook):
    person = _person()
    saved = _saved_flow(monkeypatch, owner_person_id=person["id"])
    run_id, result = _finish(saved["id"])

    assert result["status"] == "succeeded"
    assert result["email"]["status"] == "pending"
    assert result["email"]["attached"] is True
    assert result["email"]["files"] == ["weekly_2026-W30.csv", "weekly_2026-W31.csv"]
    message = outlook.messages()[0]
    assert message["to"] == "ops@example.test; lead@example.test"
    assert message["subject"] == f"Metronome flow file: {saved['name']} (run #{run_id})"
    assert [item["path"] for item in message["attachments"]] == [
        rf"C:\Reports\Downloads\#5_11-09-2026\weekly_2026-W30.csv",
        rf"C:\Reports\Downloads\#5_11-09-2026\weekly_2026-W31.csv",
    ]
    assert "weekly_2026-W30.csv (12 rows)" in message["html_body"]
    assert "FLOW FILE" in message["html_body"]
    assert "Flow owner:\n                Dana" in message["html_body"] or "Dana" in message["html_body"]
    assert any(command[:2] == ["schtasks", "/create"] for command in outlook.commands)

    row = _run_row(run_id)
    assert row["email_status"] == "pending" and row["email_dispatch_id"] and row["email_detail"] is None
    with database.get_db() as db:
        dispatch = db.execute("SELECT purpose, status FROM outlook_dispatches WHERE id=?", (row["email_dispatch_id"],)).fetchone()
    assert dispatch["purpose"] == "flow_file" and dispatch["status"] == "pending"
    assert [event["stage"] for event in _events(run_id)][-1] == "email_delivery"
    assert "email_launched" in _log_actions(saved["id"])
    detail = flows.get_run(run_id)
    assert detail["email"]["status"] == "pending"
    assert detail["email"]["recipients"] == RECIPIENTS
    assert detail["email"]["files"] == ["weekly_2026-W30.csv", "weekly_2026-W31.csv"]
    listed = next(item for item in flows.list_runs(limit=100) if item["id"] == run_id)
    assert listed["email"]["status"] == "pending" and "files" not in listed["email"]


def test_custom_subject_and_transformed_output_are_used(flow_db, monkeypatch, outlook):
    saved = _saved_flow(monkeypatch, email={**EMAIL, "subject": "Weekly sell-out file"})
    artifacts = [
        _artifact("weekly_2026-W30.csv"),
        _artifact("weekly_2026-W30.csv", status="transformed", rows=9),
    ]
    artifacts[1]["file_path"] = r"C:\Reports\Downloads\#5_11-09-2026\script_results\weekly_2026-W30.csv"
    queued = flows.queue_run(saved["id"], _request())
    with database.get_db() as db:
        job = json.loads(db.execute("SELECT job_json FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()[0])
        job["transformation"]["enabled"] = True
        db.execute("UPDATE flow_runs SET job_json=? WHERE id=?", (json.dumps(job), queued["id"]))
    flows.register_worker(flows.WorkerRegister(
        worker_id="mail-worker", display_name="Mail worker", capabilities={"shared_flow_artifacts": True},
    ))
    flows.claim_run("mail-worker")
    result = flows.update_run("mail-worker", queued["id"], flows.WorkerProgress(
        status="succeeded", progress={"stage": "complete", "message": "Saved 1 transformed CSV file(s)."}, artifacts=artifacts,
    ))
    assert result["email"]["files"] == ["weekly_2026-W30.csv"]
    message = outlook.messages()[0]
    assert message["subject"] == "Weekly sell-out file"
    assert [item["path"] for item in message["attachments"]] == [artifacts[1]["file_path"]]
    assert "(9 rows)" in message["html_body"]


def test_no_op_failed_and_disabled_runs_do_not_email(flow_db, monkeypatch, outlook):
    saved = _saved_flow(monkeypatch)
    run_id, result = _finish(saved["id"], progress={"stage": "outlook_no_op", "message": "Nothing new.", "no_op": True}, artifacts=[])
    assert result["email"] is None and not outlook.payloads
    assert _run_row(run_id)["email_status"] is None

    sent = []
    monkeypatch.setattr(email_router, "_launch_outlook_payload", lambda messages, mode="send": sent.append(messages) or len(messages))
    run_id, result = _finish(saved["id"], status="failed", worker="mail-worker-2")
    assert result["email"] is None and not outlook.payloads
    assert _run_row(run_id)["email_status"] is None

    plain = _another_flow(saved, name="No email flow")
    run_id, result = _finish(plain["id"], worker="mail-worker-3")
    assert result["email"] is None and not outlook.payloads
    assert flows.get_run(run_id)["email"] is None


def test_outlook_launch_failure_keeps_the_run_succeeded(flow_db, monkeypatch):
    saved = _saved_flow(monkeypatch)
    monkeypatch.setattr(email_router.platform, "system", lambda: "Linux")
    run_id, result = _finish(saved["id"])
    assert result["status"] == "succeeded"
    assert result["email"]["status"] == "failed"
    assert "only available on Windows" in result["email"]["detail"]
    row = _run_row(run_id)
    assert row["status"] == "succeeded" and row["email_status"] == "failed"
    assert "only available on Windows" in row["email_detail"]
    with database.get_db() as db:
        flow = db.execute("SELECT last_status, last_success_at FROM flows WHERE id=?", (saved["id"],)).fetchone()
    assert flow["last_status"] == "succeeded" and flow["last_success_at"]
    events = _events(run_id)
    assert events[-1]["stage"] == "email_failed" and "only available on Windows" in events[-1]["error"]
    assert "email_failed" in _log_actions(saved["id"])
    assert flows.get_run(run_id)["email"]["status"] == "failed"


def test_files_over_the_cap_are_described_instead_of_attached(flow_db, monkeypatch, outlook):
    saved = _saved_flow(monkeypatch)
    big = [_artifact("part_1.csv", size=15 * 1024 * 1024), _artifact("part_2.csv", size=15 * 1024 * 1024, period="2026-W31")]
    run_id, result = _finish(saved["id"], artifacts=big)
    assert result["email"]["status"] == "pending" and result["email"]["attached"] is False
    assert "30.0 MB exceeds the 20 MB limit" in result["email"]["detail"]
    message = outlook.messages()[0]
    assert "attachments" not in message
    assert "too large to attach" in message["html_body"]
    assert r"C:\Reports\Downloads\#5_11-09-2026\part_1.csv" in message["html_body"]
    assert "30.0 MB exceeds" in _run_row(run_id)["email_detail"]


def test_reconcile_mirrors_the_outlook_receipt_onto_the_run(flow_db, monkeypatch, outlook):
    saved = _saved_flow(monkeypatch)
    run_id, result = _finish(saved["id"])
    with database.get_db() as db:
        dispatch = dict(db.execute("SELECT * FROM outlook_dispatches WHERE id=?", (result["email"]["dispatch_id"],)).fetchone())
    Path(dispatch["receipt_path"]).write_text(json.dumps({"dispatch_id": dispatch["id"], "status": "submitted", "submitted_count": 1}), encoding="utf-8")
    email_router.reconcile_outlook_dispatches()
    assert _run_row(run_id)["email_status"] == "submitted"
    assert flows.get_run(run_id)["email"]["label"] == "submitted by Outlook"

    second_id, second = _finish(saved["id"], worker="mail-worker-2")
    with database.get_db() as db:
        dispatch = dict(db.execute("SELECT * FROM outlook_dispatches WHERE id=?", (second["email"]["dispatch_id"],)).fetchone())
    Path(dispatch["receipt_path"]).write_text(json.dumps({
        "dispatch_id": dispatch["id"], "status": "failed", "error": r"Attachment not found: C:\Reports\Downloads\#5_11-09-2026\weekly_2026-W30.csv",
    }), encoding="utf-8")
    email_router.reconcile_outlook_dispatches()
    row = _run_row(second_id)
    assert row["email_status"] == "failed" and "Attachment not found" in row["email_detail"]


def test_send_again_rules_and_outcome(flow_db, monkeypatch, outlook):
    saved = _saved_flow(monkeypatch)
    run_id, result = _finish(saved["id"])
    first_dispatch = result["email"]["dispatch_id"]
    again = flows.resend_run_email(run_id, _request())
    assert again["status"] == "pending" and again["dispatch_id"] != first_dispatch
    assert len(outlook.payloads) == 2
    assert _run_row(run_id)["email_dispatch_id"] == again["dispatch_id"]
    assert "email_resent" in _log_actions(saved["id"])
    assert _events(run_id)[-1]["message"].startswith("Send again:")

    monkeypatch.setattr(email_router.platform, "system", lambda: "Linux")
    with pytest.raises(HTTPException) as excinfo:
        flows.resend_run_email(run_id, _request())
    assert excinfo.value.status_code == 502 and "only available on Windows" in excinfo.value.detail
    assert _run_row(run_id)["email_status"] == "failed"

    failed_id, _ = _finish(saved["id"], status="failed", worker="mail-worker-2")
    with pytest.raises(HTTPException) as excinfo:
        flows.resend_run_email(failed_id, _request())
    assert excinfo.value.status_code == 409
    no_op_id, _ = _finish(saved["id"], progress={"stage": "outlook_no_op", "message": "Nothing new.", "no_op": True}, artifacts=[], worker="mail-worker-3")
    with pytest.raises(HTTPException) as excinfo:
        flows.resend_run_email(no_op_id, _request())
    assert "nothing new" in excinfo.value.detail
    plain = _another_flow(saved, name="No email flow")
    plain_id, _ = _finish(plain["id"], worker="mail-worker-4")
    with pytest.raises(HTTPException) as excinfo:
        flows.resend_run_email(plain_id, _request())
    assert "no Email step" in excinfo.value.detail
    with pytest.raises(HTTPException) as excinfo:
        flows.resend_run_email(999, _request())
    assert excinfo.value.status_code == 404


def test_retry_runs_resolve_the_source_files(flow_db, monkeypatch):
    saved = _saved_flow(monkeypatch)
    source_id, _ = _finish(saved["id"])
    with database.get_db() as db:
        base = dict(db.execute("SELECT job_json FROM flow_runs WHERE id=?", (source_id,)).fetchone())
        job = json.loads(base["job_json"])
        sql_retry_job = {**job, "job_type": "sql_retry", "sql_retry": {"source_run_id": source_id, "artifacts": [_artifact("carried.csv", rows=3)]}}
        view_retry_job = {**job, "job_type": "view_retry", "view_retry": {"source_run_id": source_id}}
        db.execute("INSERT INTO flow_runs (flow_id, trigger_type, status, job_json) VALUES (?, 'sql_retry', 'succeeded', ?)", (saved["id"], json.dumps(sql_retry_job)))
        sql_retry_id = db.execute("SELECT MAX(id) FROM flow_runs").fetchone()[0]
        db.execute("INSERT INTO flow_runs (flow_id, trigger_type, status, job_json) VALUES (?, 'view_retry', 'succeeded', ?)", (saved["id"], json.dumps(view_retry_job)))
        view_retry_id = db.execute("SELECT MAX(id) FROM flow_runs").fetchone()[0]
        assert [item["filename"] for item in delivery.final_files(db, sql_retry_id)] == ["carried.csv"]
        assert [item["filename"] for item in delivery.final_files(db, view_retry_id)] == ["weekly_2026-W30.csv", "weekly_2026-W31.csv"]
        assert delivery.final_files(db, 999) == []
