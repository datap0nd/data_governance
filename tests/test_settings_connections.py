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
    assert refused.value.errors == {"conn.probe_port": "Enter a whole number from 1 to 65,535."}


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
