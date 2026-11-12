# 2026-11-12: Encrypted sign-in file follows the settings file; setup signs in two workers of each kind: test plan

- Change/PR: link in the PR's testing section. Two requests from the owner.
  First: after the portal sign-in moved to `.env`, the encrypted
  `.asap_credentials` file was gone, and their Flow scripts read that file;
  it has to be generated at each start from the `.env` sign-in. Second: setup
  signs in too many worker browsers and takes too long; sign in two background
  and two visible workers, and change the number in Settings when more are
  needed.
- Code baseline: `main` at `ae6ff9c`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv` and the container's Chromium; required final-head CI for the
  full regression and for every PowerShell case (Windows job)

## Prerequisites and test data

No live portal, no real account and no work PC. Sign-ins are fictional.
Windows' protection of the file is stood in for by a reversible scramble, so
the file's content is checked on any system. The setup cases run the real
sign-in block of `setup.ps1` with stand-in helper scripts that record what
they are asked: no browser opens.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| C-01 | Settings file holds a sign-in, no encrypted file; sync | File written in the `dpapi` layout, decrypts to that sign-in, holds neither user ID nor password in plain text | `tests/test_portal_sign_in_copy.py::test_a_missing_file_is_written_from_the_settings_file_and_is_not_plain_text` |
| C-02 | Sync twice | Second time nothing is written; bytes identical | `...::test_a_copy_already_in_step_is_left_byte_for_byte` |
| C-03 | Password line edited by hand, then sync | The copy holds the new password | `...::test_a_password_corrected_in_the_settings_file_reaches_the_copy` |
| C-04 | An encrypted sign-in from an earlier version, with another or the same password; settings file holds a sign-in; sync | It becomes the copy | `...::test_an_older_stand_alone_sign_in_becomes_the_copy` |
| C-05 | A file that is not JSON, not decryptable, or not `dpapi`; sync | Replaced by the copy | `...::test_a_file_that_cannot_be_read_is_replaced` |
| C-06 | The password is removed from the settings file; sync | The copy is removed; workers have no sign-in | `...::test_removing_the_sign_in_removes_its_copy` |
| C-07 | Only an older encrypted sign-in, nothing in the settings file; sync | Left byte for byte and still used | `...::test_an_older_stand_alone_sign_in_is_never_removed` |
| C-08 | A copy exists; the settings file is missing, or cannot be read at that moment; sync | The copy is kept, and the answer is "could not read", not "no sign-in" | `...::test_a_settings_file_that_cannot_be_read_does_not_remove_the_copy` |
| C-09 | Not Windows | Nothing is written; workers still read the settings file | `...::test_nothing_is_written_where_windows_protection_does_not_exist` |
| C-10 | The file is held open by a reader for two attempts | Written on the third; no temporary file left | `...::test_a_file_a_reader_holds_open_is_tried_again` |
| C-11 | Another worker writes the copy while ours is refused | Accepted as in step | `...::test_a_copy_another_worker_wrote_in_the_meantime_is_accepted` |
| C-12 | Every write refused | The error is raised, the file is as it was, no temporary file left | `...::test_a_write_that_keeps_failing_is_reported_and_leaves_the_file_as_it_was` |
| C-13 | Two processes write | Each through its own temporary file | `...::test_each_process_writes_through_its_own_temporary_file` |
| C-14 | A worker starts, loops three times, then the settings file is edited | Written at start; no further sync until the edit; then the new password. No password in the worker's output | `...::test_a_worker_writes_the_copy_when_it_starts_and_again_only_after_the_settings_file_changes` |
| C-22 | The settings file cannot be read at the moment a worker starts; it can on the next loop, with the same date and size | Nothing written the first time; written on the next loop; no further sync after that | `...::test_a_settings_file_unreadable_when_the_worker_looks_is_read_again_on_the_next_loop` |
| C-15 | The write fails for a worker, twice, then succeeds | The worker carries on, reports the kind of failure once without the password, and writes it on the next loop | `...::test_a_copy_that_cannot_be_written_never_stops_a_worker_and_is_tried_again` |
| C-16 | The web service's start-up step, then the same with a failing write | Written; the failure is logged by kind only and start-up carries on | `...::test_the_web_service_writes_the_copy_when_it_starts_and_a_failure_only_logs_its_kind` |
| C-17 | Settings API: save the user ID, save the password, replace it, clear it | No file after the user ID alone; copy after the password; follows the replacement; removed on clear. State reports `copy_current` and holds no password | `...::test_saving_or_clearing_the_sign_in_on_the_settings_page_moves_the_copy_at_once` |
| C-18 | Settings API save while the file cannot be written | The setting is saved; the failure is logged by kind; state says the copy is not current | `...::test_a_copy_that_could_not_be_written_does_not_fail_the_save_and_shows_on_the_page` |
| C-19 | With a settings-file sign-in: replace or clear the encrypted file from Settings, and save another sign-in in the Flows website dialog. Then the same without a settings-file sign-in | 409 with where to change it, file untouched. Without a settings-file sign-in the older block works as before | `...::test_the_copy_cannot_be_replaced_or_cleared_on_its_own` |
| C-20 | Setup's `DG_SVC_PASSWORD` refresh while the settings file holds the sign-in | The copy is untouched and the message says why, without the password | `...::test_setup_does_not_put_the_windows_password_into_the_copy` |
| C-21 | Settings in a browser: copy in step, copy not written, not Windows, then no settings-file sign-in | Title, help and state text for each; no button on the copy; the older block and its buttons when there is no settings-file sign-in | `tests/test_settings_sections_browser.py::test_the_encrypted_sign_in_is_shown_as_a_copy_while_the_settings_file_holds_it` |
| S-01 | The new setting unsaved, saved as 5, unusable (99), unreadable | 2, 5, 2, 2 | `tests/test_portal_sign_in_copy.py::test_setup_signs_in_two_worker_browsers_of_each_kind_unless_settings_says_otherwise` |
| S-02 | The setting's declaration | Default 2, range 1 to 32, plain help within the catalog's rules, read by code | `tests/test_settings_flows.py::test_the_catalog_declares_exactly_these_settings_with_ranges_units_and_plain_help` and the reader tests beside it |
| S-03 | PowerShell: 12 background and 3 visible slots; 12 and 1; 1 and 1; a limit of 5 | Slot 1 of each kind, then slot 2; a kind with fewer slots gives what it has; 5 gives five background and all three visible | `tests/test_setup_sign_in_browsers.py`, first three cases (Windows CI) |
| S-04 | The real sign-in block of `setup.ps1`, 12 and 3 slots, two portals, the helper answering 2, failing, or answering nonsense, 0 or 99 | Four browsers per portal in slot order; "11 more worker browser(s) were not signed in here" and the setting's name are printed; setup carries on | `...::test_setup_signs_in_two_of_each_kind_for_each_portal_and_says_what_it_left` (Windows CI) |
| S-05 | The same block with the setting at 12, and with one slot of each kind | All fifteen per portal and no "not signed in" line; two per portal and no line | `...::test_setup_signs_in_as_many_as_settings_asks_for`, `...::test_one_slot_of_each_kind_is_signed_in_once_per_portal` (Windows CI) |
| R-01 | Existing sign-in, settings, worker, generated-script and installer tests | Unchanged results | The files named in the command below |
| R-02 | Every `.ps1` through the PowerShell parser | No parse errors | `tests/test_setup_fast_path.py::test_powershell_parses_every_script` (Windows CI) |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_portal_sign_in_copy.py --test tests/test_setup_sso_password.py \
  --test tests/test_settings_connections.py --test tests/test_flow_sign_in_guard.py \
  --test tests/test_settings_flows.py --test tests/test_settings_registry.py \
  --test tests/test_settings_flows_worker.py --test tests/test_flow_capacity.py \
  --test tests/test_flow_worker_startup.py --test tests/test_flow_portable_script.py \
  --test tests/test_flow_standalone.py --test tests/test_audit_worker.py \
  --test tests/test_audit_flows_api.py --test tests/test_setup_sign_in_browsers.py \
  --syntax app/flow_worker.py --syntax app/flow_credentials.py --syntax app/main.py \
  --syntax app/routers/system_settings.py --syntax app/routers/flows.py --syntax app/config.py \
  --syntax app/flow_capacity.py --syntax app/flow_limits.py --syntax app/settings_catalog_flows.py \
  --syntax tools/flow_setup_sign_in.py --syntax tools/sync_flow_sso_password.py
```

The Settings browser file runs directly with the container's Chromium,
because a browser cannot start under the verifier in this container. Required
final-head CI is the full regression and the only place the PowerShell cases
run.

## Usability

Wording and state only, on one existing block of Connections; no new screen
or step. While the settings file holds the sign-in the block is titled as a
copy, says when it is written, shows whether it is in step, and has no
buttons. C-21 walks each state in a browser.

## Acceptance and cleanup

Accepted when C-01 to C-22, S-01, S-02 and R-01 pass here, S-03 to S-05 and
R-02 pass in the pull request's Windows job and are not skipped there, and
`Merge ready` passes on the final head. Nothing to clean up.

Not covered by any test: Windows' real protection of the file and a real Flow
script reading it; a real portal; a full setup run on a PC with services.
