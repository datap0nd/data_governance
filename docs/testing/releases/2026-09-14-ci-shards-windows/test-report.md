# Isolated CI shards and Windows verifier gate: test report

- Plan: [test-plan.md](test-plan.md).
- PR: discoverable from the merge record of this release; the PR testing section
  preserves later final-head and post-merge evidence.
- Evidence cutoff: 2026-09-14 19:30 UTC, before pushing or running hosted CI.
- Tested revision: working tree based on `552f4821f4f5a0689ea59f17e0e8ffe63428ce69`,
  committed without code changes as `fdaedef46182a9c7677702b9099e33b35349e691`.
  The result JSON records the baseline SHA because the test preceded the commit.
- Environment: Windows 10.0.26200, PowerShell 7.6.5, checkout-owned CPython 3.13.15
  x64 with exact `requirements-ci.lock`; pytest 9.1.1. No browser needed locally.
- Finding at cutoff: implementation ready and focused local checks complete.
  Hosted CI and merge remain pending at this committed evidence cutoff.

## Executed checks

| Cases | Command/procedure | Result | Evidence |
| --- | --- | --- | --- |
| Setup | `tools/check.ps1 -Mode Setup` | Passed; created this checkout's `.venv` and installed locked dependencies. | `.test-runs/20260914T192302919Z-41644-58eb57d8/result.json` |
| V-01, V-02, S-01, S-02, G-01 | Exact Verify command in the plan, with three explicit test files and seven syntax paths. | **46 passed**, zero failures/skips/warnings, pytest 7.44s; wrapper 11.472s. All Python/PowerShell syntax checks passed. | `.test-runs/20260914T192721018Z-1524-131aa59e/result.json`; source fingerprint `6df4696f7f70df303f9774f3e94ac7034f06b0883ddf89ac51323c8e08f44f04` |
| D-01/review | Bounded diff review of workflow dependencies, artifact audit, failure/recovery paths and documentation links; `git diff --check`. | No whitespace errors; selected/excluded Windows scope and both-shard audit are required by Merge ready. | This release diff. |

The synthetic child pytest failure/skip cases are intentional assertions within
the passing focused suite. They are not an application test failure or a removed
test. Existing Windows assertions were preserved. New fixtures demonstrate both
argument errors without `.venv` and continued environment rejection for valid
commands. Local setup succeeded with official x64 Python; no sibling or agent
environment was used.

## Pending evidence at cutoff

| Cases | Status | Next action |
| --- | --- | --- |
| C-01, C-02 | Pending final-head CI | Record PR SHA/run, all selected jobs, audited counts and actual timings in the PR before merging. |
| C-03 | Pending normal main-push run | After merge, append Windows SHA/run/result and timings to the PR evidence. |

## Findings and limitations

The baseline [PR #124 Ubuntu run](https://github.com/datap0nd/data_governance/actions/runs/34841296429)
spent 38m15s from workflow creation to Merge ready completion; its Python job was
37m56s. The latest ten PRs had a roughly 20-minute median final CI duration.
Those are historical observations, not a controlled benchmark: test counts,
runner load and setup costs differ. Shards balance test counts while keeping
files together; runtime balance and wall-time improvement need hosted evidence.
Two isolated runners add setup overhead and can increase total billed runner
time. All existing tests and assertions remain; no production timeout changes.

The baseline [Windows run](https://github.com/datap0nd/data_governance/actions/runs/34844907969)
had 2,104 passed, 13 skipped and two verifier argument-validation failures.
This release validates arguments before environment preflight, preserving the
environment checks for valid requests. Final Windows regression is independent
evidence and is not implied by the local or Ubuntu outcomes.

## Merge evidence

Not yet merged at this report's cutoff. The PR testing section will record the
final CI URL and exact tested head before the head-pinned merge, followed by the
normal main-push result at its separate merge SHA. Keep this committed cutoff
intact; later results supplement it rather than rewriting earlier outcomes.
