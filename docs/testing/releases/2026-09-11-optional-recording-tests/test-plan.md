# Optional recording tests: test plan

- Change/PR: Metronome no longer forces a recording test when a Flow changes.
  Before this change, a recorded Flow could only be saved, enabled, run or
  activated while its selected recording version carried test evidence for
  exactly the current settings and transformation bytes; any settings change
  after a test answered with `Record and validate this Flow configuration, or
  approve saving without testing, before running or enabling it.` (409), the
  builder opened a **Save without testing?** confirmation for untested drafts,
  and `Saved versions` activation refused untested versions. Now:
  - Saving a recorded Flow never checks test evidence. Settings, transformation,
    schedule and recording-version changes save immediately; the retired
    `allow_untested_recording` waiver flag is accepted and ignored.
  - Running, enabling and scheduling use the selected recording as it is, with
    the current settings and the current transformation bytes. Only a missing
    recording (`Choose a saved recording for this Flow before running or
    enabling it.`) or a structurally unrunnable one (422, save rolled back)
    still stops a run.
  - The builder saves an untested draft without a confirmation dialog and
    reports `Flow saved. This recording has not been tested; check the first
    run output.`; unsaved recording edits still stop the save.
  - Any saved version can be activated from the API; the template and
    conversion notes say testing is optional.
  - **Test recording** stays available. The job and the generated `run_flow.py`
    header record whether the active version's evidence matches the current
    configuration (`recording.tested`; `has not been tested` / `That test used
    different Flow settings or transformation bytes` header lines).
  Files: `app/flow_recordings.py`, `app/routers/flows.py`,
  `app/routers/flow_recordings.py`, `app/flow_portable.py`, `app/static/app.js`,
  `app/static/flow_recording_editor.js`, `docs/recorded_flows.md`,
  `docs/flow_standalone.md`, `README.md`, `tests/test_recording_journey.py`,
  `tests/test_recording_templates.py`, `tests/test_flow_save_without_test.mjs`,
  this package and the testing index. PR link: recorded in the test report's
  Merge evidence section.
- Code baseline: `4c81929` (main after PR #115).
- Related report: [test-report.md](test-report.md)
- Intended environments: the Metronome app on the BI desktop for owner
  checks; the automated checks run anywhere with Python 3.13 and the locked
  test dependencies (Chrome channel for the synthetic browser journey). Live
  portal runs are owner checks and are not performed by the automated evidence.

## Prerequisites and test data

- Update the app from the merged `main` revision. No migration is involved;
  existing `approved` (saved without test) and `validated` versions keep their
  status and remain runnable. Workers need no update.
- Owner checks use one existing recorded Flow whose active version was tested
  earlier, plus one new recording that is never tested. Use harmless reports;
  do not publish portal URLs, credentials, workbooks or traces.
- Synthetic tests use the isolated SQLite database, a fake validation worker
  result and, for the browser journey, the shipped recording editor in
  Chromium.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| OT-01 | Open a recorded Flow whose active version was tested; change the file name template (or output folder mode, transformation script, SQL target or schedule) in Edit Flow; **Save**. | The Flow saves at once with `Flow saved`; no 409 and no confirmation. Run history and the next run use the new settings. | automated `test_pending_snapshot_atomic_apply_and_active_evidence` (`changed` save returns 200, `_build_job` runs revision with `tested` False, restoring the tested settings returns `tested` True) |
| OT-02 | Same Flow; edit the transformation script after the test; **Save**; **Run now**. | The save succeeds; the run uses the current script bytes (`recording.transformation_source` equals the saved file), not the tested copy. | automated `test_changed_transform_source_saves_and_runs_with_current_bytes` |
| OT-03 | Record a new Flow, **Save draft**, **Back to Edit Flow**, **Save** without **Test recording**. | No **Save without testing?** dialog. Toast: `Flow saved. This recording has not been tested; check the first run output.` The Flow returns to the list and can be run and scheduled. | automated `tests/test_flow_save_without_test.mjs` (no `confirm`, `recording_revision_id` sent, no `allow_untested_recording`); `test_untested_draft_saves_and_runs_without_confirmation` (draft stays `draft`, `_build_job` runs it, enabling a daily schedule saves) |
| OT-04 | Edit recorded steps in the editor without **Save draft**, return to Edit Flow and **Save**. | `Flow not saved: Save the recording draft before saving this Flow.` beside Save; form and draft preserved. This is the only remaining stop and it concerns unsaved edits, not testing. | automated `tests/test_flow_save_without_test.mjs` |
| OT-05 | Save a Flow whose selected recording has no download step. | 422 with `This recording cannot run yet: ...`; the Flow name, active pointer and revision status are unchanged (transaction rolled back). | automated `test_save_rejects_non_runnable_recording_and_rolls_back` |
| OT-06 | **Test recording** on the active untested draft; let the test fail. | The test runs on that draft; a failure leaves it `draft` and still active; the Flow still runs. Retesting a `validated` version still uses a new revision and cannot invalidate the active evidence. | automated `test_untested_draft_saves_and_runs_without_confirmation`, `test_pending_snapshot_atomic_apply_and_active_evidence` |
| OT-07 | **Choose from template**, apply a copy, activate it through `POST /api/flows/{id}/recordings/revisions/{revision}/activate` (Saved versions) without testing. | The copy becomes the active recording (`draft` status, `tested` False); an unknown revision id answers 404 `Recording revision not found.`; template notes read `Testing the copy is optional; save the Flow to use it.` | automated `test_copy_carries_full_definition_without_evidence_and_is_independent` |
| OT-08 | Open **More → Flow files** and the Flow's `run_flow.py` for an untested and for a settings-changed Flow. | The header reads `# Recording revision N has not been tested; check the first run output.` or, after a test with other settings, adds `# That test used different Flow settings or transformation bytes; check the first run output.`; a Flow without any saved recording still gets a draft script. | automated `tests/test_flow_portable_script.py`, `tests/test_flow_handover.py`, `tests/test_flow_standalone.py` |
| OT-09 | Old client behaviour: PUT the Flow with `allow_untested_recording: true`. | 200; the flag is ignored. | automated `test_untested_draft_saves_and_runs_without_confirmation` |
| OT-10 | Browser journey: record, **Test recording**, **Back to Edit Flow**, **Save**. | Unchanged: pending settings kept, tested version applied, run job carries that revision. | automated `test_browser_real_api_save_test_return_apply` (synthetic Chromium) |

## Automated checks

Focused set (checkout-owned virtual environment from `requirements-ci.lock`;
CI runs the same files):

```bash
python -m pytest tests/test_recording_journey.py tests/test_recording_templates.py tests/test_flow_recordings.py tests/test_flow_portable_script.py tests/test_flow_handover.py tests/test_flow_standalone.py -q
node tests/test_flow_save_without_test.mjs
node tests/test_flow_builder_contract.mjs
python -m py_compile app/flow_recordings.py app/routers/flows.py app/routers/flow_recordings.py app/flow_portable.py
for f in app/static/*.js; do node --check "$f"; done
```

Equivalent work-PC command:

```powershell
.\tools\check.ps1 -Mode Verify -TestPath tests/test_recording_journey.py,tests/test_recording_templates.py,tests/test_flow_recordings.py,tests/test_flow_portable_script.py,tests/test_flow_handover.py,tests/test_flow_standalone.py -SyntaxPath app/flow_recordings.py,app/routers/flows.py,app/routers/flow_recordings.py,app/flow_portable.py
```

Final-head CI `Merge ready` is the authoritative full Python and frontend
regression; it is not repeated locally.

## Acceptance and cleanup

Accept when the focused set, the Node contracts and final-head CI pass.
Owner acceptance additionally means OT-01 and OT-03 on the BI desktop: one
settings change on a tested Flow and one untested new recording both save
without a dialog or a 409 and run. Cleanup: disable any test schedule.
Rollback: reverting restores the evidence gate, the confirmation dialog and
the `allow_untested_recording` waiver; no data format changed, and the added
`recording.tested` job key is ignored by older code.
