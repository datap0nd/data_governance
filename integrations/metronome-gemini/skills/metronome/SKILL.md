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

## Discover before creating

- Search `list_catalog` and `list_flows` for the topic and likely business terms,
  including the Korean headings. Existing reports expose filter keys and choices.
  Reuse a suitable flow or recording before proposing duplicates for every sub.
- `get_flow` provides the current definition; `list_recordings` provides revision
  IDs without private browser state. Creating a new browser recording still uses
  Metronome's existing recording journey. Independent manual portal checking uses
  a separately connected browser tool, not a flow replay labelled as independent.
- `get_flow_schema` is the running API contract. Build definitions from it:
  portal flows use catalog site/report IDs; `file` uses an absolute source path
  and explicit worksheet choices; `outlook` uses the existing Inbox subject and
  attachment behavior. Do not promise arbitrary mailbox search that is absent
  from the connected tools. Ask the user to identify a source only after checking
  the supplied folder and available catalogs.
- A saved flow can affect files, SQL and schedules. A request to investigate data
  is not permission to run an existing production flow as an experiment.

## SQL analysis

Use only `query_readonly` for database analysis. It requires a dedicated reader
and configured `schema.table` allowlist. Never use Metronome's upload credentials,
its SQL catalog refresh endpoint, a worker endpoint, or an alternate shell
connection when the reader rejects access. A blocked privilege check means the
account must be corrected by its owner; do not change grants yourself.

Use `get_sql_schema` to discover the database name and column names/types within that allowlist before
guessing SQL identifiers. It uses the same reader and privilege checks.

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
