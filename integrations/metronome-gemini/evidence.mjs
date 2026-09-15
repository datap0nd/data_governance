import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { digest, definitionOf } from './client.mjs';
import { snapshot, fileSnapshot, fileHash, compare, evidenceSummary } from './verification.mjs';

export const evidenceDirectory = () => process.env.METRONOME_EVIDENCE_DIR || join(homedir(), '.gemini', 'metronome-evidence');
const validId = id => { if (!/^[a-f0-9-]{36}$/.test(id || '')) throw new Error('Invalid evidence ID.'); return id; };
const brief = ({ differences, ...result }) => result;

// Private evidence is owned by the same Windows identity as Gemini. These receipts
// enforce MCP sequencing; they are not a boundary against unrestricted shell access.
export class Evidence {
  constructor(client, sql, directory = evidenceDirectory()) { Object.assign(this, { client, sql, directory }); }
  path(id) { return join(this.directory, `${validId(id)}.json`); }
  async store(value) {
    await mkdir(this.directory, { recursive: true });
    const id = randomUUID();
    await writeFile(this.path(id), JSON.stringify(value), { flag: 'wx', mode: 0o600 });
    return id;
  }
  async read(id, kind) {
    let value;
    try { value = JSON.parse(await readFile(this.path(id), 'utf8')); }
    catch { throw new Error('Evidence is missing or damaged. Repeat the evidence step; no flow was changed.'); }
    if (value.kind !== kind) throw new Error('Wrong evidence type.');
    return value;
  }
  async begin(reference, source, output) {
    if (!['portal', 'file', 'outlook'].includes(source.type)) throw new Error('Select the source type.');
    if (source.type === 'portal') {
      if (!Number.isSafeInteger(source.site_id) || !this.client.siteAllowed(source.site_id)) throw new Error('Source is outside site scope.');
      const url = new URL(source.report_url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Use the observed portal report URL without credentials.');
      await this.client.checkPortalSource(source.site_id, url.href);
      source = { ...source, report_url: url.href, origin: url.origin };
    }
    const captured = await snapshot(reference, this.sql);
    if (captured.formula_cells) throw new Error('The trusted reference has cached formulas. Supply a verified values-only copy; cached values cannot establish freshness.');
    const record = { kind: 'reference', origin: this.client.baseUrl, reference, captured, source, output, created_at: new Date().toISOString() };
    const case_id = await this.store(record);
    return { case_id, reference: evidenceSummary(captured), source, output,
      next: 'Independently download the source using report-browser. Then compare_download with its actual download receipt. No flow can be proposed yet.' };
  }
  async compareDownload(caseId, downloadId, suppliedFile) {
    const ref = await this.read(caseId, 'reference');
    let spec, receipt = null;
    if (ref.source.type === 'portal') {
      receipt = await this.read(downloadId, 'browser_download');
      if (receipt.status !== 'completed' || receipt.page_origin !== ref.source.origin || receipt.started_at < ref.created_at) throw new Error('A completed independent download from this source after reference capture is required.');
      spec = { kind: 'file', path: receipt.path, ...ref.output };
    } else {
      if (!suppliedFile) throw new Error('Supply the independently acquired source file.');
      spec = { kind: 'file', path: suppliedFile, ...ref.output };
    }
    const actual = await fileSnapshot(spec);
    if (receipt && actual.sha256 !== receipt.sha256) throw new Error('The independent download changed after capture. Download it again.');
    if (actual.formula_cells) throw new Error('Export values-only source data before reconciliation.');
    const comparison = compare(ref.captured, actual);
    const id = await this.store({ kind: 'comparison', case_id: caseId, ref, actual, spec, comparison, download_id: downloadId || null });
    return { evidence_id: id, ...brief(comparison), evidence_path: this.path(id),
      next: comparison.verdict === 'EXACT_MATCH' ? 'Propose a recorded flow using this evidence ID.' : 'Resolve the reported differences with the owner. Do not assume refresh or silently change the reference.' };
  }
  async require(id, definition) {
    const e = await this.read(id, 'comparison');
    if (e.ref.origin !== this.client.baseUrl || e.comparison.verdict !== 'EXACT_MATCH') throw new Error('An exact independent comparison for this installation is required before flow authoring.');
    const source = e.ref.source;
    if ((definition.source_type || 'portal') !== source.type || source.type === 'portal' && definition.site_id !== source.site_id) throw new Error('Flow source differs from the compared source.');
    if (source.type === 'portal') await this.client.checkPortalSource(source.site_id, source.report_url);
    if (source.type === 'portal' && definition.report_id) {
      const catalog = await this.client.catalog();
      const report = catalog.reports.find(r => r.id === definition.report_id && r.site_id === source.site_id);
      if (!report?.report_url || new URL(report.report_url).href !== source.report_url) throw new Error('Flow report route differs from the compared source. Use the observed route or a new case.');
    }
    const current = await snapshot(e.ref.reference, this.sql);
    if (digest(current.rows) !== digest(e.ref.captured.rows) || digest(current.columns) !== digest(e.ref.captured.columns) || current.sha256 !== e.ref.captured.sha256) throw new Error('Reference changed. Capture and compare a new case before reviewing changes.');
    const actual = await fileSnapshot(e.spec);
    if (actual.sha256 !== e.actual.sha256) throw new Error('Independent evidence changed. Repeat its download and comparison.');
    return e;
  }
  async verify(record) {
    if (record.proposal.kind !== 'run_flow' || record.state !== 'completed') throw new Error('A confirmed run submission receipt is required.');
    const p = record.proposal, e = await this.read(p.evidence_id, 'comparison');
    const run = await this.client.getRun(record.receipt.id);
    if (run.id !== record.receipt.id || run.flow_id !== p.flow_id) throw new Error('Run does not belong to the reviewed flow.');
    if (run.status !== 'succeeded') return { flow_id: p.flow_id, run_id: run.id, execution: run.status, verdict: 'INCOMPLETE', next: 'Wait for terminal success or resolve the failed run through Metronome.' };
    if (digest(definitionOf(await this.client.getFlow(p.flow_id))) !== p.previous_fingerprint) throw new Error('Flow changed since run review; verification is blocked.');
    if ((p.definition.source_type || 'portal') === 'portal' && (run.recording_revision_id !== p.definition.recording_revision_id || run.recording_fingerprint !== p.recording_fingerprint)) throw new Error('Run recording differs from the reviewed revision.');
    const outputs = run.outputs || run.files?.filter(f => f.status !== 'source_snapshot');
    if (!outputs?.length) throw new Error('Successful execution has no accessible output evidence. It is not verified.');
    const results = [], all = [];
    for (const file of outputs) {
      if (!file.file_path || file.status && !['saved', 'downloaded', 'published', 'completed', 'succeeded'].includes(file.status)) throw new Error('Run output is missing or incomplete.');
      const actual = await fileSnapshot({ kind: 'file', path: file.file_path, ...e.ref.output });
      if (actual.formula_cells || file.checksum && actual.sha256 !== file.checksum) throw new Error('Run output is changed or contains unverified formulas.');
      results.push(evidenceSummary(actual)); all.push(actual);
    }
    if (all.some(a => digest(a.columns) !== digest(all[0].columns))) throw new Error('Run outputs have differing columns; resolve the output mapping.');
    const combined = { ...all[0], rows: all.flatMap(a => a.rows), row_count: all.reduce((n, a) => n + a.row_count, 0) };
    if (combined.row_count > 50000) throw new Error('Combined outputs exceed the verification limit; no complete match established.');
    const fileResult = compare(e.ref.captured, combined);
    let sqlResult = null, sqlEvidence = null;
    if (p.definition.sql_handoff_enabled) {
      const target = { kind: 'sql', database: p.definition.sql_database, schema: p.definition.sql_schema, table: p.definition.sql_table };
      if (!run.sql_target?.enabled || ['database', 'schema', 'table'].some(k => run.sql_target[k] !== target[k])) throw new Error('Run SQL target differs from the reviewed target.');
      if (run.sql_target.server !== this.sql.serverIdentity()) throw new Error('The reader server differs from the run SQL server. Configure the dedicated reader for that exact endpoint; no other database can prove this load.');
      const actual = await snapshot(target, this.sql);
      sqlResult = compare(e.ref.captured, actual); sqlEvidence = evidenceSummary(actual);
    }
    const verdict = [fileResult, sqlResult].filter(Boolean).every(r => r.verdict === 'EXACT_MATCH') ? 'SUCCESSFUL' : 'DATA_DIFFERENCES';
    const value = { kind: 'verification', proposal_id: record.id, flow_id: p.flow_id, run_id: run.id, execution: run.status, verdict,
      reference: evidenceSummary(e.ref.captured), files: results, sql: sqlEvidence, file_comparison: fileResult, sql_comparison: sqlResult, verified_at: new Date().toISOString() };
    const evidence_id = await this.store(value);
    return { ...value, file_comparison: brief(fileResult), sql_comparison: sqlResult && brief(sqlResult), evidence_id, evidence_path: this.path(evidence_id),
      scope: 'Exact scalar values and duplicate counts within the declared tables; row order ignored. SQL covers all reader-visible rows at verification time. Refresh is never inferred.' };
  }
}
