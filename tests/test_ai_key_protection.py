"""The saved AI key is kept encrypted, so the database and its backups do not hold it in plain text."""
import pytest

from app import config, database
from app.ai import runtime_config
from app.ai.runtime_config import AISettingsUpdate

KEY = "sk-test-0123456789abcdef"


@pytest.fixture
def ai_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(config, "AI_API_KEY", "")
    database.init_db()


@pytest.fixture
def windows(monkeypatch):
    """Stand in for Windows data protection with a reversible scramble."""
    state = {"readable": True}

    def protect(data, sealing):
        if not sealing and not state["readable"]:
            raise OSError("DPAPI unprotect failed")
        return bytes(byte ^ 0x5A for byte in data)

    monkeypatch.setattr(runtime_config.platform, "system", lambda: "Windows")
    monkeypatch.setattr(runtime_config, "_windows_protect", protect)
    return state


def _stored():
    with database.get_db() as db:
        row = db.execute("SELECT value, updated_at FROM app_settings WHERE key=?",
                         (runtime_config.API_KEY_KEY,)).fetchone()
    return (row["value"], row["updated_at"]) if row else (None, None)


def _save(**fields):
    with database.get_db() as db:
        return runtime_config.save_runtime_settings(db, AISettingsUpdate(**fields))


def test_a_saved_key_is_stored_encrypted_and_still_used(ai_db, windows):
    saved = _save(api_key=KEY)
    stored, _when = _stored()
    assert stored.startswith(runtime_config.PROTECTED_PREFIX) and KEY not in stored
    assert (saved.api_key, saved.api_key_source) == (KEY, "system")
    assert runtime_config.load_runtime_settings().api_key == KEY
    assert KEY not in str(saved.public_dict())


def test_clearing_still_leaves_an_empty_marker_not_an_encrypted_one(ai_db, windows):
    _save(api_key=KEY)
    cleared = _save(clear_api_key=True)
    assert _stored()[0] == ""
    assert (cleared.api_key, cleared.api_key_source) == ("", "none")


def test_a_key_another_windows_account_saved_reads_as_not_set(ai_db, windows):
    _save(api_key=KEY)
    windows["readable"] = False
    settings = runtime_config.load_runtime_settings()
    assert (settings.api_key, settings.api_key_source) == ("", "none")
    assert settings.public_dict()["api_key_configured"] is False


def test_a_key_saved_before_protection_is_encrypted_at_start_up(ai_db, windows, monkeypatch):
    monkeypatch.setattr(runtime_config.platform, "system", lambda: "Linux")
    _save(api_key=KEY)
    plain, when = _stored()
    assert plain == KEY  # how every key was stored before

    monkeypatch.setattr(runtime_config.platform, "system", lambda: "Windows")
    assert runtime_config.initialize_runtime_settings().api_key == KEY
    stored, later = _stored()
    assert stored.startswith(runtime_config.PROTECTED_PREFIX) and KEY not in stored
    assert later == when  # when the key was entered is unchanged
    assert runtime_config.protect_stored_api_key() is False  # nothing left to do


def test_where_windows_protection_is_absent_the_key_is_stored_as_before(ai_db):
    _save(api_key=KEY)
    assert _stored()[0] == KEY
    assert runtime_config.load_runtime_settings().api_key == KEY
    assert runtime_config.protect_stored_api_key() is False


def test_a_protection_failure_keeps_the_key_usable(ai_db, monkeypatch):
    def refuse(data, sealing):
        raise OSError("DPAPI protect failed")

    monkeypatch.setattr(runtime_config.platform, "system", lambda: "Windows")
    monkeypatch.setattr(runtime_config, "_windows_protect", refuse)
    assert _save(api_key=KEY).api_key == KEY
    assert _stored()[0] == KEY
