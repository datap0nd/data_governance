from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.database as database
from app.database import get_db, init_db
from app.query_history import MATERIALIZED_VIEW_KIND, mv_artifact_key, observe_query
from app.scanner import pg_deps, prober
from app.source_identity import upsert_postgres_identity


OLD_VERIFIED_AT = "2025-01-02T03:04:05+00:00"


def test_dependency_sql_rejects_cross_catalog_oid_collisions():
    """A pg_proc/pg_type OID must never masquerade as a pg_class relation."""
    normalized = " ".join(pg_deps._DEPENDENCY_SQL.split())
    assert "d.classid = 'pg_rewrite'::regclass" in normalized
    assert "d.refclassid = 'pg_class'::regclass" in normalized


@pytest.fixture()
def scan_db(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "governance.db")
        monkeypatch.setattr(database, "DB_PATH", db_path)
        init_db()
        yield db_path


def _source(
    db,
    source_id: int,
    name: str,
    *,
    discovered_by: str = "manual",
) -> None:
    db.execute(
        """INSERT INTO sources
               (id, name, type, connection_info, discovered_by, archived,
                created_at, updated_at)
           VALUES (?, ?, 'postgresql', ?, ?, 0, ?, ?)""",
        (source_id, name, name, discovered_by, OLD_VERIFIED_AT, OLD_VERIFIED_AT),
    )


def _identity(
    db,
    source_id: int,
    database_name: str,
    relation: str,
    *,
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
        verified_at=OLD_VERIFIED_AT,
    )
    assert result["status"] in {"claimed", "refreshed"}


def _edge(db, source_id: int, depends_on_id: int) -> None:
    db.execute(
        """INSERT INTO source_dependencies
               (source_id, depends_on_id, discovered_by, created_at)
           VALUES (?, ?, 'pg_matviews', ?)""",
        (source_id, depends_on_id, OLD_VERIFIED_AT),
    )


def _flow(db, database_name: str) -> None:
    db.execute("INSERT INTO flow_sites(id, name) VALUES (991, 'Test Portal')")
    db.execute(
        """INSERT INTO flow_reports(id, site_id, name, report_url)
           VALUES (991, 991, 'Test Orders', 'https://example.test/orders')"""
    )
    db.execute(
        """INSERT INTO flows
               (id, name, site_id, report_id, target_folder, filename_template,
                sql_handoff_enabled, sql_database, sql_schema, sql_table)
           VALUES (991, 'Test Orders Flow', 991, 991, '.', 'orders.csv', 1, ?,
                   'sales', 'orders')""",
        (database_name,),
    )


class _CatalogCursor:
    def __init__(
        self,
        dependency_rows=(),
        definition_rows=(),
        *,
        dependency_error: Exception | None = None,
        definition_error: Exception | None = None,
    ):
        self._dependency_rows = tuple(dependency_rows)
        self._definition_rows = tuple(definition_rows)
        self._dependency_error = dependency_error
        self._definition_error = definition_error
        self._rows = ()

    def execute(self, sql):
        if "FROM pg_depend" in sql:
            if self._dependency_error:
                raise self._dependency_error
            self._rows = self._dependency_rows
        elif "FROM pg_matviews" in sql:
            if self._definition_error:
                raise self._definition_error
            self._rows = self._definition_rows
        else:  # pragma: no cover - protects the catalog contract
            raise AssertionError(f"Unexpected PostgreSQL query: {sql}")

    def fetchall(self):
        return list(self._rows)


class _CatalogConnection:
    def __init__(self, *args, **kwargs):
        self._cursor = _CatalogCursor(*args, **kwargs)
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _configure_hosts(monkeypatch, *, database_name: str) -> None:
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", database_name)


def test_postgres_connection_helper_selects_requested_database(monkeypatch):
    calls = []

    class Connection:
        def set_session(self, **kwargs):
            calls.append(("session", kwargs))

    connection = Connection()

    def connect(**kwargs):
        calls.append(("connect", kwargs))
        return connection

    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(connect=connect))
    monkeypatch.setattr(prober, "PGHOST", "db.internal")
    monkeypatch.setattr(prober, "PGUSER", "reader")
    monkeypatch.setattr(prober, "PGPASSWORD", "secret")
    monkeypatch.setattr(prober, "PGDATABASE", "configured_db")

    result = prober._get_pg_connection(database="requested_db")

    assert result is connection
    assert calls[0][0] == "connect"
    assert calls[0][1]["database"] == "requested_db"
    assert "statement_timeout=" in calls[0][1]["options"]
    assert "lock_timeout=30000" in calls[0][1]["options"]
    assert calls[1] == ("session", {"readonly": True, "autocommit": True})


def test_flow_catalog_connection_is_forced_read_only(monkeypatch):
    calls = []

    class Connection:
        def set_session(self, **kwargs):
            calls.append(("session", kwargs))

    connection = Connection()

    def connect(**kwargs):
        calls.append(("connect", kwargs))
        return connection

    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(connect=connect))
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5433)
    monkeypatch.setattr(pg_deps, "UPLOAD_PGUSER", "flow-writer")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPASSWORD", "secret")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGDATABASE", "configured_flow_db")

    result = pg_deps._flow_catalog_connection("requested_flow_db")

    assert result is connection
    assert calls[0][0] == "connect"
    assert calls[0][1]["host"] == "upload.internal"
    assert calls[0][1]["port"] == 5433
    assert calls[0][1]["database"] == "requested_flow_db"
    assert "statement_timeout=" in calls[0][1]["options"]
    assert "lock_timeout=30000" in calls[0][1]["options"]
    assert calls[1] == ("session", {"readonly": True, "autocommit": True})


def test_flow_catalog_connection_supports_os_auth_and_closes_failed_setup(
    monkeypatch,
):
    class Connection:
        closed = False

        def set_session(self, **kwargs):
            raise RuntimeError("read-only setup failed")

        def close(self):
            self.closed = True

    connection = Connection()
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        return connection

    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(connect=connect))
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGUSER", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPASSWORD", "")

    assert pg_deps._flow_catalog_connection("warehouse") is None
    assert connection.closed is True
    assert "user" not in calls[0]
    assert "password" not in calls[0]


def test_required_databases_use_config_active_identities_and_sql_flows(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="configured_db")
    with get_db() as db:
        _source(db, 1, "Active table")
        _identity(db, 1, "active_db", "orders")
        _source(db, 2, "Inactive scan table", discovered_by="pg_deps")
        _identity(db, 2, "inactive_db", "orders")
        _flow(db, "flow_db")

    databases, origins, mismatches = pg_deps._required_databases()

    assert databases == ["active_db", "configured_db", "flow_db"]
    assert origins == {
        "active_db": ["identity"],
        "configured_db": ["configured"],
        "flow_db": ["flow"],
    }
    assert mismatches == []


def test_full_scan_discovers_active_report_database_on_flow_endpoint(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    flow_calls = []
    monkeypatch.setattr(
        pg_deps,
        "_flow_catalog_connection",
        lambda database: flow_calls.append(database) or _CatalogConnection(),
    )
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("the primary endpoint must not be used")
        ),
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Flow-host report')")
        _source(db, 1, "Report root")
        result = upsert_postgres_identity(
            db,
            source_id=1,
            server="upload.internal",
            database="report_db",
            schema="sales",
            relation="report_mv",
            relation_kind="materialized_view",
        )
        assert result["status"] in {"claimed", "refreshed"}
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 1)"
        )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["required_databases"] == ["report_db"]
    assert result["database_origins"] == {"report_db": ["flow"]}
    assert result["flow_target_catalog_databases"] == ["report_db"]
    assert flow_calls == ["report_db"]
    assert result["databases"]["report_db"]["catalog_server"] == "upload.internal"


def test_full_scan_warns_for_active_report_on_unconfigured_endpoint(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Other-host report')")
        _source(db, 1, "Report root")
        result = upsert_postgres_identity(
            db,
            source_id=1,
            server="other.internal:5433",
            database="report_db",
            schema="sales",
            relation="report_mv",
            relation_kind="materialized_view",
        )
        assert result["status"] in {"claimed", "refreshed"}
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 1)"
        )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed_with_warnings"
    assert result["required_databases"] == []
    assert result["unconfigured_catalog_targets"] == [{
        "server": "other.internal:5433",
        "database": "report_db",
        "reason_code": "unconfigured_catalog_endpoint",
    }]
    # A global scan reports this at the top level; it must not mislabel the
    # report-scoped repair component, because no report repair was requested.
    assert result["report_identity_reconciliation"]["status"] == "not_requested"
    assert result["report_identity_reconciliation"]["issues"] == []


def test_focused_recheck_never_attributes_another_reports_endpoint(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "configured.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "configured.internal")
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(),
    )
    selected_expression = (
        'let Source = PostgreSQL.Database("configured.internal", "alpha"), '
        'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.report_mv") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Selected')")
        db.execute("INSERT INTO reports(id, name) VALUES (2, 'Other')")
        _source(db, 1, "Selected root")
        selected_identity = upsert_postgres_identity(
            db,
            source_id=1,
            server="configured.internal",
            database="alpha",
            schema="sales",
            relation="report_mv",
            relation_kind="materialized_view",
        )
        assert selected_identity["status"] in {"claimed", "refreshed"}
        _source(db, 2, "Other root")
        other_identity = upsert_postgres_identity(
            db,
            source_id=2,
            server="other.internal",
            database="beta",
            schema="sales",
            relation="other_mv",
            relation_kind="materialized_view",
        )
        assert other_identity["status"] in {"claimed", "refreshed"}
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 1, ?)""",
            (selected_expression,),
        )
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (2, 'Model', 2)"
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed_with_warnings"
    assert result["unconfigured_catalog_targets"] == [{
        "server": "other.internal",
        "database": "beta",
        "reason_code": "unconfigured_catalog_endpoint",
    }]
    repair = result["report_identity_reconciliation"]
    assert repair["status"] == "completed"
    assert repair["confirmed"] == 1
    assert repair["unconfigured_catalog_targets"] == 0
    assert repair["issues"] == []


def test_database_apply_failure_rolls_back_only_that_database(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse_a")
    connections = {
        "warehouse_a": _CatalogConnection(
            [("sales", "report_mv", "sales", "new_orders", "r")]
        ),
        "warehouse_b": _CatalogConnection(
            [("sales", "report_mv", "sales", "new_orders", "r")]
        ),
    }
    calls: list[str] = []

    def connect(*, database):
        calls.append(database)
        return connections[database]

    monkeypatch.setattr(pg_deps, "_get_pg_connection", connect)

    with get_db() as db:
        _source(db, 1, "A report MV")
        _identity(db, 1, "warehouse_a", "report_mv", kind="materialized_view")
        _source(db, 2, "A prior orders", discovered_by="pg_deps")
        _identity(db, 2, "warehouse_a", "prior_orders")
        _edge(db, 1, 2)
        _source(db, 3, "A orphan", discovered_by="pg_deps")
        _identity(db, 3, "warehouse_a", "orphan")

        _source(db, 10, "B report MV one")
        _identity(db, 10, "warehouse_b", "report_mv", kind="materialized_view")
        _source(db, 11, "B prior orders", discovered_by="pg_deps")
        _identity(db, 11, "warehouse_b", "prior_orders")
        _edge(db, 10, 11)
        _source(db, 12, "B report MV two")
        _identity(db, 12, "warehouse_b", "report_mv", kind="materialized_view")
        _source(db, 13, "B orphan", discovered_by="pg_deps")
        _identity(db, 13, "warehouse_b", "orphan")

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed_with_warnings"
    assert result["databases"]["warehouse_a"]["status"] == "completed"
    assert result["databases"]["warehouse_b"]["status"] == "failed"
    assert result["databases"]["warehouse_b"]["stage"] == "apply"
    assert calls == ["warehouse_a", "warehouse_b"]
    assert all(connection.closed for connection in connections.values())

    with get_db() as db:
        a_edges = db.execute(
            """SELECT spi.relation_name AS downstream, dep.relation_name AS upstream
               FROM source_dependencies sd
               JOIN source_postgres_identities spi ON spi.source_id=sd.source_id
               JOIN source_postgres_identities dep ON dep.source_id=sd.depends_on_id
               WHERE spi.database_name='warehouse_a'"""
        ).fetchall()
        b_edges = db.execute(
            """SELECT sd.source_id, sd.depends_on_id
               FROM source_dependencies sd
               JOIN source_postgres_identities spi ON spi.source_id=sd.source_id
               WHERE spi.database_name='warehouse_b'"""
        ).fetchall()
        a_verified = db.execute(
            """SELECT verified_at FROM source_postgres_identities
               WHERE source_id=1"""
        ).fetchone()[0]
        b_verified = db.execute(
            """SELECT verified_at FROM source_postgres_identities
               WHERE source_id=10"""
        ).fetchone()[0]
        surviving_ids = {
            int(row[0]) for row in db.execute("SELECT id FROM sources").fetchall()
        }

    assert [tuple(row) for row in a_edges] == [("report_mv", "new_orders")]
    assert [tuple(row) for row in b_edges] == [(10, 11)]
    assert a_verified != OLD_VERIFIED_AT
    assert b_verified == OLD_VERIFIED_AT
    assert 3 not in surviving_ids
    assert 13 in surviving_ids


def test_database_fetch_failure_retains_prior_lineage_and_redacts_error(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse_a")
    secret = "ultra-secret-password"
    monkeypatch.setattr(pg_deps, "PGPASSWORD", secret)
    a_connection = _CatalogConnection()

    def connect(*, database):
        if database == "warehouse_b":
            raise RuntimeError(
                f"postgresql://reader:{secret}@db.internal/warehouse_b "
                f"password={secret}"
            )
        return a_connection

    monkeypatch.setattr(pg_deps, "_get_pg_connection", connect)
    with get_db() as db:
        _source(db, 10, "B report MV")
        _identity(db, 10, "warehouse_b", "report_mv", kind="materialized_view")
        _source(db, 11, "B prior orders", discovered_by="pg_deps")
        _identity(db, 11, "warehouse_b", "prior_orders")
        _edge(db, 10, 11)

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed_with_warnings"
    assert result["databases"]["warehouse_b"]["stage"] == "fetch"
    assert secret not in json.dumps(result)
    with get_db() as db:
        edge = db.execute(
            """SELECT source_id, depends_on_id FROM source_dependencies
               WHERE source_id=10"""
        ).fetchone()
        verified_at = db.execute(
            """SELECT verified_at FROM source_postgres_identities
               WHERE source_id=10"""
        ).fetchone()[0]
    assert tuple(edge) == (10, 11)
    assert verified_at == OLD_VERIFIED_AT


def test_missing_credentials_for_active_database_fail_without_local_mutation(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="")
    monkeypatch.setattr(
        pg_deps, "_get_pg_connection", lambda *, database: None
    )
    with get_db() as db:
        _source(db, 10, "Required report MV")
        _identity(db, 10, "required_db", "report_mv", kind="materialized_view")
        _source(db, 11, "Required prior orders", discovered_by="pg_deps")
        _identity(db, 11, "required_db", "prior_orders")
        _edge(db, 10, 11)

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "failed"
    required_result = result["databases"]["required_db"]
    assert required_result["status"] == "failed"
    assert required_result["stage"] == "fetch"
    assert "connection unavailable" in required_result["error"]
    with get_db() as db:
        edge = db.execute(
            """SELECT source_id, depends_on_id FROM source_dependencies
               WHERE source_id=10"""
        ).fetchone()
        verified_at = db.execute(
            """SELECT verified_at FROM source_postgres_identities
               WHERE source_id=10"""
        ).fetchone()[0]
    assert tuple(edge) == (10, 11)
    assert verified_at == OLD_VERIFIED_AT


def test_failed_catalog_fetch_does_not_apply_deferred_report_relink(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: None,
    )
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""report_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Legacy report root")
        _identity(db, 10, "warehouse", "legacy_wrong_root")
        db.execute(
            "UPDATE sources SET source_query=? WHERE id=10",
            (expression,),
        )
        _source(db, 11, "Prior upstream", discovered_by="pg_deps")
        _identity(db, 11, "warehouse", "prior_upstream")
        _edge(db, 10, 11)
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "failed"
    repair = result["report_identity_reconciliation"]
    assert repair["status"] == "completed_with_warnings"
    assert repair["relinked"] == 0
    assert repair["not_applied"] == 1
    assert repair["pending_relinks"] == 0
    with get_db() as db:
        report_source_id = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()[0]
        retained_edge = db.execute(
            "SELECT source_id, depends_on_id FROM source_dependencies WHERE source_id=10"
        ).fetchone()

    assert int(report_source_id) == 10
    assert tuple(retained_edge) == (10, 11)


def test_post_commit_report_relink_failure_keeps_truthful_catalog_result(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""report_mv""") in Rows'
    )
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(
            [("sales", "report_mv", "sales", "new_orders", "r")]
        ),
    )
    monkeypatch.setattr(
        pg_deps,
        "finalize_report_postgres_identity_relinks",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("relink locked")),
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Legacy report root")
        _identity(db, 10, "warehouse", "legacy_wrong_root")
        db.execute("UPDATE sources SET source_query=? WHERE id=10", (expression,))
        _source(db, 20, "Exact report MV", discovered_by="pg_deps")
        _identity(db, 20, "warehouse", "report_mv", kind="materialized_view")
        _source(db, 21, "Prior upstream", discovered_by="pg_deps")
        _identity(db, 21, "warehouse", "prior_orders")
        _edge(db, 20, 21)
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed_with_warnings"
    database_result = result["databases"]["warehouse"]
    assert database_result["status"] == "completed_with_warnings"
    assert database_result["warning_stage"] == "report_relink"
    assert database_result["deps_created"] == 1
    assert "Catalog lineage was committed" in database_result["log"]
    assert result["report_identity_reconciliation"]["not_applied"] == 1
    with get_db() as db:
        report_source = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()[0]
        edge = db.execute(
            """SELECT parent.relation_name, child.relation_name
                 FROM source_dependencies sd
                 JOIN source_postgres_identities parent
                   ON parent.source_id=sd.source_id
                 JOIN source_postgres_identities child
                   ON child.source_id=sd.depends_on_id
                WHERE parent.source_id=20"""
        ).fetchone()

    assert int(report_source) == 10
    assert tuple(edge) == ("report_mv", "new_orders")
    json.loads(json.dumps(result))


def test_successful_catalog_protects_and_finalizes_orphan_deferred_target(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    connection = _CatalogConnection()
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: connection,
    )
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""report_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Legacy report root")
        _identity(db, 10, "warehouse", "legacy_wrong_root")
        db.execute("UPDATE sources SET source_query=? WHERE id=10", (expression,))
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

        # This exact target is deliberately unreferenced and has no dependency
        # edge. Normal endpoint cleanup would delete it before deferred
        # finalization unless the reconciliation protects it.
        _source(db, 20, "Exact deferred target", discovered_by="pg_deps")
        _identity(db, 20, "warehouse", "report_mv", kind="materialized_view")

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed"
    repair = result["report_identity_reconciliation"]
    assert repair["relinked"] == 1
    assert repair["pending_relinks"] == 0
    assert repair["not_applied"] == 0
    assert json.loads(json.dumps(result))["status"] == "completed"
    with get_db() as db:
        report_source_id = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()[0]
        target = db.execute(
            "SELECT id FROM sources WHERE id=20"
        ).fetchone()

    assert int(report_source_id) == 20
    assert int(target["id"]) == 20


def test_unresolved_report_repair_without_catalog_targets_is_a_warning(
    scan_db, monkeypatch
):
    monkeypatch.setattr(pg_deps, "PGHOST", "")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "")
    expression = (
        "let Source = PostgreSQL.Database(ServerParameter, DatabaseParameter), "
        'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.report_mv") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Parameterized source")
        db.execute("UPDATE sources SET source_query=? WHERE id=10", (expression,))
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed_with_warnings"
    assert result["required_databases"] == []
    repair = result["report_identity_reconciliation"]
    assert repair["status"] == "completed_with_warnings"
    assert repair["unresolved"] == 1
    assert repair["issues"][0]["reason_code"] == "nonliteral_postgres_connection"


@pytest.mark.parametrize("preidentified", [False, True])
def test_unconfigured_report_catalog_endpoint_is_never_a_false_success(
    scan_db, monkeypatch, preidentified
):
    monkeypatch.setattr(pg_deps, "PGHOST", "configured.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "")
    expression = (
        'let Source = PostgreSQL.Database("other.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.report_mv") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Report source")
        if preidentified:
            result = upsert_postgres_identity(
                db,
                source_id=10,
                server="other.internal",
                database="warehouse",
                schema="sales",
                relation="report_mv",
                relation_kind="materialized_view",
            )
            assert result["status"] in {"claimed", "refreshed"}
        db.execute("UPDATE sources SET source_query=? WHERE id=10", (expression,))
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed_with_warnings"
    assert result["required_databases"] == []
    assert result["unconfigured_catalog_targets"] == [{
        "server": "other.internal",
        "database": "warehouse",
        "reason_code": "unconfigured_catalog_endpoint",
    }]
    repair = result["report_identity_reconciliation"]
    assert repair["status"] == "completed_with_warnings"
    assert repair["unconfigured_catalog_targets"] == 1
    assert repair["confirmed"] == int(preidentified)
    assert repair["claimed"] == int(not preidentified)
    assert repair["issues"][-1] == {
        "reason_code": "unconfigured_catalog_endpoint",
        "server": "other.internal",
        "database": "warehouse",
    }


def test_definition_capture_failure_commits_lineage_as_database_warning(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    secret = "definition-secret"
    monkeypatch.setattr(pg_deps, "PGPASSWORD", secret)
    connection = _CatalogConnection(
        [("sales", "report_mv", "sales", "orders", "r")],
        definition_error=RuntimeError(f"password={secret}"),
    )
    monkeypatch.setattr(
        pg_deps, "_get_pg_connection", lambda *, database: connection
    )
    with get_db() as db:
        _source(db, 1, "Report MV")
        _identity(db, 1, "warehouse", "report_mv", kind="materialized_view")

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed_with_warnings"
    database_result = result["databases"]["warehouse"]
    assert database_result["status"] == "completed_with_warnings"
    assert database_result["definition_status"] == "skipped"
    assert result["definition_status"] == "skipped"
    assert database_result["deps_created"] == 1
    assert secret not in json.dumps(result)


def test_new_materialized_view_gets_exact_database_qualified_identity(
    scan_db, monkeypatch
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    connection = _CatalogConnection(
        [("sales", "report_mv", "sales", "orders", "r")]
    )
    monkeypatch.setattr(
        pg_deps, "_get_pg_connection", lambda *, database: connection
    )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["databases"]["warehouse"]["mvs_found"] == 1
    with get_db() as db:
        identities = db.execute(
            """SELECT spi.database_name, spi.schema_name, spi.relation_name,
                      spi.relation_kind, s.discovered_by
               FROM source_postgres_identities spi
               JOIN sources s ON s.id=spi.source_id
               WHERE spi.server_name=?
               ORDER BY spi.relation_name""",
            ("db.internal",),
        ).fetchall()
        edge = db.execute(
            """SELECT downstream.relation_name, upstream.relation_name
               FROM source_dependencies sd
               JOIN source_postgres_identities downstream
                 ON downstream.source_id=sd.source_id
               JOIN source_postgres_identities upstream
                 ON upstream.source_id=sd.depends_on_id"""
        ).fetchone()

    assert [tuple(row) for row in identities] == [
        ("warehouse", "sales", "orders", "table", "pg_deps"),
        ("warehouse", "sales", "report_mv", "materialized_view", "pg_deps"),
    ]
    assert tuple(edge) == ("report_mv", "orders")


def test_catalog_scan_keeps_mv_to_view_to_table_lineage(scan_db, monkeypatch):
    _configure_hosts(monkeypatch, database_name="warehouse")
    connection = _CatalogConnection(
        [
            ("sales", "inflow_outflow_mv", "m", "sales", "asap_stage", "v"),
            ("sales", "asap_stage", "v", "sales", "asap_import", "r"),
        ],
        [("sales", "inflow_outflow_mv", "SELECT * FROM sales.asap_stage")],
    )
    monkeypatch.setattr(
        pg_deps, "_get_pg_connection", lambda *, database: connection
    )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["mvs_found"] == 1
    assert result["deps_created"] == 2
    with get_db() as db:
        identities = {
            row["relation_name"]: (int(row["source_id"]), row["relation_kind"])
            for row in db.execute(
                """SELECT source_id, relation_name, relation_kind
                     FROM source_postgres_identities
                    WHERE database_name='warehouse' AND schema_name='sales'"""
            ).fetchall()
        }
        edges = {
            tuple(row)
            for row in db.execute(
                """SELECT downstream.relation_name, upstream.relation_name
                     FROM source_dependencies sd
                     JOIN source_postgres_identities downstream
                       ON downstream.source_id=sd.source_id
                     JOIN source_postgres_identities upstream
                       ON upstream.source_id=sd.depends_on_id"""
            ).fetchall()
        }

    assert identities["inflow_outflow_mv"][1] == "materialized_view"
    assert identities["asap_stage"][1] == "view"
    assert identities["asap_import"][1] == "table"
    assert edges == {
        ("inflow_outflow_mv", "asap_stage"),
        ("asap_stage", "asap_import"),
    }


def test_flow_only_database_uses_flow_target_catalog_read_only(
    scan_db, monkeypatch
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    read_only_calls = []
    flow_connection = _CatalogConnection(
        [("sales", "inflow_outflow_mv", "sales", "orders", "r")]
    )

    def connect_read_only(*, database):  # pragma: no cover - must remain unused
        read_only_calls.append(database)
        raise AssertionError("Flow-only database must use the Flow target server")

    monkeypatch.setattr(pg_deps, "_get_pg_connection", connect_read_only)
    monkeypatch.setattr(
        pg_deps,
        "_flow_catalog_connection",
        lambda database: flow_connection if database == "flow_db" else None,
    )
    with get_db() as db:
        _flow(db, "flow_db")

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["flow_server_mismatch_databases"] == []
    assert result["flow_target_catalog_databases"] == ["flow_db"]
    assert result["databases"]["flow_db"]["credential_profile"] == "flow_target"
    assert result["databases"]["flow_db"]["catalog_server"] == "upload.internal"
    assert read_only_calls == []
    assert flow_connection.closed is True
    with get_db() as db:
        identities = {
            row["relation_name"]: (row["server_name"], int(row["source_id"]))
            for row in db.execute(
                """SELECT source_id, server_name, relation_name
                     FROM source_postgres_identities
                    WHERE database_name='flow_db' AND schema_name='sales'"""
            ).fetchall()
        }
        target_source_id = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=991"
        ).fetchone()[0]

    assert identities["orders"][0] == "upload.internal"
    assert int(target_source_id) == identities["orders"][1]


def test_mixed_report_and_flow_database_scans_both_physical_servers(
    scan_db, monkeypatch
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    primary_connection = _CatalogConnection(
        [("sales", "inflow_outflow_mv", "sales", "primary_orders", "r")]
    )
    flow_connection = _CatalogConnection(
        [("sales", "flow_stage", "sales", "orders", "r")]
    )
    primary_calls = []
    flow_calls = []

    def connect_primary(*, database):
        primary_calls.append(database)
        return primary_connection

    def connect_flow(database):
        flow_calls.append(database)
        return flow_connection

    monkeypatch.setattr(pg_deps, "_get_pg_connection", connect_primary)
    monkeypatch.setattr(pg_deps, "_flow_catalog_connection", connect_flow)
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "inflow_outflow_mv")
        upsert_postgres_identity(
            db,
            source_id=10,
            server="catalog.internal",
            database="warehouse",
            schema="sales",
            relation="inflow_outflow_mv",
            relation_kind="materialized_view",
        )
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 10)"
        )
        _flow(db, "warehouse")

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["flow_target_catalog_databases"] == ["warehouse"]
    assert set(result["databases"]) == {
        "warehouse",
        "warehouse [Flow target]",
    }
    assert result["databases"]["warehouse"]["catalog_server"] == "catalog.internal"
    assert (
        result["databases"]["warehouse [Flow target]"]["catalog_server"]
        == "upload.internal"
    )
    assert primary_calls == ["warehouse"]
    assert flow_calls == ["warehouse"]
    with get_db() as db:
        target = db.execute(
            """SELECT spi.server_name, spi.relation_name
                 FROM flows f
                 JOIN source_postgres_identities spi
                   ON spi.source_id=f.sql_target_source_id
                WHERE f.id=991"""
        ).fetchone()

    assert tuple(target) == ("upload.internal", "orders")


def test_same_hostname_different_ports_are_separate_catalog_targets(monkeypatch):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5433)

    targets = pg_deps._catalog_scan_targets(
        ["warehouse"],
        {"warehouse": ["configured", "flow"]},
    )

    assert [(target.server, target.credential_profile) for target in targets] == [
        ("db.internal", "read_only"),
        ("db.internal:5433", "flow_target"),
    ]


def test_catalog_result_keys_cannot_hide_endpoint_failure_on_legal_name_collision(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)
    monkeypatch.setattr(
        pg_deps,
        "_required_databases",
        lambda *args: (
            ["foo", "foo [Flow target]"],
            {
                "foo": ["configured", "flow"],
                "foo [Flow target]": ["configured"],
            },
            [],
        ),
    )
    catalog = pg_deps._DatabaseCatalog(dependency_rows=(), definitions={})

    def fetch(database, *, use_flow_credentials=False):
        if database == "foo" and use_flow_credentials:
            raise RuntimeError("flow endpoint unavailable")
        return catalog

    monkeypatch.setattr(pg_deps, "_fetch_database_catalog", fetch)
    monkeypatch.setattr(
        pg_deps,
        "_apply_database_catalog",
        lambda database, catalog, **kwargs: {
            "status": "completed",
            "mvs_found": 1,
            "deps_created": 0,
            "sources_created": 0,
            "changed_queries": 0,
            "definition_status": "completed",
            "log": "done",
            "query_change_log": "",
        },
    )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed_with_warnings"
    assert list(result["databases"]) == [
        "foo",
        "foo [Flow target]",
        "foo [Flow target] [2]",
    ]
    assert len(result["databases"]) == 3
    assert result["databases"]["foo [Flow target]"]["status"] == "failed"
    assert result["databases"]["foo [Flow target]"]["database"] == "foo"
    assert result["databases"]["foo [Flow target] [2]"]["status"] == "completed"
    assert (
        result["databases"]["foo [Flow target] [2]"]["database"]
        == "foo [Flow target]"
    )


def test_successful_focused_relink_drops_obsolete_endpoint_warning(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "configured.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "configured.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(
            [("sales", "inflow_outflow_mv", "sales", "asap_import", "r")]
        ),
    )
    expression = (
        'let Source = PostgreSQL.Database("configured.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""inflow_outflow_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Obsolete report root", discovered_by="scan")
        claim = upsert_postgres_identity(
            db,
            source_id=10,
            server="obsolete.internal:5433",
            database="legacy",
            schema="sales",
            relation="wrong_root",
            relation_kind="table",
        )
        assert claim["status"] in {"claimed", "refreshed"}
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed"
    assert result["unconfigured_catalog_targets"] == []
    assert result["report_identity_reconciliation"]["relinked"] == 1
    with get_db() as db:
        linked = db.execute(
            """SELECT spi.server_name, spi.database_name, spi.schema_name,
                      spi.relation_name
                 FROM report_tables rt
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE rt.report_id=1"""
        ).fetchone()
    assert tuple(linked) == (
        "configured.internal",
        "warehouse",
        "sales",
        "inflow_outflow_mv",
    )


def test_successful_relink_supersedes_failed_old_configured_database(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)

    def fetch(database, *, use_flow_credentials=False):
        if database == "legacy":
            raise RuntimeError("obsolete database is offline")
        assert database == "warehouse"
        return pg_deps._DatabaseCatalog(
            dependency_rows=(
                ("sales", "inflow_outflow_mv", "sales", "asap_import", "r"),
            ),
            definitions={},
        )

    monkeypatch.setattr(pg_deps, "_fetch_database_catalog", fetch)
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""inflow_outflow_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Legacy root", discovered_by="scan")
        claim = upsert_postgres_identity(
            db,
            source_id=10,
            server="db.internal",
            database="legacy",
            schema="sales",
            relation="wrong_root",
            relation_kind="table",
        )
        assert claim["status"] in {"claimed", "refreshed"}
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed"
    assert result["required_databases"] == ["warehouse"]
    assert result["database_origins"] == {
        "warehouse": ["configured", "identity"],
    }
    assert result["databases"]["legacy"]["status"] == "superseded"
    assert result["databases"]["legacy"]["attempt_status"] == "failed"
    assert result["databases"]["legacy"]["superseded_after_report_relink"] is True
    assert result["databases"]["warehouse"]["status"] == "completed"
    assert result["superseded_catalog_targets"] == [{
        "database": "legacy",
        "server": "db.internal",
        "credential_profile": "read_only",
        "result_key": "legacy",
        "attempt_status": "failed",
    }]
    assert "error" not in result


def test_unresolved_flow_target_downgrades_catalog_and_overall_status(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(
        pg_deps,
        "_flow_catalog_connection",
        lambda database: _CatalogConnection(),
    )
    with get_db() as db:
        _flow(db, "flow_db")

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed_with_warnings"
    database_result = result["databases"]["flow_db"]
    assert database_result["status"] == "completed_with_warnings"
    assert database_result["warning_stage"] == "flow_reconciliation"
    assert database_result["flow_reconciliation"] == {"unresolved": 1}
    assert database_result["flow_targets_needing_attention"] == 1
    assert "unresolved=1" in database_result["log"]
    with get_db() as db:
        target_source_id = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=991"
        ).fetchone()[0]
    assert target_source_id is None


def test_same_endpoint_flow_credentials_fallback_for_report_only_database(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "shared.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "shared.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)
    flow_connection = _CatalogConnection()
    primary_calls = []
    flow_calls = []

    def primary_connection(*, database):
        primary_calls.append(database)
        raise RuntimeError("primary catalog account unavailable")

    def flow_connection_for(database):
        flow_calls.append(database)
        return flow_connection

    monkeypatch.setattr(pg_deps, "_get_pg_connection", primary_connection)
    monkeypatch.setattr(pg_deps, "_flow_catalog_connection", flow_connection_for)
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Report only')")
        _source(db, 10, "Report root", discovered_by="scan")
        claim = upsert_postgres_identity(
            db,
            source_id=10,
            server="shared.internal",
            database="report_db",
            schema="sales",
            relation="report_mv",
            relation_kind="materialized_view",
        )
        assert claim["status"] in {"claimed", "refreshed"}
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 10)"
        )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert primary_calls == ["report_db"]
    assert flow_calls == ["report_db"]
    assert flow_connection.closed is True
    assert result["databases"]["report_db"]["credential_profile"] == "flow_target"


def test_full_scan_drops_unconfigured_warning_after_stale_edge_is_replaced(
    scan_db,
    monkeypatch,
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(
            [("sales", "report_mv", "sales", "current_orders", "r")]
        ),
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Current report')")
        _source(db, 10, "Report MV", discovered_by="scan")
        _identity(db, 10, "warehouse", "report_mv", kind="materialized_view")
        _source(db, 11, "Stale external dependency", discovered_by="pg_deps")
        claim = upsert_postgres_identity(
            db,
            source_id=11,
            server="obsolete.internal:5433",
            database="legacy",
            schema="sales",
            relation="old_orders",
            relation_kind="table",
        )
        assert claim["status"] in {"claimed", "refreshed"}
        _edge(db, 10, 11)
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 10)"
        )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["unconfigured_catalog_targets"] == []
    assert result["report_identity_reconciliation"]["status"] == "not_requested"
    with get_db() as db:
        dependencies = {
            row[0]
            for row in db.execute(
                """SELECT upstream.relation_name
                     FROM source_dependencies sd
                     JOIN source_postgres_identities upstream
                       ON upstream.source_id=sd.depends_on_id
                    WHERE sd.source_id=10"""
            ).fetchall()
        }
    assert dependencies == {"current_orders"}


def test_target_becoming_active_during_scan_requires_rerun(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    snapshots = [
        (["warehouse"], {"warehouse": ["configured"]}, []),
        (
            ["new_db", "warehouse"],
            {"new_db": ["identity"], "warehouse": ["configured"]},
            [],
        ),
    ]

    def required(*_args):
        assert snapshots
        return snapshots.pop(0)

    monkeypatch.setattr(pg_deps, "_required_databases", required)
    monkeypatch.setattr(
        pg_deps,
        "_fetch_database_catalog",
        lambda database, **kwargs: pg_deps._DatabaseCatalog((), {}),
    )

    result = pg_deps.scan_pg_dependencies()

    assert snapshots == []
    assert result["status"] == "completed_with_warnings"
    assert result["required_databases"] == ["new_db", "warehouse"]
    assert result["unattempted_catalog_targets"] == [{
        "database": "new_db",
        "server": "db.internal",
        "credential_profile": "read_only",
        "origins": ["identity"],
        "reason_code": "catalog_target_became_active_during_scan",
    }]
    assert "new_db" not in result["databases"]


def test_flow_enabled_during_catalog_read_gets_final_exact_reconciliation(
    scan_db,
    monkeypatch,
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    calls = 0

    def required(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["warehouse"], {"warehouse": ["configured"]}, []
        assert calls == 2
        with get_db() as db:
            _flow(db, "warehouse")
        return ["warehouse"], {"warehouse": ["configured", "flow"]}, []

    monkeypatch.setattr(pg_deps, "_required_databases", required)
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(
            [("sales", "report_mv", "sales", "orders", "r")]
        ),
    )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["unattempted_catalog_targets"] == []
    assert result["databases"]["warehouse"]["flow_reconciliation"] == {
        "confirmed": 1,
    }
    with get_db() as db:
        linked = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=991"
        ).fetchone()[0]
    assert linked is not None


def test_flow_disabled_during_scan_retires_obsolete_flow_warning(
    scan_db,
    monkeypatch,
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    calls = 0
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Report root')")
        _source(db, 10, "Report MV", discovered_by="scan")
        _identity(db, 10, "warehouse", "report_mv", kind="materialized_view")
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 10)"
        )
        _flow(db, "warehouse")

    def required(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["warehouse"], {"warehouse": ["configured", "flow"]}, []
        assert calls == 2
        with get_db() as db:
            db.execute("UPDATE flows SET sql_handoff_enabled=0 WHERE id=991")
        return ["warehouse"], {"warehouse": ["configured", "identity"]}, []

    monkeypatch.setattr(pg_deps, "_required_databases", required)
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(),
    )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    database_result = result["databases"]["warehouse"]
    assert database_result["flow_reconciliation"] == {}
    assert database_result["flow_targets_needing_attention"] == 0


def test_all_failed_targets_becoming_inactive_finishes_without_fake_error(
    scan_db,
    monkeypatch,
):
    _configure_hosts(monkeypatch, database_name="")
    calls = 0
    with get_db() as db:
        _flow(db, "flow_db")

    def required(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["flow_db"], {"flow_db": ["flow"]}, []
        assert calls == 2
        with get_db() as db:
            db.execute("UPDATE flows SET sql_handoff_enabled=0 WHERE id=991")
        return [], {}, []

    monkeypatch.setattr(pg_deps, "_required_databases", required)
    monkeypatch.setattr(
        pg_deps,
        "_fetch_database_catalog",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "not_requested"
    assert result["required_databases"] == []
    assert "error" not in result
    assert result["databases"]["flow_db"]["status"] == "superseded"
    assert result["databases"]["flow_db"]["attempt_status"] == "failed"


def test_zero_dependency_mv_and_foreign_table_are_catalogued_exactly(
    scan_db,
    monkeypatch,
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(
            [
                (
                    "sales",
                    "foreign_mv",
                    "m",
                    "external",
                    "foreign_orders",
                    "f",
                )
            ],
            [
                ("sales", "constant_mv", "SELECT 1"),
                ("sales", "foreign_mv", "SELECT * FROM external.foreign_orders"),
            ],
        ),
    )
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""constant_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Constant report')")
        _source(db, 10, "Legacy root", discovered_by="scan")
        _identity(db, 10, "warehouse", "wrong_root")
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed"
    assert result["mvs_found"] == 2
    assert result["deps_created"] == 1
    with get_db() as db:
        kinds = {
            row["relation_name"]: row["relation_kind"]
            for row in db.execute(
                """SELECT relation_name, relation_kind
                     FROM source_postgres_identities
                    WHERE database_name='warehouse'"""
            ).fetchall()
        }
        report_relation = db.execute(
            """SELECT spi.relation_name
                 FROM report_tables rt
                 JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
                WHERE rt.report_id=1"""
        ).fetchone()[0]
    assert kinds["constant_mv"] == "materialized_view"
    assert kinds["foreign_mv"] == "materialized_view"
    assert kinds["foreign_orders"] == "foreign_table"
    assert report_relation == "constant_mv"


def test_new_repaired_mv_records_baseline_before_next_change(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)
    definition = {"sql": "SELECT * FROM sales.asap_import"}

    def fetch(database, *, use_flow_credentials=False):
        if database == "legacy":
            return pg_deps._DatabaseCatalog(dependency_rows=(), definitions={})
        assert database == "warehouse"
        return pg_deps._DatabaseCatalog(
            dependency_rows=(
                ("sales", "inflow_outflow_mv", "sales", "asap_import", "r"),
            ),
            definitions={
                ("sales", "inflow_outflow_mv"): definition["sql"],
            },
        )

    monkeypatch.setattr(pg_deps, "_fetch_database_catalog", fetch)
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""inflow_outflow_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Legacy root", discovered_by="scan")
        _identity(db, 10, "legacy", "wrong_root")
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    first = pg_deps.scan_pg_dependencies(report_id=1)

    assert first["status"] == "completed"
    assert first["changed_queries"] == 0
    with get_db() as db:
        baseline = db.execute(
            """SELECT qv.id, qv.is_baseline, qv.action_id, spi.source_id
                 FROM source_postgres_identities spi
                 JOIN query_versions qv ON qv.source_id=spi.source_id
                WHERE spi.database_name='warehouse'
                  AND spi.schema_name='sales'
                  AND spi.relation_name='inflow_outflow_mv'"""
        ).fetchone()
    assert baseline is not None
    assert baseline["is_baseline"] == 1
    assert baseline["action_id"] is None

    definition["sql"] = "SELECT *, CURRENT_DATE AS loaded_on FROM sales.asap_import"
    second = pg_deps.scan_pg_dependencies(report_id=1)

    assert second["status"] == "completed"
    assert second["changed_queries"] == 1
    with get_db() as db:
        action = db.execute(
            """SELECT id, status FROM actions
                WHERE source_id=? AND type='changed_query'
                ORDER BY id DESC LIMIT 1""",
            (baseline["source_id"],),
        ).fetchone()
        versions = db.execute(
            """SELECT is_baseline, action_id FROM query_versions
                WHERE source_id=? ORDER BY id""",
            (baseline["source_id"],),
        ).fetchall()
    assert action["status"] == "open"
    assert len(versions) == 2
    assert versions[1]["is_baseline"] == 0
    assert versions[1]["action_id"] == action["id"]


def test_definition_failure_never_publishes_stale_staged_query_action(
    scan_db,
    monkeypatch,
):
    _configure_hosts(monkeypatch, database_name="warehouse")
    state = {
        "definition": "SELECT id FROM sales.asap_import",
        "error": None,
        "present": True,
    }

    def fetch(database, *, use_flow_credentials=False):
        assert database == "warehouse"
        return pg_deps._DatabaseCatalog(
            dependency_rows=(
                ("sales", "inflow_outflow_mv", "sales", "asap_import", "r"),
            ),
            definitions=(
                {}
                if state["error"] or not state["present"]
                else {("sales", "inflow_outflow_mv"): state["definition"]}
            ),
            definition_error=state["error"],
        )

    monkeypatch.setattr(pg_deps, "_fetch_database_catalog", fetch)
    with get_db() as db:
        _source(db, 10, "sales.inflow_outflow_mv", discovered_by="scan")
        _identity(
            db,
            10,
            "warehouse",
            "inflow_outflow_mv",
            kind="materialized_view",
        )
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        db.execute(
            """INSERT INTO report_tables(report_id, table_name, source_id)
               VALUES (1, 'Model', 10)"""
        )

    assert pg_deps.scan_pg_dependencies()["changed_queries"] == 0

    original_publish = pg_deps._publish_staged_changed_query_actions
    state["definition"] = "SELECT id, amount FROM sales.asap_import"
    monkeypatch.setattr(
        pg_deps,
        "_publish_staged_changed_query_actions",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("publication unavailable")),
    )
    interrupted = pg_deps.scan_pg_dependencies()
    assert interrupted["status"] == "completed_with_warnings"
    monkeypatch.setattr(
        pg_deps,
        "_publish_staged_changed_query_actions",
        original_publish,
    )
    with get_db() as db:
        staged_id = int(
            db.execute(
                """SELECT id FROM actions
                    WHERE source_id=10 AND type='changed_query'
                      AND status='resolved'
                      AND notes LIKE '%awaiting final catalog classification%'"""
            ).fetchone()["id"]
        )
        cursor = db.execute(
            """INSERT INTO actions
                   (source_id, type, status, notes, fingerprint)
               VALUES (10, 'changed_query', 'open', 'Competing prior alert',
                       'changed_query:mv:10:obsolete')"""
        )
        competing_id = int(cursor.lastrowid)

    # The live definition could have reverted while definition capture is
    # unavailable. Neither staged evidence nor competing state may be changed
    # based on the stale latest query_version.
    state["definition"] = "SELECT id FROM sales.asap_import"
    state["error"] = "pg_matviews unavailable"
    partial = pg_deps.scan_pg_dependencies()

    assert partial["status"] == "completed_with_warnings"
    assert partial["definition_status"] == "skipped"
    with get_db() as db:
        staged = db.execute(
            "SELECT status, notes FROM actions WHERE id=?",
            (staged_id,),
        ).fetchone()
        competing_status = db.execute(
            "SELECT status FROM actions WHERE id=?",
            (competing_id,),
        ).fetchone()["status"]
    assert staged["status"] == "resolved"
    assert "awaiting final catalog classification" in staged["notes"]
    assert competing_status == "open"

    # A successful endpoint read still cannot verify an MV omitted from the
    # captured pg_matviews rows (dropped, replaced, or no longer visible).
    state["error"] = None
    state["present"] = False
    absent = pg_deps.scan_pg_dependencies()

    assert absent["status"] == "completed"
    with get_db() as db:
        staged_status = db.execute(
            "SELECT status FROM actions WHERE id=?",
            (staged_id,),
        ).fetchone()["status"]
        competing_status = db.execute(
            "SELECT status FROM actions WHERE id=?",
            (competing_id,),
        ).fetchone()["status"]
    assert staged_status == "resolved"
    assert competing_status == "open"

    state["present"] = True
    state["error"] = None
    recovered = pg_deps.scan_pg_dependencies()

    assert recovered["status"] == "completed"
    assert recovered["changed_queries"] == 1
    with get_db() as db:
        statuses = db.execute(
            """SELECT id, status, notes FROM actions
                WHERE source_id=10 AND type='changed_query' ORDER BY id"""
        ).fetchall()
    assert [row["status"] for row in statuses].count("open") == 1
    assert next(row for row in statuses if row["id"] == staged_id)["status"] == "resolved"
    assert next(row for row in statuses if row["id"] == competing_id)["status"] == "resolved"


def test_superseded_inactive_mv_change_action_is_resolved_but_audited(
    scan_db,
    monkeypatch,
):
    monkeypatch.setattr(pg_deps, "PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", "warehouse")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "db.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)

    def fetch(database, *, use_flow_credentials=False):
        if database == "legacy":
            return pg_deps._DatabaseCatalog(
                dependency_rows=(),
                definitions={
                    ("sales", "wrong_root"): "SELECT 2 AS changed_value",
                },
            )
        assert database == "warehouse"
        return pg_deps._DatabaseCatalog(
            dependency_rows=(
                ("sales", "inflow_outflow_mv", "sales", "asap_import", "r"),
            ),
            definitions={},
        )

    monkeypatch.setattr(pg_deps, "_fetch_database_catalog", fetch)
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""sales"".""inflow_outflow_mv""") in Rows'
    )
    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow Outflow')")
        _source(db, 10, "Legacy MV", discovered_by="scan")
        _identity(db, 10, "legacy", "wrong_root", kind="materialized_view")
        observe_query(
            db,
            artifact_kind=MATERIALIZED_VIEW_KIND,
            artifact_key=mv_artifact_key(10),
            report_id=None,
            source_id=10,
            artifact_name="sales.wrong_root",
            language="sql",
            query_text="SELECT 1 AS old_value",
            scan_run_id=None,
            detected_at=OLD_VERIFIED_AT,
        )
        db.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Model', 10, ?)""",
            (expression,),
        )

    original_finalize = pg_deps.finalize_report_postgres_identity_relinks

    def finalize(reconciliation, *, server, database):
        if database == "legacy":
            with get_db() as db:
                staged = db.execute(
                    """SELECT status, notes FROM actions
                        WHERE source_id=10 AND type='changed_query'"""
                ).fetchone()
            # Email and AI workers may run in this exact interval. Evidence is
            # durable, but it must not be actionable before final relinking.
            assert staged["status"] == "resolved"
            assert "awaiting final catalog classification" in staged["notes"]
        return original_finalize(
            reconciliation,
            server=server,
            database=database,
        )

    monkeypatch.setattr(
        pg_deps,
        "finalize_report_postgres_identity_relinks",
        finalize,
    )

    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed"
    legacy = result["databases"]["legacy"]
    assert legacy["status"] == "superseded"
    assert legacy["inactive_changed_query_actions_resolved"] == 0
    assert legacy["staged_changed_query_actions_discarded"] == 1
    with get_db() as db:
        action = db.execute(
            """SELECT id, status, notes FROM actions
                WHERE source_id=10 AND type='changed_query'"""
        ).fetchone()
        linked_versions = db.execute(
            "SELECT COUNT(*) FROM query_versions WHERE source_id=10 AND action_id=?",
            (action["id"],),
        ).fetchone()[0]
    assert action["status"] == "resolved"
    assert "not published: catalog target no longer active" in action["notes"]
    assert linked_versions == 1
