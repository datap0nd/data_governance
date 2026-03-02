// ── Simple markdown renderer ──

function renderMd(text) {
    if (!text) return "";
    let html = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        // headers
        .replace(/^### (.+)$/gm, "<h4>$1</h4>")
        .replace(/^## (.+)$/gm, "<h3 style='font-size:0.88rem;margin:0.4rem 0 0.25rem;color:#fff'>$1</h3>")
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

async function apiPatch(path, body) {
    const res = await fetch(path, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function statusBadge(status) {
    if (!status) return '<span class="badge badge-muted">not probed</span>';
    const s = status.toLowerCase();
    if (s === "fresh" || s === "current" || s === "pass" || s === "completed")
        return `<span class="badge badge-green">${status}</span>`;
    if (s === "stale" || s === "stale sources" || s === "warn" || s === "warning")
        return `<span class="badge badge-yellow">${s === "stale sources" ? "at risk" : status}</span>`;
    if (s === "outdated" || s === "outdated sources")
        return `<span class="badge badge-red">${s === "outdated sources" ? "degraded" : status}</span>`;
    if (s === "error" || s === "fail" || s === "failed" || s === "critical")
        return `<span class="badge badge-red">${status}</span>`;
    if (s === "no_connection")
        return '<span class="badge badge-muted">no connection</span>';
    if (s === "unknown")
        return '<span class="badge badge-muted">not probed</span>';
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

function actionTypeBadge(type) {
    const labels = {
        stale_source: "Stale",
        outdated_source: "Outdated",
        error_source: "Outdated",
        broken_ref: "Broken Ref",
        changed_query: "Query Changed",
    };
    const colors = {
        stale_source: "badge-yellow",
        outdated_source: "badge-red",
        error_source: "badge-red",
        broken_ref: "badge-red",
        changed_query: "badge-blue",
    };
    return `<span class="badge ${colors[type] || "badge-muted"}">${labels[type] || type}</span>`;
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

    const headerCells = columns.map(c => {
        const resizable = c.resizable ? ' resizable' : '';
        const resizer = c.resizable ? '<div class="col-resizer"></div>' : '';
        return `<th class="sortable${resizable} ${sortCol === c.key ? 'sort-' + sortDir : ''}" data-dt="${tableId}" data-col="${c.key}">${c.label}${arrow(c.key)}${resizer}</th>`;
    }).join("");

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
        th.addEventListener("click", (e) => {
            // Don't sort when clicking resizer
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

    // Column resizers
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
                const newWidth = Math.max(60, startWidth + (e.pageX - startX));
                th.style.width = newWidth + "px";
                th.style.minWidth = newWidth + "px";
                // Also set width on filter row th and all body cells in this column
                const filterTh = table.querySelector("tr.filter-row")?.children[colIdx];
                if (filterTh) {
                    filterTh.style.width = newWidth + "px";
                    filterTh.style.minWidth = newWidth + "px";
                }
                // Update expandable cells max-width in this column
                table.querySelectorAll("tbody tr").forEach(row => {
                    const cell = row.children[colIdx];
                    if (cell) {
                        const expandable = cell.querySelector(".cell-expandable");
                        if (expandable) {
                            expandable.style.maxWidth = (newWidth - 20) + "px";
                        }
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

function _refreshDT(tableId) {
    const dt = window._dt[tableId];
    const table = document.getElementById(tableId);
    if (!table) return;

    // Filter and sort rows
    const { columns, sortCol, sortDir, filters } = dt;
    let rows = dt.rows.filter(r => {
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
    dt._displayRows = rows;

    // Only replace tbody (preserves thead/filter inputs/focus)
    const clickable = dt.opts && dt.opts.onRowClick ? ' data-clickable="1"' : '';
    const bodyHTML = rows.map((r, i) =>
        `<tr data-dt="${tableId}" data-row-idx="${i}"${clickable}>${columns.map(c => `<td>${c.render ? c.render(r) : (r[c.key] ?? "-")}</td>`).join("")}</tr>`
    ).join("") || `<tr><td colspan="${columns.length}" style="text-align:center;color:var(--text-dim);padding:2rem">No data</td></tr>`;

    const tbody = table.querySelector("tbody");
    if (tbody) tbody.innerHTML = bodyHTML;

    // Update sort arrows in header (without replacing elements)
    table.querySelectorAll("thead tr:first-child th.sortable").forEach(th => {
        const col = th.dataset.col;
        th.className = `sortable${columns.find(c => c.key === col)?.resizable ? ' resizable' : ''} ${sortCol === col ? 'sort-' + sortDir : ''}`;
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
        tr.addEventListener("click", () => {
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

        <div id="ai-report-risk-slot"></div>

        <h2>Data tables in this report (${tables.length})</h2>
        <table class="detail-table">
            <thead><tr><th>Table</th><th>Source</th></tr></thead>
            <tbody>${tableRows}</tbody>
        </table>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#btn-close-report-detail").addEventListener("click", () => panel.remove());

    // Load AI risk assessment
    const riskSlot = document.getElementById("ai-report-risk-slot");
    if (riskSlot) {
        riskSlot.innerHTML = '<div class="ai-loading"><div class="ai-spinner"></div>Assessing risk...</div>';
        try {
            const data = await api(`/api/ai/report-risk/${report.id}`);
            riskSlot.innerHTML = `
                <div class="ai-risk-card">
                    <div class="ai-risk-header">
                        <span class="risk-dot risk-${data.risk_level}"></span>
                        <h3>AI Risk Assessment</h3>
                    </div>
                    <div class="ai-risk-text ai-content">${renderMd(data.assessment)}</div>
                    <div style="margin-top:0.75rem">
                        <button class="btn-outline" id="btn-audit-queries" style="border-color:var(--purple-bg);color:var(--purple);font-size:0.75rem">&#10024; Audit Queries</button>
                    </div>
                    <div id="ai-audit-result"></div>
                </div>
            `;
            document.getElementById("btn-audit-queries").addEventListener("click", async () => {
                const btn = document.getElementById("btn-audit-queries");
                const resultDiv = document.getElementById("ai-audit-result");
                btn.disabled = true;
                btn.textContent = "Auditing...";
                resultDiv.innerHTML = '<div class="ai-loading"><div class="ai-spinner"></div>Auditing M expressions...</div>';
                try {
                    const audit = await apiPost(`/api/ai/audit/${report.id}`);
                    const sevDot = (s) => '<span class="risk-dot risk-' + s + '"></span>';
                    resultDiv.innerHTML = '<div style="margin-top:0.75rem;padding-top:0.75rem;border-top:1px solid var(--border)">' +
                        '<div style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:0.5rem" class="ai-content">' + renderMd(audit.summary) + '</div>' +
                        (audit.findings.length > 0 ? audit.findings.map(f =>
                            '<div class="ai-suggestion-item">' +
                            '<div class="ai-suggestion-priority">' + sevDot(f.severity) + '</div>' +
                            '<div class="ai-suggestion-body">' +
                            '<h4>' + f.category + ' — ' + f.table + '</h4>' +
                            '<p>' + f.detail + '</p>' +
                            '</div></div>'
                        ).join("") : '') +
                        '</div>';
                } catch (err) {
                    resultDiv.innerHTML = '<p style="color:var(--red);font-size:0.78rem;margin-top:0.5rem">Audit failed: ' + err.message + '</p>';
                }
                btn.disabled = false;
                btn.innerHTML = "&#10024; Audit Queries";
            });
        } catch (err) {
            riskSlot.innerHTML = "";
        }
    }
}


// ── Pages ──

async function renderDashboard() {
    const [data, sources, reports] = await Promise.all([
        api("/api/dashboard"),
        api("/api/sources"),
        api("/api/reports"),
    ]);
    const scan = data.last_scan;

    const total = data.sources_total;
    const hasSources = total > 0;
    const allUnknown = hasSources && data.sources_fresh === 0 && data.sources_stale === 0 && data.sources_outdated === 0;
    const freshPct = hasSources ? pct(data.sources_fresh, total) : 0;
    const stalePct = hasSources ? pct(data.sources_stale, total) : 0;
    const outdatedPct = hasSources ? pct(data.sources_outdated, total) : 0;
    const unknownPct = hasSources ? 100 - freshPct - stalePct - outdatedPct : 0;

    // Health label
    let healthLabel;
    if (!hasSources) healthLabel = "No sources yet";
    else if (allUnknown) healthLabel = "Not yet probed";
    else healthLabel = freshPct + "% fresh";

    // Find problematic sources/reports, sorted by severity (outdated first, then stale)
    const problemSources = sources.filter(s => s.status === "stale" || s.status === "outdated")
        .sort((a, b) => (a.status === "outdated" ? 0 : 1) - (b.status === "outdated" ? 0 : 1));
    const problemReports = reports.filter(r => r.status === "outdated sources" || r.status === "stale sources")
        .sort((a, b) => (a.status === "outdated sources" ? 0 : 1) - (b.status === "outdated sources" ? 0 : 1));
    const allProblems = [...problemSources.map(s => ({ kind: "source", item: s })), ...problemReports.map(r => ({ kind: "report", item: r }))];
    const ATTENTION_LIMIT = 6;

    // Store for click-through navigation
    window._dashboardSources = sources;
    window._dashboardReports = reports;

    return `
        <div class="page-header">
            <h1>Data Governance Dashboard</h1>
            <span class="subtitle">Real-time health monitoring for your BI ecosystem</span>
        </div>

        <div id="ai-briefing-slot"></div>

        <div class="stat-grid">
            <div class="stat-card card-blue">
                <div class="stat-label">Total Sources</div>
                <div class="stat-value">${data.sources_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green">${data.sources_fresh} fresh</span>
                    <span class="stat-dot dot-yellow">${data.sources_stale} stale</span>
                    <span class="stat-dot dot-red">${data.sources_outdated} outdated</span>
                    ${data.sources_unknown ? `<span class="stat-dot dot-muted">${data.sources_unknown} unknown</span>` : ""}
                </div>
            </div>
            <div class="stat-card card-purple">
                <div class="stat-label">Reports</div>
                <div class="stat-value">${data.reports_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green">${reports.filter(r => r.status === "current").length} healthy</span>
                    <span class="stat-dot dot-yellow">${reports.filter(r => r.status === "stale sources").length} at risk</span>
                    <span class="stat-dot dot-red">${reports.filter(r => r.status === "outdated sources").length} degraded</span>
                    ${reports.filter(r => r.status === "unknown").length ? `<span class="stat-dot dot-muted">${reports.filter(r => r.status === "unknown").length} unknown</span>` : ""}
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
                <span style="color:var(--text-dim);font-size:0.72rem">${healthLabel}</span>
            </div>
            ${!hasSources ? `
            <div class="health-bar">
                <div class="segment segment-muted" style="width:100%"></div>
            </div>
            <div style="text-align:center;color:var(--text-dim);font-size:0.78rem;margin-top:0.5rem">Run a scan to discover data sources</div>
            ` : allUnknown ? `
            <div class="health-bar">
                <div class="segment segment-muted" style="width:100%"></div>
            </div>
            <div style="text-align:center;color:var(--text-dim);font-size:0.78rem;margin-top:0.5rem">${total} sources discovered &mdash; probe to check freshness</div>
            ` : `
            <div class="health-bar">
                ${freshPct > 0 ? `<div class="segment segment-green" style="width:${freshPct}%"></div>` : ""}
                ${stalePct > 0 ? `<div class="segment segment-yellow" style="width:${stalePct}%"></div>` : ""}
                ${outdatedPct > 0 ? `<div class="segment segment-red" style="width:${outdatedPct}%"></div>` : ""}
                ${unknownPct > 0 ? `<div class="segment segment-muted" style="width:${unknownPct}%"></div>` : ""}
            </div>
            `}
        </div>

        <div class="section-grid">
            <div class="section">
                <h2>Attention Needed${allProblems.length > 0 ? ` <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">(${allProblems.length})</span>` : ""}</h2>
                ${allProblems.length > 0 ? `
                    <div class="alert-list" id="attention-list">
                        ${allProblems.slice(0, ATTENTION_LIMIT).map(p => {
                            if (p.kind === "source") {
                                const s = p.item;
                                const parsed = parseSourceName(s);
                                return `<div class="alert-item attention-clickable" data-kind="source" data-id="${s.id}">
                                    <div class="dot ${s.status === 'outdated' ? 'dot-red' : 'dot-yellow'}"></div>
                                    <span><strong>${parsed.shortName}</strong> &mdash; ${s.status === 'outdated' ? 'data older than 90 days' : 'data is 31-90 days old'}</span>
                                    <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${s.last_updated ? timeAgo(s.last_updated) : ""}</span>
                                </div>`;
                            } else {
                                const r = p.item;
                                return `<div class="alert-item attention-clickable" data-kind="report" data-id="${r.id}">
                                    <div class="dot ${r.status === 'outdated sources' ? 'dot-red' : 'dot-yellow'}"></div>
                                    <span><strong>${r.name}</strong> &mdash; has ${r.status === 'outdated sources' ? 'outdated' : 'stale'} sources</span>
                                    <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${r.worst_source_updated ? timeAgo(r.worst_source_updated) : ""}</span>
                                </div>`;
                            }
                        }).join("")}
                    </div>
                    ${allProblems.length > ATTENTION_LIMIT ? `
                        <div id="attention-overflow" style="display:none" class="alert-list">
                            ${allProblems.slice(ATTENTION_LIMIT).map(p => {
                                if (p.kind === "source") {
                                    const s = p.item;
                                    const parsed = parseSourceName(s);
                                    return `<div class="alert-item attention-clickable" data-kind="source" data-id="${s.id}">
                                        <div class="dot ${s.status === 'outdated' ? 'dot-red' : 'dot-yellow'}"></div>
                                        <span><strong>${parsed.shortName}</strong> &mdash; ${s.status === 'outdated' ? 'data older than 90 days' : 'data is 31-90 days old'}</span>
                                        <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${s.last_updated ? timeAgo(s.last_updated) : ""}</span>
                                    </div>`;
                                } else {
                                    const r = p.item;
                                    return `<div class="alert-item attention-clickable" data-kind="report" data-id="${r.id}">
                                        <div class="dot ${r.status === 'outdated sources' ? 'dot-red' : 'dot-yellow'}"></div>
                                        <span><strong>${r.name}</strong> &mdash; has ${r.status === 'outdated sources' ? 'outdated' : 'stale'} sources</span>
                                        <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${r.worst_source_updated ? timeAgo(r.worst_source_updated) : ""}</span>
                                    </div>`;
                                }
                            }).join("")}
                        </div>
                        <button class="btn-outline btn-sm" id="btn-show-all-attention" style="margin-top:0.5rem;font-size:0.72rem">Show all ${allProblems.length} items</button>
                    ` : ""}
                ` : allUnknown
                    ? '<div class="empty-state">No issues detected &mdash; run a probe to check source freshness</div>'
                    : '<div class="empty-state">All sources and reports are healthy</div>'
                }
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
            ${alerts.slice(0, 8).map(a => {
                const srcShort = a.source_name ? shortNameFromPath(a.source_name) : "";
                return `<div class="alert-item">
                    <div class="dot ${a.severity === 'critical' ? 'dot-red' : 'dot-yellow'}"></div>
                    <span>${srcShort ? `<strong>${srcShort}</strong> &mdash; ` : ""}${a.message}</span>
                    <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${timeAgo(a.created_at)}</span>
                </div>`;
            }).join("")}
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
        { key: "_fullLocation", label: "Full Location", resizable: true, render: s => `<span class="cell-expandable" title="${(s._fullLocation || '').replace(/"/g, '&quot;')}">${s._fullLocation || "-"}</span>`, sortVal: s => s._fullLocation || "" },
        { key: "type", label: "Type", render: s => typeBadge(s.type) },
        { key: "status", label: "Status", render: s => statusBadge(s.status) },
        { key: "last_updated", label: "Last Updated", render: s => `<span style="color:var(--text-muted)" title="${s.last_updated || ''}">${s.last_updated ? timeAgo(s.last_updated) : "-"}</span>`, sortVal: s => s.last_updated || "" },
        { key: "report_count", label: "Reports", sortVal: s => s.report_count || 0 },
        { key: "owner", label: "Owner", render: s => s.owner === "Multiple"
            ? `<span style="color:var(--text-muted);cursor:help;border-bottom:1px dotted var(--text-dim)" title="Source is used by multiple report owners">${s.owner}</span>`
            : `<span style="color:var(--text-muted)">${s.owner || "-"}</span>` },
    ];

    const fresh = sources.filter(s => s.status === "fresh").length;
    const stale = sources.filter(s => s.status === "stale").length;
    const outdated = sources.filter(s => s.status === "outdated").length;

    return `
        <div class="page-header">
            <h1>Sources</h1>
            <span class="subtitle">${sources.length} data sources tracked &mdash; ${fresh} fresh, ${stale} stale, ${outdated} outdated</span>
        </div>
        ${dataTable("dt-sources", cols, sources, { onRowClick: showSourceDetail })}
    `;
}

async function renderReports() {
    const [reports, edges, sources] = await Promise.all([
        api("/api/reports"),
        api("/api/lineage"),
        api("/api/sources"),
    ]);

    const cols = [
        { key: "name", label: "Report", render: r => `<strong>${r.name}</strong>` },
        { key: "status", label: "Status", render: r => statusBadge(r.status) },
        { key: "source_count", label: "Sources", sortVal: r => r.source_count || 0 },
        { key: "owner", label: "Report Owner", render: r => `<span style="color:var(--text-muted)">${r.owner || "-"}</span>` },
        { key: "business_owner", label: "Business Owner", render: r => `<span style="color:var(--text-muted)">${r.business_owner || "-"}</span>` },
        { key: "frequency", label: "Frequency", render: r => r.frequency
            ? `<span style="color:var(--text-muted)">${r.frequency}</span>`
            : `<span class="freq-inline" data-report-id="${r.id}"><button class="btn-outline btn-sm btn-set-freq" data-report-id="${r.id}" style="font-size:0.68rem">Set</button></span>` },
    ];

    const healthy = reports.filter(r => r.status === "current").length;
    const atRisk = reports.filter(r => r.status !== "current" && r.status !== "unknown").length;

    // Build lineage data for dependency view
    const sourceMap = new Map();
    sources.forEach(s => sourceMap.set(s.id, s));
    const reportSources = new Map();
    edges.forEach(e => {
        if (!reportSources.has(e.report_id)) reportSources.set(e.report_id, new Set());
        reportSources.get(e.report_id).add(e.source_id);
    });
    function sourceFolder(name) {
        if (!name) return "Other";
        const n = name.replace(/\\/g, "/");
        const parts = n.split("/").filter(Boolean);
        if (parts.length >= 3) return parts[parts.length - 2];
        if (parts.length === 2) return parts[0];
        return "Other";
    }
    const statusOrder = { "outdated sources": 0, "stale sources": 1, "unknown": 2, "current": 3 };
    const sortedReports = [...reports].sort((a, b) => {
        const sa = statusOrder[a.status] ?? 2;
        const sb = statusOrder[b.status] ?? 2;
        if (sa !== sb) return sa - sb;
        return a.name.localeCompare(b.name);
    });
    window._lineageData = { sortedReports, reportSources, sourceMap, edges, sourceFolder };

    return `
        <div class="page-header">
            <h1>Reports</h1>
            <span class="subtitle">${reports.length} Power BI reports &mdash; ${healthy} healthy, ${atRisk} need attention</span>
        </div>

        <div class="tab-bar">
            <button class="tab-btn active" data-tab="tab-reports-table">Table</button>
            <button class="tab-btn" data-tab="tab-reports-deps">Dependencies</button>
        </div>

        <div id="tab-reports-table" class="tab-panel active">
            ${dataTable("dt-reports", cols, reports, { onRowClick: showReportDetail })}
        </div>
        <div id="tab-reports-deps" class="tab-panel" style="display:none">
            <div class="lineage-layout">
                <div class="lineage-sidebar">
                    <div class="lineage-sidebar-header">
                        <input id="lineage-report-search" type="text" placeholder="Filter reports..." class="lineage-search-input">
                        <div class="lineage-filter-row">
                            <button class="lineage-filter-btn active" data-filter="all">All</button>
                            <button class="lineage-filter-btn" data-filter="unhealthy">Unhealthy</button>
                        </div>
                    </div>
                    <div id="lineage-report-list" class="lineage-report-list">
                        ${sortedReports.map(r => _lineageReportItem(r, reportSources)).join("")}
                    </div>
                </div>
                <div class="lineage-detail" id="lineage-detail">
                    <div class="lineage-placeholder">
                        <div class="lineage-placeholder-icon">&#8594;</div>
                        <div class="lineage-placeholder-text">Select a report to view its data sources</div>
                        <div class="lineage-placeholder-hint">Reports with issues are listed first</div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function bindReportsPage() {
    // Tab switching (Table / Dependencies)
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => {
                p.style.display = p.id === btn.dataset.tab ? "" : "none";
                p.classList.toggle("active", p.id === btn.dataset.tab);
            });
            // Lazy-init lineage on first switch to Dependencies tab
            if (btn.dataset.tab === "tab-reports-deps" && !window._lineageBound) {
                window._lineageBound = true;
                bindLineagePage();
            }
        });
    });

    // Frequency set buttons
    document.querySelectorAll(".btn-set-freq").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const reportId = btn.dataset.reportId;
            const container = btn.closest(".freq-inline");
            if (!container) return;

            container.innerHTML = `
                <span style="display:inline-flex;gap:0.3rem;align-items:center" onclick="event.stopPropagation()">
                    <select class="freq-select" data-report-id="${reportId}" style="font-size:0.72rem;padding:0.2rem 0.3rem;border:1px solid var(--border);border-radius:4px;background:var(--surface);color:var(--text)">
                        <option value="">Choose...</option>
                        <option value="Daily">Daily</option>
                        <option value="Weekly">Weekly</option>
                        <option value="Monthly">Monthly</option>
                        <option value="Quarterly">Quarterly</option>
                    </select>
                    <button class="btn-sm freq-save" data-report-id="${reportId}" style="font-size:0.68rem;padding:0.15rem 0.4rem">Save</button>
                </span>
            `;

            const saveBtn = container.querySelector(".freq-save");
            const select = container.querySelector(".freq-select");
            saveBtn.addEventListener("click", async (ev) => {
                ev.stopPropagation();
                const freq = select.value;
                if (!freq) { toast("Select a frequency"); return; }
                try {
                    await apiPatch(`/api/reports/${reportId}`, { frequency: freq });
                    toast("Frequency updated");
                    navigate("reports");
                } catch (err) {
                    toast("Failed: " + err.message);
                }
            });
            select.addEventListener("click", (ev) => ev.stopPropagation());
        });
    });
}

async function renderScanner() {
    const [runs, probeRuns] = await Promise.all([
        api("/api/scanner/runs"),
        api("/api/scanner/probe/runs"),
    ]);
    const lastRun = runs.length > 0 ? runs[0] : null;
    const lastProbe = probeRuns.length > 0 ? probeRuns[0] : null;

    return `
        <div class="page-header">
            <h1>Scanner</h1>
            <span class="subtitle">Scan Power BI reports to detect sources and track changes</span>
        </div>

        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1.25rem">
            <button id="btn-scan">Run Scan Now</button>
            <button id="btn-probe" class="btn-outline">Probe Sources</button>
            <button id="btn-ai-briefing" class="btn-outline" style="border-color:var(--purple-bg);color:var(--purple)">&#10024; AI Briefing</button>
            <span style="color:var(--text-dim);font-size:0.78rem">
                ${lastRun ? `Last scan: ${timeAgo(lastRun.started_at)}` : "No scans yet"}
                ${lastProbe ? ` · Last probe: ${timeAgo(lastProbe.started_at)}` : ""}
            </span>
        </div>

        ${lastRun && lastRun.log ? `
            <div class="section">
                <h2 class="log-toggle" data-target="scan-log-body" style="cursor:pointer;user-select:none">Last Scan Log <span style="font-size:0.72rem;font-weight:400;color:var(--text-dim)">&mdash; click to expand</span></h2>
                <div id="scan-log-body" class="scan-log" style="display:none">${lastRun.log}</div>
            </div>
        ` : ""}

        ${lastProbe && lastProbe.log ? `
            <div class="section">
                <h2 class="log-toggle" data-target="probe-log-body" style="cursor:pointer;user-select:none">Last Probe Log <span style="font-size:0.72rem;font-weight:400;color:var(--text-dim)">&mdash; click to expand</span></h2>
                <div id="probe-log-body" class="scan-log" style="display:none">${lastProbe.log}</div>
            </div>
        ` : ""}

        <div id="scanner-ai-briefing-slot"></div>

        <div class="section-grid">
            <div class="section">
                <h2>Scan History</h2>
                ${dataTable("dt-scans", [
                    { key: "started_at", label: "When", render: r => `<span title="${formatDate(r.started_at)}">${timeAgo(r.started_at)}</span>`, sortVal: r => r.started_at || "" },
                    { key: "status", label: "Status", render: r => statusBadge(r.status) },
                    { key: "reports_scanned", label: "Reports", render: r => `${r.reports_scanned ?? "-"}`, sortVal: r => r.reports_scanned ?? 0 },
                    { key: "sources_found", label: "Sources", render: r => `${r.sources_found ?? "-"}`, sortVal: r => r.sources_found ?? 0 },
                    { key: "new_sources", label: "New", render: r => r.new_sources ? `<span style="color:var(--green)">+${r.new_sources}</span>` : '-', sortVal: r => r.new_sources ?? 0 },
                ], runs)}
            </div>
            <div class="section">
                <h2>Probe History</h2>
                ${probeRuns.length > 0 ? dataTable("dt-probes", [
                    { key: "started_at", label: "When", render: r => `<span title="${formatDate(r.started_at)}">${timeAgo(r.started_at)}</span>`, sortVal: r => r.started_at || "" },
                    { key: "status", label: "Status", render: r => statusBadge(r.status) },
                    { key: "sources_probed", label: "Probed", render: r => `${r.sources_probed ?? "-"}` },
                    { key: "fresh", label: "Fresh", render: r => r.fresh ? `<span style="color:var(--green)">${r.fresh}</span>` : '-' },
                    { key: "stale", label: "Stale", render: r => r.stale ? `<span style="color:var(--yellow)">${r.stale}</span>` : '-' },
                    { key: "outdated", label: "Outdated", render: r => r.outdated ? `<span style="color:var(--red)">${r.outdated}</span>` : '-' },
                ], probeRuns) : '<div class="empty-state">No probes yet. Click "Probe Sources" to check freshness.</div>'}
            </div>
        </div>
    `;
}

async function renderAlerts() {
    const alerts = await api("/api/alerts?active_only=false");

    const cols = [
        { key: "severity", label: "Severity", render: a => statusBadge(a.severity) },
        { key: "message", label: "Message", render: a => {
            const srcShort = a.source_name ? shortNameFromPath(a.source_name) : "";
            return srcShort ? `<strong>${srcShort}</strong> &mdash; ${a.message}` : a.message;
        }},
        { key: "created_at", label: "When", render: a => `<span style="color:var(--text-muted)" title="${formatDate(a.created_at)}">${timeAgo(a.created_at)}</span>`, sortVal: a => a.created_at || "" },
        { key: "acknowledged", label: "Status", render: a => a.acknowledged
            ? `<span class="badge badge-muted">acknowledged</span>`
            : `<button class="btn-sm btn-red btn-ack-alert" data-alert-id="${a.id}">Acknowledge</button>`,
            sortVal: a => a.acknowledged ? 1 : 0 },
    ];

    const active = alerts.filter(a => !a.acknowledged).length;
    const acked = alerts.length - active;

    return { html: dataTable("dt-alerts", cols, alerts), active, acked, total: alerts.length };
}

function bindAlertsTab() {
    document.querySelectorAll(".btn-ack-alert").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const alertId = btn.dataset.alertId;
            try {
                await fetch(`/api/alerts/${alertId}/acknowledge`, { method: "POST" });
                toast("Alert acknowledged");
                navigate("issues");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    });
}

async function renderActionsContent() {
    const actions = await api("/api/actions");

    const open = actions.filter(a => a.status === "open").length;
    const investigating = actions.filter(a => a.status === "investigating").length;
    const acknowledged = actions.filter(a => a.status === "acknowledged").length;
    const resolved = actions.filter(a => a.status === "resolved" || a.status === "expected").length;

    const statusOptions = ["open", "acknowledged", "investigating", "expected", "resolved"];

    function renderActionCards(filter) {
        const filtered = filter === "all" ? actions : actions.filter(a => a.status === filter);

        if (filtered.length === 0) {
            return '<div class="empty-state">No actions match this filter</div>';
        }

        return `<div class="action-cards">${filtered.map(a => {
            const indColor = a.type.includes("outdated") || a.type.includes("error") ? "ind-red"
                           : a.type.includes("stale") ? "ind-yellow"
                           : a.type.includes("broken") ? "ind-red"
                           : "ind-blue";

            const sourceName = a.source_name || "-";
            const shortSource = shortNameFromPath(sourceName) || sourceName;

            return `
                <div class="action-card" data-action-id="${a.id}">
                    <div class="action-indicator ${indColor}"></div>
                    <div class="action-body">
                        <div class="action-title">${shortSource}</div>
                        <div class="action-meta">
                            ${actionTypeBadge(a.type)}
                            <span>Assigned: ${a.assigned_to || "unassigned"}</span>
                            <span>${timeAgo(a.created_at)}</span>
                        </div>
                        ${a.notes ? `<div class="action-notes">${a.notes}</div>` : ""}
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

    // Store render function for re-rendering after filter change
    window._actionsData = { actions, renderActionCards };

    const html = `
        <div class="summary-counts">
            <div class="summary-count"><span class="count-num" style="color:var(--red)">${open}</span><span class="count-label">open</span></div>
            <div class="summary-count"><span class="count-num" style="color:var(--blue)">${acknowledged}</span><span class="count-label">acknowledged</span></div>
            <div class="summary-count"><span class="count-num" style="color:var(--yellow)">${investigating}</span><span class="count-label">investigating</span></div>
            <div class="summary-count"><span class="count-num" style="color:var(--green)">${resolved}</span><span class="count-label">resolved</span></div>
        </div>

        <div class="action-filters">
            <button class="action-filter-btn active" data-filter="all">All (${actions.length})</button>
            <button class="action-filter-btn" data-filter="open">Open (${open})</button>
            <button class="action-filter-btn" data-filter="acknowledged">Acknowledged (${acknowledged})</button>
            <button class="action-filter-btn" data-filter="investigating">Investigating (${investigating})</button>
            <button class="action-filter-btn" data-filter="resolved">Resolved (${resolved})</button>
            <button class="action-filter-btn" data-filter="expected" title="Sources that are intentionally stale/outdated (e.g. quarterly data)">Expected (${actions.filter(a => a.status === "expected").length})</button>
        </div>

        <div id="action-list">
            ${renderActionCards("all")}
        </div>
    `;
    return { html, open, total: actions.length };
}

async function renderIssues() {
    const [actionsData, alertsData] = await Promise.all([
        renderActionsContent(),
        renderAlerts(),
    ]);

    const totalOpen = actionsData.open + alertsData.active;

    return `
        <div class="page-header">
            <h1>Issues</h1>
            <span class="subtitle">${totalOpen} open issue${totalOpen !== 1 ? 's' : ''} across actions and alerts</span>
        </div>

        <div id="ai-suggestions-slot"></div>

        <div class="tab-bar">
            <button class="tab-btn active" data-tab="tab-actions">Actions <span class="tab-count">${actionsData.total}</span></button>
            <button class="tab-btn" data-tab="tab-alerts">Alerts <span class="tab-count">${alertsData.total}</span></button>
        </div>

        <div id="tab-actions" class="tab-panel active">
            ${actionsData.html}
        </div>
        <div id="tab-alerts" class="tab-panel" style="display:none">
            ${alertsData.html}
        </div>
    `;
}

function bindActionsTab() {
    // Filter buttons
    document.querySelectorAll(".action-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".action-filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const filter = btn.dataset.filter;
            const container = $("#action-list");
            if (container && window._actionsData) {
                container.innerHTML = window._actionsData.renderActionCards(filter);
                bindActionStatusSelects();
            }
        });
    });

    bindActionStatusSelects();
}

function bindIssuesPage() {
    // Tab switching
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => {
                p.style.display = p.id === btn.dataset.tab ? "" : "none";
                p.classList.toggle("active", p.id === btn.dataset.tab);
            });
        });
    });

    bindActionsTab();
    bindAlertsTab();
    bindDataTables();
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
                navigate("issues");
            }
        });
    });
}


function shortSourceLabel(name, type) {
    const DB_TYPES = new Set(["sql", "postgresql", "mysql", "oracle", "odbc", "oledb", "ssas", "redshift", "snowflake", "bigquery"]);
    if (DB_TYPES.has(type)) {
        // "sqlserver01.company.local/SalesDB/dbo.Orders" → "dbo.Orders"
        const lastSlash = name.lastIndexOf("/");
        return lastSlash >= 0 ? name.substring(lastSlash + 1) : name;
    }
    // File-based: "C:\Data\SKU_Master.xlsx" → "SKU_Master"
    const sep = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
    const withExt = sep >= 0 ? name.substring(sep + 1) : name;
    const dot = withExt.lastIndexOf(".");
    return dot > 0 ? withExt.substring(0, dot) : withExt;
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

function _lineageReportItem(r, reportSources) {
    const srcCount = reportSources.get(r.id)?.size || 0;
    const statusClass = r.status === "current" ? "dot-green"
        : r.status === "stale sources" ? "dot-yellow"
        : r.status === "outdated sources" ? "dot-red"
        : "dot-muted";
    return `
        <div class="lineage-report-item" data-report-id="${r.id}">
            <span class="dot ${statusClass}" style="width:8px;height:8px;flex-shrink:0"></span>
            <div class="lineage-report-info">
                <span class="lineage-report-name">${shortNameFromPath(r.name) || r.name}</span>
                <span class="lineage-report-meta">${srcCount} source${srcCount !== 1 ? "s" : ""}${r.owner ? " &middot; " + r.owner : ""}</span>
            </div>
            ${statusBadge(r.status)}
        </div>
    `;
}

function _renderLineageDetail(reportId) {
    const data = window._lineageData;
    if (!data) return;

    const report = data.sortedReports.find(r => r.id === reportId);
    if (!report) return;

    const srcIds = data.reportSources.get(reportId) || new Set();
    const reportSources = [];
    srcIds.forEach(sid => {
        const s = data.sourceMap.get(sid);
        if (s) reportSources.push(s);
    });

    // Group sources by folder
    const grouped = new Map();
    reportSources.forEach(s => {
        const folder = data.sourceFolder(s.name);
        if (!grouped.has(folder)) grouped.set(folder, []);
        grouped.get(folder).push(s);
    });

    // Sort folders: folders with issues first
    const folderOrder = (sources) => {
        if (sources.some(s => s.status === "outdated" || s.status === "error")) return 0;
        if (sources.some(s => s.status === "stale")) return 1;
        return 2;
    };
    const sortedFolders = [...grouped.entries()].sort((a, b) => {
        const oa = folderOrder(a[1]);
        const ob = folderOrder(b[1]);
        if (oa !== ob) return oa - ob;
        return a[0].localeCompare(b[0]);
    });

    // Find which other reports share sources with this one
    const sharedReports = new Map(); // sourceId → [report names]
    data.edges.forEach(e => {
        if (srcIds.has(e.source_id) && e.report_id !== reportId) {
            if (!sharedReports.has(e.source_id)) sharedReports.set(e.source_id, []);
            sharedReports.get(e.source_id).push(e.report_name);
        }
    });

    const statusSummary = { fresh: 0, stale: 0, outdated: 0, unknown: 0 };
    reportSources.forEach(s => {
        if (s.status === "fresh") statusSummary.fresh++;
        else if (s.status === "stale") statusSummary.stale++;
        else if (s.status === "outdated" || s.status === "error") statusSummary.outdated++;
        else statusSummary.unknown++;
    });

    const detailEl = document.getElementById("lineage-detail");
    if (!detailEl) return;

    detailEl.innerHTML = `
        <div class="lineage-detail-header">
            <div>
                <h2 style="margin-bottom:0.15rem">${shortNameFromPath(report.name) || report.name}</h2>
                <div class="lineage-detail-meta">
                    ${statusBadge(report.status)}
                    <span>${reportSources.length} source${reportSources.length !== 1 ? "s" : ""}</span>
                    ${report.owner ? `<span>Owner: ${report.owner}</span>` : ""}
                    ${report.frequency ? `<span>Frequency: ${report.frequency}</span>` : ""}
                    ${report.worst_source_updated ? `<span>Oldest data: ${timeAgo(report.worst_source_updated)}</span>` : ""}
                </div>
            </div>
            <div class="lineage-health-pills">
                ${statusSummary.fresh ? `<span class="health-pill pill-green">${statusSummary.fresh} fresh</span>` : ""}
                ${statusSummary.stale ? `<span class="health-pill pill-yellow">${statusSummary.stale} stale</span>` : ""}
                ${statusSummary.outdated ? `<span class="health-pill pill-red">${statusSummary.outdated} outdated</span>` : ""}
                ${statusSummary.unknown ? `<span class="health-pill pill-muted">${statusSummary.unknown} unknown</span>` : ""}
            </div>
        </div>

        <div id="ai-risk-slot"></div>

        <div class="lineage-source-tree">
            ${sortedFolders.map(([folder, folderSources]) => `
                <div class="lineage-folder-group">
                    <div class="lineage-folder-label">${folder}</div>
                    ${folderSources.map(s => {
                        const shared = sharedReports.get(s.id) || [];
                        return `
                        <div class="lineage-source-row">
                            <div class="lineage-source-indicator status-dot-${s.status}"></div>
                            <div class="lineage-source-info">
                                <div class="lineage-source-name">${shortNameFromPath(s.name) || s.name}</div>
                                <div class="lineage-source-path">${s.name}</div>
                                <div class="lineage-source-details">
                                    ${typeBadge(s.type)}
                                    ${statusBadge(s.status)}
                                    ${s.last_updated ? `<span class="lineage-source-date">${timeAgo(s.last_updated)}</span>` : '<span class="lineage-source-date">never probed</span>'}
                                    ${s.owner ? `<span class="lineage-source-owner">${s.owner}</span>` : ""}
                                </div>
                                ${shared.length ? `<div class="lineage-shared">Also used by: ${shared.map(n => shortNameFromPath(n) || n).join(", ")}</div>` : ""}
                            </div>
                        </div>`;
                    }).join("")}
                </div>
            `).join("")}
        </div>
    `;
}

function bindLineagePage() {
    const data = window._lineageData;
    if (!data) return;

    const listEl = document.getElementById("lineage-report-list");
    const searchInput = document.getElementById("lineage-report-search");

    // Report item click → show detail
    function bindReportClicks() {
        document.querySelectorAll(".lineage-report-item").forEach(item => {
            item.addEventListener("click", () => {
                document.querySelectorAll(".lineage-report-item.selected").forEach(i => i.classList.remove("selected"));
                item.classList.add("selected");
                const rid = parseInt(item.dataset.reportId);
                _renderLineageDetail(rid);
                renderAIReportRisk(rid);
            });
        });
    }
    bindReportClicks();

    // Filter + search
    function filterList() {
        const query = (searchInput?.value || "").toLowerCase().trim();
        const activeFilter = document.querySelector(".lineage-filter-btn.active")?.dataset.filter || "all";

        const filtered = data.sortedReports.filter(r => {
            if (activeFilter === "unhealthy" && r.status === "current") return false;
            if (query) {
                const name = (r.name || "").toLowerCase();
                const owner = (r.owner || "").toLowerCase();
                if (!name.includes(query) && !owner.includes(query)) return false;
            }
            return true;
        });

        listEl.innerHTML = filtered.length
            ? filtered.map(r => _lineageReportItem(r, data.reportSources)).join("")
            : '<div class="lineage-empty-list">No reports match</div>';
        bindReportClicks();
    }

    if (searchInput) {
        searchInput.addEventListener("input", () => filterList());
    }

    document.querySelectorAll(".lineage-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".lineage-filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            filterList();
        });
    });

    // Auto-select first unhealthy report, or first report
    const firstUnhealthy = data.sortedReports.find(r => r.status !== "current");
    const autoSelect = firstUnhealthy || data.sortedReports[0];
    if (autoSelect) {
        const item = document.querySelector(`.lineage-report-item[data-report-id="${autoSelect.id}"]`);
        if (item) {
            item.classList.add("selected");
            _renderLineageDetail(autoSelect.id);
            renderAIReportRisk(autoSelect.id);
        }
    }
}


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    sources: renderSources,
    reports: renderReports,
    scanner: renderScanner,
    issues: renderIssues,
};

// Map old hash routes to new pages for backwards compat
const pageAliases = { alerts: "issues", actions: "issues", lineage: "reports" };

let currentPage = "dashboard";

async function navigate(page) {
    // Resolve aliases for old routes
    if (pageAliases[page]) page = pageAliases[page];
    if (!pages[page]) page = "dashboard";
    currentPage = page;

    // Update URL hash without triggering hashchange
    window._skipHash = true;
    location.hash = page === "dashboard" ? "" : page;

    // Reset lazy-init flags
    window._lineageBound = false;

    $$("nav a").forEach(a => {
        a.classList.toggle("active", a.dataset.page === page);
    });

    const app = $("#app");
    app.innerHTML = '<div class="loading">Loading...</div>';

    try {
        const html = await pages[page]();
        app.innerHTML = html;

        bindDataTables();
        if (page === "dashboard") {
            renderAIBriefing();
            renderDashboardAlerts();
            const btnShowAll = document.getElementById("btn-show-all-attention");
            if (btnShowAll) {
                btnShowAll.addEventListener("click", () => {
                    const overflow = document.getElementById("attention-overflow");
                    if (overflow) {
                        const showing = overflow.style.display !== "none";
                        overflow.style.display = showing ? "none" : "";
                        btnShowAll.textContent = showing ? btnShowAll.textContent.replace("Show less", "Show all") : "Show less";
                    }
                });
            }
            // Clickable attention items — drill down to source/report detail
            document.querySelectorAll(".attention-clickable").forEach(el => {
                el.addEventListener("click", async () => {
                    const kind = el.dataset.kind;
                    const id = parseInt(el.dataset.id);
                    if (kind === "source") {
                        const src = (window._dashboardSources || []).find(s => s.id === id);
                        if (src) { await navigate("sources"); showSourceDetail(src); }
                    } else {
                        const rpt = (window._dashboardReports || []).find(r => r.id === id);
                        if (rpt) { await navigate("reports"); showReportDetail(rpt); }
                    }
                });
            });
        }
        if (page === "scanner") bindScannerButtons();
        if (page === "issues") { bindIssuesPage(); renderAISuggestions(); }
        if (page === "reports") bindReportsPage();
    } catch (err) {
        app.innerHTML = `<div class="loading" style="color:var(--red)">Error loading page: ${err.message}</div>`;
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
                if (hint) hint.textContent = showing ? "— click to expand" : "— click to collapse";
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

    const btnAIBriefing = $("#btn-ai-briefing");
    if (btnAIBriefing) {
        btnAIBriefing.addEventListener("click", async () => {
            btnAIBriefing.disabled = true;
            btnAIBriefing.textContent = "Generating...";
            const slot = document.getElementById("scanner-ai-briefing-slot");
            if (slot) slot.innerHTML = '<div class="ai-loading"><div class="ai-spinner"></div>Generating AI briefing...</div>';
            try {
                const data = await api("/api/ai/briefing");
                if (slot) {
                    slot.innerHTML = `
                        <div class="ai-briefing-card" style="margin-bottom:1.25rem">
                            <div class="ai-briefing-header">
                                <span class="ai-briefing-label">&#10024; AI Briefing</span>
                                <span class="risk-dot risk-${data.risk_level}"></span>
                            </div>
                            <div class="ai-briefing-text">${renderMd(data.summary)}</div>
                            <div class="ai-briefing-footer">
                                <span class="ai-briefing-meta">AI-generated &middot; ${data.risk_level} risk &middot; ${timeAgo(data.generated_at)}</span>
                            </div>
                        </div>
                    `;
                }
            } catch (err) {
                toast("Briefing failed: " + err.message);
                if (slot) slot.innerHTML = "";
            }
            btnAIBriefing.disabled = false;
            btnAIBriefing.innerHTML = "&#10024; AI Briefing";
        });
    }
}


// ── AI Chat Panel ──

function initAIChatPanel() {
    if (document.getElementById("ai-chat-panel")) return;

    // Floating action button
    const fab = document.createElement("button");
    fab.className = "ai-fab";
    fab.id = "ai-fab";
    fab.innerHTML = "&#10024;";
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
            <h3><span class="ai-sparkle">&#10024;</span> AI Assistant</h3>
            <button class="ai-chat-close" id="ai-chat-close">&times;</button>
        </div>
        <div class="ai-chat-messages" id="ai-chat-messages">
            <div class="ai-msg assistant">
                <p>Hi! I can help you understand your data governance ecosystem. Ask me about risks, source health, or specific reports.</p>
            </div>
        </div>
        <div class="ai-chat-quick" id="ai-chat-quick">
            <button class="ai-quick-chip" data-q="What's at risk?">What's at risk?</button>
            <button class="ai-quick-chip" data-q="Summarize dashboard">Summarize dashboard</button>
            <button class="ai-quick-chip" data-q="Show stale sources">Show stale sources</button>
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
            headers: { "Content-Type": "application/json" },
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


// ── AI Briefing (Dashboard) ──

async function renderAIBriefing() {
    const container = document.getElementById("ai-briefing-slot");
    if (!container) return;
    container.innerHTML = '<div class="ai-loading"><div class="ai-spinner"></div>Generating AI briefing...</div>';
    try {
        const data = await api("/api/ai/briefing");
        container.innerHTML = `
            <div class="ai-briefing-card">
                <div class="ai-briefing-header">
                    <span class="ai-briefing-label">&#10024; AI Briefing</span>
                    <span class="risk-dot risk-${data.risk_level}"></span>
                </div>
                <div class="ai-briefing-text">${renderMd(data.summary)}</div>
                <div class="ai-briefing-footer">
                    <span class="ai-briefing-meta">AI-generated &middot; ${data.risk_level} risk &middot; Generated ${timeAgo(data.generated_at)}</span>
                    <button class="ai-briefing-regen" id="ai-briefing-regen">&#8635; Regenerate</button>
                </div>
            </div>
        `;
        document.getElementById("ai-briefing-regen").addEventListener("click", () => renderAIBriefing());
    } catch (err) {
        container.innerHTML = "";
    }
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
                <div class="ai-suggestions-header">&#10024; AI Suggestions</div>
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

document.addEventListener("DOMContentLoaded", () => {
    $$("nav a[data-page]").forEach(a => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            navigate(a.dataset.page);
        });
    });

    window.addEventListener("hashchange", () => {
        if (window._skipHash) { window._skipHash = false; return; }
        const page = location.hash.length > 1 ? location.hash.substring(1) : "dashboard";
        if (pages[page] && page !== currentPage) navigate(page);
    });

    initAIChatPanel();
    navigate(getInitialPage());
});
