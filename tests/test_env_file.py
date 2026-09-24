"""Project-local settings must stay private while reaching Flow SQL writes."""
import os
from pathlib import Path


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
