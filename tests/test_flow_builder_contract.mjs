import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const controls = {};
const set = (id, value, rest = {}) => controls[`#${id}`] = {value, dataset: {}, ...rest};
for (const [id, value] of Object.entries({name:' Draft ', 'schedule-type':'manual', 'schedule-time':'09:15', 'schedule-day':'2', 'owner':'', 'sql-mode':'append', 'sql-database':'CaseDB', 'sql-schema':'CaseSchema', 'sql-table':'MyTable', 'local-file-path':' C:\\input.xlsx ', 'local-file-worksheet':' Exact Sheet ', 'outlook-subject':' Daily report ', site:'7', report:'9', 'period-strategy':'none', 'download-mode':'single', 'file-format':'csv_file_format', 'browser-mode':'headless', 'excel-trim':'none', filename:'{flow}_{export}.csv', 'start-week':'2026-W01', 'end-week':'2026-W05'})) set(`flow-${id}`,value);
set('flow-sql-enabled','',{checked:true}); set('flow-transform-enabled','',{checked:false}); set('flow-sql-uppercase','',{checked:true});
set('flow-excel-enabled', '', {checked:true});
set('flow-excel-names', ' Exact Sheet ');
controls['input[name="flow-excel-mode"]:checked'] = {value:'single'};
set('flow-export-report-title','',{checked:true,dataset:{inherit:'true'}}); set('flow-export-filter-details','',{checked:false,disabled:true});
const form = {dataset:{sourceType:'file',id:''}};
controls['#flow-builder-form'] = form;
const context = {$: id => controls[id], window:{_flowsState:{flows:[],catalog:{asap_download_types:[{key:'csv_file_format',file_format:'csv'}]}}}, document:{querySelectorAll: selector => selector.includes('export-view') ? [{value:'View A'}, {value:'View B'}] : []}, _flowSiteIsAsap: () => true};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function _flowCollectBuilder'),source.indexOf('function _pipelineDuration')),context);
let body = context._flowCollectBuilder();
assert.equal(body.name,'Draft'); assert.equal(body.local_file_worksheet,' Exact Sheet ');
assert.equal(body.target_folder,null); assert.equal(body.output_mode,'private_snapshot');
assert.equal(body.owner_person_id,null); assert.equal(body.enabled,false);
assert.equal(body.sql_database,'CaseDB'); assert.equal(body.sql_table,'MyTable'); assert.equal(body.sql_uppercase,true);
set('flow-local-file-path','C:\\input.csv'); set('flow-excel-enabled', '', {checked:true,disabled:true}); assert.equal(context._flowCollectBuilder().local_file_worksheet,null);
assert.equal(context._flowCollectBuilder().excel_worksheets,null);
set('flow-excel-enabled', '', {checked:false});
form.dataset.sourceType = 'outlook'; body=context._flowCollectBuilder();
assert.equal(body.outlook_subject_contains,'Daily report'); assert.equal(body.filename_template,null); // server fixes original-name semantics
form.dataset.sourceType = 'portal'; body=context._flowCollectBuilder();
assert.deepEqual(Array.from(body.export_views),['View A','View B']);
assert.equal(body.export_report_title,null); assert.equal(body.export_filter_details,null);
assert.equal(body.period_strategy,'none'); assert.equal(body.start_week,null); assert.equal(body.end_week,null);
assert.equal(body.asap_download_type,'csv_file_format'); assert.equal(body.sql_schema,'CaseSchema');
set('flow-browser-mode', 'headed'); set('flow-download-parallelism', '3');
body = context._flowCollectBuilder();
assert.equal(body.browser_mode, 'headed'); assert.equal(body.download_parallelism, 3);
vm.runInContext(source.slice(source.indexOf('function _flowStepSummary'), source.indexOf('function _flowBuildSteps')),context);
const seen=[];
context._flowRevealStep = (_form,input) => seen.push(input);
const input={focus:()=>seen.push('focus')};
context._flowRevealServerError({querySelector:id=>id==='#flow-excel-names'?input:null}, {validation:[{loc:['body','local_file_worksheet'],msg:'Exact worksheet required'}]});
assert.deepEqual(seen,[input,'focus']);
assert.match(source,/queueMicrotask\(\(\) => \{ _flowRevealStep\(form, target\); target.focus\(\)/);
assert.match(source,/next.type = "button"/);
console.log('flow builder payload and error tests passed');

// Switching to detected controls must not submit a pending recorded revision.
form.dataset.id = '7';
context.window._flowRecordingSelections = new Map([[7, 42]]);
set('flow-execution-method', 'recorded');
assert.equal(context._flowCollectBuilder().recording_revision_id, 42);
set('flow-execution-method', 'catalog');
assert.equal(context._flowCollectBuilder().recording_revision_id, undefined);

// Email the final file: recipients split on ; , and newlines; disabled sends the Off shape.
const plain = value => JSON.parse(JSON.stringify(value));
set('flow-email-enabled', '', {checked: true});
set('flow-email-recipients', 'a@x.test; B@x.test,\nc@x.test ');
set('flow-email-subject', ' Weekly file ');
assert.deepEqual(plain(context._flowEmailRead()), {enabled: true, recipients: ['a@x.test', 'B@x.test', 'c@x.test'], subject: 'Weekly file'});
assert.deepEqual(plain(context._flowCollectBuilder().email_delivery), {enabled: true, recipients: ['a@x.test', 'B@x.test', 'c@x.test'], subject: 'Weekly file'});
set('flow-email-subject', '');
assert.equal(context._flowEmailRead().subject, null);
set('flow-email-enabled', '', {checked: false});
assert.deepEqual(plain(context._flowCollectBuilder().email_delivery), {enabled: false, recipients: [], subject: null});
for (const sourceType of ['file', 'outlook', 'portal']) {
    form.dataset.sourceType = sourceType;
    assert.deepEqual(plain(context._flowCollectBuilder().email_delivery), {enabled: false, recipients: [], subject: null});
}
const emailSeen = [];
context._flowRevealStep = (_form, input) => emailSeen.push(input);
const recipientsInput = {focus: () => emailSeen.push('focus')};
context._flowRevealServerError({querySelector: id => id === '#flow-email-recipients' ? recipientsInput : null}, {validation: [{loc: ['body', 'email_delivery'], msg: 'Invalid email address: planner'}]});
assert.deepEqual(emailSeen, [recipientsInput, 'focus']);
assert.match(source, /<h3>|emailTitle\.textContent = "Email the final file"/);
assert.match(source, /id="flow-email-enabled" type="checkbox"/);
console.log('flow builder email step tests passed');

// Every source freezes the exact ordered names and explicitly restores the default.
set('flow-excel-enabled', '', {checked:true});
set('flow-excel-names', ' North \nSouth\n');
controls['input[name="flow-excel-mode"]:checked'] = {value:'append'};
for (const sourceType of ['file', 'outlook', 'portal']) {
    form.dataset.sourceType = sourceType;
    assert.deepEqual(plain(context._flowCollectBuilder().excel_worksheets), {mode:'append', names:[' North ', 'South']});
}
set('flow-excel-enabled', '', {checked:false});
assert.equal(context._flowCollectBuilder().excel_worksheets, null);
console.log('flow builder worksheet payload tests passed');

// Python scripts: ordered absolute paths from the script rows (blank rows dropped),
// the Output radio drives the hidden #flow-sql-enabled checkbox, and SQL forces CSV.
const pythonRows = [{value:' C:\\scripts\\fetch_orders.py '}, {value:'   '}, {value:'C:\\scripts\\clean_orders.py'}];
const baseQuerySelectorAll = context.document.querySelectorAll;
context.document.querySelectorAll = selector => selector.includes('flow-python-script-path') ? pythonRows : baseQuerySelectorAll(selector);
form.dataset.sourceType = 'python';
set('flow-file-format', 'xlsx'); set('flow-filename', ' {flow}_{date}.xlsx '); set('flow-output-mode', 'direct_replace');
set('flow-sql-enabled', '', {checked:false});
body = context._flowCollectBuilder();
assert.equal(body.source_type, 'python');
assert.deepEqual(plain(body.python_scripts), ['C:\\scripts\\fetch_orders.py', 'C:\\scripts\\clean_orders.py']);
assert.equal(body.sql_handoff_enabled, false); assert.equal(body.sql_table, null);
assert.equal(body.file_format, 'xlsx'); assert.equal(body.filename_template, '{flow}_{date}.xlsx');
assert.equal(body.output_mode, 'direct_replace');
assert.equal(body.transform_enabled, false); assert.equal(body.transform_script_path, null);
assert.equal(body.browser_mode, 'headless'); assert.equal(body.target_folder, null); assert.equal(body.local_file_path, null);
assert.equal(body.outlook_subject_contains, null); assert.equal(body.site_id, null); assert.equal(body.excel_worksheets, null);
set('flow-sql-enabled', '', {checked:true});
body = context._flowCollectBuilder();
assert.equal(body.sql_handoff_enabled, true); assert.equal(body.sql_table, 'MyTable'); assert.equal(body.sql_schema, 'CaseSchema');
assert.equal(body.file_format, 'csv'); assert.equal(body.filename_template, '{flow}_{date}.csv');
set('flow-filename', '');
assert.equal(context._flowCollectBuilder().filename_template, '{flow}.csv');
set('flow-sql-enabled', '', {checked:false}); set('flow-file-format', 'csv');
assert.equal(context._flowCollectBuilder().filename_template, '{flow}.csv');
context.document.querySelectorAll = baseQuerySelectorAll;
assert.match(source, /id="flow-source-python"/);
assert.match(source, /data-source-type="python"/);
assert.match(source, /id="flow-sql-enabled" type="checkbox" hidden/);
assert.match(source, /name="flow-python-output" id="flow-python-output-sql"/);
assert.match(source, /python_scripts: "flow-python-script-1"/);
assert.match(source, /"Run queued. The worker will run the Python scripts in order."/);
console.log('flow builder python payload tests passed');
