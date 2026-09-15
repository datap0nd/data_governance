import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { MetronomeClient } from '../client.mjs';
import { Proposals } from '../proposals.mjs';

const evidence = { require: async () => ({}) };
const definition = { name: '출장비', source_type: 'portal', site_id: 1, report_id: 15, execution_method: 'recorded', recording_revision_id: 8,
  enabled: false, schedule_type: 'manual', sql_handoff_enabled: true, sql_database: 'fictional_reporting', sql_schema: 'reporting', sql_table: 'trips', sql_mode: 'append' };
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'metronome-proposals-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const client = new MetronomeClient();
  client.recordingProof = async () => 'fixture-recording';
  let current = structuredClone(definition), saves = 0, runs = 0;
  client.getFlow = async () => ({ id: 42, ...current });
  client.saveFlow = async value => { saves++; current = structuredClone(value); return { id: 42 }; };
  client.runFlow = async () => { runs++; return { id: 9, status: 'queued' }; };
  return { client, directory, proposals: new Proposals(client, directory, evidence), counts: () => ({ saves, runs }),
    change: fields => { current = { ...current, ...fields }; } };
}
test('preparing a new flow is non-mutating and its full definition is reviewable', async t => {
  const f = await fixture(t);
  const proposal = await f.proposals.prepareFlow(definition);
  assert.equal(proposal.state, 'prepared');
  assert.deepEqual(JSON.parse(proposal.confirmation_json), definition);
  assert.deepEqual(f.counts(), { saves: 0, runs: 0 });
});
test('apply binds exact definition and does not send a modified confirmation', async t => {
  const f = await fixture(t), p = await f.proposals.prepareFlow(definition);
  await assert.rejects(f.proposals.execute(p.proposal_id, { ...definition, sql_table: 'other' }, 'save_flow'), /does not match/);
  assert.equal(f.counts().saves, 0);
});
test('completed receipts deduplicate retries across MCP restarts', async t => {
  const f = await fixture(t), p = await f.proposals.prepareFlow(definition);
  const first = await f.proposals.execute(p.proposal_id, definition, 'save_flow');
  assert.equal(first.id, 42);
  const restarted = new Proposals(f.client, f.directory, evidence);
  const second = await restarted.execute(p.proposal_id, definition, 'save_flow');
  assert.equal(second.replayed_receipt, true);
  assert.equal(f.counts().saves, 1);
});
test('editing a flow after review prevents update and run dispatch', async t => {
  const f = await fixture(t);
  const edit = await f.proposals.prepareFlow({ ...definition, name: 'New title' }, 42);
  const run = await f.proposals.prepareRun(42, 'report-cycle-1');
  f.change({ sql_table: 'changed_elsewhere' });
  await assert.rejects(f.proposals.execute(edit.proposal_id, JSON.parse(edit.confirmation_json), 'save_flow'), /changed after review/);
  await assert.rejects(f.proposals.execute(run.proposal_id, JSON.parse(run.confirmation_json), 'run_flow'), /changed after review/);
  assert.deepEqual(f.counts(), { saves: 0, runs: 0 });
});
test('lost response is durable uncertainty and cannot be resent', async t => {
  const f = await fixture(t), p = await f.proposals.prepareFlow(definition);
  let calls = 0;
  f.client.saveFlow = async () => { calls++; throw new Error('network'); };
  await assert.rejects(f.proposals.execute(p.proposal_id, definition, 'save_flow'), /may have happened/);
  await assert.rejects(new Proposals(f.client, f.directory, evidence).execute(p.proposal_id, definition, 'save_flow'), /uncertain/);
  assert.equal(calls, 1);
});
test('same request key deduplicates a run; a new key denotes a separately reviewed run', async t => {
  const f = await fixture(t);
  const p = await f.proposals.prepareRun(42, 'cycle-1');
  await f.proposals.execute(p.proposal_id, JSON.parse(p.confirmation_json), 'run_flow');
  const repeated = await f.proposals.prepareRun(42, 'cycle-1');
  assert.equal(repeated.state, 'completed');
  const next = await f.proposals.prepareRun(42, 'cycle-2');
  assert.notEqual(p.proposal_id, next.proposal_id);
  assert.equal(f.counts().runs, 1);
});
test('corrupt state and concurrent requests fail closed', async t => {
  const f = await fixture(t), p = await f.proposals.prepareFlow(definition);
  await writeFile(f.proposals.path(p.proposal_id) + '.lock', '');
  await assert.rejects(f.proposals.execute(p.proposal_id, definition, 'save_flow'), /already in progress/);
  await rm(f.proposals.path(p.proposal_id) + '.lock');
  await writeFile(f.proposals.path(p.proposal_id), '{broken');
  await assert.rejects(f.proposals.prepareFlow(definition), /damaged/);
  assert.equal(f.counts().saves, 0);
});
