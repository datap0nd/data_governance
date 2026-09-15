import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { MetronomeClient, scrub } from './client.mjs';
import { ReadonlySql } from './sql.mjs';
import { Proposals } from './proposals.mjs';
import { Evidence } from './evidence.mjs';
import { accessSettings, setting, sqlSettings } from './settings.mjs';

const id = z.number().int().positive();
export function createServer({ client = new MetronomeClient(accessSettings()), sql = new ReadonlySql(sqlSettings()) } = {}) {
  const server = new McpServer({ name: 'metronome', version: '0.2.0' });
  const evidence = new Evidence(client, sql);
  const proposals = new Proposals(client, setting('METRONOME_PROPOSAL_DIR'), evidence);
  function tool(name, description, inputSchema, action, readOnly = true) {
    server.registerTool(name, { description, inputSchema,
      annotations: { readOnlyHint: readOnly, destructiveHint: !readOnly, idempotentHint: readOnly, openWorldHint: true } },
    async args => {
      try { return { content: [{ type: 'text', text: JSON.stringify(scrub(await action(args))) }] }; }
      catch (error) { return { isError: true, content: [{ type: 'text', text: error.message }] }; }
    });
  }
  tool('capabilities', 'Check this integration, configured scope and installed analysis library. Does not connect to SQL or browse portals.', {}, () => ({
    recommended_model: 'gemini-3.5-flash', compatible_model: 'gemini-3.1-pro-preview',
    metronome_url: client.baseUrl, scope: { flow_ids: client.flowIds ? [...client.flowIds] : '*', site_ids: client.siteIds ? [...client.siteIds] : '*' },
    sql_configured: sql.configured(), sql_scope: 'All databases, tables and views readable by the dedicated account on the configured server. Each database is checked separately; no manual table list.',
    exceljs_module: fileURLToPath(new URL('node_modules/exceljs/excel.js', import.meta.url)),
    workflow: 'Trusted reference → independent download → exact comparison → reviewed Playwright recorded flow → actual succeeded run → verify_run outputs and SQL.',
    boundaries: ['Metronome application logic is unchanged.', 'ExcelJS compares evidence tables; Gemini owns workbook analysis and HTML generation. Microsoft Playwright MCP supplies independent browsing with actual download receipts.',
      'Gemini uses its own tools and the installed ExcelJS library for workbook and HTML work.',
      'SQL analysis uses only a dedicated reader. Direct API calls, shell access and native Metronome schedules are outside MCP enforcement.'],
  }));
  tool('get_version', 'Read the running Metronome version and connection availability.', {}, () => client.version());
  tool('list_catalog', 'Read existing allowed portal sites, reports and filter choices. Local file and Outlook are also supported Flow source types.', {}, () => client.catalog());
  tool('list_flows', 'Find existing flows to reuse before proposing a new one.', {}, () => client.listFlows());
  tool('get_flow', 'Read one scoped flow definition, owner and managed folder.', { flow_id: id }, a => client.flowDetails(a.flow_id));
  tool('get_flow_schema', 'Read FlowWrite from the running Metronome OpenAPI schema before drafting a flow. The current API validates the saved definition.', {}, () => client.flowSchema());
  tool('list_recordings', 'List recording revision IDs without browser state or entered values.', { flow_id: id }, a => client.recordings(a.flow_id));
  tool('list_runs', 'Read bounded run status history for an allowed flow.', { flow_id: id, limit: z.number().int().min(1).max(100).default(20) }, a => client.listRuns(a.flow_id, a.limit));
  tool('get_run', 'Read the status of one run after verifying access to its flow. Raw browser logs are not exposed.', { run_id: id }, a => client.getRun(a.run_id));
  const database = z.string().min(1).max(63).optional().describe('Database on the configured SQL server. Omit to use the starting database; discover names with list_sql_databases.');
  tool('list_sql_databases', 'Discover databases the reader can connect to. Each selected database must separately pass read-only privilege checks. If the starting database is unavailable, ask for one existing database name; never request passwords in chat.', { database }, a => sql.databases(a.database));
  tool('get_sql_schema', 'Discover readable tables/views and their columns using the dedicated reader. No table list is required. Follow next_offset for additional pages; optional schema/table filters narrow discovery. Never uses upload credentials.', {
    database,
    schema_name: z.string().min(1).max(256).optional(), relation_name: z.string().min(1).max(256).optional(),
    offset: z.number().int().min(0).max(1000000000).default(0), limit: z.number().int().min(1).max(2000).default(2000),
  }, a => sql.schema(a));
  tool('query_readonly', 'Run one bounded SELECT over any schema.table readable by the dedicated database account. Use aggregates for completeness; row results can be truncated. Writes and unsafe functions are blocked.', {
    database,
    sql: z.string().min(1).max(20000), params: z.array(z.union([z.string(), z.number(), z.boolean(), z.null()])).max(100).default([]),
    limit: z.number().int().min(1).max(2000).default(1000),
  }, a => sql.query(a.sql, a.params, a.limit, a.database));
  const definitionJson = z.string().min(2).max(40000).describe('JSON object with the exact flow definition shown to the user.');
  const proposalId = z.string().regex(/^[a-f0-9]{64}$/);
  const parseDefinition = value => {
    try { return JSON.parse(value); } catch { throw new Error('Flow definition must be valid JSON.'); }
  };
  const evidenceId = z.string().uuid();
  const tableSelection = z.object({ sheet: z.string().min(1).optional(), range: z.string().min(1).optional() }).strict();
  const referenceSpec = z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('file'), path: z.string().min(1), sheet: z.string().optional(), range: z.string().optional() }).strict(),
    z.object({ kind: z.literal('sql'), database: z.string().min(1), schema: z.string().min(1), table: z.string().min(1) }).strict(),
  ]);
  tool('begin_reference', 'Capture the owner-supplied correct table before source investigation. XLSX requires a sheet and table range, e.g. A1:C* for all remaining rows in those columns. Never silently omit tables, columns or subsidiaries. Formula references require a verified values-only copy.', {
    reference: referenceSpec,
    source: z.object({ type: z.enum(['portal', 'file', 'outlook']), site_id: id.optional(), report_url: z.string().url().optional() }).strict(),
    output_table: tableSelection,
  }, a => evidence.begin(a.reference, a.source, a.output_table), false);
  tool('compare_download', 'Compare actual independently acquired data with the trusted reference. Portal sources require a genuine report-browser download_id, acquired after begin_reference. Full differences are saved privately. Unreconciled data cannot unlock flow authoring.', {
    case_id: evidenceId, download_id: evidenceId.optional(), source_file: z.string().optional(),
  }, a => evidence.compareDownload(a.case_id, a.download_id, a.source_file), false);
  tool('propose_flow', 'Prepare a complete flow definition for review without calling a mutating Metronome API. Omit flow_id to propose a new flow; include it to update an existing one.', {
    definition_json: definitionJson, flow_id: id.optional(), evidence_id: evidenceId,
  }, a => proposals.prepareFlow(parseDefinition(a.definition_json), a.flow_id ?? null, a.evidence_id));
  tool('get_proposal', 'Read the saved proposal, full definition and receipt after a pause or connection failure.', { proposal_id: proposalId }, async a => proposals.review(await proposals.read(a.proposal_id)));
  tool('apply_flow_proposal', 'Save exactly the flow definition shown in confirmation_json. Requires the user’s interactive Gemini tool confirmation. May activate a native schedule if enabled in the reviewed definition.', {
    proposal_id: proposalId, confirmation_json: definitionJson,
  }, a => proposals.execute(a.proposal_id, parseDefinition(a.confirmation_json), 'save_flow'), false);
  tool('propose_run', 'Read a flow and prepare an execution proposal. Reuse request_id for retries of the same intended run; use a new ID only for a separately intended run.', {
    flow_id: id, request_id: z.string().min(1).max(120), evidence_id: evidenceId,
  }, a => proposals.prepareRun(a.flow_id, a.request_id, a.evidence_id));
  tool('run_flow', 'Submit one previously reviewed run. May download files and write SQL according to the full definition. Requires interactive Gemini confirmation; queued does not mean succeeded.', {
    proposal_id: proposalId, confirmation_json: definitionJson,
  }, a => proposals.execute(a.proposal_id, parseDefinition(a.confirmation_json), 'run_flow'), false);
  tool('propose_recording', 'Review a native Playwright recorder action. draft creates a disabled recorded portal flow without a detected-controls scan; start opens its recorder; finish saves captured actions; cancel discards unsaved capture. Control the real recorder using available computer-control tools; pause for assistance if control fails. Reuse request_id after interruption.', {
    action: z.enum(['draft', 'start', 'finish', 'cancel']), evidence_id: evidenceId,
    flow_id: id.optional(), scan_id: id.optional(), name: z.string().min(1).max(160).optional(), request_id: z.string().min(1).max(120),
  }, a => proposals.prepareRecording(a.action, a.evidence_id, { flowId: a.flow_id ?? null, scanId: a.scan_id, name: a.name, requestId: a.request_id }));
  tool('apply_recording_proposal', 'Perform exactly the reviewed recorder action through Metronome API. Requires native user confirmation. Never submits fabricated steps, code or selectors.', {
    proposal_id: proposalId, confirmation_json: definitionJson,
  }, async a => {
    const record = await proposals.read(a.proposal_id);
    if (!record.proposal.kind.startsWith('recording_')) throw new Error('Not a recording proposal.');
    return proposals.execute(a.proposal_id, parseDefinition(a.confirmation_json), record.proposal.kind);
  }, false);
  tool('verify_run', 'Verify the submitted run from its durable proposal receipt. Fetch actual terminal status and API-listed output files, compare every declared table and the reviewed full SQL target using the reader. Only succeeded execution plus exact data can return SUCCESSFUL. Samples, missing evidence and differences cannot.', {
    proposal_id: proposalId,
  }, async a => evidence.verify(await proposals.read(a.proposal_id)), false);
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { await createServer().connect(new StdioServerTransport()); }
  catch { console.error('Metronome MCP could not start. Check the extension configuration and run npm ci in the extension directory.'); process.exitCode = 1; }
}
