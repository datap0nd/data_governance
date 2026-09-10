# 2026-09-10 NASCA browser-source recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10T18:22:17Z
- Tested code revision: baseline `38d672ee9e3e8302adaa03840e8cc43f950cd91d` plus the uncommitted browser-source implementation and tests
- Environment: Windows checkout-owned Python 3.13.15; synthetic pywin32 COM fixtures; explicitly requested BI desktop using Edge/Chrome and desktop Excel
- Overall finding: the revision-specific live failure proves Excel was given the renamed retained copy. The focused regression now makes Excel open the untouched extensionless browser file through a temporary same-record `.xlsx` alias and cleans that alias.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-LIVE-BASELINE | Installed merged build `38d672ee`, replayed unchanged MTracker_subs recording once, and observed the single download reach Excel/NASCA validation. | BI desktop; build `38d672ee`; recording test 159 | FAIL; old incomplete-ZIP error and prior COM hang did not recur, but Excel rejected opening/exporting copied `MTracker_subs___1.xlsx`. SQL was disabled and did not run. | `LIVE-MTRACKER-20260910-G` |
| DB-BASELINE | SELECT-only pgAdmin aggregate/metadata checks performed before any production retry. | BI desktop database | PASS; 145,990 rows, 14 expected columns, years 2024-2026, 15 countries, zero nulls in country/model/qty | `LIVE-MTRACKER-DB-20260910-A` |
| T-01/T-02/T-03 initial | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv,tests/test_flow_sql.py::test_nasca_excel_recovery_borrows_and_restores_active_excel -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py` | Uncommitted implementation; Windows; Python 3.13.15 | FAIL; 2 passed, 1 failed in 2.12s because the synthetic opaque payload was paired with an extensionless name that correctly classified as binary before the NASCA branch. Syntax passed. | `.test-runs/20260910T182029144Z-37764-0bf6e109/result.json` |
| T-01/T-02 corrected pairing | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py` | Uncommitted implementation; Windows; Python 3.13.15 | FAIL; 1 passed, 1 failed in 1.63s because the new alias branch exposed a missing `uuid` import. Syntax passed. | `.test-runs/20260910T182107649Z-37300-4f085ddd/result.json` |
| T-01 failed-case rerun | `.\tools\check.ps1 -Mode Verify -TestPath 'tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv[ole-wrapper]' -SyntaxPath app/flow_worker.py,tests/test_flow_sql.py` | Final source/test fingerprint before commit; Windows; Python 3.13.15 | PASS; 1 passed in 0.73s; syntax passed; no skips/warnings | `.test-runs/20260910T182203213Z-20360-343052e2/result.json` |

## Pending in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-02/T-03 final-head | NOT RUN | Their last focused pass predates only the missing-import correction, which is specific to the extensionless T-01 branch; final-head CI is authoritative. | Confirm required CI on the PR's exact head. |
| T-LIVE-01 | NOT RUN | The browser-source fix is not yet merged and installed. | Merge after CI, update the BI desktop, and rerun the unchanged recording. |
| T-LIVE-02 | BLOCKED | Production SQL is intentionally gated on a passing T-LIVE-01. | Run exactly once after validation passes, then repeat SELECT-only aggregates. |

## Review and limitations

A bounded diff review confirmed the NASCA branch now normalizes the original
staged file before copying it and uses a same-directory hard link only when the
Playwright filename lacks an Excel suffix. The alias is removed after the
temporary workbook closes, including failure paths. Synthetic COM fixtures do
not emulate the corporate NASCA provider, so the explicitly requested live
retest remains decisive.

## Merge evidence

Pending. Record the final `Merge ready` run URL and exact PR head SHA in the PR
testing section before a head-pinned merge.
