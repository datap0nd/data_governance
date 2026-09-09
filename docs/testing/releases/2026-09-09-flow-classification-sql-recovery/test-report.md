# Flow classification and replace-mode SQL recovery: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending
- Evidence cutoff (UTC): 2026-09-09T13:50:00Z
- Tested code revision: `75de7c0df79b9efa89e34b3f9646e67a9d1af0d1` for the complete Python suite; earlier affected checks used the same code before commit.
- Environment: Windows, Python 3.13.15 ARM64, pytest 8.3.5, Node.js 24.19.0; browser/live work-PC checks pending.
- Overall finding: all 140 affected Python tests and all affected frontend tests/checks pass. The local pytest process reports a Windows disabled-symbolic-link cleanup exception after writing the passing JUnit results; full-suite CI is pending. Live checks are NOT RUN.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Full Python regression, including SQL-01–SQL-04 and CLS API coverage | `python -m pytest -q --basetemp=C:\codex-pytest\flow-release-full --junitxml=test_reports/flow-classification-sql-recovery-full.xml` | `75de7c0df79b9efa89e34b3f9646e67a9d1af0d1`; Windows/Python 3.13.15 | PASS: 1,908 tests, 0 failures, 0 errors, 13 skipped, 810.285 s. Pytest cleanup then emitted WinError 1463 after writing JUnit. | `test_reports/flow-classification-sql-recovery-full.xml` |
| SQL-01, SQL-03, SQL-04, CLS-01–CLS-05 | `python -m pytest tests/test_flow_parallel.py tests/test_flow_recordings.py tests/test_view_refresh.py tests/test_flow_activity.py -q --basetemp=C:\codex-pytest\flow-release-affected --junitxml=test_reports/flow-classification-sql-recovery-affected.xml` | Pre-commit tree with the same tested code; Windows/Python 3.13.15 | PASS: 140 tests, 0 failures, 0 errors, 0 skipped, 93.963 s. Pytest cleanup then emitted WinError 1463. | `test_reports/flow-classification-sql-recovery-affected.xml` |
| SQL-01, SQL-03, CLS-01–CLS-05 | `node tests/test_flow_parallel_display.mjs`; `node tests/test_flows_display.mjs`; `node tests/test_flow_groups.mjs`; `node tests/test_flow_activity_polling.mjs`; `node --check app/static/app.js`; `node --check app/static/flow_run_log.js` | Uncommitted tree; Windows/Node 24.19.0 | PASS: 4 test scripts and 2 syntax checks | Console output |

## Unperformed or blocked checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| Final GitHub Actions | NOT RUN | PR not yet opened. | Record final head SHA, workflow link, and result in the PR testing section before merge. |
| SQL-02 live upgrade | NOT RUN | Deployed app has not updated to this revision. | Restart the merged app and confirm the stale replace flag is cleared. |
| Live portal/work-PC checks | NOT RUN | No live portal, authentication, SQL interruption, or protected business data was used. | Owner may execute SQL-01/02 and CLS-01–05 on the deployed work PC. |

## Findings, limitations and retests

- The Windows host currently permits creation of pytest temporary `current` links but forbids following their type. Assertions complete, then pytest cleanup raises WinError 1463. This is recorded as a warning rather than evidence of a product failure; GitHub Actions provides the clean Linux result.
- The owner explicitly declined further web previews and directly specified/approved the Production/Draft tab journey. No preview artifact is retained.
- Classification changes metadata only. They do not silently pause schedules, stop active runs, or disable manual execution.

## Merge evidence

Pending. The PR testing section will record final GitHub Actions URL, tested head SHA, and result before merge. The committed report captures evidence only through the cutoff above.
