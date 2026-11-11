# 2026-11-11: Recorded-Flow saves no longer rebuild their source; tests stop paying fixed costs: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 12:45
- Tested code revision: `09d41e1` on `main` at `ae034b7`; the commit that
  adds this package changes documentation only
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`. Browser tests cannot start here
- Overall finding: the same tests pass before and after, and the 20 affected
  files run in 159 seconds instead of 496 on the same machine. The effect on
  the whole gate is not known until this pull request's CI run.

## Measurements

| What | Before | After |
| --- | --- | --- |
| Asking for the script source in three ways, per cycle, after the first | 2.3 to 3.5 s, 0 cache hits in 12 calls | 0 s |
| Saving one catalogue Flow, including its files | 0.43 s | unchanged in the app; skipped in the twelve opted-in test files |
| The 20 affected test files, `python -m pytest`, same machine | 891 passed, 2 skipped in 496 s on `ae034b7` | 893 passed, 2 skipped in 159 s on `09d41e1` (two new tests) |

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| T-01 to T-04, T-06 | The `python tools/check.py verify` command in the plan with `test_flow_capacity`, `test_flows`, `test_flow_topic_groups`, `test_audit_flows_api`, `test_flow_parallel`, `test_flow_activity`, `test_flow_ownership`, `test_settings_flows`, `test_flow_python`, `test_view_refresh`, `test_gscm`, `test_flow_local_file`, `test_flow_sql`, `test_flow_excel_formats`, `test_flow_sql_view_refresh`, `test_recorded_output_storage`, `test_recording_templates`, `test_flow_portable_script`, `test_flow_folder_rename`, `test_flow_handover` | `09d41e1`, Linux | PASS: 893 passed, 0 failed, 2 skipped, 158.9 s; two syntax checks passed | `.test-runs/20261111T123041490Z-5164-fd9c451a/result.json` |
| T-03, finding the tests that need the real mirror | The twelve `skip_flow_files` files before any test was marked | Working tree before `09d41e1` | 653 passed, 2 failed: exactly the two tests that read the generated script | Console output |
| T-05, before | `python -m pytest` over the same 20 files in a checkout of `main` | `ae034b7`, same machine | 891 passed, 2 skipped, 496.0 s | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Browser tests | BLOCKED | The project's Playwright has no browser it can start in this container | The pull request's Python shards |
| Full regression and the time of the whole gate | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL, the time and the tested head in the PR testing section |

## Findings, limitations and retests

- The cache fix changes the app: a recorded Flow saves about three seconds
  faster on the work PC. That was measured here, not on the work PC.
- `skip_flow_files` is used only in files that could be run here. In those
  files the files beside a Flow are no longer produced during a test, so a
  defect in producing them would be caught only by the tests that keep the
  real mirror: the two marked tests, `tests/test_flow_handover.py`,
  `tests/test_flow_portable_script.py`, `tests/test_flow_standalone.py` and
  the recording tests.
- The record the six CI runners use to share out the tests
  (`tools/ci/test_durations.json`) still holds the old times, so the split
  will be less even until it is refreshed from this change's run.
- Left for later, because they need a browser to verify: a ten-second pacing
  wait in one journey test, page polls of three and five seconds, a browser
  started per test, and a database built per test.

## Final assessment

Ready for final-head CI.
