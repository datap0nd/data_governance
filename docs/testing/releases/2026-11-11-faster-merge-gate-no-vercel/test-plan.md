# 2026-11-11: Faster merge gate and no Vercel deployment: test plan

- Change/PR: link in the PR's testing section. Two housekeeping changes.
  The Python tests are split across six runners by how long each file takes,
  instead of two runners by number of tests. Vercel no longer deploys this
  repository, and its serverless entry point is removed.
- Code baseline: `main` at `058f0fa`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv` for the tests; the pull request's own CI run for the timing,
  since that run is the thing being changed

## Prerequisites and test data

No live system. The split is tested on made-up test lists and on small pytest
projects created in a temporary folder. The recorded times in
`tools/ci/test_durations.json` come from the JUnit results of pull request run
43010900841.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| G-01 | One slow file with two tests and six quick files with ten each; split in two with and without recorded times | With times each runner gets 120 seconds; by count they get 60 and 180. Every test runs once, a file stays whole, and collection order does not matter | `tests/test_ci_sharding.py::test_partition_balances_recorded_time_not_test_count` |
| G-02 | A file with no recorded time; a recorded file that no longer exists | The new file counts each test at the average of the timed files; the stale record is ignored | `...::test_a_file_never_timed_counts_its_tests_at_the_average` |
| G-03 | A missing, malformed or partly invalid record | Unusable entries are dropped; with none left the split is the previous count-based one | `...::test_unusable_recorded_times_fall_back_to_counting_tests` |
| G-04 | Two JUnit files given to `tools/ci/record_durations.py` | Seconds are summed per test file, including test classes; entries that are not test files are left out | `...::test_recorded_times_come_from_junit_results` |
| G-05 | Read the workflow | The matrix list, `--ci-shard-count` and the audit's `--count` name the same number, at least four | `...::test_workflow_names_one_shard_count_in_all_three_places` |
| G-06 | Split the recorded run across the workflow's runner count | The slowest runner is within 10% of the average, and no single file is longer than a runner's share | `...::test_the_recorded_run_splits_evenly_across_the_workflows_runners` |
| G-07 | A real pytest run of a three-file project with a record that makes one test slow, then the coverage audit | The slow test runs alone, and the audit accepts the two runs as complete and non-overlapping | `...::test_actual_pytest_run_uses_the_recorded_times_it_is_given` |
| G-08 | Existing sharding and audit tests | Unchanged behaviour still passes | The other 20 tests of `tests/test_ci_sharding.py` |
| G-09 | This pull request's CI run | Six Python jobs, each close to the others in duration; `Merge ready` accepts their coverage audit; time from the workflow starting to `Merge ready` finishing is recorded against the earlier runs | The run linked in the PR testing section |
| V-01 | Parse `vercel.json`; import the app; search the code for the removed entry point | The file only sets `git.deploymentEnabled` to `false`, the setting Vercel documents for "turning off all automatic deployments"; the app imports; nothing refers to `api/index.py` | Commands in the report |
| V-02 | This pull request, after the push | Vercel creates no new deployment for the pushed head | Observed on the pull request; see its testing section |

## Automated checks

```bash
python tools/check.py verify --test tests/test_ci_sharding.py \
  --test tests/test_config_refresh.py --test tests/test_env_file.py \
  --syntax tools/ci/pytest_shard.py --syntax tools/ci/record_durations.py --syntax app/config.py
```

Required final-head CI is the full regression and, for this change, also the
measurement.

## Acceptance and cleanup

Accepted when G-01 to G-08 and V-01 pass, `Merge ready` passes on the final
head with six Python jobs, and the measured time is recorded.

To finish removing Vercel, the owner disconnects or deletes the project in
Vercel's dashboard; deployments made before this change stay online until
then. After that `vercel.json` can be deleted as well.
