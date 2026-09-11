import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Saving a Flow never asks for a recording test: an untested saved draft is
// applied as it is, without a confirmation dialog or a waiver flag. Only
// unsaved recording edits stop the save.
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const start = source.indexOf('async function _flowSubmitBuilder(event)');
const end = source.indexOf('function _bindFlowWorkspace()', start);
assert.ok(start >= 0 && end > start);
assert.ok(!source.slice(start, end).includes('confirm('), 'the builder save must not open a confirmation');
assert.ok(!source.includes('allow_untested_recording'), 'the retired waiver flag must not be sent');

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
let confirmCalls = 0;
const context = {
    window:{
        _flowRecordingSelections:new Map([[12,null]]),
        _flowUntestedRecordingSelections:new Map([[12,88]]),
        _flowBuilderDrafts:new Map([[12,{pending:true}],['new',{pending:true}]]),
        confirm:()=>{confirmCalls++;return false;},
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

// An untested saved draft saves immediately as the Flow's recording.
await context._flowSubmitBuilder(event);
assert.equal(event.prevented,true);
assert.equal(confirmCalls,0);
assert.equal(calls[0].path,'/api/flows/12');
assert.equal(calls[0].body.recording_revision_id,88);
assert.equal('allow_untested_recording' in calls[0].body,false);
assert.deepEqual(calls[1],{navigate:'flows'});
assert.equal(toasts.at(-1),'Flow saved. This recording has not been tested; check the first run output.');
assert.equal(context.window._flowUntestedRecordingSelections.has(12),false);
assert.equal(context.window._flowBuilderDrafts.has(12),false);
assert.equal(error.textContent,'');

// A tested selection saves the same way with the plain confirmation.
context.window._flowRecordingSelections.set(12,90);
context._flowCollectBuilder=()=>({name:'Tested',execution_method:'recorded',recording_revision_id:90});
button.disabled=false;
await context._flowSubmitBuilder(event);
assert.equal(calls[2].body.recording_revision_id,90);
assert.deepEqual(calls[3],{navigate:'flows'});
assert.equal(toasts.at(-1),'Flow saved');
assert.equal(confirmCalls,0);

// Unsaved recording edits are the only thing that stops the save.
context.window._flowUntestedRecordingSelections.set(12,null);
button.disabled=false;error.textContent='';error.scrolled=false;
await context._flowSubmitBuilder(event);
assert.equal(error.textContent,'Flow not saved: Save the recording draft before saving this Flow.');
assert.equal(error.scrolled,true);
assert.equal(button.disabled,false);
assert.equal(calls.length,4);
assert.equal(confirmCalls,0);

console.log('save without a recording test confirmation tests passed');
