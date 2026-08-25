import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const start = source.indexOf("function _lineageSourceLayers");
const end = source.indexOf("\nfunction _renderLineageDiagram", start);

assert.notEqual(start, -1, "_lineageSourceLayers must exist");
assert.notEqual(end, -1, "_lineageSourceLayers must end before the renderer");

const context = {};
vm.createContext(context);
vm.runInContext(`${source.slice(start, end)}\nthis.buildLayers = _lineageSourceLayers;`, context);

const sources = [10, 11, 12, 13, 14].map(id => ({ id, name: `source_${id}` }));

const deep = context.buildLayers({
    sources,
    source_deps: [
        { source_id: 10, depends_on_id: 11 },
        { source_id: 11, depends_on_id: 12 },
        { source_id: 12, depends_on_id: 13 },
        { source_id: 13, depends_on_id: 14 },
    ],
}, new Set([10]));

assert.deepEqual(
    Array.from(deep.layers, layer => Array.from(layer, item => item.id)),
    [[10], [11], [12], [13], [14]],
    "deep dependencies should produce one column per level",
);

const shared = context.buildLayers({
    sources,
    source_deps: [
        { source_id: 10, depends_on_id: 11 },
        { source_id: 10, depends_on_id: 12 },
        { source_id: 11, depends_on_id: 13 },
        { source_id: 12, depends_on_id: 13 },
        { source_id: 13, depends_on_id: 14 },
    ],
}, new Set([10]));

assert.deepEqual(
    Array.from(shared.layers, layer => Array.from(layer, item => item.id)),
    [[10], [11, 12], [13], [14]],
    "a shared ancestor should render once at a stable level",
);

const unequalPathsData = {
    sources,
    source_deps: [
        { source_id: 10, depends_on_id: 11 },
        { source_id: 10, depends_on_id: 14 },
        { source_id: 11, depends_on_id: 12 },
        { source_id: 12, depends_on_id: 14 },
    ],
};
const unequalPaths = context.buildLayers(unequalPathsData, new Set([10]));

assert.deepEqual(
    Array.from(unequalPaths.layers, layer => Array.from(layer, item => item.id)),
    [[10], [11], [12], [14]],
    "the longer path should determine a shared ancestor's column",
);

const directAndUpstream = context.buildLayers(unequalPathsData, new Set([10, 14]));

assert.deepEqual(
    Array.from(directAndUpstream.layers, layer => Array.from(layer, item => item.id)),
    [[10], [11], [12], [14]],
    "a direct source that is also upstream should render once at its deepest required level",
);

const cycle = context.buildLayers({
    sources: sources.slice(0, 3),
    source_deps: [
        { source_id: 10, depends_on_id: 11 },
        { source_id: 11, depends_on_id: 12 },
        { source_id: 12, depends_on_id: 10 },
    ],
}, new Set([10]));

assert.deepEqual(
    Array.from(cycle.layers, layer => Array.from(layer, item => item.id)),
    [[10], [11], [12]],
    "cycles should terminate without duplicating source cards",
);

const mixedCycleSources = [1, 2, 3, 4].map(id => ({ id, name: `mixed_${id}` }));
const mixedCycle = context.buildLayers({
    sources: mixedCycleSources,
    source_deps: [
        { source_id: 1, depends_on_id: 3 },
        { source_id: 1, depends_on_id: 4 },
        { source_id: 2, depends_on_id: 3 },
        { source_id: 2, depends_on_id: 4 },
        { source_id: 4, depends_on_id: 2 },
    ],
}, new Set([1]));

assert.deepEqual(
    Array.from(mixedCycle.layers, layer => Array.from(layer, item => item.id)),
    [[1], [4], [2], [3]],
    "cycle breaking should not pull an acyclic downstream source left",
);

const crossingReduced = context.buildLayers({
    sources: [
        { id: 1, name: "A direct" },
        { id: 2, name: "B direct" },
        { id: 10, name: "A upstream but belongs to B" },
        { id: 11, name: "Z upstream but belongs to A" },
    ],
    source_deps: [
        { source_id: 1, depends_on_id: 11 },
        { source_id: 2, depends_on_id: 10 },
    ],
}, new Set([1, 2]));

assert.deepEqual(
    Array.from(crossingReduced.layers, layer => Array.from(layer, item => item.id)),
    [[1, 2], [11, 10]],
    "upstream cards should follow their connected parents instead of arbitrary alphabetical order",
);

console.log("lineage layer tests passed");
