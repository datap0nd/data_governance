# 2026-11-11: Setup run from the data folder stops and names the code folder: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 14:00
- Tested code revision: the commit that adds this package, on `main` at
  `a26bb04`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`. No PowerShell is available here
- Overall finding: the test files collect and the cases that need no
  PowerShell pass. Every case that exercises the change (D-01 to D-05) is NOT
  RUN here and is left to the pull request's Windows job, which is required
  before the merge.

## What was reported

The owner's database, about 1 GB, is in the same folder as the `setup.ps1`
that was run. Setup reported no database, then a 1.3 MB one, at the path it
uses.

## Cause

`setup.ps1` takes the folder it is in as the code folder and keeps the
database, the settings file, the logs and the portable Python one folder above
it. The copy that was run sits in Metronome's data folder, beside
`governance.db`. Setup therefore looked one folder above the data folder,
found no database there, created an empty one and pointed the services at it.

This corrects the two earlier reports of the day, which assumed a second copy
of the code in an unrelated folder. The checks added then are still right for
that case but did not catch this one: with no database beside the folder and
none recorded for the service at that path, setup had nothing to compare.

How a copy of the code came to be in the data folder is not known from here.
Whether the code folder is a subfolder of it on the work PC, which is what the
check looks for, has not been confirmed by the owner at the time of writing.

## Change

Before the comparison with the installed service, setup asks whether the
folder it is in looks like the data folder: it holds a `governance.db` and a
subfolder that has both `setup.ps1` and `app\main.py`. If so it stops, shows
the database with its size, explains that it would look one folder too high,
and names the `setup.ps1` to run. Nothing is created, stopped or moved.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Collection and non-PowerShell cases | The `python tools/check.py verify` command in the plan | Working tree, Linux | PASS: 9 passed, 0 failed, 43 skipped (all need Windows PowerShell, among them the 18 cases of the installer check), 3.4 s | `.test-runs/20261111T134912391Z-30185-d985d7e9/result.json` |
| Plain-text review | `grep` for bytes outside ASCII in `setup.ps1` and the helper; brace and parenthesis balance | Working tree | None found; balanced | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| D-01 to D-05 | NOT RUN | Need Windows PowerShell | The pull request's `Windows verifier contracts` job runs them; record the run in the PR testing section |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

- The check does not depend on the service settings, so it also works on a PC
  where the services already point at the empty database.
- A layout where the database sits inside the code folder and no subfolder is
  a code folder is not stopped. Setup prints a note that it does not use that
  file, as added earlier today.
- The empty database, Python, logs, settings file and Flow folders that the
  earlier runs created one folder above the data folder are left where they
  are. Removing them is the owner's decision.

## Final assessment

Ready for final-head CI. The Windows job is the first real execution of this
change.
