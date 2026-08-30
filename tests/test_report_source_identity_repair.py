from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import app.database as database
from app.database import get_db
from app.scanner import report_source_identities as repair
from app.scanner.tmdl_parser import _parse_m_expression
from app.source_identity import upsert_postgres_identity


@pytest.fixture
def repair_db(monkeypatch):
    with TemporaryDirectory(prefix="metronome-report-identity-") as folder:
        path = str(Path(folder) / "repair.db")
        monkeypatch.setattr(database, "DB_PATH", path)
        monkeypatch.setattr(repair, "UPLOAD_PGHOST", "db.internal")
        monkeypatch.setattr(repair, "UPLOAD_PGPORT", "5432")
        database.init_db()
        yield path


def _expression(
    relation: str,
    *,
    server: str = "db.internal",
    database_name: str = "warehouse",
    schema: str = "bi_reporting",
) -> str:
    return (
        f'let Source = PostgreSQL.Database("{server}", "{database_name}"), '
        f'Rows = Value.NativeQuery(Source, '
        f'"SELECT * FROM ""{schema}"".""{relation}""") in Rows'
    )


def _report(db, report_id: int, name: str) -> None:
    db.execute("INSERT INTO reports(id, name) VALUES (?, ?)", (report_id, name))


def _source(db, source_id: int, *, source_type: str = "postgresql", query: str | None = None) -> None:
    db.execute(
        """INSERT INTO sources(id, name, type, source_query)
           VALUES (?, ?, ?, ?)""",
        (source_id, f"source_{source_id}", source_type, query),
    )


def _report_table(
    db,
    report_id: int,
    table_name: str,
    expression: str,
    source_id: int | None,
) -> int:
    cursor = db.execute(
        """INSERT INTO report_tables
               (report_id, table_name, source_id, source_expression)
           VALUES (?, ?, ?, ?)""",
        (report_id, table_name, source_id, expression),
    )
    return int(cursor.lastrowid)


def _identity_for_report_table(db, report_id: int, table_name: str):
    return db.execute(
        """SELECT rt.source_id, spi.server_name, spi.database_name,
                  spi.schema_name, spi.relation_name
             FROM report_tables rt
             LEFT JOIN source_postgres_identities spi ON spi.source_id=rt.source_id
            WHERE rt.report_id=? AND rt.table_name=?""",
        (report_id, table_name),
    ).fetchone()


def test_left_join_repairs_null_missing_and_unknown_sources_without_raw_m(repair_db):
    secret_marker = "RAW_M_MUST_NOT_ESCAPE"
    unknown_expression = _expression("unknown_target") + f" // {secret_marker}"
    null_expression = _expression("null_target")
    missing_expression = _expression("missing_target")
    with get_db() as db:
        _report(db, 1, "Repair all rows")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Unknown", unknown_expression, 10)
        _report_table(db, 1, "Null", null_expression, None)

    # Simulate a legacy broken FK that SQLite may contain from an older build.
    raw = sqlite3.connect(repair_db)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute(
            """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
               VALUES (1, 'Missing', 9999, ?)""",
            (missing_expression,),
        )
        raw.commit()
    finally:
        raw.close()

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    assert result["rows_examined"] == 3
    assert result["parsed"] == 3
    assert result["claimed"] == 1
    assert result["created"] == 2
    assert result["relinked"] == 2
    assert result["catalog_targets"] == [
        {"server": "db.internal", "database": "warehouse"}
    ]
    assert secret_marker not in json.dumps(result)
    with get_db() as db:
        identities = {
            name: _identity_for_report_table(db, 1, name)
            for name in ("Unknown", "Null", "Missing")
        }
        claimed_source = db.execute(
            """SELECT type, connection_info, source_query
                 FROM sources WHERE id=10"""
        ).fetchone()
        probe_eligible = db.execute(
            """SELECT COUNT(*) FROM sources
                WHERE id=10 AND type='postgresql' AND COALESCE(archived, 0)=0"""
        ).fetchone()[0]
    assert {row["relation_name"] for row in identities.values()} == {
        "unknown_target",
        "null_target",
        "missing_target",
    }
    assert all(row["source_id"] is not None for row in identities.values())
    assert claimed_source["type"] == "postgresql"
    assert claimed_source["connection_info"] == (
        "db.internal/warehouse/bi_reporting.unknown_target"
    )
    assert claimed_source["source_query"] == unknown_expression
    assert probe_eligible == 1


def test_null_calculated_table_is_skipped_without_false_postgres_warning(repair_db):
    with get_db() as db:
        _report(db, 1, "Calculated table")
        _report_table(
            db,
            1,
            "Calendar",
            "Calendar = CALENDAR(DATE(2025, 1, 1), DATE(2026, 12, 31))",
            None,
        )

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    assert result["skipped"] == 1
    assert result["unresolved"] == 0
    assert result["issues"] == []


@pytest.mark.parametrize(
    "connection",
    [
        "PostgreSQL.Database(ServerParameter, DatabaseParameter)",
        'PostgreSQL.Database("db.internal", DatabaseParameter)',
        'PostgreSQL.Database(ServerParameter, "warehouse")',
        'PostgreSQL.Database("db.internal" & Suffix, "warehouse")',
    ],
)
def test_parameterized_postgres_connection_is_never_claimed(repair_db, connection):
    expression = (
        f"let Source = {connection}, "
        'Rows = Value.NativeQuery(Source, "SELECT * FROM bi_reporting.target") in Rows'
    )
    with get_db() as db:
        _report(db, 1, "Parameterized")
        _source(db, 10, source_type="unknown")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["parsed"] == 0
    assert result["unresolved"] == 1
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_postgres_connection",
        }
    ]
    assert expression not in json.dumps(result)
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


@pytest.mark.parametrize(
    "native_sql",
    [
        "SELECT * FROM inflow_outflow_mv",
        'SELECT * FROM ""inflow_outflow_mv""',
    ],
)
def test_unqualified_native_postgres_relation_is_never_assumed_public(
    repair_db,
    native_sql,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        f'Rows = Value.NativeQuery(Source, "{native_sql}") in Rows'
    )
    with get_db() as db:
        _report(db, 1, "Search path report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["parsed"] == 0
    assert result["unresolved"] == 1
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "unqualified_native_postgres_relation",
        }
    ]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_unqualified_postgres_navigation_is_never_assumed_public(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Source{[Name="orders", Kind="Table"]}[Data] in Rows'
    )
    with get_db() as db:
        _report(db, 1, "Bare navigation report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["parsed"] == 0
    assert result["unresolved"] == 1
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "unqualified_postgres_navigation_relation",
        }
    ]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_multiple_native_postgres_queries_are_never_arbitrarily_chosen(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Value.NativeQuery(Source, "SELECT * FROM finance.orders"), '
        'Rows = if UseSales then A else B in Rows'
    )
    with get_db() as db:
        _report(db, 1, "Branched native report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["parsed"] == 0
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "multiple_native_postgres_queries",
        }
    ]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_dynamic_connector_query_is_never_replaced_by_navigation_fallback(repair_db):
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
    with get_db() as db:
        _report(db, 1, "Dynamic query report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["parsed"] == 0
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_native_postgres_query",
        }
    ]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_connector_query_after_another_option_still_controls_exact_identity(repair_db):
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
    with get_db() as db:
        _report(db, 1, "Multi-option query report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT schema_name, relation_name
                 FROM source_postgres_identities WHERE source_id=10"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_commented_navigation_cannot_override_the_live_relation(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"),\n'
        '// old: Source{[Schema="wrong", Item="orders"]}[Data]\n'
        'Real = Source{[Schema="sales", Item="orders"]}[Data]\n'
        'in Real'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "sales.orders"
    assert parsed.postgres_identity_is_exact is True
    with get_db() as db:
        _report(db, 1, "Commented navigation report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT schema_name, relation_name
                 FROM source_postgres_identities WHERE source_id=10"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_conditional_distinct_navigation_targets_are_left_unresolved(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Source{[Schema="sales", Item="orders"]}[Data], '
        'B = Source{[Schema="sales", Item="customers"]}[Data] '
        'in if UseA then A else B'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    with get_db() as db:
        _report(db, 1, "Conditional navigation report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "unresolved_postgres_relation",
        }
    ]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_literal_and_dynamic_navigation_branches_are_left_unresolved(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Source{[Schema="sales", Item="orders"]}[Data], '
        'B = Source{[Schema=SchemaParam, Item=TableParam]}[Data] '
        'in if UseA then A else B'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    with get_db() as db:
        _report(db, 1, "Dynamic navigation branch report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "unresolved_postgres_relation",
        }
    ]


def test_dynamic_navigation_key_branch_is_left_unresolved(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Source{[Schema="sales", Item="orders"]}[Data], '
        'B = Source{NavigationKey}[Data], '
        'Choice = if UseA then A else B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table is None
    assert parsed.postgres_identity_is_exact is False
    with get_db() as db:
        _report(db, 1, "Dynamic navigation key report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "unresolved_postgres_relation",
        }
    ]


def test_commented_non_postgres_connector_cannot_hide_live_postgres(repair_db):
    expression = (
        'let\n// old: Sql.Database("legacy", "old")\n'
        'Source = PostgreSQL.Database("db.internal", "warehouse"),\n'
        'Real = Source{[Schema="sales", Item="orders"]}[Data]\n'
        'in Real'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.source_type == "postgresql"
    assert parsed.postgres_identity_is_exact is True
    with get_db() as db:
        _report(db, 1, "Commented connector report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT server_name, database_name, schema_name, relation_name
                 FROM source_postgres_identities WHERE source_id=10"""
        ).fetchone()
    assert tuple(identity) == ("db.internal", "warehouse", "sales", "orders")


def test_commented_plain_sql_cannot_become_a_postgres_identity(repair_db):
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
    with get_db() as db:
        _report(db, 1, "Commented SQL report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "unresolved_postgres_relation",
        }
    ]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_native_postgres_sql_wins_over_unrelated_navigation_step(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Nav = Source{[Schema="finance", Item="orders"]}[Data], '
        'Rows = Value.NativeQuery(Source, "SELECT * FROM sales.orders") in Rows'
    )
    with get_db() as db:
        _report(db, 1, "Mixed navigation report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    assert result["claimed"] == 1
    with get_db() as db:
        identity = db.execute(
            """SELECT schema_name, relation_name
                 FROM source_postgres_identities WHERE source_id=10"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_conditional_native_query_and_navigation_are_left_unresolved(repair_db):
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
    with get_db() as db:
        _report(db, 1, "Mixed query mechanism report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_native_postgres_query",
        }
    ]


def test_assigned_conditional_native_and_navigation_are_left_unresolved(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data], '
        'Choice = if UseNative then A else B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    with get_db() as db:
        _report(db, 1, "Assigned mixed mechanism report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_native_postgres_query",
        }
    ]


def test_try_otherwise_native_and_navigation_are_left_unresolved(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data], '
        'Choice = try A otherwise B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    with get_db() as db:
        _report(db, 1, "Try fallback mechanism report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_native_postgres_query",
        }
    ]


def test_try_catch_native_and_navigation_are_left_unresolved(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'A = Value.NativeQuery(Source, "SELECT * FROM sales.orders"), '
        'B = Source{[Schema="finance", Item="orders"]}[Data], '
        'Choice = try A catch ()=>B in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_native_query_exact is False
    assert parsed.sql_table is None
    with get_db() as db:
        _report(db, 1, "Try catch mechanism report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_native_postgres_query",
        }
    ]


def test_query_text_in_comments_or_strings_cannot_override_navigation(repair_db):
    expression = (
        'let Note = "Query = ""SELECT * FROM finance.orders""", '
        '// Query = "SELECT * FROM finance.orders"\n'
        'Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Source{[Schema="sales", Item="orders"]}[Data] in Rows'
    )
    with get_db() as db:
        _report(db, 1, "Comment decoy report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT schema_name, relation_name
                 FROM source_postgres_identities WHERE source_id=10"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


@pytest.mark.parametrize("server", ["db.internal:abc", "db.internal:99999"])
def test_invalid_literal_postgres_endpoint_is_never_claimed(repair_db, server):
    expression = _expression("target", server=server)
    with get_db() as db:
        _report(db, 1, "Invalid endpoint")
        _source(db, 10, source_type="unknown")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["parsed"] == 0
    assert result["unresolved"] == 1
    assert result["catalog_targets"] == []
    assert result["issues"] == [{
        "report_table_id": row_id,
        "source_id": 10,
        "reason_code": "invalid_postgres_endpoint",
    }]
    with get_db() as db:
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_unidentified_shared_source_is_claimed_only_when_every_report_agrees(repair_db):
    expression = _expression("shared_target")
    with get_db() as db:
        _report(db, 1, "Selected")
        _report(db, 2, "Other")
        _source(db, 10, source_type="unknown", query=expression)
        _report_table(db, 1, "SelectedModel", expression, 10)
        _report_table(db, 2, "OtherModel", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["claimed"] == 1
    assert result["created"] == 0
    assert result["relinked"] == 0
    with get_db() as db:
        links = db.execute(
            "SELECT report_id, source_id FROM report_tables ORDER BY report_id"
        ).fetchall()
        identity = db.execute(
            """SELECT spi.relation_name, s.type, s.connection_info
                 FROM source_postgres_identities spi
                 JOIN sources s ON s.id=spi.source_id
                WHERE spi.source_id=10"""
        ).fetchone()
    assert [tuple(row) for row in links] == [(1, 10), (2, 10)]
    assert identity["relation_name"] == "shared_target"
    assert identity["type"] == "postgresql"
    assert identity["connection_info"] == (
        "db.internal/warehouse/bi_reporting.shared_target"
    )


def test_incompatible_concrete_source_is_copied_not_retyped(repair_db):
    expression = _expression("orders")
    with get_db() as db:
        _report(db, 1, "Mislabeled source")
        _source(db, 10, source_type="csv")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["claimed"] == 0
    assert result["created"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        old_type = db.execute("SELECT type FROM sources WHERE id=10").fetchone()[0]
        repaired = db.execute(
            """SELECT s.id, s.type, spi.relation_name
                 FROM report_tables rt
                 JOIN sources s ON s.id=rt.source_id
                 JOIN source_postgres_identities spi ON spi.source_id=s.id
                WHERE rt.report_id=1 AND rt.table_name='Model'"""
        ).fetchone()
    assert old_type == "csv"
    assert repaired["id"] != 10
    assert repaired["type"] == "postgresql"
    assert repaired["relation_name"] == "orders"


def test_shared_source_with_different_report_target_is_copied_not_claimed(repair_db):
    selected_expression = _expression("selected_target")
    other_expression = _expression("other_target")
    with get_db() as db:
        _report(db, 1, "Selected")
        _report(db, 2, "Other")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "SelectedModel", selected_expression, 10)
        _report_table(db, 2, "OtherModel", other_expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["claimed"] == 0
    assert result["created"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        selected = _identity_for_report_table(db, 1, "SelectedModel")
        other = _identity_for_report_table(db, 2, "OtherModel")
        old_identity = db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone()
    assert selected["source_id"] != 10
    assert selected["relation_name"] == "selected_target"
    assert other["source_id"] == 10
    assert other["relation_name"] is None
    assert old_identity is None


def test_shared_source_missing_per_report_expression_is_never_claimed_in_place(
    repair_db,
):
    selected_expression = _expression("selected_target")
    with get_db() as db:
        _report(db, 1, "Selected")
        _report(db, 2, "Legacy other")
        _source(db, 10, source_type="unknown", query=selected_expression)
        _report_table(db, 1, "SelectedModel", selected_expression, 10)
        _report_table(db, 2, "LegacyModel", "", 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["claimed"] == 0
    assert result["created"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        selected = _identity_for_report_table(db, 1, "SelectedModel")
        legacy = _identity_for_report_table(db, 2, "LegacyModel")
        old_identity = db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone()

    assert selected["source_id"] != 10
    assert selected["relation_name"] == "selected_target"
    assert legacy["source_id"] == 10
    assert legacy["relation_name"] is None
    assert old_identity is None


def test_selected_shared_row_never_uses_another_rows_representative_expression(
    repair_db,
):
    representative = _expression("representative_target")
    other_expression = _expression("other_target")
    with get_db() as db:
        _report(db, 1, "Selected legacy row")
        _report(db, 2, "Other explicit row")
        _source(db, 10, source_type="unknown", query=representative)
        selected_row = _report_table(db, 1, "SelectedModel", "", 10)
        _report_table(db, 2, "OtherModel", other_expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["claimed"] == 0
    assert result["created"] == 0
    assert result["relinked"] == 0
    assert result["issues"] == [{
        "report_table_id": selected_row,
        "source_id": 10,
        "reason_code": "missing_report_source_expression",
    }]
    with get_db() as db:
        assert db.execute(
            "SELECT source_id FROM report_tables WHERE id=?",
            (selected_row,),
        ).fetchone()[0] == 10
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_incompatible_flow_reference_prevents_in_place_claim(repair_db):
    expression = _expression("report_target")
    with get_db() as db:
        _report(db, 1, "Selected")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)
        db.execute(
            """INSERT INTO flow_sites(id, name) VALUES (901, 'Repair test site')"""
        )
        db.execute(
            """INSERT INTO flow_reports(id, site_id, name, report_url)
               VALUES (901, 901, 'Repair test export', 'https://example.test')"""
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_database, sql_schema, sql_table,
                    sql_target_source_id)
               VALUES (901, 'Repair test writer', 901, 901, 'C:\\Exports', 'x.csv', 1,
                       'warehouse', 'bi_reporting', 'different_target', 10)"""
        )

    result = repair.reconcile_report_postgres_identities(1)

    assert result["claimed"] == 0
    assert result["created"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        report_source = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()[0]
        flow_source = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=901"
        ).fetchone()[0]
        old_identity = db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone()
    assert report_source != 10
    assert flow_source == 10
    assert old_identity is None


def test_flow_reference_on_different_port_prevents_in_place_claim(
    repair_db,
    monkeypatch,
):
    expression = _expression("report_target")
    monkeypatch.setattr(repair, "UPLOAD_PGPORT", "6543")
    with get_db() as db:
        _report(db, 1, "Selected")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)
        db.execute(
            "INSERT INTO flow_sites(id, name) VALUES (902, 'Port-aware site')"
        )
        db.execute(
            """INSERT INTO flow_reports(id, site_id, name, report_url)
               VALUES (902, 902, 'Port-aware export', 'https://example.test')"""
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_database, sql_schema, sql_table,
                    sql_target_source_id)
               VALUES (902, 'Port-aware writer', 902, 902, 'C:\\Exports', 'x.csv', 1,
                       'warehouse', 'bi_reporting', 'report_target', 10)"""
        )

    result = repair.reconcile_report_postgres_identities(1)

    assert result["claimed"] == 0
    assert result["created"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        assert db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()[0] != 10
        assert db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=902"
        ).fetchone()[0] == 10
        assert db.execute(
            "SELECT 1 FROM source_postgres_identities WHERE source_id=10"
        ).fetchone() is None


def test_deferred_relinks_apply_only_for_matching_successful_catalog(repair_db):
    warehouse_expression = _expression("warehouse_target", database_name="warehouse")
    staging_expression = _expression("staging_target", database_name="staging")
    with get_db() as db:
        _report(db, 1, "Deferred")
        warehouse_row = _report_table(db, 1, "Warehouse", warehouse_expression, None)
        staging_row = _report_table(db, 1, "Staging", staging_expression, None)

    result = repair.reconcile_report_postgres_identities(1, defer_relinks=True)

    assert result["status"] == "pending"
    assert result["created"] == 2
    assert result["relinked"] == 0
    assert result["pending_relinks"] == 2
    assert result["catalog_targets"] == [
        {"server": "db.internal", "database": "warehouse"},
        {"server": "db.internal", "database": "staging"},
    ]
    assert "_pending_relinks" not in result
    with get_db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM report_tables WHERE source_id IS NOT NULL"
        ).fetchone()[0] == 0

    # A catalog on another coordinate cannot move either link.
    repair.finalize_report_postgres_identity_relinks(
        result,
        server="other.internal",
        database="warehouse",
    )
    assert result["pending_relinks"] == 2
    assert result["relinked"] == 0

    # Only the successfully committed warehouse catalog is finalized.
    repair.finalize_report_postgres_identity_relinks(
        result,
        server="db.internal",
        database="warehouse",
    )
    assert result["pending_relinks"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        rows = {
            int(row["id"]): row["source_id"]
            for row in db.execute(
                "SELECT id, source_id FROM report_tables WHERE report_id=1"
            ).fetchall()
        }
    assert rows[warehouse_row] is not None
    assert rows[staging_row] is None

    # Completing after the staging catalog failed abandons that move safely.
    repair.complete_report_postgres_identity_reconciliation(result)
    assert result["status"] == "completed_with_warnings"
    assert result["pending_relinks"] == 0
    assert result["not_applied"] == 1
    assert result["issues"][-1] == {
        "report_table_id": staging_row,
        "source_id": None,
        "reason_code": "catalog_not_completed",
    }
    with get_db() as db:
        assert db.execute(
            "SELECT source_id FROM report_tables WHERE id=?", (staging_row,)
        ).fetchone()[0] is None


def test_default_direct_call_relinks_immediately(repair_db):
    expression = _expression("immediate_target")
    with get_db() as db:
        _report(db, 1, "Immediate")
        row_id = _report_table(db, 1, "Model", expression, None)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["deferred"] is False
    assert result["status"] == "completed"
    assert result["created"] == 1
    assert result["relinked"] == 1
    assert result["pending_relinks"] == 0
    with get_db() as db:
        assert db.execute(
            "SELECT source_id FROM report_tables WHERE id=?", (row_id,)
        ).fetchone()[0] is not None


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
def test_postgres_or_local_output_is_never_claimed_exact(repair_db, expression):
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False
    with get_db() as db:
        _report(db, 1, "Postgres or local report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["catalog_targets"] == []
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "conditional_postgres_output",
        }
    ]


def test_conditional_row_transform_keeps_exact_postgres_lineage(repair_db):
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
    with get_db() as db:
        _report(db, 1, "Conditional column report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    with get_db() as db:
        identity = db.execute(
            """SELECT schema_name, relation_name
                 FROM source_postgres_identities WHERE source_id=10"""
        ).fetchone()
    assert tuple(identity) == ("sales", "orders")


def test_conditional_scalar_used_inside_lambda_keeps_exact_lineage(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Orders = Source{[Schema="sales", Item="orders"]}[Data], '
        'Flag = if UseLongLabel then "positive" else "other", '
        'Added = Table.AddColumn(Orders, "Flag", each Flag) in Added'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.sql_table == "sales.orders"
    assert parsed.postgres_conditional_output_exact is True
    assert parsed.postgres_identity_is_exact is True

    with get_db() as db:
        _report(db, 1, "Conditional scalar report")
        _source(db, 10, source_type="unknown")
        _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed"
    assert result["claimed"] == 1


def test_wrapped_postgres_or_local_conditional_is_never_claimed_exact(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Pg = Source{[Schema="sales", Item="orders"]}[Data], '
        'Local = #table({"id"}, {{1}}), '
        'Choice = Table.Buffer(if UsePg then Pg else Local) in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False
    with get_db() as db:
        _report(db, 1, "Wrapped conditional report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "conditional_postgres_output",
        }
    ]


@pytest.mark.parametrize(
    "choice",
    [
        '(() => if UsePg then Pg else Local)()',
        'Function.Invoke((x) => if x then Pg else Local, {UsePg})',
        'Table.Combine(List.Transform({UsePg}, each if _ then Pg else Local))',
    ],
)
def test_table_producing_lambda_conditional_is_never_claimed_exact(
    repair_db,
    choice,
):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Pg = Source{[Schema="sales", Item="orders"]}[Data], '
        'Local = #table({"id"}, {{1}}), '
        f'Choice = {choice} in Choice'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False

    with get_db() as db:
        _report(db, 1, "Table lambda report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "conditional_postgres_output",
        }
    ]


def test_row_callback_with_native_source_branch_is_never_claimed_exact(repair_db):
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Orders = Source{[Schema="sales", Item="orders"]}[Data], '
        'Filtered = Table.SelectRows(Orders, each if UseLookup then '
        'Table.RowCount(Value.NativeQuery(Source, '
        '"SELECT * FROM finance.lookup")) > 0 else true) in Filtered'
    )
    parsed = _parse_m_expression(expression)
    assert parsed.postgres_conditional_output_exact is False
    assert parsed.postgres_identity_is_exact is False

    with get_db() as db:
        _report(db, 1, "Conditional callback source report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "nonliteral_native_postgres_query",
        }
    ]


def test_table_lambda_in_safe_operator_input_is_never_masked(repair_db):
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
    assert parsed.postgres_identity_is_exact is False

    with get_db() as db:
        _report(db, 1, "Conditional table input report")
        _source(db, 10, source_type="postgresql")
        row_id = _report_table(db, 1, "Model", expression, 10)

    result = repair.reconcile_report_postgres_identities(1)

    assert result["status"] == "completed_with_warnings"
    assert result["issues"] == [
        {
            "report_table_id": row_id,
            "source_id": 10,
            "reason_code": "conditional_postgres_output",
        }
    ]
