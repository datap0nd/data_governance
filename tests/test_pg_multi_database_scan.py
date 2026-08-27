from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.database as database
from app.database import get_db, init_db
from app.scanner import pg_deps, prober
from app.source_identity import upsert_postgres_identity


OLD_VERIFIED_AT = "2025-01-02T03:04:05+00:00"


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
    assert calls[1] == ("session", {"readonly": True, "autocommit": True})


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
    assert mismatches == set()


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


def test_flow_only_database_with_server_mismatch_is_not_scanned(
    scan_db, monkeypatch
):
    monkeypatch.setattr(pg_deps, "PGHOST", "catalog.internal")
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", "upload.internal")
    monkeypatch.setattr(pg_deps, "PGDATABASE", "")
    calls = []

    def connect(*, database):  # pragma: no cover - assertion is that it is unused
        calls.append(database)
        raise AssertionError("server-mismatched Flow database must not be scanned")

    monkeypatch.setattr(pg_deps, "_get_pg_connection", connect)
    with get_db() as db:
        _flow(db, "flow_db")

    result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "failed"
    assert result["flow_server_mismatch_databases"] == ["flow_db"]
    assert result["databases"]["flow_db"]["stage"] == "configuration"
    assert result["databases"]["flow_db"]["reason_code"] == "server_mismatch"
    assert calls == []
