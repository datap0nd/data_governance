# 2026-10-19: Users sign-in, Flow ownership and owner SQL users: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: [datap0nd/data_governance#157](https://github.com/datap0nd/data_governance/pull/157)
- Evidence cutoff (UTC): 2026-10-19 07:51
- Tested code revision: `b840550` (implementation) for the Python suites;
  `ee8ff8e` (read-only row width, empty-filter wording, plan and evidence) for
  the browser walkthroughs and Node contracts. The report itself changes no code.
- Environment: Linux 6.18 container, Python 3.13.16 checkout-owned `.venv`
  (locked dependencies), Node 22.22.0, Playwright 1.62.0 with its bundled
  Chromium 1194. The Chrome and Edge channels are not installed here.
- Overall finding: automated and synthetic browser checks pass for every new
  case. The only local failures are tests that require the Chrome or Edge
  channel, which this container lacks; a diagnostic run of the in-process ones
  on the bundled Chromium passed. Final-head CI and owner feedback are pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| I-01–I-04, P-01–P-03, E-01, O-01–O-05, S-01–S-04, R-01, U-01–U-05 | `python tools/check.py verify` with the 28 test files and 19 syntax targets listed in the plan | `b840550`, container above | 697 tests: 684 passed, 7 failed, 6 errors, 0 skipped, 2 warnings, 399.7 s; all syntax checks clean. Every failure and error is a Chrome/Edge channel launch (`Chromium distribution 'chrome'/'msedge' is not found`): 6 in `test_flow_recordings.py`, 1 in `test_managed_flow_editor.py`, 6 fixture errors in `test_flow_excel_ui.py`. All new cases passed. | `.test-runs/20261019T074201996Z-6875-f42fdd47/result.json` (source fingerprint `9e03008f…`) |
| Diagnostic for the channel-only failures | `pytest -p bundled_chromium tests/test_flow_excel_ui.py tests/test_managed_flow_editor.py` plus the three `test_flow_recordings.py::test_ui_*` cases, with a scratch plugin (not committed) that maps the `chrome` channel to the bundled Chromium | `b840550` working tree, container above | PASS: 15 tests, 26.1 s, including `test_flow_owner_sql_assignment_is_optional_in_each_builder` for all three builders with the new owner help | Console output; diagnostic only, not a substitute for CI's Chrome |
| U-01–U-05 | `python tools/check.py verify --test tests/test_flow_owners_preview.py --test tests/test_users_browser.py --syntax app/static/app.js` | `ee8ff8e`, bundled Chromium | PASS: 12 tests, 19.3 s; syntax clean | `.test-runs/20261019T074949118Z-13797-e5b90e71/result.json` |
| Node contracts | `for f in tests/test_*.mjs; do node "$f"; done` | `ee8ff8e`, Node 22.22.0 | 25 of 26 files pass. `test_gemini_extension.mjs` fails with `Cannot find package 'pg'` from `integrations/metronome-gemini`; it fails identically on unchanged `2561604` here | Console output |
| Screenshots | `PREVIEW_EVIDENCE_DIR=… METRONOME_UI_EVIDENCE_DIR=… pytest tests/test_flow_owners_preview.py tests/test_users_browser.py` | `ee8ff8e` working tree, bundled Chromium | PASS: 12 tests | [evidence/](evidence/) |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| R-01 (Chrome/Edge-channel cases), full regression | NOT RUN | Final-head CI (`Merge ready`) has not finished; it runs the channel-dependent tests with real Chrome on Ubuntu and the Windows verifier | Record the final run URL and tested head in the PR testing section |
| Owner review of the changed journey | NOT RUN | DESIGN.md requires owner feedback on a materially changed journey before merge | Owner reviews the preview and evidence, then approves or requests changes |

## Usability evidence

Synthetic browser evidence only, from production `app.js` and `users.js` with
fictional data and a fictional API layer
(`app/static/recording-preview/flow-owners.html`, `tests/test_users_browser.py`).

| Evidence | What it shows |
| --- | --- |
| [Choosing a user](evidence/sign-in-choose-user.png) | First visit on a colleague's computer: "I am" list of Users, focused, no free-text name |
| [Host switched](evidence/sign-in-host-switched.png) | Host PC continued without a person, then chose Maya from the header; header shows "Maya Chen · Host PC" |
| [SQL password saved](evidence/users-sql-password-saved.png) | Password column as asterisks, the editor's password field with "saved · type to replace", Clear SQL password |
| [Colleague's Users](evidence/users-colleague-read-only.png) | Read-only note, no Add or Edit, asterisks still visible |
| [Unassigned Flows](evidence/flow-owners-host-unassigned.png) | Host PC with User: Unassigned (2) and an owner list on each row |
| [Host builder owner](evidence/flow-owners-host-builder-owner.png) | Owner list defaulting to the signed-in person; help naming the SQL user and its missing password |
| [Colleague's list](evidence/flow-owners-colleague-list.png) | Owner as text, View on others' Flows, disabled Active switch, no Delete, Edit on their own Flow |
| [View only](evidence/flow-owners-colleague-view-only.png) | Banner naming the owner, disabled fields, readable steps, Back to Flows |
| [New Flow as colleague](evidence/flow-owners-colleague-new-flow.png) | "Omar Malik (you)" and "Flows you create are assigned to you" |
| [Narrow list](evidence/flow-owners-narrow-list.png), [narrow view only](evidence/flow-owners-narrow-view-only.png) | 390×844 without page overflow; tables scroll inside their frame |

The walkthroughs also cover failure and recovery: an empty choice, a 409 from
sign-in shown beside the list with the choice kept, a password without a SQL
username (focus moves to the username), a password request failing after the
profile saved (message says so and keeps the entry), and an empty filtered
list naming the person. No page errors and no unexpected API calls occurred.

## Findings, limitations and retests

- First focused run (before fixes, uncommitted tree): SQL loads failed where
  tests stub `flow_sql._engine` with one argument, because the shared-account
  path passed `credentials=None`. Fixed so the shared path calls
  `_engine(database)` exactly as before; the 136 SQL and view-refresh tests
  then passed. `test_flow_groups.mjs` and `test_flow_activity_polling.mjs`
  evaluate slices of `app.js`; the new helpers moved inside those slices and
  the filter binding after the sliced range, and both pass.
- While writing the ownership tests, a host-PC save that omitted
  `owner_person_id` (an older client or API caller) unassigned the Flow. It now
  keeps the saved owner, like other omitted fields; an explicit `null` from the
  host still unassigns.
- A colleague's Flows table squeezed columns because no owner select was left
  to trigger the table's minimum width; fixed in `ee8ff8e` and re-walked.
- The 2 warnings are third-party deprecations (`starlette.testclient` with
  `httpx`, `anyio` `BlockingPortal` alias), unchanged by this release.
- No PostgreSQL server, worker, Outlook or real `.env` was used: SQL sign-in is
  verified against a fake engine that records the credentials it receives, and
  Outlook delivery against a captured message. `tests/test_sql_ownership_postgres.py`
  is not in the local set; CI's PostgreSQL jobs run it.

## Merge evidence

Pending: final-head CI and owner feedback. The PR testing section records the
final run URL, tested head SHA and result before a head-pinned merge.
