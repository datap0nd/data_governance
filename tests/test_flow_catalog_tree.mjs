import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("/** The discovered reports as the folder tree");
const end = source.indexOf("/** Scan controls for one website.");
const context = { esc: s => String(s) };
vm.createContext(context);
vm.runInContext(source.slice(start, end) + "\nthis.tree = _flowCatalogTreeHtml;", context);

// A single-segment path (a manual report with no menu path) stays ungrouped.
const report = (path, name) => ({ name, stale: false, automation: { category_path: path } });
const reports = [
  report(["Public", "MDM", "Channel Site", "CS_IRAN"], "Public > MDM > Channel Site > CS_IRAN"),
  report(["Public", "MDM", "Channel Site", "CS_SEEG"], "Public > MDM > Channel Site > CS_SEEG"),
  report(["Public", "MDM", "Exchange Rate", "Rates"], "Public > MDM > Exchange Rate > Rates"),
  report(["Public", "SCM", "Actual Sales", "MENA"], "Public > SCM > Actual Sales > MENA"),
  report(["Private", "Biz_trip_GSCM"], "Private > Biz_trip_GSCM"),
];
const rowFor2 = r => `<row2>${r.name}</row2>`;
const rowFor = r => `<row>${r.automation.category_path.at(-1)}</row>`;
const html = context.tree(reports, rowFor, new Set(), "");
assert.doesNotMatch(html, /<details[^>]* open>/, "Catalog starts collapsed");
assert.match(source, /openCatalogTopics: new Set\(\), view: "list"/, "Every Flows entry resets expansion");
assert.match(source, /if \(!state_ \|\| state_\.catalogFilter \|\| !group\.isConnected\) return;/, "Search expansion is temporary");
const topic = html.match(/data-topic="([^"]+)"/)[1];
const opened = new Set([topic]);
assert.match(context.tree(reports, rowFor, opened, ""), /<details[^>]* open>/);
context.tree(reports, rowFor, opened, "iran");
assert.deepEqual([...opened], [topic], "Filtering never mutates visit expansion");
assert.equal(context.tree(reports, rowFor, opened, ""), context.tree(reports, rowFor, opened, ""), "Polling preserves opened branches");

// Every folder level becomes its own group, not one flat "Public" pile.
for (const folder of ["Public", "Private", "MDM", "SCM", "Channel Site", "Exchange Rate", "Actual Sales"]) {
  assert.ok(html.includes(`<strong>${folder}</strong>`), `missing folder group: ${folder}`);
}
assert.ok(html.includes("<row>CS_IRAN</row>"), "leaf report missing");
// A report is a leaf of its deepest folder, never a folder itself.
assert.ok(!html.includes("<strong>CS_IRAN</strong>"), "a report became a folder");
// Counts roll up.
assert.match(html, /<strong>MDM<\/strong> · 3 reports/);
assert.match(html, /<strong>Channel Site<\/strong> · 2 reports/);
// A filter opens every group so matches are not hidden in collapsed folders.
const filtered = context.tree([reports[0]], rowFor, new Set(), "iran");
assert.equal((filtered.match(/<details[^>]* open>/g) || []).length, 3, "filtered groups must be open");
// An ASAP-style two-segment path still groups by its category.
const asap = context.tree([report(["Mobile", "Weekly movement"], "Weekly movement")], rowFor, new Set(), "");
assert.ok(asap.includes("<strong>Mobile</strong>"));
assert.ok(asap.includes("<row>Weekly movement</row>"));
console.log("catalog folder tree tests passed");

const bare = context.tree([{ name: "Ad hoc", stale: false, automation: {} }], rowFor2, new Set(), "");
assert.equal(bare, "<row2>Ad hoc</row2>", "a report with no path must render as a plain row");
