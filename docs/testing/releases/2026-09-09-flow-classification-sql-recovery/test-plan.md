# Flow classification and replace-mode SQL recovery: test plan

- Change/PR: SQL replace recovery plus Production/Draft Flow classification; PR link is added before merge.
- Code baseline: `ff6f9e83f7bed1bd47e44e734ec344e0a8abe2fd` (`origin/main` at branch creation).
- Related report: [test-report.md](test-report.md)
- Intended environments: Windows local Python/Node tests, GitHub Actions, and the deployed Windows app/worker for live confirmation.

## Prerequisites and test data

- Update the app and workers to the merged `main` revision and restart the app once so the SQLite migration runs.
- Use fictional or isolated Flow settings. Do not expose portal URLs, credentials, or downloaded business data in evidence.
- Prepare one replace-mode SQL Flow and, for the negative safety case, one append-mode fixture.
- The owner specified the journey directly: two inner tabs named **Production** and **Draft flows**, with a reversible classification action. The owner also directed that no web preview be shown. Classification is therefore metadata only; it does not pause schedules or prevent manual runs.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| SQL-01 | For a replace-mode Flow, simulate a worker interruption after `sql_insertion` and before `sql_insertion_complete`, then request a fresh run. | No reconciliation flag is created and the fresh run can be queued because truncate/refill is one PostgreSQL transaction. | Automated test output and run/event IDs. |
| SQL-02 | Start with a historical `sql_reconciliation_required=1` on a replace-mode Flow, restart the app, and select **Run**. | Startup clears the stale flag and the Flow queues normally. | Sanitized DB assertion and run ID. |
| SQL-03 | Simulate the same uncertain outcome for append mode. | Reconciliation remains required; Retry SQL, Resume, and a fresh run remain fenced until acknowledged. | Automated test output. |
| SQL-04 | Complete SQL, then fail a downstream materialized-view refresh. | SQL remains recorded as committed, reconciliation is not shown, and view-only retry remains available. | Automated test output. |
| CLS-01 | Open **Flows** after upgrade with existing records. | **Production** is selected; all existing records appear there and counts match the two classifications. | Browser screenshot with fictional data. |
| CLS-02 | From a Production Flow’s **More** menu choose **Move to Draft**. Switch between both tabs. | The action reports success, the Flow disappears from Production and appears under Draft flows; configuration, schedule, and run history are preserved. | Browser screenshot and API response. |
| CLS-03 | From a Draft Flow choose **Move to Production**. | The Flow returns to Production and the counts update. | Browser screenshot and API response. |
| CLS-04 | Attempt an unsupported classification through the API and simulate a failed classification save. | The API rejects the value; the current tab remains usable and the Flow stays in its prior classification with a visible error toast. | Automated API/UI test output. |
| CLS-05 | Run, edit, activate/pause, sort, expand groups, and inspect active-run progress in each classification. | Existing controls behave as before; moving a Flow does not change execution or schedule settings. | Automated regression output and browser evidence. |

## Automated checks

Run the affected Python suites and frontend tests/syntax checks, followed by the complete Python suite because SQL execution and startup migration behavior changed:

```powershell
python -m pytest tests/test_flow_parallel.py tests/test_flow_recordings.py tests/test_view_refresh.py tests/test_flow_activity.py -q
node tests/test_flow_parallel_display.mjs
node tests/test_flows_display.mjs
node tests/test_flow_groups.mjs
node tests/test_flow_activity_polling.mjs
node --check app/static/app.js
node --check app/static/flow_run_log.js
python -m pytest -q
```

## Acceptance and cleanup

Accept when replace-mode recovery queues without reconciliation, append protection still works, classification round-trips through SQLite/API/UI, all existing Flows default to Production, and regression checks pass. Live portal authentication and a work-PC PostgreSQL interruption are NOT RUN unless explicitly exercised. Remove isolated test databases and retain only sanitized evidence. Rollback is the prior `main`; the additive classification column may remain harmlessly in SQLite.
