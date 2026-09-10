# Recorded ASAP staging completion: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 11:50
- Tested code revision: uncommitted implementation tree based on `88fbd70f37e1820aeb020b33801f5acdb89da830`; final committed SHA pending.
- Environment: Windows 11; Python 3.12.14; pytest 9.1.1; Playwright 1.62.0; synthetic Chrome plus explicitly requested live BI Desktop.
- Overall finding: the focused synthetic regression passes. Live `88fbd70` proved that content scanning alone did not fix MTracker because the recorder still selected the browser-managed GUID path instead of the stable staging file. Final-head CI and post-merge live retest are pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-03 baseline | Refreshed Edge after PR #101 auto-update, confirmed `#20260910-153204-88fbd70f3`, opened the unchanged five-step MTracker_subs recording and ran **Test recording**. | Deployed `88fbd70`; Chrome Remote Desktop > Citrix > Windows work desktop; tester Codex; 2026-09-10 11:34-11:41 UTC. | **FAIL**: the portal download completed, but recorded output validation again rejected the browser-managed GUID path as an incomplete XLSX ZIP container. No flow changes were saved and SQL did not start. | Protected reference `LIVE-MTRACKER-20260910-B`; debug test 154. |
| T-01, T-02 | `$py -m pytest tests/test_recording_optional_checks.py::test_recorded_asap_download_uses_scan_path_staging_completion tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q` | Uncommitted implementation tree based on `88fbd70`; Windows 11 synthetic Chrome. | **PASS**: 2 passed in 14.47s. | Local terminal output. |
| Syntax and whitespace | `$py -m py_compile app/flow_recording_runtime.py`; `git diff --check` | Same implementation tree and environment. | **PASS**; line-ending conversion notices only. | Local terminal output. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-03 retest, T-04 | NOT RUN | The staging-completion change has not yet passed final CI, merged, or reached BI Desktop. | Merge only after final CI, wait for the exact desktop SHA, then retest and run production once. |
| T-05 | NOT RUN | Final-head CI has not run yet. | Record the final Actions URL, SHA and result in the PR before merge. |

## Findings, limitations and retests

The failed live attempt is retained because it falsified the prior long-preamble-only diagnosis. The existing scan-selected ASAP dashboard implementation documents that a native download event is only a start signal and that completion must come from the stable configured staging file. Recorded ASAP instead called `_completed_edge_download`, then validated its GUID-named browser path. The change routes only recorded ASAP downloads to the established staging helper; downstream format detection, normalization and SQL logic remain shared and unchanged. Synthetic tests cannot prove live portal or SQL success.

## Merge evidence

Pending. Before merge, the PR testing section must record final CI on the exact head SHA. Post-merge live evidence must append a dated row without replacing the failed `88fbd70` attempt.
