/* Recording authoring stays data-only. No recorded Python is evaluated here. */
window.FlowRecordings = (() => {
    const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    const all = steps => steps.flatMap(step => [step, ...all(step.steps || [])]);
    const option = (value, label, current) => `<option value="${h(value)}" ${value === current ? 'selected' : ''}>${h(label)}</option>`;
    const target = step => ({page: step.page || 'page', locator: structuredClone(step.locator || [])});
    const describe = step => {
        const labels = {new_page:'Open page',goto:'Navigate',click:'Click',dblclick:'Double click',fill:'Fill',press:'Press',select_option:'Select',check:'Check',uncheck:'Uncheck',set_checked:'Set checkbox',hover:'Hover',clear:'Clear',press_sequentially:'Type',assert:'Check expected result',popup:'Open popup',download:'Download',close:'Close page'};
        const named = [...(step.locator || [])].reverse().find(part => part.kwargs?.name || part.args?.length);
        const value = step.label || named?.kwargs?.name || named?.args?.[0] || (step.action === 'goto' ? step.args?.[0] : step.result_page || '');
        return `${step.id}: ${labels[step.action] || step.action} ${typeof value === 'object' ? JSON.stringify(value) : value}`.trim();
    };
    const selectSteps = (steps, current, predicate = () => true) => steps.filter(predicate).map(step => option(step.id, describe(step), current)).join('');

    function dialog(title) {
        const element = document.createElement('dialog');
        element.className = 'flow-recording-dialog';
        element.innerHTML = `<div class="flow-recording-header"><h2>${h(title)}</h2><button class="btn-secondary" data-close type="button">Close</button></div><div data-body></div><p data-error role="alert"></p>`;
        document.body.append(element); element.showModal();
        element.querySelector('[data-close]').onclick = () => element.close();
        element.addEventListener('close', () => element.remove(), {once:true});
        return element;
    }

    async function create() {
        const element = dialog('Record a portal flow');
        const body = element.querySelector('[data-body]');
        try {
            const catalog = await api('/api/flows/catalog');
            const sites = catalog.sites.filter(site => site.enabled && ['asap_portal', 'gscm_portal'].includes(site.adapter));
            if (!sites.length) throw new Error('Add an ASAP or GSCM website in Catalog first. A catalog scan is not required.');
            body.innerHTML = `<form class="flow-form-grid"><label>Name <input name="name" required maxlength="160"></label><label>Website <select name="site">${sites.map(site => option(String(site.id), site.name, '')).join('')}</select></label><label class="flow-span-2">Starting report URL (optional)<input name="route" type="url" placeholder="Leave empty to start at the portal"></label><p class="flow-span-2">Metronome opens the starting page on one worker. Complete sign-in if prompted, navigate to the report, set filters and download the required file(s). Wait for every download to complete, then click Finish recording here. To repeat the same report over date ranges, record one range and configure date batches during review.</p><button class="btn-primary" type="submit">Create draft</button></form>`;
            body.querySelector('form').onsubmit = async event => {
                event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true;
                try {
                    const saved = await apiPostJson('/api/flows/recordings/draft', {name:form.elements.name.value, site_id:Number(form.elements.site.value), report_url:form.elements.route.value || null});
                    element.close(); await navigate('flows'); await open(saved.id);
                } catch (error) { element.querySelector('[data-error]').textContent = error.message; button.disabled = false; }
            };
        } catch (error) { element.querySelector('[data-error]').textContent = error.message; }
    }

    function editor(definition) {
        const steps = all(definition.steps), readiness = definition.readiness || {};
        const firstDownload = steps.findIndex(step => step.action === 'download');
        const navigation = steps.filter((step, i) => step.action === 'goto' && (firstDownload < 0 || i < firstDownload)).at(-1);
        const params = definition.parameters || {};
        return `<form data-review>
            ${firstDownload < 0 ? '<p role="alert">No download was captured. Record through a completed download, or mark its recorded triggering action as a download below if the portal already delivered the file. This draft cannot be activated until its download passes validation.</p>' : ''}
            <div class="flow-form-grid">
                <label>Report title (exact visible text) <input name="identity" required value="${h(definition.identity?.text)}"></label>
                <label>Title is in the same page/frame as <select name="identityFrame">${selectSteps(steps, steps.find(step => JSON.stringify(target(step)) === JSON.stringify(definition.identity?.target))?.id)}</select></label>
                <label>Report generation action <select name="trigger">${selectSteps(steps, readiness.trigger_step_id || navigation?.id, step => ['goto','click','press','select_option'].includes(step.action))}</select></label>
                <label>Completion signal <select name="readyMode">${option('navigation','Report document navigation completes',readiness.mode || 'navigation')}${option('loading_cycle','Loading indicator appears, then disappears',readiness.mode)}${option('changed_text','Result text changes after generation',readiness.mode)}</select></label>
                <label>Loading/result element from recorded step <select name="readyTarget">${selectSteps(steps, steps.find(step => JSON.stringify(target(step)) === JSON.stringify(readiness.target))?.id, step => step.locator?.length)}</select></label>
            </div>
            <p>For a report that calculates after navigation, choose its Run/Generate action and an observed loading indicator or changing result value. Record an assertion on that element so it is available in the list. Clickability alone does not establish completion.</p>
            <ol>${steps.map(step => {
                const [name, parameter] = Object.entries(params).find(([,p]) => p.step_id === step.id) || ['',{}];
                return `<li data-step="${h(step.id)}" style="padding:12px 0;border-bottom:1px solid var(--border,#ddd)"><strong>${h(describe(step))}</strong>
                    ${step.repair_reason ? `<p role="alert">${h(step.repair_reason)}</p>` : ''}
                    ${step.locator_note ? `<p>${h(step.locator_note)}</p>` : ''}
                    ${step.locator?.length ? `<details><summary>Repair locator / expected element</summary><label>Replacement locator <select data-repair-kind>${option('','Keep recorded locator','')}${option('text','Exact visible text','')}${option('label','Exact input label','')}${option('css','Stable CSS selector','')}</select></label> <input data-repair-value aria-label="Replacement locator value"><label>Expected element text <input data-expected value="${h(step.expected_text)}"></label><small>The replacement keeps the recorded page/frame. Ambiguous matches fail validation.</small></details>` : ''}
                    ${['click','press'].includes(step.action) && !steps.some(parent => parent.action === 'download' && all(parent.steps || []).includes(step)) ? '<label><input type="checkbox" data-download> This action produces a download</label>' : ''}
                    ${step.action === 'download' ? `<div class="flow-form-grid"><label>Output format <select data-format>${['xlsx','csv','html','txt'].map(fmt => option(fmt,fmt,step.output.format)).join('')}</select></label><label>Expected columns (comma separated, in order) <input data-headers value="${h((step.output.headers || []).join(', '))}"></label><label><input type="checkbox" data-empty ${step.output.allow_empty ? 'checked' : ''}> Allow an empty report</label><label>Download completion <select data-completion>${option('native','Browser download completion',step.output.completion || 'native')}${option('staging','Verified staging fallback',step.output.completion)}</select></label><label>Period column <input data-period-column value="${h(step.output.period_checks?.[0]?.column)}" placeholder="Optional report date column"></label><label>Must match parameter <input data-period-parameter value="${h(step.output.period_checks?.[0]?.parameter)}" placeholder="For example: start"></label></div>` : ''}
                    ${['fill','select_option','press_sequentially'].includes(step.action) ? `<div class="flow-form-grid"><label>Date parameter <select data-date-mode>${option('','Use recorded value',parameter.mode ? '_' : '')}${option('fixed','Fixed date',parameter.mode)}${option('portal_default','Portal default (leave untouched)',parameter.mode)}${option('calculated','Calculated date',parameter.mode)}</select></label><label>Parameter name <input data-date-name value="${h(name || step.id.replaceAll('-','_'))}"></label><label>Fixed date <input data-date-value value="${h(parameter.value || step.args?.[0])}"></label><label>Calculation <select data-date-expression>${['today','yesterday','month_start','previous_month_start','previous_month_end','year_start','week_start'].map(v => option(v,v.replaceAll('_',' '),parameter.expression)).join('')}</select></label><label>Date format <select data-date-format>${['%Y-%m-%d','%d/%m/%Y','%m/%d/%Y','%Y%m%d'].map(v => option(v,v,parameter.format || '%Y-%m-%d')).join('')}</select></label><label>Must not be after parameter <input data-date-end value="${h(parameter.not_after)}" placeholder="Optional end parameter name"></label></div>` : ''}
                </li>`;
            }).join('')}</ol>
            <p>To keep an end date at the portal default, select “Portal default” on its recorded input step. The replay omits that write and logs the value supplied by the portal.</p>
            <fieldset><legend>Repeat this report over date ranges</legend>
                <label><input type="checkbox" name="batchEnabled" ${definition.date_batch ? 'checked' : ''}> Download in date batches</label>
                <p>Record one representative range, including both date fields and its download(s). Choose fixed or calculated dates above for the complete range. Each batch repeats all recorded steps and downloads, with an inclusive end date and no gaps. All files are collected before transformation or SQL.</p>
                <div class="flow-form-grid"><label>Start parameter name <input name="batchStart" value="${h(definition.date_batch?.start_parameter || 'start')}"></label><label>End parameter name <input name="batchEnd" value="${h(definition.date_batch?.end_parameter || 'end')}"></label><label>Weeks per file batch <input name="batchWeeks" type="number" min="1" max="52" value="${h(definition.date_batch?.weeks || 10)}"></label></div>
                <p>Example: 2025-01-01 through 2026-12-31 in 10-week batches. The final batch is shorter. Use calculated “today” for a moving end; a portal-default boundary cannot be split reliably. Batch files receive a part number.</p>
            </fieldset>
            <button class="btn-primary" type="submit">Save reviewed revision</button>
        </form>`;
    }

    function frameTarget(step) {
        const locator = step?.locator || [];
        let end = -1;
        locator.forEach((part, i) => { if (['frame_locator','content_frame'].includes(part.method)) end = i; });
        return {page:step?.page || 'page', locator:structuredClone(locator.slice(0,end + 1))};
    }

    function collect(form, original) {
        const definition = structuredClone(original), steps = all(definition.steps);
        definition.timezone = 'Asia/Dubai';
        const identityFrame = steps.find(step => step.id === form.elements.identityFrame.value);
        const identityTarget = frameTarget(identityFrame);
        identityTarget.locator.push({method:'get_by_text',args:[form.elements.identity.value],kwargs:{exact:true}});
        definition.identity = {text:form.elements.identity.value,target:identityTarget};
        const readyTarget = steps.find(step => step.id === form.elements.readyTarget.value);
        definition.readiness = {trigger_step_id:form.elements.trigger.value,mode:form.elements.readyMode.value,target:readyTarget ? target(readyTarget) : {}};
        for (const step of steps) {
            const row = [...form.querySelectorAll('[data-step]')].find(el => el.dataset.step === step.id);
            const value = name => row.querySelector(`[data-${name}]`)?.value;
            if (value('repair-kind') && value('repair-value')) {
                const replacement = frameTarget(step), kind = value('repair-kind');
                replacement.locator.push({method:{text:'get_by_text',label:'get_by_label',css:'locator'}[kind],args:[value('repair-value')],kwargs:kind === 'css' ? {} : {exact:true}});
                Object.assign(step,replacement); delete step.repair_reason;
            }
            if (row.querySelector('[data-expected]')) step.expected_text = value('expected') || undefined;
            if (step.action === 'download') {
                Object.assign(step.output,{format:value('format'),headers:value('headers').split(',').map(v => v.trim()).filter(Boolean),allow_empty:row.querySelector('[data-empty]').checked,completion:value('completion')});
                const column = value('period-column').trim(), parameter = value('period-parameter').trim();
                if (Boolean(column) !== Boolean(parameter)) throw new Error('A period check needs both a column and parameter name.');
                step.output.period_checks = column ? [{column,parameter}, ...(step.output.period_checks || []).slice(1)] : [];
            }
            const oldName = Object.keys(definition.parameters || {}).find(name => definition.parameters[name].step_id === step.id);
            if (oldName) delete definition.parameters[oldName];
            if (value('date-mode')) {
                definition.parameters ||= {};
                const name = value('date-name');
                if (definition.parameters[name]) throw new Error('Date parameter names must be unique.');
                definition.parameters[name] = {mode:value('date-mode'),step_id:step.id,value:value('date-value'),expression:value('date-expression'),format:value('date-format'),not_after:value('date-end') || undefined};
            }
            if (row.querySelector('[data-download]')?.checked) {
                const action = structuredClone(step); action.id += '-trigger';
                Object.assign(step,{action:'download',steps:[action],locator:[],output:{format:'xlsx',headers:[],allow_empty:false}});
                delete step.args; delete step.kwargs;
            }
        }
        if (form.elements.batchEnabled.checked) {
            definition.date_batch = {start_parameter:form.elements.batchStart.value.trim(),end_parameter:form.elements.batchEnd.value.trim(),weeks:Number(form.elements.batchWeeks.value)};
        } else delete definition.date_batch;
        return definition;
    }

    async function open(flowId) {
        const element = dialog('Recorded flow'), body = element.querySelector('[data-body]'), errorBox = element.querySelector('[data-error]');
        let selected, data, timer, dirty = false, pending = false, refreshVersion = 0, refreshError = false;
        element.addEventListener('close', () => clearTimeout(timer), {once:true});
        async function action(path, payload) {
            if (pending) return;
            pending = true; updateButtons();
            refreshError = false;
            errorBox.textContent = '';
            try {
                const result = await (payload === undefined ? apiPost(path) : apiPostJson(path,payload));
                if (result?.worker?.status === 'error') throw new Error(result.worker.message);
                dirty = false; await refresh(true);
            }
            catch (error) { errorBox.textContent = error.message; }
            finally { pending = false; updateButtons(); if (element.isConnected && !timer) timer = setTimeout(() => refresh(),3000); }
        }
        async function refresh(render = false) {
            clearTimeout(timer); timer = null;
            const version = ++refreshVersion;
            try {
                const previousRevision = data?.revisions?.[0]?.id;
                const latestData = await api(`/api/flows/${flowId}/recordings`);
                if (!element.isConnected || version !== refreshVersion) return;
                data = latestData;
                if (refreshError) { errorBox.textContent = ''; refreshError = false; }
                render ||= !body.querySelector('[data-start]');
                if (!dirty && previousRevision !== data.revisions[0]?.id) { selected = data.revisions[0]?.id; render = true; }
                selected ||= data.revisions[0]?.id;
                const revision = data.revisions.find(item => item.id === Number(selected));
                const active = data.sessions.find(item => ['queued','claimed','running'].includes(item.status));
                const latest = active || data.sessions[0];
                if (render) {
                    body.innerHTML = `<p><strong>${h(data.flow.name)}</strong> · ${h(data.flow.source_adapter === 'gscm_portal' ? 'GSCM' : 'ASAP')} · active revision: ${h(data.flow.recording_revision_id || 'none')}</p><p>Browser: global Flows Settings. The recording window opens on the worker PC. Closing this review window leaves a running recording in progress.</p><button class="btn-primary" data-start type="button">Record flow</button> <button class="btn-secondary" data-config type="button">Pipeline and schedule settings</button><p data-session role="status"></p><div data-session-actions></div>
                        <label>Revision <select data-revision>${data.revisions.map(item => option(String(item.id), `Revision ${item.id} · ${item.status}`,String(selected))).join('')}</select></label>
                        ${revision ? `<p data-revision-status>Revision ${revision.id}: ${h(revision.status)}</p>${editor(revision.definition)}<p>Validation starts a fresh browser and saves evidence in the Flow’s private validation folder. It runs downloads and the saved Python transformation; it does not publish production files or execute SQL.</p><button class="btn-secondary" data-validate type="button">Validate saved revision</button> <button class="btn-primary" data-activate type="button">Activate validated revision</button>` : '<p>Start a recording to create the first revision.</p>'}`;
                    body.querySelector('[data-start]').onclick = () => action(`/api/flows/${flowId}/recordings/start`);
                    body.querySelector('[data-config]').onclick = async () => { element.close(); await navigate('flows'); _flowShowView('builder', data.flow); };
                    body.querySelector('[data-revision]').onchange = event => { selected = Number(event.target.value); dirty = false; refresh(true); };
                    const form = body.querySelector('[data-review]');
                    if (form) {
                        form.oninput = () => { dirty = true; updateButtons(); };
                        form.onsubmit = async event => { event.preventDefault(); try { await action(`/api/flows/${flowId}/recordings/revisions`,{definition:collect(form,revision.definition)}); } catch (error) { errorBox.textContent = error.message; } };
                        body.querySelector('[data-validate]').onclick = () => action(`/api/flows/${flowId}/recordings/revisions/${selected}/validate`);
                        body.querySelector('[data-activate]').onclick = () => action(`/api/flows/${flowId}/recordings/revisions/${selected}/activate`);
                    }
                }
                const status = body.querySelector('[data-session]');
                if (status) status.textContent = latest ? `${latest.operation}: ${latest.status} — ${latest.error || JSON.parse(latest.progress_json || '{}').message || ''}` : 'Ready to record.';
                const sessionActions = body.querySelector('[data-session-actions]');
                if (sessionActions && sessionActions.dataset.session !== String(active?.scan_id || '')) {
                    sessionActions.dataset.session = String(active?.scan_id || '');
                    sessionActions.innerHTML = active ? `${active.operation === 'record' ? '<button class="btn-primary" data-finish type="button">Finish recording</button> ' : ''}<button class="btn-secondary" data-cancel type="button">Cancel ${h(active.operation)}</button>${active.operation === 'record' ? '<ol><li>Use the report page Metronome opened. Navigate and set its filters.</li><li>Download the required file or files. Wait until every download completes.</li><li>Click <strong>Finish recording</strong> here to close the recording windows and review your steps.</li></ol><p>Playwright’s red square pauses recording; it does not finish this session. Closing Chrome may leave its Inspector open. Use Finish recording here, or Cancel to discard the unsaved recording.</p>' : ''}` : '';
                    sessionActions.querySelector('[data-cancel]')?.addEventListener('click', () => action(`/api/flows/${flowId}/recordings/${active.scan_id}/cancel`));
                    sessionActions.querySelector('[data-finish]')?.addEventListener('click', () => action(`/api/flows/${flowId}/recordings/${active.scan_id}/finish`));
                }
                const revisionStatus = body.querySelector('[data-revision-status]');
                if (revisionStatus && revision) revisionStatus.textContent = `Revision ${revision.id}: ${revision.status}${dirty ? ' · unsaved changes' : ''}`;
                updateButtons();
                if (active) timer = setTimeout(() => refresh(),3000);
            } catch (error) {
                if (version !== refreshVersion || !element.isConnected) return;
                errorBox.textContent = `Could not refresh recording status: ${error.message}. Retrying…`;
                refreshError = true;
                timer = setTimeout(() => refresh(),3000);
            }
        }
        function updateButtons() {
            const active = data?.sessions.find(item => ['queued','claimed','running'].includes(item.status));
            const revision = data?.revisions.find(item => item.id === Number(selected));
            const start = body.querySelector('[data-start]'); if (start) start.disabled = pending || Boolean(active);
            const config = body.querySelector('[data-config]'); if (config) config.disabled = pending || Boolean(active);
            const validate = body.querySelector('[data-validate]'); if (validate) validate.disabled = pending || active || dirty;
            const activate = body.querySelector('[data-activate]'); if (activate) activate.disabled = pending || active || dirty || revision?.status !== 'validated';
            const save = body.querySelector('[data-review] button[type=submit]'); if (save) save.disabled = pending || Boolean(active);
            const finish = body.querySelector('[data-finish]');
            if (finish) {
                finish.disabled = pending || !active || active.finish_requested || active.cancel_requested || JSON.parse(active.progress_json || '{}').stage !== 'recording';
                finish.textContent = active?.finish_requested ? 'Finishing recording…' : 'Finish recording';
            }
            const cancel = body.querySelector('[data-cancel]');
            if (cancel) {
                const force = active?.cancel_requested && Date.now() - Date.parse(active.cancel_requested) >= 10000;
                cancel.disabled = pending || Boolean(active?.cancel_requested && !force);
                cancel.textContent = force ? 'Force close recording' : active?.cancel_requested ? 'Cancelling…' : `Cancel ${active?.operation || 'recording'}`;
            }
        }
        await refresh(true);
    }
    return {create,open};
})();
