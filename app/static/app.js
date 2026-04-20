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
    if (!status) return '<span class="badge badge-muted">not probed</span>';
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
        return '<span class="badge badge-muted">no connection</span>';
    if (s === "no_rule")
        return '<span class="badge badge-muted" title="No freshness rule set for this source">no rule</span>';
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
        stale_source: "Degraded",
        outdated_source: "Degraded",
        error_source: "Degraded",
        broken_ref: "Broken Ref",
        changed_query: "Query Changed",
        refresh_failed: "Refresh Failed",
        refresh_overdue: "Refresh Overdue",
        task_failed: "Task Failed",
        script_failed: "Script Failed",
        schedule_mismatch: "Stale vs Source",
    };
    const colors = {
        stale_source: "badge-red",
        outdated_source: "badge-red",
        error_source: "badge-red",
        broken_ref: "badge-red",
        changed_query: "badge-blue",
        refresh_failed: "badge-red",
        refresh_overdue: "badge-yellow",
        task_failed: "badge-red",
        script_failed: "badge-red",
        schedule_mismatch: "badge-yellow",
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
        // Handle DD/MM/YYYY format that JS can't parse natively
        if (/1999/.test(dateStr)) return "-";
        return dateStr;
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
            if (e.target.closest(".btn-archive-action")) return;
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
            if (e.target.closest(".btn-archive-action")) return;
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

    // Treat 0 as no rule (same as NULL) for UI purposes
    const hasCustomRule = source.custom_fresh_days != null && source.custom_fresh_days > 0;
    const freshVal = hasCustomRule ? source.custom_fresh_days : "";

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(parsed.shortName)}</h2>
            <button class="btn-outline" id="btn-close-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Type</div>${typeBadge(source.type)}</div>
            <div class="detail-item"><div class="detail-label">Status</div>${statusBadge(source.status)}</div>
            <div class="detail-item"><div class="detail-label">Last Updated</div><span style="color:var(--text)">${source.last_updated ? esc(formatDate(source.last_updated)) : "-"}</span></div>
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
        <div class="freshness-rule-form">
            <label class="freshness-label">Healthy up to
                <input type="number" id="fresh-days-input" value="${freshVal}" placeholder="blank = no rule" min="1" max="9999" class="input-sm">
                days (degraded after)
            </label>
            <button class="btn-sm btn-blue" id="btn-save-freshness">Save</button>
            ${hasCustomRule ? '<button class="btn-sm btn-outline" id="btn-reset-freshness">Clear rule</button>' : ''}
            ${hasCustomRule
                ? '<span class="badge badge-blue" style="font-size:0.72rem">rule active</span>'
                : '<span style="color:var(--text-dim);font-size:0.75rem">No rule set - freshness not monitored for this source</span>'}
        </div>
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

    // Freshness rule bindings
    const saveFreshBtn = document.getElementById("btn-save-freshness");
    if (saveFreshBtn) {
        saveFreshBtn.addEventListener("click", async () => {
            const raw = document.getElementById("fresh-days-input").value.trim();
            // Blank input = clear the rule
            if (raw === "") {
                try {
                    await apiDelete(`/api/sources/${source.id}/freshness-rule`);
                    toast("Rule cleared - source not monitored");
                } catch (err) {
                    toast("Failed: " + err.message);
                }
                return;
            }
            const fd = parseInt(raw);
            if (isNaN(fd) || fd < 1) {
                toast("Enter at least 1 day, or leave blank to clear the rule");
                return;
            }
            try {
                await apiPut(`/api/sources/${source.id}/freshness-rule`, { fresh_days: fd });
                toast("Freshness rule saved - re-probe to apply");
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
                toast("Freshness rule cleared - source not monitored");
                document.getElementById("fresh-days-input").value = "";
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
    const allRows = document.querySelectorAll("#dt-reports tbody tr[data-clickable]");
    let targetRow = null;
    allRows.forEach(tr => {
        const idx = parseInt(tr.dataset.rowIdx);
        const dt = window._dt["dt-reports"];
        if (dt && dt._displayRows && dt._displayRows[idx] && dt._displayRows[idx].id === report.id) {
            targetRow = tr;
        }
    });

    const [tables, unusedData, linkedTasks, docData] = await Promise.all([
        api(`/api/reports/${report.id}/tables`),
        api(`/api/reports/${report.id}/unused`).catch(() => ({ total_measures: 0, total_columns: 0, total_fields: 0, unused_measures: [], unused_columns: [], unused_tables: [], unused_fields_count: 0, unused_pct: 0, total_tables: 0, unused_tables_count: 0 })),
        api(`/api/tasks/for-entity?entity_type=report&entity_id=${report.id}`).catch(() => []),
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

    // ── Linked Tasks ──
    const activeLinked = linkedTasks.filter(t => t.status !== "done");
    const archivedLinked = linkedTasks.filter(t => t.status === "done");

    // ── Assemble ──
    expandRow.innerHTML = `<td colspan="${colCount}" class="report-expand-cell">
        <div class="report-expand-content">
            <div style="display:flex;gap:0.5rem;margin-bottom:0.5rem">
                ${report.powerbi_url ? `<a href="${esc(report.powerbi_url)}" target="_blank" rel="noopener" class="btn-outline" style="font-size:0.72rem;text-decoration:none" onclick="event.stopPropagation()">View in Power BI</a>` : ''}
                <button class="btn-outline btn-lineage-nav" data-report-id="${report.id}" style="font-size:0.72rem">View Lineage</button>
            </div>
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

            ${linkedTasks.length > 0 ? `
            <div class="rx-section rx-l1">
                <div class="rx-toggle" data-target="linked-tasks-list">
                    <span class="rx-arrow">&#9656;</span> Linked Tasks
                    ${activeLinked.length > 0
                        ? `<span class="badge badge-blue" style="margin-left:0.35rem;font-size:0.58rem">${activeLinked.length} active</span>`
                        : `<span class="badge badge-green" style="margin-left:0.35rem;font-size:0.58rem">all done</span>`}
                </div>
                <div class="rx-body" id="linked-tasks-list" style="display:none">
                    ${activeLinked.map(t => `
                        <div class="linked-task-item rx-l2">
                            <span class="priority-tag ${t.priority}" style="font-size:0.65rem">${t.priority}</span>
                            <span class="linked-task-title">${esc(t.title)}</span>
                            ${t.assigned_to ? `<span class="assignee-chip" style="font-size:0.68rem">${esc(t.assigned_to)}</span>` : ''}
                            <span class="badge badge-${t.status === 'in_progress' ? 'yellow' : 'muted'}" style="font-size:0.62rem">${t.status}</span>
                        </div>`).join('')}
                    ${archivedLinked.length > 0 ? archivedLinked.map(t => `
                        <div class="linked-task-item rx-l2" style="opacity:0.5">
                            <span class="linked-task-title">${esc(t.title)}</span>
                            <span class="badge badge-green" style="font-size:0.62rem">done</span>
                        </div>`).join('') : ''}
                </div>
            </div>` : ''}
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
    const [data, sources, reports, actions, healthTrend, people] = await Promise.all([
        api("/api/dashboard"),
        api("/api/sources"),
        api("/api/reports"),
        api("/api/actions"),
        api("/api/schedules/health-trend"),
        api("/api/people"),
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

    return `
        <div class="stat-grid">
            <div class="stat-card card-purple stat-card-clickable" data-navigate="reports" role="button" tabindex="0" aria-label="Reports: ${data.reports_total} total">
                <div class="stat-label">Reports</div>
                <div class="stat-value">${data.reports_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green" title="All data sources are fresh and up to date">${reports.filter(r => r.status === "healthy").length} healthy</span>
                    <span class="stat-dot dot-red" title="Data sources are past freshness threshold">${reports.filter(r => r.status === "degraded" || r.status === "at risk").length} degraded</span>
                    ${reports.filter(r => r.status === "unknown").length ? `<span class="stat-dot dot-muted" title="Status has not been probed yet">${reports.filter(r => r.status === "unknown").length} unknown</span>` : ""}
                </div>
                <div class="stat-card-link">View &rarr;</div>
            </div>
            <div class="stat-card card-blue stat-card-clickable${data.sources_outdated > 0 ? ' pulse-border-red' : ''}" data-navigate="sources" role="button" tabindex="0" aria-label="Total Sources: ${data.sources_total}, ${data.sources_fresh} healthy, ${data.sources_outdated} degraded">
                <div class="stat-label">Total Sources</div>
                <div class="stat-value">${data.sources_total}</div>
                <div class="stat-breakdown">
                    <span class="stat-dot dot-green stat-filter" data-filter="healthy" title="Data updated within freshness threshold">${data.sources_fresh} healthy</span>
                    <span class="stat-dot dot-red stat-filter" data-filter="degraded" title="Data past freshness threshold">${data.sources_outdated} degraded</span>
                    ${data.sources_unknown ? `<span class="stat-dot dot-muted stat-filter" data-filter="unknown" title="Source has not been probed yet or has no rule">${data.sources_unknown} unknown</span>` : ""}
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
                <div class="segment segment-muted" style="width:100%"></div>
            </div>
            <div style="text-align:center;color:var(--text-dim);font-size:0.78rem;margin-top:0.5rem">${total} sources discovered  - probe to check freshness</div>
            ` : `
            <div class="health-bar">
                ${freshPct > 0 ? `<div class="segment segment-green segment-clickable" data-tooltip="${data.sources_fresh} healthy (${freshPct}%)" data-filter="healthy" style="width:${freshPct}%"></div>` : ""}
                ${outdatedPct > 0 ? `<div class="segment segment-red segment-clickable" data-tooltip="${data.sources_outdated} degraded (${outdatedPct}%)" data-filter="degraded" style="width:${outdatedPct}%"></div>` : ""}
                ${unknownPct > 0 ? `<div class="segment segment-muted" data-tooltip="${data.sources_unknown || 0} unknown (${unknownPct}%)" style="width:${unknownPct}%"></div>` : ""}
            </div>
            <div class="health-tooltip" id="health-tooltip"></div>
            <div class="health-legend">
                <span class="stat-dot dot-green">${data.sources_fresh} Healthy</span>
                <span class="stat-dot dot-red">${data.sources_outdated} Degraded</span>
                ${data.sources_unknown ? `<span class="stat-dot dot-muted">${data.sources_unknown} Unknown</span>` : ""}
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

        <div id="dashboard-alerts" class="dashboard-alerts-section">
            ${renderDashboardAlertsSection(actions, people)}
        </div>
    `;
}

function renderDashboardAlertsSection(actions, people) {
    const biPeople = people.filter(p => p.role === "BI").map(p => p.name);

    // Open = not resolved or expected
    const openActions = actions.filter(a => a.status !== "resolved" && a.status !== "expected");
    const unassignedCount = openActions.filter(a => !a.assigned_to).length;

    // Per-person open counts
    const personCounts = {};
    biPeople.forEach(p => { personCounts[p] = 0; });
    openActions.forEach(a => {
        if (a.assigned_to && personCounts[a.assigned_to] !== undefined) {
            personCounts[a.assigned_to]++;
        }
    });

    const chipsHtml = `
        <button class="alerts-chip active" data-filter-person="all">All <span class="alerts-chip-count">${openActions.length}</span></button>
        <button class="alerts-chip" data-filter-person="__unassigned__">Unassigned <span class="alerts-chip-count">${unassignedCount}</span></button>
        ${biPeople.map(p => `
            <button class="alerts-chip" data-filter-person="${esc(p)}">${esc(p)} <span class="alerts-chip-count">${personCounts[p]}</span></button>
        `).join("")}
    `;

    const tableHtml = renderDashboardAlertsTable(actions, biPeople, "all");

    return `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.6rem;flex-wrap:wrap;gap:0.5rem">
            <h2 style="margin:0">Alerts</h2>
            <span style="color:var(--text-dim);font-size:0.78rem">Sorted by report degradation days (sum of days each source is past its freshness rule)</span>
        </div>
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
        return '<div class="empty-state" style="padding:1.5rem">No open alerts for this filter</div>';
    }

    const statusOptions = ["open", "acknowledged", "investigating", "expected", "resolved"];
    const ownerOptions = (currentOwner) => `
        <option value=""${!currentOwner ? ' selected' : ''}>Unassigned</option>
        ${biPeople.map(p => `<option value="${esc(p)}"${p === currentOwner ? ' selected' : ''}>${esc(p)}</option>`).join("")}
    `;

    const rows = filtered.map(a => {
        const rawName = a.asset_name || a.source_name || a.report_name || "-";
        const assetName = shortNameFromPath(rawName) || rawName;
        const linkData = a.asset_type === "source"
            ? `alerts-source-link" data-source-id="${a.asset_id}`
            : a.asset_type === "report"
            ? `alerts-report-link" data-report-id="${a.asset_id}`
            : a.asset_type === "scheduled_task"
            ? `alerts-task-link" data-task-id="${a.asset_id}`
            : a.asset_type === "script"
            ? `alerts-script-link" data-script-id="${a.asset_id}`
            : null;

        // Secondary info under the asset name: for source alerts, show
        // which report is most affected; for other types, leave blank.
        let sub = "";
        if (a.asset_type === "source" && a.top_report_name) {
            sub = `<div style="font-size:0.7rem;color:var(--text-dim);font-weight:400">affects ${esc(a.top_report_name)}${a.report_names.length > 1 ? ` +${a.report_names.length - 1}` : ""}</div>`;
        }

        const assetCell = linkData
            ? `<a class="alerts-link ${linkData}" title="Open detail">
                   <strong>${esc(assetName)}</strong>${sub}
               </a>`
            : `<div><strong>${esc(assetName)}</strong>${sub}</div>`;

        const typeLabel = a.asset_type === "scheduled_task" ? "task" : a.asset_type;
        const typeCell = a.asset_type
            ? `<span class="asset-type-badge asset-type-${a.asset_type}">${typeLabel}</span>`
            : '<span style="color:var(--text-dim)">-</span>';

        const days = a.asset_days || 0;

        return `
            <tr class="alerts-row" data-action-id="${a.id}" data-assigned="${esc(a.assigned_to || '')}">
                <td>${assetCell}</td>
                <td>${typeCell}</td>
                <td style="text-align:right">
                    <span class="days-pill${days >= 7 ? ' days-pill-high' : ''}">${days}d</span>
                </td>
                <td>${actionTypeBadge(a.type)}</td>
                <td>
                    <select class="dashboard-action-owner-select" data-action-id="${a.id}">
                        ${ownerOptions(a.assigned_to || "")}
                    </select>
                </td>
                <td>
                    <div class="status-pill-wrapper">
                        <button class="status-pill status-${a.status}" data-action-id="${a.id}" data-current="${a.status}">${a.status} <span class="pill-chevron">&#9662;</span></button>
                        <div class="status-dropdown" data-action-id="${a.id}">
                            ${statusOptions.map(s => `<div class="status-option status-${s}${s === a.status ? ' active' : ''}" data-value="${s}">${s}</div>`).join("")}
                        </div>
                    </div>
                </td>
            </tr>
        `;
    }).join("");

    return `
        <div class="alerts-table-wrap">
            <table class="alerts-table">
                <thead>
                    <tr>
                        <th style="width:34%">Asset</th>
                        <th style="width:10%">Type</th>
                        <th style="width:8%;text-align:right">Days</th>
                        <th style="width:16%">Issue</th>
                        <th style="width:16%">Owner</th>
                        <th style="width:16%">Status</th>
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

function bindDashboardAlertsRowControls() {
    // Clickable report cell - navigate to reports page and open detail
    document.querySelectorAll(".alerts-report-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const rid = parseInt(el.dataset.reportId);
            if (!rid) return;
            await navigate("reports");
            const rpt = (window._dashboardReports || []).find(r => r.id === rid);
            if (rpt) {
                showReportDetail(rpt);
            } else {
                // Fresh fetch in case dashboard cache is stale
                try {
                    const all = await api("/api/reports");
                    const found = all.find(r => r.id === rid);
                    if (found) showReportDetail(found);
                } catch (_) {}
            }
        });
    });

    // Clickable source cell - navigate to sources page and open detail
    document.querySelectorAll(".alerts-source-link").forEach(el => {
        el.addEventListener("click", async (e) => {
            e.stopPropagation();
            const sid = parseInt(el.dataset.sourceId);
            if (!sid) return;
            await navigate("sources");
            const src = (window._dashboardSources || []).find(s => s.id === sid);
            if (src) {
                showSourceDetail(src);
            } else {
                // Fresh fetch
                try {
                    const fresh = await api(`/api/sources/${sid}`);
                    if (fresh) showSourceDetail(fresh);
                } catch (_) {}
            }
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

    // Status pill dropdowns (reuse open/close behavior) + dashboard-aware state update
    if (!window._statusDropdownOutsideClick) {
        window._statusDropdownOutsideClick = true;
        document.addEventListener("click", (e) => {
            if (!e.target.closest(".status-pill-wrapper")) {
                document.querySelectorAll(".status-dropdown.visible").forEach(d => d.classList.remove("visible"));
                document.querySelectorAll(".status-pill.open").forEach(p => p.classList.remove("open"));
            }
        });
    }

    document.querySelectorAll(".alerts-table .status-pill").forEach(pill => {
        pill.addEventListener("click", (e) => {
            e.stopPropagation();
            const wrapper = pill.closest(".status-pill-wrapper");
            const dropdown = wrapper.querySelector(".status-dropdown");
            const wasOpen = dropdown.classList.contains("visible");
            document.querySelectorAll(".status-dropdown.visible").forEach(d => d.classList.remove("visible"));
            document.querySelectorAll(".status-pill.open").forEach(p => p.classList.remove("open"));
            if (!wasOpen) {
                dropdown.classList.add("visible");
                pill.classList.add("open");
            }
        });
    });

    document.querySelectorAll(".alerts-table .status-option").forEach(option => {
        option.addEventListener("click", async (e) => {
            e.stopPropagation();
            const dropdown = option.closest(".status-dropdown");
            const actionId = dropdown.dataset.actionId;
            const newStatus = option.dataset.value;
            dropdown.classList.remove("visible");
            try {
                await apiPatch(`/api/actions/${actionId}`, { status: newStatus });
                if (window._dashboardActions) {
                    const a = window._dashboardActions.find(x => x.id == actionId);
                    if (a) a.status = newStatus;
                }
                toast(`Alert updated to ${newStatus}`);
                // Re-render table (filter may now hide this row) and refresh chip counts
                const activeChip = document.querySelector(".alerts-chip.active");
                const person = activeChip ? activeChip.dataset.filterPerson : "all";
                const people = window._dashboardPeople || [];
                const biPeople = people.filter(p => p.role === "BI").map(p => p.name);
                const wrap = document.getElementById("dashboard-alerts-tbody-wrap");
                if (wrap) {
                    wrap.innerHTML = renderDashboardAlertsTable(window._dashboardActions || [], biPeople, person);
                    bindDashboardAlertsRowControls();
                }
                _refreshDashboardAlertChipCounts();
            } catch (err) {
                toast("Failed to update: " + err.message);
            }
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
    const maxVal = Math.max(...trend.map(t => (t.healthy || 0) + (t.degraded || 0)), 1);

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
    const xAt = (i) => padL + (i / (trend.length - 1)) * chartW;
    const yAt = (val) => padT + chartH - (val / maxVal) * chartH;

    // Build stacked y-values per point
    const series = trend.map(t => ({
        degraded: (t.degraded || 0) + (t.at_risk || 0),
        healthy: t.healthy || 0,
    }));

    const colors = {
        healthy: { fill: "rgba(22, 128, 61, 0.12)", stroke: "#15803d" },
        degraded: { fill: "rgba(185, 28, 28, 0.12)", stroke: "#b91c1c" },
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

    // Healthy area (top)
    ctx.beginPath();
    for (let i = 0; i < trend.length; i++) {
        const x = xAt(i);
        const total = series[i].degraded + series[i].healthy;
        const y = yAt(total);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let i = trend.length - 1; i >= 0; i--) {
        ctx.lineTo(xAt(i), yAt(series[i].degraded));
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
    drawLine(i => series[i].degraded + series[i].healthy, colors.healthy.stroke);
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
    ].forEach((item, idx) => {
        const x = legendX + idx * 72;
        ctx.fillStyle = item.color;
        ctx.fillRect(x, legendY - 6, 8, 8);
        ctx.fillStyle = labelColor;
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
    const total = s.healthy + s.degraded;
    const parts = t.day.split("-");
    const dayLabel = `${parts[2]}/${parts[1]}/${parts[0]}`;

    tip.innerHTML = `<div style="font-weight:600;margin-bottom:3px">${dayLabel}</div>
        <div><span style="color:#15803d">&#9679;</span> Healthy: ${s.healthy}</div>
        <div><span style="color:#b91c1c">&#9679;</span> Degraded: ${s.degraded}</div>
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
    const [sources, options] = await Promise.all([
        api("/api/sources" + (showArchived ? "?include_archived=true" : "")),
        api("/api/create/options"),
    ]);
    const people = options.people || [];
    const upstreams = options.upstream_systems || [];

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
            if (s.custom_fresh_days != null && s.custom_fresh_days > 0) b += ' <span style="font-size:0.65rem;color:var(--blue)" title="Custom freshness rule active">*</span>';
            return b;
        }, sortVal: s => ({ fresh: "0_healthy", stale: "1_degraded", outdated: "1_degraded", unknown: "2_unknown", no_connection: "2_no_connection", no_rule: "2_no_rule" })[s.status] ?? "3_" + s.status },
        { key: "last_updated", label: "Last Updated", width: COL_W.md, render: s => `<span style="color:var(--text-muted)" title="${s.last_updated || ''}">${s.last_updated ? timeAgo(s.last_updated) : "-"}</span>`, sortVal: s => s.last_updated || "" },
        { key: "custom_fresh_days", label: "Freshness", width: COL_W.sm, render: s => {
            if (s.custom_fresh_days == null || s.custom_fresh_days === 0) {
                return '<span style="color:var(--text-dim)" title="No freshness rule set">-</span>';
            }
            return `<span style="color:var(--text-muted)">${s.custom_fresh_days}d</span>`;
        }, sortVal: s => s.custom_fresh_days ?? -1 },
        { key: "age_days", label: "Age (days)", width: COL_W.sm, render: s => {
            const d = daysOld(s.last_updated);
            if (d === null) return '<span style="color:var(--text-dim)">-</span>';
            const threshold = s.custom_fresh_days;
            if (threshold == null || threshold === 0) {
                return `<span style="color:var(--text-muted)">${d}</span>`;
            }
            const color = d <= threshold ? "var(--green)" : "var(--red)";
            return `<span style="color:${color};font-weight:600">${d}</span>`;
        }, sortVal: s => daysOld(s.last_updated) ?? 9999 },
        { key: "report_count", label: "Reports", width: COL_W.sm, sortVal: s => s.report_count || 0 },
        { key: "owner", label: "Owner", width: COL_W.md, render: s => {
            const opts = people.map(p => `<option value="${esc(p.name)}"${s.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
            return `<select class="freq-select-inline source-owner-select" data-source-id="${s.id}"><option value="">--</option>${opts}</select>`;
        }, sortVal: s => s.owner || "" },
        { key: "linked_scripts", label: "Scripts", width: COL_W.sm, render: s => {
            if (!s.linked_scripts) return '-';
            return `<span class="badge badge-blue" title="${esc(s.linked_scripts)}" style="cursor:help">python</span>`;
        }, sortVal: s => s.linked_scripts ? "0_yes" : "1_no" },
        { key: "upstream_id", label: "Upstream", width: COL_W.md, render: s => {
            const opts = upstreams.map(u => `<option value="${u.id}"${s.upstream_id === u.id ? ' selected' : ''}>${esc(u.name)}</option>`).join("");
            return `<select class="freq-select-inline source-upstream-select" data-source-id="${s.id}"><option value="">None</option>${opts}</select>`;
        }, sortVal: s => {
            if (!s.upstream_id) return "";
            const u = upstreams.find(u => u.id === s.upstream_id);
            return u ? u.name : "";
        }},
        { key: "refresh_schedule", label: "Frequency", width: COL_W.md, render: s => {
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
        { key: "linked_task_count", label: "Tasks", width: COL_W.xs, render: s => {
            if (!s.linked_task_count) return '<span style="color:var(--text-dim)">-</span>';
            return `<span class="badge badge-blue" style="cursor:help" title="${s.linked_task_count} active task${s.linked_task_count !== 1 ? 's' : ''}">${s.linked_task_count}</span>`;
        }, sortVal: s => s.linked_task_count || 0 },
        _archiveColDef("source"),
    ];

    const active = sources.filter(s => !s.archived);
    const healthy = active.filter(s => s.status === "fresh").length;
    const degradedCount = active.filter(s => s.status === "outdated" || s.status === "stale").length;

    const scriptCount = sources.filter(s => !s.archived && s.linked_scripts).length;
    const excelCount = sources.filter(s => !s.archived && (s.type === "excel" || s.type === "csv")).length;
    const pgCount = sources.filter(s => !s.archived && s.type === "postgresql").length;
    const unhealthyCount = degradedCount;

    return `
        <div class="page-header">
            <h1>Sources</h1>
            <span class="subtitle">${active.length} data sources tracked - ${healthy} healthy, ${degradedCount} degraded</span>
            ${_archiveToggleHtml("sources")}
            <button class="btn-export" onclick="exportTableCSV('dt-sources','sources.csv')">Export CSV</button>
        </div>
        <div class="source-filters" style="display:flex;gap:0.4rem;margin-bottom:0.75rem;flex-wrap:wrap">
            <button class="btn-sm source-filter-btn" data-filter="all">All (${active.length})</button>
            <button class="btn-sm btn-outline source-filter-btn" data-filter="excel">Excel/CSV (${excelCount})</button>
            <button class="btn-sm btn-outline source-filter-btn" data-filter="postgresql">PostgreSQL (${pgCount})</button>
            <button class="btn-sm btn-outline source-filter-btn" data-filter="has-script">Has Script (${scriptCount})</button>
            <button class="btn-sm btn-outline source-filter-btn" data-filter="unhealthy" style="${unhealthyCount > 0 ? 'color:var(--red);border-color:var(--red)' : ''}">Not Healthy (${unhealthyCount})</button>
        </div>
        ${dataTable("dt-sources", cols, sources, { onRowClick: showSourceDetail })}
    `;
}

function bindSourcesPage() {
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
                dt.filters["linked_scripts"] = "python";
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

    // Inline owner select dropdowns for sources
    document.querySelectorAll(".source-owner-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const sourceId = sel.dataset.sourceId;
            try {
                await apiPatch(`/api/sources/${sourceId}`, { owner: sel.value });
                toast("Owner updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });
    // Inline upstream select dropdowns for sources
    document.querySelectorAll(".source-upstream-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const sourceId = sel.dataset.sourceId;
            const val = sel.value ? parseInt(sel.value) : null;
            try {
                await apiPatch(`/api/sources/${sourceId}`, { upstream_id: val });
                toast("Upstream updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });
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
        { key: "status", label: "Status", width: COL_W.sm, render: r => statusBadge(r.status), sortVal: r => ({ healthy: "0_healthy", degraded: "1_degraded" })[r.status] ?? "2_" + r.status },
        { key: "source_count", label: "Sources", width: COL_W.sm, sortVal: r => r.source_count || 0 },
        { key: "_doc_pct", label: "Doc %", width: COL_W.sm, filterable: false, render: r => {
            const doc = docMap.get(r.id);
            if (!doc) return '<span style="color:var(--text-dim)">0%</span>';
            const fields = [doc.business_purpose, doc.business_audience, doc.technical_transformations];
            const filled = fields.filter(f => f && f.trim()).length;
            const pct = Math.round((filled / 3) * 100);
            const cls = pct === 100 ? 'badge-green' : pct >= 50 ? 'badge-yellow' : 'badge-muted';
            return `<span class="badge ${cls}">${pct}%</span>`;
        }, sortVal: r => {
            const doc = docMap.get(r.id);
            if (!doc) return 0;
            const fields = [doc.business_purpose, doc.business_audience, doc.technical_transformations];
            return fields.filter(f => f && f.trim()).length;
        }},
        { key: "views_30d", label: "Views (30d)", width: COL_W.sm, render: r => {
            if (!r.views_30d) return '<span style="color:var(--text-dim)">-</span>';
            const title = r.unique_users_30d ? `${r.views_30d} views by ${r.unique_users_30d} user${r.unique_users_30d !== 1 ? 's' : ''}` : `${r.views_30d} views`;
            return `<span style="cursor:help" title="${title}">${r.views_30d}</span>`;
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
        { key: "pbi_refresh_schedule", label: "PBI Schedule", width: COL_W.md, render: r => r.pbi_refresh_schedule ? `<span style="font-size:0.78rem">${esc(r.pbi_refresh_schedule)}</span>` : '-' },
        { key: "pbi_last_refresh_at", label: "Last Refresh", width: COL_W.md, render: r => r.pbi_last_refresh_at ? `<span title="${formatDate(r.pbi_last_refresh_at)}">${timeAgo(r.pbi_last_refresh_at)}</span>` : '-' },
        { key: "linked_task_count", label: "Tasks", width: COL_W.xs, render: r => {
            if (!r.linked_task_count) return '<span style="color:var(--text-dim)">-</span>';
            return `<span class="badge badge-blue" style="cursor:help" title="${r.linked_task_count} active task${r.linked_task_count !== 1 ? 's' : ''}">${r.linked_task_count}</span>`;
        }, sortVal: r => r.linked_task_count || 0 },
        { key: "_lineage", label: "Lineage", width: COL_W.xs, filterable: false, sortable: false, render: r =>
            `<button class="btn-table-link btn-lineage" data-lineage-report="${r.id}" title="View lineage diagram" onclick="event.stopPropagation()">View</button>` },
        _archiveColDef("report"),
    ];

    const active = reports.filter(r => !r.archived);
    const healthy = active.filter(r => r.status === "healthy").length;
    const atRisk = active.filter(r => r.status !== "healthy" && r.status !== "unknown").length;
    const overdue = active.filter(r => _isPbiOverdue(r)).length;

    // Store sources for inline expansion lookups
    window._reportPageSources = sources;

    return `
        <div class="page-header">
            <h1>Reports</h1>
            <span class="subtitle">${active.length} Power BI reports - ${healthy} healthy${atRisk ? `, ${atRisk} need attention` : ''}${overdue ? `, <span style="color:var(--red)">${overdue} overdue</span>` : ''}</span>
            ${_archiveToggleHtml("reports")}
            ${_isLocal() ? '<button class="btn-outline" id="btn-pbi-sync" style="font-size:0.78rem">Sync PBI</button>' : ''}
            ${_isLocal() ? '<button class="btn-outline" id="btn-pbi-usage-sync" style="font-size:0.78rem">Sync Usage</button>' : ''}
            <span class="info-tip-wrap"><span class="info-tip-icon">?</span><span class="info-tip-box">PBI Status checks if a report's last refresh matches its schedule cadence.<br><br><strong>Overdue thresholds</strong><br>Daily (7/week): 2 days<br>Business days (5/week): ~2.5 days<br>3x/week: ~3.5 days<br>2x/week: ~4.5 days<br>Weekly (1/week): 8 days<br><br>Overdue reports generate alerts automatically.</span></span>
            <button class="btn-outline" id="btn-generate-all-docs" style="font-size:0.78rem">Generate All Docs</button>
            <button class="btn-export" onclick="exportTableCSV('dt-reports','reports.csv')">Export CSV</button>
        </div>

        ${dataTable("dt-reports", cols, reports, { onRowClick: showReportDetail })}
    `;
}

function bindReportsPage() {
    // Inline report owner select dropdowns
    document.querySelectorAll(".report-owner-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const reportId = sel.dataset.reportId;
            try {
                await apiPatch(`/api/reports/${reportId}`, { owner: sel.value });
                toast("Report owner updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });
    // Inline business owner select dropdowns
    document.querySelectorAll(".report-bo-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const reportId = sel.dataset.reportId;
            try {
                await apiPatch(`/api/reports/${reportId}`, { business_owner: sel.value });
                toast("Business owner updated");
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
                    toast("PBI sync launched - check the PowerShell window on your desktop");
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
            if (!confirm("This will use AI to generate documentation for all reports that don't have complete docs yet. Reports with 5+ fields filled will be skipped.\n\nThis may take a few minutes. Continue?")) return;
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
                btnGenDocs.textContent = "Generate All Docs";
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
                    toast("Usage sync launched - check the PowerShell window");
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
            ${_isLocal() ? '<button id="btn-scan">Run Scan Now</button>' : ''}
            ${_isLocal() ? '<button id="btn-probe" class="btn-outline">Probe Sources</button>' : ''}
            <button id="btn-diagnose" class="btn-outline">Diagnose</button>
            <span style="color:var(--text-dim);font-size:0.78rem">
                ${lastRun ? `Last scan: ${timeAgo(lastRun.started_at)}` : "No scans yet"}
                ${lastProbe ? ` · Last probe: ${timeAgo(lastProbe.started_at)}` : ""}
            </span>
        </div>

        <div id="diagnose-panel" style="display:none"></div>

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
                    { key: "stale", label: "Degraded", width: COL_W.sm, render: r => r.stale ? `<span style="color:var(--red)">${r.stale}</span>` : '-' },
                    { key: "outdated", label: "Degraded", width: COL_W.sm, render: r => r.outdated ? `<span style="color:var(--red)">${r.outdated}</span>` : '-' },
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
                await fetch(`/api/people/${personId}`, { method: 'DELETE' });
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
            <legend style="font-weight:600;font-size:0.82rem;padding:0 0.4rem">Scripts & Tasks</legend>
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
    if (t.startsWith("bi_reporting.")) return { label: "BI Reporting", cls: "badge-blue" };
    if (t.startsWith("smartswitch.")) return { label: "SmartSwitch", cls: "badge-purple" };
    if (t.startsWith("samsung_health.")) return { label: "Samsung Health", cls: "badge-purple" };
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
    let filtered = scripts;
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
            <span class="subtitle">${activeCount}${machineFilter || scriptTypeFilter !== 'all' ? ` of ${totalCount}` : ''} scripts${machineFilter ? ` on ${machineFilter}` : ''}</span>
            ${_isLocal() ? '<button class="btn-outline" id="btn-scan-scripts-full" style="margin-left:0.5rem">Full Scan</button>' : ''}
            ${_isLocal() ? '<button class="btn-outline" id="btn-scan-scripts-new">Scan New</button>' : ''}
            ${_isLocal() ? '<button class="btn-outline" id="btn-reparse-scripts" title="Re-read and re-parse known scripts (no directory walk)">Re-parse</button>' : ''}
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

    // Inline owner select dropdowns
    document.querySelectorAll(".script-owner-select").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const scriptId = sel.dataset.scriptId;
            try {
                await apiPatch(`/api/scripts/${scriptId}`, { owner: sel.value });
                toast("Owner updated");
            } catch (err) {
                toast("Failed: " + err.message);
            }
        });
        sel.addEventListener("click", (e) => e.stopPropagation());
    });

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
    const tasks = await api("/api/scheduled-tasks" + (showArchived ? "?include_archived=true" : ""));
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
            if (!t.last_result) return '<span style="color:var(--text-dim)">-</span>';
            const ok = t.last_result === "0";
            return `<span class="badge ${ok ? 'badge-green' : 'badge-red'}">${ok ? 'OK' : 'Failed'}</span>`;
        }, sortVal: t => t.last_result || "" },
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
    const failedCount = active.filter(t => t.last_result && t.last_result !== "0").length;
    const linkedCount = active.filter(t => t.script_id).length;
    const disabledCount = active.filter(t => !t.enabled).length;
    const failedNote = failedCount > 0 ? ` <span class="badge badge-red" style="font-size:0.72rem">${failedCount} failed</span>` : "";
    const disabledNote = disabledCount > 0 ? ` <span class="badge badge-dim" style="font-size:0.72rem">${disabledCount} disabled</span>` : "";

    return `
        <div class="page-header">
            <h1>Scheduled Tasks</h1>
            <span class="subtitle">${active.length} tasks, ${linkedCount} linked${failedNote}${disabledNote}</span>
            ${_isLocal() ? '<button class="btn-outline" id="btn-scan-schtasks-full" style="margin-left:0.5rem">Full Scan</button>' : ''}
            ${_isLocal() ? '<button class="btn-outline" id="btn-scan-schtasks-new">Scan New</button>' : ''}
            <select id="schtasks-machine-filter" class="freq-select-inline" style="font-size:0.75rem;margin-left:0.25rem"><option value="">All Machines</option>${machineOpts}</select>
            <select id="schtasks-cat-filter" class="freq-select-inline" style="font-size:0.75rem;margin-left:0.25rem"><option value="">All Categories</option>${catOpts}</select>
            ${_archiveToggleHtml("scheduledtasks")}
            <button class="btn-export" onclick="exportTableCSV('dt-schtasks','scheduled_tasks.csv')">Export CSV</button>
        </div>
        ${filtered.length === 0
            ? '<div class="empty-state" style="margin-top:2rem">No scheduled tasks found. Click <strong>Scan Task Scheduler</strong> to import from Windows Task Scheduler.<br><span style="color:var(--text-dim);font-size:0.8rem">This feature only works on Windows.</span></div>'
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

    const resultBadge = !task.last_result ? '-'
        : task.last_result === "0" ? '<span class="badge badge-green">0 (Success)</span>'
        : `<span class="badge badge-red">${esc(task.last_result)} (Failed)</span>`;

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
                <div class="create-field"><label>Account</label><input id="pa-f-account" value="${esc(f.account || "")}" placeholder="e.g. user@samsung.com"></div>
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


// ── Custom Reports ──

const _CR_STATUS_BADGE = { active: "badge-green", paused: "badge-yellow", archived: "badge-muted" };

async function renderCustomReports() {
    const showArchived = _isShowingArchived("customreports");
    const reports = await api("/api/custom-reports" + (showArchived ? "?include_archived=true" : ""));
    const active = reports.filter(r => !r.archived);

    const cols = [
        { key: "name", label: "Name", width: COL_W.xl, render: r => `<strong>${esc(r.name)}</strong>`, sortVal: r => r.name || "" },
        { key: "status", label: "Status", width: COL_W.sm, render: r => {
            const cls = _CR_STATUS_BADGE[r.status] || "badge-muted";
            return `<span class="badge ${cls}">${esc(r.status || "unknown")}</span>`;
        }, sortVal: r => r.status || "" },
        { key: "frequency", label: "Frequency", width: COL_W.md, render: r => r.frequency ? esc(r.frequency) : '<span style="color:var(--text-dim)">-</span>', sortVal: r => r.frequency || "" },
        { key: "owner", label: "Owner", width: COL_W.md, render: r => r.owner ? esc(r.owner) : '<span style="color:var(--text-dim)">-</span>', sortVal: r => r.owner || "" },
        { key: "stakeholders", label: "Stakeholders", width: COL_W.lg, render: r => r.stakeholders ? `<span style="color:var(--text-muted);font-size:0.78rem">${esc(r.stakeholders)}</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: r => r.stakeholders || "" },
        { key: "estimated_hours", label: "Est. Hours", width: COL_W.sm, render: r => r.estimated_hours != null ? `<span style="color:var(--text-muted)">${r.estimated_hours}h</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: r => r.estimated_hours ?? 0 },
        { key: "last_completed", label: "Last Completed", width: COL_W.md, render: r => r.last_completed ? `<span style="color:var(--text-muted)" title="${esc(r.last_completed)}">${timeAgo(r.last_completed)}</span>` : '<span style="color:var(--text-dim)">-</span>', sortVal: r => r.last_completed || "" },
        { key: "tags", label: "Tags", width: COL_W.md, render: r => r.tags ? r.tags.split(",").map(t => `<span class="badge badge-muted" style="font-size:0.68rem;margin-right:0.2rem">${esc(t.trim())}</span>`).join("") : '<span style="color:var(--text-dim)">-</span>', sortVal: r => r.tags || "" },
        _archiveColDef("custom_report"),
    ];

    return `
        <div class="page-header">
            <h1>Custom Reports</h1>
            <span class="subtitle">${active.length} report${active.length !== 1 ? 's' : ''}</span>
            <button class="btn-outline" id="btn-cr-new" style="margin-left:0.5rem">+ New Report</button>
            ${_archiveToggleHtml("customreports")}
            <button class="btn-export" onclick="exportTableCSV('dt-custom-reports','custom_reports.csv')">Export CSV</button>
        </div>
        <div id="cr-create-form-area"></div>
        ${reports.length === 0
            ? '<div class="empty-state" style="margin-top:2rem">No custom reports yet. Click <strong>+ New Report</strong> to document a recurring task.</div>'
            : dataTable("dt-custom-reports", cols, reports, { onRowClick: showCustomReportDetail })
        }
    `;
}

async function showCustomReportDetail(report) {
    const existing = $("#cr-detail");
    if (existing) existing.remove();

    const panel = document.createElement("div");
    panel.id = "cr-detail";
    panel.className = "source-detail-panel";

    const statusCls = _CR_STATUS_BADGE[report.status] || "badge-muted";
    const tagsHtml = report.tags
        ? report.tags.split(",").map(t => `<span class="badge badge-muted" style="font-size:0.72rem;margin-right:0.2rem">${esc(t.trim())}</span>`).join("")
        : '<span style="color:var(--text-dim)">-</span>';

    panel.innerHTML = `
        <div class="source-detail-header">
            <h2>${esc(report.name)}</h2>
            <button class="btn-outline" id="btn-cr-edit" style="margin-right:0.25rem">Edit</button>
            <button class="btn-outline" id="btn-cr-delete" style="margin-right:0.25rem;color:var(--red)">Delete</button>
            <button class="btn-outline" id="btn-close-cr-detail">&times; Close</button>
        </div>
        <div class="detail-grid">
            <div class="detail-item"><div class="detail-label">Status</div><span class="badge ${statusCls}">${esc(report.status || "unknown")}</span></div>
            <div class="detail-item"><div class="detail-label">Frequency</div><span style="color:var(--text)">${esc(report.frequency || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Owner</div><span style="color:var(--text)">${esc(report.owner || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Est. Hours</div><span style="color:var(--text)">${report.estimated_hours != null ? report.estimated_hours + "h" : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Last Completed</div><span style="color:var(--text)">${report.last_completed ? formatDate(report.last_completed) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Tags</div>${tagsHtml}</div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Stakeholders</div><span style="color:var(--text)">${esc(report.stakeholders || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Description</div><span style="color:var(--text)">${esc(report.description || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Data Sources</div><span style="color:var(--text)">${esc(report.data_sources || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Output</div><span style="color:var(--text)">${esc(report.output_description || "-")}</span></div>
            <div class="detail-item" style="grid-column:1/-1"><div class="detail-label">Steps / Documentation</div><span style="color:var(--text);white-space:pre-wrap">${esc(report.steps || "-")}</span></div>
            <div class="detail-item"><div class="detail-label">Created</div><span style="color:var(--text-dim)">${report.created_at ? formatDate(report.created_at) : "-"}</span></div>
            <div class="detail-item"><div class="detail-label">Updated</div><span style="color:var(--text-dim)">${report.updated_at ? formatDate(report.updated_at) : "-"}</span></div>
        </div>
    `;

    $("#app").appendChild(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    document.getElementById("btn-close-cr-detail").addEventListener("click", () => panel.remove());

    document.getElementById("btn-cr-delete").addEventListener("click", async () => {
        if (!confirm(`Delete custom report "${report.name}"?`)) return;
        try {
            await apiDelete(`/api/custom-reports/${report.id}`);
            toast("Report deleted");
            panel.remove();
            navigate("customreports");
        } catch (err) {
            toast("Delete failed: " + err.message);
        }
    });

    document.getElementById("btn-cr-edit").addEventListener("click", () => {
        panel.remove();
        _showCrEditForm(report);
    });
}

async function _renderCrForm(existing) {
    const opts = await api("/api/custom-reports/options");
    const r = existing || {};
    const isEdit = !!existing;

    const ownerOptions = opts.people.length > 0
        ? opts.people.map(p => `<option value="${esc(p.name)}"${r.owner === p.name ? ' selected' : ''}>${esc(p.name)} (${esc(p.role)})</option>`).join("")
        : opts.owners.map(o => `<option value="${esc(o)}"${r.owner === o ? ' selected' : ''}>${esc(o)}</option>`).join("");

    const statusOptions = opts.statuses.map(s => `<option value="${s}"${(r.status || 'active') === s ? ' selected' : ''}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`).join("");

    const freqOptions = opts.frequencies.map(f => `<option value="${f}"${r.frequency === f ? ' selected' : ''}>${f}</option>`).join("");

    return `
        <div class="create-form" id="cr-form" style="margin-bottom:1.5rem">
            <h3 style="margin:0 0 0.75rem">${isEdit ? "Edit" : "New"} Custom Report</h3>
            <div class="create-fields">
                <div class="create-field"><label>Name *</label><input id="cr-f-name" value="${esc(r.name || "")}" required></div>
                <div class="create-field"><label>Status</label><select id="cr-f-status">${statusOptions}</select></div>
                <div class="create-field"><label>Owner</label><select id="cr-f-owner"><option value="">-</option>${ownerOptions}</select></div>
                <div class="create-field"><label>Frequency</label><select id="cr-f-frequency"><option value="">-</option>${freqOptions}</select></div>
                <div class="create-field"><label>Est. Hours</label><input id="cr-f-hours" type="number" step="0.5" min="0" value="${r.estimated_hours != null ? r.estimated_hours : ""}"></div>
                <div class="create-field"><label>Tags</label><input id="cr-f-tags" value="${esc(r.tags || "")}" placeholder="comma-separated"></div>
                <div class="create-field" style="grid-column:1/-1"><label>Stakeholders</label><input id="cr-f-stakeholders" value="${esc(r.stakeholders || "")}" placeholder="e.g. SETK, SEMAG, Finance team"></div>
                <div class="create-field" style="grid-column:1/-1"><label>Description</label><textarea id="cr-f-desc" rows="2" style="width:100%">${esc(r.description || "")}</textarea></div>
                <div class="create-field" style="grid-column:1/-1"><label>Data Sources</label><textarea id="cr-f-data-sources" rows="2" style="width:100%" placeholder="Where does the data come from?">${esc(r.data_sources || "")}</textarea></div>
                <div class="create-field" style="grid-column:1/-1"><label>Output</label><textarea id="cr-f-output" rows="2" style="width:100%" placeholder="What gets produced?">${esc(r.output_description || "")}</textarea></div>
                <div class="create-field" style="grid-column:1/-1"><label>Steps / Documentation</label><textarea id="cr-f-steps" rows="6" style="width:100%" placeholder="Document the process, steps, or any relevant notes...">${esc(r.steps || "")}</textarea></div>
            </div>
            <div style="margin-top:0.75rem;display:flex;gap:0.5rem">
                <button class="btn-outline" id="cr-f-save">${isEdit ? "Save Changes" : "Create Report"}</button>
                <button class="btn-outline" id="cr-f-cancel">Cancel</button>
            </div>
        </div>
    `;
}

function _collectCrFormData() {
    const name = document.getElementById("cr-f-name").value.trim();
    if (!name) { toast("Name is required"); return null; }
    const hoursVal = document.getElementById("cr-f-hours").value;
    return {
        name,
        status: document.getElementById("cr-f-status").value,
        owner: document.getElementById("cr-f-owner").value || null,
        frequency: document.getElementById("cr-f-frequency").value || null,
        estimated_hours: hoursVal ? parseFloat(hoursVal) : null,
        tags: document.getElementById("cr-f-tags").value.trim() || null,
        stakeholders: document.getElementById("cr-f-stakeholders").value.trim() || null,
        description: document.getElementById("cr-f-desc").value.trim() || null,
        data_sources: document.getElementById("cr-f-data-sources").value.trim() || null,
        output_description: document.getElementById("cr-f-output").value.trim() || null,
        steps: document.getElementById("cr-f-steps").value.trim() || null,
    };
}

async function _showCrCreateForm() {
    const area = document.getElementById("cr-create-form-area");
    if (!area) return;
    area.innerHTML = await _renderCrForm(null);

    document.getElementById("cr-f-cancel").addEventListener("click", () => { area.innerHTML = ""; });
    document.getElementById("cr-f-save").addEventListener("click", async () => {
        const data = _collectCrFormData();
        if (!data) return;
        try {
            await apiPostJson("/api/custom-reports", data);
            toast("Report created");
            navigate("customreports");
        } catch (err) {
            toast("Create failed: " + err.message);
        }
    });
}

async function _showCrEditForm(report) {
    const area = document.getElementById("cr-create-form-area");
    if (!area) return;
    area.innerHTML = await _renderCrForm(report);
    area.scrollIntoView({ behavior: "smooth", block: "nearest" });

    document.getElementById("cr-f-cancel").addEventListener("click", () => {
        area.innerHTML = "";
        showCustomReportDetail(report);
    });
    document.getElementById("cr-f-save").addEventListener("click", async () => {
        const data = _collectCrFormData();
        if (!data) return;
        try {
            const updated = await apiPatch(`/api/custom-reports/${report.id}`, data);
            toast("Report updated");
            area.innerHTML = "";
            navigate("customreports");
        } catch (err) {
            toast("Update failed: " + err.message);
        }
    });
}

function bindCustomReportsPage() {
    const btnNew = document.getElementById("btn-cr-new");
    if (btnNew) btnNew.addEventListener("click", () => _showCrCreateForm());
    _bindArchiveButtons(() => navigate("customreports"));
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


// ── Pipeline Overview (force-directed graph) ──

const OV_COLORS = { report: "#60a5fa", source: "#34d399", upstream: "#fb923c", script: "#c4b5fd", task: "#fbbf24" };
const OV_LABELS = { report: "Reports", source: "Sources", upstream: "Upstream Systems", script: "Scripts", task: "Scheduled Tasks" };
const OV_RADIUS = { report: 10, source: 7, upstream: 12, script: 8, task: 6 };
const OV_LAYER_X = { task: 0.06, script: 0.22, upstream: 0.42, source: 0.65, report: 0.88 };

async function renderOverview() {
    return `
        <div class="page-header">
            <h2>Pipeline Overview</h2>
            <p class="page-subtitle">Interactive map of all reports, sources, scripts, and upstream systems</p>
        </div>
        <div class="ov-toolbar">
            <div class="ov-legend" id="ov-legend"></div>
            <div class="ov-stats" id="ov-stats"></div>
            <div class="ov-actions">
                <button class="btn-outline btn-sm" id="ov-reset" title="Reset zoom and position">Reset View</button>
            </div>
        </div>
        <div class="ov-container" id="ov-container">
            <canvas id="ov-canvas"></canvas>
            <div class="ov-tooltip" id="ov-tooltip" style="display:none"></div>
        </div>
        <div class="ov-hint">Scroll to zoom. Drag background to pan. Drag nodes to rearrange. Click a node to trace its connections.</div>
    `;
}

function bindOverviewPage() {
    const container = document.getElementById("ov-container");
    const canvas = document.getElementById("ov-canvas");
    if (!container || !canvas) return;

    container.innerHTML = '<canvas id="ov-canvas"></canvas><div class="ov-tooltip" id="ov-tooltip" style="display:none"></div><div class="ov-loading">Loading pipeline data...</div>';

    api("/api/overview/graph").then(data => {
        const loader = container.querySelector(".ov-loading");
        if (loader) loader.remove();
        _initOverviewGraph(data, container);
    }).catch(err => {
        container.innerHTML = `<div class="ov-loading" style="color:var(--red)">Failed to load: ${esc(err.message)}</div>`;
    });
}

function _initOverviewGraph(data, container) {
    const canvas = container.querySelector("canvas");
    const tooltip = container.querySelector(".ov-tooltip");
    if (!canvas) return;

    // Build legend and stats
    const legendEl = document.getElementById("ov-legend");
    const statsEl = document.getElementById("ov-stats");
    if (legendEl) {
        const types = ["upstream", "source", "report", "script", "task"];
        legendEl.innerHTML = types.map(t => {
            const count = data.nodes.filter(n => n.type === t).length;
            if (count === 0) return "";
            return `<span class="ov-legend-item"><span class="ov-legend-dot" style="background:${OV_COLORS[t]}"></span>${OV_LABELS[t]} (${count})</span>`;
        }).join("");
    }
    if (statsEl) {
        statsEl.innerHTML = `${data.nodes.length} nodes, ${data.edges.length} connections`;
    }

    // Canvas setup
    const dpr = window.devicePixelRatio || 1;
    let W = container.clientWidth;
    let H = container.clientHeight;

    function resizeCanvas() {
        W = container.clientWidth;
        H = container.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.width = W + "px";
        canvas.style.height = H + "px";
    }
    resizeCanvas();

    const ctx = canvas.getContext("2d");

    // Initialize nodes with layered positions
    const byType = {};
    data.nodes.forEach(n => { if (!byType[n.type]) byType[n.type] = []; byType[n.type].push(n); });

    const nodes = data.nodes.map(n => {
        const peers = byType[n.type];
        const idx = peers.indexOf(n);
        const spacing = H / (peers.length + 1);
        const lx = OV_LAYER_X[n.type] || 0.5;
        return {
            ...n,
            x: lx * W + (Math.random() - 0.5) * 60,
            y: spacing * (idx + 1) + (Math.random() - 0.5) * 30,
            vx: 0, vy: 0, fx: null, fy: null,
            r: OV_RADIUS[n.type] || 7,
            color: OV_COLORS[n.type] || "#888",
        };
    });

    // Node index for fast lookup
    const nodeIdx = new Map();
    nodes.forEach(n => nodeIdx.set(n.id, n));

    // Build edges (only valid ones)
    const edges = data.edges.map(e => ({
        source: nodeIdx.get(e.source),
        target: nodeIdx.get(e.target),
    })).filter(e => e.source && e.target);

    // Adjacency for highlight tracing
    const adjFwd = new Map();
    const adjBwd = new Map();
    edges.forEach(e => {
        if (!adjFwd.has(e.source.id)) adjFwd.set(e.source.id, []);
        adjFwd.get(e.source.id).push(e.target.id);
        if (!adjBwd.has(e.target.id)) adjBwd.set(e.target.id, []);
        adjBwd.get(e.target.id).push(e.source.id);
    });

    // Transform state (pan/zoom)
    let tx = 0, ty = 0, scale = 1;

    // Interaction state
    let dragNode = null;
    let dragOffset = { x: 0, y: 0 };
    let isPanning = false;
    let panStart = { x: 0, y: 0 };
    let hoveredNode = null;
    let selectedId = null;
    let highlightSet = null;

    // Convert screen coords to world coords
    function toWorld(sx, sy) {
        return { x: (sx - tx) / scale, y: (sy - ty) / scale };
    }

    // Find node under screen coords
    function hitTest(sx, sy) {
        const w = toWorld(sx, sy);
        for (let i = nodes.length - 1; i >= 0; i--) {
            const n = nodes[i];
            const dx = w.x - n.x, dy = w.y - n.y;
            if (dx * dx + dy * dy < (n.r + 4) * (n.r + 4)) return n;
        }
        return null;
    }

    // Trace all connected nodes from a start node
    function traceConnections(startId) {
        const visited = new Set();
        // Forward
        const q = [startId];
        while (q.length) {
            const c = q.pop();
            if (visited.has(c)) continue;
            visited.add(c);
            const fwd = adjFwd.get(c);
            if (fwd) fwd.forEach(id => { if (!visited.has(id)) q.push(id); });
        }
        // Backward
        const q2 = [startId];
        const bwdSeen = new Set();
        while (q2.length) {
            const c = q2.pop();
            if (bwdSeen.has(c)) continue;
            bwdSeen.add(c);
            visited.add(c);
            const bwd = adjBwd.get(c);
            if (bwd) bwd.forEach(id => { if (!bwdSeen.has(id)) q2.push(id); });
        }
        return visited;
    }

    // ── Force simulation ──
    let simAlpha = 1.0;
    const SIM_DECAY = 0.985;
    const SIM_MIN = 0.001;

    function simTick() {
        const N = nodes.length;
        // Repulsion (charge)
        for (let i = 0; i < N; i++) {
            for (let j = i + 1; j < N; j++) {
                const a = nodes[i], b = nodes[j];
                let dx = b.x - a.x, dy = b.y - a.y;
                let d2 = dx * dx + dy * dy;
                if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
                const d = Math.sqrt(d2);
                const force = 800 / d2 * simAlpha;
                const fx = dx / d * force, fy = dy / d * force;
                a.vx -= fx; a.vy -= fy;
                b.vx += fx; b.vy += fy;
            }
        }
        // Spring (edges)
        const idealLen = 120;
        for (const e of edges) {
            const dx = e.target.x - e.source.x;
            const dy = e.target.y - e.source.y;
            const d = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = (d - idealLen) * 0.003 * simAlpha;
            const fx = dx / d * force, fy = dy / d * force;
            e.source.vx += fx; e.source.vy += fy;
            e.target.vx -= fx; e.target.vy -= fy;
        }
        // Layer force (pull toward preferred X)
        for (const n of nodes) {
            const targetX = (OV_LAYER_X[n.type] || 0.5) * W;
            n.vx += (targetX - n.x) * 0.005 * simAlpha;
        }
        // Center Y
        for (const n of nodes) {
            n.vy += (H / 2 - n.y) * 0.0005 * simAlpha;
        }
        // Apply velocity
        for (const n of nodes) {
            if (n.fx != null) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; continue; }
            n.vx *= 0.85;
            n.vy *= 0.85;
            n.x += n.vx;
            n.y += n.vy;
        }
        simAlpha *= SIM_DECAY;
    }

    // ── Rendering ──
    function _isDark() {
        const html = document.documentElement;
        return html.classList.contains("dark") ||
            (!html.classList.contains("light") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    }

    function draw() {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, W, H);
        ctx.save();
        ctx.translate(tx, ty);
        ctx.scale(scale, scale);

        const dark = _isDark();
        const dimming = highlightSet != null;

        // Draw edges
        for (const e of edges) {
            const hl = highlightSet && highlightSet.has(e.source.id) && highlightSet.has(e.target.id);
            ctx.beginPath();
            const mx = (e.source.x + e.target.x) / 2;
            ctx.moveTo(e.source.x, e.source.y);
            ctx.bezierCurveTo(mx, e.source.y, mx, e.target.y, e.target.x, e.target.y);
            if (dimming && !hl) {
                ctx.strokeStyle = dark ? "rgba(100,100,100,0.06)" : "rgba(150,150,150,0.06)";
                ctx.lineWidth = 0.5;
            } else if (hl) {
                ctx.strokeStyle = dark ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.3)";
                ctx.lineWidth = 1.5;
            } else {
                ctx.strokeStyle = dark ? "rgba(150,150,150,0.15)" : "rgba(100,100,100,0.18)";
                ctx.lineWidth = 0.8;
            }
            ctx.stroke();
        }

        // Draw nodes
        for (const n of nodes) {
            const hl = highlightSet ? highlightSet.has(n.id) : true;
            const isHovered = hoveredNode === n;

            ctx.beginPath();
            ctx.arc(n.x, n.y, n.r + (isHovered ? 2 : 0), 0, Math.PI * 2);

            if (dimming && !hl) {
                ctx.fillStyle = dark ? "rgba(100,100,100,0.15)" : "rgba(180,180,180,0.25)";
            } else {
                ctx.fillStyle = n.color;
                if (isHovered) {
                    ctx.shadowColor = n.color;
                    ctx.shadowBlur = 12;
                }
            }
            ctx.fill();
            ctx.shadowBlur = 0;

            // Border
            if (hl || !dimming) {
                ctx.strokeStyle = dark
                    ? (dimming ? "rgba(255,255,255,0.5)" : "rgba(255,255,255,0.2)")
                    : (dimming ? "rgba(0,0,0,0.4)" : "rgba(0,0,0,0.12)");
                ctx.lineWidth = isHovered ? 2 : 0.5;
                ctx.stroke();
            }

            // Label
            if (scale > 0.5 || isHovered || (highlightSet && hl)) {
                const showLabel = scale > 0.7 || isHovered || (highlightSet && hl);
                if (showLabel) {
                    const fontSize = Math.max(9, Math.min(11, 11 / scale));
                    ctx.font = `${fontSize}px 'Outfit', sans-serif`;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "top";
                    if (dimming && !hl) {
                        ctx.fillStyle = dark ? "rgba(100,100,100,0.2)" : "rgba(180,180,180,0.3)";
                    } else {
                        ctx.fillStyle = dark ? "rgba(255,255,255,0.85)" : "rgba(30,30,30,0.85)";
                    }
                    let label = n.name;
                    if (label.length > 30) label = label.substring(0, 28) + "...";
                    ctx.fillText(label, n.x, n.y + n.r + 4);
                }
            }
        }

        ctx.restore();
    }

    // ── Animation loop ──
    let animId = null;
    function animate() {
        if (simAlpha > SIM_MIN) simTick();
        draw();
        animId = requestAnimationFrame(animate);
    }

    // ── Mouse events ──
    canvas.addEventListener("mousedown", e => {
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        const node = hitTest(sx, sy);
        if (node) {
            dragNode = node;
            const w = toWorld(sx, sy);
            dragOffset.x = node.x - w.x;
            dragOffset.y = node.y - w.y;
            node.fx = node.x;
            node.fy = node.y;
            simAlpha = Math.max(simAlpha, 0.1);
        } else {
            isPanning = true;
            panStart.x = e.clientX - tx;
            panStart.y = e.clientY - ty;
        }
    });

    canvas.addEventListener("mousemove", e => {
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

        if (dragNode) {
            const w = toWorld(sx, sy);
            dragNode.fx = w.x + dragOffset.x;
            dragNode.fy = w.y + dragOffset.y;
            dragNode.x = dragNode.fx;
            dragNode.y = dragNode.fy;
            return;
        }
        if (isPanning) {
            tx = e.clientX - panStart.x;
            ty = e.clientY - panStart.y;
            return;
        }

        // Hover
        const node = hitTest(sx, sy);
        if (node !== hoveredNode) {
            hoveredNode = node;
            canvas.style.cursor = node ? "grab" : "default";
            if (node && tooltip) {
                let html = `<strong>${esc(node.name)}</strong><br><span style="color:${node.color}">${OV_LABELS[node.type] || node.type}</span>`;
                if (node.detail) html += `<br>${esc(node.detail)}`;
                if (node.status && node.status !== "unknown") html += `<br>Status: ${esc(node.status)}`;
                tooltip.innerHTML = html;
                tooltip.style.display = "";
                tooltip.style.left = (e.clientX - rect.left + 14) + "px";
                tooltip.style.top = (e.clientY - rect.top - 10) + "px";
            } else if (tooltip) {
                tooltip.style.display = "none";
            }
        } else if (node && tooltip) {
            tooltip.style.left = (e.clientX - rect.left + 14) + "px";
            tooltip.style.top = (e.clientY - rect.top - 10) + "px";
        }
    });

    canvas.addEventListener("mouseup", e => {
        if (dragNode) {
            // Keep pinned if dragged significantly, else release
            dragNode.fx = null;
            dragNode.fy = null;
            simAlpha = Math.max(simAlpha, 0.15);
            dragNode = null;
        }
        if (isPanning) {
            isPanning = false;
        }
    });

    canvas.addEventListener("click", e => {
        if (dragNode) return;
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        const node = hitTest(sx, sy);
        if (node) {
            if (selectedId === node.id) {
                selectedId = null;
                highlightSet = null;
            } else {
                selectedId = node.id;
                highlightSet = traceConnections(node.id);
            }
        } else {
            selectedId = null;
            highlightSet = null;
        }
    });

    canvas.addEventListener("wheel", e => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale = Math.min(5, Math.max(0.1, scale * delta));
        // Zoom toward mouse position
        tx = sx - (sx - tx) * (newScale / scale);
        ty = sy - (sy - ty) * (newScale / scale);
        scale = newScale;
    }, { passive: false });

    canvas.addEventListener("mouseleave", () => {
        hoveredNode = null;
        canvas.style.cursor = "default";
        if (tooltip) tooltip.style.display = "none";
    });

    // Reset button
    const resetBtn = document.getElementById("ov-reset");
    if (resetBtn) {
        resetBtn.addEventListener("click", () => {
            tx = 0; ty = 0; scale = 1;
            selectedId = null;
            highlightSet = null;
            // Re-randomize positions and restart sim
            nodes.forEach(n => {
                const peers = byType[n.type];
                const idx = peers.indexOf(n);
                const spacing = H / (peers.length + 1);
                n.x = (OV_LAYER_X[n.type] || 0.5) * W + (Math.random() - 0.5) * 60;
                n.y = spacing * (idx + 1) + (Math.random() - 0.5) * 30;
                n.vx = 0; n.vy = 0; n.fx = null; n.fy = null;
            });
            simAlpha = 1.0;
        });
    }

    // Resize handling
    const resizeObs = new ResizeObserver(() => {
        W = container.clientWidth;
        H = container.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.width = W + "px";
        canvas.style.height = H + "px";
    });
    resizeObs.observe(container);

    // Start animation
    animate();

    // Store cleanup ref
    window._ovCleanup = () => {
        if (animId) cancelAnimationFrame(animId);
        resizeObs.disconnect();
    };
}


// ── Lineage Diagram ──

const LINEAGE_COLS = [
    { key: "visuals", label: "Visuals" },
    { key: "tables", label: "Tables" },
    { key: "sources", label: "Sources" },
    { key: "mv_upstream", label: "MV Upstream" },
    { key: "scripts", label: "Scripts" },
    { key: "tasks", label: "Tasks" },
    { key: "upstreams", label: "Upstream" },
];

function _getLineageCols() {
    const defaults = Object.fromEntries(LINEAGE_COLS.map(c => [c.key, true]));
    try { const s = sessionStorage.getItem("lineage_cols"); if (s) return { ...defaults, ...JSON.parse(s) }; } catch (_) {}
    return defaults;
}
function _setLineageCols(state) { sessionStorage.setItem("lineage_cols", JSON.stringify(state)); }

async function renderLineageDiagram() {
    const reports = await api("/api/reports?include_archived=true");
    const colState = _getLineageCols();
    return `
        <div class="page-header">
            <h2>Lineage Diagram</h2>
            <p class="page-subtitle">Trace data flow from visuals to upstream systems</p>
        </div>
        <div class="lineage-controls">
            <select id="lineage-report-select" class="lineage-dropdown">
                <option value="">Select a report...</option>
                ${reports.map(r => `<option value="${r.id}">${esc(r.name)}${r.archived ? " (archived)" : ""}${r.status === "degraded" ? " \u26a0" : ""}</option>`).join("")}
            </select>
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
            window._lineageData = data;
            _renderLineageDiagram(data);
        } catch (e) {
            document.getElementById("lineage-container").innerHTML =
                `<div class="lineage-placeholder" style="color:var(--red)">Error: ${e.message}</div>`;
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

function _renderLineageDiagram(data) {
    const container = document.getElementById("lineage-container");
    const colState = _getLineageCols();
    const enabledCols = LINEAGE_COLS.filter(c => colState[c.key]).map(c => c.key);

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

    // Tables, sources, scripts, tasks, upstreams
    const tableMap = new Map();
    for (const t of data.tables) tableMap.set(t.table_name, { name: t.table_name, source_id: t.source_id });
    const sourceMap = new Map();
    for (const s of data.sources) sourceMap.set(s.id, s);
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
    const sourceNodes = [...sourceMap.values()].filter(s => usedSourceIds.has(s.id));
    // Include MV upstream dependency source IDs so scripts/tasks linked to them also show
    const allSourceIds = new Set(usedSourceIds);
    for (const d of (data.source_deps || [])) allSourceIds.add(d.depends_on_id);
    const usedUpstreamIds = new Set(sourceNodes.map(s => s.upstream_id).filter(Boolean));
    const upstreamNodes = [...upstreamMap.values()].filter(u => usedUpstreamIds.has(u.id));
    const scriptNodes = [...scriptMap.values()].filter(s => (s.source_ids || []).some(sid => allSourceIds.has(sid)));
    const usedScriptIds = new Set(scriptNodes.map(s => s.id));
    const taskNodes = [...taskMap.values()].filter(t => usedScriptIds.has(t.script_id));

    if (visualNodes.length === 0 && tableNodes.length === 0) {
        container.innerHTML = '<div class="lineage-placeholder">No visual lineage data. Run a layout scan from Admin.</div>';
        return;
    }

    // Status helpers
    const stCls = s => ({ current: "lin-st-ok", fresh: "lin-st-ok", stale: "lin-st-warn", outdated: "lin-st-err", error: "lin-st-err" }[s] || "");
    const stDot = s => {
        const c = { current: "var(--green)", fresh: "var(--green)", stale: "var(--red)", outdated: "var(--red)", error: "var(--red)" };
        return `<span class="lin-dot" style="background:${c[s] || "var(--text-dim)"}"></span>`;
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
            typeH += `<div class="lin-subgroup"><div class="lin-subgroup-hdr" data-lin-toggle><span class="lin-chev">&#9654;</span><span>${cat}</span><span class="lin-cnt">${vs.length}</span></div><div class="lin-subgroup-body">${vr}</div></div>`;
        }
        visH += `<div class="lin-card" data-lin-id="page-${pageName.replace(/"/g, "&quot;")}"><div class="lin-card-hdr" data-lin-toggle><span class="lin-chev">&#9654;</span><span class="lin-card-lbl">${esc(pageName)}</span><span class="lin-card-meta">${pCount}</span></div><div class="lin-card-body">${typeH}</div></div>`;
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
        tblH += `<div class="lin-card" data-lin-id="table-${t.name}"><div class="lin-card-hdr"${fields.length ? ' data-lin-toggle' : ''}>${fields.length ? '<span class="lin-chev">&#9654;</span>' : ''}<span class="lin-card-lbl">${esc(t.name)}</span>${fields.length ? `<span class="lin-card-meta">${fields.length} fields</span>` : ""}${noSrc}</div>${fH}</div>`;
    }
    colHtml.tables = tblH;

    // -- Sources (including MV dependencies) --
    // Build dep map: source_id -> list of upstream source objects
    const depMap = new Map();
    const depSourceMap = new Map();
    for (const d of (data.source_deps || [])) {
        if (!depMap.has(d.source_id)) depMap.set(d.source_id, []);
        depMap.get(d.source_id).push(d);
        depSourceMap.set(d.depends_on_id, d);
    }

    // Check which MVs have stale upstream data
    const mvStaleUpstream = new Set();
    for (const s of sourceNodes) {
        const deps = depMap.get(s.id);
        if (!deps) continue;
        for (const d of deps) {
            if (s.last_data_at && d.depends_on_last_data_at && d.depends_on_last_data_at > s.last_data_at) {
                mvStaleUpstream.add(s.id);
            }
        }
    }

    let srcH = "";
    for (const s of sourceNodes) {
        const hasDeps = depMap.has(s.id);
        const isMV = hasDeps ? ' <span class="lin-mv-badge">MV</span>' : '';
        const staleUp = mvStaleUpstream.has(s.id) ? ' <span class="lin-dep-warn" title="Upstream data is newer than last refresh">!</span>' : '';
        const sched = s.refresh_schedule ? `<div class="lin-card-sched" title="Refresh schedule">${esc(s.refresh_schedule)}</div>` : '';
        srcH += `<div class="lin-card lin-src ${stCls(s.status)}" data-lin-id="source-${s.id}" title="${esc(s.name)}"><div class="lin-card-hdr">${stDot(s.status)}<span class="lin-card-lbl">${esc(s.name)}</span>${isMV}${staleUp}</div>${sched}</div>`;
    }
    colHtml.sources = srcH;

    // -- MV Upstream (tables that feed materialized views, not directly used by report) --
    const existingSourceIds = new Set(sourceNodes.map(s => s.id));
    let mvUpH = "";
    const mvUpSources = [];
    for (const [depId, d] of depSourceMap) {
        if (!existingSourceIds.has(depId)) {
            // This upstream table only feeds the MV, not directly used by the report
            mvUpSources.push(d);
            mvUpH += `<div class="lin-card lin-src lin-src-upstream ${stCls(d.depends_on_status)}" data-lin-id="source-${d.depends_on_id}" title="${esc(d.depends_on_name)}"><div class="lin-card-hdr">${stDot(d.depends_on_status)}<span class="lin-card-lbl">${esc(d.depends_on_name)}</span></div></div>`;
        }
    }
    colHtml.mv_upstream = mvUpH;

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

    const colCounts = { visuals: visCount, tables: tableNodes.length, sources: sourceNodes.length, mv_upstream: mvUpSources.length, scripts: scriptNodes.length, tasks: taskNodes.length, upstreams: upstreamNodes.length };

    // Build grid
    const activeCols = enabledCols.filter(k => colHtml[k] || k === "visuals" || k === "tables");
    const gridCols = activeCols.map(() => "minmax(150px, 1fr)").join(" ");
    let gridH = "";
    for (const key of activeCols) {
        const col = LINEAGE_COLS.find(c => c.key === key);
        const empty = !colHtml[key];
        gridH += `<div class="lin-col" data-lin-col="${key}"><div class="lin-col-hdr">${col.label} <span class="lin-col-cnt">${colCounts[key] || 0}</span></div>${empty ? '<div class="lin-empty">None linked</div>' : colHtml[key]}</div>`;
    }

    const stLabel = data.report.status || "unknown";
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
        <div class="lin-hint-bar">Click any node to trace its lineage. Click empty space to reset.</div>
    `;

    _buildLinGraph(data, visualNodes, fieldsByTable, tableNodes, sourceNodes, scriptNodes, taskNodes, upstreamNodes);
    setTimeout(() => { _drawLinEdges(); _bindLinInteractions(); }, 60);
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
    // Source -> Upstream system (SVG)
    for (const s of sourceNodes) if (s.upstream_id) add(`source-${s.id}`, `upstream-${s.upstream_id}`, true);

    window._linFwd = fwd;
    window._linBwd = bwd;
    window._linSvgEdges = svgEdges;
}

function _drawLinEdges() {
    const svg = document.getElementById("lin-svg");
    const wrap = document.getElementById("lin-wrap");
    if (!svg || !wrap) return;
    const wr = wrap.getBoundingClientRect();
    svg.innerHTML = "";
    svg.setAttribute("width", wrap.scrollWidth);
    svg.setAttribute("height", wrap.scrollHeight);
    for (const e of (window._linSvgEdges || [])) {
        const fromEl = wrap.querySelector(`[data-lin-id="${CSS.escape(e.from)}"]`);
        const toEl = wrap.querySelector(`[data-lin-id="${CSS.escape(e.to)}"]`);
        if (!fromEl || !toEl) continue;
        const fc = fromEl.closest(".lin-col"), tc = toEl.closest(".lin-col");
        if (!fc || !tc || fc.offsetParent === null || tc.offsetParent === null) continue;
        const fr = fromEl.getBoundingClientRect(), tr = toEl.getBoundingClientRect();
        if (fr.width === 0 || tr.width === 0) continue;
        const x1 = fr.right - wr.left + wrap.scrollLeft;
        const y1 = fr.top + fr.height / 2 - wr.top + wrap.scrollTop;
        const x2 = tr.left - wr.left + wrap.scrollLeft;
        const y2 = tr.top + tr.height / 2 - wr.top + wrap.scrollTop;
        const mx = (x1 + x2) / 2;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
        path.setAttribute("class", "lin-edge");
        path.dataset.from = e.from; path.dataset.to = e.to;
        svg.appendChild(path);
    }
}

function _bindLinInteractions() {
    const wrap = document.getElementById("lin-wrap");
    if (!wrap) return;
    // Expand/collapse
    wrap.querySelectorAll("[data-lin-toggle]").forEach(toggle => {
        toggle.addEventListener("click", (e) => {
            e.stopPropagation();
            const card = toggle.closest(".lin-subgroup") || toggle.closest(".lin-card");
            if (!card) return;
            card.classList.toggle("expanded");
            setTimeout(_drawLinEdges, 30);
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
            if (node.classList.contains("lin-highlighted")) { _resetLinHL(); return; }
            const connected = _traceLinLineage(id);
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
                // Apply edge styles after redraw
                wrap.querySelectorAll(".lin-edge").forEach(edge => {
                    const hit = connected.has(edge.dataset.from) && connected.has(edge.dataset.to);
                    edge.classList.toggle("lin-edge-hl", hit);
                    edge.classList.toggle("lin-edge-dim", !hit);
                });
            }, 380);
        });
    });
    wrap.addEventListener("click", (e) => { if (!e.target.closest("[data-lin-id]")) _resetLinHL(); });
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
    wrap.querySelectorAll("[data-lin-id]").forEach(n => n.classList.remove("lin-highlighted", "lin-dimmed"));
    wrap.querySelectorAll(".lin-col").forEach(col => col.classList.remove("lin-col-empty"));
    wrap.querySelectorAll(".lin-edge").forEach(e => e.classList.remove("lin-edge-hl", "lin-edge-dim"));
    // Redraw edges after expand transition finishes
    setTimeout(() => {
        _drawLinEdges();
        if (svg) { svg.style.transition = "opacity 0.2s ease"; svg.style.opacity = "1"; }
    }, 380);
}


// ── Tasks / Kanban ──

const TASK_STATUSES = [
    { key: "backlog", label: "Backlog" },
    { key: "todo", label: "To Do" },
    { key: "in_progress", label: "In Progress" },
];
const TASK_ALL_STATUSES = [...TASK_STATUSES, { key: "done", label: "Done" }];

const ENTITY_TYPE_LABELS = {
    report: "Report",
    source: "Data Source",
    script: "Script",
    upstream_system: "Upstream System",
    scheduled_task: "Scheduled Task",
};

function _taskCard(task) {
    const today = new Date().toISOString().slice(0, 10);
    const overdue = task.due_date && task.due_date < today && task.status !== "done";
    const dueFmt = task.due_date ? new Date(task.due_date + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : "";
    const linkBadges = (task.linked_entities || []).map(le => {
        const typeShort = { report: "RPT", source: "SRC", script: "SCR", upstream_system: "UPS", scheduled_task: "SCH" }[le.entity_type] || le.entity_type;
        return `<span class="task-link-chip" title="${esc(ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type)}: ${esc(le.entity_name || '')}">${typeShort}: ${esc(le.entity_name || "ID " + le.entity_id)}</span>`;
    }).join("");
    return `<div class="kanban-card priority-${task.priority}" draggable="true" data-task-id="${task.id}" tabindex="0" role="listitem" aria-label="Task: ${esc(task.title)}, Priority: ${task.priority}${task.assigned_to ? ', Assigned to: ' + esc(task.assigned_to) : ''}">
        <div class="kanban-card-title">${esc(task.title)}</div>
        ${linkBadges ? `<div class="kanban-card-links">${linkBadges}</div>` : ""}
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

    const activeTasks = tasks.filter(t => t.status !== "done");
    const archivedTasks = tasks.filter(t => t.status === "done");

    const ownerOptions = owners.map(o => `<option value="${o}">${o}</option>`).join("");
    const boardHtml = _buildKanbanBoard(activeTasks);

    return `
        <div class="page-header">
            <h1>Tasks</h1>
            <button class="btn-new-task" id="btn-new-task">+ New Task</button>
            <button class="btn-outline" id="btn-export-tasks" style="font-size:0.78rem">Export</button>
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
        <div class="tasks-archive-section">
            <button class="btn-outline tasks-archive-toggle" id="btn-archive-toggle" style="font-size:0.78rem">
                ${archivedTasks.length > 0 ? `Show Archive (${archivedTasks.length})` : 'Archive (empty)'}
            </button>
            <div id="tasks-archive-list" style="display:none">
                ${_buildArchiveList(archivedTasks)}
            </div>
        </div>
    `;
}

function _buildArchiveList(tasks) {
    if (tasks.length === 0) return '<div class="kanban-empty" style="padding:0.75rem">No archived tasks</div>';
    return `<div class="tasks-archive-cards">
        ${tasks.map(t => {
            const links = (t.linked_entities || []).map(le =>
                `<span class="task-link-chip">${esc(ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type)}: ${esc(le.entity_name || "ID " + le.entity_id)}</span>`
            ).join("");
            const updated = t.updated_at ? timeAgo(t.updated_at) : "";
            return `<div class="archive-task-card" data-task-id="${t.id}" tabindex="0">
                <div class="archive-task-title">${esc(t.title)}</div>
                <div class="archive-task-meta">
                    ${t.assigned_to ? `<span class="assignee-chip" style="font-size:0.68rem">${esc(t.assigned_to)}</span>` : ''}
                    ${updated ? `<span style="color:var(--text-dim);font-size:0.68rem">completed ${updated}</span>` : ''}
                    ${links ? `<span class="kanban-card-links" style="display:inline-flex;margin-left:0.25rem">${links}</span>` : ''}
                </div>
            </div>`;
        }).join("")}
    </div>`;
}

function _exportTasksEmail() {
    const tasks = window._tasksData || [];
    const activeTasks = tasks.filter(t => t.status !== "done");
    const today = new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });

    // Group by status
    const grouped = {};
    TASK_STATUSES.forEach(s => grouped[s.key] = []);
    activeTasks.forEach(t => {
        if (grouped[t.status]) grouped[t.status].push(t);
        else grouped.backlog.push(t);
    });

    // Build HTML table
    let html = `<div style="font-family:Segoe UI,Arial,sans-serif;max-width:800px">`;
    html += `<h2 style="margin:0 0 4px;font-size:16px">Task Board Summary</h2>`;
    html += `<p style="margin:0 0 16px;color:#666;font-size:13px">${today} - ${activeTasks.length} active task${activeTasks.length !== 1 ? 's' : ''}</p>`;

    for (const s of TASK_STATUSES) {
        const list = grouped[s.key];
        if (list.length === 0) continue;
        html += `<h3 style="margin:16px 0 6px;font-size:14px;color:#555;border-bottom:1px solid #ddd;padding-bottom:4px">${s.label} (${list.length})</h3>`;
        html += `<table style="width:100%;border-collapse:collapse;font-size:13px">`;
        html += `<tr style="background:#f5f5f5;text-align:left">
            <th style="padding:5px 8px;border:1px solid #ddd">Task</th>
            <th style="padding:5px 8px;border:1px solid #ddd;width:70px">Priority</th>
            <th style="padding:5px 8px;border:1px solid #ddd;width:100px">Assigned To</th>
            <th style="padding:5px 8px;border:1px solid #ddd;width:80px">Due Date</th>
            <th style="padding:5px 8px;border:1px solid #ddd">Linked To</th>
        </tr>`;
        for (const t of list) {
            const prioColor = t.priority === "high" ? "#e74c3c" : t.priority === "low" ? "#999" : "#333";
            const dueFmt = t.due_date ? new Date(t.due_date + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : "-";
            const isOverdue = t.due_date && t.due_date < new Date().toISOString().slice(0, 10);
            const dueStyle = isOverdue ? 'color:#e74c3c;font-weight:600' : '';
            const links = (t.linked_entities || []).map(le => {
                const label = ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type;
                return `${label}: ${le.entity_name || "ID " + le.entity_id}`;
            }).join(", ");
            html += `<tr>
                <td style="padding:5px 8px;border:1px solid #ddd"><strong>${esc(t.title)}</strong>${t.description ? `<br><span style="color:#888;font-size:12px">${esc(t.description)}</span>` : ''}</td>
                <td style="padding:5px 8px;border:1px solid #ddd;color:${prioColor}">${t.priority}</td>
                <td style="padding:5px 8px;border:1px solid #ddd">${esc(t.assigned_to) || '-'}</td>
                <td style="padding:5px 8px;border:1px solid #ddd;${dueStyle}">${dueFmt}</td>
                <td style="padding:5px 8px;border:1px solid #ddd;font-size:12px;color:#666">${links || '-'}</td>
            </tr>`;
        }
        html += `</table>`;
    }
    html += `</div>`;

    // Copy to clipboard
    const blob = new Blob([html], { type: "text/html" });
    const plainText = _tasksToPlainText(grouped);
    const item = new ClipboardItem({
        "text/html": blob,
        "text/plain": new Blob([plainText], { type: "text/plain" }),
    });
    navigator.clipboard.write([item]).then(() => {
        toast("Task summary copied to clipboard - paste into email");
    }).catch(() => {
        // Fallback: open in new window
        const w = window.open("", "_blank");
        if (w) { w.document.write(html); w.document.close(); }
    });
}

function _tasksToPlainText(grouped) {
    let text = "TASK BOARD SUMMARY\n";
    text += new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) + "\n\n";
    for (const s of TASK_STATUSES) {
        const list = grouped[s.key];
        if (list.length === 0) continue;
        text += `--- ${s.label.toUpperCase()} (${list.length}) ---\n`;
        for (const t of list) {
            const links = (t.linked_entities || []).map(le => {
                const label = ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type;
                return `${label}: ${le.entity_name || "ID " + le.entity_id}`;
            }).join(", ");
            text += `  [${t.priority.toUpperCase()}] ${t.title}`;
            if (t.assigned_to) text += ` (${t.assigned_to})`;
            if (t.due_date) text += ` due: ${t.due_date}`;
            if (links) text += ` | ${links}`;
            text += "\n";
            if (t.description) text += `    ${t.description}\n`;
        }
        text += "\n";
    }
    return text;
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
        if (t.status === "done") return; // archived, skip
        if (grouped[t.status]) grouped[t.status].push(t);
        else grouped.backlog.push(t);
    });
    const prioOrder = { high: 0, medium: 1, low: 2 };
    Object.values(grouped).forEach(arr => arr.sort((a, b) => {
        const pa = prioOrder[a.priority] ?? 1;
        const pb = prioOrder[b.priority] ?? 1;
        if (pa !== pb) return pa - pb;
        return a.position - b.position;
    }));

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
    const t = task || { title: "", description: "", status: "backlog", priority: "medium", assigned_to: "", due_date: "", email_owner: false, linked_entities: [] };
    const ownerOptions = owners.map(o =>
        `<option value="${o}" ${t.assigned_to === o ? "selected" : ""}>${o}</option>`
    ).join("");
    const statusOptions = TASK_ALL_STATUSES.map(s =>
        `<option value="${s.key}" ${t.status === s.key ? "selected" : ""}>${s.label}${s.key === "done" ? " (archive)" : ""}</option>`
    ).join("");

    const existingLinks = (t.linked_entities || []).map(le =>
        `<div class="task-link-row" data-entity-type="${esc(le.entity_type)}" data-entity-id="${le.entity_id}">
            <span class="task-link-badge">${esc(ENTITY_TYPE_LABELS[le.entity_type] || le.entity_type)}</span>
            <span class="task-link-name">${esc(le.entity_name || "ID " + le.entity_id)}</span>
            <button type="button" class="task-link-remove" title="Remove">&times;</button>
        </div>`
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
            <label>Linked Entities</label>
            <div id="task-links-list" class="task-links-list">${existingLinks}</div>
            <div class="task-link-add-row">
                <select id="task-link-type">
                    <option value="">Select type...</option>
                    ${Object.entries(ENTITY_TYPE_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
                </select>
                <select id="task-link-entity" disabled>
                    <option value="">Select entity...</option>
                </select>
                <button type="button" class="btn-outline" id="task-link-add-btn" disabled>Add</button>
            </div>
            <div class="task-modal-actions">
                ${isEdit ? `<button class="btn-danger" id="task-delete-btn">Delete</button>` : ""}
                <button id="task-cancel-btn">Cancel</button>
                <button class="btn-primary" id="task-save-btn">${isEdit ? "Save" : "Create"}</button>
            </div>
        </div>
    </div>`;
}

async function _openTaskModal(task) {
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

    // --- Entity linking ---
    let linkableEntities = {};
    try { linkableEntities = await api("/api/tasks/linkable-entities"); } catch (_) {}

    const linkTypeSelect = document.getElementById("task-link-type");
    const linkEntitySelect = document.getElementById("task-link-entity");
    const linkAddBtn = document.getElementById("task-link-add-btn");
    const linksList = document.getElementById("task-links-list");

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
        // Check for duplicates
        const existingLink = linksList.querySelector(`[data-entity-type="${etype}"][data-entity-id="${eid}"]`);
        if (existingLink) { toast("Already linked"); return; }
        const ename = linkEntitySelect.options[linkEntitySelect.selectedIndex].text;
        linksList.insertAdjacentHTML("beforeend",
            `<div class="task-link-row" data-entity-type="${esc(etype)}" data-entity-id="${eid}">
                <span class="task-link-badge">${esc(ENTITY_TYPE_LABELS[etype] || etype)}</span>
                <span class="task-link-name">${esc(ename)}</span>
                <button type="button" class="task-link-remove" title="Remove">&times;</button>
            </div>`);
        // Reset selectors
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

    // --- Save ---
    saveBtn.addEventListener("click", async () => {
        const title = titleInput.value.trim();
        if (!title) { titleInput.classList.add("input-error"); titleInput.focus(); return; }

        saveBtn.disabled = true;

        // Collect linked entities from the DOM
        const linked_entities = [];
        linksList.querySelectorAll(".task-link-row").forEach(row => {
            linked_entities.push({
                entity_type: row.dataset.entityType,
                entity_id: parseInt(row.dataset.entityId),
            });
        });

        const body = {
            title,
            description: document.getElementById("task-desc").value.trim() || null,
            status: document.getElementById("task-status").value,
            priority: document.getElementById("task-priority").value,
            assigned_to: document.getElementById("task-assign").value || null,
            due_date: document.getElementById("task-due").value || null,
            email_owner: document.getElementById("task-email-owner").checked,
            linked_entities,
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
        const activeTasks = tasks.filter(t => t.status !== "done");
        const archivedTasks = tasks.filter(t => t.status === "done");
        const container = document.getElementById("kanban-board-container");
        if (container) {
            container.innerHTML = _buildKanbanBoard(activeTasks, filterOwner || null);
        }
        // Update archive section
        const archiveList = document.getElementById("tasks-archive-list");
        if (archiveList) archiveList.innerHTML = _buildArchiveList(archivedTasks);
        const archiveBtn = document.getElementById("btn-archive-toggle");
        if (archiveBtn) {
            const isOpen = archiveList && archiveList.style.display !== "none";
            archiveBtn.textContent = archivedTasks.length > 0
                ? `${isOpen ? "Hide" : "Show"} Archive (${archivedTasks.length})`
                : "Archive (empty)";
        }
    } catch (err) {
        toast("Failed to refresh tasks: " + err.message);
    }
}

function bindTasksPage() {
    // New task button
    const newBtn = document.getElementById("btn-new-task");
    if (newBtn) newBtn.addEventListener("click", () => _openTaskModal(null));

    // Export button
    const exportBtn = document.getElementById("btn-export-tasks");
    if (exportBtn) exportBtn.addEventListener("click", _exportTasksEmail);

    // Archive toggle
    const archiveBtn = document.getElementById("btn-archive-toggle");
    const archiveList = document.getElementById("tasks-archive-list");
    if (archiveBtn && archiveList) {
        archiveBtn.addEventListener("click", () => {
            const isHidden = archiveList.style.display === "none";
            archiveList.style.display = isHidden ? "" : "none";
            const archivedTasks = (window._tasksData || []).filter(t => t.status === "done");
            archiveBtn.textContent = archivedTasks.length > 0
                ? `${isHidden ? "Hide" : "Show"} Archive (${archivedTasks.length})`
                : "Archive (empty)";
        });
    }

    // Archive card clicks
    if (archiveList) {
        archiveList.addEventListener("click", (e) => {
            const card = e.target.closest(".archive-task-card");
            if (!card) return;
            const taskId = parseInt(card.dataset.taskId);
            const task = (window._tasksData || []).find(t => t.id === taskId);
            if (task) _openTaskModal(task);
        });
    }

    // Owner filter
    const filter = document.getElementById("task-owner-filter");
    if (filter) {
        filter.addEventListener("change", () => {
            const tasks = (window._tasksData || []).filter(t => t.status !== "done");
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
        { key: "created_at", label: "When", width: COL_W.md, render: e => `<span title="${esc(e.created_at || "")}">${timeAgo(e.created_at)}</span>` },
        { key: "actor", label: "User", width: COL_W.sm, render: e => e.actor ? `<span style="color:var(--accent)">${esc(e.actor)}</span>` : '<span style="color:var(--text-dim)">system</span>' },
        { key: "entity_type", label: "Type", width: COL_W.sm, render: e => `<span class="eventlog-type-badge type-${esc(e.entity_type)}">${esc(e.entity_type)}</span>` },
        { key: "entity_name", label: "Entity", width: COL_W.lg, render: e => esc(e.entity_name) || `#${e.entity_id || "—"}` },
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
        a: "MX Analytics automatically discovers Power BI reports and their data sources, monitors data freshness, flags issues, and gives the BI team a single place to manage data quality and accountability."
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
        a: "The Scripts page discovers and tracks all Python ETL scripts on the BI desktop and shared drives. It detects which SQL tables each script writes to and reads from, links scripts to data sources in the lineage, and shows modification dates. Scripts are categorized as Data to SQL, Data to Excel, or Other. Use Full Scan to discover new scripts or Re-parse to re-analyze existing ones."
    },
    {
        q: "What is the Scheduled Tasks page?",
        a: "Under Data, Scheduled Tasks shows all Windows Task Scheduler entries across scanned machines. Tasks can be linked to scripts, giving you end-to-end visibility from schedule to data refresh. Failed tasks and their schedules (daily, weekly, monthly) are highlighted."
    },
    {
        q: "What is the Power Automate page?",
        a: "Under Data, Power Automate lets you manually register Power Automate flows that feed data into your pipeline. Flows can be linked to output sources, and their last run time is derived from the linked source's probe data."
    },
    {
        q: "What are Custom Reports?",
        a: "Under Management, Custom Reports lets you document recurring tasks - things like business trip reports, monthly reconciliations, or ad-hoc data requests. Each entry tracks the owner, stakeholders, frequency, estimated hours, data sources, output, and step-by-step documentation."
    },
    {
        q: "How does the Kanban task board work?",
        a: "Under Management, create tasks with titles, descriptions, priorities, due dates, and assignees. Drag cards between Backlog, To Do, In Progress, and Done columns. Tasks can be linked to specific reports, sources, or scripts for traceability."
    },
    {
        q: "What is the Lineage view?",
        a: "Lineage shows the full dependency chain as a horizontal DAG: Visuals, Tables, Sources, MV Upstream, Scripts, and Tasks. Select a report to see exactly which sources feed into which visuals, which materialized views sit upstream, and which scripts refresh the data."
    },
    {
        q: "What is the Pipeline Overview?",
        a: "Under Tools, the Pipeline Overview shows an interactive force-directed graph of the entire data pipeline, with nodes for reports, sources, scripts, and tasks connected by their relationships."
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


// ── Router ──

const pages = {
    dashboard: renderDashboard,
    overview: renderOverview,
    sources: renderSources,
    reports: renderReports,
    scripts: renderScripts,
    scheduledtasks: renderScheduledTasks,
    powerautomate: renderPowerAutomate,
    customreports: renderCustomReports,
    lineage: renderLineageDiagram,
    scanner: renderScanner,
    changelog: renderChangelog,
    create: renderCreate,
    bestpractices: renderBestPractices,
    export: renderExport,
    tasks: renderTasks,
    eventlog: renderEventLog,
    faq: renderFaq,
};

// Map old hash routes to new pages for backwards compat
const pageAliases = { alerts: "dashboard", issues: "dashboard", actions: "dashboard" };

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
    if (window._ovCleanup) { window._ovCleanup(); window._ovCleanup = null; }

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
        if (page === "customreports") bindCustomReportsPage();
        if (page === "create") bindCreatePage();
        if (page === "changelog") bindChangelogPage();
        if (page === "bestpractices") bindBestPracticesPage();
        if (page === "export") bindExportPage();
        if (page === "faq") bindFaqPage();
        if (page === "eventlog") bindEventLogPage();
        if (page === "tasks") bindTasksPage();
        if (page === "lineage") bindLineageDiagramPage();
        if (page === "overview") bindOverviewPage();
    } catch (err) {
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

function _isLocal() {
    return window._currentUser && window._currentUser.is_local;
}

function _showConnectedUser(user) {
    const el = document.getElementById("connected-user");
    if (!el) return;
    el.innerHTML = `<span class="user-name">${esc(user.name)}</span>${user.is_local ? '<span class="user-local">(local)</span>' : ''}`;
}

function _showRegistrationModal(ip) {
    const overlay = document.createElement("div");
    overlay.className = "register-overlay";
    overlay.innerHTML = `
        <div class="register-modal">
            <h2>Welcome to MX Analytics</h2>
            <p>Enter your name to get started. This will be linked to your IP address (${esc(ip)}) so the system knows who you are.</p>
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
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
        })
        .then(r => r.json())
        .then(me => {
            window._currentUser = me;
            _showConnectedUser(me);
            overlay.remove();
        })
        .catch(() => {
            btn.disabled = false;
            btn.textContent = "Continue";
            toast("Registration failed - try again");
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

    // ── Identity check: fetch /api/me, show registration if needed ──
    window._currentUser = null;
    fetch("/api/me").then(r => r.json()).then(me => {
        if (me.name) {
            window._currentUser = me;
            _showConnectedUser(me);
        } else {
            _showRegistrationModal(me.ip);
        }
        // Show Update App button for local users only
        if (me.is_local) {
            const updateBtn = document.getElementById("btn-update-app");
            if (updateBtn) updateBtn.style.display = "";
        }
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
        const saved = localStorage.getItem("mx-theme");
        if (saved === "dark") document.documentElement.classList.add("dark");
        else if (saved === "light") document.documentElement.classList.add("light");
        updateThemeIcon();

        themeToggle.addEventListener("click", () => {
            const html = document.documentElement;
            if (html.classList.contains("dark")) {
                html.classList.remove("dark");
                html.classList.add("light");
                localStorage.setItem("mx-theme", "light");
            } else if (html.classList.contains("light")) {
                html.classList.remove("light");
                html.classList.add("dark");
                localStorage.setItem("mx-theme", "dark");
            } else {
                // Auto mode - toggle to opposite of system preference
                const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                if (prefersDark) {
                    html.classList.add("light");
                    localStorage.setItem("mx-theme", "light");
                } else {
                    html.classList.add("dark");
                    localStorage.setItem("mx-theme", "dark");
                }
            }
            updateThemeIcon();
        });
    }
});
