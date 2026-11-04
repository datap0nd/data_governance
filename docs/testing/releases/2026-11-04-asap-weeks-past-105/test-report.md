# 2026-11-04: ASAP week ranges past 105 weeks: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-04 08:06
- Tested code revision: `2a7e6bb` (application and test changes; this report
  is committed after it)
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv` with the locked CI dependencies, Playwright 1.62.0 driving the
  container's Chromium 153.0.8010.12 (synthetic, headless)
- Overall finding: every case in the plan passes locally (automated,
  synthetic fixtures). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| W-01 to W-03, R-01 | The `python tools/check.py verify` command in the plan | `2a7e6bb`, Linux above | PASS: 390 passed, 0 failed, 17 skipped, 110.9 s; syntax clean for `app/routers/flows.py` and `app/flow_range_slider.py` | `.test-runs/20261104T080147996Z-4104-ebfb70af/result.json` |
| R-01 | `python -m pytest tests/test_recording_ranges.py`, with a scratch pytest plugin (not committed) that launches the container's Chromium | `2a7e6bb`, Chromium above | PASS: 20 passed, 23.2 s | Console output |
| W-04 | The three new tests run with `app/` restored to `e8ca974` and the new tests kept | `e8ca974` application code, Linux above | 3 failed, as expected: the old code rejected the 149-week range and discovered no slider weeks | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

The first verify run (`.test-runs/20261104T075749710Z-1087-6791f705`, same
code before it was committed) also selected `tests/test_recording_ranges.py`.
18 of its 20 tests failed before running any test step: the locked Playwright
looks for `chromium_headless_shell-1234`, which this container does not have.
Those 20 tests pass with the container's Chromium (row 2). The 17 skips are
one Windows-only NTFS junction test and 16 browser preview tests in
`tests/test_python_scripts_preview.py`, for the same missing browser build.
CI installs its own browser.

The fixtures are synthetic. They prove the planning and slider expansion, not
how a real ASAP slider spanning more than 105 weeks behaves; the keyboard
driver's existing 1,000-step limit still bounds a slider move.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
