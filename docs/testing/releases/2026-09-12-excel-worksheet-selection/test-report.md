# Excel worksheet selection: test report

- [Plan](test-plan.md); [browser evidence](browser-evidence.md); [exact local verification records](local-checks.json).
- Evidence cutoff: 2026-09-12 17:00 UTC. Later results belong in a dated addition and in the PR testing section.
- Baseline: `c3f0869adf03e4ead1d497af22a20a1a8115bedd`, with the uncommitted changes in this branch. Per-invocation source fingerprints distinguish the working versions tested; these results do not claim that the final PR head has passed CI.
- Environment: Windows 10.0.26200, PowerShell 7.6.5, checkout-owned Python 3.13.15, Playwright 1.62.0, synthetic Chromium downloads and synthetic COM worksheet objects.
- Initial delivery state at 17:00 UTC: backend implemented; production UI pending owner preview feedback. See the dated addition below for implementation and UI results.

## Executed checks

| Evidence | Outcome |
| --- | --- |
| `20260912T162625333Z-4872-c25aa436` | **80 passed**, zero failures/errors/skips, 41.66s pytest time. Initial worksheet rules, formats, API persistence, original retention, NASCA selection/cleanup, and local-file regression coverage; applicable syntax checks passed. |
| `20260912T163659862Z-14488-31745db7` | **19 passed, 10 failed**. The first synthetic recorded default-guard case passed. Remaining database-backed cases failed before execution because the Windows verifier shared the same `Portable sales (id 1)` folder between separate fixture databases. Retests below use separate invocations. |
| `20260912T164137974Z-33904-2dfc6a19`, `20260912T164137986Z-24840-c0f3b5b2` | **1 passed each**. Synthetic recorded downloads delivered exactly the appended named rows and the single named sheet to the captured SQL loader. |
| `20260912T164448693Z-28500-e3fb87ac`, `20260912T164448679Z-25448-9b1994dc` | **1 passed each**. Missing selected sheet and incompatible selected columns fail before any SQL call. |
| `20260912T164448694Z-10536-aee2730c`, `20260912T164448699Z-11412-5e46c771`, `20260912T164719158Z-36392-3ddd7759` | **1 passed each**. Existing recorded text-as-Excel normalization and both synthetic NASCA inputs still reach the intended normalized SQL input. |
| `20260912T164719158Z-35580-19d3628f`, `20260912T164719166Z-36696-5806b687` | **1 failed each**. Concurrent portable-script tests collided on host-wide Flow execution locks. These are test orchestration failures, not worksheet mismatches. |
| `20260912T164719158Z-27860-b1e28972` | **1 failed**. Portable CSV execution reached run-folder creation and hit the Windows path-length limitation under the verifier's long temporary root. No worksheet implementation conclusion is drawn from this failure. |
| `20260912T165441326Z-40396-195f0731` | **3 passed, 2 failed**. Structured error evidence, zero-data failure and worker capability gating passed. Two new generic-portal fixtures omitted their required output directory. |
| `20260912T165653140Z-39964-502237a4` | **2 passed** after correcting that fixture setup. Generic portal downloads enforce the default guard and preserve the chosen append order. Syntax check passed. |

Exact commands, timestamps, selections, fingerprints and JUnit totals are in `local-checks.json`. Counts above are per invocation; repeated checks are not presented as unique coverage. No skips occurred in the listed completed checks. The retained failures are not relabeled as passes.

## Checks pending at the initial cutoff

| Check | State | Next action |
| --- | --- | --- |
| Owner preview feedback and production Flow UI | Pending | Review the clickable preview, then implement and verify the actual settings and run-error recovery controls. |
| Portable-script regression on this Windows verifier | Environment-limited | A short temporary path and sequential execution are needed. Required CI runs these cases in its supported isolated environment; record its result. |
| Full Python regression / final-head Merge ready | Not run at cutoff | Record the CI URL and exact tested SHA in the PR before a head-pinned merge. |

## Findings and scope

The existing worker already contained a `different columns` error for multi-sheet Excel normalization. This change makes worksheet selection explicit and records actionable file/sheet errors before SQL. It does not establish how many historical production runs contained that error; no production history count was obtained in this implementation pass.

The tests preserve all selected rows, including small sheets, duplicate rows and `Total` headers. Append still requires the same normalized column names in the same order. No heuristics were added to omit summary sheets or small data sets. SQL loading code and browser context lifecycle are unchanged; worksheet configuration failures stop acquisition retries.

Backend review covered selection validation, exact names/order, raw-workbook fallback, source retention, capability compatibility, local-file receipt identity, and error propagation. The generic portal path was found to bypass the shared Excel reader and was connected to it; its two focused cases passed after correcting test setup.

## 2026-09-12 17:31 UTC — implemented UI and regression follow-up

The owner approved the demonstrated journey with “implement and merge to main.” The actual portal, Outlook and local-file builders now expose the worksheet checkbox and its two choices under **After download**. Run logs identify Excel processing failures and link back to that Flow with detected names. SQL-only retry is hidden for these failures. Existing SQL modes are preserved.

These checks ran against `4c9c57dec767afd7fdf3b4ca7e5462d343dd8ab4` plus the uncommitted UI/test changes, distinguished by fingerprints in `local-checks.json`:

| Evidence | Actual result |
| --- | --- |
| `20260912T172438765Z-12380-ed17a46b` | **1 passed, 2 failed, 1 error.** The Node payload/error contract passed. The UI harness launched Chromium before pytest initialized its temporary directory, causing a Windows file lock; two builder cases tried Edit before expanding the existing collapsed group. Neither failure was a worksheet-processing error. |
| `20260912T172707725Z-12068-579711f1` | **3 passed**, zero skips, 8.41s pytest time, after fixing that fixture setup and walking the group control. Actual error-to-settings navigation, both save/reopen modes, exact spaces, missing/duplicate/count validation, interrupted and rejected saves, Cancel without persistence, disabling selection, portal/file/Outlook forms and 390px layout passed. JS/Python syntax checks passed. Browser: Chrome 152.0.7977.83, Playwright 1.62.0. |
| [Initial CI run](https://github.com/datap0nd/data_governance/actions/runs/34707186679) at `4c9c57dec767afd7fdf3b4ca7e5462d343dd8ab4` | **2066 passed, 2 failed, 22 skipped, 14 warnings**, 1134.67s. Both failures were in `test_dashboard_xlsx_normalizes_only_for_configured_downstream_processing`: the prior assertion expected one progress event, but worksheet inventory and row-count events now produce three. The other cases, including the previously environment-limited portable checks, passed. This run's Merge ready check failed. |
| `20260912T172857061Z-40676-4dabd419` | **2 passed**, zero skips, 1.54s pytest time, plus syntax. The CI-failing assertions now require that the original workbook exists at **every** normalization event. Output/row count and stage-order assertions remain. |

The bounded UI review covered checkbox-off serialization, exact name/order preservation, all three source builders, old local-file selections, failed-save focus and preserved edits, run/Flow identity checks, responsive layout and prevention of SQL retry after worksheet failure. Actual fictional browser screenshots and observations are in [browser evidence](browser-evidence.md). No new implementation issue remained after the focused retests.

Final-head CI remains pending at this addition's cutoff. Its exact tested SHA, run URL and result must be recorded in the PR before a head-pinned merge. No later CI outcome, main merge or deployment is implied by this committed report.

## 2026-09-12 17:38 UTC — recording-save contract fixture

The frontend job in [CI run 34708639569](https://github.com/datap0nd/data_governance/actions/runs/34708639569) on `38b9e8ba06f8a724c83c4b6ff8e898d2daa0163b` failed in `test_flow_save_without_test.mjs`. That isolated Node fixture extracted the submit handler without its newly called worksheet validator. The fixture now loads the actual validator with worksheet selection off; application behavior is unchanged. The Python job had not finished at this cutoff, so no result is claimed for it.

`tools/check.ps1 -Mode Verify -TestPath tests/test_flow_excel_ui.py::test_flow_save_frontend_contract -SyntaxPath tests/test_flow_save_without_test.mjs,tests/test_flow_excel_ui.py` passed **1 test**, zero skips, 0.34s pytest time; syntax passed. Evidence: `20260912T173800349Z-36332-31489953` in `local-checks.json`, against head `38b9e8ba06f8a724c83c4b6ff8e898d2daa0163b` plus the fixture correction. Final-head CI remains pending and must be recorded in the PR before merge.
