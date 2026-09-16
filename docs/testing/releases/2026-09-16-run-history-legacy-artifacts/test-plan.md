# Legacy Run History artifacts: test plan

- Change/PR: keep Run History and expanded run logs usable when an older row contains a single artifact object or another non-array artifact payload.
- Code baseline: `abf57073e20bb26b6d166012ec2b6375c42012ee`; the PR records its final tested head.
- Related report: [test-report.md](test-report.md)
- Intended environments: isolated Windows/Python fixtures, fictional Chrome preview, and final-head CI.

## Prerequisites and test data

Use the checkout-owned Python 3.13 environment created by `tools/check.ps1` and
its isolated database, browser profile and temporary Flow root. The API fixture
creates a fictional Flow and run, then stores legacy `artifact_json` shapes.
The browser fixture uses the existing fictional Run History and expanded-log
preview; it does not access an installed Metronome database, portal, worker,
email client, PostgreSQL target or business export.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| RH-01 | Seed one run with a single artifact object containing `file_path` and `filename`; request the run list. Replace it with an unsupported object and request the detail. | The single artifact is returned as a one-item array; an unsupported object becomes an empty array. The stored database value is not rewritten. | Focused API pytest result |
| RH-02 | In the fictional preview, set run `#41` artifacts to a non-array legacy object and choose **Run history**. | The tab replaces the prior screen, all four history rows remain visible, and no page error occurs. | Synthetic browser result and `runs-retry-available.png` |
| RH-03 | From RH-02 choose the expanded log for run `#41`, inspect files, materialized-view retry status, then exercise successful retry, failed retry and narrow layout. | The log renders without an artifact error; missing invalid artifacts are omitted; existing retry/status controls, failure feedback and navigation continue to work. | Synthetic browser result and existing fictional screenshots |
| RH-04 | Run the required final-head CI on the PR. | Complete selected CI scope and the always-present **Merge ready** check pass on the recorded head SHA. | PR Testing section and CI run |

## Automated checks

Prepare and diagnose the checkout once:

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Preflight
```

Run the smallest affected API and browser selectors with Python and JavaScript
syntax checks. If the combined diagnostic exposes another renderer with the
same assumption, change only that affected path and rerun only the failed
browser selector and its syntax companions.

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath @(
    'tests/test_flows.py::test_run_history_normalizes_legacy_artifact_shapes',
    'tests/test_templates_refresh_preview.py::test_run_history_and_run_log_show_stages_and_retry_only_unfinished_views'
  ) `
  -SyntaxPath @(
    'app/routers/flows.py',
    'app/static/app.js',
    'app/static/flow_run_log.js',
    'app/static/recording-preview/templates-refresh.js'
  )
```

## Usability evidence

The owner reproduced the failure, confirmed that the API and individual run
page were readable, and verified that normalizing the in-memory artifacts made
Run History open. The owner then explicitly approved implementation. The
fictional local preview must reproduce the prior stuck-tab state, then show the
same journey with Run History and the expanded log usable, visible feedback
preserved, and no page error.

## Acceptance and cleanup

Accept when RH-01–RH-03 pass locally and RH-04 passes on the final PR head.
Fixture databases, HTTP servers and browser contexts must be removed by test
teardown; ignored screenshots may remain only as local evidence. No database
migration, artifact deletion or backup restoration is part of this fix.
Rollback is a code/service rollback; preserve `governance.db` and its sidecars.
