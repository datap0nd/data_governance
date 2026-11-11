// AI, Paths, Flow workers and Premium Viewers are sections of System > Settings.
// This runs the router from app.js and the page from settings.js (no browser)
// and checks where an address or a navigate() call lands, what the rail
// offers, and that a moved page is drawn and wired by its own functions.
// tests/test_settings_sections_browser.py walks the same journey in a browser.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const appSource = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const settingsSource = fs.readFileSync(new URL("../app/static/settings.js", import.meta.url), "utf8");
const MOVED = ["ai", "paths", "flow-settings", "premiumviewers"];

// ── The router ──────────────────────────────────────────────────────────────

const routerStart = appSource.indexOf("// ── Router ──");
const routerEnd = appSource.indexOf("\nfunction _pollScannerWorkStatus", routerStart);
const initialStart = appSource.indexOf("function getInitialPage()");
const initialEnd = appSource.indexOf("\n}\n", initialStart) + 3;
assert.ok(routerStart > 0 && routerEnd > routerStart && initialStart > 0, "app.js still has its router and getInitialPage");
const routerSource = appSource.slice(routerStart, routerEnd) + "\n" + appSource.slice(initialStart, initialEnd);
const builtins = new Set(vm.runInNewContext("Object.getOwnPropertyNames(globalThis)"));

// A page with a nav, an address bar and a history, and every renderer and
// binder of app.js replaced by a recorder.
function browser({ hash = "", path = "/", settingsOnScreen = false } = {}) {
    const calls = [];
    const history = [];
    const link = page => ({ dataset: { page }, active: false, classList: { toggle(_name, on) { this.owner.active = on; } } });
    const links = ["dashboard", "settings", "updates", "scanner"].map(page => { const item = link(page); item.classList.owner = item; return item; });
    const group = { dataset: { pages: "settings,changelog,eventlog,faq,scanner,updates" }, active: false, classList: { toggle(_name, on) { group.active = on; } } };
    const location = {
        value: hash, pathname: path,
        get hash() { return this.value ? `#${this.value}` : ""; },
        set hash(next) { this.value = String(next).replace(/^#/, ""); history.push(["push", this.value]); },
    };
    const provided = {
        location,
        history: { replaceState(_state, _title, url) { location.value = String(url).replace(/^#/, ""); history.push(["replace", location.value]); } },
        window: {},
        document: { getElementById: id => (id === "st-page" && settingsOnScreen ? {} : null), querySelectorAll: () => [] },
        sessionStorage: { setItem() {}, getItem: () => null },
        $: () => ({ innerHTML: "" }),
        $$: selector => (selector.includes(".nav-group") ? [group] : links),
        renderSettings: async section => { calls.push(["renderSettings", section]); return "<div id=\"st-page\"></div>"; },
        settingsOpenSection: async section => { calls.push(["settingsOpenSection", section]); },
    };
    const scope = new Proxy(provided, {
        // "out" carries the results back; JavaScript's own names stay real.
        has: (_target, name) => typeof name === "string" && name !== "out" && !builtins.has(name),
        get: (target, name) => {
            if (name === Symbol.unscopables) return undefined;
            if (name in target) return target[name];
            return (...args) => { calls.push([name, ...args]); return ""; };
        },
    });
    const out = {};
    vm.runInNewContext(`with (scope) { ${routerSource}
        out.navigate = navigate; out.getInitialPage = getInitialPage; out.sectionOf = _settingsSectionOf;
        out.pages = pages; out.subpages = settingsSubpages; out.currentPage = () => currentPage;
        out.onScreen = page => { currentPage = page; };
    }`, { scope, out });
    return { ...out, calls, history, location, links, group };
}

{
    const page = browser();
    // The moved pages are no longer router pages of their own; what stayed, stayed.
    for (const id of MOVED) assert.equal(page.pages[id], undefined, `${id} is not a page of its own any more`);
    for (const id of ["settings", "updates", "scanner", "changelog", "eventlog", "faq"]) assert.equal(typeof page.pages[id], "function", `${id} is still a page`);
    assert.deepEqual(Array.from(page.subpages, item => item.id), MOVED);
    assert.deepEqual(Array.from(page.subpages, item => item.name), ["AI", "Paths", "Flow workers", "Premium Viewers"]);
    for (const item of page.subpages) assert.ok(item.about && typeof item.render === "function" && typeof item.bind === "function", `${item.id} brings its renderer and binder`);

    // An old address and the new one name the same section.
    for (const id of MOVED) {
        assert.equal(page.sectionOf(id), id);
        assert.equal(page.sectionOf(`settings/${id}`), id);
    }
    assert.equal(page.sectionOf("settings/flows"), "flows", "a registry section has an address too");
    for (const other of ["settings", "updates", "scanner", "reports", "", undefined]) assert.equal(page.sectionOf(other), null);
}

// navigate('<old page>') from anywhere in the app lands on Settings at that section.
for (const id of MOVED) {
    const page = browser({ hash: "reports" });
    await page.navigate(id);
    assert.equal(page.currentPage(), "settings");
    assert.deepEqual(page.calls.find(call => call[0] === "renderSettings"), ["renderSettings", id]);
    assert.equal(page.location.hash, `#settings/${id}`);
    assert.deepEqual(page.history, [["push", `settings/${id}`]], "a real move is a history entry");
    assert.equal(page.links.find(link => link.dataset.page === "settings").active, true, "the Settings menu entry is the active one");
    assert.equal(page.links.find(link => link.dataset.page === "updates").active, false);
    assert.equal(page.group.active, true, "System stays highlighted");
    assert.ok(page.calls.some(call => call[0] === "bindSettingsPage"), "Settings wires the page it drew");
}

// An old address typed or bookmarked is rewritten, not stacked: Back must not
// return to #paths and be bounced forward again.
for (const id of MOVED) {
    const page = browser({ hash: id });
    assert.equal(page.getInitialPage(), id, "the old address is still understood at start-up");
    await page.navigate(page.getInitialPage());
    assert.deepEqual(page.history, [["replace", `settings/${id}`]]);
    assert.deepEqual(page.calls.find(call => call[0] === "renderSettings"), ["renderSettings", id]);
}
{
    const page = browser({ path: "/paths" });
    assert.equal(page.getInitialPage(), "paths", "the old path-style address is still understood");
}

// Registry sections: #settings alone, and #settings/<section>.
{
    const page = browser({ hash: "settings/flows" });
    assert.equal(page.getInitialPage(), "settings/flows");
    await page.navigate("settings/flows");
    assert.deepEqual(page.calls.find(call => call[0] === "renderSettings"), ["renderSettings", "flows"]);
    assert.deepEqual(page.history, [], "the address was already right, so nothing is written");

    const plain = browser({ hash: "settings" });
    await plain.navigate("settings");
    assert.deepEqual(plain.calls.find(call => call[0] === "renderSettings"), ["renderSettings", undefined], "plain #settings reopens where it was left");
    assert.deepEqual(plain.history, []);
}

// With Settings already on screen, a moved page's own navigate('paths') after
// a save changes the section in place instead of redrawing the whole page.
{
    const page = browser({ hash: "settings/paths", settingsOnScreen: true });
    page.onScreen("settings");
    await page.navigate("paths");
    assert.deepEqual(page.calls.filter(call => call[0] === "settingsOpenSection"), [["settingsOpenSection", "paths"]]);
    assert.equal(page.calls.some(call => call[0] === "renderSettings"), false);
    assert.deepEqual(page.history, []);
}

// Updates and Scanner stayed System pages.
{
    const page = browser({ hash: "settings/paths" });
    await page.navigate("updates");
    assert.equal(page.currentPage(), "updates");
    assert.ok(page.calls.some(call => call[0] === "renderUpdates") && page.calls.some(call => call[0] === "bindUpdatesPage"));
    assert.equal(page.location.hash, "#updates");
    assert.equal(page.links.find(link => link.dataset.page === "updates").active, true);
}

// ── The Settings page ───────────────────────────────────────────────────────

const setting = (key, label) => ({
    key, label, help: "", kind: "int", unit: "", minimum: 1, maximum: 9, choices: [], choice_labels: [], placeholder: "",
    optional: false, default: 3, value: 3, source: "default", applies: "now", applies_text: "Applies immediately", source_text: "Default", saved: null,
});
const REGISTRY = {
    can_edit: true,
    sections: [
        { id: "folders", name: "Folders", blurb: "Fixture folders.", groups: [{ title: "Folders on the host PC", settings: [setting("folders.depth", "Folder depth")], test: null }],
          links: [{ label: "Flows root and enforced paths", page: "paths", note: "Shows which Flows a change would affect." }] },
        { id: "updates", name: "Updates, backups and logs", blurb: "Fixture updates.", groups: [{ title: "Logs", settings: [setting("logs.keep_days", "Days of logs kept")], test: null }],
          links: [{ label: "Install now and update history", page: "updates", note: "" }] },
    ],
};

// settings.js with the helpers it borrows from app.js, a page made of plain
// objects, and (unless told otherwise) the moved pages app.js lists.
function settingsPage({ canEdit = true, withPages = true, failing = [] } = {}) {
    const log = [];
    const registry = structuredClone(REGISTRY);
    const element = () => ({ innerHTML: "", hidden: false, value: "", checked: false, isConnected: true, contains: () => false, querySelector: () => null, focus() {}, addEventListener() {} });
    const elements = Object.fromEntries(["st-page", "st-rail", "st-panel", "st-embed", "st-savebar-slot", "st-notice-slot", "st-logged", "st-search", "st-only-changed"].map(id => [`#${id}`, element()]));
    const location = { hash: "#settings" };
    const context = {
        esc: value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
        $: selector => elements[selector] || null,
        api: async url => {
            log.push(["GET", url]);
            if (url === "/api/system/settings") return structuredClone({ ...registry, can_edit: canEdit });
            throw new Error("not in this fixture");
        },
        toast: message => log.push(["toast", message]),
        formatDate: value => String(value),
        document: { activeElement: null, getElementById: () => null, createElement: () => element() },
        location,
        history: {
            pushState: (_state, _title, url) => { location.hash = url; log.push(["push", url]); },
            replaceState: (_state, _title, url) => { location.hash = url; log.push(["replace", url]); },
        },
    };
    if (withPages) {
        context.settingsSubpages = ["AI|ai|1", "Paths|paths|1", "Flow workers|flow-settings|1", "Premium Viewers|premiumviewers|"].map(text => {
            const [name, id, hostOnly] = text.split("|");
            return {
                id, name, hostOnly: Boolean(hostOnly), about: id === "flow-settings" ? "Portal browser, worker capacity and slots." : `About ${name}.`,
                render: async () => {
                    log.push(["render", id]);
                    if (failing.includes(id)) throw new Error(`${name} is unavailable`);
                    return `<div class="page-header"><h1>${name}</h1></div><form id="${id}-form"></form>`;
                },
                bind: () => log.push(["bind", id]),
            };
        });
    }
    vm.createContext(context);
    vm.runInContext(settingsSource, context);
    const run = code => vm.runInContext(code, context);
    return { run, log, elements, location, registry };
}

const railNames = html => [...html.matchAll(/<button[^>]*data-st-section="([^"]+)"[^>]*aria-current="(true|false)"/g)].map(match => match[1] + (match[2] === "true" ? "*" : ""));

// The rail: registry sections first, then the moved pages under their own label.
{
    const page = settingsPage();
    const html = await page.run("renderSettings()");
    const rail = html.slice(html.indexOf('id="st-rail"'), html.indexOf('id="st-panel"'));
    assert.deepEqual(railNames(rail), ["folders*", "updates", "ai", "paths", "flow-settings", "premiumviewers"]);
    assert.ok(rail.indexOf('data-st-section="updates"') < rail.indexOf("More settings") && rail.indexOf("More settings") < rail.indexOf('data-st-section="ai"'),
        "the moved pages form their own labelled group after the registry sections");
    assert.match(html, /id="st-embed" hidden/, "a registry section shows the registry panel");
    assert.deepEqual(page.log.filter(entry => entry[0] === "render"), [], "no moved page is loaded until it is opened");

    // A registry section's pointer to a moved page opens that section; a pointer
    // to a page that stayed still goes to that page.
    assert.match(html, /<a href="#settings\/paths" data-st-section="paths">Flows root and enforced paths<\/a>/);
    page.run("settingsState.section = 'updates'");
    assert.match(page.run("settingsPanelHtml()"), /<a href="#updates" data-st-page="updates">Install now and update history<\/a>/);
}

// settings.js on its own (app.js not loaded yet, or a page without it) still draws its registry.
{
    const page = settingsPage({ withPages: false });
    const html = await page.run("renderSettings('paths')");
    assert.deepEqual(railNames(html), ["folders*", "updates"]);
    assert.doesNotMatch(html, /More settings/);
}

// Opening Settings at a moved page draws that page's own markup and wires it once.
{
    const page = settingsPage();
    const html = await page.run("renderSettings('paths')");
    assert.deepEqual(railNames(html), ["folders", "updates", "ai", "paths*", "flow-settings", "premiumviewers"]);
    assert.match(html, /id="st-panel" hidden/);
    assert.match(html, /id="st-embed"><div class="page-header"><h1>Paths<\/h1><\/div><form id="paths-form">/);
    assert.match(html, /id="st-logged" hidden/, "the Event Log sentence is only claimed for registry settings");
    page.elements["#st-embed"].innerHTML = "drawn";
    page.run("bindSettingsPage()");
    assert.deepEqual(page.log.filter(entry => entry[0] === "render" || entry[0] === "bind"), [["render", "paths"], ["bind", "paths"]]);
    assert.deepEqual(page.log.filter(entry => entry[0] === "replace"), [["replace", "#settings/paths"]], "the address names the open section");
}

// An unknown section falls back to the first one instead of an empty page.
{
    const page = settingsPage();
    const html = await page.run("renderSettings('nowhere')");
    assert.deepEqual(railNames(html), ["folders*", "updates", "ai", "paths", "flow-settings", "premiumviewers"]);
}

// Changing section in place: the page is loaded, drawn, wired, and addressed.
{
    const page = settingsPage();
    await page.run("renderSettings()");
    page.run("bindSettingsPage()");
    await page.run("settingsOpenSection('flow-settings')");
    assert.equal(page.elements["#st-panel"].hidden, true);
    assert.equal(page.elements["#st-embed"].hidden, false);
    assert.match(page.elements["#st-embed"].innerHTML, /id="flow-settings-form"/);
    assert.deepEqual(page.log.filter(entry => entry[0] === "render" || entry[0] === "bind"), [["render", "flow-settings"], ["bind", "flow-settings"]]);
    assert.deepEqual(page.log.filter(entry => entry[0] === "push"), [["push", "#settings/flow-settings"]]);
    assert.deepEqual(railNames(page.elements["#st-rail"].innerHTML).filter(name => name.endsWith("*")), ["flow-settings*"]);

    // Two quick clicks: only the page that is still wanted is drawn and wired.
    const first = page.run("settingsOpenSection('ai')");
    const second = page.run("settingsOpenSection('premiumviewers')");
    await Promise.all([first, second]);
    assert.match(page.elements["#st-embed"].innerHTML, /id="premiumviewers-form"/);
    assert.deepEqual(page.log.filter(entry => entry[0] === "bind").map(entry => entry[1]), ["flow-settings", "premiumviewers"]);

    // Back to a registry section: its panel returns and the moved page is gone.
    await page.run("settingsOpenSection('updates')");
    assert.equal(page.elements["#st-panel"].hidden, false);
    assert.equal(page.elements["#st-embed"].hidden, true);
    assert.equal(page.elements["#st-embed"].innerHTML, "");
    assert.match(page.elements["#st-panel"].innerHTML, /Days of logs kept/);
}

// A moved page can save a value the registry shows too (Flow workers and the
// Workers group under Flows). Leaving it reads the registry again, and an
// unsaved edit elsewhere survives that.
{
    const page = settingsPage();
    await page.run("renderSettings()");
    page.run("bindSettingsPage(); settingsState.edits['logs.keep_days'] = 8");
    await page.run("settingsOpenSection('flow-settings')");
    page.registry.sections[0].groups[0].settings[0].value = 7;
    await page.run("settingsOpenSection('folders')");
    assert.match(page.elements["#st-panel"].innerHTML, /data-st-key="folders\.depth" value="7"/, "the registry row shows what the moved page saved");
    await page.run("settingsOpenSection('updates')");
    assert.match(page.elements["#st-panel"].innerHTML, /data-st-key="logs\.keep_days" value="8"/);
    assert.match(page.run("settingsSaveBarHtml()"), />1 unsaved change</);
}

// A moved page that cannot load says so in its own place; the rest of Settings stays.
{
    const page = settingsPage({ failing: ["paths"] });
    const html = await page.run("renderSettings('paths')");
    assert.match(html, /Could not load Paths/);
    assert.match(html, /Paths is unavailable/);
    assert.match(html, /<button type="button" class="btn-outline" data-st-section="paths">Try again<\/button>/);
    assert.deepEqual(railNames(html).length, 6);
    page.run("bindSettingsPage()");
    assert.deepEqual(page.log.filter(entry => entry[0] === "bind"), [], "nothing is wired to a page that did not load");
}

// The search box finds a moved page by what it holds, and hides nothing it should not.
{
    const page = settingsPage();
    await page.run("renderSettings('paths')");
    page.run("bindSettingsPage()");
    page.elements["#st-embed"].innerHTML = "typed text lives here";
    page.run("settingsState.query = 'worker'; settingsRefresh()");
    assert.equal(page.elements["#st-embed"].hidden, true);
    assert.equal(page.elements["#st-embed"].innerHTML, "typed text lives here", "a search hides the moved page without discarding it");
    assert.match(page.elements["#st-panel"].innerHTML, /<a href="#settings\/flow-settings" data-st-section="flow-settings">Flow workers<\/a>/);
    assert.doesNotMatch(page.elements["#st-panel"].innerHTML, /data-st-section="premiumviewers"/);
    page.run("settingsState.query = ''; settingsRefresh()");
    assert.equal(page.elements["#st-embed"].hidden, false);
    assert.equal(page.elements["#st-embed"].innerHTML, "typed text lives here");
}

// Another computer: the notice says only what holds for the section on screen.
{
    const page = settingsPage({ canEdit: false });
    await page.run("renderSettings()");
    assert.match(page.run("settingsNoticeHtml()"), /Only the host PC can change settings\.<\/strong> You are looking from another computer, so everything here is read-only\./);
    page.run("settingsState.section = 'paths'");
    assert.match(page.run("settingsNoticeHtml()"), /Only the host PC can save changes to Paths\./);
    assert.doesNotMatch(page.run("settingsNoticeHtml()"), /read-only/);
    page.run("settingsState.section = 'premiumviewers'");
    assert.equal(page.run("settingsNoticeHtml()"), "", "Premium Viewers can be saved from any computer, as before");

    const host = settingsPage();
    await host.run("renderSettings('paths')");
    assert.equal(host.run("settingsNoticeHtml()"), "");
}

// Unsaved registry edits are counted where they are, also while a moved page is open.
{
    const page = settingsPage();
    await page.run("renderSettings()");
    page.run("settingsState.edits['folders.depth'] = 5");
    assert.match(page.run("settingsSaveBarHtml()"), />1 unsaved change</);
    page.run("settingsState.section = 'paths'");
    assert.match(page.run("settingsSaveBarHtml()"), />1 unsaved change in other sections</);
    assert.match(page.run("settingsRailHtml()"), /data-st-section="folders"[^>]*><span>Folders<\/span><span class="st-count">1 unsaved<\/span>/);
}

console.log("settings sections tests passed");
