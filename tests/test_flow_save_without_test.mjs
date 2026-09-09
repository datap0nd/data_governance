import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const start = source.indexOf('async function _flowSubmitBuilder(event)');
const end = source.indexOf('function _bindFlowWorkspace()', start);
assert.ok(start >= 0 && end > start);

const button = {disabled:false};
const error = {textContent:'',scrollIntoView:()=>{error.scrolled=true;}};
const form = {
    dataset:{id:'12'},
    querySelector(selector) {
        return selector === 'button[type="submit"]' ? button : error;
    },
};
const event = {currentTarget:form,preventDefault:()=>{event.prevented=true;}};
const calls = [];
const toasts = [];
const context = {
    window:{
        _flowRecordingSelections:new Map([[12,null]]),
        _flowUntestedRecordingSelections:new Map([[12,88]]),
        _flowBuilderDrafts:new Map([[12,{pending:true}],['new',{pending:true}]]),
        confirm:()=>false,
    },
    _flowCollectBuilder:()=>({name:'Pending edits',execution_method:'recorded'}),
    apiPut:async(path,body)=>{calls.push({path,body});return {id:12};},
    apiPostJson:async()=>{throw Error('unexpected create');},
    toast:message=>toasts.push(message),
    navigate:async page=>calls.push({navigate:page}),
    _flowRevealServerError:()=>{},
    _flowShowView:()=>{},
    FlowRecordings:{open:async()=>{}},
    Error,
    Number,
};
vm.createContext(context);
vm.runInContext(source.slice(start,end),context);

await context._flowSubmitBuilder(event);
assert.equal(event.prevented,true);
assert.equal(calls.length,0);
assert.equal(button.disabled,false);
assert.equal(context.window._flowUntestedRecordingSelections.get(12),88);
assert.deepEqual(context.window._flowBuilderDrafts.get(12),{pending:true});

context.window.confirm=message=>{
    assert.match(message,/Save without testing\?/);
    assert.match(message,/Check the first run output/);
    return true;
};
await context._flowSubmitBuilder(event);
assert.equal(calls[0].path,'/api/flows/12');
assert.equal(calls[0].body.recording_revision_id,88);
assert.equal(calls[0].body.allow_untested_recording,true);
assert.deepEqual(calls[1],{navigate:'flows'});
assert.equal(toasts.at(-1),'Flow saved without testing; check the first run output.');
assert.equal(context.window._flowUntestedRecordingSelections.has(12),false);
assert.equal(context.window._flowBuilderDrafts.has(12),false);

context.window._flowUntestedRecordingSelections.set(12,null);
button.disabled=false;error.textContent='';error.scrolled=false;
await context._flowSubmitBuilder(event);
assert.equal(error.textContent,'Flow not saved: Save the recording draft before saving this Flow.');
assert.equal(error.scrolled,true);
assert.equal(button.disabled,false);
assert.equal(calls.length,2);

console.log('save without testing confirmation tests passed');
