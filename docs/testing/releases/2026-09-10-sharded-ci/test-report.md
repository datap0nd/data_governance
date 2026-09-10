# 2026-09-10 sharded CI and safe scope selection: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: PR 2 of the faster-delivery rollout; pull request pending
- Evidence cutoff (UTC): 2026-09-10 17:42, before corrected final-head equivalence
- Tested code revisions: initial focused set at `044cd6ae4b839f15525abdb782eaccf4002fec5c`; Windows prerequisite recovery at `abde1e6c0d451cf661c3412b4c612acc5cf5bc88`; measured duration updater/plan at `dcd13ba7a79a849ebf8cb18cf6ec234652726720`; synthetic click stabilization at `ae47c520663da46472d338b08bbaa36167240edc`; virtual-range repaint recovery at `8afea8c22caf3c922ef2efdfa4dff93c6820d7b4`; browser preflight at `1d237c9f6e4d6f0b0dd400549610120e2b0ed33a`
- Environment: Windows 11, PowerShell 7.6.5, Python 3.13.15 locally; GitHub-hosted Ubuntu and Windows runners
- Overall finding: focused classifier, partitioner, reconciliation, aggregate-gate and fixture checks pass locally; rebased hosted equivalence passed before a later test-harness correction, so final-head equivalence and three consecutive runs remain pending

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
| SC-05 virtual-range recovery | Rerun `test_range_reacquires_virtualized_cells_after_each_scroll` after making its virtual selection state persistent and yielding two browser animation frames after every programmatic scroll | `8afea8c` content on parent `a928fe5`; same local environment, synthetic managed Chromium | PASS; 1 passed in 2.57 s; 4.928 s command | Result ID `20260910T145846742Z-24988-4190255b`; source fingerprint `9a03592a3f479a6801a7b819b7d17e736e27699e0eb84c678affa2c845f3a006`. |
| SC-06 local browser preflight | Run Preflight after installing managed Chromium into the ignored checkout cache; exercise AST-based real-launch discovery and probe-only missing-channel behavior | `1d237c9` content; same local environment | PASS after one test-expectation correction; managed Chromium and real Chrome launched; preflight 3.395 s; initial combined run had 2 passes/1 stale literal assertion, then that node and the new probe-only node passed | Preflight result `20260910T150601307Z-25896-b32c89cc`; focused results `20260910T150423649Z-17876-d0cfb2e6`, `20260910T150445576Z-36012-b019f73a`, and `20260910T150549493Z-7632-48e6515a`. |

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

The next final-head attempt
[34491225739](https://github.com/datap0nd/data_governance/actions/runs/34491225739)
then found a separate Ubuntu timing race in the existing virtualized-week-range
fixture. A fast sequence of programmatic scroll assignments could advance
before the browser delivered the `scroll` repaint, leaving the old cells in
every snapshot. The first local rerun correctly failed before the case because
the checkout lacked its managed Chromium prerequisite; Chromium was installed
into the ignored checkout cache. A second run showed the stale viewport ending
at W33, confirming the repaint race. The range implementation now yields two
animation frames after each scroll so scroll handlers and virtual rendering
complete before the next snapshot, while retaining the production 10-second
timeout and exact enabled/selected-state checks. The deterministic fixture
persists selection across its virtual repaint, and the affected case passed.
The invalid hosted run was cancelled to conserve capacity; final-head
equivalence and the three-run count restart again.

The local missing-Chromium incident also showed that dependency preflight did
not yet prove browser launchability. Setup now installs Playwright-managed
binaries into the checkout-owned ignored cache, full Preflight probes every
actually launched browser, and Verify probes only channels called by the
selected test files before pytest. The first scanner attempt falsely selected
Edge from a string literal inside its own unit test. Replacing text matching
with Python AST call inspection removed that false positive; a probe-only unit
fixture proves missing channels are reported without installing them. The
final preflight launched managed Chromium and real Chrome successfully.

After rebasing onto application changes from PRs #104 and #106, the newly
affected `tests/test_flow_sql.py` set passed locally (63 passed in 14.82 s;
18.389 s command) at `c7df10a`, and hosted run
[34498398343](https://github.com/datap0nd/data_governance/actions/runs/34498398343)
passed all twelve shards, both serial baselines, exact reconciliation, and
`Merge ready`. Parallel repeat
[34501204108](https://github.com/datap0nd/data_governance/actions/runs/34501204108)
also passed. The next repeat
[34502430269](https://github.com/datap0nd/data_governance/actions/runs/34502430269)
passed eleven Windows/Ubuntu shards but Windows shard 3 failed when the
synthetic setup-block harness exceeded its 30-second subprocess watchdog while
starting Windows PowerShell and real DPAPI. Both parameterized branches passed
locally in 2.44 s. The watchdog is not a product timing assertion; it is now 60
seconds to accommodate cold hosted-process startup while remaining bounded.
Production setup behavior and every credential, failure, and secret-redaction
assertion are unchanged. Final-head equivalence and the three-clean-run count
restart after this test-only correction. The corrected failed node, its corrupt
credential parameter, ordering companion, and Python syntax then passed locally
(3 passed in 2.14 s; 5.140 s command), result ID
`20260910T164214561Z-38876-e85bcad4`.

The next third-run attempt
[34507553784](https://github.com/datap0nd/data_governance/actions/runs/34507553784)
passed eleven shards but Ubuntu shard 1 failed when the first generated
portable Edge process exceeded its 90-second subprocess watchdog; the prior
unchanged-head run completed that test in 32.69 s for Edge and 16.63 s for
Chrome. A targeted local rerun then exposed two supported-command gaps before
reaching equivalent evidence: the checkout-rooted pytest temp path could exceed
Windows legacy path limits, and source-only AST scanning did not recognize
browser channels supplied by `pytest.mark.parametrize`. The command now uses a
short, unique `%TEMP%\\mt\\<pid-guid>` isolation root, retains its evidence in
the ignored checkout directory, and restricts cleanup to the exact temp
namespace. The browser scanner recognizes literal parameterized channels and
exact browser parameter selectors, preserving distinct real Chrome and Edge
requirements. Database-template copies now pair with a fresh per-test Flow
filesystem root, preventing repeated IDs/names from colliding in one invocation.

The local setup path correctly attempted only the missing Edge channel, but the
Playwright installer was refused for insufficient machine privileges. No
elevation or alternate browser alias was used. The exact Chrome parameter plus
the filesystem, scanner, command, and syntax companions passed locally (4
passed in 18.85 s; 22.822 s command), result ID
`20260910T173808050Z-29280-f43ebe39`. The portable-process watchdog is now a
bounded 120 seconds; all download, transformation, repeated-run, artifact, and
real-channel assertions remain unchanged. Hosted Ubuntu/Windows runners remain
authoritative for the final real Edge execution and equivalence evidence.

Before the corrected candidate could run, PR #107 changed recording validation
code and tests on `main`. The branch was rebased onto `2fec423`; the testing
index conflict was resolved by retaining both releases, and the application
merge was otherwise conflict-free. Its two newly added recording-validation
cases and affected Python syntax passed locally (2 passed, 2 dependency
warnings in 7.66 s; 10.884 s command), result ID
`20260910T174145625Z-38008-6c356b0b`. Because application/test content changed,
the hosted final-head equivalence and consecutive-run count restart on the
rebased revision.

The first run on that revision,
[34509847658](https://github.com/datap0nd/data_governance/actions/runs/34509847658),
was cancelled after Ubuntu shard 4 exposed a platform-assumption defect in the
new Flow-root isolation test. The fixture correctly uses
`<tmp_path>/metronome/flows` when no root is configured, whereas the assertion
required the final directory name itself to equal the test directory name.
The corrected invariant requires the unique test directory anywhere in the
resolved root. The invalid run's serial and unfinished shards were cancelled
to conserve hosted capacity; completed successes and the failure remain in the
run evidence. Final-head equivalence and consecutive runs restart after the
test-only correction. The corrected node and Python syntax passed locally (1
passed in 0.34 s; 2.736 s command), result ID
`20260910T174913813Z-24168-6cbc0105`.

Historical duration weights are OS-specific but partial; unlisted files
deliberately receive a conservative default until successful manifests provide
measured updates. The hosted equivalence run remains authoritative for full
collection/outcomes and browser timing.

## Merge evidence

Pending. The PR testing section will record final-head CI, exact SHA, all three
parallel run URLs and merge evidence after the required check succeeds.
