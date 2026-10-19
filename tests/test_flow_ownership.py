"""Flow ownership: who may change a Flow, owner emails and the owner's SQL user."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import config, database, flow_portable, flow_recordings, flow_sql, flow_view_refresh
from app.routers import flow_recordings as recording_routes
from app.routers import flows
from test_flows import _flow, _mark_discovered, _seed_catalog, flow_db  # noqa: F401
from test_view_refresh import SERVER


def as_person(person_id, name="Someone"):
    """A colleague's browser, as the identity middleware describes it."""
    return SimpleNamespace(state=SimpleNamespace(actor=name, person_id=person_id, is_host=False))


def as_host(person_id=None, name="Host PC"):
    return SimpleNamespace(state=SimpleNamespace(actor=name, person_id=person_id, is_host=True))


@pytest.fixture
def team(flow_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flows, "launch_local_worker", lambda *a, **k: {"status": "launched"})
    with database.get_db() as db:
        maya = db.execute("INSERT INTO people(name, role, email, sql_username) VALUES "
                          "('Maya', 'BI', 'maya@example.test', 'maya_sql')").lastrowid
        omar = db.execute("INSERT INTO people(name, role, email) VALUES ('Omar', 'BI', 'omar@example.test')").lastrowid
    return maya, omar


@pytest.fixture
def notices(monkeypatch):
    sent = []
    monkeypatch.setattr(flows, "_send_owner_change_notice", sent.append)
    return sent


def portal_flow(request, **overrides):
    with database.get_db() as db:
        found = db.execute("SELECT site_id, id FROM flow_reports ORDER BY id LIMIT 1").fetchone()
    if found:
        site_id, report_id = found
    else:
        site, report = _seed_catalog()
        _mark_discovered(report["id"])
        site_id, report_id = site["id"], report["id"]
    return flows.create_flow(_flow(site_id, report_id, **overrides), request)


def sql_flow(request=None, **overrides):
    with database.get_db() as db:
        db.execute("""INSERT OR IGNORE INTO flow_sql_catalog(database_name, schema_name, table_name, last_seen_at, stale)
                      VALUES ('analytics', 'public', 'flow_table', '2026-10-19T00:00:00+00:00', 0)""")
    values = dict(enabled=False, schedule_type="manual", sql_handoff_enabled=True, sql_mode="append",
                  sql_database="analytics", sql_schema="public", sql_table="flow_table")
    return portal_flow(request or as_host(), **{**values, **overrides})


def body_for(saved, **overrides):
    return _flow(saved["site_id"], saved["report_id"], **overrides)


def denied(call, text):
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 403
    assert text in caught.value.detail
    return caught.value.detail


def test_colleagues_create_flows_for_themselves(team, notices):
    maya, omar = team
    mine = portal_flow(as_person(maya, "Maya"))
    assert (mine["owner_person_id"], mine["owner_name"], mine["created_by"]) == (maya, "Maya", "Maya")
    site, report = mine["site_id"], mine["report_id"]
    denied(lambda: flows.create_flow(_flow(site, report, name="For Omar", owner_person_id=omar), as_person(maya)),
           "Only the host PC can create a Flow for someone else")
    denied(lambda: flows.create_flow(_flow(site, report, name="Anonymous"), as_person(None)), "Choose your user")
    assigned = flows.create_flow(_flow(site, report, name="Assigned by host", owner_person_id=omar), as_host())
    assert assigned["owner_person_id"] == omar
    assert flows.create_flow(_flow(site, report, name="Unassigned"), as_host())["owner_person_id"] is None
    assert notices == []


def test_only_the_owner_or_host_pc_changes_a_flow(team, notices):
    maya, omar = team
    saved = portal_flow(as_person(maya, "Maya"))
    omar_browser = as_person(omar, "Omar")
    reason = "belongs to Maya"
    denied(lambda: flows.update_flow(saved["id"], body_for(saved, name="Renamed by Omar"), omar_browser), reason)
    denied(lambda: flows.patch_flow(saved["id"], flows.FlowInlineWrite(browser_mode="headed"), omar_browser), reason)
    denied(lambda: flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=False), omar_browser), reason)
    denied(lambda: flows.delete_flow(saved["id"], flows.FlowDeleteWrite(confirmation=saved["name"]), omar_browser), reason)
    denied(lambda: flows.acknowledge_sql_reconciliation(saved["id"], flows.SQLReconciled(acknowledged=True), omar_browser), reason)
    denied(lambda: recording_routes.save_revision(saved["id"], recording_routes.RevisionWrite(definition={}), omar_browser), reason)
    denied(lambda: recording_routes.start_recording(saved["id"], omar_browser), reason)
    assert flows.get_flow(saved["id"])["name"] == saved["name"]

    # Anyone may still run it.
    queued = flows.queue_run(saved["id"], omar_browser)
    assert queued["status"] == "queued"
    flows.stop_run(saved["id"], omar_browser)

    # The owner edits it; omitting the owner keeps it, reassigning is the host's call.
    maya_browser = as_person(maya, "Maya")
    edited = flows.update_flow(saved["id"], body_for(saved, name="Maya's weekly download"), maya_browser)
    assert (edited["name"], edited["owner_person_id"]) == ("Maya's weekly download", maya)
    denied(lambda: flows.update_flow(saved["id"], body_for(saved, owner_person_id=omar), maya_browser),
           "Only the host PC can assign")
    denied(lambda: flows.patch_flow(saved["id"], flows.FlowInlineWrite(owner_person_id=omar), maya_browser),
           "Only the host PC can assign")
    assert flows.patch_flow(saved["id"], flows.FlowInlineWrite(browser_mode="headed"), maya_browser)["browser_mode"] == "headed"

    unassigned = flows.create_flow(body_for(saved, name="Legacy flow"), as_host())
    denied(lambda: flows.update_flow(unassigned["id"], body_for(saved, name="Legacy renamed"), maya_browser),
           "no owner")
    assert notices == []


def test_host_pc_reassigns_any_flow_and_the_previous_owner_is_told(team, notices):
    maya, omar = team
    saved = portal_flow(as_host(), owner_person_id=None)
    flows.patch_flow(saved["id"], flows.FlowInlineWrite(owner_person_id=maya), as_host())
    assert notices == []  # Nobody owned it before.
    flows.patch_flow(saved["id"], flows.FlowInlineWrite(owner_person_id=omar), as_host())
    assert flows.get_flow(saved["id"])["owner_person_id"] == omar
    assert [message["to"] for message in notices] == ["maya@example.test"]
    assert "Owner" in notices[0]["html_body"] and "Maya" in notices[0]["html_body"] and "Omar" in notices[0]["html_body"]


def test_host_edit_emails_the_owner_what_changed_and_when(team, notices, monkeypatch):
    maya, omar = team
    from datetime import datetime
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(flows, "dubai_now", lambda: datetime(2026, 10, 19, 14, 5, tzinfo=ZoneInfo("Asia/Dubai")))
    saved = portal_flow(as_person(maya, "Maya"))
    flows.update_flow(saved["id"], body_for(saved, name="Weekly download v2", schedule_time="09:30"), as_host())
    [message] = notices
    assert message["to"] == "maya@example.test"
    assert message["subject"] == "Metronome: your Flow Weekly download v2 was changed on the host PC"
    body = message["html_body"]
    for text in ("Changed by", "Host PC", "19 Oct 2026, 14:05 (Dubai time)", "Name", "Weekly report download",
                 "Weekly download v2", "Schedule time", "08:00", "09:30", "2 settings"):
        assert text in body, text
    with database.get_db() as db:
        logged = db.execute("SELECT detail FROM event_log WHERE action='owner_change_notified'").fetchone()
    assert logged["detail"] == "2 change(s) emailed to maya@example.test"

    flows.set_flow_enabled(saved["id"], flows.FlowEnabledWrite(enabled=False), as_host())
    assert "Active" in notices[-1]["html_body"] and ">On<" in notices[-1]["html_body"] and ">Off<" in notices[-1]["html_body"]
    flows.update_flow(saved["id"], body_for(saved, name="Weekly download v2", schedule_time="09:30", enabled=False), as_host())
    assert len(notices) == 2  # An unchanged save sends nothing.
    flows.update_flow(saved["id"], body_for(saved, name="Own edit", enabled=False), as_person(maya, "Maya"))
    flows.update_flow(saved["id"], body_for(saved, name="Host as Maya", enabled=False), as_host(maya, "Maya"))
    assert len(notices) == 2  # The owner changing their own Flow is not told.

    flows.delete_flow(saved["id"], flows.FlowDeleteWrite(confirmation="Host as Maya"), as_host(maya, "Maya"))
    assert len(notices) == 2  # Signed in as Maya on the host PC: still her own Flow.
    other = portal_flow(as_person(maya, "Maya"), name="Second flow", enabled=False)
    flows.delete_flow(other["id"], flows.FlowDeleteWrite(confirmation="Second flow"), as_host(omar, "Omar"))
    assert notices[-1]["subject"] == "Metronome: your Flow Second flow was deleted on the host PC"
    assert "Deleted" in notices[-1]["html_body"] and "Changed by" in notices[-1]["html_body"]
    assert ">Omar<" in notices[-1]["html_body"]


def test_owner_notice_never_blocks_the_change(team, monkeypatch):
    maya, omar = team
    with database.get_db() as db:
        db.execute("UPDATE people SET email=NULL WHERE id=?", (omar,))
    no_email = portal_flow(as_person(omar, "Omar"))
    monkeypatch.setattr(flows, "_send_owner_change_notice", lambda message: pytest.fail("Omar has no email"))
    assert flows.update_flow(no_email["id"], body_for(no_email, name="Changed"), as_host())["name"] == "Changed"

    def broken(message):
        raise RuntimeError("Outlook is not running")
    monkeypatch.setattr(flows, "_send_owner_change_notice", broken)
    mine = portal_flow(as_person(maya, "Maya"), name="Maya flow")
    assert flows.update_flow(mine["id"], body_for(mine, name="Maya flow renamed"), as_host())["name"] == "Maya flow renamed"
    with database.get_db() as db:
        skipped = [row["detail"] for row in db.execute(
            "SELECT detail FROM event_log WHERE action='owner_change_notice_skipped' ORDER BY id")]
    assert skipped == ["Owner Omar has no email in Users.", "Outlook is not running"]


def save_password(monkeypatch, person_id, password="fictional-password"):
    monkeypatch.setenv(flow_sql.person_password_setting(person_id), password)


def test_assigned_sql_flow_freezes_its_owner_and_needs_their_credentials(team, monkeypatch):
    maya, omar = team
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    mine = sql_flow(owner_person_id=maya)
    with database.get_db() as db:
        job = flows._build_job(db, mine["id"])
    assert job["sql_handoff"]["connect_as"] == {"person_id": maya, "name": "Maya", "username": "maya_sql"}
    refused = pytest.raises(HTTPException, flows.queue_run, mine["id"], as_person(omar))
    assert refused.value.status_code == 409
    assert refused.value.detail == ("The SQL password for Maya (maya_sql) is not saved on this computer. "
                                    "Ask the host PC to add it in Users, then run the Flow again.")
    save_password(monkeypatch, maya)
    queued = flows.queue_run(mine["id"], as_person(omar))
    with database.get_db() as db:
        stored = db.execute("SELECT job_json FROM flow_runs WHERE id=?", (queued["id"],)).fetchone()[0]
    assert json.loads(stored)["sql_handoff"]["connect_as"]["username"] == "maya_sql"
    assert "fictional-password" not in stored
    flows.stop_run(mine["id"], as_person(omar))

    no_username = sql_flow(owner_person_id=omar, name="Omar SQL")
    refused = pytest.raises(HTTPException, flows.queue_run, no_username["id"], as_host())
    assert refused.value.detail.startswith("Omar has no SQL username in Users.")

    unassigned = sql_flow(name="Shared account")
    with database.get_db() as db:
        assert "connect_as" not in flows._build_job(db, unassigned["id"])["sql_handoff"]


def test_scheduled_run_without_owner_credentials_fails_closed(team, monkeypatch):
    maya, _omar = team
    monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
    mine = sql_flow(owner_person_id=maya)
    with database.get_db() as db:
        db.execute("UPDATE flows SET enabled=1, schedule_type='daily', schedule_time='08:00', "
                   "next_run_at='2000-01-01T00:00:00+00:00' WHERE id=?", (mine["id"],))
    assert flows.queue_due_flows()["queued"] == []
    with database.get_db() as db:
        row = db.execute("SELECT last_error FROM flows WHERE id=?", (mine["id"],)).fetchone()
        assert db.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0] == 0
    assert "SQL password for Maya" in row["last_error"]


def csv_artifact(tmp_path):
    path = Path(tmp_path) / "export.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    return [{"file_path": str(path)}]


def test_sql_load_signs_in_as_the_owner_and_never_falls_back(tmp_path, monkeypatch):
    settings = tmp_path / ".env"
    settings.write_text('DG_USER_SQL_PASSWORD_7="first secret"\n', encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", settings)
    connections = []

    def engine(database, credentials=None):
        connections.append((database, credentials))
        raise RuntimeError(f"refused for {credentials[1] if credentials else 'shared'}")
    monkeypatch.setattr(flow_sql, "_engine", engine)
    target = {"database": "analytics", "schema": "public", "table": "sales", "mode": "append",
              "connect_as": {"person_id": 7, "name": "Maya", "username": "maya_sql"}}
    events = []
    with pytest.raises(flow_sql.SqlHandoffError) as failed:
        flow_sql.load_artifacts(csv_artifact(tmp_path), target, progress=events.append)
    assert connections == [("analytics", ("maya_sql", "first secret"))]
    assert events[2]["message"] == "Connecting to PostgreSQL database analytics as maya_sql."
    assert failed.value.stage == "connection"

    # The password is read when connecting: a host PC change applies to the next load.
    settings.write_text('DG_USER_SQL_PASSWORD_7="second secret"\n', encoding="utf-8")
    with pytest.raises(flow_sql.SqlHandoffError):
        flow_sql.load_artifacts(csv_artifact(tmp_path), target)
    assert connections[-1] == ("analytics", ("maya_sql", "second secret"))

    settings.write_text("", encoding="utf-8")
    monkeypatch.setenv("DG_UPLOAD_PGPASSWORD", "shared")  # Never used as a fallback.
    with pytest.raises(flow_sql.SqlHandoffError) as missing:
        flow_sql.load_artifacts(csv_artifact(tmp_path), target)
    assert len(connections) == 2
    assert "The SQL password for Maya (maya_sql) is not saved" in str(missing.value)

    unassigned = {key: value for key, value in target.items() if key != "connect_as"}
    with pytest.raises(flow_sql.SqlHandoffError):
        flow_sql.load_artifacts(csv_artifact(tmp_path), unassigned)
    assert connections[-1] == ("analytics", None)


def test_owner_password_is_redacted_from_database_errors(monkeypatch):
    monkeypatch.setattr(flow_sql, "_REDACTED_PASSWORDS", {"hunter2"})
    assert flow_sql._database_error(RuntimeError("login for hunter2 failed")) == "login for [redacted] failed"


def test_post_sql_view_refresh_uses_the_owner_sql_user(tmp_path, monkeypatch):
    monkeypatch.setenv(flow_sql.person_password_setting(7), "owner secret")
    seen = []

    def engine(database, credentials=None):
        seen.append(credentials)
        raise RuntimeError("stop")
    monkeypatch.setattr(flow_sql, "_engine", engine)
    job = {"sql_handoff": {"enabled": True, "connect_as": {"person_id": 7, "name": "Maya", "username": "maya_sql"}},
           "post_sql_refresh": {"views": [{"database": "analytics", "schema": "bi", "name": "mv_sales"}]}}
    with pytest.raises(RuntimeError):
        flow_view_refresh.precheck_before_sql(job, lambda *a: None, [], [])
    assert seen == [("maya_sql", "owner secret")]


def test_old_workers_never_claim_a_job_that_signs_in_as_its_owner():
    job = {"sql_handoff": {"enabled": True, "connect_as": {"person_id": 7, "username": "maya_sql"}}}
    assert not flow_sql.person_credentials_supported(job, {})
    assert flow_sql.person_credentials_supported(job, {flow_sql.PERSON_CREDENTIALS_CAPABILITY: True})
    assert flow_sql.person_credentials_supported({"validation_job": {"sql_handoff": {"enabled": True}}}, {})


def test_owner_sql_user_does_not_invalidate_recordings_but_reaches_portable_files(flow_db):  # noqa: F811
    from test_flow_recordings import draft_job
    _saved, job = draft_job()
    changed = json.loads(json.dumps(job))
    changed["sql_handoff"]["connect_as"] = {"person_id": 7, "name": "Maya", "username": "maya_sql"}
    assert flow_recordings.config_hash(changed) == flow_recordings.config_hash(job)
    source = flow_portable.source(changed)
    assert "def connection_credentials" in source and "def current_setting" in source
    compile(source, "run_flow.py", "exec")
