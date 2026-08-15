# Agent Handoff

## Current Objective

Live-verify one bundled ASAP flow that downloads the Global/Region and Selected
Countries Excel exports, then atomically creates or refreshes
`meto_db.bi_reporting.ASAP_TI` without accumulating prior snapshots.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest commit before this handoff update: `5410807 Update ASAP bundled flow handoff`
- Stable rollback tag: `asap-ti-pre-multiexport-stable-2026-08-15` at `2560be8`
- Public repo: no, private
- Push status: `5410807` verified on `origin/main`; publish this handoff update next
- Preserve untracked `governance.db-shm` and `governance.db-wal`

## Decisions Made

- One flow owns both export views and starts one SQL transaction only after both
  files download and normalize successfully.
- Raw TechInsights tables export through the hover information control, the
  `Export` submenu, the `Excel` item, and the final `Export to Excel` dialog.
- Excel originals are preserved and normalized with a `Metronome Export View`
  lineage column.
- Managed snapshot mode creates a missing target or refreshes an existing target
  in place, unions differing source columns, and rolls back the entire SQL stage
  on failure.
- The configured Metronome connection label is `dg_upload_pguser`, but its
  PostgreSQL role is `metomx`.
- With user approval, `metomx` now has `USAGE` and `CREATE` on schema
  `meto_db.bi_reporting`. Read-back returned true for both privileges.
- The production flow remains active in headless mode on its monthly day-1
  08:00 schedule.

## Files Changed

- `app/flow_worker.py`: wait for ASAP overlays, accept preserved navigation,
  recognize populated raw tables, find the unlabeled table control, and complete
  the raw Excel submenu and confirmation-dialog path.
- `tests/test_flow_worker_discovery.py`: regression coverage for the live ASAP
  navigation, raw-table readiness, geometric control, Excel menu, and final
  confirmation behavior.

## Commands And Checks

- `PYTHONPATH=. .../pytest -q`: 226 passed in 3.76s.
- `git diff --check`: passed before both scoped commits.
- Remote `main`: `5410807` verified before this handoff update.
- Citrix build `#20260815-150320`: deployed from runtime commit `a211a0c`.
- PostgreSQL grant: `GRANT USAGE, CREATE ON SCHEMA bi_reporting TO metomx;`
  succeeded through the saved `meto_reporting_rw` connection. Direct privilege
  checks returned `usage_ok=true` and `create_ok=true`.
- Live headed run 106: succeeded and committed the full two-export bundle.
- Direct PostgreSQL validation after run 106: table OID `213183`, 103,110 rows,
  11 columns, and every column is `TEXT`.
- Direct lineage counts after run 106: Global/Region 10,500 rows and Selected
  Countries 92,610 rows.
- Live headless run 107: succeeded with the same two artifacts and a managed
  snapshot refresh.
- Direct PostgreSQL validation after run 107: OID still `213183`, total still
  103,110 rows, and both lineage counts unchanged. The second run did not append
  or recreate the target.
- The running headless flow exposed its Stop control in the Flows list.

## Open Questions

- None for the bundled flow acceptance path. Future source schema changes should
  still be observed on their first live run because managed snapshot refresh adds
  new nullable `TEXT` columns while preserving older target columns.

## Next Step

Monitor the next scheduled day-1 08:00 run and confirm it retains OID `213183`
and the expected non-accumulating snapshot behavior.
