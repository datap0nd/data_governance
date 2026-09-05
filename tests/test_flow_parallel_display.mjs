import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const context = {esc: value => String(value ?? '').replaceAll('<', '&lt;')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function _flowCapacityHtml('), source.indexOf('// ── Router ──')), context);
const capacity = context._flowCapacityHtml({headless_capacity:3, online_capacity:3, active_headless:3,
    slots:[{slot:1, configured:true, status:'busy', current_task_id:9, current_run_id:4}],
    portals:[{id:7, name:'<Portal>', capacity:2}]});
assert.match(capacity, /Export task 9/);
assert.match(capacity, /&lt;Portal>/);
assert.match(capacity, /value="2" selected/);
assert.match(capacity, /Save portal limit/);
const logSource = fs.readFileSync(new URL('../app/static/flow_run_log.js', import.meta.url), 'utf8');
const container = {innerHTML:''};
const log = {location:{pathname:'/flow-runs/4'}, document:{getElementById:id => id === 'flow-run-log' ? container : null}};
vm.createContext(log);
vm.runInContext(logSource.slice(0, logSource.indexOf('if (!Number.isInteger(runId))')), log);
const run = {id:4, flow_name:'Example', status:'failed', job:{sql_handoff:{enabled:true}},
    artifacts:[{file_path:'data.csv', filename:'data.csv'}],
    downloads:{completed:1, total:3, active:1, state:'aborting', tasks:[{ordinal:2, state:'cancelling', worker_id:'<worker>', attempt:1, error:'<timeout>'}]}};
log.render(run);
assert.match(container.innerHTML, /1 of 3 completed · 1 active slots/);
assert.match(container.innerHTML, /&lt;worker&gt;/);
assert.doesNotMatch(container.innerHTML, /id="flow-retry-sql"/);
run.downloads.completed = 3;
log.render(run);
assert.match(container.innerHTML, /id="flow-retry-sql"/);
run.sql_reconciliation_required = true;
log.render(run);
assert.doesNotMatch(container.innerHTML, /id="flow-retry-sql"/);
assert.match(container.innerHTML, /SQL reconciliation required/);
console.log('Parallel download display tests passed');
