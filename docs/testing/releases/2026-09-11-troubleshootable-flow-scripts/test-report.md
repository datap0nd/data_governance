# Troubleshootable Flow scripts: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: troubleshootable, always-current per-Flow `run_flow.py` (PR link recorded in the merge evidence below).
- Evidence cutoff (UTC): 2026-09-11T08:05Z, before final-head GitHub CI.
- Tested code revision: baseline `494cc156930941738c1eb40ab351327e2c62e2dd` plus the uncommitted implementation, test and documentation diff of this PR (all files later committed unchanged, apart from this report).
- Environment: Linux container (cloud session), Python 3.13.12 venv from `requirements-ci.lock` (pytest 9.1.1, Playwright 1.62.0), bundled Chromium 141.0.7390.37 only; no `chrome`/`msedge` channel, no Windows, no live portal, Outlook, SQL or work PC.
- Overall finding: synthetic checks pass. Every save and every application update now regenerates `run_flow.py`; a hand-edited script is archived under `Scripts/versions` and refreshed instead of blocking; the script is verbatim Python with real line numbers in tracebacks, echoed progress and full tracebacks on failure; the execution-core fingerprint no longer drafts or blocks recorded Flows. The end-to-end browser pipeline case could not run locally for want of the `chrome` channel and relies on final-head CI.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Baseline of existing generator tests | `python -m pytest -p no:cacheprovider tests/test_flow_standalone.py tests/test_flow_handover.py tests/test_flow_recordings.py::test_portable_script_dry_run_has_no_metronome_import_or_adjacent_configuration tests/test_view_refresh.py::test_scripts_print_the_frozen_list_and_refuse_blocked_plans tests/test_view_refresh.py::test_recorded_script_dry_run_lists_refresh_views_and_no_sql_hides_them -q -x` with the new generator but the old test assertions | Implementation diff on `494cc156`, Linux venv | 5 passed, 1 failed in 13.09s: only `test_bundles_reject_credentials_and_foreign_launcher` failed, at its former "must raise on a hand-edited script" assertion (`DID NOT RAISE`), the behaviour this change replaces. | Local run output |
| S-01…S-07, S-09, S-10 first run | `python -m pytest -p no:cacheprovider tests/test_flow_portable_script.py tests/test_flow_standalone.py tests/test_flow_handover.py tests/test_flow_recordings.py::test_portable_script_dry_run_has_no_metronome_import_or_adjacent_configuration -q` | Implementation + updated tests, Linux venv | 28 passed, 3 failed in 53.40s. One failure was a wrong stage name in the new echo assertion (`[succeeded]` instead of the actual `[complete]`); the two fingerprint failures (`stale` status, unstable `snapshot_hash` across hash seeds) occurred while `docs/flow_standalone.md`, which is part of the fingerprint, was being edited during the run. | Local run output |
| Retest of the three failures | `python -m pytest -p no:cacheprovider tests/test_flow_handover.py::test_modified_script_archived_then_refreshed_and_missing_script_recreated tests/test_flow_handover.py::test_snapshot_fingerprint_is_stable_across_python_hash_seeds tests/test_flow_portable_script.py::test_failure_prints_traceback_and_progress_is_echoed_unless_quiet -q` | Same code, corrected assertion, documentation edits finished | PASS: 3 passed in 11.59s. | Local run output |
| S-01…S-10 full focused selection | The exact Linux command in the plan (eleven selectors) with `DG_DB_PATH`, `DG_TEST_RUN_ROOT`, `DG_BROWSER_PROFILE_ROOT` under a scratch directory | Final implementation diff, Linux venv | 39 passed, 2 failed in 72.28s. Both failures are `test_recording_v2_pipeline.py::test_v2_wait_multiple_outputs_and_transform_match_portable[False|True]` with `Chromium distribution 'chrome' is not found`; the fixture's saved browser channel is `chrome`, which this container cannot provide. All other selectors passed, including the isolated `python -I` subprocess runs, traceback line mapping, edit archiving, engine-change regeneration and the Outlook `runpy` fall-through. | JUnit `focused.xml` in the session scratch directory (not committed) |
| Syntax and hygiene | `python -m py_compile app/flow_portable.py app/flow_handover.py app/flow_recordings.py app/flow_worker.py app/flow_recording_runtime.py app/flow_standalone.py app/routers/flows.py tests/test_flow_portable_script.py tests/test_flow_standalone.py tests/test_flow_handover.py tests/test_flow_recordings.py`; `node --check app/static/app.js`; `git diff --check` | Final diff | All passed. | Local run output |
| Manual sanity | Generated a local-file Flow in a scratch database and inspected `Scripts/run_flow.py` | Final diff | 14,029 lines / 641 KB: v2 header with HOW TO USE / HOW TO TROUBLESHOOT AND PORT A FIX, `FLOW` as indented JSON, the loader, then `# ==== module: config ====` and the remaining sections as plain Python. | Scratch inspection, not committed |
| U-01 | Reviewed `app/static/recording-preview/files.html` with the new **run_flow.py was edited by hand** scenario: Flow files shows the `modified` wording, Save Flow shows the archived-copy confirmation, Flow files then reads up to date. | Final diff | Wording-only change, no new controls or layout change; the preview mirrors the exact strings in `app/routers/flows.py` and `app/static/app.js`. No browser screenshot was captured in this container. | Preview source |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| S-08 (`test_recording_v2_pipeline.py::test_v2_wait_multiple_outputs_and_transform_match_portable`) | BLOCKED locally | The fixture requires Playwright's `chrome` channel; `playwright install chrome` did not complete in this container. | Covered by required final-head CI, which installs `chromium` and `chrome` and runs the whole `tests/` tree; record its run URL and head SHA in the PR before merging. |

## Findings, limitations and retests

- The two fingerprint failures in the first run were caused by editing `docs/flow_standalone.md` (embedded as `Scripts/README.md` and part of `snapshot_hash`) while pytest was running; the retest after the edits passed and the full focused selection passed those cases again.
- Retaining comments in the embedded execution source grows a generated script from roughly 540 KB to roughly 640 KB. Every managed Flow regenerates once at the first startup after deployment, writing one new `Scripts/versions/run_flow-<hash>.py` each.
- The old `attach_job`/worker/generator gate on the execution-core fingerprint is removed by owner decision. The worker-side validation check (`same execution version`) and the server-side validation-result check remain and are covered by S-09.
- Synthetic fixtures do not prove corporate SSO, Outlook or SQL access for a real Flow; no live checks were requested.

## Merge evidence

Final-head CI: PENDING at this cutoff. Before the head-pinned merge, the PR testing section must record the `Merge ready` run URL and the exact tested head SHA; that PR supplies the merge record.
