"""Users retain BI/Business profiles and optional future SQL-owner mappings."""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.routers import flows, people
from test_flows import flow_db, _flow, _mark_discovered, _request, _seed_catalog


@pytest.fixture
def users_client(flow_db):
    app = FastAPI()
    app.include_router(people.router)
    with TestClient(app) as client:
        yield client


def _create(client, **values):
    return client.post("/api/people", json={"name": "Dana", "role": "BI", **values})


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_profile_name_is_rejected(users_client, name):
    assert _create(users_client, name=name).status_code == 422
    assert users_client.get("/api/people").json() == []


@pytest.mark.parametrize("role", ["BI", "Business"])
def test_user_profiles_remain_backwards_compatible(users_client, role):
    response = _create(users_client, role=role, email=" dana@example.test ")
    assert response.status_code == 201
    saved = response.json()
    assert saved["role"] == role
    assert saved["email"] == "dana@example.test"
    assert saved["sql_username"] is None
    assert users_client.get("/api/people/roles").json() == ["BI", "Business"]
    assert users_client.get("/api/people").json() == [saved]


def test_sql_user_link_preserves_case_and_quoted_identifier_characters(users_client):
    # The stored value is a literal identity, never an executable SQL fragment.
    sql_user = 'Dana "BI"; reporting'
    response = _create(users_client, sql_username=f"  {sql_user}  ")
    assert response.status_code == 201
    saved = response.json()
    assert saved["sql_username"] == sql_user
    assert users_client.get("/api/people").json()[0]["sql_username"] == sql_user

    updated = users_client.patch(f"/api/people/{saved['id']}", json={"name": "Dana Park"})
    assert updated.status_code == 200
    assert updated.json()["sql_username"] == sql_user
    assert updated.json()["name"] == "Dana Park"

    linked = users_client.patch(f"/api/people/{saved['id']}", json={"sql_username": " bi_dana "})
    assert linked.status_code == 200
    assert linked.json()["sql_username"] == "bi_dana"
    with database.get_db() as db:
        details = db.execute("SELECT detail FROM event_log WHERE action='updated'").fetchall()
    assert [row["detail"] for row in details] == ["name", "sql_username"]


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_sql_user_link_can_be_cleared_without_changing_profile(users_client, empty):
    saved = _create(users_client, sql_username="bi_dana", email="dana@example.test").json()
    response = users_client.patch(f"/api/people/{saved['id']}", json={"sql_username": empty})
    assert response.status_code == 200
    assert response.json() == {**saved, "sql_username": None}


@pytest.mark.parametrize("invalid", ["x" * 64, "é" * 32, "bi\x00dana", "bi\ndana", 123])
def test_invalid_sql_user_is_rejected_without_partial_profile_updates(users_client, invalid):
    saved = _create(users_client, sql_username="bi_dana").json()
    response = users_client.patch(
        f"/api/people/{saved['id']}", json={"name": "Changed", "sql_username": invalid},
    )
    assert response.status_code == 422
    assert users_client.get("/api/people").json() == [saved]
    assert _create(users_client, sql_username=invalid).status_code == 422
    assert users_client.get("/api/people").json() == [saved]


def test_sql_user_utf8_boundary_and_existing_profile_errors(users_client):
    saved = _create(users_client, sql_username="é" * 31 + "a").json()
    assert len(saved["sql_username"].encode("utf-8")) == 63
    assert users_client.patch(f"/api/people/{saved['id']}", json={}).status_code == 400
    assert users_client.patch(f"/api/people/{saved['id']}", json={"role": "Admin"}).status_code == 422
    assert users_client.patch("/api/people/99999", json={"sql_username": "other"}).status_code == 404
    assert users_client.get("/api/people").json() == [saved]


def test_flow_ownership_tracks_profile_identity_and_survives_user_deletion(users_client):
    saved = _create(users_client, sql_username="bi_dana").json()
    site, report = _seed_catalog()
    _mark_discovered(report["id"])
    flow = flows.create_flow(
        _flow(site["id"], report["id"], owner_person_id=saved["id"]), _request(),
    )
    assert flow["owner_person_id"] == saved["id"]
    assert flow["owner_sql_username"] == "bi_dana"

    assert users_client.patch(f"/api/people/{saved['id']}", json={
        "name": "Dana Park", "sql_username": "bi_dana_park",
    }).status_code == 200
    renamed = next(item for item in flows.list_flows() if item["id"] == flow["id"])
    assert renamed["owner_person_id"] == saved["id"]
    assert renamed["owner_name"] == "Dana Park"
    assert renamed["owner_sql_username"] == "bi_dana_park"

    assert users_client.delete(f"/api/people/{saved['id']}").status_code == 200
    unassigned = flows.get_flow(flow["id"])
    assert unassigned["id"] == flow["id"]
    assert unassigned["owner_person_id"] is None
    assert unassigned["owner_name"] is None
    assert unassigned["owner_sql_username"] is None


def test_legacy_people_migration_preserves_profiles_and_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-people.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE people (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                role TEXT NOT NULL, email TEXT, include_all_alerts INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO people (id, name, role, email)
            VALUES (17, 'Existing User', 'Business', 'existing@example.test');
        """)
    database.init_db()
    with database.get_db() as db:
        row = dict(db.execute("SELECT * FROM people WHERE id=17").fetchone())
        assert row["name"] == "Existing User"
        assert row["role"] == "Business"
        assert row["email"] == "existing@example.test"
        assert row["sql_username"] is None
        db.execute("UPDATE people SET sql_username='existing_sql' WHERE id=17")
    database.init_db()
    with database.get_db() as db:
        assert dict(db.execute("SELECT * FROM people WHERE id=17").fetchone()) == {
            **row, "sql_username": "existing_sql",
        }
