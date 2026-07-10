import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");

function makeContext() {
    const values = new Map();
    const storage = {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key),
    };
    const app = {
        innerHTML: "",
        querySelector: () => null,
        querySelectorAll: () => [],
    };
    const document = {
        body: { appendChild: () => {}, classList: { add: () => {}, remove: () => {} } },
        cookie: "",
        addEventListener: () => {},
        querySelector: selector => selector === "#app" ? app : null,
        querySelectorAll: () => [],
        getElementById: id => id === "app" ? app : null,
        documentElement: { classList: { contains: () => false, add: () => {}, remove: () => {} } },
    };
    const context = {
        console,
        document,
        sessionStorage: storage,
        localStorage: storage,
        location: { hash: "", pathname: "/" },
        navigator: { clipboard: { writeText: async () => {} } },
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        requestAnimationFrame: callback => callback(),
        fetch: async () => ({ ok: true, json: async () => ({}) }),
        Blob,
        URL,
        Event,
        confirm: () => false,
    };
    context.window = context;
    context.globalThis = context;
    context.addEventListener = () => {};
    context.scrollY = 0;
    context.scrollTo = () => {};
    context.matchMedia = () => ({ matches: false });
    vm.createContext(context);
    vm.runInContext(source, context, { filename: "app/static/app.js" });
    return { context, app, storage };
}

function anchors(html) {
    return Array.from(html.matchAll(/<a\b[^>]*>[\s\S]*?<\/a>/g), match => match[0]);
}

function assertTab(html, page, label, active = false) {
    const tab = anchors(html).find(anchor =>
        anchor.includes(`data-page="${page}"`) &&
        anchor.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").toLowerCase().includes(label.toLowerCase())
    );
    assert.ok(tab, `missing ${label} tab for ${page}`);
    if (active) assert.match(tab, /aria-current="page"/);
}

{
    const { context } = makeContext();
    const routes = Array.from(vm.runInContext("Object.keys(pages)", context));
    for (const route of ["dashboard", "tasks", "bestpractices", "reports", "sources", "scripts", "scheduledtasks", "powerautomate", "lineage", "scanner", "diagnostics", "refreshschedule"]) {
        assert.ok(routes.includes(route), `route must exist: ${route}`);
    }
    assert.equal(vm.runInContext("pageAliases.alerts", context), "dashboard");
    assert.equal(vm.runInContext("pageAliases.issues", context), "dashboard");
    assert.equal(vm.runInContext("LINEAGE_PRESETS.summary.scripts", context), false);
    assert.equal(vm.runInContext("LINEAGE_PRESETS.technical.scripts", context), true);

    const work = vm.runInContext('renderWorkspaceChrome("dashboard")', context);
    assertTab(work, "dashboard", "Issues", true);
    assertTab(work, "tasks", "Team tasks");
    assertTab(work, "bestpractices", "Quality");

    const catalog = vm.runInContext('renderWorkspaceChrome("scheduledtasks")', context);
    assertTab(catalog, "reports", "Reports");
    assertTab(catalog, "sources", "Sources");
    assertTab(catalog, "scripts", "Pipelines", true);
    assertTab(catalog, "scripts", "Scripts");
    assertTab(catalog, "scheduledtasks", "Scheduled jobs", true);
    assertTab(catalog, "powerautomate", "Power Automate");

    const operations = vm.runInContext('renderWorkspaceChrome("diagnostics")', context);
    assertTab(operations, "scanner", "Runs");
    assertTab(operations, "diagnostics", "Diagnostics", true);
    assertTab(operations, "refreshschedule", "Schedule");
}

{
    const { context, storage } = makeContext();
    const columns = [
        { key: "name", label: "Name" },
        { key: "owner", label: "Owner" },
        { key: "status", label: "Status" },
        { key: "type", label: "Type" },
        { key: "internal", label: "Internal" },
    ];
    const rows = [
        { id: 1, name: "Alpha", owner: "Alice", status: "degraded", type: "sql", internal: "secret-a" },
        { id: 2, name: "Beta", owner: "Bob", status: "fresh", type: "postgresql", internal: "secret-b" },
        { id: 3, name: "Gamma", owner: "Alice", status: "fresh", type: "excel", internal: "secret-c" },
    ];
    context.dataTable("dt-contract", columns, rows, { defaultHidden: ["internal"], onRowClick: () => {} });
    const dt = context.window._dt["dt-contract"];
    assert.equal(dt.globalQuery, "");
    assert.equal(dt.filtersVisible, false);
    assert.deepEqual(Array.from(dt.hiddenColumns), ["internal"]);

    dt.globalQuery = "ALICE";
    assert.deepEqual(Array.from(context._filterAndSortDT(dt), row => row.id), [1, 3]);
    dt.filters.status = "fresh";
    assert.deepEqual(Array.from(context._filterAndSortDT(dt), row => row.id), [3]);
    dt.globalQuery = "";
    dt.filters = { type: "sql|postgresql" };
    assert.deepEqual(Array.from(context._filterAndSortDT(dt), row => row.id), [1, 2]);

    dt.filters = {};
    let html = context._renderDT("dt-contract");
    assert.match(html, /data-dt-search="dt-contract"/);
    assert.match(html, /data-dt-filter-toggle="dt-contract"/);
    assert.match(html, /data-dt-reset="dt-contract"/);
    assert.doesNotMatch(html, /<th\b[^>]*data-col="internal"/);
    assert.doesNotMatch(html, />secret-[abc]</);
    assert.match(html, /data-toggle-col="internal"/);
    assert.match(html, /aria-label="Filter Name"/);
    assert.match(html, /<tr\b(?=[^>]*data-clickable)(?=[^>]*tabindex="0")[^>]*>/);

    dt.sortCol = "name";
    dt.sortDir = "desc";
    html = context._renderDT("dt-contract");
    assert.match(html.match(/<th\b[^>]*data-col="name"[^>]*>[\s\S]*?<\/th>/)?.[0] || "", /aria-sort="descending"/);

    dt.globalQuery = "alpha";
    dt.filtersVisible = true;
    context._saveDTState("dt-contract");
    const persisted = JSON.parse(storage.getItem("dt_state_dt-contract"));
    assert.equal(persisted.globalQuery, "alpha");
    assert.equal(persisted.filtersVisible, true);
    assert.deepEqual(persisted.hiddenColumns, ["internal"]);
}

{
    const { context } = makeContext();
    context.sqlSources = [
        { id: 1, type: "sql", name: "dbo.one", status: "fresh", archived: false },
        { id: 2, type: "postgresql", name: "dbo.two", status: "fresh", archived: false },
        { id: 3, type: "excel", name: "file.xlsx", status: "fresh", archived: false },
    ];
    vm.runInContext(`api = async path => path.startsWith("/api/sources") ? sqlSources : path === "/api/create/options" ? { people: [] } : []`, context);
    const html = await context.renderSources();
    const databaseButton = html.match(/<button\b[^>]*data-filter="database"[^>]*>[\s\S]*?<\/button>/)?.[0] || "";
    assert.match(databaseButton.replace(/<[^>]+>/g, " "), /\(2\)/);
    assert.match(String(context.bindSourcesPage), /sql\|postgresql/);
}

{
    const { context, app } = makeContext();
    let resolveSlow;
    let resolveFast;
    context.slowPage = new Promise(resolve => { resolveSlow = resolve; });
    context.fastPage = new Promise(resolve => { resolveFast = resolve; });
    vm.runInContext("pages.reports = () => slowPage; pages.sources = () => fastPage", context);
    const slowNavigation = context.navigate("reports");
    const fastNavigation = context.navigate("sources");
    resolveFast("<h1>FAST SOURCES</h1>");
    await fastNavigation;
    assert.match(app.innerHTML, /FAST SOURCES/);
    resolveSlow("<h1>STALE REPORTS</h1>");
    await slowNavigation;
    assert.match(app.innerHTML, /FAST SOURCES/);
    assert.doesNotMatch(app.innerHTML, /STALE REPORTS/);
    assert.equal(vm.runInContext("navigationRequestId", context), 2);
}

const bootstrapStart = source.indexOf('document.addEventListener("DOMContentLoaded"');
assert.notEqual(bootstrapStart, -1);
assert.doesNotMatch(source.slice(bootstrapStart), /^\s*initAIChatPanel\s*\(\s*\)\s*;\s*$/m);
assert.doesNotMatch(source, /window\._skipHash/);
assert.match(source, /api\(`\/api\/sources\/\$\{source\.id\}\/probes`\)/);

console.log("UI redesign tests passed");
