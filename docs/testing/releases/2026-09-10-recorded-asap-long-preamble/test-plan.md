# Recorded ASAP long-preamble recovery: test plan

- Change/PR: recognize and normalize recorded ASAP text exports whose report/filter preamble extends beyond the original detection limits
- Code baseline: `b5ac2b428308c1fdd6cf50004cf4cfed34123cdf`; final merged behavior must use the tested PR head
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11 with Python 3.12 and Playwright/Chrome; explicitly requested live MTracker validation on the BI desktop through the existing remote session

## Prerequisites and test data

Use fictional UTF-8 and UTF-16 LE tabular exports named `MTracker_subs.xlsx`.
Prepend more than 4 KiB and more than 200 rows of fictional report/filter text,
then separate the actual `Region`/`Units` table with a blank line. The browser
integration test serves the payload from localhost and replaces the SQL write
with a capturing test double, so it cannot change an external database.

For the live retest, first update the BI desktop to the merged `main`, refresh
Metronome in Edge, and preserve the existing five recorded navigation steps and
SQL configuration. Do not publish the portal URL, workbook, SQL contents, or
remote-desktop screenshot to GitHub.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| LIVE-DIAG-01 | On deployed baseline `b5ac2b42`, refresh Edge, open the production `MTracker_subs` Flow, attempt Run, then use Review recording → Test recording when the engine-change guard blocks the queue. | The existing five steps replay through the download and reproduce the incomplete-XLSX error without rerecording. The saved output label remains `xlsx`. | Sanitized live attempt reference in the report. |
| DET-LONG-01 | Classify UTF-8 and UTF-16 LE text responses named `.xlsx` with a report/filter preamble longer than 4 KiB and 200 rows. | Content is detected as delimited text instead of falling back to the filename suffix. | Focused pytest output. |
| ASAP-NORM-01 | Normalize the same payload using the recorded ASAP path. | The final rectangular table is selected after the preamble; the CSV has `Region,Units` and exactly two data rows. | Focused pytest output. |
| REC-SQL-LONG-01 | In headless Chrome, download the UTF-16 LE payload under an `.xlsx` server filename with SQL handoff enabled and capture the artifacts passed to the SQL loader. | One UTF-8 CSV artifact with two rows reaches the SQL loader; no XLSX ZIP validation occurs. | Focused pytest output. |
| EXCEL-REG-01 | Run the recorded storage, Excel-family, SQL, and recording download suites. | Genuine Excel packages still validate and normalize; opaque or corrupt Excel-labelled bytes still fail closed. | Local pytest and final CI. |
| LIVE-RETEST-01 | After the fix is merged and the BI desktop reports the new main revision, refresh Edge, retest the unchanged recording, save the validated flow, and run it once. | Download validation completes, the normalized artifact reaches SQL, and the Flow succeeds without scan recreation or navigation rerecording. | Sanitized run ID/status and protected evidence reference. |

## Automated checks

```powershell
$py = 'C:\Users\keeoh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py -m pytest tests/test_recorded_output_storage.py tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q
& $py -m pytest tests -q --durations=20 --junitxml=test_reports/recorded-asap-long-preamble-full.xml
& $py -m py_compile app/flow_worker.py app/flow_recording_runtime.py tests/test_recorded_output_storage.py tests/test_recording_optional_checks.py
git diff --check
```

Final-head GitHub Actions is authoritative for the complete Linux Python and
frontend regression. Record the run URL and head SHA in the PR before merging.

## Acceptance and cleanup

Accept when DET-LONG-01, ASAP-NORM-01, REC-SQL-LONG-01, EXCEL-REG-01, syntax,
whitespace, and final-head CI pass. LIVE-DIAG-01 must preserve the observed
baseline failure. LIVE-RETEST-01 must run only after the new main revision is
visible on the BI desktop; do not turn a synthetic pass into live evidence.
Temporary test dependencies and JUnit output are task-local and must not be
committed. Reverting the change restores the 4 KiB/200-line classification
limits and the recorded ASAP preamble loss.
