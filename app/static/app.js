// ── Helpers ──

async function api(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

async function apiPost(path) {
    const res = await fetch(path, { method: "POST" });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function statusBadge(status) {
    if (!status) return '<span class="badge badge-muted">unknown</span>';
    const s = status.toLowerCase();
    if (s === "fresh" || s === "current" || s === "pass" || s === "completed")
        return `<span class="badge badge-green">${status}</span>`;
    if (s === "stale" || s === "stale sources" || s === "warn" || s === "warning")
        return `<span class="badge badge-yellow">${status}</span>`;
    if (s === "error" || s === "fail" || s === "failed" || s === "critical")
        return `<span class="badge badge-red">${status}</span>`;
    return `<span class="badge badge-muted">${status}</span>`;
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

function timeAgo(dateStr) {
    if (!dateStr) return "-";
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr; // return raw if unparseable
    const now = new Date();
    const diffMs = now - d;
    const mins = Math.floor(diffMs / 60000);
    if (mins < 0) return "just now";
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

function formatDate(dateStr) {
    if (!dateStr) return "-";
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
         + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function toast(msg) {
    const el = document.createElement("div");
    el.className = "toast";
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
        const dotIdx = afterSlash.indexOf(".");
        if (dotIdx >= 0) {
            return {
                shortName: afterSlash.substring(dotIdx + 1),
                folderSchema: afterSlash.substring(0, dotIdx),
                fullLocation: name
            };
        }
        return { shortName: afterSlash, folderSchema: "-", fullLocation: name };
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

function dataTable(tableId, columns, rows, opts) {
    window._dt = window._dt || {};
    window._dt[tableId] = { columns, rows, sortCol: null, sortDir: "asc", filters: {}, opts: opts || {} };
    return _renderDT(tableId);
}

function _renderDT(tableId) {
    const dt = window._dt[tableId];
    const { columns, sortCol, sortDir, filters } = dt;
    let rows = dt.rows;

    rows = rows.filter(r => {
        for (const col of columns) {
            const f = (filters[col.key] || "").toLowerCase();
            if (!f) continue;
            const val = String(col.sortVal ? col.sortVal(r) : (r[col.key] ?? "")).toLowerCase();
            if (!val.includes(f)) return false;
        }
        return true;
    });

    if (sortCol) {
        const col = columns.find(c => c.key === sortCol);
        rows = [...rows].sort((a, b) => {
            let va = col && col.sortVal ? col.sortVal(a) : (a[sortCol] ?? "");
            let vb = col && col.sortVal ? col.sortVal(b) : (b[sortCol] ?? "");
            if (typeof va === "string") va = va.toLowerCase();
            if (typeof vb === "string") vb = vb.toLowerCase();
            if (va < vb) return sortDir === "asc" ? -1 : 1;
            if (va > vb) return sortDir === "asc" ? 1 : -1;
            return 0;
        });
    }

    const arrow = (key) => {
        if (sortCol !== key) return '<span class="sort-arrow">&#9650;</span>';
        return sortDir === "asc"
            ? '<span class="sort-arrow">&#9650;</span>'
            : '<span class="sort-arrow">&#9660;</span>';
    };

    const headerCells = columns.map(c =>
        `<th class="sortable ${sortCol === c.key ? 'sort-' + sortDir : ''}" data-dt="${tableId}" data-col="${c.key}">${c.label}${arrow(c.key)}</th>`
    ).join("");

    const filterCells = columns.map(c =>
        `<th><input type="text" data-dt="${tableId}" data-fcol="${c.key}" placeholder="Filter..." value="${filters[c.key] || ""}"></th>`
    ).join("");

    const clickable = dt.opts && dt.opts.onRowClick ? ' data-clickable="1"' : '';
    const bodyRows = rows.map((r, i) =>
        `<tr data-dt="${tableId}" data-row-idx="${i}"${clickable}>${columns.map(c => `<td>${c.render ? c.render(r) : (r[c.key] ?? "-")}</td>`).join("")}</tr>`
    ).join("");

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
        th.addEventListener("click", () => {
            const id = th.dataset.dt;
            const col = th.dataset.col;
            const dt = window._dt[id];
            if (dt.sortCol === col) {
                dt.sortDir = dt.sortDir === "asc" ? "desc" : "asc";
            } else {
                dt.sortCol = col;
                dt.sortDir = "asc";
            }
            _refreshDT(id);
        });
    });

    document.querySelectorAll("tr.filter-row input[data-dt]").forEach(inp => {
        inp.addEventListener("input", () => {
            const id = inp.dataset.dt;
            const col = inp.dataset.fcol;
            window._dt[id].filters[col] = inp.value;
            _refreshDT(id);
        });
    });

    document.querySelectorAll("tr[data-clickable]").forEach(tr => {
        tr.addEventListener("click", () => {
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
}

function _refreshDT(tableId) {
    const html = _renderDT(tableId);
    const oldWrapper = document.getElementById(tableId)?.closest(".table-wrapper");
    if (!oldWrapper) return;
    const temp = document.createElement("div");
    temp.innerHTML = html;
    const parent = oldWrapper.parentNode;
    const countDiv = oldWrapper.nextElementSibling;
    parent.replaceChild(temp.querySelector(".table-wrapper"), oldWrapper);
    if (countDiv && countDiv.classList.contains("table-count")) {
        parent.replaceChild(temp.querySelector(".table-count"), countDiv);
    }
    bindDataTables();
}


// ── Source detail panel ──

async function showSourceDetail(source) {
    const existing = $("#source-detail");
    if (existing) existing.remove();

    const reports = await api(`/api/sources/${source.id}/reports`);
    const parsed = parseSourceName(source);

    const panel = document.createElement("div");
    panel.id = "source-detail";
    panel.className = "source-detail-panel";

    const reportRows = reports.length > 0
        ? reports.map(r => `
            <tr>
                <td><strong>${r.name}</strong></td>
                <td style="color:var(--text-muted)">${r.table_name || "-"}</td>
                <td style="color:var(--text-muted)">${r.owner || "-"}</td>
            </tr>
        `).join("")
        : '<tr><td colspan="3" class="empty-state" style="border:none">No reports linked to this source</td></tr>';

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${parsed.shortName}</h2>
            <button class="btn-outline" id="btn-close-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Type</div>${typeBadge(source.type)}</div>
            <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(source.status)}</div>
            <div class="detail-item"><div class="detail-label">Last Updated</div><span style="color:var(--text)">${source.last_updated ? formatDate(source.last_updated) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Schema</div><span style="color:var(--text)">${parsed.folderSchema}</span></div>
            <div class="detail-item"><div class="detail-label">Full Location</div><span style="color:var(--text-muted);word-break:break-all;font-size:0.78rem">${parsed.fullLocation}</span></div>
            <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${source.owner || "-"}</span></div>
        </div>

        <h2>Reports using this source (${reports.length})</h2>
        <table class="detail-table">
            <thead><tr><th>Report</th><th>Table Name</th><th>Owner</th></tr></thead>
            <tbody>${reportRows}</tbody>
        </table>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#btn-close-detail").addEventListener("click", () => panel.remove());
}


// ── Report detail panel ──

async function showReportDetail(report) {
    const existing = $("#report-detail");
    if (existing) existing.remove();

    const tables = await api(`/api/reports/${report.id}/tables`);

    const panel = document.createElement("div");
    panel.id = "report-detail";
    panel.className = "source-detail-panel";

    const tableRows = tables.length > 0
        ? tables.map(t => `
            <tr>
                <td><strong>${t.table_name}</strong></td>
                <td>${t.source_name ? typeBadge(t.source_name.includes('/') ? 'postgresql' : 'file') : ''} <span style="color:var(--text-muted)">${t.source_name || "no linked source"}</span></td>
            </tr>
        `).join("")
        : '<tr><td colspan="2" class="empty-state" style="border:none">No tables found</td></tr>';

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${report.name}</h2>
            <button class="btn-outline" id="btn-close-report-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(report.status)}</div>
            <div class="detail-item"><div class="detail-label">Sources</div><span style="color:var(--text)">${report.source_count}</span></div>
            <div class="detail-item"><div class="detail-label">Report Owner</div><span style="color:var(--text)">${report.owner || "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Business Owner</div><span style="color:var(--text)">${report.business_owner || "-"}</span></div>
        </div>

        <h2>Data tables in this report (${tables.length})</h2>
        <table class="detail-table">
            <thead><tr><th>Table</th><th>Source</th></tr></thead>
            <tbody>${tableRows}</tbody>
        </table>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#btn-close-report-detail").addEventListener("click", () => panel.remove());
}


// ── Pages ──

async function renderDashboard() {
    const [data, sources, reports] = await Promise.all([
        api("/api/dashboard"),
        api("/api/sources"),
        api("/api/reports"),
    ]);
    const scan = data.last_scan;

    const total = data.sources_total || 1;
    const freshPct = pct(data.sources_fresh, total);
    const stalePct = pct(data.sources_stale, total);
    const errorPct = pct(data.sources_error, total);
    const unknownPct = 100 - freshPct - stalePct - errorPct;

    // Find problematic sources/reports
    const problemSources = sources.filter(s => s.status === "stale" || s.status === "error");
    const problemReports = reports.filter(r => r.status === "error" || r.status === "stale sources");

    return `
        <div class="page-header">
            <h1>Data Governance Dashboard</h1>
            <span class="subtitle">Real-time health monitoring for your BI ecosystem</span>
        </div>

        <div class="stat-grid">
            <div class="stat-card card-blue">
                <div class="stat-label">Total Sources</div>
                <div class="stat-value">${data.sources_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green">${data.sources_fresh} fresh</span>
                    <span class="stat-dot dot-yellow">${data.sources_stale} stale</span>
                    <span class="stat-dot dot-red">${data.sources_error} outdated</span>
                    ${data.sources_unknown ? `<span class="stat-dot dot-muted">${data.sources_unknown} unknown</span>` : ""}
                </div>
            </div>
            <div class="stat-card card-purple">
                <div class="stat-label">Reports</div>
                <div class="stat-value">${data.reports_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green">${reports.filter(r => r.status === "current").length} healthy</span>
                    <span class="stat-dot dot-yellow">${reports.filter(r => r.status === "stale sources").length} at risk</span>
                    <span class="stat-dot dot-red">${reports.filter(r => r.status === "error").length} degraded</span>
                </div>
            </div>
            <div class="stat-card ${data.alerts_active > 0 ? 'card-red' : 'card-green'}">
                <div class="stat-label">Active Alerts</div>
                <div class="stat-value">${data.alerts_active}</div>
            </div>
            <div class="stat-card card-green">
                <div class="stat-label">Last Scan</div>
                <div class="stat-value" style="font-size:1.1rem">${scan ? timeAgo(scan.started_at) : "never"}</div>
                ${scan ? `<div class="stat-breakdown">${scan.reports_scanned} reports &middot; ${scan.sources_found} sources</div>` : ""}
            </div>
        </div>

        <div class="health-bar-container">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <h2 style="margin-bottom:0">Source Health</h2>
                <span style="color:var(--text-dim);font-size:0.72rem">${freshPct}% fresh</span>
            </div>
            <div class="health-bar">
                <div class="segment segment-green" style="width:${freshPct}%"></div>
                <div class="segment segment-yellow" style="width:${stalePct}%"></div>
                <div class="segment segment-red" style="width:${errorPct}%"></div>
                <div class="segment segment-muted" style="width:${unknownPct}%"></div>
            </div>
        </div>

        <div class="section-grid">
            <div class="section">
                <h2>Attention Needed</h2>
                ${problemSources.length > 0 || problemReports.length > 0 ? `
                    <div class="alert-list">
                        ${problemSources.map(s => {
                            const parsed = parseSourceName(s);
                            return `<div class="alert-item">
                                <div class="dot ${s.status === 'error' ? 'dot-red' : 'dot-yellow'}"></div>
                                <span><strong>${parsed.shortName}</strong> &mdash; ${s.status === 'error' ? 'data older than 90 days' : 'data is 31-90 days old'}</span>
                                <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${s.last_updated ? timeAgo(s.last_updated) : ""}</span>
                            </div>`;
                        }).join("")}
                        ${problemReports.map(r => `
                            <div class="alert-item">
                                <div class="dot ${r.status === 'error' ? 'dot-red' : 'dot-yellow'}"></div>
                                <span><strong>${r.name}</strong> &mdash; has ${r.status === 'error' ? 'outdated' : 'stale'} sources</span>
                            </div>
                        `).join("")}
                    </div>
                ` : '<div class="empty-state">All sources and reports are healthy</div>'}
            </div>
            <div class="section" id="alerts-preview"></div>
        </div>
    `;
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
            ${alerts.slice(0, 8).map(a => `
                <div class="alert-item">
                    <div class="dot ${a.severity === 'critical' ? 'dot-red' : 'dot-yellow'}"></div>
                    <span>${a.message}</span>
                    <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${timeAgo(a.created_at)}</span>
                </div>
            `).join("")}
        </div>
    `;
}

async function renderSources() {
    const sources = await api("/api/sources");

    sources.forEach(s => {
        const parsed = parseSourceName(s);
        s._shortName = parsed.shortName;
        s._folderSchema = parsed.folderSchema;
        s._fullLocation = parsed.fullLocation;
    });

    const cols = [
        { key: "_shortName", label: "File / Table", render: s => `<strong>${s._shortName}</strong>`, sortVal: s => s._shortName || "" },
        { key: "_folderSchema", label: "Folder / Schema", render: s => `<span style="color:var(--text-muted)">${s._folderSchema || "-"}</span>`, sortVal: s => s._folderSchema || "" },
        { key: "_fullLocation", label: "Full Location", render: s => `<span class="cell-expandable" title="${(s._fullLocation || '').replace(/"/g, '&quot;')}">${s._fullLocation || "-"}</span>`, sortVal: s => s._fullLocation || "" },
        { key: "type", label: "Type", render: s => typeBadge(s.type) },
        { key: "status", label: "Status", render: s => statusBadge(s.status) },
        { key: "last_updated", label: "Last Updated", render: s => `<span style="color:var(--text-muted)" title="${s.last_updated || ''}">${s.last_updated ? timeAgo(s.last_updated) : "-"}</span>`, sortVal: s => s.last_updated || "" },
        { key: "report_count", label: "Reports", sortVal: s => s.report_count || 0 },
        { key: "owner", label: "Owner", render: s => `<span style="color:var(--text-muted)">${s.owner || "-"}</span>` },
    ];

    const fresh = sources.filter(s => s.status === "fresh").length;
    const stale = sources.filter(s => s.status === "stale").length;
    const err = sources.filter(s => s.status === "error").length;

    return `
        <div class="page-header">
            <h1>Sources</h1>
            <span class="subtitle">${sources.length} data sources tracked &mdash; ${fresh} fresh, ${stale} stale, ${err} outdated</span>
        </div>
        ${dataTable("dt-sources", cols, sources, { onRowClick: showSourceDetail })}
    `;
}

async function renderReports() {
    const reports = await api("/api/reports");

    const cols = [
        { key: "name", label: "Report", render: r => `<strong>${r.name}</strong>` },
        { key: "status", label: "Status", render: r => statusBadge(r.status) },
        { key: "source_count", label: "Sources", sortVal: r => r.source_count || 0 },
        { key: "owner", label: "Report Owner", render: r => `<span style="color:var(--text-muted)">${r.owner || "-"}</span>` },
        { key: "business_owner", label: "Business Owner", render: r => `<span style="color:var(--text-muted)">${r.business_owner || "-"}</span>` },
        { key: "frequency", label: "Frequency", render: r => `<span style="color:var(--text-muted)">${r.frequency || "-"}</span>` },
    ];

    const healthy = reports.filter(r => r.status === "current").length;
    const atRisk = reports.filter(r => r.status !== "current" && r.status !== "unknown").length;

    return `
        <div class="page-header">
            <h1>Reports</h1>
            <span class="subtitle">${reports.length} Power BI reports &mdash; ${healthy} healthy, ${atRisk} need attention</span>
        </div>
        ${dataTable("dt-reports", cols, reports, { onRowClick: showReportDetail })}
    `;
}

async function renderScanner() {
    const runs = await api("/api/scanner/runs");
    const lastRun = runs.length > 0 ? runs[0] : null;

    return `
        <div class="page-header">
            <h1>Scanner</h1>
            <span class="subtitle">Scan Power BI reports to detect sources and track changes</span>
        </div>

        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1.25rem">
            <button id="btn-scan">Run Scan Now</button>
            <button id="btn-probe" class="btn-outline">Probe Sources</button>
            <span style="color:var(--text-dim);font-size:0.78rem">
                ${lastRun ? `Last scan: ${timeAgo(lastRun.started_at)} (${lastRun.status})` : "No scans yet"}
            </span>
        </div>

        ${lastRun && lastRun.log ? `
            <div class="section">
                <h2>Last Scan Log</h2>
                <div class="scan-log">${lastRun.log}</div>
            </div>
        ` : ""}

        <div class="section">
            <h2>Scan History</h2>
            ${dataTable("dt-scans", [
                { key: "started_at", label: "When", render: r => `<span title="${formatDate(r.started_at)}">${timeAgo(r.started_at)}</span>`, sortVal: r => r.started_at || "" },
                { key: "status", label: "Status", render: r => statusBadge(r.status) },
                { key: "reports_scanned", label: "Reports", render: r => `${r.reports_scanned ?? "-"}`, sortVal: r => r.reports_scanned ?? 0 },
                { key: "sources_found", label: "Sources", render: r => `${r.sources_found ?? "-"}`, sortVal: r => r.sources_found ?? 0 },
                { key: "new_sources", label: "New", render: r => r.new_sources ? `<span style="color:var(--green)">+${r.new_sources}</span>` : '-', sortVal: r => r.new_sources ?? 0 },
                { key: "changed_queries", label: "Changed", render: r => r.changed_queries ? `<span style="color:var(--yellow)">${r.changed_queries}</span>` : '-', sortVal: r => r.changed_queries ?? 0 },
                { key: "broken_refs", label: "Broken", render: r => r.broken_refs ? `<span style="color:var(--red)">${r.broken_refs}</span>` : '-', sortVal: r => r.broken_refs ?? 0 },
            ], runs)}
        </div>
    `;
}

async function renderAlerts() {
    const alerts = await api("/api/alerts?active_only=false");

    const cols = [
        { key: "severity", label: "Severity", render: a => statusBadge(a.severity) },
        { key: "message", label: "Message" },
        { key: "source_name", label: "Source", render: a => `<span style="color:var(--text-muted)">${a.source_name || "-"}</span>` },
        { key: "created_at", label: "When", render: a => `<span style="color:var(--text-muted)" title="${formatDate(a.created_at)}">${timeAgo(a.created_at)}</span>`, sortVal: a => a.created_at || "" },
        { key: "acknowledged", label: "Status", render: a => a.acknowledged ? '<span class="badge badge-muted">ack</span>' : '<span class="badge badge-red">active</span>', sortVal: a => a.acknowledged ? 1 : 0 },
    ];

    const active = alerts.filter(a => !a.acknowledged).length;

    return `
        <div class="page-header">
            <h1>Alerts</h1>
            <span class="subtitle">${active} active alert${active !== 1 ? 's' : ''}</span>
        </div>
        ${dataTable("dt-alerts", cols, alerts)}
    `;
}


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    sources: renderSources,
    reports: renderReports,
    scanner: renderScanner,
    alerts: renderAlerts,
};

let currentPage = "dashboard";

async function navigate(page) {
    currentPage = page;

    $$("nav a").forEach(a => {
        a.classList.toggle("active", a.dataset.page === page);
    });

    const app = $("#app");
    app.innerHTML = '<div class="loading">Loading...</div>';

    try {
        const html = await pages[page]();
        app.innerHTML = html;

        bindDataTables();
        if (page === "dashboard") renderDashboardAlerts();
        if (page === "scanner") bindScannerButtons();
    } catch (err) {
        app.innerHTML = `<div class="loading" style="color:var(--red)">Error: ${err.message}</div>`;
    }
}

function bindScannerButtons() {
    const btnScan = $("#btn-scan");
    if (btnScan) {
        btnScan.addEventListener("click", async () => {
            btnScan.disabled = true;
            btnScan.textContent = "Scanning...";
            try {
                const result = await apiPost("/api/scanner/run");
                toast(`Scan complete: ${result.reports_scanned} reports, ${result.sources_found} sources`);
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
                toast(`Probe complete: ${result.matched} matched, ${result.skipped} skipped`);
                btnProbe.disabled = false;
                btnProbe.textContent = "Probe Sources";
            } catch (err) {
                toast("Probe failed: " + err.message);
                btnProbe.disabled = false;
                btnProbe.textContent = "Probe Sources";
            }
        });
    }
}


// ── Init ──

document.addEventListener("DOMContentLoaded", () => {
    $$("nav a[data-page]").forEach(a => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            navigate(a.dataset.page);
        });
    });

    navigate("dashboard");
});
