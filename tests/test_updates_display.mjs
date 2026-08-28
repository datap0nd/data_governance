import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const index = fs.readFileSync(new URL("../app/static/index.html", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");
const start = source.indexOf("// ── System > Updates ──");
const end = source.indexOf("\nasync function renderRefreshSchedule", start);

assert.notEqual(start, -1, "System Updates implementation must exist");
assert.notEqual(end, -1, "System Updates implementation must have a bounded source section");
const updatesSource = source.slice(start, end);

assert.match(index, /data-pages="[^"]*\bupdates\b/,
    "Updates must be registered as a System page");
assert.match(index, /href="#updates" data-page="updates"[^>]*>Updates<\/a>/,
    "System must expose an Updates navigation item");
assert.doesNotMatch(index, />Update App<\/a>/,
    "the legacy immediate-update navigation action must be removed");
assert.match(source, /updates:\s*renderUpdates/,
    "the application router must render the Updates page");
assert.match(source, /page === "updates"\) bindUpdatesPage\(\)/,
    "the application router must bind Updates controls");
assert.match(updatesSource, /api\("\/api\/system\/updates"\)/,
    "the page must load the consolidated update status");
assert.match(updatesSource, /apiPut\("\/api\/system\/updates", _updatesSettingsPayload\(\)\)/,
    "the page must save the watcher toggle through the settings endpoint");
assert.match(updatesSource, /"\/api\/system\/updates\/check"/,
    "Check now must use the forced-check endpoint");
assert.match(updatesSource, /"\/api\/system\/updates\/apply"/,
    "Install now must use the idle-safe apply endpoint");
assert.doesNotMatch(source, /apiPost\(["'`]\/api\/update["'`]\)/,
    "the UI must not call the legacy update alias");
assert.doesNotMatch(source, /window\.close\(\)|document\.body\.innerHTML/,
    "an update must preserve the page while the service restarts");
assert.match(updatesSource, /window\.location\.reload\(\)/,
    "the page must reload only after reconnecting to the updated service");
assert.match(style, /\.updates-shell\s*\{/,
    "Updates must have a dedicated responsive layout");

const elements = {
    "updates-auto-enabled": { checked: true },
};
const status = {
    version: "20260828-120000-abc123def",
    current_commit: "abc123def0123456789",
    latest_commit: "fed9876543210000000",
    up_to_date: false,
    auto_update: {
        enabled: true,
        branch: "main",
        interval_minutes: 5,
        task_name: "Metronome Automatic Update",
        deployed_commit: "abc123def0123456789",
        latest_commit: "fed9876543210000000",
        update_available: true,
        check_error: null,
        status: "update_available",
        last_checked_at: "2026-08-28T12:00:00Z",
        last_attempt_at: "2026-08-27T12:00:00Z",
        last_attempt_commit: "aaa111bbb0000000000",
        last_error: "Prior update failed cleanly.",
    },
    updater_ready: true,
    updater_error: null,
    tests_gate: {
        workflow: "Tests",
        target_commit: "fed9876543210000000",
        state: "passed",
        status: "completed",
        conclusion: "success",
        checked_at: "2026-08-28T12:00:00Z",
        message: "The exact main commit passed the Tests workflow.",
    },
    active_work: { scanner_jobs: 1, flow_runs: 2 },
    active_attempt: null,
    latest_attempt: {
        attempt_id: "attempt-1",
        status: "failed",
        target_commit: "aaa111bbb0000000000",
        finished_at: "2026-08-27T12:01:00Z",
        error: "Prior update failed cleanly.",
    },
};
const context = {
    Boolean,
    Date,
    Number,
    Object,
    Set,
    String,
    window: {},
    document: { getElementById: id => elements[id] || null },
    api: async () => status,
    formatDate: value => String(value),
    timeAgo: value => String(value),
};
vm.createContext(context);
vm.runInContext(`
    function esc(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }
    ${updatesSource}
    this.readPayload = _updatesSettingsPayload;
    this.renderUpdatesPage = renderUpdates;
    this.readBlockers = _updatesBlockers;
    this.normalizeStatus = _normalizeUpdateStatus;
`, context);

const payload = context.readPayload();
assert.deepEqual(Object.keys(payload), ["enabled"],
    "the settings request must send only the supported enabled field");
assert.equal(payload.enabled, true);

const html = await context.renderUpdatesPage();
assert.match(html, /id="updates-settings-form"/);
assert.match(html, /id="updates-auto-enabled"[^>]*checked/);
assert.match(html, /20260828-120000-abc123def/);
assert.match(html, /abc123def/);
assert.match(html, /fed987654/);
assert.match(html, /Metronome Automatic Update/);
assert.match(html, /Main tests/);
assert.match(html, /Passed/);
assert.match(html, /Scanner Jobs: 1/);
assert.match(html, /Flow Runs: 2/);
assert.match(html, /Prior update failed cleanly\./);
assert.match(html, /id="btn-check-updates"/);
assert.match(html, /id="btn-install-update"[^>]*disabled/,
    "active production work must visibly block manual installation");

const notReady = context.normalizeStatus({
    auto_update: {},
    updater_ready: false,
    updater_error: "Run setup.ps1 once to register the updater task.",
});
assert.equal(notReady.readiness.ready, false);
assert.equal(notReady.readiness.reason, "Run setup.ps1 once to register the updater task.");

const launched = context.normalizeStatus({
    status: "launched",
    current_commit: "abc123def0123456789",
    latest_commit: "fed9876543210000000",
    auto_update: {},
    updater_ready: true,
    attempt: {
        attempt_id: "attempt-2",
        status: "launched",
        target_commit: "fed9876543210000000",
    },
});
assert.equal(launched.activeAttempt.attempt_id, "attempt-2",
    "the apply response's nested attempt must seed durable restart tracking");

context.api = async () => ({
    ...status,
    active_work: {},
    latest_attempt: null,
    tests_gate: {
        workflow: "Tests",
        target_commit: "fed9876543210000000",
        state: "pending",
        status: "in_progress",
        conclusion: null,
        message: "The Tests workflow is still running for this main commit.",
    },
});
const pendingHtml = await context.renderUpdatesPage();
assert.match(pendingHtml, /Waiting For Tests/);
assert.match(pendingHtml, /Tests workflow is still running/);
assert.match(pendingHtml, /id="btn-install-update"[^>]*disabled/,
    "manual installation must remain disabled until exact-SHA Tests pass");

console.log("Updates display tests passed");
