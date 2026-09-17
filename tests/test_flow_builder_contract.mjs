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
// Arguments and values travel with their row: the blank-path row's entries are dropped with it.
const pythonArgumentRows = [{value:' -sheet {value} '}, {value:'--dropped-with-its-row'}, {value:''}];
const pythonValueRows = [{value:'T\r\nU\n\n  V  \n'}, {value:'dropped'}, {value:''}];
const baseQuerySelectorAll = context.document.querySelectorAll;
context.document.querySelectorAll = selector => selector.includes('flow-python-script-path') ? pythonRows : selector.includes('flow-python-script-arguments') ? pythonArgumentRows : selector.includes('flow-python-script-values') ? pythonValueRows : baseQuerySelectorAll(selector);
const helpersStart = source.indexOf('function _flowPythonValuesList');
vm.runInContext(source.slice(helpersStart, source.indexOf('function _flowPythonBuilderHtml', helpersStart)), context);
form.dataset.sourceType = 'python';
set('flow-file-format', 'xlsx'); set('flow-filename', ' {flow}_{date}.xlsx '); set('flow-output-mode', 'direct_replace');
set('flow-sql-enabled', '', {checked:false});
body = context._flowCollectBuilder();
assert.equal(body.source_type, 'python');
assert.deepEqual(plain(body.python_scripts), ['C:\\scripts\\fetch_orders.py', 'C:\\scripts\\clean_orders.py']);
assert.deepEqual(plain(body.python_script_arguments), ['-sheet {value}', '']);
assert.deepEqual(plain(body.python_script_values), [['T', 'U', 'V'], []]);
assert.equal(body.python_script_arguments.length, body.python_scripts.length);
assert.equal(body.python_script_values.length, body.python_scripts.length);
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
assert.deepEqual(plain(body.python_script_arguments), ['-sheet {value}', '']);
assert.deepEqual(plain(body.python_script_values), [['T', 'U', 'V'], []]);
// SQL keeps the final CSV in the run folder: the hidden file-output mode never publishes it.
assert.equal(controls['#flow-output-mode'].value, 'direct_replace');
assert.equal(body.output_mode, 'run_folders');
set('flow-filename', '');
assert.equal(context._flowCollectBuilder().filename_template, '{flow}.csv');
set('flow-sql-enabled', '', {checked:false}); set('flow-file-format', 'csv');
assert.equal(context._flowCollectBuilder().filename_template, '{flow}.csv');
// Rows without an Arguments or Values control still send aligned entries.
context.document.querySelectorAll = selector => selector.includes('flow-python-script-path') ? pythonRows : baseQuerySelectorAll(selector);
body = context._flowCollectBuilder();
assert.deepEqual(plain(body.python_script_arguments), ['', '']);
assert.deepEqual(plain(body.python_script_values), [[], []]);
context.document.querySelectorAll = baseQuerySelectorAll;
assert.match(source, /id="flow-source-python"/);
assert.match(source, /data-source-type="python"/);
assert.match(source, /id="flow-sql-enabled" type="checkbox" hidden/);
assert.match(source, /name="flow-python-output" id="flow-python-output-sql"/);
assert.match(source, /python_scripts: "flow-python-script-1"/);
assert.match(source, /"Run queued. The worker will run the Python scripts in order."/);
console.log('flow builder python payload tests passed');

// Per-script arguments and values: the three-line row markup, renumbered ids, the list label,
// the live run count and the server-error focus for both fields.
context.esc = value => value == null ? '' : String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const row = context._flowPythonScriptRowHtml(2, 'C:\\scripts\\clean.py', false, '-sheet "Q 1"', ['T', 'U', 'V']);
assert.match(row, /<input class="flow-python-script-path" id="flow-python-script-2" maxlength="2000" required value="C:\\scripts\\clean\.py" [^>]*aria-label="Script 2 path">/);
assert.match(row, /<label class="flow-python-row-label flow-python-arguments-label" for="flow-python-arguments-2">Arguments <span class="flow-python-row-hint">\(optional\)<\/span><\/label>/);
assert.match(row, /<input class="flow-python-script-arguments" id="flow-python-arguments-2" maxlength="2000" value="-sheet &quot;Q 1&quot;" placeholder="-sheet T" aria-label="Script 2 arguments">/);
assert.match(row, /<label class="flow-python-row-label flow-python-values-label" for="flow-python-values-2">Values, one per run <span class="flow-python-row-hint">\(optional\)<\/span><\/label>/);
assert.match(row, /<textarea class="flow-python-script-values" id="flow-python-values-2" rows="2" [^>]*aria-label="Script 2 values">T\nU\nV<\/textarea><span class="flow-python-values-count" aria-live="polite">3 runs<\/span>/);
assert.match(row, /Paste one value per line; the script runs once per value\. The value replaces \{value\} in Arguments, or is added after them\./);
const emptyRow = context._flowPythonScriptRowHtml(1);
assert.match(emptyRow, /id="flow-python-arguments-1" maxlength="2000" value="" placeholder="-sheet T" aria-label="Script 1 arguments"/);
assert.match(emptyRow, /id="flow-python-values-1" rows="2" [^>]*aria-label="Script 1 values"><\/textarea><span class="flow-python-values-count" aria-live="polite">1 run<\/span>/);
assert.deepEqual(plain(context._flowPythonValuesList(' T \r\n\nU\n  \nV\nV\n')), ['T', 'U', 'V', 'V']);
assert.deepEqual(plain(context._flowPythonValuesList(undefined)), []);
assert.equal(context._flowPythonRunCountLabel([]), '1 run');
assert.equal(context._flowPythonRunCountLabel(['T']), '1 run');
assert.equal(context._flowPythonRunCountLabel(['T', 'U', 'V']), '3 runs');
const namesStart = source.indexOf('function _flowPythonScriptNames');
vm.runInContext(source.slice(namesStart, source.indexOf('\nfunction ', namesStart + 1)), context);
assert.deepEqual(plain(context._flowPythonScriptNames({python_scripts:['C:\\s\\run_download.py', '/srv/clean.py'], python_script_arguments:[' -sheet T ', '']})), ['run_download.py -sheet T', 'clean.py']);
assert.deepEqual(plain(context._flowPythonScriptNames({python_scripts:['C:\\s\\run_download.py', '/srv/clean.py'], python_script_arguments:['-sheet', ''], python_script_values:[['T', 'U', 'V'], []]})), ['run_download.py -sheet (3 values)', 'clean.py']);
assert.deepEqual(plain(context._flowPythonScriptNames({python_scripts:['C:\\s\\a.py'], python_script_values:[['only']]})), ['a.py (1 value)']);
assert.deepEqual(plain(context._flowPythonScriptNames({python_scripts:['C:\\s\\run_download.py']})), ['run_download.py']);
assert.deepEqual(plain(context._flowPythonScriptNames({python_scripts:['C:\\s\\a.py'], python_script_arguments:['-x', '-y'], python_script_values:[[], ['z']]})), ['a.py -x']);
for (const [field, id] of [['python_script_arguments', 'flow-python-arguments-1'], ['python_script_values', 'flow-python-values-1']]) {
    const focusSeen = [];
    context._flowRevealStep = (_form, input) => focusSeen.push(input);
    const target = {focus: () => focusSeen.push('focus')};
    context._flowRevealServerError({querySelector: selector => selector === `#${id}` ? target : null}, {validation: [{loc: ['body', field], msg: 'Script arguments have an unclosed quote.'}]});
    assert.deepEqual(focusSeen, [target, 'focus'], field);
}
assert.match(source, /python_script_arguments: "flow-python-arguments-1", python_script_values: "flow-python-values-1"/);
assert.match(source, /argumentsInput\.id = `flow-python-arguments-\$\{index \+ 1\}`;\n\s+argumentsInput\.setAttribute\("aria-label", `Script \$\{index \+ 1\} arguments`\)/);
assert.match(source, /valuesInput\.id = `flow-python-values-\$\{index \+ 1\}`;\n\s+valuesInput\.setAttribute\("aria-label", `Script \$\{index \+ 1\} values`\)/);
assert.match(source, /count\.textContent = _flowPythonRunCountLabel\(_flowPythonValuesList\(textarea\.value\)\)/);
assert.match(source, /Arguments are added to the command exactly as typed, before Metronome's <code>--input<\/code>\/<code>--output<\/code>; use double quotes around a value with spaces; \{flow\}, \{date\} and \{run_id\} are replaced per run\./);
assert.match(source, /scriptArguments\[index\] \|\| "", scriptValues\[index\] \|\| \[\]\)\)\.join\(""\)/);
console.log('flow builder python arguments and values tests passed');
