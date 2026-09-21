# Optional Flow SQL table owner: test plan

- Change/PR: Let a Flow retain its person owner and failure alerts while opting out of assigning that person's PostgreSQL role to the SQL target. PR link is recorded in the report after creation.
- Code baseline: `876c953622f6e463fd95e29858866e002809c913` (`origin/main` before this change).
- Related report: [test-report.md](test-report.md).
- Intended environments: isolated SQLite/HTTP fixtures on Windows and required final-head GitHub CI. Browser checks use fictional data in headless Chrome.

## Prerequisites and test data

Use the checkout-owned Python 3.13 environment, Node, and the existing test fixtures. The SQL catalog and person profile are synthetic. No PostgreSQL server or portal session is needed for these cases.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| O-01 | Create a SQL Flow with a person who has a SQL username; leave **Set SQL table owner from Flow owner** on. | The saved setting is on and its job contains the person's `owner_username`, preserving prior behavior. | `test_sql_handoff_target_is_persisted_without_executing_insert`; `test_flow_owner_can_receive_alerts_without_becoming_sql_table_owner` |
| O-02 | Create the same Flow with the option off; build its job. | Person ID/email remain, SQL handoff stays enabled, and the job has no `owner_username`. | `test_flow_owner_can_receive_alerts_without_becoming_sql_table_owner` |
| O-03 | Update that Flow with an older client payload omitting the new field, then explicitly turn it back on. | The omitted field preserves Off; explicit On restores SQL ownership assignment. | `test_flow_owner_can_receive_alerts_without_becoming_sql_table_owner` |
| O-04 | In Outlook, file, and portal builders, switch Off, save, reopen; inspect the owner help. | The option persists Off, the person remains selected, and help states SQL ownership is unchanged. | `test_flow_owner_sql_assignment_is_optional_in_each_builder`; [desktop](screenshots/desktop.png) and [mobile](screenshots/mobile.png) screenshots |
| O-05 | Read the owner help with the option at its default and inspect the existing SQL run log wording. | Existing owner guidance and committed-only run log remain correct. | `test_sql_owner_help_and_run_log_confirm_only_committed_changes` |
| O-06 | Inspect the Flow builder's serialized payload, JS syntax, Python syntax, and patch whitespace. | The checkbox value reaches the API and source parses cleanly. | Node contract, `node --check`, `py_compile`, `git diff --check` |
| C-01 | Wait for the PR's final-head `Merge ready` workflow. | All required checks pass against the exact head merged to `main`. | GitHub Actions run URL and head SHA in the PR testing section |

## Automated checks

Run the focused selectors in O-01 through O-05, the Node Flow builder contract, and syntax/whitespace checks. The repository's locked verifier setup is the preferred local route; record any host-specific setup blocker and the exact fallback. The final-head CI is authoritative for full regression.

## Usability evidence

Inspect the changed checkbox on desktop and mobile. Confirm the label and explanation are readable, the option appears only with SQL handoff settings, the Flow owner remains selected after saving Off, and the page has no horizontal overflow. Review failure and recovery through saved configuration and run-log help.

## Acceptance and cleanup

Accept when focused behavior passes and final-head CI reports `Merge ready`. The new setting defaults On so existing Flows retain their current behavior. To roll back a choice for one Flow, reopen it and turn the option back On. Synthetic test files remain in isolated test directories; no live SQL tables are touched.
