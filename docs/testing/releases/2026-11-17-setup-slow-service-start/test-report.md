# 2026-11-17: A slow worker start no longer stops setup: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-17 11:25
- Tested code revision: the commit that adds this package, on `main` at
  `2283eb4` (tested as the working tree before commit)
- Environment: Linux 6.18 (cloud container), Python 3.13.18 checkout-owned
  `.venv`. No PowerShell in the container
- Overall finding: the static check passes here. The PowerShell cases S-01 and
  S-02 run only in the Windows CI job; they are pending at this cutoff.

## Change

- `tools/setup_flow_worker_health.ps1` gains `Start-MetronomeSetupService`. It
  runs `nssm start` with `$ErrorActionPreference = 'Continue'` inside the
  function and reads the exit code and NSSM's words, returning Started,
  Running, Starting or Failed without throwing.
- It also gains `Write-MetronomeSetupServiceStart`, which prints one line for
  Running, Starting or Failed and nothing for a clean start.
- `setup.ps1` starts the main Flows worker, every worker slot, the auditor
  reader and Metronome through the helper. The existing checks that follow
  still report a worker that never registers (with its log file) and a
  Metronome that does not answer.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| S-03, R-01 | The plan's `verify` command | Working tree on `2283eb4`, Linux | PASS: 10 passed, 55 skipped (Windows PowerShell cases), 5 s | `.test-runs/20261117T112251481Z-360-5fcb44b0/result.json` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| S-01, S-02 | NOT RUN | Need Windows PowerShell 5.1, which this container does not have | The pull-request "Windows verifier contracts" job runs them |

## Findings, limitations and retests

- The other messages in the same setup run were warnings and are unchanged.
  ASAP sign-in failed for one visible worker profile, and GSCM did not render
  its client in time for the sign-in of two profiles. Each worker signs in
  with the saved portal sign-in the first time a Flow needs the portal.
- A service that stays in START_PENDING is still found by the registration
  check that follows. That check names the slots that did not register and
  their log files, as before.

## Merge evidence

Final-head CI is pending at this cutoff. Before the head-pinned merge, the
PR's testing section records the `Merge ready` run URL, the tested head SHA
and its result.
