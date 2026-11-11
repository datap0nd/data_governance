"""The settings registry: where a value comes from, what is accepted, and who may save."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import (
    database, flow_capacity, log_housekeeping, logging_setup, settings, settings_catalog,  # noqa: F401
    settings_registry as registry,
)
from app.routers import email_schedules, pipelines, system_flows, system_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(settings, "DB_PATH", str(path))
    database.init_db()
    for name in ("DG_EMAIL_REQUIRE_FRESH_PBI", "DG_EMAIL_MAX_PBI_SYNC_AGE_HOURS",
                 "DG_EMAIL_PBI_SYNC_GRACE_MINUTES", "DG_EMAIL_PBI_STALE_RETRY_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    return path


def host():
    return SimpleNamespace(state=SimpleNamespace(actor="Host PC", person_id=None, is_host=True))


def colleague():
    return SimpleNamespace(state=SimpleNamespace(actor="Maya", person_id=4, is_host=False))


# --- Declarations --------------------------------------------------------------

def test_every_setting_is_well_formed_and_sits_in_a_known_section():
    declared = registry.all_settings()
    assert len(declared) >= 15
    assert len({item.key for item in declared}) == len(declared)
    for item in declared:
        assert item.section in registry.SECTION_IDS and item.kind in registry.KINDS, item.key
        assert item.label and not item.label.endswith("."), item.key
        if item.kind in ("int", "float"):
            assert item.minimum is not None and item.maximum is not None, item.key
            assert item.minimum <= item.default <= item.maximum, item.key
        if item.kind == "choice":
            assert item.default in item.choices, item.key
            assert not item.choice_labels or len(item.choice_labels) == len(item.choices), item.key
        # The default itself must pass the setting's own validation.
        assert registry.validate(item, item.default) == item.default, item.key


def test_no_setting_is_declared_without_code_that_reads_it():
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in (ROOT / "app").rglob("*.py") if path.name != "settings_catalog.py"
    }
    for item in registry.all_settings():
        if item.reader is not None:
            continue  # composite settings wrap an accessor the app already calls
        needle = item.storage_key or item.key
        assert any(re.search(r"['\"]" + re.escape(needle) + r"['\"]", text) for text in sources.values()), (
            f"{item.key} is on the Settings page but nothing in app/ reads {needle!r}"
        )


# --- Validation ----------------------------------------------------------------

def test_numbers_are_whole_in_range_and_explain_the_range():
    keep = registry.definition("logs.keep_days")
    assert registry.validate(keep, 30) == 30
    assert registry.validate(keep, "30") == 30
    assert registry.validate(keep, 30.0) == 30
    for bad in (0, 366, 2.5, "abc", "", None, True, float("nan")):
        with pytest.raises(registry.SettingError) as refused:
            registry.validate(keep, bad)
        assert str(refused.value) == "Enter a whole number from 1 to 365."
    age = registry.definition("email.pbi_sync_max_age_hours")
    assert registry.validate(age, 12.5) == 12.5
    with pytest.raises(registry.SettingError) as refused:
        registry.validate(age, 500)
    assert str(refused.value) == "Enter a number from 1 to 168."


def test_other_kinds_accept_only_their_own_shape():
    assert registry.validate(registry.definition("updates.auto"), False) is False
    with pytest.raises(registry.SettingError):
        registry.validate(registry.definition("updates.auto"), "yes")
    assert registry.validate(registry.definition("scan.daily_time"), "07:05") == "07:05"
    for bad in ("7:05", "24:00", "07:60", 705):
        with pytest.raises(registry.SettingError):
            registry.validate(registry.definition("scan.daily_time"), bad)
    with pytest.raises(registry.SettingError):
        registry.validate(registry.definition("logs.request_log"), "verbose")
    recipients = registry.definition("scan.failure_recipients")
    assert registry.validate(recipients, " a@example.com , b@example.com;A@example.com\n") == [
        "a@example.com", "b@example.com",
    ]
    with pytest.raises(registry.SettingError) as refused:
        registry.validate(recipients, "a@example.com, not-an-address")
    assert str(refused.value) == "not-an-address is not an email address."


# --- Where a value comes from --------------------------------------------------

def test_saved_value_beats_environment_which_beats_the_default(app_db, monkeypatch):
    key = "email.pbi_sync_grace_minutes"
    assert registry.get(key) == 30.0
    monkeypatch.setenv("DG_EMAIL_PBI_SYNC_GRACE_MINUTES", "45")
    assert registry.get(key) == 45.0
    registry.save({key: 10})
    assert registry.get(key) == 10.0
    registry.save({key: None})  # reset: back to the environment, not the built-in default
    assert registry.get(key) == 45.0
    monkeypatch.delenv("DG_EMAIL_PBI_SYNC_GRACE_MINUTES")
    assert registry.get(key) == 30.0


def test_unusable_environment_and_stored_values_do_not_break_reads(app_db, monkeypatch):
    monkeypatch.setenv("DG_EMAIL_PBI_SYNC_GRACE_MINUTES", "soon")
    assert registry.get("email.pbi_sync_grace_minutes") == 30.0
    monkeypatch.setenv("DG_EMAIL_PBI_SYNC_GRACE_MINUTES", "100000")  # clamped, as the old variable was
    assert registry.get("email.pbi_sync_grace_minutes") == 240.0
    monkeypatch.setenv("DG_EMAIL_REQUIRE_FRESH_PBI", "off")
    assert registry.get("email.require_fresh_pbi") is False
    settings.set_setting("logs.keep_days", "not a number")
    assert registry.get("logs.keep_days") == 14
    settings.set_setting("logs.keep_days", "9999")
    assert registry.get("logs.keep_days") == 365


def test_a_database_that_cannot_be_read_falls_back_to_defaults(app_db, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(settings, "get_setting", broken)
    assert registry.get("logs.keep_days") == 14


# --- Saving --------------------------------------------------------------------

def test_one_bad_value_saves_nothing_and_names_each_problem(app_db):
    with pytest.raises(registry.SettingsRejected) as refused:
        registry.save({"logs.keep_days": 30, "logs.max_total_mb": 1, "no.such.setting": 5})
    assert refused.value.errors == {
        "logs.max_total_mb": "Enter a whole number from 50 to 102,400.",
        "no.such.setting": "This setting does not exist.",
    }
    assert registry.get("logs.keep_days") == 14
    assert settings.get_setting("logs.keep_days") is None


def test_save_reports_only_what_changed_and_tells_listeners(app_db, request_log_mode):
    calls = []
    registry.subscribe(("logs.keep_days",), lambda: calls.append("keep_days"))
    changed = registry.save({"logs.keep_days": 30, "logs.max_total_mb": 1024, "logs.request_log": "errors"})
    assert [(item["key"], item["old"], item["new"]) for item in changed] == [
        ("logs.keep_days", 14, 30), ("logs.request_log", "changes", "errors"),
    ]
    assert calls == ["keep_days"]
    assert logging_setup.request_log_mode() == "errors"  # applied at once, no restart
    assert (log_housekeeping.keep_days(), log_housekeeping.max_total_mb()) == (30, 1024)
    assert registry.save({"logs.keep_days": 30}) == []
    assert calls == ["keep_days"]


@pytest.fixture
def request_log_mode():
    before = logging_setup.request_log_mode()
    yield
    logging_setup.set_request_log_mode(before)


def test_settings_that_already_existed_keep_their_storage(app_db):
    registry.save({"flows.total_workers": 20, "flows.browser": "msedge", "updates.auto": False,
                   "flows.recording_wait_seconds": 25})
    with database.get_db() as db:
        assert flow_capacity.total_capacity(db) == 20
        state = system_flows._state(db)
    assert (state["browser_channel"], state["recording_wait_seconds"]) == ("msedge", 25)
    assert settings.get_setting("automatic_main_updates_enabled") == "0"
    # And the other way round: a value saved by the older page shows here.
    settings.set_setting("flows_headed_capacity", "3")
    assert registry.get("flows.visible_windows") == 3


def test_composite_settings_read_and_write_through_their_existing_store(app_db):
    assert registry.get("scan.daily_time") == registry.definition("scan.daily_time").default
    registry.save({"scan.daily_time": "06:40", "scan.failure_recipients": "ops@example.com, bi@example.com",
                   "insights.weekday": "friday", "insights.samples": False})
    assert settings.get_overall_refresh_time()["time"] == "06:40"
    from app import pipeline_insights_settings
    from app.scanner import notifications

    assert notifications.get_notification_settings()["recipients"] == ["ops@example.com", "bi@example.com"]
    insights = pipeline_insights_settings.get_pipeline_insights_settings()
    assert (insights.weekday, insights.samples_scheduled, insights.explanations_scheduled, insights.time) == (
        "friday", False, True, "10:00",
    )
    registry.save({"scan.failure_recipients": None})
    assert notifications.get_notification_settings()["recipients"] == []


def test_snapshot_reads_several_settings_together(app_db):
    registry.save({"logs.keep_days": 21})
    assert registry.snapshot(["logs.keep_days", "logs.max_total_mb"]) == {
        "logs.keep_days": 21, "logs.max_total_mb": 1024,
    }
    with pytest.raises(KeyError):
        registry.snapshot(["logs.keep_days", "nope"])


# --- The settings are what the code uses ---------------------------------------

def test_scheduled_email_gate_follows_the_setting_without_a_restart(app_db, monkeypatch):
    asked = []
    monkeypatch.setattr(email_schedules, "pbi_sync_freshness",
                        lambda hours, kind: asked.append(hours) or {"fresh": True})
    monkeypatch.setattr(email_schedules, "_required_pbi_sync_after", lambda: None)
    schedule = {"schedule_key": email_schedules.TASK_SUMMARY_KEY, "content_types": None}

    email_schedules._require_fresh_pbi_for_scheduled_email(schedule)
    assert asked == [24.0]
    registry.save({"email.pbi_sync_max_age_hours": 6})
    email_schedules._require_fresh_pbi_for_scheduled_email(schedule)
    assert asked == [24.0, 6.0]
    registry.save({"email.require_fresh_pbi": False})
    email_schedules._require_fresh_pbi_for_scheduled_email(schedule)
    assert asked == [24.0, 6.0]  # the gate is off: Power BI is not consulted


def test_pipeline_scan_age_follows_the_setting(app_db):
    assert pipelines._identity_max_age().total_seconds() == 48 * 3600
    registry.save({"pipelines.scan_max_age_hours": 72})
    assert pipelines._identity_max_age().total_seconds() == 72 * 3600


# --- The page's API ------------------------------------------------------------

def test_anyone_reads_settings_but_only_the_host_pc_is_told_it_can_edit(app_db):
    seen = system_settings.get_settings(colleague())
    assert seen["can_edit"] is False
    assert system_settings.get_settings(host())["can_edit"] is True
    assert [section["id"] for section in seen["sections"]] == list(registry.SECTION_IDS)
    rows = {row["key"]: row for section in seen["sections"] for group in section["groups"] for row in group["settings"]}
    keep = rows["logs.keep_days"]
    assert (keep["value"], keep["default"], keep["source"], keep["minimum"], keep["maximum"], keep["unit"]) == (
        14, 14, "default", 1, 365, "days",
    )
    assert keep["applies_text"] == "Applies immediately"
    assert rows["flows.browser"]["choice_labels"] == ["Google Chrome", "Microsoft Edge"]
    assert rows["flows.recording_wait_seconds"]["applies_text"] == "Applies to new runs"


def test_a_colleague_cannot_save_and_nothing_changes(app_db):
    with pytest.raises(HTTPException) as refused:
        system_settings.save_settings(system_settings.SettingsWrite(values={"logs.keep_days": 2}), colleague())
    assert refused.value.status_code == 403
    assert "Only the host PC can change settings." in refused.value.detail
    assert registry.get("logs.keep_days") == 14
    for route, body in (
        (system_flows.save_capacity, system_flows.CapacityWrite(total_capacity=5)),
        (system_flows.save_portal_capacity, None),
    ):
        with pytest.raises(HTTPException) as legacy:
            route(body, colleague()) if body is not None else route(1, system_flows.PortalCapacityWrite(capacity=2), colleague())
        assert legacy.value.status_code == 403


def test_the_host_pc_saves_and_each_change_is_in_the_event_log(app_db):
    result = system_settings.save_settings(
        system_settings.SettingsWrite(values={"logs.keep_days": 30, "updates.auto": False, "logs.max_total_mb": 1024}),
        host(),
    )
    assert result["changed"] == ["logs.keep_days", "updates.auto"]
    rows = {row["key"]: row for section in result["sections"] for group in section["groups"] for row in group["settings"]}
    assert (rows["logs.keep_days"]["value"], rows["logs.keep_days"]["source"]) == (30, "app")
    with database.get_db() as db:
        events = [tuple(row) for row in db.execute(
            "SELECT entity_name, action, detail, actor FROM event_log ORDER BY id")]
    assert events == [
        ("Settings", "setting_changed", "Delete logs not written to for: 14 to 30", "Host PC"),
        ("Settings", "setting_changed", "Install new versions automatically: on to off", "Host PC"),
    ]


def test_a_rejected_save_answers_with_a_message_per_field_and_saves_nothing(app_db):
    response = system_settings.save_settings(
        system_settings.SettingsWrite(values={"logs.keep_days": 30, "scan.daily_time": "25:00"}), host(),
    )
    assert response.status_code == 422
    import json

    payload = json.loads(response.body)
    assert payload["errors"] == {"scan.daily_time": "Choose a time as HH:MM."}
    assert payload["detail"].startswith("Nothing was saved.")
    assert registry.get("logs.keep_days") == 14
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 0
