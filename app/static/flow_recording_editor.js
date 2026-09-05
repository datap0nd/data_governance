/* Visual authoring uses the same versioned definition as worker/portable execution. */
window.RecordedFlowEditor = (() => {
    const M=window.RecordedFlowModel;
    const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const option=(value,label,current)=>`<option value="${h(value)}" ${String(value)===String(current)?'selected':''}>${h(label)}</option>`;
    const valueActions=['fill','press_sequentially','select_option'];
    const downloadActions=['click','dblclick','press','select_option'];
    const uid=()=>`step-${Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')}`;
    const select=(steps,current,predicate=()=>true)=>'<option value="">Choose…</option>'+steps.filter(predicate).map(s=>option(s.id,M.describe(s),current)).join('');
    function openDialog(title) {
        const el=document.createElement('dialog'); el.className='flow-recording-dialog flow-visual-dialog';
        el.innerHTML=`<header class="flow-recording-header"><h2>${h(title)}</h2><button type="button" class="btn-secondary" data-close>Close</button></header><div data-body></div><p data-error role="alert"></p>`;
        document.body.append(el); el.showModal(); el.querySelector('[data-close]').onclick=()=>el.close();
        el.addEventListener('close',()=>el.remove(),{once:true}); return el;
    }
    async function open(flowId) {
        const el=openDialog('Recorded flow'),body=el.querySelector('[data-body]'),error=el.querySelector('[data-error]');
        let data,revisionId,draft,baseline,selected,undo=[],dirty=false,pending=false,timer,serial=0,expanded=new Set(),dragged,refreshError=false;
        const prefix=`/api/flows/${flowId}/recordings`;
        const revision=()=>data?.revisions.find(r=>r.id===revisionId);
        const active=()=>data?.sessions.find(s=>['queued','claimed','running'].includes(s.status));
        const latest=()=>data?.sessions.find(s=>s.revision_id===revisionId && s.operation==='validate');
        const parse=value=>{try{return JSON.parse(value||'{}');}catch{return {};}};
        const status=()=>dirty?'Draft':active()?.operation==='validate'?'Testing':revision()?.status==='validated'?'Ready to run':revision()?.status==='failed'||latest()?.status==='failed'?'Needs attention':'Draft';
        const narrow=window.matchMedia('(max-width: 760px)');
        function placeDetails() {
            const panel=body.querySelector('.recording-details');if(!panel||!draft)return;
            const root=M.owner(draft,selected);
            if(narrow.matches&&root)body.querySelector(`[data-card="${CSS.escape(root.id)}"]`)?.after(panel);
            else body.querySelector('.recording-workspace')?.append(panel);
        }
        narrow.addEventListener('change',placeDetails);
        el.addEventListener('close',()=>{clearTimeout(timer);serial++;narrow.removeEventListener('change',placeDetails);},{once:true});
        function load(r) {
            revisionId=r?.id; draft=r?M.clone(r.definition):null; baseline=JSON.stringify(draft);
            selected=draft?.steps[0]?.id;dirty=false;undo=[];expanded=new Set();
        }
        function edit(next,selection=selected,render=true) {
            undo.push({draft:M.clone(draft),selected}); if(undo.length>100)undo.shift();
            draft=next;draft.version=2;draft.timezone='Asia/Dubai';selected=selection;
            dirty=JSON.stringify(draft)!==baseline;
            if(render)renderEditor();else {updateCards();updateButtons();}
        }
        function change(fn,render=false) {const next=M.clone(draft);fn(next);edit(next,selected,render);}
        function guarded(fn) {try{error.textContent='';fn();}catch(e){error.textContent=e.message;}}
        async function request(fn) {
            if(pending)return;pending=true;refreshError=false;error.textContent='';updateButtons();
            try {await fn();await refresh();}catch(e){error.textContent=e.message;}
            finally{pending=false;updateButtons();}
        }
        async function save() {
            const invalid=body.querySelector('[data-editor] input:invalid');
            if(invalid){invalid.reportValidity();throw Error('Correct the selected step before saving.');}
            M.validatePages(draft);
            const result=await apiPostJson(`${prefix}/revisions`,{definition:draft});
            revisionId=result.revision_id;baseline=JSON.stringify(draft);dirty=false;
            // Keep selected card and history of local edits; a save never replaces the editor DOM.
            return revisionId;
        }
        function shell() {
            body.innerHTML=`<div class="recording-toolbar"><div><strong>${h(data.flow.name)}</strong><span data-state role="status"></span></div><div><button class="btn-secondary" data-start>Record flow</button> <button class="btn-secondary" data-config>Pipeline and schedule</button> <button class="btn-secondary" data-history>History</button></div></div>
                <div data-history-panel hidden></div><p data-session role="status"></p><div data-session-actions></div><div data-editor></div>
                <footer class="recording-toolbar recording-footer"><span data-save-state></span><div><button class="btn-secondary" data-undo>Undo</button> <button class="btn-secondary" data-save>Save</button> <button class="btn-primary" data-test>Test flow</button> <button class="btn-primary" data-enable>Enable schedule</button></div></footer>`;
            body.querySelector('[data-start]').onclick=()=>request(async()=>{const r=await apiPost(`${prefix}/start`);if(r.worker?.status==='error')throw Error(r.worker.message);});
            body.querySelector('[data-config]').onclick=()=>{el.close();navigate('flows').then(()=>_flowShowView('builder',data.flow));};
            body.querySelector('[data-history]').onclick=()=>{const panel=body.querySelector('[data-history-panel]');panel.hidden=!panel.hidden;renderHistory();};
            body.querySelector('[data-undo]').onclick=()=>{const previous=undo.pop();if(previous){draft=previous.draft;selected=previous.selected;dirty=JSON.stringify(draft)!==baseline;renderEditor();}};
            body.querySelector('[data-save]').onclick=()=>request(save);
            body.querySelector('[data-test]').onclick=()=>request(async()=>{
                // Suggestions returned by the API may not yet exist in immutable storage.
                const id=await save();const result=await apiPost(`${prefix}/revisions/${id}/validate`);
                if(result.worker?.status==='error')throw Error(result.worker.message);
            });
            body.querySelector('[data-enable]').onclick=()=>request(async()=>{
                if(dirty || revision()?.status!=='validated')throw Error('Test these changes before enabling the schedule.');
                await apiPost(`${prefix}/revisions/${revisionId}/activate`);
                if(data.flow.schedule_type==='manual') {
                    error.textContent='Flow tested and selected. Choose a schedule in Pipeline and schedule, then enable it.';
                    return;
                }
                await apiPatch(`/api/flows/${flowId}/enabled`,{enabled:true});
            });
        }
        function renderHistory() {
            const panel=body.querySelector('[data-history-panel]');
            if(!panel || panel.hidden)return;
            panel.innerHTML=`<p>Saved versions are immutable. Viewing history does not change the running version.</p>${data.revisions.map(r=>`<button class="btn-secondary" data-version="${r.id}">${h(typeof formatDate==='function'?formatDate(r.created_at):r.created_at)} · ${h(r.status)}${r.id===data.flow.recording_revision_id?' · active':''}</button>`).join(' ')}`;
            panel.querySelectorAll('[data-version]').forEach(button=>{button.disabled=dirty||Boolean(active())||pending;button.onclick=()=>{load(data.revisions.find(r=>r.id===Number(button.dataset.version)));renderEditor();};});
        }
        function renderEditor() {
            const host=body.querySelector('[data-editor]');
            if(!draft){host.innerHTML='<p>Record through your completed downloads, then choose Finish recording to review the flow.</p>';updateButtons();return;}
            if('date_batch' in draft){
                host.innerHTML='<form data-convert><p role="alert">Date batching has been removed. This schedule is paused. Enter one explicit range to create an ordinary recording, then test it.</p><label>Start date <input name="start" required placeholder="Recorded date format"></label><label>End date <input name="end" required placeholder="Recorded date format"></label><button class="btn-primary">Convert to one range</button></form>';
                host.querySelector('form').onsubmit=e=>{e.preventDefault();request(async()=>{const r=await apiPostJson(`${prefix}/revisions/${revisionId}/convert-single-range`,{start:e.target.elements.start.value,end:e.target.elements.end.value});revisionId=r.revision_id;draft=null;});};updateButtons();return;
            }
            if(!M.all(draft.steps).some(s=>s.id===selected))selected=draft.steps[0]?.id;
            host.innerHTML=`<div class="recording-report"><span data-report-title>${h(draft.identity?.text || 'Choose report title')}</span> <button class="btn-secondary" data-title>Change</button>${!draft.readiness?.trigger_step_id?'<button class="btn-secondary" data-readiness>Choose report completion check</button>':''}</div><div data-title-panel hidden></div>
                <div class="recording-workspace"><div class="recording-sequence" aria-label="Recorded steps">${cards()}</div><aside class="recording-details" aria-label="Selected step details"></aside></div>`;
            host.querySelector('[data-title]').onclick=()=>{const panel=host.querySelector('[data-title-panel]');panel.hidden=!panel.hidden;if(!panel.hidden)renderTitle(panel);};
            host.querySelector('[data-readiness]')?.addEventListener('click',()=>{selected=draft.readiness?.trigger_step_id || draft.steps.find(s=>['click','goto'].includes(s.action))?.id;expanded.add('readiness');renderDetails();updateCards();});
            bindCards();renderDetails();updateButtons();
        }
        function cards() {
            return draft.steps.map((step,i)=>`<div class="recording-gap"><span aria-hidden="true">${i?'↓':''}</span><button type="button" data-insert="${i}" aria-label="Insert wait before step ${i+1}">+</button></div>
                <article class="recording-card" data-card="${h(step.id)}"><span class="recording-number">${i+1}</span><button type="button" class="recording-card-title" data-select="${h(step.id)}">${h(M.describe(step))}</button>${step.action==='download'?'<span class="recording-badge">Download</span>':''}<span class="recording-outcome" role="status"></span><button type="button" class="recording-drag" draggable="true" data-drag="${h(step.id)}" aria-label="Drag step ${i+1}">⠿</button></article>`).join('')+`<div class="recording-gap"><span aria-hidden="true">↓</span><button type="button" data-insert="${draft.steps.length}" aria-label="Insert wait at end">+</button></div>`;
        }
        function bindCards() {
            body.querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>{selected=b.dataset.select;renderDetails();updateCards();});
            body.querySelectorAll('[data-insert]').forEach(b=>b.onclick=()=>guarded(()=>{const next=M.clone(draft),step={id:uid(),page:'page',action:'wait',seconds:5};next.steps.splice(Number(b.dataset.insert),0,step);edit(next,step.id);}));
            body.querySelectorAll('[data-drag]').forEach(b=>{
                b.ondragstart=e=>{if(pending||active()){e.preventDefault();return;}dragged=b.dataset.drag;e.dataTransfer.setData('text/plain',dragged);};
                b.ondragend=()=>dragged=null;
            });
            body.querySelectorAll('[data-card]').forEach(card=>{
                card.ondragover=e=>{if(dragged)e.preventDefault();};
                card.ondrop=e=>{e.preventDefault();if(!dragged)return;guarded(()=>edit(M.move(draft,dragged,draft.steps.findIndex(s=>s.id===card.dataset.card)),dragged));dragged=null;};
            });updateCards();
        }
        function updateCards() {
            if(!draft)return;
            const progress=parse(latest()?.progress_json),outcomes=progress.step_outcomes || {};
            body.querySelectorAll('[data-card]').forEach(card=>{
                const step=draft.steps.find(s=>s.id===card.dataset.card);if(!step)return;
                const own=M.owner(draft,selected);card.classList.toggle('selected',own?.id===step.id);
                card.querySelector('[data-select]').setAttribute('aria-pressed',String(own?.id===step.id));
                card.querySelector('[data-select]').textContent=M.describe(step);
                const children=M.all([step]),failed=children.map(s=>outcomes[s.id]).find(o=>o?.outcome==='failed');
                const outcome=dirty?null:failed || outcomes[step.id];
                let state=outcome?.outcome==='failed'?'failed':outcome?.outcome==='completed'?'completed':['started','running','ready'].includes(outcome?.outcome)?'running':'';
                if(outcome?.outcome==='cancelled'||(state==='running'&&!['running','queued','claimed'].includes(latest()?.status)))state='interrupted';
                card.dataset.outcome=state;card.querySelector('.recording-outcome').textContent=state==='running'?'Running…':state==='completed'?'Completed':state==='failed'?'Needs attention':state==='interrupted'?'Not completed':'';
            });
        }
        function renderTitle(panel) {
            const candidates=draft.identity_candidates || [],steps=M.all(draft.steps);
            panel.innerHTML=`<label>Suggested report title <select data-title-candidate><option value="">Choose or enter manually</option>${candidates.map((c,i)=>option(i,c.text,'')).join('')}</select></label><label>Exact report title <input data-title-text value="${h(draft.identity?.text)}"></label><details><summary>Advanced</summary><label>Page/frame from step <select data-title-frame>${select(steps,steps.find(s=>JSON.stringify(M.frame(s))===JSON.stringify(M.frame(draft.identity?.target)))?.id)}</select></label></details><button class="btn-secondary" data-apply-title>Use report title</button><p>The next test verifies this exact title. Choose the report name, not a portal name or download button.</p>`;
            panel.querySelector('[data-title-candidate]').onchange=e=>{if(e.target.value!=='')panel.querySelector('[data-title-text]').value=candidates[Number(e.target.value)].text;};
            panel.querySelector('[data-apply-title]').onclick=()=>guarded(()=>{
                const text=panel.querySelector('[data-title-text]').value.trim();if(!text)throw Error('Enter the report title.');
                const chosen=panel.querySelector('[data-title-candidate]').value,candidate=chosen!==''?candidates[Number(chosen)]:null;
                let identity;
                if(candidate?.text===text)identity={text,kind:candidate.kind,target:M.clone(candidate.target)};
                else {const source=steps.find(s=>s.id===panel.querySelector('[data-title-frame]').value);if(!source)throw Error('Choose the page/frame containing this title.');const target=M.frame(source);target.locator.push({method:'get_by_text',args:[text],kwargs:{exact:true}});identity={text,target};}
                change(d=>{d.identity=identity;},true);
            });
        }
        function renderDetails() {
            const panel=body.querySelector('.recording-details');if(!panel)return;
            const step=M.all(draft.steps).find(s=>s.id===selected),root=M.owner(draft,selected);if(!step){panel.innerHTML='Select a step.';return;}
            const action=M.triggering(step),index=draft.steps.indexOf(root),steps=M.all(draft.steps);
            const [parameterName,parameter]=Object.entries(draft.parameters || {}).find(([,p])=>p.step_id===action.id) || ['',{}];
            const output=step.action==='download'?step.output:null,ready=draft.readiness || {};
            const associated=steps.find(s=>s.action==='download'&&s.id!==step.id&&M.all(s.steps||[]).some(child=>child.id===step.id));
            const outcomes=parse(latest()?.progress_json).step_outcomes || {};
            const outcome=M.all([step]).map(s=>outcomes[s.id]).find(o=>o?.outcome==='failed') || outcomes[step.id] || outcomes[action.id];
            panel.innerHTML=`<div class="recording-detail-heading"><h3>Step ${index+1}</h3>${step===root?`<button class="btn-secondary" data-up aria-label="Move up" ${index===0?'disabled':''}>↑</button><button class="btn-secondary" data-down aria-label="Move down" ${index===draft.steps.length-1?'disabled':''}>↓</button><button class="btn-secondary" data-remove>Remove</button>`:'<button class="btn-secondary" data-parent>Back to group</button>'}</div>
                <p data-step-failure role="alert">${outcome?.outcome==='failed'?h(outcome.message):''}</p>
                ${action.repair_reason?`<p role="alert">${h(action.repair_reason)}</p>`:''}
                <section><h4>Action</h4><label>Step name <input data-label maxlength="160" value="${h(step.label || '')}" placeholder="${h(M.describe(step))}"></label><p>${h(M.describe(action))}</p>
                ${action.locator?.length?`<p class="recording-target">Target: ${h(M.name(action))}</p>`:''}
                ${valueActions.includes(action.action)||action.action==='press'||action.action==='goto'?`<label>${action.action==='goto'?'Address':'Entered value'} <input data-value value="${h(typeof action.args?.[0]==='string'?action.args[0]:'')}" ${action.args?.[0] && typeof action.args[0]!=='string'?'disabled':''}></label>`:''}
                ${step.steps?.length && action===step?`<details data-section="group" ${expanded.has('group')?'open':''}><summary>${step.steps.length} actions in this event group</summary>${step.steps.map(child=>`<button type="button" class="recording-group-action" data-child="${h(child.id)}">${h(M.describe(child))}</button>`).join('')}<p>This group moves as one unit to preserve its event listener.</p></details>`:''}</section>
                <section><h4>Options</h4>
                ${action.action==='wait'?`<label>Wait in seconds <input data-seconds type="number" min="1" max="600" step="1" value="${action.seconds}"></label><p>Waits supplement the report completion check.</p>`:''}
                ${downloadActions.includes(action.action)||output?`<label><input data-download type="checkbox" ${output||associated?'checked':''} ${(output&&action===step)||associated?'disabled':''}> This action produces a download</label>${associated?`<button class="btn-secondary" data-associated="${h(associated.id)}">Edit download group</button>`:''}`:''}
                ${output?`<label>Output format <select data-format>${['xlsx','csv','html','txt'].map(v=>option(v,v,output.format)).join('')}</select></label><label><input data-empty type="checkbox" ${output.allow_empty?'checked':''}> Allow an empty report</label>`:''}
                ${valueActions.includes(action.action)?`<label>Date behavior <select data-date-mode>${option('','Use recorded value',parameter.mode||'')}${option('fixed','Fixed date',parameter.mode)}${option('portal_default','Portal default — leave untouched',parameter.mode)}${option('calculated','Calculated date',parameter.mode)}</select></label><div data-date-options ${parameter.mode?'':'hidden'}><label>Parameter name <input data-date-name value="${h(parameterName || action.id.replaceAll('-','_'))}"></label><label ${parameter.mode==='fixed'?'':'hidden'}>Fixed date <input data-date-value value="${h(parameter.value??action.args?.[0])}"></label><label ${parameter.mode==='calculated'?'':'hidden'}>Calculation <select data-date-expression>${['today','yesterday','month_start','previous_month_start','previous_month_end','year_start','week_start'].map(v=>option(v,v.replaceAll('_',' '),parameter.expression)).join('')}</select></label><label>Date format <select data-date-format>${['%Y-%m-%d','%d/%m/%Y','%m/%d/%Y','%Y%m%d'].map(v=>option(v,v,parameter.format||'%Y-%m-%d')).join('')}</select></label></div>`:''}
                </section><details data-section="advanced" ${expanded.has('advanced')||expanded.has('readiness')||outcome?.outcome==='failed'?'open':''}><summary>Advanced</summary><p>Page: ${h(action.page)}</p><code class="recording-locator">${h(JSON.stringify(action.locator || []))}</code>
                ${action.locator?.length?`<label>Repair target <select data-repair-kind>${option('','Keep recorded target','')}${option('text','Exact visible text','')}${option('label','Exact input label','')}${option('css','Stable CSS selector','')}</select></label><input data-repair-value aria-label="Replacement target"><button class="btn-secondary" data-repair>Apply target repair</button><label>Expected element text <input data-expected value="${h(action.expected_text)}"></label>`:''}
                ${output?`<label>Expected columns, in order <input data-headers value="${h((output.headers||[]).join(', '))}"></label><label>Download completion <select data-completion>${option('native','Browser download',output.completion||'native')}${option('staging','Verified staging fallback',output.completion)}</select></label><label>Period column <input data-period-column value="${h(output.period_checks?.[0]?.column)}"></label><label>Period parameter <input data-period-parameter value="${h(output.period_checks?.[0]?.parameter)}"></label>`:''}
                ${valueActions.includes(action.action)?`<label>Date must not be after parameter <input data-not-after value="${h(parameter.not_after)}"></label>`:''}
                <details data-section="readiness" ${expanded.has('readiness')?'open':''}><summary>Report completion check</summary><label>Generate report using <select data-ready-trigger>${select(steps,ready.trigger_step_id,s=>['goto','click','press','select_option'].includes(s.action))}</select></label><label>Completion signal <select data-ready-mode>${option('','Choose…',ready.mode||'')}${option('navigation','Report document navigation',ready.mode)}${option('loading_cycle','Loading appears, then disappears',ready.mode)}${option('changed_text','Result text changes',ready.mode)}</select></label><label>Signal element from step <select data-ready-target>${select(steps,steps.find(s=>JSON.stringify(M.target(s))===JSON.stringify(ready.target))?.id,s=>s.locator?.length)}</select></label><p>A clickable button or a fixed wait does not prove that results are current.</p></details></details>`;
            placeDetails();
            bindDetails(panel,step,action,root,parameterName);
        }
        function bindDetails(panel,step,action,root,parameterName) {
            const get=d=>M.all(d.steps).find(s=>s.id===action.id),getStep=d=>M.all(d.steps).find(s=>s.id===step.id);
            const bind=(selector,fn,event='change')=>{const node=panel.querySelector(selector);if(node)node.addEventListener(event,e=>guarded(()=>fn(e.target)));};
            panel.querySelector('[data-up]')?.addEventListener('click',()=>guarded(()=>{edit(M.move(draft,root.id,draft.steps.findIndex(s=>s.id===root.id)-1));body.querySelector('[data-up]:not(:disabled)')?.focus();}));
            panel.querySelector('[data-down]')?.addEventListener('click',()=>guarded(()=>{edit(M.move(draft,root.id,draft.steps.findIndex(s=>s.id===root.id)+1));body.querySelector('[data-down]:not(:disabled)')?.focus();}));
            panel.querySelector('[data-remove]')?.addEventListener('click',()=>guarded(()=>edit(M.remove(draft,root.id))));
            panel.querySelector('[data-associated]')?.addEventListener('click',e=>{selected=e.target.dataset.associated;renderDetails();});
            panel.querySelector('[data-parent]')?.addEventListener('click',()=>{selected=root.id;renderDetails();});
            panel.querySelectorAll('[data-child]').forEach(b=>b.onclick=()=>{selected=b.dataset.child;renderDetails();updateCards();});
            panel.querySelectorAll('[data-section]').forEach(d=>d.ontoggle=()=>{if(d.open)expanded.add(d.dataset.section);else expanded.delete(d.dataset.section);});
            bind('[data-label]',n=>change(d=>getStep(d).label=n.value),'input');
            bind('[data-value]',n=>change(d=>get(d).args=[n.value]),'input');
            bind('[data-seconds]',n=>{const v=Number(n.value);if(!Number.isInteger(v)||v<1||v>600)throw Error('Choose 1–600 whole seconds.');change(d=>get(d).seconds=v);});
            bind('[data-expected]',n=>change(d=>get(d).expected_text=n.value),'input');
            panel.querySelector('[data-repair]')?.addEventListener('click',()=>guarded(()=>{const kind=panel.querySelector('[data-repair-kind]').value,text=panel.querySelector('[data-repair-value]').value;if(!kind||!text)throw Error('Choose a target type and enter its value.');change(d=>{const node=get(d),target=M.frame(node);target.locator.push({method:{text:'get_by_text',label:'get_by_label',css:'locator'}[kind],args:[text],kwargs:kind==='css'?{}:{exact:true}});Object.assign(node,target);delete node.repair_reason;},true);}));
            bind('[data-download]',n=>{
                const next=M.clone(draft),node=M.all(next.steps).find(s=>s.id===step.id);
                const container=node===next.steps.find(s=>s.id===node.id)?next.steps:M.all(next.steps).find(s=>s.steps?.some(c=>c.id===node.id)).steps;
                const index=container.indexOf(node);
                if(n.checked){const wrapper={id:uid(),action:'download',page:node.page,locator:[],steps:[node],output:{format:'xlsx',headers:[],allow_empty:false}};container[index]=wrapper;edit(next,wrapper.id);}
                else {const child=node.steps[0];if(node.label)child.label=node.label;container[index]=child;edit(next,child.id);}
            });
            for(const [field,key] of [['format','format'],['completion','completion'],['headers','headers'],['empty','allow_empty']])bind(`[data-${field}]`,n=>change(d=>{getStep(d).output[key]=field==='empty'?n.checked:field==='headers'?n.value.split(',').map(s=>s.trim()).filter(Boolean):n.value;}));
            for(const field of ['period-column','period-parameter'])bind(`[data-${field}]`,()=>change(d=>{const output=getStep(d).output,column=panel.querySelector('[data-period-column]').value,parameter=panel.querySelector('[data-period-parameter]').value;output.period_checks=column||parameter?[{column,parameter},...(output.period_checks||[]).slice(1)]:[];}));
            for(const field of ['date-mode','date-name','date-value','date-expression','date-format','not-after'])bind(`[data-${field}]`,()=>{
                const read=f=>panel.querySelector(`[data-${f}]`)?.value,mode=read('date-mode'),name=read('date-name');
                change(d=>{d.parameters ||= {};const old=Object.keys(d.parameters).find(k=>d.parameters[k].step_id===action.id);
                    if(name!==old&&d.parameters[name])throw Error('Date parameter names must be unique.');
                    const previous=old?d.parameters[old]:{};if(old)delete d.parameters[old];
                    if(mode)d.parameters[name]={...previous,step_id:action.id,mode,value:read('date-value'),expression:read('date-expression'),format:read('date-format'),not_after:read('not-after')||undefined};
                    if(old&&!mode){for(const p of Object.values(d.parameters))if(p.not_after===old)delete p.not_after;for(const s of M.all(d.steps))if(s.output?.period_checks)s.output.period_checks=s.output.period_checks.filter(c=>c.parameter!==old);}
                    if(old&&old!==name){for(const p of Object.values(d.parameters))if(p.not_after===old)p.not_after=name;for(const s of M.all(d.steps))for(const c of s.output?.period_checks||[])if(c.parameter===old)c.parameter=name;}
                },field==='date-mode');
            });
            for(const field of ['ready-trigger','ready-mode','ready-target'])bind(`[data-${field}]`,()=>change(d=>{const source=M.all(d.steps).find(s=>s.id===panel.querySelector('[data-ready-target]').value);d.readiness={trigger_step_id:panel.querySelector('[data-ready-trigger]').value,mode:panel.querySelector('[data-ready-mode]').value,target:source?M.target(source):{}};}));
        }
        function updateButtons() {
            if(!data)return;const busy=pending||Boolean(active()),batch=draft&&'date_batch' in draft;
            for(const [selector,disabled] of [['start',busy],['config',busy],['save',busy||!draft||batch],['test',busy||!draft||batch],['enable',busy||dirty||revision()?.status!=='validated'||batch],['undo',busy||!undo.length]]) {const button=body.querySelector(`[data-${selector}]`);if(button)button.disabled=Boolean(disabled);}
            body.querySelector('[data-state]').textContent=status();
            body.querySelector('[data-save-state]').textContent=dirty?'Unsaved changes':data.flow.enabled?'Schedule enabled':'Schedule paused';
            body.querySelector('[data-test]').textContent=active()?.operation==='validate'?'Testing…':'Test flow';
            body.querySelectorAll('[data-editor] input,[data-editor] select,[data-editor] button:not([data-select]):not([data-child]):not([data-parent])').forEach(n=>{if(busy){if(!n.disabled)n.dataset.busyDisabled='1';n.disabled=true;}else if(n.dataset.busyDisabled){n.disabled=false;delete n.dataset.busyDisabled;}});
            updateCards();renderHistory();
            const failure=body.querySelector('[data-step-failure]');
            if(failure&&!dirty){const current=M.owner(draft||{steps:[]},selected),outcomes=parse(latest()?.progress_json).step_outcomes||{};
                const failed=current&&M.all([current]).map(s=>outcomes[s.id]).find(o=>o?.outcome==='failed');
                failure.textContent=failed?.message||'';
            }
        }
        async function refresh() {
            clearTimeout(timer);const requestId=++serial;
            try {
                const incoming=await api(prefix);if(!el.isConnected||requestId!==serial)return;
                const previous=data?.revisions[0]?.id;data=incoming;
                if(refreshError){error.textContent='';refreshError=false;}
                const arrived=data.revisions[0]?.id;
                if(!body.querySelector('[data-start]')){load(data.revisions[0]);shell();renderEditor();}
                else if(!draft || (!dirty && previous!==arrived && revisionId!==arrived)) {load(data.revisions.find(r=>r.id===revisionId)||data.revisions[0]);if(previous!==arrived)load(data.revisions[0]);renderEditor();}
                const session=active()||data.sessions[0],progress=parse(session?.progress_json);
                body.querySelector('[data-session]').textContent=session?`${session.operation==='record'?'Recording':'Test'}: ${session.status} ${session.error||progress.message||''}`:'';
                const controls=body.querySelector('[data-session-actions]'),current=active();
                if(controls.dataset.session!==String(current?.scan_id||'')){
                controls.dataset.session=String(current?.scan_id||'');
                controls.innerHTML=current?`${current.operation==='record'?'<p>Download every required file and wait for completion. Then choose <strong>Finish recording</strong> here. Playwright’s red square only pauses recording.</p><button class="btn-primary" data-finish>Finish recording</button> ':''}<button class="btn-secondary" data-cancel>${current.cancel_requested?'Force close recording':'Cancel '+(current.operation==='record'?'recording':'test')}</button>`:'';
                }
                const finish=controls.querySelector('[data-finish]');if(finish){finish.textContent=current.finish_requested?'Finishing recording…':'Finish recording';finish.disabled=pending||current.finish_requested||current.cancel_requested||progress.stage!=='recording';finish.onclick=()=>request(()=>apiPost(`${prefix}/${current.scan_id}/finish`));}
                const cancel=controls.querySelector('[data-cancel]');if(cancel){cancel.textContent=current.cancel_requested?(Date.now()-Date.parse(current.cancel_requested)>=10000?'Force close recording':'Cancelling…'):'Cancel '+(current.operation==='record'?'recording':'test');cancel.disabled=pending||Boolean(current.cancel_requested&&Date.now()-Date.parse(current.cancel_requested)<10000);cancel.onclick=()=>request(()=>apiPost(`${prefix}/${current.scan_id}/cancel`));}
                updateButtons();
            }catch(e){refreshError=true;if(el.isConnected)error.textContent=`Could not refresh status: ${e.message}. Retrying…`;}
            if(el.isConnected)timer=setTimeout(refresh,3000);
        }
        await refresh();
    }
    return {open};
})();
