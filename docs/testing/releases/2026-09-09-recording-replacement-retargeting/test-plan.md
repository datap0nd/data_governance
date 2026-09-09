# Test plan: recording replacement and template retargeting

## Purpose and prerequisites

Verify that an existing or template-copied recording can be replaced from the
main action row and that a copied semantic click target can be renamed without
changing its recorded selector structure. Use fictional preview data for UI
review and a signed-in work PC only for the explicitly authorized live Flow.

Prerequisites: current release revision, Chrome, an available recording worker,
and—only for LIVE-01—the exact Flow open on the work PC with the local
`UGREEN-25854` feed working. Do not publish portal data, URLs, recordings, or
screenshots containing business data.

## Cases

| ID | Steps | Expected result and evidence |
| --- | --- | --- |
| UI-01 | Open a Flow with a saved recording or copied template. Inspect the action row and **More**. | **Test recording**, **Save draft**, **Record again**, **Choose from template** and applicable **Undo** are in the main row. **More** contains **Saved versions**, not **Record again**. Capture desktop and narrow fictional screenshots. |
| UI-02 | Select a copied role/text click targeting `Country`. Change **Target name (used during playback)** to `Main`; optionally enter a display-only step name. Save the draft. | The card and saved locator use `Main`. Locator method, role, frame/class context, exact-match options, action and unrelated steps are unchanged. The step name affects presentation only. |
| UI-03 | Clear the target name, then choose Save and Test. Restore a non-empty name and retry. | Empty/whitespace target is rejected beside the input and no save/test request is sent. Restoring a name permits the operation without losing other edits. |
| UI-04 | Open a click whose locator has no editable semantic name. Use the inline repair controls once without a value, then repair by exact visible text. | Repair is visible without opening **Advanced**; the empty attempt explains the required value; the valid repair becomes the editable target and Undo remains available. |
| UI-05 | Make an unsaved display edit and select **Record again**. Exercise worker-start failure and cancellation where available. | Recording starts with one click and does not create a draft from the unsaved edit. Existing saved revisions remain available. Busy controls disable; failure/cancellation restores a clear next action. |
| REG-01 | Run recording model, editor, template, startup, playback, API and journey suites plus JavaScript syntax checks. | All test bodies pass. Record exact command, revision, environment, warnings and JUnit evidence. |
| LIVE-01 | On the deployed work PC, copy a compatible template, change the intended button target from `Country` to `Main`, Test recording, then select Record again. | Test uses `Main`, a new recorder opens immediately, and Saved versions retains the earlier copy. Record app/worker revision, run/session ID and an opaque protected evidence reference. |

## Regression and cleanup

Confirm GSCM Favorite bookmark suggestion/prefill still works, template copies
remain independent drafts, Test recording does not activate a Flow, saved
versions/Undo survive polling, and the 390×844 layout has no horizontal
overflow. Cancel synthetic/live sessions created for testing, keep the live Flow
paused unless it was enabled before the test, and remove only fictional preview
state or disposable test output.
