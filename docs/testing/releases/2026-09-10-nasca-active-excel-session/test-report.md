# 2026-09-10 NASCA active Excel session: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10T17:41:01Z
- Tested code revision: baseline `2fec423070f6e3164eb30c549febb26b97ea91c2` plus the uncommitted active-session implementation and tests
- Environment: Windows checkout-owned Python 3.13.15 environment; synthetic pywin32 COM fixtures; explicitly requested BI desktop using Edge/Chrome and desktop Excel
- Overall finding: focused synthetic checks pass. The live baseline proved download selection and reached normalization, but isolated `DispatchEx` did not finish while the user's signed-in Excel session was already open; no SQL ran.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-01, T-02 | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv,tests/test_flow_sql.py::test_nasca_excel_recovery_borrows_and_restores_active_excel -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py` | Baseline `2fec423070f6e3164eb30c549febb26b97ea91c2` plus uncommitted implementation/tests; Windows; Python 3.13.15 | PASS; 3 passed in 1.86s; syntax passed; no skips/warnings | `.test-runs/20260910T173656267Z-38528-fc2e75df/result.json` |
| T-01 final fingerprint | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv -SyntaxPath tests/test_flow_sql.py` | Same implementation with final ownership assertion; Windows; Python 3.13.15 | PASS; 2 parameterized cases passed in 1.27s; syntax passed; no skips/warnings | `.test-runs/20260910T174013872Z-13904-b13d11db/result.json` |
| T-02 final fingerprint | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_nasca_excel_recovery_borrows_and_restores_active_excel -SyntaxPath app/flow_worker.py` | Same final source/test fingerprint; Windows; Python 3.13.15 | PASS; 1 passed in 0.74s; syntax passed; no skips/warnings | `.test-runs/20260910T174040135Z-23288-be023750/result.json` |
| T-LIVE-BASELINE | Updated Metronome to `2fec42307`, replayed the unchanged MTracker_subs recording once, observed its single UUID-staged 57.7 MB download and stopped the worker after 20 minutes in `Validating downloaded output`. | BI desktop; build `2fec42307`; recording test 158 | FAIL; download completed, prior incomplete-ZIP error did not recur, but isolated Excel validation did not complete. SQL was disabled for the validation and never started. | `LIVE-MTRACKER-20260910-F` |
| DB-BASELINE | In pgAdmin, ran SELECT-only aggregate/metadata checks against `bi_staging.asap_mtracker_country`. | BI desktop database before any production retry | PASS; 145,990 rows, 14 expected columns, years 2024-2026, 15 countries, zero nulls in country/model/qty | `LIVE-MTRACKER-DB-20260910-A` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-03 | NOT RUN | Covered by authoritative final-head CI rather than duplicating a local superset. | Confirm the required CI result on the PR head. |
| T-LIVE-01 | NOT RUN | The active-session fix is not yet merged and installed. | Merge after CI, update the BI desktop, and rerun the unchanged recording. |
| T-LIVE-02 | BLOCKED | Production SQL is intentionally gated on a passing T-LIVE-01. | Run once only after recording validation passes, then repeat SELECT-only aggregate checks. |

## Findings, limitations and retests

An initial sandboxed verification attempt could not execute the repository
Python binary (`.test-runs/20260910T173624252Z-33816-3beeb6b8`); rerunning the
same supported command with access to the checkout-owned environment passed.
After tightening the owned-instance assertion, the two non-overlapping paths
were rerun separately on the final source/test fingerprint and both passed.
The live baseline is revision-specific and remains a failure. It established
that the recorder selected the correct download and that PR #107 restored the
normalization branch, then isolated the remaining hang to the Excel-session
handoff. The focused fixtures do not emulate the corporate NASCA provider, so
the explicitly requested post-merge live check remains decisive.

## Merge evidence

Pending. Record the final `Merge ready` run URL and exact PR head SHA in the PR
testing section before a head-pinned merge. The PR will retain CI evidence that
finishes after this report's cutoff.
