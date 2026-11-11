// System > Settings. One page for every app-wide setting, drawn from the
// server's registry (GET /api/system/settings). Everyone can read it; only the
// host PC can save. Unsaved edits live in settingsState, so a failed save or a
// visit to another page loses nothing.
let settingsState = {
    sections: [], canEdit: false, section: null, query: '', onlyChanged: false,
    edits: {}, errors: {}, saving: false, saveError: '', loadError: null, logs: null, logsMessage: '',
    secretEditing: null, secretConfirming: null, secretError: '', tests: {},
    portal: null, portalMode: '', portalError: '',
};

function settingsRows() {
    const rows = [];
    for (const section of settingsState.sections) {
        for (const group of section.groups) {
            for (const row of group.settings) rows.push({ ...row, section, group });
        }
    }
    return rows;
}

function settingsRow(key) {
    return settingsRows().find(row => row.key === key);
}

function settingsSame(a, b) {
    return Array.isArray(a) || Array.isArray(b) ? settingsListText(a) === settingsListText(b) : String(a) === String(b);
}

function settingsListText(value) {
    if (Array.isArray(value)) return value.join(', ');
    return String(value ?? '').split(/[,;\n]/).map(item => item.trim()).filter(Boolean).join(', ');
}

function settingsCurrent(row) {
    return Object.prototype.hasOwnProperty.call(settingsState.edits, row.key) ? settingsState.edits[row.key] : row.value;
}

function settingsIsDirty(row) {
    if (row.kind === 'secret') return false;
    if (!Object.prototype.hasOwnProperty.call(settingsState.edits, row.key)) return false;
    const edit = settingsState.edits[row.key];
    // A pending reset (null) is a change whenever something is saved here.
    if (edit === null) return row.source === 'app';
    return !settingsSame(edit, row.value);
}

function settingsDirtyKeys() {
    return settingsRows().filter(settingsIsDirty).map(row => row.key);
}

function settingsShow(row, value) {
    if (row.kind === 'bool') return value ? 'On' : 'Off';
    if (row.kind === 'choice') return row.choice_labels[row.choices.indexOf(value)] || String(value);
    if (row.kind === 'list') return settingsListText(value) || 'none';
    if (value === '' || value === null || value === undefined) return 'empty';
    // A quantity reads better grouped (10,485 MB); a port number does not.
    return typeof value === 'number' && row.unit ? value.toLocaleString('en') : String(value);
}

function settingsFieldId(key) {
    return 'st-' + key.replace(/[^A-Za-z0-9]/g, '-');
}

function settingsMetaHtml(row) {
    const dirty = settingsIsDirty(row);
    const pendingReset = settingsState.edits[row.key] === null;
    const chips = [];
    if (dirty) chips.push(`<span class="st-chip st-chip-unsaved">${pendingReset ? 'Will reset' : 'Unsaved'}</span>`);
    else chips.push(`<span class="st-chip ${row.source === 'app' ? 'st-chip-set' : ''}">${esc(row.source_text || 'Default')}</span>`);
    chips.push(`<span class="st-chip">${esc(row.applies_text)}</span>`);
    if (row.kind === 'secret') return chips.join('');
    const canReset = settingsState.canEdit && !pendingReset && (row.source === 'app' || dirty);
    const reset = canReset
        ? `<button type="button" class="st-link" data-st-reset="${esc(row.key)}">Reset to default (${esc(settingsShow(row, row.default))})</button>` : '';
    return chips.join('') + reset;
}

// A password is never sent to the browser: the row says whether one is saved,
// and offers to replace or clear it.
function settingsSecretHtml(row) {
    const id = settingsFieldId(row.key);
    const off = !settingsState.canEdit ? ' disabled' : '';
    if (settingsState.secretEditing === row.key) {
        return `<div class="st-field"><input type="password" id="${id}" autocomplete="new-password" placeholder="New value" aria-label="New ${esc(row.label.toLowerCase())}"></div>
            <div class="st-field"><button type="button" class="btn-sm" data-st-secret-save="${esc(row.key)}">Save</button>
            <button type="button" class="btn-sm btn-outline" data-st-secret-cancel>Cancel</button></div>
            ${settingsState.secretError ? `<p class="st-error" role="alert">${esc(settingsState.secretError)}</p>` : ''}`;
    }
    if (settingsState.secretConfirming === row.key) {
        return `<p class="st-confirm">Clear the saved ${esc(row.label.toLowerCase())}? Whatever signs in with it stops working until a new one is saved.</p>
            <div class="st-field"><button type="button" class="btn-sm btn-red" data-st-secret-clear="${esc(row.key)}">Clear it</button>
            <button type="button" class="btn-sm btn-outline" data-st-secret-cancel>Keep it</button></div>
            ${settingsState.secretError ? `<p class="st-error" role="alert">${esc(settingsState.secretError)}</p>` : ''}`;
    }
    return `<div class="st-secret">${row.saved ? '<strong>Saved</strong> <span aria-hidden="true">••••••••</span>' : 'Not set'}</div>
        <div class="st-field"><button type="button" class="btn-sm btn-outline" id="${id}" data-st-secret-edit="${esc(row.key)}"${off}>${row.saved ? 'Replace' : 'Add'}</button>
        ${row.saved && row.source === 'app' ? `<button type="button" class="btn-sm btn-outline" data-st-secret-ask="${esc(row.key)}"${off}>Clear</button>` : ''}</div>`;
}

function settingsControlHtml(row) {
    if (row.kind === 'secret') return settingsSecretHtml(row);
    const id = settingsFieldId(row.key);
    const value = settingsState.edits[row.key] === null ? row.default : settingsCurrent(row);
    const off = !settingsState.canEdit || settingsState.saving ? ' disabled' : '';
    const error = settingsState.errors[row.key];
    const invalid = error ? ` aria-invalid="true" aria-describedby="${id}-error"` : '';
    const range = row.minimum !== null && row.maximum !== null
        ? `${settingsShow(row, Number(row.minimum))} to ${settingsShow(row, Number(row.maximum))}` : '';
    let html;
    if (row.kind === 'int' || row.kind === 'float') {
        html = `<div class="st-field"><input type="number" id="${id}" data-st-key="${esc(row.key)}" value="${esc(value)}" min="${row.minimum ?? ''}" max="${row.maximum ?? ''}" step="${row.kind === 'int' ? 1 : 'any'}" placeholder="${esc(row.placeholder || '')}"${off}${invalid}>
            <span class="st-unit">${esc([row.unit, range].filter(Boolean).join(' · '))}</span></div>`;
    } else if (row.kind === 'time') {
        html = `<div class="st-field"><input type="time" id="${id}" data-st-key="${esc(row.key)}" value="${esc(value)}"${off}${invalid}><span class="st-unit">Dubai time</span></div>`;
    } else if (row.kind === 'bool') {
        html = `<label class="st-toggle"><input type="checkbox" id="${id}" data-st-key="${esc(row.key)}"${value ? ' checked' : ''}${off}> <span>${value ? 'On' : 'Off'}</span></label>`;
    } else if (row.kind === 'choice') {
        html = `<div class="st-field"><select id="${id}" data-st-key="${esc(row.key)}"${off}>${row.choices.map((choice, index) =>
            `<option value="${esc(choice)}"${choice === value ? ' selected' : ''}>${esc(row.choice_labels[index] || choice)}</option>`).join('')}</select></div>`;
    } else if (row.kind === 'list') {
        html = `<div class="st-field"><textarea id="${id}" data-st-key="${esc(row.key)}" rows="2" placeholder="${esc(row.placeholder || '')}"${off}${invalid}>${esc(settingsListText(value))}</textarea></div>`;
    } else {
        html = `<div class="st-field"><input type="text" id="${id}" data-st-key="${esc(row.key)}" class="${row.kind === 'path' ? 'st-mono' : ''}" value="${esc(value)}" placeholder="${esc(row.placeholder || '')}"${off}${invalid}></div>`;
    }
    return html + (error ? `<p class="st-error" id="${id}-error" role="alert">${esc(error)}</p>` : '');
}

function settingsRowHtml(row) {
    return `<div class="st-row" data-st-row="${esc(row.key)}">
        <div class="st-row-text"><label for="${settingsFieldId(row.key)}">${esc(row.label)}</label>
            ${row.help ? `<p class="st-help">${esc(row.help)}</p>` : ''}
            <div class="st-meta">${settingsMetaHtml(row)}</div></div>
        <div class="st-row-control">${settingsControlHtml(row)}</div></div>`;
}

function settingsMatches(row) {
    if (settingsState.onlyChanged && row.source !== 'app' && !settingsIsDirty(row)) return false;
    const words = settingsState.query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const haystack = `${row.label} ${row.help} ${row.section.name} ${row.group.title}`.toLowerCase();
    return words.every(word => haystack.includes(word));
}

function settingsLogsHtml() {
    const logs = settingsState.logs;
    if (!logs) return '';
    const mb = bytes => (bytes / 1048576).toLocaleString('en', { maximumFractionDigits: bytes < 10485760 ? 1 : 0 }) + ' MB';
    const largest = (logs.largest || []).slice(0, 5).map(file =>
        `<tr><td class="st-mono">${esc(file.name)}</td><td>${mb(file.bytes)}</td><td>${file.rotated ? 'Rotated' : 'In use'}</td></tr>`).join('');
    return `<div class="st-logs" id="st-logs">
        <p><strong>${logs.exists ? `The logs folder holds ${mb(logs.total_bytes)} in ${logs.file_count} ${logs.file_count === 1 ? 'file' : 'files'}.` : 'No logs folder exists on this computer yet.'}</strong>
            ${logs.exists ? `<span class="st-help">${esc(logs.directory)}</span>` : ''}</p>
        ${largest ? `<table><thead><tr><th scope="col">Largest files</th><th scope="col">Size</th><th scope="col">State</th></tr></thead><tbody>${largest}</tbody></table>` : ''}
        <p>${settingsState.canEdit ? '<button type="button" class="btn-sm btn-outline" id="st-logs-prune">Clean up now</button>' : ''}
            <span class="st-help" id="st-logs-message" role="status">${esc(settingsState.logsMessage)}</span></p></div>`;
}

// The ASAP and GSCM login is one Windows-protected file, not a settings-file
// value, so it has its own block: saved or not, replace, clear.
function settingsPortalHtml() {
    const portal = settingsState.portal;
    if (!portal) return '';
    const off = !settingsState.canEdit || !portal.available ? ' disabled' : '';
    const error = settingsState.portalError ? `<p class="st-error" role="alert">${esc(settingsState.portalError)}</p>` : '';
    let control;
    if (settingsState.portalMode === 'edit') {
        control = `<div class="st-field"><input type="text" id="st-portal-user" autocomplete="off" spellcheck="false" placeholder="User ID" aria-label="Portal user ID"></div>
            <div class="st-field"><input type="password" id="st-portal-password" autocomplete="new-password" placeholder="Password" aria-label="Portal password"></div>
            <div class="st-field"><button type="button" class="btn-sm" data-st-portal="save">Save</button>
            <button type="button" class="btn-sm btn-outline" data-st-portal="cancel">Cancel</button></div>${error}`;
    } else if (settingsState.portalMode === 'confirm') {
        control = `<p class="st-confirm">Clear the saved portal sign-in? When a portal session expires, Flows that need it wait for someone to sign in by hand on the host PC.</p>
            <div class="st-field"><button type="button" class="btn-sm btn-red" data-st-portal="clear">Clear it</button>
            <button type="button" class="btn-sm btn-outline" data-st-portal="cancel">Keep it</button></div>${error}`;
    } else {
        control = `<div class="st-secret">${portal.configured ? '<strong>Saved</strong> <span aria-hidden="true">••••••••</span>' : 'Not set'}</div>
            <div class="st-field"><button type="button" class="btn-sm btn-outline" id="st-portal-edit" data-st-portal="edit"${off}>${portal.configured ? 'Replace' : 'Add'}</button>
            ${portal.configured ? `<button type="button" class="btn-sm btn-outline" data-st-portal="ask"${off}>Clear</button>` : ''}</div>`;
    }
    const chips = [`<span class="st-chip ${portal.configured ? 'st-chip-set' : ''}">${portal.configured && portal.updated_at ? 'Saved ' + esc(formatDate(portal.updated_at)) : portal.configured ? 'Saved' : 'Not set'}</span>`,
        '<span class="st-chip">Used at the next portal sign-in</span>'];
    return `<div class="st-group"><div class="st-group-head"><h3>Portal sign-in</h3></div>
        <div class="st-row"><div class="st-row-text"><label for="st-portal-edit">ASAP and GSCM login</label>
            <p class="st-help">The one company sign-on that Flow workers use when an ASAP or GSCM session expires. It is kept encrypted for the Windows account the Metronome services run as; it is never shown here.${portal.available ? '' : ' It can only be saved on the Windows host PC.'}</p>
            <div class="st-meta">${chips.join('')}</div></div>
            <div class="st-row-control">${control}</div></div></div>`;
}

function settingsPortalMatches() {
    if (settingsState.onlyChanged) return false;
    const words = settingsState.query.toLowerCase().split(/\s+/).filter(Boolean);
    const haystack = 'portal sign-in asap gscm login password user connections';
    return words.every(word => haystack.includes(word));
}

function settingsGroupHtml(section, group, prefix) {
    const rows = group.settings.map(row => ({ ...row, section, group })).filter(settingsMatches);
    if (!rows.length) return '';
    const extra = section.id === 'updates' && group.title === 'Logs' && !settingsState.query && !settingsState.onlyChanged ? settingsLogsHtml() : '';
    const test = group.test ? settingsState.tests[group.test] : null;
    const testButton = group.test && settingsState.canEdit
        ? `<button type="button" class="btn-sm btn-outline" data-st-test="${esc(group.test)}"${test === 'busy' ? ' disabled' : ''}>${test === 'busy' ? 'Testing…' : 'Test connection'}</button>` : '';
    const testLine = test && test !== 'busy'
        ? `<p class="st-test ${test.ok ? 'st-ok' : 'st-bad'}" role="status">${esc(test.message)}</p>` : '';
    return `<div class="st-group"><div class="st-group-head"><h3>${prefix ? esc(prefix) + ' · ' : ''}${esc(group.title)}</h3>${testButton}</div>${testLine}${rows.map(settingsRowHtml).join('')}${extra}</div>`;
}

function settingsPanelHtml() {
    const filtering = settingsState.query || settingsState.onlyChanged;
    if (filtering) {
        const body = settingsState.sections.map(section =>
            section.groups.map(group => settingsGroupHtml(section, group, section.name)).join('')).join('')
            + (settingsPortalMatches() ? settingsPortalHtml() : '');
        const title = settingsState.query ? `Settings matching "${esc(settingsState.query)}"` : 'Settings changed from their defaults';
        return `<header><h2>${title}</h2></header>${body || '<p class="st-empty">Nothing matches. Clear the search box or untick "Only changed from default" to see every setting.</p>'}`;
    }
    const section = settingsState.sections.find(item => item.id === settingsState.section) || settingsState.sections[0];
    if (!section) return '<p class="st-empty">No settings are available.</p>';
    const links = (section.links || []).map(link =>
        `<li><a href="#${esc(link.page)}" data-st-page="${esc(link.page)}">${esc(link.label)}</a>${link.note ? ` <span class="st-help">${esc(link.note)}</span>` : ''}</li>`).join('');
    return `<header><h2>${esc(section.name)}</h2><p>${esc(section.blurb)}</p></header>
        ${section.groups.map(group => settingsGroupHtml(section, group, '')).join('')}
        ${section.id === 'connections' ? settingsPortalHtml() : ''}
        ${links ? `<div class="st-group"><h3>Set on another page</h3><ul class="st-links">${links}</ul></div>` : ''}`;
}

function settingsRailHtml() {
    return settingsState.sections.filter(section => section.groups.length || (section.links || []).length).map(section => {
        const unsaved = section.groups.reduce((count, group) =>
            count + group.settings.filter(row => settingsIsDirty({ ...row, section, group })).length, 0);
        const current = !settingsState.query && !settingsState.onlyChanged && section.id === settingsState.section;
        return `<button type="button" data-st-section="${esc(section.id)}" aria-current="${current}"><span>${esc(section.name)}</span>${unsaved ? `<span class="st-count">${unsaved} unsaved</span>` : ''}</button>`;
    }).join('');
}

function settingsSaveBarHtml() {
    const count = settingsDirtyKeys().length;
    const errors = Object.keys(settingsState.errors).length;
    if (!count && !settingsState.saveError) return '';
    let message = `${count} unsaved ${count === 1 ? 'change' : 'changes'}`;
    let tone = '';
    if (settingsState.saving) message = 'Saving…';
    else if (settingsState.saveError) { message = settingsState.saveError; tone = ' st-bad'; }
    else if (errors) { message = `Fix the ${errors === 1 ? 'highlighted setting' : errors + ' highlighted settings'} to save. Your other changes are kept.`; tone = ' st-bad'; }
    return `<div class="st-savebar"><span class="st-savebar-message${tone}" role="status">${esc(message)}</span>
        <span class="st-savebar-actions"><button type="button" class="btn-outline" id="st-discard"${settingsState.saving || !count ? ' disabled' : ''}>Discard</button>
        <button type="button" id="st-save"${settingsState.saving || !count ? ' disabled' : ''}>${settingsState.saving ? 'Saving…' : settingsState.saveError ? 'Try again' : 'Save changes'}</button></span></div>`;
}

function settingsPageHtml() {
    if (settingsState.loadError) {
        return `<div class="page-header"><div><h1>Settings</h1></div></div>
            <section class="settings-panel st-panel" role="alert"><h2>Could not load settings</h2><p>${esc(settingsState.loadError)}</p>
            <button type="button" class="btn-outline" id="st-retry">Try again</button></section>`;
    }
    return `<div class="st-page" id="st-page">
        <div class="page-header st-header"><div><h1>Settings</h1>
            <p class="subtitle">Limits, schedules and options for the whole app. Every change is recorded in the Event Log with who made it.</p></div>
            <div class="st-tools"><label class="sr-only" for="st-search">Find a setting</label>
                <input type="search" id="st-search" placeholder="Find a setting" value="${esc(settingsState.query)}">
                <label class="st-check"><input type="checkbox" id="st-only-changed"${settingsState.onlyChanged ? ' checked' : ''}> Only changed from default</label></div></div>
        ${settingsState.canEdit ? '' : '<p class="st-notice" role="note"><strong>Only the host PC can change settings.</strong> You are looking from another computer, so everything here is read-only. On the host PC, open Metronome as http://localhost:8000.</p>'}
        <div class="st-layout"><div class="st-rail" id="st-rail" role="navigation" aria-label="Settings sections">${settingsRailHtml()}</div>
            <section class="settings-panel st-panel" id="st-panel">${settingsPanelHtml()}</section></div>
        <div id="st-savebar-slot">${settingsSaveBarHtml()}</div></div>`;
}

async function settingsLoad() {
    try {
        const data = await api('/api/system/settings');
        settingsState.sections = data.sections;
        settingsState.canEdit = Boolean(data.can_edit);
        settingsState.loadError = null;
        const visible = data.sections.filter(section => section.groups.length || (section.links || []).length);
        if (!visible.some(section => section.id === settingsState.section)) settingsState.section = visible[0]?.id || null;
    } catch (error) {
        settingsState.loadError = error.message;
    }
    try { settingsState.logs = await api('/api/system/logs'); } catch (_) { settingsState.logs = null; }
    try { settingsState.portal = await api('/api/system/settings/portal-login'); } catch (_) { settingsState.portal = null; }
}

async function renderSettings() {
    await settingsLoad();
    return settingsPageHtml();
}

function settingsRefresh(focusId) {
    const rail = $('#st-rail'), panel = $('#st-panel'), slot = $('#st-savebar-slot');
    if (!rail || !panel || !slot) return;
    rail.innerHTML = settingsRailHtml();
    panel.innerHTML = settingsPanelHtml();
    slot.innerHTML = settingsSaveBarHtml();
    if (focusId) document.getElementById(focusId)?.focus();
}

function settingsReadControl(control, row) {
    if (row.kind === 'bool') return control.checked;
    if (row.kind === 'int' || row.kind === 'float') return control.value === '' ? '' : Number(control.value);
    return control.value;
}

async function settingsSave() {
    if (settingsState.saving) return;
    const keys = settingsDirtyKeys();
    if (!keys.length) return;
    const values = {};
    for (const key of keys) {
        const row = settingsRow(key);
        const edit = settingsState.edits[key];
        values[key] = edit === null ? null : row.kind === 'list' ? settingsListText(edit) : edit;
    }
    settingsState.saving = true; settingsState.saveError = ''; settingsState.errors = {};
    settingsRefresh();
    try {
        const response = await fetch('/api/system/settings', {
            method: 'PUT', headers: apiHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ values }),
        });
        const payload = await response.json().catch(() => ({}));
        if (response.status === 422 && payload.errors) {
            settingsState.errors = payload.errors;
            const first = settingsRow(Object.keys(payload.errors)[0]);
            if (first && !settingsState.query && !settingsState.onlyChanged) settingsState.section = first.section.id;
            settingsState.saving = false;
            settingsRefresh(first ? settingsFieldId(first.key) : null);
            return;
        }
        if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `The server answered ${response.status}.`);
        settingsState.sections = payload.sections;
        settingsState.canEdit = Boolean(payload.can_edit);
        settingsState.edits = {};
        settingsState.saving = false;
        settingsState.tests = {};  // an earlier test result described the old values
        settingsRefresh();
        const saved = (payload.changed || []).length;
        const newRuns = (payload.changed || []).some(key => settingsRow(key)?.applies === 'new_runs');
        toast(`Saved ${saved} ${saved === 1 ? 'setting' : 'settings'}.${newRuns ? ' Runs already in progress keep their old values.' : ''}`);
    } catch (error) {
        settingsState.saving = false;
        settingsState.saveError = `Nothing was saved: ${String(error.message).replace(/\.$/, '')}. Your changes are still here.`;
        settingsRefresh();
    }
}

async function settingsSecretAction(button) {
    const data = button.dataset;
    settingsState.secretError = '';
    if ('stSecretCancel' in data) { settingsState.secretEditing = null; settingsState.secretConfirming = null; settingsRefresh(); return; }
    if (data.stSecretEdit) { settingsState.secretEditing = data.stSecretEdit; settingsState.secretConfirming = null; settingsRefresh(settingsFieldId(data.stSecretEdit)); return; }
    if (data.stSecretAsk) { settingsState.secretConfirming = data.stSecretAsk; settingsState.secretEditing = null; settingsRefresh(); return; }
    const key = data.stSecretSave || data.stSecretClear;
    const row = settingsRow(key);
    const clearing = Boolean(data.stSecretClear);
    const input = document.getElementById(settingsFieldId(key));
    if (!clearing && !input.value) { settingsState.secretError = 'Enter the new value, or choose Cancel.'; settingsRefresh(settingsFieldId(key)); return; }
    button.disabled = true;
    try {
        const response = await fetch(`/api/system/settings/secret/${encodeURIComponent(key)}`, clearing
            ? { method: 'DELETE', headers: apiHeaders() }
            : { method: 'PUT', headers: apiHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ value: input.value }) });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `The server answered ${response.status}.`);
        settingsState.sections = payload.sections;
        settingsState.secretEditing = null; settingsState.secretConfirming = null;
        settingsRefresh();
        toast(clearing ? `${row.label} cleared.` : `${row.label} saved. It is used from the next sign-in; nothing needs restarting.`);
    } catch (error) {
        // The typed value stays in the field so it can be corrected or retried.
        settingsState.secretError = error.message;
        const typed = input ? input.value : '';
        settingsRefresh(settingsFieldId(key));
        const field = document.getElementById(settingsFieldId(key));
        if (field && field.type === 'password') field.value = typed;
    }
}

async function settingsPortalAction(button) {
    const action = button.dataset.stPortal;
    settingsState.portalError = '';
    if (action === 'cancel') { settingsState.portalMode = ''; settingsRefresh('st-portal-edit'); return; }
    if (action === 'edit') { settingsState.portalMode = 'edit'; settingsRefresh('st-portal-user'); return; }
    if (action === 'ask') { settingsState.portalMode = 'confirm'; settingsRefresh(); return; }
    const user = $('#st-portal-user'), password = $('#st-portal-password');
    const typed = { user: user ? user.value : '', password: password ? password.value : '' };
    if (action === 'save' && (!typed.user.trim() || !typed.password)) {
        settingsState.portalError = 'Enter both the user ID and the password, or choose Cancel.';
        settingsRefresh(typed.user.trim() ? 'st-portal-password' : 'st-portal-user');
        $('#st-portal-user').value = typed.user; $('#st-portal-password').value = typed.password;
        return;
    }
    button.disabled = true;
    try {
        const response = await fetch('/api/system/settings/portal-login', action === 'clear'
            ? { method: 'DELETE', headers: apiHeaders() }
            : { method: 'PUT', headers: apiHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ username: typed.user, password: typed.password }) });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `The server answered ${response.status}.`);
        settingsState.portal = payload;
        settingsState.portalMode = '';
        settingsRefresh('st-portal-edit');
        toast(action === 'clear' ? 'Portal sign-in cleared.' : 'Portal sign-in saved. Workers use it the next time a portal asks them to sign in.');
    } catch (error) {
        // What was typed stays in the fields so it can be corrected or retried.
        settingsState.portalError = error.message;
        settingsRefresh();
        if ($('#st-portal-user')) { $('#st-portal-user').value = typed.user; $('#st-portal-password').value = typed.password; }
    }
}

async function settingsTest(name) {
    const group = settingsState.sections.flatMap(section => section.groups.map(item => ({ ...item, section }))).find(item => item.test === name);
    const unsaved = group && group.settings.some(row => settingsIsDirty({ ...row, section: group.section, group }));
    if (unsaved) {
        settingsState.tests[name] = { ok: false, message: 'Save your changes first. The test signs in with the saved values.' };
        settingsRefresh(); return;
    }
    settingsState.tests[name] = 'busy';
    settingsRefresh();
    try {
        settingsState.tests[name] = await apiPost(`/api/system/settings/test/${encodeURIComponent(name)}`);
    } catch (error) {
        settingsState.tests[name] = { ok: false, message: `The test did not run: ${error.message}` };
    }
    settingsRefresh();
}

function bindSettingsPage() {
    const page = $('#st-page');
    if (!page) {
        const retry = $('#st-retry');
        if (retry) retry.onclick = () => navigate('settings');
        return;
    }
    page.addEventListener('input', event => {
        const target = event.target;
        if (target.id === 'st-search') { settingsState.query = target.value.trim(); settingsRefresh(); return; }
        const key = target.dataset.stKey;
        const row = key && settingsRow(key);
        if (!row) return;
        settingsState.edits[key] = settingsReadControl(target, row);
        settingsState.saveError = '';
        if (settingsState.errors[key]) {
            delete settingsState.errors[key];
            document.getElementById(settingsFieldId(key) + '-error')?.remove();
            target.removeAttribute('aria-invalid');
        }
        if (row.kind === 'bool' || row.kind === 'choice') { settingsRefresh(settingsFieldId(key)); return; }
        // Typing must not rebuild the field, or the caret would jump.
        const meta = target.closest('.st-row')?.querySelector('.st-meta');
        if (meta) meta.innerHTML = settingsMetaHtml(row);
        $('#st-rail').innerHTML = settingsRailHtml();
        $('#st-savebar-slot').innerHTML = settingsSaveBarHtml();
    });
    page.addEventListener('change', event => {
        if (event.target.id === 'st-only-changed') { settingsState.onlyChanged = event.target.checked; settingsRefresh(); }
    });
    page.addEventListener('click', async event => {
        const section = event.target.closest('[data-st-section]');
        if (section) {
            settingsState.section = section.dataset.stSection; settingsState.query = ''; settingsState.onlyChanged = false;
            $('#st-search').value = ''; $('#st-only-changed').checked = false;
            settingsRefresh(); return;
        }
        const reset = event.target.closest('[data-st-reset]');
        if (reset) {
            const row = settingsRow(reset.dataset.stReset);
            // Nothing saved here: undo the edit. Something saved: plan a reset.
            if (row.source === 'app') settingsState.edits[row.key] = null; else delete settingsState.edits[row.key];
            delete settingsState.errors[row.key];
            settingsRefresh(); return;
        }
        const secretButton = event.target.closest('[data-st-secret-edit], [data-st-secret-ask], [data-st-secret-cancel], [data-st-secret-save], [data-st-secret-clear]');
        if (secretButton) { await settingsSecretAction(secretButton); return; }
        const portalButton = event.target.closest('[data-st-portal]');
        if (portalButton) { await settingsPortalAction(portalButton); return; }
        const testButton = event.target.closest('[data-st-test]');
        if (testButton) { await settingsTest(testButton.dataset.stTest); return; }
        const link = event.target.closest('[data-st-page]');
        if (link) { event.preventDefault(); navigate(link.dataset.stPage); return; }
        if (event.target.id === 'st-save') { settingsSave(); return; }
        if (event.target.id === 'st-discard') {
            settingsState.edits = {}; settingsState.errors = {}; settingsState.saveError = '';
            settingsRefresh(); toast('Changes discarded.'); return;
        }
        if (event.target.id === 'st-logs-prune') {
            const button = event.target; button.disabled = true;
            try {
                const result = await apiPost('/api/system/logs/prune');
                settingsState.logs = result;
                settingsState.logsMessage = result.deleted_files
                    ? `Deleted ${result.deleted_files} ${result.deleted_files === 1 ? 'file' : 'files'}, ${(result.deleted_bytes / 1048576).toLocaleString('en', { maximumFractionDigits: 1 })} MB.`
                    : 'Nothing to delete: every log is within the limits.';
            } catch (error) {
                settingsState.logsMessage = `Clean-up did not run: ${error.message}`;
            }
            settingsRefresh();
        }
    });
}
