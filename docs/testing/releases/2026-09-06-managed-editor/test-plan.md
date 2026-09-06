# Managed Flow editor: test plan

This change applies the owner's feedback on the recording preview: name first,
one label per category, no execution contract, no folder choice, and controls
that match the selected method.

## Setup

Use matching app and worker revisions from the merged PR. Configure the Flows
root once in System settings. Use a disposable GSCM or ASAP flow and a report
with harmless test data. Keep any existing production files untouched.

The fictional [editor preview](../../../../app/static/recording-preview/managed.html)
uses the production editor with substituted API calls. Serve `app` locally and
open `/static/recording-preview/managed.html`; it does not run a real portal.

## Cases

1. **EDIT-1 — Setup.** Create a recorded flow. Name is the first field. Source
   shows Website and Method. No bookmark, report filters, category subtitles,
   target path field, or execution-contract panel is shown. Switch to detected
   controls: Bookmark/Report appears. Switch back: it disappears and the name
   and output settings remain. Repeat at 390px width without horizontal scroll.
2. **EDIT-2 — Record.** Click Record actions from Edit Flow. The server creates
   the managed module folder before opening recording. No separate flow-name
   dialog or catalog scan is required. Return to Edit Flow; pending edits remain.
   A missing page assertion asks for text to check, not a new flow name.
3. **PATH-1 — Test with enforcement.** Enable path enforcement. In Edit Flow,
   review and test a recording. The request contains no destination. The test
   uses the saved module folder and private validation output; it must not fail
   with “Target folder must be inside …” or publish production files/SQL.
4. **PATH-2 — Management.** Create a flow through an older API payload containing
   an arbitrary target path. The returned destination is under the configured
   root/module/flow/Downloads. Save a legacy flow: future output becomes managed,
   historical files remain untouched. Open folder navigates to the managed flow.
   A queued legacy run must finish before its folder is adopted.
5. **OUTPUT-1 — Fixed path.** Choose “Fixed file path · replace previous output”.
   Use a filename without date/period tokens (use `{export}` for multiple files).
   Run twice. Both runs publish to the same file path and the second replaces
   the first only after successful validation. Point a test Power BI/Excel
   connection at that path and refresh after the second run.
6. **OUTPUT-2 — History.** Choose “Separate runs · keep last 3”. Confirm the UI
   explains separate folders. Check ordinary retention still keeps the last
   three unpinned producing runs. Switch back to fixed output: old history must
   not be deleted. Date tokens in fixed output show a contextual warning.
7. **RECOVERY-1 — Failure.** Simulate a failed Save; edits remain and retry works.
   Test invalid recording checks, missing downloads and a disconnected worker.
   Existing active recording evidence remains unchanged until a successful test
   and Save. Repeat folder/test checks on the actual UNC share.
8. **REG-1 — Other sources.** Open Outlook and file-source editors. Name is first,
   no destination picker or duplicate summaries return. Outlook retains original
   attachment names; its fixed-output help explicitly explains dated names.

## Automation and evidence

Run the full Python suite, all `tests/test_*.mjs` suites, and syntax checks for
changed JavaScript. The focused suite is `test_managed_flow_editor.py`,
`test_recording_journey.py`, `test_recording_visual_editor.py`, `test_flow_paths.py`,
`test_flow_layout.py`, and `test_flow_publish.py`.

Record app/worker SHA, browser, case ID, run/session ID, observed output paths
and sanitized screenshots. Live portal, UNC permissions and downstream Power BI
checks must remain NOT RUN until actually performed. Remove only the disposable
flows and test connections created for this plan; preserve historical files.
