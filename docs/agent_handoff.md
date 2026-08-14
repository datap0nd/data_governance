# Agent Handoff

## Current Objective

Deliver and live-verify observable SQL handoff behavior for Flows. Append must
enforce the existing target schema. Replace must atomically rebuild the exact
selected table from CSV columns and expose every SQL failure cleanly in the run
log.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Delivery target: `origin/main`
- Public repo: no, private
- Latest verified remote commit: `a103e54`
- Push status: atomic schema-replacement changes are tested locally and pending
  publication and live deployment.

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

## ASAP Rollback And Dimension Reset (2026-08-14)

- The tracked tree was restored to `05ea061`, the last evidence-backed good
  state before native-control filter manipulation began. Its live handoff
  recorded two successful headed CSV runs.
- Dimension now bypasses hidden/native controls. The worker reads visible
  selection state, plain-clicks every retained selected member off, then
  plain-clicks the exact Metronome-requested members on and verifies equality.
  The Dimension path never uses Ctrl-click.
- Week selection and the proven multi-download sequence were left unchanged.
  Each download waits for `save_as`, closes its export popup, then reopens and
  configures the report for the next period.
- Full Python suite: `161 passed` on Python 3.11.
- The first live rollback run reached the exact report, applied Dimension with
  plain clicks, ran the report, completed the visible browser download, and
  closed the export popup. The two-minute stale-run reaper then failed the run
  while Playwright was still in its blocking save step. The production grace
  period is now ten minutes; explicit shorter timeouts remain available to
  tests and diagnostics.
- A second live run confirmed the headed worker disappears specifically while
  Playwright saves directly to the configured UNC share. Downloads now wait on
  Edge's completed local file, normalize locally, and copy the final CSV to an
  exclusive-create destination. Existing files still cannot be overwritten.
- A third live run showed that `download.path()` also blocks indefinitely after
  Edge visibly completes the file. The worker now launches Edge with a known
  local download staging folder, detects a new stable file there without any
  Playwright download-path call, normalizes it locally, and copies it to the
  configured destination.
- Full Python suite after the staging-folder change: `163 passed`; Python
  compilation succeeded.
- Live run #65 proved the staging and final copy: Edge wrote a new 57,667,776
  byte local file and the worker copied an equal-size numbered CSV to the final
  share. The run then blocked only while Playwright waited for the already
  disappeared export page to acknowledge `close()`. Popup cleanup now uses the
  non-waiting close mode so the next period can begin.
- Live run #66 created both requested final-share files, Week 27 and Week 28,
  each 57,667,776 bytes. It also proved the report reopened and progressed to
  `Exporting CSV 2 of 2`. Edge's detached popup object can block even on
  `is_closed()`, so post-download popup API calls were removed entirely. ASAP
  already closes the visible wizard itself.
- Live run #67 completed the full browser and file path in 1m10s, then remained
  inside SQL insertion until the stale-run watchdog failed it. The 57 MB CSV
  was being inserted with pandas `to_sql` in 5,000-row batches. SQL handoff now
  streams the validated normalized frame through PostgreSQL's native `COPY`
  protocol in the same truncate-and-replace transaction.
- Live run #69 on the deployed final scraper build again completed navigation,
  exact Dimension configuration, report execution, local export, normalization,
  and final-share transfer in 1m13s. The saved Week 27 CSV contains 41,872 rows.
- The SQL loader no longer asks pandas to infer the delimiter with its slow
  Python parser. Flow artifacts are already normalized, so it now reads the
  guaranteed UTF-8 comma format with pandas' C parser before PostgreSQL COPY.
  Full suite: `165 passed`; Python compilation and `git diff --check` passed.
- SQL remains unverified end to end. Run #69 stayed at `Loading downloaded files
  into SQL` for more than nine minutes against
  `postgres.bi_reporting.this_is_test` and was stopped cleanly. The Citrix
  session reported high network latency during the wait. Do not change the
  proven scraper path while diagnosing this. Inspect the target database for
  an external table lock or blocked transaction, then rerun SQL only.
- PostgreSQL transactions now set a 30-second local lock timeout so a blocked
  target table produces an actionable database error instead of hanging the
  flow. Commit `8aa9afb` is deployed on the BI desktop; the updater completed
  after transient corporate-proxy HTTP 502 retries and restored the service.
- Safeguarded live run #70 again completed the browser and file phases in
  1m12s, then remained at SQL insertion for more than six minutes without a
  lock-timeout error. This rules out a PostgreSQL table-lock wait. The remaining
  blocker is the database connection/COPY path or its network route. Run #70
  was stopped cleanly; the flow is idle and ready for a database-only diagnosis.

## Observable SQL Handoff Update (2026-08-14)

- SQL insertion no longer loads the normalized CSV into pandas or serializes it
  into a second temporary CSV. It validates the header and streams the saved
  UTF-8 CSV directly through PostgreSQL `COPY`.
- Expanded logs now record artifact validation, connection, target validation,
  truncate, each COPY, commit, and failure as separate events with elapsed time.
- Database errors report the failed stage, PostgreSQL SQLSTATE when available,
  primary database message, and whether commit or rollback was confirmed.
- Connection attempts are bounded at 10 seconds, table locks at 30 seconds, and
  SQL statements at 120 seconds. TCP keepalives detect dead network paths.
- A terminal run with a saved SQL-ready artifact now offers `Retry SQL only`.
  It queues a headless SQL job from the prior artifact, does not open ASAP, and
  does not download the report again. The UI confirms the exact target and
  warns that append retries can duplicate already-committed rows.
- The run log refreshes every two seconds while work is active, so the last
  completed SQL phase is visible without manual reloads.
- Full Python suite: `169 passed`. Python compilation, JavaScript syntax checks,
  and `git diff --check` passed. No live SQL write has been performed with this
  build.
- Next step: deploy the new `main` to the BI desktop, open run #70's Expanded
  logs, use `Retry SQL only`, and observe the exact terminal phase or clean
  PostgreSQL error. This action can recreate/append the configured target and
  requires explicit Citrix approval immediately before clicking it.

## Atomic Schema-Replacing SQL Handoff (2026-08-14)

- Live run #71 proved the new diagnostic path through artifact validation,
  PostgreSQL connection, and target inspection. It cleanly identified that the
  17-column Week 27 CSV did not match the six-column random test table before
  any truncate or commit.
- Product semantics were then clarified: append must retain strict schema
  validation, while replace must accept a different CSV schema. PostgreSQL
  `TRUNCATE` cannot change a schema, so replace now drops and recreates the
  selected table inside the same transaction, creates one `TEXT` column per
  normalized CSV column, streams the file with `COPY`, and commits only after
  the full load succeeds. Any failure restores the prior table on rollback.
- The replace confirmation and flow editor explicitly warn that a successful
  schema replacement removes the old table's indexes, constraints, triggers,
  and table grants.
- Worker registration now compares process IDs. A restarted worker fails any
  nonterminal SQL run and refuses to replay the mutation automatically. The
  worker also falls back to a minimal terminal failure payload if rich
  diagnostic reporting fails.
- Full suite: `173 passed`. Python compilation, JavaScript syntax checks, and
  `git diff --check` passed.
- Next step: commit and push to `main`, deploy the new build, allow the
  replacement of `postgres.bi_reporting.this_is_test`, and verify run #72
  reaches target recreation, COPY of 41,872 rows, and commit.

## Live Run 72 Week Finding (2026-08-14)

- Build `a55546d` deployed successfully and preserved the SQLite database.
  Worker restart protection closed run #71 as failed and explicitly refused to
  replay its SQL mutation.
- Run #72's saved job and progress events requested `2026-W27`, but the visible
  ASAP Week prompt retained `202632`. The run later failed before SQL because no
  completed file appeared in the headed staging folder. It wrote no SQL data.
- Week now bypasses the hidden native-select shortcut and uses the visible
  MicroStrategy list with exact selected-state reconciliation before RUN, just
  as the live evidence requires.
- Next step: publish and deploy the Week fix, then use run #70's already
  verified Week 27 artifact for SQL-only replace testing so browser state cannot
  contaminate the SQL result.

## Live SQL Commit And Terminal Reporting Finding (2026-08-14)

- Build version `20260814-105314` ran SQL-only retry #73 from run #70's verified
  Week 27 artifact. PostgreSQL recreated the 17-column target as `TEXT`, copied
  and committed 41,872 rows, and Metronome recorded `SQL Insertion Complete`.
- The subsequent terminal-success request returned HTTP 500, so the worker's
  minimal fallback marked the run failed even though PostgreSQL had committed.
  Do not retry run #73: it would replace an already successful target.
- Root cause: SQL retry artifacts retain `period_key` as a JSON list such as
  `["2026-W27"]`, while terminal file-history storage bound that list directly
  into SQLite's `TEXT` column. Terminal storage now normalizes list periods to a
  display string before insertion. A regression test exercises the exact
  SQL-retry artifact shape and requires a succeeded terminal run.
- Next step: publish and deploy the terminal-reporting fix. Future SQL runs must
  finish as succeeded after `SQL Insertion Complete`; no additional write is
  required against the already populated test target.
