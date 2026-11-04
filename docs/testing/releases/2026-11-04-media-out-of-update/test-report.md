# 2026-11-04: Film removed, media/ kept out of updates, quiet setup: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-04 09:17
- Tested code revisions: `e4aa4d7` (film removal, `.gitattributes`, archive
  test) and `fb72d6d` (adds the `setup.ps1` progress setting and its test);
  this report is committed after them
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv` with the locked CI dependencies, git 2.43.0
- Overall finding: every case in the plan passes locally (automated,
  synthetic fixture). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| M-01, R-01 | `python tools/check.py verify --test tests/test_unattended_update_scripts.py` | `e4aa4d7`, Linux above | PASS: 8 passed, 0 failed, 1 skipped (the Windows scheduled-updater test), 1.4 s | `.test-runs/20261104T091201925Z-638-6bc98df8/result.json` |
| M-02 | The new test run with `.gitattributes` emptied, then restored | Same code, Linux above | 1 failed with the rule removed; 1 passed with it restored | Console output |
| M-03 | `git archive --format=zip HEAD` | `e4aa4d7` | 12.6 MB, 1,055 entries, none under `media/`. The same archive of `main` at `f0defc8` is 70.5 MB | Console output |
| M-01, P-01, R-01 | `python tools/check.py verify --test tests/test_unattended_update_scripts.py --test tests/test_auto_update.py` plus the 20 `setup.ps1` test ids below | `fb72d6d`, Linux above | PASS: 91 passed, 0 failed, 5 skipped, 2 third-party deprecation warnings, 13.6 s | `.test-runs/20261104T091552322Z-1250-47419826/result.json` |
| P-02 | The new progress test run against `setup.ps1` from `e4aa4d7` | `fb72d6d` test, earlier `setup.ps1` | 1 failed, as expected | Console output |

The 20 `setup.ps1` tests in R-01: `test_flows.py` (`test_setup_installs_headless_flow_worker_service`,
`test_setup_does_not_require_visible_asap_login_without_encrypted_credential`,
`test_setup_bounds_stale_port_process_cleanup`,
`test_setup_merges_new_nested_files_without_purging_local_files`,
`test_setup_registers_on_demand_interactive_headed_worker`,
`test_setup_stops_headed_worker_before_replacing_runtime_code`,
`test_setup_downloads_update_before_stopping_running_services`,
`test_setup_waits_for_headless_worker_to_stop_before_replacing_code`,
`test_setup_fails_closed_when_python_dependencies_cannot_install`);
`test_gscm.py::test_setup_registers_gscm_before_reading_the_sign_in_list`;
`test_auditor_managed.py::test_setup_installs_loopback_virtual_reader_and_denies_app_database`;
`test_env_file.py::test_template_and_setup_contract`;
`test_flow_capacity.py::test_installer_slot_profiles_and_ids_match_server_without_running_services`;
`test_flow_desktop_session.py` (`test_task_scheduler_refusal_is_reported_and_leaves_no_request`,
`test_start_task_runs_the_interactive_task_and_explains_a_missing_one`,
`test_setup_registers_the_standard_rights_desktop_launcher_task`);
`test_flow_worker_startup.py` (`test_authenticate_helper_reads_the_browser_channel_from_the_database_when_the_api_is_down`,
`test_setup_elevation_window_is_visible_when_interactive_and_the_helper_sees_the_database`);
`test_setup_sso_password.py` (`test_setup_block_uses_inherited_password_and_stops_on_failure`,
`test_setup_sync_precedes_interactive_authentication_and_is_not_interactive_only`).

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

Test removed with the content it checked: `tests/test_film_package.py`. It
verified that the film's artifacts matched its manifest and that its HTML
referenced existing files; with the film deleted it has nothing to check.

Historical release packages for the film (2026-09-28 and 2026-09-29) keep
their original text, so their links into `media/` now point at files that
exist only in git history.

The 5 skips are Windows-only: the scheduled updater (1), PowerShell service
configuration (2) and Windows PowerShell with real DPAPI (2). The first
progress-test draft matched the words in `setup.ps1`'s new comment rather
than the commands and failed at `c18dfde`; `fb72d6d` anchors it to
`Invoke-WebRequest -Uri` and `Expand-Archive -Path`
(`.test-runs/20261104T091456640Z-926-6a344429`). No PowerShell parse of
`setup.ps1` ran here.

The progress setting cannot be timed here. An update downloads and extracts
with the `setup.ps1` already installed, so the gain appears from the update
after the one that installs this change.

M-01 uses local `git archive`. GitHub's download is built the same way, but
this report does not include a download from GitHub itself.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
