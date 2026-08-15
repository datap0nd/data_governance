# Agent Handoff

## Current Objective

Finish live run `#114` of the 66-week ASAP FOTA backfill, then prove the
external transform and atomic managed-snapshot refresh of
`meto_db.bi_reporting.ASAP_Fota` in the live target system.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest runtime commit before this handoff update: `d196306`
- Public repo: no, private
- Push status: `d196306` verified on `origin/main`; publish this handoff update next
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

## Files Changed

- `app/flow_worker.py`: live ASAP slider readback, export-toolbar discovery,
  direct Export Options handling, input-based Export actions, and flat Excel
  format recognition.
- `tests/test_flow_worker_discovery.py`: regression coverage for the live control
  shapes and export format.
- `transforms/asap_fota_unpivot_v1.py`: strict external FOTA reshaping contract.
- `docs/metric_contracts.md`: FOTA snapshot grain, date logic, and acceptance rules.

## Commands And Checks

- `PYTHONPATH=. .../pytest -q`: 242 passed in 3.11s.
- Remote `main`: `d1963061cc4edf806e9f8a2f7ecfd611f0ba3028` verified.
- Citrix build `#20260815-204443`: deployed from `d196306`.
- Live run `#114`: running. Three weekly files cleared the download,
  normalization, and MX Share copy path; the UI showed `Configuring export 4 of
  66` at this handoff.
- Live week 1: 2025-W20, Date 2025-05-11 through 2025-05-17, 300,548 rows.
- Live week 2: 2025-W21, Date 2025-05-18 through 2025-05-24, 281,088 rows.
- Observed cadence is about five to six minutes per week, so the initial backfill
  is a multi-hour run.

## Open Questions

- Run `#114` is not accepted until all 66 exports and transforms succeed and the
  live SQL table proves 66 distinct weeks, minimum 2025-W20, maximum 2026-W33,
  a positive row count, and the contracted columns.
- The report contains roughly 300k rows per week, not the earlier rough 90k
  estimate. Reassess storage and recurring full-history download cost after the
  backfill, without changing the requested full-table SQL replacement behavior.

## Next Step

Monitor run `#114` to terminal state. If it succeeds, validate the SQL table in
the live system against the full acceptance checklist before calling FOTA done.
