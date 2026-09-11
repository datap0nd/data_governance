# Flow "Email the final file" step: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: recorded under Merge evidence.
- Evidence cutoff (UTC): 2026-09-11, before the implementation commit.
- Tested code revision: the working tree that became the implementation
  commit on `claude/friendly-ride-78xbol` on top of `494cc15`; the PR testing
  section records the final tested SHA.
- Environment: Linux container, Python 3.11.15 in a checkout-owned virtual
  environment built from `requirements-ci.lock` (the container's system
  Python could not install the lock because of a Debian-owned `packaging`),
  pytest with Playwright 1.62.0 and the preinstalled Chromium 1194 aliased to
  the `chrome` channel path (the network policy blocks Chrome downloads);
  Node 22.22.2. No Outlook, worker, portal or PostgreSQL is available here.
- Overall finding: automated checks PASS; the usability preview was walked in
  a real browser with fictional data and screenshots reviewed; live Outlook
  delivery is an owner check and was NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| EM-03…EM-10 (API) | `python -m pytest tests/test_flow_email_delivery.py -q` | working tree, Linux venv | PASS: 12 passed in 9.66 s | Config normalization (split, de-duplication, limits, subject), field-level 422 location, persistence independent of SQL with keep-on-omit and clear-on-disable, frozen job, hash/handover exclusion (no recipient text in the frozen script configuration or handover JSON), success hand-off payload (`to`, default subject, two attachments, row counts, `flow_file` dispatch, `pending` status, event and log rows), custom subject and transformed output, no email for no-op/failed/disabled runs, launch failure keeps the run succeeded with `last_success_at`, over-cap description instead of attachments, receipt mirror (`submitted`, `failed` with the attachment error), Send again rules (new dispatch, `email_resent`, 502 on failure, 409 for failed/no-op/no-step runs, 404), retry file resolution. |
| EM-01, EM-02, EM-03, EM-05, EM-07, EM-08, UX-01 (browser) | `PREVIEW_EVIDENCE_DIR=docs/testing/releases/2026-09-11-flow-email-file/evidence python -m pytest tests/test_templates_refresh_preview.py -q -k test_email_step_builder_run_history_and_send_again` | same, headless Chromium 1280×900 and 390×844 | PASS: 1 passed in 4.70 s | Screenshots in [evidence/](evidence/): `builder-email-step.png`, `builder-email-invalid.png`, `runs-email-status.png`, `run-log-email-failed.png`, `run-log-email-sent.png`, `builder-email-narrow.png`, `run-log-email-narrow.png`. Invalid address focuses Recipients with the server message; empty recipients are blocked before submission; Send again confirms, reports a failed hand-off beside the button, and shows `Submitted by Outlook` after a successful one; no page errors; no horizontal overflow at 390 px. |
| Builder payload and list/run wording | `node tests/test_flow_builder_contract.mjs && node tests/test_flows_display.mjs` | same, Node 22.22.2 | PASS | `_flowEmailRead` splits on `;`, `,` and newlines and sends the Off shape when disabled for file, Outlook and portal payloads; the 422 location `email_delivery` focuses Recipients; the Download cell shows `· Email N recipient(s)`; one status text per run state. |
| Syntax | `node --check app/static/app.js app/static/flow_run_log.js`; `python -m py_compile app/flow_email_delivery.py app/routers/flows.py app/routers/email.py app/database.py app/flow_standalone.py app/flow_recordings.py app/flow_handover.py` | same | PASS | Console. |
| Regression: affected modules | `python -m pytest tests/test_flows.py tests/test_flow_handover.py tests/test_flow_recordings.py tests/test_flow_standalone.py tests/test_outlook_dispatch.py tests/test_templates_refresh_preview.py tests/test_managed_flow_editor.py tests/test_flow_activity.py tests/test_email_alert_summary.py -q` | same | 306 passed, 1 failed, 2 warnings in 152 s. The failure is `tests/test_flow_recordings.py::test_same_portable_pipeline_runs_real_download_on_both_browsers[msedge]`: Playwright cannot find Microsoft Edge in this container (`Chromium distribution 'msedge' is not found`); the `chrome` parameter of the same test passed. CI installs Chrome and Edge and is the authoritative regression. | Console. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| EM-04 live delivery | NOT RUN | No Outlook, worker or BI desktop in the container; the hand-off is captured as the scheduled-task payload and the receipt is written synthetically. | On the BI desktop, enable the step on a disabled test Flow with your own mailbox, run it once, confirm the attachment opens, and record run ID, revision and an opaque mailbox reference here. |
| Owner feedback on the preview | NOT RUN | The owner was not available during this autonomous session; the preview and the implementation shipped together, as in the 2026-09-07 release. | Open the preview (see plan) and review the journey; material changes return for review. |

## Findings, limitations and retests

- The PowerShell attachment loop in `tools/outlook_task_email.ps1` cannot
  execute on Linux; its contract (one `path` per attachment, a missing file
  fails the dispatch so the receipt names it) is covered by the payload and
  receipt-mirror tests only.
- The first run of the new browser walk failed on its own assertion (the
  production `Flow saved` toast overwrites the preview status line); the
  assertion was corrected to the toast text and the walk passed on retest.
- Two new API tests initially seeded the fictional website twice in one test
  (409 "website already exists"); they now create the second Flow on the same
  catalog. No production code changed for either retest.
- Synthetic dispatches prove the payload, not Outlook's behaviour; whether the
  interactive Outlook task can read a worker-private store path is verified
  only by the live check above.

## Merge evidence

Final-head CI is pending at this cutoff. Record its run URL and exact head SHA
in the PR before merging; the PR's merge record supplies the merge SHA.
