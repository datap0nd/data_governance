# Agent Handoff

## Current Objective

Live-verify one bundled ASAP flow that downloads the Global/Region and Selected
Countries Excel exports, then atomically creates or refreshes
`meto_db.bi_reporting.ASAP_TI` without accumulating prior snapshots.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Latest commit: `a211a0c Confirm ASAP raw Excel exports`
- Stable rollback tag: `asap-ti-pre-multiexport-stable-2026-08-15` at `2560be8`
- Public repo: no, private
- Push status: `a211a0c` verified on `origin/main`
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
- Do not change PostgreSQL schema privileges without fresh action-time approval.

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
- Remote `main`: `a211a0c32b404b3777f82821ff24c61b5a5488fd` verified.
- Citrix build `#20260815-150320`: deployed from that commit.
- Live run 105: both XLSX downloads completed and normalized.
- Global/Region artifact: 105,050 rows, 934,452-byte original workbook.
- Selected Countries artifact: 926,160 rows, 8,906,633-byte original workbook.
- SQL staging failed closed with SQLSTATE `42501`, permission denied for schema
  `bi_reporting`; PostgreSQL confirmed rollback and no SQL changes were committed.

## Open Questions

- `dg_upload_pguser` needs schema-level permission to create the managed staging
  table in `bi_reporting`. This is required even when refreshing an existing
  target because the atomic snapshot uses a transaction-scoped staging table.
- The first successful database run and a second non-accumulating refresh still
  require live PostgreSQL validation. Production headless mode also remains to
  be re-enabled and tested after headed debugging succeeds.

## Next Step

After fresh approval, grant `USAGE, CREATE` on schema `bi_reporting` in database
`meto_db` to `dg_upload_pguser`, rerun the flow, validate both source counts and
the target table OID, then run once more in headless mode and verify the row count
does not double.
