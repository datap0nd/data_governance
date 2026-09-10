# NASCA direct pywin32 cell reads: test plan

- Baseline: main 6062b6098a43d3c344b2bfe96f4d544062b0b9c6.
- Scope: open the original protected browser download; read worksheet values
  through pywin32 in bounded batches instead of creating a hard-link alias and
  calling Excel SaveAs. Preserve the existing integration-event setting,
  disable workbook VBA/link updates, and restore borrowed Excel settings.
- Report: [test-report.md](test-report.md).

## Local verification

Use the checkout-owned Python 3.13 environment through tools/check.ps1.
Setup was completed in this checkout for the preceding filename-handoff PR.
Run this non-overlapping selection, with both changed Python files as syntax
targets:

    ./tools/check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_excel_com_rows_preserve_values_blanks_duplicates_and_batch_boundaries,tests/test_flow_sql.py::test_nasca_com_failure_identifies_stage_and_restores_desktop,tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv,tests/test_flow_sql.py::test_nasca_excel_recovery_borrows_and_restores_active_excel -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py

| Case | Expected result/evidence |
| --- | --- |
| C-01 values and batching | Preserve Unicode, blank cells, zeros, duplicates and Excel error text; read each row once across batch boundaries; handle a one-cell range. |
| C-02 protected SQL normalization | Both OLE and opaque fixtures open at the exact original path. Read cells directly; no Excel SaveAs. Existing header/preamble normalization produces the expected CSV. |
| C-03 borrowed desktop | No quit of the user's Excel session; restore settings and active workbook. Leave integration events unchanged. |
| C-04 open/read failure | Stage and numeric COM code identify the failure without provider text. Preserve source bytes, close only an opened task workbook without saving, restore settings, and publish no artifact or SQL load. |

Synthetic COM fixtures cannot establish live NASCA compatibility. Required
final-head Merge ready provides the full application regression.

## Owner-requested live checks

The current task explicitly includes the work PC, live downloads and pgAdmin.
Use Chrome Remote Desktop's visible UI. Outflow is excluded. Preserve all
existing workbooks, query tabs, schedules, owners and destination selections.

1. Verify the installed merge revision. For each MTracker flow, Edit, Review
   recording, Test recording. Record revision, test ID, browser and outcome.
2. If validation succeeds, return to Edit Flow and save the tested recording.
   Run production once. Inspect the run log for completed files and confirmed
   SQL commit. Reconcile any uncertain SQL outcome before retrying.
3. Compare the run file's row count with SELECT-only destination aggregates.
   Treat the downloaded source as authoritative: blanks or duplicates do not
   justify filling or deduplicating. Do not substitute older data.
4. Inflow already succeeded in run 432 and was queried on the preceding
   revision. Do not rerun it solely because the Excel reader changed.
5. If Excel fails, record the exact stage; keep the draft unactivated and retain
   protected downloads. A failed validation must not replace SQL contents.

Retain evidence by opaque identifier. Do not commit business rows, private URLs,
credentials or raw traces. Temporary Python CSVs are task-owned; existing
retention rules govern saved flow artifacts. No manual data edits or cleanup
of the user's workbooks/files is part of this test.
