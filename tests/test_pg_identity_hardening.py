from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.database as database
from app.database import get_db, init_db
from app.routers import materialized_views
from app.scanner import pg_deps, runner
from app.scanner.tmdl_parser import ParsedTable, SourceInfo
from app.scanner.walker import DiscoveredReport
from app.source_identity import exact_identity_rows, upsert_postgres_identity


NOW = "2026-08-27T12:00:00+00:00"


@pytest.fixture()
def identity_db(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "governance.db")
        monkeypatch.setattr(database, "DB_PATH", db_path)
        init_db()
        yield db_path


def _source(db, source_id: int, name: str, *, schedule: str | None = None) -> None:
    db.execute(
        """INSERT INTO sources
               (id, name, type, connection_info, discovered_by, archived,
                refresh_schedule, created_at, updated_at)
           VALUES (?, ?, 'postgresql', ?, 'test', 0, ?, ?, ?)""",
        (source_id, name, name, schedule, NOW, NOW),
    )


def _identity(
    db,
    source_id: int,
    *,
    database_name: str,
    relation: str = "orders",
    kind: str = "table",
) -> None:
    result = upsert_postgres_identity(
        db,
        source_id=source_id,
        server="db.internal",
        database=database_name,
        schema="sales",
        relation=relation,
        relation_kind=kind,
        verified_at=NOW,
    )
    assert result["status"] in {"claimed", "refreshed"}


def test_source_resolution_ignores_fuzzy_labels_and_is_database_aware(identity_db):
    with get_db() as db:
        _source(db, 1, "sales.orders")
        _source(db, 2, "staging sales.orders")
        _identity(db, 2, database_name="staging")

        resolved = pg_deps._find_or_create_source(
            db,
            server="DB.INTERNAL:5432",
            database="warehouse",
            schema="sales",
            table="orders",
            now=NOW,
        )
        resolved_again = pg_deps._find_or_create_source(
            db,
            server="db.internal",
            database="warehouse",
            schema="sales",
            table="orders",
            now=NOW,
        )

        assert resolved == resolved_again
        assert resolved not in {1, 2}
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 3
        assert len(
            exact_identity_rows(
                db,
                server="db.internal",
                database="warehouse",
                schema="sales",
                relation="orders",
            )
        ) == 1
        staging = db.execute(
            "SELECT database_name FROM source_postgres_identities WHERE source_id=2"
        ).fetchone()
        assert staging["database_name"] == "staging"


def test_source_resolution_fails_on_ambiguous_exact_identity(identity_db):
    with get_db() as db:
        _source(db, 1, "orders one")
        _source(db, 2, "orders two")
        _identity(db, 1, database_name="warehouse")
        _identity(db, 2, database_name="warehouse")
        before = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

        with pytest.raises(pg_deps.PostgresIdentityResolutionError, match="Ambiguous"):
            pg_deps._find_or_create_source(
                db,
                server="db.internal",
                database="warehouse",
                schema="sales",
                table="orders",
                now=NOW,
            )

        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == before


def test_guarded_claim_never_overwrites_an_existing_physical_identity(identity_db):
    with get_db() as db:
        _source(db, 1, "sales.orders")
        _identity(db, 1, database_name="staging")

        with pytest.raises(pg_deps.PostgresIdentityResolutionError, match="different"):
            pg_deps._claim_identity(
                db,
                source_id=1,
                server="db.internal",
                database="warehouse",
                schema="sales",
                relation="orders",
                relation_kind="table",
                verified_at=NOW,
            )

        row = db.execute(
            """SELECT server_name, database_name, schema_name, relation_name
               FROM source_postgres_identities WHERE source_id=1"""
        ).fetchone()
        assert tuple(row) == ("db.internal", "staging", "sales", "orders")


def test_materialized_view_refresh_identity_is_source_and_database_aware(
    identity_db, monkeypatch
):
    monkeypatch.setattr(materialized_views, "UPLOAD_PGHOST", "db.internal")

    with get_db() as db:
        _source(db, 1, "warehouse MV")
        _identity(
            db,
            1,
            database_name="warehouse",
            relation="orders_mv",
            kind="materialized_view",
        )
        _source(db, 2, "staging MV")
        _identity(
            db,
            2,
            database_name="staging",
            relation="orders_mv",
            kind="materialized_view",
        )

    warehouse = materialized_views._materialized_view_identity(1)
    staging = materialized_views._materialized_view_identity(2)

    assert warehouse["database_name"] == "warehouse"
    assert staging["database_name"] == "staging"
    assert warehouse["relation_name"] == staging["relation_name"] == "orders_mv"


def test_materialized_view_refresh_rejects_a_non_materialized_source(
    identity_db, monkeypatch
):
    monkeypatch.setattr(materialized_views, "UPLOAD_PGHOST", "db.internal")

    with get_db() as db:
        _source(db, 1, "orders table")
        _identity(
            db,
            1,
            database_name="warehouse",
            relation="orders",
            kind="table",
        )

    with pytest.raises(HTTPException, match="not a materialized view") as exc_info:
        materialized_views._materialized_view_identity(1)
    assert exc_info.value.status_code == 400


class _PgCursor:
    def __init__(self, dependency_rows):
        self._dependency_rows = dependency_rows
        self._definition_query = False

    def execute(self, sql):
        self._definition_query = "FROM pg_matviews" in sql

    def fetchall(self):
        return [] if self._definition_query else list(self._dependency_rows)


class _PgConnection:
    def __init__(self, dependency_rows):
        self._dependency_rows = dependency_rows
        self.closed = False

    def cursor(self):
        return _PgCursor(self._dependency_rows)

    def close(self):
        self.closed = True


def test_pg_scan_reconciles_once_after_the_complete_identity_batch(
    identity_db, monkeypatch
):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    connection = _PgConnection(
        [("sales", "report_mv", "sales", "orders", "r")]
    )
    monkeypatch.setattr(pg_deps, "_get_pg_connection", lambda: connection)
    reconciliations = []
    monkeypatch.setattr(
        pg_deps,
        "_reconcile_database_flows",
        lambda db, database: reconciliations.append(database) or {},
    )

    with get_db() as db:
        _source(db, 1, "Report MV")
        _identity(
            db,
            1,
            database_name="warehouse",
            relation="report_mv",
            kind="materialized_view",
        )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["mvs_found"] == 1
    assert result["deps_created"] == 1
    assert reconciliations == ["warehouse"]
    assert connection.closed
    with get_db() as db:
        dependency = db.execute(
            "SELECT source_id, depends_on_id FROM source_dependencies"
        ).fetchone()
        assert dependency["source_id"] == 1
        target = exact_identity_rows(
            db,
            server="db.internal",
            database="warehouse",
            schema="sales",
            relation="orders",
        )
        assert [int(row["source_id"]) for row in target] == [dependency["depends_on_id"]]


def test_pg_scan_rolls_back_when_a_tracked_mv_identity_is_ambiguous(
    identity_db, monkeypatch
):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    connection = _PgConnection(
        [("sales", "report_mv", "sales", "orders", "r")]
    )
    monkeypatch.setattr(pg_deps, "_get_pg_connection", lambda: connection)

    with get_db() as db:
        _source(db, 1, "Report MV one")
        _source(db, 2, "Report MV two")
        _source(db, 3, "Prior dependency")
        for source_id in (1, 2):
            _identity(
                db,
                source_id,
                database_name="warehouse",
                relation="report_mv",
                kind="materialized_view",
            )
        _identity(db, 3, database_name="warehouse", relation="prior_orders")
        db.execute(
            """INSERT INTO source_dependencies
                   (source_id, depends_on_id, discovered_by, created_at)
               VALUES (1, 3, 'pg_matviews', ?)""",
            (NOW,),
        )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "failed"
    assert "Ambiguous PostgreSQL identity" in result["error"]
    assert connection.closed
    with get_db() as db:
        edges = db.execute(
            """SELECT source_id, depends_on_id FROM source_dependencies
               WHERE discovered_by='pg_matviews'"""
        ).fetchall()
        assert [tuple(row) for row in edges] == [(1, 3)]


def _stub_runner_followups(monkeypatch, reports=None) -> None:
    from app import usage
    from app.routers import best_practices, documentation, schedules
    from app.scanner import pg_cron

    monkeypatch.setattr(runner, "_backup_db", lambda: None)
    monkeypatch.setattr(runner, "walk_reports_root", lambda _root: reports or [])
    monkeypatch.setattr(
        pg_deps,
        "scan_pg_dependencies",
        lambda scan_run_id=None: {
            "status": "completed",
            "changed_queries": 0,
            "query_change_log": "",
        },
    )
    monkeypatch.setattr(pg_cron, "scan_pg_cron", lambda: {"status": "completed"})
    monkeypatch.setattr(
        usage,
        "sync_usage_from_csv_if_configured",
        lambda _db: {"status": "not_requested"},
    )
    monkeypatch.setattr(
        best_practices,
        "run_best_practice_scan",
        lambda persist=False: {"status": "completed"},
    )
    monkeypatch.setattr(
        schedules,
        "run_schedule_discrepancy_scan",
        lambda persist=True: {"status": "completed"},
    )
    monkeypatch.setattr(
        documentation,
        "sync_documentation_completeness_actions",
        lambda: {"status": "completed"},
    )


def test_runner_legacy_cleanup_preserves_identified_database_sources(
    identity_db, monkeypatch
):
    _stub_runner_followups(monkeypatch)
    monkeypatch.setattr(runner, "PGHOST", "db.internal")
    monkeypatch.setattr(runner, "PGDATABASE", "warehouse")

    expected_names = {
        1: "warehouse.sales.orders",
        3: "sales.customers [warehouse@db.internal]",
        4: "sales.(quoted_orders)",
        5: "db.internal/warehouse/sales.products",
        7: "sales.inventory",
    }
    with get_db() as db:
        for source_id, name in expected_names.items():
            _source(db, source_id, name)
            _identity(
                db,
                source_id,
                database_name="warehouse",
                relation={
                    1: "orders",
                    3: "customers",
                    4: "quoted_orders",
                    5: "products",
                    7: "inventory",
                }[source_id],
            )
        # This unidentified source has the normalized label that source 1's
        # legacy database prefix would otherwise collide and merge into.
        _source(db, 2, "sales.orders")
        # The reverse direction is equally unsafe: an unidentified prefixed
        # source must not merge into an identified normalized-name source.
        _source(db, 6, "warehouse.sales.inventory")
        db.execute(
            "INSERT INTO reports(id, name, archived) VALUES (1, 'Existing report', 0)"
        )
        db.execute(
            """INSERT INTO report_tables(report_id, table_name, source_id)
               VALUES (1, 'Orders', 1)"""
        )
        db.execute(
            """INSERT INTO report_tables(report_id, table_name, source_id)
               VALUES (1, 'Inventory', 6)"""
        )

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identified = db.execute(
            """SELECT id, name, archived FROM sources
               WHERE id IN (1, 3, 4, 5, 7) ORDER BY id"""
        ).fetchall()
        report_sources = {
            row["table_name"]: int(row["source_id"])
            for row in db.execute(
                """SELECT table_name, source_id FROM report_tables
                   WHERE report_id=1 ORDER BY table_name"""
            ).fetchall()
        }

    assert {int(row["id"]): row["name"] for row in identified} == expected_names
    assert all(int(row["archived"]) == 0 for row in identified)
    assert report_sources == {"Inventory": 6, "Orders": 1}


def test_postgres_connection_key_preserves_identifiers_and_normalizes_only_host():
    first = SourceInfo(
        source_type="postgresql",
        server="DB.INTERNAL:5432",
        database='"Warehouse"',
        sql_table='"Sales"."Orders"',
    )
    host_alias = SourceInfo(
        source_type="postgresql",
        server="db.internal",
        database='"Warehouse"',
        sql_table='"Sales"."Orders"',
    )
    different_case = SourceInfo(
        source_type="postgresql",
        server="db.internal",
        database='"warehouse"',
        sql_table='"sales"."orders"',
    )

    assert first.connection_key == host_alias.connection_key
    assert first.connection_key != different_case.connection_key
    assert '"Warehouse"' in first.connection_key
    assert '"Sales"."Orders"' in first.connection_key


def _pg_report(name: str, database_name: str) -> DiscoveredReport:
    source = SourceInfo(
        source_type="postgresql",
        server="db.internal",
        database=database_name,
        sql_table="sales.orders",
        raw_expression=f"PostgreSQL.Database db.internal {database_name}",
    )
    return DiscoveredReport(
        name=name,
        tmdl_path=f"C:/{name}",
        tables=[
            ParsedTable(
                table_name="Orders",
                m_expression=source.raw_expression,
                source=source,
            )
        ],
    )


def test_tmdl_scan_does_not_claim_unidentified_same_display_source(
    identity_db, monkeypatch
):
    reports = [
        _pg_report("Warehouse report", "warehouse"),
        _pg_report("Staging report", "staging"),
    ]
    _stub_runner_followups(monkeypatch, reports)
    monkeypatch.setattr(runner, "PGHOST", "db.internal")
    monkeypatch.setattr(runner, "PGDATABASE", "warehouse")

    with get_db() as db:
        _source(db, 1, "sales.orders")

    first = runner.run_scan("unused", run_followup_probe=False)
    second = runner.run_scan("unused", run_followup_probe=False)

    assert first["status"] == "completed"
    assert first["new_sources"] == 2
    assert second["status"] == "completed"
    assert second["new_sources"] == 0
    with get_db() as db:
        legacy_identity = db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=1"
        ).fetchone()
        exact_sources = {
            row["database_name"]: int(row["source_id"])
            for row in db.execute(
                """SELECT database_name, source_id
                   FROM source_postgres_identities
                   WHERE server_name='db.internal'
                     AND schema_name='sales' AND relation_name='orders'"""
            ).fetchall()
        }
        report_sources = {
            row["name"]: int(row["source_id"])
            for row in db.execute(
                """SELECT r.name, rt.source_id
                   FROM reports r JOIN report_tables rt ON rt.report_id=r.id
                   WHERE rt.table_name='Orders'"""
            ).fetchall()
        }
        source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    assert legacy_identity is None
    assert exact_sources.keys() == {"warehouse", "staging"}
    assert 1 not in exact_sources.values()
    assert exact_sources["warehouse"] != exact_sources["staging"]
    assert report_sources == {
        "Warehouse report": exact_sources["warehouse"],
        "Staging report": exact_sources["staging"],
    }
    assert source_count == 3


def test_tmdl_scan_rolls_back_on_ambiguous_exact_identity(identity_db, monkeypatch):
    reports = [_pg_report("Warehouse report", "warehouse")]
    _stub_runner_followups(monkeypatch, reports)

    with get_db() as db:
        _source(db, 1, "Orders one")
        _source(db, 2, "Orders two")
        for source_id in (1, 2):
            _identity(db, source_id, database_name="warehouse")

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "failed"
    assert result["error"] == "Redacted; review server logs."
    assert result["components"]["core"]["status"] == "failed"
    assert result["components"]["core"]["error"] == "Redacted; review server logs."
    with get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
        scan = db.execute(
            "SELECT status, components_json, log FROM scan_runs WHERE id=?",
            (result["scan_id"],),
        ).fetchone()
    persisted_components = json.loads(scan["components_json"])
    assert scan["status"] == "failed"
    assert scan["log"] == "Core discovery failed; review server logs."
    assert persisted_components["core"]["status"] == "failed"
    assert persisted_components["core"]["error"] == "Redacted; review server logs."
