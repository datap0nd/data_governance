import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("const _flowActivityPoll =");
const end = source.indexOf("function _flowScheduleCatalogMonitor", start);
const indicator = { textContent: "" };
const timers = new Map();
const requests = [];
const patches = [];
let timerId = 0;
const context = {
    window: { _flowsState: { view: "list" } }, currentPage: "flows",
    document: {
        hidden: false,
        getElementById: () => indicator,
        addEventListener: (_, callback) => { context.visibility = callback; },
    },
    setTimeout: (fn, delay) => { assert.equal(delay, 5000); timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    api: url => {
        assert.equal(url, "/api/flows/activity");
        return new Promise((resolve, reject) => requests.push({ resolve, reject }));
    },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
context._flowPatchActivity = data => patches.push(data);
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };
const fireTimer = () => {
    assert.equal(timers.size, 1);
    const [id, fn] = [...timers][0]; timers.delete(id); fn();
};

context._flowRefreshActivity();
assert.equal(requests.length, 1, "immediate request on entry");
requests.shift().resolve({ status: "idle" }); await tick();
assert.equal(indicator.textContent, "");
fireTimer(); assert.equal(requests.length, 1, "idle lists still poll");
requests.shift().reject(new Error("offline")); await tick();
assert.equal(indicator.textContent, "Reconnecting…");
assert.equal(patches.length, 1, "errors retain last successful display");
fireTimer(); requests.shift().resolve({ status: "scheduled" }); await tick();
assert.equal(patches.at(-1).status, "scheduled");

context._flowRefreshActivity();
context._flowRefreshActivity();
context._flowRefreshActivity();
assert.equal(requests.length, 1, "overlapping action refreshes coalesce");
requests.shift().resolve({ status: "stale-before-action" }); await tick();
assert.equal(patches.at(-1).status, "scheduled", "action invalidates older snapshot");
assert.equal(requests.length, 1, "action gets an immediate fresh snapshot");
requests.shift().resolve({ status: "queued" }); await tick();
assert.equal(patches.at(-1).status, "queued");

context._flowRefreshActivity();
context._flowStopActivityMonitor(); context.window._flowsState.view = "builder";
requests.shift().resolve({ status: "stale-view" }); await tick();
assert.equal(patches.at(-1).status, "queued");
assert.equal(timers.size, 0, "leaving list cleans up polling");

context.window._flowsState = { view: "list" };
context._flowRefreshActivity();
context.document.hidden = true; context.visibility();
context.document.hidden = false; context.visibility();
assert.equal(requests.length, 1, "return waits for existing request to finish");
requests.shift().resolve({ status: "stale-hidden" }); await tick();
assert.equal(patches.at(-1).status, "queued");
assert.equal(requests.length, 1);
requests.shift().resolve({ status: "returned" }); await tick();
assert.equal(patches.at(-1).status, "returned");
context.document.hidden = true; context.visibility();
assert.equal(timers.size, 0, "hidden tabs stop timers");

context.document.hidden = false; context.visibility();
context._flowStopActivityMonitor(); context.currentPage = "reports";
requests.shift().resolve({ status: "stale-navigation" }); await tick();
assert.equal(patches.at(-1).status, "returned");
assert.equal(timers.size, 0);

// Execute the real DOM patch with stable row/control objects. Replacing a row
// or touching a dropdown would throw rather than silently passing this test.
const last = { innerHTML: "" };
const runButton = { dataset: {}, disabled: false, textContent: "Run" };
const stopButton = { dataset: {}, hidden: true, disabled: false };
const select = { value: "7", focused: true };
const row = {
    dataset: { flowId: "1" },
    querySelector: selector => ({ ".flow-last-run": last, ".flow-run": runButton, ".flow-stop": stopButton })[selector],
};
context.document.querySelectorAll = selector => {
    if (selector === ".flow-group-toggle") return [];
    assert.equal(selector, "tr[data-flow-id]"); return [row];
};
context.document.activeElement = select;
context._flowLastRunHtml = run => run?.status || "none";
context._flowPatchRowProgress = () => {};
vm.runInContext(source.slice(source.indexOf("function _flowPatchActivity(activity)"), source.indexOf("function _flowScheduleCatalogMonitor")), context);
for (const status of ["queued", "claimed", "running", "succeeded", "failed", "cancelled"]) {
    const run = { id: 10, flow_id: 1, status };
    const active = ["queued", "claimed", "running"].includes(status);
    context._flowPatchActivity({ latest_runs: [run], active_runs: active ? [run] : [], events: [], workers: { online: 1 } });
    assert.equal(last.innerHTML, status);
    assert.equal(stopButton.hidden, !active);
    assert.equal(runButton.disabled, active && status !== "queued");
    assert.equal(context.document.activeElement, select);
    assert.equal(select.value, "7");
}

// Inline saves send exactly one field, lock the native control, and roll back.
const bindStart = source.indexOf("function _bindFlowWorkspace() {");
const bindEnd = source.indexOf('    document.querySelectorAll(".flow-sort")', bindStart);
const flow = { id: 1, owner_person_id: 7, browser_mode: "headless" };
context.window._flowsState = { flows: [flow] };
const dropdown = { dataset: { id: "1", field: "owner_person_id" }, value: "", disabled: false };
context.document.querySelectorAll = () => [dropdown];
context.toast = () => {};
let save;
context.apiPatch = (url, body) => {
    assert.equal(url, "/api/flows/1");
    assert.equal(JSON.stringify(body), '{"owner_person_id":null}');
    return new Promise((resolve, reject) => { save = { resolve, reject }; });
};
vm.runInContext(source.slice(bindStart, bindEnd) + "\n}", context);
context._bindFlowWorkspace();
let saving = dropdown.onchange();
assert.equal(dropdown.disabled, true);
save.reject(new Error("locked")); await saving;
assert.equal(dropdown.value, "7");
assert.equal(dropdown.disabled, false);
assert.equal(flow.owner_person_id, 7);
dropdown.value = ""; saving = dropdown.onchange();
save.resolve({ id: 1, owner_person_id: null }); await saving;
assert.equal(flow.owner_person_id, null);
assert.equal(flow.browser_mode, "headless");

for (const name of ["flow-run", "flow-stop"]) {
    const line = source.split("\n").find(line => line.includes(`querySelectorAll(".${name}")`));
    assert.match(line, /_flowRefreshActivity\(\)/);
    assert.doesNotMatch(line, /navigate\(/);
}
assert.match(source.slice(source.indexOf("async function navigate(page)")), /_flowStopActivityMonitor\(\)/);
console.log("Flow activity polling and inline save tests passed");
