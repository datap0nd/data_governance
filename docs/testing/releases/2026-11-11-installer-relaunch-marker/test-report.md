# 2026-11-11: A reused PowerShell window no longer makes setup skip its first half: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 13:45
- Tested code revision: the commit that adds this package, on `main` at
  `28e5d1b`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`. No PowerShell is available here
- Overall finding: the test files collect and the cases that need no
  PowerShell pass. Every case that exercises the change (R-01 to R-05) is NOT
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

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Collection and non-PowerShell cases | The `python tools/check.py verify` command in the plan | Working tree, Linux | PASS: 9 passed, 0 failed, 39 skipped (all need Windows PowerShell, among them the 14 cases of the installer check), 3.5 s | `.test-runs/20261111T133215066Z-3104-e52472bd/result.json` |
| Plain-text review | `grep` for bytes outside ASCII in `setup.ps1` and the helper; brace and parenthesis balance | Working tree | None found; balanced | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| R-01 to R-05 | NOT RUN | Need Windows PowerShell | The pull request's `Windows verifier contracts` job runs them; record the run in the PR testing section |
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
