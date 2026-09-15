---
name: metronome
description: Work with Metronome's existing source catalogs, flows, recordings, run history and scoped read-only SQL. Use for /metronome or when a report needs repeatable ASAP, GSCM, Outlook or local-file acquisition.
---

# Metronome

Use the connected `metronome` MCP. First read `capabilities` and `get_version`;
discover the actual tool names instead of assuming a prefix. If the server is
missing, give the installation instructions in the extension README. If the
local app is unavailable, continue work on supplied files and preserve the next
API action until the connection is restored.

## Mandatory sequence for each flow

1. Treat the owner's sample files/tables as the correct reference. Inspect their
   real headings, separate tables, subsidiaries, periods, grain, units, blanks
   and formulas using standard workbook tools. Ask only the question blocking
   progress (for example booking date versus travel date). Preserve originals.
2. Call `begin_reference` with the exact trusted table and intended source.
   XLSX needs a sheet and range: `A1:C*` includes all remaining rows in those
   columns; an explicit end row is only that declared table, never a whole-file
   completeness claim. Include every business column. Use separate cases for
   separate inputs/tables. Formula caches are not freshness evidence: request
   a verified values-only copy if the reference contains formulas.
3. Independently navigate ASAP/GSCM using `report-browser`, select the real
   subsidiary/date options and download the actual export. This is a manual
   source exploration, before any Metronome replay. Use the returned completed
   `download_id` with `compare_download`. If still downloading, use a later
   browser snapshot; interrupted downloads must be retried. A supplied file or
   existing flow output cannot be labelled an independent portal download.
   For file/Outlook sources, independently obtain the source attachment/file
   using authorized tools and supply `source_file`; identify that provenance
   honestly. There is no bundled Outlook exploration client.
4. Resolve DATA_DIFFERENCES before authoring. Inspect the private evidence file
   for actual missing/extra rows and counts; never attribute them to refresh
   without evidence or replace the trusted reference on your own. Sampling,
   count-only matches and guessed mappings do not unlock proposals.
5. Search `list_flows`, `list_catalog` and `list_recordings` for reuse. Catalog
   reads discover routes, not a reason to use detected controls. Read
   `get_flow_schema`. For a new portal route without a catalog report, use
   `propose_recording(action=draft)` then `apply_recording_proposal`: it creates
   a disabled recorded draft through the existing API without a detection scan.
6. Use `propose_recording(action=start)` and its reviewed apply tool to open
   Metronome's real Playwright recorder. Operate that window using available
   computer-control tools. If unable, preserve the draft/session and pause for
   owner assistance. Never substitute the isolated report-browser window,
   detected controls, invented selectors or hand-written recording definitions.
   Finish through the recording API and poll `list_recordings` for the saved
   revision. Cancellation is scoped to that session. Reuse suitable existing
   recorded revisions through their actual flow, without inventing IDs.
7. `propose_flow` binds the comparison evidence ID, current definition and saved
   recording revision. Portal flows require `execution_method: "recorded"`.
   Select `recording_revision_id` from that flow's API results. Review sources,
   full selections, output, SQL database/schema/table, load behavior and schedule
   with the owner before applying. Start disabled/manual. Do not use partial
   defaults to turn SQL loading or scheduling on without review.
8. `propose_run` → reviewed `run_flow` → poll `get_run` to terminal status →
   `verify_run` using the run proposal ID. The tool reads API-listed artifacts
   and, when SQL is enabled, the exact reviewed target with the dedicated reader.
   All output tables are compared, including duplicate multiplicity and blanks.
   Fixed output ranges can hide newly appended rows: use an open-ended row range
   for a flat export. Never narrow the comparison to make a mismatch disappear.
9. Finish only with real `succeeded` execution and `verify_run` evidence. Report
   `SUCCESSFUL` only on exact data reconciliation within the declared scope.
   Otherwise report `DATA DIFFERENCES` or `INCOMPLETE`, with exact missing/extra
   counts and evidence. A newer extract may be valid but is not a 100% match.

All Metronome reads/writes use this MCP API. Never access governance.db,
SQLite backups/journals, ORM helpers, worker endpoints or application scripts;
never repair Run history or set statuses manually. No shell/API fallback after
an MCP denial. Missing capabilities require a concise blocker and next action.

## SQL analysis

Use only `query_readonly` for database analysis. It requires a dedicated reader;
all tables and views granted to that account are available without registration.
Never use Metronome's upload credentials,
its SQL catalog refresh endpoint, a worker endpoint, or an alternate shell
connection when the reader rejects access. A blocked privilege check means the
account must be corrected by its owner; do not change grants yourself.

Start with `list_sql_databases`, then `get_sql_schema` with the selected database.
Follow `next_offset` until null; schema and table filters can narrow large catalogs.
Use discovered names/types before guessing identifiers. Each database is checked
with the same reader credentials and read-only privilege checks. Keep database
names in evidence and pass `database` on subsequent queries. Cross-database joins
require separate reads and a local comparison; a PostgreSQL connection opens one
database at a time. If the starting database cannot be opened, ask for one existing
database name. Never request a password or connection string in chat; direct the
user to the extension installer for credential setup.

Qualified relations, safe aggregates, joins, subqueries and non-recursive read
CTEs are supported. Use parameters for values. Use grouped counts, min/max dates,
null/blank counts, duplicate-key groups and totals for whole-source checks. A
bounded row sample is not proof of completeness. Derive the expected subsidiaries
and periods from an agreed list or reference, not just the values present in SQL.

Record each comparison's source/flow/run, acquisition time, reporting period,
subsidiary, grain, keys, currency/unit, filters and transformations. A manual
download and its SQL target need compatible snapshots before totals can be
compared. Report mismatches and unresolved mappings without guessing their cause.

## Flow changes

Present a concrete proposal before applying a change: name, source/report,
subsidiary/period selections, worksheet(s), SQL target, load mode, enabled state
and schedule. Explain whether the SQL loader can create a missing target and
whether the chosen load mode replaces existing data. Start new flows disabled
and manual unless the user explicitly requests activation or scheduling.

The existing API also requires a current Metronome SQL catalog: append targets
must already be cataloged; creating a new table uses replace mode within a
cataloged schema. The dedicated reader's database/schema/table names help map
the target, but do not update Metronome's cached catalog. If that catalog is
missing or stale, explain the API rejection and ask the owner to resolve it in
Metronome. Never work around it with the upload-backed catalog refresh endpoint.

Use the proposal and run tools when available. Preserve their proposal IDs and
fingerprints in the reporting workspace. The user's Gemini tool confirmation is
required for a write; never bypass it with a shell request, an alternate client,
YOLO mode, or an invented `approved=true`. Re-read changed definitions and obtain
a new review. Scripts, email delivery and post-SQL view refresh are deliberately
outside this MCP's write surface; the user can configure them in Metronome.

Metronome currently supports manual, daily, weekly and monthly flow schedules
in Dubai time. A request for every two weeks needs an explicitly agreed external
scheduler or manual invocation; never silently substitute weekly or change the
app's scheduling logic. HTML regeneration remains Gemini's responsibility.

After a write, inspect the returned ID and run status. A queued run is not a
successful load. On an ambiguous connection failure, inspect current flows/runs
before any retry; a new proposal is not a workaround for an uncertain prior one.
Do not change Metronome's code or database to repair a rejected request.

## Scope of enforcement

Skills are guidance. The MCP checks scopes and SQL operations; Gemini's tool
policy handles interactive write confirmation. The unchanged Metronome API has
no revision-conditional writes or agent-specific authorization. Direct API/UI
changes, native scheduled runs and unrestricted shell access are outside this
integration's enforcement. Do not describe it as an application-wide guarantee.

## Response format

Use one compact row per requested flow: name | flow/run IDs | actual execution
status | exact match or missing/extra counts | evidence reference. Add only
blocking questions or necessary differences. Do not finish at “flow created”,
“queued” or “should work”. Keep cases and proposal IDs for all requested flows
until every input has a successful run and an honest reconciliation result.
