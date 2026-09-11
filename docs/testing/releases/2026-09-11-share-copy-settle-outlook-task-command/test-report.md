# Share copy settle wait and Outlook task command: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: recorded under Merge evidence.
- Evidence cutoff (UTC): 2026-09-11, before the implementation commit.
- Tested code revision: the working tree that became the implementation
  commit on `claude/epic-euler-frboms` on top of `fa2639a`; the PR testing
  section records the final tested SHA.
- Environment: Linux container, Python 3.11.15 in a checkout-owned virtual
  environment built from `requirements-ci.lock` (`python3 -m venv .venv`,
  `pip install -r requirements-ci.lock`; `tools/check.ps1` needs PowerShell,
  which the container lacks). No Windows, SMB share, Outlook, worker or
  portal is available here.
- Overall finding: automated checks PASS; the share lag and schtasks are
  synthetic; live BI-desktop confirmation (SC-01, OT-01) is an owner check
  and was NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| SC-01…SC-05, OT-02…OT-05 (focused) | `python -m pytest tests/test_flow_sql.py tests/test_outlook_dispatch.py tests/test_flow_excel_formats.py tests/test_flow_email_delivery.py -q` | working tree (final), Linux venv | PASS: 133 passed in 49.90 s (49.75 s on the run before the last label-only edit) | Settle wait confirms after two short reads; never-settling short view and same-size different bytes return `confirmed=False` with one `copy_verification_warning` event naming the label, byte counts and "treated as delivered"; unreadable target raises `could not be read back`; `os.fsync` observed once with the full byte count while the handle was open; Excel normalization opened the extension-less staging file (`read_from == [source]`) while the share copy stayed byte-identical and the warning reached the progress callback; `/tr` is exactly the launcher command (≤ 261 characters where the old command exceeded it), launcher lines and `-Send`/draft variants verified, launcher removed on reconciliation, over-long launcher path fails the dispatch before any schtasks call with the limit in the error. |
| Baseline of the same files before the change | same command on `fa2639a` | Linux venv | 114 passed in 34.18 s | Establishes that the 19 added/rewritten cases are the delta. |
| Syntax | `python -m py_compile app/flow_worker.py app/routers/email.py` | same | PASS | Console. |
| Regression: affected modules (SC-05, SC-06, OT-05) | `python -m pytest tests/test_flow_worker_discovery.py tests/test_flow_local_file.py tests/test_recorded_output_storage.py tests/test_flow_recordings.py tests/test_flow_activity.py tests/test_flow_handover.py tests/test_flow_standalone.py tests/test_email_alert_summary.py tests/test_pipelines.py tests/test_legacy_artifact_compat.py -q` | same | 305 passed, 6 failed, 1 skipped, 2 warnings in 137.61 s. All six failures were browser availability in this container (`Chromium distribution 'chrome'/'msedge' is not found`): three `test_ui_*` cases, the portable `rejects_binary_mislabeled_as_xlsx` case and both `runs_real_download_on_both_browsers` parameters. After aliasing the preinstalled Chromium 141 to the Chrome channel path, the five Chrome-dependent cases were rerun and PASS (`5 passed in 39.40 s`), including the real portable download through the changed copy path; `[msedge]` remains unavailable here and CI installs Chrome and Edge. | Console. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| SC-01, SC-02 live | NOT RUN | No Windows SMB share or Flow worker in the container; the lagging share is simulated by a path wrapper. | On the BI desktop, update app and workers, run one previously failing share-target Flow and record run ID, revision and whether a `copy_verification_warning` event appeared. |
| OT-01 live | NOT RUN | No Outlook or Task Scheduler in the container; schtasks is a fake. | Run one Email-step Flow to success and confirm `Email submitted by Outlook` and the received attachment; record run ID and revision. |

## Findings, limitations and retests

- The first version of the local-normalization test failed because openpyxl
  rejects a file *name* without an Excel extension before reading it
  (`InvalidFileException`), and both the test fixture and Playwright's staged
  downloads have no extension. `_open_excel_workbook` now hands openpyxl an
  open handle for such names (validated by content) and error messages carry
  the delivered file name through a new `source_label`; the test passed on
  retest with no other production change.
- The settle wait cannot distinguish a share that never refreshes from a file
  that was silently truncated after a successful flush; the flushed copy
  stream is the accepted proof, and the recorded warning is the audit trail
  for the rare case. The CSV, transformation and SQL steps still validate the
  data they read.
- `app/scanner/pbi_sync.py` builds its own schtasks command with a script
  path and API base; it stayed under the limit on the reported desktop and is
  out of scope here.

## Merge evidence

- First-head CI (`39fe421`, [run 34612379655](https://github.com/datap0nd/data_governance/actions/runs/34612379655)):
  Change scope, Frontend contracts and syntax PASS; PostgreSQL skipped by the
  scope gate; `Python (ubuntu-latest)` FAIL with 1 failed, 1989 passed,
  23 skipped in 17 min 9 s. The one failure,
  `tests/test_recording_controls.py::test_close_recorder_terminates_its_owned_process_tree`,
  raised `ProcessLookupError: [Errno 3] No such process` reading
  `/proc/<pid>/stat`: the test's own assertion checked that the entry existed
  and then read it, and the zombie child was reaped in between. The test is
  unrelated to this change (recorder process teardown); its assertion now
  reads the entry once and treats a vanished entry as terminated (the branch
  the comment already allowed). Locally the case passed three consecutive
  runs after the fix (`1 passed` each). No application code changed for this
  retest; the focused set and syntax checks above remain valid for the
  application files.

Final-head CI on the retest head is pending at this cutoff. Record its run
URL and exact head SHA in the PR before merging; the PR's merge record
supplies the merge SHA.
