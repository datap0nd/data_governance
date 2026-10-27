# 2026-10-27: Worker reports no longer time out on Flow files: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-27 10:12
- Tested code revision: working tree on `fddf81f` with exactly the
  application, test and documentation changes committed as `dd729cb`
- Environment: Linux 6.18 (cloud container), Python 3.13.17 checkout-owned
  `.venv` with the locked CI dependencies
- Overall finding: every case in the plan passes locally (automated,
  synthetic fixtures). Both new tests fail on unchanged `main`. Final-head CI
  is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| W-01, W-02, R-01, R-02 | The `python tools/check.py verify` command in the plan | `dd729cb` content, Linux above | PASS: 35 passed, 0 failed, 0 skipped, 2 third-party deprecation warnings, 49.1 s; syntax clean for `app/database.py` and `app/flow_handover.py` | `.test-runs/20261027T101031581Z-1197-c1ffb805/result.json` |
| W-03 | `.venv/bin/python -m pytest tests/test_flow_handover.py -k "worker_reports or sql_fence"` with `app/database.py` and `app/flow_handover.py` stashed | `fddf81f` (unchanged `main`) plus the new tests, Linux above | Both fail as expected: W-01 records three refreshes (claim and both reports); in W-02 the failed report waits for the refresh, so flow.json already shows the fence when the report returns | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

The diagnosis comes from the worker traceback the owner shared for run #1138:
`execute_outlook_job` → `progress` → `_api` → `httpx.ReadTimeout` on
`POST /api/flows/worker/<worker>/runs/<run>/progress`. It is confirmed in code
and by W-03, not by timed logs from the work PC. On this container one no-op
Flow-file check costs 0.39 s of code hashing per Flow, before any read from
the share. The startup report from 2026-10-26 measured 0.46 s per Flow on
Windows.

What still refreshes inline on a worker request: a terminal report that
really changes the saved Flow, namely a rolling Flow advancing its start week
or a parallel run setting the reconciliation fence directly. Both are one
report per run. By the time the refresh starts the run is already recorded,
and the worker's retry is answered at once. Saves from the UI still refresh
every Flow inline, as before.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
