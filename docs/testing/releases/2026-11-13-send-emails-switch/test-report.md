# 2026-11-13: Send emails switch in Settings > Email: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-13 07:42
- Tested code revision: the commit that adds this package, on `main` at
  `f03be7e` (tested as the working tree before commit)
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`, Node 22.23, Playwright 1.62 driving Chrome for Testing 153. No
  Windows, Outlook or BI desktop
- Overall finding: every planned case passes (automated, synthetic), and the
  browser walk found no page error. Real Outlook delivery is not exercised;
  see the limitations.

## What was asked

The owner asked for an option under **Settings > Email** to enable or disable
emails being sent to people. It is one app-wide switch, on by default, so
nothing changes until someone turns it off.

## Change

- `email.send_enabled` (**Sending > Send emails**) is declared in the settings
  registry at the top of the Email section; the section's description now
  says it covers whether Metronome sends at all.
- `launch_outlook_dispatch` is the one place every Metronome email passes
  through. While the switch is off a `send` raises `EmailSendingOff` before a
  dispatch row is written or Outlook is launched; a `draft` is still allowed.
- Each sender records the reason instead of a failure: owner summaries
  (manual 409; scheduled passed over to the next occurrence, Event Log
  `not_sent`), Power BI email recurrences (run `not_sent`, Power BI not read),
  **Email the final file** (`skipped`; **Send again** refused while off and
  available after), Flow failure and host-change notices (`not_sent`), scanner
  failure notices (not queued, runs not marked as notified) and full-pipeline
  summaries (`not_sent`, offered for resend).
- The Email page shows a notice linking to the setting and disables the Send
  buttons only. The recurrence **Run now** toast shows the reason. The README
  describes the switch under Settings.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| E-01 to E-11 | The `python tools/check.py verify` command in the plan | Working tree on `f03be7e`, Linux | PASS: 170 passed, 0 failed, 0 skipped, 49.5 s. 2 warnings, both deprecation notices from the installed Starlette test client | `.test-runs/20261113T074122143Z-2532-af26513a/result.json` |
| Node contracts | `node tests/test_ai_operations_display.mjs`, `node tests/test_settings_sections.mjs`, `node tests/test_settings_page_loading.mjs` | Working tree, Node 22.23 | PASS | Console output |
| Browser walk, steps 1 to 7 | Playwright script described in the plan (kept outside the repository) | Working tree, Chrome for Testing 153, 1360 × 1000 | PASS, details below | Console output; four screenshots kept outside the repository |

### Browser walk

1. Settings > Email: **Send emails** visible and ticked.
2. After unticking and saving, the server read `false` and the Event Log held
   `setting_changed — Send emails: on to off`.
3. Email page notice: "Sending email is off. Metronome emails nobody until it
   is turned back on in System > Settings > Email. Outlook drafts can still be
   created." **Send Now** and **Send Selected Now** disabled with the tooltip
   "Sending email is turned off in System > Settings > Email";
   **Create Outlook Draft** enabled.
4. **Create Outlook Draft**: toast "Draft launched for 1 owner"; the fake
   `schtasks` received the create and run calls.
5. A direct send answered HTTP 409: "Sending email is turned off in System >
   Settings > Email, so nothing was sent."
6. The notice's link opened `#settings/email`.
7. Back on: no notice, **Send Now** enabled.

No page error on any load. The first walk attempts were refused as "another
computer" until the in-process app answered on the page's own origin, which
is the host check working as intended, not a defect. After the walk the
notice's link was given the teal accent colour instead of the browser default,
and the walk was rerun on that code.

## Findings, limitations and retests

- One run of the new tests failed on a comparison between the scheduler's
  stored time and an aware datetime; the test now compares in the scheduler's
  own format, as `dispatch_due_email_schedules` does, and passes.
- Synthetic only: no Windows, Outlook or BI desktop. The tests prove what is
  refused, what is recorded and that no hand-off is launched, not delivery.
- An email handed to Outlook before the switch was turned off is not
  recalled; the help text says so.
- Python-script Flows that send email from their own code are outside
  Metronome's hand-off and are not covered by the switch.

## Merge evidence

Final-head CI is pending at this cutoff. The PR's testing section records the
`Merge ready` run URL, the tested head SHA and its result before the
head-pinned merge.
