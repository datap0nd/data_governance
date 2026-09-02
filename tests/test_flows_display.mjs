import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("function _flowListHtml");
const end = source.indexOf("/** The folder a report sits in", start);

assert.notEqual(start, -1, "Flow list renderer must exist");
assert.notEqual(end, -1, "Flow list renderer must precede report helpers");

const context = {
    window: { _flowsState: { catalog: { asap_download_types: [] } } },
    esc: value => String(value ?? ""),
    formatDate: value => String(value),
    timeAgo: value => String(value),
    _flowEmptyState: () => "empty",
    _flowStatusBadge: status => `<status>${status ?? ""}</status>`,
};
vm.createContext(context);
vm.runInContext(`${source.slice(start, end)}\nthis.renderFlowList = _flowListHtml;`, context);

const html = context.renderFlowList([{
    id: 1,
    name: "Daily export",
    enabled: true,
    schedule_type: "daily",
    source_type: "file",
    local_file_path: "C:\\input.csv",
    file_format: "csv",
    output_mode: "direct_replace",
    last_status: "completed",
    freshness_health: { status: "pending" },
}], [{ status: "online" }], {}, []);

assert.doesNotMatch(html, /<th>Freshness<\/th>/, "Flows must not display freshness");
assert.doesNotMatch(html, />Pending</, "Flows must not render pending freshness health");
assert.match(source, /label: "Freshness"/, "Sources must retain their Freshness column");
assert.doesNotMatch(source, /function _flowFreshnessHtml/, "Unused Flow freshness renderer must be removed");

console.log("flows display tests passed");
