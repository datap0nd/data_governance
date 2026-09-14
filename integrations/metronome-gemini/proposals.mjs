import { mkdir, readFile, writeFile, rename, open, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { definitionOf, digest, scrub } from './client.mjs';

export class Proposals {
  constructor(client, directory = join(homedir(), '.gemini', 'metronome-proposals')) {
    this.client = client;
    this.directory = directory;
  }
  path(id) {
    if (!/^[a-f0-9]{64}$/.test(id)) throw new Error('Invalid proposal ID.');
    return join(this.directory, `${id}.json`);
  }
  async persist(record) {
    await mkdir(this.directory, { recursive: true });
    const target = this.path(record.id);
    const tmp = `${target}.${randomUUID()}.tmp`;
    await writeFile(tmp, JSON.stringify(record, null, 2), { flag: 'wx', mode: 0o600 });
    await rename(tmp, target);
  }
  async read(id) {
    let record;
    let raw;
    try { raw = await readFile(this.path(id), 'utf8'); }
    catch (error) {
      if (error.code === 'ENOENT') { const missing = new Error('Proposal not found. Prepare it before applying.'); missing.code = 'ENOENT'; throw missing; }
      throw new Error('Proposal cannot be read. Inspect its existing receipt before recovery.');
    }
    try { record = JSON.parse(raw); }
    catch { throw new Error('Proposal is damaged. Inspect Metronome before recovering it; no new request was sent.'); }
    if (record.id !== id || digest(record.proposal) !== id || record.proposal.origin !== this.client.baseUrl) throw new Error('Proposal changed or belongs to another Metronome installation.');
    if (record.proposal.flow_id !== null) this.client.checkFlow(record.proposal.flow_id);
    if (record.proposal.flow_id === null && this.client.flowIds) throw new Error('Creating a flow requires * Flow scope.');
    this.client.checkDefinition(record.proposal.definition);
    return record;
  }
  async locked(id, action) {
    await mkdir(this.directory, { recursive: true });
    let handle;
    const lock = `${this.path(id)}.lock`;
    try { handle = await open(lock, 'wx', 0o600); }
    catch { throw new Error('This proposal is already in progress or has an interrupted attempt. Inspect Metronome before recovering its local lock.'); }
    try { return await action(); }
    finally { await handle.close(); await unlink(lock); }
  }
  async prepareFlow(definition, flowId = null) {
    if (flowId === null && this.client.flowIds) throw new Error('Creating a flow requires * Flow scope; a new ID is not in an existing-ID allowlist.');
    this.client.checkDefinition(definition);
    const previous = flowId === null ? null : definitionOf(await this.client.getFlow(flowId));
    const effective = previous ? { ...previous, ...definition } : {
      enabled: false, schedule_type: 'manual', sql_handoff_enabled: false, ...definition,
    };
    this.client.checkDefinition(effective);
    const proposal = { kind: 'save_flow', origin: this.client.baseUrl, flow_id: flowId,
      previous_fingerprint: previous ? digest(previous) : null, definition: effective };
    return this.prepare(proposal);
  }
  async prepareRun(flowId, requestId) {
    const current = definitionOf(await this.client.getFlow(flowId));
    this.client.checkDefinition(current);
    return this.prepare({ kind: 'run_flow', origin: this.client.baseUrl, flow_id: flowId,
      request_id: requestId, previous_fingerprint: digest(current), definition: current });
  }
  async prepare(proposal) {
    const id = digest(proposal);
    return this.locked(id, async () => {
      let record;
      try { record = await this.read(id); }
      catch (e) {
        if (e.code !== 'ENOENT') throw e;
        record = { id, proposal, state: 'prepared', created_at: new Date().toISOString() };
        await this.persist(record);
      }
      return this.review(record);
    });
  }
  review(record) {
    return { proposal_id: record.id, state: record.state, operation: record.proposal.kind,
      flow_id: record.proposal.flow_id, definition: scrub(record.proposal.definition),
      confirmation_json: JSON.stringify(record.proposal.definition),
      ...(record.receipt ? { receipt: record.receipt } : {}),
      note: 'Show this complete definition to the user. Applying requires Gemini’s interactive tool confirmation. A native Metronome schedule can run independently after activation.' };
  }
  async execute(id, confirmation, kind) {
    return this.locked(id, async () => {
      const record = await this.read(id);
      const proposal = record.proposal;
      if (proposal.kind !== kind) throw new Error('Proposal operation does not match this tool.');
      if (digest(confirmation) !== digest(proposal.definition)) throw new Error('Confirmation definition does not match the stored proposal. Review the exact definition again.');
      if (record.state === 'completed') return { ...record.receipt, replayed_receipt: true };
      if (record.state !== 'prepared') throw new Error('The previous attempt has an uncertain outcome. Inspect existing flows/runs; do not submit it again.');
      if (proposal.flow_id !== null) {
        const current = definitionOf(await this.client.getFlow(proposal.flow_id));
        if (digest(current) !== proposal.previous_fingerprint) throw new Error('The flow changed after review. Prepare and review a new proposal.');
      }
      this.client.checkDefinition(proposal.definition);
      // Persist before dispatch. A crash or lost response can never be retried as a fresh request.
      record.state = 'sending';
      await this.persist(record);
      try {
        const result = kind === 'save_flow'
          ? await this.client.saveFlow(proposal.definition, proposal.flow_id)
          : await this.client.runFlow(proposal.flow_id);
        record.receipt = { id: result.id, flow_id: kind === 'save_flow' ? result.id : proposal.flow_id,
          operation: kind, status: result.status || 'saved', completed_at: new Date().toISOString() };
        record.state = 'completed';
        await this.persist(record);
        return record.receipt;
      } catch {
        record.state = 'uncertain';
        await this.persist(record);
        throw new Error('Metronome did not confirm a complete result. The operation may have happened. Inspect flows/runs before recovery; this proposal will not be sent again.');
      }
    });
  }
}
