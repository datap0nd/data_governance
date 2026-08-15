# Agent Handoff

## Current Objective

Pass isolated 2025-W20 and latest-week FOTA smoke runs with SQL disabled, then
run the 66-week 2025-W20 through 2026-W33 backfill and prove the external
transform plus atomic managed-snapshot refresh of
`meto_db.bi_reporting.ASAP_Fota` in the live target system.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest runtime commit before this handoff update: `61fbb8e`
- Public repo: no, private
- Push status: `61fbb8e` verified on `origin/main`; publish the current header
  selection fix and this handoff update next
- Preserve untracked `governance.db-shm` and `governance.db-wal`

## Decisions Made

- Each run downloads 66 single-week flat Excel exports for 2025-W20 through
  2026-W33. SQL is a full atomic snapshot replacement, never a 12-week append.
- FOTA-specific unpivot logic stays in the external MX Share script
  `asap_fota_unpivot_v1.py`; Metronome only supplies its generic transform hook.
- Metronome downloads and normalizes every workbook before it invokes the
  transform. SQL starts only after all downloads and transformations succeed.
- The transform validates the one dynamic `YYYYWW` column against the filename
  and emits the 13 dimensions plus Week, Week Start Date, Week End Date, and
  FOTA Value.
- The live ASAP export format is `Excel with plain text`, not the formatted
  workbook option.
- The Week and Date controls are coupled two-handle sliders. Their values are
  visible labels rather than semantic handle values, so automation reads the
  labels and verifies both ranges before RUN.
- Do not start another full-range run until isolated 2025-W20 and 2026-W33
  downloads both normalize and transform successfully with SQL disabled.
- Monitor the headed worker only from the separate Metronome browser window.
  Keyboard refresh can land on the worker and detach its Playwright frame; use
  the visible mouse reload control only after verifying Metronome is foreground.

## Files Changed

- `app/flow_worker.py`: live ASAP slider readback, export-toolbar discovery,
  direct Export Options handling, input-based Export actions, flat Excel format
  recognition, and XLSX header selection that prefers contracted dimension
  labels or a valid YYYYWW label over a wider data row.
- `tests/test_flow_worker_discovery.py`: regression coverage for the live control
  shapes and export format.
- `tests/test_flow_sql.py`: regression coverage for preambles and wider unique or
  duplicate-valued data rows that must not be selected as the XLSX header.
- `transforms/asap_fota_unpivot_v1.py`: strict external FOTA reshaping contract.
  Its missing-week error now reports a bounded preview of the detected header so
  live export-shape failures are diagnosable.
- `docs/metric_contracts.md`: FOTA snapshot grain, date logic, and acceptance rules.

## Commands And Checks

- `PYTHONPATH=. uv run --python 3.11 --with pytest --with-requirements
  requirements.txt pytest -q`: 248 passed in 2.92s.
- Remote `main`: `61fbb8ed78a51fb0be1aa6052eca6407cbc749e5` verified before
  the current change.
- Citrix build `#20260816-030401`: deployed from `61fbb8e`.
- Live run `#114`: all 66 downloads completed, then the first transform failed
  because normalization selected a preamble/data row and exposed no YYYYWW
  header. SQL did not run.
- Live runs `#116`, `#117`, and clean smoke `#119` narrowed the remaining issue
  to XLSX header selection. Run `#119` completed navigation, configuration, report
  execution, and export in 4m37s, then failed before SQL with no YYYYWW column.
- Run `#118` was contaminated by a browser refresh reaching the headed worker and
  failed with `Locator.count: Error: Frame was detached`; it is not transform
  evidence.
- Clean smoke `#120` on build `#20260816-030401` again completed export in 4m30s
  but found no compact YYYYWW header. This proved the live week heading itself is
  not a bare six-digit value when header candidates are scored.
- Live week 1: 2025-W20, Date 2025-05-11 through 2025-05-17, 300,548 rows.
- Live week 2: 2025-W21, Date 2025-05-18 through 2025-05-24, 281,088 rows.
- Observed cadence is about five to six minutes per week, so the initial backfill
  is a multi-hour run.

## Open Questions

- The current header-selection fix is locally tested but not accepted until a
  live one-week export reaches the external transform successfully.
- The objective is not accepted until all 66 exports and transforms succeed and the
  live SQL table proves 66 distinct weeks, minimum 2025-W20, maximum 2026-W33,
  a positive row count, and the contracted columns.
- The report contains roughly 300k rows per week, not the earlier rough 90k
  estimate. Reassess storage and recurring full-history download cost after the
  backfill, without changing the requested full-table SQL replacement behavior.

## Next Step

Commit and push the YYYYWW-aware header-selection fix, deploy it through the
visible Metronome Update App flow, and rerun only 2025-W20 with SQL disabled.
