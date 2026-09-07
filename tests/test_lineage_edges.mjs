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
    `${source.slice(start, end)}\nthis.edgePath = _linEdgePath; this.assignChannels = _linAssignChannels; this.assignPorts = _linAssignPorts; this.routeEdges = _linRouteEdges; this.columnBands = _linColumnBands;`,
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

const saturated = Array.from({ length: 12 }, (_, i) => ({ y1: 0, y2: 500 + i }));
context.assignChannels(saturated, 100, 156); // 40px usable band => 10 slots
assert.equal(new Set(saturated.map(e => e.xc)).size, 10,
    "Channels must only be reused after every physical slot is occupied");
assert.equal(new Set(saturated.slice(0, 10).map(e => e.lane)).size, 10,
    "Lane packing must stop at the gutter's physical capacity");
assert.ok(saturated.slice(10).every(e => e.lane < 10),
    "Runs beyond physical capacity must reuse an existing lane without modulo wrapping");

// A saturated short interval must not shrink the remembered occupancy of the
// longer interval whose lane it reuses.
const saturationOccupancy = [
    { y1: 0, y2: 100 },
    { y1: 0, y2: 1000 },
    { y1: 10, y2: 20 },
    { y1: 50, y2: 60 },
];
context.assignChannels(saturationOccupancy, 100, 124); // two usable slots
assert.equal(saturationOccupancy[2].lane, saturationOccupancy[0].lane);
assert.equal(saturationOccupancy[3].lane, saturationOccupancy[0].lane,
    "Saturated reuse must retain, rather than shorten, the prior lane occupancy");

// ── Per-card ports ──

const fanOut = [
    { from: "a", to: "high", y1: 100, y2: 250, y1min: 90, y1max: 110, y2min: 240, y2max: 260 },
    { from: "a", to: "low", y1: 100, y2: 50, y1min: 90, y1max: 110, y2min: 40, y2max: 60 },
];
context.assignPorts(fanOut);
assert.notEqual(fanOut[0].y1, fanOut[1].y1, "Fan-out edges need distinct source ports");
assert.ok(fanOut.find(e => e.to === "low").y1 < fanOut.find(e => e.to === "high").y1,
    "Source ports must follow target order to avoid crossings at the card face");
assert.ok(fanOut.every(e => e.y1 >= e.y1min && e.y1 <= e.y1max),
    "Source ports must stay within the source header band");

const fanIn = [
    { from: "high", to: "z", y1: 250, y2: 100, y1min: 240, y1max: 260, y2min: 90, y2max: 110 },
    { from: "low", to: "z", y1: 50, y2: 100, y1min: 40, y1max: 60, y2min: 90, y2max: 110 },
];
context.assignPorts(fanIn);
assert.notEqual(fanIn[0].y2, fanIn[1].y2, "Fan-in edges need distinct target ports");
assert.ok(fanIn.find(e => e.from === "low").y2 < fanIn.find(e => e.from === "high").y2,
    "Target ports must follow source order to avoid crossings at the card face");
assert.ok(fanIn.every(e => e.y2 >= e.y2min && e.y2 <= e.y2max),
    "Target ports must stay within the target header band");

const singleton = [
    { from: "only-a", to: "only-b", y1: 77, y2: 88, y1min: 70, y1max: 80, y2min: 80, y2max: 90 },
];
context.assignPorts(singleton);
assert.equal(singleton[0].y1, 77, "A singleton source port must remain centered");
assert.equal(singleton[0].y2, 88, "A singleton target port must remain centered");

// ── Pure router ──

// Every column's free bands: the gaps between its stacked cards plus the open
// space below the last card. The middle columns are dense at the top so a
// straight line from "span" to "far" would cut through their cards.
const colBounds = [
    { left: 0, right: 150, bands: [{ top: 500, bottom: 520 }] },
    { left: 206, right: 356, bands: [{ top: 102.5, bottom: 117.5 }, { top: 222.5, bottom: 237.5 }, { top: 402.5, bottom: 420 }] },
    { left: 412, right: 562, bands: [{ top: 162.5, bottom: 177.5 }, { top: 282.5, bottom: 297.5 }, { top: 342.5, bottom: 360 }] },
    { left: 618, right: 768, bands: [{ top: 700, bottom: 720 }] },
];
const occupied = [
    { col: 1, top: 0, bottom: 100 }, { col: 1, top: 120, bottom: 220 }, { col: 1, top: 240, bottom: 400 },
    { col: 2, top: 0, bottom: 160 }, { col: 2, top: 180, bottom: 280 }, { col: 2, top: 300, bottom: 340 },
];
function pathPoints(d) {
    return [...d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map(m => ({ x: Number(m[1]), y: Number(m[2]) }));
}
function assertClearOfCards(edge, label) {
    // Sample the emitted cubic segments densely and refuse any point that
    // lands inside a card of a column the edge merely passes through.
    const d = context.edgePath(edge);
    const segments = d.split(/(?=[MLC])/);
    let cursor = null;
    const samples = [];
    for (const segment of segments) {
        const pts = pathPoints(segment);
        if (segment.startsWith("M") || segment.startsWith("L")) {
            if (cursor && segment.startsWith("L")) {
                for (let t = 0; t <= 1; t += 0.05) samples.push({ x: cursor.x + (pts[0].x - cursor.x) * t, y: cursor.y + (pts[0].y - cursor.y) * t });
            }
            cursor = pts[0];
        } else if (segment.startsWith("C")) {
            const [c1, c2, end] = pts;
            for (let t = 0; t <= 1; t += 0.02) {
                const u = 1 - t;
                samples.push({
                    x: u * u * u * cursor.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * end.x,
                    y: u * u * u * cursor.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * end.y,
                });
            }
            cursor = end;
        }
    }
    for (const p of samples) {
        for (const card of occupied) {
            if (card.col <= edge.ci || card.col >= edge.cj) continue;
            const b = colBounds[card.col];
            assert.ok(!(p.x > b.left && p.x < b.right && p.y > card.top && p.y < card.bottom),
                `${label} must not cross the card at column ${card.col} rows ${card.top}-${card.bottom} (hit ${p.x.toFixed(1)},${p.y.toFixed(1)})`);
        }
    }
    return samples;
}
const routed = [
    { from: "span", to: "far", x1: 150, y1: 50, x2: 618, y2: 300, y1min: 40, y1max: 60, y2min: 290, y2max: 310, ci: 0, cj: 3 },
    { from: "adj", to: "next", x1: 356, y1: 100, x2: 412, y2: 200, y1min: 90, y1max: 110, y2min: 190, y2max: 210, ci: 1, cj: 2 },
    { from: "same-a", to: "same-b", x1: 356, y1: 300, x2: 356, y2: 400, y1min: 290, y1max: 310, y2min: 390, y2max: 410, ci: 1, cj: 1 },
    { from: "back", to: "behind", x1: 562, y1: 500, x2: 206, y2: 600, y1min: 490, y1max: 510, y2min: 590, y2max: 610, ci: 2, cj: 1 },
];
context.routeEdges(routed, colBounds);
assert.ok(routed[1].c1x > routed[1].x1 && routed[1].c1x <= routed[1].c2x && routed[1].c2x < routed[1].x2,
    "Forward curve controls must stay ordered between their card endpoints");
assert.ok(routed[1].c1x > colBounds[1].right && routed[1].c2x < colBounds[2].left,
    "An adjacent-column curve must fan smoothly inside its sole gutter");

// ── Multi-column crossings ──

const span = routed[0];
assert.equal(span.c1x, undefined, "A multi-column edge must not be one long curve through the columns in between");
assert.equal(span.via?.length, 2, "A multi-column edge needs one crossing per column it passes through");
span.via.forEach((v, i) => {
    const column = colBounds[span.ci + 1 + i];
    assert.equal(v.left, column.left);
    assert.equal(v.right, column.right, "Each crossing must span the full column it passes");
    assert.ok(column.bands.some(b => v.y >= b.top && v.y <= b.bottom),
        `Crossing ${i} at y=${v.y} must sit inside a band that holds no card`);
});
assert.deepEqual(Array.from(span.via, v => Math.round(v.y * 10) / 10), [117.5, 177.5],
    "Each crossing must take the free band nearest to where a straight line would pass");
const spanPath = context.edgePath(span);
assert.match(spanPath, /^M150,50 C/, "A multi-column edge must leave its card with a smooth curve");
assert.ok(spanPath.includes(` L${colBounds[1].right},117.5`) && spanPath.includes(` L${colBounds[2].right},177.5`),
    "Each intermediate column must be crossed flat, inside its chosen band");
const spanSamples = assertClearOfCards(span, "A multi-column edge");
assert.ok(spanSamples.every(p => p.x >= span.x1 - 0.01 && p.x <= span.x2 + 0.01),
    "A multi-column edge must stay horizontally between its two cards");

const sameGap = [
    { from: "g1", to: "h1", x1: 150, y1: 108, x2: 412, y2: 100, y1min: 100, y1max: 116, y2min: 90, y2max: 110, ci: 0, cj: 2 },
    { from: "g2", to: "h2", x1: 150, y1: 112, x2: 412, y2: 130, y1min: 104, y1max: 120, y2min: 120, y2max: 140, ci: 0, cj: 2 },
    { from: "g3", to: "h3", x1: 150, y1: 116, x2: 412, y2: 160, y1min: 108, y1max: 124, y2min: 150, y2max: 170, ci: 0, cj: 2 },
];
context.routeEdges(sameGap, colBounds);
const gapYs = sameGap.map(e => Math.round(e.via[0].y * 10) / 10);
assert.equal(new Set(gapYs).size, 3, "Edges sharing one gap must spread onto distinct lanes");
assert.ok(gapYs.every(y => y >= 102.5 && y <= 117.5), "Spread lanes must remain inside the gap");
assert.deepEqual([...gapYs].sort((a, b) => a - b), gapYs,
    "Lanes inside a gap must follow arrival order so they do not cross");
for (const e of sameGap) assertClearOfCards(e, "A lane-spread crossing");

const noBands = [
    { from: "plain", to: "far", x1: 150, y1: 50, x2: 618, y2: 300, y1min: 40, y1max: 60, y2min: 290, y2max: 310, ci: 0, cj: 3 },
];
context.routeEdges(noBands, colBounds.map(c => ({ left: c.left, right: c.right })));
assert.deepEqual(Array.from(noBands[0].via, v => Math.round(v.y)), [133, 217],
    "Without band data a crossing falls back to the straight-line height");

// ── Free bands ──

// Results come from the vm realm; copy them so strict deepEqual compares plain objects.
const plain = value => JSON.parse(JSON.stringify(value));
const bands = plain(context.columnBands([
    { top: 0, bottom: 20 },
    { top: 32, bottom: 80 },
    { top: 82, bottom: 120 },
    { top: 90, bottom: 100 },
    { top: 140, bottom: 200 },
], 260));
assert.deepEqual(bands, [
    { top: 22.5, bottom: 29.5 },
    { top: 122.5, bottom: 137.5 },
    { top: 202.5, bottom: 260 },
], "Bands must cover only real gaps between stacked children, then the space below the last one");
assert.deepEqual(plain(context.columnBands([], 40)), [{ top: 2.5, bottom: 40 }],
    "An empty column is one open band down to the drawing floor");
assert.deepEqual(plain(context.columnBands([{ top: 0, bottom: 100 }], 60)), [{ top: 102.5, bottom: 102.5 }],
    "A band below the floor collapses to a single line rather than leaving the drawing");
assert.ok(routed[2].cx >= colBounds[1].right && routed[2].cx <= colBounds[2].left,
    "A same-column edge must loop through the gutter to its right");
assert.ok(routed[3].bow >= Math.min(routed[3].x1, routed[3].x2) + 8
    && routed[3].bow <= Math.max(routed[3].x1, routed[3].x2) - 8,
    "A backward edge's control point must stay between its endpoints");
assert.ok(routed.slice(0, 2).every(e => e.cx == null && e.bow == null),
    "Forward edges must not receive curve routing controls");
assert.ok(routed.slice(0, 2).every(e => e.xc == null),
    "Live forward edges must not collapse into shared orthogonal channels");
assert.equal(routed[1].via, undefined, "An adjacent-column edge has no column to cross");

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

const forwardCurve = context.edgePath(routed[1]);
assert.equal(forwardCurve, "M356,100 C379.5,100 388.5,200 412,200",
    "Forward edges must emit one smooth monotone cubic instead of shared elbow rails");
const forwardXs = [...forwardCurve.matchAll(/(-?\d+(?:\.\d+)?),-?\d+(?:\.\d+)?/g)]
    .map(match => Number(match[1]));
assert.ok(forwardXs.every(x => x >= routed[1].x1 && x <= routed[1].x2),
    "Forward curve points must stay horizontally between their cards");

const sameColumnPath = context.edgePath(routed[2]);
const sameColumnXs = [...sameColumnPath.matchAll(/(-?\d+(?:\.\d+)?),-?\d+(?:\.\d+)?/g)]
    .map(match => Number(match[1]));
assert.ok(sameColumnXs.every(x => x >= colBounds[1].right && x <= colBounds[2].left),
    "Every same-column curve point must stay outside the card column and inside its gutter");

const conflictingBack = [
    { from: "b1", to: "a1", x1: 562, y1: 100, x2: 206, y2: 300, y1min: 95, y1max: 105, y2min: 295, y2max: 305, ci: 2, cj: 1 },
    { from: "b2", to: "a2", x1: 562, y1: 150, x2: 206, y2: 350, y1min: 145, y1max: 155, y2min: 345, y2max: 355, ci: 2, cj: 1 },
];
context.routeEdges(conflictingBack, colBounds);
assert.notEqual(conflictingBack[0].bow, conflictingBack[1].bow,
    "Vertically conflicting backward curves must use distinct bows");
assert.notEqual(context.edgePath(conflictingBack[0]), context.edgePath(conflictingBack[1]),
    "Vertically conflicting curves must produce distinct paths");

const nonConflictingBack = [
    { from: "b3", to: "a3", x1: 562, y1: 100, x2: 206, y2: 150, y1min: 95, y1max: 105, y2min: 145, y2max: 155, ci: 2, cj: 1 },
    { from: "b4", to: "a4", x1: 562, y1: 300, x2: 206, y2: 350, y1min: 295, y1max: 305, y2min: 345, y2max: 355, ci: 2, cj: 1 },
];
context.routeEdges(nonConflictingBack, colBounds);
assert.equal(nonConflictingBack[0].lane, 0);
assert.equal(nonConflictingBack[1].lane, 0,
    "Non-conflicting curves in the same column pair may reuse a bow lane");

// ── Styling ──

const edgeBlock = style.match(/\.lin-edge\s*\{([^}]*)\}/)?.[1] || "";
const hlBlock = style.match(/\.lin-edge-hl\s*\{([^}]*)\}/)?.[1] || "";
const haloBlock = style.match(/\.lin-edge-halo\s*\{([^}]*)\}/)?.[1] || "";
assert.doesNotMatch(edgeBlock, /animation/, "Resting edges must not animate");
assert.doesNotMatch(hlBlock, /animation|stroke-dasharray/,
    "A traced lineage must read as solid lines, not marching ants");
assert.match(haloBlock, /stroke-width:\s*4(?:\.\d+)?/,
    "Edges need a surface-colored halo so crossings remain visually separable");
assert.match(style, /\.lin-grid\s*\{[^}]*column-gap:/,
    "The grid must reserve a column gap wide enough to route edges through");
const colGap = Number(style.match(/\.lin-col\s*\{[^}]*gap:\s*([\d.]+)rem/)?.[1]);
assert.ok(colGap >= 0.7, `Cards need a row gap of at least 0.7rem for a crossing edge to pass between them (found ${colGap}rem)`);
assert.match(style, /#lin-arrow path\s*\{[^}]*fill:/, "Traced edges must carry a direction arrow");

console.log("lineage edge routing tests passed");
