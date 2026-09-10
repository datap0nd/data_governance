# 2026-09-10 delivery foundation: test plan

- Change/PR: PR 1 of the faster-delivery rollout: reproducible local verification, exact CI dependencies and the enforced aggregate merge gate
- Code baseline: `1b0b9ea`; repository tooling and synthetic CI fixtures only
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows 11 with PowerShell and Python 3.13 for local checks; GitHub-hosted Ubuntu, Windows and PostgreSQL service containers for CI

## Prerequisites and test data

Use a clean checkout with Git, PowerShell and official Python 3.13. Keep the
checkout-owned `.venv` and ignored `.test-runs/` directory disposable. CI tests
use repository fixtures and disposable databases only. No operational database
or profile path is an input.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| DF-01 | Run `.\tools\check.ps1 -Mode Setup`, then `-Mode Preflight`. | `.venv` uses Python 3.13, matches the lock, imports required modules and emits a JSON result with revision and duration. | Local console and `.test-runs/<run>/result.json` summary. |
| DF-02 | Run `-Mode Verify -TestPath tests/test_ci_merge_gate.py -SyntaxPath tools/check.ps1,tools/ci/merge_gate.py`. | Explicit focused tests and syntax checks pass; test DB, temp, profile, JUnit and result files stay below one unique `.test-runs/` root. | Local JUnit/result summary. |
| DF-03 | Invoke Verify without selectors; invoke `-Full` without a diagnostic reason; alter the lock fingerprint after Setup in an isolated fixture. | Each case stops before pytest with one actionable diagnostic. Full local execution cannot become the routine default. | Automated assertions or captured process result. |
| DF-04 | Exercise aggregate inputs for successful app, documentation-only and PostgreSQL selections, plus failed, cancelled, missing, unexpectedly skipped and unexpectedly executed jobs. | Only an exact, successful selected graph is accepted. | `tests/test_ci_merge_gate.py`. |
| DF-05 | Open the PR and inspect the final-head workflow. | `Merge ready` stays pending until all selected jobs finish and succeeds only when their results are complete; PR body records run URL and SHA. | GitHub Actions run and PR testing section. |
| DF-06 | Attempt the authorized merge only after protection is configured; verify the configured `main` rule. | PRs and strict `Merge ready` are required for administrators; force pushes and deletion are blocked; no human approval is required. | Sanitized branch-protection response and merge record. |

## Automated checks

Run DF-02 as the smallest affected local set. Parse the workflow YAML and
PowerShell/Python syntax in that same focused command. Required final-head CI
provides the full regression because this change affects tests, dependencies
and workflow orchestration. Do not run a duplicate local full suite.

## Acceptance and cleanup

Accept when focused checks pass, the final-head aggregate check passes, branch
protection matches DF-06 and the PR is merged at its tested head. Preserve only
sanitized JSON/JUnit summaries in ignored local storage; remove `.venv` and
`.test-runs/` if a clean local reset is desired. Roll back by reverting the PR;
do not bypass the gate.
