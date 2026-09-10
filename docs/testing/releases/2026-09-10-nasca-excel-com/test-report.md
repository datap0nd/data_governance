# NASCA Excel-COM Flow recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: [#104](https://github.com/datap0nd/data_governance/pull/104); opaque-wrapper follow-up [#106](https://github.com/datap0nd/data_governance/pull/106).
- Evidence cutoff (UTC): 2026-09-10 15:18
- Tested implementation revision: `9deaf9a9518c4d8400534f372d8bf2f147e50f4f`, based on merged main `3acee314e8f1eb75d838349db14e6b4915914c68`; final report-only head pending.
- Environment: Windows 11; checkout-owned Python 3.13.15 `.venv`; pytest 9.1.1; synthetic Excel COM fixture plus explicitly requested live BI desktop.
- Overall finding: #104 added Excel-COM recovery for the expected OLE wrapper, but live build `3acee314` showed that this NASCA payload is an opaque non-ZIP wrapper detected from its `.xlsx` suffix instead. The follow-up routes that shape through COM only when a recorded/scan-selected Excel contract requires table processing. Focused recovery and ordinary corrupt-file regression checks pass; final follow-up CI and live SQL evidence are pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| NASCA-04 baseline | Confirmed deployed build `1b0b9eab`, reviewed the unchanged five-step MTracker_subs recording and ran **Test recording**. | Live BI desktop; Chrome Remote Desktop > Citrix > Windows work desktop; 2026-09-10 12:35-12:41 UTC. | **FAIL**: the staged download reached Flow processing, which rejected the NASCA OLE container as an incomplete XLSX ZIP. No flow changes were saved and SQL did not start. | Protected reference `LIVE-MTRACKER-20260910-C`. |
| NASCA-01, NASCA-02 | `$py -m pytest tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv tests/test_flow_sql.py::test_nasca_excel_recovery_requires_pywin32 tests/test_recording_optional_checks.py::test_recorded_asap_download_uses_scan_path_staging_completion tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q` | Windows 11; Python 3.12.14; synthetic Excel COM fixture. | **PASS**: 4 passed in 16.63s. The fake COM assertions verify read-only open, macro security, UTF-8 export, cleanup, preserved source bytes, missing-dependency recovery and SQL-ready normalized rows. | Local terminal output. |
| NASCA-01, NASCA-02 regression files | `$py -m pytest tests/test_flow_sql.py tests/test_recording_optional_checks.py -q` | Same working tree and environment. | **PASS**: 89 passed in 113.34s. | Local terminal output. |
| Syntax | `$py -m py_compile app/flow_worker.py` | Same working tree. | **PASS**. | Local terminal output. |
| Final-head CI attempt | GitHub Actions Python job | `fe2bf49c9130f4315e89d27c6cd252db949cbaf4`; Ubuntu, Python 3.13.15. | **FAIL**: 1934 passed, 20 skipped, 2 failed in 828.68s. Both failures were source-policy checks because temporary export cleanup directly called `Path.unlink`; no functional tests failed. | [Actions run 34480091308](https://github.com/datap0nd/data_governance/actions/runs/34480091308). |
| CI-policy correction | `$python -m pytest tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv tests/test_flow_sql.py::test_nasca_excel_recovery_requires_pywin32 tests/test_flows.py::test_worker_source_contains_no_delete_or_overwrite_operation tests/test_flows.py::test_retention_module_is_the_only_deletion_site_and_gates_every_path -q` | Windows 11; Python 3.12.14; final correction uses `TemporaryDirectory.cleanup()` for the owned scratch export. | **PASS**: 4 passed in 1.68s. | Local terminal output. |
| Supported final local verification after rebase | `tools/check.ps1 -Mode Verify` with the two NASCA nodes, the two failed retention-policy nodes, and `-SyntaxPath app/flow_worker.py` | `2231d843ba49d3e3e9ade29771fa359eb2050fc3`; Windows 11; checkout-owned Python 3.13.15 environment. | **PASS**: syntax passed; 4 tests passed in 1.17s, 0 skipped; total 3.398s. | Local run `20260910T142732293Z-25292-a490ffbb`. |
| #104 required final CI | GitHub Actions full Python suite and `Merge ready` gate | `9e575231100db44d2e5f477669344d11a6b50690`; Ubuntu, Python 3.13.15. | **PASS**: 1943 passed, 23 skipped, 13 warnings in 831.54s; `Merge ready` passed. | [Actions run 34489351115](https://github.com/datap0nd/data_governance/actions/runs/34489351115). |
| NASCA-04 opaque-wrapper live baseline | On deployed build `3acee314`, reviewed the unchanged five-step MTracker_subs recording and ran **Test recording**. | Live BI desktop; 2026-09-10 14:53-15:05 UTC. | **FAIL** after the 57.7 MB download completed: suffix-based detection still sent the opaque non-ZIP payload to the XLSX ZIP validator. No recording changes were saved and SQL did not start. A separate manual download was also active, so the follow-up retest must run without concurrent manual downloads. | Protected reference `LIVE-MTRACKER-20260910-D`. |
| Opaque-wrapper correction | `tools/check.ps1 -Mode Verify` with the parameterized NASCA COM node, missing-pywin32 node, unrelated incomplete-XLSX node, and `-SyntaxPath app/flow_worker.py` | Follow-up working tree based on `3acee314`; Windows 11; checkout-owned Python 3.13.15 environment. | **PASS**: syntax passed; 4 tests passed in 2.03s, 0 skipped. Both OLE and opaque non-ZIP recorded wrappers route through fake COM; unrelated incomplete XLSX retains its container failure. | Local run `20260910T151654207Z-21556-f1f133b8`. |

## Pending checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| NASCA-03 follow-up | NOT RUN | Full final-head CI has not run for the opaque-wrapper correction. | Record the exact head and Actions URL and merge only after `Merge ready` passes. |
| NASCA-04 retest, NASCA-05 | NOT RUN | The opaque-wrapper correction has not merged or reached the BI desktop. | Confirm the exact deployed main SHA, test the unchanged recording with no concurrent manual download, save it normally, then run production once and record protected evidence. |

## Notes

pywin32 previously appeared only in the scanner CSV reader and was not a Flow dependency. This change adds it to Windows installations only and uses a separate hidden Excel instance solely for NASCA-wrapped modern workbook normalization. Linux CI continues through the fake COM seam and ordinary non-COM readers.
