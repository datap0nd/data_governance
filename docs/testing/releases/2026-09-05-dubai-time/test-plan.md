# Dubai time: test instructions

This is increment 1 of the visual recorded-flow redesign. Application calendars,
browser contexts and displayed instants use Dubai time. Schedule hours stay
unchanged; monitoring timestamps are stored as UTC. No timezone controls are
needed. The batch and visual-editor changes follow separately.

## Work-PC cases

Use test flows and test email/recurrence recipients. Capture app/worker versions,
the test ID, actual result and a protected evidence reference for each attempt.

| ID | Steps | Expected result |
| --- | --- | --- |
| TIME-01 | Update with active work drained. Check a saved 09:00 daily Flow and a 09:00 email/Power BI recurrence. Compare next occurrence against Dubai's current day. | Saved hour stays 09:00. Due instant is 05:00 UTC. No missed occurrence is backfilled; dispatch occurs once. |
| TIME-02 | Open the same history from browsers on PCs with different OS timezones. Compare a known UTC timestamp, including one crossing Dubai midnight. | Both show the same Dubai date/time; elapsed age is unchanged. No timezone field or label appears. |
| TIME-03 | Open an existing recording after update. Compare original history and the new draft. Test the draft with SQL disabled. | Original definition remains immutable; new draft uses Dubai dates and preserves fixed input values. It needs testing before scheduling. |
| TIME-04 | Record and execute a small Chrome report, then repeat with Edge. Inspect a calculated date and portal browser clock. | Both use Dubai time independently of worker OS; download identity/schema/period are correct. |
| TIME-05 | Compare historical run timestamps before and after update. Restart the app again. | Actual historical instants are preserved; migration is not applied twice. Frozen job JSON and source/report dates are unchanged. |

The migration retains original converted timestamp values in the private
`time_policy_backup` table and records its cutoff/old machine zone in app settings.
UTC source evidence and explicit-offset history are not reinterpreted. Retain the
normal database backup for rollback; restore the matching database/code pair
through maintenance rather than downgrading live workers.

## Automated checks

Run `python -m pytest tests/test_dubai_time_policy.py -q`, the full
`python -m pytest tests -q`, all `tests/test_*.mjs` Node suites and syntax checks
for `app.js`, `flow_recordings.js` and `flow_run_log.js`. CI runs on Windows and
Linux. Local pytest temporary folders must be outside the checkout.

After testing, disable test schedules and restore test destinations. Do not
publish real reports, browser state or recipient information to GitHub.
