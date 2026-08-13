# Agent Handoff

## Current Objective

Complete a live headed Inflow/Outflow ASAP download from Metronome without
deleting, overwriting, or inserting anything into SQL.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Delivery target: `origin/main`
- Public repo: no, private
- Push status: delivered to `origin/main` through commit `4f4dc39`

## Decisions Made

- Flows owns websites, report catalogs, filter definitions, flow selections,
  schedules, queued runs, workers, artifacts, and run history in SQLite.
- Repository code contains no report-specific URL, filter value, destination,
  or credential. Users configure those values from Tools > Flows.
- Browser work runs locally on the BI desktop through the resident
  `MXFlowsWorker` headless service or the on-demand
  `Metronome_Flows_Headed` interactive task. Each uses a separate browser
  profile; both use the same account-scoped DPAPI credential.
- Headed workers claim only headed flow jobs, exit after one idle minute, and
  never claim catalog scans. Headless workers claim headless jobs and scans.
- Windows launches the headed task through the absolute System32 executable
  and root-qualified task path. Clicking Run on a queued flow retries worker
  startup without creating a duplicate run.
- The interactive task launches the installed Python worker directly. It does
  not add a PowerShell wrapper between Task Scheduler and Playwright.
- ASAP Select2 controls are matched by their complete discovered option set and
  selected through the owning native select. This avoids clicking hidden option
  elements and disambiguates controls sharing one value.
- ASAP week members are selected using an exact visible match. Report
  completion is detected from the rendered row summary across replacement
  MicroStrategy frames, rather than an unstable internal response URL.
- CSV export uses the first icon-only toolbar control beside RUN, which opens
  MicroStrategy Export Options. The worker selects `CSV file format`, activates
  `Export`, and supports both popup and in-page wizard shapes.
- The worker never deletes or overwrites an existing file. Filename collisions
  receive a numbered suffix.
- SQL handoff is displayed as a future step but is rejected by API validation
  and is not executed in this release. Cadence was reference material only and
  was not changed.

- Scanner findings use stable fingerprints. Current findings update their open
  action, cleared findings resolve automatically, and later reappearance opens
  a new action while preserving history.
- The daily overall refresh now covers report discovery, PostgreSQL dependency
  and cron discovery, scripts, Windows scheduled tasks, source probes,
  data-quality checks, best-practice findings, schedule discrepancies,
  documentation completeness, configured usage CSV data, Power BI refresh
  metadata, and Power BI usage sync.
- Query changes are event findings, so each distinct query version remains open
  for review rather than auto-closing merely because a later scan sees the same
  query.
- Data-quality checks are read-only. Probe-based rules use stored row counts;
  PostgreSQL column rules use the existing read-only probe connection.
- A failed data-quality check creates one owned action and alert. Passing later
  resolves both. Disabling a check resolves its active incident but keeps its
  definition and result history.
- Custom Reports navigation, frontend code, models, API router, and router file
  were removed. The existing `custom_reports` database table remains so an app
  update does not destroy historical user data.
- Pipeline Overview navigation, frontend graph code, dedicated CSS, FAQ copy,
  and API router were removed. Old `#overview` bookmarks redirect to Dashboard.
- The manual Tasks page and task-email UI were removed. Old `#tasks` bookmarks
  redirect to Dashboard. Legacy task APIs and tables remain in place so old
  saved data is not destroyed.
- Sources with no owner now have an evidence review queue. Auto-assignment uses
  the unique majority owner across active linked reports and skips ties.
- Freshness rules are auto-filled only from explicit source schedules. Rule
  changes immediately re-evaluate the latest stored probe, and thresholds over
  90 days are flagged for review.
- Scheduled Tasks defaults to governed jobs linked to scripts. Unlinked,
  disabled, and never-run Windows tasks do not create incidents. Known Task
  Scheduler result codes have user-facing states.
- Owner emails are alert-only, ranked by risk and impact, and require explicit
  recipient selection. Immediate sends show recipients and alert counts in the
  confirmation.
- Alert emails now contain only the intro, one alert table, and the closing.
  The duplicate top-three section and the table heading were removed.
- `Degraded since` is the current action's creation timestamp, which represents
  the start of the current detected degradation episode. It replaces age and
  `Open` wording in alert surfaces.
- Alert surfaces label the usage-prioritization metric as `Views`; the existing
  premium-viewer multiplier remains an internal ranking rule.
- Power BI refresh history parsing prefers the detailed refresh-attempt error,
  stores the extracted message, and exposes it in report details, alert
  details, the email preview, and the Outlook email as `PBI Refresh Error:`.
- Artifact marks in Outlook email use inline, email-safe HTML for Power BI,
  Excel, SQL, and the supported fallback artifact types.

## Files Changed

- `app/database.py`: flow catalog, definition, run, artifact, and worker tables.
- `app/routers/flows.py`: validated catalog/flow APIs, scheduler queue, worker
  claim/progress protocol, per-week job expansion, and disabled SQL handoff.
- `app/flow_worker.py`: persistent-profile Playwright worker with semantic
  controls, replacement-frame readiness, current ASAP Export Options handling,
  CSV validation, checksums, and collision-safe filenames.
- `app/flow_local_runner.py`, `tools/run_flow_worker.ps1`: BI-desktop task
  launchers, mode-specific worker identities, and headed idle shutdown.
- `setup.ps1`: headless service identity plus the on-demand interactive task.
- `app/static/index.html`, `app/static/app.js`, `app/static/style.css`: Flows
  navigation, catalog management, populated builder, run history, desktop and
  mobile layouts, and a mobile hidden-panel overflow fix.
- `app/main.py`: router and one-minute flow schedule dispatcher.
- `tests/test_flows.py`: persistence, filter validation, scheduling, worker,
  artifact, no-delete, no-overwrite, and SQL-disabled coverage.
- `README.md`: authenticated worker setup and safety behavior.

- `app/scanner/runner.py`, `app/scanner/prober.py`: expanded scan coverage and
  persistent actions for broken references, changed queries, stale
  dependencies, and governance checks.
- `app/scanner/findings.py`: shared fingerprinted action lifecycle.
- `app/checks/data_quality.py`, `app/routers/data_quality.py`: validation,
  execution, result history, action and alert lifecycle, and management API.
- `app/routers/best_practices.py`, `app/routers/schedules.py`,
  `app/routers/documentation.py`: persist actionable report findings.
- `app/routers/actions.py`, `app/routers/dashboard.py`: expose distinct managed
  actions and live data-quality totals.
- `app/static/app.js`, `app/static/style.css`, `app/static/index.html`: Data
  Quality management page, removal of Custom Reports, and a mobile AI-panel
  off-screen positioning fix found during the visual verification pass.
- `app/database.py`, `app/models.py`, `app/main.py`: schema migrations, API
  models, router registration, and scheduled Power BI usage sync.
- `README.md`: updated daily-refresh and data-quality behavior.
- `tests/test_data_quality.py`: validation, failure/recovery, row-count change,
  and managed-finding lifecycle coverage.
- `app/static/index.html`, `app/static/app.js`, `app/static/style.css`,
  `app/main.py`, `app/routers/overview.py`: removed Pipeline Overview end to end.
- `tests/test_overview_removed.py`: guards the removed route and navigation.
- `app/routers/sources.py`, `app/scanner/task_scheduler_runner.py`,
  `app/routers/scheduled_tasks.py`: owner evidence, immediate freshness status,
  governed job filtering, and false-positive suppression.
- `app/routers/email.py`, `app/routers/email_schedules.py`: ranked alert email
  format and alert-only profile schedules.
- `app/routers/dashboard.py`, `app/routers/reports.py`,
  `app/routers/schedules.py`, `app/scanner/runner.py`: consistent live counts,
  explicit unknown status, and complete probe history accounting.
- `app/static/app.js`, `app/static/style.css`, `app/static/index.html`: removed
  Tasks UI, fixed asynchronous navigation races, added owner review, clarified
  report governance, reduced script/task noise, and improved email safeguards.
- `tests/test_sources.py`, `tests/test_task_scheduler_runner.py`,
  `tests/test_email_alert_summary.py`, `tests/test_overview_removed.py`: focused
  regression coverage for the new behavior.
- `app/models.py`, `app/routers/actions.py`: degradation date and Power BI error
  evidence on action responses, plus plain-language alert copy.
- `app/routers/email.py`: one-table email layout with artifact marks,
  degradation dates, views, next actions, and refresh errors.
- `app/scanner/pbi_fetch.py`: detailed Power BI refresh-attempt error parsing
  for both direct refresh lookup and the scheduled refresh metadata sync.
- `app/static/app.js`, `app/static/style.css`, `app/static/index.html`: matching
  dashboard, report-detail, and email-preview surfaces.
- `docs/metric_contracts.md`, `tests/test_email_alert_summary.py`,
  `tests/test_pbi_fetch.py`, `tests/test_overview_removed.py`: metric semantics
  and regression coverage for the new alert contract.

## Commands And Checks

- Full Python suite: `125 passed`.
- BI desktop setup: downloaded `main`, preserved the existing SQLite database,
  restarted Metronome and the headless worker, and registered the headed task.
- Live headed runs #12 and #13 succeeded end to end on the BI desktop. Run #13
  completed in 53 seconds: navigation 14s, configuration 30s, report execution
  4s, and CSV export 5s.
- Run #13 saved `Mobile - Installed Base - Installed Base (MENA) (2).csv` under
  `C:\Users\meto.mx\Documents\Downloads_Flows`, 346,052 bytes, with a stored
  checksum. The numbered suffix confirms the existing CSV was not overwritten.
- Run #12 also saved one CSV and completed in 1m 6s. No files were deleted and
  SQL handoff remained disabled.
- Existing Flows desktop and 390px mobile Playwright screenshots passed with
  no horizontal overflow after their responsive fix.
- Node lineage suite: `1 passed`.
- Python `compileall` for `app`: passed.
- JavaScript syntax check: passed.
- Impeccable detector: four pre-existing side-border warnings remain; none is
  in the changed Flows code.
- `git diff --check`: passed.

## Next Step

Add automatic handling for the optional ASAP Notice popup, then run the same
flow headless to verify parity with the proven headed CSV path. Do not delete
files and do not enable SQL handoff.

## Windows Updater Recovery (2026-08-13)

- `setup.ps1` no longer invokes robocopy. Updates use the original recursive
  `Copy-Item` overlay with no mirror or purge behavior.
- The update archive is downloaded successfully before Metronome, its Flow
  worker, or the headed scheduled task is stopped. A proxy failure therefore
  leaves a running installation untouched.
- Each of the ten corporate-proxy download attempts has a 60-second timeout.
- Commit `a000b2a` is verified on `origin/main`; the full suite passes with
  `161 passed`.
- The earlier updater process had already removed the BI desktop service before
  failing. Metronome was restored from the preserved code and SQLite database
  by launching uvicorn directly on port 8000. The UI is reachable and reports
  one online worker. A later successful setup run must restore Windows service
  registration using the corrected updater.
- No user files, flow results, or database records were deleted.

## Current Flows Update (2026-08-13)

- Runtime commit `9492b0f` is on `origin/main`.
- ASAP export is CSV-only. New downloads remove a detected report-title row
  plus the following blank row while preserving the real comma-separated header.
- Scheduled execution is controlled by the flow-list Active switch. Manual flows
  cannot be activated, while manual Run remains available.
- New run progress events, timings, errors, tracebacks, artifacts, saved config,
  and SQL details are available from the full-page Expanded logs view. Historical
  runs cannot gain missing tracebacks retroactively.
- SQL insertion maps by normalized column name. It rejects unexpected source
  columns, duplicate names, and missing required target columns; nullable,
  defaulted, identity, and generated target columns may be absent. Type failures
  roll back the transaction. Truncate-and-replace also rolls back its truncate
  if insertion fails.
- Full Python suite: `148 passed`. JavaScript syntax checks and
  `git diff --check` passed. SQL insertion was not live-tested.
- BI desktop update succeeded after the built-in proxy retry. The live UI shows
  Active, CSV-only download, Stop, Run history, and Expanded logs.
- During UI inspection, an already-running headed ASAP browser was inadvertently
  closed while switching windows. That run may fail or cancel. No flow was
  started, no file was deleted, and no SQL insertion was performed by the
  inspection.
- Next step: inspect that run's expanded log after it settles, then validate a
  fresh download-only run before testing SQL.

## Transformation Stage Update (2026-08-13)

- Runtime commit `47fd881` adds an optional transformation stage between ASAP
  download and SQL insertion.
- The builder's Browse control uses the Windows file picker for `.py`, `.ps1`,
  or `.exe` scripts. The selected file is copied into the local, gitignored
  `flow_scripts` folder; proprietary scripts do not enter the repository.
- The worker invokes the script once per downloaded CSV. Python and executable
  scripts receive `--input` and `--output`; PowerShell receives `-InputPath`
  and `-OutputPath`. The same locations are exposed through
  `METRONOME_FLOW_INPUT`, `METRONOME_FLOW_OUTPUT`, and
  `METRONOME_FLOW_RESULTS_DIR`.
- Results are collision-safe CSV files in `<target folder>/script_results`.
  Original downloads remain untouched. A missing, empty, invalid, timed-out,
  or non-zero script result fails the run before SQL.
- When enabled, SQL receives only transformed files. Expanded logs retain the
  transformation timing, result paths, stdout, stderr, errors, and traceback.
- ASAP CSV normalization now detects comma, semicolon, tab, or pipe delimiters
  and UTF-8, UTF-16, or Windows encodings before writing a standard UTF-8 comma
  CSV. This fixes run #31's post-download delimiter failure.
- Full suite: `154 passed`; Python compileall, JavaScript syntax checks, and
  `git diff --check` passed. Detector warnings were pre-existing and outside
  the new Flows controls.
- BI desktop update completed and the live editor shows the Transformation
  section. Script execution and SQL insertion were not tested.
- Next step: adapt the first real transformation script to the documented
  contract, select it in the flow, and test download plus transformation with
  SQL disabled.

## ASAP Multi-Period Reliability Update (2026-08-13)

- Runtime commit `112a56e` resets each ASAP list selection with a normal first
  click, then Ctrl-clicks only the remaining requested members. This prevents
  retained portal selections from being toggled off or leaking into a run.
- ASAP export popups are tracked per download, kept open until `save_as`
  confirms file completion, and then closed before the next period starts.
  Self-closing wizard windows are tolerated.
- Full Python suite: `155 passed`; `git diff --check` passed.
- Next step: update the BI desktop and run a headed flow with several Dimension
  values and at least two downloads. Verify both the exact ASAP selection and
  that each export popup closes before the following report period begins.
- Data Configuration discovery now reads every native select, including the
  hidden owner used by Select2, and merges duplicate prompt discoveries. This
  prevents a partial visible popup snapshot from omitting an unrendered option.
- The scanner also polls opened Select2 results until they remain stable for
  1.5 seconds, capturing remotely rendered options that arrive after the old
  150 ms snapshot. Both setup script variants stop an active headed task before
  replacing code so a post-update run cannot retain the old Python runtime.
