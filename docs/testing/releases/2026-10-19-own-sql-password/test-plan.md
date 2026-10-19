# 2026-10-19: People change their own SQL password: test plan

- Change/PR: branch `claude/cool-bohr-x67q6x`; PR link in the PR's testing
  section. A signed-in person sets or clears their own SQL password from
  **Change SQL password** on their row in Users. The host PC still sets
  anyone's and remains the only one who edits profiles.
- Code baseline: `main` at `17fe5f1`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container with the checkout-owned Python 3.13
  `.venv`, Node 22, Playwright Chromium; final-head CI on Ubuntu and Windows.

## Prerequisites and test data

Disposable fixtures only: a temporary SQLite database, a temporary `.env`,
fictional people and computers on TEST-NET addresses. Browser cases render
production `users.js` and `app.js` against a fictional API layer.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| O-01 | Maya signs in on her computer; sets, then clears, her own SQL password | Both succeed; the setting is written to and removed from `.env`; the event log records Maya as the actor | `test_user_sql_passwords.py::test_people_change_only_their_own_sql_password` |
| O-02 | Signed-in Maya sets or clears Omar's password, changes her own SQL username, signs out her computers | Every request 403 naming the host PC; no password written | same test |
| O-03 | Sofia, signed in without a SQL username, sets her password | 409 asking for the SQL username first | same test |
| O-04 | Unsigned computer changes Users or anyone's password; host PC flows | Unchanged from the previous release: 403, and the host PC still sets and clears anyone's password | remaining `test_user_sql_passwords.py`, `test_people.py`, `test_user_identity.py` |
| U-01 | Users as Maya (colleague): her row, others' rows; Change SQL password; empty save; save failing once; retry; reopen and clear; Cancel; 390×844 | One button on her own row only; panel focused on the password field; empty entry explained; failure keeps the entry and re-enables Save; success closes the panel, updates the row and returns focus; clear shows "Not set"; no page overflow; Omar's password never requested | `test_users_browser.py::test_colleague_changes_only_their_own_sql_password`, screenshots |
| U-02 | Users as Omar (colleague without SQL username) | Read-only note mentions the own-password exception; his row says to ask the host PC for a SQL username; no buttons | `test_users_browser.py::test_colleagues_see_users_read_only` |
| P-01 | Six people's passwords saved at the same moment, with the file write slowed down | Writes never overlap and every password is in the file | `test_user_sql_passwords.py::test_concurrent_password_saves_never_lose_one` |
| R-01 | Host Users journey, sign-in chooser and Flow owners preview | Unchanged | remaining `test_users_browser.py`, `test_flow_owners_preview.py` |

## Automated checks

```bash
python tools/check.py verify --test tests/test_user_sql_passwords.py \
  --test tests/test_users_browser.py --test tests/test_people.py \
  --test tests/test_user_identity.py --test tests/test_flow_owners_preview.py \
  --syntax app/routers/people.py --syntax app/static/users.js
for f in tests/test_*.mjs; do node "$f"; done
```

Required final-head CI (`Merge ready`) is the full regression.

## Usability evidence

The owner asked for this control in the current task. It reuses the approved
SQL password field from the host's Users editor in a smaller panel on the
person's own row; no step is added to any existing journey. U-01 and U-02
walk it at 1440×1000 and 390×844 with screenshots in [evidence/](evidence/).

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
Rollback: revert the PR; saved passwords stay valid and only the host PC can
change them again. Nothing to clean up.
