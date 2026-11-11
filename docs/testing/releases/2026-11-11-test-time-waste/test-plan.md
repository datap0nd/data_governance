# 2026-11-11: Recorded-Flow saves no longer rebuild their source; tests stop paying fixed costs: test plan

- Change/PR: link in the PR's testing section. One fix in the app and two
  changes to how tests are set up. No test is removed and no assertion is
  changed.
- Code baseline: `main` at `ae034b7`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv`; the pull request's CI for the browser tests, the full
  regression and the timing

## What changes

1. **App.** `flow_portable.execution_sources` kept two results but was asked
   in three ways (`()`, `'recorded'`, `'catalog'`), so it never reused one.
   Every save of a recorded Flow rebuilt the Flow engine's source three times,
   about three seconds. All lookups now share one result per kind of Flow.
2. **Tests, opt-in per file: `skip_flow_files`.** Saving a Flow regenerates
   the files Metronome keeps beside it, about 0.4 seconds each time. Twelve
   files that create many Flows to test something else no longer do that. A
   test that reads those files is marked `real_flow_files` and keeps the real
   behaviour; the stand-in returns a state no real run returns, so a test
   that depends on it fails instead of passing by accident.
3. **Tests, opt-in per file: `no_settle_pause`.** The worker pauses half a
   second between two reads of a download. Six files whose tests write the
   complete file themselves skip the pause; both reads and the comparison
   still happen. No file that downloads through a real browser uses it. The
   pause is now a named constant in `app/flow_worker.py` with the same value.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Clear the cache; ask for the source in all three ways, three times over, and for its hash | Two builds in total, one per kind of Flow; the default and `'recorded'` are the same result | `tests/test_portable_source_cache.py::test_every_way_of_asking_shares_one_entry_per_kind_of_flow` |
| T-02 | Compare the kept result with a fresh build | Identical content and hash | `...::test_the_kept_result_is_the_same_content_a_fresh_build_gives` |
| T-03 | The twelve `skip_flow_files` files with the stand-in | Every test passes except the two that read the generated script; those two are marked `real_flow_files` and pass with the real mirror | The run in the report; `tests/test_flow_parallel.py::test_inline_browser_switch_keeps_parallelism_and_refreshes_standalone`, `tests/test_flow_python.py::test_standalone_dry_run_reports_python_source_and_creates_nothing` |
| T-04 | The six `no_settle_pause` files | Unchanged results; the two tests of the settling rule itself still pass | `tests/test_flow_sql.py::test_source_snapshot_waits_out_a_mid_flush_short_read`, `...::test_source_snapshot_rejects_a_file_that_never_settles`, and the run in the report |
| T-05 | The same 20 files on `main` and on this change, same machine | Same tests pass; the time is recorded | Report |
| T-06 | Files that exercise the real Flow file mirror and were not opted in | Unchanged | `tests/test_flow_handover.py`, `tests/test_flow_portable_script.py`, `tests/test_flow_folder_rename.py`, `tests/test_recording_templates.py` in the same run |

## Automated checks

```bash
python tools/check.py verify --test tests/test_portable_source_cache.py \
  <the 20 files listed in the report> \
  --syntax app/flow_worker.py --syntax app/flow_portable.py
```

Required final-head CI is the full regression, including every browser test,
and the measurement of the whole gate.

## Acceptance and cleanup

Accepted when T-01 to T-06 pass and `Merge ready` passes on the final head.

Not covered here: the remaining waits found in the analysis (a ten-second
pacing wait and three- and five-second page polls in browser tests, one
browser per test, a database built per test). Those need a browser to verify
and are left for a later change.
