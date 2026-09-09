const root = document.querySelector('#app');
const seed = {
    name: 'Main report',
    steps: [
        {label: '', action: 'Open GSCM', target: ''},
        {label: '', action: 'Click', target: 'Country', targetType: 'portal'},
        {label: '', action: 'Run report', target: ''},
        {label: '', action: 'Download workbook', target: ''},
    ],
    versions: [{revision: 1, source: 'Country report'}],
};
let state = structuredClone(seed);
let selected = 1;
let undo = null;
let busy = '';
let message = 'Copied from Country report. Test recording before activation.';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const button = (id, text, primary = false, allowBusy = false) =>
    `<button type="button" class="btn-${primary ? 'primary' : 'secondary'}" id="${id}" ${busy && !allowBusy ? 'disabled' : ''}>${text}</button>`;
const description = step => step.label || (step.target ? `Click “${step.target}”` : step.action);
const undoButton = () => `<button type="button" class="btn-secondary" id="undo" ${busy || !undo ? 'disabled' : ''}>Undo</button>`;

function bind(id, handler) {
    document.getElementById(id)?.addEventListener('click', handler);
}

function remember() {
    undo = structuredClone(state.steps);
}

function render() {
    const recording = busy === 'recording';
    root.innerHTML = `${button('back', 'Back to Edit Flow', false, true)}
      <div class="heading">
        <div><h1>${esc(state.name)}</h1><p>${recording ? 'Recording…' : `${state.steps.length} steps recorded`}</p></div>
        <details><summary>More</summary>${button('versions', 'Saved versions')}</details>
      </div>
      <div class="review-actions">
        <div class="actions">
          ${recording ? button('finish', 'Finish recording', true, true) : `${button('test', 'Test recording', true)}${button('save', 'Save draft')}${button('record-again', 'Record again')}${button('template', 'Choose from template')}${undoButton()}`}
        </div>
        <div class="feedback" role="status" aria-live="polite">${esc(message)}</div>
      </div>
      ${recording ? `<div class="empty"><h2>Go ahead in the portal</h2><p>Open the new report, run it, then download the files. The saved Country report version remains available.</p></div>` : `
      <ol class="steps">${state.steps.map((step, index) => `<li>
        <button data-step="${index}" aria-expanded="${selected === index}" aria-label="Edit step ${index + 1}: ${esc(description(step))}">
          <span class="num">${index + 1}</span><span>${esc(description(step))}</span><span class="edit-hint">Edit</span>
        </button>
        ${selected === index ? editor(step) : ''}
      </li>`).join('')}</ol>`}`;

    document.querySelectorAll('[data-step]').forEach(node => node.onclick = () => {
        selected = selected === Number(node.dataset.step) ? -1 : Number(node.dataset.step);
        render();
    });
    bind('record-again', () => { busy = 'recording'; selected = -1; message = 'Portal connected. A new recording has started.'; render(); });
    bind('finish', () => {
        busy = '';
        state.steps = [
            {label: '', action: 'Open GSCM', target: ''},
            {label: '', action: 'Click', target: 'Main', targetType: 'portal'},
            {label: '', action: 'Download workbook', target: ''},
        ];
        selected = 1;
        undo = null;
        message = 'New recording ready. The earlier saved version is still available.';
        render();
    });
    bind('template', () => { state = structuredClone(seed); selected = 1; undo = null; message = 'Copied from Country report. Test recording before activation.'; render(); });
    bind('undo', () => { state.steps = undo; undo = null; message = 'Edit undone.'; render(); });
    bind('save', () => {
        if (!validTarget()) return;
        if (!state.versions.some(version => version.target === state.steps[1]?.target)) {
            state.versions.push({revision: state.versions.length + 1, target: state.steps[1]?.target});
        }
        message = 'Draft saved. Test recording when ready.';
        render();
    });
    bind('test', () => { if (validTarget()) { message = 'Test started with the edited target.'; render(); } });
    bind('versions', showVersions);
    bind('done', () => { selected = -1; render(); });
    bind('back', () => { message = 'Preview only: the Flow editor would open here.'; render(); });

    const label = document.getElementById('step-label');
    if (label) label.oninput = event => {
        if (!undo) remember();
        state.steps[selected].label = event.target.value;
        message = 'Step label changed; the playback target is unchanged.';
        document.querySelector(`[data-step="${selected}"] span:nth-child(2)`).textContent = description(state.steps[selected]);
        updateInline();
    };
    const targetType = document.getElementById('target-type');
    if (targetType) targetType.onchange = event => {
        if (!undo) remember();
        state.steps[selected].targetType = event.target.value;
        message = event.target.value === 'bookmark' ? 'Enter the exact GSCM Favorite bookmark name.' : 'Portal element targeting selected.';
        render();
    };
    const target = document.getElementById('target-name');
    if (target) target.oninput = event => {
        if (!undo) remember();
        state.steps[selected].target = event.target.value;
        message = event.target.value.trim() ? 'Playback target changed. Test recording when ready.' : 'Enter the target name used during playback.';
        document.querySelector(`[data-step="${selected}"] span:nth-child(2)`).textContent = description(state.steps[selected]);
        updateInline();
    };
}

function updateInline() {
    const feedback = document.querySelector('.feedback');
    feedback.textContent = message;
    const undoControl = document.getElementById('undo');
    if (undoControl) { undoControl.disabled = false; undoControl.onclick = () => { state.steps = undo; undo = null; message = 'Edit undone.'; render(); }; }
}

function validTarget() {
    const click = state.steps.find(step => step.action === 'Click');
    if (click?.target.trim()) return true;
    message = click?.targetType === 'bookmark' ? 'Enter the exact bookmark name used during playback.' : 'Enter the target name used during playback.';
    render();
    const target = document.getElementById('target-name');
    target?.setCustomValidity(message); target?.reportValidity(); target?.focus();
    return false;
}

function editor(step) {
    const targetEditor = step.action === 'Click' ? `
      <label>Target type<select id="target-type"><option value="portal" ${step.targetType !== 'bookmark' ? 'selected' : ''}>Portal element</option><option value="bookmark" ${step.targetType === 'bookmark' ? 'selected' : ''}>GSCM Favorite bookmark</option></select></label>
      <label>${step.targetType === 'bookmark' ? 'Exact bookmark name' : 'Target name (used during playback)'}<input id="target-name" required value="${esc(step.target)}"></label>
      <p class="muted">${step.targetType === 'bookmark' ? 'Playback finds this bookmark by its exact Favorite-list name.' : 'Change the button name while keeping the recorded role, class, frame and exact-match settings.'}</p>` : '';
    return `<div class="step-editor">
      <label>Step name (display only)<input id="step-label" maxlength="160" value="${esc(step.label)}" placeholder="${esc(description(step))}"></label>
      ${targetEditor}
      <div class="actions">${button('done', 'Done')}</div>
      <details><summary>Advanced</summary><p class="muted">Recorded selector and frame details remain unchanged.</p><code>${step.action === 'Click' ? `get_by_role('button', name='${esc(step.target)}', exact=True)` : `Step ${selected + 1} · recorded portal target`}</code></details>
    </div>`;
}

function showVersions() {
    document.querySelector('[data-versions]')?.remove();
    const section = document.createElement('section');
    section.className = 'panel';
    section.dataset.versions = '1';
    section.innerHTML = `<h2>Saved versions</h2>${state.versions.map(version => `<p>Version ${version.revision}${version.source ? ` · from ${esc(version.source)}` : ` · target ${esc(version.target)}`}</p>`).join('')}`;
    document.querySelector('.review-actions').after(section);
}

document.getElementById('reset').onclick = () => {
    state = structuredClone(seed); selected = 1; undo = null; busy = '';
    message = 'Copied from Country report. Test recording before activation.';
    render();
};
render();
