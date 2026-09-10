# NASCA recording validation parity: test plan

- Change/PR: keep the production SQL normalization requirement during a safe **Test recording** run, while continuing to suppress the SQL write itself.
- Code baseline: `2288922968d17915f5b85fd5b1f540ac1b7fa137`; deployed baseline build `228892296`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11, checkout-owned Python 3.13, synthetic Chrome download and Excel-COM seam, plus the explicitly requested signed-in BI desktop retest.

## Prerequisites and test data

- Use the unchanged five-step `MTracker_subs` recording with SQL handoff enabled and no concurrent manual portal download.
- Use a fictional opaque non-ZIP `.xlsx` payload and a fake Excel-COM exporter locally. Do not copy the protected workbook, portal address, credentials, or business rows into the repository.
- Confirm the live Metronome header matches the merged main commit before retesting.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| RV-01 | Validate a recorded Flow whose production settings enable SQL. Inspect the job passed to recorded execution. | Validation disables SQL writes but retains an internal table-normalization requirement. | Focused unit test and result ID. |
| RV-02 | Replay a synthetic recorded `.xlsx` download containing opaque NASCA-like bytes with the validation requirement set. | The download enters desktop Excel recovery, preserves original bytes, produces a normalized CSV and reports one row; no SQL loader runs. | Focused synthetic browser test and result ID. |
| RV-03 | Run existing unchecked-download and corrupt-workbook coverage in final CI. | A recording with no tabular downstream requirement may still preserve ordinary raw downloads; an unrelated corrupt modern workbook remains rejected. | Final-head GitHub Actions run. |
| RV-04 | On the exact merged main build, open `MTracker_subs`, choose **Review recording** then **Test recording**, and leave the saved steps and editor settings unchanged. | The 57.7 MB download completes, the progress message reports desktop Excel opening the NASCA-protected content, normalized validation passes, and SQL is not executed by the test. | Protected live run reference, build SHA and terminal status. |
| RV-05 | After RV-04 passes, save normally if requested by the UI and run the existing production Flow once. Check the target with aggregate SELECT-only queries. | The Flow completes its configured replace/load; row count, expected columns, coverage and key-field completeness are plausible. | Protected run reference and sanitized aggregates only. |

## Automated checks

Run `tools/check.ps1 -Mode Setup` once. Then use `tools/check.ps1 -Mode Verify` for the RV-01 and RV-02 nodes with syntax checks for the two changed runtime files and two test files. Do not run a local superset; final-head CI supplies the full regression.

## Acceptance and cleanup

Accept when both focused regressions pass, final-head `Merge ready` passes, the PR records its exact tested head and Actions URL, and the unchanged live recording passes on that merged build before a production run is attempted. Keep only normal private retention evidence. The change adds no control or layout. Rollback removes the validation-only marker and restores the prior early ZIP rejection; it does not alter recordings or SQL configuration.
