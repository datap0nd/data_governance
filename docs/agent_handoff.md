# Agent Handoff

## Current Objective

Deploy and live-verify one bundled ASAP flow that downloads the Global/Region
and Selected Countries Excel exports, then atomically creates or refreshes one
`ASAP_TI` PostgreSQL table with both sources.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Runtime commit: `6f4a175 Add atomic multi-export ASAP flows`
- Stable rollback tag: `asap-ti-pre-multiexport-stable-2026-08-15` at `2560be8`
- Public repo: no, private
- Push status: runtime commit verified on `origin/main`
- Preserve untracked `governance.db-shm` and `governance.db-wal`

## Decisions Made

- One logical flow owns both export views. Every selected export must download
  and validate before the one downstream SQL transaction begins.
- Export views are catalogued explicitly and selected in the flow builder.
- Reports without a week prompt use `period_strategy=none` and do not click RUN.
- Excel originals are preserved and normalized to SQL-ready UTF-8 CSV files.
- Every normalized file receives `Metronome Export View` source lineage.
- Managed snapshot mode unions differing source columns by normalized name,
  creates a missing table, or refreshes an existing table without replacing its
  object. Missing source-specific fields are null.
- Monthly scheduling supports day of month. Invalid dates are skipped.
- The old startup migration that forced XLSX flows back to CSV was removed.

## Files Changed

- `app/database.py`: persist export-view selection and monthly schedule day.
- `app/routers/flows.py`: validate bundle views, no-period reports, XLSX, and monthly schedules.
- `app/flow_worker.py`: scan and execute multiple views, raw-table export fallback, Excel normalization, and source lineage.
- `app/flow_sql.py`: union different bundle schemas in one managed snapshot.
- `app/static/app.js`: bundle, XLSX, no-period, monthly, and SQL builder controls.
- `app/static/index.html`: main JavaScript cache bump.
- `README.md`: document the bundled execution contract.
- `tests/test_flows.py`, `tests/test_flow_sql.py`, `tests/test_flow_worker_discovery.py`: regression coverage.

## Commands And Checks

- Full Python suite: `213 passed in 2.63s`.
- `node --check app/static/app.js`: passed.
- `node --check app/static/flow_run_log.js`: passed.
- Python compilation for changed runtime modules: passed.
- `git diff --check`: passed.
- Real temporary PostgreSQL integration: passed first-run create and second-run
  refresh using two files with different columns and column order. Verified
  stable table OID, no duplicate accumulation, expected per-source counts,
  schema evolution, and source-specific nulls.
- Live Metronome deployment and ASAP download: not yet run.

## Open Questions

- Citrix is connected, but the nested work desktop is currently shown in a
  windowed RDP session with Outlook active. Computer Use has keyboard access but
  no coordinate targeting for the RDP window, so Edge cannot be selected safely.
- The actual Excel attachment/header shape still needs live inspection through
  the deployed worker. The implementation fails closed if no usable table header
  is found.
- Do not grant or change PostgreSQL schema permissions without a fresh exact
  approval naming the schema and privilege. First attempt the already approved
  flow with the current SQL role.

## Next Step

Have the user click the Edge icon inside the nested work desktop and leave
Metronome visible. Then update Metronome from `main`, run a targeted catalog
refresh for `ASAP -> TechInsights Smartphone M/S`, create the two-view monthly
XLSX managed-snapshot flow targeting `ASAP_TI`, and run it twice while verifying
the table and source counts in PostgreSQL.
