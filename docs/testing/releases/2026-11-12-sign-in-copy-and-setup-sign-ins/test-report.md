# 2026-11-12: Encrypted sign-in file follows the settings file; setup signs in two workers of each kind: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-12 08:45
- Tested code revision: the commit that adds this package, on `main` at
  `ae6ff9c`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`, Node 22, the container's Chromium. No PowerShell, no Windows, no
  portal and no real account
- Overall finding: every case that can run here passes (automated,
  synthetic). Every PowerShell case, which includes all of the setup change,
  runs only in the pull request's Windows job.

## What was asked

After the portal sign-in moved to `.env`, the owner reported that the
encrypted `.asap_credentials` file no longer existed and that all their
Python scripts in Flows check for it; it should be generated at each start
from the `.env` sign-in. They then added that setup signs in too many worker
browsers at start and takes too long every time: sign in two for headed and
two for headless, and wake more from Settings.

## Change

- `flow_credentials.sync_credential_file` makes the encrypted file a copy of
  the settings-file sign-in, in the layout it always had. It runs when the
  web service starts, when a worker starts (and in setup's sign-in helper),
  when the sign-in is saved or cleared on the Settings page, and on a worker's
  loop when the settings file was written since the last look.
- A copy carries a mark inside its encrypted record. Removing the sign-in
  from the settings file removes a marked copy. A file without the mark, from
  an earlier version, is replaced when the settings file holds a sign-in and
  otherwise left alone. A settings file that is missing or unreadable never
  causes a removal.
- The copy cannot be replaced or cleared on its own: Settings and the Flows
  website dialog answer 409 with where to change the sign-in, and setup's
  `DG_SVC_PASSWORD` refresh leaves it alone. Settings shows the block as a
  copy, with whether it is in step.
- `setup.ps1` signs in the first two background and the first two visible
  worker browsers for each portal instead of every configured slot, prints how
  many it left, and reads the number from the new setting **Worker browsers
  signed in during setup** (Flows, 1 to 32, default 2).

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| C-01 to C-20, S-01, S-02, R-01 | The `python tools/check.py verify` command in the plan | Working tree, Linux | PASS: 329 passed, 0 failed, 14 skipped, 79.7 s. All 14 skips need Windows PowerShell | `.test-runs/20261112T084252082Z-5312-ab6335da/result.json` |
| S-02, first attempt | The same command before the last edit | Working tree, Linux | FAILED once: the new setting's help text was 270 characters and the catalog allows fewer than 260. The text was shortened; the test was not changed | `.test-runs/20261112T084022487Z-5042-dd9348c7/result.json` |
| C-21 and the other Settings browser cases | `python -m pytest tests/test_settings_sections_browser.py` with the container's Chromium, synthetic responses | Working tree, Chromium | PASS: 19 passed, 33.7 s | Console output |
| Settings contract in Node; script syntax | `node --test tests/test_settings_sections.mjs`; `node --check app/static/settings.js`; `node --check app/static/app.js` | Working tree, Node 22 | PASS | Console output |
| The helper setup calls | `python tools/flow_setup_sign_in.py` against a database with the setting unsaved, then saved as 5 | Working tree, temporary database | Prints 2, then 5 | Console output |
| Plain-text review of `setup.ps1` and `tools/flow_pool.ps1` | Count of bytes outside ASCII; brace and parenthesis balance | Working tree | None; balanced | Console output |
| S-03 to S-05, R-02 | Windows job of final-head CI | Final head | NOT RUN here; see the PR's testing section for the run | Run URL in the PR |
| Full regression | Required final-head CI | Final head | NOT RUN at the evidence cutoff; see the PR's testing section | Run URL in the PR |

## Limitations

- PowerShell is not available in this container, so the changed lines of
  `setup.ps1` and the new function in `tools/flow_pool.ps1` are executed only
  by the Windows CI job. Their cases must show as passed there, not skipped.
- Windows' own protection of the file is replaced by a scramble in every
  test. That the real file opens for a Flow script depends on the script
  running as the Windows account of the services, as before.
- The copy follows a hand edit of the settings file when a worker next loops.
  With no worker running it follows at the next start of Metronome or of a
  worker.
- The pause after a refused sign-in covers what Metronome's workers submit. A
  script that reads the file and submits the password itself is not covered.
- Workers that setup no longer signs in rely on their own sign-in with the
  saved sign-in. That path existed before and is not changed or re-tested
  against a portal here.
