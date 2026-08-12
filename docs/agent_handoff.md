# Agent Handoff

## Current Objective

Make website-report flows debuggable without weakening background automation.
Each flow now selects a headed or headless browser from the UI, and Metronome
routes it to the matching BI desktop worker.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Delivery target: `origin/main`
- Public repo: no, private
- Push status: delivered to `origin/main` in commit `8c66c58`

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
  controls, CSV validation, checksums, and collision-safe filenames.
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

- Full Python suite: `123 passed`.
- BI desktop setup: downloaded `main`, preserved the existing SQLite database,
  restarted Metronome and the headless worker, and registered the headed task.
- Live Flows UI: both browser choices and the execution-summary mode were
  visible. The existing test flow was closed without saving or running.
- Existing Flows desktop and 390px mobile Playwright screenshots passed with
  no horizontal overflow after their responsive fix.
- Node lineage suite: `1 passed`.
- Python `compileall` for `app`: passed.
- JavaScript syntax check: passed.
- Impeccable detector: four pre-existing side-border warnings remain; none is
  in the changed Flows code.
- `git diff --check`: passed.

## Next Step

Deploy the headed-task launch fix, retry queued run #2 from the Flows page, and
watch the visible Edge window. Use that observation to replace the hidden
Select2 option click with the current visible ASAP control path.
