import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('function _flowListHtml'), source.indexOf('/** The folder a report sits in'));
const stored = new Map();
const context = {window: {}, esc: v => String(v ?? '').replaceAll('<','&lt;').replaceAll('"','&quot;'), formatDate: v => v, timeAgo: v => v,
    _flowEmptyState: () => 'empty', _flowStatusBadge: v => String(v),
    sessionStorage: {getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, String(value))}};
vm.createContext(context); vm.runInContext(code, context);

const sql = database => ({sql_handoff_enabled: true, sql_database: database, sql_schema: 'reporting', sql_table: 'target', sql_mode: 'append'});
const flows = [
    {id: 1, name: 'ASAP daily', source_adapter: 'asap_portal', enabled: true, last_status: 'succeeded', schedule_type: 'daily', scheduled_day_offsets: [0, 1, 2], ...sql('postgres')},
    {id: 2, name: 'ASAP paused', source_adapter: 'asap_portal', enabled: false, last_status: 'failed', schedule_type: 'weekly', schedule_days: ['friday'], scheduled_day_offsets: [], ...sql('meto_db')},
    {id: 3, name: 'GSCM Friday', source_adapter: 'gscm_portal', enabled: true, last_status: 'succeeded', schedule_type: 'weekly', schedule_days: ['friday'], scheduled_day_offsets: [1]},
    {id: 4, name: 'Outlook month end', source_type: 'outlook', enabled: true, schedule_type: 'monthly', schedule_day: 31, scheduled_day_offsets: [2]},
    {id: 5, name: 'Local file', source_type: 'file', local_file_path: 'x', schedule_type: 'manual'},
    {id: 6, name: 'Python script', source_type: 'python', python_scripts: ['a.py'], python_run_mode: 'run', schedule_type: 'manual', last_status: 'failed'},
    {id: 7, name: 'Draft ASAP', source_adapter: 'asap_portal', classification: 'draft', schedule_type: 'manual'},
];
const all = {source: 'all', active: 'all', result: 'all', schedule: 'all', database: 'all'};
const ids = filters => flows.filter(flow => context._flowMatchesFilters(flow, {...all, ...filters})).map(flow => flow.id);
assert.deepEqual(ids({}), [1, 2, 3, 4, 5, 6, 7]);
assert.deepEqual(ids({source: 'ASAP'}), [1, 2, 7]);
assert.deepEqual(ids({source: 'Python'}), [6]);
assert.deepEqual(ids({active: 'active'}), [1, 3, 4]);
assert.deepEqual(ids({active: 'inactive'}), [2, 5, 6, 7]);
assert.deepEqual(ids({result: 'succeeded'}), [1, 3]);
assert.deepEqual(ids({result: 'failed'}), [2, 6]);
assert.deepEqual(ids({schedule: 'today'}), [1]);
assert.deepEqual(ids({schedule: 'tomorrow'}), [1, 3]);
assert.deepEqual(ids({schedule: 'next3'}), [1, 3, 4]);
assert.deepEqual(ids({database: 'db:postgres'}), [1]);
assert.deepEqual(ids({database: 'db:meto_db'}), [2]);
assert.deepEqual(ids({database: 'none'}), [3, 4, 5, 6, 7]);
assert.deepEqual(ids({source: 'ASAP', active: 'active'}), [1]);

// Options come from the saved Flows: only sources and databases that exist.
const options = context._flowFilterOptions(flows);
assert.deepEqual([...options.source[2].map(([value]) => value)], ['ASAP', 'GSCM', 'Outlook', 'Local', 'Python']);
assert.deepEqual([...options.database[2].map(([, label]) => label)], ['meto_db', 'postgres', 'No SQL output']);

// A stored selection that is no longer offered falls back to all.
stored.set('metronome.flowFilters', JSON.stringify({source: 'Web', result: 'failed', database: 'db:retired', schedule: '<script>'}));
assert.equal(JSON.stringify(context._flowListFilters(flows)), JSON.stringify({...all, result: 'failed'}));
delete context.window._flowFilterMemory;
stored.set('metronome.flowFilters', '{');
assert.equal(JSON.stringify(context._flowListFilters(flows)), JSON.stringify(all));

// No sub-groups: every Flow in the tab is listed exactly once under its source.
context.window._flowFilterMemory = {};
context.window._flowOpenGroupMemory = new Set(['ASAP']);
let html = context._flowListHtml(flows, [], {});
for (const id of [1, 2, 3, 4, 5, 6]) assert.equal(html.split(`data-flow-id="${id}"`).length - 1, 1);
assert(!html.includes('data-flow-id="7"'));
assert.doesNotMatch(html, /topic-|data-topic|Run group|Group flows in/);
assert.match(html, /<span><strong>6<\/strong> production flows<\/span>/);
assert.match(html, /id="flow-filters-clear" hidden>/);
for (const label of ['Source', 'Active', 'Last run', 'Scheduled', 'Database']) assert(html.includes(`<span>${label}</span><select`));
assert.match(html, /<option value="next3" >Next 3 days<\/option>/);

// Filtering says how many are hidden and offers a way back.
context.window._flowFilterMemory = {source: 'ASAP'};
html = context._flowListHtml(flows, [], {});
assert.match(html, /Showing <strong>2<\/strong> of 6 production flows/);
assert.match(html, /<option value="ASAP" selected>ASAP/);
assert.match(html, /id="flow-filters-clear" >/);
assert(!html.includes('data-flow-id="3"'));
assert.match(html, /Production <span>2<\/span>/);
context.window._flowFilterMemory = {database: 'db:meto_db'};
assert.match(context._flowListHtml(flows, [], {}), /<option value="db:meto_db" selected>meto_db/);
context.window._flowFilterMemory = {source: 'GSCM', result: 'failed'};
html = context._flowListHtml(flows, [], {});
assert.match(html, /No production flows match these filters/);
assert.match(html, /Clear filters to see every Flow\./);
assert.match(html, /id="flow-filters-clear" >/);

// Live counts use the same tab, user and filters as the list.
context.window._flowsState = {flows, classification: 'production'};
context.window._flowFilterMemory = {source: 'ASAP'};
assert.deepEqual([...context._flowVisibleFlows().map(flow => flow.id)], [1, 2]);
context.window._flowsState.classification = 'draft';
assert.deepEqual([...context._flowVisibleFlows().map(flow => flow.id)], [7]);

// Last run follows the latest finished run: starting a run keeps the Flow listed,
// and a finished run that changes the result re-renders the list.
const shown = [];
Object.assign(context, {_flowShowView: view => shown.push(view), _flowSyncExecutionRows: () => {},
    document: {querySelectorAll: () => [], getElementById: () => null}});
vm.runInContext(source.slice(source.indexOf('function _flowPatchActivity(activity)'), source.indexOf('function _flowScheduleCatalogMonitor')), context);
const watched = [{id: 1, source_type: 'file', last_status: 'failed'}, {id: 2, source_type: 'file', last_status: 'succeeded'}];
context.window._flowsState = {flows: watched, classification: 'production', view: 'list'};
context.window._flowFilterMemory = {result: 'failed'};
const activity = runs => ({latest_runs: runs, active_runs: runs.filter(run => ['queued', 'claimed', 'running'].includes(run.status)), workers: {online: 1}});
const listed = () => [...context._flowVisibleFlows().map(flow => flow.id)];
context._flowPatchActivity(activity([{flow_id: 1, status: 'running'}, {flow_id: 2, status: 'succeeded'}]));
assert.deepEqual(shown, []);
assert.deepEqual(listed(), [1]);
context._flowPatchActivity(activity([{flow_id: 1, status: 'succeeded'}, {flow_id: 2, status: 'succeeded'}]));
assert.deepEqual(shown, ['list']);
assert.deepEqual(listed(), []);
context._flowPatchActivity(activity([{flow_id: 1, status: 'succeeded'}, {flow_id: 2, status: 'failed'}]));
assert.deepEqual(shown, ['list', 'list']);
assert.deepEqual(listed(), [2]);
context.window._flowFilterMemory = {};
context._flowPatchActivity(activity([{flow_id: 1, status: 'failed'}, {flow_id: 2, status: 'failed'}]));
assert.deepEqual(shown, ['list', 'list'], 'without a Last run filter a finished run patches rows in place');

// After midnight in Dubai the scheduled days are reloaded once, and only a schedule filter re-renders.
const requested = [];
context.api = url => { requested.push(url); return Promise.resolve([{id: 1, scheduled_days_from: '2026-10-30', scheduled_day_offsets: [0]}]); };
const dayState = {flows: [{id: 1, source_type: 'file', scheduled_days_from: '2026-10-29', scheduled_day_offsets: [1]}], view: 'list'};
context.window._flowsState = dayState;
context.window._flowFilterMemory = {schedule: 'today'};
shown.length = 0;
context._flowRefreshScheduledDays(dayState, '2026-10-29');
context._flowRefreshScheduledDays(dayState, undefined);
assert.deepEqual(requested, []);
context._flowRefreshScheduledDays(dayState, '2026-10-30');
const reload = dayState.scheduledDaysReload;
context._flowRefreshScheduledDays(dayState, '2026-10-30');
assert.deepEqual(requested, ['/api/flows']);
await reload;
assert.deepEqual([...dayState.flows[0].scheduled_day_offsets], [0]);
assert.equal(dayState.scheduledDaysFrom, '2026-10-30');
assert.deepEqual(shown, ['list']);
context._flowRefreshScheduledDays(dayState, '2026-10-30');
assert.deepEqual(requested, ['/api/flows']);
console.log('flow list filter tests passed');
