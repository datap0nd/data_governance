# 2026-09-10 delivery foundation: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-10 13:22, before the final corrected CI rerun
- Tested code revision: initial tooling at `d902b4a1f787f0c6543cd95862552a054871cdd1`; follow-up content committed as `97a8b9f4d3d6b96b572f4debbc458730da44c430` was tested as `691706a` plus the then-uncommitted `tools/check.ps1` and `tests/test_check_command.py` changes
- Environment: Windows 11; PowerShell 7.6.5; Python 3.13.15
- Overall finding: local setup, preflight, isolation policy and aggregate-gate checks pass; final-head CI remains pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| DF-01 | `.\tools\check.ps1 -Mode Setup`, followed by `-Mode Preflight` | `d902b4a` code in the Windows checkout; PowerShell 7.6.5, Python 3.13.15 | PASS; setup 16.880 s and preflight 1.050 s | Local result IDs `20260910T124408622Z-19436-a0d87202` and `20260910T124429050Z-27780-0cae5f6f`. |
| DF-02–DF-04 | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_ci_merge_gate.py,tests/test_check_command.py -SyntaxPath tools/check.ps1,tools/ci/merge_gate.py,tests/test_ci_merge_gate.py,tests/test_check_command.py` | `d902b4a`; Windows 11, PowerShell 7.6.5, Python 3.13.15 | PASS; 10 passed, 0 failed/skipped, pytest 2.56 s, command 4.834 s | Result ID `20260910T124653160Z-30736-53690f8f`; source fingerprint `7601d55f435cb882f4d46391fafe5b3e1fcdd72b855002ccb19eaf9636c1cbb6`. |
| DF-03 | Temporarily replace `.venv/.metronome-ci-lock.sha256`, run Preflight, restore the marker in `finally`, then rerun Preflight | `d902b4a`; same environment | PASS; mismatched lock rejected before pytest with the Setup instruction; restored environment passed preflight | Result IDs `20260910T124704664Z-18904-ca952fea` and `20260910T124705043Z-32608-5d74160a`. |
| DF-02 recovery | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_check_command.py,tests/test_flows.py::test_catalog_and_flow_configuration_persist_locally -SyntaxPath tools/check.ps1,tests/test_check_command.py` | `691706a` plus the exact two files later committed as `97a8b9f`; Windows 11, PowerShell 7.6.5, Python 3.13.15 | PASS; 4 passed, 0 failed/skipped, pytest 6.22 s, command 9.418 s | Result ID `20260910T125922331Z-13904-76549b7d`; source fingerprint `8b057a9d68de6717903617fcfacca79778e84f44f98776e0833d8ee32f839e87`. |
| DF-02 platform recovery | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_check_command.py -SyntaxPath tests/test_check_command.py` after marking the Windows-only command contract | `3431d45` plus the exact test change committed as `a5e27d2`; Windows 11, PowerShell 7.6.5, Python 3.13.15 | PASS; 3 passed, 0 failed/skipped, pytest 3.16 s, command 6.811 s | Result ID `20260910T131730768Z-33296-6c9de3ad`; source fingerprint `7191b213868473dbfd1a4dc64c412fa00a02f2e48816df467b2ab715bc2b8ffe`. |
| DF-02 cleanup recovery | Run the Windows command-contract tests and verify the exact external run path no longer exists after completion | `95ae97b` plus the exact cleanup content committed as `ccc64d1`; same environment | PASS; 3 passed in 3.12 s, command 5.590 s; external root absent | Result ID `20260910T132207454Z-35132-0d354bfb`; source fingerprint `425515bb7345ec956312fec4e67279e1c185028fa563193b845694b80efa5322`. |

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

A later focused Flow fixture exposed that the ignored run root is physically
inside the checkout, while managed Flow storage correctly rejects checkout
paths. The command now gives `DG_FLOWS_ROOT` its own unique external test path
without weakening production path validation; the command-policy tests and one
real Flow create/persist case then passed together. The earlier PR workflow run
was superseded because this code change requires new final-head evidence.

Final-head run `34479965537` then completed the full Ubuntu suite with 1,943
passes, 20 legitimate skips and two failures in 13m56s. Both failures were the
new local-command contract tests invoking the Windows `.venv\\Scripts` layout
on Ubuntu; application tests passed. The tests are now explicitly scoped to
Windows, where their focused rerun passed 3/3. The failed run and aggregate-gate
rejection are retained as evidence; a corrected final-head run is required.

The external Flow root cleanup now resolves the exact target, verifies it is a
descendant of `%LOCALAPPDATA%\\MetronomeTestRuns`, and removes only that run's
directory. The focused contract passed and an explicit post-run check found no
remaining directory for result `20260910T132207454Z-35132-0d354bfb`.

## Merge evidence

Pending. Final CI may finish after this committed report is written. The PR
testing section will record the final run URL, exact tested head SHA and result
before merge.
