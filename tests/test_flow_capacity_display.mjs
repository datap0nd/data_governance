import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const context = {esc: s => String(s ?? '').replaceAll('<', '&lt;')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function _flowCapacityHtml('), source.indexOf('// ── Router ──')), context);
const html = context._flowCapacityHtml({headless_capacity:3, online_capacity:1, active_headless:2, slots:[{slot:1, configured:true, status:'<offline>', current_run_id:42}],
    headed_capacity:4, online_headed_capacity:2, active_headed:1, headed_slots:[{slot:2, configured:true, status:'busy', current_task_id:99}]});
assert.match(html, /1 of 3 configured/);
assert.match(html, /value="3" selected/);
assert.match(html, /value="5"/);
assert.match(html, /Run 42/);
assert.match(html, /&lt;offline>/);
assert.match(html, /Lowering capacity lets active work finish/);
assert.match(html, /2 of 4 configured visible slots/);
assert.match(html, /Export task 99/);
assert.match(html, /value="4" selected/);
assert.match(html, /Start visible workers/);
const controls = {
    '#flow-capacity-form':{}, '#flow-capacity-start':{}, '#flow-capacity-start-headed':{},
    '#flow-headless-capacity':{value:'2'}, '#flow-headed-capacity':{value:'3'}, '#flow-capacity-result':{},
};
const calls=[];
Object.assign(context, {$:id=>controls[id], document:{querySelectorAll:()=>[]}, toast:()=>{}, navigate:async()=>{},
    apiPut:async(url,body)=>calls.push([url,body]), apiPost:async url=>{calls.push([url]);return {slots:[{worker_id:'visible-1',status:'starting'}]};}});
context.bindFlowSettingsPage();
await controls['#flow-capacity-form'].onsubmit({preventDefault(){}, currentTarget:{querySelector:()=>({})}});
assert.equal(calls[0][1].headless_capacity,2); assert.equal(calls[0][1].headed_capacity,3);
await controls['#flow-capacity-start-headed'].onclick({currentTarget:{}});
assert.equal(calls[1][0],'/api/system/flows/start?mode=headed');
assert.match(controls['#flow-capacity-result'].textContent,/visible-1: starting/);
console.log('Flow capacity display tests passed');
