# Date range controls in recorded Flows: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: [PR #136](https://github.com/datap0nd/data_governance/pull/136).
- Evidence cutoff (UTC): 2026-09-17 12:17 (local verification of the working tree committed unchanged as `a4175f1`; final-head CI has not finished).
- Tested code revision: the working tree committed unchanged as `a4175f1dabadb99abac1f3c219aaa5f378583bd1` on top of `origin/main` `f777f65` (PR #135). The verifier's `result.json` files record revision `e00bf87` plus uncommitted changes because the tree was built on the branch that became `f777f65`; the application content is identical. This PR-link update is a documentation-only commit on top of that head.
- Environment: Linux container, Python 3.13.12 in the checkout-owned `.venv` (`requirements-ci.lock`); Node v22.22.2; Playwright 1.62.0 with the bundled Chromium `chromium-1194` through the shared Chrome-first launch helper. The container has neither the Chrome channel nor the `chromium_headless_shell-1234` build Playwright's default launch expects, so tests that launch a browser without that helper fail here and rest on CI.
- Overall finding: local synthetic checks PASS for the change. New suites 40 passed, 0 failed after the Codex fixes (36 before them); editor companions with the PR #135 Codex fix 18 passed, 0 failed; companions 195 passed, 1 skipped, 18 browser-launch failures (environment) in one run and 104 passed, 9 failures (8 browser-launch, 1 fixture omission fixed and retested) in the other; the Node model test and syntax checks pass. Final-head CI is pending. No live, work-PC or portal check was requested or performed.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| C-01 to C-08, P-01 to P-11, E-01 to E-04 (new suites) plus syntax | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_recording_sliders.py --test tests/test_recording_slider_editor.py --syntax app/flow_range_slider.py --syntax app/flow_recording.py --syntax app/flow_recording_runtime.py --syntax app/static/flow_recording_editor.js --syntax app/static/flow_recording_model.js` | Final tree; Linux, Python 3.13.12, checkout-owned `.venv`; run root `.test-runs/20260917T121414261Z-5373-7ef64eb9` | PASS: 36 passed, 0 skipped, 0 failed; 16.1 s. The editor walk ran on bundled Chromium (1.8 s); all five syntax targets clean. | `.test-runs/20260917T121414261Z-5373-7ef64eb9/result.json` |
| P-12, C-05 (existing gating), portable and standalone companions | Plan's first companion command (`tests/test_flows.py` slider cases, `tests/test_flow_worker_discovery.py`, `tests/test_recording_ranges.py`, `tests/test_recording_v2_model.py`, `tests/test_flow_portable_script.py`, `tests/test_flow_standalone.py`, worker/router syntax) | Same tree; run root `.test-runs/20260917T121433067Z-6277-2ddf3754` | 195 passed, 1 skipped, 18 failed, 55.7 s. Every failure is in `tests/test_recording_ranges.py` and is `BrowserType.launch: Executable doesn't exist at /opt/pw-browsers/chromium_headless_shell-1234/...` before any test body: that file launches Chromium without the shared helper and cannot run in this container on any revision. All slider, discovery, gating, portable and standalone cases passed. | `.test-runs/20260917T121433067Z-6277-2ddf3754/result.json` |
| Recording API, worker claims and editor companions | Plan's second companion command (`tests/test_flow_recordings.py`, `tests/test_recording_controls.py`, `tests/test_recording_startup.py`, `tests/test_sql_ownership.py`, `tests/test_recording_visual_editor.py`, `tests/test_recording_pacing_probes.py`) | Same tree before the fixture fix below; run root `.test-runs/20260917T121434195Z-6292-f48c6c17` | 104 passed, 9 failed, 87.0 s. Eight failures are `Chromium distribution 'chrome'/'msedge' is not found` launches (the three GSCM review UI cases, the two real-download portable cases and the mislabeled-workbook case in `test_flow_recordings.py`; both browser cases in `test_recording_controls.py`). One was real: `test_recording_uses_capacity_and_requires_capable_visible_worker` registers its synthetic recorder through a helper whose capability list did not carry `recorded_flows_v4`, so it could no longer claim the version-4 recorder job. | `.test-runs/20260917T121434195Z-6292-f48c6c17/result.json` |
| Retest of the fixture fix (C-05 companions) | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_recordings.py::test_recording_uses_capacity_and_requires_capable_visible_worker --test tests/test_flow_recordings.py::test_cancellation_preserves_catalog_status_and_fences_late_worker --test tests/test_recording_v2_model.py::test_v2_only_worker_cannot_claim_v3_work` | Final tree; run root `.test-runs/20260917T121654500Z-7845-324f63b2` | PASS: 4 passed; 4.3 s. | `.test-runs/20260917T121654500Z-7845-324f63b2/result.json` |
| C-09, P-13 to P-15 with the whole new suites after the Codex fixes on this PR | Plan's first verifier command (new suites plus syntax) | Tree with both Codex fixes, committed as the third code commit of this PR; run root `.test-runs/20260917T122901649Z-14840-8a78b384` | PASS: 40 passed, 0 skipped, 0 failed; 22.3 s. Sunday-start resolution, the 120 ms slow control, the declared maximum and the never-settling control all behave as planned; the editor walk now also carries the week convention through a kind change. | `.test-runs/20260917T122901649Z-14840-8a78b384/result.json` |
| P-12, C-05 and the recording, portable and standalone companions after the Codex fixes | Plan's first companion command with `tests/test_flow_recordings.py` added and the ranges file omitted (it cannot launch here) | Same tree as the run above; run root `.test-runs/20260917T122926780Z-15958-19e5fe2e` | 238 passed, 1 skipped, 6 failed, 80.6 s. The six failures are the same `Chromium distribution 'chrome'/'msedge' is not found` launches in `tests/test_flow_recordings.py` as before; every catalog slider, discovery, gating, portable, standalone and API case passed, including the recorder-capability case fixed earlier. | `.test-runs/20260917T122926780Z-15958-19e5fe2e/result.json` |
| E-07 and E-06 (editor companions) | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_recording_visual_editor.py --syntax app/static/flow_recording_editor.js` | Tree with the Codex fix, committed as the second code commit of this PR; run root `.test-runs/20260917T122427740Z-13488-0f93357f` | PASS: 18 passed, 0 failed; 28.1 s. The new `test_fixed_date_and_entered_value_stay_in_sync` and the extended duplicate case save matching `args` and fixed parameter values in both edit directions; a portal-default parameter gains no value. | `.test-runs/20260917T122427740Z-13488-0f93357f/result.json` |
| E-05 and script syntax | `cd /home/user/data_governance && node tests/test_recording_visual_model.mjs && for f in app/static/*.js app/static/recording-preview/slider.js; do node --check "$f"; done; git diff --check` | Final tree; Node v22.22.2 | PASS: prints the three "tests passed" lines including "Date range control model tests passed"; every `node --check` clean; `git diff --check` clean. | Terminal output, 12:17 UTC |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression (all suites; Chrome-channel cases in `tests/test_recording_range_editor.py`, `tests/test_optional_recording_editor.py`, `tests/test_recording_journey.py`, `tests/test_templates_refresh_preview.py`, the GSCM review UI and real-download cases; `tests/test_recording_ranges.py` browser cases; Windows contracts) | NOT RUN | Final-head CI has not finished at this cutoff; the Chrome channel and the expected headless-shell build are not installed in this container. | Wait for the required `Merge ready` check on the final head; record its run URL and tested head SHA in the PR testing section before the head-pinned merge. |

## Usability evidence

`tests/test_recording_slider_editor.py::test_advanced_setting_converts_one_recorded_handle_click_to_a_date_range_control`
walked the fictional preview `app/static/recording-preview/slider.html` (E-01
to E-04): the Advanced switch turns `Click “Week end”` into "Set week range"
with a **Date range** badge and disables the range-step switch; Options show
Control values, Element box and the Start (portal default) and End (newest
selectable week, weeks to add 0) blocks with their parameter names; a
malformed week (`2026-W1`) is refused beside the actions and `2026-W01`
accepted; the end offset, element box, Dates control values (with **Week
starts on** Sunday), the `YYYYWW` format (converting the fixed value to
`202601`) and the End rename to `last_week` apply; a duplicate name is refused
as not unique; unchecking restores the recorded click and re-enables the range
switch; the saved definition validates at version 4 with the entered
parameters; the page-open step shows the switch disabled with "A date range
control needs an element target."; at 390×844 nothing overflows. The
implementer reviewed screenshots of the Advanced section and the configured
control at 1400×1000 and 390×844 (parameter blocks stack cleanly under the
phone width); they are scratch files and are not committed. The journey
mirrors the approved range-step journey and follows the approach the owner
specified in the task, so no further owner pause was taken.

## Findings, limitations and retests

- One defect in the change during implementation, before any commit: the
  editor's slider field bindings read `rangeParams` from the rendering
  function's scope and threw "rangeParams is not defined" when the switch was
  enabled; the editor walk caught it (timeout waiting for the fixed-week
  input), the bindings now compute the parameters themselves, and the walk
  passed on the final tree.
- One fixture defect from the version bump: synthetic recorder workers in
  the recording tests advertised `recorded_flows_v3` but not `v4`; the
  capability lists now mirror the real worker (v4 added beside v3, as v3 was
  added beside v2), and the existing "v2-only worker cannot claim v3 work"
  case now ends with the v4 grant. No assertion was removed or weakened.
- The first synthetic slider page used empty `role=slider` elements, which
  Playwright counts as invisible; the handles now have a size, as real
  handles do. The rule that a control box must expose exactly two visible
  handles is unchanged.
- Environment limits: `tests/test_recording_ranges.py` and the Chrome-channel
  cases cannot launch a browser here (see the executed-checks table); they
  ran unchanged and are covered by CI. The new suites use the shared
  Chrome-first helper so they run in both places.
- Synthetic limits: the slider pages are keyboard-driven fixtures whose only
  state is `aria-valuetext`; ASAP's real Week and Date sliders and any GSCM
  date control need a headed recording on the work PC, which is opt-in and
  was not requested. The catalog method's slider behavior is covered by its
  existing synthetic cases through the worker's aliases.

- Codex review of PR #135 (delivered after that PR merged) found a real
  defect in the step editor, older than the Duplicate control but made
  visible by it: editing **Entered value** on a step whose date behavior is
  *Fixed date* changed only the recorded argument, while playback replays the
  parameter's fixed value, so the draft showed one date and ran another. The
  editor now keeps the two in step in both directions (entered value to fixed
  value, fixed value to entered value, including the visible fields), a
  portal-default or calculated parameter is untouched, and the editor tests
  cover both directions and the duplicated copy (E-07). The first version of
  the new assertions matched the "Fixed date" label against the Date behavior
  select as well and expected the unsynchronized display; both were test
  mistakes corrected before the passing run above.

- Codex review of this PR (two findings, both real, fixed in the third
  code commit): *Current week* and *Previous week* were resolved as ISO
  (Monday-start) weeks regardless of the control's convention, so on a Sunday
  an ASAP control would have selected the previous Sunday-to-Saturday week;
  resolution now uses the step's **Week starts on**, which the editor offers
  for week-number controls as well (C-09). And the newest selectable week was
  read immediately after the End key, so a control that publishes its value
  asynchronously could hand back the old upper value, which the later
  exact-range check would then confirm as the target; the probe now waits for
  the value to hold still, presses End again and requires the same value, and
  compares a declared `aria-valuemax` (P-13 to P-15).

## Merge evidence

Final CI is pending. The PR carries the final `Merge ready` run URL, tested
head SHA and result in its testing section before the head-pinned merge; the
PR merge record supplies the merge SHA.
