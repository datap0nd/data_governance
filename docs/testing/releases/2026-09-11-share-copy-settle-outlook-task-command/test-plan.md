# Share copy settle wait and Outlook task command: test plan

- Change/PR: two Flow delivery fixes reported from the BI desktop.
  1. **Network-share copies no longer fail on a lagging read-back.** Many
     flows ended with `Downloaded Excel workbook was not copied completely to
     \\<share>\...: read N of the expected M bytes back` although the file on
     the share was complete. The worker now flushes every target copy to the
     server (`os.fsync`) before the copy stream closes, treats that flushed
     stream as the proof of delivery, and only *waits* (up to 60 s) for the
     target folder to serve the copied bytes. A share whose view does not
     catch up records a `copy_verification_warning` event on the run and the
     run proceeds. A target that cannot be opened at all still fails. Excel
     normalization now reads the settled local staging download instead of
     the share copy (openpyxl receives it as an open handle because Playwright
     stages downloads without an extension), so a stale share view can never
     feed a truncated workbook to the reader.
  2. **Outlook hand-off task command under the schtasks limit.** The "Email
     the final file" step (and every other server-side Outlook hand-off)
     failed with `The value for /TR option cannot be more than 261
     character(s)` because the task command carried the helper script path,
     the payload path and the receipt path. The dispatch now writes a short
     per-dispatch launcher (`outlook-task-email-<stamp>.launch.ps1`) beside
     its payload and the task command references only that launcher; an
     over-long launcher path fails the dispatch with a clear message before
     schtasks runs, and the launcher is removed with the payload and receipt.
  Files: `app/flow_worker.py`, `app/routers/email.py`, `tests/test_flow_sql.py`,
  `tests/test_outlook_dispatch.py`, a race-free assertion in
  `tests/test_recording_controls.py` (CI retest), this package and the
  testing index.
  PR link: recorded in the test report's Merge evidence section.
- Code baseline: `fa2639a` (main after PR #114).
- Related report: [test-report.md](test-report.md)
- Intended environments: Metronome app and Flow workers on the BI desktop
  (Windows, target folders on an SMB share, Outlook signed in); the automated
  checks run anywhere with Python 3.11+ and the locked test dependencies. Live
  runs on the BI desktop are owner checks and are not performed by the
  automated evidence.

## Prerequisites and test data

- Update the app **and the Flow workers** from the merged `main` revision:
  fix 1 lives in the worker (`app/flow_worker.py`), fix 2 in the API process
  (`app/routers/email.py`). No migration is involved.
- Live data: one Flow whose target folder is on the network share that
  produced the `not copied completely` failures (for example an ASAP
  MTracker flow), and one Flow with the Email step enabled and your own
  mailbox as recipient.
- Synthetic tests use temporary files; a lagging share is simulated by a
  path wrapper that serves short reads first, and schtasks is captured by a
  fake `subprocess.run`.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| SC-01 | Run a Flow whose target folder is on the share that previously failed. | The run succeeds; the file in the target folder opens and matches the download. Run events contain no `copy_verification_warning` when the share served the file promptly. | Run ID and revision; automated `test_copy_verification_waits_for_a_share_that_serves_a_short_view` |
| SC-02 | Same Flow while the share view lags for longer than 60 s (cannot be forced; observe when it happens). | The run still succeeds. The run log shows one **Copy verification warning** event: `<label> was written and flushed to <path> (M bytes), but reading it back through the target folder still served N of the M copied bytes after ~60 s ... treated as delivered`. The file on the share is complete once the share refreshes. | Run log event; automated `test_copy_verification_records_a_warning_instead_of_failing_a_delivered_copy`, `test_copy_verification_names_same_size_different_bytes_in_its_warning` |
| SC-03 | Target file that cannot be read back at all (for example removed by an on-access scanner). | The run fails with `<label> could not be read back from <path> after k attempt(s) over t s: <OS error>`. | automated `test_copy_verification_still_fails_a_target_that_cannot_be_read` |
| SC-04 | Copy stream contract. | Every target copy is flushed with `os.fsync` while the handle is open and after the full byte count was written; a write or flush error fails the copy. | automated `test_copy_stream_flushes_the_target_before_closing` |
| SC-05 | Excel Flow (xlsx/xlsm, legacy HTML-as-XLS) with SQL handoff or a transformation. | The normalized CSV is produced from the settled local download; the delivered workbook on the share is byte-identical; error messages still name the delivered file (`... could not be opened: <delivered name>`). Raw-only and NASCA flows are unchanged. | automated `test_excel_normalization_reads_the_settled_local_download_not_the_share_copy`, `tests/test_flow_excel_formats.py`, `tests/test_flow_sql.py` |
| SC-06 | Configured local source file Flow and a resumed run carrying artifacts. | Same settle behaviour: the private-store copy is verified by read-back, a lagging view records the warning on the run, delivery is not failed. | automated `tests/test_flow_local_file.py`, `tests/test_recorded_output_storage.py` |
| OT-01 | Flow with **Email the final file** enabled; run it to success on the BI desktop. | Run history reads `Email: handed to Outlook, waiting for its receipt` and then `Email submitted by Outlook`; no `/TR option cannot be more than 261 character(s)` error; the recipient receives the file. `%ProgramData%\DataGovernance` contains `outlook-task-email-<stamp>.launch.ps1` next to the payload until the receipt is reconciled. | Run ID and revision; automated `test_task_command_stays_within_the_schtasks_limit_via_a_launcher` |
| OT-02 | Task command shape. | `schtasks /create ... /tr` receives exactly `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<launcher>"` (well under 261 characters even with a long checkout path); the launcher calls the helper with `-PayloadPath`, `-ReceiptPath` and `-Send` (omitted for drafts) using PowerShell single-quoted literals, then `exit $LASTEXITCODE`. | automated `test_task_command_stays_within_the_schtasks_limit_via_a_launcher` |
| OT-03 | Receipt reconciliation. | After the receipt is consumed, the launcher, payload and receipt are deleted with the scheduled task. | automated `test_launcher_is_removed_with_the_payload_and_receipt`, `test_simultaneous_outlook_handoffs_use_unique_tasks_and_independent_receipts` |
| OT-04 | Launcher path itself longer than the limit (absurdly long ProgramData/temp path). | The dispatch is recorded as `failed` with `The Outlook task command is too long for Windows Task Scheduler: ... limit 261 ...`; schtasks is never called; the Flow run stays succeeded with `Email not sent: ...`. | automated `test_an_overlong_launcher_path_fails_the_dispatch_before_schtasks`, `tests/test_flow_email_delivery.py::test_outlook_launch_failure_keeps_the_run_succeeded` |
| OT-05 | Alert and recurrence emails (existing callers of the same hand-off). | Unchanged behaviour apart from the launcher; payload contents identical. | automated `tests/test_flow_email_delivery.py`, `tests/test_email_alert_summary.py` |

## Automated checks

Focused set (Linux container, checkout-owned virtual environment from
`requirements-ci.lock`; CI runs the same files):

```bash
python -m pytest tests/test_flow_sql.py tests/test_outlook_dispatch.py tests/test_flow_excel_formats.py tests/test_flow_email_delivery.py -q
python -m py_compile app/flow_worker.py app/routers/email.py
```

Equivalent work-PC command:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_sql.py,tests/test_outlook_dispatch.py,tests/test_flow_excel_formats.py,tests/test_flow_email_delivery.py -SyntaxPath app/flow_worker.py,app/routers/email.py
```

Affected-module regression (not repeated in CI's place; CI's final-head
`Merge ready` is the authoritative full Python regression):
`tests/test_flow_worker_discovery.py tests/test_flow_local_file.py tests/test_recorded_output_storage.py tests/test_flow_recordings.py tests/test_flow_activity.py tests/test_flow_handover.py tests/test_flow_standalone.py tests/test_email_alert_summary.py tests/test_pipelines.py tests/test_legacy_artifact_compat.py`.

## Acceptance and cleanup

Accept when the focused set and final-head CI pass. Live acceptance on the BI
desktop additionally requires one previously failing share-target Flow to
succeed (SC-01) and one Email-step Flow to reach `Email submitted by Outlook`
(OT-01); record run IDs and the deployed revision. Cleanup: none beyond
disabling any test Flow; launchers are removed by the Outlook watchdog with
their payloads. Rollback: reverting restores the 7.5 s hard-failing read-back
and the long task command; no data format changed.
