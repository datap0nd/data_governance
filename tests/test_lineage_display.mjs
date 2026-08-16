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
    `${source.slice(start, end)}\nthis.getLineageCols = _getLineageCols; this.setLineageCols = _setLineageCols;`,
    context,
);

const defaults = context.getLineageCols();
assert.equal(defaults.visuals, false, "Visuals must be hidden by default");
assert.equal(defaults.tables, false, "Power BI tables must be hidden by default");
assert.equal(defaults.sources, true, "Sources must remain visible by default");
assert.equal(defaults.scripts, true, "Scripts must remain visible by default");

context.setLineageCols({ ...defaults, visuals: true });
assert.equal(context.getLineageCols().visuals, true, "A user's toggle choice must persist for the session");
assert.equal(stored.has("lineage_cols_v2"), true, "The new defaults must use a versioned preference key");

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
