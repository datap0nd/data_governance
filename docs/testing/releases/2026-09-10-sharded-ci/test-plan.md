# 2026-09-10 sharded CI and safe scope selection: test plan

- Change/PR: PR 2 of the faster-delivery rollout: six-way OS sharding, safe range classification, inventory reconciliation, browser reuse and measured empty-database fixture reuse
- Code baseline: PR 1 merge; repository CI/tooling and synthetic test fixtures
- Related report: [test-report.md](test-report.md)
- Intended environments: local Windows 11/Python 3.13 focused tooling tests; GitHub-hosted Ubuntu and Windows runners, real installed browser channels and synthetic browser fixtures; PostgreSQL 14/18 containers when selected

## Prerequisites and test data

Use the checkout-owned `.venv` from `tools/check.ps1 -Mode Setup`. CI uses only
checked-in fictional fixtures, unique runner-temporary database/profile roots
and Playwright caches keyed by exact dependency lock and OS. Keep the serial
and parallel jobs on the same commit and dependency lock for equivalence.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| SC-01 | Classify documentation/policy only, mixed, frontend, known Windows-sensitive, unknown backend, SQL, test and workflow-orchestration path sets. For pushes, offer successful docs and full-Windows ancestor runs. | Docs-only uses the lightweight gate; mixed/application is never exempt; unknown backend selects Windows; SQL selects PostgreSQL; orchestration selects same-head equivalence; the newest full-Windows ancestor is the push baseline. | Focused classifier/baseline tests. |
| SC-02 | Build two six-shard plans from the same inventory in different input orders, including one unlisted new test file. | Both plans are identical; every OS group is nonempty and the complete inventory appears exactly once; the new file uses the conservative weight. | Focused partitioner tests and uploaded plan. |
| SC-03 | Reconcile success plus missing result, duplicate/missing assignment, failed/cancelled-equivalent exit, collection error, unexpected skip/outcome, stale plan and serial mismatch fixtures. | Only exact successful coverage passes; each negative case fails with a specific cause. | Focused reconciliation tests. |
| SC-04 | On the final PR head, run full serial and six-shard suites for Ubuntu and Windows, then compare collected node IDs and outcomes. | Collection and outcomes are identical per OS, with legitimate platform skips counted; migrations/startup tests still use real initialization. | Final PR workflow artifacts/jobs. |
| SC-05 | Run the parallel workflow three consecutive times on the same final head after equivalence succeeds. | All three complete without a new flaky failure and reconcile exact inventory once. | Three GitHub run URLs and SHAs. |
| SC-06 | Compare a cold browser-cache run with a warm run; inspect channel evidence. | Only missing required channels install; Chrome/Edge/Chromium identities stay distinct; warm setup is faster or the measured bottleneck is retained in the report. | Browser evidence artifacts and job timing. |
| SC-07 | Compare the copied empty-database schema with the session template, mutate one copy, then create another; run affected Flow fixture coverage. | Schemas match, mutations do not leak, and tests exercising startup/migration are not routed through the copy fixture. Setup/call/teardown timing is recorded separately. | Focused fixture test and CI manifests. |
| SC-08 | Exercise documentation PR/push, application followed by cancelling documentation push, Windows-sensitive, SQL and unknown paths through classifier fixtures. | Required jobs match policy and `Merge ready` rejects any missing/cancelled/unexpectedly skipped selected job. | Focused tests; workflow run where applicable. |

## Automated checks

Locally run one set containing `tests/test_ci_change_scope.py`,
`tests/test_ci_sharding.py`, `tests/test_ci_merge_gate.py` and a minimal Flow
fixture case, with syntax checks for changed Python/PowerShell tooling. Do not
run a local full suite. Final-head CI performs authoritative serial equivalence
and complete sharded coverage on both OSes because orchestration and test
fixtures changed. After the equivalence run, dispatch two additional
parallel-repeat runs on the unchanged head to make three consecutive parallel
executions total.

## Acceptance and cleanup

Accept exact inventory/outcome equivalence, three clean parallel executions,
successful PostgreSQL selection, a strict aggregate gate and recorded cold/warm
timings. If a performance target is missed, keep the fastest equivalent
configuration and document the bottleneck; never omit tests. CI artifacts have
14-day retention. Local `.venv`/`.test-runs` are disposable. Revert the PR to
restore serial CI; do not bypass protection.
