/* Recording authoring stays data-only. No recorded Python is evaluated here. */
window.FlowRecordings = (() => {
    const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    const option = (value, label, current) => `<option value="${h(value)}" ${value === current ? 'selected' : ''}>${h(label)}</option>`;
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
            body.innerHTML = `<form class="flow-form-grid"><label>Name <input name="name" required maxlength="160"></label><label>Website <select name="site">${sites.map(site => option(String(site.id), site.name, '')).join('')}</select></label><label class="flow-span-2">Starting report URL (optional)<input name="route" type="url" placeholder="Leave empty to start at the portal"></label><p class="flow-span-2">Open your report, run it, then download the files.</p><button class="btn-primary" type="submit">Create draft</button></form>`;
            body.querySelector('form').onsubmit = async event => {
                event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true;
                try {
                    const saved = await apiPostJson('/api/flows/recordings/draft', {name:form.elements.name.value, site_id:Number(form.elements.site.value), report_url:form.elements.route.value || null});
                    element.close(); await navigate('flows'); _flowShowView('builder',saved); await open(saved.id,_flowCollectBuilder());
                } catch (error) { element.querySelector('[data-error]').textContent = error.message; button.disabled = false; }
            };
        } catch (error) { element.querySelector('[data-error]').textContent = error.message; }
    }

    const open = (flowId,settings=null) => window.RecordedFlowEditor.open(flowId,settings);
    return {create,open};
})();
