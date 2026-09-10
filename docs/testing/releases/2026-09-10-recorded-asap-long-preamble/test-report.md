# Recorded ASAP long-preamble recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 10:59
- Tested code revision: working tree based on `b5ac2b428308c1fdd6cf50004cf4cfed34123cdf`; commit and final CI pending
- Environment: Windows 11, Python 3.12.14, pytest 9.1.1, openpyxl 3.1.5, Playwright 1.62.0, Chrome 152
- Overall finding: PASS for the focused long-preamble content detection, ASAP normalization, and recorded-browser-to-SQL regression; FAIL reproduced on the deployed baseline; full local suite, final CI, and post-merge live retest are pending at this cutoff

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| LIVE-DIAG-01 | Waited three minutes, refreshed Metronome in the remote Edge browser, attempted Run on production `MTracker_subs`, followed the engine-change guard into Review recording, and replayed Test recording without altering the five navigation steps. | Deployed main `b5ac2b42`; BI desktop; remote Edge/Chrome Remote Desktop; 2026-09-10 10:35–10:42 UTC | PASS as a baseline diagnostic: navigation reached the download, then validation failed with `The downloaded Excel workbook is not a complete XLSX ZIP container`. The step editor showed output format `xlsx`. | Protected live evidence reference `LIVE-MTRACKER-20260910-A`; no workbook, route, or screenshot published. |
| DET-LONG-01, ASAP-NORM-01, REC-SQL-LONG-01 | `python -m pytest tests/test_recorded_output_storage.py tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q` | Final working tree based on `b5ac2b42`; Windows/Python 3.12.14/Playwright Chrome | PASS: 22 passed in 16.26s. The browser case delivered a long UTF-16 LE `.xlsx`-named text response, normalized only its final two-row table, and supplied that CSV to the SQL-loader double. | Local pytest output. |
| Syntax and whitespace | `python -m py_compile app/flow_worker.py app/flow_recording_runtime.py tests/test_recorded_output_storage.py tests/test_recording_optional_checks.py`; `git diff --check` | Same working tree/environment | PASS; only configured LF-to-CRLF notices were emitted. | Local command output. |

## Pending checks

| IDs | Status | Reason / next action |
| --- | --- | --- |
| Full Python suite | RUNNING | Started on the final working tree with JUnit output; record actual counts and failures when complete. |
| Final-head CI | NOT RUN | PR and final head do not exist yet. |
| LIVE-RETEST-01 | NOT RUN | The correction is not yet merged/deployed. Refresh the BI desktop only after new main is visible, then retest/save/run the unchanged flow. |

## Findings and limitations

The first correction restored content-first handling, but the live report exposed
two older bounded scans that still assumed the real table appeared inside the
first 4 KiB and first 200 rows. MTracker's report/filter preamble exceeds both
limits. Its UTF-16 text therefore retained the `.xlsx` fallback, and even when
classified as text the recorded path could preserve preamble rows instead of
using ASAP's final-section normalization.

This follow-up reads a bounded 128 KiB prefix, searches up to 2,000 candidate
lines, and applies the same ASAP preamble mode to recorded ASAP downloads that
scan-selected flows already use. Binary signatures and workbook-container
validation remain ahead of text detection. The SQL test uses a capture double;
only LIVE-RETEST-01 can prove the protected portal payload and real SQL commit.

## Merge evidence

Pending. The PR testing section must record the final CI catalogue, run URL,
head SHA, and result before merge.
