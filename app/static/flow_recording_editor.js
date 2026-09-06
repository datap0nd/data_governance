/* Visual authoring uses the same versioned definition as worker/portable execution. */
window.RecordedFlowEditor = (() => {
    const M=window.RecordedFlowModel;
    const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const option=(value,label,current)=>`<option value="${h(value)}" ${String(value)===String(current)?'selected':''}>${h(label)}</option>`;
    const valueActions=['fill','press_sequentially','select_option'];
    const downloadActions=['click','dblclick','press','select_option'];
    const uid=()=>`step-${Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')}`;
    const select=(steps,current,predicate=()=>true)=>'<option value="">Choose…</option>'+steps.filter(predicate).map(s=>option(s.id,M.describe(s),current)).join('');
    const drafts=new Map();
    function openPage(flowId) {
        const workspace=document.getElementById('flow-workspace');
        const el=document.createElement('section'); el.className='flow-recording-page';
        el.innerHTML='<button type="button" class="btn-secondary" data-close>Back to Edit Flow</button><div data-body></div>';
        const previous=document.createDocumentFragment();
        if(workspace){while(workspace.firstChild)previous.append(workspace.firstChild);workspace.append(el);if(window._flowsState)window._flowsState.view='recording';}
        else document.body.append(el);
        el.close=()=>{el.dispatchEvent(new Event('close'));el.remove();if(workspace){workspace.replaceChildren(previous);if(window._flowsState)window._flowsState.view='builder';if(!workspace.querySelector('#flow-builder-form')&&typeof _flowShowView==='function')_flowShowView('builder',window._flowsState.flows.find(f=>f.id===flowId));}};
        el.querySelector('[data-close]').onclick=()=>el.close();
        return el;
    }
    async function open(flowId, settings=null) {
        const el=openPage(flowId),body=el.querySelector('[data-body]');
        let error;
        let data,revisionId,draft,baseline,selected,undo=[],dirty=false,pending=false,operation=null,timer,serial=0,expanded=new Set(),dragged,refreshError=false;
        const prefix=`/api/flows/${flowId}/recordings`;
        const revision=()=>data?.revisions.find(r=>r.id===revisionId);
        const active=()=>data?.sessions.find(s=>['queued','claimed','running'].includes(s.status));
        const latest=()=>data?.sessions.find(s=>s.revision_id===revisionId && s.operation==='validate');
        const parse=value=>{try{return JSON.parse(value||'{}');}catch{return {};}};
        const status=()=>active()?.operation==='record'?'Recording…':draft?`${draft.steps.length} steps recorded`:'Ready to record';
        const narrow=window.matchMedia('(max-width: 760px)');
        function placeDetails() {
            const panel=body.querySelector('.recording-details');if(!panel||!draft)return;
            const root=M.owner(draft,selected);
            if(root)body.querySelector(`[data-card="${CSS.escape(root.id)}"]`)?.after(panel);
            else {panel.hidden=true;body.querySelector('.recording-workspace')?.append(panel);} if(root)panel.hidden=false;
        }
        narrow.addEventListener('change',placeDetails);
        el.addEventListener('close',()=>{clearTimeout(timer);serial++;narrow.removeEventListener('change',placeDetails);},{once:true});
        function load(r) {
            revisionId=r?.id; draft=r?M.clone(r.definition):null; baseline=JSON.stringify(draft);
            selected=null;dirty=false;undo=[];expanded=new Set();
        }
        function edit(next,selection=selected,render=true) {
            undo.push({draft:M.clone(draft),selected}); if(undo.length>100)undo.shift();
            draft=next;draft.version=2;draft.timezone='Asia/Dubai';selected=selection;
            dirty=JSON.stringify(draft)!==baseline;
            if(settings)delete settings.recording_revision_id;window._flowRecordingSelections?.set(flowId,null);
            remember();
            if(render)renderEditor();else {updateCards();updateButtons();}
        }
        function change(fn,render=false) {const next=M.clone(draft);fn(next);edit(next,selected,render);}
        function remember(){if(draft)drafts.set(flowId,{draft:M.clone(draft),revisionId,baseline,selected,undo:M.clone(undo),dirty});}
        el.addEventListener('close',()=>{remember();if(settings?.recording_revision_id&&typeof window._flowAcceptRecording==='function')window._flowAcceptRecording(flowId,settings.recording_revision_id);});
        function showError(message){error.textContent=message;error.scrollIntoView({block:'nearest'});}
        function guarded(fn) {try{error.textContent='';fn();}catch(e){showError(e.message);}}
        async function request(fn) {
            if(pending)return;const invalid=body.querySelector('input:invalid');if(invalid){invalid.reportValidity();return;}pending=true;refreshError=false;error.textContent='';updateButtons();
            try {await fn();if(el.isConnected)await refresh();}catch(e){showError(e.message);}
            finally{pending=false;operation=null;updateButtons();}
        }
        async function save() {
            operation='saving';updateButtons();
            const invalid=body.querySelector('[data-editor] input:invalid');
            if(invalid){invalid.reportValidity();throw Error('Correct the selected step before saving.');}
            M.validatePages(draft);
            const result=await apiPostJson(`${prefix}/revisions`,{definition:draft});
            if(!el.isConnected)return null;
            revisionId=result.revision_id;baseline=JSON.stringify(draft);dirty=false;remember();body.querySelector('[data-save-state]').textContent='Draft saved';
            // Keep selected card and history of local edits; a save never replaces the editor DOM.
            return revisionId;
        }
        function shell() {
            body.innerHTML=`<div class="recording-toolbar"><div><h1>${h(settings?.name||data.flow.name)}</h1><span data-state role="status"></span></div><details><summary>More</summary><button class="btn-secondary" data-start>Record again</button> <button class="btn-secondary" data-history>Saved versions</button></details></div>
                <div data-history-panel hidden></div><div class="recording-actions"><button class="btn-primary" data-test>Test recording</button> <button class="btn-secondary" data-save>Save draft</button><span data-save-state role="status"></span><p data-error role="alert"></p><p data-session role="status"></p><div data-session-actions></div><div data-check></div></div><div data-editor></div>`;
            error=body.querySelector('[data-error]');
            body.querySelector('[data-start]').onclick=()=>request(async()=>{remember();if(draft&&dirty){await save();if(!el.isConnected)return;}const r=await apiPost(`${prefix}/start`);if(r.worker?.status==='error')throw Error(r.worker.message);});
            body.querySelector('[data-history]').onclick=()=>{const panel=body.querySelector('[data-history-panel]');panel.hidden=!panel.hidden;renderHistory();};
            body.querySelector('[data-save]').onclick=()=>request(save);
            body.querySelector('[data-test]').onclick=()=>{
                if(!checkBeforeTest())return;
                request(async()=>{
                    const id=await save();if(!id||!el.isConnected)return;operation='testing';updateButtons();const result=await apiPostJson(`${prefix}/revisions/${id}/validate`,{settings});
                    if(!el.isConnected)return;
                    if(result.revision_id)revisionId=result.revision_id;
                    remember();
                    if(result.worker?.status==='error')throw Error(result.worker.message);
                });
            };
        }
        function checkBeforeTest(){
            const host=body.querySelector('[data-check]');host.innerHTML='';
            if(!draft)return false;
            if(!draft.identity?.text || (draft.identity.kind!=='page_title'&&!draft.identity.target?.locator?.length)){host.innerHTML='<h3>Check the recorded page</h3>';renderTitle(host);host.querySelector('input')?.focus();return false;}
            if(!draft.readiness?.trigger_step_id || !draft.readiness?.mode || (draft.readiness.mode!=='navigation'&&!draft.readiness.target?.locator?.length)){
                const steps=M.all(draft.steps),ready=draft.readiness||{};
                host.innerHTML=`<h3>How do we know the report is ready?</h3><label>Run report action<select data-check-trigger>${select(steps,ready.trigger_step_id,s=>['goto','click','press','select_option'].includes(s.action))}</select></label><label>Wait until<select data-check-mode>${option('','Choose…',ready.mode||'')}${option('navigation','The report page opens',ready.mode)}${option('loading_cycle','Loading appears, then disappears',ready.mode)}${option('changed_text','A result value changes',ready.mode)}</select></label><label data-check-target-label>Watch this recorded element<select data-check-target>${select(steps,'',s=>s.locator?.length)}</select></label><button class="btn-primary" data-check-apply>Continue</button>`;
                const mode=host.querySelector('[data-check-mode]');mode.onchange=()=>host.querySelector('[data-check-target-label]').hidden=mode.value==='navigation';mode.onchange();
                host.querySelector('[data-check-apply]').onclick=()=>guarded(()=>{const id=host.querySelector('[data-check-trigger]').value,target=steps.find(s=>s.id===host.querySelector('[data-check-target]').value),trigger=steps.find(s=>s.id===id);if(!id||!mode.value||(mode.value!=='navigation'&&!target))throw Error('Choose the action and what to wait for.');if(mode.value==='navigation'&&trigger.action!=='goto')throw Error('Choose an Open page action, or use a loading/result check.');change(d=>d.readiness={trigger_step_id:id,mode:mode.value,...(target?{target:M.target(target)}:{})});host.innerHTML='';body.querySelector('[data-test]').click();});
                host.querySelector('select').focus();return false;
            }
            return true;
        }
        function renderHistory() {
            const panel=body.querySelector('[data-history-panel]');
            if(!panel || panel.hidden)return;
            panel.innerHTML=`<p>${dirty?'Save your draft before opening another version.':'Opening a saved version keeps the active recording unchanged.'}</p>${data.revisions.map(r=>`<button class="btn-secondary" data-version="${r.id}">${h(typeof formatDate==='function'?formatDate(r.created_at):r.created_at)} · ${h(r.status)}${r.id===data.flow.recording_revision_id?' · active':''}</button>`).join(' ')}`;
            panel.querySelectorAll('[data-version]').forEach(button=>{button.disabled=dirty||Boolean(active())||pending;button.onclick=()=>{load(data.revisions.find(r=>r.id===Number(button.dataset.version)));if(settings)delete settings.recording_revision_id;window._flowRecordingSelections?.set(flowId,null);renderEditor();};});
        }
        function renderEditor() {
            const host=body.querySelector('[data-editor]');
            if(!draft){host.innerHTML='<p>Open your report, run it, then download the files.</p><button class="btn-primary" data-begin>Start recording</button>';host.querySelector('[data-begin]').onclick=()=>body.querySelector('[data-start]').click();updateButtons();return;}
            if('date_batch' in draft){
                host.innerHTML='<form data-convert><p role="alert">Date batching has been removed. This schedule is paused. Enter one explicit range to create an ordinary recording, then test it.</p><label>Start date <input name="start" required placeholder="Recorded date format"></label><label>End date <input name="end" required placeholder="Recorded date format"></label><button class="btn-primary">Convert to one range</button></form>';
                host.querySelector('form').onsubmit=e=>{e.preventDefault();request(async()=>{const r=await apiPostJson(`${prefix}/revisions/${revisionId}/convert-single-range`,{start:e.target.elements.start.value,end:e.target.elements.end.value});revisionId=r.revision_id;draft=null;});};updateButtons();return;
            }
            if(selected&&!M.all(draft.steps).some(s=>s.id===selected))selected=undo.length?draft.steps[0]?.id:null;
            host.innerHTML=`<div class="recording-workspace"><div class="recording-sequence" aria-label="Recorded steps">${cards()}</div><aside class="recording-details" aria-label="Selected step details" hidden></aside></div>`;
            bindCards();renderDetails();updateButtons();
        }
        function cards() {
            return draft.steps.map((step,i)=>`<div class="recording-gap"><span aria-hidden="true">${i?'↓':''}</span><button type="button" data-insert="${i}" aria-label="Insert wait before step ${i+1}">+</button></div>
                <article class="recording-card" data-card="${h(step.id)}"><span class="recording-number">${i+1}</span><button type="button" class="recording-card-title" data-select="${h(step.id)}">${h(M.describe(step))}</button>${step.action==='download'?'<span class="recording-badge">Download</span>':''}<span class="recording-outcome" role="status"></span><button type="button" class="recording-drag" draggable="true" data-drag="${h(step.id)}" aria-label="Drag step ${i+1}">⠿</button></article>`).join('')+`<div class="recording-gap"><span aria-hidden="true">↓</span><button type="button" data-insert="${draft.steps.length}" aria-label="Insert wait at end">+</button></div>`;
        }
        function bindCards() {
            body.querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>{selected=selected===b.dataset.select?null:b.dataset.select;renderDetails();updateCards();});
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
            const frames=[...new Map(steps.map(step=>[JSON.stringify(M.frame(step)),step])).values()];
            panel.innerHTML=`<h3>Check the recorded page</h3><label>Recorded text <select data-title-candidate><option value="">Choose or enter manually</option>${candidates.map((c,i)=>option(i,c.text,'')).join('')}</select></label><label>Text to check <input data-title-text value="${h(draft.identity?.text)}"></label><label ${frames.length===1?'hidden':''}>Where is the title? Use the page from <select data-title-frame>${select(steps,steps.find(s=>JSON.stringify(M.frame(s))===JSON.stringify(M.frame(draft.identity?.target)))?.id)}</select></label><button class="btn-secondary" data-apply-title>Use this check</button><p>Choose text that identifies the page you recorded.</p>`;
            panel.querySelector('[data-title-candidate]').onchange=e=>{if(e.target.value!=='')panel.querySelector('[data-title-text]').value=candidates[Number(e.target.value)].text;};
            panel.querySelector('[data-apply-title]').onclick=()=>guarded(()=>{
                const text=panel.querySelector('[data-title-text]').value.trim();if(!text)throw Error('Enter the report title.');
                const chosen=panel.querySelector('[data-title-candidate]').value,candidate=chosen!==''?candidates[Number(chosen)]:null;
                let identity;
                if(candidate?.text===text)identity={text,kind:candidate.kind,target:M.clone(candidate.target)};
                else {const source=frames.length===1?frames[0]:steps.find(s=>s.id===panel.querySelector('[data-title-frame]').value);if(!source)throw Error('Choose the page/frame containing this title.');const target=M.frame(source);target.locator.push({method:'get_by_text',args:[text],kwargs:{exact:true}});identity={text,target};}
                change(d=>{d.identity=identity;},true);panel.innerHTML='';body.querySelector('[data-test]').click();
            });
        }
        function renderDetails() {
            const panel=body.querySelector('.recording-details');if(!panel)return;
            const step=M.all(draft.steps).find(s=>s.id===selected),root=M.owner(draft,selected);if(!step){panel.hidden=true;return;}panel.hidden=false;
            const action=M.triggering(step),index=draft.steps.indexOf(root),steps=M.all(draft.steps);
            const [parameterName,parameter]=Object.entries(draft.parameters || {}).find(([,p])=>p.step_id===action.id) || ['',{}];
            const output=step.action==='download'?step.output:null,ready=draft.readiness || {};
            const associated=steps.find(s=>s.action==='download'&&s.id!==step.id&&M.all(s.steps||[]).some(child=>child.id===step.id));
            const outcomes=parse(latest()?.progress_json).step_outcomes || {};
            const outcome=M.all([step]).map(s=>outcomes[s.id]).find(o=>o?.outcome==='failed') || outcomes[step.id] || outcomes[action.id];
            panel.innerHTML=`<div class="recording-detail-heading"><h3>Step ${index+1}</h3>${undo.length?'<button class="btn-secondary" data-undo>Undo</button>':''}<button class="btn-secondary" data-done>Done</button>${step===root?`<button class="btn-secondary" data-up aria-label="Move up" ${index===0?'disabled':''}>↑</button><button class="btn-secondary" data-down aria-label="Move down" ${index===draft.steps.length-1?'disabled':''}>↓</button><button class="btn-secondary" data-remove>Remove</button>`:'<button class="btn-secondary" data-parent>Back to group</button>'}</div>
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
            panel.querySelector('[data-undo]')?.addEventListener('click',()=>{const previous=undo.pop();if(previous){draft=previous.draft;selected=previous.selected;dirty=JSON.stringify(draft)!==baseline;remember();renderEditor();}});
            panel.querySelector('[data-done]').onclick=()=>{selected=null;renderDetails();updateCards();};
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
            for(const [selector,disabled] of [['start',busy],['begin',busy],['save',busy||!draft||batch],['test',busy||!draft||batch],['enable',busy||dirty||revision()?.status!=='validated'||batch],['undo',busy||!undo.length]]) {const button=body.querySelector(`[data-${selector}]`);if(button)button.disabled=Boolean(disabled);}
            body.querySelector('[data-state]').textContent=status();
            if(dirty)body.querySelector('[data-save-state]').textContent='Unsaved changes';
            body.querySelector('[data-save]').textContent=operation==='saving'?'Saving…':'Save draft';
            body.querySelector('[data-save]').hidden=!draft;body.querySelector('[data-test]').hidden=!draft;
            body.querySelector('[data-start]').textContent=draft?'Record again':'Start recording';
            body.querySelector('[data-test]').textContent=active()?.operation==='validate'?'Testing…':'Test recording';
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
                const previous=data?.revisions[0]?.id,wasRecording=active()?.operation==='record';data=incoming;
                if(refreshError){error.textContent='';refreshError=false;}
                const arrived=data.revisions[0]?.id;
                if(!body.querySelector('[data-start]')){load(data.revisions[0]);const kept=drafts.get(flowId);if(kept?.dirty){({draft,revisionId,baseline,selected,undo,dirty}=kept);}shell();renderEditor();}
                else if(!draft || (wasRecording && previous!==arrived) || (!dirty && previous!==arrived && revisionId!==arrived)) {load(data.revisions.find(r=>r.id===revisionId)||data.revisions[0]);if(previous!==arrived)load(data.revisions[0]);if(wasRecording){if(settings)delete settings.recording_revision_id;window._flowRecordingSelections?.set(flowId,null);}renderEditor();}
                const session=active()||data.sessions[0],progress=parse(session?.progress_json);
                const sessionMessage=!session?'':session.status==='succeeded'?(session.operation==='validate'?'Test passed. Back to Edit Flow, then Save.':''):
                    session.error||(session.status==='failed'?(['failed','worker_start_failed'].includes(progress.stage)&&progress.message||'Could not complete this session. Try again.'):
                    session.status==='cancelled'?(session.operation==='validate'?'Test cancelled.':'Recording cancelled.'):
                    progress.message||({queued:'Waiting for worker…',claimed:'Opening browser…',running:session.operation==='validate'?'Testing recording…':'Recording…'}[session.status]||''));
                body.querySelector('[data-session]').textContent=sessionMessage;
                if(session?.operation==='validate'&&session.status==='succeeded'&&session.revision_id===revisionId&&!dirty){
                    const form=document.getElementById('flow-builder-form');
                    // The builder DOM is detached while recording is open; the callback
                    // applies only a revision, never settings or schedule state.
                    if(settings)settings.recording_revision_id=revisionId;
                    el.dataset.testedRevision=revisionId;
                }

                const controls=body.querySelector('[data-session-actions]'),current=active();
                if(controls.dataset.session!==String(current?.scan_id||'')){
                controls.dataset.session=String(current?.scan_id||'');
                controls.innerHTML=current?`${current.operation==='record'?'<p>Download every required file and wait for completion. Then choose <strong>Finish recording</strong> here. Playwright’s red square only pauses recording.</p><button class="btn-primary" data-finish>Finish recording</button> ':''}<button class="btn-secondary" data-cancel>${current.cancel_requested?'Force close recording':'Cancel '+(current.operation==='record'?'recording':'test')}</button>`:'';
                }
                const finish=controls.querySelector('[data-finish]');if(finish){finish.textContent=current.finish_requested?'Finishing recording…':'Finish recording';finish.disabled=pending||current.finish_requested||current.cancel_requested||progress.stage!=='recording';finish.onclick=()=>request(()=>apiPost(`${prefix}/${current.scan_id}/finish`));}
                const cancel=controls.querySelector('[data-cancel]');if(cancel){cancel.textContent=current.cancel_requested?(Date.now()-Date.parse(current.cancel_requested)>=10000?'Force close recording':'Cancelling…'):'Cancel '+(current.operation==='record'?'recording':'test');cancel.disabled=pending||Boolean(current.cancel_requested&&Date.now()-Date.parse(current.cancel_requested)<10000);cancel.onclick=()=>request(()=>apiPost(`${prefix}/${current.scan_id}/cancel`));}
                updateButtons();
            }catch(e){refreshError=true;if(el.isConnected){if(!error){body.innerHTML='<p data-error role="alert"></p>';error=body.querySelector('[data-error]');}error.textContent=`Connection lost. Your edits are kept. Retrying…`;}}
            if(el.isConnected)timer=setTimeout(refresh,3000);
        }
        await refresh();
    }
    return {open};
})();
