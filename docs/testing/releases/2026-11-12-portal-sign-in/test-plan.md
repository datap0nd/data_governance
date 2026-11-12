# 2026-11-12: Portal sign-in in the settings file, and never repeated into a lockout: test plan

- Change/PR: link in the PR's testing section. The owner asked for the portal
  sign-in to be read from `.env` in plain text, and pointed out that an old
  saved password was submitted by every browser profile until the account
  would lock. Workers now read `DG_PORTAL_USER` and `DG_PORTAL_PASSWORD`
  from the settings file, and a sign-in that failed is not submitted again.
- Code baseline: `main` at `6d1585b`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv` and the container's Chromium; required final-head CI for the
  full regression and the Windows installer contracts

## Prerequisites and test data

No live portal and no real account. People and passwords are fictional. The
sign-on form is a stand-in page object; the browser tests use fictional HTTP
responses; the page walk uses a temporary instance with an empty database, a
temporary settings file and a temporary profile folder.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| L-01 | A sign-in is submitted and the portal refuses it; a second submission is asked for, for the other portal | The second is refused before anything is sent; the message says why, where to correct it, and holds neither user ID nor password | `tests/test_flow_sign_in_guard.py::test_a_refused_sign_in_is_never_offered_a_second_submission` |
| L-02 | Two submissions end without the portal opening, one of them by an exception after the form was sent | Not paused after one, paused after two | `...::test_two_submissions_that_get_nowhere_pause_it_and_one_does_not` |
| L-03 | The form could not be sent at all | Does not count as an attempt | `...::test_a_form_that_was_never_sent_does_not_count` |
| L-04 | A failure, then a sign-in that works | The count is cleared | `...::test_a_sign_in_that_works_clears_an_earlier_failure` |
| L-05 | A refused password, then a different one; the same one refused again, then Try again | The different one gets its first try; the same one stays paused until Try again | `...::test_a_new_password_starts_clean_and_try_again_lifts_the_pause_for_the_same_one` |
| L-06 | Read the record the guard keeps on disk | Neither the user ID nor the password is in it | `...::test_the_record_holds_neither_the_user_id_nor_the_password` |
| L-07 | Two workers reach the sign-on form at the same moment; the first is refused | The second waits, then submits nothing | `...::test_workers_submit_one_at_a_time_and_the_second_sees_the_first_refusal` |
| L-19 | A worker process is killed after sending the form, twice; a third worker then starts | One failed attempt after the first, paused after the second, and the third submits nothing | `...::test_a_worker_stopped_while_it_waits_for_the_portal_still_counts` |
| L-20 | Read the state while a second attempt is still waiting for the portal, then after it succeeds | Not reported as paused while it waits; cleared after it succeeds | `...::test_a_sign_in_still_waiting_for_the_portal_is_not_reported_as_failed` |
| L-08 | `DG_PORTAL_USER` and `DG_PORTAL_PASSWORD` set; only one of them set; a tool that names the profile folder | Both set: that is the sign-in workers use. One: not a sign-in. Named folder: the encrypted file only | `...::test_the_settings_file_sign_in_is_what_workers_use` |
| L-09 | The worker's ASAP sign-in with an old password, then three more browser profiles and portals | The password is typed into one form; the other three leave the form untouched | `...::test_an_old_password_is_typed_into_one_browser_and_no_other` |
| L-10 | The worker's GSCM sign-in when the page says the user ID or password is wrong | Stops at once instead of waiting 180 seconds, and the sign-in is paused as refused | `...::test_gscm_stops_waiting_as_soon_as_the_page_says_no` |
| L-11 | A portal that never opens, three times across both portals | Two submissions, then none | `...::test_a_portal_that_never_opens_is_tried_twice_then_left_alone` |
| L-12 | A refused password is corrected | The new one is typed at the next sign-in and signs in | `...::test_the_corrected_password_is_used_at_once_and_signs_in` |
| L-13 | The setup-time sign-in helper with a paused sign-in | Refuses before a browser is started; its exit code for setup is 3 | `...::test_setup_is_told_to_stop_before_a_browser_is_opened` |
| L-14 | Save the user ID and the password through the Settings API; edit the password line by hand | Both are in the settings file in plain text; workers read the hand-edited value at once; the Settings page payload holds no password | `tests/test_settings_connections.py::test_the_portal_sign_in_saved_in_settings_is_plain_text_in_the_settings_file_and_what_workers_use` |
| L-15 | Read the sign-in state before and after a refusal; ask to try again from another computer and from the host PC; then save a corrected password | The state names its source and the pause; Try again is refused for another computer and logged for the host PC; a corrected password is not paused | `...::test_the_sign_in_state_shows_its_source_and_a_pause_and_try_again_is_for_the_host_pc` |
| L-16 | Settings in a browser with a paused sign-in | The notice names the portal and the reason; Try again calls the server and the notice goes | `tests/test_settings_sections_browser.py::test_a_paused_portal_sign_in_is_announced_where_it_is_corrected_and_try_again_resumes_it` |
| L-17 | Existing sign-in, settings and installer tests | Unchanged results | The files named in the command below |
| L-18 | Every `.ps1` through the PowerShell parser | No parse errors | `tests/test_setup_fast_path.py::test_powershell_parses_every_script` (Windows CI) |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_flow_sign_in_guard.py --test tests/test_settings_connections.py \
  --test tests/test_settings_registry.py --test tests/test_audit_worker.py \
  --test tests/test_flow_worker_discovery.py --test tests/test_setup_sso_password.py \
  --test tests/test_audit_flows_api.py --test tests/test_env_file.py \
  --test tests/test_setup_fast_path.py --test tests/test_settings_sections_browser.py \
  --syntax app/flow_worker.py --syntax app/flow_sign_in_guard.py --syntax app/flow_credentials.py
```

Required final-head CI is the full regression.

## Page walk

On a temporary instance, as the host PC, at 1360 px:

1. Save a user ID and a password under Connections > Portal sign-in through
   the Settings API; read the settings file.
2. Open Connections; read the two rows and the older encrypted block.
3. Mark the sign-in refused the way a worker would; reload; read the notice.
4. Choose Try again; the notice goes and a message confirms it.

## Acceptance and cleanup

Accepted when L-01 to L-17, L-19 and L-20 pass, L-18 passes in the pull request's Windows
job, the walk shows no page error, and `Merge ready` passes on the final head.
The walk's instance is stopped and its folder removed.

Not covered by any test: a real portal, a real refusal page and a real
lockout policy; the installer's changed sign-in loop beyond parsing.
