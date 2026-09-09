# Faster pull-request CI: test plan

- Change/PR: split the monolithic cross-platform workflow into a fast pull-request gate and full post-merge/nightly coverage
- Code baseline: `e6f2ca9262bb03f759592efdde040cd0d3175e1a`
- Related report: [test-report.md](test-report.md)
- Intended environments: local Windows validation and GitHub-hosted Ubuntu, Windows and PostgreSQL runners

## Prerequisites and test data

- Start from current `origin/main` without changing application code or production data.
- Use fictional/repository test fixtures only. No portal login, work-PC session, database credentials or live deployment is required.
- Confirm the workflow files parse as YAML and GitHub accepts them when the branch is pushed.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| CI-01 | Parse both files under `.github/workflows/` and inspect their trigger, matrix, concurrency and path-filter values. | PRs select Ubuntu Python plus the parallel frontend job; pushes to `main` and scheduled/manual runs select Ubuntu and Windows; superseded PR runs cancel; PostgreSQL runs only for relevant paths plus scheduled/manual runs. | Local command output and diff |
| CI-02 | Compare the frontend commands before and after the refactor, ignoring the duplicate `test_flows_display.mjs` command. | Every unique Node contract test and JavaScript syntax check remains present exactly once. | Local comparison output |
| CI-03 | Run every Node contract test and JavaScript syntax command in the refactored frontend job. | Every command exits successfully. | Local command output |
| CI-04 | Open the pull request and wait for its final-head workflows. | Ubuntu Python, frontend, PostgreSQL 14 and PostgreSQL 18 pass; no Windows PR job is created. | GitHub Actions run URLs |
| CI-05 | After merge, inspect the `main` push workflow. | Both Ubuntu and Windows Python jobs are created, proving full cross-platform coverage remains after merge. | GitHub Actions run URL |
| CI-06 | Push a superseding commit only if a correction is necessary while an earlier PR run is active. | The earlier PR run is cancelled and only the latest head remains authoritative. | GitHub Actions history; N/A if no correction is needed |

## Automated checks

```powershell
python -c "import yaml; [yaml.safe_load(open(path, encoding='utf-8')) for path in ('.github/workflows/tests.yml', '.github/workflows/sql-ownership.yml')]"
git diff --check origin/main...HEAD
```

Run the Node commands listed in the `frontend` job. The GitHub PR workflow is the authoritative execution test for the dynamic OS matrix and service-container jobs. A local full Python run is not required because application execution code and tests are unchanged; the final-head Ubuntu suite and post-merge Windows suite cover accidental workflow omissions.

## Acceptance and cleanup

Accept when CI-01 through CI-04 pass and the merged `main` run demonstrates CI-05. CI-06 may be N/A. No live checks or cleanup are required. Rollback is a revert of the workflow, CI requirements and release-documentation commit; it does not affect application data.
