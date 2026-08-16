# Agent Handoff

## Current Objective

Deploy and verify the Pipelines readability update. Pipeline node names and
metadata now wrap, while Visuals and Power BI Tables are hidden by default.
Flow nodes are implemented but cannot be verified in production until matching
target tables exist. Keep the paused production Flow paused until the user
explicitly resumes it.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest code commit: `41b353d` (`Wrap pipeline labels and reduce default columns`)
- Feature commit: `de1b362` (`Improve alert assets and enable report refresh discovery`)
- Public repo: no, private
- Push status: code commit verified on `origin/main`
- Preserve untracked `governance.db-shm` and `governance.db-wal`

## Decisions Made

- Alerts identify assets with an inline Power BI, Excel, SQL, or Flow logo. The
  redundant source/report text column is removed.
- Alert owners use a wider control, and decorative purple asset badges are not
  used. Existing severity colors remain semantic.
- A Power BI refresh can discover and persist a missing semantic-model ID from
  the configured workspace before requesting the refresh.
- The dashboard Last Scan card uses the latest completed scan. A newer stopped
  or failed scan must not replace valid report/source counts with null values.
- Best-practice actions stay excluded from operational Alerts, and redundant
  alert families remain collapsed by the existing cleanup logic.
- Pipeline card names, nested field/visual names, schedules, facts, and metadata
  wrap without ellipsis. Secondary metadata uses its own line.
- Visuals and Power BI Tables use a versioned session preference and start
  hidden for both new and existing browser sessions after the update.

## Files Changed

- `app/models.py`, `app/routers/actions.py`: expose asset source type to Alerts.
- `app/scanner/pbi_fetch.py`, `app/routers/reports.py`, `app/routers/lineage.py`:
  lazy semantic-model discovery and enabled report refresh controls.
- `app/static/app.js`, `app/static/style.css`: alert logos, simplified columns,
  wider Owner controls, and neutral styling.
- `app/routers/dashboard.py`: select the latest completed scan for the summary.
- `app/static/app.js`, `app/static/style.css`: Pipeline default visibility and
  non-truncating node layout.
- `tests/test_actions_dedupe.py`, `tests/test_pbi_fetch.py`,
  `tests/test_report_refresh.py`, `tests/test_dashboard.py`,
  `tests/test_lineage_display.mjs`: regression coverage.

## Commands And Checks

- `/tmp/data-governance-test-env/bin/python -m pytest -q`: 277 passed.
- `node --check app/static/app.js`: passed.
- `/tmp/data-governance-test-env/bin/python -m compileall -q app`: passed.
- `git diff --check`: passed.
- `node tests/test_lineage_display.mjs`: passed.
- Local browser verification with a deliberately long script name: labels and
  metadata had no hidden overflow or ellipsis; default columns were Sources and
  Scripts; Visuals and Power BI Tables could be enabled; no console errors.
- Production updater installed build `20260816-232302`; service restart reported
  success.
- Live Alerts page: logos visible, no Type column, Owner controls fully visible,
  and no decorative purple asset badges.
- Live dashboard: Last Scan displays `36 reports` and `130 sources`, not nulls.
- Live report refresh: request accepted, audit event recorded, and Power BI later
  reported `Completed` with a new completion timestamp.
- Live materialized-view validation: read-only PostgreSQL query returned 487,416
  rows and current week/month maxima. Exact relation modification time was not
  readable with the available database role.

## Open Questions

- Flow-to-Pipeline production matching remains unverified because the matching
  target tables are not yet present.
- The available PostgreSQL role cannot independently read the materialized
  view's storage modification timestamp.
- Pipelines wrapping/default-column changes are on `origin/main` but are not yet
  installed in the production app because no fresh Update App approval was given.

## Next Step

After the user explicitly approves Update App, install the latest `origin/main`
build and verify long production source/script names plus the two default-hidden
columns in Citrix.
