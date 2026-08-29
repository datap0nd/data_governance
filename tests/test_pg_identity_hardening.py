from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.database as database
from app.database import get_db, init_db
from app.routers import materialized_views
from app.scanner import pg_deps, prober, runner
from app.scanner.tmdl_parser import ParsedTable, SourceInfo, _parse_m_expression
from app.scanner.walker import DiscoveredReport
from app.source_identity import (
    exact_identity_rows,
    normalize_server,
    postgres_server_identity,
    upsert_postgres_identity,
)


NOW = "2026-08-27T12:00:00+00:00"


def test_postgres_server_identity_keeps_non_default_ports_distinct():
    assert postgres_server_identity("DB.INTERNAL:5432") == "db.internal"
    assert postgres_server_identity("db.internal") == "db.internal"
    assert postgres_server_identity("DB.INTERNAL", 5433) == "db.internal:5433"
    assert postgres_server_identity("db.internal:5433") == "db.internal:5433"
    assert postgres_server_identity("db.internal:5433") != postgres_server_identity(
        "db.internal:5434"
    )


@pytest.mark.parametrize(
    "value",
    ["db.internal:abc", "db.internal:99999", "db.internal:", "[::1]:abc"],
)
def test_postgres_server_identity_rejects_invalid_explicit_ports(value):
    assert postgres_server_identity(value) == ""


@pytest.mark.parametrize("port", ["abc", 0, 65536])
def test_postgres_server_identity_rejects_invalid_separate_ports(port):
    assert postgres_server_identity("db.internal", port) == ""


@pytest.mark.parametrize(
    ("value", "port", "expected"),
    [
        ("[::1]", None, "[::1]"),
        ("[::1]:5432", None, "[::1]"),
        ("::1", None, "[::1]"),
        ("::1", 5433, "[::1]:5433"),
        ("postgresql://[2001:db8::10]:5433/warehouse", None, "[2001:db8::10]:5433"),
    ],
)
def test_postgres_ipv6_server_identity_is_canonical_and_idempotent(
    value,
    port,
    expected,
):
    identity = postgres_server_identity(value, port)

    assert identity == expected
    assert normalize_server(identity) == expected
    assert postgres_server_identity(identity) == expected


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


def test_probe_routes_same_relation_to_each_exact_database_and_endpoint(
    identity_db,
    monkeypatch,
):
    from app.checks import data_quality

    monkeypatch.setattr(prober, "PGHOST", "primary.internal")
    monkeypatch.setattr(prober, "PGPORT", 5432)
    monkeypatch.setattr(prober, "UPLOAD_PGHOST", "flow.internal")
    monkeypatch.setattr(prober, "UPLOAD_PGPORT", 5432)
    monkeypatch.setattr(data_quality, "run_quality_checks", lambda: {})

    class SqlExpression:
        def __init__(self, value=""):
            self.value = value

        def format(self, *_args):
            return self

    fake_sql = SimpleNamespace(
        SQL=lambda value: SqlExpression(value),
        Identifier=lambda value: SqlExpression(value),
    )
    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(sql=fake_sql))

    class Cursor:
        def __init__(self, row_count):
            self.row_count = row_count

        def execute(self, _query):
            return None

        def fetchone(self):
            return (datetime.now(timezone.utc), self.row_count)

    class Connection:
        def __init__(self, row_count):
            self.row_count = row_count
            self.closed = False

        def cursor(self):
            return Cursor(self.row_count)

        def close(self):
            self.closed = True

    primary_counts = {"warehouse": 101, "staging": 202}
    flow_counts = {"flow_db": 303}
    calls = []

    def primary_connection(*, database=None):
        calls.append(("primary", database))
        return Connection(primary_counts[database])

    def flow_connection(*, database=None):
        calls.append(("flow", database))
        return Connection(flow_counts[database])

    monkeypatch.setattr(prober, "_get_pg_connection", primary_connection)
    monkeypatch.setattr(prober, "_get_flow_pg_connection", flow_connection)

    identities = (
        (1, "primary.internal", "warehouse"),
        (2, "primary.internal", "staging"),
        (3, "flow.internal", "flow_db"),
        (4, "other.internal", "other_db"),
    )
    with get_db() as db:
        for source_id, server, database_name in identities:
            _source(db, source_id, f"sales.orders [{database_name}]")
            result = upsert_postgres_identity(
                db,
                source_id=source_id,
                server=server,
                database=database_name,
                schema="sales",
                relation="orders",
                relation_kind="table",
                verified_at=NOW,
            )
            assert result["status"] in {"claimed", "refreshed"}

    result = prober.run_probe()

    assert result["probed"] == 4
    assert set(calls) == {
        ("primary", "warehouse"),
        ("primary", "staging"),
        ("flow", "flow_db"),
    }
    with get_db() as db:
        probes = {
            int(row["source_id"]): (row["status"], row["row_count"], row["message"])
            for row in db.execute(
                """SELECT source_id, status, row_count, message
                     FROM source_probes ORDER BY source_id, id"""
            ).fetchall()
        }
    assert probes[1][1] == 101
    assert probes[2][1] == 202
    assert probes[3][1] == 303
    assert probes[4][0] == "unknown"
    assert probes[4][1] is None
    assert "not configured" in probes[4][2]


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
        lambda db, database, **kwargs: reconciliations.append(database) or {},
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
        db.execute("INSERT INTO flow_sites(id, name) VALUES (9011, 'Portal')")
        db.execute(
            """INSERT INTO flow_reports(id, site_id, name, report_url)
               VALUES (9011, 9011, 'Orders', 'https://example.test/orders')"""
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder,
                    filename_template, sql_handoff_enabled, sql_database,
                    sql_schema, sql_table)
               VALUES (9011, 'Orders', 9011, 9011, '.', 'orders.csv', 1,
                       'warehouse', 'sales', 'orders')"""
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
    def complete_catalog_verification(scan_run_id=None, **_kwargs):
        from app.scanner.report_source_identities import (
            complete_report_postgres_identity_reconciliation,
            finalize_report_postgres_identity_relinks,
            pending_report_postgres_identity_target_source_ids,
            reconcile_all_report_postgres_identities,
        )

        reconciliation = reconcile_all_report_postgres_identities(defer_relinks=True)
        for target in reconciliation.get("catalog_targets") or []:
            source_ids = pending_report_postgres_identity_target_source_ids(
                reconciliation,
                server=target["server"],
                database=target["database"],
            )
            finalize_report_postgres_identity_relinks(
                reconciliation,
                server=target["server"],
                database=target["database"],
                verified_source_ids=source_ids,
            )
        complete_report_postgres_identity_reconciliation(reconciliation)
        return {"status": "completed"}

    monkeypatch.setattr(pg_deps, "scan_pg_dependencies", complete_catalog_verification)
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


def _assert_unresolved_report_table(report_name: str):
    with get_db() as db:
        row = db.execute(
            """SELECT rt.source_id, rt.source_expression,
                      rt.source_resolution_status, rt.source_resolution_reason
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                WHERE r.name=?""",
            (report_name,),
        ).fetchone()
    assert row is not None
    assert row["source_id"] is None
    assert row["source_resolution_status"] == "unresolved"
    assert row["source_resolution_reason"]
    return row


def test_legacy_unknown_placeholder_retirement_uses_scanner_fingerprint(identity_db):
    log_lines = []
    with get_db() as db:
        db.execute(
            """INSERT INTO sources
                   (id, name, type, connection_info, discovered_by, archived)
               VALUES (1, 'Unknown Source', 'unknown', '', 'scan', 0)"""
        )
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Legacy report')")
        db.execute(
            """INSERT INTO report_tables(report_id, table_name, source_id)
               VALUES (1, 'Model', 1)"""
        )
        retired = runner._retire_legacy_unknown_sources(db, NOW, log_lines)

        source = db.execute(
            "SELECT name, archived FROM sources WHERE id=1"
        ).fetchone()
        linked_source_id = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()["source_id"]

    assert retired == 1
    assert source["archived"] == 1
    assert source["name"].startswith("Archived unresolved source 1")
    assert linked_source_id is None
    assert log_lines


def test_legitimate_manual_unknown_source_name_is_never_retired(identity_db):
    with get_db() as db:
        db.execute(
            """INSERT INTO sources
                   (id, name, type, connection_info, discovered_by, archived)
               VALUES (1, 'Unknown Source', 'unknown', '', 'manual', 0)"""
        )
        retired = runner._retire_legacy_unknown_sources(db, NOW, [])
        source = db.execute(
            "SELECT name, archived FROM sources WHERE id=1"
        ).fetchone()

    assert retired == 0
    assert tuple(source) == ("Unknown Source", 0)


def test_empty_governed_table_snapshot_fails_without_unlinking_catalog(
    identity_db,
    monkeypatch,
):
    incomplete_report = DiscoveredReport(
        name="Existing report",
        tmdl_path="C:/Existing report",
        tables=[
            ParsedTable(table_name="Business Owner", is_metadata=True),
            ParsedTable(table_name="LocalDateTable_deadbeef"),
        ],
    )
    _stub_runner_followups(monkeypatch, [incomplete_report])

    with get_db() as db:
        _source(db, 1, "sales.orders")
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Existing report')")
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Orders', 1, 'prior complete expression')"""
        )

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "failed"
    assert result["components"]["core"]["status"] == "failed"
    with get_db() as db:
        retained = db.execute(
            """SELECT table_name, source_id, source_expression
                 FROM report_tables WHERE report_id=1"""
        ).fetchall()
        source = db.execute("SELECT archived FROM sources WHERE id=1").fetchone()

    assert [tuple(row) for row in retained] == [
        ("Orders", 1, "prior complete expression")
    ]
    assert source["archived"] == 0


def test_full_scan_reparses_quoted_native_query_and_remaps_generic_report_source(
    identity_db, monkeypatch
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""bi_reporting"".""inflow_outflow_mv""") in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "bi_reporting.inflow_outflow_mv"
    report = DiscoveredReport(
        name="Inflow outflow",
        tmdl_path="C:/Inflow outflow",
        tables=[
            ParsedTable(
                table_name="Model",
                m_expression=expression,
                source=parsed,
            )
        ],
    )
    _stub_runner_followups(monkeypatch, [report])
    monkeypatch.setattr(runner, "PGHOST", "db.internal")
    monkeypatch.setattr(runner, "PGDATABASE", "warehouse")

    with get_db() as db:
        # This represents the pre-fix parse: the report was attached to a
        # generic PostgreSQL connection with no physical relation identity.
        _source(db, 1, "unresolved_pg_query_legacy")
        db.execute(
            "INSERT INTO reports(id, name, archived) VALUES (1, 'Inflow outflow', 0)"
        )
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 1, 'legacy truncated query')"""
        )

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        mapped = db.execute(
            """SELECT rt.source_id, rt.source_expression,
                      spi.server_name, spi.database_name,
                      spi.schema_name, spi.relation_name
                 FROM report_tables rt
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE rt.report_id=1 AND rt.table_name='Model'"""
        ).fetchone()
        old_source = db.execute("SELECT id FROM sources WHERE id=1").fetchone()

    assert int(mapped["source_id"]) != 1
    assert mapped["source_expression"] == expression
    assert (
        mapped["server_name"],
        mapped["database_name"],
        mapped["schema_name"],
        mapped["relation_name"],
    ) == (
        "db.internal",
        "warehouse",
        "bi_reporting",
        "inflow_outflow_mv",
    )
    # A full scan safely remaps this report table; it does not need to delete
    # the old generic source to repair current lineage.
    assert old_source is not None


def test_full_scan_never_assumes_public_for_unqualified_native_postgres_sql(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM inflow_outflow_mv") in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "inflow_outflow_mv"
    assert parsed.postgres_relation_exact is False
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Search path report",
        tmdl_path="C:/Search path report",
        tables=[
            ParsedTable(
                table_name="Model",
                m_expression=expression,
                source=parsed,
            )
        ],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    row = _assert_unresolved_report_table("Search path report")
    assert row["source_expression"] == expression


def test_full_scan_never_assumes_public_for_unqualified_postgres_navigation(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Source{[Name="orders", Kind="Table"]}[Data] in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "orders"
    assert parsed.sql_query is None
    assert parsed.postgres_relation_exact is False
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Bare navigation report",
        tmdl_path="C:/Bare navigation report",
        tables=[
            ParsedTable(
                table_name="Model",
                m_expression=expression,
                source=parsed,
            )
        ],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Bare navigation report")


def test_full_scan_never_chooses_one_of_multiple_native_postgres_queries(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Value.NativeQuery(Source, "SELECT * FROM finance.orders"), '
        'Rows = if UseSales then A else B in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_single_native_query is False
    assert parsed.postgres_identity_is_exact is False
    assert parsed.sql_table is None
    report = DiscoveredReport(
        name="Branched native report",
        tmdl_path="C:/Branched native report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Branched native report")


def test_full_scan_never_uses_navigation_fallback_for_dynamic_connector_query(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse", '
        '[Query=SqlParameter]), '
        'Fallback = Source{[Schema="sales", Item="orders"]}[Data] '
        'in if UseFallback then Fallback else Source'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_query is None
    assert parsed.sql_table is None
    assert parsed.postgres_native_query_exact is False
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Dynamic connector query report",
        tmdl_path="C:/Dynamic connector query report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Dynamic connector query report")


def test_full_scan_reads_query_after_another_connector_option(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse", '
        '[CreateNavigationProperties=false, Query="SELECT * FROM sales.orders"]), '
        'Fallback = Source{[Schema="fallback", Item="wrong"]}[Data] '
        'in Source'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_query == "SELECT * FROM sales.orders"
    assert parsed.sql_table == "sales.orders"
    assert parsed.postgres_identity_is_exact is True
    report = DiscoveredReport(
        name="Multi-option query report",
        tmdl_path="C:/Multi-option query report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT spi.schema_name, spi.relation_name
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE r.name='Multi-option query report'"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_full_scan_ignores_commented_navigation_target(identity_db, monkeypatch):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"),\n'
        '// old: Source{[Schema="wrong", Item="orders"]}[Data]\n'
        'Real = Source{[Schema="sales", Item="orders"]}[Data]\n'
        'in Real'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "sales.orders"
    assert parsed.postgres_identity_is_exact is True
    report = DiscoveredReport(
        name="Commented navigation report",
        tmdl_path="C:/Commented navigation report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT spi.schema_name, spi.relation_name
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE r.name='Commented navigation report'"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_full_scan_leaves_conditional_navigation_targets_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Source{[Schema="sales", Item="orders"]}[Data], '
        'B = Source{[Schema="sales", Item="customers"]}[Data] '
        'in if UseA then A else B'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Conditional navigation report",
        tmdl_path="C:/Conditional navigation report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Conditional navigation report")


def test_full_scan_leaves_dynamic_navigation_branch_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Source{[Schema="sales", Item="orders"]}[Data], '
        'B = Source{[Schema=SchemaParam, Item=TableParam]}[Data] '
        'in if UseA then A else B'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Dynamic navigation branch report",
        tmdl_path="C:/Dynamic navigation branch report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Dynamic navigation branch report")


def test_full_scan_leaves_dynamic_navigation_key_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Source{[Schema="sales", Item="orders"]}[Data], '
        'B = Source{NavigationKey}[Data], '
        'Choice = if UseA then A else B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Dynamic navigation key report",
        tmdl_path="C:/Dynamic navigation key report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Dynamic navigation key report")


def test_full_scan_ignores_commented_connector_when_selecting_postgres(
    identity_db,
    monkeypatch,
):
    expression = (
        'let\n// old: Sql.Database("legacy", "old")\n'
        'Source = PostgreSQL.Database("db.internal", "warehouse"),\n'
        'Real = Source{[Schema="sales", Item="orders"]}[Data]\n'
        'in Real'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.source_type == "postgresql"
    assert parsed.postgres_identity_is_exact is True
    report = DiscoveredReport(
        name="Commented connector report",
        tmdl_path="C:/Commented connector report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT spi.server_name, spi.database_name,
                      spi.schema_name, spi.relation_name
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE r.name='Commented connector report'"""
        ).fetchone()
    assert tuple(identity) == ("db.internal", "warehouse", "sales", "orders")


def test_full_scan_never_treats_commented_plain_sql_as_live(identity_db, monkeypatch):
    expression = (
        'let\nSource = PostgreSQL.Database("db.internal", "warehouse"),\n'
        '// removed native query: SELECT * FROM wrong.orders\n'
        'Result = Table.FirstN(Source, 10)\n'
        'in Result'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_query is None
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Commented SQL report",
        tmdl_path="C:/Commented SQL report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Commented SQL report")


def test_full_scan_native_postgres_sql_wins_over_navigation_step(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Nav = Source{[Schema="finance", Item="orders"]}[Data], '
        'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.orders") in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_query == "SELECT * FROM sales.orders"
    assert parsed.sql_table == "sales.orders"
    assert parsed.postgres_identity_is_exact is True
    report = DiscoveredReport(
        name="Mixed navigation report",
        tmdl_path="C:/Mixed navigation report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT spi.schema_name, spi.relation_name
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE r.name='Mixed navigation report'"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_full_scan_leaves_conditional_native_and_navigation_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data] '
        'in if UseNative then A else B'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Mixed query mechanism report",
        tmdl_path="C:/Mixed query mechanism report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Mixed query mechanism report")


def test_full_scan_leaves_assigned_conditional_query_mechanisms_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data], '
        'Choice = if UseNative then A else B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    report = DiscoveredReport(
        name="Assigned mixed mechanism report",
        tmdl_path="C:/Assigned mixed mechanism report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Assigned mixed mechanism report")


def test_full_scan_leaves_try_fallback_query_mechanisms_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data], '
        'Choice = try A otherwise B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    report = DiscoveredReport(
        name="Try fallback mechanism report",
        tmdl_path="C:/Try fallback mechanism report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Try fallback mechanism report")


def test_full_scan_leaves_try_catch_query_mechanisms_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data], '
        'Choice = try A catch ()=>B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    report = DiscoveredReport(
        name="Try catch mechanism report",
        tmdl_path="C:/Try catch mechanism report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Try catch mechanism report")


def test_full_scan_ignores_query_decoys_outside_connector_options(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Note = "Query = ""SELECT * FROM finance.orders""", '
        '// Query = "SELECT * FROM finance.orders"\n'
        'Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Source{[Schema="sales", Item="orders"]}[Data] in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_query is None
    assert parsed.sql_table == "sales.orders"
    report = DiscoveredReport(
        name="Comment decoy report",
        tmdl_path="C:/Comment decoy report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT spi.schema_name, spi.relation_name
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE r.name='Comment decoy report'"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


@pytest.mark.parametrize("resolve_parameters", [False, True])
def test_full_scan_never_persists_unresolved_postgres_parameters_as_identity(
    identity_db,
    monkeypatch,
    resolve_parameters,
):
    expression = (
        "let Source = PostgreSQL.Database(ServerParameter, DatabaseParameter), "
        'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.orders") in Rows'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Parameterized report",
        tmdl_path="C:/Parameterized report",
        expressions=(
            {
                "ServerParameter": "db.internal",
                "DatabaseParameter": "warehouse",
            }
            if resolve_parameters
            else {}
        ),
        tables=[
            ParsedTable(
                table_name="Orders",
                m_expression=expression,
                source=parsed,
            )
        ],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    if resolve_parameters:
        with get_db() as db:
            row = db.execute(
                """SELECT s.name, s.connection_info, spi.server_name,
                          spi.database_name, spi.schema_name, spi.relation_name
                     FROM reports r
                     JOIN report_tables rt ON rt.report_id=r.id
                     JOIN sources s ON s.id=rt.source_id
                     LEFT JOIN source_postgres_identities spi ON spi.source_id=s.id
                    WHERE r.name='Parameterized report'"""
            ).fetchone()
        assert parsed.postgres_identity_is_exact is True
        assert tuple(row) == (
            "sales.orders",
            "db.internal/warehouse/sales.orders",
            "db.internal",
            "warehouse",
            "sales",
            "orders",
        )
    else:
        _assert_unresolved_report_table("Parameterized report")


@pytest.mark.parametrize(
    ("expression", "parameters"),
    [
        (
            "let Source = if UseProd then "
            'PostgreSQL.Database("prod.internal", "warehouse") else '
            'PostgreSQL.Database("dev.internal", "warehouse"), '
            'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.orders") in Rows',
            {},
        ),
        (
            "let Source = if UseProd then "
            "PostgreSQL.Database(ServerParameter, DatabaseParameter) else "
            'PostgreSQL.Database("dev.internal", "warehouse"), '
            'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.orders") in Rows',
            {
                "ServerParameter": "prod.internal",
                "DatabaseParameter": "warehouse",
            },
        ),
    ],
)
def test_full_scan_never_chooses_one_branch_of_conditional_postgres_connection(
    identity_db,
    monkeypatch,
    expression,
    parameters,
):
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Conditional endpoint report",
        tmdl_path="C:/Conditional endpoint report",
        expressions=parameters,
        tables=[
            ParsedTable(
                table_name="Orders",
                m_expression=expression,
                source=parsed,
            )
        ],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Conditional endpoint report")


def test_runner_legacy_cleanup_preserves_identified_database_sources(
    identity_db, monkeypatch
):
    _stub_runner_followups(monkeypatch)
    monkeypatch.setattr(runner, "PGHOST", "db.internal")
    monkeypatch.setattr(runner, "PGDATABASE", "warehouse")

    expected_names = {
        1: "sales.orders",
        3: "sales.customers",
        4: "sales.quoted_orders",
        5: "sales.products",
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
        # A legacy unidentified row remains separate from canonical identities.
        _source(db, 2, "Legacy sales.orders")
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
    expression = (
        f'let Source = PostgreSQL.Database("db.internal", "{database_name}"), '
        'Rows = Source{[Schema="sales", Item="orders"]}[Data] in Rows'
    )
    source = SourceInfo(
        source_type="postgresql",
        server="db.internal",
        database=database_name,
        sql_table="sales.orders",
        raw_expression=expression,
    )
    return DiscoveredReport(
        name=name,
        tmdl_path=f"C:/{name}",
        tables=[
            ParsedTable(
                table_name="Orders",
                m_expression=expression,
                source=source,
            )
        ],
    )


def test_full_scan_metadata_refresh_preserves_mv_kind_when_catalog_fails(
    identity_db,
    monkeypatch,
):
    reports = [_pg_report("MV report", "warehouse")]
    _stub_runner_followups(monkeypatch, reports)
    monkeypatch.setattr(
        pg_deps,
        "scan_pg_dependencies",
        lambda scan_run_id=None, **_kwargs: {
            "status": "failed",
            "databases": {
                "warehouse": {"status": "failed", "stage": "fetch"},
            },
        },
    )
    with get_db() as db:
        _source(db, 1, "sales.orders")
        _identity(
            db,
            1,
            database_name="warehouse",
            relation="orders",
            kind="materialized_view",
        )
        _source(db, 2, "sales.raw_orders")
        _identity(db, 2, database_name="warehouse", relation="raw_orders")
        db.execute(
            """INSERT INTO source_dependencies
                   (source_id, depends_on_id, discovered_by, created_at)
               VALUES (1, 2, 'pg_matviews', ?)""",
            (NOW,),
        )

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["components"]["postgres_dependencies"]["status"] == "failed"
    with get_db() as db:
        kind = db.execute(
            """SELECT relation_kind FROM source_postgres_identities
                WHERE source_id=1"""
        ).fetchone()[0]
        edge = db.execute(
            """SELECT source_id, depends_on_id FROM source_dependencies
                WHERE source_id=1 AND discovered_by='pg_matviews'"""
        ).fetchone()
    assert kind == "materialized_view"
    assert tuple(edge) == (1, 2)


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


@pytest.mark.parametrize(
    "expression",
    [
        (
            'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
            'Pg = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
            'Local = #table({"id"}, {{1}}), '
            'Choice = if UsePg then Pg else Local in Choice'
        ),
        (
            'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
            'Pg = Source{[Schema="sales", Item="orders"]}[Data], '
            'Local = #table({"id"}, {{1}}), '
            'Choice = try Pg otherwise Local in Choice'
        ),
    ],
)
def test_full_scan_leaves_postgres_or_local_output_unresolved(
    identity_db,
    monkeypatch,
    expression,
):
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Postgres or local report",
        tmdl_path="C:/Postgres or local report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Postgres or local report")


def test_full_scan_keeps_lineage_for_conditional_row_transform(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Orders = Source{[Schema="sales", Item="orders"]}[Data], '
        'Added = Table.AddColumn(Orders, "Flag", '
        'each if [amount] > 0 then "positive" else "other") in Added'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "sales.orders"
    assert parsed.postgres_conditional_output_exact is True
    assert parsed.postgres_identity_is_exact is True
    report = DiscoveredReport(
        name="Conditional column report",
        tmdl_path="C:/Conditional column report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT spi.schema_name, spi.relation_name
                 FROM reports r
                 JOIN report_tables rt ON rt.report_id=r.id
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE r.name='Conditional column report'"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_full_scan_leaves_wrapped_postgres_or_local_conditional_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Pg = Source{[Schema="sales", Item="orders"]}[Data], '
        'Local = #table({"id"}, {{1}}), '
        'Choice = Table.Buffer(if UsePg then Pg else Local) in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Wrapped conditional report",
        tmdl_path="C:/Wrapped conditional report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Wrapped conditional report")


def test_full_scan_leaves_invoked_table_lambda_conditional_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Pg = Source{[Schema="sales", Item="orders"]}[Data], '
        'Local = #table({"id"}, {{1}}), '
        'Choice = Function.Invoke((x) => if x then Pg else Local, {UsePg}) '
        'in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False
    report = DiscoveredReport(
        name="Invoked table lambda report",
        tmdl_path="C:/Invoked table lambda report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Invoked table lambda report")


def test_full_scan_leaves_conditional_table_input_unresolved(
    identity_db,
    monkeypatch,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Pg = Source{[Schema="sales", Item="orders"]}[Data], '
        'Local = #table({"id"}, {{1}}), '
        'Choice = Table.AddColumn('
        '(() => if UsePg then Pg else Local)(), '
        '"Flag", each 1) in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    report = DiscoveredReport(
        name="Conditional table input report",
        tmdl_path="C:/Conditional table input report",
        tables=[ParsedTable(table_name="Model", m_expression=expression, source=parsed)],
    )
    _stub_runner_followups(monkeypatch, [report])

    result = runner.run_scan("unused", run_followup_probe=False)

    assert result["status"] == "completed"
    _assert_unresolved_report_table("Conditional table input report")
