"""Connections and passwords on the Settings page: saved to the settings file, applied live, never shown."""
import json
import os
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import config, connection_tests, database, settings, settings_registry as registry
from app.routers import system_settings

SECRET = "s3cr3t-Pa55 word!"
FILE_NAMES = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "DG_UPLOAD_PGHOST", "DG_UPLOAD_PGPORT",
              "DG_UPLOAD_PGDATABASE", "DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD", "DG_PBI_WORKSPACE",
              "DG_PBI_CLIENT_SECRET", "DG_GITHUB_TOKEN", "bio_path", "BIO_PATH", "DG_TMDL_ROOT", "DG_REPORTS_PATH",
              "DG_DEFAULT_REPORTS_PATH", "usage_files_path", "USAGE_FILES_PATH", "DG_USAGE_FILES_PATH", "DG_PYTHON_EXE")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A fresh app database and an empty settings file, with config pointed at both."""
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    database.init_db()
    environment = dict(os.environ)
    live = {name: getattr(config, name) for name in config.LIVE_NAMES}
    for name in FILE_NAMES:
        os.environ.pop(name, None)
    path = tmp_path / ".env"
    path.write_text("# Metronome settings\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    monkeypatch.setattr(config, "_names_from_file", set())
    monkeypatch.setattr(config, "_env_file_signature", None)
    monkeypatch.setattr(config, "_STARTING_ENVIRONMENT", dict(os.environ))
    config.refresh()
    yield path
    monkeypatch.undo()
    os.environ.clear()
    os.environ.update(environment)
    for name, value in live.items():
        current = getattr(config, name)
        if current != value:
            for module in list(sys.modules.values()):
                if module is not None and config._is_metronome_module(module) and module.__dict__.get(name) == current:
                    setattr(module, name, value)
            setattr(config, name, value)


def host():
    return SimpleNamespace(state=SimpleNamespace(actor="Host PC", person_id=None, is_host=True))


def colleague():
    return SimpleNamespace(state=SimpleNamespace(actor="Maya", person_id=4, is_host=False))


def rows(state):
    return {row["key"]: row for section in state["sections"] for group in section["groups"] for row in group["settings"]}


def test_a_saved_connection_reaches_the_file_config_and_importing_modules_at_once(installed):
    importer = types.ModuleType("app._connections_probe")
    importer.PGHOST, importer.UPLOAD_PGHOST = config.PGHOST, config.UPLOAD_PGHOST
    sys.modules[importer.__name__] = importer
    try:
        changed = registry.save({"conn.probe_host": "pg.example.internal", "conn.probe_port": 6543,
                                 "conn.probe_user": "metronome_ro", "conn.pbi_workspace": "Supply Chain BI"})
        assert {item["key"] for item in changed} == {"conn.probe_host", "conn.probe_port", "conn.probe_user", "conn.pbi_workspace"}
        text = installed.read_text(encoding="utf-8")
        assert 'PGHOST="pg.example.internal"' in text and 'PGPORT="6543"' in text and text.startswith("# Metronome settings\n")
        assert (config.PGHOST, config.PGPORT, config.PGUSER, config.PBI_WORKSPACE) == (
            "pg.example.internal", "6543", "metronome_ro", "Supply Chain BI")
        # The upload host falls back to the probe host, and modules that imported either follow.
        assert (config.UPLOAD_PGHOST, importer.PGHOST, importer.UPLOAD_PGHOST) == (
            "pg.example.internal", "pg.example.internal", "pg.example.internal")

        registry.save({"conn.upload_host": "upload.example.internal"})
        assert (config.UPLOAD_PGHOST, importer.UPLOAD_PGHOST, importer.PGHOST) == (
            "upload.example.internal", "upload.example.internal", "pg.example.internal")
    finally:
        sys.modules.pop(importer.__name__, None)


def test_reset_and_empty_remove_the_line_and_fall_back(installed, monkeypatch):
    registry.save({"conn.probe_host": "pg.example.internal", "conn.upload_port": 6000, "conn.probe_port": 6543})
    assert (config.PGPORT, config.UPLOAD_PGPORT) == ("6543", "6000")
    registry.save({"conn.upload_port": ""})  # empty: same as the probe port again
    assert config.UPLOAD_PGPORT == "6543" and "DG_UPLOAD_PGPORT" not in installed.read_text(encoding="utf-8")
    registry.save({"conn.probe_port": None, "conn.probe_host": None})
    assert (config.PGPORT, config.PGHOST) == ("5432", "")
    assert "PGHOST" not in installed.read_text(encoding="utf-8")
    with pytest.raises(registry.SettingsRejected) as refused:
        registry.save({"conn.probe_port": 70000})
    assert refused.value.errors == {"conn.probe_port": "Enter a whole number from 1 to 65535."}


def test_where_a_value_comes_from_is_named(installed, monkeypatch):
    monkeypatch.setitem(config._STARTING_ENVIRONMENT, "DG_PBI_WORKSPACE", "from the service")
    os.environ["DG_PBI_WORKSPACE"] = "from the service"
    seen = rows(system_settings.get_settings(host()))
    assert (seen["conn.pbi_workspace"]["value"], seen["conn.pbi_workspace"]["source_text"]) == (
        "from the service", "From the Windows environment")
    assert seen["conn.probe_host"]["source_text"] == "Not set"
    registry.save({"conn.pbi_workspace": "Supply Chain BI"})
    seen = rows(system_settings.get_settings(host()))
    assert (seen["conn.pbi_workspace"]["value"], seen["conn.pbi_workspace"]["source_text"]) == ("Supply Chain BI", "Set here")
    registry.save({"conn.pbi_workspace": None})
    assert rows(system_settings.get_settings(host()))["conn.pbi_workspace"]["value"] == "from the service"


def test_folders_write_the_name_that_wins_and_show_the_folder_in_force(installed, monkeypatch):
    monkeypatch.setitem(config._STARTING_ENVIRONMENT, "DG_REPORTS_PATH", r"\\share\old reports")
    os.environ["DG_REPORTS_PATH"] = r"\\share\old reports"
    config.refresh()
    assert registry.get("folders.reports") == r"\\share\old reports"
    registry.save({"folders.reports": r"D:\BI\Reports", "folders.usage": r"D:\BI\Usage exports",
                   "folders.python": r"C:\Python313\python.exe"})
    assert (config.REPORTS_PATH, config.USAGE_FILES_PATH) == (r"D:\BI\Reports", r"D:\BI\Usage exports")
    # The scanner's root follows; a Windows path is only absolute on Windows.
    assert config.TMDL_ROOT.endswith(r"D:\BI\Reports")
    assert os.environ["DG_PYTHON_EXE"] == r"C:\Python313\python.exe"
    with pytest.raises(registry.SettingsRejected) as refused:
        registry.save({"folders.reports": "Reports"})
    assert "Enter a full path" in refused.value.errors["folders.reports"]
    registry.save({"folders.reports": ""})
    assert config.REPORTS_PATH == r"\\share\old reports"


def test_a_password_is_written_applied_and_never_returned_or_logged(installed):
    response = system_settings.replace_secret("conn.upload_password", system_settings.SecretWrite(value=SECRET), host())
    assert config.UPLOAD_PGPASSWORD == SECRET and registry.secret("conn.upload_password") == SECRET
    assert f'DG_UPLOAD_PGPASSWORD="{SECRET}"' in installed.read_text(encoding="utf-8")
    assert SECRET not in json.dumps(response)
    row = rows(response)["conn.upload_password"]
    assert (row["saved"], row["value"], row["source_text"]) == (True, None, "Set here")
    assert SECRET not in json.dumps(system_settings.get_settings(colleague()))
    with database.get_db() as db:
        events = [tuple(item) for item in db.execute("SELECT action, detail, actor FROM event_log ORDER BY id")]
    assert events == [("secret_replaced", "Shared password replaced", "Host PC")]

    cleared = system_settings.clear_secret("conn.upload_password", host())
    assert rows(cleared)["conn.upload_password"]["saved"] is False
    assert config.UPLOAD_PGPASSWORD == "" and registry.secret("conn.upload_password") is None
    assert "DG_UPLOAD_PGPASSWORD" not in installed.read_text(encoding="utf-8")


def test_passwords_cannot_be_saved_with_the_other_settings_or_with_a_line_break(installed):
    with pytest.raises(registry.SettingsRejected) as refused:
        registry.save({"conn.probe_password": SECRET, "conn.probe_host": "pg.example.internal"})
    assert list(refused.value.errors) == ["conn.probe_password"]
    assert config.PGHOST == "" and config.PGPASSWORD == ""
    before = installed.read_bytes()
    response = system_settings.replace_secret(
        "conn.probe_password", system_settings.SecretWrite(value="x\u2028PGHOST=elsewhere"), host())
    assert response.status_code == 422 and installed.read_bytes() == before
    with pytest.raises(HTTPException) as missing:
        system_settings.replace_secret("no.such.secret", system_settings.SecretWrite(value="x"), host())
    assert missing.value.status_code == 404
    not_a_secret = system_settings.replace_secret("conn.probe_host", system_settings.SecretWrite(value="x"), host())
    assert not_a_secret.status_code == 422


def test_only_the_host_pc_changes_passwords_or_tests_connections(installed):
    for call in (
        lambda: system_settings.replace_secret("conn.probe_password", system_settings.SecretWrite(value=SECRET), colleague()),
        lambda: system_settings.clear_secret("conn.probe_password", colleague()),
        lambda: system_settings.test_connection("probe", colleague()),
        lambda: system_settings.save_settings(system_settings.SettingsWrite(values={"conn.probe_host": "elsewhere"}), colleague()),
    ):
        with pytest.raises(HTTPException) as refused:
            call()
        assert refused.value.status_code == 403
    assert config.PGPASSWORD == "" and config.PGHOST == ""
    assert installed.read_text(encoding="utf-8") == "# Metronome settings\n"


def test_without_a_settings_file_nothing_is_saved_and_the_reason_is_given(installed, monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE", None)
    with pytest.raises(registry.SettingsRejected) as refused:
        registry.save({"conn.probe_host": "pg.example.internal"})
    assert "has no settings file" in refused.value.errors["conn.probe_host"]
    response = system_settings.replace_secret("conn.probe_password", system_settings.SecretWrite(value=SECRET), host())
    assert response.status_code == 422 and "has no settings file" in json.loads(response.body)["detail"]


class _Cursor:
    def __init__(self, row):
        self.row, self.statements = row, []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, statement):
        self.statements.append(statement)

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_object, self.session, self.closed = _Cursor(row), None, False

    def set_session(self, **options):
        self.session = options

    def cursor(self):
        return self.cursor_object

    def close(self):
        self.closed = True


def _fake_psycopg2(monkeypatch, row=None, error=None):
    opened = []

    def connect(**kwargs):
        if error:
            raise error
        opened.append((kwargs, _Connection(row)))
        return opened[-1][1]

    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=connect))
    return opened


def test_probe_test_reads_only_and_refuses_an_account_that_can_write(installed, monkeypatch):
    assert connection_tests.probe() == {"ok": False, "message": "Not tested: the probe host and user and password are not set."}
    registry.save({"conn.probe_host": "pg.example.internal", "conn.probe_user": "metronome_ro", "conn.probe_database": "analytics"})
    registry.save_secret("conn.probe_password", SECRET)

    opened = _fake_psycopg2(monkeypatch, row=("metronome_ro", False, False))
    result = system_settings.test_connection("probe", host())
    assert result == {"ok": True, "message": "Signed in to analytics on pg.example.internal as metronome_ro. "
                                              "The account cannot create objects in this database."}
    kwargs, connection = opened[0]
    assert (kwargs["host"], kwargs["port"], kwargs["user"], kwargs["password"], kwargs["database"]) == (
        "pg.example.internal", 5432, "metronome_ro", SECRET, "analytics")
    assert connection.session == {"readonly": True, "autocommit": True} and connection.closed
    statement = " ".join(connection.cursor_object.statements).upper()
    assert statement.lstrip().startswith("SELECT") and not any(
        word in statement for word in ("INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "DROP ", "ALTER "))

    _fake_psycopg2(monkeypatch, row=("metronome_ro", True, False))
    writable = connection_tests.probe()
    assert writable["ok"] is False and "can create objects in this database" in writable["message"]
    _fake_psycopg2(monkeypatch, row=("postgres", False, True))
    assert "is a superuser" in connection_tests.probe()["message"]


def test_a_failed_test_explains_itself_without_the_password(installed, monkeypatch):
    registry.save({"conn.probe_host": "pg.example.internal", "conn.probe_user": "metronome_ro"})
    registry.save_secret("conn.probe_password", SECRET)
    _fake_psycopg2(monkeypatch, error=RuntimeError(f'password authentication failed for "{SECRET}"\nat pg.example.internal'))
    failed = connection_tests.probe()
    assert failed["ok"] is False and SECRET not in failed["message"] and "[redacted]" in failed["message"]
    assert failed["message"].startswith("Could not sign in to postgres on pg.example.internal:5432 as metronome_ro.")
    with pytest.raises(HTTPException) as unknown:
        system_settings.test_connection("nothing", host())
    assert unknown.value.status_code == 404
    assert connection_tests.upload()["message"].startswith("Not tested: these are not set:")


# --- Portal sign-in -----------------------------------------------------------------

@pytest.fixture
def portal_store(tmp_path, monkeypatch):
    """Stand in for the Windows-protected file: record what would be stored."""
    from app import flow_credentials

    monkeypatch.setenv("METRONOME_FLOW_PROFILE", str(tmp_path / "profile"))
    stored = {}

    def save(username, password, profile_dir=None):
        if not username.strip() or not password:
            raise ValueError("ASAP username and password are required.")
        stored.update(username=username.strip(), password=password)
        path = flow_credentials.credential_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("protected", encoding="utf-8")
        return {"configured": True, "updated_at": "2026-11-11T12:00:00+04:00"}

    def status(profile_dir=None):
        saved = flow_credentials.credential_path().exists()
        return {"configured": saved, "updated_at": "2026-11-11T12:00:00+04:00" if saved else None}

    monkeypatch.setattr(flow_credentials, "save_asap_credentials", save)
    monkeypatch.setattr(flow_credentials, "asap_credential_status", status)
    return stored


def test_the_portal_sign_in_is_saved_and_cleared_from_the_host_pc_only(installed, portal_store):
    from app import flow_credentials

    body = system_settings.PortalLoginWrite(username="  maya.k  ", password=SECRET)
    assert system_settings.get_portal_login()["configured"] is False

    for action in (lambda: system_settings.save_portal_login(body, colleague()),
                   lambda: system_settings.clear_portal_login(colleague())):
        with pytest.raises(HTTPException) as refused:
            action()
        assert refused.value.status_code == 403
        assert "localhost" in refused.value.detail
    assert portal_store == {}

    saved = system_settings.save_portal_login(body, host())
    assert saved["configured"] is True and SECRET not in json.dumps(saved)
    assert portal_store == {"username": "maya.k", "password": SECRET}

    cleared = system_settings.clear_portal_login(host())
    assert cleared["configured"] is False
    assert not flow_credentials.credential_path().exists()
    # Clearing twice is not an error: there is simply nothing left to clear.
    assert system_settings.clear_portal_login(host())["configured"] is False

    with database.get_db() as db:
        events = [tuple(row) for row in db.execute(
            "SELECT action, detail, actor FROM event_log WHERE entity_name='Settings' ORDER BY id")]
    assert [event[0] for event in events] == ["secret_replaced", "secret_cleared", "secret_cleared"]
    assert all(SECRET not in str(event) and "maya.k" not in str(event) for event in events)
    assert {event[2] for event in events} == {"Host PC"}


def test_a_portal_sign_in_that_cannot_be_stored_says_why(installed, monkeypatch):
    from app import flow_credentials

    def refuse(username, password, profile_dir=None):
        raise RuntimeError("ASAP credential storage is only available on the Windows BI desktop.")

    monkeypatch.setattr(flow_credentials, "save_asap_credentials", refuse)
    with pytest.raises(HTTPException) as failed:
        system_settings.save_portal_login(
            system_settings.PortalLoginWrite(username="maya.k", password=SECRET), host())
    assert failed.value.status_code == 422
    assert "only available on the Windows" in failed.value.detail and SECRET not in failed.value.detail


def test_clearing_the_real_credential_file_removes_it(tmp_path):
    from app import flow_credentials

    path = flow_credentials.credential_path(tmp_path)
    path.write_text("protected", encoding="utf-8")
    assert flow_credentials.clear_asap_credentials(tmp_path) == {"configured": False, "updated_at": None}
    assert not path.exists()
    assert flow_credentials.clear_asap_credentials(tmp_path)["configured"] is False


def test_a_save_that_stops_half_way_never_leaves_an_empty_settings_file(tmp_path, monkeypatch):
    from pathlib import Path

    from app import env_file

    path = tmp_path / ".env"
    path.write_text('PGHOST="db"\nPGPASSWORD="a-much-longer-old-password"\n', encoding="utf-8")
    real_open = Path.open

    class StopsAfterCuttingTheFile:
        """The process dies right after the file is cut to length."""

        def __init__(self, handle):
            self._handle = handle

        def truncate(self, *args):
            self._handle.truncate(*args)
            self._handle.flush()
            raise OSError("stopped")

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: StopsAfterCuttingTheFile(real_open(self, *a, **k)))
    with pytest.raises(OSError):
        env_file.write_value(path, "PGPASSWORD", "new")
    monkeypatch.undo()

    values, _status = env_file.read(path)
    assert values == {"PGHOST": "db", "PGPASSWORD": "new"}


# --- A save is all or nothing, across the database and the settings file -------------

def test_a_batch_that_needs_a_missing_settings_file_saves_nothing_at_all(installed, monkeypatch, tmp_path):
    before = registry.get("scan.daily_time")
    monkeypatch.setattr(config, "ENV_FILE", None)
    with pytest.raises(registry.SettingsRejected) as rejected:
        registry.save({"scan.daily_time": "09:45", "folders.reports": str(tmp_path)})
    assert list(rejected.value.errors) == ["folders.reports"]
    assert "settings file" in rejected.value.errors["folders.reports"]
    assert registry.get("scan.daily_time") == before


def test_a_write_that_fails_part_way_puts_back_what_was_already_written(installed, monkeypatch):
    from app import env_file

    registry.save({"conn.probe_host": "old-host"})
    file_before = installed.read_bytes()
    daily_before, keep_before = registry.get("scan.daily_time"), registry.get("logs.keep_days")
    told = []
    registry.subscribe(("scan.daily_time", "logs.keep_days"), lambda: told.append("listener"))
    real_write = env_file.write_value

    def locked_for_one_name(path, name, value):
        if name == "PGUSER":
            raise PermissionError("the file is locked")
        return real_write(path, name, value)

    monkeypatch.setattr(env_file, "write_value", locked_for_one_name)
    with pytest.raises(registry.SettingsRejected) as rejected:
        registry.save({
            "scan.daily_time": "09:45", "conn.probe_host": "new-host", "conn.probe_user": "reader",
            "logs.keep_days": 30,
        })

    assert list(rejected.value.errors) == ["conn.probe_user"]
    assert "Nothing was saved" in rejected.value.errors["conn.probe_user"]
    assert installed.read_bytes() == file_before
    assert registry.get("conn.probe_host") == "old-host" and config.PGHOST == "old-host"
    assert registry.get("scan.daily_time") == daily_before
    assert registry.get("logs.keep_days") == keep_before
    assert told == []


def test_a_database_that_refuses_the_save_puts_the_settings_file_back(installed, monkeypatch):
    file_before = installed.read_bytes()

    def busy(values):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(settings, "set_many", busy)
    with pytest.raises(registry.SaveFailed) as failed:
        registry.save({"conn.probe_host": "new-host", "logs.keep_days": 30})
    assert failed.value.saved == []
    assert "Nothing was saved" in str(failed.value)
    assert installed.read_bytes() == file_before
    assert config.PGHOST != "new-host"


def test_when_putting_back_also_fails_the_answer_says_what_was_saved(installed, monkeypatch):
    from app import env_file

    monkeypatch.setattr(settings, "set_many", lambda values: (_ for _ in ()).throw(RuntimeError("database is locked")))
    monkeypatch.setattr(env_file, "restore", lambda path, data: (_ for _ in ()).throw(PermissionError("locked")))
    response = system_settings.save_settings(
        system_settings.SettingsWrite(values={"conn.probe_host": "new-host", "logs.keep_days": 30}), host())
    assert response.status_code == 503
    detail = json.loads(response.body)["detail"]
    assert "Host" in detail and "saved" in detail and "Nothing was saved" not in detail
    with database.get_db() as db:
        actions = [row["action"] for row in db.execute("SELECT action FROM event_log WHERE entity_name='Settings'")]
    assert actions == ["setting_changed"]


def test_the_portal_sign_in_saved_in_settings_is_plain_text_in_the_settings_file_and_what_workers_use(
        installed, portal_store):
    # The owner asked for the sign-in to live in .env in plain text, where it
    # can also be corrected by hand before Metronome is started.
    from pathlib import Path

    from app import env_file, flow_credentials

    registry.save({"conn.portal_user": "maya.k"})
    registry.save_secret("conn.portal_password", SECRET)

    values, _status = env_file.read(config.ENV_FILE)
    assert (values["DG_PORTAL_USER"], values["DG_PORTAL_PASSWORD"]) == ("maya.k", SECRET)
    assert SECRET in Path(config.ENV_FILE).read_text(encoding="utf-8")
    used = flow_credentials.load_asap_credentials()
    assert (used["username"], used["password"], used["source"]) == ("maya.k", SECRET, "settings_file")

    # Edited by hand while Metronome runs: the next sign-in reads the new line.
    env_file.write_value(config.ENV_FILE, "DG_PORTAL_PASSWORD", "typed by hand")
    assert flow_credentials.load_asap_credentials()["password"] == "typed by hand"

    # The Settings page is told that a password is saved, never what it is.
    page = json.dumps(system_settings.get_settings(host()))
    assert SECRET not in page and "typed by hand" not in page and "maya.k" in page


def test_the_sign_in_state_shows_its_source_and_a_pause_and_try_again_is_for_the_host_pc(installed, portal_store):
    from app import flow_credentials, flow_sign_in_guard

    assert system_settings.get_portal_login()["source"] is None
    registry.save({"conn.portal_user": "maya.k"})
    registry.save_secret("conn.portal_password", SECRET)
    state = system_settings.get_portal_login()
    assert (state["source"], state["paused"], state["configured"]) == ("settings_file", False, False)
    assert SECRET not in json.dumps(state)

    with flow_sign_in_guard.attempt(flow_credentials.load_asap_credentials(), "ASAP") as attempt:
        attempt.submitted()
        attempt.refused()
    paused = system_settings.get_portal_login()
    assert (paused["paused"], paused["paused_refused"], paused["paused_portal"]) == (True, True, "ASAP")

    with pytest.raises(HTTPException) as refused:
        system_settings.resume_portal_login(colleague())
    assert refused.value.status_code == 403 and "localhost" in refused.value.detail
    assert system_settings.get_portal_login()["paused"] is True

    assert system_settings.resume_portal_login(host())["paused"] is False
    with database.get_db() as db:
        actions = [row[0] for row in db.execute("SELECT action FROM event_log WHERE entity_name='Settings' ORDER BY id")]
    assert actions[-1] == "sign_in_resumed"

    # A corrected password is a different sign-in and is not paused to begin with.
    with flow_sign_in_guard.attempt(flow_credentials.load_asap_credentials(), "ASAP") as attempt:
        attempt.submitted()
        attempt.refused()
    registry.save_secret("conn.portal_password", "the new one")
    assert system_settings.get_portal_login()["paused"] is False
