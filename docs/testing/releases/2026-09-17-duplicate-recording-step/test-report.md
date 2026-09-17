# Duplicate step in the recording editor: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: [PR #135](https://github.com/datap0nd/data_governance/pull/135).
- Evidence cutoff (UTC): 2026-09-17 11:49 (local verification of the working tree committed unchanged as `2b3a43b`; final-head CI has not finished).
- Tested code revision: the working tree committed unchanged as `2b3a43b7d3ac90bb7501fa6e4fe60e0b01479049` on top of `origin/main` `bc3418c` (the verifier's `result.json` therefore records revision `bc3418c` plus uncommitted changes). This PR-link update is a documentation-only commit on top of that head.
- Environment: Linux container, Python 3.13.12 in the checkout-owned `.venv` (`requirements-ci.lock`); Node v22.22.2 for the model test; Playwright 1.62.0 with the bundled Chromium (no Chrome channel, so the editor fixture used its Chromium fallback).
- Overall finding: local synthetic checks PASS. Verifier set 17 passed, 0 skipped, 0 failed; the Node model test and JavaScript syntax checks pass. Final-head CI is pending. No live, work-PC or portal check was requested or performed.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| E-01 to E-05 (verifier set) plus syntax | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_recording_visual_editor.py --syntax app/static/flow_recording_model.js --syntax app/static/flow_recording_editor.js --syntax tests/test_recording_visual_model.mjs` | Final working tree on `bc3418c`; Linux, Python 3.13.12, checkout-owned `.venv`; isolated run root `.test-runs/20260917T114816051Z-2326-1badbd46` | PASS: 17 passed, 0 skipped, 0 failed; 27.0 s pytest. `test_duplicate_step_copies_it_after_itself_with_fresh_ids_and_selects_the_copy` executed (0.48 s) through the Chromium fallback; all three syntax targets clean. | `.test-runs/20260917T114816051Z-2326-1badbd46/result.json` |
| D-01 to D-06 | `cd /home/user/data_governance && node tests/test_recording_visual_model.mjs` | Same tree; Node v22.22.2 | PASS: prints "Visual recording model tests passed" and "Duplicate step model tests passed". | Terminal output, 11:48 UTC |
| Script syntax and whitespace | `cd /home/user/data_governance && for f in app/static/*.js; do node --check "$f"; done; git diff --check` | Same tree | PASS: every `node --check` clean; `git diff --check` clean. | Terminal output |
| Earlier run on the same change before the copy-suffix fix | Plan's verifier command (editor tests only) | Working tree before the final model change | PASS: 17 passed, 0 failed (28.2 s); kept for completeness, superseded by the final-tree run above. | `.test-runs/20260917T114607573Z-906-c0d83e55/result.json` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression (all suites, Chrome-channel editor cases in `tests/test_optional_recording_editor.py`, `tests/test_flow_recordings.py`, `tests/test_recording_journey.py`, Windows contracts) | NOT RUN | Final-head CI has not finished at this cutoff; the Chrome channel is not installed in this container. | Wait for the required `Merge ready` check on the final head; record its run URL and tested head SHA in the PR testing section before the head-pinned merge. |

## Usability evidence

`tests/test_recording_visual_editor.py::test_duplicate_step_copies_it_after_itself_with_fresh_ids_and_selects_the_copy`
walked the changed control (E-01 to E-03): **Duplicate** sits between the
move arrows and Remove in the selected step's heading; choosing it inserts the
copy directly under its source, selects it, shows "Step 4" with the same
title, value, "Fixed date" behavior and the parameter name `start_2`, marks
the draft "Unsaved changes" and keeps focus on Duplicate; editing the copy
leaves the source alone; duplicating the download group carries its Download
badge; Save round-trips both copies with fresh ids that the Python validator
accepts; Undo removes the copy; the page-opening step shows the control
disabled with the explanation "Page open, popup and close steps cannot be
duplicated." The implementer reviewed screenshots of the selected, duplicated
and disabled states at 1400×1000 and 390×844 (heading actions wrap on the
phone width without horizontal overflow); they are scratch files and are not
committed. The control adds no screen or step and changes no decision, so no
owner pause applied.

## Findings, limitations and retests

- One defect found during implementation, before any commit: the first
  version of the fallback id generator skipped `-copy-2` and produced
  `-copy-3` for a second copy of the same step; the Node model test caught it
  (assertion at the second-duplicate case), the counter was corrected, and the
  test passed on the final tree. The Playwright run before that fix
  (`.test-runs/20260917T114607573Z-906-c0d83e55`) did not exercise a second
  copy of the same step, so its pass stands but is superseded.
- The Node model test compares plain JSON copies of objects the model builds
  with literals, because the test evaluates the model inside a `vm` context
  whose object prototype differs from the host's; no application behavior is
  involved.
- The editor fixture in `tests/test_recording_visual_editor.py` now launches
  through the shared `launch` helper of `tests/test_python_scripts_preview.py`
  (Chrome channel first, then Playwright's Chromium, then the browsers folder),
  which is why the file could run here. CI still runs it on Chrome first. No
  assertion was removed or weakened.
- Synthetic limits: the editor runs against a stubbed API and the codegen
  fixture definition; no worker replays a duplicated step in this package.
  A duplicated step is an ordinary step with a fresh id, so replay follows the
  existing recorded-step paths.

## Merge evidence

Final CI is pending. The PR carries the final `Merge ready` run URL, tested
head SHA and result in its testing section before the head-pinned merge; the
PR merge record supplies the merge SHA.
