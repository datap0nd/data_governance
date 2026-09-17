# Date range controls in recorded Flows: test plan

- Change/PR: PR link recorded in the report and the PR once opened. Scope: recording definition version 4 with the `set_range` step (a recorded click on a two-handle date or week slider becomes a step that moves both handles by keyboard and proves the values) and week-unit date parameters (portal default, fixed week, newest selectable week, current and previous week, with a weeks-to-add offset and `YYYY-Www`/`YYYYWW` formats); the shared keyboard slider driver `app/flow_range_slider.py` extracted from the ASAP catalog method; the editor journey (**Advanced → This is a date range control**, Options block for control values, week convention, element box and the start/end parameters); worker capability `recorded_flows_v4` and claim gating; docs and a fictional preview.
- Baseline: `origin/main` `f777f65` (PR #135 merged).
- Related report: [test-report.md](test-report.md).
- Environments: isolated Linux Python 3.13 fixtures (checkout-owned `.venv`, `requirements-ci.lock`), Node model test, production editor scripts driven through Playwright with fictional data, synthetic keyboard-driven slider pages in Chromium; final PR CI supplies the full Python regression with the Chrome channel. No live, work-PC or portal environment is in scope: the ASAP Week and Date sliders and any GSCM date control need a headed recording on the work PC, which the owner requests separately.

## Prerequisites and test data

Run `python tools/check.py setup` once, then `python tools/check.py preflight`.
Every fixture is synthetic. `tests/test_recording_sliders.py` builds a page
with two `role=slider` handles whose only state is `aria-valuetext`, moved by
ArrowLeft/ArrowRight/Home/End with the lower handle bounded by the upper one,
for week values (`202501` to `202633`) and for daily dates. The recording API
cases use the disposable SQLite database of `test_flows.flow_db`. The editor
walk serves `app/` over a local HTTP server and opens
`app/static/recording-preview/slider.html` (one recorded click on a week
slider handle, a fictional API layer).

## Cases

### Contract and resolution

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| C-01 | Validate a version-4 definition with a `set_range` step and its start/end week parameters; the same at version 3; `import_codegen` of the standard fixture. | Accepted at version 4; rejected at version 3 with "require recording version 4"; new recordings import as version 4. |
| C-02 | Mutate the definition: missing end parameter, two starts, a day-unit parameter on the control, unknown week calculation, offsets of 521 or "2", fixed `2025-W53` (no such week), fixed `202601` under the `YYYY-Www` format, a day format on a week parameter, unit `month`, control kind `month`, week start `friday`, seven parent levels, an empty box locator, a week parameter on an ordinary click. | Each is rejected with the named message (exactly one start and one end; needs week parameters; Unsupported week calculation; within ten years; does not exist; YYYY-Www; Unsupported week format; Unsupported date parameter unit; week numbers or dates; Sunday or Monday; 1–6 parent levels; element box; belong to a date range control). |
| C-03 | Existing day parameters on the codegen fixture; then mark one as unit `week`. | Unchanged acceptance; a week unit on a fill step is rejected. |
| C-04 | `save_revision` with a version-2 submission carrying a `set_range` step. | Stored at version 4 and valid. |
| C-05 | Queue a run whose definition is version 4; register a worker with `recorded_flows_v1..v3` only, then add `recorded_flows_v4`. | No claim without v4; claimed with it. The existing v2/v3 gating case now ends with v4. |
| C-06 | `resolve_parameters` with `current_week` minus three weeks and `previous_week` at 2026-01-01 00:30 UTC; `current_week` at Sunday 2025-12-28 20:30 UTC. | `2025-W50` and `2025-W52`; the Dubai Monday gives `2026-W01`; `latest_selectable` resolves to nothing at queue time. |
| C-07 | Fixed and override values in `YYYYWW`, an override in the wrong format for either format; `parse_parameter_value` and `format_week`. | `202601`/`202610` accepted; wrong formats rejected naming the expected one; a week parses to its Monday. |
| C-08 | `week_of` and `week_bounds` for Sunday and Monday weeks; `week_dates("2026-W22")`. | Sunday 24 May 2026 belongs to the ISO week of 25 May under the Sunday convention and to 18 May under Monday; bounds 24–30 May (Sunday) and 25–31 May (Monday); the ASAP helper still yields `20260524`–`20260530`. |

### Playback (synthetic sliders)

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| P-01 | Fixed start `2026-W20`, end newest selectable; handles at `202622`/`202630`. | Handles read `202620`/`202633`; actual parameters `2026-W20`/`2026-W33`; the end is a live value; progress phases read, latest, move. |
| P-02 | Start newest selectable with `-7`, end newest selectable. | Handles `202626`/`202633`; both parameters live. |
| P-03 | Start portal default, end fixed `202630` in `YYYYWW`. | The start handle is untouched and recorded as `2026-W22`; the end reads `202630`. |
| P-04 | Date control with fixed `2026-W22` to `2026-W23`, Sunday and Monday weeks. | Handles `20260524`/`20260606` (Sunday) or `20260525`/`20260607` (Monday); parameters recorded as weeks. |
| P-05 | Date control ending on Tuesday 9 June 2026 with newest selectable weeks. | Fails closed with a mismatch: the newest week's Saturday is beyond the control. |
| P-06 | Fixed `2026-W38` to `2026-W40` on a control ending at `202633`. | Fails with a slider mismatch before any download. |
| P-07 | Fixed `2026-W30` to `2026-W20`. | Rejected as ending before its start; the handles are untouched. |
| P-08 | A box with one handle; a start handle without a readable value under portal default. | "exactly two visible slider handles"; "current start value". |
| P-09 | The progress callback raises at the move phase. | The error propagates; only the newest-week probe moved a handle. |
| P-10 | `acquire` on the synthetic page with the control before a download whose start the callback interrupts. | The control step completes with "Set week range 202620 through 202633 (2026-W20 to 2026-W33)." and `exact_range` confirmation, with latest and handle phases in its diagnostics; handles `202620`/`202633`. |
| P-11 | `flow_portable.execution_sources()`. | The runtime bundle carries `_set_slider_range` and the new `flow_range_slider` module with `set_range_values` and `find_handles`. |
| P-12 | Existing catalog slider cases in `tests/test_flows.py` (week dates, discovery bounds and restore, period slider, collapsed range order, visible-label fallback, manual week inference, no date slider for Period) and `tests/test_flow_worker_discovery.py`. | Unchanged results through the worker's aliases and wrapper. |

### Editor journey (fictional preview)

| ID | Actions | Expected result / evidence |
| --- | --- | --- |
| E-01 | Open `slider.html`, select `Click “Week end”`, open Advanced, enable **This is a date range control**. | The card reads "Set week range" with a **Date range** badge; **This is a range step** becomes disabled; Options show Control values, Element box, Start (portal default) and End (newest selectable week, weeks to add 0) with parameter names `start`/`end`. |
| E-02 | Start behavior → Fixed week; enter `2026-W1` then `2026-W01`; end weeks to add `-1`; Element box 2; Control values → Dates; Start format → `YYYYWW`; rename End to `last_week`; rename Start to `last_week`. | The malformed week is refused beside the actions and the valid one accepted; Week starts on appears as Sunday; the fixed value converts to `202601`; the rename applies; the duplicate name is refused as not unique. |
| E-03 | Uncheck the control; check it again; set a fixed start and end offset; Save draft. | The recorded click returns (range step enabled again); defaults return; the saved definition validates at version 4 with `goto`, `set_range`, `download`, a week-kind contract with one parent level and the recorded click as source, and the two parameters exactly as entered. |
| E-04 | Select the page-open step; then the control step at 390×844. | The checkbox is disabled with "A date range control needs an element target."; no horizontal overflow. |
| E-05 | Model (`tests/test_recording_visual_model.mjs`): candidates, make/restore, defaults, describe, required version, ancestor levels, unique parameter names, duplicate of a control step, rename with references, week text conversion and validation. | All assertions pass, including that the duplicate gets `start_2`/`end_2` on the copy and the rename follows `not_after` and period checks. |
| E-06 | Existing editor cases (`tests/test_recording_visual_editor.py`) and the range editor preview. | Unchanged. |

## Automated checks

From the checkout with its owned `.venv`:

```bash
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_recording_sliders.py --test tests/test_recording_slider_editor.py --syntax app/flow_range_slider.py --syntax app/flow_recording.py --syntax app/flow_recording_runtime.py --syntax app/static/flow_recording_editor.js --syntax app/static/flow_recording_model.js
```

Companions (existing behavior the change touches):

```bash
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flows.py::test_asap_week_dates_follow_live_sunday_to_saturday_calendar --test tests/test_flows.py::test_asap_week_slider_discovery_expands_bounds_and_restores_handles --test tests/test_flows.py::test_asap_period_slider_has_no_coupled_date_range --test tests/test_flows.py::test_asap_collapsed_range_advances_upper_handle_first --test tests/test_flows.py::test_asap_range_uses_visible_labels_when_handles_have_no_values --test tests/test_flows.py::test_asap_manual_week_definition_infers_visible_range_slider --test tests/test_flows.py::test_asap_period_range_does_not_drive_a_nonexistent_date_slider --test tests/test_flow_worker_discovery.py --test tests/test_recording_ranges.py --test tests/test_recording_v2_model.py --test tests/test_flow_portable_script.py --test tests/test_flow_standalone.py --syntax app/flow_worker.py --syntax app/routers/flows.py --syntax app/routers/flow_recordings.py --syntax app/flow_recordings.py --syntax app/flow_recording_diagnostics.py
cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_recordings.py --test tests/test_recording_controls.py --test tests/test_recording_startup.py --test tests/test_sql_ownership.py --test tests/test_recording_visual_editor.py --test tests/test_recording_pacing_probes.py
```

The Node model test runs directly, with the remaining syntax and whitespace
checks:

```bash
cd /home/user/data_governance && node tests/test_recording_visual_model.mjs && for f in app/static/*.js app/static/recording-preview/slider.js; do node --check "$f"; done; git diff --check
```

Each verifier invocation writes `.test-runs/<id>/result.json`; cite that path.
Chrome-channel cases (`tests/test_recording_range_editor.py`,
`tests/test_optional_recording_editor.py`, `tests/test_recording_journey.py`,
`tests/test_templates_refresh_preview.py`) and the Windows contracts rest on
final-head CI (`Merge ready`), the authoritative full regression.

## Usability evidence

The Playwright walk (E-01 to E-04) on the fictional preview is the
changed-control evidence, including the refused malformed week, the refused
duplicate name, the restore on uncheck and the disabled state with its reason.
Screenshots at 1400×1000 and 390×844 are reviewed by the implementer and not
committed. The journey mirrors the approved range-step journey (an Advanced
switch on the recorded step, then its contract under Options); the owner
specified the approach in the task, so no further pause was taken.

## Acceptance and cleanup

Accept after the verifier sets, the Node model test and final-head required CI
pass, and the report records actual results. Test databases, run roots and
browser pages are per-run temporary fixtures removed by the verifier on
success. No settings, schedules or existing Flow data are changed.

Deployment: workers must be updated with the app. A worker that does not
advertise `recorded_flows_v4` never claims a version-4 recording, validation
or recorder job, so mixed fleets fail closed rather than replay a control step
they cannot execute. Existing version 1–3 recordings keep running unchanged.

Rollback: revert the PR. Saved version-4 definitions are refused by the older
validator ("Unsupported recording version"), so a Flow saved with a date range
control would need its previous revision reactivated from Saved versions.
