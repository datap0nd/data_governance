// Test-only shell. Actual Flows list UI, HTTP handlers and database.
if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
for (const src of ['/static/users.js', '/static/app.js']) await new Promise((resolve, reject) => {
    const script = document.createElement('script'); script.src = src; script.onload = resolve; script.onerror = reject; document.head.append(script);
});
currentPage = 'flows';
const [flows, catalog, activity] = await Promise.all(['/api/flows', '/api/flows/catalog', '/api/flows/activity'].map(api));
window._flowsState = {flows, catalog, activity, workers: [], runs: [], scans: [], people: [], classification: 'production', view: 'list'};
document.querySelector('#app').innerHTML = `<div class="page-header"><h1>Flows</h1></div><div id="flow-workspace"></div>`;
_flowShowView('list');
document.body.dataset.ready = 'true';
