# Metronome for Gemini CLI

Open Gemini in your reporting folder and use:

```text
/metronome
/html_replicate "C:\Reports\출장보고"
```

`/metronome` inspects and works with flows. `/html_replicate` understands the
reference folder, asks business questions, uses Metronome when needed and builds
standalone HTML with CSV downloads. You can use `/html_replicate` alone; it loads
the Metronome skill when necessary. Send slash commands as separate messages;
text after a command is its argument, not a second command invocation.

## Install once on the computer running Metronome

Requires Gemini CLI **0.59.0 or newer**, Node.js **22 or newer**, and Edge for the
bundled browser connection. Start with the current Metronome checkout from
GitHub `main`. From that checkout in PowerShell:

```powershell
.\integrations\metronome-gemini\install.ps1
```

The installer installs the locked Node dependencies and registers **only this
extension** with Gemini. It updates existing copies and reuses existing links.
It does not edit Metronome's application, installer, services or database.

Setup uses the local Metronome service automatically and asks for SQL details
one at a time: **server → read-only username → masked password**. The default
SQL port is 5432; enter `server:port` only if yours differs. Leave the server
blank to skip SQL. Gemini's usual extension trust prompt may appear on install.
There is no connection string, Metronome URL or table list to fill in.

Restart Gemini outside the application checkout:

```powershell
gemini --model gemini-3.5-flash
```

Use `/extensions list`, `/skills` and `/mcp` to verify discovery, then
`/metronome` to check the local API. The default Metronome address is
`http://127.0.0.1:8000`. The reporting folder can be passed to the command even
if Gemini was opened elsewhere. A workspace command with the same name can
override an extension command; remove or rename that conflicting command if
Gemini does not load the expected skill.

## Model choice

**Gemini 3.5 Flash** is the default recommendation for this tool-heavy workflow.
Google reports stronger agentic, MCP and coding performance than 3.1 Pro in its
[3.5 release comparison](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/).
This is a recommendation based on that evidence, not a model benchmark performed
on your reports. Gemini 3.1 Pro (`gemini-3.1-pro-preview`) remains compatible.
The extension does not change your global model or model-provider settings.

## Configure access

```powershell
.\integrations\metronome-gemini\install.ps1
```

Rerun setup to correct a field, then restart Gemini. Completed settings survive
an interruption. Password input and storage use Gemini's native masked prompt
and system keychain; do not paste passwords into the conversation. Usernames,
passwords and database names do not need URL encoding.

`/metronome` discovers the databases accessible to the reader on that server.
Gemini selects the relevant database from the report context or asks when the
choice is ambiguous. Tables and views are discovered from database permissions,
including newly granted tables, without a manually maintained table list.
Queries open one database at a time; comparisons across databases use separate
reads. Discovery initially connects to `postgres`. If that database is missing
or inaccessible, Gemini asks for one existing database name and retries with it.
Each database must separately pass the read-only privilege checks.

Metronome's default address is `http://127.0.0.1:8000`, matching its installer
and worker configuration. Existing local URL and flow/site restrictions are
preserved in `%USERPROFILE%\.gemini\metronome\access.json`; passwords never go
there. A new setup allows the installation's flows/sites. Operators can retain
narrower access through that public file or explicit `METRONOME_BASE_URL`,
`METRONOME_FLOW_IDS` and `METRONOME_SITE_IDS` environment variables. Only local
origins are supported. A stopped Metronome service does not prevent finishing
setup or analyzing supplied files.

Explicit `METRONOME_SQL_HOST`, `METRONOME_SQL_USER`, `METRONOME_SQL_PASSWORD`
and optional `METRONOME_SQL_DATABASE` environment settings are also supported.
The adapter never imports `PGPASSWORD`, `DG_UPLOAD_PG*`, `.pgpass`, Metronome's
configuration files or browser state. Separate setup fields take precedence over
an explicitly supplied legacy `METRONOME_READONLY_DSN`. Operators requiring TLS
can continue to use that explicit DSN with `sslmode` (`disable`, `require`,
`verify-full`), `sslrootcert`, `sslcert` and `sslkey`; omit the individual host
setting in that case. Certificate verification cannot be disabled through these
parameters. The three-field setup uses PostgreSQL's default port without TLS.

The reader must have SELECT privileges and no effective write, object ownership,
schema-creation or elevated role privileges. The tool rejects unsuitable accounts
before analysis, even if the session could otherwise be put in read-only mode.
Have the database owner provision the reader and grants; this extension does not
alter roles. A PostgreSQL 14 database granting CREATE on `public` to PUBLIC may
need that grant corrected by its owner before the reader passes. All tables/views
granted to that reader can be queried; catalog inspection uses the same reader.
Database-wide read access does not permit writes, role changes, multiple SQL
statements or unsafe functions. To cover future tables, the database owner must
also provide the appropriate future-object grants; this extension never grants
itself access.

The browser connection is Microsoft's pinned
[@playwright/mcp](https://github.com/microsoft/playwright-mcp), launched by Gemini
with an isolated Edge profile. A thin adapter records completed download
receipts and restricts the exposed tools; browser interactions remain Microsoft MCP operations. It does not connect to a worker's browser or
reuse its cookies. Sign in yourself when the new browser session requires it.
Only ordinary navigation, interaction, screenshots and snapshots are exposed;
network dumps, file uploads and arbitrary JavaScript evaluation are omitted.
The model must still follow the source scope you authorize. This browser's
interaction policy is not a database or portal-side read-only guarantee.

## What is included

- Two custom command TOMLs, two concise skills and a small context file.
- A stdio MCP using the official SDK, an explicit existing-API adapter, read-only
  SQL queries, and a local proposal/receipt store.
- Microsoft's browser MCP and the standard ExcelJS library. Gemini can use
  ExcelJS for .xlsx analysis through its normal tools. There is no user-run
  extraction script; the MCP internally uses ExcelJS for fixed evidence comparisons. ExcelJS does not recalculate formulas
  and does not read legacy .xls or decrypt protected workbooks.

The Metronome tools list catalogs/flows/recording revisions, inspect definitions
and schemas, propose/save flows, propose/submit runs, inspect status and read
SQL. They do **not** expose arbitrary API paths, credential management, worker
claims, application updates, raw browser state, transformation scripts, email
delivery or view refresh. Existing flows using those last three execution steps
must be managed in Metronome rather than executed through this adapter.

Outlook means Metronome's existing Inbox attachment source. There is no new
Outlook client or general email search service. Gemini can start with attachments
already supplied in the folder, create an existing-API Outlook flow, or explain
which additional access is needed. New portal recordings use Metronome's existing
recording API to start/finish/cancel the native Playwright recorder. Gemini
controls that window with its available computer-control tools and pauses for
owner assistance if control fails. The isolated report-browser session is used
for the independent download, not as a replacement recorder.

## Mandatory reference-to-result workflow

For each requested flow, Gemini captures the correct supplied reference table,
independently navigates the source and downloads its data, and compares it before
proposing any Metronome changes. Portal comparisons require a real completed
`report-browser` download receipt captured after the reference. File/Outlook
comparisons accept independently supplied files; their acquisition provenance
remains the agent's responsibility. Unresolved differences block authoring.

Only explicitly **recorded** portal definitions are accepted. New routes can
use `propose_recording` / `apply_recording_proposal` to create a disabled draft
without detected-controls discovery. Saved revisions stay in Metronome; no
model-authored selectors, recorder definitions or privileged scripts are exposed.

`propose_flow` and `propose_run` require the comparison evidence ID. The adapter
checks it again before dispatch and binds saved recording content to the review.
`verify_run` follows the actual submitted run receipt, requires `succeeded`, and
reads API-listed final outputs and the reviewed SQL target. It verifies the
reader endpoint matches the run's SQL endpoint. It never edits run history.

A result is `SUCCESSFUL` only when the declared tables match exactly, including
columns, scalar values, duplicate multiplicity and blanks. Row order is ignored.
`DATA_DIFFERENCES` reports missing/extra rows without assuming refresh; full
row differences stay in a private evidence file. Counts alone cannot pass.

Verification is bounded: 50,000 combined rows, 256 columns, 20 MiB per file/value
set, and the existing SQL 4 MiB/30-second limits. A limit breach remains incomplete.
XLSX uses explicit sheet/table ranges; `A1:C*` includes appended rows in those
columns. Multiple tables need separate declared scopes. Never claim whole-file
completeness from a narrowed range. Formula references need verified values-only
copies. No rounding, date reinterpretation, filtering or blank normalization is
silently applied to make values match. SQL scans cover all rows visible to the
reader, including its row-level security, at verification time; they are not a
frozen snapshot of the load transaction. Target column inference/schema drift
and native schedules remain the unchanged application's behavior.

Private source copies, snapshots and comparisons live in
`%USERPROFILE%\.gemini\metronome-evidence`. They contain business data and must
stay out of Git and shared diagnosis bundles. Keep the evidence IDs, scopes and
proposal/run IDs in the reporting workspace. These files are not protected from
Gemini's unrestricted shell under the same Windows identity.

If an independent download is interrupted, do not use its partial file. The
pinned upstream browser MCP can disconnect on a failed transfer. Restart its
connection using Gemini's MCP controls and retry the download; completed evidence
and proposals remain on disk. No browser restart retries a Metronome mutation.

## Review, execution and recovery

Gemini prepares a flow, shows its complete effective definition and calls
`apply_flow_proposal` with that same definition in `confirmation_json`. Its native
tool confirmation is the approval interaction; the extension policy requires
`ask_user` for saves, recorder actions and runs. The model is not given an `approve` tool. A flow
proposal is not an execution: new flows default to disabled/manual, and SQL table
creation happens through Metronome's normal loader only when the flow runs.
Inspect load mode and destination before confirming a run, including whether a
missing table may be created or existing data replaced. The loader's inferred
column schema is not frozen by this external adapter.

Proposal/receipt files live in `%USERPROFILE%\.gemini\metronome-proposals`, outside
the repo. A confirmation must match the stored definition. The adapter rechecks
the current flow before updating or running it. Repeated submission of a completed
proposal returns its receipt instead of submitting again. Use a stable request ID
for a particular intended run. Creating a different proposal is not a recovery
strategy for an uncertain outcome.

If a request loses its response, the proposal becomes uncertain and cannot be
resent automatically. Inspect the flow list/run history and the existing receipt
first. A process crash can also leave a `.lock` file. After confirming the process
is stopped and reconciling whether the operation happened, the owner can archive
that specific proposal/lock and prepare a new reviewed request if still needed.
Never delete all receipts as a way to make a retry work.

**Enforcement boundary:** user/admin Gemini policies can override extension
confirmation rules. Do not enable YOLO or permanent approval for these writes.
The unchanged Metronome API does not authenticate an agent separately and has no
conditional revision-write endpoint. Therefore a concurrent UI/API edit between
the adapter's check and submission cannot be ruled out atomically. Avoid editing
the same flow concurrently. Native scheduled runs, direct API/UI operations,
shell access and altered policy/configuration are outside MCP enforcement. This
is not an application-wide approval system.

Scheduling uses the existing daily/weekly/monthly capabilities in Dubai time.
Fortnightly scheduling is not silently mapped to weekly: agree on manual runs or
an external scheduler separately. Gemini generates HTML in your output folder;
Metronome does not host, generate or schedule that HTML.

## Verify with fictional inputs

Create a separate folder containing two Korean .xlsx reference reports with
multiple tables on one sheet, merged headers, amounts, periods and subsidiaries.
Supply only some of their source files. Run `/html_replicate "<that folder>"`.
Gemini should identify missing inputs, ask about ambiguous date definitions,
preserve formulas/cached-result distinctions, propose scoped flows without
automatically running them, and produce a report with Korean filters and matching
CSV exports. Decline a proposed save and confirm that no flow was submitted.

For repeatable automated integration tests:

```powershell
npm.cmd test --prefix .\integrations\metronome-gemini
```

These tests use synthetic API/SQL fixtures and the real MCP transport. PostgreSQL
privilege enforcement is also exercised against disposable databases in CI.
They do not claim that a model has reproduced your actual business-trip reports.
See the workflow release [test plan](../../docs/testing/releases/2026-09-15-gemini-evidence-first/test-plan.md)
and [test report](../../docs/testing/releases/2026-09-15-gemini-evidence-first/test-report.md).

## Update or remove

After updating the checkout from `main`, rerun the installer and restart Gemini
so locked dependencies and tool discovery match. Existing proposal receipts stay
in the user profile. To remove the integration:

```powershell
gemini extensions uninstall metronome-gemini
```

Keep reporting outputs and receipts until no longer needed. Uninstalling the
extension does not delete or disable flows already saved in Metronome.
