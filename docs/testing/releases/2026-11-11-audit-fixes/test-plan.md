# 2026-11-11: Audit fixes for Flows, scanners and access: test plan

- Change/PR: link in the PR's testing section. Fixes for the findings of the
  11 November 2026 audit of Flows, the scanners and credential handling. The
  findings keep their audit numbers: F for Flows, S for scanners, C for
  credentials and access.
- Code baseline: `main` at `7e406f2`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv`; the pull request's CI for the browser tests, PostgreSQL 14 and
  18 and the full regression

## Prerequisites and test data

No live system. Every test builds its own temporary app database, Flow
folders, report folders and settings file. PostgreSQL, Power BI, the portals,
Outlook and the browser are stand-ins written in the test files, so every
result below is synthetic. A request "from another computer" is a test client
with a non-local peer address; "the host PC" is the default test client.

## Test cases

One row per finding. Each test name starts with the finding's number unless
the evidence column says otherwise.

### Flows: scheduler, API and SQL retries (`tests/test_audit_flows_api.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| F-01 | Two due Flows, one on a start week that does not exist or on an unknown adapter; run the scheduler tick | The bad Flow is skipped with its reason and the other is queued; saving such a Flow or website is refused; a manual run answers 409 | `test_f01_*` (8) |
| F-03 | Press Retry SQL on a run whose SQL committed, on an older run after a newer one committed, and twice on one file | Each is refused; a run with nothing committed since can still be retried | `test_f03_*` (4) |
| F-04 | A Flow that failed unassigned is assigned to a person without SQL credentials, then to one with them; Retry SQL and Retry views | The retry fails closed without credentials, signs in as today's owner, never as the previous owner; a still unassigned Flow keeps the shared account | `test_f04_*` (5) |
| F-08 | A due Flow whose materialized-view refresh plan is blocked; Resume on such a run | The scheduler skips it and Resume is refused; two Flows refreshing one view are still queued | `test_f08_*` (4) |
| F-17 | Switch a Flow with an Email step to "Just run the scripts" | The Email step is cleared; a Flow that produces a file keeps an omitted Email step | `test_f17_*` (2) |
| F-21 | Build the failure email and the file email for a stored UTC instant | Times are shown in Dubai time with the zone named | `test_f21_*` in both Flow test files (4) |
| F-23 | Rename a website onto a taken name; save a report onto a taken name; partial SQL catalog scan | 409 instead of 500; tables of the database that could not be read are not marked stale | `test_f23_*` (5) |
| C-02 | From another computer: save the portal sign-in, add or change an ASAP or GSCM website | Refused with 403; other websites stay open but cannot be turned into a portal | `test_c02_*` (4) |

### Recording, replay and Power BI exports (`tests/test_audit_replay_recording.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| F-02 | Two Flows on one report with different filter selections; store a replay recipe for one, look it up for the other | Never shared; the same selections in another order share one key; a recipe stored under the old key is not found | `test_f02_*` (6) |
| F-13 | Copy a recording that has a range step; copy a plain one; replicate a Flow | The version is kept and the copy succeeds | `test_f13_*` (3) |
| F-14 | Export a visual that hits the row limit, then one that does not | The cut-off is named near the top of the email and on the run, with the real limit; delivery can be refused; a complete export carries no notice | `test_f14_*` (4), `tests/test_audit_replay_recording_display.mjs` |
| F-15 | A filter level that cannot be read during the Power BI fallback | The export fails closed and names the level; nothing is queried under fewer filters and an alert run sends no data | `test_f15_*` (6) |
| F-16 | Cancel or reject a recording; crash the recorder; start a session after a killed worker; run four validations | No raw script or sign-in state is left; a crash keeps the log it points to; only the newest three validation folders remain; a failed delete is a warning | `test_f16_*` (5) |
| F-18 | A GSCM bookmark row that moves before the click; a folder with the bookmark's name | The moved row is not clicked; the row is read again before the click; a folder is never clicked | `test_f18_*` (4) |
| F-22 | The "not signed in" error text | Carries no portal address | `test_f22_*` (1) |
| F-23a to F-23d | Dataset bookmarks named like portal chrome; a week-box scan that never reaches the last screen; a navigation URL with a credential parameter; a date slider past 1,000 days | Kept; fails closed; refused; travels to the cap | `test_f23a_*` to `test_f23d_*` (6) |

### Flow worker (`tests/test_audit_worker.py`, `tests/test_audit_worker_csv.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| F-05 | The worker's page is closed or crashed, then work arrives; the relaunch fails | The browser is relaunched; a failed relaunch fails only that work with a clear message | `test_worker_relaunches_the_browser_after_its_page_died`, `test_a_failed_relaunch_fails_only_that_work_with_a_clear_message` |
| F-06 | A sign-in failure whose browser error repeats the typed password | The password is in no error, report, log line or email text | `test_sign_in_failure_never_carries_the_typed_password`, `test_every_report_is_scrubbed_of_a_password_the_worker_typed` |
| F-07 | The app is unreachable for two minutes during a run; then after the SQL commit | Reports outlast the restart; a committed load is not reported as failed | `test_run_reports_outlast_a_service_restart`, `test_sql_load_stays_successful_when_the_committed_report_cannot_be_sent`, `test_a_committed_run_is_not_failed_by_reports_lost_after_the_commit` |
| F-09 | A catalog scan whose loading overlay never clears | That report fails after the bound; the scan continues | `test_catalog_scan_fails_a_report_whose_overlay_never_clears` |
| F-10 | Header detection on a large export, old and new implementation side by side | Same answer, linear time | `tests/test_audit_worker_csv.py` (7) |
| F-11 | A coordinator waiting on helpers with unchanged progress | A change is posted at once, otherwise one keepalive every 30 seconds | `test_waiting_coordinator_posts_changes_at_once_and_otherwise_a_keepalive` |
| F-22 | Sign-in bootstrap without an adapter argument | The portal type comes from the saved website with the same host | `test_bootstrap_adapter_comes_from_the_saved_website_with_the_same_host` |

### Scanner and its schedule (`tests/test_audit_scanner.py`, `tests/test_audit_scanner_schedule.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| S-01 | Archive a PostgreSQL source, scan, restore it; two active rows with one identity | Every scan keeps working; restoring onto an active identity is refused with 409; an active source wins over an archived twin | `test_archive_then_restore_*`, `test_restoring_a_source_*`, `test_restore_refusal_*`, `test_scan_uses_the_lowest_source_*`, `test_an_active_source_wins_*` |
| S-02 | The daily refresh starts six hours late; is missed while the service was down; is refused by an update drain; raises | It runs late, is caught up after start-up once, and is retried until its original deadline | `test_daily_refresh_*`, `test_missed_daily_refresh_*`, `test_start_up_*`, `test_retry_keeps_*`, `test_accepted_refresh_*` (9) |
| S-03 | An install fails; the watcher ticks again | No relaunch until the retry wait passes; a new commit or a manual update launches at once | `test_failed_install_*`, `test_retry_wait_*`, `test_rolled_back_install_*`, `test_stale_install_*`, `test_new_main_commit_*` (5) |
| S-05 | A model with calculated and "Enter data" tables | They are not broken references; an unmatched real source still is | `test_calculated_tables_*`, `test_unmatched_real_source_*` |
| S-06 | Back up while commits sit in the write-ahead log and while another connection writes | The backup holds those commits and finishes; earlier backups are kept unless a keep count is given; a failed backup prunes nothing | `test_backup_*` (5), `test_scan_backups_are_ignored_by_git` |
| S-07 | An unrecognised Power Query expression holding a password | Logged without its text | `test_unknown_tmdl_source_*`, `test_unknown_pbix_source_*` |
| S-08 | A report with an unparseable server name; a non-UTF-8 expressions file | Only that report is skipped | `test_unparseable_postgres_server_*`, `test_scan_survives_*`, `test_non_utf8_expressions_file_*` |
| S-09 | A long folder walk; Stop during discovery; a stall email followed by a failure | Heartbeat and progress per report; Stop takes effect before the next report; the failure email still follows, once | `test_walker_reports_progress_*`, `test_discovery_heartbeats_*`, `test_stop_during_discovery_*`, `test_module_stall_threshold_*`, `test_*_failure_email_follows_a_stall_email_once` |
| S-18 | A live-connection `.pbix` with no data model | Catalogued from its layout; never replaces a TMDL model; an unreadable model stays unparsed | `test_live_connection_pbix_*`, `test_pbix_with_an_unreadable_data_model_*` |
| S-19 | An unknown scan run id; `open-path` from another computer; a report's last measure removed | 404; 403 and nothing is opened or probed; the measure is removed unless the list was unreadable | `test_unknown_scan_run_is_a_404`, `test_only_the_host_pc_can_open_a_folder`, `test_scan_removes_*`, `test_scan_keeps_measures_*` |

### Freshness probes, alerts and usage (`tests/test_audit_probes.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| S-04 | A Power BI call for refresh history or schedule fails | Stored values and an open "refresh failed" alert are kept; no overdue alert is opened from missing data | `test_s04_*` (6) |
| S-10 | Probe and alert messages for a stored UTC instant | Dubai time in the text, UTC stored | `test_s10_*` (5) |
| S-11 | A data-quality run with a second writer | The SQLite write lock is released before each PostgreSQL query | `test_s11_*` (1) |
| S-12 | Manual materialized-view refresh; one that times out | Lock and statement timeouts are set first; a timeout is explained | `test_s12_*` (3) |
| S-13 | A timed rule in its grace period with no evidence | Unknown, not fresh; every other outcome unchanged | `test_s13_*` (3) |
| S-14 | A freshness probe on a view and a foreign table | A row count is recorded without commit timestamps | `test_s14_*` (1) |
| S-16 | A usage day cut off at the page limit; a CSV import after an API sync | Not marked synced; API days and rows are kept | `test_s16a_*`, `test_s16b_*` (3) |
| S-17 | A report listed in the workspace whose dataset is elsewhere | Not archived | `test_s17_*` (3) |
| S-19 | Proxy credentials in connectivity output; the pg_cron history query | Never shown; bounded and read-only | `test_s19a_*` to `test_s19c_*` (5) |
| F-23 | Alert and health trends for events in the small hours | Counted on their Dubai day | `test_f23_*` (2) |

### Access to the host PC's own actions (`tests/test_audit_access.py`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| C-01 | Save a value holding each of ten line-break characters; read a file that already holds one | Refused with the file untouched; reading never splits a value | `test_a_value_with_any_line_break_*`, `test_reading_never_splits_*`, `test_windows_line_endings_*`, `test_saving_an_sql_password_with_a_line_break_is_refused` |
| C-07 | Requests with this PC's names and addresses, another site's name pointed at this PC, a cross-site origin, a forwarded request | Only a direct same-site request to one of this PC's own names is the host PC; `localhost` and `127.0.0.1` both count; no DNS lookup is made | `test_only_this_pcs_own_names_*`, `test_another_sites_name_*`, `test_local_request_accepts_localhost_*`, `test_require_host_refuses_*`, `tests/test_user_identity.py` |
| C-08 | AI "test" and "list models" from another computer | 403 | `test_ai_connection_tests_are_refused_from_another_computer` |
| C-04 | From another computer: install an update, switch automatic updates, connect or disconnect Power BI, change scan failure recipients; remote Flow control from a cross-site page on the host PC; change another person's email mapping | Each is refused with 403 and nothing changes; the host PC, and a person for their own mapping, succeed; the read-only views stay open | `test_changes_that_belong_to_the_host_pc_*`, `test_remote_flow_control_needs_more_than_an_address_on_this_pc`, `test_a_persons_email_mapping_*` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_audit_access.py --test tests/test_audit_flows_api.py \
  --test tests/test_audit_probes.py --test tests/test_audit_replay_recording.py \
  --test tests/test_audit_scanner.py --test tests/test_audit_scanner_schedule.py \
  --test tests/test_audit_worker.py --test tests/test_audit_worker_csv.py \
  <the existing suites for the changed modules, listed in the report> \
  --syntax app/main.py --syntax app/flow_worker.py --syntax app/routers/flows.py \
  --syntax app/routers/scanner.py --syntax app/local_access.py \
  --syntax app/scanner/prober.py --syntax app/scanner/runner.py --syntax app/flow_replay.py
for f in tests/test_*.mjs; do node "$f"; done
```

Tests that launch a browser cannot run in the cloud container (see the
report); the pull request's Python shards run them. Required final-head CI is
the full regression, including PostgreSQL 14 and 18.

## Acceptance and cleanup

Accepted when every case passes and `Merge ready` passes on the final head.
The tests leave nothing behind outside their temporary folders.

Not covered by any test: a real portal, a real Power BI tenant, a real
PostgreSQL server holding business data, Outlook, and Chrome being killed
while a worker runs. The report lists every finding that is not fixed and why.
