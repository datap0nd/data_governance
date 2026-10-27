// Record the real Wizard web app for the film: Chrome DevTools screencast frames (JPEG, 2x) with their timestamps,
// plus markers for the edit. Wizard runs in replay mode over its SYNTHETIC fixtures (capture/serve.py).
// Capture-only presentation: the replay/synthetic development labels and the replay demo suggestions are hidden, a
// visible cursor is drawn, and nothing else on the page is changed.
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path');
const BASE = 'http://127.0.0.1:8779', W = 1600, H = 900, DPR = 2;
const take = process.argv[2], OUT = path.join(__dirname, 'takes', take);
const QUARTER = 'How did we do this quarter against our targets?';
const STOCK = 'Where is stock building up in the channel?';
const TARGETS = path.join(__dirname, 'files', 'Q3 targets.xlsx');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const CSS = `[data-testid=runtime],[data-testid=synthetic-banner],[data-testid=data-mode][data-mode=SYNTHETIC],[data-testid=suggestion],
[data-testid=empty-state] p.text-warn,button.rounded-full.shadow-lg.absolute{display:none!important}
#film-cursor{position:fixed;left:0;top:0;width:26px;height:32px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px);transition:none;filter:drop-shadow(0 2px 3px rgba(10,14,40,.35))}
#film-cursor i{position:absolute;left:-17px;top:-17px;width:34px;height:34px;border-radius:50%;border:3px solid #315bd6;opacity:0;transform:scale(.4)}
#film-cursor.click i{animation:filmring .5s ease-out}
@keyframes filmring{0%{opacity:.9;transform:scale(.4)}100%{opacity:0;transform:scale(1.6)}}`;
const INIT = `(() => {
  const css = ${JSON.stringify(CSS)};
  const strip = n => { if (n.nodeType === 3 && n.nodeValue.includes(' · replay mode')) n.nodeValue = n.nodeValue.replace(' · replay mode', ''); };
  const walk = root => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) strip(n); };
  addEventListener('DOMContentLoaded', () => {
    const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s);
    const c = document.createElement('div'); c.id = 'film-cursor';
    c.innerHTML = '<i></i><svg viewBox="0 0 26 32" width="26" height="32"><path d="M2 2 L2 26 L8.5 20 L13 30 L17.5 28 L13 18.5 L22 18.5 Z" fill="#fff" stroke="#0a0e28" stroke-width="2" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    addEventListener('mousemove', e => { c.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; }, true);
    addEventListener('mousedown', () => { c.classList.remove('click'); void c.offsetWidth; c.classList.add('click'); }, true);
    walk(document.body);
    new MutationObserver(ms => ms.forEach(m => { if (m.type === 'characterData') strip(m.target); m.addedNodes.forEach(n => n.nodeType === 3 ? strip(n) : n.nodeType === 1 && walk(n)); }))
      .observe(document.body, {subtree: true, childList: true, characterData: true});
  });
})();`;

(async () => {
  fs.rmSync(OUT, {recursive: true, force: true}); fs.mkdirSync(path.join(OUT, 'frames'), {recursive: true});
  const browser = await chromium.launch();
  const context = await browser.newContext({viewport: {width: W, height: H}, deviceScaleFactor: DPR});
  await context.addInitScript(INIT);
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  const frames = [], markers = {};
  let n = 0, writing = Promise.resolve();
  cdp.on('Page.screencastFrame', ({data, metadata, sessionId}) => {
    const file = `${String(n++).padStart(6, '0')}.jpg`;
    frames.push({file, t: metadata.timestamp});
    writing = writing.then(() => fs.promises.writeFile(path.join(OUT, 'frames', file), Buffer.from(data, 'base64')));
    cdp.send('Page.screencastFrameAck', {sessionId}).catch(() => {});
  });
  const mark = (name, extra) => { markers[name] = {t: Date.now() / 1000, ...(extra || {})}; console.log('mark', name); };
  const box = async sel => { const b = await page.locator(sel).first().boundingBox(); return b && {x: b.x + b.width / 2, y: b.y + b.height / 2, w: b.width, h: b.height}; };
  let mouse = {x: W * .62, y: H * .72};
  const moveTo = async (x, y, ms = 650) => { const steps = Math.max(8, Math.round(ms / 16)); await page.mouse.move(x, y, {steps}); mouse = {x, y}; };
  const clickAt = async (sel, ms) => { const b = await box(sel); await moveTo(b.x, b.y, ms); await sleep(140); await page.mouse.down(); await sleep(90); await page.mouse.up(); return b; };
  const scroller = '[data-testid=conversation]';
  // Smooth, eased scroll of the conversation so the camera move reads as a person scrolling.
  const scrollTo = async (target, ms) => page.evaluate(([sel, target, ms]) => new Promise(done => {
    const el = document.querySelector(sel), from = el.scrollTop, to = Math.max(0, Math.min(target, el.scrollHeight - el.clientHeight)), t0 = performance.now();
    const step = now => { const p = Math.min(1, (now - t0) / ms), e = p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; el.scrollTop = from + (to - from) * e; p < 1 ? requestAnimationFrame(step) : done(); };
    requestAnimationFrame(step);
  }), [scroller, target, ms]);
  const offsetOf = async (sel, pad = 16) => page.evaluate(([sel, scroller, pad]) => {
    const el = document.querySelector(sel), sc = document.querySelector(scroller);
    return el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - pad;
  }, [sel, scroller, pad]);
  const waitRun = async (index, timeout = 180000) => page.waitForSelector(`[data-testid=run-card]:nth-of-type(1) >> nth=0`, {timeout}).then(() =>
    page.waitForFunction(i => { const c = document.querySelectorAll('[data-testid=run-card]')[i]; return c && ['succeeded', 'failed'].includes(c.getAttribute('data-status')); }, index, {timeout, polling: 100}));

  // Sign in before recording, then start on a clean home screen.
  await page.goto(BASE + '/'); await page.getByTestId('login-u-ceo').click();
  await page.getByTestId('empty-state').waitFor();
  await page.goto(BASE + '/'); await page.getByTestId('empty-state').waitFor();
  await page.mouse.move(mouse.x, mouse.y);
  await cdp.send('Page.startScreencast', {format: 'jpeg', quality: 92, maxWidth: W * DPR, maxHeight: H * DPR, everyNthFrame: 1});
  mark('start');
  await sleep(1200);
  const ask = async (question, withFile) => {
    if (withFile) {
      const b = await clickAt('[data-testid=attach]', 800).catch(() => null);
      mark('attach_click', b);
      await page.getByTestId('file-input').setInputFiles(TARGETS);
      await page.waitForFunction(() => !document.querySelector('button[type=submit][aria-label^="Wait until"]'), null, {timeout: 20000});
      await sleep(700); mark('attached');
    }
    const t = await clickAt('textarea[aria-label="Your question"]', 700); mark('type_start', t);
    for (const ch of question) { await page.keyboard.type(ch); await sleep(38 + Math.random() * 40); }
    mark('type_end'); await sleep(450);
    const s = await clickAt('button[type=submit][aria-label=Send]', 500); mark('sent', s);
    await moveTo(W * .82, H * .55, 900);
  };
  const runCount = async () => page.locator('[data-testid=run-card]').count();
  if (take === 'quarter') {
    await sleep(1600); // the wizard waves hello
    await ask(QUARTER, true);
    await waitRun(0); mark('done');
    await sleep(3200); mark('settled');
    const card = '[data-testid=run-card]';
    await scrollTo(await offsetOf(card, 24), 1400); mark('top');
    await sleep(1800);
    await scrollTo(await offsetOf(`${card} table`, 120), 1600); mark('table');
    await sleep(1800);
    await scrollTo(1e6, 1400); mark('bottom'); await sleep(900);
    const c = await clickAt('[data-testid=check-my-data]', 800); mark('check_click', c);
    await moveTo(W * .86, H * .6, 900);
    await waitRun(1); mark('check_done');
    await sleep(2600); mark('check_settled');
    await scrollTo(await offsetOf('[data-testid=check-panel]', 140), 1400); mark('check_panel');
    await sleep(2200);
    await scrollTo(await offsetOf(card, 24), 1600); mark('checked_badge');
    await sleep(2000);
    await scrollTo(await offsetOf(`${card} table`, 120), 1200); await sleep(500);
    const e = await clickAt(`${card} table [data-testid=citation]`, 800); mark('citation_click', e);
    await page.getByTestId('evidence-drawer').waitFor(); await moveTo(W * .3, H * .7, 700); mark('evidence');
    await sleep(3200);
  } else if (take === 'stock') {
    // A fresh analysis, the first one listed in the sidebar; then its check and sources, in the same window.
    await sleep(800);
    await ask(STOCK, false);
    await waitRun(0); mark('done');
    await sleep(3200); mark('settled');
    const card = '[data-testid=run-card]';
    await scrollTo(await offsetOf(card, 24), 1400); mark('top');
    await sleep(2400);
    await scrollTo(1e6, 1600); mark('bottom'); await sleep(900);
    const c = await clickAt('[data-testid=check-my-data]', 800); mark('check_click', c);
    await moveTo(W * .86, H * .6, 900);
    await waitRun(1); mark('check_done');
    await sleep(2600); mark('check_settled');
    await scrollTo(await offsetOf('[data-testid=check-panel]', 140), 1400); mark('check_panel');
    await sleep(2200);
    await scrollTo(await offsetOf(`${card} [data-testid=citation]`, 300), 1400); await sleep(500);
    const e = await clickAt(`${card} [data-testid=citation]`, 800); mark('citation_click', e);
    await page.getByTestId('evidence-drawer').waitFor(); await moveTo(W * .3, H * .7, 700); mark('evidence');
    await sleep(3200);
  }
  mark('end');
  await cdp.send('Page.stopScreencast'); await writing;
  const runs = await page.evaluate(async () => {
    const conv = await (await fetch('/api/v1/conversations')).json();
    const id = conv.conversations[0].id; return (await (await fetch('/api/v1/conversations/' + id)).json()).runs.map(r => ({id: r.id, kind: r.kind, status: r.status, events: r.events.map(e => ({type: e.type, ts: e.ts, label: e.payload?.label, name: e.payload?.name}))}));
  });
  fs.writeFileSync(path.join(OUT, 'frames.json'), JSON.stringify({width: W * DPR, height: H * DPR, frames}, null, 0));
  fs.writeFileSync(path.join(OUT, 'markers.json'), JSON.stringify({markers, runs}, null, 1));
  console.log(`${take}: ${frames.length} frames over ${(frames.at(-1).t - frames[0].t).toFixed(1)} s`);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
