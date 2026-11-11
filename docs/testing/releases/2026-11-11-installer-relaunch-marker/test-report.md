# 2026-11-11: A reused PowerShell window no longer makes setup skip its first half: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 13:45
- Tested code revision: the commit that adds this package, on `main` at
  `28e5d1b`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`. No PowerShell is available here
- Overall finding: the test files collect and the cases that need no
  PowerShell pass. Every case that exercises the change (R-01 to R-07) is NOT
  RUN here and is left to the pull request's Windows job, which is required
  before the merge.

## What was reported

On the work PC, `setup.ps1` was run again from the installed folder, in the
administrator PowerShell window used for the earlier runs. It did not stop the
running service and finished with "An instance of the service is already
running".

## Cause

When `setup.ps1` finds that the update replaced `setup.ps1` itself, it starts
the new copy and tells it, through the environment variable
`DG_SETUP_RELAUNCHED`, that the download, the stop and the extraction are
done. Since the change that made updates faster (2026-11-11, "Faster updates
and bounded service logs") the new copy skips those three stages when it sees
the variable and an update archive on disk.

Run from a PowerShell window, the script sets that variable in the window's
own environment, and it stays there after the run. The next `setup.ps1` in the
same window saw the variable and an archive from the earlier update, concluded
it was the second half of a run, and went straight to installing dependencies
and starting services that were never stopped. Before the faster-update
change the leftover variable only suppressed a relaunch; the skip made it
harmful. The `SWITCH` answer added earlier today was kept the same way and had
the same flaw.

## Change

- A copy that relaunches records its own process id beside the marker. Only a
  process it started itself counts as the relaunched copy; a marker alone, or
  one recorded by another process, does not.
- The marker, the process id and a `SWITCH` answer are set for the started
  copy only and removed from the window again when it returns, whatever
  happens.
- A `SWITCH` answer is accepted from the environment only by a verified
  relaunched copy.
- A copy started by an older `setup.ps1` (marker without a process id) does
  the download, stop and extraction again. That costs a few seconds once and
  is what every update did before the faster-update change.

## Second report, same day: "a Database and also a DB"

Setup opened with `Database: <path>` and, on the next line, `DB exists: 1364
KB (will be preserved)` or `DB: new (will be created on first run)`. Both
lines describe the one path setup uses. The owner read them as two things
setup was looking for, and the size in KB did not show that the file found was
about 1 MB when the real database is about 1 GB. That reading was reasonable,
and it hid the actual finding for several hours: no file, and later only a
small one, at the path setup uses, while the real database is somewhere else.

- The opening now prints the code folder, the database path, and one sentence
  under it: `This file exists: 1.3 MB, last changed ... It is kept.` or `No
  file at this path. Setup would create a new, empty database here.`
- Sizes are shown in KB, MB or GB as fits.
- When a `governance.db` also sits inside the code folder, where Metronome
  looks when started without its service settings, setup says so and that it
  does not use that file.

Where the real database is on the work PC is not yet known; this release does
not move or adopt any file.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Collection and non-PowerShell cases | The `python tools/check.py verify` command in the plan | Working tree, Linux | PASS: 9 passed, 0 failed, 40 skipped (all need Windows PowerShell, among them the 15 cases of the installer check) | `.test-runs/20261111T133802974Z-8063-52821f7f/result.json` |
| Plain-text review | `grep` for bytes outside ASCII in `setup.ps1` and the helper; brace and parenthesis balance | Working tree | None found; balanced | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| R-01 to R-07 | NOT RUN | Need Windows PowerShell | The pull request's `Windows verifier contracts` job runs them; record the run in the PR testing section |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

- R-03 waits for ten failed download attempts, about a minute, because the
  installer's retry count is not a test setting.
- On a PC that still has the leftover variable in an open window, the copy of
  `setup.ps1` on disk is the old one until an update replaces it. Opening a
  new PowerShell window avoids the problem for that run.
- If the process that relaunched is killed outright, its window dies with it,
  so nothing is left behind. A window that survives with stale variables could
  only mislead a copy that the same window process starts directly.

## Final assessment

Ready for final-head CI. The Windows job is the first real execution of this
change.
