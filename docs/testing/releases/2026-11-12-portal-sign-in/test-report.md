# 2026-11-12: Portal sign-in in the settings file, and never repeated into a lockout: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-12 07:20
- Tested code revision: the commit that adds this package, on `main` at
  `6d1585b`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`, Node 22, the container's Chromium. No PowerShell, no Windows, no
  portal and no real account
- Overall finding: every case that can run here passes (automated,
  synthetic), and the page walk found no page error. What a real portal does
  with a wrong password, and the installer's changed sign-in loop, are not
  exercised anywhere; see the limitations.

## What was asked and reported

The owner asked which password signs the browsers in, found it was an old
one, and pointed out two things. First, that it could only be changed inside
Metronome. Second, that restarting with an old password would have it tried
until the account locked. They then asked for Metronome to read the sign-in
from `.env`, in plain text.

Both observations were correct. The sign-in was kept in a file encrypted for
the Windows account of the services, and changed through the app or by giving
setup the Windows password. Each browser profile submitted it on its own, for
ASAP and for GSCM, and nothing recorded a refusal: setup alone signs in every
profile of every portal in a row.

## Change

- Workers read `DG_PORTAL_USER` and `DG_PORTAL_PASSWORD` from the settings
  file, in plain text, at every sign-in. Both are settings under Connections,
  so the Settings page edits the same two lines. While either is empty the
  older encrypted sign-in is used, so an existing installation keeps working.
- One worker submits at a time. A sign-in the portal refuses is not submitted
  again by any worker, profile or portal. One that was submitted twice
  without the portal opening is not submitted a third time. A different user
  ID or password starts clean; Try again on the Settings page lifts the pause
  for the same one. The record on disk holds a salted fingerprint, not the
  sign-in.
- The setup-time helper refuses a paused sign-in before opening a browser and
  exits with code 3; setup then ends its sign-in step with one message.
- GSCM stops waiting as soon as the sign-on page says the user ID or password
  is wrong, instead of after 180 seconds.
- Settings shows a notice at the top of Connections while the sign-in is
  paused, and labels the older encrypted block as such.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| L-01 to L-15, L-17, L-19, L-20 | The `python tools/check.py verify` command in the plan | Working tree with the review fix, Linux | PASS: 308 passed, 0 failed, 45 skipped, 42.2 s. The skips are the 18 browser cases, which cannot start a browser under the verifier here, and 27 that need Windows | `.test-runs/20261112T070846152Z-7169-982cceec/result.json` |
| L-19 without the fix | The same test with the write-ahead line taken out, then put back | Working tree, Linux | FAILS as intended: the killed worker's submission is forgotten | Console output |
| L-16 and the other Settings browser cases | `python -m pytest tests/test_settings_sections_browser.py` with the container's Chromium, synthetic responses | Working tree, Chromium 153 | PASS: 18 passed, 30 s | Console output |
| Settings contracts in Node | `node tests/test_settings_sections.mjs`, `node tests/test_settings_page_loading.mjs`; `node --check app/static/settings.js` | Working tree, Node 22 | PASS | Console output |
| Page walk, steps 1 to 4 | Playwright script against a temporary instance on `127.0.0.1:8791` | Working tree, Chromium 153, 1360 px | PASS, details below | Console output; two screenshots kept outside the repository |
| Plain-text review of `setup.ps1` | `grep` for bytes outside ASCII; brace and parenthesis balance | Working tree | None found; balanced | Console output |

### Page walk

1. After saving through the Settings API the settings file read
   `DG_PORTAL_USER="maya.k"` and `DG_PORTAL_PASSWORD="Winter-2025-old"`
   (fictional), and the sign-in state named `settings_file` as its source.
2. Connections showed a Portal sign-in group with User ID and Password. The
   password row read "Saved" with dots, Replace and Clear. The older block
   read "Encrypted portal sign-in from an earlier version" and "Not in use:
   the fields above are set".
3. With the sign-in marked refused as a worker would, the page showed: "The
   portal sign-in is paused. ASAP refused the user ID or password on 12 Nov
   2026 10:59. Metronome will not submit it again, so the account cannot be
   locked by repeated attempts." and a Try again button. The time is shown in
   Dubai time; the record keeps UTC.
4. Try again removed the notice and showed "The portal sign-in will be tried
   again the next time a portal asks for it."

No page error in either load. After step 3 the notice was moved from beside
the older block to the top of the Connections section; L-16 was rerun after
that move and passes.

## Usability evidence

| Changed control | Label and placement | Feedback, failure and recovery | Entered work and navigation |
| --- | --- | --- | --- |
| Portal sign-in, User ID and Password | A group of that name in Connections, between the upload database and Power BI, as two ordinary settings rows. The help says the pair is what every worker uses and that it is plain text in the settings file | Saved through the same Save bar and Replace button as the other connection settings; a password is never shown back | Unchanged from the other rows |
| Paused notice | At the top of Connections, and with the sign-in in search results. Names the portal and whether it refused or did not open, with the time | Says nothing more is submitted and what to do: correct the two fields, or Try again | Try again keeps the page where it is; another computer sees the button disabled |
| Older encrypted block | Retitled "Encrypted portal sign-in from an earlier version" and says when it is used | Its Replace and Clear behave as before | Unchanged |

No preview was shown first: the request was the owner's own, the two fields
are ordinary settings rows, and the notice is a status line.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| L-18 | NOT RUN | Needs Windows PowerShell | The pull request's `Windows verifier contracts` job |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Found by the automated review of the pull request

- A submission could be forgotten. The outcome of an attempt was written when
  the attempt ended. A worker stopped after sending the password and before
  the portal answered (a service restart, an update killing leftover workers,
  a power cut) never reached that point, so the next worker sent the same
  password again and no count ever rose. The attempt is now written down as a
  failure before the form is sent and corrected when the outcome is known:
  cleared on success, kept on failure, removed if the form was never sent.
  Covered by L-19, which kills a real worker process mid-attempt and fails
  with the fix taken out. The Settings page does not report an attempt as
  failed while it is still waiting (L-20).

## Findings, limitations and retests

- A refusal is recognised by the sign-on page's own words ("incorrect user
  id", "incorrect password", "try again"), the same text the worker already
  looked for. A page that refuses in other words counts as "did not open":
  the sign-in is then paused after the second submission, not the first.
- Two failed submissions is a choice made without knowing the portal's
  lockout threshold. It is one constant in `app/flow_sign_in_guard.py`.
- A password in the settings file can be read by anyone who can read that
  file. Setup restricts the file to SYSTEM, Administrators and the installing
  account. This is the owner's decision; the SQL passwords are kept the same
  way.
- The older encrypted sign-in is not deleted or migrated. It is used only
  while the two settings are empty, and is guarded the same way.
- `DG_SVC_PASSWORD` given to setup still refreshes only the encrypted
  sign-in.
- Not exercised: a real portal, the browser sign-in step of `setup.ps1`, and
  several real worker processes at once (L-07 uses two threads on one lock
  file).

## Final assessment

Ready for final-head CI.
