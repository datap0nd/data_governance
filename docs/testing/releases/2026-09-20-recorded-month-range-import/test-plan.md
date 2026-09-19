# Automatic recorded month ranges: test plan

- Change/PR: [#141](https://github.com/datap0nd/data_governance/pull/141); import one clear Playwright-recorded noUi month handle as a complete month-range action.
- Code baseline: `origin/main` `d114ef5c3966096bddd63d7e0a895150b44ef130`.
- Related report: [test-report.md](test-report.md).
- Intended environments: Windows with the checkout-owned Python 3.13 environment; isolated parser fixtures; final-head GitHub CI. The owner also explicitly requested one work-PC live pre-fix recording attempt.

## Prerequisites and test data

Create the checkout-owned environment with `tools/check.ps1 -Mode Setup`. Parser fixtures use fictional report labels and literal Playwright codegen; no portal URL, browser state, raw report data or credential is stored. The live attempt uses the already authenticated Chrome Remote Desktop session and is documented only with a sanitized evidence identifier.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| I-01 | Import codegen whose nearest period wording is **Monthly performance** and whose only noUi interaction is `locator(".noUi-touch-area").first.click()`. | Import replaces the click with one version-5 `set_range` action, automatically discovers the two-handle container, and creates `YYYYMM` start/end parameters from oldest selectable through latest selectable. | Focused verifier result. |
| I-02 | Inspect the promoted definition and restore metadata. | The original click and exact locator remain in `source_step`; the editor can reverse the automatic conversion. | Focused verifier assertions. |
| N-01 | Import the same noUi click with weekly wording, with no period wording, and with two recorded noUi handle clicks. | Import stays version 4 with the original clicks and no parameters. Ambiguous recordings remain available for manual review rather than being guessed. | Parameterized focused verifier result. |
| R-01 | Import an ordinary date/download recording with no slider. | Existing import behavior and resolved parameters remain unchanged. | Focused verifier result. |
| L-01 | In the owner-requested Retail SMS monthly report, record the real flow and inspect the imported step and test gate. | Pre-fix evidence identifies whether the month handle is semantic and whether a real download was captured; no draft is activated. | Protected evidence `LIVE-SMS-20260920-01`. |
| C-01 | Run required final-head CI. | `Merge ready` passes on the exact PR head; the PR records the run URL and SHA before merge. | PR testing section. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath @(
    'tests/test_flow_recordings.py::test_import_promotes_one_recorded_no_ui_month_handle_to_a_full_range',
    'tests/test_flow_recordings.py::test_import_leaves_ambiguous_no_ui_sliders_for_manual_review',
    'tests/test_flow_recordings.py::test_import_preserves_events_and_dates_without_running_source'
  ) `
  -SyntaxPath @('app/flow_recording.py','tests/test_flow_recordings.py')
```

Final-head `Merge ready` is the authoritative full regression. This focused set does not duplicate the existing month-slider playback suite; that suite remains covered by CI.

## Acceptance and cleanup

Accept when the focused checks pass, the bounded diff review finds no unsafe inference path, and final-head CI passes. Month detection must require exactly one noUi touch-area click plus nearby unambiguous month wording. Missing wording, weekly wording or multiple distinct noUi clicks must remain available for manual review. Recorded clicks are otherwise preserved exactly; the application must not infer that repeated clicks are accidental. Rollback is a PR revert; existing saved recordings are not rewritten. Isolated verifier artifacts remain ignored, and no protected live evidence is committed.
