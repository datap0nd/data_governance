import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");
const start = source.indexOf("const LINEAGE_COLS =");
const end = source.indexOf("\nasync function renderLineageDiagram", start);

assert.notEqual(start, -1, "Pipelines column configuration must exist");
assert.notEqual(end, -1, "Pipelines column configuration must end before the renderer");

const stored = new Map();
const context = {
    sessionStorage: {
        getItem: key => stored.get(key) ?? null,
        setItem: (key, value) => stored.set(key, value),
    },
};
vm.createContext(context);
vm.runInContext(
    `${source.slice(start, end)}\nthis.lineageCols = LINEAGE_COLS; this.getLineageCols = _getLineageCols; this.setLineageCols = _setLineageCols; this.buildColumnDefs = _lineageColumnDefs;`,
    context,
);

assert.deepEqual(
    Array.from(context.lineageCols, col => col.key),
    ["upstreams", "flows", "mv_upstream", "sources", "tables", "visuals"],
    "Pipeline columns must run from upstream producers on the left to report visuals on the right",
);

const orderedColumns = context.buildColumnDefs(
    Object.fromEntries(Array.from(context.lineageCols, col => [col.key, true])),
    [
        { key: "mv_upstream_1", label: "Upstream 1" },
        { key: "mv_upstream_2", label: "Upstream 2" },
    ],
);
assert.deepEqual(
    Array.from(orderedColumns, col => col.key),
    ["upstreams", "flows", "mv_upstream_2", "mv_upstream_1", "sources", "tables", "visuals"],
    "Deep source dependencies must be placed before their consumers",
);

const defaults = context.getLineageCols();
assert.equal(defaults.visuals, false, "Visuals must be hidden by default");
assert.equal(defaults.tables, false, "Power BI tables must be hidden by default");
assert.equal(defaults.sources, true, "Sources must remain visible by default");
assert.equal(defaults.flows, true, "Flows must remain visible by default");

context.setLineageCols({ ...defaults, visuals: true });
assert.equal(context.getLineageCols().visuals, true, "A user's toggle choice must persist for the session");
assert.equal(stored.has("lineage_cols_v2"), true, "The new defaults must use a versioned preference key");

const graphStart = source.indexOf("function _buildLinGraph");
const graphEnd = source.indexOf("\n// Edge routing constants.", graphStart);
assert.notEqual(graphStart, -1, "Pipeline graph builder must exist");
assert.notEqual(graphEnd, -1, "Pipeline graph builder must end before edge routing");
vm.runInContext(
    `${source.slice(graphStart, graphEnd)}\nthis.window = {}; this.buildGraph = _buildLinGraph;`,
    context,
);
vm.runInContext(`
    buildGraph(
        {
            source_deps: [{ source_id: 10, depends_on_id: 11 }],
            flows: [{ id: 30, target_source_ids: [10] }],
        },
        [{ id: "visual-1", fields: ["Model.Field"], page: "Overview" }],
        new Map([["Model", [{ id: "field-Model.Field" }]]]),
        [{ name: "Model", source_id: 10 }],
        [{ id: 10, upstream_id: 20 }, { id: 11 }],
        [{ id: 20 }],
    );
    this.svgEdges = window._linSvgEdges;
`, context);
const edgeKeys = new Set(Array.from(context.svgEdges, edge => `${edge.from}->${edge.to}`));
for (const expected of [
    "upstream-20->source-10",
    "flow-30->source-10",
    "source-11->source-10",
    "source-10->table-Model",
    "table-Model->page-Overview",
]) {
    assert.equal(edgeKeys.has(expected), true, `Pipeline edge must follow left-to-right data flow: ${expected}`);
}

for (const selector of ["lin-card-lbl", "lin-subrow-label"]) {
    const block = style.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`))?.[1] || "";
    assert.match(block, /white-space:\s*normal/, `${selector} must allow wrapping`);
    assert.match(block, /overflow-wrap:\s*anywhere/, `${selector} must wrap unbroken names`);
    assert.doesNotMatch(block, /text-overflow:\s*ellipsis/, `${selector} must not truncate names`);
}

const headerBlock = style.match(/\.lin-card-hdr\s*\{([^}]*)\}/)?.[1] || "";
const metaBlock = style.match(/\.lin-card-meta\s*\{([^}]*)\}/)?.[1] || "";
assert.match(headerBlock, /flex-wrap:\s*wrap/, "Pipeline card headers must wrap controls and metadata");
assert.match(metaBlock, /flex:\s*1 0 100%/, "Pipeline metadata must use its own wrapping line");

console.log("lineage display tests passed");
