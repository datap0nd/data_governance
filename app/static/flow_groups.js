/* One level of organization inside the existing source/classification list. */
window.FlowGroups = (() => {
    const opened = new Set();
    try { for (const id of JSON.parse(sessionStorage.getItem('metronome.flowTopics') || '[]')) if (Number.isInteger(id)) opened.add(id); } catch (_) {}
    const pending = new Set(), feedback = new Map();
    const state = () => window._flowsState;
    const classification = () => state()?.classification || 'production';
    const groups = () => (state()?.groups || []).filter(group => group.classification === classification());
    const groupFor = id => groups().find(group => group.flow_ids.includes(id));
    const remember = () => {try {sessionStorage.setItem('metronome.flowTopics', JSON.stringify([...opened]));} catch (_) {}};
    const rowId = id => `flow-topic-${id}`;

    function entries(module, rows) {
        const seen = new Set(), result = [];
        for (const row of rows) {
            const topic = groupFor(row.flow.id);
            if (!topic || topic.module !== module) {result.push({rows: [row]}); continue;}
            if (seen.has(topic.id)) continue;
            seen.add(topic.id);
            result.push({topic, rows: rows.filter(item => topic.flow_ids.includes(item.flow.id))});
        }
        return result;
    }
    function status(topic, activeCount, failed) {
        let item = feedback.get(topic.id);
        if (item?.queued && !activeCount) {feedback.delete(topic.id); item = null;}
        if (item && (!item.error || !activeCount)) return `<span class="${item.error ? 'flow-error' : ''}" role="status">${item.queued ? `${activeCount} of ${topic.flow_ids.length} active · ` : ''}${esc(item.message)}</span>`;
        if (activeCount) return `${activeCount} of ${topic.flow_ids.length} active`;
        if (failed) return `${failed} failed · Expand to review`;
        return 'Run together · Each flow keeps its settings';
    }
    function heading(topic, rows) {
        const active = rows.filter(row => row.activeRun).length;
        const busy = pending.has(topic.id);
        const failed = rows.filter(row => row.flow.last_status === 'failed' && !row.activeRun).length;
        return `<tr class="topic-heading" id="${rowId(topic.id)}" data-topic-id="${topic.id}">
          <td class="flow-name-cell" colspan="3"><button type="button" class="topic-toggle" id="flow-topic-toggle-${topic.id}" data-topic-toggle="${topic.id}" aria-label="${esc(topic.name)} group" aria-expanded="${opened.has(topic.id)}"><span class="topic-arrow" aria-hidden="true">${opened.has(topic.id) ? '▾' : '▸'}</span>${esc(topic.name)}<span class="flow-group-count">${rows.length} flow${rows.length === 1 ? '' : 's'}</span></button></td>
          <td class="topic-summary" colspan="5">${status(topic, active, failed)}</td>
          <td class="flow-row-actions"><button class="btn-sm" type="button" data-topic-run="${topic.id}" ${active || busy ? 'disabled' : ''} title="Queue every flow in this group together. Active members must finish first.">${busy ? 'Queueing…' : active ? 'In progress' : 'Run group'}</button><details class="flow-row-menu"><summary aria-label="More actions: ${esc(topic.name)} group">More</summary><div><button type="button" class="btn-sm" data-topic-edit="${topic.id}">Edit group</button><button type="button" class="btn-sm" data-topic-ungroup="${topic.id}">Ungroup</button></div></details></td></tr>`;
    }
    function rowsHtml(module, rows) {
        return entries(module, rows).map(({topic, rows: items}) =>
            (topic ? heading(topic, items) : '') + items.filter(row => !row.activeRun).map(row => {
                let html = _flowRowHtml(row);
                if (topic) html = html.replace('<tr ', `<tr class="topic-member" ${opened.has(topic.id) ? '' : 'hidden'} `);
                return html;
            }).join('')).join('');
    }
    function sourceAction(module) {
        return `<button class="btn-sm btn-outline" type="button" data-topic-create="${module}" aria-label="Group flows in ${module}">Group</button>`;
    }
    function syncExecutionRows(active) {
        if (!groups().length) return false;
        const target = document.getElementById('flow-execution-rows');
        if (!target) return false;
        const byId = new Map([...document.querySelectorAll('tr[data-flow-id]')].map(row => [Number(row.dataset.flowId), row]));
        const focus = document.activeElement;
        for (const [id, node] of byId) if (active.has(id)) {
            node.hidden = false; node.classList.remove('topic-member');
            if (node.parentElement !== target) target.appendChild(node);
        }
        for (const module of _flowGroups()) {
            const body = document.getElementById(`flow-group-rows-${module}`);
            if (!body) continue;
            const rows = _flowSortRows(state().flows.filter(flow => (flow.classification || 'production') === classification())
                .map(flow => _flowRowModel(flow, [...active.values()], state().catalog)).filter(row => row.group === module), _flowSortState());
            const nodes = [];
            for (const {topic, rows: items} of entries(module, rows)) {
                if (topic) {
                    const head = document.getElementById(rowId(topic.id));
                    if (head) {
                        nodes.push(head);
                        const count = items.filter(row => row.activeRun).length;
                        const button = head.querySelector('[data-topic-run]');
                        button.disabled = !!count || pending.has(topic.id);
                        button.textContent = pending.has(topic.id) ? 'Queueing…' : count ? 'In progress' : 'Run group';
                        const summary = head.querySelector('.topic-summary');
                        const html = status(topic, count, items.filter(row => row.flow.last_status === 'failed' && !row.activeRun).length);
                        if (summary.innerHTML !== html) summary.innerHTML = html;
                    }
                }
                for (const row of items.filter(item => !item.activeRun)) {
                    const node = byId.get(row.flow.id);
                    if (!node) continue;
                    node.hidden = !!topic && !opened.has(topic.id);
                    node.classList.toggle('topic-member', !!topic); nodes.push(node);
                }
            }
            nodes.forEach((node, index) => {if (body.children[index] !== node) body.insertBefore(node, body.children[index] || null);});
        }
        document.getElementById('flow-execution-pane').hidden = target.children.length === 0;
        document.getElementById('flow-execution-count').textContent = `${target.children.length} active`;
        if (focus?.isConnected && document.activeElement !== focus && !focus.closest('[hidden]')) focus.focus({preventScroll: true});
        _flowWatchExecutionPane();
        return true;
    }
    async function refresh() {
        const current = state();
        const result = await api('/api/flows/groups');
        if (state() === current) current.groups = result;
        return result;
    }
    async function openEditor(module, id = null) {
        const current = state();
        document.querySelectorAll('.flow-row-menu[open]').forEach(menu => menu.open = false);
        if (document.getElementById('topic-dialog')) return;
        const dialog = document.createElement('dialog');
        dialog.id = 'topic-dialog'; dialog.className = 'flow-recording-dialog topic-dialog';
        dialog.setAttribute('aria-labelledby', 'topic-dialog-title');
        dialog.innerHTML = '<p role="status">Loading flows…</p><button type="button" class="btn-secondary">Cancel</button>';
        dialog.querySelector('button').onclick = () => dialog.close();
        dialog.addEventListener('close', () => dialog.remove(), {once: true});
        document.body.append(dialog); dialog.showModal();
        let freshFlows;
        try {
            [freshFlows] = await Promise.all([api('/api/flows'), refresh()]);
        } catch (error) {
            if (dialog.isConnected) dialog.querySelector('p').textContent = 'Could not load flows: ' + error.message + ' Close and try again.';
            return;
        }
        if (!dialog.isConnected || state() !== current || current.view !== 'list') {dialog.close(); return;}
        current.flows = freshFlows;
        const topic = id ? current.groups.find(group => group.id === id) : null;
        if (id && !topic) {dialog.close(); toast('This group no longer exists.'); _flowShowView('list'); return;}
        const selected = new Set(topic?.flow_ids || []), selectedClass = classification();
        const choices = freshFlows.filter(flow => (flow.classification || 'production') === selectedClass && _flowRowModel(flow).group === module);
        let saving = false;
        dialog.innerHTML = `<form id="topic-form" novalidate><div class="flow-recording-header"><h2 id="topic-dialog-title">${topic ? 'Edit group' : 'New group'}</h2><button type="button" class="btn-secondary" data-close-topic aria-label="Close group editor">×</button></div>
          <p class="topic-context">${esc(module)} · ${selectedClass === 'draft' ? 'Draft' : 'Production'} flows. Choose the flows to keep together.</p>
          <label class="topic-field" for="topic-name">Group name<input id="topic-name" maxlength="80" autocomplete="off" placeholder="e.g. M Tracker" value="${esc(topic?.name || '')}" required></label>
          <label class="topic-field" for="topic-search">Flows<input type="search" id="topic-search" autocomplete="off" placeholder="Find flows…"></label>
          <div id="topic-choices" class="topic-choices"></div><p id="topic-selection" class="topic-context" aria-live="polite"></p><p id="topic-error" class="flow-error" role="alert"></p>
          <div class="topic-dialog-actions"><button type="button" class="btn-secondary" data-close-topic>Cancel</button><button type="submit" class="btn-primary" id="topic-save">${topic ? 'Save changes' : 'Create group'}</button></div></form>`;
        const find = selector => dialog.querySelector(selector);
        const count = () => find('#topic-selection').textContent = `${selected.size} selected${topic ? '' : ' · Select at least 2 flows'}`;
        const renderChoices = () => {
            const query = find('#topic-search').value.trim().toLowerCase();
            find('#topic-choices').innerHTML = choices.filter(flow => flow.name.toLowerCase().includes(query)).map(flow => {
                const other = current.groups.find(group => group.flow_ids.includes(flow.id));
                return `<label class="topic-choice"><input type="checkbox" value="${flow.id}" ${selected.has(flow.id) ? 'checked' : ''}><span>${esc(flow.name)}${other && other.id !== id ? `<small>In ${esc(other.name)} · Selecting moves it here</small>` : ''}</span></label>`;
            }).join('') || '<p class="topic-context">No matching flows. Try another name.</p>';
            find('#topic-choices').querySelectorAll('input').forEach(input => input.onchange = () => {const value = Number(input.value); input.checked ? selected.add(value) : selected.delete(value); count();});
        };
        find('#topic-search').oninput = renderChoices;
        dialog.querySelectorAll('[data-close-topic]').forEach(button => button.onclick = () => dialog.close());
        dialog.addEventListener('cancel', event => {if (saving) event.preventDefault();});
        find('form').onsubmit = async event => {
            event.preventDefault(); if (saving) return;
            const name = find('#topic-name').value.trim(), error = find('#topic-error'); error.textContent = '';
            if (!name) {error.textContent = 'Enter a group name.'; find('#topic-name').focus(); return;}
            if (selected.size < (topic ? 1 : 2)) {error.textContent = topic ? 'Keep at least one flow, or use Ungroup to remove the group.' : 'Select at least 2 flows.'; return;}
            saving = true;
            const controls = [...dialog.querySelectorAll('input,button')]; controls.forEach(control => control.disabled = true);
            find('#topic-save').textContent = 'Saving…';
            const body = {name, module, classification: selectedClass, flow_ids: [...selected], ...(topic ? {version: topic.version} : {})};
            try {
                const saved = topic ? await apiPut(`/api/flows/groups/${id}`, body) : await apiPostJson('/api/flows/groups', body);
                // Apply the successful response before refreshing: a failed follow-up
                // read must never turn a committed save into a duplicate submission.
                for (const group of current.groups) if (group.id !== saved.id) group.flow_ids = group.flow_ids.filter(flowId => !selected.has(flowId));
                current.groups = current.groups.filter(group => group.id !== saved.id && group.flow_ids.length).concat(saved);
                await refresh().catch(() => {});
                opened.delete(saved.id); remember(); _flowOpenGroups().add(module);
                dialog.close();
                if (state() === current && current.view === 'list') _flowShowView('list');
                toast(`${saved.name} saved · ${saved.flow_ids.length} flows.`);
                document.getElementById(`flow-topic-toggle-${saved.id}`)?.focus();
            } catch (err) {
                error.textContent = 'Group was not saved: ' + err.message + ' Your name and selection are kept.';
            } finally {
                saving = false; controls.forEach(control => control.disabled = false);
                find('#topic-save').textContent = topic ? 'Save changes' : 'Create group';
            }
        };
        renderChoices(); count(); find('#topic-name').focus();
    }
    function bind() {
        document.querySelectorAll('[data-topic-create]').forEach(button => button.onclick = () => openEditor(button.dataset.topicCreate));
        document.querySelectorAll('[data-topic-toggle]').forEach(button => button.onclick = () => {
            const id = Number(button.dataset.topicToggle); opened.has(id) ? opened.delete(id) : opened.add(id); remember(); _flowShowView('list');
            document.getElementById(`flow-topic-toggle-${id}`)?.focus();
        });
        document.querySelectorAll('[data-topic-edit]').forEach(button => button.onclick = () => {const topic = groups().find(group => group.id === Number(button.dataset.topicEdit)); openEditor(topic.module, topic.id);});
        document.querySelectorAll('[data-topic-ungroup]').forEach(button => button.onclick = async () => {
            const current = state(), id = Number(button.dataset.topicUngroup), topic = groups().find(group => group.id === id);
            button.disabled = true;
            try {
                const result = await apiDelete(`/api/flows/groups/${id}?version=${topic.version}`);
                current.groups = current.groups.filter(group => group.id !== id); opened.delete(id); remember();
                if (state() === current && current.view === 'list') _flowShowView('list');
                toast(result.message);
            } catch (error) {
                feedback.set(id, {error: true, message: 'Could not ungroup: ' + error.message});
                if (state() === current && current.view === 'list') _flowShowView('list');
            }
        });
        document.querySelectorAll('[data-topic-run]').forEach(button => button.onclick = async () => {
            const current = state(), id = Number(button.dataset.topicRun), topic = groups().find(group => group.id === id);
            if (pending.has(id)) return;
            pending.add(id); button.disabled = true; button.textContent = 'Queueing…';
            try {
                const result = await apiPostJson(`/api/flows/groups/${id}/run`, {flow_ids: topic.flow_ids});
                feedback.set(id, {queued: true, message: result.message});
                // Keep duplicate protection even when the immediate activity read fails.
                const queued = result.runs.map(run => ({...run, status: 'queued', created_at: new Date().toISOString()}));
                current.activity = {latest_runs: [], workers: {online: current.workers?.length || 0}, ...(current.activity || {}), active_runs: [...(current.activity?.active_runs || current.runs || []).filter(run => ['queued', 'claimed', 'running'].includes(run.status) && !topic.flow_ids.includes(run.flow_id)), ...queued]};
                toast(result.message);
            } catch (error) {
                feedback.set(id, {error: true, message: error.message}); opened.add(id); remember();
            } finally {
                pending.delete(id);
                if (state() === current && current.view === 'list') {_flowShowView('list'); _flowRefreshActivity();}
            }
        });
    }
    return {sourceAction, rowsHtml, syncExecutionRows, bind, refresh};
})();
