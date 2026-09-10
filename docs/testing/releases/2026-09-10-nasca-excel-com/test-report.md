# NASCA Excel-COM Flow recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 13:20
- Tested code revision: working tree based on `1b0b9eab6decd7687a88590541ff7009642827ce`; final committed SHA pending.
- Environment: Windows 11; Python 3.12.14; pytest 9.1.1; synthetic Excel COM fixture plus explicitly requested live BI desktop.
- Overall finding: the user-confirmed NASCA container explains why stable staging still failed. Flow processing had no Excel-COM recovery and explicitly rejected an OLE payload with a modern Excel filename. The focused COM-to-normalized-CSV regression passes; final CI and post-deployment live SQL evidence are pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| NASCA-04 baseline | Confirmed deployed build `1b0b9eab`, reviewed the unchanged five-step MTracker_subs recording and ran **Test recording**. | Live BI desktop; Chrome Remote Desktop > Citrix > Windows work desktop; 2026-09-10 12:35-12:41 UTC. | **FAIL**: the staged download reached Flow processing, which rejected the NASCA OLE container as an incomplete XLSX ZIP. No flow changes were saved and SQL did not start. | Protected reference `LIVE-MTRACKER-20260910-C`. |
| NASCA-01, NASCA-02 | `$py -m pytest tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv tests/test_flow_sql.py::test_nasca_excel_recovery_requires_pywin32 tests/test_recording_optional_checks.py::test_recorded_asap_download_uses_scan_path_staging_completion tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q` | Windows 11; Python 3.12.14; synthetic Excel COM fixture. | **PASS**: 4 passed in 16.63s. The fake COM assertions verify read-only open, macro security, UTF-8 export, cleanup, preserved source bytes, missing-dependency recovery and SQL-ready normalized rows. | Local terminal output. |
| NASCA-01, NASCA-02 regression files | `$py -m pytest tests/test_flow_sql.py tests/test_recording_optional_checks.py -q` | Same working tree and environment. | **PASS**: 89 passed in 113.34s. | Local terminal output. |
| Syntax | `$py -m py_compile app/flow_worker.py` | Same working tree. | **PASS**. | Local terminal output. |
| Final-head CI attempt | GitHub Actions Python job | `fe2bf49c9130f4315e89d27c6cd252db949cbaf4`; Ubuntu, Python 3.13.15. | **FAIL**: 1934 passed, 20 skipped, 2 failed in 828.68s. Both failures were source-policy checks because temporary export cleanup directly called `Path.unlink`; no functional tests failed. | [Actions run 34480091308](https://github.com/datap0nd/data_governance/actions/runs/34480091308). |
| CI-policy correction | `$python -m pytest tests/test_flow_sql.py::test_nasca_encrypted_modern_excel_uses_desktop_excel_for_sql_csv tests/test_flow_sql.py::test_nasca_excel_recovery_requires_pywin32 tests/test_flows.py::test_worker_source_contains_no_delete_or_overwrite_operation tests/test_flows.py::test_retention_module_is_the_only_deletion_site_and_gates_every_path -q` | Windows 11; Python 3.12.14; final correction uses `TemporaryDirectory.cleanup()` for the owned scratch export. | **PASS**: 4 passed in 1.68s. | Local terminal output. |

## Pending checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| NASCA-03 | NOT RUN | Full final-head CI has not run yet. | Run the complete Python suite, record exact final SHA and Actions URL, and merge only after all required checks pass. |
| NASCA-04 retest, NASCA-05 | NOT RUN | The Excel-COM change has not merged or reached the BI desktop. | Confirm the exact deployed main SHA, test the unchanged recording, save it normally, then run production once and record protected evidence. |

## Notes

pywin32 previously appeared only in the scanner CSV reader and was not a Flow dependency. This change adds it to Windows installations only and uses a separate hidden Excel instance solely for NASCA-wrapped modern workbook normalization. Linux CI continues through the fake COM seam and ordinary non-COM readers.
