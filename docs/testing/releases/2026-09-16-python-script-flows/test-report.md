# Python-script Flows: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: [PR #133](https://github.com/datap0nd/data_governance/pull/133).
- Evidence cutoff (UTC): 2026-09-16 20:23 (local verification of the working tree after the review follow-ups and the Codex review round; final-head CI has not run).
- Tested code revision: commit `12061ba9e6d3cfb6fb6d98a3f6f3e5f2d857c259` ("Add the Python-script Flow source", rebased on `origin/main` `4b7fb019485ba4052a30b335096adb16f81c2866`) plus the review follow-ups that were then committed unchanged as `affb4f0` ("Stage Python uploads inside the enforced folder and relocate scripts on rename"): the verified working tree equals the tree of `affb4f0` except one documentation sentence in `docs/python_script_flows.md` added after the run. This report is committed on top of that head as a documentation-only change; the final-head `Merge ready` run and SHA are recorded in the PR testing section.
- Environment: Linux container, Python 3.13.12 in the checkout-owned `.venv` (`requirements-ci.lock`, `tools/check.py` preflight ready), Node v22.22.2 for the contract tests, Playwright 1.62.0 with the installed `chromium_headless_shell-1194` build (the Chrome channel is absent in this container, so the preview walk launched through the test's headless-shell fallback).
- Overall finding: local synthetic checks PASS. Combined focused set 385 passed, 1 skipped (pre-existing Windows-only path case), 0 failed before the Codex round; the Codex-round selector set 325 passed, 0 skipped, 0 failed; Node contract tests and JavaScript syntax checks pass. Final-head CI is pending; no live, work-PC or portal check was requested or performed.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| P-16, P-17, W-12 (Codex review round) plus P-01 to P-06, P-08 to P-12, W-01 to W-10, U-01 to U-07 | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_layout.py --test tests/test_flow_local_file.py --test tests/test_flow_outlook.py --test tests/test_flow_activity.py --test tests/test_flow_sql_view_refresh.py --test tests/test_flows.py --test tests/test_python_scripts_preview.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py --syntax app/database.py --syntax app/flow_activity.py --syntax app/static/app.js`; then `.venv/bin/python -m pytest tests/test_flow_folder_rename.py tests/test_flow_email_delivery.py tests/test_flow_standalone.py -q` with the CI environment variables under a scratch root | Commit `1e37710d` + the Codex-round edits committed next (working tree); Linux, Python 3.13.12, checkout-owned `.venv`; isolated run root `.test-runs/20260916T201932939Z-13811-06e2fd3f` | PASS: 325 passed, 0 skipped, 0 failed, 0 errors; 185 s; the companion pytest run 51 passed, 0 failed in 80.7 s. Warnings: the same two pre-existing dependency deprecation warnings. `tests/test_python_scripts_preview.py` executed through the headless-shell fallback. | `.test-runs/20260916T201932939Z-13811-06e2fd3f/result.json` (an identical selector run before the fourth, OSError finding was fixed: `.test-runs/20260916T201021361Z-13362-13f2cb47/result.json`, 324 passed) |
| P-01 to P-15, W-01 to W-11, U-01 to U-07 | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_layout.py --test tests/test_flow_local_file.py --test tests/test_flow_outlook.py --test tests/test_flow_standalone.py --test tests/test_flow_topic_groups.py --test tests/test_flow_activity.py --test tests/test_flow_paths.py --test tests/test_flow_sql_view_refresh.py --test tests/test_flow_handover.py --test tests/test_flow_email_delivery.py --test tests/test_flows.py --test tests/test_python_scripts_preview.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py --syntax app/flow_email_delivery.py --syntax app/static/app.js --syntax app/static/recording-preview/python-scripts.js` (the backend and frontend selector sets from the plan combined into one run, plus the SQL view-refresh, handover and `test_flows.py` companions) | Commit `12061ba9` + the review follow-ups later committed as `affb4f0`; Linux, Python 3.13.12, checkout-owned `.venv`; isolated run root `.test-runs/20260916T195252692Z-12448-4035166e` | PASS: 385 passed, 1 skipped, 0 failed, 0 errors; 293.3 s. Skip: `tests/test_flow_paths.py:36` "Native Windows path handling" (pre-existing, platform-gated). Warnings: two pre-existing dependency deprecation warnings from `starlette.testclient`. `tests/test_python_scripts_preview.py` executed (not skipped) at 1280×900 and 390×844 through the headless-shell fallback. | `.test-runs/20260916T195252692Z-12448-4035166e/result.json` (final run after the review follow-ups; the directly affected subset `tests/test_flow_python.py tests/test_flow_layout.py tests/test_flow_folder_rename.py` with syntax checks of `app/flow_folder_rename.py`, `app/flow_layout.py` and `app/routers/flows.py` passed 58/58 first in `.test-runs/20260916T195101570Z-12408-cacbef94/result.json`; the earlier pre-follow-up runs `.test-runs/20260916T191329162Z-9632-a1606d1d` and `.test-runs/20260916T183512115Z-6466-435c5fb5` gave 165 passed, 1 skipped on the smaller selector set) |
| U-03 (payload contract) and script syntax | `cd /home/user/data_governance && for f in tests/test_*.mjs; do node "$f"; done; for f in app/static/*.js app/static/recording-preview/python-scripts.js; do node --check "$f"; done; git diff --check` | Same working tree; Node v22.22.2 | PASS for `tests/test_flow_builder_contract.mjs` (python section included) and the other 22 UI contract tests (23 of the 24 `tests/test_*.mjs` files); every `node --check` clean; `git diff --check` clean. `tests/test_gemini_extension.mjs` fails here only because `integrations/metronome-gemini` has no `node_modules` (`Cannot find package 'pg'`); the same failure reproduces on a clean `origin/main` worktree and CI runs `npm ci` for it, so it is environmental and unrelated to this change. | Terminal output; environmental failure reproduced on `origin/main` |

Case coverage inside `tests/test_flow_python.py` (19 tests, all real `sys.executable` subprocesses, synthetic SQLite and temporary Flows roots): P-01 to P-04 `test_python_flow_write_validates_scripts_and_forces_shared_defaults`, `test_flow_python_helpers_describe_and_normalize`; P-05 `test_python_flow_uses_hidden_anchor_managed_folder_and_v3_job`; P-06 `test_python_flow_update_changes_scripts_and_source_category_is_fixed`; P-07, P-08, P-10, W-10 `test_python_surfaces_activity_groups_resume_and_paths`; P-09, W-11 (claim refusal) `test_worker_claim_requires_python_script_capability`; P-11 `test_standalone_dry_run_reports_python_source_and_creates_nothing`; P-12 `tests/test_flow_layout.py` with `source == "python"`; P-13 `test_browse_upload_stages_python_scripts_inside_the_enforced_folder` (with the `.uploads` slug rule also asserted in `tests/test_flow_layout.py::test_layout_refuses_foreign_marker_and_cleanup_preserves_user_content` and `tests/test_flow_folder_rename.py::test_folder_names_are_safe_and_do_not_append_identity`); P-14 `tests/test_flow_email_delivery.py::test_python_source_label_names_the_scripts_in_order`; P-15 `test_renaming_a_python_flow_relocates_scripts_kept_in_its_folder`; W-01 (including the checksum on every `python_step` event and `python_complete` result) `test_scripts_run_in_order_with_the_worker_interpreter`; W-02 (including the checksum on the failed step's event and the absent `python_complete`) `test_failing_script_names_step_and_stderr`; W-03 `test_script_that_writes_nothing_fails_on_missing_output`; W-04 `test_missing_or_non_py_script_fails_before_any_run_folder`; W-05 `test_script_timeout_fails_the_run`; W-06 `test_xlsx_final_file_is_validated_as_a_workbook`; W-07 `test_sql_destination_normalizes_csv_then_loads_and_refreshes` (with `flow_sql.load_artifacts` and `flow_view_refresh.execute_after_sql` monkeypatched); W-08 `test_headerless_csv_bound_for_sql_fails`; W-09 `test_direct_replace_publishes_the_final_file_into_the_target` (real `flow_publish`); malformed payload `test_malformed_python_job_is_rejected_by_execute_flow`. The `portal_work` browser exclusion in W-11 is a one-line set change in `run_worker` covered by review, not by a unit test.

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

- Codex review round on `affb4f0` (three findings, all confirmed and fixed;
  a fourth was found by the adversarial re-review of the fix): the hidden
  `Python` site row could collide with a user portal site of the same name
  because `flow_sites.name` is unique, so the migration now steps aside to a
  collision-safe internal name (P-16); with the SQL destination the builder
  still submitted the hidden output mode, so a stale fixed-file-path choice
  would have published the CSV, and both the builder and `FlowWrite` now force
  `run_folders` under SQL (P-17); per-step records were emitted only after a
  fully successful chain, so a failure lost the earlier steps' exit codes and
  output, and `run_scripts` now reports `python_step_complete` /
  `python_step_failed` events as each step ends, including timeouts and an
  interpreter that cannot be spawned (W-12). Each fix carries a test; the
  selector set passed 325/325 afterwards.
- No failure attributable to the change. The final combined run (385 passed,
  1 skipped) covers the review follow-ups: P-13 to P-15 were added to the plan
  for the Browse staging contract, the email source label and the rename
  relocation of `python_scripts_json` (a plain rename previously left a Python
  Flow's scripts pointing at the moved folder, so the next scheduled run would
  have failed closed; `app/flow_folder_rename.py` now relocates that column and
  its other-Flow guard considers it), `flow_layout.flow_folder_slug` strips
  leading dots so `<root>/Python/.uploads` can never be a Flow folder, and
  `target=python` uploads report "Python script" in their size errors and log
  the `flow_python_script` entity. Earlier pre-follow-up runs on the smaller
  selector set gave 165 passed, 1 skipped. The first review round's fixes
  extended existing tests only: `script_command` is now compared
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

Final CI is pending at this cutoff. [PR #133](https://github.com/datap0nd/data_governance/pull/133) carries the final `Merge ready` run URL, tested head SHA and result in its testing section before the head-pinned merge; the PR merge record supplies the merge SHA.
