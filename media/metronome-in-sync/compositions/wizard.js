// Wizard film scenes. Sections 1, 2 and 4 are drawn for the film; section 3 is the real Wizard web app, recorded
// (capture/) and cut into clips by scripts/screen.py. The logo is Wizard's own pixel art (the Wizard repository's brand/logo),
// copied unchanged into assets/brand. All data is synthetic.
import fs from 'node:fs';
const svg = name => fs.readFileSync(new URL(`../assets/brand/${name}.svg`, import.meta.url), 'utf8').replace(/<title>.*?<\/title>/, '').replace('<svg ', '<svg aria-hidden="true" ');
export const wizardIcon = svg('wizard-app-icon');
export const wizardLogo = svg('wizard-logo');

const lucidePaths = {
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 13H8M16 17H8"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  fastForward: '<path d="M13 19V5l9 7z"/><path d="M2 19V5l9 7z"/>',
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
${nodes}<div class="join-hub"><div class="hub-glow"></div>${wizardIcon}</div><div class="join-name">Wizard</div><div class="join-ai">${lu('cpu')}Local AI</div>`;
}

// 3. The real Wizard app. Each clip plays in its own window; compositions/runtime.js moves the camera over it.
export function screenScene(clips) {
  const windows = Object.entries(clips).map(([id, c], i) => `<div class="screen-win" data-clip="${id}"><video id="clip-${id}" src="${c.file}" muted playsinline preload="auto" data-start="${c.start}" data-duration="${Math.round((c.end - c.start) * 1000) / 1000}" data-track-index="${2 + i}"></video></div>`).join('');
  const safe = `<div class="safe-card"><b>${lu('shield')}Secure by design</b><div>${lu('cpu')}Authorized AI, approved by the company</div><div>${lu('eye')}Shows only what you're allowed to see</div><div>${lu('lock')}Read-only: it never changes your data</div></div>`;
  return `<div class="hub-echo">${wizardIcon}</div>${windows}<div class="screen-dim"></div>${safe}
<div class="tl-edge"></div><div class="tl-pill">${lu('fastForward')}<b>Timelapse</b><i></i><span class="tl-speed">2×</span><i></i><span class="tl-clock">0:00</span></div><div class="real-tag">Real Wizard app · illustrative data</div>`;
}

// 4. End card: Wizard's logo, the tagline and every source.
export function endScene() {
  const chips = sources.map((s, i) => `<span class="end-chip" data-end-chip="${i}">${badge(s)}${s.name}</span>`).join('');
  return `<div class="end-glow"></div><div class="end-lockup">${wizardLogo}</div><p class="end-tagline">Every source. One answer.</p><div class="end-sources">${chips}</div>`;
}
