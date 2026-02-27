// ── Helpers ──

async function api(path) {
    const res = await fetch(path);
    return res.json();
}

async function apiPost(path) {
    const res = await fetch(path, { method: "POST" });
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
    return `<span class="badge ${colors[type] || "badge-muted"}">${type}</span>`;
}

function timeAgo(dateStr) {
    if (!dateStr) return "never";
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now - d;
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
}

function toast(msg) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}


// ── DataTable (sortable + filterable) ──

/**
 * Renders a sortable, filterable table.
 *
 * @param {string} tableId - Unique id for the table element
 * @param {Array<{key: string, label: string, render?: function, sortVal?: function}>} columns
 *   - key: property name on row object (also used for filtering)
 *   - label: column header text
 *   - render(row): returns HTML string for the cell (defaults to row[key])
 *   - sortVal(row): returns a sortable primitive (defaults to row[key])
 * @param {Array<Object>} rows - data array
 * @returns {string} HTML string
 */
function dataTable(tableId, columns, rows) {
    // Store data on a global so post-render can bind events
    window._dt = window._dt || {};
    window._dt[tableId] = { columns, rows, sortCol: null, sortDir: "asc", filters: {} };

    return _renderDT(tableId);
}

function _renderDT(tableId) {
    const dt = window._dt[tableId];
    const { columns, sortCol, sortDir, filters } = dt;
    let rows = dt.rows;

    // Filter
    rows = rows.filter(r => {
        for (const col of columns) {
            const f = (filters[col.key] || "").toLowerCase();
            if (!f) continue;
            const val = String(col.sortVal ? col.sortVal(r) : (r[col.key] ?? "")).toLowerCase();
            if (!val.includes(f)) return false;
        }
        return true;
    });

    // Sort
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

    const bodyRows = rows.map(r =>
        `<tr>${columns.map(c => `<td>${c.render ? c.render(r) : (r[c.key] ?? "-")}</td>`).join("")}</tr>`
    ).join("");

    return `
        <table id="${tableId}">
            <thead>
                <tr>${headerCells}</tr>
                <tr class="filter-row">${filterCells}</tr>
            </thead>
            <tbody>${bodyRows}</tbody>
        </table>
        <div class="table-count">${rows.length} of ${dt.rows.length} rows</div>
    `;
}

function bindDataTables() {
    // Sort click
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

    // Filter input
    document.querySelectorAll("tr.filter-row input[data-dt]").forEach(inp => {
        inp.addEventListener("input", () => {
            const id = inp.dataset.dt;
            const col = inp.dataset.fcol;
            window._dt[id].filters[col] = inp.value;
            _refreshDT(id);
        });
    });
}

function _refreshDT(tableId) {
    const html = _renderDT(tableId);
    const old = document.getElementById(tableId);
    if (!old) return;
    // Replace the table and the count div after it
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    const parent = old.parentNode;
    const countDiv = old.nextElementSibling;
    parent.replaceChild(wrapper.querySelector("table"), old);
    if (countDiv && countDiv.classList.contains("table-count")) {
        parent.replaceChild(wrapper.querySelector(".table-count"), countDiv);
    }
    bindDataTables();
}


// ── Pages ──

async function renderDashboard() {
    const data = await api("/api/dashboard");
    const scan = data.last_scan;

    return `
        <h1>Dashboard</h1>
        <div class="cards">
            <div class="card">
                <div class="label">Sources</div>
                <div class="value">${data.sources_total}</div>
                <div class="breakdown">
                    <span style="color:var(--green)">${data.sources_fresh} fresh</span> &middot;
                    <span style="color:var(--yellow)">${data.sources_stale} stale</span> &middot;
                    <span style="color:var(--red)">${data.sources_error} error</span>
                    ${data.sources_unknown ? ` &middot; <span style="color:var(--text-muted)">${data.sources_unknown} unknown</span>` : ""}
                </div>
            </div>
            <div class="card">
                <div class="label">Reports</div>
                <div class="value">${data.reports_total}</div>
            </div>
            <div class="card">
                <div class="label">Active Alerts</div>
                <div class="value" style="color:${data.alerts_active > 0 ? 'var(--red)' : 'var(--green)'}">${data.alerts_active}</div>
            </div>
            <div class="card">
                <div class="label">Last Scan</div>
                <div class="value" style="font-size:1rem">${scan ? timeAgo(scan.started_at) : "never"}</div>
                ${scan ? `<div class="breakdown">${scan.reports_scanned} reports, ${scan.sources_found} sources</div>` : ""}
            </div>
        </div>

        <div class="section" id="alerts-preview"></div>
    `;
}

async function renderDashboardAlerts() {
    const alerts = await api("/api/alerts?active_only=true");
    const container = $("#alerts-preview");
    if (!container) return;

    if (alerts.length === 0) {
        container.innerHTML = "<h2>Recent Alerts</h2><p style='color:var(--text-muted)'>No active alerts.</p>";
        return;
    }

    container.innerHTML = `
        <h2>Recent Alerts</h2>
        ${alerts.slice(0, 5).map(a => `
            <div class="alert-item">
                <div class="dot ${a.severity === 'critical' ? 'dot-red' : 'dot-yellow'}"></div>
                <span>${a.message}</span>
                <span style="margin-left:auto;color:var(--text-muted);font-size:0.75rem">${timeAgo(a.created_at)}</span>
            </div>
        `).join("")}
    `;
}

async function renderSources() {
    const sources = await api("/api/sources");

    const cols = [
        { key: "name", label: "Name", render: s => `<strong>${s.name}</strong>` },
        { key: "type", label: "Type", render: s => typeBadge(s.type) },
        { key: "connection_info", label: "Connection", render: s => `<span style="color:var(--text-muted);font-size:0.8rem;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">${s.connection_info || "-"}</span>` },
        { key: "status", label: "Status", render: s => statusBadge(s.status) },
        { key: "last_probe_at", label: "Last Probe", render: s => `<span style="color:var(--text-muted)">${timeAgo(s.last_probe_at)}</span>`, sortVal: s => s.last_probe_at || "" },
        { key: "report_count", label: "Reports", sortVal: s => s.report_count || 0 },
        { key: "owner", label: "Owner", render: s => `<span style="color:var(--text-muted)">${s.owner || "-"}</span>` },
    ];

    return `
        <h1>Sources</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">${sources.length} data sources tracked</p>
        ${dataTable("dt-sources", cols, sources)}
    `;
}

async function renderReports() {
    const reports = await api("/api/reports");

    const cols = [
        { key: "name", label: "Name", render: r => `<strong>${r.name}</strong>` },
        { key: "status", label: "Status", render: r => statusBadge(r.status) },
        { key: "source_count", label: "Sources", sortVal: r => r.source_count || 0 },
        { key: "owner", label: "Report Owner", render: r => `<span style="color:var(--text-muted)">${r.owner || "-"}</span>` },
        { key: "business_owner", label: "Business Owner", render: r => `<span style="color:var(--text-muted)">${r.business_owner || "-"}</span>` },
        { key: "frequency", label: "Frequency", render: r => `<span style="color:var(--text-muted)">${r.frequency || "-"}</span>` },
    ];

    return `
        <h1>Reports</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">${reports.length} Power BI reports tracked</p>
        ${dataTable("dt-reports", cols, reports)}
    `;
}

async function renderLineage() {
    const edges = await api("/api/lineage");

    if (edges.length === 0) {
        return `
            <h1>Lineage</h1>
            <p style="color:var(--text-muted)">No lineage data yet. Run a scan first.</p>
        `;
    }

    // Build a simple text-based lineage view (visual graph can be added later)
    const bySource = {};
    for (const e of edges) {
        if (!bySource[e.source_name]) {
            bySource[e.source_name] = { type: e.source_type, reports: [] };
        }
        bySource[e.source_name].reports.push(e.report_name);
    }

    const lineageRows = Object.entries(bySource).map(([name, info]) => ({
        source_name: name,
        type: info.type,
        reports_list: info.reports.join(", "),
        report_count: info.reports.length,
    }));

    const cols = [
        { key: "source_name", label: "Source", render: r => `<strong>${r.source_name}</strong>` },
        { key: "type", label: "Type", render: r => typeBadge(r.type) },
        { key: "reports_list", label: "Feeds Reports" },
        { key: "report_count", label: "# Reports", sortVal: r => r.report_count },
    ];

    return `
        <h1>Lineage</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">Source to report dependencies</p>
        ${dataTable("dt-lineage", cols, lineageRows)}
    `;
}

async function renderScanner() {
    const runs = await api("/api/scanner/runs");
    const lastRun = runs.length > 0 ? runs[0] : null;

    return `
        <h1>Scanner</h1>
        <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem">
            <button id="btn-scan">Run Scan Now</button>
            <span style="color:var(--text-muted);font-size:0.85rem">
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
                { key: "started_at", label: "When", render: r => timeAgo(r.started_at), sortVal: r => r.started_at || "" },
                { key: "status", label: "Status", render: r => statusBadge(r.status) },
                { key: "reports_scanned", label: "Reports", render: r => `${r.reports_scanned ?? "-"}`, sortVal: r => r.reports_scanned ?? 0 },
                { key: "sources_found", label: "Sources", render: r => `${r.sources_found ?? "-"}`, sortVal: r => r.sources_found ?? 0 },
                { key: "new_sources", label: "New", render: r => `${r.new_sources ?? "-"}`, sortVal: r => r.new_sources ?? 0 },
                { key: "changed_queries", label: "Changed", render: r => `${r.changed_queries ?? "-"}`, sortVal: r => r.changed_queries ?? 0 },
                { key: "broken_refs", label: "Broken", render: r => `${r.broken_refs ?? "-"}`, sortVal: r => r.broken_refs ?? 0 },
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
        { key: "created_at", label: "When", render: a => `<span style="color:var(--text-muted)">${timeAgo(a.created_at)}</span>`, sortVal: a => a.created_at || "" },
        { key: "acknowledged", label: "Status", render: a => a.acknowledged ? '<span class="badge badge-muted">ack</span>' : '<span class="badge badge-red">active</span>', sortVal: a => a.acknowledged ? 1 : 0 },
    ];

    return `
        <h1>Alerts</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">${alerts.filter(a => !a.acknowledged).length} active alerts</p>
        ${dataTable("dt-alerts", cols, alerts)}
    `;
}


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    sources: renderSources,
    reports: renderReports,
    lineage: renderLineage,
    scanner: renderScanner,
    alerts: renderAlerts,
};

let currentPage = "dashboard";

async function navigate(page) {
    currentPage = page;

    // Update nav active state
    $$("nav a").forEach(a => {
        a.classList.toggle("active", a.dataset.page === page);
    });

    // Render page
    const app = $("#app");
    app.innerHTML = '<div class="loading">Loading...</div>';

    try {
        const html = await pages[page]();
        app.innerHTML = html;

        // Post-render hooks
        bindDataTables();
        if (page === "dashboard") renderDashboardAlerts();
        if (page === "scanner") bindScanButton();
    } catch (err) {
        app.innerHTML = `<div class="loading" style="color:var(--red)">Error loading page: ${err.message}</div>`;
    }
}

function bindScanButton() {
    const btn = $("#btn-scan");
    if (!btn) return;
    btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Scanning...";
        try {
            const result = await apiPost("/api/scanner/run");
            toast(`Scan complete: ${result.reports_scanned} reports, ${result.sources_found} sources`);
            navigate("scanner");
        } catch (err) {
            toast("Scan failed: " + err.message);
            btn.disabled = false;
            btn.textContent = "Run Scan Now";
        }
    });
}


// ── Init ──

document.addEventListener("DOMContentLoaded", () => {
    // Nav click handlers
    $$("nav a[data-page]").forEach(a => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            navigate(a.dataset.page);
        });
    });

    // Initial page
    navigate("dashboard");
});
