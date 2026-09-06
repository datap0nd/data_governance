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
        const part = [...(step.locator || [])].reverse().find(p => p.kwargs?.name || ['get_by_text','get_by_label','get_by_title','get_by_placeholder','get_by_alt_text'].includes(p.method));
        return part?.kwargs?.name || part?.args?.[0] || (step.locator?.length ? 'recorded element' : '');
    };
    function describe(step) {
        if (step.label) return step.label;
        const action = triggering(step);
        if (action.label) return action.label;
        if (action.action === 'assert' && typeof action.args?.[0] === 'string') return `Check “${action.args[0]}”`;
        if (action.action === 'wait') return `Wait ${action.seconds} seconds`;
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
    return {all,clone,target,frame,name,describe,triggering,canDelay,validatePages,move,remove,owner};
})();
