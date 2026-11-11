# 2026-11-11: Settings page, connections and passwords in the app: test plan

- Change/PR: link in the PR's testing section. One **System > Settings** page
  for the app-wide connections, folders, schedules and limits that used to be
  fixed in code or in environment variables. Everyone can read it; only the
  host PC can change it.
- Code baseline: `main` at the commit named in the report
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv`; a temporary Metronome instance and Chromium for the page walk;
  the pull request's CI for the full regression

## Prerequisites and test data

No live system. Each test builds a temporary app database and, where needed, a
temporary settings file (`.env`). PostgreSQL, Windows data protection, the
scheduler, the portals and the worker's browser are stand-ins written in the
test files, so every result is synthetic. "Another computer" is a request with
a non-local peer address or a request object marked as not from the host.
The page walk uses a temporary instance with an empty database and fictional
values.

## Test cases

### The registry and the page's API (`tests/test_settings_registry.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| R-01 | Load every declared setting | Each has a known section, kind and apply rule, a label, and code that reads it | `test_every_setting_is_well_formed_and_sits_in_a_known_section`, `test_no_setting_is_declared_without_code_that_reads_it` |
| R-02 | Submit out-of-range, fractional and wrong-shape values | Refused with a message naming the allowed range or shape | `test_numbers_are_whole_in_range_and_explain_the_range`, `test_other_kinds_accept_only_their_own_shape` |
| R-03 | A value saved in the app, the same value in an environment variable, and neither | Saved beats environment beats default; an unusable stored or environment value does not break reading; an unreadable database falls back to defaults | `test_saved_value_beats_environment_which_beats_the_default`, `test_unusable_environment_and_stored_values_do_not_break_reads`, `test_a_database_that_cannot_be_read_falls_back_to_defaults` |
| R-04 | Save several values, one of them invalid | Nothing is saved and each problem is named | `test_one_bad_value_saves_nothing_and_names_each_problem`, `test_a_rejected_save_answers_with_a_message_per_field_and_saves_nothing` |
| R-05 | Save values that existed before this page (worker limits, schedules, weekly insights) | They keep their existing storage, so the older pages and this one show the same value | `test_settings_that_already_existed_keep_their_storage`, `test_composite_settings_read_and_write_through_their_existing_store` |
| R-06 | Save, then read the Event Log | One entry per changed setting with old value, new value and who | `test_save_reports_only_what_changed_and_tells_listeners`, `test_the_host_pc_saves_and_each_change_is_in_the_event_log` |
| R-07 | Read and save from another computer | Reading works and says the page is read-only; saving is refused with 403 and nothing changes | `test_anyone_reads_settings_but_only_the_host_pc_is_told_it_can_edit`, `test_a_colleague_cannot_save_and_nothing_changes`, `test_only_the_host_pc_changes_where_flows_keep_their_files` |
| R-08 | Change a setting an existing feature reads | The feature follows it without a restart | `test_scheduled_email_gate_follows_the_setting_without_a_restart`, `test_pipeline_scan_age_follows_the_setting`, `test_snapshot_reads_several_settings_together` |

### Connections, passwords and the settings file (`tests/test_settings_connections.py`, `tests/test_config_refresh.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| K-01 | Save a database host on the page | It is written to the settings file and is in force at once in the configuration and in every module that had imported the old value | `test_a_saved_connection_reaches_the_file_config_and_importing_modules_at_once`, `tests/test_config_refresh.py` (7) |
| K-02 | Reset a value, or save it empty | Its line is removed and the value falls back; the page names where each value comes from | `test_reset_and_empty_remove_the_line_and_fall_back`, `test_where_a_value_comes_from_is_named`, `test_folders_write_the_name_that_wins_and_show_the_folder_in_force` |
| K-03 | Replace and clear a password | Written and applied; never in a response, the page data or the Event Log; cannot be saved with a line break or together with ordinary settings | `test_a_password_is_written_applied_and_never_returned_or_logged`, `test_passwords_cannot_be_saved_with_the_other_settings_or_with_a_line_break` |
| K-04 | Change a password or run a connection test from another computer | Refused with 403 | `test_only_the_host_pc_changes_passwords_or_tests_connections` |
| K-05 | Save a connection on a computer with no settings file | Nothing is saved and the message says to run setup once | `test_without_a_settings_file_nothing_is_saved_and_the_reason_is_given` |
| K-06 | Test the probe connection with an account that can write, then with a wrong password | The test fails for an account that can write; a failed sign-in is explained without the password | `test_probe_test_reads_only_and_refuses_an_account_that_can_write`, `test_a_failed_test_explains_itself_without_the_password` |
| K-07 | Save and clear the portal sign-in from the host PC and from another computer; save where Windows protection is missing | Host only; the Event Log carries neither user ID nor password; a store that cannot keep it says why | `test_the_portal_sign_in_is_saved_and_cleared_from_the_host_pc_only`, `test_a_portal_sign_in_that_cannot_be_stored_says_why`, `test_clearing_the_real_credential_file_removes_it` |
| K-08 | Stop a save right after the settings file is cut to length | The file holds every setting, never nothing | `test_a_save_that_stops_half_way_never_leaves_an_empty_settings_file` |
| K-09 | Save the AI key with Windows protection present, absent, failing, and from another account; start up with a key saved earlier | Stored encrypted and still used; plain where protection is absent; a key another account saved reads as not set; an earlier key is encrypted at start-up with its saved time unchanged | `tests/test_ai_key_protection.py` (6) |

### Scanner, freshness, update and backup settings (`tests/test_settings_scanner.py`, `tests/test_settings_scanner_schedules.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| N-01 | Read all 21 settings with nothing saved, with the older environment variables set, and with the registry unreadable | Each default is the value the code used before; an environment value is kept; a setting that cannot be read never stops the work; every row has a plain label and help text | `test_default_is_the_value_the_code_used_before` (both files), `test_constants_read_from_the_environment_agree_with_the_plain_defaults`, `test_values_set_through_the_old_environment_variables_are_kept`, `test_a_setting_that_cannot_be_read_never_stops_the_work`, `test_every_row_explains_itself_to_someone_who_is_not_a_developer`, `test_these_rows_are_plain_and_share_their_groups` |
| N-02 | Save a folder depth, folder names to skip and a largest `.pbix` size, then walk a report folder | Deeper folders and skipped names are left out; a skipped name is a folder name, not a path; an oversized `.pbix` is left unread with its reason and the scan still finishes | `test_folder_depth_follows_the_setting`, `test_skipped_folder_names_are_left_out_wherever_they_sit`, `test_a_skipped_name_is_a_folder_name_not_a_path`, `test_a_pbix_over_the_size_limit_is_left_unread_with_its_reason`, `test_a_scan_finishes_and_says_why_a_large_pbix_was_not_read` |
| N-03 | Save a stalled threshold | The same value drives the Scanner page badges and the stalled email | `test_one_stalled_threshold_drives_the_badges_and_the_email` |
| N-04 | Save probe connect, lock and statement limits, then open a probe and a data-quality session | Both sessions use the saved limits and stay read-only | `test_probe_sessions_use_the_saved_timeouts_and_stay_read_only`, `test_data_quality_checks_open_their_session_with_the_same_limits` |
| N-05 | Save the Power BI request wait, activity pages per day, usage days and sync wait | Requests, the usage sync (saved account and script) and the scan's wait follow them | `test_power_bi_requests_wait_for_the_saved_time`, `test_activity_pages_read_per_day_follow_the_setting`, `test_usage_sync_with_the_saved_account_looks_back_the_saved_days`, `test_usage_sync_script_is_told_the_days_only_when_they_differ_from_its_own`, `test_a_scan_waits_the_saved_time_for_a_power_bi_sync` |
| N-06 | Save the grace after a scheduled time, the alert level, the report lag and the history kept | Freshness status, new alerts and the history clean-up follow them | `test_grace_after_a_scheduled_time_follows_the_setting`, `test_alert_level_for_an_outdated_source_follows_the_setting`, `test_how_far_a_report_may_lag_its_source_follows_the_setting`, `test_freshness_history_is_kept_for_the_saved_number_of_days` |
| N-07 | Save the daily backup time, the number of backups kept, the update check interval and the wait after a failed install | The backup and update jobs are rescheduled at once in Dubai time; all backups are kept until a number is saved; a failed install waits the saved time | `test_daily_backup_moves_to_the_saved_dubai_time_at_once`, `test_backups_are_all_kept_until_a_number_is_saved`, `test_update_check_interval_follows_the_setting_and_is_rescheduled_at_once`, `test_a_looked_up_version_is_reused_for_the_saved_interval`, `test_wait_after_a_failed_install_follows_the_setting` |
| N-08 | Save the daily refresh time, how late it may start, its retry interval and the weekly insights settings on the Settings page | The jobs are rescheduled or removed at once; listeners are registered once; a job that cannot be rescheduled never fails the save | `test_saving_the_daily_refresh_time_reschedules_the_job`, `test_how_late_the_daily_refresh_may_start_follows_the_setting`, `test_a_refused_daily_refresh_retries_at_the_saved_interval`, `test_saving_the_weekly_insights_settings_reschedules_or_removes_the_job`, `test_listeners_are_registered_once_however_often_start_up_runs`, `test_a_job_that_cannot_be_rescheduled_never_fails_the_save` |
| N-09 | Change the daily refresh time, the pipeline insights settings and the full pipeline refresh settings through their older routes, from another computer and from the host PC | Refused with 403 and nothing changes; accepted from the host PC; reading stays open | `test_older_settings_routes_refuse_another_computer_and_change_nothing`, `test_older_settings_routes_still_work_from_the_host_pc`, `test_the_same_rule_holds_when_the_routes_are_called_directly` |

FLOW_CASES

## Page walk

A temporary instance on `127.0.0.1`, Chromium driven by Playwright, desktop
(1360 px) and phone (390 px) widths:

1. Open every section; each row has a label, a control or state, and says when
   a change applies; no sideways scrolling.
2. In each section change one number: an out-of-range value is refused beside
   the field with the allowed range and the other edits are kept; a valid
   value saves, survives a reload and shows "Set here"; **Reset to default**
   restores it.
3. Replace and clear a password; run a connection test that fails.
4. Search across sections; show only changed settings.
5. Open the page as another computer: a notice says it is read-only and no
   control can be used.

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_settings_registry.py --test tests/test_settings_connections.py \
  --test tests/test_config_refresh.py --test tests/test_ai_key_protection.py \
  <the scanner and Flow settings tests and the existing suites listed in the report> \
  --syntax app/settings_registry.py --syntax app/settings_catalog.py \
  --syntax app/routers/system_settings.py --syntax app/config.py --syntax app/env_file.py
for f in tests/test_*.mjs; do node "$f"; done
for f in app/static/*.js; do node --check "$f"; done
```

Required final-head CI is the full regression.

## Acceptance and cleanup

Accepted when every case passes, the page walk shows no problem and
`Merge ready` passes on the final head. The temporary instance and its files
are removed afterwards.

Not covered by any test: Windows data protection itself, a real PostgreSQL
sign-in, a worker process on the work PC picking up a changed connection, and
the scheduler on the work PC.
