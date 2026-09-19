/* Data-only editing. Event scopes stay atomic; IDs never depend on position. */
window.RecordedFlowModel = (() => {
    const clone = value => structuredClone(value);
    const all = steps => steps.flatMap(step => [step, ...all(step.steps || [])]);
    const triggering = step => ['download','popup'].includes(step.action) && step.steps?.length === 1 && !step.steps[0].steps ? step.steps[0] : step;
    const interactions = new Set(['click','dblclick','fill','press','select_option','check','uncheck','set_checked','hover','clear','press_sequentially']);
    const canDelay = step => interactions.has(step.action);
    const target = step => ({page:step.page || 'page',locator:clone(step.locator || [])});
    const frame = step => {
        const parts = step?.locator || []; let end = -1;
        parts.forEach((p,i) => { if (['frame_locator','content_frame'].includes(p.method)) end=i; });
        return {page:step?.page || 'page',locator:clone(parts.slice(0,end+1))};
    };
    const name = step => {
        const part = [...(step.locator || [])].reverse().find(p => p.kwargs?.name || ['get_by_text','get_by_label','get_by_title','get_by_placeholder','get_by_alt_text','get_by_test_id'].includes(p.method));
        return part?.kwargs?.name || part?.args?.[0] || (step.locator?.length ? 'recorded element' : '');
    };
    const WEEK_ISO=/^(20\d{2})-W(0[1-9]|[1-4]\d|5[0-3])$/,WEEK_COMPACT=/^(20\d{2})(0[1-9]|[1-4]\d|5[0-3])$/;
    const weekText=(value,fmt)=>{const text=String(value||'').trim(),match=text.match(WEEK_ISO)||text.match(WEEK_COMPACT);if(!match)return text;return fmt==='%G%V'?`${match[1]}${match[2]}`:`${match[1]}-W${match[2]}`;};
    const validWeek=(value,fmt)=>(fmt==='%G%V'?WEEK_COMPACT:WEEK_ISO).test(String(value||'').trim());
    const requiredVersion=definition=>{const steps=all(definition.steps||[]),parameters=Object.values(definition.parameters||{});if(steps.some(s=>s.action==='set_range'&&(s.range?.kind==='month'||s.range?.container_ancestor_levels===0))||parameters.some(p=>p&&p.unit==='month'))return 5;if(steps.some(s=>s.action==='set_range')||parameters.some(p=>p&&p.unit==='week'))return 4;if(steps.some(s=>s.action==='select_range'))return 3;return Math.max(2,Number(definition.version)||1);};
    const isoWeek = value => {
        const text=String(value??'');
        const match=text.match(/(?:^|\D)(20\d{2})\s*[-/ ]?\s*[Ww]?\s*(0?[1-9]|[1-4]\d|5[0-3])(?:\D|$)/);
        if(!match)return null;
        return `${match[1]}-W${String(Number(match[2])).padStart(2,'0')}`;
    };
    const editableTarget = step => {
        const locator=step?.locator || [];
        for(let index=locator.length-1;index>=0;index--){
            const part=locator[index];
            if(part.method==='get_by_role'&&typeof part.kwargs?.name==='string')return {index,field:'name',value:part.kwargs.name};
            if(['get_by_text','get_by_label','get_by_title','get_by_placeholder','get_by_alt_text','get_by_test_id'].includes(part.method)&&typeof part.args?.[0]==='string')return {index,field:'argument',value:part.args[0]};
        }
        return null;
    };
    function renameTarget(step,value) {
        const target=editableTarget(step);
        if(!target)throw Error('Choose a visible-text, input-label or CSS target instead.');
        if(target.field==='name')step.locator[target.index].kwargs.name=value;
        else step.locator[target.index].args[0]=value;
        return step;
    }
    function describe(step) {
        if (step.label) return step.label;
        const action = triggering(step);
        if (action.label) return action.label;
        if (action.action === 'assert' && typeof action.args?.[0] === 'string') return `Check “${action.args[0]}”`;
        if (action.action === 'wait') return `Wait ${action.seconds} seconds`;
        if (action.action === 'select_range') return `Select week range from ${action.range?.start || 'recorded start'}`;
        if (action.action === 'set_range') return `Set ${action.range?.kind === 'date' ? 'date' : action.range?.kind === 'month' ? 'month' : 'week'} range`;
        if (action.action === 'goto') { try { return `Open ${new URL(action.args[0]).hostname}`; } catch { return 'Open report page'; } }
        const verbs = {new_page:'Open browser page',click:'Click',dblclick:'Double click',fill:'Enter value in',press:'Press key in',select_option:'Select value in',check:'Check',uncheck:'Uncheck',set_checked:'Set checkbox',hover:'Hover over',clear:'Clear',press_sequentially:'Type in',assert:'Check',popup:'Open popup',download:'Download files',close:'Close page'};
        const text = name(action);
        return `${verbs[action.action] || action.action}${text ? ` “${typeof text === 'string' ? text : JSON.stringify(text)}”` : ''}`;
    }
    function validatePages(definition) {
        const pages = new Set(['page']), created = new Set(), ids = new Set();
        function walk(steps) {
            for (const step of steps) {
                if (!step.id || ids.has(step.id)) throw Error('Step identities must remain unique.');
                ids.add(step.id);
                if (step.delay_before_seconds !== undefined && (!canDelay(step) || !Number.isInteger(step.delay_before_seconds) || step.delay_before_seconds < 1 || step.delay_before_seconds > 600)) throw Error('Choose 1–600 whole seconds before an action.');
                if (step.action === 'wait') continue;
                if (step.action === 'new_page') {
                    if (created.has(step.page) || (pages.has(step.page) && step.page !== 'page')) throw Error('A page cannot be opened twice.');
                    pages.add(step.page); created.add(step.page);
                } else if (!pages.has(step.page)) throw Error('This move would use a page before it opens or after it closes.');
                if (['popup','download'].includes(step.action)) {
                    if (!step.steps?.length) throw Error('Keep the event and its triggering actions together.');
                    walk(step.steps);
                } else if (step.steps?.length) throw Error('Only event groups can contain actions.');
                if (step.action === 'popup') {
                    if (!step.result_page || pages.has(step.result_page) || created.has(step.result_page)) throw Error('A popup must create its own page.');
                    pages.add(step.result_page); created.add(step.result_page);
                }
                if (step.action === 'close') { pages.delete(step.page); created.add(step.page); }
            }
        }
        walk(definition.steps);
        for (const p of Object.values(definition.parameters || {})) {
            if (p.step_id && !ids.has(p.step_id)) throw Error('A date parameter refers to a removed input.');
        }
        return definition;
    }
    function move(definition,id,index) {
        const next=clone(definition), from=next.steps.findIndex(s=>s.id===id);
        if (from<0) throw Error('Move this event group as a unit.');
        const [step]=next.steps.splice(from,1); next.steps.splice(Math.max(0,Math.min(index,next.steps.length)),0,step);
        return validatePages(next);
    }
    function remove(definition,id) {
        const next=clone(definition), step=next.steps.find(s=>s.id===id);
        if (!step) throw Error('Remove this event group as a unit.');
        const removed=all([step]), ids=new Set(removed.map(s=>s.id));
        next.steps=next.steps.filter(s=>s.id!==id);
        const removedParameters=new Set(Object.entries(next.parameters || {}).filter(([,p])=>ids.has(p.step_id)).map(([key])=>key));
        for (const key of removedParameters) delete next.parameters[key];
        for (const p of Object.values(next.parameters || {})) if (removedParameters.has(p.not_after)) delete p.not_after;
        for (const s of all(next.steps)) if (s.output?.period_checks) s.output.period_checks=s.output.period_checks.filter(c=>!removedParameters.has(c.parameter));
        const ownsTarget=t=>t && removed.some(s=>(s.action==='assert'||s.locator?.length)&&JSON.stringify(target(s))===JSON.stringify(t));
        if (ownsTarget(next.identity?.target)) next.identity={};
        if (ids.has(next.readiness?.trigger_step_id) || ownsTarget(next.readiness?.target)) next.readiness={};
        next.identity_candidates=(next.identity_candidates || []).filter(c=>!ids.has(c.step_id));
        return validatePages(next);
    }
    function owner(definition,id) { return definition.steps.find(s=>all([s]).some(child=>child.id===id)); }
    const canDuplicate = step => !['new_page','popup','close'].includes(step?.action);
    function duplicate(definition,id,makeId) {
        const next=clone(definition), index=next.steps.findIndex(s=>s.id===id);
        if (index<0) throw Error('Duplicate this event group as a unit.');
        const source=next.steps[index];
        if (!canDuplicate(source)) throw Error('Page open, popup and close steps cannot be duplicated.');
        // Fresh identities for the copy and every nested action; the copy is a
        // separate step, so nothing keeps pointing at the original by id.
        const taken=new Set(all(next.steps).map(s=>s.id));
        const fresh=original=>{
            const candidates=[...(makeId?[makeId(original)]:[]),`${original}-copy`];
            for (let n=1;;) {
                const value=candidates.length?candidates.shift():`${original}-copy-${++n}`;
                if (typeof value==='string'&&value&&!taken.has(value)) { taken.add(value); return value; }
            }
        };
        const copy=clone(source), renamed=new Map();
        for (const step of all([copy])) { const value=fresh(step.id); renamed.set(step.id,value); step.id=value; }
        for (const step of all([copy])) if (step.range?.source_step&&renamed.has(step.range.source_step.id)) step.range.source_step.id=renamed.get(step.range.source_step.id);
        // Date parameters belong to one step, so the copy gets its own under a new name.
        const parameters=next.parameters || {}, renamedParameters=new Map();
        const freshParameter=name=>{
            for (let n=2;;n++) { const value=`${name.slice(0,64-String(n).length-1)}_${n}`; if (!(value in parameters)) return value; }
        };
        for (const [name,parameter] of Object.entries(parameters)) {
            if (!renamed.has(parameter.step_id)) continue;
            const value=freshParameter(name); renamedParameters.set(name,value);
            parameters[value]={...clone(parameter),step_id:renamed.get(parameter.step_id)};
        }
        for (const name of renamedParameters.values()) { const parameter=parameters[name]; if (renamedParameters.has(parameter.not_after)) parameter.not_after=renamedParameters.get(parameter.not_after); }
        for (const step of all([copy])) if (step.output?.period_checks) step.output.period_checks=step.output.period_checks.map(check=>renamedParameters.has(check.parameter)?{...check,parameter:renamedParameters.get(check.parameter)}:check);
        if (renamedParameters.size) next.parameters=parameters;
        next.steps.splice(index+1,0,copy);
        return validatePages(next);
    }
    function rangeCandidate(definition,id) {
        const index=definition.steps.findIndex(step=>step.id===id);
        if(index<0)return null;
        const step=definition.steps[index],action=triggering(step);
        if(['download','popup'].includes(step.action)||!action.locator?.length||['select_range','set_range'].includes(action.action))return null;
        const start=isoWeek(name(action)) || '';
        return {first:index,last:index,steps:[step],weeks:start?[start]:[],start,anchor:action};
    }
    function rangeLocator(anchor,levels) {
        if(levels===0)return clone(anchor);
        const prefix=anchor.slice(0,-1);
        const usablePrefix=prefix.length&&prefix[prefix.length-1].method!=='frame_locator';
        const locator=clone(usablePrefix?prefix:anchor);
        const parentCount=usablePrefix?levels-1:levels;
        for(let index=0;index<parentCount;index++)locator.push({method:'locator',args:['xpath=..'],kwargs:{}});
        return locator;
    }
    function makeRange(definition,id,levels=1) {
        const candidate=rangeCandidate(definition,id);
        if(!candidate)throw Error('This step needs a recorded element target before it can become a range step.');
        const next=clone(definition),anchor=clone(candidate.anchor.locator || []),source=clone(candidate.steps[0]);
        const replacement={id:source.id,action:'select_range',page:candidate.anchor.page,
            locator:rangeLocator(anchor,levels),range:{unit:'week',start:candidate.start,end:'latest_selectable',selection:'inclusive',
                cell_selector:'button,[role="gridcell"],[role="option"],[role="checkbox"],input[type="checkbox"]',
                selected_state:'auto',navigation:{kind:'scroll'},anchor_locator:anchor,container_ancestor_levels:levels,
                recorded_weeks:candidate.weeks,source_step:source}};
        next.steps.splice(candidate.first,1,replacement);next.version=3;
        return validatePages(next);
    }
    function restoreRange(definition,id) {
        const next=clone(definition),index=next.steps.findIndex(step=>step.id===id),step=next.steps[index];
        if(index<0||step.action!=='select_range'||!step.range?.source_step)throw Error('This range step has no recorded action to restore.');
        next.steps.splice(index,1,clone(step.range.source_step));
        return validatePages(next);
    }
    function setRangeAncestor(step,levels) {
        const minimum=step.action==='set_range'?0:1;
        if(!['select_range','set_range'].includes(step.action)||!Number.isInteger(levels)||levels<minimum||levels>6)throw Error('Choose automatic detection or 1–6 parent levels for the element box.');
        step.range.container_ancestor_levels=levels;
        step.locator=rangeLocator(step.range.anchor_locator,levels);
        return step;
    }
    function dropParameters(next,names) {
        const dropped=new Set(names);
        for (const key of dropped) delete next.parameters?.[key];
        for (const p of Object.values(next.parameters || {})) if (dropped.has(p.not_after)) delete p.not_after;
        for (const s of all(next.steps)) if (s.output?.period_checks) s.output.period_checks=s.output.period_checks.filter(c=>!dropped.has(c.parameter));
        return next;
    }
    function renameParameter(definition,oldName,newName) {
        const next=clone(definition),parameters=next.parameters||{};
        if(!(oldName in parameters))throw Error('This date parameter no longer exists.');
        if(newName===oldName)return next;
        if(newName in parameters)throw Error('Date parameter names must be unique.');
        parameters[newName]=parameters[oldName];delete parameters[oldName];
        for (const p of Object.values(parameters)) if (p.not_after===oldName) p.not_after=newName;
        for (const s of all(next.steps)) for (const c of s.output?.period_checks||[]) if (c.parameter===oldName) c.parameter=newName;
        return next;
    }
    // A date range control: one recorded click on a slider handle becomes a step
    // that moves both handles to its start and end week parameters.
    const freshName=(parameters,base)=>{if(!(base in parameters))return base;for(let n=2;;n++){const value=`${base}_${n}`;if(!(value in parameters))return value;}};
    function sliderCandidate(definition,id) {
        const index=definition.steps.findIndex(step=>step.id===id);
        if(index<0)return null;
        const step=definition.steps[index];
        if(!interactions.has(step.action)||!step.locator?.length||step.bookmark_target)return null;
        return {index,anchor:step};
    }
    function rangeParameters(definition,id) {
        const result={};
        for (const [name,parameter] of Object.entries(definition.parameters||{})) if (parameter.step_id===id&&['start','end'].includes(parameter.role)) result[parameter.role]={name,parameter};
        return result;
    }
    function makeSlider(definition,id,levels=0) {
        const candidate=sliderCandidate(definition,id);
        if(!candidate)throw Error('This step needs a recorded element target before it can become a date range control.');
        const next=clone(definition),source=clone(candidate.anchor),anchor=clone(source.locator||[]);
        const replacement={id:source.id,action:'set_range',page:source.page,locator:rangeLocator(anchor,levels),
            range:{kind:'week',week_days:'sunday',anchor_locator:anchor,container_ancestor_levels:levels,source_step:source}};
        next.steps.splice(candidate.index,1,replacement);
        next.parameters=next.parameters||{};
        dropParameters(next,Object.keys(next.parameters).filter(name=>next.parameters[name].step_id===id));
        const start=freshName(next.parameters,'start'),end=freshName(next.parameters,'end');
        next.parameters[start]={step_id:source.id,role:'start',unit:'week',mode:'portal_default',format:'%G-W%V'};
        next.parameters[end]={step_id:source.id,role:'end',unit:'week',mode:'calculated',expression:'latest_selectable',offset_weeks:0,format:'%G-W%V'};
        next.version=requiredVersion(next);
        return validatePages(next);
    }
    function setSliderKind(definition,id,kind) {
        if(!['week','date','month'].includes(kind))throw Error('Choose week numbers, dates or months.');
        const next=clone(definition),step=all(next.steps).find(item=>item.id===id);
        if(!step||step.action!=='set_range')throw Error('This step is not a date range control.');
        const previous=step.range.kind;
        step.range.kind=kind;
        if(kind==='month')delete step.range.week_days;else step.range.week_days=step.range.week_days||'sunday';
        if((kind==='month')!==(previous==='month')){
            const parameters=rangeParameters(next,id);
            for(const role of ['start','end']){
                const parameter=parameters[role]?.parameter;
                if(!parameter)continue;
                delete parameter.value;delete parameter.expression;delete parameter.offset_weeks;delete parameter.offset_months;
                if(kind==='month'){
                    Object.assign(parameter,{unit:'month',mode:'calculated',
                        expression:role==='start'?'oldest_selectable':'latest_selectable',offset_months:0,format:'%Y%m'});
                }else{
                    Object.assign(parameter,{unit:'week',mode:role==='start'?'portal_default':'calculated',format:'%G-W%V'});
                    if(role==='end')Object.assign(parameter,{expression:'latest_selectable',offset_weeks:0});
                }
            }
        }
        next.version=requiredVersion(next);
        return validatePages(next);
    }
    function restoreSlider(definition,id) {
        const next=clone(definition),index=next.steps.findIndex(step=>step.id===id),step=next.steps[index];
        if(index<0||step.action!=='set_range'||!step.range?.source_step)throw Error('This date range control has no recorded action to restore.');
        next.steps.splice(index,1,clone(step.range.source_step));
        dropParameters(next,Object.keys(next.parameters||{}).filter(name=>next.parameters[name].step_id===id));
        return validatePages(next);
    }
    return {all,clone,target,frame,name,editableTarget,renameTarget,describe,triggering,canDelay,validatePages,move,remove,canDuplicate,duplicate,owner,rangeCandidate,makeRange,restoreRange,setRangeAncestor,
        weekText,validWeek,requiredVersion,renameParameter,sliderCandidate,rangeParameters,makeSlider,setSliderKind,restoreSlider};
})();
