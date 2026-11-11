# 2026-11-11: Tests that prove nothing removed, with the leftover code they kept alive: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 13:40
- Tested code revision: the commit that adds this package, on `main` at
  `eed4930`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`, Google Chrome for the browser files that could start. Microsoft
  Edge and the bundled headless browser are not installed here
- Overall finding: everything that can run here passes. 49 browser cases could
  not start a browser under the verifier; 90 of the cases in four of those
  files pass when run directly with Chrome. The Edge-only case and
  `tests/test_recording_ranges.py` are left to final-head CI.

## What was removed

3,096 tests before, 2,924 after: 172 fewer, in 148 test files instead of 153.
About 2,300 lines of tests and 830 lines of application code and prototype
pages.

| Area | Tests removed | Notes |
| --- | --- | --- |
| Recording tests | 17 cases and 1 assertion | Duplicates of another case, and tests that read source text |
| Flow tests and Flow groups | 54 | 27 text-in-source tests in `tests/test_flows.py`; all 19 of `tests/test_flow_topic_groups.py`; `app/routers/flow_groups.py` and its callers |
| Remaining Flow tests and prototypes | 26 | Three test files and five prototype files deleted |
| Scanner, settings, updater and installer tests | 75 | `tests/test_overview_removed.py` deleted; 164 lines of code nothing called removed from `app/main.py` |

## Decided against, after checking

The reviewers and the final pass kept these, although the review had listed
them:

- `tests/test_env_file.py::test_template_and_setup_contract`: the only check
  that `.env` is ignored by git and that `.env.example` ships with blank
  credentials.
- `tests/test_settings_scanner_schedules.py`, whole file: one of its tests
  holds the rule that a daily refresh can never still count as late when the
  next day's is due, and the Dubai-time check for the backup time.
- Three updater tests in `tests/test_auto_update.py`: each asserts one thing
  its twin does not (the recorded commit of the attempt, a single lookup of
  the newest commit).
- `tests/test_templates_refresh_preview.py`: the only proof that the editor
  forwards the pending website to the template list.
- `test_edited_script_is_archived_and_refreshed_on_save`, both cases of
  `test_recorded_positional_click_replays_as_captured`, and two of three empty
  download cases: each covers something no other test does.

## Coverage given up knowingly

No behavioural check replaces these; each was a comparison of source text:

- In-page JavaScript and progress truncation strings in `app/flow_worker.py`.
- The GSCM 60-second and 120-second load-buffer constants.
- The elevation lines of `setup.ps1`, and brace balance of the PowerShell
  scripts on Linux (the Windows job parses every script before a merge).
- The order of direct publish before transformation in the worker source.
- Query-history strings in `app.js`.
- With the `[chrome]` case of the generated-script journey gone, that journey
  runs only where Microsoft Edge is installed. CI has it.

## Behaviour that changes

- `/api/flows/groups` (list, create, update, ungroup, run) is gone. No page
  called it. Saved groups stay in the database, untouched.
- The Data Auditor's Flow list no longer shows a saved group name under a
  Flow. It was read from tables nothing maintains any more.
- Nothing else: the removed updater functions had no caller.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| P-01 | `python -m pytest --collect-only -q` | Working tree, Linux | 2,924 tests collected, no errors | Console output |
| P-02, P-05 to P-08 | The `python tools/check.py verify` command in the plan | Before the four tests above were put back, Linux | 1,336 passed, 66 skipped, 39 failed, 10 errors, 219 s. All 49 failures and errors are in five browser files and are the browser failing to start under the verifier (`BrowserType.launch`, Edge not found). No failure outside those files | `.test-runs/20261111T131539614Z-30569-12a70a68/result.json` |
| P-02, restored files | `python -m pytest tests/test_auto_update.py tests/test_env_file.py tests/test_settings_scanner_schedules.py` | Working tree | PASS: 82 passed, 13 s | Console output |
| P-03 | The four browser files run directly, synthetic pages, real Chrome | Same test content, before the rebase onto `eed4930` | 90 passed, 1 failed in 145 s. The failure is `test_same_portable_pipeline_runs_real_download_on_both_browsers[msedge]`: Edge is not installed | Console output |
| P-04 | `python -c "import app.main"`; `grep` for the removed names | Working tree | Imports; no reference left | Console output |
| P-08 prediction | Partition the 2,913 tests then collected into six shards with the refreshed durations | Working tree | Recorded seconds per shard: 167, 179, 162, 169, 165, 176 | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| `tests/test_recording_ranges.py` (18 browser cases) | BLOCKED | Needs the bundled headless browser, which is not installed here. The file's one edit removes a single assertion from a test that needs no browser, and that test passes | Final-head CI |
| `test_same_portable_pipeline_runs_real_download_on_both_browsers[msedge]` | BLOCKED | Needs Microsoft Edge | Final-head CI |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

- Recorded durations were refreshed from run 43023114891, the first full run
  after the earlier speed-up: 1,035 seconds of tests, down from 1,488. The
  file still lists the five deleted test files; the partitioner ignores files
  that are not collected.
- The four reviews were made by separate reviewers, each on its own slice.
  One pass over the combined result removed three leftovers they could not
  reach from one slice: the drain-list entry for the deleted route, the group
  name in the Data Auditor, and an uncalled helper.
- A lock collision (`BlockingIOError` under `/tmp/Metronome/execution-locks`)
  appeared once in `tests/test_flow_shared_layout.py` while several test runs
  shared the machine. It passed on two reruns and is not caused by this
  change.

## Final assessment

Ready for final-head CI.
