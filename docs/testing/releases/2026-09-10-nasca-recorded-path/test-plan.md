# 2026-09-10 NASCA recorded-path preservation: test plan

- Change/PR: preserve the authoritative ASAP staged download through recorded-flow validation
- Code baseline: `c3fadd2744f4e3db69cb53a633b0517ee601401a`
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows/Python 3.13 synthetic browser fixture and the explicitly requested BI-desktop MTracker_subs recording

## Prerequisites and test data

Use the checkout-owned `.venv` only through `tools/check.ps1`. The synthetic
fixture represents an OLE-wrapped NASCA workbook downloaded through the recorded
ASAP staging-completion path. The live check uses unchanged recording revision
46, the existing authenticated browser profile, and signed-in desktop Excel.
Do not publish the workbook, portal route, or business rows.

## Test cases

| ID | Actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Replay a synthetic recorded ASAP download that requires SQL-shaped validation; capture both the staging-completion path and the path passed to Excel recovery. | Both resolved paths are identical; the recorder does not create a UUID byte-copy before NASCA/Excel opens the workbook. Validation produces the normalized CSV and keeps SQL disabled. | Focused repository check. |
| T-02 | Run the unchanged MTracker_subs recording test on the merged BI-desktop build with SQL disabled. | Download and validation pass, the existing Excel session remains usable, and no database write occurs. | Protected live-test reference. |
| T-03 | Only after T-02 passes, run production MTracker_subs exactly once. | The flow and configured SQL replace complete successfully. | Protected run reference. |
| T-04 | After T-03, run SELECT-only pgAdmin checks for row count, 14-column schema, year range, country cardinality, and nulls in country/model/qty. | Results are coherent against the pre-run baseline and contain no structural/null regression. | Protected database reference. |

## Automated command

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_recording_optional_checks.py::test_recording_validation_normalizes_nasca_input_for_configured_sql `
  -SyntaxPath app/flow_recording_runtime.py,tests/test_recording_optional_checks.py
```

Final-head CI is the authoritative full Python regression.

## Failure, recovery and cleanup

If validation fails, do not start production SQL. Preserve the existing open
Excel workbooks and application settings. The run staging directory remains
under the existing retention lifecycle; this change adds no deletion behavior.
Use only opaque references for live evidence.
