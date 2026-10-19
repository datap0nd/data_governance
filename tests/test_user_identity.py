"""Sign-in chooses a person from Users; only the host PC can move a computer."""
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import database, local_access, main, user_identity
from test_flows import flow_db  # noqa: F401  (fixture)

COLLEAGUE = ("203.0.113.9", 50000)  # TEST-NET address: never the host PC
OTHER_COLLEAGUE = ("203.0.113.10", 50000)


@pytest.fixture
def people(flow_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(user_identity, "resolve_hostname", lambda ip: None)
    user_identity.clear_cache()
    with database.get_db() as db:
        ids = [db.execute("INSERT INTO people(name, role, email) VALUES (?, 'BI', ?)", (name, email)).lastrowid
               for name, email in (("Maya Chen", "maya@example.test"), ("Omar Malik", None))]
    yield ids
    user_identity.clear_cache()


def client(address=COLLEAGUE, key="browser-a"):
    return TestClient(main.app, client=address, headers={"X-Client-Key": key})


def test_typed_name_sign_ins_are_cleared_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "legacy.db"))
    with sqlite3.connect(tmp_path / "legacy.db") as conn:
        conn.executescript("""
            CREATE TABLE user_ips (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT NOT NULL UNIQUE,
                                   person_name TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO user_ips (ip_address, person_name) VALUES ('203.0.113.9', 'typed name');
        """)
    database.init_db()
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM user_ips").fetchone()[0] == 0
        person = db.execute("INSERT INTO people(name, role) VALUES ('Maya', 'BI')").lastrowid
    user_identity.bind("203.0.113.9", "browser-a", None, person, "Maya")
    database.init_db()  # A later startup keeps the new person-based sign-ins.
    with database.get_db() as db:
        assert [tuple(row) for row in db.execute("SELECT ip_address, person_id FROM user_ips")] == [("203.0.113.9", person)]
        assert db.execute("SELECT COUNT(*) FROM user_devices WHERE person_id=?", (person,)).fetchone()[0] == 1


def test_first_visit_chooses_a_person_and_the_name_follows_users(people):
    maya, _omar = people
    browser = client()
    me = browser.get("/api/me").json()
    assert (me["person_id"], me["name"], me["is_host"]) == (None, None, False)

    assert browser.post("/api/register", json={"name": "Typed name"}).status_code == 422
    assert browser.post("/api/register", json={"person_id": 999}).status_code == 404
    signed_in = browser.post("/api/register", json={"person_id": maya, "client_key": "browser-a"}).json()
    assert (signed_in["person_id"], signed_in["name"], signed_in["is_host"]) == (maya, "Maya Chen", False)
    assert browser.get("/api/me").json()["name"] == "Maya Chen"
    # The IP alone also identifies the computer when the browser key is new.
    assert client(key="fresh-browser").get("/api/me").json()["person_id"] == maya

    host = TestClient(main.app)
    assert host.patch(f"/api/people/{maya}", json={"name": "Maya Park"}).status_code == 200
    assert browser.get("/api/me").json()["name"] == "Maya Park"
    assert host.delete(f"/api/people/{maya}").status_code == 200
    assert browser.get("/api/me").json()["person_id"] is None


def test_only_the_host_pc_moves_a_signed_in_computer(people):
    maya, omar = people
    browser = client()
    assert browser.post("/api/register", json={"person_id": maya}).status_code == 200
    assert browser.post("/api/register", json={"person_id": maya}).status_code == 200  # Same choice again.
    taken = browser.post("/api/register", json={"person_id": omar})
    assert taken.status_code == 409
    assert "already signed in as Maya Chen" in taken.json()["detail"]
    assert browser.get("/api/me").json()["person_id"] == maya

    assert client(OTHER_COLLEAGUE).delete(f"/api/people/{maya}/sign-ins").status_code == 403
    host = TestClient(main.app)
    listed = {person["id"]: person for person in host.get("/api/people").json()}
    assert listed[maya]["signed_in_computers"] == 1
    signed_out = host.delete(f"/api/people/{maya}/sign-ins")
    assert signed_out.status_code == 200 and signed_out.json()["signed_in_computers"] == 0
    assert browser.get("/api/me").json()["person_id"] is None
    assert browser.post("/api/register", json={"person_id": omar}).json()["name"] == "Omar Malik"


def test_host_pc_may_switch_and_acts_as_host_without_a_person(people):
    maya, omar = people
    host = TestClient(main.app, headers={"X-Client-Key": "host-browser"})
    me = host.get("/api/me").json()
    assert me["is_host"] is True and me["person_id"] is None
    assert host.post("/api/register", json={"person_id": maya}).json()["name"] == "Maya Chen"
    assert host.post("/api/register", json={"person_id": omar}).json()["name"] == "Omar Malik"


def _request(peer="127.0.0.1", host="localhost", headers=None):
    url = SimpleNamespace(hostname=host, scheme="http", netloc=f"{host}:8000")
    return SimpleNamespace(client=SimpleNamespace(host=peer), url=url, headers=headers or {})


def test_host_pc_check_refuses_other_computers_cross_site_and_proxied_requests(monkeypatch):
    monkeypatch.setattr(local_access, "is_server_machine", lambda peer: peer in {"127.0.0.1", "10.0.0.5"})
    assert local_access.is_host_request(_request())
    assert local_access.is_host_request(_request("10.0.0.5", "bi-desktop", {"origin": "http://bi-desktop:8000",
                                                                           "sec-fetch-site": "same-origin"}))
    assert not local_access.is_host_request(_request("203.0.113.9"))
    assert not local_access.is_host_request(_request(headers={"origin": "https://elsewhere.example"}))
    assert not local_access.is_host_request(_request(headers={"sec-fetch-site": "cross-site"}))
    assert not local_access.is_host_request(_request(headers={"x-forwarded-for": "203.0.113.9"}))


def test_request_identity_treats_internal_calls_as_the_application():
    assert user_identity.request_identity(None) == (None, True)
    assert user_identity.request_identity(SimpleNamespace(state=SimpleNamespace(actor="scheduler"))) == (None, True)
    browser = SimpleNamespace(state=SimpleNamespace(person_id=4, is_host=False))
    assert user_identity.request_identity(browser) == (4, False)
