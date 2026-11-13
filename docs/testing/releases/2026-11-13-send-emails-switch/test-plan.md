# 2026-11-13: Send emails switch in Settings > Email: test plan

- Change/PR: link in the PR's testing section. Adds **Send emails** at the top
  of **System > Settings > Email**. While it is off Metronome emails nobody,
  every sender records that it did not send and why, and Outlook drafts still
  work.
- Code baseline: `main` at `f03be7e`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux, Python 3.13 checkout-owned `.venv`, Node 22,
  Chromium; synthetic fixtures only. Outlook, schtasks and the BI desktop are
  replaced by the monkeypatches `tests/test_outlook_dispatch.py` uses.

## Prerequisites and test data

Each test builds its own SQLite database and points both `app.database` and
the settings store at it, so a saved **Send emails: off** never reaches
another test. People, schedules, Flows, recurrences and pipeline runs are
fictional (`example.test` addresses).

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| E-01 | Read the registry and the Email section of the Settings page | **Send emails** is a bool, on by default, applies immediately, and is the first row of the first group (**Sending**) of Email | `test_send_emails_leads_the_email_section_and_is_on_until_changed` |
| E-02 | Turn it off; hand one message to Outlook in `send` mode, then in `draft` mode; turn it on and send again | Send raises `EmailSendingOff` (409) with the sentence, writes no `outlook_dispatches` row and runs no `schtasks`; the draft is launched; once on, the send is launched | `test_while_off_nothing_is_handed_to_outlook_but_a_draft_still_is` |
| E-03 | Off; open the Email page data and **Send Now** an owner summary; then **Create Outlook Draft** | The page data says `sending_enabled: false`; send answers 409 with the sentence and records nothing; the draft is launched | `test_the_email_page_is_told_and_a_manual_send_is_refused_with_the_reason` |
| E-04 | Off; an enabled owner summary schedule is due; the scheduler runs; turn it on and run again | Nothing is built or sent; `last_error` is the sentence, `last_sent_at` stays empty, the next occurrence is in the future, the Event Log says `not_sent`; back on, nothing old is released | `test_a_due_scheduled_summary_is_passed_over_and_says_why` |
| E-05 | Off; a Flow with **Email the final file** succeeds; press **Send again**; turn it on and press it again | The run stays succeeded with email `skipped` and the sentence, a run event and an Event Log entry; **Send again** answers 409 while off; once on it hands the file to Outlook | `test_a_flow_file_email_is_skipped_with_the_reason_and_send_again_waits` |
| E-06 | Off; a Flow run fails; the host PC changes someone else's Flow | Both notices are `not_sent` with the sentence and logged as skipped; nothing is handed to Outlook | `test_flow_owner_notices_are_recorded_as_not_sent` |
| E-07 | Off; a scheduled Power BI email recurrence is due; its owner failure notice is attempted | The run is `not_sent` with the sentence, Power BI is not read, nothing is launched, the recurrence moves to its next occurrence; the owner notice is `not_sent`, not `failed` | `test_an_alert_email_run_exports_and_sends_nothing` |
| E-08 | Off; a finished full-pipeline run hands its summary to Outlook | The run's summary is `not_sent` with the sentence, the notification step is `skipped`, no dispatch row | `test_a_pipeline_summary_is_not_sent_and_stays_resendable` |
| E-09 | Off; scanner failure recipients saved; a failure notice is queued; **Send test** is pressed | The notice returns `disabled` with the sentence and does not mark the runs as notified; the test notice is refused with the sentence | `test_scanner_failure_notices_are_not_queued_while_off` |
| E-10 | Read `app.js` | The Email page shows the notice and disables only the Send buttons; the drafts button is untouched; the recurrence **Run now** toast shows the reason instead of "No matching rows" | `test_the_email_page_shows_the_notice_and_disables_only_sending` |
| E-11 | Existing Outlook, Flow email, settings, recurrence, alert summary, scanner notification, Flow audit and Flow settings tests | Unchanged behaviour while the switch is on (the default) | Files listed under Automated checks |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_email_sending_switch.py --test tests/test_outlook_dispatch.py \
  --test tests/test_flow_email_delivery.py --test tests/test_settings_registry.py \
  --test tests/test_recurrences.py --test tests/test_email_alert_summary.py \
  --test tests/test_modular_scanner.py --test tests/test_audit_flows_api.py \
  --test tests/test_settings_flows.py \
  --syntax app/routers/email.py --syntax app/routers/email_schedules.py \
  --syntax app/routers/flows.py --syntax app/routers/pipelines.py \
  --syntax app/routers/recurrences.py --syntax app/scanner/notifications.py \
  --syntax app/flow_email_delivery.py --syntax app/settings_catalog.py \
  --syntax app/settings_registry.py --syntax app/static/app.js \
  --syntax tests/test_email_sending_switch.py
node tests/test_ai_operations_display.mjs
node tests/test_settings_sections.mjs
node tests/test_settings_page_loading.mjs
```

Final-head CI (`Merge ready`) is the full regression.

## Usability evidence

A synthetic browser walk: the real `app/static` page in Chromium, with every
`/api` call answered by the real FastAPI app in process (no scheduler, workers
or updater) on an isolated database, opened as the host PC on its own origin.
Alert summaries and Outlook are fictional.

1. **Settings > Email** shows **Sending > Send emails**, on, with its help text.
2. Untick it and **Save changes**: the server reads off and the Event Log
   records `Send emails: on to off` with who changed it.
3. **Email** page: an amber notice names the switch and links to it; **Send
   Now** and **Send Selected Now** are disabled with a tooltip; **Create
   Outlook Draft** and **Create Drafts for Selected** stay enabled.
4. **Create Outlook Draft** still launches a draft.
5. A send sent around the disabled button is refused with the sentence.
6. The notice's link opens **Settings > Email**.
7. Turn it back on: the notice is gone and **Send Now** is enabled.

## Acceptance and cleanup

Accepted when E-01 to E-11 pass, the walk shows no page error, and final-head
CI passes. Nothing to clean up: every fixture is temporary. Rollback: turn
**Send emails** on (the default), or revert the PR; the setting stores one
`app_settings` row, which an older build ignores.
