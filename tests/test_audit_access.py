"""Audit fixes: the settings file cannot be injected into, and "host PC" means one thing."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import config, database, env_file, local_access, main, settings, sql_passwords
from app.models import SqlPasswordWrite
from app.routers import people

# Every character str.splitlines() treats as a line boundary.
LINE_BREAKS = ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('DG_UPLOAD_PGHOST=real-host\nDG_USER_SQL_PASSWORD_3="theirs"\n', encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    return path


@pytest.mark.parametrize("separator", LINE_BREAKS, ids=[f"U+{ord(char):04X}" for char in LINE_BREAKS])
def test_a_value_with_any_line_break_is_refused_and_the_file_is_untouched(settings_file, separator):
    before = settings_file.read_bytes()
    crafted = f"x{separator}DG_UPLOAD_PGHOST=other{separator}DG_USER_SQL_PASSWORD_3=chosen{separator}Z="
    assert not env_file.is_single_line(crafted)
    with pytest.raises(ValueError):
        env_file.write_value(settings_file, "DG_USER_SQL_PASSWORD_7", crafted)
    assert settings_file.read_bytes() == before


@pytest.mark.parametrize("separator", ["\x85", " ", " ", "\x0b", "\x0c", "\x1c"])
def test_reading_never_splits_a_value_on_a_unicode_separator(tmp_path, separator):
    # A file written before the fix, or edited by hand, may already hold one.
    path = tmp_path / ".env"
    path.write_text(
        f'DG_UPLOAD_PGHOST=real-host\nDG_USER_SQL_PASSWORD_7="x{separator}DG_UPLOAD_PGHOST=other"\n',
        encoding="utf-8",
    )
    values, status = env_file.read(path)
    assert values["DG_UPLOAD_PGHOST"] == "real-host"
    assert values["DG_USER_SQL_PASSWORD_7"] == f"x{separator}DG_UPLOAD_PGHOST=other"
    assert status["loaded_names"] == ["DG_UPLOAD_PGHOST", "DG_USER_SQL_PASSWORD_7"]


def test_windows_line_endings_and_ordinary_values_still_round_trip(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"# settings\r\nDG_UPLOAD_PGUSER=writer\r\n")
    env_file.write_value(path, "DG_USER_SQL_PASSWORD_7", ' p@ss "quoted" = #value ')
    values, _status = env_file.read(path)
    assert values == {"DG_UPLOAD_PGUSER": "writer", "DG_USER_SQL_PASSWORD_7": ' p@ss "quoted" = #value '}
    assert path.read_bytes().count(b"\r\n") == 3 and b"\n\n" not in path.read_bytes()
    assert env_file.is_single_line("café € pass")
    assert not env_file.is_single_line("")


def test_saving_an_sql_password_with_a_line_break_is_refused(tmp_path, monkeypatch, settings_file):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    database.init_db()
    with database.get_db() as db:
        person = db.execute("INSERT INTO people(name, role, sql_username) VALUES ('Maya', 'BI', 'maya_sql')").lastrowid
    own_browser = SimpleNamespace(state=SimpleNamespace(actor="Maya", person_id=person, is_host=False))
    before = settings_file.read_bytes()

    with pytest.raises(HTTPException) as refused:
        people.set_sql_password(
            person, SqlPasswordWrite(password="x DG_UPLOAD_PGHOST=other DG_USER_SQL_PASSWORD_3=chosen"),
            own_browser,
        )

    assert refused.value.status_code == 422
    assert settings_file.read_bytes() == before
    assert sql_passwords.saved_ids() == {3}


def _request(peer="127.0.0.1", host="localhost", headers=None):
    url = SimpleNamespace(hostname=host, scheme="http", netloc=f"{host}:8000")
    return SimpleNamespace(client=SimpleNamespace(host=peer), url=url, headers=headers or {})


@pytest.fixture
def this_pc(monkeypatch):
    monkeypatch.setattr(local_access, "_server_ip_keys", lambda: frozenset({"127.0.0.1", "::1", "10.0.0.5"}))
    monkeypatch.setattr(local_access, "_server_host_keys", lambda: frozenset({"localhost", "bi-desktop"}))
    monkeypatch.setattr(local_access, "is_server_machine", lambda peer: peer in {"127.0.0.1", "::1", "10.0.0.5"})
    monkeypatch.delenv("DG_HOST_NAMES", raising=False)


def test_only_this_pcs_own_names_and_addresses_count_as_its_address(this_pc, monkeypatch):
    for own in ("localhost", "LOCALHOST", "127.0.0.1", "[::1]", "10.0.0.5", "bi-desktop", "BI-Desktop"):
        assert local_access.is_own_host_name(own), own
    for other in ("", None, "metronome.attacker.example", "203.0.113.9", "bi-desktop.attacker.example"):
        assert not local_access.is_own_host_name(other), other
    monkeypatch.setenv("DG_HOST_NAMES", "metronome.corp.example, bi")
    assert local_access.is_own_host_name("metronome.corp.example") and local_access.is_own_host_name("bi")
    assert not local_access.is_own_host_name("metronome.attacker.example")


def test_another_sites_name_pointed_at_this_pc_is_not_the_host_pc(this_pc, monkeypatch):
    # DNS rebinding: the browser on the host PC loads a page from a domain the
    # attacker controls, which now resolves to 127.0.0.1. Peer and origin look
    # right; only the Host name gives it away. Resolving it must not be tried.
    def no_lookups(*_args, **_kwargs):
        raise AssertionError("the Host name must be judged without a DNS lookup")

    monkeypatch.setattr(local_access.socket, "getaddrinfo", no_lookups)
    rebound = _request("127.0.0.1", "metronome.attacker.example", {
        "origin": "http://metronome.attacker.example:8000", "sec-fetch-site": "same-origin",
    })
    assert not local_access.is_host_request(rebound)
    assert not local_access.is_local_request(rebound)
    assert local_access.is_host_request(_request("10.0.0.5", "bi-desktop"))
    assert local_access.is_host_request(_request("127.0.0.1", "localhost"))


def test_local_request_accepts_localhost_as_well_as_the_loopback_address(this_pc):
    assert local_access.is_local_request(_request("127.0.0.1", "localhost"))
    assert local_access.is_local_request(_request("127.0.0.1", "127.0.0.1"))
    assert local_access.is_local_request(_request("::1", "::1"))
    # Loopback only: the PC's network name and other computers stay refused.
    assert not local_access.is_local_request(_request("10.0.0.5", "bi-desktop"))
    assert not local_access.is_local_request(_request("203.0.113.9", "localhost"))
    assert not local_access.is_local_request(_request("127.0.0.1", "localhost", {"origin": "https://elsewhere.example"}))
    assert not local_access.is_local_request(_request("127.0.0.1", "localhost", {"x-forwarded-for": "203.0.113.9"}))


def test_require_host_refuses_a_colleague_and_says_how_to_reach_the_host_view():
    colleague = SimpleNamespace(state=SimpleNamespace(actor="Maya", person_id=4, is_host=False))
    with pytest.raises(HTTPException) as refused:
        local_access.require_host(colleague, "Only the host PC can change settings.")
    assert refused.value.status_code == 403
    assert refused.value.detail == "Only the host PC can change settings. On the host PC, open Metronome as http://localhost:8000."
    local_access.require_host(SimpleNamespace(state=SimpleNamespace(actor="Host PC", person_id=None, is_host=True)))
    local_access.require_host(None)  # Metronome's own code


def test_ai_connection_tests_are_refused_from_another_computer(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    database.init_db()
    colleague = TestClient(main.app, client=("203.0.113.9", 50000))
    for route in ("/api/ai/settings/models", "/api/ai/settings/test"):
        response = colleague.post(route, json={"endpoint": "http://127.0.0.1:8000/api/auditor/stop"})
        assert response.status_code == 403, (route, response.text)
        assert "only on this Metronome computer" in response.json()["detail"]


COLLEAGUE = ("203.0.113.9", 50000)
HINT = "On the host PC, open Metronome as http://localhost:8000."


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def test_changes_that_belong_to_the_host_pc_are_refused_from_another_computer(app_db, monkeypatch):
    from app.scanner import pbi_auth

    reached = []
    monkeypatch.setattr(main, "_apply_latest_update", lambda: reached.append("update") or {"status": "started"})
    monkeypatch.setattr(pbi_auth, "start_device_flow", lambda: reached.append("connect") or {"status": "pending"})
    monkeypatch.setattr(pbi_auth, "disconnect", lambda: reached.append("disconnect") or {"status": "disconnected"})
    calls = [
        ("post", "/api/system/updates/apply", None, "Only the host PC can install an update."),
        ("post", "/api/update", None, "Only the host PC can install an update."),
        ("put", "/api/system/updates", {"enabled": False}, "Only the host PC can turn automatic updates on or off."),
        ("post", "/api/scanner/pbi-auth/connect", None, "Only the host PC can connect the Power BI account."),
        ("post", "/api/scanner/pbi-auth/disconnect", None, "Only the host PC can disconnect the Power BI account."),
        ("put", "/api/scanner/notification-settings", {"recipients": ["someone@example.com"]},
         "Only the host PC can change who is told about failed scans."),
    ]
    before = settings.get_setting("automatic_main_updates_enabled")
    colleague = TestClient(main.app, client=COLLEAGUE)
    for method, route, body, message in calls:
        response = colleague.request(method.upper(), route, json=body)
        assert response.status_code == 403, (route, response.text)
        assert response.json() == {"detail": f"{message} {HINT}"}
    assert reached == []
    assert settings.get_setting("automatic_main_updates_enabled") == before
    # Anyone may still read the state these routes change.
    assert colleague.get("/api/scanner/pbi-auth/status").status_code == 200
    assert colleague.get("/api/scanner/notification-settings").status_code == 200

    host = TestClient(main.app)
    assert host.post("/api/system/updates/apply").status_code == 202
    assert host.post("/api/scanner/pbi-auth/connect").json() == {"status": "pending"}
    assert host.post("/api/scanner/pbi-auth/disconnect").json() == {"status": "disconnected"}
    assert reached == ["update", "connect", "disconnect"]


def test_remote_flow_control_needs_more_than_an_address_on_this_pc(app_db):
    # A page from another site, open in a browser on the host PC, reaches the
    # app from this PC's own address; the request still is not the host's.
    on_this_pc = TestClient(main.app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    cross_site = on_this_pc.get(
        "/api/system/remote-flow-control", headers={"origin": "https://elsewhere.example"})
    assert cross_site.status_code == 403
    assert TestClient(main.app, client=COLLEAGUE).get("/api/system/remote-flow-control").status_code == 403
    assert on_this_pc.get("/api/system/remote-flow-control").status_code == 200


def test_a_persons_email_mapping_is_changed_by_the_host_pc_or_that_person(app_db):
    from app.routers import email

    with database.get_db() as db:
        maya = db.execute("INSERT INTO people (name, role) VALUES ('Maya', 'Analyst')").lastrowid
        omar = db.execute("INSERT INTO people (name, role) VALUES ('Omar', 'Analyst')").lastrowid
    body = email.PersonEmailUpdate(email="maya@example.com")

    def as_person(person_id):
        return SimpleNamespace(state=SimpleNamespace(actor="Someone", person_id=person_id, is_host=False))

    with pytest.raises(HTTPException) as refused:
        email.update_person_email(maya, body, as_person(omar))
    assert refused.value.status_code == 403
    assert refused.value.detail == f"Only the host PC or that person can change their email settings. {HINT}"
    with pytest.raises(HTTPException):
        email.update_person_email(maya, body, as_person(None))
    with database.get_db() as db:
        assert db.execute("SELECT email FROM people WHERE id=?", (maya,)).fetchone()["email"] is None

    email.update_person_email(maya, body, as_person(maya))
    host = SimpleNamespace(state=SimpleNamespace(actor="Host PC", person_id=None, is_host=True))
    email.update_person_email(omar, email.PersonEmailUpdate(email="omar@example.com"), host)
    with database.get_db() as db:
        saved = {row["name"]: row["email"] for row in db.execute("SELECT name, email FROM people")}
    assert saved == {"Maya": "maya@example.com", "Omar": "omar@example.com"}
