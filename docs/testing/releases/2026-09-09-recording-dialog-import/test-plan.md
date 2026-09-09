# Recording dialog import: test plan

- Scope: finish a first recording containing Playwright's generated `page.once("dialog", lambda dialog: dialog.dismiss())` without losing its download.
- Implementation revision: `f3613c0babaffa2a77c8b42101402309928dbe71`, based on `33619375` from main.
- Report: [test-report.md](test-report.md).
- Environments: Windows/Python 3.13, Playwright 1.62.0; CI Windows and Ubuntu; authenticated work-PC ASAP for live checks.

## Prerequisites and data

Install the test dependencies and browsers from `.github/workflows/tests.yml`.
Use only the local fictional report fixtures for automated checks. For live
checks, update both the app and recorder worker to the merged main revision,
restart them after existing work finishes, and authenticate to ASAP normally.
Create a disposable, unscheduled Flow with an isolated output directory.
Record app/worker SHA, UTC time, browser version and session ID. Keep portal
URLs, credentials, raw recordings and downloaded reports in protected storage.

## Cases

| ID | Actions | Expected result | Evidence |
| --- | --- | --- | --- |
| DI-01 | Import fictional codegen with the exact dialog-dismiss line before navigation, Generate, the download event or its nested Download click. | Import succeeds; all original actions and the correlated download survive, ignoring generated step-ID shifts. | Parser test results. |
| DI-02 | Import other event names, `on`, unknown/locator/context receivers, `accept`, callback names, extra arguments or executable callback bodies/defaults. | Import rejects them without evaluating callbacks. Correcting the input to standard codegen restores successful import. | Parameterized negative test results. |
| DI-03 | Run the mocked recorder Finish path with the canonical handler and completed download; also exercise missing download, cancellation and crash. | Finish returns the download definition, closes its owned process and cleans temporary authentication; other paths preserve their existing outcomes. | Recorder lifecycle results. |
| DI-04 | Replay the synthetic iframe/popup report in Chrome, clicking Download twice; each link displays an alert before exporting CSV. | Alerts do not stall playback; two distinct downloads are correlated, validated and published. | Actual browser test result tied to code revision; fictional data only. |
| DI-05 | On the work PC, start a first ASAP recording, repeat the reported report download, handle any portal dialog normally, wait for the file, then click **Finish recording**. | Recording reaches review without `Unsupported action 'once'`; recorded actions and download are present. | Protected screenshot/reference plus session ID and revisions. |
| DI-06 | Review that draft, use the existing test control, and compare the downloaded report identity, headers and period with the intended report. | Replay completes and output matches; no schedule is enabled during testing. | Protected output comparison and run ID. |
| DI-07 | Cancel a separate disposable live recording and retry recording from the existing controls. | Cancellation releases the worker; retry can record and finish. | Session IDs and protected evidence. |

## Automated commands

```powershell
python -m pytest tests/test_flow_recordings.py tests/test_recording_controls.py tests/test_recorded_browser_pipeline.py -q
python -m pytest tests -q
git diff --check
```

On this Windows host, pytest's temporary-directory symlink creation requires
a local harness workaround. The report records the actual command; CI uses
the unmodified suite. No frontend files, controls or journeys change, so a new
preview/owner approval is not applicable. Existing browser lifecycle tests
cover the unchanged Finish/Cancel controls.

## Acceptance and cleanup

Automated suites and final-head CI must pass before merge. DI-05 through DI-07
remain separate live validation; synthetic success does not prove ASAP success.
This fix recognizes only the canonical generated dismiss callback. Custom
dialog callbacks remain unsupported; Playwright's default dialog handling is
unchanged. Remove only the disposable Flow and its designated test output,
leave schedules disabled, and retain protected evidence by opaque identifier.
For rollback, revert the merged PR through the normal tested delivery process;
recordings containing the generated handler will again fail import.
