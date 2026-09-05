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

// Run the actual folder click handlers: replacing the destination must unlock
// parallelism immediately without rebuilding or discarding the user's draft.
const controls = {};
const setup = headed => {
    controls['#flow-download-parallelism'] = {value:'1', disabled:true, dataset:{unmanaged:'true'}};
    controls['#flow-download-parallelism-help'] = {textContent:''};
    controls['#flow-browser-mode'] = {value:headed ? 'headed' : 'headless'};
    controls['#flow-name'] = {value:'Unsaved draft name'};
    controls['#flow-adopt-folder'] = {dataset:{id:'7'}, disabled:false};
    delete controls['#flow-repair-layout'];
    controls['#flow-destination'] = {set outerHTML(html) {
        assert.match(html, /Repair folder layout/);
        delete controls['#flow-adopt-folder'];
        controls['#flow-repair-layout'] = {dataset:{id:'7'}, disabled:false};
    }};
};
const requests = [];
const messages = [];
const folder = {$: id => controls[id], esc: value => String(value ?? ''), toast: message => messages.push(message),
    apiPost: async url => { requests.push(url); return {id:7, flow_folder:'/flows/Example', target_folder:'/flows/Example/Downloads'}; }};
vm.createContext(folder);
vm.runInContext(source.slice(source.indexOf('function _flowDestinationHtml('), source.indexOf('function _flowOutlookBuilderHtml(')), folder);
for (const headed of [false,true]) {
    setup(headed);
    const state = {flows:[{id:7, target_folder:'/old'}]};
    folder._flowSyncParallelism();
    assert.match(controls['#flow-download-parallelism-help'].textContent, /Where it goes.*Adopt managed folder/);
    if (headed) assert.match(controls['#flow-download-parallelism-help'].textContent, /visible slots/);
    folder._flowBindFolderActions(state);
    const adopt = controls['#flow-adopt-folder'];
    await adopt.onclick({currentTarget:adopt});
    assert.equal(controls['#flow-download-parallelism'].disabled, false);
    assert.equal(controls['#flow-name'].value, 'Unsaved draft name');
    assert.equal(state.flows[0].flow_folder, '/flows/Example');
    assert.doesNotMatch(controls['#flow-download-parallelism-help'].textContent, /Adopt managed folder/);
    const repair = controls['#flow-repair-layout'];
    assert.equal(typeof repair.onclick, 'function');
    await repair.onclick({currentTarget:repair});
    assert.equal(repair.disabled, false);
    controls['#flow-browser-mode'].value = 'headless';
    folder._flowSyncParallelism();
    assert.equal(controls['#flow-download-parallelism'].disabled, false);
    controls['#flow-download-parallelism'].value = '4';
    folder._flowSyncParallelism();
    assert.equal(controls['#flow-download-parallelism'].value, '4');
    controls['#flow-browser-mode'].value = 'headed';
    folder._flowSyncParallelism();
    assert.equal(controls['#flow-download-parallelism'].value, '4');
    assert.equal(controls['#flow-download-parallelism'].disabled, false);
}
assert.deepEqual(requests, ['/api/flows/7/adopt-folder','/api/flows/7/repair-layout','/api/flows/7/adopt-folder','/api/flows/7/repair-layout']);
setup(false);
folder.apiPost = async () => {throw new Error('Flow is active');};
folder._flowBindFolderActions({flows:[{id:7}]});
const failedAdopt = controls['#flow-adopt-folder'];
await failedAdopt.onclick({currentTarget:failedAdopt});
assert.equal(controls['#flow-download-parallelism'].disabled, true);
assert.equal(failedAdopt.disabled, false);
assert.match(messages.at(-1), /Flow is active/);
console.log('Parallel download availability and adoption tests passed');
