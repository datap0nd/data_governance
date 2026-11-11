// F-14: a Power BI alert whose export filled the row limit says so in the
// run history and in the builder preview, using the limit the server reports.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const esc = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const runsByRecurrence = {};
const context = {
    esc, Intl, Number, Math,
    parseAppTimestamp: value => new Date(value),
    _recRequest: async path => runsByRecurrence[path],
};
vm.createContext(context);
vm.runInContext(slice('function _recDateTime(', 'function _recSourceText('), context);
vm.runInContext(slice('function _recPreviewTable(', 'function _recStepThree('), context);
vm.runInContext(slice('async function _recLoadHistory(', 'function bindRecurrencesPage('), context);

// The flag alone names the real limit; a stored warning becomes the tooltip.
assert.equal(context._recExportLimitHtml({}, 12), '');
assert.equal(context._recExportLimitHtml(null, 12), '');
assert.equal(context._recExportLimitHtml({export_limit_reached: false, export_row_limit: 500}, 500), '');
const chip = context._recExportLimitHtml({
    export_limit_reached: true, export_row_limit: 1500,
    warning: 'The table was cut off at the Power BI export limit of 1,500 rows. "Only" the first 1,500 rows were exported.',
}, 1500);
assert.match(chip, /class="rec-status warning"/);
assert.match(chip, />Table cut off at 1,500 rows</);
assert.match(chip, /title="The table was cut off at the Power BI export limit of 1,500 rows\. &quot;Only&quot;/);
// A run stored before the limit was recorded still shows the rows it stopped at.
assert.match(context._recExportLimitHtml({export_limit_reached: true}, 30000), />Table cut off at 30,000 rows</);

// Run history: the cut-off run carries the warning beside its status.
runsByRecurrence['/api/recurrences/7/runs'] = [
    {status: 'sent', trigger_type: 'scheduled', started_at: '2026-11-10T04:00:00', exported_rows: 1500,
     matched_rows: 12, email_count: 2, error: null,
     detail: {export_limit_reached: true, export_row_limit: 1500, warning: 'The table was cut off at the Power BI export limit of 1,500 rows.'}},
    {status: 'sent', trigger_type: 'manual', started_at: '2026-11-09T04:00:00', exported_rows: 40,
     matched_rows: 3, email_count: 1, error: null,
     detail: {export_limit_reached: false, export_row_limit: 1500, warning: null}},
];
const cell = {innerHTML: ''};
await context._recLoadHistory(7, {querySelector: () => cell});
const rows = cell.innerHTML.split('<tr>').slice(2);
assert.equal(rows.length, 2);
assert.match(rows[0], /rec-status success">sent<\/span> <span class="rec-status warning"[^>]*>Table cut off at 1,500 rows</);
assert.doesNotMatch(rows[1], /cut off/);

// Builder preview: the real limit, not a hardcoded 30,000.
const preview = context._recPreviewTable({
    columns: ['Subsidiary'], rows: [{Subsidiary: 'North'}], row_count: 1500,
    export_limit_reached: true, export_row_limit: 1500,
});
assert.match(preview, />Table cut off at 1,500 rows</);
assert.doesNotMatch(preview, /30,000/);
assert.doesNotMatch(context._recPreviewTable({
    columns: ['Subsidiary'], rows: [{Subsidiary: 'North'}], row_count: 1,
    export_limit_reached: false, export_row_limit: 1500,
}), /cut off/);
assert.doesNotMatch(slice('function _recPreviewTable(', 'function _recStepThree('), /30,000/);

console.log('recurrence export limit display tests passed');
