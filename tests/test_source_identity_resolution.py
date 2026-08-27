from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import app.database as database
import app.main as main
from app.database import get_db
from app.source_identity import (
    flow_link_status,
    inspect_flow_target,
    reconcile_all_flow_targets,
    reconcile_flow_target,
    upsert_postgres_identity,
)


@pytest.fixture
def identity_db(monkeypatch):
    # Avoid pytest's Windows ``current`` directory symlink, which is disabled
    # on some of the ARM test hosts used for this project.
    with TemporaryDirectory(prefix="metronome-source-identity-") as folder:
        path = str(Path(folder) / "source-identity.db")
        monkeypatch.setattr(database, "DB_PATH", path)
        monkeypatch.setattr(main, "UPLOAD_PGHOST", "db.example.test")
        database.init_db()
        yield path


def _seed_flow_shell(db):
    db.execute("INSERT INTO flow_sites(id, name) VALUES (100, 'Site')")
    db.execute(
        """INSERT INTO flow_reports(id, site_id, name, report_url)
           VALUES (100, 100, 'Report', 'https://example.test/report')"""
    )


def _add_source(db, source_id, name, *, database_name="analytics", relation="Target"):
    db.execute(
        "INSERT INTO sources(id, name, type, archived) VALUES (?, ?, 'postgresql', 0)",
        (source_id, name),
    )
    return upsert_postgres_identity(
        db,
        source_id=source_id,
        server="DB.EXAMPLE.TEST:5432",
        database=database_name,
        schema="public",
        relation=relation,
        verified_at="2026-01-01T00:00:00+00:00",
    )


def _add_flow(db, flow_id=1, *, linked_id=None, enabled=1):
    db.execute(
        """INSERT INTO flows
               (id, name, site_id, report_id, target_folder, filename_template,
                sql_handoff_enabled, sql_database, sql_schema, sql_table,
                sql_target_source_id, updated_at)
           VALUES (?, ?, 100, 100, 'C:\\Exports', 'x.csv', ?, 'analytics',
                   'public', 'Target', ?, '2001-02-03 04:05:06')""",
        (flow_id, f"Writer {flow_id}", enabled, linked_id),
    )


def test_identity_claim_refresh_and_conflict_are_guarded(identity_db):
    with get_db() as db:
        db.execute(
            "INSERT INTO sources(id, name, type, archived) VALUES (1, 'public.Target', 'postgresql', 0)"
        )
        claimed = upsert_postgres_identity(
            db,
            source_id=1,
            server="DB.EXAMPLE.TEST:5432",
            database="analytics",
            schema="public",
            relation="Target",
            verified_at="first",
        )
        refreshed = upsert_postgres_identity(
            db,
            source_id=1,
            server="db.example.test",
            database="analytics",
            schema="public",
            relation="Target",
            relation_kind="materialized_view",
            verified_at="second",
        )
        conflict = upsert_postgres_identity(
            db,
            source_id=1,
            server="db.example.test",
            database="staging",
            schema="public",
            relation="Target",
            verified_at="third",
        )
        stored = db.execute(
            "SELECT * FROM source_postgres_identities WHERE source_id=1"
        ).fetchone()

    assert claimed["status"] == "claimed"
    assert refreshed["status"] == "refreshed"
    assert conflict["status"] == "conflict"
    assert conflict["existing"]["database"] == "analytics"
    assert stored["database_name"] == "analytics"
    assert stored["relation_kind"] == "materialized_view"
    assert stored["verified_at"] == "second"


def test_valid_stored_link_wins_duplicate_candidates_without_timestamp_churn(identity_db):
    with get_db() as db:
        _seed_flow_shell(db)
        _add_source(db, 1, "public.Target")
        _add_source(db, 2, "public.Target duplicate")
        _add_flow(db, linked_id=1)

        inspected = inspect_flow_target(db, 1, server="db.example.test")
        result = reconcile_flow_target(db, 1, server="db.example.test")
        stored = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=1"
        ).fetchone()

    assert inspected["status"] == "confirmed"
    assert inspected["persisted_valid"] is True
    assert inspected["effective_source_id"] == 1
    assert inspected["matches"] == [1, 2]
    assert result == {"status": "confirmed", "source_id": 1, "matches": [1]}
    assert stored["sql_target_source_id"] == 1
    assert stored["updated_at"] == "2001-02-03 04:05:06"


def test_reads_are_pure_and_unique_effective_target_reconciles_once(identity_db):
    with get_db() as db:
        _seed_flow_shell(db)
        _add_source(db, 1, "public.Target")
        _add_flow(db)
        flow = db.execute("SELECT * FROM flows WHERE id=1").fetchone()
        before_changes = db.total_changes

        inspected = inspect_flow_target(db, flow, server="db.example.test")
        compatible = flow_link_status(db, flow, server="db.example.test")
        after_reads = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=1"
        ).fetchone()

        assert db.total_changes == before_changes
        assert inspected["status"] == "confirmed"
        assert inspected["persisted_source_id"] is None
        assert inspected["effective_source_id"] == 1
        assert compatible["source_id"] == 1
        assert after_reads["sql_target_source_id"] is None
        assert after_reads["updated_at"] == "2001-02-03 04:05:06"

        reconciled = reconcile_flow_target(db, 1, server="db.example.test")
        db.execute("UPDATE flows SET updated_at='keep-me' WHERE id=1")
        reconciled_again = reconcile_flow_target(db, 1, server="db.example.test")
        after_reconcile = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=1"
        ).fetchone()

    assert reconciled == {"status": "confirmed", "source_id": 1, "matches": [1]}
    assert reconciled_again == reconciled
    assert after_reconcile["sql_target_source_id"] == 1
    assert after_reconcile["updated_at"] == "keep-me"


def test_invalid_link_is_retained_until_one_exact_replacement_exists(identity_db):
    with get_db() as db:
        _seed_flow_shell(db)
        _add_source(db, 1, "public.Old", relation="Old")
        _add_source(db, 2, "public.Target A")
        _add_source(db, 3, "public.Target B")
        _add_flow(db, linked_id=1)

        ambiguous = inspect_flow_target(db, 1, server="db.example.test")
        ambiguous_reconcile = reconcile_flow_target(db, 1, server="db.example.test")
        retained = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=1"
        ).fetchone()

        db.execute("DELETE FROM source_postgres_identities WHERE source_id=3")
        changed = inspect_flow_target(db, 1, server="db.example.test")
        confirmed = reconcile_flow_target(db, 1, server="db.example.test")
        repaired = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=1"
        ).fetchone()

    assert ambiguous["status"] == "target_changed"
    assert ambiguous["effective_source_id"] is None
    assert ambiguous["matches"] == [2, 3]
    assert ambiguous_reconcile == {
        "status": "target_changed",
        "source_id": None,
        "matches": [2, 3],
    }
    assert retained["sql_target_source_id"] == 1
    assert retained["updated_at"] == "2001-02-03 04:05:06"
    assert changed["status"] == "target_changed"
    assert changed["effective_source_id"] == 2
    assert confirmed == {"status": "confirmed", "source_id": 2, "matches": [2]}
    assert repaired["sql_target_source_id"] == 2


def test_disabled_flow_reconciliation_retains_link_for_explicit_edit_path(identity_db):
    with get_db() as db:
        _seed_flow_shell(db)
        _add_source(db, 1, "public.Target")
        _add_flow(db, linked_id=1, enabled=0)
        result = reconcile_flow_target(db, 1, server="db.example.test")
        stored = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=1"
        ).fetchone()

    assert result == {"status": "disabled", "source_id": None, "matches": []}
    assert stored["sql_target_source_id"] == 1
    assert stored["updated_at"] == "2001-02-03 04:05:06"


def test_startup_reconciliation_is_empty_safe_and_idempotent(identity_db):
    empty = main._reconcile_startup_flow_targets()
    assert empty["total"] == 0
    assert empty["changed"] == 0

    with get_db() as db:
        _seed_flow_shell(db)
        _add_source(db, 1, "public.Target")
        _add_flow(db)

    first = main._reconcile_startup_flow_targets()
    with get_db() as db:
        db.execute("UPDATE flows SET updated_at='stable' WHERE id=1")
    second = main._reconcile_startup_flow_targets()

    with get_db() as db:
        stored = db.execute(
            "SELECT sql_target_source_id, updated_at FROM flows WHERE id=1"
        ).fetchone()
        direct = reconcile_all_flow_targets(db, server="db.example.test")

    assert first["changed"] == 1
    assert second["changed"] == 0
    assert direct["changed"] == 0
    assert stored["sql_target_source_id"] == 1
    assert stored["updated_at"] == "stable"
