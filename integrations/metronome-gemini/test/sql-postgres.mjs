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
    const reader = new ReadonlySql({ dsn: readerUrl.href, relations: `${schema}.trips` });
    const result = await reader.query(`SELECT "법인",count(*) AS rows,min(trip_date)::text AS first_date,max(trip_date)::text AS last_date,sum(amount)::text AS amount FROM ${schema}.trips GROUP BY "법인" ORDER BY "법인"`);
    assert.equal(result.truncated, false);
    assert.deepEqual(result.rows, [
      { 법인: '가상 A', rows: '2', first_date: '2026-09-01', last_date: '2026-09-03', amount: '200' },
      { 법인: '가상 B', rows: '1', first_date: '2026-09-02', last_date: '2026-09-02', amount: null },
    ]);
    const columns = await reader.schema();
    assert.deepEqual(columns.rows.map(r => r.column_name), ['법인', 'trip_date', 'amount']);
    await assert.rejects(reader.query(`UPDATE ${schema}.trips SET amount=0`), /Only SELECT/);
    await assert.rejects(reader.query('select * from pg_catalog.pg_authid'), /allowlist/);
    const direct = new pg.Client({ connectionString: readerUrl.href });
    await direct.connect();
    try { await assert.rejects(direct.query(`INSERT INTO ${schema}.trips VALUES ('bad',CURRENT_DATE,0)`), /permission denied/); }
    finally { await direct.end(); }
    const writerUrl = new URL(url); writerUrl.username = writerName;
    const writer = new ReadonlySql({ dsn: writerUrl.href, relations: `${schema}.trips` });
    await assert.rejects(writer.query(`SELECT * FROM ${schema}.trips`), /dedicated SELECT-only/);
    assert.equal((await admin.query(`SELECT count(*) FROM ${schema}.trips`)).rows[0].count, '3');
  } finally {
    await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
    await admin.query(`DROP ROLE IF EXISTS ${readerName},${writerName}`);
    await admin.end();
  }
});
