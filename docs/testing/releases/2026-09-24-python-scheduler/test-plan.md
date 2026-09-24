# Python scheduler, `.env` credentials and script monitoring: test plan

- Change/PR: [PR #143](https://github.com/datap0nd/data_governance/pull/143),
  delivered in one go at the owner's request — the plan set in
  `docs/plans/python_scheduler/` plus its three parts: Flow SQL credentials
  from `.env`, "Just run it" Python Flows, and script monitoring with run
  history.
- Code baseline: `main` `a3a3b62` (PR #142 merged).
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux agent container with the checkout-owned
  `.venv`, Node contract tests, the fictional browser preview through
  Playwright, and required GitHub Actions CI. No live, work-PC or portal
  environment is in scope.

## Prerequisites and test data

None. The checks read only the repository.

## Test cases

### Plan documents (checked at the plan-only head)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| D-01 | `git diff --check origin/main...HEAD` | No whitespace errors | Command output |
| D-02 | Resolve every relative Markdown link in `docs/plans/python_scheduler/*.md`, this package, `docs/testing/releases/INDEX.md` and `docs/testing/README.md` (script in the report) | Every relative link points to an existing file | Script output |
| D-03 | Confirm every repository path the plans name as existing (for example `app/config.py`, `app/flow_python.py`, `app/flow_recorder_worker.py`, `setup.ps1`, `tests/test_auditor_managed.py`) exists at the baseline | All named existing paths exist; planned new files are named as new | Script output |
| D-04 | Final-head CI | `Merge ready` passes on the final head (a plan-only head is classified documentation-only) | Run URL and head SHA in the PR testing section |

### Implementation (added while implementing)

The implementing agent adds stable case IDs here from the **Tests** section of
each part — `E-` for [Part 1](../../../plans/python_scheduler/01_env_credentials.md),
`R-` for [Part 2](../../../plans/python_scheduler/02_run_mode.md), `M-` for
[Part 3](../../../plans/python_scheduler/03_script_monitoring.md) and `U-` for the
preview walkthrough — with exact actions, observable expected results and the
evidence each needs.

## Automated checks

Documentation checks D-01 to D-03 at the plan-only head. For the
implementation, the smallest affected `python tools/check.py verify` set per
part, `node tests/test_flow_builder_contract.mjs` and the new `.mjs` tests,
`node --check` on changed JavaScript, and `git diff --check`; required
final-head CI (D-04) is the full regression.

## Usability evidence

The fictional preview `app/static/recording-preview/python-scheduler.html`,
walked by Playwright at 1280×900 and 390×844. The owner waived the stop for
preview feedback ("I want it done in one go please"), so the walkthrough is
evidence, not an approval gate.

## Acceptance and cleanup

Accepted when every in-scope case has a recorded result, the final-head
`Merge ready` passes with its run URL and SHA in the PR, and the PR merges
pinned to that head. Test databases, Flow roots and synthetic scripts are
per-run fixtures removed by the verifier on success. Rollback: see the plan
README.
