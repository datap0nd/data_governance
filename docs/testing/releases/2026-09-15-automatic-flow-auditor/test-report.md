# Automatic Flow auditor: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff (UTC): 2026-09-15, before final-head CI.
- Tested code revision: final local verification pending on the implementation commit.
- Environment: Windows ARM64 host; Python 3.13.15 ARM64 diagnostic runtime; synthetic Chrome through Playwright 1.62.0. The final verifier records its selected checkout runtime.
- Overall finding: implementation diagnostics have exercised the revised API, containment, lifecycle and browser journey; the canonical affected run and final-head CI are pending at this report cutoff.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| A-03–A-07 | `python -m pytest -q tests/test_data_auditor.py tests/test_auditor_lifecycle.py tests/test_auditor_manifest.py --tb=short` after scheduler/model-config corrections | `0758b2f4…` plus uncommitted implementation; Windows/Python 3.13.15 | PASS, 33 passed, one existing Starlette TestClient deprecation warning, 20.66s | terminal output in task history |
| U-01–U-02 | `python -m pytest -q tests/test_auditor_browser.py --tb=short` | same uncommitted implementation; synthetic Chrome | PASS, 2 passed, one existing warning, 25.65s | task-owned `test_reports/automatic-flow-auditor-browser/` |
| A-02 | `python -m pytest -q tests/test_check_command.py::test_windows_acl_fixture_denies_reader_style_write_and_recovers --tb=short` after correcting the disposable ACL target | same uncommitted implementation; Windows ACL fixture | PASS, 1 passed, 0.46s | terminal output in task history |
| A-08 collection | `python -m pytest -q tests/test_auditor_postgres.py --tb=short -ra` without a disposable DSN | local host | 14 skipped as designed; no PostgreSQL result claimed | dedicated final CI required |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| A-01–A-07, U-01–U-02 canonical run | NOT RUN | Report was created before the one final affected verifier invocation. | Run the plan's exact command on the implementation commit and append its result/evidence. |
| A-08, R-01 | NOT RUN | Requires final PR head and disposable PostgreSQL 14/18 CI. | Record every required job, CI URL and final SHA in the PR Testing section before merge. |

## Usability evidence

The diagnostic browser pass exercised default On/all-Flows, failed and
successful time saves, Run-now outage/retry, Stop, Pause/Resume, navigation,
partial coverage and escaped evidence. No page errors were observed and the
390px page had no horizontal page overflow. Visual inspection found the global
button styling overrode the HTML `hidden` attribute for inactive Stop; a scoped
`[hidden]` rule was added and is included in the pending canonical retest.

## Findings, limitations and retests

- The first combined non-browser diagnostic had 12 expected failures while legacy tests still described operator keys, Flow selection and old settings. After updating them to the approved journey and correcting model-pin schedule preservation, the same selection passed 33 tests.
- The first browser diagnostic failed because its mocked catalog function did not accept the new frozen-config argument. The fixture was corrected without weakening application behavior; both browser cases then passed.
- A real Windows ACL diagnostic initially applied a recursive deny to its own directory and could not finish ACL enumeration. It was narrowed to the fixture file, then proved denied read/write and cleanup successfully.
- Synthetic model responses prove strict tool/schema/evidence validation, not real-model business accuracy. Unsupported formats, missing retained files, unsafe or mismatched SQL identities, append/concurrent targets, insufficient history and model outages remain explicit gaps.
- Application tool restrictions do not prove OS/network isolation of the separately deployed model server. The page and administrator guide state that remaining deployment responsibility.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR Testing section must record the final head SHA, run URL, required job results
and merge. No deployment or post-merge application test is claimed here.

