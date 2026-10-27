# 2026-10-27: Worker reports no longer time out on Flow files: test plan

- Change/PR: branch `claude/dazzling-meitner-9a2bxn`; PR link in the PR's
  testing section. No UI change.
  Runs failed with `httpx.ReadTimeout: timed out` while the worker posted a
  progress update (for example run #1138 at the `outlook_search` stage). Every
  claim and progress report writes the Flow's last-run columns, and any commit
  to `flows` refreshed every Flow's continuity files on the shared folder
  before the server replied. A no-op refresh costs about 0.4 s of code hashing
  per Flow plus shared-folder reads, so the reply took longer than the worker's
  60-second limit once there were enough Flows or the share was slow. All five
  retries timed out the same way, and each queued another full refresh.
  The fix:
  1. Writes that touch only run bookkeeping columns of `flows` (`last_*`,
     `next_run_at`, `updated_at`, the Outlook and file source receipts) no
     longer trigger a refresh. None of them is part of a Flow's files.
  2. SQLite compiles the SQL reconciliation trigger into every run status
     update, although it fires only after an interrupted append load. That
     trigger no longer forces a full inline refresh. After the commit, the
     server checks whether any Flow is fenced and refreshes only those Flows
     on a background thread.
  Real configuration changes (saves, owner and shared-setting edits, a rolling
  Flow's next start week) still refresh inline as before.
- Code baseline: `main` at `fddf81f`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux with the checkout-owned Python 3.13 `.venv`;
  final-head CI on Ubuntu and Windows.

## Prerequisites and test data

Disposable fixtures only: temporary SQLite databases and Flow folders, a
fictional catalog Flow and a registered test worker. No live portal, share or
work PC.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| W-01 | Queue a catalog Flow run, then claim it, post a `running` (`outlook_search`) report, a heartbeat and a `succeeded` report as the worker, with Flow-file refreshes recorded | No refresh is requested for this database; the Flow's `last_status` and `last_run_at` are updated | `test_flow_handover.py::test_worker_reports_do_not_wait_on_flow_files` |
| W-02 | Make the claimed run a recorded append SQL load, post `sql_insertion`, then post `failed` while the Flow-file refresh is held | The failed report returns while the refresh waits; the database fence is set; once released, the background refresh writes `sql_reconciliation_required: true` into flow.json | `test_flow_handover.py::test_sql_fence_from_failed_run_reaches_flow_files_without_delaying_the_report` |
| W-03 | Run W-01 and W-02 against unchanged `main` | Both fail: three refreshes are recorded, and the failed report waits for the refresh | Console output (baseline) |
| R-01 | Existing Flow-file behavior: saves, owner, schedule, catalog and shared-setting edits, edited-script archiving, startup hold and background catch-up, rollbacks | Unchanged | remaining `test_flow_handover.py`, `test_flow_portable_script.py`, the three lifespan tests in `test_auto_update.py` |
| R-02 | Existing SQL reconciliation fence behavior in recorded, parallel and view-refresh runs | Unchanged | `test_flow_recordings.py::test_recorded_replace_recovery_never_blocks_new_execution`, `test_flow_parallel.py::test_coordinator_restart_during_sql_is_fenced_and_never_replayed`, `test_flow_parallel.py::test_only_unknown_append_blocks_future_runs`, two `test_view_refresh.py` fence tests |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_flow_handover.py \
  --test tests/test_flow_portable_script.py \
  --test tests/test_auto_update.py::test_lifespan_reconciles_and_drains_active_update_before_runtime_start \
  --test tests/test_auto_update.py::test_lifespan_keeps_web_available_when_optional_startup_repairs_fail \
  --test tests/test_auto_update.py::test_lifespan_opens_web_before_flow_files_are_refreshed \
  --test tests/test_flow_recordings.py::test_recorded_replace_recovery_never_blocks_new_execution \
  --test tests/test_flow_parallel.py::test_coordinator_restart_during_sql_is_fenced_and_never_replayed \
  --test tests/test_flow_parallel.py::test_only_unknown_append_blocks_future_runs \
  --test tests/test_view_refresh.py::test_progress_persists_sql_outcome_separately_from_each_view_and_supports_retry \
  --test tests/test_view_refresh.py::test_replace_mode_never_sets_reconciliation_even_when_commit_status_is_uncertain \
  --syntax app/database.py --syntax app/flow_handover.py
```

Required final-head CI (`Merge ready`) is the full regression.

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
Rollback: revert the PR; no schema or data change. Nothing to clean up.
