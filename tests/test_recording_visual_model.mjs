import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const context={window:{},structuredClone,URL};vm.createContext(context);
vm.runInContext(fs.readFileSync('app/static/flow_recording_model.js','utf8'),context);
const M=context.window.RecordedFlowModel;
const click={id:'click',action:'click',page:'page',locator:[{method:'get_by_role',args:['button'],kwargs:{name:'Excel down'}}]};
const event={id:'event',action:'download',page:'page',steps:[click],output:{format:'xlsx'}};
assert.equal(M.describe(event),'Click “Excel down”');
assert.equal(M.triggering(event).id,'click');
const def={steps:[{id:'open',action:'goto',page:'page'},event,{id:'wait',action:'wait',page:'page',seconds:5}]};
const moved=M.move(def,'event',2);
assert.equal(moved.steps[2].id,'event');assert.equal(moved.steps[2].steps[0].id,'click');
assert.equal(def.steps[1].id,'event');
assert.throws(()=>M.move(def,'click',0),/unit/);
const popup={id:'popup',action:'popup',page:'page',result_page:'page1',steps:[structuredClone(click)]};
const use={id:'use',action:'click',page:'page1'};
const pages={steps:[popup,use]};M.validatePages(pages);
assert.throws(()=>M.move(pages,'use',0),/before/);
assert.throws(()=>M.remove(pages,'popup'),/before/);
const input={id:'input',action:'fill',page:'page',locator:[{method:'get_by_label',args:['Start'],kwargs:{exact:true}}]};
const owned={steps:[input,{...event,output:{period_checks:[{column:'Date',parameter:'start'}]}}],parameters:{start:{step_id:'input',mode:'fixed'},end:{step_id:'click',mode:'portal_default',not_after:'start'}},identity:{text:'Title',target:M.target(input)},readiness:{trigger_step_id:'input'}};
const removed=M.remove(owned,'input');
assert.equal(removed.parameters.start,undefined);assert.equal(removed.parameters.end.not_after,undefined);
assert.equal(removed.steps[0].output.period_checks.length,0);assert.equal(removed.identity.text,undefined);assert.equal(removed.readiness.trigger_step_id,undefined);
assert.equal(M.owner(def,'click').id,'event');
console.log('Visual recording model tests passed');

const title={text:'Sales Report',kind:'page_title',target:{page:'page',locator:[]}};
const waitTitle={steps:[{id:'wait',action:'wait',page:'page',seconds:5},{id:'open',action:'goto',page:'page'}],identity:title};
assert.equal(M.remove(waitTitle,'wait').identity.text,'Sales Report');

// Old readiness/title metadata must never impose hidden movement requirements.
const legacy={steps:[input,{id:'generate',action:'click',page:'page'},event],
    parameters:{start:{step_id:'input',mode:'fixed'}},
    identity:{text:'Old title',target:{page:'closed-page',locator:[]}},
    readiness:{mode:'changed_text',trigger_step_id:'generate',target:{page:'closed-page'}}};
assert.equal(M.move(legacy,'event',0).steps[0].id,'event');
assert.equal(M.move(legacy,'input',2).steps[2].id,'input');
assert.throws(()=>M.validatePages({...legacy,parameters:{start:{step_id:'missing'}}}),/removed input/);

const delayed={steps:[{...click,delay_before_seconds:60},{id:'pause',action:'wait',page:'page',seconds:15}]};
assert.equal(M.move(delayed,'click',1).steps[1].delay_before_seconds,60);
assert.throws(()=>M.validatePages({steps:[{...click,delay_before_seconds:0}]}),/1–600/);
assert.throws(()=>M.validatePages({steps:[{id:'pause',action:'wait',seconds:10,delay_before_seconds:10}]}),/before an action/);
assert.equal(M.canDelay(click),true);assert.equal(M.canDelay({action:'goto'}),false);

// Ordinary codegen title/text targets already describe the recorded control;
// friendly presentation must not require labels added to a validated definition.
const settingTitle={id:'setting',action:'click',page:'page',locator:[{method:'get_by_title',args:['Setting'],kwargs:{}}],args:[],kwargs:{}};
const publicText={id:'public',action:'click',page:'page',locator:[{method:'get_by_text',args:['Public'],kwargs:{exact:true}}],args:[],kwargs:{}};
assert.equal(M.describe(settingTitle),'Click “Setting”');
assert.equal(M.describe(publicText),'Click “Public”');
assert.equal(settingTitle.label,undefined);assert.equal(publicText.label,undefined);
