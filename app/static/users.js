// Dedicated user profiles with optional PostgreSQL identities. Everyone can read
// Users; only the host PC changes them, because a profile decides who signs in
// as whom, whose SQL user a Flow writes with and where Flow emails go.
let usersState = { people: [], query: '', role: 'all', loadError: null };

function usersCountText() {
    const count = usersState.people.length;
    const linked = usersState.people.filter(person => person.sql_username).length;
    return `${count} ${count === 1 ? 'user' : 'users'} · ${linked} SQL ${linked === 1 ? 'identity' : 'identities'} linked`;
}

function usersPasswordCell(person) {
    if (person.sql_password_set) return '<span class="users-password" role="img" aria-label="Saved" title="Saved on the host PC">••••••••</span>';
    return `<span class="users-muted">${person.sql_username ? 'Not set' : '—'}</span>`;
}

// A colleague changes only their own SQL password; the host PC edits whole profiles.
function usersActionCell(person, host) {
    if (host) return `<td><button type="button" class="btn-sm btn-outline" data-edit-user="${person.id}" aria-label="Edit ${esc(person.name)}">Edit</button></td>`;
    if (person.id !== window._currentUser?.person_id) return '<td></td>';
    if (!person.sql_username) return '<td><span class="users-muted">Ask the host PC to add your SQL username</span></td>';
    return `<td><button type="button" class="btn-sm btn-outline" data-own-password="${person.id}">Change SQL password</button></td>`;
}

function usersRowsHtml() {
    const query = usersState.query.trim().toLowerCase();
    const host = _isHostPc();
    const people = usersState.people.filter(person =>
        (usersState.role === 'all' || person.role === usersState.role) &&
        [person.name, person.email, person.sql_username].some(value => String(value || '').toLowerCase().includes(query))
    );
    if (!people.length) {
        return `<div class="users-empty"><strong>${usersState.people.length ? 'No matching users.' : 'Give your flows a person to turn to.'}</strong>
            ${usersState.people.length ? 'Try another name, email or SQL username, or choose Everyone.' : host ? 'Add your BI and business colleagues. Each person then chooses their name when they first open Metronome.' : 'Ask the host PC to add your BI and business colleagues.'}</div>`;
    }
    return `<div class="users-table-wrap" tabindex="0" role="region" aria-label="Users table, scroll for more columns"><table class="users-table">
        <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Email</th><th scope="col">SQL username</th><th scope="col">SQL password</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${people.map(person => `<tr>
            <td><div class="users-person"><span class="users-avatar" aria-hidden="true">${esc(person.name.split(/\s+/).map(part => part[0]).slice(0, 2).join(''))}</span><strong>${esc(person.name)}</strong></div></td>
            <td><span class="badge ${person.role === 'BI' ? 'badge-blue' : 'badge-muted'}">${esc(person.role)}</span></td>
            <td>${person.email ? esc(person.email) : '<span class="users-muted">Not added</span>'}</td>
            <td>${person.sql_username ? `<code>${esc(person.sql_username)}</code>` : '<span class="users-muted">Not linked</span>'}</td>
            <td>${usersPasswordCell(person)}</td>
            ${usersActionCell(person, host)}
        </tr>`).join('')}</tbody></table></div>`;
}

function usersPageHtml() {
    const error = usersState.loadError;
    const host = _isHostPc();
    return `<div class="users-page" id="users-page">
        <header class="page-header users-header"><div><h1>Users</h1><p class="subtitle">Your team, flow owners and SQL identities.</p></div>${host ? `<button id="users-add" ${error ? 'disabled' : ''}>Add user</button>` : ''}</header>
        ${host ? '' : '<p class="users-readonly" role="note">Only the host PC can add or change users. You can change your own SQL password from your row.</p>'}
        <div class="users-feedback" id="users-feedback" role="status" aria-live="polite"></div>
        ${error ? `<section class="users-panel users-load-error" role="alert"><h2>Could not load users</h2><p>${esc(error)}</p><button id="users-retry" class="btn-outline">Try again</button></section>` : `
        <div id="user-editor"></div>
        <div class="users-toolbar"><label class="sr-only" for="users-search">Search users</label><input id="users-search" type="search" placeholder="Search name, email or SQL username" value="${esc(usersState.query)}">
            ${[['all', 'Everyone'], ['BI', 'BI'], ['Business', 'Business']].map(([role, label]) => `<button type="button" class="btn-sm btn-outline" data-user-role="${role}" aria-pressed="${usersState.role === role}">${label}</button>`).join('')}
            <span class="users-muted" id="users-count">${usersCountText()}</span></div>
        <section class="users-panel" aria-label="User directory" id="users-list">${usersRowsHtml()}</section>
        <p class="users-muted">Each Flow inserts into SQL as its owner, signing in with their SQL username and the password saved here by that person or the host PC. Passwords stay in the host PC's settings file and only show as asterisks. The owner's role also becomes the target table owner on the next successful SQL load when that Flow enables SQL table ownership, including existing tables. Already queued runs keep their saved owner.</p>`}
    </div>`;
}

async function renderUsers() {
    await (window._currentUserReady || Promise.resolve());
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
    const add = document.getElementById('users-add');
    if (add) add.onclick = () => openUserEditor();
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
    document.querySelectorAll('[data-own-password]').forEach(button => button.onclick = () =>
        openOwnPasswordEditor(usersState.people.find(person => person.id === Number(button.dataset.ownPassword)))
    );
}

// The signed-in person's own SQL password: save or clear it, nothing else.
function openOwnPasswordEditor(person) {
    const editor = document.getElementById('user-editor');
    if (!editor || !person || editor.querySelector('form[aria-busy="true"]')) return;
    editor.innerHTML = `<section class="users-panel" aria-labelledby="own-password-title">
        <div class="users-panel-head"><h2 id="own-password-title">Your SQL password</h2><button type="button" class="btn-sm btn-outline" id="own-password-close">Cancel</button></div>
        <form class="users-form" id="own-password-form"><div class="users-form-grid">
            <label for="own-sql-password" class="users-span-2"><span>New SQL password for <code>${esc(person.sql_username)}</code></span>
                <input id="own-sql-password" name="password" type="password" autocomplete="new-password" spellcheck="false" maxlength="1024" placeholder="${person.sql_password_set ? '•••••••• saved · type to replace' : 'Not set'}">
                <small>Your Flows sign in to SQL with it. It is saved in Metronome's settings file on the host PC and only shows as asterisks.</small></label>
        </div><div class="users-feedback" id="own-password-feedback" role="status" aria-live="polite"></div>
        <div class="users-actions"><button type="submit" id="own-password-save">Save password</button>${person.sql_password_set ? '<button type="button" class="btn-outline" id="own-password-clear">Clear SQL password</button>' : ''}</div>
        </form></section>`;
    const form = document.getElementById('own-password-form');
    const input = document.getElementById('own-sql-password');
    const feedback = document.getElementById('own-password-feedback');
    const close = () => {
        editor.innerHTML = '';
        document.querySelector(`[data-own-password="${person.id}"]`)?.focus();
    };
    const setBusy = busy => {
        form.setAttribute('aria-busy', String(busy));
        [...form.elements].forEach(element => { element.disabled = busy; });
        document.getElementById('own-password-close').disabled = busy;
    };
    const finish = (saved, message) => {
        usersReplacePerson(saved);
        refreshUsersList();
        close();
        document.getElementById('users-feedback').textContent = message;
    };
    const send = async (request, busyText, message, failure) => {
        feedback.classList.remove('error');
        feedback.textContent = busyText;
        setBusy(true);
        try {
            const saved = await request();
            if (form.isConnected) finish(saved, message);
        } catch (error) {
            if (!form.isConnected) return;
            setBusy(false);
            feedback.classList.add('error');
            feedback.textContent = `${failure} ${error.message}`;
            input.focus();
        }
    };
    document.getElementById('own-password-close').onclick = close;
    form.onsubmit = event => {
        event.preventDefault();
        if (!input.value) {
            feedback.classList.add('error');
            feedback.textContent = 'Enter your new SQL password.';
            input.focus();
            return;
        }
        if (/[\x00-\x1f\x7f]/.test(input.value)) {
            feedback.classList.add('error');
            feedback.textContent = 'SQL password cannot contain line breaks or control characters.';
            input.focus();
            return;
        }
        send(() => apiPut(`/api/people/${person.id}/sql-password`, { password: input.value }),
            'Saving your SQL password…', 'Your SQL password was saved.', 'Could not save. Your entry is still here.');
    };
    const clear = document.getElementById('own-password-clear');
    if (clear) clear.onclick = () => send(() => apiDelete(`/api/people/${person.id}/sql-password`),
        'Clearing your SQL password…', 'Your SQL password was cleared. Your SQL Flows will not run until you save a new one.',
        'Could not clear it.');
    input.focus();
}

function refreshUsersList() {
    document.getElementById('users-list').innerHTML = usersRowsHtml();
    document.getElementById('users-count').textContent = usersCountText();
    bindUsersRows();
}

function usersReplacePerson(saved) {
    const known = usersState.people.some(item => item.id === saved.id);
    usersState.people = known ? usersState.people.map(item => item.id === saved.id ? saved : item) : [...usersState.people, saved];
    usersState.people.sort((a, b) => a.name.localeCompare(b.name));
}

function openUserEditor(person) {
    const editor = document.getElementById('user-editor');
    // A second editor must not replace a profile while its save/delete is pending.
    if (!editor || editor.querySelector('form[aria-busy="true"]')) return;
    const signedIn = person?.signed_in_computers || 0;
    editor.innerHTML = `<section class="users-panel" aria-labelledby="user-editor-title">
        <div class="users-panel-head"><h2 id="user-editor-title">${person ? 'Edit user' : 'Add user'}</h2><button type="button" class="btn-sm btn-outline" id="user-close">Cancel</button></div>
        <form class="users-form" id="user-form"><div class="users-form-grid">
            <label for="user-name">Name<input id="user-name" name="name" required maxlength="200" value="${esc(person?.name || '')}" autocomplete="name"></label>
            <label for="user-role">Role<select id="user-role" name="role" aria-label="Role"><option ${person?.role !== 'Business' ? 'selected' : ''}>BI</option><option ${person?.role === 'Business' ? 'selected' : ''}>Business</option></select></label>
            <label for="user-email">Email <small>Optional · used for flow owner notifications</small><input id="user-email" name="email" type="email" value="${esc(person?.email || '')}" autocomplete="email"></label>
            <label for="user-sql">SQL username <small>Optional · this person's own SQL user</small><input id="user-sql" name="sql_username" value="${esc(person?.sql_username || '')}" autocomplete="off" spellcheck="false"><small>Use an existing PostgreSQL role. Flows assigned to this person sign in with it to insert into SQL. Changing it clears the saved SQL password. Clearing this field leaves existing table ownership unchanged.</small></label>
            <label for="user-sql-password" class="users-span-2">SQL password <small>Host PC only · saved in Metronome's settings file</small>
                <input id="user-sql-password" name="sql_password" type="password" autocomplete="new-password" spellcheck="false" maxlength="1024" placeholder="${person?.sql_password_set ? '•••••••• saved · type to replace' : 'Not set'}">
                <small id="user-sql-password-help">${person?.sql_password_set ? 'A password is saved and only shows as asterisks. Leave this blank to keep it.' : 'Without it, Flows assigned to this person cannot insert into SQL.'}</small></label>
        </div><div class="users-feedback" id="user-form-feedback" role="status" aria-live="polite"></div>
        <div class="users-actions"><button type="submit" id="user-save">${person ? 'Save changes' : 'Add user'}</button>${person?.sql_password_set ? '<button type="button" class="btn-outline" id="user-password-clear">Clear SQL password</button>' : ''}${person && signedIn ? `<button type="button" class="btn-outline" id="user-sign-out">Sign out ${signedIn} computer${signedIn === 1 ? '' : 's'}</button>` : ''}${person ? '<button type="button" class="btn-outline btn-danger-outline" id="user-delete">Delete user</button>' : ''}</div>
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
    // Account actions apply at once and keep the editor open on the updated profile.
    const accountAction = async (button, busyText, request, message, failure) => {
        const feedback = document.getElementById('user-form-feedback');
        feedback.classList.remove('error');
        feedback.textContent = busyText;
        setBusy(true);
        try {
            const saved = await request();
            if (!form.isConnected) return;
            usersReplacePerson(saved);
            refreshUsersList();
            setBusy(false);
            openUserEditor(saved);
            document.getElementById('user-form-feedback').textContent = message;
        } catch (error) {
            if (!form.isConnected) return;
            setBusy(false);
            feedback.classList.add('error');
            feedback.textContent = `${failure} ${error.message}`;
            button.focus();
        }
    };
    document.getElementById('user-close').onclick = close;
    form.onsubmit = async event => {
        event.preventDefault();
        const button = document.getElementById('user-save');
        const feedback = document.getElementById('user-form-feedback');
        const body = Object.fromEntries(new FormData(form));
        const password = body.sql_password || '';
        delete body.sql_password;
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
        if (password && !body.sql_username) return invalid('Add a SQL username before saving a SQL password.', 'user-sql');
        if (/[\x00-\x1f\x7f]/.test(password)) return invalid('SQL password cannot contain line breaks or control characters.', 'user-sql-password');
        feedback.classList.remove('error');
        feedback.textContent = 'Saving user…';
        setBusy(true);
        button.textContent = 'Saving…';
        let saved = null;
        try {
            saved = await (person ? apiPatch(`/api/people/${person.id}`, body) : apiPostJson('/api/people', body));
            if (password) saved = await apiPut(`/api/people/${saved.id}/sql-password`, { password });
            if (!form.isConnected) return;
            usersReplacePerson(saved);
            updateSuccess(`${saved.name} ${person ? 'updated' : 'added'}.`);
        } catch (error) {
            if (!form.isConnected) return;
            if (saved) {
                // The profile saved; only its password did not. Keep editing that profile.
                usersReplacePerson(saved);
                refreshUsersList();
                person = saved;
            }
            feedback.classList.add('error');
            feedback.textContent = saved
                ? `${saved.name} was saved, but the SQL password was not. ${error.message}`
                : `Could not save. Your changes are still here. ${error.message}`;
        } finally {
            if (form.isConnected) {
                setBusy(false);
                button.textContent = person ? 'Save changes' : 'Add user';
            }
        }
    };
    const clearPassword = document.getElementById('user-password-clear');
    if (clearPassword) clearPassword.onclick = () => accountAction(clearPassword, 'Clearing SQL password…',
        () => apiDelete(`/api/people/${person.id}/sql-password`), 'SQL password cleared.', 'Could not clear the SQL password.');
    const signOut = document.getElementById('user-sign-out');
    if (signOut) signOut.onclick = () => accountAction(signOut, 'Signing computers out…',
        () => apiDelete(`/api/people/${person.id}/sign-ins`), 'Signed out. Those computers choose a user again on their next visit.', 'Could not sign those computers out.');
    if (person) document.getElementById('user-delete').onclick = () => {
        const confirmation = document.getElementById('user-delete-confirm');
        confirmation.innerHTML = `<div class="users-delete"><p>Delete <strong>${esc(person.name)}</strong>? Their flows will stay, with the owner set to Unassigned. Their saved SQL username will be removed from Metronome, along with their SQL password and sign-ins.</p>
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
