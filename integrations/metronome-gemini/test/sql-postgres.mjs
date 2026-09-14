// Runs only against an explicitly configured disposable CI database.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { ReadonlySql } from '../sql.mjs';

test('PostgreSQL enforces reader separation and complete Korean source aggregates', async () => {
  const url = process.env.METRONOME_TEST_PG_URL;
  assert.ok(url, 'Set METRONOME_TEST_PG_URL to an explicitly disposable database.');
  const parsed = new URL(url);
  assert.equal(parsed.pathname, '/metronome_test_ownership', 'This fixture must not run against a production database.');
  assert.ok(['127.0.0.1', 'localhost'].includes(parsed.hostname));
  const admin = new pg.Client({ connectionString: url });
  await admin.connect();
  const suffix = `${process.pid}_${Date.now()}`;
  const schema = `gemini_${suffix}`, readerName = `reader_${suffix}`, writerName = `writer_${suffix}`;
  const secondDatabase = `metronome_test_gemini_${suffix}`;
  try {
    // The database is disposable. PG14 defaults grant CREATE on public to PUBLIC.
    await admin.query('REVOKE CREATE ON SCHEMA public FROM PUBLIC');
    await admin.query(`CREATE SCHEMA ${schema}`);
    await admin.query(`CREATE TABLE ${schema}.trips ("법인" text, trip_date date, amount numeric)`);
    await admin.query(`INSERT INTO ${schema}.trips VALUES ('가상 A','2026-09-01',120),('가상 B','2026-09-02',NULL),('가상 A','2026-09-03',80)`);
    await admin.query(`CREATE ROLE ${readerName} LOGIN`);
    await admin.query(`CREATE ROLE ${writerName} LOGIN`);
    await admin.query(`GRANT USAGE ON SCHEMA ${schema} TO ${readerName},${writerName}`);
    await admin.query(`GRANT SELECT ON ${schema}.trips TO ${readerName}`);
    await admin.query(`GRANT SELECT, INSERT ON ${schema}.trips TO ${writerName}`);
    const readerUrl = new URL(url); readerUrl.username = readerName;
    const reader = new ReadonlySql({ dsn: readerUrl.href });
    const result = await reader.query(`SELECT "법인",count(*) AS rows,min(trip_date)::text AS first_date,max(trip_date)::text AS last_date,sum(amount)::text AS amount FROM ${schema}.trips GROUP BY "법인" ORDER BY "법인"`);
    assert.equal(result.truncated, false);
    assert.deepEqual(result.rows, [
      { 법인: '가상 A', rows: '2', first_date: '2026-09-01', last_date: '2026-09-03', amount: '200' },
      { 법인: '가상 B', rows: '1', first_date: '2026-09-02', last_date: '2026-09-02', amount: null },
    ]);
    const columns = await reader.schema({ schema_name: schema, relation_name: 'trips' });
    assert.ok(columns.rows.every(r => r.database_name === 'metronome_test_ownership'));
    assert.deepEqual(columns.rows.map(r => r.column_name), ['법인', 'trip_date', 'amount']);
    await assert.rejects(reader.query(`UPDATE ${schema}.trips SET amount=0`), /Only SELECT/);
    await assert.rejects(reader.query('select * from pg_catalog.pg_authid'), /Read-only SQL failed/);
    await admin.query(`CREATE TABLE ${schema}.later_table AS SELECT '추가'::text AS label`);
    await admin.query(`CREATE VIEW ${schema}.trip_view AS SELECT * FROM ${schema}.trips`);
    await admin.query(`CREATE TABLE ${schema}.private_table (secret text)`);
    await admin.query(`GRANT SELECT ON ${schema}.later_table,${schema}.trip_view TO ${readerName}`);
    assert.deepEqual((await reader.query(`SELECT * FROM ${schema}.later_table`)).rows, [{ label: '추가' }]);
    assert.equal((await reader.query(`SELECT count(*) FROM ${schema}.trip_view`)).rows[0].count, '3');
    await assert.rejects(reader.query(`SELECT * FROM ${schema}.private_table`), /Read-only SQL failed/);
    const discovered = await reader.schema({ schema_name: schema });
    assert.ok(discovered.rows.some(r => r.relation_name === 'later_table'));
    assert.ok(!discovered.rows.some(r => r.relation_name === 'private_table'));
    const firstPage = await reader.schema({ schema_name: schema, relation_name: 'trips', limit: 2 });
    assert.equal(firstPage.next_offset, 2);
    const lastPage = await reader.schema({ schema_name: schema, relation_name: 'trips', limit: 2, offset: 2 });
    assert.equal(lastPage.next_offset, null); assert.equal(lastPage.rows[0].column_name, 'amount');
    const direct = new pg.Client({ connectionString: readerUrl.href });
    await direct.connect();
    try { await assert.rejects(direct.query(`INSERT INTO ${schema}.trips VALUES ('bad',CURRENT_DATE,0)`), /permission denied/); }
    finally { await direct.end(); }
    const writerUrl = new URL(url); writerUrl.username = writerName;
    const writer = new ReadonlySql({ dsn: writerUrl.href });
    await assert.rejects(writer.query(`SELECT * FROM ${schema}.trips`), /dedicated SELECT-only/);
    assert.equal((await admin.query(`SELECT count(*) FROM ${schema}.trips`)).rows[0].count, '3');
    await admin.query(`CREATE DATABASE ${secondDatabase}`);
    const secondUrl = new URL(url); secondUrl.pathname = '/' + secondDatabase;
    const secondAdmin = new pg.Client({ connectionString: secondUrl.href });
    await secondAdmin.connect();
    try {
      await secondAdmin.query('REVOKE CREATE ON SCHEMA public FROM PUBLIC');
      await secondAdmin.query('CREATE TABLE public.other_report (label text)');
      await secondAdmin.query("INSERT INTO public.other_report VALUES ('두 번째 데이터베이스')");
      await secondAdmin.query(`GRANT SELECT ON public.other_report TO ${readerName}`);
      const fieldsReader = new ReadonlySql({ host: `${parsed.hostname}:${parsed.port || 5432}`,
        user: readerName, password: decodeURIComponent(parsed.password), database: 'metronome_test_ownership' });
      const databases = await fieldsReader.databases();
      assert.ok(databases.rows.some(r => r.database_name === secondDatabase));
      assert.deepEqual((await fieldsReader.query('SELECT * FROM public.other_report', [], 10, secondDatabase)).rows,
        [{ label: '두 번째 데이터베이스' }]);
      assert.equal((await fieldsReader.schema({ database: secondDatabase, relation_name: 'other_report' })).rows[0].database_name, secondDatabase);
      await secondAdmin.query(`GRANT INSERT ON public.other_report TO ${readerName}`);
      await assert.rejects(fieldsReader.query('SELECT * FROM public.other_report', [], 10, secondDatabase), /dedicated SELECT-only/);
      // Changing databases must not poison the reader's original connection or grants.
      assert.equal((await fieldsReader.query(`SELECT count(*) FROM ${schema}.trips`)).rows[0].count, '3');
      await secondAdmin.query(`REVOKE CONNECT ON DATABASE ${secondDatabase} FROM PUBLIC`);
      assert.ok(!(await fieldsReader.databases()).rows.some(r => r.database_name === secondDatabase));
      await assert.rejects(fieldsReader.query('SELECT 1', [], 1, secondDatabase), /starting database could not be opened/);
    } finally { await secondAdmin.end(); }
  } finally {
    await admin.query(`DROP DATABASE IF EXISTS ${secondDatabase} WITH (FORCE)`);
    await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
    await admin.query(`DROP ROLE IF EXISTS ${readerName},${writerName}`);
    await admin.end();
  }
});
