// Review-only prototype. Reuses the actual Flow table and row renderers;
// topic interactions and runs are fictional, held solely in this tab.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
for (const src of ['/static/users.js', '/static/app.js']) await new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src; script.onload = resolve; script.onerror = reject;
    document.head.append(script);
});

const $p = selector => document.querySelector(selector);
const data = {flows: [], topics: [], runs: [], open: new Set(), feedback: new Map(), nextTopic: 2, nextRun: 100};
const catalog = {sites: [], reports: [], asap_download_types: [{key: 'excel', label: 'Excel workbook'}]};
let editor = null;
const moduleOf = flow => _flowRowModel(flow, [], catalog).group;
const isActive = run => ['queued', 'claimed', 'running'].includes(run.status);
const activeFor = id => data.runs.find(run => run.flow_id === id && isActive(run));
const members = topic => data.flows.filter(flow => topic.ids.includes(flow.id));
const topicOf = id => data.topics.find(topic => topic.ids.includes(id));
const notice = (message, error = false) => {
    $p('#groups-feedback').textContent = message;
    $p('#groups-feedback').classList.toggle('is-error', error);
};
function fixture(id, name, module = 'ASAP') {
    return {id, name, source_type: module === 'Outlook' ? 'outlook' : module === 'Local' ? 'file' : 'portal',
        source_adapter: module === 'ASAP' ? 'asap_portal' : 'gscm_portal', site_name: module,
        report_name: name, category_path: ['Reporting', name], classification: 'production',
        local_file_path: 'C:/Example/inventory.xlsx', outlook_subject_contains: 'Weekly inventory',
        owner_name: 'Alex Morgan', owner_person_id: 1, enabled: true, schedule_type: 'daily',
        browser_mode: 'headless', file_format: 'xlsx', period_strategy: 'none',
        folder_relative: `${module}/${name}`, sql_handoff_enabled: true, sql_database: 'analytics',
        sql_schema: 'public', sql_table: name.toLowerCase().replaceAll(/[^a-z0-9]+/g, '_'), sql_mode: 'replace',
        last_status: 'succeeded', last_run_at: '2026-09-13T07:00:00Z'};
}
function reset() {
    data.flows = ['M Tracker Country', 'M Tracker Subs', 'Photo', 'Wi-Fi', 'TI'].map((name, i) => fixture(i + 1, name));
    data.flows.push(fixture(6, 'Inventory', 'GSCM'), fixture(7, 'Weekly summary', 'Outlook'), fixture(8, 'Stock file', 'Local'));
    data.topics = [{id: 1, name: 'M Tracker', module: 'ASAP', ids: [1, 2]}];
    data.runs = []; data.open = new Set(); data.feedback = new Map(); data.nextTopic = 2;
    window._flowOpenGroupMemory = new Set(['ASAP']); window._flowSortMemory = null;
    $p('#topic-dialog').close(); $p('#existing-dialog').close(); editor = null;
    document.querySelectorAll('.groups-preview-options input').forEach(input => input.checked = false);
    notice(''); render();
}
function topicHtml(topic) {
    const flows = members(topic), active = flows.filter(flow => activeFor(flow.id)).length;
    const failed = flows.filter(flow => flow.last_status === 'failed' && !activeFor(flow.id)).length;
    const feedback = data.feedback.get(topic.id);
    const status = feedback ? `<span class="${feedback.error ? 'flow-error' : ''}" role="status">${esc(feedback.message)}</span>`
        : active ? `${active} of ${flows.length} active` : failed ? `${failed} failed · Expand to review` : 'Run together · Each flow keeps its settings';
    const expanded = data.open.has(topic.id);
    return `<tr class="topic-heading" data-topic-id="${topic.id}">
        <td class="flow-name-cell" colspan="3"><button type="button" class="topic-toggle" data-topic-toggle="${topic.id}" aria-expanded="${expanded}" aria-label="${esc(topic.name)} group"><span class="topic-arrow" aria-hidden="true">${expanded ? '▾' : '▸'}</span>${esc(topic.name)}<span class="flow-group-count">${flows.length} flow${flows.length === 1 ? '' : 's'}</span></button></td>
        <td colspan="5" class="topic-summary">${status}</td>
        <td class="flow-row-actions"><button type="button" class="btn-sm topic-run" data-topic-run="${topic.id}" ${active ? 'disabled' : ''} title="${active ? 'Wait for the active flows to finish, or run an idle flow individually.' : 'Queue every flow in this group together.'}">${active ? 'In progress' : 'Run group'}</button>
        <details class="flow-row-menu"><summary aria-label="More actions: ${esc(topic.name)} group">More</summary><div><button type="button" class="btn-sm" data-topic-edit="${topic.id}">Edit group</button><button type="button" class="btn-sm" data-topic-ungroup="${topic.id}">Ungroup</button></div></details></td></tr>`;
}
function render() {
    window._flowsState = {flows: data.flows, classification: 'production', catalog, people: [{id: 1, name: 'Alex Morgan'}]};
    const workspace = $p('#flow-workspace');
    workspace.innerHTML = _flowListHtml(data.flows, [{status: 'online'}, {status: 'online'}], catalog, data.runs);
    workspace.querySelectorAll('.flow-group-toggle').forEach(button => {
        const module = button.dataset.group;
        const head = button.parentElement;
        const wrapper = document.createElement('div'); wrapper.className = 'group-source-heading';
        head.replaceChildren(wrapper); wrapper.append(button);
        const create = document.createElement('button'); create.type = 'button'; create.className = 'btn-sm btn-outline';
        create.textContent = 'Group'; create.setAttribute('aria-label', `Group flows in ${module}`);
        create.onclick = () => openEditor(module); wrapper.append(create);
        button.onclick = () => {const open = _flowOpenGroups(); open.has(module) ? open.delete(module) : open.add(module); render();};
        const body = document.getElementById(`flow-group-rows-${module}`);
        const rows = _flowSortRows(data.flows.filter(flow => moduleOf(flow) === module).map(flow => _flowRowModel(flow, data.runs, catalog)), _flowSortState());
        const seen = new Set(); let html = '';
        for (const row of rows) {
            const topic = topicOf(row.flow.id);
            if (topic) {
                if (seen.has(topic.id)) continue;
                seen.add(topic.id); html += topicHtml(topic);
                if (data.open.has(topic.id)) html += rows.filter(member => topic.ids.includes(member.flow.id) && !member.activeRun)
                    .map(member => _flowRowHtml(member).replace('<tr ', '<tr class="topic-member" ')).join('');
            } else if (!row.activeRun) html += _flowRowHtml(row);
        }
        body.innerHTML = html;
    });
    workspace.querySelectorAll('[data-topic-toggle]').forEach(button => button.onclick = () => {
        const id = Number(button.dataset.topicToggle); data.open.has(id) ? data.open.delete(id) : data.open.add(id); render();
        $p(`[data-topic-toggle="${id}"]`)?.focus();
    });
    workspace.querySelectorAll('[data-topic-edit]').forEach(button => button.onclick = () => {
        const topic = data.topics.find(item => item.id === Number(button.dataset.topicEdit)); openEditor(topic.module, topic);
    });
    workspace.querySelectorAll('[data-topic-ungroup]').forEach(button => button.onclick = () => {
        const topic = data.topics.find(item => item.id === Number(button.dataset.topicUngroup));
        data.topics = data.topics.filter(item => item !== topic); render();
        notice(`${topic.name} ungrouped. All ${topic.ids.length} flows kept.`);
    });
    workspace.querySelectorAll('[data-topic-run]').forEach(button => button.onclick = () => run(data.topics.find(topic => topic.id === Number(button.dataset.topicRun))));
    workspace.querySelectorAll('.flow-run').forEach(button => button.onclick = () => run(null, Number(button.dataset.id)));
    workspace.querySelectorAll('.flow-stop').forEach(button => button.onclick = () => {
        const active = activeFor(Number(button.dataset.id)); if (active) active.status = 'cancelled';
        data.feedback.clear(); render(); notice('Flow stopped. You can run it again.');
    });
    workspace.querySelectorAll('.flow-edit').forEach(button => button.onclick = () => {
        const flow = data.flows.find(item => item.id === Number(button.dataset.id));
        showExisting(flow.name, `<p>Existing flow settings</p><dl><dt>Source</dt><dd>${esc(moduleOf(flow))}</dd><dt>SQL table</dt><dd>${esc(flow.sql_database)}.${esc(flow.sql_schema)}.${esc(flow.sql_table)}</dd><dt>Schedule</dt><dd>${esc(_flowScheduleLabel(flow))}</dd><dt>Browser</dt><dd>${esc(flow.browser_mode)}</dd></dl><p class="topic-context">Grouping keeps the existing flow editor and settings.</p>`);
    });
    workspace.querySelectorAll('.flow-inline-edit, .flow-enabled-switch').forEach(control => control.onchange = () => {
        const flow = data.flows.find(item => item.id === Number(control.dataset.id));
        if (control.classList.contains('flow-enabled-switch')) flow.enabled = control.checked;
        else flow[control.dataset.field] = control.dataset.field === 'owner_person_id' ? Number(control.value) : control.value;
        notice(`${flow.name} updated in this fictional preview.`);
    });
    workspace.querySelectorAll('.flow-sort').forEach(button => button.onclick = () => {window._flowSortMemory = _flowNextSort(_flowSortState(), button.dataset.key); render();});
    workspace.querySelectorAll('[data-flow-classification]').forEach(button => button.onclick = () => {
        if (button.dataset.flowClassification === 'draft') showExisting('Draft flows', '<p>This example contains production flows. Groups stay inside their current module and classification.</p>');
    });
    workspace.querySelectorAll('.flow-open-folder, .flow-classification, .flow-delete').forEach(button => button.onclick = () => notice('This existing action is outside the group preview.'));
    _flowSizeExecutionPane();
}
function openEditor(module, topic = null) {
    editor = {module, id: topic?.id, ids: new Set(topic?.ids || [])};
    $p('#topic-dialog-title').textContent = topic ? 'Edit group' : 'New group';
    $p('#topic-context').textContent = `${module} · Production flows. Choose the flows to keep together.`;
    $p('#topic-name').value = topic?.name || ''; $p('#topic-search').value = ''; $p('#topic-error').textContent = '';
    $p('#topic-save').textContent = topic ? 'Save changes' : 'Create group';
    renderChoices(); $p('#topic-dialog').showModal(); $p('#topic-name').focus();
}
function renderChoices() {
    const filter = $p('#topic-search').value.toLowerCase();
    const flows = data.flows.filter(flow => moduleOf(flow) === editor.module && flow.name.toLowerCase().includes(filter));
    $p('#topic-choices').innerHTML = flows.map(flow => {
        const current = topicOf(flow.id);
        return `<label class="topic-choice"><input type="checkbox" value="${flow.id}" ${editor.ids.has(flow.id) ? 'checked' : ''}><span>${esc(flow.name)}${current && current.id !== editor.id ? `<small>In ${esc(current.name)} · Selecting moves it here</small>` : ''}</span></label>`;
    }).join('') || '<p class="topic-context">No matching flows. Try another name.</p>';
    $p('#topic-selection').textContent = `${editor.ids.size} selected${editor.id ? '' : ' · Select at least 2 flows'}`;
    $p('#topic-choices').querySelectorAll('input').forEach(input => input.onchange = () => {
        const id = Number(input.value); input.checked ? editor.ids.add(id) : editor.ids.delete(id);
        $p('#topic-selection').textContent = `${editor.ids.size} selected${editor.id ? '' : ' · Select at least 2 flows'}`;
    });
}
$p('#topic-search').oninput = renderChoices;
$p('#topic-form').onsubmit = async event => {
    event.preventDefault();
    const error = $p('#topic-error'), name = $p('#topic-name').value.trim(); error.textContent = '';
    if (!name) {error.textContent = 'Enter a group name.'; $p('#topic-name').focus(); return;}
    if (editor.ids.size < (editor.id ? 1 : 2)) {error.textContent = editor.id ? 'Keep at least one flow, or use Ungroup to remove the group.' : 'Select at least 2 flows.'; return;}
    if (data.topics.some(topic => topic.id !== editor.id && topic.module === editor.module && topic.name.toLowerCase() === name.toLowerCase())) {
        error.textContent = `A group named “${name}” already exists in ${editor.module}. Choose another name.`; return;
    }
    const save = $p('#topic-save'); save.disabled = true; save.textContent = 'Saving…';
    await new Promise(resolve => setTimeout(resolve, 200));
    save.disabled = false; save.textContent = editor.id ? 'Save changes' : 'Create group';
    if ($p('#preview-save-failure').checked) {
        $p('#preview-save-failure').checked = false;
        error.textContent = 'Group was not saved. Your name and selection are kept. Try again.'; return;
    }
    const topic = data.topics.find(item => item.id === editor.id) || {id: data.nextTopic++, module: editor.module};
    for (const other of data.topics) if (other !== topic) other.ids = other.ids.filter(id => !editor.ids.has(id));
    data.topics = data.topics.filter(other => other === topic || other.ids.length);
    if (!data.topics.includes(topic)) data.topics.push(topic);
    topic.name = name; topic.ids = [...editor.ids]; data.feedback.delete(topic.id);
    _flowOpenGroups().add(topic.module); data.open.delete(topic.id); $p('#topic-dialog').close(); render();
    notice(`${topic.name} saved · ${topic.ids.length} flows.`); $p(`[data-topic-toggle="${topic.id}"]`)?.focus(); editor = null;
};
document.querySelectorAll('[data-close-topic]').forEach(button => button.onclick = () => $p('#topic-dialog').close());
function run(topic, id) {
    const flows = topic ? members(topic) : data.flows.filter(flow => flow.id === id);
    if (flows.some(flow => activeFor(flow.id))) return;
    if ($p('#preview-run-failure').checked) {
        $p('#preview-run-failure').checked = false;
        const message = `${flows.at(-1).name}: its output is busy. No flows queued. Retry when the other run finishes.`;
        if (topic) {data.feedback.set(topic.id, {message, error: true}); data.open.add(topic.id);}
        render(); notice(message, true); return;
    }
    const oneWorker = $p('#preview-one-worker').checked;
    const at = new Date().toISOString();
    flows.forEach((flow, index) => data.runs.unshift({id: data.nextRun++, flow_id: flow.id, flow_name: flow.name,
        status: oneWorker && index > 0 ? 'queued' : 'running', created_at: at,
        progress: {message: oneWorker && index > 0 ? 'Waiting for an available worker' : 'Reading report · Fictional run'}}));
    const message = `${flows.length} flow${flows.length === 1 ? '' : 's'} queued${topic ? ' together' : ''}. ${oneWorker && flows.length > 1 ? '1 running; the rest wait for a worker.' : 'Running on available workers.'}`;
    if (topic) data.feedback.set(topic.id, {message, error: false});
    render(); notice(message);
}
function showExisting(title, content) {
    $p('#existing-title').textContent = title; $p('#existing-content').innerHTML = content; $p('#existing-dialog').showModal();
}
$p('#existing-close').onclick = () => $p('#existing-dialog').close();
$p('#preview-create-flow').onclick = () => showExisting('Create flow', '<p>The existing flow creation journey stays the same. Create a flow, then organize it from the Flows list.</p>');
document.querySelectorAll('[data-preview-view]').forEach(button => button.onclick = () => {
    if (button.dataset.previewView === 'Flows') return;
    if (button.dataset.previewView === 'Run history') showExisting('Run history', data.runs.length ? data.runs.map(run => `<p>${esc(run.flow_name)} · ${esc(run.status)}</p>`).join('') : '<p>No fictional runs yet. Use Run group or Run.</p>');
    else showExisting(button.dataset.previewView, '<p>This existing view stays the same. Group flows directly in the Flows list.</p>');
});
$p('#preview-reset').onclick = reset;
$p('#preview-scale').onclick = () => {
    for (let id = 9; id <= 200; id++) if (!data.flows.some(flow => flow.id === id)) data.flows.push(fixture(id, `Regional report ${String(id).padStart(3, '0')}`));
    render(); notice('200 fictional flows loaded. Use Group and search to find the flows you need.');
};
$p('#preview-complete').onclick = () => {
    for (const run of data.runs.filter(isActive)) {run.status = 'succeeded'; const flow = data.flows.find(item => item.id === run.flow_id); flow.last_status = 'succeeded'; flow.last_run_at = new Date().toISOString();}
    data.feedback.clear(); render(); notice('Fictional runs finished. Flows have returned to their groups.');
};
window.topicPreview = data;
reset();
