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

### 3. Configure TMDL path

Set where your TMDL report exports live. By default it looks at:
```
%USERPROFILE%\documents\projects\data_governance\reports\
```

To change it, set the environment variable before running:
```bash
set DG_TMDL_ROOT=C:\Users\YourName\documents\projects\data_governance
```

Flow schedules use the host's named local timezone. To pin that wall-clock
contract explicitly (recommended for production), set an IANA timezone such as:

```bash
set DG_FLOW_TIMEZONE=Europe/Lisbon
```

Freshness evidence and monitoring baselines are stored as UTC instants.

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
default. When a new commit appears, Metronome immediately starts one
pre-registered elevated Windows task. That task validates the exact
40-character commit and runs the same `setup.ps1` used for manual installs in
unattended mode. `setup.ps1` remains the only installer: it preserves the
database and existing Windows service credentials, replaces the code with the
detected commit, installs dependencies, restarts Metronome, and verifies the
local `/api/version` health check. GitHub Tests and currently active work remain
visible in **System > Updates** for awareness, but they do not delay the update.

Run `setup.ps1` interactively once after deploying this version so it can
register the fixed `Metronome_Auto_Update` task using the existing service
account. After that, use **System > Updates** to enable/disable automatic
installs, force a check, install manually, or inspect the latest durable
attempt. Private-repository installs must expose `DG_GITHUB_TOKEN` to the
service account.

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

The same worker navigates through the signed-in portal, activates each exact export view selected by the flow, scopes prompt controls inside ASAP's `content-frame`, applies week members in `YYYYWW` form when the view has a week prompt, and selects one of ASAP's five exact Export Wizard choices: Excel with plain text, CSV file format, Excel with formatting, HTML, or Plain text. It can also enforce Export Report Title and Export filter details. Existing migrated Flows inherit the portal's checkbox state until a detailed scan observes a consistent value; new Flows default both checked. Raw-data export views use their rendered table information menu, but the direct Excel shortcut is restricted to inherited options and Excel with plain text. A transient report-load HTTP 500 is retried once by returning through the portal menu. Opaque MicroStrategy object IDs and prompt payloads are never required or stored. See [ASAP export options](docs/asap_export_options.md).

Embedded HTML dashboards such as M Tracker use their catalogued download links instead of MicroStrategy's Export Wizard. Metronome follows in-page and popup download controls, treats Edge's native event as proof that the transfer started, and finishes from the stable, locally staged file. It detaches popup listeners without synchronously closing a half-detached download page, allowing the existing validated staging-to-target copy to run even when Edge never emits its separate terminal notification. For a download-only dashboard flow, a completed XLSX is ZIP/CRC validated locally, checksummed while it is copied, and immediately recorded as the finished artifact; Metronome does not silently stream the entire workbook into an unused CSV afterward.

ASAP Flows can download both Excel modes, CSV, HTML, or Plain text. Excel originals are preserved and normalized to a UTF-8 CSV for downstream work. ASAP CSV preserves the byte-exact portal response as `_raw.csv` while keeping the normalized configured filename as the primary artifact for compatibility. HTML and Plain text are validated download-only artifacts; transformation and SQL are rejected. Download-only HTML dashboards retain validated workbooks directly. Every normalized artifact receives a `Metronome Export View` lineage column. A bundled flow finishes and validates every selected export before one downstream SQL transaction starts, so a partial download bundle never replaces the target. Append validates each artifact against an existing target before writing. Managed snapshot refresh builds the ordered union of columns across the bundle, loads every artifact into one transaction-scoped staging table, and atomically creates a missing target or preserves an existing target while adding new nullable `TEXT` columns and replacing its prior rows. Columns absent from one export become null for that export's rows. Older target columns remain available and become null when absent from the current snapshot, so grants, indexes, constraints, and dependent objects are not discarded. A failed staging load or promotion rolls the entire transaction back. Duplicate normalized names, incompatible required columns, PostgreSQL errors, and worker restarts fail closed with a staged diagnostic record.

GSCM bookmark runs activate Setting, Favorite, scope tabs, and Go through their owning Nexacro components when rendered caption clicks do not dispatch framework events. A failed GSCM export attempt reloads the portal component tree before retrying so an empty virtual bookmark grid cannot leak into the next attempt.

Outlook attachment flows search the signed-in user's default, top-level Inbox for a case-insensitive subject substring. The newest qualifying message must contain exactly one CSV or XLSX attachment; messages without a supported attachment are skipped and multiple supported attachments on the newest qualifying message fail the run. Scheduled runs treat no match and an already-processed attachment as successful no-ops without creating a run folder or invoking SQL. Manual Run forces the current attachment to be processed again. CSV and XLSX headers must be in the physical first row, cannot be blank or duplicated, and must have at least one data row. See [Outlook attachment flows](docs/outlook_attachment_flows.md).

Automatic execution is controlled from the Flows list with an Active/Inactive switch. The builder stores a daily, weekly, or monthly day-of-month schedule but does not activate it. Months that do not contain the configured day are skipped. Run history provides an Expanded logs link for each run. New runs retain every progress event, phase timing, source export view, downloaded artifact, SQL target and mode, final error, and complete worker traceback in the local SQLite database.

Each Flow chooses an **Output storage** mode. The default, **Run folders**, keeps the existing `#<run>_<dd-mm-yyyy>` folders visible under the configured target and retains the newest three unpinned runs. **Direct files** keeps those same immutable run artifacts in `<browser-profile>/run_artifacts/<target-hash>` and publishes only the validated configured deliverables into the target folder. An identical resolved filename is replaced; filename tokens such as `{date}` and `{week}` deliberately create additional stable files, and a changed extension leaves the older basename untouched with a warning in the run log. Outlook v1 retains the attachment's original basename, so dated attachment names accumulate. Changing mode, target, or template never deletes prior stable files or visible run folders.

Direct publication is serialized per normalized target folder. Metronome verifies the complete bundle, copies it into dot-prefixed same-directory staging files, journals backups, and atomically installs each filename. A normal failure restores the previous bundle. If Excel or another process locks restoration, the owned backup and journal remain for the next serialized run to reconcile; unrelated user files are never scanned or deleted. Publication happens before transformation and SQL, matching the existing rule that a later downstream failure does not erase successfully downloaded deliverables.

Direct mode's private run folders preserve Resume, SQL Retry, diagnostics, normalized CSVs, and transformation outputs without exposing those working files in the target folder. The worker advertises an opaque identity for that machine/profile store: Resume revalidates files and downloads missing work again, while SQL Retry can be claimed only by the matching store and verifies size/checksum before SQL. Moving or reinstalling the profile therefore makes those cached artifacts unavailable rather than silently selecting another run. Budget local profile disk for three full unpinned runs per configured target; active Resume/SQL Retry pins can temporarily retain more.

Flows can optionally run one local transformation script per downloaded CSV before SQL insertion. The script is selected through the flow builder and copied into the BI desktop's local, gitignored `flow_scripts` folder. Metronome calls it with `--input` and `--output`, requires one non-empty CSV result under the immutable run folder's `script_results` subfolder, and passes only those transformed results to SQL. Original downloads are never overwritten inside artifact storage; the whole owned run folder is removed only after it ages out of the keep-newest-three cleanup and has no recovery pin. See [the transformation script contract](docs/flow_transformation_contract.md).

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

Within immutable artifact storage the worker never overwrites an existing file: name collisions receive a numbered suffix. Run-folder mode keeps the newest three visible run folders. Direct mode replaces only exact public destination filenames through its ownership journal while retaining the newest three private run folders. In both modes, the server assigns cleanup only for run folders it recorded itself; normal user files are never retention candidates. Optional SQL handoff can append to an existing target or perform the managed snapshot refresh described above.

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
can appear as dashed **Possible file link** evidence, including a static
direct-file output, but they are deliberately not run automatically.
Executable Pipeline identity and governance for file-output Flows is a
separate feature; this release does not change `included_flow_ids` or legacy
source matching.

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
