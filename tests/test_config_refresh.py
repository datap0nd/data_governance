"""Connection values change while Metronome runs: the settings file is re-read in place."""
import os
import sys
import types

import pytest

from app import config


@pytest.fixture(autouse=True)
def untouched_environment():
    # refresh() writes the file's values into os.environ; leave none behind.
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("PGHOST=first-host\nDG_UPLOAD_PGUSER=writer\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    monkeypatch.setattr(config, "_names_from_file", set())
    monkeypatch.setattr(config, "_env_file_signature", None)
    monkeypatch.setattr(config, "_STARTING_ENVIRONMENT", {
        key: value for key, value in os.environ.items()
        if key not in {"PGHOST", "PGPORT", "DG_UPLOAD_PGHOST", "DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD", "DG_PBI_WORKSPACE"}
    })
    for name in ("PGHOST", "PGPORT", "DG_UPLOAD_PGHOST", "DG_UPLOAD_PGUSER", "DG_UPLOAD_PGPASSWORD", "DG_PBI_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    # Whatever a test changes is put back, in config and in every module refresh touched.
    before = {name: getattr(config, name) for name in config.LIVE_NAMES}
    yield path
    monkeypatch.undo()
    for name, value in before.items():
        current = getattr(config, name)
        if current == value:
            continue
        for module in list(sys.modules.values()):
            if module is not None and config._is_metronome_module(module) and module.__dict__.get(name) == current:
                setattr(module, name, value)
        setattr(config, name, value)


def _importer(name, **values):
    """A stand-in for a module that did ``from app.config import ...`` at import."""
    module = types.ModuleType(f"app._refresh_probe_{name}")
    module.__dict__.update(values)
    sys.modules[module.__name__] = module
    return module


def test_start_up_values_and_the_refresh_derivation_agree():
    live = config._live_values(os.environ)
    assert set(live) == set(config.LIVE_NAMES)
    for name, value in live.items():
        assert getattr(config, name) == value, name


def test_refresh_updates_config_and_every_module_that_imported_the_old_value(settings_file):
    old_host, old_upload = config.PGHOST, config.UPLOAD_PGHOST
    prober = _importer("prober", PGHOST=old_host, UPLOAD_PGHOST=old_upload, UNRELATED="keep")
    try:
        changed = config.refresh()
        assert {"PGHOST", "UPLOAD_PGHOST", "UPLOAD_PGUSER"} <= set(changed)
        assert (config.PGHOST, config.UPLOAD_PGHOST, config.UPLOAD_PGUSER) == ("first-host", "first-host", "writer")
        assert (prober.PGHOST, prober.UPLOAD_PGHOST, prober.UNRELATED) == ("first-host", "first-host", "keep")

        settings_file.write_text("PGHOST=second-host\nDG_UPLOAD_PGHOST=upload-host\nDG_UPLOAD_PGUSER=writer\n", encoding="utf-8")
        assert set(config.refresh()) == {"PGHOST", "UPLOAD_PGHOST"}
        assert (prober.PGHOST, prober.UPLOAD_PGHOST) == ("second-host", "upload-host")
        assert os.environ["PGHOST"] == "second-host"
        assert config.refresh() == []
    finally:
        sys.modules.pop(prober.__name__, None)


def test_a_module_given_its_own_value_on_purpose_is_left_alone(settings_file):
    patched = _importer("patched", PGHOST="set-by-a-test")
    outsider = types.ModuleType("somewhere_else")
    outsider.PGHOST = config.PGHOST
    sys.modules[outsider.__name__] = outsider
    try:
        config.refresh()
        assert patched.PGHOST == "set-by-a-test"
        assert outsider.PGHOST != "first-host"  # not a Metronome module
    finally:
        sys.modules.pop(patched.__name__, None)
        sys.modules.pop(outsider.__name__, None)


def test_a_name_removed_from_the_file_falls_back_to_what_the_process_started_with(settings_file, monkeypatch):
    monkeypatch.setitem(config._STARTING_ENVIRONMENT, "DG_PBI_WORKSPACE", "from the service environment")
    settings_file.write_text("PGHOST=first-host\nDG_PBI_WORKSPACE=from the file\n", encoding="utf-8")
    config.refresh()
    assert config.PBI_WORKSPACE == "from the file"
    settings_file.write_text("", encoding="utf-8")
    config.refresh()
    assert config.PBI_WORKSPACE == "from the service environment"
    assert config.PGHOST == ""
    assert "PGHOST" not in os.environ


def test_refresh_if_changed_reads_only_after_the_file_was_written(settings_file, monkeypatch):
    assert "PGHOST" in config.refresh_if_changed()
    reads = []
    real = config.env_file.read
    monkeypatch.setattr(config.env_file, "read", lambda path: reads.append(path) or real(path))
    assert config.refresh_if_changed() == []
    assert reads == []
    settings_file.write_text("PGHOST=third-host-with-a-longer-name\n", encoding="utf-8")
    assert "PGHOST" in config.refresh_if_changed()
    assert len(reads) == 1 and config.PGHOST == "third-host-with-a-longer-name"


def test_a_file_that_cannot_be_read_right_now_keeps_the_values_in_force(settings_file, monkeypatch):
    config.refresh()
    assert config.PGHOST == "first-host"
    monkeypatch.setattr(config.env_file, "read", lambda path: ({}, {"error": "PermissionError while reading settings file",
                                                                    "loaded_names": [], "exists": True}))
    assert config.refresh() == []
    assert config.PGHOST == "first-host" and os.environ["PGHOST"] == "first-host"


def test_without_a_settings_file_refresh_follows_the_environment(monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE", None)
    before = config.PBI_WORKSPACE
    monkeypatch.setenv("DG_PBI_WORKSPACE", "workspace from the environment")
    try:
        assert config.refresh() == ["PBI_WORKSPACE"]
        assert config.PBI_WORKSPACE == "workspace from the environment"
        assert config.refresh_if_changed() == []
    finally:
        monkeypatch.undo()
        config.refresh()
    assert config.PBI_WORKSPACE == before
