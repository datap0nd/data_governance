# 2026-11-11: Faster updates and bounded service logs: test plan

- Change/PR: link in the PR's testing section. An automatic update skips work
  that has not changed, stops services side by side, extracts faster and no
  longer repeats itself when `setup.ps1` changed. Service logs rotate while a
  service runs, Metronome deletes old ones, and the request log stops
  recording polling.
- Code baseline: `main` at `bd09c94`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv` for the text contracts and Python tests; the pull request's
  `windows-latest` job for every case that needs Windows PowerShell

## Prerequisites and test data

No live system. Log folders, update folders and the app database are temporary
directories created by each test. PowerShell cases run helper functions from
`tools/setup_fast_path.ps1` against temporary files; no service, scheduled
task or ACL is touched. `setup.ps1` itself is never executed by a test.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| L-01 | Log folder with rotated and plain `.log` files of different ages; `prune(keep_days=14)` | Files not written to for 14 days are deleted, newer ones kept | `tests/test_log_housekeeping.py::test_logs_nobody_wrote_to_for_the_keep_period_are_deleted` |
| L-02 | 18 MB of logs, 12 MB limit, one active log | Rotated logs go oldest first until the folder fits; the active log stays | `...::test_size_limit_removes_rotated_logs_oldest_first_and_never_an_active_log` |
| L-03 | An active log bigger than the limit | Reported as over the limit, not deleted | `...::test_an_active_log_larger_than_the_limit_is_reported_not_deleted` |
| L-04 | Folder also holds a `.txt`, a `.db`, a sub-folder and a symlink to a log outside | Only `*.log` files directly in the folder are candidates; the link and its target survive | `...::test_only_log_files_directly_in_the_folder_are_considered` |
| L-05 | Saved limits of 3 days and 5 MB, then a non-number | 3 days applies; 5 MB is raised to the 50 MB minimum; a bad value falls back to 14 days | `...::test_run_uses_the_saved_limits_and_clamps_them` |
| L-06 | 25 update transcripts older than the keep period | The newest 20 stay | `...::test_update_transcripts_keep_the_newest_twenty` |
| L-07 | Access records for page loads, worker polling, a saved Flow, a 404 and a 500, in each mode | `changes` keeps failures and changes; `errors` keeps failures; `all` keeps everything; an unknown mode means `changes`; an unrecognised record passes | `...::test_request_log_keeps_failures_and_changes_and_drops_polling`, `...::test_request_log_passes_a_record_it_does_not_recognise` |
| L-08 | One identical error ten times in a minute, then again after a quiet minute | Three written (the test's burst), seven dropped, and the next line says seven were dropped | `...::test_an_identical_message_is_written_a_few_times_then_counted` |
| L-09 | `GET /api/system/logs`, then `POST /api/system/logs/prune` from a colleague's browser and from the host PC | Anyone sees usage; the colleague gets 403 and nothing is deleted; the host PC deletes and the Event Log records it | `...::test_anyone_sees_log_usage_but_only_the_host_pc_cleans_up` |
| S-01 | Read `setup.ps1` | Every service sets `AppRotateOnline 1`, `AppRotateSeconds 0` and the shared 10 MB size; `86400` is gone | `tests/test_setup_fast_path.py::test_logs_rotate_while_a_service_runs_not_only_after_a_quiet_day` |
| S-02 | Read `setup.ps1` | A relaunched copy skips download, stop and extract only when the archive is on disk; the variables those stages defined exist on both paths; VERSION can be stamped from the requested commit | `...::test_a_relaunched_setup_resumes_after_extraction` |
| S-03 | Read `setup.ps1` | Every skip requires `-Unattended`; without the helper file or a fingerprint a step is "not current"; `Expand-Archive` remains as the fallback | `...::test_fast_path_only_applies_to_unattended_updates_and_fails_towards_full_work` |
| S-04 | Read `setup.ps1` | The side-by-side stop request sits in the relaxed error region before the per-service stops, and the 30-second abort is unchanged | `...::test_services_are_asked_to_stop_together_before_the_per_service_waits` |
| S-05 | Read `tools/apply_update.ps1` and `setup.ps1` | The bridge compares VERSION's commit prefix with the start of the target, not its end | `...::test_bridge_accepts_the_commit_prefix_setup_stamps` |
| S-06 | Fixture repository with this `.gitattributes`; `git archive` | Nothing under `docs/testing/` is in the archive; `docs/flow_standalone.md`, `tests/`, `tools/setup_fast_path.ps1` and `setup.ps1` are | `...::test_update_archive_leaves_out_release_test_packages`, `...::test_the_app_reads_no_document_the_archive_leaves_out` |
| W-01 | Windows: parse every `*.ps1` in the repository root and `tools/` with Windows PowerShell | No parse error in any script | `...::test_powershell_parses_every_script` (17 scripts) |
| W-02 | Windows: fingerprints of equal, different and re-joined inputs; of files before and after an edit and a removal | Stable for equal input, different otherwise | `...::test_fingerprints_are_stable_and_sensitive`, `...::test_file_fingerprint_follows_content_and_presence` |
| W-03 | Windows: record two steps, read them back, then damage the state file | Round trip is exact and case-sensitive; a damaged file reads as empty and is replaced on the next write | `...::test_step_state_round_trips_and_survives_a_damaged_file` |
| W-04 | Windows: extract a zip into a folder that already holds a leftover from a failed run | Only the archive's content is there afterwards | `...::test_archive_extraction_matches_the_archive_and_replaces_a_leftover_folder` |
| W-05 | Windows: compare a task object with the intended program, arguments and folder | Reused only on an exact match; a missing task or a task with two actions is not | `...::test_task_is_reused_only_when_its_command_is_exactly_the_intended_one` |
| W-06 | Windows: the bridge's version rule on a 9-character stamp, an upper-case stamp, a full stamp, another commit and no commit | True for the first three, false for the rest | `...::test_bridge_version_rule_accepts_a_prefix_and_rejects_another_commit` |
| R-01 | Existing installer and updater tests | Unchanged behaviour still passes | The 20 `setup.ps1` tests listed in the report, `tests/test_auto_update.py`, `tests/test_unattended_update_scripts.py` |
| N-01 | The new `setup.ps1` text contracts run against `setup.ps1`, `.gitattributes` and `tools/apply_update.ps1` from `bd09c94` | They fail | Console output in the report |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_log_housekeeping.py --test tests/test_setup_fast_path.py \
  --test tests/test_unattended_update_scripts.py --test tests/test_setup_worker_health.py \
  --test tests/test_setup_sso_password.py --test tests/test_auto_update.py \
  --test tests/test_auditor_managed.py --test tests/test_env_file.py \
  --test tests/test_flow_capacity.py --test tests/test_flow_desktop_session.py \
  --test tests/test_flow_worker_startup.py --test tests/test_version_check.py \
  <the ten setup.ps1 node ids in tests/test_flows.py and tests/test_gscm.py> \
  --syntax app/log_housekeeping.py --syntax app/logging_setup.py \
  --syntax app/routers/system_logs.py --syntax app/main.py
```

W-01 to W-06 need Windows PowerShell and are skipped on Linux. The pull
request's `Windows verifier contracts` job runs them, together with the
Windows-only cases already in `tests/test_unattended_update_scripts.py` and
`tests/test_setup_worker_health.py`; this change adds those three files to
that job. Required final-head CI is the full regression.

## Acceptance and cleanup

Accepted when every case passes, including W-01 to W-06 on the pull request's
Windows job, and `Merge ready` passes on the final head.

Not covered by any test: `setup.ps1` running end to end, nssm's rotation
behaviour, and how long an update takes on the work PC. The `[timing]` lines
in `updates\logs\<attempt>.log` are the measurement after the first update
with this change.

Rollback: revert the PR. An update with the reverted `setup.ps1` re-applies
the previous service settings because the recorded fingerprints include the
script. `updates\setup_state.json` can be deleted at any time; the next update
then does every step in full. Logs already deleted are not restored.
