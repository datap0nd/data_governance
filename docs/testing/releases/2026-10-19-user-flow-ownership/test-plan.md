# 2026-10-19: Users sign-in, Flow ownership and owner SQL users: test plan

- Change/PR: branch `claude/cool-bohr-x67q6x`; PR link in the PR's own testing
  section. Sign-in chooses a person from Users; only the host PC changes Users
  and SQL passwords; a Flow belongs to its creator and only its owner or the
  host PC changes it; host-PC changes email the owner; assigned Flows insert
  into SQL as their owner.
- Code baseline: `origin/main` at `2561604`. Workers must be updated to this
  release (they advertise `sql_person_credentials_v1`) before they claim an
  assigned Flow that writes to SQL.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container with the checkout-owned Python 3.13
  `.venv`, Node 22, Playwright Chromium; final-head CI on Ubuntu and Windows.

## Prerequisites and test data

All cases use disposable fixtures: a temporary SQLite database per test, a
temporary `.env` file where a settings file is needed, fictional people (Maya,
Omar, Sofia), fictional Flows and a fake SQL engine that records the
credentials it is given. Browser cases use production `app.js`/`users.js` with
a fictional API layer. No worker, Outlook, PostgreSQL server or real settings
file is reached.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| I-01 | Database with a typed-name `user_ips` row; run `init_db()` twice, binding a person in between | First start clears typed-name sign-ins; the second keeps the new person-based binding | `test_user_identity.py::test_typed_name_sign_ins_are_cleared_exactly_once` |
| I-02 | Colleague (TEST-NET IP) calls `/api/me`, registers by name, by unknown id, then by Maya's id; host renames, then deletes Maya | Unbound at first; name body 422; unknown id 404; bound to Maya, also by IP; rename shows at once; delete signs the computer out | `test_first_visit_chooses_a_person_and_the_name_follows_users` |
| I-03 | Signed-in colleague registers as Omar; another computer and the host call Sign out | 409 "already signed in as Maya Chen"; colleague sign-out 403; host sign-out clears it and the computer chooses again | `test_only_the_host_pc_moves_a_signed_in_computer` |
| I-04 | Host PC registers twice; host-request check with LAN name, foreign origin, cross-site fetch, forwarded header | Host may switch; only same-site requests from the host machine count as the host PC | `test_host_pc_may_switch...`, `test_host_pc_check_refuses...` |
| P-01 | Colleague creates, edits, deletes people and sets/clears a password | Every change 403 naming the host PC; Users unchanged; no password written | `test_user_sql_passwords.py::test_only_the_host_pc_changes_users` |
| P-02 | Host saves a password with quotes and spaces, reads Users, clears it | Written as `DG_USER_SQL_PASSWORD_<id>="…"` keeping CRLF and other lines; API reports `sql_password_set` only; event log has no value; clear restores the file | `test_password_is_saved_in_the_settings_file_and_never_returned` |
| P-03 | Password without SQL username, with control characters, for unknown person; new vs same SQL username; delete person; no or missing settings file | 409/422/404 as appropriate; a different username clears the password; delete removes password and sign-ins; missing file refused, never created | remaining `test_user_sql_passwords.py` cases |
| E-01 | `env_file.write_value` on a BOM/CRLF file with duplicate and `export` lines; invalid names/values; missing file; `current_setting` in app and portable config | Edited in place (same inode), other lines kept, value round-trips; unsafe input and missing files refused; both readers see each change | `test_env_file.py` new cases |
| O-01 | Colleague creates a Flow (no owner, someone else as owner, not signed in); host creates with and without owner | Colleague's Flow is theirs; others 403; unsigned 403; host's choice kept | `test_flow_ownership.py::test_colleagues_create_flows_for_themselves` |
| O-02 | Omar updates, patches, toggles, deletes, acknowledges SQL reconciliation and edits the recording of Maya's Flow; then runs and stops it; Maya edits and tries to reassign; Maya edits an unassigned Flow | Every change 403 "belongs to Maya"; run and stop work; Maya's edit keeps her as owner; reassigning 403; unassigned 403 "no owner" | `test_only_the_owner_or_host_pc_changes_a_flow` |
| O-03 | Host assigns an unassigned Flow to Maya, then reassigns it to Omar | No email for the first; Maya emailed with Owner Maya → Omar | `test_host_pc_reassigns_any_flow_and_the_previous_owner_is_told` |
| O-04 | Host renames and reschedules Maya's Flow, pauses it, saves unchanged, Maya edits, host signed in as Maya edits and deletes, host signed in as Omar deletes another | One email per real change with Changed by, Dubai time, each setting before/after; nothing for an unchanged save or the owner's own edits; deletion email says Deleted | `test_host_edit_emails_the_owner_what_changed_and_when` |
| O-05 | Owner without email; Outlook launcher raising | Change saved; skip recorded with the reason | `test_owner_notice_never_blocks_the_change` |
| S-01 | Assigned SQL Flow without saved password, then with it; owner without SQL username; unassigned SQL Flow | Queue 409 with the exact reason; queued job carries `connect_as` and no password; no username 409; unassigned job has no `connect_as` | `test_assigned_sql_flow_freezes_its_owner_and_needs_their_credentials` |
| S-02 | Due scheduled assigned SQL Flow without a saved password | Not queued; reason saved as the Flow's last error | `test_scheduled_run_without_owner_credentials_fails_closed` |
| S-03 | `load_artifacts` for an assigned target; change the password in the file; remove it; unassigned target | Connects as the owner with the file's current password; no password fails at connection without trying another account; unassigned uses the shared engine | `test_sql_load_signs_in_as_the_owner_and_never_falls_back` |
| S-04 | Database error containing the owner's password; post-SQL view precheck; worker capability; recording hash and portable file | Password redacted; view check uses the owner's user; old workers cannot claim; recording hash unchanged; portable file compiles with the credential code | remaining `test_flow_ownership.py` cases, `test_sql_ownership.py` |
| R-01 | Existing Flow, SQL, view-refresh, recording, portable, standalone, handover, groups, pipelines, triggers, editor, alert, diagnostics, recurrence, update and Users suites | Unchanged behavior apart from the new ownership rules | affected suites in the verify command below |
| U-01 | Users browser: colleague's first visit; empty Users; host first visit; host switches user | Chooser lists Users, validates an empty choice, shows a 409 beside the list with the choice kept, then signs in; empty Users explains next step; host may continue without a person and is remembered; host can switch from the header | `test_users_browser.py` new cases, screenshots `sign-in-*.png` |
| U-02 | Users browser: host saves a password (username missing, request failing once, retry), reopens, clears it, signs computers out; colleague view | Asterisks only, focused validation, partial-save message keeps the entry, clear and sign-out update in place; colleague sees a read-only note and no edit controls | `test_users_browser.py`, screenshots `users-*.png` |
| U-03 | Flows preview as host: User filter, Unassigned, inline reassignment, empty filter | Counts per person; row stays after reassignment and counts update; empty state names the filter | `test_flow_owners_preview.py`, `flow-owners-host-unassigned.png` |
| U-04 | Flows preview as host signed in as Maya, new Flow and Maya's Flow | Owner list defaults to Maya; help names the SQL user and missing password; Maya's Flow saves normally | `flow-owners-host-builder-owner.png` |
| U-05 | Flows preview as Omar: list, View Maya's Flow, Back, new Flow; narrow screen | Owner as text, View, disabled Active, no Delete/Move; view-only banner and disabled fields, steps readable; new Flow shows "Omar Malik (you)"; no page overflow at 390×844 | `flow-owners-colleague-*.png`, `flow-owners-narrow-*.png` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_user_identity.py --test tests/test_user_sql_passwords.py \
  --test tests/test_flow_ownership.py --test tests/test_env_file.py \
  --test tests/test_people.py --test tests/test_sql_ownership.py \
  --test tests/test_users_browser.py --test tests/test_flow_owners_preview.py \
  --test tests/test_flows.py --test tests/test_flow_recordings.py \
  --test tests/test_view_refresh.py --test tests/test_flow_sql.py \
  --test tests/test_flow_sql_view_refresh.py --test tests/test_flow_portable_script.py \
  --test tests/test_flow_standalone.py --test tests/test_flow_handover.py \
  --test tests/test_flow_topic_groups.py --test tests/test_pipelines.py \
  --test tests/test_managed_flow_editor.py --test tests/test_flow_triggers.py \
  --test tests/test_flow_excel_ui.py --test tests/test_auto_update.py \
  --test tests/test_recurrences.py --test tests/test_alert_occurrences.py \
  --test tests/test_flow_diagnostics.py --test tests/test_tmdl_checker_retired.py \
  --test tests/test_import_data_removed.py --test tests/test_legacy_artifact_compat.py \
  --syntax <each changed Python and JavaScript file>
for f in tests/test_*.mjs; do node "$f"; done
PREVIEW_EVIDENCE_DIR=docs/testing/releases/2026-10-19-user-flow-ownership/evidence \
METRONOME_UI_EVIDENCE_DIR=docs/testing/releases/2026-10-19-user-flow-ownership/evidence \
  .venv/bin/python -m pytest tests/test_flow_owners_preview.py tests/test_users_browser.py
```

Required final-head CI (`Merge ready`) is the full regression.

## Usability evidence

This changes what people decide: the first visit is a choice from Users instead
of a typed name, colleagues no longer pick a Flow owner, other people's Flows
open view-only, and Users gains a host-only SQL password. Per DESIGN.md this is
a materially changed journey: the clickable fictional preview
(`app/static/recording-preview/flow-owners.html`) and the Users browser cases
are walked at 1280×900 (Users at 1440×1000) and 390×844, and owner feedback is
required before merge.

## Acceptance and cleanup

Accepted when every case passes locally, the final-head `Merge ready` check
passes and the owner approves the demonstrated journey. On upgrade everyone
chooses their user once; the host PC should then assign unassigned Flows
(Flows > User: Unassigned) and save each owner's SQL password in Users before
assigned SQL Flows run. Rollback: revert the PR. Older code ignores the
`person_id` columns and `DG_USER_SQL_PASSWORD_*` settings, but the cleared
typed-name sign-ins are not restored (everyone types their name again), and
Flows reassigned meanwhile keep their new owner. Remove saved passwords from
`.env` after a rollback if they are no longer wanted. No test data needs
cleanup.
