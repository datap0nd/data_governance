# 2026-09-10 delivery foundation: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 12:47, before final-head CI
- Tested code revision: `d902b4a1f787f0c6543cd95862552a054871cdd1`; the later report-only commit does not change application, test, dependency or workflow sources
- Environment: Windows 11; PowerShell 7.6.5; Python 3.13.15
- Overall finding: local setup, preflight, isolation policy and aggregate-gate checks pass; final-head CI remains pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| DF-01 | `.\tools\check.ps1 -Mode Setup`, followed by `-Mode Preflight` | `d902b4a` code in the Windows checkout; PowerShell 7.6.5, Python 3.13.15 | PASS; setup 16.880 s and preflight 1.050 s | Local result IDs `20260910T124408622Z-19436-a0d87202` and `20260910T124429050Z-27780-0cae5f6f`. |
| DF-02–DF-04 | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_ci_merge_gate.py,tests/test_check_command.py -SyntaxPath tools/check.ps1,tools/ci/merge_gate.py,tests/test_ci_merge_gate.py,tests/test_check_command.py` | `d902b4a`; Windows 11, PowerShell 7.6.5, Python 3.13.15 | PASS; 10 passed, 0 failed/skipped, pytest 2.56 s, command 4.834 s | Result ID `20260910T124653160Z-30736-53690f8f`; source fingerprint `7601d55f435cb882f4d46391fafe5b3e1fcdd72b855002ccb19eaf9636c1cbb6`. |
| DF-03 | Temporarily replace `.venv/.metronome-ci-lock.sha256`, run Preflight, restore the marker in `finally`, then rerun Preflight | `d902b4a`; same environment | PASS; mismatched lock rejected before pytest with the Setup instruction; restored environment passed preflight | Result IDs `20260910T124704664Z-18904-ca952fea` and `20260910T124705043Z-32608-5d74160a`. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| DF-05–DF-06 | NOT RUN | These checks require the committed PR head. | Run CI, configure protection, record final evidence in the PR and merge only after success. |

## Findings, limitations and retests

The first focused attempt failed before pytest because the child PowerShell
command omitted its call operator (result
`20260910T124430150Z-27780-68c81f7f`). Adding the operator produced 7 passing
aggregate cases. A later combined run had 9 passes and one failed assertion:
PowerShell serialized an omitted string parameter as `""` rather than JSON
`null` (`20260910T124505313Z-3132-7c081b20`). The corrected failed node passed,
then the committed focused set passed all 10 cases. These were tooling/test
expectation defects found during implementation; no application regression was
run locally. GitHub Actions remains authoritative for the full regression and
hosted runner behavior.

## Merge evidence

Pending. Final CI may finish after this committed report is written. The PR
testing section will record the final run URL, exact tested head SHA and result
before merge.
