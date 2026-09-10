# 2026-09-10 NASCA active Excel session: test plan

- Change/PR: follow-up to PR #107; use the signed-in desktop Excel session for NASCA recovery when Excel is already open
- Code baseline: `2fec423070f6e3164eb30c549febb26b97ea91c2`
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows/Python 3.13 synthetic COM fixtures and the explicitly requested BI-desktop live MTracker_subs recording

## Prerequisites and test data

Use the checkout-owned `.venv` through `tools/check.ps1`. Synthetic COM fixtures
must represent both states: no active Excel session and an existing user Excel
session with a workbook already open. The live check uses the unchanged
MTracker_subs recording and its existing authenticated Chrome profile. Do not
publish the downloaded workbook or raw business rows.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | With no active Excel object, normalize OLE-wrapped and opaque NASCA `.xlsx` fixtures. | Metronome creates a dedicated hidden `DispatchEx` instance, exports CSV, closes the temporary workbook and quits the owned Excel instance. | Focused repository test result. |
| T-02 | Expose an active Excel fixture with a prior workbook and non-default application settings, then normalize a NASCA fixture. | Metronome reuses the active signed-in session, closes only the protected workbook, restores all changed settings and reactivates the prior workbook; it never calls `Quit`. | Focused repository test result. |
| T-03 | Remove pywin32 modules from the fixture and invoke NASCA recovery. | The existing explicit pywin32/desktop-Excel prerequisite error remains. | Final-head Python CI. |
| T-LIVE-01 | On the BI desktop, leave the existing user workbook open, update to the merged head, open MTracker_subs > Review recording, and click Test recording without re-recording or starting another download. | One UUID-staged 57.7 MB download completes, the active Excel session performs NASCA normalization, and recording validation succeeds without changing or closing the user's workbook. | Protected live reference and recording test ID. |
| T-LIVE-02 | Only after T-LIVE-01 passes, run MTracker_subs once and wait for its configured SQL replace to commit. In pgAdmin, rerun read-only aggregate checks for row count, column count/names, year range, distinct country count and nulls in country/model/qty. | The run succeeds and the aggregate shape is consistent with the pre-run baseline; no manual database edits occur. | Protected run/database references. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv,tests/test_flow_sql.py::test_nasca_excel_recovery_borrows_and_restores_active_excel `
  -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py
```

Final-head CI is the authoritative full Python regression.

## Acceptance and cleanup

Accept when both COM ownership paths pass focused tests, required final-head CI
passes, and the explicitly requested live recording validation succeeds. Run
production SQL only after that live validation. Preserve the user's existing
Excel workbook and settings. Stop a live test worker if it remains blocked in
Excel automation; do not force a SQL run around a failed validation. Retain
only opaque live references in repository or PR evidence.
