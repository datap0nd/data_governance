import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import ExcelJS from 'exceljs';
import { compare, snapshot, evidenceSummary, MAX_ROWS } from '../verification.mjs';

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'gemini-verification-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const workbook = new ExcelJS.Workbook(), sheet = workbook.addWorksheet('출장비');
  sheet.addRows([['법인', '금액', '코드'], ['가상 A', 120, '001'], ['가상 B', null, '002'], ['가상 A', 120, '001']]);
  const path = join(root, 'reference.xlsx'); await workbook.xlsx.writeFile(path);
  return { root, workbook, sheet, path, spec: { kind: 'file', path, sheet: '출장비', range: 'A1:C4' } };
}

test('verification reads actual Korean workbook and CSV values, preserving IDs and duplicate multiplicity', async t => {
  const f = await fixture(t), reference = await snapshot(f.spec);
  const path = join(f.root, 'download.csv');
  await writeFile(path, '\ufeff법인,금액,코드\r\n가상 A,120,001\r\n가상 B,,002\r\n가상 A,120,001\r\n');
  const actual = await snapshot({ kind: 'file', path });
  assert.equal(actual.rows[0][2], '001');
  const check = compare(reference, actual);
  assert.equal(check.verdict, 'DATA_DIFFERENCES');
  assert.equal(check.missing_rows, 1); assert.equal(check.extra_rows, 1);
  assert.equal(evidenceSummary(reference).rows, undefined);
  assert.equal(evidenceSummary(reference).sha256.length, 64);
  const reordered = { ...reference, rows: [...reference.rows].reverse() };
  assert.equal(compare(reference, reordered).verdict, 'EXACT_MATCH');
  const missingDuplicate = { ...reference, rows: reference.rows.slice(0, 2), row_count: 2 };
  assert.equal(compare(reference, missingDuplicate).missing_rows, 1);
  f.sheet.addRow(['가상 C', 50, '003']); await f.workbook.xlsx.writeFile(f.path);
  assert.equal(compare(reference, await snapshot({ ...f.spec, range: 'A1:C*' })).extra_rows, 1);
});

test('verification refuses guessed table bounds, merged headings and missing formula results', async t => {
  const f = await fixture(t);
  await assert.rejects(snapshot({ ...f.spec, range: undefined }), /exact table range/);
  await assert.rejects(snapshot({ kind: 'file', path: join(f.root, 'governance.db') }), /Application databases/);
  f.sheet.getCell('B2').value = { formula: '100+20' }; await f.workbook.xlsx.writeFile(f.path);
  await assert.rejects(snapshot(f.spec), /no stored result/);
  f.sheet.getCell('B2').value = { formula: '100+20', result: 120 }; await f.workbook.xlsx.writeFile(f.path);
  const result = await snapshot(f.spec);
  assert.equal(result.formula_cells, 1); assert.match(result.scope_note, /without recalculation/);
  f.sheet.getCell('B1').value = '법인'; await f.workbook.xlsx.writeFile(f.path);
  await assert.rejects(snapshot(f.spec), /unique column labels/);
});

test('SQL verification cannot accept arbitrary SQL, partial scans or altered column sets', async () => {
  let called;
  const sql = { snapshot: async (...args) => {
    called = args; return { columns: ['법인'], rows: [{ '법인': '가상 A' }], truncated: false };
  } };
  const spec = { kind: 'sql', database: 'fixture', schema: '보고', table: '출장' };
  const reference = await snapshot(spec, sql);
  assert.deepEqual(called, ['SELECT * FROM "보고"."출장"', 'fixture']);
  assert.equal(reference.row_count, 1);
  assert.equal(compare(reference, { ...reference, columns: ['Other'] }).verdict, 'DATA_DIFFERENCES');
  await assert.rejects(snapshot(spec, { snapshot: async () => ({ truncated: true }) }), /bounded full-table/);
  assert.throws(() => compare(reference, { ...reference, complete: false }), /Incomplete/);
  assert.equal(MAX_ROWS, 50000);
});
