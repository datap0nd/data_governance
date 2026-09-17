# Python-script arguments and setup fixes: test report

- Plan: [test-plan.md](test-plan.md).
- Change/PR: pending (the PR link is added when it is opened).
- Evidence cutoff (UTC): not yet recorded.
- Tested code revision: not yet recorded (the working tree of branch `claude/jolly-franklin-amklm0` on top of `origin/main` `9164347`).
- Environment: Linux container, Python 3.13.12 in the checkout-owned `.venv` (`requirements-ci.lock`); Node for the contract tests; Playwright with the bundled headless shell (no Chrome channel, no PowerShell).
- Overall finding: not yet recorded. No live, work-PC, portal or PowerShell check was requested or performed.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |

No result is recorded yet; the rows are filled from the verifier's
`result.json` files once the checks below have run.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| A-01 to A-17, S-02, S-03 | NOT RUN | Backend verifier set not yet recorded. | Run the backend/preview command from the plan and record counts and the `.test-runs/<id>/result.json` path. |
| S-01, S-04 | NOT RUN | No PowerShell on the Linux runner; the line check in `tests/test_flow_worker_startup.py` is part of the verifier set. | Record the verifier result; note the `--syntax setup.ps1` skip. |
| U-01 to U-07 | NOT RUN | `tests/test_flow_builder_contract.mjs` and `tests/test_python_scripts_preview.py` not yet recorded. | Run the frontend command and the preview walk (the verifier set includes it) and record the results. |
| Full regression (all suites, Windows contracts, Chrome-channel preview tests) | NOT RUN | Final-head CI has not run; the PR is not open yet. | Open the PR and wait for the required `Merge ready` check on the final head; record its run URL and tested head SHA here and in the PR. |

## Usability evidence

Not yet recorded. The preview walk covers U-01 to U-07 with fictional
in-memory data only; screenshots are saved to `PREVIEW_EVIDENCE_DIR` when set.

## Findings, limitations and retests

- Not yet recorded.
- Synthetic limits: SQL insertion and materialized-view refresh are
  monkeypatched (no PostgreSQL); scripts are throw-away fixtures written by
  the tests; the authentication helper's browser call is captured, not run;
  `setup.ps1` is read, not executed (no PowerShell on Linux runners).

## Merge evidence

Final CI is pending. The PR carries the final `Merge ready` run URL, tested
head SHA and result in its testing section before the head-pinned merge; the
PR merge record supplies the merge SHA.
