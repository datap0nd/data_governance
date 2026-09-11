# Flow "Email the final file" step: test plan

- Change/PR: an optional last step in every Flow builder (portal, Outlook and
  file sources) that emails **the final file** to configured recipients after
  every successful producing run. The attachment is exactly what SQL insertion
  receives: the normalized CSV of each download, or the transformation result
  when a script runs. The step is independent of SQL insertion and both can
  coexist. The API process hands the message to Outlook on the BI desktop
  through the existing scheduled-task helper (`tools/outlook_task_email.ps1`
  gains an `attachments` list); the run keeps the worker's data result and
  records the email outcome separately (`pending` → `submitted` / `failed` /
  `unknown`, or `skipped`), mirrored from the Outlook receipt. Files over 20 MB
  in total are described instead of attached. Run history shows the status,
  the run log adds an **Email the final file** section with **Send again**.
  Files: `app/flow_email_delivery.py` (new), `app/routers/flows.py`,
  `app/routers/email.py`, `app/database.py`, `app/flow_recordings.py`,
  `app/flow_standalone.py`, `app/flow_handover.py`, `tools/outlook_task_email.ps1`,
  `app/static/app.js`, `app/static/flow_run_log.js`, `app/static/style.css`,
  `app/static/recording-preview/templates-refresh*`, docs and tests.
  PR link: recorded in the test report's Merge evidence section.
- Code baseline: `494cc15` (main after PR #112).
- Related report: [test-report.md](test-report.md)
- Intended environments: Metronome app on the BI desktop with Outlook signed in
  and at least one Flow worker; the automated checks run anywhere with Python
  3.11+, Node 22 and Playwright Chromium (CI installs Chrome/Edge). Live Outlook
  delivery is an owner check and is not performed by the automated evidence.

## Prerequisites and test data

- Update the app from the merged `main` revision. Migrations add
  `flows.email_delivery_json`, `flow_runs.email_dispatch_id`,
  `flow_runs.email_status`, `flow_runs.email_detail` and an index; they run on
  startup. Workers need no update: the email is sent by the API process.
- Outlook must be signed in on the BI desktop account that also runs the Flow
  workers, so the interactive Outlook task can read the run folder or the
  private worker store where the final file lives.
- Fictional preview: serve `app` with
  `python -m http.server 8769 --bind 127.0.0.1 --directory app` and open
  `http://127.0.0.1:8769/static/recording-preview/templates-refresh.html`.
  The preview bar's **Send again outcome** switches whether the fictional
  Outlook hand-off succeeds or fails.
- Live data: one disabled test Flow with a safe destination (a file-source or
  Outlook Flow is enough) and a mailbox you control as the recipient.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| EM-01 | Edit any Flow; open step **After download**. | Below **SQL handoff** a block **Email the final file** shows a checkbox, **Recipients** and **Subject (optional)**. The fields are hidden until the checkbox is on. Turning SQL insertion off leaves the Email block available. New flows start with the step off. | `builder-email-step.png`, API test |
| EM-02 | Enable the step, enter `planner` as recipient, **Save changes**. | The form reports `Flow not saved: Invalid email address: planner`, the After download step opens and the Recipients field has focus; the other entries are preserved. Empty recipients are blocked before submission with the same focus. | `builder-email-invalid.png` |
| EM-03 | Enter `planner@example.test; ops@example.test` and a subject, save. | The saved Flow returns `email_delivery` with two recipients and the subject; the Flows list Download cell reads `· Email 2 recipient(s)`. An older client that omits the field keeps the saved setting; unchecking the box clears recipients on save. | API test `test_email_step_persists_independently_of_sql` |
| EM-04 | Run the Flow (manual or scheduled) so that it succeeds. | After the worker reports success the run's Result cell reads `Email: handed to Outlook, waiting for its receipt`; within about a minute the Outlook watchdog mirrors the receipt (`Email submitted by Outlook`). The recipient receives one email whose subject is `Metronome flow file: <flow> (run #N)` (or the custom subject) with the normalized CSV attached, one attachment per download; when a transformation runs the `script_results` output is attached instead. | API test `test_succeeded_run_hands_the_final_file_to_outlook`, `test_custom_subject_and_transformed_output_are_used`; live owner check |
| EM-05 | Run a Flow whose final files total more than 20 MB. | The email is sent without attachments and names the file location; Run history reads `Email submitted by Outlook: Attachments skipped: … exceeds the 20 MB limit`. | API test `test_files_over_the_cap_are_described_instead_of_attached`, `runs-email-status.png` (run #38) |
| EM-06 | Run an Outlook or file Flow whose source is unchanged (no-op); make another run fail; run a Flow without the step. | No email is sent in any of these cases; the failed run still emails the Flow owner as before; the run log of a Flow without the step shows `Email: Disabled`. | API test `test_no_op_failed_and_disabled_runs_do_not_email` |
| EM-07 | Make the Outlook hand-off fail (for example Outlook closed or the scheduled task refused). | The run stays **succeeded** with its SQL outcome and `last_success_at`; Run history reads `Email not sent: <reason>`; the run log's Email section shows the reason and offers **Send again**; the event log records `email_failed`. | API test `test_outlook_launch_failure_keeps_the_run_succeeded`, `run-log-email-failed.png` |
| EM-08 | In the run log use **Send again**; cancel the confirmation once, then confirm. | The confirmation names the file(s) and recipients and warns about duplicates when Outlook already submitted the email. Cancel sends nothing. Confirming hands the file to Outlook again, records `email_resent`, and the status updates to the new receipt. Send again is refused for failed runs, no-op runs and Flows without the step. | `run-log-email-sent.png`, API test `test_send_again_rules_and_outcome` |
| EM-09 | Retry SQL only / Retry view refresh after a failed run of a Flow with the step. | The recovery run that succeeds emails the same final file(s): SQL-only retries use their carried files, view-refresh retries use the files of the run they recover. | API test `test_retry_runs_resolve_the_source_files` |
| EM-10 | Recorded Flow with the step enabled: run, enable, save; generate Flow files. | No `409` about a changed recording configuration; `Scripts/run_flow.py` and the handover JSON contain no recipient address and their hashes are unchanged by the email setting. | API test `test_email_config_stays_out_of_recording_script_and_handover_hashes` |
| UX-01 | Repeat EM-01, EM-02 and the run log at 390 px width. | No horizontal overflow; controls, feedback and Send again remain visible. | `builder-email-narrow.png`, `run-log-email-narrow.png` |

## Automated checks

Focused set (Linux container, checkout-owned virtual environment from
`requirements-ci.lock`; CI runs the same files):

```bash
python -m pytest tests/test_flow_email_delivery.py -q
PREVIEW_EVIDENCE_DIR=docs/testing/releases/2026-09-11-flow-email-file/evidence \
  python -m pytest tests/test_templates_refresh_preview.py -q -k test_email_step_builder_run_history_and_send_again
node tests/test_flow_builder_contract.mjs && node tests/test_flows_display.mjs
node --check app/static/app.js && node --check app/static/flow_run_log.js
python -m py_compile app/flow_email_delivery.py app/routers/flows.py app/routers/email.py app/database.py
```

Equivalent work-PC command:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath tests/test_flow_email_delivery.py,tests/test_templates_refresh_preview.py::test_email_step_builder_run_history_and_send_again -SyntaxPath app/flow_email_delivery.py,app/routers/flows.py,app/routers/email.py,app/static/app.js,app/static/flow_run_log.js
```

Affected-module regression (not repeated in CI's place; CI's final-head
`Merge ready` is the authoritative full Python regression):
`tests/test_flows.py tests/test_flow_handover.py tests/test_flow_recordings.py tests/test_flow_standalone.py tests/test_outlook_dispatch.py tests/test_templates_refresh_preview.py tests/test_managed_flow_editor.py tests/test_flow_activity.py tests/test_email_alert_summary.py`.

## Acceptance and cleanup

Accept when the focused set and final-head CI pass and the fictional preview
walk shows the builder, run history and run log states above. Live acceptance
additionally requires one real Outlook delivery from the BI desktop with the
attachment opened by the recipient; record run ID, revision and recipient
mailbox as an opaque reference. Disable the test Flow afterwards and delete the
test email. Rollback: a Flow with the step off behaves exactly as before; the
new columns are nullable and harmless when unused. No worker restart is needed.
