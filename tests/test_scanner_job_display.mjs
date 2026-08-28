import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("function _scannerJobLabel");
const end = source.indexOf("\nfunction actionStatusBadge", start);

assert.notEqual(start, -1, "Scanner job display helpers must exist");
assert.notEqual(end, -1, "Scanner job display helpers must precede action badges");

const context = { Math, Number, String, Object, Array };
vm.createContext(context);
vm.runInContext(
    `function esc(value) { return String(value); }
     function formatDate(value) { return String(value); }
     function timeAgo() { return "2m ago"; }
     function statusBadge(status) { return "<b>" + status + "</b>"; }
     ${source.slice(start, end)}
     this.jobsHtml = _scannerJobsHtml;
     this.jobStatus = _scannerJobStatusHtml;`,
    context,
);

const stale = {
    id: 7,
    job_type: "postgres_lineage",
    status: "running",
    display_status: "stale",
    active: true,
    is_stale: true,
    heartbeat_age_seconds: 1810,
    created_at: "2026-08-28T08:00:00Z",
    current_step: "Reading PostgreSQL catalog",
    message: "Scanning warehouse.",
    progress_current: 1,
    progress_total: 3,
    result: {},
};
const staleHtml = context.jobsHtml([stale]);
assert.match(staleHtml, /PostgreSQL lineage recheck/);
assert.match(staleHtml, /possibly stuck/);
assert.match(staleHtml, /Reading PostgreSQL catalog · 1\/3/);
assert.match(staleHtml, /No heartbeat for 30 minutes/);
assert.match(staleHtml, /Stop Refresh Work before retrying/);

const failedHtml = context.jobsHtml([{
    id: 8,
    job_type: "postgres_lineage",
    status: "failed",
    display_status: "failed",
    active: false,
    created_at: "2026-08-28T08:00:00Z",
    current_step: "Failed",
    message: "Lineage could not be refreshed for: staging (fetch)",
    result: { databases: { staging: { status: "failed", stage: "fetch" } } },
}]);
assert.match(failedHtml, /Lineage could not be refreshed/);
assert.match(failedHtml, /Affected: staging · fetch/);

const mixedHtml = context.jobsHtml([stale, {
    id: 8,
    job_type: "postgres_lineage",
    status: "failed",
    display_status: "failed",
    active: false,
    created_at: "2026-08-28T07:00:00Z",
    current_step: "Failed",
    message: "Prior lineage recheck failed.",
    result: {},
}]);
assert.match(mixedHtml, /Scanning warehouse/,
    "the active operation must remain visible");
assert.match(mixedHtml, /Prior lineage recheck failed/,
    "recent terminal failures must remain visible beside active work");

assert.match(source, /api\("\/api\/scanner\/jobs"\)/,
    "Scanner must fetch durable live jobs");
assert.match(source, /apiPost\("\/api\/scanner\/jobs\/full-scan"\)/,
    "Full scans must start asynchronously");
assert.match(source, /apiPost\(`\/api\/scanner\/jobs\/postgres-lineage/,
    "Pipeline lineage rechecks must start asynchronously");
assert.match(source, /_waitForScannerJob\(start\.job_id/,
    "Pipeline lineage must poll its durable job id");

console.log("scanner job display tests passed");
