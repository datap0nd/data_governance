# Optional Flow SQL table owner: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: PR pending at this evidence cutoff.
- Evidence cutoff (UTC): 2026-09-21 10:57.
- Tested code revision: initial PR head `8abb196` plus the Users-browser assertion correction, which passed a focused local retest; the PR will record the exact final head.
- Environment: Windows ARM64, Python 3.13.15, Node 24.19.0, Playwright 1.62.0, headless Chrome 153.0.8010.50; synthetic SQLite/HTTP fixtures.
- Overall finding: Focused backend, browser, payload, and syntax checks passed. The first CI run found one stale assertion about the Users copy; that assertion was corrected and passed locally. Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result | Evidence |
| --- | --- | --- | --- | --- |
| Local setup | `python tools/check.py setup` | Baseline plus working changes; Windows ARM64 | BLOCKED: locked `psycopg2-binary==2.9.13` attempted a source build and `pg_config` was unavailable. | `.test-runs/20260921T102934389Z-15112-f408062e/result.json` |
| O-01 to O-05 | Checkout `.venv` with every other pinned package; `python -m pytest -p pytest_local` and four initial focused selectors, `-q --tb=short` | Working change; isolated fixtures | PASS: 6 passed in 15.57 s. A local plugin disabled pytest's temp-symlink cleanup, which fails on this Windows host; it does not change test or app behavior. The PostgreSQL adapter was absent because of the setup blocker and these cases do not use it. | Focused command output; screenshots below |
| O-06 | `node tests/test_flow_builder_contract.mjs` | Working change; Node 24.19.0 | PASS: payload, email, worksheet, and Python builder contracts. | Command output |
| O-06 | `node --check app/static/app.js`; `.venv/Scripts/python -m py_compile app/database.py app/routers/flows.py tests/test_flows.py tests/test_flow_excel_ui.py`; `git diff --check` | Working change; Windows | PASS: parsing and whitespace checks. | Command output |
| First CI attempt | Full `Tests` workflow on `8abb196`. | Ubuntu Python shards, Windows verifier, frontend contracts | FAIL: shard 1 had 1 failed, 1147 passed, 15 skipped. The failure was `test_users_profile_journey` expecting the former copy. Shard 0, Windows verifier, and frontend contracts passed. | [Run 35590155162](https://github.com/datap0nd/data_governance/actions/runs/35590155162) |
| Failed-case and companion retest | Checkout `.venv` pytest on `test_users_profile_journey` and `test_sql_owner_help_and_run_log_confirm_only_committed_changes`, with local temp-cleanup plugin. | Copy assertion correction; Windows | PASS: 2 passed in 6.86 s. | Focused command output |

The focused Python command selected `test_sql_handoff_target_is_persisted_without_executing_insert`, `test_flow_owner_can_receive_alerts_without_becoming_sql_table_owner`, `test_flow_owner_sql_assignment_is_optional_in_each_builder` (three source variants), and `test_sql_owner_help_and_run_log_confirm_only_committed_changes`. The local plugin was stored outside the checkout in `work/pytest_local.py` and was not committed.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| C-01 | NOT RUN | Final PR head did not exist at this cutoff. | Push the commit, wait for `Merge ready`, and record its run URL and exact SHA in the PR testing section before merging. |

## Usability evidence

Headless Chrome showed the option in Outlook, file, and portal builders. Turning it Off updated the owner help, retained the person on save, and persisted after reopening. The [desktop](screenshots/desktop.png) and [mobile](screenshots/mobile.png) captures show the wording and layout with fictional data. At 390 px, the Flow form had no horizontal overflow. Python-builder markup and payload behavior are covered by the Node contract; the Python UI was not exercised in the browser fixture.

## Findings, limitations and retests

The first browser fixture attempt closed its own **After download** step before looking for the checkbox; the next attempt found the fixture's preexisting worksheet validation. The test was corrected to leave the step open and provide a worksheet choice. The final six-case focused run passed. The first CI attempt then found a Users-browser assertion that expected the previous SQL-username guidance. The application copy was intentionally updated for the new option, so the assertion was updated and both affected Users-browser cases passed locally. The full locked Windows setup remains blocked by the host's missing `pg_config`; required CI will test the complete dependency set. No live SQL load was performed.

## Merge evidence

Final-head `Merge ready` is pending. Its run URL, exact tested head SHA, and result must be added to the PR testing section before a head-pinned merge. This report is local and synthetic evidence, not deployment verification.
