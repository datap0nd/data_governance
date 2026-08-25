// ── Simple markdown renderer ──

function renderMd(text) {
    if (!text) return "";
    let html = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        // headers
        .replace(/^### (.+)$/gm, "<h4>$1</h4>")
        .replace(/^## (.+)$/gm, "<h3 style='font-size:0.88rem;margin:0.4rem 0 0.25rem;color:var(--text)'>$1</h3>")
        // bold
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        // tables
        .replace(/^\|(.+)\|$/gm, (m) => {
            const cells = m.split("|").filter(c => c.trim() !== "");
            if (cells.every(c => /^[\s-:]+$/.test(c))) return "<!--sep-->";
            return cells.map(c => `<td>${c.trim()}</td>`).join("");
        })
        // list items
        .replace(/^- (.+)$/gm, "<li>$1</li>")
        .replace(/^\d+\. (.+)$/gm, "<li>$1</li>");
    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>.+<\/li>\n?)+)/g, "<ul>$1</ul>");
    // Wrap table rows
    html = html.replace(/((?:<td>.+<\/td>\n?)+)/g, (block) => {
        const rows = block.trim().split("\n").filter(r => r.trim() && !r.includes("<!--sep-->"));
        if (rows.length === 0) return "";
        const thead = `<tr>${rows[0].replace(/<td>/g, "<th>").replace(/<\/td>/g, "</th>")}</tr>`;
        const tbody = rows.slice(1).map(r => `<tr>${r}</tr>`).join("");
        return `<table>${thead}${tbody}</table>`;
    });
    html = html.replace(/<!--sep-->\n?/g, "");
    // Paragraphs
    html = html.split("\n\n").map(p => {
        p = p.trim();
        if (!p || p.startsWith("<h") || p.startsWith("<ul") || p.startsWith("<table") || p.startsWith("<li")) return p;
        return `<p>${p}</p>`;
    }).join("\n");
    html = html.replace(/\n/g, " ").replace(/  +/g, " ");
    return html;
}


// ── Helpers ──

function esc(str) {
    if (str == null) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtInt(value) {
    return Number(value || 0).toLocaleString();
}

function fmtRows(value) {
    if (value == null || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return n.toLocaleString();
}

function rowCountHtml(value) {
    if (value == null || value === "") return '<span style="color:var(--text-dim)">-</span>';
    const n = Number(value);
    if (!Number.isFinite(n)) return '<span style="color:var(--text-dim)">-</span>';
    const cls = n === 0 ? "badge badge-red" : "badge badge-muted";
    const title = n === 0 ? "Latest probe found no data rows" : "Rows from latest probe";
    return `<span class="${cls}" title="${title}">${fmtInt(n)}</span>`;
}

function _getClientKey() {
    const keyName = "dg-client-key";
    let key = localStorage.getItem(keyName);
    if (!key) {
        key = (window.crypto && window.crypto.randomUUID)
            ? window.crypto.randomUUID()
            : `client-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        localStorage.setItem(keyName, key);
    }
    document.cookie = `dg_client_key=${encodeURIComponent(key)}; Path=/; Max-Age=34560000; SameSite=Lax`;
    return key;
}

function apiHeaders(extra = {}) {
    return { "X-Client-Key": _getClientKey(), ...extra };
}

async function apiError(res) {
    let detail = "";
    try {
        const payload = await res.json();
        if (typeof payload.detail === "string") detail = payload.detail;
        else if (Array.isArray(payload.detail)) {
            detail = payload.detail.map(item => item.msg || item.message || "Invalid value").join("; ");
        }
    } catch (_) {}
    return new Error(detail || `API error: ${res.status}`);
}

async function api(path) {
    const res = await fetch(path, { headers: apiHeaders() });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

async function apiPost(path) {
    const res = await fetch(path, { method: "POST", headers: apiHeaders() });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

async function apiPatch(path, body) {
    const res = await fetch(path, {
        method: "PATCH",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

async function apiPostJson(path, body) {
    const res = await fetch(path, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

async function apiPostForm(path, body) {
    const res = await fetch(path, { method: "POST", headers: apiHeaders(), body });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

async function apiPut(path, body) {
    const res = await fetch(path, {
        method: "PUT",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
    });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

async function apiDelete(path) {
    const res = await fetch(path, { method: "DELETE", headers: apiHeaders() });
    if (!res.ok) throw await apiError(res);
    return res.json();
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

// PBI refresh overdue detection (mirrors backend logic)
const _PBI_WEEKDAYS = new Set(["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]);
function _pbiScheduleDays(schedule) {
    if (!schedule) return 0;
    const dayPart = schedule.includes(" @ ") ? schedule.split(" @ ")[0] : schedule;
    return dayPart.split(",").filter(d => _PBI_WEEKDAYS.has(d.trim().toLowerCase())).length;
}
function _isPbiOverdue(r) {
    const dpw = _pbiScheduleDays(r.pbi_refresh_schedule);
    if (dpw === 0) return false;
    if (!r.pbi_last_refresh_at) return true;
    try {
        const last = new Date(r.pbi_last_refresh_at);
        const hours = (Date.now() - last.getTime()) / 3600000;
        return hours > (7 / dpw + 1) * 24;
    } catch (_) { return false; }
}

// Archive state per page (persisted in sessionStorage)
const _archiveShow = {};
function _isShowingArchived(page) {
    if (_archiveShow[page] === undefined) {
        _archiveShow[page] = sessionStorage.getItem("archive_" + page) === "1";
    }
    return _archiveShow[page];
}
function _toggleShowArchived(page) {
    _archiveShow[page] = !_isShowingArchived(page);
    sessionStorage.setItem("archive_" + page, _archiveShow[page] ? "1" : "0");
    return _archiveShow[page];
}
function _archiveToggleHtml(page) {
    const on = _isShowingArchived(page);
    return `<button class="btn-archive-toggle${on ? ' active' : ''}" id="btn-toggle-archived" data-page="${page}" title="Show/hide archived items">${on ? 'Hide Archived' : 'Show Archived'}</button>`;
}
function _archiveColDef(entityType) {
    return {
        key: "_archive", label: "", width: COL_W.xs, filterable: false, sortable: false,
        render: item => {
            const isArchived = item.archived;
            return `<button class="btn-archive-action" data-entity-type="${entityType}" data-entity-id="${item.id}" data-archived="${isArchived ? '1' : '0'}" title="${isArchived ? 'Unarchive' : 'Archive'}">${isArchived ? 'Restore' : 'Archive'}</button>`;
        },
    };
}
// Global event delegation for archive action buttons.
// Bound once on document so it survives tbody rebuilds from sort/filter.
let _archiveDelegated = false;
function _initArchiveDelegation() {
    if (_archiveDelegated) return;
    _archiveDelegated = true;
    document.addEventListener("click", async (e) => {
        const btn = e.target.closest(".btn-archive-action");
        if (!btn) return;
        e.stopPropagation();
        const entityType = btn.dataset.entityType;
        const entityId = btn.dataset.entityId;
        try {
            await apiPost(`/api/archive/${entityType}/${entityId}`);
            toast(btn.dataset.archived === "1" ? "Restored" : "Archived");
            if (typeof currentPage !== "undefined") navigate(currentPage);
        } catch (err) {
            toast("Failed: " + err.message);
        }
    });
}

function _bindArchiveButtons(reloadFn) {
    _initArchiveDelegation();
    const toggleBtn = document.getElementById("btn-toggle-archived");
    if (toggleBtn) {
        toggleBtn.addEventListener("click", () => {
            _toggleShowArchived(toggleBtn.dataset.page);
            reloadFn();
        });
    }
}

function statusBadge(status) {
    if (!status) return '<span class="badge badge-yellow">not probed</span>';
    const s = status.toLowerCase();
    if (s === "fresh" || s === "healthy" || s === "current" || s === "pass" || s === "completed")
        return `<span class="badge badge-green">healthy</span>`;
    if (s === "stale" || s === "at risk" || s === "stale sources" || s === "warn" || s === "warning")
        return `<span class="badge badge-red">degraded</span>`;
    if (s === "outdated" || s === "degraded" || s === "outdated sources")
        return `<span class="badge badge-red">degraded</span>`;
    if (s === "error" || s === "fail" || s === "failed" || s === "critical")
        return `<span class="badge badge-red">${status}</span>`;
    if (s === "no_connection")
        return '<span class="badge badge-yellow">no connection</span>';
    if (s === "no_rule")
        return '<span class="badge badge-yellow" title="No freshness rule set for this source">no rule</span>';
    if (s === "unknown")
        return '<span class="badge badge-yellow">not probed</span>';
    return `<span class="badge badge-muted">${status}</span>`;
}

function actionStatusBadge(status) {
    const colors = {
        open: "badge-red",
        acknowledged: "badge-blue",
        investigating: "badge-yellow",
        expected: "badge-muted",
        resolved: "badge-green",
    };
    return `<span class="badge ${colors[status] || "badge-muted"}">${status}</span>`;
}

function actionTypeBadge(type, neutral = false) {
    const labels = {
        stale_source: "Stale Source",
        outdated_source: "Outdated Source",
        error_source: "Source Error",
        empty_source: "Empty Source",
        broken_ref: "Broken Ref",
        changed_query: "Query Changed",
        refresh_failed: "Refresh Failed",
        refresh_overdue: "Refresh Overdue",
        task_failed: "Task Failed",
        script_failed: "Script Failed",
        schedule_mismatch: "Stale vs Source",
        data_quality: "Data Quality",
        dependency_stale: "Upstream Newer",
        schedule_discrepancy: "Schedule Mismatch",
        best_practice: "Model Quality",
        documentation_missing: "Documentation",
    };
    const colors = {
        stale_source: "badge-red",
        outdated_source: "badge-red",
        error_source: "badge-red",
        empty_source: "badge-red",
        broken_ref: "badge-red",
        changed_query: "badge-blue",
        refresh_failed: "badge-red",
        refresh_overdue: "badge-yellow",
        task_failed: "badge-red",
        script_failed: "badge-red",
        schedule_mismatch: "badge-yellow",
        data_quality: "badge-red",
        dependency_stale: "badge-yellow",
        schedule_discrepancy: "badge-yellow",
        best_practice: "badge-yellow",
        documentation_missing: "badge-yellow",
    };
    const badgeClass = neutral ? "badge-muted alerts-issue-label" : (colors[type] || "badge-muted");
    return `<span class="badge ${badgeClass}">${labels[type] || type}</span>`;
}

function actionTypeLabel(type) {
    const labels = {
        stale_source: "Stale source",
        outdated_source: "Outdated source",
        error_source: "Source error",
        empty_source: "Empty source",
        broken_ref: "Broken reference",
        changed_query: "Query changed",
        refresh_failed: "Refresh failed",
        refresh_overdue: "Refresh overdue",
        task_failed: "Scheduled task failed",
        script_failed: "Script failed",
        schedule_mismatch: "Stale vs source",
        data_quality: "Data-quality check failed",
        dependency_stale: "Upstream data is newer",
        schedule_discrepancy: "Refresh schedule mismatch",
        best_practice: "Model quality issue",
        documentation_missing: "Documentation incomplete",
    };
    return labels[type] || String(type || "Alert").replace(/_/g, " ");
}

function alertAssetKind(action) {
    const assetType = String(action.asset_type || "").toLowerCase();
    if (assetType === "report") return "powerbi";
    if (["flow", "script", "scheduled_task"].includes(assetType)) return "flow";

    const sourceType = String(action.source_type || "").toLowerCase();
    const sourceName = String(action.asset_name || action.source_name || "").toLowerCase();
    if (["excel", "csv", "file", "folder"].some(type => sourceType.includes(type)) ||
        /\.(xlsx?|xlsm|csv)(?:$|[?#])/.test(sourceName)) {
        return "excel";
    }
    return "sql";
}

function alertAssetLogo(action) {
    const kind = alertAssetKind(action);
    const labels = {
        powerbi: "Power BI report",
        excel: "Excel source",
        sql: "SQL source",
        flow: "Flow automation",
    };
    const icons = {
        powerbi: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="11" width="3" height="8" rx="1"/><rect x="9" y="7" width="3" height="12" rx="1"/><rect x="14" y="4" width="3" height="15" rx="1"/><rect x="19" y="9" width="2" height="10" rx="1"/></svg>`,
        excel: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h13a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/><path d="m7.5 8 5 8m0-8-5 8M14 8h3m-3 4h3m-3 4h3"/></svg>`,
        sql: `<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>`,
        flow: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="10" cy="18" r="2.5"/><path d="M8.4 6.4 15.5 7.6M16.5 10l-5.2 5.8M8.5 15.8 6.7 8.4"/></svg>`,
    };
    return `<span class="alert-source-logo alert-source-logo-${kind}" role="img" aria-label="${labels[kind]}" title="${labels[kind]}">${icons[kind]}</span>`;
}

function _notifySlaIssueReasons(type) {
    if (type === "refresh_failed" || type === "refresh_overdue" || type === "schedule_mismatch") {
        return [
            "The issue appears to be caused by an outdated data source.",
            "The issue may be related to internet or gateway connectivity.",
            "The issue may be related to scraping or automation.",
            "The root cause is still under investigation.",
        ];
    }
    if (type === "stale_source" || type === "outdated_source" || type === "error_source" || type === "empty_source") {
        return [
            "The source data has not arrived or has not refreshed as expected.",
            "The upstream system appears to be delayed.",
            "The issue may be related to scraping or automation.",
            "The root cause is still under investigation.",
        ];
    }
    if (type === "task_failed" || type === "script_failed") {
        return [
            "The scheduled automation failed and needs a rerun after investigation.",
            "The issue may be related to credentials, access, or a script dependency.",
            "The issue may be related to internet or gateway connectivity.",
            "The root cause is still under investigation.",
        ];
    }
    if (type === "broken_ref" || type === "changed_query") {
        return [
            "The source reference or query changed and needs confirmation.",
            "The downstream report needs a source mapping review.",
            "The root cause is still under investigation.",
        ];
    }
    return ["The root cause is still under investigation."];
}

function _notifySlaDefaultSla(type) {
    if (["refresh_failed", "error_source", "empty_source", "task_failed", "script_failed"].includes(type)) {
        return { value: 4, unit: "hours" };
    }
    if (["refresh_overdue", "schedule_mismatch", "stale_source", "outdated_source"].includes(type)) {
        return { value: 1, unit: "days" };
    }
    return { value: 2, unit: "days" };
}

function _notifySlaPersonByName(name) {
    const target = String(name || "").trim().toLowerCase();
    if (!target) return null;
    return (window._dashboardPeople || []).find(p => String(p.name || "").trim().toLowerCase() === target) || null;
}

function _notifySlaReportForAction(action) {
    const reports = window._dashboardReports || [];
    const reportId = action.report_id || (action.asset_type === "report" ? action.asset_id : null) || action.top_report_id;
    if (reportId) {
        const found = reports.find(r => Number(r.id) === Number(reportId));
        if (found) return found;
    }
    const name = action.report_name || (action.asset_type === "report" ? action.asset_name : null) || action.top_report_name;
    if (!name) return null;
    return reports.find(r => r.name === name) || null;
}

function _notifySlaSourceForAction(action) {
    const sources = window._dashboardSources || [];
    const sourceId = action.source_id || (action.asset_type === "source" ? action.asset_id : null);
    if (sourceId) {
        const found = sources.find(s => Number(s.id) === Number(sourceId));
        if (found) return found;
    }
    const name = action.source_name || (action.asset_type === "source" ? action.asset_name : null);
    if (!name) return null;
    return sources.find(s => s.name === name) || null;
}

function _notifySlaRecipientSuggestions(action) {
    const selected = new Map();
    const add = (name, reason) => {
        const person = _notifySlaPersonByName(name);
        if (!person || !person.email) return;
        const key = person.email.toLowerCase();
        if (!selected.has(key)) selected.set(key, { person, reason });
    };

    add(action.assigned_to, "Assigned owner");
    const source = _notifySlaSourceForAction(action);
    const report = _notifySlaReportForAction(action);
    if (source) add(source.owner, "Source owner");
    if (report) {
        add(report.owner, "Report owner");
        add(report.business_owner, "Business owner");
    }
    return selected;
}

function _notifySlaEmailTokens(value) {
    return String(value || "")
        .split(/[\s,;]+/)
        .map(v => v.trim())
        .filter(Boolean);
}

function _notifySlaValidEmail(value) {
    return /^[^\s@]+@[^\s@]+$/.test(String(value || "").trim());
}

function _notifySlaCollectRecipients() {
    const picked = [];
    document.querySelectorAll(".notify-sla-recipient-check:checked").forEach(input => {
        if (input.dataset.email) picked.push(input.dataset.email);
    });
    const customInput = document.getElementById("notify-sla-custom-emails");
    const tokens = _notifySlaEmailTokens(customInput ? customInput.value : "");
    const invalid = tokens.filter(email => !_notifySlaValidEmail(email));
    if (invalid.length) {
        toast(`Check custom email: ${invalid[0]}`);
        return null;
    }
    const seen = new Set();
    return [...picked, ...tokens].filter(email => {
        const key = email.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function _notifySlaAssetName(action) {
    const raw = action.asset_name || action.source_name || action.report_name || "Unknown asset";
    return shortNameFromPath(raw) || raw;
}

function _notifySlaBuildBody(action, reason, slaValue, slaUnit) {
    const assetName = _notifySlaAssetName(action);
    const issue = actionTypeLabel(action.type);
    const report = _notifySlaReportForAction(action);
    const source = _notifySlaSourceForAction(action);
    const slaText = `${slaValue} ${slaUnit}`;
    const lines = [
        "Hi,",
        "",
        "This is an SLA notification for an active alert.",
        "",
        `Asset: ${assetName}`,
        `Issue: ${issue}`,
        `Status: ${action.status || "open"}`,
        `Assigned owner: ${action.assigned_to || "Unassigned"}`,
        `Views last 30 days: ${fmtInt(action.impact_views_30d || 0)}`,
        `Degraded since: ${formatDateOnly(action.degraded_since || action.created_at)}`,
        "",
        `Reason: ${reason}`,
        `Estimated SLA: ${slaText}`,
    ];

    if (action.recommendation) {
        lines.push("", `Recommended next step: ${action.recommendation}`);
    }
    if (source && source.owner) {
        lines.push(`Source owner: ${source.owner}`);
    }
    if (report) {
        if (report.owner) lines.push(`Report owner: ${report.owner}`);
        if (report.business_owner) lines.push(`Business owner: ${report.business_owner}`);
    }
    if (action.detail_items && action.detail_items.length) {
        const details = action.detail_items.slice(0, 4).map(item => {
            const gap = item.delta_hours < 48 ? `${item.delta_hours} hours` : `${Math.floor(item.delta_hours / 24)} days`;
            return `- ${item.name}: ${gap} newer than the report`;
        });
        lines.push("", "Context:", ...details);
    }

    lines.push("", "Thanks,", "Metronome");
    return lines.join("\n");
}

function _notifySlaModalHtml(action) {
    const people = window._dashboardPeople || [];
    const profiles = people.filter(p => p.email && (p.role === "BI" || p.role === "Business"));
    const suggestions = _notifySlaRecipientSuggestions(action);
    const suggestedEmails = new Set([...suggestions.keys()]);
    const reasonOptions = _notifySlaIssueReasons(action.type);
    const defaultSla = _notifySlaDefaultSla(action.type);
    const assetName = _notifySlaAssetName(action);
    const issue = actionTypeLabel(action.type);
    const groupHtml = ["BI", "Business"].map(role => {
        const rolePeople = profiles.filter(p => p.role === role);
        const rows = rolePeople.length ? rolePeople.map(p => {
            const emailKey = p.email.toLowerCase();
            const suggestion = suggestions.get(emailKey);
            return `
                <label class="notify-sla-choice">
                    <input type="checkbox" class="notify-sla-recipient-check" data-email="${esc(p.email)}" ${suggestedEmails.has(emailKey) ? "checked" : ""}>
                    <span>
                        <strong>${esc(p.name)}</strong>
                        <small>${esc(p.email)}${suggestion ? ` - ${esc(suggestion.reason)}` : ""}</small>
                    </span>
                </label>
            `;
        }).join("") : '<div class="notify-sla-empty">No mapped emails</div>';
        return `
            <div class="notify-sla-recipient-group">
                <div class="notify-sla-group-title">${role}</div>
                ${rows}
            </div>
        `;
    }).join("");

    return `
        <div class="task-modal-overlay" id="notify-sla-overlay">
            <div class="task-modal notify-sla-modal" role="dialog" aria-modal="true" aria-labelledby="notify-sla-title">
                <h2 id="notify-sla-title">Notify SLA</h2>
                <div class="notify-sla-summary">
                    <strong>${esc(assetName)}</strong>
                    <span>${esc(issue)} - ${esc(action.status || "open")} - ${fmtInt(action.impact_views_30d || 0)} impact views</span>
                </div>

                <label>Recipients</label>
                <div class="notify-sla-recipient-grid">${groupHtml}</div>

                <label for="notify-sla-custom-emails">Custom emails</label>
                <input id="notify-sla-custom-emails" type="text" placeholder="name@example.com; team@example.com">

                <label for="notify-sla-reason-preset">Reason</label>
                <select id="notify-sla-reason-preset">
                    ${reasonOptions.map((reason, index) => `<option value="${esc(reason)}"${index === 0 ? " selected" : ""}>${esc(reason)}</option>`).join("")}
                    <option value="__custom__">Custom reason</option>
                </select>
                <textarea id="notify-sla-reason" rows="4">${esc(reasonOptions[0])}</textarea>

                <label>Estimated SLA</label>
                <div class="notify-sla-estimate">
                    <input id="notify-sla-value" type="number" min="1" step="1" value="${defaultSla.value}">
                    <select id="notify-sla-unit">
                        <option value="hours"${defaultSla.unit === "hours" ? " selected" : ""}>hours</option>
                        <option value="days"${defaultSla.unit === "days" ? " selected" : ""}>days</option>
                    </select>
                </div>

                <div class="task-modal-actions notify-sla-actions">
                    <button type="button" id="notify-sla-cancel">Cancel</button>
                    <button type="button" id="notify-sla-copy">Copy Email</button>
                    <button type="button" class="btn-primary" id="notify-sla-open">Open Draft</button>
                </div>
            </div>
        </div>
    `;
}

function _notifySlaReadEmail(action) {
    const recipients = _notifySlaCollectRecipients();
    if (!recipients) return null;
    if (recipients.length === 0) {
        toast("Choose at least one recipient or add a custom email");
        return null;
    }
    const reason = (document.getElementById("notify-sla-reason")?.value || "").trim();
    if (!reason) {
        toast("Add a reason before opening the draft");
        return null;
    }
    const slaValue = parseInt(document.getElementById("notify-sla-value")?.value || "", 10);
    if (!Number.isFinite(slaValue) || slaValue < 1) {
        toast("Enter an SLA of at least 1");
        return null;
    }
    const slaUnit = document.getElementById("notify-sla-unit")?.value || "hours";
    const subject = `SLA notice: ${actionTypeLabel(action.type)} - ${_notifySlaAssetName(action)}`;
    const body = _notifySlaBuildBody(action, reason, slaValue, slaUnit);
    return { recipients, subject, body };
}

function _notifySlaOpenDraft(action) {
    const email = _notifySlaReadEmail(action);
    if (!email) return;
    const to = email.recipients.map(recipient => encodeURIComponent(recipient)).join(",");
    window.location.href = `mailto:${to}?subject=${encodeURIComponent(email.subject)}&body=${encodeURIComponent(email.body)}`;
}

async function _notifySlaCopyEmail(action) {
    const email = _notifySlaReadEmail(action);
    if (!email) return;
    try {
        await navigator.clipboard.writeText(`To: ${email.recipients.join("; ")}\nSubject: ${email.subject}\n\n${email.body}`);
        toast("Email copied");
    } catch (_) {
        toast("Clipboard unavailable");
    }
}

function _openNotifySlaModal(actionId) {
    const action = (window._dashboardActions || []).find(a => String(a.id) === String(actionId));
    if (!action) {
        toast("Alert not found");
        return;
    }
    document.getElementById("notify-sla-overlay")?.remove();
    document.body.insertAdjacentHTML("beforeend", _notifySlaModalHtml(action));
    const overlay = document.getElementById("notify-sla-overlay");
    const close = () => overlay?.remove();
    overlay?.addEventListener("click", e => {
        if (e.target === overlay) close();
    });
    document.getElementById("notify-sla-cancel")?.addEventListener("click", close);
    document.getElementById("notify-sla-open")?.addEventListener("click", () => _notifySlaOpenDraft(action));
    document.getElementById("notify-sla-copy")?.addEventListener("click", () => _notifySlaCopyEmail(action));
    const preset = document.getElementById("notify-sla-reason-preset");
    const reason = document.getElementById("notify-sla-reason");
    preset?.addEventListener("change", () => {
        if (!reason || preset.value === "__custom__") return;
        reason.value = preset.value;
    });
    reason?.focus();
}

function typeBadge(type) {
    const colors = {
        csv: "badge-blue", excel: "badge-green", sql: "badge-yellow",
        postgresql: "badge-yellow", mysql: "badge-yellow", oracle: "badge-yellow",
        odbc: "badge-yellow", oledb: "badge-yellow", ssas: "badge-yellow",
        redshift: "badge-yellow", snowflake: "badge-yellow", bigquery: "badge-yellow",
        sharepoint: "badge-blue", web: "badge-blue", folder: "badge-blue",
    };
    return `<span class="badge badge-type ${colors[type] || "badge-muted"}">${type}</span>`;
}

function daysOld(dateStr) {
    if (!dateStr) return null;
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return null;
    return Math.floor((Date.now() - d.getTime()) / 86400000);
}

function timeAgo(dateStr) {
    if (!dateStr) return "-";
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) {
        return "-";
    }
    if (d.getFullYear() < 2000) return "-";
    const now = new Date();
    const diffMs = now - d;
    const mins = Math.floor(diffMs / 60000);
    if (mins < 0) return "in the future";
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `${days}d ago`;
    const months = Math.floor(days / 30);
    if (months < 12) return `${months}mo ago`;
    return `${Math.floor(months / 12)}y ago`;
}

const FRESHNESS_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function sourceFreshnessRuleType(source) {
    if (source.freshness_rule_type) return source.freshness_rule_type;
    if (source.custom_fresh_days != null && source.custom_fresh_days > 0) return "custom";
    return "";
}

function sourceHasFreshnessRule(source) {
    return !!sourceFreshnessRuleType(source);
}

function sourceFreshnessDays(source) {
    if (!source.freshness_schedule_days) return [];
    return source.freshness_schedule_days.split(",").map(d => d.trim()).filter(Boolean);
}

function freshnessRuleLabel(source) {
    const type = sourceFreshnessRuleType(source);
    if (type === "daily") return "Daily";
    if (type === "fixed") {
        const days = sourceFreshnessDays(source).map(d => d.slice(0, 3)).join(", ");
        return days ? `Fixed: ${days}` : "Fixed";
    }
    if (type === "custom" && source.custom_fresh_days) return `Custom: ${source.custom_fresh_days}d`;
    return "-";
}

function _freshnessRuleFormHtml(source, opts = {}) {
    const prefix = opts.prefix || "freshness";
    const freshnessType = sourceFreshnessRuleType(source);
    const hasFreshnessRule = sourceHasFreshnessRule(source);
    const freshVal = source.custom_fresh_days != null && source.custom_fresh_days > 0 ? source.custom_fresh_days : "";
    const selectedFreshDays = new Set(sourceFreshnessDays(source));
    const dayBoxes = FRESHNESS_DAYS.map(day => `
        <label class="freshness-day-pill">
            <input type="checkbox" value="${day}" ${selectedFreshDays.has(day) ? "checked" : ""}>
            <span>${day.slice(0, 3)}</span>
        </label>
    `).join("");
    const wideClass = opts.wide === false ? "" : " freshness-rule-form-wide";

    return `
        <div class="freshness-rule-form${wideClass}" data-freshness-prefix="${esc(prefix)}">
            <label class="freshness-label">Rule
                <select id="${esc(prefix)}-rule-type" class="freshness-select">
                    <option value="" ${!freshnessType ? "selected" : ""}>No rule</option>
                    <option value="daily" ${freshnessType === "daily" ? "selected" : ""}>Daily</option>
                    <option value="custom" ${freshnessType === "custom" ? "selected" : ""}>Custom days</option>
                    <option value="fixed" ${freshnessType === "fixed" ? "selected" : ""}>Fixed schedule</option>
                </select>
            </label>
            <label class="freshness-label freshness-custom-options">Healthy up to
                <input type="number" id="${esc(prefix)}-fresh-days-input" value="${freshVal}" placeholder="days" min="1" max="9999" class="input-sm">
                days
            </label>
            <div class="freshness-fixed-options">
                <span class="freshness-label">Refresh days</span>
                <div class="freshness-day-list">${dayBoxes}</div>
                <span class="freshness-help">Choose the expected refresh days</span>
            </div>
            <button class="btn-sm btn-blue" id="${esc(prefix)}-save-freshness">Save</button>
            ${hasFreshnessRule ? `<button class="btn-sm btn-outline" id="${esc(prefix)}-reset-freshness">Clear rule</button>` : ""}
            ${hasFreshnessRule
                ? `<span class="badge badge-blue" style="font-size:0.72rem">${esc(freshnessRuleLabel(source))}</span>`
                : '<span style="color:var(--text-dim);font-size:0.75rem">No rule set - freshness not monitored for this source</span>'}
        </div>
    `;
}

function _bindFreshnessRuleForm(panel, source, opts = {}) {
    const prefix = opts.prefix || "freshness";
    const ruleTypeSelect = panel.querySelector(`#${prefix}-rule-type`);
    const afterChange = typeof opts.afterChange === "function" ? opts.afterChange : null;
    const syncFreshnessOptions = () => {
        const type = ruleTypeSelect ? ruleTypeSelect.value : "";
        panel.querySelectorAll(".freshness-custom-options").forEach(el => {
            el.style.display = type === "custom" ? "flex" : "none";
        });
        panel.querySelectorAll(".freshness-fixed-options").forEach(el => {
            el.style.display = type === "fixed" ? "flex" : "none";
        });
    };
    if (ruleTypeSelect) {
        ruleTypeSelect.addEventListener("change", syncFreshnessOptions);
        syncFreshnessOptions();
    }
    const saveFreshBtn = panel.querySelector(`#${prefix}-save-freshness`);
    if (saveFreshBtn) {
        saveFreshBtn.addEventListener("click", async () => {
            const ruleType = ruleTypeSelect ? ruleTypeSelect.value : "";
            if (!ruleType) {
                try {
                    await apiDelete(`/api/sources/${source.id}/freshness-rule`);
                    toast("Rule cleared - source not monitored");
                    if (afterChange) await afterChange();
                } catch (err) {
                    toast("Failed: " + err.message);
                }
                return;
            }
            const payload = { rule_type: ruleType };
            if (ruleType === "custom") {
                const raw = panel.querySelector(`#${prefix}-fresh-days-input`).value.trim();
                const fd = parseInt(raw, 10);
                if (isNaN(fd) || fd < 1) {
                    toast("Enter at least 1 freshness day");
                    return;
                }
                payload.fresh_days = fd;
            }
            if (ruleType === "fixed") {
                const days = [...panel.querySelectorAll(".freshness-day-pill input:checked")].map(el => el.value);
                if (days.length === 0) {
                    toast("Choose at least one refresh day");
                    return;
                }
                payload.refresh_days = days;
            }
            try {
                await apiPut(`/api/sources/${source.id}/freshness-rule`, payload);
                toast("Freshness rule saved - re-probe to apply");
                if (afterChange) await afterChange();
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    }

    const resetFreshBtn = panel.querySelector(`#${prefix}-reset-freshness`);
    if (resetFreshBtn) {
        resetFreshBtn.addEventListener("click", async () => {
            try {
                await apiDelete(`/api/sources/${source.id}/freshness-rule`);
                toast("Freshness rule cleared - source not monitored");
                if (afterChange) {
                    await afterChange();
                    return;
                }
                if (ruleTypeSelect) ruleTypeSelect.value = "";
                const freshInput = panel.querySelector(`#${prefix}-fresh-days-input`);
                if (freshInput) freshInput.value = "";
                panel.querySelectorAll(".freshness-day-pill input").forEach(box => { box.checked = false; });
                syncFreshnessOptions();
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    }
}

function formatDate(dateStr) {
    if (!dateStr) return "-";
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
         + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function formatDateOnly(dateStr) {
    if (!dateStr) return "-";
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function formatCompactTimestamp(dateStr) {
    if (!dateStr) return "-";
    const raw = String(dateStr).trim();
    const exact = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (exact) return `${exact[1]}-${exact[2]}-${exact[3]}-${exact[4]}-${exact[5]}`;
    const d = new Date(raw);
    if (isNaN(d.getTime())) return raw;
    const pad = value => String(value).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-${pad(d.getHours())}-${pad(d.getMinutes())}`;
}

function exportTableCSV(tableId, filename) {
    const dt = window._dt && window._dt[tableId];
    if (!dt) return;
    const rows = dt._displayRows || dt.rows;
    const cols = dt.columns;
    const header = cols.map(c => c.label).join(",");
    const body = rows.map(r =>
        cols.map(c => {
            let val = r[c.key] ?? "";
            val = String(val).replace(/"/g, '""');
            return `"${val}"`;
        }).join(",")
    ).join("\n");
    const csv = header + "\n" + body;
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || (tableId + ".csv");
    a.click();
    URL.revokeObjectURL(a.href);
}

function toast(msg) {
    const el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

function pct(n, total) {
    if (!total) return 0;
    return Math.round((n / total) * 100);
}


// ── Source name parsing ──

const DB_TYPES = new Set(["sql", "postgresql", "mysql", "oracle", "odbc", "oledb", "ssas", "redshift", "snowflake", "bigquery"]);

function parseSourceName(s) {
    const name = s.name || "";
    const type = s.type || "";

    if (DB_TYPES.has(type)) {
        const lastSlash = name.lastIndexOf("/");
        const afterSlash = lastSlash >= 0 ? name.substring(lastSlash + 1) : name;
        const strip = v => v.replace(/\s*\([^)]*\)\s*$/, "").trim();
        const dotIdx = afterSlash.indexOf(".");
        if (dotIdx >= 0) {
            return {
                shortName: strip(afterSlash.substring(dotIdx + 1)),
                folderSchema: strip(afterSlash.substring(0, dotIdx)),
                fullLocation: name
            };
        }
        return { shortName: strip(afterSlash), folderSchema: "-", fullLocation: name };
    }

    const fileName = name.includes("/") ? name.substring(name.lastIndexOf("/") + 1)
                   : name.includes("\\") ? name.substring(name.lastIndexOf("\\") + 1)
                   : name;
    const dotIdx = fileName.lastIndexOf(".");
    const shortName = dotIdx > 0 ? fileName.substring(0, dotIdx) : fileName;
    const connInfo = s.connection_info || name;
    const lastSep = Math.max(connInfo.lastIndexOf("/"), connInfo.lastIndexOf("\\"));
    const folder = lastSep >= 0 ? connInfo.substring(0, lastSep) : "-";

    return { shortName, folderSchema: folder, fullLocation: connInfo || name };
}


// ── DataTable ──

// Standard column widths by type
const COL_W = { xs: 50, sm: 75, md: 110, lg: 170, xl: 300 };

function _saveDTState(tableId) {
    const dt = window._dt[tableId];
    if (!dt) return;
    const state = { filters: dt.filters, sortCol: dt.sortCol, sortDir: dt.sortDir };
    try { sessionStorage.setItem("dt_state_" + tableId, JSON.stringify(state)); } catch (_) {}
}

function _loadDTState(tableId) {
    try {
        const raw = sessionStorage.getItem("dt_state_" + tableId);
        return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
}

function dataTable(tableId, columns, rows, opts) {
    window._dt = window._dt || {};
    const saved = _loadDTState(tableId);
    window._dt[tableId] = {
        columns, rows,
        sortCol: saved ? saved.sortCol : null,
        sortDir: saved ? saved.sortDir : "asc",
        filters: saved ? saved.filters : {},
        opts: opts || {},
    };
    return _renderDT(tableId);
}

function _isInteractiveTableTarget(target) {
    return !!target.closest("button, a, input, select, textarea, label, [role='button']");
}

function _filterAndSortDT(dt) {
    const { columns, sortCol, sortDir, filters } = dt;
    let rows = dt.rows.filter(r => {
        for (const col of columns) {
            if (col.filterable === false) continue;
            const f = (filters[col.key] || "").toLowerCase();
            if (!f) continue;
            const rawVal = col.filterVal ? col.filterVal(r) : col.sortVal ? col.sortVal(r) : (r[col.key] ?? "");
            const val = String(rawVal).toLowerCase();
            if (f.includes("|")) {
                if (!f.split("|").some(p => val.includes(p))) return false;
            } else {
                if (!val.includes(f)) return false;
            }
        }
        return true;
    });

    if (sortCol) {
        const col = columns.find(c => c.key === sortCol);
        if (col && col.sortable !== false) {
            rows = [...rows].sort((a, b) => {
                let va = col.sortVal ? col.sortVal(a) : (a[sortCol] ?? "");
                let vb = col.sortVal ? col.sortVal(b) : (b[sortCol] ?? "");
                if (typeof va === "string") va = va.toLowerCase();
                if (typeof vb === "string") vb = vb.toLowerCase();
                if (va < vb) return sortDir === "asc" ? -1 : 1;
                if (va > vb) return sortDir === "asc" ? 1 : -1;
                return 0;
            });
        }
    }
    return rows;
}

function _colStyle(c) {
    const w = c.width || 0;
    if (!w) return "";
    return ` style="min-width:${w}px;width:${w}px"`;
}

function _renderDT(tableId) {
    const dt = window._dt[tableId];
    const { columns, sortCol, sortDir, filters } = dt;
    let rows = _filterAndSortDT(dt);

    const arrow = (key) => {
        if (sortCol !== key) return '<span class="sort-arrow">&#9650;</span>';
        return sortDir === "asc"
            ? '<span class="sort-arrow">&#9650;</span>'
            : '<span class="sort-arrow">&#9660;</span>';
    };

    const headerCells = columns.map(c => {
        const isSortable = c.sortable !== false;
        const sortCls = isSortable ? 'sortable' : '';
        const activeCls = isSortable && sortCol === c.key ? ' sort-' + sortDir : '';
        const sortArrow = isSortable ? arrow(c.key) : '';
        return `<th class="resizable ${sortCls}${activeCls}" data-dt="${tableId}" data-col="${c.key}"${_colStyle(c)}>${c.label}${sortArrow}<div class="col-resizer"></div></th>`;
    }).join("");

    const filterCells = columns.map(c => {
        if (c.filterable === false) return '<th></th>';
        const ph = c.filterPlaceholder || "Filter...";
        return `<th><input type="text" data-dt="${tableId}" data-fcol="${c.key}" placeholder="${ph}" value="${filters[c.key] || ""}"></th>`;
    }).join("");

    const clickable = dt.opts && dt.opts.onRowClick ? ' data-clickable="1"' : '';
    const bodyRows = rows.map((r, i) => {
        const archivedCls = r.archived ? ' class="row-archived"' : '';
        return `<tr data-dt="${tableId}" data-row-idx="${i}"${clickable}${archivedCls}>${columns.map(c => `<td>${c.render ? c.render(r) : (r[c.key] ?? "-")}</td>`).join("")}</tr>`;
    }).join("");

    dt._displayRows = rows;

    return `
        <div class="table-wrapper">
            <table id="${tableId}">
                <thead>
                    <tr>${headerCells}</tr>
                    <tr class="filter-row">${filterCells}</tr>
                </thead>
                <tbody>${bodyRows || '<tr><td colspan="' + columns.length + '" style="text-align:center;color:var(--text-dim);padding:2rem">No data</td></tr>'}</tbody>
            </table>
        </div>
        <div class="table-count">${rows.length} of ${dt.rows.length} rows</div>
    `;
}

function bindDataTables() {
    document.querySelectorAll("th.sortable[data-dt]").forEach(th => {
        th.addEventListener("click", (e) => {
            if (e.target.classList.contains("col-resizer")) return;
            const id = th.dataset.dt;
            const col = th.dataset.col;
            const dt = window._dt[id];
            if (dt.sortCol === col) {
                dt.sortDir = dt.sortDir === "asc" ? "desc" : "asc";
            } else {
                dt.sortCol = col;
                dt.sortDir = "asc";
            }
            _saveDTState(id);
            _refreshDT(id);
        });
    });

    document.querySelectorAll("tr.filter-row input[data-dt]").forEach(inp => {
        inp.addEventListener("input", () => {
            const id = inp.dataset.dt;
            const col = inp.dataset.fcol;
            window._dt[id].filters[col] = inp.value;
            _saveDTState(id);
            _refreshDT(id);
        });
    });

    document.querySelectorAll("tr[data-clickable]").forEach(tr => {
        tr.addEventListener("click", (e) => {
            if (_isInteractiveTableTarget(e.target)) return;
            const id = tr.dataset.dt;
            const idx = parseInt(tr.dataset.rowIdx);
            const dt = window._dt[id];
            if (dt.opts && dt.opts.onRowClick && dt._displayRows) {
                dt.opts.onRowClick(dt._displayRows[idx]);
            }
        });
    });

    document.querySelectorAll("td .cell-expandable").forEach(el => {
        el.addEventListener("dblclick", (e) => {
            e.stopPropagation();
            el.classList.toggle("expanded");
        });
    });

    // View path buttons inside data tables
    document.querySelectorAll(".view-path-btn").forEach(btn => {
        if (btn._viewBound) return;
        btn._viewBound = true;
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            try { await apiPostJson("/api/scanner/open-path", { path: btn.dataset.path }); }
            catch { toast("Could not open path (only works on server machine)"); }
        });
    });

    _bindColumnResizers();
}

function _bindColumnResizers() {
    document.querySelectorAll(".col-resizer").forEach(resizer => {
        resizer.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const th = resizer.parentElement;
            const table = th.closest("table");
            const colIdx = Array.from(th.parentElement.children).indexOf(th);
            const startX = e.pageX;
            const startWidth = th.offsetWidth;

            resizer.classList.add("dragging");

            function onMouseMove(e) {
                const newWidth = Math.max(40, startWidth + (e.pageX - startX));
                th.style.width = newWidth + "px";
                th.style.minWidth = newWidth + "px";
                const filterTh = table.querySelector("tr.filter-row")?.children[colIdx];
                if (filterTh) {
                    filterTh.style.width = newWidth + "px";
                    filterTh.style.minWidth = newWidth + "px";
                }
                table.querySelectorAll("tbody tr").forEach(row => {
                    const cell = row.children[colIdx];
                    if (cell) {
                        const expandable = cell.querySelector(".cell-expandable");
                        if (expandable) expandable.style.maxWidth = (newWidth - 20) + "px";
                    }
                });
            }

            function onMouseUp() {
                resizer.classList.remove("dragging");
                document.removeEventListener("mousemove", onMouseMove);
                document.removeEventListener("mouseup", onMouseUp);
            }

            document.addEventListener("mousemove", onMouseMove);
            document.addEventListener("mouseup", onMouseUp);
        });
    });
}

function detectTableScroll() {
    document.querySelectorAll(".table-wrapper").forEach(wrapper => {
        const hasScroll = wrapper.scrollWidth > wrapper.clientWidth;
        wrapper.classList.toggle("has-scroll", hasScroll);
        wrapper.addEventListener("scroll", () => {
            const atEnd = wrapper.scrollLeft + wrapper.clientWidth >= wrapper.scrollWidth - 2;
            wrapper.classList.toggle("has-scroll", !atEnd && wrapper.scrollWidth > wrapper.clientWidth);
        });
    });
}

function _refreshDT(tableId) {
    const dt = window._dt[tableId];
    const table = document.getElementById(tableId);
    if (!table) return;

    const { columns, sortCol, sortDir } = dt;
    let rows = _filterAndSortDT(dt);
    dt._displayRows = rows;

    const clickable = dt.opts && dt.opts.onRowClick ? ' data-clickable="1"' : '';
    const bodyHTML = rows.map((r, i) =>
        `<tr data-dt="${tableId}" data-row-idx="${i}"${clickable}>${columns.map(c => `<td>${c.render ? c.render(r) : (r[c.key] ?? "-")}</td>`).join("")}</tr>`
    ).join("") || `<tr><td colspan="${columns.length}" style="text-align:center;color:var(--text-dim);padding:2rem">No data</td></tr>`;

    const tbody = table.querySelector("tbody");
    if (tbody) tbody.innerHTML = bodyHTML;

    // Update sort arrows in header
    table.querySelectorAll("thead tr:first-child th[data-dt]").forEach(th => {
        const col = th.dataset.col;
        const cDef = columns.find(c => c.key === col);
        const isSortable = cDef && cDef.sortable !== false;
        th.className = `resizable${isSortable ? ' sortable' : ''}${isSortable && sortCol === col ? ' sort-' + sortDir : ''}`;
        const arrow = th.querySelector(".sort-arrow");
        if (arrow) arrow.innerHTML = sortCol === col && sortDir === "desc" ? "&#9660;" : "&#9650;";
    });

    // Update count
    const wrapper = table.closest(".table-wrapper");
    if (wrapper) {
        const countDiv = wrapper.nextElementSibling;
        if (countDiv && countDiv.classList.contains("table-count")) {
            countDiv.textContent = `${rows.length} of ${dt.rows.length} rows`;
        }
    }

    // Re-bind only tbody row clicks and expandable cells (not filter inputs or sort headers)
    table.querySelectorAll("tr[data-clickable]").forEach(tr => {
        tr.addEventListener("click", (e) => {
            if (_isInteractiveTableTarget(e.target)) return;
            const idx = parseInt(tr.dataset.rowIdx);
            if (dt.opts && dt.opts.onRowClick && dt._displayRows) {
                dt.opts.onRowClick(dt._displayRows[idx]);
            }
        });
    });
    table.querySelectorAll("td .cell-expandable").forEach(el => {
        el.addEventListener("dblclick", (e) => {
            e.stopPropagation();
            el.classList.toggle("expanded");
        });
    });
}


// ── Source detail panel ──

function _viewPathBtn(filePath) {
    if (!filePath) return "";
    return `<button class="btn-xs btn-outline view-path-btn" data-path="${esc(filePath)}" title="Open containing folder">View</button>`;
}

async function showSourceDetail(source) {
    const existing = $("#source-detail");
    if (existing) existing.remove();

    const [reports, scripts] = await Promise.all([
        api(`/api/sources/${source.id}/reports`),
        api(`/api/sources/${source.id}/scripts`),
    ]);
    const parsed = parseSourceName(source);

    const panel = document.createElement("div");
    panel.id = "source-detail";
    panel.className = "source-detail-panel";

    const reportRows = reports.length > 0
        ? reports.map(r => `
            <tr>
                <td><strong>${esc(r.name)}</strong></td>
                <td style="color:var(--text-muted)">${esc(r.table_name) || "-"}</td>
                <td style="color:var(--text-muted)">${esc(r.owner) || "-"}</td>
            </tr>
        `).join("")
        : '<tr><td colspan="3" class="empty-state" style="border:none">No reports linked to this source</td></tr>';

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(parsed.shortName)}</h2>
            <button class="btn-outline" id="btn-close-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Type</div>${typeBadge(source.type)}</div>
            <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(source.status)}</div>
            <div class="detail-item"><div class="detail-label">Last Updated</div><span style="color:var(--text)">${source.last_updated ? esc(formatDate(source.last_updated)) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Rows</div>${rowCountHtml(source.row_count)}</div>
            <div class="detail-item"><div class="detail-label">Schema</div><span style="color:var(--text)">${esc(parsed.folderSchema)}</span></div>
            <div class="detail-item"><div class="detail-label">Full Location</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${esc(parsed.fullLocation)} ${_viewPathBtn(source.connection_info || parsed.fullLocation)}</span></div>
            <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${esc(source.owner) || "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Upstream System</div><span style="color:var(--text)">${esc(source.upstream_name) || "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Upstream Refresh</div><span style="color:var(--text)">${esc(source.upstream_refresh_day) || "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Source Refresh</div><span style="color:var(--text)">${source.refresh_schedule ? 'Weekly - ' + esc(source.refresh_schedule) : "-"}</span></div>
        </div>

        <h2>Reports using this source (${reports.length})</h2>
        <table class="detail-table">
            <thead><tr><th>Report</th><th>Table Name</th><th>Owner</th></tr></thead>
            <tbody>${reportRows}</tbody>
        </table>

        <h2>Scripts linked to this source (${scripts.length})</h2>
        <table class="detail-table">
            <thead><tr><th>Script</th><th>Direction</th><th>Table/File</th><th>Path</th></tr></thead>
            <tbody>${scripts.length > 0
                ? scripts.map(sc => `
                    <tr>
                        <td><strong>${esc(sc.display_name)}</strong></td>
                        <td><span class="badge ${sc.direction === 'write' ? 'badge-orange' : 'badge-blue'}" style="font-size:0.7rem">${esc(sc.direction)}</span></td>
                        <td style="color:var(--text-muted);font-size:0.78rem">${esc(sc.table_name) || "-"}</td>
                        <td style="font-size:0.75rem;word-break:break-all;color:var(--text-dim)">${esc(sc.path)} ${_viewPathBtn(sc.path)}</td>
                    </tr>
                `).join("")
                : '<tr><td colspan="4" class="empty-state" style="border:none">No scripts linked to this source</td></tr>'
            }</tbody>
        </table>

        <h2>Freshness Rule</h2>
        ${_freshnessRuleFormHtml(source, { prefix: "source-freshness", wide: true })}
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#btn-close-detail").addEventListener("click", () => panel.remove());

    // View path buttons
    panel.querySelectorAll(".view-path-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const p = btn.dataset.path;
            try {
                await apiPostJson("/api/scanner/open-path", { path: p });
            } catch (err) {
                toast("Could not open path (only works on server machine)");
            }
        });
    });

    _bindFreshnessRuleForm(panel, source, { prefix: "source-freshness" });
}


// ── Report detail panel ──

async function showReportDetail(report) {
    // Toggle: if already expanded for this report, collapse it
    const existing = document.querySelector(`.report-expand-row[data-report-id="${report.id}"]`);
    if (existing) { existing.remove(); return; }

    // Collapse any other expanded report
    document.querySelectorAll(".report-expand-row").forEach(r => r.remove());

    // Find the clicked row in the table
    const allRows = document.querySelectorAll("#dt-reports tbody tr[data-clickable]");
    let targetRow = null;
    allRows.forEach(tr => {
        const idx = parseInt(tr.dataset.rowIdx);
        const dt = window._dt["dt-reports"];
        if (dt && dt._displayRows && dt._displayRows[idx] && dt._displayRows[idx].id === report.id) {
            targetRow = tr;
        }
    });

    const [tables, unusedData, docData] = await Promise.all([
        api(`/api/reports/${report.id}/tables`),
        api(`/api/reports/${report.id}/unused`).catch(() => ({ total_measures: 0, total_columns: 0, total_fields: 0, unused_measures: [], unused_columns: [], unused_tables: [], unused_fields_count: 0, unused_pct: 0, total_tables: 0, unused_tables_count: 0 })),
        api(`/api/documentation?report_id=${report.id}`).catch(() => []),
    ]);
    let doc = docData.length > 0 ? docData[0] : null;

    const allSources = window._reportPageSources || [];
    const sourceMap = new Map();
    allSources.forEach(s => sourceMap.set(s.id, s));

    const colCount = targetRow ? targetRow.children.length : 8;
    const expandRow = document.createElement("tr");
    expandRow.className = "report-expand-row";
    expandRow.dataset.reportId = report.id;

    // ── Data Sources: group by type, each group collapsible ──
    const typeGroups = {};
    tables.forEach(t => {
        const src = t.source_id ? sourceMap.get(t.source_id) : null;
        const type = src ? (src.type || "Unknown") : "Unlinked";
        if (!typeGroups[type]) typeGroups[type] = [];
        typeGroups[type].push({ table: t, source: src });
    });
    const typeSummary = Object.entries(typeGroups)
        .map(([type, items]) => `${type} (${items.length})`)
        .join(", ");

    let dsGroupIdx = 0;
    const groupedSourcesHtml = Object.entries(typeGroups).map(([type, items]) => {
        const gid = `ds-group-${dsGroupIdx++}`;
        return `
        <div class="rx-section rx-l2">
            <div class="rx-toggle" data-target="${gid}">
                <span class="rx-arrow">&#9656;</span> ${esc(type)} <span class="rx-count">(${items.length})</span>
            </div>
            <div class="rx-body" id="${gid}" style="display:none">
                ${items.map(({ table: t, source: src }) => {
                    const srcName = src ? (shortNameFromPath(src.name) || src.name) : (t.source_name || "no linked source");
                    return `<div class="report-source-item rx-l3${src ? ' report-source-clickable' : ''}" ${src ? `data-source-id="${src.id}"` : ''}>
                        <span class="report-source-table">${t.table_name}</span>
                        <span class="report-source-arrow">&rarr;</span>
                        <span class="report-source-name">${srcName}</span>
                        ${src && src.last_updated ? `<span style="color:var(--text-dim);font-size:0.72rem;margin-left:auto">${timeAgo(src.last_updated)}</span>` : ''}
                    </div>`;
                }).join("")}
            </div>
        </div>`;
    }).join("");

    // ── Documentation: each field collapsible ──
    const docSections = [];
    const _ds = (label, id, content, badge) => {
        if (!content) return;
        docSections.push({ label, id, content, badge });
    };
    _ds("Purpose", "doc-s-purpose", doc?.business_purpose ? renderMd(doc.business_purpose) : null);
    _ds("Audience", "doc-s-audience", doc?.business_audience ? renderMd(doc.business_audience) : null);
    _ds("Key Formulas", "doc-s-formulas", doc?.technical_transformations ? `<div style="white-space:pre-wrap;font-size:0.8rem">${esc(doc.technical_transformations)}</div>` : null);

    const docFieldsHtml = docSections.length > 0
        ? docSections.map(s => `
            <div class="rx-section rx-l2">
                <div class="rx-toggle" data-target="${s.id}">
                    <span class="rx-arrow">&#9656;</span> ${s.label} <span class="badge badge-green" style="font-size:0.58rem;margin-left:0.3rem">filled</span>
                </div>
                <div class="rx-body" id="${s.id}" style="display:none">
                    <div class="rx-l3 doc-text-block">${s.content}</div>
                </div>
            </div>`).join("")
        : '<div class="rx-l2" style="color:var(--text-dim);font-size:0.78rem;padding:0.3rem 0">No documentation yet.</div>';

    // Count unfilled for context
    const allDocLabels = ["Purpose", "Audience", "Key Formulas"];
    const filledCount = docSections.length;
    const unfilledCount = allDocLabels.length - filledCount;

    // Doc %
    const docPct = Math.round((filledCount / 3) * 100);
    const docPctCls = docPct === 100 ? 'badge-green' : docPct >= 50 ? 'badge-yellow' : 'badge-muted';

    // Known Issues (separate from documentation)
    const hasKnownIssues = doc?.technical_known_issues && doc.technical_known_issues.trim();

    const docEditHtml = `
        <div class="doc-inline-edit">
            <label>Purpose - Why does this report exist?</label><textarea id="doc-e-purpose" rows="2">${esc(doc?.business_purpose || "")}</textarea>
            <label>Audience - Who uses it and how?</label><textarea id="doc-e-audience" rows="2">${esc(doc?.business_audience || "")}</textarea>
            <label>Key Formulas - Plain English, not DAX</label><textarea id="doc-e-transforms" rows="4">${esc(doc?.technical_transformations || "")}</textarea>
            <div style="display:flex;gap:0.5rem;margin-top:0.5rem">
                <button class="btn-outline" id="doc-e-save">Save</button>
                <button class="btn-outline" id="doc-e-cancel">Cancel</button>
            </div>
        </div>
    `;

    // ── Optimization ──
    const unusedMC = unusedData.unused_measures.length + unusedData.unused_columns.length;
    const hasUnusedData = unusedData.total_fields > 0 || unusedData.total_tables > 0;
    const optimizationInner = hasUnusedData ? `
        <div class="rx-section rx-l2">
            <div class="rx-toggle" data-target="unused-mc">
                <span class="rx-arrow">&#9656;</span> Unused Measures / Columns <span class="rx-count">(${unusedMC} of ${unusedData.total_fields})</span>
                ${unusedMC > 0
                    ? `<span class="badge badge-yellow" style="margin-left:0.35rem;font-size:0.58rem">${unusedData.unused_pct}%</span>`
                    : `<span class="badge badge-green" style="margin-left:0.35rem;font-size:0.58rem">all used</span>`}
            </div>
            <div class="rx-body" id="unused-mc" style="display:none">
                ${unusedData.unused_measures.length > 0 ? unusedData.unused_measures.map(m => `
                    <div class="unused-measure-item rx-l3">
                        <span class="unused-measure-name">${m.name}</span>
                        <span class="unused-measure-table">${m.table_name}</span>
                        ${m.dax ? `<span class="unused-measure-dax" style="display:none">${m.dax.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>` : ''}
                    </div>`).join('') : ''}
                ${unusedData.unused_columns.length > 0 ? unusedData.unused_columns.map(c => `
                    <div class="unused-measure-item rx-l3">
                        <span class="unused-measure-name">${c.name}</span>
                        <span class="unused-measure-table">${c.table_name}</span>
                    </div>`).join('') : ''}
            </div>
        </div>
        <div class="rx-section rx-l2">
            <div class="rx-toggle" data-target="unused-tables">
                <span class="rx-arrow">&#9656;</span> Unused Tables <span class="rx-count">(${unusedData.unused_tables_count} of ${unusedData.total_tables})</span>
            </div>
            <div class="rx-body" id="unused-tables" style="display:none">
                ${unusedData.unused_tables.length > 0
                    ? unusedData.unused_tables.map(t => `<div class="unused-measure-item rx-l3"><span class="unused-measure-name">${t}</span></div>`).join('')
                    : '<div class="rx-l3" style="color:var(--green);font-size:0.78rem">All tables referenced</div>'}
            </div>
        </div>
    ` : '<div class="rx-l2" style="color:var(--text-dim);font-size:0.78rem">No scan data</div>';

    // ── Assemble ──
    expandRow.innerHTML = `<td colspan="${colCount}" class="report-expand-cell">
        <div class="report-expand-content">
            <div style="display:flex;gap:0.5rem;margin-bottom:0.5rem">
                ${report.powerbi_url ? `<a href="${esc(report.powerbi_url)}" target="_blank" rel="noopener" class="btn-outline" style="font-size:0.72rem;text-decoration:none" onclick="event.stopPropagation()">View in Power BI</a>` : ''}
                <button class="btn-outline btn-lineage-nav" data-report-id="${report.id}" style="font-size:0.72rem">View Lineage</button>
            </div>
            ${report.pbi_refresh_error ? `<div class="alerts-refresh-error"><strong>PBI Refresh Error:</strong> ${esc(report.pbi_refresh_error)}</div>` : ''}
            <div class="rx-section rx-l1">
                <div class="rx-toggle" data-target="ds-list">
                    <span class="rx-arrow">&#9656;</span> Data Sources <span class="rx-count">(${tables.length})</span>
                    <span style="font-size:0.72rem;color:var(--text-dim);font-weight:400;margin-left:0.5rem">${typeSummary}</span>
                </div>
                <div class="rx-body" id="ds-list" style="display:none">${groupedSourcesHtml}</div>
            </div>

            <div class="rx-section rx-l1">
                <div class="rx-toggle" data-target="doc-section">
                    <span class="rx-arrow">&#9656;</span> Documentation <span class="badge ${docPctCls}" style="margin-left:0.35rem;font-size:0.58rem">${docPct}%</span>
                    ${unfilledCount > 0 ? `<span style="font-size:0.72rem;color:var(--text-dim);font-weight:400;margin-left:0.5rem">${unfilledCount} unfilled</span>` : ''}
                </div>
                <div class="rx-body" id="doc-section" style="display:none">
                    <div id="doc-view-area">
                        ${docFieldsHtml}
                        <div class="rx-l2" style="display:flex;gap:0.5rem;margin-top:0.5rem;padding:0.25rem 0">
                            <button class="btn-outline" id="btn-doc-inline-edit" style="font-size:0.72rem">Edit</button>
                            <button class="btn-outline" id="btn-doc-inline-suggest" style="font-size:0.72rem" title="Pre-fill with Python extraction">Auto-fill</button>
                            <button class="btn-outline" id="btn-doc-ai-suggest" style="font-size:0.72rem" title="Generate with AI">AI Suggest</button>
                        </div>
                    </div>
                    <div id="doc-edit-area" style="display:none">${docEditHtml}</div>
                </div>
            </div>

            <div class="rx-section rx-l1">
                <div class="rx-toggle" data-target="known-issues-section">
                    <span class="rx-arrow">&#9656;</span> Known Issues
                    ${hasKnownIssues
                        ? '<span class="badge badge-yellow" style="margin-left:0.35rem;font-size:0.58rem">flagged</span>'
                        : '<span style="font-size:0.72rem;color:var(--text-dim);font-weight:400;margin-left:0.5rem">none</span>'}
                </div>
                <div class="rx-body" id="known-issues-section" style="display:none">
                    ${hasKnownIssues
                        ? `<div class="rx-l2 doc-text-block">${renderMd(doc.technical_known_issues)}</div>`
                        : ''}
                    <div class="rx-l2" style="padding:0.25rem 0">
                        <button class="btn-outline" id="btn-ki-edit" style="font-size:0.72rem">${hasKnownIssues ? 'Edit' : 'Add Issue'}</button>
                    </div>
                    <div id="ki-edit-area" style="display:none;padding:0.25rem 0" class="rx-l2">
                        <textarea id="ki-textarea" rows="3" style="width:100%;font-size:0.8rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:0.4rem">${esc(doc?.technical_known_issues || "")}</textarea>
                        <div style="display:flex;gap:0.5rem;margin-top:0.4rem">
                            <button class="btn-outline" id="ki-save" style="font-size:0.72rem">Save</button>
                            <button class="btn-outline" id="ki-cancel" style="font-size:0.72rem">Cancel</button>
                        </div>
                    </div>
                </div>
            </div>

            <div class="rx-section rx-l1">
                <div class="rx-toggle" data-target="optimization-section">
                    <span class="rx-arrow">&#9656;</span> Optimization
                    ${unusedMC + unusedData.unused_tables_count > 0
                        ? `<span class="badge badge-yellow" style="margin-left:0.35rem;font-size:0.58rem">${unusedMC + unusedData.unused_tables_count} unused</span>`
                        : `<span class="badge badge-green" style="margin-left:0.35rem;font-size:0.58rem">clean</span>`}
                </div>
                <div class="rx-body" id="optimization-section" style="display:none">${optimizationInner}</div>
            </div>

        </div>
    </td>`;

    if (targetRow) {
        targetRow.after(expandRow);
    } else {
        const tbody = document.querySelector("#dt-reports tbody");
        if (tbody) tbody.appendChild(expandRow);
    }

    // ── Bind all collapsible toggles ──
    expandRow.querySelectorAll(".rx-toggle[data-target]").forEach(toggle => {
        toggle.addEventListener("click", () => {
            const body = expandRow.querySelector(`#${toggle.dataset.target}`);
            if (!body) return;
            const showing = body.style.display !== "none";
            body.style.display = showing ? "none" : "";
            const arrow = toggle.querySelector(".rx-arrow");
            if (arrow) arrow.innerHTML = showing ? "&#9656;" : "&#9662;";
        });
    });

    // Clickable sources -> navigate to source detail
    expandRow.querySelectorAll(".report-source-clickable").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const srcId = parseInt(el.dataset.sourceId);
            const src = sourceMap.get(srcId);
            if (src) { await navigate("sources"); showSourceDetail(src); }
        });
    });

    // Unused measure items - click to show/hide DAX
    expandRow.querySelectorAll(".unused-measure-item").forEach(el => {
        const dax = el.querySelector(".unused-measure-dax");
        if (dax) {
            el.style.cursor = "pointer";
            el.addEventListener("click", () => {
                dax.style.display = dax.style.display === "none" ? "block" : "none";
            });
        }
    });

    // Lineage button
    expandRow.querySelectorAll(".btn-lineage-nav").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            await navigate("lineage");
            const sel = document.getElementById("lineage-report-select");
            if (sel) { sel.value = report.id; sel.dispatchEvent(new Event("change")); }
        });
    });

    // ── Documentation inline edit ──
    const docViewArea = expandRow.querySelector("#doc-view-area");
    const docEditArea = expandRow.querySelector("#doc-edit-area");

    expandRow.querySelector("#btn-doc-inline-edit")?.addEventListener("click", (e) => {
        e.stopPropagation();
        docViewArea.style.display = "none";
        docEditArea.style.display = "";
    });

    expandRow.querySelector("#doc-e-cancel")?.addEventListener("click", (e) => {
        e.stopPropagation();
        docEditArea.style.display = "none";
        docViewArea.style.display = "";
    });

    expandRow.querySelector("#doc-e-save")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        e.target.disabled = true;
        const body = {
            report_id: report.id,
            title: report.name,
            business_purpose: expandRow.querySelector("#doc-e-purpose").value.trim() || null,
            business_audience: expandRow.querySelector("#doc-e-audience").value.trim() || null,
            technical_transformations: expandRow.querySelector("#doc-e-transforms").value.trim() || null,
        };
        try {
            if (doc) {
                await apiPatch(`/api/documentation/${doc.id}`, body);
            } else {
                body.status = "draft";
                body.linked_entities = [{ entity_type: "report", entity_id: report.id }];
                const created = await apiPostJson("/api/documentation", body);
                doc = created;
            }
            toast("Documentation saved");
            expandRow.remove();
            showReportDetail(report);
        } catch (err) {
            toast("Save failed: " + err.message);
            e.target.disabled = false;
        }
    });

    // Auto-fill (Python extraction)
    expandRow.querySelector("#btn-doc-inline-suggest")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        e.target.disabled = true;
        e.target.textContent = "Loading...";
        try {
            const s = await api(`/api/documentation/suggest/${report.id}`);
            docViewArea.style.display = "none";
            docEditArea.style.display = "";
            if (s.technical_transformations) expandRow.querySelector("#doc-e-transforms").value = s.technical_transformations;
            toast("Technical fields pre-filled. Add business context and save.");
        } catch (err) { toast("Auto-fill failed: " + err.message); }
        finally { e.target.disabled = false; e.target.textContent = "Auto-fill"; }
    });

    // AI Suggest (LLM-powered)
    expandRow.querySelector("#btn-doc-ai-suggest")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        e.target.disabled = true;
        e.target.textContent = "Generating...";
        try {
            const s = await apiPost(`/api/documentation/ai-suggest/${report.id}`);
            docViewArea.style.display = "none";
            docEditArea.style.display = "";
            if (s.purpose) expandRow.querySelector("#doc-e-purpose").value = s.purpose;
            if (s.formulas) expandRow.querySelector("#doc-e-transforms").value = s.formulas;
            toast("AI suggestions filled in. Review and save.");
        } catch (err) { toast("AI suggest failed: " + err.message); }
        finally { e.target.disabled = false; e.target.textContent = "AI Suggest"; }
    });

    // ── Known Issues edit/save ──
    expandRow.querySelector("#btn-ki-edit")?.addEventListener("click", (e) => {
        e.stopPropagation();
        const editArea = expandRow.querySelector("#ki-edit-area");
        editArea.style.display = editArea.style.display === "none" ? "" : "none";
    });

    expandRow.querySelector("#ki-cancel")?.addEventListener("click", (e) => {
        e.stopPropagation();
        expandRow.querySelector("#ki-edit-area").style.display = "none";
    });

    expandRow.querySelector("#ki-save")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        e.target.disabled = true;
        const kiText = expandRow.querySelector("#ki-textarea").value.trim() || null;
        try {
            if (doc) {
                await apiPatch(`/api/documentation/${doc.id}`, { technical_known_issues: kiText });
            } else {
                const created = await apiPostJson("/api/documentation", {
                    report_id: report.id, title: report.name, status: "draft",
                    technical_known_issues: kiText,
                    linked_entities: [{ entity_type: "report", entity_id: report.id }],
                });
                doc = created;
            }
            toast("Known issues saved");
            expandRow.remove();
            showReportDetail(report);
        } catch (err) {
            toast("Save failed: " + err.message);
            e.target.disabled = false;
        }
    });
}

function _visualTypeLabel(type) {
    const labels = {
        barChart: "Bar", clusteredBarChart: "Bar", stackedBarChart: "Bar",
        columnChart: "Column", clusteredColumnChart: "Column", stackedColumnChart: "Column",
        lineChart: "Line", areaChart: "Area", lineClusteredColumnComboChart: "Combo",
        lineStackedColumnComboChart: "Combo",
        pieChart: "Pie", donutChart: "Donut", treemap: "Treemap",
        card: "Card", multiRowCard: "Multi Card", kpi: "KPI",
        tableEx: "Table", pivotTable: "Matrix", table: "Table",
        slicer: "Slicer", advancedSlicerVisual: "Slicer",
        map: "Map", filledMap: "Filled Map", shape: "Shape",
        gauge: "Gauge", waterfallChart: "Waterfall", funnel: "Funnel",
        ribbonChart: "Ribbon", scatterChart: "Scatter",
        decompositionTreeVisual: "Decomp Tree", cardVisual: "Card",
        textbox: "Text", image: "Image", actionButton: "Button",
    };
    return labels[type] || type || "Visual";
}

function _autoVisualTitle(v) {
    // Generate a descriptive label from field references
    if (!v.fields || v.fields.length === 0) return '<span style="color:var(--text-dim)">(no fields)</span>';
    const fieldNames = v.fields.map(f => f.field.replace(/_/g, ' '));
    const label = fieldNames.slice(0, 3).join(', ') + (fieldNames.length > 3 ? ` +${fieldNames.length - 3}` : '');
    return `<span style="color:var(--text-dim)">${label}</span>`;
}


// ── Pages ──

async function renderDashboard() {
    const [data, sources, reports, actions, healthTrend, people, userActivity] = await Promise.all([
        api("/api/dashboard"),
        api("/api/sources"),
        api("/api/reports"),
        api("/api/actions"),
        api("/api/schedules/health-trend"),
        api("/api/people"),
        api("/api/dashboard/user-activity"),
    ]);
    const scan = data.last_scan;
    window._dashboardData = data;
    window._healthTrend = healthTrend;

    const total = data.sources_total;
    const hasSources = total > 0;
    const allUnknown = hasSources && data.sources_fresh === 0 && data.sources_stale === 0 && data.sources_outdated === 0;
    const freshPct = hasSources ? pct(data.sources_fresh, total) : 0;
    const outdatedPct = hasSources ? pct(data.sources_outdated, total) : 0;
    const unknownPct = hasSources ? 100 - freshPct - outdatedPct : 0;

    // Health label
    let healthLabel;
    if (!hasSources) healthLabel = "No sources yet";
    else if (allUnknown) healthLabel = "Not yet probed";
    else healthLabel = freshPct + "% healthy";

    // Store for click-through navigation
    window._dashboardSources = sources;
    window._dashboardReports = reports;
    window._dashboardActions = actions;
    window._dashboardPeople = people;
    window._dashboardUserActivity = userActivity;

    return `
        <div class="stat-grid">
            <div class="stat-card card-purple stat-card-clickable" data-navigate="reports" role="button" tabindex="0" aria-label="Reports: ${data.reports_total} total">
                <div class="stat-label">Reports</div>
                <div class="stat-value">${data.reports_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green" title="All data sources are fresh and up to date">${reports.filter(r => r.status === "healthy").length} healthy</span>
                    <span class="stat-dot dot-red" title="Data sources are past freshness threshold">${reports.filter(r => r.status === "degraded" || r.status === "at risk").length} degraded</span>
                    ${reports.filter(r => r.status === "unknown").length ? `<span class="stat-dot dot-yellow" title="Status has not been probed yet">${reports.filter(r => r.status === "unknown").length} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card card-blue stat-card-clickable${data.sources_outdated > 0 ? ' pulse-border-red' : ''}" data-navigate="sources" role="button" tabindex="0" aria-label="Total Sources: ${data.sources_total}, ${data.sources_fresh} healthy, ${data.sources_outdated} degraded">
                <div class="stat-label">Total Sources</div>
                <div class="stat-value">${data.sources_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green stat-filter" data-filter="healthy" title="Data updated within freshness threshold">${data.sources_fresh} healthy</span>
                    <span class="stat-dot dot-red stat-filter" data-filter="degraded" title="Data past freshness threshold">${data.sources_outdated} degraded</span>
                    ${data.sources_unknown ? `<span class="stat-dot dot-yellow stat-filter" data-filter="unknown" title="Source has not been probed yet or has no rule">${data.sources_unknown} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card ${data.alerts_active > 0 ? 'card-red pulse-border-red' : 'card-green'} stat-card-clickable" data-scroll-to="dashboard-alerts" role="button" tabindex="0" aria-label="Active Alerts: ${data.alerts_active}">
                <div class="stat-label">Active Alerts</div>
                <div class="stat-value">${data.alerts_active}</div>
                <div class="stat-card-link">View &darr;</div>
            </div>
            <div class="stat-card card-green stat-card-clickable" data-navigate="scanner" role="button" tabindex="0" aria-label="Last Scan: ${scan ? timeAgo(scan.started_at) : 'never'}" title="Click to view scanner details and trigger new scans">
                <div class="stat-label">Last Scan</div>
                <div class="stat-value" style="font-size:1.1rem">${scan ? timeAgo(scan.started_at) : "never"}</div>
                ${scan ? `<div class="stat-breakdown">${scan.reports_scanned} reports &middot; ${scan.sources_found} sources</div>` : ""}
                <div class="stat-card-link">View &rarr;</div>
            </div>
        </div>

        <div class="health-bar-container">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <h2 style="margin-bottom:0" title="Freshness status of all registered data sources">Source Health</h2>
                <span style="color:var(--text-dim);font-size:0.78rem" title="Healthy = within freshness threshold. Degraded = past threshold.">${healthLabel}</span>
            </div>
            ${!hasSources ? `
            <div class="health-bar">
                <div class="segment segment-muted" style="width:100%"></div>
            </div>
            <div style="text-align:center;color:var(--text-dim);font-size:0.78rem;margin-top:0.5rem">Run a scan to discover data sources</div>
            ` : allUnknown ? `
            <div class="health-bar">
                <div class="segment segment-yellow" style="width:100%"></div>
            </div>
            <div style="text-align:center;color:var(--text-dim);font-size:0.78rem;margin-top:0.5rem">${total} sources discovered  - probe to check freshness</div>
            ` : `
            <div class="health-bar">
                ${freshPct > 0 ? `<div class="segment segment-green segment-clickable" data-tooltip="${data.sources_fresh} healthy (${freshPct}%)" data-filter="healthy" style="width:${freshPct}%"></div>` : ""}
                ${outdatedPct > 0 ? `<div class="segment segment-red segment-clickable" data-tooltip="${data.sources_outdated} degraded (${outdatedPct}%)" data-filter="degraded" style="width:${outdatedPct}%"></div>` : ""}
                ${unknownPct > 0 ? `<div class="segment segment-yellow" data-tooltip="${data.sources_unknown || 0} unknown (${unknownPct}%)" style="width:${unknownPct}%"></div>` : ""}
            </div>
            <div class="health-tooltip" id="health-tooltip"></div>
            <div class="health-legend">
                <span class="stat-dot dot-green">${data.sources_fresh} Healthy</span>
                <span class="stat-dot dot-red">${data.sources_outdated} Degraded</span>
                ${data.sources_unknown ? `<span class="stat-dot dot-yellow">${data.sources_unknown} Unknown</span>` : ""}
            </div>
            `}
        </div>

        <div class="dashboard-trend">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.4rem">
                <h2 style="margin:0">Health Trend <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">past 30 days</span></h2>
            </div>
            <div class="alert-trend-container" style="position:relative">
                <canvas id="health-trend-canvas" height="120" role="img" aria-label="Health trend chart showing source freshness over the past 30 days"></canvas>
                <div id="health-trend-tooltip" class="chart-tooltip"></div>
            </div>
        </div>

        ${renderUserActivityTable(userActivity)}

        <div id="dashboard-alerts" class="dashboard-alerts-section">
            ${renderDashboardAlertsSection(actions, people)}
        </div>
    `;
}

function renderUserActivityTable(rows) {
    const activity = [...(rows || [])].sort((a, b) =>
        (b.action_count || 0) - (a.action_count || 0) ||
        String(b.last_action_at || "").localeCompare(String(a.last_action_at || "")) ||
        String(a.actor || "").localeCompare(String(b.actor || ""))
    );
    const totalActions = activity.reduce((sum, row) => sum + Number(row.action_count || 0), 0);

    const bodyHtml = activity.length
        ? `
            <div class="user-activity-table-wrap">
                <table class="user-activity-table">
                    <thead>
                        <tr>
                            <th>User</th>
                            <th style="text-align:right">Actions last 7d</th>
                            <th>Last action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${activity.map((row, index) => `
                            <tr>
                                <td>
                                    <span class="user-activity-rank">${index + 1}</span>
                                    <span class="user-activity-name">${esc(row.actor || "Unregistered")}</span>
                                </td>
                                <td style="text-align:right"><strong>${fmtInt(row.action_count || 0)}</strong></td>
                                <td>${row.last_action_at ? timeAgo(row.last_action_at) : "-"}</td>
                            </tr>
                        `).join("")}
                    </tbody>
                </table>
            </div>
        `
        : '<div class="empty-state" style="padding:1.1rem">No user activity in the last 7 days</div>';

    return `
        <section class="user-activity-panel" aria-label="Actions per user">
            <div class="user-activity-header">
                <div>
                    <h2>Actions Per User</h2>
                    <span>Last 7 days, ranked by configuration activity</span>
                </div>
                <strong>${fmtInt(totalActions)} total</strong>
            </div>
            ${bodyHtml}
        </section>
    `;
}

function renderFixFirstPanel(openActions) {
    const picks = [...openActions]
        .filter(a => a.triage_rank)
        .sort((a, b) => (a.triage_rank || 999) - (b.triage_rank || 999))
        .slice(0, 3);
    if (picks.length === 0) return "";

    const ctaHtml = (a) => {
        const label = esc(a.triage_cta || "Open details");
        if (a.triage_cta === "Assign owner") {
            return `<button type="button" class="fix-first-cta fix-first-focus-owner" data-action-id="${a.id}">${label}</button>`;
        }
        if (a.asset_type === "source" && a.asset_id) {
            return `<button type="button" class="fix-first-cta alerts-source-link" data-source-id="${a.asset_id}">${label}</button>`;
        }
        if (a.asset_type === "report" && a.asset_id) {
            return `<button type="button" class="fix-first-cta alerts-go-report" data-report-id="${a.asset_id}">${label}</button>`;
        }
        if (a.asset_type === "scheduled_task" && a.asset_id) {
            return `<button type="button" class="fix-first-cta alerts-task-link" data-task-id="${a.asset_id}">${label}</button>`;
        }
        if (a.asset_type === "script" && a.asset_id) {
            return `<button type="button" class="fix-first-cta alerts-script-link" data-script-id="${a.asset_id}">${label}</button>`;
        }
        return `<button type="button" class="fix-first-cta fix-first-focus-owner" data-action-id="${a.id}">${label}</button>`;
    };

    const items = picks.map(a => {
        const rawName = a.asset_name || a.source_name || a.report_name || "-";
        const assetName = shortNameFromPath(rawName) || rawName;
        const reasons = (a.triage_reasons || []).slice(0, 3).map(r => `<span>${esc(r)}</span>`).join("");
        return `
            <div class="fix-first-item">
                <div class="fix-first-rank">${a.triage_rank}</div>
                <div class="fix-first-body">
                    <div class="fix-first-title-row">
                        ${alertAssetLogo(a)}
                        <strong>${esc(assetName)}</strong>
                        ${actionTypeBadge(a.type)}
                    </div>
                    <div class="fix-first-reasons">${reasons}</div>
                </div>
                <div class="fix-first-metrics">
                    <span class="impact-pill" title="Views in the last 30 days">${fmtInt(a.impact_views_30d || 0)}</span>
                    ${ctaHtml(a)}
                </div>
            </div>
        `;
    }).join("");

    return `
        <section class="fix-first-panel" aria-label="Fix this first">
            <div class="fix-first-header">
                <div>
                    <h3>Fix This First</h3>
                    <span>Ranked by views, issue type, time since detection, and ownership.</span>
                </div>
            </div>
            <div class="fix-first-list">${items}</div>
        </section>
    `;
}

function renderDashboardAlertsSection(actions, people) {
    const biPeople = people.filter(p => p.role === "BI").map(p => p.name);

    // The scanner controls whether an alert remains active.
    const activeActions = actions.filter(a => a.status !== "resolved" && a.status !== "expected");
    const unassignedCount = activeActions.filter(a => !a.assigned_to).length;

    // Per-person active counts
    const personCounts = {};
    biPeople.forEach(p => { personCounts[p] = 0; });
    activeActions.forEach(a => {
        if (a.assigned_to && personCounts[a.assigned_to] !== undefined) {
            personCounts[a.assigned_to]++;
        }
    });

    const chipsHtml = `
        <button class="alerts-chip active" data-filter-person="all">All <span class="alerts-chip-count">${activeActions.length}</span></button>
        <button class="alerts-chip" data-filter-person="__unassigned__">Unassigned <span class="alerts-chip-count">${unassignedCount}</span></button>
        ${biPeople.map(p => `
            <button class="alerts-chip" data-filter-person="${esc(p)}">${esc(p)} <span class="alerts-chip-count">${personCounts[p]}</span></button>
        `).join("")}
    `;

    const tableHtml = renderDashboardAlertsTable(actions, biPeople, "all");
    const fixFirstHtml = renderFixFirstPanel(activeActions);

    return `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.6rem;flex-wrap:wrap;gap:0.5rem">
            <h2 style="margin:0">Alerts</h2>
            <span style="color:var(--text-dim);font-size:0.78rem">Sorted by views, then first detected</span>
        </div>
        ${fixFirstHtml}
        <div class="alerts-chips">${chipsHtml}</div>
        <div id="dashboard-alerts-tbody-wrap">${tableHtml}</div>
    `;
}

function renderDashboardAlertsTable(actions, biPeople, personFilter) {
    let filtered = actions.filter(a => a.status !== "resolved" && a.status !== "expected");
    if (personFilter && personFilter !== "all") {
        if (personFilter === "__unassigned__") {
            filtered = filtered.filter(a => !a.assigned_to);
        } else {
            filtered = filtered.filter(a => a.assigned_to === personFilter);
        }
    }

    if (filtered.length === 0) {
        return '<div class="empty-state" style="padding:1.5rem">No active alerts for this filter</div>';
    }

    const ownerOptions = (currentOwner) => `
        <option value=""${!currentOwner ? ' selected' : ''}>Unassigned</option>
        ${biPeople.map(p => `<option value="${esc(p)}"${p === currentOwner ? ' selected' : ''}>${esc(p)}</option>`).join("")}
    `;

    const rows = filtered.map(a => {
        const rawName = a.asset_name || a.source_name || a.report_name || "-";
        const assetName = shortNameFromPath(rawName) || rawName;
        const linkData = a.asset_type === "source"
            ? `alerts-source-link" data-source-id="${a.asset_id}`
            : a.asset_type === "scheduled_task"
            ? `alerts-task-link" data-task-id="${a.asset_id}`
            : a.asset_type === "script"
            ? `alerts-script-link" data-script-id="${a.asset_id}`
            : null;

        // Secondary info under the asset name
        let sub = "";
        if (a.asset_type === "source" && a.top_report_name) {
            sub = `<div style="font-size:0.7rem;color:var(--text-dim);font-weight:400">affects ${esc(a.top_report_name)}${a.report_names.length > 1 ? ` +${a.report_names.length - 1}` : ""}</div>`;
        } else if (a.detail_items && a.detail_items.length > 0) {
            // For schedule_mismatch, show the sources that refreshed after
            const summary = a.detail_items.slice(0, 3).map(d =>
                `${esc(d.name)} (+${d.delta_hours < 48 ? d.delta_hours + 'h' : Math.floor(d.delta_hours / 24) + 'd'})`
            ).join(", ");
            const more = a.detail_items.length > 3 ? ` +${a.detail_items.length - 3}` : "";
            sub = `<div style="font-size:0.7rem;color:var(--text-dim);font-weight:400">stale vs: ${summary}${more}</div>`;
        }

        const assetBody = `<div class="alerts-asset-body"><div class="alerts-asset-title"><strong>${esc(assetName)}</strong></div>${sub}</div>`;
        const assetCell = linkData
            ? `<a class="alerts-link ${linkData}" title="Open detail">${alertAssetLogo(a)}${assetBody}</a>`
            : `<div class="alerts-asset-plain">${alertAssetLogo(a)}${assetBody}</div>`;

        const degradedSince = a.degraded_since || a.created_at;
        const impact = a.impact_views_30d || 0;
        const hasExpandable = a.type === "schedule_mismatch" || !!a.recommendation || !!a.pbi_refresh_error || (a.detail_items && a.detail_items.length > 0);
        const mainRow = `
            <tr class="alerts-row" data-action-id="${a.id}" data-assigned="${esc(a.assigned_to || '')}">
                <td>
                    ${hasExpandable
                        ? `<button type="button" class="alerts-expand-btn" data-action-id="${a.id}" aria-expanded="false" title="Show error and recommendation"><span class="alerts-expand-label">Details</span><span class="alerts-expand-arrow">&#8250;</span></button>`
                        : '<span class="alerts-expand-spacer"></span>'}
                    <span class="alerts-asset-wrap">${assetCell}</span>
                </td>
                <td style="text-align:right">
                    <span class="impact-pill" title="Views in the last 30 days">${fmtInt(impact)}</span>
                </td>
                <td>
                    <span class="degraded-since" title="${esc(degradedSince || "Unknown")}">${formatDateOnly(degradedSince)}</span>
                </td>
                <td>${actionTypeBadge(a.type, true)}</td>
                <td class="alerts-owner-cell">
                    <select class="dashboard-action-owner-select" data-action-id="${a.id}">
                        ${ownerOptions(a.assigned_to || "")}
                    </select>
                </td>
                <td>
                    <div class="alerts-row-actions">
                        <button type="button" class="alerts-action-btn alerts-notify-sla" data-action-id="${a.id}">Notify SLA</button>
                    </div>
                </td>
            </tr>
        `;

        const sourceLinksHtml = (a.detail_items || []).map(d => {
            const delta = d.delta_hours < 48
                ? `${d.delta_hours}h`
                : `${Math.floor(d.delta_hours / 24)}d`;
            return `<a class="alerts-source-link alerts-detail-source" data-source-id="${d.id}">
                <span class="alerts-source-main">
                    <strong>${esc(d.name)}</strong>
                    <span class="alerts-source-gap">+${delta} after report</span>
                </span>
            </a>`;
        }).join("");

        const expandRow = hasExpandable ? `
            <tr class="alerts-expand-row" data-action-id="${a.id}" style="display:none">
                <td colspan="6" class="alerts-expand-cell">
                    ${a.pbi_refresh_error ? `<div class="alerts-refresh-error"><strong>PBI Refresh Error:</strong> ${esc(a.pbi_refresh_error)}</div>` : ""}
                    ${a.recommendation ? `<div class="alerts-recommendation"><strong>Recommendation:</strong> ${esc(a.recommendation)}</div>` : ""}
                    ${sourceLinksHtml ? `<div class="alerts-sources-label">Sources refreshed after the report:</div><div class="alerts-sources-list">${sourceLinksHtml}</div>` : ""}
                </td>
            </tr>
        ` : "";

        return mainRow + expandRow;
    }).join("");

    return `
        <div class="alerts-table-wrap">
            <table class="alerts-table">
                <thead>
                    <tr>
                        <th style="width:38%">Asset</th>
                        <th style="width:8%;text-align:right">Views</th>
                        <th style="width:13%">First detected</th>
                        <th style="width:16%">Issue</th>
                        <th style="width:17%">Owner</th>
                        <th style="width:8%">Action</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

function bindDashboardAlerts() {
    // Person filter chips
    document.querySelectorAll(".alerts-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            document.querySelectorAll(".alerts-chip").forEach(c => c.classList.remove("active"));
            chip.classList.add("active");
            const person = chip.dataset.filterPerson;
            const actions = window._dashboardActions || [];
            const people = window._dashboardPeople || [];
            const biPeople = people.filter(p => p.role === "BI").map(p => p.name);
            const wrap = document.getElementById("dashboard-alerts-tbody-wrap");
            if (wrap) {
                wrap.innerHTML = renderDashboardAlertsTable(actions, biPeople, person);
                bindDashboardAlertsRowControls();
            }
        });
    });
    bindDashboardAlertsRowControls();

    // Scroll-to anchors
    document.querySelectorAll(".stat-card-clickable[data-scroll-to]").forEach(card => {
        card.addEventListener("click", () => {
            const target = document.getElementById(card.dataset.scrollTo);
            if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });
}

function _waitForDataTable(id, timeoutMs = 2000) {
    // Poll until the tbody for this table is in the DOM with at least one
    // clickable row AND window._dt[id]._displayRows is populated. Both
    // conditions need to hold for showReportDetail/showSourceDetail to
    // find their anchor row.
    return new Promise(resolve => {
        const start = Date.now();
        const check = () => {
            const rows = document.querySelectorAll(`#${id} tbody tr[data-clickable]`);
            const dt = window._dt && window._dt[id];
            if (rows.length > 0 && dt && dt._displayRows && dt._displayRows.length > 0) {
                resolve(true);
                return;
            }
            if (Date.now() - start > timeoutMs) {
                resolve(false);
                return;
            }
            requestAnimationFrame(check);
        };
        check();
    });
}

function _waitForElement(selector, timeoutMs = 2000) {
    return new Promise(resolve => {
        const start = Date.now();
        const check = () => {
            const el = document.querySelector(selector);
            if (el) { resolve(el); return; }
            if (Date.now() - start > timeoutMs) { resolve(null); return; }
            requestAnimationFrame(check);
        };
        check();
    });
}

function bindDashboardAlertsRowControls() {
    document.querySelectorAll(".fix-first-focus-owner").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const actionId = btn.dataset.actionId;
            if (!actionId) return;
            let row = document.querySelector(`.alerts-row[data-action-id="${actionId}"]`);
            if (!row) {
                const actions = window._dashboardActions || [];
                const people = window._dashboardPeople || [];
                const biPeople = people.filter(p => p.role === "BI").map(p => p.name);
                const wrap = document.getElementById("dashboard-alerts-tbody-wrap");
                if (wrap) {
                    wrap.innerHTML = renderDashboardAlertsTable(actions, biPeople, "all");
                    document.querySelectorAll(".alerts-chip").forEach(chip => chip.classList.toggle("active", chip.dataset.filterPerson === "all"));
                    bindDashboardAlertsRowControls();
                    row = document.querySelector(`.alerts-row[data-action-id="${actionId}"]`);
                }
            }
            if (!row) return;
            row.scrollIntoView({ behavior: "smooth", block: "center" });
            const ownerSelect = row.querySelector(".dashboard-action-owner-select");
            if (ownerSelect) {
                ownerSelect.focus();
                ownerSelect.classList.add("focus-pulse");
                setTimeout(() => ownerSelect.classList.remove("focus-pulse"), 1200);
            }
        });
    });

    // Explicit report button - navigate to reports page and open detail.
    document.querySelectorAll(".alerts-go-report").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const rid = parseInt(el.dataset.reportId);
            if (!rid) return;
            await navigate("reports");
            await _waitForDataTable("dt-reports");
            let report;
            try {
                const all = await api("/api/reports");
                report = all.find(r => r.id === rid);
            } catch (_) {
                report = (window._dashboardReports || []).find(r => r.id === rid);
            }
            if (!report) return;
            await showReportDetail(report);
            const expanded = await _waitForElement(`.report-expand-row[data-report-id="${rid}"]`, 3000);
            if (expanded) expanded.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    // Clickable source cell - navigate to sources page and open detail
    document.querySelectorAll(".alerts-source-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const sid = parseInt(el.dataset.sourceId);
            if (!sid) return;
            await navigate("sources");
            await _waitForDataTable("dt-sources");
            let src;
            try {
                src = await api(`/api/sources/${sid}`);
            } catch (_) {
                src = (window._dashboardSources || []).find(s => s.id === sid);
            }
            if (!src) return;
            await showSourceDetail(src);
            const panel = await _waitForElement("#source-detail", 3000);
            if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    // Expand button toggles the recommendation/source-list row
    document.querySelectorAll(".alerts-expand-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const actionId = btn.dataset.actionId;
            const expandRow = document.querySelector(`.alerts-expand-row[data-action-id="${actionId}"]`);
            if (!expandRow) return;
            const isOpen = expandRow.style.display !== "none";
            expandRow.style.display = isOpen ? "none" : "";
            btn.classList.toggle("expanded", !isOpen);
            btn.setAttribute("aria-expanded", isOpen ? "false" : "true");
        });
    });

    // Source links inside the expanded section (schedule_mismatch sources)
    document.querySelectorAll(".alerts-detail-source").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const sid = parseInt(el.dataset.sourceId);
            if (!sid) return;
            await navigate("sources");
            await _waitForDataTable("dt-sources");
            let src;
            try { src = await api(`/api/sources/${sid}`); } catch (_) {}
            if (!src) return;
            await showSourceDetail(src);
            const panel = await _waitForElement("#source-detail", 3000);
            if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    // Clickable scheduled task cell - navigate to scheduled tasks page
    document.querySelectorAll(".alerts-task-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const tid = parseInt(el.dataset.taskId);
            if (!tid) return;
            await navigate("scheduledtasks");
            // Try to scroll/open the specific task
            setTimeout(() => {
                const row = document.querySelector(`[data-task-id="${tid}"]`);
                if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 150);
        });
    });

    // Clickable script cell - navigate to scripts page
    document.querySelectorAll(".alerts-script-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const scid = parseInt(el.dataset.scriptId);
            if (!scid) return;
            await navigate("scripts");
            setTimeout(() => {
                const row = document.querySelector(`[data-script-id="${scid}"]`);
                if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 150);
        });
    });

    document.querySelectorAll(".alerts-notify-sla").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            _openNotifySlaModal(btn.dataset.actionId);
        });
    });

    // Owner assignment selects
    document.querySelectorAll(".dashboard-action-owner-select").forEach(sel => {
        sel.addEventListener("change", async () => {
            const actionId = sel.dataset.actionId;
            const newOwner = sel.value || null;
            try {
                await apiPatch(`/api/actions/${actionId}`, { assigned_to: newOwner });
                if (window._dashboardActions) {
                    const a = window._dashboardActions.find(x => x.id == actionId);
                    if (a) a.assigned_to = newOwner;
                }
                _refreshDashboardAlertChipCounts();
                toast(`Alert assigned to ${newOwner || "unassigned"}`);
            } catch (err) {
                toast("Failed to assign: " + err.message);
            }
        });
    });
}

function _refreshDashboardAlertChipCounts() {
    const actions = window._dashboardActions || [];
    const people = window._dashboardPeople || [];
    const biPeople = people.filter(p => p.role === "BI").map(p => p.name);
    const open = actions.filter(a => a.status !== "resolved" && a.status !== "expected");
    const unassigned = open.filter(a => !a.assigned_to).length;
    const counts = {};
    biPeople.forEach(p => counts[p] = 0);
    open.forEach(a => {
        if (a.assigned_to && counts[a.assigned_to] !== undefined) counts[a.assigned_to]++;
    });
    document.querySelectorAll(".alerts-chip").forEach(chip => {
        const person = chip.dataset.filterPerson;
        const countEl = chip.querySelector(".alerts-chip-count");
        if (!countEl) return;
        if (person === "all") countEl.textContent = open.length;
        else if (person === "__unassigned__") countEl.textContent = unassigned;
        else countEl.textContent = counts[person] || 0;
    });
}

function drawHealthTrendChart() {
    const canvas = document.getElementById("health-trend-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width, H = rect.height;
    const isDarkMode = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim().startsWith('#1');
    const gridColor = isDarkMode ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
    const labelColor = isDarkMode ? "rgba(255,255,255,0.45)" : "rgba(0,0,0,0.35)";

    const trend = window._healthTrend || [];
    if (trend.length === 0) return;

    const padL = 30, padR = 10, padT = 10, padB = 24;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;

    // Compute max total (stacked)
    const maxVal = Math.max(...trend.map(t => (t.healthy || 0) + (t.degraded || 0) + (t.unknown || 0)), 1);

    // Grid lines
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    const gridSteps = Math.min(maxVal, 4);
    for (let i = 0; i <= gridSteps; i++) {
        const y = padT + chartH - (i / gridSteps) * chartH;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
        ctx.fillStyle = labelColor;
        ctx.font = "10px 'DM Sans', sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(Math.round(i / gridSteps * maxVal), padL - 4, y + 3);
    }

    // Helper to get x position for index
    const xAt = (i) => trend.length === 1 ? padL + chartW / 2 : padL + (i / (trend.length - 1)) * chartW;
    const yAt = (val) => padT + chartH - (val / maxVal) * chartH;

    // Build stacked y-values per point
    const series = trend.map(t => ({
        degraded: (t.degraded || 0) + (t.at_risk || 0),
        unknown: t.unknown || 0,
        healthy: t.healthy || 0,
    }));

    const colors = {
        healthy: { fill: "rgba(22, 128, 61, 0.12)", stroke: "#15803d" },
        degraded: { fill: "rgba(185, 28, 28, 0.12)", stroke: "#b91c1c" },
        unknown: { fill: "rgba(100, 116, 139, 0.12)", stroke: "#64748b" },
    };

    // Degraded area (bottom)
    ctx.beginPath();
    for (let i = 0; i < trend.length; i++) {
        const x = xAt(i);
        const y = yAt(series[i].degraded);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.lineTo(xAt(trend.length - 1), padT + chartH);
    ctx.lineTo(padL, padT + chartH);
    ctx.closePath();
    ctx.fillStyle = colors.degraded.fill;
    ctx.fill();

    // Unknown area (middle)
    ctx.beginPath();
    for (let i = 0; i < trend.length; i++) {
        const x = xAt(i);
        const total = series[i].degraded + series[i].unknown;
        const y = yAt(total);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = trend.length - 1; i >= 0; i--) {
        ctx.lineTo(xAt(i), yAt(series[i].degraded));
    }
    ctx.closePath();
    ctx.fillStyle = colors.unknown.fill;
    ctx.fill();

    // Healthy area (top)
    ctx.beginPath();
    for (let i = 0; i < trend.length; i++) {
        const x = xAt(i);
        const total = series[i].degraded + series[i].unknown + series[i].healthy;
        const y = yAt(total);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = trend.length - 1; i >= 0; i--) {
        ctx.lineTo(xAt(i), yAt(series[i].degraded + series[i].unknown));
    }
    ctx.closePath();
    ctx.fillStyle = colors.healthy.fill;
    ctx.fill();

    // Draw lines for each series
    function drawLine(getY, color) {
        ctx.beginPath();
        for (let i = 0; i < trend.length; i++) {
            const x = xAt(i);
            const y = yAt(getY(i));
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }
    drawLine(i => series[i].degraded + series[i].unknown + series[i].healthy, colors.healthy.stroke);
    drawLine(i => series[i].degraded + series[i].unknown, colors.unknown.stroke);
    drawLine(i => series[i].degraded, colors.degraded.stroke);

    // X-axis labels (every 7 days)
    ctx.fillStyle = labelColor;
    ctx.font = "9px 'DM Sans', sans-serif";
    ctx.textAlign = "center";
    trend.forEach((t, i) => {
        if (i % 7 === 0 || i === trend.length - 1) {
            const x = xAt(i);
            const parts = t.day.split("-");
            ctx.fillText(`${parts[2]}/${parts[1]}`, x, H - 4);
        }
    });

    // Legend
    ctx.font = "9px 'DM Sans', sans-serif";
    const legendX = padL + 4;
    const legendY = padT + 10;
    [
        { color: colors.healthy.stroke, label: "Healthy" },
        { color: colors.degraded.stroke, label: "Degraded" },
        { color: colors.unknown.stroke, label: "Unknown" },
    ].forEach((item, idx) => {
        const x = legendX + idx * 78;
        ctx.fillStyle = item.color;
        ctx.fillRect(x, legendY - 6, 8, 8);
        ctx.fillStyle = labelColor;
        ctx.textAlign = "left";
        ctx.fillText(item.label, x + 11, legendY + 1);
    });

    // Store chart geometry for tooltip
    window._healthChartGeom = { padL, padR, padT, padB, chartW, chartH, trend, series, xAt, yAt, maxVal, canvasRect: rect };

    // Tooltip handler: remove previous listeners to avoid accumulation
    canvas.removeEventListener("mousemove", _healthChartMouseMove);
    canvas.removeEventListener("mouseleave", _healthChartMouseLeave);
    canvas.addEventListener("mousemove", _healthChartMouseMove);
    canvas.addEventListener("mouseleave", _healthChartMouseLeave);
}

function _healthChartMouseLeave() {
    const tip = document.getElementById("health-trend-tooltip");
    if (tip) tip.style.display = "none";
}

function _healthChartMouseMove(e) {
    const g = window._healthChartGeom;
    if (!g) return;
    const tip = document.getElementById("health-trend-tooltip");
    if (!tip) return;

    const rect = e.target.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    // Find nearest data point index
    if (mouseX < g.padL || mouseX > g.padL + g.chartW) { tip.style.display = "none"; return; }
    const ratio = (mouseX - g.padL) / g.chartW;
    const idx = Math.round(ratio * (g.trend.length - 1));
    if (idx < 0 || idx >= g.trend.length) { tip.style.display = "none"; return; }

    const t = g.trend[idx];
    const s = g.series[idx];
    const total = s.healthy + s.degraded + s.unknown;
    const parts = t.day.split("-");
    const dayLabel = `${parts[2]}/${parts[1]}/${parts[0]}`;

    tip.innerHTML = `<div style="font-weight:600;margin-bottom:3px">${dayLabel}</div>
        <div><span style="color:#15803d">&#9679;</span> Healthy: ${s.healthy}</div>
        <div><span style="color:#b91c1c">&#9679;</span> Degraded: ${s.degraded}</div>
        <div><span style="color:#64748b">&#9679;</span> Unknown: ${s.unknown}</div>
        <div style="border-top:1px solid rgba(255,255,255,0.15);margin-top:3px;padding-top:3px;color:var(--text-dim)">Total: ${total}</div>`;
    tip.style.display = "block";

    // Position tooltip near cursor
    const tipX = Math.min(mouseX + 12, rect.width - 140);
    const tipY = Math.max(mouseY - 70, 0);
    tip.style.left = tipX + "px";
    tip.style.top = tipY + "px";

    // Draw vertical guideline
    const canvas = e.target;
    const ctx = canvas.getContext("2d");
    // Redraw chart then overlay guideline
}

async function renderDashboardAlerts() {
    const alerts = await api("/api/alerts?active_only=true");
    const container = $("#alerts-preview");
    if (!container) return;

    if (alerts.length === 0) {
        container.innerHTML = `<h2>Recent Alerts</h2><div class="empty-state">No active alerts</div>`;
        return;
    }

    container.innerHTML = `
        <h2>Recent Alerts</h2>
        <div class="alert-list">
            ${alerts.slice(0, 8).map(a => {
                const srcShort = a.source_name ? shortNameFromPath(a.source_name) : "";
                return `<div class="alert-item">
                    <div class="dot dot-red"></div>
                    <span>${srcShort ? `<strong>${esc(srcShort)}</strong>  - ` : ""}${esc(a.message)}</span>
                    <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${timeAgo(a.created_at)}</span>
                </div>`;
            }).join("")}
        </div>
    `;
}

async function renderSources() {
    const showArchived = _isShowingArchived("sources");
    const [sources, options, ownerSuggestions] = await Promise.all([
        api("/api/sources" + (showArchived ? "?include_archived=true" : "")),
        api("/api/create/options"),
        api("/api/sources/owner-suggestions"),
    ]);
    const people = options.people || [];

    sources.forEach(s => {
        const parsed = parseSourceName(s);
        s._shortName = parsed.shortName;
        s._folderSchema = parsed.folderSchema;
        s._fullLocation = parsed.fullLocation;
    });

    const cols = [
        { key: "type", label: "Type", width: COL_W.sm, render: s => typeBadge(s.type) },
        { key: "_shortName", label: "File / Table", width: COL_W.lg, render: s => `<strong>${esc(s._shortName)}</strong>`, sortVal: s => s._shortName || "" },
        { key: "_folderSchema", label: "Folder / Schema", width: COL_W.md, render: s => `<span style="color:var(--text-muted);font-size:0.75rem">${s._folderSchema || "-"}</span>`, sortVal: s => s._folderSchema || "" },
        { key: "status", label: "Status", width: COL_W.sm, render: s => {
            let b = statusBadge(s.status);
            if (sourceHasFreshnessRule(s)) b += ' <span style="font-size:0.65rem;color:var(--blue)" title="Freshness rule active">*</span>';
            return b;
        }, sortVal: s => ({ fresh: "0_healthy", stale: "1_degraded", outdated: "1_degraded", unknown: "2_unknown", no_connection: "2_no_connection", no_rule: "2_no_rule" })[s.status] ?? "3_" + s.status },
        { key: "last_updated", label: "Last Updated", width: COL_W.md, render: s => `<span style="color:var(--text-muted)" title="${s.last_updated || ''}">${s.last_updated ? timeAgo(s.last_updated) : "-"}</span>`, sortVal: s => s.last_updated || "" },
        { key: "row_count", label: "Rows", width: COL_W.sm, render: s => rowCountHtml(s.row_count), sortVal: s => s.row_count == null ? Number.MAX_SAFE_INTEGER : s.row_count, filterVal: s => s.row_count == null ? "unknown" : String(s.row_count) },
        { key: "freshness_rule_type", label: "Freshness", width: COL_W.md, render: s => {
            if (!sourceHasFreshnessRule(s)) {
                return '<span style="color:var(--yellow)" title="No freshness rule set">no rule</span>';
            }
            const unusual = Number(s.custom_fresh_days || 0) > 90;
            return `<span style="color:${unusual ? 'var(--yellow)' : 'var(--text-muted)'}"${unusual ? ' title="Unusually long threshold. Review whether this source is still active."' : ''}>${esc(freshnessRuleLabel(s))}${unusual ? ' - review' : ''}</span>`;
        }, sortVal: s => freshnessRuleLabel(s) },
        { key: "age_days", label: "Age (days)", width: COL_W.sm, render: s => {
            const d = daysOld(s.last_updated);
            if (d === null) return '<span style="color:var(--yellow)">no update</span>';
            if (!sourceHasFreshnessRule(s)) {
                return `<span style="color:var(--yellow);font-weight:600">${d}</span>`;
            }
            const color = s.status === "fresh" ? "var(--green)" : (s.status === "outdated" || s.status === "stale" || s.status === "error") ? "var(--red)" : "var(--yellow)";
            return `<span style="color:${color};font-weight:600">${d}</span>`;
        }, sortVal: s => daysOld(s.last_updated) ?? 9999 },
        { key: "report_count", label: "Reports", width: COL_W.sm, sortVal: s => s.report_count || 0 },
        { key: "views_30d", label: "Views last 30d", width: COL_W.sm, render: s => {
            if (!s.views_30d) return '<span style="color:var(--text-dim)">-</span>';
            const title = s.unique_users_30d ? `${s.views_30d} views by ${s.unique_users_30d} user${s.unique_users_30d !== 1 ? 's' : ''}` : `${s.views_30d} views`;
            return `<span style="cursor:help" title="${title}">${fmtInt(s.views_30d)}</span>`;
        }, sortVal: s => s.views_30d || 0 },
        { key: "owner", label: "Owner", width: COL_W.md, render: s => {
            const knownOwner = people.some(p => p.name === s.owner);
            const currentOption = s.owner && !knownOwner
                ? `<option value="${esc(s.owner)}" selected>${esc(s.owner)} (not in People)</option>`
                : "";
            const opts = people.map(p => `<option value="${esc(p.name)}"${s.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
            return `<select class="freq-select-inline source-owner-select" data-source-id="${s.id}" aria-label="Owner for ${esc(s._shortName)}"><option value="">--</option>${currentOption}${opts}</select>`;
        }, sortVal: s => s.owner || "" },
        { key: "linked_scripts", label: "Scripts", width: COL_W.sm, render: s => {
            if (!s.linked_scripts) return '-';
            return `<span class="badge badge-blue" title="${esc(s.linked_scripts)}" style="cursor:help">python</span>`;
        }, sortVal: s => s.linked_scripts ? "0_yes" : "1_no", filterVal: s => s.linked_scripts ? `yes has script ${s.linked_scripts}` : "no script" },
        _archiveColDef("source"),
    ];

    const active = sources.filter(s => !s.archived);
    const healthy = active.filter(s => s.status === "fresh").length;
    const degradedCount = active.filter(s => s.status === "outdated" || s.status === "stale").length;

    const scriptCount = sources.filter(s => !s.archived && s.linked_scripts).length;
    const excelCount = sources.filter(s => !s.archived && (s.type === "excel" || s.type === "csv")).length;
    const pgCount = sources.filter(s => !s.archived && s.type === "postgresql").length;
    const unhealthyCount = degradedCount;
    const missingOwnerCount = active.filter(s => !String(s.owner || "").trim()).length;
    const missingRuleCount = active.filter(s => !sourceHasFreshnessRule(s)).length;
    const unusualRuleCount = active.filter(s => Number(s.custom_fresh_days || 0) > 90).length;
    const ownershipRows = ownerSuggestions.map(item => {
        const evidence = item.candidates.length
            ? item.candidates.map(candidate => `${candidate.owner}: ${candidate.report_count}`).join(", ")
            : "No linked report has an owner";
        const result = item.state === "suggested"
            ? `<strong>${esc(item.owner)}</strong> <span class="badge badge-green">${Math.round(item.confidence * 100)}%</span>`
            : item.state === "tie"
                ? '<span class="badge badge-yellow">Tie - review</span>'
                : '<span class="badge badge-muted">No evidence</span>';
        return `<tr><td>${esc(shortNameFromPath(item.source_name))}</td><td>${result}</td><td>${esc(evidence)}</td></tr>`;
    }).join("");

    return `
        <div class="sources-wide-section">
            <div class="page-header">
                <h1>Sources</h1>
                <span class="subtitle">${active.length} data sources tracked - ${healthy} healthy, ${degradedCount} degraded${unusualRuleCount ? `, ${unusualRuleCount} long rule${unusualRuleCount === 1 ? "" : "s"} to review` : ""}</span>
                ${_archiveToggleHtml("sources")}
                <button class="btn-export" onclick="exportTableCSV('dt-sources','sources.csv')">Export CSV</button>
            </div>
            <div class="source-toolbar">
                <div class="source-filters">
                    <button class="btn-sm source-filter-btn" data-filter="all">All (${active.length})</button>
                    <button class="btn-sm btn-outline source-filter-btn" data-filter="excel">Excel/CSV (${excelCount})</button>
                    <button class="btn-sm btn-outline source-filter-btn" data-filter="postgresql">PostgreSQL (${pgCount})</button>
                    <button class="btn-sm btn-outline source-filter-btn" data-filter="has-script">Has Script (${scriptCount})</button>
                    <button class="btn-sm btn-outline source-filter-btn" data-filter="unhealthy">Not Healthy (${unhealthyCount})</button>
                </div>
                <div class="source-bulk-actions" aria-label="Fill missing source information">
                    <button class="btn-sm btn-outline" id="btn-auto-source-owners" ${missingOwnerCount ? "" : "disabled"} title="Assign each unowned source to the most common owner among its linked reports. Ties are skipped.">Fill missing owners (${missingOwnerCount})</button>
                    <button class="btn-sm btn-outline" id="btn-review-source-owners" ${ownerSuggestions.length ? "" : "disabled"}>Review owner evidence (${ownerSuggestions.length})</button>
                    <button class="btn-sm btn-outline" id="btn-auto-source-freshness" ${missingRuleCount ? "" : "disabled"} title="Create rules only for sources with no rule, using their saved source refresh schedule.">Set missing freshness rules (${missingRuleCount})</button>
                </div>
            </div>
            <section class="source-owner-review" id="source-owner-review" hidden>
                <div class="source-owner-review-head"><div><h2>Owner evidence</h2><p>Suggestions use only active linked reports with assigned report owners. Ties and missing evidence are never auto-assigned.</p></div><button class="btn-sm btn-outline" id="btn-close-owner-review">Close</button></div>
                <div class="table-scroll"><table class="mini-table"><thead><tr><th>Source</th><th>Suggested owner</th><th>Linked report evidence</th></tr></thead><tbody>${ownershipRows}</tbody></table></div>
            </section>
            ${dataTable("dt-sources", cols, sources, { onRowClick: showSourceDetail })}
        </div>
    `;
}

function bindSourcesPage() {
    const reviewPanel = document.getElementById("source-owner-review");
    document.getElementById("btn-review-source-owners")?.addEventListener("click", () => {
        reviewPanel.hidden = false;
        reviewPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    document.getElementById("btn-close-owner-review")?.addEventListener("click", () => { reviewPanel.hidden = true; });
    // Source filter buttons
    document.querySelectorAll(".source-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const filter = btn.dataset.filter;
            const dt = window._dt["dt-sources"];
            if (!dt) return;

            // Reset all column filters
            for (const k in dt.filters) dt.filters[k] = "";
            // Clear all filter inputs
            document.querySelectorAll('tr.filter-row input[data-dt="dt-sources"]').forEach(inp => { inp.value = ""; });

            if (filter === "excel") {
                dt.filters["type"] = "excel|csv";
            } else if (filter === "postgresql") {
                dt.filters["type"] = "postgresql";
            } else if (filter === "has-script") {
                dt.filters["linked_scripts"] = "yes";
            } else if (filter === "unhealthy") {
                dt.filters["status"] = "outdated|degraded|unknown";
            }
            // else "all" - no filter

            // Sync filter inputs with applied filter
            for (const [col, val] of Object.entries(dt.filters)) {
                if (!val) continue;
                const inp = document.querySelector(`tr.filter-row input[data-dt="dt-sources"][data-fcol="${col}"]`);
                if (inp) inp.value = val;
            }

            // Update button styles
            document.querySelectorAll(".source-filter-btn").forEach(b => {
                b.classList.toggle("btn-outline", b !== btn);
            });

            _saveDTState("dt-sources");
            _refreshDT("dt-sources");
        });
    });

    // Delegate owner edits to the table so sorting and filtering cannot remove
    // the handler when the table body is rebuilt.
    const sourcesTable = document.getElementById("dt-sources");
    if (sourcesTable) {
        sourcesTable.addEventListener("focusin", (e) => {
            const sel = e.target.closest(".source-owner-select");
            if (sel) sel.dataset.previousValue = sel.value;
        });
        sourcesTable.addEventListener("change", async (e) => {
            const sel = e.target.closest(".source-owner-select");
            if (!sel) return;
            const sourceId = Number(sel.dataset.sourceId);
            const previousValue = sel.dataset.previousValue || "";
            sel.disabled = true;
            try {
                const updated = await apiPatch(`/api/sources/${sourceId}`, { owner: sel.value || null });
                const source = window._dt?.["dt-sources"]?.rows.find(item => item.id === sourceId);
                if (source) source.owner = updated.owner;
                sel.dataset.previousValue = updated.owner || "";
                toast("Owner updated");
            } catch (err) {
                sel.value = previousValue;
                toast("Owner was not saved: " + err.message);
            } finally {
                sel.disabled = false;
            }
        });
    }

    const autoOwnerBtn = document.getElementById("btn-auto-source-owners");
    if (autoOwnerBtn) {
        autoOwnerBtn.addEventListener("click", async () => {
            const original = autoOwnerBtn.textContent;
            autoOwnerBtn.disabled = true;
            autoOwnerBtn.textContent = "Filling owners...";
            try {
                const result = await apiPost("/api/sources/auto-assign-owners");
                const skipped = result.skipped_ties + result.skipped_no_report_owner;
                toast(`Assigned ${result.assigned} source owner${result.assigned === 1 ? "" : "s"}${skipped ? `; skipped ${skipped}` : ""}`);
                await navigate("sources");
            } catch (err) {
                autoOwnerBtn.disabled = false;
                autoOwnerBtn.textContent = original;
                toast("Owners were not filled: " + err.message);
            }
        });
    }

    const autoFreshnessBtn = document.getElementById("btn-auto-source-freshness");
    if (autoFreshnessBtn) {
        autoFreshnessBtn.addEventListener("click", async () => {
            const original = autoFreshnessBtn.textContent;
            autoFreshnessBtn.disabled = true;
            autoFreshnessBtn.textContent = "Setting rules...";
            try {
                const result = await apiPost("/api/sources/auto-set-freshness-rules");
                toast(`Set ${result.configured} freshness rule${result.configured === 1 ? "" : "s"}${result.skipped_unsupported_schedule ? `; skipped ${result.skipped_unsupported_schedule}` : ""}`);
                await navigate("sources");
            } catch (err) {
                autoFreshnessBtn.disabled = false;
                autoFreshnessBtn.textContent = original;
                toast("Freshness rules were not set: " + err.message);
            }
        });
    }
    // Click-to-copy on full location paths
    document.querySelectorAll(".cell-copyable").forEach(el => {
        el.addEventListener("click", (e) => {
            e.stopPropagation();
            const path = el.dataset.copy;
            if (!path || path === "-") return;
            navigator.clipboard.writeText(path).then(() => {
                toast("Path copied to clipboard");
            }).catch(() => {
                toast("Failed to copy path");
            });
        });
    });
    _bindArchiveButtons(() => navigate("sources"));
}

function _freqDetailOpts(type, selected) {
    const days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
    const monthDays = [...Array.from({length: 31}, (_, i) => String(i + 1)), "First working day", "Last working day"];
    let items = [];
    if (type === "Weekly") items = days;
    else if (type === "Monthly") items = monthDays;
    else return '<option value="">--</option>';
    return items.map(d => `<option value="${d}"${d === selected ? " selected" : ""}>${d}</option>`).join("");
}

function _reportDocPct(reportId, docMap) {
    const doc = docMap.get(reportId);
    if (!doc) return 0;
    const fields = [doc.business_purpose, doc.business_audience, doc.technical_transformations];
    return Math.round((fields.filter(field => field && field.trim()).length / fields.length) * 100);
}

function _compactPbiSchedule(value) {
    if (!value) return "-";
    let text = String(value).trim();
    try {
        const parsed = JSON.parse(text);
        if (Array.isArray(parsed)) text = parsed.join(", ");
        else if (parsed && typeof parsed === "object") {
            const days = parsed.days || parsed.weekdays || parsed.schedule_days || [];
            const time = parsed.time || parsed.refresh_time || "";
            text = `${Array.isArray(days) ? days.join(", ") : days}${time ? ` at ${time}` : ""}`;
        }
    } catch (_) {}
    return text
        .replace(/Monday,?\s*Tuesday,?\s*Wednesday,?\s*Thursday,?\s*Friday,?\s*Saturday,?\s*Sunday/gi, "Daily")
        .replace(/Monday,?\s*Tuesday,?\s*Wednesday,?\s*Thursday,?\s*Friday/gi, "Weekdays")
        .replace(/\bat\s+/i, "@ ");
}

async function renderReports() {
    const showArchived = _isShowingArchived("reports");
    const [reports, edges, sources, options, allDocs] = await Promise.all([
        api("/api/reports" + (showArchived ? "?include_archived=true" : "")),
        api("/api/lineage"),
        api("/api/sources"),
        api("/api/create/options"),
        api("/api/documentation").catch(() => []),
    ]);
    const people = options.people || [];

    // Build report_id -> doc map for Doc % column
    const docMap = new Map();
    allDocs.forEach(d => { if (d.report_id) docMap.set(d.report_id, d); });
    window._reportPageDocs = docMap;

    const cols = [
        { key: "name", label: "Report", width: COL_W.lg, render: r => r.powerbi_url
            ? `<strong><a href="${r.powerbi_url}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="color:inherit;text-decoration:none;border-bottom:1px dotted var(--text-dim)">${esc(r.name)}</a></strong>`
            : `<strong>${esc(r.name)}</strong>` },
        { key: "status", label: "Data health", width: COL_W.sm, render: r => statusBadge(r.status), sortVal: r => ({ healthy: "0_healthy", degraded: "1_degraded" })[r.status] ?? "2_" + r.status },
        { key: "source_count", label: "Sources", width: COL_W.sm, sortVal: r => r.source_count || 0 },
        { key: "_doc_pct", label: "Doc %", width: COL_W.sm, filterable: false, render: r => {
            const pct = _reportDocPct(r.id, docMap);
            const cls = pct === 100 ? 'badge-green' : pct >= 50 ? 'badge-yellow' : 'badge-muted';
            return `<span class="badge ${cls}">${pct}%</span>`;
        }, sortVal: r => _reportDocPct(r.id, docMap) },
        { key: "_governance", label: "Governance", width: COL_W.md, render: r => {
            const gaps = [];
            if (!r.owner) gaps.push("report owner");
            if (!r.business_owner) gaps.push("business owner");
            if (_reportDocPct(r.id, docMap) < 100) gaps.push("documentation");
            return gaps.length
                ? `<span class="badge badge-yellow" title="Missing ${esc(gaps.join(', '))}">Needs ${gaps.length} item${gaps.length === 1 ? "" : "s"}</span>`
                : '<span class="badge badge-green">Complete</span>';
        }, sortVal: r => Number(!r.owner) + Number(!r.business_owner) + Number(_reportDocPct(r.id, docMap) < 100) },
        { key: "views_30d", label: "Views last 30d", width: COL_W.sm, render: r => {
            if (!r.views_30d) return '<span style="color:var(--text-dim)">-</span>';
            const title = r.unique_users_30d ? `${r.views_30d} views by ${r.unique_users_30d} user${r.unique_users_30d !== 1 ? 's' : ''}` : `${r.views_30d} views`;
            return `<span style="cursor:help" title="${title}">${fmtInt(r.views_30d)}</span>`;
        }, sortVal: r => r.views_30d || 0 },
        { key: "owner", label: "Report Owner", width: COL_W.md, render: r => {
            const biFirst = [...people].sort((a, b) => a.role === "BI" && b.role !== "BI" ? -1 : a.role !== "BI" && b.role === "BI" ? 1 : 0);
            const opts = biFirst.map(p => `<option value="${esc(p.name)}"${r.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
            return `<select class="freq-select-inline report-owner-select" data-report-id="${r.id}"><option value="">--</option>${opts}</select>`;
        }, sortVal: r => r.owner || "" },
        { key: "business_owner", label: "Business Owner", width: COL_W.md, render: r => {
            const bizFirst = [...people].sort((a, b) => a.role === "Business" && b.role !== "Business" ? -1 : a.role !== "Business" && b.role === "Business" ? 1 : 0);
            const opts = bizFirst.map(p => `<option value="${esc(p.name)}"${r.business_owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
            return `<select class="freq-select-inline report-bo-select" data-report-id="${r.id}"><option value="">--</option>${opts}</select>`;
        }, sortVal: r => r.business_owner || "" },
        { key: "pbi_refresh_schedule", label: "PBI Schedule", width: COL_W.md, render: r => r.pbi_refresh_schedule ? `<span style="font-size:0.78rem" title="${esc(r.pbi_refresh_schedule)}">${esc(_compactPbiSchedule(r.pbi_refresh_schedule))}</span>` : '-' },
        { key: "pbi_last_refresh_at", label: "Last Refresh", width: COL_W.md, render: r => r.pbi_last_refresh_at ? `<span title="${formatDate(r.pbi_last_refresh_at)}">${timeAgo(r.pbi_last_refresh_at)}</span>` : '-' },
        { key: "_lineage", label: "Lineage", width: COL_W.xs, filterable: false, sortable: false, render: r =>
            `<button class="btn-table-link btn-lineage" data-lineage-report="${r.id}" title="View lineage diagram" onclick="event.stopPropagation()">View</button>` },
        _archiveColDef("report"),
    ];

    const active = reports.filter(r => !r.archived);
    const healthy = active.filter(r => r.status === "healthy").length;
    const atRisk = active.filter(r => r.status !== "healthy" && r.status !== "unknown").length;
    const overdue = active.filter(r => _isPbiOverdue(r)).length;
    const unknown = active.filter(r => r.status === "unknown").length;
    const governanceGaps = active.filter(r => !r.owner || !r.business_owner || _reportDocPct(r.id, docMap) < 100).length;
    window._incompleteReportDocCount = active.filter(r => _reportDocPct(r.id, docMap) < 100).length;

    // Store sources for inline expansion lookups
    window._reportPageSources = sources;

    return `
        <div class="reports-wide-section">
            <div class="page-header">
                <h1>Reports</h1>
                <span class="subtitle">${active.length} Power BI reports - data: ${healthy} healthy, ${atRisk} need attention, ${unknown} unknown - governance: ${governanceGaps} incomplete${overdue ? ` - <span style="color:var(--red)">${overdue} overdue</span>` : ''}</span>
                ${_archiveToggleHtml("reports")}
                <button class="btn-outline" id="btn-pbi-sync" style="font-size:0.78rem">Sync PBI</button>
                <button class="btn-outline" id="btn-pbi-usage-sync" style="font-size:0.78rem">Sync Usage</button>
                <span class="info-tip-wrap"><span class="info-tip-icon">?</span><span class="info-tip-box">PBI Status checks if a report's last refresh matches its schedule cadence.<br><br><strong>Overdue thresholds</strong><br>Daily (7/week): 2 days<br>Business days (5/week): ~2.5 days<br>3x/week: ~3.5 days<br>2x/week: ~4.5 days<br>Weekly (1/week): 8 days<br><br>Overdue reports generate alerts automatically.</span></span>
                <button class="btn-outline" id="btn-generate-all-docs" style="font-size:0.78rem" ${window._incompleteReportDocCount ? "" : "disabled"}>Generate Missing Docs (${window._incompleteReportDocCount})</button>
                <button class="btn-export" onclick="exportTableCSV('dt-reports','reports.csv')">Export CSV</button>
            </div>

            ${dataTable("dt-reports", cols, reports, { onRowClick: showReportDetail })}
        </div>
    `;
}

function bindReportsPage() {
    // Delegate owner edits so sorting or filtering the table cannot detach the
    // controls while a user is working in a dropdown.
    const reportsTable = document.getElementById("dt-reports");
    if (reportsTable) {
        reportsTable.addEventListener("focusin", e => {
            const select = e.target.closest(".report-owner-select, .report-bo-select");
            if (select) select.dataset.previousValue = select.value;
        });
        reportsTable.addEventListener("click", e => {
            if (e.target.closest(".report-owner-select, .report-bo-select")) e.stopPropagation();
        });
        reportsTable.addEventListener("change", async e => {
            const select = e.target.closest(".report-owner-select, .report-bo-select");
            if (!select) return;
            e.stopPropagation();
            const reportId = Number(select.dataset.reportId);
            const field = select.classList.contains("report-bo-select") ? "business_owner" : "owner";
            const previousValue = select.dataset.previousValue || "";
            select.disabled = true;
            try {
                const updated = await apiPatch(`/api/reports/${reportId}`, { [field]: select.value || null });
                const report = window._dt?.["dt-reports"]?.rows.find(item => item.id === reportId);
                if (report) report[field] = updated[field];
                select.dataset.previousValue = updated[field] || "";
                toast(field === "business_owner" ? "Business owner updated" : "Report owner updated");
            } catch (err) {
                select.value = previousValue;
                toast("Owner was not saved: " + err.message);
            } finally {
                select.disabled = false;
            }
        });
    }
    // Lineage button: navigate to Lineage tab with report pre-selected
    document.querySelectorAll(".btn-lineage[data-lineage-report]").forEach(btn => {
        btn.addEventListener("click", async () => {
            const reportId = btn.dataset.lineageReport;
            await navigate("lineage");
            const sel = document.getElementById("lineage-report-select");
            if (sel) {
                sel.value = reportId;
                sel.dispatchEvent(new Event("change"));
            }
        });
    });
    _bindArchiveButtons(() => navigate("reports"));

    // Sync PBI button - launches PS1 in user's session, then polls for results
    const btnPbi = document.getElementById("btn-pbi-sync");
    if (btnPbi) {
        btnPbi.addEventListener("click", async () => {
            btnPbi.disabled = true;
            btnPbi.textContent = "Launching...";
            try {
                const result = await apiPost("/api/scanner/pbi-sync");
                if (result.status === "launched") {
                    toast(result.message || "PBI sync launched");
                    btnPbi.textContent = "Waiting for sync...";
                    // Poll until the PS1 script POSTs back (check updated_at on reports)
                    let attempts = 0;
                    const poll = setInterval(async () => {
                        attempts++;
                        if (attempts > 60) { // 2 minutes max
                            clearInterval(poll);
                            btnPbi.disabled = false;
                            btnPbi.textContent = "Sync PBI";
                            return;
                        }
                        try {
                            const reports = await api("/api/reports");
                            const hasPbi = reports.some(r => r.pbi_refresh_status);
                            if (hasPbi && attempts > 3) {
                                clearInterval(poll);
                                toast("PBI sync complete - refreshing reports");
                                btnPbi.disabled = false;
                                btnPbi.textContent = "Sync PBI";
                                navigate("reports");
                            }
                        } catch (_) {}
                    }, 2000);
                } else {
                    toast("PBI sync: " + (result.message || result.status));
                    btnPbi.disabled = false;
                    btnPbi.textContent = "Sync PBI";
                }
            } catch (err) {
                toast("PBI sync failed: " + err.message);
                btnPbi.disabled = false;
                btnPbi.textContent = "Sync PBI";
            }
        });
    }

    // Generate All Docs button
    const btnGenDocs = document.getElementById("btn-generate-all-docs");
    if (btnGenDocs) {
        btnGenDocs.addEventListener("click", async () => {
            if (!confirm(`Use AI to generate documentation for ${window._incompleteReportDocCount || 0} report${window._incompleteReportDocCount === 1 ? "" : "s"} with incomplete docs?\n\nExisting completed documentation is left unchanged. This may take a few minutes.`)) return;
            btnGenDocs.disabled = true;
            btnGenDocs.textContent = "Generating...";
            try {
                const result = await apiPost("/api/documentation/ai-suggest-all");
                const msg = `Done: ${result.generated} generated, ${result.skipped} skipped, ${result.failed} failed`;
                toast(msg);
                if (result.errors && result.errors.length > 0) {
                    console.warn("AI doc generation errors:", result.errors);
                }
                if (result.generated > 0) {
                    navigate("reports");
                }
            } catch (err) {
                toast("Generation failed: " + err.message);
            } finally {
                btnGenDocs.disabled = false;
                btnGenDocs.textContent = `Generate Missing Docs (${window._incompleteReportDocCount || 0})`;
            }
        });
    }

    // Sync Usage button
    const btnUsage = document.getElementById("btn-pbi-usage-sync");
    if (btnUsage) {
        btnUsage.addEventListener("click", async () => {
            btnUsage.disabled = true;
            btnUsage.textContent = "Launching...";
            try {
                const result = await apiPost("/api/scanner/pbi-usage-sync");
                if (result.status === "launched") {
                    toast(result.message || "Usage sync launched");
                    btnUsage.textContent = "Syncing...";
                    let attempts = 0;
                    const poll = setInterval(async () => {
                        attempts++;
                        if (attempts > 90) {
                            clearInterval(poll);
                            btnUsage.disabled = false;
                            btnUsage.textContent = "Sync Usage";
                            return;
                        }
                        try {
                            const days = await api("/api/scanner/pbi-usage-days");
                            if (days.length > 0 && attempts > 5) {
                                clearInterval(poll);
                                toast("Usage sync complete - refreshing");
                                btnUsage.disabled = false;
                                btnUsage.textContent = "Sync Usage";
                                navigate("reports");
                            }
                        } catch (_) {}
                    }, 2000);
                } else if (result.status === "completed" || result.status === "unchanged") {
                    toast(result.message || "Usage sync complete");
                    btnUsage.disabled = false;
                    btnUsage.textContent = "Sync Usage";
                    navigate("reports");
                } else {
                    toast("Usage sync: " + (result.message || result.status));
                    btnUsage.disabled = false;
                    btnUsage.textContent = "Sync Usage";
                }
            } catch (err) {
                toast("Usage sync failed: " + err.message);
                btnUsage.disabled = false;
                btnUsage.textContent = "Sync Usage";
            }
        });
    }
}

function _pbiDeviceFlowHtml(flow) {
    if (!flow) return "";
    if (flow.status === "pending") {
        return `
            <div style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 0.9rem;margin:0.6rem 0">
                <div style="font-size:0.82rem;margin-bottom:0.45rem">Sign in from <strong>any device</strong> (your laptop or phone works - the server desktop is not needed):</div>
                <div style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap">
                    <a href="${esc(flow.verification_uri || "https://microsoft.com/devicelogin")}" target="_blank" rel="noopener">${esc(flow.verification_uri || "https://microsoft.com/devicelogin")}</a>
                    <code style="font-size:1.05rem;letter-spacing:0.12em;padding:0.2rem 0.55rem;border:1px solid var(--border);border-radius:4px">${esc(flow.user_code || "")}</code>
                    <button class="btn-outline" style="font-size:0.72rem" onclick="navigator.clipboard && navigator.clipboard.writeText('${esc(flow.user_code || "")}')">Copy code</button>
                    <span id="pbi-flow-note" style="color:var(--text-dim);font-size:0.78rem">Waiting for the sign-in to complete...</span>
                </div>
            </div>`;
    }
    if (flow.status === "failed" || flow.status === "expired") {
        return `<div class="pbi-sync-warning">${esc(flow.message || "Sign-in did not complete.")}</div>`;
    }
    return "";
}

function _watchPbiDeviceFlow() {
    if (window._pbiFlowPoll) clearInterval(window._pbiFlowPoll);
    window._pbiFlowPoll = setInterval(async () => {
        const box = document.getElementById("pbi-connect-box");
        if (!box) { clearInterval(window._pbiFlowPoll); return; }
        try {
            const status = await api("/api/scanner/pbi-auth/status");
            const flow = status.device_flow;
            if (!flow || flow.status === "pending") return;
            clearInterval(window._pbiFlowPoll);
            if (flow.status === "completed") {
                toast(`Power BI connected as ${status.account || "saved account"} - syncs now run headless`);
            } else {
                toast(flow.message || "Power BI sign-in did not complete");
            }
            navigate("scanner");
        } catch (_) {}
    }, 4000);
}

async function renderScanner() {
    const [runs, probeRuns, pbiStatus] = await Promise.all([
        api("/api/scanner/runs"),
        api("/api/scanner/probe/runs"),
        api("/api/scanner/pbi-sync/status").catch(() => null),
    ]);
    const lastRun = runs.length > 0 ? runs[0] : null;
    const lastProbe = probeRuns.length > 0 ? probeRuns[0] : null;
    const refreshStatus = pbiStatus?.refresh || {};
    const latestPbiSuccess = refreshStatus.latest_success;
    const latestPbiAttempt = refreshStatus.latest_attempt;
    const pbiFresh = refreshStatus.freshness?.fresh;
    const rdpGuard = pbiStatus?.rdp_guard;
    const pendingPbiSync = pbiStatus?.pending;
    const auth = pbiStatus?.auth || {};
    const authMode = pbiStatus?.auth_mode || "interactive";
    const isInteractiveMode = authMode === "interactive";
    const modeLabel = authMode === "service_principal" ? "Service principal"
        : authMode === "cached_account" ? "Saved Microsoft account (headless)"
        : "Interactive window (fallback)";
    const latestPbiAttemptMessage = latestPbiAttempt?.message || "";
    const pbiAttemptWarning = ["failed", "stopped", "pending"].includes(latestPbiAttempt?.status) && latestPbiAttemptMessage
        ? `<div class="pbi-sync-warning">${esc(latestPbiAttemptMessage)}</div>`
        : "";
    const pendingPbiWarning = pendingPbiSync
        ? `<div class="pbi-sync-warning">Pending Power BI sync: ${esc(pendingPbiSync.message || "waiting for an interactive desktop")}</div>`
        : "";
    const rdpGuardWarning = isInteractiveMode && rdpGuard && !rdpGuard.ready
        ? `<div class="pbi-sync-warning">${esc(rdpGuard.message || "RDP console guard is not ready.")}</div>`
        : "";
    const reconnectWarning = auth.reconnect_required
        ? `<div class="pbi-sync-warning">${esc(auth.message || "The saved Power BI sign-in expired - use Connect Power BI.")}</div>`
        : "";
    const connectHint = isInteractiveMode && !auth.connected
        ? `<div class="pbi-sync-warning">Syncs currently need a visible sign-in window on the server desktop. Click Connect Power BI once to switch to headless sync that also works while the PC is locked.</div>`
        : "";
    const pbiStatusBadge = !pbiStatus
        ? '<span class="badge badge-muted">Unknown</span>'
        : pbiFresh
            ? '<span class="badge badge-green">Fresh</span>'
            : '<span class="badge badge-red">Stale</span>';
    const authButtons = authMode === "service_principal" ? "" : `
        ${!auth.connected || auth.reconnect_required ? '<button id="btn-pbi-connect" class="btn-outline" style="font-size:0.78rem">Connect Power BI</button>' : ""}
        ${auth.connected ? '<button id="btn-pbi-disconnect" class="btn-outline" style="font-size:0.78rem">Disconnect account</button>' : ""}
    `;
    const pbiSyncMeta = pbiStatus ? `
        <div class="section pbi-sync-status">
            <h2>Power BI Sync</h2>
            <div class="pbi-sync-facts">
                <span>${pbiStatusBadge}</span>
                <span>Mode: <strong>${modeLabel}</strong></span>
                ${auth.connected ? `<span>Account: <strong>${esc(auth.account || "saved account")}</strong>${auth.reconnect_required ? ' <span class="badge badge-red">Reconnect needed</span>' : ""}</span>` : ""}
                <span>Last success: <strong>${latestPbiSuccess?.finished_at ? timeAgo(latestPbiSuccess.finished_at) : "never"}</strong></span>
                <span>Last attempt: <strong>${latestPbiAttempt?.status || "none"}</strong>${latestPbiAttempt?.started_at ? ` ${timeAgo(latestPbiAttempt.started_at)}` : ""}</span>
                ${pendingPbiSync ? `<span>Pending: <strong>Yes</strong>${pendingPbiSync.updated_at ? ` ${timeAgo(pendingPbiSync.updated_at)}` : ""}</span>` : ""}
                ${isInteractiveMode && rdpGuard ? `<span>RDP guard: <strong>${rdpGuard.ready ? "Ready" : "Not ready"}</strong></span>` : ""}
                ${authButtons}
            </div>
            <div id="pbi-connect-box">${_pbiDeviceFlowHtml(auth.device_flow)}</div>
            ${refreshStatus.freshness?.reason ? `<div class="pbi-sync-warning">${esc(refreshStatus.freshness.reason)}</div>` : ""}
            ${reconnectWarning}
            ${connectHint}
            ${pbiAttemptWarning}
            ${pendingPbiWarning}
            ${rdpGuardWarning}
        </div>
    ` : "";

    return `
        <div class="page-header">
            <h1>Scanner</h1>
            <span class="subtitle">Scan Power BI reports to detect sources and track changes</span>
        </div>

        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1.25rem">
            <button id="btn-scan">Run Scan Now</button>
            <button id="btn-probe" class="btn-outline">Probe Sources</button>
            <button id="btn-diagnose" class="btn-outline">Diagnose</button>
            <button id="btn-stop-pbi-sync" class="btn-outline btn-danger-outline">Stop Refresh Work</button>
            <span style="color:var(--text-dim);font-size:0.78rem">
                ${lastRun ? `Last scan: ${timeAgo(lastRun.started_at)}` : "No scans yet"}
                ${lastProbe ? ` · Last probe: ${timeAgo(lastProbe.started_at)}` : ""}
            </span>
        </div>

        <div id="diagnose-panel" style="display:none"></div>

        ${pbiSyncMeta}

        ${lastRun && lastRun.log ? `
            <div class="section">
                <h2 class="log-toggle" data-target="scan-log-body" style="cursor:pointer;user-select:none">Last Scan Log <span style="font-size:0.72rem;font-weight:400;color:var(--text-dim)"> - click to expand</span></h2>
                <div id="scan-log-body" class="scan-log" style="display:none">${lastRun.log}</div>
            </div>
        ` : ""}

        ${lastProbe && lastProbe.log ? `
            <div class="section">
                <h2 class="log-toggle" data-target="probe-log-body" style="cursor:pointer;user-select:none">Last Probe Log <span style="font-size:0.72rem;font-weight:400;color:var(--text-dim)"> - click to expand</span></h2>
                <div id="probe-log-body" class="scan-log" style="display:none">${lastProbe.log}</div>
            </div>
        ` : ""}

        <div class="section-grid">
            <div class="section">
                <h2>Scan History</h2>
                ${dataTable("dt-scans", [
                    { key: "started_at", label: "When", width: COL_W.md, render: r => `<span title="${formatDate(r.started_at)}">${timeAgo(r.started_at)}</span>`, sortVal: r => r.started_at || "" },
                    { key: "status", label: "Status", width: COL_W.sm, render: r => statusBadge(r.status) },
                    { key: "reports_scanned", label: "Reports", width: COL_W.sm, render: r => `${r.reports_scanned ?? "-"}`, sortVal: r => r.reports_scanned ?? 0 },
                    { key: "sources_found", label: "Sources", width: COL_W.sm, render: r => `${r.sources_found ?? "-"}`, sortVal: r => r.sources_found ?? 0 },
                    { key: "new_sources", label: "New", width: COL_W.sm, render: r => r.new_sources ? `<span style="color:var(--green)">+${r.new_sources}</span>` : '-', sortVal: r => r.new_sources ?? 0 },
                ], runs)}
            </div>
            <div class="section">
                <h2>Probe History</h2>
                ${probeRuns.length > 0 ? dataTable("dt-probes", [
                    { key: "started_at", label: "When", width: COL_W.md, render: r => `<span title="${formatDate(r.started_at)}">${timeAgo(r.started_at)}</span>`, sortVal: r => r.started_at || "" },
                    { key: "status", label: "Status", width: COL_W.sm, render: r => statusBadge(r.status) },
                    { key: "sources_probed", label: "Probed", width: COL_W.sm, render: r => `${r.sources_probed ?? "-"}` },
                    { key: "fresh", label: "Healthy", width: COL_W.sm, render: r => r.fresh ? `<span style="color:var(--green)">${r.fresh}</span>` : '-' },
                    { key: "_degraded", label: "Degraded", width: COL_W.sm, render: r => (r.stale || r.outdated) ? `<span style="color:var(--red)">${(r.stale || 0) + (r.outdated || 0)}</span>` : '-', sortVal: r => (r.stale || 0) + (r.outdated || 0) },
                    { key: "unknown", label: "Unknown", width: COL_W.sm, render: r => r.unknown ? `<span style="color:var(--text-muted)">${r.unknown}</span>` : '-' },
                    { key: "no_rule", label: "No rule", width: COL_W.sm, render: r => r.no_rule ? `<span style="color:var(--yellow)">${r.no_rule}</span>` : '-' },
                    { key: "_coverage", label: "Coverage", width: COL_W.sm, render: r => {
                        const accounted = (r.fresh || 0) + (r.stale || 0) + (r.outdated || 0) + (r.unknown || 0) + (r.no_rule || 0);
                        const complete = accounted === (r.sources_probed || 0);
                        return `<span class="badge ${complete ? 'badge-green' : 'badge-yellow'}" title="${complete ? 'Every probe result is accounted for' : 'This historical row predates complete status accounting'}">${accounted}/${r.sources_probed || 0}</span>`;
                    }, sortVal: r => (r.fresh || 0) + (r.stale || 0) + (r.outdated || 0) + (r.unknown || 0) + (r.no_rule || 0) },
                ], probeRuns) : '<div class="empty-state">No probes yet. Click "Probe Sources" to check freshness.</div>'}
            </div>
        </div>
    `;
}

async function renderAlerts() {
    const [alerts, allPeople] = await Promise.all([
        api("/api/alerts?active_only=false"),
        api("/api/people"),
    ]);
    const owners = allPeople.filter(p => p.role === "BI").map(p => p.name);
    window._alertOwners = owners;

    const cols = [
        { key: "severity", label: "Severity", width: COL_W.sm, render: a => statusBadge(a.severity), sortVal: a => ({ critical: "0_critical", warning: "1_warning" })[a.severity] ?? "2_" + a.severity },
        { key: "message", label: "Message", width: COL_W.xl, render: a => {
            const srcShort = a.source_name ? shortNameFromPath(a.source_name) : "";
            return srcShort ? `<strong>${esc(srcShort)}</strong>  - ${esc(a.message)}` : esc(a.message);
        }},
        { key: "assigned_to", label: "Owner", width: COL_W.md, render: a => {
            const opts = (window._alertOwners || []).map(o =>
                `<option value="${o}"${a.assigned_to === o ? ' selected' : ''}>${o}</option>`
            ).join("");
            return `<select class="alert-owner-select" data-alert-id="${a.id}">
                <option value="">Unassigned</option>${opts}
            </select>`;
        }, sortVal: a => a.assigned_to || "zzz_unassigned" },
        { key: "created_at", label: "When", width: COL_W.md, render: a => `<span style="color:var(--text-muted)" title="${formatDate(a.created_at)}">${timeAgo(a.created_at)}</span>`, sortVal: a => a.created_at || "" },
        { key: "resolution_status", label: "Status", width: COL_W.lg, render: a => {
            const reasonHtml = a.resolution_reason ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-top:0.2rem;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${a.resolution_reason.replace(/"/g, '&quot;')}">${a.resolution_reason}</div>` : "";
            if (a.resolution_status === "acknowledged") {
                return `<span class="badge badge-blue">acknowledged</span> <button class="btn-sm btn-outline btn-reopen-alert" data-alert-id="${a.id}">Reopen</button>${reasonHtml}`;
            }
            if (a.resolution_status === "resolved") {
                return `<span class="badge badge-green">resolved</span> <button class="btn-sm btn-outline btn-reopen-alert" data-alert-id="${a.id}">Reopen</button>${reasonHtml}`;
            }
            return `<button class="btn-sm btn-blue btn-resolve-alert" data-alert-id="${a.id}" data-action="acknowledged">Acknowledge</button> <button class="btn-sm btn-green btn-resolve-alert" data-alert-id="${a.id}" data-action="resolved">Resolve</button>`;
        }, sortVal: a => a.resolution_status ? "1_" + a.resolution_status : "0_active" },
    ];

    const active = alerts.filter(a => !a.resolution_status).length;
    const acked = alerts.filter(a => a.resolution_status === "acknowledged").length;
    const resolved = alerts.filter(a => a.resolution_status === "resolved").length;

    return { html: dataTable("dt-alerts", cols, alerts), active, acked, resolved, total: alerts.length };
}

function bindAlertsTab() {
    // Owner select dropdowns
    document.querySelectorAll(".alert-owner-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const alertId = sel.dataset.alertId;
            const owner = sel.value || "";
            try {
                const url = owner
                    ? `/api/alerts/${alertId}/assign?owner=${encodeURIComponent(owner)}`
                    : `/api/alerts/${alertId}/assign`;
                await fetch(url, { method: "PATCH", headers: apiHeaders() });
                toast(owner ? `Assigned to ${owner}` : "Unassigned");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });
    // Acknowledge / Resolve buttons
    document.querySelectorAll(".btn-resolve-alert").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const alertId = btn.dataset.alertId;
            const action = btn.dataset.action; // "acknowledged" or "resolved"
            const label = action === "resolved" ? "resolving" : "acknowledging";
            const reason = prompt(`Reason for ${label} this alert (optional):`);
            if (reason === null) return; // cancelled
            try {
                await apiPostJson(`/api/alerts/${alertId}/resolve`, { status: action, reason: reason || null });
                const cell = btn.closest("td");
                const badge = action === "resolved" ? "badge-green" : "badge-blue";
                const reasonAttr = reason ? ` title="${reason.replace(/"/g, '&quot;')}"` : "";
                if (cell) {
                    cell.innerHTML = `<span class="badge ${badge}"${reasonAttr}>${action}</span> <button class="btn-sm btn-outline btn-reopen-alert" data-alert-id="${alertId}">Reopen</button>`;
                    bindReopenButtons(cell);
                }
                toast(`Alert ${action}`);
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    });
    // Reopen buttons
    bindReopenButtons();
}

function bindReopenButtons(scope) {
    const root = scope || document;
    root.querySelectorAll(".btn-reopen-alert").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const alertId = btn.dataset.alertId;
            try {
                await apiPost(`/api/alerts/${alertId}/reopen`);
                toast("Alert reopened");
                await navigate("dashboard");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    });
}

async function renderActionsContent() {
    const actions = await api("/api/actions");
    let owners = [];
    try {
        const allPeople = await api("/api/people");
        owners = allPeople.filter(p => p.role === "BI").map(p => p.name);
    } catch(e) {}

    const open = actions.filter(a => a.status === "open").length;
    const investigating = actions.filter(a => a.status === "investigating").length;
    const acknowledged = actions.filter(a => a.status === "acknowledged").length;
    const resolved = actions.filter(a => a.status === "resolved" || a.status === "expected").length;

    const statusOptions = ["open", "acknowledged", "investigating", "expected", "resolved"];

    // Build owner options HTML for assignment dropdowns
    const ownerOptionsHtml = owners.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join("");

    function renderActionCards(filter, ownerFilter) {
        let filtered = filter === "all" ? actions : actions.filter(a => a.status === filter);
        if (ownerFilter && ownerFilter !== "all") {
            if (ownerFilter === "unassigned") {
                filtered = filtered.filter(a => !a.assigned_to);
            } else {
                filtered = filtered.filter(a => a.assigned_to === ownerFilter);
            }
        }

        if (filtered.length === 0) {
            return '<div class="empty-state">No actions match this filter</div>';
        }

        return `<div class="action-cards">${filtered.map(a => {
            const indColor = a.type.includes("outdated") || a.type.includes("error") || a.type.includes("degraded") ? "ind-red"
                           : a.type.includes("stale") || a.type.includes("at_risk") ? "ind-red"
                           : a.type.includes("broken") ? "ind-red"
                           : "ind-blue";

            const assetName = a.asset_name || a.source_name || a.report_name || "-";
            const shortAsset = shortNameFromPath(assetName) || assetName;
            const currentOwner = a.assigned_to || "";

            return `
                <div class="action-card" data-action-id="${a.id}">
                    <div class="action-indicator ${indColor}"></div>
                    <div class="action-body">
                        <div class="action-title">${shortAsset}</div>
                        <div class="action-meta">
                            ${actionTypeBadge(a.type)}
                            <span title="Views in the last 30 days">Views ${fmtInt(a.impact_views_30d || 0)}</span>
                            <select class="action-owner-select" data-action-id="${a.id}">
                                <option value=""${!currentOwner ? ' selected' : ''}>Unassigned</option>
                                ${owners.map(o => `<option value="${esc(o)}"${o === currentOwner ? ' selected' : ''}>${esc(o)}</option>`).join("")}
                            </select>
                            <span>${timeAgo(a.created_at)}</span>
                        </div>
                        ${a.notes ? `<div class="action-notes">${esc(a.notes)}</div>` : ""}
                    </div>
                    <div class="action-controls">
                        <div class="status-pill-wrapper">
                            <button class="status-pill status-${a.status}" data-action-id="${a.id}" data-current="${a.status}">${a.status} <span class="pill-chevron">&#9662;</span></button>
                            <div class="status-dropdown" data-action-id="${a.id}">
                                ${statusOptions.map(s => `<div class="status-option status-${s}${s === a.status ? ' active' : ''}" data-value="${s}">${s}</div>`).join("")}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join("")}</div>`;
    }

    // Collect unique assigned owners for filter dropdown
    const assignedOwners = [...new Set(actions.map(a => a.assigned_to).filter(Boolean))].sort();

    // Store render function for re-rendering after filter change
    window._actionsData = { actions, renderActionCards, owners };

    const html = `
        <div class="summary-counts">
            <div class="summary-count"><span class="count-num" style="color:var(--red)">${open}</span><span class="count-label">open</span></div>
            <div class="summary-count"><span class="count-num" style="color:var(--blue)">${acknowledged}</span><span class="count-label">acknowledged</span></div>
            <div class="summary-count"><span class="count-num" style="color:var(--yellow)">${investigating}</span><span class="count-label">investigating</span></div>
            <div class="summary-count"><span class="count-num" style="color:var(--green)">${resolved}</span><span class="count-label">resolved</span></div>
        </div>

        <div class="action-filters">
            <div class="action-filters-row">
                <button class="action-filter-btn active" data-filter="all">All (${actions.length})</button>
                <button class="action-filter-btn" data-filter="open">Open (${open})</button>
                <button class="action-filter-btn" data-filter="acknowledged">Acknowledged (${acknowledged})</button>
                <button class="action-filter-btn" data-filter="investigating">Investigating (${investigating})</button>
                <button class="action-filter-btn" data-filter="resolved">Resolved (${resolved})</button>
                <button class="action-filter-btn" data-filter="expected" title="Sources that are intentionally degraded (e.g. quarterly data)">Expected (${actions.filter(a => a.status === "expected").length})</button>
            </div>
            <select id="action-owner-filter" class="action-owner-filter">
                <option value="all">All owners</option>
                <option value="unassigned">Unassigned</option>
                ${assignedOwners.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join("")}
            </select>
        </div>

        <div id="action-list">
            ${renderActionCards("all", "all")}
        </div>
    `;
    return { html, open, total: actions.length };
}

function _getActiveActionFilters() {
    const activeBtn = document.querySelector(".action-filter-btn.active");
    const statusFilter = activeBtn ? activeBtn.dataset.filter : "all";
    const ownerSelect = document.getElementById("action-owner-filter");
    const ownerFilter = ownerSelect ? ownerSelect.value : "all";
    return { statusFilter, ownerFilter };
}

function _reRenderActionList() {
    const container = document.getElementById("action-list");
    if (!container || !window._actionsData) return;
    const { statusFilter, ownerFilter } = _getActiveActionFilters();
    container.innerHTML = window._actionsData.renderActionCards(statusFilter, ownerFilter);
    bindActionStatusSelects();
    bindActionOwnerSelects();
}

function bindActionsTab() {
    // Status filter buttons
    document.querySelectorAll(".action-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".action-filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _reRenderActionList();
        });
    });

    // Owner filter dropdown
    const ownerFilter = document.getElementById("action-owner-filter");
    if (ownerFilter) {
        ownerFilter.addEventListener("change", () => _reRenderActionList());
    }

    bindActionStatusSelects();
    bindActionOwnerSelects();
}

function bindIssuesPage() {
    bindAlertsTab();
}

function bindActionStatusSelects() {
    // Close any open dropdown when clicking outside (register once)
    if (!window._statusDropdownOutsideClick) {
        window._statusDropdownOutsideClick = true;
        document.addEventListener("click", (e) => {
            if (!e.target.closest(".status-pill-wrapper")) {
                document.querySelectorAll(".status-dropdown.visible").forEach(d => d.classList.remove("visible"));
                document.querySelectorAll(".status-pill.open").forEach(p => p.classList.remove("open"));
            }
        });
    }

    // Pill click → toggle dropdown
    document.querySelectorAll(".status-pill").forEach(pill => {
        pill.addEventListener("click", (e) => {
            e.stopPropagation();
            const wrapper = pill.closest(".status-pill-wrapper");
            const dropdown = wrapper.querySelector(".status-dropdown");
            const wasOpen = dropdown.classList.contains("visible");

            // Close all other dropdowns first
            document.querySelectorAll(".status-dropdown.visible").forEach(d => d.classList.remove("visible"));
            document.querySelectorAll(".status-pill.open").forEach(p => p.classList.remove("open"));

            if (!wasOpen) {
                dropdown.classList.add("visible");
                pill.classList.add("open");
            }
        });
    });

    // Option click → update status
    document.querySelectorAll(".status-option").forEach(option => {
        option.addEventListener("click", async (e) => {
            e.stopPropagation();
            const dropdown = option.closest(".status-dropdown");
            const actionId = dropdown.dataset.actionId;
            const newStatus = option.dataset.value;
            const wrapper = option.closest(".status-pill-wrapper");
            const pill = wrapper.querySelector(".status-pill");

            // Close dropdown
            dropdown.classList.remove("visible");
            pill.classList.remove("open");

            // Optimistic UI update
            pill.className = `status-pill status-${newStatus}`;
            pill.innerHTML = `${newStatus} <span class="pill-chevron">&#9662;</span>`;
            pill.dataset.current = newStatus;

            // Mark active option
            dropdown.querySelectorAll(".status-option").forEach(o => o.classList.remove("active"));
            option.classList.add("active");

            try {
                await apiPatch(`/api/actions/${actionId}`, { status: newStatus });
                toast(`Action #${actionId} updated to ${newStatus}`);
            } catch (err) {
                toast("Failed to update: " + err.message);
                navigate("dashboard");
            }
        });
    });
}


function bindActionOwnerSelects() {
    document.querySelectorAll(".action-owner-select").forEach(select => {
        select.addEventListener("change", async (e) => {
            const actionId = select.dataset.actionId;
            const newOwner = select.value || null;
            try {
                await apiPatch(`/api/actions/${actionId}`, { assigned_to: newOwner });
                // Update cached data so filters stay consistent
                if (window._actionsData) {
                    const action = window._actionsData.actions.find(a => a.id == actionId);
                    if (action) action.assigned_to = newOwner;
                }
                toast(`Action #${actionId} assigned to ${newOwner || "unassigned"}`);
            } catch (err) {
                toast("Failed to assign: " + err.message);
            }
        });
    });
}

async function renderActions() {
    const result = await renderActionsContent();
    return `
        <div class="page-header">
            <h1>Actions</h1>
            <span class="subtitle">${result.open} open of ${result.total} total</span>
        </div>
        ${result.html}
    `;
}


/** Extract a short display name from a source name string (no extension for files). */
function shortNameFromPath(fullName) {
    if (!fullName) return "";
    // Handle both forward slashes and backslashes
    const normalized = fullName.replace(/\\/g, "/");
    const lastSlash = normalized.lastIndexOf("/");
    const base = lastSlash >= 0 ? normalized.substring(lastSlash + 1) : normalized;
    // For DB-style names like "dbo.Orders", keep as-is
    if (base.includes(".") && !/\.(csv|xlsx|xls|json|parquet|txt)$/i.test(base)) return base;
    // Strip file extension
    const dot = base.lastIndexOf(".");
    return dot > 0 ? base.substring(0, dot) : base;
}

// ── Changelog ──

async function renderChangelog() {
    const entries = await api("/api/changelog");

    // Group by date (day)
    const grouped = {};
    for (const e of entries) {
        const day = e.date ? e.date.substring(0, 10) : "Unknown";
        if (!grouped[day]) grouped[day] = [];
        grouped[day].push(e);
    }

    const days = Object.keys(grouped).sort().reverse();

    const rows = days.map(day => {
        const d = new Date(day + "T00:00:00Z");
        const label = d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
        const items = grouped[day].map(e => {
            return `
                <div class="changelog-item">
                    <div class="changelog-body">
                        <div class="changelog-title">${esc(e.title)}</div>
                        <div class="changelog-desc">${esc(e.description)}</div>
                    </div>
                    <span class="changelog-commit">${esc(e.commit)}</span>
                </div>
            `;
        }).join("");
        return `
            <div class="changelog-day">
                <div class="changelog-date">${label}</div>
                ${items}
            </div>
        `;
    }).join("");

    const flowchart = `
        <div class="flowchart-wrap">
            <div class="flowchart-title">Data Governance Pipeline</div>
            <div class="flowchart-sub">Hover each step for details</div>
            <div class="fc-pipeline">
                <div class="fc-col">
                    <div class="fc-tip"><b>Data Input</b>.pbix report files and TMDL exports from the shared network folder.</div>
                    <div class="fc-ico">
                        <svg viewBox="0 0 34 34" fill="none">
                            <rect x="22" y="4" width="5" height="26" rx="2.5" fill="#F2C811"/>
                            <rect x="14.5" y="10" width="5" height="20" rx="2.5" fill="#E8A40A"/>
                            <rect x="7" y="16" width="5" height="14" rx="2.5" fill="#DA9508"/>
                        </svg>
                    </div>
                    <div class="fc-lbl">Power BI</div>
                </div>
                <div class="fc-harr"></div>
                <div class="fc-col">
                    <div class="fc-tip"><b>Python Scanner</b>Opens each report, extracts all data sources, deduplicates across reports, detects changes.</div>
                    <div class="fc-ico">
                        <svg viewBox="0 0 34 34" fill="none">
                            <path d="M16.9 4C11.5 4 11.8 6.4 11.8 6.4v2.5h5.3v.8H9.6S5.5 9.2 5.5 14.7s3.6 5.3 3.6 5.3h2.1v-2.5s-.1-3.6 3.5-3.6h5.1s3.4.1 3.4-3.3V6.9S23.7 4 16.9 4Zm-2.8 1.7c.5 0 1 .4 1 1s-.4 1-1 1c-.5 0-1-.4-1-1s.5-1 1-1Z" fill="#3776AB"/>
                            <path d="M17.1 30c5.4 0 5.1-2.4 5.1-2.4v-2.5h-5.3v-.8h7.5s4.1.5 4.1-5c0-5.5-3.6-5.3-3.6-5.3h-2.1v2.5s.1 3.6-3.5 3.6h-5.1s-3.4-.1-3.4 3.3v3.7S10.3 30 17.1 30Zm2.8-1.7c-.5 0-1-.4-1-1s.4-1 1-1 1 .4 1 1-.5 1-1 1Z" fill="#FFD43B"/>
                        </svg>
                    </div>
                    <div class="fc-lbl">Scanner</div>
                    <div class="fc-branch">
                        <div class="fc-vline"></div>
                        <div class="fc-vtag">discovers</div>
                        <div class="fc-chips">
                            <div class="fc-chip"><svg viewBox="0 0 24 24" fill="none" stroke="#86868b" stroke-width="1.5"><path d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v4.875c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375"/></svg>SQL Server</div>
                            <div class="fc-chip"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="6" stroke="#336791" stroke-width="1.5"/><path d="M12 6v12" stroke="#336791" stroke-width="1"/></svg>PostgreSQL</div>
                            <div class="fc-chip"><svg viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="16" height="16" rx="2" stroke="#217346" stroke-width="1.5"/><path d="M8 8h8M8 12h8M8 16h5" stroke="#217346" stroke-width="1"/></svg>Excel</div>
                            <div class="fc-chip"><svg viewBox="0 0 24 24" fill="none" stroke="#86868b" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>CSV</div>
                        </div>
                    </div>
                </div>
                <div class="fc-harr"></div>
                <div class="fc-col">
                    <div class="fc-tip"><b>governance.db</b>Single SQLite file. All sources, reports, lineage, alerts, actions, and scan history.</div>
                    <div class="fc-ico">
                        <svg viewBox="0 0 34 34" fill="none">
                            <ellipse cx="17" cy="9" rx="10" ry="4" stroke="#5E5CE6" stroke-width="2" fill="none"/>
                            <path d="M7 9v16c0 2.2 4.5 4 10 4s10-1.8 10-4V9" stroke="#5E5CE6" stroke-width="2" fill="none"/>
                            <path d="M7 17c0 2.2 4.5 4 10 4s10-1.8 10-4" stroke="#5E5CE6" stroke-width="1.5" fill="none" opacity="0.4"/>
                        </svg>
                    </div>
                    <div class="fc-lbl">SQLite</div>
                    <div class="fc-branch">
                        <div class="fc-vline"></div>
                        <div class="fc-vtag">stores</div>
                        <div class="fc-chips">
                            <div class="fc-chip">Sources</div>
                            <div class="fc-chip">Reports</div>
                            <div class="fc-chip">Lineage</div>
                            <div class="fc-chip">History</div>
                        </div>
                    </div>
                </div>
                <div class="fc-harr"></div>
                <div class="fc-col">
                    <div class="fc-tip"><b>Freshness Monitor</b>Checks file dates and database timestamps. Configurable thresholds per source.</div>
                    <div class="fc-ico">
                        <svg viewBox="0 0 34 34" fill="none">
                            <circle cx="17" cy="17" r="12" stroke="#34C759" stroke-width="2" fill="none"/>
                            <circle cx="17" cy="17" r="1.5" fill="#34C759"/>
                            <line x1="17" y1="17" x2="17" y2="9" stroke="#34C759" stroke-width="2" stroke-linecap="round"/>
                            <line x1="17" y1="17" x2="23" y2="20" stroke="#34C759" stroke-width="1.5" stroke-linecap="round"/>
                        </svg>
                    </div>
                    <div class="fc-lbl">Freshness</div>
                    <div class="fc-branch">
                        <div class="fc-vline"></div>
                        <div class="fc-vtag">classifies</div>
                        <div class="fc-chips">
                            <div class="fc-chip"><div class="fc-dot fc-dot-g"></div> Fresh</div>
                            <div class="fc-chip"><div class="fc-dot fc-dot-y"></div> Stale</div>
                            <div class="fc-chip"><div class="fc-dot fc-dot-r"></div> Outdated</div>
                        </div>
                    </div>
                </div>
                <div class="fc-harr"></div>
                <div class="fc-col">
                    <div class="fc-tip"><b>Alerts &amp; Actions</b>Auto-created for stale/broken sources. Assigned to report owners. Tracked to resolution.</div>
                    <div class="fc-ico">
                        <svg viewBox="0 0 34 34" fill="none">
                            <path d="M25 14a8 8 0 1 0-16 0c0 8-3.5 10-3.5 10h23S25 22 25 14Z" stroke="#FF3B30" stroke-width="2" fill="none"/>
                            <path d="M19.5 28a2.5 2.5 0 0 1-5 0" stroke="#FF3B30" stroke-width="2" fill="none"/>
                        </svg>
                    </div>
                    <div class="fc-lbl">Alerts</div>
                    <div class="fc-branch">
                        <div class="fc-vline"></div>
                        <div class="fc-vtag">workflow</div>
                        <div class="fc-chips">
                            <div class="fc-chip"><div class="fc-dot fc-dot-r"></div> Open</div>
                            <div class="fc-chip"><div class="fc-dot fc-dot-y"></div> Investigating</div>
                            <div class="fc-chip"><div class="fc-dot fc-dot-g"></div> Resolved</div>
                        </div>
                    </div>
                </div>
                <div class="fc-harr"></div>
                <div class="fc-col">
                    <div class="fc-tip"><b>Web Dashboard</b>Browser-based panel for the whole team. Health KPIs, lineage maps, alert triage, AI assistant.</div>
                    <div class="fc-ico">
                        <svg viewBox="0 0 34 34" fill="none">
                            <rect x="4" y="4" width="11" height="11" rx="2.5" stroke="#FF9500" stroke-width="2" fill="none"/>
                            <rect x="19" y="4" width="11" height="11" rx="2.5" stroke="#FF9500" stroke-width="2" fill="none"/>
                            <rect x="4" y="19" width="11" height="11" rx="2.5" stroke="#FF9500" stroke-width="2" fill="none"/>
                            <rect x="19" y="19" width="11" height="11" rx="2.5" stroke="#FF9500" stroke-width="2" fill="none"/>
                        </svg>
                    </div>
                    <div class="fc-lbl">Dashboard</div>
                </div>
            </div>
            <div class="fc-foot">governance.db is the only file to back up</div>
        </div>
    `;

    return `
        <div class="page-header">
            <h1>Changelog</h1>
            <span class="subtitle">${entries.length} updates</span>
        </div>
        ${flowchart}
        <div class="changelog-list">${rows || '<div style="color:var(--text-muted)">No changelog entries found.</div>'}</div>
    `;
}


function bindChangelogPage() {
    // Placeholder: no interactive elements currently needed
}


// ── Create Page ──

function _entityTypeBadge(type) {
    const labels = { source: "Source", report: "Report", upstream: "Upstream" };
    const colors = { source: "badge-blue", report: "badge-green", upstream: "badge-purple" };
    return `<span class="badge ${colors[type] || 'badge-muted'}">${labels[type] || type}</span>`;
}

function _renderCreateForm(entity) {
    const opts = window._createOptions;
    if (!opts) return '';
    let fields = '';
    const ownerOpts = (opts.owners || []).map(o => `<option value="${o}">${o}</option>`).join('');
    const dayOpts = (opts.weekdays || []).map(d => `<option value="${d}">${d}</option>`).join('');

    if (entity === 'source') {
        const typeOpts = (opts.source_types || []).map(t => `<option value="${t}">${t}</option>`).join('');
        const upOpts = (opts.upstream_systems || []).map(u => `<option value="${u.id}">${u.name}</option>`).join('');
        const reportCheckboxes = (opts.reports || []).map(r =>
            `<label class="create-checkbox"><input type="checkbox" value="${r.id}" class="cf-report-cb"> ${r.name}</label>`
        ).join('');
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" placeholder="e.g. dbo.Customers or sales_data.csv" required></div>
            <div class="create-field"><label>Type <span class="required">*</span></label>
                <select id="cf-type"><option value="">Choose...</option>${typeOpts}</select></div>
            <div class="create-field"><label>Connection Info</label>
                <input type="text" id="cf-connection_info" placeholder="Server/path"></div>
            <div class="create-field"><label>Source Query</label>
                <input type="text" id="cf-source_query" placeholder="SQL query or file path"></div>
            <div class="create-field"><label>Owner</label>
                <select id="cf-owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Refresh Schedule</label>
                <select id="cf-refresh_schedule"><option value="">Choose...</option>${dayOpts}</select></div>
            <div class="create-field"><label>Tags</label>
                <input type="text" id="cf-tags" placeholder="comma-separated tags"></div>
            <div class="create-field"><label>Upstream System</label>
                <select id="cf-upstream_id"><option value="">None</option>${upOpts}</select></div>
            <div class="create-field create-field-full"><label>Associated Reports</label>
                <div class="create-checkbox-list" id="cf-report-list">${reportCheckboxes || '<span style="color:var(--text-dim)">No reports available</span>'}</div>
            </div>
        `;
    } else if (entity === 'report') {
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" placeholder="e.g. Weekly Sales Report" required></div>
            <div class="create-field"><label>Report Owner</label>
                <select id="cf-owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Business Owner</label>
                <select id="cf-business_owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Frequency</label>
                <span class="freq-pair-create">
                    <select id="cf-freq-type"><option value="">--</option><option value="Weekly">Weekly</option><option value="Monthly">Monthly</option></select>
                    <select id="cf-freq-detail" style="display:none"><option value="">--</option></select>
                </span></div>
            <div class="create-field"><label>Power BI URL</label>
                <input type="url" id="cf-powerbi_url" placeholder="https://app.powerbi.com/..."></div>
        `;
    } else if (entity === 'upstream') {
        const codeOpts = (opts.upstream_codes || []).map(c => `<option value="${c}">${c}</option>`).join('');
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" placeholder="e.g. GSCM - Global Supply Chain Master" required></div>
            <div class="create-field"><label>Code <span class="required">*</span></label>
                <select id="cf-code"><option value="">Choose...</option>${codeOpts}</select></div>
            <div class="create-field"><label>Refresh Day</label>
                <select id="cf-refresh_day"><option value="">Choose...</option>${dayOpts}</select></div>
        `;
    }

    const entityLabels = { source: 'Data Source', report: 'Report', upstream: 'Upstream System' };
    return `
        <div class="create-form">
            <h2>New ${entityLabels[entity]}</h2>
            <div class="create-fields">${fields}</div>
            <div class="create-form-actions">
                <button id="btn-create-submit" data-entity="${entity}">Create ${entityLabels[entity]}</button>
                <button class="btn-outline" id="btn-create-cancel">Cancel</button>
            </div>
        </div>
    `;
}

async function _handleCreateSubmit(e) {
    const entity = e.target.dataset.entity;
    const name = (document.getElementById('cf-name')?.value || '').trim();
    if (!name) { toast('Name is required'); return; }

    let body = { name };
    let url = `/api/create/${entity}`;

    if (entity === 'source') {
        const type = document.getElementById('cf-type')?.value;
        if (!type) { toast('Type is required'); return; }
        body.type = type;
        body.connection_info = document.getElementById('cf-connection_info')?.value || null;
        body.source_query = document.getElementById('cf-source_query')?.value || null;
        body.owner = document.getElementById('cf-owner')?.value || null;
        body.refresh_schedule = document.getElementById('cf-refresh_schedule')?.value || null;
        body.tags = document.getElementById('cf-tags')?.value || null;
        const upId = document.getElementById('cf-upstream_id')?.value;
        body.upstream_id = upId ? parseInt(upId) : null;
        const reportIds = [...document.querySelectorAll('.cf-report-cb:checked')].map(cb => parseInt(cb.value));
        body.report_ids = reportIds.length > 0 ? reportIds : null;
    } else if (entity === 'report') {
        body.owner = document.getElementById('cf-owner')?.value || null;
        body.business_owner = document.getElementById('cf-business_owner')?.value || null;
        const fType = document.getElementById('cf-freq-type')?.value;
        const fDetail = document.getElementById('cf-freq-detail')?.value;
        body.frequency = (fType && fDetail) ? `${fType} - ${fDetail}` : null;
        body.powerbi_url = document.getElementById('cf-powerbi_url')?.value || null;
    } else if (entity === 'upstream') {
        const code = document.getElementById('cf-code')?.value;
        if (!code) { toast('Code is required'); return; }
        body.code = code;
        body.refresh_day = document.getElementById('cf-refresh_day')?.value || null;
    }

    try {
        await apiPostJson(url, body);
        const label = entity === 'upstream' ? 'Upstream system' : entity.charAt(0).toUpperCase() + entity.slice(1);
        toast(`${label} "${name}" created`);
        navigate('create');
    } catch (err) {
        if (err.message.includes('409')) {
            toast('An entry with that name already exists');
        } else {
            toast('Failed: ' + err.message);
        }
    }
}

async function renderCreate() {
    const [options, customEntries, people] = await Promise.all([
        api("/api/create/options"),
        api("/api/create/custom-entries"),
        api("/api/people"),
    ]);
    window._createOptions = options;

    const entryTable = customEntries.length > 0 ? dataTable("dt-custom-entries", [
        { key: "entity_type", label: "Type", width: COL_W.sm, render: e => _entityTypeBadge(e.entity_type) },
        { key: "name", label: "Name", width: COL_W.lg, render: e => '<strong>' + e.name + '</strong>' },
        { key: "detail", label: "Detail", width: COL_W.xl, render: e => '<span style="color:var(--text-muted)">' + (e.detail || '-') + '</span>' },
        { key: "created_at", label: "Created", width: COL_W.md, render: e => '<span style="color:var(--text-muted)" title="' + formatDate(e.created_at) + '">' + timeAgo(e.created_at) + '</span>', sortVal: e => e.created_at || '' },
        { key: "actions", label: "", width: COL_W.md, filterable: false, sortable: false, render: e => `<div class="ce-actions">
            <button class="btn-sm btn-outline ce-edit-btn" data-id="${e.id}" data-type="${e.entity_type}">Edit</button>
            <button class="btn-sm btn-outline btn-danger-outline ce-delete-btn" data-id="${e.id}" data-type="${e.entity_type}">Delete</button>
        </div>` },
    ], customEntries) : '<div class="empty-state">No custom entries yet</div>';

    const roles = ["BI", "Business"];
    const roleOpts = roles.map(r => `<option value="${r}">${r}</option>`).join("");

    const peopleRows = people.map(p => `<tr>
        <td style="padding:0.35rem 0.5rem">${esc(p.name)}</td>
        <td style="padding:0.35rem 0.5rem;color:var(--text-muted)">${esc(p.role)}</td>
        <td style="padding:0.35rem 0.5rem"><button class="btn-sm btn-outline btn-danger-outline people-delete-btn" data-person-id="${p.id}">Delete</button></td>
    </tr>`).join("");

    const peopleContent = `
        <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.75rem">
            <input type="text" id="people-name-input" placeholder="Name" style="background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:0.3rem 0.5rem;font-size:0.82rem">
            <select id="people-role-input" style="background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:0.3rem 0.5rem;font-size:0.82rem">
                <option value="">Role...</option>${roleOpts}
            </select>
            <button id="btn-add-person" class="btn-sm">Add</button>
        </div>
        ${people.length > 0 ? `<table style="width:100%;border-collapse:collapse;font-size:0.82rem">
            <thead><tr style="border-bottom:1px solid var(--border)">
                <th style="text-align:left;padding:0.35rem 0.5rem;color:var(--text-dim);font-weight:500">Name</th>
                <th style="text-align:left;padding:0.35rem 0.5rem;color:var(--text-dim);font-weight:500">Role</th>
                <th style="padding:0.35rem 0.5rem;width:60px"></th>
            </tr></thead>
            <tbody>${peopleRows}</tbody>
        </table>` : '<div style="color:var(--text-dim);font-size:0.82rem">No people added yet</div>'}
    `;

    const assetsContent = `
        <div class="create-type-selector">
            <button class="create-type-btn" data-entity="report">Report</button>
            <button class="create-type-btn" data-entity="source">Data Source</button>
            <button class="create-type-btn" data-entity="upstream">Upstream System</button>
        </div>

        <div id="create-form-container">
            <div class="create-prompt" style="text-align:center;padding:2.5rem 1rem;color:var(--text-muted);font-size:0.9rem">
                <div style="font-size:1.2rem;margin-bottom:0.75rem;opacity:0.4">+</div>
                <div>Select an asset type above to create a new entry</div>
            </div>
        </div>

        <div class="section" style="margin-top:2rem">
            <h2 class="create-history-toggle" style="cursor:pointer;user-select:none">
                Custom Entries (${customEntries.length})
                <span style="font-size:0.72rem;font-weight:400;color:var(--text-dim)"> - click to expand</span>
            </h2>
            <div id="create-history-body" style="display:none">
                ${entryTable}
            </div>
        </div>
    `;

    return `
        <div class="page-header">
            <h1>Create</h1>
            <span class="subtitle">Manually add assets and people</span>
        </div>

        <div class="create-tabs">
            <button class="create-tab active" data-tab="assets">Assets</button>
            <button class="create-tab" data-tab="people">People</button>
        </div>

        <div id="create-tab-assets" class="create-tab-content">${assetsContent}</div>
        <div id="create-tab-people" class="create-tab-content" style="display:none">${peopleContent}</div>
    `;
}

function bindCreatePage() {
    // Tab switching
    document.querySelectorAll('.create-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.create-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.create-tab-content').forEach(c => c.style.display = 'none');
            const target = document.getElementById('create-tab-' + tab.dataset.tab);
            if (target) target.style.display = '';
        });
    });

    document.querySelectorAll('.create-type-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.create-type-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const container = document.getElementById('create-form-container');
            container.innerHTML = _renderCreateForm(btn.dataset.entity);
            document.getElementById('btn-create-submit').addEventListener('click', _handleCreateSubmit);
            // Wire linked frequency dropdowns in create form
            const cfType = document.getElementById('cf-freq-type');
            const cfDetail = document.getElementById('cf-freq-detail');
            if (cfType && cfDetail) {
                cfType.addEventListener('change', () => {
                    cfDetail.innerHTML = _freqDetailOpts(cfType.value, "");
                    cfDetail.style.display = cfType.value ? "" : "none";
                });
            }
            document.getElementById('btn-create-cancel').addEventListener('click', () => {
                container.innerHTML = '';
                document.querySelectorAll('.create-type-btn').forEach(b => b.classList.remove('active'));
            });
        });
    });

    // Add Person button
    const addPersonBtn = document.getElementById('btn-add-person');
    if (addPersonBtn) {
        addPersonBtn.addEventListener('click', async () => {
            const nameInput = document.getElementById('people-name-input');
            const roleInput = document.getElementById('people-role-input');
            const name = (nameInput.value || '').trim();
            const role = roleInput.value;
            if (!name) { toast('Name is required'); return; }
            if (!role) { toast('Role is required'); return; }
            try {
                await apiPostJson('/api/people', { name, role });
                toast('Person added');
                navigate('create');
            } catch (err) {
                toast('Failed: ' + err.message);
            }
        });
    }
    // Delete person buttons
    document.querySelectorAll('.people-delete-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const personId = btn.dataset.personId;
            if (!confirm('Delete this person?')) return;
            try {
                await fetch(`/api/people/${personId}`, { method: 'DELETE', headers: apiHeaders() });
                toast('Person deleted');
                navigate('create');
            } catch (err) {
                toast('Failed: ' + err.message);
            }
        });
    });

    const toggle = document.querySelector('.create-history-toggle');
    if (toggle) {
        toggle.addEventListener('click', () => {
            const body = document.getElementById('create-history-body');
            if (body) {
                const showing = body.style.display !== 'none';
                body.style.display = showing ? 'none' : '';
                if (!showing) bindDataTables();
            }
        });
    }

    // Delete handler (event delegation)
    document.addEventListener('click', async (e) => {
        const delBtn = e.target.closest('.ce-delete-btn');
        if (!delBtn) return;
        e.stopPropagation();
        const id = delBtn.dataset.id;
        const type = delBtn.dataset.type;
        const label = type === 'upstream' ? 'upstream system' : type;
        if (!confirm(`Delete this ${label}? This cannot be undone.`)) return;
        try {
            await apiDelete(`/api/create/${type}/${id}`);
            toast(`${label.charAt(0).toUpperCase() + label.slice(1)} deleted`);
            navigate('create');
        } catch (err) {
            toast('Delete failed: ' + err.message);
        }
    });

    // Edit handler (event delegation)
    document.addEventListener('click', async (e) => {
        const editBtn = e.target.closest('.ce-edit-btn');
        if (!editBtn) return;
        e.stopPropagation();
        const id = editBtn.dataset.id;
        const type = editBtn.dataset.type;
        try {
            let entity;
            if (type === 'source') {
                entity = await api(`/api/sources/${id}`);
            } else if (type === 'report') {
                entity = await api(`/api/reports/${id}`);
            } else if (type === 'upstream') {
                const upstreams = await api('/api/schedules/upstream-systems');
                entity = upstreams.find(u => u.id === parseInt(id));
                if (!entity) throw new Error('Upstream not found');
            }
            _showEditForm(type, id, entity);
        } catch (err) {
            toast('Failed to load entry: ' + err.message);
        }
    });
}

function _showEditForm(type, id, entity) {
    const container = document.getElementById('create-form-container');
    if (!container) return;
    // Deselect type buttons
    document.querySelectorAll('.create-type-btn').forEach(b => b.classList.remove('active'));

    const opts = window._createOptions;
    if (!opts) return;
    let fields = '';
    const ownerOpts = (opts.owners || []).map(o => `<option value="${o}" ${entity.owner === o ? 'selected' : ''}>${o}</option>`).join('');
    const dayOpts = (opts.weekdays || []).map(d => `<option value="${d}">${d}</option>`).join('');

    if (type === 'source') {
        const typeOpts = (opts.source_types || []).map(t => `<option value="${t}" ${entity.type === t ? 'selected' : ''}>${t}</option>`).join('');
        const upOpts = (opts.upstream_systems || []).map(u => `<option value="${u.id}" ${entity.upstream_id === u.id ? 'selected' : ''}>${u.name}</option>`).join('');
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" value="${esc(entity.name)}" required></div>
            <div class="create-field"><label>Type <span class="required">*</span></label>
                <select id="cf-type"><option value="">Choose...</option>${typeOpts}</select></div>
            <div class="create-field"><label>Connection Info</label>
                <input type="text" id="cf-connection_info" value="${esc(entity.connection_info)}"></div>
            <div class="create-field"><label>Source Query</label>
                <input type="text" id="cf-source_query" value="${esc(entity.source_query)}"></div>
            <div class="create-field"><label>Owner</label>
                <select id="cf-owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Refresh Schedule</label>
                <select id="cf-refresh_schedule"><option value="">Choose...</option>${dayOpts.replace(`value="${entity.refresh_schedule}"`, `value="${entity.refresh_schedule}" selected`)}</select></div>
            <div class="create-field"><label>Tags</label>
                <input type="text" id="cf-tags" value="${esc(entity.tags)}"></div>
            <div class="create-field"><label>Upstream System</label>
                <select id="cf-upstream_id"><option value="">None</option>${upOpts}</select></div>
        `;
    } else if (type === 'report') {
        const boOpts = (opts.owners || []).map(o => `<option value="${o}" ${entity.business_owner === o ? 'selected' : ''}>${o}</option>`).join('');
        const editFreq = entity.frequency || "";
        const editType = editFreq.startsWith("Monthly") ? "Monthly" : editFreq.startsWith("Weekly") ? "Weekly" : "";
        const editDetail = editFreq.replace(/^(Weekly|Monthly) - /, "");
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" value="${esc(entity.name)}" required></div>
            <div class="create-field"><label>Report Owner</label>
                <select id="cf-owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Business Owner</label>
                <select id="cf-business_owner"><option value="">Choose...</option>${boOpts}</select></div>
            <div class="create-field"><label>Frequency</label>
                <span class="freq-pair-create">
                    <select id="cf-freq-type"><option value="">--</option><option value="Weekly"${editType === "Weekly" ? " selected" : ""}>Weekly</option><option value="Monthly"${editType === "Monthly" ? " selected" : ""}>Monthly</option></select>
                    <select id="cf-freq-detail" ${!editType ? 'style="display:none"' : ""}>${_freqDetailOpts(editType, editDetail)}</select>
                </span></div>
            <div class="create-field"><label>Power BI URL</label>
                <input type="url" id="cf-powerbi_url" value="${entity.powerbi_url || ''}"></div>
        `;
    } else if (type === 'upstream') {
        const codeOpts = (opts.upstream_codes || []).map(c => `<option value="${c}" ${entity.code === c ? 'selected' : ''}>${c}</option>`).join('');
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" value="${esc(entity.name)}" required></div>
            <div class="create-field"><label>Code <span class="required">*</span></label>
                <select id="cf-code"><option value="">Choose...</option>${codeOpts}</select></div>
            <div class="create-field"><label>Refresh Day</label>
                <select id="cf-refresh_day"><option value="">Choose...</option>${dayOpts.replace(`value="${entity.refresh_day}"`, `value="${entity.refresh_day}" selected`)}</select></div>
        `;
    }

    const entityLabels = { source: 'Data Source', report: 'Report', upstream: 'Upstream System' };
    container.innerHTML = `
        <div class="create-form">
            <h2>Edit ${entityLabels[type]}</h2>
            <div class="create-fields">${fields}</div>
            <div class="create-form-actions">
                <button id="btn-edit-submit" data-entity="${type}" data-id="${id}">Save Changes</button>
                <button class="btn-outline" id="btn-edit-cancel">Cancel</button>
            </div>
        </div>
    `;
    container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // Wire linked frequency dropdowns in edit form
    const efType = document.getElementById('cf-freq-type');
    const efDetail = document.getElementById('cf-freq-detail');
    if (efType && efDetail) {
        efType.addEventListener('change', () => {
            efDetail.innerHTML = _freqDetailOpts(efType.value, "");
            efDetail.style.display = efType.value ? "" : "none";
        });
    }

    document.getElementById('btn-edit-submit').addEventListener('click', async (e) => {
        const entType = e.target.dataset.entity;
        const entId = e.target.dataset.id;
        let body = {};

        if (entType === 'source') {
            body.name = document.getElementById('cf-name')?.value || null;
            body.type = document.getElementById('cf-type')?.value || null;
            body.connection_info = document.getElementById('cf-connection_info')?.value || null;
            body.source_query = document.getElementById('cf-source_query')?.value || null;
            body.owner = document.getElementById('cf-owner')?.value || null;
            body.refresh_schedule = document.getElementById('cf-refresh_schedule')?.value || null;
            body.tags = document.getElementById('cf-tags')?.value || null;
            const upId = document.getElementById('cf-upstream_id')?.value;
            body.upstream_id = upId ? parseInt(upId) : null;
        } else if (entType === 'report') {
            body.name = document.getElementById('cf-name')?.value || null;
            body.owner = document.getElementById('cf-owner')?.value || null;
            body.business_owner = document.getElementById('cf-business_owner')?.value || null;
            const efType = document.getElementById('cf-freq-type')?.value;
            const efDetail = document.getElementById('cf-freq-detail')?.value;
            body.frequency = (efType && efDetail) ? `${efType} - ${efDetail}` : null;
            body.powerbi_url = document.getElementById('cf-powerbi_url')?.value || null;
        } else if (entType === 'upstream') {
            body.name = document.getElementById('cf-name')?.value || null;
            body.code = document.getElementById('cf-code')?.value || null;
            body.refresh_day = document.getElementById('cf-refresh_day')?.value || null;
        }

        try {
            await apiPatch(`/api/create/${entType}/${entId}`, body);
            toast('Changes saved');
            navigate('create');
        } catch (err) {
            toast('Update failed: ' + err.message);
        }
    });

    document.getElementById('btn-edit-cancel').addEventListener('click', () => {
        container.innerHTML = '';
    });
}


// ── Best Practices page ──

function _bpSevBadge(sev) {
    if (sev === "high") return '<span class="badge badge-red">high</span>';
    if (sev === "medium") return '<span class="badge badge-yellow">medium</span>';
    return '<span class="badge badge-muted">low</span>';
}

async function renderBestPractices() {
    const [data, reports] = await Promise.all([
        api("/api/best-practices"),
        api("/api/reports"),
    ]);
    const findings = data.findings || [];

    // Build report→owner lookup
    const ownerMap = {};
    const ownerSet = new Set();
    reports.forEach(r => {
        if (r.owner) { ownerMap[r.name] = r.owner; ownerSet.add(r.owner); }
    });

    // Enrich findings with owner
    findings.forEach(f => { f.owner = ownerMap[f.report] || ""; });
    window._bpFindings = findings;

    const owners = [...ownerSet].sort();
    const ownerOptions = owners.map(o => `<option value="${o}">${o}</option>`).join("");

    // Severity counts
    const counts = { high: 0, medium: 0, low: 0 };
    findings.forEach(f => { counts[f.severity] = (counts[f.severity] || 0) + 1; });

    const cols = [
        { key: "severity", label: "Severity", width: COL_W.sm, render: f => _bpSevBadge(f.severity), sortVal: f => ({ high: "0_high", medium: "1_medium", low: "2_low" })[f.severity] || "3" },
        { key: "report", label: "Report", width: COL_W.lg },
        { key: "owner", label: "Owner", width: COL_W.md },
        { key: "table", label: "Table", width: COL_W.lg },
        { key: "rule", label: "Rule", width: COL_W.lg },
        { key: "issue", label: "Issue", width: COL_W.xl, render: f => `<span style="white-space:normal;color:var(--text-secondary)">${f.issue}</span>` },
    ];

    const noIssues = findings.length === 0
        ? '<p style="color:var(--green);margin:1rem 0">All reports pass TMDL checks.</p>'
        : '';

    return `
    <div class="page-header">
        <h1>TMDL Checker</h1>
        <span class="subtitle">Automated checks against Power BI reports</span>
        <button class="btn-export" onclick="exportTableCSV('dt-bp','tmdl_checker.csv')">Export CSV</button>
    </div>
    <div class="kanban-toolbar" style="margin-bottom:0.75rem">
        <span class="owner-filter-label">Report Owner:</span>
        <select id="bp-owner-filter">
            <option value="">All Owners</option>
            ${ownerOptions}
        </select>
    </div>
    <div class="stat-row" style="margin-bottom:1.25rem" id="bp-stat-row">
        <div class="stat-card bp-filter-card" data-bp-filter="high" style="border-left:3px solid var(--red);cursor:pointer">
            <div class="stat-value" id="bp-count-high">${counts.high}</div>
            <div class="stat-label">High</div>
        </div>
        <div class="stat-card bp-filter-card" data-bp-filter="medium" style="border-left:3px solid var(--yellow);cursor:pointer">
            <div class="stat-value" id="bp-count-medium">${counts.medium}</div>
            <div class="stat-label">Medium</div>
        </div>
        <div class="stat-card bp-filter-card" data-bp-filter="low" style="border-left:3px solid var(--text-dim);cursor:pointer">
            <div class="stat-value" id="bp-count-low">${counts.low}</div>
            <div class="stat-label">Low</div>
        </div>
        <div class="stat-card bp-filter-card" data-bp-filter="" style="cursor:pointer">
            <div class="stat-value" id="bp-count-total">${findings.length}</div>
            <div class="stat-label">Total Issues</div>
        </div>
    </div>
    ${noIssues}
    <div id="bp-table-container">
        ${findings.length > 0 ? dataTable("dt-bp", cols, findings) : ''}
    </div>
    <div class="section-card" style="margin-top:1rem">
        <h2 style="margin-bottom:0.5rem">Rules checked</h2>
        <table class="mini-table">
            <thead><tr><th>Severity</th><th>Rule</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td>${_bpSevBadge("high")}</td><td>No local file sources</td><td>Data sources must not point to local drives (C:\\, D:\\). Use shared network paths or database connections.</td></tr>
                <tr><td>${_bpSevBadge("medium")}</td><td>Report Owner required</td><td>Every report should include a Report Owner metadata table for accountability.</td></tr>
                <tr><td>${_bpSevBadge("medium")}</td><td>Avoid DirectQuery mode</td><td>Tables should use Import mode for better performance. DirectQuery queries the source on every interaction.</td></tr>
                <tr><td>${_bpSevBadge("low")}</td><td>Too many columns</td><td>Tables with more than 30 columns may hurt performance. Consider splitting or removing unused columns.</td></tr>
                <tr><td>${_bpSevBadge("low")}</td><td>Duplicate data source</td><td>Multiple tables pulling from the same source should be consolidated into a single table or use reference queries.</td></tr>
                <tr><td>${_bpSevBadge("medium")}</td><td>Measure bloat</td><td>Reports with 50+ measures slow refresh and are hard to maintain. 100+ is high severity. Consider a shared dataset.</td></tr>
                <tr><td>${_bpSevBadge("low")}</td><td>Too many visuals on page</td><td>Pages with more than 15 visuals are slower to render and harder to read. Split into multiple pages.</td></tr>
                <tr><td>${_bpSevBadge("medium")}</td><td>Hardcoded date in DAX</td><td>DAX measures should not contain hardcoded dates like DATE(2024,1,1). Use TODAY(), NOW(), or a date parameter table.</td></tr>
            </tbody>
        </table>
    </div>`;
}

function _rebuildBpTable(filtered) {
    const cols = [
        { key: "severity", label: "Severity", width: COL_W.sm, render: f => _bpSevBadge(f.severity), sortVal: f => ({ high: "0_high", medium: "1_medium", low: "2_low" })[f.severity] || "3" },
        { key: "report", label: "Report", width: COL_W.lg },
        { key: "owner", label: "Owner", width: COL_W.md },
        { key: "table", label: "Table", width: COL_W.lg },
        { key: "rule", label: "Rule", width: COL_W.lg },
        { key: "issue", label: "Issue", width: COL_W.xl, render: f => `<span style="white-space:normal;color:var(--text-secondary)">${f.issue}</span>` },
    ];
    const container = document.getElementById("bp-table-container");
    if (container) {
        container.innerHTML = filtered.length > 0 ? dataTable("dt-bp", cols, filtered) : '<p style="color:var(--green);margin:1rem 0">No issues for this owner.</p>';
        bindDataTables();
    }
    // Update counts
    const counts = { high: 0, medium: 0, low: 0 };
    filtered.forEach(f => { counts[f.severity] = (counts[f.severity] || 0) + 1; });
    const hEl = document.getElementById("bp-count-high"); if (hEl) hEl.textContent = counts.high;
    const mEl = document.getElementById("bp-count-medium"); if (mEl) mEl.textContent = counts.medium;
    const lEl = document.getElementById("bp-count-low"); if (lEl) lEl.textContent = counts.low;
    const tEl = document.getElementById("bp-count-total"); if (tEl) tEl.textContent = filtered.length;
}

function bindBestPracticesPage() {
    // Owner filter
    const ownerFilter = document.getElementById("bp-owner-filter");
    if (ownerFilter) {
        ownerFilter.addEventListener("change", () => {
            const owner = ownerFilter.value;
            const all = window._bpFindings || [];
            const filtered = owner ? all.filter(f => f.owner === owner) : all;
            _rebuildBpTable(filtered);
        });
    }

    // Severity card filters
    document.querySelectorAll(".bp-filter-card[data-bp-filter]").forEach(card => {
        card.addEventListener("click", () => {
            const sev = card.dataset.bpFilter;
            const dt = window._dt && window._dt["dt-bp"];
            if (!dt) return;
            dt.filters["severity"] = sev;
            const filterInput = document.querySelector('tr.filter-row input[data-dt="dt-bp"][data-fcol="severity"]');
            if (filterInput) filterInput.value = sev;
            _refreshDT("dt-bp");
        });
    });
}


// ── Data Quality page ──

const _DQ_TYPE_LABELS = {
    row_count_range: "Row count range",
    row_count_change: "Row count change",
    null_rate: "Null rate",
    duplicate_key: "Duplicate key",
    value_range: "Value range",
};

function _dqStatusBadge(status) {
    if (status === "pass") return '<span class="badge badge-green">passed</span>';
    if (status === "fail" || status === "error") return `<span class="badge badge-red">${esc(status)}</span>`;
    if (status === "skipped") return '<span class="badge badge-yellow">waiting</span>';
    return '<span class="badge badge-muted">not run</span>';
}

function _dqConfigSummary(check) {
    const c = check.config || {};
    if (check.type === "row_count_range") return `Min ${c.min_rows ?? "-"}, max ${c.max_rows ?? "-"}`;
    if (check.type === "row_count_change") return `Drop ${c.max_drop_pct ?? "-"}%, increase ${c.max_increase_pct ?? "-"}%`;
    if (check.type === "null_rate") return `${c.column}: max ${c.max_null_pct}% null`;
    if (check.type === "duplicate_key") return `${(c.columns || []).join(", ")}: max ${c.max_duplicate_rows ?? 0} duplicate rows`;
    if (check.type === "value_range") return `${c.column}: ${c.min_value ?? "-"} to ${c.max_value ?? "-"}`;
    return JSON.stringify(c);
}

async function renderDataQuality() {
    const [checks, sources, typeData] = await Promise.all([
        api("/api/data-quality/checks"),
        api("/api/sources"),
        api("/api/data-quality/types"),
    ]);
    window._dqChecks = checks;
    window._dqSources = sources;
    window._dqTypes = typeData.types || [];

    const counts = { pass: 0, fail: 0, error: 0, skipped: 0, pending: 0 };
    checks.filter(c => c.enabled).forEach(c => {
        const key = c.latest_status || "pending";
        counts[key] = (counts[key] || 0) + 1;
    });
    const cols = [
        { key: "latest_status", label: "Status", width: COL_W.sm, render: c => _dqStatusBadge(c.latest_status), sortVal: c => c.latest_status || "pending" },
        { key: "name", label: "Check", width: COL_W.lg, render: c => `<strong>${esc(c.name)}</strong>${c.enabled ? "" : '<br><span style="color:var(--text-dim);font-size:0.7rem">disabled</span>'}` },
        { key: "source_name", label: "Source", width: COL_W.lg },
        { key: "type", label: "Rule", width: COL_W.md, render: c => esc(_DQ_TYPE_LABELS[c.type] || c.type) },
        { key: "config", label: "Limits", width: COL_W.lg, render: c => `<span style="white-space:normal;color:var(--text-secondary)">${esc(_dqConfigSummary(c))}</span>`, sortVal: c => _dqConfigSummary(c) },
        { key: "severity", label: "Severity", width: COL_W.sm, render: c => _bpSevBadge(c.severity === "critical" ? "high" : c.severity === "warning" ? "medium" : "low") },
        { key: "latest_value", label: "Value", width: COL_W.sm, render: c => c.latest_value == null ? "-" : esc(c.latest_value) },
        { key: "latest_ran_at", label: "Last run", width: COL_W.md, render: c => c.latest_ran_at ? `<span title="${esc(formatDate(c.latest_ran_at))}">${timeAgo(c.latest_ran_at)}</span>` : "-" },
        { key: "latest_message", label: "Result", width: COL_W.xl, render: c => `<span style="white-space:normal;color:var(--text-secondary)">${esc(c.latest_message || "Not run yet")}</span>` },
        { key: "_actions", label: "", width: COL_W.md, filterable: false, sortable: false, render: c => `
            <div style="display:flex;gap:0.3rem;flex-wrap:wrap">
                <button class="btn-sm btn-outline dq-run" data-id="${c.id}">Run</button>
                <button class="btn-sm btn-outline dq-edit" data-id="${c.id}">Edit</button>
                <button class="btn-sm btn-outline dq-toggle" data-id="${c.id}" data-enabled="${c.enabled ? "1" : "0"}">${c.enabled ? "Disable" : "Enable"}</button>
            </div>` },
    ];
    return `
        <div class="page-header">
            <h1>Data Quality</h1>
            <span class="subtitle">Scheduled read-only checks against governed sources</span>
            <button class="btn-outline" id="btn-dq-new">+ New Check</button>
            <button class="btn-new-task" id="btn-dq-run-all">Run all checks</button>
        </div>
        <div class="summary-counts" aria-label="Data-quality check summary">
            <div class="summary-count"><span class="count-num">${checks.filter(c => c.enabled).length}</span><span>Enabled</span></div>
            <div class="summary-count"><span class="count-num">${counts.pass}</span><span>Passed</span></div>
            <div class="summary-count"><span class="count-num">${counts.fail + counts.error}</span><span>Need attention</span></div>
            <div class="summary-count"><span class="count-num">${counts.skipped + counts.pending}</span><span>Waiting</span></div>
        </div>
        <div id="dq-form-area"></div>
        ${checks.length ? dataTable("dt-data-quality", cols, checks) : '<div class="empty-state">No checks configured. Create a row-count or PostgreSQL column check to start monitoring data validity.</div>'}
        <div class="section-card" style="margin-top:1rem">
            <h2 style="margin-bottom:0.5rem">How execution works</h2>
            <p style="color:var(--text-secondary);font-size:0.82rem">Checks run automatically after every source probe. PostgreSQL checks use the existing read-only connection. A failed check creates an owned action and alert; a later pass closes both automatically.</p>
        </div>`;
}

function _dqConfigFields(type, config = {}) {
    if (type === "row_count_range") return `
        <div class="create-field"><label>Minimum rows</label><input id="dq-min-rows" type="number" min="0" value="${esc(config.min_rows ?? "")}"></div>
        <div class="create-field"><label>Maximum rows</label><input id="dq-max-rows" type="number" min="0" value="${esc(config.max_rows ?? "")}"></div>`;
    if (type === "row_count_change") return `
        <div class="create-field"><label>Maximum drop %</label><input id="dq-max-drop" type="number" min="0" max="100" step="0.1" value="${esc(config.max_drop_pct ?? "")}"></div>
        <div class="create-field"><label>Maximum increase %</label><input id="dq-max-increase" type="number" min="0" step="0.1" value="${esc(config.max_increase_pct ?? "")}"></div>`;
    if (type === "null_rate") return `
        <div class="create-field"><label>Column</label><input id="dq-column" value="${esc(config.column || "")}" required></div>
        <div class="create-field"><label>Maximum null %</label><input id="dq-max-null" type="number" min="0" max="100" step="0.1" value="${esc(config.max_null_pct ?? "")}" required></div>`;
    if (type === "duplicate_key") return `
        <div class="create-field"><label>Key columns</label><input id="dq-columns" value="${esc((config.columns || []).join(", "))}" placeholder="order_id, line_id" required></div>
        <div class="create-field"><label>Allowed duplicate rows</label><input id="dq-max-duplicates" type="number" min="0" value="${esc(config.max_duplicate_rows ?? 0)}"></div>`;
    return `
        <div class="create-field"><label>Column</label><input id="dq-column" value="${esc(config.column || "")}" required></div>
        <div class="create-field"><label>Minimum value</label><input id="dq-min-value" type="number" step="any" value="${esc(config.min_value ?? "")}"></div>
        <div class="create-field"><label>Maximum value</label><input id="dq-max-value" type="number" step="any" value="${esc(config.max_value ?? "")}"></div>`;
}

function _dqNumber(id) {
    const raw = document.getElementById(id)?.value;
    return raw == null || raw === "" ? null : Number(raw);
}

function _dqCollectConfig(type) {
    if (type === "row_count_range") return { min_rows: _dqNumber("dq-min-rows"), max_rows: _dqNumber("dq-max-rows") };
    if (type === "row_count_change") return { max_drop_pct: _dqNumber("dq-max-drop"), max_increase_pct: _dqNumber("dq-max-increase") };
    if (type === "null_rate") return { column: document.getElementById("dq-column").value.trim(), max_null_pct: _dqNumber("dq-max-null") };
    if (type === "duplicate_key") return { columns: document.getElementById("dq-columns").value.split(",").map(v => v.trim()).filter(Boolean), max_duplicate_rows: _dqNumber("dq-max-duplicates") };
    return { column: document.getElementById("dq-column").value.trim(), min_value: _dqNumber("dq-min-value"), max_value: _dqNumber("dq-max-value") };
}

function _showDqForm(check = null) {
    const area = document.getElementById("dq-form-area");
    if (!area) return;
    const sources = window._dqSources || [];
    const types = window._dqTypes || [];
    const selectedType = check?.type || "row_count_range";
    area.innerHTML = `
        <div class="create-form" style="margin-bottom:1.25rem">
            <h2>${check ? "Edit" : "New"} data-quality check</h2>
            <div class="create-fields">
                <div class="create-field"><label>Name *</label><input id="dq-name" value="${esc(check?.name || "")}" required></div>
                <div class="create-field"><label>Source *</label><select id="dq-source">${sources.map(s => `<option value="${s.id}"${s.id === check?.source_id ? " selected" : ""}>${esc(s.name)} (${esc(s.type)})</option>`).join("")}</select></div>
                <div class="create-field"><label>Rule *</label><select id="dq-type">${types.map(t => `<option value="${t.type}"${t.type === selectedType ? " selected" : ""}>${esc(t.label)}</option>`).join("")}</select></div>
                <div class="create-field"><label>Severity</label><select id="dq-severity"><option value="critical"${check?.severity === "critical" ? " selected" : ""}>Critical</option><option value="warning"${check?.severity === "warning" ? " selected" : ""}>Warning</option><option value="info"${check?.severity === "info" ? " selected" : ""}>Info</option></select></div>
                <div id="dq-config-fields" style="display:contents">${_dqConfigFields(selectedType, check?.config || {})}</div>
            </div>
            <div style="margin-top:0.75rem;display:flex;gap:0.5rem"><button class="btn-new-task" id="dq-save">${check ? "Save changes" : "Create check"}</button><button class="btn-outline" id="dq-cancel">Cancel</button></div>
        </div>`;
    document.getElementById("dq-type").addEventListener("change", e => {
        document.getElementById("dq-config-fields").innerHTML = _dqConfigFields(e.target.value, {});
    });
    document.getElementById("dq-cancel").addEventListener("click", () => { area.innerHTML = ""; });
    document.getElementById("dq-save").addEventListener("click", async e => {
        const saveButton = e.currentTarget;
        const type = document.getElementById("dq-type").value;
        const body = {
            name: document.getElementById("dq-name").value.trim(),
            source_id: Number(document.getElementById("dq-source").value),
            type,
            severity: document.getElementById("dq-severity").value,
            enabled: check ? check.enabled : true,
            config: _dqCollectConfig(type),
        };
        if (!body.name) { toast("Check name is required"); return; }
        saveButton.disabled = true;
        saveButton.textContent = check ? "Saving changes..." : "Creating check...";
        try {
            if (check) await apiPatch(`/api/data-quality/checks/${check.id}`, body);
            else await apiPostJson("/api/data-quality/checks", body);
            toast(check ? "Check updated" : "Check created");
            await navigate("dataquality");
        } catch (err) {
            toast("Could not save check: " + err.message);
            saveButton.disabled = false;
            saveButton.textContent = check ? "Save changes" : "Create check";
        }
    });
    area.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function bindDataQualityPage() {
    document.getElementById("btn-dq-new")?.addEventListener("click", () => _showDqForm());
    document.getElementById("btn-dq-run-all")?.addEventListener("click", async e => {
        const button = e.currentTarget;
        button.disabled = true; button.textContent = "Running...";
        try {
            const result = await apiPost("/api/data-quality/run");
            toast(`${result.pass || 0} passed, ${(result.fail || 0) + (result.error || 0)} need attention`);
            await navigate("dataquality");
        } catch (err) { toast("Checks failed: " + err.message); }
        finally { button.disabled = false; button.textContent = "Run all checks"; }
    });
    const app = document.getElementById("app");
    if (app && window._dqTableClickHandler) app.removeEventListener("click", window._dqTableClickHandler);
    window._dqTableClickHandler = async e => {
        const button = e.target.closest(".dq-edit, .dq-run, .dq-toggle");
        if (!button) return;
        if (button.classList.contains("dq-edit")) {
            const check = (window._dqChecks || []).find(c => c.id === Number(button.dataset.id));
            if (check) _showDqForm(check);
            return;
        }
        if (button.classList.contains("dq-run")) {
            button.disabled = true;
            try { await apiPost(`/api/data-quality/checks/${button.dataset.id}/run`); await navigate("dataquality"); }
            catch (err) { toast("Check failed: " + err.message); }
            return;
        }
        const enabled = button.dataset.enabled !== "1";
        button.disabled = true;
        try { await apiPatch(`/api/data-quality/checks/${button.dataset.id}`, { enabled }); await navigate("dataquality"); }
        catch (err) { button.disabled = false; toast("Could not update check: " + err.message); }
    };
    if (app) app.addEventListener("click", window._dqTableClickHandler);
}


// ── Email ──

function _emailFixLinks(alert) {
    const reportLinks = (alert.report_links || [])
        .filter(r => r.powerbi_url)
        .map(r => `<a href="${esc(r.powerbi_url)}" target="_blank" rel="noopener">Power BI: ${esc(r.report_name)}</a>`);
    const sourceLinks = (alert.source_links || [])
        .filter(s => s.folder_path)
        .map(s => ({ ...s, label: s.link_label || "Source Folder" }))
        .map(s => s.href
            ? `<a href="${esc(s.href)}" target="_blank" rel="noopener">${esc(s.label)}: ${esc(s.source_name)}</a>`
            : `<span title="${esc(s.folder_path)}">${esc(s.label)}: ${esc(s.source_name)}</span>`);
    return [...reportLinks, ...sourceLinks];
}

function _emailAlertLine(alert) {
    const views = Number(alert.impact_views_30d || 0).toLocaleString();
    const degradedSince = formatDateOnly(alert.degraded_since || alert.created_at);
    const nextAction = alert.recommendation || alert.triage_cta || "Open the alert and review the evidence.";
    const links = _emailFixLinks(alert).slice(0, 3).join("");
    return `<tr>
        <td><span class="email-artifact email-artifact-${esc(alert.artifact_kind || "data")}">${esc(alert.artifact_label || "Data")}</span></td>
        <td>
            <div class="email-alert-title"><strong>${esc(alert.issue_label || alert.type)}</strong></div>
            <div>${esc(alert.asset_name || "Unknown asset")}</div>
            ${alert.pbi_refresh_error ? `<div class="email-refresh-error"><strong>PBI Refresh Error:</strong> ${esc(alert.pbi_refresh_error)}</div>` : ""}
            <div class="email-next-action"><strong>Next:</strong> ${esc(nextAction)}</div>
            ${links ? `<div class="email-fix-links">${links}</div>` : ""}
        </td>
        <td class="email-degraded-since">${esc(degradedSince)}</td>
        <td class="email-views">${esc(views)}</td>
    </tr>`;
}

async function renderEmail() {
    const [people, alertData, emailSchedules] = await Promise.all([
        api("/api/email/people"),
        api("/api/email/alert-summaries"),
        api("/api/email-schedules/people"),
    ]);
    const alertSummaries = alertData.summaries || [];
    window._emailPeople = people;
    window._emailAlertSummaries = alertSummaries;
    window._emailSchedulesByPerson = new Map((emailSchedules || []).map(s => [String(s.person_id), s]));

    const biPeople = people.filter(p => p.role === "BI");
    const mappingRows = biPeople.map(p => {
        const schedule = window._emailSchedulesByPerson.get(String(p.id));
        const status = schedule?.enabled ? "Scheduled" : "Manual";
        return `
        <tr>
            <td>${esc(p.name)}</td>
            <td>
                <label class="email-all-alerts-toggle">
                    <input class="email-all-alerts-check" data-person-id="${p.id}" type="checkbox" ${p.include_all_alerts ? "checked" : ""}>
                    <span>Include all</span>
                </label>
            </td>
            <td>${p.pending_alert_count || 0}</td>
            <td><input class="email-map-input" data-person-id="${p.id}" type="email" value="${esc(p.email || "")}" placeholder="name@example.com"></td>
            <td>
                <div class="email-profile-actions">
                    <button class="btn-sm btn-outline email-save-person" data-person-id="${p.id}">Save</button>
                    <button type="button" class="btn-sm btn-outline email-schedule-person" data-person-id="${p.id}" onclick="event.stopPropagation(); window.openEmailScheduleModal && window.openEmailScheduleModal(${p.id})">Schedule</button>
                    <span class="email-schedule-status${schedule?.enabled ? " active" : ""}">${esc(status)}</span>
                </div>
            </td>
        </tr>
    `;
    }).join("");

    const alertCards = alertSummaries.length ? alertSummaries.map(s => {
        const missingEmail = !s.email;
        const previewAlerts = (s.alerts || []).slice(0, 4).map(_emailAlertLine).join("");
        return `
            <div class="email-summary-panel" data-owner="${esc(s.owner_name)}">
                <div class="email-summary-head">
                    <label class="email-summary-check">
                        <input type="checkbox" class="email-alert-owner-check" value="${esc(s.owner_name)}" ${missingEmail ? "disabled" : ""}>
                        <span>${esc(s.owner_name)}</span>
                    </label>
                    <span class="email-address">${missingEmail ? "No email mapped" : esc(s.email)}</span>
                </div>
                <div class="email-summary-meta">
                    <span>${s.alert_count} active alert${s.alert_count === 1 ? "" : "s"}</span>
                    ${s.include_all_alerts ? "<span>All alerts</span>" : ""}
                </div>
                <div class="email-task-preview-wrap">
                    <table class="email-task-preview">
                        <thead><tr><th>Artifact</th><th>Issue and name</th><th>Degraded since</th><th>Views</th></tr></thead>
                        <tbody>${previewAlerts}</tbody>
                    </table>
                </div>
                ${s.alerts.length > 4 ? `<div class="email-more">+${s.alerts.length - 4} more</div>` : ""}
                <div class="email-summary-actions">
                    <button class="btn-outline email-open-draft" data-owner="${esc(s.owner_name)}" ${missingEmail ? "disabled" : ""}>Open Draft</button>
                    <button class="btn-outline email-copy-summary" data-owner="${esc(s.owner_name)}">Copy Summary</button>
                    <button class="btn-outline email-server-draft" data-owner="${esc(s.owner_name)}" ${missingEmail ? "disabled" : ""}>Create Outlook Draft</button>
                    <button class="btn-outline btn-danger-outline email-server-send" data-owner="${esc(s.owner_name)}" ${missingEmail ? "disabled" : ""}>Send Now</button>
                </div>
            </div>
        `;
    }).join("") : '<div class="empty-state">No active alerts assigned to BI owners.</div>';

    const mappedCount = biPeople.filter(p => p.email).length;
    return `
        <div class="page-header">
            <h1>Email</h1>
            <span class="subtitle">Ranked Outlook alert summaries by owner</span>
        </div>
        <div class="email-layout">
            <section class="email-section">
                <div class="email-section-head">
                    <h2>Owner Email Mapping</h2>
                    <span>${mappedCount}/${biPeople.length} mapped</span>
                </div>
                ${biPeople.length ? `
                    <table class="email-map-table">
                        <thead><tr><th>BI Owner</th><th>Scope</th><th>Active Alerts</th><th>Email</th><th></th></tr></thead>
                        <tbody>${mappingRows}</tbody>
                    </table>
                ` : '<div class="empty-state">Add BI people under Management -> Create -> People first.</div>'}
            </section>

            <section class="email-section">
                <div class="email-section-head">
                    <h2>Active Alert Summaries</h2>
                    <div class="email-bulk-actions">
                        <button class="btn-outline" id="email-alert-draft-selected">Create Drafts for Selected</button>
                        <button class="btn-outline btn-danger-outline" id="email-alert-send-selected">Send Selected Now</button>
                    </div>
                </div>
                <p class="email-send-note">Nothing is selected by default. Review the ranked preview, select recipients, then create drafts or send.</p>
                <div class="email-summary-list">${alertCards}</div>
            </section>
        </div>
    `;
}

function _emailSelectedOwners(fallbackOwner) {
    if (fallbackOwner) return [fallbackOwner];
    return Array.from(document.querySelectorAll(".email-alert-owner-check:checked")).map(i => i.value);
}

function _emailFindSummary(owner) {
    return (window._emailAlertSummaries || []).find(s => s.owner_name === owner);
}

async function _emailLaunchServer(mode, owner) {
    const ownerNames = _emailSelectedOwners(owner);
    if (ownerNames.length === 0) { toast("Select at least one owner with an email"); return; }
    const summaries = ownerNames.map(_emailFindSummary).filter(Boolean);
    if (mode === "send") {
        const recipients = summaries.map(s => `- ${s.owner_name} <${s.email}>: ${s.alert_count} alert${s.alert_count === 1 ? "" : "s"}`).join("\n");
        if (!confirm(`Send ${summaries.length} alert summary email${summaries.length === 1 ? "" : "s"} through Outlook now?\n\nRecipients:\n${recipients}\n\nThis action sends immediately.`)) return;
    }
    try {
        const result = await apiPostJson("/api/email/send-alert-summaries", { mode, owner_names: ownerNames });
        toast(`${mode === "send" ? "Send" : "Draft"} launched for ${result.count} owner${result.count === 1 ? "" : "s"}`);
    } catch (err) {
        toast("Outlook email failed: " + err.message);
    }
}

function bindEmailPage() {
    bindEmailProfileSchedules();

    document.querySelectorAll(".email-save-person").forEach(btn => {
        btn.addEventListener("click", async () => {
            const personId = btn.dataset.personId;
            const input = document.querySelector(`.email-map-input[data-person-id="${personId}"]`);
            const allAlertsInput = document.querySelector(`.email-all-alerts-check[data-person-id="${personId}"]`);
            btn.disabled = true;
            try {
                await apiPatch(`/api/email/people/${personId}`, {
                    email: input.value.trim() || null,
                    include_all_alerts: !!allAlertsInput?.checked,
                });
                toast("Email settings saved");
                await navigate("email");
            } catch (err) {
                btn.disabled = false;
                toast("Failed to save email: " + err.message);
            }
        });
    });

    document.querySelectorAll(".email-open-draft").forEach(btn => {
        btn.addEventListener("click", () => {
            const summary = _emailFindSummary(btn.dataset.owner);
            if (!summary || !summary.email) { toast("Map an email first"); return; }
            window.location.href = summary.mailto;
        });
    });

    document.querySelectorAll(".email-copy-summary").forEach(btn => {
        btn.addEventListener("click", async () => {
            const summary = _emailFindSummary(btn.dataset.owner);
            if (!summary) return;
            try {
                await navigator.clipboard.writeText(summary.body_text);
                toast("Summary copied");
            } catch (_) {
                toast("Clipboard unavailable");
            }
        });
    });

    document.querySelectorAll(".email-server-draft").forEach(btn => {
        btn.addEventListener("click", () => _emailLaunchServer("draft", btn.dataset.owner));
    });
    document.querySelectorAll(".email-server-send").forEach(btn => {
        btn.addEventListener("click", () => _emailLaunchServer("send", btn.dataset.owner));
    });
    const alertDraftSelected = document.getElementById("email-alert-draft-selected");
    if (alertDraftSelected) alertDraftSelected.addEventListener("click", () => _emailLaunchServer("draft"));
    const alertSendSelected = document.getElementById("email-alert-send-selected");
    if (alertSendSelected) alertSendSelected.addEventListener("click", () => _emailLaunchServer("send"));
}


// ── Power BI email recurrences ──

function _recValidationDetail(item) {
    const location = (item?.loc || []).filter(part => part !== "body");
    const fieldLabels = {
        name: "Recurrence name",
        report_name: "Power BI report",
        embed_url: "Power BI embed URL",
        page_name: "Report page",
        visual_name: "Table visual",
        visual_type: "Visual type",
        owner_name: "Alert owner",
        recipients: "Recipients",
        group_column: "Subgroup column",
        subject_template: "Email subject",
        alert_message: "Alert message",
        send_time: "Send time",
        weekdays: "Weekdays",
        groups: "Subgroups",
        rules: "Row rules",
    };
    let label = "Recurrence configuration";
    if (location[0] === "groups" && Number.isInteger(location[1])) {
        const suffix = location[2] === "display_name" ? "name" : fieldLabels[location[2]] || location[2] || "configuration";
        label = `Subgroup ${Number(location[1]) + 1} ${suffix}`;
    } else if (location.length) {
        label = fieldLabels[location.at(-1)] || String(location.at(-1)).replaceAll("_", " ");
    }
    const message = String(item?.msg || item || "Invalid value").replace(/^Value error,\s*/i, "");
    return `${label}: ${message}`;
}

async function _recRequest(path, options = {}) {
    const headers = apiHeaders(options.body ? { "Content-Type": "application/json" } : {});
    const response = await fetch(path, { ...options, headers });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
        let detail = payload?.detail;
        if (Array.isArray(detail)) detail = detail.map(_recValidationDetail).join(" ");
        if (detail && typeof detail === "object") detail = JSON.stringify(detail);
        throw new Error(detail || `Request failed with status ${response.status}`);
    }
    return payload;
}

function _recDateTime(value) {
    if (!value) return "-";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}

function _recScheduleText(item) {
    const time = item.send_time || "08:00";
    if (item.recurrence === "daily") return `Daily at ${time}`;
    if (item.recurrence === "weekdays") return `Weekdays at ${time}`;
    if (item.recurrence === "monthly") return `Monthly on day ${item.month_day || 1} at ${time}`;
    const days = (item.weekdays || []).map(day => day.slice(0, 3).replace(/^./, c => c.toUpperCase())).join(", ");
    return `${days || "Mon"} at ${time}`;
}

function _recStatusHtml(status) {
    const key = (status || "never run").toLowerCase();
    const success = ["sent", "drafted"].includes(key);
    const warning = ["no_rows", "running", "never run"].includes(key);
    const error = key === "failed";
    const label = key === "no_rows" ? "No matching rows" : key.replaceAll("_", " ");
    return `<span class="rec-status${success ? " success" : warning ? " warning" : error ? " error" : ""}">${esc(label)}</span>`;
}

function _recSourceText(item) {
    return [item.report_name, item.page_display_name || item.page_name, item.visual_title || item.visual_name]
        .filter(Boolean)
        .map(esc)
        .join(" / ");
}

async function renderRecurrences() {
    const [status, recurrences] = await Promise.all([
        _recRequest("/api/recurrences/status"),
        _recRequest("/api/recurrences"),
    ]);
    window._recurrenceStatus = status;
    window._recurrences = recurrences || [];
    const runtimeReady = status.cached_account && status.playwright_installed && status.edge_detected;
    const rows = window._recurrences.map(item => {
        const enabledGroups = (item.groups || []).filter(group => group.enabled).length;
        const deliveryLabel = item.delivery_mode === "single"
            ? "One recipient list"
            : `${enabledGroups} subgroup${enabledGroups === 1 ? "" : "s"}`;
        return `
            <tr data-recurrence-id="${item.id}">
                <td>
                    <span class="rec-name">${esc(item.name)}</span>
                    <span class="rec-meta">${item.enabled ? "Enabled" : "Paused"} - Owner: ${esc(item.owner_name || "Not assigned")}</span>
                </td>
                <td><div class="rec-source-path">${_recSourceText(item)}</div></td>
                <td>
                    <span class="rec-name">${deliveryLabel}</span>
                    <span class="rec-meta">${(item.rules || []).length} row rule${(item.rules || []).length === 1 ? "" : "s"}</span>
                </td>
                <td>
                    <span class="rec-name">${esc(_recScheduleText(item))}</span>
                    <span class="rec-meta">Next: ${esc(_recDateTime(item.next_run_at))}</span>
                </td>
                <td>
                    ${_recStatusHtml(item.last_status)}
                    <div class="rec-meta">${item.last_row_count ?? 0} rows - ${esc(_recDateTime(item.last_run_at))}</div>
                    ${item.last_error ? `<div class="rec-history-error">${esc(item.last_error)}</div>` : ""}
                </td>
                <td>
                    <div class="rec-row-actions">
                        <button class="btn-sm btn-outline rec-edit" data-id="${item.id}">Edit</button>
                        <button class="btn-sm btn-outline rec-draft" data-id="${item.id}">Create drafts</button>
                        <button class="btn-sm btn-outline btn-danger-outline rec-run" data-id="${item.id}">Run now</button>
                        <button class="btn-sm btn-outline rec-history" data-id="${item.id}">History</button>
                        <button class="btn-sm btn-outline rec-delete" data-id="${item.id}">Delete</button>
                    </div>
                </td>
            </tr>
            <tr class="rec-history-panel" id="rec-history-${item.id}" hidden>
                <td colspan="6"><div class="rec-loading">Loading run history...</div></td>
            </tr>
        `;
    }).join("");
    return `
        <div class="rec-page-header">
            <div class="page-header">
                <h1>Recurrences</h1>
                <span class="subtitle">Scheduled Outlook emails from live Power BI table visuals</span>
            </div>
            <div class="rec-page-actions">
                <button class="btn-new-task" id="rec-create">Create recurrence</button>
            </div>
        </div>
        <div class="rec-status-strip" aria-label="Recurrence runtime status">
            <span>Power BI: <strong>${status.cached_account ? "Cached account connected" : "Reconnect required"}</strong></span>
            <span>Visual export: <strong>${status.playwright_installed && status.edge_detected ? "Headless Edge ready" : "Setup required"}</strong></span>
            <span>Refresh gate: <strong>Latest refresh must succeed</strong></span>
            <span>Schedule timezone: <strong>${esc(status.system_timezone || "Local machine time")}</strong></span>
            <span>Export limit: <strong>${Number(status.max_rows || 30000).toLocaleString()} rows</strong></span>
        </div>
        ${runtimeReady ? "" : `
            <div class="rec-runtime-warning" role="alert">
                Recurrences cannot export visuals yet. Connect the cached Power BI account, run the latest setup.ps1 so Playwright is installed, and confirm Microsoft Edge is installed for the Windows account running Metronome.
            </div>
        `}
        <section class="rec-list-section">
            <div class="rec-list-head">
                <h2>Saved recurrences</h2>
                <span>${window._recurrences.length} configured</span>
            </div>
            ${rows ? `
                <div class="rec-table-wrap">
                    <table class="rec-table">
                        <thead><tr><th>Name</th><th>Power BI source</th><th>Delivery</th><th>Schedule</th><th>Last run</th><th>Actions</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            ` : `
                <div class="rec-empty">
                    <strong>No recurrences configured</strong>
                    Create one to send Power BI table rows to one recipient list or separate subgroups on a schedule.
                    <div class="rec-page-actions" style="justify-content:center;margin-top:var(--space-md)">
                        <button class="btn-new-task" id="rec-create-empty">Create recurrence</button>
                    </div>
                </div>
            `}
        </section>
    `;
}

function _recBaseConfig(existing = null) {
    return {
        name: existing?.name || "",
        enabled: existing ? !!existing.enabled : true,
        workspace_id: existing?.workspace_id || "",
        workspace_name: existing?.workspace_name || "",
        report_id: existing?.report_id || "",
        report_name: existing?.report_name || "",
        report_url: existing?.report_url || null,
        embed_url: existing?.embed_url || "",
        dataset_id: existing?.dataset_id || null,
        page_name: existing?.page_name || "",
        page_display_name: existing?.page_display_name || "",
        visual_name: existing?.visual_name || "",
        visual_title: existing?.visual_title || "",
        visual_type: existing?.visual_type || "",
        owner_name: existing?.owner_name || "",
        delivery_mode: existing?.delivery_mode || "single",
        recipients: existing?.recipients || "",
        group_column: existing?.group_column || "",
        subject_template: existing?.subject_template || "{recurrence} ({row_count} rows)",
        alert_message: existing?.alert_message || "",
        recurrence: existing?.recurrence || "weekly",
        weekdays: [...(existing?.weekdays || ["monday"])],
        month_day: existing?.month_day || 1,
        send_time: existing?.send_time || "08:00",
        groups: (existing?.groups || []).map(group => ({
            match_value: group.match_value ?? "",
            display_name: group.display_name || group.match_value || "Group",
            recipients: group.recipients || "",
            enabled: group.enabled !== false,
        })),
        rules: (existing?.rules || []).map(rule => ({
            column_name: rule.column_name || "",
            operator: rule.operator || "gt",
            compare_value: rule.compare_value ?? "",
        })),
    };
}

async function _recOpenBuilder(existing = null) {
    const app = $("#app");
    app.innerHTML = '<div class="loading">Loading live Power BI reports...</div>';
    try {
        const reportPayload = await _recRequest("/api/recurrences/reports");
        const config = _recBaseConfig(existing);
        const reports = [...(reportPayload.reports || [])];
        if (existing && !reports.some(report => report.id === existing.report_id)) {
            reports.push({
                id: existing.report_id,
                name: existing.report_name,
                dataset_id: existing.dataset_id,
                web_url: existing.report_url,
                embed_url: existing.embed_url,
                owner_name: existing.owner_name,
                unavailable: true,
            });
        }
        if (!config.owner_name) {
            const selectedReport = reports.find(report => report.id === config.report_id);
            config.owner_name = selectedReport?.owner_name || "";
        }
        window._recBuilder = {
            id: existing?.id || null,
            step: 1,
            config,
            reports,
            people: [...(reportPayload.people || [])],
            workspace: reportPayload.workspace,
            pages: [],
            visuals: [],
            preview: null,
            loading: "",
            error: "",
        };
        _recRenderBuilder();
        window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
        app.innerHTML = `
            <div class="rec-empty">
                <strong>Power BI reports could not be loaded</strong>
                <div class="rec-inline-error">${esc(error.message)}</div>
                <div class="rec-page-actions" style="justify-content:center;margin-top:var(--space-md)">
                    <button class="btn-outline" id="rec-builder-return">Return to recurrences</button>
                </div>
            </div>`;
        $("#rec-builder-return")?.addEventListener("click", () => navigate("recurrences"));
    }
}

function _recBuilderSummary(state) {
    const config = state.config;
    const enabledGroups = config.groups.filter(group => group.enabled).length;
    const delivery = config.delivery_mode === "single"
        ? (config.recipients ? "One recipient list" : "Recipients not entered")
        : `${enabledGroups} subgroup${enabledGroups === 1 ? "" : "s"} enabled`;
    return `
        <aside class="rec-builder-summary" aria-label="Recurrence summary">
            <h2>Current configuration</h2>
            <dl class="rec-summary-list">
                <div><dt>Report</dt><dd>${esc(config.report_name || "Not selected")}</dd></div>
                <div><dt>Page</dt><dd>${esc(config.page_display_name || "Not selected")}</dd></div>
                <div><dt>Visual</dt><dd>${esc(config.visual_title || "Not selected")}</dd></div>
                <div><dt>Alert owner</dt><dd>${esc(config.owner_name || "Not assigned")}</dd></div>
                <div><dt>Preview</dt><dd>${state.preview ? `${state.preview.row_count.toLocaleString()} rows, ${state.preview.columns.length} columns` : "Not loaded"}</dd></div>
                <div><dt>Delivery</dt><dd>${esc(delivery)}</dd></div>
                <div><dt>Row rules</dt><dd>${config.rules.length}</dd></div>
                <div><dt>Alert message</dt><dd>${config.alert_message.trim() ? "Added" : "Not added"}</dd></div>
                <div><dt>Schedule</dt><dd>${esc(_recScheduleText(config))}</dd></div>
            </dl>
        </aside>`;
}

function _recStepButtons(step) {
    const labels = ["Report", "Page and visual", "Data rules", "Schedule"];
    return `<div class="rec-stepper" aria-label="Recurrence setup progress">${labels.map((label, index) => {
        const number = index + 1;
        const cls = number === step ? " current" : number < step ? " complete" : "";
        return `<div class="rec-step-button${cls}" ${number === step ? 'aria-current="step"' : ""}>
            <span class="rec-step-number">${number}</span><span>${label}</span>
        </div>`;
    }).join("")}</div>`;
}

function _recStepOne(state) {
    const options = state.reports.map(report => `
        <option value="${esc(report.id)}" ${report.id === state.config.report_id ? "selected" : ""}>
            ${esc(report.name)}${report.unavailable ? " (not in current workspace list)" : ""}
        </option>`).join("");
    return `
        <div class="rec-step-content">
            <h2>Choose a Power BI report</h2>
            <p class="rec-step-intro">This list comes directly from the Power BI workspace configured in Metronome. The saved account token remains on the server.</p>
            <div class="rec-form-grid">
                <label class="rec-field full">Report
                    <select id="rec-report-select">
                        <option value="">Select a report</option>
                        ${options}
                    </select>
                    <small>Workspace: ${esc(state.workspace?.name || state.config.workspace_name || "Configured workspace")}</small>
                </label>
            </div>
            <div class="rec-step-footer">
                <span class="rec-table-note">The report must remain accessible to the cached Power BI account.</span>
                <button class="btn-new-task" id="rec-next-report">Continue to pages</button>
            </div>
        </div>`;
}

function _recStepTwo(state) {
    const pageOptions = state.pages.map(page => `
        <option value="${esc(page.name)}" ${page.name === state.config.page_name ? "selected" : ""}>${esc(page.display_name)}</option>
    `).join("");
    const visualOptions = state.visuals.length ? state.visuals.map(visual => {
        const selectable = visual.is_table;
        return `
            <label class="rec-visual-option${selectable ? "" : " disabled"}">
                <input type="radio" name="rec-visual" value="${esc(visual.name)}" ${visual.name === state.config.visual_name ? "checked" : ""} ${selectable ? "" : "disabled"}>
                <span>
                    <span class="rec-visual-title">${esc(visual.title || "Untitled visual")}</span>
                    <span class="rec-visual-id">${esc(visual.name)}</span>
                </span>
                <span class="rec-status${selectable ? " success" : " warning"}">${selectable ? "Table" : esc(visual.type || "Unknown")}</span>
            </label>`;
    }).join("") : "";
    return `
        <div class="rec-step-content">
            <h2>Choose the page and table visual</h2>
            <p class="rec-step-intro">Metronome uses the technical page and visual identifiers. Moving, renaming, or changing the columns does not change the saved selection.</p>
            <div class="rec-form-grid">
                <label class="rec-field">Report page
                    <select id="rec-page-select">
                        <option value="">Select a page</option>
                        ${pageOptions}
                    </select>
                </label>
                <div class="rec-field">
                    <span>Live visuals</span>
                    <button class="btn-outline" id="rec-load-visuals" ${state.config.page_name ? "" : "disabled"}>Load page visuals</button>
                    <small>This usually takes 10-60 seconds.</small>
                </div>
            </div>
            ${visualOptions ? `<div class="rec-config-section"><h3>Table visuals</h3><p>Select the exact visual that should drive the email.</p><div class="rec-visual-list">${visualOptions}</div></div>` : ""}
            <div class="rec-step-footer">
                <button class="btn-outline" id="rec-back-page">Back to report</button>
                <button class="btn-new-task" id="rec-next-preview" ${state.config.visual_name ? "" : "disabled"}>Fetch data preview</button>
            </div>
        </div>`;
}

function _recPreviewTable(preview) {
    if (!preview?.columns?.length) return '<div class="rec-empty"><strong>The visual returned no columns</strong>Check the visual export settings in Power BI.</div>';
    const header = preview.columns.map(column => `<th>${esc(column)}</th>`).join("");
    const rows = (preview.rows || []).slice(0, 10).map(row => `<tr>${preview.columns.map(column => `<td>${esc(row[column] ?? "")}</td>`).join("")}</tr>`).join("");
    return `
        <div class="rec-preview-summary">
            <span><strong>${preview.row_count.toLocaleString()}</strong> exported rows</span>
            <span><strong>${preview.columns.length}</strong> columns</span>
            ${preview.export_limit_reached ? '<span class="rec-status warning">30,000 row limit reached</span>' : ""}
        </div>
        <div class="rec-preview-wrap">
            <table class="rec-preview-table"><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table>
        </div>
        <p class="rec-table-note">Showing the first ${Math.min(10, preview.rows.length)} rows. Emails use all exported rows that match the saved rules.</p>`;
}

function _recStepThree(state) {
    const config = state.config;
    const preview = state.preview;
    const columnOptions = (preview?.columns || []).map(column => `<option value="${esc(column)}" ${column === config.group_column ? "selected" : ""}>${esc(column)}</option>`).join("");
    const groupRows = config.groups.map((group, index) => `
        <tr>
            <td><input class="rec-group-enabled" data-index="${index}" type="checkbox" ${group.enabled ? "checked" : ""} aria-label="Enable ${esc(group.display_name)}"></td>
            <td class="rec-group-value">${esc(group.match_value || "(Blank)")}</td>
            <td><input class="rec-group-name" data-index="${index}" type="text" value="${esc(group.display_name)}" maxlength="200" aria-label="Subgroup name"></td>
            <td><input class="rec-group-recipients" data-index="${index}" type="text" value="${esc(group.recipients)}" placeholder="name@example.com; team@example.com" aria-label="Subgroup recipients"></td>
        </tr>`).join("");
    const operatorOptions = [
        ["gt", "Above (>)"], ["gte", "At least (>=)"], ["lt", "Below (<)"], ["lte", "At most (<=)"],
        ["eq", "Equals"], ["ne", "Does not equal"], ["contains", "Contains"], ["not_contains", "Does not contain"],
        ["is_empty", "Is empty"], ["not_empty", "Is not empty"],
    ];
    const ruleRows = config.rules.map((rule, index) => {
        const noValue = ["is_empty", "not_empty"].includes(rule.operator);
        return `<div class="rec-rule-row" data-index="${index}">
            <select class="rec-rule-column" data-index="${index}" aria-label="Rule column">
                ${(preview?.columns || []).map(column => `<option value="${esc(column)}" ${column === rule.column_name ? "selected" : ""}>${esc(column)}</option>`).join("")}
            </select>
            <select class="rec-rule-operator" data-index="${index}" aria-label="Rule operator">
                ${operatorOptions.map(([value, label]) => `<option value="${value}" ${value === rule.operator ? "selected" : ""}>${label}</option>`).join("")}
            </select>
            <input class="rec-rule-value" data-index="${index}" type="text" value="${esc(rule.compare_value || "")}" placeholder="Threshold or value" aria-label="Rule comparison value" ${noValue ? "disabled" : ""}>
            <button class="btn-sm btn-outline rec-rule-remove" data-index="${index}">Remove rule</button>
        </div>`;
    }).join("");
    return `
        <div class="rec-step-content">
            <h2>Preview and define email rows</h2>
            <p class="rec-step-intro">Row rules are optional. Send all eligible rows to one recipient list, or split them into subgroup emails. If a required rule or subgroup column disappears, the run fails and sends nothing.</p>
            ${_recPreviewTable(preview)}
            <section class="rec-config-section">
                <h3>Email delivery</h3>
                <p>Choose whether this recurrence sends one email or a separate email for each subgroup value.</p>
                <label class="rec-field">Delivery method
                    <select id="rec-delivery-mode">
                        <option value="single" ${config.delivery_mode === "single" ? "selected" : ""}>One email with all eligible rows</option>
                        <option value="subgroups" ${config.delivery_mode === "subgroups" ? "selected" : ""}>Separate emails by subgroup</option>
                    </select>
                </label>
                ${config.delivery_mode === "single" ? `
                    <label class="rec-field" style="margin-top:var(--space-md)">Recipients
                        <input id="rec-single-recipients" type="text" value="${esc(config.recipients)}" placeholder="name@example.com; team@example.com">
                        <small>All rows that pass the optional rules will be included in one email.</small>
                    </label>` : `
                    <div style="margin-top:var(--space-md)">
                        <label class="rec-field">Subgroup column
                            <select id="rec-group-column"><option value="">Select a column</option>${columnOptions}</select>
                        </label>
                        ${config.group_column ? `
                            <div class="rec-groups-wrap" style="margin-top:var(--space-md)">
                                <table class="rec-groups-table">
                                    <thead><tr><th>Send</th><th>Column value</th><th>Email group name</th><th>Recipients</th></tr></thead>
                                    <tbody>${groupRows}</tbody>
                                </table>
                            </div>` : ""}
                    </div>`}
            </section>
            <section class="rec-config-section">
                <h3>Row rules</h3>
                <p>All configured rules are combined. Only rows matching every rule are eligible for email.</p>
                <div class="rec-rules-list">${ruleRows || '<div class="rec-table-note">No row rules. Every exported row is eligible.</div>'}</div>
                <button class="btn-outline" id="rec-add-rule" style="margin-top:var(--space-md)">Add row rule</button>
            </section>
            <div class="rec-step-footer">
                <button class="btn-outline" id="rec-back-visual">Back to visual</button>
                <button class="btn-new-task" id="rec-next-schedule">Continue to schedule</button>
            </div>
        </div>`;
}

function _recStepFour(state) {
    const config = state.config;
    const weekdayLabels = Object.entries({ monday: "Monday", tuesday: "Tuesday", wednesday: "Wednesday", thursday: "Thursday", friday: "Friday", saturday: "Saturday", sunday: "Sunday" });
    const knownOwner = state.people.some(person => person.name === config.owner_name);
    const ownerOptions = [
        ...(!knownOwner && config.owner_name ? [{ name: config.owner_name, email: null, unavailable: true }] : []),
        ...state.people,
    ].map(person => `
        <option value="${esc(person.name)}" ${person.name === config.owner_name ? "selected" : ""}>
            ${esc(person.name)}${person.email ? ` - ${esc(person.email)}` : " - email not mapped"}${person.unavailable ? " (unavailable)" : ""}
        </option>`).join("");
    return `
        <div class="rec-step-content">
            <h2>Name and schedule the recurrence</h2>
            <p class="rec-step-intro">Metronome checks due recurrences every minute using the Windows host's local timezone. It sends only after the semantic model's latest refresh succeeds. If a send run fails, the alert owner receives the reason by email.</p>
            <div class="rec-form-grid">
                <label class="rec-field full">Recurrence name
                    <input id="rec-name" type="text" maxlength="200" value="${esc(config.name)}" placeholder="Weekly subsidiary exceptions">
                </label>
                <label class="rec-field full">Alert message
                    <textarea id="rec-alert-message" maxlength="2000" placeholder="Action required: Review these alerts and update the affected records before the next refresh.">${esc(config.alert_message)}</textarea>
                    <small>Optional. Shown in an information box above the results. Use it for definitions, key actions, warnings, ownership, or other context specific to this alert.</small>
                </label>
                <label class="rec-enable-row full"><input id="rec-enabled" type="checkbox" ${config.enabled ? "checked" : ""}> Enable scheduled sending after save</label>
                <label class="rec-field full">Alert owner
                    <select id="rec-owner">
                        <option value="">Select an owner</option>
                        ${ownerOptions}
                    </select>
                    <small>Defaults to the selected report owner. The owner's email comes from Management &gt; Create &gt; People.</small>
                </label>
                <label class="rec-field full">Email subject
                    <input id="rec-subject" type="text" maxlength="500" value="${esc(config.subject_template)}">
                    <small>Available placeholders: {recurrence}, {group}, {row_count}, {report}, {date}</small>
                </label>
                <label class="rec-field">Frequency
                    <select id="rec-frequency">
                        <option value="daily" ${config.recurrence === "daily" ? "selected" : ""}>Daily</option>
                        <option value="weekly" ${config.recurrence === "weekly" ? "selected" : ""}>Weekly</option>
                        <option value="weekdays" ${config.recurrence === "weekdays" ? "selected" : ""}>Weekdays</option>
                        <option value="monthly" ${config.recurrence === "monthly" ? "selected" : ""}>Monthly</option>
                    </select>
                </label>
                <label class="rec-field">Send time
                    <input id="rec-send-time" type="time" value="${esc(config.send_time)}">
                    <small>${esc(window._recurrenceStatus?.system_timezone || "Windows host local time")}</small>
                </label>
                ${config.recurrence === "monthly" ? `
                    <label class="rec-field">Day of month
                        <input id="rec-month-day" type="number" min="1" max="31" value="${config.month_day || 1}">
                        <small>Short months use their final calendar day.</small>
                    </label>` : ""}
                ${config.recurrence === "weekly" ? `
                    <div class="rec-field full"><span>Send on</span>
                        <div class="rec-weekdays">${weekdayLabels.map(([value, label]) => `
                            <label class="rec-weekday"><input class="rec-weekday-input" type="checkbox" value="${value}" ${config.weekdays.includes(value) ? "checked" : ""}> ${label}</label>
                        `).join("")}</div>
                    </div>` : ""}
            </div>
            <div class="rec-step-footer">
                <button class="btn-outline" id="rec-back-rules">Back to data rules</button>
                <button class="btn-new-task" id="rec-save">${state.id ? "Save changes" : "Create recurrence"}</button>
            </div>
        </div>`;
}

function _recRenderBuilder() {
    const state = window._recBuilder;
    if (!state) return;
    const stepContent = state.step === 1 ? _recStepOne(state)
        : state.step === 2 ? _recStepTwo(state)
        : state.step === 3 ? _recStepThree(state)
        : _recStepFour(state);
    $("#app").innerHTML = `
        <div class="rec-builder-heading">
            <div><h1>${state.id ? "Edit recurrence" : "Create recurrence"}</h1><p>Configure one live Power BI table visual and its scheduled emails.</p></div>
            <button class="btn-outline" id="rec-close-builder">Close builder</button>
        </div>
        ${_recStepButtons(state.step)}
        ${state.error ? `<div class="rec-inline-error" role="alert" style="margin-bottom:var(--space-lg)">${esc(state.error)}</div>` : ""}
        ${state.loading ? `<div class="rec-loading" role="status" style="margin-bottom:var(--space-lg)"><strong>${esc(state.loading)}</strong><br>Keep this page open. Power BI visual operations usually take 10-60 seconds.</div>` : ""}
        <div class="rec-builder-shell">
            <main class="rec-builder-main">${stepContent}</main>
            ${_recBuilderSummary(state)}
        </div>`;
    _recBindBuilder();
}

function _recSelectedReport(state) {
    return state.reports.find(report => report.id === state.config.report_id) || null;
}

function _recMergeGroups(state, reset = false) {
    const column = state.config.group_column;
    const values = state.preview?.values?.[column] || [];
    const existing = new Map((reset ? [] : state.config.groups).map(group => [String(group.match_value).trim().toLowerCase(), group]));
    state.config.groups = values.map(value => {
        const found = existing.get(String(value).trim().toLowerCase());
        return found || {
            match_value: String(value),
            display_name: String(value).trim() || "Blank",
            recipients: "",
            enabled: true,
        };
    });
}

async function _recRunLoading(label, operation) {
    const state = window._recBuilder;
    state.loading = label;
    state.error = "";
    _recRenderBuilder();
    try {
        await operation();
        state.loading = "";
    } catch (error) {
        state.loading = "";
        state.error = error.message;
    }
    _recRenderBuilder();
}

function _recValidateDelivery(state) {
    if (state.config.delivery_mode === "single") {
        if (!state.config.recipients.trim()) return "Enter at least one recipient email address.";
    } else {
        if (!state.config.group_column) return "Choose a subgroup column or select one-email delivery.";
        const enabled = state.config.groups.filter(group => group.enabled);
        if (!enabled.length) return "Enable at least one subgroup or select one-email delivery.";
        for (const group of enabled) {
            if (!group.display_name.trim()) return `Enter a name for subgroup '${group.match_value || "(Blank)"}'.`;
            if (!group.recipients.trim()) return `Enter recipients for subgroup '${group.display_name}'.`;
        }
    }
    for (const rule of state.config.rules) {
        if (!["is_empty", "not_empty"].includes(rule.operator) && !String(rule.compare_value || "").trim()) {
            return `Enter a comparison value for the ${rule.column_name} rule.`;
        }
    }
    return "";
}

function _recPayload(state) {
    const config = state.config;
    return {
        name: config.name.trim(),
        enabled: !!config.enabled,
        workspace_id: config.workspace_id,
        workspace_name: config.workspace_name || null,
        report_id: config.report_id,
        report_name: config.report_name,
        report_url: config.report_url || null,
        embed_url: config.embed_url,
        dataset_id: config.dataset_id || null,
        page_name: config.page_name,
        page_display_name: config.page_display_name || null,
        visual_name: config.visual_name,
        visual_title: config.visual_title || null,
        visual_type: config.visual_type,
        owner_name: config.owner_name || null,
        delivery_mode: config.delivery_mode,
        recipients: config.delivery_mode === "single" ? config.recipients.trim() : null,
        group_column: config.delivery_mode === "subgroups" ? config.group_column : "",
        subject_template: config.subject_template.trim(),
        alert_message: config.alert_message.trim() || null,
        recurrence: config.recurrence,
        weekdays: config.weekdays,
        month_day: config.recurrence === "monthly" ? Number(config.month_day || 1) : null,
        send_time: config.send_time,
        groups: (config.delivery_mode === "subgroups" ? config.groups : []).map(group => ({
            match_value: String(group.match_value),
            display_name: group.display_name.trim(),
            recipients: group.recipients.trim(),
            enabled: !!group.enabled,
        })),
        rules: config.rules.map(rule => ({
            column_name: rule.column_name,
            operator: rule.operator,
            compare_value: ["is_empty", "not_empty"].includes(rule.operator) ? null : String(rule.compare_value || "").trim(),
        })),
    };
}

function _recBindBuilder() {
    const state = window._recBuilder;
    $("#rec-close-builder")?.addEventListener("click", () => {
        if (confirm("Close the recurrence builder? Unsaved changes will be lost.")) {
            window._recBuilder = null;
            navigate("recurrences");
        }
    });
    $("#rec-report-select")?.addEventListener("change", event => {
        const report = state.reports.find(item => item.id === event.target.value);
        state.config.report_id = report?.id || "";
        state.config.report_name = report?.name || "";
        state.config.report_url = report?.web_url || null;
        state.config.embed_url = report?.embed_url || "";
        state.config.dataset_id = report?.dataset_id || null;
        state.config.workspace_id = state.workspace?.id || state.config.workspace_id;
        state.config.workspace_name = state.workspace?.name || state.config.workspace_name;
        state.config.page_name = "";
        state.config.page_display_name = "";
        state.config.visual_name = "";
        state.config.visual_title = "";
        state.config.visual_type = "";
        state.config.owner_name = report?.owner_name || "";
        state.pages = [];
        state.visuals = [];
        state.preview = null;
    });
    $("#rec-next-report")?.addEventListener("click", async () => {
        if (!state.config.report_id) { state.error = "Choose a Power BI report."; _recRenderBuilder(); return; }
        await _recRunLoading("Loading report pages...", async () => {
            const data = await _recRequest(`/api/recurrences/reports/${encodeURIComponent(state.config.report_id)}/pages?workspace_id=${encodeURIComponent(state.config.workspace_id)}`);
            state.pages = data.pages || [];
            if (!state.pages.some(page => page.name === state.config.page_name)) {
                state.config.page_name = "";
                state.config.page_display_name = "";
            }
            state.step = 2;
        });
    });
    $("#rec-back-page")?.addEventListener("click", () => { state.step = 1; state.error = ""; _recRenderBuilder(); });
    $("#rec-page-select")?.addEventListener("change", event => {
        const page = state.pages.find(item => item.name === event.target.value);
        state.config.page_name = page?.name || "";
        state.config.page_display_name = page?.display_name || "";
        state.config.visual_name = "";
        state.config.visual_title = "";
        state.config.visual_type = "";
        state.visuals = [];
        state.preview = null;
        _recRenderBuilder();
    });
    $("#rec-load-visuals")?.addEventListener("click", async () => {
        if (!state.config.page_name) return;
        await _recRunLoading("Loading live table visuals...", async () => {
            const data = await _recRequest("/api/recurrences/visuals/discover", {
                method: "POST",
                body: JSON.stringify({ report_id: state.config.report_id, embed_url: state.config.embed_url, page_name: state.config.page_name }),
            });
            state.visuals = data.visuals || [];
            const selectedVisual = state.visuals.find(
                visual => visual.name === state.config.visual_name && visual.is_table
            );
            if (!selectedVisual) {
                state.config.visual_name = "";
                state.config.visual_title = "";
                state.config.visual_type = "";
            } else {
                state.config.visual_title = selectedVisual.title || "";
                state.config.visual_type = selectedVisual.type || state.config.visual_type;
            }
        });
    });
    document.querySelectorAll('input[name="rec-visual"]').forEach(input => input.addEventListener("change", () => {
        const visual = state.visuals.find(item => item.name === input.value);
        state.config.visual_name = visual?.name || "";
        state.config.visual_title = visual?.title || "";
        state.config.visual_type = visual?.type || "";
        _recRenderBuilder();
    }));
    $("#rec-next-preview")?.addEventListener("click", async () => {
        if (!state.config.visual_name) return;
        await _recRunLoading("Exporting a live data preview...", async () => {
            state.preview = await _recRequest("/api/recurrences/preview", {
                method: "POST",
                body: JSON.stringify({
                    report_id: state.config.report_id,
                    embed_url: state.config.embed_url,
                    page_name: state.config.page_name,
                    visual_name: state.config.visual_name,
                    workspace_id: state.config.workspace_id,
                    dataset_id: state.config.dataset_id,
                }),
            });
            if (state.preview.visual?.title) {
                state.config.visual_title = state.preview.visual.title;
                state.config.visual_type = state.preview.visual.type || state.config.visual_type;
            }
            if (!state.preview.columns.includes(state.config.group_column)) {
                state.config.group_column = "";
                state.config.groups = [];
            } else {
                _recMergeGroups(state);
            }
            state.config.rules = state.config.rules.filter(rule => state.preview.columns.includes(rule.column_name));
            state.step = 3;
        });
    });
    $("#rec-back-visual")?.addEventListener("click", () => { state.step = 2; state.error = ""; _recRenderBuilder(); });
    $("#rec-delivery-mode")?.addEventListener("change", event => {
        state.config.delivery_mode = event.target.value;
        state.error = "";
        _recRenderBuilder();
    });
    $("#rec-single-recipients")?.addEventListener("input", event => {
        state.config.recipients = event.target.value;
    });
    $("#rec-group-column")?.addEventListener("change", event => {
        const changed = state.config.group_column !== event.target.value;
        state.config.group_column = event.target.value;
        _recMergeGroups(state, changed);
        _recRenderBuilder();
    });
    document.querySelectorAll(".rec-group-enabled").forEach(input => input.addEventListener("change", () => {
        state.config.groups[Number(input.dataset.index)].enabled = input.checked;
    }));
    document.querySelectorAll(".rec-group-name").forEach(input => input.addEventListener("input", () => {
        state.config.groups[Number(input.dataset.index)].display_name = input.value;
    }));
    document.querySelectorAll(".rec-group-recipients").forEach(input => input.addEventListener("input", () => {
        state.config.groups[Number(input.dataset.index)].recipients = input.value;
    }));
    $("#rec-add-rule")?.addEventListener("click", () => {
        const firstColumn = state.preview?.columns?.[0] || "";
        state.config.rules.push({ column_name: firstColumn, operator: "gt", compare_value: "" });
        _recRenderBuilder();
    });
    document.querySelectorAll(".rec-rule-column").forEach(input => input.addEventListener("change", () => {
        state.config.rules[Number(input.dataset.index)].column_name = input.value;
    }));
    document.querySelectorAll(".rec-rule-operator").forEach(input => input.addEventListener("change", () => {
        const rule = state.config.rules[Number(input.dataset.index)];
        rule.operator = input.value;
        if (["is_empty", "not_empty"].includes(rule.operator)) rule.compare_value = "";
        _recRenderBuilder();
    }));
    document.querySelectorAll(".rec-rule-value").forEach(input => input.addEventListener("input", () => {
        state.config.rules[Number(input.dataset.index)].compare_value = input.value;
    }));
    document.querySelectorAll(".rec-rule-remove").forEach(button => button.addEventListener("click", () => {
        state.config.rules.splice(Number(button.dataset.index), 1);
        _recRenderBuilder();
    }));
    $("#rec-next-schedule")?.addEventListener("click", () => {
        const error = _recValidateDelivery(state);
        if (error) { state.error = error; _recRenderBuilder(); return; }
        state.error = "";
        state.step = 4;
        _recRenderBuilder();
    });
    $("#rec-back-rules")?.addEventListener("click", () => { state.step = 3; state.error = ""; _recRenderBuilder(); });
    $("#rec-name")?.addEventListener("input", event => { state.config.name = event.target.value; });
    $("#rec-owner")?.addEventListener("change", event => { state.config.owner_name = event.target.value; });
    $("#rec-enabled")?.addEventListener("change", event => { state.config.enabled = event.target.checked; });
    $("#rec-subject")?.addEventListener("input", event => { state.config.subject_template = event.target.value; });
    $("#rec-alert-message")?.addEventListener("input", event => { state.config.alert_message = event.target.value; });
    $("#rec-send-time")?.addEventListener("change", event => { state.config.send_time = event.target.value; });
    $("#rec-month-day")?.addEventListener("input", event => { state.config.month_day = Number(event.target.value || 1); });
    $("#rec-frequency")?.addEventListener("change", event => {
        state.config.recurrence = event.target.value;
        if (state.config.recurrence === "weekly" && !state.config.weekdays.length) state.config.weekdays = ["monday"];
        _recRenderBuilder();
    });
    document.querySelectorAll(".rec-weekday-input").forEach(input => input.addEventListener("change", () => {
        state.config.weekdays = [...document.querySelectorAll(".rec-weekday-input:checked")].map(item => item.value);
    }));
    $("#rec-save")?.addEventListener("click", async () => {
        if (!state.config.name.trim()) { state.error = "Enter a recurrence name."; _recRenderBuilder(); return; }
        if (!state.config.owner_name) { state.error = "Choose an alert owner."; _recRenderBuilder(); return; }
        const alertOwner = state.people.find(person => person.name === state.config.owner_name);
        if (!alertOwner?.email) {
            state.error = `Alert owner '${state.config.owner_name}' needs an email in Management > Create > People.`;
            _recRenderBuilder();
            return;
        }
        if (!state.config.subject_template.trim()) { state.error = "Enter an email subject."; _recRenderBuilder(); return; }
        if (state.config.recurrence === "weekly" && !state.config.weekdays.length) { state.error = "Choose at least one weekday."; _recRenderBuilder(); return; }
        const deliveryError = _recValidateDelivery(state);
        if (deliveryError) { state.error = deliveryError; _recRenderBuilder(); return; }
        await _recRunLoading(state.id ? "Saving recurrence changes..." : "Creating recurrence...", async () => {
            const method = state.id ? "PUT" : "POST";
            const path = state.id ? `/api/recurrences/${state.id}` : "/api/recurrences";
            await _recRequest(path, { method, body: JSON.stringify(_recPayload(state)) });
            toast(state.id ? "Recurrence saved" : "Recurrence created");
            window._recBuilder = null;
            await navigate("recurrences");
        });
    });
}

async function _recLoadHistory(id, row) {
    try {
        const runs = await _recRequest(`/api/recurrences/${id}/runs`);
        if (!runs.length) {
            row.querySelector("td").innerHTML = '<div class="rec-empty"><strong>No run history yet</strong>Use Create drafts or Run now to test this recurrence.</div>';
            return;
        }
        const body = runs.map(run => `
            <tr>
                <td>${_recStatusHtml(run.status)}</td>
                <td>${esc(run.trigger_type)}</td>
                <td>${esc(_recDateTime(run.started_at))}</td>
                <td>${Number(run.exported_rows || 0).toLocaleString()}</td>
                <td>${Number(run.matched_rows || 0).toLocaleString()}</td>
                <td>${Number(run.email_count || 0).toLocaleString()}</td>
                <td class="rec-history-error">${esc(run.error || "")}</td>
            </tr>`).join("");
        row.querySelector("td").innerHTML = `<div class="rec-history-wrap"><table class="rec-history-table"><thead><tr><th>Status</th><th>Trigger</th><th>Started</th><th>Exported</th><th>Matched</th><th>Emails</th><th>Error</th></tr></thead><tbody>${body}</tbody></table></div>`;
    } catch (error) {
        row.querySelector("td").innerHTML = `<div class="rec-inline-error">Run history could not be loaded. ${esc(error.message)}</div>`;
    }
}

function bindRecurrencesPage() {
    const create = () => _recOpenBuilder();
    $("#rec-create")?.addEventListener("click", create);
    $("#rec-create-empty")?.addEventListener("click", create);
    document.querySelectorAll(".rec-edit").forEach(button => button.addEventListener("click", () => {
        const item = (window._recurrences || []).find(value => String(value.id) === button.dataset.id);
        if (item) _recOpenBuilder(item);
    }));
    document.querySelectorAll(".rec-draft").forEach(button => button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            const result = await _recRequest(`/api/recurrences/${button.dataset.id}/run`, { method: "POST", body: JSON.stringify({ mode: "draft" }) });
            toast(result.email_count ? `${result.email_count} Outlook draft${result.email_count === 1 ? "" : "s"} launched` : "No matching rows. No drafts created.");
            await navigate("recurrences");
        } catch (error) {
            button.disabled = false;
            toast("Draft test failed: " + error.message);
        }
    }));
    document.querySelectorAll(".rec-run").forEach(button => button.addEventListener("click", async () => {
        const item = (window._recurrences || []).find(value => String(value.id) === button.dataset.id);
        if (!item || !confirm(`Run '${item.name}' now and send all matching subgroup emails through Outlook?`)) return;
        button.disabled = true;
        try {
            const result = await _recRequest(`/api/recurrences/${button.dataset.id}/run`, { method: "POST", body: JSON.stringify({ mode: "send" }) });
            toast(result.email_count ? `${result.email_count} email${result.email_count === 1 ? "" : "s"} launched through Outlook` : "No matching rows. No emails sent.");
            await navigate("recurrences");
        } catch (error) {
            button.disabled = false;
            toast("Recurrence failed: " + error.message);
        }
    }));
    document.querySelectorAll(".rec-delete").forEach(button => button.addEventListener("click", async () => {
        const item = (window._recurrences || []).find(value => String(value.id) === button.dataset.id);
        if (!item || !confirm(`Delete recurrence '${item.name}' and its run history? This cannot be undone.`)) return;
        try {
            await _recRequest(`/api/recurrences/${button.dataset.id}`, { method: "DELETE" });
            toast("Recurrence deleted");
            await navigate("recurrences");
        } catch (error) {
            toast("Delete failed: " + error.message);
        }
    }));
    document.querySelectorAll(".rec-history").forEach(button => button.addEventListener("click", async () => {
        const row = $(`#rec-history-${button.dataset.id}`);
        if (!row) return;
        row.hidden = !row.hidden;
        button.textContent = row.hidden ? "History" : "Hide history";
        if (!row.hidden && !row.dataset.loaded) {
            row.dataset.loaded = "1";
            await _recLoadHistory(button.dataset.id, row);
        }
    }));
}


// ── Full Export ──

async function renderExport() {
    return `
    <div class="page-header">
        <h1>Full Export</h1>
        <span class="subtitle">Select sections to export as markdown</span>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:1rem;margin-bottom:1rem">
        <fieldset style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;min-width:220px;flex:1">
            <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">PBI Reports</legend>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="reports-overview" checked> Report overview (name, owner, status, frequency, pages, sources)
            </label>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="visuals-fields"> Visuals and fields (visual -> table.field mapping per page)
            </label>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="measures-dax"> Measures and DAX expressions
            </label>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="columns"> Table columns (all columns per report table)
            </label>
        </fieldset>
        <fieldset style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;min-width:220px;flex:1">
            <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">Data Sources</legend>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="sources" checked> All sources (type, status, refresh, connections)
            </label>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="lineage"> Lineage map (source -> report edges)
            </label>
        </fieldset>
        <fieldset style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;min-width:220px;flex:1">
            <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">Scripts & Schedules</legend>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="scripts"> Python scripts (path, owner, tables read/written)
            </label>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="script-code"> Python scripts - full source code
            </label>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="scheduled-tasks"> Scheduled tasks (task scheduler entries)
            </label>
        </fieldset>
        <fieldset style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;min-width:220px;flex:1">
            <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">Diagnostics</legend>
            <label style="display:block;margin:0.3rem 0;font-size:0.8rem;cursor:pointer">
                <input type="checkbox" class="export-opt" data-section="diagnostic"> Diagnostic report (debugging info for troubleshooting)
            </label>
        </fieldset>
    </div>
    <div style="margin-bottom:0.75rem;display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
        <button class="btn-export" id="btn-generate-export" style="float:none">Generate Export</button>
        <button class="btn-export" id="btn-copy-export" style="float:none;display:none">Copy to Clipboard</button>
        <button class="btn-outline" id="btn-select-all" style="font-size:0.78rem">Select all</button>
        <button class="btn-outline" id="btn-select-none" style="font-size:0.78rem">Select none</button>
        <span id="export-status" style="font-size:0.78rem;color:var(--text-dim)"></span>
    </div>
    <textarea id="export-output" readonly
        style="width:100%;min-height:500px;font-family:monospace;font-size:0.78rem;padding:0.75rem;
        background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;
        resize:vertical;white-space:pre;tab-size:4;line-height:1.5"
        placeholder="Select sections above, then click 'Generate Export'."></textarea>
    `;
}

function bindExportPage() {
    const btnGenerate = document.getElementById("btn-generate-export");
    const btnCopy = document.getElementById("btn-copy-export");
    const textarea = document.getElementById("export-output");
    const status = document.getElementById("export-status");
    const btnAll = document.getElementById("btn-select-all");
    const btnNone = document.getElementById("btn-select-none");

    if (!btnGenerate) return;

    const checkboxes = () => document.querySelectorAll(".export-opt");

    btnAll.addEventListener("click", () => checkboxes().forEach(c => c.checked = true));
    btnNone.addEventListener("click", () => checkboxes().forEach(c => c.checked = false));

    function getSelected() {
        const s = new Set();
        checkboxes().forEach(c => { if (c.checked) s.add(c.dataset.section); });
        return s;
    }

    btnGenerate.addEventListener("click", async () => {
        const selected = getSelected();
        if (selected.size === 0) { toast("Select at least one section"); return; }

        btnGenerate.disabled = true;
        btnGenerate.textContent = "Fetching data...";
        status.textContent = "";
        btnCopy.style.display = "none";

        try {
            // Determine which API calls we need
            const needReports = selected.has("reports-overview") || selected.has("visuals-fields");
            const needSources = selected.has("sources");
            const needLineage = selected.has("lineage") || selected.has("reports-overview") || selected.has("sources");
            const needVisuals = selected.has("visuals-fields") || selected.has("reports-overview");
            const needMeasures = selected.has("measures-dax");
            const needColumns = selected.has("columns");
            const needScripts = selected.has("scripts");
            const needScriptCode = selected.has("script-code");
            const needSchTasks = selected.has("scheduled-tasks");
            const needDiagnostic = selected.has("diagnostic");

            // Fetch base data in parallel
            status.textContent = "Fetching base data...";
            const fetches = {};
            if (needReports) fetches.reports = api("/api/reports");
            if (needSources) fetches.sources = api("/api/sources");
            if (needLineage) fetches.edges = api("/api/lineage");
            if (needMeasures) fetches.measures = api("/api/reports/all-measures");
            if (needColumns) fetches.columns = api("/api/reports/all-columns");
            if (needScripts) fetches.scripts = api("/api/scripts");
            if (needScriptCode) fetches.scriptCode = api("/api/scripts/export-code");
            if (needSchTasks) fetches.schTasks = api("/api/scheduled-tasks");
            if (needDiagnostic) fetches.diagnostic = api("/api/scanner/diagnostic");

            const keys = Object.keys(fetches);
            const values = await Promise.all(Object.values(fetches));
            const data = {};
            keys.forEach((k, i) => data[k] = values[i]);

            const reports = data.reports || [];
            const sources = data.sources || [];
            const edges = data.edges || [];

            // Fetch visuals per report if needed
            let visualsMap = {};
            if (needVisuals && reports.length > 0) {
                status.textContent = `Fetching visuals for ${reports.length} reports...`;
                const promises = reports.map(r =>
                    api(`/api/reports/${r.id}/visuals`)
                        .then(v => { visualsMap[r.id] = v; })
                        .catch(() => { visualsMap[r.id] = []; })
                );
                await Promise.all(promises);
            }

            // Build lineage maps
            const reportSourceMap = {};
            const sourceReportMap = {};
            edges.forEach(e => {
                if (!reportSourceMap[e.report_id]) reportSourceMap[e.report_id] = [];
                if (!reportSourceMap[e.report_id].includes(e.source_name))
                    reportSourceMap[e.report_id].push(e.source_name);
                if (!sourceReportMap[e.source_id]) sourceReportMap[e.source_id] = [];
                if (!sourceReportMap[e.source_id].includes(e.report_name))
                    sourceReportMap[e.source_id].push(e.report_name);
            });

            // Build markdown
            status.textContent = "Building markdown...";
            const now = new Date();
            const dateStr = now.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
                + " " + now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

            const sections = [];
            let md = `# Data Governance Export\nGenerated: ${dateStr}\n`;
            md += `Sections: ${[...selected].join(", ")}\n\n`;

            // ── Reports Overview ──
            if (selected.has("reports-overview")) {
                md += `## Reports (${reports.length})\n\n`;
                for (const r of reports) {
                    md += `### ${r.name}\n`;
                    md += `- Owner: ${r.owner || "-"}\n`;
                    md += `- Business Owner: ${r.business_owner || "-"}\n`;
                    md += `- Status: ${r.status || "-"}\n`;
                    md += `- Frequency: ${r.frequency || "-"}\n`;
                    const srcNames = reportSourceMap[r.id] || [];
                    md += `- Sources: ${srcNames.length > 0 ? srcNames.join(", ") : "-"}\n`;
                    const pages = (visualsMap[r.id] || []).map(p => p.page_name).filter(Boolean);
                    md += `- Pages: ${pages.length > 0 ? pages.join(", ") : "-"}\n`;
                    md += `- Unused: ${r.unused_pct != null ? r.unused_pct + "%" : "-"}\n`;
                    md += "\n";
                }
                sections.push(`${reports.length} reports`);
            }

            // ── Visuals & Fields ──
            if (selected.has("visuals-fields")) {
                md += `## Visuals & Fields\n\n`;
                for (const r of reports) {
                    const rpages = visualsMap[r.id] || [];
                    if (rpages.length === 0) continue;
                    md += `### ${r.name}\n\n`;
                    for (const pg of rpages) {
                        md += `#### Page: ${pg.page_name}\n`;
                        for (const v of pg.visuals) {
                            const label = v.title ? `${v.visual_type} - "${v.title}"` : v.visual_type;
                            md += `- **${label}**\n`;
                            for (const f of v.fields) {
                                md += `  - ${f.table}.${f.field}\n`;
                            }
                        }
                        md += "\n";
                    }
                }
                const totalVisuals = reports.reduce((sum, r) =>
                    sum + (visualsMap[r.id] || []).reduce((s, p) => s + p.visuals.length, 0), 0);
                sections.push(`${totalVisuals} visuals`);
            }

            // ── Measures & DAX ──
            if (selected.has("measures-dax")) {
                const allMeasures = data.measures || [];
                md += `## Measures & DAX (${allMeasures.length})\n\n`;
                // Group by report
                const byReport = {};
                allMeasures.forEach(m => {
                    if (!byReport[m.report_name]) byReport[m.report_name] = {};
                    if (!byReport[m.report_name][m.table_name]) byReport[m.report_name][m.table_name] = [];
                    byReport[m.report_name][m.table_name].push(m);
                });
                for (const [rptName, tables] of Object.entries(byReport).sort()) {
                    md += `### ${rptName}\n\n`;
                    for (const [tblName, measures] of Object.entries(tables).sort()) {
                        md += `**Table: ${tblName}**\n\n`;
                        for (const m of measures) {
                            md += `\`${m.measure_name}\`\n`;
                            if (m.measure_dax) {
                                md += "```dax\n" + m.measure_dax + "\n```\n";
                            }
                            md += "\n";
                        }
                    }
                }
                sections.push(`${allMeasures.length} measures`);
            }

            // ── Table Columns ──
            if (selected.has("columns")) {
                const allCols = data.columns || [];
                md += `## Table Columns (${allCols.length})\n\n`;
                const byReport = {};
                allCols.forEach(c => {
                    if (!byReport[c.report_name]) byReport[c.report_name] = {};
                    if (!byReport[c.report_name][c.table_name]) byReport[c.report_name][c.table_name] = [];
                    byReport[c.report_name][c.table_name].push(c.column_name);
                });
                for (const [rptName, tables] of Object.entries(byReport).sort()) {
                    md += `### ${rptName}\n\n`;
                    for (const [tblName, cols] of Object.entries(tables).sort()) {
                        md += `**${tblName}:** ${cols.join(", ")}\n\n`;
                    }
                }
                sections.push(`${allCols.length} columns`);
            }

            // ── Data Sources ──
            if (selected.has("sources")) {
                md += `## Data Sources (${sources.length})\n\n`;
                // Group by type
                const byType = {};
                sources.forEach(s => {
                    const t = s.type || "unknown";
                    if (!byType[t]) byType[t] = [];
                    byType[t].push(s);
                });
                for (const [typeName, srcs] of Object.entries(byType).sort()) {
                    md += `### ${typeName} (${srcs.length})\n\n`;
                    for (const s of srcs) {
                        md += `**${s.name}**\n`;
                        md += `- Owner: ${s.owner || "-"}\n`;
                        md += `- Status: ${s.status || "-"}\n`;
                        md += `- Refresh: ${s.refresh_schedule || "-"}\n`;
                        md += `- Last Data: ${s.last_updated || "-"}\n`;
                        const rptNames = sourceReportMap[s.id] || [];
                        md += `- Reports: ${rptNames.length > 0 ? rptNames.join(", ") : "-"}\n`;
                        md += "\n";
                    }
                }
                sections.push(`${sources.length} sources`);
            }

            // ── Lineage ──
            if (selected.has("lineage")) {
                md += `## Lineage (${edges.length} edges)\n\n`;
                for (const e of edges) {
                    md += `- ${e.source_name} -> ${e.report_name}\n`;
                }
                md += "\n";
                sections.push(`${edges.length} edges`);
            }

            // ── Python Scripts ──
            if (selected.has("scripts")) {
                const scripts = data.scripts || [];
                md += `## Python Scripts (${scripts.length})\n\n`;
                for (const s of scripts) {
                    md += `**${s.display_name}**\n`;
                    md += `- Path: ${s.path || "-"}\n`;
                    md += `- Owner: ${s.owner || "-"}\n`;
                    md += `- Modified: ${s.last_modified || "-"}\n`;
                    md += `- Size: ${s.file_size ? Math.round(s.file_size / 1024) + " KB" : "-"}\n`;
                    if (s.tables_written && s.tables_written.length > 0)
                        md += `- Writes to: ${s.tables_written.join(", ")}\n`;
                    if (s.tables_read && s.tables_read.length > 0)
                        md += `- Reads from: ${s.tables_read.join(", ")}\n`;
                    md += "\n";
                }
                sections.push(`${scripts.length} scripts`);
            }

            // ── Script Source Code ──
            if (selected.has("script-code")) {
                const codeData = data.scriptCode || {};
                md += `## Python Script Source Code (${codeData.count || 0} files)\n\n`;
                md += "```\n" + (codeData.code || "No scripts found") + "\n```\n\n";
                sections.push(`${codeData.count || 0} script source files`);
            }

            // ── Scheduled Tasks ──
            if (selected.has("scheduled-tasks")) {
                const tasks = data.schTasks || [];
                md += `## Scheduled Tasks (${tasks.length})\n\n`;
                for (const t of tasks) {
                    md += `**${t.task_name}**\n`;
                    md += `- Path: ${t.task_path || "-"}\n`;
                    md += `- Status: ${t.status || "-"}\n`;
                    md += `- Enabled: ${t.enabled ? "Yes" : "No"}\n`;
                    md += `- Schedule: ${t.schedule_type || "-"}\n`;
                    md += `- Last Run: ${t.last_run_time && !/1999/.test(t.last_run_time) ? t.last_run_time : "Never"}\n`;
                    md += `- Last Result: ${t.last_result || "-"}\n`;
                    md += `- Next Run: ${t.next_run_time || "-"}\n`;
                    md += `- Command: ${t.action_command || "-"}\n`;
                    if (t.action_args) md += `- Args: ${t.action_args}\n`;
                    if (t.script_name) md += `- Linked Script: ${t.script_name}\n`;
                    md += "\n";
                }
                sections.push(`${tasks.length} scheduled tasks`);
            }

            // ── Diagnostic Report ──
            if (selected.has("diagnostic")) {
                const d = data.diagnostic || {};
                md += `## Diagnostic Report\n\n`;

                // Environment
                md += `### Environment\n\n`;
                const env = d.environment || {};
                for (const [k, v] of Object.entries(env)) {
                    md += `- **${k}:** ${v}\n`;
                }
                md += "\n";

                // Row counts
                md += `### Table Row Counts\n\n`;
                md += `| Table | Rows |\n|-------|------|\n`;
                for (const [t, c] of Object.entries(d.row_counts || {})) {
                    md += `| ${t} | ${c} |\n`;
                }
                md += "\n";

                // Source type distribution
                md += `### Source Type Distribution\n\n`;
                for (const [t, c] of Object.entries(d.source_type_distribution || {})) {
                    md += `- ${t}: ${c}\n`;
                }
                md += "\n";

                // Probe status distribution
                md += `### Probe Status Distribution\n\n`;
                for (const [s, c] of Object.entries(d.probe_status_distribution || {})) {
                    md += `- ${s}: ${c}\n`;
                }
                md += "\n";

                // Sources with IP prefix
                const ipSrcs = d.sources_with_ip_prefix || [];
                md += `### Sources with IP Prefix Still in Name (${ipSrcs.length})\n\n`;
                if (ipSrcs.length > 0) {
                    for (const s of ipSrcs) md += `- [${s.id}] ${s.name}\n`;
                } else {
                    md += `None found (good).\n`;
                }
                md += "\n";

                // Potential duplicates
                const dups = d.potential_duplicate_sources || [];
                md += `### Potential Duplicate Sources (${dups.length})\n\n`;
                if (dups.length > 0) {
                    for (const d2 of dups) md += `- [${d2.id1}] ${d2.name1} <-> [${d2.id2}] ${d2.name2}\n`;
                } else {
                    md += `None found.\n`;
                }
                md += "\n";

                // Broken FK references
                const fks = d.broken_fk_references || {};
                md += `### Broken FK References\n\n`;
                for (const [table, rows] of Object.entries(fks)) {
                    md += `**${table}:** ${rows.length} broken\n`;
                    for (const r of rows.slice(0, 20)) {
                        md += `  - ${JSON.stringify(r)}\n`;
                    }
                    if (rows.length > 20) md += `  - ... and ${rows.length - 20} more\n`;
                }
                md += "\n";

                // All sources
                md += `### All Sources (${(d.sources || []).length})\n\n`;
                md += `| ID | Name | Type | Discovered By | Status | Reports | Scripts | Dep From | Dep To |\n`;
                md += `|----|------|------|---------------|--------|---------|---------|----------|--------|\n`;
                for (const s of (d.sources || [])) {
                    md += `| ${s.id} | ${s.name} | ${s.type} | ${s.discovered_by} | ${s.probe_status} | ${s.report_count} | ${s.script_ref_count} | ${s.dep_from_count} | ${s.dep_to_count} |\n`;
                }
                md += "\n";

                // Source dependencies
                const deps = d.source_dependencies || [];
                md += `### Source Dependencies (${deps.length})\n\n`;
                if (deps.length > 0) {
                    md += `| Source | Depends On | Discovered By |\n|--------|------------|---------------|\n`;
                    for (const dep of deps) {
                        md += `| ${dep.source_name || dep.source_id} | ${dep.depends_on_name || dep.depends_on_id} | ${dep.discovered_by} |\n`;
                    }
                }
                md += "\n";

                // Script tables
                const stAll = d.script_tables || [];
                const stUnlinked = d.unlinked_script_tables || [];
                md += `### Script-to-Table Mappings (${stAll.length} total, ${stUnlinked.length} unlinked)\n\n`;
                md += `| Script | Table | Direction | Source ID | Matched Source |\n`;
                md += `|--------|-------|-----------|----------|----------------|\n`;
                for (const st of stAll) {
                    md += `| ${st.script} | ${st.table} | ${st.direction} | ${st.source_id || "-"} | ${st.matched_source || "UNLINKED"} |\n`;
                }
                md += "\n";

                // Reports with no sources
                const noSrc = d.reports_with_no_sources || [];
                md += `### Reports with No Sources (${noSrc.length})\n\n`;
                for (const r of noSrc) md += `- [${r.id}] ${r.name}\n`;
                md += "\n";

                // Recent scans
                md += `### Recent Scan Runs\n\n`;
                for (const scan of (d.recent_scans || [])) {
                    md += `**Scan #${scan.id}** (${scan.status})\n`;
                    md += `- Started: ${scan.started_at || "-"}\n`;
                    md += `- Finished: ${scan.finished_at || "-"}\n`;
                    md += `- Reports: ${scan.reports_scanned}, Sources: ${scan.sources_found}, New: ${scan.new_sources}, Changed: ${scan.changed_queries}, Broken: ${scan.broken_refs}\n`;
                    if (scan.log) md += `- Log:\n\`\`\`\n${scan.log}\n\`\`\`\n`;
                    md += "\n";
                }

                sections.push("diagnostic");
            }

            textarea.value = md;
            btnCopy.style.display = "";
            status.textContent = `Done - ${sections.join(", ")}`;
        } catch (err) {
            status.textContent = "Error: " + err.message;
        } finally {
            btnGenerate.disabled = false;
            btnGenerate.textContent = "Generate Export";
        }
    });

    btnCopy.addEventListener("click", () => {
        if (!textarea.value) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(textarea.value).then(() => {
                toast("Copied to clipboard");
            }).catch(() => {
                textarea.select();
                document.execCommand("copy");
                toast("Copied to clipboard");
            });
        } else {
            textarea.select();
            document.execCommand("copy");
            toast("Copied to clipboard");
        }
    });
}


// ── Scripts ──

function formatFileSize(bytes) {
    if (bytes == null) return "-";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
}

function truncatePath(path, maxLen) {
    if (!path) return "-";
    if (path.length <= maxLen) return path;
    return "..." + path.slice(path.length - maxLen + 3);
}

// Derive script category from its data (SQL writes vs Excel)
// SQL = writes to PostgreSQL tables/MVs. Excel = no SQL writes.
// SQL takes precedence if a script does both.
function _scriptCategory(script) {
    if (!script) return "Other";
    const writes = script.tables_written || [];
    const sqlWrites = writes.filter(t => !t.startsWith("["));
    const fileWrites = writes.filter(t => t.startsWith("[excel]") || t.startsWith("[csv]") || t.startsWith("[parquet]") || t.startsWith("[json]"));
    if (sqlWrites.length > 0) return "Data to SQL";
    if (fileWrites.length > 0) return "Data to Excel";
    if (writes.length > 0) return "Data to Excel";
    return "Other";
}

function _refType(name) {
    if (name.startsWith("[excel]")) return { label: name.slice(7), type: "excel", cls: "badge-yellow" };
    if (name.startsWith("[csv]")) return { label: name.slice(5), type: "csv", cls: "badge-purple" };
    if (name.startsWith("[parquet]")) return { label: name.slice(9), type: "parquet", cls: "badge-purple" };
    if (name.startsWith("[json]")) return { label: name.slice(6), type: "json", cls: "badge-purple" };
    if (name.startsWith("[web-scraping]")) return { label: name.slice(14), type: "web-scraping", cls: "badge-dim" };
    if (name.startsWith("[web-download]")) return { label: name.slice(14), type: "web-download", cls: "badge-dim" };
    if (name.startsWith("[web]")) return { label: name.slice(5), type: "web", cls: "badge-dim" };
    if (name.startsWith("[pdf]")) return { label: name.slice(5), type: "pdf", cls: "badge-muted" };
    if (name.startsWith("[text]")) return { label: name.slice(6), type: "text", cls: "badge-muted" };
    return { label: name, type: "sql", cls: "" };
}

const _CATEGORY_COLORS = {
    "Data to SQL": "badge-green",
    "Data to Excel": "badge-yellow",
    "Other": "badge-muted",
};

// Classify a table reference by its PostgreSQL schema or pattern
function _classifyTable(tableName) {
    const t = tableName.toLowerCase();
    if (t.startsWith("reporting.")) return { label: "Reporting", cls: "badge-blue" };
    if (t.startsWith("smartswitch.")) return { label: "SmartSwitch", cls: "badge-purple" };
    if (t.startsWith("device_health.")) return { label: "Device Health", cls: "badge-purple" };
    if (t.startsWith("do_not_use_tables.")) return { label: "Internal", cls: "badge-dim" };
    if (t.includes("mdscm") || t.includes("gscm")) return { label: "GSCM", cls: "badge-green" };
    if (t.includes("asap")) return { label: "ASAP", cls: "badge-yellow" };
    if (/\.csv$/i.test(t) || /csv/i.test(t)) return { label: "CSV", cls: "badge-muted" };
    if (t.includes(".") && !t.includes("/") && !t.includes("\\")) return { label: "Postgres", cls: "badge-blue" };
    return { label: "Other", cls: "badge-muted" };
}

async function renderScripts() {
    const showArchived = _isShowingArchived("scripts");
    const [scripts, options] = await Promise.all([
        api("/api/scripts" + (showArchived ? "?include_archived=true" : "")),
        api("/api/create/options"),
    ]);
    const people = options.people || [];

    const cols = [
        { key: "machine_alias", label: "Machine", width: COL_W.sm, render: s => {
            const alias = s.machine_alias || s.hostname || "Local";
            return `<span class="badge badge-muted" style="font-size:0.68rem" title="${esc(s.hostname || '')}">${esc(alias)}</span>`;
        }, sortVal: s => s.machine_alias || s.hostname || "" },
        { key: "category", label: "Category", width: COL_W.sm, render: s => {
            const cat = _scriptCategory(s);
            const cls = _CATEGORY_COLORS[cat] || "badge-muted";
            return `<span class="badge ${cls}" style="font-size:0.68rem">${esc(cat)}</span>`;
        }, sortVal: s => _scriptCategory(s) },
        { key: "display_name", label: "Script", width: COL_W.lg, render: s => `<strong>${esc(s.display_name)}</strong>`, sortVal: s => s.display_name || "" },
        { key: "_scope", label: "Role", width: COL_W.sm, render: s => {
            const connected = (s.tables_written || []).length || (s.tables_read || []).length;
            return connected ? '<span class="badge badge-blue">Pipeline</span>' : '<span class="badge badge-muted">Helper</span>';
        }, sortVal: s => ((s.tables_written || []).length || (s.tables_read || []).length) ? "0_pipeline" : "1_helper" },
        { key: "path", label: "Path", width: COL_W.xl, render: s => {
            const escaped = (s.path || "").replace(/"/g, '&quot;');
            return `<span class="cell-expandable cell-copyable" title="Click to copy path" data-copy="${escaped}" style="font-size:0.75rem;color:var(--text-muted)">${esc(s.path || "-")}</span> ${_viewPathBtn(s.path)}`;
        }, sortVal: s => s.path || "" },
        { key: "owner", label: "Owner", width: COL_W.md, render: s => {
            const opts = people.map(p => `<option value="${esc(p.name)}"${s.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
            return `<select class="freq-select-inline script-owner-select" data-script-id="${s.id}"><option value="">--</option>${opts}</select>`;
        }, sortVal: s => s.owner || "" },
        { key: "tables_written", label: "Writes to", width: COL_W.lg, render: s => {
            const all = s.tables_written || [];
            if (all.length === 0) return '<span style="color:var(--text-dim)">-</span>';
            const sql = all.filter(t => !t.startsWith("["));
            const excel = all.filter(t => t.startsWith("[excel]"));
            const csv = all.filter(t => t.startsWith("[csv]"));
            const parquet = all.filter(t => t.startsWith("[parquet]") || t.startsWith("[json]"));
            const parts = [];
            if (sql.length) parts.push(`<span class="badge badge-red" style="font-size:0.72rem">${sql.length} SQL</span>`);
            if (excel.length) parts.push(`<span class="badge badge-yellow" style="font-size:0.72rem">${excel.length} Excel</span>`);
            if (csv.length) parts.push(`<span class="badge badge-purple" style="font-size:0.72rem">${csv.length} CSV</span>`);
            if (parquet.length) parts.push(`<span class="badge badge-muted" style="font-size:0.72rem">${parquet.length} File</span>`);
            return parts.join(" ") || '<span style="color:var(--text-dim)">-</span>';
        }, sortVal: s => (s.tables_written || []).length, filterVal: s => (s.tables_written || []).join(" "), filterPlaceholder: "Search tables..." },
        { key: "tables_read", label: "Reads from", width: COL_W.lg, render: s => {
            const all = s.tables_read || [];
            if (all.length === 0) return '<span style="color:var(--text-dim)">-</span>';
            const sql = all.filter(t => !t.startsWith("["));
            const excel = all.filter(t => t.startsWith("[excel]"));
            const csv = all.filter(t => t.startsWith("[csv]"));
            const otherFiles = all.filter(t => t.startsWith("[pdf]") || t.startsWith("[parquet]") || t.startsWith("[json]") || t.startsWith("[text]"));
            const scraping = all.filter(t => t.startsWith("[web-scraping]"));
            const download = all.filter(t => t.startsWith("[web-download]") || t.startsWith("[web]"));
            const parts = [];
            if (sql.length) parts.push(`<span class="badge badge-blue" style="font-size:0.72rem">${sql.length} SQL</span>`);
            if (excel.length) parts.push(`<span class="badge badge-yellow" style="font-size:0.72rem">${excel.length} Excel</span>`);
            if (csv.length) parts.push(`<span class="badge badge-purple" style="font-size:0.72rem">${csv.length} CSV</span>`);
            if (otherFiles.length) parts.push(`<span class="badge badge-muted" style="font-size:0.72rem">${otherFiles.length} File</span>`);
            if (scraping.length) parts.push(`<span class="badge badge-dim" style="font-size:0.72rem">${scraping.length} Web Scrape</span>`);
            if (download.length) parts.push(`<span class="badge badge-dim" style="font-size:0.72rem">${download.length} Web DL</span>`);
            return parts.join(" ") || '<span style="color:var(--text-dim)">-</span>';
        }, sortVal: s => (s.tables_read || []).length, filterVal: s => (s.tables_read || []).join(" "), filterPlaceholder: "Search tables..." },
        { key: "last_modified", label: "Modified", width: COL_W.md, render: s => `<span style="color:var(--text-muted)" title="${s.last_modified || ''}">${s.last_modified ? timeAgo(s.last_modified) : "-"}</span>`, sortVal: s => s.last_modified || "" },
        { key: "age_days", label: "Age (days)", width: COL_W.sm, render: s => {
            const d = daysOld(s.last_modified);
            if (d === null) return '<span style="color:var(--text-dim)">-</span>';
            return `<span style="font-weight:600">${d}</span>`;
        }, sortVal: s => daysOld(s.last_modified) ?? 9999 },
        _archiveColDef("script"),
    ];

    const scriptTypeFilter = sessionStorage.getItem("scripts_type_filter") || "all";
    const machineFilter = sessionStorage.getItem("scripts_machine") || "";
    const scopeFilter = sessionStorage.getItem("scripts_scope") || "connected";
    const connectedScripts = scripts.filter(s => (s.tables_written || []).length || (s.tables_read || []).length);
    let filtered = scripts;
    if (scopeFilter === "connected") filtered = filtered.filter(s => (s.tables_written || []).length || (s.tables_read || []).length);
    if (scriptTypeFilter === "sql") filtered = filtered.filter(s => _scriptCategory(s) === "Data to SQL");
    else if (scriptTypeFilter === "excel") filtered = filtered.filter(s => _scriptCategory(s) === "Data to Excel");
    else if (scriptTypeFilter === "other") filtered = filtered.filter(s => _scriptCategory(s) === "Other");
    if (machineFilter) filtered = filtered.filter(s => (s.machine_alias || s.hostname || "Local") === machineFilter);
    const activeCount = filtered.filter(s => !s.archived).length;
    const totalCount = scripts.filter(s => !s.archived).length;
    const sqlCount = scripts.filter(s => !s.archived && _scriptCategory(s) === "Data to SQL").length;
    const excelCount = scripts.filter(s => !s.archived && _scriptCategory(s) === "Data to Excel").length;
    const otherCount = scripts.filter(s => !s.archived && _scriptCategory(s) === "Other").length;

    const allMachines = [...new Set(scripts.map(s => s.machine_alias || s.hostname || "Local"))].sort();
    const machineOpts = allMachines.map(m => `<option value="${esc(m)}"${machineFilter === m ? ' selected' : ''}>${esc(m)}</option>`).join("");

    return `
        <div class="page-header">
            <h1>Scripts</h1>
            <span class="subtitle">${activeCount}${scopeFilter === "connected" || machineFilter || scriptTypeFilter !== 'all' ? ` of ${totalCount}` : ''} scripts${machineFilter ? ` on ${machineFilter}` : ''} - ${connectedScripts.filter(s => !s.archived).length} connected to governed data</span>
            <button class="btn-outline" id="btn-scan-scripts-full" style="margin-left:0.5rem">Full Scan</button>
            <button class="btn-outline" id="btn-scan-scripts-new">Scan New</button>
            <button class="btn-outline" id="btn-reparse-scripts" title="Re-read and re-parse known scripts (no directory walk)">Re-parse</button>
            <button class="btn-outline btn-archive-toggle ${scopeFilter === 'connected' ? 'active' : ''}" id="btn-scripts-scope">${scopeFilter === "connected" ? "Show all scripts" : "Show connected only"}</button>
            <select id="scripts-machine-filter" class="freq-select-inline" style="font-size:0.75rem;margin-left:0.25rem"><option value="">All Machines</option>${machineOpts}</select>
            <button class="btn-outline btn-archive-toggle ${scriptTypeFilter === 'all' ? 'active' : ''}" id="btn-filter-all" style="font-size:0.75rem">All (${totalCount})</button>
            <button class="btn-outline btn-archive-toggle ${scriptTypeFilter === 'sql' ? 'active' : ''}" id="btn-filter-sql" style="font-size:0.75rem">Data to SQL (${sqlCount})</button>
            <button class="btn-outline btn-archive-toggle ${scriptTypeFilter === 'excel' ? 'active' : ''}" id="btn-filter-excel" style="font-size:0.75rem">Data to Excel (${excelCount})</button>
            <button class="btn-outline btn-archive-toggle ${scriptTypeFilter === 'other' ? 'active' : ''}" id="btn-filter-other" style="font-size:0.75rem">Other (${otherCount})</button>
            ${_archiveToggleHtml("scripts")}
            <button class="btn-export" onclick="exportTableCSV('dt-scripts','scripts.csv')">Export CSV</button>
        </div>
        <div id="script-scan-log-wrap" class="scan-log-wrap" style="display:none">
            <button class="scan-log-toggle" id="btn-toggle-scan-log">Scan Log <span class="nav-arrow">&#9662;</span></button>
            <div id="script-scan-log-status" class="scan-log-status"></div>
            <pre id="script-scan-log" class="scan-log-pre" style="display:none"></pre>
        </div>
        ${dataTable("dt-scripts", cols, filtered, { onRowClick: showScriptDetail })}
    `;
}

async function showScriptDetail(script) {
    const existing = $("#script-detail");
    if (existing) existing.remove();

    const [tables, options] = await Promise.all([
        api(`/api/scripts/${script.id}/tables`),
        api("/api/create/options"),
    ]);
    const people = options.people || [];

    const panel = document.createElement("div");
    panel.id = "script-detail";
    panel.className = "source-detail-panel";

    const writeRows = tables.filter(t => t.direction === "write");
    const readRows = tables.filter(t => t.direction === "read");

    const writeBadges = writeRows.length > 0
        ? writeRows.map(t => {
            const ref = _refType(t.table_name);
            const cls = ref.type === "sql" ? "badge-red" : ref.cls;
            if (t.source_id) {
                return `<span class="badge ${cls} script-table-link" style="cursor:pointer;margin:2px" data-source-id="${t.source_id}" title="Click to view source">${esc(ref.label)}</span>`;
            }
            return `<span class="badge ${cls}" style="margin:2px">${esc(ref.label)}</span>`;
        }).join(" ")
        : '<span style="color:var(--text-dim)">None detected</span>';

    const readBadges = readRows.length > 0
        ? readRows.map(t => {
            const ref = _refType(t.table_name);
            const cls = ref.type === "sql" ? "badge-blue" : ref.cls;
            if (t.source_id) {
                return `<span class="badge ${cls} script-table-link" style="cursor:pointer;margin:2px" data-source-id="${t.source_id}" title="Click to view source">${esc(ref.label)}</span>`;
            }
            return `<span class="badge ${cls}" style="margin:2px">${esc(ref.label)}</span>`;
        }).join(" ")
        : '<span style="color:var(--text-dim)">None detected</span>';

    const ownerOpts = people.map(p => `<option value="${esc(p.name)}"${script.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("");

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(script.display_name)}</h2>
            <button class="btn-outline" id="btn-close-script-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Category</div><span class="badge ${_CATEGORY_COLORS[_scriptCategory(script)] || 'badge-muted'}">${esc(_scriptCategory(script))}</span></div>
            <div class="detail-item"><div class="detail-label">Full Path</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${esc(script.path)} ${_viewPathBtn(script.path)}</span></div>
            <div class="detail-item"><div class="detail-label">Owner</div>
                <select class="freq-select-inline script-detail-owner-select" data-script-id="${script.id}">
                    <option value="">--</option>${ownerOpts}
                </select>
            </div>
            <div class="detail-item"><div class="detail-label">File Size</div><span style="color:var(--text)">${formatFileSize(script.file_size)}</span></div>
            <div class="detail-item"><div class="detail-label">Last Modified</div><span style="color:var(--text)">${script.last_modified ? formatDate(script.last_modified) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Last Scanned</div><span style="color:var(--text)">${script.last_scanned ? formatDate(script.last_scanned) : "-"}</span></div>
        </div>

        <h2>Writes to / Refreshes (${writeRows.length})</h2>
        <div style="padding:0.25rem 0">${writeBadges}</div>

        <h2>Reads From (${readRows.length})</h2>
        <div style="padding:0.25rem 0">${readBadges}</div>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Close button
    document.getElementById("btn-close-script-detail").addEventListener("click", () => panel.remove());

    // View path buttons
    panel.querySelectorAll(".view-path-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            try { await apiPostJson("/api/scanner/open-path", { path: btn.dataset.path }); }
            catch { toast("Could not open path (only works on server machine)"); }
        });
    });

    // Owner dropdown in detail panel
    const ownerSelect = panel.querySelector(".script-detail-owner-select");
    if (ownerSelect) {
        ownerSelect.addEventListener("change", async () => {
            try {
                await apiPatch(`/api/scripts/${script.id}`, { owner: ownerSelect.value });
                toast("Owner updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    }

    // Clickable table badges that link to sources
    panel.querySelectorAll(".script-table-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const srcId = parseInt(el.dataset.sourceId);
            try {
                const source = await api(`/api/sources/${srcId}`);
                await navigate("sources");
                showSourceDetail(source);
            } catch (err) {
                toast("Source not found");
            }
        });
    });
}

function bindScriptsPage() {
    document.getElementById("btn-scripts-scope")?.addEventListener("click", () => {
        const current = sessionStorage.getItem("scripts_scope") || "connected";
        sessionStorage.setItem("scripts_scope", current === "connected" ? "all" : "connected");
        navigate("scripts");
    });
    // Machine filter dropdown
    const machSel = document.getElementById("scripts-machine-filter");
    if (machSel) {
        machSel.addEventListener("change", () => {
            sessionStorage.setItem("scripts_machine", machSel.value);
            navigate("scripts");
        });
    }

    // Script type filter buttons
    const filterBtns = {
        "btn-filter-all": "all",
        "btn-filter-sql": "sql",
        "btn-filter-excel": "excel",
        "btn-filter-other": "other",
    };
    for (const [id, val] of Object.entries(filterBtns)) {
        const btn = document.getElementById(id);
        if (btn) {
            btn.addEventListener("click", () => {
                sessionStorage.setItem("scripts_type_filter", val);
                navigate("scripts");
            });
        }
    }

    // Scan buttons - async with live log polling
    const btnFull = document.getElementById("btn-scan-scripts-full");
    const btnNew = document.getElementById("btn-scan-scripts-new");
    const btnReparse = document.getElementById("btn-reparse-scripts");
    const logWrap = document.getElementById("script-scan-log-wrap");
    const logPre = document.getElementById("script-scan-log");
    const logStatus = document.getElementById("script-scan-log-status");
    const btnToggle = document.getElementById("btn-toggle-scan-log");
    let logExpanded = false;

    if (btnToggle) {
        btnToggle.addEventListener("click", () => {
            logExpanded = !logExpanded;
            logPre.style.display = logExpanded ? "" : "none";
            btnToggle.querySelector(".nav-arrow").innerHTML = logExpanded ? "&#9652;" : "&#9662;";
        });
    }

    function _disableScanBtns(label) {
        if (btnFull) { btnFull.disabled = true; btnFull.textContent = label; }
        if (btnNew) { btnNew.disabled = true; }
        if (btnReparse) { btnReparse.disabled = true; }
    }
    function _enableScanBtns() {
        if (btnFull) { btnFull.disabled = false; btnFull.textContent = "Full Scan"; }
        if (btnNew) { btnNew.disabled = false; }
        if (btnReparse) { btnReparse.disabled = false; }
    }

    // Check if a scan is already running on page load
    (async () => {
        try {
            const st = await api("/api/scripts/scan/status");
            if (st.status === "running") {
                logWrap.style.display = "";
                logStatus.innerHTML = '<span class="badge badge-yellow">Scanning...</span>';
                logPre.textContent = (st.log || []).join("\n");
                _disableScanBtns("Scanning...");
                _pollScriptScanLog();
            }
        } catch (_) {}
    })();

    function _startScriptScan(newOnly) {
        return async () => {
            _disableScanBtns("Scanning...");
            logWrap.style.display = "";
            logPre.textContent = "";
            logStatus.innerHTML = '<span class="badge badge-yellow">Scanning...</span>';
            logExpanded = true;
            logPre.style.display = "";
            btnToggle.querySelector(".nav-arrow").innerHTML = "&#9652;";

            try {
                const url = "/api/scripts/scan" + (newOnly ? "?new_only=true" : "");
                const result = await apiPost(url);
                if (result.status === "already_running") {
                    toast("Scan already running");
                    logPre.textContent = (result.log || []).join("\n");
                }
                _pollScriptScanLog();
            } catch (err) {
                toast("Scan failed: " + err.message);
                logStatus.innerHTML = '<span class="badge badge-red">Failed</span>';
                _enableScanBtns();
            }
        };
    }
    if (btnFull) btnFull.addEventListener("click", _startScriptScan(false));
    if (btnNew) btnNew.addEventListener("click", _startScriptScan(true));
    if (btnReparse) btnReparse.addEventListener("click", async () => {
        _disableScanBtns("Re-parsing...");
        logWrap.style.display = "";
        logPre.textContent = "";
        logStatus.innerHTML = '<span class="badge badge-yellow">Re-parsing...</span>';
        logExpanded = true;
        logPre.style.display = "";
        btnToggle.querySelector(".nav-arrow").innerHTML = "&#9652;";
        try {
            const result = await apiPost("/api/scripts/reparse");
            if (result.status === "already_running") {
                toast("Scan already running");
                logPre.textContent = (result.log || []).join("\n");
            }
            _pollScriptScanLog();
        } catch (err) {
            toast("Re-parse failed: " + err.message);
            logStatus.innerHTML = '<span class="badge badge-red">Failed</span>';
            _enableScanBtns();
        }
    });

    function _pollScriptScanLog() {
        const interval = setInterval(async () => {
            // Stop polling if user navigated away
            if (currentPage !== "scripts") { clearInterval(interval); return; }
            try {
                const st = await api("/api/scripts/scan/status");
                logPre.textContent = (st.log || []).join("\n");
                logPre.scrollTop = logPre.scrollHeight;

                if (st.status !== "running") {
                    clearInterval(interval);
                    if (st.status === "completed") {
                        const r = st.result || {};
                        logStatus.innerHTML = `<span class="badge badge-green">Complete</span> ${r.scripts_total || 0} scripts, ${r.tables_linked || 0} linked`;
                        toast(`Scan complete - ${r.scripts_total || 0} scripts found`);
                    } else {
                        logStatus.innerHTML = `<span class="badge badge-red">Failed</span> ${(st.result || {}).error || ""}`;
                    }
                    _enableScanBtns();
                    // Refresh the whole page to show new data
                    navigate("scripts");
                }
            } catch (_) {
                clearInterval(interval);
            }
        }, 2000);
    }

    // Keep owner controls alive when the data table rebuilds its rows.
    const scriptsTable = document.getElementById("dt-scripts");
    if (scriptsTable) {
        scriptsTable.addEventListener("focusin", e => {
            const select = e.target.closest(".script-owner-select");
            if (select) select.dataset.previousValue = select.value;
        });
        scriptsTable.addEventListener("click", e => {
            if (e.target.closest(".script-owner-select")) e.stopPropagation();
        });
        scriptsTable.addEventListener("change", async e => {
            const select = e.target.closest(".script-owner-select");
            if (!select) return;
            e.stopPropagation();
            const scriptId = Number(select.dataset.scriptId);
            const previousValue = select.dataset.previousValue || "";
            select.disabled = true;
            try {
                const updated = await apiPatch(`/api/scripts/${scriptId}`, { owner: select.value || null });
                const script = window._dt?.["dt-scripts"]?.rows.find(item => item.id === scriptId);
                if (script) script.owner = updated.owner;
                select.dataset.previousValue = updated.owner || "";
                toast("Owner updated");
            } catch (err) {
                select.value = previousValue;
                toast("Owner was not saved: " + err.message);
            } finally {
                select.disabled = false;
            }
        });
    }

    // Click-to-copy on path cells
    document.querySelectorAll(".cell-copyable").forEach(el => {
        el.addEventListener("click", (e) => {
            e.stopPropagation();
            const path = el.dataset.copy;
            if (!path || path === "-") return;
            navigator.clipboard.writeText(path).then(() => {
                toast("Path copied to clipboard");
            }).catch(() => {
                toast("Failed to copy path");
            });
        });
    });
    _bindArchiveButtons(() => navigate("scripts"));
}


// ── Scheduled Tasks (Windows Task Scheduler) ──

// Derive category for a scheduled task from its linked script path or action command
function _taskCategory(task) {
    if (task.script_id) return "Data to SQL";
    const cmd = (task.action_command || "").toLowerCase();
    if (cmd.includes("python") || cmd.includes(".py")) return "Data to SQL";
    if (cmd.includes("excel") || cmd.includes(".xlsx") || cmd.includes(".csv")) return "Data to Excel";
    return "Other";
}

async function renderScheduledTasks() {
    const showArchived = _isShowingArchived("scheduledtasks");
    const showOtherTasks = sessionStorage.getItem("schtasks_show_other") === "1";
    const query = new URLSearchParams();
    if (showArchived) query.set("include_archived", "true");
    if (showOtherTasks) query.set("include_unlinked", "true");
    const tasks = await api("/api/scheduled-tasks" + (query.size ? `?${query}` : ""));
    const catFilter = sessionStorage.getItem("schtasks_category") || "";
    const machineFilter = sessionStorage.getItem("schtasks_machine") || "";
    let filtered = catFilter ? tasks.filter(t => _taskCategory(t) === catFilter) : tasks;
    if (machineFilter) filtered = filtered.filter(t => (t.machine_alias || t.hostname || "Local") === machineFilter);

    // Collect unique categories and machines
    const allCats = [...new Set(tasks.map(t => _taskCategory(t)))].sort();
    const catOpts = allCats.map(c => `<option value="${esc(c)}"${catFilter === c ? ' selected' : ''}>${esc(c)}</option>`).join("");
    const allMachines = [...new Set(tasks.map(t => t.machine_alias || t.hostname || "Local"))].sort();
    const machineOpts = allMachines.map(m => `<option value="${esc(m)}"${machineFilter === m ? ' selected' : ''}>${esc(m)}</option>`).join("");

    const cols = [
        { key: "machine_alias", label: "Machine", width: COL_W.sm, render: t => {
            const alias = t.machine_alias || t.hostname || "Local";
            return `<span class="badge badge-muted" style="font-size:0.68rem" title="${esc(t.hostname || '')}">${esc(alias)}</span>`;
        }, sortVal: t => t.machine_alias || t.hostname || "" },
        { key: "category", label: "Category", width: COL_W.sm, render: t => {
            const cat = _taskCategory(t);
            const cls = _CATEGORY_COLORS[cat] || "badge-muted";
            return `<span class="badge ${cls}" style="font-size:0.68rem">${esc(cat)}</span>`;
        }, sortVal: t => _taskCategory(t) },
        { key: "task_name", label: "Task", width: COL_W.xl, render: t => `<strong>${esc(t.task_name)}</strong>`, sortVal: t => t.task_name || "" },
        { key: "status", label: "Status", width: COL_W.sm, render: t => {
            if (!t.status) return '<span style="color:var(--text-dim)">-</span>';
            const cls = t.status === "Ready" ? "badge-green" : t.status === "Running" ? "badge-yellow" : t.status === "Disabled" ? "badge-dim" : "badge-yellow";
            return `<span class="badge ${cls}">${esc(t.status)}</span>`;
        }, sortVal: t => t.status || "" },
        { key: "last_run_time", label: "Last Run", width: COL_W.md, render: t => t.last_run_time
            ? `<span style="color:var(--text-muted)" title="${esc(t.last_run_time)}">${timeAgo(t.last_run_time)}</span>`
            : '<span style="color:var(--text-dim)">Never</span>',
          sortVal: t => t.last_run_time || "" },
        { key: "last_result", label: "Result", width: COL_W.sm, render: t => {
            const cls = t.result_state === "success" ? "badge-green" : t.result_state === "failed" ? "badge-red" : t.result_state === "benign" ? "badge-muted" : "badge-dim";
            return `<span class="badge ${cls}" title="${esc(t.last_result || '')}">${esc(t.result_label || "Unknown")}</span>`;
        }, sortVal: t => `${t.result_state || "unknown"}_${t.result_label || ""}` },
        { key: "next_run_time", label: "Next Run", width: COL_W.md, render: t => t.next_run_time
            ? `<span style="color:var(--text-muted)" title="${esc(t.next_run_time)}">${timeAgo(t.next_run_time)}</span>`
            : '<span style="color:var(--text-dim)">-</span>',
          sortVal: t => t.next_run_time || "" },
        { key: "schedule_type", label: "Schedule", width: COL_W.sm, render: t => {
            if (!t.schedule_type) return '<span style="color:var(--text-dim)">-</span>';
            const s = t.schedule_type.toLowerCase();
            const cls = s.includes("daily") ? "badge-green" : s.includes("weekly") ? "badge-blue" : s.includes("monthly") ? "badge-yellow" : "badge-muted";
            return `<span class="badge ${cls}" style="font-size:0.68rem">${esc(t.schedule_type)}</span>`;
        }, sortVal: t => t.schedule_type || "" },
        { key: "script_name", label: "Linked Script", width: COL_W.lg, render: t => t.script_id
            ? `<span class="badge badge-blue schtask-script-link" style="cursor:pointer" data-script-id="${t.script_id}">${esc(t.script_name)}</span>`
            : '<span style="color:var(--text-dim)">-</span>',
          sortVal: t => t.script_name || "" },
        _archiveColDef("scheduled_task"),
    ];

    const active = filtered.filter(t => !t.archived);
    const failedCount = active.filter(t => t.result_state === "failed").length;
    const linkedCount = active.filter(t => t.script_id).length;
    const disabledCount = active.filter(t => !t.enabled).length;
    const failedNote = failedCount > 0 ? ` <span class="badge badge-red" style="font-size:0.72rem">${failedCount} failed</span>` : "";
    const disabledNote = disabledCount > 0 ? ` <span class="badge badge-dim" style="font-size:0.72rem">${disabledCount} disabled</span>` : "";

    return `
        <div class="page-header">
            <h1>Scheduled Tasks</h1>
            <span class="subtitle">${active.length} ${showOtherTasks ? "Windows" : "governed refresh"} tasks, ${linkedCount} linked${failedNote}${disabledNote}</span>
            <button class="btn-outline" id="btn-scan-schtasks-full" style="margin-left:0.5rem">Full Scan</button>
            <button class="btn-outline" id="btn-scan-schtasks-new">Scan New</button>
            <button class="btn-outline btn-archive-toggle ${showOtherTasks ? 'active' : ''}" id="btn-schtasks-scope">${showOtherTasks ? "Show governed only" : "Show other Windows tasks"}</button>
            <select id="schtasks-machine-filter" class="freq-select-inline" style="font-size:0.75rem;margin-left:0.25rem"><option value="">All Machines</option>${machineOpts}</select>
            <select id="schtasks-cat-filter" class="freq-select-inline" style="font-size:0.75rem;margin-left:0.25rem"><option value="">All Categories</option>${catOpts}</select>
            ${_archiveToggleHtml("scheduledtasks")}
            <button class="btn-export" onclick="exportTableCSV('dt-schtasks','scheduled_tasks.csv')">Export CSV</button>
        </div>
        ${filtered.length === 0
            ? `<div class="empty-state" style="margin-top:2rem">${showOtherTasks ? "No Windows scheduled tasks found." : "No governed refresh tasks are linked to a script yet."}<br><span style="color:var(--text-dim);font-size:0.8rem">Run a scan to import Task Scheduler data. Use Show other Windows tasks to inspect unrelated entries.</span></div>`
            : dataTable("dt-schtasks", cols, filtered, { onRowClick: showScheduledTaskDetail })
        }
    `;
}

async function showScheduledTaskDetail(task) {
    const existing = $("#schtask-detail");
    if (existing) existing.remove();

    const panel = document.createElement("div");
    panel.id = "schtask-detail";
    panel.className = "source-detail-panel";

    const resultClass = task.result_state === "success" ? "badge-green" : task.result_state === "failed" ? "badge-red" : task.result_state === "benign" ? "badge-muted" : "badge-dim";
    const resultBadge = `<span class="badge ${resultClass}" title="${esc(task.last_result || '')}">${esc(task.result_label || "Unknown")}</span>`;

    const enabledBadge = task.enabled
        ? '<span class="badge badge-green">Enabled</span>'
        : '<span class="badge badge-dim">Disabled</span>';

    const statusBadge = !task.status ? '-'
        : task.status === "Ready" ? '<span class="badge badge-green">Ready</span>'
        : task.status === "Running" ? '<span class="badge badge-yellow">Running</span>'
        : `<span class="badge badge-dim">${esc(task.status)}</span>`;

    const scriptLink = task.script_id
        ? `<span class="badge badge-blue schtask-detail-script-link" style="cursor:pointer" data-script-id="${task.script_id}">${esc(task.script_name)}</span>`
        : '<span style="color:var(--text-dim)">Not linked</span>';

    const actionDisplay = [task.action_command, task.action_args].filter(Boolean).join(" ");

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(task.task_name)}</h2>
            <button class="btn-outline" id="btn-close-schtask-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Machine</div><span class="badge badge-muted">${esc(task.machine_alias || task.hostname || 'Local')}</span> <span style="color:var(--text-dim);font-size:0.75rem">${task.hostname ? esc(task.hostname) : ''}</span></div>
            <div class="detail-item"><div class="detail-label">Category</div><span class="badge ${_CATEGORY_COLORS[_taskCategory(task)] || 'badge-muted'}">${esc(_taskCategory(task))}</span></div>
            <div class="detail-item"><div class="detail-label">Full Path</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${esc(task.task_path)}</span></div>
            <div class="detail-item"><div class="detail-label">Status</div>${statusBadge}</div>
            <div class="detail-item"><div class="detail-label">Enabled</div>${enabledBadge}</div>
            <div class="detail-item"><div class="detail-label">Schedule</div><span style="color:var(--text)">${esc(task.schedule_type || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Last Run</div><span style="color:var(--text)">${task.last_run_time && !/1999/.test(task.last_run_time) ? esc(task.last_run_time) : "Never"}</span></div>
            <div class="detail-item"><div class="detail-label">Last Result</div>${resultBadge}</div>
            <div class="detail-item"><div class="detail-label">Next Run</div><span style="color:var(--text)">${esc(task.next_run_time || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Author</div><span style="color:var(--text)">${esc(task.author || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Run As</div><span style="color:var(--text)">${esc(task.run_as_user || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Action</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${esc(actionDisplay || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Linked Script</div>${scriptLink}</div>
            <div class="detail-item"><div class="detail-label">Last Scanned</div><span style="color:var(--text)">${task.last_scanned ? formatDate(task.last_scanned) : "-"}</span></div>
        </div>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    document.getElementById("btn-close-schtask-detail").addEventListener("click", () => panel.remove());

    // Click linked script to navigate
    const scriptEl = panel.querySelector(".schtask-detail-script-link");
    if (scriptEl) {
        scriptEl.addEventListener("click", async () => {
            const scriptId = parseInt(scriptEl.dataset.scriptId);
            try {
                const script = await api(`/api/scripts/${scriptId}`);
                await navigate("scripts");
                showScriptDetail(script);
            } catch (err) {
                toast("Script not found");
            }
        });
    }
}

function bindScheduledTasksPage() {
    document.getElementById("btn-schtasks-scope")?.addEventListener("click", () => {
        const showOther = sessionStorage.getItem("schtasks_show_other") === "1";
        sessionStorage.setItem("schtasks_show_other", showOther ? "0" : "1");
        navigate("scheduledtasks");
    });
    // Machine filter
    const machSel = document.getElementById("schtasks-machine-filter");
    if (machSel) {
        machSel.addEventListener("change", () => {
            sessionStorage.setItem("schtasks_machine", machSel.value);
            navigate("scheduledtasks");
        });
    }

    // Category filter
    const catSel = document.getElementById("schtasks-cat-filter");
    if (catSel) {
        catSel.addEventListener("change", () => {
            sessionStorage.setItem("schtasks_category", catSel.value);
            navigate("scheduledtasks");
        });
    }

    // Scan buttons
    const btnSchFull = document.getElementById("btn-scan-schtasks-full");
    const btnSchNew = document.getElementById("btn-scan-schtasks-new");

    async function _runSchScan(newOnly) {
        if (btnSchFull) { btnSchFull.disabled = true; btnSchFull.textContent = "Scanning..."; }
        if (btnSchNew) btnSchNew.disabled = true;
        try {
            const url = "/api/scheduled-tasks/scan" + (newOnly ? "?new_only=true" : "");
            const result = await apiPost(url);
            if (result.status === "completed") {
                toast(`Scan complete - ${result.tasks_total || 0} tasks found, ${result.scripts_linked || 0} linked`);
            } else {
                toast("Scan failed: " + (result.error || "unknown error"));
            }
            await navigate("scheduledtasks");
        } catch (err) {
            toast("Scan failed: " + err.message);
            if (btnSchFull) { btnSchFull.disabled = false; btnSchFull.textContent = "Full Scan"; }
            if (btnSchNew) btnSchNew.disabled = false;
        }
    }
    if (btnSchFull) btnSchFull.addEventListener("click", () => _runSchScan(false));
    if (btnSchNew) btnSchNew.addEventListener("click", () => _runSchScan(true));

    // Clickable script badges in table rows
    document.querySelectorAll(".schtask-script-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const scriptId = parseInt(el.dataset.scriptId);
            try {
                const script = await api(`/api/scripts/${scriptId}`);
                await navigate("scripts");
                showScriptDetail(script);
            } catch (err) {
                toast("Script not found");
            }
        });
    });
    _bindArchiveButtons(() => navigate("scheduledtasks"));
}


// ── Power Automate Flows ──

const _PA_STATUS_BADGE = {
    active: "badge-green",
    paused: "badge-yellow",
    error: "badge-red",
    disabled: "badge-dim",
};

async function renderPowerAutomate() {
    const showArchived = _isShowingArchived("powerautomate");
    const flows = await api("/api/power-automate-flows" + (showArchived ? "?include_archived=true" : ""));
    const active = flows.filter(f => !f.archived);
    const errorCount = active.filter(f => f.status === "error").length;
    const errorNote = errorCount > 0 ? ` <span class="badge badge-red" style="font-size:0.72rem">${errorCount} error</span>` : "";

    const cols = [
        { key: "name", label: "Name", width: COL_W.xl, render: f => `<strong>${esc(f.name)}</strong>`, sortVal: f => f.name || "" },
        { key: "status", label: "Status", width: COL_W.sm, render: f => {
            const cls = _PA_STATUS_BADGE[f.status] || "badge-muted";
            return `<span class="badge ${cls}">${esc(f.status || "unknown")}</span>`;
        }, sortVal: f => f.status || "" },
        { key: "owner", label: "Owner", width: COL_W.md, render: f => f.owner ? esc(f.owner) : '<span style="color:var(--text-dim)">-</span>', sortVal: f => f.owner || "" },
        { key: "schedule", label: "Schedule", width: COL_W.md, render: f => f.schedule ? esc(f.schedule) : '<span style="color:var(--text-dim)">-</span>', sortVal: f => f.schedule || "" },
        { key: "account", label: "Account", width: COL_W.md, render: f => f.account ? `<span class="badge badge-muted" style="font-size:0.68rem">${esc(f.account)}</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: f => f.account || "" },
        { key: "source_url", label: "Source URL", width: COL_W.lg, render: f => f.source_url ? `<span style="color:var(--text-muted);font-size:0.78rem;word-break:break-all" title="${esc(f.source_url)}">${esc(f.source_url.length > 40 ? f.source_url.slice(0, 40) + "..." : f.source_url)}</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: f => f.source_url || "" },
        { key: "output_source_name", label: "Output", width: COL_W.lg, render: f => f.output_source_id ? `<span class="badge badge-blue pa-source-link" style="cursor:pointer" data-source-id="${f.output_source_id}">${esc(f.output_source_name || "Source #" + f.output_source_id)}</span>` : (f.output_description ? `<span style="color:var(--text-muted);font-size:0.78rem">${esc(f.output_description)}</span>` : '<span style="color:var(--text-dim)">-</span>'), sortVal: f => f.output_source_name || f.output_description || "" },
        { key: "last_run_time", label: "Last Run", width: COL_W.md, render: f => f.last_run_time ? `<span style="color:var(--text-muted)" title="${esc(f.last_run_time)}">${timeAgo(f.last_run_time)}</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: f => f.last_run_time || "" },
        _archiveColDef("power_automate"),
    ];

    return `
        <div class="page-header">
            <h1>Power Automate</h1>
            <span class="subtitle">${active.length} flow${active.length !== 1 ? 's' : ''}${errorNote}</span>
            <button class="btn-outline" id="btn-pa-new-flow" style="margin-left:0.5rem">+ New Flow</button>
            ${_archiveToggleHtml("powerautomate")}
            <button class="btn-export" onclick="exportTableCSV('dt-pa-flows','power_automate_flows.csv')">Export CSV</button>
        </div>
        <div id="pa-create-form-area"></div>
        ${flows.length === 0
            ? '<div class="empty-state" style="margin-top:2rem">No Power Automate flows registered yet. Click <strong>+ New Flow</strong> to add one.</div>'
            : dataTable("dt-pa-flows", cols, flows, { onRowClick: showPowerAutomateDetail })
        }
    `;
}

async function showPowerAutomateDetail(flow) {
    const existing = $("#pa-detail");
    if (existing) existing.remove();

    const panel = document.createElement("div");
    panel.id = "pa-detail";
    panel.className = "source-detail-panel";

    const statusCls = _PA_STATUS_BADGE[flow.status] || "badge-muted";
    const outputDisplay = flow.output_source_id
        ? `<span class="badge badge-blue pa-detail-source-link" style="cursor:pointer" data-source-id="${flow.output_source_id}">${esc(flow.output_source_name || "Source #" + flow.output_source_id)}</span>`
        : (flow.output_description ? esc(flow.output_description) : '<span style="color:var(--text-dim)">Not linked</span>');

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(flow.name)}</h2>
            <button class="btn-outline" id="btn-pa-edit" style="margin-right:0.25rem">Edit</button>
            <button class="btn-outline" id="btn-pa-delete" style="margin-right:0.25rem;color:var(--red)">Delete</button>
            <button class="btn-outline" id="btn-close-pa-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Status</div><span class="badge ${statusCls}">${esc(flow.status || "unknown")}</span></div>
            <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${esc(flow.owner || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Account</div><span style="color:var(--text)">${esc(flow.account || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Schedule</div><span style="color:var(--text)">${esc(flow.schedule || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Last Run</div><span style="color:var(--text)">${flow.last_run_time ? formatDate(flow.last_run_time) : "-"}</span>${flow.output_source_id ? ' <span style="color:var(--text-dim);font-size:0.7rem">(from output source)</span>' : ''}</div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Source URL</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${flow.source_url ? esc(flow.source_url) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Output</div>${outputDisplay}</div>
            <div class="detail-item"><div class="detail-label">Output Description</div><span style="color:var(--text)">${esc(flow.output_description || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Description</div><span style="color:var(--text)">${esc(flow.description || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Notes</div><span style="color:var(--text);white-space:pre-wrap">${esc(flow.notes || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Created</div><span style="color:var(--text-dim)">${flow.created_at ? formatDate(flow.created_at) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Updated</div><span style="color:var(--text-dim)">${flow.updated_at ? formatDate(flow.updated_at) : "-"}</span></div>
        </div>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    document.getElementById("btn-close-pa-detail").addEventListener("click", () => panel.remove());

    document.getElementById("btn-pa-delete").addEventListener("click", async () => {
        if (!confirm(`Delete flow "${flow.name}"?`)) return;
        try {
            await apiDelete(`/api/power-automate-flows/${flow.id}`);
            toast("Flow deleted");
            panel.remove();
            navigate("powerautomate");
        } catch (err) {
            toast("Delete failed: " + err.message);
        }
    });

    document.getElementById("btn-pa-edit").addEventListener("click", () => {
        panel.remove();
        _showPaEditForm(flow);
    });

    // Click linked source
    const srcEl = panel.querySelector(".pa-detail-source-link");
    if (srcEl) {
        srcEl.addEventListener("click", async () => {
            const srcId = parseInt(srcEl.dataset.sourceId);
            try {
                const src = await api(`/api/sources/${srcId}`);
                await navigate("sources");
                showSourceDetail(src);
            } catch (err) {
                toast("Source not found");
            }
        });
    }
}

async function _renderPaForm(existing) {
    const opts = await api("/api/power-automate-flows/options");
    const f = existing || {};
    const isEdit = !!existing;

    const ownerOptions = opts.people.length > 0
        ? opts.people.map(p => `<option value="${esc(p.name)}"${f.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("")
        : opts.owners.map(o => `<option value="${esc(o)}"${f.owner === o ? ' selected' : ''}>${esc(o)}</option>`).join("");

    const sourceOptions = opts.sources.map(s => `<option value="${s.id}"${f.output_source_id === s.id ? ' selected' : ''}>${esc(s.name)}</option>`).join("");

    const statusOptions = opts.statuses.map(s => `<option value="${s}"${(f.status || 'active') === s ? ' selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`).join("");

    return `
        <div class="create-form" id="pa-form" style="margin-bottom:1.5rem">
            <h3 style="margin:0 0 0.75rem">${isEdit ? "Edit" : "New"} Power Automate Flow</h3>
            <div class="create-fields">
                <div class="create-field"><label>Name *</label><input id="pa-f-name" value="${esc(f.name || "")}" required></div>
                <div class="create-field"><label>Status</label><select id="pa-f-status">${statusOptions}</select></div>
                <div class="create-field"><label>Owner</label><select id="pa-f-owner"><option value="">-</option>${ownerOptions}</select></div>
                <div class="create-field"><label>Account</label><input id="pa-f-account" value="${esc(f.account || "")}" placeholder="e.g. user@example.com"></div>
                <div class="create-field"><label>Schedule</label><input id="pa-f-schedule" value="${esc(f.schedule || "")}" placeholder="e.g. Daily 6:00 AM"></div>
                <div class="create-field"><label>Source URL</label><input id="pa-f-source-url" value="${esc(f.source_url || "")}" placeholder="Website the flow scrapes"></div>
                <div class="create-field"><label>Output Source</label><select id="pa-f-output-source"><option value="">- None -</option>${sourceOptions}</select></div>
                <div class="create-field"><label>Output Description</label><input id="pa-f-output-desc" value="${esc(f.output_description || "")}" placeholder="e.g. C:\\Data\\retailer_x.csv"></div>
                <div class="create-field" style="grid-column:1/-1"><label>Description</label><textarea id="pa-f-desc" rows="2" style="width:100%">${esc(f.description || "")}</textarea></div>
                <div class="create-field" style="grid-column:1/-1"><label>Notes</label><textarea id="pa-f-notes" rows="2" style="width:100%">${esc(f.notes || "")}</textarea></div>
            </div>
            <div style="margin-top:0.75rem;display:flex;gap:0.5rem">
                <button class="btn-outline" id="pa-f-save">${isEdit ? "Save Changes" : "Create Flow"}</button>
                <button class="btn-outline" id="pa-f-cancel">Cancel</button>
            </div>
        </div>
    `;
}

function _collectPaFormData() {
    const name = document.getElementById("pa-f-name").value.trim();
    if (!name) { toast("Name is required"); return null; }
    return {
        name,
        status: document.getElementById("pa-f-status").value,
        owner: document.getElementById("pa-f-owner").value || null,
        account: document.getElementById("pa-f-account").value.trim() || null,
        schedule: document.getElementById("pa-f-schedule").value.trim() || null,
        source_url: document.getElementById("pa-f-source-url").value.trim() || null,
        output_source_id: parseInt(document.getElementById("pa-f-output-source").value) || null,
        output_description: document.getElementById("pa-f-output-desc").value.trim() || null,
        description: document.getElementById("pa-f-desc").value.trim() || null,
        notes: document.getElementById("pa-f-notes").value.trim() || null,
    };
}

async function _showPaCreateForm() {
    const area = document.getElementById("pa-create-form-area");
    if (!area) return;
    area.innerHTML = await _renderPaForm(null);

    document.getElementById("pa-f-cancel").addEventListener("click", () => { area.innerHTML = ""; });
    document.getElementById("pa-f-save").addEventListener("click", async () => {
        const data = _collectPaFormData();
        if (!data) return;
        try {
            await apiPostJson("/api/power-automate-flows", data);
            toast("Flow created");
            navigate("powerautomate");
        } catch (err) {
            toast("Create failed: " + err.message);
        }
    });
}

async function _showPaEditForm(flow) {
    const area = document.getElementById("pa-create-form-area");
    if (!area) return;
    area.innerHTML = await _renderPaForm(flow);
    area.scrollIntoView({ behavior: "smooth", block: "nearest" });

    document.getElementById("pa-f-cancel").addEventListener("click", () => {
        area.innerHTML = "";
        showPowerAutomateDetail(flow);
    });
    document.getElementById("pa-f-save").addEventListener("click", async () => {
        const data = _collectPaFormData();
        if (!data) return;
        try {
            const updated = await apiPatch(`/api/power-automate-flows/${flow.id}`, data);
            toast("Flow updated");
            area.innerHTML = "";
            navigate("powerautomate");
        } catch (err) {
            toast("Update failed: " + err.message);
        }
    });
}

function bindPowerAutomatePage() {
    const btnNew = document.getElementById("btn-pa-new-flow");
    if (btnNew) btnNew.addEventListener("click", () => _showPaCreateForm());

    // Clickable source badges in table rows
    document.querySelectorAll(".pa-source-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const srcId = parseInt(el.dataset.sourceId);
            try {
                const src = await api(`/api/sources/${srcId}`);
                await navigate("sources");
                showSourceDetail(src);
            } catch (err) {
                toast("Source not found");
            }
        });
    });

    _bindArchiveButtons(() => navigate("powerautomate"));
}


// ── Documentation ──

const _DOC_STATUS_BADGE = { draft: "badge-yellow", published: "badge-green" };

async function renderDocumentation() {
    const showArchived = _isShowingArchived("documentation");
    const docs = await api("/api/documentation" + (showArchived ? "?include_archived=true" : ""));
    const active = docs.filter(d => !d.archived);

    const cols = [
        { key: "title", label: "Title", width: COL_W.xl, render: d => `<strong>${esc(d.title)}</strong>`, sortVal: d => d.title || "" },
        { key: "status", label: "Status", width: COL_W.sm, render: d => {
            const cls = _DOC_STATUS_BADGE[d.status] || "badge-muted";
            return `<span class="badge ${cls}">${esc(d.status || "draft")}</span>`;
        }, sortVal: d => d.status || "" },
        { key: "report_name", label: "Report", width: COL_W.lg, render: d => d.report_name ? esc(d.report_name) : '<span style="color:var(--text-dim)">Standalone</span>', sortVal: d => d.report_name || "" },
        { key: "business_cadence", label: "Cadence", width: COL_W.md, render: d => d.business_cadence ? esc(d.business_cadence) : '<span style="color:var(--text-dim)">-</span>', sortVal: d => d.business_cadence || "" },
        { key: "created_by", label: "Author", width: COL_W.md, render: d => d.created_by ? esc(d.created_by) : '<span style="color:var(--text-dim)">-</span>', sortVal: d => d.created_by || "" },
        { key: "linked_entities", label: "Links", width: COL_W.sm, render: d => `<span style="color:var(--text-muted)">${(d.linked_entities || []).length}</span>`, sortVal: d => (d.linked_entities || []).length },
        { key: "updated_at", label: "Updated", width: COL_W.md, render: d => d.updated_at ? `<span style="color:var(--text-muted)" title="${esc(d.updated_at)}">${timeAgo(d.updated_at)}</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: d => d.updated_at || "" },
        _archiveColDef("documentation"),
    ];

    return `
        <div class="page-header">
            <h1>Documentation</h1>
            <span class="subtitle">${active.length} pipeline doc${active.length !== 1 ? 's' : ''}</span>
            <button class="btn-outline" id="btn-doc-new" style="margin-left:0.5rem">+ New Documentation</button>
            ${_archiveToggleHtml("documentation")}
            <button class="btn-export" onclick="exportTableCSV('dt-documentation','documentation.csv')">Export CSV</button>
        </div>
        <div id="doc-form-area"></div>
        ${docs.length === 0
            ? '<div class="empty-state" style="margin-top:2rem">No documentation yet. Click <strong>+ New Documentation</strong> to document a pipeline.</div>'
            : dataTable("dt-documentation", cols, docs, { onRowClick: showDocDetail })
        }
    `;
}

function _renderMeasures(text) {
    if (!text) return "";
    // Split on double-newline to get individual measure blocks
    // Format from suggest endpoint: **MeasureName** (TableName)\nDAX_expression
    const blocks = text.split(/\n\n+/);
    return blocks.map(block => {
        const lines = block.trim().split("\n");
        if (!lines[0]) return "";
        // Check if first line matches **Name** (Table) pattern
        const headerMatch = lines[0].match(/^\*\*(.+?)\*\*\s*\((.+?)\)$/);
        if (headerMatch) {
            const name = headerMatch[1];
            const table = headerMatch[2];
            const dax = lines.slice(1).join("\n").trim();
            return `<div class="doc-measure-block">
                <div class="doc-measure-header"><strong>${esc(name)}</strong> <span class="doc-measure-table">${esc(table)}</span></div>
                ${dax ? `<pre class="doc-measure-dax">${esc(dax)}</pre>` : ''}
            </div>`;
        }
        // Fallback: just render as preformatted text
        return `<pre class="doc-measure-dax">${esc(block)}</pre>`;
    }).join("");
}

async function showDocDetail(doc) {
    const existing = $("#doc-detail");
    if (existing) {
        // Toggle: if clicking the same doc, just close
        if (existing.dataset.docId === String(doc.id)) { existing.remove(); return; }
        existing.remove();
    }

    const panel = document.createElement("div");
    panel.id = "doc-detail";
    panel.dataset.docId = doc.id;
    panel.className = "source-detail-panel";

    const statusCls = _DOC_STATUS_BADGE[doc.status] || "badge-muted";
    const linksHtml = (doc.linked_entities || []).map(le =>
        `<span class="task-link-chip">${esc(le.entity_type)}: ${esc(le.entity_name || "ID " + le.entity_id)}</span>`
    ).join("") || '<span style="color:var(--text-dim)">None</span>';

    // Parse technical_sources JSON if possible
    let sourcesTableHtml = '<span style="color:var(--text-dim)">-</span>';
    if (doc.technical_sources) {
        try {
            const srcs = JSON.parse(doc.technical_sources);
            if (Array.isArray(srcs) && srcs.length > 0) {
                sourcesTableHtml = `<table class="doc-sources-table">
                    <tr><th>Source</th><th>Type</th><th>Table</th><th>Upstream</th></tr>
                    ${srcs.map(s => `<tr><td>${esc(s.name || "")}</td><td>${esc(s.type || "")}</td><td>${esc(s.table || "")}</td><td>${esc(s.upstream || "")}</td></tr>`).join("")}
                </table>`;
            }
        } catch (_) {
            sourcesTableHtml = `<span style="color:var(--text);white-space:pre-wrap">${esc(doc.technical_sources)}</span>`;
        }
    }

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(doc.title)}</h2>
            <span class="badge ${statusCls}" style="margin-left:0.5rem">${esc(doc.status || "draft")}</span>
            <span style="flex:1"></span>
            <button class="btn-outline" id="btn-doc-edit" style="margin-right:0.25rem">Edit</button>
            <button class="btn-outline" id="btn-doc-delete" style="margin-right:0.25rem;color:var(--red)">Delete</button>
            <button class="btn-outline" id="btn-doc-print" style="margin-right:0.25rem">Print</button>
            <button class="btn-outline" id="btn-close-doc-detail">&times; Close</button>
        </div>

        <div class="doc-view-section">
            <h3>Business Context</h3>
            <div class="detail-grid">
                <div class="detail-item"><div class="detail-label">Report</div><span>${esc(doc.report_name || "Standalone")}</span></div>
                <div class="detail-item"><div class="detail-label">Cadence</div><span>${esc(doc.business_cadence || "-")}</span></div>
                <div class="detail-item"><div class="detail-label">Author</div><span>${esc(doc.created_by || "-")}</span></div>
                <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Purpose</div><div class="doc-text-block">${doc.business_purpose ? renderMd(doc.business_purpose) : '<span style="color:var(--text-dim)">Not documented</span>'}</div></div>
                <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Audience</div><div class="doc-text-block">${doc.business_audience ? renderMd(doc.business_audience) : '<span style="color:var(--text-dim)">Not documented</span>'}</div></div>
            </div>
        </div>

        <div class="doc-view-section">
            <h3>Technical Lineage</h3>
            <div id="doc-mermaid-container" class="doc-mermaid-container">
                ${doc.technical_lineage_mermaid
                    ? `<pre class="doc-mermaid-source" style="display:none">${esc(doc.technical_lineage_mermaid)}</pre><div class="doc-mermaid-render">Loading diagram...</div>`
                    : '<span style="color:var(--text-dim)">No lineage diagram</span>'}
            </div>
        </div>

        <div class="doc-view-section">
            <h3>Data Sources</h3>
            ${sourcesTableHtml}
        </div>

        <div class="doc-view-section">
            <h3>Key Formulas &amp; Transformations</h3>
            <div class="doc-text-block">${doc.technical_transformations ? _renderMeasures(doc.technical_transformations) : '<span style="color:var(--text-dim)">Not documented</span>'}</div>
        </div>

        ${doc.information_tab ? `<div class="doc-view-section">
            <h3>Information Tab</h3>
            <div class="doc-text-block" style="white-space:pre-wrap">${esc(doc.information_tab)}</div>
        </div>` : ''}

        ${doc.technical_known_issues ? `<div class="doc-view-section">
            <h3>Known Issues</h3>
            <div class="doc-text-block">${renderMd(doc.technical_known_issues)}</div>
        </div>` : ''}

        <div class="doc-view-section">
            <h3>Linked Entities</h3>
            <div>${linksHtml}</div>
        </div>

        <div style="margin-top:1rem;color:var(--text-dim);font-size:0.72rem">
            Created ${doc.created_at ? formatDate(doc.created_at) : "-"} | Updated ${doc.updated_at ? formatDate(doc.updated_at) : "-"}
        </div>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Render Mermaid diagram if present
    if (doc.technical_lineage_mermaid) {
        _renderDocMermaid(panel);
    }

    document.getElementById("btn-close-doc-detail").addEventListener("click", () => panel.remove());

    document.getElementById("btn-doc-delete").addEventListener("click", async () => {
        if (!confirm(`Delete documentation "${doc.title}"?`)) return;
        try {
            await apiDelete(`/api/documentation/${doc.id}`);
            toast("Documentation deleted");
            panel.remove();
            navigate("documentation");
        } catch (err) {
            toast("Delete failed: " + err.message);
        }
    });

    document.getElementById("btn-doc-edit").addEventListener("click", () => {
        panel.remove();
        _showDocEditForm(doc);
    });

    document.getElementById("btn-doc-print").addEventListener("click", () => {
        const printWin = window.open("", "_blank");
        printWin.document.write(`<html><head><title>${esc(doc.title)}</title>
            <style>body{font-family:system-ui,sans-serif;max-width:800px;margin:2rem auto;color:#222}
            h2{margin-bottom:0.5rem}h3{color:#555;border-bottom:1px solid #ddd;padding-bottom:0.25rem;margin-top:1.5rem}
            .badge{padding:0.15rem 0.5rem;border-radius:4px;font-size:0.75rem;background:#eee}
            table{border-collapse:collapse;width:100%;margin:0.5rem 0}th,td{border:1px solid #ddd;padding:0.35rem 0.5rem;text-align:left;font-size:0.82rem}
            th{background:#f5f5f5}pre{background:#f5f5f5;padding:0.75rem;border-radius:4px;overflow-x:auto;font-size:0.8rem}</style></head><body>`);
        printWin.document.write(panel.innerHTML);
        printWin.document.write("</body></html>");
        printWin.document.close();
        printWin.print();
    });
}

async function _renderDocMermaid(panel) {
    const container = panel.querySelector(".doc-mermaid-render");
    const source = panel.querySelector(".doc-mermaid-source");
    if (!container || !source) return;

    const code = source.textContent;
    if (!code.trim()) { container.innerHTML = '<span style="color:var(--text-dim)">Empty diagram</span>'; return; }

    // Load mermaid from CDN if not already loaded
    if (!window.mermaid) {
        try {
            await new Promise((resolve, reject) => {
                const script = document.createElement("script");
                script.src = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
            window.mermaid.initialize({ startOnLoad: false, theme: document.body.classList.contains("dark") ? "dark" : "default" });
        } catch (_) {
            container.innerHTML = `<pre style="white-space:pre-wrap;font-size:0.8rem">${esc(code)}</pre>`;
            return;
        }
    }

    try {
        const id = "doc-mermaid-" + Date.now();
        const { svg } = await window.mermaid.render(id, code);
        container.innerHTML = svg;
    } catch (err) {
        container.innerHTML = `<pre style="white-space:pre-wrap;font-size:0.8rem">${esc(code)}</pre>
            <div style="color:var(--red);font-size:0.75rem;margin-top:0.25rem">Mermaid render error: ${esc(err.message || "")}</div>`;
    }
}

async function _renderDocForm(existing) {
    const opts = await api("/api/documentation/options");
    const d = existing || {};
    const isEdit = !!existing;

    const reportOptions = opts.reports.map(r =>
        `<option value="${r.id}"${d.report_id === r.id ? ' selected' : ''}>${esc(r.name)}</option>`
    ).join("");

    const statusOptions = opts.statuses.map(s =>
        `<option value="${s}"${(d.status || 'draft') === s ? ' selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`
    ).join("");

    const cadenceOptions = opts.cadences.map(c =>
        `<option value="${c}"${d.business_cadence === c ? ' selected' : ''}>${c}</option>`
    ).join("");

    const existingLinks = (d.linked_entities || []).map(le =>
        `<div class="task-link-row" data-entity-type="${esc(le.entity_type)}" data-entity-id="${le.entity_id}">
            <span class="task-link-badge">${esc(ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type)}</span>
            <span class="task-link-name">${esc(le.entity_name || "ID " + le.entity_id)}</span>
            <button type="button" class="task-link-remove" title="Remove">&times;</button>
        </div>`
    ).join("");

    return `
        <div class="create-form" id="doc-form" style="margin-bottom:1.5rem">
            <h3 style="margin:0 0 0.75rem">${isEdit ? "Edit" : "New"} Documentation</h3>

            <div style="margin-bottom:0.75rem;padding:0.5rem;background:var(--bg-card);border:1px solid var(--border);border-radius:6px">
                <strong style="font-size:0.82rem">Basics</strong>
                <div class="create-fields" style="margin-top:0.5rem">
                    <div class="create-field"><label>Report (optional)</label>
                        <select id="doc-f-report"><option value="">Standalone</option>${reportOptions}</select>
                        ${!isEdit ? '<button class="btn-outline" id="doc-f-suggest" type="button" style="margin-top:0.25rem;font-size:0.72rem">Auto-fill from report</button>' : ''}
                    </div>
                    <div class="create-field"><label>Title *</label><input id="doc-f-title" value="${esc(d.title || "")}" required></div>
                    <div class="create-field"><label>Status</label><select id="doc-f-status">${statusOptions}</select></div>
                </div>
            </div>

            <div style="margin-bottom:0.75rem;padding:0.5rem;background:var(--bg-card);border:1px solid var(--border);border-radius:6px">
                <strong style="font-size:0.82rem">Business Context</strong>
                <div class="create-fields" style="margin-top:0.5rem">
                    <div class="create-field" style="grid-column:1/-1"><label>Purpose - Why does this report exist?</label><textarea id="doc-f-purpose" rows="3" style="width:100%">${esc(d.business_purpose || "")}</textarea></div>
                    <div class="create-field" style="grid-column:1/-1"><label>Audience - Who uses it and how?</label><textarea id="doc-f-audience" rows="2" style="width:100%">${esc(d.business_audience || "")}</textarea></div>
                    <div class="create-field"><label>Cadence</label><select id="doc-f-cadence"><option value="">-</option>${cadenceOptions}</select></div>
                </div>
            </div>

            <div style="margin-bottom:0.75rem;padding:0.5rem;background:var(--bg-card);border:1px solid var(--border);border-radius:6px">
                <strong style="font-size:0.82rem">Technical</strong>
                <div class="create-fields" style="margin-top:0.5rem">
                    <div class="create-field" style="grid-column:1/-1"><label>Lineage Diagram (Mermaid)</label><textarea id="doc-f-mermaid" rows="8" style="width:100%;font-family:monospace;font-size:0.78rem">${esc(d.technical_lineage_mermaid || "")}</textarea>
                        <button class="btn-outline" id="doc-f-preview-mermaid" type="button" style="margin-top:0.25rem;font-size:0.72rem">Preview Diagram</button>
                        <div id="doc-f-mermaid-preview" style="margin-top:0.5rem"></div>
                    </div>
                    <div class="create-field" style="grid-column:1/-1"><label>Data Sources (JSON or text)</label><textarea id="doc-f-sources" rows="4" style="width:100%;font-family:monospace;font-size:0.78rem">${esc(d.technical_sources || "")}</textarea></div>
                    <div class="create-field" style="grid-column:1/-1"><label>Key Formulas &amp; Transformations</label><textarea id="doc-f-transforms" rows="6" style="width:100%">${esc(d.technical_transformations || "")}</textarea></div>
                    <div class="create-field" style="grid-column:1/-1"><label>Known Issues</label><textarea id="doc-f-issues" rows="3" style="width:100%">${esc(d.technical_known_issues || "")}</textarea></div>
                </div>
            </div>

            <div style="margin-bottom:0.75rem;padding:0.5rem;background:var(--bg-card);border:1px solid var(--border);border-radius:6px">
                <strong style="font-size:0.82rem">Information Tab</strong>
                <div class="create-fields" style="margin-top:0.5rem">
                    <div class="create-field" style="grid-column:1/-1"><label>Paste PBI Information tab content here</label><textarea id="doc-f-info" rows="6" style="width:100%">${esc(d.information_tab || "")}</textarea></div>
                </div>
            </div>

            <div style="margin-bottom:0.75rem;padding:0.5rem;background:var(--bg-card);border:1px solid var(--border);border-radius:6px">
                <strong style="font-size:0.82rem">Linked Entities</strong>
                <div id="doc-links-list" class="task-links-list" style="margin-top:0.5rem">${existingLinks}</div>
                <div class="task-link-add-row" style="margin-top:0.25rem">
                    <select id="doc-link-type">
                        <option value="">Select type...</option>
                        ${Object.entries(ENTITY_TYPE_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
                    </select>
                    <select id="doc-link-entity" disabled>
                        <option value="">Select entity...</option>
                    </select>
                    <button type="button" class="btn-outline" id="doc-link-add-btn" disabled>Add</button>
                </div>
            </div>

            <div style="margin-top:0.75rem;display:flex;gap:0.5rem">
                <button class="btn-outline" id="doc-f-save">${isEdit ? "Save Changes" : "Create Documentation"}</button>
                <button class="btn-outline" id="doc-f-cancel">Cancel</button>
            </div>
        </div>
    `;
}

function _collectDocFormData() {
    const title = document.getElementById("doc-f-title").value.trim();
    if (!title) { toast("Title is required"); return null; }

    const reportVal = document.getElementById("doc-f-report").value;

    // Collect linked entities from DOM
    const linked_entities = [];
    document.querySelectorAll("#doc-links-list .task-link-row").forEach(row => {
        linked_entities.push({
            entity_type: row.dataset.entityType,
            entity_id: parseInt(row.dataset.entityId),
        });
    });

    return {
        report_id: reportVal ? parseInt(reportVal) : null,
        title,
        status: document.getElementById("doc-f-status").value,
        business_purpose: document.getElementById("doc-f-purpose").value.trim() || null,
        business_audience: document.getElementById("doc-f-audience").value.trim() || null,
        business_cadence: document.getElementById("doc-f-cadence").value || null,
        technical_lineage_mermaid: document.getElementById("doc-f-mermaid").value.trim() || null,
        technical_sources: document.getElementById("doc-f-sources").value.trim() || null,
        technical_transformations: document.getElementById("doc-f-transforms").value.trim() || null,
        technical_known_issues: document.getElementById("doc-f-issues").value.trim() || null,
        information_tab: document.getElementById("doc-f-info").value.trim() || null,
        linked_entities,
    };
}

async function _bindDocFormEvents(opts) {
    // Entity linking (reuse task pattern)
    let linkableEntities = {};
    try { linkableEntities = await api("/api/tasks/linkable-entities"); } catch (_) {}

    const linkTypeSelect = document.getElementById("doc-link-type");
    const linkEntitySelect = document.getElementById("doc-link-entity");
    const linkAddBtn = document.getElementById("doc-link-add-btn");
    const linksList = document.getElementById("doc-links-list");

    if (linkTypeSelect) {
        linkTypeSelect.addEventListener("change", () => {
            const etype = linkTypeSelect.value;
            linkEntitySelect.innerHTML = '<option value="">Select entity...</option>';
            linkEntitySelect.disabled = !etype;
            linkAddBtn.disabled = true;
            if (etype && linkableEntities[etype]) {
                linkableEntities[etype].forEach(e => {
                    linkEntitySelect.insertAdjacentHTML("beforeend",
                        `<option value="${e.id}">${esc(e.name)}</option>`);
                });
            }
        });

        linkEntitySelect.addEventListener("change", () => {
            linkAddBtn.disabled = !linkEntitySelect.value;
        });

        linkAddBtn.addEventListener("click", () => {
            const etype = linkTypeSelect.value;
            const eid = parseInt(linkEntitySelect.value);
            if (!etype || !eid) return;
            const existingLink = linksList.querySelector(`[data-entity-type="${etype}"][data-entity-id="${eid}"]`);
            if (existingLink) { toast("Already linked"); return; }
            const ename = linkEntitySelect.options[linkEntitySelect.selectedIndex].text;
            linksList.insertAdjacentHTML("beforeend",
                `<div class="task-link-row" data-entity-type="${esc(etype)}" data-entity-id="${eid}">
                    <span class="task-link-badge">${esc(ENTITY_TYPE_LABELS[etype] || etype)}</span>
                    <span class="task-link-name">${esc(ename)}</span>
                    <button type="button" class="task-link-remove" title="Remove">&times;</button>
                </div>`);
            linkTypeSelect.value = "";
            linkEntitySelect.innerHTML = '<option value="">Select entity...</option>';
            linkEntitySelect.disabled = true;
            linkAddBtn.disabled = true;
        });

        linksList.addEventListener("click", (e) => {
            if (e.target.classList.contains("task-link-remove")) {
                e.target.closest(".task-link-row").remove();
            }
        });
    }

    // Auto-suggest button: pre-fill form from report data
    const suggestBtn = document.getElementById("doc-f-suggest");
    if (suggestBtn) {
        suggestBtn.addEventListener("click", async () => {
            const reportId = document.getElementById("doc-f-report").value;
            if (!reportId) { toast("Select a report first"); return; }
            suggestBtn.disabled = true;
            suggestBtn.textContent = "Loading...";
            try {
                const suggestion = await api(`/api/documentation/suggest/${reportId}`);
                if (suggestion.title && !document.getElementById("doc-f-title").value.trim()) {
                    document.getElementById("doc-f-title").value = suggestion.title;
                }
                if (suggestion.business_cadence) {
                    const cadSel = document.getElementById("doc-f-cadence");
                    for (const opt of cadSel.options) {
                        if (opt.value === suggestion.business_cadence) { opt.selected = true; break; }
                    }
                }
                if (suggestion.technical_lineage_mermaid) {
                    document.getElementById("doc-f-mermaid").value = suggestion.technical_lineage_mermaid;
                }
                if (suggestion.technical_sources) {
                    document.getElementById("doc-f-sources").value = suggestion.technical_sources;
                }
                if (suggestion.technical_transformations) {
                    document.getElementById("doc-f-transforms").value = suggestion.technical_transformations;
                }
                // Auto-add report as linked entity
                if (suggestion.linked_entities) {
                    suggestion.linked_entities.forEach(le => {
                        const exists = linksList.querySelector(`[data-entity-type="${le.entity_type}"][data-entity-id="${le.entity_id}"]`);
                        if (!exists) {
                            const rptName = opts.reports.find(r => r.id === le.entity_id)?.name || "ID " + le.entity_id;
                            linksList.insertAdjacentHTML("beforeend",
                                `<div class="task-link-row" data-entity-type="${esc(le.entity_type)}" data-entity-id="${le.entity_id}">
                                    <span class="task-link-badge">${esc(ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type)}</span>
                                    <span class="task-link-name">${esc(rptName)}</span>
                                    <button type="button" class="task-link-remove" title="Remove">&times;</button>
                                </div>`);
                        }
                    });
                }
                toast("Form pre-filled from report data");
            } catch (err) {
                toast("Auto-suggest failed: " + err.message);
            } finally {
                suggestBtn.disabled = false;
                suggestBtn.textContent = "Auto-fill from report";
            }
        });
    }

    // Mermaid preview button
    const previewBtn = document.getElementById("doc-f-preview-mermaid");
    if (previewBtn) {
        previewBtn.addEventListener("click", async () => {
            const code = document.getElementById("doc-f-mermaid").value.trim();
            const previewDiv = document.getElementById("doc-f-mermaid-preview");
            if (!code) { previewDiv.innerHTML = '<span style="color:var(--text-dim)">Nothing to preview</span>'; return; }

            if (!window.mermaid) {
                try {
                    await new Promise((resolve, reject) => {
                        const script = document.createElement("script");
                        script.src = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";
                        script.onload = resolve;
                        script.onerror = reject;
                        document.head.appendChild(script);
                    });
                    window.mermaid.initialize({ startOnLoad: false, theme: document.body.classList.contains("dark") ? "dark" : "default" });
                } catch (_) {
                    previewDiv.innerHTML = '<span style="color:var(--red)">Failed to load Mermaid</span>';
                    return;
                }
            }

            try {
                const id = "doc-preview-" + Date.now();
                const { svg } = await window.mermaid.render(id, code);
                previewDiv.innerHTML = svg;
            } catch (err) {
                previewDiv.innerHTML = `<span style="color:var(--red)">Render error: ${esc(err.message || "")}</span>`;
            }
        });
    }
}

async function _showDocCreateForm() {
    const area = document.getElementById("doc-form-area");
    if (!area) return;
    const opts = await api("/api/documentation/options");
    area.innerHTML = await _renderDocForm(null);

    _bindDocFormEvents(opts);

    document.getElementById("doc-f-cancel").addEventListener("click", () => { area.innerHTML = ""; });
    document.getElementById("doc-f-save").addEventListener("click", async () => {
        const data = _collectDocFormData();
        if (!data) return;
        try {
            await apiPostJson("/api/documentation", data);
            toast("Documentation created");
            navigate("documentation");
        } catch (err) {
            toast("Create failed: " + err.message);
        }
    });
}

async function _showDocEditForm(doc) {
    const area = document.getElementById("doc-form-area");
    if (!area) return;
    const opts = await api("/api/documentation/options");
    area.innerHTML = await _renderDocForm(doc);
    area.scrollIntoView({ behavior: "smooth", block: "nearest" });

    _bindDocFormEvents(opts);

    document.getElementById("doc-f-cancel").addEventListener("click", () => {
        area.innerHTML = "";
        showDocDetail(doc);
    });
    document.getElementById("doc-f-save").addEventListener("click", async () => {
        const data = _collectDocFormData();
        if (!data) return;
        try {
            await apiPatch(`/api/documentation/${doc.id}`, data);
            toast("Documentation updated");
            area.innerHTML = "";
            navigate("documentation");
        } catch (err) {
            toast("Update failed: " + err.message);
        }
    });
}

function bindDocumentationPage() {
    const btnNew = document.getElementById("btn-doc-new");
    if (btnNew) btnNew.addEventListener("click", () => _showDocCreateForm());
    _bindArchiveButtons(() => navigate("documentation"));
}


// ── Lineage Diagram ──

const LINEAGE_COLS = [
    { key: "visuals", label: "Visuals" },
    { key: "tables", label: "Power BI Tables" },
    { key: "sources", label: "Sources" },
    { key: "mv_upstream", label: "Source Dependencies" },
    { key: "flows", label: "Flows" },
    { key: "scripts", label: "Scripts" },
    { key: "tasks", label: "Scheduled Tasks" },
    { key: "upstreams", label: "Upstream Systems" },
];

const LINEAGE_COL_STORAGE_KEY = "lineage_cols_v2";

function _getLineageCols() {
    const defaults = Object.fromEntries(LINEAGE_COLS.map(c => [c.key, true]));
    defaults.visuals = false;
    defaults.tables = false;
    try { const s = sessionStorage.getItem(LINEAGE_COL_STORAGE_KEY); if (s) return { ...defaults, ...JSON.parse(s) }; } catch (_) {}
    return defaults;
}
function _setLineageCols(state) { sessionStorage.setItem(LINEAGE_COL_STORAGE_KEY, JSON.stringify(state)); }

async function renderLineageDiagram() {
    const showArchived = sessionStorage.getItem("lineage_show_archived") === "1";
    const reports = await api("/api/reports" + (showArchived ? "?include_archived=true" : ""));
    const colState = _getLineageCols();
    return `
        <div class="page-header">
            <h2>Pipelines</h2>
            <p class="page-subtitle">Trace and refresh the governed path from Power BI to upstream systems</p>
        </div>
        <div class="lineage-controls">
            <select id="lineage-report-select" class="lineage-dropdown">
                <option value="">Select a report...</option>
                ${reports.map(r => `<option value="${r.id}">${esc(r.name)}${r.archived ? " (archived)" : ""}${r.status === "degraded" ? " \u26a0" : ""}</option>`).join("")}
            </select>
            <button class="btn-outline lineage-refresh-report" id="lineage-report-refresh" type="button" disabled>Refresh report</button>
            <label class="lineage-archive-toggle"><input type="checkbox" id="lineage-show-archived" ${showArchived ? "checked" : ""}> Show archived reports</label>
            <div class="lineage-col-toggles" id="lineage-col-toggles">
                ${LINEAGE_COLS.map(c => `<button class="lineage-col-toggle${colState[c.key] ? ' active' : ''}" data-col="${c.key}">${c.label}</button>`).join("")}
            </div>
        </div>
        <div id="lineage-container" class="lineage-container">
            <div class="lineage-placeholder">Select a report above to view its data lineage</div>
        </div>
    `;
}

function bindLineageDiagramPage() {
    const sel = document.getElementById("lineage-report-select");
    const reportRefresh = document.getElementById("lineage-report-refresh");
    if (!sel) return;
    document.getElementById("lineage-show-archived")?.addEventListener("change", event => {
        sessionStorage.setItem("lineage_show_archived", event.target.checked ? "1" : "0");
        navigate("lineage");
    });
    sel.addEventListener("change", async () => {
        const id = sel.value;
        reportRefresh.disabled = true;
        reportRefresh.dataset.canRefresh = "0";
        if (!id) {
            document.getElementById("lineage-container").innerHTML =
                '<div class="lineage-placeholder">Select a report above to view its data lineage</div>';
            return;
        }
        document.getElementById("lineage-container").innerHTML =
            '<div class="lineage-placeholder">Loading lineage...</div>';
        try {
            const data = await api(`/api/lineage/report/${id}/diagram`);
            window._lineageData = data;
            reportRefresh.dataset.canRefresh = data.report.can_refresh ? "1" : "0";
            reportRefresh.disabled = !data.report.can_refresh;
            reportRefresh.title = data.report.can_refresh
                ? (data.report.pbi_dataset_id
                    ? `Refresh ${data.report.name} in Power BI`
                    : `Find ${data.report.name}'s semantic model in Power BI and refresh it`)
                : "Power BI refresh is unavailable because no workspace is configured";
            _renderLineageDiagram(data);
        } catch (e) {
            document.getElementById("lineage-container").innerHTML =
                `<div class="lineage-placeholder" style="color:var(--red)">Error: ${e.message}</div>`;
        }
    });
    reportRefresh?.addEventListener("click", async () => {
        const reportId = sel.value;
        if (!reportId || reportRefresh.dataset.canRefresh !== "1" || reportRefresh.disabled) return;
        const original = reportRefresh.textContent;
        reportRefresh.disabled = true;
        reportRefresh.textContent = "Requesting...";
        try {
            await apiPost(`/api/reports/${reportId}/refresh`);
            toast("Power BI refresh requested. Its status will update on the next Power BI sync.");
        } catch (err) {
            toast("Power BI refresh was not requested: " + err.message);
        } finally {
            reportRefresh.textContent = original;
            reportRefresh.disabled = reportRefresh.dataset.canRefresh !== "1";
        }
    });
    document.querySelectorAll(".lineage-col-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
            const key = btn.dataset.col;
            const state = _getLineageCols();
            state[key] = !state[key];
            if (Object.values(state).filter(Boolean).length === 0) { state[key] = true; return; }
            _setLineageCols(state);
            btn.classList.toggle("active", state[key]);
            if (window._lineageData) _renderLineageDiagram(window._lineageData);
        });
    });
}

// Visual type classification
const _linChartTypes = new Set(["barChart","clusteredBarChart","stackedBarChart","columnChart","clusteredColumnChart","stackedColumnChart","lineChart","areaChart","lineClusteredColumnComboChart","lineStackedColumnComboChart","pieChart","donutChart","treemap","waterfallChart","funnel","ribbonChart","scatterChart","decompositionTreeVisual","gauge"]);
const _linCardTypes = new Set(["card","multiRowCard","cardVisual","kpi"]);
const _linSlicerTypes = new Set(["slicer","advancedSlicerVisual"]);
const _linMatrixTypes = new Set(["tableEx","pivotTable","table"]);
function _linCat(t) {
    if (_linChartTypes.has(t)) return "Charts";
    if (_linCardTypes.has(t)) return "Cards";
    if (_linSlicerTypes.has(t)) return "Slicers";
    if (_linMatrixTypes.has(t)) return "Tables & Matrices";
    return "Other";
}

function _lineageSourceLayers(data, directSourceIds) {
    const sourceMap = new Map();
    for (const source of (data.sources || [])) sourceMap.set(source.id, source);

    // Backward-compatible fallback for an older API response where upstream
    // source details lived only on dependency rows.
    for (const dep of (data.source_deps || [])) {
        if (!sourceMap.has(dep.depends_on_id)) {
            sourceMap.set(dep.depends_on_id, {
                id: dep.depends_on_id,
                name: dep.depends_on_name,
                type: dep.depends_on_type,
                status: dep.depends_on_status,
                last_data_at: dep.depends_on_last_data_at,
                row_count: dep.depends_on_row_count,
                custom_fresh_days: dep.depends_on_custom_fresh_days,
                freshness_rule_type: dep.depends_on_freshness_rule_type,
                freshness_schedule_days: dep.depends_on_freshness_schedule_days,
            });
        }
    }

    const depsBySource = new Map();
    for (const dep of (data.source_deps || [])) {
        if (!depsBySource.has(dep.source_id)) depsBySource.set(dep.source_id, []);
        depsBySource.get(dep.source_id).push(dep.depends_on_id);
    }

    // First find the reachable subgraph and each source's nearest level. This
    // pass is also the cycle-safe boundary for everything that follows.
    const nearestDepth = new Map();
    const discoveryQueue = [];
    for (const sourceId of directSourceIds) {
        if (!sourceMap.has(sourceId) || nearestDepth.has(sourceId)) continue;
        nearestDepth.set(sourceId, 0);
        discoveryQueue.push(sourceId);
    }
    for (let i = 0; i < discoveryQueue.length; i++) {
        const sourceId = discoveryQueue[i];
        const nextDepth = nearestDepth.get(sourceId) + 1;
        for (const dependencyId of (depsBySource.get(sourceId) || [])) {
            if (!sourceMap.has(dependencyId) || nearestDepth.has(dependencyId)) continue;
            nearestDepth.set(dependencyId, nextDepth);
            discoveryQueue.push(dependencyId);
        }
    }

    // Assign the longest DAG depth so every non-cyclic dependency edge moves
    // to the right, even when a shared ancestor is reached by unequal paths.
    // If malformed data contains a cycle, deterministically break that cycle
    // at the nearest remaining node; only the cycle's back-edge can then point
    // left, while downstream dependencies continue at increasing levels.
    const reachableIds = new Set(nearestDepth.keys());
    const indegree = new Map([...reachableIds].map(sourceId => [sourceId, 0]));
    for (const sourceId of reachableIds) {
        for (const dependencyId of (depsBySource.get(sourceId) || [])) {
            if (reachableIds.has(dependencyId)) {
                indegree.set(dependencyId, indegree.get(dependencyId) + 1);
            }
        }
    }

    const depthById = new Map();
    for (const sourceId of directSourceIds) {
        if (reachableIds.has(sourceId)) depthById.set(sourceId, 0);
    }
    const processed = new Set();
    const compareSourceIds = (a, b) =>
        (nearestDepth.get(a) - nearestDepth.get(b)) ||
        ((sourceMap.get(a)?.name || "").localeCompare(sourceMap.get(b)?.name || "")) ||
        (a - b);
    const ready = [...reachableIds].filter(sourceId => indegree.get(sourceId) === 0).sort(compareSourceIds);
    const enqueueReady = sourceId => {
        if (!processed.has(sourceId) && !ready.includes(sourceId)) {
            ready.push(sourceId);
            ready.sort(compareSourceIds);
        }
    };

    while (processed.size < reachableIds.size) {
        if (ready.length === 0) {
            const remainingIds = [...reachableIds].filter(sourceId => !processed.has(sourceId));
            const remainingSet = new Set(remainingIds);
            const isInRemainingCycle = startId => {
                const stack = (depsBySource.get(startId) || []).filter(id => remainingSet.has(id));
                const seen = new Set();
                while (stack.length > 0) {
                    const sourceId = stack.pop();
                    if (sourceId === startId) return true;
                    if (seen.has(sourceId)) continue;
                    seen.add(sourceId);
                    for (const dependencyId of (depsBySource.get(sourceId) || [])) {
                        if (remainingSet.has(dependencyId)) stack.push(dependencyId);
                    }
                }
                return false;
            };
            const cycleIds = remainingIds.filter(isInRemainingCycle);
            const cycleEntry = (cycleIds.length > 0 ? cycleIds : remainingIds)
                .sort(compareSourceIds)[0];
            enqueueReady(cycleEntry);
        }

        const sourceId = ready.shift();
        if (processed.has(sourceId)) continue;
        processed.add(sourceId);
        const sourceDepth = depthById.get(sourceId) ?? nearestDepth.get(sourceId) ?? 0;
        depthById.set(sourceId, sourceDepth);

        for (const dependencyId of (depsBySource.get(sourceId) || [])) {
            if (!reachableIds.has(dependencyId) || processed.has(dependencyId)) continue;
            const candidateDepth = sourceDepth + 1;
            if (candidateDepth > (depthById.get(dependencyId) ?? -1)) {
                depthById.set(dependencyId, candidateDepth);
            }
            indegree.set(dependencyId, Math.max(0, indegree.get(dependencyId) - 1));
            if (indegree.get(dependencyId) === 0) enqueueReady(dependencyId);
        }
    }

    const layers = [];
    for (const [sourceId, depth] of depthById) {
        if (!layers[depth]) layers[depth] = [];
        layers[depth].push(sourceMap.get(sourceId));
    }
    for (const layer of layers) {
        if (layer) layer.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    }

    // Reduce crossings with alternating barycentric sweeps. Alphabetical order
    // is deterministic but visually arbitrary; positioning each upstream card
    // near the average position of its connected cards produces much calmer
    // fans without changing the column/depth assignment.
    const parentsById = new Map();
    for (const sourceId of reachableIds) {
        for (const dependencyId of (depsBySource.get(sourceId) || [])) {
            if (!reachableIds.has(dependencyId)) continue;
            if (!parentsById.has(dependencyId)) parentsById.set(dependencyId, []);
            parentsById.get(dependencyId).push(sourceId);
        }
    }
    const reorderByNeighbors = (depth, neighborDepth, getNeighborIds) => {
        const layer = layers[depth];
        const neighbors = layers[neighborDepth];
        if (!layer || layer.length < 2 || !neighbors?.length) return;
        const positions = new Map(neighbors.map((item, index) => [item.id, index]));
        const scored = layer.map((item, originalIndex) => {
            const linked = getNeighborIds(item.id)
                .filter(id => depthById.get(id) === neighborDepth && positions.has(id))
                .map(id => positions.get(id));
            const score = linked.length
                ? linked.reduce((sum, value) => sum + value, 0) / linked.length
                : Number.POSITIVE_INFINITY;
            return { item, originalIndex, score };
        });
        scored.sort((a, b) => (a.score - b.score) || (a.originalIndex - b.originalIndex));
        layers[depth] = scored.map(entry => entry.item);
    };
    for (let pass = 0; pass < 4; pass++) {
        for (let depth = 1; depth < layers.length; depth++) {
            reorderByNeighbors(depth, depth - 1, id => parentsById.get(id) || []);
        }
        for (let depth = layers.length - 2; depth >= 1; depth--) {
            reorderByNeighbors(depth, depth + 1, id => depsBySource.get(id) || []);
        }
    }

    return { sourceMap, depthById, layers };
}

function _renderLineageDiagram(data) {
    const container = document.getElementById("lineage-container");
    window._linHL = null;
    if (window._lineageBindTimer) {
        clearTimeout(window._lineageBindTimer);
        window._lineageBindTimer = null;
    }
    const colState = _getLineageCols();

    // === Process data ===
    const visualNodes = [];
    const fieldMap = new Map();
    for (const page of data.pages) {
        for (const v of page.visuals) {
            const fieldKeys = (v.fields || []).map(f => `${f.table}.${f.field}`);
            visualNodes.push({ id: `visual-${v.visual_db_id}`, type: v.visual_type, title: v.title, fields: fieldKeys, page: page.page_name });
            for (const f of (v.fields || [])) {
                const key = `${f.table}.${f.field}`;
                if (!fieldMap.has(key)) fieldMap.set(key, { table: f.table, field: f.field });
            }
        }
    }

    // Group visuals: page -> type -> visuals
    const pageGroups = new Map();
    for (const v of visualNodes) {
        if (!pageGroups.has(v.page)) pageGroups.set(v.page, new Map());
        const cat = _linCat(v.type);
        const pg = pageGroups.get(v.page);
        if (!pg.has(cat)) pg.set(cat, []);
        pg.get(cat).push(v);
    }

    // Fields grouped by table
    const fieldsByTable = new Map();
    for (const [key, f] of fieldMap) {
        if (!fieldsByTable.has(f.table)) fieldsByTable.set(f.table, []);
        fieldsByTable.get(f.table).push({ id: `field-${key}`, key, table: f.table, field: f.field });
    }

    // Tables, sources, flows, scripts, tasks, upstreams
    const tableMap = new Map();
    for (const t of data.tables) tableMap.set(t.table_name, { name: t.table_name, source_id: t.source_id });
    const scriptMap = new Map();
    for (const s of (data.scripts || [])) scriptMap.set(s.id, s);
    const taskMap = new Map();
    for (const t of (data.scheduled_tasks || [])) taskMap.set(t.id, t);
    const upstreamMap = new Map();
    for (const u of data.upstreams) upstreamMap.set(u.id, u);

    // Filter to used items only
    const usedTableNames = new Set([...fieldMap.values()].map(f => f.table));
    const tableNodes = [...tableMap.values()].filter(t => usedTableNames.has(t.name) || t.source_id);
    const usedSourceIds = new Set(tableNodes.map(t => t.source_id).filter(Boolean));
    const { layers: sourceLayers } = _lineageSourceLayers(data, usedSourceIds);
    const sourceNodes = sourceLayers[0] || [];
    const allSourceNodes = sourceLayers.flatMap(layer => layer || []);
    const allSourceIds = new Set(allSourceNodes.map(s => s.id));
    const usedUpstreamIds = new Set(allSourceNodes.map(s => s.upstream_id).filter(Boolean));
    const upstreamNodes = [...upstreamMap.values()].filter(u => usedUpstreamIds.has(u.id));
    const scriptNodes = [...scriptMap.values()].filter(s => (s.source_ids || []).some(sid => allSourceIds.has(sid)));
    const usedScriptIds = new Set(scriptNodes.map(s => s.id));
    const taskNodes = [...taskMap.values()].filter(t => usedScriptIds.has(t.script_id));
    const flowNodes = (data.flows || []).filter(flow =>
        (flow.target_source_ids || []).some(sourceId => allSourceIds.has(sourceId))
    );

    if (visualNodes.length === 0 && tableNodes.length === 0) {
        container.innerHTML = '<div class="lineage-placeholder">No visual lineage data. Run a layout scan from Scanner.</div>';
        return;
    }

    // Status helpers
    const hasLineageRule = (item, prefix = "") => {
        const ruleType = item[`${prefix}freshness_rule_type`];
        const customDays = item[`${prefix}custom_fresh_days`];
        return !!ruleType || (customDays != null && customDays > 0);
    };
    const lineageState = (item, prefix = "") => {
        const status = item[`${prefix}status`] || "unknown";
        const lastDate = item[`${prefix}last_data_at`];
        if (!hasLineageRule(item, prefix)) return "warn";
        if (!lastDate) return "warn";
        if (["fresh", "current"].includes(status)) return "ok";
        if (["stale", "outdated", "error", "degraded"].includes(status)) return "err";
        if (["unknown", "no_connection", "no_rule"].includes(status)) return "warn";
        return "warn";
    };
    const stCls = (item, prefix = "") => ({ ok: "lin-st-ok", err: "lin-st-err", warn: "lin-st-warn" }[lineageState(item, prefix)] || "lin-st-warn");
    const sourceFacts = (item, prefix = "") => {
        const lastDate = item[`${prefix}last_data_at`];
        const rowCount = item[`${prefix}row_count`];
        const parts = [];
        if (lastDate) parts.push(`<span title="${esc(formatDate(lastDate))}">${timeAgo(lastDate)}</span>`);
        if (rowCount != null) {
            const zeroClass = Number(rowCount) === 0 ? ' class="lin-rows-zero"' : "";
            parts.push(`<span${zeroClass} title="Rows from latest probe">${fmtRows(rowCount)} rows</span>`);
        }
        return parts.length ? `<div class="lin-card-facts">${parts.join('<span class="lin-card-sep">-</span>')}</div>` : "";
    };
    const stDot = (item, prefix = "") => {
        const state = lineageState(item, prefix);
        const lastDate = item[`${prefix}last_data_at`];
        const status = item[`${prefix}status`] || "unknown";
        const c = { ok: "var(--green)", err: "var(--red)", warn: "var(--yellow)" };
        let title = "Freshness rule not set";
        if (hasLineageRule(item, prefix)) {
            title = lastDate ? `${status}: refreshed ${timeAgo(lastDate)}` : `${status}: no refresh date`;
        }
        const label = state === "ok" ? "Healthy" : state === "err" ? "Degraded" : "Unknown";
        return `<span class="lin-status"><span class="lin-dot" style="background:${c[state]}" title="${esc(title)}"></span><span>${label}</span></span>`;
    };

    // === Column HTML builders ===
    const colHtml = {};
    const catOrder = ["Charts", "Cards", "Slicers", "Tables & Matrices", "Other"];
    let visCount = 0;

    // -- Visuals --
    let visH = "";
    for (const [pageName, typeMap] of pageGroups) {
        let pCount = 0;
        for (const arr of typeMap.values()) pCount += arr.length;
        visCount += pCount;
        let typeH = "";
        for (const cat of catOrder) {
            const vs = typeMap.get(cat);
            if (!vs) continue;
            let vr = "";
            for (const v of vs) {
                const label = v.title || (v.fields.length > 0 ? v.fields.slice(0, 2).map(f => f.split(".").pop().replace(/_/g, " ")).join(", ") : "") || _visualTypeLabel(v.type);
                vr += `<div class="lin-subrow" data-lin-id="${v.id}"><span class="lin-vtype">${_visualTypeLabel(v.type)}</span><span class="lin-subrow-label">${esc(label)}</span></div>`;
            }
            typeH += `<div class="lin-subgroup"><div class="lin-subgroup-hdr"><button type="button" class="lin-chev" data-lin-toggle aria-label="Expand ${esc(cat)}">&#9654;</button><span>${cat}</span><span class="lin-cnt">${vs.length}</span></div><div class="lin-subgroup-body">${vr}</div></div>`;
        }
        visH += `<div class="lin-card" data-lin-id="page-${pageName.replace(/"/g, "&quot;")}"><div class="lin-card-hdr"><button type="button" class="lin-chev" data-lin-toggle aria-label="Expand ${esc(pageName)}">&#9654;</button><span class="lin-card-lbl">${esc(pageName)}</span><span class="lin-card-meta">${pCount}</span></div><div class="lin-card-body">${typeH}</div></div>`;
    }
    colHtml.visuals = visH;

    // -- Tables --
    let tblH = "";
    for (const t of tableNodes) {
        const fields = fieldsByTable.get(t.name) || [];
        const noSrc = t.source_id ? "" : ' <span class="lin-hint">no source</span>';
        let fH = "";
        if (fields.length > 0) {
            const fRows = fields.map(f => {
                const isMeasure = /^[A-Z]/.test(f.field) && /\s/.test(f.field);
                return `<div class="lin-subrow" data-lin-id="${f.id}"><span class="lin-ficon">${isMeasure ? "fx" : "&#9632;"}</span><span class="lin-subrow-label">${esc(f.field)}</span>${isMeasure ? '<span class="lin-fbadge">measure</span>' : ""}</div>`;
            }).join("");
            fH = `<div class="lin-card-body">${fRows}</div>`;
        }
        tblH += `<div class="lin-card" data-lin-id="table-${t.name}"><div class="lin-card-hdr">${fields.length ? `<button type="button" class="lin-chev" data-lin-toggle aria-label="Expand ${esc(t.name)} fields">&#9654;</button>` : ''}<span class="lin-card-lbl">${esc(t.name)}</span>${fields.length ? `<span class="lin-card-meta">${fields.length} fields</span>` : ""}${noSrc}</div>${fH}</div>`;
    }
    colHtml.tables = tblH;

    // -- Sources and every recursively upstream level --
    // Build dep map: source_id -> list of upstream source objects
    const depMap = new Map();
    for (const d of (data.source_deps || [])) {
        if (!depMap.has(d.source_id)) depMap.set(d.source_id, []);
        depMap.get(d.source_id).push(d);
    }

    // Check which MVs have stale upstream data
    const mvStaleUpstream = new Set();
    for (const s of allSourceNodes) {
        const deps = depMap.get(s.id);
        if (!deps) continue;
        for (const d of deps) {
            if (s.last_data_at && d.depends_on_last_data_at && d.depends_on_last_data_at > s.last_data_at) {
                mvStaleUpstream.add(s.id);
            }
        }
    }

    const sourceCardHtml = (s, isUpstream = false) => {
        const hasDeps = depMap.has(s.id);
        const isMV = hasDeps ? ' <span class="lin-mv-badge">MV</span>' : '';
        const staleUp = mvStaleUpstream.has(s.id) ? ' <span class="lin-dep-warn" title="Upstream data is newer than last refresh">!</span>' : '';
        const upstreamClass = isUpstream ? " lin-src-upstream" : "";
        const refresh = hasDeps && s.postgres_ref
            ? `<button class="lin-refresh-action" type="button" data-lin-refresh-mv="${s.id}" aria-label="Refresh materialized view ${esc(s.name)}">Refresh</button>`
            : "";
        return `<div class="lin-card lin-src${upstreamClass} ${stCls(s)}" data-lin-id="source-${s.id}" title="${esc(s.name)}"><div class="lin-card-hdr">${stDot(s)}<span class="lin-card-lbl">${esc(s.name)}</span>${isMV}${staleUp}${refresh}</div>${sourceFacts(s)}</div>`;
    };

    colHtml.sources = sourceNodes.map(s => sourceCardHtml(s)).join("");
    const upstreamColumnDefs = [];
    for (let depth = 1; depth < sourceLayers.length; depth++) {
        const layer = sourceLayers[depth] || [];
        if (layer.length === 0) continue;
        const key = `mv_upstream_${depth}`;
        colHtml[key] = layer.map(s => sourceCardHtml(s, true)).join("");
        upstreamColumnDefs.push({ key, label: `Upstream ${depth}`, stateKey: "mv_upstream" });
    }

    // -- Flows --
    const flowStateClass = flow => {
        if (flow.last_status === "failed") return "lin-st-err";
        if (flow.last_status === "succeeded") return "lin-st-ok";
        return "lin-st-warn";
    };
    let flowH = "";
    for (const flow of flowNodes) {
        const active = flow.has_active_run || ["queued", "claimed", "running"].includes(flow.last_status);
        const statusLabel = flow.last_status ? flow.last_status.replaceAll("_", " ") : "never run";
        const loaded = flow.last_success_at
            ? `<span title="${esc(formatDate(flow.last_success_at))}">Loaded ${timeAgo(flow.last_success_at)}</span>`
            : "<span>Never loaded</span>";
        const target = [flow.sql_schema, flow.sql_table].filter(Boolean).join(".");
        flowH += `<div class="lin-card lin-flow ${flowStateClass(flow)}" data-lin-id="flow-${flow.id}" title="${esc(flow.last_error || flow.name)}">
            <div class="lin-card-hdr"><span class="lin-card-lbl">${esc(flow.name)}</span><span class="lin-flow-status">${esc(statusLabel)}</span><button class="lin-refresh-action" type="button" data-lin-refresh-flow="${flow.id}" ${active ? "disabled" : ""} aria-label="Refresh flow ${esc(flow.name)}">${active ? "Running" : "Refresh"}</button></div>
            <div class="lin-card-facts">${loaded}${target ? `<span class="lin-card-sep">-</span><span title="Target table">${esc(target)}</span>` : ""}</div>
        </div>`;
    }
    colHtml.flows = flowH;

    // -- Scripts --
    let scrH = "";
    for (const s of scriptNodes) {
        const m = s.machine_alias || s.hostname || "";
        scrH += `<div class="lin-card" data-lin-id="script-${s.id}"><div class="lin-card-hdr"><span class="lin-card-lbl">${esc(s.display_name)}</span>${m ? `<span class="lin-card-meta">${esc(m)}</span>` : ""}</div></div>`;
    }
    colHtml.scripts = scrH;

    // -- Tasks --
    let tskH = "";
    for (const t of taskNodes) {
        const ok = t.enabled && t.last_result === "0";
        const warn = t.enabled && t.last_result && t.last_result !== "0";
        const cls = !t.enabled ? "lin-st-off" : warn ? "lin-st-warn" : ok ? "lin-st-ok" : "";
        tskH += `<div class="lin-card ${cls}" data-lin-id="task-${t.id}"><div class="lin-card-hdr"><span class="lin-card-lbl">${esc(t.task_name)}</span>${t.schedule_type ? `<span class="lin-card-meta">${esc(t.schedule_type)}</span>` : ""}</div></div>`;
    }
    colHtml.tasks = tskH;

    // -- Upstreams --
    let upH = "";
    for (const u of upstreamNodes) {
        upH += `<div class="lin-card lin-upstream" data-lin-id="upstream-${u.id}"><div class="lin-card-hdr"><span class="lin-card-lbl">${esc(u.name)}</span>${u.refresh_day ? `<span class="lin-card-meta">${esc(u.refresh_day)}</span>` : ""}</div></div>`;
    }
    colHtml.upstreams = upH;

    const colCounts = { visuals: visCount, tables: tableNodes.length, sources: sourceNodes.length, flows: flowNodes.length, scripts: scriptNodes.length, tasks: taskNodes.length, upstreams: upstreamNodes.length };
    for (let depth = 1; depth < sourceLayers.length; depth++) {
        colCounts[`mv_upstream_${depth}`] = (sourceLayers[depth] || []).length;
    }

    // Build grid
    const columnDefs = [];
    for (const col of LINEAGE_COLS) {
        if (col.key === "mv_upstream") {
            if (colState.mv_upstream) columnDefs.push(...upstreamColumnDefs);
        } else if (colState[col.key]) {
            columnDefs.push({ ...col, stateKey: col.key });
        }
    }
    const activeCols = columnDefs.filter(col => colHtml[col.key] || col.key === "visuals" || col.key === "tables");
    const gridCols = activeCols.map(() => "minmax(180px, 1fr)").join(" ");
    let gridH = "";
    for (const col of activeCols) {
        const empty = !colHtml[col.key];
        gridH += `<div class="lin-col" data-lin-col="${col.key}"><div class="lin-col-hdr">${col.label} <span class="lin-col-cnt">${colCounts[col.key] || 0}</span></div>${empty ? '<div class="lin-empty">None linked</div>' : colHtml[col.key]}</div>`;
    }

    const stLabel = data.report.archived ? "archived" : (data.report.status || "unknown");
    if (_linSettleTimer !== null) {
        clearTimeout(_linSettleTimer);
        _linSettleTimer = null;
    }
    if (_linTransitionFrame !== null) {
        cancelAnimationFrame(_linTransitionFrame);
        _linTransitionFrame = null;
    }
    container.innerHTML = `
        <div class="lineage-report-header">
            <strong>${esc(data.report.name)}</strong>
            <span class="lineage-report-status lineage-report-status-${stLabel.replace(/\s+/g, "-")}">${stLabel}</span>
            ${data.report.owner ? `<span class="lineage-report-owner">${esc(data.report.owner)}</span>` : ""}
        </div>
        <div class="lin-wrap" id="lin-wrap">
            <div class="lin-grid" id="lin-grid" style="grid-template-columns:${gridCols}">${gridH}</div>
            <svg class="lin-svg" id="lin-svg"></svg>
        </div>
        <div class="lin-hint-bar">Click any node to trace its lineage. Scroll horizontally for deeper levels. Click empty space to reset.</div>
    `;

    _buildLinGraph(data, visualNodes, fieldsByTable, tableNodes, allSourceNodes, scriptNodes, taskNodes, upstreamNodes);
    window._lineageBindTimer = setTimeout(() => {
        window._lineageBindTimer = null;
        _drawLinEdges();
        _bindLinInteractions();
    }, 60);
}

async function _showLineageSourceDetail(sourceId) {
    const id = parseInt(sourceId, 10);
    const container = document.getElementById("lineage-container");
    if (!container || isNaN(id)) return;

    const scriptPanel = document.getElementById("lineage-script-detail");
    if (scriptPanel) scriptPanel.remove();

    let panel = document.getElementById("lineage-source-detail");
    if (!panel) {
        panel = document.createElement("div");
        panel.id = "lineage-source-detail";
        panel.className = "source-detail-panel lineage-source-detail-panel";
        container.appendChild(panel);
    }
    panel.innerHTML = '<div class="lineage-source-detail-loading">Loading source details...</div>';

    try {
        const source = await api(`/api/sources/${id}`);
        const parsed = parseSourceName(source);
        const location = source.connection_info || parsed.fullLocation || source.name;
        const lastRefreshed = source.last_updated ? esc(formatCompactTimestamp(source.last_updated)) : "-";

        panel.innerHTML = `
            <div class="source-detail-header">
                <h2>${esc(parsed.shortName)}</h2>
                <button class="btn-outline" id="btn-close-lineage-source-detail">&times; Close</button>
            </div>
            <div class="detail-grid lineage-source-detail-grid">
                <div class="detail-item"><div class="detail-label">Type</div>${typeBadge(source.type)}</div>
                <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(source.status)}</div>
                <div class="detail-item"><div class="detail-label">Last Refreshed</div><span class="lineage-exact-timestamp" title="${esc(source.last_updated || "")}">${lastRefreshed}</span></div>
                <div class="detail-item"><div class="detail-label">Rows</div>${rowCountHtml(source.row_count)}</div>
                <div class="detail-item"><div class="detail-label">Source Refresh</div><span style="color:var(--text)">${source.refresh_schedule ? 'Weekly - ' + esc(source.refresh_schedule) : "-"}</span></div>
                <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${esc(source.owner) || "-"}</span></div>
                <div class="detail-item"><div class="detail-label">Upstream System</div><span style="color:var(--text)">${esc(source.upstream_name) || "-"}</span></div>
                <div class="detail-item"><div class="detail-label">Upstream Refresh</div><span style="color:var(--text)">${esc(source.upstream_refresh_day) || "-"}</span></div>
                <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Location</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${esc(location)} ${_viewPathBtn(location)}</span></div>
            </div>
            <h2>Freshness Rule</h2>
            ${_freshnessRuleFormHtml(source, { prefix: "lineage-freshness", wide: true })}
        `;

        panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

        const closeBtn = panel.querySelector("#btn-close-lineage-source-detail");
        if (closeBtn) closeBtn.addEventListener("click", () => panel.remove());

        panel.querySelectorAll(".view-path-btn").forEach(btn => {
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                const p = btn.dataset.path;
                try {
                    await apiPostJson("/api/scanner/open-path", { path: p });
                } catch (err) {
                    toast("Could not open path (only works on server machine)");
                }
            });
        });

        _bindFreshnessRuleForm(panel, source, {
            prefix: "lineage-freshness",
            afterChange: () => _showLineageSourceDetail(source.id),
        });
    } catch (err) {
        panel.innerHTML = `<div class="lineage-source-detail-loading" style="color:var(--red)">Failed to load source: ${esc(err.message)}</div>`;
    }
}

async function _showLineageScriptDetail(scriptId) {
    const id = parseInt(scriptId, 10);
    const container = document.getElementById("lineage-container");
    if (!container || isNaN(id)) return;

    const sourcePanel = document.getElementById("lineage-source-detail");
    if (sourcePanel) sourcePanel.remove();

    let panel = document.getElementById("lineage-script-detail");
    if (!panel) {
        panel = document.createElement("div");
        panel.id = "lineage-script-detail";
        panel.className = "source-detail-panel lineage-source-detail-panel";
        container.appendChild(panel);
    }
    panel.innerHTML = '<div class="lineage-source-detail-loading">Loading script details...</div>';

    try {
        const [script, tables] = await Promise.all([
            api(`/api/scripts/${id}`),
            api(`/api/scripts/${id}/tables`),
        ]);
        const writeRows = tables.filter(t => t.direction === "write");
        const readRows = tables.filter(t => t.direction === "read");
        const machine = script.machine_alias || script.hostname || "-";
        const lastModified = script.last_modified ? esc(script.last_modified) : "-";
        const lastScanned = script.last_scanned ? esc(script.last_scanned) : "-";

        const tableBadges = (rows, mode) => rows.length > 0
            ? rows.map(t => {
                const ref = _refType(t.table_name);
                const cls = mode === "write" && ref.type === "sql" ? "badge-red" : mode === "read" && ref.type === "sql" ? "badge-blue" : ref.cls;
                if (t.source_id) {
                    return `<span class="badge ${cls} lineage-script-source-link" style="cursor:pointer;margin:2px" data-source-id="${t.source_id}" title="Click to view source">${esc(ref.label)}</span>`;
                }
                return `<span class="badge ${cls}" style="margin:2px">${esc(ref.label)}</span>`;
            }).join(" ")
            : '<span style="color:var(--text-dim)">None detected</span>';

        panel.innerHTML = `
            <div class="source-detail-header">
                <h2>${esc(script.display_name)}</h2>
                <button class="btn-outline" id="btn-close-lineage-script-detail">&times; Close</button>
            </div>
            <div class="detail-grid lineage-source-detail-grid">
                <div class="detail-item"><div class="detail-label">Category</div><span class="badge ${_CATEGORY_COLORS[_scriptCategory(script)] || 'badge-muted'}">${esc(_scriptCategory(script))}</span></div>
                <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${esc(script.owner) || "-"}</span></div>
                <div class="detail-item"><div class="detail-label">Machine</div><span style="color:var(--text)">${esc(machine)}</span></div>
                <div class="detail-item"><div class="detail-label">File Size</div><span style="color:var(--text)">${formatFileSize(script.file_size)}</span></div>
                <div class="detail-item"><div class="detail-label">Last Modified</div><span class="lineage-exact-timestamp">${lastModified}</span></div>
                <div class="detail-item"><div class="detail-label">Last Scanned</div><span class="lineage-exact-timestamp">${lastScanned}</span></div>
                <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Path</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${esc(script.path)} ${_viewPathBtn(script.path)}</span></div>
            </div>
            <h2>Writes to / Refreshes (${writeRows.length})</h2>
            <div style="padding:0.25rem 0">${tableBadges(writeRows, "write")}</div>
            <h2>Reads From (${readRows.length})</h2>
            <div style="padding:0.25rem 0">${tableBadges(readRows, "read")}</div>
        `;

        panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

        const closeBtn = panel.querySelector("#btn-close-lineage-script-detail");
        if (closeBtn) closeBtn.addEventListener("click", () => panel.remove());

        panel.querySelectorAll(".view-path-btn").forEach(btn => {
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                try { await apiPostJson("/api/scanner/open-path", { path: btn.dataset.path }); }
                catch { toast("Could not open path (only works on server machine)"); }
            });
        });

        panel.querySelectorAll(".lineage-script-source-link").forEach(el => {
            el.addEventListener("click", (e) => {
                e.stopPropagation();
                _showLineageSourceDetail(el.dataset.sourceId);
            });
        });
    } catch (err) {
        panel.innerHTML = `<div class="lineage-source-detail-loading" style="color:var(--red)">Failed to load script: ${esc(err.message)}</div>`;
    }
}

function _buildLinGraph(data, visualNodes, fieldsByTable, tableNodes, sourceNodes, scriptNodes, taskNodes, upstreamNodes) {
    const fwd = new Map(), bwd = new Map(), svgEdges = [];
    function add(a, b, svg) {
        if (!fwd.has(a)) fwd.set(a, new Set()); fwd.get(a).add(b);
        if (!bwd.has(b)) bwd.set(b, new Set()); bwd.get(b).add(a);
        if (svg) svgEdges.push({ from: a, to: b });
    }
    // Visual -> Field (detail)
    for (const v of visualNodes) for (const fk of v.fields) add(v.id, `field-${fk}`, false);
    // Page -> Table (SVG)
    const ptDone = new Set();
    for (const v of visualNodes) for (const fk of v.fields) {
        const tbl = fk.split(".")[0];
        const k = `page-${v.page}|table-${tbl}`;
        if (!ptDone.has(k)) { ptDone.add(k); add(`page-${v.page}`, `table-${tbl}`, true); }
    }
    // Field -> Table (detail)
    for (const [tbl, fields] of fieldsByTable) for (const f of fields) add(f.id, `table-${tbl}`, false);
    // Table -> Source (SVG)
    for (const t of tableNodes) if (t.source_id) add(`table-${t.name}`, `source-${t.source_id}`, true);
    // Source -> Script (SVG)
    for (const s of scriptNodes) for (const sid of (s.source_ids || [])) add(`source-${sid}`, `script-${s.id}`, true);
    // Script -> Task (SVG)
    for (const t of taskNodes) add(`script-${t.script_id}`, `task-${t.id}`, true);
    // Source -> Upstream dependency (MV -> upstream table) (SVG)
    for (const d of (data.source_deps || [])) {
        add(`source-${d.source_id}`, `source-${d.depends_on_id}`, true);
    }
    // Target source -> Flow that loads it (SVG)
    for (const flow of (data.flows || [])) {
        for (const sourceId of (flow.target_source_ids || [])) {
            add(`source-${sourceId}`, `flow-${flow.id}`, true);
        }
    }
    // Source -> Upstream system (SVG)
    for (const s of sourceNodes) if (s.upstream_id) add(`source-${s.id}`, `upstream-${s.upstream_id}`, true);

    window._linFwd = fwd;
    window._linBwd = bwd;
    window._linSvgEdges = svgEdges;
}

// Edge routing constants. Forward edges use monotone curves between distributed
// card ports, avoiding the shared vertical rails that made even sparse graphs
// look tangled. Same-column edges loop right; backward edges stay bounded.
const LIN_EDGE_RADIUS = 8;    // corner rounding on the two elbows
const LIN_EDGE_STUB = 8;      // keep channels clear of the column edges
const LIN_EDGE_LANE_GAP = 6;  // vertical clearance before two runs share a lane
const LIN_EDGE_LANE_MIN = 4;  // narrowest usable spacing between channels
const LIN_EDGE_PORT_GAP = 6;  // preferred spacing between ports on one card
const LIN_EDGE_ARROW = '<defs><marker id="lin-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path d="M1,1.2 L7,4 L1,6.8 Z"/></marker></defs>';

let _linSettleTimer = null;
let _linTransitionFrame = null;

function _linScheduleRedraw() {
    _drawLinEdges();
    if (_linSettleTimer !== null) clearTimeout(_linSettleTimer);
    _linSettleTimer = setTimeout(() => {
        _linSettleTimer = null;
        _drawLinEdges();
    }, 380);
}

/** Vertical attach band: prefer the card header so edges aim at the title
 *  rather than at the middle of a long expanded body. */
function _linAnchorBand(el, rect) {
    const hdr = el.querySelector(":scope > .lin-card-hdr");
    let band = rect;
    if (hdr) {
        const hr = hdr.getBoundingClientRect();
        if (hr.height > 2) band = hr;
    }
    const y = band.top + band.height / 2;
    const inset = Math.min(3, band.height / 2);
    return { y, top: band.top + inset, bottom: band.bottom - inset };
}

function _linAnchorY(el, rect) { return _linAnchorBand(el, rect).y; }

function _linRound(n) { return Math.round(n * 10) / 10; }
function _linClamp(n, min, max) { return Math.min(Math.max(n, min), max); }

/** Emit a routed edge. The orthogonal branch remains available to the pure
 *  channel-packing tests, while the live forward router uses smooth fan curves. */
function _linEdgePath(e) {
    const x1 = _linRound(e.x1), y1 = _linRound(e.y1);
    const x2 = _linRound(e.x2), y2 = _linRound(e.y2);
    if (e.cx != null) {
        const cx = _linRound(e.cx);
        return `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}`;
    }
    if (Math.abs(y2 - y1) < 1.5) return `M${x1},${y1} L${x2},${y2}`;
    if (e.c1x != null && e.c2x != null) {
        const c1x = _linRound(e.c1x), c2x = _linRound(e.c2x);
        return `M${x1},${y1} C${c1x},${y1} ${c2x},${y2} ${x2},${y2}`;
    }
    if (e.xc == null || x2 - x1 < 4 * LIN_EDGE_RADIUS) {
        const mid = _linRound(e.bow == null ? (x1 + x2) / 2 : e.bow);
        return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
    }
    const xc = _linRound(Math.min(Math.max(e.xc, x1 + LIN_EDGE_RADIUS), x2 - LIN_EDGE_RADIUS));
    const r = _linRound(Math.min(LIN_EDGE_RADIUS, Math.abs(y2 - y1) / 2, xc - x1, x2 - xc));
    const dir = y2 > y1 ? 1 : -1;
    return `M${x1},${y1} L${_linRound(xc - r)},${y1}`
        + ` Q${xc},${y1} ${xc},${_linRound(y1 + dir * r)}`
        + ` L${xc},${_linRound(y2 - dir * r)}`
        + ` Q${xc},${y2} ${_linRound(xc + r)},${y2} L${x2},${y2}`;
}

/** Give every vertical run in a gutter its own channel: greedily pack the runs
 *  into lanes (runs that do not overlap vertically reuse a lane), then spread
 *  the lanes evenly across the gap. */
function _linAssignChannels(group, gutterLeft, gutterRight) {
    const band = Math.max(0, gutterRight - gutterLeft - LIN_EDGE_STUB * 2);
    const slots = Math.max(1, Math.floor(band / LIN_EDGE_LANE_MIN));
    const laneEnd = [];
    group.sort((a, b) => Math.min(a.y1, a.y2) - Math.min(b.y1, b.y2));
    for (const e of group) {
        const top = Math.min(e.y1, e.y2), bottom = Math.max(e.y1, e.y2);
        let lane = laneEnd.findIndex(end => end + LIN_EDGE_LANE_GAP < top);
        if (lane === -1 && laneEnd.length < slots) {
            lane = laneEnd.length;
            laneEnd.push(bottom);
        } else if (lane === -1) {
            const firstFree = Math.min(...laneEnd);
            lane = laneEnd.indexOf(firstFree);
            laneEnd[lane] = Math.max(laneEnd[lane], bottom);
        } else {
            laneEnd[lane] = Math.max(laneEnd[lane], bottom);
        }
        e.lane = lane;
    }
    if (band <= LIN_EDGE_LANE_MIN) {
        for (const e of group) e.xc = (gutterLeft + gutterRight) / 2;
        return;
    }
    const used = laneEnd.length;
    for (const e of group) {
        e.xc = gutterLeft + LIN_EDGE_STUB + band * (e.lane + 1) / (used + 1);
    }
}

/** Spread fan-in/fan-out edges across the available header band. */
function _linAssignPorts(edges) {
    function assign(key, port, minKey, maxKey, farPort, tieKey) {
        const groups = new Map();
        for (const e of edges) {
            if (!groups.has(e[key])) groups.set(e[key], []);
            groups.get(e[key]).push(e);
        }
        for (const group of groups.values()) {
            if (group.length < 2) continue;
            group.sort((a, b) => a[farPort] - b[farPort]
                || String(a[tieKey]).localeCompare(String(b[tieKey])));
            const center = group.reduce((sum, e) => sum + e[port], 0) / group.length;
            const min = Math.max(...group.map(e => e[minKey]));
            const max = Math.min(...group.map(e => e[maxKey]));
            const room = Math.max(0, Math.min(center - min, max - center));
            const step = Math.min(LIN_EDGE_PORT_GAP, 2 * room / (group.length - 1));
            group.forEach((e, i) => {
                const offset = (i - (group.length - 1) / 2) * step;
                e[port] = _linClamp(center + offset, e[minKey], e[maxKey]);
            });
        }
    }
    assign("from", "y1", "y1min", "y1max", "y2", "to");
    assign("to", "y2", "y2min", "y2max", "y1", "from");
    return edges;
}

/** Pack vertically overlapping curves into conflict lanes. */
function _linAssignCurveLanes(group) {
    const laneEnd = [];
    group.sort((a, b) => Math.min(a.y1, a.y2) - Math.min(b.y1, b.y2));
    for (const e of group) {
        const top = Math.min(e.y1, e.y2), bottom = Math.max(e.y1, e.y2);
        let lane = laneEnd.findIndex(end => end + LIN_EDGE_LANE_GAP < top);
        if (lane === -1) {
            lane = laneEnd.length;
            laneEnd.push(bottom);
        } else {
            laneEnd[lane] = Math.max(laneEnd[lane], bottom);
        }
        e.lane = lane;
    }
    return laneEnd.length;
}

/** Pure edge router. All inputs and column bounds use the SVG coordinate space. */
function _linRouteEdges(edges, colBounds) {
    _linAssignPorts(edges);

    const byCurvePair = new Map();
    for (const e of edges) {
        delete e.xc;
        delete e.cx;
        delete e.bow;
        delete e.c1x;
        delete e.c2x;
        delete e.lane;
        if (e.cj > e.ci) {
            const span = Math.max(0, e.x2 - e.x1);
            const handle = _linClamp(
                span * (e.cj === e.ci + 1 ? 0.42 : 0.24),
                Math.min(18, span / 2),
                Math.min(96, span / 2),
            );
            e.c1x = e.x1 + handle;
            e.c2x = e.x2 - handle;
        } else {
            const key = `${e.ci}:${e.cj}`;
            if (!byCurvePair.has(key)) byCurvePair.set(key, []);
            byCurvePair.get(key).push(e);
        }
    }

    for (const group of byCurvePair.values()) {
        const lanes = _linAssignCurveLanes(group);
        for (const e of group) {
            if (e.ci === e.cj) {
                const column = colBounds[e.ci];
                if (!column) continue;
                const gutterRight = colBounds[e.ci + 1]?.left ?? column.right + 24;
                const gutterBand = Math.max(0, gutterRight - column.right);
                const band = Math.max(0, gutterBand - LIN_EDGE_STUB * 2);
                const spacing = Math.min(LIN_EDGE_LANE_MIN, band / (lanes + 1));
                const minCx = column.right + LIN_EDGE_STUB;
                const maxCx = Math.max(minCx, column.right + gutterBand - LIN_EDGE_STUB);
                e.cx = _linClamp(minCx + e.lane * spacing, minCx, maxCx);
            } else {
                const minX = Math.min(e.x1, e.x2), maxX = Math.max(e.x1, e.x2);
                const minBow = minX + LIN_EDGE_RADIUS, maxBow = maxX - LIN_EDGE_RADIUS;
                const centered = (e.x1 + e.x2) / 2 + (e.lane - (lanes - 1) / 2) * 10;
                e.bow = minBow <= maxBow
                    ? _linClamp(centered, minBow, maxBow)
                    : (e.x1 + e.x2) / 2;
            }
        }
    }
    return edges;
}

function _drawLinEdges() {
    const svg = document.getElementById("lin-svg");
    const wrap = document.getElementById("lin-wrap");
    if (!svg || !wrap) return;
    const wr = wrap.getBoundingClientRect();
    const ox = wrap.scrollLeft - wr.left, oy = wrap.scrollTop - wr.top;
    svg.innerHTML = LIN_EDGE_ARROW;
    svg.setAttribute("width", wrap.scrollWidth);
    svg.setAttribute("height", wrap.scrollHeight);

    // Column bounds define the routing gutters.
    const colEls = [...wrap.querySelectorAll(".lin-col")].filter(c => c.offsetParent !== null);
    const colIndex = new Map();
    const colBounds = colEls.map((c, i) => {
        colIndex.set(c, i);
        const r = c.getBoundingClientRect();
        return { left: r.left + ox, right: r.right + ox };
    });

    const highlighted = window._linHL || null;
    const edges = [];
    for (const e of (window._linSvgEdges || [])) {
        // While a lineage is traced, everything off the path is collapsed to
        // zero height - drawing those edges only adds invisible clutter.
        if (highlighted && !(highlighted.has(e.from) && highlighted.has(e.to))) continue;
        const fromEl = wrap.querySelector(`[data-lin-id="${CSS.escape(e.from)}"]`);
        const toEl = wrap.querySelector(`[data-lin-id="${CSS.escape(e.to)}"]`);
        if (!fromEl || !toEl) continue;
        const fc = fromEl.closest(".lin-col"), tc = toEl.closest(".lin-col");
        if (!fc || !tc || fc.offsetParent === null || tc.offsetParent === null) continue;
        const fr = fromEl.getBoundingClientRect(), tr = toEl.getBoundingClientRect();
        if (fr.width < 2 || tr.width < 2 || fr.height < 2 || tr.height < 2) continue;
        const ci = colIndex.get(fc), cj = colIndex.get(tc);
        const fromBand = _linAnchorBand(fromEl, fr);
        const toBand = _linAnchorBand(toEl, tr);
        edges.push({
            from: e.from, to: e.to, hl: !!highlighted,
            x1: fr.right + ox, y1: fromBand.y + oy,
            x2: (ci === cj ? tr.right : tr.left) + ox, y2: toBand.y + oy,
            y1min: fromBand.top + oy, y1max: fromBand.bottom + oy,
            y2min: toBand.top + oy, y2max: toBand.bottom + oy,
            ci, cj,
        });
    }

    _linRouteEdges(edges, colBounds);

    for (const e of edges) {
        const halo = document.createElementNS("http://www.w3.org/2000/svg", "path");
        halo.setAttribute("d", _linEdgePath(e));
        halo.setAttribute("class", e.hl ? "lin-edge-halo lin-edge-halo-hl" : "lin-edge-halo");
        svg.appendChild(halo);

        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", _linEdgePath(e));
        path.setAttribute("class", e.hl ? "lin-edge lin-edge-hl" : "lin-edge");
        if (e.hl) path.setAttribute("marker-end", "url(#lin-arrow)");
        path.dataset.from = e.from; path.dataset.to = e.to;
        svg.appendChild(path);
    }
}

function _bindLinInteractions() {
    const wrap = document.getElementById("lin-wrap");
    if (!wrap) return;
    wrap.querySelectorAll("[data-lin-refresh-flow]").forEach(button => {
        button.addEventListener("click", async event => {
            event.preventDefault();
            event.stopPropagation();
            if (button.disabled) return;
            const flowId = Number(button.dataset.linRefreshFlow);
            const flow = (window._lineageData?.flows || []).find(item => item.id === flowId);
            button.disabled = true;
            button.textContent = "Queueing...";
            try {
                await apiPost(`/api/flows/${flowId}/run`);
                if (flow) {
                    flow.has_active_run = true;
                    flow.last_status = "queued";
                }
                toast(`Flow refresh queued${flow?.name ? ` for ${flow.name}` : ""}.`);
                if (window._lineageData) _renderLineageDiagram(window._lineageData);
            } catch (err) {
                button.disabled = false;
                button.textContent = "Refresh";
                toast("Flow refresh was not queued: " + err.message);
            }
        });
    });
    wrap.querySelectorAll("[data-lin-refresh-mv]").forEach(button => {
        button.addEventListener("click", async event => {
            event.preventDefault();
            event.stopPropagation();
            if (button.disabled) return;
            const sourceId = Number(button.dataset.linRefreshMv);
            const source = (window._lineageData?.sources || []).find(item => item.id === sourceId);
            if (!source?.postgres_ref) {
                toast("Materialized view refresh is unavailable because its PostgreSQL name could not be resolved.");
                return;
            }
            button.disabled = true;
            button.textContent = "Refreshing...";
            try {
                await apiPostJson("/api/data-import/materialized-views/refresh", {
                    materialized_views: [source.postgres_ref],
                });
                toast(`Materialized view ${source.name} refreshed. Freshness updates on the next probe.`);
            } catch (err) {
                toast("Materialized view refresh failed: " + err.message);
            } finally {
                button.disabled = false;
                button.textContent = "Refresh";
            }
        });
    });
    // Expand/collapse
    wrap.querySelectorAll("[data-lin-toggle]").forEach(toggle => {
        toggle.addEventListener("click", (e) => {
            e.stopPropagation();
            const card = toggle.closest(".lin-subgroup") || toggle.closest(".lin-card");
            if (!card) return;
            card.classList.toggle("expanded");
            _linScheduleRedraw();
        });
    });
    // Click to highlight and collapse
    wrap.querySelectorAll("[data-lin-id]").forEach(node => {
        node.addEventListener("click", (e) => {
            if (e.target.closest("[data-lin-toggle]") && !e.target.closest(".lin-subrow")) return;
            // Don't trigger highlight when clicking inside an expanded card body (let inner interactions work)
            if (node.classList.contains("expanded") && e.target.closest(".lin-card-body") && !e.target.closest(".lin-subrow")) return;
            e.stopPropagation();
            const id = node.dataset.linId;
            if (id.startsWith("source-")) _showLineageSourceDetail(id.replace("source-", ""));
            if (id.startsWith("script-")) _showLineageScriptDetail(id.replace("script-", ""));
            if (node.classList.contains("lin-highlighted")) { _resetLinHL(); return; }
            const connected = _traceLinLineage(id);
            window._linHL = connected;
            // Hide SVG immediately so stale edges don't show during transition
            const svg = document.getElementById("lin-svg");
            if (svg) svg.style.opacity = "0";
            wrap.querySelectorAll("[data-lin-id]").forEach(n => {
                const c = connected.has(n.dataset.linId);
                n.classList.toggle("lin-highlighted", c);
                n.classList.toggle("lin-dimmed", !c);
            });
            // Mark columns that have no highlighted cards
            wrap.querySelectorAll(".lin-col").forEach(col => {
                const hasHL = col.querySelector(".lin-highlighted");
                col.classList.toggle("lin-col-empty", !hasHL);
            });
            // Auto-expand groups with highlighted items
            wrap.querySelectorAll(".lin-card, .lin-subgroup").forEach(card => {
                if (card.querySelector(".lin-highlighted")) card.classList.add("expanded");
            });
            // Redraw edges after collapse transition finishes
            setTimeout(() => {
                _drawLinEdges();
                // Fade SVG back in
                if (svg) { svg.style.transition = "opacity 0.2s ease"; svg.style.opacity = "1"; }
            }, 380);
        });
    });
    wrap.addEventListener("click", (e) => { if (!e.target.closest("[data-lin-id]")) _resetLinHL(); });
    const grid = wrap.querySelector(".lin-grid");
    if (grid) {
        grid.addEventListener("transitionend", (event) => {
            if (event.propertyName !== "max-height") return;
            if (_linSettleTimer !== null) {
                clearTimeout(_linSettleTimer);
                _linSettleTimer = null;
            }
            if (_linTransitionFrame !== null) cancelAnimationFrame(_linTransitionFrame);
            _linTransitionFrame = requestAnimationFrame(() => {
                _linTransitionFrame = null;
                _drawLinEdges();
            });
        });
    }
    if (window._linResize) window.removeEventListener("resize", window._linResize);
    window._linResize = () => _drawLinEdges();
    window.addEventListener("resize", window._linResize);
}

function _traceLinLineage(startId) {
    const fwd = window._linFwd, bwd = window._linBwd;
    if (!fwd || !bwd) return new Set([startId]);
    const visited = new Set();
    // Forward (toward upstream/right)
    const q1 = [startId];
    while (q1.length) { const c = q1.shift(); if (visited.has(c)) continue; visited.add(c); const n = fwd.get(c); if (n) for (const x of n) if (!visited.has(x)) q1.push(x); }
    // Backward (toward visuals/left) - separate seen set so startId gets reprocessed
    const bwdSeen = new Set();
    const q2 = [startId];
    while (q2.length) { const c = q2.shift(); if (bwdSeen.has(c)) continue; bwdSeen.add(c); visited.add(c); const n = bwd.get(c); if (n) for (const x of n) if (!bwdSeen.has(x)) q2.push(x); }
    // Include parent containers
    for (const id of [...visited]) {
        if (id.startsWith("visual-")) {
            const el = document.querySelector(`[data-lin-id="${CSS.escape(id)}"]`);
            if (el) { const p = el.closest(".lin-card[data-lin-id^='page-']"); if (p) visited.add(p.dataset.linId); }
        }
        if (id.startsWith("field-")) {
            const tbl = id.replace("field-", "").split(".")[0];
            visited.add(`table-${tbl}`);
        }
    }
    return visited;
}

function _resetLinHL() {
    const wrap = document.getElementById("lin-wrap");
    if (!wrap) return;
    // Hide SVG during layout shift
    const svg = document.getElementById("lin-svg");
    if (svg) svg.style.opacity = "0";
    window._linHL = null;
    wrap.querySelectorAll("[data-lin-id]").forEach(n => n.classList.remove("lin-highlighted", "lin-dimmed"));
    wrap.querySelectorAll(".lin-col").forEach(col => col.classList.remove("lin-col-empty"));
    // Redraw edges after expand transition finishes
    setTimeout(() => {
        _drawLinEdges();
        if (svg) { svg.style.transition = "opacity 0.2s ease"; svg.style.opacity = "1"; }
    }, 380);
}


const ENTITY_TYPE_LABELS = {
    report: "Report",
    source: "Data Source",
    script: "Script",
    upstream_system: "Upstream System",
    scheduled_task: "Scheduled Task",
};

function _emailPersonById(personId) {
    return (window._emailPeople || []).find(p => String(p.id) === String(personId));
}

function _emailScheduleForPerson(personId) {
    return window._emailSchedulesByPerson?.get(String(personId)) || null;
}

function _emailScheduleModalHtml(person, schedule) {
    const s = schedule || {};
    const contentTypes = new Set(["alerts"]);
    const recurrence = s.recurrence === "daily" ? "daily" : "weekdays";
    const nextRun = s.next_run_at ? formatDate(s.next_run_at) : "-";
    const lastSent = s.last_sent_at ? formatDate(s.last_sent_at) : "-";
    return `
        <div class="email-schedule-overlay" id="email-schedule-overlay">
            <div class="email-schedule-dialog" role="dialog" aria-modal="true" aria-labelledby="email-schedule-title">
                <div class="email-schedule-dialog-head">
                    <div>
                        <h2 id="email-schedule-title">Schedule ${esc(person.name)}</h2>
                        <p>${person.email ? esc(person.email) : "No email mapped"}</p>
                    </div>
                    <button class="btn-outline" id="email-schedule-close">&times; Close</button>
                </div>
                <label class="email-scheduler-toggle email-schedule-enabled-row">
                    <input type="checkbox" id="email-profile-schedule-enabled" ${s.enabled ? "checked" : ""}>
                    <span>Enabled</span>
                </label>
                <div class="email-schedule-dialog-grid">
                    <label class="email-scheduler-field">Frequency
                        <select id="email-profile-schedule-recurrence">
                            <option value="daily" ${recurrence === "daily" ? "selected" : ""}>Daily</option>
                            <option value="weekdays" ${recurrence === "weekdays" ? "selected" : ""}>Week-days only</option>
                        </select>
                    </label>
                    <label class="email-scheduler-field">Hour
                        <input type="time" id="email-profile-schedule-time" step="3600" value="${esc(s.send_time || "09:00")}">
                    </label>
                    <div class="email-scheduler-field email-schedule-content-field">
                        <span>Send</span>
                        <div class="email-schedule-content-options">
                            <label><input type="checkbox" class="email-profile-schedule-content" value="alerts" checked disabled> Active alerts</label>
                        </div>
                    </div>
                </div>
                <div class="email-scheduler-footer">
                    <div class="email-scheduler-meta">
                        <span>Next: <strong>${esc(nextRun)}</strong></span>
                        <span>Last sent: <strong>${esc(lastSent)}</strong></span>
                        ${s.last_error ? `<span class="email-scheduler-error" title="${esc(s.last_error)}">Last error: ${esc(s.last_error)}</span>` : ""}
                    </div>
                    <div class="email-scheduler-actions">
                        <button class="btn-outline" id="email-schedule-cancel">Cancel</button>
                        <button class="btn-new-task" id="email-schedule-save">Save Schedule</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

async function _openEmailScheduleModal(personId) {
    const person = _emailPersonById(personId);
    if (!person) return;
    let schedule = _emailScheduleForPerson(personId);
    if (!schedule) {
        schedule = await api(`/api/email-schedules/people/${personId}`);
        window._emailSchedulesByPerson?.set(String(personId), schedule);
    }
    const existing = document.getElementById("email-schedule-overlay");
    if (existing) existing.remove();
    const wrapper = document.createElement("div");
    wrapper.innerHTML = _emailScheduleModalHtml(person, schedule);
    document.body.appendChild(wrapper.firstElementChild);

    const overlay = document.getElementById("email-schedule-overlay");
    const close = () => overlay?.remove();
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close();
    });
    document.getElementById("email-schedule-close")?.addEventListener("click", close);
    document.getElementById("email-schedule-cancel")?.addEventListener("click", close);

    const saveBtn = document.getElementById("email-schedule-save");
    saveBtn?.addEventListener("click", async () => {
        const enabled = document.getElementById("email-profile-schedule-enabled")?.checked || false;
        const contentTypes = [...document.querySelectorAll(".email-profile-schedule-content:checked")].map(el => el.value);
        if (contentTypes.length === 0) {
            toast("Alert summaries must remain selected");
            return;
        }
        if (enabled && !person.email) {
            toast("Save an email address before enabling the schedule");
            return;
        }
        const body = {
            enabled,
            recurrence: document.getElementById("email-profile-schedule-recurrence")?.value || "weekdays",
            send_time: document.getElementById("email-profile-schedule-time")?.value || "09:00",
            content_types: contentTypes,
        };
        saveBtn.disabled = true;
        try {
            const updated = await apiPut(`/api/email-schedules/people/${personId}`, body);
            window._emailSchedulesByPerson?.set(String(personId), updated);
            toast("Email schedule saved");
            close();
            await navigate("email");
        } catch (err) {
            saveBtn.disabled = false;
            toast("Failed to save schedule: " + err.message);
        }
    });
}

function bindEmailProfileSchedules() {
    if (window._emailScheduleDelegated) return;
    window._emailScheduleDelegated = true;
    document.addEventListener("click", (event) => {
        const target = event.target && event.target.closest ? event.target : event.target?.parentElement;
        const btn = target?.closest(".email-schedule-person");
        if (!btn) return;
        event.preventDefault();
        _openEmailScheduleModal(btn.dataset.personId);
    });
}

window.openEmailScheduleModal = _openEmailScheduleModal;

// ── Event Log Page ──

async function renderEventLog() {
    const events = await api("/api/eventlog");
    const cols = [
        { key: "created_at", label: "When", width: COL_W.md, render: e => `<span title="${esc(e.created_at || "")}">${timeAgo(e.created_at)}</span>` },
        { key: "actor", label: "User", width: COL_W.sm, render: e => e.actor ? `<span style="color:var(--accent)">${esc(e.actor)}</span>` : '<span style="color:var(--text-dim)">system</span>' },
        { key: "entity_type", label: "Type", width: COL_W.sm, render: e => `<span class="eventlog-type-badge type-${esc(e.entity_type)}">${esc(e.entity_type)}</span>` },
        { key: "entity_name", label: "Entity", width: COL_W.lg, render: e => esc(e.entity_name) || `#${e.entity_id || "-"}` },
        { key: "action", label: "Action", width: COL_W.sm, render: e => `<span class="eventlog-action action-${esc(e.action)}">${esc(e.action)}</span>` },
        { key: "detail", label: "Detail", width: COL_W.xl, render: e => esc(e.detail) || "" },
    ];

    return `
        <div class="page-header">
            <h1>Event Log</h1>
            <span class="subtitle">${events.length} events</span>
        </div>
        ${dataTable("dt-eventlog", cols, events)}
    `;
}

function bindEventLogPage() {
    bindDataTables();
}


// ── FAQ Page ──

const FAQ_ITEMS = [
    {
        q: "What does this platform do?",
        a: "The data governance panel automatically discovers Power BI reports and their data sources, monitors data freshness, flags issues, and gives the BI team a single place to manage data quality and accountability."
    },
    {
        q: "Where does the data come from?",
        a: "The scanner reads .pbix files and TMDL exports from a shared folder you configure (DG_TMDL_ROOT). It extracts all tables, data sources, measures, and columns automatically. It also scans Python scripts, Windows Task Scheduler, Power Automate flows, and PostgreSQL materialized view dependencies."
    },
    {
        q: "What data source types are supported?",
        a: "SQL Server, PostgreSQL, MySQL, Oracle, CSV files, Excel workbooks, SharePoint lists, web sources, and folder-based imports. The scanner identifies the type from the Power Query M expression in each report."
    },
    {
        q: "How does freshness monitoring work?",
        a: "After a scan, the prober checks when each data source was last updated. PostgreSQL sources are probed directly via track_commit_timestamp. Sources are classified as Healthy or Degraded based on per-source freshness rules you set. Sources without a rule are not monitored - set a rule from the source detail panel to enable tracking."
    },
    {
        q: "What are Report Owner and Business Owner?",
        a: "These are metadata tables inside each Power BI report. Report Owner is typically the developer or analyst who maintains the report. Business Owner is the stakeholder accountable for the data. Both are extracted automatically during scans. You can also assign owners manually from the People list under Management."
    },
    {
        q: "How do alerts work?",
        a: "Alerts are auto-generated when sources become stale, go offline, have broken references, or have changed queries. Each alert can be assigned to an owner, acknowledged, or resolved with a reason."
    },
    {
        q: "What is the TMDL Checker?",
        a: "Under Tools, the TMDL Checker scans all reports against best-practice rules: no local file paths, required owner metadata, proper date types, avoiding DirectQuery mode, excessive columns, duplicate sources, unused measures, and visual density. Findings are shown by severity with filtering by report owner."
    },
    {
        q: "What is the Scripts page?",
        a: "The Scripts page discovers and tracks all Python ETL scripts on the desktop machine and shared drives. It detects which SQL tables each script writes to and reads from, links scripts to data sources in the lineage, and shows modification dates. Scripts are categorized as Data to SQL, Data to Excel, or Other. Use Full Scan to discover new scripts or Re-parse to re-analyze existing ones."
    },
    {
        q: "What is the Scheduled Tasks page?",
        a: "Under Data, Scheduled Tasks shows governed Windows refresh jobs linked to scripts. Failed actionable jobs and their schedules are highlighted. Use Show other Windows tasks only when you need to inspect unrelated Task Scheduler entries."
    },
    {
        q: "What is the Power Automate page?",
        a: "Under Data, Power Automate lets you manually register Power Automate flows that feed data into your pipeline. Flows can be linked to output sources, and their last run time is derived from the linked source's probe data."
    },
    {
        q: "What is the Lineage view?",
        a: "Lineage shows the full dependency chain as a horizontal DAG: Visuals, Tables, Sources, every upstream source level, Scripts, and Tasks. Select a report to see exactly which sources feed each visual, how materialized views depend on one another, and which scripts refresh the data."
    },
    {
        q: "How do upstream systems work?",
        a: "Upstream systems (like GSCM or ASAP) represent the parent data platforms that feed your sources. Linking sources to upstream systems enables schedule discrepancy detection. PostgreSQL materialized view dependencies are detected automatically via pg_depend."
    },
    {
        q: "What does the Schedule Discrepancies check do?",
        a: "It validates that data flows in the right order: Upstream System refreshes before Source, which refreshes before Report. If the timing is wrong (e.g., a report refreshes before its source), it flags a warning or critical issue. pg_cron schedules are also scanned for mismatches."
    },
    {
        q: "How does multi-machine support work?",
        a: "The platform can scan scripts and scheduled tasks from multiple machines. Each script and task is tagged with its hostname. The Sources page filter buttons let you focus on specific machines."
    },
    {
        q: "What is the Archive feature?",
        a: "Reports, sources, scripts, and scheduled tasks can be archived to remove them from active views without deleting data. Archived items are hidden by default but can be shown with the Show Archived toggle."
    },
    {
        q: "Can I add sources and reports manually?",
        a: "Yes. Under Management, the Create page has Assets and People tabs. Assets lets you add reports, sources, or upstream systems manually. People lets you manage team members who can be assigned as owners."
    },
    {
        q: "What is the Full Export?",
        a: "Under Tools, Full Export generates a structured text dump of all platform data (sources, reports, scripts, tasks, lineage) with section checkboxes. Includes a diagnostic report and optional Python source code export."
    },
    {
        q: "How do GSCM flows work?",
        a: "GSCM is a separate portal from ASAP. You configure a report inside GSCM and save it as a bookmark on its home screen; Flows > Catalog > GSCM > Scan bookmarks then catalogues every bookmark you saved. A GSCM flow points at one bookmark and has no filters or period to set here - the bookmark already holds them. Each run opens the bookmark, exports GSCM's Excel workbook, normalizes it to CSV, and hands it to SQL like any other flow."
    },
    {
        q: "What is the AI Assistant?",
        a: "The AI chat (bottom-right button) lets you ask questions about your data ecosystem - risks, source health, specific reports, or general governance questions. It uses live data from the database to give contextual answers."
    },
    {
        q: "What database does the platform use?",
        a: "A single SQLite file (governance.db). No external database server needed. A daily backup runs automatically at 6 AM. This file is the only thing you need to back up to preserve all state."
    },
    {
        q: "How do I set up the platform?",
        a: "Run setup.ps1 as Administrator. It installs portable Python, dependencies, and registers the app as a Windows service. The app starts automatically and is accessible at http://localhost:8000. Multiple users on the network can connect via the machine's IP address."
    },
];

async function renderFaq() {
    const items = FAQ_ITEMS.map((f, i) => `
        <div class="faq-item">
            <div class="faq-question" data-faq-idx="${i}" role="button" tabindex="0" aria-expanded="false" aria-controls="faq-ans-${i}">
                <span class="faq-chevron">&#9654;</span>
                <span>${f.q}</span>
            </div>
            <div class="faq-answer" id="faq-ans-${i}" role="region">${f.a}</div>
        </div>
    `).join("");

    return `
        <div class="page-header">
            <h1>FAQ</h1>
            <span class="subtitle">${FAQ_ITEMS.length} questions</span>
        </div>
        <div class="faq-list">
            ${items}
        </div>
    `;
}

function bindFaqPage() {
    document.querySelectorAll(".faq-question[data-faq-idx]").forEach(q => {
        const toggle = () => {
            const idx = q.dataset.faqIdx;
            const ans = document.getElementById("faq-ans-" + idx);
            if (!ans) return;
            const open = q.classList.contains("expanded");
            if (open) {
                q.classList.remove("expanded");
                ans.classList.remove("visible");
                q.setAttribute("aria-expanded", "false");
            } else {
                q.classList.add("expanded");
                ans.classList.add("visible");
                q.setAttribute("aria-expanded", "true");
            }
        };
        q.addEventListener("click", toggle);
        q.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
        });
    });
}

async function renderPremiumViewers() {
    const data = await api("/api/usage/premium-viewers");
    const viewers = data.viewers || [];
    const emails = viewers.map(v => v.email).join("\n");
    return `
        <div class="page-header">
            <h1>Premium Viewers</h1>
            <span class="subtitle">${viewers.length} premium viewer${viewers.length === 1 ? "" : "s"} - ${data.weight || 5}x impact weight</span>
            <button class="btn-outline" id="btn-premium-sync-usage" style="font-size:0.78rem">Sync Usage</button>
        </div>
        <div class="settings-panel" style="max-width:760px">
            <label for="premium-viewers-input" style="display:block;font-size:0.78rem;font-weight:600;color:var(--text-dim);margin-bottom:0.35rem">Viewer emails</label>
            <textarea id="premium-viewers-input" rows="12" spellcheck="false" style="width:100%;font-family:monospace;font-size:0.82rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:0.75rem">${esc(emails)}</textarea>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-top:0.75rem;flex-wrap:wrap">
                <span style="color:var(--text-dim);font-size:0.78rem">One email per line. Commas and semicolons also work.</span>
                <button id="btn-save-premium-viewers">Save Premium Viewers</button>
            </div>
        </div>
    `;
}

function bindPremiumViewersPage() {
    const saveBtn = document.getElementById("btn-save-premium-viewers");
    const input = document.getElementById("premium-viewers-input");
    if (saveBtn && input) {
        saveBtn.addEventListener("click", async () => {
            const emails = input.value.split(/[\n,;]+/).map(v => v.trim()).filter(Boolean);
            saveBtn.disabled = true;
            saveBtn.textContent = "Saving...";
            try {
                const result = await apiPut("/api/usage/premium-viewers", { emails });
                toast(`Saved ${result.count} premium viewer${result.count === 1 ? "" : "s"}`);
                await navigate("premiumviewers");
            } catch (err) {
                toast("Save failed: " + err.message);
                saveBtn.disabled = false;
                saveBtn.textContent = "Save Premium Viewers";
            }
        });
    }

    const syncBtn = document.getElementById("btn-premium-sync-usage");
    if (syncBtn) {
        syncBtn.addEventListener("click", async () => {
            syncBtn.disabled = true;
            syncBtn.textContent = "Syncing...";
            try {
                const result = await apiPost("/api/usage/sync");
                toast(result.message || "Usage sync complete");
                await navigate("premiumviewers");
            } catch (err) {
                toast("Usage sync failed: " + err.message);
                syncBtn.disabled = false;
                syncBtn.textContent = "Sync Usage";
            }
        });
    }
}

async function renderRefreshSchedule() {
    const schedule = await api("/api/system/refresh-schedule");
    return `
        <div class="page-header">
            <h1>Refresh Schedule</h1>
            <span class="subtitle">Overall refresh at ${esc(schedule.refresh_time || "-")}</span>
        </div>
        <div class="section refresh-schedule-section">
            <div class="refresh-schedule-grid">
                <label class="refresh-schedule-field">
                    <span>Daily refresh time</span>
                    <input type="time" id="overall-refresh-time" value="${esc(schedule.refresh_time || "08:15")}" step="60">
                </label>
                <div class="refresh-schedule-status">
                    <span>Next run</span>
                    <strong>${schedule.next_run_at ? timeAgo(schedule.next_run_at) : "pending scheduler"}</strong>
                    ${schedule.next_run_at ? `<small title="${esc(formatDate(schedule.next_run_at))}">${esc(formatDate(schedule.next_run_at))}</small>` : ""}
                </div>
            </div>
            <div class="refresh-schedule-actions">
                <button id="btn-save-refresh-schedule">Save schedule</button>
                <button class="btn-outline" id="btn-run-overall-refresh">Run once now</button>
            </div>
        </div>
    `;
}

function bindRefreshSchedulePage() {
    const input = document.getElementById("overall-refresh-time");
    const saveBtn = document.getElementById("btn-save-refresh-schedule");
    const runBtn = document.getElementById("btn-run-overall-refresh");

    if (saveBtn && input) {
        saveBtn.addEventListener("click", async () => {
            saveBtn.disabled = true;
            saveBtn.textContent = "Saving...";
            try {
                const updated = await apiPut("/api/system/refresh-schedule", { refresh_time: input.value });
                toast(updated.reschedule_error
                    ? `Refresh time saved for ${updated.refresh_time}; scheduler reschedule needs restart`
                    : `Refresh schedule saved for ${updated.refresh_time}`);
                await navigate("refreshschedule");
            } catch (err) {
                toast("Schedule save failed: " + err.message);
                saveBtn.disabled = false;
                saveBtn.textContent = "Save schedule";
            }
        });
    }

    if (runBtn) {
        runBtn.addEventListener("click", async () => {
            runBtn.disabled = true;
            runBtn.textContent = "Queueing...";
            try {
                await apiPost("/api/system/refresh-now");
                toast("Overall refresh queued; PBI sync launches first, then scan and probe run");
                await navigate("scanner");
            } catch (err) {
                toast("Refresh launch failed: " + err.message);
                runBtn.disabled = false;
                runBtn.textContent = "Run once now";
            }
        });
    }
}


// ── Import Data ──

async function renderDataImport() {
    let status = { configured: false, missing: [], dependencies_installed: true, host: "", database: "", schema: "", script_dir: "" };
    try {
        status = await api("/api/data-import/status");
    } catch (_) { /* endpoint unreachable; render unconfigured state */ }

    const target = status.host && status.database
        ? `${esc(status.database)} on ${esc(status.host)} &middot; schema <b>${esc(status.schema)}</b>`
        : "connection not configured";

    let warning = "";
    if (!status.dependencies_installed) {
        warning = `<div style="border:1px solid var(--yellow);border-radius:6px;padding:0.6rem 0.9rem;margin-bottom:1rem;font-size:0.8rem;color:var(--text-secondary)">
            Python dependencies for imports (pandas, sqlalchemy) are not installed on this machine. Re-run setup.ps1, then restart the service.</div>`;
    } else if (status.missing.length) {
        warning = `<div style="border:1px solid var(--yellow);border-radius:6px;padding:0.6rem 0.9rem;margin-bottom:1rem;font-size:0.8rem;color:var(--text-secondary)">
            Imports are disabled until these environment variables are set: <b>${status.missing.map(esc).join(", ")}</b>. Restart the service after setting them.</div>`;
    }

    return `
    <div class="page-header">
        <h1>Import Data</h1>
        <span class="subtitle">Load a CSV or Excel file into PostgreSQL (${target})</span>
    </div>
    ${warning}
    <fieldset style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;margin-bottom:1rem">
        <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">1 &middot; File</legend>
        <input type="file" id="di-file" accept=".csv,.xlsx,.xls" style="display:none">
        <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap">
            <button class="btn-export" id="di-pick" style="float:none" ${status.configured ? "" : "disabled"}>Choose file...</button>
            <span id="di-file-info" style="font-size:0.8rem;color:var(--text-dim)">CSV, XLSX or XLS. Column names are normalized to lowercase_with_underscores.</span>
        </div>
        <div id="di-preview" style="margin-top:0.75rem"></div>
    </fieldset>
    <fieldset id="di-target" style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;margin-bottom:1rem;display:none">
        <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">2 &middot; Target table</legend>
        <div style="display:flex;gap:1.25rem;flex-wrap:wrap;margin-bottom:0.6rem">
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-targettype" value="existing" checked> Existing table</label>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-targettype" value="new"> New table</label>
        </div>
        <div id="di-existing">
            <div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap">
                <select id="di-table" style="font-size:0.82rem;padding:0.3rem 0.5rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:240px"></select>
            </div>
        </div>
        <div id="di-new" style="display:none">
            <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap">
                <input type="text" id="di-newname" placeholder="new_table_name" style="font-size:0.82rem;padding:0.3rem 0.5rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:240px">
                <button class="btn-export" id="di-create-table" type="button" style="float:none">Create table now</button>
                <span id="di-create-result" style="font-size:0.8rem;color:var(--text-dim)"></span>
            </div>
            <div style="font-size:0.75rem;color:var(--text-dim);margin-top:0.35rem">lowercase letters, numbers and underscores; column types are inferred from this upload. No scheduled script is created in this step.</div>
        </div>
    </fieldset>
    <fieldset id="di-schedule" style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;margin-bottom:1rem;display:none">
        <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">3 &middot; Recurring import options</legend>
        <div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin-bottom:0.75rem">
            <span style="font-size:0.78rem;font-weight:600">Script action</span>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-mode" value="append" checked> Append rows</label>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-mode" value="replace"> Truncate and replace</label>
        </div>
        <div style="font-size:0.78rem;font-weight:600;margin-bottom:0.35rem">Materialized views included after SQL write</div>
        <div id="di-mv-picker" style="position:relative;max-width:520px;margin-bottom:0.7rem">
            <button class="btn-outline" id="di-mv-toggle" type="button" aria-expanded="false" style="width:100%;float:none;display:flex;justify-content:space-between;align-items:center;font-size:0.8rem">
                <span id="di-mv-toggle-text">Select materialized views</span><span aria-hidden="true">v</span>
            </button>
            <div id="di-mv-menu" style="display:none;position:absolute;z-index:30;top:calc(100% + 0.25rem);left:0;right:0;background:var(--surface);border:1px solid var(--border);border-radius:6px;box-shadow:0 8px 20px rgba(15,23,42,0.16);padding:0.5rem">
                <input type="text" id="di-mv-filter" placeholder="Filter views" style="width:100%;box-sizing:border-box;font-size:0.8rem;padding:0.35rem 0.5rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;margin-bottom:0.45rem">
                <div id="di-mv-list" style="display:flex;flex-direction:column;gap:0.15rem;max-height:280px;overflow:auto"></div>
            </div>
        </div>
        <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap">
            <button class="btn-outline" id="di-refresh-mv" style="font-size:0.78rem">Refresh selected now</button>
            <span id="di-mv-status" style="font-size:0.8rem;color:var(--text-dim)"></span>
        </div>
    </fieldset>
    <fieldset id="di-script" style="border:1px solid var(--border);border-radius:6px;padding:0.75rem 1rem;margin-bottom:1rem;display:none">
        <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">4 &middot; Python scheduling script</legend>
        <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;margin-bottom:0.55rem">
            <span style="font-size:0.78rem;font-weight:600">Script folder</span>
            <input type="text" id="di-script-dir" aria-label="Script folder" value="${esc(status.script_dir || "generated_imports")}" style="font-size:0.82rem;padding:0.3rem 0.5rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:340px;max-width:100%">
            <button class="btn-outline" id="di-save-script-dir" type="button" style="font-size:0.78rem">Save folder</button>
            <span id="di-script-dir-status" style="font-size:0.75rem;color:var(--text-dim)"></span>
        </div>
        <div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin-bottom:0.55rem">
            <span style="font-size:0.78rem;font-weight:600">Prefect schedule</span>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-schedule-type" value="manual" checked> One-time/manual</label>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-schedule-type" value="daily"> Daily</label>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-schedule-type" value="weekly"> Weekly</label>
            <label style="font-size:0.82rem;cursor:pointer"><input type="radio" name="di-schedule-type" value="cron"> Custom cron</label>
        </div>
        <div id="di-prefect-schedule-fields" style="display:none;gap:0.75rem;align-items:center;flex-wrap:wrap;margin-bottom:0.45rem">
            <label id="di-schedule-time-wrap" style="font-size:0.78rem">Time
                <input type="time" id="di-schedule-time" value="08:00" step="60" style="font-size:0.82rem;padding:0.25rem 0.45rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;margin-left:0.35rem">
            </label>
            <label id="di-schedule-day-wrap" style="font-size:0.78rem;display:none">Day
                <select id="di-schedule-day" style="font-size:0.82rem;padding:0.25rem 0.45rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;margin-left:0.35rem">
                    <option value="1">Monday</option>
                    <option value="2">Tuesday</option>
                    <option value="3">Wednesday</option>
                    <option value="4">Thursday</option>
                    <option value="5">Friday</option>
                    <option value="6">Saturday</option>
                    <option value="0">Sunday</option>
                </select>
            </label>
            <label id="di-schedule-cron-wrap" style="font-size:0.78rem;display:none">Cron
                <input type="text" id="di-schedule-cron" value="0 8 * * 1" placeholder="0 8 * * 1" style="font-size:0.82rem;padding:0.25rem 0.45rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:190px;margin-left:0.35rem">
            </label>
            <label id="di-schedule-timezone-wrap" style="font-size:0.78rem">Timezone
                <input type="text" id="di-schedule-timezone" value="UTC" placeholder="UTC" style="font-size:0.82rem;padding:0.25rem 0.45rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:180px;margin-left:0.35rem">
            </label>
        </div>
        <div id="di-schedule-summary" style="font-size:0.75rem;color:var(--text-dim);margin-bottom:0.65rem">Manual / run once</div>
        <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap">
            <input type="text" id="di-script-name" placeholder="optional_script_name" style="font-size:0.82rem;padding:0.3rem 0.5rem;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:5px;min-width:240px">
            <button class="btn-export" id="di-create-script" style="float:none">Create append/replace script</button>
        </div>
        <div id="di-script-result" style="margin-top:0.65rem;font-size:0.78rem;color:var(--text-secondary)"></div>
    </fieldset>
    `;
}


// ── Configurable website report flows ──

const _FLOW_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

function _flowStatusBadge(status) {
    const key = String(status || "draft").toLowerCase();
    const cls = ["succeeded"].includes(key) ? "badge-green"
        : ["failed"].includes(key) ? "badge-red"
        : ["queued", "claimed", "running"].includes(key) ? "badge-yellow"
        : "badge-muted";
    return `<span class="badge ${cls}">${esc(key.replaceAll("_", " "))}</span>`;
}

function _flowEmptyState(catalog) {
    const hasSites = catalog.sites.some(site => site.enabled);
    const hasReports = catalog.reports.some(report => report.enabled);
    const action = !hasSites
        ? '<button class="btn-primary" id="flow-add-site-empty">Add website</button>'
        : !hasReports
            ? '<button class="btn-primary" id="flow-scan-empty">Scan the catalog</button>'
            : '<button class="btn-primary" id="flow-create-empty">Create flow</button>';
    const copy = !hasSites
        ? "Start with the website whose authenticated browser will download reports."
        : !hasReports
            ? "The website is ready. Scan it to discover its reports - ASAP menus, or the bookmarks saved on GSCM's home screen."
            : "The catalog is ready. Create a flow and choose its filters, files, and schedule.";
    return `
        <div class="flow-empty">
            <h2>${!hasSites ? "Add the first website" : !hasReports ? "Discover portal reports" : "Build the first download flow"}</h2>
            <p>${copy} Configuration stays in this machine's SQLite database.</p>
            <div class="flow-empty-actions">${action}</div>
        </div>`;
}

function _flowListHtml(flows, workers, catalog, runs = []) {
    if (!flows.length) return _flowEmptyState(catalog);
    const online = workers.filter(worker => worker.status !== "offline").length;
    return `
        <div class="flow-status-strip">
            <span><strong>${flows.length}</strong> configured flow${flows.length === 1 ? "" : "s"}</span>
            <span><strong>${online}</strong> online worker${online === 1 ? "" : "s"}</span>
            <span>Existing files are never deleted or overwritten</span>
        </div>
        <div class="flow-table-wrap">
            <table class="flow-table">
                <thead><tr><th>Flow</th><th>Active</th><th>Website / report</th><th>Download</th><th>Schedule</th><th>Last run</th><th></th></tr></thead>
                <tbody>${flows.map(flow => { const activeRun = runs.find(run => run.flow_id === flow.id && ["queued", "claimed", "running"].includes(run.status)); return `
                    <tr>
                        <td><strong>${esc(flow.name)}</strong>${flow.owner_name ? `<small>Owner: ${esc(flow.owner_name)}${flow.owner_email ? "" : " · no email mapped"}</small>` : "<small>No owner · failure alerts disabled</small>"}</td>
                        <td><label class="flow-switch"><input class="flow-enabled-switch" type="checkbox" data-id="${flow.id}" ${flow.enabled ? "checked" : ""} ${flow.schedule_type === "manual" ? "disabled" : ""}><span aria-hidden="true"></span><strong>${flow.enabled ? "Active" : "Inactive"}</strong></label>${flow.schedule_type === "manual" ? '<small>Choose a schedule to activate</small>' : ""}</td>
                        <td>${esc(flow.site_name)}<small>${esc(flow.report_name)}</small></td>
                        <td>${esc(flow.download_mode === "one_per_period" || flow.download_mode === "one_per_week" ? `One ${String(flow.file_format || "csv").toUpperCase()} every ${flow.window_weeks || 1} week(s)` : `${flow.export_views?.length || 1} ${String(flow.file_format || "csv").toUpperCase()} export(s)`)}<small>${flow.period_strategy === "none" ? "No period prompt" : flow.period_strategy === "latest" ? "Start to latest available" : flow.period_strategy === "rolling" ? "Rolling window" : "Fixed start + end"} · ${flow.browser_mode === "headed" ? "Headed browser" : "Headless browser"}</small></td>
                        <td>${esc(flow.schedule_type)}${flow.schedule_type === "monthly" ? `<small>Day ${esc(flow.schedule_day)}</small>` : ""}${flow.next_run_at ? `<small>Next ${esc(formatDate(flow.next_run_at))}</small>` : ""}</td>
                        <td>${_flowStatusBadge(flow.last_status)}${flow.last_run_at ? `<small>${esc(timeAgo(flow.last_run_at))}</small>` : '<small>Not run yet</small>'}</td>
                        <td class="flow-row-actions">
                            <button class="btn-sm flow-run" data-id="${flow.id}" ${activeRun ? "disabled" : ""}>${activeRun ? "Running" : "Run"}</button>
                            ${activeRun ? `<button class="btn-sm btn-outline btn-danger-outline flow-stop" data-id="${flow.id}">Stop</button>` : ""}
                            <button class="btn-sm flow-edit" data-id="${flow.id}">Edit</button>
                        </td>
                    </tr>`; }).join("")}</tbody>
            </table>
        </div>`;
}

/** The folder a report sits in, and its own name, from the discovery path.
 *  GSCM catalogues hundreds of bookmarks under Public > MDM > Channel Site >
 *  ..., so a flat list of full paths is unreadable. */
function _flowReportGroup(report) {
    const path = report.automation?.category_path || [];
    if (path.length > 1) {
        return { folder: path.slice(0, -1).join(" › "), leaf: path[path.length - 1] };
    }
    return { folder: "", leaf: report.name };
}

function _flowReportOptions(catalog, siteId, selected) {
    const reports = catalog.reports.filter(report => String(report.site_id) === String(siteId) && report.enabled && !report.stale);
    if (!reports.length) return '<option value="">Add or discover a report for this website first</option>';
    const groups = new Map();
    for (const report of reports) {
        const { folder, leaf } = _flowReportGroup(report);
        if (!groups.has(folder)) groups.set(folder, []);
        groups.get(folder).push({ report, leaf });
    }
    const option = ({ report, leaf }) => `<option value="${report.id}" ${String(report.id) === String(selected) ? "selected" : ""}>${esc(leaf)}</option>`;
    return [...groups.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([folder, items]) => {
            items.sort((a, b) => a.leaf.localeCompare(b.leaf));
            return folder
                ? `<optgroup label="${esc(folder)}">${items.map(option).join("")}</optgroup>`
                : items.map(option).join("");
        }).join("");
}

function _flowFilterControl(definition, value) {
    const id = `flow-filter-${definition.filter_key}`;
    const required = definition.required ? "required" : "";
    if (definition.control_type === "multi_select") {
        const selected = new Set(Array.isArray(value) ? value.map(String) : []);
        return `<select id="${id}" data-flow-filter="${esc(definition.filter_key)}" multiple ${required}>${definition.options.map(option => `<option ${selected.has(String(option)) ? "selected" : ""}>${esc(option)}</option>`).join("")}</select>`;
    }
    if (definition.control_type === "select") {
        return `<select id="${id}" data-flow-filter="${esc(definition.filter_key)}" ${required}><option value="">Choose...</option>${definition.options.map(option => `<option ${String(value ?? "") === String(option) ? "selected" : ""}>${esc(option)}</option>`).join("")}</select>`;
    }
    if (definition.control_type === "week") {
        return `<input id="${id}" type="week" data-flow-filter="${esc(definition.filter_key)}" value="${esc(value || "")}" ${required}>`;
    }
    return `<input id="${id}" type="text" data-flow-filter="${esc(definition.filter_key)}" value="${esc(value || "")}" ${required}>`;
}

function _flowIsoWeekMonday(value) {
    const match = /^(\d{4})-W(\d{2})$/.exec(String(value));
    if (!match) return null;
    const year = Number(match[1]);
    const week = Number(match[2]);
    const january4 = new Date(Date.UTC(year, 0, 4));
    const january4Day = january4.getUTCDay() || 7;
    const monday = new Date(january4);
    monday.setUTCDate(january4.getUTCDate() - january4Day + 1 + ((week - 1) * 7));
    return monday;
}

function _flowIsoWeekValue(date) {
    const thursday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    const day = thursday.getUTCDay() || 7;
    thursday.setUTCDate(thursday.getUTCDate() + 4 - day);
    const year = thursday.getUTCFullYear();
    const yearStart = new Date(Date.UTC(year, 0, 1));
    const week = Math.ceil((((thursday - yearStart) / 86400000) + 1) / 7);
    return `${year}-W${String(week).padStart(2, "0")}`;
}

function _flowDiscoveredWeeks(report, selected) {
    const definition = (report?.filters || []).find(filter => filter.control_type === "week" && filter.enabled && !filter.stale);
    const discovered = [...new Set((definition?.options || []).map(option => {
        const raw = String(option).trim();
        return /^\d{6}$/.test(raw) ? `${raw.slice(0, 4)}-W${raw.slice(4)}` : raw;
    }).filter(value => /^\d{4}-W\d{2}$/.test(value)))].sort();
    const weeks = [];
    if (discovered.length) {
        const first = _flowIsoWeekMonday(discovered[0]);
        const last = _flowIsoWeekMonday(discovered[discovered.length - 1]);
        for (const cursor = first; cursor && last && cursor <= last; cursor.setUTCDate(cursor.getUTCDate() + 7)) {
            weeks.push(_flowIsoWeekValue(cursor));
        }
    }
    if (selected && !weeks.includes(selected)) weeks.push(selected);
    weeks.sort();
    return weeks.map(value => {
        const [year, week] = value.split("-W");
        return `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>Week ${Number(week)}, ${esc(year)}</option>`;
    }).join("");
}

function _flowExportViewLabels(report) {
    const items = report?.automation?.export_views || [];
    const labels = items.map(item => String(typeof item === "string" ? item : item?.label || "").trim()).filter(Boolean);
    const fallback = String(report?.automation?.report_tab || "").trim();
    if (!labels.length && fallback) labels.push(fallback);
    return [...new Set(labels)];
}

function _flowExportViewsHtml(report, selected = null) {
    const labels = _flowExportViewLabels(report);
    const chosen = new Set(selected?.length ? selected.map(String) : labels);
    return labels.length
        ? labels.map(label => `<label class="flow-check"><input type="checkbox" data-flow-export-view value="${esc(label)}" ${chosen.has(label) ? "checked" : ""}><span>${esc(label)}</span></label>`).join("")
        : '<p class="flow-inline-empty">No export view was discovered. Refresh this report before creating a flow.</p>';
}

function _flowDownloadLinkLabels(report) {
    return [...new Set((report?.automation?.download_links || [])
        .map(link => String(link?.label || "").trim()).filter(Boolean))];
}

function _flowDownloadLinksHtml(report, selected = null) {
    const labels = _flowDownloadLinkLabels(report);
    const chosen = new Set(selected?.length ? selected.map(String) : labels);
    return labels.length
        ? labels.map(label => `<label class="flow-check"><input type="checkbox" data-flow-download-link value="${esc(label)}" ${chosen.has(label) ? "checked" : ""}><span>${esc(label)}</span></label>`).join("")
        : '<p class="flow-inline-empty">No download links were discovered. Scan this report first.</p>';
}

function _flowSyncExportSections(report, selected = {}) {
    const dashboard = _flowDownloadLinkLabels(report).length > 0 && _flowExportViewLabels(report).length === 0;
    const exportViews = $("#flow-export-views");
    const downloadLinks = $("#flow-download-links");
    if (exportViews) exportViews.innerHTML = _flowExportViewsHtml(report, selected.export_views);
    if (downloadLinks) downloadLinks.innerHTML = _flowDownloadLinksHtml(report, selected.download_links);
    const exportSection = $("#flow-export-views-section");
    const downloadSection = $("#flow-download-links-section");
    if (exportSection) exportSection.hidden = dashboard;
    if (downloadSection) downloadSection.hidden = !dashboard;
}

function _flowHasWeekFilter(report) {
    return Boolean((report?.filters || []).find(filter => filter.control_type === "week" && filter.enabled && !filter.stale));
}

function _flowOwnerOptions(people, selectedId) {
    return ['<option value="">No owner - failure alerts disabled</option>']
        .concat(people.map(person => `<option value="${person.id}" ${person.id === selectedId ? "selected" : ""}>${esc(person.name)} (${esc(person.role)})</option>`))
        .join("");
}

function _flowOwnerHelp(owner) {
    if (!owner) return "Choose a person from Management > Create > People. Without an owner, nobody is emailed when this flow fails.";
    if (!owner.email) return `${esc(owner.name)} has no email mapped in People. Add one or no failure alert can be delivered.`;
    return `Failure alerts are sent to ${esc(owner.email)} through Outlook on the app host.`;
}

function _flowOwnerSummary(owner) {
    if (!owner) return "Disabled · no owner";
    return owner.email ? `${esc(owner.name)} · ${esc(owner.email)}` : `${esc(owner.name)} · no email mapped`;
}

const FLOW_GSCM_ADAPTER = "gscm_portal";

/** GSCM bookmarks carry their own filters, period, and dimensions inside GSCM,
 *  so a GSCM flow is "open the bookmark, export, load" with nothing to
 *  configure here. ASAP is the opposite: every prompt is driven from Metronome. */
function _flowSiteIsGscm(catalog, siteId) {
    const site = catalog.sites.find(item => String(item.id) === String(siteId));
    return site?.adapter === FLOW_GSCM_ADAPTER;
}

function _flowBuilderHtml(catalog, existing = null) {
    const sites = catalog.sites.filter(site => site.enabled);
    const siteId = existing?.site_id || sites[0]?.id || "";
    const reports = catalog.reports.filter(report => String(report.site_id) === String(siteId) && report.enabled && !report.stale);
    const reportId = existing?.report_id || reports[0]?.id || "";
    const report = catalog.reports.find(item => String(item.id) === String(reportId));
    const selections = { ...(existing?.selections || {}) };
    for (const definition of (report?.filters || [])) {
        if (definition.control_type === "week") delete selections[definition.filter_key];
    }
    const scheduleDays = new Set(existing?.schedule_days || []);
    const downloadEstimate = window._flowsState?.estimates?.flow_download;
    const sqlCatalog = window._flowsState?.sqlCatalog || { configured: false, targets: [], scan: {} };
    const sqlDatabases = [...new Set(sqlCatalog.targets.map(item => item.database))];
    const selectedDatabase = existing?.sql_database || sqlDatabases[0] || "";
    const sqlSchemas = [...new Set(sqlCatalog.targets.filter(item => item.database === selectedDatabase).map(item => item.schema))];
    const selectedSchema = existing?.sql_schema || sqlSchemas[0] || "";
    const sqlTables = sqlCatalog.targets.filter(item => item.database === selectedDatabase && item.schema === selectedSchema).map(item => item.table);
    const isGscm = _flowSiteIsGscm(catalog, siteId);
    const hasWeekFilter = !isGscm && _flowHasWeekFilter(report);
    const periodStrategy = hasWeekFilter ? (existing?.period_strategy || "latest") : "none";
    // GSCM exports through its Nexacro toolbar, which only emits .xlsx.
    const fileFormat = isGscm ? "xlsx" : (existing?.file_format || "xlsx");
    const people = window._flowsState?.people || [];
    const owner = people.find(person => person.id === existing?.owner_person_id);
    const exportViewCount = _flowExportViewLabels(report).length;
    const downloadLinkCount = _flowDownloadLinkLabels(report).length;
    const isDashboard = downloadLinkCount > 0 && exportViewCount === 0;
    const defaultFilename = isGscm
        ? `{flow}_{date}.${fileFormat}`
        : (exportViewCount > 1 || downloadLinkCount > 1 || !hasWeekFilter
            ? `{flow}_{export}.${fileFormat}`
            : `{flow}_{start_period}_{end_period}.${fileFormat}`);
    return `
        <div class="flow-builder-shell">
            <div class="flow-builder-main">
                <form id="flow-builder-form" data-id="${existing?.id || ""}" data-site-id="${siteId}">
                    ${existing?.id ? "" : `<div class="flow-form-section" id="flow-replicate-section">
                        <div class="flow-section-head"><h2>Start from an existing flow</h2><p>Copy every setting from a flow you already built, then change only what differs. Nothing is copied until you choose one.</p></div>
                        <div class="flow-form-grid">
                            <label class="flow-span-2"><span>Replicate flow</span><div class="flow-file-control"><select id="flow-replicate-source"><option value="">Start from scratch</option>${(window._flowsState?.flows || []).map(item => `<option value="${item.id}" ${String(item.id) === String(existing?._replicated_from || "") ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select><button type="button" class="btn-secondary" id="flow-replicate-apply">Copy settings</button></div><small id="flow-replicate-status">The copy keeps the source flow untouched. Give the new flow its own name and SQL table.</small></label>
                        </div>
                    </div>`}
                    <div class="flow-form-section">
                        <div class="flow-section-head"><h2>Source and report</h2><p>${isGscm ? "Choose a GSCM bookmark. It already holds the filters you saved in GSCM." : "Choose an enabled catalog report and its configured filters."}</p></div>
                        <div class="flow-form-grid">
                            <label><span>Flow name</span><input id="flow-name" required maxlength="200" value="${esc(existing?.name || "")}" placeholder="Weekly report download"></label>
                            <label><span>Website</span><select id="flow-site" required>${sites.length ? sites.map(site => `<option value="${site.id}" ${String(site.id) === String(siteId) ? "selected" : ""}>${esc(site.name)}</option>`).join("") : '<option value="">Add a website first</option>'}</select></label>
                            <label class="flow-span-2"><span>${isGscm ? "Bookmark" : "Report"}</span><div class="flow-file-control"><select id="flow-report" required>${_flowReportOptions(catalog, siteId, reportId)}</select><button type="button" class="btn-secondary" id="flow-scan-report-now">${isGscm ? "Rescan bookmark" : "Scan report"}</button></div><small id="flow-report-scan-status">${isGscm ? "Rescan to confirm this bookmark is still on the GSCM home screen." : "Scan this report to discover its filters and options without running a full catalog scan."}</small></label>
                        </div>
                    </div>
                    <div class="flow-form-section" id="flow-report-filters" ${isGscm ? "hidden" : ""}>
                        <div class="flow-section-head"><h2>Report filters</h2><p>Every value below was discovered from the website. Rescan the catalog when the portal changes.</p></div>
                        <div class="flow-form-grid">${report && report.filters.filter(filter => filter.enabled && filter.control_type !== "week").length ? report.filters.filter(filter => filter.enabled && filter.control_type !== "week").map(definition => `
                            <label><span>${esc(definition.label)}${definition.required ? " *" : ""}</span>${_flowFilterControl(definition, selections[definition.filter_key])}</label>`).join("") : '<p class="flow-inline-empty">This report has no configured filters.</p>'}</div>
                    </div>
                    <div class="flow-form-section" id="flow-export-views-section" ${isDashboard || isGscm ? "hidden" : ""}>
                        <div class="flow-section-head"><h2>Export views</h2><p>One run downloads every checked view, validates the full bundle, and sends all files to one SQL transaction.</p></div>
                        <div class="flow-form-grid" id="flow-export-views">${_flowExportViewsHtml(report, existing?.export_views)}</div>
                    </div>
                    <div class="flow-form-section" id="flow-download-links-section" ${isDashboard && !isGscm ? "" : "hidden"}>
                        <div class="flow-section-head"><h2>Download links</h2><p>This report is an embedded HTML dashboard with no Export Wizard. One run clicks every checked download link and saves each file.</p></div>
                        <div class="flow-form-grid" id="flow-download-links">${_flowDownloadLinksHtml(report, existing?.download_links)}</div>
                    </div>
                    <div class="flow-form-section">
                        <div class="flow-section-head"><h2>Download behavior</h2><p>${isGscm ? "One run opens the bookmark, exports GSCM's Excel workbook, and normalizes it for SQL." : "Download through the latest ASAP week, use a fixed range, or advance through rolling windows."}</p></div>
                        <div class="flow-form-grid">
                            <label ${isGscm ? "hidden" : ""}><span>Period</span><select id="flow-period-strategy"><option value="none" ${periodStrategy === "none" ? "selected" : ""}>No period selection</option><option value="latest" ${periodStrategy === "latest" ? "selected" : ""}>Start to latest available</option><option value="fixed" ${periodStrategy === "fixed" ? "selected" : ""}>Fixed start + end</option><option value="rolling" ${periodStrategy === "rolling" ? "selected" : ""}>Rolling X-week window</option></select><small id="flow-period-help">${hasWeekFilter ? "Latest available comes from ASAP discovery." : "This report has no Sell-out Week prompt."}</small></label>
                            <label ${isGscm ? "hidden" : ""}><span>File grouping</span><select id="flow-download-mode"><option value="single" ${!["one_per_period", "one_per_week"].includes(existing?.download_mode) ? "selected" : ""}>One file for the full range</option><option value="one_per_period" ${["one_per_period", "one_per_week"].includes(existing?.download_mode) ? "selected" : ""}>One file every X weeks</option></select></label>
                            <label><span>Download type</span><select id="flow-file-format" ${isGscm ? "disabled" : ""}><option value="xlsx" ${fileFormat === "xlsx" ? "selected" : ""}>Excel workbook (.xlsx)</option>${isGscm ? "" : `<option value="csv" ${fileFormat === "csv" ? "selected" : ""}>CSV (.csv)</option>`}</select><small>${isGscm ? "GSCM's toolbar export only emits Excel. The original workbook is kept and normalized to CSV for SQL." : "Excel originals are preserved and normalized to CSV for SQL."}</small></label>
                            <label><span>Browser mode</span><select id="flow-browser-mode"><option value="headless" ${existing?.browser_mode !== "headed" ? "selected" : ""}>Headless · background</option><option value="headed" ${existing?.browser_mode === "headed" ? "selected" : ""}>Headed · visible debugging</option></select><small id="flow-browser-mode-help">Headless is best for routine runs.</small></label>
                            <label class="flow-week-field"><span>Sell-out Week - start</span><select id="flow-start-week" required><option value="">Choose a discovered week...</option>${_flowDiscoveredWeeks(report, existing?.start_week || "")}</select></label>
                            <label class="flow-week-field"><span>Sell-out Week - end</span><select id="flow-end-week" required><option value="">Choose a discovered week...</option>${_flowDiscoveredWeeks(report, existing?.end_week || "")}</select></label>
                            <label id="flow-window-weeks-field"><span>Weeks per download</span><input id="flow-window-weeks" type="number" min="1" max="105" value="${esc(existing?.window_weeks || 1)}"><small>Each file covers this many consecutive weeks.</small></label>
                            <label class="flow-span-2"><span>Target folder</span><input id="flow-target-folder" required value="${esc(existing?.target_folder || "")}" placeholder="C:\\Reports\\Downloads"><small>The folder must already exist on the authenticated worker machine.</small></label>
                            <label class="flow-span-2"><span>Filename template</span><input id="flow-filename" required value="${esc(existing?.filename_template || defaultFilename)}"><small>${isGscm ? "One bookmark saves one file per run." : "Use {export} when downloading multiple views."} Tokens: {flow}, {report}, {export}, {week}, {start_period}, {end_period}, {year}, {week_number}, {index}, {date}.</small></label>
                        </div>
                    </div>
                    <div class="flow-form-section">
                        <div class="flow-section-head"><h2>Transformation</h2><p>Optionally run one local script against every downloaded file before SQL insertion.</p></div>
                        <div class="flow-form-grid">
                            <label class="flow-check flow-span-2"><input id="flow-transform-enabled" type="checkbox" ${existing?.transform_enabled ? "checked" : ""}><span>Transform downloaded files before SQL insertion</span></label>
                            <div id="flow-transform-fields" class="flow-form-grid flow-span-2">
                                <label class="flow-span-2"><span>Transformation script</span><div class="flow-file-control"><input id="flow-transform-script" required value="${esc(existing?.transform_script_path || "")}" placeholder="Absolute worker path to a .py, .ps1, or .exe script"><button type="button" class="btn-secondary" id="flow-transform-browse">Browse...</button><input id="flow-transform-file" type="file" accept=".py,.ps1,.exe" hidden></div><small>Enter an absolute worker-visible path, including an MX Share UNC path, or upload a script. Python/EXE scripts receive --input and --output; PowerShell receives -InputPath and -OutputPath. The script must create one CSV in script_results per download. Those outputs become the SQL input.</small></label>
                            </div>
                        </div>
                    </div>
                    <div class="flow-form-section">
                        <div class="flow-section-head"><h2>Ownership and failure alerts</h2><p>The owner receives an Outlook alert every time a run of this flow fails, for any reason.</p></div>
                        <div class="flow-form-grid">
                            <label class="flow-span-2"><span>Flow owner</span><select id="flow-owner">${_flowOwnerOptions(people, existing?.owner_person_id)}</select><small id="flow-owner-help">${_flowOwnerHelp(owner)}</small></label>
                        </div>
                    </div>
                    <div class="flow-form-section">
                        <div class="flow-section-head"><h2>Schedule and SQL handoff</h2><p>Save the schedule here, then activate or pause the flow from the Flows list. SQL handoff uses transformed files when transformation is enabled.</p></div>
                        <div class="flow-form-grid">
                            <label><span>Schedule</span><select id="flow-schedule-type"><option value="manual" ${existing?.schedule_type === "manual" || !existing ? "selected" : ""}>Manual</option><option value="daily" ${existing?.schedule_type === "daily" ? "selected" : ""}>Daily</option><option value="weekly" ${existing?.schedule_type === "weekly" ? "selected" : ""}>Weekly</option><option value="monthly" ${existing?.schedule_type === "monthly" ? "selected" : ""}>Monthly</option></select></label>
                            <label><span>Run time</span><input id="flow-schedule-time" type="time" value="${esc(existing?.schedule_time || "08:00")}"></label>
                            <fieldset class="flow-weekdays flow-span-2"><legend>Weekdays</legend>${_FLOW_WEEKDAYS.map(day => `<label><input type="checkbox" value="${day}" ${scheduleDays.has(day) ? "checked" : ""}> ${day.slice(0, 3)}</label>`).join("")}</fieldset>
                            <label id="flow-schedule-day-field"><span>Day of month</span><input id="flow-schedule-day" type="number" min="1" max="31" value="${esc(existing?.schedule_day || 1)}"><small>Months without that day are skipped.</small></label>
                            <label class="flow-check flow-span-2"><input id="flow-sql-enabled" type="checkbox" ${existing?.sql_handoff_enabled ? "checked" : ""} ${!sqlCatalog.configured ? "disabled" : ""}><span>Insert downloaded file into SQL after download</span></label>
                            <div id="flow-sql-fields" class="flow-form-grid flow-span-2">
                                <label><span>Write behavior</span><select id="flow-sql-mode"><option value="append" ${existing?.sql_mode !== "replace" ? "selected" : ""}>Append rows</option><option value="replace" ${existing?.sql_mode === "replace" ? "selected" : ""}>Replace all rows</option></select><small>Replace all rows deletes every row in the table and loads the file from this run instead. The table itself is kept, with its grants, indexes, and constraints; a table that does not exist yet is created. New CSV columns are added as nullable TEXT and older target columns remain. The SQL account needs USAGE and CREATE on the schema, and ownership of an existing target.</small></label>
                                <label><span>Database</span><select id="flow-sql-database">${sqlDatabases.map(value => `<option ${value === selectedDatabase ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></label>
                                <label><span>Schema</span><select id="flow-sql-schema">${sqlSchemas.map(value => `<option ${value === selectedSchema ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></label>
                                <label><span>Table</span><input id="flow-sql-table" list="flow-sql-table-options" maxlength="63" value="${esc(existing?.sql_table || sqlTables[0] || "")}" placeholder="Existing or new table name"><datalist id="flow-sql-table-options">${sqlTables.map(value => `<option value="${esc(value)}"></option>`).join("")}</datalist><small>Append rows requires an existing table. Replace all rows may create this name in the selected schema.</small></label>
                            </div>
                            <div class="flow-span-2 flow-dialog-help">${sqlCatalog.configured ? `SQL catalog: ${sqlCatalog.targets.length} table(s), last scan ${sqlCatalog.scan?.last_scan_at ? esc(timeAgo(sqlCatalog.scan.last_scan_at)) : "not run"}${Number.isFinite(Number(sqlCatalog.scan?.duration_ms)) ? ` (${_flowDuration(sqlCatalog.scan.duration_ms)})` : ""}.` : `SQL handoff unavailable. ${esc((sqlCatalog.missing || []).join(", "))}`} <button type="button" class="btn-sm" id="flow-sql-refresh" ${!sqlCatalog.configured ? "disabled" : ""}>Refresh SQL targets</button></div>
                        </div>
                    </div>
                    <div class="flow-form-error" role="alert"></div><div class="flow-builder-actions"><button type="button" class="btn-secondary" id="flow-builder-cancel">Cancel</button><button type="submit" class="btn-primary">${existing?.id ? "Save changes" : "Create flow"}</button></div>
                </form>
            </div>
            <aside class="flow-summary">
                <h2>Execution contract</h2>
                <dl><div><dt>Estimated download</dt><dd id="flow-download-estimate">${_flowDuration(downloadEstimate?.estimated_ms)}</dd></div><div><dt>Estimate source</dt><dd id="flow-download-estimate-source">${esc(downloadEstimate?.source || "No history")}</dd></div><div><dt>Execution host</dt><dd>BI desktop</dd></div><div><dt>Browser</dt><dd id="flow-browser-summary">${existing?.browser_mode === "headed" ? "Headed · visible" : "Headless · background"}</dd></div><div><dt>Transformation</dt><dd id="flow-transform-summary">${existing?.transform_enabled ? "Enabled · script_results" : "Disabled"}</dd></div><div><dt>Existing files</dt><dd>Keep and add a number suffix</dd></div><div><dt>File deletion</dt><dd>Never</dd></div><div><dt>SQL write</dt><dd id="flow-sql-summary">${existing?.sql_handoff_enabled ? esc(existing.sql_mode === "replace" ? "Replace all rows" : "Append rows") : "Disabled"}</dd></div><div><dt>Failure alerts</dt><dd id="flow-owner-summary">${_flowOwnerSummary(owner)}</dd></div><div><dt>Authentication</dt><dd>Shared local credential</dd></div></dl>
            </aside>
        </div>`;
}

function _flowDuration(ms) {
    if (!Number.isFinite(Number(ms))) return "Not available";
    const seconds = Math.max(0, Math.round(Number(ms) / 1000));
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function _flowTimingSummary(timings) {
    const phases = (timings || []).filter(item => item.phase !== "total");
    return phases.length ? phases.map(item => `${esc(item.phase.replaceAll("_", " "))}: ${_flowDuration(item.duration_ms)}`).join(" · ") : "No phase timing yet";
}

/** The discovered reports as the folder tree the portal itself shows.
 *
 *  A GSCM scan catalogues every bookmark under Public > MDM > Channel Site >
 *  ..., which is hundreds of rows. Grouping only by the first path segment
 *  would put all of them in one "Public" pile, so each level of the path
 *  becomes its own collapsible group, deepest folders holding the reports.
 *  While a filter is active every group is open, so matches are visible
 *  without hunting through collapsed folders. */
function _flowCatalogTreeHtml(reports, reportRow, openTopics, filter = "", depth = 0, prefix = "") {
    const leaves = [];
    const groups = new Map();
    for (const report of reports) {
        const path = report.automation?.category_path || [];
        const segment = path[depth];
        // The last path segment is the report itself, not a folder.
        if (!segment || depth >= path.length - 1) { leaves.push(report); continue; }
        if (!groups.has(segment)) groups.set(segment, []);
        groups.get(segment).push(report);
    }
    const folders = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([segment, items]) => {
        const key = prefix ? `${prefix} › ${segment}` : segment;
        const stale = items.filter(report => report.stale).length;
        const open = filter || openTopics.has(key) ? "open" : "";
        return `<details class="flow-catalog-group" data-topic="${esc(key)}" ${open}><summary><strong>${esc(segment)}</strong> · ${items.length} report${items.length === 1 ? "" : "s"}${stale ? ` · ${stale} stale` : ""}</summary><div class="flow-catalog-list">${_flowCatalogTreeHtml(items, reportRow, openTopics, filter, depth + 1, key)}</div></details>`;
    }).join("");
    return folders + (leaves.length ? leaves.map(reportRow).join("") : "");
}

/** Scan controls for one website. ASAP offers a cheap names-only pass because
 *  a full menu walk opens every report; GSCM's bookmark sweep is one screen. */
function _flowSiteScanButtons(site, activeScan) {
    if (activeScan) {
        return `<button class="btn-sm btn-outline btn-danger-outline flow-stop-scan" data-id="${activeScan.id}" aria-label="Stop active scan of ${esc(site.name)}">Stop scan</button>`;
    }
    if (site.adapter === FLOW_GSCM_ADAPTER) {
        return `<button class="btn-sm flow-scan-site" data-id="${site.id}" title="Read the bookmarks saved on the GSCM home screen">Scan bookmarks</button>`;
    }
    if (site.supports_discovery === false) return "";
    return `<button class="btn-sm flow-scan-site-quick" data-id="${site.id}" title="Catalog report names and menu paths only - fast, no filters">Quick scan</button><button class="btn-sm flow-scan-site" data-id="${site.id}" title="Open every report and discover all filters and options">Full scan</button>`;
}

function _flowScanTargetsReport(scan, report) {
    if (String(scan?.job?.target_report?.id || "") === String(report.id)) return true;
    if (scan?.trigger_type !== "report") return false;
    const reportPath = report.automation?.category_path || [];
    return (scan.job?.discovery?.report_paths || []).some(path =>
        JSON.stringify(path) === JSON.stringify(reportPath));
}

function _flowCatalogHtml(catalog, scans, estimates, workers = []) {
    const latestBySite = new Map();
    scans.forEach(scan => { if (!latestBySite.has(scan.site_id)) latestBySite.set(scan.site_id, scan); });
    const scanEstimate = estimates.catalog_scan;
    const onlineWorkers = workers.filter(worker => worker.status !== "offline");
    const activeStatuses = new Set(["queued", "claimed", "running"]);
    const workerMessage = onlineWorkers.length
        ? `${onlineWorkers.length} BI desktop worker${onlineWorkers.length === 1 ? "" : "s"} online`
        : "No BI desktop worker online";
    const scanMonitor = scan => {
        if (!scan) return "Never scanned";
        const total = scan.timings?.find(item => item.phase === "total");
        const active = activeStatuses.has(scan.status);
        const elapsedFrom = scan.started_at || scan.claimed_at || scan.created_at;
        const elapsed = active && elapsedFrom ? `Elapsed ${_flowDuration(Date.now() - new Date(elapsedFrom))}` : "";
        const waiting = scan.status === "queued" && !onlineWorkers.length
            ? "Waiting for BI desktop worker to start."
            : (scan.progress?.message || (scan.status === "queued" ? "Waiting for worker to claim this scan." : ""));
        const timing = total ? `Actual ${_flowDuration(total.duration_ms)} · ${_flowTimingSummary(scan.timings)}` : [elapsed, _flowTimingSummary(scan.timings)].filter(Boolean).join(" · ");
        return `${_flowStatusBadge(scan.status)}<small>${esc(waiting)}</small><small>${esc(timing || "No phase timing yet")}</small>${scan.error ? `<small class="flow-error">${esc(scan.error)}</small>` : ""}`;
    };
    const activeScans = scans.filter(scan => activeStatuses.has(scan.status));
    const scanBusy = activeScans.length > 0;
    const reportRow = report => {
        // A GSCM bookmark has no Metronome-side prompts to count: GSCM stores
        // the filters, so describing it by "0 discovered filters" would read
        // as a broken scan rather than as the design.
        const detail = report.automation?.kind === "gscm_favorite"
            ? "GSCM bookmark · filters saved in GSCM"
            : `${report.filters.filter(filter => filter.enabled && !filter.stale).length} discovered filters · ${_flowExportViewLabels(report).length} export view(s)${report.automation?.download_links?.length ? ` · ${report.automation.download_links.length} download link(s)` : ""}`;
        const activeScan = activeScans.find(scan => _flowScanTargetsReport(scan, report));
        const action = activeScan
            ? `<button class="btn-sm btn-outline btn-danger-outline flow-stop-scan" data-id="${activeScan.id}" aria-label="Stop scan of ${esc(report.name)}">Stop scan</button>`
            : `<button class="btn-sm flow-scan-report" data-id="${report.id}" ${scanBusy ? "disabled" : ""}>Refresh</button>`;
        return `<div class="flow-catalog-row"><span><strong>${esc(report.name)}</strong><small>${esc(detail)}</small><small>${esc((report.automation?.category_path || []).join(" > "))}</small></span><span>${report.stale ? "Stale" : `Seen ${esc(timeAgo(report.last_seen_at))}`}</span>${action}</div>`;
    };
    const filter = (window._flowsState?.catalogFilter || "").trim().toLowerCase();
    const visibleReports = filter
        ? catalog.reports.filter(report =>
            report.name.toLowerCase().includes(filter)
            || (report.automation?.category_path || []).join(" ").toLowerCase().includes(filter))
        : catalog.reports;
    const openTopics = window._flowsState?.openCatalogTopics || new Set();
    const groupedReports = _flowCatalogTreeHtml(visibleReports, reportRow, openTopics, filter);
    const latestScan = scans[0];
    const scanEvents = window._flowsState?.scanEvents || [];
    const logEntries = scanEvents.map(event => `<div>${esc((event.created_at || "").slice(11, 19))} [${esc(event.stage || event.status)}] ${esc(event.message || "")}</div>`).join("");
    return `
        <div class="flow-discovery-summary">
            <div><h2>Portal discovery</h2><p>Metronome scans ASAP's visible menus, reports, filters, and options, and GSCM's saved home-screen bookmarks. Missing entries are marked stale, never deleted.</p></div>
            <dl><div><dt>Estimated full scan</dt><dd>${_flowDuration(scanEstimate?.estimated_ms)}</dd></div><div><dt>Estimate source</dt><dd>${esc(scanEstimate?.source || "No history")}</dd></div><div><dt>Worker</dt><dd>${esc(workerMessage)}</dd></div></dl>
        </div>
        <div class="flow-catalog-grid">
            <section><div class="flow-panel-head"><div><h2>Websites</h2><p>Authentication, discovery scope, and weekly scan schedule.</p></div><button class="btn-secondary" id="flow-add-site">Add website</button></div>
                ${catalog.sites.length ? `<div class="flow-catalog-list">${catalog.sites.map(site => { const scan = latestBySite.get(site.id); const activeScan = scan && activeStatuses.has(scan.status) ? scan : null; return `<div class="flow-catalog-row"><button class="flow-catalog-main flow-edit-site" data-id="${site.id}"><span><strong>${esc(site.name)}</strong><small>${esc(site.auth_url || site.base_url || "No URL")}</small><small>${site.discovery_enabled ? `Weekly ${esc(site.discovery_weekday)} at ${esc(site.discovery_time)}` : "Automatic scan disabled"}</small></span></button><span>${scanMonitor(scan)}</span><span class="flow-row-actions">${_flowSiteScanButtons(site, activeScan)}</span></div>`; }).join("")}</div>` : '<p class="flow-inline-empty">No websites configured.</p>'}
                <div class="flow-panel-head" style="margin-top:16px"><div><h2>Scan log</h2><p>${latestScan ? `Scan #${latestScan.id} · ${esc(latestScan.status)}${scanBusy ? " · updating every 5s" : ""}` : "No scans yet."}</p></div>${latestScan && activeStatuses.has(latestScan.status) ? `<button class="btn-sm btn-outline btn-danger-outline flow-stop-scan" data-id="${latestScan.id}" aria-label="Stop scan ${latestScan.id}">Stop scan</button>` : ""}</div>
                <div id="flow-scan-log" style="font-family:ui-monospace,Consolas,monospace;font-size:0.72rem;line-height:1.45;max-height:320px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:10px;white-space:pre-wrap;word-break:break-word">${logEntries || '<span style="color:var(--text-dim)">Scan progress events appear here while a scan runs.</span>'}</div>
            </section>
            <section><div class="flow-panel-head"><div><h2>Discovered reports</h2><p>Catalog from successful scans, in the portal's own folder structure.</p></div><input id="flow-catalog-filter" type="search" placeholder="Filter ${catalog.reports.length} report(s)..." value="${esc(window._flowsState?.catalogFilter || "")}" style="min-width:220px"></div>
                ${!catalog.reports.length ? '<p class="flow-inline-empty">No reports discovered yet.</p>'
                    : !visibleReports.length ? '<p class="flow-inline-empty">No report matches that filter.</p>'
                    : groupedReports}
            </section>
        </div>`;
}

function _flowRunsHtml(runs) {
    return runs.length ? `<div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Run</th><th>Flow</th><th>Status</th><th>Requested</th><th>Worker</th><th>Duration</th><th>Result</th><th></th></tr></thead><tbody>${runs.map(run => { const total = run.timings?.find(item => item.phase === "total"); const duration = total?.duration_ms ?? (run.started_at && run.finished_at ? new Date(run.finished_at) - new Date(run.started_at) : null); const browserMode = run.job?.execution?.browser_mode === "headed" ? "Headed browser" : "Headless browser"; const totalFiles = (run.job?.downloads?.periods?.length || 1) * ((run.job?.report?.export_views?.length || 0) || 1); const doneFiles = (run.job?.resume?.completed?.length || 0) + (run.artifacts || []).filter(item => item.status === "saved" && item.file_path).length; const resumable = ["failed", "cancelled"].includes(run.status) && doneFiles > 0 && doneFiles < totalFiles; return `<tr><td>#${run.id}<small>${esc(timeAgo(run.created_at))}</small></td><td>${esc(run.flow_name)}</td><td>${_flowStatusBadge(run.status)}</td><td>${esc(run.requested_by || run.trigger_type)}</td><td>${esc(run.worker_id || "Waiting")}<small>${browserMode}</small></td><td>${duration === null ? "Pending" : _flowDuration(duration)}<small>${_flowTimingSummary(run.timings)}</small></td><td>${run.error ? `<span class="flow-error">${esc(run.error)}</span>` : esc(run.progress?.message || `${run.artifacts?.length || 0} file(s)`)}</td><td class="flow-row-actions">${resumable ? `<button class="btn-sm flow-resume" data-id="${run.id}" title="Queue a run that skips the ${doneFiles} file(s) already saved">Resume · ${doneFiles} of ${totalFiles} saved</button>` : ""}<a class="btn-sm btn-outline" href="/flow-runs/${run.id}" target="_blank" rel="noopener">Expanded logs</a></td></tr>`; }).join("")}</tbody></table></div>` : '<div class="flow-inline-empty">No runs yet.</div>';
}

async function renderFlows() {
    const [catalog, flows, runs, workers, scans, estimates, sqlCatalog, people] = await Promise.all([
        api("/api/flows/catalog"), api("/api/flows"), api("/api/flows/runs"), api("/api/flows/workers"),
        api("/api/flows/scans"), api("/api/flows/estimates"), api("/api/flows/sql/catalog"), api("/api/people"),
    ]);
    let scanEvents = [];
    if (scans[0]) {
        try { scanEvents = (await api(`/api/flows/scans/${scans[0].id}/events`)).events || []; } catch (_) {}
    }
    window._flowsState = {
        catalog, flows, runs, workers, scans, estimates, sqlCatalog, people, scanEvents,
        openCatalogTopics: window._flowsState?.openCatalogTopics || new Set(), view: "list",
    };
    const canCreate = catalog.sites.some(site => site.enabled) && catalog.reports.some(report => report.enabled && !report.stale);
    return `
        <div class="page-header flow-page-header"><div><h1>Flows</h1><p class="subtitle">Configure report downloads executed by the authenticated BI desktop.</p></div>${canCreate ? '<button class="btn-primary" id="flow-create">Create flow</button>' : ""}</div>
        <div class="flow-tabs" role="tablist" aria-label="Flow views"><button id="flow-tab-list" class="active" role="tab" aria-selected="true" aria-controls="flow-workspace" data-flow-view="list">Flows</button><button id="flow-tab-catalog" role="tab" aria-selected="false" aria-controls="flow-workspace" data-flow-view="catalog">Catalog</button><button id="flow-tab-runs" role="tab" aria-selected="false" aria-controls="flow-workspace" data-flow-view="runs">Run history</button></div>
        <div id="flow-workspace" role="tabpanel" aria-labelledby="flow-tab-list">${_flowListHtml(flows, workers, catalog, runs)}</div>`;
}

function _flowShowView(view, payload = null) {
    const state = window._flowsState;
    state.view = view;
    const workspace = document.getElementById("flow-workspace");
    document.querySelectorAll(".flow-tabs button").forEach(button => {
        const active = button.dataset.flowView === view || (view === "builder" && button.dataset.flowView === "list");
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
    });
    const selectedTab = document.querySelector('.flow-tabs button[aria-selected="true"]');
    workspace.setAttribute("aria-labelledby", selectedTab?.id || "flow-tab-list");
    if (view === "catalog") workspace.innerHTML = _flowCatalogHtml(state.catalog, state.scans, state.estimates, state.workers);
    else if (view === "runs") workspace.innerHTML = _flowRunsHtml(state.runs);
    else if (view === "builder") workspace.innerHTML = _flowBuilderHtml(state.catalog, payload);
    else workspace.innerHTML = _flowListHtml(state.flows, state.workers, state.catalog, state.runs);
    _bindFlowWorkspace();
    _flowScheduleCatalogMonitor();
}

function _flowScheduleCatalogMonitor() {
    clearTimeout(window._flowCatalogMonitorTimer);
    const state = window._flowsState;
    const active = state?.view === "catalog" && state.scans.some(scan => ["queued", "claimed", "running"].includes(scan.status));
    if (!active) return;
    window._flowCatalogMonitorTimer = setTimeout(async () => {
        if (window._flowsState?.view !== "catalog") return;
        try {
            const [catalog, scans, workers, estimates] = await Promise.all([
                api("/api/flows/catalog"), api("/api/flows/scans"), api("/api/flows/workers"), api("/api/flows/estimates"),
            ]);
            let scanEvents = window._flowsState.scanEvents || [];
            if (scans[0]) {
                try { scanEvents = (await api(`/api/flows/scans/${scans[0].id}/events`)).events || []; } catch (_) {}
            }
            Object.assign(window._flowsState, { catalog, scans, workers, estimates, scanEvents });
            const workspace = document.getElementById("flow-workspace");
            if (!workspace || window._flowsState.view !== "catalog") return;
            workspace.innerHTML = _flowCatalogHtml(catalog, scans, estimates, workers);
            _bindFlowWorkspace();
            _flowScheduleCatalogMonitor();
        } catch (_) {
            _flowScheduleCatalogMonitor();
        }
    }, 5000);
}

function _flowBindDialog(overlay, restoreFocus) {
    const close = () => { overlay.remove(); restoreFocus?.focus?.(); };
    overlay.querySelector(".flow-dialog-cancel").onclick = close;
    overlay.addEventListener("keydown", event => {
        if (event.key === "Escape") { event.preventDefault(); close(); return; }
        if (event.key !== "Tab") return;
        const focusable = [...overlay.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')]
            .filter(element => !element.hidden && element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    overlay.querySelector("input, select, button")?.focus();
    return close;
}

function _flowSiteDialog(site = null) {
    const restoreFocus = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "task-modal-overlay";
    overlay.innerHTML = `<div class="task-modal flow-dialog" role="dialog" aria-modal="true" aria-labelledby="flow-site-title"><h2 id="flow-site-title">${site ? "Edit" : "Add"} website</h2><form id="flow-site-form"><label>Website type<select id="flow-site-adapter"><option value="asap_portal" ${site?.adapter === "asap_portal" ? "selected" : ""}>ASAP</option><option value="gscm_portal" ${site?.adapter === "gscm_portal" ? "selected" : ""}>GSCM</option><option value="web_export" ${!["asap_portal", "gscm_portal"].includes(site?.adapter) ? "selected" : ""}>Other report portal</option></select></label><label>Name<input id="flow-site-name" required value="${esc(site?.name || "")}" placeholder="ASAP"></label><label>Portal URL<input id="flow-site-auth" type="url" value="${esc(site?.auth_url || "")}" placeholder="https://portal.example.com/login"></label><label>Base URL<input id="flow-site-base" type="url" value="${esc(site?.base_url || "")}"></label><div id="flow-site-credentials" ${site?.adapter === "gscm_portal" ? "hidden" : ""}><div class="flow-dialog-rule"></div><h3>Automatic sign-in</h3><p class="flow-dialog-help">${site?.credentials_configured ? "An encrypted BI-desktop credential is configured. Leave these blank to keep it." : "Store the ASAP credential once. Windows encrypts it for this BI-desktop account; Metronome never returns it or stores it in GitHub."}</p><label>ASAP user ID<input id="flow-site-username" autocomplete="off" value=""></label><label>ASAP password<input id="flow-site-password" type="password" autocomplete="new-password" value=""></label></div><p id="flow-site-sso-help" class="flow-dialog-help" ${site?.adapter === "gscm_portal" ? "" : "hidden"}>GSCM has no stored credential. Its session lives in the automation browser profile: rerun setup.ps1, or the worker's --authenticate-url bootstrap, and complete Samsung SSO once in the visible window.</p><div class="flow-dialog-rule"></div><h3>Automatic discovery</h3><p class="flow-dialog-help">${site?.adapter === "gscm_portal" ? "A weekly BI-desktop scan reads the bookmarks saved on the GSCM home screen. Missing bookmarks are marked stale, never deleted." : "A weekly BI-desktop scan detects every visible ASAP menu, report, filter, and option. Missing entries are marked stale, never deleted."}</p><div class="flow-form-grid"><label>Weekend day<select id="flow-site-scan-day">${_FLOW_WEEKDAYS.map(day => `<option value="${day}" ${day === (site?.discovery_weekday || "saturday") ? "selected" : ""}>${day[0].toUpperCase() + day.slice(1)}</option>`).join("")}</select></label><label>Scan time<input id="flow-site-scan-time" type="time" value="${esc(site?.discovery_time || "06:00")}"></label></div><label class="flow-check"><input id="flow-site-discovery" type="checkbox" ${site?.discovery_enabled !== false ? "checked" : ""}><span>Run discovery weekly</span></label><label class="flow-check"><input id="flow-site-enabled" type="checkbox" ${site?.enabled !== false ? "checked" : ""}><span>Website enabled</span></label><div class="flow-form-error" role="alert"></div><div class="flow-builder-actions"><button type="button" class="btn-secondary flow-dialog-cancel">Cancel</button><button type="submit" class="btn-primary">Save website</button></div></form></div>`;
    document.body.appendChild(overlay);
    const close = _flowBindDialog(overlay, restoreFocus);
    overlay.querySelector("#flow-site-adapter").addEventListener("change", event => {
        // GSCM signs in through the automation browser profile, so it has no
        // credential fields to show.
        const gscm = event.target.value === "gscm_portal";
        overlay.querySelector("#flow-site-credentials").hidden = gscm;
        overlay.querySelector("#flow-site-sso-help").hidden = !gscm;
    });
    overlay.querySelector("form").onsubmit = async event => {
        event.preventDefault();
        const submit = event.currentTarget.querySelector('button[type="submit"]');
        const error = overlay.querySelector(".flow-form-error");
        submit.disabled = true;
        error.textContent = "";
        const body = { name: $("#flow-site-name").value.trim(), adapter: $("#flow-site-adapter").value, auth_url: $("#flow-site-auth").value.trim() || null, base_url: $("#flow-site-base").value.trim() || null, discovery_enabled: $("#flow-site-discovery").checked, discovery_interval_hours: 168, discovery_scope: ["*"], discovery_weekday: $("#flow-site-scan-day").value, discovery_time: $("#flow-site-scan-time").value, enabled: $("#flow-site-enabled").checked };
        try {
            const saved = await (site ? apiPut(`/api/flows/sites/${site.id}`, body) : apiPostJson("/api/flows/sites", body));
            const username = $("#flow-site-username").value;
            const password = $("#flow-site-password").value;
            // Only ASAP stores a credential locally; GSCM signs in through the
            // browser profile, so there is nothing to post for it.
            if (body.adapter === "asap_portal" && (username || password)) {
                if (!username || !password) throw new Error("Enter both the ASAP user ID and password.");
                await apiPostJson(`/api/flows/sites/${saved.id}/credentials`, { username, password });
            }
            close(); await navigate("flows"); toast("Website saved");
        }
        catch (err) { error.textContent = "Website not saved: " + err.message; submit.disabled = false; }
    };
}

function _flowCollectBuilder() {
    const selections = {};
    document.querySelectorAll("[data-flow-filter]").forEach(control => {
        selections[control.dataset.flowFilter] = control.multiple ? [...control.selectedOptions].map(option => option.value) : control.value;
    });
    const scheduleType = $("#flow-schedule-type").value;
    const periodStrategy = $("#flow-period-strategy").value;
    const downloadMode = $("#flow-download-mode").value;
    const transformEnabled = $("#flow-transform-enabled")?.checked || false;
    const sqlEnabled = $("#flow-sql-enabled")?.checked || false;
    const existing = window._flowsState?.flows?.find(flow => flow.id === Number($("#flow-builder-form")?.dataset.id));
    const flowEnabled = scheduleType === "manual" ? false : (existing?.enabled || false);
    return { name: $("#flow-name").value.trim(), site_id: Number($("#flow-site").value), report_id: Number($("#flow-report").value), export_views: [...document.querySelectorAll("[data-flow-export-view]:checked")].map(input => input.value), download_links: [...document.querySelectorAll("[data-flow-download-link]:checked")].map(input => input.value), enabled: flowEnabled, selections, download_mode: downloadMode, period_strategy: periodStrategy, window_weeks: periodStrategy !== "none" && (periodStrategy === "rolling" || downloadMode === "one_per_period") ? Number($("#flow-window-weeks").value) : null, file_format: $("#flow-file-format").value, browser_mode: $("#flow-browser-mode").value, start_week: periodStrategy === "none" ? null : ($("#flow-start-week").value || null), end_week: periodStrategy === "fixed" ? ($("#flow-end-week").value || null) : null, target_folder: $("#flow-target-folder").value.trim(), filename_template: $("#flow-filename").value.trim(), schedule_type: scheduleType, schedule_time: scheduleType === "manual" ? null : $("#flow-schedule-time").value, schedule_days: scheduleType === "weekly" ? [...document.querySelectorAll(".flow-weekdays input:checked")].map(input => input.value) : [], schedule_day: scheduleType === "monthly" ? Number($("#flow-schedule-day").value) : null, transform_enabled: transformEnabled, transform_script_path: transformEnabled ? $("#flow-transform-script").value.trim() : null, sql_handoff_enabled: sqlEnabled, sql_mode: sqlEnabled ? $("#flow-sql-mode").value : null, sql_database: sqlEnabled ? $("#flow-sql-database").value : null, sql_schema: sqlEnabled ? $("#flow-sql-schema").value : null, sql_table: sqlEnabled ? $("#flow-sql-table").value : null, owner_person_id: Number($("#flow-owner")?.value) || null };
}

function _bindFlowWorkspace() {
    const state = window._flowsState;
    $("#flow-add-site-empty")?.addEventListener("click", () => _flowSiteDialog());
    $("#flow-scan-empty")?.addEventListener("click", async () => { const site = state.catalog.sites.find(item => item.enabled); if (!site) return; try { await apiPost(`/api/flows/sites/${site.id}/scan`); toast("Catalog discovery queued"); await navigate("flows"); } catch (err) { toast("Scan not queued: " + err.message); } });
    $("#flow-create-empty")?.addEventListener("click", () => _flowShowView("builder"));
    $("#flow-add-site")?.addEventListener("click", () => _flowSiteDialog());
    // Discovered-report groups: remember which topics the user opened so the
    // 5-second scan monitor re-render does not collapse them again.
    document.querySelectorAll(".flow-catalog-group").forEach(group => group.addEventListener("toggle", () => {
        const state_ = window._flowsState;
        if (!state_) return;
        state_.openCatalogTopics = state_.openCatalogTopics || new Set();
        if (group.open) state_.openCatalogTopics.add(group.dataset.topic);
        else state_.openCatalogTopics.delete(group.dataset.topic);
    }));
    const scanLog = $("#flow-scan-log");
    if (scanLog) scanLog.scrollTop = scanLog.scrollHeight;
    document.querySelectorAll(".flow-edit-site").forEach(button => button.onclick = () => _flowSiteDialog(state.catalog.sites.find(site => site.id === Number(button.dataset.id))));
    document.querySelectorAll(".flow-scan-site").forEach(button => button.onclick = async () => {
        button.disabled = true;
        const site = state.catalog.sites.find(item => String(item.id) === String(button.dataset.id));
        const queued = site?.adapter === FLOW_GSCM_ADAPTER
            ? "GSCM bookmark scan queued"
            : "Full ASAP discovery queued";
        try { await apiPost(`/api/flows/sites/${button.dataset.id}/scan?mode=full`); toast(queued); await navigate("flows"); }
        catch (err) { toast("Scan not queued: " + err.message); button.disabled = false; }
    });
    document.querySelectorAll(".flow-scan-site-quick").forEach(button => button.onclick = async () => { button.disabled = true; try { await apiPost(`/api/flows/sites/${button.dataset.id}/scan?mode=partial`); toast("Quick scan queued - report names and menu paths only"); await navigate("flows"); } catch (err) { toast("Quick scan not queued: " + err.message); button.disabled = false; } });
    document.querySelectorAll(".flow-stop-scan").forEach(button => button.onclick = async () => {
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = "Stopping...";
        try {
            const result = await apiPost(`/api/flows/scans/${button.dataset.id}/stop`);
            toast(result.message || "Scan stopped");
            const [catalog, scans, workers, estimates] = await Promise.all([
                api("/api/flows/catalog"), api("/api/flows/scans"), api("/api/flows/workers"), api("/api/flows/estimates"),
            ]);
            let scanEvents = state.scanEvents || [];
            if (scans[0]) {
                try { scanEvents = (await api(`/api/flows/scans/${scans[0].id}/events`)).events || []; } catch (_) {}
            }
            Object.assign(state, { catalog, scans, workers, estimates, scanEvents });
            _flowShowView("catalog");
        } catch (err) {
            toast("Scan not stopped: " + err.message);
            button.disabled = false;
            button.textContent = "Stop scan";
        }
    });
    const catalogFilter = $("#flow-catalog-filter");
    if (catalogFilter) {
        catalogFilter.addEventListener("input", event => {
            state.catalogFilter = event.target.value;
            const caret = event.target.selectionStart;
            _flowShowView("catalog");
            const refocused = $("#flow-catalog-filter");
            if (refocused) { refocused.focus(); refocused.setSelectionRange(caret, caret); }
        });
    }
    document.querySelectorAll(".flow-scan-report").forEach(button => button.onclick = async () => { button.disabled = true; try { await apiPost(`/api/flows/reports/${button.dataset.id}/scan`); toast("Report refresh queued"); await navigate("flows"); } catch (err) { toast("Report refresh not queued: " + err.message); button.disabled = false; } });
    document.querySelectorAll(".flow-edit").forEach(button => button.onclick = () => _flowShowView("builder", state.flows.find(flow => flow.id === Number(button.dataset.id))));
    document.querySelectorAll(".flow-enabled-switch").forEach(input => input.onchange = async () => { const enabled = input.checked; input.disabled = true; try { const updated = await apiPatch(`/api/flows/${input.dataset.id}/enabled`, { enabled }); const flow = state.flows.find(item => item.id === updated.id); Object.assign(flow, updated); _flowShowView("list"); toast(enabled ? "Flow activated" : "Flow paused"); } catch (err) { input.checked = !enabled; input.disabled = false; toast("Flow status not changed: " + err.message); } });
    document.querySelectorAll(".flow-run").forEach(button => button.onclick = async () => { button.disabled = true; const flow = state.flows.find(item => item.id === Number(button.dataset.id)); try { await apiPost(`/api/flows/${button.dataset.id}/run`); toast(flow?.browser_mode === "headed" ? "Run queued. Edge is opening in the BI desktop." : "Run queued for the background worker"); await navigate("flows"); } catch (err) { toast("Run not queued: " + err.message); button.disabled = false; } });
    document.querySelectorAll(".flow-stop").forEach(button => button.onclick = async () => { button.disabled = true; try { const result = await apiPost(`/api/flows/${button.dataset.id}/stop`); toast(result.message || "Run stopped"); await navigate("flows"); } catch (err) { toast("Run not stopped: " + err.message); button.disabled = false; } });
    document.querySelectorAll(".flow-resume").forEach(button => button.onclick = async () => { button.disabled = true; try { const result = await apiPost(`/api/flows/runs/${button.dataset.id}/resume`); toast(`Resume queued - skipping ${result.skipped_files} saved file(s)`); await navigate("flows"); } catch (err) { toast("Resume not queued: " + err.message); button.disabled = false; } });
    $("#flow-builder-cancel")?.addEventListener("click", () => _flowShowView("list"));
    $("#flow-replicate-apply")?.addEventListener("click", () => {
        const sourceId = Number($("#flow-replicate-source")?.value);
        if (!sourceId) { toast("Choose a flow to replicate"); return; }
        const source = (state.flows || []).find(item => item.id === sourceId);
        if (!source) { toast("That flow is no longer available"); return; }
        // Copy everything except identity: the new flow needs its own name,
        // and it starts paused so a copied schedule cannot fire before it has
        // been reviewed. The SQL table is kept - changing it is usually the
        // only edit - but the name is deliberately left for the user.
        const copy = {
            ...source,
            id: null, name: "", enabled: false, _replicated_from: sourceId,
            last_run_at: null, last_status: null, last_error: null, last_success_at: null,
        };
        _flowShowView("builder", copy);
        const status = $("#flow-replicate-status");
        const picker = $("#flow-replicate-source");
        if (picker) picker.value = String(sourceId);
        if (status) status.textContent = `Copied from ${source.name}. Give this flow a name, then change what differs.`;
        const nameInput = $("#flow-name");
        if (nameInput) { nameInput.value = `${source.name} copy`; nameInput.focus(); nameInput.select(); }
        toast(`Settings copied from ${source.name}`);
    });
    // Scan just the report being configured: the fast path to filters when
    // the catalog was built by a quick scan, or when the report changed.
    $("#flow-scan-report-now")?.addEventListener("click", async event => {
        const button = event.currentTarget;
        const status = $("#flow-report-scan-status");
        const reportId = $("#flow-report")?.value;
        if (!reportId) { toast("Choose a report first"); return; }
        button.disabled = true;
        const original = button.textContent;
        button.textContent = "Scanning...";
        // Keep what the user already picked; a rescan should refresh the
        // available options, not silently discard the configuration.
        const previous = {};
        document.querySelectorAll("[data-flow-filter]").forEach(control => {
            previous[control.dataset.flowFilter] = control.multiple
                ? [...control.selectedOptions].map(option => option.value) : control.value;
        });
        try {
            const queued = await apiPost(`/api/flows/reports/${reportId}/scan`);
            if (status) status.textContent = "Scan queued. Waiting for the BI desktop worker...";
            const deadline = Date.now() + 15 * 60 * 1000;
            let scan = null;
            while (Date.now() < deadline) {
                await new Promise(resolve => setTimeout(resolve, 5000));
                const scans = await api("/api/flows/scans");
                scan = scans.find(item => item.id === queued.id);
                if (!scan) break;
                if (status) status.textContent = `Scan ${scan.status}: ${scan.progress?.message || "working..."}`;
                if (["succeeded", "failed", "cancelled"].includes(scan.status)) break;
            }
            if (scan && scan.status !== "succeeded") throw new Error(scan.error || `Scan ${scan.status}`);
            const catalog = await api("/api/flows/catalog");
            window._flowsState.catalog = catalog;
            const report = catalog.reports.find(item => String(item.id) === String(reportId));
            const active = (report?.filters || []).filter(filter => filter.enabled && !filter.stale);
            const section = $("#flow-report-filters .flow-form-grid");
            if (section) {
                const editable = active.filter(filter => filter.control_type !== "week");
                section.innerHTML = editable.length
                    ? editable.map(definition => `<label><span>${esc(definition.label)}${definition.required ? " *" : ""}</span>${_flowFilterControl(definition, previous[definition.filter_key] ?? null)}</label>`).join("")
                    : '<p class="flow-inline-empty">This report has no configurable filters.</p>';
            }
            _flowSyncExportSections(report);
            const start = $("#flow-start-week");
            const end = $("#flow-end-week");
            if (start) start.innerHTML = `<option value="">Choose a discovered week...</option>${_flowDiscoveredWeeks(report, start.value || "")}`;
            if (end) end.innerHTML = `<option value="">Choose a discovered week...</option>${_flowDiscoveredWeeks(report, end.value || "")}`;
            const links = _flowDownloadLinkLabels(report).length;
            if (status) status.textContent = `Scanned: ${active.length} filter(s), ${_flowExportViewLabels(report).length} export view(s)${links ? `, ${links} download link(s)` : ""} discovered.`;
            toast("Report scanned");
        } catch (err) {
            if (status) status.textContent = "Scan failed: " + err.message;
            toast("Report scan failed: " + err.message);
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    });
    $("#flow-site")?.addEventListener("change", event => {
        // ASAP and GSCM are different websites with different forms: GSCM hides
        // filters, export views, and the period controls entirely. Swapping the
        // report list alone would leave the wrong sections on screen.
        const form = $("#flow-builder-form");
        const previous = _flowSiteIsGscm(state.catalog, form?.dataset.siteId || "");
        const next = _flowSiteIsGscm(state.catalog, event.target.value);
        if (previous !== next) {
            const draft = _flowCollectBuilder();
            _flowShowView("builder", {
                ...draft,
                id: Number(form?.dataset.id) || null,
                site_id: Number(event.target.value),
                report_id: null,
                selections: {},
                export_views: [],
                download_links: [],
            });
            return;
        }
        const reportSelect = $("#flow-report");
        reportSelect.innerHTML = _flowReportOptions(state.catalog, event.target.value, null);
        reportSelect.dispatchEvent(new Event("change"));
    });
    $("#flow-report")?.addEventListener("change", async event => { const report = state.catalog.reports.find(item => String(item.id) === event.target.value); const section = $("#flow-report-filters"); if (!section) return; section.querySelector(".flow-form-grid").innerHTML = report && report.filters.filter(filter => filter.enabled && !filter.stale && filter.control_type !== "week").length ? report.filters.filter(filter => filter.enabled && !filter.stale && filter.control_type !== "week").map(definition => `<label><span>${esc(definition.label)}${definition.required ? " *" : ""}</span>${_flowFilterControl(definition, null)}</label>`).join("") : '<p class="flow-inline-empty">This discovered report has no additional configurable filters.</p>'; _flowSyncExportSections(report); const hasWeek = _flowHasWeekFilter(report); const period = $("#flow-period-strategy"); if (period) { period.value = hasWeek ? "latest" : "none"; period.dispatchEvent(new Event("change")); } const start = $("#flow-start-week"); const end = $("#flow-end-week"); if (start) start.innerHTML = `<option value="">Choose a discovered week...</option>${_flowDiscoveredWeeks(report, "")}`; if (end) end.innerHTML = `<option value="">Choose a discovered week...</option>${_flowDiscoveredWeeks(report, "")}`; if (report) { try { const estimates = await api(`/api/flows/estimates?site_id=${report.site_id}&report_id=${report.id}`); $("#flow-download-estimate").textContent = _flowDuration(estimates.flow_download.estimated_ms); $("#flow-download-estimate-source").textContent = estimates.flow_download.source; } catch (_) {} } });
    $("#flow-browser-mode")?.addEventListener("change", event => {
        const headed = event.target.value === "headed";
        $("#flow-browser-mode-help").textContent = headed
            ? "Opens Edge in the signed-in BI desktop. Use it to build or debug a flow."
            : "Runs in the background. Best for routine and scheduled downloads.";
        $("#flow-browser-summary").textContent = headed ? "Headed · visible" : "Headless · background";
    });
    $("#flow-browser-mode")?.dispatchEvent(new Event("change"));
    $("#flow-owner")?.addEventListener("change", event => {
        const owner = (state.people || []).find(person => person.id === Number(event.target.value));
        const help = $("#flow-owner-help");
        const summary = $("#flow-owner-summary");
        if (help) help.innerHTML = _flowOwnerHelp(owner);
        if (summary) summary.innerHTML = _flowOwnerSummary(owner);
    });
    $("#flow-file-format")?.addEventListener("change", event => {
        const filename = $("#flow-filename");
        if (filename) filename.value = filename.value.replace(/\.(?:csv|xlsx)$/i, `.${event.target.value}`);
    });
    $("#flow-period-strategy")?.addEventListener("change", event => {
        const none = event.target.value === "none";
        const rolling = event.target.value === "rolling";
        const fixed = event.target.value === "fixed";
        $("#flow-start-week").closest("label").hidden = none;
        $("#flow-start-week").required = !none;
        $("#flow-end-week").closest("label").hidden = !fixed;
        $("#flow-end-week").required = fixed;
        $("#flow-window-weeks-field").hidden = !rolling;
        $("#flow-window-weeks").required = rolling;
        if (none) {
            $("#flow-download-mode").value = "single";
            $("#flow-download-mode").disabled = true;
        } else if (rolling) {
            $("#flow-download-mode").value = "one_per_period";
            $("#flow-download-mode").disabled = true;
        } else {
            $("#flow-download-mode").disabled = false;
        }
    });
    const updatePeriodFields = () => {
        const none = $("#flow-period-strategy").value === "none";
        const rolling = $("#flow-period-strategy").value === "rolling";
        const perPeriod = $("#flow-download-mode").value === "one_per_period";
        $("#flow-window-weeks-field").hidden = none || !(rolling || perPeriod);
        $("#flow-window-weeks").required = !none && (rolling || perPeriod);
    };
    $("#flow-download-mode")?.addEventListener("change", updatePeriodFields);
    $("#flow-period-strategy")?.addEventListener("change", updatePeriodFields);
    $("#flow-period-strategy")?.dispatchEvent(new Event("change"));
    const updateTransformFields = () => {
        const enabled = $("#flow-transform-enabled")?.checked || false;
        const fields = $("#flow-transform-fields");
        const input = $("#flow-transform-script");
        if (fields) fields.hidden = !enabled;
        if (input) input.required = enabled;
        if ($("#flow-transform-summary")) $("#flow-transform-summary").textContent = enabled ? "Enabled · script_results" : "Disabled";
    };
    $("#flow-transform-enabled")?.addEventListener("change", updateTransformFields);
    $("#flow-transform-browse")?.addEventListener("click", () => $("#flow-transform-file")?.click());
    $("#flow-transform-file")?.addEventListener("change", async event => {
        const file = event.target.files?.[0];
        if (!file) return;
        const button = $("#flow-transform-browse");
        button.disabled = true;
        const original = button.textContent;
        button.textContent = "Adding...";
        try {
            const body = new FormData();
            body.append("file", file, file.name);
            const saved = await apiPostForm("/api/flows/transform-script", body);
            $("#flow-transform-script").value = saved.script_path;
            toast(`Transformation script added: ${saved.filename}`);
        } catch (err) {
            toast("Script not added: " + err.message);
        } finally {
            event.target.value = "";
            button.disabled = false;
            button.textContent = original;
        }
    });
    updateTransformFields();
    const updateSqlFields = () => {
        const enabled = $("#flow-sql-enabled")?.checked || false;
        const fields = $("#flow-sql-fields");
        const summary = $("#flow-sql-summary");
        if (fields) fields.hidden = !enabled;
        if (summary) summary.textContent = enabled ? ($("#flow-sql-mode").value === "replace" ? "Replace all rows" : "Append rows") : "Disabled";
    };
    const repopulateSql = () => {
        const targets = state.sqlCatalog.targets || [];
        const database = $("#flow-sql-database").value;
        const schemas = [...new Set(targets.filter(item => item.database === database).map(item => item.schema))];
        const priorSchema = $("#flow-sql-schema").value;
        $("#flow-sql-schema").innerHTML = schemas.map(value => `<option ${value === priorSchema ? "selected" : ""}>${esc(value)}</option>`).join("");
        const schema = $("#flow-sql-schema").value;
        const tables = targets.filter(item => item.database === database && item.schema === schema).map(item => item.table);
        $("#flow-sql-table-options").innerHTML = tables.map(item => `<option value="${esc(item)}"></option>`).join("");
        if ($("#flow-sql-mode").value === "append" && !tables.includes($("#flow-sql-table").value)) $("#flow-sql-table").value = tables[0] || "";
    };
    $("#flow-sql-enabled")?.addEventListener("change", updateSqlFields);
    $("#flow-sql-mode")?.addEventListener("change", () => { updateSqlFields(); repopulateSql(); });
    $("#flow-sql-database")?.addEventListener("change", repopulateSql);
    $("#flow-sql-schema")?.addEventListener("change", repopulateSql);
    $("#flow-sql-refresh")?.addEventListener("click", async event => { event.currentTarget.disabled = true; try { await apiPost("/api/flows/sql/catalog/refresh"); toast("SQL targets refreshed"); await navigate("flows"); } catch (err) { toast("SQL targets not refreshed: " + err.message); event.currentTarget.disabled = false; } });
    if ($("#flow-sql-fields")) updateSqlFields();
    $("#flow-schedule-type")?.addEventListener("change", event => {
        const manual = event.target.value === "manual";
        const weekly = event.target.value === "weekly";
        const monthly = event.target.value === "monthly";
        $("#flow-schedule-time").closest("label").hidden = manual;
        $(".flow-weekdays").hidden = !weekly;
        $("#flow-schedule-day-field").hidden = !monthly;
        $("#flow-schedule-day").required = monthly;
    });
    $("#flow-schedule-type")?.dispatchEvent(new Event("change"));
    $("#flow-builder-form")?.addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const button = form.querySelector('button[type="submit"]'); const error = form.querySelector(".flow-form-error"); button.disabled = true; error.textContent = ""; try { const body = _flowCollectBuilder(); await (form.dataset.id ? apiPut(`/api/flows/${form.dataset.id}`, body) : apiPostJson("/api/flows", body)); toast("Flow saved"); await navigate("flows"); } catch (err) { error.textContent = "Flow not saved: " + err.message; error.scrollIntoView({ behavior: "smooth", block: "nearest" }); button.disabled = false; } });
}

function bindFlowsPage() {
    document.querySelectorAll(".flow-tabs button").forEach(button => button.onclick = () => _flowShowView(button.dataset.flowView));
    $("#flow-create")?.addEventListener("click", () => _flowShowView("builder"));
    _bindFlowWorkspace();
}

function bindDataImportPage() {
    const pick = document.getElementById("di-pick");
    if (!pick) return;
    const fileInput = document.getElementById("di-file");
    const fileInfo = document.getElementById("di-file-info");
    const previewBox = document.getElementById("di-preview");
    const targetBox = document.getElementById("di-target");
    const scheduleBox = document.getElementById("di-schedule");
    const mvPicker = document.getElementById("di-mv-picker");
    const mvToggle = document.getElementById("di-mv-toggle");
    const mvToggleText = document.getElementById("di-mv-toggle-text");
    const mvMenu = document.getElementById("di-mv-menu");
    const mvFilter = document.getElementById("di-mv-filter");
    const mvList = document.getElementById("di-mv-list");
    const mvRefreshBtn = document.getElementById("di-refresh-mv");
    const mvStatus = document.getElementById("di-mv-status");
    const scriptBox = document.getElementById("di-script");
    const scriptDir = document.getElementById("di-script-dir");
    const saveScriptDirBtn = document.getElementById("di-save-script-dir");
    const scriptDirStatus = document.getElementById("di-script-dir-status");
    const scheduleFields = document.getElementById("di-prefect-schedule-fields");
    const scheduleTimeWrap = document.getElementById("di-schedule-time-wrap");
    const scheduleDayWrap = document.getElementById("di-schedule-day-wrap");
    const scheduleCronWrap = document.getElementById("di-schedule-cron-wrap");
    const scheduleTimezoneWrap = document.getElementById("di-schedule-timezone-wrap");
    const scheduleTime = document.getElementById("di-schedule-time");
    const scheduleDay = document.getElementById("di-schedule-day");
    const scheduleCron = document.getElementById("di-schedule-cron");
    const scheduleTimezone = document.getElementById("di-schedule-timezone");
    const scheduleSummary = document.getElementById("di-schedule-summary");
    const scriptName = document.getElementById("di-script-name");
    const createScriptBtn = document.getElementById("di-create-script");
    const scriptResult = document.getElementById("di-script-result");
    const tableSelect = document.getElementById("di-table");
    const newName = document.getElementById("di-newname");
    const createTableBtn = document.getElementById("di-create-table");
    const createResult = document.getElementById("di-create-result");

    let staged = null;   // { token, filename, rows, columns, sample }
    let tablesLoaded = false;
    let materializedViewsLoaded = false;
    let materializedViews = [];
    let selectedMvKeys = new Set();
    let currentScriptDir = scriptDir.value.trim();

    async function diFetch(path, opts = {}) {
        const res = await fetch(path, { ...opts, headers: apiHeaders(opts.headers || {}) });
        let data = {};
        try { data = await res.json(); } catch (_) {}
        if (!res.ok) throw new Error(data.detail || `API error: ${res.status}`);
        return data;
    }

    const targetType = () => document.querySelector('input[name="di-targettype"]:checked').value;
    const mode = () => document.querySelector('input[name="di-mode"]:checked').value;
    const tableName = () => targetType() === "new" ? newName.value.trim().toLowerCase() : tableSelect.value;
    const mvKey = v => `${v.schema}.${v.name}`;
    const selectedMaterializedViews = () => materializedViews
        .filter(v => selectedMvKeys.has(mvKey(v)))
        .map(v => ({ schema: v.schema, name: v.name }));
    const selectedScheduleType = () => document.querySelector('input[name="di-schedule-type"]:checked').value;

    try {
        scheduleTimezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (_) {
        scheduleTimezone.value = "UTC";
    }

    function invalidateScript() {
        if (scriptResult) scriptResult.innerHTML = "";
        refreshLoadButton();
    }

    function canConfigureScript() {
        return Boolean(staged && targetType() === "existing" && tableName());
    }

    function refreshLoadButton() {
        const ready = canConfigureScript();
        scheduleBox.style.display = ready ? "" : "none";
        scriptBox.style.display = ready ? "" : "none";
    }

    function scheduleTimeParts() {
        const raw = scheduleTime.value || "08:00";
        const parts = raw.split(":");
        const hour = Number(parts[0]);
        const minute = Number(parts[1]);
        if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
            throw new Error("Choose a valid Prefect schedule time");
        }
        return {
            hour,
            minute,
            text: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`,
        };
    }

    function cleanScheduleTimezone() {
        return (scheduleTimezone.value || "UTC").trim() || "UTC";
    }

    function cleanCron(value) {
        const cron = (value || "").trim().replace(/\s+/g, " ");
        if (cron.split(" ").filter(Boolean).length !== 5) {
            throw new Error("Cron schedule must have five fields");
        }
        return cron;
    }

    function buildPrefectSchedule() {
        const type = selectedScheduleType();
        const timezone = cleanScheduleTimezone();
        if (type === "manual") {
            return { type: "manual", cron: null, timezone };
        }
        if (type === "cron") {
            return { type: "cron", cron: cleanCron(scheduleCron.value), timezone };
        }

        const time = scheduleTimeParts();
        if (type === "daily") {
            return { type: "daily", cron: `${time.minute} ${time.hour} * * *`, timezone };
        }
        if (type === "weekly") {
            return { type: "weekly", cron: `${time.minute} ${time.hour} * * ${scheduleDay.value}`, timezone };
        }
        throw new Error("Choose a Prefect schedule type");
    }

    function refreshPrefectScheduleControls() {
        const type = selectedScheduleType();
        const usesCron = type === "cron";
        const usesTime = type === "daily" || type === "weekly";
        scheduleFields.style.display = type === "manual" ? "none" : "flex";
        scheduleTimeWrap.style.display = usesTime ? "" : "none";
        scheduleDayWrap.style.display = type === "weekly" ? "" : "none";
        scheduleCronWrap.style.display = usesCron ? "" : "none";
        scheduleTimezoneWrap.style.display = type === "manual" ? "none" : "";
        try {
            const schedule = buildPrefectSchedule();
            if (schedule.type === "manual") {
                scheduleSummary.textContent = "Manual / run once";
            } else {
                scheduleSummary.textContent = `${schedule.type === "cron" ? "Custom cron" : schedule.type}: ${schedule.cron} (${schedule.timezone})`;
            }
        } catch (err) {
            scheduleSummary.textContent = err.message;
        }
    }

    function refreshMvToggle() {
        if (materializedViewsLoaded && !materializedViews.length) {
            mvToggleText.textContent = "No materialized views found";
            mvToggle.setAttribute("aria-expanded", "false");
            return;
        }
        const count = selectedMvKeys.size;
        mvToggleText.textContent = count ? `${count} materialized view${count === 1 ? "" : "s"} selected for script` : "No materialized views selected for script";
        mvToggle.setAttribute("aria-expanded", mvMenu.style.display !== "none" ? "true" : "false");
    }

    async function saveScriptDir() {
        const path = scriptDir.value.trim();
        if (!path) {
            toast("Script folder cannot be blank");
            throw new Error("Script folder cannot be blank");
        }
        saveScriptDirBtn.disabled = true;
        saveScriptDirBtn.textContent = "Saving...";
        scriptDirStatus.textContent = "";
        try {
            const r = await diFetch("/api/data-import/script-dir", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path }),
            });
            currentScriptDir = r.script_dir;
            scriptDir.value = r.script_dir;
            scriptDirStatus.textContent = "Saved";
            toast("Script folder saved");
            return r;
        } catch (err) {
            scriptDirStatus.textContent = err.message;
            toast("Script folder save failed: " + err.message);
            throw err;
        } finally {
            saveScriptDirBtn.disabled = false;
            saveScriptDirBtn.textContent = "Save folder";
        }
    }

    function renderMaterializedViewList() {
        const filter = (mvFilter.value || "").trim().toLowerCase();
        const views = filter
            ? materializedViews.filter(v => `${v.display_name} ${v.refresh_schedule || ""}`.toLowerCase().includes(filter))
            : materializedViews;
        if (!materializedViews.length) {
            mvList.innerHTML = `<span style="font-size:0.78rem;color:var(--text-dim);padding:0.25rem">No materialized views found.</span>`;
            return;
        }
        if (!views.length) {
            mvList.innerHTML = `<span style="font-size:0.78rem;color:var(--text-dim);padding:0.25rem">No matches.</span>`;
            return;
        }
        mvList.innerHTML = views.map((v, i) => {
            const key = mvKey(v);
            const id = `di-mv-${i}`;
            const sched = v.refresh_schedule ? `<span style="color:var(--text-dim);font-size:0.72rem;margin-left:0.35rem">${esc(v.refresh_schedule)}</span>` : "";
            return `<label for="${id}" style="display:flex;gap:0.45rem;align-items:flex-start;font-size:0.8rem;cursor:pointer;padding:0.35rem 0.4rem;border-radius:4px">
                <input type="checkbox" class="di-mv-check" id="${id}" data-key="${esc(key)}" ${selectedMvKeys.has(key) ? "checked" : ""} style="margin-top:0.1rem">
                <span><strong>${esc(v.display_name)}</strong>${sched}</span>
            </label>`;
        }).join("");
        document.querySelectorAll(".di-mv-check").forEach(cb => {
            cb.addEventListener("change", () => {
                if (cb.checked) selectedMvKeys.add(cb.dataset.key);
                else selectedMvKeys.delete(cb.dataset.key);
                refreshMvToggle();
                invalidateScript();
            });
        });
    }

    async function loadTables(force = false) {
        if (tablesLoaded && !force) return;
        try {
            const data = await diFetch("/api/data-import/tables");
            tableSelect.innerHTML = data.tables.length
                ? data.tables.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join("")
                : '<option value="">(no tables in schema)</option>';
            tablesLoaded = true;
        } catch (err) {
            tableSelect.innerHTML = '<option value="">(could not list tables)</option>';
            toast("Could not list tables: " + err.message);
        }
        refreshLoadButton();
    }

    async function loadMaterializedViews() {
        if (materializedViewsLoaded) return;
        mvToggle.disabled = true;
        mvToggleText.textContent = "Loading materialized views...";
        try {
            const data = await diFetch("/api/data-import/materialized-views");
            materializedViews = data.views || [];
            selectedMvKeys = new Set([...selectedMvKeys].filter(key => materializedViews.some(v => mvKey(v) === key)));
            renderMaterializedViewList();
            mvToggle.disabled = false;
            if (!materializedViews.length) mvToggle.disabled = true;
            refreshMvToggle();
            materializedViewsLoaded = true;
        } catch (err) {
            materializedViews = [];
            selectedMvKeys = new Set();
            mvList.innerHTML = `<span style="font-size:0.78rem;color:var(--red);padding:0.25rem">Could not list materialized views: ${esc(err.message)}</span>`;
            mvToggleText.textContent = "Could not list materialized views";
            mvToggle.disabled = true;
            toast("Could not list materialized views: " + err.message);
        }
        refreshLoadButton();
    }

    mvToggle.addEventListener("click", () => {
        if (mvToggle.disabled) return;
        mvMenu.style.display = mvMenu.style.display === "none" ? "" : "none";
        refreshMvToggle();
        if (mvMenu.style.display !== "none") mvFilter.focus();
    });
    mvFilter.addEventListener("input", renderMaterializedViewList);
    document.addEventListener("click", e => {
        if (!mvPicker.contains(e.target)) {
            mvMenu.style.display = "none";
            refreshMvToggle();
        }
    });
    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && mvMenu.style.display !== "none") {
            mvMenu.style.display = "none";
            refreshMvToggle();
            mvToggle.focus();
        }
    });

    pick.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", async () => {
        const f = fileInput.files[0];
        if (!f) return;
        pick.disabled = true;
        pick.textContent = "Parsing...";
        createResult.textContent = "";
        if (scriptResult) scriptResult.innerHTML = "";
        if (mvStatus) mvStatus.textContent = "";
        try {
            const fd = new FormData();
            fd.append("file", f);
            const p = await diFetch("/api/data-import/preview", { method: "POST", body: fd });
            staged = p;
            fileInfo.textContent = `${p.filename} - ${p.rows} rows, ${p.columns.length} columns`;
            const head = p.columns.map(c => `<th style="text-align:left;padding:0.25rem 0.6rem;border-bottom:1px solid var(--border);font-weight:600">${esc(c)}</th>`).join("");
            const body = p.sample.map(r => `<tr>${r.map(v => `<td style="padding:0.25rem 0.6rem;border-bottom:1px solid var(--border);color:var(--text-secondary)">${esc(v)}</td>`).join("")}</tr>`).join("");
            previewBox.innerHTML = `
                <div style="overflow-x:auto;border:1px solid var(--border);border-radius:5px">
                    <table style="border-collapse:collapse;font-size:0.75rem;font-family:monospace;min-width:100%">
                        <thead><tr>${head}</tr></thead><tbody>${body}</tbody>
                    </table>
                </div>
                <div style="font-size:0.72rem;color:var(--text-dim);margin-top:0.3rem">First ${p.sample.length} rows shown with normalized column names.</div>`;
            targetBox.style.display = "";
            await loadTables();
            await loadMaterializedViews();
            refreshLoadButton();
        } catch (err) {
            staged = null;
            previewBox.innerHTML = "";
            targetBox.style.display = "none";
            scheduleBox.style.display = "none";
            scriptBox.style.display = "none";
            fileInfo.textContent = "Parse failed: " + err.message;
            toast("Parse failed: " + err.message);
        } finally {
            pick.disabled = false;
            pick.textContent = "Choose file...";
            fileInput.value = "";
            refreshLoadButton();
        }
    });

    document.querySelectorAll('input[name="di-targettype"]').forEach(r => {
        r.addEventListener("change", () => {
            document.getElementById("di-existing").style.display = targetType() === "existing" ? "" : "none";
            document.getElementById("di-new").style.display = targetType() === "new" ? "" : "none";
            createResult.textContent = "";
            invalidateScript();
        });
    });
    document.querySelectorAll('input[name="di-mode"]').forEach(r => r.addEventListener("change", invalidateScript));
    tableSelect.addEventListener("change", invalidateScript);
    newName.addEventListener("input", () => {
        createResult.textContent = "";
        invalidateScript();
    });
    scriptDir.addEventListener("input", invalidateScript);
    saveScriptDirBtn.addEventListener("click", () => saveScriptDir().catch(() => {}));
    document.querySelectorAll('input[name="di-schedule-type"]').forEach(r => {
        r.addEventListener("change", () => {
            refreshPrefectScheduleControls();
            invalidateScript();
        });
    });
    [scheduleTime, scheduleDay, scheduleCron, scheduleTimezone].forEach(el => {
        el.addEventListener("input", () => {
            refreshPrefectScheduleControls();
            invalidateScript();
        });
        el.addEventListener("change", () => {
            refreshPrefectScheduleControls();
            invalidateScript();
        });
    });
    scriptName.addEventListener("input", invalidateScript);

    createTableBtn.addEventListener("click", async () => {
        if (!staged) return;
        const t = newName.value.trim().toLowerCase();
        if (!t) {
            toast("Enter a new table name");
            return;
        }
        createTableBtn.disabled = true;
        createTableBtn.textContent = "Creating...";
        createResult.textContent = "";
        try {
            const r = await diFetch("/api/data-import/load", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token: staged.token, table: t, mode: "create", materialized_views: [] }),
            });
            createResult.textContent = `Created ${r.schema}.${r.table} (${r.rows} rows).`;
            fileInfo.textContent = `${staged.filename} - ${staged.rows} rows. Created ${r.schema}.${r.table}; configure the recurring script below.`;
            toast(`${r.rows} rows loaded into ${r.schema}.${r.table}`);
            tablesLoaded = false;
            await loadTables(true);
            if (![...tableSelect.options].some(option => option.value === r.table)) {
                tableSelect.insertAdjacentHTML("beforeend", `<option value="${esc(r.table)}">${esc(r.table)}</option>`);
            }
            tableSelect.value = r.table;
            const existingRadio = document.querySelector('input[name="di-targettype"][value="existing"]');
            if (existingRadio) existingRadio.checked = true;
            document.getElementById("di-existing").style.display = "";
            document.getElementById("di-new").style.display = "none";
            invalidateScript();
        } catch (err) {
            createResult.textContent = err.message;
            toast("Create table failed: " + err.message);
        } finally {
            createTableBtn.disabled = false;
            createTableBtn.textContent = "Create table now";
            refreshLoadButton();
        }
    });

    mvRefreshBtn.addEventListener("click", async () => {
        const views = selectedMaterializedViews();
        if (!views.length) {
            toast("Choose at least one materialized view");
            return;
        }
        mvRefreshBtn.disabled = true;
        mvRefreshBtn.textContent = "Refreshing...";
        mvStatus.textContent = "";
        try {
            const r = await diFetch("/api/data-import/materialized-views/refresh", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ materialized_views: views }),
            });
            mvStatus.textContent = `Refreshed ${r.refreshed.join(", ")}`;
            toast(`Refreshed ${r.refreshed.length} materialized view${r.refreshed.length === 1 ? "" : "s"}`);
        } catch (err) {
            mvStatus.textContent = err.message;
            toast("Refresh failed: " + err.message);
        } finally {
            mvRefreshBtn.disabled = false;
            mvRefreshBtn.textContent = "Refresh selected now";
        }
    });

    createScriptBtn.addEventListener("click", async () => {
        if (!canConfigureScript()) {
            toast(targetType() === "new" ? "Create the table first" : "Choose an existing target table");
            return;
        }
        const t = tableName();
        if (!t) {
            toast("Choose a target table");
            return;
        }
        const m = mode();
        let prefectSchedule;
        try {
            prefectSchedule = buildPrefectSchedule();
        } catch (err) {
            toast(err.message);
            return;
        }
        createScriptBtn.disabled = true;
        createScriptBtn.textContent = "Creating...";
        scriptResult.innerHTML = "";
        try {
            if (scriptDir.value.trim() !== currentScriptDir) await saveScriptDir();
            const body = {
                token: staged.token,
                table: t,
                mode: m,
                materialized_views: selectedMaterializedViews(),
                schedule: prefectSchedule,
                script_name: scriptName.value.trim() || null,
            };
            const r = await diFetch("/api/data-import/script", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const mvLine = r.materialized_views && r.materialized_views.length
                ? `<div>Refreshes: <code>${esc(r.materialized_views.join(", "))}</code></div>`
                : "";
            const sched = r.schedule || {};
            const scheduleLine = sched.label
                ? `<div>Prefect schedule: <code style="word-break:break-all">${esc(sched.label)}</code></div>`
                : "";
            scriptResult.innerHTML = `
                <div style="border:1px solid var(--border);border-radius:5px;padding:0.55rem 0.7rem">
                    <div>Script: <code style="word-break:break-all">${esc(r.script_path)}</code></div>
                    <div>Entrypoint: <code style="word-break:break-all">${esc(r.entrypoint)}</code></div>
                    <div>Run: <code style="word-break:break-all">${esc(r.run_command)}</code></div>
                    <div>Serve: <code style="word-break:break-all">${esc(r.serve_command)}</code></div>
                    ${scheduleLine}
                    ${mvLine}
                </div>`;
            toast("Import script created");
            refreshLoadButton();
        } catch (err) {
            scriptResult.textContent = err.message;
            toast("Script creation failed: " + err.message);
            refreshLoadButton();
        } finally {
            createScriptBtn.disabled = false;
            createScriptBtn.textContent = "Create append/replace script";
        }
    });

    refreshPrefectScheduleControls();
    refreshLoadButton();
}


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    sources: renderSources,
    reports: renderReports,
    scripts: renderScripts,
    scheduledtasks: renderScheduledTasks,
    powerautomate: renderPowerAutomate,
    lineage: renderLineageDiagram,
    scanner: renderScanner,
    changelog: renderChangelog,
    create: renderCreate,
    bestpractices: renderBestPractices,
    dataquality: renderDataQuality,
    email: renderEmail,
    recurrences: renderRecurrences,
    export: renderExport,
    dataimport: renderDataImport,
    flows: renderFlows,
    eventlog: renderEventLog,
    faq: renderFaq,
    refreshschedule: renderRefreshSchedule,
    premiumviewers: renderPremiumViewers,
};

// Map old hash routes to new pages for backwards compat
const pageAliases = { alerts: "dashboard", issues: "dashboard", actions: "dashboard", overview: "dashboard", tasks: "dashboard" };

let currentPage = "dashboard";
let navigationRequestId = 0;

async function navigate(page) {
    const requestId = ++navigationRequestId;
    // Resolve aliases for old routes
    if (pageAliases[page]) page = pageAliases[page];
    if (!pages[page]) page = "dashboard";
    currentPage = page;

    // Update URL hash without triggering hashchange
    window._skipHash = true;
    location.hash = page === "dashboard" ? "" : page;

    // Reset lazy-init flags
    window._lineageBound = false;

    $$("nav a[data-page]").forEach(a => {
        a.classList.toggle("active", a.dataset.page === page);
    });
    // Highlight parent nav-group when a child page is active
    $$("nav .nav-group").forEach(g => {
        const childPages = (g.dataset.pages || "").split(",");
        g.classList.toggle("active", childPages.includes(page));
    });

    // Save scroll position before destroying the page
    const prevPage = window._prevNavPage;
    const scrollY = window.scrollY;
    if (prevPage) {
        try { sessionStorage.setItem("scroll_" + prevPage, String(scrollY)); } catch (_) {}
    }
    window._prevNavPage = page;

    const app = $("#app");
    app.innerHTML = '<div class="loading">Loading...</div>';

    try {
        const html = await pages[page]();
        if (requestId !== navigationRequestId || page !== currentPage) return;
        app.innerHTML = html;

        bindDataTables();
        detectTableScroll();

        // Restore scroll position if returning to same page
        if (prevPage === page) {
            const savedScroll = sessionStorage.getItem("scroll_" + page);
            if (savedScroll) requestAnimationFrame(() => window.scrollTo(0, parseInt(savedScroll)));
        }
        if (page === "dashboard") {
            // Update nav health dot
            const navDot = document.getElementById("nav-health-dot");
            const dd = window._dashboardData;
            if (navDot && dd) {
                if (dd.sources_outdated > 0 || dd.alerts_active > 5) navDot.style.background = "var(--red)"; // degraded
                else if (dd.sources_stale > 0) navDot.style.background = "var(--red)"; // degraded (legacy stale)
                else navDot.style.background = "var(--green)"; // healthy
            }
            // Clickable stat card sub-labels: filter navigation
            document.querySelectorAll(".stat-filter[data-filter]").forEach(dot => {
                dot.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const filterVal = dot.dataset.filter;
                    await navigate("sources");
                    const dt = window._dt && window._dt["dt-sources"];
                    if (dt) {
                        dt.filters["status"] = filterVal;
                        const filterInput = document.querySelector('tr.filter-row input[data-dt="dt-sources"][data-fcol="status"]');
                        if (filterInput) filterInput.value = filterVal;
                        _refreshDT("dt-sources");
                    }
                });
            });
            // Clickable stat cards: navigate to target page
            document.querySelectorAll(".stat-card-clickable[data-navigate]").forEach(card => {
                card.addEventListener("click", () => navigate(card.dataset.navigate));
                card.addEventListener("keydown", (e) => {
                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(card.dataset.navigate); }
                });
            });
            // Health bar tooltips
            const healthTooltip = document.getElementById("health-tooltip");
            if (healthTooltip) {
                document.querySelectorAll(".health-bar .segment[data-tooltip]").forEach(seg => {
                    seg.addEventListener("mouseenter", () => {
                        healthTooltip.textContent = seg.dataset.tooltip;
                        healthTooltip.classList.add("visible");
                        const rect = seg.getBoundingClientRect();
                        const containerRect = seg.closest(".health-bar-container").getBoundingClientRect();
                        healthTooltip.style.left = (rect.left + rect.width / 2 - containerRect.left) + "px";
                    });
                    seg.addEventListener("mouseleave", () => {
                        healthTooltip.classList.remove("visible");
                    });
                });
            }
            // Health bar click-to-filter navigation
            document.querySelectorAll(".segment-clickable[data-filter]").forEach(seg => {
                seg.addEventListener("click", async () => {
                    const filterVal = seg.dataset.filter;
                    await navigate("sources");
                    const dt = window._dt && window._dt["dt-sources"];
                    if (dt) {
                        dt.filters["status"] = filterVal;
                        const filterInput = document.querySelector('tr.filter-row input[data-dt="dt-sources"][data-fcol="status"]');
                        if (filterInput) filterInput.value = filterVal;
                        _refreshDT("dt-sources");
                    }
                });
            });
            // Dashboard alerts (table + person filter chips)
            bindDashboardAlerts();
            // Draw health trend chart
            drawHealthTrendChart();
        }
        if (page === "scanner") bindScannerButtons();
        if (page === "sources") bindSourcesPage();
        if (page === "reports") bindReportsPage();
        if (page === "scripts") bindScriptsPage();
        if (page === "scheduledtasks") bindScheduledTasksPage();
        if (page === "powerautomate") bindPowerAutomatePage();
        if (page === "flows") bindFlowsPage();
        if (page === "create") bindCreatePage();
        if (page === "changelog") bindChangelogPage();
        if (page === "bestpractices") bindBestPracticesPage();
        if (page === "dataquality") bindDataQualityPage();
        if (page === "email") bindEmailPage();
        if (page === "recurrences") bindRecurrencesPage();
        if (page === "export") bindExportPage();
        if (page === "dataimport") bindDataImportPage();
        if (page === "faq") bindFaqPage();
        if (page === "eventlog") bindEventLogPage();
        if (page === "refreshschedule") bindRefreshSchedulePage();
        if (page === "premiumviewers") bindPremiumViewersPage();
        if (page === "lineage") bindLineageDiagramPage();
    } catch (err) {
        if (requestId !== navigationRequestId || page !== currentPage) return;
        app.innerHTML = '<div class="empty-state" style="margin-top:2rem"><strong>Failed to load page</strong><br><span style="color:var(--text-dim);font-size:0.8rem">' + esc(err.message) + '</span><br><br><button onclick="navigate(\'' + page + '\')" class="btn-outline" style="font-size:0.8rem">Retry</button></div>';
    }
}

function bindScannerButtons() {
    // Log toggle
    document.querySelectorAll(".log-toggle").forEach(h2 => {
        h2.addEventListener("click", () => {
            const target = document.getElementById(h2.dataset.target);
            if (target) {
                const showing = target.style.display !== "none";
                target.style.display = showing ? "none" : "";
                const hint = h2.querySelector("span");
                if (hint) hint.textContent = showing ? "- click to expand" : "- click to collapse";
            }
        });
    });

    const btnScan = $("#btn-scan");
    if (btnScan) {
        btnScan.addEventListener("click", async () => {
            btnScan.disabled = true;
            btnScan.textContent = "Scanning...";
            try {
                const result = await apiPost("/api/scanner/run");
                if (result.status === "pbi_sync_not_completed") {
                    const pbi = result.pbi_sync || {};
                    toast(`Refresh stopped before scan: PBI sync ${pbi.status || "did not complete"}`);
                    navigate("scanner");
                    return;
                }
                if (result.status === "stopped") {
                    toast(result.message || "Scanner refresh stopped");
                    navigate("scanner");
                    return;
                }
                if (result.status === "failed") {
                    toast("Scanner refresh failed: " + (result.error || result.message || "unknown error"));
                    btnScan.disabled = false;
                    btnScan.textContent = "Run Scan Now";
                    return;
                }
                const pbi = result.pbi_sync || {};
                const pbiMsg = pbi.status === "completed"
                    ? "PBI sync completed"
                    : `PBI sync ${pbi.status || "not launched"}`;
                toast(`Scan complete: ${result.reports_scanned} reports, ${result.sources_found} sources; ${pbiMsg}`);
                navigate("scanner");
            } catch (err) {
                toast("Scan failed: " + err.message);
                btnScan.disabled = false;
                btnScan.textContent = "Run Scan Now";
            }
        });
    }

    const btnProbe = $("#btn-probe");
    if (btnProbe) {
        btnProbe.addEventListener("click", async () => {
            btnProbe.disabled = true;
            btnProbe.textContent = "Probing...";
            try {
                const result = await apiPost("/api/scanner/probe");
                if (result.status === "stopped") {
                    toast(result.message || "Probe stopped");
                } else {
                    toast(`Probe complete: ${result.probed || 0} sources checked`);
                }
                btnProbe.disabled = false;
                btnProbe.textContent = "Probe Sources";
            } catch (err) {
                toast("Probe failed: " + err.message);
                btnProbe.disabled = false;
                btnProbe.textContent = "Probe Sources";
            }
        });
    }

    const btnDiagnose = $("#btn-diagnose");
    if (btnDiagnose) {
        btnDiagnose.addEventListener("click", async () => {
            const panel = $("#diagnose-panel");
            if (!panel) return;
            // Toggle off if already showing
            if (panel.style.display !== "none") {
                panel.style.display = "none";
                return;
            }
            btnDiagnose.disabled = true;
            btnDiagnose.textContent = "Diagnosing...";
            try {
                const d = await api("/api/scanner/diagnose");
                panel.innerHTML = renderDiagnosePanel(d);
                panel.style.display = "";
            } catch (err) {
                panel.innerHTML = `<div class="section" style="border-left:3px solid var(--red);padding-left:0.75rem;margin-bottom:1.25rem"><h2>Diagnostics Error</h2><pre class="scan-log">${esc(err.message)}</pre></div>`;
                panel.style.display = "";
            }
            btnDiagnose.disabled = false;
            btnDiagnose.textContent = "Diagnose";
        });
    }

    const btnStopPbiSync = $("#btn-stop-pbi-sync");
    if (btnStopPbiSync) {
        btnStopPbiSync.addEventListener("click", async () => {
            if (!confirm("Stop all running or pending scanner refresh, probe, and Power BI sync work?")) return;
            btnStopPbiSync.disabled = true;
            btnStopPbiSync.textContent = "Stopping...";
            try {
                const result = await apiPost("/api/scanner/pbi-sync/stop");
                toast(result.message || "Sync stop requested");
                navigate("scanner");
            } catch (err) {
                toast("Stop refresh work failed: " + err.message);
                btnStopPbiSync.disabled = false;
                btnStopPbiSync.textContent = "Stop Refresh Work";
            }
        });
    }

    const btnPbiConnect = $("#btn-pbi-connect");
    if (btnPbiConnect) {
        btnPbiConnect.addEventListener("click", async () => {
            btnPbiConnect.disabled = true;
            btnPbiConnect.textContent = "Starting sign-in...";
            const box = document.getElementById("pbi-connect-box");
            if (box) box.innerHTML = '<div class="pbi-sync-warning">Contacting the Microsoft sign-in service from the server... this can take up to a minute if the network blocks or proxies the connection.</div>';
            try {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 75000);
                const res = await fetch("/api/scanner/pbi-auth/connect", { method: "POST", headers: apiHeaders(), signal: controller.signal });
                clearTimeout(timer);
                if (!res.ok) {
                    const hint = res.status === 404
                        ? "The app service is still running old code. Update and restart the service, then reload this page."
                        : `The connect endpoint returned HTTP ${res.status}. Check the service error log.`;
                    if (box) box.innerHTML = `<div class="pbi-sync-warning">${esc(hint)}</div>`;
                    toast("Connect failed: " + hint);
                    return;
                }
                const flow = await res.json();
                if (box) box.innerHTML = _pbiDeviceFlowHtml(flow);
                if (flow.status === "pending") {
                    btnPbiConnect.textContent = "Waiting for sign-in...";
                    _watchPbiDeviceFlow();
                    return;
                }
                toast(flow.message || "Could not start the Microsoft sign-in");
            } catch (err) {
                const msg = err.name === "AbortError"
                    ? "No reply from the app after 75 seconds. The server may be unable to reach login.microsoftonline.com (firewall or missing proxy). Check the service error log."
                    : "Connect failed: " + err.message;
                if (box) box.innerHTML = `<div class="pbi-sync-warning">${esc(msg)}</div>`;
                toast(msg);
            } finally {
                if (btnPbiConnect.textContent === "Starting sign-in...") {
                    btnPbiConnect.disabled = false;
                    btnPbiConnect.textContent = "Connect Power BI";
                }
            }
        });
    }

    const btnPbiDisconnect = $("#btn-pbi-disconnect");
    if (btnPbiDisconnect) {
        btnPbiDisconnect.addEventListener("click", async () => {
            if (!confirm("Forget the saved Microsoft account? Scheduled syncs will fall back to the interactive sign-in window until you connect again.")) return;
            try {
                const result = await apiPost("/api/scanner/pbi-auth/disconnect");
                toast(result.message || "Disconnected");
                navigate("scanner");
            } catch (err) {
                toast("Disconnect failed: " + err.message);
            }
        });
    }

    // Resume polling if a device-code sign-in is already in progress
    const flowNote = document.getElementById("pbi-flow-note");
    if (flowNote) _watchPbiDeviceFlow();
}

function renderDiagnosePanel(d) {
    const errBlock = d.errors.length > 0
        ? `<div style="background:rgba(239,68,68,0.08);border:1px solid var(--red);border-radius:6px;padding:0.6rem 0.75rem;margin-bottom:0.75rem">
            <strong style="color:var(--red)">Errors (${d.errors.length})</strong>
            <ul style="margin:0.4rem 0 0 1.2rem;padding:0;color:var(--red)">${d.errors.map(e => `<li>${esc(e)}</li>`).join("")}</ul>
           </div>`
        : "";

    const pathStatus = d.exists
        ? (d.is_dir ? '<span style="color:var(--green)">exists (directory)</span>' : '<span style="color:var(--yellow)">exists (not a directory)</span>')
        : '<span style="color:var(--red)">does not exist</span>';

    // Directory listing
    let dirListHtml = "";
    if (d.directory_listing.length > 0) {
        const rows = d.directory_listing.map(e => {
            const icon = e.is_dir ? "DIR" : "FILE";
            const size = e.size_bytes != null ? ` (${(e.size_bytes / 1024).toFixed(1)} KB)` : "";
            const isPbix = e.name.endsWith(".pbix") ? ' style="color:var(--green)"' : "";
            const isSemantic = e.name.toLowerCase().endsWith(".semanticmodel") ? ' style="color:var(--blue)"' : "";
            return `<span style="color:var(--text-dim);display:inline-block;width:3rem">${icon}</span> <span${isPbix || isSemantic}>${esc(e.name)}</span>${size}`;
        }).join("\n");
        dirListHtml = `<pre class="scan-log" style="max-height:200px">${rows}</pre>`;
    } else if (d.exists) {
        dirListHtml = `<div style="color:var(--text-dim);font-size:0.8rem">Directory is empty</div>`;
    }

    // Steps
    const stepsHtml = d.steps.map(s => {
        const found = s.found != null ? ` - found ${s.found}` : "";
        const result = s.result ? ` - ${s.result}` : "";
        const extra = s.tables != null ? ` (${s.tables} tables, ${s.measures} measures)` : "";
        const files = s.files && s.files.length > 0 ? `\n    ${s.files.join("\n    ")}` : "";
        return `${esc(s.action)}${found}${result}${extra}${files}`;
    }).join("\n");

    // TMDL folder analysis
    let tmdlHtml = "";
    if (d.tmdl_folders.length > 0) {
        const rows = d.tmdl_folders.map(f => {
            const checks = [
                f.has_semantic_model ? '<span style="color:var(--green)">SemanticModel</span>' : '<span style="color:var(--red)">SemanticModel</span>',
                f.has_definition ? '<span style="color:var(--green)">Definition</span>' : '<span style="color:var(--text-dim)">Definition</span>',
                f.has_tables ? '<span style="color:var(--green)">Tables</span>' : '<span style="color:var(--text-dim)">Tables</span>',
            ].join(" / ");
            const tmdlCount = f.tmdl_file_count > 0 ? ` - ${f.tmdl_file_count} .tmdl files` : "";
            const reason = f.skip_reason ? `<div style="color:var(--yellow);font-size:0.72rem;margin-left:1rem">${esc(f.skip_reason)}</div>` : `<div style="color:var(--green);font-size:0.72rem;margin-left:1rem">OK${tmdlCount}</div>`;
            const contents = f.contents.length > 0 ? `<div style="color:var(--text-dim);font-size:0.68rem;margin-left:1rem">Contents: ${f.contents.join(", ")}</div>` : "";
            return `<div style="padding:0.3rem 0;border-bottom:1px solid var(--border)"><strong>${esc(f.folder)}</strong> ${checks}${reason}${contents}</div>`;
        }).join("");
        tmdlHtml = `<div class="section"><h2>TMDL Folder Analysis</h2><div style="font-size:0.82rem">${rows}</div></div>`;
    }

    // PBIX file list
    let pbixHtml = "";
    if (d.pbix_files.length > 0) {
        pbixHtml = `<div class="section"><h2>PBIX Files Found (${d.pbix_files.length})</h2><pre class="scan-log">${d.pbix_files.map(f => esc(f)).join("\n")}</pre></div>`;
    }

    return `
        <div class="section" style="border-left:3px solid var(--blue);padding-left:0.75rem;margin-bottom:1.25rem">
            <h2>Scanner Diagnostics</h2>
            ${errBlock}
            <table style="font-size:0.82rem;margin-bottom:0.75rem">
                <tr><td style="padding-right:1rem;color:var(--text-dim)">Raw path</td><td><code>${esc(d.raw_path)}</code></td></tr>
                <tr><td style="padding-right:1rem;color:var(--text-dim)">Resolved path</td><td><code>${esc(d.resolved_path)}</code></td></tr>
                <tr><td style="padding-right:1rem;color:var(--text-dim)">Status</td><td>${pathStatus}</td></tr>
                <tr><td style="padding-right:1rem;color:var(--text-dim)">Mode</td><td>${d.mode ? esc(d.mode).toUpperCase() : "N/A"}</td></tr>
            </table>
            <h3 style="font-size:0.82rem;margin-bottom:0.4rem">Directory Listing</h3>
            ${dirListHtml}
            <h3 style="font-size:0.82rem;margin:0.75rem 0 0.4rem">Discovery Steps</h3>
            <pre class="scan-log">${stepsHtml}</pre>
            ${pbixHtml}
            ${tmdlHtml}
        </div>
    `;
}


// ── AI Chat Panel ──

function initAIChatPanel() {
    if (document.getElementById("ai-chat-panel")) return;

    // Floating action button
    const fab = document.createElement("button");
    fab.className = "ai-fab";
    fab.id = "ai-fab";
    fab.innerHTML = "AI";
    fab.title = "AI Assistant";
    document.body.appendChild(fab);

    // Overlay
    const overlay = document.createElement("div");
    overlay.className = "ai-chat-overlay";
    overlay.id = "ai-chat-overlay";
    document.body.appendChild(overlay);

    // Panel
    const panel = document.createElement("div");
    panel.className = "ai-chat-panel";
    panel.id = "ai-chat-panel";
    panel.innerHTML = `
        <div class="ai-chat-header">
            <h3>AI Assistant</h3>
            <button class="ai-chat-close" id="ai-chat-close">&times;</button>
        </div>
        <div class="ai-chat-messages" id="ai-chat-messages">
            <div class="ai-msg assistant">
                <p>Hi! I can help you understand your analytics ecosystem. Ask me about risks, source health, or specific reports.</p>
            </div>
        </div>
        <div class="ai-chat-quick" id="ai-chat-quick">
            <button class="ai-quick-chip" data-q="What's degraded?">What's degraded?</button>
            <button class="ai-quick-chip" data-q="Summarize dashboard">Summarize dashboard</button>
            <button class="ai-quick-chip" data-q="Show degraded sources">Show degraded sources</button>
            <button class="ai-quick-chip" data-q="Audit report queries">Audit report queries</button>
        </div>
        <div class="ai-chat-input-area">
            <textarea class="ai-chat-input" id="ai-chat-input" placeholder="Ask about your data..." rows="1"></textarea>
            <button class="ai-chat-send" id="ai-chat-send">&#9654;</button>
        </div>
    `;
    document.body.appendChild(panel);

    // Bind events
    fab.addEventListener("click", () => toggleAIChat(true));
    overlay.addEventListener("click", () => toggleAIChat(false));
    document.getElementById("ai-chat-close").addEventListener("click", () => toggleAIChat(false));
    document.getElementById("ai-chat-send").addEventListener("click", sendAIChat);

    const input = document.getElementById("ai-chat-input");
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendAIChat();
        }
    });
    // Auto-resize
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 100) + "px";
    });

    // Quick chips
    document.querySelectorAll(".ai-quick-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            input.value = chip.dataset.q;
            sendAIChat();
        });
    });
}

function toggleAIChat(open) {
    const panel = document.getElementById("ai-chat-panel");
    const overlay = document.getElementById("ai-chat-overlay");
    const fab = document.getElementById("ai-fab");
    if (open) {
        panel.classList.add("open");
        overlay.classList.add("visible");
        fab.style.display = "none";
        document.body.classList.add("ai-panel-open");
        document.getElementById("ai-chat-input").focus();
    } else {
        panel.classList.remove("open");
        overlay.classList.remove("visible");
        fab.style.display = "flex";
        document.body.classList.remove("ai-panel-open");
    }
}

async function sendAIChat() {
    const input = document.getElementById("ai-chat-input");
    const msg = input.value.trim();
    if (!msg) return;

    const messages = document.getElementById("ai-chat-messages");
    const sendBtn = document.getElementById("ai-chat-send");

    // Add user message
    const userEl = document.createElement("div");
    userEl.className = "ai-msg user";
    userEl.textContent = msg;
    messages.appendChild(userEl);
    input.value = "";
    input.style.height = "auto";
    sendBtn.disabled = true;

    // Hide quick chips after first message
    const quickArea = document.getElementById("ai-chat-quick");
    if (quickArea) quickArea.style.display = "none";

    // Typing indicator
    const typing = document.createElement("div");
    typing.className = "ai-typing";
    typing.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
    messages.appendChild(typing);
    messages.scrollTop = messages.scrollHeight;

    try {
        const res = await fetch("/api/ai/chat", {
            method: "POST",
            headers: apiHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ message: msg }),
        });
        const data = await res.json();
        typing.remove();

        const assistantEl = document.createElement("div");
        assistantEl.className = "ai-msg assistant ai-content";
        assistantEl.innerHTML = renderMd(data.response);
        messages.appendChild(assistantEl);
    } catch (err) {
        typing.remove();
        const errEl = document.createElement("div");
        errEl.className = "ai-msg assistant";
        errEl.innerHTML = `<p style="color:var(--red)">Error: ${err.message}</p>`;
        messages.appendChild(errEl);
    }

    sendBtn.disabled = false;
    messages.scrollTop = messages.scrollHeight;
}


// ── AI Report Risk (Dependencies detail) ──

async function renderAIReportRisk(reportId) {
    const container = document.getElementById("ai-risk-slot");
    if (!container) return;
    container.innerHTML = '<div class="ai-loading"><div class="ai-spinner"></div>Assessing risk...</div>';
    try {
        const data = await api(`/api/ai/report-risk/${reportId}`);
        container.innerHTML = `
            <div class="ai-risk-card">
                <div class="ai-risk-header">
                    <span class="risk-dot risk-${data.risk_level}"></span>
                    <h3>AI Risk Assessment</h3>
                </div>
                <div class="ai-risk-text ai-content">${renderMd(data.assessment)}</div>
            </div>
        `;
    } catch (err) {
        container.innerHTML = "";
    }
}


// ── AI Suggestions (Issues page) ──

async function renderAISuggestions() {
    const container = document.getElementById("ai-suggestions-slot");
    if (!container) return;
    container.innerHTML = '<div class="ai-loading"><div class="ai-spinner"></div>Generating suggestions...</div>';
    try {
        const data = await api("/api/ai/suggestions");
        const priorityDot = (p) => `<span class="risk-dot risk-${p}"></span>`;
        container.innerHTML = `
            <div class="ai-suggestions-card">
                <div class="ai-suggestions-header">AI Suggestions</div>
                ${data.suggestions.map(s => `
                    <div class="ai-suggestion-item">
                        <div class="ai-suggestion-priority">${priorityDot(s.priority)}</div>
                        <div class="ai-suggestion-body">
                            <h4>${s.title}</h4>
                            <p>${s.description}</p>
                        </div>
                    </div>
                `).join("")}
            </div>
        `;
    } catch (err) {
        container.innerHTML = "";
    }
}


// ── Init ──

function getInitialPage() {
    // Support hash-based routing: /#sources, /#reports, etc.
    if (location.hash && location.hash.length > 1) {
        let page = location.hash.substring(1);
        if (pageAliases[page]) page = pageAliases[page];
        if (pages[page]) return page;
    }
    // Support path-based routing: /sources, /reports, etc.
    let path = location.pathname.replace(/^\/+/, "");
    if (pageAliases[path]) path = pageAliases[path];
    if (path && pages[path]) return path;
    return "dashboard";
}

function _isLocal() {
    return window._currentUser && window._currentUser.is_local;
}

function _showConnectedUser(user) {
    const el = document.getElementById("connected-user");
    if (!el) return;
    const title = [user.hostname, user.ip].filter(Boolean).join(" - ");
    el.title = title ? `Connected from ${title}` : "";
    el.innerHTML = `<span class="user-name">${esc(user.name)}</span>`;
}

function _applyAccessUi(user) {
    const refreshLink = document.getElementById("nav-refresh-schedule");
    const premiumLink = document.getElementById("nav-premium-viewers");
    const updateBtn = document.getElementById("btn-update-app");
    if (refreshLink) refreshLink.style.display = "";
    if (premiumLink) premiumLink.style.display = "";
    if (updateBtn) updateBtn.style.display = "";
    if (currentPage === "premiumviewers" || currentPage === "refreshschedule") {
        navigate(currentPage);
    }
}

function _showRegistrationModal(ip) {
    const overlay = document.createElement("div");
    overlay.className = "register-overlay";
    overlay.innerHTML = `
        <div class="register-modal">
            <h2>Welcome to Metronome</h2>
            <p>Enter your name to get started. This will be remembered for this browser and IP address (${esc(ip)}) so the system knows who you are.</p>
            <input type="text" id="register-name" placeholder="Your name" autocomplete="off" autofocus>
            <button id="register-submit">Continue</button>
        </div>
    `;
    document.body.appendChild(overlay);

    const input = document.getElementById("register-name");
    const btn = document.getElementById("register-submit");

    function doRegister() {
        const name = input.value.trim();
        if (!name) { input.focus(); return; }
        btn.disabled = true;
        btn.textContent = "Saving...";
        fetch("/api/register", {
            method: "POST",
            headers: apiHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ name, client_key: _getClientKey() }),
        })
        .then(async r => {
            const payload = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(payload.detail || `API error: ${r.status}`);
            return payload;
        })
        .then(me => {
            window._currentUser = me;
            _showConnectedUser(me);
            _applyAccessUi(me);
            overlay.remove();
        })
        .catch((err) => {
            btn.disabled = false;
            btn.textContent = "Continue";
            toast("Registration failed: " + err.message);
        });
    }

    btn.addEventListener("click", doRegister);
    input.addEventListener("keydown", e => { if (e.key === "Enter") doRegister(); });
    setTimeout(() => input.focus(), 50);
}

function updateThemeIcon() {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    const html = document.documentElement;
    const isDark = html.classList.contains("dark") ||
        (!html.classList.contains("light") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    btn.innerHTML = isDark ? "&#9788;" : "&#9790;";
    btn.title = isDark ? "Switch to light mode" : "Switch to dark mode";
}

document.addEventListener("DOMContentLoaded", () => {
    bindEmailProfileSchedules();

    $$("nav a[data-page]").forEach(a => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            navigate(a.dataset.page);
        });
    });

    // Keyboard navigation for nav dropdown groups
    $$("nav .nav-group-label[tabindex]").forEach(label => {
        label.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                const dropdown = label.parentElement.querySelector(".nav-dropdown");
                if (dropdown) {
                    const firstLink = dropdown.querySelector("a");
                    if (firstLink) firstLink.focus();
                }
            }
        });
    });

    // Arrow key navigation inside dropdowns
    $$("nav .nav-dropdown").forEach(dropdown => {
        dropdown.addEventListener("keydown", (e) => {
            const links = Array.from(dropdown.querySelectorAll("a"));
            const idx = links.indexOf(document.activeElement);
            if (e.key === "ArrowDown") {
                e.preventDefault();
                const next = idx < links.length - 1 ? idx + 1 : 0;
                links[next].focus();
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                const prev = idx > 0 ? idx - 1 : links.length - 1;
                links[prev].focus();
            } else if (e.key === "Escape") {
                const label = dropdown.parentElement.querySelector(".nav-group-label");
                if (label) label.focus();
            }
        });
    });

    window.addEventListener("hashchange", () => {
        if (window._skipHash) { window._skipHash = false; return; }
        let page = location.hash.length > 1 ? location.hash.substring(1) : "dashboard";
        if (pageAliases[page]) page = pageAliases[page];
        if (pages[page] && page !== currentPage) navigate(page);
    });

    // ── Identity check: fetch /api/me, show registration if needed ──
    window._currentUser = null;
    fetch("/api/me", { headers: apiHeaders() }).then(r => r.json()).then(me => {
        if (me.name) {
            window._currentUser = me;
            _showConnectedUser(me);
        } else {
            _showRegistrationModal(me.ip);
        }
        _applyAccessUi(me);
    }).catch(() => {});

    initAIChatPanel();
    navigate(getInitialPage());

    // Show version in nav
    api("/api/version").then(v => {
        const el = document.getElementById("app-version");
        if (el && v.version) el.textContent = "#" + v.version;
    }).catch(() => {});

    // Update App button
    const updateBtn = document.getElementById("btn-update-app");
    if (updateBtn) {
        updateBtn.addEventListener("click", async (e) => {
            e.preventDefault();
            if (!confirm("This will download the latest version and restart the service. Continue?")) return;
            try {
                await apiPost("/api/update");
                window.close();
                // window.close() may be blocked by the browser - show fallback
                document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:inherit;color:var(--text)"><div style="text-align:center"><h2>Updating...</h2><p style="color:var(--text-muted);margin-top:0.5rem">Setup is running. You can close this tab.</p></div></div>';
            } catch (err) {
                toast("Update failed: " + err.message);
            }
        });
    }

    // Theme toggle
    const themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
        // Restore saved preference
        const saved = localStorage.getItem("dg-theme");
        if (saved === "dark") document.documentElement.classList.add("dark");
        else if (saved === "light") document.documentElement.classList.add("light");
        updateThemeIcon();

        themeToggle.addEventListener("click", () => {
            const html = document.documentElement;
            if (html.classList.contains("dark")) {
                html.classList.remove("dark");
                html.classList.add("light");
                localStorage.setItem("dg-theme", "light");
            } else if (html.classList.contains("light")) {
                html.classList.remove("light");
                html.classList.add("dark");
                localStorage.setItem("dg-theme", "dark");
            } else {
                // Auto mode - toggle to opposite of system preference
                const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                if (prefersDark) {
                    html.classList.add("light");
                    localStorage.setItem("dg-theme", "light");
                } else {
                    html.classList.add("dark");
                    localStorage.setItem("dg-theme", "dark");
                }
            }
            updateThemeIcon();
        });
    }
});
