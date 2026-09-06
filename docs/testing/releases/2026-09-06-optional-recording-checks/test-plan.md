# Optional recording checks: test plan

- Scope: replay captured actions without generated report-title/readiness
  requirements. Waits and downloaded-data checks are optional. Existing
  generated page checks no longer gate testing or execution.
- Baseline: `995ebb21289b6af8086e5c9c2a0de6156835e18f`.
- Results: [test-report.md](test-report.md).
- Environments: local Windows ARM64 with headless Chrome; final Windows/Linux
  CI; live work PC/portal/share verification recorded separately.

## Prerequisites

Update the app and worker from the merged main revision. If a previous test
is still queued/running, cancel it first; active work prevents the updater
from proceeding. Preserve the saved recording. Use a disabled test Flow in
the default managed module and a fictional fixture or authorized test report.
Record deployed app/worker SHA, browser version and run/session IDs.

For a local UI walkthrough, serve `app` with
`python -m http.server 8769 --bind 127.0.0.1 --directory app`, then open
`http://127.0.0.1:8769/static/recording-preview/optional.html`. This preview
uses production editor components with fictional APIs, not a live worker.

## Cases

| ID | Actions | Expected result and evidence |
| --- | --- | --- |
| OPT-1 | Record opening a report, clicking Run, then downloading. Finish and select Test recording without adding checks. Repeat with an existing recording carrying old generated title/readiness metadata. | Test queues directly. No title or report-ready question. Worker replays captured actions and records actual download completion. Save/reopen preserves actions and pending Flow settings. |
| OPT-2 | Select Add wait. Set 60 seconds, move it between Run and Download, save/reopen, then test. | Wait remains in the selected position; progress shows the wait. Playback pauses before Download. No completion-signal configuration. The synthetic preview simulates elapsed time; a real 60-second wait requires live execution. |
| OPT-3 | Select a CSV/Excel download, Add data check. Test a file with three populated data rows against the default minimum of four. Include blank rows. | Failure reports three data rows versus four required; headers and blank rows do not count. No new output is published. Previous stable output remains intact. |
| OPT-4 | Change the minimum to three and test; then remove the check, save/reopen and test a header-only CSV or valid empty workbook. Undo a check edit. | Threshold pass and removal work. No implicit empty-report gate remains. Removed checks stay removed; Undo restores the prior configuration. |
| OPT-5 | Choose HTML/text output; then switch from checked Excel to HTML/text. | Unsupported row-check creation is not offered. An existing incompatible check remains removable. A saved unchecked HTML/text download does not require a tabular layout. |
| OPT-6 | Export a standalone script with no identity/readiness metadata; run it on fictional data. Repeat with minimum-row checks and a Python transform. | Worker and portable execution use the same optional-check behavior. Unchecked workbook bytes are preserved. Checked/processed tables normalize when needed. |
| OPT-7 | Replay an explicitly captured assertion, an indexed locator and a later-page date parameter. Test a missing native download, incorrect optional columns/date, sign-in response and corrupt workbook. | Recorded assertions still execute; supported recorded locator ordering is retained. Genuine action/download failures surface. Optional checks fail before publication. No automatic generated page checks are introduced. |
| OPT-8 | Repeat the editor controls at desktop and 390px width, including failure, removal, navigation and reopening. | Controls remain visible, feedback sits beside the action, edits are preserved and the viewport does not overflow. |
| LIVE-1 | On the work PC, run OPT-1 through OPT-4 with the user's actual portal and managed share, using a disabled test Flow. | Collect protected run evidence, output path/row count, app/worker SHA and browser version. Verify the optional wait is sufficient for the portal and the stable destination remains usable by reports. |

## Automated checks

Install the dependencies/browser versions from `.github/workflows/tests.yml`.
Run the full suite for the shared runtime/storage change:

```powershell
python -m pytest tests -q
Get-ChildItem tests/test_*.mjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE) { throw "Node test failed" } }
node --check app/static/app.js
node --check app/static/flow_recording_editor.js
node --check app/static/flow_recording_model.js
node --check app/static/recording-preview/optional.js
git diff --check
```

Focused coverage: `test_recording_optional_checks.py`,
`test_recorded_output_storage.py`, `test_optional_recording_editor.py`,
`test_recording_journey.py`, `test_recording_v2_pipeline.py`, and the existing
recording, browser, worker, transformation, SQL and managed-output suites.

## Acceptance and cleanup

Accept after automated checks pass on the final PR head and the UI matches
the user's requested correction. Mark live cases NOT RUN until actually
performed. No hidden mandatory replacement for removed page checks is allowed.
Tests use fictional reports and isolated temporary folders; keep production
schedules disabled and remove only designated test outputs after review.
Revert the PR to roll back code; do not delete recordings or prior output.
