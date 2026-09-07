import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

// Synthetic Pipelines harness: renders app.js with a fake diagram payload and
// counts every edge path that passes through a card it neither leaves nor enters.
// Usage: PLAYWRIGHT_MODULE=<path to the playwright package> node check.mjs <all|default|nodeps> [screenshot.png]
// Optional: TRACE=<data-lin-id> clicks that card first to test the traced state; W=<viewport width>.
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || "playwright");

const root = path.dirname(new URL(import.meta.url).pathname);
const staticRoot = path.resolve(root, "../../../../../../app/static");
const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".woff": "font/woff" };
const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const f = p.startsWith("/static/") ? path.join(staticRoot, p.slice("/static/".length)) : path.join(root, p === "/" ? "index.html" : p);
  try { const data = fs.readFileSync(f); res.writeHead(200, { "content-type": mime[path.extname(f)] || "application/octet-stream" }); res.end(data); }
  catch { res.writeHead(404); res.end(); }
});
await new Promise(r => server.listen(0, r));
const port = server.address().port;
const mode = process.argv[2] || "all";
const out = process.argv[3] || `${root}/shot-${mode}.png`;
const colsByMode = {
  all: { upstreams: true, flows: true, mv_upstream: true, sources: true, tables: true, visuals: true },
  default: { upstreams: true, flows: true, mv_upstream: true, sources: true, tables: false, visuals: false },
  nodeps: { upstreams: true, flows: true, mv_upstream: false, sources: true, tables: true, visuals: true },
};
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: Number(process.env.W || 1500), height: 900 } });
page.on("pageerror", e => console.log("PAGEERROR", e.message));
await page.addInitScript(cols => { window.__cols = cols; }, colsByMode[mode]);
await page.goto(`http://127.0.0.1:${port}/`);
await page.waitForSelector("#lin-svg path.lin-edge", { timeout: 5000 });
await page.waitForTimeout(400);
await page.evaluate(() => document.querySelectorAll(".register-overlay").forEach(el => el.remove()));
if (process.env.TRACE) {
  await page.click(`[data-lin-id="${process.env.TRACE}"] .lin-card-lbl`);
  await page.waitForTimeout(900);
}
const report = await page.evaluate(() => {
  const wrap = document.getElementById("lin-wrap");
  const wr = wrap.getBoundingClientRect();
  const ox = wrap.scrollLeft - wr.left, oy = wrap.scrollTop - wr.top;
  const cards = [...wrap.querySelectorAll(".lin-card")].map(c => {
    const r = c.getBoundingClientRect();
    return { id: c.dataset.linId, left: r.left + ox, right: r.right + ox, top: r.top + oy, bottom: r.bottom + oy };
  });
  const hits = [];
  for (const p of wrap.querySelectorAll("path.lin-edge")) {
    const len = p.getTotalLength();
    const from = p.dataset.from, to = p.dataset.to;
    const bad = new Set();
    for (let d = 0; d <= len; d += 2) {
      const pt = p.getPointAtLength(d);
      for (const c of cards) {
        if (c.id === from || c.id === to) continue;
        if (pt.x > c.left + 0.5 && pt.x < c.right - 0.5 && pt.y > c.top + 0.5 && pt.y < c.bottom - 0.5) bad.add(c.id);
      }
    }
    if (bad.size) hits.push({ from, to, crosses: [...bad] });
  }
  return { edges: wrap.querySelectorAll("path.lin-edge").length, cards: cards.length, hits };
});
console.log(JSON.stringify(report, null, 1));
await page.locator("#lin-wrap").screenshot({ path: out });
await browser.close();
server.close();
