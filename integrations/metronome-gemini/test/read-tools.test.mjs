import test from 'node:test';
import assert from 'node:assert/strict';
import { MetronomeClient, scrub, definitionOf, digest } from '../client.mjs';
import { ReadonlySql, validateReadQuery, PRIVILEGE_CHECK, readerConnection } from '../sql.mjs';
import pg from 'pg';

const fixture = { id: 42, name: '출장비', source_type: 'portal', site_id: 1, report_id: 15, enabled: false,
  selections: { subsidiary: '가상 A' }, sql_handoff_enabled: false, schedule_type: 'manual' };
function clientFor(routes, options = {}) {
  const requests = [];
  return { requests, client: new MetronomeClient({ ...options, fetchImpl: async (url, init) => {
    const path = new URL(url).pathname + new URL(url).search;
    requests.push({ path, ...init });
    assert.equal(init.redirect, 'error');
    return Response.json(routes[path] ?? {}, { status: Object.hasOwn(routes, path) ? 200 : 404 });
  } }) };
}
test('loopback origins only; model cannot smuggle credentials, paths or remote hosts', () => {
  for (const url of ['https://example.com', 'file:///tmp/a', 'http://127.0.0.1/api', 'http://u:p@localhost', 'http://localhost?token=x']) {
    assert.throws(() => new MetronomeClient({ baseUrl: url }), /loopback/);
  }
  assert.doesNotThrow(() => new MetronomeClient({ baseUrl: 'http://[::1]:8000' }));
});
test('flow and site scope enforced before exposing data', async () => {
  const { client, requests } = clientFor({ '/api/flows/42': fixture, '/api/flows': [fixture, { ...fixture, id: 43 }] }, { flowIds: '42', siteIds: '1' });
  assert.equal((await client.listFlows()).length, 1);
  await assert.rejects(client.getFlow(43), /outside/);
  assert.equal(requests.length, 1);
  const other = clientFor({ '/api/flows/42': fixture }, { siteIds: '2' }).client;
  await assert.rejects(other.getFlow(42), /outside/);
});
test('catalog projections keep filter options but omit arbitrary automation and secrets', async () => {
  const { client } = clientFor({ '/api/flows/catalog': { sites: [{ id: 1, name: 'ASAP', password: 'secret' }],
    reports: [{ id: 15, site_id: 1, name: '출장', automation: { storage_state: 'secret' },
      filters: [{ filter_key: 'sub', options: ['가상 A'], automation: { token: 'secret' } }] }] } });
  const result = await client.catalog();
  assert.deepEqual(result.reports[0].filters[0].options, ['가상 A']);
  assert.ok(!JSON.stringify(result).includes('secret'));
});
test('recording metadata does not return entered values, session state or evidence', async () => {
  const { client } = clientFor({ '/api/flows/42': fixture, '/api/flows/42/recordings': {
    revisions: [{ id: 8, definition: { steps: [{ value: 'private-login' }] }, evidence: 'private' }], sessions: [{ result_json: 'secret' }] } });
  const result = await client.recordings(42);
  assert.deepEqual(result.revisions, [{ id: 8 }]);
  assert.ok(!JSON.stringify(result).includes('private'));
});
test('run reads verify flow access and omit raw jobs, logs and entered values', async () => {
  const { client } = clientFor({ '/api/flows/42': fixture, '/api/flows/runs/9': {
    id: 9, flow_id: 42, status: 'failed', job: { password: 'private' }, events: [{ message: 'private' }], error: 'private' } });
  assert.deepEqual(await client.getRun(9), { id: 9, flow_id: 42, status: 'failed', files: [] });
});
test('transport failure never retries a write or echoes upstream secrets', async () => {
  let count = 0;
  const client = new MetronomeClient({ fetchImpl: async () => { count++; throw new Error('password=secret'); } });
  await assert.rejects(client.saveFlow({ ...fixture, id: undefined }), /Unsupported flow field/);
  await assert.rejects(client.saveFlow(definitionOf(fixture)), error => /may have happened/.test(error.message) && !error.message.includes('secret'));
  assert.equal(count, 1);
});
test('script, email and refresh execution cannot be smuggled through a flow definition', () => {
  const client = new MetronomeClient();
  for (const extra of [{ transform_enabled: true }, { transform_script_path: 'evil.py' },
    { email_delivery: { enabled: true } }, { post_sql_refresh: { mode: 'all' } }, { credentials: {} }]) {
    assert.throws(() => client.checkDefinition({ ...definitionOf(fixture), ...extra }));
  }
  assert.equal(digest({ b: 2, a: 1 }), digest({ a: 1, b: 2 }));
});
const relations = new Set(['reporting.trips', 'reporting.subs']);
test('SQL supports scoped Korean columns, aggregates, joins, subqueries and read CTEs', () => {
  for (const sql of [
    'select "법인", count(*) from reporting.trips group by "법인"',
    'select t.sub, sum(t.amount::numeric) from reporting.trips t join reporting.subs s on t.sub=s.id group by t.sub',
    'select * from reporting.trips where trip_date >= $1',
    "select date_trunc('month',trip_date), min(amount), max(amount) from reporting.trips group by date_trunc('month',trip_date)",
    'with a as (select * from reporting.trips) select count(*) from a',
    'select * from (select sub from reporting.trips) a',
  ]) assert.doesNotThrow(() => validateReadQuery(sql, relations), sql);
});
test('SQL rejects mutation, multiple statements, unscoped objects and side-effect functions', () => {
  for (const sql of ['delete from reporting.trips', 'select 1; drop table reporting.trips',
    'with a as (delete from reporting.trips returning *) select * from a', 'select * into temp a from reporting.trips',
    'select * from reporting.trips for update', 'select * from secret.trips', 'select * from trips',
    "select set_config('transaction_read_only','off',false)", "select nextval('x')", "select pg_read_file('/etc/passwd')",
    'select public.count(*) from reporting.trips', 'select dblink_connect(\'x\')', 'select amount::public.evil from reporting.trips',
    'set role uploader', 'copy reporting.trips to program \'evil\'', 'select pg_sleep(500)']) {
    assert.throws(() => validateReadQuery(sql, relations), undefined, sql);
  }
});
function fakeDatabase({ elevated = false, writable = false, can_create = false, can_create_schema = false, rows = [{ n: '12' }] } = {}) {
  const calls = [];
  class Client {
    constructor(config) { calls.push(['config', config]); }
    async connect() { calls.push(['connect']); }
    async query(sql, params) {
      calls.push([sql, params]);
      if (sql === PRIVILEGE_CHECK) return { rows: [{ elevated, writable, can_create, can_create_schema }] };
      return { rows, fields: [{ name: 'n' }] };
    }
    async end() { calls.push(['end']); }
  }
  return { calls, Client };
}
test('SQL is opt-in and never falls back to PostgreSQL/upload environment credentials', async () => {
  const { Client, calls } = fakeDatabase();
  await assert.rejects(new ReadonlySql({ Client }).query('select 1'), /not configured/);
  assert.equal(calls.length, 0);
});
test('actual pg configuration cannot inherit uploader credentials, ambient password, port or insecure TLS', async () => {
  const ambient = { PGUSER: 'uploader', PGPASSWORD: 'must-not-use', PGHOST: 'other-host', PGDATABASE: 'other-db',
    PGPORT: '9999', PGSSLMODE: 'no-verify', DG_UPLOAD_PGPASSWORD: 'must-not-use-either' };
  const previous = Object.fromEntries(Object.keys(ambient).map(k => [k, process.env[k]]));
  try {
    Object.assign(process.env, ambient);
    const client = new pg.Client(readerConnection('postgresql://reader@localhost/fixture'));
    const actual = client.connectionParameters;
    assert.equal(actual.user, 'reader'); assert.equal(actual.host, 'localhost');
    assert.equal(actual.database, 'fixture'); assert.equal(actual.port, 5432);
    assert.equal(actual.ssl, false); assert.equal(typeof actual.password, 'function');
    assert.equal(await actual.password(), '');
    assert.equal(await readerConnection('postgresql://reader:explicit@localhost/fixture').password(), 'explicit');
    assert.notEqual(readerConnection('postgresql://reader@localhost/fixture?sslmode=require').ssl.rejectUnauthorized, false);
    for (const query of ['sslmode=no-verify', 'ssl=no-verify', 'user=uploader', 'options=-c%20role=uploader']) {
      assert.throws(() => readerConnection(`postgresql://reader@localhost/fixture?${query}`));
    }
  } finally {
    for (const [key, value] of Object.entries(previous)) if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
});
test('reader rejects effective write/ownership/elevated privileges before the analysis query', async () => {
  for (const key of ['elevated', 'writable', 'can_create', 'can_create_schema']) {
    const { Client, calls } = fakeDatabase({ [key]: true });
    const reader = new ReadonlySql({ dsn: 'postgresql://reader@localhost/fixture', relations: 'reporting.trips', Client });
    await assert.rejects(reader.query('select * from reporting.trips'), /dedicated SELECT-only/);
    assert.ok(!calls.some(([sql]) => sql.startsWith('SELECT * FROM (')));
    assert.ok(calls.some(([sql]) => sql === 'ROLLBACK'));
  }
});
test('reader uses read-only transaction, fixed search path, parameters, bounds and rollback', async () => {
  const { Client, calls } = fakeDatabase({ rows: [{ n: '1' }, { n: '2' }] });
  const reader = new ReadonlySql({ dsn: 'postgresql://reader@localhost/fixture', relations: 'reporting.trips', Client });
  const result = await reader.query('select * from reporting.trips where sub=$1', ['가상 A'], 1);
  assert.equal(result.truncated, true);
  assert.equal(result.returned_rows, 1);
  assert.ok(calls.some(([sql]) => sql === 'BEGIN READ ONLY'));
  assert.ok(calls.some(([sql]) => sql === 'SET LOCAL search_path = pg_catalog'));
  assert.deepEqual(calls.find(([sql]) => sql.startsWith('SELECT * FROM ('))[1], ['가상 A']);
  assert.equal(calls.at(-2)[0], 'ROLLBACK');
  assert.equal(calls.at(-1)[0], 'end');
});
test('credential-shaped response fields and URL authentication are scrubbed', () => {
  const result = scrub({ api_token: 's', value: { password: 's', storage_state: 's' }, url: 'https://u:pw@example.com/x?token=s' });
  assert.deepEqual(result.value, {});
  assert.ok(!JSON.stringify(result).includes('pw'));
  assert.equal(result.url, 'https://[redacted]@example.com/x?token=[redacted]');
});
