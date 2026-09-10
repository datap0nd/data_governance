# 2026-09-10 NASCA browser-source recovery: test plan

- Change/PR: follow-up to PR #108; open the untouched browser-managed NASCA file before publishing a retained copy
- Code baseline: `38d672ee9e3e8302adaa03840e8cc43f950cd91d`
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows/Python 3.13 synthetic COM fixtures and the explicitly requested BI-desktop live MTracker_subs recording

## Prerequisites and test data

Use the checkout-owned `.venv` only through `tools/check.ps1`. Synthetic
fixtures represent an OLE-wrapped extensionless Playwright download and an
opaque protected `.xlsx`. The live check uses the unchanged MTracker_subs
recording, its existing authenticated Chrome profile and the already open,
signed-in Excel session. Do not publish the workbook or raw business rows.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Normalize an extensionless OLE-wrapped fixture whose final retained name is `result.xlsx`. | Excel opens a same-file-record `.xlsx` hard-link alias in the browser staging folder, not the copied final target; the alias is removed, normalized CSV is produced, and the protected original is retained only afterward. | Focused repository test result. |
| T-02 | Normalize an opaque protected fixture that already has an `.xlsx` suffix. | Excel opens the original staged file directly; existing normalized CSV and retained-original metadata remain unchanged. | Focused repository test result. |
| T-03 | Repeat T-01/T-02 with an existing active Excel fixture. | The signed-in Excel session is reused and restored without quitting or closing the user's prior workbook. | Focused repository/CI result. |
| T-LIVE-01 | On the BI desktop, keep the existing manually opened workbook open, update to the merged head, and run MTracker_subs > Review recording > Test recording without re-recording. | The single browser download completes, Excel/NASCA reads the untouched staged file, validation completes, and the user workbook remains open. | Protected recording-test reference. |
| T-LIVE-02 | Only after T-LIVE-01 passes, run production MTracker_subs exactly once and wait for the configured SQL replace to commit. Then run SELECT-only pgAdmin checks for row count, 14 column names, year range, country cardinality and nulls in country/model/qty. | The production run succeeds and the table shape is coherent against the pre-run baseline; no manual database write occurs. | Protected run/database reference. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv,tests/test_flow_sql.py::test_nasca_excel_recovery_borrows_and_restores_active_excel `
  -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py
```

Final-head CI is the authoritative full Python regression.

## Failure, recovery and cleanup

If Excel cannot open the staged source, validation must fail before SQL and the
production flow must not be run. The hard-link alias must be removed on both
success and failure. Preserve the user's open workbook and application settings.
Keep only opaque live evidence references in GitHub.
