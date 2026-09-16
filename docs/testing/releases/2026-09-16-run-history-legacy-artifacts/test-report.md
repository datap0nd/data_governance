# Legacy Run History artifacts: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: pending; the PR will link this package.
- Evidence cutoff: **2026-09-16 17:48 UTC**. Final-head CI follows this committed report and is recorded in the PR Testing section before merge.
- Tested code revision: uncommitted release working tree based on `abf57073e20bb26b6d166012ec2b6375c42012ee`; the PR supplies the exact committed head.
- Environment: Windows, checkout-owned Python **3.13.15**, pytest 9.1.1, Playwright 1.62.0, Chrome **152.0.7977.83**, Node **24.19.0**.
- Overall finding: the focused API case passed; the first browser pass exposed the same legacy-shape failure in the expanded log, and the failure-specific retest passed after that renderer was hardened. Final-head CI is pending.

## Executed checks

Verifier run identifiers refer to `.test-runs/<identifier>/result.json` in the
task checkout. Screenshots produced by the fictional preview contain no
business data and remain in the ignored run directory.

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| Setup | `.\tools\check.ps1 -Mode Setup` | baseline `abf5707`; Python 3.13.15 | PASS; lock-exact checkout environment created | `20260916T174147472Z-17884-648238e0/result.json` |
| Environment diagnosis | `.\tools\check.ps1 -Mode Preflight` | uncommitted release tree; Windows | PASS; dependencies/imports ready and paths isolated | `20260916T174633100Z-39640-a25a8551/result.json` |
| Preview diagnosis | Serve the existing fictional preview, inject a non-array artifact into run `#41`, choose Run History, then apply the proposed in-memory normalization and choose it again. | baseline application renderer; headless Chrome 152 | Reproduced `(run.artifacts || []).filter is not a function`, preserved the prior view, then recovered all 4 history rows including `#41`. | `.test-runs/run-history-legacy-preview/` |
| RH-01 and initial RH-02/RH-03 | Combined focused verifier for the two plan selectors plus `flows.py`, `app.js` and preview JavaScript syntax | uncommitted release tree | API **PASS: 1**; browser **FAIL/ERROR: 1** because the expanded-log renderer retained the same array assumption; 11.52s | `20260916T174641607Z-44456-bc16ce13/result.json` |
| RH-02–RH-03 retest | `.\tools\check.ps1 -Mode Verify -TestPath 'tests/test_templates_refresh_preview.py::test_run_history_and_run_log_show_stages_and_retry_only_unfinished_views' -SyntaxPath @('app/static/app.js','app/static/flow_run_log.js','app/static/recording-preview/templates-refresh.js')` | uncommitted release tree after the run-log correction | **PASS: 1**, zero failures/errors/skips; 2.02s pytest / 5.06s verifier | `20260916T174734894Z-9112-be74cc73/result.json` |
| Verifier invocation diagnosis | Attempted syntax-only Verify without a test selector. | same tree | STOPPED before preflight/tests: Verify correctly requires an explicit selector; no product result claimed. | `20260916T174748949Z-40024-a30d9994/result.json` |

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| RH-04 | NOT RUN | Final PR head does not yet exist at this report cutoff. | Run required CI, record its URL and exact head SHA in the PR, then merge only if **Merge ready** passes. |

## Usability evidence

The owner-provided reproduction showed that selecting **Run history** changed
the active tab while leaving the prior Flows table visible. The browser console
identified the artifact `.filter` exception, and the owner confirmed the
non-persistent normalization restored the history before requesting the
permanent change.

The local fictional preview reproduced that stuck-view failure without an
installed database. After the correction, Run History displayed all four
fictional rows and the expanded log retained its materialized-view retry,
failure/recovery and narrow-layout behavior with no page errors. The fix adds
no new controls and preserves the existing labels, feedback and next actions.

## Findings, limitations and retests

- The first focused browser execution is retained as a failure: it proved the table fix alone was incomplete because the expanded log independently filtered the same malformed value.
- The run-log renderer now uses the same defensive array check, and the failed browser case passed on its targeted retest. The already-passing API selector was not duplicated after this browser-only correction.
- One attempted syntax-only verifier command was rejected before checks because repository policy requires a focused test selector. Syntax for `flows.py` ran before pytest in the initial invocation; all changed JavaScript syntax ran in the successful browser retest.
- Legacy database rows are read compatibly but not rewritten. A plausible single artifact object becomes a one-item public array; unsupported objects and non-object list members are omitted from display.
- Final CI is authoritative for the full Python regression and frontend contracts.

## Merge evidence

Pending. Final CI completes after this report's committed evidence cutoff. The
PR Testing section must record the final head SHA, run URL, required job results
and merge. No deployment or post-merge verification is claimed here.
