# 2026-11-11: Setup refuses to start a second, empty installation: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 13:30
- Tested code revision: the commit that adds this package, on `main` at
  `ab1b09f`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`. No PowerShell is available here, so none of the PowerShell cases
  ran locally
- Overall finding: the cases that need no PowerShell pass. Every case that
  exercises the change (G-01 to G-10) is NOT RUN here and is left to the pull
  request's Windows job, which is required before the merge.

## What was reported

After the update on the work PC, Metronome opened with no users and no data;
only the Settings page had content. The database and the backups were still on
disk. During setup the console showed `Code dir`, then `Database`, then
`DB: new (will be created on first run)`.

## Cause

`setup.ps1` installs into the folder it is run from: the database, the
settings file, the logs and the portable Python are all placed beside that
folder, and the Windows services are pointed there. `DB: new` means the folder
it was run from had no `governance.db` beside it, so it was not the folder
Metronome was installed in. Setup then did what it does on a new PC: it
created an empty database and pointed the services at it. The earlier failure
of the same run, "No matching distribution found for pandas", has the same
cause: the Python beside that folder was new as well.

The real database was not opened, moved or changed. Nothing in the installer
compared the folder it was run from with the installed one.

Not known from here: which folder was used, and how it came to hold a copy of
the code.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| G-11 and the existing installer and sharding tests | The `python tools/check.py verify` command in the plan | Working tree, Linux | PASS: 49 passed, 0 failed, 33 skipped (7 new and 26 existing cases that need Windows PowerShell), 5.1 s | `.test-runs/20261111T130033605Z-19190-13b6c14d/result.json` |
| Plain-text review | `grep` for bytes outside ASCII in `setup.ps1`, `update_app.ps1` and the new helper | Working tree | None found (Windows PowerShell 5.1 reads these files without a byte-order mark) | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| G-01 to G-10 | NOT RUN | Need Windows PowerShell | The pull request's `Windows verifier contracts` job runs them; record the run in the PR testing section |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

- The check reads only: the service's settings in the registry, and the size
  and time of two files. The creation of the Flow source folders moved from
  the top of the script to after the check, so a refused run leaves nothing
  behind (G-09).
- A first version of this change let a folder with its own database take the
  services over with only a printed note. That would have let the same mistake
  repeat on the affected PC, where the second copy now has a database too, so
  it was replaced before the first CI run finished: setup shows both databases
  with size and time and continues only on `SWITCH` (G-04); an unattended
  update refuses (G-09).
- A PC that already has a second copy installed is pointed back by running
  `setup.ps1` from the folder beside the real database and typing `SWITCH`.
  That folder's own `setup.ps1` predates the check, so the question appears
  after the download and extraction, from the relaunched new copy. If the
  answer is no there, the services that the older half stopped are started
  again. Starting real services is not exercised by any test.
- A run that is refused names the folder the services use now. On a PC where a
  second copy is already installed, that is the second copy, not the original.
- `update_app.ps1` now prefers the folder the installed service runs from over
  the folder the file sits in. This is checked for syntax only.
- Two paths that reach one file by different routes count as one database when
  size and last-write time match (G-03).

## Final assessment

Ready for final-head CI. The Windows job is the first real execution of this
change.
