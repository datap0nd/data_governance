# Agent Handoff

## Current Objective

Keep Metronome focused on actionable governance workflows. The latest change
implements the findings from the live-app audit, removes the manual Tasks
surface, and makes owner, freshness, scheduler, scanner, and alert-email
behavior more trustworthy.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Delivery target: `origin/main`
- Public repo: no, private

## Decisions Made

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

## Files Changed

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

## Commands And Checks

- Full Python suite: `81 passed`.
- Node lineage suite: `1 passed`.
- Python `compileall` for `app`: passed.
- JavaScript syntax check: passed.
- Impeccable detector: only pre-existing side-border, bounce, and layout
  transition warnings remain; none came from the changed product surfaces.
- `git diff --check`: passed.

## Next Step

Update the installed service from `main`, then validate one owner save, one
freshness-rule change, one governed Task Scheduler failure, and one Outlook
draft against live Windows data.
