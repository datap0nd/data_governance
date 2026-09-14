import pg from 'pg';
import { parse as parseConnection } from 'pg-connection-string';
import { parse } from 'pgsql-ast-parser';

const FUNCTIONS = new Set(`count sum avg min max round abs ceil ceiling floor coalesce nullif greatest least
lower upper length char_length trim btrim ltrim rtrim substring concat concat_ws replace date_trunc date_part
extract to_char to_date current_date current_timestamp now age`.split(/\s+/));
const TYPES = new Set(`text varchar character char numeric decimal integer int int2 int4 int8 smallint bigint
real float float4 float8 double double precision boolean bool date timestamp timestamptz time timetz interval`.split(/\s+/));
const NODES = new Set(['select', 'union', 'union all', 'with', 'ref', 'table', 'statement', 'call', 'binary', 'unary',
  'cast', 'case', 'integer', 'numeric', 'string', 'boolean', 'null', 'list', 'parameter', 'keyword', 'extract',
  'INNER JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN', 'CROSS JOIN']);
const OPERATORS = new Set(['=', '!=', '<>', '>', '>=', '<', '<=', '+', '-', '*', '/', '%', 'AND', 'OR', 'NOT',
  'IS NULL', 'IS NOT NULL', 'IS TRUE', 'IS FALSE', 'IS NOT TRUE', 'IS NOT FALSE', 'LIKE', 'NOT LIKE', 'ILIKE',
  'NOT ILIKE', 'IN', 'NOT IN', 'BETWEEN', 'NOT BETWEEN', '||']);

export function readerConnection(dsn) {
  try {
    const url = new URL(dsn);
    if (!['postgres:', 'postgresql:'].includes(url.protocol) || !url.username || !url.hostname || url.pathname.length < 2 || url.hash) throw new Error();
    for (const key of url.searchParams.keys()) if (!['sslmode', 'sslrootcert', 'sslcert', 'sslkey'].includes(key)) throw new Error();
    if (url.searchParams.has('sslmode') && !['disable', 'require', 'verify-full'].includes(url.searchParams.get('sslmode'))) throw new Error();
    const parsed = parseConnection(dsn);
    return { user: parsed.user, host: parsed.host.replace(/^\[|\]$/g, ''), database: parsed.database,
      port: Number(parsed.port || 5432),
      // A function also suppresses node-postgres's implicit .pgpass lookup.
      password: () => parsed.password || '', ssl: parsed.ssl || false, sslnegotiation: 'postgres',
      connectionTimeoutMillis: 10000, application_name: 'metronome-gemini-readonly', client_encoding: 'UTF8',
      options: '-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=2000' };
  } catch { throw new Error('Configure an explicit PostgreSQL reader DSN with host, database and username. Supported parameters: sslmode (disable, require, verify-full), sslrootcert, sslcert, sslkey.'); }
}

export function validateReadQuery(sql, relations) {
  if (typeof sql !== 'string' || sql.length > 20000 || sql.includes('\0')) throw new Error('SQL must be a SELECT of at most 20,000 characters.');
  let statements;
  try { statements = parse(sql); } catch { throw new Error('SQL is outside the supported read-only grammar. Use a SELECT with qualified relations, safe aggregates and parameters.'); }
  if (statements.length !== 1) throw new Error('Exactly one read-only statement is allowed.');
  const root = statements[0];
  if (!['select', 'with', 'union', 'union all'].includes(root.type)) throw new Error('Only SELECT queries are allowed.');
  const used = new Set();
  function visit(node, ctes = new Set()) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(n => visit(n, ctes)); return; }
    if (node.type && !NODES.has(node.type)) throw new Error(`Unsupported SQL construct: ${node.type}`);
    if (node.into || node.for || node.locking || node.withRecursive) throw new Error('Writes, locks and recursive statements are not allowed.');
    if (node.type === 'with') {
      const names = new Set(ctes);
      for (const binding of node.bind || []) {
        visit(binding.statement, names);
        names.add(binding.alias.name);
      }
      visit(node.in, names);
      return;
    }
    if (node.type === 'table' && !(!node.name?.schema && ctes.has(node.name?.name))) {
      const identity = `${node.name?.schema}.${node.name?.name}`;
      if (!node.name?.schema || !relations.has(identity)) throw new Error('SQL relation is outside the configured read-only allowlist. Use schema.table names.');
      used.add(identity);
    }
    if (node.type === 'call') {
      const fn = node.function;
      if ((fn?.schema && fn.schema !== 'pg_catalog') || !FUNCTIONS.has(fn?.name?.toLowerCase())) throw new Error('SQL function is not in the safe built-in allowlist.');
    }
    if (node.type === 'cast' && (node.to?.schema && node.to.schema !== 'pg_catalog' || !TYPES.has(node.to?.name?.toLowerCase()))) throw new Error('Only built-in scalar casts are allowed.');
    if (['binary', 'unary'].includes(node.type) && !OPERATORS.has(node.op?.toUpperCase())) throw new Error('SQL operator is not in the safe allowlist.');
    Object.values(node).forEach(n => visit(n, ctes));
  }
  visit(root);
  return [...used];
}

// Check effective privileges, not the account's display name or a prompt promise.
export const PRIVILEGE_CHECK = `SELECT
  EXISTS (SELECT 1 FROM pg_roles r WHERE pg_has_role(current_user,r.oid,'USAGE')
    AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls
      OR r.rolname IN ('pg_write_all_data','pg_write_server_files','pg_execute_server_program'))) AS elevated,
  EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
      AND c.relkind IN ('r','p','v','m','f')
      AND (pg_has_role(current_user,c.relowner,'USAGE')
        OR has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'))) AS writable,
  EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%'
    AND n.nspname <> 'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE')) AS can_create,
  has_database_privilege(current_user,current_database(),'CREATE') AS can_create_schema`;

export class ReadonlySql {
  constructor({ dsn = '', relations = '', Client = pg.Client } = {}) {
    this.dsn = dsn;
    this.relations = new Set(relations.split(',').map(s => s.trim()).filter(Boolean));
    this.Client = Client;
    for (const relation of this.relations) if (!/^[\p{L}_][\p{L}\p{N}_]*\.[\p{L}_][\p{L}\p{N}_]*$/u.test(relation)) {
      throw new Error('SQL relations must be comma-separated schema.table identifiers.');
    }
  }
  configured() { return Boolean(this.dsn && this.relations.size); }
  async schema() {
    return this.execute(`SELECT pg_catalog.current_database() AS database_name,
      n.nspname AS schema_name, c.relname AS relation_name,
      a.attname AS column_name, pg_catalog.format_type(a.atttypid,a.atttypmod) AS data_type,
      a.attnotnull AS not_null FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
      WHERE (n.nspname || '.' || c.relname) = ANY($1::text[])
      AND c.relkind IN ('r','p','v','m','f') AND a.attnum > 0 AND NOT a.attisdropped
      AND pg_catalog.has_table_privilege(current_user,c.oid,'SELECT')
      ORDER BY n.nspname,c.relname,a.attnum`, [[...this.relations]], 2000, [...this.relations]);
  }
  async query(sql, params = [], limit = 1000) {
    if (!this.configured()) throw new Error('Read-only SQL is not configured. Set METRONOME_READONLY_DSN and METRONOME_SQL_RELATIONS; upload credentials are never used.');
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 2000) throw new Error('Row limit must be between 1 and 2,000.');
    if (!Array.isArray(params) || params.length > 100 || params.some(p => p !== null && !['string', 'number', 'boolean'].includes(typeof p))) throw new Error('SQL parameters must be scalar values.');
    const used = validateReadQuery(sql, this.relations);
    return this.execute(sql, params, limit, used);
  }
  async execute(sql, params, limit, used) {
    if (!this.configured()) throw new Error('Read-only SQL is not configured. Set METRONOME_READONLY_DSN and METRONOME_SQL_RELATIONS; upload credentials are never used.');
    const config = readerConnection(this.dsn);
    const client = new this.Client(config);
    try {
      await client.connect();
      await client.query('BEGIN READ ONLY');
      await client.query("SET LOCAL search_path = pg_catalog");
      await client.query("SET LOCAL statement_timeout = '30s'");
      await client.query("SET LOCAL lock_timeout = '2s'");
      const privileges = (await client.query(PRIVILEGE_CHECK)).rows[0];
      if (!privileges || ['elevated', 'writable', 'can_create', 'can_create_schema'].some(k => privileges[k] !== false)) {
        throw new Error('The configured SQL account has write, ownership or elevated privileges. Use a dedicated SELECT-only account; no analysis query was run.');
      }
      const trimmed = sql.trim().replace(/;\s*$/, '');
      const result = await client.query(`SELECT * FROM (${trimmed}\n) AS metronome_read LIMIT ${limit + 1}`, params);
      const rows = result.rows.slice(0, limit);
      if (Buffer.byteLength(JSON.stringify(rows)) > 4 * 1024 * 1024) throw new Error('SQL result exceeds 4 MiB. Use an aggregate or a smaller projection.');
      return { columns: result.fields.map(f => f.name), rows, returned_rows: rows.length, truncated: result.rows.length > limit,
        relations: used, read_only: true, checked_at: new Date().toISOString(),
        note: 'A row limit is not a completeness check. Use counts and grouped aggregates to validate the entire source.' };
    } catch (error) {
      if (error.message.startsWith('The configured SQL account') || error.message.startsWith('SQL result exceeds')) throw error;
      throw new Error('Read-only SQL failed or timed out. Check the reader connection, relation grants and query; no upload-account fallback was attempted.');
    } finally {
      try { await client.query('ROLLBACK'); } catch { /* A failed connection has nothing to roll back. */ }
      try { await client.end(); } catch { /* Do not expose connection details in cleanup errors. */ }
    }
  }
}
