import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

// Several schedule triggers on one Flow: the builder rows, the payload reader,
// the Add default and the Flows list label. The schedule above is trigger 1.
const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8").replace(/\r\n?/g, "\n");
const slice = name => {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `${name} must exist`);
    return source.slice(start, source.indexOf("\nfunction ", start + 1));
};
const plain = value => JSON.parse(JSON.stringify(value));
const context = { esc: value => String(value ?? "") };
vm.createContext(context);
vm.runInContext([
    'const _FLOW_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];',
    "const _FLOW_MAX_TRIGGERS = 24;",
    ...["_flowTriggerRowHtml", "_flowTriggersHtml", "_flowNextTrigger", "_flowReadTriggers", "_flowScheduleLabel", "_flowMainScheduleLabel"].map(slice),
    "this.api = {_flowTriggerRowHtml, _flowTriggersHtml, _flowNextTrigger, _flowReadTriggers, _flowScheduleLabel};",
].join("\n"), context);
const { api } = context;
assert.match(source, /const _FLOW_MAX_TRIGGERS = 24;/, "The builder limit must match the API's 1 + 23 triggers");

// Rows are numbered from 2 and only show the fields their schedule needs.
const weekly = api._flowTriggerRowHtml(2, { type: "weekly", time: "11:00", days: ["monday", "friday"] });
assert.match(weekly, /<span class="flow-trigger-name">Trigger 2<\/span>/);
assert.match(weekly, /id="flow-trigger-2-time" type="time" required value="11:00" aria-label="Trigger 2 run time"/);
assert.match(weekly, /<option value="weekly" selected>Weekly<\/option>/);
assert.match(weekly, /<fieldset class="flow-trigger-weekdays" >/);
assert.match(weekly, /value="monday" checked/);
assert.doesNotMatch(weekly, /value="tuesday" checked/);
assert.match(weekly, /<label class="flow-trigger-day-field" hidden>/);
assert.match(weekly, /aria-label="Remove trigger 2"/);
const monthly = api._flowTriggerRowHtml(3, { type: "monthly", time: "06:00", day: 31 });
assert.match(monthly, /<fieldset class="flow-trigger-weekdays" hidden>/);
assert.match(monthly, /id="flow-trigger-3-day" type="number" min="1" max="31" required value="31"/);
assert.match(api._flowTriggerRowHtml(4, { type: "hourly" }), /<option value="daily" selected>Daily<\/option>/);

// The list renders saved triggers; manual Flows show none and Outlook/file
// builders add the no-op sentence.
const saved = { schedule_type: "daily", additional_triggers: [{ type: "daily", time: "10:00" }, { type: "daily", time: "12:00" }] };
const outlook = api._flowTriggersHtml(saved, true);
assert.equal((outlook.match(/class="flow-trigger"/g) || []).length, 2);
assert.match(outlook, /<legend>More triggers<\/legend>/);
assert.match(outlook, /Trigger 1 is the schedule above; output freshness follows it\. A run that finds no new file finishes without changes\./);
assert.match(outlook, /id="flow-trigger-add" >Add trigger/);
assert.doesNotMatch(api._flowTriggersHtml(saved), /no new file/);
assert.doesNotMatch(api._flowTriggersHtml({ ...saved, schedule_type: "manual" }), /class="flow-trigger"/);
assert.doesNotMatch(api._flowTriggersHtml(null), /class="flow-trigger"/);
const full = api._flowTriggersHtml({ schedule_type: "daily", additional_triggers: Array.from({ length: 23 }, (_, index) => ({ type: "daily", time: `${String(index).padStart(2, "0")}:30` })) });
assert.match(full, /id="flow-trigger-add" disabled>/);
assert.match(full, /<span id="flow-trigger-limit" class="flow-dialog-help" >A Flow can have at most 24 triggers\.<\/span>/);

// Add copies the previous trigger one hour later, wrapping past midnight.
assert.deepEqual(plain(api._flowNextTrigger({ type: "weekly", time: "08:15", days: ["monday"], day: 4 })), { type: "weekly", time: "09:15", days: ["monday"], day: null });
assert.deepEqual(plain(api._flowNextTrigger({ type: "monthly", time: "23:30", days: [], day: 5 })), { type: "monthly", time: "00:30", days: [], day: 5 });
assert.deepEqual(plain(api._flowNextTrigger({ type: "manual", time: "" })), { type: "daily", time: "09:00", days: [], day: null });

// The reader sends only the fields each schedule uses.
const row = (type, time, days = [], day = "1") => ({
    querySelector: selector => ({ ".flow-trigger-type": { value: type }, ".flow-trigger-time": { value: time }, ".flow-trigger-day": { value: day } })[selector],
    querySelectorAll: () => days.map(value => ({ value })),
});
const root = { querySelectorAll: selector => selector === ".flow-trigger" ? [row("weekly", "11:00", ["monday"]), row("monthly", "06:00", ["friday"], "15"), row("daily", "14:00", ["sunday"], "9")] : [] };
assert.deepEqual(plain(api._flowReadTriggers(root)), [
    { type: "weekly", time: "11:00", days: ["monday"], day: null },
    { type: "monthly", time: "06:00", days: [], day: 15 },
    { type: "daily", time: "14:00", days: [], day: null },
]);
assert.match(source, /additional_triggers: scheduleType === "manual" \? \[\] : _flowReadTriggers\(document\),/);
// A server error naming a trigger focuses that row's time.
assert.match(source, /const trigger = field === 'additional_triggers' && \/Trigger \(\\d\+\)\/\.exec\(item\.msg \|\| ''\)\?\.\[1\];/);

// The Flows list keeps its old labels and counts the extra triggers.
assert.equal(api._flowScheduleLabel({ schedule_type: "daily" }), "Daily");
assert.equal(api._flowScheduleLabel(saved), "Daily + 2 more triggers");
assert.equal(api._flowScheduleLabel({ schedule_type: "weekly", schedule_days: ["monday"], additional_triggers: [{ type: "daily", time: "09:00" }] }), "Mondays + 1 more trigger");
assert.equal(api._flowScheduleLabel({ schedule_type: "manual", additional_triggers: [{ type: "daily", time: "09:00" }] }), "Manual");

console.log("flow trigger tests passed");
