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

async function apiPostJson(path, body) {
    const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

async function apiPut(path, body) {
    const res = await fetch(path, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

async function apiDelete(path) {
    const res = await fetch(path, { method: "DELETE" });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function statusBadge(status) {
    if (!status) return '<span class="badge badge-muted">not probed</span>';
    const s = status.toLowerCase();
    if (s === "fresh" || s === "healthy" || s === "current" || s === "pass" || s === "completed")
        return `<span class="badge badge-green">healthy</span>`;
    if (s === "stale" || s === "at risk" || s === "stale sources" || s === "warn" || s === "warning")
        return `<span class="badge badge-yellow">at risk</span>`;
    if (s === "outdated" || s === "degraded" || s === "outdated sources")
        return `<span class="badge badge-red">degraded</span>`;
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
        stale_source: "At Risk",
        outdated_source: "Degraded",
        error_source: "Degraded",
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

    const hasCustomRule = source.custom_fresh_days != null;
    const freshVal = source.custom_fresh_days || "";
    const staleVal = source.custom_stale_days || "";

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
            <div class="detail-item"><div class="detail-label">Upstream System</div><span style="color:var(--text)">${source.upstream_name || "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Upstream Refresh</div><span style="color:var(--text)">${source.upstream_refresh_day || "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Source Refresh</div><span style="color:var(--text)">${source.refresh_schedule ? 'Weekly - ' + source.refresh_schedule : "-"}</span></div>
        </div>

        <h2>Reports using this source (${reports.length})</h2>
        <table class="detail-table">
            <thead><tr><th>Report</th><th>Table Name</th><th>Owner</th></tr></thead>
            <tbody>${reportRows}</tbody>
        </table>

        <h2>Freshness Rule</h2>
        <div class="freshness-rule-form">
            <label class="freshness-label">Healthy up to
                <input type="number" id="fresh-days-input" value="${freshVal}" placeholder="31" min="1" max="9999" class="input-sm">
                days
            </label>
            <label class="freshness-label">At risk up to
                <input type="number" id="stale-days-input" value="${staleVal}" placeholder="90" min="1" max="9999" class="input-sm">
                days
            </label>
            <button class="btn-sm btn-blue" id="btn-save-freshness">Save</button>
            ${hasCustomRule ? '<button class="btn-sm btn-outline" id="btn-reset-freshness">Reset to default</button>' : ''}
            ${hasCustomRule
                ? '<span class="badge badge-blue" style="font-size:0.72rem">custom rule active</span>'
                : '<span style="color:var(--text-dim);font-size:0.75rem">Using global defaults (31 / 90 days)</span>'}
        </div>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#btn-close-detail").addEventListener("click", () => panel.remove());

    // Freshness rule bindings
    const saveFreshBtn = document.getElementById("btn-save-freshness");
    if (saveFreshBtn) {
        saveFreshBtn.addEventListener("click", async () => {
            const fd = parseInt(document.getElementById("fresh-days-input").value);
            const sd = parseInt(document.getElementById("stale-days-input").value);
            if (!fd || !sd || fd >= sd) {
                toast("Fresh days must be less than stale days");
                return;
            }
            try {
                await apiPut(`/api/sources/${source.id}/freshness-rule`, { fresh_days: fd, stale_days: sd });
                toast("Freshness rule saved — re-probe to apply");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    }
    const resetFreshBtn = document.getElementById("btn-reset-freshness");
    if (resetFreshBtn) {
        resetFreshBtn.addEventListener("click", async () => {
            try {
                await apiDelete(`/api/sources/${source.id}/freshness-rule`);
                toast("Freshness rule reset to defaults");
                document.getElementById("fresh-days-input").value = "";
                document.getElementById("stale-days-input").value = "";
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
    }
}


// ── Report detail panel ──

async function showReportDetail(report) {
    // Toggle: if already expanded for this report, collapse it
    const existing = document.querySelector(`.report-expand-row[data-report-id="${report.id}"]`);
    if (existing) { existing.remove(); return; }

    // Collapse any other expanded report
    document.querySelectorAll(".report-expand-row").forEach(r => r.remove());

    // Find the clicked row in the table
    const clickedRow = document.querySelector(`tr[data-clickable][data-row-idx]`);
    const allRows = document.querySelectorAll("#dt-reports tbody tr[data-clickable]");
    let targetRow = null;
    allRows.forEach(tr => {
        const idx = parseInt(tr.dataset.rowIdx);
        const dt = window._dt["dt-reports"];
        if (dt && dt._displayRows && dt._displayRows[idx] && dt._displayRows[idx].id === report.id) {
            targetRow = tr;
        }
    });

    const tables = await api(`/api/reports/${report.id}/tables`);

    // Look up full source objects from the sources we fetched
    const allSources = window._reportPageSources || [];
    const sourceMap = new Map();
    allSources.forEach(s => sourceMap.set(s.id, s));

    const colCount = targetRow ? targetRow.children.length : 6;
    const expandRow = document.createElement("tr");
    expandRow.className = "report-expand-row";
    expandRow.dataset.reportId = report.id;

    const sourceRows = tables.length > 0
        ? tables.map(t => {
            const src = t.source_id ? sourceMap.get(t.source_id) : null;
            const srcName = src ? (shortNameFromPath(src.name) || src.name) : (t.source_name || "no linked source");
            const srcStatus = src ? src.status : "unknown";
            return `<div class="report-source-item${src ? ' report-source-clickable' : ''}" ${src ? `data-source-id="${src.id}"` : ''}>
                <span class="dot dot-${srcStatus === 'fresh' || srcStatus === 'healthy' ? 'green' : srcStatus === 'stale' || srcStatus === 'at risk' ? 'yellow' : srcStatus === 'outdated' || srcStatus === 'degraded' ? 'red' : 'muted'}" style="width:6px;height:6px"></span>
                <span class="report-source-table">${t.table_name}</span>
                <span class="report-source-arrow">&rarr;</span>
                <span class="report-source-name">${srcName}</span>
                ${src ? statusBadge(src.status) : ''}
                ${src && src.last_updated ? `<span style="color:var(--text-dim);font-size:0.72rem;margin-left:auto">${timeAgo(src.last_updated)}</span>` : ''}
            </div>`;
        }).join("")
        : '<div class="empty-state" style="padding:0.5rem">No tables found</div>';

    expandRow.innerHTML = `<td colspan="${colCount}" class="report-expand-cell">
        <div class="report-expand-content">
            <div class="report-expand-header">
                <div class="detail-grid" style="margin-bottom:0.5rem">
                    <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(report.status)}</div>
                    <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${report.owner || "-"}</span></div>
                    <div class="detail-item"><div class="detail-label">Business Owner</div><span style="color:var(--text)">${report.business_owner || "-"}</span></div>
                    <div class="detail-item"><div class="detail-label">Frequency</div><span style="color:var(--text)">${report.frequency || "-"}</span></div>
                    <div class="detail-item">${report.powerbi_url
                        ? `<a href="${report.powerbi_url}" target="_blank" rel="noopener" class="powerbi-link">Open in Power BI &rarr;</a>`
                        : `<span class="powerbi-link powerbi-link-disabled">Open in Power BI</span>`}</div>
                </div>
            </div>
            <div class="report-expand-label">Data Sources (${tables.length})</div>
            <div class="report-source-list">${sourceRows}</div>
        </div>
    </td>`;

    if (targetRow) {
        targetRow.after(expandRow);
    } else {
        const tbody = document.querySelector("#dt-reports tbody");
        if (tbody) tbody.appendChild(expandRow);
    }

    // Bind clickable sources
    expandRow.querySelectorAll(".report-source-clickable").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const srcId = parseInt(el.dataset.sourceId);
            const src = sourceMap.get(srcId);
            if (src) { await navigate("sources"); showSourceDetail(src); }
        });
    });
}


// ── Pages ──

async function renderDashboard() {
    const [data, sources, reports, alerts, healthTrend] = await Promise.all([
        api("/api/dashboard"),
        api("/api/sources"),
        api("/api/reports"),
        api("/api/alerts?active_only=true"),
        api("/api/schedules/health-trend"),
    ]);
    const scan = data.last_scan;
    window._dashboardData = data;
    window._healthTrend = healthTrend;

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
    else healthLabel = freshPct + "% healthy";

    // Build unified "Needs Attention" list from problem sources, reports, and alerts
    const problemSources = sources.filter(s => s.status === "stale" || s.status === "outdated");
    const problemReports = reports.filter(r => r.status === "degraded" || r.status === "at risk");
    const needsAttention = [];
    problemSources.forEach(s => {
        const parsed = parseSourceName(s);
        needsAttention.push({
            severity: s.status === "outdated" ? "red" : "yellow",
            kind: "source", name: parsed.shortName,
            description: s.status === "outdated" ? "degraded — data older than 90 days" : "at risk — data is 31\u201390 days old",
            timestamp: s.last_updated, id: s.id, data: s,
        });
    });
    problemReports.forEach(r => {
        needsAttention.push({
            severity: r.status === "degraded" ? "red" : "yellow",
            kind: "report", name: r.name,
            description: "has " + (r.status === "degraded" ? "degraded" : "at-risk") + " sources",
            timestamp: r.worst_source_updated, id: r.id, data: r,
        });
    });
    alerts.forEach(a => {
        const srcShort = a.source_name ? shortNameFromPath(a.source_name) : "";
        needsAttention.push({
            severity: a.severity === "critical" ? "red" : "yellow",
            kind: "alert", name: srcShort || "Alert",
            description: a.message, timestamp: a.created_at, id: a.id, data: a,
        });
    });
    needsAttention.sort((a, b) => {
        const sev = { red: 0, yellow: 1 };
        if (sev[a.severity] !== sev[b.severity]) return sev[a.severity] - sev[b.severity];
        const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
        const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
        return tb - ta;
    });
    const ATTENTION_LIMIT = 8;

    // Store for click-through navigation
    window._dashboardSources = sources;
    window._dashboardReports = reports;

    return `
        <div class="stat-grid">
            <div class="stat-card card-blue stat-card-clickable${data.sources_outdated > 0 ? ' pulse-border-red' : ''}" data-navigate="sources">
                <div class="stat-label">Total Sources</div>
                <div class="stat-value">${data.sources_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green stat-filter" data-filter="healthy">${data.sources_fresh} healthy</span>
                    <span class="stat-dot dot-yellow stat-filter" data-filter="at risk">${data.sources_stale} at risk</span>
                    <span class="stat-dot dot-red stat-filter" data-filter="degraded">${data.sources_outdated} degraded</span>
                    ${data.sources_unknown ? `<span class="stat-dot dot-muted stat-filter" data-filter="unknown">${data.sources_unknown} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card card-purple stat-card-clickable" data-navigate="reports">
                <div class="stat-label">Reports</div>
                <div class="stat-value">${data.reports_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green">${reports.filter(r => r.status === "healthy").length} healthy</span>
                    <span class="stat-dot dot-yellow">${reports.filter(r => r.status === "at risk").length} at risk</span>
                    <span class="stat-dot dot-red">${reports.filter(r => r.status === "degraded").length} degraded</span>
                    ${reports.filter(r => r.status === "unknown").length ? `<span class="stat-dot dot-muted">${reports.filter(r => r.status === "unknown").length} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card ${data.alerts_active > 0 ? 'card-red pulse-border-red' : 'card-green'} stat-card-clickable" data-navigate="issues">
                <div class="stat-label">Active Alerts</div>
                <div class="stat-value">${data.alerts_active}</div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card card-green stat-card-clickable" data-navigate="scanner">
                <div class="stat-label">Last Scan</div>
                <div class="stat-value" style="font-size:1.1rem">${scan ? timeAgo(scan.started_at) : "never"}</div>
                ${scan ? `<div class="stat-breakdown">${scan.reports_scanned} reports &middot; ${scan.sources_found} sources</div>` : ""}
                <div class="stat-card-link">View &rarr;</div>
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
                ${freshPct > 0 ? `<div class="segment segment-green segment-clickable" data-tooltip="${data.sources_fresh} healthy (${freshPct}%)" data-filter="healthy" style="width:${freshPct}%"></div>` : ""}
                ${stalePct > 0 ? `<div class="segment segment-yellow segment-clickable" data-tooltip="${data.sources_stale} at risk (${stalePct}%)" data-filter="at risk" style="width:${stalePct}%"></div>` : ""}
                ${outdatedPct > 0 ? `<div class="segment segment-red segment-clickable" data-tooltip="${data.sources_outdated} degraded (${outdatedPct}%)" data-filter="degraded" style="width:${outdatedPct}%"></div>` : ""}
                ${unknownPct > 0 ? `<div class="segment segment-muted" data-tooltip="${data.sources_unknown || 0} unknown (${unknownPct}%)" style="width:${unknownPct}%"></div>` : ""}
            </div>
            <div class="health-tooltip" id="health-tooltip"></div>
            <div class="health-legend">
                <span class="stat-dot dot-green">${data.sources_fresh} Healthy</span>
                <span class="stat-dot dot-yellow">${data.sources_stale} At Risk</span>
                <span class="stat-dot dot-red">${data.sources_outdated} Degraded</span>
                ${data.sources_unknown ? `<span class="stat-dot dot-muted">${data.sources_unknown} Unknown</span>` : ""}
            </div>
            `}
        </div>

        <div class="alert-trend-container" style="position:relative">
            <h2>Health Trend <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">past 30 days</span></h2>
            <canvas id="health-trend-canvas" height="140"></canvas>
            <div id="health-trend-tooltip" class="chart-tooltip"></div>
        </div>

        <div class="section" style="margin-top:1.5rem">
            <h2>Needs Attention${needsAttention.length > 0 ? ` <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">(${needsAttention.length})</span>` : ""}</h2>
            ${needsAttention.length > 0 ? `
                <div class="alert-list" id="attention-list">
                    ${needsAttention.slice(0, ATTENTION_LIMIT).map(item => `
                        <div class="alert-item attention-clickable" data-kind="${item.kind}" data-id="${item.id}">
                            <div class="dot dot-${item.severity}"></div>
                            <span class="attention-kind-badge kind-${item.kind}">${item.kind}</span>
                            <span><strong>${item.name}</strong> &mdash; ${item.description}</span>
                            <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem;white-space:nowrap">${item.timestamp ? timeAgo(item.timestamp) : ""}</span>
                        </div>
                    `).join("")}
                </div>
                ${needsAttention.length > ATTENTION_LIMIT ? `
                    <div id="attention-overflow" style="display:none" class="alert-list">
                        ${needsAttention.slice(ATTENTION_LIMIT).map(item => `
                            <div class="alert-item attention-clickable" data-kind="${item.kind}" data-id="${item.id}">
                                <div class="dot dot-${item.severity}"></div>
                                <span class="attention-kind-badge kind-${item.kind}">${item.kind}</span>
                                <span><strong>${item.name}</strong> &mdash; ${item.description}</span>
                                <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem;white-space:nowrap">${item.timestamp ? timeAgo(item.timestamp) : ""}</span>
                            </div>
                        `).join("")}
                    </div>
                    <button class="btn-outline btn-sm" id="btn-show-all-attention" style="margin-top:0.5rem;font-size:0.72rem">Show all ${needsAttention.length} items</button>
                ` : ""}
            ` : allUnknown
                ? '<div class="empty-state">No issues detected &mdash; run a probe to check source freshness</div>'
                : '<div class="empty-state">All sources and reports are healthy</div>'
            }
        </div>
    `;
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

    const trend = window._healthTrend || [];
    if (trend.length === 0) return;

    const padL = 30, padR = 10, padT = 10, padB = 24;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;

    // Compute max total (stacked)
    const maxVal = Math.max(...trend.map(t => (t.healthy || 0) + (t.at_risk || 0) + (t.degraded || 0)), 1);

    // Grid lines
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    const gridSteps = Math.min(maxVal, 4);
    for (let i = 0; i <= gridSteps; i++) {
        const y = padT + chartH - (i / gridSteps) * chartH;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
        ctx.fillStyle = "rgba(255,255,255,0.3)";
        ctx.font = "10px Inter, sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(Math.round(i / gridSteps * maxVal), padL - 4, y + 3);
    }

    // Helper to get x position for index
    const xAt = (i) => padL + (i / (trend.length - 1)) * chartW;
    const yAt = (val) => padT + chartH - (val / maxVal) * chartH;

    // Build stacked y-values per point
    const series = trend.map(t => ({
        degraded: t.degraded || 0,
        at_risk: t.at_risk || 0,
        healthy: t.healthy || 0,
    }));

    // Draw stacked areas (bottom to top: degraded, at_risk, healthy)
    // degraded: from 0 to degraded
    // at_risk: from degraded to degraded+at_risk
    // healthy: from degraded+at_risk to total

    const colors = {
        healthy: { fill: "rgba(34, 197, 94, 0.18)", stroke: "#22c55e" },
        at_risk: { fill: "rgba(234, 179, 8, 0.18)", stroke: "#eab308" },
        degraded: { fill: "rgba(239, 68, 68, 0.18)", stroke: "#ef4444" },
    };

    // Draw areas (in order: healthy on top, then at_risk, then degraded at bottom)
    // But for stacked area, draw bottom-most first (degraded), then at_risk, then healthy

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

    // At risk area (middle)
    ctx.beginPath();
    for (let i = 0; i < trend.length; i++) {
        const x = xAt(i);
        const y = yAt(series[i].degraded + series[i].at_risk);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = trend.length - 1; i >= 0; i--) {
        ctx.lineTo(xAt(i), yAt(series[i].degraded));
    }
    ctx.closePath();
    ctx.fillStyle = colors.at_risk.fill;
    ctx.fill();

    // Healthy area (top)
    ctx.beginPath();
    for (let i = 0; i < trend.length; i++) {
        const x = xAt(i);
        const total = series[i].degraded + series[i].at_risk + series[i].healthy;
        const y = yAt(total);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = trend.length - 1; i >= 0; i--) {
        ctx.lineTo(xAt(i), yAt(series[i].degraded + series[i].at_risk));
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
    drawLine(i => series[i].degraded + series[i].at_risk + series[i].healthy, colors.healthy.stroke);
    drawLine(i => series[i].degraded + series[i].at_risk, colors.at_risk.stroke);
    drawLine(i => series[i].degraded, colors.degraded.stroke);

    // X-axis labels (every 7 days)
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = "9px Inter, sans-serif";
    ctx.textAlign = "center";
    trend.forEach((t, i) => {
        if (i % 7 === 0 || i === trend.length - 1) {
            const x = xAt(i);
            const parts = t.day.split("-");
            ctx.fillText(`${parts[2]}/${parts[1]}`, x, H - 4);
        }
    });

    // Legend
    ctx.font = "9px Inter, sans-serif";
    const legendX = padL + 4;
    const legendY = padT + 10;
    [
        { color: colors.healthy.stroke, label: "Healthy" },
        { color: colors.at_risk.stroke, label: "At Risk" },
        { color: colors.degraded.stroke, label: "Degraded" },
    ].forEach((item, idx) => {
        const x = legendX + idx * 72;
        ctx.fillStyle = item.color;
        ctx.fillRect(x, legendY - 6, 8, 8);
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        ctx.textAlign = "left";
        ctx.fillText(item.label, x + 11, legendY + 1);
    });

    // Store chart geometry for tooltip
    window._healthChartGeom = { padL, padR, padT, padB, chartW, chartH, trend, series, xAt, yAt, maxVal, canvasRect: rect };

    // Tooltip handler
    canvas.addEventListener("mousemove", _healthChartMouseMove);
    canvas.addEventListener("mouseleave", () => {
        const tip = document.getElementById("health-trend-tooltip");
        if (tip) tip.style.display = "none";
    });
}

function _healthChartMouseMove(e) {
    const g = window._healthChartGeom;
    if (!g) return;
    const tip = document.getElementById("health-trend-tooltip");
    if (!tip) return;

    const rect = e.target.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    // Find nearest data point index
    if (mx < g.padL || mx > g.padL + g.chartW) { tip.style.display = "none"; return; }
    const ratio = (mx - g.padL) / g.chartW;
    const idx = Math.round(ratio * (g.trend.length - 1));
    if (idx < 0 || idx >= g.trend.length) { tip.style.display = "none"; return; }

    const t = g.trend[idx];
    const s = g.series[idx];
    const total = s.healthy + s.at_risk + s.degraded;
    const parts = t.day.split("-");
    const dayLabel = `${parts[2]}/${parts[1]}/${parts[0]}`;

    tip.innerHTML = `<div style="font-weight:600;margin-bottom:3px">${dayLabel}</div>
        <div><span style="color:#22c55e">&#9679;</span> Healthy: ${s.healthy}</div>
        <div><span style="color:#eab308">&#9679;</span> At Risk: ${s.at_risk}</div>
        <div><span style="color:#ef4444">&#9679;</span> Degraded: ${s.degraded}</div>
        <div style="border-top:1px solid rgba(255,255,255,0.15);margin-top:3px;padding-top:3px;color:var(--text-dim)">Total: ${total}</div>`;
    tip.style.display = "block";

    // Position tooltip near cursor
    const tipX = Math.min(mx + 12, rect.width - 140);
    const tipY = Math.max(my - 70, 0);
    tip.style.left = tipX + "px";
    tip.style.top = tipY + "px";

    // Draw vertical guideline
    const canvas = e.target;
    const ctx = canvas.getContext("2d");
    // Redraw chart then overlay guideline
    drawHealthTrendChart.__lastHoverIdx = idx;
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
        { key: "status", label: "Status", render: s => {
            let b = statusBadge(s.status);
            if (s.custom_fresh_days != null) b += ' <span style="font-size:0.65rem;color:var(--blue)" title="Custom freshness rule active">*</span>';
            return b;
        }, sortVal: s => ({ fresh: "0_healthy", stale: "1_at risk", outdated: "2_degraded", unknown: "3_unknown", no_connection: "3_no_connection" })[s.status] ?? "4_" + s.status },
        { key: "last_updated", label: "Last Updated", render: s => `<span style="color:var(--text-muted)" title="${s.last_updated || ''}">${s.last_updated ? timeAgo(s.last_updated) : "-"}</span>`, sortVal: s => s.last_updated || "" },
        { key: "report_count", label: "Reports", sortVal: s => s.report_count || 0 },
        { key: "owner", label: "Owner", render: s => s.owner === "Multiple"
            ? `<span style="color:var(--text-muted);cursor:help;border-bottom:1px dotted var(--text-dim)" title="Source is used by multiple report owners">${s.owner}</span>`
            : `<span style="color:var(--text-muted)">${s.owner || "-"}</span>` },
        { key: "refresh_schedule", label: "Frequency", render: s => {
            const days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
            const current = s.refresh_schedule ? `Weekly - ${s.refresh_schedule}` : "";
            const opts = days.map(d => {
                const val = `Weekly - ${d}`;
                return `<option value="${d}"${s.refresh_schedule === d ? ' selected' : ''}>${val}</option>`;
            }).join("");
            return `<select class="freq-select-inline source-freq-select" data-source-id="${s.id}">
                ${s.refresh_schedule ? '' : '<option value="">-</option>'}${opts}
            </select>`;
        }},
    ];

    const healthy = sources.filter(s => s.status === "fresh").length;
    const atRiskCount = sources.filter(s => s.status === "stale").length;
    const degradedCount = sources.filter(s => s.status === "outdated").length;

    return `
        <div class="page-header">
            <h1>Sources</h1>
            <span class="subtitle">${sources.length} data sources tracked &mdash; ${healthy} healthy, ${atRiskCount} at risk, ${degradedCount} degraded</span>
            <button class="btn-export" onclick="exportTableCSV('dt-sources','sources.csv')">Export CSV</button>
        </div>
        ${dataTable("dt-sources", cols, sources, { onRowClick: showSourceDetail })}
    `;
}

function bindSourcesPage() {
    // Inline frequency select dropdowns for sources
    document.querySelectorAll(".source-freq-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const sourceId = sel.dataset.sourceId;
            const day = sel.value;
            if (!day) return;
            try {
                await apiPatch(`/api/sources/${sourceId}`, { refresh_schedule: day });
                toast("Frequency updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });
}

async function renderReports() {
    const [reports, edges, sources] = await Promise.all([
        api("/api/reports"),
        api("/api/lineage"),
        api("/api/sources"),
    ]);

    const cols = [
        { key: "name", label: "Report", render: r => `<strong>${r.name}</strong>` },
        { key: "status", label: "Status", render: r => statusBadge(r.status), sortVal: r => ({ healthy: "0_healthy", "at risk": "1_at risk", degraded: "2_degraded" })[r.status] ?? "3_" + r.status },
        { key: "source_count", label: "Sources", sortVal: r => r.source_count || 0 },
        { key: "owner", label: "Report Owner", render: r => `<span style="color:var(--text-muted)">${r.owner || "-"}</span>` },
        { key: "business_owner", label: "Business Owner", render: r => `<span style="color:var(--text-muted)">${r.business_owner || "-"}</span>` },
        { key: "frequency", label: "Frequency", render: r => {
            const days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
            const opts = days.map(d => {
                const val = `Weekly - ${d}`;
                return `<option value="${val}"${r.frequency === val ? ' selected' : ''}>${val}</option>`;
            }).join("");
            return `<select class="freq-select-inline" data-report-id="${r.id}">
                ${r.frequency ? '' : '<option value="">Choose...</option>'}${opts}
            </select>`;
        }},
    ];

    const healthy = reports.filter(r => r.status === "healthy").length;
    const atRisk = reports.filter(r => r.status !== "healthy" && r.status !== "unknown").length;

    // Store sources for inline expansion lookups
    window._reportPageSources = sources;

    return `
        <div class="page-header">
            <h1>Reports</h1>
            <span class="subtitle">${reports.length} Power BI reports &mdash; ${healthy} healthy, ${atRisk} need attention</span>
            <button class="btn-export" onclick="exportTableCSV('dt-reports','reports.csv')">Export CSV</button>
        </div>

        ${dataTable("dt-reports", cols, reports, { onRowClick: showReportDetail })}
    `;
}

function bindReportsPage() {
    // Inline frequency select dropdowns
    document.querySelectorAll(".freq-select-inline").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const reportId = sel.dataset.reportId;
            const freq = sel.value;
            if (!freq) return;
            try {
                await apiPatch(`/api/reports/${reportId}`, { frequency: freq });
                toast("Frequency updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
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
                    { key: "fresh", label: "Healthy", render: r => r.fresh ? `<span style="color:var(--green)">${r.fresh}</span>` : '-' },
                    { key: "stale", label: "At Risk", render: r => r.stale ? `<span style="color:var(--yellow)">${r.stale}</span>` : '-' },
                    { key: "outdated", label: "Degraded", render: r => r.outdated ? `<span style="color:var(--red)">${r.outdated}</span>` : '-' },
                ], probeRuns) : '<div class="empty-state">No probes yet. Click "Probe Sources" to check freshness.</div>'}
            </div>
        </div>
    `;
}

async function renderAlerts() {
    const [alerts, owners] = await Promise.all([
        api("/api/alerts?active_only=false"),
        api("/api/alerts/owners/list"),
    ]);
    window._alertOwners = owners;

    const cols = [
        { key: "severity", label: "Severity", render: a => statusBadge(a.severity), sortVal: a => ({ critical: "0_critical", warning: "1_warning" })[a.severity] ?? "2_" + a.severity },
        { key: "message", label: "Message", render: a => {
            const srcShort = a.source_name ? shortNameFromPath(a.source_name) : "";
            return srcShort ? `<strong>${srcShort}</strong> &mdash; ${a.message}` : a.message;
        }},
        { key: "assigned_to", label: "Owner", render: a => {
            const opts = (window._alertOwners || []).map(o =>
                `<option value="${o}"${a.assigned_to === o ? ' selected' : ''}>${o}</option>`
            ).join("");
            return `<select class="alert-owner-select" data-alert-id="${a.id}">
                <option value="">Unassigned</option>${opts}
            </select>`;
        }, sortVal: a => a.assigned_to || "zzz_unassigned" },
        { key: "created_at", label: "When", render: a => `<span style="color:var(--text-muted)" title="${formatDate(a.created_at)}">${timeAgo(a.created_at)}</span>`, sortVal: a => a.created_at || "" },
        { key: "resolution_status", label: "Status", render: a => {
            const reasonAttr = a.resolution_reason ? ` title="${a.resolution_reason.replace(/"/g, '&quot;')}"` : "";
            if (a.resolution_status === "acknowledged") {
                return `<span class="badge badge-blue"${reasonAttr}>acknowledged</span> <button class="btn-sm btn-outline btn-reopen-alert" data-alert-id="${a.id}">Reopen</button>`;
            }
            if (a.resolution_status === "resolved") {
                return `<span class="badge badge-green"${reasonAttr}>resolved</span> <button class="btn-sm btn-outline btn-reopen-alert" data-alert-id="${a.id}">Reopen</button>`;
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
                await fetch(url, { method: "PATCH" });
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
                await navigate("issues");
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
            const indColor = a.type.includes("outdated") || a.type.includes("error") || a.type.includes("degraded") ? "ind-red"
                           : a.type.includes("stale") || a.type.includes("at_risk") ? "ind-yellow"
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
            <button class="action-filter-btn" data-filter="expected" title="Sources that are intentionally at-risk/degraded (e.g. quarterly data)">Expected (${actions.filter(a => a.status === "expected").length})</button>
        </div>

        <div id="action-list">
            ${renderActionCards("all")}
        </div>
    `;
    return { html, open, total: actions.length };
}

async function renderIssues() {
    const [alertsData, discData] = await Promise.all([
        renderAlerts(),
        api("/api/schedules/discrepancies"),
    ]);

    const discHtml = renderDiscrepancies(discData);

    return `
        <div class="page-header">
            <h1>Issues</h1>
            <span class="subtitle">${alertsData.active} active &middot; ${alertsData.acked} acknowledged &middot; ${alertsData.resolved} resolved &middot; ${discData.summary.discrepancy_count} schedule</span>
            <button class="btn-export" onclick="exportTableCSV('dt-alerts','issues.csv')">Export CSV</button>
        </div>

        <h2>Data Freshness Alerts</h2>
        ${alertsData.html}

        <h2 style="margin-top:2rem">Schedule Discrepancies
            <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">
                (${discData.summary.discrepancy_count} of ${discData.summary.total_chains} chains)
            </span>
        </h2>
        <p style="color:var(--text-dim);font-size:0.78rem;margin-bottom:0.75rem">
            Flags refresh chains where the timing order is broken:
            Upstream System &rarr; Source &rarr; Report (Sunday)
        </p>
        ${discHtml}
    `;
}

function renderDiscrepancies(data) {
    if (!data.discrepancies || data.discrepancies.length === 0) {
        return '<div class="empty-state">No schedule discrepancies detected</div>';
    }

    const cols = [
        { key: "severity", label: "Severity",
          render: d => {
              const worst = d.issues.some(i => i.severity === "critical") ? "critical" : "warning";
              return statusBadge(worst);
          },
          sortVal: d => d.issues.some(i => i.severity === "critical") ? "0_critical" : "1_warning"
        },
        { key: "upstream_name", label: "Upstream System",
          render: d => `<span style="color:var(--text-muted)">${d.upstream_name}</span>
                        <span class="badge badge-muted" style="font-size:0.65rem;margin-left:0.3rem">${d.upstream_refresh_date || d.upstream_refresh_day}</span>`
        },
        { key: "source_name", label: "Source",
          render: d => {
              const short = shortNameFromPath(d.source_name) || d.source_name;
              return `<strong>${short}</strong>
                      <span class="badge badge-muted" style="font-size:0.65rem;margin-left:0.3rem">${d.source_refresh_date || d.source_refresh_day}</span>`;
          }
        },
        { key: "report_name", label: "Report",
          render: d => `<span style="color:var(--text-muted)">${d.report_name}</span>
                        <span class="badge badge-muted" style="font-size:0.65rem;margin-left:0.3rem">${d.report_refresh_date || 'Sunday'}</span>`
        },
        { key: "issue", label: "Issue",
          render: d => d.issues.map(i => `<div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:0.2rem">${i.message}</div>`).join("")
        },
    ];

    return dataTable("dt-discrepancies", cols, data.discrepancies);
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
    const statusClass = r.status === "healthy" ? "dot-green"
        : r.status === "at risk" ? "dot-yellow"
        : r.status === "degraded" ? "dot-red"
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
        if (sources.some(s => s.status === "outdated" || s.status === "error" || s.status === "degraded")) return 0;
        if (sources.some(s => s.status === "stale" || s.status === "at risk")) return 1;
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

    const statusSummary = { healthy: 0, at_risk: 0, degraded: 0, unknown: 0 };
    reportSources.forEach(s => {
        if (s.status === "fresh") statusSummary.healthy++;
        else if (s.status === "stale") statusSummary.at_risk++;
        else if (s.status === "outdated" || s.status === "error") statusSummary.degraded++;
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
                ${statusSummary.healthy ? `<span class="health-pill pill-green">${statusSummary.healthy} healthy</span>` : ""}
                ${statusSummary.at_risk ? `<span class="health-pill pill-yellow">${statusSummary.at_risk} at risk</span>` : ""}
                ${statusSummary.degraded ? `<span class="health-pill pill-red">${statusSummary.degraded} degraded</span>` : ""}
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
            if (activeFilter === "unhealthy" && r.status === "healthy") return false;
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
    const firstUnhealthy = data.sortedReports.find(r => r.status !== "healthy");
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
            const time = e.date ? new Date(e.date).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) : "";
            return `
                <div class="changelog-item">
                    <div class="changelog-time">${time}</div>
                    <div class="changelog-body">
                        <div class="changelog-title">${e.title}</div>
                        <div class="changelog-desc">${e.description}</div>
                    </div>
                    <span class="changelog-commit">${e.commit}</span>
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

    return `
        <div class="page-header">
            <h1>Changelog</h1>
            <span class="subtitle">${entries.length} updates</span>
        </div>
        <div class="changelog-list">${rows || '<div style="color:var(--text-muted)">No changelog entries found.</div>'}</div>
    `;
}


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    sources: renderSources,
    reports: renderReports,
    scanner: renderScanner,
    issues: renderIssues,
    changelog: renderChangelog,
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
            // Update nav health dot
            const navDot = document.getElementById("nav-health-dot");
            const dd = window._dashboardData;
            if (navDot && dd) {
                if (dd.sources_outdated > 0 || dd.alerts_active > 5) navDot.style.background = "var(--red)"; // degraded
                else if (dd.sources_stale > 0) navDot.style.background = "var(--yellow)"; // at risk
                else navDot.style.background = "var(--green)"; // healthy
            }
            const btnShowAll = document.getElementById("btn-show-all-attention");
            if (btnShowAll) {
                btnShowAll.addEventListener("click", () => {
                    const overflow = document.getElementById("attention-overflow");
                    if (overflow) {
                        const showing = overflow.style.display !== "none";
                        overflow.style.display = showing ? "none" : "";
                        btnShowAll.textContent = showing ? `Show all ${document.querySelectorAll("#attention-list .attention-clickable, #attention-overflow .attention-clickable").length} items` : "Show less";
                    }
                });
            }
            // Clickable stat card sub-labels — filter navigation
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
            // Clickable stat cards — navigate to target page
            document.querySelectorAll(".stat-card-clickable[data-navigate]").forEach(card => {
                card.addEventListener("click", () => navigate(card.dataset.navigate));
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
            // Clickable attention items — drill down to source/report/alert detail
            document.querySelectorAll(".attention-clickable").forEach(el => {
                el.addEventListener("click", async () => {
                    const kind = el.dataset.kind;
                    const id = parseInt(el.dataset.id);
                    if (kind === "source") {
                        const src = (window._dashboardSources || []).find(s => s.id === id);
                        if (src) { await navigate("sources"); showSourceDetail(src); }
                    } else if (kind === "report") {
                        const rpt = (window._dashboardReports || []).find(r => r.id === id);
                        if (rpt) { await navigate("reports"); showReportDetail(rpt); }
                    } else if (kind === "alert") {
                        await navigate("issues");
                    }
                });
            });
            // Draw health trend chart
            drawHealthTrendChart();
        }
        if (page === "scanner") bindScannerButtons();
        if (page === "issues") { bindIssuesPage(); }
        if (page === "sources") bindSourcesPage();
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
                <p>Hi! I can help you understand your data governance ecosystem. Ask me about risks, source health, or specific reports.</p>
            </div>
        </div>
        <div class="ai-chat-quick" id="ai-chat-quick">
            <button class="ai-quick-chip" data-q="What's at risk?">What's at risk?</button>
            <button class="ai-quick-chip" data-q="Summarize dashboard">Summarize dashboard</button>
            <button class="ai-quick-chip" data-q="Show at-risk sources">Show at-risk sources</button>
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
