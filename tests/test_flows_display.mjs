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

const render = flow => context.renderFlowList([{ id: 2, name: "Export", ...flow }], [], {}, []);
assert.deepEqual(Array.from(context._flowSortColumns(), column => column[1]), ["Flow", "Active", "Owner", "Source", "Download", "Browser", "Schedule", "Last run"]);
assert.ok(html.indexOf('class="flow-activity"') < html.indexOf('<table class="flow-table'));
assert.match(render({ schedule_type: "manual" }), /aria-label="Active: Export"[^>]*disabled/);
assert.match(render({ schedule_type: "manual" }), /title="Choose a schedule to activate this flow"/);
assert.doesNotMatch(render({ owner_name: "Dana" }), /Owner: Dana|No owner/);
assert.match(render({ source_type: "portal" }), /data-field="browser_mode"/);
for (const source_type of ["outlook", "file"]) {
    assert.doesNotMatch(render({ source_type }), /data-field="browser_mode"/);
}
for (const [config, label] of [
    [{ schedule_type: "manual" }, "Manual"],
    [{ schedule_type: "daily" }, "Daily"],
    [{ schedule_type: "weekly", schedule_days: ["monday"] }, "Mondays"],
    [{ schedule_type: "weekly", schedule_days: ["wednesday", "monday"] }, "Mondays and Wednesdays"],
    [{ schedule_type: "monthly", schedule_day: 15 }, "Monthly · day 15"],
]) assert.match(render({ ...config, next_run_at: "hidden-next" }), new RegExp(`<td>${label}</td>`));
assert.doesNotMatch(render({ next_run_at: "hidden-next" }), /hidden-next|Next /);
for (const status of ["claimed", "running"]) assert.match(render({ last_status: status }), /🔧/);
assert.match(render({ last_status: "queued" }), /Waiting/);
context.window._flowsState.people = [{ id: 7, name: "Dana" }];
assert.match(render({ owner_person_id: 7 }), /<option value="7" selected>Dana/);
assert.match(render({}), /<option value="">Unassigned/);
const css = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");
assert.match(css, /main:has\(\.flow-page-header\) \{ max-width: none;/);
assert.match(css, /\.flow-run-status \.badge \{ white-space: nowrap; overflow-wrap: normal;/);
