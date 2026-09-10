# Recorded ASAP staging completion: test plan

- Change/PR: follow-up to #101; make recorded ASAP navigation finish downloads through the same stable staging-file contract as scan-selected ASAP dashboard flows.
- Code baseline: `88fbd70f37e1820aeb020b33801f5acdb89da830`; live retest must verify the later merged SHA displayed by BI Desktop.
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11 synthetic Chrome fixture and the explicitly requested Chrome Remote Desktop > Citrix > Windows work desktop > Edge/Chrome live path.

## Prerequisites and test data

- Keep the existing MTracker_subs recording unchanged; do not re-record selectors or alter its download/SQL settings.
- Use fake synthetic workbook rows only in repository tests.
- For the live case, confirm Metronome displays the merged build SHA before starting and keep portal URLs, filenames, workbook contents, credentials and screenshots in protected storage.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Run `test_recorded_asap_download_uses_scan_path_staging_completion`. Leave the recorded output completion unspecified, make the browser-managed path unusable, and provide a valid workbook through the configured staging folder. | Recorded ASAP calls the established staging completion helper, never the native GUID-path helper, and publishes the byte-identical valid workbook. | Pytest output. |
| T-02 | Run `test_excel_named_text_download_reuses_shared_normalization_for_sql` with a long UTF-16 preamble and an `.xlsx` download name. | Stable staging output is content-detected, ASAP-normalized and handed to SQL as a two-row CSV. | Pytest output. |
| T-03 | After merge and desktop auto-update, refresh Edge, confirm the exact new SHA, open the unchanged MTracker_subs recording, choose **Test recording**, and wait for all five steps plus downloaded-output validation. | No incomplete-XLSX error; the recording test succeeds without re-recording. | Protected live reference with timestamp, revision and test ID. |
| T-04 | After T-03 passes, return to **Edit Flow**, save the validated unchanged recording, then run MTracker_subs once from **Flows** and monitor **Run history** through SQL insertion. | Production run succeeds, reports the SQL commit/rows, and leaves no duplicate run. | Sanitized run ID/status and protected evidence reference. |
| T-05 | Exercise a non-ASAP recorded download through final-head CI. | GSCM/generic recordings retain native browser completion; the ASAP compatibility rule does not change them. | Final CI run. |

## Automated checks

Use the bundled Python runtime after installing `requirements-ci.txt`:

```powershell
$py = 'C:\Users\keeoh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py -m pytest tests/test_recording_optional_checks.py::test_recorded_asap_download_uses_scan_path_staging_completion tests/test_recording_optional_checks.py::test_excel_named_text_download_reuses_shared_normalization_for_sql -q
```

Run `python -m py_compile app/flow_recording_runtime.py` and `git diff --check`. Final-head GitHub CI supplies the full Python regression and frontend/scope gates.

## Acceptance and cleanup

Accept only if final-head CI passes, the deployed live recording test succeeds on the displayed merge SHA, and one production run reaches committed SQL insertion. Preserve the existing recording and normal retained run folders. If the staging rule causes a regression, revert the follow-up merge; do not revert the previously working scan-selected processing or alter the flow recording to compensate.
