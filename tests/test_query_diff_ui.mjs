import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");

const diffStart = source.indexOf("// ── Query history & diff ──");
const diffEnd = source.indexOf("\nfunction typeBadge", diffStart);
assert.notEqual(diffStart, -1, "Query history & diff helpers must exist");
assert.notEqual(diffEnd, -1, "Query history helpers must end before typeBadge");

const alertsStart = source.indexOf("function renderDashboardAlertsTable");
const alertsEnd = source.indexOf("\nfunction bindDashboardAlerts", alertsStart);
assert.notEqual(alertsStart, -1, "Dashboard alerts renderer must exist");

const context = {
    esc: v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
    fmtInt: v => String(v ?? 0),
    formatDate: v => v ? `date(${v})` : "-",
    formatDateOnly: v => v ? String(v).slice(0, 10) : "-",
    actionTypeBadge: () => "<span>badge</span>",
    alertAssetLogo: () => "<span>logo</span>",
    shortNameFromPath: v => v,
    toast: () => {},
    document: undefined,
};
vm.createContext(context);
vm.runInContext(
    source.slice(diffStart, diffEnd) + "\n" + source.slice(alertsStart, alertsEnd) + `
    this.queryChangesListHtml = queryChangesListHtml;
    this.queryHistoryGroupsHtml = queryHistoryGroupsHtml;
    this._queryDiffRowsHtml = _queryDiffRowsHtml;
    this.renderDashboardAlertsTable = renderDashboardAlertsTable;
    `,
    context,
);

// ── Grouped change list with View diff actions ──
const changes = [
    { version_id: 7, prev_version_id: 3, artifact_kind: "report_table", artifact_name: "Sales Orders", language: "m", change_kind: "changed", detected_at: "2026-08-21T10:00:00" },
    { version_id: 8, prev_version_id: null, artifact_kind: "report_table", artifact_name: "New Table", language: "m", change_kind: "added", detected_at: "2026-08-21T10:00:00" },
];
const listHtml = context.queryChangesListHtml(changes);
assert.match(listHtml, /Sales Orders/, "Changed table names must be listed");
assert.match(listHtml, /query-view-diff/, "Each change must expose a View diff action");
assert.match(listHtml, /data-to-id="7"[^>]*data-from-id="3"/, "Diff buttons must carry version ids");
assert.doesNotMatch(listHtml.split("New Table")[1].split("</div>")[0] + listHtml.match(/data-to-id="8"[^>]*/)[0], /data-from-id/, "Added changes have no Before version");

// ── History groups: versions, compare selectors, empty state ──
const groups = [{
    table_name: "Sales Orders",
    versions: [
        { id: 1, prev_version_id: null, change_kind: "baseline", detected_at: "2026-08-01", has_text: true },
        { id: 7, prev_version_id: 1, change_kind: "changed", detected_at: "2026-08-21", has_text: true },
    ],
}];
const groupsHtml = context.queryHistoryGroupsHtml(groups, "qh-test");
assert.match(groupsHtml, /Sales Orders/, "Group header must name the table");
assert.match(groupsHtml, /2 versions/, "Version count must be shown");
assert.match(groupsHtml, /qh-compare-from/, "Two-version comparison selector must exist");
assert.match(groupsHtml, /qh-compare-to/, "Two-version comparison selector must exist");
assert.match(groupsHtml, /qh-compare-btn/, "Compare action must exist");
const optionCount = (groupsHtml.match(/<option/g) || []).length;
assert.equal(optionCount, 4, "Both selectors must list every version");

const emptyHtml = context.queryHistoryGroupsHtml([], "qh-test");
assert.match(emptyHtml, /No recorded query history yet/, "Empty history must show an explanatory state");

// ── Diff rendering: line numbers, red removals, green additions, context ──
const rows = [
    { kind: "context", left_line: 1, right_line: 1, left_text: "let", right_text: "let" },
    { kind: "changed", left_line: 2, right_line: 2, left_text: 'Item="a"', right_text: 'Item="b"' },
    { kind: "removed", left_line: 3, right_line: null, left_text: "old line", right_text: null },
    { kind: "added", left_line: null, right_line: 3, left_text: null, right_text: "new line" },
];
const diffHtml = context._queryDiffRowsHtml(rows);
assert.match(diffHtml, /qdiff-removed/, "Removed lines must be marked");
assert.match(diffHtml, /qdiff-added/, "Added lines must be marked");
assert.match(diffHtml, /qdiff-blank/, "One-sided rows must show a blank counterpart");
assert.match(diffHtml, /class="qdiff-ln">1</, "Line numbers must render");
assert.match(diffHtml, /Item=&quot;a&quot;/, "Query text must be escaped");

const removedStyle = style.match(/\.qdiff-text\.qdiff-removed\s*\{([^}]*)\}/)?.[1] || "";
const addedStyle = style.match(/\.qdiff-text\.qdiff-added\s*\{([^}]*)\}/)?.[1] || "";
assert.match(removedStyle, /--red/, "Removals must use the red palette");
assert.match(addedStyle, /--green/, "Additions must use the green palette");
assert.match(style, /\.query-diff-modal\s*\{[^}]*94vw/, "Diff modal must stay responsive");

// ── Dashboard alert: report is the clickable primary artifact for M changes ──
const mAction = {
    id: 42, status: "open", type: "changed_query", asset_type: "report", asset_id: 9,
    asset_name: "Weekly_Sales", report_names: [], detail_items: [],
    query_changes: changes, recommendation: "Review the query change",
    created_at: "2026-08-21", assigned_to: "",
};
const tableHtml = context.renderDashboardAlertsTable([mAction], [], "all");
assert.match(tableHtml, /alerts-go-report" data-report-id="9"/, "M query alerts must link to the exact report");
assert.match(tableHtml, /Changed report tables:/, "Expanded alert must list changed report tables");
assert.match(tableHtml, /alerts-expand-btn/, "Query change alerts must be expandable");
assert.match(tableHtml, /query-view-diff/, "Expanded alert must offer View diff per change");
assert.match(tableHtml, /changed tables: Sales Orders, New Table/, "Alert summary must name the changed tables");

// ── Dashboard alert: MV source is the primary artifact for SQL changes ──
const mvAction = {
    id: 43, status: "open", type: "changed_query", asset_type: "source", asset_id: 10,
    asset_name: "reporting.sales_mv", source_type: "postgresql", report_names: [], detail_items: [],
    query_changes: [{ version_id: 11, prev_version_id: 5, artifact_kind: "mv", artifact_name: "reporting.sales_mv", language: "sql", change_kind: "changed", detected_at: "2026-08-21" }],
    created_at: "2026-08-21", assigned_to: "",
};
const mvHtml = context.renderDashboardAlertsTable([mvAction], [], "all");
assert.match(mvHtml, /alerts-source-link" data-source-id="10"/, "MV SQL alerts must link to the exact source");
assert.match(mvHtml, /Changed definitions:/, "Expanded MV alert must list the changed definition");

console.log("query diff ui tests passed");
