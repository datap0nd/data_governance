# Flows: initial testing report

**Automated checks passed. Work-PC portal acceptance and capacity benchmarking
are NOT RUN.** This is evidence for the cumulative implementation through #69,
not a claim that live GSCM/ASAP behavior or a 99% success rate has been verified.

- Instructions: [work-PC test plan](test-plan.md).
- Live attempts: [results worksheet](manual-results.csv), initially all NOT RUN.
- Evidence reviewed: 2026-09-05 UTC, after #69 merged.
- Final tested PR head: `6424653ae67cec3dd01620acb58d48f8a312df74`.
- Resulting main squash commit: `bfff7bd0aa3af4a21f02706db65bc3b3e0524694`.
- [Final PR CI run](https://github.com/datap0nd/data_governance/actions/runs/33974422732), conclusion **success**.

## Changes covered

| PR | Delivered behavior | Main merge commit |
| --- | --- | --- |
| [#67](https://github.com/datap0nd/data_governance/pull/67) | GSCM dataset/identity hardening, ASAP/GSCM recorded method, revision validation, portable Python and global Chrome/Edge choice | `c43a6df2c20e1d91836a5998ec67d6c6f565f907` |
| [#68](https://github.com/datap0nd/data_governance/pull/68) | Up to 32 slots, shared total limit, preserved saved capacities, installer/worker coordination | `46a8a8b1f823e22135e88a229ba62fa310b244af` |
| [#69](https://github.com/datap0nd/data_governance/pull/69) | Single-worker recording, Finish/Cancel/force close, partial drafts, inclusive date batches and animated worker icon | `bfff7bd0aa3af4a21f02706db65bc3b3e0524694` |

The final #69 suite includes the earlier changes. Counts below refer to that
final head, not separately reconstructed runs of #67 or #68. The public merge
records identify their historical revisions and validation notes.

## Verified automated results

| Environment/check | Command/scope | Result | Evidence |
| --- | --- | --- | --- |
| GitHub Windows, Python 3.13.15 x64 | `python -m pytest tests -q` | **PASS: 1,522 passed, 12 warnings**, 782.64 s | [Windows job](https://github.com/datap0nd/data_governance/actions/runs/33974422732/job/101328506006), summary at 15:37:43 UTC |
| GitHub Linux, Python 3.13.15 x64 | `python -m pytest tests -q` | **PASS: 1,517 passed, 5 skipped, 12 warnings**, 236.84 s | [Linux job](https://github.com/datap0nd/data_governance/actions/runs/33974422732/job/101328505831), summary at 15:24:07 UTC |
| Both GitHub jobs | All 21 unique Node frontend test files; syntax checks for `app.js`, `flow_run_log.js`, `flow_recordings.js` | **PASS**, all corresponding steps succeeded | Same final CI jobs; workflow repeats the Flow display test once |
| Local Windows ARM, Python 3.13 ARM64, final head | Full `pytest.main(['tests', '-q', '--tb=short', '--basetemp=<external temp>'])` invocation | **PASS: 1,522 passed, 11 warnings**, 354.80 s | Retained local log `recording-release-full.txt`, re-read after merge; aggregate recorded here |

CI installed Playwright **1.62.0**, Chromium, Chrome and Edge. Exact installed
browser build numbers were not preserved in this report; they must be captured
for the work-PC comparison. No work-PC CPU/RAM/GPU load measurements were taken.

The local run used a temporary pytest harness workaround: `_pytest.pathlib`'s
temporary symlink helper was disabled to avoid a Windows ARM temporary-directory
cleanup problem. Application code was not patched by that harness. Standard CI
ran `python -m pytest tests -q` without it. The canonical test instructions do
not depend on private pytest APIs.

The warnings are Starlette TestClient/httpx, per-request TestClient timeout and,
in CI, AnyIO BlockingPortal deprecations. CI setup also emitted Node punycode
deprecation notices outside pytest's warning count. These were warnings, not
test failures. Linux's five skips are reported as skips, not passes; the saved
`-q` summary does not enumerate their reasons.

## Automated coverage and live case mapping

| Test source | Verified scenarios | Related manual cases |
| --- | --- | --- |
| [test_recording_controls.py](../../../../tests/test_recording_controls.py) | One-slot reservation, queue priority, no spare catalog browser, finish/cancel, racing updates, exact process-tree cleanup, partial draft, auth handoff cleanup, polling recovery, motion preference | REC-01–REC-09, UI-01 |
| [test_recording_date_batches.py](../../../../tests/test_recording_date_batches.py) | Two-year 10-week partition, invalid boundaries, frozen timezone/leap dates, complete batch validation, failed later batch, six-download worker/portable transformation fixture, SQL retry identity, worker capability gating | DATE-02–DATE-06, PORT-02, SQL-01 |
| [test_flow_recordings.py](../../../../tests/test_flow_recordings.py) | AST-only import, action/locator validation, dates, revision lifecycle, browser settings, capability/lease/profile ownership, portable execution and SQL reconciliation | REV-01–REV-05, DATE-01, BROWSER-01–BROWSER-02, PORT-01–PORT-03, RECOVERY-01, SQL-02 |
| [test_recorded_browser_pipeline.py](../../../../tests/test_recorded_browser_pipeline.py) | Real synthetic browser reports with replaced iframe, popup and two downloads; schema/period/default failures before downstream stages | REC-03, REV-02, REV-04, DATE-06 |
| [test_gscm_dataset_inventory.py](../../../../tests/test_gscm_dataset_inventory.py), [test_gscm.py](../../../../tests/test_gscm.py) | 350 loaded/filtered bookmarks, delayed/failed/empty/unproven scope loading, unfiltered API limits, current-binding selection after sorting; GSCM compatibility behavior | GSCM-01–GSCM-04 |
| [test_flow_capacity.py](../../../../tests/test_flow_capacity.py), [test_flow_parallel.py](../../../../tests/test_flow_parallel.py), [test_flows.py](../../../../tests/test_flows.py) | Shared/mode/portal capacities, reservations, drain/lease/cancel behavior, coordinated bundles, installer/service identities and existing flow contracts | CAP-01–CAP-03, RECOVERY-01, REG-01 |
| [CI workflow](../../../../.github/workflows/tests.yml) and Node frontend suites | Builder, settings, capacity display, progress/polling, sorting and existing UI regressions; full Python regression set | ENV-01 and affected UI cases |

These tests include actual browser downloads from controlled local fixtures.
They do not establish that internal portals expose the same completion signals,
that all corporate sign-ins transfer correctly, or that every report is supported.

## Findings and retests

- An earlier local run failed the repeated portable Edge fixture with a timeout.
  The unchanged earlier baseline passed. Preserving the existing unbatched
  browser/page lifecycle restored the focused case; the final local and both
  CI full suites above passed. This documents the observed compatibility fix,
  not a general conclusion that Chrome is more reliable than Edge.
- An earlier CI run for a superseded PR head was cancelled after a new head was
  pushed. It is not the final release result; the final run linked above passed.
- Earlier Windows ARM experiments found repeated persistent-profile download
  crashes in both browsers. Recorded flows use fresh authenticated contexts;
  legacy catalog profiles still need the live repeated-run test. See
  [authentication and isolation notes](../../../recorded_flows.md).
- The recording execution core changed in #69. Existing recorded flows need a
  newly saved/validated revision. Existing catalog/bookmark flows keep their
  selected method. HTTP replay/API acceleration remains unqualified and disabled
  for recorded execution.

## Outstanding acceptance

Every case in [manual-results.csv](manual-results.csv) is **NOT RUN** at this
report's cutoff. Required next work is the work-PC plan, including three fresh
browser validations per representative pilot recording and parameter variation.
In particular:

- **Recording:** actual one-worker launch, corporate sign-in, Finish/Cancel and
  multiple report/download shapes on the deployed work PC.
- **GSCM:** expected live bookmark identities/scope coverage, previously failing
  bookmarks and direct activation without a rendered-grid sweep.
- **Dates and data:** live period/schema/bundle comparisons, default behavior,
  representative large exports and recovery on the same date context.
- **Browser/portability/SQL:** actual Chrome/Edge comparisons, a separate machine
  with only declared dependencies, and isolated real-database stage verification.
- **Capacity:** safe throughput on the 5090/96 GB/9900X3D work PC. The default 12
  and maximum 32 are configuration values, not measured live capacity.

No first-attempt reliability percentage can be reported from these results.
Acceptance remains **automated regression verified; live qualification pending**.
