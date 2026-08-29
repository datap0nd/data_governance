from __future__ import annotations

import pytest

import app.database as database
from app.database import get_db
from app.flow_diagnostics import build_flow_diagnostics, included_flow_ids
from app.routers import lineage, pipelines
from app.scanner import pg_deps
from app.scanner.report_source_identities import reconcile_report_postgres_identities
from app.scanner.tmdl_parser import (
    SourceInfo,
    _extract_table_navigation,
    _parse_m_expression,
    _validate_table_name,
)
from app.source_identity import split_relation, upsert_postgres_identity


@pytest.mark.parametrize(
    ("expression", "expected_sql", "expected_table", "expected_parts"),
    [
        (
            'let Source = PostgreSQL.Database("host", "warehouse"), '
            'Rows = Value.NativeQuery(Source, '
            '"SELECT * FROM schema_.""table_name""") in Rows',
            'SELECT * FROM schema_."table_name"',
            "schema_.table_name",
            ("schema_", "table_name"),
        ),
        (
            'let Source = PostgreSQL.Database("host", "warehouse"), '
            'Rows = Value.NativeQuery(Source, '
            '"SELECT * FROM ""Sales Data"".""Order Details""") in Rows',
            'SELECT * FROM "Sales Data"."Order Details"',
            '"Sales Data"."Order Details"',
            ("Sales Data", "Order Details"),
        ),
        (
            'let Source = PostgreSQL.Database("host", "warehouse"), '
            'Rows = Value.NativeQuery(Source, '
            '"SELECT * FROM ""odd"".""say""""hello""") in Rows',
            'SELECT * FROM "odd"."say""hello"',
            'odd."say""hello"',
            ("odd", 'say"hello'),
        ),
        (
            'let Rows = PostgreSQL.Database("host", "warehouse", '
            '[Query="SELECT *#(lf)FROM public.""ASAP_Import"""]) in Rows',
            'SELECT *\nFROM public."ASAP_Import"',
            "public.ASAP_Import",
            ("public", "ASAP_Import"),
        ),
    ],
)
def test_postgres_native_m_decodes_quoted_relations(
    expression, expected_sql, expected_table, expected_parts
):
    parsed = _parse_m_expression(expression)

    assert parsed.source_type == "postgresql"
    assert parsed.sql_query == expected_sql
    assert parsed.sql_table == expected_table
    assert _extract_table_navigation(expression) == expected_table
    assert split_relation(parsed.sql_table) == expected_parts


@pytest.mark.parametrize(
    ("expression", "expected_sql", "expected_table"),
    [
        (
            'let Source = Sql.Database("sqlhost", "warehouse"), '
            'Rows = Value.NativeQuery(Source, '
            '"SELECT * FROM [dbo].[Orders]") in Rows',
            "SELECT * FROM [dbo].[Orders]",
            "dbo.Orders",
        ),
        (
            'let Rows = Sql.Database("sqlhost", "warehouse", '
            '[Query="SELECT * FROM [Sales Data].[Order]] Details]"]) in Rows',
            "SELECT * FROM [Sales Data].[Order]] Details]",
            '"Sales Data"."Order] Details"',
        ),
        (
            'let Source = MySQL.Database("mysqlhost", "warehouse"), '
            'Rows = Value.NativeQuery(Source, '
            '"SELECT * FROM `sales``data`.`order``details`") in Rows',
            "SELECT * FROM `sales``data`.`order``details`",
            '"sales`data"."order`details"',
        ),
    ],
)
def test_native_sql_keeps_bracket_and_backtick_identifiers(
    expression, expected_sql, expected_table
):
    parsed = _parse_m_expression(expression)

    assert parsed.sql_query == expected_sql
    assert parsed.sql_table == expected_table
    assert _extract_table_navigation(expression) == expected_table


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM [dbo].[Orders]]",
        "SELECT * FROM `sales``.`orders`",
    ],
)
def test_native_sql_rejects_unbalanced_dialect_identifiers(sql):
    assert _extract_table_navigation(sql) is None


def test_postgres_native_sql_folds_only_unquoted_identifiers():
    unquoted_expression = (
        'let Source = PostgreSQL.Database("host", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM BI.ReportingTable") in Rows'
    )
    quoted_expression = (
        'let Source = PostgreSQL.Database("host", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""BI"".""ReportingTable""") in Rows'
    )

    unquoted = _parse_m_expression(unquoted_expression)
    quoted = _parse_m_expression(quoted_expression)

    assert unquoted.sql_table == "bi.reportingtable"
    assert quoted.sql_table == "BI.ReportingTable"
    assert split_relation(unquoted.sql_table) == ("bi", "reportingtable")
    assert split_relation(quoted.sql_table) == ("BI", "ReportingTable")
    assert unquoted.connection_key != quoted.connection_key
    assert _extract_table_navigation(
        unquoted_expression,
        source_type="postgresql",
    ) == "bi.reportingtable"
    assert _extract_table_navigation(
        quoted_expression,
        source_type="postgresql",
    ) == "BI.ReportingTable"


def test_postgres_tmdl_navigation_keeps_resolved_catalog_case():
    expression = (
        'let Source = PostgreSQL.Database("host", "warehouse"), '
        'Rows = Source{[Schema="BI", Item="ReportingTable"]}[Data] in Rows'
    )

    parsed = _parse_m_expression(expression)

    assert parsed.sql_table == "BI.ReportingTable"
    assert _extract_table_navigation(
        expression,
        source_type="postgresql",
    ) == "BI.ReportingTable"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "-- FROM fake.comment\n"
            "SELECT 'FROM fake.string' AS note FROM real.good",
            "real.good",
        ),
        (
            "/* FROM fake.outer /* FROM fake.inner */ still comment */ "
            "SELECT $$FROM fake.dollar$$ FROM real.good",
            "real.good",
        ),
        ("SELECT * FROM ONLY real.good", "real.good"),
        ("WITH staged AS (SELECT * FROM real.good) SELECT * FROM staged", "real.good"),
        ("WITH staged AS (SELECT 1) SELECT * FROM staged", None),
        ("SELECT * FROM LATERAL make_rows()", None),
        ("SELECT * FROM real.good JOIN other.input ON true", None),
        ("SELECT * FROM real.good a JOIN real.good b ON true", "real.good"),
    ],
)
def test_native_sql_relation_extraction_is_conservative(sql, expected):
    assert _extract_table_navigation(sql, source_type="postgresql") == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM real.good g, other.input o",
        "SELECT * FROM real.good a, real.good b",
        "SELECT * FROM (SELECT * FROM real.good) g, other.input o",
        "SELECT * FROM generate_series(1, 2) g, real.good",
        'SELECT * FROM schema_."table_name" t, other.input o',
    ],
)
def test_native_sql_comma_join_is_never_claimed_as_one_relation(sql):
    assert _extract_table_navigation(sql, source_type="postgresql") is None


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT * FROM real.good WHERE coalesce(left_value, right_value) IS NOT NULL",
            "real.good",
        ),
        (
            "SELECT * FROM (SELECT coalesce(left_value, right_value) FROM real.good) nested",
            "real.good",
        ),
        (
            "SELECT * FROM real.good AS renamed(first_column, second_column)",
            "real.good",
        ),
        (
            "SELECT * FROM real.good JOIN LATERAL make_rows(left_value, right_value) rows ON true",
            "real.good",
        ),
    ],
)
def test_native_sql_nested_commas_do_not_look_like_comma_joins(sql, expected):
    assert _extract_table_navigation(sql, source_type="postgresql") == expected


@pytest.mark.parametrize(
    "value",
    [
        'schema."unterminated',
        '"unterminated.schema',
        'schema."embedded""quote',
    ],
)
def test_split_relation_rejects_unbalanced_quotes(value):
    assert split_relation(value) is None


def test_unresolved_postgres_queries_do_not_share_one_generic_identity():
    first = SourceInfo(
        source_type="postgresql",
        server="db.internal",
        database="warehouse",
        raw_expression='Value.NativeQuery(Source, "SELECT dynamic_a()")',
    )
    second = SourceInfo(
        source_type="postgresql",
        server="db.internal",
        database="warehouse",
        raw_expression='Value.NativeQuery(Source, "SELECT dynamic_b()")',
    )
    other_server = SourceInfo(
        source_type="postgresql",
        server="other.internal",
        database="warehouse",
        raw_expression=first.raw_expression,
    )

    assert first.connection_key != second.connection_key
    assert first.connection_key != other_server.connection_key
    assert first.display_name != second.display_name
    assert first.display_name != other_server.display_name
    assert _validate_table_name(first.display_name) == first.display_name
    assert _validate_table_name(second.display_name) == second.display_name


class _CatalogCursor:
    def __init__(self):
        self.rows = []

    def execute(self, query, parameters=None):
        if "FROM pg_depend" in query:
            assert "c_dep.relkind IN ('r', 'p', 'm', 'v', 'f')" in query
            self.rows = [
                (
                    "bi_reporting",
                    "inflow_outflow_mv",
                    "m",
                    "bi_reporting",
                    "asap_stage",
                    "v",
                ),
                (
                    "bi_reporting",
                    "asap_stage",
                    "v",
                    "bi_reporting",
                    "asap_import",
                    "p",
                ),
            ]
        elif "FROM pg_matviews" in query:
            self.rows = []
        elif "FROM pg_class cls" in query:
            values = tuple(parameters or ())
            requested = list(zip(values[::2], values[1::2]))
            kinds = {
                ("bi_reporting", "inflow_outflow_mv"): "m",
                ("bi_reporting", "asap_stage"): "v",
                ("bi_reporting", "asap_import"): "p",
            }
            self.rows = [
                (schema, relation, kinds[(schema, relation)])
                for schema, relation in requested
                if (schema, relation) in kinds
            ]
        else:  # pragma: no cover - fails loudly if catalog behavior expands
            raise AssertionError(query)

    def fetchall(self):
        return list(self.rows)


class _CatalogConnection:
    def __init__(self):
        self.cursor_instance = _CatalogCursor()

    def cursor(self):
        return self.cursor_instance

    def close(self):
        return None


def test_quoted_report_source_connects_flow_through_view_and_partitioned_table(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "quoted-lineage.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()

    server = "db.internal"
    database_name = "warehouse"
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""bi_reporting"".""inflow_outflow_mv""") in Rows'
    )
    parsed = _parse_m_expression(expression)
    schema, relation = split_relation(parsed.sql_table) or (None, None)
    assert (schema, relation) == ("bi_reporting", "inflow_outflow_mv")

    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Inflow outflow')")
        db.execute(
            "INSERT INTO sources(id, name, type, connection_info, source_query) "
            "VALUES (10, ?, 'postgresql', ?, ?)",
            (parsed.display_name, parsed.connection_info, expression),
        )
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id, source_expression) "
            "VALUES (1, 'Model', 10, ?)",
            (expression,),
        )
        db.execute(
            "INSERT INTO flow_sites(id, name, base_url) "
            "VALUES (100, 'Quoted lineage portal', 'https://example.test')"
        )
        db.execute(
            "INSERT INTO flow_reports(id, site_id, name, report_url) "
            "VALUES (100, 100, 'Export', 'https://example.test/export')"
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_mode, sql_database, sql_schema, sql_table)
               VALUES (20, 'ASAP import', 100, 100, 'C:\\Exports', 'asap.csv',
                       1, 'append', ?, 'bi_reporting', 'asap_import')""",
            (database_name,),
        )

    monkeypatch.setattr(pg_deps, "PGHOST", server)
    monkeypatch.setattr(pg_deps, "PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "UPLOAD_PGHOST", server)
    monkeypatch.setattr(pg_deps, "UPLOAD_PGPORT", 5432)
    monkeypatch.setattr(pg_deps, "PGDATABASE", database_name)
    monkeypatch.setattr(lineage, "UPLOAD_PGHOST", server)
    monkeypatch.setattr(lineage, "UPLOAD_PGPORT", 5432)
    monkeypatch.setattr(
        pg_deps,
        "_get_pg_connection",
        lambda *, database: _CatalogConnection(),
    )

    # This deliberately begins with the pre-upgrade state: the report points
    # at a PostgreSQL source whose exact identity has not been claimed yet.
    # The report-scoped Pipeline recheck must repair that root before reading
    # pg_depend; requiring a separate Full Scan was the production bug.
    result = pg_deps.scan_pg_dependencies(report_id=1)

    assert result["status"] == "completed"
    assert result["deps_created"] == 2
    assert result["report_identity_reconciliation"]["claimed"] == 1
    with get_db() as db:
        source_ids, edges = pipelines._source_closure(db, 1)
        identities = {
            row["relation_name"]: (int(row["source_id"]), row["relation_kind"])
            for row in db.execute(
                """SELECT source_id, relation_name, relation_kind
                     FROM source_postgres_identities
                    WHERE server_name=? AND database_name=? AND schema_name='bi_reporting'""",
                (server, database_name),
            ).fetchall()
        }
        diagnostics = build_flow_diagnostics(db, source_ids, server=server)
        saved_target = db.execute(
            "SELECT sql_target_source_id FROM flows WHERE id=20"
        ).fetchone()[0]

    assert identities["inflow_outflow_mv"] == (10, "materialized_view")
    assert identities["asap_stage"][1] == "view"
    assert identities["asap_import"][1] == "table"
    assert set(edges) == {
        (identities["inflow_outflow_mv"][0], identities["asap_stage"][0]),
        (identities["asap_stage"][0], identities["asap_import"][0]),
    }
    assert identities["asap_import"][0] in source_ids
    assert saved_target == identities["asap_import"][0]
    assert included_flow_ids(diagnostics) == {20}
    diagram = lineage.get_lineage_diagram(1)
    assert [flow["name"] for flow in diagram["flows"]] == ["ASAP import"]
    assert diagram["flows"][0]["target_source_ids"] == [identities["asap_import"][0]]


def test_report_scoped_recheck_repairs_only_the_selected_legacy_report(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "selected-report-repair.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    expression = (
        'let Source = PostgreSQL.Database("db.internal", "warehouse"), '
        'Rows = Value.NativeQuery(Source, '
        '"SELECT * FROM ""bi_reporting"".""inflow_outflow_mv""") in Rows'
    )

    with get_db() as db:
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Selected')")
        db.execute("INSERT INTO reports(id, name) VALUES (2, 'Other')")
        db.execute(
            """INSERT INTO sources
                   (id, name, type, connection_info, source_query)
               VALUES (10, 'legacy_wrong_root', 'postgresql', 'legacy', ?)""",
            (expression,),
        )
        upsert_postgres_identity(
            db,
            source_id=10,
            server="db.internal",
            database="warehouse",
            schema="bi_reporting",
            relation="legacy_wrong_root",
        )
        for report_id in (1, 2):
            db.execute(
                """INSERT INTO report_tables
                       (report_id, table_name, source_id, source_expression)
                   VALUES (?, 'Model', 10, ?)""",
                (report_id, expression),
            )

    result = reconcile_report_postgres_identities(1)

    assert result["created"] == 1
    assert result["relinked"] == 1
    with get_db() as db:
        selected_source = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=1"
        ).fetchone()[0]
        other_source = db.execute(
            "SELECT source_id FROM report_tables WHERE report_id=2"
        ).fetchone()[0]
        old_identity = db.execute(
            "SELECT relation_name FROM source_postgres_identities WHERE source_id=10"
        ).fetchone()[0]
        new_identity = db.execute(
            "SELECT relation_name FROM source_postgres_identities WHERE source_id=?",
            (selected_source,),
        ).fetchone()[0]

    assert selected_source != 10
    assert other_source == 10
    assert old_identity == "legacy_wrong_root"
    assert new_identity == "inflow_outflow_mv"
