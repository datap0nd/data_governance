# Data Governance Panel

A web-based panel that monitors your Power BI reports, tracks data sources, and validates data quality.

## Quick Start (Windows)

### 1. Install Python

Download Python 3.11+ from [python.org](https://www.python.org/downloads/). During install, check "Add Python to PATH".

### 2. Set up the project

Open a terminal (PowerShell or Command Prompt) in this folder:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure the Power BI workspace

Production scans read the configured workspace directly instead of relying on
a possibly stale local PBIX/TMDL export. Set `DG_PBI_WORKSPACE` to the Power BI
workspace name and connect the saved Microsoft account from **Scanner > Connect
Power BI**. An explicitly supplied reports path remains available for tests and
manual development scans only.

### 4. Run the app

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open your browser to `http://localhost:8000`

For other people on the network to access it, they go to `http://YOUR_COMPUTER_IP:8000`

### Access

All registered users have the same application access, including remote PCs on the network. There is no app-level admin allowlist or IP toggle.

### Automatic main updates

Production installs watch the GitHub `main` branch every five minutes by
default. When a new commit appears, Metronome first waits for that exact
commit's GitHub Actions **Tests** workflow to pass. It then pauses new work
starts, lets active scans, Flows, Pipelines, AI runs, Power BI work, and Outlook
hand-offs finish, and asks one pre-registered elevated Windows task to install
that exact 40-character commit. The updater stages and compiles the release, proves
its dependencies can install offline, takes a WAL-aware SQLite backup and a
code snapshot, restarts the existing services without changing their service
identity, and verifies `/api/version`. A failed health check restores the prior
code and database snapshot. The updater retains the current and two prior
attempt snapshots; a host power loss or forced process kill during replacement
can still require manual recovery from those artifacts.

Run `setup.ps1` interactively once after deploying this version so it can
register the fixed `Metronome_Auto_Update` task using the existing service
account. After that, use **System > Updates** to enable/disable automatic
installs, force a check, install manually, or inspect blockers and the latest
durable attempt. Private-repository installs must expose `DG_GITHUB_TOKEN` to
the service account. Automatic installation deliberately refuses a folder
containing `.git`; develop and merge in a Git working copy, then run the service
and `setup.ps1` from a separate release-style folder without `.git` so local
source changes cannot be overwritten.

Ordinary setup and updates preserve the existing ASAP/GSCM browser sessions and
do not stop for interactive website authentication. Run
`setup.ps1 -AuthenticateFlows` only when those portal sessions intentionally
need to be refreshed. Optional XMLA/TOM build failures are written to
`logs\pbi_metadata_build.log`; the installation continues with Fabric
`getDefinition` instead.

Ordinary updates also preserve the existing Windows service identity instead
of asking for the account password again. If Windows reports a service logon
failure, run `setup.ps1 -ResetServiceCredentials` once and enter the Windows
account password, not a Windows Hello PIN.

### Read-only Operations Investigator

**Alerts is the operational inbox.** Every active Alert evidence revision is automatically queued for an overall read-only review. Expanding an Alert shows whether the current evidence confirms, likely supports, contradicts, or is insufficient to judge the Alert, followed by a concise explanation and suggested next step. Flow and Pipeline failures also retain immutable run occurrences and optional exact-run analysis under evidence history.

Overall analysis is pinned on the server to the Alert and its current evidence revision. The bounded Qwen3.8-27B tool loop receives a redacted operational neighbourhood for the linked source, report, Flow, check, scanner state, and exact run when one exists. It never receives credentials, connection strings, raw query/file contents, local paths, or email recipients. When a current assessment is complete, its conclusion and first suggestion are included in the Outlook Alert summary; if Qwen is pending or unavailable, the deterministic Alert email still goes out and says so explicitly. If the Alert gains newer evidence, is resolved, or is marked expected, the old analysis becomes historical and cannot supply current email advice.

The investigator cannot run, resume, retry, stop, refresh, edit, publish, browse, execute SQL, access the shell, change an Alert, or send email. Its only writes are its own durable run/step/evidence records. A recommendation such as Resume or Retry SQL is text only and is accepted only when the shared server preflight says it is currently eligible; the normal operational control revalidates again before queueing anything.

Without a configured model endpoint, Metronome shows a clearly labelled deterministic preview using recorded state and recovery preflight only. To connect Qwen through an OpenAI-compatible vLLM/SGLang endpoint, set:

```text
DG_AI_API_URL=http://QWEN_HOST:8000/v1
DG_AI_MODEL=Qwen/Qwen3.8-27B
DG_AI_PROVIDER_PROFILE=qwen_vllm
DG_AI_REASONING_EFFORT=medium
```

The server must expose native OpenAI-compatible `tool_calls` with the Qwen reasoning and tool-call parsers enabled. Raw `<think>` or `<tool_call>` text is rejected rather than executed. Optional bounded tuning variables are `DG_AI_AGENT_MAX_TOOL_CALLS`, `DG_AI_AGENT_MAX_MODEL_TURNS`, `DG_AI_AGENT_MAX_SECONDS`, `DG_AI_AGENT_HTTP_TIMEOUT_SECONDS`, and `DG_AI_AGENT_MAX_OUTPUT_TOKENS`.

See [the AI agent plan](docs/ai_agent_plan.md) for the implemented boundary and later evaluation-gated modes.

### Unattended Power BI sync

The sync picks the first available auth mode, in this order:

1. **Service principal** (`DG_PBI_TENANT_ID` + `DG_PBI_CLIENT_ID` + `DG_PBI_CLIENT_SECRET`): fully unattended, requires tenant-admin setup.
2. **Saved Microsoft account (recommended when no tenant access exists)**: open the panel, go to Scanner, click **Connect Power BI**, and enter the shown code at microsoft.com/devicelogin from any device (your own laptop or phone works). After this one-time sign-in, every sync runs headless inside the app with a silently refreshed token: no PowerShell window, no account picker, and it works while the PC is locked or the RDP session is disconnected.
3. **Interactive fallback**: the legacy scheduled-task flow that opens a PowerShell window plus the Microsoft account picker in the sync user's session. Only used when neither of the above is configured.

The report scanner now acquires each live semantic model before changing the
local catalog. It first uses the bundled XMLA/TOM helper and falls back to the
Fabric `getDefinition` API in TMDL format. If either provider cannot produce a
complete workspace snapshot, the scan fails and retains the prior Metronome
catalog rather than publishing a partial result. The setup script extracts the
tested Windows helper included in each release, without contacting NuGet; a
local .NET build is only a fallback. The helper targets the Windows .NET
Framework already included with supported Windows 10/11 installations, so it
does not require the separate x64 .NET 8 runtime. Set `DG_PBI_TOM_HELPER` only
to use a different published helper path. The optional Fabric fallback requires the
saved account to have read-write access to the semantic model and delegated
`SemanticModel.ReadWrite.All` (or `Item.ReadWrite.All`) consent. Its timeout can
be changed with `DG_PBI_METADATA_TIMEOUT_SECONDS`.

#### Saved Microsoft account details

- The sign-in is stored in `pbi_token.json` next to the app, encrypted with Windows DPAPI for the account that runs the service. Treat the file as a credential; it is gitignored and `Disconnect account` on the Scanner page deletes it.
- The refresh token rotates on every sync, so daily syncs keep the sign-in alive indefinitely. If the org forces a re-auth (password change, revocation, or a conditional-access sign-in frequency policy), the sync fails with a clear "reconnect" message, raises a critical alert on the dashboard, and the Scanner page shows a **Reconnect needed** badge. Click Connect Power BI again to fix it; nothing hangs.
- The default sign-in client is Microsoft's first-party Azure CLI public app, pre-consented in nearly every tenant. If your tenant blocks it or blocks the device code flow, set `DG_PBI_PUBLIC_CLIENT_ID` (the Azure PowerShell client `1950a258-227b-4e31-a9cf-717495945fc2` is a common alternative).
- Proxy handling: the app resolves the outbound proxy automatically, in this order: `DG_PBI_PROXY`, standard `HTTPS_PROXY`/`HTTP_PROXY` env vars, then the Windows system proxy settings (including a configured PAC "setup script"), then direct. Set `DG_PBI_PROXY=http://proxyhost:port` only if auto-detection picks the wrong route; a `.pac` script URL itself is not a valid proxy value. Connect failures show which proxy was used.
- Usage sync (activity events) still requires the signed-in account to hold the Power BI/Fabric administrator role, same as before.

#### Interactive fallback (legacy)

Without a saved account or service principal, the sync needs an active, unlocked interactive session so the Microsoft account picker can be selected.

For RDP-heavy machines, setup installs an automatic RDP console guard. The guard runs every five minutes, also runs after RDP disconnect events, and runs once immediately before a Power BI sync starts. It targets the configured sync Windows user and transfers that user's disconnected RDP session back to the console with `tscon`, so the sync does not depend on every RDP user remembering a manual step.

This is a workaround for delegated interactive auth, not a secure headless design. Use it only on a controlled machine where leaving the sync user's desktop available at the console is acceptable.

Set the target sync user if the service account is not the same account used during setup:

```bash
DG_PBI_SYNC_WINDOWS_USER=<windows-user-name>
```

The guard only repairs disconnected sessions by default. It does not kick an actively connected RDP user. If the sync user is actively connected through RDP, that counts as ready because the account picker is visible in that session. If someone is actively using a different account at the sync time, the sync may still have to wait or fail cleanly. That case needs an operating rule, such as reserving the sync window or running the sync under a dedicated service account that users do not RDP into.

If multiple Microsoft accounts are cached, set this environment variable for the service account so the picker chooses the expected account:

```bash
DG_PBI_ACCOUNT=<user@example.com>
```

The sync scripts also check for a locked/disconnected session before interactive sign-in and record a failed sync instead of hanging overnight. If sign-in reaches the account picker but does not finish, the process is stopped after `DG_PBI_CONNECT_TIMEOUT_SECONDS`, defaulting to `120`.

The current RDP guard diagnostics are available at:

```text
http://localhost:8000/api/scanner/pbi-sync/status
```

Scheduled emails are blocked by default if the latest completed Power BI refresh sync is older than `DG_EMAIL_MAX_PBI_SYNC_AGE_HOURS` hours, defaulting to `24`. After the configured overall refresh time plus `DG_EMAIL_PBI_SYNC_GRACE_MINUTES`, defaulting to `30`, emails require that day's sync to have completed. If PBI is stale, the email is deferred and retried after `DG_EMAIL_PBI_STALE_RETRY_MINUTES`, defaulting to `30`. Set `DG_EMAIL_REQUIRE_FRESH_PBI=false` only if stale PBI metadata is acceptable.

If service-principal access is later approved, set `DG_PBI_TENANT_ID`, `DG_PBI_CLIENT_ID`, and `DG_PBI_CLIENT_SECRET` to run the sync without an interactive account picker.

Users can change the daily overall refresh time from System > Refresh Schedule. The job runs the Power BI refresh sync, report and lineage scan, PostgreSQL dependency and cron discovery, source probe, configured data-quality checks, governance checks, configured usage CSV import, and Power BI usage sync. The default can also be set with `DG_OVERALL_REFRESH_HOUR` and `DG_OVERALL_REFRESH_MINUTE`.

### Data quality checks

Tools > Data Quality manages read-only rules for governed sources. Row-count range and row-count-change rules use the source probe history. Null-rate, duplicate-key, and numeric value-range rules run against PostgreSQL using the existing read-only probe connection. Checks run automatically after every source probe and can also be run individually or as a group from the page.

A failed or errored check stores a result, opens one owned dashboard action, and creates an alert. A later passing result resolves that action and alert automatically. Disabling a check also closes its active incident while retaining the check and result history.

### Configurable report-download flows

Tools > Flows stores website discovery settings, the discovered report/filter catalog, flow selections, download behavior, filenames, folders, schedules, and execution timings in the local Metronome SQLite database. Repository code contains only the generic scanner and flow engine. Authentication credentials and report-specific configuration are not stored in Git.

For ASAP, choose the `ASAP` website type, the visible top-level menus to scan, and a weekend scan schedule. The BI desktop worker traverses those menus and discovers report paths, every visible export view, filters, control types, and available options. Scans run weekly by default and upsert the local catalog. If a later complete scan cannot find an existing report or filter, Metronome marks it stale instead of deleting it. Flow creation only permits currently discovered entries.

The same worker navigates through the signed-in portal, activates each exact export view selected by the flow, scopes prompt controls inside ASAP's `content-frame`, applies week members in `YYYYWW` form when the view has a week prompt, and selects CSV or Excel in MicroStrategy's export window. Raw-data export views without a RUN prompt are exported from their rendered table information menu. A transient report-load HTTP 500 is retried once by returning through the portal menu. Opaque MicroStrategy object IDs and prompt payloads are never required or stored.

Embedded HTML dashboards such as M Tracker use their catalogued download links instead of MicroStrategy's Export Wizard. Metronome follows in-page and popup download controls, treats Edge's native event as proof that the transfer started, and finishes from the stable, locally staged file. It detaches popup listeners without synchronously closing a half-detached download page, allowing the existing validated staging-to-target copy to run even when Edge never emits its separate terminal notification. For a download-only dashboard flow, a completed XLSX is ZIP/CRC validated locally, checksummed while it is copied, and immediately recorded as the finished artifact; Metronome does not silently stream the entire workbook into an unused CSV afterward.

Flows can download CSV or Excel XLSX. Excel originals are preserved, and each populated worksheet is normalized to a UTF-8 CSV when transformation or SQL requires it. Download-only HTML dashboards retain the validated workbook directly. CSV downloads are normalized directly. Every normalized artifact receives a `Metronome Export View` lineage column. A bundled flow finishes and validates every selected export before one downstream SQL transaction starts, so a partial download bundle never replaces the target. Append validates each artifact against an existing target before writing. Managed snapshot refresh builds the ordered union of columns across the bundle, loads every artifact into one transaction-scoped staging table, and atomically creates a missing target or preserves an existing target while adding new nullable `TEXT` columns and replacing its prior rows. Columns absent from one export become null for that export's rows. Older target columns remain available and become null when absent from the current snapshot, so grants, indexes, constraints, and dependent objects are not discarded. A failed staging load or promotion rolls the entire transaction back. Duplicate normalized names, incompatible required columns, PostgreSQL errors, and worker restarts fail closed with a staged diagnostic record.

GSCM bookmark runs activate Setting, Favorite, scope tabs, and Go through their owning Nexacro components when rendered caption clicks do not dispatch framework events. A failed GSCM export attempt reloads the portal component tree before retrying so an empty virtual bookmark grid cannot leak into the next attempt.

Outlook attachment flows search the signed-in user's default, top-level Inbox for a case-insensitive subject substring. The newest qualifying message must contain exactly one CSV or XLSX attachment; messages without a supported attachment are skipped and multiple supported attachments on the newest qualifying message fail the run. Scheduled runs treat no match and an already-processed attachment as successful no-ops without creating a run folder or invoking SQL. Manual Run forces the current attachment to be processed again. CSV and XLSX headers must be in the physical first row, cannot be blank or duplicated, and must have at least one data row. See [Outlook attachment flows](docs/outlook_attachment_flows.md).

Automatic execution is controlled from the Flows list with an Active/Inactive switch. The builder stores a daily, weekly, or monthly day-of-month schedule but does not activate it. Months that do not contain the configured day are skipped. Run history provides an Expanded logs link for each run. New runs retain every progress event, phase timing, source export view, downloaded artifact, SQL target and mode, final error, and complete worker traceback in the local SQLite database.

Flows can optionally run one local transformation script per downloaded CSV before SQL insertion. The script is selected through the flow builder and copied into the BI desktop's local, gitignored `flow_scripts` folder. Metronome calls it with `--input` and `--output`, requires one non-empty CSV result under the run folder's `script_results` subfolder, and passes only those transformed results to SQL. Original downloads are never overwritten; they stay in their run folder and are removed only when that whole run folder ages out of the keep-newest-3 cleanup. See [the transformation script contract](docs/flow_transformation_contract.md).

Every scan and download records total duration and phase timings. Scan phases include portal navigation, report discovery, report navigation, and filter inspection. Download phases include navigation, configuration, report execution, CSV export, file transfer, optional transformation, and optional SQL insertion. The UI estimates the next scan and download from the median of up to ten comparable successful operations; before history exists, it clearly labels a conservative fallback.

Metronome queues each run for a browser worker on the BI desktop. Website flows have a Browser mode setting. Headless runs use the resident `MXFlowsWorker` Windows service and are appropriate for routine and scheduled downloads. Headed runs start the on-demand `Metronome_Flows_Headed` interactive task and open Edge in the signed-in BI desktop so a flow can be built or debugged visibly. Outlook flows are routed to the headless worker; that worker still owns its persistent Edge context, then launches a distinct per-run interactive Windows task so Outlook COM executes in the signed-in user's desktop session.

ASAP credentials are enrolled once in the website dialog and stored only on the BI desktop as a Windows DPAPI-encrypted blob beside the default automation browser profile. The API reports only whether a credential is configured and never returns its values. When ASAP redirects to SSO because a session expired, the worker detects the password form, fills it with Playwright DOM APIs, signs in, and resumes the scan or download. Credentials are not stored in SQLite, logs, or the repository.

The two workers use separate persistent browser profiles to prevent Edge profile contention. Both read the same Windows DPAPI-protected ASAP credential stored locally for the BI desktop account. Headed runs require that account to be signed in to the desktop. No website credentials or flow configuration are committed to GitHub.

For diagnostics, the same worker can still be started manually:

```powershell
python -m app.flow_worker --server http://BI_DESKTOP:8000 --worker-id authorized-browser --name "Authorized browser" --headed
```

The setup script installs both worker modes. Rerun it after updating Metronome so the interactive headed task is registered. Do not run headed schedules unless the BI desktop user will be signed in at the scheduled time.

Do not copy browser tokens or credentials between Windows accounts. Enterprise policy may still require the local worker to remain headed.

The worker never overwrites existing files - name collisions create a numbered filename. Each run downloads into its own `#<run>_<dd-mm-yyyy>` subfolder of the target folder, and only the newest 3 run folders are kept: the server assigns the cleanup of older run folders it recorded itself, and nothing else in the target folder is ever touched. Optional SQL handoff can append to an existing target or perform the managed snapshot refresh described above.

### Power BI email recurrences

Tools > Recurrences creates scheduled Outlook emails from the summarized data behind a live Power BI table or matrix visual. The builder selects a report from the configured workspace, then an exact page and visual, previews its current columns, applies optional row rules, and saves a daily, weekday, weekly, or monthly schedule. Each recurrence can include an optional alert message for definitions, key actions, warnings, ownership, or other recipient context; the email displays it in an information box above the results. Recipient emails show the report name in the report summary and the selected Power BI visual title above the result table. Visual discovery reads the rendered title property instead of treating generic descriptor values such as `matrix` or `tableEx` as titles. Supported expression-based titles are evaluated from their semantic-model measure under the recurrence's current filter context. Existing saved recurrences refresh their visual title on the next draft or send run, and an unavailable title falls back to `Alert results`. Numeric output follows each field or measure format unless Power BI reports an explicitly configured visual decimal-place override; default or `Auto` visual values do not replace field formatting. Delivery can send all eligible rows to one recipient list or split rows into separate emails by a subgroup column. A delivery with no matching rows sends nothing.

Recurrences deliberately reuse the saved Microsoft account connected from Scanner. The bearer token stays inside the Metronome server process and is silently refreshed through the existing token cache. Service-principal-only authentication is not used for visual export. Microsoft Edge and the Playwright package from `requirements.txt` host Microsoft's official embedded client so Metronome can locate the exact saved visual and read its current field bindings. Metronome does not click the Power BI export menu or scrape the report DOM. A connected saved account and an Outlook profile for the Windows account running Metronome are also required.

Metronome first calls the official JavaScript `visual.exportData` summarized export. Microsoft restricts that API for some visuals that use semantic-model or report measures. When Power BI returns `Error running visual data query`, Metronome reads the selected visual's current columns, measures, formatting strings, report/page/visual filters, and slicer selections, builds an escaped DAX query, and calls the official Power BI Execute Queries REST endpoint with the same cached account. This fallback requires semantic-model Read and Build permission plus the tenant setting `Dataset Execute Queries REST API`. It does not require a separate token or sign-in.

The scheduler checks for due recurrences every minute and interprets the saved time in the Windows host's local timezone. Before every draft or send run, Metronome queries Power BI Service for the semantic model's latest refresh attempt using the same cached Microsoft account. The run continues only when the live status is `Completed`. A failed, cancelled, running, missing, or unavailable refresh check blocks the visual export and sends no alert data.

Each recurrence has an alert owner. New recurrences default to the selected report's owner, and the owner's current email is resolved from Tools > Create Artifacts > People. The builder requires a valid People email before saving. When an actual send run fails before delivery is launched, Metronome sends that owner a separate failure email with the reason, latest refresh status when available, the report/page/visual, and a link to the Power BI report. Draft-test failures are recorded but do not notify the owner. Failure notifications use the same Outlook delivery path, so if Outlook itself is unavailable the notification attempt is recorded in the run detail but cannot be delivered by email.

`Create drafts` runs the complete refresh, export, and filtering path without sending alert emails, while `Run now` sends immediately after confirmation. Scheduled runs send automatically through the existing Outlook implementation.

The saved source uses Power BI's technical page and visual identifiers. Renaming or moving the visual, and adding or removing non-rule columns, does not require editing the recurrence. Every run reads the visual's current fields, so newly added standard columns or measures appear automatically in the HTML table. Static visual field format strings are applied by the REST fallback. The primary summarized-export path also reads those formats and safely normalizes fields formatted as whole numbers before previewing, filtering, grouping, or emailing the rows. Richer currency, percentage, locale, and dynamic formats remain Power BI's responsibility. If the visual is deleted, export is disabled, a configured subgroup/rule column disappears, the row limit would truncate the result, or the visual uses a construct that cannot be reproduced safely, the run fails closed and sends no email. The current fallback supports normal columns, explicit column aggregations, measures, basic filters, advanced filters, and standard slicer selections. Visual calculations, hierarchies, percent-of-grand-total fields, Top N filters, relative date/time filters, identity filters, and multi-field filters are rejected rather than approximated. Summarized export is capped at 30,000 rows. The timeout and lower row limit can be configured with `DG_PBI_VISUAL_EXPORT_TIMEOUT_SECONDS` and `DG_PBI_VISUAL_EXPORT_MAX_ROWS`.

### Flow SQL and materialized-view refresh

Optional Flow SQL handoffs use the dedicated write credentials `DG_UPLOAD_PGUSER` and `DG_UPLOAD_PGPASSWORD`. Set the target server with `DG_UPLOAD_PGHOST`, `DG_UPLOAD_PGPORT`, and `DG_UPLOAD_PGDATABASE`; host and database fall back to the read-only probe connection values when omitted. Each Flow stores its own database, schema, table, and append or managed-snapshot behavior.

The Pipelines lineage view can also refresh a resolved PostgreSQL materialized view with these credentials. That action preserves the existing Pipeline resource lock and exact source-identity checks. Metronome no longer provides a standalone file-import page or generates Prefect import scripts.

Pipeline Flow lineage is report-scoped. An exact Flow SQL target is matched by
server, database, schema, and table through the report's full recursive source
dependency graph, so an upstream table such as `asap_import` remains visible
when it feeds an intermediate materialized view. Static Excel/CSV output names
can appear as dashed **Possible file link** evidence, but they are not run
automatically: current Flow artifacts live in versioned run folders, so a
filename alone does not prove that the Flow updates the exact file Power BI
consumes.

Schedule the lineage scan after PostgreSQL load jobs that temporarily drop and
recreate relations. A complete catalog snapshot taken inside that window will
correctly mark the missing relation unresolved and will restore the exact link
on the next successful scan, but the pipeline remains unlinked in between.

The startup migration that removes the retired Query Changed feature first
creates a timestamped SQLite backup under `backups/`. It then removes the query
version table and every `changed_query` action, including resolved history and
notes. SQLite versions older than 3.35 retain the unused `changed_queries` scan
counter column so startup remains compatible; no application code reads it.

### 5. Run the scanner

Click "Run Scan Now" on the Scanner page, or hit the API:
```bash
curl -X POST http://localhost:8000/api/scanner/jobs/full-scan
curl http://localhost:8000/api/scanner/jobs/JOB_ID
```

Scanner mutations share one durable worker lane. The Scanner page and job API
show the active phase, source/database progress, heartbeat, failures, and recent
completed work; a second request reuses or reports the active job instead of
silently overlapping it. The older `/api/scanner/run`, `/probe`, `/pg-deps`, and
`/pg-cron` endpoints are non-blocking aliases for the same durable jobs.

## PostgreSQL "Last Updated" Probing

To show when PostgreSQL tables were last updated:

1. Export the query results from pgAdmin as a CSV named `latest_upload_date.csv`
2. Place it in the `data_governance` project root (same level as `app/`)
3. CSV must have columns: `schema_name, table_name, last_activity` (with a header row)
4. Run a scan - the prober runs automatically after each scan

You can also trigger a probe independently:
```bash
curl -X POST http://localhost:8000/api/scanner/probe
```

### Debugging probe matching

If "Last Updated" shows empty, open this URL in your browser to see what the CSV contains vs what's stored:
```
http://localhost:8000/api/scanner/probe/debug
```

Or paste this into Chrome DevTools Console (F12 → Console):
```js
fetch("/api/scanner/probe/debug").then(r=>r.json()).then(d=>console.log(d))
```

This shows `csv_samples` (what the CSV has) and `postgresql_sources` (what's in the database) side-by-side so you can spot the mismatch.

## Running Tests

```bash
python tests/test_scanner.py
```

## Expected Folder Structure for TMDL Exports

```
{DG_TMDL_ROOT}/
└── reports/
    ├── Weekly_Sales/
    │   └── Weekly_Sales.SemanticModel/
    │       └── Definition/
    │           ├── Tables/
    │           │   ├── Main.tmdl
    │           │   ├── SKU Master.tmdl
    │           │   └── ...
    │           └── expressions.tmdl  (optional, for parameters)
    ├── Monthly_KPI/
    │   └── ...
    └── ...
```

## What it does

- **TMDL Scanner**: Parses your Power BI TMDL files to auto-detect all data sources (SQL Server, Excel, CSV)
- **Source Registry**: Tracks every data source, deduplicates across reports
- **Report Inventory**: Lists all reports with their upstream sources
- **Lineage Map**: Shows which sources feed which reports
- **Alerts**: Flags stale sources and broken references

See [plan.md](plan.md) for the full architecture and roadmap.
