# Visual recorded-flow editor: start here

Update app and workers from this release's merged main revision after active work
has drained. Use an existing test flow, or Flows → Create flow → Record a portal
flow. Choose GSCM or ASAP. Keep business data/traces in protected storage.

## First work-PC test

1. Click Record flow. Use the browser to open one report, set filters and download
   all required files. Wait for them to complete. Click **Finish recording in
   Metronome**; Playwright's red square only pauses recording.
2. Review the numbered cards and arrows. A captured Excel down click must appear
   once with a Download badge. Select it: Action and Options are visible; Advanced
   is closed. Check the expected format. Multiple downloads stay separate cards.
3. Confirm the suggested report title, or select **Change** to choose the exact
   report title and its page/frame. Select a date-input card only if its recorded
   value needs adjustment. There are no timezone or date-batching controls.
4. Optionally click **+** between cards to insert a five-second wait. Rename it,
   change its seconds, move it, remove it, then Undo. Verify the original sequence.
5. Click **Test flow**. It saves first, then shows progress on the cards. Select a
   failure to see its message and repair options. A successful test says Ready to
   run. It must not publish production files or execute SQL.
6. Click **Enable schedule** for an already configured schedule. A manual-only
   flow directs you to Pipeline and schedule. Confirm the next run at the saved
   Dubai clock hour. Editing the tested recording must disable Enable schedule
   until another successful test. Confirm the previous active version stays
   selected while editing its replacement.

## Complete verification matrix

| Case | Action | Expected result |
| --- | --- | --- |
| VIS-01 | Record one download and then two downloads | Listener plus single click is one card; both files complete before Finish; correct output count |
| VIS-02 | Expand a multi-action popup/download group; move it and save/reopen | Event scope, nested IDs and frame/page identities are preserved; groups move as units |
| VIS-03 | Move popup use before creation or after closure | Move rejected on client and server; no changed definition |
| VIS-04 | Remove an input with date/output references, then Undo | Owned parameters/checks cleaned; unresolved identity/readiness requires repair; Undo restores original |
| VIS-05 | Insert waits of 1 and 600 seconds; try 0, 601 and fractions; cancel long wait | Valid whole seconds only; cancellation/heartbeat work; later steps do not run; same portable behavior |
| VIS-06 | Rename/input edit, wait for several polls; open/close Advanced | Selection, unsaved value and collapsed state remain; one details panel |
| VIS-07 | Run test with slow download, missing title, button/portal name as title or wrong schema | Correct card shows failure and explanation; no successful activation or production publication |
| VIS-08 | Use keyboard selection and Move up/down; use drag handle; narrow screen | Controls operate without mouse; mobile details follow selected card |
| VIS-09 | Edit after test; test again; enable | Save precedes validation; changed draft cannot enable; exact tested revision selected |
| VIS-10 | Open History and old batch conversion | History stays immutable; batching stays blocked; explicit conversion requires retest |
| VIS-11 | Test fixed start/default end and calculated date across midnight | Default write omitted, resolved values logged, date behavior remains in input Options |
| VIS-12 | Export portable script, run without Metronome installed | Same downloads, wait behavior, validation and Python transformation; SQL safeguards retained |

Before validation, verify an outdated worker cannot claim the new validation
job and a mismatched execution hash fails before authentication. Update app and
workers together; the worker advertises `recorded_validation_engine_v1`.

Run full Python suite, all Node test files and frontend syntax checks. CI must
pass on Windows/Linux at final PR head. Synthetic browser fixtures cover repeated
iframe replacement, popups, multiple outputs and portable execution; existing
regressions cover files, Outlook, bookmarks, transformation, SQL and locks.

Live acceptance: perform three fresh-browser tests for representative GSCM and
ASAP reports, including one custom HTML report and multiple files. Compare report
identity, periods, rows and schema, not workbook bytes. This is a bounded manual
pilot; the stopped ten-minute monitor stays stopped. Record actual SHA/browser,
case ID, session/run ID, result and sanitized evidence reference. Cancel remaining
test sessions and remove only test artifacts after collecting evidence.
