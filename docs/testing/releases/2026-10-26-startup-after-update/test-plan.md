# 2026-10-26: Metronome starts promptly after an update: test plan

- Change/PR: branch `fix/startup-after-update`; PR link in the PR's testing
  section. Two startup fixes, no UI change:
  1. The web port no longer waits for every Flow's continuity files. Startup
     used to refresh all of them before Metronome listened on port 8000. After
     an update, that refresh rewrites every Flow on the shared folder and can
     take longer than the ten minutes a worker keeps retrying registration.
     Commits made while starting are now held, and one background pass
     refreshes the files after the port opens. A save made during that pass
     returns at once; the pass runs again to include it.
  2. `scan_runs.changed_queries` is re-added to databases where the
     short-lived query-history removal (29 August) dropped it. Without it,
     automatic Alert analysis fails with `no such column: changed_queries`.
- Code baseline: `main` at `c4095ae`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11 with the checkout-owned Python 3.13
  `.venv`; final-head CI on Ubuntu and Windows.

## Prerequisites and test data

Disposable fixtures only: temporary SQLite databases and Flow folders, and
fictional Flows. The lifespan case stubs recovery, scheduler and worker start,
as the existing lifespan tests do.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| S-01 | Run the app lifespan with the Flow refresh slowed down; a startup repair commits a Flow change | Lifespan yields (web opens) while the refresh is still running; the startup commit does not refresh inline; one background pass covers it | `test_auto_update.py::test_lifespan_opens_web_before_flow_files_are_refreshed` |
| S-02 | Hold refreshes, rename a Flow, then catch up in the background; rename it again afterwards | Files unchanged while held; the background pass writes the first name; the later save refreshes inline as before | `test_flow_handover.py::test_held_commits_are_refreshed_in_background_then_inline_again` |
| S-03 | Save a Flow while the background pass is still running | The save returns without refreshing on its thread; the pass runs a second time and the files show the saved name | `test_flow_handover.py::test_save_during_background_refresh_does_not_wait_and_is_refreshed` |
| D-01 | Drop `scan_runs.changed_queries` from an initialized database, then run `init_db()` | The column is back; rows can write and read it | `test_query_history.py::test_startup_restores_counter_dropped_by_the_retired_removal` |
| R-01 | Existing lifespan, update-drain and Flow handover behavior | Unchanged | remaining `test_auto_update.py` and `test_flow_handover.py` |

## Automated checks

```powershell
python tools/check.py verify `
  --test tests/test_query_history.py::test_startup_restores_counter_dropped_by_the_retired_removal `
  --test tests/test_flow_handover.py --test tests/test_auto_update.py `
  --syntax app/main.py --syntax app/flow_handover.py --syntax app/database.py
```

Required final-head CI (`Merge ready`) is the full regression.

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
Rollback: revert the PR. The re-added column is harmless to older code, which
reads and writes it. Nothing to clean up.
