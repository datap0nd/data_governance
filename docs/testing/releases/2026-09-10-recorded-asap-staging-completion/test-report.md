# Recorded ASAP staging completion: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 12:10
- Tested code revision: uncommitted implementation and fixture tree based on `caf2164274e60356d3387eec5fe1b437a6e3f9a6`; final committed SHA pending.
- Environment: Windows 11; Python 3.12.14; pytest 9.1.1; Playwright 1.62.0; synthetic Chrome plus explicitly requested live BI Desktop.
- Overall finding: the focused synthetic regression passes. Live `88fbd70` proved that content scanning alone did not fix MTracker because the recorder still selected the browser-managed GUID path instead of the stable staging file. Final-head CI and post-merge live retest are pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-03 baseline | Refreshed Edge after PR #101 auto-update, confirmed `#20260910-153204-88fbd70f3`, opened the unchanged five-step MTracker_subs recording and ran **Test recording**. | Deployed `88fbd70`; Chrome Remote Desktop > Citrix > Windows work desktop; tester Codex; 2026-09-10 11:34-11:41 UTC. | **FAIL**: the portal download completed, but recorded output validation again rejected the browser-managed GUID path as an incomplete XLSX ZIP container. No flow changes were saved and SQL did not start. | Protected reference `LIVE-MTRACKER-20260910-B`; debug test 154. |
| T-03 post-merge retest | Refreshed Edge after PR #102 auto-update, confirmed build `1b0b9eab`, opened the unchanged five-step MTracker_subs recording and ran **Test recording**. | Deployed `1b0b9eab`; Chrome Remote Desktop > Citrix > Windows work desktop; tester Codex; 2026-09-10 12:35-12:41 UTC. | **FAIL**: stable staging was used, but Flow processing classified the NASCA-wrapped `.xlsx` as an incomplete modern ZIP. The preserved file has an Excel document icon and an OLE container signature. No flow changes were saved and SQL did not start. | Protected reference `LIVE-MTRACKER-20260910-C`. Follow-up coverage: `2026-09-10-nasca-excel-com`. |
| T-01, T-02 and recording journey | `$py -m pytest tests/test_recording_optional_checks.py::test_recorded_asap_download_uses_scan_path_staging_completion tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql tests/test_recording_journey.py::test_browser_real_api_save_test_return_apply -q` | Uncommitted fixture follow-up based on `caf2164`; Windows 11 synthetic Chrome. | **PASS**: 3 passed, 2 warnings in 70.15s. | Local terminal output. |
| Final-head CI attempt 1 | Full Python suite. | GitHub Actions Ubuntu/Python 3.13 on `caf2164274e60356d3387eec5fe1b437a6e3f9a6`. | **FAIL**: 1 failed, 1,934 passed, 20 skipped, 12 warnings in 925.65s. The real-API journey fixture did not configure the production downloads path; implementation regressions passed. | [Actions run 34473393535](https://github.com/datap0nd/data_governance/actions/runs/34473393535) |
| Syntax and whitespace | `$py -m py_compile app/flow_recording_runtime.py`; `git diff --check` | Same implementation tree and environment. | **PASS**; line-ending conversion notices only. | Local terminal output. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-03 retest, T-04 | NOT RUN | The staging-completion change has not yet passed final CI, merged, or reached BI Desktop. | Merge only after final CI, wait for the exact desktop SHA, then retest and run production once. |
| T-05 | NOT RUN | Final-head CI has not run yet. | Record the final Actions URL, SHA and result in the PR before merge. |

## Findings, limitations and retests

The failed live attempt is retained because it falsified the prior long-preamble-only diagnosis. The existing scan-selected ASAP dashboard implementation documents that a native download event is only a start signal and that completion must come from the stable configured staging file. Recorded ASAP instead called `_completed_edge_download`, then validated its GUID-named browser path. The change routes only recorded ASAP downloads to the established staging helper; downstream format detection, normalization and SQL logic remain shared and unchanged. CI attempt 1 exposed a synthetic journey browser that omitted the downloads directory production validation has always supplied; its fixture now mirrors that production launch contract. Synthetic tests cannot prove live portal or SQL success.

## Merge evidence

Pending. Before merge, the PR testing section must record final CI on the exact head SHA. Post-merge live evidence must append a dated row without replacing the failed `88fbd70` attempt.
