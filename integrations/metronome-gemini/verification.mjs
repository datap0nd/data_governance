import { readFile, stat, realpath } from 'node:fs/promises';
import { extname, isAbsolute, basename } from 'node:path';
import ExcelJS from 'exceljs';
import { digest } from './client.mjs';
import { createHash } from 'node:crypto';

export const MAX_ROWS = 50000;
const MAX_BYTES = 20 * 1024 * 1024;
export const fileHash = bytes => createHash('sha256').update(bytes).digest('hex');
const quote = name => '"' + name.replaceAll('"', '""') + '"';

function scalar(value) {
  if (value === undefined || value === null) return null;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'object') {
    if ('formula' in value || 'sharedFormula' in value) {
      if (!Object.hasOwn(value, 'result')) throw new Error('A formula has no stored result. Supply a verified values-only reference/export; no value was guessed.');
      return scalar(value.result);
    }
    if (value.richText) return value.richText.map(r => r.text).join('');
    if (value.hyperlink) return value.text;
    throw new Error('Unsupported cell value or Excel error. Resolve it before claiming a match.');
  }
  // Compare exported scalar values, without trimming, rounding or dropping blanks.
  if (!['string', 'number', 'boolean'].includes(typeof value) || typeof value === 'number' && !Number.isFinite(value)) throw new Error('Unsupported scalar value.');
  return String(value);
}

function table(columns, rows, evidence) {
  if (!columns.length || columns.length > 256 || columns.some(c => typeof c !== 'string' || !c.length) || new Set(columns).size !== columns.length) throw new Error('Use one table with explicit, unique column labels; do not flatten merged headers silently.');
  if (rows.length > MAX_ROWS) throw new Error('Verification exceeds 50,000 rows. This scan is incomplete; no match can be certified.');
  const values = rows.map(row => columns.map((_, i) => scalar(row[i])));
  if (Buffer.byteLength(JSON.stringify(values)) > MAX_BYTES) throw new Error('Verification exceeds 20 MiB of values. No complete match was established.');
  return { columns, rows: values, row_count: values.length, complete: true, captured_at: new Date().toISOString(), ...evidence };
}

export async function fileSnapshot(spec) {
  if (!isAbsolute(spec.path || '') || !['.xlsx', '.csv'].includes(extname(spec.path).toLowerCase()) || /governance\.db/i.test(basename(spec.path))) throw new Error('Select an absolute .xlsx or .csv evidence file. Application databases and scripts are not evidence inputs.');
  const path = await realpath(spec.path), info = await stat(path);
  if (!['.xlsx', '.csv'].includes(extname(path).toLowerCase()) || /governance\.db/i.test(basename(path))) throw new Error('The resolved evidence path is not an allowed spreadsheet.');
  if (!info.isFile() || info.size > MAX_BYTES) throw new Error('Evidence must be a completed file no larger than 20 MiB.');
  const bytes = await readFile(path);
  if (bytes.length > MAX_BYTES) throw new Error('Evidence grew beyond the file-size limit; wait for the download to finish.');
  const workbook = new ExcelJS.Workbook();
  let sheet, bounds, formula_cells = 0;
  if (extname(path).toLowerCase() === '.xlsx') {
    if (!spec.sheet || !/^[A-Z]+[1-9][0-9]*:[A-Z]+(?:[1-9][0-9]*|\*)$/.test(spec.range || '')) throw new Error('Specify the worksheet and exact table range, including one header row.');
    await workbook.xlsx.load(bytes);
    sheet = workbook.getWorksheet(spec.sheet);
    if (!sheet) throw new Error('The requested worksheet does not exist. Inspect the reference before choosing it.');
    const [first, last] = spec.range.replace('*', String(sheet.rowCount)).split(':').map(address => sheet.getCell(address));
    bounds = [first.row, last.row, first.col, last.col];
  } else {
    // The same bytes are hashed and parsed; do not reopen a changing download.
    const { Readable } = await import('node:stream');
    sheet = await workbook.csv.read(Readable.from([bytes]), { map: value => value });
    bounds = [1, sheet.rowCount, 1, sheet.columnCount];
  }
  const [r1, r2, c1, c2] = bounds;
  if (r2 < r1 || c2 < c1 || r2 - r1 > MAX_ROWS || c2 - c1 >= 256) throw new Error('Invalid or incomplete table range. Maximum: 50,000 data rows and 256 columns.');
  const raw = [];
  for (let r = r1; r <= r2; r++) {
    const row = [];
    for (let c = c1; c <= c2; c++) {
      const value = sheet.getCell(r, c).value;
      if (value && typeof value === 'object' && ('formula' in value || 'sharedFormula' in value)) formula_cells++;
      row.push(value);
    }
    raw.push(row);
  }
  const columns = raw.shift().map(scalar);
  return table(columns, raw, { kind: 'file', path, sha256: fileHash(bytes), file_bytes: bytes.length,
    sheet: spec.sheet || null, range: spec.range || null, formula_cells,
    scope_note: 'All rows and columns in the declared table are compared. Stored formula results are compared without recalculation; this is not a freshness guarantee.' });
}

export async function sqlSnapshot(spec, sql) {
  for (const key of ['database', 'schema', 'table']) if (typeof spec[key] !== 'string' || !spec[key].length || Buffer.byteLength(spec[key]) > 63 || spec[key].includes('\0')) throw new Error('SQL evidence requires database, schema and table names.');
  // No model-supplied filters, LIMIT, projections or custom SQL can hide rows.
  const statement = `SELECT * FROM ${quote(spec.schema)}.${quote(spec.table)}`;
  const result = await sql.snapshot(statement, spec.database);
  if (result.truncated) throw new Error('SQL evidence exceeds the bounded full-table scan. No complete match was established.');
  return table(result.columns, result.rows.map(row => result.columns.map(c => row[c])), {
    kind: 'sql', database: spec.database, schema: spec.schema, table: spec.table,
    scope_note: 'Full visible table/view under the dedicated read-only account, including its row-level security. No model-supplied filter or projection.' });
}

export async function snapshot(spec, sql) {
  if (spec.kind === 'file') return fileSnapshot(spec);
  if (spec.kind === 'sql') return sqlSnapshot(spec, sql);
  throw new Error('Evidence must be a file or a read-only SQL table.');
}

export function compare(reference, actual) {
  if (!reference.complete || !actual.complete) throw new Error('Incomplete evidence cannot produce a match.');
  const columns_match = JSON.stringify(reference.columns) === JSON.stringify(actual.columns);
  const bag = rows => {
    const counts = new Map();
    for (const row of rows) { const key = JSON.stringify(row); counts.set(key, (counts.get(key) || 0) + 1); }
    return counts;
  };
  const left = bag(reference.rows), right = bag(actual.rows);
  let missing = 0, extra = 0;
  const differences = [];
  for (const key of new Set([...left.keys(), ...right.keys()])) {
    const delta = (right.get(key) || 0) - (left.get(key) || 0);
    if (delta < 0) missing -= delta; else extra += delta;
    if (delta) differences.push({ row: JSON.parse(key), reference_count: left.get(key) || 0, actual_count: right.get(key) || 0 });
  }
  const blanks = evidence => Object.fromEntries(evidence.columns.map((c, i) => [c,
    evidence.rows.reduce((n, row) => n + (row[i] === null || /^\s*$/.test(row[i]) ? 1 : 0), 0)]));
  return { verdict: columns_match && !missing && !extra ? 'EXACT_MATCH' : 'DATA_DIFFERENCES',
    columns_match, reference_columns: reference.columns, actual_columns: actual.columns,
    reference_rows: reference.row_count, actual_rows: actual.row_count, missing_rows: missing, extra_rows: extra,
    reference_blanks: blanks(reference), actual_blanks: blanks(actual), differences,
    note: 'Duplicate multiplicities and blanks are preserved; row order is ignored. A changed row appears as one missing and one extra row. Refresh is not assumed.' };
}

export function evidenceSummary(value) {
  const { rows, ...summary } = value;
  return { ...summary, content_fingerprint: digest({ columns: value.columns, rows }) };
}
