# Agent Handoff

## Current Objective

Complete the daily governance scan, persist actionable findings with a real
lifecycle, activate the dormant data-quality engine, and remove the Custom
Reports product surface.

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

## Commands And Checks

- Full Python suite: `67 passed`.
- Node lineage suite: `1 passed`.
- Python `compileall` for `app`: passed.
- JavaScript syntax check: passed.
- Fresh SQLite initialization and migration smoke check: passed.
- Full application route smoke check: Data Quality routes present and Custom
  Reports routes absent.
- Browser verification at 1440x1000 and 390x844: Data Quality page and form
  rendered without console errors; mobile AI panel opened and closed without
  obscuring the page while closed.
- Impeccable detector: remaining warnings are pre-existing patterns outside the
  new Data Quality surface.
- `git diff --check`: passed.

## Next Step

After updating the installed service from `main`, configure initial rules under
Tools > Data Quality. The first source probe will execute them and populate
their result status. Existing Custom Reports rows remain in SQLite but are no
longer exposed by the application.
