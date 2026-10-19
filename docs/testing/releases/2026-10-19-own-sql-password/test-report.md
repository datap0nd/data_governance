# 2026-10-19: People change their own SQL password: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-19 11:20
- Tested code revision: `8ca8709`, then the password-file lock fix (commit
  named in the PR testing section) for `test_user_sql_passwords.py`
- Environment: Linux container, Python 3.13.16 checkout-owned `.venv`, Node
  22.22.0, Playwright 1.62.0 bundled Chromium 1194
- Overall finding: every Python and browser case passes locally (automated and
  synthetic browser). One Node contract, `test_gemini_extension.mjs`, is
  BLOCKED locally because this container lacks the extension's `pg` package;
  CI's frontend job installs it and runs every contract. Final-head CI is
  pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| O-01–O-04, U-01, U-02, R-01 | `python tools/check.py verify --test tests/test_user_sql_passwords.py --test tests/test_users_browser.py --test tests/test_people.py --test tests/test_user_identity.py --test tests/test_flow_owners_preview.py --syntax app/routers/people.py --syntax app/static/users.js` | `8ca8709`, container above | PASS: 42 tests, 0 failed, 0 skipped, 2 third-party deprecation warnings, 36.4 s; syntax clean | `.test-runs/20261019T111637182Z-1878-27665a69/result.json` |
| Node contracts | `for f in tests/test_*.mjs; do node "$f"; done` | `8ca8709`, Node 22.22.0 | 25 of 26 pass. `test_gemini_extension.mjs` BLOCKED: `Cannot find package 'pg'` from `integrations/metronome-gemini` (not installed in this container; fails the same way on `main` here) | Console output |
| P-01 (password-file lock) | `python tools/check.py verify --test tests/test_user_sql_passwords.py --syntax app/sql_passwords.py`; negative check with the lock disabled | Lock fix, container above | PASS: 8 tests, 3.7 s. With the lock disabled, `test_concurrent_password_saves_never_lose_one` fails (two writers overlapped) | Console output |
| Screenshots | `METRONOME_UI_EVIDENCE_DIR=… pytest` of U-01 and U-02 | `8ca8709` working tree, bundled Chromium | PASS: 2 tests | [evidence/](evidence/) |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |
| `test_gemini_extension.mjs` | BLOCKED locally | The container lacks the extension's `pg` package; unrelated to this change | CI's "Frontend contracts and syntax" job installs it; record its result with the final run |

## Usability evidence

Synthetic browser evidence from production `users.js` with fictional data:

| Evidence | What it shows |
| --- | --- |
| [Save failed, entry kept](evidence/own-sql-password-retry.png) | Maya's own panel after a failed save: message beside the action, password kept, Save enabled; only her row has **Change SQL password** |
| [Narrow panel](evidence/own-sql-password-narrow.png) | 390×844 after clearing: "Not set" in her row, panel without page overflow |
| [Colleague without SQL username](evidence/users-colleague-read-only.png) | Omar's row asks him to get a SQL username from the host PC |

## Findings, limitations and retests

Codex reviewed `b839aa4` and raised three findings. Concurrent password saves
could drop one person's password, because each save rewrites the whole
settings file; saves are now serialized and a test proves it (P-01). This
report previously called the local run a full pass despite the blocked Node
contract above; corrected. The third, that self-service changes trust the
self-chosen sign-in, is the sign-in finding below; the owner chose to merge
with it disclosed.

The first screenshot showed "New SQL password for" and the username on two
lines; the label text now sits in one span and the screenshots above are the
retest. Signing in still trusts the name a new computer chooses (Codex P1 on
#157, awaiting the owner's decision), so whoever signs in as a person can also
replace that person's SQL password; they cannot read it or change the SQL
username.

## Merge evidence

Pending: final-head CI. The PR testing section records the run URL, tested
head SHA and result before a head-pinned merge.
