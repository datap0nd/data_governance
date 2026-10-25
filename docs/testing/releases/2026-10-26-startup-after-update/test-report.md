# 2026-10-26: Metronome starts promptly after an update: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-25 21:05
- Tested code revision: working tree on `c4095ae` with exactly the
  application and test changes committed as `9ea8815`
- Environment: Windows 11 Pro 10.0.26200, Python 3.13.15 checkout-owned
  `.venv` with the locked CI dependencies
- Overall finding: every case in the plan passes locally (automated). The
  wider affected set has 11 `test_flow_handover.py` failures that are
  identical on unchanged `main` on this PC (below). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| S-01, S-02, S-03, D-01, R-01 (lifespan) | `python tools/check.py verify --test tests/test_query_history.py::test_startup_restores_counter_dropped_by_the_retired_removal --test tests/test_flow_handover.py::test_held_commits_are_refreshed_in_background_then_inline_again --test tests/test_flow_handover.py::test_save_during_background_refresh_does_not_wait_and_is_refreshed --test tests/test_auto_update.py::test_lifespan_opens_web_before_flow_files_are_refreshed --test tests/test_auto_update.py::test_lifespan_reconciles_and_drains_active_update_before_runtime_start --test tests/test_auto_update.py::test_lifespan_keeps_web_available_when_optional_startup_repairs_fail --syntax app/main.py --syntax app/flow_handover.py --syntax app/database.py` | `9ea8815` content, Windows above | PASS: 6 tests, 0 skipped, 2 third-party deprecation warnings, 11.0 s; syntax clean | `.test-runs/20261025T210131766Z-32472-88658dea/result.json` |
| R-01 | `python tools/check.py verify --test tests/test_query_history.py::test_startup_restores_counter_dropped_by_the_retired_removal --test tests/test_flow_handover.py --test tests/test_auto_update.py --syntax app/main.py --syntax app/flow_handover.py --syntax app/database.py` | `9ea8815` content, Windows above | 70 passed, 11 failed (all in `test_flow_handover.py`, listed below), 0 skipped, 98.3 s | `.test-runs/20261025T205918836Z-18984-b244198e/result.json` |
| Baseline for the 11 | `python tools/check.py verify --test tests/test_flow_handover.py` with the application and test changes stashed | `c4095ae` (unchanged `main`), Windows above | The same 11 failed, 3 passed, 29.0 s | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

Eleven existing `test_flow_handover.py` tests fail on this Windows PC both
with this change and on unchanged `main`: the Flow file sync raises
`FileNotFoundError`, so the handover state is `error` instead of `current`.
This PC has Windows long paths disabled (`LongPathsEnabled=0`) and the tests
write deep paths under the per-run temporary root, which is consistent with
the 260-character path limit; it was not investigated further. The affected
tests are:
`test_create_has_complete_json_script_and_operator_instructions`,
`test_python_runs_in_isolation_without_installed_app_or_server`,
`test_owner_schedule_and_shared_changes_refresh_without_export`,
`test_draft_always_has_python_and_saved_definition`,
`test_generation_failure_preserves_database_and_recovers_automatically`,
`test_modified_script_archived_then_refreshed_and_missing_script_recreated`,
`test_source_catalog_edits_refresh_json_and_script`,
`test_outlook_portable_embeds_and_materializes_its_helper`,
`test_legacy_installed_launcher_is_upgraded_and_archived`,
`test_existing_operator_notes_and_dependencies_are_archived_before_refresh`,
`test_status_detects_old_files_when_failed_share_write_could_not_record_error`.
Final-head CI runs them on Ubuntu.

The two existing lifespan tests now join the background Flow refresh their
startup begins, so its thread cannot outlive the test's temporary database.
Their assertions are unchanged.

The cause of the slow startup is inferred from the code and the work PC's
symptoms reported in the task (connection refused on port 8000 for the whole
ten-minute registration window, then normal traffic later), not from timed
work-PC logs. On this PC, hashing the shipped Flow code costs about 0.46 s per
Flow on every refresh, before any file is written. A refresh after an update
also rewrites every Flow's files.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
