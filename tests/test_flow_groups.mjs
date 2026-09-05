import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('function _flowListHtml'), source.indexOf('/** The folder a report sits in'));
const context = {window: {}, esc: v => String(v ?? '').replaceAll('<','&lt;').replaceAll('"','&quot;'), formatDate: v => v, timeAgo: v => v, _flowEmptyState: () => 'empty', _flowStatusBadge: v => String(v)};
vm.createContext(context); vm.runInContext(code, context);
const flows = [{id: 1, name: '<Local>', source_type: 'file', local_file_path: 'x', schedule_type: 'manual'}, {id: 2, name: 'ASAP', source_adapter: 'asap_portal', last_status: 'failed', sql_handoff_enabled: true, sql_database: 'Db', sql_schema: 'CaseSensitive', sql_table: 'MyTable'}, {id: 3, name: 'Other', source_type: 'portal'}];
let html = context._flowListHtml(flows, [], {}, [{flow_id: 1, status: 'running'}]);
assert(html.indexOf('> ASAP ') < html.indexOf('> Local '));
assert(html.indexOf('> Local ') < html.indexOf('> Web '));
assert(!html.includes('flow-group-GSCM'));
assert.match(html, /aria-expanded="false"/);
assert.match(html, /1 active runs/); assert.match(html, /1 failed/);
assert.match(html, /&lt;Local>/); assert.match(html, /Db.CaseSensitive.MyTable/);
assert.match(html, /Private snapshots/); assert(!html.includes('Freshness'));
for (const label of ['Flow','Active','Owner','Source','Download','Browser','Schedule','Last run','Actions']) assert(html.includes(`>${label}<`));
for (const cls of ['flow-run', 'flow-stop', 'flow-edit', 'flow-delete', 'flow-open-folder', 'flow-enabled-switch']) assert(html.includes(cls));
context.window._flowOpenGroupMemory = undefined;
context.sessionStorage = {getItem: () => '["Local","<script>"]'};
assert.deepEqual([...context._flowOpenGroups()], ['Local']);
html = context._flowListHtml(flows, [], {});
assert.match(html, /id="flow-group-rows-Local" >/);
context.window._flowOpenGroupMemory = undefined;
context.sessionStorage.getItem = () => '{';
assert.equal(context._flowOpenGroups().size, 0);
console.log('flow group tests passed');

// Running rows live in the pinned table once, including collapsed categories.
context.window._flowOpenGroupMemory = new Set();
const executingHtml = context._flowListHtml(flows, [], {}, [{flow_id:1,status:'running'}, {flow_id:2,status:'queued'}]);
const pinned = executingHtml.slice(executingHtml.indexOf('<section id="flow-execution-pane"'), executingHtml.indexOf('</section>'));
assert.match(pinned, /Flows in execution/);
assert.match(pinned, /data-flow-id="1"/);
assert.match(pinned, /data-flow-id="2"/);
assert.doesNotMatch(pinned, /data-flow-id="3"/);
assert(executingHtml.indexOf('flow-execution-pane') < executingHtml.indexOf('flow-status-strip'));
for (const id of [1,2,3]) assert.equal(executingHtml.split(`data-flow-id="${id}"`).length-1, 1);
assert.match(executingHtml, /id="flow-group-rows-Local" hidden><\/tbody>/);
assert.match(context._flowListHtml(flows,[],{},[]), /id="flow-execution-pane"[^>]*hidden/);
assert.equal((executingHtml.match(/id="flow-sort-name"/g)||[]).length,1, 'pinned headings must not duplicate sort controls');
const css=fs.readFileSync(new URL('../app/static/style.css',import.meta.url),'utf8');
assert.match(css,/\.flow-execution-pane \{ position: sticky/);
assert.match(css,/max-height: min\(var\(--flow-execution-height, 360px\), 50dvh\); overflow: auto/);

// Execute row relocation with stable DOM objects: no cloning/rebinding.
function body(id) {
    return {id,children:[],hidden:true,scrollTop:77,
        appendChild(row) { this.insertBefore(row,null); },
        insertBefore(row,next) {
            if(row.parentElement)row.parentElement.children.splice(row.parentElement.children.indexOf(row),1);
            this.children.splice(next ? this.children.indexOf(next) : this.children.length,0,row);
            row.parentElement=this;
        }};
}
const execution=body('flow-execution-rows'), local=body('flow-group-rows-Local');
const pane={hidden:true}, counter={textContent:''};
let refocused=0;
const control={isConnected:true,focus:()=>refocused++};
const rowNodes=[1,2,3,4].map(id=>({dataset:{flowId:String(id),flowGroup:'Local'},
    contains:node=>id===2&&node===control,listener:'preserved'}));
rowNodes.forEach(row=>local.appendChild(row));
context.window._flowsState={flows:rowNodes.map(row=>({id:Number(row.dataset.flowId),name:row.dataset.flowId,source_type:'file'})),catalog:{}};
context.window._flowSortMemory=null;
context.document={activeElement:control,querySelectorAll:()=>rowNodes,
    getElementById:id=>({'flow-execution-rows':execution,'flow-group-rows-Local':local,'flow-execution-pane':pane,'flow-execution-count':counter})[id]};
context._flowWatchExecutionPane=()=>{};
context._flowSyncExecutionRows(new Map([[2,{}],[4,{}]]));
assert.deepEqual(execution.children.map(row=>row.dataset.flowId),['2','4']);
assert.deepEqual(local.children.map(row=>row.dataset.flowId),['1','3']);
assert.equal(pane.hidden,false);assert.equal(counter.textContent,'2 active');
assert.equal(execution.children[0],rowNodes[1]);assert.equal(refocused,1);
context._flowSyncExecutionRows(new Map([[2,{}],[4,{}]]));
assert.equal(execution.scrollTop,77);assert.equal(refocused,1,'unchanged polls must not move focus');
context._flowSyncExecutionRows(new Map());
assert.deepEqual(local.children.map(row=>row.dataset.flowId),['1','2','3','4']);
assert.equal(local.hidden,true,'returning to a category preserves its collapsed state');
assert.equal(pane.hidden,true);assert.equal(rowNodes[1].listener,'preserved');
