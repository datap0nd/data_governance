import { createHash } from 'node:crypto';

export const FLOW_FIELDS = `name execution_method recording_revision_id source_type site_id report_id
outlook_subject_contains local_file_path local_file_worksheet export_views download_links enabled selections
download_mode period_strategy window_weeks file_format asap_download_type export_report_title export_filter_details
excel_trim excel_worksheets browser_mode download_parallelism start_week end_week target_folder filename_template
output_mode schedule_type schedule_time schedule_days schedule_day transform_enabled transform_script_path
sql_handoff_enabled sql_mode sql_uppercase sql_database sql_schema sql_table sql_target_source_id
post_sql_refresh email_delivery owner_person_id`.split(/\s+/);
const FLOW_KEYS = new Set(FLOW_FIELDS);
const PRIVATE_KEYS = /password|passwd|secret|token|cookie|credential(?!s_configured)|storage_state|authorization|connection_string|replay_recipe/i;

export function scrub(value) {
  if (Array.isArray(value)) return value.map(scrub);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !PRIVATE_KEYS.test(key)).map(([key, item]) => [key, scrub(item)]));
  if (typeof value === 'string') return value
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[redacted]@')
    .replace(/([?&](?:access_token|token|key|password|secret|code)=)[^&#\s]+/gi, '$1[redacted]');
  return value;
}

export function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k])]));
  return value;
}
export const digest = value => createHash('sha256').update(JSON.stringify(stable(value))).digest('hex');
export const definitionOf = value => Object.fromEntries(FLOW_FIELDS.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
const pick = (value, fields) => Object.fromEntries(fields.filter(k => Object.hasOwn(value, k)).map(k => [k, value[k]]));
const httpOrigin = value => { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.origin : null; } catch { return null; } };
export const recordingFingerprint = definition => digest(Object.fromEntries(Object.entries(definition || {}).filter(([k]) => !['identity', 'identity_candidates', 'readiness'].includes(k))));

export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.status = status; }
}

export class MetronomeClient {
  constructor({ baseUrl = 'http://127.0.0.1:8000', flowIds = '*', siteIds = '*', fetchImpl = fetch, timeoutMs = 30000 } = {}) {
    const url = new URL(baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
      throw new Error('Metronome URL must be a loopback HTTP(S) origin, for example http://127.0.0.1:8000. Remote access is not supported.');
    }
    this.baseUrl = url.origin;
    this.flowIds = this.parseScope(flowIds);
    this.siteIds = this.parseScope(siteIds);
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }
  parseScope(raw) {
    if (raw === '*') return null;
    if (!raw || !/^\d+(,\d+)*$/.test(raw)) throw new Error('ID scope must be * or comma-separated positive IDs.');
    const ids = raw.split(',').map(Number);
    if (ids.some(id => !Number.isSafeInteger(id) || id < 1)) throw new Error('Scope IDs must be positive integers.');
    return new Set(ids);
  }
  flowAllowed(id) { return !this.flowIds || this.flowIds.has(id); }
  siteAllowed(id) { return !this.siteIds || this.siteIds.has(id); }
  checkFlow(id) {
    if (!Number.isSafeInteger(id) || id < 1 || !this.flowAllowed(id)) throw new Error('Flow is outside the configured scope.');
  }
  async request(path, { method = 'GET', body } = {}) {
    // Paths are produced only by methods below, never by model arguments.
    if (!path.startsWith('/api/') && path !== '/openapi.json') throw new Error('Unsupported API path.');
    let response;
    try {
      response = await this.fetch(this.baseUrl + path, {
        method, redirect: 'error', signal: AbortSignal.timeout(this.timeoutMs),
        headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    } catch {
      throw new ApiError(method === 'GET' ? 'Metronome is unavailable. Check its local URL and service, then retry the read.'
        : 'Metronome did not return a response. The operation may have happened; inspect its state before trying again.');
    }
    if (!response.ok) {
      throw new ApiError(`Metronome returned HTTP ${response.status}. ${response.status === 422
        ? 'Check the definition against get_flow_schema; no automatic retry was sent.'
        : 'Inspect the flow in Metronome before retrying.'}`, response.status);
    }
    const reader = response.body.getReader();
    let size = 0;
    const chunks = [];
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 8 * 1024 * 1024) { await reader.cancel(); throw new Error('Metronome response exceeds 8 MiB; narrow the request.'); }
        chunks.push(value);
      }
      return JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      throw new ApiError('Metronome response was incomplete, too large or invalid. Inspect state before retrying a write.');
    }
  }
  async version() { return scrub(await this.request('/api/version')); }
  async catalog() {
    const result = await this.request('/api/flows/catalog');
    return scrub({
      sites: (result.sites || []).filter(s => this.siteAllowed(s.id)).map(s => ({ ...pick(s, ['id', 'name', 'adapter', 'enabled', 'supports_discovery', 'credentials_configured']),
        source_origins: [...new Set([httpOrigin(s.base_url), ...(result.reports || []).filter(r => r.site_id === s.id).map(r => httpOrigin(r.report_url))].filter(Boolean))] })),
      reports: (result.reports || []).filter(r => this.siteAllowed(r.site_id)).map(r => ({
        ...pick(r, ['id', 'site_id', 'name', 'report_url', 'enabled', 'stale']),
        filters: (r.filters || []).map(f => pick(f, ['id', 'filter_key', 'label', 'control_type', 'required', 'options', 'enabled', 'stale'])),
      })),
    });
  }
  async checkPortalSource(siteId, reportUrl) {
    const url = new URL(reportUrl);
    const site = (await this.catalog()).sites.find(s => s.id === siteId);
    if (url.username || url.password || !site?.source_origins.includes(url.origin)) throw new Error('Report URL is outside this configured website. Use an observed route on its registered source origin.');
  }
  async listFlows() {
    const flows = await this.request('/api/flows');
    return scrub(flows.filter(f => this.flowAllowed(f.id) && this.siteAllowed(f.site_id)).map(f => ({
      ...definitionOf(f), ...pick(f, ['id', 'site_name', 'report_name', 'owner_name', 'updated_at']),
    })));
  }
  async getFlow(id) {
    this.checkFlow(id);
    const flow = await this.request(`/api/flows/${id}`);
    if (!this.siteAllowed(flow.site_id)) throw new Error('Flow source is outside the configured site scope.');
    return flow;
  }
  async flowDetails(id) {
    const f = await this.getFlow(id);
    return scrub({ ...definitionOf(f), ...pick(f, ['id', 'site_name', 'report_name', 'owner_name', 'updated_at', 'flow_folder']) });
  }
  async listRuns(flowId, limit = 20) {
    await this.getFlow(flowId);
    const runs = await this.request(`/api/flows/runs?flow_id=${flowId}&limit=${limit}`);
    return scrub(runs.map(r => pick(r, ['id', 'flow_id', 'status', 'trigger_type', 'started_at', 'finished_at', 'created_at', 'rows_inserted', 'row_count'])));
  }
  async getRun(id) {
    const run = await this.request(`/api/flows/runs/${id}`);
    await this.getFlow(run.flow_id);
    // Raw jobs, events and errors can contain browser state or entered values.
    return scrub({ ...pick(run, ['id', 'flow_id', 'status', 'trigger_type', 'started_at', 'finished_at', 'created_at', 'rows_inserted', 'row_count']),
      ...(run.job?.recording ? { recording_revision_id: run.job.recording.revision,
        recording_fingerprint: recordingFingerprint(run.job.recording.definition) } : {}),
      ...(run.job?.sql_handoff ? { sql_target: pick(run.job.sql_handoff, ['enabled', 'server', 'database', 'schema', 'table', 'mode']) } : {}),
      ...(run.artifacts?.length ? { outputs: run.artifacts.filter(a => a.status !== 'source_snapshot').map(a => ({
        status: a.status, file_path: a.published_file_path || a.deliverable_file_path || a.file_path,
        checksum: a.published_checksum || a.deliverable_checksum || a.checksum,
      })) } : {}),
      files: (run.files || []).map(f => pick(f, ['period_key', 'file_path', 'filename', 'file_size', 'checksum', 'row_count', 'status'])) });
  }
  async recordings(flowId) {
    await this.getFlow(flowId);
    const result = await this.request(`/api/flows/${flowId}/recordings`);
    return { flow_id: flowId, revisions: (result.revisions || []).map(r => pick(r,
      ['id', 'flow_id', 'created_at', 'status', 'validation_status'])),
      sessions: (result.sessions || []).map(s => ({ ...pick(s, ['scan_id', 'flow_id', 'operation', 'revision_id', 'status', 'finish_requested', 'cancel_requested']),
        stage: (() => { try { return JSON.parse(s.progress_json || '{}').stage || null; } catch { return null; } })() })),
      note: 'Control the native Playwright recorder with your available computer-control tool. If unavailable, pause for assistance. Never substitute detected controls or invented steps.' };
  }
  async recordingProof(flowId, revisionId) {
    await this.getFlow(flowId);
    const result = await this.request(`/api/flows/${flowId}/recordings`);
    const revision = (result.revisions || []).find(r => r.id === revisionId && r.flow_id === flowId);
    if (!revision?.definition?.steps?.length) throw new Error('Select an existing, nonempty Playwright recording revision belonging to this flow.');
    return recordingFingerprint(revision.definition);
  }
  async recordingAction(flowId, action, scanId) {
    const f = await this.getFlow(flowId);
    this.checkDefinition(definitionOf(f));
    if ((f.source_type || 'portal') !== 'portal') throw new Error('Only portal flows have a Playwright recorder.');
    if (!['start', 'finish', 'cancel'].includes(action)) throw new Error('Unsupported recorder action.');
    if (action !== 'start') {
      const list = await this.recordings(flowId);
      if (!Number.isSafeInteger(scanId) || !list.sessions.some(s => s.scan_id === scanId && s.flow_id === flowId && s.operation === 'record')) throw new Error('Recording session is outside this flow.');
    }
    const result = await this.request(`/api/flows/${flowId}/recordings/${action === 'start' ? action : `${scanId}/${action}`}`, { method: 'POST' });
    return { id: result.scan_id, scan_id: result.scan_id, status: result.status || (action === 'start' ? 'queued' : action === 'finish' ? 'finishing' : 'cancelling') };
  }
  async draftRecording(definition, reportUrl) {
    this.checkDefinition(definition);
    if (this.flowIds) throw new Error('Creating a recording draft requires * Flow scope.');
    await this.checkPortalSource(definition.site_id, reportUrl);
    return this.request('/api/flows/recordings/draft', { method: 'POST', body: { name: definition.name, site_id: definition.site_id, report_url: reportUrl } });
  }
  async flowSchema() {
    const api = await this.request('/openapi.json');
    const schemas = api.components?.schemas || {};
    if (!schemas.FlowWrite) throw new Error('This Metronome version does not expose FlowWrite. Update Metronome before creating flows.');
    const collected = {};
    function collect(name) {
      if (Object.hasOwn(collected, name) || !schemas[name]) return;
      collected[name] = schemas[name];
      const visit = item => {
        if (!item || typeof item !== 'object') return;
        if (typeof item.$ref === 'string' && item.$ref.startsWith('#/components/schemas/')) collect(item.$ref.split('/').at(-1));
        Object.values(item).forEach(visit);
      };
      visit(schemas[name]);
    }
    collect('FlowWrite');
    return { root: '#/components/schemas/FlowWrite', components: { schemas: collected },
      supported_fields: FLOW_FIELDS, note: 'Use the running server schema. Unsupported fields are rejected by the adapter; application validation remains authoritative.' };
  }
  checkDefinition(value) {
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('A flow definition must be a JSON object.');
    for (const key of Object.keys(value)) if (!FLOW_KEYS.has(key)) throw new Error(`Unsupported flow field: ${key}`);
    if (typeof value.name !== 'string' || !value.name.trim()) throw new Error('A flow name is required.');
    if (value.transform_enabled || value.transform_script_path) throw new Error('Executable transformation scripts are not exposed by this MCP. Prepare data separately or review the transformation in Metronome.');
    if (value.email_delivery?.enabled) throw new Error('Email delivery is not exposed by this MCP.');
    if (value.post_sql_refresh?.mode && value.post_sql_refresh.mode !== 'off') throw new Error('Post-SQL view refresh is not exposed by this MCP.');
    const source = value.source_type || 'portal';
    if (!['portal', 'outlook', 'file'].includes(source)) throw new Error('Unsupported source type.');
    if (source === 'portal' && (!Number.isSafeInteger(value.site_id) || !this.siteAllowed(value.site_id))) throw new Error('Select an allowed portal site from the catalog.');
    if (source === 'portal' && value.execution_method !== 'recorded') throw new Error('Portal flows must explicitly use execution_method recorded. Detected controls/catalog mode is not allowed.');
    if (this.siteIds && source !== 'portal') throw new Error('File and Outlook sources require unrestricted site scope in this version.');
    return value;
  }
  async saveFlow(definition, flowId = null) {
    this.checkDefinition(definition);
    if (flowId !== null) await this.getFlow(flowId);
    return this.request(flowId === null ? '/api/flows' : `/api/flows/${flowId}`, { method: flowId === null ? 'POST' : 'PUT', body: definition });
  }
  async runFlow(flowId) {
    const f = await this.getFlow(flowId);
    this.checkDefinition(definitionOf(f));
    if ((f.source_type || 'portal') === 'portal') await this.recordingProof(flowId, f.recording_revision_id);
    return this.request(`/api/flows/${flowId}/run`, { method: 'POST' });
  }
}
