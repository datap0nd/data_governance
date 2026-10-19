"""Host-only Users changes and per-person SQL passwords kept in the settings file."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, database, main, user_identity
from test_flows import flow_db  # noqa: F401  (fixture)

COLLEAGUE = ("203.0.113.9", 50000)
SETTING = "DG_USER_SQL_PASSWORD_{}"


@pytest.fixture
def settings_file(flow_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(user_identity, "resolve_hostname", lambda ip: None)
    user_identity.clear_cache()
    path = tmp_path / ".env"
    path.write_text("# Metronome settings\r\nDG_UPLOAD_PGUSER=loader\r\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    return path


def host():
    return TestClient(main.app)


def contents(path):
    return path.read_bytes().decode("utf-8")  # Exact line endings, unlike read_text().


def person(client, **values):
    response = client.post("/api/people", json={"name": "Maya", "role": "BI", **values})
    assert response.status_code == 201, response.text
    return response.json()


def test_only_the_host_pc_changes_users(settings_file):
    maya = person(host(), sql_username="maya_sql")
    colleague = TestClient(main.app, client=COLLEAGUE)
    assert colleague.get("/api/people").status_code == 200
    for method, path, body in [
        ("post", "/api/people", {"name": "Mallory", "role": "BI"}),
        ("patch", f"/api/people/{maya['id']}", {"email": "mallory@example.test"}),
        ("put", f"/api/people/{maya['id']}/sql-password", {"password": "guess"}),
        ("delete", f"/api/people/{maya['id']}/sql-password", None),
        ("delete", f"/api/people/{maya['id']}", None),
    ]:
        response = getattr(colleague, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, (method, path)
        assert "host PC" in response.json()["detail"]
    assert host().get("/api/people").json() == [maya]
    assert "guess" not in contents(settings_file)


def test_password_is_saved_in_the_settings_file_and_never_returned(settings_file):
    client = host()
    maya = person(client, sql_username="maya_sql")
    assert maya["sql_password_set"] is False
    secret = ' s3cret "with" quotes '
    saved = client.put(f"/api/people/{maya['id']}/sql-password", json={"password": secret})
    assert saved.status_code == 200
    assert saved.json()["sql_password_set"] is True
    text = contents(settings_file)
    assert text.startswith("# Metronome settings\r\nDG_UPLOAD_PGUSER=loader\r\n")
    assert f'{SETTING.format(maya["id"])}="{secret}"\r\n' in text
    from app.flow_sql import person_password
    assert person_password(maya["id"]) == secret
    for response in (saved, client.get("/api/people"), client.patch(f"/api/people/{maya['id']}", json={"role": "BI"})):
        assert "s3cret" not in response.text
    with database.get_db() as db:
        events = [dict(row) for row in db.execute("SELECT action, detail FROM event_log WHERE entity_type='person'")]
    assert {"action": "sql_password_set", "detail": None} in events
    assert "s3cret" not in json.dumps(events)

    cleared = client.delete(f"/api/people/{maya['id']}/sql-password")
    assert cleared.json()["sql_password_set"] is False
    assert contents(settings_file) == "# Metronome settings\r\nDG_UPLOAD_PGUSER=loader\r\n"


def test_password_needs_a_username_and_one_line(settings_file):
    client = host()
    maya = person(client)
    missing = client.put(f"/api/people/{maya['id']}/sql-password", json={"password": "secret"})
    assert missing.status_code == 409 and "SQL username" in missing.json()["detail"]
    client.patch(f"/api/people/{maya['id']}", json={"sql_username": "maya_sql"})
    for bad in ["two\nlines", "tab\tinside", ""]:
        assert client.put(f"/api/people/{maya['id']}/sql-password", json={"password": bad}).status_code == 422
    assert client.put("/api/people/999/sql-password", json={"password": "secret"}).status_code == 404
    assert SETTING.format(maya["id"]) not in contents(settings_file)


def test_new_sql_username_clears_the_old_password(settings_file):
    client = host()
    maya = person(client, sql_username="maya_sql")
    client.put(f"/api/people/{maya['id']}/sql-password", json={"password": "secret"})
    kept = client.patch(f"/api/people/{maya['id']}", json={"sql_username": " maya_sql ", "email": "maya@example.test"})
    assert kept.json()["sql_password_set"] is True
    changed = client.patch(f"/api/people/{maya['id']}", json={"sql_username": "maya_reporting"})
    assert changed.json()["sql_username"] == "maya_reporting"
    assert changed.json()["sql_password_set"] is False
    assert SETTING.format(maya["id"]) not in contents(settings_file)


def test_deleting_a_person_removes_their_password_and_sign_ins(settings_file):
    client = host()
    maya = person(client, sql_username="maya_sql")
    client.put(f"/api/people/{maya['id']}/sql-password", json={"password": "secret"})
    user_identity.bind("203.0.113.9", "browser-a", None, maya["id"], "Maya")
    assert client.delete(f"/api/people/{maya['id']}").status_code == 200
    assert SETTING.format(maya["id"]) not in contents(settings_file)
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM user_ips WHERE person_id=?", (maya["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM user_devices WHERE person_id=?", (maya["id"],)).fetchone()[0] == 0


def test_without_a_settings_file_passwords_are_refused_not_lost(settings_file, monkeypatch):
    client = host()
    maya = person(client, sql_username="maya_sql")
    monkeypatch.setattr(config, "ENV_FILE", None)
    refused = client.put(f"/api/people/{maya['id']}/sql-password", json={"password": "secret"})
    assert refused.status_code == 409 and "settings file" in refused.json()["detail"]
    monkeypatch.setattr(config, "ENV_FILE", settings_file.parent / "missing.env")
    refused = client.put(f"/api/people/{maya['id']}/sql-password", json={"password": "secret"})
    assert refused.status_code == 409 and "does not exist" in refused.json()["detail"]
    assert not (settings_file.parent / "missing.env").exists()


def test_people_change_only_their_own_sql_password(settings_file):
    client = host()
    maya = person(client, sql_username="maya_sql")
    omar = person(client, name="Omar", sql_username="omar_sql")
    sofia = person(client, name="Sofia")
    browser = TestClient(main.app, client=COLLEAGUE, headers={"X-Client-Key": "maya-browser"})
    assert browser.post("/api/register", json={"person_id": maya["id"]}).status_code == 200

    saved = browser.put(f"/api/people/{maya['id']}/sql-password", json={"password": "maya secret"})
    assert saved.status_code == 200 and saved.json()["sql_password_set"] is True
    assert f'{SETTING.format(maya["id"])}="maya secret"' in contents(settings_file)
    with database.get_db() as db:
        actor = db.execute("SELECT actor FROM event_log WHERE action='sql_password_set'").fetchone()["actor"]
    assert actor == "Maya"
    cleared = browser.delete(f"/api/people/{maya['id']}/sql-password")
    assert cleared.status_code == 200 and cleared.json()["sql_password_set"] is False

    for method, path, body in [
        ("put", f"/api/people/{omar['id']}/sql-password", {"password": "guess"}),
        ("delete", f"/api/people/{omar['id']}/sql-password", None),
        ("patch", f"/api/people/{maya['id']}", {"sql_username": "maya_admin"}),
        ("delete", f"/api/people/{maya['id']}/sign-ins", None),
    ]:
        response = getattr(browser, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, (method, path)
        assert "host PC" in response.json()["detail"]
    assert "guess" not in contents(settings_file)

    other = TestClient(main.app, client=("203.0.113.10", 50000), headers={"X-Client-Key": "sofia-browser"})
    assert other.post("/api/register", json={"person_id": sofia["id"]}).status_code == 200
    missing = other.put(f"/api/people/{sofia['id']}/sql-password", json={"password": "secret"})
    assert missing.status_code == 409 and "SQL username" in missing.json()["detail"]


def test_concurrent_password_saves_never_lose_one(settings_file, monkeypatch):
    import threading
    import time

    from app import env_file, sql_passwords

    client = host()
    people = [person(client, name=f"Person {index}", sql_username=f"sql_{index}") for index in range(6)]
    write_value = env_file.write_value
    active, overlaps = [0], []

    def slow_write(path, name, value):
        # Widen the read-then-rewrite window so interleaved writers would lose data.
        active[0] += 1
        overlaps.append(active[0])
        time.sleep(0.02)
        write_value(path, name, value)
        active[0] -= 1
    monkeypatch.setattr(env_file, "write_value", slow_write)
    threads = [threading.Thread(target=sql_passwords.store, args=(item["id"], f"secret {item['id']}"))
               for item in people]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max(overlaps) == 1
    saved = contents(settings_file)
    for item in people:
        assert f'{SETTING.format(item["id"])}="secret {item["id"]}"' in saved
