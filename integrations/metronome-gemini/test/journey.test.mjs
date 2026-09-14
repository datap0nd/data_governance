import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

test('complete synthetic flow journey through real HTTP and MCP transports', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'metronome-journey-'));
  let flow = null, saves = 0, runs = 0;
  const app = createServer(async (req, res) => {
    let input = '';
    for await (const chunk of req) input += chunk;
    const body = input ? JSON.parse(input) : {};
    const send = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (req.url === '/api/version') return send({ version: 'synthetic' });
    if (req.url === '/api/flows/catalog') return send({ sites: [{ id: 1, name: 'Fictional ASAP' }], reports: [{ id: 15, site_id: 1, name: '출장비', filters: [] }] });
    if (req.url === '/openapi.json') return send({ components: { schemas: { FlowWrite: { type: 'object', properties: { name: { type: 'string' } } } } } });
    if (req.url === '/api/flows' && req.method === 'POST') { saves++; flow = { id: 42, ...body }; return send(flow); }
    if (req.url === '/api/flows') return send(flow ? [flow] : []);
    if (req.url === '/api/flows/42') return send(flow);
    if (req.url === '/api/flows/42/run' && req.method === 'POST') { runs++; return send({ id: 9, status: 'queued' }); }
    if (req.url === '/api/flows/runs/9') return send({ id: 9, flow_id: 42, status: 'succeeded', row_count: 3, files: [], job: { password: 'MUST_NOT_LEAK' } });
    res.statusCode = 404; send({ detail: 'not found' });
  });
  await new Promise(resolve => app.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'synthetic-owner-client', version: '1' });
  try {
    await client.connect(new StdioClientTransport({ command: process.execPath,
      args: [fileURLToPath(new URL('../server.mjs', import.meta.url))], stderr: 'pipe',
      env: { PATH: process.env.PATH || '', METRONOME_BASE_URL: `http://127.0.0.1:${app.address().port}`, METRONOME_PROPOSAL_DIR: directory } }));
    const call = async (name, args = {}) => {
      const result = await client.callTool({ name, arguments: args });
      assert.ok(!result.isError, result.content[0].text);
      return JSON.parse(result.content[0].text);
    };
    assert.equal((await call('get_version')).version, 'synthetic');
    assert.equal((await call('list_catalog')).reports[0].name, '출장비');
    await call('get_flow_schema');
    const p = await call('propose_flow', { definition_json: JSON.stringify({ name: '출장비', source_type: 'portal', site_id: 1, report_id: 15 }) });
    assert.equal(saves, 0);
    assert.equal(p.definition.enabled, false);
    const saved = await call('apply_flow_proposal', { proposal_id: p.proposal_id, confirmation_json: p.confirmation_json });
    assert.equal(saved.flow_id, 42);
    await call('apply_flow_proposal', { proposal_id: p.proposal_id, confirmation_json: p.confirmation_json });
    assert.equal(saves, 1);
    assert.equal((await call('get_flow', { flow_id: 42 })).name, '출장비');
    const run = await call('propose_run', { flow_id: 42, request_id: 'fictional-cycle-1' });
    const queued = await call('run_flow', { proposal_id: run.proposal_id, confirmation_json: run.confirmation_json });
    assert.equal(queued.status, 'queued');
    const result = await call('get_run', { run_id: queued.id });
    assert.equal(result.status, 'succeeded');
    assert.ok(!JSON.stringify(result).includes('MUST_NOT_LEAK'));
    assert.equal(runs, 1);
    const writeTools = (await client.listTools()).tools.filter(t => ['run_flow', 'apply_flow_proposal'].includes(t.name));
    assert.ok(writeTools.every(t => t.annotations.readOnlyHint === false));
  } finally {
    await client.close();
    await new Promise(resolve => app.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});
