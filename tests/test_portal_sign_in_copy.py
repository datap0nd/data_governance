"""The encrypted portal sign-in file as a copy of the settings-file sign-in.

Flow scripts read the portal sign-in from the encrypted file beside the worker
browser profiles. Since the sign-in moved to the settings file that file was
no longer written, so those scripts found nothing, or an older password.
Metronome now rewrites it from the settings file.

Fictional sign-ins only. Windows' protection is stood in for by a reversible
scramble, so every case runs on any system and none touches a real account.
"""
import json
import logging
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import config, env_file, flow_capacity, flow_credentials, flow_worker, settings_registry as registry
from app.routers import flows, system_settings
from test_settings_connections import colleague, host, installed  # noqa: F401
from tools import flow_setup_sign_in, sync_flow_sso_password

USER = "maya.k"
PASSWORD = "fictional-Pa55 word!"
OLDER = "fictional-older-password"


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(flow_credentials.platform, "system", lambda: "Windows")
    monkeypatch.setattr(flow_credentials, "_dpapi", lambda data, protect: bytes(value ^ 0xA5 for value in data))
    monkeypatch.setattr(flow_credentials.time, "sleep", lambda seconds: None)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """A settings file with no sign-in yet, and an empty folder for the encrypted file."""
    path = tmp_path / ".env"
    path.write_text("# Metronome settings\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    monkeypatch.setenv("METRONOME_FLOW_PROFILE", str(tmp_path / "profile"))
    return path


def _set(path, user=USER, password=PASSWORD):
    env_file.write_value(path, "DG_PORTAL_USER", user)
    env_file.write_value(path, "DG_PORTAL_PASSWORD", password)


def _file():
    return flow_credentials.credential_path()


def _held():
    """What a Flow script finds when it opens the encrypted file."""
    return flow_credentials.load_asap_credentials(flow_credentials.flow_profile_dir())


# --- What the copy holds ------------------------------------------------------------

def test_a_missing_file_is_written_from_the_settings_file_and_is_not_plain_text(windows, settings_file):
    _set(settings_file)

    assert flow_credentials.sync_credential_file() == "written"

    held = _held()
    assert (held["username"], held["password"]) == (USER, PASSWORD)
    raw = _file().read_text(encoding="utf-8")
    assert json.loads(raw)["format"] == "dpapi"  # the layout scripts have always read
    assert PASSWORD not in raw and USER not in raw
    assert flow_credentials.credential_file_is_current_copy() is True


def test_a_copy_already_in_step_is_left_byte_for_byte(windows, settings_file):
    _set(settings_file)
    flow_credentials.sync_credential_file()
    before = _file().read_bytes()

    assert flow_credentials.sync_credential_file() == "unchanged"
    assert _file().read_bytes() == before


def test_a_password_corrected_in_the_settings_file_reaches_the_copy(windows, settings_file):
    _set(settings_file, password=OLDER)
    flow_credentials.sync_credential_file()

    env_file.write_value(settings_file, "DG_PORTAL_PASSWORD", PASSWORD)  # typed by hand
    assert flow_credentials.credential_file_is_current_copy() is False
    assert flow_credentials.sync_credential_file() == "written"
    assert _held()["password"] == PASSWORD


@pytest.mark.parametrize("older_password", [OLDER, PASSWORD])
def test_an_older_stand_alone_sign_in_becomes_the_copy(windows, settings_file, older_password):
    # Saved by an earlier version, perhaps with a password that has since changed.
    flow_credentials.save_asap_credentials("someone.else", older_password)
    _set(settings_file)

    assert flow_credentials.sync_credential_file() == "written"
    assert (_held()["username"], _held()["password"]) == (USER, PASSWORD)
    assert flow_credentials.sync_credential_file() == "unchanged"


@pytest.mark.parametrize("content", ["not json", '{"format": "dpapi", "data": "AAAA"}', '{"format": "plain"}'])
def test_a_file_that_cannot_be_read_is_replaced(windows, settings_file, content):
    _set(settings_file)
    _file().parent.mkdir(parents=True)
    _file().write_text(content, encoding="utf-8")

    assert flow_credentials.sync_credential_file() == "written"
    assert _held()["password"] == PASSWORD


# --- When the settings file holds no sign-in ----------------------------------------

def test_removing_the_sign_in_removes_its_copy(windows, settings_file):
    _set(settings_file)
    flow_credentials.sync_credential_file()

    env_file.write_value(settings_file, "DG_PORTAL_PASSWORD", None)
    assert flow_credentials.sync_credential_file() == "removed"
    assert not _file().exists()
    # Workers then have no sign-in at all, instead of the one just removed.
    assert flow_credentials.load_asap_credentials() is None
    assert flow_credentials.sync_credential_file() == "not_set"


def test_an_older_stand_alone_sign_in_is_never_removed(windows, settings_file):
    flow_credentials.save_asap_credentials(USER, OLDER)
    before = _file().read_bytes()

    assert flow_credentials.sync_credential_file() == "not_set"
    assert _file().read_bytes() == before
    assert flow_credentials.load_asap_credentials()["password"] == OLDER


@pytest.mark.parametrize("problem", ["missing", "unreadable"])
def test_a_settings_file_that_cannot_be_read_does_not_remove_the_copy(windows, settings_file, monkeypatch, problem):
    _set(settings_file)
    flow_credentials.sync_credential_file()
    before = _file().read_bytes()

    if problem == "missing":
        settings_file.unlink()
    else:  # being rewritten, or locked, at this moment
        monkeypatch.setattr(env_file, "read", lambda path: ({}, {"exists": True, "error": "PermissionError"}))
    assert flow_credentials.sync_credential_file() == "not_set"
    assert _file().read_bytes() == before


def test_nothing_is_written_where_windows_protection_does_not_exist(settings_file, monkeypatch):
    monkeypatch.setattr(flow_credentials.platform, "system", lambda: "Linux")
    _set(settings_file)

    assert flow_credentials.sync_credential_file() == "unsupported"
    assert not _file().exists()
    assert flow_credentials.credential_file_is_current_copy() is False
    assert flow_credentials.load_asap_credentials()["password"] == PASSWORD  # workers are unaffected


# --- Several processes writing at once ----------------------------------------------

def test_a_file_a_reader_holds_open_is_tried_again(windows, settings_file, monkeypatch):
    _set(settings_file)
    replace, refusals = Path.replace, []

    def held_open(self, target):
        if len(refusals) < 2:
            refusals.append(target)
            raise PermissionError(13, "The process cannot access the file")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", held_open)
    assert flow_credentials.sync_credential_file() == "written"
    assert len(refusals) == 2 and _held()["password"] == PASSWORD
    assert [path.name for path in _file().parent.iterdir()] == [flow_credentials.CREDENTIAL_FILENAME]


def test_a_copy_another_worker_wrote_in_the_meantime_is_accepted(windows, settings_file, monkeypatch):
    _set(settings_file)
    save = flow_credentials.save_asap_credentials

    def beaten_to_it(*args, **kwargs):
        save(*args, **kwargs)  # the other worker's write lands...
        raise PermissionError(13, "The process cannot access the file")  # ...and ours is refused

    monkeypatch.setattr(flow_credentials, "save_asap_credentials", beaten_to_it)
    assert flow_credentials.sync_credential_file() == "unchanged"


def test_a_write_that_keeps_failing_is_reported_and_leaves_the_file_as_it_was(windows, settings_file, monkeypatch):
    flow_credentials.save_asap_credentials(USER, OLDER)
    before = _file().read_bytes()
    _set(settings_file)

    def refuse(self, target):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(PermissionError):
        flow_credentials.sync_credential_file()
    assert _file().read_bytes() == before
    # No half-written file is left beside it, whichever process wrote it.
    assert [path.name for path in _file().parent.iterdir()] == [flow_credentials.CREDENTIAL_FILENAME]


def test_each_process_writes_through_its_own_temporary_file(windows, settings_file, monkeypatch):
    _set(settings_file)
    written = []
    replace = Path.replace
    monkeypatch.setattr(Path, "replace", lambda self, target: written.append(self.name) or replace(self, target))

    flow_credentials.sync_credential_file()
    monkeypatch.setattr(os, "getpid", lambda: 424242)
    env_file.write_value(settings_file, "DG_PORTAL_PASSWORD", OLDER)
    flow_credentials.sync_credential_file()

    assert len(set(written)) == 2 and "424242" in written[1]


# --- When Metronome writes it -------------------------------------------------------

@pytest.fixture
def fresh_worker(monkeypatch):
    monkeypatch.setattr(flow_worker, "_credential_copy_signature", object())
    monkeypatch.setattr(flow_worker, "_credential_copy_problem", None)
    calls = []
    sync = flow_credentials.sync_credential_file
    monkeypatch.setattr(flow_credentials, "sync_credential_file", lambda: calls.append(1) or sync())
    return calls


def test_a_worker_writes_the_copy_when_it_starts_and_again_only_after_the_settings_file_changes(
        windows, settings_file, fresh_worker, capsys):
    _set(settings_file)

    flow_worker._keep_credential_file_current()  # the worker starts
    assert _held()["password"] == PASSWORD
    assert "Wrote the encrypted copy of the portal sign-in" in capsys.readouterr().out

    for _loop in range(3):  # idle loops cost a look at the file's date, nothing more
        flow_worker._keep_credential_file_current()
    assert len(fresh_worker) == 1

    env_file.write_value(settings_file, "DG_PORTAL_PASSWORD", OLDER + " typed by hand")
    flow_worker._keep_credential_file_current()
    assert len(fresh_worker) == 2 and _held()["password"] == OLDER + " typed by hand"
    printed = capsys.readouterr()
    assert PASSWORD not in printed.out + printed.err and OLDER not in printed.out + printed.err


def test_a_copy_that_cannot_be_written_never_stops_a_worker_and_is_tried_again(
        windows, settings_file, fresh_worker, monkeypatch, capsys):
    _set(settings_file)
    replace = Path.replace

    def refuse(self, target):
        raise PermissionError(13, f"cannot replace, {PASSWORD}")

    monkeypatch.setattr(Path, "replace", refuse)
    flow_worker._keep_credential_file_current()
    flow_worker._keep_credential_file_current()
    reported = capsys.readouterr().err
    assert reported.count("Could not write the encrypted copy of the portal sign-in (PermissionError)") == 1
    assert PASSWORD not in reported
    assert not _file().exists()

    monkeypatch.setattr(Path, "replace", replace)  # whatever held the file lets go
    flow_worker._keep_credential_file_current()
    assert _held()["password"] == PASSWORD


def test_the_web_service_writes_the_copy_when_it_starts_and_a_failure_only_logs_its_kind(
        windows, settings_file, monkeypatch, caplog):
    from app import main

    _set(settings_file)
    main._sync_portal_sign_in_copy()
    assert _held()["password"] == PASSWORD

    def fail():
        raise OSError(f"disk says no, {PASSWORD}")

    monkeypatch.setattr(flow_credentials, "sync_credential_file", fail)
    with caplog.at_level(logging.WARNING):
        main._sync_portal_sign_in_copy()  # start-up carries on
    assert "was not written (OSError)" in caplog.text and PASSWORD not in caplog.text


def test_saving_or_clearing_the_sign_in_on_the_settings_page_moves_the_copy_at_once(installed, windows, monkeypatch):  # noqa: F811
    monkeypatch.setenv("METRONOME_FLOW_PROFILE", str(installed.parent / "profile"))

    system_settings.save_settings(system_settings.SettingsWrite(values={"conn.portal_user": USER}), host())
    assert not _file().exists()  # a user ID alone is not a sign-in
    system_settings.replace_secret("conn.portal_password", system_settings.SecretWrite(value=PASSWORD), host())
    assert (_held()["username"], _held()["password"]) == (USER, PASSWORD)
    state = system_settings.get_portal_login()
    assert (state["source"], state["copy_current"], state["configured"]) == ("settings_file", True, True)
    assert PASSWORD not in json.dumps(state)

    system_settings.replace_secret("conn.portal_password", system_settings.SecretWrite(value="the new one"), host())
    assert _held()["password"] == "the new one"

    system_settings.clear_secret("conn.portal_password", host())
    assert not _file().exists()
    state = system_settings.get_portal_login()
    assert (state["source"], state["copy_current"], state["configured"]) == (None, False, False)


def test_a_copy_that_could_not_be_written_does_not_fail_the_save_and_shows_on_the_page(
        installed, windows, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setenv("METRONOME_FLOW_PROFILE", str(installed.parent / "profile"))
    system_settings.save_settings(system_settings.SettingsWrite(values={"conn.portal_user": USER}), host())

    def refuse(self, target):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(Path, "replace", refuse)
    with caplog.at_level(logging.WARNING):
        system_settings.replace_secret("conn.portal_password", system_settings.SecretWrite(value=PASSWORD), host())

    assert env_file.read(installed)[0]["DG_PORTAL_PASSWORD"] == PASSWORD  # the save itself held
    assert "was not written (PermissionError)" in caplog.text and PASSWORD not in caplog.text
    assert system_settings.get_portal_login()["copy_current"] is False


def test_the_copy_cannot_be_replaced_or_cleared_on_its_own(installed, windows, monkeypatch):  # noqa: F811
    # Anything else in the file would be read by Flow scripts until the next start.
    monkeypatch.setenv("METRONOME_FLOW_PROFILE", str(installed.parent / "profile"))
    registry.save({"conn.portal_user": USER})
    registry.save_secret("conn.portal_password", PASSWORD)
    flow_credentials.sync_credential_file()
    before = _file().read_bytes()
    other = system_settings.PortalLoginWrite(username="someone.else", password=OLDER)
    with flows.get_db() as db:
        site_id = db.execute(
            "INSERT INTO flow_sites(name, adapter, base_url) VALUES ('ASAP', 'asap_portal', 'https://portal.example.test')"
        ).lastrowid

    for attempt in (
        lambda: system_settings.save_portal_login(other, host()),
        lambda: system_settings.clear_portal_login(host()),
        lambda: flows.configure_site_credentials(
            site_id, flows.CredentialWrite(username="someone.else", password=OLDER), host()),
    ):
        with pytest.raises(HTTPException) as refused:
            attempt()
        assert refused.value.status_code == 409
        assert "Portal sign-in" in refused.value.detail and OLDER not in refused.value.detail
    assert _file().read_bytes() == before

    # Without a sign-in in the settings file the older block works as it always has.
    registry.save_secret("conn.portal_password", None)
    flow_credentials.sync_credential_file()
    assert system_settings.save_portal_login(other, host())["configured"] is True
    assert flow_credentials.load_asap_credentials()["username"] == "someone.else"
    assert system_settings.clear_portal_login(host())["configured"] is False


def test_setup_does_not_put_the_windows_password_into_the_copy(windows, settings_file, monkeypatch):
    # setup.ps1 refreshes the older encrypted sign-in from DG_SVC_PASSWORD.
    _set(settings_file)
    flow_credentials.sync_credential_file()
    before = _file().read_bytes()
    monkeypatch.setenv("DG_SVC_PASSWORD", "fictional-windows-password")

    message = sync_flow_sso_password.sync_password(flow_credentials.flow_profile_dir())

    assert _file().read_bytes() == before
    assert "settings file" in message and "fictional-windows-password" not in message


# --- How many worker browsers setup signs in ----------------------------------------

def test_setup_signs_in_two_worker_browsers_of_each_kind_unless_settings_says_otherwise(installed, monkeypatch):  # noqa: F811
    assert flow_capacity.setup_sign_in_browsers() == 2
    assert flow_setup_sign_in.browsers() == 2

    registry.save({"flows.setup_sign_in_browsers": 5})
    assert flow_setup_sign_in.browsers() == 5

    # Setup never stops over this: a value it cannot read, or cannot use, is the default.
    monkeypatch.setattr(flow_capacity, "setup_sign_in_browsers", lambda: 99)
    assert flow_setup_sign_in.browsers() == 2

    def unreadable():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(flow_capacity, "setup_sign_in_browsers", unreadable)
    assert flow_setup_sign_in.browsers() == 2
