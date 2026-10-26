"""Project-local settings must stay private while reaching Flow SQL writes."""
import os
from pathlib import Path

import pytest

from app import config, env_file


def test_env_parser_overrides_nonempty_values_without_reporting_them(tmp_path, monkeypatch):
    from app import config

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\ufeff# settings\nexport DG_UPLOAD_PGUSER='writer'\n"
        'DG_UPLOAD_PGPASSWORD="secret#tail"\nDG_UPLOAD_PGHOST=\ninvalid line\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DG_ENV_FILE", str(env_file))
    monkeypatch.setenv("DG_UPLOAD_PGUSER", "old")
    monkeypatch.setenv("DG_UPLOAD_PGHOST", "keep")
    path, status = config._load_env_file()
    assert path == env_file
    assert os.environ["DG_UPLOAD_PGUSER"] == "writer"
    assert os.environ["DG_UPLOAD_PGPASSWORD"] == "secret#tail"
    assert os.environ["DG_UPLOAD_PGHOST"] == "keep"
    assert status["loaded_names"] == ["DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD"]
    assert status["ignored_lines"] == [5]
    assert "secret" not in repr(status) and "writer" not in repr(status)


def test_env_disabled_missing_and_unreadable(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setenv("DG_ENV_FILE", "")
    assert config._load_env_file()[0] is None
    monkeypatch.setenv("DG_ENV_FILE", str(tmp_path / "missing"))
    assert config._load_env_file()[1]["exists"] is False
    directory = tmp_path / "directory"
    directory.mkdir()
    monkeypatch.setenv("DG_ENV_FILE", str(directory))
    assert config._load_env_file()[1]["error"]


def test_portable_config_reads_nearest_parent(tmp_path, monkeypatch):
    from app.flow_portable import CONFIG_SOURCE

    parent = tmp_path / "parent"
    child = parent / "flow"
    child.mkdir(parents=True)
    (parent / ".env").write_text("DG_UPLOAD_PGUSER=parent_writer\n", encoding="utf-8")
    monkeypatch.delenv("DG_ENV_FILE", raising=False)
    monkeypatch.setenv("DG_UPLOAD_PGUSER", "old")
    namespace = {"__file__": str(child / "run_flow.py")}
    exec(CONFIG_SOURCE, namespace)
    assert namespace["UPLOAD_PGUSER"] == "parent_writer"
    monkeypatch.setenv("DG_ENV_FILE", "")
    monkeypatch.setenv("DG_UPLOAD_PGUSER", "old")
    exec(CONFIG_SOURCE, namespace)
    assert namespace["UPLOAD_PGUSER"] == "old"


def test_template_and_setup_contract():
    root = Path(__file__).resolve().parents[1]
    template = (root / ".env.example").read_text(encoding="utf-8")
    assert [line for line in template.splitlines() if line.startswith("DG_UPLOAD_PG") and not line.startswith("#")] == [
        "DG_UPLOAD_PGUSER=", "DG_UPLOAD_PGPASSWORD=",
    ]
    assert ".env\n" in (root / ".gitignore").read_text(encoding="utf-8")
    setup = (root / "setup.ps1").read_text(encoding="utf-8")
    assert "if (-not (Test-Path $EnvFile -PathType Leaf))" in setup
    assert "${AuditorIdentity}:R" in setup
    assert "/inheritance:r" in setup


def test_write_value_replaces_one_setting_in_place(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"\xef\xbb\xbf# keep me\r\nA=1\r\nexport DG_X=old\r\nDG_X = older\r\nB='2'\r\n")
    inode = path.stat().st_ino
    env_file.write_value(path, "DG_X", ' new "value" ')
    assert path.read_bytes() == b"\xef\xbb\xbf# keep me\r\nA=1\r\nB='2'\r\nDG_X=\" new \"value\" \"\r\n"
    assert path.stat().st_ino == inode  # Rewritten in place: setup's access list stays.
    assert env_file.read(path)[0] == {"A": "1", "B": "2", "DG_X": ' new "value" '}
    env_file.write_value(path, "DG_X", None)
    assert path.read_bytes() == b"\xef\xbb\xbf# keep me\r\nA=1\r\nB='2'\r\n"
    env_file.write_value(path, "DG_X", None)  # Removing an absent setting changes nothing.
    assert path.read_bytes() == b"\xef\xbb\xbf# keep me\r\nA=1\r\nB='2'\r\n"


def test_write_value_refuses_unsafe_input_and_missing_files(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=1\n", encoding="utf-8")
    for name, value in [("bad-name", "x"), ("DG_X", "two\nlines"), ("DG_X", "")]:
        with pytest.raises(ValueError):
            env_file.write_value(path, name, value)
    assert path.read_text(encoding="utf-8") == "A=1\n"
    with pytest.raises(FileNotFoundError):
        env_file.write_value(tmp_path / "missing.env", "DG_X", "x")
    assert not (tmp_path / "missing.env").exists()


def test_current_setting_reads_the_file_now_in_app_and_portable_config(tmp_path, monkeypatch):
    from app.flow_portable import CONFIG_SOURCE

    path = tmp_path / ".env"
    path.write_text("DG_USER_SQL_PASSWORD_7=first\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    monkeypatch.setenv("DG_ENV_FILE", str(path))
    namespace = {"__file__": str(tmp_path / "run_flow.py")}
    exec(CONFIG_SOURCE, namespace)
    for current in (config.current_setting, namespace["current_setting"]):
        assert current("DG_USER_SQL_PASSWORD_7") == "first"
    env_file.write_value(path, "DG_USER_SQL_PASSWORD_7", "second")
    for current in (config.current_setting, namespace["current_setting"]):
        assert current("DG_USER_SQL_PASSWORD_7") == "second"
    env_file.write_value(path, "DG_USER_SQL_PASSWORD_7", None)
    for current in (config.current_setting, namespace["current_setting"]):
        assert current("DG_USER_SQL_PASSWORD_7") is None
