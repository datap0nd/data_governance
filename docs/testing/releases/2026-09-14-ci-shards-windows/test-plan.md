# Isolated CI shards and Windows verifier gate: test plan

- Scope: faster full CI without dropping tests; early Windows verifier checks;
  argument errors remain actionable before the checkout environment exists.
- Baseline: `552f4821f4f5a0689ea59f17e0e8ffe63428ce69` (PR #124).
- Implementation: `fdaedef46182a9c7677702b9099e33b35349e691`.
- Related report: [test-report.md](test-report.md).
- Environments: checkout-owned Python 3.13 on Windows for focused local checks;
  GitHub Ubuntu/Windows runners for CI. Nested pytest/git fixtures are synthetic.

## Prerequisites and test data

Start from this branch, install official Python 3.13, PowerShell and Git, then
run `tools/check.ps1 -Mode Setup` once. Use only the checkout-owned `.venv` and
locked requirements. The focused cases create disposable test modules and an
empty Git checkout containing a copy of the verifier; that fixture deliberately
has no Python environment. No browser is required for the focused cases.

## Test cases

| ID | Actions | Expected result | Evidence |
| --- | --- | --- | --- |
| V-01 | Run `tests/test_check_command.py`, including the two existing Windows failure cases. | Invalid Verify selection/full-suite arguments fail with their intended message and a failed JSON result, with or without `.venv`. | Local result and Windows contract JUnit. |
| V-02 | In the synthetic no-environment fixture, supply a valid test selector, then call Preflight. | Both still reject the missing environment. No test runs and no `.venv` is created. Run Setup in a real checkout to recover. | Parameterized verifier cases. |
| S-01 | Run `tests/test_ci_sharding.py`; synthetic collection includes unequal file sizes, parametrized cases and a skipped case. | Every node ID belongs to exactly one shard; files stay together; both actual child pytest runs finish and their audited union includes the skipped case. | Focused result and assertions. |
| S-02 | The same file supplies missing/duplicate shards, stale SHA, failed/unfinished execution, overlapping/missing tests, differing collections and malformed artifacts. | Every damaged case fails the audit. A complete two-platform fixture passes. An actual failing child test remains failed. | Focused result. |
| G-01 | Run `tests/test_ci_merge_gate.py`. | Selected Windows failures/cancellation/skips and missing scope/result block merging. Explicitly excluded work remains accepted; existing Python/PostgreSQL rules remain enforced. | Focused result. |
| C-01 | Open the PR and inspect its final-head Tests run. | Two Ubuntu shards, Windows verifier contracts, frontend, selected PostgreSQL jobs and Merge ready pass. Merge ready downloads both manifests and reports complete Linux collection coverage. | Final run URL, head SHA, JUnit and manifest artifacts. |
| C-02 | Inspect each PR shard manifest/JUnit and job timestamps. | Equal collection sets, disjoint selections, all selected tests finished, no failed tests. Record full collected/pass/skip counts and actual wall time; compare with historical serial runs without claiming controlled benchmark equivalence. | CI artifacts and timestamp summary in PR. |
| C-03 | After merge, inspect the normal main-push run. | Full Windows collection runs across two isolated jobs and passes its coverage audit. Record its separate revision/result; do not infer this from the pre-merge Ubuntu result. | Main run URL, merge SHA and job outcomes in PR. |
| D-01 | Inspect README/index links and the delivery guidance. | Plan/report are linked; later CI results belong in the PR body; required code corrections still trigger final-head CI. | Diff/link review. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_check_command.py,tests/test_ci_merge_gate.py,tests/test_ci_sharding.py `
  -SyntaxPath tools/check.ps1,tools/ci/merge_gate.py,tools/ci/pytest_shard.py,tools/ci/shard_audit.py,tests/test_ci_sharding.py,tests/test_check_command.py,tests/test_ci_merge_gate.py
```

CI continues to collect `tests` using the locked dependencies. The shard plugin
is explicitly enabled only in CI, and each job runs sequentially. No xdist or
other dependency is added. Existing browser fixtures remain synthetic. A failed
shard or artifact upload blocks the final gate; fix the cause and run fresh CI on
the corrected head. Do not mask it with retries, skipped tests or allowed failure.

## Acceptance and cleanup

Require the focused checks, final-head Merge ready and normal Windows main run.
Retain GitHub JUnit/manifests for their configured 14 days and retain local result
IDs in the report. Pytest owns the synthetic temporary fixtures; do not remove
unrelated worktrees or test outputs. Revert the workflow, shard tools and gate
changes together if rollback is needed, keeping the standalone verifier fix when
appropriate. No application migration or user interface change is involved.
