# Faster pull-request CI: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-09T12:07:17Z; final-head checks pending
- Tested code revision: uncommitted workflow and documentation changes based on `e6f2ca9262bb03f759592efdde040cd0d3175e1a`
- Environment: Windows 11 ARM64, Python 3.13.15, Node v24.19.0; GitHub-hosted environments pending
- Overall finding: local workflow structure, command preservation, documentation and all frontend commands pass; hosted workflow execution pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| CI-01 | Parse both workflow files with PyYAML; inspect trigger, matrix, concurrency and path-filter definitions; run `git diff --check` | Uncommitted tree based on `e6f2ca92`; Windows/Python 3.13.15 | PASS; both YAML files parsed and the diff check reported no whitespace errors | Local console output `2026-09-09T12:07:17Z` |
| CI-02 | Compare every `run: node` command in `origin/main` with the refactored workflow | Same tree/environment | PASS; old 29 commands/28 unique, new 28 commands/28 unique, with no unique command added or lost | Local console output |
| CI-03 | Execute all commands from the frontend job in sequence | Same tree; Node v24.19.0 | PASS; 28/28 commands exited successfully | Local console output |
| Documentation | Resolve relative Markdown links in the plan, report and testing index | Same tree/environment | PASS; all referenced local files exist | Local console output |
| CI dependency manifest | `python -m pip install --dry-run -r requirements-ci.txt` | Same tree; Windows ARM64 | BLOCKED during `psycopg2-binary` metadata preparation because PyPI does not provide the hosted-runner x64 wheel for this local ARM64 interpreter and local `pg_config` is absent | Local console output; hosted x64 CI is the required retest |
| CI-04 | Final-head pull-request workflows | Pending | NOT RUN | Pending |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| CI-05 | NOT RUN | Requires merge to `main`. | Inspect the post-merge workflow and record the jobs. |
| CI-06 | NOT RUN | Applicable only if a superseding PR commit is pushed during an active run. | Record N/A if no correction is needed. |
| Live portal/work-PC | N/A | CI orchestration only; no application journey, authentication, portal or production behavior changes. | None. |

## Findings, limitations and retests

The workflow refactor does not change application code. The dependency dry-run exposed the existing Windows ARM64 limitation for `psycopg2-binary`; the former inline CI install used the same package, and GitHub's x64 runners are the relevant environment. GitHub Actions is the authoritative validator for dependency installation, event expressions, service containers and hosted-runner behavior. The committed report records evidence available at its cutoff; final CI and merge evidence will be preserved in the PR.

## Merge evidence

Pending final-head CI and merge.
