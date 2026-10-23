# 2026-10-23: Python Flows write their own email body: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-23 07:03
- Tested code revision: `a3855f1` (code, tests and plan; this report is the
  only later file)
- Environment: Linux container, Python 3.13.16 checkout-owned `.venv`
- Overall finding: every planned automated case passes locally on synthetic
  fixtures. Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| E-01–E-07, R-01 (email, Python worker) | `python tools/check.py verify --test tests/test_flow_email_delivery.py --test tests/test_flow_python.py --syntax app/flow_email_delivery.py --syntax app/flow_python.py --syntax app/flow_worker.py` | `a3855f1`, container above | PASS: 49 tests, 0 failed, 0 skipped, 2 third-party deprecation warnings, 30.1 s; syntax clean | `.test-runs/20261023T070142484Z-810-19a81fb8/result.json` |
| R-01 (portable, handover, standalone) | `python tools/check.py verify --test tests/test_flow_portable_script.py --test tests/test_flow_handover.py --test tests/test_flow_standalone.py` | `a3855f1`, container above | PASS: 31 tests, 0 failed, 0 skipped, 2 third-party deprecation warnings, 43.0 s | `.test-runs/20261023T070213723Z-926-bf7e0a65/result.json` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

No failures. The Outlook hand-off is captured, not launched: these tests prove
the payload (`html_body`, `inline_images`, `attachments`) that
`tools/outlook_task_email.ps1` already turns into inline attachments with a
content ID. A real Outlook send was not in scope. In `test_script_body_rules`
the symlink case runs only where the OS lets the test create a symlink.

## Merge evidence

Pending: final-head CI and the merge are recorded in the PR.
