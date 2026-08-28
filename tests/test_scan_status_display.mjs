import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("function statusBadge");
const end = source.indexOf("\nfunction actionStatusBadge", start);

assert.notEqual(start, -1, "The shared status badge renderer must exist");
assert.notEqual(end, -1, "Scanner status helpers must precede action badges");

const context = {};
vm.createContext(context);
vm.runInContext(
    `function esc(value) { return String(value); }\n${source.slice(start, end)}\n` +
    "this.statusBadge = statusBadge; " +
    "this.warningLabels = _scanWarningLabels; " +
    "this.runStatusHtml = _scanRunStatusHtml; " +
    "this.completionToast = _scanCompletionToast;",
    context,
);

const warningRun = {
    status: "completed_with_warnings",
    reports_scanned: 12,
    sources_found: 34,
    components: {
        core: { status: "completed" },
        postgres_dependencies: {
            status: "completed_with_warnings",
            databases: {
                warehouse: { status: "completed" },
                staging: { status: "failed", error: "redacted" },
            },
        },
        postgres_schedules: { status: "skipped" },
        probe: { status: "not_requested" },
    },
};

assert.match(
    context.statusBadge("completed_with_warnings"),
    /badge-yellow[^>]*>completed with warnings</,
    "A warning scan must render as an amber, explicit status rather than healthy",
);
assert.deepEqual(
    Array.from(context.warningLabels(warningRun)),
    ["PostgreSQL dependencies (staging)", "PostgreSQL schedules"],
    "Warning details must name the affected database and component without treating not_requested as a warning",
);
assert.match(
    context.runStatusHtml(warningRun),
    /PostgreSQL dependencies \(staging\)/,
    "Scanner history must retain the affected database beside its warning badge",
);
const toast = context.completionToast(warningRun, "PBI sync completed");
assert.match(toast, /^Scan completed with warnings/);
assert.match(toast, /PostgreSQL dependencies \(staging\)/);
assert.match(toast, /PostgreSQL schedules/);
assert.match(toast, /12 reports, 34 sources; PBI sync completed$/);

assert.equal(
    context.completionToast(
        { status: "completed", reports_scanned: 2, sources_found: 5, components: null },
        "PBI sync skipped",
    ),
    "Scan complete: 2 reports, 5 sources; PBI sync skipped",
    "A clean scan must keep its existing plain-success wording",
);

assert.match(
    source,
    /render: r => _scanRunStatusHtml\(r\)/,
    "Scanner history must use the warning-aware status renderer",
);
assert.match(
    source,
    /apiPost\("\/api\/scanner\/jobs\/full-scan"\)/,
    "The manual scan must start a durable background job instead of holding one request open",
);
assert.match(
    source,
    /_scanRunStatusHtml\(scan\).*scan\.reports_scanned/s,
    "The Dashboard last-scan card must expose the warning status",
);

console.log("scan status display tests passed");
