# 2026-09-10 sharded CI and safe scope selection: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: PR 2 of the faster-delivery rollout; pull request pending
- Evidence cutoff (UTC): 2026-09-10 13:42, before hosted final-head validation
- Tested code revision: `044cd6ae4b839f15525abdb782eaccf4002fec5c`
- Environment: Windows 11, PowerShell 7.6.5, Python 3.13.15 locally; GitHub-hosted environments pending
- Overall finding: focused classifier, partitioner, reconciliation, aggregate-gate and fixture checks pass locally; hosted serial/parallel equivalence and three consecutive runs remain pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| SC-01–SC-03, SC-07–SC-08 | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_ci_change_scope.py,tests/test_ci_sharding.py,tests/test_ci_merge_gate.py,tests/test_check_command.py,tests/test_flows.py::test_catalog_and_flow_configuration_persist_locally` with syntax selection for every changed Python/PowerShell source | `044cd6a`; Windows 11, PowerShell 7.6.5, Python 3.13.15 | PASS; 33 passed, 0 failed/skipped in 6.91 s; 11.568 s command | Result ID `20260910T134201915Z-7812-0b1feca4`; source fingerprint `e996daab6f72e7b73c7e751377a0fc888c727271c3ff926a65e83c4633b5227b`. |
| SC-02 | Independently inventory tests and build Ubuntu/Windows plans with `tools/ci/sharding.py` | `044cd6a`; same environment | PASS; 103 Python test files, exactly six nonempty shards per OS; estimated Ubuntu groups 275–285 s and Windows groups 345–355 s | Local `inventory-final.json` and `plan-final.json` (disposable evidence). |
| SC-07 | `tools/ci/benchmark_db_fixture.py --iterations 7` | `044cd6a`; same environment | PASS; schemas equal; direct initialization median 0.343031 s; one template setup 0.359059 s; per-test copy median 0.000807 s | Local `db-benchmark-final.json` (disposable evidence). |
| SC-03, SC-08 | `actionlint` 1.7.12 against `.github/workflows/tests.yml` | `044cd6a`; same environment | PASS; no workflow syntax or expression findings | Command exit 0, no output. |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| SC-04–SC-06 | NOT RUN | Require the final committed PR head and hosted runners. | Run equivalence, then two unchanged-head parallel repeats, and record all run evidence. |

## Findings, limitations and retests

The first combined local run found two tooling-test defects: serial fixture
outcomes were compared without normalizing order, and the supported command
initially placed the Flow root inside the checkout. Targeted reruns passed
after normalizing outcomes and using the already-isolated external Flow root.
The database benchmark initially exposed a module-path invocation error and an
unclosed SQLite connection; the corrected benchmark uses module-aware startup
and explicit connection closing. These failures are retained rather than
rewritten as passes. The exact rebased head then passed the complete focused
set above.

Historical duration weights are OS-specific but partial; unlisted files
deliberately receive a conservative default until successful manifests provide
measured updates. The hosted equivalence run remains authoritative for full
collection/outcomes and browser timing.

## Merge evidence

Pending. The PR testing section will record final-head CI, exact SHA, all three
parallel run URLs and merge evidence after the required check succeeds.
