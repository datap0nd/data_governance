# Browser filename handoff: test report

- Plan: [test-plan.md](test-plan.md).
- Evidence cutoff: 2026-09-10T21:39:00Z.
- Tested source: baseline `7dbf0e5be05a9d98d2d53b205179d06d82663b07` plus the uncommitted four-file implementation/test diff in this PR.
- Environment: Windows, checkout-owned Python 3.13.15, locked dependencies; synthetic Chromium downloads with an Excel stub. Explicitly requested live BI desktop uses build `7dbf0e5be`, Chrome `152.0.7977.83` for MTracker_subs validation.
- Finding: preserving the browser's extensionless staging path discarded the response filename from format detection. An opaque NASCA wrapper consequently classified as binary before desktop Excel recovery. The fix carries the response filename separately; the protected file stays in place.

## Executed evidence

| ID | Command/procedure | Actual result | Evidence |
| --- | --- | --- | --- |
| Setup | `.\tools\check.ps1 -Mode Setup` | PASS; checkout-owned environment and locked dependencies ready. | `.test-runs/20260910T213147211Z-2596-bd73d9b8/result.json` |
| L-01 baseline subs | Unchanged recording revision 46, validation test 161, build `7dbf0e5be`. | FAIL after download: extensionless source classified as binary. No production SQL ran. | `LIVE-THREE-FLOWS-20260911-SUBS-161` |
| L-01 baseline country | Retested existing country recording on `7dbf0e5be`. | FAIL with the same extensionless binary classification. No production SQL ran. | `LIVE-THREE-FLOWS-20260911-COUNTRY-BASELINE` |
| F-01/F-02/F-03 initial | `tools/check.ps1 -Mode Verify` with both NASCA parameters, text-to-SQL selector and the three new SQL selectors; syntax paths: `app/flow_worker.py`, `app/flow_recording_runtime.py`, `tests/test_recording_optional_checks.py`, `tests/test_flow_sql.py`. | 6 passed, 2 failed in 11.04s; syntax passed. OLE path and all five unit cases passed. The second browser case and text companion failed at fixture creation (WinError 183, shared managed Flow folder), before exercising their assertions. | `.test-runs/20260910T213723942Z-1528-b582181e/result.json` |
| F-01 opaque failed-case rerun | `.\tools\check.ps1 -Mode Verify -TestPath 'tests/test_recording_optional_checks.py::test_recording_validation_normalizes_nasca_input_for_configured_sql[opaque-extensionless-wrapper]' -SyntaxPath app/flow_worker.py,app/flow_recording_runtime.py,tests/test_recording_optional_checks.py,tests/test_flow_sql.py` | PASS; 1 passed in 8.25s; syntax passed; no skips/warnings. Exact extensionless source reached Excel stub. | `.test-runs/20260910T213757313Z-33432-97804c6e/result.json` |
| F-02 failed-case rerun | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql` | PASS; 1 passed in 8.22s; no skips/warnings. | `.test-runs/20260910T213757303Z-34416-baee79f3/result.json` |

## Review and pending evidence

One bounded diff review checked filename propagation, unchanged source-path handling, content precedence, add-in rejection, and failure before publication. No UI journey changed. The filename is only a suffix hint and is never opened as a path. Existing callers retain their source-name behavior.

The initial combined local command used the exact five selectors listed in the plan, with both F-01 parameters selected together; the reruns isolated only the failed cases. Eight distinct focused cases now have passing evidence. Synthetic Excel cannot certify the corporate protection provider.

Final-head CI is pending at this cutoff. Record its run URL and exact head SHA in the PR before merging. Inflow's requested production run was started on the baseline and was exporting CSV at the last observation; no success is claimed. Fixed-build validation/production runs and SELECT-only database checks remain pending. Append dated, revision-specific live evidence after it exists; preserve these failures.
