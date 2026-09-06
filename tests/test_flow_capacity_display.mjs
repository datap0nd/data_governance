import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const context = {esc: s => String(s ?? '').replaceAll('<', '&lt;')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function _flowCapacityHtml('), source.indexOf('// ── Router ──')), context);
const html = context._flowCapacityHtml({total_capacity:12, active_total:3, max_slots:32, headless_capacity:3, online_capacity:1, active_headless:2, slots:[{slot:1, configured:true, status:'<offline>', current_run_id:42}],
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
    '#flow-browser-channel':{value:'chrome'}, '#flow-recording-wait':{value:'20'}, '#flow-total-capacity':{value:'16'},
};
const calls=[];
Object.assign(context, {$:id=>controls[id], document:{querySelectorAll:()=>[]}, toast:()=>{}, navigate:async()=>{}, currentPage:'flow-settings',
    apiPut:async(url,body)=>calls.push([url,body]), apiPost:async url=>{calls.push([url]);return {slots:[{worker_id:'visible-1',status:'starting'}]};}});
context.bindFlowSettingsPage();
await controls['#flow-capacity-form'].onsubmit({preventDefault(){}, currentTarget:{querySelector:()=>({})}});
assert.equal(calls[0][1].total_capacity,16); assert.equal(calls[0][1].headless_capacity,2); assert.equal(calls[0][1].headed_capacity,3);
assert.equal(calls[0][1].browser_channel,'chrome');
assert.equal(calls[0][1].recording_wait_seconds,20);
assert.match(html, /Default wait before each action/);
assert.match(html, /id="flow-recording-wait"[^>]*min="1"[^>]*max="600"[^>]*value="10"/);
assert.match(html, /Google Chrome/); assert.match(html, /Microsoft Edge/);
await controls['#flow-capacity-start-headed'].onclick({currentTarget:{}});
assert.equal(calls[1][0],'/api/system/flows/start?mode=headed');
assert.match(controls['#flow-capacity-result'].textContent,/visible-1: starting/);
console.log('Flow capacity display tests passed');

assert.match(html, /value="32"/);
assert.match(html, /3 of 12 total workers active/);
assert.match(html, /Background and visible work share this limit/);
const expanded = context._flowCapacityHtml({total_capacity:24, headless_capacity:32, headed_capacity:16, active_total:12,
    slots:[{slot:32,configured:true,online:true,status:'busy',current_run_id:32},{slot:2,configured:false,status:'offline'}], headed_slots:[]});
assert.match(expanded, /value="24" selected/);
assert.match(expanded, /1 unused slots/);
assert.match(expanded, /Run 32/);
assert.match(context._flowPortalCapacityHtml([{id:1,name:'GSCM',capacity:16,adapter:'gscm_portal'}]), /value="32"/);
