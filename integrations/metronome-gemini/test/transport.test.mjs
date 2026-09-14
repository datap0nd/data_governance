import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';

test('real SDK client negotiates stdio, discovers tools and receives bounded tool errors', async () => {
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [fileURLToPath(new URL('../server.mjs', import.meta.url))],
    env: { PATH: process.env.PATH || '', METRONOME_BASE_URL: 'http://127.0.0.1:1' }, stderr: 'pipe' });
  const client = new Client({ name: 'fixture', version: '1' });
  try {
    await client.connect(transport);
    const result = await client.listTools();
    assert.ok(result.tools.some(t => t.name === 'list_flows'));
    assert.ok(!result.tools.some(t => /shell|credentials|upload|evaluate/.test(t.name)));
    const caps = await client.callTool({ name: 'capabilities', arguments: {} });
    assert.equal(JSON.parse(caps.content[0].text).recommended_model, 'gemini-3.5-flash');
    const unavailable = await client.callTool({ name: 'get_version', arguments: {} });
    assert.equal(unavailable.isError, true);
    assert.match(unavailable.content[0].text, /unavailable/);
    const sql = await client.callTool({ name: 'query_readonly', arguments: { sql: 'select 1' } });
    assert.equal(sql.isError, true);
    assert.match(sql.content[0].text, /not configured/);
    const badId = await client.callTool({ name: 'get_flow', arguments: { flow_id: -1 } });
    assert.equal(badId.isError, true);
  } finally { await client.close(); }
});
