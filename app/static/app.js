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

function esc(str) {
    if (str == null) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

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

function _filterAndSortDT(dt) {
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
    return rows;
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
    const { columns, sortCol, sortDir } = dt;
    let rows = _filterAndSortDT(dt);
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
                <td><strong>${esc(r.name)}</strong></td>
                <td style="color:var(--text-muted)">${esc(r.table_name) || "-"}</td>
                <td style="color:var(--text-muted)">${esc(r.owner) || "-"}</td>
            </tr>
        `).join("")
        : '<tr><td colspan="3" class="empty-state" style="border:none">No reports linked to this source</td></tr>';

    const hasCustomRule = source.custom_fresh_days != null;
    const freshVal = source.custom_fresh_days || "";
    const staleVal = source.custom_stale_days || "";

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(parsed.shortName)}</h2>
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

    const [tables, visuals, unusedData] = await Promise.all([
        api(`/api/reports/${report.id}/tables`),
        api(`/api/reports/${report.id}/visuals`).catch(() => []),
        api(`/api/reports/${report.id}/unused`).catch(() => ({ total_measures: 0, total_columns: 0, total_fields: 0, unused_measures: [], unused_columns: [], unused_tables: [], unused_fields_count: 0, unused_pct: 0, total_tables: 0, unused_tables_count: 0 })),
    ]);

    // Look up full source objects from the sources we fetched
    const allSources = window._reportPageSources || [];
    const sourceMap = new Map();
    allSources.forEach(s => sourceMap.set(s.id, s));

    // Count how many visuals reference each table
    const tableVisualCount = new Map();
    visuals.forEach(page => {
        page.visuals.forEach(v => {
            v.fields.forEach(f => {
                tableVisualCount.set(f.table, (tableVisualCount.get(f.table) || 0) + 1);
            });
        });
    });

    const colCount = targetRow ? targetRow.children.length : 6;
    const expandRow = document.createElement("tr");
    expandRow.className = "report-expand-row";
    expandRow.dataset.reportId = report.id;

    const sourceRows = tables.length > 0
        ? tables.map(t => {
            const src = t.source_id ? sourceMap.get(t.source_id) : null;
            const srcName = src ? (shortNameFromPath(src.name) || src.name) : (t.source_name || "no linked source");
            const srcStatus = src ? src.status : "unknown";
            const vCount = tableVisualCount.get(t.table_name) || 0;
            return `<div class="report-source-item${src ? ' report-source-clickable' : ''}" ${src ? `data-source-id="${src.id}"` : ''}>
                <span class="dot dot-${srcStatus === 'fresh' || srcStatus === 'healthy' ? 'green' : srcStatus === 'stale' || srcStatus === 'at risk' ? 'yellow' : srcStatus === 'outdated' || srcStatus === 'degraded' ? 'red' : 'muted'}" style="width:6px;height:6px"></span>
                <span class="report-source-table">${t.table_name}</span>
                ${vCount > 0 ? `<span class="badge badge-muted" style="margin-left:0.25rem;font-size:0.65rem">${vCount} visual${vCount !== 1 ? 's' : ''}</span>` : ''}
                <span class="report-source-arrow">&rarr;</span>
                <span class="report-source-name">${srcName}</span>
                ${src ? statusBadge(src.status) : ''}
                ${src && src.last_updated ? `<span style="color:var(--text-dim);font-size:0.72rem;margin-left:auto">${timeAgo(src.last_updated)}</span>` : ''}
            </div>`;
        }).join("")
        : '<div class="empty-state" style="padding:0.5rem">No tables found</div>';

    const totalVisuals = visuals.reduce((a, p) => a + p.visuals.length, 0);

    // Unused measures/columns section
    const hasUnusedData = unusedData.total_fields > 0 || unusedData.total_tables > 0;
    const unusedMC = unusedData.unused_measures.length + unusedData.unused_columns.length;
    const unusedSection = hasUnusedData ? `
        <div class="report-expand-label unused-toggle" style="margin-top:0.75rem;cursor:pointer" data-target="unused-mc">
            Unused Measures / Columns (${unusedMC} of ${unusedData.total_fields})
            ${unusedMC > 0
                ? `<span class="badge badge-yellow" style="margin-left:0.35rem;font-size:0.62rem">${unusedData.unused_pct}%</span>`
                : `<span class="badge badge-green" style="margin-left:0.35rem;font-size:0.62rem">all used</span>`}
            <span class="unused-toggle-hint" style="font-size:0.7rem;color:var(--text-dim)"> — click to expand</span>
        </div>
        <div class="unused-measures-list" id="unused-mc" style="display:none">
            ${unusedData.unused_measures.length > 0 ? `
                <div style="font-size:0.68rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin:0.3rem 0 0.15rem 0.2rem">Measures (${unusedData.unused_measures.length})</div>
                ${unusedData.unused_measures.map(m => `
                    <div class="unused-measure-item">
                        <span class="unused-measure-name">${m.name}</span>
                        <span class="unused-measure-table">${m.table_name}</span>
                        ${m.dax ? `<span class="unused-measure-dax" style="display:none">${m.dax.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>` : ''}
                    </div>`).join('')}` : ''}
            ${unusedData.unused_columns.length > 0 ? `
                <div style="font-size:0.68rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin:0.3rem 0 0.15rem 0.2rem">Columns (${unusedData.unused_columns.length})</div>
                ${unusedData.unused_columns.map(c => `
                    <div class="unused-measure-item">
                        <span class="unused-measure-name">${c.name}</span>
                        <span class="unused-measure-table">${c.table_name}</span>
                    </div>`).join('')}` : ''}
            ${unusedMC === 0 ? '<div style="padding:0.4rem;color:var(--green);font-size:0.78rem">All measures and columns are used in visuals</div>' : ''}
        </div>
        <div class="report-expand-label unused-toggle" style="margin-top:0.5rem;cursor:pointer" data-target="unused-tables">
            Unused Tables (${unusedData.unused_tables_count} of ${unusedData.total_tables})
            ${unusedData.unused_tables_count > 0
                ? `<span class="badge badge-yellow" style="margin-left:0.35rem;font-size:0.62rem">${Math.round(unusedData.unused_tables_count / unusedData.total_tables * 100)}%</span>`
                : `<span class="badge badge-green" style="margin-left:0.35rem;font-size:0.62rem">all used</span>`}
            <span class="unused-toggle-hint" style="font-size:0.7rem;color:var(--text-dim)"> — click to expand</span>
        </div>
        <div class="unused-measures-list" id="unused-tables" style="display:none">
            ${unusedData.unused_tables.length > 0
                ? unusedData.unused_tables.map(t => `
                    <div class="unused-measure-item"><span class="unused-measure-name">${t}</span></div>`).join('')
                : '<div style="padding:0.4rem;color:var(--green);font-size:0.78rem">All tables are referenced by visuals</div>'}
        </div>
    ` : '';

    expandRow.innerHTML = `<td colspan="${colCount}" class="report-expand-cell">
        <div class="report-expand-content">
            <div class="report-expand-header">
                <div class="detail-grid" style="margin-bottom:0.5rem">
                    <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(report.status)}</div>
                    <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${report.owner || "-"}</span></div>
                    <div class="detail-item"><div class="detail-label">Business Owner</div><span style="color:var(--text)">${report.business_owner || "-"}</span></div>
                    <div class="detail-item"><div class="detail-label">Frequency</div><span style="color:var(--text)">${report.frequency || "-"}</span></div>
                </div>
            </div>
            <div class="report-expand-label">Data Sources (${tables.length})</div>
            <div class="report-source-list">${sourceRows}</div>
            ${unusedSection}
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

    // Bind unused section toggles
    expandRow.querySelectorAll(".unused-toggle[data-target]").forEach(toggle => {
        toggle.addEventListener("click", () => {
            const list = expandRow.querySelector(`#${toggle.dataset.target}`);
            if (!list) return;
            const showing = list.style.display !== "none";
            list.style.display = showing ? "none" : "";
            const hint = toggle.querySelector(".unused-toggle-hint");
            if (hint) hint.textContent = showing ? " — click to expand" : " — click to collapse";
        });
    });

    // Bind unused measure items — click to show/hide DAX
    expandRow.querySelectorAll(".unused-measure-item").forEach(el => {
        const dax = el.querySelector(".unused-measure-dax");
        if (dax) {
            el.style.cursor = "pointer";
            el.addEventListener("click", () => {
                dax.style.display = dax.style.display === "none" ? "block" : "none";
            });
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
    const [data, sources, reports, alerts, healthTrend, impactData] = await Promise.all([
        api("/api/dashboard"),
        api("/api/sources"),
        api("/api/reports"),
        api("/api/alerts?active_only=true"),
        api("/api/schedules/health-trend"),
        api("/api/dashboard/impact"),
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

    // Store for click-through navigation
    window._dashboardSources = sources;
    window._dashboardReports = reports;

    return `
        <div class="stat-grid">
            <div class="stat-card card-purple stat-card-clickable" data-navigate="reports" role="button" tabindex="0" aria-label="Reports: ${data.reports_total} total">
                <div class="stat-label">Reports</div>
                <div class="stat-value">${data.reports_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green" title="All data sources are fresh and up to date">${reports.filter(r => r.status === "healthy").length} healthy</span>
                    <span class="stat-dot dot-yellow" title="Some data sources are 31–90 days old">${reports.filter(r => r.status === "at risk").length} at risk</span>
                    <span class="stat-dot dot-red" title="Data sources are older than 90 days">${reports.filter(r => r.status === "degraded").length} degraded</span>
                    ${reports.filter(r => r.status === "unknown").length ? `<span class="stat-dot dot-muted" title="Status has not been probed yet">${reports.filter(r => r.status === "unknown").length} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card card-blue stat-card-clickable${data.sources_outdated > 0 ? ' pulse-border-red' : ''}" data-navigate="sources" role="button" tabindex="0" aria-label="Total Sources: ${data.sources_total}, ${data.sources_fresh} healthy, ${data.sources_stale} at risk, ${data.sources_outdated} degraded">
                <div class="stat-label">Total Sources</div>
                <div class="stat-value">${data.sources_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green stat-filter" data-filter="healthy" title="Data updated within the last 30 days">${data.sources_fresh} healthy</span>
                    <span class="stat-dot dot-yellow stat-filter" data-filter="at risk" title="Data is 31–90 days old — may need refresh">${data.sources_stale} at risk</span>
                    <span class="stat-dot dot-red stat-filter" data-filter="degraded" title="Data is older than 90 days — action required">${data.sources_outdated} degraded</span>
                    ${data.sources_unknown ? `<span class="stat-dot dot-muted stat-filter" data-filter="unknown" title="Source has not been probed yet">${data.sources_unknown} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card ${data.alerts_active > 0 ? 'card-red pulse-border-red' : 'card-green'} stat-card-clickable" data-navigate="dashboard" role="button" tabindex="0" aria-label="Active Alerts: ${data.alerts_active}">
                <div class="stat-label">Active Alerts</div>
                <div class="stat-value">${data.alerts_active}</div>
                <div class="stat-card-link">View &rarr;</div>
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
                <span style="color:var(--text-dim);font-size:0.78rem" title="Healthy = updated within 30 days. At risk = 31–90 days. Degraded = 90+ days.">${healthLabel}</span>
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

        <div class="dashboard-attention-row">
        <div class="section dashboard-attention-main">
            <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem">
                <h2 style="margin:0">Needs Attention${needsAttention.length > 0 ? ` <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">(${needsAttention.length})</span>` : ""}</h2>
                <div class="impact-toggle">
                    <button class="impact-toggle-btn active" data-view="timeline">Timeline</button>
                    <button class="impact-toggle-btn" data-view="impact">By Impact${impactData.length > 0 ? ` (${impactData.length})` : ""}</button>
                </div>
            </div>
            <div id="attention-timeline">
            ${needsAttention.length > 0 ? `
                <div class="alert-list attention-scrollable" id="attention-list">
                    ${needsAttention.map(item => `
                        <div class="alert-item attention-clickable" data-kind="${item.kind}" data-id="${item.id}">
                            <div class="dot dot-${item.severity}"></div>
                            <span class="attention-kind-badge kind-${item.kind}">${item.kind}</span>
                            <span><strong>${esc(item.name)}</strong> &mdash; ${esc(item.description)}</span>
                            <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem;white-space:nowrap">${item.timestamp ? timeAgo(item.timestamp) : ""}</span>
                        </div>
                    `).join("")}
                </div>
            ` : allUnknown
                ? '<div class="empty-state">No issues detected &mdash; run a probe to check source freshness</div>'
                : '<div class="empty-state">All sources and reports are healthy</div>'
            }
            </div>
            <div id="attention-impact" style="display:none">
            ${impactData.length > 0 ? `
                <div class="impact-list">
                    ${impactData.map(item => `
                        <div class="impact-item impact-clickable" data-source-id="${item.source_id}">
                            <div class="impact-header">
                                <div class="dot dot-${item.status === "outdated" || item.status === "error" ? "red" : "yellow"}"></div>
                                <strong>${esc(item.source_name)}</strong>
                                <span class="impact-status">${esc(item.status)}</span>
                                <span class="impact-badge">${item.affected_reports} report${item.affected_reports !== 1 ? "s" : ""}</span>
                                <span style="margin-left:auto;color:var(--text-dim);font-size:0.72rem">${item.last_data_at ? timeAgo(item.last_data_at) : ""}</span>
                            </div>
                            <div class="impact-reports">${item.report_names.map(n => esc(n)).join(", ")}</div>
                        </div>
                    `).join("")}
                </div>
            ` : '<div class="empty-state">No freshness issues affecting reports</div>'}
            </div>
        </div>
        <div class="dashboard-attention-side">
            <h2>Health Trend <span style="font-weight:400;font-size:0.78rem;color:var(--text-dim)">past 30 days</span></h2>
            <div class="alert-trend-container" style="position:relative">
                <canvas id="health-trend-canvas" height="200"></canvas>
                <div id="health-trend-tooltip" class="chart-tooltip"></div>
            </div>
        </div>
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

    // Tooltip handler — remove previous listeners to avoid accumulation
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
                    <span>${srcShort ? `<strong>${esc(srcShort)}</strong> &mdash; ` : ""}${esc(a.message)}</span>
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
        { key: "_shortName", label: "File / Table", render: s => `<strong>${esc(s._shortName)}</strong>`, sortVal: s => s._shortName || "" },
        { key: "_folderSchema", label: "Folder / Schema", render: s => `<span style="color:var(--text-muted);font-size:0.75rem">${s._folderSchema || "-"}</span>`, sortVal: s => s._folderSchema || "" },
        { key: "_fullLocation", label: "Full Location", resizable: true, render: s => {
            const loc = s._fullLocation || "-";
            const escaped = loc.replace(/"/g, '&quot;');
            return `<span class="cell-expandable cell-copyable" title="Click to copy path" data-copy="${escaped}">${loc}</span>`;
        }, sortVal: s => s._fullLocation || "" },
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
            if (!confirm(`Change refresh schedule to "${day}"?`)) {
                sel.value = sel.dataset.prev || "";
                return;
            }
            try {
                await apiPatch(`/api/sources/${sourceId}`, { refresh_schedule: day });
                sel.dataset.prev = day;
                toast("Frequency updated");
            } catch (err) {
                sel.value = sel.dataset.prev || "";
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });
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
}

async function renderReports() {
    const [reports, edges, sources] = await Promise.all([
        api("/api/reports"),
        api("/api/lineage"),
        api("/api/sources"),
    ]);

    const cols = [
        { key: "name", label: "Report", render: r => `<strong>${esc(r.name)}</strong>` },
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
        { key: "powerbi_url", label: "Power BI", filterable: false, sortable: false, render: r => r.powerbi_url
            ? `<a href="${r.powerbi_url}" target="_blank" rel="noopener" class="btn-table-link btn-pbi" title="Open in Power BI" onclick="event.stopPropagation()">Open</a>`
            : `<span class="btn-table-link btn-table-link-disabled">-</span>` },
        { key: "_lineage", label: "Lineage", filterable: false, sortable: false, render: r =>
            `<button class="btn-table-link btn-lineage" data-lineage-report="${r.id}" title="View lineage diagram" onclick="event.stopPropagation()">View</button>` },
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

    // Lineage button — navigate to Lineage tab with report pre-selected
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
            return srcShort ? `<strong>${esc(srcShort)}</strong> &mdash; ${esc(a.message)}` : esc(a.message);
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
    try { owners = await api("/api/alerts/owners/list"); } catch(e) {}

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
                           : a.type.includes("stale") || a.type.includes("at_risk") ? "ind-yellow"
                           : a.type.includes("broken") ? "ind-red"
                           : "ind-blue";

            const sourceName = a.source_name || "-";
            const shortSource = shortNameFromPath(sourceName) || sourceName;
            const currentOwner = a.assigned_to || "";

            return `
                <div class="action-card" data-action-id="${a.id}">
                    <div class="action-indicator ${indColor}"></div>
                    <div class="action-body">
                        <div class="action-title">${shortSource}</div>
                        <div class="action-meta">
                            ${actionTypeBadge(a.type)}
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
                <button class="action-filter-btn" data-filter="expected" title="Sources that are intentionally at-risk/degraded (e.g. quarterly data)">Expected (${actions.filter(a => a.status === "expected").length})</button>
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

    const flowchart = `
        <div class="flowchart-wrap">
            <div class="flowchart-title">MX Analytics Pipeline</div>
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
    // Placeholder — no interactive elements currently needed
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
        const freqOpts = (opts.report_frequencies || []).map(f => `<option value="${f}">${f}</option>`).join('');
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" placeholder="e.g. Weekly Sales Report" required></div>
            <div class="create-field"><label>Report Owner</label>
                <select id="cf-owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Business Owner</label>
                <select id="cf-business_owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Frequency</label>
                <select id="cf-frequency"><option value="">Choose...</option>${freqOpts}</select></div>
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

    const entityLabels = { source: 'Data Source', report: 'Report', upstream: 'Upstream Data Source' };
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
        body.frequency = document.getElementById('cf-frequency')?.value || null;
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
    const [options, customEntries] = await Promise.all([
        api("/api/create/options"),
        api("/api/create/custom-entries"),
    ]);
    window._createOptions = options;

    const entryTable = customEntries.length > 0 ? dataTable("dt-custom-entries", [
        { key: "entity_type", label: "Type", render: e => _entityTypeBadge(e.entity_type) },
        { key: "name", label: "Name", render: e => '<strong>' + e.name + '</strong>' },
        { key: "detail", label: "Detail", render: e => '<span style="color:var(--text-muted)">' + (e.detail || '-') + '</span>' },
        { key: "created_at", label: "Created", render: e => '<span style="color:var(--text-muted)" title="' + formatDate(e.created_at) + '">' + timeAgo(e.created_at) + '</span>', sortVal: e => e.created_at || '' },
        { key: "actions", label: "", render: e => `<div class="ce-actions">
            <button class="btn-sm btn-outline ce-edit-btn" data-id="${e.id}" data-type="${e.entity_type}">Edit</button>
            <button class="btn-sm btn-outline btn-danger-outline ce-delete-btn" data-id="${e.id}" data-type="${e.entity_type}">Delete</button>
        </div>` },
    ], customEntries) : '<div class="empty-state">No custom entries yet</div>';

    return `
        <div class="page-header">
            <h1>Create Entry</h1>
            <span class="subtitle">Manually add reports, data sources, and upstream systems</span>
        </div>

        <div class="create-type-selector">
            <button class="create-type-btn" data-entity="report">&#128196; Report</button>
            <button class="create-type-btn" data-entity="source">&#128451; Data Source</button>
            <button class="create-type-btn" data-entity="upstream">&#9650; Upstream System</button>
        </div>

        <div id="create-form-container">
            <div class="create-prompt" style="text-align:center;padding:2.5rem 1rem;color:var(--text-muted);font-size:0.9rem">
                <div style="font-size:1.8rem;margin-bottom:0.75rem;opacity:0.4">&#10010;</div>
                <div>Select a type above to create a new entry</div>
            </div>
        </div>

        <div class="section" style="margin-top:2rem">
            <h2 class="create-history-toggle" style="cursor:pointer;user-select:none">
                Custom Entries (${customEntries.length})
                <span style="font-size:0.72rem;font-weight:400;color:var(--text-dim)">&mdash; click to expand</span>
            </h2>
            <div id="create-history-body" style="display:none">
                ${entryTable}
            </div>
        </div>
    `;
}

function bindCreatePage() {
    document.querySelectorAll('.create-type-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.create-type-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const container = document.getElementById('create-form-container');
            container.innerHTML = _renderCreateForm(btn.dataset.entity);
            document.getElementById('btn-create-submit').addEventListener('click', _handleCreateSubmit);
            document.getElementById('btn-create-cancel').addEventListener('click', () => {
                container.innerHTML = '';
                document.querySelectorAll('.create-type-btn').forEach(b => b.classList.remove('active'));
            });
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
        const freqOpts = (opts.report_frequencies || []).map(f => `<option value="${f}" ${entity.frequency === f ? 'selected' : ''}>${f}</option>`).join('');
        const boOpts = (opts.owners || []).map(o => `<option value="${o}" ${entity.business_owner === o ? 'selected' : ''}>${o}</option>`).join('');
        fields = `
            <div class="create-field"><label>Name <span class="required">*</span></label>
                <input type="text" id="cf-name" value="${esc(entity.name)}" required></div>
            <div class="create-field"><label>Report Owner</label>
                <select id="cf-owner"><option value="">Choose...</option>${ownerOpts}</select></div>
            <div class="create-field"><label>Business Owner</label>
                <select id="cf-business_owner"><option value="">Choose...</option>${boOpts}</select></div>
            <div class="create-field"><label>Frequency</label>
                <select id="cf-frequency"><option value="">Choose...</option>${freqOpts}</select></div>
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
            body.frequency = document.getElementById('cf-frequency')?.value || null;
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
        { key: "severity", label: "Severity", render: f => _bpSevBadge(f.severity), sortVal: f => ({ high: "0_high", medium: "1_medium", low: "2_low" })[f.severity] || "3" },
        { key: "report", label: "Report" },
        { key: "owner", label: "Owner" },
        { key: "table", label: "Table" },
        { key: "rule", label: "Rule" },
        { key: "issue", label: "Issue", render: f => `<span style="white-space:normal;color:var(--text-secondary)">${f.issue}</span>` },
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
        { key: "severity", label: "Severity", render: f => _bpSevBadge(f.severity), sortVal: f => ({ high: "0_high", medium: "1_medium", low: "2_low" })[f.severity] || "3" },
        { key: "report", label: "Report" },
        { key: "owner", label: "Owner" },
        { key: "table", label: "Table" },
        { key: "rule", label: "Rule" },
        { key: "issue", label: "Issue", render: f => `<span style="white-space:normal;color:var(--text-secondary)">${f.issue}</span>` },
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


// ── Lineage Diagram ──

async function renderLineageDiagram() {
    const reports = await api("/api/reports");
    return `
        <div class="page-header">
            <h2>Lineage Diagram</h2>
            <p class="page-subtitle">Trace data flow from upstream systems to report visuals</p>
        </div>
        <div class="lineage-controls">
            <select id="lineage-report-select" class="lineage-dropdown">
                <option value="">Select a report...</option>
                ${reports.map(r => `<option value="${r.id}">${r.name}${r.status === "degraded" || r.status === "at risk" ? " \u26a0" : ""}</option>`).join("")}
            </select>
        </div>
        <div id="lineage-container" class="lineage-container">
            <div class="lineage-placeholder">Select a report above to view its data lineage</div>
        </div>
    `;
}

function bindLineageDiagramPage() {
    const sel = document.getElementById("lineage-report-select");
    if (!sel) return;
    sel.addEventListener("change", async () => {
        const id = sel.value;
        if (!id) {
            document.getElementById("lineage-container").innerHTML =
                '<div class="lineage-placeholder">Select a report above to view its data lineage</div>';
            return;
        }
        document.getElementById("lineage-container").innerHTML =
            '<div class="lineage-placeholder">Loading lineage...</div>';
        try {
            const data = await api(`/api/lineage/report/${id}/diagram`);
            _renderLineageDiagram(data);
        } catch (e) {
            document.getElementById("lineage-container").innerHTML =
                `<div class="lineage-placeholder" style="color:var(--red)">Error: ${e.message}</div>`;
        }
    });
}

function _renderLineageDiagram(data) {
    const container = document.getElementById("lineage-container");

    // Build unique nodes for each column
    const fieldMap = new Map(); // "Table.Field" -> { table, field }
    const visualNodes = [];     // { id, type, title, fields: ["T.F", ...], page }

    // Collect visuals with fields (skip empty visuals)
    for (const page of data.pages) {
        for (const v of page.visuals) {
            if (!v.fields || v.fields.length === 0) continue;
            const fieldKeys = v.fields.map(f => `${f.table}.${f.field}`);
            visualNodes.push({
                id: `visual-${v.visual_db_id}`,
                type: v.visual_type,
                title: v.title,
                fields: fieldKeys,
                page: page.page_name,
            });
            for (const f of v.fields) {
                const key = `${f.table}.${f.field}`;
                if (!fieldMap.has(key)) fieldMap.set(key, { table: f.table, field: f.field });
            }
        }
    }

    // Table nodes
    const tableMap = new Map();
    for (const t of data.tables) {
        tableMap.set(t.table_name, { name: t.table_name, source_id: t.source_id });
    }

    // Source nodes
    const sourceMap = new Map();
    for (const s of data.sources) {
        sourceMap.set(s.id, s);
    }

    // Upstream nodes
    const upstreamMap = new Map();
    for (const u of data.upstreams) {
        upstreamMap.set(u.id, u);
    }

    // Only keep fields that reference tables in this report
    const fieldNodes = [];
    for (const [key, f] of fieldMap) {
        fieldNodes.push({ id: `field-${key}`, key, table: f.table, field: f.field });
    }

    // Only keep tables referenced by fields
    const usedTableNames = new Set(fieldNodes.map(f => f.table));
    const tableNodes = [];
    for (const [name, t] of tableMap) {
        if (usedTableNames.has(name) || t.source_id) {
            tableNodes.push({ id: `table-${name}`, name, source_id: t.source_id });
        }
    }

    // Only keep sources referenced by tables
    const usedSourceIds = new Set(tableNodes.map(t => t.source_id).filter(Boolean));
    const sourceNodes = [];
    for (const [id, s] of sourceMap) {
        if (usedSourceIds.has(id)) sourceNodes.push(s);
    }

    // Only keep upstreams referenced by sources
    const usedUpstreamIds = new Set(sourceNodes.map(s => s.upstream_id).filter(Boolean));
    const upstreamNodes = [];
    for (const [id, u] of upstreamMap) {
        if (usedUpstreamIds.has(id)) upstreamNodes.push(u);
    }

    if (visualNodes.length === 0 && tableNodes.length === 0) {
        container.innerHTML = '<div class="lineage-placeholder">No visual lineage data for this report. Run a scan from Admin to parse layout data.</div>';
        return;
    }

    // Group visuals by page
    const pageGroups = new Map();
    for (const v of visualNodes) {
        if (!pageGroups.has(v.page)) pageGroups.set(v.page, []);
        pageGroups.get(v.page).push(v);
    }

    // Build HTML
    const statusClass = s => {
        if (s === "current" || s === "fresh") return "lineage-status-current";
        if (s === "stale") return "lineage-status-stale";
        if (s === "outdated" || s === "error") return "lineage-status-error";
        return "lineage-status-unknown";
    };

    const statusDot = s => {
        const colors = { current: "var(--green)", fresh: "var(--green)", stale: "var(--yellow)", outdated: "var(--red)", error: "var(--red)" };
        const c = colors[s] || "var(--text-dim)";
        return `<span class="lineage-dot" style="background:${c}"></span>`;
    };

    // Visuals column
    let visualsHtml = '<div class="lineage-col" data-col="visuals"><div class="lineage-col-header">Visuals</div>';
    for (const [pageName, visuals] of pageGroups) {
        visualsHtml += `<div class="lineage-page-group"><div class="lineage-page-label">${pageName}</div>`;
        for (const v of visuals) {
            const autoLabel = v.fields.length > 0 ? v.fields.slice(0, 3).map(f => f.split(".").pop().replace(/_/g, " ")).join(", ") + (v.fields.length > 3 ? ` +${v.fields.length - 3}` : "") : null;
            const label = v.title || autoLabel || _visualTypeLabel(v.type);
            visualsHtml += `<div class="lineage-node lineage-node-visual" data-lineage-id="${v.id}" title="${(v.title || "").replace(/"/g, "&quot;")}">
                <span class="visual-type-badge">${_visualTypeLabel(v.type)}</span>
                <span class="lineage-node-label">${label}</span>
            </div>`;
        }
        visualsHtml += '</div>';
    }
    visualsHtml += '</div>';

    // Fields column
    let fieldsHtml = '<div class="lineage-col" data-col="fields"><div class="lineage-col-header">Fields / Measures</div>';
    for (const f of fieldNodes) {
        fieldsHtml += `<div class="lineage-node lineage-node-field" data-lineage-id="${f.id}">
            <span class="lineage-node-label">${f.field.replace(/_/g, " ")}</span>
            <span class="lineage-table-hint">${f.table}</span>
        </div>`;
    }
    fieldsHtml += '</div>';

    // Tables column
    let tablesHtml = '<div class="lineage-col" data-col="tables"><div class="lineage-col-header">Tables</div>';
    for (const t of tableNodes) {
        const srcBadge = t.source_id ? "" : ' <span class="lineage-no-link">no source</span>';
        tablesHtml += `<div class="lineage-node lineage-node-table" data-lineage-id="${t.id}">
            <span class="lineage-node-label">${t.name}</span>${srcBadge}
        </div>`;
    }
    tablesHtml += '</div>';

    // Sources column
    let sourcesHtml = '<div class="lineage-col" data-col="sources"><div class="lineage-col-header">Sources</div>';
    for (const s of sourceNodes) {
        const shortName = s.name.length > 30 ? "..." + s.name.slice(-27) : s.name;
        sourcesHtml += `<div class="lineage-node lineage-node-source ${statusClass(s.status)}" data-lineage-id="source-${s.id}" title="${esc(s.name)}">
            ${statusDot(s.status)}
            <span class="lineage-node-label">${esc(shortName)}</span>
        </div>`;
    }
    sourcesHtml += '</div>';

    // Upstream column
    let upstreamHtml = '<div class="lineage-col" data-col="upstreams"><div class="lineage-col-header">Upstream Systems</div>';
    for (const u of upstreamNodes) {
        upstreamHtml += `<div class="lineage-node lineage-node-upstream" data-lineage-id="upstream-${u.id}">
            <span class="lineage-node-label">${u.name}</span>
            <span class="lineage-table-hint">${u.refresh_day || ""}</span>
        </div>`;
    }
    upstreamHtml += '</div>';

    // Report header
    const reportHeader = `<div class="lineage-report-header">
        <strong>${esc(data.report.name)}</strong>
        <span class="lineage-report-status lineage-report-status-${(data.report.status || "").replace(/\s+/g, "-")}">${data.report.status}</span>
        ${data.report.owner ? `<span class="lineage-report-owner">${esc(data.report.owner)}</span>` : ""}
    </div>`;

    container.innerHTML = `
        ${reportHeader}
        <div class="lineage-diagram-wrap">
            <div class="lineage-diagram-grid">
                ${visualsHtml}${fieldsHtml}${tablesHtml}${sourcesHtml}${upstreamHtml}
            </div>
            <svg class="lineage-svg" id="lineage-svg"></svg>
        </div>
    `;

    // Build connections and draw lines
    const { connections, forward, backward } = _buildLineageGraph(data, visualNodes, fieldNodes, tableNodes, sourceNodes, upstreamNodes);
    window._lineageFwd = forward;
    window._lineageBwd = backward;
    _drawLineageLines(connections);

    // Bind click interactions
    _bindLineageClicks();

    // Redraw on resize
    if (window._lineageResizeObs) window._lineageResizeObs.disconnect();
    const wrap = container.querySelector(".lineage-diagram-wrap");
    if (wrap) {
        window._lineageResizeObs = new ResizeObserver(() => {
            clearTimeout(window._lineageResizeTimer);
            window._lineageResizeTimer = setTimeout(() => _drawLineageLines(connections), 50);
        });
        window._lineageResizeObs.observe(wrap);
    }
}

function _buildLineageGraph(data, visualNodes, fieldNodes, tableNodes, sourceNodes, upstreamNodes) {
    const connections = [];
    // Directed graphs: forward = left-to-right (visual→field→table→source→upstream)
    //                  backward = right-to-left (upstream→source→table→field→visual)
    const forward = new Map();
    const backward = new Map();

    function addEdge(a, b) {
        connections.push({ from: a, to: b });
        if (!forward.has(a)) forward.set(a, new Set());
        forward.get(a).add(b);
        if (!backward.has(b)) backward.set(b, new Set());
        backward.get(b).add(a);
    }

    // Visual -> Field
    for (const v of visualNodes) {
        for (const fk of v.fields) {
            addEdge(v.id, `field-${fk}`);
        }
    }

    // Field -> Table
    const ftDone = new Set();
    for (const f of fieldNodes) {
        const key = `${f.id}->table-${f.table}`;
        if (!ftDone.has(key)) {
            ftDone.add(key);
            addEdge(f.id, `table-${f.table}`);
        }
    }

    // Table -> Source
    for (const t of tableNodes) {
        if (t.source_id) addEdge(t.id, `source-${t.source_id}`);
    }

    // Source -> Upstream
    for (const s of sourceNodes) {
        if (s.upstream_id) addEdge(`source-${s.id}`, `upstream-${s.upstream_id}`);
    }

    return { connections, forward, backward };
}

function _drawLineageLines(connections) {
    const svg = document.getElementById("lineage-svg");
    if (!svg) return;
    const wrap = svg.parentElement;
    if (!wrap) return;

    svg.innerHTML = "";
    svg.setAttribute("width", wrap.scrollWidth);
    svg.setAttribute("height", wrap.scrollHeight);

    const wrapRect = wrap.getBoundingClientRect();

    for (const conn of connections) {
        const fromEl = wrap.querySelector(`[data-lineage-id="${conn.from}"]`);
        const toEl = wrap.querySelector(`[data-lineage-id="${conn.to}"]`);
        if (!fromEl || !toEl) continue;

        const fromRect = fromEl.getBoundingClientRect();
        const toRect = toEl.getBoundingClientRect();

        const x1 = fromRect.right - wrapRect.left;
        const y1 = fromRect.top + fromRect.height / 2 - wrapRect.top;
        const x2 = toRect.left - wrapRect.left;
        const y2 = toRect.top + toRect.height / 2 - wrapRect.top;

        const midX = (x1 + x2) / 2;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`);
        path.setAttribute("class", "lineage-line");
        path.dataset.from = conn.from;
        path.dataset.to = conn.to;
        svg.appendChild(path);
    }
}

function _bindLineageClicks() {
    const wrap = document.querySelector(".lineage-diagram-wrap");
    if (!wrap) return;
    const svg = document.getElementById("lineage-svg");

    wrap.querySelectorAll(".lineage-node").forEach(node => {
        node.addEventListener("click", (e) => {
            e.stopPropagation();
            const id = node.dataset.lineageId;

            // Toggle off if already highlighted
            if (node.classList.contains("lineage-highlighted")) {
                _resetLineageHighlight(wrap, svg);
                return;
            }

            // BFS to find all connected nodes
            const connected = _traceConnections(id);

            wrap.querySelectorAll(".lineage-node").forEach(n => {
                const isConn = connected.has(n.dataset.lineageId);
                n.classList.toggle("lineage-highlighted", isConn);
                n.classList.toggle("lineage-dimmed", !isConn);
            });

            if (svg) {
                svg.querySelectorAll(".lineage-line").forEach(line => {
                    const isConn = connected.has(line.dataset.from) && connected.has(line.dataset.to);
                    line.classList.toggle("lineage-highlighted", isConn);
                    line.classList.toggle("lineage-dimmed", !isConn);
                });
            }
        });
    });

    // Click empty space to reset
    wrap.addEventListener("click", () => _resetLineageHighlight(wrap, svg));
}

function _traceConnections(startId) {
    const fwd = window._lineageFwd;
    const bwd = window._lineageBwd;
    if (!fwd || !bwd) return new Set([startId]);

    // Determine node type from prefix
    const isVisual = startId.startsWith("visual-");
    const isField = startId.startsWith("field-");

    // Always trace forward (left→right: toward upstream)
    const visited = new Set();
    const fwdQueue = [startId];
    while (fwdQueue.length > 0) {
        const cur = fwdQueue.shift();
        if (visited.has(cur)) continue;
        visited.add(cur);
        const neighbors = fwd.get(cur);
        if (neighbors) for (const n of neighbors) if (!visited.has(n)) fwdQueue.push(n);
    }

    // Only trace backward (right→left: toward visuals) when clicking
    // on tables, sources, or upstreams — NOT visuals or fields
    if (!isVisual && !isField) {
        const bwdQueue = [startId];
        while (bwdQueue.length > 0) {
            const cur = bwdQueue.shift();
            if (visited.has(cur)) continue;
            visited.add(cur);
            const neighbors = bwd.get(cur);
            if (neighbors) for (const n of neighbors) if (!visited.has(n)) bwdQueue.push(n);
        }
    }

    return visited;
}

function _resetLineageHighlight(wrap, svg) {
    wrap.querySelectorAll(".lineage-node").forEach(n => {
        n.classList.remove("lineage-highlighted", "lineage-dimmed");
    });
    if (svg) {
        svg.querySelectorAll(".lineage-line").forEach(l => {
            l.classList.remove("lineage-highlighted", "lineage-dimmed");
        });
    }
}



// ── Tasks / Kanban ──

const TASK_STATUSES = [
    { key: "backlog", label: "Backlog" },
    { key: "todo", label: "To Do" },
    { key: "in_progress", label: "In Progress" },
    { key: "done", label: "Done" },
];

function _taskCard(task) {
    const today = new Date().toISOString().slice(0, 10);
    const overdue = task.due_date && task.due_date < today && task.status !== "done";
    const dueFmt = task.due_date ? new Date(task.due_date + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : "";
    return `<div class="kanban-card priority-${task.priority}" draggable="true" data-task-id="${task.id}" tabindex="0" role="listitem" aria-label="Task: ${esc(task.title)}, Priority: ${task.priority}${task.assigned_to ? ', Assigned to: ' + esc(task.assigned_to) : ''}">
        <div class="kanban-card-title">${esc(task.title)}</div>
        <div class="kanban-card-meta">
            <span class="priority-tag ${task.priority}">${task.priority}</span>
            ${task.assigned_to ? `<span class="assignee-chip" title="${esc(task.assigned_to)}">${esc(task.assigned_to)}</span>` : ""}
            ${dueFmt ? `<span class="due-date${overdue ? " overdue" : ""}">${overdue ? "Overdue: " : ""}${dueFmt}</span>` : ""}
        </div>
    </div>`;
}

async function renderTasks() {
    const [tasks, owners] = await Promise.all([
        api("/api/tasks"),
        api("/api/tasks/owners"),
    ]);

    window._tasksData = tasks;
    window._tasksOwners = owners;

    const ownerOptions = owners.map(o => `<option value="${o}">${o}</option>`).join("");
    const boardHtml = _buildKanbanBoard(tasks);

    return `
        <div class="page-header">
            <h1>Tasks</h1>
            <button class="btn-new-task" id="btn-new-task">+ New Task</button>
        </div>
        <div class="kanban-toolbar">
            <span class="owner-filter-label">View:</span>
            <select id="task-owner-filter">
                <option value="">All Team Members</option>
                ${ownerOptions}
            </select>
        </div>
        <div id="kanban-board-container">
            ${boardHtml}
        </div>
    `;
}

function _buildKanbanBoard(tasks, filterOwner) {
    const filtered = filterOwner ? tasks.filter(t => t.assigned_to === filterOwner) : tasks;

    // Show message when filter yields nothing
    if (filterOwner && filtered.length === 0) {
        return `<div class="kanban-empty-filtered">No tasks assigned to <strong>${filterOwner}</strong></div>`;
    }

    const grouped = {};
    TASK_STATUSES.forEach(s => grouped[s.key] = []);
    filtered.forEach(t => {
        if (grouped[t.status]) grouped[t.status].push(t);
        else grouped.backlog.push(t);
    });
    Object.values(grouped).forEach(arr => arr.sort((a, b) => a.position - b.position));

    return `<div class="kanban-board">
        ${TASK_STATUSES.map(s => `
            <div class="kanban-column" data-status="${s.key}">
                <div class="kanban-col-header">
                    <span>${s.label}</span>
                    <span class="col-count">${grouped[s.key].length}</span>
                </div>
                <div class="kanban-col-body" data-status="${s.key}" role="list">
                    ${grouped[s.key].length === 0
                        ? '<div class="kanban-empty">No tasks</div>'
                        : grouped[s.key].map(t => _taskCard(t)).join("")}
                </div>
            </div>
        `).join("")}
    </div>`;
}

function _taskModalHtml(task, owners) {
    const isEdit = !!task;
    const title = isEdit ? "Edit Task" : "New Task";
    const t = task || { title: "", description: "", status: "backlog", priority: "medium", assigned_to: "", due_date: "", email_owner: false };
    const ownerOptions = owners.map(o =>
        `<option value="${o}" ${t.assigned_to === o ? "selected" : ""}>${o}</option>`
    ).join("");
    const statusOptions = TASK_STATUSES.map(s =>
        `<option value="${s.key}" ${t.status === s.key ? "selected" : ""}>${s.label}</option>`
    ).join("");

    return `<div class="task-modal-overlay" id="task-modal-overlay" role="dialog" aria-modal="true" aria-labelledby="task-modal-title">
        <div class="task-modal">
            <h2 id="task-modal-title">${title}</h2>
            <label>Title</label>
            <input type="text" id="task-title" value="${esc(t.title)}" placeholder="Task title..." />
            <label>Description</label>
            <textarea id="task-desc" placeholder="Optional description...">${esc(t.description)}</textarea>
            <label>Status</label>
            <select id="task-status">${statusOptions}</select>
            <label>Priority</label>
            <select id="task-priority">
                <option value="high" ${t.priority === "high" ? "selected" : ""}>High</option>
                <option value="medium" ${t.priority === "medium" ? "selected" : ""}>Medium</option>
                <option value="low" ${t.priority === "low" ? "selected" : ""}>Low</option>
            </select>
            <label>Assign To</label>
            <select id="task-assign">
                <option value="">Unassigned</option>
                ${ownerOptions}
            </select>
            <label>Due Date</label>
            <input type="date" id="task-due" value="${t.due_date || ""}" />
            <label class="task-checkbox-label">
                <input type="checkbox" id="task-email-owner" ${t.email_owner ? "checked" : ""} />
                Email owner on assignment
            </label>
            <div class="task-modal-actions">
                ${isEdit ? `<button class="btn-danger" id="task-delete-btn">Delete</button>` : ""}
                <button id="task-cancel-btn">Cancel</button>
                <button class="btn-primary" id="task-save-btn">${isEdit ? "Save" : "Create"}</button>
            </div>
        </div>
    </div>`;
}

function _openTaskModal(task) {
    const owners = window._tasksOwners || [];
    const existing = document.getElementById("task-modal-overlay");
    if (existing) existing.remove();
    document.body.insertAdjacentHTML("beforeend", _taskModalHtml(task, owners));

    const overlay = document.getElementById("task-modal-overlay");
    const cancelBtn = document.getElementById("task-cancel-btn");
    const saveBtn = document.getElementById("task-save-btn");
    const deleteBtn = document.getElementById("task-delete-btn");
    const titleInput = document.getElementById("task-title");

    const close = () => { document.removeEventListener("keydown", escHandler); overlay.remove(); };
    const escHandler = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", escHandler);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    cancelBtn.addEventListener("click", close);

    // Clear validation error on typing
    titleInput.addEventListener("input", () => titleInput.classList.remove("input-error"));

    saveBtn.addEventListener("click", async () => {
        const title = titleInput.value.trim();
        if (!title) { titleInput.classList.add("input-error"); titleInput.focus(); return; }

        saveBtn.disabled = true;
        const body = {
            title,
            description: document.getElementById("task-desc").value.trim() || null,
            status: document.getElementById("task-status").value,
            priority: document.getElementById("task-priority").value,
            assigned_to: document.getElementById("task-assign").value || null,
            due_date: document.getElementById("task-due").value || null,
            email_owner: document.getElementById("task-email-owner").checked,
        };

        try {
            if (task) {
                await apiPatch(`/api/tasks/${task.id}`, body);
            } else {
                await apiPostJson("/api/tasks", body);
            }
            close();
            await _refreshTaskBoard();
        } catch (err) {
            saveBtn.disabled = false;
            toast("Failed to save task: " + err.message);
        }
    });

    if (deleteBtn && task) {
        deleteBtn.addEventListener("click", async () => {
            if (!confirm("Delete this task?")) return;
            deleteBtn.disabled = true;
            try {
                await apiDelete(`/api/tasks/${task.id}`);
                close();
                await _refreshTaskBoard();
            } catch (err) {
                deleteBtn.disabled = false;
                toast("Failed to delete task: " + err.message);
            }
        });
    }

    titleInput.focus();
}

async function _refreshTaskBoard() {
    try {
        const tasks = await api("/api/tasks");
        window._tasksData = tasks;
        const filterOwner = document.getElementById("task-owner-filter")?.value || "";
        const container = document.getElementById("kanban-board-container");
        if (container) {
            container.innerHTML = _buildKanbanBoard(tasks, filterOwner || null);
        }
    } catch (err) {
        toast("Failed to refresh tasks: " + err.message);
    }
}

function bindTasksPage() {
    // New task button
    const newBtn = document.getElementById("btn-new-task");
    if (newBtn) newBtn.addEventListener("click", () => _openTaskModal(null));

    // Owner filter
    const filter = document.getElementById("task-owner-filter");
    if (filter) {
        filter.addEventListener("change", () => {
            const tasks = window._tasksData || [];
            const filterOwner = filter.value || null;
            const container = document.getElementById("kanban-board-container");
            if (container) {
                container.innerHTML = _buildKanbanBoard(tasks, filterOwner);
            }
        });
    }

    // Event delegation for kanban board (drag-drop, clicks, keyboard)
    const board = document.getElementById("kanban-board-container");
    if (!board) return;

    // Card clicks + keyboard (Enter/Space)
    board.addEventListener("click", (e) => {
        const card = e.target.closest(".kanban-card");
        if (!card) return;
        const taskId = parseInt(card.dataset.taskId);
        const task = (window._tasksData || []).find(t => t.id === taskId);
        if (task) _openTaskModal(task);
    });
    board.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        const card = e.target.closest(".kanban-card");
        if (!card) return;
        e.preventDefault();
        const taskId = parseInt(card.dataset.taskId);
        const task = (window._tasksData || []).find(t => t.id === taskId);
        if (task) _openTaskModal(task);
    });

    // Drag-and-drop (delegated)
    board.addEventListener("dragstart", (e) => {
        const card = e.target.closest(".kanban-card");
        if (!card) return;
        card.classList.add("dragging");
        e.dataTransfer.setData("text/plain", card.dataset.taskId);
        e.dataTransfer.effectAllowed = "move";
    });
    board.addEventListener("dragend", (e) => {
        const card = e.target.closest(".kanban-card");
        if (card) card.classList.remove("dragging");
        board.querySelectorAll(".kanban-column.drag-over").forEach(c => c.classList.remove("drag-over"));
    });
    board.addEventListener("dragover", (e) => {
        const colBody = e.target.closest(".kanban-col-body");
        if (!colBody) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        colBody.closest(".kanban-column").classList.add("drag-over");
    });
    board.addEventListener("dragleave", (e) => {
        const colBody = e.target.closest(".kanban-col-body");
        if (!colBody) return;
        if (!colBody.contains(e.relatedTarget)) {
            colBody.closest(".kanban-column").classList.remove("drag-over");
        }
    });
    board.addEventListener("drop", async (e) => {
        const colBody = e.target.closest(".kanban-col-body");
        if (!colBody) return;
        e.preventDefault();
        colBody.closest(".kanban-column").classList.remove("drag-over");
        const taskId = e.dataTransfer.getData("text/plain");
        const newStatus = colBody.dataset.status;

        const cards = [...colBody.querySelectorAll(".kanban-card")];
        let position = cards.length;
        const rect = colBody.getBoundingClientRect();
        const y = e.clientY - rect.top;
        for (let i = 0; i < cards.length; i++) {
            const cardRect = cards[i].getBoundingClientRect();
            const cardMid = cardRect.top + cardRect.height / 2 - rect.top;
            if (y < cardMid) { position = i; break; }
        }

        try {
            await apiPatch(`/api/tasks/${taskId}/move`, { status: newStatus, position });
            await _refreshTaskBoard();
        } catch (err) {
            toast("Failed to move task: " + err.message);
        }
    });
}


// ── Event Log Page ──

async function renderEventLog() {
    const events = await api("/api/eventlog");
    const cols = [
        { key: "created_at", label: "When", render: e => `<span title="${esc(e.created_at || "")}">${timeAgo(e.created_at)}</span>` },
        { key: "entity_type", label: "Type", render: e => `<span class="eventlog-type-badge type-${esc(e.entity_type)}">${esc(e.entity_type)}</span>` },
        { key: "entity_name", label: "Entity", render: e => esc(e.entity_name) || `#${e.entity_id || "—"}` },
        { key: "action", label: "Action", render: e => `<span class="eventlog-action action-${esc(e.action)}">${esc(e.action)}</span>` },
        { key: "detail", label: "Detail", render: e => esc(e.detail) || "" },
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
        a: "MX Analytics automatically discovers Power BI reports and their data sources, monitors data freshness, flags issues, and gives your team a single place to manage data quality and accountability."
    },
    {
        q: "Where does the data come from?",
        a: "The scanner reads .pbix files and TMDL exports from a shared folder you configure (DG_TMDL_ROOT). It extracts all tables, data sources, measures, and columns automatically — no manual entry needed."
    },
    {
        q: "What data source types are supported?",
        a: "SQL Server, PostgreSQL, MySQL, Oracle, CSV files, Excel workbooks, SharePoint lists, web sources, and folder-based imports. The scanner identifies the type from the Power Query M expression in each report."
    },
    {
        q: "How does freshness monitoring work?",
        a: "After a scan, the prober checks when each data source was last updated. Sources are classified as Fresh, Stale, or Outdated based on configurable thresholds (default: fresh < 3 days, stale 3–7 days, outdated > 7 days). You can set custom thresholds per source."
    },
    {
        q: "What are Report Owner and Business Owner?",
        a: "These are metadata tables inside each Power BI report. Report Owner is typically the developer or analyst who maintains the report. Business Owner is the stakeholder accountable for the data. Both are extracted automatically during scans."
    },
    {
        q: "How do alerts work?",
        a: "Alerts are auto-generated when sources become stale, go offline, have broken references, or have changed queries. Each alert can be assigned to an owner, acknowledged, or resolved with a reason."
    },
    {
        q: "What is the TMDL Checker?",
        a: "It scans all reports against a set of best-practice rules: no local file paths, required owner metadata, proper date types, avoiding DirectQuery mode, excessive columns, and duplicate sources. Findings are shown by severity with filtering by report owner."
    },
    {
        q: "How does the Kanban task board work?",
        a: "Create tasks for your team with titles, descriptions, priorities, due dates, and assignees (from the report owner list). Drag cards between Backlog, To Do, In Progress, and Done columns. Filter by team member to see individual workloads."
    },
    {
        q: "What is the Lineage view?",
        a: "Lineage shows the full dependency chain: Visuals → Fields → Tables → Data Sources → Upstream Systems. Select a report to see exactly which sources feed into which visuals, helping you trace data quality issues upstream."
    },
    {
        q: "How do upstream systems work?",
        a: "Upstream systems (like GSCM or ASAP) represent the parent data platforms that feed your sources. Linking sources to upstream systems enables schedule discrepancy detection — flagging when the refresh chain timing is broken."
    },
    {
        q: "What does the Schedule Discrepancies check do?",
        a: "It validates that data flows in the right order: Upstream System refreshes before Source, which refreshes before Report. If the timing is wrong (e.g., a report refreshes before its source), it flags a warning or critical issue."
    },
    {
        q: "Can I add sources and reports manually?",
        a: "Yes. Use the Create page to add sources, reports, or upstream systems manually. These appear alongside scanned entries and can be linked together."
    },
    {
        q: "What is the AI Assistant?",
        a: "The AI chat (bottom-right button) lets you ask questions about your data ecosystem — risks, source health, specific reports, or general governance questions. It uses live data from the database to give contextual answers."
    },
    {
        q: "What database does the platform use?",
        a: "A single SQLite file (governance.db). No external database server needed. This is the only file you need to back up to preserve all state."
    },
    {
        q: "How do I set up the platform?",
        a: "Install Python 3.11+, run pip install -r requirements.txt, set DG_TMDL_ROOT to your reports folder, and start with: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000. Open http://localhost:8000 in your browser."
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


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    sources: renderSources,
    reports: renderReports,
    lineage: renderLineageDiagram,
    scanner: renderScanner,
    changelog: renderChangelog,
    create: renderCreate,
    bestpractices: renderBestPractices,
    actions: renderActions,
    tasks: renderTasks,
    eventlog: renderEventLog,
    faq: renderFaq,
};

// Map old hash routes to new pages for backwards compat
const pageAliases = { alerts: "dashboard", issues: "dashboard" };

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

    $$("nav a[data-page]").forEach(a => {
        a.classList.toggle("active", a.dataset.page === page);
    });
    // Highlight parent nav-group when a child page is active
    $$("nav .nav-group").forEach(g => {
        const childPages = (g.dataset.pages || "").split(",");
        g.classList.toggle("active", childPages.includes(page));
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
                        // Flash-highlight the clicked alert row
                        el.style.background = "rgba(59,130,246,0.25)";
                        setTimeout(() => { el.style.background = ""; }, 1200);
                    }
                });
            });
            // Impact toggle
            document.querySelectorAll(".impact-toggle-btn").forEach(btn => {
                btn.addEventListener("click", () => {
                    const view = btn.dataset.view;
                    document.querySelectorAll(".impact-toggle-btn").forEach(b => b.classList.remove("active"));
                    btn.classList.add("active");
                    const timeline = document.getElementById("attention-timeline");
                    const impact = document.getElementById("attention-impact");
                    if (timeline) timeline.style.display = view === "timeline" ? "" : "none";
                    if (impact) impact.style.display = view === "impact" ? "" : "none";
                });
            });
            // Impact items — click to source detail
            document.querySelectorAll(".impact-clickable").forEach(el => {
                el.addEventListener("click", async () => {
                    const srcId = parseInt(el.dataset.sourceId);
                    const src = (window._dashboardSources || []).find(s => s.id === srcId);
                    if (src) { await navigate("sources"); showSourceDetail(src); }
                });
            });
            // Draw health trend chart
            drawHealthTrendChart();
        }
        if (page === "scanner") bindScannerButtons();
        if (page === "sources") bindSourcesPage();
        if (page === "actions") bindActionsTab();
        if (page === "reports") bindReportsPage();
        if (page === "create") bindCreatePage();
        if (page === "changelog") bindChangelogPage();
        if (page === "bestpractices") bindBestPracticesPage();
        if (page === "faq") bindFaqPage();
        if (page === "eventlog") bindEventLogPage();
        if (page === "tasks") bindTasksPage();
        if (page === "lineage") bindLineageDiagramPage();
    } catch (err) {
        app.innerHTML = `<div class="loading" style="color:var(--red)">Error loading page: ${esc(err.message)}</div>`;
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
                <p>Hi! I can help you understand your analytics ecosystem. Ask me about risks, source health, or specific reports.</p>
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
        const page = location.hash.length > 1 ? location.hash.substring(1) : "dashboard";
        if (pages[page] && page !== currentPage) navigate(page);
    });

    initAIChatPanel();
    navigate(getInitialPage());
});
