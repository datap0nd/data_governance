from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

import app.database as database
from app.database import get_db
from app.routers import flows
from app.source_identity import upsert_postgres_identity


SERVER = "warehouse.example.test"
DATABASE = "analytics"
SCHEMA = "public"
RELATION = "Target"


@pytest.fixture
def flow_target_db(monkeypatch):
    # Avoid pytest's disabled ``current`` directory symlink on Windows ARM.
    with TemporaryDirectory(prefix="metronome-flow-target-") as folder:
        path = str(Path(folder) / "flow-target.db")
        monkeypatch.setattr(database, "DB_PATH", path)
        monkeypatch.setattr(flows, "UPLOAD_PGHOST", SERVER)
        database.init_db()
        yield path


def _request():
    return SimpleNamespace(state=SimpleNamespace(actor="Analyst"))


def _seed_target_flow(db):
    db.execute(
        """INSERT INTO flow_sites(id, name, adapter, base_url)
           VALUES (100, 'Portal', 'web_export', 'https://example.test')"""
    )
    db.execute(
        """INSERT INTO flow_reports(id, site_id, name, report_url)
           VALUES (100, 100, 'Export', 'https://example.test/export')"""
    )
    db.execute(
        """INSERT INTO flow_sql_catalog
               (database_name, schema_name, table_name, last_seen_at, stale)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP, 0)""",
        (DATABASE, SCHEMA, RELATION),
    )
    for source_id, name, relation in (
        (10, "public.Old", "Old"),
        (11, "public.Target [analytics@warehouse-a]", RELATION),
        (12, "public.Target [analytics@warehouse-b]", RELATION),
    ):
        db.execute(
            """INSERT INTO sources(id, name, type, archived)
               VALUES (?, ?, 'postgresql', 0)""",
            (source_id, name),
        )
        upsert_postgres_identity(
            db,
            source_id=source_id,
            server=SERVER,
            database=DATABASE,
            schema=SCHEMA,
            relation=relation,
            verified_at="2026-08-27T12:00:00+00:00",
        )
    db.execute(
        """INSERT INTO flows
               (id, name, site_id, report_id, target_folder, filename_template,
                period_strategy, sql_handoff_enabled, sql_mode, sql_database,
                sql_schema, sql_table, sql_target_source_id, updated_at)
           VALUES (20, 'Writer', 100, 100, 'C:\\Exports', 'x.csv', 'none',
                   1, 'append', ?, ?, ?, 10, '2001-02-03 04:05:06')""",
        (DATABASE, SCHEMA, RELATION),
    )


def _same_target_write(*, name="Writer renamed", source_id=None):
    return flows.FlowWrite(
        name=name,
        source_type="portal",
        site_id=100,
        report_id=100,
        target_folder=r"C:\Exports",
        filename_template="x.csv",
        period_strategy="none",
        download_mode="single",
        file_format="csv",
        schedule_type="manual",
        sql_handoff_enabled=True,
        sql_mode="append",
        sql_database=DATABASE,
        sql_schema=SCHEMA,
        sql_table=RELATION,
        sql_target_source_id=source_id,
    )


def test_flow_get_separates_persisted_and_effective_ids_without_mutation(
    flow_target_db,
):
    with get_db() as db:
        _seed_target_flow(db)
        before_changes = db.total_changes

        result = flows._flow_out(db, 20)
        stored = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=20"
        ).fetchone()

        assert db.total_changes == before_changes

    assert result["sql_target_source_id"] == 10
    assert result["sql_target_effective_source_id"] is None
    assert result["sql_target_link_status"] == "target_changed"
    assert result["sql_target_match_source_ids"] == [11, 12]
    assert stored["sql_target_source_id"] == 10
    assert stored["updated_at"] == "2001-02-03 04:05:06"


def test_flow_get_returns_exact_qualified_ambiguity_candidates(flow_target_db):
    with get_db() as db:
        _seed_target_flow(db)
        result = flows._flow_out(db, 20)

    assert result["sql_target_exact_candidates"] == [
        {"id": 11, "name": "public.Target"},
        {"id": 12, "name": "public.Target [analytics]"},
    ]
    # Exact structured identity IDs are not repeated as legacy text matches.
    assert result["sql_target_legacy_suggestions"] == []


@pytest.mark.parametrize("roundtrip_source_id", [None, 10])
def test_same_target_put_retains_invalid_persisted_id_when_ambiguous(
    flow_target_db, roundtrip_source_id
):
    with get_db() as db:
        _seed_target_flow(db)

    result = flows.update_flow(
        20,
        _same_target_write(source_id=roundtrip_source_id),
        _request(),
    )

    with get_db() as db:
        stored = db.execute(
            "SELECT name, sql_target_source_id FROM flows WHERE id=20"
        ).fetchone()
    assert stored["name"] == "Writer renamed"
    assert stored["sql_target_source_id"] == 10
    assert result["sql_target_source_id"] == 10
    assert result["sql_target_effective_source_id"] is None
    assert result["sql_target_link_status"] == "target_changed"


@pytest.mark.parametrize("roundtrip_source_id", [None, 10])
def test_same_target_put_repairs_invalid_id_when_one_exact_match_exists(
    flow_target_db, roundtrip_source_id
):
    with get_db() as db:
        _seed_target_flow(db)
        db.execute("DELETE FROM source_postgres_identities WHERE source_id=12")

    result = flows.update_flow(
        20,
        _same_target_write(source_id=roundtrip_source_id),
        _request(),
    )

    with get_db() as db:
        stored = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=20"
        ).fetchone()
    assert stored["sql_target_source_id"] == 11
    assert result["sql_target_source_id"] == 11
    assert result["sql_target_effective_source_id"] == 11
    assert result["sql_target_link_status"] == "confirmed"
