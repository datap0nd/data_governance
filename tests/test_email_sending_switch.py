"""Settings > Email > Send emails: one switch that stops Metronome emailing anyone.

Outlook, schtasks and the BI desktop are synthetic: hand-offs are captured with
the same monkeypatches tests/test_outlook_dispatch.py uses, so these checks
prove what is refused, what is recorded and what still works, not a delivery.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import database, settings, settings_registry as registry
from app.routers import email as email_router
from app.routers import email_schedules, flows, pipelines, recurrences
from app.scanner import notifications
from test_flow_email_delivery import _events, _finish, _log_actions, _run_row, _saved_flow, outlook  # noqa: F401
from test_flows import _request, flow_db  # noqa: F401
from test_recurrences import _seed_owner_data

OFF = email_router.SENDING_OFF
MESSAGE = {"to": "owner@example.test", "subject": "Synthetic", "html_body": "<p>synthetic</p>"}


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(settings, "DB_PATH", str(path))
    database.init_db()
    return path


def _sending(on: bool) -> None:
    registry.save({"email.send_enabled": on})


def _dispatch_count() -> int:
    with database.get_db() as db:
        return db.execute("SELECT COUNT(*) FROM outlook_dispatches").fetchone()[0]


def test_send_emails_leads_the_email_section_and_is_on_until_changed(app_db):
    setting = registry.definition("email.send_enabled")
    assert (setting.kind, setting.default, setting.group, setting.applies) == ("bool", True, "Sending", "now")
    email = next(section for section in registry.describe() if section["id"] == "email")
    first = email["groups"][0]
    assert first["title"] == "Sending"
    assert first["settings"][0]["key"] == "email.send_enabled"
    assert first["settings"][0]["value"] is True
    assert email_router.sending_enabled() is True
    _sending(False)
    assert email_router.sending_enabled() is False


def test_while_off_nothing_is_handed_to_outlook_but_a_draft_still_is(app_db, outlook):
    _sending(False)
    with pytest.raises(email_router.EmailSendingOff) as refused:
        email_router.launch_outlook_dispatch([MESSAGE], "send", purpose="test")
    assert refused.value.status_code == 409 and refused.value.detail == OFF
    assert _dispatch_count() == 0 and outlook.commands == [] and outlook.payloads == []

    draft = email_router.launch_outlook_dispatch([MESSAGE], "draft", purpose="test")
    assert draft["status"] == "pending" and _dispatch_count() == 1

    _sending(True)
    sent = email_router.launch_outlook_dispatch([MESSAGE], "send", purpose="test")
    assert sent["status"] == "pending" and _dispatch_count() == 2


def test_the_email_page_is_told_and_a_manual_send_is_refused_with_the_reason(app_db, monkeypatch, outlook):
    summary = {"owner_name": "Owner One", "email": "owner@example.test", "subject": "Alerts", "body_html": "<p>x</p>"}
    monkeypatch.setattr(email_router, "_load_alert_summaries", lambda *args, **kwargs: [summary])
    request = SimpleNamespace(state=SimpleNamespace(actor="Host PC"), headers={})
    assert email_router.get_alert_summaries()["sending_enabled"] is True

    _sending(False)
    assert email_router.get_alert_summaries()["sending_enabled"] is False
    with pytest.raises(HTTPException) as refused:
        email_router.send_alert_summaries(email_router.SendSummariesRequest(mode="send"), request)
    assert refused.value.status_code == 409 and refused.value.detail == OFF
    assert _dispatch_count() == 0

    drafted = email_router.send_alert_summaries(email_router.SendSummariesRequest(mode="draft"), request)
    assert drafted == {"status": "launched", "mode": "draft", "count": 1}


def test_a_due_scheduled_summary_is_passed_over_and_says_why(app_db, monkeypatch):
    with database.get_db() as db:
        person_id = db.execute(
            "INSERT INTO people (name, role, email) VALUES ('Owner One', 'BI', 'owner@example.test')"
        ).lastrowid
        schedule = email_schedules._ensure_person_schedule(db, person_id)
        db.execute(
            "UPDATE email_schedules SET enabled=1, next_run_at=? WHERE id=?",
            ("2026-01-01T05:00:00+00:00", schedule["id"]),
        )
    built = []
    monkeypatch.setattr(email_schedules, "_send_person_summary", lambda item: built.append(item) or 1)
    _sending(False)

    assert email_schedules.dispatch_due_email_schedules() == 0
    assert built == []
    with database.get_db() as db:
        row = db.execute("SELECT * FROM email_schedules WHERE id=?", (schedule["id"],)).fetchone()
        events = db.execute(
            "SELECT action, detail FROM event_log WHERE entity_type='email_schedule'"
        ).fetchall()
    assert row["last_error"] == OFF and row["last_sent_at"] is None
    assert row["next_run_at"] > email_schedules._iso(email_schedules._now())  # the dispatcher's own comparison
    assert [tuple(event) for event in events] == [("not_sent", OFF)]

    # Nothing was held back for later: back on, the next occurrence is not due yet.
    _sending(True)
    assert email_schedules.dispatch_due_email_schedules() == 0 and built == []


def test_a_flow_file_email_is_skipped_with_the_reason_and_send_again_waits(flow_db, monkeypatch, outlook):
    saved = _saved_flow(monkeypatch)
    _sending(False)
    run_id, result = _finish(saved["id"])
    assert result["status"] == "succeeded"
    assert result["email"]["status"] == "skipped" and result["email"]["detail"] == OFF
    assert outlook.payloads == []
    row = _run_row(run_id)
    assert (row["status"], row["email_status"], row["email_detail"]) == ("succeeded", "skipped", OFF)
    assert _events(run_id)[-1]["stage"] == "email_skipped"
    assert "email_skipped" in _log_actions(saved["id"])

    with pytest.raises(HTTPException) as refused:
        flows.resend_run_email(run_id, _request())
    assert refused.value.status_code == 409 and refused.value.detail == OFF
    assert _run_row(run_id)["email_status"] == "skipped"

    _sending(True)
    again = flows.resend_run_email(run_id, _request())
    assert again["status"] == "pending" and len(outlook.payloads) == 1


def test_flow_owner_notices_are_recorded_as_not_sent(app_db, monkeypatch, outlook):
    context = {"flow_id": 7, "flow_name": "Weekly stock", "owner_name": "Owner One", "owner_email": "owner@example.test"}
    monkeypatch.setattr(flows, "_flow_failure_context", lambda db, run_id: context)
    monkeypatch.setattr(flows, "_flow_failure_message", lambda item: dict(MESSAGE))
    _sending(False)

    outcome = flows.notify_flow_owner_of_failure(41)
    assert outcome == {"status": "not_sent", "owner_name": "Owner One", "reason": OFF}

    with database.get_db() as db:
        owner_id = db.execute(
            "INSERT INTO people (name, role, email) VALUES ('Owner One', 'BI', 'owner@example.test')"
        ).lastrowid
    host = SimpleNamespace(state=SimpleNamespace(actor="Host PC", is_host=True, person_id=None))
    before = {"id": 7, "name": "Weekly stock", "owner_person_id": owner_id}
    monkeypatch.setattr(flows, "flow_changes", lambda old, new: [("Name", "a", "b")])
    monkeypatch.setattr(flows, "_owner_change_message", lambda *args: dict(MESSAGE))
    assert flows.notify_owner_of_host_change(host, before, dict(before)) == {"status": "not_sent", "reason": OFF}

    assert _dispatch_count() == 0 and outlook.payloads == []
    with database.get_db() as db:
        actions = [tuple(row) for row in db.execute(
            "SELECT action, detail FROM event_log WHERE entity_type='flow' ORDER BY id")]
    assert actions == [("owner_alert_skipped", f"run #41: {OFF}"), ("owner_change_notice_skipped", OFF)]


def test_an_alert_email_run_exports_and_sends_nothing(app_db, monkeypatch):
    _seed_owner_data()
    with database.get_db() as db:
        recurrence_id = db.execute(
            """INSERT INTO pbi_recurrences
               (name, enabled, workspace_id, workspace_name, report_id, report_name, report_url,
                embed_url, dataset_id, page_name, page_display_name, visual_name, visual_title,
                visual_type, owner_name, group_column, subject_template, recurrence, weekdays,
                send_time, next_run_at)
               VALUES ('Weekly exceptions', 1, 'w', 'Analytics', 'r', 'Operations',
                       'https://app.powerbi.com/groups/w/reports/r',
                       'https://app.powerbi.com/reportEmbed?reportId=r', 'd', 'Page', 'Summary',
                       'visual', 'Exceptions', 'tableEx', 'Report Owner', 'Subsidiary', '{recurrence}',
                       'weekly', 'monday', '08:00', '2026-01-05T08:00:00')"""
        ).lastrowid
    monkeypatch.setattr(recurrences, "get_db", database.get_db)

    def no_export(*args, **kwargs):
        raise AssertionError("Power BI must not be read while sending is off")

    monkeypatch.setattr(recurrences, "export_visual_data", no_export)
    launched = []
    monkeypatch.setattr(recurrences, "_launch_outlook_payload", lambda messages, mode: launched.append(mode) or 1)
    _sending(False)

    result = recurrences.run_recurrence(recurrence_id, mode="send", trigger_type="scheduled")
    assert result["status"] == "not_sent" and result["reason"] == OFF and result["email_count"] == 0
    assert launched == []
    with database.get_db() as db:
        run = db.execute("SELECT status, error FROM pbi_recurrence_runs WHERE recurrence_id=?", (recurrence_id,)).fetchone()
        parent = db.execute("SELECT last_status, last_error, last_sent_at, next_run_at FROM pbi_recurrences WHERE id=?",
                            (recurrence_id,)).fetchone()
    assert tuple(run) == ("not_sent", OFF)
    assert (parent["last_status"], parent["last_error"], parent["last_sent_at"]) == ("not_sent", OFF, None)
    assert parent["next_run_at"] != "2026-01-05T08:00:00"  # a scheduled alert moves on

    # A failure notice to the owner is suppressed the same way, never reported as a failure.
    owner = {"name": "Report Owner", "email": "owner@example.com"}
    monkeypatch.setattr(recurrences, "_launch_outlook_payload", email_router._launch_outlook_payload)
    monkeypatch.setattr(recurrences, "_failure_notification_message", lambda *args: dict(MESSAGE))
    assert recurrences._notify_owner_of_failure({}, owner, "boom") == {
        "status": "not_sent", "owner_name": "Report Owner", "reason": OFF,
    }


def test_a_pipeline_summary_is_not_sent_and_stays_resendable(app_db, monkeypatch):
    with database.get_db() as db:
        report_id = db.execute("INSERT INTO reports(name) VALUES ('Operations')").lastrowid
        run_id = db.execute(
            """INSERT INTO pipeline_runs (report_id, status, stage, plan_hash, plan_json)
               VALUES (?, 'succeeded', 'complete', 'hash', ?)""",
            (report_id, '{"report": {"name": "Operations"}, "recipient": {"email": "owner@example.test"}}'),
        ).lastrowid
        db.execute(
            "INSERT INTO pipeline_run_steps (run_id, step_type, sequence_no, status) VALUES (?, 'notification', 9, 'pending')",
            (run_id,),
        )
    monkeypatch.setattr(pipelines, "_summary_html", lambda run: "<p>synthetic</p>")
    _sending(False)

    pipelines._submit_summary(run_id)

    run = pipelines.get_pipeline_run(run_id)
    assert (run["notification_status"], run["notification_error"]) == ("not_sent", OFF)
    step = next(item for item in run["steps"] if item["step_type"] == "notification")
    assert (step["status"], step["error"]) == ("skipped", OFF)
    assert _dispatch_count() == 0


def test_scanner_failure_notices_are_not_queued_while_off(app_db, monkeypatch):
    notifications.save_notification_settings(["ops@example.test"])
    marked = []
    monkeypatch.setattr(notifications.modules, "mark_notification", lambda *args, **kwargs: marked.append(args))
    _sending(False)

    assert notifications.queue_notification([1, 2]) == {"status": "disabled", "dispatch_id": None, "reason": OFF}
    assert marked == []  # the runs are not recorded as notified
    with pytest.raises(ValueError) as refused:
        notifications.queue_test_notification()
    assert str(refused.value) == OFF
    assert _dispatch_count() == 0


def test_the_email_page_shows_the_notice_and_disables_only_sending():
    """Reads app.js: the Email page names the switch and keeps drafts usable."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    start = source.index("async function renderEmail")
    page = source[start:source.index("function _emailSelectedOwners", start)]
    assert "alertData.sending_enabled !== false" in page
    assert 'href="#settings/email"' in page and "Outlook drafts can still be created." in page
    assert 'email-server-send" data-owner="${esc(s.owner_name)}" ${missingEmail || !sendingOn ? "disabled" : ""}' in page
    assert 'id="email-alert-send-selected" ${sendingOn ? "" : "disabled"}' in page
    assert 'id="email-alert-draft-selected">' in page  # drafts are never disabled by the switch
    run_now = source[source.index('document.querySelectorAll(".rec-run")'):]
    run_now = run_now[:run_now.index("}));")]
    assert 'result.status === "not_sent" ? result.reason' in run_now  # never "No matching rows"
