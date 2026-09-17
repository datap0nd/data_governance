# Duplicate step in the recording editor: test plan

- Change/PR: [PR #135](https://github.com/datap0nd/data_governance/pull/135). Scope: a **Duplicate** control in Review recording, beside the move arrows and Remove, that inserts an independent copy of the selected step right after it so a missing click, value or download can be added from the nearest recorded step.
- Baseline: `origin/main` `bc3418c` (PR #134 merged).
- Related report: [test-report.md](test-report.md).
- Environments: isolated Linux Python 3.13 fixtures (checkout-owned `.venv`, `requirements-ci.lock`), Node model test, production editor scripts driven through Playwright with fictional in-memory data; final PR CI supplies the full Python regression with the Chrome channel. No live, work-PC or portal environment is in scope.

## Prerequisites and test data

Run `python tools/check.py setup` once, then `python tools/check.py preflight`.
Every fixture is synthetic: the browser tests load `flow_recording_model.js`,
`flow_recording_editor.js` and `flow_recordings.js` into a blank page with a
stubbed API and the codegen-derived definition from
`tests/test_flow_recordings.py::definition` (page open, report address, two
date inputs with the `start`/`end` parameters, Generate, a check and one
download group). Nothing touches a worker, a portal or a real database.

## Cases

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| D-01 | Model: `duplicate` on a root value step that owns the fixed `start` parameter; then duplicate the same step again. | The copy sits right after its source with the id `<id>-copy` (then `-copy-2`), the same locator and value; `start_2` (then `start_3`) points at the copy with the source's mode; `start` and `end` are unchanged; the input definition is not mutated. |
| D-02 | Model: duplicate a download event group. | The group and its nested click both get fresh ids; the copy keeps the `download` action and its output settings; `owner` resolves the copied child to the copied group. |
| D-03 | Model: pass an id factory; pass one that returns an id already in use. | The factory's id is used when free; a colliding factory falls back to `<id>-copy`. |
| D-04 | Model: duplicate a group holding two date inputs whose `start` is `not_after: end` and whose output has a period check on `start`. | The copied parameters `start_2`/`end_2` point at the copied inputs, `start_2.not_after` is `end_2`, the copy's period check references `start_2`, and the source's parameters and check are untouched. |
| D-05 | Model: duplicate a version-3 range step, then restore the copy. | The copy's `range.source_step.id` equals the copy's id; `restoreRange` on the copy yields the recorded click under the copy's id. |
| D-06 | Model: `canDuplicate` for page open, popup and close steps; `duplicate` on them and on a nested child id. | False for the three page-lifecycle actions, true for clicks, groups and waits; duplicating them throws "Page open, popup and close steps cannot be duplicated."; a nested id throws the "as a unit" error. |
| E-01 | Editor: select the first date input (Step 3) and choose **Duplicate**. | One more card; the copy is the fourth card with a new id, the source stays third; the copy is selected and the panel reads Step 4 with the same title, entered value, "Fixed date" behavior and parameter name `start_2`; the state reads "Unsaved changes"; focus is on the panel's Duplicate button. |
| E-02 | Editor: change the copy's entered value; select the download group and choose **Duplicate**; **Save draft**. | The source card is unchanged; the copied group carries the Download badge; the saved definition keeps the original ids in order with the two copies inserted after their sources, the copy's new value, `start_2` pointing at the copy, and a copied group whose nested click has a new id and the same output; `validate_definition` accepts it. |
| E-03 | Editor: **Undo**; select the page-opening first step. | The last copy disappears; the Duplicate button is disabled with the title "Page open, popup and close steps cannot be duplicated." |
| E-04 | Existing editor cases (`tests/test_recording_visual_editor.py`): download card and options, retargeting, repair controls, Record again, wait/undo/move/remove, polling, testing state, nested progress, mobile details, compound download, identity, drag and page moves, failed save, late test, history and tab navigation. | Unchanged behavior with the extra heading button present. |
| E-05 | Fixture: run the editor tests on a checkout without the Chrome channel. | The fixture launches the bundled Chromium (as `tests/test_python_scripts_preview.py` already does) instead of failing to launch; CI still runs Chrome first. |

## Automated checks

From the checkout with its owned `.venv`:

```bash
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_recording_visual_editor.py --syntax app/static/flow_recording_model.js --syntax app/static/flow_recording_editor.js --syntax tests/test_recording_visual_model.mjs
```

The Node model test runs directly, with the remaining syntax and whitespace
checks:

```bash
cd /home/user/data_governance && node tests/test_recording_visual_model.mjs && for f in app/static/*.js; do node --check "$f"; done; git diff --check
```

Each verifier invocation writes `.test-runs/<id>/result.json`; cite that path.
`tests/test_optional_recording_editor.py` and the editor cases in
`tests/test_flow_recordings.py` and `tests/test_recording_journey.py` also
render the details panel but require the Chrome channel; final-head CI
(`Merge ready`) runs them and is the authoritative full regression.

## Usability evidence

The Playwright walk (E-01 to E-03) is the changed-control evidence: the new
button beside the existing heading actions, the selected copy with visible
"Unsaved changes" feedback, preserved edits, Undo recovery and the disabled
state with its explanation. Screenshots at 1400×1000 and 390×844 were
reviewed by the implementer and are not committed. The control adds no screen
or step and changes no decision, so no owner pause applies.

## Acceptance and cleanup

Accept after the verifier set, the Node model test and final-head required CI
pass, and the report records actual results. Test databases and run roots are
per-run temporary fixtures removed by the verifier on success. No settings,
schedules or existing Flow data are changed.

Rollback: revert the PR. Definitions saved with duplicated steps are ordinary
recordings (fresh ids, unique parameter names) and remain valid for older
editor and worker code.
