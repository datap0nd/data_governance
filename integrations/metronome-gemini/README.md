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

The installer installs the locked Node dependencies and links **only this
extension** into Gemini. It does not edit Metronome's application, installer,
services, database or your other Gemini settings. If your PowerShell policy
does not permit the script, use the equivalent commands instead of changing it:

```powershell
npm.cmd ci --ignore-scripts --prefix .\integrations\metronome-gemini
gemini extensions link .\integrations\metronome-gemini
```

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
gemini extensions config metronome-gemini
```

The extension declares these settings; Gemini stores the sensitive reader DSN
in its system keychain. Do not paste passwords into the conversation or commit
them to files. Restart Gemini after changing settings.

| Setting | Default and purpose |
| --- | --- |
| Metronome local URL | Blank uses `http://127.0.0.1:8000`. Loopback origins only. |
| Flow scope | Blank or `*` allows your installation's flows. Comma-separated IDs restrict existing flows and disable creating new ones outside that list. |
| Portal site scope | Blank or `*` allows all portal sites plus file/Outlook sources. Comma-separated site IDs restrict portal operations. |
| Read-only PostgreSQL connection | Optional `postgresql://reader:password@host/database`. URL-encode special characters in credential components. Blank disables SQL analysis. Use your approved TLS parameters where required; certificate verification is never disabled by the extension. |
| Readable SQL relations | Comma-separated `schema.table` names. Empty disables SQL analysis. Both the DSN and relations are required. |

Environment variables with the names in `gemini-extension.json` may also be
provided by the operator. The adapter never reads `PGPASSWORD`,
`DG_UPLOAD_PG*`, Metronome's configuration files or stored browser state.

The reader supplies explicit identity, port and password settings, including an
empty password when omitted; it does not inherit `.pgpass` or ambient database
credentials. DSN parameters are limited to `sslmode` (`disable`, `require`,
`verify-full`), `sslrootcert`, `sslcert` and `sslkey`. TLS certificate verification
cannot be disabled through these parameters.

The reader must have SELECT privileges and no effective write, object ownership,
schema-creation or elevated role privileges. The tool rejects unsuitable accounts
before analysis, even if the session could otherwise be put in read-only mode.
Have the database owner provision the reader and grants; this extension does not
alter roles. A PostgreSQL 14 database granting CREATE on `public` to PUBLIC may
need that grant corrected by its owner before the reader passes. Only explicitly
allowlisted relations can be queried; catalog inspection uses the same reader.

The browser connection is Microsoft's pinned
[@playwright/mcp](https://github.com/microsoft/playwright-mcp), launched by Gemini
with an isolated Edge profile. It does not connect to a worker's browser or
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
  ExcelJS for .xlsx analysis through its normal tools; there is no custom Excel
  reader or prescribed extraction script. ExcelJS does not recalculate formulas
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
recording UI; independent portal browsing can help discover the required report.

## Review, execution and recovery

Gemini prepares a flow, shows its complete effective definition and calls
`apply_flow_proposal` with that same definition in `confirmation_json`. Its native
tool confirmation is the approval interaction; the extension policy requires
`ask_user` for saves and runs. The model is not given an `approve` tool. A flow
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
See the release [test plan](../../docs/testing/releases/2026-09-14-gemini-reporting-extension/test-plan.md)
and [test report](../../docs/testing/releases/2026-09-14-gemini-reporting-extension/test-report.md).

## Update or remove

After updating the checkout from `main`, rerun the installer and restart Gemini
so locked dependencies and tool discovery match. Existing proposal receipts stay
in the user profile. To remove the integration:

```powershell
gemini extensions uninstall metronome-gemini
```

Keep reporting outputs and receipts until no longer needed. Uninstalling the
extension does not delete or disable flows already saved in Metronome.
