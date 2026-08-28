import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("const LINEAGE_FLOW_DIAGNOSTIC_DISMISSALS_KEY");
const end = source.indexOf("\nasync function renderLineageDiagram", start);

assert.notEqual(start, -1, "Flow diagnostic presentation helpers must exist");
assert.notEqual(end, -1, "Flow diagnostic helpers must precede the Pipelines page renderer");

const stored = new Map();
const context = {
    sessionStorage: {
        getItem: key => stored.get(key) ?? null,
        setItem: (key, value) => stored.set(key, value),
    },
};
vm.createContext(context);
vm.runInContext(`
    const LINEAGE_COLS = [];
    const LINEAGE_COL_STORAGE_KEY = "unused";
    function esc(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }
    ${source.slice(start, end)}
    this.model = _flowDiagnosticsModel;
    this.html = _flowDiagnosticsHtml;
    this.hiddenHtml = _flowsHiddenWarningHtml;
    this.payloadWarnings = _lineageFlowPayloadWarnings;
    this.dismiss = _dismissFlowDiagnostic;
`, context);

const legacy = {
    id: 12,
    name: "Legacy suffix collision",
    target: { server: "warehouse-host", database: "warehouse", schema: "sales", table: "orders" },
    persisted_source_id: null,
    effective_source_id: null,
    candidate_source_ids: [44],
    link_status: "unresolved",
    scope_status: "candidate_in_report",
    severity: "warning",
    reason_code: "legacy_display_match",
    message: "A display name happens to end with sales.orders.",
    recommended_action: "edit_flow",
    executable: false,
};
const data = {
    report: { id: 77, name: "Orders report" },
    flows: [{ id: 2, name: "Confirmed orders", target_source_ids: [44] }],
    legacy_flow_suggestions: [{
        id: 12,
        name: "Legacy suffix collision",
        sql_database: "warehouse",
        sql_schema: "sales",
        sql_table: "orders",
        target_source_ids: [44],
        reason: "Legacy fallback",
    }],
    flow_diagnostics: {
        included_count: 1,
        excluded_count: 3,
        download_only_count: 2,
        items: [
            {
                id: 11,
                name: "Ambiguous orders",
                target: { database: "warehouse", schema: "sales", table: "orders" },
                severity: "blocker",
                reason_code: "ambiguous_target",
                message: "Multiple exact source identities match this target.",
                recommended_action: "edit_flow",
            },
            legacy,
        ],
        postgres_dependencies: {
            status: "completed_with_warnings",
            scan_run_id: 91,
            databases: {
                warehouse: { status: "completed" },
                staging: { status: "failed" },
            },
        },
    },
};

const html = context.html(data);
assert.match(html, /Flow lineage/);
assert.match(html, /2 issues for this report/);
assert.doesNotMatch(html, /connected|excluded|download-only/,
    "Pipeline diagnostics must not present a global all-Flow match catalogue");
assert.match(html, /Ambiguous orders/);
assert.match(html, /Multiple exact source identities match this target/);
assert.match(html, /Legacy suffix collision/);
assert.match(html, /PostgreSQL dependency scan: completed with warnings \(staging\)/);
assert.match(html, /data-flow-diagnostic-edit="11"[^>]*>Edit Flow</);
assert.match(html, />Dismiss</);
assert.match(html, />Recheck lineage</);
assert.match(html, />Open Scanner</);
assert.equal((html.match(/Legacy suffix collision/g) || []).length, 1,
    "The compatibility legacy list must not duplicate an item already in flow_diagnostics");
assert.match(html, /pipeline-blocked/, "An exact in-scope ambiguity must receive blocker presentation");

const aliasGapHtml = context.html({
    report: { id: 77, name: "Orders report" },
    flows: [],
    flow_diagnostics: {
        items: [{
            diagnostic_kind: "lineage_gap",
            id: null,
            name: "inflow_outflow_mv",
            target: { database: "warehouse", schema: "reporting", table: "inflow_outflow_mv" },
            severity: "blocker",
            reason_code: "server_alias_lineage_gap",
            message: "Power BI uses db-alias but catalog dependencies use 10.20.30.40. Metronome did not merge them automatically.",
        }],
    },
});
assert.match(aliasGapHtml, /inflow_outflow_mv/);
assert.match(aliasGapHtml, /did not merge them automatically/,
    "A report-specific host split must be visible instead of silently disconnecting lineage");
assert.doesNotMatch(aliasGapHtml, /data-flow-diagnostic-edit/,
    "A server identity gap is not a Flow edit and must not offer the wrong action");
assert.doesNotMatch(aliasGapHtml, />Recheck lineage</,
    "A focused PostgreSQL recheck cannot reparse the Power BI source identity");
assert.match(aliasGapHtml, /Open Scanner · Run Scan Now \(full scan\)/,
    "A server identity gap must direct the user to the full scanner operation that can fix it");
assert.match(aliasGapHtml, /pipeline-blocked/,
    "A disconnected catalog graph must block an incomplete full-pipeline plan");

context.dismiss(77, legacy);
const dismissedHtml = context.html(data);
assert.doesNotMatch(dismissedHtml, /Legacy suffix collision/,
    "Dismissing a legacy warning must hide it for the current session");
assert.match(dismissedHtml, /1 issue for this report/,
    "A dismissed legacy warning must leave only current report-specific issues");
assert.match(dismissedHtml, /Ambiguous orders/,
    "Dismissing a legacy suggestion must not suppress an exact blocker");
const changedTargetData = structuredClone(data);
changedTargetData.flow_diagnostics.items[1].target.table = "orders_v2";
assert.match(context.html(changedTargetData), /Legacy suffix collision/,
    "Changing the Flow target fingerprint must resurface a dismissed legacy warning");
const caseChangedTargetData = structuredClone(data);
caseChangedTargetData.flow_diagnostics.items[1].target.table = "Orders";
assert.match(context.html(caseChangedTargetData), /Legacy suffix collision/,
    "A case-only PostgreSQL target change must resurface a dismissed legacy warning");

const payloadWarnings = context.payloadWarnings(
    [{ id: 20, name: "Missing target Flow", target_source_ids: [99] }],
    new Set([44]),
);
assert.equal(payloadWarnings.length, 1);
assert.match(payloadWarnings[0].message, /source #99 is missing from the rendered lineage payload/);
assert.match(
    context.html({ report: { id: 77 }, flows: [], flow_diagnostics: {} }, { payloadWarnings }),
    /Missing target Flow/,
    "A backend-confirmed Flow with a missing rendered source must become a visible payload warning",
);

assert.equal(
    context.html({ report: { id: 1 }, flows: [], flow_diagnostics: {} }),
    "",
    "A clean report with no Flow information must not show an empty warning banner",
);
assert.match(context.hiddenHtml(data), /Flows are hidden—.*Show Flows/,
    "Turning off a relevant Flows column must expose a one-click recovery hint");
assert.equal(
    context.hiddenHtml({ report: { id: 1 }, flows: [], flow_diagnostics: {} }),
    "",
    "The hidden-column hint must not appear when there is no Flow information",
);

assert.match(
    source,
    /const flowNodes = \[\.\.\.\(data\.flows \|\| \[\]\)\];/,
    "The diagram must treat backend-confirmed Flows as authoritative",
);
assert.doesNotMatch(
    source,
    /\(flow\.target_source_ids \|\| \[\]\)\.some\(sourceId => allSourceIds\.has\(sourceId\)\)/,
    "The browser must not silently apply the old second reachability filter",
);
assert.doesNotMatch(source, /for \(const flow of legacyFlowNodes\)/,
    "Legacy suggestions must render in diagnostics, never as graph cards");
assert.match(
    source,
    /\["flows", "visuals", "tables"\]\.includes\(col\.key\)/,
    "An enabled Flows column must remain visible when it has zero cards",
);
assert.match(source, /const flowDiagnostics = _flowDiagnosticsHtml\(plan\)/,
    "The full-refresh preview must use the same Flow diagnostic summary");
assert.match(source, /apiPost\(`\/api\/scanner\/jobs\/postgres-lineage\$\{reportQuery\}`\)/,
    "Recheck lineage must start the durable focused PostgreSQL lineage job");
assert.match(source, /_waitForScannerJob\(start\.job_id/,
    "Recheck lineage must wait for the durable job instead of holding one request open");
assert.match(source, /flow\.executable === false/,
    "Filename-only Flow candidates must be visibly distinguished from executable SQL lineage");
assert.match(source, /Possible file link/,
    "The candidate card must explain that its file edge is tentative");
assert.match(source, /lin-edge-tentative/,
    "Filename-only lineage edges must render as tentative rather than authoritative");

console.log("flow diagnostics display tests passed");
