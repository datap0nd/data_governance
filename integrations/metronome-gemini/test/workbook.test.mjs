import test from 'node:test';
import assert from 'node:assert/strict';
import ExcelJS from 'exceljs';
import { readFile } from 'node:fs/promises';

test('bundled standard library preserves Korean multi-table structure and cached formula distinction', async () => {
  const source = new ExcelJS.Workbook();
  const sheet = source.addWorksheet('출장 보고');
  sheet.mergeCells('A1:C1');
  sheet.getCell('A1').value = '출장비 — 가상 자료';
  sheet.getRow(3).values = ['법인', '기간', '금액'];
  sheet.getRow(4).values = ['가상 A', new Date('2026-09-01T00:00:00Z'), 120];
  sheet.getRow(5).values = ['가상 B', new Date('2026-09-01T00:00:00Z'), null];
  sheet.getCell('C6').value = { formula: 'SUM(C4:C5)', result: 120 };
  sheet.getCell('C7').value = { formula: 'SUM(C4:C5)' };
  sheet.getRow(10).values = ['법인', '인원'];
  sheet.getRow(11).values = ['가상 A', 3];
  sheet.getRow(12).values = ['가상 C', 1];
  sheet.getRow(12).hidden = true;
  sheet.addConditionalFormatting({ ref: 'C4:C5', rules: [{ type: 'dataBar', minLength: 0, maxLength: 100,
    cfvo: [{ type: 'min' }, { type: 'max' }], color: { argb: 'FF007777' } }] });
  const bytes = await source.xlsx.writeBuffer();
  const reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(bytes);
  const result = reopened.getWorksheet('출장 보고');
  assert.equal(result.getCell('B1').master.address, 'A1');
  assert.equal(result.getCell('A4').value, '가상 A');
  assert.equal(result.getCell('C5').value, null);
  assert.deepEqual(result.getCell('C6').value, { formula: 'SUM(C4:C5)', result: 120 });
  assert.equal(result.getCell('C7').value.result, undefined);
  assert.equal(result.getCell('A10').value, '법인');
  assert.equal(result.getRow(12).hidden, true);
  assert.equal(result.getCell('B4').value.toISOString(), '2026-09-01T00:00:00.000Z');
});
test('extension entry points and browser allowlist resolve to packaged files', async () => {
  const base = new URL('../', import.meta.url);
  const manifest = JSON.parse(await readFile(new URL('gemini-extension.json', base), 'utf8'));
  assert.equal(manifest.name, 'metronome-gemini');
  assert.equal(manifest.mcpServers.metronome.command, 'node');
  for (const entry of ['server.mjs', 'commands/metronome.toml', 'commands/html_replicate.toml',
    'skills/metronome/SKILL.md', 'skills/html-replicate/SKILL.md', manifest.contextFileName]) {
    assert.ok((await readFile(new URL(entry, base), 'utf8')).length > 0);
  }
  const browser = manifest.mcpServers['report-browser'];
  assert.deepEqual(browser.args, ['${extensionPath}/browser.mjs']);
  assert.match(await readFile(new URL('browser.mjs', base), 'utf8'), /browser.newContext/);
  assert.ok(!browser.includeTools.some(t => /evaluate|run_code|upload|network/.test(t)));
});
