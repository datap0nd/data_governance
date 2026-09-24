# Python scheduler plan set: test plan

- Change/PR: plans only — `docs/plans/python_scheduler/` (README, three stage
  plans, delivery log) for `.env` Flow SQL credentials, "Just run it" Python
  Flows and script monitoring. No application, installer, test or workflow
  change.
- Code baseline: `main` `a3a3b62` (PR #142 merged).
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux agent container and required GitHub Actions CI.
  No live environment is in scope.

## Prerequisites and test data

None. The checks read only the repository.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| D-01 | `git diff --check origin/main...HEAD` | No whitespace errors | Command output |
| D-02 | Resolve every relative Markdown link in `docs/plans/python_scheduler/*.md`, this package, `docs/testing/releases/INDEX.md` and `docs/testing/README.md` (script in the report) | Every relative link points to an existing file | Script output |
| D-03 | Confirm every repository path the plans name as existing (for example `app/config.py`, `app/flow_python.py`, `app/flow_recorder_worker.py`, `setup.ps1`, `tests/test_auditor_managed.py`) exists at the baseline | All named existing paths exist; planned new files are named as new | Script output |
| D-04 | Final-head CI | `Change scope` classifies the PR as documentation-only (`application=false`) and `Merge ready` passes | Run URL and head SHA in the PR testing section |

## Automated checks

Local documentation checks D-01 to D-03. Required final-head CI (D-04) applies
the lightweight documentation scope gate.

## Acceptance and cleanup

Accepted when D-01 to D-03 pass locally and `Merge ready` passes on the final
head, with its run URL and SHA recorded in the PR. Nothing to clean up.
