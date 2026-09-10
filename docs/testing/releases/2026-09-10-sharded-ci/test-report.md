# 2026-09-10 sharded CI and safe scope selection: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: PR 2 of the faster-delivery rollout; pull request pending
- Evidence cutoff (UTC): 2026-09-10 14:45, before corrected final-head equivalence
- Tested code revisions: initial focused set at `044cd6ae4b839f15525abdb782eaccf4002fec5c`; Windows prerequisite recovery at `abde1e6c0d451cf661c3412b4c612acc5cf5bc88`; measured duration updater/plan at `dcd13ba7a79a849ebf8cb18cf6ec234652726720`; synthetic click stabilization at `ae47c520663da46472d338b08bbaa36167240edc`
- Environment: Windows 11, PowerShell 7.6.5, Python 3.13.15 locally; GitHub-hosted environments pending
- Overall finding: focused classifier, partitioner, reconciliation, aggregate-gate and fixture checks pass locally; hosted serial/parallel equivalence and three consecutive runs remain pending

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| SC-01–SC-03, SC-07–SC-08 | `.\tools\check.ps1 -Mode Verify -TestPath tests/test_ci_change_scope.py,tests/test_ci_sharding.py,tests/test_ci_merge_gate.py,tests/test_check_command.py,tests/test_flows.py::test_catalog_and_flow_configuration_persist_locally` with syntax selection for every changed Python/PowerShell source | `044cd6a`; Windows 11, PowerShell 7.6.5, Python 3.13.15 | PASS; 33 passed, 0 failed/skipped in 6.91 s; 11.568 s command | Result ID `20260910T134201915Z-7812-0b1feca4`; source fingerprint `e996daab6f72e7b73c7e751377a0fc888c727271c3ff926a65e83c4633b5227b`. |
| SC-02 | Independently inventory tests and build Ubuntu/Windows plans with `tools/ci/sharding.py` | `044cd6a`; same environment | PASS; 103 Python test files, exactly six nonempty shards per OS; estimated Ubuntu groups 275–285 s and Windows groups 345–355 s | Local `inventory-final.json` and `plan-final.json` (disposable evidence). |
| SC-07 | `tools/ci/benchmark_db_fixture.py --iterations 7` | `044cd6a`; same environment | PASS; schemas equal; direct initialization median 0.343031 s; one template setup 0.359059 s; per-test copy median 0.000807 s | Local `db-benchmark-final.json` (disposable evidence). |
| SC-03, SC-08 | `actionlint` 1.7.12 against `.github/workflows/tests.yml` | `044cd6a`; same environment | PASS; no workflow syntax or expression findings | Command exit 0, no output. |
| SC-04 recovery | Rerun only the eight failed Windows cases from command-contract and synthetic recorded-click shards after moving argument validation before environment preflight and increasing the fixture-only click budget from 500 ms to 2 s | `abde1e6` content on parent `6495675`; same local environment, synthetic Chrome fixture | PASS; 8 passed in 5.81 s; 8.788 s command | Result ID `20260910T135523170Z-38448-2231f246`; source fingerprint `cfb2486c4a067dbf61e2d6d642279acd14dd35d81f19670d2613c6794bc67d24`. |
| SC-02 measured rebalance | Aggregate all six JUnit artifacts per OS from run 34484474215 with `tools/ci/update_durations.py`, then independently rebuild the plan | Recovery content after `abde1e6`; same local environment | PASS; all 103 files weighted; measured totals 845.673 s Ubuntu and 1,283.904 s Windows; planned groups 140.941–140.959 s Ubuntu and 213.976–213.993 s Windows | Sanitized checked-in duration tables sourced to run 34484474215. |
| SC-02 updater contract | Run the focused updater test with one JUnit artifact per shard, then remove one artifact | Recovery content after `abde1e6`; same local environment | PASS; exact aggregation passed and five-of-six input failed closed; 1 passed in 0.13 s | Result ID `20260910T140540128Z-12436-8d6a6cad`. |
| SC-05 flake recovery | Rerun the three failed synthetic click scenarios and their parameterized dispatch companions after raising only the fixture helper's actionability allowance from 2 s to 10 s | `ae47c52` content on parent `deb20a0`; same local environment, synthetic Chrome fixture | PASS; 5 passed in 3.12 s; 5.921 s command | Result ID `20260910T144503813Z-328-f5b9b71f`; source fingerprint `ceb797324206b5cadeb37b56228c8e89c0b82029fd7c91ca52eb96de551d2855`. |

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

Hosted run [34484474215](https://github.com/datap0nd/data_governance/actions/runs/34484474215)
then passed all six Ubuntu shards and four of six Windows shards. Windows shard
1 failed two supported-command contract assertions because CI intentionally
does not create the local checkout `.venv` before validating invalid arguments.
Windows shard 2 failed six synthetic click fixtures when Playwright could not
complete its initial actionability check within the fixture's 500 ms budget.
The command now rejects invalid usage before preflight, and only the synthetic
fixture dispatch allowance increased to 2 s; production timeouts and timing
assertions are unchanged. The eight affected cases passed locally. The failed
run was cancelled while its now-invalid serial jobs were still running to
avoid consuming unnecessary hosted capacity; corrected same-head equivalence
remains required.

Measured-plan equivalence run
[34487005542](https://github.com/datap0nd/data_governance/actions/runs/34487005542)
passed all twelve parallel shards, both serial baselines, exact per-OS outcome
reconciliation and `Merge ready` on `deb20a0`. Its Ubuntu test steps were
2m04s–2m53s and Windows test steps were 3m07s–3m50s; the one-time serial steps
took 13m19s Ubuntu and 23m25s Windows. This proved equivalence but is not
counted toward final-head consecutive runs after the later fixture-only change.

The next parallel-only manual run
[34490063768](https://github.com/datap0nd/data_governance/actions/runs/34490063768)
passed eleven of twelve shards plus frontend and PostgreSQL 14/18, but Windows
shard 3 failed three synthetic click cases when Playwright used almost the full
2-second fixture allowance waiting for an element to become stable. The same
head had passed those cases in the prior parallel and serial executions, so
this is retained as a new-runner timing flake, not hidden as a product defect.
The fixture now allows 10 seconds for Playwright actionability while retaining
all visibility, stability, hit-testing and enabled-state assertions; production
timeouts and meaningful timing tests are unchanged. The affected local cases
passed, and final-head equivalence plus three clean parallel executions restart
from zero.

Historical duration weights are OS-specific but partial; unlisted files
deliberately receive a conservative default until successful manifests provide
measured updates. The hosted equivalence run remains authoritative for full
collection/outcomes and browser timing.

## Merge evidence

Pending. The PR testing section will record final-head CI, exact SHA, all three
parallel run URLs and merge evidence after the required check succeeds.
