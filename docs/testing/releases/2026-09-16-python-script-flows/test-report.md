# Python-script Flows: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: PR: pending.
- Evidence cutoff (UTC): 2026-09-16 18:38 (local verification of the uncommitted working tree; final-head CI has not run).
- Tested code revision: working tree on top of `abf57073e20bb26b6d166012ec2b6375c42012ee` (`origin/main`) with the uncommitted Python-script Flow change (application, static UI, tests and docs listed in the PR); the committed head SHA is pending.
- Environment: Linux container, Python 3.13.12 in the checkout-owned `.venv` (`requirements-ci.lock`, `tools/check.py` preflight ready), Node v22.22.2 for the contract tests, Playwright 1.62.0 with the installed `chromium_headless_shell-1194` build (the Chrome channel is absent in this container, so the preview walk launched through the test's headless-shell fallback).
- Overall finding: local synthetic checks PASS. Combined focused set 165 passed, 1 skipped (pre-existing Windows-only path case), 0 failed; Node contract test and JavaScript syntax checks pass. Final-head CI is pending; no live, work-PC or portal check was requested or performed.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| P-01 to P-12, W-01 to W-11, U-01 to U-07 | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_layout.py --test tests/test_flow_local_file.py --test tests/test_flow_outlook.py --test tests/test_flow_standalone.py --test tests/test_flow_topic_groups.py --test tests/test_flow_activity.py --test tests/test_flow_paths.py --test tests/test_flow_sql_view_refresh.py --test tests/test_flow_handover.py --test tests/test_python_scripts_preview.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py --syntax app/static/app.js --syntax app/static/recording-preview/python-scripts.js` (the backend and frontend selector sets from the plan combined into one run, plus the SQL view-refresh and handover companions) | Working tree on `abf57073` + uncommitted change; Linux, Python 3.13.12, checkout-owned `.venv`; isolated run root `.test-runs/20260916T191329162Z-9632-a1606d1d` | PASS: 165 passed, 1 skipped, 0 failed, 0 errors; 180.1 s. Skip: `tests/test_flow_paths.py:36` "Native Windows path handling" (pre-existing, platform-gated). Warnings: two pre-existing dependency deprecation warnings from `starlette.testclient`. `tests/test_python_scripts_preview.py` executed (not skipped) at 1280×900 and 390×844 through the headless-shell fallback. | `.test-runs/20260916T191329162Z-9632-a1606d1d/result.json` (final run after the review fixes; an earlier pre-review run with the same counts: `.test-runs/20260916T183512115Z-6466-435c5fb5/result.json`) |
| U-03 (payload contract) and script syntax | `cd /home/user/data_governance && for f in tests/test_*.mjs; do node "$f"; done; for f in app/static/*.js app/static/recording-preview/python-scripts.js; do node --check "$f"; done; git diff --check` | Same working tree; Node v22.22.2 | PASS for `tests/test_flow_builder_contract.mjs` (python section included) and the other 24 UI contract tests; every `node --check` clean; `git diff --check` clean. `tests/test_gemini_extension.mjs` fails here only because `integrations/metronome-gemini` has no `node_modules` (`Cannot find package 'pg'`); the same failure reproduces on a clean `origin/main` worktree and CI runs `npm ci` for it, so it is environmental and unrelated to this change. | Terminal output; environmental failure reproduced on `origin/main` |

Case coverage inside `tests/test_flow_python.py` (17 tests, all real `sys.executable` subprocesses, synthetic SQLite and temporary Flows roots): P-01 to P-04 `test_python_flow_write_validates_scripts_and_forces_shared_defaults`, `test_flow_python_helpers_describe_and_normalize`; P-05 `test_python_flow_uses_hidden_anchor_managed_folder_and_v3_job`; P-06 `test_python_flow_update_changes_scripts_and_source_category_is_fixed`; P-07, P-08, P-10, W-10 `test_python_surfaces_activity_groups_resume_and_paths`; P-09, W-11 (claim refusal) `test_worker_claim_requires_python_script_capability`; P-11 `test_standalone_dry_run_reports_python_source_and_creates_nothing`; P-12 `tests/test_flow_layout.py` with `source == "python"`; W-01 `test_scripts_run_in_order_with_the_worker_interpreter`; W-02 `test_failing_script_names_step_and_stderr`; W-03 `test_script_that_writes_nothing_fails_on_missing_output`; W-04 `test_missing_or_non_py_script_fails_before_any_run_folder`; W-05 `test_script_timeout_fails_the_run`; W-06 `test_xlsx_final_file_is_validated_as_a_workbook`; W-07 `test_sql_destination_normalizes_csv_then_loads_and_refreshes` (with `flow_sql.load_artifacts` and `flow_view_refresh.execute_after_sql` monkeypatched); W-08 `test_headerless_csv_bound_for_sql_fails`; W-09 `test_direct_replace_publishes_the_final_file_into_the_target` (real `flow_publish`); malformed payload `test_malformed_python_job_is_rejected_by_execute_flow`. The `portal_work` browser exclusion in W-11 is a one-line set change in `run_worker` covered by review, not by a unit test.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression (all suites, Windows contracts, Chrome-channel preview tests) | NOT RUN | Final-head CI has not run; the PR is not open yet. | Open the PR and wait for the required `Merge ready` check on the final head; record its run URL and tested head SHA here and in the PR. |

## Usability evidence

`tests/test_python_scripts_preview.py::test_python_builder_list_and_run_history_walkthrough`
walked the fictional preview `app/static/recording-preview/python-scripts.html`
at 1280×900 and 390×844: source picker with the fifth **Python scripts** card
(U-01), add/remove script rows with renumbering and Browse through the file
chooser (U-02), Output radio swap between file fields and the SQL controls with
a manual materialized-view selection (U-03), the "Python scripts must be .py
files." validation error beside Save with the form preserved and the first
script focused (U-04), a successful save producing the **Python** group in the
list (U-05), the failed run's "Python script clean_orders.py (step 2 of 2)
failed with exit code 1: KeyError 'region'" in run history (U-06), and a
click on the saved Flow's Run action that shows the "Run queued. The worker
will run the Python scripts in order." toast and adds a Queued run for that
Flow, labelled "Python scripts", to run history (U-07). The Node contract
test additionally asserts the toast string in `app.js` source.
The test asserts no page errors and no horizontal overflow at both widths.
`PREVIEW_EVIDENCE_DIR` was not set for the recorded runs, so no screenshots
were saved. The preview uses fictional in-memory data and demonstrates the
builder journey only, not a real worker run.

## Findings, limitations and retests

- No failure attributable to the change. The combined selector set gave the
  same counts before and after the review fixes (165 passed, 1 skipped). The
  review fixes extended existing tests only: `script_command` is now compared
  against `str(Path)` with `sys.executable` (portable to the Windows CI
  shard), `flow_python.normalize_scripts` is asserted to reject 21 scripts with
  "Choose at most 20 Python scripts." (P-02), the failure alert is asserted to
  label the source "Python scripts: fetch_orders.py → enrich_orders.py"
  (P-07), `row_progress` is re-read at `python_complete` (W-10) and the preview
  walk clicks Run (U-07). The first attempt at the U-07 step failed on badge
  case ("Queued", not "queued"): `.test-runs/20260916T190948304Z-9222-0839945f`
  (1 failed, 164 passed); the corrected assertion passed alone in
  `.test-runs/20260916T191316814Z-9540-acbab514` and in the combined run above.
- `tests/test_flow_paths.py` line 51 now expects six managed source folders
  instead of five because `flow_paths.SOURCE_FOLDERS` gained the `Python`
  folder; this is an expected-value update, not a weakened assertion.
- Environment: this container has Playwright's `chromium_headless_shell-1194`
  but neither the Chrome channel nor the full Chromium build matching the
  locked driver, so the preview walk used the test's executable-path
  fallback; CI installs matching browsers and the fallback is inert there.
  The pre-existing Chrome-channel-only tests (for example
  `tests/test_templates_refresh_preview.py`, `tests/test_managed_flow_editor.py`)
  were outside the focused set and rely on CI.
- `tests/test_gemini_extension.mjs` needs `npm ci` in
  `integrations/metronome-gemini`; it fails identically on `origin/main` in
  this container and is exercised by CI.
- Synthetic limits: SQL insertion and materialized-view refresh are
  monkeypatched (no PostgreSQL); scripts are throw-away fixtures written by
  the tests; no worker service, desktop browser, portal or work PC took part.

## Merge evidence

Pending. Final CI has not run. Before merging, the PR testing section will
record the final `Merge ready` run URL, tested head SHA and result; the PR
merge record supplies the merge SHA.
