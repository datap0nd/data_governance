# Several schedule triggers per Flow: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: branch `claude/dreamy-dijkstra-8btkqe`; the PR testing section
  links this report.
- Evidence cutoff (UTC): 2026-10-12 11:10.
- Tested code revision: implementation commit
  `6409746ef71c57c243b669694be15ecb1c2cdd63`. The follow-up commit with this
  report changes only documentation, evidence screenshots and the preview
  test's list step (it now expands the Outlook group before checking the
  label); that test was rerun on the follow-up working tree.
- Environment: Linux container, checkout-owned CPython 3.13.16 `.venv` with
  the locked CI dependencies, Node.js 22.22.0, Playwright 1.62.0 with the
  preinstalled Chromium 141.0.7390.37 (headless shell). No Google Chrome or
  Microsoft Edge is installed.
- Overall finding: every planned automated and synthetic browser case passes.
  Six existing recording tests that need a Chrome or Edge browser channel
  could not launch here; final-head CI runs them.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-01–T-10, T-12, U-01–U-05 | `python tools/check.py verify --test tests/test_flow_triggers.py --test tests/test_flow_triggers_preview.py --test tests/test_flows.py --test tests/test_freshness_inheritance.py --test tests/test_flow_handover.py --test tests/test_ai_operations.py --test tests/test_dubai_time_policy.py --test tests/test_flow_standalone.py --test tests/test_flow_recordings.py --syntax app/freshness.py --syntax app/routers/flows.py --syntax app/database.py --syntax app/flow_handover.py --syntax app/ai/operations_tools.py --syntax app/static/app.js --syntax app/static/recording-preview/flow-triggers.js --syntax tests/test_flow_triggers.mjs` | `6409746`, container above | 337 tests: 331 passed, 6 failed, 0 skipped, 162.8 s; all syntax checks clean. Every failure is a browser launch error in `test_flow_recordings.py` (see Findings). All new trigger cases and the Flows, freshness, handover, AI evidence, Dubai time and standalone suites passed. | `.test-runs/20261012T110304992Z-3053-92ea836f/result.json` (source fingerprint `a099ed4d…`) |
| U-04 retest | `PREVIEW_EVIDENCE_DIR=docs/testing/releases/2026-10-12-flow-multiple-triggers/evidence .venv/bin/python -m pytest tests/test_flow_triggers_preview.py` | follow-up working tree, same container | PASS: 4 tests, 6.3 s, after expanding the Outlook group so the screenshot shows the label | [evidence/](evidence/) screenshots |
| T-11 | `node tests/test_flow_triggers.mjs` | `6409746` | PASS | Local command output: "flow trigger tests passed" |
| T-12 (frontend) | `for f in tests/test_*.mjs; do node "$f"; done`, excluding `test_gemini_extension.mjs` | `6409746` | PASS: 25 of 25 files | Local command output |
| T-12 (Gemini) | `node tests/test_gemini_extension.mjs` | `6409746` and `main` `97f0abf` | Not counted: fails identically on `main` with `ERR_MODULE_NOT_FOUND` because the extension's npm packages are not installed here; this change does not touch it. Final-head CI installs them. | Local command output |

During implementation, one affected-suite run (`pytest` on Flows, freshness
inheritance, handover, AI operations and Dubai time policy) reported 258
passed and 1 failed: `test_owner_schedule_and_shared_changes_refresh_without_export`
compared the handover schedule as an exact dictionary, which now also carries
`additional_triggers: []`. The expectation was updated to the new intended
shape (non-empty triggers are asserted by T-10), and the file then passed
14/14; the verifier run above includes it.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| T-12 (Chrome/Edge recording cases) | BLOCKED | `test_flow_recordings.py`: `test_portable_unchecked_recording_rejects_binary_mislabeled_as_xlsx[report_server0]`, `test_same_portable_pipeline_runs_real_download_on_both_browsers[chrome]`, `[msedge]`, `test_ui_can_review_gscm_without_code_and_keeps_portal_default_date`, `test_ui_repairs_a_recycled_gscm_row_as_an_exact_bookmark_target`, `test_ui_prefills_the_recorded_bookmark_text_when_choosing_the_favorite_target` fail at browser launch: "Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome" (and the Edge equivalent). | Final-head CI, which installs Chrome and Edge |
| Final-head CI | NOT RUN | Pending at this cutoff | Record the `Merge ready` run URL and tested head SHA in the PR testing section, then merge pinned to that head |

## Usability evidence

Synthetic browser evidence only, from the fictional preview
`app/static/recording-preview/flow-triggers.html` rendered by production
`app.js` with an in-memory API (no worker, Outlook, Python or SQL access).
Walked controls: Add trigger (copies the last row one hour later and focuses
its time), per-row Daily/Weekly/Monthly switching, Remove with renumbering and
focus on the next Remove button, the 24-trigger limit and its recovery,
Manual hiding the block, a new Flow, the Python help text, the save failure
with focus on the named trigger and every value kept, the successful retry,
the Flows list label, and the 390×844 layout with no horizontal overflow.

| Screen | Evidence |
| --- | --- |
| Saved Outlook Flow with triggers 2 and 3 | [flow-triggers-saved.png](evidence/flow-triggers-saved.png) |
| Added monthly trigger, trigger 3 removed and renumbered | [flow-triggers-edited.png](evidence/flow-triggers-edited.png) |
| Validation error beside the actions, Trigger 3's time focused | [flow-triggers-validation-error.png](evidence/flow-triggers-validation-error.png) |
| New Outlook Flow: Daily 07:30 plus Trigger 2 at 08:30 | [flow-triggers-new-flow.png](evidence/flow-triggers-new-flow.png) |
| Flows list: "Mondays, …, Thursdays and Fridays + 2 more triggers" | [flow-triggers-list.png](evidence/flow-triggers-list.png) |
| 390×844 with a monthly Trigger 4 | [flow-triggers-narrow.png](evidence/flow-triggers-narrow.png) |

The journey keeps the existing schedule step and its required choices; More
triggers is an optional addition inside that step, so no owner review pause
applied under [DESIGN.md](../../../../DESIGN.md).

## Findings, limitations and retests

- The six Chrome/Edge recording failures above are environmental and occur
  before any application code runs. The remaining 331 cases, including the
  non-browser recording tests that re-validate the Flow form, passed.
- Scheduler, activation and health behavior was verified against a pinned
  clock and a disposable SQLite database, not a running worker. No live
  Outlook, portal or work-PC check was requested or run.
- The verifier kept its scratch directory because the run failed; it holds
  only fixture data.
- Existing Starlette/anyio deprecation warnings (2) were unchanged.

## Merge evidence

Final-head CI is pending at this cutoff. The PR testing section records the
final `Merge ready` run URL, the tested head SHA and its result before the
head-pinned merge; the PR's merge record supplies the merge SHA. A merge is
not a deployment: the running app picks this up when it is updated from
`main`.
