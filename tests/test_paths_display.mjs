import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const state = {flows_root: '/flows', source: 'default', default: '/flows', enforced: false,
    source_folders: [{name:'ASAP',path:'/flows/ASAP'}], flows_outside_root: [{name:'<bad>',reason:'Outside',target_folder:'/old'}]};
const context = {api: async () => state, esc: s => String(s ?? '').replaceAll('<','&lt;')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('// ── System > Paths ──'), source.indexOf('// ── Router ──')), context);
const html = await context.renderPaths();
assert.match(html, /id="paths-form"/);
assert.match(html, /&lt;bad>/);
assert.doesNotMatch(html, /id="paths-enforced" checked/);
assert.match(html, /does not move files/);
console.log('paths display tests passed');
