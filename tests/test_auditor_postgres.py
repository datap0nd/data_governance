"""Read-only guarantees against a dedicated disposable PostgreSQL database."""
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from auditor_reader import policy, postgres


@pytest.fixture
def pg():
    url = os.environ.get("METRONOME_TEST_POSTGRES_DSN")
    if not url:
        pytest.skip("Disposable PostgreSQL is exercised by the PostgreSQL CI jobs.")
    import psycopg2
    from psycopg2 import sql
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database.startswith("metronome_test_")
    name = "metronome_test_auditor_" + uuid.uuid4().hex[:10]
    role, parent = name + "_reader", name + "_parent"
    parameters = {"host": parsed.host, "port": parsed.port or 5432, "user": parsed.username,
                  "dbname": parsed.database}
    admin = psycopg2.connect(**parameters)
    admin.autocommit = True
    def admin_sql(command):
        with admin.cursor() as cursor:
            cursor.execute(command)
    admin_sql(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin_sql(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    admin_sql(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(parent)))
    owner = psycopg2.connect(**{**parameters, "dbname": name})
    owner.autocommit = True
    def execute(statement):
        with owner.cursor() as cursor:
            cursor.execute(statement)
            return cursor.fetchall() if cursor.description else None
    execute(sql.SQL("REVOKE TEMPORARY,CREATE ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
    execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    execute("CREATE SCHEMA audit_source")
    execute("CREATE TABLE audit_source.sales(region text, units numeric)")
    execute("INSERT INTO audit_source.sales VALUES('North',10),('South',20),('South',3)")
    execute(sql.SQL("GRANT USAGE ON SCHEMA audit_source TO {}").format(sql.Identifier(role)))
    execute(sql.SQL("GRANT SELECT ON audit_source.sales TO {}").format(sql.Identifier(role)))
    dsn = psycopg2.extensions.make_dsn(**{**parameters, "dbname": name, "user": role, "password": "synthetic-trust-fixture"})
    dataset = policy.Dataset.model_validate_json('{"id":"sales","flow_id":1,"schema_name":"audit_source","table_name":"sales","columns":[{"name":"region","csv_header":"Region"},{"name":"units","csv_header":"Units","kind":"number"}]}')
    digest = postgres.profile_sql(dataset, dsn, b"s" * 32, lambda: False, discover=True)["fingerprint"]
    dataset = dataset.model_copy(update={"fingerprint": digest})
    try:
        yield SimpleNamespace(**locals())
    finally:
        owner.close()
        admin_sql(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        for item in (role, parent):
            admin_sql(sql.SQL("DROP ROLE {}").format(sql.Identifier(item)))
        admin.close()


def test_complete_aggregates_do_not_change_table(pg):
    before = pg.execute("SELECT * FROM audit_source.sales ORDER BY region,units")
    result = postgres.profile_sql(pg.dataset, pg.dsn, b"s" * 32, lambda: False)
    assert result["rows"] == 3 and result["columns"]["units"]["sum"] == "33"
    assert result["columns"]["region"]["distinct"] == 2
    assert "North" not in str(result)
    assert pg.execute("SELECT * FROM audit_source.sales ORDER BY region,units") == before


@pytest.mark.parametrize("power", ["update", "public_temp", "create", "membership", "public_function", "sequence", "owner"])
def test_effective_write_and_indirect_powers_are_rejected(pg, power):
    quote = pg.sql.Identifier
    if power == "update":
        pg.execute(pg.sql.SQL("GRANT UPDATE(units) ON audit_source.sales TO {}").format(quote(pg.role)))
    elif power == "public_temp":
        pg.execute(pg.sql.SQL("GRANT TEMPORARY ON DATABASE {} TO PUBLIC").format(quote(pg.name)))
    elif power == "create":
        pg.execute(pg.sql.SQL("GRANT CREATE ON SCHEMA audit_source TO {}").format(quote(pg.role)))
    elif power == "membership":
        pg.admin_sql(pg.sql.SQL("GRANT {} TO {}").format(quote(pg.parent), quote(pg.role)))
    elif power == "public_function":
        pg.execute("CREATE FUNCTION audit_source.dangerous() RETURNS int LANGUAGE sql AS 'DELETE FROM audit_source.sales RETURNING 1'")
    elif power == "sequence":
        pg.execute("CREATE SEQUENCE audit_source.sequence")
        pg.execute(pg.sql.SQL("GRANT USAGE ON SEQUENCE audit_source.sequence TO {}").format(quote(pg.role)))
    elif power == "owner":
        pg.execute(pg.sql.SQL("ALTER TABLE audit_source.sales OWNER TO {}").format(quote(pg.role)))
    with pytest.raises(policy.InspectionError, match="reader_privileges_unsafe"):
        postgres.profile_sql(pg.dataset, pg.dsn, b"s" * 32, lambda: False)
    assert pg.execute("SELECT count(*) FROM audit_source.sales")[0][0] == 3


@pytest.mark.parametrize("drift", ["column", "rls", "view", "domain", "expression_index"])
def test_definition_drift_and_indirect_execution_are_rejected(pg, drift):
    if drift == "column":
        pg.execute("ALTER TABLE audit_source.sales ADD COLUMN extra text")
    elif drift == "rls":
        pg.execute("ALTER TABLE audit_source.sales ENABLE ROW LEVEL SECURITY")
    elif drift == "view":
        pg.execute("ALTER TABLE audit_source.sales RENAME TO original")
        pg.execute("CREATE VIEW audit_source.sales AS SELECT * FROM audit_source.original")
    elif drift == "domain":
        pg.execute("CREATE DOMAIN audit_source.custom AS text")
        pg.execute("ALTER TABLE audit_source.sales ADD COLUMN extra audit_source.custom")
    elif drift == "expression_index":
        pg.execute("CREATE INDEX expression_index ON audit_source.sales ((lower(region)))")
    with pytest.raises(policy.InspectionError):
        postgres.profile_sql(pg.dataset, pg.dsn, b"s" * 32, lambda: False)


def test_transaction_has_no_write_or_commit_path(pg):
    import psycopg2
    with postgres.connection(pg.dsn, lambda: False) as cursor:
        cursor.execute("SHOW transaction_read_only")
        assert cursor.fetchone()[0] == "on"
        with pytest.raises(psycopg2.Error):
            cursor.execute("DELETE FROM audit_source.sales")
    assert pg.execute("SELECT count(*) FROM audit_source.sales")[0][0] == 3
