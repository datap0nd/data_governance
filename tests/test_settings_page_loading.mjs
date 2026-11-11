// app.js must load on a page that does not include settings.js. The preview
// pages under app/static/recording-preview load app.js on its own, and a name
// from settings.js used while app.js loads stops every one of them.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const settingsSource = fs.readFileSync(new URL("../app/static/settings.js", import.meta.url), "utf8");

// Every top-level name settings.js defines.
const fromSettings = new Set(
    [...settingsSource.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|^(?:let|const|var)\s+([A-Za-z_$][\w$]*)/gm)]
        .map(match => match[1] || match[2]),
);
assert.ok(fromSettings.has("renderSettings") && fromSettings.has("bindSettingsPage"));

// Build the router's page table the way the browser does while app.js loads:
// every name resolves except the ones that live in settings.js.
const start = source.indexOf("const pages = {");
assert.ok(start > 0, "app.js still declares its page table");
const end = source.indexOf("\n};", start);
const literal = source.slice(start + "const pages = ".length, end + 2);
const scope = new Proxy({}, {
    // "pages" is the result, kept outside the stand-in scope.
    has: (_target, name) => typeof name === "string" && name !== "pages" && !fromSettings.has(name),
    get: (_target, name) => (name === Symbol.unscopables ? undefined : () => ""),
});
const sandbox = { scope, pages: null };
vm.runInNewContext(`with (scope) { pages = ${literal} }`, sandbox);
assert.equal(typeof sandbox.pages.settings, "function", "Settings stays a page of the router");
assert.equal(typeof sandbox.pages.dashboard, "function");

// The same holds for the list of pages that live inside Settings: app.js
// builds it while loading, from its own functions only.
const listStart = source.indexOf("const settingsSubpages = [");
assert.ok(listStart > 0 && listStart < start, "app.js lists the pages inside Settings before its page table");
const listEnd = source.indexOf("\n];", listStart);
// The stand-in scope leaves the name "pages" alone, so the list lands there.
const listed = { scope, pages: null };
vm.runInNewContext(`with (scope) { pages = ${source.slice(listStart + "const settingsSubpages = ".length, listEnd + 2)} }`, listed);
assert.equal(listed.pages.length, 4);
for (const page of listed.pages) {
    assert.equal(typeof page.render, "function", `${page.id} is drawn by a function of app.js`);
    assert.equal(typeof page.bind, "function", `${page.id} is wired by a function of app.js`);
}

// With settings.js loaded as well, opening the page reaches its renderer.
let opened = 0;
const loaded = { scope: new Proxy({ renderSettings: () => { opened += 1; return "<div></div>"; } }, {
    has: (_target, name) => name !== "pages",
    get: (target, name) => (name === Symbol.unscopables ? undefined : target[name] || (() => "")),
}), pages: null };
vm.runInNewContext(`with (scope) { pages = ${literal} }`, loaded);
loaded.pages.settings();
assert.equal(opened, 1);

// The production page includes settings.js before app.js.
const index = fs.readFileSync(new URL("../app/static/index.html", import.meta.url), "utf8");
assert.ok(index.indexOf("/static/settings.js") > 0 && index.indexOf("/static/settings.js") < index.indexOf("/static/app.js"));

console.log("ok");
