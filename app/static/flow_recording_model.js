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
    function rangeCandidate(definition,id) {
        const index=definition.steps.findIndex(step=>step.id===id);
        if(index<0)return null;
        const step=definition.steps[index],action=triggering(step);
        if(['download','popup'].includes(step.action)||!action.locator?.length||action.action==='select_range')return null;
        const start=isoWeek(name(action)) || '';
        return {first:index,last:index,steps:[step],weeks:start?[start]:[],start,anchor:action};
    }
    function rangeLocator(anchor,levels) {
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
        if(step.action!=='select_range'||!Number.isInteger(levels)||levels<1||levels>6)throw Error('Choose 1–6 parent levels for the element box.');
        step.range.container_ancestor_levels=levels;
        step.locator=rangeLocator(step.range.anchor_locator,levels);
        return step;
    }
    return {all,clone,target,frame,name,editableTarget,renameTarget,describe,triggering,canDelay,validatePages,move,remove,owner,rangeCandidate,makeRange,restoreRange,setRangeAncestor};
})();
