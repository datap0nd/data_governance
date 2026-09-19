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
const renamed=structuredClone(click),originalLocator=structuredClone(click.locator);
assert.deepEqual({...M.editableTarget(renamed)}, {index:0,field:'name',value:'Excel down'});
M.renameTarget(renamed,'Main');
assert.equal(renamed.locator[0].kwargs.name,'Main');
assert.deepEqual(renamed.locator[0].args,originalLocator[0].args);
assert.equal(renamed.locator[0].method,originalLocator[0].method);
assert.equal(M.editableTarget({locator:[{method:'locator',args:['.country-button'],kwargs:{}}]}),null);
assert.throws(()=>M.renameTarget({locator:[{method:'locator',args:['.country-button'],kwargs:{}}]},'Main'),/visible-text/);
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

const weekClick=value=>({id:`week-${value}`,action:'click',page:'page',locator:[
    {method:'locator',args:['#week-box'],kwargs:{}},
    {method:'get_by_role',args:['button'],kwargs:{name:value,exact:true}}
],args:[],kwargs:{}});
const weekly={version:2,steps:[weekClick('2026-W33'),event],parameters:{}};
assert.equal(M.rangeCandidate(weekly,'week-2026-W33').steps.length,1);
const ranged=M.makeRange(weekly,'week-2026-W33');
assert.equal(ranged.version,3);assert.equal(ranged.steps.length,2);
assert.equal(ranged.steps[0].action,'select_range');
assert.equal(ranged.steps[0].range.start,'2026-W33');
assert.equal(ranged.steps[0].range.end,'latest_selectable');
assert.match(M.describe(ranged.steps[0]),/2026-W33/);
assert.deepEqual(ranged.steps[0].locator,[{method:'locator',args:['#week-box'],kwargs:{}}]);
assert.doesNotMatch(JSON.stringify(ranged.steps[0].locator),/2026-W33/);
assert.equal(ranged.steps[0].range.source_step.action,'click');
assert.deepEqual(M.restoreRange(ranged,'week-2026-W33').steps[0],weekly.steps[0]);
assert.equal(M.rangeCandidate({version:2,steps:[{id:'open',action:'goto',page:'page',locator:[],args:['https://example.test']}],parameters:{}},'open'),null);
M.setRangeAncestor(ranged.steps[0],3);
assert.equal(ranged.steps[0].range.container_ancestor_levels,3);
assert.deepEqual(ranged.steps[0].locator.slice(-2).map(part=>part.args[0]),['xpath=..','xpath=..']);

// Duplicate copies one root step right after itself with fresh identities.
// The original definition is untouched; a copied date parameter gets its own name.
// Objects the model builds with literals belong to the vm realm, so compare plain copies.
const plain=value=>JSON.parse(JSON.stringify(value));
const duplicated=M.duplicate(owned,'input');
assert.deepEqual(duplicated.steps.map(s=>s.id),['input','input-copy','event']);
assert.deepEqual(duplicated.steps[1].locator,input.locator);
assert.deepEqual(plain(duplicated.parameters.start_2),{step_id:'input-copy',mode:'fixed'});
assert.deepEqual(plain(duplicated.parameters.start),{step_id:'input',mode:'fixed'});
assert.deepEqual(plain(duplicated.parameters.end),owned.parameters.end);
assert.deepEqual(owned.steps.map(s=>s.id),['input','event']);
assert.equal(Object.keys(owned.parameters).length,2);
assert.deepEqual(M.duplicate(duplicated,'input').steps.map(s=>s.id),['input','input-copy-2','input-copy','event']);
assert.equal(M.duplicate(duplicated,'input').parameters.start_3.step_id,'input-copy-2');
// An event group copies as one unit; its nested action is renamed with it.
const groupCopy=M.duplicate(def,'event');
assert.deepEqual(groupCopy.steps.map(s=>s.id),['open','event','event-copy','wait']);
assert.equal(groupCopy.steps[2].action,'download');assert.equal(groupCopy.steps[2].steps[0].id,'click-copy');
assert.deepEqual(plain(groupCopy.steps[2].output),event.output);
assert.equal(M.owner(groupCopy,'click-copy').id,'event-copy');
// A caller-supplied identity factory is used when it yields a free id.
assert.equal(M.duplicate(def,'event',id=>`${id}-x`).steps[2].id,'event-x');
assert.equal(M.duplicate(def,'event',()=>'event').steps[2].id,'event-copy');
// Parameters and period checks inside the copied group point at the copy, not the source.
const groupedFills={steps:[{id:'dl',action:'download',page:'page',output:{format:'csv',period_checks:[{column:'Period',parameter:'start'}]},
    steps:[{id:'in-start',action:'fill',page:'page',locator:input.locator},{id:'in-end',action:'fill',page:'page',locator:input.locator}]}],
    parameters:{start:{step_id:'in-start',mode:'fixed',value:'2026-01-01',not_after:'end'},end:{step_id:'in-end',mode:'portal_default'}}};
const groupedCopy=M.duplicate(groupedFills,'dl');
assert.deepEqual(groupedCopy.steps[1].steps.map(s=>s.id),['in-start-copy','in-end-copy']);
assert.deepEqual(plain(groupedCopy.parameters.start_2),{step_id:'in-start-copy',mode:'fixed',value:'2026-01-01',not_after:'end_2'});
assert.deepEqual(plain(groupedCopy.parameters.end_2),{step_id:'in-end-copy',mode:'portal_default'});
assert.deepEqual(plain(groupedCopy.steps[1].output.period_checks),[{column:'Period',parameter:'start_2'}]);
assert.deepEqual(plain(groupedCopy.steps[0].output.period_checks),[{column:'Period',parameter:'start'}]);
assert.equal(groupedCopy.parameters.start.not_after,'end');
// A range step keeps its restorable source under the copy's identity.
const rangeCopy=M.duplicate(ranged,'week-2026-W33');
assert.equal(rangeCopy.steps[1].id,'week-2026-W33-copy');
assert.equal(rangeCopy.steps[1].range.source_step.id,'week-2026-W33-copy');
assert.equal(M.restoreRange(rangeCopy,'week-2026-W33-copy').steps[1].id,'week-2026-W33-copy');
assert.equal(M.restoreRange(rangeCopy,'week-2026-W33-copy').steps[1].action,'click');
// Steps that open or close a page cannot exist twice.
assert.equal(M.canDuplicate(popup),false);assert.equal(M.canDuplicate({action:'new_page'}),false);assert.equal(M.canDuplicate({action:'close'}),false);
assert.equal(M.canDuplicate(click),true);assert.equal(M.canDuplicate(event),true);assert.equal(M.canDuplicate({action:'wait'}),true);
assert.throws(()=>M.duplicate(pages,'popup'),/cannot be duplicated/);
assert.throws(()=>M.duplicate({steps:[{id:'open',action:'new_page',page:'page'}]},'open'),/cannot be duplicated/);
assert.throws(()=>M.duplicate(def,'click'),/unit/);
console.log('Duplicate step model tests passed');

// A date range control: one recorded click on a slider handle becomes a set_range
// step whose start and end are week parameters; the recorded click stays restorable.
const handleClick={id:'week-handle',action:'click',page:'page',locator:[{method:'locator',args:['#week-prompt'],kwargs:{}},{method:'get_by_role',args:['slider'],kwargs:{name:'Week end'}}],args:[],kwargs:{}};
const sliderDef={version:3,steps:[{id:'open',action:'goto',page:'page',locator:[],args:['https://example.test']},handleClick,event],parameters:{}};
assert.equal(M.sliderCandidate(sliderDef,'week-handle').index,1);
assert.equal(M.sliderCandidate(sliderDef,'open'),null);
assert.equal(M.sliderCandidate(sliderDef,'event'),null);
const slid=M.makeSlider(sliderDef,'week-handle');
assert.equal(slid.version,5);assert.equal(slid.steps[1].action,'set_range');assert.equal(slid.steps[1].range.kind,'week');assert.equal(slid.steps[1].range.week_days,'sunday');
assert.equal(slid.steps[1].range.container_ancestor_levels,0);
assert.deepEqual(plain(slid.steps[1].locator),plain(handleClick.locator));
assert.equal(slid.steps[1].range.source_step.action,'click');
assert.deepEqual(plain(slid.parameters.start),{step_id:'week-handle',role:'start',unit:'week',mode:'portal_default',format:'%G-W%V'});
assert.deepEqual(plain(slid.parameters.end),{step_id:'week-handle',role:'end',unit:'week',mode:'calculated',expression:'latest_selectable',offset_weeks:0,format:'%G-W%V'});
assert.deepEqual(Object.keys(M.rangeParameters(slid,'week-handle')).sort(),['end','start']);
assert.equal(M.describe(slid.steps[1]),'Set week range');
assert.equal(M.describe({...slid.steps[1],range:{...slid.steps[1].range,kind:'date'}}),'Set date range');
assert.equal(M.describe({...slid.steps[1],range:{...slid.steps[1].range,kind:'month'}}),'Set month range');
assert.equal(M.rangeCandidate(slid,'week-handle'),null);
assert.equal(M.sliderCandidate(slid,'week-handle'),null);
assert.equal(M.requiredVersion(slid),5);assert.equal(M.requiredVersion(sliderDef),3);assert.equal(M.requiredVersion(weekly),2);
assert.equal(sliderDef.steps[1].action,'click');
M.setRangeAncestor(slid.steps[1],2);
assert.deepEqual(plain(slid.steps[1].locator).map(part=>part.args[0]),['#week-prompt','xpath=..']);
const restored=M.restoreSlider(slid,'week-handle');
assert.deepEqual(plain(restored.steps[1]),handleClick);assert.deepEqual(plain(restored.parameters),{});
// Existing parameter names stay unique, and a duplicate of the step gets its own pair.
const named={...sliderDef,parameters:{start:{step_id:'open',mode:'fixed',value:'2026-01-01'}}};
const slid2=M.makeSlider(named,'week-handle');
assert.deepEqual(Object.keys(slid2.parameters).sort(),['end','start','start_2']);
assert.equal(slid2.parameters.start_2.role,'start');assert.equal(slid2.parameters.start.step_id,'open');
const dupSlider=M.duplicate(slid,'week-handle');
assert.equal(dupSlider.steps[2].action,'set_range');assert.equal(dupSlider.steps[2].range.source_step.id,'week-handle-copy');
const copyParams=M.rangeParameters(dupSlider,'week-handle-copy');
assert.equal(copyParams.start.name,'start_2');assert.equal(copyParams.end.name,'end_2');
assert.equal(copyParams.start.parameter.step_id,'week-handle-copy');
assert.equal(M.rangeParameters(dupSlider,'week-handle').start.name,'start');
// Renaming a parameter follows its references; names must stay unique.
const referenced={...slid,steps:[...slid.steps.slice(0,2),{...event,output:{format:'xlsx',period_checks:[{column:'Week',parameter:'end'}]}}],parameters:{...slid.parameters,other:{step_id:'open',mode:'fixed',not_after:'end'}}};
const renamedParameters=M.renameParameter(referenced,'end','last_week');
assert.equal(renamedParameters.parameters.end,undefined);assert.equal(renamedParameters.parameters.last_week.role,'end');
assert.equal(renamedParameters.parameters.other.not_after,'last_week');assert.equal(renamedParameters.steps[2].output.period_checks[0].parameter,'last_week');
assert.equal(referenced.parameters.end.role,'end');
assert.throws(()=>M.renameParameter(slid,'end','start'),/unique/);
assert.equal(M.renameParameter(slid,'end','end').parameters.end.role,'end');
assert.equal(M.weekText('2026-W05','%G%V'),'202605');assert.equal(M.weekText('202605','%G-W%V'),'2026-W05');assert.equal(M.weekText('x','%G%V'),'x');
assert.equal(M.validWeek('2026-W53','%G-W%V'),true);assert.equal(M.validWeek('2026-W54','%G-W%V'),false);assert.equal(M.validWeek('202605','%G-W%V'),false);assert.equal(M.validWeek('202605','%G%V'),true);
assert.throws(()=>M.makeSlider(sliderDef,'open'),/element target/);
assert.throws(()=>M.restoreSlider(sliderDef,'week-handle'),/no recorded action/);
const monthly=M.setSliderKind(slid,'week-handle','month');
assert.equal(monthly.version,5);assert.equal(monthly.steps[1].range.kind,'month');assert.equal(monthly.steps[1].range.week_days,undefined);
assert.deepEqual(plain(monthly.parameters.start),{step_id:'week-handle',role:'start',unit:'month',mode:'calculated',expression:'oldest_selectable',offset_months:0,format:'%Y%m'});
assert.deepEqual(plain(monthly.parameters.end),{step_id:'week-handle',role:'end',unit:'month',mode:'calculated',expression:'latest_selectable',offset_months:0,format:'%Y%m'});
const backToWeek=M.setSliderKind(monthly,'week-handle','week');
assert.equal(backToWeek.parameters.start.mode,'portal_default');assert.equal(backToWeek.parameters.start.unit,'week');
assert.equal(backToWeek.parameters.end.expression,'latest_selectable');assert.equal(backToWeek.parameters.end.offset_weeks,0);
console.log('Date range control model tests passed');
