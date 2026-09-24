# Python scheduler, `.env` credentials and script monitoring: test report

- Plan: [test-plan.md](test-plan.md). Change: [PR #143](https://github.com/datap0nd/data_governance/pull/143).
- Evidence cutoff: 2026-09-24 08:35 UTC.
- Local revision: implementation working tree based on PR head
  `0bf01d8139e1dbbc742b0adbcf2a50845c605360`. Each verifier result has
  a source fingerprint; final-head CI will verify the committed revision.
- Environment: WSL Ubuntu 24.04 on AArch64, checkout-owned CPython 3.13.15
  `.venv` and locked dependencies; Windows Node.js 24.19.0; local in-app
  browser preview at 1280×900 and 390×844.
- Current delivery state: local checks complete. Final-head CI and merge are
  pending at this cutoff.

## Automated results

| Cases | Exact command or procedure | Result | Evidence |
| --- | --- | --- | --- |
| D-01–D-03 | Plan-document whitespace, relative-link and named-path checks at the plan-only PR head | PASS: no whitespace errors; 214 links in 9 files resolved; 26 named paths checked | Plan-only PR report before this implementation; [run 35970081363](https://github.com/datap0nd/data_governance/actions/runs/35970081363) |
| E-01, R-01–R-03, M-01–M-03 | `tools/check.py verify --test tests/test_flow_run_mode.py --test tests/test_flow_live_output.py --test tests/test_env_file.py --syntax app/flow_python.py --syntax app/flow_process_tree.py --syntax app/flow_script_live.py --syntax app/routers/flows.py` | PASS: 17 tests at that working-tree fingerprint; exact argv, child wait and timeout, live output bounds, Stop, retention and `.env` parser | `.test-runs/20260924T083033518Z-383-21ab3c87/result.json` |
| R-01–R-03, M-01–M-03 final local retest | `tools/check.py verify --test tests/test_flow_run_mode.py --test tests/test_flow_live_output.py --syntax app/flow_python.py --syntax app/flow_process_tree.py --syntax app/flow_script_live.py` | PASS: 14 tests after the final runner and output-queue fixes | `.test-runs/20260924T083437422Z-383-b52efe37/result.json` |
| E-02–E-03, R-04 | `tools/check.py verify --test tests/test_auditor_managed.py --test tests/test_flow_portable_script.py --test tests/test_flow_standalone.py --test tests/test_pipelines.py --syntax app/config.py --syntax app/flow_portable.py --syntax app/flow_sql.py --syntax app/flow_standalone.py` | PASS: 46 passed, 1 Windows ACL case skipped on Linux | `.test-runs/20260924T082310731Z-373-7f33611e/result.json` |
| R-04 and affected activity | `tools/check.py verify --test tests/test_flow_python.py --test tests/test_env_file.py --test tests/test_flow_activity.py --syntax app/flow_python.py --syntax app/flow_worker.py --syntax app/routers/flows.py` | Initial run: 56 passed, 4 failed; the failures were two new job-key expectations, one old `subprocess.run` mock after switching to `Popen`, and a real missing portable `ENV_FILE` export. Fixed all four and reran those cases: 4 passed. | `.test-runs/20260924T081056233Z-413-c1d0bbee/result.json`; retest `.test-runs/20260924T081348013Z-384-a3fa96ae/result.json` |
| M-04 | `node tests/test_flow_builder_contract.mjs`; `node tests/test_flow_run_log_live.mjs`; `node --check app/static/app.js`; `node --check app/static/flow_run_log.js`; `node --check app/static/recording-preview/python-scheduler.js` | PASS: builder payload/recovery and incremental console contracts; syntax clean | Local command output |
| U-01–U-03 | Fictional preview browser walkthrough at the two planned viewport sizes | PASS: two-step run journey, save recovery, history filter, activity log, Stop/failure and mobile step switch; no document overflow at 390 px | [Walkthrough](evidence/preview-walkthrough.md) |

The verifier's `result.json` files are ignored local artifacts; the final CI
run will provide durable GitHub evidence for the committed head. The synthetic
browser preview runs entirely against in-memory fictional data.

## Findings and limitations

- The initial Windows ARM64 host Python could not install locked
  `psycopg2-binary` because that wheel was unavailable for that platform.
  Verification therefore used the repository-owned Linux `.venv` under WSL.
- The Linux auditor suite skipped its Windows ACL execution case. The
  installer contract assertions passed; required Windows CI remains the
  platform gate.
- The test client emitted two existing Starlette deprecation warnings about
  `httpx` and `anyio`. They did not affect results.
- Process exit codes for orphaned children are best effort. Processes launched
  through COM or Task Scheduler cannot be followed by the process-tree
  observer; these limits are documented for operators.

## Final-head gate

| Case | Status at cutoff | Next action |
| --- | --- | --- |
| D-04 | NOT RUN | Push the implementation, record the final `Merge ready` run URL and exact tested head SHA in the PR testing section, then merge pinned to that head. |
