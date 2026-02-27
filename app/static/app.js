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
    const colors = { csv: "badge-blue", excel: "badge-green", sql: "badge-yellow" };
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

    return `
        <h1>Sources</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">${sources.length} data sources tracked</p>
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Connection</th>
                    <th>Status</th>
                    <th>Last Probe</th>
                    <th>Reports</th>
                    <th>Owner</th>
                </tr>
            </thead>
            <tbody>
                ${sources.map(s => `
                    <tr>
                        <td><strong>${s.name}</strong></td>
                        <td>${typeBadge(s.type)}</td>
                        <td style="color:var(--text-muted);font-size:0.8rem;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.connection_info || "-"}</td>
                        <td>${statusBadge(s.status)}</td>
                        <td style="color:var(--text-muted)">${timeAgo(s.last_probe_at)}</td>
                        <td>${s.report_count}</td>
                        <td style="color:var(--text-muted)">${s.owner || "-"}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;
}

async function renderReports() {
    const reports = await api("/api/reports");

    return `
        <h1>Reports</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">${reports.length} Power BI reports tracked</p>
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Status</th>
                    <th>Sources</th>
                    <th>Report Owner</th>
                    <th>Business Owner</th>
                    <th>Frequency</th>
                </tr>
            </thead>
            <tbody>
                ${reports.map(r => `
                    <tr>
                        <td><strong>${r.name}</strong></td>
                        <td>${statusBadge(r.status)}</td>
                        <td>${r.source_count}</td>
                        <td style="color:var(--text-muted)">${r.owner || "-"}</td>
                        <td style="color:var(--text-muted)">${r.business_owner || "-"}</td>
                        <td style="color:var(--text-muted)">${r.frequency || "-"}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
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

    return `
        <h1>Lineage</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">Source to report dependencies</p>
        <table>
            <thead>
                <tr>
                    <th>Source</th>
                    <th>Type</th>
                    <th>Feeds Reports</th>
                </tr>
            </thead>
            <tbody>
                ${Object.entries(bySource).map(([name, info]) => `
                    <tr>
                        <td><strong>${name}</strong></td>
                        <td>${typeBadge(info.type)}</td>
                        <td>${info.reports.join(", ")}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
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
            <table>
                <thead>
                    <tr>
                        <th>When</th>
                        <th>Status</th>
                        <th>Reports</th>
                        <th>Sources</th>
                        <th>New</th>
                        <th>Changed</th>
                        <th>Broken</th>
                    </tr>
                </thead>
                <tbody>
                    ${runs.map(r => `
                        <tr>
                            <td>${timeAgo(r.started_at)}</td>
                            <td>${statusBadge(r.status)}</td>
                            <td>${r.reports_scanned ?? "-"}</td>
                            <td>${r.sources_found ?? "-"}</td>
                            <td>${r.new_sources ?? "-"}</td>
                            <td>${r.changed_queries ?? "-"}</td>
                            <td>${r.broken_refs ?? "-"}</td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        </div>
    `;
}

async function renderAlerts() {
    const alerts = await api("/api/alerts?active_only=false");

    return `
        <h1>Alerts</h1>
        <p style="color:var(--text-muted);margin-bottom:1rem">${alerts.filter(a => !a.acknowledged).length} active alerts</p>
        <table>
            <thead>
                <tr>
                    <th>Severity</th>
                    <th>Message</th>
                    <th>Source</th>
                    <th>When</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                ${alerts.map(a => `
                    <tr>
                        <td>${statusBadge(a.severity)}</td>
                        <td>${a.message}</td>
                        <td style="color:var(--text-muted)">${a.source_name || "-"}</td>
                        <td style="color:var(--text-muted)">${timeAgo(a.created_at)}</td>
                        <td>${a.acknowledged ? '<span class="badge badge-muted">ack</span>' : '<span class="badge badge-red">active</span>'}</td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
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
