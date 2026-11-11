// System > Settings. One page for every app-wide setting, drawn from the
// server's registry (GET /api/system/settings). Everyone can read it; only the
// host PC can save. Unsaved edits live in settingsState, so a failed save or a
// visit to another page loses nothing.
let settingsState = {
    sections: [], canEdit: false, section: null, query: '', onlyChanged: false,
    edits: {}, errors: {}, saving: false, saveError: '', loadError: null, logs: null, logsMessage: '',
};

const SETTINGS_SOURCE_TEXT = { default: 'Default', app: 'Set here', env: 'From the .env file' };

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
    return typeof value === 'number' ? value.toLocaleString('en') : String(value);
}

function settingsFieldId(key) {
    return 'st-' + key.replace(/[^A-Za-z0-9]/g, '-');
}

function settingsMetaHtml(row) {
    const dirty = settingsIsDirty(row);
    const pendingReset = settingsState.edits[row.key] === null;
    const chips = [];
    if (dirty) chips.push(`<span class="st-chip st-chip-unsaved">${pendingReset ? 'Will reset' : 'Unsaved'}</span>`);
    else chips.push(`<span class="st-chip ${row.source === 'app' ? 'st-chip-set' : ''}">${SETTINGS_SOURCE_TEXT[row.source] || 'Default'}</span>`);
    chips.push(`<span class="st-chip">${esc(row.applies_text)}</span>`);
    const canReset = settingsState.canEdit && !pendingReset && (row.source === 'app' || dirty);
    const reset = canReset
        ? `<button type="button" class="st-link" data-st-reset="${esc(row.key)}">Reset to default (${esc(settingsShow(row, row.default))})</button>` : '';
    return chips.join('') + reset;
}

function settingsControlHtml(row) {
    const id = settingsFieldId(row.key);
    const value = settingsState.edits[row.key] === null ? row.default : settingsCurrent(row);
    const off = !settingsState.canEdit || settingsState.saving ? ' disabled' : '';
    const error = settingsState.errors[row.key];
    const invalid = error ? ` aria-invalid="true" aria-describedby="${id}-error"` : '';
    const range = row.minimum !== null && row.maximum !== null
        ? `${Number(row.minimum).toLocaleString('en')} to ${Number(row.maximum).toLocaleString('en')}` : '';
    let html;
    if (row.kind === 'int' || row.kind === 'float') {
        html = `<div class="st-field"><input type="number" id="${id}" data-st-key="${esc(row.key)}" value="${esc(value)}" min="${row.minimum ?? ''}" max="${row.maximum ?? ''}" step="${row.kind === 'int' ? 1 : 'any'}"${off}${invalid}>
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

function settingsGroupHtml(section, group, prefix) {
    const rows = group.settings.map(row => ({ ...row, section, group })).filter(settingsMatches);
    if (!rows.length) return '';
    const extra = section.id === 'updates' && group.title === 'Logs' && !settingsState.query && !settingsState.onlyChanged ? settingsLogsHtml() : '';
    return `<div class="st-group"><h3>${prefix ? esc(prefix) + ' · ' : ''}${esc(group.title)}</h3>${rows.map(settingsRowHtml).join('')}${extra}</div>`;
}

function settingsPanelHtml() {
    const filtering = settingsState.query || settingsState.onlyChanged;
    if (filtering) {
        const body = settingsState.sections.map(section =>
            section.groups.map(group => settingsGroupHtml(section, group, section.name)).join('')).join('');
        const title = settingsState.query ? `Settings matching "${esc(settingsState.query)}"` : 'Settings changed from their defaults';
        return `<header><h2>${title}</h2></header>${body || '<p class="st-empty">Nothing matches. Clear the search box or untick "Only changed from default" to see every setting.</p>'}`;
    }
    const section = settingsState.sections.find(item => item.id === settingsState.section) || settingsState.sections[0];
    if (!section) return '<p class="st-empty">No settings are available.</p>';
    const links = (section.links || []).map(link =>
        `<li><a href="#${esc(link.page)}" data-st-page="${esc(link.page)}">${esc(link.label)}</a>${link.note ? ` <span class="st-help">${esc(link.note)}</span>` : ''}</li>`).join('');
    return `<header><h2>${esc(section.name)}</h2><p>${esc(section.blurb)}</p></header>
        ${section.groups.map(group => settingsGroupHtml(section, group, '')).join('')}
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
