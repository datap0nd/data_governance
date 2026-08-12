from types import SimpleNamespace

import pytest

from app import database
from app.models import SourceUpdate
from app.routers import sources


@pytest.fixture
def source_db(tmp_path):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    return database.DB_PATH


def _request(actor="Test User"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _insert_source(db, source_id, name, **fields):
    columns = ["id", "name", "type", *fields.keys()]
    values = [source_id, name, "excel", *fields.values()]
    placeholders = ", ".join("?" for _ in values)
    db.execute(
        f"INSERT INTO sources ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def test_manual_source_owner_update_persists(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Source One")

    updated = sources.update_source(
        1,
        SourceUpdate(owner="Owner One"),
        _request(),
    )

    assert updated.owner == "Owner One"
    with database.get_db() as db:
        assert db.execute("SELECT owner FROM sources WHERE id = 1").fetchone()["owner"] == "Owner One"


def test_auto_assign_owners_uses_unique_majority_and_skips_ties(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Majority Source")
        _insert_source(db, 2, "Tied Source")
        _insert_source(db, 3, "Existing Source", owner="Existing Owner")
        _insert_source(db, 4, "No Reports Source")
        db.executemany(
            "INSERT INTO reports (id, name, owner, archived) VALUES (?, ?, ?, 0)",
            [
                (1, "Report A", "Owner X"),
                (2, "Report B", "Owner X"),
                (3, "Report C", "Owner Y"),
                (4, "Report D", "Owner X"),
                (5, "Report E", "Owner Y"),
            ],
        )
        db.executemany(
            "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (?, ?, ?)",
            [
                (1, "Table A", 1),
                (1, "Table A duplicate source", 1),
                (2, "Table B", 1),
                (3, "Table C", 1),
                (4, "Table D", 2),
                (5, "Table E", 2),
                (1, "Existing Table", 3),
            ],
        )

    result = sources.auto_assign_source_owners(_request())

    assert result["assigned"] == 1
    assert result["skipped_ties"] == 1
    assert result["skipped_no_report_owner"] == 1
    assert result["assignments"][0]["owner"] == "Owner X"
    assert result["assignments"][0]["owner_report_count"] == 2
    assert result["assignments"][0]["total_owned_reports"] == 3
    with database.get_db() as db:
        rows = db.execute("SELECT id, owner FROM sources ORDER BY id").fetchall()
        assert [(row["id"], row["owner"]) for row in rows] == [
            (1, "Owner X"),
            (2, None),
            (3, "Existing Owner"),
            (4, None),
        ]


def test_auto_set_freshness_rules_uses_explicit_source_schedules_only(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Weekly Source", refresh_schedule="Monday")
        _insert_source(db, 2, "Cron Source", refresh_schedule="0 6 * * MON-FRI")
        _insert_source(db, 3, "Daily Source", refresh_schedule="@daily")
        _insert_source(db, 4, "Disabled Source", refresh_schedule="0 6 * * 2 (disabled)")
        _insert_source(db, 5, "Unknown Source", refresh_schedule="whenever ready")
        _insert_source(
            db,
            6,
            "Existing Rule Source",
            refresh_schedule="Tuesday",
            freshness_rule_type="custom",
            custom_fresh_days=4,
        )

    result = sources.auto_set_source_freshness_rules(_request())

    assert result["configured"] == 3
    assert result["skipped_unsupported_schedule"] == 2
    with database.get_db() as db:
        rows = {
            row["id"]: dict(row)
            for row in db.execute(
                """SELECT id, freshness_rule_type, custom_fresh_days,
                          freshness_schedule_days
                   FROM sources ORDER BY id"""
            ).fetchall()
        }
    assert rows[1]["freshness_rule_type"] == "fixed"
    assert rows[1]["freshness_schedule_days"] == "Monday"
    assert rows[2]["freshness_rule_type"] == "fixed"
    assert rows[2]["freshness_schedule_days"] == "Monday,Tuesday,Wednesday,Thursday,Friday"
    assert rows[3]["freshness_rule_type"] == "daily"
    assert rows[3]["custom_fresh_days"] == 1
    assert rows[4]["freshness_rule_type"] is None
    assert rows[5]["freshness_rule_type"] is None
    assert rows[6]["freshness_rule_type"] == "custom"
    assert rows[6]["custom_fresh_days"] == 4


@pytest.mark.parametrize(
    ("schedule", "rule_type", "days"),
    [
        ("Weekly - Tuesday", "fixed", ["Tuesday"]),
        ("0 7 * * 1,3,5", "fixed", ["Monday", "Wednesday", "Friday"]),
        ("15 * * * *", "daily", []),
    ],
)
def test_freshness_schedule_parser(schedule, rule_type, days):
    rule = sources._freshness_rule_from_schedule(schedule)
    assert rule["rule_type"] == rule_type
    assert rule["refresh_days"] == days

