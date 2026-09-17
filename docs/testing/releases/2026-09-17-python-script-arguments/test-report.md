# Python-script arguments and setup fixes: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: [PR #134](https://github.com/datap0nd/data_governance/pull/134).
- Evidence cutoff (UTC): 2026-09-17 10:20 (local verification of the tree committed as `80efba5`; final-head CI has not finished).
- Tested code revision: the working tree later committed unchanged as `80efba5a40af422f97012feec9646f1fc6931634` on top of `origin/main` `9164347` (the verifier's `result.json` files therefore record revision `9164347` plus uncommitted changes). This report is committed on top of that head as a documentation-only change.
- Environment: Linux container, Python 3.13.12 in the checkout-owned `.venv` (`requirements-ci.lock`); Node v22.22.2 for the contract tests; Playwright 1.62.0 with the bundled `chromium_headless_shell-1194` (no Chrome channel, no PowerShell, so `--syntax setup.ps1` was not run).
- Overall finding: local synthetic checks PASS. Combined focused set 345 passed, 0 skipped, 0 failed; the Node contract test and JavaScript syntax checks pass. Final-head CI is pending. No live, work-PC, portal or PowerShell check was requested or performed.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| A-01 to A-17, S-01 to S-04, U-01 to U-07 (verifier set) | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_email_delivery.py --test tests/test_flow_layout.py --test tests/test_flow_worker_startup.py --test tests/test_python_scripts_preview.py --test tests/test_flows.py --test tests/test_flow_standalone.py --test tests/test_flow_activity.py --test tests/test_flow_folder_rename.py --test tests/test_flow_local_file.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py --syntax app/database.py --syntax app/flow_activity.py --syntax app/flow_email_delivery.py --syntax app/static/app.js --syntax app/static/recording-preview/python-scripts.js` (the plan's backend set plus the standalone, activity, folder-rename and local-file companions; `--syntax setup.ps1` omitted because the verifier refuses the whole run without PowerShell) | Tree of `80efba5` before commit; Linux, Python 3.13.12, checkout-owned `.venv`; isolated run root `.test-runs/20260917T101515609Z-2476-07339029` | PASS: 345 passed, 0 skipped, 0 failed, 0 errors; 168 s pytest, 169.1 s total. Warnings: the two pre-existing dependency deprecation warnings. `tests/test_python_scripts_preview.py` executed (not skipped) at 1280×900 and 390×844 through the headless-shell fallback. | `.test-runs/20260917T101515609Z-2476-07339029/result.json` (earlier runs on the same tree by the implementers: the plan's set minus `--syntax setup.ps1`, 261 passed, `.test-runs/20260917T101025356Z-2262-caba46d0/result.json`; companions 64 passed, `.test-runs/20260917T101227542Z-2416-c7003daf/result.json`) |
| U-01 to U-07 (payload contract) and script syntax | `cd /home/user/data_governance && node tests/test_flow_builder_contract.mjs && for f in app/static/*.js app/static/recording-preview/python-scripts.js; do node --check "$f"; done; git diff --check` | Same tree; Node v22.22.2 | PASS: the contract test prints its five sections including "flow builder python arguments and values tests passed"; every `node --check` clean; `git diff --check` clean. | Terminal output |
| A-18 retest with A-01 to A-17, S-02, S-03 (Codex capability finding) | `cd /home/user/data_governance && .venv/bin/python tools/check.py verify --test tests/test_flow_python.py --test tests/test_flow_worker_startup.py --syntax app/flow_python.py --syntax app/routers/flows.py --syntax app/flow_worker.py` | Tree committed next as the capability-gate commit on top of `189206b`; same environment | PASS: 33 passed, 0 skipped, 0 failed; 14.4 s. | `.test-runs/20260917T102205981Z-3043-6aaae004/result.json` |
| `--syntax setup.ps1` | Plan's verifier command with `--syntax setup.ps1` | Same tree | NOT RUN: the verifier exits 2 before any test ("PowerShell is required to check setup.ps1"), `.test-runs/20260917T100955392Z-2140-39669dd0/result.json`; S-01 and S-04 rest on `tests/test_flow_worker_startup.py::test_setup_elevation_window_is_visible_when_interactive_and_the_helper_sees_the_database`, which reads the exact lines and passed in the set above; CI's Windows job runs the script's verifier contracts. | `.test-runs/20260917T100955392Z-2140-39669dd0/result.json` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression (all suites, Windows contracts, Chrome-channel preview tests) | NOT RUN | Final-head CI has not finished at this cutoff. | Wait for the required `Merge ready` check on the final head; record its run URL and tested head SHA in the PR testing section before the head-pinned merge. |

## Usability evidence

`tests/test_python_scripts_preview.py::test_python_builder_list_and_run_history_walkthrough`
walked the fictional preview at 1280×900 and 390×844 (U-01 to U-07): the
three-line script rows with Arguments and Values and their live run count,
renumbering after removing a row, a blank-path row dropped together with its
arguments and values from the payload, the "Script arguments have an unclosed
quote." error shown beside Save with the arguments field focused and the
values preserved, a successful save producing the list label
`fetch_orders.py -sheet (3 values) → clean_orders.py`, run history showing
"Running script 2 of 4: fetch_orders.py -sheet U." and the bundle summaries,
and the edit screen restoring `-sheet` with `T`, `U`, `V`. No page errors and
no horizontal overflow at either width. The frontend implementer reviewed the
screenshots in a scratch folder; none are committed. The preview uses
fictional in-memory data and demonstrates the builder journey only, not a
real worker run.

## Findings, limitations and retests

- Codex review on `80efba5` (two findings): the report placeholders were
  filled in `189206b`; the second finding was real: a worker from the
  previous release advertises the `python_script` adapter and would have
  claimed a job with arguments or values and run each script once without
  them. Such jobs now also require the `python_script_arguments_v1` worker
  capability (A-18, `test_worker_claim_requires_the_arguments_capability_only_when_arguments_or_values_are_used`);
  plain Python jobs stay claimable by the older worker. Result recorded in
  the executed-checks table (retest row).
- Final-head CI run 381 on `2f0ca20` failed one case in Python shard 0:
  `tests/test_flow_email_delivery.py::test_python_source_label_names_the_scripts_in_order`
  registered a worker with only the `python_script` adapter and then claimed a
  job that uses values, which the new capability gate correctly refuses. The
  test worker now also advertises `python_script_arguments_v1`; the focused
  retest (`--test tests/test_flow_email_delivery.py --test tests/test_flow_python.py`)
  passed 43/43 in `.test-runs/20260917T103535334Z-367-a914fd7a/result.json`.
  The gate itself is unchanged.
- No failure attributable to the change in the final tree. During
  implementation one new test initially passed an unquoted `{value}` token
  with a value containing a space, which correctly split into two tokens
  (`.test-runs/20260917T100639096Z-2028-14a1ba3c`, 44 passed, 1 failed); the
  test was corrected to quote the token (the code was right) and passed alone
  in `.test-runs/20260917T100713987Z-2073-63eb3898` and in every later run.
- Six existing assertions on worker message text were updated to the new
  formats ("Ran N Python script run(s) and saved M file(s): ..." and "Ran N
  script run(s); M final CSV file(s): ...") because the run is now a bundle;
  no assertion was removed.
- `setup.ps1` gained one line beyond the two planned fixes:
  `$env:DG_DB_PATH = $DbPath` before the authentication loop, because the
  app's default database path is beside the code folder while setup keeps
  `governance.db` one level up; without it the helper's database fallback
  would look in the wrong place on the owner's layout. The helper fails
  closed with a clear message when no database exists there.
- Synthetic limits: SQL insertion and materialized-view refresh are
  monkeypatched (no PostgreSQL); scripts are throw-away fixtures written by
  the tests; the authentication helper's browser call is captured, not run;
  `setup.ps1` is read, not executed (no PowerShell on Linux runners).

## Merge evidence

Final CI is pending. The PR carries the final `Merge ready` run URL, tested
head SHA and result in its testing section before the head-pinned merge; the
PR merge record supplies the merge SHA.
