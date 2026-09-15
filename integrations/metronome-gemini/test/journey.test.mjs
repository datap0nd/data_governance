import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer as httpServer } from 'node:http';
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer } from '../server.mjs';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
import { MetronomeClient, digest, recordingFingerprint } from '../client.mjs';
import { fileHash } from '../verification.mjs';

const csv = '\ufeff법인,일자,금액\r\n가상 A,2026-09-01,120\r\n가상 B,2026-09-02,80\r\n';
const rows = [{ 법인: '가상 A', 일자: '2026-09-01', 금액: '120' }, { 법인: '가상 B', 일자: '2026-09-02', 금액: '80' }];
async function connect(server) {
  const client = new Client({ name: 'synthetic-fixture', version: '1' });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await server.connect(a); await client.connect(b); return client;
}

test('real browser download → evidence-gated recorder/API run → actual output and SQL reconciliation', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'metronome-journey-'));
  const before = { METRONOME_EVIDENCE_DIR: process.env.METRONOME_EVIDENCE_DIR, METRONOME_PROPOSAL_DIR: process.env.METRONOME_PROPOSAL_DIR };
  Object.assign(process.env, { METRONOME_EVIDENCE_DIR: directory, METRONOME_PROPOSAL_DIR: join(directory, 'proposals') });
  let flow, saves = 0, runs = 0, starts = 0, revision = null, session = null;
  let runStatus = 'queued', runRevision = 8, sqlRows = rows, sqlTruncated = false, files = [], wrongTarget = false, wrongServer = false;
  const recording = { steps: [{ action: 'click', locator: 'fictional-recorded-only' }] };
  const reference = join(directory, 'reference.csv'), output = join(directory, 'output.csv');
  await writeFile(reference, csv); await writeFile(output, csv);
  const portal = httpServer((req, res) => {
    if (req.url === '/export') {
      res.setHeader('Content-Type', 'text/csv'); res.setHeader('Content-Disposition', 'attachment; filename="trips.csv"'); return res.end(csv);
    }
    if (req.url === '/broken') { res.setHeader('Content-Disposition', 'attachment; filename="broken.csv"'); res.setHeader('Content-Length', '100000'); res.write('partial'); setTimeout(() => res.destroy(), 50); return; }
    res.setHeader('Content-Type', 'text/html'); res.end('<a href="/export">Download</a><a href="/broken">Interrupted download</a>');
  });
  await new Promise(resolve => portal.listen(0, '127.0.0.1', resolve));
  const sourceUrl = `http://127.0.0.1:${portal.address().port}/report`;
  const app = httpServer(async (req, res) => {
    let input = ''; for await (const chunk of req) input += chunk;
    const body = input ? JSON.parse(input) : {};
    const send = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (req.url === '/api/flows/catalog') return send({ sites: [{ id: 1, name: 'Fictional ASAP' }], reports: [{ id: 15, site_id: 1, report_url: sourceUrl }] });
    if (req.url === '/api/flows/recordings/draft') { saves++; flow = { id: 42, name: body.name, source_type: 'portal', site_id: 1, report_id: 15, execution_method: 'recorded', enabled: false, schedule_type: 'manual', sql_handoff_enabled: false }; return send(flow); }
    if (req.url === '/api/flows/42' && req.method === 'PUT') { saves++; flow = { id: 42, ...body }; return send(flow); }
    if (req.url === '/api/flows/42') return send(flow);
    if (req.url === '/api/flows/42/recordings') return send({ revisions: revision ? [{ id: 8, flow_id: 42, definition: recording, status: 'draft' }] : [], sessions: session ? [session] : [] });
    if (req.url === '/api/flows/42/recordings/start') { starts++; session = { scan_id: 6, flow_id: 42, operation: 'record', status: 'running', progress_json: '{"stage":"recording"}' }; return send({ scan_id: 6 }); }
    if (req.url === '/api/flows/42/recordings/6/finish') { revision = 8; session.status = 'succeeded'; return send({ scan_id: 6 }); }
    if (req.url === '/api/flows/42/run') { runs++; return send({ id: 9, status: 'queued' }); }
    if (req.url === '/api/flows/runs/9') return send({ id: 9, flow_id: 42, status: runStatus, files,
      job: { password: 'MUST_NOT_LEAK', recording: { revision: runRevision, definition: recording }, sql_handoff: { enabled: true, server: wrongServer ? 'other' : 'fictional', database: 'fixture', schema: 'reporting', table: wrongTarget ? 'wrong' : 'trips' } } });
    res.statusCode = 404; send({ detail: 'not found' });
  });
  await new Promise(resolve => app.listen(0, '127.0.0.1', resolve));
  const api = new MetronomeClient({ baseUrl: `http://127.0.0.1:${app.address().port}` });
  const sql = { serverIdentity: () => 'fictional', snapshot: async (query, database) => {
    assert.equal(query, 'SELECT * FROM "reporting"."trips"'); assert.equal(database, 'fixture');
    return { columns: Object.keys(rows[0]), rows: sqlRows, truncated: sqlTruncated };
  } };
  let browserAdapter, browserClient, client;
  t.after(async () => {
    await client?.close(); await browserClient?.close(); await browserAdapter?.close();
    await new Promise(resolve => app.close(resolve)); await new Promise(resolve => portal.close(resolve));
    for (const [k, v] of Object.entries(before)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
    await rm(directory, { recursive: true, force: true });
  });
  client = await connect(createServer({ client: api, sql }));
  const call = async (name, args = {}, error) => {
    const result = await client.callTool({ name, arguments: args });
    if (error) { assert.equal(result.isError, true, name); assert.match(result.content[0].text, error); return; }
    assert.ok(!result.isError, result.content[0].text); return JSON.parse(result.content[0].text);
  };
  const base = { name: '출장비', source_type: 'portal', site_id: 1, report_id: 15, execution_method: 'recorded' };
  await call('begin_reference', { reference: { kind: 'file', path: reference }, source: { type: 'portal', site_id: 1, report_url: 'https://unrelated.invalid/report' }, output_table: {} }, /outside this configured website/);
  await call('propose_flow', { definition_json: JSON.stringify(base) }, /evidence_id|validation/i);
  const ref = await call('begin_reference', { reference: { kind: 'file', path: reference }, source: { type: 'portal', site_id: 1, report_url: sourceUrl }, output_table: {} });
  const missingSubsidiary = join(directory, 'expected-three.csv');
  await writeFile(missingSubsidiary, csv + '가상 C,2026-09-03,50\r\n');
  const largerRef = await call('begin_reference', { reference: { kind: 'file', path: missingSubsidiary }, source: { type: 'portal', site_id: 1, report_url: sourceUrl }, output_table: {} });
  await call('compare_download', { case_id: ref.case_id, source_file: reference }, /missing|Invalid/);
  const openBrowser = async () => {
    const client = new Client({ name: 'synthetic-browser-client', version: '1' });
    await client.connect(new StdioClientTransport({ command: process.execPath,
      args: [fileURLToPath(new URL('browser-fixture.mjs', import.meta.url))], stderr: 'pipe',
      env: { PATH: process.env.PATH || '', METRONOME_EVIDENCE_DIR: directory, METRONOME_BASE_URL: api.baseUrl } }));
    return client;
  };
  browserClient = await openBrowser();
  const tools = (await browserClient.listTools()).tools;
  assert.ok(!tools.some(x => /evaluate|run_code|upload|network|storage/.test(x.name)));
  const navigate = await browserClient.callTool({ name: 'browser_navigate', arguments: { url: sourceUrl } });
  assert.ok(!navigate.isError, JSON.stringify(navigate));
  const observed = await browserClient.callTool({ name: 'browser_snapshot', arguments: {} });
  const text = observed.content.filter(c => c.type === 'text').map(c => c.text).join('\n');
  const link = text.match(/link "Download" \[ref=([^\]]+)\]/);
  assert.ok(link, text);
  let downloaded = await browserClient.callTool({ name: 'browser_click', arguments: { target: link[1] } });
  assert.ok(!downloaded.isError, JSON.stringify(downloaded));
  let receipt;
  for (let attempt = 0; attempt < 15; attempt++) {
    for (const content of downloaded.content) {
      if (content.type !== 'text') continue;
      try { receipt ||= JSON.parse(content.text).independent_downloads?.find(r => r.status === 'completed'); } catch {}
    }
    if (receipt) break;
    downloaded = await browserClient.callTool({ name: 'browser_snapshot', arguments: {} });
  }
  assert.ok(receipt, JSON.stringify(downloaded));
  const compared = await call('compare_download', { case_id: ref.case_id, download_id: receipt.download_id });
  assert.equal(compared.verdict, 'EXACT_MATCH'); assert.equal(saves, 0);
  const eid = compared.evidence_id;
  const incompleteSource = await call('compare_download', { case_id: largerRef.case_id, download_id: receipt.download_id });
  assert.equal(incompleteSource.missing_rows, 1);
  await call('propose_flow', { definition_json: JSON.stringify(base), evidence_id: incompleteSource.evidence_id }, /exact independent comparison/);
  await writeFile(receipt.path, csv + 'unexpected,row,1\r\n');
  await call('propose_flow', { definition_json: JSON.stringify(base), evidence_id: eid }, /Independent evidence changed/);
  await writeFile(receipt.path, csv);
  await call('propose_flow', { definition_json: JSON.stringify({ ...base, execution_method: 'catalog' }), evidence_id: eid }, /recorded/);
  await call('propose_flow', { definition_json: JSON.stringify({ ...base, site_id: 2 }), evidence_id: eid }, /source differs/);
  await call('propose_flow', { definition_json: JSON.stringify({ ...base, report_id: 99 }), evidence_id: eid }, /route differs/);
  const draft = await call('propose_recording', { action: 'draft', evidence_id: eid, name: '출장비', request_id: 'draft-1' });
  const saved = await call('apply_recording_proposal', { proposal_id: draft.proposal_id, confirmation_json: draft.confirmation_json });
  assert.equal(saved.flow_id, 42);
  await call('apply_recording_proposal', { proposal_id: draft.proposal_id, confirmation_json: draft.confirmation_json }); assert.equal(saves, 1);
  await call('propose_run', { flow_id: 42, evidence_id: eid, request_id: 'early' }, /existing, nonempty/);
  const start = await call('propose_recording', { action: 'start', flow_id: 42, evidence_id: eid, request_id: 'capture-1' });
  await call('apply_recording_proposal', { proposal_id: start.proposal_id, confirmation_json: start.confirmation_json });
  await call('apply_recording_proposal', { proposal_id: start.proposal_id, confirmation_json: start.confirmation_json }); assert.equal(starts, 1);
  const list = await call('list_recordings', { flow_id: 42 }); assert.equal(list.sessions[0].stage, 'recording');
  await assert.rejects(api.recordingAction(42, 'finish', 99), /session is outside/);
  const finish = await call('propose_recording', { action: 'finish', flow_id: 42, scan_id: 6, evidence_id: eid, request_id: 'finish-1' });
  await call('apply_recording_proposal', { proposal_id: finish.proposal_id, confirmation_json: finish.confirmation_json });
  const proposal = await call('propose_flow', { flow_id: 42, evidence_id: eid, definition_json: JSON.stringify({ ...base, recording_revision_id: 8, sql_handoff_enabled: true, sql_database: 'fixture', sql_schema: 'reporting', sql_table: 'trips', sql_mode: 'replace' }) });
  await call('apply_flow_proposal', { proposal_id: proposal.proposal_id, confirmation_json: JSON.stringify({ ...proposal.definition, sql_table: 'evil' }) }, /does not match/);
  await writeFile(reference, csv + '가상 C,2026-09-03,50\r\n');
  await call('apply_flow_proposal', { proposal_id: proposal.proposal_id, confirmation_json: proposal.confirmation_json }, /Reference changed/);
  await writeFile(reference, csv);
  await call('apply_flow_proposal', { proposal_id: proposal.proposal_id, confirmation_json: proposal.confirmation_json });
  const run = await call('propose_run', { flow_id: 42, evidence_id: eid, request_id: 'cycle-1' });
  const verify = () => call('verify_run', { proposal_id: run.proposal_id });
  recording.steps.push({ action: 'click', locator: 'changed' });
  await call('run_flow', { proposal_id: run.proposal_id, confirmation_json: run.confirmation_json }, /Recording changed/);
  recording.steps.pop();
  await call('run_flow', { proposal_id: run.proposal_id, confirmation_json: run.confirmation_json });
  await call('run_flow', { proposal_id: run.proposal_id, confirmation_json: run.confirmation_json }); assert.equal(runs, 1);
  assert.equal((await verify()).verdict, 'INCOMPLETE');
  runStatus = 'failed'; assert.equal((await verify()).execution, 'failed');
  runStatus = 'succeeded'; await call('verify_run', { proposal_id: run.proposal_id }, /no accessible output/);
  files = [{ file_path: output, status: 'saved', checksum: fileHash(Buffer.from(csv)) }];
  runRevision = 10; await call('verify_run', { proposal_id: run.proposal_id }, /recording differs/); runRevision = 8;
  wrongTarget = true; await call('verify_run', { proposal_id: run.proposal_id }, /SQL target differs/); wrongTarget = false;
  wrongServer = true; await call('verify_run', { proposal_id: run.proposal_id }, /reader server differs/); wrongServer = false;
  sqlTruncated = true; await call('verify_run', { proposal_id: run.proposal_id }, /bounded full-table/); sqlTruncated = false;
  sqlRows = rows.slice(0, 1); const diff = await verify(); assert.equal(diff.verdict, 'DATA_DIFFERENCES'); assert.equal(diff.sql_comparison.missing_rows, 1);
  sqlRows = rows;
  await writeFile(output, csv + '가상 C,2026-09-03,50\r\n');
  await call('verify_run', { proposal_id: run.proposal_id }, /changed/);
  files[0].checksum = fileHash(await readFile(output));
  assert.equal((await verify()).file_comparison.extra_rows, 1);
  await writeFile(output, csv); files[0].checksum = fileHash(Buffer.from(csv));
  const result = await verify(); assert.equal(result.verdict, 'SUCCESSFUL'); assert.equal(result.file_comparison.reference_rows, 2);
  assert.ok(!JSON.stringify(await call('get_run', { run_id: 9 })).includes('MUST_NOT_LEAK'));
  assert.equal(JSON.parse(await readFile(result.evidence_path, 'utf8')).verdict, 'SUCCESSFUL');
  assert.equal(result.sql_comparison.actual_rows, 2);
  await assert.rejects(browserClient.callTool({ name: 'browser_navigate', arguments: { url: api.baseUrl } }), /Metronome operations/);
  await assert.rejects(browserClient.callTool({ name: 'browser_take_screenshot', arguments: { filename: join(directory, 'governance.db') } }), /output paths/);
  const brokenLink = text.match(/link "Interrupted download" \[ref=([^\]]+)\]/);
  // The pinned upstream MCP has an unhandled saveAs rejection on interrupted
  // transfers. Isolate its process; lost connection remains failure, never proof.
  let interruptionObserved = false;
  try {
    let broken = await browserClient.callTool({ name: 'browser_click', arguments: { target: brokenLink[1] } });
    for (let i = 0; i < 20; i++) {
      if (broken.isError || broken.content.some(c => c.type === 'text' && c.text.includes('"status":"failed"'))) { interruptionObserved = true; break; }
      broken = await browserClient.callTool({ name: 'browser_snapshot', arguments: {} });
    }
  } catch { interruptionObserved = true; }
  assert.ok(interruptionObserved, 'Interrupted download must fail or lose its browser connection.');
  await browserClient.close(); browserClient = await openBrowser();
  const resumed = await browserClient.callTool({ name: 'browser_navigate', arguments: { url: sourceUrl } });
  assert.ok(!resumed.isError, JSON.stringify(resumed));
  const resumeSnapshot = await browserClient.callTool({ name: 'browser_snapshot', arguments: {} });
  const resumeText = resumeSnapshot.content.filter(c => c.type === 'text').map(c => c.text).join('\n');
  const retryLink = resumeText.match(/link "Download" \[ref=([^\]]+)\]/);
  const recovered = await browserClient.callTool({ name: 'browser_click', arguments: { target: retryLink[1] } });
  assert.ok(!recovered.isError);
  assert.equal((await verify()).verdict, 'SUCCESSFUL', 'Saved evidence survives independent browser restart.');
  // Missing recorder control is handled by the agent pause instruction; this
  // fixture simulates only the existing recording API, not a native recorder.
});
