# Intuitive recording — test plan

Use an isolated SQLite database, a temporary Flows root outside the checkout,
and Chrome installed by Playwright. Never point synthetic tests at the production
database or report destination.

## Automated

Run `python -m pytest tests -q`, `node tests/test_flow_builder_contract.mjs`,
`node tests/test_recording_visual_model.mjs`, and the frontend syntax checks in
`.github/workflows/tests.yml`. The new `tests/test_recording_journey.py` exercises
actual ASGI routes/database from Chromium; only worker launching and synthetic
portal authentication are replaced. The validation engine itself executes.

## Manual journey

1. In Flows create an ASAP/GSCM draft. Record my actions is the default;
   Use detected controls remains available. Existing flows keep their method.
2. Set a name, output and schedule. Open recording, capture report navigation,
   date inputs, Run report and two downloads. Finish and check the step list.
3. Save draft with missing checks. Expect Draft saved beside the action. Return
   to Edit Flow and confirm all pending values remain; reopen the recording.
4. Test. If essential evidence is missing, answer the contextual report/ready
   question. Required page, action and signal choices must be visible.
5. Edit a step, undo, reorder and inspect Advanced. Only the selected step opens.
   Test again; confirm progress and errors appear beside Test recording.
6. After success return to Edit Flow and Save. Check selected revision and
   output settings apply together. Testing alone must not activate or schedule.
7. Change output settings after testing: Save must reject stale evidence and keep
   prior settings/revision. Changing schedule alone must still save.
8. Retry an already tested version and fail/cancel it. Prior active evidence
   remains validated. Saved versions and replacement recording remain available.

## Recovery and execution

Exercise failed save, missing downloads, wrong report title, delayed results,
replaced iframe, popup, two downloads, date parameters, connection loss, worker
startup/sign-in and cancellation. Use the existing recorded browser pipeline
and worker suites. Inspect private `.recording-validation` output and verify no
production publication or SQL load occurs during validation. Check the eight-step
review at desktop, laptop and narrow widths; no invisible rejection or lost edits.

## Work PC after deployment

Update app and worker from the merged main revision. Repeat the original flow
at least twice, comparing report identity, dates, expected columns, row counts
and output contents with a manual export. Record deployed SHA, session/run IDs,
results and opaque protected evidence references. Synthetic success is not proof
of original-portal correctness. These checks remain NOT RUN until executed.

## Cleanup and rollback

Remove only the isolated test database/temporary Flows root. Production versions
and running jobs are preserved. Revert the PR through the normal reviewed workflow
if needed; do not delete stored revisions. No database migration is introduced.
