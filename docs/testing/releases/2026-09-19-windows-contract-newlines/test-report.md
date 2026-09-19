# Cross-platform Flow builder contract: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff (UTC): **2026-09-19 14:03:16 UTC**. Final-head CI follows this committed report and is recorded in the PR before merge.
- Tested code revision: baseline `864d4eba2b3948837a153d72a01a12e91ba6b683` plus the uncommitted test correction with verifier source fingerprint `534c53fce5a91d9c53e9ffbcbf0a2654f803bcf9394fb0fccc3540e9d7b85e3d`.
- Environment: Microsoft Windows `10.0.26200`, PowerShell `7.6.5`, checkout-owned Python `3.13.15`, Node `24.19.0`, exact `requirements-ci.lock`.
- Overall finding: the focused Windows contract and Node syntax check passed after newline normalization. Final-head CI remains pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Main failure | Merged-commit `main` regression. | `864d4eba2b3948837a153d72a01a12e91ba6b683`; GitHub Windows runner, Python 3.13.15, Node 22.23.2 | **FAIL:** 1 failed, 1,144 passed, 14 skipped, 1,159 deselected; `test_flow_builder_frontend_contract` expected LF between two source statements while the checkout contained CRLF. | [Run 35445107514, shard 0](https://github.com/datap0nd/data_governance/actions/runs/35445107514/job/105902603219) |
| Setup prerequisite | `.\tools\check.ps1 -Mode Setup` | Baseline plus correction; Windows/Python 3.13.15 | PASS; checkout-owned locked environment created. | Run `20260919T140233933Z-39724-9f7973c8`. |
| N-01–N-03 | Exact focused verifier command from the plan. | Baseline plus correction; source fingerprint `534c53fc…`; Windows/Node 24.19.0 | **PASS: 1 passed, zero failures/errors/skips; Node syntax passed; 0.43s pytest / 4.769s verifier.** | [Local verification record](evidence/local-verification.json); run `20260919T140303391Z-38164-69dd0afa`. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| R-01 | NOT RUN | Final-head CI starts after the PR is opened. | Record the exact head SHA and `Merge ready` run in the PR before merging. |

## Findings, limitations and retests

The failing contract launches Node from pytest on a Windows runner. Git checkout
converted `app/static/app.js` to CRLF, while two source-shape assertions embedded
a literal LF. JavaScript behavior was not failing; every preceding Flow builder
contract section passed. The correction normalizes source text inside the test
harness before parsing or matching it, so application code and runtime newline
handling are unchanged.

The focused rerun passed on the same Windows platform class that exposed the
failure. A bounded diff review confirmed the only executable change is newline
normalization in the Node test harness; both neighboring multi-line assertions
still execute, and no application or UI file changed. `git diff --check` passed
with only expected checkout line-ending notices.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR testing section must record the final head SHA, run URL, required result, and
merge outcome. No deployment is claimed.
