import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../app/static/flow_run_log.js', import.meta.url), 'utf8');
const elements = {
    'python-console-lines': {innerHTML: '', scrollTop: 0, scrollHeight: 150},
    'python-console-count': {textContent: ''},
};
const requested = [];
const responses = [
    {lines: [{line_no: 1, stream: 'stdout', text: 'hello <world>', at: '2026-09-24T08:00:00Z'},
             {line_no: 4, stream: 'stderr', text: '::stage::Loading', at: '2026-09-24T08:00:01Z'}], omitted: 2},
    {lines: [{line_no: 5, stream: 'stdout', text: 'finished', at: '2026-09-24T08:00:02Z'}], omitted: 2},
];
const context = {
    location: {pathname: '/flow-runs/91'},
    document: {getElementById: id => elements[id] || null},
    fetch: async path => {
        requested.push(path);
        return {ok: true, json: async () => responses.shift() || {lines: [], omitted: 2}};
    },
};
vm.createContext(context);
vm.runInContext(source.slice(0, source.indexOf('function render(run)')), context);
await context.loadOutput();
assert.match(requested[0], /\/runs\/91\/output\?after_line=0&limit=1000/);
assert.equal(elements['python-console-lines'].scrollTop, 150);
assert.match(elements['python-console-lines'].innerHTML, /hello &lt;world&gt;/);
assert.match(elements['python-console-lines'].innerHTML, /… 2 lines omitted …/);
assert.match(elements['python-console-lines'].innerHTML, /python-console-line stderr marker/);
assert.match(elements['python-console-count'].textContent, /2 omitted/);
vm.runInContext('consoleState.follow = false', context);
elements['python-console-lines'].scrollTop = 25;
await context.loadOutput();
assert.match(requested[1], /after_line=4&limit=1000/);
assert.equal(elements['python-console-lines'].scrollTop, 25);
assert.match(elements['python-console-lines'].innerHTML, /finished/);
console.log('flow run log incremental console tests passed');
