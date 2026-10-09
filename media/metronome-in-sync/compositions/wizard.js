// Wizard film scenes. The app is a simplified presentation of Wizard (apps/web/src): its mark, cobalt accent and Inter type,
// with the controls a non-technical audience does not need left out. All content is synthetic.
export const wizardMark = `<svg viewBox="0 0 32 32" aria-label="Wizard"><rect width="32" height="32" rx="8" fill="#315bd6"/><path d="M9 10l3 12 4-9 4 9 3-12" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const lucidePaths = {
  arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  sparkles: '<path d="M9.94 15.5a2 2 0 0 0-1.44-1.44l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0L14.06 8.5a2 2 0 0 0 1.44 1.44l6.13 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  circleCheck: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 13H8M16 17H8"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  trendUp: '<path d="M16 7h6v6"/><path d="m22 7-8.5 8.5-5-5L2 17"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>'
};
export const lu = (name, cls = '') => `<svg class="lu ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${lucidePaths[name]}</svg>`;

// Every place an answer can hide.
export const sources = [
  {key: 'asap', name: 'ASAP', sub: 'Sales & market', color: '#1d4f91', glyph: 'A'},
  {key: 'gscm', name: 'GSCM', sub: 'Supply chain', color: '#6551a8', glyph: 'G'},
  {key: 'bdp', name: 'BDP', sub: 'Big Data Portal', color: '#0e7490', glyph: 'B'},
  {key: 'nerp', name: 'NERP', sub: 'Finance & planning', color: '#9a3412', glyph: 'N'},
  {key: 'excel', name: 'Excel', sub: 'Spreadsheets', color: '#107c41', glyph: 'X'},
  {key: 'email', name: 'Email', sub: 'Inbox', color: '#2563eb', glyph: '@'},
  {key: 'ppt', name: 'PowerPoint', sub: 'Presentations', color: '#c2410c', glyph: 'P'},
  {key: 'pdf', name: 'PDF', sub: 'Documents', color: '#b91c1c', glyph: 'PDF'}
];
const badge = s => `<i class="src-badge${s.glyph.length > 1 ? ' wide' : ''}" style="--c:${s.color}">${s.glyph}</i>`;

const reportNames = {
  asap: ['Sell-out by country', 'Market share by brand', 'Weekly sales summary', 'Price tracker', 'Model mix by channel', 'Smart Switch transfers', 'Retail coverage', 'Promotion results', 'Daily sales flash', 'Competitor launches', 'Sales by price band', 'Top models by market', 'Dealer performance', 'Online vs offline sales'],
  gscm: ['Sell-in by account', 'Stock by warehouse', 'Stock aging', 'Shipments in transit', 'Forecast vs actual', 'Open orders', 'Allocation status', 'Weeks of cover', 'Inbound schedule', 'Backorders by model', 'Supply plan', 'Returns by reason', 'Port arrivals', 'Safety stock'],
  bdp: ['Device activations', 'Customer activity', 'App usage', 'Service visits', 'Campaign reach', 'Web traffic', 'Loyalty members', 'Trade-in volumes', 'Warranty claims', 'Survey results', 'Store footfall', 'Repeat buyers', 'Search trends', 'Social mentions'],
  nerp: ['Marketing spend', 'Budget vs actual', 'Purchase orders', 'Invoices', 'Cost centers', 'Payments due', 'Accruals', 'Spend by campaign', 'Vendor master', 'Profit by market', 'Expense claims', 'FX rates', 'Capex tracker', 'Credit notes']
};
const files = [
  ['excel', 'Q3 targets.xlsx', 'Excel workbook'], ['email', 'RE: Kuwait stock levels', 'Email · 14 replies'], ['ppt', 'Q2 business review.pptx', 'PowerPoint · 48 slides'], ['pdf', 'Channel plan 2026.pdf', 'PDF · 36 pages'],
  ['excel', 'Weekly stock – Gulf.xlsx', 'Excel workbook'], ['email', 'FW: Promo results UAE', 'Email · 3 attachments'], ['ppt', 'Launch recap.pptx', 'PowerPoint · 22 slides'], ['pdf', 'Price list September.pdf', 'PDF · 12 pages']
];
export const fileSlots = [[120, 780, -2], [560, 794, 1.5], [1000, 778, -1], [1440, 792, 2], [300, 890, 1], [740, 902, -1.5], [1180, 888, 2], [1560, 900, -1]];
export const windowSlots = [[60, 128], [505, 168], [950, 116], [1395, 160]];

// 1. Too many places to look.
export function messScene() {
  const windows = sources.slice(0, 4).map((s, i) => {
    // The lists scroll past their window edge and sit under the next window in the cascade on purpose.
    const layered = 'data-layout-allow-occlusion data-layout-allow-overlap';
    const rows = [...reportNames[s.key], ...reportNames[s.key]].map(r => `<div class="report-row" ${layered}>${lu('file')}<span ${layered}>${r}</span></div>`).join('');
    return `<div class="portal-win" data-portal="${i}" style="left:${windowSlots[i][0]}px;top:${windowSlots[i][1]}px;--c:${s.color}"><header>${badge(s)}<div><b>${s.name}</b><span>${s.sub}</span></div></header><div class="report-list" data-layout-allow-overflow><div class="report-scroll">${rows}</div><i class="list-thumb"></i></div></div>`;
  }).join('');
  const fileCards = files.map((f, i) => {
    const s = sources.find(x => x.key === f[0]);
    return `<div class="file-card" data-file="${i}" style="left:${fileSlots[i][0]}px;top:${fileSlots[i][1]}px;--r:${fileSlots[i][2]}deg">${badge(s)}<div><b>${f[1]}</b><span>${f[2]}</span></div></div>`;
  }).join('');
  return `${windows}${fileCards}<div class="hunt-question">${lu('search')}<span>Which market grew the most this quarter?</span></div><div class="hunt-clock">${lu('clock')}<span>0 min</span></div><div class="hunt-pointer"><svg class="pointer" viewBox="0 0 40 48"><path d="M4 3 33 28 21 30 27 43 20 46 14 32 4 41Z" fill="#fff" stroke="#172d38" stroke-width="2.5"/></svg></div>`;
}

// 2. Everything comes together in Wizard.
// Portals gather on the left, files on the right, both flowing into Wizard; the space under the hub stays clear for its name.
export const ringCenter = [960, 500];
export const ringSlots = sources.map((_, i) => [i < 4 ? 330 : 1590, [270, 423, 577, 730][i % 4]]);
export function joinScene() {
  const [cx, cy] = ringCenter;
  const lines = ringSlots.map(([x, y], i) => { const k = x < cx ? 1 : -1; return `<path data-join-line="${i}" d="M${x} ${y}C${x + k * 300} ${y} ${cx - k * 280} ${cy} ${cx} ${cy}"/>`; }).join('');
  const nodes = sources.map((s, i) => `<div class="join-node" data-node="${i}">${badge(s)}<div><b>${s.name}</b><span>${s.sub}</span></div></div>`).join('');
  return `<svg class="join-lines" viewBox="0 0 1920 1080" aria-hidden="true"><g class="join-base" fill="none" stroke="#d5dcef" stroke-width="2.5">${lines}</g><g class="join-flow" fill="none" stroke="#315bd6" stroke-width="5" stroke-linecap="round" stroke-dasharray="6 46">${lines}</g></svg>
${nodes}<div class="join-hub"><div class="hub-glow"></div>${wizardMark}</div><div class="join-name">Wizard</div><div class="join-ai">${lu('cpu')}Local AI</div>`;
}

// 3. The app: ask, find, answer, trust.
export const question = 'How did we do in the Gulf this quarter?';
const found = [
  ['gscm', 'Sell-out by country', 'GSCM'], ['asap', 'Market share by brand', 'ASAP'], ['nerp', 'Marketing spend', 'NERP'], ['excel', 'Q3 targets.xlsx', 'Excel']
];
export const bars = [['KSA', 452, 'Saudi Arabia'], ['UAE', 305], ['Kuwait', 148], ['Qatar', 126], ['Oman', 112], ['Bahrain', 97]];
export const share = [['Samsung', 31.4, '#315bd6'], ['Apple', 27, '#64748b'], ['Xiaomi', 17, '#0891b2'], ['Others', 24.6, '#cbd5e1']];
const kpis = [
  ['Sell-out', '1.24M', 'units', '▲ 8% vs Q2', 'up', 'GSCM'], ['Market share', '31.4%', '', '▲ 1.2 pts', 'up', 'ASAP'],
  ['Marketing spend', '$8.3M', '', 'On plan', 'flat', 'NERP'], ['Stock cover', '5.1', 'weeks', 'Kuwait 7.4 weeks', 'warn', 'GSCM']
];
export function appScene(cursor) {
  const chips = sources.map(s => `<span class="src-chip">${badge(s)}${s.name}</span>`).join('');
  const home = `<div class="wz-home"><div class="home-mark">${wizardMark}</div><p class="home-hello">Good morning</p><h1>What would you like to know?</h1>
<div class="ask-box">${lu('sparkles', 'ask-spark')}<span class="ask-input"><span class="ask-placeholder">Ask anything about our business</span><span class="ask-typed"></span><i class="ask-caret"></i></span><span class="ask-send">${lu('arrowUp')}</span></div>
<p class="home-sources-label">Connected sources</p><div class="home-sources">${chips}</div></div>`;
  const tiles = sources.map((s, i) => `<div class="find-tile" data-tile="${s.key}">${badge(s)}<div><b>${s.name}</b><span>${s.sub}</span></div><i class="tile-scan" data-layout-allow-overflow></i>${lu('circleCheck', 'tile-ok')}</div>`).join('');
  const foundCards = found.map((f, i) => {
    const s = sources.find(x => x.key === f[0]);
    return `<div class="found-card" data-found="${i}">${badge(s)}<div><b>${f[1]}</b><span>${f[2]}</span></div>${lu('check', 'found-ok')}</div>`;
  }).join('');
  const finding = `<div class="wz-find"><div class="find-status"><i class="find-spinner"></i><div><b class="find-title">Finding the right reports…</b><span>Searching ${sources.length} connected sources</span></div></div>
<div class="find-grid">${tiles}</div><div class="find-panel"><h3>Reports found</h3>${foundCards}</div></div>`;
  const max = 500, plotH = 228;
  const barSvg = bars.map(([c, v], i) => {
    const h = v / max * plotH, x = 46 + i * 140;
    return `<g data-bar="${i}"><rect class="bar" x="${x}" y="${300 - h}" width="84" height="${h}" rx="8"/><text class="bar-value" x="${x + 42}" y="${288 - h}">${v}k</text><text class="bar-label" x="${x + 42}" y="332">${c}</text></g>`;
  }).join('');
  let offset = 0;
  const slices = share.map(([n, v, c], i) => { const el = `<circle data-slice="${i}" cx="160" cy="160" r="112" pathLength="100" stroke="${c}" stroke-dasharray="0 100" stroke-dashoffset="${-offset}" data-len="${v}" data-off="${offset}"/>`; offset += v; return el; }).join('');
  const legend = share.map(([n, v, c], i) => `<div class="legend-row" data-legend="${i}"><i style="background:${c}"></i><span>${n}</span><b>${v}%</b></div>`).join('');
  const kpiCards = kpis.map((k, i) => `<div class="kpi" data-kpi="${i}"><span class="kpi-label">${k[0]}</span><b>${k[1]}<small>${k[2]}</small></b><span class="kpi-delta ${k[4]}">${k[3]}</span><span class="kpi-src">${lu('check')}${k[5]}</span></div>`).join('');
  const sourceTag = (s, r) => `<span class="chart-src">${lu('circleCheck')}From ${s} · ${r}</span>`;
  const dash = `<div class="wz-dash"><div class="dash-used">${lu('circleCheck')}Answered from 4 reports · GSCM, ASAP, NERP and Excel</div>
<div class="dash-head"><h2>Gulf performance · Q3 2026</h2><span class="dash-checked">${lu('shield')}Every number checked</span></div>
<p class="dash-summary"><b>A strong quarter.</b> Sell-out rose <b>8%</b> to 1.24 million units, led by Saudi Arabia, and market share climbed to <b>31.4%</b>. One watch-out: Kuwait is holding over seven weeks of stock.</p>
<div class="kpi-row">${kpiCards}</div>
<div class="chart-row"><section class="chart-card bar-card"><h3>Sell-out by country <span>thousand units</span></h3><svg viewBox="0 0 880 350">${[0, 1, 2, 3, 4].map(g => `<line class="grid" x1="20" x2="870" y1="${300 - g * 57}" y2="${300 - g * 57}"/>`).join('')}${barSvg}</svg><span class="bar-callout">${lu('trendUp')}+12% vs Q2</span>${sourceTag('GSCM', 'Sell-out by country')}</section>
<section class="chart-card pie-card"><h3>Market share <span>Q3 2026</span></h3><div class="pie-wrap"><svg viewBox="0 0 320 320"><g transform="rotate(-90 160 160)" fill="none" stroke-width="56">${slices}</g></svg><div class="pie-center"><b>31.4%</b><span>Samsung</span></div></div><div class="legend">${legend}</div>${sourceTag('ASAP', 'Market share by brand')}</section></div></div>`;
  const secure = `<div class="secure-card"><b>${lu('shield')}Secure by design</b><div>${lu('cpu')}Authorized AI, approved by the company</div><div>${lu('eye')}Shows only what you're allowed to see</div><div>${lu('lock')}Read-only: it never changes your data</div></div>`;
  return `<div class="app-window"><header class="app-bar"><span class="app-brand">${wizardMark}<b>Wizard</b></span><span class="app-secure">${lu('shield')}Authorized local AI</span><span class="app-user">CEO</span></header>
<div class="app-body"><div class="wz-user">${question}</div>${home}${finding}${dash}${secure}<div class="app-pointer">${cursor}<i></i></div></div></div>`;
}

// 4. End card.
export function endScene() {
  const chips = sources.map((s, i) => `<span class="end-chip" data-end-chip="${i}">${badge(s)}${s.name}</span>`).join('');
  return `<div class="end-glow"></div><div class="end-lockup"><span class="end-mark">${wizardMark}</span><b>Wizard</b></div><p class="end-tagline">Every source. One answer.</p><div class="end-sources">${chips}</div>`;
}
