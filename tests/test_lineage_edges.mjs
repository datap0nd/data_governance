import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");

const start = source.indexOf("// Edge routing constants.");
const end = source.indexOf("\nfunction _drawLinEdges", start);
assert.notEqual(start, -1, "Pipeline edge routing helpers must exist");
assert.notEqual(end, -1, "Pipeline edge routing helpers must sit before the renderer");

const context = {};
vm.createContext(context);
vm.runInContext(
    `${source.slice(start, end)}\nthis.edgePath = _linEdgePath; this.assignChannels = _linAssignChannels;`,
    context,
);

// ── Channel packing ──

const gutterLeft = 100, gutterRight = 144;
const overlapping = [
    { y1: 100, y2: 400 },
    { y1: 120, y2: 300 },
    { y1: 140, y2: 260 },
];
context.assignChannels(overlapping, gutterLeft, gutterRight);
const lanes = new Set(overlapping.map(e => e.lane));
assert.equal(lanes.size, 3, "Runs that overlap vertically must each get their own channel");
const channels = new Set(overlapping.map(e => e.xc));
assert.equal(channels.size, 3, "Overlapping runs must not be drawn on the same x");
for (const e of overlapping) {
    assert.ok(e.xc > gutterLeft && e.xc < gutterRight,
        `Channel ${e.xc} must stay inside the gutter between the two columns`);
}

const stacked = [
    { y1: 100, y2: 140 },
    { y1: 300, y2: 340 },
    { y1: 500, y2: 540 },
];
context.assignChannels(stacked, gutterLeft, gutterRight);
assert.deepEqual(stacked.map(e => e.lane), [0, 0, 0],
    "Runs that never overlap vertically must reuse one channel");

const crowded = Array.from({ length: 40 }, (_, i) => ({ y1: 0, y2: 1000 + i }));
context.assignChannels(crowded, gutterLeft, gutterRight);
const spacings = [...new Set(crowded.map(e => e.xc))].sort((a, b) => a - b);
for (let i = 1; i < spacings.length; i++) {
    assert.ok(spacings[i] - spacings[i - 1] >= 3.5,
        "Channels must never crowd into an unreadable bundle");
}

// ── Path shape ──

const straight = context.edgePath({ x1: 10, y1: 50, x2: 200, y2: 50.4, xc: 120 });
assert.match(straight, /^M10,50 L200,50\.4$/, "Cards on the same row must be joined by one flat line");

const elbow = context.edgePath({ x1: 10, y1: 50, x2: 200, y2: 300, xc: 120 });
const points = [...elbow.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)]
    .map(m => ({ x: Number(m[1]), y: Number(m[2]) }));
for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    assert.ok(Math.abs(a.x - b.x) < 0.01 || Math.abs(a.y - b.y) < 0.01,
        "Every segment must run flat or straight down, never diagonally across the grid");
}
for (const p of points) {
    assert.ok(p.x >= 10 - 0.01 && p.x <= 200 + 0.01,
        "An edge must stay between the card it leaves and the card it enters");
}
assert.ok(elbow.includes("Q"), "Elbows must be rounded rather than square");
const verticalRun = points.filter(p => Math.abs(p.x - 120) < 0.01);
assert.ok(verticalRun.length >= 2, "The vertical hop must sit on the assigned channel");

const tooClose = context.edgePath({ x1: 10, y1: 50, x2: 24, y2: 300, xc: 17 });
assert.match(tooClose, /^M10,50 C/, "Cards too close to route around must fall back to a curve");

const noChannel = context.edgePath({ x1: 10, y1: 50, x2: 400, y2: 300, xc: null });
assert.match(noChannel, /^M10,50 C/, "Edges without a channel (same or backward column) must curve");

// ── Styling ──

const edgeBlock = style.match(/\.lin-edge\s*\{([^}]*)\}/)?.[1] || "";
const hlBlock = style.match(/\.lin-edge-hl\s*\{([^}]*)\}/)?.[1] || "";
assert.doesNotMatch(edgeBlock, /animation/, "Resting edges must not animate");
assert.doesNotMatch(hlBlock, /animation|stroke-dasharray/,
    "A traced lineage must read as solid lines, not marching ants");
assert.match(style, /\.lin-grid\s*\{[^}]*column-gap:/,
    "The grid must reserve a column gap wide enough to route edges through");
assert.match(style, /#lin-arrow path\s*\{[^}]*fill:/, "Traced edges must carry a direction arrow");

console.log("lineage edge routing tests passed");
