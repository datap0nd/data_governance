# Recorded download processing regression: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 09:38
- Tested code revision: working tree based on `7291a559bd160a2f8ecabba5e15817da370b306b`; exact committed revision and final CI pending
- Environment: Windows 11 Pro 10.0.26200, Python 3.12.14, pytest 9.1.1, openpyxl 3.1.5, Playwright 1.62.0, Chrome 152.0.7977.83
- Overall finding: PASS for the implemented content-detection, storage, Excel-family, SQL, and recorded-browser regression checks executed so far; full suite and live work-PC recovery pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| HIST-01 | Inspected `git log -L` and diffs for commits `3dac36c`, `3ee7e73`, `c43a6df`, `e6f2ca9`, and `85fd024`; compared scan-selected `_store_completed_download` calls with recorded `acquire`. | Baseline `7291a559`; local read-only history review | PASS: commit `85fd024` placed modern-Excel suffix fallback ahead of tabular-text handling, while the recorded runtime supplied a hard-coded `excel_plain_text` assertion that the recording had not discovered. | PR change explanation. |
| DET-01, HTML-01, XLS-REG-01 | `python -m pytest tests/test_recorded_output_storage.py tests/test_flow_excel_formats.py tests/test_flow_sql.py -q --disable-warnings --maxfail=1` | Uncommitted implementation based on `7291a559`; Windows/Python 3.12.14 | PASS: 122 passed in 40.88s. | Local command output. |
| REC-SQL-01 (diagnostic run) | `python -m pytest tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q --disable-warnings` | Same working tree; Playwright/Chrome environment above | FAIL: product path completed download, normalization, and SQL-loader call; the new test double omitted existing `rows_written` result metadata, causing a test-only `KeyError`. | Local pytest traceback. |
| REC-SQL-01 (retest) | Same focused browser command after completing the test double's result contract. | Same working tree/environment | PASS: 1 passed in 5.19s. | Local command output. |
| REC-SQL-01 and recording regressions | `python -m pytest tests/test_recording_optional_checks.py -q --disable-warnings --maxfail=1` | Same working tree/environment | PASS: 27 passed in 59.83s. | Local command output. |
| Syntax | `python -m py_compile app/flow_worker.py app/flow_recording_runtime.py tests/test_recorded_output_storage.py tests/test_recording_optional_checks.py` | Same working tree; Python 3.12.14 | PASS. | Local command output. |
| Whitespace | `git diff --check` | Same working tree | PASS; only Git's configured LF-to-CRLF notices were emitted. | Local command output. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full Python suite | NOT RUN | Implementation is still being finalized. | Run on the committed code revision before PR merge. |
| Final-head CI | NOT RUN | No PR or final head exists yet. | Push the final branch, wait for required checks, and record run URL/SHA/result in the PR testing section. |
| LIVE-01 | NOT RUN | This local session is not the authenticated work PC and no live portal/database run was performed. | Update the BI desktop from merged `main` and rerun the existing saved MTracker Flow; record only sanitized evidence. |

## Findings, limitations and retests

The second screenshot is a more precise symptom produced by PR #99, not a
second workbook defect: the captured response is not an OOXML ZIP. History
shows the recorded path converted its user-facing `.xlsx` output label into an
unproven ASAP semantic assertion. The September 9 Excel-family expansion then
allowed the filename suffix to override decodable tabular content. The fix
removes that invented assertion and restores content-first routing for strong
delimited-text evidence while preserving strict container validation for
opaque or damaged `.xlsx` files.

The first REC-SQL-01 attempt is retained above. Its traceback occurs after the
captured artifact had already reached the SQL test double; completing the
double's normal return shape made the unchanged product behavior pass. The
synthetic download contains no business data and does not prove the live ASAP
response, authentication, network share, or PostgreSQL permissions.

## Merge evidence

Pending. Before merging, the PR testing section will record the required final
CI run URL, tested head SHA, and result. GitHub will record the actual merge SHA.
