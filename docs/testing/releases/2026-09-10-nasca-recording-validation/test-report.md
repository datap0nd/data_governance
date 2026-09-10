# NASCA recording validation parity: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 16:28
- Tested code revision: uncommitted hotfix based on `2288922968d17915f5b85fd5b1f540ac1b7fa137`.
- Environment: Windows 11; checkout-owned Python 3.13.15; pytest 9.1.1; Playwright synthetic Chrome fixture; explicitly requested signed-in BI desktop.
- Overall finding: live build `228892296` contains the NASCA Excel-COM recovery but **Test recording** still failed before it because validation suppresses SQL and thereby lost the production table-normalization requirement. The hotfix preserves that requirement while keeping SQL disabled. The exact synthetic NASCA replay and the validation side-effect boundary pass locally; final-head CI and the merged-build live retest are pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Live baseline RV-04 | Updated Metronome, confirmed header `228892296`, reviewed the unchanged five-step `MTracker_subs` recording, and selected **Test recording** with no concurrent manual download. | Merged baseline `2288922968d17915f5b85fd5b1f540ac1b7fa137`; signed-in BI desktop; Chrome; 2026-09-10 16:09–16:12 UTC. | **FAIL** after the 57.7 MB UUID-staged download completed: validation reported an incomplete XLSX ZIP. Debug test 157 showed failure during downloaded-output validation; SQL did not start and no editor changes were saved. | Protected reference `LIVE-MTRACKER-20260910-E`. |
| Existing SQL target baseline | In pgAdmin, ran aggregate SELECT-only checks on `bi_staging.asap_mtracker_country`. | Signed-in BI desktop; 2026-09-10 16:00–16:07 UTC. | 145,990 rows; 14 expected business columns; year range 2024–2026; 15 countries; zero rows missing country, model or qty. This confirms the pre-hotfix target state only, not the new load. | Protected reference `LIVE-MTRACKER-DB-20260910-A`; no database edits. |
| Setup | `pwsh -NoProfile -File tools/check.ps1 -Mode Setup` | Baseline `228892296`; Windows 11. | **PASS**; Python 3.13.15 checkout environment and locked dependencies ready. | Local result `20260910T162510988Z-5376-e11d9b9b`. |
| RV-01, RV-02 initial focused run | `tools/check.ps1 -Mode Verify` with both named regression nodes and syntax paths for the four changed Python files. | Uncommitted hotfix; Windows 11/Python 3.13.15. | RV-02 **PASS**. RV-01 fixture **FAIL** because two isolated test databases reused the same external managed-flow folder; product execution was not reached. Syntax passed. | Local result `20260910T162540548Z-16600-2288e5f6`. |
| RV-01 fixture retest 1 | Reran only `test_validation_retains_sql_input_checks_without_executing_sql` after replacing its unnecessary managed Flow fixture with a minimal job. | Same hotfix. | **FAIL**: the minimal fixture omitted `configuration_hash`; product assertions had passed before result construction. | Local result `20260910T162647704Z-34924-b3fe54a6`. |
| RV-01 fixture retest 2 | Reran only `test_validation_retains_sql_input_checks_without_executing_sql` with the required frozen configuration hash and the four syntax paths. | Same hotfix. | **PASS**: 1 passed in 0.60 s; 2 dependency deprecation warnings; syntax passed. | Local result `20260910T162735273Z-10556-3db28872`. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| RV-03 | NOT RUN | Final-head CI requires the committed PR head. | Open the PR, wait for `Merge ready`, and record its Actions URL and exact head in the PR before merge. |
| RV-04 retest, RV-05 | NOT RUN | The hotfix has not merged or reached the BI desktop. | Update to the exact merged build, rerun the unchanged recording, then run production once only after validation passes. |

## Findings, limitations and retests

The live debug log proved download acquisition completed and failure occurred three seconds later during validation. Code review then found that `flow_recorder_worker.validate` sets `sql_handoff.enabled` false for safety before `flow_recording_runtime.acquire` decides whether the download requires tabular normalization. The safe test therefore took the unchecked raw-workbook branch and invoked ZIP validation before Excel COM. The hotfix carries a validation-only requirement flag across that boundary; SQL remains disabled. Synthetic COM verifies routing and artifacts, not the proprietary NASCA client itself. The two local failures were test-fixture construction defects and remain recorded above.

## Merge evidence

Pending. The PR testing section must record the final `Merge ready` run URL and exact head SHA before the head-pinned merge.
