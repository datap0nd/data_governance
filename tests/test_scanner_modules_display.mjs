import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("function _scannerModuleState");
const end = source.indexOf("function _scannerAccordion", start);
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = {
    esc: value => String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll('"', "&quot;")
        .replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
    formatDate: value => String(value),
    timeAgo: () => "recently",
    _pbiDeviceFlowHtml: () => "",
};
vm.createContext(context);
vm.runInContext(`${source.slice(start, end)}
    this.state = _scannerModuleState;
    this.duration = _scannerRunDuration;
    this.card = _scannerModuleCard;
    this.history = _scannerModuleHistoryHtml;
`, context);

const run = (status, summary = `${status} explanation`) => ({
    id: 7,
    status,
    display_status: status,
    trigger_source: "manual",
    started_at: "2026-09-02T10:00:00Z",
    finished_at: "2026-09-02T10:00:00Z",
    summary,
    details: {
        status,
        diagnostic: {
            health_impact: status === "failed" ? "error" : status === "completed_with_warnings" ? "warning" : "none",
            reason_code: `reason_${status}`,
            operator_summary: summary,
            remediation: ["Take a specific action."],
            facts: { jobs_found: 3 },
        },
    },
});

for (const status of ["completed_with_warnings", "failed", "skipped", "stopped"]) {
    const module = { key: "postgres_schedules", label: "PostgreSQL schedules", scans: "Schedules", description: "Reads schedules", prerequisites: "Access", last_run: run(status), current_run: null };
    const html = context.card(module, false, null);
    assert.match(html, new RegExp(`${status} explanation`), `${status} must show its explanation`);
    assert.match(html, /Detailed run information/);
    assert.match(html, /Take a specific action/);
    assert.match(html, /Recent runs/);
}

assert.match(context.state(run("completed_with_warnings", "Why it warned")), /title="Why it warned"/);
assert.equal(context.duration(run("completed")), "<1s");
assert.equal(context.duration({ ...run("completed"), finished_at: "2026-09-02T10:00:01Z" }), "~1s");
assert.match(context.history([run("failed", "Authorization was rejected")]), /Authorization was rejected/);
assert.match(context.card({ key: "postgres_lineage", label: "PostgreSQL lineage", scans: "Lineage", description: "Reads lineage", prerequisites: "Access", last_run: { ...run("running"), status: "running", display_status: "stalled", finished_at: null }, current_run: null }, false, null), /stopped reporting progress/);

console.log("scanner module display tests passed");
