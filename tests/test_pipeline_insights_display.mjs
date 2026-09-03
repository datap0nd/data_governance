import assert from "node:assert/strict";
import fs from "node:fs";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");

const drawStart = source.indexOf("function _drawLinEdges");
const bindStart = source.indexOf("function _bindLinInsightInteractions", drawStart);
const normalBindings = source.indexOf("function _bindLinInteractions", bindStart);
assert.notEqual(drawStart, -1);
assert.notEqual(bindStart, -1);
assert.notEqual(normalBindings, -1);
const insightSource = source.slice(drawStart, normalBindings);

assert.match(insightSource, /class", "lin-edge-hit"/,
    "every rendered connection needs a dedicated transparent hit path");
assert.match(insightSource, /hit\.setAttribute\("tabindex", "0"\)/,
    "edge explanations must be keyboard focusable");
assert.match(insightSource, /_linHideEdgeTooltip\(\);[\s\S]*svg\.innerHTML/,
    "a redraw must unanchor the delegated tooltip before replacing SVG children");
assert.match(insightSource, /wrap\.addEventListener\("pointerover"/,
    "one wrapper-level handler must delegate pointer hover");
assert.match(insightSource, /wrap\.addEventListener\("focusin"/,
    "one wrapper-level handler must delegate keyboard focus");
assert.doesNotMatch(insightSource, /hit\.addEventListener/,
    "redrawn paths must not accumulate individual listeners");
assert.match(insightSource, /\/api\/pipeline-insights\/sources\/\$\{encodeURIComponent\(sourceId\)\}\/sample/,
    "relation previews must be fetched lazily from the cache-only endpoint");
assert.doesNotMatch(insightSource, /postgres|qwen/i,
    "browser hover code must not contain a live PostgreSQL or Qwen request path");
assert.match(insightSource, /_linSampleCell[\s\S]*esc\(/,
    "all cached cell values must be escaped before rendering");

const hitStyle = style.match(/\.lin-edge-hit\s*\{([^}]*)\}/)?.[1] || "";
assert.match(hitStyle, /stroke-width:\s*12/,
    "the transparent edge hit target should be approximately twelve pixels wide");
assert.match(hitStyle, /pointer-events:\s*stroke/);
const svgStyle = style.match(/\.lin-svg\s*\{([^}]*)\}/)?.[1] || "";
assert.match(svgStyle, /z-index:\s*(?:[2-9]|[1-9]\d+)/,
    "the interactive SVG must sit above the lineage grid so its hit paths receive pointer events");
assert.match(style, /\.lin-sample-scroll\s*\{[^}]*overflow:\s*auto/,
    "wide cached previews need an interactive horizontal scroller");

assert.match(source, /Pipeline connection explanations/,
    "System AI settings must expose the independent feature toggle");
assert.match(source, /Hover or focus a connection to read its explanation/,
    "the lineage view must explain how to reveal cached connection explanations");
assert.match(source, /Save Pipeline Insights schedule/,
    "Refresh Schedule must expose the independent weekly scanner settings");

console.log("pipeline insights display tests passed");
