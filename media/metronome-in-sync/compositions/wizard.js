// Wizard scenes: recreated from the Wizard web app (apps/web/src) in its light B2B style, with synthetic content.
export const wizardMark = `<svg viewBox="0 0 32 32" aria-label="Wizard"><rect width="32" height="32" rx="8" fill="#315bd6"/><path d="M9 10l3 12 4-9 4 9 3-12" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const lucidePaths = {
  megaphone: '<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
  packageSearch: '<path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l2-1.14"/><path d="m7.5 4.27 9 5.15M3.29 7 12 12l8.71-5M12 22V12"/><circle cx="18.5" cy="15.5" r="2.5"/><path d="M20.27 17.27 22 19"/>',
  repeat: '<path d="m2 9 3-3 3 3"/><path d="M13 18H7a2 2 0 0 1-2-2V6"/><path d="m22 15-3 3-3-3"/><path d="M11 6h6a2 2 0 0 1 2 2v10"/>',
  paperclip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  sparkles: '<path d="M9.94 15.5a2 2 0 0 0-1.44-1.44l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0L14.06 8.5a2 2 0 0 0 1.44 1.44l6.13 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/>',
  radio: '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
  dashed: '<circle cx="12" cy="12" r="9" stroke-dasharray="3.2 3.2"/>',
  badgeCheck: '<path d="M3.85 8.62a4 4 0 0 1 4.78-4.77 4 4 0 0 1 6.74 0 4 4 0 0 1 4.78 4.78 4 4 0 0 1 0 6.74 4 4 0 0 1-4.77 4.78 4 4 0 0 1-6.75 0 4 4 0 0 1-4.78-4.77 4 4 0 0 1 0-6.76Z"/><path d="m9 12 2 2 4-4"/>',
  shieldCheck: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  list: '<path d="M3 12h.01M3 18h.01M3 6h.01M8 12h13M8 18h13M8 6h13"/>',
  fileText: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8M16 13H8M16 17H8"/>',
  mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
  flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  messages: '<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/><path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>',
  plus: '<path d="M5 12h14M12 5v14"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  circleCheck: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  note: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M13 8H7M17 12H7"/>',
  calculator: '<rect width="16" height="20" x="4" y="2" rx="2"/><path d="M8 6h8M16 14v4M16 10h.01M12 10h.01M8 10h.01M12 14h.01M8 14h.01M12 18h.01M8 18h.01"/>',
  chart: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M7 16h8M7 11h12M7 6h3"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'
};
export const lu = (name, cls = '') => `<svg class="lu ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${lucidePaths[name]}</svg>`;
const badge = (tone, icon, label, attrs = '') => `<span class="wz-badge ${tone}" ${attrs}>${lu(icon)}${label}</span>`;

export const question = 'Which market gave us the best return on marketing investment last quarter?';

const stories = [
  ['Executive', 'megaphone', 'Which market gave us the best return on marketing investment last quarter — tie NERP spend to sell-through, share gain, and competitive switching, and rank them.'],
  ['Planner', 'packageSearch', 'Flag any model where sell-in is outpacing sell-out and installed-base growth is stalling — then check Smart Switch and app usage.'],
  ['Conquest', 'repeat', 'Where are we winning switchers from Apple and Xiaomi according to Smart Switch, and does our sell-out data support doubling down there?']
];

// Leaders' questions span every system; each card wires to the sources it needs.
export function questionsScene() {
  const asks = [
    ['Executive', 'megaphone', 'Which market gave us the best return on marketing investment last quarter?', [0, 1, 3]],
    ['Planner', 'packageSearch', 'Is any model’s sell-in outpacing sell-out?', [1, 3, 2]],
    ['Conquest', 'repeat', 'Where are we winning switchers from Apple and Xiaomi?', [2, 0, 3]]
  ];
  const systems = [['NERP', 'Marketing spend', 'website'], ['GSCM', 'Sell-in · sell-out', 'website'], ['ASAP', 'Share · switching', 'website'], ['PostgreSQL', 'Fresh from Metronome', 'db']];
  const cardX = [130, 700, 1270], tileX = [130, 556, 982, 1408], cardW = 520, tileW = 382;
  let serial = 0;
  const wires = asks.flatMap((a, i) => a[3].map(j => {
    const x1 = cardX[i] + cardW / 2 + (j - 1.5) * 34, y1 = 520, x2 = tileX[j] + tileW / 2 + (i - 1) * 40, y2 = 742;
    return `<path data-wire="${serial++}" data-card="${i}" pathLength="1" d="M${x1} ${y1}C${x1} ${y1 + 120} ${x2} ${y2 - 110} ${x2} ${y2}"/>`;
  })).join('');
  return `<h1 class="questions-title">One question, many systems.</h1>
<svg class="question-wires" viewBox="0 0 1920 1080" aria-hidden="true"><g fill="none" stroke="#7d93cf" stroke-width="3" stroke-linecap="round">${wires}</g></svg>
${asks.map((a, i) => `<div class="leader-question" data-question="${i}" style="left:${cardX[i]}px"><span class="wz-story">${lu(a[1])}${a[0]}</span><p>${a[2]}</p></div>`).join('')}
${systems.map((s, j) => `<div class="system-tile${j === 3 ? ' metronome-fed' : ''}" data-system="${j}" style="left:${tileX[j]}px"><span class="system-icon">${j === 3 ? lu('database') : `<b>${s[0][0]}</b>`}</span><div><b>${s[0]}</b><span>${s[1]}</span></div></div>`).join('')}`;
}

const steps = [
  {icon: 'database', text: 'Read NERP · Marketing spend by market, quarter and category <code>(fiscal_quarter=2026-Q2/2026-Q3)</code>', evidence: 'E1', meta: '7 rows · as of 2026-10-01', time: '0:03 · 1.4 s'},
  {icon: 'database', text: 'Queried PostgreSQL · <code>bi_reporting.psi_combined</code> — sell-out by market, 2026-Q2/2026-Q3', evidence: 'E2', meta: '8 rows · Live · read-only query', time: '0:06 · 0.8 s', live: true},
  {icon: 'database', text: 'Read ASAP · Smartphone market share by brand <code>(brand=Samsung)</code>', evidence: 'E3', meta: '8 rows · as of 2026-09-28', time: '0:09 · 1.2 s'},
  {icon: 'calculator', text: 'Calculated <code>(q3 - q2) / spend_musd</code> for EG, SA and AE', meta: '= 14,666.7 · 10,000 · 9,615.4', time: '0:10 · 0 ms'},
  {icon: 'chart', text: 'Prepared a bar: Investment-efficiency proxy, 2026-Q3', meta: 'V1 ready', time: '0:11 · 1 ms'}
];
const bars = [['EG', 14667, '14,667'], ['SA', 10000, '10,000'], ['AE', 9615, '9,615']];
const sql = `<span class="k">SELECT</span> market, fiscal_quarter,\n       <span class="k">SUM</span>(sell_out_qty) <span class="k">AS</span> sell_out_units\n<span class="k">FROM</span> bi_reporting.psi_combined\n<span class="k">WHERE</span> fiscal_quarter <span class="k">IN</span> (<span class="s">'2026-Q2'</span>, <span class="s">'2026-Q3'</span>)\n  <span class="k">AND</span> market <span class="k">IN</span> (<span class="s">'EG'</span>, <span class="s">'SA'</span>, <span class="s">'AE'</span>, <span class="s">'MA'</span>)\n<span class="k">GROUP BY</span> market, fiscal_quarter`;

// The layout audit measures full text boxes; truncated history titles and the drawer overlay are intentional.
const truncated = 'data-layout-allow-overflow data-layout-allow-overlap data-layout-allow-occlusion';
const history = (cls, text) => `<div class="wz-history ${cls}" ${truncated}>${text}</div>`;

export function wizardApp(cursor) {
  const side = `<aside class="wz-side"><div class="wz-brand"><span class="wz-tile">${wizardMark}</span><b>Wizard</b>${lu('plus', 'wz-new')}</div>
<div class="wz-tabs"><span class="on">${lu('messages')}Analyses</span><span>${lu('database')}Sources</span></div>
<div class="wz-search">${lu('search')}Search analyses</div>
<small class="wz-group wz-today">Today</small>${history('wz-today wz-current', question)}
<small class="wz-group">Previous 7 days</small>${['Sell-in vs sell-out by model, Gulf', 'Smart Switch wins from Apple, Q3', 'Z Flip8 launch stock cover'].map(text => history('', text)).join('')}
<div class="wz-account"><i>C</i><div><b>CEO Office</b><span><em></em>Gemini linked</span></div></div></aside>`;
  const empty = `<div class="wz-empty"><p class="wz-eyebrow">Wizard · executive analyst</p><h1>Every approved source.<br>One question away.</h1><p class="wz-lede">Wizard searches NERP, GSCM and ASAP, compares what it finds and shows every number with its source. You see each step it takes.</p>
<div class="wz-cards">${stories.map((s, i) => `<div class="wz-card" data-suggestion="${i}"><span class="wz-story">${lu(s[1])}${s[0]}</span><p>${s[2]}</p><span class="wz-ask">Ask this${lu('arrowRight')}</span></div>`).join('')}</div></div>`;
  const timeline = `<div class="wz-timeline"><div class="wz-timeline-head">${lu('shieldCheck')}<b>What Wizard did</b><span>· <i class="wz-step-count">0 steps</i> · consulted NERP, PostgreSQL, ASAP</span>${lu('chevron', 'wz-chevron')}</div>
<div class="wz-steps"><div class="wz-step wz-note" data-step="note">${lu('note')}<p>I'll measure return with an investment-efficiency proxy, then pull 2026-Q2 and Q3 spend, sell-out and share by market.</p></div>
${steps.map((s, i) => `<div class="wz-step" data-step="${i}">${lu(s.icon)}<div><p>${s.text}${lu('circleCheck', 'wz-ok')}${s.evidence ? `<span class="wz-eid">${s.evidence}</span>` : ''}</p><small>${s.live ? `<b class="wz-live-dot"></b>` : ''}${s.meta}</small></div><time>${s.time}</time></div>`).join('')}
<div class="wz-deciding"><i></i>Gemini is deciding the next step</div></div></div>`;
  const chart = `<div class="wz-chart"><div class="wz-chart-head"><div><b>Investment-efficiency proxy, 2026-Q3</b><span>Extra sell-out units (Q3 vs Q2) per USD 1M of Q3 marketing spend. Not ROI.</span></div><div class="wz-toggle"><span class="on">Chart</span><span>Data</span></div></div>
<div class="wz-bars">${bars.map((b, i) => `<div class="wz-bar-row" data-bar="${i}"><b>${b[0]}</b><div class="wz-track" style="--w:${(b[1] / 15000 * 100).toFixed(2)}%"><i></i><span>${b[2]} units/USD 1M</span></div></div>`).join('')}
<div class="wz-axis">${['0', '3k', '6k', '9k', '12k', '15k'].map(t => `<span>${t}</span>`).join('')}</div></div>
<p class="wz-prepared">Prepared by Gemini from <span class="wz-eid">E1</span><span class="wz-eid">E2</span><span class="wz-eid">E3</span> · Proxy = (Q3 sell-out − Q2 sell-out) / Q3 spend in USD millions.</p></div>`;
  const check = `<div class="wz-check"><b>Check my data · checked</b>${[['EG Q3 spend'], ['EG proxy'], ['SA proxy'], ['AE proxy']].map((r, i) => `<div data-match="${i}"><em>MATCH</em>${r[0]}</div>`).join('')}<div data-match="4" class="wz-rerun">E2 re-run: unchanged — the query returns the same rows today</div></div>`;
  const run = `<div class="wz-thread" data-layout-allow-overflow><div class="wz-user">${question}</div>
<article class="wz-run"><header>${'<b>Wizard</b>'}${badge('neutral', 'sparkles', 'gemini-3.8-flash')}${badge('live', 'radio', 'Live')}<span class="wz-check-state">${badge('muted wz-unchecked', 'dashed', 'Not checked')}${badge('ok wz-checked', 'badgeCheck', 'Checked')}</span><time class="wz-elapsed">0:00</time></header>
${timeline}
<div class="wz-answer"><p><strong>EG ranks first on an investment-efficiency proxy for 2026-Q3, ahead of SA and AE.</strong> MA cannot be ranked because no Q3 spend is posted in NERP. This is a proxy, not proven ROI.</p>${chart}</div>
<footer class="wz-actions"><span class="wz-btn wz-check-btn">${lu('shieldCheck')}Check my data</span><span class="wz-btn wz-sources-btn">${lu('list')}Show sources (3)</span><span class="wz-btn">${lu('fileText')}Report</span><span class="wz-btn">${lu('link')}</span><span class="wz-btn">${lu('mail')}Email</span><span class="wz-btn wz-feedback">${lu('flag')}Feedback</span></footer>
${check}</article></div>`;
  const composer = `<div class="wz-composer-wrap"><div class="wz-composer">${lu('paperclip')}<span class="wz-input"><span class="wz-placeholder">Ask about markets, models, spend, share or switching</span><span class="wz-typed"></span><i class="wz-caret"></i></span><span class="wz-send">${lu('arrowUp')}</span></div><p>Check the evidence before acting on a number.</p></div>`;
  const drawer = `<div class="wz-scrim" data-layout-ignore></div><aside class="wz-drawer" data-layout-allow-overlap><header><div><b>Sources and evidence</b><span>Every number Wizard cites comes from one of these retrievals. Source text is data, never instructions.</span></div>${lu('close')}</header>
<section class="wz-evidence"><h3><span class="wz-eid">E2</span>Sell-out by market and quarter${badge('live', 'radio', 'Live')}</h3>
<dl><dt>Source</dt><dd>PostgreSQL · <code>bi_reporting.psi_combined</code></dd><dt>Retrieved</dt><dd>9 Oct 2026, 08:12 · read-only transaction, rolled back</dd><dt>Rows</dt><dd>8 shown of 8 · digest 4be19c07a2</dd></dl>
<pre class="wz-sql">${sql}</pre><span class="wz-pill">A live query Gemini wrote</span></section>
<section class="wz-evidence wz-evidence-next"><h3><span class="wz-eid">E1</span>Marketing spend by market, quarter and category</h3><dl><dt>Source</dt><dd>NERP › Marketing · <code>nerp-mkt-spend-quarterly</code></dd></dl></section></aside>`;
  return `<div class="wz">${side}<div class="wz-main"><header class="wz-head"><b class="wz-title"><span class="wz-title-new">New analysis</span><span class="wz-title-q">${question}</span></b>${badge('neutral', 'sparkles', 'gemini-3.8-flash')}${badge('live', 'radio', 'Live')}</header>
<div class="wz-stage">${empty}${run}${composer}</div></div>${drawer}<div class="wz-cursor">${cursor}<i></i></div></div>`;
}

// Closing: portals → Metronome → PostgreSQL → Wizard → answer, then the paired lockup.
export function closingScene(mark) {
  const node = (cls, inner) => `<div class="close-node ${cls}">${inner}</div>`;
  return `<div class="close-diagram"><svg class="close-links" viewBox="0 0 1920 1080" aria-hidden="true"><g fill="none" stroke="#cbd6d6" stroke-width="3"><path d="M290 380H420M290 520H420"/><path d="M750 450H850"/><path d="M1110 450H1210"/><path d="M1540 450H1620"/></g><g class="close-dashes" fill="none" stroke-width="5" stroke-linecap="round" stroke-dasharray="8 70"><path data-close-link="0" stroke="#0d7377" d="M290 380H420M290 520H420"/><path data-close-link="1" stroke="#0d7377" d="M750 450H850"/><path data-close-link="2" stroke="#315bd6" d="M1110 450H1210"/><path data-close-link="3" stroke="#315bd6" d="M1540 450H1620"/></g></svg>
${node('close-portal close-asap', '<b>ASAP</b><span>Reports</span>')}${node('close-portal close-gscm', '<b>GSCM</b><span>Reports</span>')}
${node('close-hub close-metronome', `${mark}<h2>Metronome</h2><p>Build · Schedule · Track</p>`)}
${node('close-db', `${lu('database')}<b>PostgreSQL</b><span>Fresh every morning</span>`)}
${node('close-hub close-wizard', `${wizardMark}<h2>Wizard</h2><p>Ask · Trace · Check</p>`)}
${node('close-answer', `<b>EG ranks first</b><span>${lu('badgeCheck')}Checked</span><span>${lu('radio')}Live</span>`)}</div>
<div class="close-lockup"><span class="logo">${mark}<b>Metronome</b></span><em>+</em><span class="logo wizard-logo">${wizardMark}<b>Wizard</b></span></div>`;
}
