// Dedicated user profiles with optional PostgreSQL table ownership identities.
let usersState = { people: [], query: '', role: 'all', loadError: null };

function usersCountText() {
    const count = usersState.people.length;
    const linked = usersState.people.filter(person => person.sql_username).length;
    return `${count} ${count === 1 ? 'user' : 'users'} · ${linked} SQL ${linked === 1 ? 'identity' : 'identities'} linked`;
}

function usersRowsHtml() {
    const query = usersState.query.trim().toLowerCase();
    const people = usersState.people.filter(person =>
        (usersState.role === 'all' || person.role === usersState.role) &&
        [person.name, person.email, person.sql_username].some(value => String(value || '').toLowerCase().includes(query))
    );
    if (!people.length) {
        return `<div class="users-empty"><strong>${usersState.people.length ? 'No matching users.' : 'Give your flows a person to turn to.'}</strong>
            ${usersState.people.length ? 'Try another name, email or SQL username, or choose Everyone.' : 'Add your BI and business colleagues, then assign them as flow owners.'}</div>`;
    }
    return `<div class="users-table-wrap" tabindex="0" role="region" aria-label="Users table, scroll for more columns"><table class="users-table">
        <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Email</th><th scope="col">SQL username</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${people.map(person => `<tr>
            <td><div class="users-person"><span class="users-avatar" aria-hidden="true">${esc(person.name.split(/\s+/).map(part => part[0]).slice(0, 2).join(''))}</span><strong>${esc(person.name)}</strong></div></td>
            <td><span class="badge ${person.role === 'BI' ? 'badge-blue' : 'badge-muted'}">${esc(person.role)}</span></td>
            <td>${person.email ? esc(person.email) : '<span class="users-muted">Not added</span>'}</td>
            <td>${person.sql_username ? `<code>${esc(person.sql_username)}</code>` : '<span class="users-muted">Not linked</span>'}</td>
            <td><button type="button" class="btn-sm btn-outline" data-edit-user="${person.id}" aria-label="Edit ${esc(person.name)}">Edit</button></td>
        </tr>`).join('')}</tbody></table></div>`;
}

function usersPageHtml() {
    const error = usersState.loadError;
    return `<div class="users-page" id="users-page">
        <header class="page-header users-header"><div><h1>Users</h1><p class="subtitle">Your team, flow owners and SQL identities.</p></div><button id="users-add" ${error ? 'disabled' : ''}>Add user</button></header>
        <div class="users-feedback" id="users-feedback" role="status" aria-live="polite"></div>
        ${error ? `<section class="users-panel users-load-error" role="alert"><h2>Could not load users</h2><p>${esc(error)}</p><button id="users-retry" class="btn-outline">Try again</button></section>` : `
        <div id="user-editor"></div>
        <div class="users-toolbar"><label class="sr-only" for="users-search">Search users</label><input id="users-search" type="search" placeholder="Search name, email or SQL username" value="${esc(usersState.query)}">
            ${[['all', 'Everyone'], ['BI', 'BI'], ['Business', 'Business']].map(([role, label]) => `<button type="button" class="btn-sm btn-outline" data-user-role="${role}" aria-pressed="${usersState.role === role}">${label}</button>`).join('')}
            <span class="users-muted" id="users-count">${usersCountText()}</span></div>
        <section class="users-panel" aria-label="User directory" id="users-list">${usersRowsHtml()}</section>
        <p class="users-muted">SQL usernames are optional. Assigned Flows apply this role as their target table owner on the next successful SQL load, including existing tables. Saving a profile does not change the database immediately. Already queued runs keep their saved owner.</p>`}
    </div>`;
}

async function renderUsers() {
    try {
        usersState.people = await api('/api/people');
        usersState.loadError = null;
    } catch (error) {
        usersState.loadError = error.message;
    }
    return usersPageHtml();
}

function bindUsersPage() {
    const retry = document.getElementById('users-retry');
    if (retry) {
        retry.onclick = () => navigate('users');
        return;
    }
    document.getElementById('users-add').onclick = () => openUserEditor();
    document.getElementById('users-search').oninput = event => {
        usersState.query = event.target.value;
        refreshUsersList();
    };
    document.querySelectorAll('[data-user-role]').forEach(button => button.onclick = () => {
        usersState.role = button.dataset.userRole;
        document.querySelectorAll('[data-user-role]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        refreshUsersList();
    });
    bindUsersRows();
}

function bindUsersRows() {
    document.querySelectorAll('[data-edit-user]').forEach(button => button.onclick = () =>
        openUserEditor(usersState.people.find(person => person.id === Number(button.dataset.editUser)))
    );
}

function refreshUsersList() {
    document.getElementById('users-list').innerHTML = usersRowsHtml();
    document.getElementById('users-count').textContent = usersCountText();
    bindUsersRows();
}

function openUserEditor(person) {
    const editor = document.getElementById('user-editor');
    // A second editor must not replace a profile while its save/delete is pending.
    if (!editor || editor.querySelector('form[aria-busy="true"]')) return;
    editor.innerHTML = `<section class="users-panel" aria-labelledby="user-editor-title">
        <div class="users-panel-head"><h2 id="user-editor-title">${person ? 'Edit user' : 'Add user'}</h2><button type="button" class="btn-sm btn-outline" id="user-close">Cancel</button></div>
        <form class="users-form" id="user-form"><div class="users-form-grid">
            <label for="user-name">Name<input id="user-name" name="name" required maxlength="200" value="${esc(person?.name || '')}" autocomplete="name"></label>
            <label for="user-role">Role<select id="user-role" name="role" aria-label="Role"><option ${person?.role !== 'Business' ? 'selected' : ''}>BI</option><option ${person?.role === 'Business' ? 'selected' : ''}>Business</option></select></label>
            <label for="user-email">Email <small>Optional · used for flow owner notifications</small><input id="user-email" name="email" type="email" value="${esc(person?.email || '')}" autocomplete="email"></label>
            <label for="user-sql">SQL username <small>Optional · SQL table ownership</small><input id="user-sql" name="sql_username" value="${esc(person?.sql_username || '')}" autocomplete="off" spellcheck="false"><small>Use an existing PostgreSQL role. Ownership applies when an assigned Flow successfully loads SQL; the Metronome SQL account must have permission. Clearing this field leaves existing table ownership unchanged.</small></label>
        </div><div class="users-feedback" id="user-form-feedback" role="status" aria-live="polite"></div>
        <div class="users-actions"><button type="submit" id="user-save">${person ? 'Save changes' : 'Add user'}</button>${person ? '<button type="button" class="btn-outline btn-danger-outline" id="user-delete">Delete user</button>' : ''}</div>
        <div id="user-delete-confirm"></div></form></section>`;
    const form = document.getElementById('user-form');
    const close = () => {
        editor.innerHTML = '';
        document.getElementById('users-add').focus();
    };
    const setBusy = busy => {
        form.setAttribute('aria-busy', String(busy));
        [...form.elements].forEach(element => { element.disabled = busy; });
        document.getElementById('user-close').disabled = busy;
        document.getElementById('users-add').disabled = busy;
        document.querySelectorAll('[data-edit-user]').forEach(button => { button.disabled = busy; });
    };
    const updateSuccess = message => {
        refreshUsersList();
        setBusy(false);
        close();
        document.getElementById('users-feedback').textContent = message;
    };
    document.getElementById('user-close').onclick = close;
    form.onsubmit = async event => {
        event.preventDefault();
        const button = document.getElementById('user-save');
        const feedback = document.getElementById('user-form-feedback');
        const body = Object.fromEntries(new FormData(form));
        body.name = body.name.trim();
        body.email = body.email.trim() || null;
        body.sql_username = body.sql_username.trim() || null;
        const invalid = (message, id) => {
            feedback.classList.add('error');
            feedback.textContent = message;
            document.getElementById(id).focus();
        };
        if (!body.name) return invalid('Enter a name.', 'user-name');
        if (new TextEncoder().encode(body.sql_username || '').length > 63) return invalid('SQL username must be 63 UTF-8 bytes or fewer.', 'user-sql');
        if (/[\x00-\x1f\x7f]/.test(body.sql_username || '')) return invalid('SQL username cannot contain control characters.', 'user-sql');
        feedback.classList.remove('error');
        feedback.textContent = 'Saving user…';
        setBusy(true);
        button.textContent = 'Saving…';
        try {
            const saved = await (person ? apiPatch(`/api/people/${person.id}`, body) : apiPostJson('/api/people', body));
            if (!form.isConnected) return;
            usersState.people = person ? usersState.people.map(item => item.id === saved.id ? saved : item) : [...usersState.people, saved];
            usersState.people.sort((a, b) => a.name.localeCompare(b.name));
            updateSuccess(`${saved.name} ${person ? 'updated' : 'added'}.`);
        } catch (error) {
            if (!form.isConnected) return;
            feedback.classList.add('error');
            feedback.textContent = `Could not save. Your changes are still here. ${error.message}`;
        } finally {
            if (form.isConnected) {
                setBusy(false);
                button.textContent = person ? 'Save changes' : 'Add user';
            }
        }
    };
    if (person) document.getElementById('user-delete').onclick = () => {
        const confirmation = document.getElementById('user-delete-confirm');
        confirmation.innerHTML = `<div class="users-delete"><p>Delete <strong>${esc(person.name)}</strong>? Their flows will stay, with the owner set to Unassigned. Their saved SQL username will be removed from Metronome.</p>
            <div class="users-actions"><button type="button" class="btn-danger-outline btn-outline" id="user-delete-yes">Delete ${esc(person.name)}</button><button type="button" class="btn-outline" id="user-delete-no">Keep user</button></div>
            <p id="user-delete-feedback" role="status" aria-live="polite"></p></div>`;
        document.getElementById('user-delete-no').onclick = () => {
            confirmation.innerHTML = '';
            document.getElementById('user-delete').focus();
        };
        document.getElementById('user-delete-yes').onclick = async () => {
            const feedback = document.getElementById('user-delete-feedback');
            feedback.textContent = 'Deleting user…';
            setBusy(true);
            try {
                await apiDelete(`/api/people/${person.id}`);
                if (!form.isConnected) return;
                usersState.people = usersState.people.filter(item => item.id !== person.id);
                updateSuccess(`${person.name} deleted. Their flows were kept.`);
            } catch (error) {
                if (form.isConnected) feedback.textContent = `Could not delete. ${error.message}`;
            } finally {
                if (form.isConnected) setBusy(false);
            }
        };
        document.getElementById('user-delete-no').focus();
    };
    document.getElementById('user-name').focus();
}
