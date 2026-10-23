# 2026-10-23: Python Flows write their own email body: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-10-23 07:29
- Tested code revision: `8e5844b` (code, tests and plan; this report is the
  only later file)
- Environment: Linux container, Python 3.13.16 checkout-owned `.venv`
- Overall finding: every planned automated case passes locally on synthetic
  fixtures. The first CI run (`a3855f1`) failed two worker invariants; fixed in
  `8e5844b` (below). Final-head CI is pending.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| E-01–E-07, R-01 (email, Python worker, worker invariants) | `python tools/check.py verify --test tests/test_flow_email_delivery.py --test tests/test_flow_python.py --test tests/test_flows.py::test_worker_source_contains_no_delete_or_overwrite_operation --test tests/test_flows.py::test_retention_module_is_the_only_deletion_site_and_gates_every_path --syntax app/flow_email_delivery.py --syntax app/flow_python.py --syntax app/flow_worker.py` | `8e5844b`, container above | PASS: 51 tests, 0 failed, 0 skipped, 2 third-party deprecation warnings, 33.3 s; syntax clean | `.test-runs/20261023T072640641Z-4434-c47c916e/result.json` |
| R-01 (portable, handover, standalone) | `python tools/check.py verify --test tests/test_flow_portable_script.py --test tests/test_flow_handover.py --test tests/test_flow_standalone.py` | `8e5844b`, container above | PASS: 31 tests, 0 failed, 0 skipped, 2 third-party deprecation warnings, 48.8 s | `.test-runs/20261023T072715152Z-4929-0fb537bb/result.json` |
| E-04 with Wizard's real output (synthetic) | Wizard's generated brief script run with real matplotlib 3.11.2 and openpyxl 3.1.5 (fake database driver), then `flow_email_delivery.script_body` on its run folder | `8e5844b`, scratch Python 3.13 environment | PASS: `body.html` and two PNG charts accepted (`chart-2.png`, `chart-3.png`, 50,966 bytes of images) | Console output |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Full regression | NOT RUN | Final-head CI (`Merge ready`) has not run at this cutoff | Record the run URL and tested head in the PR testing section |

## Findings, limitations and retests

The first final-head CI run on `a3855f1` failed
`test_worker_source_contains_no_delete_or_overwrite_operation` and
`test_retention_module_is_the_only_deletion_site_and_gates_every_path`: the
worker created the email folder and removed it with `.rmdir()` when the script
left it empty, and the worker must never delete anything. `8e5844b` makes the
worker only name the folder; the script creates it when it writes a body, and
run-folder retention removes it. Both invariants pass locally on `8e5844b`.

The Outlook hand-off is captured, not launched: these tests prove the payload
(`html_body`, `inline_images`, `attachments`) that
`tools/outlook_task_email.ps1` already turns into inline attachments with a
content ID. A real Outlook send was not in scope. In `test_script_body_rules`
the symlink case runs only where the OS lets the test create a symlink.

## Merge evidence

Pending: final-head CI and the merge are recorded in the PR.
