"""Real PostgreSQL ownership transactions in an explicitly disposable cluster."""
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app import flow_sql


@pytest.fixture
def pg(tmp_path, monkeypatch):
    dsn = os.environ.get('METRONOME_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Dedicated disposable PostgreSQL fixture not configured; runs in PostgreSQL CI jobs.')
    url = make_url(dsn)
    assert url.host in {'127.0.0.1', 'localhost'} and (url.database or '').startswith('metronome_test_'), 'Use only a local disposable test database.'
    prefix = 'so_' + uuid.uuid4().hex[:10]
    schema, loader, owner, reader, other = [prefix + suffix for suffix in ('_schema', '_loader', '_owner', '_reader', '_other')]
    admin = create_engine(url, poolclass=NullPool)
    quote = flow_sql._quote_identifier
    qualified = f'{quote(schema)}."target"'
    roles = [loader, owner, reader, other]

    def sql(statement, params=None):
        with admin.begin() as connection:
            result = connection.exec_driver_sql(statement, params, execution_options={'no_parameters': params is None})
            return result.fetchall() if result.returns_rows else None

    sql(f'CREATE ROLE {quote(loader)} LOGIN INHERIT')
    for role in roles[1:]:
        sql(f'CREATE ROLE {quote(role)} NOLOGIN')
    sql(f'GRANT {quote(owner)} TO {quote(loader)}')
    sql(f'CREATE SCHEMA {quote(schema)}')
    sql(f'GRANT USAGE, CREATE ON SCHEMA {quote(schema)} TO {quote(loader)}, {quote(owner)}')
    sql(f'GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(reader)}')
    loader_url = url.set(username=loader, password=None)

    def engine(database):
        assert database == url.database
        return create_engine(loader_url, poolclass=NullPool)

    monkeypatch.setattr(flow_sql, '_engine', engine)
    source = tmp_path / 'report.csv'
    source.write_text('Code,Units\nA,7\nB,8\n', encoding='utf-8')
    target = {'database': url.database, 'schema': schema, 'table': 'target', 'mode': 'replace', 'owner_username': owner}

    def seed():
        uploader = engine(url.database)
        try:
            with uploader.begin() as connection:
                connection.exec_driver_sql(f'CREATE TABLE {qualified} (code TEXT PRIMARY KEY, units TEXT)')
                connection.exec_driver_sql(f"INSERT INTO {qualified} VALUES ('before','1')")
                connection.exec_driver_sql(f'GRANT SELECT ON {qualified} TO {quote(reader)}')
                connection.exec_driver_sql(f'CREATE MATERIALIZED VIEW {quote(schema)}.summary AS SELECT count(*) AS n FROM {qualified}')
        finally:
            uploader.dispose()

    def snapshot():
        catalog = sql('SELECT c.oid, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s', (schema, 'target'))
        return (catalog, sql(f'SELECT * FROM {qualified} ORDER BY code') if catalog else [])

    def load():
        events = []
        result = flow_sql.load_artifacts([{'file_path': str(source)}], target, progress=events.append)
        return result, events

    try:
        yield SimpleNamespace(**locals())
    finally:
        sql(f'DROP SCHEMA {quote(schema)} CASCADE')
        for role in reversed(roles):
            sql(f'DROP ROLE {quote(role)}')
        admin.dispose()


@pytest.mark.parametrize('existing,mode', [(False, 'replace'), (True, 'replace'), (True, 'append')])
def test_owner_applies_atomically_and_repeated_loads_keep_working(pg, existing, mode):
    if existing:
        pg.seed()
    before = pg.snapshot()
    pg.target['mode'] = mode
    result, events = pg.load()
    assert result['owner_username'] == pg.owner and result['owner_changed']
    assert pg.snapshot()[0][0][1] == pg.owner
    assert events[-1]['stage'] == 'sql_commit' and events[-1]['owner_username'] == pg.owner
    if existing:
        assert pg.snapshot()[0][0][0] == before[0][0][0]
        assert pg.sql('SELECT has_table_privilege(%s, %s, %s)', (pg.reader, pg.qualified, 'SELECT')) == [(True,)]
        assert pg.sql('SELECT count(*) FROM pg_constraint WHERE conrelid=%s AND contype=%s', (before[0][0][0], 'p')) == [(1,)]
    pg.source.write_text('Code,Units\nC,9\n', encoding='utf-8')
    repeated, _ = pg.load()
    assert repeated['owner_changed'] is False
    assert pg.snapshot()[0][0][1] == pg.owner
    if existing:
        engine = pg.engine(pg.url.database)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'REFRESH MATERIALIZED VIEW {pg.quote(pg.schema)}.summary')
        finally:
            engine.dispose()
    if mode == 'replace':
        pg.source.write_text('Code,Units,Extra\nD,10,yes\n', encoding='utf-8')
        result, _ = pg.load()
        assert result['columns_added'] == ['extra']
        assert pg.snapshot()[1] == [('D', '10', 'yes')]


@pytest.mark.parametrize('failure', ['missing_role', 'no_inherit', 'no_set', 'no_create', 'no_usage', 'not_owner'])
def test_permission_failure_preserves_rows_and_owner(pg, failure):
    pg.seed()
    if failure == 'missing_role':
        pg.target['owner_username'] = pg.prefix + '_absent'
    elif failure == 'no_inherit':
        if int(pg.sql('SHOW server_version_num')[0][0]) >= 160000:
            pg.sql(f'GRANT {pg.quote(pg.owner)} TO {pg.quote(pg.loader)} WITH INHERIT FALSE')
        else:
            pg.sql(f'ALTER ROLE {pg.quote(pg.loader)} NOINHERIT')
    elif failure == 'no_set':
        if int(pg.sql('SHOW server_version_num')[0][0]) < 160000:
            pytest.skip('Independent SET membership option was introduced in PostgreSQL 16.')
        pg.sql(f'GRANT {pg.quote(pg.owner)} TO {pg.quote(pg.loader)} WITH SET FALSE')
    elif failure == 'no_create':
        pg.sql(f'REVOKE CREATE ON SCHEMA {pg.quote(pg.schema)} FROM {pg.quote(pg.owner)}')
    elif failure == 'no_usage':
        pg.sql(f'REVOKE USAGE ON SCHEMA {pg.quote(pg.schema)} FROM {pg.quote(pg.owner)}')
    else:
        pg.sql(f'ALTER TABLE {pg.qualified} OWNER TO {pg.quote(pg.other)}')
    before = pg.snapshot()
    with pytest.raises(flow_sql.SqlHandoffError, match='ownership validation') as error:
        pg.load()
    assert 'confirmed rollback' in str(error.value)
    assert pg.snapshot() == before


@pytest.mark.parametrize('failure', ['copy', 'after_ownership'])
def test_actual_transaction_rolls_back_rows_and_ownership(pg, monkeypatch, failure):
    pg.seed()
    before = pg.snapshot()
    name = '_copy_artifact' if failure == 'copy' else '_apply_ownership'
    original = getattr(flow_sql, name)

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Injected failure after actual SQL mutation')

    monkeypatch.setattr(flow_sql, name, fail)
    with pytest.raises(flow_sql.SqlHandoffError, match='confirmed rollback'):
        pg.load()
    assert pg.snapshot() == before


def test_quoted_owner_and_reassignment_do_not_change_unrelated_tables(pg):
    pg.seed()
    pg.sql(f'CREATE TABLE {pg.quote(pg.schema)}.unrelated (code TEXT)')
    odd = pg.prefix + ' "Owner"; -- :value %(odd)s'
    pg.sql(f'CREATE ROLE {pg.quote(odd)} NOLOGIN')
    pg.roles.append(odd)
    pg.sql(f'GRANT {pg.quote(odd)} TO {pg.quote(pg.loader)}')
    pg.sql(f'GRANT USAGE, CREATE ON SCHEMA {pg.quote(pg.schema)} TO {pg.quote(odd)}')
    pg.load()
    pg.target['owner_username'] = odd
    result, _ = pg.load()
    assert result['previous_owner'] == pg.owner
    assert pg.snapshot()[0][0][1] == odd
    assert pg.sql('SELECT tableowner FROM pg_tables WHERE schemaname=%s AND tablename=%s', (pg.schema, 'unrelated')) == [(pg.url.username,)]


def test_without_sql_username_existing_owner_is_preserved(pg):
    pg.seed()
    before = pg.snapshot()
    pg.target.pop('owner_username')
    result, _ = pg.load()
    assert 'owner_username' not in result
    assert pg.snapshot()[0] == before[0]
